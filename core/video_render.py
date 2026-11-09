"""Shared frame effects used by timeline preview and export compositor.

Decoding and editor selection controls stay with their callers.  Pixel
transforms, chroma key, mask, and opacity are evaluated here in one order.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPainter, QTransform


def blur_image(image: QImage, radius: float) -> QImage:
    if radius <= 0.5 or image is None or image.isNull():
        return image
    try:
        import cv2
        import numpy as np

        source = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = source.width(), source.height()
        pixels = np.frombuffer(
            source.constBits().asstring(width * height * 4), np.uint8
        ).reshape((height, width, 4)).copy()
        bgr = cv2.cvtColor(pixels[:, :, :3], cv2.COLOR_RGB2BGR)
        kernel = max(3, int(round(radius * 2)) | 1)
        blurred = cv2.GaussianBlur(bgr, (kernel, kernel), radius)
        pixels[:, :, :3] = cv2.cvtColor(blurred, cv2.COLOR_BGR2RGB)
        return QImage(pixels.data, width, height, width * 4,
                      QImage.Format.Format_RGBA8888).copy()
    except Exception:
        logging.debug("shared video blur failed", exc_info=True)
        return image


def apply_chroma(image: QImage, clip, *, cache: dict | None = None,
                 cache_limit: int = 16) -> QImage:
    if not getattr(clip, "chroma_key_enabled", False):
        return image
    params = (
        tuple(getattr(clip, "chroma_key_color", (0, 255, 0))),
        getattr(clip, "chroma_key_similarity", 0.40),
        getattr(clip, "chroma_key_smoothness", 0.10),
        getattr(clip, "chroma_key_spill", 0.10),
    )
    key = (int(image.cacheKey()), id(clip), params)
    if cache is not None and key in cache:
        return cache[key]
    try:
        from utils.chroma_key import apply_chroma_key
        result = apply_chroma_key(image, *params)
        if cache is not None:
            cache[key] = result
            while len(cache) > cache_limit:
                cache.pop(next(iter(cache)))
        return result
    except Exception:
        logging.debug("shared video chroma key failed", exc_info=True)
        return image


def transform_video_image(image: QImage, clip, width: int, height: int,
                          values: dict, *, source_size=None,
                          cache: dict | None = None,
                          cache_limit: int = 32) -> QImage:
    if image is None or image.isNull():
        return image
    source_w, source_h = source_size or (image.width(), image.height())
    base = min(width / max(1, source_w), height / max(1, source_h))
    target_w = max(1, int(source_w * base * values["scale"]))
    target_h = max(1, int(source_h * base * values["scale"]))
    key = (
        int(image.cacheKey()), id(clip), width, height, target_w, target_h,
        round(values["rotation"], 4), round(values["blur_radius"], 4),
    )
    if cache is not None:
        cached = cache.get(key)
        if cached is not None and not cached.isNull():
            return cached

    transformed = image.scaled(
        target_w, target_h, Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation)
    if abs(values["rotation"]) > 0.001:
        rotation = QTransform().translate(
            transformed.width() / 2, transformed.height() / 2)
        rotation.rotate(values["rotation"])
        rotation.translate(-transformed.width() / 2, -transformed.height() / 2)
        transformed = transformed.transformed(
            rotation, Qt.TransformationMode.SmoothTransformation)
    transformed = blur_image(transformed, values["blur_radius"])

    if cache is not None:
        cache[key] = transformed
        while len(cache) > cache_limit:
            cache.pop(next(iter(cache)))
    return transformed


def paint_video_image(painter: QPainter, clip, image: QImage,
                      x: int, y: int, sec: float, opacity: float = 1.0):
    if image is None or image.isNull():
        return
    if getattr(clip, "mask_enabled", False):
        try:
            from utils.video_mask import apply_video_mask
            image = apply_video_mask(
                image, clip, sec - clip.timeline_start)
        except Exception:
            logging.debug("shared video mask failed", exc_info=True)
    painter.save()
    if opacity < 1.0:
        painter.setOpacity(max(0.0, opacity))
    painter.drawImage(x, y, image)
    painter.restore()
