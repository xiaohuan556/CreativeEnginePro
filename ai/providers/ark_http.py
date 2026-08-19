"""
火山方舟（Volcengine Ark）HTTP 客户端 —— 纯标准库实现，无第三方依赖。

所有 Ark 调用（Seedream 图片 / Seedance 视频）共用：
  * ark_post(url, api_key, payload, timeout)  → 解析 JSON
  * ark_get(url, api_key, timeout)            → 解析 JSON
  * download(url, dest, timeout)              → 把远程文件落到本地 dest

Ark 鉴权统一为请求头：  Authorization: Bearer <ARK_API_KEY>
"""
from __future__ import annotations

import json
import ssl
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Optional

_CTX = ssl.create_default_context()


# 火山方舟常见业务错误 → 中文友好提示
# key 为「英文原消息里的特征片段」，命中即替换为更易懂的中文说明 + 解决建议。
_ARK_ERROR_HINTS = [
    (
        "privacyinformation",
        "参考图包含可识别的真实人物，触发了 Seedance 的真人隐私保护策略。"
        "普通本地截图无法作为真人参考图直接生成；请改用非真人素材、"
        "在方舟完成真人素材授权，或切换 Veo 引擎。",
    ),
    (
        "may contain real person",
        "参考图包含可识别的真实人物，触发了 Seedance 的真人隐私保护策略。"
        "请改用非真人素材、已授权真人资产，或切换 Veo 引擎。",
    ),
    (
        "35561375",
        "内容审核未通过：你的 prompt 或参考图触发了火山方舟的内容安全策略"
        "（错误码 35561375）。请修改描述后重试——避免具体人名、真实品牌、"
        "特定地点或敏感场景，或用更抽象的表述；也可改用「Veo」引擎（审核相对宽松）。",
    ),
    (
        "interests of third-party content providers",
        "内容审核未通过：你的 prompt 或参考图触发了火山方舟的内容安全策略。"
        "请修改描述（避免具体人名 / 品牌 / 敏感场景）后重试，或改用「Veo」引擎。",
    ),
    (
        "copyright",
        "参考图可能包含版权内容（影视画面 / 品牌 / 受保护素材），火山方舟拒绝生成。"
        "请换用原创图片或改用「Veo」引擎重试。",
    ),
]


def _friendly_ark_message(msg: str) -> str:
    """把 Ark 英文业务错误转成中文友好提示（命中已知特征时）。"""
    if not msg:
        return msg
    for frag, hint in _ARK_ERROR_HINTS:
        if frag.lower() in msg.lower():
            return f"Ark 错误：{hint}"
    return msg


def _http_error_message(status: int, raw: str) -> str:
    """Extract Ark's useful message instead of exposing a raw JSON blob."""
    message = ""
    code = ""
    try:
        parsed = json.loads(raw)
        error = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(error, dict):
            message = str(error.get("message") or "")
            code = str(error.get("code") or "")
        elif error:
            message = str(error)
    except (TypeError, ValueError, json.JSONDecodeError):
        message = ""
    friendly = _friendly_ark_message(message or raw)
    if friendly != (message or raw):
        return friendly
    detail = friendly.strip() or "请求被服务端拒绝"
    code_label = f" · {code}" if code else ""
    return f"Ark HTTP {status}{code_label}：{detail[:500]}"


class ArkHTTPError(RuntimeError):
    """Ark 返回非 2xx，或业务字段里带 error。"""

    def __init__(self, message: str, status: int = 0, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def _request(method: str, url: str, *, api_key: str = "",
             data: Optional[bytes] = None, headers: Optional[dict] = None,
             timeout: int = 120) -> Any:
    hdrs = dict(headers or {})
    if api_key:
        hdrs["Authorization"] = f"Bearer {api_key}"
    if data is not None and "Content-Type" not in hdrs:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.getcode()
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace") if e.fp else ""
        raise ArkHTTPError(
            _http_error_message(e.code, raw), status=e.code, body=raw) from e
    except urllib.error.URLError as e:
        raise ArkHTTPError(f"Ark 网络错误: {e.reason}") from e

    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise ArkHTTPError(f"Ark 返回非 JSON: {raw[:500]}", status=status, body=raw)

    # Ark 业务错误通常放在顶层 error 字段
    if isinstance(parsed, dict) and parsed.get("error"):
        err = parsed["error"]
        msg = err.get("message", "") if isinstance(err, dict) else str(err)
        raise ArkHTTPError(f"Ark 业务错误: {_friendly_ark_message(msg)}",
                           status=status, body=raw)
    return parsed


def ark_post(url: str, api_key: str, payload: dict, timeout: int = 120) -> Any:
    """POST JSON，返回解析后的响应体。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _request("POST", url, api_key=api_key, data=body, timeout=timeout)


def ark_get(url: str, api_key: str, timeout: int = 120) -> Any:
    """GET，返回解析后的响应体。"""
    return _request("GET", url, api_key=api_key, timeout=timeout)


def download(url: str, dest: Path, timeout: int = 600) -> Path:
    """把远程文件下载到 dest（覆盖写）。返回 dest。"""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "CreativeEnginePro/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
            data = resp.read()
    except urllib.error.URLError as e:
        raise ArkHTTPError(f"下载失败: {e.reason}") from e
    dest.write_bytes(data)
    return dest


def to_image_data_url(image) -> str:
    """把本地图片（Path / bytes）转成 Ark 接受的 data URL；已是 http(s) 则原样返回。

    Ark 图片生成接口 `image` 字段支持：远程 URL 或 `data:<mime>;base64,...`。
    """
    if isinstance(image, str):
        if image.startswith("http://") or image.startswith("https://"):
            return image
        image = Path(image)
    if isinstance(image, Path):
        if image.exists():
            data = image.read_bytes()
        else:
            raise ArkHTTPError(f"参考图不存在: {image}")
    elif isinstance(image, (bytes, bytearray)):
        data = bytes(image)
    else:
        raise ArkHTTPError(f"不支持的图片输入类型: {type(image)}")

    # 简单按文件头判断 mime（种子方舟支持 png / jpeg / webp / bmp / tiff / gif）
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    elif data[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    elif data[:4] in (b"RIFF",) and data[8:12] == b"WEBP":
        mime = "image/webp"
    elif data[:2] == b"BM":
        mime = "image/bmp"
    elif data[:4] == b"GIF8":
        mime = "image/gif"
    elif data[:4] in (b"II*\x00", b"MM\x00*"):
        mime = "image/tiff"
    else:
        mime = "image/png"
    import base64
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def wait(seconds: float):
    """可被取消逻辑替换的占位（保持代码清晰）。"""
    time.sleep(seconds)
