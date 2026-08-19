"""
mix_handler.py — 视频混剪 UI 模块（完整版）
混入 UltimateEngine 使用
布局：左（素材管理）| 中（预览播放）| 右（参数+导出）
"""

import os
import sys
import subprocess
from utils.ffmpeg_utils import get_ffmpeg_path
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QGroupBox, QLineEdit, QComboBox, QFileDialog,
    QMessageBox, QProgressBar, QSizePolicy,
    QDoubleSpinBox, QTextEdit, QGridLayout, QFrame,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QUrl
from .widgets import CheckMarkBox
from PyQt6.QtGui import QColor, QDragEnterEvent, QDropEvent, QPainter, QPen
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget

from core.mix_engine import (
    ClipMaterial, MixMode, ComboCalculator, TaskGenerator, FFmpegMixer
)


# ==================== 混剪工作线程 ====================

class MixWorkerThread(QThread):
    log_signal      = pyqtSignal(str)
    progress_signal = pyqtSignal(int, int)
    finished_signal = pyqtSignal()

    def __init__(self, tasks, audio_path, out_dir, rename_tpl,
                 total_duration, target_w, target_h, ffmpeg_path='ffmpeg'):
        super().__init__()
        self.tasks         = tasks
        self.audio_path    = audio_path
        self.out_dir       = out_dir
        self.rename_tpl    = rename_tpl
        self.total_dur     = total_duration
        self.target_w      = target_w
        self.target_h      = target_h
        self.ffmpeg_path   = ffmpeg_path
        self._is_running   = True

    def stop(self):
        self._is_running = False

    def run(self):
        mixer = FFmpegMixer(ffmpeg_path=self.ffmpeg_path,
                            log_callback=self.log_signal.emit)
        if not mixer.check_ffmpeg():
            self.log_signal.emit(
                "未检测到 FFmpeg！请安装并加入系统 PATH。\n"
                "下载: https://ffmpeg.org/download.html")
            self.finished_signal.emit()
            return

        total = len(self.tasks)
        now   = datetime.now().strftime("%Y%m%d")

        for i, task_slots in enumerate(self.tasks):
            if not self._is_running:
                self.log_signal.emit("已停止。")
                break
            idx_str = str(i + 1).zfill(3)
            if self.rename_tpl.strip():
                name = (self.rename_tpl
                        .replace('{index}', idx_str)
                        .replace('{date}', now))
                # 自定义模板未写 {index} 时也必须追加序号，否则批量任务会
                # 全部覆盖到同一个 mp4，最终目录里看起来只有一条。
                if '{index}' not in self.rename_tpl:
                    name = f"{name}_{idx_str}"
            else:
                name = f"mix_{idx_str}_{now}"

            out_path = os.path.join(self.out_dir, f"{name}.mp4")
            self.log_signal.emit(f"[{i+1}/{total}] 渲染: {name}.mp4")

            ok = mixer.render_task(
                task_slots=task_slots, audio_path=self.audio_path,
                out_path=out_path, total_duration=self.total_dur,
                target_w=self.target_w, target_h=self.target_h,
            )
            self.progress_signal.emit(i + 1, total)
            self.log_signal.emit(
                f"{'完成' if ok else '失败'} [{i+1}/{total}] {name}.mp4")

        self.finished_signal.emit()


# ==================== 素材区间控件 ====================

class RangeWidget(QWidget):
    changed = pyqtSignal()

    def __init__(self, duration: float = 0.0):
        super().__init__()
        self._dur = max(duration, 0.1)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(3)

        self.spin_s = QDoubleSpinBox()
        self.spin_s.setRange(0, max(0, self._dur - 0.1))
        self.spin_s.setValue(0)
        self.spin_s.setSuffix("s")
        self.spin_s.setDecimals(1)
        self.spin_s.setFixedWidth(68)
        self.spin_s.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        lbl = QLabel("~")
        lbl.setFixedWidth(8)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.spin_e = QDoubleSpinBox()
        self.spin_e.setRange(0.1, self._dur)
        self.spin_e.setValue(self._dur)
        self.spin_e.setSuffix("s")
        self.spin_e.setDecimals(1)
        self.spin_e.setFixedWidth(68)
        self.spin_e.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        lay.addWidget(self.spin_s)
        lay.addWidget(lbl)
        lay.addWidget(self.spin_e)
        lay.addStretch()

        self.spin_s.valueChanged.connect(self.changed.emit)
        self.spin_e.valueChanged.connect(self.changed.emit)

    def get_range(self):
        return self.spin_s.value(), self.spin_e.value()


# ==================== 简易时间轴 ====================

class MixTimeline(QWidget):
    seek_requested = pyqtSignal(float)

    def __init__(self):
        super().__init__()
        self.setFixedHeight(36)
        self.ratio = 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setBrush(QColor("#222"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 8, w, 20, 3, 3)
        played_w = int(w * self.ratio)
        if played_w > 0:
            p.setBrush(QColor("#007acc"))
            p.drawRoundedRect(0, 8, played_w, 20, 3, 3)
        p.setPen(QPen(QColor("white"), 2))
        p.drawLine(played_w, 2, played_w, 34)

    def mousePressEvent(self, event):
        self._seek(event.pos().x())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._seek(event.pos().x())

    def _seek(self, x):
        w = self.width()
        if w > 0:
            self.ratio = max(0.0, min(1.0, x / w))
            self.update()
            self.seek_requested.emit(self.ratio)

    def set_ratio(self, r: float):
        self.ratio = max(0.0, min(1.0, r))
        self.update()


# ==================== 混剪 UI Mixin ====================

class MixHandler:

    # ------------------------------------------------------------------ #
    #  顶层构建
    # ------------------------------------------------------------------ #
    def build_mix_module(self):
        self._mix_materials  = {'random': [], 'head': [], 'mid': [], 'tail': [], 'suffix': []}
        self._mix_audio_path = ""
        self._mix_out_dir    = ""
        self._mix_tasks      = []
        self._mix_worker     = None

        root_w = QWidget()
        root_w.setAcceptDrops(True)
        root_w.dragEnterEvent = self._mix_drag_enter
        root_w.dropEvent      = self._mix_drop

        root = QHBoxLayout(root_w)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # 左栏
        root.addWidget(self._build_left_panel(), 4)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setStyleSheet("color:#2d2d2d;")
        root.addWidget(sep1)

        # 中栏
        root.addWidget(self._build_mid_panel(), 4)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet("color:#2d2d2d;")
        root.addWidget(sep2)

        # 右栏
        root.addWidget(self._build_right_panel(), 3)

        self._refresh_mode_ui()
        return root_w

    # ------------------------------------------------------------------ #
    #  左栏：素材管理
    # ------------------------------------------------------------------ #
    def _build_left_panel(self):
        w = QWidget()
        ll = QVBoxLayout(w)
        ll.setSpacing(6)
        ll.setContentsMargins(0, 0, 0, 0)

        title = QLabel("素材库")
        title.setStyleSheet("font-weight:bold; font-size:15px; color:#e0e0e0;")
        ll.addWidget(title)

        hint = QLabel("拖拽视频到对应标签页  |  可为每条素材设定可用区间")
        hint.setStyleSheet("color:#555; font-size:11px;")
        hint.setWordWrap(True)
        ll.addWidget(hint)

        # 模式选择
        mode_grp = QGroupBox("混剪模式")
        mode_grp.setStyleSheet(self._grp_style("#00eaff"))
        mgl = QVBoxLayout(mode_grp)
        mgl.setSpacing(6)

        self.mix_mode_combo = QComboBox()
        self.mix_mode_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mix_mode_combo.addItems([MixMode.MODE_A, MixMode.MODE_B, MixMode.MODE_C,
                                      MixMode.MODE_D, MixMode.MODE_E, MixMode.MODE_F])
        self.mix_mode_combo.currentIndexChanged.connect(self._refresh_mode_ui)
        mgl.addWidget(self.mix_mode_combo)

        self.mix_mode_desc = QLabel()
        self.mix_mode_desc.setStyleSheet("color:#888; font-size:11px;")
        self.mix_mode_desc.setWordWrap(True)
        mgl.addWidget(self.mix_mode_desc)

        ll.addWidget(mode_grp)

        # 素材 Tab
        self.mix_mat_tabs = self._build_mat_tabs()
        ll.addWidget(self.mix_mat_tabs, 1)

        return w

    def _build_mat_tabs(self):
        from PyQt6.QtWidgets import QTabWidget
        tabs = QTabWidget()
        tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tabs.setStyleSheet("""
            QTabBar::tab { padding:6px 10px; font-size:12px; }
            QTabBar::tab:selected { color:#00eaff; border-bottom:2px solid #00eaff; }
            QTabBar::tab:disabled { color:#3a3a3a; }
        """)

        self._mat_tables = {}
        configs = [
            ('random', '随机素材', False),
            ('head',   '固定开头', True),
            ('mid',    '固定中间', True),
            ('tail',   '固定结尾', True),
            ('suffix', '固定后段', False),
        ]
        for role, label, is_fixed in configs:
            tab_w, table = self._build_mat_tab(role, is_fixed)
            self._mat_tables[role] = table
            tabs.addTab(tab_w, label)

        return tabs

    def _build_mat_tab(self, role: str, is_fixed: bool):
        w = QWidget()
        w.setAcceptDrops(True)
        w.dragEnterEvent = self._mix_drag_enter
        w.dropEvent      = lambda e, r=role: self._mix_drop_role(e, r)

        vl = QVBoxLayout(w)
        vl.setContentsMargins(2, 4, 2, 2)
        vl.setSpacing(4)

        if is_fixed:
            lim = QLabel("此槽位只能放 1 条素材（固定不变）")
            lim.setStyleSheet(
                "color:#ffaa00; font-size:11px; "
                "background:#2a2000; border-radius:3px; padding:3px 8px;")
            vl.addWidget(lim)

        # 按钮行
        btn_lay = QHBoxLayout()
        btn_add = QPushButton("➕ 导入素材")
        btn_add.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_add.clicked.connect(lambda: self._mix_import_files(role))
        btn_del = QPushButton("删除选中")
        btn_del.setObjectName("DangerBtn")
        btn_del.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_del.clicked.connect(lambda: self._mix_delete_mat(role))
        btn_clr = QPushButton("清空")
        btn_clr.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_clr.clicked.connect(lambda: self._mix_clear_mat(role))
        btn_lay.addWidget(btn_add)
        btn_lay.addWidget(btn_del)
        btn_lay.addWidget(btn_clr)
        btn_lay.addStretch()
        vl.addLayout(btn_lay)

        # 表格
        table = QTableWidget(0, 4)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setHorizontalHeaderLabels(["文件名", "总时长", "可用区间", "可用时长"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.itemSelectionChanged.connect(lambda r=role: self._on_mix_mat_selected(r))
        # viewport 接受拖拽
        table.viewport().setAcceptDrops(True)
        table.viewport().dragEnterEvent = self._mix_drag_enter
        table.viewport().dropEvent      = lambda e, r=role: self._mix_drop_role(e, r)
        vl.addWidget(table)

        return w, table

    # ------------------------------------------------------------------ #
    #  中栏：预览播放器
    # ------------------------------------------------------------------ #
    def _build_mid_panel(self):
        w = QWidget()
        ml = QVBoxLayout(w)
        ml.setSpacing(6)
        ml.setContentsMargins(0, 0, 0, 0)

        title = QLabel("素材预览")
        title.setStyleSheet("font-weight:bold; font-size:15px; color:#e0e0e0;")
        ml.addWidget(title)

        hint = QLabel("点击素材列表预览  |  空格 播放/暂停  |  ← → 逐帧微调")
        hint.setStyleSheet("color:#555; font-size:11px;")
        ml.addWidget(hint)

        self.mix_video_widget = QVideoWidget()
        self.mix_video_widget.setMinimumHeight(280)
        self.mix_video_widget.setStyleSheet(
            "background:#000; border:1px solid #333; border-radius:4px;")
        ml.addWidget(self.mix_video_widget, 1)

        self.mix_player   = QMediaPlayer()
        self.mix_audio_out = QAudioOutput()
        self.mix_player.setAudioOutput(self.mix_audio_out)
        self.mix_player.setVideoOutput(self.mix_video_widget)
        self.mix_player.positionChanged.connect(self._mix_on_pos_changed)
        self.mix_player.durationChanged.connect(self._mix_on_dur_changed)

        # 时间轴
        self.mix_timeline = MixTimeline()
        self.mix_timeline.seek_requested.connect(self._mix_seek)
        ml.addWidget(self.mix_timeline)

        # 时间标签
        self.mix_time_label = QLabel("00:00 / 00:00")
        self.mix_time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mix_time_label.setStyleSheet("color:#888; font-size:11px;")
        ml.addWidget(self.mix_time_label)

        # 控制按钮
        ctrl = QHBoxLayout()
        self.mix_btn_play = QPushButton("▶ 播放")
        self.mix_btn_play.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mix_btn_play.clicked.connect(self._mix_toggle_play)

        btn_bwd = QPushButton("◀◀ -1s")
        btn_bwd.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_bwd.clicked.connect(lambda: self._mix_step(-1000))

        btn_fwd = QPushButton("+1s ▶▶")
        btn_fwd.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_fwd.clicked.connect(lambda: self._mix_step(1000))

        ctrl.addWidget(self.mix_btn_play)
        ctrl.addWidget(btn_bwd)
        ctrl.addWidget(btn_fwd)
        ml.addLayout(ctrl)

        # 素材信息
        self.mix_preview_info = QLabel("未选择素材")
        self.mix_preview_info.setStyleSheet(
            "color:#666; font-size:11px; padding:4px; "
            "background:#151515; border-radius:3px;")
        self.mix_preview_info.setWordWrap(True)
        ml.addWidget(self.mix_preview_info)

        return w

    # ------------------------------------------------------------------ #
    #  右栏：参数 + 导出
    # ------------------------------------------------------------------ #
    def _build_right_panel(self):
        w = QWidget()
        rl = QVBoxLayout(w)
        rl.setSpacing(8)
        rl.setContentsMargins(0, 0, 0, 0)

        title = QLabel("输出配置")
        title.setStyleSheet("font-weight:bold; font-size:15px; color:#e0e0e0;")
        rl.addWidget(title)

        # -- 片段时长（模式A/B用，总时长自动计算；模式C只显示总时长）--
        seg_grp = QGroupBox("片段时长设置")
        seg_grp.setStyleSheet(self._grp_style("#00eaff"))
        sgl = QGridLayout(seg_grp)
        sgl.setSpacing(6)

        sgl.addWidget(QLabel("开头:"), 0, 0)
        self.spin_head = QDoubleSpinBox()
        self.spin_head.setRange(0.5, 300); self.spin_head.setValue(3.0)
        self.spin_head.setSuffix("s"); self.spin_head.setDecimals(1)
        self.spin_head.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.spin_head.valueChanged.connect(self._sync_total)
        sgl.addWidget(self.spin_head, 0, 1)

        sgl.addWidget(QLabel("中间:"), 0, 2)
        self.spin_mid = QDoubleSpinBox()
        self.spin_mid.setRange(0.5, 300); self.spin_mid.setValue(7.0)
        self.spin_mid.setSuffix("s"); self.spin_mid.setDecimals(1)
        self.spin_mid.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.spin_mid.valueChanged.connect(self._sync_total)
        sgl.addWidget(self.spin_mid, 0, 3)

        sgl.addWidget(QLabel("结尾:"), 1, 0)
        self.spin_tail = QDoubleSpinBox()
        self.spin_tail.setRange(0.5, 300); self.spin_tail.setValue(5.0)
        self.spin_tail.setSuffix("s"); self.spin_tail.setDecimals(1)
        self.spin_tail.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.spin_tail.valueChanged.connect(self._sync_total)
        sgl.addWidget(self.spin_tail, 1, 1)

        sgl.addWidget(QLabel("总时长:"), 1, 2)
        self.spin_total = QDoubleSpinBox()
        self.spin_total.setRange(1, 300); self.spin_total.setValue(15.0)
        self.spin_total.setSuffix("s"); self.spin_total.setDecimals(1)
        self.spin_total.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.spin_total.valueChanged.connect(self._update_combo_analysis)
        sgl.addWidget(self.spin_total, 1, 3)

        self.lbl_seg_hint = QLabel()
        self.lbl_seg_hint.setStyleSheet("color:#555; font-size:10px;")
        sgl.addWidget(self.lbl_seg_hint, 2, 0, 1, 4)

        rl.addWidget(seg_grp)

        # -- 输出尺寸（可直接输入）--
        size_grp = QGroupBox("输出尺寸")
        size_grp.setStyleSheet(self._grp_style("#00eaff"))
        szl = QVBoxLayout(size_grp)
        szl.setSpacing(6)

        preset_lay = QHBoxLayout()
        for label, ww, hh in [("9:16",1080,1920),("4:5",1080,1350),
                               ("1:1",1080,1080),("16:9",1920,1080)]:
            btn = QPushButton(label)
            btn.setFixedWidth(52)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setStyleSheet("padding:4px; font-size:11px;")
            btn.clicked.connect(lambda _, a=ww, b=hh: self._mix_set_size(a, b))
            preset_lay.addWidget(btn)
        preset_lay.addStretch()
        szl.addLayout(preset_lay)

        wh_lay = QHBoxLayout()
        wh_lay.addWidget(QLabel("W:"))
        self.mix_out_w = QLineEdit("1080")
        self.mix_out_w.setFixedWidth(64)
        self.mix_out_w.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wh_lay.addWidget(self.mix_out_w)
        wh_lay.addWidget(QLabel("H:"))
        self.mix_out_h = QLineEdit("1920")
        self.mix_out_h.setFixedWidth(64)
        self.mix_out_h.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wh_lay.addWidget(self.mix_out_h)
        wh_lay.addStretch()
        szl.addLayout(wh_lay)
        rl.addWidget(size_grp)

        # -- 背景音频 --
        audio_grp = QGroupBox("背景音频（所有视频共用）")
        audio_grp.setStyleSheet(self._grp_style("#00eaff"))
        agl = QVBoxLayout(audio_grp)
        self.mix_audio_btn = QPushButton("点击选择音频（mp3 / wav / aac）")
        self.mix_audio_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mix_audio_btn.setStyleSheet("""
            QPushButton {
                text-align:left; padding-left:10px; height:30px;
                border:1px dashed #444; border-radius:4px;
                background:#1e1e1e; color:#888;
            }
            QPushButton:hover { border:1px solid #00eaff; color:#00eaff; }
        """)
        self.mix_audio_btn.clicked.connect(self._mix_select_audio)
        agl.addWidget(self.mix_audio_btn)
        rl.addWidget(audio_grp)

        # -- 输出设置 --
        out_grp = QGroupBox("输出设置")
        out_grp.setStyleSheet(self._grp_style("#00eaff"))
        ogl = QGridLayout(out_grp)
        ogl.setSpacing(8)

        ogl.addWidget(QLabel("生成数量:"), 0, 0)
        self.mix_count_input = QLineEdit("10")
        self.mix_count_input.setFixedWidth(70)
        self.mix_count_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mix_count_input.textChanged.connect(self._update_combo_analysis)
        ogl.addWidget(self.mix_count_input, 0, 1)

        ogl.addWidget(QLabel("命名模板:"), 1, 0)
        self.mix_rename = QLineEdit()
        self.mix_rename.setPlaceholderText("留空: mix_{index}_{date}")
        ogl.addWidget(self.mix_rename, 1, 1)
        rl.addWidget(out_grp)

        # -- 素材分析 --
        analysis_grp = QGroupBox("素材分析")
        analysis_grp.setStyleSheet(self._grp_style("#00eaff"))
        angl = QVBoxLayout(analysis_grp)
        self.mix_analysis_label = QLabel("导入素材后自动分析")
        self.mix_analysis_label.setWordWrap(True)
        self.mix_analysis_label.setStyleSheet(
            "color:#aaa; font-size:11px; padding:4px; "
            "background:#151515; border-radius:3px;")
        angl.addWidget(self.mix_analysis_label)
        rl.addWidget(analysis_grp)

        # -- 日志 --
        log_grp = QGroupBox("渲染日志")
        log_grp.setStyleSheet(
            "QGroupBox{color:#444;border:1px solid #222;"
            "border-radius:4px;margin-top:10px;padding-top:8px;}")
        lgl = QVBoxLayout(log_grp)
        self.mix_console = QTextEdit()
        self.mix_console.setReadOnly(True)
        self.mix_console.setMaximumHeight(110)
        self.mix_console.setStyleSheet(
            "background:#000; color:#00eaff; border:none; "
            "font-family:Consolas; font-size:11px;")
        lgl.addWidget(self.mix_console)
        rl.addWidget(log_grp)

        rl.addStretch()

        # 进度条
        self.mix_progress = QProgressBar()
        self.mix_progress.setValue(0)
        self.mix_progress.setVisible(False)
        rl.addWidget(self.mix_progress)

        self.cb_mix_open_dir = CheckMarkBox("完成后自动打开输出文件夹")
        self.cb_mix_open_dir.setChecked(True)
        self.cb_mix_open_dir.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        rl.addWidget(self.cb_mix_open_dir)

        self.mix_btn_stop = QPushButton("停止渲染")
        self.mix_btn_stop.setObjectName("DangerBtn")
        self.mix_btn_stop.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mix_btn_stop.setEnabled(False)
        self.mix_btn_stop.clicked.connect(self._mix_stop)
        rl.addWidget(self.mix_btn_stop)

        self.mix_btn_start = QPushButton("开始批量混剪")
        self.mix_btn_start.setObjectName("PrimaryBtn")
        self.mix_btn_start.setFixedHeight(50)
        self.mix_btn_start.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mix_btn_start.clicked.connect(self._mix_start)
        rl.addWidget(self.mix_btn_start)

        return w

    # ------------------------------------------------------------------ #
    #  辅助样式
    # ------------------------------------------------------------------ #
    @staticmethod
    def _grp_style(color):
        return (f"QGroupBox{{color:{color};font-weight:bold;"
                f"border:1px solid #333;border-radius:4px;"
                f"margin-top:10px;padding-top:8px;}}")

    # ------------------------------------------------------------------ #
    #  模式切换联动
    # ------------------------------------------------------------------ #
    def _refresh_mode_ui(self):
        if not hasattr(self, 'mix_mode_combo'):
            return
        mode = self.mix_mode_combo.currentText()

        desc = {
            MixMode.MODE_A: "前N秒 固定开头 → 中间M秒 随机换 → 后K秒 固定结尾\n需要：固定开头×1、固定结尾×1、随机素材若干",
            MixMode.MODE_B: "前N秒 随机换 → 中间M秒 固定不变 → 后K秒 随机换\n需要：固定中间×1、随机素材若干",
            MixMode.MODE_C: "全程随机拼接达到总时长，只需导入随机素材\n固定开头/中间/结尾 在此模式下禁用",
            MixMode.MODE_D: "前N秒 固定开头 → 剩余时长全部随机变换\n需要：固定开头×1、随机素材若干；直接填写总时长",
            MixMode.MODE_E: "前段全部随机变换 → 后K秒 固定结尾\n需要：固定结尾×1、随机素材若干；直接填写总时长",
            MixMode.MODE_F: "按导入顺序逐条取每个视频的前4秒 → 后面统一接固定后段\n素材与成片一一对应：20个片头素材输出20条视频，只使用背景音频",
        }
        self.mix_mode_desc.setText(desc.get(mode, ""))

        # Tab 启用/禁用：0=随机 1=固定开头 2=固定中间 3=固定结尾 4=固定后段
        enabled_map = {
            MixMode.MODE_A: [True, True,  False, True,  False],
            MixMode.MODE_B: [True, False, True,  False, False],
            MixMode.MODE_C: [True, False, False, False, False],
            MixMode.MODE_D: [True, True,  False, False, False],
            MixMode.MODE_E: [True, False, False, True,  False],
            MixMode.MODE_F: [True, False, False, False, True ],
        }
        for i, en in enumerate(enabled_map.get(mode, [True]*5)):
            self.mix_mat_tabs.setTabEnabled(i, en)
            if not en:
                role = ['random','head','mid','tail','suffix'][i]
                self._mat_tables[role].setRowCount(0)
                self._mix_materials[role] = []

        # 片段时长：C/D/E/F 总时长可填写/显示；A/B 自动计算只读
        is_c = mode == MixMode.MODE_C
        is_d = mode == MixMode.MODE_D
        is_e = mode == MixMode.MODE_E
        is_f = mode == MixMode.MODE_F
        self.mix_mat_tabs.setTabText(0, "顺序片头素材" if is_f else "随机素材")
        self.mix_count_input.setReadOnly(is_f)
        self.spin_head.setVisible(not is_c and not is_e and not is_f)
        self.spin_mid.setVisible(not is_c and not is_d and not is_e and not is_f)
        self.spin_tail.setVisible(not is_c and not is_d and not is_f)
        self.spin_total.setReadOnly(not is_c and not is_d and not is_e)
        if is_f:
            self.spin_total.setStyleSheet(
                "background:#1a1a1a; color:#00eaff; font-weight:bold;")
            self.lbl_seg_hint.setText(
                "顺序片头模式：每条素材取前4秒 + 固定后段，输出数量等于片头素材数")
            self._sync_total_f()  # 计算 4s + 后段总时长
        elif is_c:
            self.spin_total.setStyleSheet("")
            self.lbl_seg_hint.setText("模式C：直接设置总时长即可")
        elif is_d:
            self.spin_total.setStyleSheet("")
            self.lbl_seg_hint.setText("模式D：设置「开头」时长 + 「总时长」，随机段自动填满剩余")
        elif is_e:
            self.spin_total.setStyleSheet("")
            self.lbl_seg_hint.setText("模式E：设置「结尾」时长 + 「总时长」，随机段自动填满剩余")
        else:
            self.spin_total.setStyleSheet("background:#1a1a1a; color:#666;")
            self.lbl_seg_hint.setText("总时长 = 开头 + 中间 + 结尾（自动计算）")
            self._sync_total()

        self._update_combo_analysis()

    def _sync_total(self):
        """模式A/B：总时长 = 开头+中间+结尾（自动计算写入只读框）"""
        if not hasattr(self, 'mix_mode_combo'):
            return
        mode = self.mix_mode_combo.currentText()
        if mode in (MixMode.MODE_A, MixMode.MODE_B):
            total = (self.spin_head.value() +
                     self.spin_mid.value() +
                     self.spin_tail.value())
            self.spin_total.blockSignals(True)
            self.spin_total.setValue(total)
            self.spin_total.blockSignals(False)
        self._update_combo_analysis()

    def _sync_total_f(self):
        """模式F：总时长 = 4s片头 + 后段各素材可用时长之和"""
        if not hasattr(self, 'mix_mode_combo'):
            return
        suffix_dur = sum(
            m.usable_duration for m in self._mix_materials.get('suffix', []))
        total = 4.0 + suffix_dur
        self.spin_total.blockSignals(True)
        self.spin_total.setValue(total)
        self.spin_total.blockSignals(False)
        self._update_combo_analysis()

    def _maybe_sync_total_f(self):
        """仅在模式F时触发总时长同步"""
        if hasattr(self, 'mix_mode_combo') and self.mix_mode_combo.currentText() == MixMode.MODE_F:
            self._sync_total_f()

    # ------------------------------------------------------------------ #
    #  尺寸 / 数量工具
    # ------------------------------------------------------------------ #
    def _mix_set_size(self, w: int, h: int):
        self.mix_out_w.setText(str(w))
        self.mix_out_h.setText(str(h))

    def _get_mix_count(self) -> int:
        try:
            return max(1, int(self.mix_count_input.text()))
        except ValueError:
            return 1

    def _get_mix_size(self) -> tuple:
        try:
            w = max(240, min(4096, int(self.mix_out_w.text())))
            h = max(240, min(4096, int(self.mix_out_h.text())))
            return w, h
        except ValueError:
            return 1080, 1920

    # ------------------------------------------------------------------ #
    #  拖拽
    # ------------------------------------------------------------------ #
    def _mix_drag_enter(self, event: QDragEnterEvent):
        event.accept() if event.mimeData().hasUrls() else event.ignore()

    def _mix_drop(self, event: QDropEvent):
        self._mix_drop_role(event, 'random')

    def _mix_drop_role(self, event: QDropEvent, role: str):
        files = [u.toLocalFile() for u in event.mimeData().urls()]
        vids  = [f for f in files
                 if f.lower().endswith(('.mp4','.mov','.avi','.mkv'))]
        if vids:
            self._mix_add_files(role, vids)

    # ------------------------------------------------------------------ #
    #  导入素材
    # ------------------------------------------------------------------ #
    def _mix_import_files(self, role: str):
        files, _ = QFileDialog.getOpenFileNames(
            self, "导入素材", "", "Videos (*.mp4 *.mov *.avi *.mkv)")
        if files:
            self._mix_add_files(role, files)

    def _mix_add_files(self, role: str, files: list):
        import cv2
        table    = self._mat_tables[role]
        existing = {m.path for m in self._mix_materials[role]}
        is_fixed = role in ('head', 'mid', 'tail')

        for f in files:
            if f in existing:
                continue
            if is_fixed and len(self._mix_materials[role]) >= 1:
                QMessageBox.warning(
                    self, "提醒",
                    f"固定槽位只能放 1 条素材，请先删除再导入新的！")
                break

            try:
                cap   = cv2.VideoCapture(f)
                fps   = cap.get(cv2.CAP_PROP_FPS)
                count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                dur   = count / fps if fps > 0 else 0.0
                cap.release()
            except Exception:
                dur = 0.0

            mat = ClipMaterial(f, start=0.0, end=dur)
            mat._duration = dur
            self._mix_materials[role].append(mat)

            r = table.rowCount()
            table.insertRow(r)

            name_item = QTableWidgetItem(os.path.basename(f))
            name_item.setData(Qt.ItemDataRole.UserRole, f)
            table.setItem(r, 0, name_item)
            table.setItem(r, 1, QTableWidgetItem(f"{dur:.1f}s"))

            rw = RangeWidget(dur)
            rw.changed.connect(
                lambda m=mat, w=rw, ro=role: self._on_mix_range_changed(m, w, ro))
            table.setCellWidget(r, 2, rw)
            table.setItem(r, 3, QTableWidgetItem(f"{dur:.1f}s"))
            existing.add(f)

        self._update_combo_analysis()

        # 模式F：后段素材导入/删除后更新总时长
        if role == 'suffix' and hasattr(self, 'mix_mode_combo'):
            if self.mix_mode_combo.currentText() == MixMode.MODE_F:
                self._sync_total_f()

    def _on_mix_range_changed(self, mat: ClipMaterial, rw: RangeWidget, role: str):
        s, e = rw.get_range()
        mat.start = s
        mat.end   = e
        table = self._mat_tables[role]
        for r in range(table.rowCount()):
            if table.cellWidget(r, 2) is rw:
                table.setItem(r, 3, QTableWidgetItem(f"{mat.usable_duration:.1f}s"))
                break
        # 模式F：后段时长变化时更新总时长
        if role == 'suffix' and hasattr(self, 'mix_mode_combo'):
            if self.mix_mode_combo.currentText() == MixMode.MODE_F:
                self._sync_total_f()
        self._update_combo_analysis()

    # ------------------------------------------------------------------ #
    #  删除 / 清空
    # ------------------------------------------------------------------ #
    def _mix_delete_mat(self, role: str):
        table = self._mat_tables[role]
        rows  = sorted({i.row() for i in table.selectedIndexes()}, reverse=True)
        for r in rows:
            table.removeRow(r)
            if r < len(self._mix_materials[role]):
                self._mix_materials[role].pop(r)
        self._update_combo_analysis()
        if role == 'suffix':
            self._maybe_sync_total_f()

    def _mix_clear_mat(self, role: str):
        self._mat_tables[role].setRowCount(0)
        self._mix_materials[role] = []
        self._update_combo_analysis()
        if role == 'suffix':
            self._maybe_sync_total_f()

    # ------------------------------------------------------------------ #
    #  预览播放
    # ------------------------------------------------------------------ #
    def _on_mix_mat_selected(self, role: str):
        table = self._mat_tables[role]
        sel   = table.selectedItems()
        if not sel:
            return
        row  = sel[0].row()
        mats = self._mix_materials[role]
        if row >= len(mats):
            return
        mat = mats[row]

        seek_ms = int(mat.start * 1000) if mat.start > 0 else 0

        def _on_loaded(status):
            if status == QMediaPlayer.MediaStatus.LoadedMedia:
                self.mix_player.mediaStatusChanged.disconnect(_on_loaded)
                self.mix_player.pause()
                self.mix_btn_play.setText("▶ 播放")
                if seek_ms > 0:
                    QTimer.singleShot(50, lambda: self.mix_player.setPosition(seek_ms))

        self.mix_player.mediaStatusChanged.connect(_on_loaded)
        self.mix_player.setSource(QUrl.fromLocalFile(mat.path))

        self.mix_preview_info.setText(
            f"{os.path.basename(mat.path)}  |  "
            f"总时长: {mat._duration:.1f}s  |  "
            f"可用区间: {mat.start:.1f}s ~ {mat.end:.1f}s  |  "
            f"可用时长: {mat.usable_duration:.1f}s"
        )

    def _mix_toggle_play(self):
        if self.mix_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.mix_player.pause()
            self.mix_btn_play.setText("▶ 播放")
        else:
            self.mix_player.play()
            self.mix_btn_play.setText("⏸ 暂停")

    def _mix_step(self, ms: int):
        if self.mix_player.duration() > 0:
            pos = max(0, min(self.mix_player.duration(),
                             self.mix_player.position() + ms))
            self.mix_player.setPosition(pos)
            self.mix_player.pause()
            self.mix_btn_play.setText("▶ 播放")

    def _mix_seek(self, ratio: float):
        if self.mix_player.duration() > 0:
            self.mix_player.setPosition(int(self.mix_player.duration() * ratio))

    def _mix_on_pos_changed(self, pos_ms: int):
        dur = self.mix_player.duration()
        if dur > 0:
            self.mix_timeline.set_ratio(pos_ms / dur)
        self.mix_time_label.setText(
            f"{self._ms2str(pos_ms)} / {self._ms2str(dur)}")

    def _mix_on_dur_changed(self, dur_ms: int):
        self.mix_time_label.setText(f"00:00 / {self._ms2str(dur_ms)}")

    @staticmethod
    def _ms2str(ms: int) -> str:
        s = ms // 1000
        return f"{s // 60:02d}:{s % 60:02d}"

    def mix_key_press(self, event) -> bool:
        """在主窗口 keyPressEvent 里调用，返回 True 表示已消费"""
        if event.key() == Qt.Key.Key_Space:
            if hasattr(self, 'mix_btn_play'):
                self._mix_toggle_play()
                return True
        elif event.key() == Qt.Key.Key_Left:
            if hasattr(self, 'mix_player'):
                self._mix_step(-100)
                return True
        elif event.key() == Qt.Key.Key_Right:
            if hasattr(self, 'mix_player'):
                self._mix_step(100)
                return True
        return False

    # ------------------------------------------------------------------ #
    #  音频
    # ------------------------------------------------------------------ #
    def _mix_select_audio(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "选择背景音频", "",
            "Audio (*.mp3 *.wav *.aac *.m4a *.flac)")
        if f:
            self._mix_audio_path = f
            self.mix_audio_btn.setText(f"已选: {os.path.basename(f)}")
            self.mix_audio_btn.setToolTip(f)

    # ------------------------------------------------------------------ #
    #  素材分析
    # ------------------------------------------------------------------ #
    def _update_combo_analysis(self):
        if not hasattr(self, 'mix_analysis_label'):
            return
        mode   = self.mix_mode_combo.currentText()
        if mode == MixMode.MODE_F:
            head_count = len(self._mix_materials['random'])
            self.mix_count_input.blockSignals(True)
            self.mix_count_input.setText(str(head_count))
            self.mix_count_input.blockSignals(False)
            suffix = self._mix_materials.get('suffix', [])
            suffix_total = sum(m.usable_duration for m in suffix)
            self.mix_analysis_label.setText(
                f"顺序片头素材：{head_count} 条 → 输出 {head_count} 条视频\n"
                f"每条取前4秒，按导入顺序处理，不随机、不重复\n"
                f"固定后段：{len(suffix)} 个片段，共 {suffix_total:.1f}s")
            return
        target = self._get_mix_count()
        head_s = self.spin_head.value()
        mid_s  = self.spin_mid.value()
        tail_s = self.spin_tail.value()

        fixed = {
            'head': self._mix_materials['head'][0] if self._mix_materials['head'] else None,
            'mid':  self._mix_materials['mid'][0]  if self._mix_materials['mid']  else None,
            'tail': self._mix_materials['tail'][0] if self._mix_materials['tail'] else None,
        }
        info = ComboCalculator.estimate_info(
            mode=mode, fixed_materials=fixed,
            random_materials=self._mix_materials['random'],
            target_count=target,
            seg_a=(head_s, mid_s, tail_s),
            seg_b=(head_s, mid_s, tail_s),
            total=self.spin_total.value(),
            max_reuse=2,
        )
        lines = [info['message'],
                 f"当前随机素材: {len(self._mix_materials['random'])} 条  |  每条最多使用 2 次"]
        # 模式F：显示后段信息
        if mode == MixMode.MODE_F:
            suffix_count = len(self._mix_materials.get('suffix', []))
            suffix_total = sum(m.usable_duration for m in self._mix_materials.get('suffix', []))
            lines.append(f"固定后段: {suffix_count} 个片段  |  后段总时长: {suffix_total:.1f}s")
        if not info['is_feasible'] and info['need_random_mats'] > 0:
            lines.append(f"建议补充到 {info['need_random_mats']} 条随机素材")
        self.mix_analysis_label.setText("\n".join(lines))

    # ------------------------------------------------------------------ #
    #  开始混剪
    # ------------------------------------------------------------------ #
    def _mix_start(self):
        mode   = self.mix_mode_combo.currentText()
        count  = self._get_mix_count()
        total  = self.spin_total.value()
        head_s = self.spin_head.value()
        mid_s  = self.spin_mid.value()
        tail_s = self.spin_tail.value()
        tw, th = self._get_mix_size()

        if not self._mix_materials['random']:
            source_label = "顺序片头素材" if mode == MixMode.MODE_F else "随机素材"
            QMessageBox.warning(self, "提醒", f"请先在「{source_label}」标签页导入素材！")
            return
        if mode == MixMode.MODE_A:
            if not self._mix_materials['head']:
                QMessageBox.warning(self, "提醒", "模式A 需要「固定开头」素材！"); return
            if not self._mix_materials['tail']:
                QMessageBox.warning(self, "提醒", "模式A 需要「固定结尾」素材！"); return
        elif mode == MixMode.MODE_B:
            if not self._mix_materials['mid']:
                QMessageBox.warning(self, "提醒", "模式B 需要「固定中间」素材！"); return
        elif mode == MixMode.MODE_D:
            if not self._mix_materials['head']:
                QMessageBox.warning(self, "提醒", "模式D 需要「固定开头」素材！"); return
        elif mode == MixMode.MODE_E:
            if not self._mix_materials['tail']:
                QMessageBox.warning(self, "提醒", "模式E 需要「固定结尾」素材！"); return
        elif mode == MixMode.MODE_F:
            suffix = self._mix_materials.get('suffix', [])
            if not suffix:
                QMessageBox.warning(self, "提醒", "模式F 需要「固定后段」素材（至少1个片段）！"); return
            # 过滤掉时长≤0的后段素材，确保至少有一个有效
            valid_suffix = [m for m in suffix if m.usable_duration > 0.1]
            if not valid_suffix:
                QMessageBox.warning(self, "提醒", "固定后段素材的可用时长不足！请检查区间设置。"); return
            # 顺序片头模式：每个片头素材的前4秒分别接同一套固定后段。
            # 输出数量始终等于片头素材数量，不使用普通模式的手填数量。
            count = len(self._mix_materials['random'])
            self.mix_count_input.setText(str(count))

        out_dir = QFileDialog.getExistingDirectory(self, "选择输出文件夹")
        if not out_dir:
            return
        self._mix_out_dir = out_dir

        fixed = {
            'head': self._mix_materials['head'][0] if self._mix_materials['head'] else None,
            'mid':  self._mix_materials['mid'][0]  if self._mix_materials['mid']  else None,
            'tail': self._mix_materials['tail'][0] if self._mix_materials['tail'] else None,
        }
        gen = TaskGenerator(
            mode=mode, fixed_materials=fixed,
            random_materials=self._mix_materials['random'],
            seg_a=(head_s, mid_s, tail_s),
            seg_b=(head_s, mid_s, tail_s),
            total=total,
            suffix_materials=self._mix_materials.get('suffix', []) if mode == MixMode.MODE_F else None,
        )
        self._mix_tasks = gen.generate(count, max_reuse=2)
        self._mix_log(f"已生成 {len(self._mix_tasks)} 个任务，开始渲染...")

        self.mix_btn_start.setEnabled(False)
        self.mix_btn_stop.setEnabled(True)
        self.mix_progress.setVisible(True)
        self.mix_progress.setMaximum(count)
        self.mix_progress.setValue(0)

        self._mix_worker = MixWorkerThread(
            tasks=self._mix_tasks,
            audio_path=self._mix_audio_path,
            out_dir=out_dir,
            rename_tpl=self.mix_rename.text().strip(),
            total_duration=total,
            target_w=tw, target_h=th,
            ffmpeg_path=get_ffmpeg_path(),
        )
        self._mix_worker.log_signal.connect(self._mix_log)
        self._mix_worker.progress_signal.connect(
            lambda cur, tot: self.mix_progress.setValue(cur))
        self._mix_worker.finished_signal.connect(self._mix_on_finished)
        self._mix_worker.start()

    def _mix_stop(self):
        if self._mix_worker and self._mix_worker.isRunning():
            self._mix_worker.stop()
            self._mix_log("正在等待当前任务完成后停止...")

    def _mix_on_finished(self):
        self.mix_btn_start.setEnabled(True)
        self.mix_btn_stop.setEnabled(False)
        self.mix_progress.setVisible(False)
        self._mix_log("全部完成！")
        QMessageBox.information(self, "完成", "混剪任务全部完成！")
        if self.cb_mix_open_dir.isChecked() and self._mix_out_dir:
            if os.path.exists(self._mix_out_dir):
                if sys.platform == 'win32':
                    os.startfile(self._mix_out_dir)
                elif sys.platform == 'darwin':
                    subprocess.Popen(['open', self._mix_out_dir])
                else:
                    subprocess.Popen(['xdg-open', self._mix_out_dir])

    # ------------------------------------------------------------------ #
    #  日志
    # ------------------------------------------------------------------ #
    def _mix_log(self, text: str):
        ts = datetime.now().strftime('%H:%M:%S')
        self.mix_console.append(f"[{ts}] {text}")
        self.mix_console.moveCursor(
            self.mix_console.textCursor().MoveOperation.End)
