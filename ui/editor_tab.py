"""
editor_tab.py — 剪辑Tab主布局（仿剪映风格）
布局：
  顶部工具栏（项目名 + 右上角"导出"按钮）
  ├─ 左：素材库
  ├─ 中：预览播放器（支持画布比例切换）
  └─ 右：属性面板（位置/缩放/旋转/速度/音量/字体）
  底部：多轨时间线

AI工具（分离人声/语音识别）集成在视频轨右键菜单
字幕识别结果通过独立弹窗管理
"""
from __future__ import annotations
import os
import re
import logging
import traceback
import shutil
import subprocess
import uuid
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QPushButton,
    QLabel, QFileDialog, QProgressBar, QComboBox, QDialog,
    QMessageBox, QSizePolicy, QFrame, QFormLayout, QInputDialog,
    QDialogButtonBox, QScrollArea, QTableWidget, QTableWidgetItem,
    QHeaderView, QDoubleSpinBox, QLineEdit,
    QStackedWidget, QGroupBox, QTextEdit, QSpinBox, QTabWidget,
    QColorDialog, QApplication, QCheckBox, QSlider,
    QProgressDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QTimer
from PyQt6.QtGui import QColor, QFont, QShortcut, QKeySequence

from core.edit_engine import (
    EditTimeline, VideoClip, AudioClip, SubtitleBlock, TrackInfo,
    rebase_clip_keyframes,
    FFmpegDirectExportWorker,
)
from ui.export_dialogs import ExportDialog, AudioExportDialog, _ProgressDialog, _AudioExportWorker
from ui.timeline_widget import TimelineWidget
from ui.preview_player import PreviewPlayer
from ui.clip_properties import ClipPropertiesPanel
from ui.media_library import MediaLibrary
from ui.download_panel import DownloadPanel
from ui.scrape_panel import ScrapePanel
from ui.openverse_panel import OpenversePanel
from ui.widgets import CheckMarkBox
from ui.scene_detect_dialog import SceneDetectDialog

# ══ 模块常量 ══
PROP_DEBOUNCE_MS = 30       # 属性滑块 debounce
SEEK_DEBOUNCE_MS = 100      # 手动 seek 音频 debounce
THUMB_STRIP_W = 60          # 缩略图条宽度
THUMB_STRIP_H = 34          # 缩略图条高度
MAX_THUMB_WORKERS = 2       # 同时运行的缩略图线程上限（防止卡顿）
DEFAULT_IMAGE_DURATION = 5.0 # 图片默认时长（秒）
FFMPEG_TIMEOUT_SHORT = 60   # FFmpeg 短操作超时（秒）
FFMPEG_TIMEOUT_MEDIUM = 120 # FFmpeg 中等操作超时（秒）
FFMPEG_TIMEOUT_LONG = 300   # FFmpeg 长操作超时（秒）
# 自动保存（学习剪映：随时保存）
AUTOSAVE_DEBOUNCE_MS = 1200  # 改动后防抖落盘（毫秒）
AUTOSAVE_INTERVAL_MS = 90000 # 后台定时兜底（每 90 秒）
TAB_STYLE = (
    "QPushButton{background:#1a1a1a;color:#666;border:none;"
    "border-radius:3px;padding:2px 10px;font-size:11px;}"
    "QPushButton:hover{background:#252525;color:#aaa;}"
    "QPushButton:checked{background:#2a5fa8;color:#fff;}"
)
TAB_DRAG_STYLE = (
    "QPushButton{background:#3d8ef8;color:#fff;border:none;"
    "border-radius:3px;padding:2px 10px;font-size:11px;}"
)


# ══ AI Workers（复用原有逻辑）══
class ThumbnailWorker(QThread):
    """后台生成视频缩略图 — 单次 ffmpeg fps 滤镜一次抽完（比逐帧 N 次 seek 快 10x+）
    
    缩略图数量由调用方根据 clip 像素宽度和缩放计算（而非固定 3-15 张）。
    输出 2x 目标尺寸（显示时 GPU 缩小 → 更锐利）。
    磁盘缓存到 Cache/ 目录，第二次打开工程直接读取。"""
    finished = pyqtSignal(object, list)  # clip, list of QPixmap

    def __init__(self, clip, count: int, thumb_h: int = 36):
        super().__init__()
        self._clip = clip
        self._count = count
        self._thumb_h = thumb_h  # 显示高度，实际生成 2x
        trim_start = getattr(clip, 'trim_start', 0.0)
        trim_end = getattr(clip, 'trim_end', getattr(clip, 'source_duration', 1.0))
        self._trim_start = trim_start
        self._trim_end = trim_end

    @staticmethod
    def cache_path(source_path: str, trim_start: float, trim_end: float,
                   count: int, thumb_h: int) -> str:
        """磁盘缓存路径：Cache/thumbnails/{hash}.idx"""
        import hashlib
        key = f"{source_path}|{trim_start:.3f}|{trim_end:.3f}|{count}|{thumb_h}"
        h = hashlib.md5(key.encode()).hexdigest()
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "Cache", "thumbnails", f"{h}")

    def run(self):
        import subprocess, os, tempfile, glob
        from PyQt6.QtGui import QPixmap
        from utils.ffmpeg_utils import get_ffmpeg_path

        try:
            # 静态图片没有持续的视频帧流，不能按视频用 fps 滤镜抽帧。
            # 直接读取一次并复用，时间线绘制时会按片段宽度平铺。
            image_exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
            if Path(self._clip.source_path).suffix.lower() in image_exts:
                px = QPixmap(self._clip.source_path)
                if not px.isNull():
                    self.finished.emit(self._clip, [px])
                else:
                    self.finished.emit(self._clip, [])
                return

            ffmpeg = get_ffmpeg_path()
            if not ffmpeg or not os.path.exists(ffmpeg):
                self.finished.emit(self._clip, [])
                return

            trim_dur = max(0.1, self._trim_end - self._trim_start)
            count = max(2, min(300, self._count))
            # 抽帧间隔：首帧=trim_start，末帧=trim_end，中间均匀分布
            interval = trim_dur / (count - 1)
            # 输出 3x 高度用于锐利缩放（track_h=52, display≈48px, gen=144px）
            out_h = max(20, self._thumb_h * 3)

            # 磁盘缓存
            cache_file = self.cache_path(
                self._clip.source_path, self._trim_start, self._trim_end,
                count, self._thumb_h)
            if os.path.exists(cache_file + ".ready"):
                # 缓存命中 → 直接读
                thumbnails = []
                for i in range(count):
                    jpg = f"{cache_file}_{i:04d}.jpg"
                    if os.path.exists(jpg):
                        px = QPixmap(jpg)
                        if not px.isNull():
                            thumbnails.append(px)
                if thumbnails:
                    self.finished.emit(self._clip, thumbnails)
                    return

            # 准备输出目录
            out_dir = cache_file + "_tmp"
            os.makedirs(out_dir, exist_ok=True)

            # 单次 ffmpeg：fps 滤镜 + scale，输出编号 JPG
            cmd = [
                ffmpeg, "-ss", f"{self._trim_start:.3f}",
                "-i", self._clip.source_path,
                "-t", f"{trim_dur:.3f}",
                "-vf", f"fps=1/{interval:.4f}:start_time=0:round=near,scale=-1:{out_h}",
                "-q:v", "3", "-vsync", "vfr",
                "-y",
                os.path.join(out_dir, "thumb_%04d.jpg"),
            ]
            subprocess.run(
                cmd, capture_output=True, timeout=max(60, int(trim_dur * 2)),
                stdin=subprocess.DEVNULL,
            )

            # 读取结果
            jpgs = sorted(glob.glob(os.path.join(out_dir, "thumb_*.jpg")))
            thumbnails = []
            for jpg in jpgs:
                px = QPixmap(jpg)
                if not px.isNull():
                    thumbnails.append(px)

            # 写入磁盘缓存
            cache_dir = os.path.dirname(cache_file)
            os.makedirs(cache_dir, exist_ok=True)
            for i, px in enumerate(thumbnails):
                dst = f"{cache_file}_{i:04d}.jpg"
                if not os.path.exists(dst):
                    src = jpgs[i] if i < len(jpgs) else None
                    if src and os.path.exists(src):
                        import shutil
                        shutil.copy2(src, dst)
            # 标记缓存完成
            with open(cache_file + ".ready", "w") as f:
                f.write(str(count))

            # 清理临时目录
            import shutil
            shutil.rmtree(out_dir, ignore_errors=True)

            self.finished.emit(self._clip, thumbnails)

        except Exception:
            logging.debug("ThumbnailWorker failed", exc_info=True)
            self.finished.emit(self._clip, [])


class WaveformWorker(QThread):
    """后台把音频解码为低采样率单声道，并压缩成时间线绘制用峰值。"""
    finished = pyqtSignal(object, list)

    def __init__(self, clip, bins: int = 1600):
        super().__init__()
        self._clip = clip
        self._bins = max(200, min(4000, bins))

    def run(self):
        from array import array
        from utils.ffmpeg_utils import get_ffmpeg_path
        try:
            ffmpeg = get_ffmpeg_path()
            if not ffmpeg or not os.path.exists(ffmpeg):
                self.finished.emit(self._clip, [])
                return
            cmd = [
                ffmpeg, "-v", "error", "-i", self._clip.source_path,
                "-map", "0:a:0", "-ac", "1", "-ar", "8000",
                "-f", "s16le", "pipe:1",
            ]
            result = subprocess.run(
                cmd, capture_output=True, timeout=FFMPEG_TIMEOUT_MEDIUM,
                stdin=subprocess.DEVNULL,
            )
            samples = array("h")
            samples.frombytes(result.stdout)
            if not samples:
                self.finished.emit(self._clip, [])
                return
            step = max(1, len(samples) // self._bins)
            peaks = []
            for start in range(0, len(samples), step):
                block = samples[start:start + step]
                peaks.append(min(1.0, max(abs(v) for v in block) / 32768.0))
                if len(peaks) >= self._bins:
                    break
            self.finished.emit(self._clip, peaks)
        except Exception:
            logging.debug("WaveformWorker failed", exc_info=True)
            self.finished.emit(self._clip, [])


class _SepWorker(QThread):
    progress = pyqtSignal(int, str)  # pct, msg
    finished = pyqtSignal(str, str)
    error    = pyqtSignal(str)
    def __init__(self, path: str):
        super().__init__(); self._path = path
    def run(self):
        try:
            from core.separator import AudioSeparator
            self.progress.emit(5, "提取音轨…")
            sep = AudioSeparator()
            full_audio = sep.extract_audio(Path(self._path))
            self.progress.emit(40, f"分离人声/伴奏 ({sep.engine_name})…")
            v, b = sep.separate_vocals(full_audio)
            full_audio.unlink(missing_ok=True)
            self.progress.emit(95, "整理文件…")
            self.finished.emit(str(v), str(b))
        except Exception as e:
            import traceback
            self.error.emit(f"{e}\n{traceback.format_exc()}")


class _ASRWorker(QThread):
    progress = pyqtSignal(int, str)  # pct, msg
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)
    def __init__(self, audio_path: str, src_lang: str = ""):
        super().__init__(); self._path = audio_path; self._lang = src_lang
    def run(self):
        try:
            self.progress.emit(10, "加载 Whisper 模型…")
            from core.whisper_runner import run_whisper_asr
            entries = run_whisper_asr(self._path, language=self._lang)
            self.progress.emit(90, "识别完成，整理字幕…")
            from core.transcriber import SRTEntry
            self.finished.emit([SRTEntry(i+1, e["start"], e["end"], e["text"])
                                 for i, e in enumerate(entries)])
        except Exception as e:
            import traceback
            self.error.emit(f"{e}\n{traceback.format_exc()}")


class _SceneDetectWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, clip: VideoClip, threshold: float,
                 min_length: float, filter_flashes: bool):
        super().__init__()
        self._clip = clip
        self._threshold = threshold
        self._min_length = min_length
        self._filter_flashes = filter_flashes

    def run(self):
        try:
            from core.scene_detector import detect_scene_changes
            from utils.ffmpeg_utils import get_ffmpeg_path
            self.progress.emit(8, "正在分析画面跳变…")
            cuts = detect_scene_changes(
                self._clip.source_path,
                ffmpeg_path=get_ffmpeg_path(),
                source_start=self._clip.trim_start,
                source_end=self._clip.trim_end,
                threshold=self._threshold,
                min_length=self._min_length,
                filter_flashes=self._filter_flashes,
                cancel_check=self.isInterruptionRequested,
            )
            self.progress.emit(95, "正在生成分镜片段…")
            self.finished.emit(cuts)
        except Exception as exc:
            self.error.emit(f"{exc}\n{traceback.format_exc()}")


class _SubTransWorker(QThread):
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)
    def __init__(self, entries, target_lang: str):
        super().__init__(); self._entries = entries; self._lang = target_lang
    def run(self):
        try:
            from core.transcriber import Translator
            self.finished.emit(Translator(target_lang=self._lang).translate(self._entries))
        except Exception as e:
            import traceback
            self.error.emit(f"{e}\n{traceback.format_exc()}")


# ══ 导出弹窗 ══
LANGUAGES = [
    ("中文", "zh"), ("英语", "en"), ("日语", "ja"), ("韩语", "ko"), ("泰语", "th"),
    ("越南语", "vi"), ("西语", "es"), ("葡语", "pt"), ("阿语", "ar"), ("印尼语", "id"),
]

CANVAS_RATIOS = {
    "16:9 (横屏)": (16, 9),
    "9:16 (竖屏)": (9, 16),
    "1:1 (方形)":  (1, 1),
    "4:3":         (4, 3),
    "21:9 (超宽)":  (21, 9),
    "自定义...":   None,
    "默认":        None,
}

# ══ 字幕管理弹窗 ══
class SubtitleManagerDialog(QDialog):
    sync_to_timeline = pyqtSignal(list)  # list of {start, end, text}
    rough_cut_requested = pyqtSignal(list, float, bool, bool)
    preview_requested = pyqtSignal(float)

    def __init__(self, orig_subs: list, trans_subs: list, parent=None,
                 rough_cut_enabled: bool = False):
        super().__init__(parent)
        self.setWindowTitle("AI 文字粗剪 / 字幕管理" if rough_cut_enabled else "字幕管理")
        self.resize(780, 620 if rough_cut_enabled else 500)
        self.setStyleSheet("""
            QDialog { background:#1a1a1a; color:#ccc; }
            QLabel { color:#ccc; }
            QTableWidget { background:#111; border:1px solid #222; color:#ccc;
                           gridline-color:#1e1e1e; font-size:12px; }
            QTableWidget::item { padding:4px 6px; }
            QTableWidget::item:selected { background:#1a3050; color:#7fb8f5; }
            QHeaderView::section { background:#161616; color:#666; border:none;
                                   border-bottom:1px solid #222; font-size:11px; padding:4px; }
            QPushButton { border-radius:3px; padding:5px 14px; font-size:12px; }
        """)
        self._orig = orig_subs
        self._trans = trans_subs
        self._show_orig = True
        self._rough_cut_enabled = rough_cut_enabled
        for sub in self._orig:
            sub.setdefault("_keep", True)
        for sub in self._trans:
            sub.setdefault("_keep", True)
        self._build()
        self._fill_table()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)

        # 头部
        top = QHBoxLayout()
        lbl = QLabel("字幕列表")
        lbl.setStyleSheet("color:#aaa; font-size:13px; font-weight:600;")
        top.addWidget(lbl)
        top.addStretch()

        for text, flag in [("原文", True), ("译文", False)]:
            b = QPushButton(text)
            b.setCheckable(True)
            b.setChecked(flag == True)
            b.setStyleSheet(_TAB_BTN)
            b.clicked.connect(lambda _, f=flag, btn=b: self._switch_view(f))
            setattr(self, f"_btn_{'orig' if flag else 'trans'}", b)
            top.addWidget(b)
        lay.addLayout(top)

        # 翻译工具行
        trans_row = QHBoxLayout()
        trans_lbl = QLabel("翻译到：")
        trans_lbl.setStyleSheet("color:#888; font-size:11px;")
        trans_row.addWidget(trans_lbl)
        self._lang_btns: dict = {}
        for label, code in LANGUAGES[:5]:
            b = QPushButton(label)
            b.setFixedSize(44, 22)
            b.setStyleSheet(_LANG_OFF)
            b.clicked.connect(lambda _, c=code: self._request_translate(c))
            self._lang_btns[code] = b
            trans_row.addWidget(b)
        b_custom = QPushButton("自定义")
        b_custom.setFixedSize(44, 22)
        b_custom.setStyleSheet(_LANG_OFF)
        b_custom.clicked.connect(self._custom_translate)
        trans_row.addWidget(b_custom)
        trans_row.addStretch()
        lay.addLayout(trans_row)

        if self._rough_cut_enabled:
            cut_tools = QHBoxLayout()
            cut_tools.setSpacing(6)
            for text, slot in [
                ("全选", lambda: self._set_all(True)),
                ("全不选", lambda: self._set_all(False)),
                ("反选", self._invert_selection),
                ("去口癖", self._remove_fillers),
                ("智能选重点", self._smart_highlights),
            ]:
                button = QPushButton(text)
                button.setStyleSheet(
                    "QPushButton{background:#252525;color:#aaa;border:1px solid #3a3a3a;}"
                    "QPushButton:hover{background:#333;color:#fff;}")
                button.clicked.connect(slot)
                cut_tools.addWidget(button)
            cut_tools.addStretch()
            cut_tools.addWidget(QLabel("目标时长"))
            self._target_seconds = QDoubleSpinBox()
            self._target_seconds.setRange(3, 3600)
            self._target_seconds.setValue(30)
            self._target_seconds.setSuffix(" 秒")
            self._target_seconds.setFixedWidth(92)
            cut_tools.addWidget(self._target_seconds)
            lay.addLayout(cut_tools)

            cut_options = QHBoxLayout()
            cut_options.addWidget(QLabel("句子边缘保留"))
            self._cut_padding = QDoubleSpinBox()
            self._cut_padding.setRange(0, 1.5)
            self._cut_padding.setSingleStep(0.05)
            self._cut_padding.setValue(0.15)
            self._cut_padding.setSuffix(" 秒")
            self._cut_padding.setFixedWidth(90)
            cut_options.addWidget(self._cut_padding)
            self._compact_cut = CheckMarkBox("删除未选内容并自动压紧")
            self._compact_cut.setChecked(True)
            cut_options.addWidget(self._compact_cut)
            self._add_cut_subtitles = CheckMarkBox("粗剪后生成字幕")
            self._add_cut_subtitles.setChecked(True)
            cut_options.addWidget(self._add_cut_subtitles)
            cut_options.addStretch()
            lay.addLayout(cut_options)

        # 进度/状态
        self._ai_status = QLabel("")
        self._ai_status.setStyleSheet("color:#888; font-size:11px;")
        lay.addWidget(self._ai_status)

        # 表格
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["保留", "#", "时间", "文本"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(0, 48)
        self._table.setColumnWidth(1, 32)
        self._table.setColumnWidth(2, 135)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.cellChanged.connect(self._on_edit)
        self._table.cellClicked.connect(self._on_cell_clicked)
        self._table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        lay.addWidget(self._table, 1)

        self._selection_status = QLabel("")
        self._selection_status.setStyleSheet("color:#7f9fbd;font-size:11px;")
        lay.addWidget(self._selection_status)

        # 底部
        bot = QHBoxLayout()
        bot.addStretch()
        btn_sync = QPushButton("→ 加入时间线")
        btn_sync.setStyleSheet(
            "QPushButton{background:#2a5fa8;color:#fff;border:none;font-weight:bold;}"
            "QPushButton:hover{background:#3d8ef8;}")
        btn_sync.clicked.connect(self._do_sync)
        if self._rough_cut_enabled:
            btn_cut = QPushButton("✂ 按勾选内容粗剪")
            btn_cut.setStyleSheet(
                "QPushButton{background:#d06b28;color:#fff;border:none;font-weight:bold;}"
                "QPushButton:hover{background:#e77c35;}")
            btn_cut.clicked.connect(self._do_rough_cut)
        btn_close = QPushButton("关闭")
        btn_close.setStyleSheet(
            "QPushButton{background:#252525;color:#aaa;border:1px solid #444;}"
            "QPushButton:hover{background:#333;color:#fff;}")
        btn_close.clicked.connect(self.accept)
        bot.addWidget(btn_close)
        bot.addWidget(btn_sync)
        if self._rough_cut_enabled:
            bot.addWidget(btn_cut)
        lay.addLayout(bot)

        self._ai_worker = None
        self._target_lang = "en"

    def _switch_view(self, show_orig: bool):
        self._show_orig = show_orig
        self._btn_orig.setChecked(show_orig)
        self._btn_trans.setChecked(not show_orig)
        self._btn_orig.setStyleSheet(_TAB_BTN_ON if show_orig else _TAB_BTN)
        self._btn_trans.setStyleSheet(_TAB_BTN_ON if not show_orig else _TAB_BTN)
        self._fill_table()

    def _fill_table(self):
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        subs = self._orig if self._show_orig else self._trans
        for i, s in enumerate(subs):
            row = self._table.rowCount()
            self._table.insertRow(row)
            s.setdefault("_keep", True)
            keep = QTableWidgetItem("☑" if s["_keep"] else "☐")
            keep.setFlags(keep.flags() & ~Qt.ItemFlag.ItemIsEditable)
            keep.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            keep.setForeground(QColor("#64b5f6" if s["_keep"] else "#666666"))
            self._table.setItem(row, 0, keep)
            i0 = QTableWidgetItem(str(i+1))
            i0.setFlags(i0.flags() & ~Qt.ItemFlag.ItemIsEditable)
            i0.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 1, i0)
            i1 = QTableWidgetItem(f"{_fmt_s(s['start'])} → {_fmt_s(s['end'])}")
            i1.setFlags(i1.flags() & ~Qt.ItemFlag.ItemIsEditable)
            i1.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 2, i1)
            self._table.setItem(row, 3, QTableWidgetItem(s["text"]))
        self._table.blockSignals(False)
        self._update_selection_status()

    def _on_edit(self, row, col):
        if col != 3: return
        item = self._table.item(row, col)
        if not item: return
        subs = self._orig if self._show_orig else self._trans
        if 0 <= row < len(subs):
            subs[row]["text"] = item.text()

    def _on_cell_clicked(self, row: int, col: int):
        if col != 0:
            return
        subs = self._current_subs()
        if 0 <= row < len(subs):
            subs[row]["_keep"] = not subs[row].get("_keep", True)
            self._refresh_keep_cell(row)
            self._update_selection_status()

    def _on_cell_double_clicked(self, row: int, _col: int):
        subs = self._current_subs()
        if 0 <= row < len(subs):
            self.preview_requested.emit(float(subs[row].get("start", 0.0)))

    def _current_subs(self) -> list:
        return self._orig if self._show_orig else self._trans

    def _selected_subs(self) -> list:
        return [dict(s) for s in self._current_subs() if s.get("_keep", True)]

    def _refresh_keep_cell(self, row: int):
        subs = self._current_subs()
        if not (0 <= row < len(subs)):
            return
        item = self._table.item(row, 0)
        if item is not None:
            kept = subs[row].get("_keep", True)
            item.setText("☑" if kept else "☐")
            item.setForeground(QColor("#64b5f6" if kept else "#666666"))

    def _set_all(self, keep: bool):
        for sub in self._current_subs():
            sub["_keep"] = keep
        self._fill_table()

    def _invert_selection(self):
        for sub in self._current_subs():
            sub["_keep"] = not sub.get("_keep", True)
        self._fill_table()

    def _remove_fillers(self):
        from core.text_rough_cut import is_filler_sentence
        removed = 0
        for sub in self._current_subs():
            if sub.get("_keep", True) and is_filler_sentence(sub.get("text", "")):
                sub["_keep"] = False
                removed += 1
        self._fill_table()
        self._ai_status.setText(f"已取消 {removed} 条独立口癖/空句")

    def _smart_highlights(self):
        from core.text_rough_cut import choose_highlight_indices
        subs = self._current_subs()
        selected = set(choose_highlight_indices(subs, self._target_seconds.value()))
        for index, sub in enumerate(subs):
            sub["_keep"] = index in selected
        self._fill_table()
        self._ai_status.setText(f"已按 {self._target_seconds.value():.0f} 秒目标选出 {len(selected)} 条重点")

    def _update_selection_status(self):
        subs = self._current_subs()
        selected = [s for s in subs if s.get("_keep", True)]
        duration = sum(max(0.0, float(s.get("end", 0)) - float(s.get("start", 0)))
                       for s in selected)
        self._selection_status.setText(
            f"已保留 {len(selected)} / {len(subs)} 句，语句总时长约 {duration:.1f} 秒；双击句子可定位预览")

    def _request_translate(self, code: str):
        self._target_lang = code
        if not self._orig:
            self._ai_status.setText("请先完成语音识别"); return
        if self._ai_worker and self._ai_worker.isRunning():
            self._ai_status.setText("翻译中，请稍候…"); return
        from core.transcriber import SRTEntry
        entries = [SRTEntry(i+1, s["start"], s["end"], s["text"])
                   for i, s in enumerate(self._orig)]
        self._ai_status.setText(f"翻译到 {code}…")
        self._stop_ai_worker()  # 安全停止旧 Worker，防止 QThread::Destroyed 错误
        self._ai_worker = _SubTransWorker(entries, code)
        self._ai_worker.finished.connect(self._on_trans_done)
        self._ai_worker.error.connect(self._on_trans_error)
        self._ai_worker.start()

    def _custom_translate(self):
        lang, ok = QInputDialog.getText(self, "自定义语种", "目标语言（如：法语）：")
        if ok and lang.strip():
            self._request_translate(lang.strip())

    def _on_trans_done(self, entries):
        self._trans = []
        for index, entry in enumerate(entries):
            source = self._orig[index] if index < len(self._orig) else {}
            self._trans.append({
                "start": entry.start, "end": entry.end, "text": entry.text,
                "source_start": source.get("source_start"),
                "source_end": source.get("source_end"),
                "_keep": source.get("_keep", True),
            })
        self._show_orig = False
        self._fill_table()
        self._ai_status.setText(f"翻译完成 {len(entries)} 条 ✓")

    def _on_trans_error(self, e: str):
        self._ai_status.setText(f"翻译失败: {e[:80]}")

    def _do_sync(self):
        subs = self._selected_subs()
        if not subs:
            self._ai_status.setText("请至少勾选一条字幕")
            return
        self.sync_to_timeline.emit(subs)
        self._ai_status.setText(f"已同步 {len(subs)} 条到时间线 ✓")

    def _do_rough_cut(self):
        subs = self._selected_subs()
        if not subs:
            self._ai_status.setText("请至少勾选一句后再粗剪")
            return
        self.rough_cut_requested.emit(
            subs, self._cut_padding.value(), self._compact_cut.isChecked(),
            self._add_cut_subtitles.isChecked())

    def set_orig_subs(self, subs: list):
        self._orig = subs
        if self._show_orig:
            self._fill_table()


# ══ 主 Tab 控件 ══
class EditorTab(QWidget):
    status_msg = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        # ── 多时间线系统 ──
        tl0 = EditTimeline()
        self._timelines: list = [tl0]
        self._tl_widgets: list = []  # 在 _build_ui 中创建
        self._active_tl_idx: int = 0

        self._export_worker = None
        self._exporting = False  # 防止并发导出
        self._export_dialog: ExportDialog | None = None
        self._ai_worker = None
        self._orig_subs = []
        self._trans_subs = []
        self._vocals_path = ""
        self._bgm_path = ""
        self._separated_vocals: dict[str, str] = {}
        self._canvas_ratio = None  # 默认跟随视频尺寸
        self._custom_size = None   # (w, h) 用户自定义的实际像素尺寸
        self._project_path: str | None = None  # 当前工程文件路径（None=从未保存）
        self._project_name: str = "未命名工程"   # 用户自定义工程名称
        self._project_dirty: bool = False      # 是否有未保存的修改
        self._sub_dialog: SubtitleManagerDialog | None = None
        self._separated_state: dict = {}  # {clip_id: extracted_audio_path}, toggle Ctrl+Shift+S
        self._thumb_workers: list = []  # 保持引用防止 GC
        self._thumb_pending: list = []   # 缩略图待处理队列 (clip, dur)
        self._waveform_workers: list = []  # 音频波形后台任务

        # 属性面板 debounce：快速拖拽滑块时避免每 tick 都 seek，防止卡死闪退
        self._prop_debounce = QTimer(self)
        self._prop_debounce.setSingleShot(True)
        self._prop_debounce.setInterval(PROP_DEBOUNCE_MS)
        self._prop_debounce.timeout.connect(self._do_property_seek)

        # 手动 seek 音频 debounce：拖拽标尺时避免每像素都重启音频
        self._seek_audio_debounce = QTimer(self)
        self._seek_audio_debounce.setSingleShot(True)
        self._seek_audio_debounce.setInterval(SEEK_DEBOUNCE_MS)
        self._seek_audio_debounce.timeout.connect(self._do_seek_audio)
        self._seek_audio_sec = 0.0

        # 键盘快捷键（替代全局事件过滤器，避免拦截整个应用事件）
        self._setup_shortcuts()

        self._build_ui()
        self._connect_signals()
        # 初始化默认画布比例（None = 默认，跟随视频尺寸）
        self.preview.set_aspect_ratio(self._canvas_ratio)
        # 全局鼠标点击拦截：预览模式下任何区域点击都退出预览
        QApplication.instance().installEventFilter(self)

        # ── 自动保存（学习剪映：随时保存）──
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(AUTOSAVE_DEBOUNCE_MS)
        self._autosave_timer.timeout.connect(self._autosave)
        # 定时兜底：防止连续高频改动时防抖被反复重置而永不落盘
        self._autosave_fallback = QTimer(self)
        self._autosave_fallback.setInterval(AUTOSAVE_INTERVAL_MS)
        self._autosave_fallback.timeout.connect(self._autosave)
        self._autosave_fallback.start()
        # 启动后检测并提示恢复自动保存草稿（崩溃/异常退出保护）
        QTimer.singleShot(0, self._maybe_restore_autosave)

    @property
    def timeline(self) -> EditTimeline:
        return self._timelines[self._active_tl_idx]

    @property
    def timeline_widget(self) -> TimelineWidget:
        return self._tl_widgets[self._active_tl_idx]

    # ─────────────────────────────────────────
    # 构建 UI
    # ─────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ══ 顶部工具栏 ══
        root.addWidget(self._build_top_toolbar())

        # ══ AI 进度条（默认隐藏）══
        self._ai_progress = QProgressBar()
        self._ai_progress.setRange(0, 100)
        self._ai_progress.setValue(0)
        self._ai_progress.setFixedHeight(3)
        self._ai_progress.setTextVisible(False)
        self._ai_progress.setVisible(False)
        self._ai_progress.setStyleSheet(
            "QProgressBar{background:transparent;border:none;}"
            "QProgressBar::chunk{background:#3d8ef8;border-radius:2px;}")
        root.addWidget(self._ai_progress)

        # ══ AI 状态标签 ══
        self._ai_status_bar = QLabel("")
        self._ai_status_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ai_status_bar.setVisible(False)
        self._ai_status_bar.setStyleSheet(
            "color:#888;font-size:11px;padding:2px 0;")
        root.addWidget(self._ai_status_bar)

        # ══ 中间三段区 ══
        self._top_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._top_splitter.setStyleSheet("QSplitter::handle { background:#2a2a2a; width:3px; }")
        self._top_splitter.setOpaqueResize(True)
        self._top_splitter.setChildrenCollapsible(False)

        # 左：素材库 + 下载 + 扒取 + Openverse（QTabWidget）
        self.media_lib = MediaLibrary()
        self.media_lib.setMinimumWidth(150)
        self.download_panel = DownloadPanel()
        self.download_panel.setMinimumWidth(150)
        self.scrape_panel = ScrapePanel()
        self.scrape_panel.setMinimumWidth(150)
        self.openverse_panel = OpenversePanel()
        self.openverse_panel.setMinimumWidth(260)
        self._left_tabs = QTabWidget()
        self._left_tabs.setStyleSheet("""
            QTabWidget::pane { border:none; }
            QTabBar::tab {
                background:#1e1e1e; color:#888; border:none;
                padding:5px 10px; font-size:11px; min-width:50px;
            }
            QTabBar::tab:selected { color:#fff; border-bottom:2px solid #3d8ef8; }
            QTabBar::tab:hover { color:#ccc; }
        """)
        self._left_tabs.addTab(self.media_lib, "📁 素材库")
        self._left_tabs.addTab(self.download_panel, "📥 下载")
        self._left_tabs.addTab(self.scrape_panel, "📡 扒取")
        self._left_tabs.addTab(self.openverse_panel, "🎵 音乐音效")
        self._top_splitter.addWidget(self._left_tabs)

        # 中：预览播放器
        preview_wrap = self._build_preview_wrap()
        preview_wrap.setMinimumWidth(300)
        self._top_splitter.addWidget(preview_wrap)

        # 右：属性面板
        right_panel = self._build_right_panel()
        right_panel.setMinimumWidth(200)
        self._top_splitter.addWidget(right_panel)

        self._top_splitter.setSizes([280, 480, 260])

        # ══ 时间线标签栏 ══
        self._tl_tab_bar = QWidget()
        self._tl_tab_bar.setFixedHeight(28)
        self._tl_tab_bar.setStyleSheet("background:#111; border-bottom:1px solid #2a2a2a;")
        self._tl_tab_bar.setAcceptDrops(True)
        self._tl_tab_bar.installEventFilter(self)
        self._tl_tab_layout = QHBoxLayout(self._tl_tab_bar)
        self._tl_tab_layout.setContentsMargins(4, 0, 4, 0)
        self._tl_tab_layout.setSpacing(2)
        self._tl_tab_layout.addStretch()

        # 拖拽排序状态
        self._drag_btn: QPushButton | None = None
        self._drag_source_idx: int = -1
        self._drag_offset_x: float = 0
        self._drag_threshold: int = 5
        self._drag_started: bool = False

        # ══ 时间线堆叠 ══
        self._tl_stack = QStackedWidget()

        # 创建第一条时间线
        self._add_timeline(is_first=True)

        self._main_splitter = QSplitter(Qt.Orientation.Vertical)
        self._main_splitter.setStyleSheet("QSplitter::handle { background:#2a2a2a; height:4px; }")
        self._main_splitter.setOpaqueResize(True)
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.addWidget(self._top_splitter)
        self._main_splitter.addWidget(self._tl_tab_bar)
        self._main_splitter.addWidget(self._tl_stack)
        self._main_splitter.setSizes([350, 0, 220])

        root.addWidget(self._main_splitter, 1)

    def _build_top_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(40)
        bar.setStyleSheet("background:#141414; border-bottom:1px solid #2a2a2a;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(8)

        # 工程名称（可编辑）
        self._proj_name_label = QLabel("✂  未命名工程")
        self._proj_name_label.setStyleSheet(
            "color:#888; font-size:13px; font-weight:600; padding:2px 6px;"
            "border:1px solid transparent; border-radius:3px;")
        self._proj_name_label.setToolTip("双击修改工程名称")
        self._proj_name_label.mouseDoubleClickEvent = self._on_proj_name_dblclick
        lay.addWidget(self._proj_name_label)

        # 工程名称编辑框（隐藏，双击后显示）
        self._proj_name_edit = QLineEdit()
        self._proj_name_edit.setVisible(False)
        self._proj_name_edit.setStyleSheet(
            "QLineEdit{background:#1a1a1a;color:#ddd;border:1px solid #3d8ef8;"
            "border-radius:3px;padding:2px 6px;font-size:13px;font-weight:600;}")
        self._proj_name_edit.setMaxLength(100)
        self._proj_name_edit.editingFinished.connect(self._on_proj_name_done)
        lay.addWidget(self._proj_name_edit)

        # 💾 保存 / 📂 打开
        btn_save = QPushButton("💾 保存")
        btn_save.setFixedSize(100, 28)
        btn_save.setToolTip("保存工程 (Ctrl+S)")
        btn_save.setStyleSheet(
            "QPushButton{background:#252525;color:#aaa;border:1px solid #3a3a3a;"
            "border-radius:3px;padding:2px 8px;font-size:13px;}"
            "QPushButton:hover{background:#333;color:#fff;border-color:#555;}")
        btn_save.clicked.connect(self._save_project)
        lay.addWidget(btn_save)

        btn_load = QPushButton("📂 打开")
        btn_load.setFixedSize(100, 28)
        btn_load.setToolTip("打开工程 (Ctrl+O)")
        btn_load.setStyleSheet(
            "QPushButton{background:#252525;color:#aaa;border:1px solid #3a3a3a;"
            "border-radius:3px;padding:2px 8px;font-size:13px;}"
            "QPushButton:hover{background:#333;color:#fff;border-color:#555;}")
        btn_load.clicked.connect(self._load_project)
        lay.addWidget(btn_load)

        lay.addStretch()

        # 画布比例选择
        ratio_lbl = QLabel("画布")
        ratio_lbl.setStyleSheet("color:#666; font-size:11px;")
        lay.addWidget(ratio_lbl)
        self._ratio_combo = QComboBox()
        self._ratio_combo.addItems(list(CANVAS_RATIOS.keys()))
        self._ratio_combo.setCurrentText("默认")  # 默认跟随视频原始尺寸
        self._ratio_combo.setFixedWidth(120)
        self._ratio_combo.setStyleSheet(
            "QComboBox{background:#252525;color:#ccc;border:1px solid #3a3a3a;"
            "border-radius:3px;padding:3px 8px;font-size:11px;}"
            "QComboBox QAbstractItemView{background:#252525;color:#ccc;"
            "selection-background-color:#3d8ef8;}")
        self._ratio_combo.currentTextChanged.connect(self._on_ratio_changed)
        lay.addWidget(self._ratio_combo)

        lay.addSpacing(8)

        # 导出按钮
        self._btn_export = QPushButton("  导出")
        self._btn_export.setFixedSize(75, 28)
        self._btn_export.setStyleSheet(
            "QPushButton{background:#3d8ef8;color:#fff;border:none;"
            "border-radius:4px;padding:3px 16px;font-size:13px;font-weight:bold;}"
            "QPushButton:hover{background:#5aa0ff;}"
            "QPushButton:disabled{background:#333;color:#666;}")
        self._btn_export.clicked.connect(self._open_export_dialog)
        lay.addWidget(self._btn_export)

        return bar

    # ─── 工程名称编辑 ───
    def _on_proj_name_dblclick(self, event):
        """双击工程名称 → 进入编辑模式"""
        self._proj_name_label.setVisible(False)
        self._proj_name_edit.setText(self._project_name)
        self._proj_name_edit.setVisible(True)
        self._proj_name_edit.setFocus()
        # 延迟 selectAll，等焦点完全就绪后再选中
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._proj_name_edit.selectAll)

    def _on_proj_name_done(self):
        """编辑完成（回车或失焦） → 确认名称"""
        text = self._proj_name_edit.text().strip()
        if text != self._project_name:
            self._project_name = text if text else "未命名工程"
            self._mark_dirty()
            if text:
                self.status_msg.emit(f"工程已命名: {text}", "info")
        self._proj_name_edit.setVisible(False)
        self._update_proj_name_label()
        self._proj_name_label.setVisible(True)

    def _update_proj_name_label(self):
        """刷新工程名称标签文字和样式"""
        dirty_mark = " *" if self._project_dirty else ""
        self._proj_name_label.setText(f"✂  {self._project_name}{dirty_mark}")
        if self._project_dirty:
            self._proj_name_label.setStyleSheet(
                "color:#ffaa00; font-size:13px; font-weight:600; padding:2px 6px;"
                "border:1px solid transparent; border-radius:3px;")
        else:
            self._proj_name_label.setStyleSheet(
                "color:#888; font-size:13px; font-weight:600; padding:2px 6px;"
                "border:1px solid transparent; border-radius:3px;")

    def _mark_dirty(self):
        """标记工程为已修改 + 刷新名称标签 + 触发自动保存防抖"""
        if not self._project_dirty:
            self._project_dirty = True
            self._update_proj_name_label()
        # 即便已是 dirty 也重置计时，集中落盘，避免高频改动时反复写盘
        if getattr(self, '_autosave_timer', None):
            self._autosave_timer.start()

    def _build_preview_wrap(self) -> QWidget:
        """预览区：包含比例框和播放器"""
        w = QWidget()
        w.setStyleSheet("background:#0a0a0a;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.preview = PreviewPlayer(self.timeline)
        lay.addWidget(self.preview, 1)

        return w

    def _build_right_panel(self) -> QWidget:
        """右侧只保留剪辑属性；AI 生成统一回到制片画布。"""
        # 顶部小标签切换
        bar = QWidget()
        bar.setFixedHeight(28)
        bar.setStyleSheet("background:#141414; border-bottom:1px solid #2a2a2a;")
        blay = QHBoxLayout(bar)
        blay.setContentsMargins(6, 0, 6, 0)
        blay.setSpacing(4)

        self._right_tabs = QTabWidget()
        self._right_tabs.setStyleSheet("""
            QTabWidget::pane { border:none; }
            QTabBar::tab {
                background:#1e1e1e; color:#888; border:none;
                padding:4px 12px; font-size:11px; min-width:50px;
            }
            QTabBar::tab:selected { color:#fff; border-bottom:2px solid #3d8ef8; }
            QTabBar::tab:hover { color:#ccc; }
        """)
        self._right_tabs.setTabPosition(QTabWidget.TabPosition.North)
        self._right_tabs.setDocumentMode(True)

        # 页 0：属性面板（保留原 ClipPropertiesPanel 的滚动容器）
        props_scroll = QScrollArea()
        props_scroll.setWidgetResizable(True)
        props_scroll.setStyleSheet(
            "QScrollArea{background:#1a1a1a;border:none;}"
            "QScrollBar:vertical{background:#141414;width:6px;}"
            "QScrollBar::handle:vertical{background:#3a3a3a;border-radius:3px;}"
        )
        container = QWidget()
        container.setStyleSheet("background:#1a1a1a;")
        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.props_panel = ClipPropertiesPanel(
            self.timeline, add_audio_cb=self._dubbing_add_audio,
            get_subtitles_cb=self._get_selected_subtitles)
        lay.addWidget(self.props_panel, 1)
        lay.addStretch()
        props_scroll.setWidget(container)
        self._right_tabs.addTab(props_scroll, "📋 属性")

        # 栈容器（预留扩展位）
        self._right_stack = QStackedWidget()
        self._right_stack.addWidget(self._right_tabs)

        wrap = QWidget()
        wraplay = QVBoxLayout(wrap)
        wraplay.setContentsMargins(0, 0, 0, 0)
        wraplay.setSpacing(0)
        wraplay.addWidget(self._right_stack)
        return wrap

    # ── AI 助手入口 ──
    def open_ai_assistant(self, tab: str = "video", reference_image: str | None = None):
        """旧入口兼容：剪辑台不再承担 AI 生成。"""
        self.status_msg.emit("请在 AI 制片画布中使用视频节点生成", "info")

    def open_storyboard_video_generator(self, prompt: str, ratio: str, duration: int):
        """旧入口兼容：视频生成已统一由画布节点执行。"""
        self.status_msg.emit("视频生成已迁移到 AI 制片画布", "info")

    def import_storyboard(self, board: dict, audio_policy: str = "replace"):
        """把已定稿素材的分镜按时间顺序追加到当前时间线。

        每个镜头优先导入定稿视频；没有视频时才使用定稿图片。当前预览结果
        不参与导入，避免为了导入视频而破坏关键帧选择。存在外部 TTS 时，
        replace 会静音生成视频的混合原声；duck 会把它压到 12%。
        """
        def final_path(shot: dict) -> str:
            chosen = str(shot.get("selected_video_asset") or
                         shot.get("selected_image_asset") or
                         shot.get("anchor_frame_id") or "")
            if chosen:
                return chosen
            if ("selected_video_asset" in shot or
                    "selected_image_asset" in shot):
                return ""
            return str(shot.get("selected_asset") or "")

        shots = [s for s in (board or {}).get("shots", []) if final_path(s)]
        if not shots:
            return 0
        tl = self.timeline
        if not tl.video_tracks:
            tl.video_tracks = [[]]
            tl.video_track_info = [TrackInfo("主轨道")]
        base = max((c.timeline_end for c in tl.video_tracks[0]), default=0.0)
        first_start = min(float(s.get("start", 0.0)) for s in shots)
        imported = 0
        slowed = 0
        dialogue_track_idx = None

        def ensure_dialogue_track():
            nonlocal dialogue_track_idx
            if dialogue_track_idx is not None:
                return dialogue_track_idx
            for index, info in enumerate(tl.audio_track_info):
                if str(getattr(info, "name", "")) == "AI 对白":
                    dialogue_track_idx = index
                    return index
            if (len(tl.audio_tracks) == 1 and not tl.audio_tracks[0] and
                    tl.audio_track_info):
                tl.audio_track_info[0].name = "AI 对白"
                dialogue_track_idx = 0
            else:
                dialogue_track_idx = tl.add_audio_track()
                tl.audio_track_info[dialogue_track_idx].name = "AI 对白"
            return dialogue_track_idx

        for shot in sorted(shots, key=lambda s: float(s.get("start", 0.0))):
            path = final_path(shot)
            if not path or not os.path.exists(path):
                continue
            duration = max(0.5, float(shot.get("duration", 3.0)))
            start = base + float(shot.get("start", 0.0)) - first_start
            selected_meta = next(
                (asset for asset in shot.get("assets", [])
                 if isinstance(asset, dict) and asset.get("path") == path), {})
            asset_kind = str(selected_meta.get("kind") or shot.get("asset_type") or "image")
            performance = shot.get("performance") or {}
            pause_before = max(0.0, float(performance.get("pause_before", 0) or 0))
            dialogue_audio = str(shot.get("dialogue_audio") or "")
            has_dialogue_audio = bool(
                dialogue_audio and os.path.exists(dialogue_audio))
            source_duration = duration
            trim_start = 0.0
            trim_end = duration
            speed = 1.0
            if asset_kind == "video":
                trim_start = max(0.0, float(
                    shot.get("video_segment_offset") or 0.0))
                actual_duration = float(selected_meta.get("actual_duration", 0) or 0)
                if actual_duration <= 0:
                    try:
                        from ui.media_library import _get_duration
                        actual_duration = float(_get_duration(path, "video") or 0)
                    except Exception:
                        actual_duration = 0.0
                if actual_duration > 0:
                    source_duration = actual_duration
                    available = max(0.0, actual_duration - trim_start)
                    if available >= duration:
                        # 生成档位通常比目标镜头长：精确裁掉尾部。
                        trim_end = trim_start + duration
                    else:
                        # 服务偶尔返回略短视频：用轻微慢放补齐，时间线时长仍严格等于目标。
                        trim_end = actual_duration
                        speed = available / duration if available > 0 else 1.0
                        slowed += 1
            clip = VideoClip(
                source_path=path,
                source_duration=source_duration,
                trim_start=trim_start,
                trim_end=trim_end,
                timeline_start=start,
                speed=speed,
                volume=(0.12 if has_dialogue_audio and audio_policy == "duck" else 1.0),
                mute=(has_dialogue_audio and audio_policy != "duck"),
                has_alpha=(asset_kind == "video" and
                           self._detect_video_alpha(path)),
                out_transition=shot.get("transition") or None,
            )
            tl.add_video_clip(clip, track_idx=0, skip_overlap=True)
            self.media_lib.add_file(path)
            count, th = self._thumb_params(duration)
            self._start_thumbnail_worker(clip, count, th)
            voiceover = str(
                performance.get("dialogue") or shot.get("voiceover") or "").strip()
            if voiceover:
                subtitle_start = min(start + pause_before, start + duration - 0.1)
                tl.add_subtitle(SubtitleBlock(
                    text=voiceover,
                    timeline_start=subtitle_start,
                    timeline_end=start + duration,
                ))
            if has_dialogue_audio:
                try:
                    from ui.media_library import _get_duration
                    audio_duration = float(
                        _get_duration(dialogue_audio, "audio") or 0.0)
                except Exception:
                    audio_duration = 0.0
                if audio_duration > 0:
                    tl.add_audio_clip(AudioClip(
                        source_path=dialogue_audio,
                        source_duration=audio_duration,
                        trim_start=0.0,
                        trim_end=audio_duration,
                        timeline_start=min(
                            start + pause_before, start + duration - 0.1),
                        volume=1.0,
                        fade_in=0.03,
                        fade_out=0.08,
                        label="AI 对白",
                    ), track_idx=ensure_dialogue_track(), skip_overlap=True)
                    self.media_lib.add_file(dialogue_audio)
            imported += 1
        if imported:
            self._mark_dirty()
            note = f"；{slowed} 个短素材已轻微慢放补齐" if slowed else ""
            self.status_msg.emit(
                (f"AI 分镜已按目标秒数导入：{imported} 个镜头{note}；"
                 f"外部 TTS 已进入独立对白轨，VEO 原声"
                 f"{'压到 12%' if audio_policy == 'duck' else '已替换'} ✓"), "success")
        return imported

    def import_canvas_media(self, payload: dict):
        """把画布上的单个生成结果追加到当前剪辑时间线。"""
        path = str((payload or {}).get("path") or "")
        media_type = str((payload or {}).get("media_type") or "")
        if not path or not os.path.exists(path) or media_type not in ("image", "video", "audio"):
            return False
        try:
            from ui.media_library import _get_duration
            duration = float(_get_duration(path, media_type) or 0.0)
        except Exception:
            duration = 0.0
        self.media_lib.add_file(path)
        self._add_to_timeline(path, media_type, duration,
                              track_idx=-1, timeline_start=-1)
        self._mark_dirty()
        self.status_msg.emit(
            f"{payload.get('title') or Path(path).stem} 已送到剪辑台 ✓", "success")
        return True

    def _on_ai_video_to_timeline(self, path: str):
        """AI 视频 → 加入时间线。"""
        if not path or not os.path.exists(path):
            return
        duration = 0.0
        try:
            from utils.ffmpeg_utils import get_ffmpeg_path
            import re as _re
            ffmpeg = get_ffmpeg_path()
            probe = subprocess.run([ffmpeg, "-i", path], capture_output=True, text=True)
            m = _re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", probe.stderr or "")
            if m:
                h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
                duration = h * 3600 + mi * 60 + s
        except Exception:
            duration = 0.0
        self.media_lib.add_file(path)
        self._add_to_timeline(path, "video", duration, track_idx=-1, timeline_start=-1)
        self.status_msg.emit("AI 生成视频已添加到时间线 ✓", "success")

    def _on_ai_video_to_library(self, path: str):
        """AI 视频生成完成 → 直接加入素材库（不再显示生成结果页）。"""
        if not path or not os.path.exists(path):
            return
        self.media_lib.add_file(path)
        self.status_msg.emit("AI 生成视频已加入素材库 ✓", "success")

    # ─────────────────────────────────────────
    # 键盘快捷键
    # ─────────────────────────────────────────
    def _setup_shortcuts(self):
        """创建 QShortcut 替代全局事件过滤器，仅在本 Tab 可见时生效"""
        # 空格：播放/暂停
        sc = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc.activated.connect(self._on_shortcut_space)

        # Ctrl+Z：撤销
        sc = QShortcut(QKeySequence("Ctrl+Z"), self)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc.activated.connect(self._on_shortcut_undo)

        # Ctrl+Y / Ctrl+Shift+Z：重做
        sc = QShortcut(QKeySequence("Ctrl+Y"), self)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc.activated.connect(self._on_shortcut_redo)

        sc = QShortcut(QKeySequence("Ctrl+Shift+Z"), self)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc.activated.connect(self._on_shortcut_redo)

        # Ctrl+B：分割片段
        sc = QShortcut(QKeySequence("Ctrl+B"), self)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc.activated.connect(self._on_shortcut_split)

        # V：隐藏/显示选中片段
        sc = QShortcut(QKeySequence(Qt.Key.Key_V), self)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc.activated.connect(self._on_shortcut_toggle_visibility)

        # Ctrl+Shift+S：分离人声/BGM
        sc = QShortcut(QKeySequence("Ctrl+Shift+S"), self)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc.activated.connect(self._on_shortcut_separate)

        # Ctrl+Shift+M：导出
        sc = QShortcut(QKeySequence("Ctrl+Shift+M"), self)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc.activated.connect(self._on_shortcut_export)

        # Ctrl+S：保存工程
        sc = QShortcut(QKeySequence("Ctrl+S"), self)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc.activated.connect(self._on_shortcut_save)

        # Ctrl+O：打开工程
        sc = QShortcut(QKeySequence("Ctrl+O"), self)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc.activated.connect(self._on_shortcut_open)

    def _in_text_field(self) -> bool:
        """焦点是否在文本输入控件中"""
        from PyQt6.QtWidgets import QTextEdit, QLineEdit, QPlainTextEdit
        focused = QApplication.focusWidget()
        return isinstance(focused, (QTextEdit, QLineEdit, QPlainTextEdit))

    def _on_shortcut_space(self):
        if not self.isVisible():
            return
        editing_inline = getattr(self.preview, '_editing_sub', None) is not None
        if not self._in_text_field() and not editing_inline:
            self.timeline_widget.toggle_play()

    def _on_shortcut_undo(self):
        if not self.isVisible() or self._in_text_field():
            return
        self.timeline.undo()

    def _on_shortcut_redo(self):
        if not self.isVisible() or self._in_text_field():
            return
        self.timeline.redo()

    def _on_shortcut_split(self):
        if not self.isVisible() or self._in_text_field():
            return
        self.timeline_widget._do_split()

    def _on_shortcut_delete(self):
        if not self.isVisible() or self._in_text_field():
            return
        if getattr(self.preview, '_editing_sub', None) is not None:
            return
        sel_sub = getattr(self.preview, '_selected_sub', None)
        if sel_sub is not None:
            self.timeline.remove_subtitle(sel_sub.id)
            self.preview._selected_sub = None
            self.preview._sub_interaction = None
            self.preview._seq_state = None
            if hasattr(self.preview, '_current_sec'):
                self.preview._async_fetch(self.preview._current_sec)
            return
        self.timeline_widget._do_delete()

    def _on_shortcut_toggle_visibility(self):
        if not self.isVisible() or self._in_text_field():
            return
        self.timeline_widget._do_toggle_visibility()

    def _on_shortcut_separate(self):
        if not self.isVisible() or self._in_text_field():
            return
        self._do_separate_selected()

    def _on_shortcut_export(self):
        if not self.isVisible() or self._in_text_field():
            return
        self._open_export_dialog()

    def _on_shortcut_save(self):
        if not self.isVisible() or self._in_text_field():
            return
        self._save_project()

    def _on_shortcut_open(self):
        if not self.isVisible() or self._in_text_field():
            return
        self._load_project()

    # ─────────────────────────────────────────
    # 信号连接
    # ─────────────────────────────────────────
    def _connect_signals(self):
        self.media_lib.add_to_timeline_requested.connect(self._add_to_timeline)
        # 素材库双击 → 画布预览
        self.media_lib.play_preview_requested.connect(self._on_play_preview)
        # 素材库删除素材 → 同步删除时间线中的相关片段
        self.media_lib.file_removed.connect(self._on_file_removed)
        # 下载完成 → 自动加入素材库
        self.download_panel.download_finished.connect(self._on_download_finished)
        # Openverse 下载完成 → 按页面开关自动加入素材库
        self.openverse_panel.download_finished.connect(self._on_openverse_download_finished)
        # 扒取面板 → 批量下载 + 去重
        self.scrape_panel.download_requested.connect(self.download_panel.add_tasks)
        self.download_panel.url_downloaded.connect(self.scrape_panel.mark_downloaded)
        # 共享对象信号（preview / props_panel 在整个 Tab 生命周期内不变，只连一次）
        self.props_panel.property_changed.connect(self._on_property_changed)
        self.props_panel.seek_requested.connect(self._on_seek_requested)
        self.preview.video_selected.connect(self._on_preview_selection)
        self.preview.pause_requested.connect(self._on_pause_requested)
        self.preview.subtitle_pos_changed.connect(self._on_sub_pos_changed)
        # 对当前激活的时间线连接（tw 相关信号 + tl.changed）
        self._bind_timeline_signals()
        # 标签拖拽排序：仅拦截标签按钮事件（不再拦截全局QApplication事件）
        pass  # 每个按钮已在 _create_tab_button 中 installEventFilter(self)

    def _bind_timeline_signals(self):
        """绑定当前激活时间线的所有信号"""
        tw = self.timeline_widget
        tw.selection_changed.connect(self._on_selection_changed)
        tw.clip_double_clicked.connect(self._on_clip_double_clicked)
        tw.seam_double_clicked.connect(self._on_seam_double_clicked)
        tw.thumbs_regen_requested.connect(self._on_thumbs_regen_requested)
        tw.playhead_moved.connect(self._on_playhead_moved)
        tw.ai_separate_requested.connect(self._do_separate_clip)
        tw.ai_asr_requested.connect(self._do_asr_clip)
        tw.scene_detect_requested.connect(self._start_scene_detection)
        tw.replace_video_requested.connect(self._on_replace_video_requested)
        tw.drop_media_requested.connect(self._on_drop_media)
        tw.new_timeline_requested.connect(self._on_new_timeline)
        tw.scene_detect_selected_requested.connect(
            self._start_scene_detection_for_selection)
        tw.text_rough_cut_requested.connect(self._start_text_rough_cut)
        tw.freeze_requested.connect(self._on_freeze_frame)
        tw.extract_frame_requested.connect(self._on_extract_frame_to_image_editor)
        tw.reverse_requested.connect(self._on_reverse)
        tw.subtitle_edit_requested.connect(self._on_subtitle_edit_requested)
        tw.clip_trimmed.connect(self._on_clip_trimmed)
        # 注入 PreviewPlayer 到 TimelineWidget，使其可以控制音频
        tw._preview_player = self.preview
        # 每个时间线独有的 changed 信号（共享对象信号已在 _connect_signals 中一次性连接）
        self.timeline.changed.connect(self._on_timeline_changed)
        self.timeline.overlays_changed.connect(self._on_overlays_changed)

    def _unbind_timeline_signals(self, tw: TimelineWidget):
        """断开指定 TimelineWidget 的所有信号"""
        pairs = [
            (tw.selection_changed, self._on_selection_changed),
            (tw.clip_double_clicked, self._on_clip_double_clicked),
            (tw.seam_double_clicked, self._on_seam_double_clicked),
            (tw.thumbs_regen_requested, self._on_thumbs_regen_requested),
            (tw.playhead_moved, self._on_playhead_moved),
            (tw.ai_separate_requested, self._do_separate_clip),
            (tw.ai_asr_requested, self._do_asr_clip),
            (tw.scene_detect_requested, self._start_scene_detection),
            (tw.replace_video_requested, self._on_replace_video_requested),
            (tw.drop_media_requested, self._on_drop_media),
            (tw.new_timeline_requested, self._on_new_timeline),
            (tw.scene_detect_selected_requested,
             self._start_scene_detection_for_selection),
            (tw.text_rough_cut_requested, self._start_text_rough_cut),
            (tw.freeze_requested, self._on_freeze_frame),
            (tw.extract_frame_requested, self._on_extract_frame_to_image_editor),
            (tw.reverse_requested, self._on_reverse),
            (tw.subtitle_edit_requested, self._on_subtitle_edit_requested),
            (tw.clip_trimmed, self._on_clip_trimmed),
            # 必须断开旧时间线的 changed 信号，否则跨时间线 pollution
            (tw.tl.changed, self._on_timeline_changed),
            (tw.tl.overlays_changed, self._on_overlays_changed),
        ]
        for signal, slot in pairs:
            try:
                signal.disconnect(slot)
            except TypeError:
                pass  # 未连接，正常情况

    def _rebind_signals(self, old_widget, new_widget):
        """切换时间线时：断开旧信号，连接新信号，更新 preview/props_panel 引用"""
        self._unbind_timeline_signals(old_widget)
        self._bind_timeline_signals()
        # 更新 PreviewPlayer 的时间线引用
        self.preview.tl = self.timeline
        # 更新属性面板的时间线引用
        self.props_panel.tl = self.timeline

    # ─────────────────────────────────────────
    # 多时间线管理
    def _create_tab_button(self, idx: int, name: str, checked: bool) -> QPushButton:
        """创建标签按钮（消除重复代码）"""
        btn = QPushButton(name)
        btn.setCheckable(True)
        btn.setChecked(checked)
        btn.setFixedHeight(22)
        btn.setMouseTracking(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(TAB_STYLE)
        btn.clicked.connect(lambda checked, i=idx: self._switch_timeline(i))
        btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        btn.customContextMenuRequested.connect(
            lambda pos, b=btn, i=idx: self._tab_context_menu(pos, b, i))
        # 双击重命名 → 复用右键重命名逻辑
        btn.mouseDoubleClickEvent = lambda e, b=btn, i=idx: self._rename_timeline_tab(b, i)
        btn.installEventFilter(self)
        return btn

    # ─────────────────────────────────────────
    def _on_new_timeline(self):
        self._mark_dirty()
        self._add_timeline()

    def _add_timeline(self, is_first: bool = False, name: str = ""):
        """创建新时间线并添加到 UI"""
        tl = EditTimeline() if not is_first else self._timelines[0]
        tw = TimelineWidget(tl, dubbing_config_provider=self._dubbing_cfg_provider)
        tw.setMinimumHeight(100)

        idx = len(self._tl_widgets)
        if not is_first:
            self._timelines.append(tl)
        self._tl_widgets.append(tw)
        self._tl_stack.addWidget(tw)

        # 标签按钮
        tab_name = name or f"时间线 {len(self._tl_widgets)}"
        btn = self._create_tab_button(idx, tab_name, is_first)

        # 插入到 stretch 之前
        self._tl_tab_layout.insertWidget(
            self._tl_tab_layout.count() - 1, btn)

        if not is_first:
            self._switch_timeline(idx)

    def _switch_timeline(self, idx: int):
        """切换到指定时间线"""
        if idx == self._active_tl_idx:
            return
        old_idx = self._active_tl_idx
        old_widget = self._tl_widgets[old_idx]
        old_tl = self._timelines[old_idx]

        # 停止旧时间线的播放和音频（时间线完全独立）
        if old_widget.is_playing():
            old_widget.stop_playback()
        self.preview.stop_audio()
        self.preview.set_playing(False)

        # 释放旧时间线的 alpha 视频管道（避免进程泄漏）
        try:
            from utils.alpha_video import close_all_pipe_readers
            close_all_pipe_readers()
        except Exception:
            pass

        self._active_tl_idx = idx

        # 断开旧信号
        self._unbind_timeline_signals(old_widget)
        try:
            old_tl.changed.disconnect(self._on_timeline_changed)
        except Exception: pass

        # 更新按钮样式
        for i in range(self._tl_tab_layout.count()):
            w = self._tl_tab_layout.itemAt(i).widget()
            if isinstance(w, QPushButton) and w.isCheckable():
                w.setChecked(i == idx)

        # 切换堆叠
        self._tl_stack.setCurrentIndex(idx)

        # 绑定新信号（_bind_timeline_signals 已包含 timeline.changed 连接，无需重复）
        self._bind_timeline_signals()
        self.preview.tl = self.timeline
        self.props_panel.tl = self.timeline
        self.props_panel.clear_selection()

        # 刷新预览
        self.preview.seek(self.timeline_widget.get_playhead())

    def _tab_context_menu(self, pos, btn: QPushButton, idx: int):
        """标签右键菜单：重命名、关闭"""
        # 右键弹出菜单前自动暂停播放
        if self.timeline_widget.is_playing():
            self.timeline_widget.toggle_play()
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#2a2a2a;color:#ccc;border:1px solid #444;}"
            "QMenu::item:selected{background:#3d8ef8;}")
        # 重命名（始终可用）
        rename_act = menu.addAction("✏  重命名")
        rename_act.triggered.connect(lambda: self._rename_timeline_tab(btn, idx))
        # 关闭（至少保留一条）
        if len(self._timelines) > 1:
            close_act = menu.addAction("✕  关闭")
            close_act.triggered.connect(lambda: self._close_timeline(idx))
        menu.exec(btn.mapToGlobal(pos))

    def _rename_timeline_tab(self, btn: QPushButton, idx: int):
        """弹出输入框重命名时间线标签"""
        from PyQt6.QtWidgets import QInputDialog
        cur_name = self._timelines[idx].name if idx < len(self._timelines) else btn.text()
        name, ok = QInputDialog.getText(self, "重命名", "时间线名称:", text=cur_name)
        if ok and name.strip():
            btn.setText(name.strip())
            if idx < len(self._timelines):
                self._timelines[idx].name = name.strip()
                self._mark_dirty()

    def _close_timeline(self, idx: int):
        """关闭指定时间线（至少保留一条）"""
        if len(self._timelines) <= 1:
            return
        self._mark_dirty()
        # 如果要关闭的是当前激活的，先切换到其他
        if idx == self._active_tl_idx:
            new_idx = idx - 1 if idx > 0 else 0
            self._switch_timeline(new_idx)

        # 移除数据（必须在重建标签栏之前，确保 _rebuild_tab_bar 看到正确状态）
        old_tl = self._timelines.pop(idx)
        old_tw = self._tl_widgets.pop(idx)
        self._tl_stack.removeWidget(old_tw)
        old_tw.deleteLater()

        # 调整激活索引：关闭的索引在激活索引之前时，激活索引需减1
        if idx < self._active_tl_idx:
            self._active_tl_idx -= 1
        # 关闭的索引在激活索引之后时的安全边界检查
        if self._active_tl_idx >= len(self._timelines):
            self._active_tl_idx = max(0, len(self._timelines) - 1)

        # 重建标签栏
        self._rebuild_tab_bar()

    def _rebuild_tab_bar(self):
        """重建标签栏（关闭/重排时间线后）"""
        # 清空现有按钮（保留最后的 stretch）
        while self._tl_tab_layout.count() > 1:
            w = self._tl_tab_layout.takeAt(0).widget()
            if w:
                w.removeEventFilter(self)
                w.deleteLater()
        # 重建按钮
        for i in range(len(self._timelines)):
            tl = self._timelines[i]
            tl_name = tl.name if tl.name else f"时间线 {i + 1}"
            btn = self._create_tab_button(i, tl_name, i == self._active_tl_idx)
            self._tl_tab_layout.insertWidget(
                self._tl_tab_layout.count() - 1, btn)

    def _calc_drop_target(self, x: int) -> int:
        """根据鼠标 X 坐标计算拖拽落点索引"""
        for i in range(self._tl_tab_layout.count()):
            w = self._tl_tab_layout.itemAt(i).widget()
            if isinstance(w, QPushButton) and w.isCheckable():
                rect = w.geometry()
                if x < rect.center().x():
                    return i
        # 落在所有标签右边 → 最后一个位置
        return len(self._timelines) - 1

    def _reorder_timelines(self, src: int, dst: int):
        """将 src 位置的时间线移动到 dst 位置"""
        if src == dst:
            return
        self._mark_dirty()
        # 调整激活索引
        old_active = self._active_tl_idx
        if src == old_active:
            self._active_tl_idx = dst
        elif src < old_active <= dst:
            self._active_tl_idx -= 1
        elif dst <= old_active < src:
            self._active_tl_idx += 1
        # 移动数据
        tl = self._timelines.pop(src)
        tw = self._tl_widgets.pop(src)
        self._timelines.insert(dst, tl)
        self._tl_widgets.insert(dst, tw)
        # 更新堆叠顺序
        self._tl_stack.removeWidget(tw)
        self._tl_stack.insertWidget(dst, tw)
        # 重建标签栏
        self._rebuild_tab_bar()
        # 切回当前激活的时间线
        self._switch_timeline(self._active_tl_idx)

    # ─────────────────────────────────────────
    # 播放头移动（同步帧 + 手动 seek 时同步音频）
    # ─────────────────────────────────────────
    def _on_playhead_moved(self, sec: float):
        self.preview.seek(sec)
        self.props_panel.set_current_time(sec)
        # 非播放状态下拖动播放头：只更新画面位置，不启动音频
        if not self.timeline_widget.is_playing():
            return

    def _do_seek_audio(self):
        """debounce 到期后停止旧音频并从新位置播放"""
        # 内联编辑字幕时不启动音频播放
        if getattr(self.preview, '_editing_sub', None) is not None:
            return
        sec = getattr(self, '_seek_audio_sec', None)
        if sec is not None:
            self.preview.stop_audio()
            self.preview.play_all_audio(sec)

    # ─────────────────────────────────────────
    # 空格键全局播放（任意位置聚焦）
    # ─────────────────────────────────────────
    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Space:
            editing_inline = getattr(self.preview, '_editing_sub', None) is not None
            if not editing_inline:
                self.timeline_widget.toggle_play()
            e.accept()
        elif e.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            # 内联编辑中不拦截（preview_player 已 accept 处理）
            if getattr(self.preview, '_editing_sub', None) is not None:
                return super().keyPressEvent(e)
            if not self.isVisible() or self._in_text_field():
                return super().keyPressEvent(e)
            sel_sub = getattr(self.preview, '_selected_sub', None)
            if sel_sub is not None:
                self.timeline.remove_subtitle(sel_sub.id)
                self.preview._selected_sub = None
                self.preview._sub_interaction = None
                self.preview._set_seq_state(None)
                if hasattr(self.preview, '_current_sec'):
                    self.preview._async_fetch(self.preview._current_sec)
                e.accept()
            else:
                self.timeline_widget._do_delete()
                e.accept()
        else:
            super().keyPressEvent(e)

    def eventFilter(self, obj, event):
        """拦截整个应用内的按键事件 + 标签栏拖拽排序 + 预览退出"""
        from PyQt6.QtCore import QEvent

        # ── 字幕编辑中按 Escape 全局退出（即使焦点不在画布上）──
        if (event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Escape
                and getattr(self, 'preview', None) is not None
                and getattr(self.preview, '_editing_sub', None) is not None):
            self.preview._hide_sub_editor(save=True)
            return False  # 不消耗，让其他 Escape 处理器也能收到

        # ── 预览模式下：任何区域的鼠标点击都退出预览 ──
        if (event.type() == QEvent.Type.MouseButtonPress
                and getattr(self, 'preview', None) is not None
                and getattr(self.preview, '_preview_active', False)):
            self.preview.stop_preview()
            # 不消耗事件，让点击继续传递（正常响应被点击的控件）
            return False

        # ── 字幕编辑中点击其他区域 → 退出编辑 ──
        if (event.type() == QEvent.Type.MouseButtonPress
                and getattr(self, 'preview', None) is not None
                and getattr(self.preview, '_editing_sub', None) is not None):
            self.preview._hide_sub_editor(save=True)
            # 不消耗事件
            return False

        # ── 标签按钮拖拽排序 ──
        if isinstance(obj, QPushButton) and obj.isCheckable() and obj.parent() is self._tl_tab_bar:
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                # 记录起始位置，但不消耗事件（让按钮保持正常 press/click 行为）
                for i in range(self._tl_tab_layout.count()):
                    if self._tl_tab_layout.itemAt(i).widget() is obj:
                        self._drag_source_idx = i
                        self._drag_btn = obj
                        self._drag_offset_x = event.position().x()
                        self._drag_started = False
                        break
                return False
            elif event.type() == QEvent.Type.MouseMove and self._drag_btn is obj:
                if not getattr(self, '_drag_started', False):
                    delta = abs(event.position().x() - self._drag_offset_x)
                    if delta > self._drag_threshold:
                        self._drag_started = True
                        obj.setStyleSheet(
                            "QPushButton{background:#3d8ef8;color:#fff;border:none;"
                            "border-radius:3px;padding:2px 10px;font-size:11px;}")
                        obj.raise_()
                return getattr(self, '_drag_started', False)
            elif event.type() == QEvent.Type.MouseButtonRelease and self._drag_btn is obj:
                was_dragging = getattr(self, '_drag_started', False)
                if was_dragging:
                    is_active = self._drag_source_idx == self._active_tl_idx
                    self._drag_btn.setStyleSheet(
                        "QPushButton{background:#1a1a1a;color:#666;border:none;"
                        "border-radius:3px;padding:2px 10px;font-size:11px;}"
                        "QPushButton:hover{background:#252525;color:#aaa;}"
                        "QPushButton:checked{background:#2a5fa8;color:#fff;}")
                    if is_active:
                        self._drag_btn.setChecked(True)
                    pos = event.globalPosition().toPoint()
                    bar_pos = self._tl_tab_bar.mapFromGlobal(pos)
                    target = self._calc_drop_target(bar_pos.x())
                    if target >= 0 and target != self._drag_source_idx:
                        self._reorder_timelines(self._drag_source_idx, target)
                self._drag_btn = None
                self._drag_source_idx = -1
                self._drag_started = False
                return was_dragging
            elif event.type() == QEvent.Type.ContextMenu:
                # 右键菜单前自动暂停
                if self.timeline_widget.is_playing():
                    self.timeline_widget.toggle_play()
                return False

        return super().eventFilter(obj, event)

    # ─────────────────────────────────────────
    # 属性面板关键帧跳转请求
    # ─────────────────────────────────────────
    def _on_seek_requested(self, sec: float):
        """属性面板 < 上一个 / 下一个 > 按钮 → 跳转播放头"""
        self.timeline_widget.set_playhead(sec)
        # 同步更新属性面板的内部时间状态，否则关键帧导航/插入都基于旧时间
        self.props_panel.set_current_time(sec)

    def _on_pause_requested(self):
        """预览画布右键 → 暂停播放"""
        if self.timeline_widget.is_playing():
            self.timeline_widget.toggle_play()

    # ─────────────────────────────────────────
    # 属性面板变动 → 立即刷新预览帧
    # ─────────────────────────────────────────
    def _on_property_changed(self):
        """属性改动 → 30ms debounce 后刷新预览，避免拖拽滑块时高频 seek 导致卡死闪退"""
        self._prop_debounce.start()  # 重置 timer，连续触发只保留最后一次
        # 同步刷新时间线（如视频片段 out_transition 变化时，接缝处的转场标记需重绘）
        try:
            self.timeline_widget.update()
        except Exception:
            pass

    def _on_sub_pos_changed(self, block, pos_x: float, pos_y: float):
        """画布拖拽字幕位置 → 通过属性面板同步机制广播到所有字幕块"""
        if self.props_panel._sync_subs and self.props_panel._track == "subtitle":
            self.props_panel._pending_sync_attrs['pos_x'] = pos_x
            self.props_panel._pending_sync_attrs['pos_y'] = pos_y
            self.props_panel._sync_debounce.start()

    def _do_property_seek(self):
        """debounce 到期后真正执行 seek + 音频重启（音量/速度等变动需要立即反映）"""
        sec = self.timeline_widget.get_playhead()
        if getattr(self.props_panel, '_track', '') == "subtitle":
            # 字幕属性（位置/缩放/旋转/字间距/字号等）只影响叠加层，视频底图不变，
            # 走轻量重绘路径，避免 seek(force=True) 全量重解码导致的卡顿（问题5）。
            try:
                self.preview._recompose_overlays()
            except Exception:
                logging.debug(
                    "subtitle recompose failed, fallback to seek", exc_info=True)
                self.preview._seq_state = None
                self.preview.seek(sec, force=True)
            return
        self.preview._seq_state = None
        # force=True：属性变更（含 out_transition 转场）后，即使播放头未移动也必须
        # 重新取帧——否则 seek 的同位置快速路径会跳过 fetch，导致"加了转场预览没反应"。
        self.preview.seek(sec, force=True)
        # 播放中的属性变更（如音量滑块）：重启音频以应用新值
        if self.timeline_widget.is_playing():
            self.preview.stop_audio()
            self.preview.play_all_audio(sec)

    def _paths_on_timeline(self) -> set:
        """收集所有时间线中正在使用的素材路径（用于素材库状态角标）"""
        paths: set = set()
        for tl in getattr(self, "_timelines", []):
            # EditTimeline 无独立 image_tracks 属性，图片以 VideoClip 形式存于 video_tracks；
            # 用 getattr 安全降级为空，避免 AttributeError 中断整个 refresh 流程。
            for tracks in (tl.video_tracks, tl.audio_tracks, getattr(tl, "image_tracks", [])):
                for track in tracks:
                    for c in track:
                        sp = getattr(c, "source_path", "")
                        if sp:
                            paths.add(sp)
        return paths

    def _on_timeline_changed(self):
        """时间线数据改变 → 刷新当前帧 + 标记工程脏 + 同步素材库状态角标"""
        self._mark_dirty()
        # 强制刷新帧缓存，确保 undo/redo 等数据变更后画面正确（如视频位置恢复）
        self.preview._last_frame_image = None
        self.preview._last_raw_img = None
        self.preview._last_raw_overlays = []
        self.preview.seek(self.timeline_widget.get_playhead(), force=True)
        # 同步素材库「已添加/未添加」角标
        try:
            self.media_lib.refresh_statuses(self._paths_on_timeline())
        except Exception:
            logging.debug("refresh media statuses failed", exc_info=True)

    def _on_overlays_changed(self):
        """仅叠加层变化（字幕同步/样式）→ 保留帧缓存，轻量重绘叠加层，避免闪黑"""
        self._mark_dirty()
        self.preview._recompose_overlays()

    def _on_play_preview(self, path: str, media_type: str):
        """素材库双击 → 在画布预览区按原尺寸播放"""
        if not os.path.exists(path):
            return
        self.preview.start_preview(path, media_type)

    def _on_file_removed(self, path: str):
        """素材从素材库移除 → 同步删除所有时间线中使用该素材的片段"""
        removed = False
        for tl in self._timelines:
            # 删除视频轨中使用该素材的片段
            for track in tl.video_tracks:
                to_remove = [c for c in track if hasattr(c, 'source_path') and c.source_path == path]
                for clip in to_remove:
                    tl.remove_video_clip(clip.id)
                    removed = True
            # 删除音频轨中使用该素材的片段
            for track in tl.audio_tracks:
                to_remove = [c for c in track if hasattr(c, 'source_path') and c.source_path == path]
                for clip in to_remove:
                    tl.remove_audio_clip(clip.id)
                    removed = True
        # 清除预览缓存
        if hasattr(self, 'preview') and self.preview:
            try:
                self.preview.clear_file_cache(path)
            except Exception:
                logging.debug("clear_file_cache error", exc_info=True)
        # 刷新预览（即使时间线中没有该素材，也要确保预览区刷新）
        try:
            self.preview._async_fetch(self.preview._current_sec) if hasattr(
                self.preview, '_current_sec') and hasattr(self.preview, '_async_fetch') else None
        except Exception:
            logging.debug("_async_fetch error", exc_info=True)
        if removed:
            self._on_timeline_changed()

    def _on_download_finished(self, path: str):
        """下载完成 → 自动加入素材库"""
        print(f"[editor_tab] _on_download_finished 收到 path={path!r}")
        if not path:
            print("[editor_tab] 跳过：path 为空")
            return
        if not os.path.exists(path):
            print(f"[editor_tab] 跳过：文件不存在 {path}")
            return
        self.media_lib.add_file(path)
        print(f"[editor_tab] 已调用 media_lib.add_file({path!r})")

    def _on_openverse_download_finished(self, path: str):
        """Openverse 音频下载完成 → 加入素材库并给出工作台反馈。"""
        self._on_download_finished(path)
        if path and os.path.exists(path):
            self.status_msg.emit("Openverse 音频已下载并加入素材库 ✓", "success")

    # ─────────────────────────────────────────
    # 画布比例
    # ─────────────────────────────────────────
    def _on_ratio_changed(self, text: str):
        # "自定义..." → 弹出尺寸输入对话框
        if text == "自定义...":
            from math import gcd
            w, ok = QInputDialog.getInt(self, "自定义画布", "宽度 (px):", 1920, 1, 7680, 1)
            if not ok:
                self._restore_ratio_selection()
                return
            h, ok = QInputDialog.getInt(self, "自定义画布", "高度 (px):", 1080, 1, 7680, 1)
            if not ok:
                self._restore_ratio_selection()
                return
            g = gcd(w, h)
            ratio = (w // g, h // g)

            self._ratio_combo.blockSignals(True)
            # 检查是否匹配已有预设
            matched = False
            for k, v in CANVAS_RATIOS.items():
                if v == ratio and k != "自定义...":
                    self._ratio_combo.setCurrentText(k)
                    matched = True
                    break
            if not matched:
                label = f"{w}×{h}"
                self._remove_custom_items()
                ci = self._ratio_combo.findText("自定义...")
                if ci >= 0:
                    self._ratio_combo.insertItem(ci, label)
                self._ratio_combo.setCurrentText(label)
            self._ratio_combo.blockSignals(False)

            self._custom_size = (w, h)
            self._canvas_ratio = ratio
            self.preview.set_aspect_ratio(ratio)
            return

        # 自定义尺寸格式 "W×H"
        if "×" in text and text != "自定义...":
            self._apply_custom_size_from_text(text)
            return

        # 预设比例
        ratio = CANVAS_RATIOS.get(text)
        self._canvas_ratio = ratio  # None = 默认（跟随视频尺寸）
        self._custom_size = None
        self.preview.set_aspect_ratio(ratio)

    def _get_canvas_resolution(self):
        """根据当前画布比例返回推荐导出分辨率 (w, h)，None 表示不干预"""
        if self._custom_size:
            # 自定义尺寸直接使用实际像素
            return self._custom_size
        if self._canvas_ratio:
            # 预设比例 → 映射到标准分辨率
            ratio = self._canvas_ratio
            standard = {
                (16, 9): (1920, 1080),
                (9, 16): (1080, 1920),
                (1, 1): (1080, 1080),
                (4, 3): (1440, 1080),
                (21, 9): (2560, 1080),
            }
            if ratio in standard:
                return standard[ratio]
            # 非标准比例：用一个合理的基础宽度 1920 计算高度
            rw, rh = ratio
            base_w = 1920
            base_h = int(base_w * rh / rw)
            # 确保偶数（FFmpeg 编码要求）
            base_h = base_h if base_h % 2 == 0 else base_h + 1
            return (base_w, base_h)
        # 默认：不干预，ExportDialog 使用默认 1920×1080
        return None

    def _remove_custom_items(self):
        """移除下拉框中所有自定义尺寸项"""
        for i in range(self._ratio_combo.count() - 1, -1, -1):
            t = self._ratio_combo.itemText(i)
            if "×" in t and t not in CANVAS_RATIOS:
                self._ratio_combo.removeItem(i)

    def _apply_custom_size_from_text(self, text: str):
        """从 "W×H" 格式文本解析并应用自定义画布尺寸"""
        parts = text.split("×")
        if len(parts) != 2:
            return
        try:
            w = int(parts[0].strip())
            h = int(parts[1].strip())
            if w <= 0 or h <= 0:
                return
            from math import gcd
            g = gcd(w, h)
            self._custom_size = (w, h)
            self._canvas_ratio = (w // g, h // g)
            self.preview.set_aspect_ratio(self._canvas_ratio)
        except ValueError:
            pass

    def _restore_ratio_selection(self):
        """取消自定义 → 恢复到之前的选择"""
        self._ratio_combo.blockSignals(True)
        if self._canvas_ratio is None:
            self._ratio_combo.setCurrentText("默认")
        elif self._custom_size:
            w, h = self._custom_size
            label = f"{w}×{h}"
            if self._ratio_combo.findText(label) < 0:
                ci = self._ratio_combo.findText("自定义...")
                if ci >= 0:
                    self._ratio_combo.insertItem(ci, label)
            self._ratio_combo.setCurrentText(label)
        else:
            found = False
            for k, v in CANVAS_RATIOS.items():
                if v == self._canvas_ratio and k != "自定义...":
                    self._ratio_combo.setCurrentText(k)
                    found = True
                    break
            if not found:
                self._ratio_combo.setCurrentText("默认")
        self._ratio_combo.blockSignals(False)

    def _sync_ratio_combo(self):
        """根据当前 _canvas_ratio / _custom_size 同步下拉框显示"""
        self._ratio_combo.blockSignals(True)
        if self._canvas_ratio is None:
            self._ratio_combo.setCurrentText("默认")
        elif self._custom_size:
            w, h = self._custom_size
            label = f"{w}×{h}"
            self._remove_custom_items()
            ci = self._ratio_combo.findText("自定义...")
            if ci >= 0:
                self._ratio_combo.insertItem(ci, label)
            self._ratio_combo.setCurrentText(label)
        else:
            matched = False
            for k, v in CANVAS_RATIOS.items():
                if v == self._canvas_ratio and k != "自定义...":
                    self._ratio_combo.setCurrentText(k)
                    matched = True
                    break
            if not matched:
                self._ratio_combo.setCurrentText("默认")
        self._ratio_combo.blockSignals(False)

    # ─────────────────────────────────────────
    # 素材加入时间线
    # ─────────────────────────────────────────
    def _find_current_clip_by_id(self, clip_id: str):
        """跨所有时间线按 id 找当前生效的片段（undo/redo 可能已替换 clip 对象，
        必须用当前时间线里的对象，而非 worker 持有的旧引用）。"""
        for tl in self._timelines:
            for track in tl.video_tracks:
                for c in track:
                    if getattr(c, "id", None) == clip_id:
                        return c
        return None

    def _on_thumbnails_ready(self, clip, thumbnails):
        """缩略图后台生成完毕，更新片段并刷新时间线"""
        # Worker 持有的 clip 可能是被 undo/redo 替换掉的游离旧对象，
        # 必须按 id 命中当前时间线里的 clip，否则新图挂错对象 → 时间线空白。
        cur = self._find_current_clip_by_id(getattr(clip, "id", "")) if hasattr(clip, "id") else None
        target = cur if cur is not None else clip
        # Worker 返回 QPixmap 列表（已在后台线程用 disk/QImage 生成）
        target.thumbnails = thumbnails
        if thumbnails:
            target.thumbnail = thumbnails[0]
        target._scaled_thumbs_cache = None  # 清除旧缓存
        self.timeline_widget.refresh_canvas()
        # 清理已完成的 worker
        w = self.sender()
        if w is not None and w in self._thumb_workers:
            self._thumb_workers.remove(w)
            w.deleteLater()
        # 处理待处理队列（启动下一个 pending 的缩略图任务）
        self._process_thumb_pending()

    def _start_thumbnail_worker(self, clip, count: int, thumb_h: int = 36):
        """安全启动缩略图后台任务（遵守 MAX_THUMB_WORKERS 并发上限，超出时入队列）
        count: 需要生成的缩略图张数（根据 clip 像素宽度动态计算）
        thumb_h: 显示高度（px），实际生成 2x 尺寸"""
        if not hasattr(clip, 'source_path') or not clip.source_path:
            return
        # 如果已达上限，加入待处理队列
        if len(self._thumb_workers) >= MAX_THUMB_WORKERS:
            self._thumb_pending.append((clip, count, thumb_h))
            return
        # 保留旧缩略图：缩放/重新生成期间旧图继续显示（拉伸或压缩，不闪白），
        # 新图在后台 worker 完成后于 _on_thumbnails_ready 中整体替换。
        clip._scaled_thumbs_cache = None
        clip._scaled_single_thumb = None
        worker = ThumbnailWorker(clip, count, thumb_h)
        worker.finished.connect(self._on_thumbnails_ready)
        self._thumb_workers.append(worker)
        worker.start()

    def _process_thumb_pending(self):
        """处理待处理队列：启动下一个 pending 的缩略图任务"""
        if not self._thumb_pending:
            return
        if len(self._thumb_workers) >= MAX_THUMB_WORKERS:
            return
        item = self._thumb_pending.pop(0)
        clip, count, thumb_h = item
        if not hasattr(clip, 'source_path') or not clip.source_path:
            self._process_thumb_pending()
            return
        self._start_thumbnail_worker(clip, count, thumb_h)

    def _start_waveform_worker(self, clip):
        if not getattr(clip, "source_path", "") or not os.path.exists(clip.source_path):
            return
        worker = WaveformWorker(clip)
        worker.finished.connect(self._on_waveform_ready)
        self._waveform_workers.append(worker)
        worker.start()

    def _on_waveform_ready(self, clip, peaks):
        clip_id = getattr(clip, "id", "")
        target = clip
        for tl in self._timelines:
            for track in tl.audio_tracks:
                found = next((c for c in track if getattr(c, "id", "") == clip_id), None)
                if found is not None:
                    target = found
                    break
        target.waveform = peaks
        self.timeline_widget.refresh_canvas()
        worker = self.sender()
        if worker in self._waveform_workers:
            self._waveform_workers.remove(worker)
            worker.deleteLater()

    def _regenerate_thumbnails(self, clip):
        """截断/分割后重新生成缩略图（以当前 trim_start/trim_end 为准）"""
        dur = getattr(clip, 'source_duration', 0) or clip.duration
        count, thumb_h = self._thumb_params(dur)
        self._start_thumbnail_worker(clip, count, thumb_h)

    def _thumb_params(self, dur: float) -> tuple:
        """缩略图数量与缩放彻底解耦：仅由片段时长决定（约每 0.4s 一张，封顶 60）。
        缩放只改变布局（每张显示宽度 = clip像素宽 / count），不改变缩略图集合
        → 缩放不闪白、磁盘缓存命中（cache key 不再含 zoom）。"""
        # 约每 0.4s 抽一张；短片段至少 3 张，长片段封顶 60 张（控制内存/解码量）
        count = max(3, min(60, int(dur / 0.4)))
        # 缩略图显示高度 = 轨道高度（约 36px）
        thumb_h = 36
        return count, thumb_h

    def _on_clip_trimmed(self, clip):
        """时间线拖拽 trim 手柄后，重新渲染缩略图"""
        self._regenerate_thumbnails(clip)
        self._mark_dirty()
        # 同步更新预览
        self.preview.seek(self.preview._current_sec)

    def _add_to_timeline(self, path: str, media_type: str, duration: float,
                         track_idx: int = -1, timeline_start: float = -1.0):
        """
        双击素材库 / 拖拽入时间线的统一入口。
        track_idx=-1  → 智能选轨：
          - timeline_start<0（双击）→ 追加到主轨末尾
          - timeline_start>=0（拖拽）→ 检测主轨是否有冲突，有冲突则找/建叠加轨
        timeline_start=-1 → 自动追加到主轨末尾
        当 track_idx 由调用方指定（如 dropEvent 中拖到叠加轨）时直接用该轨道，
        但仍检测冲突：若目标轨道也有冲突，再往上找空叠加轨。
        """
        tl = self.timeline
        if media_type in ("video", "image"):
            dur = 5.0 if media_type == "image" else duration
            if dur <= 0:
                dur = 5.0

            if track_idx < 0:
                # 自动决策轨道
                if timeline_start < 0:
                    # 双击 → 直接追加到主轨末尾
                    track_idx = 0
                    track_clips = tl.video_tracks[0] if tl.video_tracks else []
                    timeline_start = max((c.timeline_end for c in track_clips), default=0.0)
                else:
                    # 拖拽 → 主轨有空就用主轨，有冲突则用 auto_align 逻辑
                    if tl.auto_align:
                        # 自动磁吸 → 主轨末尾无间隙放入
                        track_idx = 0
                        track_clips = tl.video_tracks[0] if tl.video_tracks else []
                        timeline_start = max((c.timeline_end for c in track_clips), default=0.0)
                    elif self._has_overlap(tl, 0, timeline_start, dur):
                        # 自动对齐关 → 找叠加轨空隙
                        track_idx = self._find_free_video_track(timeline_start, dur, prefer_track=0)
                    else:
                        track_idx = 0
            elif track_idx >= len(tl.video_tracks):
                # 轨道索引超出范围（拖放到视频拖放区→新建叠加轨）
                # 拖到主轨且开启磁吸 → 从0s或末尾开始
                if track_idx == 0 and tl.auto_align:
                    track_clips = tl.video_tracks[0] if tl.video_tracks else []
                    timeline_start = max((c.timeline_end for c in track_clips), default=0.0)
            else:
                # 调用方指定了轨道（如拖到主轨），仍检测冲突
                if track_idx == 0 and tl.auto_align:
                    # 拖到主轨且开启磁吸 → 放到主轨末尾，无间隙
                    track_clips = tl.video_tracks[0] if track_idx < len(tl.video_tracks) else []
                    timeline_start = max((c.timeline_end for c in track_clips), default=0.0)
                elif timeline_start >= 0:
                    if self._has_overlap(tl, track_idx, timeline_start, dur):
                        track_idx = self._find_free_video_track(timeline_start, dur, prefer_track=track_idx)
                elif timeline_start < 0:
                    track_clips = tl.video_tracks[track_idx] if track_idx < len(tl.video_tracks) else []
                    timeline_start = max((c.timeline_end for c in track_clips), default=0.0)

            clip = VideoClip(source_path=path, source_duration=dur,
                             trim_start=0.0, trim_end=dur, timeline_start=timeline_start,
                             has_alpha=(media_type == "video" and self._detect_video_alpha(path)))
            # 自动磁吸已在主轨末尾放置，无重叠可能，跳过额外防重叠检查
            snapped = (track_idx == 0 and tl.auto_align)
            tl.add_video_clip(clip, track_idx=track_idx, skip_overlap=snapped)
            # 预提取音频到 WAV（后台线程，不阻塞），避免首次按空格播放时无声
            try:
                self.preview._ensure_audio_for_video(path)
            except Exception:
                pass
            # 首次导入视频/图片 → 自动设置画布比例为素材原始尺寸
            if self._canvas_ratio is None and media_type in ("video", "image"):
                self._auto_canvas_from_media(path, media_type)
            # 后台生成缩略图（缩放自适应张数）
            count, th = self._thumb_params(dur)
            self._start_thumbnail_worker(clip, count, th)
        elif media_type == "audio":
            if duration <= 0:
                duration = 0.0
            # MP3/M4A/AAC 等需先转成 WAV；拖入即预热，避免首次按空格才开始转码。
            try:
                self.preview._ensure_audio_for_video(path)
            except Exception:
                logging.debug("audio prewarm failed for %s", path, exc_info=True)
            if track_idx < 0:
                track_idx = 0
            if timeline_start < 0:
                # 未指定起点（双击素材库）→ 追加到该轨末尾
                track_clips = tl.audio_tracks[track_idx] if track_idx < len(tl.audio_tracks) else []
                timeline_start = max((c.timeline_end for c in track_clips), default=0.0)
                clip = AudioClip(source_path=path, source_duration=duration,
                                 trim_start=0.0, trim_end=duration, timeline_start=timeline_start)
                tl.add_audio_clip(clip, track_idx=track_idx)
            else:
                # 指定起点（配音对齐字幕 / 拖拽）→ 精确落在指定时间，选不冲突轨
                clip = AudioClip(source_path=path, source_duration=duration,
                                 trim_start=0.0, trim_end=duration, timeline_start=timeline_start)
                target = track_idx
                while target >= len(tl.audio_tracks):
                    tl.add_audio_track()
                for i in range(track_idx, len(tl.audio_tracks)):
                    conflict = any(
                        c.timeline_start < timeline_start + duration
                        and (c.timeline_start + c.duration) > timeline_start
                        for c in tl.audio_tracks[i]
                    )
                    if not conflict:
                        target = i
                        break
                # 选定轨道仍冲突（所有轨都占满）→ 新建轨道放最上层
                if any(
                    c.timeline_start < timeline_start + duration
                    and (c.timeline_start + c.duration) > timeline_start
                    for c in tl.audio_tracks[target]
                ):
                    target = tl.add_audio_track()
                tl.add_audio_clip(clip, track_idx=target, skip_overlap=True)
            self._start_waveform_worker(clip)
        # 标记素材已加入轨道（素材库状态角标）
        try:
            self.media_lib.mark_on_track(path, True)
        except Exception:
            pass
        self.timeline_widget.rebuild_canvas()
        # 导入素材后立即触发首帧提取，避免预览一直黑屏
        if media_type in ("video", "image"):
            self.preview._async_fetch(self.preview._current_sec)

    def _dubbing_add_audio(self, path: str, duration: float, timeline_start: float,
                           subtitle_end=None):
        """配音面板生成完成后的落轨回调：配音按字幕起点对齐，追加到音频轨。

        timeline_start 由配音面板传入（字幕 clip 的起始时间），使配音与字幕对齐。
        使用完整配音时长落轨（不截断到字幕段，也不加入素材库）。

        subtitle_end 仅用于起点对齐参考，不再用于截断音频时长。
        """
        # 按字幕起点对齐落轨，使用完整配音时长（避免「一丁点 / 无声」）
        self._add_to_timeline(path, "audio", duration, timeline_start=timeline_start)

    def _dubbing_cfg_provider(self) -> dict:
        """供轨道多选字幕朗读复用：取配音面板当前引擎/音色/语速/音量配置。"""
        dp = getattr(self, "props_panel", None)
        dp = getattr(dp, "dubbing_panel", None) if dp is not None else None
        if dp is not None:
            try:
                return dp.get_config()
            except Exception:
                pass
        return {"engine": "edge", "voice": "", "rate": "+0%", "volume": 1.0}

    def _get_selected_subtitles(self) -> list:
        """返回当前时间线画布上选中的全部字幕 clip（框选多选 + 单条选中），按时间排序。

        供配音面板批量生成使用：框选多条字幕后点「生成配音」可逐条生成并各自落轨。
        """
        try:
            canvas = self.timeline_widget._canvas
        except Exception:
            return []
        subs = []
        for c, t in getattr(canvas, "_marquee_selected", []) or []:
            if getattr(t, "kind", "") == "subtitle":
                subs.append(c)
        sel = getattr(canvas, "_selected_clip", None)
        std = getattr(canvas, "_selected_td", None)
        if sel is not None and std is not None and getattr(std, "kind", "") == "subtitle":
            if sel not in subs:
                subs.append(sel)
        subs.sort(key=lambda c: getattr(c, "timeline_start", 0.0))
        return subs

    def _snap_canvas_to_loaded_media(self):
        """工程加载后，画布比例未显式保存时，从已加载时间线首个视频/图片自动磁吸"""
        for tl in self._timelines:
            for track in tl.video_tracks:
                for clip in track:
                    if not clip.source_path or not os.path.exists(clip.source_path):
                        continue
                    self._auto_canvas_from_media(clip.source_path, "video")
                    return
            for track in getattr(tl, "image_tracks", []):
                for clip in track:
                    if not clip.source_path or not os.path.exists(clip.source_path):
                        continue
                    self._auto_canvas_from_media(clip.source_path, "image")
                    return

    @staticmethod
    def _detect_video_alpha(path: str) -> bool:
        """检测视频是否含 alpha 通道。

        只有 MOV / WebM 才可能含 alpha（MP4 等格式几乎不会有），
        对于这些格式用 probe_has_alpha（FFmpeg 快速探测），避免主线程
        上 cv2.VideoCapture 打开大文件造成的阻塞（moov atom 在尾部时
        OpenCV 可能需要读完整文件元数据）。
        """
        ext = os.path.splitext(path)[1].lower()
        if ext not in {".mov", ".webm"}:
            return False  # MP4 等格式：快速返回，不打开文件
        try:
            from utils.alpha_video import probe_has_alpha
            return probe_has_alpha(path)
        except Exception:
            return False

    def _auto_canvas_from_media(self, path: str, media_type: str):
        """首次导入视频/图片时，自动将画布比例设置为素材原始分辨率"""
        try:
            import cv2
            from math import gcd
            if media_type == "video":
                cap = cv2.VideoCapture(path)
                if cap.isOpened():
                    mw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    mh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    cap.release()
                else:
                    return
            else:
                img = cv2.imread(path)
                if img is not None:
                    mh, mw = img.shape[:2]
                else:
                    return
            if mw > 0 and mh > 0:
                g = gcd(mw, mh)
                ratio = (mw // g, mh // g)
                self._canvas_ratio = ratio
                self._custom_size = (mw, mh)
                self.preview.set_aspect_ratio(ratio)
                self._sync_ratio_combo()
        except Exception:
            pass

    @staticmethod
    def _has_overlap(tl, track_idx: int, start: float, dur: float) -> bool:
        """检测指定视频轨道的 [start, start+dur) 是否与现有片段重叠"""
        if track_idx >= len(tl.video_tracks):
            return False
        end = start + dur
        for c in tl.video_tracks[track_idx]:
            if c.timeline_start < end and (c.timeline_start + c.duration) > start:
                return True
        return False

    def _find_free_video_track(self, start: float, dur: float, prefer_track: int = 0) -> int:
        """
        从 prefer_track 开始，向上找第一条与 [start, start+dur) 无重叠的视频轨。
        若所有现有轨道都有重叠，则新建叠加轨并返回其 idx。
        """
        tl = self.timeline
        if start < 0:
            return prefer_track
        end = start + dur
        # 先检测 prefer_track 本身
        start_idx = prefer_track
        for idx in range(start_idx, len(tl.video_tracks)):
            overlap = any(
                c.timeline_start < end and (c.timeline_start + c.duration) > start
                for c in tl.video_tracks[idx]
            )
            if not overlap:
                return idx
        # 所有现有轨道都重叠 → 新建叠加轨
        new_idx = tl.add_video_track()
        return new_idx

    def _on_drop_media(self, path: str, media_type: str, duration: float,
                       track_kind: str, track_idx: int, timeline_start: float):
        """时间线画布 drop 事件回调：在指定轨道的指定位置插入素材"""
        # 先把素材加到素材库（若不存在）
        self.media_lib.add_file(path)
        # 加入时间线，指定轨道和位置
        if track_kind == "video":
            self._add_to_timeline(path, media_type, duration,
                                  track_idx=track_idx, timeline_start=timeline_start)
        elif track_kind == "audio":
            self._add_to_timeline(path, "audio", duration,
                                  track_idx=track_idx, timeline_start=timeline_start)

    # ─────────────────────────────────────────
    # 选中处理
    # ─────────────────────────────────────────
    def _on_selection_changed(self, clip, track: str, track_idx: int):
        if clip is None:
            self.props_panel.clear_selection()
            # 清除画布上的选中/拖拽/编辑态（删除片段/轨道后防止残留选中框）
            self.preview.clear_video_selection()
        else:
            prev_video = self.preview._selected_video_clip
            self.props_panel.set_selection(clip, track)
            if track == "subtitle":
                self.preview._selected_sub = clip
                self.preview._selected_video_clip = None
            else:
                self.preview._selected_video_clip = clip
                self.preview._selected_sub = None
                # 仅在选中的视频片段【对象】真正变化时才清除 _seq_state；
                # 重复选中同一片段（或拖动中重复触发选中回调）保持 _seq_state 不变，
                # 避免强制从头重解码导致主轨二次拖动闪屏（问题1）。
                if clip is not prev_video:
                    self.preview._seq_state = None

    def _on_preview_selection(self, clip, kind: str):
        """预览画布点击选中（视频/字幕）"""
        if clip is None:
            self.props_panel.clear_selection()
            self.preview._selected_video_clip = None
            self.preview._selected_sub = None
        else:
            self.props_panel.set_selection(clip, kind)
            if kind == "subtitle":
                self.preview._selected_sub = clip
                self.preview._selected_video_clip = None
            else:
                self.preview._selected_video_clip = clip
                self.preview._selected_sub = None

    def _on_clip_double_clicked(self, clip, track: str, track_idx: int):
        self.props_panel.set_selection(clip, track)
        if track == "subtitle":
            self.preview._selected_sub = clip
            self.preview._selected_video_clip = None
        else:
            self.preview._selected_video_clip = clip
            self.preview._selected_sub = None
        # 只选中，不动播放头
        self.preview._seq_state = None

        # 双击字幕 → 直接在内联虚线框内编辑
        if track == "subtitle":
            from PyQt6.QtCore import QTimer as _Qt
            _Qt.singleShot(120, lambda: self.preview._show_sub_editor(clip))

    # ── 接缝双击 → 转场设置浮层（比进属性面板更快）──
    def _on_seam_double_clicked(self, A, B):
        """背景轨相邻两段（A→B）接缝双击 → 弹出轻量转场设置"""
        self._open_transition_dialog(A, B)

    def _on_thumbs_regen_requested(self):
        """缩放变化后重新生成所有可见片段的缩略图（300ms debounce）"""
        for tl in self._timelines:
            for track in tl.video_tracks:
                for clip in track:
                    if getattr(clip, 'source_path', ''):
                        self._regenerate_thumbnails(clip)

    def _open_transition_dialog(self, A, B):
        from core.slideshow_engine import TRANSITIONS, TRANS_DESCS
        from ui.widgets import CheckMarkBox
        from ui.clip_properties import CHECK_STYLE, COMBO_STYLE, SLIDER_STYLE

        dlg = QDialog(self)
        dlg.setWindowTitle("转场设置")
        dlg.setMinimumWidth(360)
        dlg.setStyleSheet(
            "QDialog{background:#1e1e1e; border:1px solid #333; border-radius:8px;}"
            "QLabel{color:#ddd;}"
        )
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        # ── 标题区（图标 + A → B）──
        hdr = QHBoxLayout()
        icon = QLabel("⇄")
        icon.setStyleSheet("color:#3d8ef8; font-size:20px; font-weight:bold;")
        hdr.addWidget(icon)
        a_name = getattr(A, "label", "") or (os.path.basename(A.source_path)
                  if getattr(A, "source_path", "") else "片段A")
        b_name = getattr(B, "label", "") or (os.path.basename(B.source_path)
                  if getattr(B, "source_path", "") else "片段B")
        title = QLabel(f"{a_name}  →  {b_name}")
        title.setStyleSheet("font-weight:bold; color:#3d8ef8; font-size:13px;")
        hdr.addWidget(title, 1)
        lay.addLayout(hdr)

        # ── 启用开关（CheckMarkBox）──
        en = CheckMarkBox("启用转场")
        ot = getattr(A, "out_transition", None)
        en.setChecked(bool(ot and ot.get("type")))
        en.setStyleSheet(CHECK_STYLE)
        lay.addWidget(en)

        # ── 设置卡片（类型 / 描述 / 时长）──
        card = QFrame()
        card.setStyleSheet(
            "QFrame{background:#252525; border:1px solid #3a3a3a; border-radius:6px;}"
        )
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(12, 10, 12, 10)
        card_lay.setSpacing(8)

        # 类型
        type_lay = QHBoxLayout()
        type_lay.addWidget(QLabel("类型"))
        combo = QComboBox()
        combo.setStyleSheet(COMBO_STYLE)
        combo.setFixedHeight(26)
        labels = list(TRANSITIONS.keys())
        combo.addItems(labels)
        cur_type = (ot or {}).get("type")
        if cur_type:
            for lab, typ in TRANSITIONS.items():
                if typ == cur_type:
                    combo.setCurrentText(lab)
                    break
        type_lay.addWidget(combo, 1)
        card_lay.addLayout(type_lay)

        # 描述
        desc = QLabel()
        desc.setWordWrap(True)
        desc.setStyleSheet("color:#999; font-size:11px;")
        def _upd_desc():
            desc.setText(TRANS_DESCS.get(combo.currentText(), ""))
        combo.currentTextChanged.connect(_upd_desc)
        _upd_desc()
        card_lay.addWidget(desc)

        # 时长
        dur_lay = QHBoxLayout()
        dur_lay.addWidget(QLabel("时长"))
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setStyleSheet(SLIDER_STYLE)
        slider.setRange(10, 200)  # 0.10s ~ 2.00s
        slider.setValue(int((ot or {}).get("duration", 0.5) * 100))
        dur_val = QLabel(f"{(ot or {}).get('duration', 0.5):.2f}s")
        dur_val.setStyleSheet("color:#3d8ef8; font-weight:bold; min-width:42px;")
        def _upd_dur(v):
            dur_val.setText(f"{v/100:.2f}s")
        slider.valueChanged.connect(_upd_dur)
        dur_lay.addWidget(slider, 1)
        dur_lay.addWidget(dur_val)
        card_lay.addLayout(dur_lay)

        lay.addWidget(card)

        # 时长滑块初始禁用（未启用时）
        slider.setEnabled(en.isChecked())
        combo.setEnabled(en.isChecked())
        en.toggled.connect(lambda c: (slider.setEnabled(c), combo.setEnabled(c)))

        # ── 按钮 ──
        btns = QHBoxLayout()
        btns.setSpacing(8)
        remove_btn = QPushButton("移除转场")
        remove_btn.setStyleSheet(
            "QPushButton{background:#3a2a2a; color:#ff9a9a; border:none;"
            "border-radius:4px; padding:6px 12px;}"
            "QPushButton:hover{background:#4a3333;}"
        )
        ok_btn = QPushButton("确定")
        ok_btn.setStyleSheet(
            "QPushButton{background:#3d8ef8; color:#fff; font-weight:bold; border:none;"
            "border-radius:4px; padding:6px 16px;}"
            "QPushButton:hover{background:#5aa0ff;}"
        )
        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border:none;"
            "border-radius:4px; padding:6px 14px;}"
            "QPushButton:hover{background:#3d3d3d;}"
        )
        btns.addWidget(remove_btn)
        btns.addStretch(1)
        btns.addWidget(cancel_btn)
        btns.addWidget(ok_btn)
        lay.addLayout(btns)

        result = {}

        def _apply():
            if en.isChecked():
                typ = TRANSITIONS.get(combo.currentText(), "fade")
                result["ot"] = {"type": typ, "duration": slider.value() / 100.0}
            else:
                result["ot"] = None
            dlg.accept()

        ok_btn.clicked.connect(_apply)
        cancel_btn.clicked.connect(dlg.reject)
        def _remove():
            result["ot"] = None
            dlg.accept()
        remove_btn.clicked.connect(_remove)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_ot = result.get("ot", "UNCHANGED")
            if new_ot == "UNCHANGED":
                return
            if new_ot is None:
                if hasattr(A, "out_transition"):
                    A.out_transition = None
            else:
                A.out_transition = new_ot
            # 刷新：接缝标记 + 预览（强制重取帧）+ 属性面板
            self.timeline_widget.update()
            self.props_panel.set_selection(A, "video")
            try:
                self.preview._seq_state = None
                self.preview.seek(self.timeline_widget.get_playhead(), force=True)
            except Exception:
                pass
            try:
                self.tl.changed.emit()
            except Exception:
                pass

    def _on_replace_video_requested(self, clip, new_path: str = ""):
        """时间线右键/拖拽 -> 替换视频"""
        self.props_panel.set_selection(clip, "video")
        self.props_panel._replace_video(clip, new_path)
        # 替换后重新生成缩略图（源文件已变，旧缩略图不匹配）
        dur = getattr(clip, "source_duration", 0.0)
        cnt, th = self._thumb_params(dur)
        self._start_thumbnail_worker(clip, cnt, th)

    # ── 定格帧 ──
    def _on_freeze_frame(self, clip, playhead_pos: float):
        """在当前播放头位置截取一帧，截断原片段并插入3s定格帧"""
        from core.edit_engine import VideoClip
        from utils.ffmpeg_utils import get_ffmpeg_path
        import time as _time

        ffmpeg = get_ffmpeg_path()
        src = clip.source_path
        if not src or not os.path.exists(src):
            QMessageBox.warning(self, "错误", "源文件不存在")
            return

        # 计算在源视频中的实际时间位置
        rel_time = playhead_pos - clip.timeline_start  # 相对于片段开始的时间
        if rel_time <= 0 or rel_time >= clip.duration * 0.99:
            QMessageBox.warning(self, "错误", "定格点需要在片段内部（不能是开头或结尾）")
            return
        src_time = clip.trim_start + rel_time * clip.speed  # 对应源视频中的时间

        # 保存当前播放头位置（避免后续修改导致跳帧）
        saved_head = playhead_pos

        # 生成输出文件名
        stamp = str(int(_time.time() * 1000))
        img_path = os.path.join("work_temp", f"freeze_{stamp}.png")
        vid_path = os.path.join("work_temp", f"freeze_{stamp}.mp4")
        os.makedirs("work_temp", exist_ok=True)

        try:
            # 提取帧为PNG（匹配原片段分辨率）
            subprocess.run([
                ffmpeg, "-y", "-ss", str(src_time), "-i", src,
                "-vframes", "1", "-q:v", "2", img_path
            ], capture_output=True, check=True)

            # image → 3s 定格视频
            subprocess.run([
                ffmpeg, "-y", "-loop", "1", "-i", img_path,
                "-c:v", "libx264", "-t", "3", "-pix_fmt", "yuv420p",
                "-r", "30", vid_path
            ], capture_output=True, check=True)

            # 获取生成的定格帧视频时长
            probe2 = subprocess.run([
                ffmpeg, "-i", vid_path
            ], capture_output=True, text=True)
            dur_match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", probe2.stderr or "")
            freeze_dur = 3.0
            if dur_match:
                h, m, s = int(dur_match[1]), int(dur_match[2]), float(dur_match[3])
                freeze_dur = h * 3600 + m * 60 + s

            # ── 定位原片段在轨道中的位置 ──
            td, idx_in_track = self._find_clip_in_timeline(clip)
            if not td or idx_in_track < 0:
                QMessageBox.warning(self, "错误", "找不到片段在时间线中的位置")
                return
            track = self.timeline.video_tracks[td.idx]

            # ── 保存原始 trim（用于后续关键帧重映射）──
            orig_trim_start = clip.trim_start
            orig_trim_end = clip.trim_end

            # ── 创建后半段（从定格点开始） ──
            # 后半段源时间：原 trim_end 不变，trim_start 跳到定格帧对应的源位置
            # timeline_start = 原片段开始 + rel_time + freeze_dur
            latter_src_start = src_time  # 后半段从定格帧的源位置开始
            latter_src_end = clip.trim_end  # 到原片段结束
            latter_timeline_start = playhead_pos + freeze_dur  # 时间线起点后移 3s
            latter = VideoClip.from_dict(clip.to_dict())
            latter.id = str(uuid.uuid4())[:8]
            latter.trim_start = latter_src_start
            latter.trim_end = latter_src_end
            latter.timeline_start = latter_timeline_start

            # ── 截断原片段（结束于定格点） ──
            self.timeline._save_history()
            clip.trim_end = src_time
            from core.edit_engine import rebase_clip_keyframes
            rebase_clip_keyframes(clip, orig_trim_start, orig_trim_end)
            rebase_clip_keyframes(latter, orig_trim_start, orig_trim_end)

            # ── 创建定格帧 VideoClip ──
            freeze_clip = VideoClip.from_dict(clip.to_dict())
            freeze_clip.id = str(uuid.uuid4())[:8]
            freeze_clip.source_path = os.path.abspath(vid_path)
            freeze_clip.source_duration = freeze_dur
            freeze_clip.trim_start = 0.0
            freeze_clip.trim_end = freeze_dur
            freeze_clip.timeline_start = playhead_pos
            freeze_clip.speed = 1.0
            freeze_clip.has_alpha = False  # 定格帧是截取的 JPEG，无源 alpha
            freeze_clip.keyframes = {}
            freeze_clip.out_transition = None

            # ── 插入：原片段后 → 定格帧 → 后半段 ──
            insert_idx = idx_in_track + 1
            track.insert(insert_idx, freeze_clip)
            track.insert(insert_idx + 1, latter)

            # ── 后移同轨及所有轨道中 start >= playhead_pos 的片段 ──
            for ti in range(len(self.timeline.video_tracks)):
                for c in self.timeline.video_tracks[ti]:
                    # 跳过刚处理过的三个片段
                    if c is clip or c is freeze_clip or c is latter:
                        continue
                    if c.timeline_start >= playhead_pos:
                        c.timeline_start += freeze_dur
            for ti in range(len(self.timeline.audio_tracks)):
                for c in self.timeline.audio_tracks[ti]:
                    if c.timeline_start >= playhead_pos:
                        c.timeline_start += freeze_dur
            for ti in range(len(self.timeline.subtitle_tracks)):
                for c in self.timeline.subtitle_tracks[ti]:
                    if c.timeline_start >= playhead_pos:
                        c.timeline_start += freeze_dur
                        c.timeline_end += freeze_dur

            # ── 恢复播放头位置（防止时间线跳帧） ──
            # 重新生成截断后的缩略图（clip 被截短了，latter 从新的 trim_start 开始）
            self._regenerate_thumbnails(clip)
            self._regenerate_thumbnails(latter)
            self.timeline.changed.emit()
            self.timeline_widget.set_playhead(saved_head + freeze_dur + 0.5)

            self.status_msg.emit(f"定格帧已插入（{freeze_dur:.1f}s） ✓", "success")

        except subprocess.CalledProcessError as e:
            QMessageBox.warning(self, "错误", f"FFmpeg 定格帧失败：\n{e.stderr.decode() if e.stderr else str(e)}")
        except Exception as e:
            QMessageBox.warning(self, "错误", f"定格帧生成异常：{e}")

    # ── 提取当前帧到图层编辑 ──
    def _on_extract_frame_to_image_editor(self, clip, playhead_pos: float):
        """在播放头位置截取当前帧（保留 alpha），发送到图层编辑作为新图层。"""
        from utils.ffmpeg_utils import get_ffmpeg_path
        import time as _time

        ffmpeg = get_ffmpeg_path()
        src = clip.source_path
        if not src or not os.path.exists(src):
            QMessageBox.warning(self, "错误", "源文件不存在")
            return

        rel_time = playhead_pos - clip.timeline_start
        if rel_time <= 0 or rel_time >= clip.duration * 0.99:
            QMessageBox.warning(self, "错误", "提取点需要在片段内部（不能是开头或结尾）")
            return
        src_time = clip.trim_start + rel_time * clip.speed

        has_alpha = getattr(clip, "has_alpha", False)
        # 透明视频：先探测（clip.has_alpha 可能未设置）
        if not has_alpha:
            try:
                from utils.alpha_video import probe_has_alpha
                has_alpha = probe_has_alpha(src)
            except Exception:
                has_alpha = False

        stamp = str(int(_time.time() * 1000))
        os.makedirs("work_temp", exist_ok=True)
        img_path = os.path.join("work_temp", f"frame_{stamp}.png")

        pix = "rgba" if has_alpha else "rgb24"
        try:
            subprocess.run([
                ffmpeg, "-y", "-ss", str(src_time), "-i", src,
                "-vframes", "1", "-pix_fmt", pix, img_path
            ], capture_output=True, check=True)
        except subprocess.CalledProcessError as e:
            QMessageBox.warning(self, "错误",
                                f"提取当前帧失败：\n{e.stderr.decode() if e.stderr else str(e)}")
            return

        # 发送到图层编辑
        mw = self.window()
        if mw is None or not hasattr(mw, "image_editor"):
            QMessageBox.warning(self, "错误", "未找到图层编辑模块")
            return
        mw.image_editor.add_image_from_path(img_path)
        # 跳转并高亮图层编辑 Tab（stacked index = 8）
        mw._xh_jump_tab(8)
        self.status_msg.emit("当前帧已提取到图层编辑 ✓", "success")

    # ── 倒放 ──
    def _on_reverse(self, clip):
        """为选中片段生成倒放版本，替换源路径"""
        from core.edit_engine import VideoClip
        from utils.ffmpeg_utils import get_ffmpeg_path
        import time as _time

        ffmpeg = get_ffmpeg_path()
        src = clip.source_path
        if not src or not os.path.exists(src):
            QMessageBox.warning(self, "错误", "源文件不存在")
            return

        stamp = str(int(_time.time() * 1000))
        out_path = os.path.abspath(os.path.join("work_temp", f"reverse_{stamp}.mp4"))
        os.makedirs("work_temp", exist_ok=True)

        try:
            # 先裁剪片段，再倒放
            tmp_path = os.path.join("work_temp", f"reverse_trim_{stamp}.mp4")
            subprocess.run([
                ffmpeg, "-y", "-ss", str(clip.trim_start), "-i", src,
                "-to", str(clip.trim_end - clip.trim_start),
                "-c", "copy", tmp_path
            ], capture_output=True, check=True)

            # 倒放
            subprocess.run([
                ffmpeg, "-y", "-i", tmp_path,
                "-vf", "reverse", "-af", "areverse",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-crf", "18", out_path
            ], capture_output=True, check=True)

            # 获取新时长
            probe = subprocess.run([
                ffmpeg, "-i", out_path
            ], capture_output=True, text=True)
            import re
            dur_match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", probe.stderr or "")
            new_dur = clip.source_duration
            if dur_match:
                h, m, s = int(dur_match[1]), int(dur_match[2]), float(dur_match[3])
                new_dur = h * 3600 + m * 60 + s

            # 更新片段
            self.timeline._save_history()
            clip.source_path = out_path
            clip.source_duration = new_dur
            clip.trim_start = 0.0
            clip.trim_end = new_dur
            clip.keyframes.clear()  # 倒放后视频内容重新排序，旧关键帧不再有意义

            # 清理临时文件
            try:
                os.remove(tmp_path)
            except OSError:
                pass

            self.timeline.changed.emit()
            # 倒放后源文件变了，重新生成缩略图
            self._regenerate_thumbnails(clip)
            self.status_msg.emit(f"倒放完成 ✓", "success")

        except subprocess.CalledProcessError as e:
            QMessageBox.warning(self, "错误", f"FFmpeg 倒放失败：\n{e.stderr.decode() if e.stderr else str(e)}")
        except Exception as e:
            QMessageBox.warning(self, "错误", f"倒放异常：{e}")

    # ── AI 生成视频（时间线右键）──
    def _on_ai_video_gen(self, clip):
        """旧信号兼容：剪辑台不再发起视频生成。"""
        self.status_msg.emit("请把视频送回 AI 制片画布后使用视频节点", "info")

    def _extract_clip_frame(self, clip, rel_time: float) -> str | None:
        """在 clip 内部 rel_time（时间线秒）处抽一帧 PNG 到 work_temp/，返回路径；失败返回 None。"""
        try:
            from utils.ffmpeg_utils import get_ffmpeg_path
            import time as _time

            ffmpeg = get_ffmpeg_path()
            src = getattr(clip, "source_path", None)
            if not src or not os.path.exists(src):
                return None
            rel_time = max(0.0, min(rel_time, clip.duration * 0.999))
            src_time = clip.trim_start + rel_time * clip.speed

            stamp = str(int(_time.time() * 1000))
            os.makedirs("work_temp", exist_ok=True)
            out = os.path.abspath(os.path.join("work_temp", f"aiframe_{stamp}.png"))
            subprocess.run(
                [ffmpeg, "-y", "-ss", str(src_time), "-i", src,
                 "-vframes", "1", "-pix_fmt", "rgb24", out],
                capture_output=True, check=True, timeout=30)
            if os.path.exists(out) and os.path.getsize(out) > 0:
                return out
        except Exception:
            pass
        return None

    def _extract_clip_frame_at_playhead(self, clip) -> str | None:
        """在当前播放头位置抽一帧 PNG 到 work_temp/，返回路径；失败返回 None。"""
        playhead = self.timeline_widget.get_playhead()
        rel_time = playhead - clip.timeline_start
        return self._extract_clip_frame(clip, rel_time)

    def _clip_at_playhead(self, ph: float):
        """返回播放头 ph 所在的视频/叠加轨片段；不在任何片段上则返回 None。"""
        canvas = self.timeline_widget._canvas
        for td in canvas._tracks:
            if td.kind != "video":
                continue
            for c in canvas._clips_of(td):
                start = c.timeline_start
                end = start + c.duration
                if start <= ph < end:
                    return c
        return None

    def _detect_cut_at_playhead(self, ph: float, thresh: float = 0.1):
        """检测播放头是否落在「某视频片段结束 → 紧接另一视频片段开始」的截断点。

        返回 (preceding_clip, following_clip)；否则 (None, None)。
        """
        canvas = self.timeline_widget._canvas
        for td in canvas._tracks:
            if td.kind != "video":
                continue
            clips = sorted(canvas._clips_of(td), key=lambda c: c.timeline_start)
            for i, c in enumerate(clips):
                c_end = c.timeline_start + c.duration
                if abs(c_end - ph) <= thresh:
                    nxt = clips[i + 1] if i + 1 < len(clips) else None
                    if nxt is not None and abs(nxt.timeline_start - ph) <= thresh:
                        return c, nxt
        return None, None

    def _on_capture_frame_for_ai(self, slot: int = 1):
        """📷 截取帧 → 填到视频生成参考图 slot。

        首尾帧模式：若播放头在 A片段末尾→B片段开头 的截断点，
        slot=1 截 A末帧（首帧）、slot=2 截 B首帧（尾帧）。
        非首尾帧模式 / 未在截断点：截当前帧。
        """
        self.status_msg.emit("截帧生成已迁移到 AI 制片画布", "info")
        return
        ph = self.timeline_widget.get_playhead()
        vp = self._ai_assistant._video_panel
        is_first_last = vp._first_last_toggle.isChecked()

        ref = None
        label = {1: "参考图 1", 2: "参考图 2"}.get(slot, f"参考图 {slot}")

        if is_first_last:
            preceding, following = self._detect_cut_at_playhead(ph)
            if slot == 1 and preceding is not None:
                ref = self._extract_clip_frame(preceding, max(0, preceding.duration - 0.05))
                label = "参考图 1（首帧）"
            elif slot == 2 and following is not None:
                ref = self._extract_clip_frame(following, 0.0)
                label = "参考图 2（尾帧）"

        if ref is None:
            clip = self._clip_at_playhead(ph)
            if clip is not None:
                ref = self._extract_clip_frame_at_playhead(clip)

        if ref:
            vp.set_ref(slot, ref)
            self.open_ai_assistant("video")
            self.status_msg.emit(f"已截取当前帧作为{label} ✓", "info")
            return

        self.open_ai_assistant("video")
        self.status_msg.emit("播放头未落在视频片段上，请先移动到片段内再截取", "warn")

    # ── 查找片段在时间线中的位置 ──
    def _find_clip_in_timeline(self, clip):
        """返回 (TrackDesc, index_in_track) 或 (None, -1)"""
        canvas = self.timeline_widget._canvas
        for td in canvas._tracks:
            clips = canvas._clips_of(td)
            for i, c in enumerate(clips):
                if c is clip:
                    return td, i
        return None, -1

    def _on_subtitle_edit_requested(self, clip: SubtitleBlock):
        """右键编辑字幕 → 在预览虚线框内直接编辑"""
        self._edit_subtitle_inline(clip)

    def _edit_subtitle_inline(self, clip: SubtitleBlock):
        """双击/右键编辑字幕 → 在预览虚线框内直接编辑（无弹窗）"""
        self.props_panel.set_selection(clip, "subtitle")
        self.preview._selected_sub = clip
        self.preview._selected_video_clip = None
        # 跳转到字幕位置
        pos = clip.timeline_start + 0.05
        self.preview._current_sec = pos
        self.timeline_widget.set_playhead(pos)
        self.preview._seq_state = None
        self.preview._async_fetch(pos)
        # 等帧渲染后进入画布内联编辑
        from PyQt6.QtCore import QTimer as _Qt
        _Qt.singleShot(150, lambda: self.preview._show_sub_editor(clip))

    # ─────────────────────────────────────────
    # AI 工具（由右键菜单触发）
    # ─────────────────────────────────────────
    def _get_clip_path(self, clip) -> str:
        return getattr(clip, "source_path", "")

    def _ai_busy(self) -> bool:
        return self._ai_worker is not None and self._ai_worker.isRunning()

    def _on_ai_progress(self, pct: int, msg: str):
        """AI 操作进度回调"""
        self._ai_progress.setVisible(True)
        self._ai_progress.setValue(pct)
        self._ai_status_bar.setVisible(True)
        self._ai_status_bar.setText(msg)

    def _on_ai_error(self, msg: str):
        """AI 操作错误回调"""
        self._clear_ai_progress()
        self.status_msg.emit(msg, "error")

    def _clear_ai_progress(self):
        """隐藏 AI 进度条"""
        self._ai_progress.setVisible(False)
        self._ai_progress.setValue(0)
        self._ai_status_bar.setVisible(False)
        self._ai_status_bar.setText("")

    def _stop_ai_worker(self):
        """安全停止并断开旧 AI Worker，防止 QThread 在 isRunning 时被替换/GC"""
        w = self._ai_worker
        if w is None:
            return
        # 断开所有信号连接，阻止回调在新 worker 启动后意外触发
        # _SubTransWorker 没有 progress 信号，用 AttributeError 兜底
        try:
            w.progress.disconnect()
        except (TypeError, RuntimeError, AttributeError):
            pass
        try:
            w.finished.disconnect()
        except (TypeError, RuntimeError, AttributeError):
            pass
        try:
            w.error.disconnect()
        except (TypeError, RuntimeError, AttributeError):
            pass
        if w.isRunning():
            w.requestInterruption()
            w.quit()
            if not w.wait(3000):
                w.terminate()
                w.wait(1000)
        w.deleteLater()
        self._ai_worker = None
        self._clear_ai_progress()

    def _do_separate_selected(self):
        """Ctrl+Shift+S：视频片段→提取/恢复原声(toggle)，支持多选"""
        # 收集所有选中的视频片段
        clips_to_process = []
        c = self.props_panel.current_clip()
        if isinstance(c, VideoClip):
            clips_to_process.append(c)
        # 也检查时间线上的多选（marquee / Ctrl+点击）
        canvas = self.timeline_widget._canvas
        if canvas._marquee_selected:
            for clip, td in canvas._marquee_selected:
                if isinstance(clip, VideoClip) and clip not in clips_to_process:
                    clips_to_process.append(clip)
        # 如果没有通过上述方式获取到，但画布有选中片段且是视频，也加入
        if not clips_to_process and canvas._selected_clip and isinstance(canvas._selected_clip, VideoClip):
            clips_to_process.append(canvas._selected_clip)

        for clip in clips_to_process:
            self._toggle_video_audio(clip)

    def _toggle_video_audio(self, clip: VideoClip):
        """切换视频原声提取/恢复"""
        if not clip.source_path or not os.path.exists(clip.source_path):
            return
        clip_key = str(id(clip))

        if clip_key in self._separated_state:
            # 已分离 → 恢复：不删除音频片段，只取消静音
            self._separated_state.pop(clip_key)
            clip.mute = False
            self.timeline.changed.emit()
            self.timeline_widget.rebuild_canvas()
            return

        # 未分离 → 提取原声到音频轨
        import subprocess
        stem = Path(clip.source_path).stem
        try:
            from config import WORK_DIR
            out_audio = str(WORK_DIR / f"{stem}_audio.m4a")
        except Exception:
            out_audio = os.path.join(os.path.dirname(clip.source_path), f"{stem}_audio.m4a")

        try:
            from utils.ffmpeg_utils import get_ffmpeg_path
            ffmpeg = get_ffmpeg_path()
        except Exception:
            ffmpeg = "ffmpeg"

        try:
            r = subprocess.run(
                [ffmpeg, "-y", "-i", clip.source_path,
                 "-vn", "-c:a", "aac", "-b:a", "192k", out_audio],
                capture_output=True, timeout=60
            )
            if r.returncode != 0:
                return  # 静默失败
        except Exception:
            logging.debug("_do_separate audio extraction failed", exc_info=True)
            return

        # 获取时长
        try:
            r2 = subprocess.run(
                [ffmpeg.replace("ffmpeg", "ffprobe"), "-v", "quiet",
                 "-print_format", "json", "-show_streams", out_audio],
                capture_output=True, text=True, timeout=10
            )
            import json as _json
            dur = 0.0
            data = _json.loads(r2.stdout)
            for s in data.get("streams", []):
                if "duration" in s:
                    dur = float(s["duration"]); break
            if dur <= 0:
                # 用整段源时长兜底，而不是已裁剪的 clip.duration，
                # 否则 source_duration 被压短 → 分离音频无法向右拉长
                dur = getattr(clip, "source_duration", 0.0) or clip.duration
        except Exception:
            dur = getattr(clip, "source_duration", 0.0) or clip.duration

        # 加入音频轨0（自动创建）
        while 0 >= len(self.timeline.audio_tracks):
            self.timeline.add_audio_track()
        start = clip.timeline_start
        # 对齐视频片段的源窗口：音频沿用视频的 [trim_start, trim_end]，
        # source_duration 保留整段提取音频时长 —— 这样左右都留有延展空间，
        # 分离后的音频可以「向右拉长 / 向左缩短」（J/L cut），不再被锁死。
        vs = float(getattr(clip, "trim_start", 0.0) or 0.0)
        ve = float(getattr(clip, "trim_end", dur) or dur)
        if ve <= vs or ve > dur:
            # 窗口非法（如视频未探测到 trim）→ 退化为整段
            ve = dur
            vs = 0.0
        ac = AudioClip(source_path=out_audio, source_duration=dur,
                       trim_start=vs, trim_end=ve, timeline_start=start,
                       label=f"{stem} 原声")
        self.timeline.add_audio_clip(ac, track_idx=0)

        clip.mute = True
        self._separated_state[clip_key] = out_audio
        self.timeline.changed.emit()
        self.timeline_widget.rebuild_canvas()

    def _do_separate_clip(self, clip):
        """AI 人声/背景分离（视频或音频片段均可）"""
        if self._ai_busy():
            return
        path = self._get_clip_path(clip)
        if not path or not os.path.exists(path):
            return

        clip_start = clip.timeline_start
        self.status_msg.emit(f"正在分离人声：{Path(path).name}…", "info")
        self._stop_ai_worker()  # 安全停止旧 Worker
        w = _SepWorker(path)
        w.progress.connect(self._on_ai_progress)
        w.finished.connect(lambda vp, bp: self._on_sep_done(path, vp, bp, clip_start))
        w.error.connect(lambda e: self._on_ai_error(f"分离失败: {e[:80]}"))
        self._ai_worker = w
        w.start()

    def _on_sep_done(self, src_path: str, vocals_wav: str, bgm_wav: str, video_start: float = 0.0):
        try:
            from config import WORK_DIR
            stem = Path(src_path).stem
            nv = str(WORK_DIR / f"{stem}_vocals.wav")
            nb = str(WORK_DIR / f"{stem}_bgm.wav")
            if Path(vocals_wav).exists(): shutil.move(vocals_wav, nv)
            if Path(bgm_wav).exists():    shutil.move(bgm_wav, nb)
            self._vocals_path = nv
            self._bgm_path = nb
        except Exception:
            self._vocals_path = vocals_wav
            self._bgm_path = bgm_wav
        if self._vocals_path:
            self._separated_vocals[os.path.abspath(src_path)] = self._vocals_path

        self._clear_ai_progress()
        self.status_msg.emit("人声分离完成 ✓", "success")

        # ── 人声/背景音对齐视频片段位置，不排到轨道末尾 ──
        def _add_audio_track(path: str, track_idx: int, name: str):
            if not path or not Path(path).exists():
                return
            # 获取时长
            try:
                import subprocess, json as _json
                ff = "ffprobe"
                r = subprocess.run(
                    [ff, "-v", "quiet", "-print_format", "json",
                     "-show_streams", path],
                    capture_output=True, text=True, timeout=10
                )
                dur = 0.0
                if r.returncode == 0:
                    data = _json.loads(r.stdout)
                    for s in data.get("streams", []):
                        if "duration" in s:
                            dur = float(s["duration"]); break
            except Exception:
                dur = 60.0  # fallback

            # 确保有足够多的轨道
            while track_idx >= len(self.timeline.audio_tracks):
                self.timeline.add_audio_track()

            # 对齐视频片段的起始位置（自动对齐时腾空间）
            actual_start = self.timeline._make_room_on_track("audio", track_idx, video_start, dur)
            clip = AudioClip(source_path=path, source_duration=dur,
                             trim_start=0.0, trim_end=dur, timeline_start=actual_start,
                             label=name)
            self.timeline.add_audio_clip(clip, track_idx=track_idx)
            self.timeline_widget.rebuild_canvas()

        _add_audio_track(self._vocals_path, 0, "人声")
        _add_audio_track(self._bgm_path,    1, "背景音")

    def _selected_video_clip(self):
        clip = self.props_panel.current_clip()
        if not isinstance(clip, VideoClip):
            clip = getattr(self.timeline_widget._canvas, "_selected_clip", None)
        return clip if isinstance(clip, VideoClip) else None

    def _start_scene_detection_for_selection(self):
        clip = self._selected_video_clip()
        if not isinstance(clip, VideoClip):
            QMessageBox.information(self, "智能分镜", "请先在时间线中选中一个视频片段。")
            return
        self._start_scene_detection(clip)

    def _start_scene_detection(self, clip: VideoClip):
        if self._ai_busy():
            QMessageBox.information(self, "智能分镜", "另一个 AI/分析任务正在运行，请稍候。")
            return
        if not clip.source_path or not os.path.exists(clip.source_path):
            QMessageBox.warning(self, "智能分镜", "找不到该视频的源文件。")
            return
        if clip.duration < 0.6:
            QMessageBox.information(self, "智能分镜", "片段太短，无需继续截开。")
            return
        dialog = SceneDetectDialog(clip.duration, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        config = dialog.config()
        self._scene_detect_clip = clip
        self._stop_ai_worker()
        worker = _SceneDetectWorker(clip, **config)
        worker.progress.connect(self._on_ai_progress)
        worker.finished.connect(lambda cuts, source=clip: self._on_scene_detect_done(source, cuts))
        worker.error.connect(self._on_scene_detect_error)
        self._ai_worker = worker
        self.status_msg.emit(
            f"智能分镜：正在分析 {Path(clip.source_path).name}…", "info")
        worker.start()

    def _on_scene_detect_error(self, error: str):
        self._clear_ai_progress()
        logging.error("scene detection failed: %s", error)
        QMessageBox.warning(self, "智能分镜失败", error.splitlines()[0][:240])
        self.status_msg.emit("智能分镜检测失败", "error")

    def _on_scene_detect_done(self, clip: VideoClip, cuts: list):
        self._clear_ai_progress()
        track = next((candidate for candidate in self.timeline.video_tracks
                      if any(item is clip for item in candidate)), None)
        if track is None:
            self.status_msg.emit("检测完成，但原视频片段已被修改或删除", "warn")
            return
        if not cuts:
            QMessageBox.information(
                self, "智能分镜", "没有检测到足够明显的画面跳变。\n\n"
                "可以重新检测并选择“灵敏”，或降低自定义阈值。")
            self.status_msg.emit("智能分镜：未检测到有效切点", "info")
            return
        if len(cuts) > 100:
            answer = QMessageBox.question(
                self, "智能分镜",
                f"检测到 {len(cuts)} 个切点，将生成 {len(cuts) + 1} 个片段。\n"
                "结果可能比较碎，仍要继续吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._apply_scene_cuts(clip, track, cuts)

    def _apply_scene_cuts(self, clip: VideoClip, track: list, cuts: list):
        speed = max(clip.speed, 0.01)
        source_boundaries = [clip.trim_start]
        source_boundaries.extend(
            clip.trim_start + float(row["time"]) for row in cuts)
        source_boundaries.append(clip.trim_end)
        source_boundaries = sorted(set(round(value, 6) for value in source_boundaries))
        if len(source_boundaries) < 3:
            return

        self.timeline._save_history()
        new_clips = []
        for index, (source_start, source_end) in enumerate(
                zip(source_boundaries, source_boundaries[1:])):
            if source_end - source_start < 0.03:
                continue
            clone = VideoClip.from_dict(clip.to_dict())
            clone.id = str(uuid.uuid4())[:8]
            old_trim_start, old_trim_end = clone.trim_start, clone.trim_end
            clone.trim_start = source_start
            clone.trim_end = source_end
            clone.timeline_start = (
                clip.timeline_start + (source_start - clip.trim_start) / speed)
            rebase_clip_keyframes(clone, old_trim_start, old_trim_end)
            clone.out_transition = (
                clip.out_transition if index == len(source_boundaries) - 2 else None)
            if hasattr(clip, "thumbnail"):
                clone.thumbnail = clip.thumbnail
            if hasattr(clip, "thumbnails"):
                clone.thumbnails = clip.thumbnails
            new_clips.append(clone)

        if len(new_clips) < 2:
            return
        track[:] = [item for item in track if item is not clip]
        track.extend(new_clips)
        track.sort(key=lambda item: item.timeline_start)
        self.timeline.changed.emit()
        self.timeline_widget.rebuild_canvas()
        self.timeline_widget._canvas._selected_clip = new_clips[0]
        self.timeline_widget.set_playhead(new_clips[0].timeline_start)
        self.props_panel.set_selection(new_clips[0], "video")
        self.preview.seek(new_clips[0].timeline_start, force=True)
        for new_clip in new_clips:
            self._regenerate_thumbnails(new_clip)
        self.status_msg.emit(
            f"智能分镜完成：检测到 {len(new_clips) - 1} 个切点，生成 {len(new_clips)} 个片段 ✓",
            "success")

    def _start_text_rough_cut(self):
        clip = self._selected_video_clip()
        if not isinstance(clip, VideoClip):
            QMessageBox.information(self, "文字粗剪", "请先在时间线中选中一个视频片段。")
            return
        self._do_asr_clip(clip)

    def _do_asr_clip(self, clip):
        if self._ai_busy():
            QMessageBox.information(self, "提示", "AI 工作中，请稍候。"); return
        # 记录偏移量：ASR时间戳需要加上此值才能映射到时间线位置
        self._asr_offset = clip.timeline_start - clip.trim_start
        self._asr_clip = clip
        # 只复用“当前源视频”对应的人声文件，避免误拿上一次分离的其他视频。
        from core.edit_engine import VideoClip
        source_audio = self._get_clip_path(clip)
        separated = self._separated_vocals.get(os.path.abspath(source_audio)) if source_audio else None
        audio = separated if separated and Path(separated).exists() else source_audio
        if not audio:
            QMessageBox.warning(self, "错误", "找不到音频文件"); return

        self.status_msg.emit(f"识别中：{Path(audio).name}…", "info")
        self._stop_ai_worker()  # 安全停止旧 Worker
        w = _ASRWorker(audio)
        w.progress.connect(self._on_ai_progress)
        w.finished.connect(self._on_asr_done)
        w.error.connect(lambda e: self._on_ai_error(f"识别失败: {e[:80]}"))
        self._ai_worker = w
        w.start()

    def _on_asr_done(self, entries):
        self._clear_ai_progress()
        # 应用 ASR 偏移量（Whisper 时间戳 + offset → 时间线位置）
        offset = getattr(self, '_asr_offset', 0.0)
        # 去除标点符号（中文+英文标点，保留空格和字母数字）
        _strip_punct = str.maketrans('', '', '，。！？、；：""''（）【】《》…—·,.:;!?\"\'()[]{}<>…—–-')

        # 收集轨道上已有字幕（叠加不清除，保留原有样式）
        existing = []
        for track in self.timeline.subtitle_tracks:
            for b in track:
                d = b.to_dict()
                existing.append({
                    "start": d["timeline_start"], "end": d["timeline_end"], "text": d["text"],
                    "from_asr": d["from_asr"], "word_animation": d["word_animation"],
                    "fill_enabled": d["fill_enabled"], "background_color": d["background_color"],
                    "color": d["color"], "outline_color": d["outline_color"],
                    "outline_width": d["outline_width"], "font_family": d["font_family"],
                    "font_size": d["font_size"], "font_bold": d["font_bold"],
                    "font_italic": d["font_italic"], "position": d["position"],
                    "margin_v": d["margin_v"], "align": d["align"],
                    "pos_x": d["pos_x"], "pos_y": d["pos_y"], "scale": d["scale"],
                    "rotation": d["rotation"], "opacity": d["opacity"],
                    "word_timings": d["word_timings"],
                })

        # 文字粗剪只处理当前视频的有效源区间；Whisper 返回的是整个源文件时间戳。
        clip = getattr(self, "_asr_clip", None)
        is_video_cut = isinstance(clip, VideoClip)
        trim_start = float(getattr(clip, "trim_start", 0.0))
        trim_end = float(getattr(clip, "trim_end", float("inf")))
        new_subs = []
        for entry in entries:
            source_start = max(trim_start, float(entry.start)) if is_video_cut else float(entry.start)
            source_end = min(trim_end, float(entry.end)) if is_video_cut else float(entry.end)
            text = entry.text.translate(_strip_punct).strip()
            if source_end - source_start < 0.03 or not text:
                continue
            timeline_start = (
                clip.timeline_start + (source_start - clip.trim_start) / max(clip.speed, 0.01)
                if is_video_cut else source_start + offset)
            timeline_end = (
                clip.timeline_start + (source_end - clip.trim_start) / max(clip.speed, 0.01)
                if is_video_cut else source_end + offset)
            new_subs.append({
                "start": timeline_start,
                "end": timeline_end,
                "source_start": source_start,
                "source_end": source_end,
                "text": text,
                "_keep": True,
            })
        self._orig_subs = new_subs if is_video_cut else existing + new_subs
        self._trans_subs = []
        self.status_msg.emit(f"识别完成：{len(new_subs)} 条有效语句 ✓", "success")

        # 音频片段仍沿用原来的识别即加字幕；视频片段先交给文字粗剪弹窗选择。
        if new_subs and not is_video_cut:
            self._sync_subs_to_timeline(self._orig_subs)
            self.status_msg.emit(f"识别完成，已同步 {len(new_subs)} 条字幕到轨道 ✓", "success")

        # 打开字幕管理弹窗（供预览/翻译）
        self._open_subtitle_dialog()

    # ─────────────────────────────────────────
    # 字幕管理弹窗
    # ─────────────────────────────────────────
    def _open_subtitle_dialog(self):
        # 清理画布上的内联编辑/选中状态，避免模态弹窗期间残留引用导致播放异常
        if getattr(self.preview, '_editing_sub', None) is not None:
            self.preview._hide_sub_editor(save=True)
        self.preview._selected_sub = None
        self.preview._sub_interaction = None
        self.preview._seq_state = None
        self.preview._async_fetch(self.preview._current_sec)
        active_clip = getattr(self, "_asr_clip", None)
        rough_cut_enabled = isinstance(active_clip, VideoClip) and any(
            active_clip is clip for track in self.timeline.video_tracks for clip in track)
        dlg = SubtitleManagerDialog(
            self._orig_subs, self._trans_subs, self,
            rough_cut_enabled=rough_cut_enabled)
        dlg.sync_to_timeline.connect(self._sync_subs_to_timeline)
        dlg.rough_cut_requested.connect(self._apply_text_rough_cut)
        dlg.preview_requested.connect(self._preview_rough_cut_sentence)
        dlg.exec()
        # 粗剪会替换原 clip，并在槽函数里写入重排后的字幕；此时不要再被弹窗旧数据覆盖。
        source_clip_still_exists = any(
            active_clip is clip for track in self.timeline.video_tracks for clip in track)
        if not rough_cut_enabled or source_clip_still_exists:
            self._orig_subs = dlg._orig
            self._trans_subs = dlg._trans

    def _preview_rough_cut_sentence(self, sec: float):
        self.timeline_widget.set_playhead(sec)
        self.preview.seek(sec, force=True)

    def _sync_subs_to_timeline(self, subs: list, save_history: bool = True):
        # 先清理画布上内联编辑/选中状态，避免 rebuild 后 _editing_sub/_selected_sub 变成野指针
        if getattr(self.preview, '_editing_sub', None) is not None:
            self.preview._hide_sub_editor(save=False)
        self.preview._selected_sub = None
        self.preview._sub_interaction = None
        self.preview._seq_state = None

        if save_history:
            self.timeline._save_history()  # 保存 undo 快照
        for track in self.timeline.subtitle_tracks:
            track.clear()
        if not self.timeline.subtitle_tracks:
            self.timeline.subtitle_tracks.append([])
            self.timeline.subtitle_track_info.append(TrackInfo("字幕1"))
        for s in subs:
            block = SubtitleBlock(
                text=s.get("text", ""),
                timeline_start=s.get("start", s.get("timeline_start", 0.0)),
                timeline_end=s.get("end", s.get("timeline_end", s.get("start", 0.0) + 3.0)),
                from_asr=s.get("from_asr", True),
                word_animation=s.get("word_animation", True),
                fill_enabled=s.get("fill_enabled", False),
                background_color=s.get("background_color", ""),
                color=s.get("color", "#FFFFFF"),
                outline_color=s.get("outline_color", "#000000"),
                outline_width=s.get("outline_width", 0),
                font_family=s.get("font_family", "Microsoft YaHei"),
                font_size=s.get("font_size", 15),
                font_bold=s.get("font_bold", False),
                font_italic=s.get("font_italic", False),
                position=s.get("position", "bottom"),
                margin_v=s.get("margin_v", 60),
                align=s.get("align", "center"),
                pos_x=s.get("pos_x"),
                pos_y=s.get("pos_y"),
                scale=s.get("scale", 1.0),
                rotation=s.get("rotation", 0.0),
                opacity=s.get("opacity", 1.0),
                word_timings=s.get("word_timings", []),
            )
            self.timeline.subtitle_tracks[0].append(block)
        self.timeline.changed.emit()
        self.timeline_widget.rebuild_canvas()
        self.status_msg.emit(f"已同步 {len(subs)} 条字幕到时间线", "success")

    def _apply_text_rough_cut(self, selected_subs: list, padding: float,
                              compact: bool, add_subtitles: bool):
        clip = getattr(self, "_asr_clip", None)
        track = None
        for candidate_track in self.timeline.video_tracks:
            if any(clip is item for item in candidate_track):
                track = candidate_track
                break
        if not isinstance(clip, VideoClip) or track is None:
            QMessageBox.warning(self, "文字粗剪", "原视频片段已不存在，请重新选中视频并识别。")
            return

        from core.text_rough_cut import build_cut_plan
        ranges, remapped_subs = build_cut_plan(
            selected_subs,
            source_offset=clip.timeline_start - clip.trim_start / max(clip.speed, 0.01),
            trim_start=clip.trim_start,
            trim_end=clip.trim_end,
            timeline_start=clip.timeline_start,
            speed=clip.speed,
            padding=padding,
            compact=compact,
        )
        if not ranges:
            QMessageBox.information(self, "文字粗剪", "勾选内容没有形成有效剪辑区间。")
            return

        old_end = clip.timeline_end
        old_duration = clip.duration
        self.timeline._save_history()
        new_clips = []
        for index, cut_range in enumerate(ranges):
            clone = VideoClip.from_dict(clip.to_dict())
            clone.id = str(uuid.uuid4())[:8]
            old_trim_start, old_trim_end = clone.trim_start, clone.trim_end
            clone.trim_start = cut_range["source_start"]
            clone.trim_end = cut_range["source_end"]
            clone.timeline_start = cut_range["timeline_start"]
            rebase_clip_keyframes(clone, old_trim_start, old_trim_end)
            clone.out_transition = clip.out_transition if index == len(ranges) - 1 else None
            if hasattr(clip, "thumbnail"):
                clone.thumbnail = clip.thumbnail
            if hasattr(clip, "thumbnails"):
                clone.thumbnails = clip.thumbnails
            new_clips.append(clone)

        track[:] = [item for item in track if item is not clip]
        track.extend(new_clips)
        track.sort(key=lambda item: item.timeline_start)

        # 压紧时同时把同轨后续素材整体左移，避免只压紧当前片段内部却留下尾部空洞。
        new_duration = sum(item.duration for item in new_clips)
        removed_duration = max(0.0, old_duration - new_duration)
        if compact and removed_duration > 0.001:
            for other in track:
                if other not in new_clips and other.timeline_start >= old_end - 0.001:
                    other.timeline_start = max(0.0, other.timeline_start - removed_duration)

        if add_subtitles:
            self._orig_subs = remapped_subs
            self._trans_subs = []
            self._sync_subs_to_timeline(remapped_subs, save_history=False)
        else:
            self.timeline.changed.emit()
            self.timeline_widget.rebuild_canvas()

        canvas = self.timeline_widget._canvas
        canvas._selected_clip = new_clips[0]
        self.timeline_widget.set_playhead(new_clips[0].timeline_start)
        self.props_panel.set_selection(new_clips[0], "video")
        self.preview.seek(new_clips[0].timeline_start, force=True)
        for new_clip in new_clips:
            self._regenerate_thumbnails(new_clip)
        self.status_msg.emit(
            f"文字粗剪完成：生成 {len(new_clips)} 个片段，删除约 {removed_duration:.1f} 秒 ✓",
            "success")
        sender = self.sender()
        if isinstance(sender, SubtitleManagerDialog):
            sender.accept()

    # ─────────────────────────────────────────
    # 工程保存/加载 (.cep JSON)
    # ─────────────────────────────────────────
    def _build_project_dict(self) -> dict:
        """构造工程序列化 dict（手动保存与自动保存共用）"""
        return {
            "version": 1,
            "project_name": self._project_name,
            "canvas_ratio": self._canvas_ratio,
            "canvas_custom_size": self._custom_size,
            "active_timeline": self._active_tl_idx,
            "playhead": getattr(self.preview, '_current_sec', 0),
            "timelines": [tl.to_dict() for tl in self._timelines],
            "media_library": self.media_lib.get_paths(),
            "splitter_top": self._top_splitter.sizes(),
            "splitter_main": self._main_splitter.sizes(),
        }

    def _save_project(self):
        """保存所有时间线为 .cep JSON 文件。
        已有路径 → 直接保存（静默）；无路径 → 弹出文件对话框。
        """
        if self._project_path:
            path = self._project_path
        else:
            # 用工程名称作为默认文件名
            def_name = self._project_name if self._project_name != "未命名工程" else ""
            path, _ = QFileDialog.getSaveFileName(
                self, "保存工程", def_name, "CEP 工程文件 (*.cep)")
            if not path:
                return
        try:
            import json as _json
            data = self._build_project_dict()
            with open(path, "w", encoding="utf-8") as f:
                _json.dump(data, f, ensure_ascii=False, indent=2)
            self._project_path = path
            self._project_dirty = False
            # 首次保存时，如果用的是默认名称，从文件名自动取名
            if self._project_name == "未命名工程":
                stem = os.path.splitext(os.path.basename(path))[0]
                if stem:
                    self._project_name = stem
            self._update_proj_name_label()
            # 同步自动保存草稿（保持与已保存工程一致；退出时会被清理）
            self._write_autosave()
            self.status_msg.emit(f"工程已保存: {os.path.basename(path)}", "success")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))

    def _check_save_before_close(self) -> bool:
        """关闭/加载前检查是否保存。返回 True=可继续，False=取消。"""
        if not self._project_dirty:
            return True
        name = self._project_name
        box = QMessageBox(self)
        box.setWindowTitle("保存更改？")
        box.setText(f"「{name}」有未保存的更改。\n\n是否保存后再关闭？")
        box.setIcon(QMessageBox.Icon.Question)
        btn_save = box.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
        btn_discard = box.addButton("不保存", QMessageBox.ButtonRole.DestructiveRole)
        btn_cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_save)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_save:
            self._save_project()
            return not self._project_dirty  # 保存成功→可继续，取消→中止
        elif clicked is btn_discard:
            # 用户明确放弃更改 → 删除自动保存草稿，下次不再提醒恢复
            try:
                p = self._get_autosave_path()
                if p and os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
            return True
        else:  # Cancel
            return False

    def _restore_project_data(self, data: dict, source_path=None):
        """根据已解析的工程 dict 重建编辑器状态（手动打开 / 自动恢复共用）。

        source_path 仅用于「工程名缺失时回退为文件名」；自动恢复草稿无真实路径则传 None。
        """
        # 恢复工程名称
        loaded_name = data.get("project_name", "")
        if loaded_name:
            self._project_name = loaded_name
        elif source_path:
            stem = os.path.splitext(os.path.basename(source_path))[0]
            self._project_name = stem if stem else "未命名工程"
        self._update_proj_name_label()

        # 清空现有时间线
        for tw in self._tl_widgets:
            self._tl_stack.removeWidget(tw)
            tw.deleteLater()
        self._timelines.clear()
        self._tl_widgets.clear()

        # 重建标签栏
        while self._tl_tab_layout.count() > 1:
            w = self._tl_tab_layout.takeAt(0).widget()
            if w:
                w.deleteLater()

        # 加载时间线
        tl_dicts = data.get("timelines", [])
        for i, td in enumerate(tl_dicts):
            tl = EditTimeline.from_dict(td)
            self._timelines.append(tl)
            tw = TimelineWidget(tl, dubbing_config_provider=self._dubbing_cfg_provider)
            tw.setMinimumHeight(100)
            self._tl_widgets.append(tw)
            self._tl_stack.addWidget(tw)

            # 标签按钮
            tab_name = tl.name if tl.name else f"时间线 {i + 1}"
            btn = self._create_tab_button(i, tab_name, False)
            self._tl_tab_layout.insertWidget(
                self._tl_tab_layout.count() - 1, btn)

        if not self._timelines:
            # 无内容时创建默认时间线
            tl = EditTimeline()
            self._timelines.append(tl)
            tw = TimelineWidget(tl, dubbing_config_provider=self._dubbing_cfg_provider)
            tw.setMinimumHeight(100)
            self._tl_widgets.append(tw)
            self._tl_stack.addWidget(tw)

        # 恢复画布比例
        ratio = data.get("canvas_ratio")
        custom_size = data.get("canvas_custom_size")
        if custom_size:
            self._custom_size = tuple(custom_size) if isinstance(custom_size, list) else custom_size
        else:
            self._custom_size = None
        if ratio:
            self._canvas_ratio = tuple(ratio) if isinstance(ratio, list) else ratio
            self.preview.set_aspect_ratio(self._canvas_ratio)
            self._sync_ratio_combo()
        else:
            # 未保存画布比例（默认模式）→ 自动磁吸到时间线首个媒体尺寸
            self._snap_canvas_to_loaded_media()

        # 恢复素材库
        ml_paths = data.get("media_library", [])
        if ml_paths:
            self.media_lib.clear_and_load_paths(ml_paths)

        # 恢复分隔条布局
        st = data.get("splitter_top")
        if st and len(st) == 3:
            self._top_splitter.setSizes([int(x) for x in st])
        sm = data.get("splitter_main")
        if sm and len(sm) == 3:
            self._main_splitter.setSizes([int(x) for x in sm])

        # 切换到保存时的激活时间线
        active = data.get("active_timeline", 0)
        # 破除 _switch_timeline 的 early-return（idx==_active_tl_idx 时直接返回，
        # 会导致刚重建的 TimelineWidget 信号未绑定 → 素材库拖拽到轨道失效）。
        # 清空后 _active_tl_idx 仍是恢复前的旧值，故强制置 -1 使其走完整绑定路径。
        self._active_tl_idx = -1
        self._switch_timeline(min(active, len(self._timelines) - 1))
        self._rebuild_tab_bar()
        # 更新所有标签按钮为当前状态
        for i in range(self._tl_tab_layout.count()):
            w = self._tl_tab_layout.itemAt(i).widget()
            if isinstance(w, QPushButton) and w.isCheckable():
                w.setChecked(i == self._active_tl_idx)

        # 恢复播放头位置
        ph = data.get("playhead", 0)
        if ph > 0:
            self.timeline_widget.set_playhead(ph)
            self.preview.seek(ph)

        # 为加载的所有视频片段后台生成缩略图（遵守并发上限，超出时入队列）
        for tl in self._timelines:
            for track in tl.video_tracks:
                for clip in track:
                    if clip.source_path and os.path.exists(clip.source_path):
                        cnt, th = self._thumb_params(clip.source_duration)
                        self._start_thumbnail_worker(clip, cnt, th)
            for track in tl.audio_tracks:
                for clip in track:
                    if clip.source_path and os.path.exists(clip.source_path):
                        self._start_waveform_worker(clip)

    def _load_project(self):
        """从 .cep JSON 文件加载工程"""
        if not self._check_save_before_close():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "打开工程", "", "CEP 工程文件 (*.cep)")
        if not path:
            return
        self._project_path = path
        self._project_dirty = False
        # 清理旧 worker/状态，防止跨工程污染
        # ThumbnailWorker.run() 不进入事件循环，quit() 无法中断，需直接 wait()
        for w in list(self._thumb_workers):
            if w.isRunning():
                w.wait(3000)
        self._thumb_workers.clear()
        for w in list(self._waveform_workers):
            if w.isRunning():
                w.wait(3000)
        self._waveform_workers.clear()
        self._stop_ai_worker()
        # 停止当前播放
        self.preview.stop_audio()
        if self.timeline_widget.is_playing():
            self.timeline_widget.stop_playback()
        try:
            import json as _json
            with open(path, "r", encoding="utf-8") as f:
                data = _json.load(f)

            if data.get("version", 0) < 1:
                QMessageBox.warning(self, "版本不兼容", "工程文件版本过旧，无法加载。")
                return

            self._restore_project_data(data, source_path=path)
            self.status_msg.emit(f"工程已加载: {os.path.basename(path)}", "success")
            self.preview.seek(0)
        except Exception as e:
            import traceback
            QMessageBox.critical(self, "加载失败", f"{e}\n{traceback.format_exc()}")

    # ─────────────────────────────────────────
    # 自动保存（学习剪映：随时保存）
    # ─────────────────────────────────────────
    def _get_autosave_path(self) -> str:
        """返回自动保存草稿路径（目录不存在时创建）。失败返回空串。"""
        d = os.path.join(os.path.expanduser("~"), ".creativeenginepro", "autosave")
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as e:
            logging.debug("autosave dir create failed: %s", e)
            return ""
        return os.path.join(d, "last_draft.cep")

    def _write_autosave(self):
        """原子写自动保存草稿（临时文件 → os.replace）。失败静默。"""
        try:
            path = self._get_autosave_path()
            if not path:
                return
            import json as _json
            data = self._build_project_dict()
            data["_autosaved"] = True
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                _json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception as e:
            logging.debug("autosave write failed: %s", e)

    def _autosave(self):
        """防抖/定时兜底触发 → 仅在有未保存改动时落盘"""
        if not self._project_dirty:
            return
        self._write_autosave()

    def _maybe_restore_autosave(self):
        """启动后检测自动保存草稿，提示用户是否恢复（崩溃/异常退出保护）"""
        path = self._get_autosave_path()
        if not path or not os.path.exists(path):
            return
        try:
            import json as _json
            with open(path, "r", encoding="utf-8") as f:
                data = _json.load(f)
        except Exception:
            # 草稿损坏 → 直接删除，避免反复弹窗
            try:
                os.remove(path)
            except OSError:
                pass
            return
        name = data.get("project_name", "") or "未命名工程"
        box = QMessageBox(self)
        box.setWindowTitle("恢复未保存工程")
        box.setText(
            f"检测到上次自动保存的草稿「{name}」。\n\n"
            "是否恢复该草稿？")
        box.setIcon(QMessageBox.Icon.Question)
        btn_restore = box.addButton("恢复", QMessageBox.ButtonRole.AcceptRole)
        btn_discard = box.addButton("丢弃", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(btn_restore)
        box.exec()
        if box.clickedButton() is btn_restore:
            # 草稿没有真实工程路径，标记为未保存到命名文件
            self._project_path = None
            self._project_dirty = True
            self._restore_project_data(data)
            self._update_proj_name_label()
            self.status_msg.emit("已恢复自动保存草稿", "success")
            self.preview.seek(0)
        else:
            try:
                os.remove(path)
            except OSError:
                pass

    def _cleanup_autosave(self):
        """应用退出时调用：停定时器；若已干净保存到命名文件则清理草稿"""
        try:
            if getattr(self, '_autosave_fallback', None):
                self._autosave_fallback.stop()
            if getattr(self, '_autosave_timer', None):
                self._autosave_timer.stop()
        except Exception:
            pass
        # 仅当工程已干净保存到命名文件时，草稿才无保留意义
        if (not self._project_dirty) and self._project_path:
            try:
                p = self._get_autosave_path()
                if p and os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass

    # ─────────────────────────────────────────
    # 导出弹窗
    # ─────────────────────────────────────────
    def _open_export_dialog(self):
        if self._exporting:
            return
        self._exporting = True
        try:
            self._do_open_export_dialog()
        except Exception:
            self._exporting = False
            raise

    def _do_open_export_dialog(self):
        # 检测纯音频时间线 → 音频导出模式
        has_video = any(
            len(track) > 0
            for tl in self._timelines
            for track in tl.video_tracks
        )
        has_audio = any(
            len(track) > 0
            for tl in self._timelines
            for track in tl.audio_tracks
        ) or has_video  # 视频轨也含音频

        if not has_video and not has_audio:
            self._exporting = False
            QMessageBox.warning(self, "无内容", "时间线上没有任何片段，请先添加素材。")
            return

        if not has_video and has_audio:
            # ── 纯音频导出 ──
            dlg = AudioExportDialog(self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                self._exporting = False
                return
            cfg = dlg.get_settings()
            if not cfg["path"]:
                QMessageBox.warning(self, "未选择路径", "请先选择输出文件路径。")
                self._exporting = False
                return

            self._btn_export.setEnabled(False)
            self._prog_dlg = _ProgressDialog(self)
            self._prog_dlg.show()
            tl = self.timeline
            worker = _AudioExportWorker(tl, cfg["path"], cfg["bitrate"],
                                        cfg["sample_rate"], cfg["channels"])
            self._export_worker = worker
            self._prog_dlg.cancelled.connect(worker.cancel)
            worker.progress.connect(self._prog_dlg.update_progress)
            worker.finished.connect(self._on_export_finished)
            prog_dlg = self._prog_dlg  # 捕获局部引用防止悬空
            worker.finished.connect(lambda: prog_dlg.accept())
            worker.start()
            return

        # ── 视频导出 ──
        # 根据画布比例计算推荐分辨率
        canvas_size = self._get_canvas_resolution()
        proj_name = self._project_name if self._project_name != "未命名工程" else ""
        dlg = ExportDialog(self, canvas_size=canvas_size, default_name=proj_name)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            self._exporting = False
            return
        cfg = dlg.get_settings()
        if not cfg["path"]:
            QMessageBox.warning(self, "未选择路径", "请先选择输出文件路径。")
            self._exporting = False
            return

        self._btn_export.setEnabled(False)
        W, H = cfg["resolution"]
        fps = cfg["fps"]
        crf = cfg.get("crf", 18)

        # 字幕预览实际绘制在工作台当前可见画布（通常约 640×360）上，
        # font_size 也是基于这个画布调出来的。导出时必须以该可见画布为
        # 参考缩放到输出分辨率，才能保持字幕在画面中的相对大小一致。
        preview_w = int(getattr(self.preview, "_canvas_w", 0) or 0)
        preview_h = int(getattr(self.preview, "_canvas_h", 0) or 0)
        reference_size = ((preview_w, preview_h)
                          if preview_w > 0 and preview_h > 0
                          else (canvas_size or (W, H)))
        worker = FFmpegDirectExportWorker(
            self.timeline, cfg["path"], (W, H), fps, crf,
            reference_resolution=reference_size,
        )

        self._export_worker = worker

        # 进度显示用一个简单的进度弹窗
        self._prog_dlg = _ProgressDialog(self)
        self._prog_dlg.show()
        self._prog_dlg.cancelled.connect(worker.cancel)
        worker.progress.connect(self._prog_dlg.update_progress)
        worker.finished.connect(self._on_export_finished)
        prog_dlg = self._prog_dlg  # 捕获局部引用防止悬空
        worker.finished.connect(lambda: prog_dlg.accept())
        worker.start()

    def _on_export_finished(self, success: bool, result: str):
        self._exporting = False
        self._btn_export.setEnabled(True)
        if success:
            ext = os.path.splitext(result)[1].lower()
            type_name = "音频" if ext in (".mp3", ".wav") else "视频"
            QMessageBox.information(self, "导出完成", f"{type_name}已保存至：\n{result}")
        elif "用户取消" in result:
            pass  # 用户主动取消，无提示
        else:
            QMessageBox.critical(self, "导出失败", result)

    def closeEvent(self, event):
        """清理所有活跃 worker 和 timer，防止资源泄露"""
        # 停止缩略图 worker
        # ThumbnailWorker.run() 不进入事件循环，quit() 无法中断，需直接 wait()
        for w in list(self._thumb_workers):
            if w.isRunning():
                w.wait(3000)   # 最多等 3 秒让线程自然结束
        self._thumb_workers.clear()
        for w in list(self._waveform_workers):
            if w.isRunning():
                w.wait(3000)
        self._waveform_workers.clear()

        # 停止 AI worker（使用安全停止方法）
        self._stop_ai_worker()

        # 停止导出 worker
        if self._export_worker and self._export_worker.isRunning():
            self._export_worker.quit()
            self._export_worker.wait(3000)
            self._export_worker = None

        # 停止播放定时器（在 TimelineWidget 上，不在 EditorTab 上）
        for tw in self._tl_widgets:
            if hasattr(tw, '_play_timer') and tw._play_timer:
                tw._play_timer.stop()

        # 停止音频
        self.preview.stop_audio()
        event.accept()


# ─────────────────────────────────────────
# 工具函数 & 样式常量
# ─────────────────────────────────────────
def _fmt_s(sec: float) -> str:
    m = int(sec // 60); s = sec % 60
    return f"{m:02d}:{s:04.1f}"

_TAB_BTN = (
    "QPushButton{background:#252525;color:#666;border:1px solid #2a2a2a;"
    "border-radius:3px;font-size:10px;padding:2px 8px;}"
    "QPushButton:hover{color:#aaa;border-color:#555;}"
)
_TAB_BTN_ON = (
    "QPushButton{background:#3d8ef8;color:#fff;border:none;"
    "border-radius:3px;font-size:10px;font-weight:bold;padding:2px 8px;}"
)
_LANG_OFF = (
    "QPushButton{background:#1e1e1e;color:#555;border:1px solid #2a2a2a;"
    "border-radius:3px;font-size:10px;padding:2px 4px;}"
    "QPushButton:hover{color:#ccc;border-color:#555;}"
)
_LANG_ON = (
    "QPushButton{background:#1a3050;color:#3d8ef8;border:1px solid #3d8ef8;"
    "border-radius:3px;font-size:10px;font-weight:bold;padding:2px 4px;}"
)
