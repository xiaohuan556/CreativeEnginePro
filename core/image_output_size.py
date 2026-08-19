"""AI 图片输出比例到各 Provider size 参数的统一映射。"""
from __future__ import annotations


ASPECT_OPTIONS = (
    ("original", "原图比例"),
    ("9:16", "9:16 竖屏"),
    ("4:5", "4:5 竖幅"),
    ("1:1", "1:1 方形"),
    ("16:9", "16:9 横屏"),
)


_GPT_SIZES = {
    "original": "auto",
    "9:16": "1024x1792",
    "4:5": "1024x1280",
    "1:1": "1024x1024",
    "16:9": "1792x1024",
}

_SEEDREAM_LONG_EDGE = {
    "1K": 1280,
    "2K": 2048,
    "4K": 4096,
}


def _aligned(value: float) -> int:
    return max(256, int(round(value / 16.0)) * 16)


def normalize_aspect_ratio(value, default: str = "16:9") -> str:
    """Return one canonical project delivery ratio.

    Old projects occasionally persisted full-width colons or UI labels.  Keep
    those readable and use the caller's explicit fallback for unknown values.
    """
    text = str(value or "").strip().lower().replace("：", ":")
    aliases = {
        "horizontal":"16:9", "landscape":"16:9", "横屏":"16:9",
        "vertical":"9:16", "portrait":"9:16", "竖屏":"9:16",
        "square":"1:1", "方形":"1:1", "竖幅":"4:5",
    }
    text = aliases.get(text, text)
    supported = {item[0] for item in ASPECT_OPTIONS if item[0] != "original"}
    return text if text in supported else default


def aspect_ratio_value(value, default: str = "16:9") -> float:
    aspect = normalize_aspect_ratio(value, default)
    width, height = aspect.split(":", 1)
    return int(width) / max(1, int(height))


def resolve_image_output_size(engine: str, base_size: str, aspect: str) -> str:
    """将统一比例设置转换为 GPT-Image / Seedream 的 size 参数。

    original:
      - GPT-Image 使用 auto，由图生图接口按参考图决定。
      - Seedream 保留 1K/2K/4K 档位，由模型按参考图比例决定。
    """
    engine = (engine or "gptimage").lower()
    raw_aspect = str(aspect or "").strip().replace("：", ":")
    aspect = (raw_aspect if raw_aspect in {item[0] for item in ASPECT_OPTIONS}
              else "original")
    if engine == "gptimage":
        return _GPT_SIZES[aspect]

    tier = (base_size or "2K").upper()
    if tier not in _SEEDREAM_LONG_EDGE:
        tier = "2K"
    if aspect == "original":
        return tier

    long_edge = _SEEDREAM_LONG_EDGE[tier]
    if aspect == "1:1":
        return f"{long_edge}x{long_edge}"
    ratio = aspect_ratio_value(aspect)
    short_edge = _aligned(long_edge * min(ratio, 1 / ratio))
    if aspect == "9:16":
        return f"{short_edge}x{long_edge}"
    if ratio < 1:
        return f"{short_edge}x{long_edge}"
    return f"{long_edge}x{short_edge}"
