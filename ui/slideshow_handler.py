# ui/slideshow_handler.py
"""
图片轮播视频生成器 UI 模块 — 混入 UltimateEngine 使用
布局：左（参数设置 + 转场 + BGM + 尾页 + 输出设置）| 右（素材库 + 生成控制）
"""

import os
import sys
import json
import random
import shutil
import threading
import time
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QGroupBox, QLineEdit, QComboBox, QFileDialog, QSlider,
    QSpinBox, QMessageBox, QProgressBar, QSizePolicy,
    QScrollArea, QGridLayout, QFrame, QTextEdit, QListWidget,
    QListWidgetItem, QAbstractItemView, QSplitter
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QUrl, QSize
from PyQt6.QtGui import QPixmap, QImage, QColor, QIcon
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

from core.slideshow_engine import (
    render_video, mix_audio, concat_videos,
    TRANSITIONS, TRANS_DESCS, IMG_EXTS, is_image,
    get_video_duration
)
from utils.ffmpeg_utils import get_ffmpeg_path
from .widgets import CheckMarkBox


# ─── 默认配置 ──────────────────────────────────────────────

DEFAULT_CFG = {
    "imgs_per_video": 8,
    "video_duration": 10,
    "video_count": 20,
    "fps": 30,
    "resolution": "1080x1920",
    "transition_frames": 15,
    "video_quality": "minimal",
    "bgm_paths": [],
    "bgm_volume": 80,
    "endpage_enabled": False,
    "endpage_path": "",
    "transition_type": "推进放大",
    "random_transition": False,
    "random_bgm": False,
    "bgm_selected": 0,
    "shuffle_mode": True,
    "output_dir": "",
    "file_prefix": "slideshow",
}


# ─── 轮播渲染线程 ──────────────────────────────────────────

class SlideshowRenderThread(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int, int)   # current, total
    video_done_signal = pyqtSignal(int, int)  # video_idx, total
    finished_signal = pyqtSignal(bool, str)    # success, message

    def __init__(self, images, cfg, ffmpeg_path):
        super().__init__()
        self.images = images
        self.cfg = cfg
        self.ffmpeg_path = ffmpeg_path
        self._is_running = True
        self._stop_event = threading.Event()

    def stop(self):
        self._is_running = False
        self._stop_event.set()

    def run(self):
        try:
            imgs_per = self.cfg.get("imgs_per_video", 8)
            count = self.cfg.get("video_count", 20)
            prefix = self.cfg.get("file_prefix", "slideshow") or "slideshow"
            out_dir = Path(self.cfg.get("output_dir", ""))
            out_dir.mkdir(parents=True, exist_ok=True)

            bgm_list = self.cfg.get("bgm_paths", [])
            ep_on = self.cfg.get("endpage_enabled", False)
            ep_path = self.cfg.get("endpage_path", "")
            vol = self.cfg.get("bgm_volume", 80)
            random_trans = self.cfg.get("random_transition", False)
            trans_keys = list(TRANSITIONS.keys()) if random_trans else None
            shuffle_mode = self.cfg.get("shuffle_mode", True)

            pool = list(self.images)
            n_pool = len(pool)
            if n_pool == 0:
                self.finished_signal.emit(False, "没有可用的图片")
                return

            # 洗牌轮巡
            seed = None
            cursor = 0
            shuf_pool = list(pool)
            if shuffle_mode:
                seed_file = out_dir / f"_{prefix}_shuffle.json"
                if seed_file.exists():
                    try:
                        with open(seed_file, "r", encoding="utf-8") as f:
                            sd = json.load(f)
                        seed = sd["seed"]
                        videos_done = sd["videos"]
                    except Exception:
                        seed = random.randint(0, 2**31 - 1)
                        videos_done = 0
                else:
                    seed = random.randint(0, 2**31 - 1)
                    videos_done = 0
                rng = random.Random(seed)
                rng.shuffle(shuf_pool)
                # 恢复游标
                for _ in range(videos_done):
                    if cursor + imgs_per > n_pool:
                        rng.shuffle(shuf_pool)
                        cursor = 0
                    cursor += imgs_per

            for i in range(count):
                if not self._is_running:
                    break

                # BGM 选择
                if self.cfg.get("random_bgm", False) and len(bgm_list) > 1:
                    bgm = random.choice(bgm_list)
                else:
                    sel = self.cfg.get("bgm_selected", 0)
                    bgm = bgm_list[sel] if 0 <= sel < len(bgm_list) else (
                        bgm_list[0] if bgm_list else "")

                # 随机转场
                if trans_keys:
                    self.cfg["transition_type"] = random.choice(trans_keys)

                # 选图
                if shuffle_mode:
                    if cursor + imgs_per > n_pool:
                        random.shuffle(shuf_pool)
                        cursor = 0
                    selected = shuf_pool[cursor:cursor + imgs_per]
                    cursor += imgs_per
                else:
                    if imgs_per <= n_pool:
                        selected = random.sample(pool, imgs_per)
                    else:
                        selected = random.choices(pool, k=imgs_per)

                out_base = out_dir / f"{prefix}_{i + 1:03d}"
                raw_video = out_dir / f"{prefix}_{i + 1:03d}_raw.mp4"
                final_video = out_dir / f"{prefix}_{i + 1:03d}.mp4"

                # 文件名冲突
                if final_video.exists():
                    suffix = 1
                    while final_video.exists():
                        final_video = out_dir / f"{prefix}_{i + 1:03d}_{suffix}.mp4"
                        suffix += 1

                self.log_signal.emit(f"[{i+1}/{count}] 渲染轮播视频…")

                try:
                    render_video(selected, raw_video, self.cfg,
                                 stop_event=self._stop_event)
                    current = raw_video
                except Exception as e:
                    self.log_signal.emit(f"❌ 第 {i+1} 个渲染失败: {e}")
                    if raw_video.exists():
                        try:
                            raw_video.unlink()
                        except Exception:
                            pass
                    continue

                # BGM 混音
                if bgm and os.path.exists(bgm) and current.exists():
                    self.log_signal.emit(f"[{i+1}/{count}] 混音…")
                    try:
                        mixed = out_dir / f"{prefix}_{i + 1:03d}_mixed.mp4"
                        mix_audio(current, bgm, mixed, vol, self.ffmpeg_path,
                                  stop_event=self._stop_event)
                        current = mixed
                    except Exception as e:
                        self.log_signal.emit(f"⚠ BGM混音失败，输出无BGM视频")

                # 尾页拼接
                if ep_on and ep_path and os.path.exists(ep_path) and current.exists():
                    self.log_signal.emit(f"[{i+1}/{count}] 拼接尾页…")
                    try:
                        concat_videos(current, ep_path, final_video,
                                      self.ffmpeg_path, stop_event=self._stop_event)
                        current = final_video
                    except Exception as e:
                        self.log_signal.emit(f"⚠ 尾页拼接失败: {e}")
                        if current.exists():
                            try:
                                if final_video.exists():
                                    final_video.unlink()
                                shutil.move(str(current), str(final_video))
                                current = final_video
                            except Exception:
                                pass
                else:
                    if current.exists() and current != final_video:
                        try:
                            if final_video.exists():
                                final_video.unlink()
                            shutil.move(str(current), str(final_video))
                            current = final_video
                        except Exception:
                            pass

                # 清理临时
                for tmp in [raw_video, out_dir / f"{prefix}_{i + 1:03d}_mixed.mp4"]:
                    if tmp.exists() and tmp != final_video:
                        try:
                            tmp.unlink()
                        except Exception:
                            pass

                # 洗牌状态持久化
                if shuffle_mode and seed is not None:
                    try:
                        seed_file = out_dir / f"_{prefix}_shuffle.json"
                        with open(seed_file, "w", encoding="utf-8") as f:
                            json.dump({"seed": seed, "videos": i + 1}, f)
                    except Exception:
                        pass

                self.video_done_signal.emit(i + 1, count)
                self.progress_signal.emit(i + 1, count)

            if not self._is_running:
                self.finished_signal.emit(False, "已停止")
            else:
                self.finished_signal.emit(True, f"已完成 {count} 个视频")

        except Exception as e:
            self.finished_signal.emit(False, f"生成出错: {e}")


# ─── 素材缩略图列表项 ──────────────────────────────────────

class ImageThumbItem(QListWidgetItem):
    """带图片路径的列表项 — 仅 hover 显示文件名，双击出预览"""
    def __init__(self, path, icon=None):
        display_name = Path(path).stem[:18] + ('…' if len(Path(path).stem) > 18 else '')
        if icon is not None:
            super().__init__(icon, display_name)
        else:
            super().__init__(display_name)
        self.image_path = path
        # 强制固定尺寸，防止 IconMode 下无图标时塌缩堆叠
        self.setSizeHint(QSize(130, 110))
        self.setToolTip(os.path.basename(path))   # hover 只显示文件名
        self.setFlags(self.flags() | Qt.ItemFlag.ItemIsSelectable)


# ─── SlideshowHandler Mixin ────────────────────────────────

class SlideshowHandler:

    def build_slideshow_module(self):
        """构建图片轮播模块 UI，返回 QWidget"""
        page = QWidget()
        main_lay = QHBoxLayout(page)
        main_lay.setContentsMargins(8, 8, 8, 8)
        main_lay.setSpacing(8)

        # 初始化数据（必须在构建 UI 之前）
        self._ss_images = []
        self._ss_cfg = dict(DEFAULT_CFG)
        self._ss_load_config()

        # 初始化 BGM 试听播放器
        self._bgm_player = QMediaPlayer()
        self._bgm_audio_output = QAudioOutput()
        self._bgm_player.setAudioOutput(self._bgm_audio_output)
        self._bgm_player.mediaStatusChanged.connect(self._ss_on_bgm_media_status)

        # ── 左侧：参数面板（可滚动） ──
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFixedWidth(360)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_inner = QWidget()
        left_lay = QVBoxLayout(left_inner)
        left_lay.setSpacing(6)

        # 参数设置
        self._build_ss_config(left_lay)
        # 转场效果
        self._build_ss_transition(left_lay)
        # BGM
        self._build_ss_bgm(left_lay)
        # 尾页
        self._build_ss_endpage(left_lay)
        # 输出设置
        self._build_ss_output(left_lay)

        left_lay.addStretch()
        left_scroll.setWidget(left_inner)
        main_lay.addWidget(left_scroll)

        # ── 右侧：素材库 + 控制区 ──
        right_w = QWidget()
        right_lay = QVBoxLayout(right_w)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(8)

        # 素材工具栏
        tb = QHBoxLayout()
        tb.addWidget(QLabel("📁 素材库", styleSheet="font-weight:bold; font-size:14px; color:#3d8ef8;"))

        self.btn_ss_add = QPushButton("添加图片")
        self.btn_ss_add.clicked.connect(lambda: self._ss_add_images())
        tb.addWidget(self.btn_ss_add)

        self.btn_ss_del_sel = QPushButton("删除所选")
        self.btn_ss_del_sel.setObjectName("DangerBtn")
        self.btn_ss_del_sel.clicked.connect(self._ss_delete_selected)
        self.btn_ss_del_sel.setEnabled(False)
        tb.addWidget(self.btn_ss_del_sel)

        self.btn_ss_clear = QPushButton("清空")
        self.btn_ss_clear.setObjectName("DangerBtn")
        self.btn_ss_clear.clicked.connect(self._ss_clear_images)
        tb.addWidget(self.btn_ss_clear)

        self.lbl_ss_count = QLabel("")
        self.lbl_ss_count.setStyleSheet("color:#888888;")
        tb.addStretch()
        tb.addWidget(self.lbl_ss_count)
        right_lay.addLayout(tb)

        # 素材列表（图标模式）
        self.ss_image_list = QListWidget()
        self.ss_image_list.itemSelectionChanged.connect(self._ss_on_selection_changed)
        self.ss_image_list.itemDoubleClicked.connect(self._ss_preview_image)
        self.ss_image_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.ss_image_list.setIconSize(QSize(120, 90))
        self.ss_image_list.setResizeMode(QListWidget.ResizeMode.Fixed)
        self.ss_image_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.ss_image_list.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.ss_image_list.setAcceptDrops(True)
        self.ss_image_list.setStyleSheet("""
            QListWidget {
                background: #1a1a1a;
                border: 1px solid #333;
                border-radius: 4px;
            }
            QListWidget::item {
                background: #252525;
                border: 1px solid #383838;
                border-radius: 6px;
                margin: 3px;
                padding: 2px;
            }
            QListWidget::item:selected {
                background: #1a3a5c;
                border: 1px solid #3d8ef8;
            }
            QListWidget::item:hover {
                background: #2a2a2a;
            }
        """)
        right_lay.addWidget(self.ss_image_list, stretch=1)

        # 底部：状态栏 + 生成按钮
        bottom = QHBoxLayout()

        self.lbl_ss_status = QLabel("就绪")
        self.lbl_ss_status.setStyleSheet("color:#888888;")
        bottom.addWidget(self.lbl_ss_status)

        self.pb_ss = QProgressBar()
        self.pb_ss.setFixedWidth(250)
        self.pb_ss.setValue(0)
        bottom.addWidget(self.pb_ss)

        bottom.addStretch()

        self.lbl_ss_stat = QLabel("")
        self.lbl_ss_stat.setStyleSheet("color:#888888;")
        bottom.addWidget(self.lbl_ss_stat)

        self.btn_ss_gen = QPushButton("生成视频")
        self.btn_ss_gen.setObjectName("PrimaryBtn")
        self.btn_ss_gen.setFixedWidth(130)
        self.btn_ss_gen.setFixedHeight(36)
        self.btn_ss_gen.clicked.connect(self._ss_on_gen_click)
        bottom.addWidget(self.btn_ss_gen)

        right_lay.addLayout(bottom)

        main_lay.addWidget(right_w, stretch=1)

        # 刷新状态
        self._ss_refresh_image_list()
        self._ss_update_stat()

        return page

    # ════════════════════════════════════════════════════
    #  参数设置
    # ════════════════════════════════════════════════════

    def _build_ss_config(self, parent_lay):
        grp = QGroupBox("⚙️ 参数设置")
        lay = QGridLayout()
        row = 0

        # 生成数量
        lay.addWidget(QLabel("生成数量:"), row, 0)
        self.sp_ss_count = QSpinBox()
        self.sp_ss_count.setRange(1, 500)
        self.sp_ss_count.setValue(self._ss_cfg_get("video_count", 20))
        self.sp_ss_count.valueChanged.connect(self._ss_on_cfg_changed)
        lay.addWidget(self.sp_ss_count, row, 1)
        row += 1

        # 每视频图片
        lay.addWidget(QLabel("每视频图片:"), row, 0)
        self.sp_ss_imgs = QSpinBox()
        self.sp_ss_imgs.setRange(2, 50)
        self.sp_ss_imgs.setValue(self._ss_cfg_get("imgs_per_video", 8))
        self.sp_ss_imgs.valueChanged.connect(self._ss_on_cfg_changed)
        lay.addWidget(self.sp_ss_imgs, row, 1)
        row += 1

        # 视频时长
        lay.addWidget(QLabel("视频时长(秒):"), row, 0)
        self.sp_ss_dur = QSpinBox()
        self.sp_ss_dur.setRange(3, 120)
        self.sp_ss_dur.setValue(self._ss_cfg_get("video_duration", 10))
        self.sp_ss_dur.valueChanged.connect(self._ss_on_cfg_changed)
        lay.addWidget(self.sp_ss_dur, row, 1)
        row += 1

        # 帧率
        lay.addWidget(QLabel("帧率(FPS):"), row, 0)
        self.cb_ss_fps = QComboBox()
        self.cb_ss_fps.addItems(["24", "25", "30", "60"])
        self.cb_ss_fps.setCurrentText(str(self._ss_cfg_get("fps", 30)))
        self.cb_ss_fps.currentTextChanged.connect(self._ss_on_cfg_changed)
        lay.addWidget(self.cb_ss_fps, row, 1)
        row += 1

        # 转场帧数（下拉选项，和帧率同样的逻辑）
        lay.addWidget(QLabel("转场帧数:"), row, 0)
        self.cb_ss_tf = QComboBox()
        self.cb_ss_tf.addItems(["5", "10", "15", "20", "25", "30", "45", "60"])
        self.cb_ss_tf.setCurrentText(str(self._ss_cfg_get("transition_frames", 15)))
        self.cb_ss_tf.currentTextChanged.connect(self._ss_on_cfg_changed)
        lay.addWidget(self.cb_ss_tf, row, 1)
        row += 1

        # 分辨率
        lay.addWidget(QLabel("分辨率:"), row, 0)
        self.cb_ss_res = QComboBox()
        self.cb_ss_res.addItems(["1080x1920", "720x1280", "540x960", "2160x3840", "1440x2560"])
        self.cb_ss_res.setCurrentText(self._ss_cfg_get("resolution", "1080x1920"))
        self.cb_ss_res.currentTextChanged.connect(self._ss_on_cfg_changed)
        lay.addWidget(self.cb_ss_res, row, 1)
        row += 1

        # 文件名前缀
        lay.addWidget(QLabel("文件名前缀:"), row, 0)
        self.le_ss_prefix = QLineEdit(self._ss_cfg_get("file_prefix", "slideshow"))
        self.le_ss_prefix.textChanged.connect(self._ss_on_cfg_changed)
        lay.addWidget(self.le_ss_prefix, row, 1)
        row += 1

        # 素材排重
        self.chk_ss_shuffle = CheckMarkBox("素材排重 (洗牌轮巡)")
        self.chk_ss_shuffle.setChecked(self._ss_cfg_get("shuffle_mode", True))
        self.chk_ss_shuffle.stateChanged.connect(self._ss_on_shuffle_toggle)
        lay.addWidget(self.chk_ss_shuffle, row, 0, 1, 2)
        row += 1

        # 排重模式说明
        self.lbl_ss_shuffle_desc = QLabel("")
        self.lbl_ss_shuffle_desc.setWordWrap(True)
        self.lbl_ss_shuffle_desc.setStyleSheet("color:#888888; font-size:11px; padding:2px 8px;")
        lay.addWidget(self.lbl_ss_shuffle_desc, row, 0, 1, 2)
        row += 1

        # 确定按钮
        self.btn_ss_apply_cfg = QPushButton("确定")
        self.btn_ss_apply_cfg.setObjectName("PrimaryBtn")
        self.btn_ss_apply_cfg.setFixedHeight(32)
        self.btn_ss_apply_cfg.clicked.connect(self._ss_apply_config)
        lay.addWidget(self.btn_ss_apply_cfg, row, 0, 1, 2)
        row += 1

        grp.setLayout(lay)
        parent_lay.addWidget(grp)
        self._ss_on_shuffle_toggle()

    # ════════════════════════════════════════════════════
    #  转场效果
    # ════════════════════════════════════════════════════

    def _build_ss_transition(self, parent_lay):
        grp = QGroupBox("🎞️ 转场效果")
        lay = QVBoxLayout()

        # 随机转场
        self.chk_ss_random_trans = CheckMarkBox("随机转场")
        self.chk_ss_random_trans.setChecked(self._ss_cfg_get("random_transition", False))
        self.chk_ss_random_trans.stateChanged.connect(self._ss_on_trans_toggle)
        lay.addWidget(self.chk_ss_random_trans)

        # 转场选择
        hl = QHBoxLayout()
        hl.addWidget(QLabel("转场:"))
        self.cb_ss_trans = QComboBox()
        self.cb_ss_trans.addItems(list(TRANSITIONS.keys()))
        self.cb_ss_trans.setCurrentText(self._ss_cfg_get("transition_type", "推进放大"))
        self.cb_ss_trans.currentTextChanged.connect(self._ss_on_trans_change)
        hl.addWidget(self.cb_ss_trans, stretch=1)
        lay.addLayout(hl)

        # 转场描述
        self.lbl_ss_trans_desc = QLabel("")
        self.lbl_ss_trans_desc.setWordWrap(True)
        self.lbl_ss_trans_desc.setStyleSheet("color:#3d8ef8; font-size:11px; padding:4px 8px; background:#1a2a3a; border-radius:4px;")
        lay.addWidget(self.lbl_ss_trans_desc)
        self._ss_on_trans_change(self.cb_ss_trans.currentText())
        self._ss_on_trans_toggle()

        grp.setLayout(lay)
        parent_lay.addWidget(grp)

    def _ss_on_trans_toggle(self):
        is_random = self.chk_ss_random_trans.isChecked()
        self.cb_ss_trans.setEnabled(not is_random)
        if is_random:
            self.lbl_ss_trans_desc.setText("🎲 每个视频随机选择一种转场效果")
        else:
            self._ss_on_trans_change(self.cb_ss_trans.currentText())
        self._ss_save_config()

    def _ss_on_trans_change(self, name):
        if not self.chk_ss_random_trans.isChecked():
            self.lbl_ss_trans_desc.setText(TRANS_DESCS.get(name, ""))
        self._ss_save_config()

    # ════════════════════════════════════════════════════
    #  BGM
    # ════════════════════════════════════════════════════

    def _build_ss_bgm(self, parent_lay):
        grp = QGroupBox("🎵 背景音乐")
        lay = QVBoxLayout()

        # 随机BGM + 添加/清空按钮
        hl = QHBoxLayout()
        self.chk_ss_random_bgm = CheckMarkBox("随机BGM")
        self.chk_ss_random_bgm.setChecked(self._ss_cfg_get("random_bgm", False))
        self.chk_ss_random_bgm.stateChanged.connect(self._ss_save_config)
        hl.addWidget(self.chk_ss_random_bgm)

        btn_add_bgm = QPushButton("添加BGM")
        btn_add_bgm.clicked.connect(self._ss_pick_bgm)
        hl.addWidget(btn_add_bgm)

        btn_clear_bgm = QPushButton("清空")
        btn_clear_bgm.setObjectName("DangerBtn")
        btn_clear_bgm.clicked.connect(self._ss_clear_bgm)
        hl.addWidget(btn_clear_bgm)
        lay.addLayout(hl)

        # BGM 列表
        self.lst_ss_bgm = QListWidget()
        self.lst_ss_bgm.setMinimumHeight(120)
        self.lst_ss_bgm.setStyleSheet("""
            QListWidget { background: #1a1a1a; border: 1px solid #333; border-radius: 3px; }
            QListWidget::item { padding: 3px 6px; color: #cccccc; }
            QListWidget::item:selected { background: #1a3a5c; }
        """)
        self.lst_ss_bgm.itemSelectionChanged.connect(self._ss_on_bgm_selection_changed)
        self.lst_ss_bgm.itemDoubleClicked.connect(self._ss_toggle_bgm_preview)
        lay.addWidget(self.lst_ss_bgm)

        self.lbl_ss_bgm_count = QLabel("")
        self.lbl_ss_bgm_count.setStyleSheet("color:#888; font-size:11px;")
        lay.addWidget(self.lbl_ss_bgm_count)

        # 操作按钮行：删除 + 试听/暂停
        btn_hl = QHBoxLayout()
        self.btn_ss_bgm_del = QPushButton("🗑 删除")
        self.btn_ss_bgm_del.setObjectName("DangerBtn")
        self.btn_ss_bgm_del.clicked.connect(self._ss_delete_selected_bgm)
        self.btn_ss_bgm_del.setEnabled(False)
        btn_hl.addWidget(self.btn_ss_bgm_del)

        self.btn_ss_bgm_preview = QPushButton("▶ 试听")
        self.btn_ss_bgm_preview.clicked.connect(self._ss_toggle_bgm_preview)
        self.btn_ss_bgm_preview.setEnabled(False)
        btn_hl.addWidget(self.btn_ss_bgm_preview)
        lay.addLayout(btn_hl)

        # 音量
        vl = QHBoxLayout()
        vl.addWidget(QLabel("音量:"))
        self.sld_ss_vol = QSlider(Qt.Orientation.Horizontal)
        self.sld_ss_vol.setRange(0, 100)
        self.sld_ss_vol.setValue(self._ss_cfg_get("bgm_volume", 80))
        self.sld_ss_vol.valueChanged.connect(self._ss_on_vol_change)
        vl.addWidget(self.sld_ss_vol)
        self.lbl_ss_vol = QLabel(f"{self.sld_ss_vol.value()}%")
        self.lbl_ss_vol.setStyleSheet("color:#3d8ef8; font-weight:bold;")
        vl.addWidget(self.lbl_ss_vol)
        lay.addLayout(vl)

        # 初始化 BGM 列表
        self._ss_bgm_list = list(self._ss_cfg.get("bgm_paths", []))
        self._ss_refresh_bgm_list()

        grp.setLayout(lay)
        parent_lay.addWidget(grp)

    def _ss_on_bgm_selection_changed(self):
        """BGM 列表选中变化 → 启用/禁用按钮"""
        has_selection = len(self.lst_ss_bgm.selectedItems()) > 0
        self.btn_ss_bgm_del.setEnabled(has_selection)
        self.btn_ss_bgm_preview.setEnabled(has_selection)

    def _ss_delete_selected_bgm(self):
        """删除选中的 BGM 项（支持多选）"""
        items = self.lst_ss_bgm.selectedItems()
        if not items:
            return
        rows = sorted([self.lst_ss_bgm.row(item) for item in items], reverse=True)

        # 检查是否删到当前播放的 BGM
        current_src = self._bgm_player.source()
        current_playing = current_src and current_src.toLocalFile()

        for row in rows:
            if 0 <= row < len(self._ss_bgm_list):
                removed_path = self._ss_bgm_list[row]
                if current_playing and removed_path == current_playing:
                    self._bgm_player.stop()
                    self.btn_ss_bgm_preview.setText("▶ 试听")
                    current_playing = None
                self._ss_bgm_list.pop(row)
        self._ss_refresh_bgm_list()
        self._ss_save_config()

    def _ss_toggle_bgm_preview(self):
        """试听/暂停 BGM — 双击或点击试听按钮调用"""
        items = self.lst_ss_bgm.selectedItems()
        if not items:
            return
        row = self.lst_ss_bgm.row(items[0])
        if row < 0 or row >= len(self._ss_bgm_list):
            return

        bgm_path = self._ss_bgm_list[row]
        if not os.path.exists(bgm_path):
            QMessageBox.warning(self, "提示", "音频文件不存在，可能已被移动或删除")
            return

        player = self._bgm_player
        current_src = player.source()
        current_path = current_src and current_src.toLocalFile()

        # 如果正在播放同一首 → 暂停/恢复
        if current_path == bgm_path:
            if player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                player.pause()
                self.btn_ss_bgm_preview.setText("▶ 试听")
            else:
                player.play()
                self.btn_ss_bgm_preview.setText("⏸ 暂停")
            return

        # 切换到新曲目：停止旧的，播放新的
        player.stop()
        player.setSource(QUrl.fromLocalFile(bgm_path))
        player.play()
        self.btn_ss_bgm_preview.setText("⏸ 暂停")

    def _ss_on_bgm_media_status(self, status):
        """BGM 播放完毕自动恢复按钮"""
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.btn_ss_bgm_preview.setText("▶ 试听")

    def _ss_pick_bgm(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择背景音乐", "",
            "音频文件 (*.mp3 *.wav *.aac *.flac *.ogg *.m4a);;所有文件 (*)")
        if files:
            existing = set(self._ss_bgm_list)
            for f in files:
                if f not in existing:
                    self._ss_bgm_list.append(f)
                    existing.add(f)
            self._ss_refresh_bgm_list()
            self._ss_save_config()

    def _ss_clear_bgm(self):
        self._bgm_player.stop()
        self.btn_ss_bgm_preview.setText("▶ 试听")
        self._ss_bgm_list.clear()
        self._ss_refresh_bgm_list()
        self._ss_save_config()

    def _ss_refresh_bgm_list(self):
        self._bgm_player.stop()
        self.btn_ss_bgm_preview.setText("▶ 试听")
        self.lst_ss_bgm.clear()
        n = len(self._ss_bgm_list)
        self.lbl_ss_bgm_count.setText(f"共 {n} 首" if n else "未添加")
        for path in self._ss_bgm_list:
            self.lst_ss_bgm.addItem(os.path.basename(path))

    def _ss_on_vol_change(self, val):
        self.lbl_ss_vol.setText(f"{val}%")
        self._ss_save_config()

    # ════════════════════════════════════════════════════
    #  尾页视频（对齐原代码逻辑：开关控制选项区显隐、提示卡片、移除按钮）
    # ════════════════════════════════════════════════════

    def _build_ss_endpage(self, parent_lay):
        grp = QGroupBox("📽️ 尾页视频")
        lay = QVBoxLayout()

        self.chk_ss_ep = CheckMarkBox("启用尾页拼接")
        self.chk_ss_ep.setChecked(self._ss_cfg_get("endpage_enabled", False))
        self.chk_ss_ep.stateChanged.connect(self._ss_on_ep_toggle)
        lay.addWidget(self.chk_ss_ep)

        # ★ 尾页选项区（整体显隐由开关控制）
        self._ss_ep_opts = QWidget()
        ep_lay = QVBoxLayout(self._ss_ep_opts)
        ep_lay.setContentsMargins(0, 0, 0, 0)
        ep_lay.setSpacing(4)

        # 提示卡片
        self.lbl_ss_ep_hint = QLabel("")
        self.lbl_ss_ep_hint.setWordWrap(True)
        self.lbl_ss_ep_hint.setStyleSheet(
            "color:#3d8ef8; font-size:11px; padding:6px 12px; "
            "background:#1a2a3a; border-radius:4px;")
        ep_lay.addWidget(self.lbl_ss_ep_hint)

        # 路径选择行
        hl = QHBoxLayout()
        self.le_ss_ep_path = QLineEdit(self._ss_cfg_get("endpage_path", ""))
        self.le_ss_ep_path.setPlaceholderText("选择尾页视频文件")
        self.le_ss_ep_path.textChanged.connect(self._ss_save_config)
        hl.addWidget(self.le_ss_ep_path, stretch=1)

        btn_ep = QPushButton("选择")
        btn_ep.clicked.connect(self._ss_pick_ep)
        hl.addWidget(btn_ep)
        ep_lay.addLayout(hl)

        # 文件信息 + 移除按钮
        info_row = QHBoxLayout()
        info_row.setSpacing(8)
        self.lbl_ss_ep_info = QLabel("")
        self.lbl_ss_ep_info.setStyleSheet("color:#3d8ef8; font-size:11px;")
        self.lbl_ss_ep_info.setMinimumWidth(0)
        info_row.addWidget(self.lbl_ss_ep_info, stretch=1)

        self.btn_ss_ep_remove = QPushButton("移除")
        self.btn_ss_ep_remove.setObjectName("DangerBtn")
        self.btn_ss_ep_remove.setFixedSize(48, 22)
        self.btn_ss_ep_remove.setStyleSheet(
            "QPushButton { background:transparent; color:#e06060; border:1px solid #e06060; border-radius:3px; font-size:11px; }"
            "QPushButton:hover { color:#ff6666; border-color:#ff6666; background:#2a1a1a; }")
        self.btn_ss_ep_remove.clicked.connect(self._ss_clear_ep)
        info_row.addWidget(self.btn_ss_ep_remove)
        ep_lay.addLayout(info_row)

        lay.addWidget(self._ss_ep_opts)

        # 初始状态
        ep_path = self._ss_cfg_get("endpage_path", "")
        if ep_path and os.path.exists(ep_path):
            self._ss_update_ep_info(ep_path)
        self._ss_on_ep_toggle()

        grp.setLayout(lay)
        parent_lay.addWidget(grp)

    def _ss_pick_ep(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "选择尾页视频", "",
            "视频文件 (*.mp4 *.avi *.mov *.mkv);;所有文件 (*)")
        if f:
            self.le_ss_ep_path.setText(f)
            self._ss_update_ep_info(f)
            self._ss_save_config()

    def _ss_clear_ep(self):
        self.le_ss_ep_path.setText("")
        self.lbl_ss_ep_info.setText("")
        self._ss_save_config()

    def _ss_update_ep_info(self, path):
        if path and os.path.exists(path):
            sz = os.path.getsize(path) / 1024 / 1024
            self.lbl_ss_ep_info.setText(f"{Path(path).name} ({sz:.1f}MB)")
        else:
            self.lbl_ss_ep_info.setText("")

    def _ss_on_ep_toggle(self):
        enabled = self.chk_ss_ep.isChecked()
        # ★ 开关控制选项区显隐（对齐原代码 _toggle_ep 逻辑）
        self._ss_ep_opts.setVisible(enabled)
        if enabled:
            self.lbl_ss_ep_hint.setText("✓ 已开启  ·  将在轮播结束后拼接视频")
        self._ss_save_config()
        self._ss_update_stat()

    # ════════════════════════════════════════════════════
    #  输出设置
    # ════════════════════════════════════════════════════

    def _build_ss_output(self, parent_lay):
        grp = QGroupBox("📦 输出设置")
        lay = QGridLayout()
        row = 0

        # 画质
        lay.addWidget(QLabel("画质:"), row, 0)
        self.cb_ss_quality = QComboBox()
        self.cb_ss_quality.addItems(["best", "high", "normal", "low", "minimal"])
        self.cb_ss_quality.setCurrentText(self._ss_cfg_get("video_quality", "minimal"))
        self.cb_ss_quality.currentTextChanged.connect(self._ss_save_config)
        lay.addWidget(self.cb_ss_quality, row, 1)
        row += 1

        # 输出目录
        lay.addWidget(QLabel("保存到:"), row, 0)
        hl = QHBoxLayout()
        self.le_ss_out_dir = QLineEdit(self._ss_cfg_get("output_dir", ""))
        self.le_ss_out_dir.setPlaceholderText("选择输出目录")
        self.le_ss_out_dir.textChanged.connect(self._ss_save_config)
        hl.addWidget(self.le_ss_out_dir, stretch=1)
        btn_out = QPushButton("浏览")
        btn_out.clicked.connect(self._ss_pick_out_dir)
        hl.addWidget(btn_out)
        lay.addLayout(hl, row, 1)
        row += 1

        grp.setLayout(lay)
        parent_lay.addWidget(grp)

    def _ss_pick_out_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if d:
            self.le_ss_out_dir.setText(d)
            self._ss_save_config()

    # ════════════════════════════════════════════════════
    #  素材管理
    # ════════════════════════════════════════════════════

    def _ss_add_images(self, paths=None):
        if paths is None:
            files, _ = QFileDialog.getOpenFileNames(
                self, "选择图片", "",
                "图片文件 (*.jpg *.jpeg *.png *.webp *.bmp *.tiff *.gif);;所有文件 (*)")
            paths = list(files)
        # 守卫：确保 paths 是可迭代的路径列表
        if not isinstance(paths, (list, tuple)):
            paths = [paths] if isinstance(paths, str) else []

        existing = {os.path.normpath(p) for p in self._ss_images}
        new_paths = []
        for p in paths:
            np_ = os.path.normpath(p)
            if not is_image(np_):
                continue
            if np_ in existing:
                continue
            existing.add(np_)
            new_paths.append(np_)

        if new_paths:
            self._ss_images.extend(new_paths)
            self._ss_refresh_image_list()
            self._ss_update_stat()
            self._ss_save_imagelist()
            self.lbl_ss_status.setText(f"已添加 {len(new_paths)} 张，共 {len(self._ss_images)} 张")

    def _ss_preview_image(self, item: QListWidgetItem):
        """双击图片 → 弹出居中预览窗口，支持 ← → 导航浏览"""
        path = getattr(item, 'image_path', None)
        if not path or not Path(path).exists():
            return
        cur_idx = self.ss_image_list.row(item)
        img_paths = [self.ss_image_list.item(i).image_path
                     for i in range(self.ss_image_list.count())
                     if hasattr(self.ss_image_list.item(i), 'image_path')]
        total = len(img_paths)
        cur_pos = img_paths.index(path) if path in img_paths else 0
        self._ss_show_overlay(cur_pos, img_paths, total)

    def _ss_show_overlay(self, pos: int, paths: list, total: int):
        """显示预览覆盖层，pos=当前索引，paths=所有图片路径"""
        try:
            path = paths[pos]
            pix = QPixmap(path)
            if pix.isNull():
                import cv2, numpy as np
                img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
                if img is not None:
                    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                    h, w, ch = img_rgb.shape
                    qimg = QImage(img_rgb.data, w, h, w * ch, QImage.Format.Format_RGB888)
                    pix = QPixmap.fromImage(qimg)
            if pix.isNull():
                QMessageBox.warning(self, "预览失败", f"无法加载图片:\n{path}")
                return

            screen = self.screen().availableGeometry()
            max_w, max_h = int(screen.width() * 0.88), int(screen.height() * 0.85)
            img_w, img_h = pix.width(), pix.height()
            scale = min((max_w - 24) / img_w, (max_h - 60) / img_h, 1.0)
            scaled_w = max(1, int(img_w * scale))
            scaled_h = max(1, int(img_h * scale))
            win_w, win_h = scaled_w + 24, scaled_h + 60

            dlg = QWidget(self, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
            dlg.setWindowTitle(Path(path).name)
            dlg.setFixedSize(win_w, win_h)
            dlg.setStyleSheet("background:#080808;")
            dlg.setWindowOpacity(0.97)

            root_lay = QVBoxLayout(dlg)
            root_lay.setContentsMargins(12, 10, 12, 10)
            root_lay.setSpacing(6)

            # 顶部栏
            top_bar = QHBoxLayout()
            top_bar.setSpacing(8)
            lbl_name = QLabel(Path(path).name, styleSheet="color:#aaa;font-size:12px;")
            lbl_name.setMaximumWidth(win_w - 200)
            top_bar.addWidget(lbl_name)
            top_bar.addStretch()
            lbl_pos = QLabel(f"{pos + 1} / {total}", styleSheet="color:#555;font-size:12px;")
            top_bar.addWidget(lbl_pos)
            btn_close = QPushButton("✕")
            btn_close.setFixedSize(28, 28)
            btn_close.setStyleSheet("QPushButton{background:#2a2a2a;color:#999;border:none;border-radius:4px;font-size:16px;}QPushButton:hover{background:#555;}")
            btn_close.clicked.connect(dlg.close)
            top_bar.addWidget(btn_close)
            root_lay.addLayout(top_bar)

            # 图片 + 方向箭头
            body = QHBoxLayout()
            body.setSpacing(0)

            btn_prev = QPushButton("◀")
            btn_prev.setFixedSize(36, 36)
            btn_prev.setStyleSheet("QPushButton{background:transparent;color:#555;border:none;font-size:18px;}QPushButton:hover{color:#aaa;}")
            btn_prev.setVisible(pos > 0)
            body.addWidget(btn_prev)

            lbl_img = QLabel()
            lbl_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
            scaled = pix.scaled(scaled_w, scaled_h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            lbl_img.setPixmap(scaled)
            body.addWidget(lbl_img)

            btn_next = QPushButton("▶")
            btn_next.setFixedSize(36, 36)
            btn_next.setStyleSheet("QPushButton{background:transparent;color:#555;border:none;font-size:18px;}QPushButton:hover{color:#aaa;}")
            btn_next.setVisible(pos < total - 1)
            body.addWidget(btn_next)
            root_lay.addLayout(body)

            dlg.move(screen.x() + (screen.width() - win_w) // 2,
                     screen.y() + (screen.height() - win_h) // 2)

            def go_to(new_pos: int):
                if 0 <= new_pos < total:
                    dlg.close()
                    QTimer.singleShot(50, lambda p=new_pos: self._ss_show_overlay(p, paths, total))

            btn_prev.clicked.connect(lambda: go_to(pos - 1))
            btn_next.clicked.connect(lambda: go_to(pos + 1))

            def key_handler(e):
                from PyQt6.QtGui import QKeyEvent
                if e.key() == Qt.Key.Key_Escape:
                    dlg.close()
                elif e.key() == Qt.Key.Key_Left:
                    go_to(pos - 1)
                elif e.key() == Qt.Key.Key_Right:
                    go_to(pos + 1)
            dlg.keyPressEvent = key_handler
            dlg.setFocus()

            lbl_img.mousePressEvent = lambda e: dlg.close() if e.button() == Qt.MouseButton.LeftButton else None
            lbl_img.setCursor(Qt.CursorShape.PointingHandCursor)

            dlg.show()
            dlg.raise_()
            dlg.activateWindow()

            if not hasattr(self, '_ss_preview_dlgs'):
                self._ss_preview_dlgs = []
            self._ss_preview_dlgs.append(dlg)
            self._ss_preview_dlgs = [d for d in self._ss_preview_dlgs if d.isVisible()]
        except Exception as e:
            QMessageBox.warning(self, "预览失败", str(e))

    def _ss_delete_selected(self):
        items = self.ss_image_list.selectedItems()
        if not items:
            return
        paths_to_remove = {item.image_path for item in items}
        self._ss_images = [p for p in self._ss_images if p not in paths_to_remove]
        self._ss_refresh_image_list()
        self._ss_update_stat()
        self._ss_save_imagelist()

    def _ss_clear_images(self):
        if not self._ss_images:
            return
        if QMessageBox.question(self, "确认", f"清空所有 {len(self._ss_images)} 张图片吗？") == QMessageBox.StandardButton.Yes:
            self._ss_images.clear()
            self._ss_refresh_image_list()
            self._ss_update_stat()
            self._ss_save_imagelist()

    def _ss_refresh_image_list(self):
        self.ss_image_list.clear()
        if not self._ss_images:
            self.lbl_ss_count.setText("共 0 张")
            return

        # 先加占位项 + 显示加载中
        self.lbl_ss_count.setText(f"加载缩略图… 0/{len(self._ss_images)}")
        for path in self._ss_images:
            item = ImageThumbItem(path)
            self.ss_image_list.addItem(item)

        # 后台加载缩略图
        if hasattr(self, '_ss_thumb_thread') and self._ss_thumb_thread:
            self._ss_thumb_thread.quit()
            self._ss_thumb_thread.wait(100)

        self._ss_thumb_thread = ThumbLoaderThread(self._ss_images, self)
        self._ss_thumb_thread.thumb_ready.connect(self._ss_on_thumb_ready)
        self._ss_thumb_thread.all_done.connect(self._ss_on_thumb_done)
        self._ss_thumb_thread.start()

    def _ss_on_thumb_ready(self, idx, icon, path, total, count):
        if icon and idx < self.ss_image_list.count():
            item = self.ss_image_list.item(idx)
            item.setIcon(icon)
        self.lbl_ss_count.setText(f"加载缩略图… {count}/{total}")
        # 每 10 张强制刷一下 UI
        if count % 10 == 0:
            from PyQt6.QtWidgets import QApplication
            QApplication.processEvents()

    def _ss_on_thumb_done(self):
        self.lbl_ss_count.setText(f"共 {len(self._ss_images)} 张")
        self.btn_ss_del_sel.setEnabled(False)

    def _ss_on_selection_changed(self):
        items = self.ss_image_list.selectedItems()
        self.btn_ss_del_sel.setEnabled(len(items) > 0)
        self.btn_ss_del_sel.setText(f"删除所选 ({len(items)})" if items else "删除所选")

    def _ss_update_stat(self):
        if not hasattr(self, 'lbl_ss_stat'):
            return
        n = len(self._ss_images)
        if not hasattr(self, 'sp_ss_imgs'):
            return
        imgs_per = self.sp_ss_imgs.value()
        count = self.sp_ss_count.value()
        nv = min(count, n // imgs_per if imgs_per else 0)
        if n > 0:
            self.lbl_ss_stat.setText(f"素材 {n} 张 → 可生成 {nv} 个视频")
        else:
            self.lbl_ss_stat.setText("请先添加素材")

    # ════════════════════════════════════════════════════
    #  生成控制
    # ════════════════════════════════════════════════════

    def _ss_on_gen_click(self):
        if hasattr(self, '_ss_render_thread') and self._ss_render_thread and self._ss_render_thread.isRunning():
            self._ss_stop_gen()
            return
        self._ss_render_thread = None
        self._ss_start_gen()

    def _ss_start_gen(self):
        if not self._ss_images:
            QMessageBox.warning(self, "提示", "请先添加素材图片")
            return

        ffmpeg_path = get_ffmpeg_path()
        if not ffmpeg_path or not os.path.exists(ffmpeg_path):
            QMessageBox.critical(self, "错误", "未找到 FFmpeg，无法生成视频")
            return

        cfg = self._ss_read_cfg()
        imgs_per = cfg.get("imgs_per_video", 8)
        count = cfg.get("video_count", 20)
        n = len(self._ss_images)
        available = n // imgs_per if imgs_per else 0

        if available < count:
            shuffle_on = cfg.get("shuffle_mode", True)
            hint = ("（洗牌轮巡模式下，素材将打乱后轮流分配）" if shuffle_on
                    else "（纯随机模式下，每个视频独立抽选，可能出现重复）")
            reply = QMessageBox.question(
                self, "素材不足",
                f"当前 {n} 张素材，按每视频 {imgs_per} 张\n"
                f"不重复使用最多可生成 {available} 个视频，但您要求 {count} 个。\n\n"
                f"是否允许重复使用图片？\n{hint}")
            if reply != QMessageBox.StandardButton.Yes:
                return

        self.btn_ss_gen.setText("停止生成")
        self.btn_ss_gen.setStyleSheet("background: #e74c3c; color: white; font-weight: bold; border: none; border-radius: 4px;")
        self.pb_ss.setValue(0)
        self.lbl_ss_status.setText("正在准备…")

        self._ss_render_thread = SlideshowRenderThread(
            self._ss_images, cfg, ffmpeg_path)
        self._ss_render_thread.log_signal.connect(self._ss_on_log)
        self._ss_render_thread.progress_signal.connect(self._ss_on_progress)
        self._ss_render_thread.video_done_signal.connect(self._ss_on_video_done)
        self._ss_render_thread.finished_signal.connect(self._ss_on_gen_done)
        self._ss_render_thread.start()

    def _ss_stop_gen(self):
        if hasattr(self, '_ss_render_thread') and self._ss_render_thread:
            self._ss_render_thread.stop()
        self.lbl_ss_status.setText("正在停止…")

    def _ss_on_log(self, msg):
        self.lbl_ss_status.setText(msg)

    def _ss_on_progress(self, current, total):
        self.pb_ss.setMaximum(total)
        self.pb_ss.setValue(current)

    def _ss_on_video_done(self, idx, total):
        self.lbl_ss_status.setText(f"已完成 {idx}/{total}")

    def _ss_on_gen_done(self, success, msg):
        self.btn_ss_gen.setText("生成视频")
        self.btn_ss_gen.setStyleSheet("")  # 恢复默认样式
        self.lbl_ss_status.setText(msg)
        if success:
            self.pb_ss.setValue(self.pb_ss.maximum())
            out_dir = self.le_ss_out_dir.text() or os.path.join(os.path.expanduser("~"), "Videos")
            reply = QMessageBox.question(
                self, "生成完成",
                f"{msg}\n\n打开输出目录？")
            if reply == QMessageBox.StandardButton.Yes:
                import subprocess
                if sys.platform == "win32":
                    os.startfile(out_dir)
                elif sys.platform == "darwin":
                    subprocess.run(["open", out_dir])
                else:
                    subprocess.run(["xdg-open", out_dir])
        else:
            self.pb_ss.setValue(0)

    # ════════════════════════════════════════════════════
    #  配置持久化
    # ════════════════════════════════════════════════════

    def _ss_cfg_get(self, key, default):
        return self._ss_cfg.get(key, default)

    def _ss_on_cfg_changed(self):
        self._ss_save_config()
        self._ss_update_stat()

    def _ss_on_shuffle_toggle(self):
        """素材排重模式说明"""
        if self.chk_ss_shuffle.isChecked():
            self.lbl_ss_shuffle_desc.setText(
                "洗牌轮巡：所有素材打乱后轮流分配，\n"
                "同一轮内绝不重复，视频间素材尽量错开")
        else:
            self.lbl_ss_shuffle_desc.setText(
                "纯随机：每个视频独立随机抽选，\n"
                "可能出现相邻视频素材高度雷同")
        self._ss_save_config()

    def _ss_apply_config(self):
        """确定按钮回调：保存配置并给反馈"""
        self._ss_save_config()
        self._ss_update_stat()
        self.lbl_ss_status.setText("✓ 参数已保存")
        # 2秒后恢复
        QTimer.singleShot(2000, lambda: self.lbl_ss_status.setText("就绪"))

    def _ss_read_cfg(self):
        self._ss_save_config()
        return dict(self._ss_cfg)

    def _ss_save_config(self):
        """保存当前 UI 状态到 _ss_cfg 和配置文件"""
        self._ss_cfg.update({
            "video_count": self.sp_ss_count.value() if hasattr(self, 'sp_ss_count') else 20,
            "imgs_per_video": self.sp_ss_imgs.value() if hasattr(self, 'sp_ss_imgs') else 8,
            "video_duration": self.sp_ss_dur.value() if hasattr(self, 'sp_ss_dur') else 10,
            "fps": int(self.cb_ss_fps.currentText()) if hasattr(self, 'cb_ss_fps') else 30,
            "resolution": self.cb_ss_res.currentText() if hasattr(self, 'cb_ss_res') else "1080x1920",
            "transition_frames": int(self.cb_ss_tf.currentText()) if hasattr(self, 'cb_ss_tf') else 15,
            "video_quality": self.cb_ss_quality.currentText() if hasattr(self, 'cb_ss_quality') else "minimal",
            "bgm_volume": self.sld_ss_vol.value() if hasattr(self, 'sld_ss_vol') else 80,
            "endpage_enabled": self.chk_ss_ep.isChecked() if hasattr(self, 'chk_ss_ep') else False,
            "endpage_path": self.le_ss_ep_path.text() if hasattr(self, 'le_ss_ep_path') else "",
            "transition_type": self.cb_ss_trans.currentText() if hasattr(self, 'cb_ss_trans') else "推进放大",
            "random_transition": self.chk_ss_random_trans.isChecked() if hasattr(self, 'chk_ss_random_trans') else False,
            "random_bgm": self.chk_ss_random_bgm.isChecked() if hasattr(self, 'chk_ss_random_bgm') else False,
            "shuffle_mode": self.chk_ss_shuffle.isChecked() if hasattr(self, 'chk_ss_shuffle') else True,
            "output_dir": self.le_ss_out_dir.text() if hasattr(self, 'le_ss_out_dir') else "",
            "file_prefix": self.le_ss_prefix.text() if hasattr(self, 'le_ss_prefix') else "slideshow",
        })
        if hasattr(self, '_ss_bgm_list'):
            self._ss_cfg["bgm_paths"] = list(self._ss_bgm_list)

        # 写文件
        try:
            cfg_path = Path(os.path.dirname(os.path.abspath(__file__))).parent / "ss_config.json"
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(self._ss_cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _ss_load_config(self):
        try:
            cfg_path = Path(os.path.dirname(os.path.abspath(__file__))).parent / "ss_config.json"
            if cfg_path.exists():
                with open(cfg_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self._ss_cfg.update(loaded)
        except Exception:
            pass

        # 加载图片列表
        try:
            lst_path = Path(os.path.dirname(os.path.abspath(__file__))).parent / "ss_images.json"
            if lst_path.exists():
                with open(lst_path, "r", encoding="utf-8") as f:
                    paths = json.load(f)
                self._ss_images = [os.path.normpath(p) for p in paths if os.path.exists(p)]
        except Exception:
            pass

    def _ss_save_imagelist(self):
        try:
            lst_path = Path(os.path.dirname(os.path.abspath(__file__))).parent / "ss_images.json"
            with open(lst_path, "w", encoding="utf-8") as f:
                json.dump(self._ss_images, f, ensure_ascii=False)
        except Exception:
            pass


# ════════════════════════════════════════════════════
#  后台缩略图加载线程
# ════════════════════════════════════════════════════

class ThumbLoaderThread(QThread):
    """后台生成缩略图，避免 UI 卡死"""
    thumb_ready = pyqtSignal(int, object, str, int, int)  # (index, QIcon, path, total, count)
    all_done = pyqtSignal()

    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self._paths = paths

    def run(self):
        total = len(self._paths)
        for i, path in enumerate(self._paths):
            icon = None
            try:
                import cv2, numpy as np
                img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
                if img is not None:
                    h, w = img.shape[:2]
                    scale = min(120 / w, 90 / h)
                    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
                    img_small = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
                    img_rgb = cv2.cvtColor(img_small, cv2.COLOR_BGR2RGB)
                    qimg = QImage(img_rgb.data, nw, nh, nw * 3, QImage.Format.Format_RGB888)
                    icon = QIcon(QPixmap.fromImage(qimg))
            except Exception:
                pass
            self.thumb_ready.emit(i, icon, path, total, i + 1)
        self.all_done.emit()
