# -*- coding: utf-8 -*-
"""固定区域蒙版生成（批量去水印用）。

无需 AI：对统一构图的素材（如 1024×1024 右下角水印）直接生成二值蒙版，
区域置 255 表示需要修复。对应规格里的：
    mask = np.zeros((H, W)); mask[-90:-10, -310:-10] = 255
这里参数化，支持右下角矩形与任意绝对矩形两种模式。

坐标约定：图像 shape = (H, W) 或 (H, W, C)；蒙版同 (H, W)，uint8，255=修复区。
"""
import numpy as np


def make_mask(shape, params=None):
    """按参数生成 (H, W) uint8 蒙版。

    params 支持：
      mode='bottom_right'（默认）: 右下角矩形
          width / height       水印区域宽高（默认 300×80，匹配 1024×1024 素材）
          margin_right / margin_bottom  距右边/底边的边距（默认 10）
      mode='rect': 绝对矩形
          x / y / width / height          左上角 + 宽高（像素）
    无任何区域时返回全零蒙版（调用方应跳过修复）。
    """
    h = shape[0]
    w = shape[1]
    mask = np.zeros((h, w), np.uint8)
    p = params or {}
    mode = p.get("mode", "bottom_right")

    if mode == "rect":
        x = max(0, int(p.get("x", 0)))
        y = max(0, int(p.get("y", 0)))
        rw = int(p.get("width", 100))
        rh = int(p.get("height", 100))
        x1 = min(w, x + rw)
        y1 = min(h, y + rh)
        if x1 > x and y1 > y:
            mask[y:y1, x:x1] = 255
        return mask

    # bottom_right
    mw = int(p.get("width", 300))
    mh = int(p.get("height", 80))
    mr = int(p.get("margin_right", 10))
    mb = int(p.get("margin_bottom", 10))
    x0 = max(0, w - mw - mr)
    x1 = max(x0, w - mr)
    y0 = max(0, h - mh - mb)
    y1 = max(y0, h - mb)
    if x1 > x0 and y1 > y0:
        mask[y0:y1, x0:x1] = 255
    return mask


def mask_has_region(mask):
    return mask is not None and int(np.count_nonzero(mask)) > 0
