# -*- coding: utf-8 -*-
"""改尺寸 / 改比例（像素插件）。

mode:
  scale  按比例缩放（factor）
  size   指定宽高
  preset 改为标准比例并居中裁剪（1:1 / 9:16 / 16:9），输出标准尺寸。
"""
import cv2
import numpy as np

from core.plugins import register

_PRESETS = {
    "1:1": (1024, 1024),
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
}


@register
class Resize:
    NAME = "resize"
    LABEL = "改尺寸 / 改比例"
    CATEGORY = "pixel"

    def run(self, image, ctx):
        p = ctx.get("resize") or {}
        if not p.get("enabled"):
            return image
        h, w = image.shape[:2]
        mode = p.get("mode", "scale")

        if mode == "scale":
            f = float(p.get("scale", 1))
            if abs(f - 1.0) < 1e-6:
                return image
            nw, nh = max(1, int(round(w * f))), max(1, int(round(h * f)))
        elif mode == "size":
            nw, nh = max(1, int(p.get("width", w))), max(1, int(p.get("height", h)))
        elif mode == "preset":
            tw, th = _PRESETS.get(p.get("preset", "1:1"), (1024, 1024))
            tr, cur = tw / th, w / h
            if cur > tr:
                cw, ch = int(h * tr), h
            else:
                cw, ch = w, int(w / tr)
            x0, y0 = (w - cw) // 2, (h - ch) // 2
            image = image[y0:y0 + ch, x0:x0 + cw]
            nw, nh = tw, th
        else:
            return image

        interp = cv2.INTER_LANCZOS4 if (nh >= h and nw >= w) else cv2.INTER_AREA
        return cv2.resize(image, (nw, nh), interpolation=interp)
