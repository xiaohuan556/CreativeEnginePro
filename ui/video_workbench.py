"""
小欢语音 - 视频工作室 (Tab 2)
导入 → 分离 → 语音识别 → 翻译 → 合成导出
左侧: 视频预览+控制 | 右侧: 素材列表(含音轨切换) | 底部: 字幕表格
"""
import os
import time
import tempfile
import subprocess
import shutil
import hashlib
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QProgressBar, QFileDialog,
    QFrame, QSplitter, QSlider, QTableWidget, QTableWidgetItem,
    QHeaderView, QInputDialog, QMessageBox, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal, QUrl, QThread, QSize
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from .widgets import CheckMarkBox
from PyQt6.QtMultimediaWidgets import QVideoWidget


# ── 翻译语种 ──
LANGUAGES = [
    ("中文", "zh"), ("英语", "en"), ("日语", "ja"), ("韩语", "ko"), ("泰语", "th"),
    ("越南语", "vi"), ("西语", "es"), ("葡语", "pt"), ("阿语", "ar"), ("印尼语", "id"),
]
_LANG_CUSTOM = "__custom__"


# ═══════════════ 视频列表项 Widget ═══════════════
class _VideoItemWidget(QWidget):
    """列表内嵌：☑ 名称 + [原声][人声][背景] 小按钮"""
    track_changed = pyqtSignal(int, str)   # (video_index, track_type)
    check_changed = pyqtSignal(int, bool)  # (video_index, checked)

    def __init__(self, name: str, idx: int, parent=None):
        super().__init__(parent)
        self._idx = idx
        self._track = "original"
        self._has_sep = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 6, 4, 6); lay.setSpacing(6)

        self.chk = CheckMarkBox()
        self.chk.setFixedSize(20, 20)
        self.chk.stateChanged.connect(lambda s: self.check_changed.emit(self._idx, s == Qt.CheckState.Checked.value))
        lay.addWidget(self.chk)

        self.lbl = QLabel(f"📹 {name}")
        self.lbl.setStyleSheet("color:#bbb;font-size:12px;")
        self.lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.lbl)

        # 右侧音轨按钮组
        btn_box = QHBoxLayout()
        btn_box.setSpacing(2)

        self.btn_orig = QPushButton("原声")
        self.btn_orig.setFixedSize(36, 22)
        self.btn_orig.setStyleSheet(_TK_SM_ON)
        self.btn_orig.clicked.connect(lambda: self._click("original"))
        btn_box.addWidget(self.btn_orig)

        self.btn_voc = QPushButton("人声")
        self.btn_voc.setFixedSize(36, 22)
        self.btn_voc.setStyleSheet(_TK_SM_HIDE)
        self.btn_voc.clicked.connect(lambda: self._click("vocals"))
        btn_box.addWidget(self.btn_voc)

        self.btn_bgm = QPushButton("背景")
        self.btn_bgm.setFixedSize(36, 22)
        self.btn_bgm.setStyleSheet(_TK_SM_HIDE)
        self.btn_bgm.clicked.connect(lambda: self._click("bgm"))
        btn_box.addWidget(self.btn_bgm)

        lay.addLayout(btn_box)

    def _click(self, which: str):
        # 未分离时只允许原声
        if not self._has_sep and which != "original":
            self._track = "original"
            self._update_btns()
            return
        self._track = which
        self._update_btns()
        self.track_changed.emit(self._idx, which)

    def _update_btns(self):
        if self._has_sep:
            self.btn_orig.setStyleSheet(_TK_SM_ON if self._track == "original" else _TK_SM_OFF)
            self.btn_voc.setStyleSheet(_TK_SM_ON if self._track == "vocals" else _TK_SM_OFF)
            self.btn_bgm.setStyleSheet(_TK_SM_ON if self._track == "bgm" else _TK_SM_OFF)
            self.btn_voc.setEnabled(True); self.btn_bgm.setEnabled(True)
            self.btn_voc.show(); self.btn_bgm.show()
        else:
            self.btn_orig.setStyleSheet(_TK_SM_ON)
            self.btn_voc.setEnabled(False); self.btn_bgm.setEnabled(False)
            self.btn_voc.hide(); self.btn_bgm.hide()
            self._track = "original"

    def set_separated(self, has: bool):
        self._has_sep = has
        if has and self._track == "original":
            # 分离完成后默认切换到人声轨
            self._track = "vocals"
        self._update_btns()

    @property
    def track(self): return self._track


class _AudioItemWidget(QWidget):
    """列表内嵌：☑ 名称 + [原声][人声][背景] 小按钮"""
    track_changed = pyqtSignal(int, str)   # (audio_index, track_type)
    check_changed = pyqtSignal(int, bool)  # (audio_index, checked)

    def __init__(self, name: str, idx: int, parent=None):
        super().__init__(parent)
        self._idx = idx
        self._track = "original"
        self._has_sep = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 6, 4, 6); lay.setSpacing(6)

        self.chk = CheckMarkBox()
        self.chk.setFixedSize(20, 20)
        self.chk.stateChanged.connect(lambda s: self.check_changed.emit(self._idx, s == Qt.CheckState.Checked.value))
        lay.addWidget(self.chk)

        self.lbl = QLabel(f"♫ {name}")
        self.lbl.setStyleSheet("color:#8bc34a;font-size:12px;")
        self.lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.lbl)

        # 右侧音轨按钮组
        btn_box = QHBoxLayout()
        btn_box.setSpacing(2)

        self.btn_orig = QPushButton("原声")
        self.btn_orig.setFixedSize(36, 22)
        self.btn_orig.setStyleSheet(_TK_SM_ON)
        self.btn_orig.clicked.connect(lambda: self._click("original"))
        btn_box.addWidget(self.btn_orig)

        self.btn_voc = QPushButton("人声")
        self.btn_voc.setFixedSize(36, 22)
        self.btn_voc.setStyleSheet(_TK_SM_HIDE)
        self.btn_voc.clicked.connect(lambda: self._click("vocals"))
        btn_box.addWidget(self.btn_voc)

        self.btn_bgm = QPushButton("背景")
        self.btn_bgm.setFixedSize(36, 22)
        self.btn_bgm.setStyleSheet(_TK_SM_HIDE)
        self.btn_bgm.clicked.connect(lambda: self._click("bgm"))
        btn_box.addWidget(self.btn_bgm)

        lay.addLayout(btn_box)

    def _click(self, which: str):
        if not self._has_sep and which != "original":
            self._track = "original"
            self._update_btns()
            return
        self._track = which
        self._update_btns()
        self.track_changed.emit(self._idx, which)

    def _update_btns(self):
        if self._has_sep:
            self.btn_orig.setStyleSheet(_TK_SM_ON if self._track == "original" else _TK_SM_OFF)
            self.btn_voc.setStyleSheet(_TK_SM_ON if self._track == "vocals" else _TK_SM_OFF)
            self.btn_bgm.setStyleSheet(_TK_SM_ON if self._track == "bgm" else _TK_SM_OFF)
            self.btn_voc.setEnabled(True); self.btn_bgm.setEnabled(True)
            self.btn_voc.show(); self.btn_bgm.show()
        else:
            self.btn_orig.setStyleSheet(_TK_SM_ON)
            self.btn_voc.setEnabled(False); self.btn_bgm.setEnabled(False)
            self.btn_voc.hide(); self.btn_bgm.hide()
            self._track = "original"

    def set_separated(self, has: bool):
        self._has_sep = has
        self._update_btns()

    @property
    def track(self): return self._track


class VideoWorkbench(QWidget):
    status_msg = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._videos = []
        self._audios = []
        self._current_idx = -1
        self._player = None
        self._audio_out = None
        self._seeking = False
        self._subtitles = []
        self._export_worker = None
        self._sub_debounce = None  # 字幕编辑防抖定时器
        self._target_lang = "en"
        self._worker = None
        self._show_original = True
        self._first_frame = False
        self._filter = "all"  # all | video | audio
        self._sep_queue = []
        self._sep_idx = 0
        self._batch_worker = None
        self._item_widgets = {}  # idx -> _VideoItemWidget
        self._audio_widgets = {}  # idx -> _AudioItemWidget
        self._track_vols = {"vocals": 80, "bgm": 60, "voice": 75}
        self._build()

    def _build(self):
        self.setStyleSheet("background:#1a1a1a;")
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 8)
        root.setSpacing(6)

        # ═══ 预览 + 素材列表 ═══
        top = QSplitter(Qt.Orientation.Horizontal)
        top.setHandleWidth(2)
        top.setStyleSheet("QSplitter::handle{background:#2a2a2a;}")

        self.preview_frame = QFrame()
        self.preview_frame.setMinimumWidth(480)
        self.preview_frame.setStyleSheet("background:#000;border-radius:6px;")
        pl = QVBoxLayout(self.preview_frame)
        pl.setContentsMargins(0, 0, 0, 0); pl.setSpacing(0)

        self.video_widget = QVideoWidget()
        self._player = QMediaPlayer()
        self._audio_out = QAudioOutput()
        self._player.setAudioOutput(self._audio_out)
        self._player.setVideoOutput(self.video_widget)
        self._player.durationChanged.connect(self._on_duration)
        self._player.positionChanged.connect(self._on_pos)
        self._player.playbackStateChanged.connect(self._on_main_state)
        pl.addWidget(self.video_widget)

        # 第二个播放器：配音试听（纯音频，BGM + TTS 同时播放时用）
        self._voice_player = QMediaPlayer()
        self._voice_audio_out = QAudioOutput()
        self._voice_player.setAudioOutput(self._voice_audio_out)
        self._voice_audio_out.setVolume(0.7)

        self.subtitle_label = QLabel(self.video_widget)
        self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setStyleSheet(
            "background:rgba(0,0,0,0.72);color:#fff;font-size:15px;"
            "font-weight:bold;padding:8px 20px;border-radius:6px;")
        self.subtitle_label.hide(); self.subtitle_label.raise_()

        top.addWidget(self.preview_frame)

        # ── 右侧素材列表 ──
        right = QWidget()
        right.setFixedWidth(420)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(4)

        # ── 素材列表 Header：两行 ──
        # 行1：批量操作（右对齐，最显眼）
        hdr1 = QHBoxLayout()
        hdr1.setSpacing(6)
        hdr1.addStretch()
        self.btn_batch_rename = QPushButton("✏ 批量命名")
        self.btn_batch_rename.setStyleSheet(_GHOST_TINY)
        self.btn_batch_rename.setFixedSize(78, 26)
        self.btn_batch_rename.clicked.connect(self._batch_rename)
        hdr1.addWidget(self.btn_batch_rename)
        self.btn_batch_export = QPushButton("📦 一键导出")
        self.btn_batch_export.setStyleSheet(_EXPORT_BTN)
        self.btn_batch_export.setFixedSize(96, 26)
        self.btn_batch_export.clicked.connect(self._batch_export)
        hdr1.addWidget(self.btn_batch_export)
        rl.addLayout(hdr1)
        # 行间距
        rl.addSpacing(3)
        # 行2：导入 + 勾选 + 筛选
        hdr2 = QHBoxLayout()
        hdr2.setSpacing(4)
        btn_import = QPushButton("📂 导入")
        btn_import.setStyleSheet(_TINY_PRIMARY)
        btn_import.setFixedSize(64, 26)
        btn_import.clicked.connect(self._smart_import)
        hdr2.addWidget(btn_import)
        self.btn_select_all = QPushButton("☑ 全选")
        self.btn_select_all.setStyleSheet(_GHOST_TINY)
        self.btn_select_all.setFixedSize(52, 26)
        self.btn_select_all.clicked.connect(self._select_all)
        hdr2.addWidget(self.btn_select_all)
        self.btn_deselect_all = QPushButton("☐ 取消")
        self.btn_deselect_all.setStyleSheet(_GHOST_TINY)
        self.btn_deselect_all.setFixedSize(52, 26)
        self.btn_deselect_all.clicked.connect(self._deselect_all)
        hdr2.addWidget(self.btn_deselect_all)
        hdr2.addStretch()
        self.btn_filter_v = QPushButton("📹视频")
        self.btn_filter_v.setCheckable(True)
        self.btn_filter_v.setStyleSheet(_TINY_PRIMARY)
        self.btn_filter_v.setFixedSize(58, 26)
        self.btn_filter_v.clicked.connect(lambda: self._set_filter("video"))
        hdr2.addWidget(self.btn_filter_v)
        self.btn_filter_a = QPushButton("🎵音频")
        self.btn_filter_a.setCheckable(True)
        self.btn_filter_a.setStyleSheet(_TINY_GREEN)
        self.btn_filter_a.setFixedSize(58, 26)
        self.btn_filter_a.clicked.connect(lambda: self._set_filter("audio"))
        hdr2.addWidget(self.btn_filter_a)
        rl.addLayout(hdr2)

        self.media_list = QListWidget()
        self.media_list.setStyleSheet(_MLIST)
        self.media_list.installEventFilter(self)  # 拦截空格键用于播放控制
        self.media_list.itemClicked.connect(self._on_media_click)
        self.media_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.media_list.customContextMenuRequested.connect(self._media_menu)
        self.media_list.mouseDoubleClickEvent = self._on_media_dbl
        rl.addWidget(self.media_list, 1)

        top.addWidget(right)
        top.setSizes([560, 420])
        root.addWidget(top, 3)

        # ═══ 控制条 ═══
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)
        self.btn_play = QPushButton("▶")
        self.btn_play.setFixedSize(34, 32)
        self.btn_play.setStyleSheet(_CTRL_BTN)
        self.btn_play.clicked.connect(self._toggle)
        ctrl.addWidget(self.btn_play)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.sliderPressed.connect(lambda: setattr(self, '_seeking', True))
        self.seek.sliderReleased.connect(lambda: (self._player.setPosition(self.seek.value()), setattr(self, '_seeking', False)))
        ctrl.addWidget(self.seek, 1)
        self.lbl_time = QLabel("00:00 / 00:00")
        self.lbl_time.setFixedWidth(100)
        self.lbl_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_time.setStyleSheet("color:#aaa;font-size:12px;")
        ctrl.addWidget(self.lbl_time)
        ctrl.addWidget(QLabel("🔊"))
        self.vol = QSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 100); self.vol.setValue(70); self.vol.setFixedWidth(60)
        self.vol.valueChanged.connect(lambda v: self._audio_out.setVolume(v / 100))
        ctrl.addWidget(self.vol)
        ctrl.addWidget(QLabel("  "))
        root.addLayout(ctrl)

        # ═══ 音轨分量控制（按需显示）═══
        self._vol_row = QHBoxLayout()
        self._vol_row.setSpacing(6)
        self._vol_labels = {}
        self._vol_sliders = {}
        for key, label, default in [
            ("vocals", "人声", 80),
            ("bgm", "背景", 60), ("voice", "配音", 75),
        ]:
            lbl = QLabel(label)
            lbl.setStyleSheet("color:#666;font-size:10px;")
            self._vol_row.addWidget(lbl)
            s = QSlider(Qt.Orientation.Horizontal)
            s.setRange(0, 200); s.setValue(default); s.setFixedWidth(55)
            s.setToolTip(f"{label}音量 {default}% (最大200%)")
            s.valueChanged.connect(lambda v, k=key: self._update_track_vol(k, v))
            self._vol_sliders[key] = s
            self._vol_labels[key] = lbl
            self._vol_row.addWidget(s)
        self._vol_row.addStretch()
        root.addLayout(self._vol_row)
        self._refresh_vol_visibility()

        # ═══ 操作栏 ═══
        act_bar = QHBoxLayout()
        act_bar.setSpacing(8)
        self.btn_batch_sep = QPushButton("🎵 分离人声背景声")
        self.btn_batch_sep.setStyleSheet(_SEP_BATCH_BTN)
        self.btn_batch_sep.clicked.connect(self._batch_separate)
        act_bar.addWidget(self.btn_batch_sep)
        self.btn_asr = QPushButton("🎙 语音识别")
        self.btn_asr.setStyleSheet(_ACT)
        self.btn_asr.clicked.connect(self._recognize)
        act_bar.addWidget(self.btn_asr)

        act_bar.addStretch()

        # 翻译语种按钮 — 点击即翻译
        act_bar.addWidget(QLabel("翻译到:"))
        self._lang_btns = {}
        for label, code in LANGUAGES:
            b = QPushButton(label)
            b.setFixedSize(50, 26)
            b.setStyleSheet(_LANG_OFF)
            b.clicked.connect(lambda _, c=code: self._lang_and_translate(c))
            self._lang_btns[code] = b
            act_bar.addWidget(b)
        self.btn_custom_lang = QPushButton("自定义")
        self.btn_custom_lang.setFixedSize(54, 26)
        self.btn_custom_lang.setStyleSheet(_LANG_OFF)
        self.btn_custom_lang.clicked.connect(self._pick_and_translate)
        act_bar.addWidget(self.btn_custom_lang)

        act_bar.addStretch()

        root.addLayout(act_bar)

        # 进度条
        self.progress = QProgressBar()
        self.progress.setRange(0, 100); self.progress.setFixedHeight(2)
        self.progress.setTextVisible(False); self.progress.setStyleSheet(_PROG)
        root.addWidget(self.progress)

        # ═══ 字幕表格 ═══
        sub_bar = QHBoxLayout()
        sub_bar.setSpacing(8)
        self.btn_show_orig = QPushButton("📝 原文")
        self.btn_show_orig.setFixedSize(70, 26)
        self.btn_show_orig.setStyleSheet(_SUBTAB_ON)
        self.btn_show_orig.clicked.connect(lambda: self._switch_sub_view(True))
        sub_bar.addWidget(self.btn_show_orig)
        self.btn_show_trans = QPushButton("🌐 译文")
        self.btn_show_trans.setFixedSize(70, 26)
        self.btn_show_trans.setStyleSheet(_SUBTAB_OFF)
        self.btn_show_trans.clicked.connect(lambda: self._switch_sub_view(False))
        sub_bar.addWidget(self.btn_show_trans)
        sub_bar.addWidget(QLabel("  双击单元格可编辑字幕"))
        sub_bar.addStretch()
        root.addLayout(sub_bar)

        self.sub_table = QTableWidget(0, 3)
        self.sub_table.setStyleSheet(_TABLE)
        self.sub_table.setHorizontalHeaderLabels(["#", "时间", "文本"])
        self.sub_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.sub_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.sub_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.sub_table.setColumnWidth(0, 32)
        self.sub_table.setColumnWidth(1, 140)
        self.sub_table.verticalHeader().setVisible(False)
        self.sub_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.sub_table.cellDoubleClicked.connect(self._on_sub_dbl_click)
        self.sub_table.cellChanged.connect(self._on_sub_edit)
        self.sub_table.setFixedHeight(150)
        self.sub_table.cellClicked.connect(self._on_sub_click)
        root.addWidget(self.sub_table)

        self._highlight_lang("en")

    # ═══════════════ 键盘事件
    def eventFilter(self, obj, event):
        """拦截 media_list 的空格键，避免被子控件 QListWidget 抢走用于勾选"""
        if (obj is self.media_list and
            event.type() == event.Type.KeyPress and
            event.key() == Qt.Key.Key_Space):
            self._toggle()
            return True
        return super().eventFilter(obj, event)

    # ═══════════════ 快捷键
    def _toggle(self):
        """空格/播放：暂停/续播/新播"""
        is_playing = self._player and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        was_paused = self._player and self._player.playbackState() == QMediaPlayer.PlaybackState.PausedState

        # 播放中 → 暂停
        if is_playing:
            self._player.pause(); self.btn_play.setText("▶")
            self._voice_player.pause()
            return

        # 暂停中 → 续播（不重新加载）
        if was_paused and self._player.duration() > 0:
            self._player.play(); self.btn_play.setText("⏸")
            if self._voice_player.source().isValid():
                self._voice_player.setPosition(self._player.position())
                self._voice_player.play()
            return

        # 首次播放：加载勾选内容
        checked_v = self._get_checked_videos()
        checked_a = self._get_checked_audios()
        if not checked_v and not checked_a:
            self.status_msg.emit("请先勾选视频或配音文件", "warn")
            return

        if checked_v:
            for i, vd in enumerate(self._videos):
                if i in self._item_widgets and self._item_widgets[i].chk.isChecked():
                    self._current_idx = i
                    track = vd.get("track", "original")
                    if track != "original" and not vd.get("vocals"):
                        track = "original"
                    self._play_track(track)
                    break

        if checked_a:
            self._start_voice_mix(checked_a)

        self.btn_play.setText("⏸")

    def _start_voice_mix(self, checked_a):
        """混音并播放勾选的配音文件（副播放器）"""
        if getattr(self, '_voice_mixing', False):
            return  # 防止重复触发
        self._voice_mixing = True
        tts_paths = [a["path"] for a in checked_a if Path(a["path"]).exists()]
        try:
            if not tts_paths:
                self.status_msg.emit("勾选的配音文件不存在，请重新导入", "warn")
                return
            if len(tts_paths) == 1:
                mixed = tts_paths[0]
            else:
                from config import FFMPEG_BIN
                tmp = Path(tempfile.gettempdir()) / f"xh_mix_{int(time.time())}.mp3"
                inputs = []; filters = []
                for j, p in enumerate(tts_paths):
                    inputs.extend(["-i", p]); filters.append(f"[{j}:a]")
                merge = "".join(filters) + f"amix=inputs={len(tts_paths)}:duration=longest[aout]"
                r = subprocess.run([FFMPEG_BIN, "-y"] + inputs +
                    ["-filter_complex", merge, "-map", "[aout]",
                     "-c:a", "libmp3lame", "-b:a", "192k", str(tmp)], capture_output=True)
                mixed = str(tmp) if (r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0) else tts_paths[0]
                if r.returncode != 0:
                    self.status_msg.emit(f"配音混音失败，已用单文件: {r.stderr.decode('utf-8','replace')[-100:]}", "warn")
            self._voice_player.setSource(QUrl.fromLocalFile(mixed))
            self._voice_audio_out.setVolume(min(self._track_vols.get("voice", 75), 100) / 100)
            self._voice_player.play()
            # 同步到主播放器位置
            if self._player and self._player.duration() > 0:
                pos = self._player.position()
                if pos > 0:
                    self._voice_player.setPosition(pos)
        finally:
            self._voice_mixing = False

    def seek_relative(self, sec: int):
        if self._player and self._player.duration() > 0:
            p = self._player.position() + sec * 1000
            p = max(0, min(p, self._player.duration()))
            self._player.setPosition(p)
            if self._voice_player.source().isValid():
                self._voice_player.setPosition(p)

    def adjust_volume(self, d: int):
        self.vol.setValue(max(0, min(100, self.vol.value() + d)))

    def _update_track_vol(self, key: str, val: int):
        self._track_vols[key] = val
        # 预览音量封顶 100%（QAudioOutput 上限），导出用全值
        preview_vol = min(val, 100) / 100.0
        if key == "voice":
            self._voice_audio_out.setVolume(preview_vol)
        if self._current_idx >= 0 and self._current_idx < len(self._videos):
            cur_track = self._videos[self._current_idx].get("track", "original")
            if key == cur_track:
                self._audio_out.setVolume(preview_vol)

    def _refresh_vol_visibility(self):
        """按音轨按钮状态显示滑块：点人声→人声滑块，点背景→背景滑块，勾配音→配音滑块"""
        track = "original"
        if self._current_idx >= 0 and self._current_idx < len(self._videos):
            track = self._videos[self._current_idx].get("track", "original")
        has_audio = any(w.chk.isChecked() for w in self._audio_widgets.values())
        for key in ("vocals", "bgm", "voice"):
            lbl = self._vol_labels.get(key)
            sld = self._vol_sliders.get(key)
            if not lbl or not sld: continue
            show = {"vocals": track == "vocals", "bgm": track == "bgm", "voice": has_audio}.get(key, False)
            lbl.setVisible(show)
            sld.setVisible(show)

    def undo_last_step(self):
        """撤回上一步操作：断开 worker 信号并终止线程"""
        if self._worker and self._worker.isRunning():
            try: self._worker.finished.disconnect(); self._worker.error.disconnect()
            except Exception: pass
            self._worker.quit(); self._worker.wait(1000)
            self._worker.finished.connect(self._worker.deleteLater)
            self._worker = None
            self.status_msg.emit("已撤回当前操作", "info")
        elif self._export_worker and self._export_worker.isRunning():
            try:
                self._export_worker.finished.disconnect(); self._export_worker.error.disconnect()
                self._export_worker.progress.disconnect(); self._export_worker.done_one.disconnect()
            except Exception: pass
            self._export_worker.finished.connect(self._export_worker.deleteLater)
            self._export_worker = None
            self.btn_batch_export.setEnabled(True)
            self.btn_batch_export.setText("📦 一键导出")
            self.status_msg.emit("已撤回导出操作", "info")

        else:
            self.status_msg.emit("没有正在执行的操作", "warn")

    # ═══════════════ 接收配音
    def set_voice(self, path: str):
        """接收语音台推送：直接导入，不弹命名框"""
        name = Path(path).stem
        self._add_audio(path, name)
        self.status_msg.emit(f"已导入: {name}", "success")

    # ═══════════════ 素材管理
    def _smart_import(self):
        """智能导入：自动识别视频/音频并归类"""
        paths, _ = QFileDialog.getOpenFileNames(self, "导入素材", "",
            "媒体 (*.mp4 *.mov *.avi *.mkv *.mp3 *.wav *.ogg *.flac *.aac *.m4a);;所有文件 (*)")
        video_ext = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv"}
        audio_ext = {".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a"}
        imported = 0
        for p in paths:
            ext = Path(p).suffix.lower()
            if ext in video_ext:
                imported += int(self._add_video(p))
            elif ext in audio_ext:
                imported += int(self._add_audio(p, Path(p).stem))
            else:
                self.status_msg.emit(f"跳过不支持的文件: {Path(p).name}", "warn")
        if imported:
            self.status_msg.emit(f"导入 {imported} 个文件", "success")

    def _smart_import_fallback(self, paths):
        """从 dropEvent 调用：直接接收路径列表，不弹文件对话框"""
        video_ext = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv"}
        for p in paths:
            ext = Path(p).suffix.lower()
            if ext in video_ext:
                self._add_video(p)
            elif ext in {".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a"}:
                self._add_audio(p, Path(p).stem)
            else:
                self.status_msg.emit(f"跳过不支持的文件: {Path(p).name}", "warn")
        self.status_msg.emit(f"拖入 {len(paths)} 个文件", "success")

    def _add_audio_fallback(self, path: str):
        """仅添加单个音频（从 dropEvent 调用）"""
        self._add_audio(path, Path(path).stem)
        self.status_msg.emit(f"已导入音频: {Path(path).stem}", "success")

    def _add_video(self, path: str):
        if not Path(path).is_file() or any(v["path"] == path for v in self._videos):
            return False
        idx = len(self._videos)
        it = QListWidgetItem()
        it.setData(Qt.ItemDataRole.UserRole, ("video", idx))
        it.setSizeHint(QSize(0, 44))
        self.media_list.addItem(it)

        # 自定义 Widget：☑ 名称 + 音轨切换按钮
        w = _VideoItemWidget(Path(path).name, idx)
        w.track_changed.connect(self._on_item_track)
        w.check_changed.connect(self._on_item_check)
        self.media_list.setItemWidget(it, w)
        self._item_widgets[idx] = w

        self._videos.append({"path": path, "vocals": "", "bgm": "", "orig_subs": [], "trans_subs": [], "track": "original"})
        if len(self._videos) + len(self._audios) == 1:
            self._load_video(0)
        self.status_msg.emit(f"导入视频: {Path(path).name}", "info")
        return True

    def _add_audio(self, path: str, name: str):
        if not Path(path).is_file() or any(a["path"] == path for a in self._audios):
            return False
        idx = len(self._audios)
        it = QListWidgetItem()
        it.setData(Qt.ItemDataRole.UserRole, ("audio", idx))
        it.setSizeHint(QSize(0, 44))
        self.media_list.addItem(it)

        # 自定义 Widget：☑ 名称 + 音轨切换按钮
        w = _AudioItemWidget(name, idx)
        w.track_changed.connect(self._on_audio_track)
        w.check_changed.connect(self._on_audio_check)
        self.media_list.setItemWidget(it, w)
        self._audio_widgets[idx] = w

        self._audios.append({"path": path, "name": name, "vocals": "", "bgm": "", "track": "original"})
        self.status_msg.emit(f"导入音频: {name}", "success")
        return True

    def _on_item_track(self, idx: int, track: str):
        """列表项内音轨切换：更新数据 + 预览播放"""
        if 0 <= idx < len(self._videos):
            v = self._videos[idx]
            # 未分离时强制 original，防止意外切换
            if not v.get("vocals") and track != "original":
                track = "original"
            v["track"] = track
            if idx == self._current_idx:
                self._play_track(track)
            self._refresh_vol_visibility()

    def _on_audio_track(self, idx: int, track: str):
        """音频列表项内音轨切换：更新数据 + 副播放器预览"""
        if 0 <= idx < len(self._audios):
            a = self._audios[idx]
            if not a.get("vocals") and track != "original":
                track = "original"
            a["track"] = track
            path = a.get("path")
            use_player = self._voice_player
            use_out = self._voice_audio_out
            vol_key = "voice"
            if track == "original" and path:
                use_player.setSource(QUrl.fromLocalFile(path))
            elif track == "vocals" and a.get("vocals"):
                use_player.setSource(QUrl.fromLocalFile(a["vocals"])); vol_key = "vocals"
            elif track == "bgm" and a.get("bgm"):
                use_player.setSource(QUrl.fromLocalFile(a["bgm"])); vol_key = "bgm"
            else:
                return
            use_out.setVolume(min(self._track_vols.get(vol_key, 75), 100) / 100)
            use_player.play()

    def _on_item_check(self, idx: int, checked: bool):
        """视频勾选切换（仅记录状态，播放由空格/▶触发）"""
        self._refresh_vol_visibility()

    def _on_audio_check(self, idx: int, checked: bool):
        """音频勾选切换"""
        self._refresh_vol_visibility()
        checked_a = self._get_checked_audios()
        if checked_a and (self._player and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState):
            self._start_voice_mix(checked_a)
        elif not checked_a:
            self._voice_player.stop()
            self._voice_player.setSource(QUrl())

    def _play_track(self, which: str):
        """预览播放指定音轨，失败时保持原音轨"""
        if self._current_idx < 0: return
        v = self._videos[self._current_idx]
        try:
            if which == "original":
                self._player.setSource(QUrl.fromLocalFile(v["path"]))
                self._player.play()
            elif which in ("vocals", "bgm") and v.get("vocals"):
                audio = v["vocals"] if which == "vocals" else v["bgm"]
                key = f"pv_{which}"
                if key not in v or not Path(v.get(key, "")).exists():
                    tmp = Path(tempfile.gettempdir()) / f"xh_{which}_{Path(v['path']).stem}.mp4"
                    _ffmpeg_swap(v["path"], audio, str(tmp))
                    v[key] = str(tmp)
                self._player.setSource(QUrl.fromLocalFile(v[key]))
                self._player.play()
            vol = self._track_vols.get(which, 70)
            self._audio_out.setVolume(min(vol, 100) / 100)
        except Exception as e:
            self.status_msg.emit(f"切换失败: {e}", "error")

    def _on_media_click(self, it: QListWidgetItem):
        data = it.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, tuple) or len(data) != 2: return
        kind, idx = data
        if kind == "video":
            self._load_video(idx)
        elif kind == "audio":
            # 单击只选中，不自动播放（勾选+空格才播）
            name = self._audios[idx].get("name", Path(self._audios[idx]["path"]).stem)
            self.status_msg.emit(f"已选中: {name}", "info")

    def _on_media_dbl(self, ev):
        it = self.media_list.itemAt(ev.pos())
        if it is None:
            self._smart_import()
        else:
            d = it.data(Qt.ItemDataRole.UserRole)
            if isinstance(d, tuple) and d[0] == "video" and d[1] in self._item_widgets:
                w = self._item_widgets[d[1]]
                w.chk.setChecked(not w.chk.isChecked())
            elif isinstance(d, tuple) and d[0] == "audio" and d[1] in self._audio_widgets:
                w = self._audio_widgets[d[1]]
                w.chk.setChecked(not w.chk.isChecked())

    def _set_filter(self, kind: str):
        if self._filter == kind:
            self._filter = "all"
        else:
            self._filter = kind
        self._refresh_filter()

    def _refresh_filter(self):
        is_all = self._filter == "all"
        self.btn_filter_v.setChecked(self._filter == "video")
        self.btn_filter_a.setChecked(self._filter == "audio")
        if is_all:
            self.btn_filter_v.setStyleSheet(_TINY_PRIMARY)
            self.btn_filter_a.setStyleSheet(_TINY_GREEN)
        elif self._filter == "video":
            self.btn_filter_v.setStyleSheet(_FILTER_V_ON)
            self.btn_filter_a.setStyleSheet(_TINY_GREEN)
        else:
            self.btn_filter_v.setStyleSheet(_TINY_PRIMARY)
            self.btn_filter_a.setStyleSheet(_FILTER_ON)

        for i in range(self.media_list.count()):
            it = self.media_list.item(i)
            d = it.data(Qt.ItemDataRole.UserRole)
            if is_all:
                it.setHidden(False)
            elif isinstance(d, tuple) and len(d) == 2:
                it.setHidden(d[0] != self._filter)

    def _media_menu(self, pos):
        it = self.media_list.itemAt(pos)
        if not it: self._smart_import(); return
        data = it.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, tuple): return
        kind, idx = data
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        if kind == "video" and 0 <= idx < len(self._videos):
            v = self._videos[idx]
            has_sep = bool(v.get("vocals"))
            export_menu = menu.addMenu("📤 导出")
            act_exp_orig = export_menu.addAction("🎬 导出原声")
            act_exp_voc = export_menu.addAction("🎤 导出人声")
            act_exp_bgm = export_menu.addAction("🎵 导出背景声")
            if not has_sep:
                act_exp_voc.setVisible(False)
                act_exp_bgm.setVisible(False)
            menu.addSeparator()
            act_open_dir = menu.addAction("📂 打开文件目录")
            menu.addSeparator()
            act_sep = menu.addAction("🎵 分离人声背景声")
            act_sep.setEnabled(not has_sep)
            menu.addSeparator()
            act_rename = menu.addAction("✏ 重命名")
            act_del = menu.addAction("🗑 删除")
        elif kind == "audio" and 0 <= idx < len(self._audios):
            a = self._audios[idx]
            has_sep = bool(a.get("vocals"))
            export_menu = menu.addMenu("📤 导出")
            act_exp_orig = export_menu.addAction("🎬 导出原声")
            act_exp_voc = export_menu.addAction("🎤 导出人声")
            act_exp_bgm = export_menu.addAction("🎵 导出背景声")
            if not has_sep:
                act_exp_voc.setVisible(False)
                act_exp_bgm.setVisible(False)
            menu.addSeparator()
            act_sep = menu.addAction("🎵 分离人声背景声")
            act_sep.setEnabled(not has_sep)
            menu.addSeparator()
            act_rename = menu.addAction("✏ 重命名")
            act_del = menu.addAction("🗑 删除")

        chosen = menu.exec(self.media_list.mapToGlobal(pos))
        if not chosen: return

        if kind == "video":
            if chosen == act_exp_orig:
                self._export_video_track(idx, "original")
            elif has_sep and chosen == act_exp_voc:
                self._export_video_track(idx, "vocals")
            elif has_sep and chosen == act_exp_bgm:
                self._export_video_track(idx, "bgm")
            elif chosen == act_open_dir:
                self._open_media_directory(v.get("path", ""))
            elif chosen == act_sep:
                self._separate_single(idx)
            elif chosen == act_rename:
                self._rename_video(idx, it)
            elif chosen == act_del:
                self._remove_media(kind, idx, it)
        elif kind == "audio":
            if chosen == act_exp_orig:
                self._export_audio_track(idx, "original")
            elif has_sep and chosen == act_exp_voc:
                self._export_audio_track(idx, "vocals")
            elif has_sep and chosen == act_exp_bgm:
                self._export_audio_track(idx, "bgm")
            elif chosen == act_sep:
                self._separate_audio(idx)
            elif chosen == act_rename:
                self._rename_audio(idx, it)
            elif chosen == act_del:
                self._remove_media(kind, idx, it)

    def _open_media_directory(self, media_path: str):
        """打开素材所在目录；源文件丢失时仍尝试打开其父目录。"""
        if not media_path:
            self.status_msg.emit("素材路径为空", "warn")
            return
        folder = Path(media_path).expanduser().parent
        if not folder.is_dir():
            self.status_msg.emit(f"文件目录不存在: {folder}", "warn")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve()))):
            self.status_msg.emit(f"无法打开文件目录: {folder}", "error")

    def _export_video_track(self, idx: int, track: str):
        """右键导出单个视频的指定音轨"""
        if not (0 <= idx < len(self._videos)): return
        v = self._videos[idx]
        if track in ("vocals", "bgm") and not v.get("vocals"):
            self.status_msg.emit("请先分离音频", "warn"); return
        # 先输入文件名
        default_name = v.get("rename") or Path(v["path"]).stem
        name, ok = QInputDialog.getText(self, "导出命名", "导出文件名：", text=default_name)
        if not ok or not name.strip(): return
        out_dir = QFileDialog.getExistingDirectory(self, "导出目录")
        if not out_dir: return
        # 把自定义名临时塞进 v，导出后清除
        v["_export_name"] = name.strip()
        self._cleanup_export_worker()
        self._export_worker = _ExportWorker([v], [], out_dir, video_tracks={0: track},
            track_vols=self._track_vols)
        self._export_worker.progress.connect(self._on_export_progress)
        self._export_worker.done_one.connect(lambda n: self.status_msg.emit(f"✓ {n}", "success"))
        self._export_worker.finished.connect(lambda: self._on_export_all_done(out_dir))
        self._export_worker.error.connect(self._on_export_failed)
        self.btn_batch_export.setEnabled(False)
        self.btn_batch_export.setText("📦 导出中...")
        self.progress.setValue(0)
        self._export_worker.start()

    def _separate_single(self, idx: int):
        """右键分离单个视频"""
        if not (0 <= idx < len(self._videos)): return
        if self._worker is not None and self._worker.isRunning():
            self.status_msg.emit("正在处理中，请稍候", "warn"); return
        source = self._videos[idx]
        self._run_worker(_SepWorker(source["path"]),
            done=lambda r, source=source: self._on_sep_done(source, *r),
            err=lambda e: self.status_msg.emit(f"分离失败: {e}", "error"),
            btn=self.btn_batch_sep, label="分离中...")

    def _export_audio_track(self, idx: int, track: str):
        """右键导出单个音频的指定音轨"""
        if not (0 <= idx < len(self._audios)): return
        a = self._audios[idx]
        if track in ("vocals", "bgm") and not a.get("vocals"):
            self.status_msg.emit("请先分离音频", "warn"); return
        out_dir = QFileDialog.getExistingDirectory(self, "导出目录")
        if not out_dir: return
        default_name = a.get("name", Path(a["path"]).stem)
        name, ok = QInputDialog.getText(self, "导出命名", "导出文件名：", text=default_name)
        if not ok or not name.strip(): return
        name = name.strip()
        try:
            if track == "original":
                src = a["path"]
                ext = Path(src).suffix
                dest = Path(out_dir) / f"{name}{ext}"
                if dest.exists(): dest = Path(out_dir) / f"{name}_copy{ext}"
                shutil.copy(src, dest)
            elif track == "vocals":
                src = a["vocals"]
                dest = Path(out_dir) / f"{name}_人声.wav"
                if dest.exists(): dest = Path(out_dir) / f"{name}_人声_{int(time.time())}.wav"
                shutil.copy(src, dest)
            elif track == "bgm":
                src = a["bgm"]
                dest = Path(out_dir) / f"{name}_背景声.wav"
                if dest.exists(): dest = Path(out_dir) / f"{name}_背景声_{int(time.time())}.wav"
                shutil.copy(src, dest)
            self.status_msg.emit(f"✓ 已导出: {dest.name}", "success")
        except Exception as e:
            self.status_msg.emit(f"导出失败: {e}", "error")

    def _separate_audio(self, idx: int):
        """右键分离单个音频"""
        if not (0 <= idx < len(self._audios)): return
        if self._worker is not None and self._worker.isRunning():
            self.status_msg.emit("正在处理中，请稍候", "warn"); return
        a = self._audios[idx]
        self._run_worker(_SepWorker(a["path"]),
            done=lambda r, source=a: self._on_audio_sep_done(source, *r),
            err=lambda e: self.status_msg.emit(f"分离失败: {e}", "error"),
            btn=self.btn_batch_sep, label="分离中...")

    def _on_audio_sep_done(self, source, vwav: str, bwav: str):
        """音频分离完成回调"""
        if source not in self._audios:
            self.status_msg.emit("分离完成，但源音频已被删除", "warn")
            return
        idx = self._audios.index(source)
        a = source
        a["vocals"], a["bgm"] = self._rename_sep(a["path"], vwav, bwav)
        # 刷新音频widget的按钮状态
        self._refresh_audio_widget(idx)
        self.status_msg.emit(f"✓ 音频分离完成: {a.get('name', '')}", "success")

    def _refresh_audio_widget(self, idx: int):
        """刷新音频列表项的音轨按钮状态"""
        if idx in self._audio_widgets:
            has_sep = bool(self._audios[idx].get("vocals"))
            self._audio_widgets[idx].set_separated(has_sep)

    def _rename_video(self, idx: int, it: QListWidgetItem):
        """右键重命名视频"""
        if not (0 <= idx < len(self._videos)): return
        v = self._videos[idx]
        old_name = v.get("rename") or Path(v["path"]).stem
        name, ok = QInputDialog.getText(self, "重命名", "新名称：", text=old_name)
        if ok and name.strip():
            v["rename"] = name.strip()
            if idx in self._item_widgets:
                self._item_widgets[idx].lbl.setText(f"📹 {name.strip()}")

    def _rename_audio(self, idx: int, it: QListWidgetItem):
        """右键重命名音频"""
        if not (0 <= idx < len(self._audios)): return
        a = self._audios[idx]
        name, ok = QInputDialog.getText(self, "重命名", "新名称：", text=a["name"])
        if ok and name.strip():
            a["name"] = name.strip()
            if idx in self._audio_widgets:
                self._audio_widgets[idx].lbl.setText(f"♫ {name.strip()}")

    def _remove_media(self, kind: str, idx: int, it: QListWidgetItem):
        row = self.media_list.row(it)
        if kind == "video" and 0 <= idx < len(self._videos):
            self._videos.pop(idx)
            if self._current_idx == idx:
                self._player.stop()
                self._player.setSource(QUrl())
                self._current_idx = -1
                self.btn_play.setText("▶")
            elif self._current_idx > idx:
                self._current_idx -= 1
        elif kind == "audio" and 0 <= idx < len(self._audios):
            self._audios.pop(idx)
        self.media_list.takeItem(row)
        # 重建索引映射
        self._rebuild_indices()

    def _rebuild_indices(self):
        """删除素材后重建所有索引和 Widget 映射"""
        self._item_widgets.clear()
        self._audio_widgets.clear()
        vi = ai = 0
        for i in range(self.media_list.count()):
            it = self.media_list.item(i)
            d = it.data(Qt.ItemDataRole.UserRole)
            if not isinstance(d, tuple) or len(d) != 2: continue
            kind = d[0]
            if kind == "video":
                it.setData(Qt.ItemDataRole.UserRole, ("video", vi))
                w = self.media_list.itemWidget(it)
                if isinstance(w, _VideoItemWidget):
                    w._idx = vi
                    self._item_widgets[vi] = w
                vi += 1
            elif kind == "audio":
                it.setData(Qt.ItemDataRole.UserRole, ("audio", ai))
                w = self.media_list.itemWidget(it)
                if isinstance(w, _AudioItemWidget):
                    w._idx = ai
                    self._audio_widgets[ai] = w
                ai += 1

    def _load_video(self, idx: int):
        if 0 <= idx < len(self._videos):
            self._current_idx = idx
            v = self._videos[idx]
            track = v.get("track", "original")
            if track != "original" and v.get("vocals"):
                self._play_track(track)
            else:
                self._player.setSource(QUrl.fromLocalFile(v["path"]))
                self._player.play()
            self._first_frame = True
            self.btn_play.setText("▶")
            self._refresh_sub_table()
            self.status_msg.emit(f"加载: {Path(v['path']).name}", "info")

    def _get_checked_audios(self) -> list:
        result = []
        for idx, w in self._audio_widgets.items():
            if w.chk.isChecked() and 0 <= idx < len(self._audios):
                result.append(self._audios[idx])
        return result

    def _get_checked_videos(self) -> list:
        out = []
        for idx, w in self._item_widgets.items():
            if w.chk.isChecked() and 0 <= idx < len(self._videos):
                out.append(self._videos[idx])
        return out

    # ═══════════════ 全选 / 取消全选
    def _select_all(self):
        for idx, w in self._item_widgets.items():
            w.chk.setChecked(True)
        for idx, w in self._audio_widgets.items():
            w.chk.setChecked(True)

    def _deselect_all(self):
        for idx, w in self._item_widgets.items():
            w.chk.setChecked(False)
        for idx, w in self._audio_widgets.items():
            w.chk.setChecked(False)

    # ═══════════════ 分离
    def _on_sep_done(self, source, vwav: str, bwav: str):
        if source not in self._videos:
            self.status_msg.emit("分离完成，但源视频已被删除", "warn")
            return
        idx = self._videos.index(source)
        v = source
        v["vocals"], v["bgm"] = self._rename_sep(v["path"], vwav, bwav)
        self._refresh_item_widget(idx)
        self.status_msg.emit("分离完成 ✓", "success")
        self._refresh_vol_visibility()

    def _rename_sep(self, video_path: str, vwav: str, bwav: str):
        """分离后重命名为唯一文件，避免批量覆盖"""
        from config import WORK_DIR
        stem = Path(video_path).stem
        source_id = hashlib.sha1(str(Path(video_path).resolve()).encode("utf-8")).hexdigest()[:8]
        new_v = str(WORK_DIR / f"{stem}_{source_id}_vocals.wav")
        new_b = str(WORK_DIR / f"{stem}_{source_id}_bgm.wav")
        if Path(vwav).exists():
            os.replace(vwav, new_v)
        if Path(bwav).exists():
            os.replace(bwav, new_b)
        return new_v, new_b

    def _refresh_item_widget(self, idx: int):
        """刷新列表项的音轨按钮状态"""
        if idx in self._item_widgets:
            has_sep = bool(self._videos[idx].get("vocals"))
            self._item_widgets[idx].set_separated(has_sep)

    # ═══════════════ 识别
    def _recognize(self):
        if self._current_idx < 0:
            self.status_msg.emit("请先选择视频", "warn"); return
        if self._worker is not None and self._worker.isRunning():
            self.status_msg.emit("正在处理中，请稍候", "warn"); return
        source_idx = self._current_idx
        v = self._videos[source_idx]
        audio = v.get("vocals") or v.get("path")
        if not v.get("vocals"):
            self.status_msg.emit("未分离，将用原视频音轨识别", "warn")
        self._run_worker(_ASRWorker(audio),
            done=lambda entries, source=v: self._on_asr_done(source, entries),
            err=lambda e: self.status_msg.emit(f"识别失败: {e}", "error"),
            btn=self.btn_asr, label="识别中...")

    def _on_asr_done(self, source, entries):
        if source not in self._videos:
            self.status_msg.emit("识别完成，但源视频已被删除", "warn")
            return
        idx = self._videos.index(source)
        v = source
        v["orig_subs"] = [{"start": e.start, "end": e.end, "text": e.text} for e in entries]
        if idx == self._current_idx:
            self._show_original = True
            self._subtitles = v["orig_subs"]
            self._refresh_sub_table()
            self._update_subtab_style()
        self.status_msg.emit(f"识别完成: {len(entries)} 条字幕", "success")

    # ═══════════════ 翻译
    def _lang_and_translate(self, code: str):
        self._target_lang = code
        self._highlight_lang(code)
        self._do_translate()

    def _pick_and_translate(self):
        lang, ok = QInputDialog.getText(self, "自定义翻译语种",
            "请输入目标语言（如：法语、德语、意大利语…）")
        if ok and lang.strip():
            self._target_lang = lang.strip()
            self.btn_custom_lang.setText(f"{lang.strip()[:4]}")
            self._highlight_lang(_LANG_CUSTOM)
            self._do_translate()

    def _highlight_lang(self, code: str):
        for c, b in self._lang_btns.items():
            b.setStyleSheet(_LANG_ON if c == code else _LANG_OFF)
        self.btn_custom_lang.setStyleSheet(_LANG_ON if code not in self._lang_btns else _LANG_OFF)

    def _do_translate(self):
        if self._current_idx < 0:
            self.status_msg.emit("请先选择视频", "warn"); return
        if self._worker is not None and self._worker.isRunning():
            self.status_msg.emit("正在处理中，请稍候", "warn"); return
        source_idx = self._current_idx
        v = self._videos[source_idx]
        entries = v.get("orig_subs", [])
        if not entries:
            self.status_msg.emit("请先识别语音", "warn"); return
        from core.transcriber import SRTEntry
        srt_entries = [SRTEntry(i + 1, e["start"], e["end"], e["text"]) for i, e in enumerate(entries)]
        self._run_worker(_SubTransWorker(srt_entries, self._target_lang),
            done=lambda result, source=v: self._on_trans_done(source, result),
            err=lambda e: self.status_msg.emit(f"翻译失败: {e}", "error"),
            btn=self._lang_btns.get(self._target_lang, self.btn_custom_lang),
            label="翻译中...")

    def _on_trans_done(self, source, entries):
        if source not in self._videos:
            self.status_msg.emit("翻译完成，但源视频已被删除", "warn")
            return
        idx = self._videos.index(source)
        v = source
        v["trans_subs"] = [{"start": e.start, "end": e.end, "text": e.text} for e in entries]
        if idx == self._current_idx:
            self._show_original = False
            self._subtitles = v["trans_subs"]
            self._refresh_sub_table()
            self._update_subtab_style()
        self.status_msg.emit(f"翻译完成: {len(entries)} 条", "success")

    # ═══════════════ 字幕表格
    def _switch_sub_view(self, show_original: bool):
        self._show_original = show_original
        if self._current_idx >= 0:
            v = self._videos[self._current_idx]
            self._subtitles = v.get("orig_subs" if show_original else "trans_subs", [])
        self._refresh_sub_table()
        self._update_subtab_style()

    def _update_subtab_style(self):
        self.btn_show_orig.setStyleSheet(_SUBTAB_ON if self._show_original else _SUBTAB_OFF)
        self.btn_show_trans.setStyleSheet(_SUBTAB_ON if not self._show_original else _SUBTAB_OFF)

    def _refresh_sub_table(self):
        self.sub_table.blockSignals(True)
        self.sub_table.setRowCount(0)
        if self._current_idx < 0:
            self.sub_table.blockSignals(False); return
        v = self._videos[self._current_idx]
        subs = v.get("orig_subs" if self._show_original else "trans_subs", [])
        self._subtitles = subs
        for i, s in enumerate(subs):
            row = self.sub_table.rowCount()
            self.sub_table.insertRow(row)
            i0 = QTableWidgetItem(str(i + 1))
            i0.setFlags(i0.flags() & ~Qt.ItemFlag.ItemIsEditable)
            i0.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.sub_table.setItem(row, 0, i0)
            i1 = QTableWidgetItem(f"{_fmt_s(s['start'])} → {_fmt_s(s['end'])}")
            i1.setFlags(i1.flags() & ~Qt.ItemFlag.ItemIsEditable)
            i1.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.sub_table.setItem(row, 1, i1)
            self.sub_table.setItem(row, 2, QTableWidgetItem(s["text"]))
        self.sub_table.blockSignals(False)

    def _on_sub_dbl_click(self, row: int, col: int):
        """双击字幕行 → 进入编辑模式"""
        if col == 2 and self._current_idx >= 0:
            self.sub_table.editItem(self.sub_table.item(row, col))

    def _on_sub_click(self, row: int, col: int):
        if self._current_idx < 0: return
        v = self._videos[self._current_idx]
        subs = v.get("orig_subs" if self._show_original else "trans_subs", [])
        if 0 <= row < len(subs):
            self._player.setPosition(int(subs[row]["start"] * 1000))

    def _on_sub_edit(self, row: int, col: int):
        if col != 2 or self._current_idx < 0: return
        item = self.sub_table.item(row, col)
        if not item: return
        # 防抖：300ms 无编辑后才写入数据
        from PyQt6.QtCore import QTimer
        if self._sub_debounce is not None:
            self._sub_debounce.stop()
            self._sub_debounce.deleteLater()
        text = item.text()
        v = self._videos[self._current_idx]
        subs = v.get("orig_subs" if self._show_original else "trans_subs", [])
        self._sub_debounce = QTimer(self)
        self._sub_debounce.setSingleShot(True)
        self._sub_debounce.timeout.connect(lambda s=subs, r=row, t=text: self._apply_sub_edit(s, r, t))
        self._sub_debounce.start(300)

    def _apply_sub_edit(self, subs, row, text):
        """延迟应用字幕编辑（防抖后执行，验证列表仍属于当前视频）"""
        if self._current_idx < 0: return
        cur = self._videos[self._current_idx]
        cur_subs = cur.get("orig_subs" if self._show_original else "trans_subs", [])
        if id(subs) != id(cur_subs): return  # 视频已切换，丢弃
        if 0 <= row < len(subs):
            subs[row]["text"] = text
            self._subtitles = subs
            self.status_msg.emit(f"已发送{'原文' if self._show_original else '译文'}到语音台", "success")

    # ═══════════════ 一键批量分离
    def _batch_separate(self):
        checked_v = self._get_checked_videos()
        checked_a = self._get_checked_audios()
        queue = ([('video', item) for item in checked_v]
                 + [('audio', item) for item in checked_a])
        if not queue:
            self.status_msg.emit("请先勾选视频或音频", "warn")
            return
        self.status_msg.emit(f"批量分离 {len(queue)} 个素材...", "info")
        self._sep_queue = queue
        self._sep_idx = 0
        self._run_next_sep()

    def _run_next_sep(self):
        if self._sep_idx >= len(self._sep_queue):
            self.btn_batch_sep.setText("🎵 分离人声背景声")
            self.progress.setValue(100)
            # 刷新所有已分离项的按钮
            for kind, source in self._sep_queue:
                pool = self._videos if kind == "video" else self._audios
                if source not in pool:
                    continue
                idx = pool.index(source)
                if kind == "video":
                    self._refresh_item_widget(idx)
                else:
                    self._refresh_audio_widget(idx)
            self.status_msg.emit(f"全部 {len(self._sep_queue)} 个分离完成 ✓", "success")
            # 2 秒后进度条归零
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(2000, lambda: self.progress.setValue(0))
            # 清理 batch worker
            if self._batch_worker:
                self._batch_worker.deleteLater()
                self._batch_worker = None
            return
        # 清理上一个 batch worker
        if self._batch_worker:
            self._batch_worker.deleteLater()
        kind, source = self._sep_queue[self._sep_idx]
        pct = int((self._sep_idx) / len(self._sep_queue) * 100)
        self.btn_batch_sep.setText(f"分离中 {self._sep_idx+1}/{len(self._sep_queue)} {pct}%")
        self.progress.setValue(pct)
        self._batch_worker = _SepWorker(source["path"])
        self._batch_worker.finished.connect(
            lambda vw, bw, k=kind, s=source: self._on_batch_sep(k, s, vw, bw))
        self._batch_worker.error.connect(
            lambda e, k=kind, s=source: self._on_batch_sep_err(k, s, e))
        self._batch_worker.start()

    def _on_batch_sep(self, kind, source, vwav, bwav):
        pool = self._videos if kind == "video" else self._audios
        if source in pool:
            source["vocals"], source["bgm"] = self._rename_sep(
                source["path"], vwav, bwav)
            self.status_msg.emit(f"✓ {Path(source['path']).name}", "success")
        self._sep_idx += 1
        self._run_next_sep()

    def _on_batch_sep_err(self, kind, source, err):
        self.status_msg.emit(f"✗ {Path(source['path']).name}: {err}", "error")
        self._sep_idx += 1
        self._run_next_sep()

    # ═══════════════ 一键导出（按每个视频选中的音轨导出）
    def _batch_export(self):
        checked_v = self._get_checked_videos()
        if not checked_v:
            self.status_msg.emit("请至少勾选一个视频", "warn"); return

        checked_a = self._get_checked_audios()

        # ── 先预览导出清单 ──
        track_labels = {"original": "🎬 原声", "vocals": "🎤 人声", "bgm": "🎵 背景"}
        lines = []
        for i, vd in enumerate(checked_v, 1):
            name = vd.get("rename") or Path(vd["path"]).stem
            track = vd.get("track", "original")
            tl = track_labels.get(track, "原声")
            detail = ""
            if track == "original" and checked_a:
                anames = ", ".join(a.get("name", Path(a["path"]).stem)[:8] for a in checked_a[:3])
                if len(checked_a) > 3: anames += f" +{len(checked_a)-3}"
                detail = f" + 配音({anames})"
            elif track == "bgm" and checked_a:
                anames = ", ".join(a.get("name", Path(a["path"]).stem)[:8] for a in checked_a[:3])
                if len(checked_a) > 3: anames += f" +{len(checked_a)-3}"
                detail = f" + 配音({anames})"
            lines.append(f"{i}. {tl}  ←  {name}{detail}")

        preview = "\n".join(lines)
        total = len(checked_v) * max(1, len(checked_a)) if checked_a else len(checked_v)
        msg = f"将导出 {total} 个文件：\n\n{preview}\n\n继续导出？"
        reply = QMessageBox.question(self, "导出预览", msg,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok)
        if reply != QMessageBox.StandardButton.Ok:
            return

        out_dir = QFileDialog.getExistingDirectory(self, "导出目录")
        if not out_dir: return

        self._cleanup_export_worker()
        self._export_worker = _ExportWorker(checked_v, checked_a, out_dir,
            track_vols=self._track_vols)
        self._export_worker.progress.connect(self._on_export_progress)
        self._export_worker.done_one.connect(lambda n: self.status_msg.emit(f"✓ {n}", "success"))
        self._export_worker.finished.connect(lambda: self._on_export_all_done(out_dir))
        self._export_worker.error.connect(self._on_export_failed)

        self.btn_batch_export.setEnabled(False)
        self.btn_batch_export.setText("📦 导出中 0/0")
        self.progress.setValue(0)
        self._export_worker.start()

    def _on_export_progress(self, cur: int, total: int, name: str):
        pct = int(cur / total * 100) if total else 0
        self.progress.setValue(pct)
        self.btn_batch_export.setText(f"📦 导出中 {cur}/{total}")
        self.status_msg.emit(f"导出 [{cur}/{total}] {name}", "info")

    def _on_export_all_done(self, out_dir: str):
        self.btn_batch_export.setEnabled(True)
        self.btn_batch_export.setText("📦 一键导出")
        self.progress.setValue(100)
        self.status_msg.emit("全部导出完成 ✓", "success")
        # worker 已完成，安全清理
        if self._export_worker:
            self._export_worker.deleteLater()
            self._export_worker = None
        # 2 秒后进度条归零
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(2000, lambda: self.progress.setValue(0))

    def _on_export_failed(self, error):
        """导出失败后恢复按钮和进度，避免界面永久停在“导出中”。"""
        self.btn_batch_export.setEnabled(True)
        self.btn_batch_export.setText("📦 一键导出")
        self.progress.setValue(0)
        self.status_msg.emit(f"导出失败: {error}", "error")
        if self._export_worker is not None:
            self._export_worker.deleteLater()
            self._export_worker = None

    def _cleanup_export_worker(self):
        """安全清理导出 worker（复用模式）"""
        if self._export_worker is not None:
            old = self._export_worker
            try:
                old.finished.disconnect(); old.error.disconnect()
                old.progress.disconnect(); old.done_one.disconnect()
            except Exception: pass
            if old.isRunning():
                old.finished.connect(old.deleteLater)
            else:
                old.deleteLater()
            self._export_worker = None

    # ═══════════════ 批量重命名（导出文件命名）
    def _batch_rename(self):
        checked_v = self._get_checked_videos()
        if not checked_v:
            self.status_msg.emit("请先勾选视频", "warn"); return
        prefix, ok = QInputDialog.getText(self, "批量命名", "导出文件名前缀（自动编号）：", text="视频")
        if not ok or not prefix.strip(): return
        prefix = prefix.strip()
        for i, vd in enumerate(checked_v, 1):
            vd["rename"] = f"{prefix}{i:02d}"
            idx = self._videos.index(vd)
            if idx in self._item_widgets:
                self._item_widgets[idx].lbl.setText(f"📹 {vd['rename']}")
            self.status_msg.emit(f"→ {vd['rename']}", "info")
        self.status_msg.emit(f"已命名 {len(checked_v)} 个导出文件", "success")

    # ═══════════════ Worker 调度
    def _run_worker(self, w, done, err, btn, label):
        # 先清理旧 worker
        if self._worker is not None:
            old = self._worker
            try:
                old.finished.disconnect(); old.error.disconnect()
            except Exception: pass
            if old.isRunning():
                old.finished.connect(old.deleteLater)
            else:
                old.deleteLater()
        self._worker = w
        orig = btn.text()
        btn.setEnabled(False); btn.setText(label); self.progress.setValue(5)

        def _done(*args, _btn=btn, _orig=orig, _w=w):
            try:
                done(args[0] if len(args) == 1 else args)
            except Exception as e:
                self.status_msg.emit(f"回调错误: {e}", "error")
            finally:
                _btn.setEnabled(True); _btn.setText(_orig); self.progress.setValue(100)
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(2000, lambda: self.progress.setValue(0))
                # Worker 结束后延迟清理
                _w.deleteLater()
                if self._worker is _w:
                    self._worker = None

        def _err(e, _btn=btn, _orig=orig, _w=w):
            try:
                err(e)
            except Exception as ex:
                self.status_msg.emit(f"错误回调异常: {ex}", "error")
            finally:
                _btn.setEnabled(True); _btn.setText(_orig); self.progress.setValue(0)
                _w.deleteLater()
                if self._worker is _w:
                    self._worker = None

        w.finished.connect(_done)
        w.error.connect(_err)
        w.start()

    # ═══════════════ 播放器
    def _on_duration(self, ms):
        self.seek.setRange(0, ms)

    def _on_main_state(self, state):
        """视频停止 → 配音也停止"""
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self._voice_player.stop()
            self.btn_play.setText("▶")

    def _on_pos(self, pos):
        if not self._seeking:
            self.seek.setValue(pos)
        if self._first_frame and pos > 0:
            self._player.pause()
            self._first_frame = False
        d = self._player.duration()
        self.lbl_time.setText(f"{_fmt(pos)} / {_fmt(d)}" if d > 0 else "00:00 / 00:00")
        self._render_subtitle(pos)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._reposition_subtitle()

    def _reposition_subtitle(self):
        pw = self.video_widget.width(); ph = self.video_widget.height()
        sw = max(0, min(pw - 40, 700))
        self.subtitle_label.setFixedWidth(sw)
        self.subtitle_label.move((pw - sw) // 2, ph - 60)
        self.subtitle_label.raise_()

    def _render_subtitle(self, pos_ms):
        sec = pos_ms / 1000
        text = ""
        for s in self._subtitles:
            try:
                if s.get("start", 0) <= sec <= s.get("end", 0):
                    text = s.get("text", ""); break
            except (TypeError, KeyError):
                continue
        if text:
            self.subtitle_label.setText(text)
            self.subtitle_label.show()
            self._reposition_subtitle()
        else:
            self.subtitle_label.hide()


# ═══════════════ Workers ═══════════════
class _ExportWorker(QThread):
    """批量导出 Worker，逐个处理并报告进度"""
    progress = pyqtSignal(int, int, str)   # (current, total, filename)
    done_one = pyqtSignal(str)             # 完成单个文件的名称
    finished = pyqtSignal()                # 全部完成
    error = pyqtSignal(str)

    def __init__(self, videos, audios, out_dir, video_tracks=None, track_vols=None):
        """video_tracks: dict mapping video index to track type, or None to read from vd['track']"""
        super().__init__()
        self._videos = videos
        self._audios = audios
        self._out_dir = out_dir
        self._video_tracks = video_tracks or {}  # idx -> track type
        self._track_vols = track_vols or {"vocals": 80, "bgm": 60, "voice": 75}

    def run(self):
        try:
            from config import FFMPEG_BIN
            total = len(self._videos)
            for i, vd in enumerate(self._videos):
                basename = vd.get("_export_name") or vd.get("rename") or Path(vd["path"]).stem
                # 优先用传入的轨道映射，否则从 dict 读
                track = self._video_tracks.get(i, vd.get("track", "original"))
                self.progress.emit(i + 1, total, basename)
                # 导完清除临时名
                if "_export_name" in vd: vd.pop("_export_name", None)

                if track == "original":
                    if self._audios:
                        for a in self._audios:
                            aname = a.get("name", Path(a["path"]).stem)
                            adest = Path(self._out_dir) / f"{basename}_{aname}.mp4"
                            _ffmpeg_swap(vd["path"], a["path"], str(adest), keep_original=True,
                                bgm_vol=1.0, voice_vol=self._track_vols["voice"]/100)
                            self.done_one.emit(adest.name)
                    else:
                        dest = Path(self._out_dir) / f"{basename}{Path(vd['path']).suffix}"
                        if dest.exists(): dest = Path(self._out_dir) / f"{basename}_copy{Path(vd['path']).suffix}"
                        shutil.copy(vd["path"], dest)
                        self.done_one.emit(dest.name)

                elif track == "vocals" and vd.get("vocals"):
                    dest = Path(self._out_dir) / f"{basename}_人声.wav"
                    if dest.exists(): dest = Path(self._out_dir) / f"{basename}_人声_{int(time.time())}.wav"
                    # 人声导出：应用人声音量
                    if self._track_vols["vocals"] != 100:
                        tmp = Path(tempfile.gettempdir()) / f"xh_vol_voc_{basename}.wav"
                        subprocess.run([FFMPEG_BIN, "-y", "-i", vd["vocals"],
                            "-filter:a", f"volume={self._track_vols['vocals']/100:.2f}",
                            str(tmp)], capture_output=True)
                        shutil.copy(str(tmp), dest)
                    else:
                        shutil.copy(vd["vocals"], dest)
                    self.done_one.emit(dest.name)

                elif track == "bgm" and vd.get("bgm"):
                    if self._audios:
                        for a in self._audios:
                            aname = a.get("name", Path(a["path"]).stem)
                            adest = Path(self._out_dir) / f"{basename}_背景_{aname}.mp4"
                            # 一次 FFmpeg：视频画面 + BGM(调音量) + 配音，直接输出
                            vtmp = Path(tempfile.gettempdir()) / f"xh_bexp_{basename}.mp4"
                            r = subprocess.run([FFMPEG_BIN, "-y",
                                "-i", vd["path"], "-i", vd["bgm"], "-i", a["path"],
                                "-filter_complex",
                                f"[1:a]volume={self._track_vols.get('bgm',60)/100:.2f}[bga];"
                                f"[2:a]volume={self._track_vols.get('voice',75)/100:.2f}[va];"
                                f"[bga][va]amix=inputs=2:duration=longest:weights=1|1[aout]",
                                "-map", "0:v:0", "-map", "[aout]",
                                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                                "-shortest", "-movflags", "+faststart", str(vtmp)],
                                capture_output=True)
                            if r.returncode == 0 and vtmp.exists():
                                shutil.copy(str(vtmp), adest)
                            self.done_one.emit(adest.name)
                    else:
                        dest = Path(self._out_dir) / f"{basename}_去人声.mp4"
                        _ffmpeg_swap(vd["path"], vd["bgm"], str(dest),
                            voice_vol=self._track_vols["bgm"]/100)
                        self.done_one.emit(dest.name)

            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))

class _SepWorker(QThread):
    finished = pyqtSignal(str, str); error = pyqtSignal(str)
    def __init__(self, path): super().__init__(); self._path = path
    def run(self):
        try:
            from core.separator import AudioSeparator
            v, b = AudioSeparator().process_video(Path(self._path))
            self.finished.emit(str(v), str(b))
        except Exception as e:
            import traceback; self.error.emit(f"{e}\n{traceback.format_exc()}")

class _ASRWorker(QThread):
    finished = pyqtSignal(list); error = pyqtSignal(str)
    def __init__(self, audio_path, src_lang=""):
        super().__init__(); self._path = audio_path; self._src_lang = src_lang
    def run(self):
        try:
            from core.whisper_runner import run_whisper_asr
            entries = run_whisper_asr(self._path, language=self._src_lang)
            from core.transcriber import SRTEntry
            self.finished.emit([SRTEntry(i+1, e["start"], e["end"], e["text"]) for i, e in enumerate(entries)])
        except Exception as e:
            import traceback; self.error.emit(f"{e}\n{traceback.format_exc()}")

class _SubTransWorker(QThread):
    finished = pyqtSignal(list); error = pyqtSignal(str)
    def __init__(self, entries, target_lang):
        super().__init__(); self._entries = entries; self._lang = target_lang
    def run(self):
        try:
            from core.transcriber import Translator
            self.finished.emit(Translator(target_lang=self._lang).translate(self._entries))
        except Exception as e:
            import traceback; self.error.emit(f"{e}\n{traceback.format_exc()}")

def _ffmpeg_swap(video: str, audio: str, output: str, keep_original: bool = False,
                 bgm_vol: float = 1.0, voice_vol: float = 1.0):
    """
    替换视频音频轨
    keep_original=True: 混合原音轨 + 新音轨
    bgm_vol: 原音轨/BGM 音量 (0.0~1.0)
    voice_vol: 新音轨/TTS 音量 (0.0~1.0)
    """
    from config import FFMPEG_BIN
    if keep_original:
        r = subprocess.run([FFMPEG_BIN, "-y", "-i", video, "-i", audio,
            "-filter_complex",
            f"[1:a]volume={voice_vol:.2f}[vad];"
            f"[0:a]volume={bgm_vol:.2f}[bgad];"
            f"[bgad][vad]amix=inputs=2:duration=first:weights=1|1[aout]",
            "-map", "0:v:0", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart", output], capture_output=True)
    else:
        r = subprocess.run([FFMPEG_BIN, "-y", "-i", video, "-i", audio,
            "-filter_complex", f"[1:a]volume={voice_vol:.2f}[va];[va]anull[out]",
            "-map", "0:v:0", "-map", "[out]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart", output], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"FFmpeg: {r.stderr.decode('utf-8', errors='replace')[-400:]}")

def _fmt(ms: int) -> str:
    s = ms // 1000; return f"{s//60:02d}:{s%60:02d}"
def _fmt_s(sec: float) -> str:
    m = int(sec // 60); s = sec % 60; return f"{m:02d}:{s:04.1f}"


# ── 样式 ──
_MLIST = (
    "QListWidget{background:#151515;border:1px solid #2a2a2a;border-radius:6px;"
    "color:#aaa;font-size:12px;padding:6px;outline:none;}"
    "QListWidget::item{padding:5px 0px;border-bottom:1px solid #222;margin-bottom:6px;}"
    "QListWidget::item:hover{background:#1e2d3d;}"
    "QListWidget::item:selected{background:#1a3050;color:#3d8ef8;}")
_TINY_PRIMARY = (
    "QPushButton{background:#3d8ef8;color:#fff;border:none;border-radius:4px;"
    "font-size:11px;padding:3px 8px;}QPushButton:hover{background:#5a9ff9;}")
_FILTER_V_ON = (
    "QPushButton{background:#3d8ef8;color:#fff;border:2px solid #5a9ff9;border-radius:4px;"
    "font-size:11px;font-weight:bold;padding:2px 6px;}QPushButton:hover{background:#5a9ff9;}")
_TINY_GREEN = (
    "QPushButton{background:#2e7d32;color:#fff;border:none;border-radius:4px;"
    "font-size:11px;padding:3px 8px;}QPushButton:hover{background:#388e3c;}")
_FILTER_ON = (
    "QPushButton{background:#2e7d32;color:#fff;border:2px solid #4caf50;border-radius:4px;"
    "font-size:11px;font-weight:bold;padding:2px 6px;}QPushButton:hover{background:#388e3c;}")
_GHOST_TINY = (
    "QPushButton{background:transparent;color:#888;border:1px solid #333;"
    "border-radius:4px;font-size:11px;padding:2px 5px;}"
    "QPushButton:hover{color:#ccc;border-color:#555;}")
# 右上角一键导出——绿色大按钮
_EXPORT_BTN = (
    "QPushButton{background:#27ae60;color:#fff;border:none;border-radius:5px;"
    "font-size:12px;font-weight:bold;padding:5px 14px;}"
    "QPushButton:hover{background:#2ecc71;}"
    "QPushButton:disabled{background:#1a5e30;color:#4a8c5e;}")
# 操作栏分离人声背景声——蓝色主调（深一档区别于小按钮）
_SEP_BATCH_BTN = (
    "QPushButton{background:#1a6fd4;color:#fff;border:none;border-radius:4px;"
    "font-size:11px;font-weight:bold;padding:5px 12px;}"
    "QPushButton:hover{background:#3d8ef8;}"
    "QPushButton:disabled{background:#1a3050;color:#4a6a98;}")
_CTRL_BTN = (
    "QPushButton{background:#2a2a2a;color:#fff;border:none;border-radius:4px;"
    "font-size:14px;}QPushButton:hover{background:#3a3a3a;}")
_ACT = (
    "QPushButton{background:#1e1e1e;color:#aaa;border:1px solid #333;border-radius:6px;"
    "font-size:12px;padding:7px 16px;}QPushButton:hover{color:#ccc;border-color:#3d8ef8;}"
    "QPushButton:disabled{color:#555;}")
_PROG = (
    "QProgressBar{background:#1a1a1a;border:none;border-radius:1px;}"
    "QProgressBar::chunk{background:#2a4a70;border-radius:1px;}")
# ── 列表内音轨小按钮样式 ──
_TK_SM_ON = (
    "QPushButton{background:#3d8ef8;color:#fff;border:none;"
    "border-radius:4px;font-size:10px;font-weight:bold;padding:2px 6px;}"
    "QPushButton:hover{background:#5a9ff9;}")
_TK_SM_OFF = (
    "QPushButton{background:#2a2a2a;color:#888;border:1px solid #444;"
    "border-radius:4px;font-size:10px;padding:2px 6px;}"
    "QPushButton:hover{color:#ccc;border-color:#3d8ef8;}")
_TK_SM_HIDE = (
    "QPushButton{background:transparent;color:transparent;border:none;"
    "border-radius:4px;font-size:10px;padding:2px 6px;}"
    "QPushButton:hover{background:transparent;}")
_LANG_ON = (
    "QPushButton{background:#1a3050;color:#3d8ef8;border:1px solid #3d8ef8;"
    "border-radius:3px;font-size:11px;font-weight:bold;padding:3px 8px;}")
_LANG_OFF = (
    "QPushButton{background:#1e1e1e;color:#666;border:1px solid #2a2a2a;"
    "border-radius:3px;font-size:11px;padding:3px 8px;}"
    "QPushButton:hover{color:#ccc;border-color:#555;}")
_SUBTAB_ON = (
    "QPushButton{background:#3d8ef8;color:#fff;border:none;"
    "border-radius:4px;font-size:11px;font-weight:bold;padding:3px 10px;}")
_SUBTAB_OFF = (
    "QPushButton{background:#1e1e1e;color:#555;border:1px solid #2a2a2a;"
    "border-radius:4px;font-size:11px;padding:3px 10px;}"
    "QPushButton:hover{color:#aaa;border-color:#555;}")
_TABLE = (
    "QTableWidget{background:#0e0e0e;border:1px solid #222;border-radius:4px;"
    "color:#aaa;font-size:12px;gridline-color:#1a1a1a;}"
    "QTableWidget::item{padding:3px 6px;}"
    "QTableWidget::item:selected{background:#1a3050;color:#3d8ef8;}"
    "QHeaderView::section{background:#141414;color:#555;border:none;"
    "border-bottom:1px solid #222;border-right:1px solid #1a1a1a;"
    "font-size:11px;padding:4px 8px;}"
    "QTableWidget::item:edit{background:#1a2a3a;color:#ccc;}")
