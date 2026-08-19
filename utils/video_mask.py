"""视频片段几何蒙版：形状、旋转、羽化、反转以及 QImage alpha 合成。"""
from __future__ import annotations

from collections import OrderedDict
import math


_MASK_CACHE: OrderedDict[tuple, object] = OrderedDict()
_MASK_CACHE_LIMIT = 32


def evaluate_mask_values(clip, rel_time: float | None = None) -> dict:
    values = {
        "mask_x": float(getattr(clip, "mask_x", 0.0)),
        "mask_y": float(getattr(clip, "mask_y", 0.0)),
        "mask_width": float(getattr(clip, "mask_width", 0.65)),
        "mask_height": float(getattr(clip, "mask_height", 0.65)),
        "mask_rotation": float(getattr(clip, "mask_rotation", 0.0)),
        "mask_feather": float(getattr(clip, "mask_feather", 0.0)),
    }
    if rel_time is not None:
        keyframes = getattr(clip, "keyframes", None) or {}
        mask_keyframes = {key: keyframes[key] for key in values if keyframes.get(key)}
        if mask_keyframes:
            try:
                from core.edit_engine import interpolate_keyframes
                values = interpolate_keyframes(clip, mask_keyframes, rel_time, values)
            except Exception:
                pass
    values["mask_type"] = getattr(clip, "mask_type", "rectangle") or "rectangle"
    values["mask_inverted"] = bool(getattr(clip, "mask_inverted", False))
    return values


def build_mask_alpha(width: int, height: int, values: dict):
    """返回 uint8 alpha 蒙版；白色区域保留，黑色区域透明。"""
    import cv2
    import numpy as np

    width, height = max(1, int(width)), max(1, int(height))
    mask_type = str(values.get("mask_type", "rectangle"))
    mx = max(-1.5, min(1.5, float(values.get("mask_x", 0.0))))
    my = max(-1.5, min(1.5, float(values.get("mask_y", 0.0))))
    mw = max(0.01, min(2.5, float(values.get("mask_width", 0.65))))
    mh = max(0.01, min(2.5, float(values.get("mask_height", 0.65))))
    rotation = float(values.get("mask_rotation", 0.0))
    feather = max(0.0, min(1.0, float(values.get("mask_feather", 0.0))))
    inverted = bool(values.get("mask_inverted", False))
    cache_key = (
        width, height, mask_type, round(mx, 3), round(my, 3), round(mw, 3),
        round(mh, 3), round(rotation, 2), round(feather, 3), inverted)
    cached = _MASK_CACHE.get(cache_key)
    if cached is not None:
        _MASK_CACHE.move_to_end(cache_key)
        return cached

    yy, xx = np.ogrid[:height, :width]
    cx = width * (0.5 + mx * 0.5)
    cy = height * (0.5 + my * 0.5)
    dx = xx - cx
    dy = yy - cy
    angle = math.radians(rotation)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    local_x = cos_a * dx + sin_a * dy
    local_y = -sin_a * dx + cos_a * dy
    half_w = max(0.5, width * mw * 0.5)
    half_h = max(0.5, height * mh * 0.5)

    if mask_type == "linear":
        inside = local_y <= 0
    elif mask_type == "mirror":
        inside = (abs(local_y) <= half_h) & (abs(local_x) <= width * 1.5)
    elif mask_type == "circle":
        inside = (local_x / half_w) ** 2 + (local_y / half_h) ** 2 <= 1.0
    elif mask_type == "heart":
        nx = local_x / half_w
        ny = -local_y / half_h
        inside = (nx * nx + ny * ny - 1.0) ** 3 - nx * nx * ny ** 3 <= 0
    elif mask_type == "star":
        alpha = np.zeros((height, width), dtype=np.uint8)
        points = []
        for index in range(10):
            theta = -math.pi / 2 + index * math.pi / 5
            radius = 1.0 if index % 2 == 0 else 0.43
            lx = math.cos(theta) * half_w * radius
            ly = math.sin(theta) * half_h * radius
            px = cx + cos_a * lx - sin_a * ly
            py = cy + sin_a * lx + cos_a * ly
            points.append((round(px), round(py)))
        cv2.fillPoly(alpha, [np.asarray(points, dtype=np.int32)], 255)
        inside = alpha > 0
    else:  # rectangle
        inside = (abs(local_x) <= half_w) & (abs(local_y) <= half_h)

    if mask_type != "star":
        alpha = np.where(inside, 255, 0).astype(np.uint8)
    if feather > 0.001:
        sigma = max(0.5, feather * min(width, height) * 0.20)
        alpha = cv2.GaussianBlur(alpha, (0, 0), sigmaX=sigma, sigmaY=sigma)
    if inverted:
        alpha = 255 - alpha

    _MASK_CACHE[cache_key] = alpha
    _MASK_CACHE.move_to_end(cache_key)
    while len(_MASK_CACHE) > _MASK_CACHE_LIMIT:
        _MASK_CACHE.popitem(last=False)
    return alpha


def apply_video_mask(image, clip, rel_time: float | None = None):
    """把 clip 的蒙版乘到 QImage 原 alpha 上；未启用时原样返回。"""
    if not getattr(clip, "mask_enabled", False) or image is None or image.isNull():
        return image
    try:
        import numpy as np
        from PyQt6.QtGui import QImage

        rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = rgba.width(), rgba.height()
        ptr = rgba.bits()
        ptr.setsize(height * rgba.bytesPerLine())
        pixels = np.frombuffer(ptr, np.uint8).reshape(
            (height, rgba.bytesPerLine() // 4, 4))[:, :width].copy()
        mask = build_mask_alpha(width, height, evaluate_mask_values(clip, rel_time))
        pixels[:, :, 3] = (
            pixels[:, :, 3].astype(np.uint16) * mask.astype(np.uint16) // 255
        ).astype(np.uint8)
        return QImage(
            pixels.data, width, height, pixels.strides[0],
            QImage.Format.Format_RGBA8888).copy()
    except Exception:
        return image
