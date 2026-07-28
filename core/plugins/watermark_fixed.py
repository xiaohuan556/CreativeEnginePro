# -*- coding: utf-8 -*-
"""固定蒙版去水印（像素插件）。

无需模型 / GPU：生成固定区域二值蒙版，用 OpenCV 内容识别修复（inpaint）填补。
覆盖统一构图素材（如 1024×1024 右下角水印）的场景，速度远快于扩散模型。

支持 RGB / RGBA（alpha 通道在修复后保留）。
"""
import numpy as np
import cv2

from utils.mask import make_mask, mask_has_region
from core.plugins import register


@register
class WatermarkFixed:
    NAME = "watermark_fixed"
    LABEL = "AI 去水印（固定蒙版）"
    CATEGORY = "pixel"

    def run(self, image, ctx):
        params = (ctx.get("watermark") or {})
        mask = make_mask(image.shape, params)
        if not mask_has_region(mask):
            return image

        has_alpha = image.ndim == 3 and image.shape[2] == 4
        if has_alpha:
            rgb = image[..., :3]
            alpha = image[..., 3:]
        else:
            rgb = image

        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        # radius=3 足够；TELEA 在文字水印上效果自然
        inp = cv2.inpaint(bgr, mask, 3, cv2.INPAINT_TELEA)
        inp_rgb = cv2.cvtColor(inp, cv2.COLOR_BGR2RGB)

        if has_alpha:
            return np.dstack([inp_rgb, alpha]).copy()
        return inp_rgb
