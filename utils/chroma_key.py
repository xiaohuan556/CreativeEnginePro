"""绿幕抠像（Chroma Key）工具。

对 QImage 做色度键抠像：基于像素与键色的颜色距离生成透明遮罩，
叠加边缘羽化与溢色抑制，返回带 alpha 通道的 RGBA8888 图像。
预览（preview_player）与导出（compositor）共用本模块，保证"导出=预览"。
"""
from __future__ import annotations

import numpy as np
from PyQt6.QtGui import QImage


def apply_chroma_key(
    img: QImage,
    key_color=(0, 255, 0),
    similarity: float = 0.40,
    smoothness: float = 0.10,
    spill: float = 0.10,
) -> QImage:
    """对 RGB(A) 图像做色度键抠像。

    key_color : (R, G, B) 0~255，要抠掉的键色（默认纯绿）
    similarity: 0~1，颜色接近键色的判定阈值（越大抠得越多）
    smoothness: 0~1，边缘羽化过渡宽度（越大边缘越柔）
    spill     : 0~1，溢色抑制强度（去除边缘残留的键色）
    """
    if img is None or img.isNull():
        return img
    src = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = src.width(), src.height()
    if w <= 0 or h <= 0:
        return src
    ptr = src.bits()
    ptr.setsize(h * src.bytesPerLine())
    arr = np.frombuffer(ptr, np.uint8).reshape((h, w, 4)).copy()

    rgb = arr[:, :, :3].astype(np.float32) / 255.0
    k = np.array(key_color, dtype=np.float32)[:3] / 255.0

    # 归一化 RGB 欧氏距离（最大 sqrt(3) ≈ 1.732）
    diff = rgb - k
    d = np.sqrt(np.sum(diff * diff, axis=2))

    lo = max(float(similarity), 1e-4)
    sm = max(float(smoothness), 1e-4)
    # 透明因子：d<=lo → 0(全透明)；d>=lo+sm → 1(不透明)，中间线性过渡
    alpha_f = (d - lo) / sm
    np.clip(alpha_f, 0.0, 1.0, out=alpha_f)

    # 与已有 alpha 相乘（alpha 视频保留其透明度，普通视频原 alpha=1）
    old_a = arr[:, :, 3].astype(np.float32) / 255.0
    new_a = old_a * alpha_f
    arr[:, :, 3] = np.clip(new_a * 255.0, 0, 255).astype(np.uint8)

    # 溢色抑制：对保留像素，将键色主通道向另两通道最小值靠拢，去掉绿边
    sp = float(spill)
    if sp > 0:
        kidx = int(np.argmax(k))
        others = [i for i in range(3) if i != kidx]
        min_other = np.min(rgb[:, :, others], axis=2)
        cur = rgb[:, :, kidx]
        rgb[:, :, kidx] = cur * (1.0 - sp * alpha_f) + min_other * (sp * alpha_f)
        arr[:, :, :3] = np.clip(rgb * 255.0, 0, 255).astype(np.uint8)

    out = QImage(
        arr.data, w, h, src.bytesPerLine(),
        QImage.Format.Format_RGBA8888,
    ).copy()
    return out
