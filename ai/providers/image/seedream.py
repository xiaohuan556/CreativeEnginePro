"""
图片 Provider 基类 + 实现（Seedream 已接入真实 Ark API，GPT-Image 接入 ModelHub 代理）。

每个图片 Provider 继承 ImageProvider，实现 execute()。
"""

import base64 as _b64
import io
import os
import time
import uuid
from pathlib import Path
from typing import Any

import requests

from ..base import AIProvider, ProviderDomain, TaskRequest, TaskResult, TaskHandle, TaskStatus
from ..ark_http import ark_post, download, to_image_data_url, ArkHTTPError
from ...reference_assets import normalize_reference_assets, append_manifest


def _image_to_bytes(ref: Any) -> bytes:
    """将各种图片输入统一转为 PNG 字节流，供 multipart 上传使用。"""
    if isinstance(ref, bytes):
        return ref
    if isinstance(ref, (str, Path)):
        p = Path(ref)
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
            return p.read_bytes()
    if isinstance(ref, io.BytesIO):
        return ref.getvalue()
    # PIL Image
    try:
        from PIL import Image
        if isinstance(ref, Image.Image):
            buf = io.BytesIO()
            ref.save(buf, "PNG")
            return buf.getvalue()
    except ImportError:
        pass
    # 最后尝试作为路径读
    return Path(str(ref)).read_bytes()


def _normalize_edit_mask(mask_bytes: bytes, image_bytes: bytes) -> bytes:
    """将编辑蒙版规范为与首张输入图同尺寸的 RGBA PNG。"""
    from PIL import Image

    with Image.open(io.BytesIO(image_bytes)) as source:
        target_size = source.size
    with Image.open(io.BytesIO(mask_bytes)) as mask:
        # 旧版 UI 产生的是 L 灰度图；其灰度值实际表达的就是 alpha。
        alpha = mask.getchannel("A") if "A" in mask.getbands() else mask.convert("L")
        if alpha.size != target_size:
            alpha = alpha.resize(target_size, Image.Resampling.NEAREST)
        rgba = Image.new("RGBA", target_size, (255, 255, 255, 255))
        rgba.putalpha(alpha)
    output = io.BytesIO()
    rgba.save(output, "PNG")
    return output.getvalue()


def _apply_hard_edit_mask(output_path: Path, image_bytes: bytes,
                          mask_bytes: bytes | None) -> None:
    """Restore every protected pixel after a generative edit.

    GPT Image treats masks as guidance and may redraw outside the transparent
    region.  Production scene geometry needs a hard guarantee, so opaque mask
    pixels are composited back from the first (composition) image locally.
    """
    if mask_bytes is None:
        return
    from PIL import Image

    with Image.open(io.BytesIO(image_bytes)) as source_image, \
            Image.open(io.BytesIO(mask_bytes)) as mask_image, \
            Image.open(output_path) as generated_image:
        generated = generated_image.convert("RGBA")
        size = generated.size
        source = source_image.convert("RGBA")
        if source.size != size:
            source = source.resize(size, Image.Resampling.LANCZOS)
        alpha = mask_image.getchannel("A")
        if alpha.size != size:
            alpha = alpha.resize(size, Image.Resampling.BILINEAR)
        locked = Image.composite(source, generated, alpha)
        locked.save(output_path, "PNG")


class ImageProvider(AIProvider):
    """图片生成 / 编辑 Provider 基类。"""
    domain = ProviderDomain.IMAGE

    # 子类应覆盖：
    # name: str = "seedream" / "flux" / "gptimage"
    # capabilities: list[str] = ["text_to_image", "image_edit", "inpaint", "outpaint", ...]

    def execute(self, request: TaskRequest) -> TaskHandle:
        raise NotImplementedError(f"{self.name}.execute() 未实现")

    # ── 共用：输出目录 ──
    @staticmethod
    def _out_dir() -> Path:
        try:
            from config import OUTPUT_DIR
            base = Path(OUTPUT_DIR)
        except Exception:
            base = Path.home() / ".cep_output"
        d = base / "ai_images"
        d.mkdir(parents=True, exist_ok=True)
        return d


# ──────────────────────────────────────────────
# Seedream（已接入火山方舟真实 API）
# ──────────────────────────────────────────────

class SeedreamProvider(ImageProvider):
    """字节跳动 Seedream 5.0 Pro 图片生成（火山方舟 Ark）。

    接口：POST {base}/images/generations
    文档：https://www.volcengine.com/docs/3019/1541523

    鉴权：真实 Ark API Key（Bearer）来自 .env 的 SEEDREAM_API_KEY
    （用户给的 `ark-...` 就是密钥，已写入 .env）。
    模型/端点 ID 优先级：request.config["model"] → .env 的 SEEDREAM_MODEL
    → api_config seedream.default_model。端点 ID 必须是 ep- 开头的 Seedream 专属接入点。
    """
    name = "seedream"
    capabilities = ["text_to_image", "image_edit"]

    def _creds(self):
        from api_config import get as _ac_get
        entry = _ac_get("seedream")
        api_key = self.api_key or entry.value()
        model = (self.config.get("model")
                 or os.environ.get("SEEDREAM_MODEL")
                 or entry.default_model or "").strip()
        base = (self.config.get("base_url") or entry.default_base_url).rstrip("/")
        return api_key, model, base

    def execute(self, request: TaskRequest) -> TaskHandle:
        handle = TaskHandle(
            id=f"seedream_{uuid.uuid4().hex[:10]}",
            provider_name=self.name,
            operation=request.operation,
            status=TaskStatus.RUNNING,
        )
        try:
            api_key, model, base = self._creds()
            if not api_key:
                raise ArkHTTPError("未配置 Ark API Key（请在 .env 设置 SEEDREAM_API_KEY）")
            if not model:
                raise ArkHTTPError("未配置 Seedream 端点/模型 ID（api_config seedream.default_model）")

            prompt = (request.inputs.get("prompt") or "").strip()
            if not prompt:
                raise ArkHTTPError("缺少 prompt")

            fallback_refs = request.inputs.get("images") or [
                request.inputs.get("image")]
            typed_refs = normalize_reference_assets(
                request.inputs.get("reference_assets"), fallback_refs)
            prompt = append_manifest(prompt, typed_refs)

            # Seedream 不支持单次请求生成多张 → 循环 n 次
            n = max(1, int(request.params.get("n", 1) or 1))

            base_payload: dict[str, Any] = {
                "model": model,
                "prompt": prompt,
                "size": request.params.get("size", "2K"),
                "output_format": request.params.get("output_format", "png"),
                "watermark": bool(request.params.get("watermark", False)),
                "response_format": "url",
            }
            # 图生图 / 编辑
            refs = [item["path"] for item in typed_refs]
            ref = refs[0] if refs else None
            if refs:
                # Ark /images/generations 始终接收 JSON。远程 URL 原样发送，
                # 本地图片转换为 data URL；multipart 会被 Ark 当成无效 JSON 拒绝。
                encoded_refs = [to_image_data_url(item) for item in refs]
                base_payload["image"] = encoded_refs[0] if len(encoded_refs) == 1 else encoded_refs

            out_paths: list[Path] = []
            for i in range(n):
                parsed = ark_post(f"{base}/images/generations", api_key, base_payload, timeout=180)
                data_list = parsed.get("data") or []
                if not data_list:
                    raise ArkHTTPError(f"Seedream 返回为空: {str(parsed)[:300]}")
                url = data_list[0].get("url")
                if not url:
                    raise ArkHTTPError(f"Seedream 返回缺少 url: {str(parsed)[:300]}")
                out = self._out_dir() / f"seedream_{uuid.uuid4().hex[:8]}.png"
                download(url, out, timeout=600)
                out_paths.append(out)
                handle.progress = (i + 1) / n

            handle.result = TaskResult(
                success=True,
                data=out_paths[0] if len(out_paths) == 1 else out_paths,
                provider_raw={"count": len(out_paths), "usage": parsed.get("usage")},
            )
            handle.status = TaskStatus.DONE
        except Exception as e:
            handle.result = TaskResult(success=False, error=str(e))
            handle.status = TaskStatus.FAILED
        handle.finished_at = time.time()
        return handle


# ─���────────────────────────────────────────────
# 桩：FLUX
# ──────────────────────────────────────────────

class FluxProvider(ImageProvider):
    """FLUX.1 开源图片生成（本地部署或 Replicate API）。

    API 文档：https://replicate.com/black-forest-labs/flux-pro
    """
    name = "flux"
    capabilities = ["text_to_image", "image_edit", "inpaint"]

    def execute(self, request: TaskRequest) -> TaskHandle:
        handle = TaskHandle(
            id=f"flux_{request.to_cache_key()[:8]}",
            provider_name=self.name,
            operation=request.operation,
            status=TaskStatus.QUEUED,
        )
        handle.status = TaskStatus.FAILED
        handle.result = TaskResult(success=False, error="FluxProvider 尚未实现")
        handle.finished_at = __import__("time").time()
        return handle


# ──────────────────────────────────────────────
# OpenAI GPT-Image（gpt-image-2）
# ──────────────────────────────────────────────

class GPTImageProvider(ImageProvider):
    """OpenAI GPT-Image 图片生成 / 编辑 / 局部重绘（走 ModelHub 统一代理）。

    文生图：  POST {base}/images/generations  (JSON)
    图生图：  POST {base}/images/edits       (multipart: image)
    局部重绘：POST {base}/images/edits       (multipart: image + mask)

    /images/edits 必须用 requests.post(files=...) 发标准 multipart，
    不能用手拼 boundary（会被网关错误解析为 'model is required'）。
    """
    name = "gptimage"
    capabilities = ["text_to_image", "image_edit", "inpaint"]

    def _creds(self):
        from api_config import get as _ac_get
        entry = _ac_get("openai_image")
        api_key = (self.api_key
                   or entry.value()
                   or os.environ.get("OPENAI_API_KEY", ""))
        model = (self.config.get("model")
                 or entry.default_model
                 or "gpt-image-2").strip()
        base = (self.config.get("base_url")
                or entry.default_base_url
                or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        return api_key, model, base

    def execute(self, request: TaskRequest) -> TaskHandle:
        handle = TaskHandle(
            id=f"gptimage_{uuid.uuid4().hex[:10]}",
            provider_name=self.name,
            operation=request.operation,
            status=TaskStatus.RUNNING,
        )
        try:
            api_key, model, base = self._creds()
            if not api_key:
                raise ArkHTTPError("未配置 OPENAI_API_KEY")

            prompt = (request.inputs.get("prompt") or "").strip()
            if not prompt:
                raise ArkHTTPError("缺少 prompt")

            fallback_refs = request.inputs.get("images") or [
                request.inputs.get("image")]
            typed_refs = normalize_reference_assets(
                request.inputs.get("reference_assets"), fallback_refs)
            prompt = append_manifest(prompt, typed_refs)
            refs = [item["path"] for item in typed_refs]
            ref = refs[0] if refs else None
            mask = request.inputs.get("mask")
            is_edit = request.operation in ("image_edit", "inpaint") and ref is not None
            # GPT Image 2 accepts multiple image parts.  Keep the typed order so
            # scene, character and element references do not collapse to image 1.
            n = max(1, int(request.params.get("n", 1) or 1))
            out_dir = self._out_dir()

            if is_edit:
                # ── 图生图 / 局部编辑：/images/edits 单次只返1张 → 循环 n 次 ──
                ref_bytes = [_image_to_bytes(item) for item in refs]
                mask_bytes = (_normalize_edit_mask(_image_to_bytes(mask), ref_bytes[0])
                              if mask is not None else None)
                out_paths: list[Path] = []
                for i in range(n):
                    files = [
                        ("image", (f"image_{j + 1}.png", item, "image/png"))
                        for j, item in enumerate(ref_bytes)
                    ]
                    if mask_bytes is not None:
                        files.append(("mask", ("mask.png", mask_bytes, "image/png")))
                    resp = requests.post(
                        f"{base}/images/edits",
                        headers={"Authorization": f"Bearer {api_key}"},
                        data={
                            "model": model,
                            "prompt": prompt,
                            "n": "1",
                            "size": request.params.get("size", "1024x1024") if request.params.get("size", "1024x1024") not in ("auto", "") else "1024x1024",
                            "response_format": "b64_json",
                        },
                        files=files,
                        timeout=180,
                    )
                    if resp.status_code != 200:
                        raise ArkHTTPError(
                            f"GPT-Image 编辑失败 {resp.status_code}: {resp.text[:300]}",
                            status=resp.status_code, body=resp.text,
                        )
                    items = resp.json().get("data") or []
                    if not items:
                        raise ArkHTTPError(f"GPT-Image 编辑返回为空: {resp.text[:300]}")
                    b64 = items[0].get("b64_json")
                    if not b64:
                        raise ArkHTTPError(
                            f"GPT-Image 编辑返回缺少 b64_json: {resp.text[:300]}")
                    out_i = out_dir / f"gptimage_{uuid.uuid4().hex[:8]}.png"
                    out_i.write_bytes(_b64.b64decode(b64))
                    _apply_hard_edit_mask(out_i, ref_bytes[0], mask_bytes)
                    out_paths.append(out_i)
                    handle.progress = (i + 1) / n
                handle.result = TaskResult(
                    success=True,
                    data=out_paths[0] if len(out_paths) == 1 else out_paths,
                    provider_raw={"count": len(out_paths)},
                )
            else:
                # ── 文生图：/images/generations 原生支持 n>1 ──
                payload: dict[str, Any] = {
                    "model": model,
                    "prompt": prompt,
                    "n": n,
                    "size": request.params.get("size", "1024x1024"),
                    "quality": request.params.get("quality", "high"),
                    "response_format": "b64_json",
                }
                resp = ark_post(f"{base}/images/generations", api_key, payload, timeout=180)
                data = resp.get("data") or []
                if not data:
                    raise ArkHTTPError(f"GPT-Image 返回为空: {str(resp)[:300]}")
                out_paths = []
                for item in data:
                    url = item.get("url")
                    b64 = item.get("b64_json")
                    out_i = out_dir / f"gptimage_{uuid.uuid4().hex[:8]}.png"
                    if b64:
                        out_i.write_bytes(_b64.b64decode(b64))
                    elif url:
                        ext = "png"
                        if url.lower().endswith((".jpg", ".jpeg")):
                            ext = "jpg"
                        elif url.lower().endswith(".webp"):
                            ext = "webp"
                        out_i = out_i.with_suffix(f".{ext}")
                        download(url, out_i, timeout=600)
                    else:
                        continue
                    out_paths.append(out_i)
                if not out_paths:
                    raise ArkHTTPError(f"GPT-Image 返回缺少 url/b64_json: {str(resp)[:300]}")
                handle.result = TaskResult(
                    success=True,
                    data=out_paths[0] if len(out_paths) == 1 else out_paths,
                    provider_raw={"count": len(out_paths),
                                  "usage": resp.get("usage")},
                )

            handle.progress = 1.0
            handle.status = TaskStatus.DONE
        except Exception as e:
            handle.result = TaskResult(success=False, error=str(e))
            handle.status = TaskStatus.FAILED
        handle.finished_at = time.time()
        return handle
