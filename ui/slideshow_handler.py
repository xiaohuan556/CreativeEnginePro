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
    QSpinBox, QDoubleSpinBox, QMessageBox, QProgressBar, QSizePolicy,
    QScrollArea, QGridLayout, QFrame, QTextEdit, QListWidget,
    QListWidgetItem, QAbstractItemView, QSplitter, QStackedWidget,
    QMenu
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QUrl, QSize, QEvent, QObject
from PyQt6.QtGui import QPixmap, QImage, QColor, QIcon, QAction, QPainter, QPen
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

from core.slideshow_engine import (
    render_video, mix_audio, concat_videos,
    TRANSITIONS, TRANS_DESCS, IMG_EXTS, is_image,
    get_video_duration
)
from utils.ffmpeg_utils import get_ffmpeg_path
from core.asset_pipeline import ALLOWED_EXT
from core.image_output_size import ASPECT_OPTIONS, resolve_image_output_size
from .widgets import CheckMarkBox


# ─── AI 生成（参数预设，搬自智能成片）────────────────────────────
STYLE_PRESETS = [
    {"key": "none", "label": "✍️ 自定义（用下方描述）", "prompt": ""},
    {"key": "anime", "label": "🌸 日系动漫", "prompt": "转换成日系二次元动漫风格，清晰线稿，柔和水彩上色，明亮色调"},
    {"key": "watercolor", "label": "🎨 水彩画", "prompt": "watercolor painting style, soft edges, paper texture, gentle pastel colors"},
    {"key": "cyberpunk", "label": "🌃 赛博朋克", "prompt": "cyberpunk style, neon lights, futuristic city, high contrast, sci-fi atmosphere"},
    {"key": "oil", "label": "🖼️ 油画", "prompt": "classical oil painting style, visible brushstrokes, rich texture, museum quality"},
    {"key": "film", "label": "🎞️ 复古胶片", "prompt": "vintage film photography, soft grain, faded colors, 35mm analog look"},
    {"key": "3d", "label": "🧊 3D 渲染", "prompt": "3D render style, smooth shading, clean CGI, vibrant Pixar-like"},
    {"key": "pixel", "label": "👾 像素风", "prompt": "pixel art style, 16-bit retro game, blocky, limited palette"},
    {"key": "guochao", "label": "🐉 国潮", "prompt": "Chinese guochao style, traditional patterns with modern design, bold red and gold"},
    {"key": "sketch", "label": "✏️ 手绘线稿", "prompt": "hand-drawn pencil sketch style, clean lines, minimal shading, white background"},
]

SIZE_OPTIONS = {
    "gptimage": ["1024x1024", "1792x1024", "1024x1792"],
    "seedream": ["2K", "1K", "4K"],
}
QUALITY_MAP = {"标准": "standard", "高清": "high"}

ENGINE_LABELS = {
    "gptimage": "GPT-Image-2（OpenAI / ModelHub，本地文件直接可用）",
    "seedream": "Seedream 5.0 Pro（火山方舟，本地文件走上传）",
}

# 顶部分段按钮样式（素材库 / AI 生成）
_SEG_BTN_STYLE = (
    "QPushButton{padding:5px 16px;font-size:12px;border-radius:7px;"
    "background:#2d2d34;color:#e8e8ea;border:1px solid #3c3c44;}"
    "QPushButton:checked{background:#3d8ef8;color:#fff;border:1px solid #3d8ef8;font-weight:700;}"
    "QPushButton:hover{border-color:#3d8ef8;}"
)

_SS_LIST_STYLE = """
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
"""


def _ss_close_icon(size: int = 16) -> QIcon:
    """自绘预览关闭图标，避免全局字体缺字时显示成方块。"""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QPen(QColor("#d2d2d6"), 1.8, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap))
    inset = max(3, size // 4)
    painter.drawLine(inset, inset, size - inset, size - inset)
    painter.drawLine(size - inset, inset, inset, size - inset)
    painter.end()
    return QIcon(pix)


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
    """带图片路径与来源标记的列表项，来源显示在缩略图文字底部。"""
    def __init__(self, path, icon=None, source_label=""):
        display_name = Path(path).stem[:18] + ('…' if len(Path(path).stem) > 18 else '')
        if source_label:
            display_name += f"\n来源：{source_label}"
        if icon is not None:
            super().__init__(icon, display_name)
        else:
            super().__init__(display_name)
        self.image_path = path
        self.source_label = source_label
        # 强制固定尺寸，防止 IconMode 下无图标时塌缩堆叠
        self.setSizeHint(QSize(130, 132))
        tooltip = os.path.basename(path)
        if source_label:
            tooltip += f"\n来源：{source_label}"
        self.setToolTip(tooltip)
        self.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.setFlags(self.flags() | Qt.ItemFlag.ItemIsSelectable)


# ── 滚轮保护：QComboBox/QSpinBox 不偷走滚轮事件 ──
class _ScrollGuard(QObject):
    """阻止 QComboBox/QSpinBox 截获滚轮事件，保证滚动区正常滚动。"""
    def __init__(self, scroll_area):
        super().__init__()
        self._scroll = scroll_area

    def install_on(self, parent):
        for child in parent.findChildren(QWidget):
            if isinstance(child, (QComboBox, QSpinBox, QDoubleSpinBox)):
                child.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel:
            event.setAccepted(False)
            return True
        return super().eventFilter(watched, event)


# ─── SlideshowHandler Mixin ────────────────────────────────

class SlideshowHandler:

    def build_slideshow_module(self):
        """构建图片轮播模块 UI，返回 QWidget"""
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        # ── 顶部：分段切换 [素材库 | AI 生成] + 一键自动化 ──
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        self._seg_local = QPushButton("📁 素材库")
        self._seg_ai = QPushButton("🤖 AI 生成")
        for _b in (self._seg_local, self._seg_ai):
            _b.setCheckable(True)
            _b.setCursor(Qt.CursorShape.PointingHandCursor)
            _b.setStyleSheet(_SEG_BTN_STYLE)
        self._seg_local.setChecked(True)
        self._seg_local.clicked.connect(lambda: self._ss_switch_lib(0))
        self._seg_ai.clicked.connect(lambda: self._ss_switch_lib(1))
        top.addWidget(self._seg_local)
        top.addWidget(self._seg_ai)
        top.addStretch()
        self._ai_btn_automation = QPushButton("🤖 一键自动化")
        self._ai_btn_automation.setObjectName("PrimaryBtn")
        self._ai_btn_automation.setFixedHeight(32)
        self._ai_btn_automation.clicked.connect(self._ai_on_automation_click)
        top.addWidget(self._ai_btn_automation)
        outer.addLayout(top)

        main_lay = QHBoxLayout()
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(8)

        # 初始化数据（必须在构建 UI 之前）
        self._ss_images = []
        self._ss_image_meta = {}
        self._ss_cfg = dict(DEFAULT_CFG)
        self._ss_load_config()

        # AI 生成页状态
        self._ai_images = []
        self._ai_run_images = []
        self._ss_ai_render_images = []
        self._ai_thread = None
        self._ai_thumb_loader = None
        self._ai_source_dir = ""
        self._ai_out_dir = ""
        self._pinterest_thread = None
        self._pinterest_run_paths = []

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

        # 素材库（100% 还原最初样式：无分段切换，本地导入即唯一内容）
        page_local = QWidget()
        pl_lay = QVBoxLayout(page_local)
        pl_lay.setContentsMargins(0, 0, 0, 0)
        pl_lay.setSpacing(8)

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
        pl_lay.addLayout(tb)

        # 素材列表（图标模式）
        self.ss_image_list = QListWidget()
        self.ss_image_list.itemSelectionChanged.connect(self._ss_on_selection_changed)
        self.ss_image_list.itemDoubleClicked.connect(self._ss_preview_image)
        self.ss_image_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.ss_image_list.customContextMenuRequested.connect(self._ss_list_context_menu)
        self.ss_image_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.ss_image_list.setIconSize(QSize(120, 90))
        # Fixed 模式不会在 stacked page / 主窗口宽度变化后重新排版，
        # 会造成缩略图只挤在左半边甚至被裁切。
        self.ss_image_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.ss_image_list.setMovement(QListWidget.Movement.Static)
        self.ss_image_list.setFlow(QListWidget.Flow.LeftToRight)
        self.ss_image_list.setWrapping(True)
        self.ss_image_list.setGridSize(QSize(140, 140))
        self.ss_image_list.setSpacing(2)
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
        pl_lay.addWidget(self.ss_image_list, stretch=1)

        # 素材库堆叠：0=素材库(本地导入) 1=AI 生成（库页面保持 100% 原样）
        self._ss_lib_stack = QStackedWidget()
        self._ss_lib_stack.setFrameShape(QFrame.Shape.NoFrame)
        self._ss_lib_stack.setContentsMargins(0, 0, 0, 0)
        self._ss_lib_stack.addWidget(page_local)
        page_ai = self._build_ai_page()
        self._ss_lib_stack.addWidget(page_ai)
        right_lay.addWidget(self._ss_lib_stack, stretch=1)

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
        outer.addLayout(main_lay, stretch=1)

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

    def _ss_add_images(self, paths=None, source_label="", source_url=""):
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
            if source_label:
                self._ss_image_meta[np_] = {
                    "source": source_label,
                    "source_url": source_url,
                }
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
        if source_label:
            self._ss_save_image_meta()

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

    def _ss_close_preview(self):
        """关闭所有仍打开的图片预览覆盖层（保证同一时刻只有一个）"""
        dlgs = getattr(self, '_ss_preview_dlgs', None)
        if not dlgs:
            return
        for d in dlgs:
            try:
                if d.isVisible():
                    d.close()
            except Exception:
                pass
        self._ss_preview_dlgs = []

    def _ss_show_overlay(self, pos: int, paths: list, total: int):
        """显示预览覆盖层，pos=当前索引，paths=所有图片路径"""
        # 先关掉上一个预览，避免双击多个后层层叠加、需要逐个关闭
        self._ss_close_preview()
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
            # 小图保持原始 1:1 像素尺寸，大图才等比缩小（不拉伸、不放大）
            scale = min((max_w - 24) / img_w, (max_h - 60) / img_h, 1.0)
            if scale >= 1.0:
                scaled = pix
                scaled_w, scaled_h = img_w, img_h
            else:
                scaled_w = max(1, int(img_w * scale))
                scaled_h = max(1, int(img_h * scale))
                scaled = pix.scaled(
                    scaled_w, scaled_h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
            win_w, win_h = scaled_w + 24, scaled_h + 60
            # 多图时预留左右导航按钮(各 36px)的横向空间，避免布局被挤压导致图片错位
            if total > 1:
                win_w += 72

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
            btn_close = QPushButton()
            btn_close.setIcon(_ss_close_icon())
            btn_close.setIconSize(QSize(16, 16))
            btn_close.setToolTip("关闭预览 (Esc)")
            btn_close.setFixedSize(28, 28)
            btn_close.setStyleSheet("QPushButton{background:#2a2a2a;border:none;border-radius:4px;}QPushButton:hover{background:#4a4a4e;}")
            btn_close.clicked.connect(dlg.close)
            top_bar.addWidget(btn_close)
            root_lay.addLayout(top_bar)

            # 图片 + 方向箭头：用 QGridLayout + 列拉伸，保证图片始终水平居中，
            # 不受 prev/next 按钮（pos==0 时 prev 隐藏）影响，避免双击后图片错位
            body = QGridLayout()
            body.setContentsMargins(0, 0, 0, 0)
            body.setSpacing(0)
            body.setColumnStretch(0, 1)   # 左列（prev 所在）可拉伸
            body.setColumnStretch(1, 0)   # 中列（图片）自适应
            body.setColumnStretch(2, 1)   # 右列（next 所在）可拉伸

            btn_prev = QPushButton("◀")
            btn_prev.setFixedSize(36, 36)
            btn_prev.setStyleSheet("QPushButton{background:transparent;color:#555;border:none;font-size:18px;}QPushButton:hover{color:#aaa;}")
            btn_prev.setVisible(pos > 0)
            body.addWidget(btn_prev, 0, 0, alignment=Qt.AlignmentFlag.AlignLeft)

            lbl_img = QLabel()
            lbl_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_img.setFixedSize(scaled_w, scaled_h)
            lbl_img.setPixmap(scaled)
            body.addWidget(lbl_img, 0, 1, alignment=Qt.AlignmentFlag.AlignCenter)

            btn_next = QPushButton("▶")
            btn_next.setFixedSize(36, 36)
            btn_next.setStyleSheet("QPushButton{background:transparent;color:#555;border:none;font-size:18px;}QPushButton:hover{color:#aaa;}")
            btn_next.setVisible(pos < total - 1)
            body.addWidget(btn_next, 0, 2, alignment=Qt.AlignmentFlag.AlignRight)
            root_lay.addLayout(body)

            dlg.move(screen.x() + (screen.width() - win_w) // 2,
                     screen.y() + (screen.height() - win_h) // 2)

            def go_to(new_pos: int):
                if 0 <= new_pos < total:
                    dlg.close()
                    QTimer.singleShot(50, lambda p=new_pos: self._ss_show_overlay(p, paths, total))

            btn_prev.clicked.connect(lambda: go_to(pos - 1))
            btn_next.clicked.connect(lambda: go_to(pos + 1))

            def _on_preview_key(e):
                if e.key() == Qt.Key.Key_Escape:
                    dlg.close()
                elif e.key() == Qt.Key.Key_Left:
                    go_to(pos - 1)
                elif e.key() == Qt.Key.Key_Right:
                    go_to(pos + 1)

            def _on_preview_img_click(e):
                if e.button() == Qt.MouseButton.LeftButton:
                    dlg.close()

            dlg.keyPressEvent = _on_preview_key
            dlg.setFocus()

            lbl_img.mousePressEvent = _on_preview_img_click
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

    def _ss_list_context_menu(self, pos):
        """图片列表右键菜单：删除选中 / 在文件夹中显示"""
        item = self.ss_image_list.itemAt(pos)
        if item is None:
            return
        path = getattr(item, 'image_path', None)
        # 右键时选中该项（若未选中），便于直接删除单张
        if item not in self.ss_image_list.selectedItems():
            self.ss_image_list.setCurrentItem(item)
        sel_items = self.ss_image_list.selectedItems()

        menu = QMenu(self)
        act_open = QAction("在文件夹中显示", self)
        act_del = QAction("删除图片", self)
        menu.addAction(act_open)
        menu.addAction(act_del)

        if path and Path(path).exists():
            act_open.triggered.connect(lambda: self._ss_reveal_in_folder(path))
        else:
            act_open.setEnabled(False)
        act_del.triggered.connect(lambda: self._ss_delete_items(sel_items))
        menu.exec(self.ss_image_list.viewport().mapToGlobal(pos))

    def _ss_reveal_in_folder(self, path: str):
        """在资源管理器中定位并选中该文件"""
        p = Path(path)
        try:
            if sys.platform.startswith("win"):
                os.system(f'explorer /select,"{p}"')
            elif sys.platform == "darwin":
                os.system(f'open -R "{p}"')
            else:
                os.system(f'xdg-open "{p.parent}"')
        except Exception as e:
            QMessageBox.warning(self, "操作失败", f"无法打开文件夹:\n{e}")

    def _ss_delete_items(self, items):
        """删除指定的若干张图片（右键单张或选中的多张）"""
        if not items:
            return
        paths_to_remove = {getattr(it, 'image_path', None) for it in items}
        paths_to_remove.discard(None)
        before = len(self._ss_images)
        self._ss_images = [p for p in self._ss_images if p not in paths_to_remove]
        if len(self._ss_images) == before:
            return
        for path in paths_to_remove:
            self._ss_image_meta.pop(os.path.normpath(path), None)
        self._ss_refresh_image_list()
        self._ss_update_stat()
        self._ss_save_imagelist()
        self._ss_save_image_meta()

    def _ss_delete_selected(self):
        items = self.ss_image_list.selectedItems()
        if not items:
            return
        self._ss_delete_items(items)

    def _ss_clear_images(self):
        if not self._ss_images:
            return
        if QMessageBox.question(self, "确认", f"清空所有 {len(self._ss_images)} 张图片吗？") == QMessageBox.StandardButton.Yes:
            self._ss_images.clear()
            self._ss_image_meta.clear()
            self._ss_refresh_image_list()
            self._ss_update_stat()
            self._ss_save_imagelist()
            self._ss_save_image_meta()

    def _ss_refresh_image_list(self):
        self.ss_image_list.clear()
        if not self._ss_images:
            self.lbl_ss_count.setText("共 0 张")
            return

        # 先加占位项 + 显示加载中
        self.lbl_ss_count.setText(f"加载缩略图… 0/{len(self._ss_images)}")
        for path in self._ss_images:
            meta = self._ss_image_meta.get(os.path.normpath(path), {})
            item = ImageThumbItem(path, source_label=meta.get("source", ""))
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
        images = self._ss_active_images()
        n = len(images)
        if not hasattr(self, 'sp_ss_imgs'):
            return
        imgs_per = self.sp_ss_imgs.value()
        count = self.sp_ss_count.value()
        nv = min(count, n // imgs_per if imgs_per else 0)
        if n > 0:
            source = "AI 素材" if (hasattr(self, '_ss_lib_stack') and
                                   self._ss_lib_stack.currentIndex() == 1) else "本地素材"
            self.lbl_ss_stat.setText(f"{source} {n} 张 → 可生成 {nv} 个视频")
        else:
            self.lbl_ss_stat.setText("请先添加或生成素材")

    def _ss_active_images(self):
        """按当前素材页返回轮播输入；AI 素材始终与本地素材库隔离。"""
        if (hasattr(self, '_ss_lib_stack') and self._ss_lib_stack.currentIndex() == 1
                and hasattr(self, 'ai_image_list')):
            selected = self.ai_image_list.selectedItems()
            if selected:
                paths = [getattr(item, 'image_path', None) for item in selected]
            else:
                paths = list(getattr(self, '_ss_ai_render_images', []))
                if not paths:
                    paths = [getattr(self.ai_image_list.item(i), 'image_path', None)
                             for i in range(self.ai_image_list.count())]
            return [p for p in paths if p and os.path.exists(p)]
        return [p for p in self._ss_images if p and os.path.exists(p)]

    # ════════════════════════════════════════════════════
    #  生成控制
    # ════════════════════════════════════════════════════

    def _ss_on_gen_click(self):
        if hasattr(self, '_ss_render_thread') and self._ss_render_thread and self._ss_render_thread.isRunning():
            self._ss_stop_gen()
            return
        self._ss_render_thread = None
        self._ss_start_gen()

    def _ss_start_gen(self, skip_confirm=False, images_override=None):
        render_images = ([p for p in images_override if p and os.path.exists(p)]
                         if images_override is not None else self._ss_active_images())
        if not render_images:
            QMessageBox.warning(self, "提示", "请先添加本地素材，或在 AI 生成素材页选择图片")
            return

        ffmpeg_path = get_ffmpeg_path()
        if not ffmpeg_path or not os.path.exists(ffmpeg_path):
            QMessageBox.critical(self, "错误", "未找到 FFmpeg，无法生成视频")
            return

        cfg = self._ss_read_cfg()
        imgs_per = cfg.get("imgs_per_video", 8)
        count = cfg.get("video_count", 20)
        n = len(render_images)
        available = n // imgs_per if imgs_per else 0

        if available < count:
            if not skip_confirm:
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
            list(render_images), cfg, ffmpeg_path)
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

        # 来源信息单独保存，保持 ss_images.json 的旧列表格式向后兼容。
        try:
            meta_path = Path(os.path.dirname(os.path.abspath(__file__))).parent / "ss_image_meta.json"
            if meta_path.exists():
                with open(meta_path, "r", encoding="utf-8") as f:
                    loaded_meta = json.load(f)
                if isinstance(loaded_meta, dict):
                    self._ss_image_meta = {
                        os.path.normpath(path): meta
                        for path, meta in loaded_meta.items()
                        if isinstance(meta, dict)
                    }
        except Exception:
            self._ss_image_meta = {}

    def _ss_save_imagelist(self):
        try:
            lst_path = Path(os.path.dirname(os.path.abspath(__file__))).parent / "ss_images.json"
            with open(lst_path, "w", encoding="utf-8") as f:
                json.dump(self._ss_images, f, ensure_ascii=False)
        except Exception:
            pass

    def _ss_save_image_meta(self):
        try:
            meta_path = Path(os.path.dirname(os.path.abspath(__file__))).parent / "ss_image_meta.json"
            live = {
                os.path.normpath(path): self._ss_image_meta[os.path.normpath(path)]
                for path in self._ss_images
                if os.path.normpath(path) in self._ss_image_meta
            }
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(live, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ════════════════════════════════════════════════════
    #  图片轮播顶部分段切换
    # ════════════════════════════════════════════════════

    def _ss_switch_lib(self, idx):
        self._ss_lib_stack.setCurrentIndex(idx)
        self._seg_local.setChecked(idx == 0)
        self._seg_ai.setChecked(idx == 1)
        self._ss_update_stat()

    # ════════════════════════════════════════════════════
    #  AI 生成页
    # ════════════════════════════════════════════════════

    def _build_ai_page(self):
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # ── 控制区（直接撑满，不滚动）──
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(8)

        # Pinterest 页面 → 下载原图进素材库 → 可选自动执行当前 AI 风格预设。
        pinterest_box = QGroupBox("Pinterest 图片扒取")
        pinterest_lay = QGridLayout(pinterest_box)
        pinterest_lay.addWidget(QLabel("页面链接"), 0, 0)
        self._pinterest_url = QLineEdit()
        self._pinterest_url.setPlaceholderText(
            "粘贴 Pinterest 看板、搜索页、单 Pin 或 pin.it 链接")
        self._pinterest_url.setText(self._ss_cfg_get("pinterest_url", ""))
        pinterest_lay.addWidget(self._pinterest_url, 0, 1, 1, 3)
        pinterest_lay.addWidget(QLabel("下载数量"), 1, 0)
        self._pinterest_count = QSpinBox()
        self._pinterest_count.setRange(1, 500)
        self._pinterest_count.setValue(int(self._ss_cfg_get("pinterest_count", 30)))
        pinterest_lay.addWidget(self._pinterest_count, 1, 1)
        self._pinterest_portrait = CheckMarkBox("仅竖屏 ≥9:16")
        self._pinterest_portrait.setChecked(
            bool(self._ss_cfg_get("pinterest_portrait", True)))
        self._pinterest_portrait.setToolTip(
            "勾选后只保留高/宽 ≥ 16:9 的竖屏图片，自动过滤方形和横图")
        pinterest_lay.addWidget(self._pinterest_portrait, 1, 2)
        self._pinterest_auto_ai = CheckMarkBox("下载后按当前预设自动 AI 处理")
        self._pinterest_auto_ai.setChecked(
            bool(self._ss_cfg_get("pinterest_auto_ai", True)))
        pinterest_lay.addWidget(self._pinterest_auto_ai, 1, 3)
        self._pinterest_btn = QPushButton("扒取并下载")
        self._pinterest_btn.setObjectName("PrimaryBtn")
        self._pinterest_btn.clicked.connect(self._pinterest_on_click)
        pinterest_lay.addWidget(self._pinterest_btn, 1, 4)
        self._pinterest_progress = QProgressBar()
        self._pinterest_progress.setRange(0, self._pinterest_count.value())
        self._pinterest_progress.setValue(0)
        self._pinterest_progress.setTextVisible(True)
        pinterest_lay.addWidget(self._pinterest_progress, 2, 0, 1, 4)
        self._pinterest_status = QLabel(
            "原图进入素材库并标记来源；AI 结果保留在本页下方。")
        self._pinterest_status.setWordWrap(True)
        self._pinterest_status.setStyleSheet("color:#888888;font-size:11px;")
        pinterest_lay.addWidget(self._pinterest_status, 3, 0, 1, 4)
        lay.addWidget(pinterest_box)

        # 源文件夹
        row = QHBoxLayout()
        self._ai_src_edit = QLineEdit()
        self._ai_src_edit.setPlaceholderText("选择包含图片的本地文件夹…")
        self._ai_src_edit.textChanged.connect(self._ai_on_source_changed)
        row.addWidget(self._ai_src_edit, 1)
        b = QPushButton("选择文件夹")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(self._ai_pick_source)
        row.addWidget(b)
        lay.addLayout(row)
        self.lbl_ai_src_info = QLabel("尚未选择文件夹")
        self.lbl_ai_src_info.setStyleSheet("color:#888888;font-size:11px;")
        lay.addWidget(self.lbl_ai_src_info)

        # ── 折叠标签栏（风格预设 / 图生图 / 生成参数）──

        # Tab 按钮行
        tab_bar = QHBoxLayout()
        tab_bar.setSpacing(0)
        self._ai_tabs: list[QPushButton] = []
        self._ai_tab_stack = QStackedWidget()
        self._ai_tab_stack.setStyleSheet(
            "QStackedWidget{background:#1a1a20;border:1px solid #2a2a32;"
            "border-top:0;border-radius:0 0 8px 8px;}")

        def _make_tab(name, icon):
            idx = len(self._ai_tabs)
            btn = QPushButton(f"  {icon} {name}")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(34)
            btn.setStyleSheet(
                "QPushButton{background:#1a1a22;border:1px solid #2a2a32;"
                "border-radius:6px 6px 0 0;padding:6px 14px;color:#888;font-size:12px;"
                f"margin-right:2px;}} "
                "QPushButton:checked{background:#252530;color:#3d8ef8;"
                "border-bottom:2px solid #3d8ef8;font-weight:bold;} "
                "QPushButton:hover{color:#cfd2d8;}")
            btn.clicked.connect(
                lambda _checked, i=idx: self._ai_switch_tab(i))
            tab_bar.addWidget(btn)
            self._ai_tabs.append(btn)
            return btn

        _make_tab("风格预设", "🎨")
        _make_tab("图生图", "🖼️")
        _make_tab("生成参数", "⚙️")
        tab_bar.addStretch()
        lay.addLayout(tab_bar)

        # ── Tab 0: 风格预设 ──
        page0 = QWidget()
        p0 = QVBoxLayout(page0)
        p0.setContentsMargins(6, 4, 6, 4)
        p0.setSpacing(4)
        self._ai_style_checks = {}
        style_grid = QGridLayout()
        style_grid.setSpacing(4)
        style_items = [p for p in STYLE_PRESETS if p["key"] != "none"]
        _cols = 3
        for i, p in enumerate(style_items):
            chk = CheckMarkBox(p["label"])
            chk.stateChanged.connect(self._ai_on_style_toggled)
            self._ai_style_checks[p["key"]] = chk
            style_grid.addWidget(chk, i // _cols, i % _cols)
        p0.addLayout(style_grid)
        self._ai_custom_chk = CheckMarkBox("✍️ 自定义（用下方描述）")
        self._ai_custom_chk.stateChanged.connect(self._ai_on_style_toggled)
        p0.addWidget(self._ai_custom_chk)
        self._ai_custom_text = QTextEdit()
        self._ai_custom_text.setMaximumHeight(40)
        self._ai_custom_text.setPlaceholderText("描述想要的风格，如：赛博朋克夜景、水彩插画…")
        self._ai_custom_text.setEnabled(False)
        p0.addWidget(self._ai_custom_text)
        self._ai_tab_stack.addWidget(page0)

        # ── Tab 1: 图生图（参考图）──
        page1 = QWidget()
        p1 = QVBoxLayout(page1)
        p1.setContentsMargins(6, 4, 6, 4)
        p1.setSpacing(4)

        ref_row = QHBoxLayout()
        self._ai_ref_edit = QLineEdit()
        self._ai_ref_edit.setPlaceholderText("选择参考图 → AI 按此风格图生图")
        self._ai_ref_edit.setReadOnly(True)
        self._ai_ref_edit.setText(self._ss_cfg_get("ai_ref_image", ""))
        ref_row.addWidget(self._ai_ref_edit, 1)
        b_ref = QPushButton("浏览")
        b_ref.setCursor(Qt.CursorShape.PointingHandCursor)
        b_ref.clicked.connect(self._ai_pick_ref)
        ref_row.addWidget(b_ref)
        b_clr = QPushButton("删除")
        b_clr.setCursor(Qt.CursorShape.PointingHandCursor)
        b_clr.setToolTip("清除参考图")
        b_clr.setStyleSheet("QPushButton{color:#e05555;border:1px solid #3a2020;"
                           "border-radius:4px;padding:2px 8px;font-size:11px;}"
                           "QPushButton:hover{background:#3a2020;}")
        b_clr.clicked.connect(self._ai_clear_ref)
        ref_row.addWidget(b_clr)
        p1.addLayout(ref_row)

        # 参考图预览
        self._ai_ref_preview = QLabel()
        self._ai_ref_preview.setFixedHeight(120)
        self._ai_ref_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ai_ref_preview.setStyleSheet(
            "QLabel{background:#0f0f13;border:1px dashed #333;"
            "border-radius:6px;color:#555;font-size:11px;}")
        self._ai_ref_preview.setText("未选择参考图")
        p1.addWidget(self._ai_ref_preview)
        # 控件都已建好，现在可以安全刷新预览
        self._ai_update_ref_preview()
        self._ai_tab_stack.addWidget(page1)

        # ── Tab 2: 生成参数（去掉基础尺寸）──
        page2 = QWidget()
        p2 = QGridLayout(page2)
        p2.setContentsMargins(6, 4, 6, 4)
        p2.setSpacing(4)

        r = 0
        p2.addWidget(QLabel("引擎"), r, 0)
        self._ai_engine = QComboBox()
        for k, v in ENGINE_LABELS.items():
            self._ai_engine.addItem(v, k)
        self._ai_engine.currentIndexChanged.connect(self._ai_on_engine_changed)
        p2.addWidget(self._ai_engine, r, 1)
        r += 1

        p2.addWidget(QLabel("生成比例"), r, 0)
        self._ai_aspect = QComboBox()
        for aspect_key, aspect_label in ASPECT_OPTIONS:
            self._ai_aspect.addItem(aspect_label, aspect_key)
        p2.addWidget(self._ai_aspect, r, 1)
        r += 1

        # 基础尺寸 → 去掉 UI，保留内部变量自动跟随引擎
        self._ai_size = "1024x1024"

        p2.addWidget(QLabel("质量"), r, 0)
        self._ai_quality = QComboBox()
        self._ai_quality.addItems(list(QUALITY_MAP.keys()))
        p2.addWidget(self._ai_quality, r, 1)
        r += 1

        p2.addWidget(QLabel("强度"), r, 0)
        str_row = QHBoxLayout()
        self._ai_strength = QSpinBox()
        self._ai_strength.setRange(0, 100)
        self._ai_strength.setValue(60)
        str_row.addWidget(self._ai_strength)
        hl = QLabel("大→接近原图")
        hl.setStyleSheet("color:#888888;font-size:11px;")
        str_row.addWidget(hl)
        str_row.addStretch(1)
        p2.addLayout(str_row, r, 1)
        r += 1

        p2.addWidget(QLabel("目标生成张数"), r, 0)
        self._ai_target = QSpinBox()
        self._ai_target.setRange(1, 9999)
        self._ai_target.setValue(self._pinterest_count.value())
        self._ai_target.setToolTip("默认跟随 Pinterest 下载数量")
        p2.addWidget(self._ai_target, r, 1)
        r += 1

        self._ai_tab_stack.addWidget(page2)

        lay.addWidget(self._ai_tab_stack)
        # 默认展开风格预设
        self._ai_switch_tab(0)

        lay.addStretch()
        root.addWidget(inner, stretch=1)

        # ── 生成控制区（始终可见，不随滚动区折叠）──
        gen_box = QWidget()
        gen_box.setObjectName("GenBox")
        gen_box.setStyleSheet(
            "QWidget#GenBox{background:#15151a;border:1px solid #2a2a32;"
            "border-radius:10px;}")
        glay = QVBoxLayout(gen_box)
        glay.setContentsMargins(10, 10, 10, 10)
        glay.setSpacing(6)
        self._ai_auto = CheckMarkBox("生成后直接用 AI 素材自动出片（不加入本地素材库）")
        self._ai_auto.setChecked(True)
        glay.addWidget(self._ai_auto)
        self._ai_btn_gen = QPushButton("🚀 开始生成")
        self._ai_btn_gen.setObjectName("PrimaryBtn")
        self._ai_btn_gen.setFixedHeight(36)
        self._ai_btn_gen.clicked.connect(self._ai_on_gen_click)
        glay.addWidget(self._ai_btn_gen)
        self.pb_ai = QProgressBar()
        self.pb_ai.setValue(0)
        self.pb_ai.setStyleSheet(
            "QProgressBar{background:#161618;border:1px solid #2c2c32;border-radius:7px;"
            "height:14px;text-align:center;color:#cfd2d8;font-size:10px;}"
            "QProgressBar::chunk{background:#3d8ef8;border-radius:6px;}")
        glay.addWidget(self.pb_ai)
        self.lbl_ai_status = QLabel("")
        self.lbl_ai_status.setStyleSheet("color:#888888;font-size:11px;")
        glay.addWidget(self.lbl_ai_status)
        root.addWidget(gen_box)

        # ── AI 生成素材列表（与本地素材库独立）──
        ai_list_title = QLabel("✨ AI 生成素材")
        ai_list_title.setStyleSheet("font-weight:bold;font-size:13px;color:#3d8ef8;")
        root.addWidget(ai_list_title)
        self.ai_image_list = QListWidget()
        self.ai_image_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.ai_image_list.setIconSize(QSize(120, 90))
        self.ai_image_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.ai_image_list.setMovement(QListWidget.Movement.Static)
        self.ai_image_list.setFlow(QListWidget.Flow.LeftToRight)
        self.ai_image_list.setWrapping(True)
        self.ai_image_list.setGridSize(QSize(140, 140))
        self.ai_image_list.setSpacing(2)
        self.ai_image_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.ai_image_list.setStyleSheet(_SS_LIST_STYLE)
        self.ai_image_list.itemSelectionChanged.connect(self._ss_update_stat)
        self.ai_image_list.itemDoubleClicked.connect(self._ai_preview_image)
        root.addWidget(self.ai_image_list, stretch=1)

        # ── 操作栏 ──
        op = QHBoxLayout()
        self._ai_btn_selall = QPushButton("全选")
        self._ai_btn_selall.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ai_btn_selall.clicked.connect(self._ai_select_all)
        op.addWidget(self._ai_btn_selall)
        self._ai_btn_preview = QPushButton("查看")
        self._ai_btn_preview.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ai_btn_preview.clicked.connect(self._ai_preview_selected)
        op.addWidget(self._ai_btn_preview)
        self._ai_btn_add = QPushButton("✓ 设为本次轮播素材")
        self._ai_btn_add.setObjectName("PrimaryBtn")
        self._ai_btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ai_btn_add.clicked.connect(self._ai_add_to_slideshow)
        op.addWidget(self._ai_btn_add)
        self.lbl_ai_count = QLabel("暂存 0 张")
        self.lbl_ai_count.setStyleSheet("color:#888888;")
        op.addStretch()
        op.addWidget(self.lbl_ai_count)
        root.addLayout(op)

        # 恢复并持续保存 AI 批处理预设，便于下次直接一键运行。
        saved_styles = set(self._ss_cfg_get("ai_style_keys", []))
        for key, chk in self._ai_style_checks.items():
            chk.setChecked(key in saved_styles)
        self._ai_custom_chk.setChecked(bool(self._ss_cfg_get("ai_custom_enabled", False)))
        self._ai_custom_text.setPlainText(self._ss_cfg_get("ai_custom_prompt", ""))
        engine_idx = self._ai_engine.findData(self._ss_cfg_get("ai_engine", "gptimage"))
        if engine_idx >= 0:
            self._ai_engine.setCurrentIndex(engine_idx)
        aspect_idx = self._ai_aspect.findData(
            self._ss_cfg_get("ai_aspect", "original"))
        if aspect_idx >= 0:
            self._ai_aspect.setCurrentIndex(aspect_idx)
        self._ai_size = self._ss_cfg_get("ai_size", "1024x1024")
        quality_idx = self._ai_quality.findText(self._ss_cfg_get("ai_quality_label", "标准"))
        if quality_idx >= 0:
            self._ai_quality.setCurrentIndex(quality_idx)
        self._ai_strength.setValue(int(self._ss_cfg_get("ai_strength", 60)))
        # 旧版本固定默认 100；现在默认跟随 Pinterest 计划下载数量。
        self._ai_target.setValue(self._pinterest_count.value())
        self._ai_auto.setChecked(bool(self._ss_cfg_get("ai_auto_render", True)))
        self._ai_src_edit.setText(self._ss_cfg_get("ai_source_dir", ""))

        self._ai_src_edit.editingFinished.connect(self._ai_save_settings)
        self._ai_engine.currentIndexChanged.connect(self._ai_save_settings)
        self._ai_aspect.currentIndexChanged.connect(self._ai_save_settings)
        self._ai_aspect.currentIndexChanged.connect(self._ai_update_size_hint)
        self._ai_quality.currentIndexChanged.connect(self._ai_save_settings)
        self._ai_strength.valueChanged.connect(self._ai_save_settings)
        self._ai_target.valueChanged.connect(self._ai_save_settings)
        self._ai_auto.stateChanged.connect(self._ai_save_settings)
        self._ai_custom_chk.stateChanged.connect(self._ai_save_settings)
        self._ai_custom_text.textChanged.connect(self._ai_save_settings)
        self._pinterest_url.editingFinished.connect(self._ai_save_settings)
        self._pinterest_count.valueChanged.connect(self._ai_sync_target_to_pinterest)
        self._pinterest_count.valueChanged.connect(self._ai_save_settings)
        self._pinterest_auto_ai.stateChanged.connect(self._ai_save_settings)
        self._pinterest_portrait.stateChanged.connect(self._ai_save_settings)
        for chk in self._ai_style_checks.values():
            chk.stateChanged.connect(self._ai_save_settings)
        self._ai_update_size_hint()

        return page

    # ── AI 页交互 ──
    def _ai_on_engine_changed(self, _i):
        if not hasattr(self, "_ai_engine"):
            return
        eng = self._ai_engine.currentData()
        sizes = SIZE_OPTIONS.get(eng, ["1024x1024"])
        self._ai_size = sizes[0]
        self._ai_update_size_hint()

    def _ai_switch_tab(self, idx):
        for i, btn in enumerate(self._ai_tabs):
            btn.setChecked(i == idx)
        self._ai_tab_stack.setCurrentIndex(idx)

    def _ai_update_size_hint(self, *_args):
        # 基础尺寸不再暴露 UI，仅内部传递给图像生成。
        if not all(hasattr(self, name) for name in
                   ("_ai_engine", "_ai_aspect", "_ai_size")):
            return
        try:
            require_size_ui = False  # 无 UI 需刷新
        except RuntimeError:
            return

    def _ai_sync_target_to_pinterest(self, count):
        """下载计划数量变化时，同步本轮默认 AI 生成数量。"""
        try:
            if not (self._ai_thread and self._ai_thread.isRunning()):
                self._ai_target.setValue(max(1, int(count)))
        except RuntimeError:
            return

    def _ai_save_settings(self, *_args):
        if not hasattr(self, '_ai_target'):
            return
        try:
            values = {
                "ai_source_dir": self._ai_src_edit.text().strip(),
                "ai_style_keys": [key for key, chk in self._ai_style_checks.items()
                                  if chk.isChecked()],
                "ai_custom_enabled": self._ai_custom_chk.isChecked(),
                "ai_custom_prompt": self._ai_custom_text.toPlainText().strip(),
                "ai_engine": self._ai_engine.currentData(),
                "ai_aspect": self._ai_aspect.currentData(),
                "ai_size": self._ai_size if isinstance(self._ai_size, str)
                else getattr(self._ai_size, "currentText", lambda: "1024x1024")(),
                "ai_quality_label": self._ai_quality.currentText(),
                "ai_strength": self._ai_strength.value(),
                "ai_target": self._ai_target.value(),
                "ai_auto_render": self._ai_auto.isChecked(),
                "pinterest_url": self._pinterest_url.text().strip(),
                "pinterest_count": self._pinterest_count.value(),
                "pinterest_auto_ai": self._pinterest_auto_ai.isChecked(),
                "pinterest_portrait": self._pinterest_portrait.isChecked(),
                "ai_ref_image": self._ai_ref_edit.text().strip(),
            }
        except RuntimeError:
            # 页面关闭/重建过程中，Qt 可能先销毁控件再投递最后一个变更信号。
            return
        self._ss_cfg.update(values)
        self._ss_save_config()

    # ── Pinterest 扒取 ──
    def _pinterest_on_click(self):
        if self._pinterest_thread and self._pinterest_thread.isRunning():
            self._pinterest_thread.stop()
            self._pinterest_status.setText("正在停止 Pinterest 扒取…")
            return

        url = self._pinterest_url.text().strip()
        try:
            from core.pinterest_importer import is_pinterest_page_url
            if not is_pinterest_page_url(url):
                self._pinterest_status.setText("请输入有效的 pinterest.com 或 pin.it 页面链接")
                return
        except Exception as exc:
            self._pinterest_status.setText(f"Pinterest 导入器加载失败：{exc}")
            return

        count = self._pinterest_count.value()
        portrait = self._pinterest_portrait.isChecked()
        min_aspect = 16.0 / 9.0 if portrait else 0.0
        min_side = 240
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            import config
            root = Path(config.PROJECT_ROOT)
        except Exception:
            root = Path.cwd()
        output_dir = root / "MediaLibrary" / "Pinterest" / stamp
        try:
            from core.downloader import YOUTUBE_COOKIES_FILE
            cookie_file = YOUTUBE_COOKIES_FILE
        except Exception:
            cookie_file = ""

        self._ai_save_settings()
        self._ss_switch_lib(1)
        self._pinterest_run_paths = []
        self._pinterest_progress.setRange(0, count)
        self._pinterest_progress.setValue(0)
        self._pinterest_btn.setText("停止扒取")
        filter_note = "（仅竖屏 ≥9:16）" if portrait else ""
        self._pinterest_status.setText(
            f"准备下载 {count} 张 Pinterest 图片{filter_note}…")
        self._pinterest_thread = PinterestScrapeThread(
            url, count, str(output_dir), cookie_file,
            min_aspect=min_aspect, min_side=min_side, parent=self)
        self._pinterest_thread.progress_signal.connect(self._pinterest_on_progress)
        self._pinterest_thread.item_signal.connect(self._pinterest_on_item)
        self._pinterest_thread.finished_signal.connect(self._pinterest_on_finished)
        self._pinterest_thread.error_signal.connect(self._pinterest_on_error)
        self._pinterest_thread.start()

    def _pinterest_on_progress(self, done, total, text):
        self._pinterest_progress.setMaximum(max(1, total))
        self._pinterest_progress.setValue(min(done, total))
        self._pinterest_status.setText(text)

    def _pinterest_on_item(self, path, _image_url):
        if path and os.path.exists(path):
            np_ = os.path.normpath(path)
            self._pinterest_run_paths.append(np_)
            # 每下一张就实时显示到素材库
            source_url = self._pinterest_url.text().strip()
            self._ss_add_images([np_], source_label="Pinterest", source_url=source_url)

    def _pinterest_on_finished(self, summary):
        self._pinterest_btn.setText("扒取并下载")
        paths = [
            os.path.normpath(item.get("path", ""))
            for item in summary.get("items", [])
            if item.get("path") and os.path.exists(item.get("path"))
        ]
        if not paths:
            min_a = summary.get("filter_min_aspect", 0)
            if min_a > 0:
                self._pinterest_status.setText(
                    "没有下载到满足竖屏 ≥9:16 条件的图片，请尝试关闭竖屏过滤或更换链接")
            else:
                self._pinterest_status.setText("没有下载到可用图片")
            return

        source_url = summary.get("page_url", self._pinterest_url.text().strip())
        self._ss_add_images(paths, source_label="Pinterest", source_url=source_url)
        output_dir = summary.get("output_dir", str(Path(paths[0]).parent))
        self._ai_src_edit.setText(output_dir)
        requested = summary.get("requested", len(paths))
        stopped = summary.get("stopped", False)
        prefix = "已停止，" if stopped else ""
        self._pinterest_progress.setValue(len(paths))
        self._pinterest_status.setText(
            f"{prefix}已下载 {len(paths)}/{requested} 张并加入素材库 · 来源：Pinterest")
        self.lbl_ss_status.setText(f"Pinterest 素材已加入：{len(paths)} 张")

        if self._pinterest_auto_ai.isChecked():
            if not self._ai_selected_styles():
                self._pinterest_status.setText(
                    f"已下载 {len(paths)} 张；请选择至少一个 AI 风格后点击“开始生成”")
                return
            # 目标数等于本轮下载数；任务构造会先覆盖每一张源图。
            self._ai_target.setValue(len(paths))
            self._pinterest_status.setText(
                f"已加入素材库，正在按当前预设 AI 处理 {len(paths)} 张…")
            QTimer.singleShot(100, self._ai_on_gen_click)

    def _pinterest_on_error(self, message):
        self._pinterest_btn.setText("扒取并下载")
        self._pinterest_status.setText(f"Pinterest 扒取失败：{message}")
        self.lbl_ss_status.setText("Pinterest 扒取失败")

    def _ai_on_style_toggled(self):
        self._ai_custom_text.setEnabled(self._ai_custom_chk.isChecked())

    def _ai_pick_source(self):
        d = QFileDialog.getExistingDirectory(self, "选择图片文件夹")
        if d:
            self._ai_src_edit.setText(d)

    def _ai_pick_ref(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "选择参考图",
            "", "图片文件 (*.jpg *.jpeg *.png *.webp *.bmp);;所有文件 (*)")
        if f:
            self._ai_ref_edit.setText(f)
            self._ai_update_ref_preview()
            self._ai_save_settings()

    def _ai_clear_ref(self):
        self._ai_ref_edit.clear()
        self._ai_ref_preview.setText("未选择参考图")
        self._ai_ref_preview.setPixmap(QPixmap())
        self._ai_save_settings()

    def _ai_update_ref_preview(self):
        path = self._ai_ref_edit.text().strip()
        if path and Path(path).exists():
            pm = QPixmap(path)
            if not pm.isNull():
                pm = pm.scaledToHeight(
                    110, Qt.TransformationMode.SmoothTransformation)
                self._ai_ref_preview.setPixmap(pm)
                return
        self._ai_ref_preview.setText("未选择参考图")

    def _ai_on_source_changed(self, text):
        self._ai_source_dir = text.strip()
        if not self._ai_source_dir:
            self.lbl_ai_src_info.setText("尚未选择文件夹")
            return
        imgs = self._scan_ai_sources(self._ai_source_dir)
        self.lbl_ai_src_info.setText(f"识别到 {len(imgs)} 张图片 · 支持 png/jpg/jpeg/webp")

    def _scan_ai_sources(self, folder):
        out_dirs = {os.path.normpath(os.path.join(folder, "AI生成"))}
        ai_out = getattr(self, "_ai_out_dir", "") or ""
        if ai_out:
            out_dirs.add(os.path.normpath(ai_out))
        out = []
        for root, _d, files in os.walk(folder):
            rn = os.path.normpath(root)
            if any(rn == d or rn.startswith(d + os.sep) for d in out_dirs):
                continue
            for f in files:
                if Path(f).suffix.lower() in ALLOWED_EXT:
                    out.append(os.path.join(root, f))
        return out

    def _ai_selected_styles(self):
        styles = []
        for p in STYLE_PRESETS:
            if p["key"] == "none":
                continue
            chk = self._ai_style_checks.get(p["key"])
            if chk and chk.isChecked():
                styles.append(p)
        if self._ai_custom_chk.isChecked():
            txt = self._ai_custom_text.toPlainText().strip()
            if txt:
                styles.append({"key": "custom", "label": "自定义", "prompt": txt})
        # 无风格但有参考图 → 用默认图生图 prompt
        if not styles:
            ref = self._ai_ref_edit.text().strip()
            if ref and Path(ref).exists():
                styles.append({
                    "key": "img2img", "label": "图生图",
                    "prompt": "Apply the reference image's exact visual style, "
                              "color palette, lighting and texture to the input image. "
                              "Keep the composition and main elements but transform "
                              "the style to match the reference."
                })
        return styles

    def _ai_build_tasks(self, sources, styles, target):
        """构造严格等于 target 的任务；优先让每张源图至少处理一次。"""
        if not sources or not styles or target <= 0:
            return []
        tasks = []
        for index in range(target):
            source_index = index % len(sources)
            style_round = index // len(sources)
            src = sources[source_index]
            st = styles[style_round % len(styles)]
            rnd = index // (len(sources) * len(styles))
            tasks.append((src, st["key"], st["label"], st["prompt"], rnd, index))
        return tasks

    def _ai_on_automation_click(self):
        """一键自动化：扒取 Pinterest → AI 生图 → 自动轮播出片。"""
        url = self._pinterest_url.text().strip()
        from core.pinterest_importer import is_pinterest_page_url
        if not is_pinterest_page_url(url):
            QMessageBox.information(
                self, "一键自动化",
                "请先粘贴 Pinterest 页面链接（搜索页 / 看板 / 单 Pin）。")
            return
        self._ss_switch_lib(1)  # 切到 AI 页面看进度
        self._pinterest_auto_ai.setChecked(True)
        self._ai_auto.setChecked(True)
        self.lbl_ai_status.setText("▶ 一键自动化：扒取 → AI 生图 → 轮播…")
        self._pinterest_on_click()

    def _ai_on_gen_click(self):
        if self._ai_thread and self._ai_thread.isRunning():
            self._ai_stop_gen()
            return
        try:
            self._ai_do_gen()
        except Exception as exc:
            self.lbl_ai_status.setText(f"启动失败：{str(exc)[:200]}")
            import traceback
            traceback.print_exc()

    def _ai_do_gen(self):
        src = self._ai_source_dir
        if not src or not os.path.isdir(src):
            self.lbl_ai_status.setText("请先选择源文件夹（扒取 Pinterest 后自动填入）")
            return
        styles = self._ai_selected_styles()
        if not styles:
            self.lbl_ai_status.setText("请选择风格、填写自定义描述、或上传参考图")
            return
        target = self._ai_target.value()
        sources = self._scan_ai_sources(src)
        if not sources:
            self.lbl_ai_status.setText(f"源文件夹 {src} 没有可用图片（支持 jpg/png/webp）")
            return
        try:
            self._ai_save_settings()
            src_base = os.path.basename(src.rstrip(os.sep)) or "source"
            self._ai_out_dir = os.path.join(os.path.dirname(src), "AI生成_" + src_base)
            os.makedirs(self._ai_out_dir, exist_ok=True)

            tasks = self._ai_build_tasks(sources, styles, target)
            self.lbl_ai_status.setText(
                f"将生成 {len(tasks)} 张（{len(sources)}源 × {len(styles)}风格���")
            self._ai_run_images = []

            self._ai_btn_gen.setText("⏹ 停止生成")
            self.pb_ai.setValue(0)
            self._ai_thread = AIStyleGenThread(
                tasks, src, self._ai_out_dir,
                self._ai_engine.currentData(), self._ai_size,
                self._ai_aspect.currentData(),
                QUALITY_MAP.get(self._ai_quality.currentText(), "standard"),
                self._ai_strength.value() / 100.0,
                ref_image=self._ai_ref_edit.text().strip() or "",
                parent=self)
            self._ai_thread.progress_signal.connect(self._ai_on_progress)
            self._ai_thread.item_signal.connect(self._ai_on_item_done)
            self._ai_thread.log_signal.connect(self.lbl_ai_status.setText)
            self._ai_thread.finished_signal.connect(self._ai_on_gen_finished)
            self._ai_thread.error_signal.connect(self._ai_on_gen_error)
            self._ai_thread.start()
        except Exception as exc:
            import traceback
            self.lbl_ai_status.setText(f"启动失败：{str(exc)[:180]}")
            traceback.print_exc()

    def _ai_on_progress(self, done, total, text):
        self.pb_ai.setMaximum(total or 1)
        self.pb_ai.setValue(done)
        self.lbl_ai_status.setText(text)

    def _ai_on_item_done(self, path, status, msg):
        if status == "done" and path and os.path.exists(path):
            source_label = "AI 生成"
            source_path = ""
            if self._ai_thread is not None:
                source_path = self._ai_thread.output_sources.get(
                    os.path.normpath(path), "")
            if source_path:
                source_meta = self._ss_image_meta.get(os.path.normpath(source_path), {})
                upstream = source_meta.get("source", "")
                if upstream:
                    source_label = f"{upstream} · AI"
            idx = self.ai_image_list.count()
            self.ai_image_list.addItem(
                ImageThumbItem(path, source_label=source_label))
            self._ai_images.append(path)
            self._ai_run_images.append(path)
            self._ai_ensure_thumb_loader()
            self._ai_thumb_loader.enqueue(idx, path)
            self.lbl_ai_count.setText(f"暂存 {len(self._ai_images)} 张")
        elif msg:
            pass  # 日志已移除，单图错误仅通过 progress 反馈

    def _ai_on_gen_finished(self, summary):
        self._ai_btn_gen.setText("🚀 开始生成")
        ok = summary.get("ok", 0)
        failed = summary.get("failed", 0)
        if summary.get("stopped"):
            self.lbl_ai_status.setText(f"已停止：成功 {ok} 张，失败 {failed} 张")
            return
        self.lbl_ai_status.setText(f"生成完成：成功 {ok} 张，失败 {failed} 张")
        # 全自动闭环：直接用本轮 AI 结果出片
        if self._ai_auto.isChecked() and ok > 0:
            run_images = list(getattr(self, "_ai_run_images", []))
            self._ss_ai_render_images = run_images
            self._ss_start_gen(skip_confirm=True, images_override=run_images)

    def _ai_on_gen_error(self, msg):
        self._ai_btn_gen.setText("🚀 开始生成")
        import traceback
        self.lbl_ai_status.setText(f"生成出错：{msg}")
        traceback.print_exc()

    def _ai_stop_gen(self):
        if self._ai_thread:
            self._ai_thread.stop()
        self.lbl_ai_status.setText("正在停止…")

    def _ai_ensure_thumb_loader(self):
        if self._ai_thumb_loader is None:
            self._ai_thumb_loader = StreamThumbLoader(self)
            self._ai_thumb_loader.thumb_ready.connect(self._ai_on_thumb_ready)
        if not self._ai_thumb_loader.isRunning():
            self._ai_thumb_loader.start()

    def _ai_on_thumb_ready(self, idx, icon, path):
        if icon and 0 <= idx < self.ai_image_list.count():
            it = self.ai_image_list.item(idx)
            if it:
                it.setIcon(icon)

    def _ai_select_all(self):
        self.ai_image_list.selectAll()

    def _ai_preview_selected(self):
        item = self.ai_image_list.currentItem()
        if item is None and self.ai_image_list.count():
            item = self.ai_image_list.item(0)
        if item is None:
            self.lbl_ai_status.setText("还没有可查看的 AI 图片")
            return
        self._ai_preview_image(item)

    def _ai_preview_image(self, item: QListWidgetItem):
        """双击或点击“查看”后，使用轮播素材预览器浏览全部 AI 图片。"""
        path = getattr(item, "image_path", "")
        if not path or not os.path.exists(path):
            self.lbl_ai_status.setText("AI 图片文件不存在")
            return
        paths = [
            getattr(self.ai_image_list.item(index), "image_path", "")
            for index in range(self.ai_image_list.count())
        ]
        paths = [candidate for candidate in paths
                 if candidate and os.path.exists(candidate)]
        if not paths:
            return
        pos = paths.index(path) if path in paths else 0
        self._ss_show_overlay(pos, paths, len(paths))

    def _ai_add_to_slideshow(self, switch=True):
        sel = self.ai_image_list.selectedItems()
        if not sel:
            sel = [self.ai_image_list.item(i) for i in range(self.ai_image_list.count())]
        paths = [getattr(it, "image_path", None) for it in sel]
        paths = [p for p in paths if p and os.path.exists(p)]
        if not paths:
            return
        self._ss_ai_render_images = paths
        self.lbl_ai_status.setText(f"已设为本次轮播素材：{len(paths)} 张（仍保留在 AI 生成素材页）")
        self._ss_update_stat()


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


# ─── AI 图生图处理器 ───────────────────────────────────────────────
class AIStylePlugin:
    """把一张图交给 AI 图生图引擎换风格。"""

    name = "ai_style"

    def run(self, arr, ctx):
        from ai import TaskRequest
        from ai.service import get_ai_manager

        engine = ctx.get("engine", "gptimage")
        prompt = (ctx.get("prompt") or "").strip()
        size = resolve_image_output_size(
            engine,
            ctx.get("size", "1024x1024"),
            ctx.get("aspect", "original"),
        )
        quality = ctx.get("quality", "standard")
        strength = float(ctx.get("strength", 0.6))
        ref_image = (ctx.get("ref_image") or "").strip()

        if not prompt:
            raise RuntimeError("未填写风格描述 / 预设")

        # 设有参考图时，追加风格迁移指令到 prompt
        if ref_image:
            ref_stem = Path(ref_image).stem
            prompt = (
                f"Apply the exact visual style, color palette, lighting, texture, "
                f"and aesthetic of the reference image to the input image. "
                f"The output should look like the reference's artistic style "
                f"was transferred onto the input scene. {prompt}"
            )

        mgr = get_ai_manager()
        provider = mgr.registry.get(engine)
        if provider is None:
            raise RuntimeError(f"未找到引擎「{engine}」，请检查对应 API Key 是否配置")

        import uuid
        from pathlib import Path
        from PIL import Image
        import numpy as np
        import tempfile

        tmpin = Path(tempfile.gettempdir()) / f"ai_style_in_{uuid.uuid4().hex[:8]}.png"
        Image.fromarray(arr).save(tmpin)
        try:
            inputs = {"image": str(tmpin), "prompt": prompt}
            # 参考图同时作为额外图片传入（Seedream Ark 支持 images 数组）
            if ref_image and Path(ref_image).exists():
                inputs["images"] = [str(tmpin), ref_image]
            req = TaskRequest(
                operation="image_edit",
                inputs=inputs,
                params={"size": size, "quality": quality, "n": 1, "strength": strength},
            )
            handle = provider.execute(req)
            if not handle.is_success or handle.result is None:
                err = handle.result.error if handle.result else "生成失败"
                raise RuntimeError(err)
            out = handle.result.data
            if isinstance(out, (list, tuple)):
                out = out[0]
            out = Path(out)
            if not out.exists():
                raise RuntimeError("生成结果文件不存在")
            return np.array(Image.open(out).convert("RGBA"))
        finally:
            try:
                tmpin.unlink()
            except Exception:
                pass


def _make_thumb_icon(path):
    """生成 120x90 缩略图 QIcon（与 ThumbLoaderThread 同逻辑）。"""
    try:
        import cv2
        import numpy as np
        img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is not None:
            h, w = img.shape[:2]
            scale = min(120 / w, 90 / h)
            nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
            img_small = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
            img_rgb = cv2.cvtColor(img_small, cv2.COLOR_BGR2RGB)
            qimg = QImage(img_rgb.data, nw, nh, nw * 3, QImage.Format.Format_RGB888)
            return QIcon(QPixmap.fromImage(qimg))
    except Exception:
        pass
    return None


class StreamThumbLoader(QThread):
    """增量缩略图加载：AI 生成是流式出图的，逐张入队加载，避免整批重载。"""
    thumb_ready = pyqtSignal(int, object, str)  # (idx, QIcon, path)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._q = []
        self._lock = threading.Lock()
        self._stop_flag = False

    def enqueue(self, idx, path):
        with self._lock:
            self._q.append((idx, path))

    def stop(self):
        self._stop_flag = True

    def run(self):
        while not self._stop_flag:
            with self._lock:
                item = self._q.pop(0) if self._q else None
            if item is None:
                time.sleep(0.05)
                continue
            idx, path = item
            icon = _make_thumb_icon(path)
            self.thumb_ready.emit(idx, icon, path)


class PinterestScrapeThread(QThread):
    """在后台解析和下载 Pinterest 图片，避免阻塞轮播界面。"""
    progress_signal = pyqtSignal(int, int, str)
    item_signal = pyqtSignal(str, str)       # (local_path, image_url)
    finished_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)

    def __init__(self, url, count, output_dir, cookie_file="",
                 min_aspect=0.0, min_side=200, parent=None):
        super().__init__(parent)
        self._url = url
        self._count = count
        self._output_dir = output_dir
        self._cookie_file = cookie_file
        self._min_aspect = min_aspect
        self._min_side = min_side
        self._stop_ev = threading.Event()

    def stop(self):
        self._stop_ev.set()

    def run(self):
        try:
            from core.pinterest_importer import PinterestImporter
            importer = PinterestImporter(self._cookie_file)
            summary = importer.import_page(
                self._url,
                self._count,
                self._output_dir,
                stop_event=self._stop_ev,
                progress=lambda done, total, text: self.progress_signal.emit(
                    done, total, text),
                item_ready=lambda path, url: self.item_signal.emit(path, url),
                min_aspect=self._min_aspect,
                min_side=self._min_side,
            )
            self.finished_signal.emit(summary)
        except Exception as exc:
            self.error_signal.emit(str(exc)[:500])


class AIStyleGenThread(QThread):
    """逐张执行精确数量的图生图任务，并流式上报结果。"""
    progress_signal = pyqtSignal(int, int, str)   # (done, total, status_text)
    item_signal = pyqtSignal(str, str, str)       # (path, status, msg)
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(dict)            # summary
    error_signal = pyqtSignal(str)

    def __init__(
            self, tasks, source_dir, out_dir, engine, size, aspect,
            quality, strength, ref_image="", parent=None):
        super().__init__(parent)
        self._tasks = tasks
        self._source_dir = source_dir
        self._out_dir = out_dir
        self._engine = engine
        self._size = size
        self._aspect = aspect
        self._quality = quality
        self._strength = strength
        self._ref_image = ref_image
        self._stop_ev = threading.Event()
        self.output_sources = {}

    def stop(self):
        self._stop_ev.set()

    def run(self):
        try:
            from PIL import Image
            import numpy as np

            total_ok = 0
            total_failed = 0
            total = len(self._tasks)
            plugin = AIStylePlugin()
            Path(self._out_dir).mkdir(parents=True, exist_ok=True)

            for done, (source, st_key, st_label, st_prompt, rnd, task_index) in enumerate(
                    self._tasks, start=1):
                if self._stop_ev.is_set():
                    break
                self.progress_signal.emit(done - 1, total, f"{st_label} {done}/{total}")
                ctx = {
                    "engine": self._engine,
                    "prompt": st_prompt,
                    "size": self._size,
                    "aspect": self._aspect,
                    "quality": self._quality,
                    "strength": self._strength,
                    "ref_image": self._ref_image,
                }
                try:
                    arr = np.array(Image.open(source).convert("RGBA"))
                    result = plugin.run(arr, ctx)
                    output = Path(self._out_dir) / (
                        f"{Path(source).stem}_{st_key}_r{rnd + 1}_{task_index + 1:04d}.png")
                    Image.fromarray(result).save(output)
                    total_ok += 1
                    self.output_sources[os.path.normpath(str(output))] = os.path.normpath(source)
                    self.item_signal.emit(str(output), "done", "")
                except Exception as exc:  # 单张失败不终止整批
                    total_failed += 1
                    self.item_signal.emit("", "failed", f"{Path(source).name}: {str(exc)[:240]}")
                self.progress_signal.emit(done, total, f"已完成 {done}/{total}")
            self.finished_signal.emit({
                "ok": total_ok,
                "failed": total_failed,
                "stopped": self._stop_ev.is_set(),
            })
        except Exception as e:  # noqa: BLE001
            self.error_signal.emit(str(e)[:500])
