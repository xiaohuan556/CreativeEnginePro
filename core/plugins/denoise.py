# -*- coding: utf-8 -*-
"""去噪（像素插件，可选）。

对 RGB 做非局部均值去噪；RGBA 输入保留 alpha。
"""
import cv2
import numpy as np

from core.plugins import register


@register
class Denoise:
    NAME = "denoise"
    LABEL = "AI 去噪"
    CATEGORY = "pixel"

    def run(self, image, ctx):
        p = ctx.get("denoise") or {}
        if not p.get("enabled"):
            return image
        strength = float(p.get("strength", 3))
        has_alpha = image.ndim == 3 and image.shape[2] == 4
        rgb = image[..., :3] if has_alpha else image
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        out = cv2.fastNlMeansDenoisingColored(bgr, None, strength, strength, 7, 21)
        out_rgb = cv2.cvtColor(out, cv2.COLOR_BGR2RGB)
        if has_alpha:
            return np.dstack([out_rgb, image[..., 3:]]).copy()
        return out_rgb
