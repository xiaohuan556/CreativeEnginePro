"""绿幕抠像（Chroma Key）工具。

对 QImage 或 numpy 数组做色度键抠像：基于像素与键色的颜色距离生成透明遮罩，
叠加边缘羽化与溢色抑制，返回带 alpha 通道的 RGBA8888 图像或 RGBA numpy 数组。
预览（preview_player）与导出（compositor）共用本模块，保证"导出=预览"。

性能优化（v2）：
- LUT 查表替代 float32 转换 + sqrt：整数运算，内存操作量降至 1/4
- 帧级结果缓存：同一 (source, frame, params) 复用结果，解决多轨道重复计算
- 预览路径使用 apply_chroma_key_numpy() / apply_chroma_key_cached() 在后台解码线程处理
"""
from __future__ import annotations

import numpy as np
from PyQt6.QtGui import QImage
from collections import OrderedDict
import logging

# ─── LUT 缓存：(key_color, similarity, smoothness) → _ChromaLUT ───
_lut_cache: OrderedDict = OrderedDict()
_LUT_CACHE_MAX = 16

# ─── 帧级结果缓存：(source_path, frame_idx, params) → RGBA uint8 array ───
_frame_cache: OrderedDict = OrderedDict()
_FRAME_CACHE_MAX = 24

_MAX_D_SQ = 3 * 255 * 255  # 195075 — RGB 三通道最大平方距离


class _ChromaLUT:
    """预计算的查表集合：逐通道平方差 LUT + alpha 映射 LUT。

    核心优化：用 3 个 256 项小表（L1 缓存友好）替代整帧 int16→int32 转换。
    每帧只需 3 次 fancy indexing（uint8→uint16）+ 2 次加法 + 1 次 alpha 查表。
    """
    __slots__ = ('sq_r', 'sq_g', 'sq_b', 'alpha')

    def __init__(self, key_color, similarity: float, smoothness: float):
        kr, kg, kb = int(key_color[0]), int(key_color[1]), int(key_color[2])
        r = np.arange(256)
        # (pixel - key)^2，结果恒非负，max 255^2=65025 fits uint16
        self.sq_r = ((r - kr) ** 2).astype(np.uint16)
        self.sq_g = ((r - kg) ** 2).astype(np.uint16)
        self.sq_b = ((r - kb) ** 2).astype(np.uint16)

        # alpha 映射：d_sq [0..195075] → alpha [0..255]
        lo = max(float(similarity), 1e-4) * 255.0
        sm = max(float(smoothness), 1e-4) * 255.0
        d = np.sqrt(np.arange(_MAX_D_SQ + 1, dtype=np.float32))
        alpha_f = (d - lo) / sm
        np.clip(alpha_f, 0.0, 1.0, out=alpha_f)
        self.alpha = (alpha_f * 255.0).astype(np.uint8)


def _get_chroma_lut(key_color, similarity: float, smoothness: float) -> _ChromaLUT:
    """获取或构建 _ChromaLUT（参数相同时复用）。"""
    lut_key = (int(key_color[0]), int(key_color[1]), int(key_color[2]),
               round(similarity, 4), round(smoothness, 4))
    lut = _lut_cache.get(lut_key)
    if lut is not None:
        _lut_cache.move_to_end(lut_key)
        return lut
    lut = _ChromaLUT(key_color, similarity, smoothness)
    _lut_cache[lut_key] = lut
    while len(_lut_cache) > _LUT_CACHE_MAX:
        _lut_cache.popitem(last=False)
    return lut


def _compute_alpha_mask(rgb_u8: np.ndarray, key_color, similarity, smoothness):
    """用逐通道平方查表计算 alpha 遮罩。

    3 次 256 项小表 fancy indexing（L1 缓存命中）+ 2 次加法 + 1 次 alpha 查表。
    无 float 转换、无 sqrt、无 int16/int32 大数组转换。
    返回 uint8 (H, W) alpha 遮罩。
    """
    lut = _get_chroma_lut(key_color, similarity, smoothness)
    # 逐通道平方差：uint8 索引 → uint16 值（fancy indexing，L1 友好）
    d_sq = lut.sq_r[rgb_u8[:, :, 0]].astype(np.uint32)
    d_sq += lut.sq_g[rgb_u8[:, :, 1]]
    d_sq += lut.sq_b[rgb_u8[:, :, 2]]
    # alpha 查表
    return lut.alpha[d_sq]


def _chroma_core(
    rgb: np.ndarray,
    old_alpha: np.ndarray,
    key_color: tuple,
    similarity: float,
    smoothness: float,
    spill: float,
):
    """公共核心（v2 优化版）：LUT 查表 + 整数运算。

    rgb: float32 归一化 [0,1] (H,W,3) — 仅为兼容旧调用路径保留
    返回 new_a: float32 (H,W) 归一化 [0,1]
    """
    # rgb 是 [0,1] float32，转回 uint8 用 LUT
    rgb_u8 = np.clip(rgb * 255.0, 0, 255).astype(np.uint8)
    alpha_u8 = _compute_alpha_mask(rgb_u8, key_color, similarity, smoothness)

    # new alpha = old_alpha * alpha_factor
    old_a = old_alpha.astype(np.float32) / 255.0
    new_a = old_a * (alpha_u8.astype(np.float32) / 255.0)

    # 溢色抑制（仍用 float，因为 rgb 已是 float32）
    sp = float(spill)
    if sp > 0:
        alpha_f = alpha_u8.astype(np.float32) / 255.0
        kidx = int(np.argmax(np.array(key_color[:3], dtype=np.float32)))
        others = [i for i in range(3) if i != kidx]
        min_other = np.min(rgb[:, :, others], axis=2)
        cur = rgb[:, :, kidx]
        rgb[:, :, kidx] = cur * (1.0 - sp * alpha_f) + min_other * (sp * alpha_f)

    return new_a


def apply_chroma_key_numpy(
    arr: np.ndarray,
    key_color=(0, 255, 0),
    similarity: float = 0.40,
    smoothness: float = 0.10,
    spill: float = 0.10,
) -> np.ndarray:
    """对 numpy RGB/BGR 数组做色度键抠像，返回 RGBA uint8 数组。

    输入：shape (H, W, 3) RGB uint8 或 (H, W, 4) RGBA uint8
    输出：shape (H, W, 4) RGBA uint8，绿色区域 alpha=0 透明

    v2 优化：LUT 查表 + 整数运算，无 float32 转换、无 sqrt。
    """
    if arr is None or arr.size == 0:
        return arr

    h, w = arr.shape[:2]
    ch = arr.shape[2] if len(arr.shape) == 3 else 1

    if ch == 4:
        rgb = arr[:, :, :3]
        old_alpha = arr[:, :, 3]
    else:
        rgb = arr[:, :, :3]
        old_alpha = None

    # LUT 查表计算 raw alpha（整数运算）
    raw_alpha = _compute_alpha_mask(rgb, key_color, similarity, smoothness)

    # 合成 alpha = old_alpha * raw_alpha / 255
    if old_alpha is not None:
        alpha = ((old_alpha.astype(np.uint16) * raw_alpha.astype(np.uint16)) // 255
                 ).astype(np.uint8)
    else:
        alpha = raw_alpha

    # 构建输出
    out = np.empty((h, w, 4), dtype=np.uint8)
    out[:, :, :3] = rgb

    # 溢色抑制（整数运算）
    sp = float(spill)
    if sp > 0:
        kidx = int(np.argmax(np.array(key_color[:3], dtype=np.float32)))
        others = [i for i in range(3) if i != kidx]
        min_other = np.min(rgb[:, :, others], axis=2)
        cur = rgb[:, :, kidx]
        sp_u8 = max(1, int(round(sp * 255)))
        # spill_amount = raw_alpha * sp / 255 (uint8)
        spill_amount = (raw_alpha.astype(np.uint16) * sp_u8 // 255).astype(np.uint8)
        inv_spill = 255 - spill_amount
        out[:, :, kidx] = (
            (cur.astype(np.uint16) * inv_spill.astype(np.uint16) +
             min_other.astype(np.uint16) * spill_amount.astype(np.uint16)) // 255
        ).astype(np.uint8)

    out[:, :, 3] = alpha
    return out


def apply_chroma_key_cached(
    source_path: str,
    frame_idx: int,
    arr: np.ndarray,
    key_color=(0, 255, 0),
    similarity: float = 0.40,
    smoothness: float = 0.10,
    spill: float = 0.10,
) -> np.ndarray:
    """带帧级缓存的色度键抠像。

    同一 (source_path, frame_idx, params) 的结果会被缓存复用，
    解决同一视频在多轨道叠加时重复计算绿幕的问题。

    返回的数组不应被调用方原地修改（缓存共享引用）。
    """
    params_key = (int(key_color[0]), int(key_color[1]), int(key_color[2]),
                  round(similarity, 4), round(smoothness, 4), round(spill, 4))
    cache_key = (source_path, int(frame_idx), params_key)

    cached = _frame_cache.get(cache_key)
    if cached is not None:
        _frame_cache.move_to_end(cache_key)
        return cached

    result = apply_chroma_key_numpy(arr, key_color, similarity, smoothness, spill)
    _frame_cache[cache_key] = result
    while len(_frame_cache) > _FRAME_CACHE_MAX:
        _frame_cache.popitem(last=False)
    return result


def clear_chroma_cache():
    """清空帧级缓存（时间线变化/参数变化时调用）。"""
    _frame_cache.clear()


def apply_chroma_key(
    img: QImage,
    key_color=(0, 255, 0),
    similarity: float = 0.40,
    smoothness: float = 0.10,
    spill: float = 0.10,
) -> QImage:
    """对 RGB(A) QImage 做色度键抠像（QImage 版本，用于导出路径 + 图片预览）。

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

    out_arr = apply_chroma_key_numpy(arr, key_color, similarity, smoothness, spill)

    out = QImage(
        out_arr.data, w, h, w * 4,
        QImage.Format.Format_RGBA8888,
    ).copy()
    return out
