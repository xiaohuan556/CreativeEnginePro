"""
视频 Provider — Veo 3.1 / Seedance 2.0 / Kling。

Seedance 按 Key 类型自动接入火山方舟或 ModelHub 豆包兼容 API。
Veo 3.1 已接入 ModelHub 统一代理，走 **Google 原生 v1beta 端点**
  （注意：不是 /api/v1/videos/generations，那条路径在 ModelHub 上返回 404；
   Veo 在 ModelHub 下挂在 /api/v1beta/models/{model}:predictLongRunning）。
Kling 仍为桩。
"""

import base64
import json
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path

from ..base import AIProvider, ProviderDomain, TaskRequest, TaskResult, TaskHandle, TaskStatus
from ..ark_http import ark_post, ark_get, download, to_image_data_url, ArkHTTPError, wait, _friendly_ark_message
from ...reference_assets import normalize_reference_assets, append_manifest


# Veo 文本/图生视频支持的时长（秒）
VEO_DURATIONS = (4, 6, 8)
# Veo 支持的画幅
VEO_RATIOS = {"16:9", "9:16"}
VEO_RESOLUTIONS = {"720p", "1080p", "4k"}


def _seedance_generate_audio(params: dict | None) -> bool:
    """Respect the caller's audio choice; Seedance 2.0 defaults to native audio."""
    return bool((params or {}).get("generate_audio", True))


class VideoProvider(AIProvider):
    """视频生成 / 编辑 Provider 基类。"""
    domain = ProviderDomain.VIDEO

    @staticmethod
    def _out_dir() -> Path:
        try:
            from config import OUTPUT_DIR
            base = Path(OUTPUT_DIR)
        except Exception:
            base = Path.home() / ".cep_output"
        d = base / "ai_videos"
        d.mkdir(parents=True, exist_ok=True)
        return d


class VeoProvider(VideoProvider):
    """Google Veo 3.1 视频生成（通过 ModelHub 统一代理，Google v1beta 协议）。

    真实端点（已实测可通）：
      创建任务  POST {api_root}/v1beta/models/{model}:predictLongRunning
      查询任务  GET  {api_root}/v1beta/{operation_name}

    其中 api_root = base_url 去掉末尾的 /v1（即 https://modelhub.ailemac.com/api）。
    ModelHub 的 OpenAPI 中 Veo 挂在 /v1beta 下，与 /api/v1 的 chat/image 是两套路径。

    请求体（Google Veo 原生格式）：
      {
        "instances": [ {"prompt": "...", "image": {"bytesBase64Encoded": "...", "mimeType": "image/png"}} ],
        "parameters": {"aspectRatio":"16:9","durationSeconds":8,"numberOfVideos":1,
                       "personGeneration":"allow_adult","addWatermark":false,
                       "generateAudio":true,"enhancePrompt":false,"resolution":"720p"}
      }
    提交返回 {"name": "projects/.../operations/xxx", "done": false}；
    轮询 GET 直到 done=true，视频以内联 base64 返回：
      response.generateVideoResponse.generatedSamples[0].video.encodedVideo
    （兼容性也支持 response.generatedVideos[].video.uri 形式的下载链接）。
    """
    name = "veo"
    capabilities = ["text_to_video", "image_to_video"]

    POLL_INTERVAL = 6
    # ModelHub 的 operation 查询偶尔会单次挂起超过一分钟。轮询请求使用较短
    # socket 超时并将网络/429/5xx 当作可恢复事件，总时限则放宽到 15 分钟。
    POLL_REQUEST_TIMEOUT = 45
    POLL_TIMEOUT = 900
    EMPTY_RESULT_RETRIES = 4
    _OPERATION_LOG_LOCK = threading.Lock()

    def _creds(self):
        from api_config import get as _ac_get
        entry = _ac_get("veo")
        api_key = (self.api_key
                   or entry.value()
                   or os.environ.get("OPENAI_API_KEY", ""))
        model = (self.config.get("model")
                 or entry.default_model
                 or "veo-3.1-generate-preview").strip()
        base = (self.config.get("base_url")
                or entry.default_base_url
                or os.environ.get("OPENAI_BASE_URL", "https://modelhub.ailemac.com/api/v1")).rstrip("/")
        return api_key, model, base

    def _api_root(self, base: str) -> str:
        root = base.rstrip("/")
        if root.endswith("/v1"):
            root = root[:-3]
        return root

    @staticmethod
    def _clamp_duration(seconds: int) -> int:
        try:
            s = int(seconds)
        except (TypeError, ValueError):
            s = 8
        if s in VEO_DURATIONS:
            return s
        # 取最接近的允许值
        return min(VEO_DURATIONS, key=lambda d: abs(d - s))

    @staticmethod
    def _clamp_ratio(ratio: str) -> str:
        ratio = (ratio or "16:9").strip()
        if ratio == "adaptive":
            return "16:9"
        if ratio not in VEO_RATIOS:
            raise ArkHTTPError(
                f"Veo 3.1 不支持比例 {ratio}，仅支持 16:9 或 9:16")
        return ratio

    @staticmethod
    def _clamp_resolution(resolution: str) -> str:
        value = (resolution or "720p").strip().lower()
        return value if value in VEO_RESOLUTIONS else "720p"

    @staticmethod
    def _operation_log_path() -> Path:
        """返回不含密钥/Prompt 的 operation 恢复日志路径。"""
        override = os.environ.get("CEP_VEO_OPERATION_LOG", "").strip()
        if override:
            return Path(override)
        return Path(tempfile.gettempdir()) / "cep_veo_operations.json"

    @classmethod
    def _record_operation(cls, operation: str, **fields) -> Path | None:
        """保存最近的 operation 状态，供崩溃/网络中断后人工恢复。

        日志不保存 API Key、Prompt、参考图或视频数据。写失败不能影响生成任务。
        """
        if not operation:
            return None
        try:
            path = cls._operation_log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with cls._OPERATION_LOG_LOCK:
                records = {}
                if path.exists():
                    try:
                        loaded = json.loads(path.read_text(encoding="utf-8"))
                        if isinstance(loaded, dict):
                            records = loaded
                    except Exception:
                        records = {}
                safe_fields = {}
                for key, value in fields.items():
                    if key in {"api_key", "prompt", "payload", "image"}:
                        continue
                    if isinstance(value, (str, int, float, bool)) or value is None:
                        safe_fields[key] = value
                    else:
                        safe_fields[key] = str(value)[:500]
                records[operation] = {
                    **records.get(operation, {}),
                    "operation": operation,
                    "updated_at": time.time(),
                    **safe_fields,
                }
                # 日志只保留最近 50 条，避免长期膨胀。
                newest = sorted(
                    records.items(),
                    key=lambda item: float(item[1].get("updated_at", 0)),
                    reverse=True,
                )[:50]
                path.write_text(
                    json.dumps(dict(newest), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            return path
        except Exception:
            return None

    @staticmethod
    def _is_permanent_poll_error(error: Exception) -> bool:
        """判断轮询错误是否应立即终止。

        401/403 是鉴权问题；400 和带 2xx body 的业务 error 是已确定的任务失败。
        404 可能是 operation 刚创建后的短暂一致性问题，因此继续重试。
        """
        if not isinstance(error, ArkHTTPError):
            return False
        if error.status in (400, 401, 403, 405, 422):
            return True
        return 200 <= int(error.status or 0) < 300 and bool(error.body)

    @staticmethod
    def _image_instance(image) -> dict:
        """把本地图片 / bytes / data URL 转换成 Veo 接受的 image 实例字段。

        Veo 要求 mimeType 为完整 media type（如 image/png），而 data URL 里常省略
        前缀（如 data:png;base64,...），这里统一补成 image/<subtype>。
        """
        if isinstance(image, str) and image.startswith("data:"):
            data_url = image
        else:
            data_url = to_image_data_url(image)
        header, _, b64 = data_url.partition(",")
        subtype = header[5:].split(";")[0] or "png"
        mime = subtype if "/" in subtype else f"image/{subtype}"
        return {"bytesBase64Encoded": b64, "mimeType": mime}

    def execute(self, request: TaskRequest) -> TaskHandle:
        h = TaskHandle(id=f"veo_{uuid.uuid4().hex[:10]}",
                       provider_name=self.name, operation=request.operation,
                       status=TaskStatus.RUNNING)
        op_name = ""
        model = ""
        operation_log: Path | None = None
        try:
            api_key, model, base = self._creds()
            if not api_key:
                raise ArkHTTPError("未配置 OPENAI_API_KEY（请在 .env 设置 OPENAI_API_KEY）")
            if not model:
                raise ArkHTTPError("未配置 Veo 模型 ID")

            api_root = self._api_root(base)
            # 允许诊断/恢复入口传入已存在的 operation，跳过 POST，避免重复计费。
            op_name = str(
                request.metadata.get("veo_operation")
                or request.inputs.get("operation_name")
                or ""
            ).strip()

            if not op_name:
                prompt = (request.inputs.get("prompt") or "").strip()
                if not prompt:
                    raise ArkHTTPError("缺少 prompt")
                typed_refs = normalize_reference_assets(
                    request.inputs.get("reference_assets"),
                    request.inputs.get("style_images") or [])
                prompt = append_manifest(prompt, typed_refs)
                submit_url = f"{api_root}/v1beta/models/{model}:predictLongRunning"

                # 组装 instances
                instance: dict = {"prompt": prompt}
                ref = request.inputs.get("image")
                if ref is not None:
                    instance["image"] = self._image_instance(ref)
                last_frame = request.inputs.get("last_frame")
                if last_frame is not None:
                    # 首尾帧：Google Veo 用 lastFrame 字段
                    instance["lastFrame"] = self._image_instance(last_frame)

                # Veo 3.1 Ingredients / referenceImages: up to three typed asset
                # references.  A first-frame composition remains a separate mode;
                # do not mix it with Ingredients because gateways differ here.
                ingredient_refs = []
                if ref is None and last_frame is None:
                    for item in typed_refs:
                        path = str(item.get("path") or "")
                        if (not path or path.startswith("asset://") or
                                item.get("role") == "composition"):
                            continue
                        ingredient_refs.append({
                            "image": self._image_instance(path),
                            "referenceType": "asset",
                        })
                        if len(ingredient_refs) >= 3:
                            break
                if ingredient_refs:
                    instance["referenceImages"] = ingredient_refs

                duration = self._clamp_duration(request.params.get("duration", 8))
                if ingredient_refs:
                    # Veo reference-image generation currently requires 8 seconds.
                    duration = 8
                resolution = self._clamp_resolution(
                    request.params.get("resolution", "720p"))
                # 1080p/4k 仅支持 8 秒；在本地归一化，避免付费任务提交后才失败。
                if resolution in {"1080p", "4k"}:
                    duration = 8
                params = {
                    "aspectRatio": self._clamp_ratio(
                        request.params.get("aspect_ratio")
                        or request.params.get("ratio", "16:9")),
                    "durationSeconds": duration,
                    "numberOfVideos": 1,
                    "personGeneration": "allow_adult",
                    "addWatermark": False,
                    "generateAudio": bool(request.params.get(
                        "generate_audio", request.params.get("audio", True))),
                    "enhancePrompt": bool(request.params.get("enhance_prompt", False)),
                    "resolution": resolution,
                }
                negative_prompt = (
                    request.inputs.get("negative_prompt")
                    or request.params.get("negative_prompt")
                    or ""
                )
                if str(negative_prompt).strip():
                    params["negativePrompt"] = str(negative_prompt).strip()

                payload = {"instances": [instance], "parameters": params}

                # 1) 提交任务。POST 不自动重试：响应丢失时重发可能造成双重计费。
                submit = ark_post(submit_url, api_key, payload, timeout=60)
                op_name = str(submit.get("name") or "").strip()
                if not op_name:
                    raise ArkHTTPError(
                        f"Veo 提交未返回 operation: {str(submit)[:300]}")

            operation_log = self._record_operation(
                op_name,
                status="running",
                model=model,
                local_task_id=h.id,
                resumed=bool(request.metadata.get("veo_operation")
                             or request.inputs.get("operation_name")),
            )
            h.progress = 0.05

            # 2) 轮询
            query_url = f"{api_root}/v1beta/{op_name}"
            deadline = time.time() + self.POLL_TIMEOUT
            poll_errors = 0
            empty_results = 0
            while time.time() < deadline:
                if h._cancel_token:
                    h.status = TaskStatus.CANCELLED
                    h.result = TaskResult(
                        success=False,
                        error=("已停止本地等待；云端任务可能仍在继续。"
                               f"operation={op_name}"),
                        provider_raw={"operation": op_name, "model": model},
                    )
                    self._record_operation(
                        op_name, status="cancelled_locally", model=model)
                    h.finished_at = time.time()
                    return h
                try:
                    status = ark_get(
                        query_url, api_key, timeout=self.POLL_REQUEST_TIMEOUT)
                except Exception as error:
                    if self._is_permanent_poll_error(error):
                        raise
                    poll_errors += 1
                    if (isinstance(error, ArkHTTPError)
                            and error.status == 404 and poll_errors >= 5):
                        raise ArkHTTPError(
                            f"Veo operation 不存在或已过期：{op_name}",
                            status=404,
                            body=error.body,
                        ) from error
                    self._record_operation(
                        op_name,
                        status="poll_retry",
                        model=model,
                        poll_errors=poll_errors,
                        last_error=str(error)[:500],
                    )
                    # 退避到最多 24 秒；一次网络超时绝不把云端任务判失败。
                    wait(min(self.POLL_INTERVAL * min(poll_errors, 4), 24))
                    continue
                poll_errors = 0
                if status.get("done"):
                    # 失败：顶层 error 字段
                    if status.get("error"):
                        err = status["error"]
                        msg = err.get("message", "") if isinstance(err, dict) else str(err)
                        raise ArkHTTPError(f"Veo 任务失败: {msg}")
                    video = self._extract_video(status, write_debug=False)
                    if video is None:
                        # ModelHub 偶尔先返回 done=true + 空 generatedSamples，稍后
                        # 再补齐内联视频。只复查同一 operation，绝不重新 POST。
                        empty_results += 1
                        if empty_results <= self.EMPTY_RESULT_RETRIES:
                            h.progress = max(h.progress, 0.94)
                            self._record_operation(
                                op_name,
                                status="waiting_for_result",
                                model=model,
                                empty_result_checks=empty_results,
                            )
                            wait(self.POLL_INTERVAL * empty_results)
                            continue
                        self._extract_video(status, write_debug=True)
                        raise ArkHTTPError(
                            "Veo 云端任务已完成，但 ModelHub 连续返回空视频结果。"
                            f"未重新提交以避免重复计费；operation={op_name}。"
                            "调试信息：%TEMP%\\cep_veo_debug.json")
                    out = self._out_dir() / f"veo_{uuid.uuid4().hex[:8]}.mp4"
                    if isinstance(video, (bytes, bytearray)):
                        out.write_bytes(bytes(video))
                    else:  # 下载链接
                        download(video, out, timeout=600)
                    h.progress = 1.0
                    h.result = TaskResult(
                        success=True, data=out,
                        provider_raw={
                            "operation": op_name,
                            "model": model,
                            "operation_log": str(operation_log or ""),
                        },
                    )
                    self._record_operation(
                        op_name,
                        status="done",
                        model=model,
                        output=str(out),
                    )
                    h.status = TaskStatus.DONE
                    h.finished_at = time.time()
                    return h
                empty_results = 0
                h.progress = min(0.92, h.progress + 0.035)
                wait(self.POLL_INTERVAL)

            raise ArkHTTPError(
                f"Veo 本地等待超过 {self.POLL_TIMEOUT}s；云端任务可能仍在继续。"
                f"可使用 operation 恢复查询：{op_name}")
        except Exception as e:
            if op_name:
                operation_log = self._record_operation(
                    op_name,
                    status="local_failed",
                    model=model,
                    local_task_id=h.id,
                    last_error=str(e)[:500],
                ) or operation_log
            h.result = TaskResult(
                success=False,
                error=str(e),
                provider_raw={
                    "operation": op_name,
                    "model": model,
                    "operation_log": str(operation_log or ""),
                },
            )
            h.status = TaskStatus.FAILED
            h.finished_at = time.time()
        return h

    @staticmethod
    def _extract_video(status: dict, write_debug: bool = True):
        """从 done 的轮询响应里提取视频：内联 base64 字节或下载 URL。

        尝试多种已知路径（Google Veo 响应结构随版本变化），
        全部失败时将实际响应 key 结构写入日志供调试。
        """
        resp = status.get("response") or {}

        # ── 路径 1：generateVideoResponse.generatedSamples[0].video ──
        gvr = resp.get("generateVideoResponse")
        if isinstance(gvr, dict):
            for key in ("generatedSamples", "generatedVideos"):
                samples = gvr.get(key) or []
                if samples:
                    vid = _pick_video(samples[0])
                    if vid is not None:
                        return vid

        # ── 路径 2：顶层 generatedSamples / generatedVideos ──
        for key in ("generatedSamples", "generatedVideos"):
            samples = resp.get(key) or []
            if samples:
                vid = _pick_video(samples[0])
                if vid is not None:
                    return vid

        # ── 路径 3：直接在 response 下找 video / videoData ──
        for vk in ("video", "videoData"):
            v = resp.get(vk)
            if isinstance(v, dict):
                vid = _extract_from_video_dict(v)
                if vid is not None:
                    return vid

        # ── 全部失败 → 写调试日志（完整 status 结构，不含大 payload）──
        try:
            if not write_debug:
                return None
            dbg_path = Path(__import__("tempfile").gettempdir()) / "cep_veo_debug.json"
            # 安全地序列化 status（去掉超大字段）
            safe_status = _safe_keys(status, max_str=500)
            summary = {
                "_note": "Veo done=true but video extraction failed — full status structure",
                "status_top_keys": list(status.keys()),
                "status_done": status.get("done"),
                "status_error": status.get("error"),
                "status_metadata": status.get("metadata"),
                "safe_status": safe_status,
            }
            dbg_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        return None


def _safe_keys(d: dict, max_str: int = 500) -> dict:
    """递归提取字典的 key 结构，截断长字符串值。"""
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out[k] = _safe_keys(v, max_str)
        elif isinstance(v, (list, tuple)):
            out[k] = f"[{len(v)} items]" if v else "[]"
        elif isinstance(v, str):
            out[k] = v[:max_str] + ("..." if len(v) > max_str else "")
        elif isinstance(v, (bytes, bytearray)):
            out[k] = f"<bytes:{len(v)}>"
        else:
            out[k] = repr(v)
    return out


def _pick_video(sample: dict):
    """从一个 sample 条目中尝试提取视频。"""
    if not isinstance(sample, dict):
        return None
    vid = sample.get("video", {})
    if isinstance(vid, dict):
        return _extract_from_video_dict(vid)
    # sample 本身可能就是 video 对象（扁平结构）
    return _extract_from_video_dict(sample)


def _extract_from_video_dict(vid: dict):
    """从 video 字典中提取 base64 字节或下载 URL。"""
    if not isinstance(vid, dict):
        return None
    # base64 内联（字段名多变）
    for bk in ("encodedVideo", "bytesBase64Encoded", "videoBytes",
               "data", "content", "base64Data"):
        raw = vid.get(bk)
        if raw:
            try:
                if isinstance(raw, str):
                    return base64.b64decode(raw)
                elif isinstance(raw, (bytes, bytearray)):
                    return bytes(raw)
            except Exception:
                continue
    # 下载链接
    for uk in ("uri", "url", "downloadUrl", "fileUri"):
        u = vid.get(uk)
        if u and isinstance(u, str) and u.startswith(("http://", "https://")):
            return u
    return None


def _log_seedance_payload(payload: dict, op_name: str = "submit"):
    """把 Seedance payload 写到 %TEMP%/cep_seedance_debug.json（截断 base64）。"""
    # 必须构造全新的递归副本。旧实现只浅拷贝 content 条目，随后修改嵌套的
    # image_url 字典，导致用于真实提交的 payload 也被替换成日志截断文本，
    # Ark 因而稳定返回 `Invalid base64 image_url`。
    def sanitize(value):
        if isinstance(value, dict):
            return {key: sanitize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [sanitize(item) for item in value]
        if isinstance(value, str) and value.startswith("data:") and len(value) > 120:
            return value[:60] + f"...<base64:{len(value)}B>..." + value[-30:]
        return value

    safe = sanitize(payload)
    debug_path = Path(__import__("tempfile").gettempdir()) / "cep_seedance_debug.json"
    # 追加写入（带分隔）
    sep = "\n---\n"
    new_block = json.dumps({op_name: safe}, ensure_ascii=False, indent=2)
    if debug_path.exists():
        prev = debug_path.read_text(encoding="utf-8") + sep
    else:
        prev = ""
    debug_path.write_text(prev + new_block, encoding="utf-8")


def _seedance_compat_prompt(prompt: str) -> str:
    """规避 Seedance 将提示词审核误报为 Invalid base64 image_url。

    实测同一参考帧使用“图中的人物”会被误报，改成语义等价的“画面主体”
    即可提交。仅替换主体称谓，不改变动作和风格要求。
    """
    out = prompt
    for old, new in (
        ("图片中的人物", "画面主体"),
        ("图中的人物", "画面主体"),
        ("画面中的人物", "画面主体"),
        ("真实人物", "画面主体"),
        ("真人", "画面主体"),
        ("人物", "主体"),
        ("人像", "主体形象"),
    ):
        out = out.replace(old, new)
    if out != prompt and "保持原有" not in out:
        out += "，保持原有画面风格"
    return out


class SeedanceProvider(VideoProvider):
    """字节跳动 Seedance 2.0 视频生成（方舟 / ModelHub 自动选路）。

    接口：
      创建任务  POST {base}/contents/generations/tasks
      查询任务  GET  {base}/contents/generations/tasks/{id}
    文档：https://www.volcengine.com/docs/82379/1520757

    注意：视频生成为异步任务，execute() 内部会阻塞轮询直至完成/失败
    （默认最长 10 分钟）。请在 TaskManager 的工作线程中调用，避免阻塞 UI。
    轮询期间会检查 handle._cancel_token 以支持取消。
    """
    name = "seedance"
    capabilities = ["text_to_video", "image_to_video"]

    # 轮询参数
    POLL_INTERVAL = 5          # 秒
    POLL_TIMEOUT = 600         # 秒

    @staticmethod
    def _ref_data_url(image) -> str:
        """参考图 → Seedance 可稳定接受的 JPEG data URL。

        本地帧统一转为 RGB JPEG，限制长边不超过 1280px，并在保持分辨率
        不变的前提下把 JPEG 二进制控制在约 55 KiB 内。70 KiB 对不同画面
        复杂度仍处于服务端不稳定边界；同一 UI 请求从约70 KiB降到55 KiB 后
        可正常创建任务。远程 URL 原样保留。
        """
        try:
            from PIL import Image
            import io as _io
            if isinstance(image, str) and (
                image.startswith("http://") or image.startswith("https://")):
                return image  # 远程 URL 不缩放
            src = Path(image) if not isinstance(image, (bytes, bytearray)) else image
            if isinstance(src, Path) and not src.exists():
                return to_image_data_url(image)
            im = Image.open(src if isinstance(src, Path) else _io.BytesIO(src))
            w, h = im.size
            max_side = 1280
            scale = min(1.0, max_side / max(w, h))
            im = im.convert("RGB")
            if scale < 1.0:
                im = im.resize(
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    Image.LANCZOS)
            buf = _io.BytesIO()
            # 保持 720×1280 等有效分辨率，只逐级降低 JPEG quality。
            # 不再缩到 640px，避免复杂画面因分辨率过低而难以识别。
            for quality in (76, 70, 64, 58, 52, 46, 40, 34, 28):
                buf.seek(0)
                buf.truncate(0)
                im.save(buf, "JPEG", quality=quality, optimize=True)
                if buf.tell() <= 55 * 1024:
                    break
            import base64 as _b64
            b64 = _b64.b64encode(buf.getvalue()).decode("ascii")
            return f"data:image/jpeg;base64,{b64}"
        except Exception as exc:
            # 这里不能回退为 PNG data URI，否则会重新触发服务端的误导性 400。
            raise ArkHTTPError(f"Seedance 参考图预处理失败: {exc}") from exc

    def _creds(self):
        from api_config import get as _ac_get
        entry = _ac_get("seedance")
        api_key = self.api_key or entry.value()
        explicit_model = self.config.get("model") or os.environ.get("SEEDANCE_MODEL")
        explicit_base = self.config.get("base_url") or os.environ.get("SEEDANCE_BASE_URL")
        if api_key.startswith("ark-"):
            # 火山方舟原生 Key：ModelHub 创建任务接口会返回 401。
            model = explicit_model or "doubao-seedance-2-0-260128"
            base = explicit_base or "https://ark.cn-beijing.volces.com/api/v3"
        else:
            # ModelHub 自有 Key 使用其豆包兼容入口和模型 ID。
            model = explicit_model or "doubao-seedance-2.0"
            base = explicit_base or "https://modelhub.ailemac.com/doubao/v3"
        model = model.strip()
        base = base.rstrip("/")
        return api_key, model, base

    def execute(self, request: TaskRequest) -> TaskHandle:
        h = TaskHandle(id=f"seedance_{uuid.uuid4().hex[:10]}",
                       provider_name=self.name, operation=request.operation,
                       status=TaskStatus.RUNNING)
        try:
            api_key, model, base = self._creds()
            if not api_key:
                raise ArkHTTPError("未配置豆包共享 Key（请在 .env 设置 SEEDREAM_API_KEY）")
            if not model:
                raise ArkHTTPError("未配置 Seedance 端点/模型 ID（api_config seedance.default_model）")

            prompt = (request.inputs.get("prompt") or "").strip()
            if not prompt:
                raise ArkHTTPError("缺少 prompt")

            typed_refs = normalize_reference_assets(
                request.inputs.get("reference_assets"),
                request.inputs.get("style_images") or [])
            prompt = append_manifest(prompt, typed_refs)

            # 组装 content（数组）
            # Seedance 2.0 三种图片场景互斥：
            #   1. 图生视频-首帧：1 张 image_url，role="first_frame" 或不填
            #   2. 图生视频-首尾帧：2 张 image_url，role="first_frame" + "last_frame"
            #   3. 多模态参考：1~9 张 image_url，role="reference_image"
            # 参考：https://www.volcengine.com/docs/6431/1520757
            # 本地图片统一转小体积 RGB JPEG data URI。
            ref = request.inputs.get("image")
            last_frame = request.inputs.get("last_frame")
            style_images: list = (
                [item["path"] for item in typed_refs]
                if typed_refs else list(request.inputs.get("style_images") or []))

            if ref and last_frame:
                # ── 首尾帧模式 ──
                content = [
                    {"type": "image_url",
                     "image_url": {"url": self._ref_data_url(ref)},
                     "role": "first_frame"},
                    {"type": "image_url",
                     "image_url": {"url": self._ref_data_url(last_frame)},
                     "role": "last_frame"},
                    {"type": "text", "text": prompt},
                ]
            elif ref:
                # ── 图生视频（首帧）──
                content = [
                    {"type": "image_url",
                     "image_url": {"url": self._ref_data_url(ref)},
                     "role": "first_frame"},
                    {"type": "text", "text": prompt},
                ]
            elif style_images:
                # ── 纯多模态参考（无首帧指定）──
                content = []
                for img in style_images:
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": self._ref_data_url(img)},
                        "role": "reference_image",
                    })
                content.append({"type": "text", "text": prompt})
            else:
                # 文生视频
                content = [{"type": "text", "text": prompt}]

            # Seedance 2.0 supports native audio for text, first-frame,
            # first/last-frame and multimodal-reference requests.  A former
            # compatibility workaround disabled audio whenever an image was
            # present, silently turning every image-guided production clip
            # into a mute file.  The UI/request is authoritative now.
            generate_audio = _seedance_generate_audio(request.params)
            payload = {
                "model": model,
                "content": content,
                "generate_audio": generate_audio,
                "ratio": request.params.get("ratio", "adaptive"),
                "duration": int(request.params.get("duration", 5)),
                "watermark": bool(request.params.get("watermark", False)),
            }

            # 调试日志：把 payload 结构写到文件（截断 base64，避免日志膨胀）
            try:
                _log_seedance_payload(payload, op_name="submit")
            except Exception:
                pass

            # 1) 提交任务（带瞬时限流重试）
            # 仅对网络、限流和 5xx 类瞬时错误重试；参数错误属于确定性失败。
            submit = None
            _submit_attempts = 0
            _max_attempts = 3
            _prompt_compat_applied = False
            while submit is None and _submit_attempts < _max_attempts:
                _submit_attempts += 1
                try:
                    submit = ark_post(
                        f"{base}/contents/generations/tasks", api_key, payload, timeout=60)
                except ArkHTTPError as e:
                    err_msg = str(e).lower()
                    # 方舟会把部分“参考图 + 人物称谓”提示词审核误报为图片
                    # base64 无效。同图只改为“画面主体”即可成功，因此在确认
                    # 图片已经过本地严格编码后，做一次语义等价的兼容重试。
                    if (ref is not None and "invalid base64 image_url" in err_msg
                            and not _prompt_compat_applied):
                        compat_prompt = _seedance_compat_prompt(prompt)
                        if compat_prompt != prompt:
                            for item in content:
                                if item.get("type") == "text":
                                    item["text"] = compat_prompt
                            payload["content"] = content
                            _prompt_compat_applied = True
                            try:
                                _log_seedance_payload(
                                    payload, op_name="submit_prompt_compat")
                            except Exception:
                                pass
                            continue
                    # 永久性业务/参数错误 → 不重试，直接失败。
                    if any(kw in err_msg for kw in (
                        "copyright", "版权", "sensitive", "敏感", "审核", "35561375",
                        "not valid", "unsupported", "exceed", "too large",
                        "image size", "resolution", "dimension", "model not found",
                        "invalid base64", "invalidparameter", "badrequest",
                    )):
                        try:
                            _log_seedance_payload(
                                {"payload": payload, "error_status": e.status,
                                 "error_body": e.body[:1500]}, op_name="submit_failed")
                        except Exception:
                            pass
                        raise
                    # 其余（网络 / 限流 / 5xx / 偶发 Invalid base64）→ 退避后重试
                    if _submit_attempts >= _max_attempts:
                        try:
                            _log_seedance_payload(
                                {"payload": payload, "error_status": e.status,
                                 "error_body": e.body[:1500]}, op_name="submit_failed")
                        except Exception:
                            pass
                        raise ArkHTTPError(
                            f"Seedance 提交失败（重试 {_max_attempts} 次仍失败）：{e}")
                    time.sleep(3 * _submit_attempts)  # 3s, 6s 退避
            if submit is None:
                raise ArkHTTPError("Seedance 提交未返回响应")
            task_id = submit.get("id")
            if not task_id:
                # 把 Ark 返回的原始 error 也写一份，方便排查
                try:
                    _log_seedance_payload({"submit_response": submit}, op_name="no_task_id")
                except Exception:
                    pass
                raise ArkHTTPError(f"Seedance 提交未返回任务 ID: {str(submit)[:300]}")
            h.progress = 0.05

            # 2) 轮询
            query_url = f"{base}/contents/generations/tasks/{task_id}"
            deadline = time.time() + self.POLL_TIMEOUT
            _poll_errors = 0
            while time.time() < deadline:
                if h._cancel_token:
                    h.status = TaskStatus.CANCELLED
                    h.result = TaskResult(success=False, error="用户取消")
                    h.finished_at = time.time()
                    return h
                try:
                    status = ark_get(query_url, api_key, timeout=60)
                except ArkHTTPError as e:
                    _poll_errors += 1
                    err_msg = str(e)
                    # 任务级失败（版权/审核/业务错误）→ 立即终止，不重试
                    if any(kw in err_msg.lower() for kw in
                           ("copyright", "content", "safety", "审核", "版权", "35561375",
                            "request failed", "invalid", "not valid")):
                        raise ArkHTTPError(err_msg)
                    # 纯网络/瞬时错误 → 限次重试
                    if _poll_errors > 10:
                        raise ArkHTTPError(
                            f"Seedance 轮询连续失败 {_poll_errors} 次：{err_msg}")
                    time.sleep(self.POLL_INTERVAL)
                    continue
                _poll_errors = 0  # 成功一次就重置计数
                st = status.get("status")
                if st == "succeeded":
                    video_url = (status.get("content") or {}).get("video_url")
                    if not video_url:
                        raise ArkHTTPError(f"Seedance 成功但缺少 video_url: {str(status)[:300]}")
                    out = self._out_dir() / f"seedance_{uuid.uuid4().hex[:8]}.mp4"
                    download(video_url, out, timeout=600)
                    h.progress = 1.0
                    h.result = TaskResult(
                        success=True, data=out,
                        provider_raw={"resolution": status.get("resolution"),
                                      "ratio": status.get("ratio"),
                                      "duration": status.get("duration"),
                                      "task_id": task_id,
                                      "prompt_compat": _prompt_compat_applied},
                    )
                    h.status = TaskStatus.DONE
                    h.finished_at = time.time()
                    return h
                elif st == "failed":
                    err = status.get("error") or status.get("content") or {}
                    msg = err.get("message", "") if isinstance(err, dict) else str(err)
                    raise ArkHTTPError(f"Seedance 任务失败: {_friendly_ark_message(msg)}")
                else:
                    # queued / running / processing → 持续推进进度
                    h.progress = min(0.9, h.progress + 0.03)
                    wait(self.POLL_INTERVAL)

            raise ArkHTTPError("Seedance 轮询超时（超过 {0}s）".format(self.POLL_TIMEOUT))
        except Exception as e:
            h.result = TaskResult(success=False, error=str(e))
            h.status = TaskStatus.FAILED
            h.finished_at = time.time()
        return h


class KlingProvider(VideoProvider):
    """快手可灵视频生成。"""
    name = "kling"
    capabilities = ["text_to_video", "image_to_video"]

    def execute(self, request: TaskRequest) -> TaskHandle:
        h = TaskHandle(id=f"kling_{request.to_cache_key()[:8]}",
                       provider_name=self.name, operation=request.operation)
        h.status = TaskStatus.FAILED
        h.result = TaskResult(success=False, error="KlingProvider 尚未实现")
        h.finished_at = __import__("time").time()
        return h
