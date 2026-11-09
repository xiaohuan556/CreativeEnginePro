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
                          QFontMetrics, QBrush, QPainterPath)
from PyQt6.QtCore import Qt, QRectF
from core.preview_effects import (
    evaluate_video_effects,
    evaluate_subtitle_effects,
    subtitle_words,
    subtitle_word_animation_state,
)
from core.video_render import apply_chroma, paint_video_image, transform_video_image

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
                 fps: float = 30.0,
                 subtitle_reference_resolution: Tuple[int, int] | None = None):
        self.tl = timeline
        self.W, self.H = resolution
        self.fps = fps
        ref_w, ref_h = subtitle_reference_resolution or resolution
        self._subtitle_scale = min(
            self.W / max(float(ref_w), 1.0),
            self.H / max(float(ref_h), 1.0),
        )

        # 状态机解码器（替代原始 cv2.VideoCapture：连续 read 替代每帧 seek ~200ms）
        from core.clip_decoder import DecoderManager, ClipDecoder
        self._decoders = DecoderManager()
        self._decode_state = "playing"  # 导出是顺序播放，连续 read 无 seek
        # 叠加轨独立解码器池：id(clip) -> ClipDecoder（同文件多轨道分离 cap）
        self._overlay_decoders: dict = {}
        self._clip_src_cache: dict = {}
        self._image_frames: dict[str, QImage] = {}
        self._image_transforms: dict = {}
        self._image_chroma: dict = {}
        self._transition_freeze: dict = {}
        # OpenCV 在连续处理多条 HEVC 时间线时可能因解码器资源不足而返回空帧。
        # 导出不能把空帧静默替换成黑色，因此为每个片段准备独立 FFmpeg 连续管道兜底。
        self._ffmpeg_fallback_readers: dict = {}

    def _clip_opacity(self, clip, sec: float, default: float = 1.0) -> float:
        """返回片段不透明度（含关键帧插值）。

        注意：opacity=0 是合法值（完全透明），绝不能用 `or 1.0` 写法——
        `0 or 1.0` 在 Python 中恒为 1.0，会导致「不透明度拉到 0 反而恢复原样」。
        """
        try:
            return evaluate_video_effects(clip, sec)["opacity"]
        except Exception:
            op = getattr(clip, 'opacity', default)
            return max(0.0, min(1.0, float(op))) if isinstance(op, (int, float)) else default

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
        canvas.fill(QColor(getattr(self.tl, 'canvas_bg_color', '#000000') or '#000000'))

        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

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
                self._paint_transition(
                    painter, A, clip, alpha, tfn, A_end, sec,
                    source_images.get(id(clip)))
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
        """Render one source frame with the same pixel effects as preview."""
        if img is None or img.isNull():
            return
        values = evaluate_video_effects(clip, sec)
        is_image = os.path.splitext(clip.source_path)[1].lower() in {
            ".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
        source = apply_chroma(
            img, clip, cache=self._image_chroma if is_image else None,
            cache_limit=4)
        transformed = transform_video_image(
            source, clip, self.W, self.H, values,
            cache=self._image_transforms if is_image else None,
            cache_limit=4)
        x = (self.W - transformed.width()) // 2 + int(values["pos_x"])
        y = (self.H - transformed.height()) // 2 + int(values["pos_y"])
        paint_video_image(
            painter, clip, transformed, x, y, sec, values["opacity"])

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
                          tfn: str, A_end: float, sec: float,
                          b_img: Optional[QImage] = None):
        """渲染转场：A 冻结末帧 + B 当前帧，按 alpha 混合（仅背景轨，全屏不透明）"""
        try:
            from core.slideshow_engine import apply_transition
            import cv2, numpy as np
        except Exception:
            b_img = b_img if b_img is not None else self._extract_frame(B, sec)
            if b_img and not b_img.isNull():
                self._paint_video_clip(painter, B, b_img, sec, 0)
            return

        b_img = b_img if b_img is not None else self._extract_frame(B, sec)
        if b_img is None or b_img.isNull():
            return
        freeze_key = (id(A), round(A_end, 4), self.W, self.H)
        a_bgr = self._transition_freeze.get(freeze_key)
        if a_bgr is None:
            a_img = self._extract_frame(A, A_end - 0.001)
            if a_img is not None and not a_img.isNull():
                a_bgr = self._render_clip_offscreen(A, A_end - 0.001, a_img)
                if a_bgr is not None:
                    self._transition_freeze.clear()
                    self._transition_freeze[freeze_key] = a_bgr
        if a_bgr is None:
            if b_img and not b_img.isNull():
                self._paint_video_clip(painter, B, b_img, sec, 0)
            return

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

    def render_background_transition(self, previous, following,
                                     sec: float) -> QImage:
        """Render only a transition window for the hybrid FFmpeg export."""
        transition = getattr(previous, "out_transition", None) or {}
        duration = max(0.001, float(transition.get("duration", 0.5)))
        end = (previous.timeline_start
               + (previous.trim_end - previous.trim_start)
               / max(previous.speed, 0.01))
        alpha = max(0.0, min(1.0, (sec - end) / duration))
        image = QImage(self.W, self.H, QImage.Format.Format_ARGB32)
        image.fill(QColor(getattr(self.tl, "canvas_bg_color", "#000000")
                          or "#000000"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self._paint_transition(
            painter, previous, following, alpha,
            transition.get("type", "zoom_push"), end, sec)
        painter.end()
        return image

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
        src_sec = clip.trim_start + (sec - clip.timeline_start) * clip.speed
        dec = self._decoders.get(clip)
        if dec is not None:
            res = dec.request(src_sec, "playing", ahead_frames=0)
            if res is not None:
                frame_rgb, w, h = res
                # frame_rgb 可能是 RGB 或 RGBA
                if frame_rgb.shape[2] == 4:
                    bytes_per_line = 4 * w
                    return QImage(frame_rgb.data, w, h, bytes_per_line,
                                  QImage.Format.Format_RGBA8888).copy()
                bytes_per_line = 3 * w
                return QImage(frame_rgb.data, w, h, bytes_per_line,
                              QImage.Format.Format_RGB888).copy()

        # OpenCV 无法打开/读取（常见于连续多条 HEVC 时间线）时，改走 FFmpeg
        # 持久 rawvideo 管道。每个 clip 独立 reader，重复素材的不同时间位置不会互相 seek。
        try:
            import cv2
            from utils.alpha_video import AlphaVideoPipeReader
            clip_id = id(clip)
            reader = self._ffmpeg_fallback_readers.get(clip_id)
            if reader is None:
                reader = AlphaVideoPipeReader(clip.source_path)
                if reader._w <= 0 or reader._h <= 0:
                    reader.close()
                    return None
                self._ffmpeg_fallback_readers[clip_id] = reader
            bgra = reader.read_frame(src_sec)
            if bgra is None:
                return None
            rgba = cv2.cvtColor(bgra, cv2.COLOR_BGRA2RGBA)
            h, w = rgba.shape[:2]
            return QImage(rgba.data, w, h, w * 4,
                          QImage.Format.Format_RGBA8888).copy()
        except Exception:
            logging.warning("FFmpeg fallback decoder failed: %s @ %.3f",
                            clip.source_path, src_sec, exc_info=True)
            return None

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
        self._image_frames.clear()
        self._image_transforms.clear()
        self._image_chroma.clear()
        self._transition_freeze.clear()
        self._release_decoders()
        for reader in list(self._ffmpeg_fallback_readers.values()):
            try:
                reader.close()
            except Exception:
                pass
        self._ffmpeg_fallback_readers.clear()

    def _load_image_frame(self, path: str) -> Optional[QImage]:
        """加载图片帧（保留 alpha 通道）"""
        cached = self._image_frames.get(path)
        if cached is not None:
            return cached
        img = QImage(path)
        if img.isNull():
            return None
        if img.hasAlphaChannel():
            img = img.convertToFormat(QImage.Format.Format_ARGB32)
        else:
            img = img.convertToFormat(QImage.Format.Format_RGB888)
        self._image_frames[path] = img
        while len(self._image_frames) > 4:
            self._image_frames.pop(next(iter(self._image_frames)))
        return img

    # ────────────────────────────────────
    # 字幕渲染
    # ────────────────────────────────────

    def _paint_subtitles(self, painter: QPainter, canvas: QImage, sec: float):
        """渲染所有活跃字幕块"""
        info_list = getattr(self.tl, 'subtitle_track_info', [])
        for track_idx, track in enumerate(self.tl.subtitle_tracks):
            info = info_list[track_idx] if track_idx < len(info_list) else None
            if info and getattr(info, 'muted', False):
                continue
            for b in track:
                if not getattr(b, 'visible', True):
                    continue
                if b.timeline_start <= sec < b.timeline_end:
                    self._paint_one_subtitle(painter, canvas, b, sec)

    def paint_subtitles_on_image(self, image: QImage, blocks: list,
                                 sec: float, interactive_block=None) -> QImage:
        """Paint preview subtitles using the exact export drawing path."""
        if image is None or image.isNull() or not blocks:
            return image
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        for block in blocks:
            self._paint_one_subtitle(
                painter, image, block, sec,
                interactive=(block is interactive_block))
        painter.end()
        return image

    def _paint_one_subtitle(self, painter: QPainter, canvas: QImage,
                            block, sec: float, interactive: bool = False):
        """渲染单个字幕块（完整样式）"""
        text = getattr(block, 'text', '') or ''
        if not text.strip():
            return

        img_w, img_h = self.W, self.H

        # ── 位置/关键帧/整体透明度：与实时预览共用评估器 ──
        effect_values = evaluate_subtitle_effects(
            block, sec, interactive=interactive)
        px = effect_values["pos_x"]
        py = effect_values["pos_y"]

        # ── 样式 ──
        fs = effect_values["font_size"] * self._subtitle_scale
        fc = getattr(block, 'color', '#ffffff') or '#ffffff'
        family = getattr(block, 'font_family', 'Microsoft YaHei') or 'Microsoft YaHei'
        bold = getattr(block, 'font_bold', False)
        italic = getattr(block, 'font_italic', False)
        underline = getattr(block, 'font_underline', False)
        letter_sp = int(round((getattr(block, 'letter_spacing', 0) or 0) * self._subtitle_scale))
        line_sp = int(round((getattr(block, 'line_spacing', 0) or 0) * self._subtitle_scale))
        ow = int(round((getattr(block, 'outline_width', 0) or 0) * self._subtitle_scale))
        oc = getattr(block, 'outline_color', '#000000') or '#000000'

        # ── 背景填充 ──
        has_fill = getattr(block, 'fill_enabled', False)
        bg_color = getattr(block, 'background_color', '#000000') or '#000000'
        border_radius = int(round((getattr(block, 'border_radius', 0) or 0) * self._subtitle_scale))

        # ── 逐词动画 ──
        word_anim = getattr(block, 'word_animation', False)
        word_anim_dur = getattr(block, 'word_anim_duration', 0.15) or 0.15
        from_asr = getattr(block, 'from_asr', False)
        word_timings = getattr(block, 'word_timings', []) or []

        # ── 关键帧插值 ──
        rot = effect_values["rotation"]
        fs = max(6, int(fs * effect_values["scale"]))

        # ── custom_width 实时换行 ──
        cw_custom = int(round((getattr(block, 'custom_width', 0) or 0) * self._subtitle_scale))
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
        _op = effect_values["opacity"]
        painter.save()
        if _op < 1.0:
            painter.setOpacity(_op)
        if word_anim and not has_fill and text.strip():
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
            w = fm.horizontalAdvance(line)
            if letter_sp > 0 and len(line) > 1:
                w += letter_sp * (len(line) - 1)
            line_widths.append(max(1, w))

        max_w = max(line_widths) if line_widths else 0

        painter.save()

        # 旋转（绕文字中心）
        if rot != 0.0:
            painter.translate(cx, cy)
            painter.rotate(rot)
            painter.translate(-cx, -cy)

        # 背景填充
        if has_fill:
            pad_h = max(4, int(round(8 * self._subtitle_scale)))
            pad_v = max(3, int(round(6 * self._subtitle_scale)))
            bx = cx - max_w // 2 - pad_h
            by = cy - total_h // 2 - pad_v
            bw = max_w + pad_h * 2
            bh = total_h + pad_v * 2
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

            # 与实时预览一致：由 QFont 的字间距负责排版，整行绘制，
            # 避免导出路径逐字绘制造成字形和基线差异。
            if ow > 0:
                _draw_text_with_outline(
                    painter, line, lx, ly, ow, QColor(oc), QColor(fc))
            else:
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
        words = subtitle_words(text)
        if not words:
            return
        current_word, alpha, anim_scale = subtitle_word_animation_state(
            block, sec, words)
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
        # 预览和导出共享同一套弹入缩放曲线。
        tx = cx
        ty = cy
        painter.translate(tx, ty)
        painter.scale(anim_scale, anim_scale)
        painter.translate(-tx, -ty)

        # 与预览一致：逐词淡入写进文字颜色，整体透明度留给外层 painter。
        fill_color = QColor(fc)
        fill_color.setAlpha(int(255 * alpha))
        outline_color = QColor(oc)
        outline_color.setAlpha(int(255 * alpha))

        lx = cx - w // 2
        ly = cy - fm.height() // 2

        if ow > 0:
            _draw_text_with_outline(
                painter, current_word, lx, ly, ow, outline_color, fill_color)
        else:
            painter.setPen(fill_color)
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
        for cap in getattr(self, "_cap_cache", {}).values():
            try:
                cap.release()
            except Exception:
                pass
        getattr(self, "_cap_cache", {}).clear()
        getattr(self, "_cap_cache_order", []).clear()
        getattr(self, "_clip_src_cache", {}).clear()


def _draw_text_with_outline(painter: QPainter, text: str, x: int, y: int,
                            ow: int, color: QColor, fill: QColor):
    """与预览一致：先描边，再用字形路径覆盖侵入文字内部的描边。"""
    font = painter.font()
    path = QPainterPath()
    path.addText(x, y + QFontMetrics(font).ascent(), font, text)
    pen = QPen(color, ow * 2)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(fill)
    painter.drawPath(path)
