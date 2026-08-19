"""
timeline_widget.py — 多轨时间线控件
布局（从上往下）：字幕轨 → 视频轨×N → 音频轨×N
支持：多条视频/音频轨、磁吸、Trim、分割、右键AI工具
"""
from __future__ import annotations
import os
import math
from typing import Optional, Tuple, List
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
                              QSizePolicy, QMenu, QLabel, QSlider, QPushButton,
                              QApplication)
from PyQt6.QtCore import (Qt, QRect, QPoint, QPointF, QTimer, pyqtSignal, QSize, QUrl)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtGui import (QPainter, QColor, QFont, QPen, QBrush, QPixmap,
                          QFontMetrics, QCursor, QKeyEvent, QMouseEvent,
                          QWheelEvent, QPainterPath, QLinearGradient)

from core.edit_engine import EditTimeline, VideoClip, AudioClip, SubtitleBlock

# ─── 颜色常量 ───
C_BG          = QColor("#1a1a1a")
C_TRACK_BG_A  = QColor("#212121")
C_TRACK_BG_B  = QColor("#1e1e1e")
C_RULER_BG    = QColor("#161616")
C_RULER_TEXT  = QColor("#666666")
C_RULER_TEXT2 = QColor("#888888")
C_HEAD        = QColor("#00eaff")
C_VIDEO_CLIP  = QColor("#1a3a6e")
C_VIDEO_HOVER = QColor("#244a8e")
C_AUDIO_CLIP  = QColor("#1a6640")
C_AUDIO_HOVER = QColor("#27995e")
C_SUB_CLIP    = QColor("#5c2d80")
C_SUB_HOVER   = QColor("#8040b0")
C_SELECTED    = QColor("#00eaff")
C_GRID        = QColor("#252525")
C_LABEL_BG    = QColor("#161616")
C_LABEL_SEP   = QColor("#2a2a2a")

RULER_H   = 26
LABEL_W   = 80
TRACK_H   = 52
SUB_H     = 38
PADDING   = 4
MIN_ZOOM  = 2.0
MAX_ZOOM  = 800.0
SNAP_PX   = 8
SEAM_TOL_PX = 10      # 接缝双击容差（像素）
PLAYHEAD_GRAB_PX = 8  # 播放头抓取范围（像素）
MUTE_BTN_SIZE = 16


# ─── 轨道描述器 ───
class TrackDesc:
    def __init__(self, kind: str, idx: int, y: int, h: int, label: str = ""):
        self.kind = kind
        self.idx = idx
        self.y = y
        self.h = h
        self.label = label

    @property
    def bottom(self): return self.y + self.h


class TimelineCanvas(QWidget):
    playhead_moved      = pyqtSignal(float)
    selection_changed   = pyqtSignal(object, str, int)
    clip_double_clicked = pyqtSignal(object, str, int)
    ai_separate_requested  = pyqtSignal(object)
    ai_asr_requested       = pyqtSignal(object)
    scene_detect_requested = pyqtSignal(object)
    drop_media_requested   = pyqtSignal(str, str, float, str, int, float)
    replace_video_requested = pyqtSignal(object, str)
    # 旧签名 (object, object, int) 已改为 (object, str)：clip + 可选文件路径
    freeze_requested       = pyqtSignal(object, float)
    extract_frame_requested = pyqtSignal(object, float)  # 视频片段 → 提取当前帧到图层编辑
    reverse_requested      = pyqtSignal(object)
    clip_trimmed           = pyqtSignal(object)
    subtitle_edit_requested = pyqtSignal(object)  # 右键编辑字幕 → 内联编辑
    seam_double_clicked    = pyqtSignal(object, object)  # 背景轨相邻片段接缝双击 → (A_clip, B_clip)

    def __init__(self, timeline: EditTimeline, parent=None):
        super().__init__(parent)
        self.tl = timeline
        self.zoom: float = 100.0
        self.playhead: float = 0.0

        self._drag_mode: Optional[str] = None
        self._drag_clip = None
        self._drag_track_desc: Optional[TrackDesc] = None
        self._drag_target_track: Optional[TrackDesc] = None  # 拖拽中实时计算的目标轨（提前切轨）
        self._DRAG_EXPAND_MAX: int = 30      # 命中区扩展上限；实际值 = min(30, 轨高*0.35)，轨高变了无需改常量
        self._DRAG_HYSTERESIS: int = 20      # 切轨滞回：新轨需比当前目标轨近 >=20px 才切换，防边界疯狂跳
        self._drag_orig_start: float = 0.0   # 拖拽起始时刻（画"源位置 ghost"用）
        self._drag_orig_track: Optional[TrackDesc] = None  # 拖拽起始轨（画"源位置 ghost"用）
        self._drag_start_x: int = 0
        self._drag_start_y: int = 0
        self._drag_clip_start0: float = 0.0
        self._drag_trim_start0: float = 0.0
        self._drag_trim_end0: float = 0.0
        self._drag_clip_dur0: float = 0.0
        self._drag_pixel_offset: int = 0
        self._drag_sub_dur0: float = 0.0
        self._hover_clip: Optional = None
        self._hover_td: Optional[TrackDesc] = None
        self._selected_clip = None
        self._selected_td: Optional[TrackDesc] = None
        self._creating_sub_start: float = 0.0
        self._drag_pre_snapshot: Optional[dict] = None
        self._selected_track: Optional[str] = None

        # 框选
        self._marquee_start = None
        self._marquee_rect = None
        self._marquee_active = False
        self._marquee_selected: list = []

        # ── 朗读播放器（多选字幕直接朗读，不落轨）──
        self._read_player = QMediaPlayer(self)
        self._read_audio = QAudioOutput(self)
        self._read_player.setAudioOutput(self._read_audio)
        self._read_worker = None
        self._read_busy = False

        # 拖入高亮
        self._drag_over_track: Optional[TrackDesc] = None
        self._drag_over_x: int = 0

        # 轨道布局
        self._tracks: List[TrackDesc] = []
        # 大区域拖放区（视频=最顶部，音频=最底部），单位=像素
        self._DROP_ZONE_H = 60
        self._vid_drop_zone: tuple = (-1, -1)   # (y, bottom)
        self._aud_drop_zone: tuple = (-1, -1)   # (y, bottom)
        self._timeline_dur: float = 600.0

        # ── 预创建画笔/字体 ──
        self._pen_grid       = QPen(C_GRID, 1)
        self._pen_playhead   = QPen(C_HEAD, 2)
        self._pen_label_sep  = QPen(C_LABEL_SEP, 1)
        self._pen_ruler      = QPen(C_RULER_TEXT, 1)
        self._pen_ruler_text = QPen(C_RULER_TEXT2, 1)
        self._pen_clip_text  = QPen(QColor("#e0e0e0"), 1)
        self._pen_sel_border = QPen(C_SELECTED, 2)
        self._pen_sel_trim   = QPen(C_SELECTED, 3)
        # 轨道绘制用画笔/字体
        self._pen_track_bottom = QPen(C_LABEL_SEP, 1)
        self._pen_spacer_dash  = QPen(QColor("#3a3a3a"), 1, Qt.PenStyle.DashLine)
        self._pen_spacer_text  = QPen(QColor("#555"), 1)
        self._font_track_label = QFont("Microsoft YaHei", 8)
        self._font_mute_icon   = QFont("Segoe UI Emoji", 10)
        self._font_ruler     = QFont("Segoe UI", 8)
        self._font_clip      = QFont("Microsoft YaHei", 8)
        self._font_clip_bold = QFont("Microsoft YaHei", 8)
        self._font_clip_bold.setBold(True)
        self._font_sub       = QFont("Microsoft YaHei", 9)
        self._font_sub_bold  = QFont("Microsoft YaHei", 9)
        self._font_sub_bold.setBold(True)

        # ── 预创建画笔/颜色（paintEvent 热路径，避免每帧构造）──
        self._pen_accent_2      = QPen(QColor("#00eaff"), 2)
        self._pen_accent_dash   = QPen(QColor("#00eaff"), 1, Qt.PenStyle.DashLine)
        self._brush_accent      = QColor("#00eaff")
        self._pen_drag_over     = QPen(QColor(0, 234, 255, 80), 1)
        self._color_drag_over   = QColor(0, 234, 255, 20)
        self._color_mute_overlay = QColor(0, 0, 0, 100)

        self._play_paint_cnt = 0

        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # ════════════════════════════════════════════
    #  轨道布局
    # ════════════════════════════════════════════
    #  轨道布局 —— 主轨居中，上下大区域拖放
    # ════════════════════════════════════════════
    def _rebuild_tracks(self):
        tracks: List[TrackDesc] = []
        y = RULER_H
        DZ = self._DROP_ZONE_H

        # ── 1. 字幕轨（有内容才显示）──
        for i in range(len(self.tl.subtitle_tracks)):
            if len(self.tl.subtitle_tracks[i]) == 0:
                continue
            info = self.tl.subtitle_track_info[i] if i < len(self.tl.subtitle_track_info) else None
            lbl = info.name if info else "字幕"
            tracks.append(TrackDesc("subtitle", i, y, SUB_H, lbl))
            y += SUB_H

        # ── 2. 视频拖放区：最顶部（拖入视频→新建顶部叠加轨）──
        vid_drop_y = y
        y += DZ

        # ── 3. 视频轨道：叠加轨（倒序，高idx在上）+ 主轨──
        overlay_tracks = []
        main_track = None
        for i in reversed(range(len(self.tl.video_tracks))):
            if i == 0:
                info = self.tl.video_track_info[0] if len(self.tl.video_track_info) > 0 else None
                lbl = info.name if info else "主视频"
                main_track = TrackDesc("video", 0, 0, TRACK_H, lbl)
            elif len(self.tl.video_tracks[i]) > 0:
                info = self.tl.video_track_info[i] if i < len(self.tl.video_track_info) else None
                lbl = info.name if info else f"叠{i}"
                overlay_tracks.append(TrackDesc("video", i, 0, TRACK_H, lbl))

        for td in overlay_tracks:
            td.y = y
            tracks.append(td)
            y += TRACK_H

        if main_track:
            main_track.y = y
            tracks.append(main_track)
            y += TRACK_H

        # ── 4. 音频轨 ──
        for i in range(len(self.tl.audio_tracks)):
            if i > 0 and len(self.tl.audio_tracks[i]) == 0:
                continue
            info = self.tl.audio_track_info[i] if i < len(self.tl.audio_track_info) else None
            lbl = info.name if info else f"音频{i+1}"
            tracks.append(TrackDesc("audio", i, y, TRACK_H, lbl))
            y += TRACK_H

        # ── 5. 音频拖放区：最底部 ──
        aud_drop_y = y
        y += DZ

        self._tracks = tracks
        self._vid_drop_zone = (vid_drop_y, vid_drop_y + DZ)
        self._aud_drop_zone = (aud_drop_y, aud_drop_y + DZ)
        total_h = y + 12
        self.setMinimumHeight(total_h)
        self.setFixedHeight(total_h)
        self.updateGeometry()  # 通知父级（滚动区）尺寸已变

    def _update_width(self):
        content_end = self.tl.total_duration
        # _timeline_dur 已在 __init__ 中初始化为 600.0，无需 hasattr 检查
        needed = max(600.0, content_end + 120.0)
        if hasattr(self, 'parent_timeline') and self.parent_timeline:
            pt = self.parent_timeline
            scr = getattr(pt, '_scroll', None)
            if scr and scr.viewport():
                vp_w = scr.viewport().width()
                # 使用 MIN_ZOOM 防止 zoom 异常导致除零
                needed = max(needed, vp_w / max(self.zoom, MIN_ZOOM) + content_end)
        self._timeline_dur = needed
        w = max(800, LABEL_W + int(needed * self.zoom) + 200)
        # 防止超出 Qt widget 最大宽度（约16MB），导致滚动条失效
        w = min(w, 14_000_000)
        self.setMinimumWidth(w)
        self.setFixedWidth(w)
        self.setFixedWidth(w)

    # ════════════════════════════════════════════
    #  坐标转换 / 工具
    # ════════════════════════════════════════════
    def _sec_to_x(self, sec: float) -> int:
        return LABEL_W + int(sec * self.zoom)

    def _x_to_sec(self, x: int) -> float:
        return max(0.0, (x - LABEL_W) / self.zoom)

    def _track_at(self, y: int) -> Optional[TrackDesc]:
        """返回 y 坐标所在的轨道/拖放区。拖放区始终可用。"""
        for td in self._tracks:
            if td.y <= y < td.bottom:
                return td
        # 视频拖放区（最顶部 → 新建顶部叠加轨）
        vdz = self._vid_drop_zone
        if vdz[0] >= 0 and vdz[0] <= y < vdz[1]:
            nxt = len(self.tl.video_tracks)
            return TrackDesc("video", nxt, vdz[0], vdz[1] - vdz[0], f"叠{nxt}")
        # 音频拖放区（音频轨下方）
        adz = self._aud_drop_zone
        if adz[0] >= 0 and adz[0] <= y < adz[1]:
            nxt = len(self.tl.audio_tracks)
            return TrackDesc("audio", nxt, adz[0], adz[1] - adz[0], f"音频{nxt+1}")
        return None

    def _track_at_drag(self, y: int) -> Optional[TrackDesc]:
        """拖拽时的提前切轨判定：用'到轨道中心距离'而非硬边界，并把每条轨道的命中区
        向外扩展（=min(_DRAG_EXPAND_MAX, 轨高*0.35)），使轨道主动'接住'素材（剪映式），
        不用硬拖进轨道内。只在同一类型轨道间匹配（视频↔视频、音频↔音频）；带滞回防抖。"""
        src = self._drag_track_desc
        if src is None:
            return self._track_at(y)
        cur = self._drag_target_track          # 滞回锚点（上一帧的目标轨）
        best = None
        best_d = None
        for td in self._tracks:
            if td.kind != src.kind:
                continue
            cy = td.y + td.h / 2
            d = abs(y - cy)
            if best_d is None or d < best_d:
                best_d = d
                best = td
        if best is None:
            return None
        # 滞回：已有目标轨且新轨不够更近（差 < 滞回阈值）→ 保持当前目标轨，防边界疯狂跳
        if cur is not None and best is not cur:
            cur_cy = cur.y + cur.h / 2
            cur_d = abs(y - cur_cy)
            if best_d > cur_d - self._DRAG_HYSTERESIS:
                return cur
        expand = min(self._DRAG_EXPAND_MAX, best.h * 0.35)   # 轨道越高，命中区越大（自适应）
        if best_d <= (best.h / 2 + expand):
            return best
        return None

    def _is_in_video_zone(self, y: int) -> bool:
        """y 是否在视频区域（视频拖放区+叠加轨+主轨）内"""
        vdz = self._vid_drop_zone
        if vdz[0] < 0:
            return False
        # 视频区域从拖放区顶部开始，到主轨底部结束
        # 主轨是最后一个视频轨，bottom 可通过 vdz[0] - DZ 回溯
        if not self._tracks:
            return False
        # 找最后一个 video track 的 bottom
        last_vid_bottom = vdz[0] + self._DROP_ZONE_H  # 兜底：拖放区底部
        for td in reversed(self._tracks):
            if td.kind == "video":
                last_vid_bottom = td.bottom
                break
        return vdz[0] <= y < last_vid_bottom

    def _is_in_audio_zone(self, y: int) -> bool:
        """y 是否在音频区域（音频轨+音频拖放区）内"""
        adz = self._aud_drop_zone
        if adz[0] < 0:
            return False
        return adz[0] <= y < adz[1]

    def _clips_of(self, td: TrackDesc):
        if td.kind == "video":
            if td.idx < len(self.tl.video_tracks):
                return self.tl.video_tracks[td.idx]
        elif td.kind == "audio":
            if td.idx < len(self.tl.audio_tracks):
                return self.tl.audio_tracks[td.idx]
        elif td.kind == "subtitle":
            if td.idx < len(self.tl.subtitle_tracks):
                return self.tl.subtitle_tracks[td.idx]
        return []

    def _clip_rect(self, clip, td: TrackDesc, start: Optional[float] = None) -> QRect:
        x = self._sec_to_x(clip.timeline_start if start is None else start)
        if td.kind == "subtitle":
            w = int((clip.timeline_end - clip.timeline_start) * self.zoom)
        else:
            w = int(clip.duration * self.zoom)
        pad = 2
        return QRect(x, td.y + pad, max(w, 4), td.h - pad * 2)

    def _clip_at(self, x: int, y: int):
        td = self._track_at(y)
        if not td:
            return None, None
        for clip in reversed(self._clips_of(td)):
            r = self._clip_rect(clip, td)
            if r.contains(x, y):
                return clip, td
        return None, None

    def _snap_sec(self, sec: float, exclude_clip=None, main_track_only: bool = False, max_threshold: float = 0.08) -> float:
        """通用吸附：吸附到所有轨道的片段边界 + 时间线刻度 + 0.0 起始点。
        
        - 边界吸附阈值：SNAP_PX / zoom（像素空间），优先级最高
        - 刻度吸附阈值：2x SNAP_PX / zoom，边界无匹配时生效
        - 所有轨道都参与吸附（不再限制主轨）
        - exclude_clip：拖拽中的片段，排除自身边界
        - max_threshold：吸附距离上限（秒），拖拽中用较小值防过度吸附，释放时用较大值精准对齐
        """
        if sec <= 0.01:
            return 0.0
        threshold = min(SNAP_PX / self.zoom, max_threshold)  # 上限防止低 zoom 下过度吸附
        best = sec
        best_dist = threshold
        
        # ── 1. 片段边界吸附（优先级最高）──
        for td in self._tracks:
            for clip in self._clips_of(td):
                if clip is exclude_clip:
                    continue
                c_end = clip.timeline_end if hasattr(clip, "timeline_end") else (
                    clip.timeline_start + clip.duration)
                for t in [clip.timeline_start, c_end]:
                    if abs(t - sec) < best_dist:
                        best_dist = abs(t - sec)
                        best = t
        
        # ── 2. 时间线刻度吸附（边界无匹配时生效）──
        if best == sec:  # 边界未吸附
            grid_step = self._grid_step()
            if grid_step > 0:
                snap_threshold = 2 * threshold  # 刻度吸附阈值稍大
                nearest_grid = round(sec / grid_step) * grid_step
                if abs(nearest_grid - sec) <= snap_threshold:
                    best = nearest_grid
                    best_dist = abs(nearest_grid - sec)
        
        # ── 3. 0.0 起始点吸附 ──
        if abs(sec) < best_dist and abs(sec) <= max(threshold, 1.0):
            best = 0.0
        
        # ── 4. 播放头吸附 ──
        ph = getattr(self, 'playhead', None)
        if ph is not None and abs(ph - sec) < best_dist and abs(ph - sec) <= threshold:
            best = ph
            best_dist = abs(ph - sec)
        
        return best

    def _snap_to_keyframes(self, sec: float) -> float:
        """播放头吸附：先吸附片段边界（SNAP_PX/zoom 像素范围），再吸附关键帧（0.6s 范围）。
        片段边界包括所有轨道片段的 timeline_start / timeline_end，以及 0.0 起始点。"""
        best = sec
        best_dist = float("inf")

        # ── 1. 片段边界吸附 ──
        boundary_threshold = SNAP_PX / self.zoom
        for td in self._tracks:
            for clip in self._clips_of(td):
                c_end = clip.timeline_end if hasattr(clip, "timeline_end") else (
                    clip.timeline_start + clip.duration)
                for t in [clip.timeline_start, c_end]:
                    dist = abs(t - sec)
                    if dist < best_dist and dist <= boundary_threshold:
                        best_dist = dist
                        best = t
        # 0.0 也是吸附点
        if abs(sec) <= boundary_threshold and abs(sec) < best_dist:
            best = 0.0
            best_dist = abs(sec)

        # ── 2. 关键帧吸附（0.6s 范围） ──
        kf_threshold = 0.6
        for td in self._tracks:
            for clip in self._clips_of(td):
                kfs = getattr(clip, "keyframes", None)
                if not kfs:
                    continue
                for kf_list in kfs.values():
                    for t, _ in kf_list:
                        abs_t = clip.timeline_start + t
                        dist = abs(abs_t - sec)
                        if dist < best_dist and dist <= kf_threshold:
                            best_dist = dist
                            best = abs_t
        return best

    def _snap_out_of_overlap(self, clip, td: TrackDesc):
        """字幕专用：同轨防重叠——向后推移时间。"""
        if td.kind != "subtitle":
            return
        c_end = clip.timeline_start + (clip.timeline_end - clip.timeline_start)
        best_left = 0.0
        best_right = float("inf")
        for other in self._clips_of(td):
            if other is clip:
                continue
            o_end = other.timeline_end if hasattr(other, "timeline_end") else (
                other.timeline_start + other.duration)
            if clip.timeline_start < o_end and c_end > other.timeline_start:
                left_candidate = max(0.0, other.timeline_start - (c_end - clip.timeline_start))
                right_candidate = o_end
                if left_candidate > best_left:
                    best_left = left_candidate
                if right_candidate < best_right:
                    best_right = right_candidate
        if best_right < float("inf") or best_left > 0.0:
            dist_to_left = abs(clip.timeline_start - best_left)
            dist_to_right = abs(clip.timeline_start - best_right) if best_right < float("inf") else float("inf")
            if dist_to_left <= dist_to_right:
                clip.timeline_start = best_left
            else:
                clip.timeline_start = best_right

    def _auto_stack_to_higher_track(self, clip, td: TrackDesc) -> Optional[TrackDesc]:
        """视频/音频专用：检测同轨重叠，若有则向上堆叠到不重叠轨道。
        返回新的 TrackDesc（若轨道改变），否则返回 None。"""
        if td.kind not in ("video", "audio"):
            return None
        c_end = clip.timeline_start + clip.duration
        clips = self._clips_of(td)
        has_overlap = any(
            (c.timeline_end if hasattr(c, "timeline_end") else (c.timeline_start + c.duration)) > clip.timeline_start
            and clip.timeline_start + clip.duration > c.timeline_start
            for c in clips if c is not clip
        )
        if not has_overlap:
            return None  # 无重叠，原地不动

        tracks = self.tl.video_tracks if td.kind == "video" else self.tl.audio_tracks
        # 从当前轨+1 向上寻找不重叠的轨道
        for i in range(td.idx + 1, len(tracks)):
            conflict = any(
                (c.timeline_end if hasattr(c, "timeline_end") else (c.timeline_start + c.duration)) > clip.timeline_start
                and clip.timeline_start + clip.duration > c.timeline_start
                for c in tracks[i]
            )
            if not conflict:
                if clip in clips:
                    clips.remove(clip)
                tracks[i].append(clip)
                self.tl.changed.emit()
                # 查找已显示的 TrackDesc
                for t in self._tracks:
                    if t.kind == td.kind and t.idx == i:
                        return t
                self._rebuild_tracks()
                for t in self._tracks:
                    if t.kind == td.kind and t.idx == i:
                        return t
                return None

        # 所有轨道都冲突 → 在顶部新建轨道
        if clip in clips:
            clips.remove(clip)
        if td.kind == "video":
            new_idx = self.tl.add_video_track()
        else:
            new_idx = self.tl.add_audio_track()
        tracks = self.tl.video_tracks if td.kind == "video" else self.tl.audio_tracks
        tracks[new_idx].append(clip)
        self.tl.changed.emit()
        self._rebuild_tracks()
        for t in self._tracks:
            if t.kind == td.kind and t.idx == new_idx:
                return t
        return None

    def _track_info(self, td: TrackDesc):
        if td.kind == "video" and td.idx < len(self.tl.video_track_info):
            return self.tl.video_track_info[td.idx]
        elif td.kind == "audio" and td.idx < len(self.tl.audio_track_info):
            return self.tl.audio_track_info[td.idx]
        elif td.kind == "subtitle" and td.idx < len(self.tl.subtitle_track_info):
            return self.tl.subtitle_track_info[td.idx]
        return None

    def _mute_btn_rect(self, td: TrackDesc) -> QRect:
        bx = LABEL_W - MUTE_BTN_SIZE - 4
        by = td.y + (td.h - MUTE_BTN_SIZE) // 2
        return QRect(bx, by, MUTE_BTN_SIZE, MUTE_BTN_SIZE)

    def _format_time(self, sec: float) -> str:
        m = int(sec // 60)
        s = sec % 60
        if sec < 60:
            return f"{s:.1f}s"
        return f"{m}:{s:04.1f}"

    def _invalidate_thumb_cache(self):
        """zoom 变化时清空所有片段的缩略图缓存，并触发延迟重新生成（缩放自适应张数）"""
        for td in self._tracks:
            for clip in self._clips_of(td):
                if hasattr(clip, "_scaled_thumbs_cache"):
                    clip._scaled_thumbs_cache = None
        # 300ms debounce 后重新生成缩略图（缩放自适应张数）
        tw = getattr(self, 'parent_timeline', None)
        if tw and hasattr(tw, '_thumb_regen_timer'):
            tw._thumb_regen_timer.start()

    # ════════════════════════════════════════════
    #  绘制
    # ════════════════════════════════════════════
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        clip_rect = event.rect()
        W = self.width()

        # 背景
        p.fillRect(clip_rect, C_BG)
        p.fillRect(0, clip_rect.top(), LABEL_W, clip_rect.height(), C_LABEL_BG)

        # 标签分隔线
        p.setPen(self._pen_label_sep)
        p.drawLine(LABEL_W, 0, LABEL_W, self.height())

        self._draw_grid(p, clip_rect)
        self._draw_ruler(p, clip_rect)
        self._draw_tracks(p, clip_rect)

        # 外部拖入视觉反馈
        if self._drag_over_track is not None:
            dot = QRect(LABEL_W, self._drag_over_track.y,
                        W - LABEL_W, self._drag_over_track.h)
            p.fillRect(dot, self._color_drag_over)
            p.setPen(self._pen_drag_over)
            p.drawLine(dot.left(), dot.top(), dot.right(), dot.top())
            p.drawLine(dot.left(), dot.bottom() - 1, dot.right(), dot.bottom() - 1)
            if self._drag_over_x >= LABEL_W:
                p.setBrush(self._brush_accent)
                p.drawEllipse(QPointF(self._drag_over_x, dot.top() + 2), 3, 3)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(self._pen_accent_2)
                p.drawLine(self._drag_over_x, dot.top(), self._drag_over_x, dot.bottom())

        self._draw_clips(p, clip_rect)

        # 框选
        if self._drag_mode == "marquee" and self._marquee_rect:
            p.setPen(self._pen_accent_dash)
            p.drawRect(self._marquee_rect)
        if (self._drag_mode == "marquee" or self._marquee_active) and self._marquee_selected:
            p.setPen(self._pen_accent_2)
            for clip, td in self._marquee_selected:
                r = self._clip_rect(clip, td)
                p.drawRect(r.adjusted(1, 1, -1, -1))

        self._draw_playhead(p)
        p.end()

    def _draw_grid(self, p: QPainter, rect: QRect):
        step = self._grid_step()
        p.setPen(self._pen_grid)
        start_sec = max(0, self._x_to_sec(rect.left() - 50))
        end_x = rect.right() + 200
        sec = start_sec
        while True:
            x = self._sec_to_x(sec)
            if x > end_x:
                break
            if x >= LABEL_W:
                p.drawLine(x, RULER_H, x, self.height())
            sec += step

    def _draw_ruler(self, p: QPainter, rect: QRect):
        p.fillRect(max(LABEL_W, rect.left()), 0, rect.width(), RULER_H, C_RULER_BG)
        step = self._grid_step()
        p.setFont(self._font_ruler)
        start_sec = max(0, self._x_to_sec(rect.left() - 50))
        end_x = rect.right() + 200
        sec = start_sec
        while True:
            x = self._sec_to_x(sec)
            if x > end_x:
                break
            if x >= LABEL_W:
                p.setPen(self._pen_ruler)
                p.drawLine(x, RULER_H - 5, x, RULER_H)
                label = self._format_time(sec)
                p.setPen(self._pen_ruler_text)
                p.drawText(x + 3, RULER_H - 8, label)
            sec += step

    def _grid_step(self) -> float:
        raw = 60 / self.zoom
        for s in [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600]:
            if s >= raw:
                return s
        return 600

    def _draw_tracks(self, p: QPainter, rect: QRect):
        p.setFont(self._font_track_label)
        left = rect.left()
        right = rect.right()
        for i, td in enumerate(self._tracks):
            if td.y + td.h < rect.top() or td.y > rect.bottom():
                continue
            bg = C_TRACK_BG_A if i % 2 == 0 else C_TRACK_BG_B
            p.fillRect(max(LABEL_W, left), td.y, right - max(LABEL_W, left), td.h, bg)
            p.fillRect(0, td.y, LABEL_W, td.h, C_LABEL_BG)

            # 轨道标签（为静音按钮预留空间）
            p.setPen(self._pen_clip_text)
            p.setFont(self._font_track_label)
            text_w = LABEL_W - MUTE_BTN_SIZE - 10
            p.drawText(QRect(4, td.y, text_w, td.h),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       td.label)

            # 静音按钮 (非字幕轨)
            info = self._track_info(td)
            is_muted = info.muted if info else False
            if td.kind != "subtitle":
                mr = self._mute_btn_rect(td)
                icon = "🔇" if is_muted else "🔊"
                p.setFont(self._font_mute_icon)
                p.drawText(mr, Qt.AlignmentFlag.AlignCenter, icon)
                if is_muted:
                    p.fillRect(LABEL_W, td.y, right - LABEL_W, td.h, self._color_mute_overlay)

            # 轨道底边
            p.setPen(self._pen_track_bottom)
            p.drawLine(0, td.bottom - 1, self.width(), td.bottom - 1)

        # ── 大区域拖放区绘制 ──
        is_dragging = self._drag_mode in ("move",) or self._drag_over_track is not None
        vdz = self._vid_drop_zone
        if vdz[0] >= 0:
            if is_dragging:
                # 拖拽中：高亮显示
                p.fillRect(LABEL_W, vdz[0], right - LABEL_W, vdz[1] - vdz[0],
                           QColor(30, 60, 100, 80))
                p.setPen(self._pen_spacer_dash)
                p.drawLine(LABEL_W, (vdz[0] + vdz[1]) // 2,
                           self.width(), (vdz[0] + vdz[1]) // 2)
                p.setFont(self._font_track_label)
                p.setPen(self._pen_spacer_text)
                dz_h = vdz[1] - vdz[0]
                p.drawText(QRect(LABEL_W, vdz[0], self.width() - LABEL_W, dz_h),
                           Qt.AlignmentFlag.AlignCenter,
                           "🎬  拖放视频 → 自动叠加到上方轨道")
            else:
                # 静态：微暗背景，无文字
                p.fillRect(LABEL_W, vdz[0], right - LABEL_W, vdz[1] - vdz[0],
                           QColor(25, 25, 35, 60))
        adz = self._aud_drop_zone
        if adz[0] >= 0:
            if is_dragging:
                p.fillRect(LABEL_W, adz[0], right - LABEL_W, adz[1] - adz[0],
                           QColor(30, 60, 100, 80))
                p.setPen(self._pen_spacer_dash)
                p.drawLine(LABEL_W, (adz[0] + adz[1]) // 2,
                           self.width(), (adz[0] + adz[1]) // 2)
                p.setFont(self._font_track_label)
                p.setPen(self._pen_spacer_text)
                dz_h = adz[1] - adz[0]
                p.drawText(QRect(LABEL_W, adz[0], self.width() - LABEL_W, dz_h),
                           Qt.AlignmentFlag.AlignCenter,
                           "🔊  拖放音频 → 新建音频轨")
            else:
                p.fillRect(LABEL_W, adz[0], right - LABEL_W, adz[1] - adz[0],
                           QColor(25, 25, 35, 60))

    def _draw_clips(self, p: QPainter, clip_rect: QRect):
        # 拖拽目标轨高亮已按需求移除（不再绘制蓝色轨道高亮 / 深蓝底色）
        p.setOpacity(1.0)
        # 拖拽中跳过缩略图+关键帧渲染：每像素 mouseMove→update→完整重绘开销巨大，
        # 跳过非必要的装饰元素可消除闪烁并大幅提升拖拽跟手度。
        _fast = (self._drag_mode is not None)

        for td in self._tracks:
            if td.y + td.h < clip_rect.top() or td.y > clip_rect.bottom():
                continue

            if td.kind == "video":
                cn, ch = C_VIDEO_CLIP, C_VIDEO_HOVER
            elif td.kind == "audio":
                cn, ch = C_AUDIO_CLIP, C_AUDIO_HOVER
            else:
                cn, ch = C_SUB_CLIP, C_SUB_HOVER

            for clip in self._clips_of(td):
                p.setOpacity(1.0)
                r = self._clip_rect(clip, td)
                if r.right() < clip_rect.left() or r.left() > clip_rect.right():
                    continue

                is_sel = (clip is self._selected_clip)
                is_hover = (clip is self._hover_clip and td is self._hover_td)

                # 隐藏片段：半透明绘制
                vis = getattr(clip, "visible", True)
                fill_base = ch if is_hover else cn
                if not vis:
                    fill_base = QColor(fill_base.red(), fill_base.green(), fill_base.blue(), 60)
                fill = fill_base

                # 音频轨
                if td.kind == "audio":
                    p.fillRect(r, fill)
                    if is_sel:
                        p.setPen(self._pen_sel_border)
                        p.drawRect(r.adjusted(1, 1, -1, -1))
                        # 选中时画 trim 手柄（左右边缘竖线），让抓取点可见
                        p.setPen(self._pen_sel_trim)
                        p.drawLine(r.left() + 2, r.top() + 4, r.left() + 2, r.bottom() - 4)
                        p.drawLine(r.right() - 2, r.top() + 4, r.right() - 2, r.bottom() - 4)
                    else:
                        p.setPen(QPen(fill.darker(160), 1))
                        p.drawRect(r)
                    # 音频波形：按当前可见宽度对峰值降采样，避免长音频逐点绘制造成卡顿。
                    peaks = getattr(clip, "waveform", None)
                    if peaks and not _fast and r.width() > 4 and r.height() > 8:
                        p.save()
                        p.setClipRect(r.adjusted(2, 2, -2, -2))
                        mid_y = r.center().y()
                        amp_h = max(2, (r.height() - 8) // 2)
                        draw_w = max(1, r.width() - 4)
                        step = max(1, len(peaks) // draw_w)
                        color = QColor("#c5a8ff") if vis else QColor(197, 168, 255, 90)
                        p.setPen(QPen(color, 1))
                        for x_off in range(draw_w):
                            start = x_off * step
                            if start >= len(peaks):
                                break
                            peak = max(peaks[start:min(len(peaks), start + step)])
                            height = max(1, int(peak * amp_h))
                            x = r.left() + 2 + x_off
                            p.drawLine(x, mid_y - height, x, mid_y + height)
                        p.restore()
                    # ── 淡入淡出 UI ──
                    fi = getattr(clip, "fade_in", 0) or 0
                    fo = getattr(clip, "fade_out", 0) or 0
                    if fi > 0.001:
                        fi_px = int(fi * self.zoom)
                        if fi_px > 0:
                            g = QLinearGradient(r.left(), 0, r.left() + fi_px, 0)
                            g.setColorAt(0.0, QColor(0, 0, 0, 180))
                            g.setColorAt(1.0, QColor(0, 0, 0, 0))
                            p.fillRect(QRect(r.left(), r.top(), fi_px, r.height()), g)
                            # 左上角 "IN" 标签
                            p.save()
                            p.setPen(QPen(QColor("#a5d6a7"), 1))
                            p.setFont(QFont(self._font_clip.family(), 7))
                            p.drawText(QRect(r.left() + 2, r.top() + 1, fi_px - 2, 12),
                                       Qt.AlignmentFlag.AlignLeft, "IN")
                            p.restore()
                    if fo > 0.001:
                        fo_px = int(fo * self.zoom)
                        if fo_px > 0:
                            g = QLinearGradient(r.right() - fo_px, 0, r.right(), 0)
                            g.setColorAt(0.0, QColor(0, 0, 0, 0))
                            g.setColorAt(1.0, QColor(0, 0, 0, 180))
                            p.fillRect(QRect(r.right() - fo_px, r.top(), fo_px, r.height()), g)
                            # 右上角 "OUT" 标签
                            p.save()
                            p.setPen(QPen(QColor("#e0a0a0"), 1))
                            p.setFont(QFont(self._font_clip.family(), 7))
                            p.drawText(QRect(r.right() - fo_px, r.top() + 1, fo_px - 2, 12),
                                       Qt.AlignmentFlag.AlignRight, "OUT")
                            p.restore()
                    # 文字
                    lbl = getattr(clip, "label", "")
                    name = lbl if lbl else (os.path.basename(clip.source_path) if hasattr(clip, "source_path") else "")
                    p.setPen(self._pen_clip_text)
                    fnt = self._font_sub_bold if is_sel else self._font_sub
                    p.setFont(fnt)
                    fm = QFontMetrics(fnt)
                    elided = fm.elidedText(name, Qt.TextElideMode.ElideRight, r.width() - 8)
                    p.drawText(r.adjusted(4, 0, -4, 0),
                               Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)
                    # 关键帧点（音量等）— 拖拽中跳过
                    if not _fast:
                        self._draw_clip_keyframes(p, clip, r)
                    continue

                # 视频/字幕轨
                p.fillRect(r, fill)

                # 选中边框 + trim手柄
                if is_sel:
                    p.setPen(self._pen_sel_border)
                    p.drawRect(r.adjusted(1, 1, -1, -1))
                    p.setPen(self._pen_sel_trim)
                    p.drawLine(r.left() + 2, r.top() + 4, r.left() + 2, r.bottom() - 4)
                    p.drawLine(r.right() - 2, r.top() + 4, r.right() - 2, r.bottom() - 4)
                else:
                    p.setPen(QPen(fill.darker(160), 1))
                    p.drawRect(r)

                # 缩略图（视频轨）— 仅绘制视口内可见的缩略图（虚拟列表思想）
                # ★ 拖拽中跳过缩略图：每像素重绘缩略图是最主要的性能瓶颈
                if td.kind == "video" and not _fast:
                    thumbs = getattr(clip, "thumbnails", None)
                    if thumbs and len(thumbs) > 0:
                        p.save()
                        if not vis:
                            p.setOpacity(0.3)
                        thumb_h = r.height() - 4
                        if thumb_h >= 2:
                            n = len(thumbs)
                            clip_w = max(r.width() - 4, 1)
                            # 缩略图数量与缩放解耦（不触发 FFmpeg 重抽），但渲染时自适应降采样：
                            # 每张至少 8px 才有辨识度，小于此值则均匀跳张、加大单张宽度。
                            MIN_THUMB_W = 8
                            if clip_w // n < MIN_THUMB_W:
                                draw_n = max(1, clip_w // MIN_THUMB_W)
                                step = max(1, n // draw_n)
                                thumb_w = max(clip_w // draw_n, 1)
                            else:
                                step = 1
                                draw_n = n
                                thumb_w = max(clip_w // n, 1)
                            # 视口裁剪：只画可见列
                            vp_l, vp_r = r.x(), r.right()
                            tw = getattr(self, 'parent_timeline', None)
                            if tw and hasattr(tw, '_scroll'):
                                scroll = tw._scroll
                                vp_l2 = scroll.horizontalScrollBar().value()
                                vp_r2 = vp_l2 + scroll.viewport().width()
                                vp_l, vp_r = max(vp_l, vp_l2), min(vp_r, vp_r2)
                            start_i = max(0, int((vp_l - r.x()) / max(thumb_w, 1)))
                            end_i = min(draw_n, int((vp_r - r.x()) / max(thumb_w, 1)) + 1)
                            for di in range(start_i, end_i):
                                i = di * step  # 源缩略图索引（step>1 时跳张）
                                if i >= n:
                                    break
                                tx = r.x() + 2 + di * thumb_w
                                if tx >= r.right() - 2:
                                    break
                                sc = thumbs[i]
                                # 末张缩略图补到右边界，消除余数间隙（蓝条）
                                tw_use = thumb_w
                                if di == draw_n - 1:
                                    tw_use = max(1, (r.right() - 2) - tx)
                                self._draw_pixmap_cover(p, sc, tx, r.y() + 2,
                                                       tw_use, thumb_h)
                        p.restore()
                    elif hasattr(clip, "thumbnail") and clip.thumbnail:
                        # 单张缩略图：cover 裁剪保留宽高比
                        p.save()
                        if not vis:
                            p.setOpacity(0.3)
                        self._draw_pixmap_cover(p, clip.thumbnail,
                                               r.x() + 2, r.y() + 2,
                                               r.width() - 4, r.height() - 4)
                        p.restore()

                # 文字
                p.setPen(self._pen_clip_text)
                if td.kind == "subtitle":
                    fnt = self._font_sub_bold if is_sel else self._font_sub
                    name = clip.text[:24] + ("…" if len(clip.text) > 24 else "")
                else:
                    fnt = self._font_clip_bold if is_sel else self._font_clip
                    lbl = getattr(clip, "label", "")
                    name = lbl if lbl else (os.path.basename(clip.source_path) if hasattr(clip, "source_path") else "")
                p.setFont(fnt)
                fm = QFontMetrics(fnt)
                elided = fm.elidedText(name, Qt.TextElideMode.ElideRight, r.width() - 8)
                p.drawText(r.adjusted(4, 0, -4, 0),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)

                # 关键帧点 — 拖拽中跳过
                if not _fast:
                    self._draw_clip_keyframes(p, clip, r)

                # 转场标记：左右相邻背景轨片段的接缝处，显示醒目的转场指示器
                # 仅背景轨（idx==0）生效：compositor 也只在 track 0 渲染转场
                # ★ 拖拽中跳过转场指示器渲染（非核心元素，省掉重绘开销）
                if td.kind == "video" and td.idx == 0 and not _fast:
                    ot = getattr(clip, "out_transition", None)
                    if not isinstance(ot, dict):
                        # 外部/旧项目数据异常时，时间线仍应可绘制；数据模型会在
                        # 新建或重新载入片段时完成正式规范化。
                        ot = None
                    # 找紧接 clip 之后的下一个背景轨片段
                    nxt = None
                    tracks = getattr(self.tl, 'video_tracks', []) or []
                    if tracks:
                        track0 = tracks[0]
                        for j, c in enumerate(track0):
                            if c is clip and j + 1 < len(track0):
                                nxt = track0[j + 1]
                                break
                    a_end = clip.timeline_end
                    # 接缝处右边界 x 坐标（clamp 到可见范围）
                    seam_x = self._sec_to_x(a_end)
                    tr_has = bool(ot and ot.get("type"))

                    # 转场指示器统一在满透明度下绘制（即使该片段正在被拖拽）
                    p.save()
                    p.setOpacity(1.0)
                    if tr_has:
                        # ── 已配置转场 ── 醒目圆形徽标 + 时长区间 ──
                        d_sec = float(ot.get("duration", 0.5))
                        d_px = max(6, int(d_sec * self.zoom))
                        # 时长区间高亮带（接缝向左，绿色渐变）
                        band_left = max(r.left(), seam_x - d_px)
                        band_w = seam_x - band_left
                        if band_w > 0:
                            grad = QLinearGradient(seam_x, 0, band_left, 0)
                            grad.setColorAt(0.0, QColor(76, 175, 80, 210))    # 绿色
                            grad.setColorAt(0.5, QColor(102, 187, 106, 130))
                            grad.setColorAt(1.0, QColor(102, 187, 106, 0))
                            p.fillRect(QRect(band_left, r.top(), band_w, r.height()), grad)
                        # 接缝竖线（粗亮）
                        p.setPen(QPen(QColor("#4caf50"), 2))
                        p.drawLine(seam_x, r.top(), seam_x, r.bottom())
                        # 中央圆形转场徽标（绿底白字"转"，一眼可辨）
                        cy = (r.top() + r.bottom()) // 2
                        rad = 10
                        p.setPen(Qt.PenStyle.NoPen)
                        p.setBrush(QColor("#4caf50"))
                        p.drawEllipse(QRect(seam_x - rad, cy - rad, rad * 2, rad * 2))
                        p.setPen(QPen(QColor("#ffffff"), 1))
                        p.setFont(QFont(self._font_clip.family(), 10, QFont.Weight.Bold))
                        p.drawText(QRect(seam_x - rad, cy - rad, rad * 2, rad * 2),
                                   Qt.AlignmentFlag.AlignCenter, "转")
                        # 时长标签（徽标上方）
                        dur_text = f"{d_sec:.1f}s"
                        p.setPen(QPen(QColor("#a5d6a7"), 1))
                        p.setFont(QFont(self._font_clip.family(), 8))
                        p.drawText(QRect(seam_x - d_px, r.top() - 12, d_px, 12),
                                   Qt.AlignmentFlag.AlignCenter, dur_text)
                    else:
                        # ── 未配置转场，但有相邻片段 ── 虚线"+"幽灵徽标 + 提示 ──
                        if nxt is not None:
                            cy = (r.top() + r.bottom()) // 2
                            rad = 9
                            # 虚线圆 + 加号（清晰提示可双击添加）
                            p.setPen(QPen(QColor("#3d8ef8"), 1.5, Qt.PenStyle.DashLine))
                            p.setBrush(Qt.BrushStyle.NoBrush)
                            p.drawEllipse(QRect(seam_x - rad, cy - rad, rad * 2, rad * 2))
                            p.setPen(QPen(QColor("#3d8ef8"), 1.5))
                            p.drawLine(seam_x - 4, cy, seam_x + 4, cy)
                            p.drawLine(seam_x, cy - 4, seam_x, cy + 4)
                            # 提示文字（足够宽时显示，带深色药丸底保证可读）
                            if self.zoom >= 16:
                                hint = "双击加转场"
                                fm = p.fontMetrics()
                                tw = fm.horizontalAdvance(hint)
                                pad = 6
                                pill_w = int(tw + pad * 2)
                                pill_x = int(seam_x - rad - 6 - pill_w)
                                pill_x = max(int(r.left()) + 2, pill_x)
                                pill = QRect(pill_x, cy - 9, pill_w, 18)
                                p.setBrush(QColor(0, 0, 0, 150))
                                p.setPen(QPen(QColor("#3d8ef8"), 1))
                                p.drawRoundedRect(pill, 4, 4)
                                p.setPen(QPen(QColor("#cfe6ff"), 1))
                                p.setFont(QFont(self._font_clip.family(), 9))
                                p.drawText(pill, Qt.AlignmentFlag.AlignCenter, hint)
                    p.restore()

        p.setOpacity(1.0)  # 复位不透明度，避免拖拽半透明泄漏到播放头/框选/选中绘制

    @staticmethod
    def _draw_pixmap_cover(p: QPainter, sc, tx: int, ty: int, dw: int, dh: int):
        """以 cover 方式将 pixmap 绘制到目标矩形（保持原始宽高比，居中裁剪溢出部分）。

        相比 IgnoreAspectRatio 直接拉伸，cover 不会让视频帧被压扁/拉长，
        而是等比缩放后居中裁剪，符合剪辑软件缩略图条的常见观感。
        """
        if sc is None:
            return
        sw = sc.width()
        sh = sc.height()
        if sw <= 0 or sh <= 0 or dw <= 0 or dh <= 0:
            return
        scale = max(dw / sw, dh / sh)
        cw = dw / scale
        ch = dh / scale
        sx = max(0, int((sw - cw) / 2))
        sy = max(0, int((sh - ch) / 2))
        sw_src = min(int(cw), sw - sx)
        sh_src = min(int(ch), sh - sy)
        if sw_src <= 0 or sh_src <= 0:
            return
        p.drawPixmap(int(tx), int(ty), int(dw), int(dh), sc, sx, sy, sw_src, sh_src)

    def _draw_clip_keyframes(self, p: QPainter, clip, r: QRect):
        """在片段下方绘制关键帧菱形标记，画完后重置画笔防止泄漏"""
        kfs = getattr(clip, "keyframes", None)
        if not kfs:
            return
        all_times = set()
        for kf_list in kfs.values():
            for t, _ in kf_list:
                all_times.add(t)
        if not all_times:
            return
        dur = getattr(clip, "duration", None)
        if dur is None and hasattr(clip, "timeline_end"):
            dur = clip.timeline_end - clip.timeline_start
        if not (dur and dur > 0):
            return
        # 预计算菱形路径（只创建一次，平移到各关键帧位置复用）
        diamond_cache = getattr(TimelineWidget, '_kf_diamond_cache', None)
        if diamond_cache is None:
            diamond_cache = QPainterPath()
            diamond_cache.moveTo(0, -6)
            diamond_cache.lineTo(5, 0)
            diamond_cache.lineTo(0, 6)
            diamond_cache.lineTo(-5, 0)
            diamond_cache.closeSubpath()
            TimelineWidget._kf_diamond_cache = diamond_cache
        p.setPen(self._pen_accent_2)
        p.setBrush(self._brush_accent)
        for t in all_times:
            kx = int(r.x() + t / dur * r.width())
            ky = r.bottom() + 2
            p.save()
            p.translate(kx, ky)
            p.drawPath(diamond_cache)
            p.restore()
        # 关键：重置画笔，防止泄漏到后续轨道渲染
        p.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_playhead(self, p: QPainter):
        x = self._sec_to_x(self.playhead)
        p.setPen(self._pen_playhead)
        p.drawLine(x, 0, x, self.height())
        path = QPainterPath()
        path.moveTo(x - 6, 0)
        path.lineTo(x + 6, 0)
        path.lineTo(x, 12)
        path.closeSubpath()
        p.fillPath(path, C_HEAD)

    # ════════════════════════════════════════════
    #  鼠标交互
    # ════════════════════════════════════════════
    def mousePressEvent(self, e: QMouseEvent):
        x, y = e.pos().x(), e.pos().y()
        self.setFocus()

        # 静音按钮检测
        if y >= RULER_H:
            for td in self._tracks:
                if td.kind == "subtitle":
                    continue
                if self._mute_btn_rect(td).contains(x, y):
                    info = self._track_info(td)
                    if info:
                        self.tl._save_history()  # 撤回
                        info.muted = not info.muted
                        self.tl.changed.emit()
                        self.update()
                        if hasattr(self, "parent_timeline"):
                            pt = self.parent_timeline
                            if pt._preview_player and pt._playing:
                                # 直接重排音频 slot，不先 stop_audio 全停
                                # play_all_audio 会自行跳过已静音轨道 + 停止多余 slot
                                pt._preview_player.play_all_audio(self.playhead)
                    return

        if e.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(x, y)
            return

        if e.button() == Qt.MouseButton.LeftButton:
            tw = getattr(self, 'parent_timeline', None)  # TimelineWidget（播放控制）
            if y < RULER_H:
                # 点击标尺
                if tw is None:
                    return
                sec = self._snap_to_keyframes(self._x_to_sec(x))
                if tw._playing:
                    # 播放中点击 → 跳到此处并继续播放（音画同步重置）
                    self._seek_and_continue_playing(sec)
                else:
                    # 未播放 → 仅 seek（保持暂停）
                    self._drag_mode = "playhead"
                    self._move_playhead(x, snap_kf=True)
                    if tw._preview_player:
                        tw._preview_player.set_decode_state("scrubbing")
                return

            clip, clip_td = self._clip_at(x, y)

            if clip is None and clip_td and clip_td.kind == "subtitle" and y >= RULER_H:
                sec = self._x_to_sec(x)
                self._drag_mode = "create_sub"
                self._creating_sub_start = sec
                return

            if clip is not None:
                self._selected_clip = clip
                self._selected_td = clip_td
                self._selected_track = clip_td.kind
                self.selection_changed.emit(clip, clip_td.kind, clip_td.idx)

                r = self._clip_rect(clip, clip_td)
                edge_px = min(12, r.width() // 4)

                if x - r.left() < edge_px:
                    self._drag_mode = "trim_left"
                    if clip_td.kind == "subtitle":
                        self._drag_trim_start0 = clip.timeline_start
                        self._drag_trim_end0 = clip.timeline_end
                    else:
                        self._drag_trim_start0 = clip.trim_start
                        self._drag_trim_end0 = clip.trim_end
                    self._drag_clip_start0 = clip.timeline_start
                    self._drag_clip_dur0 = clip.duration
                elif r.right() - x < edge_px:
                    self._drag_mode = "trim_right"
                    if clip_td.kind == "subtitle":
                        self._drag_trim_start0 = clip.timeline_start
                        self._drag_trim_end0 = clip.timeline_end
                    else:
                        self._drag_trim_start0 = clip.trim_start
                        self._drag_trim_end0 = clip.trim_end
                    self._drag_clip_start0 = clip.timeline_start
                    self._drag_clip_dur0 = clip.duration
                else:
                    self._drag_mode = "move"
                    if clip_td.kind == "subtitle":
                        self._drag_sub_dur0 = clip.timeline_end - clip.timeline_start

                self._drag_clip = clip
                self._drag_track_desc = clip_td
                self._drag_target_track = clip_td  # 拖拽起始：目标轨=源轨
                self._drag_orig_start = clip.timeline_start   # 源位置 ghost 起点
                self._drag_orig_track = clip_td
                self._drag_start_x = x
                self._drag_start_y = y
                self._drag_clip_start0 = clip.timeline_start
                self._drag_pixel_offset = x - self._sec_to_x(clip.timeline_start)
                self._drag_pre_snapshot = self.tl._snapshot()
                self._drag_modified = False
                return

            # 空白区域：
            #   - 播放中点击空白 → 播放头跳到此处并继续播放（不论是否靠近播放头）
            #   - 暂停态：
            #       · 靠近播放头 → 拖拽播放头
            #       · 否则 → 移动播放头到点击位置 + 框选
            ph_x = self._sec_to_x(self.playhead)
            if tw is not None and tw._playing:
                # 播放中点击空白：seek 到此处并从该处继续播放（音画同步重置）
                sec = self._snap_to_keyframes(self._x_to_sec(x))
                self._seek_and_continue_playing(sec)
                return
            if abs(x - ph_x) <= PLAYHEAD_GRAB_PX:
                # 暂停态：抓住播放头拖拽
                self._drag_mode = "playhead"
                self._move_playhead(x, snap_kf=True)
                if tw and tw._preview_player:
                    tw._preview_player.set_decode_state("scrubbing")
            else:
                # 点击空白区域 → 播放头跳到此处 + 取消选中
                self._move_playhead(x, snap_kf=True)
                self._selected_clip = None
                self._selected_td = None
                self._selected_track = None
                self._marquee_selected = []
                self._marquee_active = False
                self.selection_changed.emit(None, "", -1)
                self._drag_mode = "marquee"
                self._marquee_start = (x, y)
                self._marquee_rect = QRect(x, y, 0, 0)

        self.update()

    def mouseMoveEvent(self, e: QMouseEvent):
        x, y = e.pos().x(), e.pos().y()

        if self._drag_mode == "playhead":
            self._move_playhead(x, snap_kf=True)
            # 自动滚动画布：鼠标接近视口边缘时滚动
            tw = getattr(self, 'parent_timeline', None)
            if tw and hasattr(tw, '_scroll'):
                scroll = tw._scroll
                vp = scroll.viewport()
                if vp:
                    # 视口在 canvas 坐标系中的左/右边界
                    vp_left = scroll.horizontalScrollBar().value()
                    vp_right = vp_left + vp.width()
                    sb = scroll.horizontalScrollBar()
                    SCROLL_SPEED = 40  # 每帧滚动像素
                    EDGE = 50          # 检测边缘宽度
                    if x < vp_left + EDGE:
                        sb.setValue(max(0, vp_left - SCROLL_SPEED))
                    elif x > vp_right - EDGE:
                        sb.setValue(min(sb.maximum(), vp_left + SCROLL_SPEED))
            return

        if self._drag_mode == "marquee":
            sx, sy = self._marquee_start
            self._marquee_rect = QRect(min(sx, x), min(sy, y), abs(x - sx), abs(y - sy))
            # 选中框内片段
            sel = []
            for td in self._tracks:
                for clip in self._clips_of(td):
                    r = self._clip_rect(clip, td)
                    if self._marquee_rect.intersects(r):
                        sel.append((clip, td))
            self._marquee_selected = sel
            self.update()
            return

        if self._drag_clip and self._drag_track_desc:
            clip = self._drag_clip
            td = self._drag_track_desc
            # AudioClip 无 speed 属性，video 才有；统一 1.0
            speed = getattr(clip, "speed", 1.0)
            # 位移超过 2px 才算真实拖拽；单击选中（无位移）不标记，
            # 避免释放时把"单击"误存为无效撤回快照（幽灵条目）
            if abs(x - self._drag_start_x) > 2 or abs(y - self._drag_start_y) > 2:
                self._drag_modified = True

            if self._drag_mode == "trim_left":
                new_left_px = x - self._drag_pixel_offset
                new_left_sec = self._x_to_sec(new_left_px)
                # 吸附到所有轨道片段边界 + 播放头
                new_left_sec = self._snap_sec(new_left_sec, exclude_clip=clip)
                if td.kind == "subtitle":
                    clip.timeline_start = max(0.0, min(new_left_sec, clip.timeline_end - 0.1))
                else:
                    dt = new_left_sec - self._drag_clip_start0
                    new_trim = max(0.0, min(self._drag_trim_start0 + dt * speed, clip.trim_end - 0.1))
                    clip.trim_start = new_trim
                    clip.timeline_start = max(0.0, self._drag_clip_start0 + dt)
                    if dt < 0:
                        prev_end = 0.0
                        for other in self._clips_of(td):
                            if other is clip:
                                continue
                            o_end = other.timeline_end if hasattr(other, "timeline_end") else (other.timeline_start + other.duration)
                            if o_end <= clip.timeline_start and o_end > prev_end:
                                prev_end = o_end
                        if clip.timeline_start < prev_end:
                            clip.timeline_start = prev_end
                            clip.trim_start = self._drag_trim_start0 + (prev_end - self._drag_clip_start0) * speed
            elif self._drag_mode == "trim_right":
                new_right_px = x
                new_right_sec = self._x_to_sec(new_right_px)
                # 吸附到所有轨道片段边界 + 播放头
                new_right_sec = self._snap_sec(new_right_sec, exclude_clip=clip)
                if td.kind == "subtitle":
                    clip.timeline_end = max(clip.timeline_start + 0.1, new_right_sec)
                else:
                    dt = new_right_sec - (self._drag_clip_start0 + self._drag_clip_dur0)
                    new_trim = max(clip.trim_start + 0.1, min(self._drag_trim_end0 + dt * speed, clip.source_duration))
                    clip.trim_end = new_trim
                    clip.timeline_start = self._drag_clip_start0
                    # 防止右侧重叠到下一个片段
                    if dt > 0:
                        next_start = float("inf")
                        for other in self._clips_of(td):
                            if other is clip:
                                continue
                            o_start = other.timeline_start
                            if o_start > self._drag_clip_start0 and o_start < next_start:
                                next_start = o_start
                        if clip.timeline_end > next_start:
                            clip.trim_end = clip.trim_start + (next_start - clip.timeline_start) * speed
                            if clip.trim_end <= clip.trim_start:
                                clip.trim_end = clip.trim_start + 0.1
            elif self._drag_mode == "move":
                new_start_px = x - self._drag_pixel_offset
                raw_sec = max(0.0, self._x_to_sec(new_start_px))
                # 吸附到附近边界（视觉辅助，不锁定光标）
                snapped = self._snap_sec(raw_sec, exclude_clip=clip)
                if snapped != raw_sec:
                    clip.timeline_start = snapped
                else:
                    clip.timeline_start = raw_sec
                if td.kind == "subtitle":
                    clip.timeline_end = clip.timeline_start + (self._drag_sub_dur0 if self._drag_sub_dur0 else (clip.timeline_end - clip.timeline_start))
                # 字幕拖拽中推迟到右侧避免视觉重叠；视频/音频释放时自动堆叠
                if td.kind == "subtitle":
                    self._snap_out_of_overlap(clip, td)
                # 基于原始（未吸附）位置更新偏移，避免吸附后偏移漂移 → 片段落后于光标
                self._drag_pixel_offset = x - self._sec_to_x(raw_sec)
                # 跨轨提示（提前切轨：用距离中心+扩展命中区，轨道主动接住素材）
                self._drag_target_track = self._track_at_drag(y)
                target_td = self._drag_target_track
                if target_td and target_td.kind == td.kind and target_td.idx != td.idx:
                    self.setCursor(Qt.CursorShape.DragMoveCursor)
                else:
                    self.setCursor(Qt.CursorShape.SizeAllCursor)

            self.update()
            return

        # 悬停光标
        clip, td = self._clip_at(x, y)
        if clip is not None and clip is not self._hover_clip:
            self._hover_clip = clip
            self._hover_td = td
            self.update()
        elif clip is None and self._hover_clip is not None:
            self._hover_clip = None
            self._hover_td = None
            self.update()

        if clip is not None and td and td.kind != "subtitle":
            r = self._clip_rect(clip, td)
            edge_px = min(12, r.width() // 4)
            if x - r.left() < edge_px or r.right() - x < edge_px:
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                self.setCursor(Qt.CursorShape.SizeAllCursor)
        elif y >= RULER_H and abs(x - self._sec_to_x(self.playhead)) <= PLAYHEAD_GRAB_PX:
            self.setCursor(Qt.CursorShape.SplitHCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e: QMouseEvent):
        x, y = e.pos().x(), e.pos().y()

        # 框选结束
        if self._drag_mode == "marquee":
            if self._marquee_selected:
                self._marquee_active = True
                first_clip, first_td = self._marquee_selected[0]
                self._selected_clip = first_clip
                self._selected_td = first_td
                self._selected_track = first_td.kind
                self.selection_changed.emit(first_clip, first_td.kind, first_td.idx)
            else:
                self._marquee_active = False
                self._marquee_selected = []
            self._marquee_start = None
            self._marquee_rect = None
            self._drag_mode = None
            self.update()
            return

        # 播放头拖拽结束
        if self._drag_mode == "playhead":
            snapped = self._snap_to_keyframes(self.playhead)
            if snapped != self.playhead:
                self.playhead = snapped
                self.playhead_moved.emit(self.playhead)
            tw = getattr(self, 'parent_timeline', None)
            if tw and tw._preview_player:
                tw._preview_player.set_decode_state("playing" if tw._playing else "paused")
            self._drag_mode = None
            return

        # 创建字幕
        if self._drag_mode == "create_sub":
            end_sec = self._x_to_sec(x)
            start = min(self._creating_sub_start, end_sec)
            end = max(self._creating_sub_start, end_sec)
            if end - start > 0.2:
                blk = SubtitleBlock(timeline_start=start, timeline_end=end)
                self.tl.add_subtitle(blk)
                self._rebuild_tracks()         # 重建轨道布局（新轨/叠加轨可见）
                self._update_width()
                self._selected_clip = blk
                td = None
                for t in self._tracks:
                    if t.kind == "subtitle" and blk in self._clips_of(t):
                        td = t
                        break
                self._selected_td = td
                self._selected_track = "subtitle"
                self.selection_changed.emit(blk, "subtitle", td.idx if td else 0)
            self._drag_mode = None
            self.updateGeometry()  # 通知父级滚动区尺寸变更
            self.update()
            return

        # 跨轨道移动
        _cross_track_moved = False  # 标记是否显式跨轨（跳过后续自动堆叠）
        if self._drag_mode == "move" and self._drag_clip and self._drag_track_desc:
            src_td = self._drag_track_desc
            # 优先用拖拽中实时计算的目标轨（提前接住）；用 is not None 判定，
            # 避免任何"类 0 假值"误判（如未来 TrackDesc 带 falsy 字段）
            if self._drag_target_track is not None:
                dst_td = self._drag_target_track
            else:
                dst_td = self._track_at(y)

            if dst_td and dst_td.kind == src_td.kind and dst_td.idx != src_td.idx:
                clip = self._drag_clip
                # ── 检查目标轨是否有重叠 ──
                # 空轨道允许移动，有片段的轨不允许重叠
                dst_list = self._clips_of(dst_td)
                if dst_list:
                    # 目标轨非空：检查时间范围是否冲突
                    c_start = clip.timeline_start
                    c_end = clip.timeline_start + clip.duration
                    conflict = any(
                        (o.timeline_end if hasattr(o, "timeline_end") else (o.timeline_start + o.duration)) > c_start
                        and c_end > o.timeline_start
                        for o in dst_list
                    )
                    if conflict:
                        # 目标轨有空位才允许移过去；有重叠则拒绝，保持原位
                        self._drag_track_desc = src_td  # 不回弹到目标轨
                        self._rebuild_tracks()
                        self.update()
                        self._drag_mode = None
                        self._drag_clip = None
                        self._drag_track_desc = None
                        self._drag_pixel_offset = 0
                        self._drag_pre_snapshot = None
                        return
                src_list = self._clips_of(src_td)
                if clip in src_list:
                    src_list.remove(clip)
                if src_td.kind == "video":
                    while dst_td.idx >= len(self.tl.video_tracks):
                        self.tl.add_video_track()
                    self.tl.video_tracks[dst_td.idx].append(clip)
                elif src_td.kind == "audio":
                    while dst_td.idx >= len(self.tl.audio_tracks):
                        self.tl.add_audio_track()
                    self.tl.audio_tracks[dst_td.idx].append(clip)
                self._drag_modified = True
                self.tl.changed.emit()
                self._drag_track_desc = dst_td
                _cross_track_moved = True
            elif dst_td is None and src_td.kind in ("video", "audio"):
                if src_td.kind == "video":
                    new_idx = self.tl.add_video_track()
                else:
                    new_idx = self.tl.add_audio_track()
                clip = self._drag_clip
                src_list = self._clips_of(src_td)
                if clip in src_list:
                    src_list.remove(clip)
                if src_td.kind == "video":
                    self.tl.video_tracks[new_idx].append(clip)
                else:
                    self.tl.audio_tracks[new_idx].append(clip)
                self.tl.changed.emit()
                # 新建轨后重建 TrackDesc 列表，使 drag_track_desc 指向新轨
                self._rebuild_tracks()
                for td in self._tracks:
                    if td.kind == src_td.kind and td.idx == new_idx:
                        self._drag_track_desc = td
                        break
                _cross_track_moved = True

        # 存储 undo 快照（仅当本次拖拽确实改变了片段状态时才入栈，
        # 否则单击选中之类无操作的动作会污染撤回历史，导致 Ctrl+Z 需多次）
        if self._drag_mode in ("move", "trim_left", "trim_right") and self._drag_clip and self._drag_modified:
            if self._drag_mode == "move" and self._drag_track_desc:
                td = self._drag_track_desc
                clip = self._drag_clip
                # 释放时最终吸附：所有轨道的所有片段都吸附到边界（大阈值，精准对齐）
                snapped = self._snap_sec(clip.timeline_start, exclude_clip=clip, max_threshold=0.15)
                if snapped != clip.timeline_start:
                    clip.timeline_start = snapped
                # 主轨 auto_align 模式：关闭间隙
                if td.kind == "video" and td.idx == 0 and self.tl.auto_align:
                    self.tl.close_main_track_gaps(save_history=False)
                # 字幕：拖放后同轨防重叠（推迟时间）
                if td.kind == "subtitle":
                    self._snap_out_of_overlap(clip, td)
                # 视频/音频：自动向上堆叠到不重叠轨道
                # 但不干涉用户显式跨轨移动（叠1→主轨等），跨轨后由用户自行调整
                # 自动磁吸模式下主轨由 snapping 保证无重叠，不自动堆叠
                _auto_align_main = (self.tl.auto_align and td.kind == "video" and td.idx == 0)
                if td.kind in ("video", "audio") and not _cross_track_moved and not _auto_align_main:
                    new_td = self._auto_stack_to_higher_track(clip, td)
                    if new_td is not None:
                        self._drag_modified = True
                        self._drag_track_desc = new_td
                        td = new_td
            # 发射 trim 结束信号
            if self._drag_mode in ("trim_left", "trim_right"):
                if self._drag_track_desc and self._drag_track_desc.kind != "subtitle":
                    from core.edit_engine import rebase_clip_keyframes
                    rebase_clip_keyframes(self._drag_clip, self._drag_trim_start0, self._drag_trim_end0)
                self.clip_trimmed.emit(self._drag_clip)
            # 推入 undo：以拖拽前状态作为撤销点（已包含 _save_history 截断逻辑）
            if self._drag_pre_snapshot is not None:
                self.tl._history = self.tl._history[:self.tl._undo_index + 1]
                self.tl._history.append(self._drag_pre_snapshot)
                self.tl._undo_index = len(self.tl._history) - 1
                if len(self.tl._history) > 50:
                    self.tl._history.pop(0)
                    self.tl._undo_index -= 1
            self._drag_pre_snapshot = None

        self._drag_mode = None
        self._drag_clip = None
        self._drag_track_desc = None
        self._drag_target_track = None
        self._drag_orig_start = 0.0
        self._drag_orig_track = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def mouseDoubleClickEvent(self, e: QMouseEvent):
        x, y = e.pos().x(), e.pos().y()
        td = self._track_at(y)
        # 背景轨（idx==0）相邻片段接缝双击 → 弹出转场设置
        if td is not None and td.kind == "video" and td.idx == 0:
            sec = self._x_to_sec(x)
            tol = SEAM_TOL_PX / self.zoom
            clips = sorted(self._clips_of(td), key=lambda c: c.timeline_start)
            for i in range(len(clips) - 1):
                A = clips[i]; B = clips[i + 1]
                a_end = A.timeline_start + (A.trim_end - A.trim_start) / max(A.speed, 0.01)
                # 接缝处：B 紧接 A 末尾（容差内），且点击位置贴近 A 末尾
                if B.timeline_start <= a_end + tol and abs(sec - a_end) <= tol:
                    self.seam_double_clicked.emit(A, B)
                    return
        # 回退：普通 clip 双击
        clip, td2 = self._clip_at(x, y)
        if clip and td2:
            self.clip_double_clicked.emit(clip, td2.kind, td2.idx)

    def wheelEvent(self, e: QWheelEvent):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = e.angleDelta().y()
            # 乘法缩放：每次滚动 ×1.15 或 ÷1.15，保证大小zoom端步进都合理
            factor = 1.15 if delta > 0 else (1 / 1.15)
            self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, self.zoom * factor))
            # 缩放只重排布局，不触发缩略图重新生成（避免闪白）
            self._rebuild_tracks()
            self._update_width()
            self.update()
        else:
            super().wheelEvent(e)

    def keyPressEvent(self, e: QKeyEvent):
        if e.key() == Qt.Key.Key_Space:
            if hasattr(self, "parent_timeline"):
                self.parent_timeline.toggle_play()
            return
        elif e.key() == Qt.Key.Key_Delete or e.key() == Qt.Key.Key_Backspace:
            if hasattr(self, "parent_timeline"):
                # 画布内联编辑中不拦截 Delete/Backspace
                pw = getattr(self.parent_timeline, 'preview', None)
                if pw is not None and getattr(pw, '_editing_sub', None) is not None:
                    super().keyPressEvent(e)
                    return
                self.parent_timeline._do_delete()
            elif self._selected_clip:
                # fallback：无父控件时直接删除
                td = self._selected_td
                if td:
                    if td.kind == "video":
                        self.tl.remove_video_clip(self._selected_clip.id)
                    elif td.kind == "audio":
                        self.tl.remove_audio_clip(self._selected_clip.id)
                    elif td.kind == "subtitle":
                        self.tl.remove_subtitle(self._selected_clip.id)
                self._selected_clip = None
                self._selected_td = None
                self._selected_track = None
                self.selection_changed.emit(None, "", -1)
                self.update()
            return
        elif e.key() == Qt.Key.Key_S and self._selected_clip and self._selected_td:
            td = self._selected_td
            if td.kind == "video":
                self.tl.split_video_clip(self._selected_clip.id, self.playhead)
                # 预提取音频（后台线程），避免分割后新片段播放时无声
                pw = getattr(self, 'parent_timeline', None)
                if pw:
                    pv = getattr(pw, '_preview_player', None)
                    if pv:
                        try:
                            pv._ensure_audio_for_video(self._selected_clip.source_path)
                        except Exception:
                            pass
            elif td.kind == "audio":
                self.tl.split_audio_clip(self._selected_clip.id, self.playhead)
            elif td.kind == "subtitle":
                self.tl.split_subtitle(self._selected_clip.id, self.playhead)
            self._selected_clip = None
            self._selected_td = None
            self.selection_changed.emit(None, "", -1)
            self.update()
            return
        elif e.key() == Qt.Key.Key_Z and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.tl.undo()
            self._rebuild_tracks()
            self._update_width()
            self.update()
        super().keyPressEvent(e)

    def _move_playhead(self, x: int, emit: bool = True, snap_kf: bool = False):
        sec = max(0.0, self._x_to_sec(x))
        if snap_kf:
            sec = self._snap_to_keyframes(sec)
        self.playhead = sec
        if emit:
            self.playhead_moved.emit(sec)
        self.update()

    def _seek_and_continue_playing(self, sec: float):
        """播放中点击时间线任意位置：播放头跳到 sec 并从此处继续播放。

        视频帧立即 seek；音频停止旧播放并从 sec 重新播放（音画同步重置），
        保持 _playing 状态与 33ms 播放计时器运行，主时钟（音频）从 sec 起推进。
        """
        tw = getattr(self, 'parent_timeline', None)
        if tw is None:
            return
        self.playhead = sec
        self.playhead_moved.emit(sec)   # → _on_playhead_moved → preview.seek 更新视频帧
        self._drag_mode = None          # 视为一次点击（非拖拽），释放时无副作用
        self.update()
        if tw._preview_player:
            pp = tw._preview_player
            pp.stop_audio()
            pp.set_decode_state("playing")
            tw._sync_audio(sec)          # 音频从 sec 重新播放
            import time
            tw._last_tick = time.time()
            tw._audio_startup = True
            tw._audio_synced = False

    def _show_context_menu(self, x: int, y: int):
        # 右键弹出菜单前自动暂停播放
        if hasattr(self, 'parent_timeline') and self.parent_timeline and self.parent_timeline._playing:
            self.parent_timeline.toggle_play()
        clip, td = self._clip_at(x, y)
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background:#1e1e1e; color:#ccc; border:1px solid #3a3a3a; }
            QMenu::item { padding:5px 20px 5px 12px; }
            QMenu::item:selected { background:#2c5fa8; color:#fff; }
            QMenu::separator { height:1px; background:#333; margin:3px 0; }
        """)

        if clip and td:
            if td.kind == "video":
                act_sep = menu.addAction("🎵  分离人声")
                act_scene = menu.addAction("🎬  智能分镜（自动截断）")
                act_asr = menu.addAction("✂  AI 文字粗剪 / 语音识别")
                menu.addSeparator()
                act_freeze = menu.addAction("📸  定格帧 (3s)")
                act_extract = menu.addAction("🖼  提取当前帧到图层编辑")
                act_reverse = menu.addAction("🔄  倒放")
            elif td.kind == "audio":
                act_asr = menu.addAction("📝  语音识别")
            elif td.kind == "subtitle":
                act_edit = menu.addAction("✏  编辑字幕文本")
                act_polish = menu.addAction("✨  AI 润色")

        # ── 多选字幕直接朗读（不必打开语音台）──
        sel_subs = [c for c, t in getattr(self, "_marquee_selected", []) if t.kind == "subtitle"]
        if sel_subs:
            act_read_sel = menu.addAction(f"▶  朗读选中字幕 ({len(sel_subs)})")
        else:
            act_read_sel = None
        # 右键点在单条字幕上且它不在多选集合内 → 单条朗读
        if clip and td and td.kind == "subtitle" and clip not in sel_subs:
            act_read_one = menu.addAction("▶  朗读字幕")
        else:
            act_read_one = None

        act_kf_set = menu.addAction("🔷  在此设置关键帧")
        act_kf_clr = menu.addAction("❌  清除所有关键帧")
        menu.addSeparator()
        act_del = menu.addAction("🗑  删除")

        act = menu.exec(QCursor.pos())

        if not act:
            return

        # 朗读优先判断
        if sel_subs and act == act_read_sel:
            self._read_aloud(sel_subs)
            return
        if clip and td and td.kind == "subtitle" and act == act_read_one:
            self._read_aloud([clip])
            return

        if clip and td:
            if td.kind == "video":
                if act == act_sep:
                    self.ai_separate_requested.emit(clip)
                elif act == act_scene:
                    self.scene_detect_requested.emit(clip)
                elif act == act_asr:
                    self.ai_asr_requested.emit(clip)
                elif act == act_freeze:
                    self.freeze_requested.emit(clip, self.playhead)
                elif act == act_extract:
                    self.extract_frame_requested.emit(clip, self.playhead)
                elif act == act_reverse:
                    self.reverse_requested.emit(clip)
            elif td.kind == "audio":
                if act == act_asr:
                    self.ai_asr_requested.emit(clip)
            elif td.kind == "subtitle":
                if act == act_edit:
                    self.subtitle_edit_requested.emit(clip)
                elif act == act_polish:
                    self._menu_ai_polish(clip)

            if act == act_kf_set:
                self._menu_add_keyframe(clip)
            elif act == act_kf_clr:
                self.tl._save_history()  # 撤回
                setattr(clip, "keyframes", {})
                self.tl.changed.emit()
                self.update()
            elif act == act_del:
                if td.kind == "video":
                    self.tl.remove_video_clip(clip.id)
                elif td.kind == "audio":
                    self.tl.remove_audio_clip(clip.id)
                elif td.kind == "subtitle":
                    self.tl.remove_subtitle(clip.id)
                self._selected_clip = None
                self._selected_td = None
                self._selected_track = None
                self.selection_changed.emit(None, "", -1)
                self.update()
                # 强制刷新预览画面，清除被删除片段的残留帧/选中框
                pt = getattr(self, 'parent_timeline', None)
                if pt and pt._preview_player:
                    pt._preview_player.clear_video_selection()

    # ─── 多选字幕直接朗读（不落轨）───
    def _read_aloud(self, clips):
        """把选中字幕文本直接 TTS 朗读（不生成文件、不落轨、不进素材库）。

        复用配音面板当前引擎/音色/语速/音量配置。
        """
        if self._read_busy:
            return
        # 按时间排序，保证朗读顺序与画面一致
        items = [(getattr(c, "timeline_start", 0.0),
                  (getattr(c, "text", "") or "").strip()) for c in clips]
        items.sort(key=lambda x: x[0])
        texts = [t for _, t in items if t]
        if not texts:
            return
        full = "\n".join(texts)
        cfg = {}
        if hasattr(self, "parent_timeline") and self.parent_timeline is not None:
            try:
                cfg = self.parent_timeline.get_dubbing_config()
            except Exception:
                cfg = {}
        engine = cfg.get("engine", "edge")
        voice = cfg.get("voice", "")
        rate = cfg.get("rate", "+0%")
        volume = cfg.get("volume", 1.0)
        try:
            from ui.workers.tts_worker import TTSGenerationWorker
        except Exception:
            return
        self._read_busy = True
        self._read_worker = TTSGenerationWorker(
            text=full, voice=voice, rate=rate, engine_type=engine, volume=volume)
        self._read_worker.finished.connect(self._on_read_done)
        self._read_worker.error.connect(self._on_read_error)
        self._read_worker.start()

    def _on_read_done(self, path: str):
        self._read_busy = False
        if path and os.path.exists(path):
            self._read_player.setSource(QUrl.fromLocalFile(path))
            self._read_player.play()
        if self._read_worker is not None:
            self._read_worker.deleteLater()
            self._read_worker = None

    def _on_read_error(self, err: str):
        self._read_busy = False
        if self._read_worker is not None:
            self._read_worker.deleteLater()
            self._read_worker = None

    # ─── 字幕 AI 润色（右键菜单） ───
    def _ensure_polish_dialog(self):
        """复用同一个进度对话框（不确定进度，LLM 调用中）。"""
        dlg = getattr(self, "_polish_dlg", None)
        if dlg is None:
            from PyQt6.QtWidgets import QProgressDialog
            dlg = QProgressDialog(self)
            dlg.setWindowTitle("AI 润色")
            dlg.setRange(0, 0)          # 不确定进度
            dlg.setCancelButton(None)   # 调用中不可取消
            dlg.setMinimumDuration(0)
            dlg.setFixedSize(260, 96)
            dlg.setWindowModality(Qt.WindowModality.NonModal)  # 不阻塞时间线
            dlg.setStyleSheet(
                "QProgressDialog{background:#1e1e1e;color:#ddd;border:1px solid #333;"
                "border-radius:8px;} QLabel{color:#ddd;font-size:12px;}")
            self._polish_dlg = dlg
        return dlg

    def _polish_progress_show(self, n: int):
        dlg = self._ensure_polish_dialog()
        dlg.setLabelText(f"AI 润色中…（{n} 个任务进行中）" if n > 1 else "AI 润色中…")
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _polish_progress_hide(self):
        dlg = getattr(self, "_polish_dlg", None)
        if dlg is not None:
            dlg.reset()  # 隐藏并重置

    def _menu_ai_polish(self, clip):
        """对字幕块文本做 LLM 润色，结果作为新字幕块叠加在原字幕上方轨道。"""
        text = (getattr(clip, "text", "") or "").strip()
        if not text:
            return
        busy = getattr(self, "_polish_busy", None)
        if busy is None:
            busy = set()
            self._polish_busy = busy
        if clip.id in busy:
            return  # 该字幕正在润色中，防重复
        busy.add(clip.id)
        self._polish_progress_show(len(busy))

        from ui.dubbing_panel import _PolishThread
        th = _PolishThread(text)
        if not hasattr(self, "_polish_threads"):
            self._polish_threads = []
        self._polish_threads.append(th)

        def _cleanup():
            busy.discard(clip.id)
            if busy:
                self._polish_progress_show(len(busy))
            else:
                self._polish_progress_hide()
            try:
                self._polish_threads.remove(th)
            except ValueError:
                pass
            th.deleteLater()

        def _on_done(polished: str):
            _cleanup()
            self._apply_polish_result(clip, polished)

        def _on_failed(err: str):
            _cleanup()
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "AI 润色失败", str(err))

        th.done.connect(_on_done)
        th.failed.connect(_on_failed)
        th.start()

    def _apply_polish_result(self, orig, polished: str):
        """把润色结果作为新字幕块放到原字幕正上方（时间完全重叠）。"""
        polished = (polished or "").strip()
        if not polished:
            return
        import copy as _copy
        import uuid as _uuid
        blk = _copy.deepcopy(orig)
        blk.id = str(_uuid.uuid4())[:8]
        blk.text = polished
        blk.keyframes = {}
        blk.word_animation = False
        blk.word_timings = []
        blk.from_asr = False
        # 画布位置：在原字幕基础上上移一档，避免文字重叠遮挡
        pos_map = {'top': -0.85, 'center': 0.0, 'bottom': 0.85}
        base_px = orig.pos_x if getattr(orig, "pos_x", None) is not None else 0.0
        base_py = (orig.pos_y if getattr(orig, "pos_y", None) is not None
                   else pos_map.get(getattr(orig, "position", "bottom"), 0.85))
        blk.pos_x = base_px
        blk.pos_y = max(-1.0, base_py - 0.12)

        tl = self.tl
        tl._save_history()  # 撤回
        tracks = tl.subtitle_tracks
        orig_idx = next((i for i, t in enumerate(tracks) if orig in t), 0)
        # 找原字幕上方（索引更小 = 视觉更靠上）第一条无时间重叠的轨道
        chosen = -1
        for i in range(orig_idx - 1, -1, -1):
            conflict = any(b.timeline_start < blk.timeline_end
                           and b.timeline_end > blk.timeline_start
                           for b in tracks[i])
            if not conflict:
                chosen = i
                break
        if chosen >= 0:
            tracks[chosen].append(blk)
        else:
            # 上方无可用轨道 → 顶部新建一条
            from core.edit_engine import TrackInfo
            tracks.insert(0, [blk])
            tl.subtitle_track_info.insert(0, TrackInfo("字幕1"))
            for i, info in enumerate(tl.subtitle_track_info):
                if info and (not info.name or info.name.startswith("字幕")):
                    info.name = f"字幕{i+1}"
        tl.changed.emit()
        self.update()

    def _menu_add_keyframe(self, clip):
        try:
            self.tl._save_history()  # 撤回
            kfs = getattr(clip, "keyframes", None)
            if kfs is None or not isinstance(kfs, dict):
                kfs = {}
                setattr(clip, "keyframes", kfs)
            rel_t = max(0.0, self.playhead - clip.timeline_start)
            dur = getattr(clip, "duration", None)
            if dur is None or dur <= 0:
                dur = (clip.timeline_end - clip.timeline_start) if hasattr(clip, "timeline_end") and clip.timeline_end > clip.timeline_start else 1.0
            rel_t = min(rel_t, dur)

            props = []
            for p in ["scale", "pos_x", "pos_y", "rotation", "volume"]:
                val = getattr(clip, p, None)
                if val is not None:
                    props.append(p)
            for p in props:
                val = getattr(clip, p)
                if p not in kfs:
                    kfs[p] = []
                # 过滤掉时间相同的关键帧，用元组存储
                kfs[p] = [(float(t), float(v)) for t, v in kfs[p] if abs(t - rel_t) > 0.05]
                kfs[p].append((float(rel_t), float(val)))
            setattr(clip, "keyframes", kfs)
            # 关键帧变更只需重绘叠加层，不需要清空帧缓存+重新 seek（避免
            # 缓存冷导致的「拖拽轴锁住 / 画面空白」问题）。
            self.tl.overlays_changed.emit()
            self.update()
        except Exception as e:
            import traceback
            traceback.print_exc()

    def set_playhead(self, sec: float, playing: bool = False):
        self.playhead = sec
        if playing:
            cnt = getattr(self, "_play_paint_cnt", 0) + 1
            self._play_paint_cnt = cnt
            if cnt % 3 != 0:
                return
        self.update()

    # ─── 拖入素材 ───
    def dragEnterEvent(self, e):
        if e.mimeData().hasText() or e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if not (e.mimeData().hasText() or e.mimeData().hasUrls()):
            e.ignore()
            return
        e.acceptProposedAction()
        self._drag_over_track = self._track_at(int(e.position().y()))
        self._drag_over_x = int(e.position().x())
        self.update()

    def dragLeaveEvent(self, e):
        if self._drag_over_track is not None:
            self._drag_over_track = None
            self._drag_over_x = 0
            self.update()
        e.accept()

    def dropEvent(self, e):
        mime = e.mimeData()
        path = ""
        media_type = ""
        duration = 0.0

        if mime.hasText():
            parts = mime.text().split("||")
            if len(parts) == 3:
                path, media_type, duration = parts[0], parts[1], float(parts[2])

        if not path and mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    path = url.toLocalFile()
                    break
            if path:
                ext = os.path.splitext(path)[1].lower()
                video_exts = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".m4v", ".ts"}
                audio_exts = {".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".wma"}
                image_exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
                if ext in video_exts:
                    media_type = "video"
                elif ext in audio_exts:
                    media_type = "audio"
                elif ext in image_exts:
                    media_type = "image"
                else:
                    e.ignore()
                    return
                try:
                    from ui.media_library import _get_duration
                    duration = _get_duration(path, media_type)
                except Exception:
                    import traceback
                    traceback.print_exc()
                    duration = 0.0

        if not path or not media_type:
            e.ignore()
            return

        drop_x = e.position().x() if hasattr(e, "position") else e.pos().x()
        drop_y = e.position().y() if hasattr(e, "position") else e.pos().y()

        # 检测是否拖到视频片段上 → 替换
        if media_type == "video":
            target_clip, target_td = self._clip_at(int(drop_x), int(drop_y))
            if target_clip is not None and target_td is not None and target_td.kind == "video":
                self.replace_video_requested.emit(target_clip, path)
                self._drag_over_track = None
                self._drag_over_x = 0
                self.update()
                return

        td = self._track_at(int(drop_y))

        if media_type in ("video", "image"):
            track_kind = "video"
            if td and td.kind == "video":
                track_idx = td.idx
            else:
                track_idx = -1
        elif media_type == "audio":
            track_kind = "audio"
            if td and td.kind == "audio":
                track_idx = td.idx
            else:
                track_idx = -1
        else:
            e.ignore()
            return

        timeline_start = max(0.0, self._x_to_sec(int(drop_x)))

        self._drag_over_track = None
        self._drag_over_x = 0
        self.drop_media_requested.emit(path, media_type, duration, track_kind, track_idx, timeline_start)
        e.acceptProposedAction()


# ═══════════════════════════════════════════════════════════
#  TimelineWidget —— 外层容器（含工具栏+ScrollArea）
# ═══════════════════════════════════════════════════════════
class TimelineWidget(QWidget):
    playhead_moved      = pyqtSignal(float)
    selection_changed   = pyqtSignal(object, str, int)
    clip_double_clicked = pyqtSignal(object, str, int)
    ai_separate_requested = pyqtSignal(object)
    ai_asr_requested      = pyqtSignal(object)
    scene_detect_requested = pyqtSignal(object)
    subtitle_asr_requested = pyqtSignal(float, float)
    replace_video_requested = pyqtSignal(object, str)
    # 旧签名 (object, object, int) 已改为 (object, str)：clip + 可选文件路径
    freeze_requested       = pyqtSignal(object, float)
    extract_frame_requested = pyqtSignal(object, float)  # 视频片段 → 提取当前帧到图层编辑
    reverse_requested      = pyqtSignal(object)
    clip_trimmed           = pyqtSignal(object)
    drop_media_requested   = pyqtSignal(str, str, float, str, int, float)
    new_timeline_requested = pyqtSignal()
    scene_detect_selected_requested = pyqtSignal()
    text_rough_cut_requested = pyqtSignal()
    subtitle_edit_requested = pyqtSignal(object)  # 右键编辑字幕 → 内联编辑
    seam_double_clicked    = pyqtSignal(object, object)  # 背景轨相邻片段接缝双击 → (A_clip, B_clip)
    thumbs_regen_requested = pyqtSignal()   # 缩放导致缩略图张数变化，请求重新生成

    def __init__(self, timeline: EditTimeline, parent=None,
                 dubbing_config_provider=None):
        super().__init__(parent)
        self.tl = timeline
        self.dubbing_config_provider = dubbing_config_provider
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(33)
        self._play_timer.timeout.connect(self._tick_play)
        self._playing = False
        self._preview_player = None
        self._audio_synced = False
        self._audio_startup = False
        self._last_tick = 0.0
        self._last_bc_time = 0.0
        self._play_tick_cnt = 0
        # 音频时钟→时间线偏移：音频时钟追踪源文件本地时间，
        # 跨 clip 边界后需加偏移量映射回时间线绝对时间。详见 _sync_audio / _tick_play
        self._audio_timeline_offset = 0.0
        self._audio_offset_pending = False
        self._last_sync_time = 0.0
        self.fps = 30
        self._thumb_regen_timer = QTimer(self)
        self._thumb_regen_timer.setSingleShot(True)
        self._thumb_regen_timer.setInterval(300)
        self._thumb_regen_timer.timeout.connect(self.thumbs_regen_requested.emit)
        self._build_ui()
        self.tl.changed.connect(self._on_timeline_changed)

    def get_dubbing_config(self) -> dict:
        """取当前配音配置（引擎/音色/语速/音量），供轨道朗读复用。

        优先用 editor_tab 注入的 provider（即配音面板当前配置），
        否则回退到 edge 默认。
        """
        if callable(self.dubbing_config_provider):
            try:
                cfg = self.dubbing_config_provider()
                if isinstance(cfg, dict):
                    return cfg
            except Exception:
                pass
        return {"engine": "edge", "voice": "", "rate": "+0%", "volume": 1.0}

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 工具栏 ──
        toolbar = QWidget()
        toolbar.setFixedHeight(36)
        toolbar.setStyleSheet("background:#161616; border-bottom:1px solid #2a2a2a;")
        tb_lay = QHBoxLayout(toolbar)
        tb_lay.setContentsMargins(8, 0, 8, 0)
        tb_lay.setSpacing(4)

        btn_s = """
            QPushButton {
                background:#252525; color:#bbb; border:1px solid #3a3a3a;
                border-radius:3px; padding:3px 10px; font-size:12px;
            }
            QPushButton:hover { background:#333; color:#fff; border-color:#555; }
            QPushButton:pressed { background:#1a1a1a; }
        """

        self._btn_play = QPushButton("▶")
        self._btn_play.setFixedWidth(36)
        self._btn_play.setStyleSheet(btn_s.replace("color:#bbb", "color:#00eaff").replace(
            "color:#fff", "color:#00eaff"))
        self._btn_play.setToolTip("播放/暂停 (空格键)")
        self._btn_play.clicked.connect(self.toggle_play)

        btn_split = QPushButton("✂ 分割")
        btn_split.setStyleSheet(btn_s)
        btn_split.setToolTip("在播放头位置分割选中片段 (S键)")
        btn_split.clicked.connect(self._do_split)

        btn_del = QPushButton("🗑 删除")
        btn_del.setStyleSheet(btn_s)
        btn_del.clicked.connect(self._do_delete)
        btn_del.setToolTip("删除选中片段 (Del键)")

        btn_sub = QPushButton("+ 字幕")
        btn_sub.setStyleSheet(btn_s)
        btn_sub.setToolTip("在播放头位置添加字幕块")
        btn_sub.clicked.connect(self._add_subtitle_at_head)

        self._time_label = QLabel("00:00.00")
        self._time_label.setStyleSheet("color:#00eaff; font-size:13px; "
                                        "font-family:'Courier New'; min-width:80px;")

        zoom_label = QLabel("缩放")
        zoom_label.setStyleSheet("color:#666; font-size:11px;")
        self._zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self._zoom_slider.setRange(0, 100)
        self._zoom_slider.setValue(40)
        self._zoom_slider.setFixedWidth(120)
        self._zoom_slider.setStyleSheet("""
            QSlider::groove:horizontal { background:#2a2a2a; height:4px; border-radius:2px; }
            QSlider::handle:horizontal { background:#3d8ef8; width:12px; height:12px;
                margin:-4px 0; border-radius:6px; }
        """)
        self._zoom_slider.valueChanged.connect(self._on_zoom_changed)

        self._btn_align = QPushButton("🧲 自动磁吸")
        self._btn_align.setFixedHeight(24)
        self._btn_align.setCheckable(True)
        self._btn_align.setChecked(True)
        self._btn_align.setToolTip("开启后拖动片段自动吸附不留空隙")
        self._btn_align.setStyleSheet(
            "QPushButton{background:#1a2a3a;color:#3d8ef8;border:1px solid #3d8ef8;"
            "border-radius:3px;padding:2px 8px;font-size:11px;}"
            "QPushButton:hover{background:#1e3050;}"
            "QPushButton:checked{background:#1a2a3a;color:#3d8ef8;border:1px solid #3d8ef8;}"
            "QPushButton:!checked{background:#252525;color:#666;border:1px solid #3a3a3a;}")
        self._btn_align.clicked.connect(self._on_align_toggled)

        btn_new_tl = QPushButton("📋 新建时间线")
        btn_new_tl.setStyleSheet(btn_s)
        btn_new_tl.setToolTip("新建一条空白时间线")
        btn_new_tl.clicked.connect(self.new_timeline_requested.emit)

        btn_scene_detect = QPushButton("🎬 智能分镜")
        btn_scene_detect.setStyleSheet(
            "QPushButton{background:#1e2d32;color:#83c9d8;border:1px solid #315963;"
            "border-radius:3px;padding:3px 10px;font-size:12px;}"
            "QPushButton:hover{background:#274149;color:#a8e8f2;border-color:#4c8190;}"
            "QPushButton:pressed{background:#182529;}")
        btn_scene_detect.setToolTip("检测画面跳变并自动截开选中的视频片段")
        btn_scene_detect.clicked.connect(self.scene_detect_selected_requested.emit)

        btn_text_cut = QPushButton("✂ 文字粗剪")
        btn_text_cut.setStyleSheet(
            "QPushButton{background:#30251d;color:#e6a66f;border:1px solid #704523;"
            "border-radius:3px;padding:3px 10px;font-size:12px;}"
            "QPushButton:hover{background:#493121;color:#ffc28e;border-color:#a86731;}"
            "QPushButton:pressed{background:#251d17;}")
        btn_text_cut.setToolTip("选中视频，通过语音文字勾选需要保留的内容")
        btn_text_cut.clicked.connect(self.text_rough_cut_requested.emit)

        for w in [self._btn_play, btn_split, btn_del, btn_sub]:
            tb_lay.addWidget(w)
        tb_lay.addWidget(btn_new_tl)
        tb_lay.addWidget(btn_scene_detect)
        tb_lay.addWidget(btn_text_cut)
        tb_lay.addStretch()
        tb_lay.addWidget(self._btn_align)
        tb_lay.addSpacing(8)
        tb_lay.addWidget(self._time_label)
        tb_lay.addSpacing(12)
        tb_lay.addWidget(zoom_label)
        tb_lay.addWidget(self._zoom_slider)

        # ── ScrollArea + Canvas ──
        self._canvas = TimelineCanvas(self.tl, self)
        self._canvas.parent_timeline = self
        self._canvas._rebuild_tracks()
        self._canvas._update_width()

        self._canvas.playhead_moved.connect(
            lambda s: (self._time_label.setText(self._sec_to_timestr(s)), self.playhead_moved.emit(s)))
        self._canvas.selection_changed.connect(
            lambda c, k, i: self.selection_changed.emit(c, k, i))
        self._canvas.clip_double_clicked.connect(
            lambda c, k, i: self.clip_double_clicked.emit(c, k, i))
        self._canvas.seam_double_clicked.connect(
            lambda a, b: self.seam_double_clicked.emit(a, b))
        self._canvas.ai_separate_requested.connect(
            lambda c: self.ai_separate_requested.emit(c))
        self._canvas.ai_asr_requested.connect(
            lambda c: self.ai_asr_requested.emit(c))
        self._canvas.scene_detect_requested.connect(
            lambda c: self.scene_detect_requested.emit(c))
        self._canvas.replace_video_requested.connect(
            lambda c, p: self.replace_video_requested.emit(c, p))
        self._canvas.freeze_requested.connect(
            lambda c, s: self.freeze_requested.emit(c, s))
        self._canvas.extract_frame_requested.connect(
            lambda c, s: self.extract_frame_requested.emit(c, s))
        self._canvas.reverse_requested.connect(
            lambda c: self.reverse_requested.emit(c))
        self._canvas.clip_trimmed.connect(
            lambda c: self.clip_trimmed.emit(c))
        self._canvas.drop_media_requested.connect(
            lambda p, m, d, k, i, t: self.drop_media_requested.emit(p, m, d, k, i, t))
        self._canvas.subtitle_edit_requested.connect(
            lambda c: self.subtitle_edit_requested.emit(c))

        scroll = QScrollArea()
        scroll.setWidget(self._canvas)
        scroll.setWidgetResizable(False)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet("""
            QScrollArea { border:none; background:#1a1a1a; }
            QScrollBar:horizontal { background:#161616; height:10px; }
            QScrollBar::handle:horizontal { background:#3a3a3a; border-radius:4px; min-width:20px; }
            QScrollBar::handle:horizontal:hover { background:#555; }
            QScrollBar:vertical { background:#161616; width:10px; }
            QScrollBar::handle:vertical { background:#3a3a3a; border-radius:4px; min-height:20px; }
            QScrollBar::add-line, QScrollBar::sub-line { width:0; height:0; }
        """)
        self._scroll = scroll
        self._scroll.horizontalScrollBar().valueChanged.connect(self._on_scroll_changed)

        root.addWidget(toolbar)
        root.addWidget(scroll, 1)
        self.setMinimumHeight(200)

    def _on_timeline_changed(self):
        self._canvas._rebuild_tracks()
        self._canvas._update_width()
        self._canvas.update()

    def _on_align_toggled(self):
        self.tl.auto_align = self._btn_align.isChecked()
        if self.tl.auto_align:
            self.tl.close_main_track_gaps()
            self._canvas._rebuild_tracks()
            self._canvas._update_width()
            self._canvas.update()

    def _on_scroll_changed(self, val: int):
        sb = self._scroll.horizontalScrollBar()
        if sb.maximum() <= 0:
            return
        remaining = sb.maximum() - val
        if remaining < 200:
            self._canvas._timeline_dur += 300.0
            self._canvas._update_width()

    def _on_zoom_changed(self, val: int):
        # 指数映射：val=0 → zoom=MIN_ZOOM(2), val=100 → zoom=MAX_ZOOM(800)
        # 这样低缩放端（慢速/长视频）有更大调节范围
        import math
        ratio = val / 100.0
        z = MIN_ZOOM * (MAX_ZOOM / MIN_ZOOM) ** ratio  # 指数插值
        self._canvas.zoom = z
        # 缩放只重排布局，不触发缩略图重新生成（缩略图集合与缩放解耦，避免闪白）
        self._canvas._rebuild_tracks()
        self._canvas._update_width()
        self._canvas.update()

    def _sec_to_timestr(self, sec: float) -> str:
        m = int(sec // 60)
        s = int(sec % 60)
        f = int((sec % 1) * self.fps + 0.5)
        if f >= self.fps:
            f = 0
            s += 1
            if s >= 60:
                s = 0
                m += 1
        return f"{m:02d}:{s:02d}.{f:02d}"

    def set_playhead(self, sec: float):
        self._canvas.set_playhead(sec)
        self._time_label.setText(self._sec_to_timestr(sec))

    def get_playhead(self) -> float:
        return self._canvas.playhead

    def step_frame(self, direction: int):
        step = 1.0 / self.fps
        new_sec = max(0.0, self._canvas.playhead + direction * step)
        self._move_playhead(self._canvas._sec_to_x(new_sec))

    def _move_playhead(self, x: int):
        self._canvas._move_playhead(x)

    def _do_split(self):
        c = self._canvas._selected_clip
        td = self._canvas._selected_td
        if c and td:
            if td.kind == "video":
                self.tl.split_video_clip(c.id, self._canvas.playhead)
                if self.tl.auto_align and td.idx == 0:
                    self.tl.close_main_track_gaps(save_history=False)
                # 预提取音频，避免分割后新片段播放时无声
                pv = getattr(self, '_preview_player', None)
                if pv:
                    try:
                        pv._ensure_audio_for_video(c.source_path)
                    except Exception:
                        pass
            elif td.kind == "audio":
                self.tl.split_audio_clip(c.id, self._canvas.playhead)
            elif td.kind == "subtitle":
                self.tl.split_subtitle(c.id, self._canvas.playhead)
            self._canvas._selected_clip = None
            self._canvas._selected_td = None
            self.selection_changed.emit(None, "", -1)

    def _do_delete(self):
        canvas = self._canvas
        # 收集所有要删的：单选 + 框选
        to_delete = []
        if canvas._selected_clip and canvas._selected_td:
            to_delete.append((canvas._selected_clip, canvas._selected_td))
        if canvas._marquee_selected:
            for (clip, td) in canvas._marquee_selected:
                if (clip, td) not in to_delete:
                    to_delete.append((clip, td))

        if not to_delete:
            return

        for clip, td in to_delete:
            if td.kind == "video":
                self.tl.remove_video_clip(clip.id)
            elif td.kind == "audio":
                self.tl.remove_audio_clip(clip.id)
            elif td.kind == "subtitle":
                self.tl.remove_subtitle(clip.id)

        # 自动磁吸模式下删除后压实主轨空隙（与删除共用一个撤回快照）
        if self.tl.auto_align:
            self.tl.close_main_track_gaps(save_history=False)

        canvas._selected_clip = None
        canvas._selected_td = None
        canvas._marquee_selected = []
        canvas._marquee_active = False
        self.selection_changed.emit(None, "", -1)
        canvas.update()
        # 删除视频/音频后重同步音频（纯字幕删除不触发音频播放）
        has_media = any(td.kind in ("video", "audio") for _, td in to_delete)
        if has_media and self._preview_player:
            if self._playing:
                self._preview_player.play_all_audio(self._canvas.playhead)
            else:
                # 非播放状态：只停止被删除片段的音频，不启动任何新音频
                self._preview_player.stop_audio()
        # 强制刷新预览画面，清除被删除片段的残留帧/选中框
        if self._preview_player:
            self._preview_player.clear_video_selection()

    def _do_toggle_visibility(self):
        """V 键：切换选中片段的可见性"""
        canvas = self._canvas
        to_toggle = []
        if canvas._selected_clip and canvas._selected_td:
            to_toggle.append((canvas._selected_clip, canvas._selected_td))
        if canvas._marquee_selected:
            for clip, td in canvas._marquee_selected:
                if (clip, td) not in to_toggle:
                    to_toggle.append((clip, td))

        if not to_toggle:
            return

        self.tl._save_history()  # 撤回
        has_media = False
        affects_main_track = False
        for clip, td in to_toggle:
            clip.visible = not getattr(clip, "visible", True)
            if td.kind in ("video", "audio"):
                has_media = True
                if getattr(td, 'idx', -1) == 0:  # 切换主轨片段 → 需重同步 slot 0 音频
                    affects_main_track = True

        self.tl.changed.emit()
        canvas.update()

        # 隐藏/显示视频/音频片段后，重新同步音频并刷新预览帧
        if has_media and self._preview_player:
            if self._playing:
                cur_sec = canvas.playhead if hasattr(canvas, 'playhead') else 0.0
                if affects_main_track:  # 仅主轨可见性变化才重同步音频（slot 0 = 时钟源）
                    self._preview_player.play_all_audio(cur_sec)
                    import time
                    self._last_tick = time.time()
            else:
                self._preview_player.stop_audio()
            # 强制重新获取帧，确保隐藏/显示生效
            if hasattr(self._preview_player, '_async_fetch'):
                cur_sec = canvas.playhead if hasattr(canvas, 'playhead') else 0.0
                self._preview_player._async_fetch(cur_sec)

    def _add_subtitle_at_head(self):
        import traceback as _tb
        try:
            head = self._canvas.playhead
            dur = 3.0
            blk = SubtitleBlock(timeline_start=head, timeline_end=head + dur)
            self.tl.add_subtitle(blk, track_idx=-1)  # -1 = 自动找空闲轨
            self._canvas._rebuild_tracks()
            self._canvas.updateGeometry()
            self._canvas._selected_clip = blk
            td = None
            for t in self._canvas._tracks:
                if t.kind == "subtitle" and blk in self._clips_of(t):
                    td = t
                    break
            self._canvas._selected_td = td
            self._canvas._selected_track = "subtitle"
            self.selection_changed.emit(blk, "subtitle", td.idx if td else 0)
            self._canvas._update_width()
            self._canvas.setFocus()
        except Exception:
            print("[add_subtitle ERR]", _tb.format_exc(), file=__import__('sys').stderr)

    def _clips_of(self, td: TrackDesc):
        return self._canvas._clips_of(td)

    def toggle_play(self):
        import time
        if self._playing:
            self._play_timer.stop()
            self._playing = False
            self._audio_synced = False
            self._audio_startup = False
            self._audio_timeline_offset = 0.0
            self._audio_offset_pending = False
            self._btn_play.setText("▶")
            self._canvas._play_paint_cnt = 0
            self._play_tick_cnt = 0
            self._last_bc_time = 0
            if self._preview_player:
                self._preview_player.stop_audio()
                self._preview_player.set_playing(False)
                self._preview_player.set_decode_state("paused")
        else:
            self._last_tick = time.time()
            self._audio_startup = True
            self._playing = True
            self._audio_synced = False
            self._btn_play.setText("⏸")
            if self._preview_player:
                self._preview_player.set_playing(True)
                self._preview_player.set_decode_state("playing")
            self._sync_audio(self._canvas.playhead)
            from PyQt6.QtCore import QTimer as _QtTimer
            _QtTimer.singleShot(60, self._start_play_timer)

    # ── 公共 API ──
    def is_playing(self) -> bool:
        return self._playing

    def stop_playback(self):
        """停止播放并关闭音频"""
        self._play_timer.stop()
        self._playing = False
        self._audio_synced = False
        self._audio_startup = False
        self._audio_timeline_offset = 0.0
        self._audio_offset_pending = False
        self._btn_play.setText("▶")
        self._canvas._play_paint_cnt = 0
        self._play_tick_cnt = 0
        self._last_bc_time = 0
        if self._preview_player:
            self._preview_player.stop_audio()
            self._preview_player.set_playing(False)
            self._preview_player.set_decode_state("paused")

    def refresh_canvas(self):
        """刷新时间线画布（公共接口）"""
        self._canvas.update()

    def rebuild_canvas(self):
        """完整重建时间线布局（公共接口）"""
        self._canvas._rebuild_tracks()
        self._canvas._update_width()
        self._canvas.updateGeometry()
        self._canvas.update()

    def _start_play_timer(self):
        self._audio_startup = False
        if self._playing:
            import time
            self._last_bc_time = time.time()
            self._play_timer.start()

    def _tick_play(self):
        import time
        now = time.time()
        # 主时钟：优先音频时钟（ffplay 子进程反推），消除 wall-clock 漂移导致的卡顿；
        # 无音频（静音 / 纯图片时间线）时回退 wall-clock 累加。
        # 音频时钟追踪的是源文件本地时间；跨 clip 边界后需加 _audio_timeline_offset
        # 映射回时间线绝对时间，否则连续播放第二片段时会跳到 ~0.x 秒起。
        wall_delta = now - getattr(self, "_last_tick", now)
        if wall_delta > 0.1:
            wall_delta = 0
        master = self._preview_player.master_clock_sec() if self._preview_player else None
        if master is not None:
            if getattr(self, '_audio_offset_pending', False):
                # 用 wall-clock 流逝时间估算当前位置（上一 tick 后可能又过了几十毫秒）
                estimated = self._canvas.playhead + wall_delta
                self._audio_timeline_offset = estimated - master
                self._audio_offset_pending = False
            new_pos = master + getattr(self, '_audio_timeline_offset', 0.0)
            self._last_tick = now
        else:
            self._last_tick = now
            new_pos = self._canvas.playhead + wall_delta
        total = self.tl.total_duration
        if total <= 0 or new_pos >= total:
            new_pos = max(0.0, total)
            self._play_timer.stop()
            self._playing = False
            self._audio_synced = False
            self._audio_timeline_offset = 0.0
            self._audio_offset_pending = False
            self._btn_play.setText("▶")
            if self._preview_player:
                self._preview_player.stop_audio()
            self._canvas.set_playhead(new_pos)
            self._time_label.setText(self._sec_to_timestr(new_pos))
            return

        self._canvas.set_playhead(new_pos, playing=True)
        self._on_playhead_moved(new_pos)

        tc = getattr(self, "_play_tick_cnt", 0) + 1
        self._play_tick_cnt = tc
        # 每 3 tick (~100ms) 检查音频边界，跨越 clip 边界时重新同步
        if tc % 3 == 0:
            bc_delta = now - getattr(self, "_last_bc_time", now)
            self._last_bc_time = now
            self._check_audio_boundary(new_pos, bc_delta)
        if tc % 3 == 0:
            self._time_label.setText(self._sec_to_timestr(new_pos))

    def _on_playhead_moved(self, sec: float):
        self.playhead_moved.emit(sec)

    def _sync_audio(self, sec: float):
        import time
        if not self._preview_player:
            return
        self._preview_player.play_all_audio(sec)
        self._audio_synced = True
        self._last_sync_time = time.time()
        # 音频时钟是源文件本地时间；跨 clip 边界后需记录偏移映射回时间线绝对时间。
        # ffplay 子进程启动有延迟，若此时尚无音频时钟，延迟到 _tick_play 首帧取出后再算偏移。
        mc = self._preview_player.master_clock_sec()
        if mc is not None:
            self._audio_timeline_offset = sec - mc
            self._audio_offset_pending = False
        else:
            self._audio_boundary_sec = sec
            self._audio_offset_pending = True

    def _check_audio_boundary(self, sec: float, delta: float):
        prev_sec = sec - delta
        need_resync = False
        # 主轨（track 0）边界交叉 → 需要完整同步（音频时钟重对齐）
        for track in self.tl.video_tracks[:1]:
            for c in track:
                if prev_sec < c.timeline_start <= sec or prev_sec < c.timeline_end <= sec:
                    need_resync = True
                    break
        # 若上次同步的音频尚在启动中（_audio_offset_pending），跳过本次重同步，
        # 避免 ffplay 子进程未就绪就被重复启停 → 音频断流 / 时钟抖动。
        # 但最多等 300ms，超时后强制放行（无音频流时不至于永久屏蔽）。
        import time as _time
        if need_resync and getattr(self, '_audio_offset_pending', False):
            if _time.time() - getattr(self, '_last_sync_time', 0) < 0.3:
                need_resync = False
        # 叠加轨（track 1+）边界交叉 → 仅调 play_all_audio 启动/停止音频，
        # 不设 _audio_synced（不干扰主轨时钟），避免频繁触发→时钟跳
        overlay_boundary = False
        for track in self.tl.video_tracks[1:]:
            for c in track:
                if prev_sec < c.timeline_start <= sec or prev_sec < c.timeline_end <= sec:
                    overlay_boundary = True
                    break
            if overlay_boundary:
                break
        if not need_resync:
            for track in self.tl.audio_tracks:
                for c in track:
                    if prev_sec < c.timeline_start <= sec or prev_sec < c.timeline_end <= sec:
                        need_resync = True
                        break
        if need_resync:
            self._sync_audio(sec)
        elif overlay_boundary and self._preview_player:
            self._preview_player.play_overlay_audio(sec)
