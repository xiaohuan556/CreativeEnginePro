"""User-facing classification for provider generation failures."""
from __future__ import annotations

import re


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
