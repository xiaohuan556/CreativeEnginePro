"""
media_library.py — 素材库面板（剪映风格卡片网格）
- 每个素材一张卡片：圆角 16:9 预览框 + 底色，大小统一
- 默认一行三个，固定间距，窗口缩放自适应（2~5 列）
- 右上角：素材时长（秒）
- 左上角：轨道状态角标（✓ 已添加 / 未添加）
- 底部：素材名（过长自动省略）
- 支持：拖拽加入时间线、双击预览、右键加入/移除、从文件管理器拖入导入
"""
from __future__ import annotations
import os
import re
import sys
import logging
from typing import Optional, Callable
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFileDialog,
    QAbstractItemView, QSizePolicy, QMenu, QApplication, QFrame,
    QGridLayout, QScrollArea,
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QMimeData, QPoint, QUrl, QThread
from PyQt6.QtGui import QIcon, QPixmap, QColor, QDrag, QImage, QFontMetrics, QPainter, QFont

from core.edit_engine import EditTimeline, VideoClip, AudioClip
import cv2

try:
    from config import THUMB_SIZE
except Exception:
    THUMB_SIZE = 320


VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".m4v", ".ts"}
AUDIO_EXTS = {".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".wma"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}

# 缩略图生成尺寸（16:9）
THUMB_W = 320
THUMB_H = 180
# 卡片类型底色（剪映风深浅区分）
TINT = {
    "video": "#1b2230",
    "audio": "#1f2a22",
    "image": "#26222e",
    "unknown": "#23262d",
}
ICON_EMOJI = {"video": "🎬", "audio": "🎵", "image": "🖼", "unknown": "📄"}


def _get_media_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in IMAGE_EXTS:
        return "image"
    return "unknown"


def _make_thumbnail(path: str, media_type: str, size=None) -> QPixmap:
    """生成 16:9 缩略图（在后台线程中调用）。size 为 None 时使用 config.THUMB_SIZE。"""
    if size is None:
        W = THUMB_SIZE
        H = int(THUMB_SIZE * 9 / 16)
    else:
        W, H = size
    try:
        if media_type == "video":
            cap = cv2.VideoCapture(path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(1, int(fps)))
            ret, frame = cap.read()
            cap.release()
            if ret:
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = frame_rgb.shape
                qimg = QImage(frame_rgb.data, w, h, ch * w,
                              QImage.Format.Format_RGB888).copy()
                return QPixmap.fromImage(qimg).scaled(
                    W, H, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
        elif media_type == "image":
            return QPixmap(path).scaled(
                W, H, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
    except Exception:
        pass
    return _make_placeholder(media_type, size)


def _make_placeholder(media_type: str, size=(THUMB_W, THUMB_H)) -> QPixmap:
    """生成占位图（带媒体类型图标 + 底色）"""
    W, H = size
    pix = QPixmap(W, H)
    pix.fill(QColor(TINT.get(media_type, "#23262d")))
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    icon = ICON_EMOJI.get(media_type, "📄")
    p.setPen(QColor("#5a6473"))
    font = QFont("Microsoft YaHei", max(28, H // 3))
    p.setFont(font)
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, icon)
    p.end()
    return pix


def _get_duration(path: str, media_type: str) -> float:
    """
    获取媒体时长（秒）。
    优先级：ffprobe（最准）→ ffmpeg -i 解析 stderr（ffmpeg.exe 必然存在）→ cv2 回退。
    项目缺少 ffprobe.exe 时，纯音频用 cv2 会返回 0，因此必须有 ffmpeg 回退。
    """
    # 1) ffprobe
    try:
        import subprocess, json
        from utils.ffmpeg_utils import get_ffmpeg_path
        ffprobe = get_ffmpeg_path().replace("ffmpeg.exe", "ffprobe.exe")
        if not os.path.exists(ffprobe):
            ffprobe = "ffprobe"
        r = subprocess.run(
            [ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True, timeout=15, encoding="utf-8", errors="ignore")
        if r.returncode == 0:
            info = json.loads(r.stdout)
            dur = float(info.get("format", {}).get("duration", 0) or 0)
            if dur > 0:
                return dur
    except Exception:
        pass
    # 2) ffmpeg -i 解析 stderr（ffmpeg.exe 必存在）
    try:
        import subprocess
        from utils.ffmpeg_utils import get_ffmpeg_path
        ffmpeg = get_ffmpeg_path()
        if os.path.exists(ffmpeg):
            r = subprocess.run([ffmpeg, "-i", path], capture_output=True,
                               timeout=15, encoding="utf-8", errors="ignore")
            out = (r.stderr or "") + (r.stdout or "")
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out)
            if m:
                h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
                dur = h * 3600 + mi * 60 + s
                if dur > 0:
                    return dur
    except Exception:
        pass
    # 3) cv2 回退
    try:
        if media_type in ("video", "audio"):
            cap = cv2.VideoCapture(path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            cap.release()
            return frames / fps if fps > 0 else 0.0
    except Exception:
        pass
    logging.warning("无法获取媒体时长: %s", path)
    return 0.0


class MediaItem:
    """素材元数据"""
    def __init__(self, path: str):
        self.path = path
        self.name = os.path.basename(path)
        self.media_type = _get_media_type(path)
        self._duration: Optional[float] = None
        self._duration_loaded = False
        self._thumbnail: Optional[QPixmap] = None

    @property
    def duration(self) -> float:
        if not self._duration_loaded:
            self._duration = _get_duration(self.path, self.media_type)
            self._duration_loaded = True
        return self._duration or 0.0

    @property
    def thumbnail(self) -> Optional[QPixmap]:
        return self._thumbnail

    @thumbnail.setter
    def thumbnail(self, value):
        self._thumbnail = value


# ─── 后台缩略图生成线程 ───
class _ThumbWorker(QThread):
    thumb_ready = pyqtSignal(str, QPixmap)

    def __init__(self, path: str, media_type: str, size=(THUMB_W, THUMB_H)):
        super().__init__()
        self._path = path
        self._media_type = media_type
        self._size = size

    def run(self):
        try:
            pix = _make_thumbnail(self._path, self._media_type, self._size)
            self.thumb_ready.emit(self._path, pix)
        except Exception:
            pass


class _MediaCard(QFrame):
    """单个素材卡片（剪映风格）"""

    def __init__(self, media: MediaItem, callbacks: dict, parent=None):
        super().__init__(parent)
        self._media = media
        self._cb = callbacks  # {add, remove, preview}
        self._on_track = False
        self._orig_pix: Optional[QPixmap] = None
        self._drag_start: Optional[QPoint] = None
        self._build_ui()
        self._apply_thumb(_make_placeholder(media.media_type))

    def _build_ui(self):
        self.setObjectName("mediaCard")
        self.setStyleSheet("""
            QFrame#mediaCard {
                background:#20242b; border:1px solid #2c313a; border-radius:8px;
            }
            QFrame#mediaCard:hover {
                border:1px solid #3d8ef8; background:#242a33;
            }
        """)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ── 预览区（16:9，含角标）──
        self._preview = QWidget()
        self._preview.setStyleSheet(
            "background:#15171c; border-top-left-radius:8px; border-top-right-radius:8px;")
        pg = QGridLayout(self._preview)
        pg.setContentsMargins(0, 0, 0, 0)
        pg.setSpacing(0)

        self._thumb_label = QLabel()
        self._thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_label.setStyleSheet("background:transparent;")
        pg.addWidget(self._thumb_label, 0, 0, 1, 3)

        # 左上角：轨道状态角标
        self._status_badge = QLabel("未添加")
        self._status_badge.setStyleSheet(
            "QLabel{ background:rgba(40,44,52,0.85); color:#9aa3b2;"
            " border-radius:4px; padding:1px 5px; font-size:10px; font-weight:500; }")
        pg.addWidget(self._status_badge, 0, 0,
                     alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        # 右上角：时长
        self._dur_badge = QLabel("")
        self._dur_badge.setStyleSheet(
            "QLabel{ background:rgba(0,0,0,0.6); color:#e6e6e6;"
            " border-radius:4px; padding:1px 5px; font-size:10px; }")
        pg.addWidget(self._dur_badge, 0, 2,
                     alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)

        lay.addWidget(self._preview, 1)

        # ── 底部：名称 ──
        self._name_label = QLabel()
        self._name_label.setFixedHeight(24)
        self._name_label.setWordWrap(False)
        self._name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._name_label.setStyleSheet(
            "color:#c9d1d9; font-size:11px; padding:0 7px;"
            " background:#1b1e24; border-bottom-left-radius:8px; border-bottom-right-radius:8px;")
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._name_label.setText(self._media.name)
        lay.addWidget(self._name_label)

        # 时长（懒加载一次）
        dur = self._media.duration
        self._dur_badge.setText(f"{dur:.1f}s" if dur > 0 else "")

    # ── 公开接口 ──
    def set_thumbnail(self, pix: QPixmap):
        self._apply_thumb(pix)

    def _apply_thumb(self, pix: QPixmap):
        self._orig_pix = pix
        self._scale_thumb()

    def set_on_track(self, on_track: bool):
        if self._on_track == on_track:
            return
        self._on_track = on_track
        if on_track:
            self._status_badge.setText("✓ 已添加")
            self._status_badge.setStyleSheet(
                "QLabel{ background:rgba(76,175,80,0.92); color:#fff;"
                " border-radius:4px; padding:1px 5px; font-size:10px; font-weight:600; }")
        else:
            self._status_badge.setText("未添加")
            self._status_badge.setStyleSheet(
                "QLabel{ background:rgba(40,44,52,0.85); color:#9aa3b2;"
                " border-radius:4px; padding:1px 5px; font-size:10px; font-weight:500; }")

    def _scale_thumb(self):
        if self._orig_pix is None or self._orig_pix.isNull():
            return
        w = self._preview.width()
        h = self._preview.height()
        if w <= 0 or h <= 0:
            return
        scaled = self._orig_pix.scaled(
            w, h, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self._thumb_label.setPixmap(scaled)

    def _elide_name(self):
        fm = QFontMetrics(self._name_label.font())
        avail = self._name_label.width() - 14
        if avail <= 0:
            return
        self._name_label.setText(fm.elidedText(self._media.name,
                                               Qt.TextElideMode.ElideRight, avail))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 16:9 预览高度
        ph = max(40, int(self.width() * THUMB_H / THUMB_W))
        self._preview.setFixedHeight(ph)
        self._scale_thumb()
        self._elide_name()

    # ── 交互 ──
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_start = e.pos()
            if self._cb.get("select"):
                self._cb["select"](self._media.path)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if (e.buttons() & Qt.MouseButton.LeftButton) and self._drag_start is not None:
            dist = (e.pos() - self._drag_start).manhattanLength()
            if dist >= QApplication.startDragDistance():
                self._start_drag()
                self._drag_start = None
                return
        super().mouseMoveEvent(e)

    def mouseDoubleClickEvent(self, e):
        if self._cb.get("preview"):
            self._cb["preview"](self._media.path, self._media.media_type)

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background:#252525; color:#ccc; border:1px solid #444; }
            QMenu::item:selected { background:#3d8ef8; color:#fff; }
        """)
        act_add = menu.addAction("加入时间线")
        act_del = menu.addAction("从素材库移除")
        act = menu.exec(self.mapToGlobal(e.pos()))
        if act == act_add and self._cb.get("add"):
            self._cb["add"](self._media.path, self._media.media_type, self._media.duration)
        elif act == act_del and self._cb.get("remove"):
            self._cb["remove"](self._media.path)

    def _start_drag(self):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(self._media.path)])
        mime.setText(f"{self._media.path}||{self._media.media_type}||{self._media.duration}")
        drag = QDrag(self)
        drag.setMimeData(mime)
        try:
            drag.exec(Qt.DropAction.CopyAction)
        except TypeError:
            pass


class _MediaScroll(QScrollArea):
    """支持自适应列数重排的滚动区"""
    reflow_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("""
            QScrollArea { background:#1e1e1e; border:none; }
            QScrollBar:vertical { background:#1a1c22; width:8px; }
            QScrollBar::handle:vertical { background:#3a3f4a; border-radius:4px; }
        """)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.reflow_requested.emit()


class MediaLibrary(QWidget):
    """素材库面板 — 剪映风格卡片网格"""
    add_to_timeline_requested = pyqtSignal(str, str, float)
    play_preview_requested = pyqtSignal(str, str)
    file_removed = pyqtSignal(str)

    COL_MIN_W = 110          # 单列最小宽度（决定列数）
    SPACING = 10             # 卡片间距（统一）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[MediaItem] = []
        self._item_map: dict[str, MediaItem] = {}
        self._cards: list[_MediaCard] = []
        self._card_map: dict[str, _MediaCard] = {}
        self._workers: list[_ThumbWorker] = []
        self.last_selected_path: str = ""
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 标题
        title = QLabel("素材库")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFixedHeight(28)
        title.setStyleSheet(
            "background:#1a1a1a; color:#aaa; font-size:12px; font-weight:500;"
            " border-bottom:1px solid #333;")
        root.addWidget(title)

        # 按钮行
        btn_bar = QWidget()
        btn_bar.setFixedHeight(34)
        btn_bar.setStyleSheet("background:#1e1e1e; border-bottom:1px solid #333;")
        btn_lay = QHBoxLayout(btn_bar)
        btn_lay.setContentsMargins(6, 4, 6, 4)
        btn_lay.setSpacing(4)
        btn_style = """
            QPushButton { background:#2a2a2a; color:#bbb; border:1px solid #444;
                border-radius:3px; padding:2px 8px; font-size:11px; }
            QPushButton:hover { background:#3a3a3a; color:#fff; }
        """
        btn_import = QPushButton("📁 导入素材")
        btn_import.setStyleSheet(btn_style)
        btn_import.clicked.connect(self._import_files)
        btn_lay.addWidget(btn_import)
        btn_lay.addStretch()
        root.addWidget(btn_bar)

        # 卡片网格滚动区
        self._scroll = _MediaScroll()
        self._grid_widget = QWidget()
        self._grid_widget.setStyleSheet("background:#1e1e1e;")
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setContentsMargins(self.SPACING, self.SPACING, self.SPACING, self.SPACING)
        self._grid.setHorizontalSpacing(self.SPACING)
        self._grid.setVerticalSpacing(8)   # 卡片上下留适当空隙
        self._scroll.setWidget(self._grid_widget)
        self._scroll.reflow_requested.connect(self._reflow)
        root.addWidget(self._scroll, 1)

        # 底部提示
        hint = QLabel("双击预览 · 拖入或拖出 · 右键管理")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setFixedHeight(20)
        hint.setStyleSheet("color:#555; font-size:10px; border-top:1px solid #2a2a2a;")
        root.addWidget(hint)

    # ─── 拖入导入（从文件管理器）───
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls()
                 if u.isLocalFile() and os.path.isfile(u.toLocalFile())]
        for p in paths:
            self._add_item(p)
        if paths:
            e.acceptProposedAction()
        else:
            e.ignore()

    # ─── 导入 ───
    def _import_files(self):
        all_exts = []
        for exts in (VIDEO_EXTS, AUDIO_EXTS, IMAGE_EXTS):
            all_exts.extend(f"*{e}" for e in exts)
        files, _ = QFileDialog.getOpenFileNames(
            self, "导入素材",
            filter=f"所有媒体文件 ({' '.join(all_exts)})")
        for f in files:
            self._add_item(f)

    def add_file(self, path: str):
        self._add_item(path)

    def _norm_key(self, path: str) -> str:
        """归一化路径去重键：绝对路径 + Windows 大小写不敏感"""
        p = os.path.abspath(path)
        if sys.platform == "win32":
            return os.path.normcase(p)
        return p

    def _add_item(self, path: str):
        if not os.path.exists(path):
            return
        key = self._norm_key(path)
        if key in self._item_map:
            return

        media = MediaItem(path)
        self._items.append(media)
        self._item_map[key] = media

        cb = {
            "add": lambda p, t, d: self.add_to_timeline_requested.emit(p, t, d),
            "remove": self._remove_media,
            "preview": lambda p, t: self.play_preview_requested.emit(p, t),
            "select": self._on_select,
        }
        card = _MediaCard(media, cb)
        self._cards.append(card)
        self._card_map[key] = card
        self._reflow()
        self._start_thumb_worker(media, key)

    def _on_select(self, path: str):
        self.last_selected_path = path

    def _remove_media(self, path: str):
        key = self._norm_key(path)
        media = self._item_map.get(key)
        if media is None:
            return
        card = self._card_map.pop(key, None)
        if card is not None:
            self._grid.removeWidget(card)
            card.deleteLater()
            self._cards = [c for c in self._cards if c is not card]
        self._items = [m for m in self._items if self._norm_key(m.path) != key]
        self._item_map.pop(key, None)
        self.file_removed.emit(path)
        self._reflow()

    def _start_thumb_worker(self, media: MediaItem, key: str = ""):
        if media.media_type == "image":
            try:
                pix = _make_thumbnail(media.path, media.media_type)
                media.thumbnail = pix
                card = self._card_map.get(key or self._norm_key(media.path))
                if card:
                    card.set_thumbnail(pix)
                return
            except Exception:
                pass
        worker = _ThumbWorker(media.path, media.media_type)
        worker.thumb_ready.connect(self._on_thumb_ready)
        worker.finished.connect(
            lambda w=worker: self._workers.remove(w) if w in self._workers else None)
        self._workers.append(worker)
        worker.start()

    def _on_thumb_ready(self, path: str, pix: QPixmap):
        key = self._norm_key(path)
        media = self._item_map.get(key)
        if media:
            media.thumbnail = pix
        card = self._card_map.get(key)
        if card:
            card.set_thumbnail(pix)

    # ─── 列重排 ───
    COL_MAX_W = 150           # 单列最大宽度（避免宽屏下卡片过大，"不大不小"）

    def _compute_cols(self) -> int:
        # 用户要求：默认一行三个
        return 3

    def _reflow(self):
        if not self._cards:
            return
        cols = self._compute_cols()
        vw = self._scroll.viewport().width()
        if vw <= 0:
            vw = self.width()
        # 卡片目标宽度：在视口内均分 cols 列，并限制为"不大不小"
        # max_fit：保证 3 列 + 间距 + 边距不超出视口（卡片绝不溢出框）
        max_fit = (vw - (cols + 1) * self.SPACING) // cols
        card_w = max(48, min(max_fit, self.COL_MAX_W))
        # 左上角对齐：左右边距统一为 SPACING，行从最左开始排列，不居中
        self._grid.setContentsMargins(self.SPACING, self.SPACING, self.SPACING, self.SPACING)
        # 清除旧行拉伸：否则富余垂直空间会被平均塞进各行间距，导致行距过大、顶部留白
        for r in range(self._grid.rowCount()):
            self._grid.setRowStretch(r, 0)
        # 移除旧 widgets，重置列宽/stretch（防止长文件名把卡片 sizeHint 撑宽出框）
        for c in self._cards:
            self._grid.removeWidget(c)
        for c in range(self._grid.columnCount()):
            self._grid.setColumnStretch(c, 0)
            self._grid.setColumnMinimumWidth(c, 0)
        # 所有列等 stretch=1：富余水平空间平均进各列宽（而非塞进列间距/整体居中），
        # 卡片始终沿最左列排列（导入单个素材也贴左上，不会跑到中间）
        for c in range(cols):
            self._grid.setColumnStretch(c, 1)
        for i, card in enumerate(self._cards):
            r = i // cols
            c = i % cols
            self._grid.addWidget(card, r, c)
            self._grid.setColumnMinimumWidth(c, card_w)
            # 锁死卡片宽高：宽度均分，高度 = 16:9 预览高 + 名称栏（24px）。
            # 不锁总高时 QGridLayout 会把 scroll 区富余垂直空间分配给行 → 卡片被拉成长条。
            card_w_h = int(card_w * THUMB_H / THUMB_W)
            card.setFixedWidth(card_w)
            card.setFixedHeight(card_w_h + 24)
        # 把滚动区富余垂直空间全部吸入底部额外行，
        # 卡片因此紧凑靠上、行间仅留 verticalSpacing，不再上下撑开
        last_row = (len(self._cards) - 1) // cols
        self._grid.setRowStretch(last_row + 1, 1)

    # ─── 轨道状态角标 ───
    def mark_on_track(self, path: str, on_track: bool):
        card = self._card_map.get(self._norm_key(path))
        if card:
            card.set_on_track(on_track)

    def refresh_statuses(self, on_track_paths: set):
        """批量刷新所有卡片的轨道状态角标"""
        normal_on_track = {self._norm_key(p) for p in on_track_paths}
        for key, card in self._card_map.items():
            card.set_on_track(key in normal_on_track)

    def get_paths(self) -> list:
        return [item.path for item in self._items]

    def clear_and_load_paths(self, paths: list):
        # 清空
        for c in self._cards:
            self._grid.removeWidget(c)
            c.deleteLater()
        self._cards.clear()
        self._card_map.clear()
        self._items.clear()
        self._item_map.clear()
        for p in paths:
            if os.path.exists(p):
                self._add_item(p)
