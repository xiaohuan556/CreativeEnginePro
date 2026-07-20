"""
compositor.py — 视频合成器（逐帧渲染，导出与预览完全一致）

负责：
1. 多轨视频 PiP 叠加（pos_x/pos_y/scale/rotation + 关键帧插值）
2. 字幕完整样式渲染（背景填充、逐词动画、关键帧、自定义位置、字间距/行间距）
3. 逐帧渲染到 QImage，供 FFmpeg 编码
"""

from __future__ import annotations
import os
# 强制 ffmpeg 单线程解码：同一进程内多个 cv2.VideoCapture 实例争夺
# ffmpeg 内部 async_lock 会导致 "Assertion fctx->async_lock failed" 并 crash。
# 必须设在 cv2 import 之前。
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;1")
import logging
from typing import Optional, List, Tuple, Callable
from PyQt6.QtGui import (QImage, QColor, QPainter, QPen, QFont,
                          QFontMetrics, QBrush, QPainterPath,
                          QPixmap, QTransform)
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtWidgets import QGraphicsScene

_HAS_KF = True
try:
    from core.edit_engine import EditTimeline, VideoClip, AudioClip, SubtitleBlock, interpolate_keyframes
except ImportError:
    _HAS_KF = False


class VideoCompositor:
    """
    视频合成器 — 从 EditTimeline 逐帧渲染完整的合成画面

    使用方式:
        comp = VideoCompositor(timeline, (1920, 1080), 30.0)
        for sec in frame_timestamps:
            img = comp.render_frame(sec)
            # 将 img 写入 FFmpeg pipe 或保存为 PNG
        comp.close()
    """

    def __init__(self, timeline: EditTimeline, resolution: Tuple[int, int],
                 fps: float = 30.0):
        self.tl = timeline
        self.W, self.H = resolution
        self.fps = fps

        # 状态机解码器（替代原始 cv2.VideoCapture：连续 read 替代每帧 seek ~200ms）
        from core.clip_decoder import DecoderManager, ClipDecoder
        self._decoders = DecoderManager()
        self._decode_state = "playing"  # 导出是顺序播放，连续 read 无 seek
        # 叠加轨独立解码器池：id(clip) -> ClipDecoder（同文件多轨道分离 cap）
        self._overlay_decoders: dict = {}
        self._clip_src_cache: dict = {}

    def _clip_opacity(self, clip, sec: float, default: float = 1.0) -> float:
        """返回片段不透明度（含关键帧插值）。

        注意：opacity=0 是合法值（完全透明），绝不能用 `or 1.0` 写法——
        `0 or 1.0` 在 Python 中恒为 1.0，会导致「不透明度拉到 0 反而恢复原样」。
        """
        op = getattr(clip, 'opacity', default)
        if not isinstance(op, (int, float)):
            op = default
        kf = getattr(clip, 'keyframes', None) or {}
        op_kf = kf.get('opacity')
        if _HAS_KF and op_kf:
            try:
                rel_t = sec - clip.timeline_start
                vals = interpolate_keyframes(clip, {'opacity': op_kf}, rel_t, {'opacity': op})
                v = vals.get('opacity', op)
                if isinstance(v, (int, float)):
                    op = v
            except Exception:
                logging.debug("opacity 关键帧插值失败", exc_info=True)
        return op

    def total_duration(self) -> float:
        """计算时间线的总时长（所有轨道的最大结束时间）"""
        max_end = 0.0
        for track in self.tl.video_tracks:
            for c in track:
                end = c.timeline_start + (c.trim_end - c.trim_start) / max(c.speed, 0.01)
                max_end = max(max_end, end)
        for track in self.tl.audio_tracks:
            for c in track:
                end = c.timeline_start + (c.trim_end - c.trim_start)
                max_end = max(max_end, end)
        for track in self.tl.subtitle_tracks:
            for b in track:
                max_end = max(max_end, b.timeline_end)
        return max_end

    def render_frame(self, sec: float) -> QImage:
        """
        渲染时间线在 sec 时刻的完整合成画面

        Returns:
            QImage (Format_ARGB32), 尺寸 = (W, H)
        """
        canvas = QImage(self.W, self.H, QImage.Format.Format_ARGB32)
        canvas.fill(QColor('#000000'))

        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # ── 1. 收集活跃的视频片段（轨道顺序：0=背景, 1+=PiP）──
        active_clips: List[Tuple[int, VideoClip]] = []
        source_images: dict = {}  # id(clip) -> QImage（VideoClip 不可哈希，用 id 作键）

        for ti, track in enumerate(self.tl.video_tracks):
            info_list = getattr(self.tl, 'video_track_info', [])
            info = info_list[ti] if ti < len(info_list) else None
            if info and info.muted:
                continue
            for c in track:
                if not getattr(c, 'visible', True):
                    continue
                dur = (c.trim_end - c.trim_start) / max(c.speed, 0.01)
                if c.timeline_start <= sec < c.timeline_start + dur:
                    active_clips.append((ti, c))
                    # 提取帧
                    img = self._extract_frame(c, sec)
                    if img and not img.isNull():
                        source_images[id(c)] = img

        # ── 2. 检测转场（仅背景轨 ti==0：全屏不透明，避免覆盖下方轨道）──
        # id(B) -> (A, alpha, tfn, A_end)
        transition_incoming: dict = {}
        for ti, track in enumerate(self.tl.video_tracks):
            if ti != 0:
                continue
            for i, A in enumerate(track):
                ot = getattr(A, 'out_transition', None)
                if not (ot and ot.get('type')):
                    continue
                d = max(0.0, float(ot.get('duration', 0.5)))
                if d <= 0:
                    continue
                A_end = A.timeline_start + (A.trim_end - A.trim_start) / max(A.speed, 0.01)
                if not (A_end - 1e-6 <= sec <= A_end + d + 1e-6):
                    continue
                # 同轨下一片段 B
                B = None
                for j in range(i + 1, len(track)):
                    if track[j].timeline_start >= A_end - 1e-6:
                        B = track[j]
                        break
                if B is None:
                    continue
                if B.timeline_start > A_end + d + 1e-6:
                    continue  # 间隔过大无重叠，跳过
                alpha = (sec - A_end) / d
                alpha = max(0.0, min(1.0, alpha))
                transition_incoming[id(B)] = (A, alpha, ot['type'], A_end)

        # ── 3. 渲染视频：非绿幕先画，绿幕后画（保证透明区露出所有下层内容）──
        normal_clips = []
        green_clips = []
        for ti, clip in active_clips:
            if id(clip) in transition_incoming:
                A, alpha, tfn, A_end = transition_incoming[id(clip)]
                self._paint_transition(painter, A, clip, alpha, tfn, A_end, sec)
                continue
            img = source_images.get(id(clip))
            if img is None or img.isNull():
                continue
            if getattr(clip, 'chroma_key_enabled', False):
                green_clips.append((ti, clip, img))
            else:
                normal_clips.append((ti, clip, img))

        for ti, clip, img in normal_clips:
            self._paint_video_clip(painter, clip, img, sec, ti)
        for ti, clip, img in green_clips:
            self._paint_video_clip(painter, clip, img, sec, ti)

        # ── 3. 渲染字幕 ──
        self._paint_subtitles(painter, canvas, sec)

        painter.end()
        return canvas

    def _paint_video_clip(self, painter: QPainter, clip, img: QImage,
                          sec: float, track_idx: int):
        """渲染单个视频片段（含变换 + 关键帧）"""
        iw, ih = img.width(), img.height()
        if iw <= 0 or ih <= 0:
            return

        s = getattr(clip, 'scale', 1.0) or 1.0
        px = getattr(clip, 'pos_x', 0.0) or 0.0
        py = getattr(clip, 'pos_y', 0.0) or 0.0
        rot = getattr(clip, 'rotation', 0.0) or 0.0
        blur = getattr(clip, 'blur_radius', 0.0) or 0.0

        # 关键帧插值
        kf = getattr(clip, 'keyframes', None) or {}
        if _HAS_KF and kf:
            rel_t = sec - clip.timeline_start
            base = {"scale": s, "pos_x": px, "pos_y": py, "rotation": rot, "blur_radius": blur}
            try:
                vals = interpolate_keyframes(clip, kf, rel_t, base)
                s = vals["scale"]
                px = vals["pos_x"]
                py = vals["pos_y"]
                rot = vals["rotation"]
                blur = vals["blur_radius"]
            except Exception:
                logging.debug("关键帧插值失败", exc_info=True)

        # 适配画布
        fit_w = self.W / iw
        fit_h = self.H / ih
        base_scale = min(fit_w, fit_h)
        total_scale = base_scale * s

        new_w = max(1, int(iw * total_scale))
        new_h = max(1, int(ih * total_scale))

        # 缩放（QImage.scaled() 保留 alpha 通道，不转 QPixmap）
        scaled_img = img.scaled(
            new_w, new_h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        # 旋转（QImage，保留格式）
        if rot != 0.0:
            t = QTransform().translate(scaled_img.width() / 2, scaled_img.height() / 2)
            t.rotate(rot)
            t.translate(-scaled_img.width() / 2, -scaled_img.height() / 2)
            scaled_img = scaled_img.transformed(t, Qt.TransformationMode.SmoothTransformation)

        # 高斯模糊（QImage 路径，不转 QPixmap，保留 alpha）
        if blur > 0.5:
            try:
                import cv2, numpy as np
                w, h = scaled_img.width(), scaled_img.height()
                if w > 0 and h > 0:
                    ptr = scaled_img.bits()
                    ptr.setsize(h * scaled_img.bytesPerLine())
                    arr = np.frombuffer(ptr, np.uint8).reshape((h, w, 4)).copy()
                    bgr = cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2BGR)
                    ksize = int(blur * 2) | 1
                    if ksize < 3:
                        ksize = 3
                    blur_cv = cv2.GaussianBlur(bgr, (ksize, ksize), blur)
                    arr[:, :, :3] = cv2.cvtColor(blur_cv, cv2.COLOR_BGR2RGB)
                    blur_img = QImage(arr.data, w, h, scaled_img.bytesPerLine(),
                                          QImage.Format.Format_RGBA8888).copy()
                    scaled_img = blur_img
            except Exception:
                logging.debug("compositor OpenCV blur failed, skipping", exc_info=True)

        ox = (self.W - scaled_img.width()) // 2 + int(px)
        oy = (self.H - scaled_img.height()) // 2 + int(py)

        # 绿幕抠像（导出路径，与预览一致）
        if getattr(clip, 'chroma_key_enabled', False):
            try:
                from utils.chroma_key import apply_chroma_key
                scaled_img = apply_chroma_key(
                    scaled_img,
                    getattr(clip, 'chroma_key_color', (0, 255, 0)),
                    getattr(clip, 'chroma_key_similarity', 0.40),
                    getattr(clip, 'chroma_key_smoothness', 0.10),
                    getattr(clip, 'chroma_key_spill', 0.10),
                )
            except Exception:
                logging.debug("compositor chroma key failed", exc_info=True)

        painter.save()
        # 视频整体不透明度（含 alpha 视频：setOpacity 会乘到源 alpha 上）
        _op = self._clip_opacity(clip, sec)
        if _op < 1.0:
            painter.setOpacity(_op)
        # 直接用 drawImage，QImage 的 alpha 通道自动做 SourceOver 混合
        painter.drawImage(ox, oy, scaled_img)
        painter.restore()

    def _render_clip_offscreen(self, clip, sec: float, img: QImage):
        """把单个片段（已提取帧）合成到全尺寸黑底画布，返回 BGR numpy（供转场混合）"""
        import cv2, numpy as np
        off = QImage(self.W, self.H, QImage.Format.Format_ARGB32)
        off.fill(QColor(0, 0, 0, 255))  # 黑底（背景轨转场：外部无内容）
        p = QPainter(off)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self._paint_video_clip(p, clip, img, sec, 0)
        p.end()
        bits = off.bits()
        bits.setsize(self.H * off.bytesPerLine())
        arr = np.frombuffer(bits, np.uint8).reshape((self.H, self.W, 4)).copy()
        bgr = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
        return bgr

    def _paint_transition(self, painter: QPainter, A, B, alpha: float,
                          tfn: str, A_end: float, sec: float):
        """渲染转场：A 冻结末帧 + B 当前帧，按 alpha 混合（仅背景轨，全屏不透明）"""
        try:
            from core.slideshow_engine import apply_transition
            import cv2, numpy as np
        except Exception:
            b_img = self._extract_frame(B, sec)
            if b_img and not b_img.isNull():
                self._paint_video_clip(painter, B, b_img, sec, 0)
            return

        a_img = self._extract_frame(A, A_end - 0.001)
        b_img = self._extract_frame(B, sec)
        if a_img is None or b_img is None or a_img.isNull() or b_img.isNull():
            if b_img and not b_img.isNull():
                self._paint_video_clip(painter, B, b_img, sec, 0)
            return

        a_bgr = self._render_clip_offscreen(A, A_end - 0.001, a_img)
        b_bgr = self._render_clip_offscreen(B, sec, b_img)
        if a_bgr is None or b_bgr is None:
            if b_img and not b_img.isNull():
                self._paint_video_clip(painter, B, b_img, sec, 0)
            return

        try:
            blended = apply_transition(a_bgr, b_bgr, alpha, tfn, self.W, self.H)
        except Exception:
            logging.debug("转场 apply_transition 失败，回退画 B", exc_info=True)
            self._paint_video_clip(painter, B, b_img, sec, 0)
            return

        blended_rgba = cv2.cvtColor(blended, cv2.COLOR_BGR2RGBA)
        h, w = blended_rgba.shape[:2]
        qimg = QImage(blended_rgba.data, w, h, w * 4,
                      QImage.Format.Format_RGBA8888).copy()
        painter.drawImage(0, 0, qimg)

    def _extract_frame(self, clip, sec: float) -> Optional[QImage]:
        """从视频片段中提取 sec 时刻的帧（保留 alpha 通道）"""
        try:
            import cv2
            import numpy as np
        except ImportError:
            return None

        path = clip.source_path
        if not path or not os.path.exists(path):
            return None

        # 检查是否为图片
        ext = os.path.splitext(path)[1].lower()
        _IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
        if ext in _IMG_EXTS:
            return self._load_image_frame(path)

        # 视频：计算源时间
        src_sec = clip.trim_start + (sec - clip.timeline_start) * clip.speed

        # ── alpha 视频（如 MOV ProRes 4444）走 FFmpeg，OpenCV 会丢弃 alpha ──
        # ext 快速判断避免对 MP4 调用 probe_has_alpha（子进程阻塞）
        _ALPHA_EXTS = {".mov", ".webm"}
        if ext in _ALPHA_EXTS:
            try:
                from utils.alpha_video import probe_has_alpha, read_frame_with_alpha
                if probe_has_alpha(path):
                    bgra = read_frame_with_alpha(path, src_sec)
                    if bgra is not None:
                        frame_rgba = cv2.cvtColor(bgra, cv2.COLOR_BGRA2RGBA)
                        frame_rgba = np.ascontiguousarray(frame_rgba)
                        h, w, ch = frame_rgba.shape
                        return QImage(frame_rgba.data, w, h, ch * w,
                                      QImage.Format.Format_RGBA8888).copy()
                    # alpha 帧提取失败 → 不回退，避免花帧（FFmpeg 失败通常不是暂时的）
                    logging.debug("read_frame_with_alpha returned None for %s @ %.3f", path, src_sec)
                    return None
            except Exception:
                logging.debug("alpha path failed, fallback to cv2", exc_info=True)

        # ── 常规视频：使用状态机解码器（连续 read ~3ms，替代逐帧 seek ~200ms）──
        return self._extract_with_decoder(clip, sec)

    def _get_compositor_decoder(self, clip, is_overlay: bool = False):
        """获取或创建解码器。is_overlay=True 时为同文件多轨道分离 cap。"""
        if is_overlay:
            clid = id(clip)
            if clid in self._overlay_decoders:
                dec = self._overlay_decoders[clid]
                if dec.is_open():
                    return dec
                self._overlay_decoders.pop(clid, None)
            from core.clip_decoder import ClipDecoder
            dec = ClipDecoder(clip.source_path)
            if dec.open():
                dec.set_state("playing")
                self._overlay_decoders[clid] = dec
                return dec
            return None
        else:
            return self._decoders.get(clip)

    def _extract_with_decoder(self, clip, sec: float) -> Optional[QImage]:
        """使用状态机解码器提取帧（连续 read，无逐帧 seek）。"""
        import cv2
        src_sec = clip.trim_start + (sec - clip.timeline_start) * clip.speed
        dec = self._decoders.get(clip)
        if dec is None:
            return None
        res = dec.request(src_sec, "playing", ahead_frames=0)
        if res is None:
            return None
        frame_rgb, w, h = res
        # frame_rgb 可能是 RGB 或 RGBA
        if frame_rgb.shape[2] == 4:
            bytes_per_line = 4 * w
            return QImage(frame_rgb.data, w, h, bytes_per_line,
                          QImage.Format.Format_RGBA8888).copy()
        else:
            bytes_per_line = 3 * w
            return QImage(frame_rgb.data, w, h, bytes_per_line,
                          QImage.Format.Format_RGB888).copy()

    def _release_decoders(self):
        """释放所有解码器。"""
        for dec in self._overlay_decoders.values():
            try:
                dec.release()
            except Exception:
                pass
        self._overlay_decoders.clear()
        try:
            self._decoders.release()
        except Exception:
            pass

    def close(self):
        """释放所有资源"""
        self._release_decoders()
        for cap in list(self._cap_cache.values()):
            try:
                cap.release()
            except Exception:
                pass
        self._cap_cache.clear()
        self._cap_cache_order.clear()

    def _load_image_frame(self, path: str) -> Optional[QImage]:
        """加载图片帧（保留 alpha 通道）"""
        img = QImage(path)
        if img.isNull():
            return None
        if img.hasAlphaChannel():
            return img.convertToFormat(QImage.Format.Format_ARGB32)
        return img.convertToFormat(QImage.Format.Format_RGB888)

    # ────────────────────────────────────
    # 字幕渲染
    # ────────────────────────────────────

    def _paint_subtitles(self, painter: QPainter, canvas: QImage, sec: float):
        """渲染所有活跃字幕块"""
        for track in self.tl.subtitle_tracks:
            info_list = getattr(self.tl, 'subtitle_track_info', [])
            # 无法确定 track index，简化处理
            for b in track:
                if not getattr(b, 'visible', True):
                    continue
                if b.timeline_start <= sec < b.timeline_end:
                    self._paint_one_subtitle(painter, canvas, b, sec)

    def _paint_one_subtitle(self, painter: QPainter, canvas: QImage,
                            block, sec: float):
        """渲染单个字幕块（完整样式）"""
        text = getattr(block, 'text', '') or ''
        if not text.strip():
            return

        img_w, img_h = self.W, self.H

        # ── 位置 ──
        position = getattr(block, 'position', 'bottom') or 'bottom'
        margin_v = getattr(block, 'margin_v', 8) or 8
        pos_x = getattr(block, 'pos_x', None)
        pos_y = getattr(block, 'pos_y', None)

        if pos_x is None or pos_y is None:
            pos_map = {'top': -0.85, 'center': 0.0, 'bottom': 0.85}
            px = 0.0
            py = pos_map.get(position, 0.85)
        else:
            px = float(pos_x)
            py = float(pos_y)

        # ── 样式 ──
        fs = getattr(block, 'font_size', 15) or 15
        fc = getattr(block, 'color', '#ffffff') or '#ffffff'
        family = getattr(block, 'font_family', 'Microsoft YaHei') or 'Microsoft YaHei'
        bold = getattr(block, 'font_bold', False)
        italic = getattr(block, 'font_italic', False)
        underline = getattr(block, 'font_underline', False)
        letter_sp = getattr(block, 'letter_spacing', 0) or 0
        line_sp = getattr(block, 'line_spacing', 0) or 0
        ow = getattr(block, 'outline_width', 0) or 0
        oc = getattr(block, 'outline_color', '#000000') or '#000000'

        # ── 背景填充 ──
        has_fill = getattr(block, 'fill_enabled', False)
        bg_color = getattr(block, 'background_color', '#000000') or '#000000'
        border_radius = getattr(block, 'border_radius', 4) or 4

        # ── 逐词动画 ──
        word_anim = getattr(block, 'word_animation', False)
        word_anim_dur = getattr(block, 'word_anim_duration', 0.15) or 0.15
        from_asr = getattr(block, 'from_asr', False)
        word_timings = getattr(block, 'word_timings', []) or []

        # ── 关键帧插值 ──
        kf = getattr(block, 'keyframes', None) or {}
        rot = getattr(block, 'rotation', 0.0) or 0.0
        sc = getattr(block, 'scale', 1.0) or 1.0
        kf_applied = False
        if _HAS_KF and kf:
            try:
                rel_t = sec - block.timeline_start
                base = {"pos_x": px, "pos_y": py, "font_size": fs,
                        "rotation": rot, "scale": sc}
                vals = interpolate_keyframes(block, kf, rel_t, base)
                px = float(vals.get("pos_x", px))
                py = float(vals.get("pos_y", py))
                fs = int(vals.get("font_size", fs) * vals.get("scale", sc))
                rot = float(vals.get("rotation", rot))
                kf_applied = True
            except Exception:
                pass
        if not kf_applied:
            fs = max(6, int(fs * sc))

        # ── custom_width 实时换行 ──
        cw_custom = getattr(block, 'custom_width', 0) or 0
        if cw_custom > 0:
            flat = text.replace('\n', '')
            wrap_font = QFont(family, fs)
            wrap_font.setBold(bold)
            wrap_font.setItalic(italic)
            wrap_fm = QFontMetrics(wrap_font)
            wrap_w = max(1, cw_custom)
            text = '\n'.join(self._wrap_text_pixel(flat, wrap_fm, wrap_w))

        # 归一化坐标 → 像素
        cx = int((px + 1.0) / 2.0 * img_w)
        cy = int((py + 1.0) / 2.0 * img_h)

        # ── 渲染分支 ──
        # 字幕整体不透明度（0~1）；逐词动画自带淡入 alpha，二者相乘
        _op = self._clip_opacity(block, sec)
        painter.save()
        if _op < 1.0:
            painter.setOpacity(_op)
        if word_anim and from_asr and not has_fill and text.strip():
            self._draw_word_anim(painter, block, text, sec, cx, cy,
                                 fs, fc, family, bold, italic, underline,
                                 ow, oc, word_anim_dur, word_timings,
                                 letter_sp, line_sp)
        else:
            self._draw_normal(painter, text, cx, cy,
                              fs, fc, family, bold, italic, underline,
                              ow, oc, letter_sp, line_sp,
                              has_fill, bg_color, border_radius, rot)
        painter.restore()

    def _draw_normal(self, painter: QPainter, text: str,
                     cx: int, cy: int, fs: int, fc: str, family: str,
                     bold: bool, italic: bool, underline: bool,
                     ow: int, oc: str, letter_sp: int, line_sp: int,
                     has_fill: bool, bg_color: str, border_radius: int,
                     rot: float = 0.0):
        """正常字幕渲染（支持字间距、行间距、下划线、背景填充、旋转）"""
        font = QFont(family, fs)
        font.setBold(bold)
        font.setItalic(italic)
        font.setUnderline(underline)
        if letter_sp > 0:
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letter_sp)
        painter.setFont(font)
        fm = QFontMetrics(font)

        lines = text.split('\n')
        line_h = fm.height() + line_sp
        total_h = line_h * len(lines) - line_sp if line_sp else fm.height() * len(lines)

        # 计算每行宽度
        line_widths = []
        for line in lines:
            if letter_sp > 0:
                w = sum(fm.horizontalAdvance(ch) for ch in line) + letter_sp * (len(line) - 1)
            else:
                w = fm.horizontalAdvance(line)
            if not w:
                w = 1
            line_widths.append(w)

        max_w = max(line_widths) if line_widths else 0

        # 每个字的位置（用于字间距手绘）
        def _char_positions(line: str) -> List[Tuple[int, int, str]]:
            pos = []
            x = 0
            for ch in line:
                cw_ch = fm.horizontalAdvance(ch)
                pos.append((x, cw_ch, ch))
                x += cw_ch + letter_sp
            return pos

        painter.save()

        # 旋转（绕文字中心）
        if rot != 0.0:
            painter.translate(cx, cy)
            painter.rotate(rot)
            painter.translate(-cx, -cy)

        # 背景填充
        if has_fill:
            padding = max(4, fs // 3)
            bx = cx - max_w // 2 - padding
            by = cy - total_h // 2 - padding
            bw = max_w + padding * 2
            bh = total_h + padding * 2
            painter.setBrush(QColor(bg_color))
            painter.setPen(Qt.PenStyle.NoPen)
            if border_radius > 0:
                painter.drawRoundedRect(bx, by, bw, bh, border_radius, border_radius)
            else:
                painter.drawRect(bx, by, bw, bh)

        # 逐行绘制
        for line_idx, line in enumerate(lines):
            lw = line_widths[line_idx]
            lx = cx - lw // 2
            ly = cy - total_h // 2 + line_idx * line_h

            if letter_sp > 0:
                # 逐字符绘制（精确控制间距）
                for ch_x, ch_w, ch in _char_positions(line):
                    chx = lx + ch_x
                    # 描边
                    if ow > 0:
                        _draw_text_with_outline(painter, ch, chx, ly, ow, QColor(oc))
                    painter.setPen(QColor(fc))
                    painter.drawText(chx, ly, ch)
            else:
                # 整行绘制
                if ow > 0:
                    _draw_text_with_outline(painter, line, lx, ly, ow, QColor(oc))
                painter.setPen(QColor(fc))
                painter.drawText(lx, ly + fm.ascent(), line)

        painter.restore()

    def _draw_word_anim(self, painter: QPainter, block, text: str,
                        sec: float, cx: int, cy: int,
                        fs: int, fc: str, family: str,
                        bold: bool, italic: bool, underline: bool,
                        ow: int, oc: str, word_anim_dur: float,
                        word_timings: list, letter_sp: int, line_sp: int):
        """逐词动画渲染（每个词依次淡入展示）"""
        if not word_timings:
            # 无时间戳时，按空格简单分词
            words = text.split()
            if not words:
                return
            block_dur = block.timeline_end - block.timeline_start
            word_dur = block_dur / len(words)
            rel_t = sec - block.timeline_start
            idx = min(int(rel_t / word_dur), len(words) - 1)
            current_word = words[idx]
            elapsed = rel_t - idx * word_dur
            alpha = min(1.0, elapsed / max(word_anim_dur, 0.01))
        else:
            # 有 ASR 时间戳
            current_word = ""
            idx = -1
            rel_t = sec - block.timeline_start
            for wi, wt in enumerate(word_timings):
                ws = wt.get("start", 0)
                we = wt.get("end", 1)
                if ws <= rel_t < we:
                    current_word = wt.get("text", "")
                    idx = wi
                    elapsed = rel_t - ws
                    alpha = min(1.0, elapsed / max(word_anim_dur, 0.01))
                    break
            if not current_word:
                return

        font = QFont(family, fs)
        font.setBold(bold)
        font.setItalic(italic)
        font.setUnderline(underline)
        if letter_sp > 0:
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letter_sp)
        painter.setFont(font)
        fm = QFontMetrics(font)
        w = fm.horizontalAdvance(current_word)
        if not w:
            w = 1

        painter.save()
        # 缩放动画（1.0 → 1.3 → 1.0）
        anim_scale = 1.0 + 0.3 * (1.0 - abs(alpha - 0.5) * 2)
        tx = cx
        ty = cy
        painter.translate(tx, ty)
        painter.scale(anim_scale, anim_scale)
        painter.translate(-tx, -ty)

        # 透明度（逐词淡入 alpha × 字幕整体不透明度）
        _op = self._clip_opacity(block, sec)
        _final_alpha = alpha * _op
        if _final_alpha < 1.0:
            painter.setOpacity(_final_alpha)

        lx = cx - int(w * anim_scale) // 2
        ly = cy - fm.height() // 2

        if ow > 0:
            _draw_text_with_outline(painter, current_word, lx, ly, ow, QColor(oc))
        painter.setPen(QColor(fc))
        painter.drawText(lx, ly + fm.ascent(), current_word)

        painter.restore()

    @staticmethod
    def _wrap_text_pixel(text: str, fm: QFontMetrics, max_w: int) -> List[str]:
        """像素级换行（兼容 CJK / 拉丁 / 阿拉伯等混合语言）"""
        import unicodedata
        lines = []
        current = ""
        for ch in text:
            if ch == '\n':
                lines.append(current)
                current = ""
                continue
            test = current + ch
            w = fm.horizontalAdvance(test)
            if w > max_w and current:
                # CJK 字符或空格处可以断行
                if '\u4e00' <= ch <= '\u9fff' or '\u3040' <= ch <= '\u30ff':
                    lines.append(current)
                    current = ch
                else:
                    # 拉丁文字：在词边界断行
                    last_space = current.rfind(' ')
                    if last_space > 0:
                        lines.append(current[:last_space])
                        current = current[last_space + 1:] + ch
                    else:
                        lines.append(current)
                        current = ch
            else:
                current = test
        if current:
            lines.append(current)
        return lines or [text]

    def close(self):
        """释放所有 OpenCV 资源"""
        import cv2
        for cap in self._cap_cache.values():
            try:
                cap.release()
            except Exception:
                pass
        self._cap_cache.clear()
        self._cap_cache_order.clear()
        self._clip_src_cache.clear()


def _draw_text_with_outline(painter: QPainter, text: str, x: int, y: int,
                            ow: int, color: QColor):
    """使用 QPainterPath 高效绘制文字描边（O(1) 替代逐像素 for 循环）"""
    font = painter.font()
    path = QPainterPath()
    path.addText(x, y + QFontMetrics(font).ascent(), font, text)
    pen = QPen(color, ow * 2 + 1)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
