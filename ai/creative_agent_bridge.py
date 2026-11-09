"""Deterministic routing for locally executable canvas Agent tasks."""
from __future__ import annotations

import re


def resolve_agent_route(instruction: str, selected: str = "auto",
                        media_types: tuple[str, ...] = ()) -> str:
    if selected in {"image", "video", "audio", "editor"}:
        return selected
    text = str(instruction or "").lower()
    if any(word in text for word in ("剪辑台", "时间轴", "多轨", "剪辑工作台")):
        return "editor"
    if any(word in text for word in ("配音", "朗读", "语音", "音效", "旁白")):
        return "audio"
    if any(word in text for word in ("生成视频", "做视频", "图生视频", "视频生成", "动态视频")):
        return "video"
    if any(word in text for word in ("生成图片", "生图", "改图", "修图", "绘图", "图片生成")):
        return "image"
    if "video" in media_types and any(word in text for word in (
            "剪", "转场", "字幕", "静音", "模糊", "缩放", "放大", "淡入", "淡出")):
        return "editor"
    return ""


def plan_local_edit(instruction: str) -> dict:
    """Parse only edits that can be applied to desktop timeline data."""
    text = str(instruction or "").strip()
    plan = {"import": True, "mute": False, "blur_radius": None,
            "scale": None, "subtitle": "", "fade": False}
    if not text or re.fullmatch(r"(?:把素材)?送到(?:剪辑台|时间轴)|导入(?:剪辑台|时间轴)", text):
        return plan
    remaining = text
    if "静音" in remaining or "去除原声" in remaining:
        plan["mute"] = True
        remaining = remaining.replace("静音", "").replace("去除原声", "")
    if "模糊" in remaining:
        match = re.search(r"模糊\s*(\d{1,2})", remaining)
        plan["blur_radius"] = min(50, int(match.group(1))) if match else 10
        remaining = remaining.replace(match.group(0) if match else "模糊", "")
    match = re.search(r"(?:放大|缩放)\s*(\d{2,3})\s*%", remaining)
    if match:
        plan["scale"] = max(0.1, min(5.0, int(match.group(1)) / 100.0))
        remaining = remaining.replace(match.group(0), "")
    match = re.search(r"(?:添加|加)字幕[：:]\s*[“\"']?([^、，,；;。\n”\"']+)", remaining)
    if match:
        plan["subtitle"] = match.group(1).strip()
        remaining = remaining.replace(match.group(0), "")
    if "淡入淡出" in remaining:
        plan["fade"] = True
        remaining = remaining.replace("淡入淡出", "")
    remaining = re.sub(r"(?:请|把|给|将|这些|所有|素材|视频|图片|并|和|再|然后|到|送到剪辑台|送到时间轴|导入剪辑台|导入时间轴|添加|进行|处理|一下|的|原声|、|，|,|。|；|;|\s)+", "", remaining)
    if remaining:
        raise ValueError(f"暂不支持自动执行：{remaining[:50]}。请使用明确的本地操作指令。")
    return plan
