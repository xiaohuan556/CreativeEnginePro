"""User-facing classification for provider generation failures."""
from __future__ import annotations

import re


_REQUEST_ID_PATTERN = re.compile(
    r"(?:request(?:[\s_-]*id)?|x-request-id|apim-request-id)[\s:=\"']+([a-z0-9_-]{8,})",
    re.IGNORECASE,
)


def _request_suffix(raw: str) -> str:
    match = _REQUEST_ID_PATTERN.search(raw)
    return f"（请求编号：{match.group(1)}）" if match else ""


def public_generation_error(error) -> str:
    """Turn common provider payloads into concise, actionable Chinese text."""
    raw = str(error or "").strip()
    if not raw:
        return "操作失败，请稍后重试。"
    specific = moderation_failure(raw) or transient_gateway_failure(raw)
    if specific is not None:
        return specific["message"]
    suffix = _request_suffix(raw)
    patterns = (
        (r"certificate_verify_failed|certificate verify failed|self[- ]signed certificate|ssl certificate",
         "安全证书验证失败，请检查电脑时间、证书或网络代理设置。"),
        (r"insufficient[_\s-]?quota|quota exceeded|billing|credit balance|hard limit|余额不足|额度不足",
         "模型额度或账户余额不足，请检查模型账户的额度与计费状态。"),
        (r"rate[_\s-]?limit|too many requests|\b429\b",
         "请求过于频繁，请稍等一会儿再重试。"),
        (r"invalid api key|incorrect api key|authentication failed|unauthorized|\b401\b",
         "模型服务身份验证失败，请检查接口密钥。"),
        (r"permission denied|access denied|forbidden|\b403\b",
         "当前模型密钥没有执行此操作的权限。"),
        (r"model not found|unknown model|does not exist.*model",
         "所选模型不存在，请检查模型名称或配置。"),
        (r"connection refused|econnrefused|actively refused",
         "无法连接到模型服务，请检查服务地址和网络。"),
        (r"enotfound|name or service not known|getaddrinfo|\bdns\b",
         "无法解析服务地址，请检查网络或 DNS 设置。"),
        (r"failed to fetch|fetch failed|network error|network is unreachable|connection reset|econnreset",
         "网络连接失败，请检查网络后重试。"),
        (r"timed? out|timeout|\b504\b",
         "请求等待超时，任务可能仍在后台执行，请先查看任务状态再决定是否重试。"),
        (r"payload too large|request entity too large|file too large|\b413\b",
         "上传内容超过大小限制，请压缩文件后重试。"),
        (r"unsupported media type|unsupported file|\b415\b",
         "文件格式不受支持，请换成常见图片、视频或音频格式。"),
        (r"bad gateway|upstream error|\b502\b",
         "上游模型服务暂时异常，请稍后重试。"),
        (r"service unavailable|provider unavailable|temporarily unavailable|\b503\b",
         "模型服务暂时不可用，请稍后重试。"),
        (r"invalid request|invalid parameter|invalid argument|bad request|\b400\b",
         "提交的内容或参数不符合模型要求，请检查节点参数和参考素材。"),
        (r"no space left|disk full",
         "电脑磁盘空间不足，请清理空间后重试。"),
        (r"cancelled|canceled", "任务已取消。"),
    )
    for pattern, message in patterns:
        if re.search(pattern, raw, re.IGNORECASE):
            return f"{message}{suffix}"
    # Preserve already useful Chinese errors, but do not expose an opaque
    # provider JSON/HTML payload to desktop users.
    concise = re.sub(r"https?://\S+", "", raw, flags=re.IGNORECASE)
    concise = re.sub(r"\s+", " ", concise).strip(" ，,;；")
    if len(re.findall(r"[\u3400-\u9fff]", concise)) >= 4:
        return concise if len(concise) <= 240 else f"{concise[:237]}…"
    return f"模型服务返回了无法识别的错误，请稍后重试。{suffix}"


def moderation_failure(error) -> dict | None:
    text = str(error or "")
    lowered = text.lower()
    if not any(token in lowered for token in (
            "moderation_blocked", "safety system", "content policy",
            "safety policy", "被安全", "安全审核")):
        return None
    request_match = re.search(r"request(?: id)?[ '\":]+([a-z0-9-]{12,})", text,
                              flags=re.IGNORECASE)
    request_id = request_match.group(1) if request_match else ""
    return {
        "code": "IMAGE_SAFETY_REVIEW",
        "request_id": request_id,
        "title": "图片请求未通过安全审核",
        "message": (
            "这不是节点故障，也不会扣除为成功产出。服务端没有返回具体触发片段。\n\n"
            "请检查提示词和参考图：避免真实人物冒充、未成年人敏感内容、露骨性内容、"
            "血腥伤害或仇恨符号；正常剧情可改成明确的虚构成年角色，并用非血腥、"
            "电影化的表达后重试。"
            + (f"\n\n请求编号：{request_id}" if request_id else "")
        ),
    }


def transient_gateway_failure(error) -> dict | None:
    """Recognize proxy/gateway outages without exposing an HTML error page."""
    text = str(error or "")
    lowered = text.lower()
    status = next((code for code in (504, 503, 502)
                   if str(code) in lowered), 0)
    if not status and not any(token in lowered for token in (
            "gateway timeout", "cloudfront", "service unavailable",
            "upstream timed out")):
        return None
    request_match = re.search(r"request(?: id)?\s*[:：]\s*([a-z0-9_-]{8,})",
                              text, flags=re.IGNORECASE)
    request_id = request_match.group(1) if request_match else ""
    return {
        "code": f"UPSTREAM_{status or 'UNAVAILABLE'}",
        "request_id": request_id,
        "title": "AI 服务暂时超时",
        "message": (
            "上游 AI 服务没有在网关时限内返回结果。项目和原稿均已保留，"
            "这不是内容审核拒绝。请稍后直接重试当前操作；如果连续出现，"
            "可切换另一个文本模型再执行。"
            + (f"\n\n请求编号：{request_id}" if request_id else "")
        ),
    }
