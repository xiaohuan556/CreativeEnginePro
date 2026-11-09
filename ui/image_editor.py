# -*- coding: utf-8 -*-
"""图片编辑器（图层版）—— 一个轻量 PS：图层 / 选区 / 形状 / 文字 / 笔刷 / 导出 / 工程。
P0+P1 MVP：数据模型 + 画布渲染 + 图层 + 选区(魔棒/矩形/套索) + 选区删除/填充
          + 形状 + 文字样式 + 笔刷/橡皮 + 导出PNG + 保存工程 + 撤销重做。
P2：模糊(整层/选区) + 羽化 + 剪切蒙版(clip) + 渐变/圆角/描边/阴影 + 合并图层 + AI 抠图(rembg)。
复用 core/edit_engine 的 pre-state 快照思路做撤销。
"""
import os
import sys
import copy
import math
import time
import pickle
from enum import IntEnum
from pathlib import Path

import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QToolBar, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QFileDialog, QColorDialog, QSlider,
    QComboBox, QLineEdit, QCheckBox, QDialog, QFormLayout, QSpinBox,
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QApplication,
    QSizePolicy, QFrame, QAbstractItemView, QMessageBox, QPlainTextEdit,
    QSplitter, QMenu, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QTabBar, QInputDialog, QStyle, QDialogButtonBox, QScrollArea, QToolButton,
)
from PyQt6.QtGui import (
    QImage, QPixmap, QPainter, QColor, QFont, QPen, QBrush, QTransform,
    QCursor, QMouseEvent, QWheelEvent, QPolygonF, QPainterPath, QDesktopServices,
    QLinearGradient, QFontMetrics, QIcon, QAction, QTextLayout, QTextOption,
    QDrag,
)
from PyQt6.QtCore import (
    Qt, QRectF, QPointF, QSize, QTimer, pyqtSignal, QUrl, QPoint, QThread, QEvent,
    QMimeData,
)
from PyQt6 import sip

ACCENT = "#3d8ef8"
AUX = "#00eaff"
from ui.image_workbench_ops import WorkbenchOperations, ADJUSTMENTS, apply_extra_adjustment
from ui.duo_delivery import offer_duo_export
from ui.image_workbench_widgets import CanvasNavigator, CommandSearchInput, command_match_score, CurveEditor, ScrubbableLabel
from core.image_effects import apply_curves, mix_rgba, blur_rgba
from ui.image_folders import ImageFolderOperations, walk_layers
from ui.image_file_ops import ImageFileOperations
from ui.image_commands import ImageCommandsOperations
from ui.image_painting import ImagePaintingOperations
from ui.image_adjustment_panel import AdjustmentPropertyPanel
from ui.image_style_dialog import edit_layer_effects
from core.image_layer_effects import apply_layer_effects
from ui.image_layout_ops import ImageLayoutOperations
from ui.image_selection_ops import ImageSelectionOperations, combine_selection_masks, selection_history
from ui.image_text_panel import TextPropertyPanel,render_text,text_font
from ui.image_text_interaction import TextInteractionOperations
from ui.image_interaction_rendering import ImageInteractionRenderingOperations
from core.image_history import ImageHistoryOperations
from core.project_files import PIC_FILTER, PIC_SUFFIX, read_project
from ui.image_recovery import ImageRecoveryOperations
from ui.image_chrome import (ColorSwatchButton, EditorColorCard,
                             ZoomControls, EditorToolButton, AdaptiveToolRail)
from ui.widgets.workbench_controls import (
    IMAGE_ACCENT, workbench_button, workbench_field_style, workbench_horizontal_strip, workbench_icon,
    workbench_slider_style, workbench_tabs_style,
)
from ui.image_color_dialog import choose_color
from ui.image_layer_marquee import LayerMarqueeOperations


# ───────────────────────────── 工具枚举 ─────────────────────────────
class Tool(IntEnum):
    MOVE = 0
    SELECT_RECT = 1
    SELECT_ELLIPSE = 2
    SELECT_LASSO = 3
    WAND = 4
    BRUSH = 5
    ERASER = 6
    TEXT = 7
    SHAPE_RECT = 8
    SHAPE_ELLIPSE = 9
    EYEDROPPER = 10
    CROP = 11
    POLY_LASSO = 12      # P4 多边形套索
    QUICK_SELECT = 13    # P5 快速选择（笔刷式智能选区）
    GRADIENT = 14        # 渐变工具（拖拽生成线性/径向渐变填充层）
    CLONE = 15           # 克隆图章（Alt 取源点，拖拽复制纹理）
    HEAL = 16            # 修复画笔（克隆 + 目标亮度/纹理混合）
    OBJECT_SELECT = 17
    SHAPE_LINE = 18


class AIInpaintPromptDialog(QDialog):
    """图片选区右键使用的轻量 Prompt 输入弹窗。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI 局部修改")
        self.setModal(True)
        self.setMinimumWidth(500)
        self.setMinimumHeight(290)
        self.setStyleSheet("""
            QDialog { background:#181a1f; color:#eef2f8; }
            QLabel#aiTitle { color:#ffffff; font-size:18px; font-weight:700; }
            QLabel#aiHint { color:#929baa; font-size:12px; }
            QLabel#aiTip {
                background:#282318; color:#e8c982; border:1px solid #4a3d22;
                border-radius:7px; padding:8px 10px; font-size:11px;
            }
            QLabel#aiCount { color:#697384; font-size:11px; }
            QPlainTextEdit {
                background:#111318; color:#f2f5fa; border:1px solid #343a45;
                border-radius:9px; padding:11px; font-size:13px;
                selection-background-color:#397fda;
            }
            QPlainTextEdit:focus { border:1px solid #4b97ff; }
            QPushButton {
                min-height:34px; border-radius:7px; padding:0 18px;
                font-size:12px; font-weight:600;
            }
            QPushButton#cancelButton {
                background:#242830; color:#aeb6c2; border:1px solid #363c47;
            }
            QPushButton#cancelButton:hover { background:#2c313b; color:#ffffff; }
            QPushButton#polishButton {
                background:#222b3a; color:#7db5ff; border:1px solid #334f73;
                min-height:28px; padding:0 12px; font-size:11px;
            }
            QPushButton#polishButton:hover {
                background:#293a52; color:#a9d0ff; border-color:#4b78ad;
            }
            QPushButton#generateButton {
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #2878e8, stop:1 #4899ff);
                color:#ffffff; border:1px solid #5ba5ff;
            }
            QPushButton#generateButton:hover {
                background:#56a4ff; border-color:#7bb9ff;
            }
            QPushButton#generateButton:disabled {
                background:#273246; color:#647087; border-color:#303b4d;
            }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(9)

        title = QLabel("✨  AI 局部修改")
        title.setObjectName("aiTitle")
        root.addWidget(title)
        hint = QLabel("描述框选区域要变成什么，选区外的画面将保持不变")
        hint.setObjectName("aiHint")
        root.addWidget(hint)

        tip = QLabel("💡 温馨提示：框选时请尽量覆盖完整的待修改对象，并在周围留少量空间，生成效果会更自然。")
        tip.setObjectName("aiTip")
        tip.setWordWrap(True)
        root.addWidget(tip)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(
            "例如：把选区内的帽子改成红色针织帽，保持人物姿态和光照不变")
        self.editor.setMinimumHeight(125)
        root.addWidget(self.editor, 1)

        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        self.polish_button = QPushButton("✨ AI 润色")
        self.polish_button.setObjectName("polishButton")
        self.polish_button.setEnabled(False)
        self.polish_button.clicked.connect(self._polish_prompt)
        meta_row.addWidget(self.polish_button)
        meta_row.addStretch(1)
        self.count_label = QLabel("0 / 500")
        self.count_label.setObjectName("aiCount")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        meta_row.addWidget(self.count_label)
        root.addLayout(meta_row)

        buttons = QDialogButtonBox()
        cancel = buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        self.generate = buttons.addButton(
            "开始修改", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel.setObjectName("cancelButton")
        self.generate.setObjectName("generateButton")
        self.generate.setEnabled(False)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)

        self.editor.textChanged.connect(self._on_text_changed)
        self._polish_handle = None
        self._polish_timer = QTimer(self)
        self._polish_timer.setInterval(150)
        self._polish_timer.timeout.connect(self._poll_polish)
        QTimer.singleShot(0, self.editor.setFocus)

    def _on_text_changed(self):
        text = self.editor.toPlainText()
        if len(text) > 500:
            cursor = self.editor.textCursor()
            pos = min(cursor.position(), 500)
            self.editor.setPlainText(text[:500])
            cursor = self.editor.textCursor()
            cursor.setPosition(pos)
            self.editor.setTextCursor(cursor)
            text = text[:500]
        self.count_label.setText(f"{len(text)} / 500")
        busy = self._polish_handle is not None and not self._polish_handle.is_finished
        self.generate.setEnabled(bool(text.strip()) and not busy)
        self.polish_button.setEnabled(bool(text.strip()) and not busy)

    def _polish_prompt(self):
        source = self.editor.toPlainText().strip()
        if not source:
            return
        try:
            from ai import TaskRequest, ProviderDomain
            from ai.service import get_ai_manager

            manager = get_ai_manager()
            providers = manager.registry.by_domain(ProviderDomain.LLM)
            provider = next((item for item in providers if item.name == "openai"),
                            providers[0] if providers else None)
            if provider is None:
                QMessageBox.information(
                    self, "AI 润色",
                    "未检测到文本模型。请在 .env 配置 LLM_API_KEY 或 OPENAI_API_KEY 后重启。")
                return
            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是专业的 AI 图片局部重绘提示词优化助手。"
                        "在不改变用户意图的前提下，把输入改写成清晰、具体、可执行的中文提示词。"
                        "说明对象、材质、颜色、细节、光照和融合要求；强调保持原图构图、"
                        "主体位置、比例、透视及选区外内容不变。只输出润色后的提示词，"
                        "不要解释、不要标题、不要引号，控制在 300 字以内。"
                    ),
                },
                {"role": "user", "content": source},
            ]
            params = {}
            try:
                from config import LLM_MODEL_NAME
                if LLM_MODEL_NAME:
                    params["model"] = LLM_MODEL_NAME
            except Exception:
                pass
            request = TaskRequest(operation="chat", inputs={"messages": messages},
                                  params=params)
            self._polish_handle = manager.submit(provider.name, request)
            self.polish_button.setText("润色中…")
            self.polish_button.setEnabled(False)
            self.generate.setEnabled(False)
            self._polish_timer.start()
        except Exception as exc:
            self._finish_polish_error(str(exc))

    def _poll_polish(self):
        handle = self._polish_handle
        if handle is None or not handle.is_finished:
            return
        self._polish_timer.stop()
        if handle.is_success and handle.result:
            polished = str(handle.result.data or "").strip().strip('"').strip("'")
            if polished:
                self.editor.setPlainText(polished[:500])
                cursor = self.editor.textCursor()
                cursor.movePosition(cursor.MoveOperation.End)
                self.editor.setTextCursor(cursor)
        else:
            error = handle.result.error if handle.result else "未知错误"
            QMessageBox.warning(self, "AI 润色失败", str(error))
        self._polish_handle = None
        self.polish_button.setText("✨ AI 润色")
        self._on_text_changed()
        self.editor.setFocus()

    def _finish_polish_error(self, error):
        self._polish_timer.stop()
        self._polish_handle = None
        self.polish_button.setText("✨ AI 润色")
        self._on_text_changed()
        QMessageBox.warning(self, "AI 润色失败", error)

    def prompt(self) -> str:
        return self.editor.toPlainText().strip()


# ───────────────────────────── 数据模型 ─────────────────────────────
class ImageLayer:
    _counter = 0

    def __init__(self, name, pixels=None, w=1, h=1, kind="image"):
        ImageLayer._counter += 1
        self.id = ImageLayer._counter
        self.name = name
        self.kind = kind  # image | text | shape
        self.pixels = pixels  # (H,W,4) uint8, 仅 image 用
        self.w = w
        self.h = h
        self.opacity = 1.0
        self.visible = True
        self.blend = "normal"
        self.x = 0.0  # 中心在画布中的坐标
        self.y = 0.0
        self.scale = 1.0
        self.stretch_x = self.stretch_y = 1.
        self.flip_x = self.flip_y = False
        self.mask_linked = True
        self.quad = []
        self.rotation = 0.0
        self.clip_to = None
        # text
        self.text = ""
        self.font_family = "Microsoft YaHei"
        self.font_size = 48
        self.letter_spacing = 0
        self.line_spacing = 100
        self.text_box_explicit = False
        self.bold = False
        self.italic = False
        self.color = "#ffffff"
        self.align = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.stroke_color = "#000000"  # 文本描边色
        # shape
        self.shape = "rect"
        self.line_start,self.line_end = (0.,0.),(1.,1.)
        self.line_style = int(Qt.PenStyle.SolidLine.value)
        self.line_cap = int(Qt.PenCapStyle.SquareCap.value)
        self.rect = QRectF(0, 0, 100, 100)
        self.fill_color = "#3d8ef8"
        self.stroke_w = 0
        self.stroke_on = False      # 描边开启/关闭（绘制矩形等时控制）
        self.filled = True
        self.radius = 0          # 圆角半径(矩形)
        self.gradient = False     # 渐变填充
        self.grad_from = "#3d8ef8"
        self.grad_to = "#00eaff"
        self.grad_angle = 0       # 渐变角度（度，文字渐变用）
        self.clip = False         # 剪切蒙版：贴紧下一层(下方)的 alpha
        self.locked = False       # 锁定图层：不可选中/移动/编辑
        # 阴影(矢量层 shape/text)
        self.shadow = False
        self.shadow_dx = 4
        self.shadow_dy = 4
        self.shadow_blur = 8
        self.shadow_color = "#000000"
        self.shadow_opacity = 0.5
        # P3 图层蒙版：numpy uint8 (h,w)，None 表示无蒙版；255=完全不透明，0=完全透明
        self.mask = None
        # P3 增强图层样式
        self.inner_shadow = False
        self.inner_shadow_dx = 0
        self.inner_shadow_dy = 0
        self.inner_shadow_blur = 4
        self.inner_shadow_color = "#000000"
        self.inner_shadow_opacity = 0.4
        self.outer_glow = False
        self.outer_glow_size = 8
        self.outer_glow_color = "#00eaff"
        self.outer_glow_opacity = 0.5
        self.bevel_emboss = False
        self.bevel_size = 3
        self.bevel_highlight = "#ffffff"
        self.bevel_shadow = "#000000"
        self.bevel_depth = 0.5
        # P3 自由变换（扭曲/透视）
        self.skew_x = 0.0
        self.skew_y = 0.0
        self.perspective_x = 0.0
        self.perspective_y = 0.0
        # P4 图层编组（同 group_id 的层联动选中/移动）
        self.group_id = None
        # P4 智能对象：保留合并前的原始图层序列化数据，可随时还原编辑
        self.smart = False
        self.smart_source = None
        # P4 调整图层：kind="adjust" 时生效，影响其下所有图层
        # {"type": "brightness_contrast"|"hsl", "brightness":0,"contrast":0,"hue":0,"saturation":0,"lightness":0}
        self.adjust = None
        self.children = []
        self.collapsed = True
        self.frames = []
        self.frame_durations = []
        self.frame_index = 0
        self.svg_source = ''
        self.effects = {}

    @property
    def size(self):
        return (self.w, self.h)

    def canvas_transform(self):
        t = QTransform()
        t.translate(self.x, self.y)
        t.rotate(self.rotation)
        t.scale(self.scale*self.stretch_x*(-1 if self.flip_x else 1), self.scale*self.stretch_y*(-1 if self.flip_y else 1))
        # P3 自由变换：斜切 + 透视（可选）
        if self.skew_x != 0 or self.skew_y != 0:
            t.shear(self.skew_x, self.skew_y)
        if self.perspective_x != 0 or self.perspective_y != 0:
            # 简易透视：用 projection 矩阵近似
            w2, h2 = self.w / 2.0, self.h / 2.0
            q = QTransform(1, self.perspective_y, 0, self.perspective_x, 1, 0, 0, 0, 1)
            t = q * t
        t.translate(-self.w / 2.0, -self.h / 2.0)
        if self.quad:
            import cv2
            source = np.float32([[0,0],[self.w,0],[self.w,self.h],[0,self.h]])
            target = np.float32([[x*self.w,y*self.h] for x,y in self.quad])
            matrix = cv2.getPerspectiveTransform(source,target)
            warp = QTransform(matrix[0,0],matrix[1,0],matrix[2,0],matrix[0,1],matrix[1,1],matrix[2,1],matrix[0,2],matrix[1,2],matrix[2,2])
            t = warp*t
        return t

    def canvas_to_layer(self, cx, cy):
        inv, ok = self.canvas_transform().inverted()
        if not ok:
            return None
        p = inv.map(QPointF(cx, cy))
        return p.x(), p.y()


class Artboard:
    """Photoshop 风格画板：文档中的一块命名矩形区域，自带尺寸/位置/背景，
    内部承载一组图层（坐标相对画板左上角）。图层被画板裁剪（导出即所见）。"""
    _counter = 0

    def __init__(self, name, x, y, w, h):
        Artboard._counter += 1
        self.id = Artboard._counter
        self.name = name
        self.x = x          # 左上角在文档坐标系中的位置
        self.y = y
        self.w = w
        self.h = h
        self.bg_color = QColor(255, 255, 255)
        self.transparent = False   # 画板默认白底（与 PS 一致）
        self.layers = []           # 底 -> 顶，坐标相对画板左上角
        self.collapsed = False


class ImageProject:
    def __init__(self, w=1080, h=1080):
        self.w = w
        self.h = h
        self.bg_color = QColor(255, 255, 255)
        self.transparent = True
        self.layers = []  # 底 -> 顶
        self.artboards = []  # 画板列表（空 = 传统单画布模式）
        self.h_guides = []   # P3 水平参考线（文档坐标 y 值）
        self.v_guides = []   # P3 垂直参考线（文档坐标 x 值）
        self.show_grid = False
        self.grid_subdivisions = 1
        self.snap_targets = dict(grid=True,guides=True,layers=True,canvas=True)
        self.grid_size = 60  # px

    def add_layer(self, layer, top=True):
        if top:
            self.layers.append(layer)
        else:
            self.layers.insert(0, layer)
        return layer


# ───────────────────────────── numpy <-> QImage ─────────────────────────────
def qimage_from_numpy(arr):
    """把 numpy 数组安全转为 QImage（强制连续 / uint8 / 4 通道）。

    健壮性说明：BiRefNet 等 AI 结果或异常裁剪可能产生非连续、非 uint8 或通道
    数不符的数组；直接 QImage(arr.data, …) 会因缓冲解释错误而崩溃（含
    "wrapped C/C++ object of type QImage has been deleted" 这类诡异报错）。
    这里统一规整，确保渲染路径永不拿到非法缓冲。"""
    import numpy as np
    arr = np.ascontiguousarray(arr)
    if arr.dtype != np.uint8:
        try:
            arr = np.clip(arr, 0, 255).astype(np.uint8)
        except Exception:
            arr = np.zeros((max(1, arr.shape[0]), max(1, arr.shape[1]), 4), np.uint8)
    if arr.ndim == 2:
        arr = np.dstack([arr, arr, arr, np.full(arr.shape[:2], 255, np.uint8)])
    elif arr.ndim == 3 and arr.shape[2] == 1:
        a = arr[:, :, 0]
        arr = np.dstack([a, a, a, np.full(arr.shape[:2], 255, np.uint8)])
    elif arr.ndim == 3 and arr.shape[2] == 3:
        arr = np.dstack([arr, np.full(arr.shape[:2], 255, np.uint8)])
    elif arr.ndim != 3 or arr.shape[2] != 4:
        arr = np.zeros((max(1, arr.shape[0] if arr.ndim >= 2 else 1),
                        max(1, arr.shape[1] if arr.ndim >= 2 else 1), 4), np.uint8)
    h, w = arr.shape[:2]
    img = QImage(arr.data, w, h, w * 4, QImage.Format.Format_RGBA8888)
    return img.copy()


def numpy_from_qimage(img):
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    buf = img.constBits()
    arr = np.frombuffer(buf.asarray(w * h * 4), dtype=np.uint8).reshape(h, w, 4).copy()
    return arr


def _extract_selected_rgba(image_rgba, selection, soft_selection=None):
    """按像素选区提取 RGBA；选区外强制透明，并裁到选区包围盒。"""
    pixels = np.asarray(image_rgba, dtype=np.uint8)
    mask = np.asarray(selection, dtype=bool)
    if pixels.ndim != 3 or pixels.shape[2] != 4 or mask.ndim != 2:
        return None, None
    h = min(pixels.shape[0], mask.shape[0])
    w = min(pixels.shape[1], mask.shape[1])
    if h <= 0 or w <= 0:
        return None, None

    if soft_selection is not None:
        soft = np.asarray(soft_selection, dtype=np.float32)
        if soft.ndim != 2:
            return None, None
        h = min(h, soft.shape[0])
        w = min(w, soft.shape[1])
        weights = soft[:h, :w]
        weights = np.clip(weights, 0.0, 1.0)
        selected = weights > 0.001
    else:
        selected = mask[:h, :w]
        weights = selected.astype(np.float32)
    ys, xs = np.where(selected)
    if len(xs) == 0:
        return None, None

    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    out = np.ascontiguousarray(pixels[y0:y1, x0:x1]).copy()
    local_weights = weights[y0:y1, x0:x1]
    out[:, :, 3] = np.clip(
        out[:, :, 3].astype(np.float32) * local_weights, 0, 255
    ).astype(np.uint8)
    out[out[:, :, 3] == 0, :3] = 0
    return out, (x0, y0)


def _trim_transparent_rgba(image_rgba):
    """去掉 RGBA 图四周完全透明的空白，返回内容及相对偏移。"""
    pixels = np.asarray(image_rgba, dtype=np.uint8)
    if pixels.ndim != 3 or pixels.shape[2] != 4 or pixels.size == 0:
        return None, None
    ys, xs = np.where(pixels[:, :, 3] > 0)
    if len(xs) == 0:
        return None, None
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    return np.ascontiguousarray(pixels[y0:y1, x0:x1]), (x0, y0)


# ───────────────────────────── 画布视图 ─────────────────────────────
class CanvasView(QGraphicsView):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setBackgroundBrush(QColor("#161616"))
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._set_canvas_cursor(Qt.CursorShape.CrossCursor)
        self.composite_item = QGraphicsPixmapItem()
        self.scene.addItem(self.composite_item)
        self.sel_item = QGraphicsPixmapItem()
        self.scene.addItem(self.sel_item)
        self.shape_preview_item = QGraphicsPixmapItem()
        self.scene.addItem(self.shape_preview_item)
        self.handle_item = QGraphicsPixmapItem()
        self.handle_item.setZValue(10)
        self.scene.addItem(self.handle_item)
        self.guide_item = QGraphicsPixmapItem()
        self.guide_item.setZValue(5)  # 在选区之上、手柄之下
        self.scene.addItem(self.guide_item)
        # 画板边框 / 名称标签叠加层（不进入导出）
        self.artboard_item = QGraphicsPixmapItem()
        self.artboard_item.setZValue(5)
        self.scene.addItem(self.artboard_item)
        # Canvas gestures use our layer hit testing; never scan a full-size
        # pixmap's alpha channel to construct QGraphicsItem collision masks.
        for item in (self.composite_item, self.sel_item, self.shape_preview_item,
                     self.handle_item, self.guide_item, self.artboard_item):
            item.setShapeMode(QGraphicsPixmapItem.ShapeMode.BoundingRectShape)
        self.setAcceptDrops(True)
        # QGraphicsView 的实际接收面是 viewport；部分平台不会把 view 的设置自动下传。
        self.viewport().setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # 文字编辑态下需要由本控件直接接收中文输入法事件（IME），
        # 故显式开启 WA_InputMethodEnabled（默认自定义 QWidget 为 False）。
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        # 右键上下文菜单：有像素选区时给出选区操作（参考 PS：右键蚂蚁线）
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        # 变换手柄
        self._handle_drag = None       # 手柄名 或 None
        self._handle_orig = {}         # 拖拽起始状态
        self._handle_pre_snap = None   # 手柄拖拽前的 pre-state 快照（供撤销精确回退）
        # 画板拖拽（画板模式下，MOVE 工具点击画板空白背景拖动整块画板）
        self._artboard_drag = None     # 正在拖拽的画板 Artboard 或 None
        self._artboard_drag_start = (0, 0)
        self._artboard_drag_orig = (0, 0)
        # 交互态
        self._dragging = False
        self._start = QPointF()
        self._last = QPointF()
        self._lasso_pts = []
        self._pending_shape_rect = None
        self._pan = False
        self._pan_start = QPoint()
        self._guide_drag = None        # 'h'/'v'：正在从标尺拖出参考线
        self._guide_move = None        # ('h'/'v', idx)：正在拖动已有参考线
        self._brush_hover_pos = None   # 视口坐标；绘制笔刷/橡皮擦实际作用范围
        self.navigator = CanvasNavigator(self)

    RULER_W = 20  # 标尺宽度(px, 视口坐标)
    PAN_MARGIN = 8000  # 场景四周留白(px)：保证可平移到画布外空白，不受 sceneRect 限制

    # —— 坐标 ——
    def canvas_pos(self, event):
        return self.mapToScene(int(event.position().x()), int(event.position().y()))

    def viewportEvent(self, event):
        if event.type() in (QEvent.Type.TabletPress, QEvent.Type.TabletMove, QEvent.Type.TabletRelease):
            self.tabletEvent(event)
            return True
        return super().viewportEvent(event)

    def tabletEvent(self, event):
        if self.editor.tool not in (Tool.BRUSH, Tool.ERASER):
            event.ignore()
            return
        event_types = {QEvent.Type.TabletPress: QEvent.Type.MouseButtonPress,
                       QEvent.Type.TabletMove: QEvent.Type.MouseMove,
                       QEvent.Type.TabletRelease: QEvent.Type.MouseButtonRelease}
        if event.type() != QEvent.Type.TabletRelease:
            self.editor._tablet_pressure = max(.01, min(1., event.pressure()))
        button = Qt.MouseButton.NoButton if event.type() == QEvent.Type.TabletMove else Qt.MouseButton.LeftButton
        buttons = Qt.MouseButton.NoButton if event.type() == QEvent.Type.TabletRelease else Qt.MouseButton.LeftButton
        mouse = QMouseEvent(event_types[event.type()], event.position(), event.globalPosition(), button, buttons, event.modifiers())
        if event.type() == QEvent.Type.TabletPress:
            self.mousePressEvent(mouse)
        elif event.type() == QEvent.Type.TabletMove:
            self.mouseMoveEvent(mouse)
        else:
            self.mouseReleaseEvent(mouse)
            self.editor._tablet_pressure = 1.
        event.accept()

    # —— 标尺（PS 风格，绘制在视口顶部/左侧，Ctrl+R 切换）——
    def paintEvent(self, e):
        super().paintEvent(e)
        self.editor._paint_layer_handles()
        self.editor.paint_text_frame()
        self.editor.paint_layer_selection()
        if getattr(self.editor,'show_pixel_grid',True) and abs(self.transform().m11())>=8:
            rect = self.mapToScene(self.viewport().rect()).boundingRect().intersected(self.composite_item.sceneBoundingRect())
            if not rect.isEmpty():
                painter = QPainter(self.viewport())
                painter.setPen(QPen(QColor(255,255,255,40),1))
                for x in range(math.floor(rect.left()),math.ceil(rect.right())+1):
                    painter.drawLine(self.mapFromScene(QPointF(x,rect.top())),self.mapFromScene(QPointF(x,rect.bottom())))
                for y in range(math.floor(rect.top()),math.ceil(rect.bottom())+1):
                    painter.drawLine(self.mapFromScene(QPointF(rect.left(),y)),self.mapFromScene(QPointF(rect.right(),y)))
                painter.end()
        # P5 智能参考线（拖动图层时显示的洋红对齐线，先于标尺绘制）
        try:
            self._paint_smart_guides()
        except Exception:
            pass
        if getattr(self.editor, "show_rulers", False):
            try:
                self._paint_rulers()
            except Exception:
                pass
        # 吸管取色预览 HUD（编辑区「小标志」）
        try:
            self._paint_eyedrop_hud()
        except Exception:
            pass
        try:
            self._paint_brush_cursor()
        except Exception:
            pass

    def _set_canvas_cursor(self, cursor):
        """Keep the view and its real mouse surface on the same tool cursor."""
        self.setCursor(cursor)
        self.viewport().setCursor(cursor)

    def _paint_brush_cursor(self):
        """Draw an accurate brush footprint that follows zoom and layer scale."""
        if (self.editor.tool not in (Tool.BRUSH, Tool.ERASER) or
                self._brush_hover_pos is None):
            return
        center = QPointF(self._brush_hover_pos)
        radius = self.editor._brush_cursor_view_radius()
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0, 220), 3.0))
        painter.drawEllipse(center, radius, radius)
        color = (QColor("#ffb35c") if self.editor.tool == Tool.ERASER
                 else QColor(self.editor.fg))
        pen = QPen(color, 1.5)
        if self.editor.tool == Tool.ERASER:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawEllipse(center, radius, radius)
        if self.editor.tool == Tool.BRUSH:
            painter.setPen(QPen(QColor("#ffffff"), 1.0))
            painter.setBrush(color)
            painter.drawEllipse(center, 2.5, 2.5)
        else:
            painter.save()
            painter.translate(center)
            painter.rotate(-35)
            painter.setPen(QPen(QColor("#ffffff"), 1.0))
            painter.setBrush(QColor("#ffb35c"))
            painter.drawRoundedRect(QRectF(-5, -3, 10, 6), 1.5, 1.5)
            painter.restore()
        painter.end()

    def _paint_eyedrop_hud(self):
        """吸管工具悬停时，在光标旁画一个跟随的取色小圆（编辑区「小标志」）。

        圆内填充当前鼠标下像素颜色；若处在透明/画布外则画灰底 + 红斜杠提示。
        """
        prev = getattr(self.editor, "_eyedrop_preview", None)
        if prev is None:
            return
        vx, vy, color = prev
        vp = self.viewport()
        p = QPainter(vp)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = 9
        cx, cy = vx + 16, vy + 16
        if color is not None:
            p.setBrush(QBrush(color))
        else:
            p.setBrush(QBrush(QColor(60, 60, 66)))
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.drawEllipse(QPointF(cx, cy), r, r)
        if color is None:
            p.setPen(QPen(QColor("#ff5555"), 1.5))
            p.drawLine(int(cx - r), int(cy - r), int(cx + r), int(cy + r))
        p.end()

    def leaveEvent(self, e):
        # 鼠标离开画布：清除吸管取色 HUD，避免「小标志」残留
        prev = getattr(self.editor, "_eyedrop_preview", None)
        if prev is not None:
            self.editor._eyedrop_preview = None
            self.viewport().update()
        if self._brush_hover_pos is not None:
            self._brush_hover_pos = None
            self.viewport().update()
        super().leaveEvent(e)

    def _paint_smart_guides(self):
        guides = getattr(self.editor, "_smart_guides", None)
        if not guides:
            return
        vp = self.viewport()
        w, h = vp.width(), vp.height()
        R = self.RULER_W
        p = QPainter(vp)
        pen = QPen(QColor(255, 0, 255, 230), 1)
        p.setPen(pen)
        for kind, val in guides:
            if kind == "v":
                vx = self.mapFromScene(QPointF(val, 0)).x()
                p.drawLine(int(vx), R, int(vx), h)
            else:
                vy = self.mapFromScene(QPointF(0, val)).y()
                p.drawLine(R, int(vy), w, int(vy))
        p.end()

    def _nice_step(self, zoom):
        """选择合适的刻度间隔，使屏幕上主刻度间距 >= 55px。"""
        for s in (5, 10, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000):
            if s * zoom >= 55:
                return s
        return 20000

    def _paint_rulers(self):
        R = self.RULER_W
        vp = self.viewport()
        w, h = vp.width(), vp.height()
        p = QPainter(vp)
        p.fillRect(0, 0, w, R, QColor("#232323"))
        p.fillRect(0, 0, R, h, QColor("#232323"))
        p.setPen(QPen(QColor("#3a3a3a"), 1))
        p.drawLine(0, R, w, R)
        p.drawLine(R, 0, R, h)
        zoom = max(1e-6, self.transform().m11())
        step = self._nice_step(zoom)
        minor = max(1, step // 5)
        f = QFont("Arial", 7)
        p.setFont(f)
        # 水平标尺
        x0 = self.mapToScene(R, 0).x()
        x1 = self.mapToScene(w, 0).x()
        start = int(x0 // minor) * minor
        for xd in range(start, int(x1) + minor, minor):
            vx = self.mapFromScene(QPointF(xd, 0)).x()
            if vx < R:
                continue
            if xd % step == 0:
                p.setPen(QPen(QColor("#888"), 1))
                p.drawLine(int(vx), 4, int(vx), R)
                p.setPen(QColor("#9a9a9a"))
                p.drawText(int(vx) + 2, 10, str(xd))
            else:
                p.setPen(QPen(QColor("#555"), 1))
                p.drawLine(int(vx), R - 5, int(vx), R)
        # 垂直标尺
        y0 = self.mapToScene(0, R).y()
        y1 = self.mapToScene(0, h).y()
        start = int(y0 // minor) * minor
        for yd in range(start, int(y1) + minor, minor):
            vy = self.mapFromScene(QPointF(0, yd)).y()
            if vy < R:
                continue
            if yd % step == 0:
                p.setPen(QPen(QColor("#888"), 1))
                p.drawLine(4, int(vy), R, int(vy))
                p.setPen(QColor("#9a9a9a"))
                p.save()
                p.translate(9, int(vy) + 2)
                p.rotate(90)
                p.drawText(0, 0, str(yd))
                p.restore()
            else:
                p.setPen(QPen(QColor("#555"), 1))
                p.drawLine(R - 5, int(vy), R, int(vy))
        # 左上角小方块
        p.fillRect(0, 0, R, R, QColor("#2a2a2a"))
        # 拖出参考线的预览（青色虚线）
        gp = getattr(self.editor, "_guide_preview", None)
        if gp is not None:
            pen = QPen(QColor(0, 234, 255, 220), 1, Qt.PenStyle.DashLine)
            p.setPen(pen)
            if gp[0] == "h":
                vy = self.mapFromScene(QPointF(0, gp[1])).y()
                p.drawLine(R, int(vy), w, int(vy))
            else:
                vx = self.mapFromScene(QPointF(gp[1], 0)).x()
                p.drawLine(int(vx), R, int(vx), h)
        p.end()

    def _update_zoom_label(self):
        lbl = getattr(self.editor, "zoom_label", None)
        if lbl is not None:
            lbl.setText("%d%%" % int(self.transform().m11() * 100 + 0.5))
        self.navigator.refresh()

    def wheelEvent(self, e: QWheelEvent):
        # 以视口中心为锚缩放：画布始终朝屏幕中心对称缩放，缩小也不会跑出视野
        delta = e.angleDelta().y()
        if delta:
            self.zoom_by(1.15 ** max(-8., min(8., delta / 120.)))
        elif e.pixelDelta().y():
            self.zoom_by(1.0015 ** max(-600, min(600, e.pixelDelta().y())))
        e.accept()

    def zoom_by(self, factor):
        """以视图中心缩放（供按钮 / 快捷键调用）。"""
        current = max(abs(self.transform().m11()), 1e-9)
        target = max(.01, min(32., current * factor))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.scale(target / current, target / current)
        self._update_zoom_label()

    def fit_view(self):
        # 画板模式：用 _doc_bounds() 包围盒，非画板模式用项目尺寸
        if self.editor.project.artboards:
            w, h = self.editor._doc_bounds()
        else:
            w, h = self.editor.project.w, self.editor.project.h
        if not w or not h:
            return
        rect = QRectF(0, 0, w, h)
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.scale(0.92, 0.92)
        self._update_zoom_label()

    # —— 鼠标 ——
    def mousePressEvent(self, e: QMouseEvent):
        # 空格抓手模式：交给 QGraphicsView 的 ScrollHandDrag 原生平移
        if self.editor._space_held:
            self._pan_last = e.position()
            return
        # 右键交给上下文菜单（选区/画布操作），不进入绘制逻辑
        if e.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(e)
            return
        # P5 标尺：从标尺按下 → 拖出参考线
        if getattr(self.editor, "show_rulers", False):
            px, py = e.position().x(), e.position().y()
            if py < self.RULER_W and px >= self.RULER_W:
                self._guide_pre_snap = self.editor._snapshot()
                self._guide_drag = "h"
                return
            if px < self.RULER_W and py >= self.RULER_W:
                self._guide_pre_snap = self.editor._snapshot()
                self._guide_drag = "v"
                return
        if self.editor.begin_text_pointer(e):
            return
        self._dragging = True
        self._start = self.canvas_pos(e)
        # P5 抓取已有参考线拖动（在标尺可见且靠近参考线时）
        if getattr(self.editor, "show_rulers", False):
            gi = self.editor._hit_guide(
                self._start, tol=6.0 / max(0.2, self.transform().m11()))
            if gi is not None:
                self._guide_pre_snap = self.editor._snapshot()
                kind, idx = gi
                values = (self.editor.project.h_guides if kind == "h"
                          else self.editor.project.v_guides)
                self._guide_move_orig = values[idx] if 0 <= idx < len(values) else None
                self._guide_move = gi
                return
        # 文字编辑态：点击非文字层空白区域 → 提交并退出编辑框
        if self.editor._text_editing and e.button() == Qt.MouseButton.LeftButton:
            ly = self.editor._text_edit_layer
            if ly is not None:
                bbox = self.editor._layer_bbox(ly)
                if bbox is None or not bbox.contains(self._start):
                    self.editor._end_text_edit(save=True)
        if self.editor.tool in (Tool.MOVE,Tool.CROP,Tool.SELECT_RECT,Tool.SELECT_ELLIPSE,Tool.SHAPE_RECT,Tool.SHAPE_ELLIPSE,Tool.SHAPE_LINE):
            self._start = self.editor._snap_pt(self._start)
        self._last = self._start
        tool = self.editor.tool
        if tool in (Tool.BRUSH, Tool.ERASER):
            self._brush_hover_pos = QPointF(e.position())
            self._set_canvas_cursor(self.editor._brush_cursor(painting=True))
            self.viewport().update()
        mods = e.modifiers()
        # A polygon is one gesture: retain the mode chosen at its first vertex.
        if tool != Tool.POLY_LASSO or not getattr(self, '_poly_pts', None):
            self.editor._sel_combine = self.editor.selection_mode_for_modifiers(mods)

        # 变换手柄优先
        if tool == Tool.MOVE:
            handle = self.editor._hit_handle(self._start)
            if handle is not None:
                self._handle_drag = handle
                self._handle_pre_snap = self.editor._snapshot(metadata_only=True)
                self._handle_orig = {
                    "x": self.editor.active.x, "y": self.editor.active.y,
                    "scale": self.editor.active.scale,
                    "rotation": self.editor.active.rotation,
                    'quad':copy.deepcopy(self.editor.active.quad),
                    "bbox": self.editor._layer_bbox(self.editor.active),
                    "start": QPointF(self._start),
                }
                return
        if tool == Tool.MOVE:
            # 点空处：清像素选区 + 清图层选择 + 隐藏手柄
            hit = self.editor._hit_test_at(self._start)
            mods = e.modifiers()
            if hit is None:
                # Empty-board drags select layers. Alt retains whole-board movement.
                if self.editor.active_artboard is not None:
                    ab = self.editor._artboard_at(self._start)
                    if ab is not None:
                        # 点中任意画板：先激活（若不是当前），再直接拖拽该画板
                        if ab is not self.editor.active_artboard:
                            self.editor._set_active_artboard(ab)
                        if mods & Qt.KeyboardModifier.AltModifier:
                            self.editor._push_undo("移动画板")
                            self._artboard_drag = ab
                            self.editor._invalidate_doc_bounds()
                            self._artboard_drag_start = (self._start.x(), self._start.y())
                            self._artboard_drag_orig = (ab.x, ab.y)
                            self._dragging = True
                            return
                self.editor._begin_layer_marquee(e.position(), mods)
                return
            else:
                if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
                    self.editor._toggle_layer_select(hit)
                    self.editor._show_handles = (len(self.editor.selected) == 1)
                    self.editor._refresh_layers()
                    self.editor._draw_handles()
                    self._dragging = False
                elif mods & Qt.KeyboardModifier.ShiftModifier:
                    if hit not in self.editor.selected:
                        self.editor.selected.append(hit)
                    self.editor.active = hit
                    self.editor._show_handles = (len(self.editor.selected) == 1)
                    self.editor._refresh_layers()
                    self.editor._draw_handles()
                    self._dragging = False
                else:                                   # 普通点击
                    if hit not in self.editor.selected:
                        self.editor._select_layer_only(hit)
                    self.editor._show_handles = (len(self.editor.selected) == 1)
                    self.editor._begin_move(self._start)   # 支持整组拖动
        elif tool in (Tool.SELECT_RECT, Tool.SELECT_ELLIPSE):
            # 冻结已提交选区作为多选预览底图（避免拖拽中反复回读 sel_item 累积残影）
            self.editor._stop_sel_anim()
            self._sel_preview_base = self.sel_item.pixmap() \
                if (not self.sel_item.pixmap().isNull()) else None
        elif tool == Tool.SELECT_LASSO:
            self.editor._stop_sel_anim()
            self._sel_preview_base = self.sel_item.pixmap() \
                if (not self.sel_item.pixmap().isNull()) else None
            self._lasso_pts = [self._start]
        elif tool == Tool.POLY_LASSO:
            # 多边形套索：首次点击初始化顶点列表，后续点击追加
            if not hasattr(self, '_poly_pts') or self._poly_pts is None:
                self._poly_pts = [self._start]
                self.editor._stop_sel_anim()
                self._sel_preview_base = self.sel_item.pixmap() \
                    if (not self.sel_item.pixmap().isNull()) else None
            else:
                self._poly_pts.append(self._start)
                self.editor._preview_lasso(self._poly_pts)
            self._dragging = False
        elif tool == Tool.WAND:
            self.editor._wand(self._start, self.editor.tolerance,
                              self.editor._sel_combine)
            self._dragging = False
        elif tool == Tool.OBJECT_SELECT:
            self.editor.object_select(self._start)
            self._dragging = False
        elif tool == Tool.QUICK_SELECT:
            self.editor._quick_select_begin(mode=self.editor._sel_combine)
            self.editor._quick_select_point(self._start)
        elif tool in (Tool.BRUSH, Tool.ERASER):
                self.editor._brush_last_point = (getattr(self.editor, '_brush_anchor', None)
                                                 if mods & Qt.KeyboardModifier.ShiftModifier else None)
                self.editor._push_undo("橡皮擦除" if tool == Tool.ERASER else "画笔绘制")
                self.editor._stroke_point(self._start, e)
        elif tool in (Tool.CLONE, Tool.HEAL):
            if e.modifiers() & Qt.KeyboardModifier.AltModifier:
                self.editor._set_clone_source(self._start)
                self._dragging = False
                self._set_canvas_cursor(self.editor._clone_cursor(alt=True))
            else:
                self.editor._push_undo("克隆图章" if tool == Tool.CLONE else "修复画笔")
                self.editor.begin_clone_stroke(self._start)
                self.editor._clone_painting = True
                self._set_canvas_cursor(self.editor._clone_cursor(painting=True))
                self.editor._stroke_clone(self._start, e)
        elif tool == Tool.EYEDROPPER:
            self.editor._pick_color(self._start)
            self._dragging = False
        elif tool == Tool.TEXT:
            # 文字工具：单击只选中已有文字层 / 点空白清除选择，不再弹编辑框；
            # 真正的文字框在「双击」时弹出（避免一点画布就建一堆空文字框）
            if self.editor._text_editing:
                self.editor._end_text_edit(save=True)
            hit = self.editor._hit_test_at(self._start, kinds=("text",))
            if hit is not None:
                self.editor.set_active(hit)
            else:
                self.editor._clear_layer_selection()
                self.editor._refresh_layers()
            self._dragging = False
        elif tool in (Tool.SHAPE_RECT, Tool.SHAPE_ELLIPSE, Tool.SHAPE_LINE):
            self._pending_shape_rect = QRectF(self._start, self._start)
        elif tool == Tool.CROP:
            pass  # 记录起始点，拖拽时绘制裁剪预览
        elif tool == Tool.GRADIENT:
            # 渐变：从起点拖到终点定义方向与范围；清残留预览并进入拖拽态
            self.editor._clear_selection()
            self.sel_item.setPixmap(QPixmap())
            self._dragging = True
        # 进入文字编辑态：焦点交给画布本身即可（IME 事件由 CanvasView 转发给
        # ImageEditorWidget，不再依赖子控件覆盖层，从根源上避免抢焦点问题）
        if self.editor._text_editing:
            self.setFocus()
        else:
            self.setFocus()
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent):
        if self.editor.update_text_pointer(e):
            return
        if self.editor._update_layer_marquee(e.position()):
            return
        # 空格抓手模式：手动移动滚动条（不受 sceneRect 限制，画布边角自由拖）
        if self.editor._space_held:
            cur = e.position()
            if hasattr(self, "_pan_last") and self._pan_last is not None:
                dx = int(self._pan_last.x() - cur.x())
                dy = int(self._pan_last.y() - cur.y())
                self._pan_last = cur
                self.horizontalScrollBar().setValue(
                    self.horizontalScrollBar().value() + dx)
                self.verticalScrollBar().setValue(
                    self.verticalScrollBar().value() + dy)
            return
        if self.editor.tool in (Tool.BRUSH, Tool.ERASER):
            self._brush_hover_pos = QPointF(e.position())
            self.viewport().update()
        elif self._brush_hover_pos is not None:
            self._brush_hover_pos = None
            self.viewport().update()
        # P5 标尺参考线拖拽预览
        if self._guide_drag is not None:
            sp = self.canvas_pos(e)
            self.editor._guide_preview = (
                ("h", sp.y()) if self._guide_drag == "h" else ("v", sp.x()))
            self.viewport().update()
            return
        # P5 拖动已有参考线
        if getattr(self, "_guide_move", None) is not None:
            sp = self.canvas_pos(e)
            kind, idx = self._guide_move
            if kind == 'h':
                if 0 <= idx < len(self.editor.project.h_guides):
                    self.editor.project.h_guides[idx] = sp.y()
            else:
                if 0 <= idx < len(self.editor.project.v_guides):
                    self.editor.project.v_guides[idx] = sp.x()
            self.editor._draw_guides_and_grid()
            self.viewport().update()
            return
        # 悬停时更新光标（非拖拽状态下）
        if not self._dragging and self._handle_drag is None:
            text_cursor = self.editor.text_pointer_cursor(e.position())
            if text_cursor is not None:
                self._set_canvas_cursor(text_cursor)
                return
            p = self.canvas_pos(e)
            handle = self.editor._hit_handle(p)
            # 靠近参考线 → 抓取光标
            if getattr(self.editor, "show_rulers", False) and handle is None:
                gi = self.editor._hit_guide(
                    p, tol=6.0 / max(0.2, self.transform().m11()))
                if gi is not None:
                    self._set_canvas_cursor(
                        Qt.CursorShape.SplitVCursor if gi[0] == 'h'
                        else Qt.CursorShape.SplitHCursor)
                    return
            if self.editor.tool in (Tool.CLONE, Tool.HEAL):
                # 克隆图章/修复画笔：悬停保持图章光标（Alt 状态实时跟手）
                cursor = self.editor._clone_cursor(
                    alt=bool(e.modifiers() & Qt.KeyboardModifier.AltModifier))
            elif self.editor.tool == Tool.EYEDROPPER:
                # 吸管：保持专属光标 + 实时取色预览 HUD（编辑区「小标志」）
                cursor = self.editor._eyedropper_cursor()
                vx, vy = e.position().x(), e.position().y()
                self.editor._update_eyedrop_preview(int(p.x()), int(p.y()), vx, vy)
            elif self.editor.tool == Tool.GRADIENT:
                # 渐变：保持线段光标，提示「拖拽画渐变线段」
                cursor = self.editor._gradient_cursor()
            elif self.editor.tool in (Tool.BRUSH, Tool.ERASER):
                cursor = self.editor._brush_cursor()
            elif self.editor.tool == Tool.MOVE and handle is not None:
                cursor = {
                    "nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
                    "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
                    "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
                    "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor,
                    "rotate": Qt.CursorShape.CrossCursor,
                }[handle]
            else:
                # 其他工具把手统一普通箭头
                cursor = Qt.CursorShape.ArrowCursor
            self._set_canvas_cursor(cursor)
            return
        if not self._dragging:
            return
        p = self.canvas_pos(e)
        if self.editor.tool in (Tool.MOVE,Tool.CROP,Tool.SELECT_RECT,Tool.SELECT_ELLIPSE,Tool.SHAPE_RECT,Tool.SHAPE_ELLIPSE,Tool.SHAPE_LINE):
            p = self.editor._snap_pt(p)
        tool = self.editor.tool

        # 手柄拖拽
        if self._handle_drag is not None:
            self.editor._handle_move(self._handle_drag, p, self._handle_orig)
            self._last = p
            return
        # 画板拖拽
        if self._artboard_drag is not None:
            dx = p.x() - self._artboard_drag_start[0]
            dy = p.y() - self._artboard_drag_start[1]
            ab = self._artboard_drag
            # 夹紧到 >= 0：画板不会被拖出文档左上角（固定平移下不会出现错位/裁切）
            ab.x = max(0, self._artboard_drag_orig[0] + dx)
            ab.y = max(0, self._artboard_drag_orig[1] + dy)
            # 关键：拖拽中实时使文档包围盒缓存失效，让画布（场景矩形）随画板扩大，
            # 否则场景矩形停留在拖拽前的尺寸，画板往下/右挪会被那块「深色背景」裁切
            # （即用户说的「约束框限制往下显示」）。
            self.editor._invalidate_doc_bounds()
            self.editor._redraw()
            self._last = p
            return
        if tool == Tool.MOVE:
            self.editor._move_to(p)
        elif tool in (Tool.SELECT_RECT, Tool.SELECT_ELLIPSE):
            p = self.canvas_pos(e)
            # Shift 约束正圆/正方形
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                dx = p.x() - self._start.x()
                dy = p.y() - self._start.y()
                side = max(abs(dx), abs(dy))
                dx = side if dx >= 0 else -side
                dy = side if dy >= 0 else -side
                p = QPointF(self._start.x() + dx, self._start.y() + dy)
            self.editor._preview_rect_sel(self._start, p, tool)
        elif tool == Tool.SELECT_LASSO:
            self._lasso_pts.append(p)
            self.editor._preview_lasso(self._lasso_pts)
        elif tool == Tool.POLY_LASSO and self._poly_pts:
            # 显示从最后一个顶点到鼠标位置的预览线
            preview = list(self._poly_pts) + [p]
            self.editor._preview_lasso(preview)
        elif tool in (Tool.BRUSH, Tool.ERASER):
            self.editor._stroke_point(p, e)
        elif tool in (Tool.CLONE, Tool.HEAL):
            if getattr(self.editor, "_clone_painting", False):
                self.editor._stroke_clone(p, e)
        elif tool == Tool.QUICK_SELECT:
            self.editor._quick_select_point(p)
        elif tool in (Tool.SHAPE_RECT, Tool.SHAPE_ELLIPSE, Tool.SHAPE_LINE):
            # Shift 约束正圆/正方形
            pt = p
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                dx = p.x() - self._start.x()
                dy = p.y() - self._start.y()
                side = max(abs(dx), abs(dy))
                dx = side if dx >= 0 else -side
                dy = side if dy >= 0 else -side
                pt = QPointF(self._start.x() + dx, self._start.y() + dy)
            self.editor._line_direction = (pt.x()-self._start.x(),pt.y()-self._start.y())
            self._pending_shape_rect = QRectF(self._start, pt).normalized()
            self.editor._preview_shape(tool, self._pending_shape_rect)
        elif tool == Tool.CROP:
            rect = self.editor.crop_geometry(self._start, p, bool(e.modifiers() & Qt.KeyboardModifier.AltModifier))
            self.editor._crop_rect = rect
            self.editor._preview_crop(rect)
        elif tool == Tool.GRADIENT:
            # 渐变：拖拽时实时显示「线段」指示（起点/终点圆点 + 连线 + 角度）
            radial = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.editor._preview_gradient(self._start, p, radial)
        self._last = p
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent):
        if self.editor.finish_text_pointer(e):
            return
        if e.button() == Qt.MouseButton.LeftButton and self.editor._finish_layer_marquee(e.position()):
            return
        # 空格抓手模式：交给 QGraphicsView 原生处理
        if self.editor._space_held:
            self._pan_last = None
            return
        # P5 标尺参考线落点：在画布区域内释放 → 正式添加参考线
        if self._guide_drag is not None:
            kind = self._guide_drag
            self._guide_drag = None
            self.editor._guide_preview = None
            px, py = e.position().x(), e.position().y()
            if px >= self.RULER_W and py >= self.RULER_W:
                sp = self.canvas_pos(e)
                if kind == "h":
                    self.editor.project.h_guides.append(sp.y())
                else:
                    self.editor.project.v_guides.append(sp.x())
                self.editor._push_undo_snapshot("添加参考线", self._guide_pre_snap)
                self.editor._draw_guides_and_grid()
            self._guide_pre_snap = None
            self.viewport().update()
            return
        # P5 拖动已有参考线结束：拖回标尺区域 → 删除该线
        if getattr(self, "_guide_move", None) is not None:
            kind, idx = self._guide_move
            self._guide_move = None
            px, py = e.position().x(), e.position().y()
            in_ruler = (px < self.RULER_W or py < self.RULER_W)
            if in_ruler:
                lst = self.editor.project.h_guides if kind == 'h' else self.editor.project.v_guides
                if 0 <= idx < len(lst):
                    lst.pop(idx)
            values = (self.editor.project.h_guides if kind == "h"
                      else self.editor.project.v_guides)
            current = values[idx] if 0 <= idx < len(values) else None
            if current != getattr(self, "_guide_move_orig", None):
                self.editor._push_undo_snapshot("移动/删除参考线", self._guide_pre_snap)
            self._guide_pre_snap = None
            self._guide_move_orig = None
            self.editor._draw_guides_and_grid()
            self.viewport().update()
            return
        if not self._dragging and self._handle_drag is None and self._artboard_drag is None:
            return
        # 结束画板拖拽
        if self._artboard_drag is not None:
            self._artboard_drag = None
            self._dragging = False
            self.editor._redraw()
            self.viewport().update()
            return
        # 结束手柄拖拽
        if self._handle_drag is not None:
            layer = self.editor.active
            orig = self._handle_orig
            self._handle_drag = None
            self._handle_orig = {}
            self._dragging = False
            if self.editor._clear_interaction_preview():
                self.editor._redraw()
            self.editor._smart_guides = []   # P5 清除洋红参考线
            self.editor._rot_hud.hide()       # 隐藏旋转角度 HUD
            # 操作后入栈：仅当确有变化才记录，避免空拖拽产生无效撤销步
            if layer is not None and orig:
                changed = ((layer.x, layer.y, layer.scale, layer.rotation) !=
                           (orig["x"], orig["y"], orig["scale"], orig["rotation"]))
                changed = changed or layer.quad != orig.get('quad',[])
                if changed:
                    self.editor._push_undo_snapshot("变换图层", self._handle_pre_snap)
            self.viewport().update()
            return
        p = self.canvas_pos(e)
        tool = self.editor.tool
        if tool == Tool.MOVE:
            self.editor._end_move()
        elif tool in (Tool.SELECT_RECT, Tool.SELECT_ELLIPSE):
            self.editor._commit_rect_sel(self._start, p, tool, self.editor._sel_combine)
        elif tool == Tool.SELECT_LASSO:
            self.editor._commit_lasso(self._lasso_pts, self.editor._sel_combine)
            self._lasso_pts = []
        elif tool in (Tool.SHAPE_RECT, Tool.SHAPE_ELLIPSE, Tool.SHAPE_LINE):
            self.editor._commit_shape(tool, self._pending_shape_rect)
            self._pending_shape_rect = None
        elif tool == Tool.CROP:
            rect = self.editor.crop_geometry(self._start, p, bool(e.modifiers() & Qt.KeyboardModifier.AltModifier))
            self.editor._crop_rect = rect
            self.editor._preview_crop(rect)
        elif tool == Tool.GRADIENT:
            radial = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.editor._create_gradient_layer(self._start, p, radial)
            self.sel_item.setPixmap(QPixmap())  # 清除拖拽线段预览
        elif tool in (Tool.CLONE, Tool.HEAL):
            self.editor._clone_painting = False
            self.editor._brush_last_point = None
            # 松开后：若仍按住 Alt → 维持「设源点」光标，否则回到常态
            self._set_canvas_cursor(
                self.editor._clone_cursor(
                    alt=bool(e.modifiers() & Qt.KeyboardModifier.AltModifier)))
        elif tool in (Tool.BRUSH, Tool.ERASER):
            self.editor._stroke_point(p, e, force=True)
            self.editor._brush_anchor = QPointF(p)
            self.editor._brush_last_point = None
            self._set_canvas_cursor(self.editor._brush_cursor())
            self._brush_hover_pos = QPointF(e.position())
            self.viewport().update()
        elif tool == Tool.POLY_LASSO:
            pass  # 多边形套索通过 Enter 或双击闭合
        elif tool == Tool.QUICK_SELECT:
            self.editor._quick_select_point(p)
            self.editor._quick_select_end()
        self._dragging = False
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e: QMouseEvent):
        """双击：文字层编辑 / 多边形套索闭合 / 文字工具空白双击新建文字框"""
        # 多边形套索双击 = 闭合多边形
        if self.editor.tool == Tool.POLY_LASSO and self._poly_pts and len(self._poly_pts) >= 3:
            self.editor._commit_lasso(self._poly_pts, self.editor._sel_combine)
            self._poly_pts = None
            return
        pt = self.canvas_pos(e)
        hit = self.editor._hit_test_at(pt, kinds=("text",))
        if self.editor.tool == Tool.TEXT:
            # 文字工具：双击空白→新建文字框；双击已有文字→编辑
            if self.editor._text_editing:
                self.editor._end_text_edit(save=True)
            if hit is not None:
                self.editor.set_active(hit)
                self.editor._start_text_edit(hit, is_new=False)
            else:
                self.editor._start_text_edit(None, is_new=True, pt=pt)
        elif hit is not None:
            if self.editor._text_editing:
                self.editor._end_text_edit(save=True)
            self.editor.set_active(hit)
            self.editor._start_text_edit(hit, is_new=False)
        self.setFocus()
        super().mouseDoubleClickEvent(e)

    def dragEnterEvent(self, e):
        if self._has_supported_drop_image(e.mimeData()):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        # 持续接受移动事件，避免 QGraphicsScene/父窗口在拖动途中抢走 drop。
        if self._has_supported_drop_image(e.mimeData()):
            e.acceptProposedAction()
        else:
            e.ignore()

    @staticmethod
    def _has_supported_drop_image(mime) -> bool:
        if not mime.hasUrls():
            return False
        from core.image_import import SUPPORTED_SUFFIXES
        supported = SUPPORTED_SUFFIXES
        return any(u.isLocalFile() and os.path.splitext(u.toLocalFile())[1].lower() in supported
                   for u in mime.urls())

    def dropEvent(self, e):
        # 批量拖入时逐个导入，只有首张成功导入的图片可调整空画布。
        imported = False
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if not p:
                continue
            ext = os.path.splitext(p)[1].lower()
            from core.image_import import SUPPORTED_SUFFIXES
            if ext not in SUPPORTED_SUFFIXES:
                continue
            if self.editor.add_image_from_path(p, from_drop=True):
                imported = True
        if imported:
            e.acceptProposedAction()
        else:
            e.ignore()

    def keyPressEvent(self, e):
        self.editor.keyPressEvent(e)

    def inputMethodEvent(self, e):
        # 文字编辑态：把 IME 组合/提交事件转交给 ImageEditorWidget 处理
        if self.editor._text_editing:
            self.editor.inputMethodEvent(e)
        else:
            super().inputMethodEvent(e)

    def inputMethodQuery(self, query):
        if self.editor._text_editing:
            return self.editor.inputMethodQuery(query)
        return super().inputMethodQuery(query)

    def _on_context_menu(self, pos):
        """右键上下文菜单：画布上有像素选区（蚂蚁线）时给出选区操作。"""
        self.editor._canvas_context_menu(self.mapToGlobal(pos))


# ───────────────────────────── 图层列表项 ─────────────────────────────
class LayerList(QListWidget):
    """图层面板列表：支持图层行拖拽重排序（上下移动 z-order）。"""

    LAYER_MIME = "application/x-cep-layer"

    def __init__(self, editor=None):
        super().__init__()
        self.editor = editor
        self._drop_row = -1          # 插入指示线位置（0..count）
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(self.LAYER_MIME):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(self.LAYER_MIME):
            e.acceptProposedAction()
            self._drop_row = self._row_at(e.position())
            self.viewport().update()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self._drop_row = -1
        self.viewport().update()
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        if not e.mimeData().hasFormat(self.LAYER_MIME):
            e.ignore()
            return
        ed = self.editor
        row = self._row_at(e.position())
        self._drop_row = -1
        self.viewport().update()
        if ed is None or getattr(ed, "_drag_layer", None) is None:
            e.ignore()
            return
        layer = ed._drag_layer
        ed._drag_layer = None
        ed._drop_layer_at(layer, row)
        e.acceptProposedAction()

    def _row_at(self, pos):
        """返回拖拽悬停处的插入位置（列表顺序，0=最顶层）。"""
        idx = self.indexAt(QPoint(int(pos.x()), int(pos.y())))
        if not idx.isValid():
            return self.count()
        rect = self.visualItemRect(self.item(idx.row()))
        if pos.y() < rect.center().y():
            return idx.row()
        return idx.row() + 1

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._drop_row < 0 or self._drop_row > self.count():
            return
        v = self.viewport()
        p = QPainter(v)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # PS 风格：在目标落点画一条醒目的实线 + 半透明高亮带，明确「拖到第几就第几个」
        if self._drop_row >= self.count() or self.count() == 0:
            y = v.rect().bottom() if self.count() > 0 else 0
            r = None
        else:
            r = self.visualItemRect(self.item(self._drop_row))
            y = r.top()
        # 半透明高亮带（目标槽位）
        band = QColor(AUX)
        band.setAlpha(40)
        p.fillRect(v.rect().left(), y - (r.height() // 2 if r else 14),
                   v.rect().width(), (r.height() if r else 28), band)
        # 实线指示
        p.setPen(QPen(QColor(AUX), 2))
        p.drawLine(v.rect().left() + 2, y, v.rect().right() - 2, y)
        p.end()


class LayerItemWidget(QWidget):
    def __init__(self, layer, editor):
        super().__init__()
        self.layer = layer
        self.editor = editor
        self.setFixedHeight(50)
        self.setStyleSheet("LayerItemWidget{background:transparent;}")
        self._press_pos = None        # 拖拽排序：按下起点
        self._can_drag = False        # 是否允许发起拖拽（点按钮时为 False）
        self._drag_started = False
        self._pending_click_select = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(8)
        if layer.kind == 'group':
            disclosure = QPushButton('▸' if layer.collapsed else '▾')
            disclosure.setFixedSize(22, 26)
            disclosure.setToolTip('展开 / 收起文件夹')
            def toggle_folder():
                layer.collapsed = not layer.collapsed
                QTimer.singleShot(0, editor._refresh_layers)
            disclosure.clicked.connect(toggle_folder)
            lay.addWidget(disclosure)

        self.eye = workbench_button("", None, accent=IMAGE_ACCENT, quiet=True)
        self.eye.setFixedSize(26, 26)
        self.eye.setIconSize(QSize(16, 16))
        self._update_eye_button()
        self.eye.clicked.connect(self._toggle_eye)
        lay.addWidget(self.eye)

        self.thumb = QLabel()
        self.thumb.setFixedSize(38, 38)
        self.thumb.setStyleSheet("border:1px solid #3a3a3d;border-radius:2px;background:#1a1a1d;")
        lay.addWidget(self.thumb)

        # P4 前缀标识：调整图层 ◐ / 智能对象 📦 / 编组 🗂
        prefix = ""
        if layer.kind == 'group':
            prefix = '文件夹 · '
        if layer.kind == "adjust":
            prefix = "◐ "
        elif getattr(layer, "smart", False):
            prefix = "📦 "
        if getattr(layer, "group_id", None) is not None:
            prefix = f"🗂 {prefix}"
        self.name = QLabel(prefix + layer.name)
        self.name.setStyleSheet("text-align:left;border:none;background:transparent;color:#aaa;")
        lay.addWidget(self.name, 1)
        self.lock_button = workbench_button("", None, accent=IMAGE_ACCENT, quiet=True)
        self.lock_button.setCheckable(True)
        self.lock_button.setFixedSize(26, 26)
        self.lock_button.setIconSize(QSize(16, 16))
        self._update_lock_button()
        self.lock_button.clicked.connect(self._toggle_lock)
        lay.addWidget(self.lock_button)

    def _toggle_lock(self, checked):
        self.editor._push_undo("锁定图层" if checked else "解锁图层")
        self.layer.locked = checked
        self._update_lock_button()
        self.editor._draw_handles()

    def _toggle_eye(self):
        self.editor._push_undo("切换图层可见性")
        self.layer.visible = not self.layer.visible
        self._update_eye_button()
        self.editor._redraw()

    def _update_eye_button(self):
        visible = self.layer.visible
        label = "隐藏图层" if visible else "显示图层"
        self.eye.setIcon(workbench_icon("eye" if visible else "eye-off",
                                       "#dce4f4" if visible else "#8b8c99"))
        self.eye.setAccessibleName(label)
        self.eye.setToolTip(label)

    def _update_lock_button(self):
        locked = self.layer.locked
        label = "解锁图层" if locked else "锁定图层"
        self.lock_button.setChecked(locked)
        self.lock_button.setIcon(workbench_icon("lock-keyhole" if locked else "lock-keyhole-open",
                                               IMAGE_ACCENT if locked else "#8b8c99"))
        self.lock_button.setAccessibleName(label)
        self.lock_button.setToolTip(label)

    def mousePressEvent(self, e):
        """点击图层行主体即选中；双击文字层 → 直接编辑文字。

        使用自己实现的双击检测，因为子控件会吃掉 QListWidget 的 itemDoubleClicked 信号。
        """
        if e.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(e)
            return
        # 拖拽排序准备：记录按下起点（点中眼睛时不发起拖拽）
        child = self.childAt(e.position().toPoint())
        if child is not None and child is self.eye:
            # 眼睛按钮：仅切换可见性，不选中 / 不拖拽（对齐 PS）
            self._can_drag = False
            self._press_pos = None
            super().mousePressEvent(e)
            return
        self._can_drag = True
        self._press_pos = e.position()
        self._drag_started = False
        layer = self.layer
        editor = self.editor
        # Keep a multiselection intact while the pointer may start a drag.
        # A plain click still collapses it on mouse release.
        self._pending_click_select = (
            len(editor.selected) > 1 and layer in editor.selected
            and not (e.modifiers() & (Qt.KeyboardModifier.ControlModifier
                                    | Qt.KeyboardModifier.ShiftModifier)))
        if self._pending_click_select:
            super().mousePressEvent(e)
            return
        now = time.time()
        dbl_interval = QApplication.instance().doubleClickInterval() / 1000.0

        if (editor._layer_dbl_last_layer is layer and
                (now - editor._layer_dbl_last_time) < dbl_interval):
            # 双击
            editor._layer_dbl_last_layer = None
            if layer.kind == "text":
                QTimer.singleShot(0, lambda ly=layer, ed=editor: (
                    ed.set_active(ly), ed._start_text_edit(ly, is_new=False)))
            elif layer.kind == "adjust":
                # P4 双击调整图层 → 编辑参数
                QTimer.singleShot(0, lambda ly=layer, ed=editor:
                                  ed._edit_adjust_layer(ly))
            elif getattr(layer, "smart", False) and layer.smart_source:
                # P4 双击智能对象 → 还原编辑内容
                QTimer.singleShot(0, lambda ly=layer, ed=editor:
                                  ed._edit_smart_object(ly))
        else:
            # 单击：记录时间，延迟执行选中（防双击误触两次选中）
            editor._layer_dbl_last_layer = layer
            editor._layer_dbl_last_time = now
            QTimer.singleShot(0, lambda: editor._select_layer_interactive(layer))
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if (self._can_drag and self._press_pos is not None and not self._drag_started
                and (e.buttons() & Qt.MouseButton.LeftButton)):
            d = e.position() - self._press_pos
            if d.x() * d.x() + d.y() * d.y() > 36:   # 拖动超过 6px 即发起排序
                self._start_drag()
                return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._pending_click_select and not self._drag_started:
            self.editor._select_layer_interactive(self.layer)
        self._pending_click_select = False
        self._can_drag = False
        self._press_pos = None
        self._drag_started = False
        super().mouseReleaseEvent(e)

    def _start_drag(self):
        self._drag_started = True
        self._pending_click_select = False
        ed = self.editor
        ed._drag_layer = self.layer
        mime = QMimeData()
        mime.setData(LayerList.LAYER_MIME, b"x")
        exported_paths = ed._export_layers_for_drag(self.layer)
        if exported_paths:
            mime.setUrls([QUrl.fromLocalFile(path) for path in exported_paths])
        drag = QDrag(self)
        drag.setMimeData(mime)
        try:
            pm = self.grab().scaledToWidth(180, Qt.TransformationMode.SmoothTransformation)
            drag.setPixmap(pm)
            drag.setHotSpot(QPoint(int(pm.width() / 2), int(pm.height() / 2)))
        except Exception:
            pass
        result = Qt.DropAction.IgnoreAction
        try:
            result = drag.exec(
                Qt.DropAction.CopyAction | Qt.DropAction.MoveAction,
                Qt.DropAction.CopyAction)
        finally:
            # A local reorder clears _drag_layer. External targets keep the
            # PNGs because their saved projects reference these paths.
            if result == Qt.DropAction.IgnoreAction or ed._drag_layer is None:
                for path in exported_paths:
                    Path(path).unlink(missing_ok=True)
            self._press_pos = None
            self._drag_started = False
            ed._drag_layer = None

    def refresh(self):
        self.name.setText(self.layer.name + (" ↳" if self.layer.clip else ""))
        self._update_eye_button()
        self._update_lock_button()
        # 缩略图：用图层自身内容渲染并居中，保留透明通道
        pm = self.editor._layer_thumbnail(self.layer, 38)
        if not pm.isNull():
            self.thumb.setPixmap(pm)
        self._apply_highlight()
        # 如果该图层已被删除（可能属于某画板），销毁自己
        if self.layer not in self.editor._all_layers():
            self.deleteLater()

    def _apply_highlight(self):
        """仅刷新选中高亮样式（不重渲缩略图）。

        供面板选中时频繁调用——widget 始终存活，避免在点击/拖拽中
        因重建列表而销毁正在交互的 widget（那是「点不动/拖不动」的根因）。"""
        # active 主图层最强，selected 次选稍弱，未选常规
        if self.layer is self.editor.active:
            self.setStyleSheet(f"LayerItemWidget{{background:#342c49;border-left:4px solid {IMAGE_ACCENT};border-radius:6px;}}")
            self.name.setStyleSheet("text-align:left;border:none;background:transparent;color:#ffffff;font-weight:bold;")
            self.thumb.setStyleSheet(f"border:1px solid {IMAGE_ACCENT};border-radius:4px;background:#1a1a1d;")
        elif self.layer in self.editor.selected:
            self.setStyleSheet("LayerItemWidget{background:#2a253a;border-left:4px solid #7967a2;border-radius:6px;}")
            self.name.setStyleSheet("text-align:left;border:none;background:transparent;color:#e6f4fb;")
            self.thumb.setStyleSheet("border:1px solid #7967a2;border-radius:4px;background:#1a1a1d;")
        else:
            self.setStyleSheet("LayerItemWidget{background:transparent;border-left:4px solid transparent;}")
            self.name.setStyleSheet("text-align:left;border:none;background:transparent;color:#aaa;")
            self.thumb.setStyleSheet("border:1px solid #3a3a3d;border-radius:2px;background:#1a1a1d;")


class ArtboardHeaderWidget(QWidget):
    """图层面板里的画板分组头（PS 风格：折叠箭头 + 名称 + 操作）。"""
    def __init__(self, artboard, editor):
        super().__init__()
        self.artboard = artboard
        self.editor = editor
        self.setFixedHeight(30)
        self.setStyleSheet("ArtboardHeaderWidget{background:#2a2a30;}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.setSpacing(6)
        self.toggle = QPushButton("▾" if not artboard.collapsed else "▸")
        self.toggle.setFixedSize(20, 20)
        self.toggle.setStyleSheet("border:none;background:transparent;color:#bbb;font-size:11px;")
        self.toggle.clicked.connect(self._toggle)
        lay.addWidget(self.toggle)
        self.name = QLabel(artboard.name)
        self.name.setStyleSheet("color:#fff;font-weight:bold;font-size:12px;")
        lay.addWidget(self.name, 1)
        self.addbtn = QPushButton("＋")
        self.addbtn.setFixedSize(20, 20)
        self.addbtn.setToolTip("添加图片到该画板")
        self.addbtn.setStyleSheet("border:none;background:transparent;color:#9fe;")
        self.addbtn.clicked.connect(self._add_image)
        lay.addWidget(self.addbtn)
        self.delbtn = QPushButton("×")
        self.delbtn.setFixedSize(20, 20)
        self.delbtn.setStyleSheet("border:none;background:transparent;color:#c98;font-size:15px;font-weight:bold;")
        self.delbtn.setToolTip("删除画板")
        self.delbtn.clicked.connect(self._del)
        lay.addWidget(self.delbtn)

    def _toggle(self):
        self.artboard.collapsed = not self.artboard.collapsed
        self.editor._refresh_layers()

    def _add_image(self):
        self.editor._set_active_artboard(self.artboard)
        self.editor.add_image_dialog()

    def _del(self):
        self.editor._delete_artboard(self.artboard)

    def mousePressEvent(self, e):
        # 点击画板标题行（非按钮区域）切换激活画板
        if e.button() == Qt.MouseButton.LeftButton:
            ab = self.artboard
            editor = self.editor
            QTimer.singleShot(0, lambda: editor._set_active_artboard(ab))
        super().mousePressEvent(e)

    def refresh(self):
        active = (self.artboard is self.editor.active_artboard)
        if active:
            self.setStyleSheet("ArtboardHeaderWidget{background:#3a3a44;border-left:3px solid #00eaff;}")
        else:
            self.setStyleSheet("ArtboardHeaderWidget{background:#2a2a30;border-left:3px solid transparent;}")
        self.name.setText(self.artboard.name)
        self.toggle.setText("▾" if not self.artboard.collapsed else "▸")


class ArtboardDialog(QDialog):
    """新建画板对话框：预设设备尺寸 / 自定义宽高 / 命名。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建画板")
        self.setMinimumWidth(280)
        self.setStyleSheet(
            "QDialog{background:#1e1e22;} QLabel{color:#ccc;font-size:12px;}"
            "QComboBox,QSpinBox,QLineEdit{background:#2a2a2e;color:#eee;"
            "border:1px solid #3a3a3e;border-radius:3px;padding:4px;}"
            "QPushButton{background:#252528;color:#ccc;border:1px solid #333;"
            "border-radius:3px;padding:5px 12px;}"
            "QPushButton:hover{background:#333;color:#fff;}")
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.addWidget(QLabel("预设尺寸"))
        self.preset = QComboBox()
        self.presets = [
            ("当前画布", 0, 0),
            ("方形 1080×1080", 1080, 1080),
            ("竖屏手机 1080×1920", 1080, 1920),
            ("iPhone 14 1170×2532", 1170, 2532),
            ("iPad 2048×2732", 2048, 2732),
            ("横屏 1920×1080", 1920, 1080),
            ("自定义", -1, -1),
        ]
        for name, w, h in self.presets:
            self.preset.addItem(name)
        self.preset.setCurrentIndex(0)
        lay.addWidget(self.preset)
        lay.addWidget(QLabel("画板名称"))
        self.name_edit = QLineEdit("画板")
        lay.addWidget(self.name_edit)
        hl = QHBoxLayout()
        hl.addWidget(QLabel("宽"))
        self.w_spin = QSpinBox(); self.w_spin.setRange(1, 8000); self.w_spin.setValue(1080)
        hl.addWidget(self.w_spin, 1)
        hl.addWidget(QLabel("高"))
        self.h_spin = QSpinBox(); self.h_spin.setRange(1, 8000); self.h_spin.setValue(1080)
        hl.addWidget(self.h_spin, 1)
        lay.addLayout(hl)
        bl = QHBoxLayout()
        ok = QPushButton("确定"); ok.setDefault(True); ok.clicked.connect(self.accept)
        cancel = QPushButton("取消"); cancel.clicked.connect(self.reject)
        bl.addStretch(); bl.addWidget(ok); bl.addWidget(cancel)
        lay.addLayout(bl)
        self.preset.currentIndexChanged.connect(self._on_preset)
        if hasattr(parent, "project"):
            n = len(parent.project.artboards) + 1
            self.name_edit.setText(f"画板 {n}")
        self._on_preset(0)

    def _on_preset(self, idx):
        name, w, h = self.presets[idx]
        if w > 0 and h > 0:
            self.w_spin.setValue(w); self.h_spin.setValue(h)
        elif w == 0 and h == 0 and self.parent() is not None:
            ed = self.parent()
            if ed.active_artboard is not None:
                self.w_spin.setValue(ed.active_artboard.w)
                self.h_spin.setValue(ed.active_artboard.h)
            else:
                self.w_spin.setValue(ed.project.w)
                self.h_spin.setValue(ed.project.h)


# ───────────────────────────── AI 抠图工作线程 ─────────────────────────────
class _RembgWorker(QThread):
    """后台跑 rembg.remove()，避免模型下载 + CPU 推理冻结 GUI。"""
    finished = pyqtSignal(object)   # numpy RGBA 数组 或 None
    error = pyqtSignal(str)

    def __init__(self, arr, use_subprocess=False, python_exe=None):
        super().__init__()
        self.arr = arr
        self.use_subprocess = use_subprocess
        self.python_exe = python_exe or sys.executable

    def run(self):
        try:
            if self.use_subprocess:
                result = self._run_in_subprocess()
            else:
                result = self._run_in_thread()
            self.finished.emit(result)
        except BaseException as e:
            msg = str(e)
            lower = msg.lower()
            if "no onnxruntime backend found" in lower:
                msg = "AI 抠图后端 onnxruntime 未正确安装或加载失败，请重启应用后再试。\n原始错误：" + msg
            elif "onnxruntime" in lower or "backend" in lower:
                msg = "AI 抠图后端(onnxruntime)不可用：" + msg
            elif "download" in lower or "url" in lower or "connection" in lower or "ssl" in lower:
                msg = "下载 U²-Net 模型失败，请检查网络连接后重试。\n原始错误：" + msg
            self.error.emit(msg[:900])

    def _run_in_thread(self):
        """在当前进程线程内运行（默认路径，性能最好）。"""
        import importlib
        importlib.import_module("onnxruntime")  # 触发原生 DLL 加载；失败抛 BaseException
        rembg = importlib.import_module("rembg")
        remove = rembg.remove
        new_session = rembg.new_session
        from PIL import Image
        session = new_session("u2netp")
        pil = Image.fromarray(self.arr, "RGBA")
        out = remove(pil, session=session)
        return np.array(out.convert("RGBA"))

    def _run_in_subprocess(self):
        """在当前进程无法 import rembg 时，派生子进程完成推理。

        通过临时 PNG 文件传递输入/输出，避免当前进程加载 onnxruntime/rembg。
        """
        import os
        import tempfile
        import subprocess
        from PIL import Image

        fd_in, in_path = tempfile.mkstemp(suffix=".png", prefix="rembg_in_")
        fd_out, out_path = tempfile.mkstemp(suffix=".png", prefix="rembg_out_")
        fd_script, script_path = tempfile.mkstemp(suffix=".py", prefix="rembg_sub_")
        os.close(fd_in)
        os.close(fd_out)
        os.close(fd_script)

        try:
            Image.fromarray(self.arr, "RGBA").save(in_path, "PNG")

            script = (
                "import sys\n"
                "from PIL import Image\n"
                "from rembg import remove, new_session\n"
                "inp, out = sys.argv[1], sys.argv[2]\n"
                "session = new_session('u2netp')\n"
                "img = Image.open(inp)\n"
                "if img.mode != 'RGBA':\n"
                "    img = img.convert('RGBA')\n"
                "result = remove(img, session=session)\n"
                "result.save(out, 'PNG')\n"
            )
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(script)

            proc = subprocess.run(
                [self.python_exe, script_path, in_path, out_path],
                capture_output=True, text=True, timeout=300)
            if proc.returncode != 0:
                err = (proc.stderr or "").strip() or (proc.stdout or "").strip() or "子进程异常退出"
                raise RuntimeError("AI 抠图子进程失败：{}".format(err[:500]))

            out_img = Image.open(out_path).convert("RGBA")
            return np.array(out_img)
        finally:
            for p in (in_path, out_path, script_path):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass


# ───────────────────────────── AI 模型管理 ─────────────────────────────
def _cep_models_dir():
    """AI 模型统一存放目录 ~/.cep_models/（自动创建）。"""
    import os
    d = os.path.join(os.path.expanduser("~"), ".cep_models")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


# ONNX 模型下载源（均为社区稳定直链）。自动下载失败时，
# 用户可手动把同名文件放进 ~/.cep_models/ 即可离线启用。
_AI_MODELS = {
    "realesr_x4": {
        # Real-ESRGAN General x4 v3 官方轻量模型（仅 ~5MB，动态输入，×4 输出）
        # 2026-07-23 实测两个直链均可达；hf-mirror 在前（国内可直连）
        "file": "realesr_general_x4v3.onnx",
        "urls": [
            "https://hf-mirror.com/Heliosoph/realesrgan-onnx/resolve/main/realesr-general-x4v3.onnx",
            "https://huggingface.co/Heliosoph/realesrgan-onnx/resolve/main/realesr-general-x4v3.onnx",
        ],
    },
    "realesr_anime_x4": {
        # Real-ESRGAN AnimeVideo v3：针对插画/线稿/二次元优化，边缘锐利、不糊线条
        # （通用版会把这些内容平滑成锯齿/模糊，故插画优先用此模型）
        "file": "realesr_animevideov3.onnx",
        "urls": [
            "https://hf-mirror.com/Heliosoph/realesrgan-onnx/resolve/main/realesr-animevideov3.onnx",
            "https://huggingface.co/Heliosoph/realesrgan-onnx/resolve/main/realesr-animevideov3.onnx",
        ],
    },
    "yunet": {
        "file": "face_detection_yunet_2023mar.onnx",
        "urls": [
            "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
            "https://hf-mirror.com/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx",
        ],
    },
    "gfpgan": {  # 340MB，ONNX 版 GFPGANv1.4
        "file": "gfpgan_1.4.onnx",
        "urls": [
            "https://hf-mirror.com/hacksider/deep-live-cam/resolve/main/GFPGANv1.4.onnx",
            "https://huggingface.co/hacksider/deep-live-cam/resolve/main/GFPGANv1.4.onnx",
        ],
    },
    "birefnet": {  # SOTA 通用去背，~250MB，运行时按需下载（优于 u2netp）
        "file": "birefnet-general.onnx",
        "urls": [
            "https://github.com/danielgatis/rembg/releases/download/v0.0.0/BiRefNet-general-epoch_244.onnx",
        ],
        "desc": "BiRefNet 通用去背（SOTA，效果优于 u2netp）",
    },
    "codeformer": {  # 人脸修复，377MB，运行时按需下载（优于 GFPGAN）
        "file": "codeformer.onnx",
        "urls": [
            "https://huggingface.co/Jonny001/Models-Pack-01/resolve/main/codeformer.onnx",
            "https://hf-mirror.com/Jonny001/Models-Pack-01/resolve/main/codeformer.onnx",
        ],
        "desc": "CodeFormer 人脸修复（优于 GFPGAN）",
    },
}


def _model_local_path(key):
    """返回模型本地路径（不下载）。"""
    import os
    spec = _AI_MODELS.get(key)
    if not spec:
        return None
    return os.path.join(_cep_models_dir(), spec["file"])


def _model_exists(key):
    import os
    p = _model_local_path(key)
    return bool(p and os.path.exists(p) and os.path.getsize(p) > 4096)


def _download_model(key, progress_cb=None):
    """下载/定位 ONNX 模型，返回本地路径；失败返回 None。已存在则直接返回。"""
    import os
    import urllib.request
    spec = _AI_MODELS.get(key)
    if not spec:
        return None
    dest = os.path.join(_cep_models_dir(), spec["file"])
    if os.path.exists(dest) and os.path.getsize(dest) > 4096:
        return dest
    for url in spec["urls"]:
        tmp = dest + ".part"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            # 大模型（BiRefNet ~900MB）单次读 128KB 在弱网下可能超过 30s，
            # 这里放宽到 120s，并允许用 total<=0 时仍按 MB 报进度
            with urllib.request.urlopen(req, timeout=120) as resp:
                total = int(resp.headers.get("Content-Length", 0) or 0)
                got = 0
                with open(tmp, "wb") as f:
                    while True:
                        try:
                            chunk = resp.read(131072)
                        except Exception:
                            # 单次读超时：跳过这一块继续读，不中断整次下载
                            continue
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        if progress_cb:
                            if total:
                                progress_cb(got, total)
                            else:
                                # 未知总长：每 1MB 回调一次（用 total=-1 表示「无总量」）
                                if got % (1024 * 1024) < 131072:
                                    progress_cb(got, -1)
        except Exception:
            pass
        finally:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
    return None


# ───────────────────────────── AI 增强工作线程（超分 / 人脸修复） ─────────────
def _looks_like_illustration(rgb):
    """粗略判断 RGB 是否为插画/线稿/二次元（适合锐利的 anime 超分模型）。

    依据：① 高饱和纯色像素占比高；② 边缘密集锐利。写实照片多为连续色调、
    少见大块高饱和纯色，故据此区分。阈值偏保守，避免把真实照片误判成插画。
    """
    import numpy as np
    try:
        import cv2
        r = rgb[:, :, 0].astype(np.int16)
        g = rgb[:, :, 1].astype(np.int16)
        b = rgb[:, :, 2].astype(np.int16)
        mx = np.maximum(np.maximum(r, g), b)
        mn = np.minimum(np.minimum(r, g), b)
        sat = mx - mn
        n = r.size
        if n == 0:
            return False
        # 高饱和且非极端亮度的彩色像素（插画常见大块纯色）
        vivid = np.sum((sat > 60) & (mx > 25) & (mx < 250))
        # Sobel 边缘密度
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        gy = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gx = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        gmag = np.sqrt(gx * gx + gy * gy)
        edge = np.sum(gmag > 60)
        vivid_ratio = vivid / n
        edge_ratio = edge / n
        # 同时满足：明显的高饱和纯色 + 明显的密集边缘 → 判定为插画
        return vivid_ratio > 0.15 and edge_ratio > 0.04
    except Exception:
        return False


class _AIEnhanceWorker(QThread):
    """通用 AI 增强后台线程。全程走 onnxruntime / opencv-DNN（无需 torch）。

    task='upscale'   → Real-ESRGAN x4（缺模型则 Lanczos+USM 降级）
    task='face'      → YuNet 检测 + CodeFormer/GFPGAN 修复（缺则经典磨皮+锐化）
    task='birefnet'  → BiRefNet 通用去背，输出带透明通道的 RGBA
    """
    finished = pyqtSignal(object, str)   # (RGBA numpy, 引擎描述)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, arr, task, factor=4, fidelity=0.5, sr_model="auto"):
        super().__init__()
        self.arr = arr
        self.task = task
        self.factor = factor
        self.fidelity = fidelity
        self.sr_model = sr_model   # "auto" | "general" | "anime"

    def run(self):
        try:
            if self.task == "upscale":
                out, eng = self._upscale()
            elif self.task == "birefnet":
                out, eng = self._birefnet()
            else:
                out, eng = self._face()
            self.finished.emit(out, eng)
        except BaseException as e:
            self.error.emit("{}: {}".format(type(e).__name__, e)[:900])

    # ---------- 超分 ----------
    def _upscale(self):
        import numpy as np
        import cv2
        rgba = self.arr
        rgb = rgba[:, :, :3]
        alpha = rgba[:, :, 3] if rgba.ndim == 3 and rgba.shape[2] == 4 else None
        h, w = rgb.shape[:2]
        out_rgb = None
        eng = ""

        # ① 选择超分模型：auto 时按内容（插画/写实）自动挑锐利或通用版
        pref = getattr(self, "sr_model", "auto")
        if pref == "auto":
            pref = "anime" if _looks_like_illustration(rgb) else "general"
        if pref == "anime":
            key, model_name = "realesr_anime_x4", "Real-ESRGAN 插画锐利版 x4"
        else:
            key, model_name = "realesr_x4", "Real-ESRGAN 通用版 x4"

        # ② 下载并推理（进程内 + 子进程兜底）
        try:
            self.progress.emit("准备超分模型（首次约 5MB）…")
            model = _download_model(
                key,
                lambda g, t: self.progress.emit(
                    "下载超分模型 {}%".format(g * 100 // t) if t > 0
                    else "下载超分模型 {:.0f} MB…".format(g / 1048576)))
        except Exception:
            model = None
        if model:
            # 进程内推理（打包版 onnxruntime 经 datas 内置，可直接 import）
            try:
                self.progress.emit("AI 超分推理中…")
                out_rgb = self._run_realesr(rgb, model)   # 输出 4×
                eng = "{} (ONNX)".format(model_name)
            except Exception:
                out_rgb = None
            # 子进程兜底：PyQt6/cv2 加载后进程内 onnxruntime DLL 常初始化失败
            # （与 rembg 同坑），干净子进程 import 正常。
            if out_rgb is None:
                try:
                    self.progress.emit("AI 超分推理中（子进程模式）…")
                    out_rgb = self._run_realesr_subproc(rgb, model)
                    eng = "{} (ONNX 子进程)".format(model_name)
                except Exception:
                    out_rgb = None
            if out_rgb is not None and self.factor != 4:
                out_rgb = cv2.resize(out_rgb, (w * self.factor, h * self.factor),
                                     interpolation=cv2.INTER_AREA)
        if out_rgb is None:
            self.progress.emit("AI 模型不可用，使用增强 Lanczos…")
            up = cv2.resize(rgb, (w * self.factor, h * self.factor),
                            interpolation=cv2.INTER_LANCZOS4)
            blur = cv2.GaussianBlur(up, (0, 0), 2.0)
            out_rgb = cv2.addWeighted(up, 1.6, blur, -0.6, 0)
            eng = "Lanczos4 + USM（未启用 AI 模型）"

        # ③ 透明通道对齐升级（Plan A）：用 LANCZOS4 放大避免线性模糊，
        #    再清理近二值 alpha，消除「AI 锐化 RGB + 半透明 alpha halo」造成的锯齿错位。
        oh, ow = out_rgb.shape[:2]
        if alpha is not None:
            up_a = cv2.resize(alpha, (ow, oh), interpolation=cv2.INTER_LANCZOS4)
            up_a = up_a.astype(np.float32)
            up_a = np.where(up_a < 12, 0.0, np.where(up_a > 243, 255.0, up_a))
            up_a = up_a.astype(np.uint8)
        else:
            up_a = np.full((oh, ow), 255, np.uint8)
        out = np.dstack([out_rgb.astype(np.uint8), up_a.astype(np.uint8)])
        return out, eng

    def _run_realesr(self, rgb, model_path):
        """Real-ESRGAN x4 ONNX 分块推理（RGB, NCHW, 0-1）。自动探测固定/动态输入。"""
        import numpy as np
        import onnxruntime as ort
        sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        inp = sess.get_inputs()[0]
        iname = inp.name
        shp = inp.shape  # 形如 [1,3,H,W] 或含动态维
        fixed = isinstance(shp[-1], int) and isinstance(shp[-2], int) and shp[-1] > 0 and shp[-2] > 0
        tile = int(shp[-1]) if fixed else 128
        tile = max(64, min(tile, 256))
        pad = 8 if not fixed else 0
        h, w = rgb.shape[:2]
        scale = 4
        x = rgb.astype(np.float32) / 255.0
        out = np.zeros((h * scale, w * scale, 3), np.float32)
        total = ((h + tile - 1) // tile) * ((w + tile - 1) // tile)
        done = 0
        for y0 in range(0, h, tile):
            for x0 in range(0, w, tile):
                y1 = min(y0 + tile, h)
                x1 = min(x0 + tile, w)
                yy0, xx0 = max(0, y0 - pad), max(0, x0 - pad)
                yy1, xx1 = min(h, y1 + pad), min(w, x1 + pad)
                patch = x[yy0:yy1, xx0:xx1, :]
                ph, pw = patch.shape[:2]
                if fixed:  # 固定输入尺寸：补齐到 tile×tile
                    buf = np.zeros((tile, tile, 3), np.float32)
                    buf[:ph, :pw, :] = patch
                    feed = buf
                else:
                    feed = patch
                t = np.transpose(feed, (2, 0, 1))[None, ...]
                res = sess.run(None, {iname: t})[0][0]
                res = np.clip(res, 0, 1)
                res = np.transpose(res, (1, 2, 0))
                if fixed:
                    res = res[:ph * scale, :pw * scale, :]
                oy0 = (y0 - yy0) * scale
                ox0 = (x0 - xx0) * scale
                out[y0 * scale:y1 * scale, x0 * scale:x1 * scale, :] = \
                    res[oy0:oy0 + (y1 - y0) * scale, ox0:ox0 + (x1 - x0) * scale, :]
                done += 1
                self.progress.emit("超分推理 {}%".format(min(99, done * 100 // total)))
        return (out * 255).clip(0, 255).astype(np.uint8)

    def _run_realesr_subproc(self, rgb, model_path):
        """子进程 ONNX 超分：当前进程 onnxruntime DLL 冲突时的兜底路径。

        输入/输出经 .npy 临时文件传递；脚本内容全 ASCII，路径走 argv（中文路径安全）。
        """
        import numpy as np
        import subprocess
        import tempfile
        import os
        script = (
            "import sys, numpy as np\n"
            "import onnxruntime as ort\n"
            "model, fin, fout = sys.argv[1], sys.argv[2], sys.argv[3]\n"
            "rgb = np.load(fin)\n"
            "sess = ort.InferenceSession(model, providers=['CPUExecutionProvider'])\n"
            "inp = sess.get_inputs()[0]\n"
            "shp = inp.shape\n"
            "fixed = isinstance(shp[-1], int) and isinstance(shp[-2], int) and shp[-1] > 0 and shp[-2] > 0\n"
            "tile = int(shp[-1]) if fixed else 128\n"
            "tile = max(64, min(tile, 256))\n"
            "pad = 8 if not fixed else 0\n"
            "h, w = rgb.shape[:2]\n"
            "scale = 4\n"
            "x = rgb.astype(np.float32) / 255.0\n"
            "out = np.zeros((h * scale, w * scale, 3), np.float32)\n"
            "for y0 in range(0, h, tile):\n"
            "    for x0 in range(0, w, tile):\n"
            "        y1 = min(y0 + tile, h); x1 = min(x0 + tile, w)\n"
            "        yy0, xx0 = max(0, y0 - pad), max(0, x0 - pad)\n"
            "        yy1, xx1 = min(h, y1 + pad), min(w, x1 + pad)\n"
            "        patch = x[yy0:yy1, xx0:xx1, :]\n"
            "        ph, pw = patch.shape[:2]\n"
            "        if fixed:\n"
            "            buf = np.zeros((tile, tile, 3), np.float32)\n"
            "            buf[:ph, :pw, :] = patch\n"
            "            feed = buf\n"
            "        else:\n"
            "            feed = patch\n"
            "        t = np.transpose(feed, (2, 0, 1))[None, ...]\n"
            "        res = sess.run(None, {inp.name: t})[0][0]\n"
            "        res = np.clip(res, 0, 1)\n"
            "        res = np.transpose(res, (1, 2, 0))\n"
            "        if fixed:\n"
            "            res = res[:ph * scale, :pw * scale, :]\n"
            "        oy0 = (y0 - yy0) * scale; ox0 = (x0 - xx0) * scale\n"
            "        out[y0 * scale:y1 * scale, x0 * scale:x1 * scale, :] = \\\n"
            "            res[oy0:oy0 + (y1 - y0) * scale, ox0:ox0 + (x1 - x0) * scale, :]\n"
            "np.save(fout, (out * 255).clip(0, 255).astype(np.uint8))\n"
            "print('ESR_OK')\n"
        )
        tmpdir = tempfile.mkdtemp(prefix="cep_esr_")
        fin = os.path.join(tmpdir, "in.npy")
        fout = os.path.join(tmpdir, "out.npy")
        try:
            np.save(fin, rgb)
            if getattr(sys, "frozen", False):
                # EXE 模式：以 --realesr-worker 重启自身为干净子进程，
                # 避开 PyQt6/cv2 已加载导致的 onnxruntime DLL 冲突。
                cmd = [sys.executable, "--realesr-worker", model_path, fin, fout]
            else:
                # 源码模式：用独立 python 跑内联脚本（同样未加载 PyQt6/cv2）。
                fscript = os.path.join(tmpdir, "run_esr.py")
                with open(fscript, "w", encoding="ascii") as f:
                    f.write(script)
                cmd = [sys.executable, fscript, model_path, fin, fout]
            p = subprocess.run(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600,
                creationflags=(subprocess.CREATE_NO_WINDOW
                               if hasattr(subprocess, "CREATE_NO_WINDOW") else 0))
            ok = p.returncode == 0 and b"ESR_OK" in (p.stdout or b"")
            if not ok:
                raise RuntimeError(
                    (p.stderr or b"").decode("utf-8", "replace")[:400] or "subprocess failed")
            return np.load(fout)
        finally:
            for fp in (fin, fout, fscript):
                try:
                    if os.path.exists(fp):
                        os.remove(fp)
                except Exception:
                    pass
            try:
                os.rmdir(tmpdir)
            except Exception:
                pass

    # ---------- 人脸修复 ----------
    def _face(self):
        import numpy as np
        import cv2
        rgba = self.arr
        rgb = rgba[:, :, :3].copy()
        alpha = rgba[:, :, 3] if rgba.ndim == 3 and rgba.shape[2] == 4 else None
        h, w = rgb.shape[:2]
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        self.progress.emit("准备人脸检测模型…")
        yunet = _download_model("yunet")
        faces = []
        if yunet:
            try:
                det = cv2.FaceDetectorYN.create(yunet, "", (w, h), 0.6, 0.3, 5000)
                det.setInputSize((w, h))
                _, res = det.detect(bgr)
                if res is not None:
                    faces = [f for f in res]
            except Exception:
                faces = []
        # GFPGAN / CodeFormer 仅当本地已存在（避免强制 340MB+ 下载）
        gfp = _model_local_path("gfpgan") if _model_exists("gfpgan") else None
        cf = _model_local_path("codeformer") if _model_exists("codeformer") else None

        if not faces:
            self.progress.emit("未检测到人脸，整图增强…")
            out_rgb = self._enhance_region(rgb)
            out = np.dstack([out_rgb, alpha]) if alpha is not None else out_rgb
            return out, "整图增强（未检测到人脸）"

        used_gfp = False
        used_cf = False
        for i, f in enumerate(faces):
            self.progress.emit("修复第 {}/{} 张人脸…".format(i + 1, len(faces)))
            x, y, fw, fh = [int(v) for v in f[:4]]
            mx, my = int(fw * 0.35), int(fh * 0.45)
            x0, y0 = max(0, x - mx), max(0, y - my)
            x1, y1 = min(w, x + fw + mx), min(h, y + fh + my)
            if x1 <= x0 or y1 <= y0:
                continue
            region = rgb[y0:y1, x0:x1].copy()
            enh = None
            # CodeFormer 优先（效果优于 GFPGAN）
            if cf:
                try:
                    g = self._run_codeformer(region, cf, self.fidelity)
                    enh = cv2.resize(g, (x1 - x0, y1 - y0), interpolation=cv2.INTER_LINEAR)
                    used_cf = True
                except Exception:
                    enh = None
            if enh is None and gfp:
                try:
                    g = self._run_gfpgan(region, gfp)
                    enh = cv2.resize(g, (x1 - x0, y1 - y0), interpolation=cv2.INTER_LINEAR)
                    used_gfp = True
                except Exception:
                    enh = None
            if enh is None:
                enh = self._enhance_region(region)
            # 椭圆羽化融合，避免边缘接缝
            mask = np.zeros((y1 - y0, x1 - x0), np.float32)
            cv2.ellipse(mask, ((x1 - x0) // 2, (y1 - y0) // 2),
                        (max(1, (x1 - x0) // 2 - 2), max(1, (y1 - y0) // 2 - 2)),
                        0, 0, 360, 1, -1)
            mask = cv2.GaussianBlur(mask, (0, 0), max(3, (x1 - x0) // 12))[..., None]
            rgb[y0:y1, x0:x1] = (enh.astype(np.float32) * mask +
                                 region.astype(np.float32) * (1 - mask)).astype(np.uint8)
        out = np.dstack([rgb, alpha]) if alpha is not None else rgb
        if used_cf:
            eng = "CodeFormer (ONNX) × {} 张".format(len(faces))
        elif used_gfp:
            eng = "GFPGAN (ONNX) × {} 张".format(len(faces))
        else:
            eng = "人脸增强 磨皮+锐化 × {} 张".format(len(faces))
        return out, eng

    @staticmethod
    def _enhance_region(rgb):
        """经典人脸增强：保边磨皮 + USM 锐化 + 自然混合。"""
        import numpy as np
        import cv2
        smooth = cv2.bilateralFilter(rgb, 9, 60, 60)
        blur = cv2.GaussianBlur(smooth, (0, 0), 2.0)
        sharp = cv2.addWeighted(smooth, 1.5, blur, -0.5, 0)
        out = cv2.addWeighted(sharp, 0.7, rgb, 0.3, 0)
        return np.clip(out, 0, 255).astype(np.uint8)

    def _run_gfpgan(self, face_rgb, model_path):
        """GFPGAN v1.4 ONNX：512×512, RGB, [-1,1], NCHW。返回 512×512 RGB。"""
        import numpy as np
        import cv2
        import onnxruntime as ort
        sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        iname = sess.get_inputs()[0].name
        face = cv2.resize(face_rgb, (512, 512), interpolation=cv2.INTER_LINEAR)
        x = face.astype(np.float32) / 255.0
        x = (x - 0.5) / 0.5
        x = np.transpose(x, (2, 0, 1))[None, ...]
        out = sess.run(None, {iname: x})[0][0]
        out = np.transpose(out, (1, 2, 0))
        out = np.clip(out * 0.5 + 0.5, 0, 1) * 255.0
        return out.astype(np.uint8)

    # ---------- BiRefNet 去背 ----------
    def _birefnet(self):
        """BiRefNet 通用去背：输出带透明通道的 RGBA（保留原 RGB，alpha=matte）。"""
        import numpy as np
        import cv2
        rgba = self.arr
        rgb = rgba[:, :, :3].copy()
        h, w = rgb.shape[:2]
        self.progress.emit("准备 BiRefNet 模型（首次约 900MB，请耐心等待）…")
        model = _download_model(
            "birefnet",
            lambda g, t: self.progress.emit(
                "下载 BiRefNet {}%".format(g * 100 // t) if t > 0
                else "下载 BiRefNet {:.0f} MB…".format(g / 1048576)))
        if not model:
            raise RuntimeError(
                "BiRefNet 模型下载失败（请检查网络，或手动放入 "
                "~/.cep_models/birefnet-general.onnx）")
        self.progress.emit("BiRefNet 推理中…")
        matte = self._run_birefnet(rgb, model)  # (H,W) 0-255
        new_a = matte.astype(np.uint8)
        out = np.dstack([rgb, new_a])
        return out, "BiRefNet (ONNX)"

    @staticmethod
    def _run_onnx_worker(model_path, feed_dict):
        """在干净子进程里跑 onnxruntime 推理；主进程已加载 PyQt6/cv2 时用。
        feed_dict 的 key 仅用于区分数组，worker 会按形状匹配到模型输入。"""
        import os
        import sys
        import tempfile
        import subprocess
        import numpy as np
        fd_in = tempfile.NamedTemporaryFile(suffix=".npz", delete=False)
        fd_out = tempfile.NamedTemporaryFile(suffix=".npy", delete=False)
        try:
            np.savez(fd_in, **feed_dict)
            fd_in.close()
            if getattr(sys, "frozen", False):
                # EXE 模式：sys.executable 就是打包后的可执行文件，
                # bootloader 会把命令行参数传给 main.py，因此可直接 --ai-worker。
                cmd = [sys.executable, "--ai-worker", model_path, fd_in.name, fd_out.name]
            else:
                # 源码模式：必须显式指定 main.py 脚本，否则 python.exe 会把
                # --ai-worker 当成 python 自身的不合法选项（Windows Store 版尤甚）。
                main_py = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "main.py")
                cmd = [sys.executable, main_py, "--ai-worker", model_path, fd_in.name, fd_out.name]
            proc = subprocess.run(
                cmd, capture_output=True, timeout=600,
                creationflags=(subprocess.CREATE_NO_WINDOW
                               if hasattr(subprocess, "CREATE_NO_WINDOW") else 0))
            err = proc.stderr.decode("utf-8", "replace") if proc.stderr else ""
            out = proc.stdout.decode("utf-8", "replace") if proc.stdout else ""
            if proc.returncode != 0 or "AI_OK" not in out:
                detail = (err or out or "unknown").strip().replace("\n", " ")[:1500]
                raise RuntimeError("AI worker 子进程推理失败：{}".format(detail))
            return np.load(fd_out.name)
        finally:
            try:
                os.unlink(fd_in.name)
            except Exception:
                pass
            try:
                os.unlink(fd_out.name)
            except Exception:
                pass

    @staticmethod
    def _run_birefnet(rgb, model_path):
        """BiRefNet ONNX（rembg 官方转换）：输入 1024×1024, ImageNet 归一化,
        输出 matte(0-1)。等比缩放+填充避免拉伸畸变。"""
        import numpy as np
        import cv2
        import sys
        SIZE = 1024
        h, w = rgb.shape[:2]
        scale = min(SIZE / w, SIZE / h)
        nw, nh = int(round(w * scale)), int(round(h * scale))
        resized = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_LINEAR)
        canvas = np.zeros((SIZE, SIZE, 3), np.uint8)
        yy0, xx0 = (SIZE - nh) // 2, (SIZE - nw) // 2
        canvas[yy0:yy0 + nh, xx0:xx0 + nw] = resized
        x = canvas.astype(np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406], np.float32)
        std = np.array([0.229, 0.224, 0.225], np.float32)
        x = (x - mean) / std
        x = np.transpose(x, (2, 0, 1))[None, ...]
        # 始终走干净子进程推理：主进程已加载 PyQt6/cv2 时，进程内 import onnxruntime
        # 会触发原生 DLL (onnxruntime_pybind11_state) 初始化失败，源码模式同样命中。
        out = _AIEnhanceWorker._run_onnx_worker(model_path, {"image": x})
        m = out[0] if out.ndim == 4 else out
        if m.ndim == 3:
            m = m[0]
        # 容错：部分 BiRefNet 导出输出形状为 (H,W,1)/(1,H,W,1) 等，统一压成 (H,W) 单通道
        m = np.squeeze(np.asarray(m))
        if m.ndim == 3:
            m = m[0]
        if m.ndim != 2:
            raise RuntimeError(
                "BiRefNet 模型输出形状异常（{}），无法解析为单张 matte，"
                "请确认模型为 rembg 官方 BiRefNet-general ONNX。".format(out.shape))
        m = 1.0 / (1.0 + np.exp(-m))  # sigmoid → 0-1
        m = (m * 255).astype(np.uint8)
        # 仅当尺寸匹配才裁剪填充区，否则直接缩放，避免越界
        if m.shape[0] >= yy0 + nh and m.shape[1] >= xx0 + nw:
            m = m[yy0:yy0 + nh, xx0:xx0 + nw]
        m = cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)
        return m

    @staticmethod
    def _run_codeformer(face_rgb, model_path, fidelity=0.5):
        """CodeFormer ONNX：输入 512×512 RGB, [-1,1], NCHW；输出同规格 [-1,1]。
        latent=zeros(1,512)，fidelity 控制保真/质量权衡（0=更清晰,1=更保真）。"""
        import numpy as np
        import cv2
        import sys
        SIZE = 512
        face = cv2.resize(face_rgb, (SIZE, SIZE), interpolation=cv2.INTER_LINEAR)
        x = face.astype(np.float32) / 255.0
        x = (x - 0.5) / 0.5
        x = np.transpose(x, (2, 0, 1))[None, ...]
        # 始终走干净子进程推理（避免主进程 PyQt6/cv2 加载后 onnxruntime DLL 初始化失败）
        o = _AIEnhanceWorker._run_onnx_worker(model_path, {
            "image": x,
            "latent": np.zeros((1, 512), np.float32),
            "w": np.array([[float(fidelity)]], np.float32),
        })
        if o.ndim == 4:
            o = o[0]
        if o.shape[0] == 3:
            o = np.transpose(o, (1, 2, 0))
        o = np.clip(o * 0.5 + 0.5, 0, 1) * 255.0
        return o.astype(np.uint8)


# ───────────────────────────── 主编辑器 ─────────────────────────────
# ───────────────────────────── 画布内联文字编辑（画布渲染 + IME 直采） ─────────────────────────────
# 参考视频区字幕输入方式：不使用 QPlainTextEdit 覆盖层，而是直接在画布上渲染文字 +
# 闪烁光标，由 ImageEditorWidget.keyPressEvent / inputMethodEvent 直接捕获键入与
# 中文输入法组合文本，从根源上避免「子控件抢焦点导致无法输入」的问题。


class ImageEditorWidget(LayerMarqueeOperations, ImageRecoveryOperations, ImageHistoryOperations, ImageInteractionRenderingOperations, TextInteractionOperations, ImageSelectionOperations, ImageLayoutOperations, ImagePaintingOperations, ImageCommandsOperations, ImageFileOperations, ImageFolderOperations, WorkbenchOperations, QWidget):
    add_layer_to_media_requested = pyqtSignal(str)  # 图层导出 PNG 路径 → 媒体库

    def __init__(self):
        super().__init__()
        # 文字编辑态需要本控件能接收 IME 事件（聚焦时）
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.project = ImageProject(1080, 1080)
        self.active = None
        self.tool = Tool.MOVE
        self.fg = QColor(255, 255, 255)   # 前景色（笔刷绘制色）
        self.bg = QColor(0, 0, 0)          # 背景色（橡皮擦为透明，BG 用于交换色等）
        self.brush_size = 20
        self.brush_opacity = 100
        self.brush_hardness = 100
        self.brush_smoothing = 0
        self.pressure_size = True
        self.pressure_opacity = True
        self._tablet_pressure = 1.
        self.clone_aligned = True
        self.clone_sample_all = False
        self._crop_rect = None
        self.tolerance = 32
        self.wand_contiguous = True
        self.wand_sample_all = False
        self.feather = 0
        self.blur_radius = 5
        # 形状绘制默认样式（选项条控制，参考 PS 形状工具选项栏）
        self.shape_fill_on = True
        self.shape_fill_color = QColor("#3d8ef8")
        self.shape_stroke_on = False
        self.shape_stroke_color = QColor("#000000")
        self.shape_stroke_w = 2
        self.shape_line_style = int(Qt.PenStyle.SolidLine.value)
        self.shape_line_cap = int(Qt.PenCapStyle.SquareCap.value)
        self.show_pixel_grid = True
        self.shape_gradient_on = False
        self.shape_grad_from = QColor("#3d8ef8")
        self.shape_grad_to = QColor("#00eaff")
        self._sel_combine = "new"
        self.selection_mode = 'new'
        # 图层蒙版编辑模式：开启后画笔/橡皮擦写入当前图层的蒙版（黑=隐藏，白=显示）
        self._mask_edit = False
        # 线性历史记录（PS 风格）：支持点击任意历史状态跳转
        self._history = []           # [{"name": str, "snapshot": dict}]
        self._history_idx = -1       # 当前所在历史位置（-1 = 无历史）
        self.max_undo = 30
        self._pending_property_history = None
        self._committing_property_history = False
        self._property_history_timer = QTimer(self)
        self._property_history_timer.setSingleShot(True)
        self._property_history_timer.setInterval(280)
        self._property_history_timer.timeout.connect(self._commit_property_history)
        # 空格抓手（PS 风格）：按住空格临时切为平移画布模式
        self._space_held = False
        self._space_prev_tool = Tool.MOVE
        # P5 标尺 / 智能参考线 / 吸附
        self.show_rulers = True        # 显示标尺（Ctrl+R 切换）
        self.smart_guides_on = True    # 拖动图层时智能吸附 + 洋红参考线
        self.snap_on = True            # 网格/参考线吸附总开关
        self._smart_guides = []        # 当前显示的智能参考线 [('v', x), ('h', y)] 文档坐标
        self._guide_preview = None     # 从标尺拖出参考线时的预览 ('h', y) / ('v', x)
        # P4 自由变换（Ctrl+T）：进入变换态，Enter 确认 / Esc 取消
        self._ft_active = False
        self._ft_saved = None      # (layer, x, y, scale, rotation, skew_x, skew_y)
        self._ft_bar = None        # 画布顶部悬浮 确认/取消 工具条
        # 图层多选（仿 PS）：selected=选中集合，active=主图层(驱动手柄/属性)，_anchor=Shift 连续选起点
        self.selected = []
        self._anchor = None
        self.selection = None  # numpy bool (h,w) 画布空间
        self.sel_alpha = None   # numpy float (h,w) 0..1 软权重(羽化)
        self._sel_base = None   # 羽化前的原始二值选区（实时调羽化滑块时从此重算）
        # 选区蚂蚁线动画（marching ants）
        self._sel_phase = 0
        self._sel_preview_base = None   # 拖拽多选时冻结的已提交选区底图（避免回读 sel_item 累积残影）
        self._sel_timer = QTimer(self)
        self._sel_timer.setInterval(150)
        self._sel_timer.timeout.connect(self._tick_sel_anim)
        self._show_handles = False   # 点图层才显示变换手柄
        self.active_artboard = None   # 当前激活画板（None = 传统单画布模式）
        self.host = None              # 所属多文档容器（ImageEditorContainer），单文档时为 None
        self.doc_name = "未命名"       # 文档名（显示在容器标签上，用作导出默认名）
        # 首次显示时自动适应居中：启动时控件尚未布局，需延迟到 viewport 真正就绪后再 fit
        self._fit_pending = True
        # 变换交互
        self._move_orig = None
        self._move_olds = None
        self._move_pre_snap = None    # 拖动前的 pre-state 快照（供撤销精确回退）
        # 画布内联文字编辑（画布渲染 + IME 直采，无 QPlainTextEdit 覆盖层，
        # 参考视频区字幕输入方式：直接捕获 keyPressEvent / inputMethodEvent）
        self._text_editing = False     # 是否处于文字编辑态
        self._text_edit_layer = None  # 正在编辑的图层
        self._text_edit_was_new = False
        self._text_edit_added = False  # 新建文字层是否已真正加入工程（PS 式延迟建层）
        self._text_edit_orig = ""
        self._edit_flat = ""           # 编辑缓冲（已确定文本，不含 IME 预编辑）
        self._edit_cursor = 0          # 光标在 _edit_flat 中的位置
        self._edit_blink = True        # 光标闪烁开关
        self._edit_blink_timer = QTimer(self)
        self._edit_blink_timer.setInterval(530)
        self._edit_blink_timer.timeout.connect(self._on_edit_blink)
        self._ime_active = False       # IME 正在组合（拼音等）
        self._ime_preedit = ""         # IME 预编辑文本（仅显示）
        self._ime_compose_start = 0    # IME 组合起始位置
        self._edit_cursor_rect = QRectF(0, 0, 2, 12)  # IME 查询用光标矩形（画布像素）
        # AI 抠图运行模式：False=当前进程内线程运行；True=子进程运行（当前进程无法 import rembg 时 fallback）
        self._rembg_subproc = False
        self._rembg_python = None
        self._rembg_ensured = None   # True/False 缓存 _ensure_rembg 结果，避免重复弹窗
        # AI 增强（超分 / 人脸修复）异步任务状态
        self._ai_enh_busy = False
        # 通用 AI 前后对比：保存 AI 操作前的整张画布快照，支持所有 AI 修改。
        self._compare_before_image = None
        self._compare_pending_image = None
        self._compare_pending_label = ""
        self._compare_label = "AI 处理"
        self._compare_active = False
        self._compare_btn = None
        # 高清放大超分模型偏好：auto（按内容自动）/ general（写实通用）/ anime（插画锐利）
        self._upscale_model_pref = "auto"
        # 图层列表双击检测（处理文字图层双击编辑，因 child widget 吃掉 QListWidget 的 itemDoubleClicked）
        self._layer_dbl_last_time = 0.0
        self._layer_dbl_last_layer = None
        self._drag_layer = None          # 图层拖拽排序：当前拖动的图层
        self.installEventFilter(self)  # 全局 Delete/Backspace 删除活跃层（输入框内除外）
        self._init_ui()
        self.new_project(1080, 1080)
        self.init_recovery()

    # ═══════ UI ═══════
    def _init_ui(self):
        # 全局 tooltip 样式：深色底白字
        self.setStyleSheet("""
            QLabel, QCheckBox { color:#c2cad6; }
            QDialog { background:#252830; }
            QDialog QPushButton {
                background:#333c49; color:#edf2f8; border:1px solid #4c596d;
                border-radius:4px; padding:5px 14px;
            }
            QDialog QPushButton:hover { background:#40536d; }
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {
                background:#252830; color:#e1e7ef; border:1px solid #3c424d;
                border-radius:3px; padding:3px;
            }
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color:#579fff; }
            QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled { color:#858e9e; }
            QComboBox QAbstractItemView { background:#252830;color:#e1e7ef;selection-background-color:#397ac7; }
            QTabWidget, QTabBar { background:#1b1b1e; }
            QTableWidget { background:#1b1f26; color:#d5ddea; gridline-color:#39414e; }
            QHeaderView::section { background:#303744; color:#e1e7ef; padding:5px; border:1px solid #414b5c; }
            QToolTip { background:#2a2a30;color:#ffffff;border:1px solid #444;
                       padding:4px 8px;border-radius:3px;font-size:12px; }
        """)

        self.view = CanvasView(self)
        self.view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # 确保 viewport 能接受焦点，文字编辑态下 CanvasView 可接收键盘/IME 事件
        self.view.viewport().setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # 旋转实时角度 HUD（覆盖在画布顶部居中）
        self._rot_hud = QLabel(self.view)
        self._rot_hud.setStyleSheet(
            "QLabel{background:rgba(28,28,32,220);color:#00eaff;border:1px solid #00eaff;"
            "border-radius:3px;padding:3px 10px;font-size:12px;font-weight:bold;}")
        self._rot_hud.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._rot_hud.hide()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ────── 顶部菜单栏：文件 / 编辑 / 选择 / 图层 ──────
        top_bar = self._build_top_menu_bar()
        search_button = workbench_button("搜索操作", None, "搜索菜单功能 (Ctrl+K)，回车执行，Esc 关闭",
                                         accent=IMAGE_ACCENT, quiet=True)
        search_button.clicked.connect(self._search_commands)
        top_bar.layout().addWidget(search_button)
        outer.addWidget(workbench_horizontal_strip(top_bar))
        search_action = QAction(self)
        search_action.setShortcut("Ctrl+K")
        search_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        search_action.triggered.connect(self._search_commands)
        self.addAction(search_action)

        # ────── 选项条（随工具变化）──────
        self._recovery_notice = QPushButton()
        self._recovery_notice.setStyleSheet('background:#514428;color:#ffe1a0;padding:7px;')
        self._recovery_notice.clicked.connect(self.show_recovery_dialog)
        self._recovery_notice.hide()
        outer.addWidget(self._recovery_notice)
        opt_bar = self._build_option_bar()
        outer.addWidget(opt_bar)

        # ────── 主体：左侧工具栏 | 画布 | 右侧（属性上 / 图层下）──────
        main_split = QSplitter(Qt.Orientation.Horizontal)
        self.main_split = main_split
        main_split.setHandleWidth(6)
        main_split.setStyleSheet('QSplitter::handle{background:#252527;}'
                                'QSplitter::handle:hover{background:#49494e;}')
        main_split.setOpaqueResize(True)
        main_split.setChildrenCollapsible(False)

        # 左侧：垂直工具栏
        left_toolbar = self._build_left_toolbar()
        main_split.addWidget(left_toolbar)
        self._update_option_bar()

        # 中间：画布
        canvas_wrap = QFrame()
        canvas_wrap.setStyleSheet("background:#161618;")
        cl = QVBoxLayout(canvas_wrap)
        cl.setContentsMargins(0, 0, 0, 0); cl.setSpacing(0)
        cl.addWidget(self.view, 1)
        main_split.addWidget(canvas_wrap)

        # 右侧：垂直分割（属性面板在上，图层面板在下）
        right_split = QSplitter(Qt.Orientation.Vertical)
        right_split.setStyleSheet("QSplitter::handle{background:#2a2a2a;height:4px;}")
        right_split.setOpaqueResize(True)
        right_split.setChildrenCollapsible(False)

        right_panel = self._build_right_panel()
        property_scroll = QScrollArea()
        property_scroll.setWidgetResizable(True)
        property_scroll.setFrameShape(QFrame.Shape.NoFrame)
        property_scroll.setWidget(right_panel)
        right_split.addWidget(property_scroll)

        # 图层面板 + 历史记录面板（PS 风格 Tab 切换）
        self._layer_panel = self._build_layers_panel()
        self._history_panel = self._build_history_panel()
        self._layer_history_tabs = QTabWidget()
        self._layer_history_tabs.setStyleSheet(workbench_tabs_style(IMAGE_ACCENT))
        self._layer_history_tabs.addTab(self._layer_panel, "图层")
        self._layer_history_tabs.addTab(self._history_panel, "历史记录")
        right_split.addWidget(self._layer_history_tabs)

        right_split.setSizes([380, 260])
        main_split.addWidget(right_split)
        self._right_split = right_split
        self._focus_panels = [left_toolbar, right_split, opt_bar]

        # AI 生图已统一迁移到 AI 制片画布；图片工作台只保留像素级编辑。
        self._ai_image_panel = None
        main_split.setStretchFactor(0, 0)
        main_split.setStretchFactor(1, 1)
        main_split.setStretchFactor(2, 0)
        main_split.setSizes([164, 720, 280])
        self._tool_rail.attach_splitter(main_split)
        outer.addWidget(main_split, 1)
        outer.addWidget(self._build_gif_controls())

        # ────── 底部状态栏 ──────
        status = QWidget()
        status.setFixedHeight(24)
        status.setStyleSheet("background:#1e1e22;border-top:1px solid #2c2c2c;")
        sl = QHBoxLayout(status)
        sl.setContentsMargins(12, 2, 12, 2); sl.setSpacing(16)
        self.status_coord = QLabel(""); self.status_coord.setStyleSheet("color:#777;font-size:11px;")
        self.status_tool = QLabel("移动"); self.status_tool.setStyleSheet("color:#aaa;font-size:11px;")
        sl.addWidget(self.status_tool)
        sl.addStretch()
        self.status_size = QLabel("1080 × 1080"); self.status_size.setStyleSheet("color:#777;font-size:11px;")
        sl.addWidget(self.status_size)
        sl.addWidget(self.status_coord)
        outer.addWidget(status)

        self.set_tool(Tool.MOVE)
        self.install_tool_shortcuts()

    def _search_commands(self):
        """Discover existing menu commands without duplicating their behavior."""
        dialog = QDialog(self)
        dialog.setWindowTitle("搜索操作")
        dialog.resize(560, 420)
        layout = QVBoxLayout(dialog)
        results = QListWidget()
        query = CommandSearchInput(results)
        query.setPlaceholderText("输入操作名称，例如：蒙版、导出、曲线…")
        layout.addWidget(query)
        layout.addWidget(results)
        commands = []
        seen = set()
        for menu in self.findChildren(QMenu):
            for action in menu.actions():
                if action.isSeparator() or action.menu() or not action.text() or id(action) in seen:
                    continue
                seen.add(id(action))
                commands.append((menu.title() or (menu.parent().text() if isinstance(menu.parent(), QPushButton) else "操作"), action))
        for tool, button in self._tool_buttons.items():
            action = QAction(button.text(), dialog)
            action.triggered.connect(lambda _=False, t=tool: self.set_tool(t))
            commands.append(("工具", action))

        def refresh():
            results.clear()
            matches = []
            for category, action in commands:
                name = category + " › " + action.text().replace('&', '')
                score = command_match_score(query.text(), name)
                if score is not None:
                    matches.append((score, name, action))
            for _, name, action in sorted(matches, key=lambda match: -match[0]):
                shortcut = action.shortcut().toString()
                item = QListWidgetItem(name + ("    " + shortcut if shortcut else ""))
                item.setData(Qt.ItemDataRole.UserRole, action)
                if not action.isEnabled():
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                results.addItem(item)
            for row in range(results.count()):
                if results.item(row).flags() & Qt.ItemFlag.ItemIsEnabled:
                    results.setCurrentRow(row)
                    break

        def execute(*_):
            item = results.currentItem()
            if item is None:
                return
            action = item.data(Qt.ItemDataRole.UserRole)
            if action.isEnabled():
                dialog.accept()
                action.trigger()

        query.textChanged.connect(refresh)
        query.returnPressed.connect(execute)
        results.itemActivated.connect(execute)
        refresh()
        query.setFocus()
        dialog.exec()

    def _section(self, title):
        w = QLabel(title)
        w.setStyleSheet(f"color:{IMAGE_ACCENT};font-weight:600;font-size:12px;"
                        "padding:7px 0 4px;border-bottom:1px solid #36313f;")
        return w

    def _sep_v(self):
        f = QFrame(); f.setFrameShape(QFrame.Shape.VLine); f.setStyleSheet("color:#383838;"); f.setFixedWidth(1)
        return f

    # ═══════ 布局组件（PS 风格：左工具栏 / 右上属性 / 右下图层）═══════
    def _build_top_menu_bar(self):
        """顶部菜单栏（PS 风格）：文件 / 编辑 / 选择 / 导出 下拉菜单"""
        bar = QWidget()
        bar.setObjectName("imageMenuBar")
        bar.setMinimumHeight(40)
        bar.setStyleSheet("QWidget#imageMenuBar{background:#1c1e23;border-bottom:1px solid #30323b;}")
        l = QHBoxLayout(bar)
        l.setContentsMargins(8, 2, 8, 2); l.setSpacing(2)

        # ── 文件 ──
        btn_file = self._menu_btn("文件")
        menu_file = QMenu(btn_file)
        menu_file.setStyleSheet(self._menu_style())
        self._add_menu_action(menu_file, "新建画布", self._on_new_canvas, "Ctrl+N")
        menu_file.addSeparator()
        self._add_menu_action(menu_file, "导入图片...", self.add_image_dialog, "Ctrl+I")
        self._add_menu_action(menu_file, '导入报告', self.show_import_report)
        self._add_menu_action(menu_file, '打开 Compositor 工程…', self.open_comp)
        self._add_menu_action(menu_file, '载入外部更新（当前状态可撤销）', self.reload_comp_from_disk)
        self._add_menu_action(menu_file, "导入 PSD...", self.import_psd, "")
        menu_file.addSeparator()
        self._add_menu_action(menu_file, "保存工程", self.save_project, "Ctrl+S")
        self._add_menu_action(menu_file, "打开工程...", self.open_project, "Ctrl+O")
        self._add_menu_action(menu_file, '恢复自动备份…', self.show_recovery_dialog)
        btn_file.setMenu(menu_file)
        l.addWidget(btn_file)

        # ── 编辑 ──
        btn_edit = self._menu_btn("编辑")
        menu_edit = QMenu(btn_edit)
        menu_edit.setStyleSheet(self._menu_style())
        self._add_menu_action(menu_edit, "撤销", self.undo, "Ctrl+Z")
        self._add_menu_action(menu_edit, "重做", self.redo, "Ctrl+Y")
        menu_edit.addSeparator()
        self._add_menu_action(menu_edit, "复制选区 / 图层", self.copy_selection, "Ctrl+C")
        self._add_menu_action(menu_edit, "复制合成图", self.copy_merged, "Ctrl+Shift+C")
        self._add_menu_action(menu_edit, "粘贴", self.paste_image, "Ctrl+V")
        menu_edit.addSeparator()
        self._add_menu_action(menu_edit, "自由变换", self._start_free_transform, "Ctrl+T")
        self._add_menu_action(menu_edit, "精确变换…", self.exact_transform)
        self._add_menu_action(menu_edit, "变换复制", self._transform_copy, "Ctrl+Alt+T")
        self._add_menu_action(menu_edit, '键盘快捷键…', self.edit_shortcuts)
        btn_edit.setMenu(menu_edit)
        l.addWidget(btn_edit)

        # ── 图层 ──
        btn_layer = self._menu_btn("图层")
        menu_layer = QMenu(btn_layer)
        menu_layer.setStyleSheet(self._menu_style())
        self._add_menu_action(menu_layer, '图层效果…', lambda:edit_layer_effects(self))
        m_adj = menu_layer.addMenu("新建调整图层")
        m_adj.setStyleSheet(self._menu_style())
        for kind, (label, _) in ADJUSTMENTS.items():
            self._add_menu_action(m_adj, label + "…", lambda _=False, k=kind: self._add_adjust_layer(k))
        self._add_menu_action(m_adj, "亮度/对比度...",
                              lambda: self._add_adjust_layer("brightness_contrast"))
        self._add_menu_action(m_adj, "色相/饱和度...",
                              lambda: self._add_adjust_layer("hsl"))
        self._add_menu_action(m_adj, "色阶...",
                              lambda: self._add_adjust_layer("levels"))
        self._add_menu_action(m_adj, "曲线...",
                              lambda: self._add_adjust_layer("curves"))
        self._add_menu_action(m_adj, "白平衡...",
                              lambda: self._add_adjust_layer("white_balance"))
        self._add_menu_action(m_adj, "色彩平衡...",
                              lambda: self._add_adjust_layer("color_balance"))
        menu_layer.addSeparator()
        masks = menu_layer.addMenu("蒙版操作")
        self._add_menu_action(masks,'切换蒙版联动 / 独立',self.toggle_mask_link)
        self._add_menu_action(masks, "柔化蒙版边缘…", self.blur_mask)
        for label, operation in (("从选区创建蒙版", "selection"), ("反转蒙版", "invert"),
                                 ("蒙版全部显示", "white"), ("蒙版全部隐藏", "black")):
            self._add_menu_action(masks, label, lambda _=False, op=operation: self.mask_operation(op))
        self._add_menu_action(menu_layer, "载入图层透明度为选区", self.load_layer_selection)
        self._add_menu_action(menu_layer, "载入蒙版为选区", lambda: self.load_layer_selection(mask=True))
        menu_layer.addSeparator()
        self._add_menu_action(menu_layer, "图层编组", self._group_selected, "Ctrl+G")
        self._add_menu_action(menu_layer, "取消编组", self._ungroup_selected, "Ctrl+⇧+G")
        menu_layer.addSeparator()
        self._add_menu_action(menu_layer, "转换为智能对象", self._convert_to_smart_object, "")
        menu_layer.addSeparator()
        m_align = menu_layer.addMenu("对齐")
        m_align.setStyleSheet(self._menu_style())
        for label, mode in (("左对齐", "left"), ("水平居中", "hcenter"), ("右对齐", "right"),
                            ("顶对齐", "top"), ("垂直居中", "vcenter"), ("底对齐", "bottom")):
            self._add_menu_action(m_align, label,
                                  lambda _=False, m=mode: self._align_layers(m))
        m_dist = menu_layer.addMenu("分布")
        m_dist.setStyleSheet(self._menu_style())
        self._add_menu_action(m_dist, "水平均分",
                              lambda: self._distribute_layers("h"))
        self._add_menu_action(m_dist, "垂直均分",
                              lambda: self._distribute_layers("v"))
        btn_layer.setMenu(menu_layer)
        l.addWidget(btn_layer)

        # ── 图像 ──
        btn_image = self._menu_btn("图像")
        menu_image = QMenu(btn_image)
        menu_image.setStyleSheet(self._menu_style())
        self._add_menu_action(menu_image, '缩放图像尺寸（保留源像素）…', self.resize_image_dialog)
        self._add_menu_action(menu_image, '按颜色或透明度裁切边缘…', self.trim_edges_dialog)
        self._add_menu_action(menu_image, "裁切透明边缘（保留图层）", self.trim_transparent)
        m_rot_c = menu_image.addMenu("旋转画布")
        m_rot_c.setStyleSheet(self._menu_style())
        self._add_menu_action(m_rot_c, "顺时针 90°",
                              lambda: self._rotate_canvas(90, swap=True))
        self._add_menu_action(m_rot_c, "逆时针 90°",
                              lambda: self._rotate_canvas(-90, swap=True))
        self._add_menu_action(m_rot_c, "旋转 180°",
                              lambda: self._rotate_canvas(180, swap=False))
        self._add_menu_action(m_rot_c, "按角度旋转…",
                              lambda: self._rotate_canvas_angle())
        m_rot_l = menu_image.addMenu("旋转图层")
        m_rot_l.setStyleSheet(self._menu_style())
        self._add_menu_action(m_rot_l, "顺时针 90°",
                              lambda: self._rotate_active_layer_90(ccw=False))
        self._add_menu_action(m_rot_l, "逆时针 90°",
                              lambda: self._rotate_active_layer_90(ccw=True))
        self._add_menu_action(m_rot_l, "旋转 180°",
                              lambda: self._rotate_active_layer_180())
        btn_image.setMenu(menu_image)
        l.addWidget(btn_image)

        # ── 滤镜 ──
        btn_filter = self._menu_btn("滤镜")
        menu_filter = QMenu(btn_filter)
        menu_filter.setStyleSheet(self._menu_style())
        live_filters = menu_filter.addMenu("新建无损滤镜层")
        for kind, (label, _) in self.FILTERS.items():
            self._add_menu_action(live_filters, label + "…", lambda _=False, k=kind: self.add_filter_layer(k))
        self._add_menu_action(menu_filter, "再次应用上次滤镜", self.repeat_filter, "Ctrl+Alt+F")
        for _k in ("gaussian_blur", "motion_blur", "sharpen", "usm"):
            self._add_menu_action(menu_filter, self.FILTERS[_k][0] + "...",
                                  lambda _=False, k=_k: self._run_filter(k))
        menu_filter.addSeparator()
        for _k in ("noise", "denoise", "mosaic", "posterize"):
            self._add_menu_action(menu_filter, self.FILTERS[_k][0] + "...",
                                  lambda _=False, k=_k: self._run_filter(k))
        menu_filter.addSeparator()
        for _k in ("grayscale", "invert"):
            self._add_menu_action(menu_filter, self.FILTERS[_k][0],
                                  lambda _=False, k=_k: self._run_filter(k))
        btn_filter.setMenu(menu_filter)
        l.addWidget(btn_filter)

        # ── 选择 ──
        btn_sel = self._menu_btn("选择")
        menu_sel = QMenu(btn_sel)
        menu_sel.setStyleSheet(self._menu_style())
        self._add_menu_action(menu_sel, "全选画布", self.select_canvas, "Ctrl+A")
        self._add_menu_action(menu_sel, '选择主体（AI）', self.select_subject)
        self._add_menu_action(menu_sel, "全选图层", self._select_all_layers)
        self._add_menu_action(menu_sel, "扩展选区…", lambda: self.modify_selection(True))
        self._add_menu_action(menu_sel, "收缩选区…", lambda: self.modify_selection(False))
        self._add_menu_action(menu_sel, "取消选区", self._clear_selection, "Ctrl+D")
        self._add_menu_action(menu_sel, "反选", self._invert_selection, "Ctrl+⇧+I")
        menu_sel.addSeparator()
        self._add_menu_action(menu_sel, "删除选区", lambda: self.delete_selection(silent=True), "Delete")
        self._add_menu_action(menu_sel, "填充选区...", self.fill_selection, "Shift+F5")
        self._add_menu_action(menu_sel, "内容识别填充", self._content_aware_fill, "Ctrl+Shift+F5")
        self._add_menu_action(menu_sel, "羽化选区...", self._feather_sel, "")
        btn_sel.setMenu(menu_sel)
        l.addWidget(btn_sel)

        # ── 导出 ──
        btn_export = self._menu_btn("导出")
        menu_export = QMenu(btn_export)
        menu_export.setStyleSheet(self._menu_style())
        self._add_menu_action(menu_export, '导出 Compositor 工程…', self.export_comp)
        self._add_menu_action(menu_export, '导出动画 GIF…', self.export_gif)
        self._add_menu_action(menu_export, '导出 PDF…', self.export_pdf)
        self._add_menu_action(menu_export, '通道打包 PNG…', self.export_channel_pack)
        self._add_menu_action(menu_layer, '拆分 RGBA 通道', self.split_channels)
        self._add_menu_action(menu_export, "导出 PNG...", self.export_png, "")
        self._add_menu_action(menu_export, "导出为…（JPG/WebP/质量）", self.export_as, "Ctrl+Shift+E")
        menu_export.addSeparator()
        self._add_menu_action(menu_export, "导出全部画板...", self.export_all_artboards, "")
        self._add_menu_action(menu_export, "批量导出多尺寸…", self.export_batch_multisize, "")
        self._add_menu_action(menu_export, "模板批处理…", self.open_template_batch)
        btn_export.setMenu(menu_export)
        l.addWidget(btn_export)

        # ── 视图 ──
        btn_view = self._menu_btn("视图")
        menu_view = QMenu(btn_view)
        menu_view.setStyleSheet(self._menu_style())
        navigator_action = menu_view.addAction("显示导航缩略图（300% 起）")
        navigator_action.setCheckable(True)
        navigator_action.setChecked(True)
        navigator_action.toggled.connect(lambda enabled: self._set_navigator_enabled(enabled))
        pixel_grid_action = self._add_menu_action(menu_view,'高倍缩放显示像素网格',self.toggle_pixel_grid)
        pixel_grid_action.setCheckable(True)
        pixel_grid_action.setChecked(self.show_pixel_grid)
        self._add_menu_action(menu_view, "网格间距…", self.configure_grid)
        self._add_menu_action(menu_view, "专注画布 / 恢复面板", self.toggle_focus_canvas)
        a_ruler = QAction("显示标尺", menu_view)
        a_ruler.setCheckable(True); a_ruler.setChecked(self.show_rulers)
        a_ruler.triggered.connect(lambda: self._toggle_rulers(a_ruler))
        menu_view.addAction(a_ruler)
        a_grid = QAction("显示网格", menu_view)
        a_grid.setCheckable(True); a_grid.setChecked(self.project.show_grid)
        a_grid.triggered.connect(lambda: self._toggle_grid_action(a_grid))
        menu_view.addAction(a_grid)
        a_smart = QAction("智能参考线", menu_view)
        a_smart.setCheckable(True); a_smart.setChecked(self.smart_guides_on)
        a_smart.triggered.connect(lambda: self._toggle_smart_guides(a_smart))
        menu_view.addAction(a_smart)
        menu_view.addSeparator()
        menu_view.addAction("清除参考线", self._clear_guides)
        btn_view.setMenu(menu_view)
        l.addWidget(btn_view)
        self._act_rulers = a_ruler
        self._act_grid = a_grid
        self._act_smart = a_smart

        # ── 窗口 ──
        btn_win = self._menu_btn("窗口")
        menu_win = QMenu(btn_win)
        menu_win.setStyleSheet(self._menu_style())
        self._add_menu_action(menu_win, "历史记录", self._show_history_panel, "")
        btn_win.setMenu(menu_win)
        l.addWidget(btn_win)

        l.addSpacing(6)
        l.addWidget(self._sep_v())
        l.addSpacing(4)

        # 画板按钮
        b_art = workbench_button("画板", "layout-grid", "添加画板", accent=IMAGE_ACCENT)
        b_art.clicked.connect(self._open_artboard_dialog)
        l.addWidget(b_art)

        l.addSpacing(4)

        # AI 抠图 —— 顶栏显眼按钮
        ai_btn = workbench_button("AI", "sparkles", "抠图 / 高清放大 / 人脸修复",
                                  accent=IMAGE_ACCENT, primary=True)
        ai_menu = QMenu(ai_btn)
        ai_menu.setStyleSheet(self._menu_style())
        self._add_menu_action(ai_menu, "🤖 AI 抠图（移除背景）", self._ai_remove_bg)
        self._add_menu_action(ai_menu, "🤖 AI 智能抠图（BiRefNet）", self._ai_remove_bg_birefnet)
        ai_menu.addSeparator()
        self._add_menu_action(ai_menu, "🔍 高清放大 2×", lambda: self._ai_upscale(2))
        self._add_menu_action(ai_menu, "🔍 高清放大 4×", lambda: self._ai_upscale(4))
        # 超分模型选择（自动按内容 / 写实通用 / 插画锐利）
        sr_menu = QMenu("🔧 超分模型", ai_btn)
        sr_menu.setStyleSheet(self._menu_style())
        self._sr_actions = {}
        for val, label in (("auto", "自动（按内容）"),
                           ("general", "通用·写实"),
                           ("anime", "插画·锐利")):
            act = sr_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._upscale_model_pref == val)
            act.triggered.connect(
                lambda _checked, v=val: self._set_upscale_model(v))
            self._sr_actions[val] = act
        ai_menu.addMenu(sr_menu)
        ai_menu.addSeparator()
        self._add_menu_action(ai_menu, "✨ AI 人脸修复（CodeFormer/GFPGAN）", self._ai_face_restore)
        ai_menu.addSeparator()
        self._add_menu_action(ai_menu, "📦 模型管理", self._open_model_manager)
        ai_btn.setMenu(ai_menu)
        l.addWidget(ai_btn)

        # 通用 AI 前后对比按钮
        self._compare_btn = workbench_button("前后对比", "contrast",
            "完成 AI 修改后，点击切换查看处理前和当前结果", accent=IMAGE_ACCENT)
        self._compare_btn.setCheckable(True)
        self._compare_btn.setEnabled(False)
        self._compare_btn.toggled.connect(self._on_compare_toggle)
        l.addWidget(self._compare_btn)

        l.addStretch()
        self._zoom_controls = ZoomControls(self.view)
        self.zoom_label = self._zoom_controls.level
        self.b_zoom_out, self.b_zoom_in, self.b_fit = (self._zoom_controls.out_button,
            self._zoom_controls.in_button, self._zoom_controls.fit_button)
        l.addWidget(self._zoom_controls)
        return bar

    def _build_option_bar(self):
        """工具选项条（随工具变化，参考 PS 选项栏）"""
        opt_bar = QWidget()
        opt_bar.setObjectName("imageToolOptions")
        opt_bar.setFixedHeight(40)
        opt_bar.setStyleSheet("QWidget#imageToolOptions{background:#1c1e23;border-bottom:1px solid #30323b;}"
                              + workbench_field_style(IMAGE_ACCENT))
        ol = QHBoxLayout(opt_bar)
        ol.setContentsMargins(10, 2, 10, 2); ol.setSpacing(6)

        self._opt_tool_label = QLabel()
        self._opt_tool_label.setStyleSheet(f"color:{IMAGE_ACCENT};font-size:12px;font-weight:600;padding-right:4px;")
        ol.addWidget(self._opt_tool_label)
        self._opt_hint = QLabel("选择左侧工具以查看对应参数")
        self._opt_hint.setStyleSheet("color:#a8abb8;font-size:12px;")

        # 笔刷
        self._ob_brush_lbl = QLabel("笔刷大小")
        self._ob_brush = self._slider(1, 200, self.brush_size,
                                      self._on_brush_size_slider)
        # 容差（魔棒）
        self._ob_tol_lbl = QLabel("容差")
        self._ob_tol = self._slider(0, 255, self.tolerance,
                                    lambda v: setattr(self, "tolerance", v))
        # 羽化（选区）
        self._ob_feather_lbl = QLabel("羽化")
        self._ob_feather = self._slider(0, 60, self.feather, self._on_feather_slider)
        # 模糊（选区模糊半径）
        self._ob_blur_lbl = QLabel("模糊")
        self._ob_blur = self._slider(1, 40, self.blur_radius,
                                     lambda v: setattr(self, "blur_radius", v))

        # 前景色（笔刷）—— 颜色块按钮
        self.fg_btn = self._color_btn(self.fg, "前景色（笔刷颜色）")
        self.fg_btn.clicked.connect(self._choose_fg)

        # ── 形状工具组：填充开关 + 填充色 / 描边开关 + 粗细 + 描边色（参考 PS）──
        self._sh_fill_chk = QCheckBox("填充")
        self._sh_fill_chk.setChecked(self.shape_fill_on)
        self._sh_fill_chk.toggled.connect(self._set_shape_fill_on)
        self._sh_fill_btn = self._color_btn(self.shape_fill_color, "形状填充色")
        self._sh_fill_btn.clicked.connect(self._choose_shape_fill_color)

        self._sh_stroke_chk = QCheckBox("描边")
        self._sh_stroke_chk.setChecked(self.shape_stroke_on)
        self._sh_stroke_chk.toggled.connect(self._set_shape_stroke_on)
        self._sh_stroke_lbl = QLabel("粗细")
        self._sh_stroke_w = QSpinBox()
        self._sh_stroke_w.setRange(0, 100)
        self._sh_stroke_w.setValue(self.shape_stroke_w)
        self._sh_stroke_w.setFixedWidth(52)
        self._sh_stroke_w.valueChanged.connect(self._set_shape_stroke_w)
        self._sh_stroke_btn = self._color_btn(self.shape_stroke_color, "形状描边色")
        self._sh_stroke_btn.clicked.connect(self._choose_shape_stroke_color)

        # 渐变（形状工具组：与填充/描边并列；开启后覆盖纯色填充）
        self._sh_grad_chk = QCheckBox("渐变")
        self._sh_grad_chk.setChecked(self.shape_gradient_on)
        self._sh_grad_chk.toggled.connect(self._set_shape_gradient_on)
        self._sh_grad_from_btn = self._color_btn(self.shape_grad_from, "渐变起始色")
        self._sh_grad_from_btn.clicked.connect(self._choose_shape_grad_from)
        self._sh_grad_to_btn = self._color_btn(self.shape_grad_to, "渐变结束色")
        self._sh_grad_to_btn.clicked.connect(self._choose_shape_grad_to)

        def _mini_chk(c):
            c.setStyleSheet("QCheckBox{color:#ccc;font-size:12px;spacing:3px;}")
            return c

        for c in (self._sh_fill_chk, self._sh_stroke_chk, self._sh_grad_chk):
            _mini_chk(c)

        # 加入布局（可见性由 _update_option_bar 控制）
        self._selection_controls = self.build_selection_mode_controls()
        ol.addWidget(self._selection_controls)
        self._wand_controls = QWidget()
        wand_layout = QHBoxLayout(self._wand_controls)
        wand_layout.setContentsMargins(0, 0, 0, 0)
        for label, key in (('连续', 'wand_contiguous'), ('所有图层', 'wand_sample_all')):
            check = QCheckBox(label)
            check.setChecked(getattr(self, key))
            check.toggled.connect(lambda checked, name=key: setattr(self, name, checked))
            check.setToolTip('只选相连区域；关闭后选择整张图中相似的颜色' if key == 'wand_contiguous'
                             else '按所有可见图层的合成颜色取样；关闭后只取样当前图片图层')
            wand_layout.addWidget(check)
        ol.addWidget(self._wand_controls)
        ol.addWidget(self._opt_hint)
        ol.addWidget(self._ob_brush_lbl); ol.addWidget(self._ob_brush)
        ol.addWidget(self._ob_tol_lbl); ol.addWidget(self._ob_tol)
        ol.addWidget(self._ob_feather_lbl); ol.addWidget(self._ob_feather)
        ol.addWidget(self._ob_blur_lbl); ol.addWidget(self._ob_blur)
        ol.addWidget(self.fg_btn)
        self._brush_details = QWidget()
        details = QHBoxLayout(self._brush_details)
        details.setContentsMargins(0, 0, 0, 0)
        for label, key in (("不透明度", "brush_opacity"), ("硬度", "brush_hardness"), ('平滑', 'brush_smoothing')):
            details.addWidget(QLabel(label))
            spin = QSpinBox()
            spin.setRange(0, 100)
            spin.setSuffix("%")
            spin.setValue(getattr(self, key))
            spin.valueChanged.connect(lambda value, k=key: setattr(self, k, value))
            details.addWidget(spin)
        ol.addWidget(self._brush_details)
        for label, key in (('压感大小', 'pressure_size'), ('压感透明度', 'pressure_opacity')):
            check = QCheckBox(label)
            check.setChecked(getattr(self,key))
            check.toggled.connect(lambda checked, name=key:setattr(self,name,checked))
            details.addWidget(check)
        self._clone_details = QWidget()
        clone_layout = QHBoxLayout(self._clone_details)
        clone_layout.setContentsMargins(0,0,0,0)
        for label, key in (('对齐源点', 'clone_aligned'), ('取样所有可见图层', 'clone_sample_all')):
            check = QCheckBox(label)
            check.setChecked(getattr(self,key))
            check.toggled.connect(lambda checked, name=key:setattr(self,name,checked))
            clone_layout.addWidget(check)
        ol.addWidget(self._clone_details)
        self._line_controls = QWidget()
        line_layout = QHBoxLayout(self._line_controls)
        line_layout.setContentsMargins(0,0,0,0)
        for attribute,choices in (('line_style',[('实线',1),('虚线',2),('点线',3)]),('line_cap',[('方形端点',16),('圆形端点',32)])):
            combo = QComboBox()
            for label,value in choices:
                combo.addItem(label,value)
            combo.currentIndexChanged.connect(lambda _,name=attribute,widget=combo:self.set_line_option(name,widget.currentData()))
            line_layout.addWidget(combo)
        ol.addWidget(self._line_controls)
        self._crop_controls = QWidget()
        crop_layout = QHBoxLayout(self._crop_controls)
        crop_layout.setContentsMargins(0, 0, 0, 0)
        self._crop_ratio = QComboBox()
        for label, ratio in (("自由比例", None), ("1:1", 1.), ("3:4", 3/4), ("4:3", 4/3),
                             ("9:16", 9/16), ("16:9", 16/9), ("2:3", 2/3), ("3:2", 3/2)):
            self._crop_ratio.addItem(label, ratio)
        self._crop_ratio.setToolTip("裁剪比例；按住 Alt 从中心对称裁剪")
        self._crop_ratio.currentIndexChanged.connect(self.cancel_crop)
        crop_layout.addWidget(self._crop_ratio)
        self._crop_output = QComboBox()
        self._crop_output.addItems(["当前文档 · 保留图层", "复制合成到新文档"])
        crop_layout.addWidget(self._crop_output)
        for label, callback in (("从选区", self.crop_from_selection), ("应用 ↵", self.apply_crop),
                                ("取消 Esc", self.cancel_crop)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            crop_layout.addWidget(button)
        ol.addWidget(self._crop_controls)
        # 形状组
        ol.addWidget(self._sep_v())
        ol.addWidget(self._sh_fill_chk); ol.addWidget(self._sh_fill_btn)
        ol.addWidget(self._sh_stroke_chk); ol.addWidget(self._sh_stroke_lbl)
        ol.addWidget(self._sh_stroke_w); ol.addWidget(self._sh_stroke_btn)
        ol.addWidget(self._sep_v())
        ol.addWidget(self._sh_grad_chk); ol.addWidget(self._sh_grad_from_btn); ol.addWidget(self._sh_grad_to_btn)
        ol.addStretch()
        return opt_bar

    def _build_left_toolbar(self):
        """Grouped tool rows with aligned icons, labels and shortcut badges."""
        tb = QFrame()
        tb.setMinimumWidth(0)
        tb.setStyleSheet('background:#1d1d20;border:0;')
        lay = QVBoxLayout(tb)
        lay.setContentsMargins(8, 6, 8, 8); lay.setSpacing(2)
        self._tool_buttons = {}
        headings = []
        sections = [
            ('选择', [
                (Tool.MOVE, '移动', 'move', 'V', '拖动移动图层；空白处框选，Shift / Ctrl 点击多选'),
                (Tool.SELECT_RECT, '矩形选框', 'select_rect', 'M', '拖动建立矩形选区'),
                (Tool.SELECT_ELLIPSE, '椭圆选框', 'select_ellipse', 'Shift+M', '拖动建立椭圆选区'),
                (Tool.SELECT_LASSO, '自由套索', 'lasso', 'L', '沿目标边缘拖动，自由建立选区'),
                (Tool.POLY_LASSO, '多边形套索', 'polygon', 'P', '逐点点击，双击或 Enter 闭合'),
                (Tool.WAND, '魔棒', 'wand', 'W', '点击选择相近颜色，顶部可设置选区组合方式'),
                (Tool.OBJECT_SELECT, '对象选择', 'object', 'Q', '点击目标，识别并选择对象'),
                (Tool.QUICK_SELECT, '快速选择', 'quick_select', 'A', '涂抹扩选；Shift 添加，Alt 减去'),
            ]),
            ('绘制', [
                (Tool.BRUSH, '画笔', 'brush', 'B', '使用前景色绘制；方括号调整大小'),
                (Tool.ERASER, '橡皮擦', 'eraser', 'E', '涂抹擦除像素'),
                (Tool.TEXT, '文字', 'text', 'T', '双击编辑；拖动移动或调整文字框'),
                (Tool.SHAPE_RECT, '矩形', 'rectangle', 'R', '拖动绘制矩形形状'),
                (Tool.SHAPE_ELLIPSE, '椭圆', 'ellipse', 'O', '拖动绘制椭圆形状'),
                (Tool.SHAPE_LINE, '直线', 'line', 'U', '拖动绘制直线，可调整描边和端点'),
                (Tool.GRADIENT, '渐变', 'gradient', 'G', '拖动绘制渐变，Shift 使用径向渐变'),
            ]),
            ('修饰', [
                (Tool.EYEDROPPER, '吸管', 'eyedropper', 'I', '从画布拾取前景色和填充色'),
                (Tool.CROP, '裁剪', 'crop', 'C', '拖动裁剪区域，Enter 应用，Esc 取消'),
                (Tool.HEAL, '修复画笔', 'heal', 'J', 'Alt 点击取源，再涂抹修复'),
                (Tool.CLONE, '克隆图章', 'clone', 'S', 'Alt 点击取源，再涂抹复制'),
            ]),
        ]
        for index, (title, tools) in enumerate(sections):
            if index:
                lay.addSpacing(5)
            heading = QWidget(); heading.setFixedHeight(20)
            row = QHBoxLayout(heading); row.setContentsMargins(4, 0, 4, 0); row.setSpacing(8)
            label = QLabel(title)
            label.setStyleSheet('color:#77777f;background:transparent;font-size:10px;font-weight:600;')
            row.addWidget(label)
            line = QFrame(); line.setFixedHeight(1); line.setStyleSheet('background:#303034;border:0;')
            row.addWidget(line, 1); lay.addWidget(heading)
            headings.append((heading, label))
            for tool, label, icon, shortcut, help_text in tools:
                button = EditorToolButton(label, icon, shortcut, help_text)
                button.clicked.connect(lambda checked=False, value=tool: self.set_tool(value))
                self._tool_buttons[tool] = button
                lay.addWidget(button)
        # Keep colors visible while the tool list scrolls on shorter windows.
        self._color_card = EditorColorCard(self)
        self._swatch_btn = self._color_card.fill
        lay.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(tb)
        scroll.setStyleSheet('QScrollArea{background:#1d1d20;border:0;}'
            'QScrollBar:vertical{background:transparent;width:4px;margin:2px 0;}'
            'QScrollBar::handle:vertical{background:#444449;min-height:28px;border-radius:2px;}'
            'QScrollBar::handle:vertical:hover{background:#626269;}'
            'QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}'
            'QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}')
        self._tool_rail = AdaptiveToolRail(scroll, tb, self._tool_buttons.values(), headings, self._color_card)
        return self._tool_rail

    def _build_right_panel(self):
        """右侧属性面板（仅保留属性，无操作按钮）"""
        right_panel = QFrame()
        right_panel.setObjectName("imageProperties")
        right_panel.setMinimumWidth(250)
        right_panel.setStyleSheet(
            "QFrame#imageProperties{background:#1c1e23;border-left:1px solid #30323b;}"
            + workbench_field_style(IMAGE_ACCENT) + workbench_tabs_style(IMAGE_ACCENT))
        rl = QVBoxLayout(right_panel)
        rl.setContentsMargins(10, 8, 10, 8); rl.setSpacing(6)
        header = QHBoxLayout()
        header.setSpacing(7)
        header_icon = QLabel()
        header_icon.setPixmap(workbench_icon("sliders-horizontal", IMAGE_ACCENT).pixmap(16, 16))
        header.addWidget(header_icon)
        header_label = QLabel("属性")
        header_label.setStyleSheet("color:#ecedf3;font-size:13px;font-weight:600;")
        header.addWidget(header_label, 1)
        rl.addLayout(header)
        self._active_context = QLabel("未选择图层")
        self._active_context.setWordWrap(True)
        self._active_context.setStyleSheet(f"color:{IMAGE_ACCENT};background:#312942;"
                                           "padding:7px;border-radius:7px;")
        rl.addWidget(self._active_context)
        self._mask_exit = workbench_button("退出蒙版编辑", "x", accent=IMAGE_ACCENT)
        self._mask_exit.clicked.connect(self._exit_mask_edit)
        self._mask_exit.hide()
        rl.addWidget(self._mask_exit)

        # AI 抠图状态（仍被 _ai_remove_bg 使用）
        self.ai_status = QLabel("")
        self.ai_status.setWordWrap(True)
        self.ai_status.setStyleSheet("color:#00eaff;font-size:11px;")
        self.ai_status.setVisible(False)
        rl.addWidget(self.ai_status)

        # 画布
        rl.addWidget(self._section("画布"))
        cs = QFormLayout(); cs.setSpacing(6); cs.setContentsMargins(0, 0, 0, 0)
        self.cw = QSpinBox(); self.cw.setRange(1, 8000); self.cw.setValue(1080)
        self.ch = QSpinBox(); self.ch.setRange(1, 8000); self.ch.setValue(1080)
        self.cw.setFixedHeight(26); self.ch.setFixedHeight(26)
        cs.addRow(ScrubbableLabel('宽 W',self.cw),self.cw)
        cs.addRow(ScrubbableLabel('高 H',self.ch),self.ch)
        self.cs = cs
        rl.addLayout(cs)
        self.b_resize = workbench_button("应用尺寸", None, accent=IMAGE_ACCENT)
        self.b_resize.clicked.connect(self._apply_canvas_size)
        rl.addWidget(self.b_resize)
        bgrow = QHBoxLayout()
        self.bg_color_btn = workbench_button("背景色", "palette", accent=IMAGE_ACCENT)
        self.bg_color_btn.clicked.connect(self._choose_bg_color)
        bgrow.addWidget(self.bg_color_btn)
        self.trans_chk = QCheckBox("透明"); self.trans_chk.setChecked(True); self.trans_chk.toggled.connect(self._set_transparent)
        bgrow.addWidget(self.trans_chk); bgrow.addStretch()
        rl.addLayout(bgrow)

        # 图层属性
        rl.addWidget(self._section("图层属性"))
        # 混合模式
        blend_row = QHBoxLayout()
        blend_row.addWidget(QLabel("混合模式"))
        self.blend_combo = QComboBox()
        self.blend_combo.addItems(["正常", "变暗", "正片叠底", "颜色加深", "线性加深",
                                    "变亮", "滤色", "颜色减淡", "线性减淡",
                                    "叠加", "柔光", "强光", "亮光", "线性光",
                                    "差值", "排除", "色相", "饱和度", "颜色", "明度"])
        self.blend_combo.setAccessibleName("图层混合模式")
        self.blend_combo.currentTextChanged.connect(self._set_blend_mode)
        blend_row.addWidget(self.blend_combo)
        rl.addLayout(blend_row)

        # P4 对齐快捷按钮行（单选=对画布，多选=对选区包围盒）
        self._align_reference = QComboBox()
        self._align_reference.setAccessibleName("图层对齐参照")
        for label,value in (('对齐参照：自动','auto'),('对齐参照：画布','canvas'),('对齐参照：图层','layers'),('对齐参照：像素选区','selection')):
            self._align_reference.addItem(label,value)
        rl.addWidget(self._align_reference)
        align_row = QHBoxLayout()
        align_row.setSpacing(2)
        for icon, mode, tip in (
            ("align-start-vertical", "left", "左对齐"), ("align-center-vertical", "hcenter", "水平居中"),
            ("align-end-vertical", "right", "右对齐"), ("align-start-horizontal", "top", "顶对齐"),
            ("align-center-horizontal", "vcenter", "垂直居中"), ("align-end-horizontal", "bottom", "底对齐"),
        ):
            ab = workbench_button("", icon, tip, accent=IMAGE_ACCENT)
            ab.setFixedSize(28, 26)
            ab.clicked.connect(lambda _c, m=mode: self._align_layers(m))
            align_row.addWidget(ab)
        align_row.addStretch()
        rl.addLayout(align_row)
        rl.addWidget(QLabel("不透明度"))
        self.op_slider = QSlider(Qt.Orientation.Horizontal); self.op_slider.setRange(0, 100); self.op_slider.setValue(100)
        self.op_slider.setAccessibleName("图层不透明度")
        self.op_slider.setStyleSheet(workbench_slider_style(IMAGE_ACCENT))
        self.op_slider.sliderPressed.connect(self._begin_opacity_change)
        self.op_slider.sliderReleased.connect(self._end_opacity_change)
        self.op_slider.valueChanged.connect(self._set_active_opacity)
        rl.addWidget(self.op_slider)

        # 样式
        self._text_property_panel = TextPropertyPanel(self)
        rl.addWidget(self._text_property_panel)
        self._adjust_property_panel = AdjustmentPropertyPanel(self)
        rl.addWidget(self._adjust_property_panel)
        rl.addWidget(self._section("样式"))
        self.style_box = self._build_style_box()
        rl.addWidget(self.style_box)
        effects_button = workbench_button("图层效果…", "sparkles", "描边 / 发光 / 叠色", accent=IMAGE_ACCENT)
        effects_button.clicked.connect(lambda:edit_layer_effects(self))
        rl.addWidget(effects_button)

        rl.addStretch()
        return right_panel

    def _build_layers_panel(self):
        """右下角图层面板"""
        panel = QFrame()
        panel.setStyleSheet("background:#1b1b1e;border-top:1px solid #2c2c2c;")
        lp = QVBoxLayout(panel)
        lp.setContentsMargins(8, 6, 8, 6); lp.setSpacing(4)

        # 表头：图层 + 数量
        lhdr = QHBoxLayout()
        layer_icon = QLabel()
        layer_icon.setPixmap(workbench_icon("layers", "#a8abb8").pixmap(16, 16))
        lhdr.addWidget(layer_icon)
        lhdr.addWidget(QLabel("图层"))
        lhdr.addStretch()
        self.layer_count = QLabel("0")
        self.layer_count.setStyleSheet("color:#777;font-size:11px;")
        lhdr.addWidget(self.layer_count)
        lp.addLayout(lhdr)

        # 不透明度（PS 风格：图层面板顶部）
        oprow = QHBoxLayout(); oprow.setSpacing(6)
        op_lbl = QLabel("不透明度")
        op_lbl.setStyleSheet("color:#999;font-size:11px;")
        oprow.addWidget(op_lbl)
        self.layer_op_slider = QSlider(Qt.Orientation.Horizontal)
        self.layer_op_slider.setRange(0, 100); self.layer_op_slider.setValue(100)
        self.layer_op_slider.setAccessibleName("图层不透明度")
        self.layer_op_slider.setStyleSheet(workbench_slider_style(IMAGE_ACCENT))
        self.layer_op_slider.sliderPressed.connect(self._begin_opacity_change)
        self.layer_op_slider.sliderReleased.connect(self._end_opacity_change)
        self.layer_op_slider.valueChanged.connect(self._set_active_opacity)
        oprow.addWidget(self.layer_op_slider, 1)
        self.layer_op_label = QLabel("100%")
        self.layer_op_label.setFixedWidth(34)
        self.layer_op_label.setStyleSheet("color:#ccc;font-size:11px;")
        oprow.addWidget(self.layer_op_label)
        lp.addLayout(oprow)

        # 选择工具条
        selbar = QHBoxLayout(); selbar.setSpacing(4)
        b_all = workbench_button("全选", None, accent=IMAGE_ACCENT, quiet=True)
        b_all.clicked.connect(self._select_all_layers)
        b_inv = workbench_button("反选", None, accent=IMAGE_ACCENT, quiet=True)
        b_inv.clicked.connect(self._invert_layer_selection)
        b_clr = workbench_button("取消", None, accent=IMAGE_ACCENT, quiet=True)
        b_clr.clicked.connect(self._clear_layer_selection)
        selbar.addWidget(b_all); selbar.addWidget(b_inv); selbar.addWidget(b_clr)
        lp.addLayout(selbar)
        filter_row = QHBoxLayout()
        self._layer_search = QLineEdit()
        self._layer_search.setPlaceholderText("搜索图层名称")
        self._layer_search.setClearButtonEnabled(True)
        self._layer_search.textChanged.connect(self._filter_layer_list)
        filter_row.addWidget(self._layer_search, 1)
        self._layer_kind = QComboBox()
        for label, kind in (("全部", ""), ("图片", "image"), ("文字", "text"),
                             ("形状", "shape"), ("调整", "adjust")):
            self._layer_kind.addItem(label, kind)
        self._layer_kind.currentIndexChanged.connect(self._filter_layer_list)
        filter_row.addWidget(self._layer_kind)
        lp.addLayout(filter_row)

        self.layer_list = LayerList(self)
        self.layer_list.setStyleSheet("QListWidget{background:#121215;border:1px solid #2a2a2d;border-radius:4px;}"
                                       "QListWidget::item{padding:2px 4px;}")
        self.layer_list.setSpacing(2)
        self.layer_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        # 选择改由 LayerItemWidget / ArtboardHeaderWidget 的 mousePressEvent 触发
        # （setItemWidget 内的子控件会吃掉 itemClicked，导致点击图层不选中）
        self.layer_list.itemDoubleClicked.connect(self._on_list_double_click)
        self.layer_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.layer_list.customContextMenuRequested.connect(self._layer_context_menu)
        lp.addWidget(self.layer_list, 1)

        # 底部按钮全部取消（按用户要求）
        return panel

    def _build_history_panel(self):
        """历史记录面板（PS 风格）：显示操作步骤，可点击跳转。"""
        panel = QFrame()
        panel.setStyleSheet("background:#1b1b1e;border-top:1px solid #2c2c2c;")
        hp = QVBoxLayout(panel)
        hp.setContentsMargins(8, 6, 8, 6); hp.setSpacing(4)

        hdr = QLabel("历史记录")
        hdr.setStyleSheet("color:#aaa;font-size:11px;font-weight:bold;")
        hp.addWidget(hdr)

        self.history_list = QListWidget()
        self.history_list.setStyleSheet(
            "QListWidget{background:#121215;border:1px solid #2a2a2d;border-radius:4px;}"
            "QListWidget::item{padding:3px 6px;color:#aaa;font-size:11px;}"
            "QListWidget::item:hover{background:#2a2a30;color:#fff;}"
        )
        self.history_list.setSpacing(1)
        self.history_list.itemClicked.connect(self._on_history_clicked)
        hp.addWidget(self.history_list, 1)
        return panel

    def _refresh_history_panel(self):
        """刷新历史记录列表显示（PS 风格）。"""
        self.history_list.clear()
        if not self._history:
            return
        # 显示所有历史条目：每条对应一次操作（操作后状态）
        # _history[i] 是操作 i 执行前的快照，所以操作 i 的"结果状态"需要取 _history[i+1]
        # 或在末尾时显示"当前状态"
        is_tip = (self._history_idx == len(self._history) - 1)
        for i, entry in enumerate(self._history):
            name = entry.get("name", "操作")
            is_current = (i == self._history_idx)
            if is_current:
                if is_tip:
                    # 最新一条：操作后的实时状态未存入快照，显示"当前状态"
                    label = f"▶ {name}（当前）"
                else:
                    label = f"▶ {name}"
            else:
                label = f"  {name}"
            item = QListWidgetItem(label)
            if is_current:
                item.setForeground(QColor("#3d8ef8"))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.history_list.addItem(item)
        # 自动滚动到当前项
        if 0 <= self._history_idx < self.history_list.count():
            self.history_list.setCurrentRow(self._history_idx)
            self.history_list.scrollToItem(self.history_list.item(self._history_idx))

    def _on_history_clicked(self, item):
        """点击历史条目跳转到该状态。点击当前项时，若为最新状态则不变。"""
        idx = self.history_list.row(item)
        is_tip = (self._history_idx == len(self._history) - 1)
        if idx == self._history_idx and is_tip:
            return  # 点击"当前状态"不跳转
        self._history_jump_to(idx)

    def _show_history_panel(self):
        """窗口菜单 → 切换到历史记录标签页。"""
        self._layer_history_tabs.setCurrentIndex(1)  # 0=图层, 1=历史记录

    def _slider(self, lo, hi, val, cb):
        s = QSlider(Qt.Orientation.Horizontal); s.setRange(lo, hi); s.setValue(val); s.setFixedWidth(72)
        s.setStyleSheet(workbench_slider_style(IMAGE_ACCENT))
        s.valueChanged.connect(cb)
        return s

    def _mini_btn(self):
        return "QPushButton{font-size:14px;border:1px solid transparent;border-radius:4px;background:transparent;color:#aaa;}" \
               "QPushButton:hover{background:#3a3a3d;color:#fff;}"

    def _menu_style(self):
        """下拉菜单样式（深色主题）。"""
        return (
            "QMenu{background:#252832;border:1px solid #494958;border-radius:7px;padding:4px;}"
            "QMenu::item{padding:6px 24px 6px 12px;color:#ecedf3;font-size:12px;border-radius:4px;}"
            "QMenu::item:selected{background:#423653;color:#f3eaff;}"
            "QMenu::separator{height:1px;background:#3a3a3d;margin:4px 12px;}"
        )

    def _menu_btn(self, text):
        """下拉菜单触发按钮（PS 风格）。"""
        icons = {"文件": "folder-open", "编辑": "pencil", "图层": "layers", "图像": "image",
                 "滤镜": "sliders-horizontal", "选择": "square-dashed", "导出": "upload",
                 "视图": "eye", "窗口": "panel-top"}
        return workbench_button(text, icons.get(text), accent=IMAGE_ACCENT, quiet=True)

    def _add_menu_action(self, menu, text, fn, shortcut=""):
        return self.register_command(menu, text, fn, shortcut)

    @staticmethod
    def _color_btn(color, tooltip=""):
        return ColorSwatchButton(color, tooltip)

    @staticmethod
    def _update_color_btn(btn, color):
        """更新颜色块按钮的背景色。"""
        if isinstance(btn, ColorSwatchButton):
            btn.set_color(color)
            return
        btn.setStyleSheet(
            f"QPushButton{{background:{color.name()};border:1px solid #555;border-radius:2px;}}"
            "QPushButton:hover{border-color:#aaa;}"
        )

    # ═══════ 项目 / 图层管理 ═══════
    def set_doc_name(self, name):
        """设置文档名并同步更新容器标签标题。"""
        self.doc_name = name or "未命名"
        host = getattr(self, "host", None)
        if host is not None:
            idx = host.tab.indexOf(self)
            if idx >= 0:
                host.tab.setTabText(idx, self.doc_name)

    def new_project(self, w=1080, h=1080):
        if hasattr(self, '_recovery_id'):
            self.autosave_recovery(force=True)
            self._start_recovery_session()
        self._clear_interaction_preview()
        self._move_olds = None
        self.view._handle_drag = None
        self.view._layer_marquee = None
        self.detach_comp()
        self.stop_text_session()
        self._property_history_timer.stop()
        self._pending_property_history = None
        if hasattr(self, "_compare_before_image"):
            self._reset_ai_compare()
        self.project = ImageProject(w, h)
        self.active = None
        self.active_artboard = None
        self.selected = []
        self._anchor = None
        self.selection = None
        self.sel_alpha = None
        self._sel_base = None
        self._history.clear()
        self._history_idx = -1
        # 播种初始状态，使「第一步操作」也可撤销（避免首步撤销失效 / 撤销少一步）
        self._history = [{"name": "初始状态", "snapshot": self._snapshot()}]
        self._history_idx = 0
        self._refresh_history_panel()
        self._refresh_layers()
        self._redraw()
        self._request_fit()   # 延迟到视口就绪后居中（启动时非可见，showEvent 会兜底）

    def _on_new_canvas(self):
        """文件 → 新建画布：弹对话框命名 + 设定尺寸，在容器内新建独立文档。"""
        dlg = NewCanvasDialog(self,
                              default_w=self._ctx_size()[0],
                              default_h=self._ctx_size()[1])
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        w, h = dlg.result_size()
        name = dlg.result_name()
        host = getattr(self, "host", None)
        if host is not None:
            host.new_document(w, h, name=name)
        else:
            self.new_project(w, h)
            self.set_doc_name(name)

    def add_image_dialog(self):
        from core.image_import import ASSET_FILTER
        paths, _ = QFileDialog.getOpenFileNames(self, "导入图片或文档", "", ASSET_FILTER)
        for p in paths:
            self.add_image_from_path(p)

    _SELECTION_ORIGIN_MIME = "application/x-creativeenginepro-selection-origin"

    def copy_selection(self):
        """复制当前像素选区的真实 RGBA 内容，不包含棋盘格或选区外画面。"""
        if self.selection is None or not self.selection.any():
            return self.copy_layers()
        if self.active_artboard is not None:
            image = self._render_artboard(self.active_artboard)
        else:
            image = self._render_composite(for_export=True)
        pixels, origin = _extract_selected_rgba(
            numpy_from_qimage(image), self.selection, self.sel_alpha)
        if pixels is None:
            self.status_tool.setText("选区为空，未复制")
            return False

        mime = QMimeData()
        mime.setImageData(qimage_from_numpy(pixels))
        mime.setData(
            self._SELECTION_ORIGIN_MIME,
            f"{origin[0]},{origin[1]}".encode("ascii"),
        )
        QApplication.clipboard().setMimeData(mime)
        self.status_tool.setText(
            f"已复制选区 {pixels.shape[1]} × {pixels.shape[0]}（透明背景已保留）")
        return True

    def paste_image(self):
        """从系统剪贴板粘贴图片（如截图 / 从文件复制的图片）。"""
        if self.paste_layers():
            return
        cb = QApplication.clipboard()
        img = cb.image()
        if img.isNull():
            QMessageBox.information(self, "提示",
                "剪贴板里没有可粘贴的图片。\n请先复制一张图片（截图，或从文件夹/浏览器复制图片），再粘贴到这里。")
            return
        img = img.convertToFormat(QImage.Format.Format_RGBA8888)
        arr = numpy_from_qimage(img)
        iw, ih = arr.shape[1], arr.shape[0]
        layer = ImageLayer("粘贴图片", pixels=arr, w=iw, h=ih, kind="image")
        self._push_undo("粘贴图片")
        copied_origin = None
        mime = cb.mimeData()
        if mime is not None and mime.hasFormat(self._SELECTION_ORIGIN_MIME):
            try:
                raw = bytes(mime.data(self._SELECTION_ORIGIN_MIME)).decode("ascii")
                ox, oy = raw.split(",", 1)
                copied_origin = (float(ox), float(oy))
            except (TypeError, ValueError, UnicodeError):
                copied_origin = None
        ab = self.active_artboard
        if self.project.artboards and ab is None:
            ab = self.project.artboards[0]
            self.active_artboard = ab
        if ab is not None:
            if copied_origin is not None:
                layer.scale = 1.0
                layer.x = copied_origin[0] + iw / 2.0
                layer.y = copied_origin[1] + ih / 2.0
            else:
                sc = min(ab.w / iw, ab.h / ih, 1.0) * 0.9
                layer.scale = sc
                layer.x = ab.w / 2.0
                layer.y = ab.h / 2.0
            ab.layers.append(layer)
        else:
            # 仅在画布完全为空时自动匹配图片尺寸；其余情况保持当前画布、居中缩放
            has_content = len(self.project.layers) > 0
            if not has_content:
                self._set_canvas_size(iw, ih)
                layer.scale = 1.0
                layer.x = iw / 2.0
                layer.y = ih / 2.0
            else:
                cw, ch = self.project.w, self.project.h
                if copied_origin is not None:
                    layer.scale = 1.0
                    layer.x = copied_origin[0] + iw / 2.0
                    layer.y = copied_origin[1] + ih / 2.0
                else:
                    sc = min(cw / iw, ch / ih, 1.0) * 0.9
                    layer.scale = sc
                    layer.x = cw / 2.0
                    layer.y = ch / 2.0
            self.project.add_layer(layer)
        self.set_active(layer)
        self._refresh_layers()
        self._redraw()
        self.status_tool.setText(
            f"已粘贴 {iw} × {ih}（透明背景已保留）")

    # ═══════ 键盘快捷键（PS 风格）═══════
    def _swap_colors(self):
        """交换前景色和背景色（PS: X 键）。"""
        self.fg, self.bg = self.bg, self.fg
        self._sync_color_ui()

    def _default_colors(self):
        """恢复默认前景/背景色（PS: D 键）—— 黑/白。"""
        self.fg = QColor(255, 255, 255)
        self.bg = QColor(0, 0, 0)
        self._sync_color_ui()

    def _adjust_brush_size(self, delta):
        """调整笔刷大小（PS: [ / ] 键）。"""
        self.brush_size = max(1, min(200, self.brush_size + delta))
        # 同步更新选项条滑块
        self._ob_brush.blockSignals(True)
        self._ob_brush.setValue(self.brush_size)
        self._ob_brush.blockSignals(False)
        self._refresh_brush_cursor()

    def _on_brush_size_slider(self, value):
        self.brush_size = max(1, min(200, int(value)))
        self._refresh_brush_cursor()

    def _refresh_brush_cursor(self):
        if self.tool not in (Tool.BRUSH, Tool.ERASER):
            return
        self.view._set_canvas_cursor(self._brush_cursor())
        self.view.viewport().update()

    def _zoom_view(self, factor):
        """缩放画布视图。"""
        self.view.zoom_by(factor)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape and self._finish_layer_marquee(cancel=True):
            e.accept()
            return
        # 文字编辑态：直接捕获键入，不进入快捷键分发（Esc/Enter 等由 _text_edit_key 处理）
        if self._text_editing:
            self._text_edit_key(e)
            return
        if self.suppress_remapped_key(e):
            e.accept()
            return
        # Delete/Backspace 由 eventFilter 统一处理（含子控件焦点情况），避免重复删除
        ctrl = e.modifiers() & Qt.KeyboardModifier.ControlModifier
        shift = e.modifiers() & Qt.KeyboardModifier.ShiftModifier
        alt = e.modifiers() & Qt.KeyboardModifier.AltModifier

        # 克隆图章 / 修复画笔：按住 Alt 时光标变「设源点」样式
        if e.key() == Qt.Key.Key_Alt and self.tool in (Tool.CLONE, Tool.HEAL):
            self.view._set_canvas_cursor(self._clone_cursor(alt=True))
            return

        # ── P4 自由变换态：Enter 确认 / Esc 取消（优先级最高）──
        if self._ft_active:
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._ft_commit()
                return
            if e.key() == Qt.Key.Key_Escape:
                self._ft_cancel()
                return

        # ── 编辑快捷键 ──
        if self.tool == Tool.CROP and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape):
            if e.key() == Qt.Key.Key_Escape:
                self.cancel_crop()
            else:
                self.apply_crop()
            return
        if ctrl and e.key() == Qt.Key.Key_Z:
            if shift:
                self.redo()
            else:
                self.undo()
            return
        if ctrl and e.key() == Qt.Key.Key_Y:
            self.redo()
            return
        if ctrl and alt and e.key() == Qt.Key.Key_F:
            self.repeat_filter()
            return
        if ctrl and e.key() == Qt.Key.Key_A:
            self.select_canvas()
            return
        if ctrl and e.key() == Qt.Key.Key_C:
            fw = QApplication.focusWidget()
            if isinstance(fw, (QLineEdit, QPlainTextEdit, QSpinBox, QComboBox)):
                super().keyPressEvent(e)
                return
            if shift:
                self.copy_merged()
            else:
                self.copy_selection()
            return
        if ctrl and e.key() == Qt.Key.Key_V:
            fw = QApplication.focusWidget()
            if isinstance(fw, (QLineEdit, QPlainTextEdit, QSpinBox, QComboBox)):
                super().keyPressEvent(e)
                return
            self.paste_image()
            return

        # ── PS 选区快捷键 ──
        if ctrl and e.key() == Qt.Key.Key_D:
            self._clear_selection()
            return
        if ctrl and shift and e.key() == Qt.Key.Key_I:
            self._invert_selection()
            return
        # PS: Ctrl+Shift+F5 = 内容识别填充（需先建立选区）
        if ctrl and shift and e.key() == Qt.Key.Key_F5:
            self._content_aware_fill()
            return
        # PS: M=矩形选框，Shift+M=椭圆选框切换
        if shift and e.key() == Qt.Key.Key_M and not ctrl and not alt:
            self.set_tool(Tool.SELECT_ELLIPSE)
            return

        # ── PS 填充快捷键（Alt+Backspace=填前景色，Ctrl+Backspace=填背景色）──
        has_sel = self.selection is not None and self.selection.any()
        if alt and e.key() == Qt.Key.Key_Backspace and not ctrl and has_sel:
            self._push_undo("填充前景色")
            r, g, b = self.fg.red(), self.fg.green(), self.fg.blue()
            self._apply_selection(lambda px, ly, lx: (r, g, b, 255))
            self._redraw()
            return
        if ctrl and e.key() == Qt.Key.Key_Backspace and not alt and has_sel:
            self._push_undo("填充前景色")
            r, g, b = self.bg.red(), self.bg.green(), self.bg.blue()
            self._apply_selection(lambda px, ly, lx: (r, g, b, 255))
            self._redraw()
            return

        # ── PS 图层快捷键 ──
        if ctrl and e.key() == Qt.Key.Key_J:
            if self.active is not None:
                self._duplicate_layer(self.active)
            return
        if ctrl and e.key() == Qt.Key.Key_E:
            if len(self.selected) >= 2:
                self._merge_selected_layers()
            else:
                self._merge_down()
            return
        # PS: Ctrl+Alt+G = 创建/释放剪切蒙版
        if ctrl and alt and e.key() == Qt.Key.Key_G:
            if self.active is not None:
                self._toggle_clip(self.active)
            return
        # PS: Ctrl+Shift+G = 取消编组；Ctrl+G = 图层编组
        if ctrl and shift and not alt and e.key() == Qt.Key.Key_G:
            self._ungroup_selected()
            return
        if ctrl and not shift and not alt and e.key() == Qt.Key.Key_G:
            self._group_selected()
            return
        # PS: Ctrl+T = 自由变换（Enter 确认 / Esc 取消）
        if ctrl and not shift and not alt and e.key() == Qt.Key.Key_T:
            self._start_free_transform()
            return
        # PS: Ctrl+Alt+T = 变换复制（复制当前图层并立即进入自由变换）
        if ctrl and alt and not shift and e.key() == Qt.Key.Key_T:
            self._transform_copy()
            return

        # ── PS 视图快捷键 ──
        if ctrl and e.key() == Qt.Key.Key_R:  # Ctrl+R 切换标尺
            self._toggle_rulers()
            return
        if ctrl and e.key() == Qt.Key.Key_0:
            self.view.fit_view()
            return
        if ctrl and e.key() == Qt.Key.Key_Equal:  # Ctrl+= (Ctrl++)
            self._zoom_view(1.25)
            return
        if ctrl and e.key() == Qt.Key.Key_Minus:  # Ctrl+-
            self._zoom_view(0.8)
            return

        # ── PS 颜色快捷键 ──
        if e.key() == Qt.Key.Key_F and not e.modifiers():
            self.toggle_focus_canvas()
            return
        if e.key() == Qt.Key.Key_Escape and getattr(self, '_canvas_focused', False):
            self.toggle_focus_canvas()
            return
        if e.key() == Qt.Key.Key_D and not e.modifiers():
            self._default_colors()
            return
        if e.key() == Qt.Key.Key_X and not e.modifiers():
            self._swap_colors()
            return

        # ── PS 笔刷大小调节 ──
        if e.key() == Qt.Key.Key_BracketLeft:   # [
            self._adjust_brush_size(-5)
            return
        if e.key() == Qt.Key.Key_BracketRight:  # ]
            self._adjust_brush_size(5)
            return

        # ── Enter：多边形套索闭合 / 文字层编辑 ──
        if e.key() == Qt.Key.Key_Return and not e.modifiers():
            # 多边形套索：Enter 闭合多边形
            if self.tool == Tool.POLY_LASSO:
                pts = getattr(self.view, '_poly_pts', None)
                if pts and len(pts) >= 3:
                    self._commit_lasso(pts, self._sel_combine)
                    self.view._poly_pts = None
                    return
            fw = QApplication.focusWidget()
            if not isinstance(fw, (QLineEdit, QPlainTextEdit, QSpinBox, QComboBox)):
                if self.active is not None and self.active.kind == "text" \
                        and not self._text_editing:
                    self._start_text_edit(self.active, is_new=False)
                    return

        # ── 空格 = 临时抓手平移画布（PS 风格，自动重复忽略）──
        if e.key() == Qt.Key.Key_Space and not e.modifiers() and not e.isAutoRepeat():
            self._space_prev_tool = self.tool
            self._space_held = True
            self.view._set_canvas_cursor(Qt.CursorShape.OpenHandCursor)
            # P2: 不用 DragMode（受 sceneRect 限制），改为手动滚动手柄
            # 第一次 mouseMove 时 _pan_last 由 CanvasView.mousePressEvent 初始化
            self.view._pan_last = None
            return

        # ── 工具快捷键（无修饰键，PS 风格）──
        # G selects the gradient tool, matching the toolbar label.
        if e.key() == Qt.Key.Key_G and not e.modifiers():
            self.set_tool(Tool.GRADIENT)
            return
        key_map = {
            Qt.Key.Key_V: Tool.MOVE,
            Qt.Key.Key_M: Tool.SELECT_RECT,
            Qt.Key.Key_E: Tool.ERASER,           # PS: E = 橡皮擦
            Qt.Key.Key_L: Tool.SELECT_LASSO,
            Qt.Key.Key_P: Tool.POLY_LASSO,        # PS: P = 多边形套索
            Qt.Key.Key_W: Tool.WAND,
            Qt.Key.Key_B: Tool.BRUSH,
            Qt.Key.Key_T: Tool.TEXT,
            Qt.Key.Key_R: Tool.SHAPE_RECT,
            Qt.Key.Key_O: Tool.SHAPE_ELLIPSE,
            Qt.Key.Key_I: Tool.EYEDROPPER,
            Qt.Key.Key_C: Tool.CROP,
            Qt.Key.Key_A: Tool.QUICK_SELECT,      # PS 近似: A = 快速选择
            Qt.Key.Key_J: Tool.HEAL,
            Qt.Key.Key_S: Tool.CLONE,
        }
        t = key_map.get(e.key())
        if t is not None and not e.modifiers():
            self.set_tool(t)
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        """空格松开 → 退出抓手模式，恢复之前的工具（PS 风格）。"""
        if e.key() == Qt.Key.Key_Space and self._space_held and not e.isAutoRepeat():
            self._space_held = False
            self.set_tool(self._space_prev_tool)
            return
        # 克隆图章 / 修复画笔：松开 Alt 时恢复常态光标
        if e.key() == Qt.Key.Key_Alt and self.tool in (Tool.CLONE, Tool.HEAL):
            self.view._set_canvas_cursor(self._clone_cursor(alt=False))
            return
        super().keyReleaseEvent(e)

    def eventFilter(self, obj, e):
        if e.type() in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride):
            if isinstance(obj, QWidget) and obj is not self and not self.isAncestorOf(obj):
                return False
            if e.type() == QEvent.Type.ShortcutOverride:
                focused = QApplication.focusWidget()
                editing = isinstance(focused, (QLineEdit, QPlainTextEdit, QSpinBox, QComboBox))
                native = e.key() in (Qt.Key.Key_C, Qt.Key.Key_V, Qt.Key.Key_X, Qt.Key.Key_A, Qt.Key.Key_Z, Qt.Key.Key_Y)
                if self._text_editing or editing and (not e.modifiers() or e.text().isprintable() or native and e.modifiers() & Qt.KeyboardModifier.ControlModifier):
                    e.accept()
                    return True
        """全局捕获 Delete/Backspace：
        - 画布上有像素选区（魔棒/矩形/椭圆/套索）时，删除选区内的像素（仿 PS：Delete 清选区）；
        - 否则删除选中的图层；
        - 文本/数值输入框聚焦时不拦截，交给输入框自身处理。"""
        if e.type() == QEvent.Type.KeyPress and \
                e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            # 文字编辑态：Backspace/Delete 交给 _text_edit_key 处理，不要删除图层
            if self._text_editing:
                return False
            fw = QApplication.focusWidget()
            # 仅当焦点在真正的文本输入框（名称/正文编辑）时才把 Delete 让给输入框；
            # 数值 QSpinBox / 下拉 QComboBox（字号、描边、字体等）不是文本录入，
            # Delete 应删除选中的图层（否则选中文字层后焦点落在属性面板输入框，
            # 导致按 Delete 永远删不掉图层）。
            if isinstance(fw, (QLineEdit, QPlainTextEdit)):
                return False
            # 优先：存在像素选区时清除选区内像素（静默，失败则回退到删除图层）
            if self.selection is not None and self.selection.any():
                if self.delete_selection(silent=True):
                    return True
            if self.selected:
                self._delete_selected()
                return True
        return super().eventFilter(obj, e)

    def showEvent(self, e):
        super().showEvent(e)
        # 延迟到布局完成后再居中 fit（规避启动时/切换时 viewport 尺寸为 0 导致画布偏位）
        if self._fit_pending:
            QTimer.singleShot(0, self._do_fit_when_ready)

    def _do_fit_when_ready(self):
        """视口布局完成后再 fit；若仍未就绪则下一轮事件重试，直到拿到有效尺寸。"""
        vp = self.view.viewport()
        if vp.width() <= 1 or vp.height() <= 1:
            QTimer.singleShot(0, self._do_fit_when_ready)
            return
        self.view.fit_view()
        self._fit_pending = False

    def _request_fit(self):
        """请求一次自动适应居中（仅初次/画布尺寸变化时），不覆盖用户已有的缩放与平移。"""
        self._fit_pending = True
        if self.isVisible():
            QTimer.singleShot(0, self._do_fit_when_ready)

    def set_active(self, layer):
        if getattr(layer, 'locked', False):
            return  # 锁定图层不可设为活跃
        self._select_layer_only(layer)
        self._refresh_layers()
        self._sync_props()

    # ───── 画板上下文辅助 ─────

    def _ctx_size(self):
        if self.active_artboard is not None:
            return self.active_artboard.w, self.active_artboard.h
        return self.project.w, self.project.h

    def _doc_to_local(self, pt):
        """文档坐标 -> 当前画板本地坐标（画板模式）。"""
        if self.active_artboard is not None:
            tx, ty = self._ab_translate()
            return QPointF(pt.x() - tx - self.active_artboard.x,
                           pt.y() - ty - self.active_artboard.y)
        return QPointF(pt.x(), pt.y())




    def _set_active_artboard(self, ab):
        if ab is not None and ab not in self.project.artboards:
            return
        self.active_artboard = ab
        self.selected = []
        self.active = None
        self._anchor = None
        self._show_handles = False
        self.selection = None
        self.sel_alpha = None
        self._sel_base = None
        # 自动选中画板内最顶层图层，切换后即可编辑
        if ab is not None and ab.layers:
            top = ab.layers[-1]
            self.active = top
            self.selected = [top]
        self._refresh_layers()
        self._redraw()
        self._sync_props()

    # ───── 画板管理（Photoshop 风格） ─────
    def _open_artboard_dialog(self):
        dlg = ArtboardDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._add_artboard(dlg.name_edit.text().strip() or "画板",
                               dlg.w_spin.value(), dlg.h_spin.value())

    def _add_artboard(self, name, w, h):
        self._push_undo("新建画板")
        if not self.project.artboards:
            # 首次添加：把当前单画布整体转成第一个画板（保留已有内容/坐标）
            first = Artboard(name, 0, 0, self.project.w, self.project.h)
            first.bg_color = QColor(self.project.bg_color)
            first.transparent = False  # 画板默认白底，不继承项目透明设置
            first.layers = list(self.project.layers)
            self.project.artboards.append(first)
            self.project.layers = []
            self.active_artboard = first
        else:
            # 横向单行排列：所有画板沿 x 轴依次向右排开，顶部对齐 y=0，间隔 gap。
            # 新画板自动排到已有画板最右侧，无需用户手动摆放。
            gap = 80
            existing = list(self.project.artboards)
            nx = sum(ab.w for ab in existing) + gap * len(existing)
            ny = 0
            ab = Artboard(name, nx, ny, w, h)
            ab.transparent = False                       # 取消透明背景，统一实色（白底）
            ab.bg_color = QColor(self.project.bg_color) # 与首画板一致的实色底
            self.project.artboards.append(ab)
            self.active_artboard = ab
        # 同步画布尺寸框到当前激活画板
        self.cw.setValue(self.active_artboard.w)
        self.ch.setValue(self.active_artboard.h)
        self._invalidate_doc_bounds()
        self._clear_layer_selection()
        self._refresh_layers()
        self._redraw()
        self.view.fit_view()

    def _delete_artboard(self, ab):
        if ab not in self.project.artboards:
            return
        self._push_undo("删除画板")
        self.project.artboards.remove(ab)
        if self.active_artboard is ab:
            self.active_artboard = self.project.artboards[-1] if self.project.artboards else None
        # 删到最后一个画板：退回传统单画布（把该画板内容交还 project.layers）
        if not self.project.artboards:
            self.project.w, self.project.h = ab.w, ab.h
            self.project.transparent = ab.transparent
            self.project.bg_color = QColor(ab.bg_color)
            self.project.layers = list(ab.layers)
            self.cw.setValue(self.project.w); self.ch.setValue(self.project.h)
        self.active = None
        self.selected = []
        self._anchor = None
        self._show_handles = False
        self._invalidate_doc_bounds()
        self._refresh_layers()
        self._redraw()
        self.view.fit_view()

    def _rename_artboard(self, ab):
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "重命名画板", "画板名称：",
                                         text=ab.name)
        if ok and name.strip():
            ab.name = name.strip()
            self._refresh_layers()
            self._redraw()

    # ───── 图层多选（仿 PS） ─────
    def _is_layer_selected(self, layer):
        return layer in self.selected

    def _select_layer_only(self, layer):
        """单选：清空其它，仅选中该层（active=主图层）。
        若该层属于编组，则联动选中同组所有层（PS 组行为：整组一起移动）。"""
        if self._text_editing and layer is not self._text_edit_layer:
            self._end_text_edit(save=True)
        board = self._active_artboard_of(layer) if layer is not None else None
        if board is not None and board is not self.active_artboard:
            self.active_artboard = board
            self.selection = self.sel_alpha = self._sel_base = None
        if layer is not self.active:
            self._mask_edit = False
        self.selected = [layer] if layer is not None else []
        self.active = layer
        self._anchor = layer
        gid = getattr(layer, "group_id", None) if layer is not None else None
        if gid is not None:
            group = [l for l in self._ctx_layers()
                     if getattr(l, "group_id", None) == gid and not getattr(l, "locked", False)]
            if len(group) > 1:
                self.selected = group
        self._show_handles = (len(self.selected) == 1)

    # ═══════ P4 图层编组 ═══════

    def _ungroup_legacy(self):
        """Ctrl+Shift+G：解散选中图层所属的编组。"""
        gids = {getattr(l, "group_id", None) for l in self.selected} - {None}
        if not gids:
            return
        self._push_undo("取消编组")
        for l in self._all_layers():
            if getattr(l, "group_id", None) in gids:
                l.group_id = None
        self._refresh_layers()

    # ═══════ P4 智能对象 ═══════
    def _convert_to_smart_object(self):
        """把选中图层合并为一个智能对象层，原始图层数据内嵌保留，可随时还原编辑。"""
        sel = [l for l in (self.selected or ([self.active] if self.active else []))
               if l is not None and l.kind != "adjust"]
        if not sel:
            QMessageBox.information(self, "提示", "请先选中至少 1 个图层。")
            return
        self._push_undo("转换为智能对象")
        layers = self._ctx_layers()
        sel_sorted = [l for l in layers if l in sel]   # 按堆叠顺序
        cw, ch = self._ctx_size()
        buf = QImage(cw, ch, QImage.Format.Format_ARGB32)
        buf.fill(Qt.GlobalColor.transparent)
        p = QPainter(buf)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for l in sel_sorted:
            p.drawImage(0, 0, self._render_layer_canvas(l))
        p.end()
        arr = numpy_from_qimage(buf)
        ys, xs = np.where(arr[:, :, 3] > 0)
        if xs.size == 0:
            QMessageBox.information(self, "提示", "选中图层没有可见内容。")
            self._history.pop()
            self._history_idx -= 1
            self._refresh_history_panel()
            return
        x0, x1 = int(xs.min()), int(xs.max()) + 1
        y0, y1 = int(ys.min()), int(ys.max()) + 1
        crop = np.ascontiguousarray(arr[y0:y1, x0:x1])
        new = ImageLayer(sel_sorted[-1].name + " (智能对象)", pixels=crop,
                         w=x1 - x0, h=y1 - y0, kind="image")
        new.x = (x0 + x1) / 2.0
        new.y = (y0 + y1) / 2.0
        new.smart = True
        new.smart_source = [self._ser_layer(l, with_pixels=True) for l in sel_sorted]
        idx = layers.index(sel_sorted[-1])
        for l in sel_sorted:
            layers.remove(l)
        layers.insert(min(idx - len(sel_sorted) + 1, len(layers)), new)
        self._select_layer_only(new)
        self._refresh_layers()
        self._redraw()

    def _edit_smart_object(self, layer):
        """还原智能对象为原始图层继续编辑。"""
        if not getattr(layer, "smart", False) or not layer.smart_source:
            return
        self._push_undo("编辑智能对象内容")
        layers = self._parent_list_of(layer)
        idx = layers.index(layer)
        children = [self._deser_layer(o) for o in layer.smart_source]
        layers[idx:idx + 1] = children
        self.selected = list(children)
        self.active = children[-1]
        self._anchor = self.active
        self._show_handles = (len(children) == 1)
        self._refresh_layers()
        self._redraw()

    def _toggle_layer_select(self, layer):
        """Ctrl/⌘ 点击：在选区中切换该层。"""
        if layer is None:
            return
        if layer in self.selected:
            self.selected.remove(layer)
            if self.active is layer:
                self.active = self.selected[-1] if self.selected else None
        else:
            self.selected.append(layer)
            self.active = layer
        if self._anchor is None:
            self._anchor = layer
        self._show_handles = (len(self.selected) == 1)

    def _range_layer_select(self, layer):
        """Shift 点击：从 _anchor 到该层之间的连续层全部选中。"""
        if layer is None:
            return
        layers = self._ctx_layers()
        if self._anchor is None or self._anchor not in layers:
            self._select_layer_only(layer)
            return
        a = layers.index(self._anchor)
        b = layers.index(layer)
        lo, hi = (a, b) if a <= b else (b, a)
        self.selected = layers[lo:hi + 1]
        self.active = layer
        self._show_handles = (len(self.selected) == 1)

    # ═══════ P4 对齐 / 分布 ═══════


    def _clear_layer_selection(self):
        """取消所有图层选择。"""
        self.selected = []
        self.active = None
        self._anchor = None
        self._show_handles = False

    def _select_all_layers(self):
        """全选所有图层。"""
        layers = self._ctx_layers()
        self.selected = list(layers)
        self.active = layers[-1] if layers else None
        self._anchor = self.active
        self._show_handles = (len(self.selected) == 1)

    def _invert_layer_selection(self):
        """反选：被选中的变未选，未选的变选中。"""
        layers = self._ctx_layers()
        sel = set(self.selected)
        self.selected = [l for l in layers if l not in sel]
        self.active = self.selected[-1] if self.selected else None
        self._anchor = self.active
        self._show_handles = (len(self.selected) == 1)

    def _brush_cursor_view_radius(self):
        """Brush footprint radius in viewport pixels, including current zoom."""
        zoom = max(0.01, abs(float(self.view.transform().m11() or 1.0)))
        layer_scale = 1.0
        if (not getattr(self, "_mask_edit", False) and self.active is not None and
                self.active.kind == "image"):
            layer_scale = max(0.01, abs(float(self.active.scale or 1.0)))
        radius = float(self.brush_size) * zoom / layer_scale / 2.0
        return max(2.5, min(320.0, radius))

    def _brush_cursor(self, painting=False):
        """Tool-specific OS cursor; the full-size footprint is drawn by CanvasView."""
        size = 36
        center = size // 2
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        is_eraser = self.tool == Tool.ERASER
        color = QColor("#ffb35c") if is_eraser else QColor(self.fg)
        painter.setBrush(
            QColor(color.red(), color.green(), color.blue(), 65)
            if painting else Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0, 220), 3.0))
        painter.drawEllipse(QPointF(center, center), 10.0, 10.0)
        painter.setPen(QPen(color, 1.7, Qt.PenStyle.DashLine
                            if is_eraser else Qt.PenStyle.SolidLine))
        painter.drawEllipse(QPointF(center, center), 10.0, 10.0)
        if is_eraser:
            painter.save()
            painter.translate(center, center)
            painter.rotate(-35)
            painter.setPen(QPen(QColor("#ffffff"), 1.0))
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(-5, -3, 10, 6), 1.5, 1.5)
            painter.restore()
        else:
            painter.setPen(QPen(QColor("#ffffff"), 1.0))
            painter.setBrush(color)
            painter.drawEllipse(QPointF(center, center), 2.5, 2.5)
        painter.end()
        return QCursor(pixmap, center, center)


    def _clone_cursor(self, alt=False, painting=False):
        """克隆图章 / 修复画笔 的自定义光标：笔刷范围环 + 中心图章图标，
        颜色随工具区分（克隆=蓝 #3d8ef8，修复=青 #00eaff）。
          alt=True      → 琥珀色 + 十字标，表示「即将设置源点」
          painting=True  → 半透明填充环 + 中心实心点，表示「正在涂抹」"""
        size = 32
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = cy = size // 2
        base = QColor("#00eaff") if self.tool == Tool.HEAL else QColor("#3d8ef8")
        col = QColor("#ffb020") if alt else base
        r = max(4.0, min(14.0, float(self.brush_size) * 0.5))
        # 笔刷覆盖范围环
        pen = QPen(col); pen.setWidth(2)
        p.setPen(pen)
        if painting:
            p.setBrush(QBrush(QColor(col.red(), col.green(), col.blue(), 55)))
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r, r)
        # 中心图章图标：方块底座 + 斜柄
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(col))
        bw = 8
        p.drawRect(int(cx - bw / 2), int(cy - bw / 2), bw, bw)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(int(cx - 3), int(cy + 3), int(cx + 4), int(cy - 4))
        # alt：十字标（醒目提示「点此设源点」）
        if alt:
            p.setPen(QPen(QColor("#ffffff"), 1))
            p.drawLine(cx, int(cy - r - 4), cx, int(cy - r - 1))
            p.drawLine(cx, int(cy + r + 1), cx, int(cy + r + 4))
            p.drawLine(int(cx - r - 4), cy, int(cx - r - 1), cy)
            p.drawLine(int(cx + r + 1), cy, int(cx + r + 4), cy)
        # painting：中心实心点
        if painting:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor("#ffffff")))
            p.drawEllipse(QPointF(cx, cy), 2.5, 2.5)
        p.end()
        return QCursor(pm, cx, cy)

    def _eyedropper_cursor(self):
        """吸管工具专属光标：清晰的小吸管图标，作为编辑区「吸管模式」指示标。"""
        size = 32
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = QColor("#00eaff")
        p.translate(size // 2, size // 2)
        p.rotate(-45)
        # 管身（圆角矩形）
        p.setPen(QPen(col, 2))
        p.setBrush(QBrush(QColor(col.red(), col.green(), col.blue(), 55)))
        p.drawRoundedRect(-3, -11, 6, 15, 2, 2)
        # 顶端宽口
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(col))
        p.drawRect(-5, -13, 10, 4)
        # 底端笔尖（三角）
        p.drawPolygon([QPointF(-3, 4), QPointF(3, 4), QPointF(0, 11)])
        p.end()
        # 热点放在笔尖附近
        return QCursor(pm, 16, 16)

    def _gradient_cursor(self):
        """渐变工具专属光标：一条斜线段 + 两端起止色圆点，暗示「拖拽画线段」。"""
        size = 32
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#ffffff"), 2)
        p.setPen(pen)
        p.drawLine(6, 26, 26, 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor("#3d8ef8")))
        p.drawEllipse(QPointF(6, 26), 3.5, 3.5)
        p.setBrush(QBrush(QColor("#00eaff")))
        p.drawEllipse(QPointF(26, 6), 3.5, 3.5)
        p.end()
        return QCursor(pm, 6, 26)

    def set_tool(self, t):
        self._finish_layer_marquee(cancel=True)
        if self.tool == Tool.CROP and t != Tool.CROP:
            self.cancel_crop()
        # 切换工具时先收尾正在编辑的文字（PS 行为：换工具即落定当前文字层；
        # 新建层若尚未输入则直接丢弃，不留空图层）
        if self._text_editing:
            self._end_text_edit(save=True)
        self.tool = t
        for tt, b in self._tool_buttons.items():
            b.setChecked(tt == t)
        # 切换工具时清掉吸管取色预览缓存（避免残留 HUD / 旧光标的「小标志」）
        self._eyedrop_img = None
        self._eyedrop_preview = None
        cursor = Qt.CursorShape.CrossCursor
        tool_names = {Tool.MOVE: "移动 V", Tool.SELECT_RECT: "选框 M",
                      Tool.SELECT_ELLIPSE: "椭圆 ⇧M", Tool.SELECT_LASSO: "套索 L",
                      Tool.POLY_LASSO: "多边形套索 P", Tool.WAND: "魔棒 W",
                      Tool.QUICK_SELECT: "快速选择 A",
                      Tool.BRUSH: "笔刷 B", Tool.ERASER: "橡皮 E",
                      Tool.TEXT: "文字 T", Tool.SHAPE_RECT: "矩形 R",
                      Tool.SHAPE_ELLIPSE: "椭圆 O", Tool.EYEDROPPER: "吸管 I",
                      Tool.GRADIENT: "渐变 G", Tool.CROP: "裁剪 C"}
        self.status_tool.setText(tool_names.get(t, ""))
        if t not in tool_names:
            self.status_tool.setText(self._tool_buttons[t].toolTip())
        if t == Tool.MOVE:
            cursor = Qt.CursorShape.OpenHandCursor
        elif t in (Tool.BRUSH, Tool.ERASER):
            cursor = self._brush_cursor()
        elif t in (Tool.CLONE, Tool.HEAL):
            cursor = self._clone_cursor()
        elif t == Tool.EYEDROPPER:
            # 专属吸管光标 + 缓存合成图，供悬停时实时取色预览（编辑区「小标志」）
            cursor = self._eyedropper_cursor()
            try:
                self._eyedrop_img = self._render_composite()
            except Exception:
                self._eyedrop_img = None
        elif t == Tool.GRADIENT:
            cursor = self._gradient_cursor()
        self.view._set_canvas_cursor(cursor)
        if t not in (Tool.BRUSH, Tool.ERASER):
            self.view._brush_hover_pos = None
        self.view.viewport().update()
        self._update_option_bar()
        if t == Tool.CROP:
            self.crop_from_selection()

    def _update_option_bar(self):
        """选项条只显示当前工具相关的参数，避免一堆无关滑块堆在一起。"""
        t = self.tool
        tool_button = self._tool_buttons[t]
        self._opt_tool_label.setText(tool_button.label)
        self._opt_tool_label.setToolTip(tool_button.toolTip())
        self._brush_details.setVisible(t in (Tool.BRUSH, Tool.ERASER))
        self._clone_details.setVisible(t in (Tool.CLONE, Tool.HEAL))
        self._line_controls.setVisible(t==Tool.SHAPE_LINE)
        self._crop_controls.setVisible(t == Tool.CROP)
        self._selection_controls.setVisible(t in (Tool.SELECT_RECT, Tool.SELECT_ELLIPSE,
            Tool.SELECT_LASSO, Tool.POLY_LASSO, Tool.WAND, Tool.QUICK_SELECT, Tool.OBJECT_SELECT))
        self._wand_controls.setVisible(t == Tool.WAND)
        hints = {
            Tool.MOVE: "空白拖动框选 · Shift / Ctrl 点击多选 · Alt 拖动画板",
            Tool.TEXT: "文字：双击编辑；拖动移动，拖方块调整编辑框",
            Tool.CROP: "拖动预览 · Alt 中心裁剪",
            Tool.EYEDROPPER: "吸管：点击画布取色",
            Tool.POLY_LASSO: "多边形套索：逐点点击，双击结束",
            Tool.QUICK_SELECT: "拖动选择；Shift 添加，Alt 减去，Shift+Alt 交叉",
        }
        self._opt_hint.setText(hints.get(t, "选择工具后，在画布上拖动操作"))
        for w in (self._opt_hint, self._ob_brush_lbl, self._ob_brush,
                  self._ob_tol_lbl, self._ob_tol,
                  self._ob_feather_lbl, self._ob_feather,
                  self._ob_blur_lbl, self._ob_blur, self.fg_btn,
                  self._sh_fill_chk, self._sh_fill_btn,
                  self._sh_stroke_chk, self._sh_stroke_lbl,
                  self._sh_stroke_w, self._sh_stroke_btn,
                  self._sh_grad_chk, self._sh_grad_from_btn, self._sh_grad_to_btn):
            w.setVisible(False)
        if t in (Tool.BRUSH, Tool.ERASER, Tool.CLONE, Tool.HEAL, Tool.QUICK_SELECT):
            self._ob_brush_lbl.setVisible(True); self._ob_brush.setVisible(True)
            if t == Tool.BRUSH:
                self.fg_btn.setVisible(True)          # 橡皮擦成透明，不用前景色
        elif t in (Tool.WAND,Tool.OBJECT_SELECT):
            self._ob_tol_lbl.setVisible(True); self._ob_tol.setVisible(True)
        elif t in (Tool.SELECT_RECT, Tool.SELECT_ELLIPSE, Tool.SELECT_LASSO, Tool.POLY_LASSO):
            self._ob_feather_lbl.setVisible(True); self._ob_feather.setVisible(True)
            self._ob_blur_lbl.setVisible(True); self._ob_blur.setVisible(True)  # 选区模糊半径
        elif t in (Tool.SHAPE_RECT, Tool.SHAPE_ELLIPSE, Tool.SHAPE_LINE):
            # 形状工具不需要选区蚂蚁线 → 切过来就清掉旧选区
            if self.selection is not None:
                self.selection = None
                self.sel_alpha = None
                self._sel_base = None
                self._stop_sel_anim()
                self._redraw()
            # 形状工具：填充开关 + 填充色 / 描边开关 + 粗细 + 描边色 / 渐变开关 + 起止色
            self._sh_fill_chk.setVisible(True); self._sh_fill_btn.setVisible(True)
            self._sh_stroke_chk.setVisible(True); self._sh_stroke_lbl.setVisible(True)
            self._sh_stroke_w.setVisible(True); self._sh_stroke_btn.setVisible(True)
            self._sh_grad_chk.setVisible(True); self._sh_grad_from_btn.setVisible(True)
            self._sh_grad_to_btn.setVisible(True)
            self._sync_shape_options()  # 同步已选形状层的参数到选项条
        elif t == Tool.GRADIENT:
            # 渐变工具：必须能选起止色，否则无法决定渐变颜色
            self._sh_grad_from_btn.setVisible(True)
            self._sh_grad_to_btn.setVisible(True)
            self._opt_hint.setText("渐变：拖拽生成线性渐变，按住 Shift 画径向渐变")
            self._opt_hint.setVisible(True)
        else:
            self._opt_hint.setVisible(True)



    def _delete_selected(self):
        """删除当前选中的所有图层（一次撤销）。"""
        if not self.selected:
            return
        # 若正在编辑的文字层在删除集合中，先安全关闭（不写入内容）
        if self._text_editing and self._text_edit_layer in self.selected:
            self._end_text_edit(save=False)
        # 预取下一个候选层（删除前），删完后自动切换
        next_layer = self._find_next_after(self.selected)
        self._push_undo("删除图层")
        for layer in list(self.selected):
            parent = self._parent_list_of(layer)
            if layer in parent:
                parent.remove(layer)
        self.selected = []
        self.active = None
        if next_layer is not None:
            self.active = next_layer
            self.selected = [next_layer]
        self._show_handles = False   # 删除后清理变换手柄蓝框，避免残留
        self._refresh_layers()
        self._redraw()

    def _delete_layer(self, layer):
        # 若该层正在编辑，先关闭编辑态（不写入内容）
        if self._text_editing and self._text_edit_layer is layer:
            self._end_text_edit(save=False)
        parent = self._parent_list_of(layer)
        if layer in parent:
            self._push_undo("删除图层")
            parent.remove(layer)
            if layer in self.selected:
                self.selected.remove(layer)
            if self.active is layer:
                self.active = (self.selected[-1] if self.selected
                             else (self._ctx_layers()[-1] if self._ctx_layers() else None))
                if self.active is None:
                    self.active = self._find_next_after([layer])  # 确保总有候选
            self._show_handles = False   # 删除后清理变换手柄蓝框，避免残留
            self._refresh_layers()
            self._redraw()


    def _select_layer_interactive(self, layer):
        """图层面板点击选中：单选 / Ctrl 多选 / Shift 连续选（仿 PS）。
        由 LayerItemWidget / ArtboardHeaderWidget 的 mousePressEvent 触发，
        因为 setItemWidget 内的子控件会吃掉 itemClicked 信号。"""
        if layer is None:
            return
        mods = QApplication.keyboardModifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:          # Ctrl/⌘：切换该层
            self._toggle_layer_select(layer)
        elif mods & Qt.KeyboardModifier.ShiftModifier:         # Shift：连续选
            self._range_layer_select(layer)
        else:                                   # 普通：单选
            self._select_layer_only(layer)
        # 不重建 widget（避免点击/拖拽中销毁正在交互的目标），仅原地刷新高亮
        self._update_layer_highlights()
        # 画布联动：选中的图层在画布上即时显示变换手柄 / 轮廓 + 名称标签
        self._draw_handles()
        self.view.setFocus()

    def _on_list_double_click(self, item):
        """图层列表双击：文字层 → 直接编辑文字；其余层 → 等同单击选中。"""
        if item is None:
            return
        w = self.layer_list.itemWidget(item)
        if w is None:
            return
        if isinstance(w, ArtboardHeaderWidget):
            return
        layer = w.layer
        if layer.kind == "text":
            self.set_active(layer)
            self._start_text_edit(layer, is_new=False)
        elif layer.kind == "adjust":
            # P4 双击调整图层 → 打开参数对话框
            self._select_layer_only(layer)
            self._refresh_layers()
            self._edit_adjust_layer(layer)
        else:
            self._select_layer_only(layer)
            self._sync_props()

    def _layer_context_menu(self, pos):
        from PyQt6.QtWidgets import QMenu
        item = self.layer_list.itemAt(pos)
        if item is None:
            return
        w = self.layer_list.itemWidget(item)
        if w is None:
            return
        menu = QMenu(self)
        if isinstance(w, ArtboardHeaderWidget):
            rename_a = menu.addAction("重命名画板")
            addimg_a = menu.addAction("添加图片")
            addtxt_a = menu.addAction("添加文字")
            menu.addSeparator()
            del_a = menu.addAction("删除画板")
            action = menu.exec(self.layer_list.mapToGlobal(pos))
            if action == rename_a:
                self._rename_artboard(w.artboard)
            elif action == addimg_a:
                self._set_active_artboard(w.artboard); self.add_image_dialog()
            elif action == addtxt_a:
                self._set_active_artboard(w.artboard)
                self._start_text_edit(None, is_new=True)
            elif action == del_a:
                self._delete_artboard(w.artboard)
            return
        layer = w.layer
        del_action = menu.addAction("删除图层")
        folder_menu = menu.addMenu('移入文件夹')
        for folder in self._all_layers():
            if folder.kind == 'group' and folder not in list(walk_layers([layer])):
                folder_menu.addAction(folder.name, lambda _=False, destination=folder: self.move_layer_into(layer, destination))
        menu.addAction('移出到画板顶层', lambda: self.move_layer_into(layer))
        dup_action = menu.addAction("复制图层")
        # 文字层：编辑文字
        edit_text_a = None
        if layer.kind == "text":
            edit_text_a = menu.addAction("✎ 编辑文字")
        menu.addSeparator()
        # 剪切蒙版
        clip_label = "释放剪切蒙版" if layer.clip else "创建剪切蒙版"
        clip_action = menu.addAction(clip_label)
        # 图层蒙版
        mask_actions = []
        if layer.kind in ("image", "adjust", "text", "shape", "group"):
            if layer.mask is None:
                mask_actions.append(("添加图层蒙版", lambda: self._add_layer_mask(layer)))
            else:
                mask_actions.append(("删除图层蒙版", lambda: self._remove_layer_mask(layer)))
                if layer.kind == 'image':
                    mask_actions.append(("应用图层蒙版", lambda: self._apply_layer_mask(layer)))
                editing = getattr(self, "_mask_edit", False)
                mask_actions.append(
                    ("退出蒙版编辑" if editing else "进入蒙版编辑（画笔涂抹）",
                     lambda: self._toggle_mask_edit(layer)))
            for label, fn in mask_actions:
                menu.addAction(label, fn)
        menu.addSeparator()
        # AI 抠图仅对图片图层
        ai_action = None
        if layer.kind == "image" and layer.pixels is not None:
            ai_action = menu.addAction("🤖 AI抠图")
        # 栅格化（文字/形状转为像素图层）
        rasterize_a = None
        if layer.kind in ("text", "shape", "group", "svg"):
            rasterize_a = menu.addAction("栅格化图层")
        # P4 智能对象
        smart_a = None
        smart_edit_a = None
        if getattr(layer, "smart", False) and layer.smart_source:
            smart_edit_a = menu.addAction("📦 编辑智能对象内容")
        elif layer.kind != "adjust":
            smart_a = menu.addAction("📦 转换为智能对象")
        # P4 调整图层参数
        adjust_a = None
        if layer.kind == "adjust":
            adjust_a = menu.addAction("🎛 编辑调整参数...")
        # P4 编组
        group_a = None
        ungroup_a = None
        if len([ly for ly in self.selected if ly is not None]) >= 2:
            group_a = menu.addAction("🗂 图层编组 (Ctrl+G)")
        if layer.kind == 'group' or getattr(layer, "group_id", None) is not None:
            ungroup_a = menu.addAction("取消编组 (Ctrl+Shift+G)")
        # 翻转
        menu.addSeparator()
        flip_h_action = menu.addAction("水平翻转")
        flip_v_action = menu.addAction("垂直翻转")
        # 多选时才显示「合并图层」
        merge_a = None
        selected_in_ctx = [ly for ly in self.selected if ly is not None]
        if len(selected_in_ctx) >= 2 and layer in selected_in_ctx:
            merge_a = menu.addAction("🧩 合并选中图层")
        merge_vis_a = menu.addAction("合并可见图层")
        menu.addSeparator()
        # 锁定
        lock_label = "解锁图层" if getattr(layer, 'locked', False) else "锁定图层"
        lock_action = menu.addAction(lock_label)
        menu.addSeparator()
        add_media_a = menu.addAction("📤  添加到视频素材库")
        add_resource_a = menu.addAction("📚  保存到 AI 资产库")
        menu.addSeparator()
        menu.addAction("全选图层", self._select_all_layers)
        menu.addAction("反选图层", self._invert_layer_selection)
        menu.addAction("取消选择", self._clear_layer_selection)
        action = menu.exec(self.layer_list.mapToGlobal(pos))
        if action == del_action:
            self._delete_layer(layer)
        elif action == dup_action:
            self._duplicate_layer(layer)
        elif action is edit_text_a and edit_text_a is not None:
            self.set_active(layer)
            self._start_text_edit(layer, is_new=False)
        elif action == clip_action:
            self._toggle_clip(layer)
        elif action is ai_action and ai_action is not None:
            self.set_active(layer)
            self._ai_remove_bg()
        elif action is merge_a and merge_a is not None:
            self._merge_selected_layers()
        elif action is merge_vis_a and merge_vis_a is not None:
            self._merge_visible()
        elif action is rasterize_a and rasterize_a is not None:
            self._rasterize_layer(layer)
        elif action is smart_a and smart_a is not None:
            if layer not in self.selected:
                self._select_layer_only(layer)
            self._convert_to_smart_object()
        elif action is smart_edit_a and smart_edit_a is not None:
            self._edit_smart_object(layer)
        elif action is adjust_a and adjust_a is not None:
            self._edit_adjust_layer(layer)
        elif action is group_a and group_a is not None:
            self._group_selected()
        elif action is ungroup_a and ungroup_a is not None:
            self._select_layer_only(layer)
            self._ungroup_selected()
        elif action == flip_h_action:
            self._flip_horizontal(layer)
        elif action == flip_v_action:
            self._flip_vertical(layer)
        elif action == lock_action:
            self._push_undo("锁定图层" if not getattr(layer, 'locked', False) else "解锁图层")
            layer.locked = not getattr(layer, 'locked', False)
            self._refresh_layers()
        elif action is add_media_a and add_media_a is not None:
            self._add_layer_to_media_library(layer)
        elif action is add_resource_a and add_resource_a is not None:
            self._push_layer_to_resource_center(layer)

    def _export_layer_to_png(self, layer, path):
        """把单个图层渲染为带透明通道的 PNG（保留不透明度/样式/阴影），并裁剪到内容包围盒。"""
        img = self._render_layer_canvas(layer)        # 画布尺寸，透明背景
        arr = numpy_from_qimage(img)
        alpha = arr[:, :, 3]
        ys, xs = np.where(alpha > 0)
        if xs.size == 0 or ys.size == 0:
            crop = np.zeros((1, 1, 4), dtype=np.uint8)  # 全透明，保存 1x1 避免空图
        else:
            pad = 1
            x0 = max(0, int(xs.min()) - pad)
            y0 = max(0, int(ys.min()) - pad)
            x1 = min(arr.shape[1] - 1, int(xs.max()) + pad)
            y1 = min(arr.shape[0] - 1, int(ys.max()) + pad)
            crop = np.ascontiguousarray(arr[y0:y1 + 1, x0:x1 + 1])
        qimage_from_numpy(crop).save(path, "PNG")

    def _export_layers_for_drag(self, anchor):
        """Render selected layers to persistent images for other workbenches."""
        from uuid import uuid4
        from utils.app_paths import user_data_root

        selected = self.selected if anchor in self.selected else [anchor]
        layers = [layer for layer in self._ctx_layers()
                  if layer in selected and layer.kind in
                  {"image", "shape", "text", "svg", "group"}]
        if not layers:
            return []
        folder = user_data_root() / "dragged_layers"
        folder.mkdir(parents=True, exist_ok=True)
        paths = []
        try:
            for layer in layers:
                path = folder / f"layer_{uuid4().hex}.png"
                self._export_layer_to_png(layer, str(path))
                if not path.is_file():
                    raise OSError(f"无法保存图层图片：{layer.name}")
                paths.append(str(path))
        except Exception:
            import logging
            for path in paths:
                Path(path).unlink(missing_ok=True)
            logging.exception("export selected layers for drag failed")
            return []
        return paths

    def _add_layer_to_media_library(self, layer):
        """将当前图层导出为带透明通道的 PNG，并联动添加到视频素材库。"""
        import time as _time
        if layer is None or layer.kind not in ("image", "shape", "text"):
            return
        os.makedirs("work_temp", exist_ok=True)
        stamp = str(int(_time.time() * 1000))
        path = os.path.join("work_temp", f"layer_{stamp}.png")
        try:
            self._export_layer_to_png(layer, path)
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "错误", f"导出图层失败：{e}")
            return
        self.add_layer_to_media_requested.emit(path)

    def _push_layer_to_resource_center(self, layer):
        """导出当前图层，并选择新建/追加为角色或场景资产。"""
        import time as _time
        if layer is None or layer.kind not in ("image", "shape", "text"):
            return
        from utils.app_paths import work_root
        folder = work_root() / "resource_imports"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"resource_{int(_time.time() * 1000)}.png"
        try:
            self._export_layer_to_png(layer, str(path))
            from ai.ui.resource_center import import_assets_to_resource_center
            result = import_assets_to_resource_center(
                self, [str(path)], default_kind="character")
            if result:
                kind, items = result
                label = "角色" if kind == "character" else "场景"
                self.status_tool.setText(
                    f"已保存到 AI 资产库：{len(items)} 个{label}资产")
        except Exception as error:
            QMessageBox.warning(self, "推送失败", str(error))


    def _update_layer_highlights(self):
        """仅刷新图层面板选中高亮，不重建 widget。

        关键：点击/拖拽图层时若调用 _refresh_layers()（clear() 后重建所有
        LayerItemWidget），正在交互的 widget 会被销毁 → 表现为「点不动 / 拖不动」。
        这里原地更新现有 widget 的样式，widget 始终存活，交互不中断。"""
        for i in range(self.layer_list.count()):
            item = self.layer_list.item(i)
            w = self.layer_list.itemWidget(item)
            if isinstance(w, LayerItemWidget):
                w._apply_highlight()
            elif isinstance(w, ArtboardHeaderWidget):
                w.refresh()
        self.layer_count.setText(str(len(self._all_layers())))
        self._sync_props()

    # ═══════ 混合模式映射 ─────────────────
    BLEND_MODES = {
        "正常": "normal", "变暗": "darken", "正片叠底": "multiply",
        "颜色加深": "color_burn", "线性加深": "linear_burn",
        "变亮": "lighten", "滤色": "screen", "颜色减淡": "color_dodge",
        "线性减淡": "linear_dodge", "叠加": "overlay", "柔光": "soft_light",
        "强光": "hard_light", "亮光": "vivid_light", "线性光": "linear_light",
        "差值": "difference", "排除": "exclusion",
        "色相": "hue", "饱和度": "saturation", "颜色": "color", "明度": "luminosity",
    }
    BLEND_REVERSE = {v: k for k, v in BLEND_MODES.items()}
    # Qt 原生支持的混合模式（QPainter CompositionMode）
    _QT_BLEND = {
        "darken": QPainter.CompositionMode.CompositionMode_Darken,
        "multiply": QPainter.CompositionMode.CompositionMode_Multiply,
        "color_burn": QPainter.CompositionMode.CompositionMode_ColorBurn,
        "lighten": QPainter.CompositionMode.CompositionMode_Lighten,
        "screen": QPainter.CompositionMode.CompositionMode_Screen,
        "color_dodge": QPainter.CompositionMode.CompositionMode_ColorDodge,
        "linear_dodge": QPainter.CompositionMode.CompositionMode_Plus,
        "overlay": QPainter.CompositionMode.CompositionMode_Overlay,
        "soft_light": QPainter.CompositionMode.CompositionMode_SoftLight,
        "hard_light": QPainter.CompositionMode.CompositionMode_HardLight,
        "difference": QPainter.CompositionMode.CompositionMode_Difference,
        "exclusion": QPainter.CompositionMode.CompositionMode_Exclusion,
    }
    # 需 numpy 实现的模式（Qt 无对应）
    _NP_BLEND = {"linear_burn", "vivid_light", "linear_light",
                 "hue", "saturation", "color", "luminosity"}

    @staticmethod
    def _blend_hsl_component(cb, cs, mode):
        """PDF/W3C 规范的 HSL 分量混合（hue/saturation/color/luminosity）。"""
        def lum(c):
            return 0.3 * c[..., 0:1] + 0.59 * c[..., 1:2] + 0.11 * c[..., 2:3]

        def clip_color(c):
            l = lum(c)
            mn = c.min(axis=-1, keepdims=True)
            mx = c.max(axis=-1, keepdims=True)
            c = np.where(mn < 0, l + (c - l) * l / np.maximum(l - mn, 1e-5), c)
            c = np.where(mx > 1, l + (c - l) * (1 - l) / np.maximum(mx - l, 1e-5), c)
            return np.clip(c, 0, 1)

        def set_lum(c, l):
            return clip_color(c + (l - lum(c)))

        def sat(c):
            return c.max(axis=-1, keepdims=True) - c.min(axis=-1, keepdims=True)

        def set_sat(c, s):
            mx = c.max(axis=-1, keepdims=True)
            mn = c.min(axis=-1, keepdims=True)
            rng = np.maximum(mx - mn, 1e-5)
            res = (c - mn) / rng * s
            return np.where(mx > mn, res, 0.0)

        if mode == "hue":
            return set_lum(set_sat(cs, sat(cb)), lum(cb))
        if mode == "saturation":
            return set_lum(set_sat(cb, sat(cs)), lum(cb))
        if mode == "color":
            return set_lum(cs, lum(cb))
        return set_lum(cb, lum(cs))  # luminosity

    def _numpy_blend(self, dest, src, mode):
        """numpy 实现的混合模式合成（W3C compositing 公式，直通 alpha）。"""
        d = numpy_from_qimage(dest).astype(np.float32) / 255.0
        s = numpy_from_qimage(src).astype(np.float32) / 255.0
        cb, ca = d[:, :, :3], d[:, :, 3:4]   # backdrop 颜色/alpha
        cs, sa = s[:, :, :3], s[:, :, 3:4]   # source 颜色/alpha
        eps = 1e-5
        if mode == "linear_burn":
            b = np.clip(cb + cs - 1.0, 0, 1)
        elif mode == "linear_light":
            b = np.clip(cb + 2.0 * cs - 1.0, 0, 1)
        elif mode == "vivid_light":
            burn = 1.0 - np.minimum(1.0, (1.0 - cb) / np.maximum(2.0 * cs, eps))
            dodge = np.minimum(1.0, cb / np.maximum(2.0 * (1.0 - cs), eps))
            b = np.where(cs <= 0.5, burn, dodge)
        elif mode in ("hue", "saturation", "color", "luminosity"):
            b = self._blend_hsl_component(cb, cs, mode)
        else:
            b = cs
        mixed = (1.0 - ca) * cs + ca * b            # 混合结果按 backdrop alpha 插值
        out_a = sa + ca * (1.0 - sa)
        out_rgb = (sa * mixed + ca * cb * (1.0 - sa)) / np.maximum(out_a, eps)
        res = np.zeros_like(d)
        res[:, :, :3] = np.clip(out_rgb, 0, 1)
        res[:, :, 3:4] = out_a
        return qimage_from_numpy((res * 255.0 + 0.5).astype(np.uint8))

    def _set_blend_mode(self, text):
        if self.active:
            value = self.BLEND_MODES.get(text, "normal")
            if self.active.blend == value:
                return
            self._push_undo("更改混合模式")
            self.active.blend = value
            self._redraw()

    def _sync_props(self):
        self._sync_gif_controls()
        self._text_property_panel.sync(self.active)
        self._adjust_property_panel.sync(self.active)
        if hasattr(self, "_active_context"):
            layer = self.active
            editing = bool(layer and layer.mask is not None and self._mask_edit)
            self._active_context.setText(
                ("正在编辑蒙版 · " if editing else "当前图层 · ") + layer.name
                if layer else "未选择图层 · 点击画布或图层列表选择")
            self._mask_exit.setVisible(editing)
            self.blend_combo.setEnabled(layer is not None)
            self.op_slider.setEnabled(layer is not None)
            self.style_box.setEnabled(layer is not None)
        if self.active:
            v = int(self.active.opacity * 100)
            self.op_slider.blockSignals(True)
            self.op_slider.setValue(v)
            self.op_slider.blockSignals(False)
            self._sync_opacity_widgets(v)
            # 同步混合模式下拉
            key = self.BLEND_REVERSE.get(self.active.blend, "正常")
            self.blend_combo.blockSignals(True)
            self.blend_combo.setCurrentText(key)
            self.blend_combo.blockSignals(False)
        # 画板模式下「透明」勾选框反映的是【当前激活画板】的透明状态，
        # 否则反映项目级透明。此前误用 project.transparent，导致进入画板后
        # 勾选框初始状态与画板实际状态错位 → 需连点两下才生效，且各画板不独立。
        self.trans_chk.blockSignals(True)
        self.trans_chk.setChecked(
            self.active_artboard.transparent if self.active_artboard is not None
            else self.project.transparent)
        self.trans_chk.blockSignals(False)
        self._sync_style()

    def _begin_opacity_change(self):
        """保存拖动前状态；一次连续拖动只生成一条历史记录。"""
        self._opacity_pre_snap = self._snapshot() if self.active else None
        self._opacity_start_value = self.active.opacity if self.active else None

    def _end_opacity_change(self):
        snap = getattr(self, "_opacity_pre_snap", None)
        start = getattr(self, "_opacity_start_value", None)
        self._opacity_pre_snap = None
        self._opacity_start_value = None
        if snap is not None and self.active is not None and self.active.opacity != start:
            self._push_undo_snapshot("调整图层不透明度", snap)

    def _set_active_opacity(self, v):
        if self.active:
            value = v / 100.0
            if self.active.opacity == value:
                return
            # 键盘单步调整不会触发 sliderPressed，仍然记录本次操作。
            if getattr(self, "_opacity_pre_snap", None) is None:
                self._push_undo("调整图层不透明度")
            self.active.opacity = value
            self._sync_opacity_widgets(v)
            self._redraw()

    def _sync_opacity_widgets(self, v):
        """双滑杆（属性面板 + 图层面板）同步，防信号回环"""
        for s in (self.op_slider, getattr(self, "layer_op_slider", None)):
            if s is not None and s.value() != v:
                s.blockSignals(True); s.setValue(v); s.blockSignals(False)
        if hasattr(self, "layer_op_label"):
            self.layer_op_label.setText(f"{v}%")

    def _set_canvas_size(self, w, h, push_undo=False):
        if push_undo:
            self._push_undo("画布尺寸")
        # 画板模式：调整的是当前激活画板的尺寸
        if self.active_artboard is not None:
            ab = self.active_artboard
            ab.w, ab.h = w, h
            for l in ab.layers:
                if l.kind in ("text", "shape"):
                    l.w, l.h = w, h
            self.selection = None
            self.sel_alpha = None
            self._sel_base = None
            self.cw.setValue(w); self.ch.setValue(h)
            self.status_size.setText(f"{w} × {h}")
            self._redraw()
            self._redraw_selection()
            self._request_fit()
            return
        self.project.w, self.project.h = w, h
        for l in self.project.layers:
            if l.kind in ("text", "shape"):
                l.w, l.h = w, h
        # 场景四周留白，保证可平移到画布外空白（不受 sceneRect 限制）
        M = self.view.PAN_MARGIN
        self.view.scene.setSceneRect(-M, -M, w + 2 * M, h + 2 * M)
        # 画布尺寸变化后旧像素选区形状不再匹配，清空避免错位
        self.selection = None
        self.sel_alpha = None
        self._sel_base = None
        self.cw.setValue(w); self.ch.setValue(h)
        self._redraw()
        self._redraw_selection()
        self._request_fit()

    def _apply_canvas_size(self):
        self._set_canvas_size(self.cw.value(), self.ch.value(), push_undo=True)

    def _set_transparent(self, on):
        current = (self.active_artboard.transparent if self.active_artboard is not None
                   else self.project.transparent)
        if current == on:
            return
        self._push_undo("切换透明背景")
        if self.active_artboard is not None:
            self.active_artboard.transparent = on
        else:
            self.project.transparent = on
        self._redraw()

    # ═══════ 样式 / 处理 处理器 ═══════
    def _set_clip(self, on):
        if self.active:
            if self.active.clip == on:
                return
            self._push_undo("切换剪切属性")
            self.active.clip = on
            self._redraw()

    def _blur_sel(self):
        self._blur_selection(self.blur_radius)

    def _feather_sel(self):
        self._feather_selection(self.feather)

    def _on_feather_slider(self, v):
        if self.selection is not None and v != self.feather:
            self._queue_property_undo('羽化选区')
        self.feather = v
        if self.selection is not None:
            self._selection_history_depth = getattr(self, '_selection_history_depth', 0) + 1
            try:
                self._feather_selection(v)
            finally:
                self._selection_history_depth -= 1

    def _build_style_box(self):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        # 文字色（仅文字图层可见；形状层的填充/渐变已移到顶部选项条，避免重复与拥挤）
        ht = QHBoxLayout()
        self.text_color_btn = QPushButton("文字色")
        self.text_color_btn.setFixedHeight(24)
        self.text_color_btn.setStyleSheet(
            "QPushButton{background:#3d8ef8;color:#fff;border:1px solid #555;border-radius:2px;padding:2px 6px;}"
            "QPushButton:hover{border-color:#aaa;}")
        self.text_color_btn.clicked.connect(lambda: self._pick_style_color("text_color", self.text_color_btn))
        ht.addWidget(self.text_color_btn, 1)
        lay.addLayout(ht)
        hr = QHBoxLayout()
        hr.addWidget(QLabel("圆角"))
        self.radius_spin = QSpinBox()
        self.radius_spin.setRange(0, 400)
        self.radius_spin.valueChanged.connect(self._set_radius)
        hr.addWidget(self.radius_spin, 1)
        lay.addLayout(hr)
        # 描边开关 + 粗细 + 描边色
        hst = QHBoxLayout()
        self.stroke_on_chk = QCheckBox("描边")
        self.stroke_on_chk.toggled.connect(self._set_stroke_on_shape)
        hst.addWidget(self.stroke_on_chk)
        hst.addWidget(QLabel("宽"))
        self.stroke_spin = QSpinBox()
        self.stroke_spin.setRange(0, 100)
        self.stroke_spin.valueChanged.connect(self._set_stroke_w)
        hst.addWidget(self.stroke_spin, 1)
        self.stroke_color_btn = QPushButton("描边色")
        self.stroke_color_btn.setFixedHeight(24)
        self.stroke_color_btn.setStyleSheet(
            "QPushButton{background:#000;color:#fff;border:1px solid #555;border-radius:2px;padding:2px 6px;}"
            "QPushButton:hover{border-color:#aaa;}")
        self.stroke_color_btn.clicked.connect(lambda: self._pick_style_color("stroke_target", self.stroke_color_btn))
        hst.addWidget(self.stroke_color_btn)
        lay.addLayout(hst)
        self.shadow_chk = QCheckBox("阴影")
        self.shadow_chk.toggled.connect(self._set_shadow)
        lay.addWidget(self.shadow_chk)
        hs = QHBoxLayout()
        hs.addWidget(QLabel("偏X"))
        self.sdx = QSpinBox(); self.sdx.setRange(-100, 100); self.sdx.valueChanged.connect(self._set_shadow_dx)
        hs.addWidget(self.sdx)
        hs.addWidget(QLabel("偏Y"))
        self.sdy = QSpinBox(); self.sdy.setRange(-100, 100); self.sdy.valueChanged.connect(self._set_shadow_dy)
        hs.addWidget(self.sdy)
        lay.addLayout(hs)
        hsb = QHBoxLayout()
        hsb.addWidget(QLabel("模糊"))
        self.sblur = QSpinBox(); self.sblur.setRange(0, 60); self.sblur.valueChanged.connect(self._set_shadow_blur)
        hsb.addWidget(self.sblur)
        hsb.addWidget(QLabel("透明"))
        self.sop = QSlider(Qt.Orientation.Horizontal); self.sop.setRange(0, 100)
        self.sop.valueChanged.connect(self._set_shadow_op)
        hsb.addWidget(self.sop, 1)
        lay.addLayout(hsb)
        self.shadow_color_btn = QPushButton("阴影色")
        self.shadow_color_btn.setFixedHeight(24)
        self.shadow_color_btn.setStyleSheet(
            "QPushButton{background:#000;color:#fff;border:1px solid #555;border-radius:2px;padding:2px 6px;}"
            "QPushButton:hover{border-color:#aaa;}")
        self.shadow_color_btn.clicked.connect(lambda: self._pick_style_color("shadow_color", self.shadow_color_btn))
        lay.addWidget(self.shadow_color_btn)
        # ── P5 文字渐变填充（仅文字图层显示；形状的渐变在顶部选项条）──
        self.grad_chk = QCheckBox("渐变填充")
        self.grad_chk.toggled.connect(self._set_text_gradient_on)
        lay.addWidget(self.grad_chk)
        hgf = QHBoxLayout()
        self.grad_from_btn = QPushButton("起始")
        self.grad_from_btn.setFixedHeight(24)
        self.grad_from_btn.setStyleSheet(self._style_color_btn_css("#3d8ef8"))
        self.grad_from_btn.clicked.connect(lambda: self._pick_style_color("grad_from", self.grad_from_btn))
        hgf.addWidget(self.grad_from_btn)
        self.grad_to_btn = QPushButton("结束")
        self.grad_to_btn.setFixedHeight(24)
        self.grad_to_btn.setStyleSheet(self._style_color_btn_css("#00eaff"))
        self.grad_to_btn.clicked.connect(lambda: self._pick_style_color("grad_to", self.grad_to_btn))
        hgf.addWidget(self.grad_to_btn)
        lay.addLayout(hgf)
        hga = QHBoxLayout()
        hga.addWidget(QLabel("角度"))
        self.grad_angle_spin = QSpinBox()
        self.grad_angle_spin.setRange(-180, 180)
        self.grad_angle_spin.valueChanged.connect(self._set_text_grad_angle)
        hga.addWidget(self.grad_angle_spin, 1)
        lay.addLayout(hga)
        return box

    @staticmethod
    def _style_color_btn_css(color):
        return (f"QPushButton{{background:{color};color:#fff;border:1px solid #555;"
                "border-radius:2px;padding:2px 6px;}}"
                "QPushButton:hover{border-color:#aaa;}")

    def _set_stroke_on_shape(self, on):
        if self.active and self.active.kind in ("shape", "text"):
            if self.active.stroke_on == on:
                return
            self._queue_property_undo("切换图层描边")
            self.active.stroke_on = on
            if on and self.active.stroke_w < 1:
                self.active.stroke_w = 1
                self.stroke_spin.setValue(1)
            self._redraw()

    def _set_radius(self, v):
        if self.active and self.active.kind == "shape":
            if self.active.radius == v:
                return
            self._queue_property_undo("调整圆角")
            self.active.radius = v
            self._redraw()

    def _set_stroke_w(self, v):
        if self.active and self.active.kind in ("shape", "text"):
            if self.active.stroke_w == v:
                return
            self._queue_property_undo("调整描边宽度")
            self.active.stroke_w = v
            self._redraw()

    def _set_shadow(self, on):
        if self.active:
            if self.active.shadow == on:
                return
            self._queue_property_undo("切换投影")
            self.active.shadow = on
            self._redraw()

    def _set_shadow_dx(self, v):
        if self.active:
            if self.active.shadow_dx == v:
                return
            self._queue_property_undo("调整投影位置")
            self.active.shadow_dx = v
            self._redraw()

    def _set_shadow_dy(self, v):
        if self.active:
            if self.active.shadow_dy == v:
                return
            self._queue_property_undo("调整投影位置")
            self.active.shadow_dy = v
            self._redraw()

    def _set_shadow_blur(self, v):
        if self.active:
            if self.active.shadow_blur == v:
                return
            self._queue_property_undo("调整投影模糊")
            self.active.shadow_blur = v
            self._redraw()

    def _set_shadow_op(self, v):
        if self.active:
            if self.active.shadow_opacity == v / 100.0:
                return
            self._queue_property_undo("调整投影不透明度")
            self.active.shadow_opacity = v / 100.0
            self._redraw()

    # ── P5 文字渐变填充控制 ──
    def _set_text_gradient_on(self, on):
        if self.active and self.active.kind == "text":
            if self.active.gradient == on:
                return
            self._queue_property_undo("切换文字渐变")
            self.active.gradient = on
            self._redraw()

    def _set_text_grad_angle(self, v):
        if self.active and self.active.kind == "text":
            if self.active.grad_angle == v:
                return
            self._queue_property_undo("调整渐变角度")
            self.active.grad_angle = v
            self._redraw()

    def _pick_style_color(self, target, btn):
        c = choose_color(self._style_color_current(target), self, '图层样式颜色')
        if not c.isValid():
            return
        self._push_undo("更改图层样式颜色")
        if target == "grad_from" and self.active:
            self.active.grad_from = c.name()
        elif target == "grad_to" and self.active:
            self.active.grad_to = c.name()
        elif target == "stroke_target" and self.active:
            if self.active.kind == "text":
                self.active.stroke_color = c.name()
            else:
                self.active.color = c.name()
        elif target == "text_color" and self.active:
            self.active.color = c.name()
        elif target == "shadow_color" and self.active:
            self.active.shadow_color = c.name()
        btn.setStyleSheet(
            f"QPushButton{{background:{c.name()};color:#fff;border:1px solid #555;border-radius:2px;padding:2px 6px;}}"
            "QPushButton:hover{border-color:#aaa;}")
        self._redraw()

    def _style_color_current(self, target):
        if not self.active:
            return QColor("#000000")
        if target == "grad_from":
            return QColor(self.active.grad_from)
        if target == "grad_to":
            return QColor(self.active.grad_to)
        if target == "stroke_target":
            return QColor(self.active.stroke_color if self.active.kind == "text" else self.active.color)
        if target == "text_color":
            return QColor(self.active.color)
        if target == "shadow_color":
            return QColor(self.active.shadow_color)
        return QColor("#000000")

    def _sync_style(self):
        a = self.active
        self.style_box.setVisible(a is not None and a.kind in ("shape", "text"))
        if a is None:
            return
        if a.kind not in ("shape", "text"):
            return
        self.text_color_btn.setVisible(a.kind == "text")
        self.text_color_btn.setStyleSheet(
            f"QPushButton{{background:{a.color};color:#fff;border:1px solid #555;border-radius:2px;padding:2px 6px;}}"
            "QPushButton:hover{border-color:#aaa;}")
        self.radius_spin.setValue(a.radius)
        self.stroke_on_chk.setChecked(a.stroke_on)
        self.stroke_spin.setValue(a.stroke_w)
        self.stroke_color_btn.setStyleSheet(
            f"QPushButton{{background:{a.stroke_color if a.kind == 'text' else a.color};color:#fff;"
            "border:1px solid #555;border-radius:2px;padding:2px 6px;}"
            "QPushButton:hover{border-color:#aaa;}")
        self.shadow_chk.setChecked(a.shadow)
        self.sdx.setValue(a.shadow_dx)
        self.sdy.setValue(a.shadow_dy)
        self.sblur.setValue(a.shadow_blur)
        self.sop.setValue(int(a.shadow_opacity * 100))
        self.shadow_color_btn.setStyleSheet(
            f"QPushButton{{background:{a.shadow_color};color:#fff;border:1px solid #555;border-radius:2px;padding:2px 6px;}}"
            "QPushButton:hover{border-color:#aaa;}")
        # P5 文字渐变填充控件（仅文字图层可见）
        is_text = a.kind == "text"
        self.grad_chk.setVisible(is_text)
        self.grad_from_btn.setVisible(is_text)
        self.grad_to_btn.setVisible(is_text)
        self.grad_angle_spin.setVisible(is_text)
        if is_text:
            self.grad_chk.setChecked(a.gradient)
            self.grad_from_btn.setStyleSheet(self._style_color_btn_css(a.grad_from))
            self.grad_to_btn.setStyleSheet(self._style_color_btn_css(a.grad_to))
            self.grad_angle_spin.setValue(int(getattr(a, "grad_angle", 0)))


    # ═══════ 渲染 ═══════
    def _layer_content(self, layer):
        if layer.kind in ("image", "svg"):
            return qimage_from_numpy(layer.pixels)
        if layer.kind == "text":
            return self._render_text(layer)
        if layer.kind == "shape":
            return self._render_shape(layer)
        return None

    def _layer_thumbnail(self, layer, size=38):
        """生成图层缩略图（居中、保持透明通道），用于图层面板列表。"""
        try:
            content = self._layer_content(layer)
        except Exception:
            return QPixmap()
        if content is None:
            return QPixmap()
        cw, ch = content.width(), content.height()
        if cw <= 0 or ch <= 0:
            return QPixmap()
        scale = min(size / cw, size / ch, 1.5)
        tw, th = max(1, int(cw * scale)), max(1, int(ch * scale))
        thumb = content.scaled(tw, th, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        result = QImage(size, size, QImage.Format.Format_ARGB32)
        result.fill(Qt.GlobalColor.transparent)
        p = QPainter(result)
        p.drawImage((size - tw) // 2, (size - th) // 2, thumb)
        p.end()
        return QPixmap.fromImage(result)

    def _render_text(self, layer):
        return render_text(layer)

    def _render_shape(self, layer):
        img = QImage(max(1, layer.w), max(1, layer.h), QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if layer.stroke_on and layer.stroke_w > 0:
            pen = QPen(QColor(layer.color))
            pen.setWidth(layer.stroke_w)
            p.setPen(pen)
        else:
            p.setPen(Qt.PenStyle.NoPen)
        if layer.gradient and layer.filled:
            g = QLinearGradient(0, 0, layer.w, layer.h)
            g.setColorAt(0, QColor(layer.grad_from))
            g.setColorAt(1, QColor(layer.grad_to))
            p.setBrush(QBrush(g))
        elif layer.filled:
            p.setBrush(QBrush(QColor(layer.fill_color)))
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
        r = layer.rect
        if layer.shape == 'line':
            pen = QPen(QColor(layer.color if layer.stroke_on else layer.fill_color),max(1,layer.stroke_w or 3))
            pen.setStyle(Qt.PenStyle(layer.line_style))
            pen.setCapStyle(Qt.PenCapStyle(layer.line_cap))
            p.setPen(pen)
            p.drawLine(QPointF(r.x()+r.width()*layer.line_start[0],r.y()+r.height()*layer.line_start[1]),
                       QPointF(r.x()+r.width()*layer.line_end[0],r.y()+r.height()*layer.line_end[1]))
        elif layer.shape == "rect":
            if layer.radius > 0:
                p.drawRoundedRect(r, layer.radius, layer.radius)
            else:
                p.drawRect(r)
        else:
            p.drawEllipse(r)
        p.end()
        return img

    # ───────────────────────────── 模糊（OpenCV GaussianBlur，numpy 兜底） ─────────────────────────────
    def blur_qimage(self, img, radius):
        arr = numpy_from_qimage(img)
        try:
            import cv2
            k = max(3, int(round(radius * 2)) + 1)
            if k % 2 == 0:
                k += 1
            sigma = max(0.5, radius / 2.0)
            b = cv2.GaussianBlur(arr, (k, k), sigma)
        except Exception:
            b = self._gaussian_np(arr, max(0.5, radius / 2.0))
        return qimage_from_numpy(b)

    @staticmethod
    def _gaussian_np(arr, sigma, k=5):
        # 可分离 1D 高斯，numpy 兜底
        kernel = np.exp(-(np.arange(-k, k + 1) ** 2) / (2 * sigma * sigma))
        kernel /= kernel.sum()
        out = arr.astype(np.float32)
        for c in range(arr.shape[2]):
            out[:, :, c] = np.apply_along_axis(
                lambda row: np.convolve(row, kernel, mode="same"), 1,
                np.apply_along_axis(
                    lambda col: np.convolve(col, kernel, mode="same"), 0, out[:, :, c]))
        return out.astype(np.uint8)


    def _draw_blended(self, out, p, img, layer):
        """按图层混合模式把 img 画到 out 上；返回（可能被替换的）out 和 painter。"""
        mode = getattr(layer, "blend", "normal")
        if mode in self._NP_BLEND:
            p.end()
            out = self._numpy_blend(out, img, mode)
            p = QPainter(out)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
        elif mode in self._QT_BLEND:
            p.save()
            p.setCompositionMode(self._QT_BLEND[mode])
            p.drawImage(0, 0, img)
            p.restore()
        else:
            p.drawImage(0, 0, img)
        return out, p

    # ───────── 画板模式渲染 ─────────
    _AB_MARGIN = 200   # 画板四周留白宽度

    def _ab_translate(self):
        """画板坐标映射到文档坐标的固定平移量。
        返回常量 (MARGIN, MARGIN)：移动 / 拖动单个画板时，其它画板位置【不变】
        （不再整体重居中、不再因 min 偏移而漂移），实现「想挪到哪里就在哪里」。"""
        return self._AB_MARGIN, self._AB_MARGIN

    def _ab_screen(self, ab):
        """画板在文档中的渲染位置 (scaled by _ab_translate)。"""
        tx, ty = self._ab_translate()
        return int(ab.x + tx), int(ab.y + ty)

    def _ab_untranslate(self, pt):
        """文档坐标 → 画板本地坐标（去掉_ab_translate平移）。"""
        tx, ty = self._ab_translate()
        return QPointF(pt.x() - tx, pt.y() - ty)

    def _doc_bounds(self):
        """文档尺寸 = 所有画板包围盒 + 四边等距边距。
        结果会被缓存；添加 / 删除 / 拖动画板时通过 _invalidate_doc_bounds 清除缓存。
        平移固定为 (MARGIN, MARGIN)，故移动单个画板不会令其它画板漂移。"""
        if not self.project.artboards:
            return self.project.w, self.project.h
        cached = getattr(self, '_cached_doc_bounds', None)
        if cached is not None:
            return cached
        right = max(ab.x + ab.w for ab in self.project.artboards)
        bottom = max(ab.y + ab.h for ab in self.project.artboards)
        m = self._AB_MARGIN
        w = int(right + 2 * m)
        h = int(bottom + 2 * m)
        self._cached_doc_bounds = (w, h)
        return w, h

    def _invalidate_doc_bounds(self):
        """添加/删除/拖动画板时调用，使缓存失效（下次 _doc_bounds() 重新计算）。"""
        self._cached_doc_bounds = None


    def _render_doc_composite(self, for_export=False):
        dw, dh = self._doc_bounds()
        out = QImage(dw, dh, QImage.Format.Format_ARGB32)
        # 编辑/导出均用透明底：画板外区域不再填实色深灰「页面」，
        # 否则那块深色底会被误认成限制画板下移的「约束框」，且会随画板拖动裁切内容。
        # 画板悬浮在视图中性背景上，可自由摆放到任意位置；画板标识由 _draw_artboards 的
        # 边框线 + 名称标签框承担。
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 画板居中：固定平移量（移动单个画板不影响其它画板）
        tx, ty = self._ab_translate()
        for ab in self.project.artboards:
            x, y = int(ab.x + tx), int(ab.y + ty)
            # 编辑模式下：透明画板用棋盘格显示，直观表达「透明」（导出仍纯透明）
            if not for_export and ab.transparent:
                p.save()
                p.translate(x, y)
                self._paint_checker(p, ab.w, ab.h)
                p.restore()
            buf = self._render_artboard(ab)
            p.drawImage(x, y, buf)
        p.end()
        return out

    def _render_layer_canvas(self, layer, cw=None, ch=None):
        if layer.kind == 'group':
            return self.render_folder(layer, cw, ch)
        """把单个层按自身变换/不透明度渲染到指定尺寸 QImage（默认整画布）。"""
        # 正在编辑的文字层：合成时留透明，避免与编辑态叠加框（含相同文字）重复绘制
        if self._text_editing and layer is self._text_edit_layer:
            img = QImage(cw or self._ctx_size()[0], ch or self._ctx_size()[1],
                         QImage.Format.Format_ARGB32)
            img.fill(Qt.GlobalColor.transparent)
            return img
        if cw is None:
            cw, ch = self._ctx_size()
        img = QImage(cw, ch, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(1.)
        p.setTransform(layer.canvas_transform())
        content = self._layer_content(layer)
        if content is not None:
            if layer.kind == 'svg' and layer.svg_source:
                import base64
                from PyQt6.QtCore import QByteArray
                from PyQt6.QtSvg import QSvgRenderer
                renderer = QSvgRenderer(QByteArray(base64.b64decode(layer.svg_source)))
                renderer.render(p, QRectF(0, 0, layer.w, layer.h))
            else:
                p.drawImage(0, 0, content)
        p.end()
        from core.image_layer_effects import has_layer_effects
        if layer.mask is None and layer.opacity == 1. and not has_layer_effects(layer):
            return img
        # P3 图层蒙版：应用到 alpha 通道
        if layer.mask is not None:
            arr = numpy_from_qimage(img)
            mh, mw = layer.mask.shape
            if mh == arr.shape[0] and mw == arr.shape[1]:
                mask_f = layer.mask.astype(np.float32) / 255.0
                arr[:, :, 3] = (arr[:, :, 3].astype(np.float32) * mask_f).astype(np.uint8)
                img = qimage_from_numpy(arr)
        return qimage_from_numpy(apply_layer_effects(numpy_from_qimage(img), layer))

    # ═══════ P4 调整图层 ═══════
    def _apply_adjustment(self, img, layer):
        """把调整图层的色彩调整应用到累积合成结果上（不透明度=强度）。"""
        adj = layer.adjust or {}
        arr = numpy_from_qimage(img)
        if adj.get('type') == 'filter':
            filtered = self._filter_apply(arr, adj['filter'], adj.get('params', {}))
            strength = max(0., min(1., layer.opacity))
            if layer.mask is not None and layer.mask.shape == arr.shape[:2]:
                strength *= layer.mask[:, :, None].astype(np.float32) / 255
            return qimage_from_numpy(mix_rgba(arr, filtered, strength))
        orig_rgb = arr[:, :, :3].copy()
        rgb = arr[:, :, :3].astype(np.float32)
        t = adj.get("type", "brightness_contrast")
        if t in ADJUSTMENTS:
            rgb = apply_extra_adjustment(rgb, t, adj, arr[:, :, 3])
        elif t == "brightness_contrast":
            b = float(adj.get("brightness", 0))   # -100..100
            c = float(adj.get("contrast", 0))     # -100..100
            rgb = (rgb - 127.5) * (1.0 + c / 100.0) + 127.5 + b * 1.275
            rgb = np.clip(rgb, 0, 255)
        elif t == "levels":
            black = float(adj.get("black", 0)) / 255.0
            white = float(adj.get("white", 255)) / 255.0
            gamma = max(0.1, float(adj.get("gamma", 100)) / 100.0)
            # 把 [black, white] 线性拉伸到 [0,1]，再做 gamma
            if white > black:
                rgb = (rgb / 255.0 - black) / (white - black)
                rgb = np.clip(rgb, 0, 1)
                rgb = np.power(rgb, 1.0 / gamma)
                rgb = np.clip(rgb, 0, 1) * 255.0
        elif t == "curves":
            sh = float(adj.get("shadows", 0)) / 100.0
            mid = float(adj.get("midtones", 0)) / 100.0
            hi = float(adj.get("highlights", 0)) / 100.0
            # 简化 S 形曲线：阴影/中间调/高光三段线性偏移
            n = rgb.shape[0] * rgb.shape[1]
            lum = rgb[:, :, 0] * 0.299 + rgb[:, :, 1] * 0.587 + rgb[:, :, 2] * 0.114
            lum = np.clip(lum / 255.0, 0, 1)
            # 权重：阴影=1-lum（低端），高光=lum（高端），中间调=钟形
            w_sh = (1 - lum)
            w_hi = lum
            w_mid = 4 * lum * (1 - lum)
            delta = (sh * w_sh + mid * w_mid + hi * w_hi) * 80.0
            rgb = np.clip(rgb + delta[:, :, None], 0, 255)
            if adj.get('channels'):
                rgb = apply_curves(rgb, adj['channels'])
        elif t == "white_balance":
            temp = float(adj.get("temperature", 0)) / 100.0   # 负=冷(蓝)，正=暖(黄)
            tint = float(adj.get("tint", 0)) / 100.0          # 负=绿，正=洋红
            r = rgb[:, :, 0] + temp * 30.0 + tint * 15.0
            g = rgb[:, :, 1] - tint * 15.0
            b = rgb[:, :, 2] - temp * 30.0 + tint * 15.0
            rgb = np.clip(np.stack([r, g, b], axis=2), 0, 255)
        elif t == "color_balance":
            cr = float(adj.get("cyan_red", 0)) / 100.0        # 负=青，正=红
            mg = float(adj.get("mag_green", 0)) / 100.0       # 负=洋红，正=绿
            yb = float(adj.get("yel_blue", 0)) / 100.0        # 负=黄，正=蓝
            r = rgb[:, :, 0] + cr * 40.0 + yb * 20.0
            g = rgb[:, :, 1] + mg * 40.0 - yb * 20.0
            b = rgb[:, :, 2] - cr * 40.0 - mg * 40.0
            rgb = np.clip(np.stack([r, g, b], axis=2), 0, 255)
        else:  # hsl
            try:
                import cv2
                h = float(adj.get("hue", 0))         # -180..180
                s = float(adj.get("saturation", 0))  # -100..100
                li = float(adj.get("lightness", 0))  # -100..100
                hsv = cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2HSV).astype(np.float32)
                hsv[:, :, 0] = (hsv[:, :, 0] + h / 2.0) % 180.0
                hsv[:, :, 1] = np.clip(hsv[:, :, 1] * (1.0 + s / 100.0), 0, 255)
                hsv[:, :, 2] = np.clip(hsv[:, :, 2] + li * 1.275, 0, 255)
                rgb = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB).astype(np.float32)
            except Exception:
                pass
        # 调整层不透明度 = 调整强度
        op = max(0.0, min(1.0, layer.opacity))
        strength = op
        if layer.mask is not None and layer.mask.shape == arr.shape[:2]:
            strength = op * layer.mask[:, :, None].astype(np.float32) / 255
        rgb = rgb * strength + orig_rgb.astype(np.float32) * (1.0 - strength)
        arr[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
        return qimage_from_numpy(arr)

    def _add_adjust_layer(self, kind):
        """新建调整图层（插到活跃图层上方）。"""
        self._push_undo("新建调整图层")
        names = {
            "brightness_contrast": "亮度/对比度",
            "hsl": "色相/饱和度",
            "levels": "色阶",
            "curves": "曲线",
            "white_balance": "白平衡",
            "color_balance": "色彩平衡",
        }
        defaults = {
            "brightness_contrast": {"brightness": 0, "contrast": 0},
            "hsl": {"hue": 0, "saturation": 0, "lightness": 0},
            "levels": {"black": 0, "gamma": 100, "white": 255},
            "curves": {"shadows": 0, "midtones": 0, "highlights": 0},
            "white_balance": {"temperature": 0, "tint": 0},
            "color_balance": {"cyan_red": 0, "mag_green": 0, "yel_blue": 0},
        }
        name = names.get(kind, kind)
        if kind in ADJUSTMENTS:
            name, specs = ADJUSTMENTS[kind]
            defaults[kind] = {key: default for key, _, _, _, default in specs}
        l = ImageLayer(name, kind="adjust", w=1, h=1)
        l.adjust = {"type": kind}
        l.adjust.update(defaults.get(kind, {}))
        layers = self._ctx_layers()
        idx = layers.index(self.active) + 1 if self.active in layers else len(layers)
        layers.insert(idx, l)
        self._select_layer_only(l)
        self._refresh_layers()
        self._redraw()
        if kind == 'curves':
            self._edit_adjust_layer(l)

    def _edit_adjust_layer(self, layer):
        """调整图层参数对话框（滑块实时预览，取消可还原）。"""
        if layer.kind != "adjust" or layer.adjust is None or layer.locked:
            return
        if layer.adjust.get('type') == 'filter':
            self.edit_filter_layer(layer)
            return
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout
        backup = copy.deepcopy(layer.adjust)
        dlg = QDialog(self)
        dlg.setWindowTitle(f"调整图层 — {layer.name}")
        dlg.setStyleSheet("QDialog{background:#252528;} QLabel{color:#ccc;font-size:12px;}")
        form = QFormLayout(dlg)
        t = layer.adjust.get("type", "brightness_contrast")
        ADJ_PARAMS = {
            "brightness_contrast": [("brightness", "亮度", -100, 100),
                                    ("contrast", "对比度", -100, 100)],
            "hsl": [("hue", "色相", -180, 180), ("saturation", "饱和度", -100, 100),
                    ("lightness", "明度", -100, 100)],
            "levels": [("black", "黑场", 0, 255), ("gamma", "灰度系数(×100)", 10, 300),
                       ("white", "白场", 0, 255)],
            "curves": [("shadows", "阴影", -100, 100), ("midtones", "中间调", -100, 100),
                       ("highlights", "高光", -100, 100)],
            "white_balance": [("temperature", "色温(暖↔冷)", -100, 100),
                              ("tint", "色调(洋红↔绿)", -100, 100)],
            "color_balance": [("cyan_red", "青—红", -100, 100),
                              ("mag_green", "洋红—绿", -100, 100),
                              ("yel_blue", "黄—蓝", -100, 100)],
        }
        params = ADJ_PARAMS.get(t, [])
        if t in ADJUSTMENTS:
            params = [(key, label, lo, hi) for key, label, lo, hi, _ in ADJUSTMENTS[t][1]]
        before = self._snapshot()
        if t == 'curves':
            if not any(layer.adjust.get(key, 0) for key in ('shadows', 'midtones', 'highlights')):
                params = []
            channel = QComboBox()
            for label, key in (("RGB 总曲线", 'rgb'), ('红通道', 'red'), ('绿通道', 'green'), ('蓝通道', 'blue')):
                channel.addItem(label, key)
            form.addRow('通道', channel)
            graph = CurveEditor()
            graph.setObjectName('curveEditor')
            form.addRow(graph)
            point_x, point_y = QSpinBox(), QSpinBox()
            for spin in (point_x, point_y):
                spin.setRange(0, 255)
                spin.setEnabled(False)
            point_x.setObjectName('curveInput')
            point_y.setObjectName('curveOutput')
            form.addRow('控制点输入', point_x)
            form.addRow('控制点输出', point_y)
            def sync_point(point):
                for index, spin in enumerate((point_x, point_y)):
                    spin.blockSignals(True)
                    spin.setEnabled(point is not None and (index == 1 or graph.selected not in (0, len(graph.points) - 1)))
                    if point is not None:
                        spin.setValue(round(point[index]))
                    spin.blockSignals(False)
            graph.selectionChanged.connect(sync_point)
            point_x.valueChanged.connect(lambda value: graph.update_selected_point(x=value))
            point_y.valueChanged.connect(lambda value: graph.update_selected_point(y=value))
            form.addRow(QLabel('点击加点 · 拖动调节 · 右键删除中间点（最多 32 点）'))
            def sync_curve():
                graph.set_points(layer.adjust.get('channels', {}).get(channel.currentData(), [[0, 0], [255, 255]]), channel.currentData())
            def change_curve(points):
                channels = copy.deepcopy(layer.adjust.get('channels', {}))
                channels[channel.currentData()] = points
                layer.adjust['channels'] = channels
                self._redraw()
            graph.pointsChanged.connect(change_curve)
            channel.currentIndexChanged.connect(sync_curve)
            reset_curve = QPushButton('重置当前通道')
            reset_curve.clicked.connect(lambda: (change_curve([[0, 0], [255, 255]]), sync_curve()))
            form.addRow(reset_curve)
            sync_curve()
        for key, label, lo, hi in params:
            row = QHBoxLayout()
            sld = QSlider(Qt.Orientation.Horizontal)
            sld.setRange(lo, hi)
            sld.setValue(int(layer.adjust.get(key, 0)))
            sld.setFixedWidth(220)
            val_lb = QLabel(str(sld.value()))
            val_lb.setFixedWidth(36)

            def _mk(k, lb):
                def _on(v):
                    layer.adjust[k] = v
                    lb.setText(str(v))
                    self._redraw()
                return _on
            sld.valueChanged.connect(_mk(key, val_lb))
            row.addWidget(sld)
            row.addWidget(val_lb)
            form.addRow(label, row)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText('应用')
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            layer.adjust = backup
            self._redraw()
        elif layer.adjust != backup:
            self._push_undo_snapshot("修改调整图层", before)

    def _draw_shadow(self, p, layer, content):
        col = QColor(layer.shadow_color)
        op = max(0.0, min(1.0, layer.shadow_opacity))
        ca = numpy_from_qimage(content)
        sh = ca.copy()
        sh[:, :, 0:3] = (col.red(), col.green(), col.blue())
        sh[:, :, 3] = (ca[:, :, 3].astype(np.int32) * int(op * 255) // 255).astype(np.uint8)
        sh_img = qimage_from_numpy(sh)
        if layer.shadow_blur > 0:
            sh_img = self.blur_qimage(sh_img, layer.shadow_blur)
        p.save()
        p.setOpacity(1.0)
        p.drawImage(layer.shadow_dx, layer.shadow_dy, sh_img)
        p.restore()

    def _render_outer_glow(self, layer, content):
        """P3 外发光效果：膨胀 alpha + 模糊 + 着色"""
        try:
            import cv2
            ca = numpy_from_qimage(content)
            alpha = ca[:, :, 3].astype(np.float32)
            if alpha.max() < 1:
                return None  # 无内容，无需发光
            size = max(1, int(layer.outer_glow_size))
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size*2+1, size*2+1))
            dilated = cv2.dilate(alpha, kernel)
            alpha_glow = dilated - alpha
            alpha_glow = np.clip(alpha_glow, 0, 255)
            # 高斯模糊
            k = max(3, size*2+1)
            if k % 2 == 0:
                k += 1
            alpha_glow = cv2.GaussianBlur(alpha_glow, (k, k), size/2.0)
            col = QColor(layer.outer_glow_color)
            out = np.zeros((ca.shape[0], ca.shape[1], 4), dtype=np.uint8)
            out[:, :, 0] = col.red()
            out[:, :, 1] = col.green()
            out[:, :, 2] = col.blue()
            out[:, :, 3] = (alpha_glow * layer.outer_glow_opacity).astype(np.uint8)
            return qimage_from_numpy(out)
        except Exception:
            return None

    def _paint_checker(self, p, w, h):
        if not hasattr(self, '_checker_brush'):
            tile = QPixmap(24, 24)
            tile.fill(QColor(58, 58, 58))
            painter = QPainter(tile)
            painter.fillRect(12, 0, 12, 12, QColor(40, 40, 40))
            painter.fillRect(0, 12, 12, 12, QColor(40, 40, 40))
            painter.end()
            self._checker_brush = QBrush(tile)
        p.fillRect(0, 0, w, h, self._checker_brush)

    def _redraw(self):
        self._display_generation = getattr(self, '_display_generation', 0) + 1
        # The view is reused when the last document closes or its last board is removed.
        if not self.project.artboards and not self.view.artboard_item.pixmap().isNull():
            self._draw_artboards()
        if self._redraw_interaction_preview():
            return
        if self._request_full_resolution_render():
            return
        img = self._render_composite(for_export=False)
        if self._compare_active and self._compare_before_image is not None:
            try:
                self._draw_compare_overlay(img)
            except Exception as _e:
                # 前后对比叠加出问题时不影响主画布渲染，且关掉对比避免循环报错
                self._compare_active = False
                if self._compare_btn is not None:
                    self._compare_btn.setChecked(False)
                self.ai_status.setText("⚠ 前后对比叠加失败：{}".format(str(_e)[:120]))
                self.ai_status.setVisible(True)
                QTimer.singleShot(3000, lambda: self.ai_status.setVisible(False))
        # 文字编辑态：在合成图上叠加编辑框 + 闪烁光标
        # 注意：必须在 setPixmap 之前绘制 —— QPixmap.fromImage 是深拷贝，
        # 若在 setPixmap 之后才画到 img 上，叠加内容不会进入已显示的 pixmap
        # （导致输入/删除时画布上看不到任何变化，只有提交后才显示）。
        if self._text_editing:
            self._draw_text_editor(img)
        self.view.composite_item.setTransform(QTransform())
        self.view.composite_item.setPixmap(QPixmap.fromImage(img))
        self.view.navigator.set_document(self.view.composite_item.pixmap())
        if self.project.artboards:
            dw, dh = self._doc_bounds()
            M = self.view.PAN_MARGIN
            self.view.scene.setSceneRect(-M, -M, dw + 2 * M, dh + 2 * M)
            self._draw_artboards()
        else:
            M = self.view.PAN_MARGIN
            self.view.scene.setSceneRect(
                -M, -M, self.project.w + 2 * M, self.project.h + 2 * M)
        self._redraw_selection()
        self._draw_handles()
        self._draw_guides_and_grid()

    # ═══════ 变换手柄（PS 风格缩放/旋转） ═══════
    HANDLE_SIZE = 7
    ROTATE_HANDLE_DIST = 28

    def _leaf_bbox(self, layer):
        """返回图层在画布空间的包围盒（考虑变换）"""
        if not layer:
            return None
        t = layer.canvas_transform()
        corners = [t.map(QPointF(0, 0)), t.map(QPointF(layer.w, 0)),
                   t.map(QPointF(layer.w, layer.h)), t.map(QPointF(0, layer.h))]
        xs = [p.x() for p in corners]; ys = [p.y() for p in corners]
        return QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    def _draw_handles(self):
        self.view.handle_item.setPixmap(QPixmap())
        self.view.viewport().update()

    def _paint_layer_handles(self):
        """Paint small transform controls in the viewport, not a document-sized bitmap."""
        if not self._ft_active and self.tool in (Tool.SELECT_RECT, Tool.SELECT_ELLIPSE,
                Tool.SELECT_LASSO, Tool.POLY_LASSO, Tool.WAND, Tool.QUICK_SELECT, Tool.OBJECT_SELECT):
            return
        if self._text_editing or self.active is not None and self.active.kind=='text' and not self._ft_active:
            return
        """只在 _show_handles=True 且有活跃图层时显示变换手柄"""
        if not self._show_handles or not self.active or not self.active.visible:
            return
        bbox = self._layer_bbox(self.active)
        if bbox is None or bbox.width() < 4 or bbox.height() < 4:
            return
        cw, ch = self._ctx_size()
        w, h = cw, ch
        p = QPainter(self.view.viewport())
        p.setWorldTransform(self.view.viewportTransform())
        if self.active_artboard is not None:
            sx, sy = self._ab_screen(self.active_artboard)
            p.translate(sx, sy)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 包围盒虚线
        pen = QPen(QColor("#3d8ef8"), 1.0)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setDashPattern([4, 3])
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(bbox)
        # 8 个角/边手柄
        zoom = abs(self.view.viewportTransform().m11())
        l, r, t, b = bbox.left(), bbox.right(), bbox.top(), bbox.bottom()
        cx, cy = bbox.center().x(), bbox.center().y()
        handles = {
            "nw": QPointF(l, t), "n": QPointF(cx, t), "ne": QPointF(r, t),
            "e": QPointF(r, cy), "se": QPointF(r, b), "s": QPointF(cx, b),
            "sw": QPointF(l, b), "w": QPointF(l, cy),
        }
        handle_pen = QPen(QColor("#3d8ef8"), 1)
        handle_pen.setCosmetic(True)
        p.setPen(handle_pen)
        p.setBrush(QBrush(QColor(255, 255, 255)))
        for name, pt in handles.items():
            hs = (10 if len(name) == 2 else self.HANDLE_SIZE) / (2 * zoom)
            p.drawRect(QRectF(pt.x() - hs, pt.y() - hs, hs * 2, hs * 2))
        # 旋转锚点（圆，在上方）
        rp = QPointF(cx, t - self.ROTATE_HANDLE_DIST)
        p.setPen(QPen(QColor("#00eaff"), 2))
        p.setBrush(QBrush(QColor("#00eaff")))
        p.drawEllipse(rp, 4, 4)
        # 连接线
        p.setPen(QPen(QColor("#3d8ef8"), 1.0))
        p.drawLine(QPointF(cx, t), rp)
        # 图层名标签：明确指示画布上当前选中的具体是哪个图层
        name = (getattr(self.active, "name", "") or "").strip()
        if name:
            p.setFont(QFont("Microsoft YaHei", 11))
            metrics = QFontMetrics(p.font())
            tw = metrics.horizontalAdvance(name) + 12
            th = 20
            lx = max(0.0, min(bbox.left(), w - tw))
            ly = bbox.top() - th - 6
            if ly < 0:
                ly = bbox.top() + 6
            ly = max(0.0, min(ly, h - th))
            badge = QRectF(lx, ly, tw, th)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor("#00eaff")))
            p.drawRect(badge)
            p.setPen(QColor("#06222a"))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, name)
        p.end()

    def _show_rot_hud(self, deg):
        """旋转时实时显示当前角度（画布顶部居中 HUD）。"""
        ang = ((deg + 180.0) % 360.0) - 180.0   # 归一化到 (-180, 180]
        self._rot_hud.setText("↻ {:.1f}°".format(ang))
        self._rot_hud.adjustSize()
        vp = self.view.viewport()
        x = max(4, (vp.width() - self._rot_hud.width()) // 2)
        self._rot_hud.move(x, 8)
        self._rot_hud.show()

    def _hit_handle(self, pt):
        if self.active is not None and self.active.kind=='text' and not self._ft_active:
            return None
        """检测点击位置是否在手柄上，返回手柄名或 None。画板模式下 pt 需转为本地坐标。"""
        if not self._show_handles or not self.active or not self.active.visible:
            return None
        bbox = self._layer_bbox(self.active)
        if bbox is None or bbox.width() < 4 or bbox.height() < 4:
            return None
        # 画板模式下 pt 是文档坐标，转为画板本地坐标以匹配 bbox（画板本地空间）
        pt = self._doc_to_local(pt)
        zoom = abs(self.view.viewportTransform().m11())
        l, r, t, b = bbox.left(), bbox.right(), bbox.top(), bbox.bottom()
        cx, cy = bbox.center().x(), bbox.center().y()
        handles = {
            "nw": QPointF(l, t), "n": QPointF(cx, t), "ne": QPointF(r, t),
            "e": QPointF(r, cy), "se": QPointF(r, b), "s": QPointF(cx, b),
            "sw": QPointF(l, b), "w": QPointF(l, cy),
        }
        targets = []
        for name, hp in handles.items():
            dx, dy = (pt.x() - hp.x()) * zoom, (pt.y() - hp.y()) * zoom
            radius = 12 if len(name) == 2 else 8
            if max(abs(dx), abs(dy)) <= radius:
                targets.append((dx * dx + dy * dy, name))
        rotation_y = t - self.ROTATE_HANDLE_DIST
        rotation_distance = ((pt.x() - cx)**2 + (pt.y() - rotation_y)**2) * zoom**2
        if rotation_distance <= max(6, 4 * zoom)**2:
            targets.append((rotation_distance, "rotate"))
        return min(targets)[1] if targets else None

    # ═══════ P3 参考线 / 网格 ═══════

    def _hit_guide(self, pt, tol=6.0):
        """命中已有参考线？返回 ('h', idx) / ('v', idx) / None（pt 为画布坐标）"""
        for i, y in enumerate(self.project.h_guides):
            if abs(pt.y() - y) <= tol:
                return ('h', i)
        for i, x in enumerate(self.project.v_guides):
            if abs(pt.x() - x) <= tol:
                return ('v', i)
        return None


    def _toggle_grid(self):
        self._push_undo("切换网格")
        self.project.show_grid = not self.project.show_grid
        self._redraw()

    # ── P5 视图菜单切换 ──
    def _toggle_rulers(self, action=None):
        if action is not None:
            self.show_rulers = action.isChecked()
        else:
            self.show_rulers = not self.show_rulers
            if getattr(self, "_act_rulers", None):
                self._act_rulers.setChecked(self.show_rulers)
        self.view.viewport().update()

    def _toggle_grid_action(self, action=None):
        target = action.isChecked() if action is not None else not self.project.show_grid
        if target == self.project.show_grid:
            return
        self._push_undo("切换网格")
        if action is not None:
            self.project.show_grid = action.isChecked()
        else:
            self.project.show_grid = not self.project.show_grid
            if getattr(self, "_act_grid", None):
                self._act_grid.setChecked(self.project.show_grid)
        self._draw_guides_and_grid()
        self.view.viewport().update()

    def _toggle_smart_guides(self, action=None):
        if action is not None:
            self.smart_guides_on = action.isChecked()
        else:
            self.smart_guides_on = not self.smart_guides_on
            if getattr(self, "_act_smart", None):
                self._act_smart.setChecked(self.smart_guides_on)

    def _add_guide_at(self, pt):
        """在指定文档坐标添加参考线（优先水平，垂直备选）"""
        self._push_undo("添加参考线")
        dx = min(abs(pt.x() - gx) for gx in self.project.v_guides) if self.project.v_guides else 999
        dy = min(abs(pt.y() - gy) for gy in self.project.h_guides) if self.project.h_guides else 999
        if dy <= dx:
            self.project.h_guides.append(pt.y())
        else:
            self.project.v_guides.append(pt.x())
        self._redraw()

    def _clear_guides(self):
        if not self.project.h_guides and not self.project.v_guides:
            return
        self._push_undo("清除参考线")
        self.project.h_guides.clear()
        self.project.v_guides.clear()
        self._redraw()

    # ───────── P5 智能参考线（拖动/变换时吸附对齐） ─────────

    # ───────── 画板叠加层（边框 + 名称标签，不进入导出） ─────────
    def _draw_artboards(self):
        """绘制每个画板的边框线 + 名称标签框（纯叠加层，不进入导出）。
        边框/名称仅作标识与选中提示，不再充当「约束框」——画布可随画板自由扩大，
        画板可挪到任意位置，不再被深色背景裁切。"""
        if not self.project.artboards:
            self.view.artboard_item.setPixmap(QPixmap())
            return
        dw, dh = self._doc_bounds()
        img = QImage(dw, dh, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        tx, ty = self._ab_translate()
        for ab in self.project.artboards:
            x, y, w, h = int(ab.x + tx), int(ab.y + ty), ab.w, ab.h
            active = (ab is self.active_artboard)
            # 边框线
            pen = QPen(QColor("#00eaff") if active else QColor("#5a5a5e"))
            pen.setWidth(1)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(x, y, w, h)
            # 名称标签框（顶部）
            f = QFont("Microsoft YaHei", 11)
            p.setFont(f)
            metrics = QFontMetrics(f)
            tw = metrics.horizontalAdvance(ab.name) + 12
            th = 18
            bx, by = x, y - th - 2
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor("#00eaff") if active else QColor("#3a3a3e")))
            p.drawRect(bx, by, max(tw, 12), th)
            p.setPen(QColor("#101010") if active else QColor("#cfcfcf"))
            p.drawText(bx + 6, by + 13, ab.name)
        p.end()
        self.view.artboard_item.setPixmap(QPixmap.fromImage(img))

    def _artboard_at(self, pt):
        """返回点击点所在（顶层优先）的画板，或 None。pt 为文档坐标。"""
        tx, ty = self._ab_translate()
        for ab in reversed(self.project.artboards):
            ax, ay = ab.x + tx, ab.y + ty
            if ax <= pt.x() <= ax + ab.w and ay <= pt.y() <= ay + ab.h:
                return ab
        return None

    def _redraw_selection(self):
        if self.selection is None or not self.selection.any():
            self.view.sel_item.setPixmap(QPixmap())
            self.view.sel_item.setOffset(0, 0)
            self._stop_sel_anim()
            return
        # 画板模式下选区相对画板本地坐标，需偏移到画板在文档中的位置
        if self.active_artboard is not None:
            sx, sy = self._ab_screen(self.active_artboard)
            self.view.sel_item.setOffset(sx, sy)
        else:
            self.view.sel_item.setOffset(0, 0)
        h, w = self.selection.shape
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        phase = self._sel_phase % 20
        # 找选区边界，画闭合虚线框（双色蚂蚁线，随时间平移）
        try:
            import cv2
            sel_u8 = self.selection.astype(np.uint8) * 255
            contours, _ = cv2.findContours(sel_u8, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for cnt in contours:
                if len(cnt) < 3:
                    continue
                pts = [QPointF(float(pt[0][0]), float(pt[0][1])) for pt in cnt]
                poly = QPolygonF(pts)
                if not poly.isClosed():
                    poly.append(poly.first())
                # 白+黑双色蚂蚁线（仿 PS，随时间平移）：黑色底层 + 白色半周期偏移上层
                pen_b = QPen(QColor(0, 0, 0, 220), 0.65, Qt.PenStyle.DashLine, Qt.PenCapStyle.FlatCap)
                pen_b.setDashPattern([5, 5])
                pen_b.setDashOffset(phase)
                pen_w = QPen(QColor(255, 255, 255, 240), 0.65, Qt.PenStyle.DashLine, Qt.PenCapStyle.FlatCap)
                pen_w.setDashPattern([5, 5])
                pen_w.setDashOffset(phase + 5)   # 半周期偏移 → 白色落在黑色间隙
                p.setPen(pen_b); p.drawPolygon(poly)
                p.setPen(pen_w); p.drawPolygon(poly)
            p.end()
        except Exception:
            # numpy 兜底：边界像素画点（dash 随时间平移）
            edges = np.zeros((h, w), bool)
            edges[1:, :] |= self.selection[1:, :] != self.selection[:-1, :]
            edges[:, 1:] |= self.selection[:, 1:] != self.selection[:, :-1]
            ys, xs = np.where(edges)
            p = QPainter(img)
            pen_b = QPen(QColor(0, 0, 0, 220)); pen_b.setWidth(1)
            pen_w = QPen(QColor(255, 255, 255, 240)); pen_w.setWidth(1)
            for y, x in zip(ys.tolist(), xs.tolist()):
                # 半周期交错：白点落在黑点间隙（仿 PS 蚂蚁线）
                if (x + y + phase) % 8 < 4:
                    p.setPen(pen_w); p.drawPoint(x, y)
                else:
                    p.setPen(pen_b); p.drawPoint(x, y)
            p.end()
        self.view.sel_item.setPixmap(QPixmap.fromImage(img))
        self._start_sel_anim()

    def _tick_sel_anim(self):
        """蚂蚁线动画：偏移 dash phase 并重绘选区框。"""
        if self.selection is None or not self.selection.any():
            self._stop_sel_anim()
            return
        self._sel_phase = (self._sel_phase + 2) % 100000
        self._redraw_selection()

    def _start_sel_anim(self):
        if not self._sel_timer.isActive():
            self._sel_timer.start()

    def _stop_sel_anim(self):
        if self._sel_timer.isActive():
            self._sel_timer.stop()

    # ═══════ 选区 ═══════

    @selection_history('取消选区')
    def _clear_selection(self, checked=False):
        self.selection = None
        self.sel_alpha = None
        self._sel_base = None
        self._sel_preview_base = None
        self._redraw_selection()

    @selection_history('反选选区')
    def _invert_selection(self, checked=False):
        """反选：画布内已选↔未选互换（参考 PS 选区反选）。"""
        if self.selection is None:
            return
        self.selection = ~self.selection
        self._sel_base = self.selection.copy()  # 反选后的原始二值选区
        # 反选后软边缘权重失效，重新按当前羽化值羽化
        if self.feather > 0:
            self._feather_selection(self.feather)
        else:
            self.sel_alpha = None
        self._redraw_selection()

    def _canvas_context_menu(self, global_pos):
        """画布右键菜单：有像素选区时给出选区操作（删除/填充/羽化/模糊）；否则给画布级快捷。"""
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        if self.selection is not None:
            menu.addAction("📋 复制选区", self.copy_selection)
            menu.addAction("✨ AI 局部修改…", self._prompt_ai_inpaint)
            menu.addSeparator()
            menu.addAction("🗑 删除选区", lambda: self.delete_selection(silent=True))
            menu.addAction("🎨 填充选区", self.fill_selection)
            menu.addAction("内容识别填充", self._content_aware_fill)
            menu.addAction("🔁 反选选区", self._invert_selection)
            menu.addAction("🌫 羽化选区", self._feather_sel)
            menu.addAction("🌫 模糊选区", self._blur_sel)
            menu.addSeparator()
            menu.addAction("✕ 取消选区", self._clear_selection)
        else:
            menu.addAction("⊡ 适应画布", self.view.fit_view)
            menu.addAction("📋 粘贴图片", self.paste_image)
            menu.addAction("📂 打开图片", self.add_image_dialog)
            # 画板模式：右键画板空白处可删除当前画板
            if self.active_artboard is not None:
                menu.addSeparator()
                menu.addAction("🗑 删除画板", lambda: self._delete_artboard(self.active_artboard))
        menu.exec(global_pos)

    def _prompt_ai_inpaint(self):
        """选区右键入口：输入一句修改描述并直接提交局部重绘。"""
        if self.selection is None or not self.selection.any():
            QMessageBox.information(self, "AI 局部修改", "请先框选需要修改的区域。")
            return
        dialog = AIInpaintPromptDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        prompt = dialog.prompt()
        if not prompt:
            return
        panel = getattr(self, "_ai_image_panel", None)
        if panel is None:
            # 局部重绘是精修能力，按需创建无界面后端；
            # 不再把完整生图面板挂回图片工作台。
            try:
                from ai.ui.image_ai_panel import ImageAIPanel
                panel = ImageAIPanel(parent=self, host=self)
                panel.hide()
                self._ai_image_panel = panel
            except Exception as error:
                QMessageBox.warning(self, "AI 局部修改", str(error))
                return
        self._begin_ai_compare("AI 局部修改")
        if panel.generate_inpaint(prompt):
            self.status_tool.setText("AI 正在修改选区…")
        else:
            self._cancel_ai_compare()

    def add_ai_inpaint_result(self, path, selection, name="AI 局部修改"):
        """把完整的 inpaint 返回图裁成透明选区图层并叠回当前画板。"""
        from PIL import Image as PILImage, ImageFilter
        mask = np.asarray(selection, dtype=bool)
        mh, mw = mask.shape
        image = PILImage.open(path).convert("RGBA")
        if image.size != (mw, mh):
            image = image.resize((mw, mh), PILImage.Resampling.LANCZOS)
        pixels = np.array(image, dtype=np.uint8)

        # AI 返回的是整张重绘图。回贴时使用只向选区内部渐隐的软边蒙版，
        # 避免矩形选区在脸部、衣物等连续纹理上形成生硬拼贴接缝。
        feather_radius = max(4, min(18, int(min(mw, mh) * 0.012)))
        hard_mask = PILImage.fromarray(mask.astype(np.uint8) * 255, mode="L")
        soft_mask = np.asarray(
            hard_mask.filter(ImageFilter.GaussianBlur(feather_radius)),
            dtype=np.float32,
        ) / 255.0
        soft_mask *= mask.astype(np.float32)  # 不允许修改泄漏到用户选区外
        pixels[:, :, 3] = np.clip(
            pixels[:, :, 3].astype(np.float32) * soft_mask, 0, 255
        ).astype(np.uint8)

        layer = ImageLayer(name, pixels=pixels, w=mw, h=mh, kind="image")
        layer.x = mw / 2.0
        layer.y = mh / 2.0
        layer.scale = 1.0
        self._push_undo("AI 局部修改")
        if self.active_artboard is not None:
            self.active_artboard.layers.append(layer)
        else:
            self.project.add_layer(layer)
        self.set_active(layer)
        self._refresh_layers()
        self._redraw()
        self._finish_ai_compare()
        self.status_tool.setText("AI 局部修改完成")

    def _handle_leaf_move(self, name, pt, orig):
        """拖拽变换手柄：旋转 / 缩放。画板模式下 pt 是文档坐标，需转为本地坐标。"""
        layer = self.active
        if not layer or not orig.get("bbox"):
            return
        # 画板模式下：pt 和 orig["start"] 是文档坐标，bbox 是画板本地坐标
        pt = self._doc_to_local(pt)
        start = self._doc_to_local(orig["start"]) if self.active_artboard is not None else orig["start"]
        bbox = orig["bbox"]
        cx, cy = bbox.center().x(), bbox.center().y()

        if name == "rotate":
            a1 = math.atan2(start.y() - cy, start.x() - cx)
            a2 = math.atan2(pt.y() - cy, pt.x() - cx)
            layer.rotation = orig["rotation"] + math.degrees(a2 - a1)
            self._show_rot_hud(layer.rotation)
            self._redraw()
            return

        # 缩放：以对角/对边为锚点，拖动当前手柄 → 更新对应边界
        bw, bh = bbox.width(), bbox.height()
        if bw < 2 or bh < 2:
            return
        left, top, right, bottom = bbox.left(), bbox.top(), bbox.right(), bbox.bottom()
        # 新边界（缺省保持原值，被拖动的那条边跟随鼠标）
        n_left, n_top, n_right, n_bottom = left, top, right, bottom
        if "w" in name:   # 西边（nw / sw / w）
            n_left = left + pt.x() - start.x()
        if "e" in name:   # 东边（ne / se / e）
            n_right = right + pt.x() - start.x()
        if "n" in name:   # 北边（nw / ne / n）
            n_top = top + pt.y() - start.y()
        if "s" in name:   # 南边（sw / se / s）
            n_bottom = bottom + pt.y() - start.y()

        nw = max(n_right - n_left, 1.0)
        nh = max(n_bottom - n_top, 1.0)

        # Shift 锁定宽高比（角手柄 + 边手柄均生效）
        shift = (QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
        if shift and name in ("nw", "ne", "sw", "se", "n", "s", "e", "w"):
            aspect = bw / max(bh, 1)
            if name in ("nw", "ne", "sw", "se"):
                # 角手柄：保持该角锚点固定，按对应边长等比延展
                if nw / max(nh, 1) > aspect:
                    nh = nw / aspect
                else:
                    nw = nh * aspect
                if "w" in name:
                    n_left = n_right - nw
                else:
                    n_right = n_left + nw
                if "n" in name:
                    n_top = n_bottom - nh
                else:
                    n_bottom = n_top + nh
            elif name in ("e", "w"):
                # 水平边手柄：以垂直中心为锚，按宽高比同步延展高度
                nh = nw / aspect
                c = (n_top + n_bottom) / 2.0
                n_top = c - nh / 2.0
                n_bottom = c + nh / 2.0
            else:  # n / s
                # 垂直边手柄：以水平中心为锚，按宽高比同步延展宽度
                nw = nh * aspect
                c = (n_left + n_right) / 2.0
                n_left = c - nw / 2.0
                n_right = c + nw / 2.0

        # 计算缩放比例（单浮点 scale 模型：按拖动轴取比例）
        if name in ("e", "w"):
            f = nw / max(bw, 1)
        elif name in ("n", "s"):
            f = nh / max(bh, 1)
        else:  # 角手柄：几何平均，避免任一方向反转导致整体放大
            f = math.sqrt((nw / max(bw, 1)) * (nh / max(bh, 1)))
        layer.scale = orig["scale"] * max(min(f, 50.0), 0.01)
        layer.x = (n_left + n_right) / 2
        layer.y = (n_top + n_bottom) / 2
        # P5 智能参考线：把被拖拽的边吸附到画布 / 其余图层
        if self.smart_guides_on:
            b = self._layer_bbox(layer)
            cand_xs = [b.left(), (b.left() + b.right()) / 2, b.right()]
            cand_ys = [b.top(), (b.top() + b.bottom()) / 2, b.bottom()]
            if "w" in name:
                cand_xs = [b.left()]
            elif "e" in name:
                cand_xs = [b.right()]
            if "n" in name:
                cand_ys = [b.top()]
            elif "s" in name:
                cand_ys = [b.bottom()]
            sdx, sdy, guides = self._snap_bbox_lines(cand_xs, cand_ys, {layer})
            layer.x += sdx
            layer.y += sdy
            self._smart_guides = guides
            self.view.viewport().update()
        else:
            self._smart_guides = []
        self._redraw()

    @selection_history(lambda self, mask, mode: {'new': '新建选区', 'add': '添加选区',
                                               'sub': '减去选区', 'intersect': '交叉选区'}[mode])
    def _combine_mask(self, mask, mode):
        self.selection = combine_selection_masks(self.selection, mask, mode)
        self.sel_alpha = None  # 重新选区后羽化权重失效
        self._sel_base = self.selection.copy()  # 记录羽化前原始二值选区

    def _commit_rect_sel(self, a, b, tool, mode):
        a = self._doc_to_local(a)
        b = self._doc_to_local(b)
        x0, y0 = int(min(a.x(), b.x())), int(min(a.y(), b.y()))
        x1, y1 = int(max(a.x(), b.x())), int(max(a.y(), b.y()))
        w, h = self._ctx_size()
        mask = np.zeros((h, w), bool)
        mask[max(0, min(h, y0)):max(0, min(h, y1)),
             max(0, min(w, x0)):max(0, min(w, x1))] = True
        if tool == Tool.SELECT_ELLIPSE and x1 > x0 and y1 > y0:
            yy, xx = np.mgrid[0:h, 0:w]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            rx, ry = (x1 - x0) / 2, (y1 - y0) / 2
            em = (((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2) <= 1
            mask = mask & em
        self._combine_mask(mask, mode)
        self._sel_preview_base = None
        self._redraw_selection()

    def _preview_rect_sel(self, a, b, tool):
        """虚线框预览选区（不填充）"""
        # 暂停已提交选区的蚂蚁线动画，避免与实时预览写入同一图层产生残影
        self._stop_sel_anim()
        a = self._doc_to_local(a)
        b = self._doc_to_local(b)
        x0, y0 = int(min(a.x(), b.x())), int(min(a.y(), b.y()))
        x1, y1 = int(max(a.x(), b.x())), int(max(a.y(), b.y()))
        if x1 - x0 < 2 and y1 - y0 < 2:
            self.view.sel_item.setPixmap(QPixmap())
            return
        w, h = self._ctx_size()
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 叠加模式(Shift/Alt)：画入拖拽开始时冻结的已提交选区底图（绝不回读 sel_item，避免逐帧累积残影）
        base = getattr(self, "_sel_preview_base", None)
        if getattr(self, "_sel_combine", "new") in ("add", "sub", "intersect") and base is not None and not base.isNull():
            p.drawImage(0, 0, base.toImage())
        # 白+黑双色蚂蚁线（仿 PS）
        phase = self._sel_phase % 20
        pen_b = QPen(QColor(0, 0, 0, 220), 0.65, Qt.PenStyle.DashLine, Qt.PenCapStyle.FlatCap)
        pen_b.setDashPattern([5, 5]); pen_b.setDashOffset(phase)
        pen_w = QPen(QColor(255, 255, 255, 240), 0.65, Qt.PenStyle.DashLine, Qt.PenCapStyle.FlatCap)
        pen_w.setDashPattern([5, 5]); pen_w.setDashOffset(phase + 5)
        r = QRectF(float(x0), float(y0), float(x1 - x0), float(y1 - y0))
        if tool == Tool.SELECT_RECT:
            p.setPen(pen_b); p.drawRect(r)
            p.setPen(pen_w); p.drawRect(r)
        else:
            p.setPen(pen_b); p.drawEllipse(r)
            p.setPen(pen_w); p.drawEllipse(r)
        p.end()
        if self.active_artboard is not None:
            self.view.sel_item.setOffset(*self._ab_screen(self.active_artboard))
        else:
            self.view.sel_item.setOffset(0, 0)
        self.view.sel_item.setPixmap(QPixmap.fromImage(img))

    def _preview_lasso(self, pts):
        # 暂停已提交选区的蚂蚁线动画，避免与实时预览写入同一图层产生残影
        self._stop_sel_anim()
        pts = [self._doc_to_local(pt) for pt in pts]
        w, h = self._ctx_size()
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 叠加模式(Shift/Alt)：画入拖拽开始时冻结的已提交选区底图（绝不回读 sel_item）
        base = getattr(self, "_sel_preview_base", None)
        if getattr(self, "_sel_combine", "new") in ("add", "sub", "intersect") and base is not None and not base.isNull():
            p.drawImage(0, 0, base.toImage())
        # 白+黑双色蚂蚁线（仿 PS）
        phase = self._sel_phase % 20
        pen_b = QPen(QColor(0, 0, 0, 220), 0.65, Qt.PenStyle.DashLine, Qt.PenCapStyle.FlatCap)
        pen_b.setDashPattern([5, 5]); pen_b.setDashOffset(phase)
        pen_w = QPen(QColor(255, 255, 255, 240), 0.65, Qt.PenStyle.DashLine, Qt.PenCapStyle.FlatCap)
        pen_w.setDashPattern([5, 5]); pen_w.setDashOffset(phase + 5)
        poly = QPolygonF([QPointF(pt.x(), pt.y()) for pt in pts])
        p.setPen(pen_b); p.drawPolyline(poly)
        p.setPen(pen_w); p.drawPolyline(poly)
        p.end()
        if self.active_artboard is not None:
            self.view.sel_item.setOffset(*self._ab_screen(self.active_artboard))
        else:
            self.view.sel_item.setOffset(0, 0)
        self.view.sel_item.setPixmap(QPixmap.fromImage(img))

    # ═══════ P3 裁剪 ═══════
    def _preview_crop(self, rect):
        """裁剪预览：裁剪区域外半透明遮罩 + 白虚线裁剪框。画板模式下转为本地坐标。"""
        ctx_w, ctx_h = self._ctx_size()
        img = QImage(ctx_w, ctx_h, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        # 半透明暗色遮罩覆盖整画布
        p.fillRect(0, 0, ctx_w, ctx_h, QColor(0, 0, 0, 120))
        # 画板模式下 rect 是文档坐标，转为画板本地坐标
        if self.active_artboard is not None:
            sx, sy = self._ab_screen(self.active_artboard)
            ox, oy = sx, sy
            local_rect = QRectF(rect.x() - ox, rect.y() - oy, rect.width(), rect.height())
            self.view.sel_item.setOffset(ox, oy)
        else:
            local_rect = QRectF(rect)
            self.view.sel_item.setOffset(0, 0)
        # 裁剪区域清空遮罩
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        p.fillRect(local_rect, Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        # 白虚线裁剪框
        pen = QPen(QColor(255, 255, 255, 220), 1.0)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setDashPattern([4, 4])
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(local_rect)
        # 九宫格辅助线（三等分）
        for i in range(1, 3):
            x = local_rect.x() + local_rect.width() / 3 * i
            y = local_rect.y() + local_rect.height() / 3 * i
            pn = QPen(QColor(255, 255, 255, 60), 0.5)
            pn.setStyle(Qt.PenStyle.DashLine)
            pn.setDashPattern([2, 6])
            p.setPen(pn)
            p.drawLine(QPointF(x, local_rect.y()), QPointF(x, local_rect.bottom()))
            p.drawLine(QPointF(local_rect.x(), y), QPointF(local_rect.right(), y))
        p.end()
        self.view.sel_item.setPixmap(QPixmap.fromImage(img))

    def _commit_crop(self, rect):
        """确认裁剪：与 PS 一致，把裁剪区域生成为【独立的新文档】（原画布保持不变）。
        新文档中单一图像层居中于新画布，天然对齐、无错位。"""
        self.view.sel_item.setPixmap(QPixmap())
        if rect.width() < 4 or rect.height() < 4:
            return
        ctx_w, ctx_h = self._ctx_size()
        # 画板模式下：rect 是文档坐标，需转为画板本地坐标
        if self.active_artboard is not None:
            sx, sy = self._ab_screen(self.active_artboard)
            ox, oy = sx, sy
            x0 = max(0, min(ctx_w, int(rect.x() - ox)))
            y0 = max(0, min(ctx_h, int(rect.y() - oy)))
            x1 = max(0, min(ctx_w, int(rect.right() - ox)))
            y1 = max(0, min(ctx_h, int(rect.bottom() - oy)))
        else:
            x0 = max(0, int(rect.x()))
            y0 = max(0, int(rect.y()))
            x1 = min(ctx_w, int(rect.right()))
            y1 = min(ctx_h, int(rect.bottom()))
        nw, nh = x1 - x0, y1 - y0
        if nw < 2 or nh < 2:
            return
        # 渲染真实 RGBA 内容（禁止把编辑态棋盘格写进裁剪结果），再裁出目标区域。
        if self.active_artboard is not None:
            base = self._render_artboard(self.active_artboard)
        else:
            base = self._render_composite(for_export=True)
        base_arr = numpy_from_qimage(base)
        if y1 > base_arr.shape[0]:
            y1 = base_arr.shape[0]
        if x1 > base_arr.shape[1]:
            x1 = base_arr.shape[1]
        crop = np.ascontiguousarray(base_arr[y0:y1, x0:x1])
        if crop.size == 0:
            return
        # 透明画布常在框选边缘留有空白；裁剪结果自动收紧到实际像素内容。
        trimmed, offset = _trim_transparent_rgba(crop)
        if trimmed is None:
            self.status_tool.setText("裁剪区域没有可见内容")
            return
        crop = trimmed
        x0 += offset[0]
        y0 += offset[1]
        nh, nw = crop.shape[:2]
        host = getattr(self, "host", None)
        if host is not None:
            name = (self.doc_name + " 裁剪") if self.doc_name else "裁剪结果"
            doc = host.new_document(nw, nh, name=name)
            layer = ImageLayer(name, pixels=crop, w=nw, h=nh, kind="image")
            layer.x = nw / 2.0   # 居中于新画布
            layer.y = nh / 2.0
            doc.project.add_layer(layer)
            doc.set_active(layer)
            doc._refresh_layers()
            doc._redraw()
            doc.view.fit_view()
        else:
            # 无容器（测试 / 独立运行）：退化为原地裁剪
            self._apply_crop_inplace(rect, x0, y0, nw, nh)
        self.selection = None
        self._stop_sel_anim()
        self._redraw()

    def _apply_crop_inplace(self, rect, x0, y0, nw, nh):
        """原地裁剪（仅无多文档容器时用作兜底，保持旧行为）。"""
        self._push_undo("裁剪")
        for layer in self._ctx_layers():
            if layer.kind == "image" and layer.pixels is not None:
                if x0 != 0 or y0 != 0 or nw != layer.w or nh != layer.h:
                    new_px = np.zeros((nh, nw, 4), dtype=np.uint8)
                    lp = layer.canvas_to_layer(x0, y0)
                    if lp is None:
                        continue
                    lx, ly = int(lp[0]), int(lp[1])
                    src_x0 = max(0, lx); src_y0 = max(0, ly)
                    src_x1 = min(layer.w, lx + nw); src_y1 = min(layer.h, ly + nh)
                    dst_x0 = src_x0 - lx; dst_y0 = src_y0 - ly
                    if src_x1 > src_x0 and src_y1 > src_y0:
                        new_px[dst_y0:dst_y0+src_y1-src_y0, dst_x0:dst_x0+src_x1-src_x0] = \
                            layer.pixels[src_y0:src_y1, src_x0:src_x1]
                    layer.pixels = new_px
                    layer.w, layer.h = nw, nh
                layer.x -= x0
                layer.y -= y0
            else:
                layer.x -= x0
                layer.y -= y0
        if self.active_artboard is not None:
            self.active_artboard.w = nw
            self.active_artboard.h = nh
            self.active_artboard.x += x0
            self.active_artboard.y += y0
        else:
            self.project.w = nw
            self.project.h = nh
        self._refresh_layers()

    def _canvas_resize_dialog(self):
        """画布大小调整对话框"""
        ctx_w, ctx_h = self._ctx_size()
        dlg = QDialog(self)
        dlg.setWindowTitle("调整画布大小")
        dlg.setMinimumWidth(300)
        dlg.setStyleSheet(
            "QDialog{background:#1e1e22;}"
            "QLabel{color:#ccc;font-size:12px;}"
            "QSpinBox{background:#2a2a2e;color:#eee;border:1px solid #3a3a3e;border-radius:3px;padding:4px;}"
            "QPushButton{background:#252528;color:#ccc;border:1px solid #333;border-radius:3px;padding:5px 12px;}"
            "QPushButton:hover{background:#333;}")
        lay = QVBoxLayout(dlg)
        fl = QFormLayout()
        w_spin = QSpinBox(); w_spin.setRange(1, 10000); w_spin.setValue(ctx_w)
        h_spin = QSpinBox(); h_spin.setRange(1, 10000); h_spin.setValue(ctx_h)
        fl.addRow("宽度", w_spin)
        fl.addRow("高度", h_spin)
        lay.addLayout(fl)
        bl = QHBoxLayout()
        ok = QPushButton("确定"); cancel = QPushButton("取消")
        ok.clicked.connect(dlg.accept); cancel.clicked.connect(dlg.reject)
        bl.addStretch(); bl.addWidget(ok); bl.addWidget(cancel)
        lay.addLayout(bl)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        nw, nh = w_spin.value(), h_spin.value()
        if nw == ctx_w and nh == ctx_h:
            return
        self._push_undo("调整画布大小")
        if self.active_artboard is not None:
            self.active_artboard.w = nw
            self.active_artboard.h = nh
        else:
            self.project.w = nw
            self.project.h = nh
        self._refresh_layers()
        self._redraw()

    def _commit_lasso(self, pts, mode):
        if len(pts) < 3:
            return
        pts = [self._doc_to_local(pt) for pt in pts]
        w, h = self._ctx_size()
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setBrush(QBrush(QColor(255, 255, 255)))
        p.setPen(Qt.PenStyle.NoPen)
        poly = QPolygonF([QPointF(pt.x(), pt.y()) for pt in pts])
        p.drawPolygon(poly)
        p.end()
        arr = numpy_from_qimage(img)
        mask = arr[:, :, 3] > 0
        self._combine_mask(mask, mode)
        self._sel_preview_base = None
        self._redraw_selection()

    def _wand(self, pt, tol, mode):
        if self.wand_sample_all:
            image = self._render_artboard(self.active_artboard) if self.active_artboard else self._render_composite(True)
            pixels = numpy_from_qimage(image)
            point = self._doc_to_local(pt)
            x, y = int(math.floor(point.x())), int(math.floor(point.y()))
            if not (0 <= x < pixels.shape[1] and 0 <= y < pixels.shape[0]):
                return
            self._combine_mask(self.wand_region(pixels, y, x, tol), mode)
            self._sel_preview_base = None
            self._redraw_selection()
            return
        layer = self.active
        if not layer or layer.kind != "image" or layer.pixels is None:
            QMessageBox.information(self, "提示", "请先选中一个图片图层再用魔棒。")
            return
        local_pt = self._doc_to_local(pt)
        lp = layer.canvas_to_layer(local_pt.x(), local_pt.y())
        if lp is None:
            return
        lx, ly = int(round(lp[0])), int(round(lp[1]))
        if not (0 <= lx < layer.w and 0 <= ly < layer.h):
            return
        mask_layer = self.wand_region(layer.pixels, ly, lx, tol)
        # 变换回画布空间
        w, h = self._ctx_size()
        tmp = np.zeros((layer.h, layer.w, 4), np.uint8)
        tmp[mask_layer, 0:3] = 255
        tmp[mask_layer, 3] = 255
        q = qimage_from_numpy(tmp)
        canvas_mask = QImage(w, h, QImage.Format.Format_ARGB32)
        canvas_mask.fill(Qt.GlobalColor.transparent)
        pp = QPainter(canvas_mask)
        pp.setTransform(layer.canvas_transform())
        pp.drawImage(0, 0, q)
        pp.end()
        ca = numpy_from_qimage(canvas_mask)
        mask = ca[:, :, 3] > 0
        self._combine_mask(mask, mode)
        self._sel_preview_base = None
        self._redraw_selection()

    @staticmethod
    def _flood(px, sy, sx, tol):
        """漫水填充：优先用 OpenCV C++ 实现（快 20-50×），失败回退纯 Python。"""
        h, w = px.shape[:2]
        mask = np.zeros((h, w), bool)
        if not (0 <= sx < w and 0 <= sy < h):
            return mask
        try:
            import cv2
            # cv2.floodFill 逐通道容差 + FIXED_RANGE（始终对比种子点），
            # 与 PS 魔棒行为接近，且底层 C++ 实现比纯 Python 栈式循环快两个数量级
            flood_mask = np.zeros((h + 2, w + 2), np.uint8)
            transparent = px[sy, sx, 3] == 0
            valid = px[:, :, 3] == 0 if transparent else px[:, :, 3] > 0
            flood_mask[1:-1, 1:-1][~valid] = 1
            lo = (tol,) * 3
            hi = (tol,) * 3
            flags = 4 | (255 << 8) | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE
            # px 为 RGBA，取前 3 通道做 flood fill（cv2 按 BGR 解释但不影响逐通道容差判断）
            # An RGB slice of RGBA is not contiguous; OpenCV rejects it.
            rgb = np.zeros((h, w), np.uint8) if transparent else np.ascontiguousarray(px[:, :, :3])
            cv2.floodFill(rgb, flood_mask, (sx, sy), 0, lo, hi, flags)
            mask = flood_mask[1:-1, 1:-1] == 255
        except Exception:
            # Match native fixed-range tolerance and transparency behavior.
            transparent = px[sy, sx, 3] == 0
            valid = px[:, :, 3] == 0 if transparent else px[:, :, 3] > 0
            target = px[sy, sx, :3].astype(np.int32)
            stack = [(sy, sx)]
            while stack:
                y, x = stack.pop()
                if not (0 <= x < w and 0 <= y < h) or mask[y, x] or not valid[y, x]:
                    continue
                d = px[y, x, :3].astype(np.int32) - target
                if transparent or np.all(np.abs(d) <= tol):
                    mask[y, x] = True
                    stack.append((y + 1, x)); stack.append((y - 1, x))
                    stack.append((y, x + 1)); stack.append((y, x - 1))
        return mask

    # ═══════ P5 快速选择工具（笔刷式智能选区） ═══════
    def _quick_select_begin(self, subtract=False, mode=None):
        """Freeze the starting selection; combine the whole stroke against it."""
        self._qs_mode = mode if mode is not None else ('sub' if subtract else self._sel_combine)
        self._qs_base = None if self.selection is None else self.selection.copy()
        self._qs_before = self._snapshot(metadata_only=True)
        self._qs_source_layer = None
        self._qs_last_point = None
        self._qs_mask = None          # 本次拖拽累积的画布空间 mask
        self._qs_visited = set()      # 去重：避免同一点重复 floodFill

    def _quick_select_point(self, pt):
        self.quick_select_brush_point(pt)

    def _quick_select_end(self):
        """拖拽结束：mask 已在 _quick_select_point 实时合并，仅清理状态。"""
        self._qs_mask = None
        self._qs_visited = set()
        self._qs_base = None
        self._qs_source_layer = None
        self._qs_rgb = self._qs_flood = self._qs_layer_mask = None
        if self.selection is not None and not self.selection.any():
            self.selection = None
            self._sel_base = None
            self._redraw_selection()
        if getattr(self, '_qs_before', None) is not None:
            self.commit_selection_history(self._qs_before, '快速选择')
            self._qs_before = None

    # ═══════ 选区操作 ═══════
    def delete_selection(self, silent=False):
        """删除选区内的像素（清为透明）。仿 PS：Delete/Backspace 对选区生效。
        返回 True 表示已对图片图层成功清除；False 表示无选区或当前层非图片层。"""
        if self.selection is None:
            return False
        layer = self.active
        if not layer or layer.kind != "image" or layer.pixels is None:
            if not silent:
                QMessageBox.information(self, "提示",
                    "请先选中一个图片图层，再用魔棒 / 选区工具删除选区内的像素。")
            return False
        self._push_undo("删除选区内容")
        self._apply_selection(lambda px, ly, lx: (0, 0, 0, 0))
        # 保留蚂蚁线，便于重复删除 / 后续填充等操作
        self._redraw()
        return True

    def fill_selection(self):
        if self.selection is None:
            return
        layer = self.active
        if not layer or layer.kind != "image" or layer.pixels is None:
            QMessageBox.information(self, "提示", "请先选中一个图片图层再填充选区。")
            return
        # 弹出颜色选择器，默认当前前景色
        c = choose_color(self.fg, self, '选择填充颜色')
        if not c.isValid():
            return
        self.fg = c
        self._update_color_btn(self.fg_btn, c)
        self._push_undo("填充选区")
        r, g, b = c.red(), c.green(), c.blue()
        self._apply_selection(lambda px, ly, lx: (r, g, b, 255))
        # 保留蚂蚁线，便于重复填充
        self._redraw()

    def _apply_selection(self, fn):
        layer = self.active
        if not layer or layer.kind != "image" or layer.pixels is None:
            return
        if self.selection is None:
            return
        mask = self.selection
        alpha = self.sel_alpha if self.sel_alpha is not None else mask.astype(np.float32)
        ys, xs = np.where(alpha > 0.001)
        if len(xs) == 0:
            return
        for y, x in zip(ys.tolist(), xs.tolist()):
            lp = layer.canvas_to_layer(x, y)
            if lp is None:
                continue
            lx, ly = int(round(lp[0])), int(round(lp[1]))
            if 0 <= lx < layer.w and 0 <= ly < layer.h:
                w = float(alpha[y, x])
                cur = layer.pixels[ly, lx].astype(np.float32)
                tgt = np.array(fn(layer.pixels, ly, lx), np.float32)
                layer.pixels[ly, lx] = (cur * (1 - w) + tgt * w).astype(np.uint8)

    # ═══════ P5 滤镜系统（cv2） ═══════
    # kind → (显示名, [(参数key, 标签, min, max, 默认值), ...])
    FILTERS = {
        "gaussian_blur":  ("高斯模糊",  [("radius", "半径", 1, 60, 8)]),
        "motion_blur":    ("动感模糊",  [("size", "距离", 3, 80, 15), ("angle", "角度", 0, 360, 0)]),
        "sharpen":        ("锐化",      [("amount", "强度", 10, 300, 80)]),
        "usm":            ("USM 锐化",  [("radius", "半径", 1, 30, 4), ("amount", "数量", 10, 300, 100)]),
        "noise":          ("添加杂色",  [("amount", "数量", 1, 100, 12)]),
        "denoise":        ("降噪",      [("strength", "强度", 1, 30, 5)]),
        "mosaic":         ("马赛克",    [("block", "单元格", 2, 100, 12)]),
        "posterize":      ("色调分离",  [("levels", "色阶", 2, 16, 4)]),
        "grayscale":      ("黑白", []),
        "invert":         ("反相", []),
    }

    @staticmethod
    def _filter_apply(px, kind, params):
        """对 RGBA numpy 数组应用滤镜，返回新数组。cv2 缺失时对不支持的滤镜抛异常。"""
        import cv2
        if kind in ('gaussian_blur', 'motion_blur'):
            return blur_rgba(px, kind, params)
        out = px.copy()
        rgb = out[:, :, :3]
        if kind in ("sharpen", "usm"):
            r = int(params.get("radius", 3)) | 1
            a = float(params["amount"]) / 100.0
            blur = cv2.GaussianBlur(rgb, (r * 2 + 1, r * 2 + 1), 0)
            sharp = cv2.addWeighted(rgb, 1.0 + a, blur, -a, 0)
            out[:, :, :3] = sharp
        elif kind == "noise":
            amt = float(params["amount"])
            noise = np.random.default_rng(int(params.get('seed', 0))).normal(0, amt, rgb.shape).astype(np.float32)
            out[:, :, :3] = np.clip(rgb.astype(np.float32) + noise, 0, 255).astype(np.uint8)
        elif kind == "denoise":
            s = max(1, min(30, int(params["strength"])))
            out[:, :, :3] = cv2.fastNlMeansDenoisingColored(
                rgb, None, float(s), float(s), 7, 21)
        elif kind == "mosaic":
            b = max(2, int(params["block"]))
            h, w = rgb.shape[:2]
            small = cv2.resize(out, (max(1, w // b), max(1, h // b)),
                               interpolation=cv2.INTER_LINEAR)
            out = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
        elif kind == "posterize":
            lv = max(2, int(params["levels"]))
            step = 255.0 / (lv - 1)
            out[:, :, :3] = (np.round(rgb / step) * step).clip(0, 255).astype(np.uint8)
        elif kind == "grayscale":
            g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            out[:, :, 0] = out[:, :, 1] = out[:, :, 2] = g
        elif kind == "invert":
            out[:, :, :3] = 255 - rgb
        return out


    def _run_filter(self, kind):
        """滤镜入口：非图片层先栅格化；带参数的弹实时预览对话框。"""
        layer = self.active
        if layer is None or layer.kind == "adjust" or layer.locked:
            QMessageBox.information(self, "提示", "请先选中一个图层再应用滤镜。")
            return
        name, spec = self.FILTERS[kind]
        has_sel = self.selection is not None and self.selection.any()
        pushed = False
        if layer.kind != "image":
            self._rasterize_layer(layer)   # 自带 undo 快照（栅格化）
        else:
            self._push_undo(name)
            pushed = True
        if layer.pixels is None:
            return
        original = layer.pixels.copy()
        if not spec:  # 无参数滤镜直接应用
            try:
                filt = self._filter_apply(original, kind, {})
                layer.pixels = self._confine_to_selection(layer, original, filt)
                self._last_filter = (kind, {})
            except Exception as ex:
                layer.pixels = original
                QMessageBox.warning(self, "滤镜失败", str(ex))
            self._redraw()
            return
        # 带参数：实时预览对话框（防抖避免 slider 每 tick 都跑重滤镜）
        dlg = QDialog(self)
        dlg.setWindowTitle(name + "（仅选区）" if has_sel else name)
        dlg.setStyleSheet("QDialog{background:#252528;} QLabel{color:#ccc;}")
        lay = QVBoxLayout(dlg)
        sliders = {}
        val_labels = {}

        def _do_preview():
            params = {k: s.value() for k, s in sliders.items()}
            for k, s in sliders.items():
                val_labels[k].setText(str(s.value()))
            try:
                filt = self._filter_apply(original, kind, params)
                layer.pixels = self._confine_to_selection(layer, original, filt)
                self._redraw()
            except Exception:
                pass

        dlg._preview_timer = QTimer()
        dlg._preview_timer.setSingleShot(True)
        dlg._preview_timer.timeout.connect(_do_preview)

        def schedule_preview():
            dlg._preview_timer.start(80)

        for pkey, plabel, pmin, pmax, pdef in spec:
            row = QHBoxLayout()
            row.addWidget(QLabel(plabel))
            s = QSlider(Qt.Orientation.Horizontal)
            s.setRange(pmin, pmax); s.setValue(pdef)
            s.valueChanged.connect(lambda _v: schedule_preview())
            row.addWidget(s, 1)
            vl = QLabel(str(pdef)); vl.setFixedWidth(32)
            row.addWidget(vl)
            sliders[pkey] = s
            val_labels[pkey] = vl
            lay.addLayout(row)
        bl = QHBoxLayout()
        ok = QPushButton("确定"); cancel = QPushButton("取消")
        for b in (ok, cancel):
            b.setStyleSheet("QPushButton{background:#3a3a3e;color:#ccc;border:none;"
                            "border-radius:3px;padding:4px 14px;}"
                            "QPushButton:hover{background:#4a4a4e;color:#fff;}")
        ok.setStyleSheet(ok.styleSheet().replace("#3a3a3e", "#3d8ef8").replace("color:#ccc", "color:#fff"))
        ok.clicked.connect(dlg.accept); cancel.clicked.connect(dlg.reject)
        bl.addStretch(); bl.addWidget(ok); bl.addWidget(cancel)
        lay.addLayout(bl)
        dlg.resize(360, dlg.sizeHint().height())
        _do_preview()  # 初始预览
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        dlg._preview_timer.stop()
        if accepted:
            _do_preview()
            self._last_filter = (kind, {k: s.value() for k, s in sliders.items()})
        else:
            layer.pixels = original           # 取消 → 还原
            if pushed:
                self._history.pop(); self._history_idx -= 1
                self._refresh_history_panel()
            self._redraw()

    # ═══════ 模糊 / 羽化 ═══════
    def _blur_selection(self, radius):
        if self.selection is None:
            QMessageBox.information(self, "提示", "先用选区工具框选区域。")
            return
        layer = self.active
        if not layer or layer.kind != "image" or layer.pixels is None:
            return
        self._push_undo("模糊选区")
        base = layer.pixels.astype(np.float32)
        blurred = numpy_from_qimage(self.blur_qimage(qimage_from_numpy(layer.pixels), radius)).astype(np.float32)
        alpha = self.sel_alpha if self.sel_alpha is not None else self.selection.astype(np.float32)
        ys, xs = np.where(alpha > 0.001)
        for y, x in zip(ys.tolist(), xs.tolist()):
            w = float(alpha[y, x])
            base[y, x] = base[y, x] * (1 - w) + blurred[y, x] * w
        layer.pixels = base.astype(np.uint8)
        self._redraw()

    @selection_history('羽化选区')
    def _feather_selection(self, radius, push_undo=False):
        if self.selection is None:
            QMessageBox.information(self, "提示", "请先创建选区。")
            return
        # 羽化值为 0 时直接恢复硬边选区（不模糊）
        if radius <= 0 and self._sel_base is not None:
            self.selection = self._sel_base.copy()
            self.sel_alpha = None
            self._redraw_selection()
            return
        # 始终从羽化前的原始二值选区重算，避免重复羽化叠加
        if self._sel_base is not None:
            alpha = self._sel_base.astype(np.float32)
        else:
            alpha = self.selection.astype(np.float32)
        try:
            import cv2
            k = max(3, int(round(radius * 2)) + 1)
            if k % 2 == 0:
                k += 1
            sigma = max(0.5, radius)
            ab = cv2.GaussianBlur((alpha * 255).astype(np.uint8), (k, k), sigma)
            self.sel_alpha = ab.astype(np.float32) / 255.0
        except Exception:
            self.sel_alpha = self._gaussian_np((alpha * 255).astype(np.uint8), max(0.5, radius))[:, :, 0].astype(np.float32) / 255.0
        self.selection = self.sel_alpha > 0.5
        if self._sel_base is not None and not self.selection.any():
            # 羽化把整个选区抹没了，回退到原始硬边
            self.selection = self._sel_base.copy()
        self._redraw_selection()
    # ═══════ 笔刷 / 橡皮 ═══════

    # ═══════ 克隆图章 / 修复画笔 ═══════


    def _content_aware_fill(self):
        """用附近匹配的背景纹理修复当前选区。"""
        layer = self.active
        if not layer or layer.kind != "image" or layer.pixels is None:
            QMessageBox.information(self, "提示", "请选中一个图片图层，并建立选区。")
            return
        if self.selection is None:
            QMessageBox.information(self, "提示", "请先用选区工具框选要填充的区域。")
            return
        try:
            import cv2
        except Exception:
            QMessageBox.information(self, "提示", "opencv 不可用，无法内容识别填充。")
            return
        from core.image_effects import pixel_matrix

        inverse, valid = layer.canvas_transform().inverted()
        if not valid:
            QMessageBox.information(self, "提示", "当前图层变换无法用于填充。")
            return
        matrix = [[inverse.m11(), inverse.m21(), inverse.m31()],
                  [inverse.m12(), inverse.m22(), inverse.m32()],
                  [inverse.m13(), inverse.m23(), inverse.m33()]]
        height, width = layer.pixels.shape[:2]
        mask = cv2.warpPerspective(
            self.selection.astype(np.uint8), pixel_matrix(matrix),
            (width, height), flags=cv2.INTER_NEAREST)
        if not mask.any():
            QMessageBox.information(self, "提示", "选区内没有该图层的像素。")
            return
        if mask.all():
            QMessageBox.information(self, "提示", "请缩小选区，保留一些周围像素供填充参考。")
            return

        try:
            from core.content_aware_fill import fill_from_background

            filled = fill_from_background(layer.pixels[:, :, :3], mask,
                                          valid=layer.pixels[:, :, 3] > 0)
        except cv2.error as exc:
            QMessageBox.warning(self, "内容识别填充失败", str(exc))
            return
        selected = mask.astype(bool)
        result = layer.pixels.copy()
        result[selected, :3] = filled[selected]
        result[selected, 3] = 255
        self._push_undo("内容识别填充")
        layer.pixels = result
        self._redraw()
        self._refresh_layers()

    # ═══════ 移动 ═══════

    def _move_leaf_to(self, pt, redraw=True):
        if not self._move_olds:
            return
        start = self.view._start  # CanvasView 上记录的鼠标起点
        dx = pt.x() - start.x()
        dy = pt.y() - start.y()
        # P5 智能参考线：把选中图层整体 bbox 的边/中心吸附到画布或其余图层
        if self.smart_guides_on:
            cand_xs, cand_ys = [], []
            for l in self.selected:
                key = id(l)
                if key not in self._move_olds:
                    continue
                ox, oy = self._move_olds[key]
                sx, sy = l.x, l.y
                l.x, l.y = ox + dx, oy + dy
                b = self._layer_bbox(l)
                l.x, l.y = sx, sy
                cand_xs += [b.left(), (b.left() + b.right()) / 2, b.right()]
                cand_ys += [b.top(), (b.top() + b.bottom()) / 2, b.bottom()]
            sdx, sdy, guides = self._snap_bbox_lines(cand_xs, cand_ys, set(self.selected))
            dx += sdx; dy += sdy
            self._smart_guides = guides
        else:
            self._smart_guides = []
        for l in self.selected:
            if id(l) in self._move_olds:
                ox, oy = self._move_olds[id(l)]
                l.x = ox + dx
                l.y = oy + dy
        if redraw:
            self._redraw()
            self.view.viewport().update()


    def _transform_copy(self):
        """PS Ctrl+Alt+T：复制当前图层并立即进入自由变换。"""
        if self.active is None or self._ft_active:
            return
        self._duplicate_layer(self.active)   # 内部已 push_undo 并 set_active 到副本
        self._start_free_transform()

    # ═══════ P4 自由变换（Ctrl+T，PS 风格确认/取消） ═══════
    def _start_free_transform(self):
        if self._text_editing:
            self._end_text_edit(save=True)
        if self._ft_active:
            return
        l = self.active
        if l is None or getattr(l, "locked", False) or l.kind == "adjust":
            return
        self._ft_tree_before = self._snapshot(metadata_only=True)
        self._push_undo("自由变换")
        self._ft_saved = (l, l.x, l.y, l.scale, l.rotation, l.skew_x, l.skew_y)
        self._ft_active = True
        self.set_tool(Tool.MOVE)
        self._show_handles = True
        self._show_ft_bar()
        self._redraw()

    def _show_ft_bar(self):
        if self._ft_bar is None:
            bar = QWidget(self.view)
            bar.setStyleSheet(
                "QWidget{background:#252528;border:1px solid #3d8ef8;border-radius:4px;}")
            hl = QHBoxLayout(bar)
            hl.setContentsMargins(8, 4, 8, 4)
            hl.setSpacing(6)
            tip = QLabel("自由变换：拖动手柄缩放/旋转")
            tip.setStyleSheet("color:#ccc;font-size:11px;border:none;")
            hl.addWidget(tip)
            ok = QPushButton("✓ 确认 (Enter)")
            ok.setStyleSheet("QPushButton{background:#3d8ef8;color:#fff;border:none;"
                             "border-radius:3px;padding:3px 10px;font-size:11px;}"
                             "QPushButton:hover{background:#5aa0ff;}")
            ok.clicked.connect(self._ft_commit)
            hl.addWidget(ok)
            cancel = QPushButton("✗ 取消 (Esc)")
            cancel.setStyleSheet("QPushButton{background:#3a3a3e;color:#ccc;border:none;"
                                 "border-radius:3px;padding:3px 10px;font-size:11px;}"
                                 "QPushButton:hover{background:#4a4a4e;color:#fff;}")
            cancel.clicked.connect(self._ft_cancel)
            hl.addWidget(cancel)
            self._ft_bar = bar
        self._ft_bar.adjustSize()
        vw = self.view.viewport().width()
        self._ft_bar.move(max(0, (vw - self._ft_bar.width()) // 2), 10)
        self._ft_bar.show()
        self._ft_bar.raise_()

    def _ft_commit(self):
        """确认变换：保留当前状态（撤销点已在进入时压入）。"""
        if not self._ft_active:
            return
        self._ft_active = False
        self._ft_tree_before = None
        self._ft_saved = None
        if self._ft_bar:
            self._ft_bar.hide()
        self.setFocus()

    def _ft_cancel(self):
        """取消变换：还原进入时的位置/缩放/旋转/斜切。"""
        if not self._ft_active:
            return
        if getattr(self, '_ft_tree_before', None) is not None:
            before = self._ft_tree_before
            self._ft_tree_before = None
            self._ft_active = False
            self._ft_saved = None
            if self._ft_bar:
                self._ft_bar.hide()
            self._restore(before)
            self.setFocus()
            return
        if self._ft_saved:
            l, x, y, sc, rot, skx, sky = self._ft_saved
            l.x, l.y, l.scale, l.rotation = x, y, sc, rot
            l.skew_x, l.skew_y = skx, sky
        self._ft_active = False
        self._ft_saved = None
        if self._ft_bar:
            self._ft_bar.hide()
        self._redraw()
        self._sync_props()
        self.setFocus()

    # ═══════ 文字 / 形状 ═══════
    # ═══════ 文字：画布内联编辑（替代模态弹窗） ═══════

    def _start_text_edit(self, layer, is_new, pt=None):
        """进入画布内联文字编辑态（参考视频区字幕输入方式，画布渲染 + IME 直采）。
        is_new=True 时先建一个"待定"空文字层对象，但**暂不**加入工程——直到真正键入
        字符才提交进图层列表（PS 式延迟建层），避免点一下画布就多一个空图层。
        焦点交给 CanvasView，IME 事件由它转发。"""
        if self._text_editing:
            self._end_text_edit(save=True)
        if is_new:
            cw, ch = self._ctx_size()
            if pt is not None:
                local = self._doc_to_local(pt)
                x, y = local.x(), local.y()
            else:
                x, y = cw / 2.0, ch / 2.0
            layer = ImageLayer("文字", kind="text", w=200, h=80)
            layer.x = x
            layer.y = y
            layer.text = ""
            layer.font_size = 48
            # 注意：此处不 append / 不 set_active，等首次实际输入再 _commit_new_text_layer()
        else:
            # 编辑已有图层：它已经在工程里
            self._text_edit_added = True
            self._select_layer_only(layer)
        self._text_editing = True
        self._text_edit_layer = layer
        self._text_edit_was_new = is_new
        self._text_edit_orig = layer.text
        self._edit_flat = layer.text
        self._edit_cursor = len(layer.text)
        self._ime_active = False
        self._ime_preedit = ""
        self._ime_compose_start = 0
        self._edit_blink = True
        self._refresh_layers()
        self._redraw()
        # 焦点交给画布本身（IME 事件由 CanvasView 转发给本类），无需子控件覆盖层
        self.view.setFocus()
        self._update_text_cursor_rect()
        self._edit_blink_timer.start()

    def _end_text_edit(self, save=True):
        """退出文字编辑态（PS 式延迟建层）：
        - 已有文字层：save=True 写入文本；False 回滚原文本。
        - 新建文字层：真正键入字符后才加入工程；若最终文本为空（点一下没输入 /
          全部删空），无论 save 与否都直接丢弃，不残留空图层，并撤销该次操作快照。
        """
        if not self._text_editing:
            return
        self._edit_blink_timer.stop()
        layer = self._text_edit_layer
        pre = self._snapshot()   # pre-state：文字编辑前的状态（仅接受且有变化时才入栈）
        was_new = self._text_edit_was_new
        added = self._text_edit_added
        orig = self._text_edit_orig
        flat = self._edit_flat
        self._text_editing = False
        self._text_edit_layer = None
        self._text_edit_was_new = False
        self._text_edit_added = False
        self._edit_flat = ""
        self._edit_cursor = 0
        self._ime_active = False
        self._ime_preedit = ""
        self._ime_compose_start = 0
        if layer is None:
            self._redraw()
            return
        # 新建层但最终无文本 → 丢弃（不进工程、不留空图层，无撤销步）
        if was_new and not flat.strip():
            parent = self._parent_list_of(layer)
            if layer in parent:
                parent.remove(layer)
            if self.active is layer:
                self.active = parent[-1] if parent else None
            self._refresh_layers()
            self._redraw()
            return
        if not save:
            if was_new:
                parent = self._parent_list_of(layer)
                if layer in parent:
                    parent.remove(layer)
                if self.active is layer:
                    self.active = parent[-1] if parent else None
            else:
                layer.text = orig
            self._refresh_layers()
            self._redraw()
            return
        # Paragraph bounds are explicit geometry; accepting text must not shrink them.
        layer.text = flat
        # pre-state 入栈：仅文本确有变化才记录撤销步
        if flat != orig:
            self._push_undo_snapshot("编辑文字", pre)
        self._refresh_layers()
        self._redraw()

    def _commit_new_text_layer(self):
        """PS 式延迟建层：新建文字层在首次键入实际字符时才真正加入工程与图层列表。
        已加入则幂等返回。"""
        if not (self._text_editing and self._text_edit_was_new
                and not self._text_edit_added):
            return
        layer = self._text_edit_layer
        if layer is None:
            return
        if self.active_artboard is not None:
            self.active_artboard.layers.append(layer)
        else:
            self.project.add_layer(layer)
        self._text_edit_added = True
        self.set_active(layer)

    def _on_edit_blink(self):
        """光标闪烁开关：轻量重绘。"""
        if not self._text_editing:
            self._edit_blink_timer.stop()
            return
        self._edit_blink = not self._edit_blink
        self._redraw()




    def inputMethodEvent(self, event):
        """IME 事件：中文/日文/韩文等组合文本输入。
        预编辑文本仅屏幕显示，提交文本写入 _edit_flat（设计同视频区字幕编辑器）。"""
        if not self._text_editing or self._text_edit_layer is None:
            event.ignore()
            return
        commit = event.commitString()
        preedit = event.preeditString()
        flat = self._edit_flat
        # IME 开始组合（之前无预编辑，现在有了）→ 记录组合起始位置
        if preedit and not self._ime_preedit:
            self._ime_compose_start = self._edit_cursor
        if commit:
            pos = self._ime_compose_start
            if len(flat) + len(commit) > 4000:
                event.ignore()
                return
            flat = flat[:pos] + commit + flat[pos:]
            self._edit_flat = flat
            self._edit_cursor = pos + len(commit)
            self._ime_preedit = ""
            self._ime_active = False
            self._commit_new_text_layer()
        elif preedit:
            self._ime_preedit = preedit
            self._ime_active = True
        else:
            self._ime_preedit = ""
            self._ime_active = False
        self._edit_blink = True
        event.accept()
        self._redraw()

    def inputMethodQuery(self, query):
        if not self._text_editing:
            return super().inputMethodQuery(query)
        if query == Qt.InputMethodQuery.ImEnabled:
            return True
        if query == Qt.InputMethodQuery.ImCursorRectangle:
            r = self._edit_cursor_rect
            return QRectF(r.x(), r.y(), max(r.width(), 2), max(r.height(), 10))
        if query == Qt.InputMethodQuery.ImCursorPosition:
            return self._ime_compose_start if self._ime_preedit else self._edit_cursor
        if query == Qt.InputMethodQuery.ImSurroundingText:
            return self._edit_flat
        if query == Qt.InputMethodQuery.ImCurrentSelection:
            return ""
        return super().inputMethodQuery(query)

    def _text_edit_key(self, e):
        """文字编辑态键处理：直接捕获键入，不经过快捷键分发。"""
        key = e.key()
        text = e.text()
        flat = self._edit_flat
        cur = self._edit_cursor

        def _r():
            self._redraw()

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # Enter = 提交；Shift+Enter = 换行后提交
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                flat = flat[:cur] + "\n" + flat[cur:]
                cur += 1
                self._edit_flat = flat
                self._edit_cursor = cur
                self._commit_new_text_layer()
            self._end_text_edit(save=True)
            return
        if key == Qt.Key.Key_Escape:
            self._end_text_edit(save=False)
            return
        if key == Qt.Key.Key_Backspace:
            if self._ime_active:
                e.ignore()
                super().keyPressEvent(e)
                return
            if cur > 0:
                flat = flat[:cur - 1] + flat[cur:]
                cur -= 1
            self._edit_flat = flat
            self._edit_cursor = cur
            self._edit_blink = True
            _r()
            return
        if key == Qt.Key.Key_Delete:
            if self._ime_active:
                e.ignore()
                super().keyPressEvent(e)
                return
            if cur < len(flat):
                flat = flat[:cur] + flat[cur + 1:]
            self._edit_flat = flat
            self._edit_cursor = cur
            self._edit_blink = True
            _r()
            return
        if key == Qt.Key.Key_Left:
            if cur > 0:
                self._edit_cursor = cur - 1
                self._edit_blink = True
                _r()
            return
        if key == Qt.Key.Key_Right:
            if cur < len(flat):
                self._edit_cursor = cur + 1
                self._edit_blink = True
                _r()
            return
        if key == Qt.Key.Key_Home:
            self._edit_cursor = 0
            self._edit_blink = True
            _r()
            return
        if key == Qt.Key.Key_End:
            self._edit_cursor = len(flat)
            self._edit_blink = True
            _r()
            return
        # 可打印字符（英文直接键入；中文由 inputMethodEvent 处理）
        if text and len(text) == 1 and text.isprintable() and not self._ime_active:
            if len(flat) >= 4000:
                return
            flat = flat[:cur] + text + flat[cur:]
            cur += 1
            self._edit_flat = flat
            self._edit_cursor = cur
            self._edit_blink = True
            self._commit_new_text_layer()
            _r()
            return
        super().keyPressEvent(e)

    def _edit_text(self, layer):
        """兼容入口：在画布内直接编辑已有文字层。"""
        if layer is None:
            return
        self._start_text_edit(layer, is_new=False)

    def _preview_shape(self, tool, rect):
        # 画板模式下使用文档尺寸，否则 rect（文档坐标）会超出预览图范围
        if self.active_artboard is not None:
            w, h = self._doc_bounds()
        else:
            w, h = self.project.w, self.project.h
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        # 描边（虚线预览）
        if self.shape_stroke_on and self.shape_stroke_w > 0:
            pen = QPen(self.shape_stroke_color, max(1, self.shape_stroke_w))
        else:
            pen = QPen(QColor(255, 255, 255), 1)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setDashPattern([6, 4])
        p.setPen(pen)
        # 填充
        if self.shape_fill_on:
            fc = self.shape_fill_color
            p.setBrush(QBrush(QColor(fc.red(), fc.green(), fc.blue(), 70)))
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
        if tool == Tool.SHAPE_LINE:
            dx,dy = getattr(self,'_line_direction',(1,1))
            p.drawLine(QPointF(rect.left() if dx>=0 else rect.right(),rect.top() if dy>=0 else rect.bottom()),
                       QPointF(rect.right() if dx>=0 else rect.left(),rect.bottom() if dy>=0 else rect.top()))
        elif tool == Tool.SHAPE_RECT:
            p.drawRect(rect)
        else:
            p.drawEllipse(rect)
        p.end()
        self.view.shape_preview_item.setPixmap(QPixmap.fromImage(img))

    def _commit_shape(self, tool, rect):
        self.view.shape_preview_item.setPixmap(QPixmap())
        if tool==Tool.SHAPE_LINE and rect is not None:
            if rect.width()+rect.height()<2:
                return
            rect = QRectF(rect)
            rect.setWidth(max(2.,rect.width()))
            rect.setHeight(max(2.,rect.height()))
        if rect is None or rect.width() < 2 or rect.height() < 2:
            return
        self._push_undo("绘制形状")
        # 画板模式下：rect 是文档坐标，需转为画板本地坐标（画板渲染时使用 0,0 原点）
        if self.active_artboard is not None:
            sx, sy = self._ab_screen(self.active_artboard)
            ox, oy = sx, sy
            rect = QRectF(rect.x() - ox, rect.y() - oy, rect.width(), rect.height())
        # 给形状四周留出透明内边距，避免变换手柄（画在图层包围盒边缘）压住图形本身
        pad = max(self.HANDLE_SIZE, 6)
        layer = ImageLayer("形状", kind="shape",
                          w=int(rect.width()) + pad * 2, h=int(rect.height()) + pad * 2)
        layer.shape = "rect" if tool == Tool.SHAPE_RECT else "ellipse"
        if tool==Tool.SHAPE_LINE:
            layer.shape = 'line'
            sx,sy = getattr(self,'_line_direction',(1,1))
            layer.line_start = (0. if sx>=0 else 1.,0. if sy>=0 else 1.)
            layer.line_end = (1.-layer.line_start[0],1.-layer.line_start[1])
            if abs(sx)<.001:
                layer.line_start = (.5,layer.line_start[1])
                layer.line_end = (.5,layer.line_end[1])
            if abs(sy)<.001:
                layer.line_start = (layer.line_start[0],.5)
                layer.line_end = (layer.line_end[0],.5)
            layer.line_style,layer.line_cap = self.shape_line_style,self.shape_line_cap
        layer.rect = QRectF(pad, pad, rect.width(), rect.height())
        layer.x = rect.x() + rect.width() / 2
        layer.y = rect.y() + rect.height() / 2
        layer.filled = self.shape_fill_on
        layer.fill_color = self.shape_fill_color.name()
        layer.gradient = self.shape_gradient_on
        layer.grad_from = self.shape_grad_from.name()
        layer.grad_to = self.shape_grad_to.name()
        layer.stroke_on = self.shape_stroke_on
        layer.stroke_w = self.shape_stroke_w if self.shape_stroke_on else 0
        layer.color = self.shape_stroke_color.name()
        self.project.add_layer(layer)
        # 画板模式下形状应加到对应画板，否则不可见且无法选中
        if self.active_artboard is not None:
            self.project.layers.remove(layer)
            self.active_artboard.layers.append(layer)
        self.set_active(layer)
        self._refresh_layers()
        self._redraw()
        # 画完形状自动切到移动工具，方便调整大小/旋转或点空白取消选中
        self.set_tool(Tool.MOVE)

    # ═══════ 吸管 ═══════
    def _pick_color(self, pt):
        img = self._render_composite()
        x, y = int(pt.x()), int(pt.y())
        if 0 <= x < img.width() and 0 <= y < img.height():
            c = img.pixelColor(x, y)
            if c.alpha() > 0:
                self.fg = c
                self.shape_fill_color = c
                self._update_color_btn(self.fg_btn, c)
                self._update_color_btn(self._sh_fill_btn, c)
                if hasattr(self, "_swatch_btn"):
                    self._update_color_btn(self._swatch_btn, c)
                self._sync_color_ui()
                self.ai_status.setText("💧 已拾取颜色 {}（已更新填充色块）".format(c.name()))
                self.ai_status.setVisible(True)
                QTimer.singleShot(2000, lambda: self.ai_status.setVisible(False))
                return
        # 点在了透明 / 画布外区域：明确告知，避免「点了没反应」
        self.ai_status.setText("💧 该处透明（或在画布外），未拾取颜色——请点在图像像素上")
        self.ai_status.setVisible(True)
        QTimer.singleShot(2600, lambda: self.ai_status.setVisible(False))

    def _update_eyedrop_preview(self, x, y, vx, vy):
        """更新吸管悬停取色 HUD 的位置与颜色（编辑区「小标志」）。

        x,y 为文档坐标（用于从缓存合成图取色）；vx,vy 为视口坐标（用于绘制 HUD）。
        """
        img = getattr(self, "_eyedrop_img", None)
        color = None
        if img is not None and not img.isNull():
            if 0 <= x < img.width() and 0 <= y < img.height():
                c = img.pixelColor(x, y)
                if c.alpha() > 0:
                    color = c
        self._eyedrop_preview = (vx, vy, color)
        try:
            self.view.viewport().update()
        except Exception:
            pass

    def _pick_color_dialog(self, holder):
        c = choose_color(holder[0], self)
        if c.isValid():
            holder[0] = c

    def _choose_fg(self):
        c = choose_color(self.fg, self, '前景色')
        if c.isValid():
            self.fg = c
            self._sync_color_ui()

    def _choose_secondary_color(self):
        color = choose_color(self.bg, self, '背景色 · 备用颜色')
        if color.isValid():
            self.bg = color
            self._sync_color_ui()

    def _sync_color_ui(self):
        self._update_color_btn(self.fg_btn, self.fg)
        if hasattr(self, '_color_card'):
            self._color_card.sync(self)

    def _choose_bg_color(self):
        target = self.active_artboard or self.project
        c = choose_color(target.bg_color, self, '画布背景色')
        if c.isValid():
            if c == target.bg_color:
                return
            self._push_undo("更改背景色")
            target.bg_color = c
            self._redraw()

    # ── 形状工具选项条（填充/描边 开关 + 粗细 + 颜色）──
    def _set_shape_fill_on(self, on):
        self.shape_fill_on = on
        if self.active and self.active.kind == "shape":
            if self.active.filled == on:
                return
            self._push_undo("切换形状填充")
            self.active.filled = on
            self._redraw()

    def _set_shape_stroke_on(self, on):
        self.shape_stroke_on = on
        if on and self.shape_stroke_w < 1:
            self.shape_stroke_w = 2
            self._sh_stroke_w.setValue(2)
        if self.active and self.active.kind == "shape":
            if self.active.stroke_on == on:
                return
            self._push_undo("切换形状描边")
            self.active.stroke_on = on
            if on:
                self.active.stroke_w = max(self.active.stroke_w, 1)
            self._redraw()

    def _set_shape_stroke_w(self, v):
        self.shape_stroke_w = v
        if self.active and self.active.kind == "shape":
            if self.active.stroke_w == v:
                return
            self._queue_property_undo("调整形状描边宽度")
            self.active.stroke_w = v
            self._redraw()

    def _choose_shape_fill_color(self):
        c = choose_color(self.shape_fill_color, self, '形状填充色')
        if c.isValid():
            self.shape_fill_color = c
            self._update_color_btn(self._sh_fill_btn, c)
            if hasattr(self, "_swatch_btn"):
                self._update_color_btn(self._swatch_btn, c)
            if self.active and self.active.kind == "shape":
                self._push_undo("更改形状填充色")
                self.active.fill_color = c.name()
                self._redraw()

    def _choose_shape_stroke_color(self):
        c = choose_color(self.shape_stroke_color, self, '形状描边色')
        if c.isValid():
            self.shape_stroke_color = c
            self._update_color_btn(self._sh_stroke_btn, c)
            if self.active and self.active.kind == "shape":
                self._push_undo("更改形状描边色")
                self.active.color = c.name()
                self._redraw()

    def _set_shape_gradient_on(self, on):
        self.shape_gradient_on = on
        if self.active and self.active.kind == "shape":
            if self.active.gradient == on:
                return
            self._queue_property_undo("切换形状渐变")
            self.active.gradient = on
            self._redraw()

    def _choose_shape_grad_from(self):
        c = choose_color(self.shape_grad_from, self, '渐变起始色')
        if c.isValid():
            self.shape_grad_from = c
            self._update_color_btn(self._sh_grad_from_btn, c)
            if self.active and self.active.kind == "shape":
                self._push_undo("更改渐变起始色")
                self.active.grad_from = c.name()
                self._redraw()

    def _choose_shape_grad_to(self):
        c = choose_color(self.shape_grad_to, self, '渐变结束色')
        if c.isValid():
            self.shape_grad_to = c
            self._update_color_btn(self._sh_grad_to_btn, c)
            if self.active and self.active.kind == "shape":
                self._push_undo("更改渐变结束色")
                self.active.grad_to = c.name()
                self._redraw()

    def _sync_shape_options(self):
        """选中已有形状层时，把其参数同步到选项条（PS 行为）。"""
        a = self.active
        if a is None or a.kind != "shape":
            return
        self.shape_fill_on = a.filled
        self.shape_fill_color = QColor(a.fill_color)
        self.shape_gradient_on = a.gradient
        self.shape_grad_from = QColor(a.grad_from)
        self.shape_grad_to = QColor(a.grad_to)
        self.shape_stroke_on = a.stroke_on
        self.shape_stroke_color = QColor(a.color)
        self.shape_stroke_w = a.stroke_w if a.stroke_w > 0 else 2
        self._sh_fill_chk.setChecked(a.filled)
        self._update_color_btn(self._sh_fill_btn, QColor(a.fill_color))
        self._sh_grad_chk.setChecked(a.gradient)
        self._update_color_btn(self._sh_grad_from_btn, QColor(a.grad_from))
        self._update_color_btn(self._sh_grad_to_btn, QColor(a.grad_to))
        self._sh_stroke_chk.setChecked(a.stroke_on)
        self._sh_stroke_w.setValue(self.shape_stroke_w)
        self._update_color_btn(self._sh_stroke_btn, QColor(a.color))

    # ═══════ 导出 / 工程 ═══════
    def _save_delivery_image(self, image, path, fmt, quality=-1, label=""):
        if not image.save(path, fmt, quality):
            QMessageBox.warning(self, "导出失败", f"无法保存图片：{path}")
            return
        offer_duo_export(self, [path], "图片工作台", "导出完成", label or f"已导出：{path}")

    def export_png(self):
        if self.project.artboards:
            ab = self.active_artboard or self.project.artboards[-1]
            path, _ = QFileDialog.getSaveFileName(self, "导出画板 PNG", "",
                                                  "PNG (*.png)")
            if not path:
                return
            if not path.lower().endswith(".png"):
                path += ".png"
            img = self._render_artboard(ab)
            self._save_delivery_image(img, path, "PNG", label=f"已导出画板「{ab.name}」：{path}")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 PNG", "", "PNG (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        img = self._render_composite(for_export=True)
        self._save_delivery_image(img, path, "PNG")

    def export_jpg(self):
        if self.project.artboards:
            ab = self.active_artboard or self.project.artboards[-1]
            path, _ = QFileDialog.getSaveFileName(self, "导出画板 JPG", "",
                                                  "JPG (*.jpg)")
            if not path:
                return
            if not path.lower().endswith(".jpg"):
                path += ".jpg"
            img = self._render_artboard(ab)
            if ab.transparent:
                fill = QImage(img.size(), QImage.Format.Format_RGB32)
                fill.fill(Qt.GlobalColor.white)
                p = QPainter(fill); p.drawImage(0, 0, img); p.end()
                img = fill
            self._save_delivery_image(img, path, "JPG", 95, f"已导出画板「{ab.name}」：{path}")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 JPG", "", "JPG (*.jpg)")
        if not path:
            return
        if not path.lower().endswith(".jpg"):
            path += ".jpg"
        img = self._render_composite(for_export=True)
        if self.project.transparent:
            fill = QImage(img.size(), QImage.Format.Format_RGB32)
            fill.fill(self.project.bg_color if not self.project.transparent else Qt.GlobalColor.white)
            p = QPainter(fill)
            p.drawImage(0, 0, img)
            p.end()
            img = fill
        self._save_delivery_image(img, path, "JPG", 95)

    def export_all_artboards(self):
        """批量导出每个画板为独立的 PNG（PS「导出画板」行为）。"""
        if not self.project.artboards:
            QMessageBox.information(self, "提示", "当前没有画板，无法批量导出。")
            return
        d = QFileDialog.getExistingDirectory(self, "选择导出文件夹")
        if not d:
            return
        paths = []
        for ab in self.project.artboards:
            safe = "".join(c if (c.isalnum() or c in "-_ ") else "_" for c in ab.name)
            img = self._render_artboard(ab)
            p = os.path.join(d, f"{safe}.png")
            if img.save(p, "PNG"):
                paths.append(p)
        offer_duo_export(self, paths, "图片工作台", "画板导出结束",
                         f"已导出 {len(paths)}/{len(self.project.artboards)} 个画板到：{d}")


    def export_as(self):
        """导出为…：格式下拉(PNG/JPG/WebP) + 质量滑杆。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("导出为…")
        dlg.setStyleSheet("QDialog{background:#252528;} QLabel{color:#ccc;}")
        lay = QVBoxLayout(dlg)
        # 格式
        frow = QHBoxLayout()
        frow.addWidget(QLabel("格式"))
        fmt_combo = QComboBox()
        fmt_combo.addItems(["PNG（无损，支持透明）", "JPG（有损，体积小）", "WebP（现代格式，兼顾）"])
        fmt_combo.setStyleSheet(
            "QComboBox{background:#2a2a2e;color:#ccc;border:1px solid #3a3a3e;"
            "border-radius:3px;padding:2px 6px;}"
            "QComboBox QAbstractItemView{background:#252528;color:#ccc;"
            "selection-background-color:#3d8ef8;}")
        frow.addWidget(fmt_combo, 1)
        lay.addLayout(frow)
        # 质量
        qrow = QHBoxLayout()
        qrow.addWidget(QLabel("质量"))
        q_slider = QSlider(Qt.Orientation.Horizontal)
        q_slider.setRange(10, 100); q_slider.setValue(90)
        qrow.addWidget(q_slider, 1)
        q_label = QLabel("90"); q_label.setFixedWidth(28)
        qrow.addWidget(q_label)
        lay.addLayout(qrow)
        q_slider.valueChanged.connect(lambda v: q_label.setText(str(v)))

        def _upd_quality(idx):
            en = idx != 0   # PNG 无损，质量无效
            q_slider.setEnabled(en)
            q_label.setStyleSheet("" if en else "color:#666;")
        fmt_combo.currentIndexChanged.connect(_upd_quality)
        _upd_quality(0)

        bl = QHBoxLayout()
        ok = QPushButton("导出…"); cancel = QPushButton("取消")
        ok.setStyleSheet("QPushButton{background:#3d8ef8;color:#fff;border:none;"
                         "border-radius:3px;padding:4px 14px;}"
                         "QPushButton:hover{background:#5aa0ff;}")
        cancel.setStyleSheet("QPushButton{background:#3a3a3e;color:#ccc;border:none;"
                             "border-radius:3px;padding:4px 14px;}"
                             "QPushButton:hover{background:#4a4a4e;color:#fff;}")
        ok.clicked.connect(dlg.accept); cancel.clicked.connect(dlg.reject)
        bl.addStretch(); bl.addWidget(ok); bl.addWidget(cancel)
        lay.addLayout(bl)
        dlg.resize(340, dlg.sizeHint().height())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        fmt = ("png", "jpg", "webp")[fmt_combo.currentIndex()]
        quality = q_slider.value()
        filt = {"png": "PNG (*.png)", "jpg": "JPG (*.jpg)", "webp": "WebP (*.webp)"}[fmt]
        path, _ = QFileDialog.getSaveFileName(self, "导出", "", filt)
        if not path:
            return
        if not path.lower().endswith("." + fmt):
            path += "." + fmt
        # 渲染
        if self.project.artboards:
            ab = self.active_artboard or self.project.artboards[-1]
            img = self._render_artboard(ab)
        else:
            img = self._render_composite(for_export=True)
        # JPG 不支持透明 → 白底
        if fmt == "jpg":
            fill = QImage(img.size(), QImage.Format.Format_RGB32)
            fill.fill(Qt.GlobalColor.white)
            p = QPainter(fill); p.drawImage(0, 0, img); p.end()
            img = fill
        self._save_delivery_image(img, path, fmt.upper(), -1 if fmt == "png" else quality,
                                  f"已导出（{fmt.upper()}）：{path}")

    # 信息流常用尺寸预设：(名称, 宽, 高)
    EXPORT_SIZE_PRESETS = [
        ("原始尺寸", 0, 0),
        ("抖音/快手竖版 1080×1920", 1080, 1920),
        ("小红书 3:4 1080×1440", 1080, 1440),
        ("方图 1080×1080", 1080, 1080),
        ("横版 16:9 1920×1080", 1920, 1080),
        ("横版信息流 1280×720", 1280, 720),
        ("Banner 1080×608", 1080, 608),
    ]

    def export_batch_multisize(self):
        """批量导出：所有画板 × 勾选的多个尺寸 → 文件夹。"""
        if not self.project.artboards:
            QMessageBox.information(self, "提示",
                "批量多尺寸导出需要画板。\n请先用「▦ 画板」创建画板再导出。")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("批量导出（画板 × 尺寸）")
        dlg.setStyleSheet("QDialog{background:#252528;} QLabel{color:#ccc;} "
                          "QCheckBox{color:#ccc;}")
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(f"将导出 {len(self.project.artboards)} 个画板，勾选目标尺寸："))
        checks = []
        for name, w, h in self.EXPORT_SIZE_PRESETS:
            cb = QCheckBox(name)
            cb.setChecked(w == 0)  # 默认勾选原始尺寸
            checks.append((cb, w, h))
            lay.addWidget(cb)
        # 格式 + 质量
        frow = QHBoxLayout()
        frow.addWidget(QLabel("格式"))
        fmt_combo = QComboBox(); fmt_combo.addItems(["PNG", "JPG", "WebP"])
        fmt_combo.setStyleSheet(
            "QComboBox{background:#2a2a2e;color:#ccc;border:1px solid #3a3a3e;"
            "border-radius:3px;padding:2px 6px;}"
            "QComboBox QAbstractItemView{background:#252528;color:#ccc;"
            "selection-background-color:#3d8ef8;}")
        frow.addWidget(fmt_combo)
        frow.addWidget(QLabel("质量"))
        q_spin = QSpinBox(); q_spin.setRange(10, 100); q_spin.setValue(90)
        q_spin.setStyleSheet("QSpinBox{background:#2a2a2e;color:#ccc;"
                             "border:1px solid #3a3a3e;border-radius:3px;padding:2px;}")
        frow.addWidget(q_spin); frow.addStretch()
        lay.addLayout(frow)
        bl = QHBoxLayout()
        ok = QPushButton("选择文件夹并导出"); cancel = QPushButton("取消")
        ok.setStyleSheet("QPushButton{background:#3d8ef8;color:#fff;border:none;"
                         "border-radius:3px;padding:4px 14px;}"
                         "QPushButton:hover{background:#5aa0ff;}")
        cancel.setStyleSheet("QPushButton{background:#3a3a3e;color:#ccc;border:none;"
                             "border-radius:3px;padding:4px 14px;}"
                             "QPushButton:hover{background:#4a4a4e;color:#fff;}")
        ok.clicked.connect(dlg.accept); cancel.clicked.connect(dlg.reject)
        bl.addStretch(); bl.addWidget(ok); bl.addWidget(cancel)
        lay.addLayout(bl)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        sizes = [(cb.text(), w, h) for cb, w, h in checks if cb.isChecked()]
        if not sizes:
            QMessageBox.information(self, "提示", "请至少勾选一个尺寸。")
            return
        d = QFileDialog.getExistingDirectory(self, "选择导出文件夹")
        if not d:
            return
        fmt = fmt_combo.currentText().lower()
        quality = q_spin.value()
        paths = []
        for ab in self.project.artboards:
            safe = "".join(c if (c.isalnum() or c in "-_ ") else "_" for c in ab.name)
            base = self._render_artboard(ab)
            for sname, sw, sh in sizes:
                if sw == 0:  # 原始尺寸
                    img, suffix = base, f"{base.width()}x{base.height()}"
                else:
                    # 等比缩放 + 居中裁剪填满目标尺寸（信息流投放不留黑边）
                    scale = max(sw / base.width(), sh / base.height())
                    scaled = base.scaled(
                        max(1, round(base.width() * scale)),
                        max(1, round(base.height() * scale)),
                        Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
                    img = QImage(sw, sh, QImage.Format.Format_ARGB32)
                    img.fill(Qt.GlobalColor.transparent)
                    p = QPainter(img)
                    p.drawImage((sw - scaled.width()) // 2,
                                (sh - scaled.height()) // 2, scaled)
                    p.end()
                    suffix = f"{sw}x{sh}"
                if fmt == "jpg":
                    fill = QImage(img.size(), QImage.Format.Format_RGB32)
                    fill.fill(Qt.GlobalColor.white)
                    p = QPainter(fill); p.drawImage(0, 0, img); p.end()
                    img = fill
                out_path = os.path.join(d, f"{safe}_{suffix}.{fmt}")
                if img.save(out_path, fmt.upper(), -1 if fmt == "png" else quality):
                    paths.append(out_path)
        offer_duo_export(self, paths, "图片工作台", "图片导出结束",
                         f"已导出 {len(paths)}/{len(self.project.artboards) * len(sizes)} 张图片到：\n{d}")


    def open_template_batch(self):
        from ui.image_template_batch import open_template_batch
        open_template_batch(self)

    def open_project(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, "打开图片工程", "", f"{PIC_FILTER};;旧版图片工程 (*.json)")
        if not path:
            return False
        try:
            snapshot = self.unpack_project(read_project(path, PIC_SUFFIX))
            if not {"w", "h", "layers", "transparent", "bg"} <= snapshot.keys():
                raise ValueError("工程缺少图片画布或图层数据。")
        except Exception as error:
            QMessageBox.warning(self, "打开图片工程失败", str(error))
            return False
        before = self._snapshot()
        self.autosave_recovery(force=True)
        try:
            self._rebuild_from_snap(snapshot)
        except Exception as error:
            self._rebuild_from_snap(before)
            QMessageBox.warning(self, "打开图片工程失败", str(error))
            return False
        self.detach_comp()
        self._start_recovery_session()
        self._source_project_path = str(Path(path).resolve())
        self.set_doc_name(Path(path).stem)
        self._history = [{'name': '打开工程', 'snapshot': self._snapshot()}]
        self._history_idx = 0
        self._mark_recovery_dirty()
        self._recovery_saved_token = self._recovery_token()
        return True

    # ═══════ 合并图层 / AI 抠图 ═══════
    def _merge_down(self):
        layers = self._parent_list_of(self.active) if self.active else self._ctx_layers()
        if not self.active or self.active not in layers:
            return
        i = layers.index(self.active)
        if i == 0:
            QMessageBox.information(self, "提示", "已是最底层，无法向下合并。")
            return
        cw, ch = self._ctx_size()
        below = layers[i - 1]
        above = layers[i]
        img_below = self._render_layer_canvas(below, cw, ch)
        img_above = self._render_layer_canvas(above, cw, ch)
        ab = numpy_from_qimage(img_below).astype(np.float32)
        aa = numpy_from_qimage(img_above).astype(np.float32)
        a = aa[:, :, 3:4] / 255.0
        merged = (ab * (1 - a) + aa * a).astype(np.uint8)
        self._push_undo("向下合并图层")
        # 烘焙到下层（保留其在栈中的位置与可见性）
        below.kind = "image"
        below.pixels = merged
        below.w = cw
        below.h = ch
        below.x = cw / 2.0
        below.y = ch / 2.0
        below.scale = 1.0
        below.rotation = 0.0
        below.clip = False
        below.shadow = False
        below.gradient = False
        below.text = ""
        below.shape = "rect"
        below.rect = QRectF(0, 0, below.w, below.h)
        below.fill_color = "#3d8ef8"
        below.stroke_w = 0
        below.filled = True
        layers.remove(above)
        self.set_active(below)
        self._refresh_layers()
        self._redraw()

    def _merge_selected_layers(self):
        """合并所有已选中的图层（多选右键菜单入口）。"""
        selected = [ly for ly in self.selected if ly is not None]
        if len(selected) < 2:
            self._merge_down()
            return
        layers = self._parent_list_of(self.active) if self.active else self.project.layers
        # 只合并同在一个容器里的图层
        selected = [ly for ly in selected if ly in layers]
        if len(selected) < 2:
            QMessageBox.information(self, "提示", "请选中至少两个同组图层再合并。")
            return
        selected.sort(key=lambda ly: layers.index(ly))  # 从底到顶
        base = selected[0]
        cw, ch = self._ctx_size()
        merged = numpy_from_qimage(self._render_layer_canvas(base, cw, ch)).astype(np.float32)
        for ly in selected[1:]:
            img = self._render_layer_canvas(ly, cw, ch)
            arr = numpy_from_qimage(img).astype(np.float32)
            alpha = arr[:, :, 3:4] / 255.0
            merged = merged * (1 - alpha) + arr * alpha
        merged = merged.astype(np.uint8)
        self._push_undo("合并图层")
        base.kind = "image"
        base.pixels = merged
        base.w = cw; base.h = ch
        base.x = cw / 2.0; base.y = ch / 2.0
        base.scale = 1.0; base.rotation = 0.0
        base.clip = False; base.shadow = False; base.gradient = False
        base.text = ""; base.shape = "rect"
        base.rect = QRectF(0, 0, base.w, base.h)
        base.fill_color = "#3d8ef8"; base.stroke_w = 0; base.filled = True
        for ly in reversed(selected[1:]):
            layers.remove(ly)
        self.set_active(base)
        self.selected = [base]
        self._anchor = None
        self._refresh_layers()
        self._redraw()

    # ═══════ 剪切蒙版 / 图层蒙版 / 栅格化 / 翻转 ═══════
    def _toggle_clip(self, layer):
        """切换图层的剪切蒙版状态。"""
        self._push_undo("剪切蒙版")
        layer.clip = not layer.clip
        self._redraw()
        self._refresh_layers()

    def _add_layer_mask(self, layer):
        """为内容或效果图层添加画布空间的白色蒙版。"""
        if layer is None or layer.locked:
            return
        board = self._active_artboard_of(layer)
        cw, ch = (board.w, board.h) if board else (self.project.w, self.project.h)
        self._push_undo("添加图层蒙版")
        layer.mask = np.ones((ch, cw), dtype=np.uint8) * 255
        self._redraw()
        self._refresh_layers()

    def _remove_layer_mask(self, layer):
        """删除图层蒙版。"""
        if layer.mask is None:
            return
        self._push_undo("删除图层蒙版")
        layer.mask = None
        self._redraw()
        self._refresh_layers()

    def _apply_layer_mask(self, layer):
        """应用图层蒙版：将蒙版烘焙到像素 alpha 通道。"""
        if layer.mask is None or layer.kind != "image" or layer.pixels is None:
            return
        self._push_undo("应用图层蒙版")
        arr = numpy_from_qimage(self._render_layer_canvas(layer))
        layer.pixels = arr
        layer.w = arr.shape[1]
        layer.h = arr.shape[0]
        layer.mask = None
        layer.x, layer.y = layer.w / 2, layer.h / 2
        layer.scale, layer.rotation = 1.0, 0.0
        layer.stretch_x = layer.stretch_y = 1.
        layer.flip_x = layer.flip_y = False
        layer.skew_x = layer.skew_y = 0.0
        layer.perspective_x = layer.perspective_y = 0.0
        layer.quad = []
        layer.opacity = 1.0
        layer.effects = {}
        layer.shadow = layer.outer_glow = layer.inner_shadow = layer.bevel_emboss = False
        self._mask_edit = False
        self._redraw()
        self._refresh_layers()

    def _exit_mask_edit(self):
        self._mask_edit = False
        self._refresh_layers()
        self._sync_props()

    def _toggle_mask_edit(self, layer):
        if layer.locked:
            return
        if layer is not self.active:
            board = self._active_artboard_of(layer)
            if board is not None:
                self.active_artboard = board
            self.set_active(layer)
        """进入/退出图层蒙版编辑模式：画笔/橡皮擦改为写入蒙版。"""
        if layer.mask is None:
            QMessageBox.information(self, "提示", "请先添加图层蒙版。")
            return
        if getattr(self, "_mask_edit", False):
            self._mask_edit = False
        else:
            self._mask_edit = True
            self.set_tool(Tool.BRUSH)  # 自动切到画笔，方便涂抹
            QMessageBox.information(
                self, "蒙版编辑",
                "已进入蒙版编辑：用画笔涂白显示、橡皮涂黑隐藏。\n"
                "再次在图层右键选择「退出蒙版编辑」即可。")
        self._refresh_layers()
        self._sync_props()

    def _rasterize_layer(self, layer):
        """栅格化文字/形状图层为像素图层。"""
        if layer.kind == "image":
            return
        self._push_undo("栅格化图层")
        cw, ch = self._ctx_size()
        img = self._render_layer_canvas(layer, cw, ch)
        arr = numpy_from_qimage(img)
        layer.kind = "image"
        layer.pixels = arr
        layer.w = cw
        layer.h = ch
        layer.x,layer.y = cw/2,ch/2
        layer.scale,layer.rotation = 1.,0.
        layer.stretch_x = layer.stretch_y = 1.
        layer.flip_x = layer.flip_y = False
        layer.opacity = 1.
        layer.skew_x = layer.skew_y = layer.perspective_x = layer.perspective_y = 0.
        layer.quad = []
        layer.mask = None
        layer.effects = {}
        layer.shadow = layer.outer_glow = layer.inner_shadow = layer.bevel_emboss = False
        layer.children = []
        layer.svg_source = ''
        layer.text = ""
        layer.shape = "rect"
        layer.stroke_w = 0
        layer.gradient = False
        self._redraw()
        self._refresh_layers()

    # ═══════ 旋转 ═══════

    def _rotate_canvas_angle(self):
        """按任意角度旋转画布（保持画布尺寸，边角可能裁切）。"""
        from PyQt6.QtWidgets import QInputDialog
        deg, ok = QInputDialog.getDouble(self, "旋转画布",
                                         "旋转角度（度，正=逆时针）：", 0, -360, 360, 1)
        if ok:
            self._rotate_canvas(float(deg), swap=False)

    def _rotate_active_layer_90(self, ccw=False):
        """旋转当前图片图层 90°（绕自身中心）。"""
        layer = self.active
        if not layer or layer.kind != "image" or layer.pixels is None:
            QMessageBox.information(self, "提示", "请选中一个图片图层再旋转。")
            return
        self._push_undo("旋转图层 90°")
        arr = np.rot90(layer.pixels, k=1 if ccw else 3)
        layer.pixels = arr
        layer.w, layer.h = arr.shape[1], arr.shape[0]
        layer.rotation = 0
        layer.scale = 1.0
        self._redraw(); self._refresh_layers()

    def _rotate_active_layer_180(self):
        layer = self.active
        if not layer or layer.kind != "image" or layer.pixels is None:
            QMessageBox.information(self, "提示", "请选中一个图片图层再旋转。")
            return
        self._push_undo("旋转图层 180°")
        arr = np.rot90(layer.pixels, k=2)
        layer.pixels = arr
        layer.rotation = 0
        layer.scale = 1.0
        self._redraw(); self._refresh_layers()

    def _preview_gradient(self, p0, p1, radial=False):
        """渐变拖拽预览：在编辑区画一条「线段」指示（起点/终点圆点 + 连线 + 角度），
        让用户直观看到渐变的方向与范围。画板模式下转为本地坐标。"""
        ctx_w, ctx_h = self._ctx_size()
        img = QImage(ctx_w, ctx_h, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.active_artboard is not None:
            sx, sy = self._ab_screen(self.active_artboard)
            ox, oy = sx, sy
            a = QPointF(p0.x() - ox, p0.y() - oy)
            b = QPointF(p1.x() - ox, p1.y() - oy)
            self.view.sel_item.setOffset(ox, oy)
        else:
            a = p0; b = p1
            self.view.sel_item.setOffset(0, 0)
        cf = self.shape_grad_from
        ct = self.shape_grad_to
        # 连线（青色实线：起点 → 终点）
        pen = QPen(QColor("#00eaff"), 2)
        p.setPen(pen)
        p.drawLine(a, b)
        # 起点 / 终点圆点（填充对应渐变色，便于辨认起止）
        p.setPen(QPen(QColor("#ffffff"), 1))
        p.setBrush(QBrush(cf))
        p.drawEllipse(a, 6, 6)
        p.setBrush(QBrush(ct))
        p.drawEllipse(b, 6, 6)
        # 角度 / 模式标注（便于精确摆放）
        ang = math.degrees(math.atan2(b.y() - a.y(), b.x() - a.x()))
        label = "径向" if radial else "{}°".format(int(round(ang)))
        f = QFont("Microsoft YaHei", 10)
        p.setFont(f)
        metrics = QFontMetrics(f)
        tw = metrics.horizontalAdvance(label) + 8
        mx = (a.x() + b.x()) / 2
        my = (a.y() + b.y()) / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(0, 0, 0, 160)))
        p.drawRect(int(mx - tw / 2), int(my - 8), tw, 16)
        p.setPen(QColor("#ffffff"))
        p.drawText(int(mx - tw / 2) + 4, int(my + 4), label)
        p.end()
        self.view.sel_item.setPixmap(QPixmap.fromImage(img))

    def _create_gradient_layer(self, p0, p1, radial=False):
        """根据拖拽线段生成渐变填充图层（线性默认；Shift=径向）。"""
        cw, ch = self._ctx_size()
        cf = self.shape_grad_from
        ct = self.shape_grad_to
        f = np.array([cf.red(), cf.green(), cf.blue()], np.float32)
        t = np.array([ct.red(), ct.green(), ct.blue()], np.float32)
        ys, xs = np.mgrid[0:ch, 0:cw]
        if radial:
            cx, cy = p0.x(), p0.y()
            r = max(1.0, float(((p1.x() - p0.x()) ** 2 + (p1.y() - p0.y()) ** 2) ** 0.5))
            d = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2) / r
        else:
            dx = p1.x() - p0.x(); dy = p1.y() - p0.y()
            L = max(1.0, float((dx * dx + dy * dy) ** 0.5))
            ux, uy = dx / L, dy / L
            d = ((xs - p0.x()) * ux + (ys - p0.y()) * uy) / L
        d = np.clip(d, 0, 1)
        rgb = (f[None, None, :] * (1 - d[..., None]) + t[None, None, :] * d[..., None]).astype(np.uint8)
        arr = np.dstack([rgb, np.full((ch, cw), 255, np.uint8)])
        layer = ImageLayer("渐变填充", pixels=arr, w=cw, h=ch, kind="image")
        layer.x = cw / 2.0; layer.y = ch / 2.0; layer.scale = 1.0
        self._push_undo("渐变填充")
        ab = self.active_artboard
        if self.project.artboards and ab is None:
            ab = self.project.artboards[0]; self.active_artboard = ab
        if ab is not None:
            ab.layers.append(layer)
        else:
            self.project.layers.append(layer)
        self.set_active(layer)
        self._refresh_layers(); self._redraw()



    def _merge_visible(self):
        """合并所有可见图层（保留隐藏层）。"""
        layers = self._ctx_layers()
        visible = [ly for ly in layers if ly.visible]
        if len(visible) < 2:
            QMessageBox.information(self, "提示", "至少需要两个可见图层才能合并。")
            return
        self._push_undo("合并可见图层")
        visible.sort(key=lambda ly: layers.index(ly))
        base = visible[0]
        cw, ch = self._ctx_size()
        merged = numpy_from_qimage(self._render_layer_canvas(base, cw, ch)).astype(np.float32)
        for ly in visible[1:]:
            img = self._render_layer_canvas(ly, cw, ch)
            arr = numpy_from_qimage(img).astype(np.float32)
            alpha = arr[:, :, 3:4] / 255.0
            merged = merged * (1 - alpha) + arr * alpha
        merged = merged.astype(np.uint8)
        base.kind = "image"
        base.pixels = merged
        base.w = cw; base.h = ch
        base.x = cw / 2.0; base.y = ch / 2.0
        base.scale = 1.0; base.rotation = 0.0
        base.clip = False; base.shadow = False; base.gradient = False
        base.text = ""; base.shape = "rect"
        base.rect = QRectF(0, 0, base.w, base.h)
        base.fill_color = "#3d8ef8"; base.stroke_w = 0; base.filled = True
        for ly in reversed(visible[1:]):
            layers.remove(ly)
        self.set_active(base)
        self.selected = [base]
        self._anchor = None
        self._refresh_layers()
        self._redraw()

    def _ai_upscale(self, factor=2):
        """AI 高清放大：Real-ESRGAN x4（ONNX）优先，缺模型自动降级 Lanczos+USM。
        像素尺寸变为 factor 倍，scale 相应缩小以保持视觉大小不变（清晰度提升）。异步执行。
        超分模型由 self._upscale_model_pref 决定（auto/general/anime）。"""
        layer = self.active
        if not layer or layer.kind != "image" or layer.pixels is None:
            QMessageBox.information(self, "提示", "请选中一个图片图层再放大。")
            return
        h, w = layer.pixels.shape[:2]
        if w * factor > 12000 or h * factor > 12000:
            QMessageBox.warning(self, "尺寸过大",
                f"放大后将达到 {w*factor}×{h*factor}，超过 12000px 上限，请先缩小图片。")
            return
        if self._ai_enh_busy:
            QMessageBox.information(self, "请稍候", "上一个 AI 任务仍在进行中…")
            return
        self._ai_enh_pre_snap = self._snapshot()
        self._ai_enh_busy = True
        self._ai_enh_layer = layer
        self._ai_enh_kind = "upscale"
        self._ai_enh_factor = factor
        self._ai_enh_src_w = w
        self._begin_ai_compare(f"AI 高清放大 {factor}×")
        self.ai_status.setText("🤖 AI 超分准备中…")
        self.ai_status.setVisible(True)
        QApplication.processEvents()
        self._ai_enh_worker = _AIEnhanceWorker(
            layer.pixels.copy(), "upscale", factor,
            sr_model=getattr(self, "_upscale_model_pref", "auto"))
        self._ai_enh_worker.progress.connect(lambda s: self.ai_status.setText("🤖 " + s))
        self._ai_enh_worker.finished.connect(self._on_ai_enh_done)
        self._ai_enh_worker.error.connect(self._on_ai_enh_err)
        self._ai_enh_worker.finished.connect(self._ai_enh_worker.deleteLater)
        self._ai_enh_worker.error.connect(self._ai_enh_worker.deleteLater)
        self._ai_enh_worker.start()

    def _set_upscale_model(self, val):
        """设置超分模型偏好并刷新子菜单勾选态。"""
        self._upscale_model_pref = val
        for v, act in getattr(self, "_sr_actions", {}).items():
            act.setChecked(v == val)
        names = {"auto": "自动（按内容）", "general": "通用·写实",
                 "anime": "插画·锐利"}
        self.ai_status.setVisible(True)
        self.ai_status.setText("🔧 超分模型：" + names.get(val, val))
        QTimer.singleShot(2500, lambda: self.ai_status.setVisible(False))

    def _ai_face_restore(self):
        """AI 人脸修复：YuNet 检测人脸 → GFPGAN(若本地有模型) 或经典磨皮+锐化。异步执行。"""
        layer = self.active
        if not layer or layer.kind != "image" or layer.pixels is None:
            QMessageBox.information(self, "提示", "请选中一个图片图层再修复。")
            return
        if self._ai_enh_busy:
            QMessageBox.information(self, "请稍候", "上一个 AI 任务仍在进行中…")
            return
        self._ai_enh_pre_snap = self._snapshot()
        self._ai_enh_busy = True
        self._ai_enh_layer = layer
        self._ai_enh_kind = "face"
        self._ai_enh_sel = self.selection.copy() if self.selection is not None else None
        self._begin_ai_compare("AI 人脸修复")
        self.ai_status.setText("🤖 AI 人脸修复准备中…")
        self.ai_status.setVisible(True)
        QApplication.processEvents()
        self._ai_enh_worker = _AIEnhanceWorker(layer.pixels.copy(), "face")
        self._ai_enh_worker.progress.connect(lambda s: self.ai_status.setText("🤖 " + s))
        self._ai_enh_worker.finished.connect(self._on_ai_enh_done)
        self._ai_enh_worker.error.connect(self._on_ai_enh_err)
        self._ai_enh_worker.finished.connect(self._ai_enh_worker.deleteLater)
        self._ai_enh_worker.error.connect(self._ai_enh_worker.deleteLater)
        self._ai_enh_worker.start()

    def _ai_remove_bg_birefnet(self):
        """AI 智能抠图：BiRefNet（SOTA 通用去背，效果优于 u2netp）。异步执行。"""
        layer = self.active
        if not layer or layer.kind != "image" or layer.pixels is None:
            QMessageBox.information(self, "提示", "请选中一个图片图层再抠图。")
            return
        if self._ai_enh_busy:
            QMessageBox.information(self, "请稍候", "上一个 AI 任务仍在进行中…")
            return
        self._ai_enh_pre_snap = self._snapshot()
        self._ai_enh_busy = True
        self._ai_enh_layer = layer
        self._ai_enh_kind = "birefnet"
        self._ai_enh_sel = None
        self._begin_ai_compare("AI 智能抠图")
        self.ai_status.setText("🤖 AI 智能抠图准备中…")
        self.ai_status.setVisible(True)
        QApplication.processEvents()
        self._ai_enh_worker = _AIEnhanceWorker(layer.pixels.copy(), "birefnet")
        self._ai_enh_worker.progress.connect(lambda s: self.ai_status.setText("🤖 " + s))
        self._ai_enh_worker.finished.connect(self._on_ai_enh_done)
        self._ai_enh_worker.error.connect(self._on_ai_enh_err)
        self._ai_enh_worker.finished.connect(self._ai_enh_worker.deleteLater)
        self._ai_enh_worker.error.connect(self._ai_enh_worker.deleteLater)
        self._ai_enh_worker.start()

    def _on_ai_enh_done(self, out_arr, engine):
        self._ai_enh_busy = False
        layer = getattr(self, "_ai_enh_layer", None)
        kind = getattr(self, "_ai_enh_kind", "")
        if layer is None or out_arr is None or layer not in self._all_layers():
            self._ai_enh_pre_snap = None
            self._cancel_ai_compare()
            self.ai_status.setText("⚠ AI 处理完成，但原图层已被删除")
            self.ai_status.setVisible(True)
            QTimer.singleShot(3000, lambda: self.ai_status.setVisible(False))
            return
        if layer is not None and out_arr is not None:
            if kind == "upscale":
                ow = getattr(self, "_ai_enh_src_w", out_arr.shape[1])
                layer.pixels = out_arr
                layer.h, layer.w = out_arr.shape[0], out_arr.shape[1]
                if out_arr.shape[1]:
                    layer.scale = layer.scale * ow / out_arr.shape[1]
                self._refresh_layers()
            elif kind == "birefnet":
                layer.pixels = out_arr  # 同尺寸，仅替换 alpha 通道
                self._refresh_layers()
            else:
                sel = getattr(self, "_ai_enh_sel", None)
                if sel is not None and sel.shape == out_arr.shape[:2]:
                    old = layer.pixels.copy()
                    layer.pixels = out_arr
                    layer.pixels[~sel] = old[~sel]
                else:
                    layer.pixels = out_arr
            self._redraw()
            pre_snap = getattr(self, "_ai_enh_pre_snap", None)
            if kind == "upscale":
                name = f"AI 高清放大 {getattr(self, '_ai_enh_factor', 2)}×"
            elif kind == "birefnet":
                name = "AI 智能抠图 (BiRefNet)"
            else:
                name = "AI 人脸修复"
            self._push_undo_snapshot(name, pre_snap or self._snapshot())
            self._history[self._history_idx]["post"] = self._snapshot()
            self._finish_ai_compare()
        self._ai_enh_pre_snap = None
        self.ai_status.setText("✅ " + engine)
        QTimer.singleShot(3500, lambda: self.ai_status.setVisible(False))
        if kind == "upscale":
            if "Lanczos" in engine or "未启用" in engine:
                QMessageBox.warning(self, "高清放大（传统模式）",
                    "⚠️ AI 超分模型未成功启用，本次为传统 Lanczos 放大（与原图差异很小）。\n\n"
                    "如需真正的 AI 超清，请打开「🤖 AI → 📦 模型管理」下载 Real-ESRGAN 模型，"
                    "或检查网络后重试。")
            else:
                QMessageBox.information(self, "完成",
                    f"高清放大完成（{engine}）。\n画布视觉大小不变——"
                    "请放大查看或导出以获得更清晰的效果。\n"
                    "点击顶栏「◑ 前后对比」可切换查看原图。")
        elif kind == "birefnet":
            QMessageBox.information(self, "完成",
                f"智能抠图完成。\n引擎：{engine}\n已生成透明通道。")
        else:
            QMessageBox.information(self, "完成", f"人脸修复完成。\n引擎：{engine}")

    def _on_ai_enh_err(self, msg):
        self._ai_enh_busy = False
        self._ai_enh_pre_snap = None
        self._cancel_ai_compare()
        self.ai_status.setText("❌ AI 任务失败")
        QTimer.singleShot(4000, lambda: self.ai_status.setVisible(False))
        QMessageBox.warning(self, "失败", "AI 处理出错：\n" + msg)

    # ───────────── 通用 AI 前后对比 ─────────────
    def _reset_ai_compare(self):
        """清除当前文档对比状态，避免新建/恢复文档后串用旧快照。"""
        self._compare_before_image = None
        self._compare_pending_image = None
        self._compare_pending_label = ""
        self._compare_label = "AI 处理"
        self._compare_active = False
        button = getattr(self, "_compare_btn", None)
        if button is not None:
            button.blockSignals(True)
            button.setChecked(False)
            button.setEnabled(False)
            button.blockSignals(False)

    def _begin_ai_compare(self, label="AI 处理"):
        """暂存 AI 操作前的整张画布；任务成功后才成为有效对比。"""
        try:
            self._compare_pending_image = self._render_composite(for_export=False).copy()
            self._compare_pending_label = label
        except Exception:
            self._compare_pending_image = None
            self._compare_pending_label = ""
        self._compare_active = False
        if self._compare_btn is not None:
            self._compare_btn.blockSignals(True)
            self._compare_btn.setChecked(False)
            self._compare_btn.blockSignals(False)
            self._compare_btn.setEnabled(False)

    def _finish_ai_compare(self):
        """AI 操作成功：启用本次处理前后的对比。"""
        if self._compare_pending_image is not None:
            self._compare_before_image = self._compare_pending_image
            self._compare_label = self._compare_pending_label or "AI 处理"
        self._compare_pending_image = None
        self._compare_pending_label = ""
        self._compare_active = False
        if self._compare_btn is not None:
            self._compare_btn.blockSignals(True)
            self._compare_btn.setChecked(False)
            self._compare_btn.blockSignals(False)
            self._compare_btn.setEnabled(self._compare_before_image is not None)

    def _cancel_ai_compare(self):
        """AI 操作失败：丢弃本次暂存，保留上一次成功结果。"""
        self._compare_pending_image = None
        self._compare_pending_label = ""
        if self._compare_btn is not None:
            self._compare_btn.setEnabled(self._compare_before_image is not None)

    def _on_compare_toggle(self, checked):
        """开启显示最近一次 AI 操作前快照，关闭显示当前结果。"""
        self._compare_active = checked
        if checked and self._compare_before_image is None:
            self.ai_status.setText("⚠ 暂无可对比内容，请先完成一次 AI 修改")
            self.ai_status.setVisible(True)
            QTimer.singleShot(2800, lambda: self.ai_status.setVisible(False))
            self._compare_active = False
            if self._compare_btn is not None:
                self._compare_btn.setChecked(False)
            return
        if checked:
            self.ai_status.setText(
                f"👁 显示：{self._compare_label}前 — 再次点击切回当前结果")
        else:
            self.ai_status.setText(f"✨ 显示：{self._compare_label}后")
        self.ai_status.setVisible(True)
        QTimer.singleShot(2200, lambda: self.ai_status.setVisible(False))
        self._redraw()

    def _draw_compare_overlay(self, img):
        """用最近一次 AI 操作前的整画布快照覆盖当前预览。"""
        before = self._compare_before_image
        if before is None:
            return
        p = QPainter(img)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.drawImage(QRectF(0, 0, img.width(), img.height()), before)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        badge = QRectF(8, 8, 150, 26)
        p.setPen(QPen(QColor("#00eaff"), 1))
        p.setBrush(QColor(0, 0, 0, 175))
        p.drawRoundedRect(badge, 5, 5)
        p.setPen(QColor("#00eaff"))
        p.setFont(QFont("Microsoft YaHei", 10))
        p.drawText(badge.adjusted(9, 0, 0, 0),
                   Qt.AlignmentFlag.AlignVCenter, "AI 处理前")
        p.end()

    def _open_model_manager(self):
        """模型管理：列出所有已注册 AI 模型，支持一键下载 / 删除。"""
        import os

        class _ModelDownloadWorker(QThread):
            progress = pyqtSignal(int, int)
            done = pyqtSignal(bool, str)

            def __init__(self, key):
                super().__init__()
                self.key = key

            def run(self):
                try:
                    p = _download_model(self.key,
                                        lambda g, t: self.progress.emit(g, t))
                    self.done.emit(bool(p), self.key)
                except Exception as e:
                    self.done.emit(False, "{}: {}".format(type(e).__name__, e))

        def human(n):
            for unit in ("B", "KB", "MB", "GB"):
                if n < 1024:
                    return "{:.1f} {}".format(n, unit)
                n /= 1024.0
            return "{:.1f} TB".format(n)

        dlg = QDialog(self)
        dlg.setWindowTitle("AI 模型管理")
        dlg.setMinimumSize(640, 380)
        dlg.setStyleSheet(
            "QDialog{background:#1e1e22;} QLabel{color:#ccc;font-size:12px;} "
            "QPushButton{background:#2a2a2e;color:#ccc;border:1px solid #3a3a3e;"
            "border-radius:3px;padding:3px 10px;} "
            "QPushButton:hover{background:#3d8ef8;color:#fff;} "
            "QPushButton:disabled{color:#777;border-color:#2a2a2e;} "
            "QTableWidget{background:#1e1e22;color:#ccc;gridline-color:#333;} "
            "QHeaderView::section{background:#252528;color:#aaa;padding:4px;} "
            "QTableWidget::item{color:#ccc;}")
        vl = QVBoxLayout(dlg)
        tip = QLabel("已注册的开源 AI 模型（自动下载到 ~/.cep_models/）。"
                     "下载按钮灰色表示有任务进行中。")
        vl.addWidget(tip)
        keys = list(_AI_MODELS.keys())
        table = QTableWidget(len(keys), 4, dlg)
        table.setHorizontalHeaderLabels(["模型", "说明", "大小 / 状态", "操作"])
        table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        vl.addWidget(table)
        prog = QLabel("")
        vl.addWidget(prog)

        busy = {"active": False}
        rows = {}
        worker = [None]

        def alive(obj):
            return obj is not None and not sip.isdeleted(obj)

        def refresh():
            for r, key in enumerate(keys):
                spec = _AI_MODELS[key]
                path = _model_local_path(key)
                exists = _model_exists(key)
                table.item(r, 0).setText(key)
                table.item(r, 1).setText(spec.get("desc", spec.get("file", "")))
                if exists:
                    table.item(r, 2).setText(human(os.path.getsize(path)) + "  ✅")
                else:
                    table.item(r, 2).setText("未下载")
                dl_btn, del_btn = rows[key]
                dl_btn.setEnabled(not busy["active"])
                del_btn.setEnabled(exists and not busy["active"])

        def on_done(ok, key):
            if not alive(prog):
                return
            busy["active"] = False
            prog.setText(("✅ {} 下载完成".format(key) if ok
                          else "❌ {} 下载失败（检查网络或手动放置）".format(key)))
            refresh()

        def start_download(key):
            if busy["active"]:
                return
            busy["active"] = True
            prog.setText("下载 {} 中…".format(key))
            refresh()
            w = _ModelDownloadWorker(key)
            w.progress.connect(
                lambda g, t: alive(prog) and prog.setText(
                    "下载 {} {}%".format(key, g * 100 // t)))
            w.done.connect(on_done)
            w.finished.connect(w.deleteLater)
            w.start()
            worker[0] = w

        def do_delete(key):
            path = _model_local_path(key)
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                    prog.setText("已删除 {}".format(key))
                except Exception as e:
                    prog.setText("删除失败：{}".format(e))
            refresh()

        for r, key in enumerate(keys):
            spec = _AI_MODELS[key]
            table.setItem(r, 0, QTableWidgetItem(key))
            table.setItem(r, 1, QTableWidgetItem(spec.get("desc", spec.get("file", ""))))
            table.setItem(r, 2, QTableWidgetItem("…"))
            opw = QWidget()
            opl = QHBoxLayout(opw)
            opl.setContentsMargins(2, 2, 2, 2)
            opl.setSpacing(4)
            dl_btn = QPushButton("下载")
            del_btn = QPushButton("删除")
            dl_btn.clicked.connect(lambda _=False, k=key: start_download(k))
            del_btn.clicked.connect(lambda _=False, k=key: do_delete(k))
            opl.addWidget(dl_btn)
            opl.addWidget(del_btn)
            table.setCellWidget(r, 3, opw)
            rows[key] = (dl_btn, del_btn)

        refresh()
        dlg.exec()

    def _ai_remove_bg(self):
        layer = self.active
        if not layer or layer.kind != "image" or layer.pixels is None:
            QMessageBox.information(self, "提示", "请选中一个图片图层再抠图。")
            return
        if not self._ensure_rembg():
            return
        if getattr(self, "_ai_busy", False):
            QMessageBox.information(self, "请稍候", "上一次 AI 抠图仍在处理中…")
            return
        # 暂存 pre-state；仅在任务成功并写回图层后登记历史。
        self._ai_remove_bg_pre_snap = self._snapshot()
        self._begin_ai_compare("AI 抠图")
        self._ai_busy = True
        self._ai_layer_ref = layer
        # 记录当前选区（抠图结果只应用到选区内）
        self._ai_selection = self.selection.copy() if self.selection is not None else None
        import os as _os
        _model_ready = _os.path.exists(_os.path.expanduser("~/.u2net/u2netp.onnx"))
        if _model_ready:
            self.ai_status.setText("🤖 AI 抠图中…（轻量模型已就绪，请稍候）")
        else:
            self.ai_status.setText("🤖 AI 抠图中…（首次需下载约 4MB 轻量模型，请稍候）")
        self.ai_status.setVisible(True)
        QApplication.processEvents()
        self._ai_worker = _RembgWorker(
            layer.pixels.copy(),
            use_subprocess=self._rembg_subproc,
            python_exe=self._rembg_python)
        if self._rembg_subproc:
            self.ai_status.setText("🤖 AI 抠图中…（子进程模式，请稍候）")
        self._ai_worker.finished.connect(self._on_ai_done)
        self._ai_worker.error.connect(self._on_ai_err)
        self._ai_worker.finished.connect(self._ai_worker.deleteLater)
        self._ai_worker.error.connect(self._ai_worker.deleteLater)
        self._ai_worker.start()

    def _on_ai_done(self, out_arr):
        self._ai_busy = False
        layer = getattr(self, "_ai_layer_ref", None)
        if layer is not None and out_arr is not None and layer in self._all_layers():
            sel_mask = getattr(self, "_ai_selection", None)
            if sel_mask is not None:
                # 只更新选区内的像素
                old = layer.pixels.copy()
                layer.pixels = out_arr
                layer.pixels[~sel_mask] = old[~sel_mask]
            else:
                layer.pixels = out_arr
            self._redraw()
            pre_snap = getattr(self, "_ai_remove_bg_pre_snap", None)
            if pre_snap is not None:
                self._push_undo_snapshot("AI 抠图", pre_snap)
                self._history[self._history_idx]["post"] = self._snapshot()
            self._finish_ai_compare()
        else:
            self._cancel_ai_compare()
            self.ai_status.setText("⚠ AI 抠图完成，但原图层已被删除")
            self._ai_remove_bg_pre_snap = None
            return
        self._ai_remove_bg_pre_snap = None
        self.ai_status.setText("✅ AI 抠图完成，透明通道已更新")
        QTimer.singleShot(2500, lambda: self.ai_status.setVisible(False))
        QMessageBox.information(
            self, "完成",
            "已用 AI 抠除背景，透明通道已更新\n（可继续用「填充/删除选区」或导出 PNG 保留透明）。")

    def _on_ai_err(self, msg):
        self._ai_busy = False
        self._ai_remove_bg_pre_snap = None
        self._cancel_ai_compare()
        self.ai_status.setText("❌ AI 抠图失败")
        QTimer.singleShot(4000, lambda: self.ai_status.setVisible(False))
        QMessageBox.warning(self, "失败", "AI 抠图出错：\n" + msg)

    def _ensure_rembg(self):
        """确保 rembg + onnxruntime 可用；源码模式下可自动 pip 安装。

        若当前进程无法 import 但子进程可以，则启用子进程模式（_rembg_subproc=True），
        不再要求用户重启应用——AI 抠图会在独立子进程里完成。
        """
        import sys
        import subprocess
        import importlib

        def _try_import():
            try:
                importlib.import_module("onnxruntime")
                importlib.import_module("rembg")
                return True, ""
            except BaseException as e:
                return False, "{}: {}".format(type(e).__name__, e)

        def _probe_subprocess(py_exe):
            try:
                p = subprocess.run(
                    [py_exe, "-c", "import onnxruntime, rembg; print('REMBG_OK')"],
                    capture_output=True, text=True, timeout=30)
                return p.returncode == 0 and "REMBG_OK" in (p.stdout or "")
            except Exception:
                return False

        # 已缓存结果：成功/失败都直接返回，避免每次重复探测、弹窗
        if self._rembg_ensured is True:
            return True
        if self._rembg_ensured is False:
            return False

        ok, err0 = _try_import()
        if ok:
            self._rembg_subproc = False
            self._rembg_python = None
            self._rembg_ensured = True
            return True

        # 当前进程导入失败（EXE 下多为 onnxruntime DLL 冲突）：先静默探测子进程。
        # 子进程能跑就切到子进程模式，不再弹窗。
        py_exe = sys.executable
        if _probe_subprocess(py_exe):
            self._rembg_subproc = True
            self._rembg_python = py_exe
            self._rembg_ensured = True
            return True

        # 子进程也失败： frozen 模式直接提示；源码模式再问是否 pip 安装。
        if getattr(sys, "frozen", False):
            self._rembg_ensured = False
            QMessageBox.warning(
                self, "缺少 AI 抠图后端",
                "当前程序包未包含可用的 onnxruntime / rembg 推理后端，\n"
                "AI 抠图无法使用。请在源码环境下运行本程序，\n"
                "或重新打包已内置该后端的版本。")
            return False

        res = QMessageBox.question(
            self, "安装依赖",
            "未检测到 AI 抠图依赖(onnxruntime + rembg)。是否现在联网安装？\n"
            "（轻量模型若已预置则无需下载；否则首次会下载约 4MB 模型）\n\n"
            "当前导入错误：{}".format(err0),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

        if res == QMessageBox.StandardButton.Yes:
            QMessageBox.information(self, "安装中", "正在为当前 Python 安装 onnxruntime 与 rembg，请稍候…")

            def _pip_install(pkgs, extra=None):
                cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
                if extra:
                    cmd += extra
                cmd += pkgs
                return subprocess.run(cmd, capture_output=True, text=True)

            r = _pip_install(["rembg", "onnxruntime"])
            if r.returncode != 0:
                r2 = _pip_install(["rembg", "onnxruntime-cpu"])
                if r2.returncode == 0:
                    r = r2

            # 安装后先尝试当前进程直接导入
            for _ in range(2):
                ok, _ = _try_import()
                if ok:
                    self._rembg_subproc = False
                    self._rembg_python = None
                    self._rembg_ensured = True
                    return True

            # 当前进程仍不行但子进程可以时（Windows Store 等场景），切子进程
            if _probe_subprocess(py_exe):
                self._rembg_subproc = True
                self._rembg_python = py_exe
                self._rembg_ensured = True
                return True

        # 最终失败
        self._rembg_ensured = False
        pip_out = ""
        if res == QMessageBox.StandardButton.Yes:
            pip_out = (getattr(r, "stdout", "") or "") + "\n" + (getattr(r, "stderr", "") or "")
        pip_lines = [ln for ln in pip_out.splitlines() if ln.strip() and not ln.startswith("WARNING: ")]
        pip_snippet = "\n".join(pip_lines[-15:]) or "pip 无有效输出"

        hint = ""
        if "No matching distribution found" in pip_out or "Could not find a version" in pip_out:
            hint = (
                "\n\n可能原因：当前 Python {} 暂无官方 onnxruntime 预编译包。\n"
                "建议：将 Python 降级到 3.12/3.11，或在项目 venv 中手动安装兼容版本。"
            ).format(sys.version.split()[0])
        else:
            hint = (
                "\n\n依赖已安装但仍无法导入，常见原因：\n"
                "1. 当前运行 Python 与 pip 安装目标不一致；\n"
                "2. onnxruntime 原生 DLL 加载失败（缺少 VC++ 运行库 / CPU 不支持 AVX / 包损坏）；\n"
                "3. 多版本 Python 混用。可在命令行执行：\n"
                "   {} -m pip install --force-reinstall rembg onnxruntime\n"
                "然后重启应用。"
            ).format(sys.executable)

        QMessageBox.warning(
            self, "依赖安装失败",
            "AI 抠图依赖安装后仍无法导入。\n\n"
            "当前进程导入错误：{}\n\n"
            "pip 输出：\n{}\n{}".format(err0 or "未知错误", pip_snippet, hint).strip())
        return False
    # ═══════ 撤销 / 重做（pre-state 快照） ═══════
    def _capture_document_snapshot(self):
        return dict(
            w=self.project.w, h=self.project.h,
            bg=(self.project.bg_color.red(), self.project.bg_color.green(),
                self.project.bg_color.blue()),
            transparent=self.project.transparent,
            batch_template=copy.deepcopy(getattr(self, '_batch_template', None)),
            h_guides=list(self.project.h_guides),
            v_guides=list(self.project.v_guides),
            show_grid=self.project.show_grid,
            grid_size=self.project.grid_size,
            grid_subdivisions=self.project.grid_subdivisions,
            snap_targets=dict(self.project.snap_targets),
            active_id=self.active.id if self.active else None,
            selected_ids=[layer.id for layer in self.selected],
            selection=self._snapshot_array(self.selection),
            sel_alpha=self._snapshot_array(self.sel_alpha),
            sel_base=self._snapshot_array(self._sel_base),
            feather=self.feather,
            active_artboard_id=self.active_artboard.id if self.active_artboard else None,
            artboards=[dict(
                id=ab.id, name=ab.name, x=ab.x, y=ab.y, w=ab.w, h=ab.h,
                transparent=ab.transparent, collapsed=ab.collapsed,
                bg=(ab.bg_color.red(), ab.bg_color.green(), ab.bg_color.blue()),
                layers=[self._ser_layer(l, with_pixels=True) for l in ab.layers],
            ) for ab in self.project.artboards],
            layers=[self._ser_layer(l, with_pixels=True) for l in self.project.layers],
        )

    def _ser_layer(self, l, with_pixels=False, asset_base=None):
        o = dict(id=l.id, name=l.name, kind=l.kind, opacity=l.opacity,
                 visible=l.visible, blend=l.blend, x=l.x, y=l.y, scale=l.scale,
                 rotation=l.rotation, w=l.w, h=l.h,
                 clip=l.clip, radius=l.radius, gradient=l.gradient,
                 grad_from=l.grad_from, grad_to=l.grad_to, grad_angle=l.grad_angle,
                 shadow=l.shadow, shadow_dx=l.shadow_dx, shadow_dy=l.shadow_dy,
                 shadow_blur=l.shadow_blur, shadow_color=l.shadow_color,
                 shadow_opacity=l.shadow_opacity, stroke_color=l.stroke_color,
                 text=l.text, font_family=l.font_family, font_size=l.font_size,
                 bold=l.bold, italic=l.italic, color=l.color, align=l.align,
                 shape=l.shape, rect=[l.rect.x(), l.rect.y(), l.rect.width(), l.rect.height()],
                 fill_color=l.fill_color, stroke_w=l.stroke_w, stroke_on=l.stroke_on,
                 filled=l.filled, locked=getattr(l, 'locked', False),
                 group_id=getattr(l, 'group_id', None),
                 inner_shadow=getattr(l, 'inner_shadow', False),
                 inner_shadow_dx=getattr(l, 'inner_shadow_dx', 0),
                 inner_shadow_dy=getattr(l, 'inner_shadow_dy', 0),
                 inner_shadow_blur=getattr(l, 'inner_shadow_blur', 4),
                 inner_shadow_color=getattr(l, 'inner_shadow_color', '#000000'),
                 inner_shadow_opacity=getattr(l, 'inner_shadow_opacity', 0.4),
                 outer_glow=getattr(l, 'outer_glow', False),
                 outer_glow_size=getattr(l, 'outer_glow_size', 8),
                 outer_glow_color=getattr(l, 'outer_glow_color', '#00eaff'),
                 outer_glow_opacity=getattr(l, 'outer_glow_opacity', 0.5),
                 bevel_emboss=getattr(l, 'bevel_emboss', False),
                 bevel_size=getattr(l, 'bevel_size', 3),
                 bevel_highlight=getattr(l, 'bevel_highlight', '#ffffff'),
                 bevel_shadow=getattr(l, 'bevel_shadow', '#000000'),
                 bevel_depth=getattr(l, 'bevel_depth', 0.5),
                 skew_x=getattr(l, 'skew_x', 0.0), skew_y=getattr(l, 'skew_y', 0.0),
                 perspective_x=getattr(l, 'perspective_x', 0.0),
                 perspective_y=getattr(l, 'perspective_y', 0.0))
        if l.svg_source:
            o['svg_source'] = l.svg_source
        o['letter_spacing'] = l.letter_spacing
        o['line_spacing'] = l.line_spacing
        o['text_box_explicit'] = l.text_box_explicit
        o['stretch_x'],o['stretch_y'] = l.stretch_x,l.stretch_y
        o['line_start'],o['line_end'],o['line_style'],o['line_cap'] = l.line_start,l.line_end,l.line_style,l.line_cap
        o['flip_x'],o['flip_y'],o['mask_linked'] = l.flip_x,l.flip_y,l.mask_linked
        o['quad'] = copy.deepcopy(l.quad)
        if l.effects:
            o['effects'] = copy.deepcopy(l.effects)
        if l.frames and with_pixels:
            l.frames[l.frame_index] = l.pixels
            o['frames'] = [self._snapshot_array(frame) for frame in l.frames]
            o['frame_durations'] = list(l.frame_durations)
            o['frame_index'] = l.frame_index
        if l.kind == 'group':
            o['children'] = [self._ser_layer(child, with_pixels=with_pixels) for child in l.children]
            o['collapsed'] = l.collapsed
        if with_pixels and getattr(l, "mask", None) is not None:
            o["mask"] = self._snapshot_array(l.mask)
        if getattr(l, 'adjust', None):
            o["adjust"] = copy.deepcopy(l.adjust)
        if getattr(l, 'smart', False):
            o["smart"] = True
            if with_pixels and l.smart_source:
                o["smart_source"] = self._snapshot_nested(l.smart_source)
        if l.kind in ("image", "svg"):
            if with_pixels and l.pixels is not None:
                o["pixels"] = self._snapshot_array(l.pixels)
            if asset_base:
                o["asset"] = asset_base + f".assets/layer_{l.id}.png"
        return o

    def _rebuild_from_snap(self, d):
        """从快照/工程字典重建完整状态（撤销重做与打开工程共用）。"""
        self._clear_interaction_preview()
        self._move_olds = None
        self.view._handle_drag = None
        self.view._layer_marquee = None
        self._metadata_array_cache = {}
        if hasattr(self, '_history_store'):
            self._history_store.freeze_pending()
        self.stop_text_session()
        if hasattr(self, "_compare_before_image"):
            self._reset_ai_compare()
        self.project = ImageProject(d["w"], d["h"])
        self._batch_template = copy.deepcopy(d.get('batch_template'))
        self._crop_rect = None
        self._mask_edit = False
        self.view.sel_item.setPixmap(QPixmap())
        self.project.transparent = d["transparent"]
        self.project.bg_color = QColor(*d["bg"])
        self.project.h_guides = list(d.get("h_guides", []))
        self.project.v_guides = list(d.get("v_guides", []))
        self.project.show_grid = bool(d.get("show_grid", False))
        self.project.grid_size = int(d.get("grid_size", 60))
        self.project.grid_subdivisions = int(d.get('grid_subdivisions',1))
        self.project.snap_targets.update(d.get('snap_targets',{}))
        self.project.artboards = []
        ab_by_id = {}
        for a in d.get("artboards", []):
            ab = Artboard(a["name"], a["x"], a["y"], a["w"], a["h"])
            ab.id = a["id"]
            ab.transparent = a.get("transparent", False)
            ab.collapsed = a.get("collapsed", False)
            ab.bg_color = QColor(*a.get("bg", (255, 255, 255)))
            ab.layers = [self._deser_layer(o) for o in a.get("layers", [])]
            self.project.artboards.append(ab)
            ab_by_id[a["id"]] = ab
        self.project.layers = [self._deser_layer(o) for o in d.get("layers", [])]
        self.active_artboard = ab_by_id.get(d.get("active_artboard_id"))
        self.active = next((l for l in self._all_layers()
                            if l.id == d.get("active_id")), None)
        self.selected = [layer for layer in self._all_layers() if layer.id in d.get('selected_ids', [d.get('active_id')])]
        self._anchor = self.active
        self.selection = d['selection'].copy() if d.get('selection') is not None else None
        self.sel_alpha = d['sel_alpha'].copy() if d.get('sel_alpha') is not None else None
        self._sel_base = d['sel_base'].copy() if d.get('sel_base') is not None else None
        self.feather = d.get('feather', self.feather)
        if self.active_artboard is not None:
            self.cw.setValue(self.active_artboard.w)
            self.ch.setValue(self.active_artboard.h)
        else:
            self.cw.setValue(d["w"]); self.ch.setValue(d["h"])
        self.trans_chk.blockSignals(True)
        self.trans_chk.setChecked(
            self.active_artboard.transparent if self.active_artboard is not None
            else self.project.transparent)
        self.trans_chk.blockSignals(False)
        self._refresh_layers()
        self._redraw()
        self.view.fit_view()

    def _restore(self, snap):
        transform = QTransform(self.view.transform())
        center = self.view.mapToScene(self.view.viewport().rect().center())
        self._rebuild_from_snap(snap)
        self.view.setTransform(transform)
        self.view.centerOn(center)
        self._mark_recovery_dirty()

    def _deser_layer(self, o):
        l = ImageLayer(o["name"], kind=o["kind"], w=o["w"], h=o["h"])
        l.id = o["id"]; l.opacity = o["opacity"]; l.visible = o["visible"]
        ImageLayer._counter = max(ImageLayer._counter, l.id)
        if l.kind == 'group':
            l.children = [self._deser_layer(child) for child in o.get('children', [])]
            l.collapsed = bool(o.get('collapsed', True))
        l.blend = o["blend"]; l.x = o["x"]; l.y = o["y"]; l.scale = o["scale"]
        l.rotation = o["rotation"]
        l.letter_spacing = o.get('letter_spacing',0)
        l.line_spacing = o.get('line_spacing',100)
        l.text_box_explicit = bool(o.get('text_box_explicit',False))
        l.stretch_x,l.stretch_y = o.get('stretch_x',1.),o.get('stretch_y',1.)
        l.line_start,l.line_end = o.get('line_start',(0.,0.)),o.get('line_end',(1.,1.))
        l.line_style,l.line_cap = o.get('line_style',1),o.get('line_cap',16)
        l.flip_x,l.flip_y,l.mask_linked = o.get('flip_x',False),o.get('flip_y',False),o.get('mask_linked',True)
        l.quad = copy.deepcopy(o.get('quad',[]))
        if l.scale<0:
            l.scale = abs(l.scale)
            l.flip_x,l.flip_y = not l.flip_x,not l.flip_y
        l.svg_source = o.get('svg_source', '')
        l.effects = copy.deepcopy(o.get('effects', {}))
        l.frames = [frame.copy() for frame in o.get('frames', [])]
        l.frame_durations = list(o.get('frame_durations', []))
        l.frame_index = int(o.get('frame_index', 0))
        l.clip = o.get("clip", False); l.radius = o.get("radius", 0)
        l.gradient = o.get("gradient", False)
        l.grad_from = o.get("grad_from", "#3d8ef8"); l.grad_to = o.get("grad_to", "#00eaff")
        l.grad_angle = o.get("grad_angle", 0)
        l.shadow = o.get("shadow", False); l.shadow_dx = o.get("shadow_dx", 4)
        l.shadow_dy = o.get("shadow_dy", 4); l.shadow_blur = o.get("shadow_blur", 8)
        l.shadow_color = o.get("shadow_color", "#000000")
        l.shadow_opacity = o.get("shadow_opacity", 0.5)
        l.stroke_color = o.get("stroke_color", "#000000")
        l.text = o.get("text", ""); l.font_family = o.get("font_family", "Microsoft YaHei")
        l.font_size = o.get("font_size", 48); l.bold = o.get("bold", False)
        l.italic = o.get("italic", False); l.color = o.get("color", "#ffffff")
        l.align = o.get("align", int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop))
        l.shape = o.get("shape", "rect"); r = o.get("rect", [0, 0, o["w"], o["h"]])
        l.rect = QRectF(r[0], r[1], r[2], r[3])
        l.fill_color = o.get("fill_color", "#3d8ef8"); l.stroke_w = o.get("stroke_w", 0)
        l.stroke_on = o.get("stroke_on", l.stroke_w > 0)
        l.filled = o.get("filled", True)
        l.locked = o.get("locked", False)
        l.group_id = o.get("group_id")
        l.mask = o["mask"].copy() if o.get("mask") is not None else None
        for attr, default in (
                ("inner_shadow", False), ("inner_shadow_dx", 0),
                ("inner_shadow_dy", 0), ("inner_shadow_blur", 4),
                ("inner_shadow_color", "#000000"), ("inner_shadow_opacity", 0.4),
                ("outer_glow", False), ("outer_glow_size", 8),
                ("outer_glow_color", "#00eaff"), ("outer_glow_opacity", 0.5),
                ("bevel_emboss", False), ("bevel_size", 3),
                ("bevel_highlight", "#ffffff"), ("bevel_shadow", "#000000"),
                ("bevel_depth", 0.5), ("skew_x", 0.0), ("skew_y", 0.0),
                ("perspective_x", 0.0), ("perspective_y", 0.0)):
            setattr(l, attr, o.get(attr, default))
        l.smart = o.get("smart", False)
        l.smart_source = o.get("smart_source")
        if o.get("adjust"):
            l.adjust = copy.deepcopy(o["adjust"])
        if o["kind"] in ("image", "svg"):
            if "pixels" in o:
                l.pixels = o["pixels"].copy()
            elif o.get("asset"):
                l.pixels = numpy_from_qimage(QImage(o["asset"]))
        return l

    def _push_undo(self, name="操作", snap=None):
        """保存当前状态到线性历史（PS 风格）；之后可撤销。
        采用 pre-state 模型：快照记录「本次操作之前」的状态，
        因此每次撤销都精确回退一步，不会跳步（修复「直接跳到上上一步」）。"""
        self._commit_property_history()
        self._push_undo_snapshot(name, self._snapshot() if snap is None else snap)

    def _queue_property_undo(self, name):
        """合并滑杆/数值框的连续变化，同时保证最终产生历史记录。"""
        pending = self._pending_property_history
        active_id = self.active.id if self.active else None
        if pending is not None and (pending[0] != name or pending[2] != active_id):
            self._commit_property_history()
            pending = None
        if pending is None:
            self._pending_property_history = (name, self._snapshot(metadata_only=True), active_id)
            self._mark_recovery_dirty()
        self._property_history_timer.start()

    def _commit_property_history(self):
        pending = self._pending_property_history
        if pending is None:
            return
        self._property_history_timer.stop()
        self._pending_property_history = None
        self._committing_property_history = True
        try:
            self._push_undo_snapshot(pending[0], pending[1])
        finally:
            self._committing_property_history = False

    def _push_undo_snapshot(self, name, snap):
        """以给定快照入栈（供「操作后」才确定是否入栈的操作复用，保持 pre-state 一致）。"""
        if not self._committing_property_history:
            self._commit_property_history()
        self._history_idx += 1
        # 截断 redo 分支（新操作后旧 redo 不可达）
        self._history = self._history[:self._history_idx]
        self._history.append({"name": name, "snapshot": snap, "post": None})
        self._mark_recovery_dirty()
        if len(self._history) > self.max_undo:
            self._history.pop(0)
            self._history_idx -= 1
        self.trim_history_memory()
        self._refresh_history_panel()

    def undo(self):
        """撤销到上一步（pre-state 模型：历史[idx] 即本步操作前的状态）。"""
        self._commit_property_history()
        if self._history_idx <= 0:
            return
        # 保存当前实时状态，供重做（redo）恢复（pre-state 不存 after-state）
        cur = self._history[self._history_idx]
        try:
            cur["post"] = self._snapshot()
        except Exception:
            cur["post"] = None
        target = cur["snapshot"]   # 本步操作之前的状态 = 撤销后要回到的状态
        self._history_idx -= 1
        self._restore(target)
        self._refresh_history_panel()

    def redo(self):
        """重做到下一步（pre-state 模型：用撤销时保存的 after-state 恢复）。"""
        self._commit_property_history()
        if self._history_idx >= len(self._history) - 1:
            return
        next_idx = self._history_idx + 1
        snap = self._history_state_after(next_idx)
        self._history_idx = next_idx
        self._restore(snap)
        self._refresh_history_panel()

    def _history_state_after(self, idx):
        """返回历史面板第 idx 项所代表的操作后状态。"""
        if idx <= 0:
            return self._history[0]["snapshot"]
        entry = self._history[idx]
        if entry.get("post") is not None:
            return entry["post"]
        # 下一操作的 pre-state，恰好就是本操作完成后的状态。
        if idx + 1 < len(self._history):
            return self._history[idx + 1]["snapshot"]
        # 最新状态尚未固化时只有当前实时画布可作为 after-state。
        if idx == self._history_idx:
            return self._snapshot()
        return entry["snapshot"]

    def _history_jump_to(self, idx):
        """PS 风格：点击历史面板任意条目跳转到该状态。"""
        if 0 <= idx < len(self._history):
            # 保存当前实时状态，供之后重做使用
            try:
                self._history[self._history_idx]["post"] = self._snapshot()
            except Exception:
                pass
            target = self._history_state_after(idx)
            self._history_idx = idx
            self._restore(target)
            self._refresh_history_panel()


def QFontMetrics_virtual(font, text):
    # 在画布渲染里无法直接用 QFontMetrics，这里用工件估算尺寸
    img = QImage(4000, 200, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setFont(font)
    r = p.boundingRect(QRectF(0, 0, 4000, 200), Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, text)
    p.end()
    return r


# ───────────────────────────── 新建画布对话框 ─────────────────────────────
class NewCanvasDialog(QDialog):
    """新建画布对话框：命名 + 预设尺寸 / 自定义宽高。"""

    PRESETS = [
        ("方形 1080×1080", 1080, 1080),
        ("竖屏手机 1080×1920", 1080, 1920),
        ("iPhone 14 1170×2532", 1170, 2532),
        ("iPad 2048×2732", 2048, 2732),
        ("横屏 1920×1080", 1920, 1080),
        ("自定义", -1, -1),
    ]

    def __init__(self, parent=None, default_w=1080, default_h=1080):
        super().__init__(parent)
        self.setWindowTitle("新建画布")
        self.setMinimumWidth(280)
        self.setStyleSheet(
            "QDialog{background:#1e1e22;} QLabel{color:#ccc;font-size:12px;}"
            "QComboBox,QSpinBox,QLineEdit{background:#2a2a2e;color:#eee;"
            "border:1px solid #3a3a3e;border-radius:3px;padding:4px;}"
            "QPushButton{background:#252528;color:#ccc;border:1px solid #333;"
            "border-radius:3px;padding:5px 12px;}"
            "QPushButton:hover{background:#333;color:#fff;}")
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.addWidget(QLabel("画布名称"))
        self.name_edit = QLineEdit("未命名")
        lay.addWidget(self.name_edit)
        lay.addWidget(QLabel("预设尺寸"))
        self.preset = QComboBox()
        for name, w, h in self.PRESETS:
            self.preset.addItem(name)
        self.preset.setCurrentIndex(0)
        lay.addWidget(self.preset)
        hl = QHBoxLayout()
        hl.addWidget(QLabel("宽"))
        self.w_spin = QSpinBox(); self.w_spin.setRange(1, 8000); self.w_spin.setValue(default_w)
        hl.addWidget(self.w_spin, 1)
        hl.addWidget(QLabel("高"))
        self.h_spin = QSpinBox(); self.h_spin.setRange(1, 8000); self.h_spin.setValue(default_h)
        hl.addWidget(self.h_spin, 1)
        lay.addLayout(hl)
        bl = QHBoxLayout()
        ok = QPushButton("确定"); ok.setDefault(True); ok.clicked.connect(self.accept)
        cancel = QPushButton("取消"); cancel.clicked.connect(self.reject)
        # 回车确认 / Esc 取消
        ok.setShortcut("Return")
        bl.addStretch(); bl.addWidget(ok); bl.addWidget(cancel)
        lay.addLayout(bl)
        self.preset.currentIndexChanged.connect(self._on_preset)
        # 默认选「自定义」以保留传入的默认尺寸（用户改预设时再覆盖）
        self.preset.setCurrentIndex(len(self.PRESETS) - 1)

    def _on_preset(self, idx):
        name, w, h = self.PRESETS[idx]
        if w > 0 and h > 0:
            self.w_spin.setValue(w); self.h_spin.setValue(h)

    def result_size(self):
        return self.w_spin.value(), self.h_spin.value()

    def result_name(self):
        return (self.name_edit.text() or "未命名").strip()


# ───────────────────────────── 工具函数 ─────────────────────────────
def _tab_close_icon():
    """自绘一个浅灰 "✕" 图标，保证深色标签栏下清晰可见（不依赖系统主题）。
    用 QImage 绘制（而非屏幕后端 QPixmap），避免无头/首帧下图标为空。"""
    from PyQt6.QtGui import QImage, QPainter, QPen, QColor
    img = QImage(16, 16, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor('#d5dae4'))
    pen.setWidthF(2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(4, 4, 12, 12)
    p.drawLine(12, 4, 4, 12)
    p.end()
    return QIcon(QPixmap.fromImage(img))


# ───────────────────────────── Mixin 接入主窗口 ─────────────────────────────
class ImageEditorContainer(QWidget):
    """PS 风格多文档容器：每个标签页是一个独立 ImageEditorWidget（独立工程 / 图层 /
    撤销栈），互不干扰。文件 → 新建画布 与 裁剪生成新画布 都通过它开新标签。"""
    add_layer_to_media_requested = pyqtSignal(str)  # 透传子文档信号到主窗口
    storyboard_refined_ready = pyqtSignal(str, str, bool)  # shot_id, png, 立即图生视频

    def __init__(self):
        super().__init__()
        self._init_ui()
        # 默认开一个空白文档
        self.new_document(1080, 1080, name="未命名")

    def _init_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._storyboard_bar = QWidget()
        self._storyboard_bar.setFixedHeight(38)
        self._storyboard_bar.setStyleSheet(
            "background:#211b31;border-bottom:1px solid #4b3b6d;"
        )
        sbl = QHBoxLayout(self._storyboard_bar)
        sbl.setContentsMargins(10, 4, 10, 4)
        self._storyboard_label = QLabel("分镜图片精修")
        self._storyboard_label.setStyleSheet("color:#c7b6f5;font-weight:bold;")
        sbl.addWidget(self._storyboard_label)
        sbl.addStretch()
        btn_return = QPushButton("保存并返回分镜")
        btn_return.setStyleSheet(
            "QPushButton{background:#345b49;color:#b8efd4;border:1px solid #4b8068;"
            "border-radius:4px;padding:4px 12px;}QPushButton:hover{background:#416f5a;}"
        )
        btn_return.clicked.connect(lambda: self._finish_storyboard_refine(False))
        sbl.addWidget(btn_return)
        btn_video = QPushButton("保存并以此图生成视频")
        btn_video.setStyleSheet(
            "QPushButton{background:#28517a;color:#d2e8ff;border:1px solid #3f76a9;"
            "border-radius:4px;padding:4px 12px;}QPushButton:hover{background:#336796;}"
        )
        btn_video.clicked.connect(lambda: self._finish_storyboard_refine(True))
        sbl.addWidget(btn_video)
        btn_cancel = QPushButton("退出精修模式")
        btn_cancel.clicked.connect(self._close_storyboard_refine)
        btn_cancel.setStyleSheet(
            "QPushButton{background:transparent;color:#888;border:1px solid #45404f;"
            "border-radius:4px;padding:4px 10px;}QPushButton:hover{color:#ccc;}"
        )
        sbl.addWidget(btn_cancel)
        self._storyboard_bar.hide()
        self._storyboard_shot_id = ""
        outer.addWidget(self._storyboard_bar)

        self.tab = QTabWidget()
        self.tab.setMovable(True)
        self.tab.setDocumentMode(False)
        self.tab.setStyleSheet(
            "QTabWidget::pane{background:#1b1b1e;border:1px solid #2c2c2c;border-top:none;}"
            "QTabBar::tab{background:#252528;color:#888;padding:5px 14px;font-size:12px;"
            "border:1px solid #2c2c2c;border-bottom:none;margin-right:2px;}"
            "QTabBar::tab:selected{background:#1b1b1e;color:#fff;}"
            "QTabBar::tab:hover{color:#ccc;}"
        )
        self.tab.tabBar().tabBarDoubleClicked.connect(self._on_tab_rename)
        outer.addWidget(self.tab, 1)

    def new_document(self, w=1080, h=1080, name=None):
        """新建独立文档标签并返回该 ImageEditorWidget。"""
        doc = ImageEditorWidget()
        doc.host = self
        name = name or "未命名"
        doc.doc_name = name
        idx = self.tab.addTab(doc, name)
        self.tab.setCurrentIndex(idx)
        tb = self.tab.tabBar()
        close = QToolButton(tb)
        close.setIcon(_tab_close_icon())
        close.setIconSize(QSize(13, 13))
        close.setFixedSize(19, 19)
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.setToolTip("关闭画布")
        close.setAccessibleName("关闭画布")
        close.setStyleSheet(
            "QToolButton{background:transparent;border:0;border-radius:4px;padding:0;}"
            "QToolButton:hover{background:#434958;}"
        )
        close.clicked.connect(lambda: self._on_close_tab(self.tab.indexOf(doc)))
        tb.setTabButton(idx, QTabBar.ButtonPosition.RightSide, close)
        # 子文档信号透传到主窗口
        doc.add_layer_to_media_requested.connect(
            lambda p: self.add_layer_to_media_requested.emit(p))
        if w and h:
            doc.new_project(w, h)
        return doc

    def current_widget(self):
        return self.tab.currentWidget()

    def add_image_from_path(self, path):
        """向当前激活文档导入图片（供外部如视频工作台调用）。"""
        w = self.current_widget()
        if w is not None:
            w.add_image_from_path(path)

    def open_ai_generation(self, prompt: str, aspect: str = "1:1"):
        """旧入口兼容：完整生图已迁移到 AI 制片画布。"""
        QMessageBox.information(
            self, "AI 生图已迁移",
            "请在 AI 制片画布中使用“多图生成图片”节点。\n"
            "生成后右键图片选择“发送到图片工作台”进行精修。")
        return None

    def open_storyboard_refine(self, path: str, shot_id: str):
        """从分镜进入可选PS精修；顶部提供明确的返回与图生视频动作。"""
        image = QImage(path)
        if image.isNull():
            return False
        doc = self.new_document(image.width(), image.height(), name="分镜精修")
        doc.add_image_from_path(path)
        self._storyboard_shot_id = shot_id
        self._storyboard_label.setText(f"分镜图片精修 · {shot_id}")
        self._storyboard_bar.show()
        return True

    def _finish_storyboard_refine(self, make_video: bool):
        doc = self.current_widget()
        shot_id = self._storyboard_shot_id
        if doc is None or not shot_id:
            return
        try:
            from config import OUTPUT_DIR
            folder = os.path.join(str(OUTPUT_DIR), "ai_images")
        except Exception:
            folder = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                  "work_temp", "ai_assets")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"storyboard_refined_{int(time.time() * 1000)}.png")
        image = doc._render_composite(for_export=True)
        if image.isNull() or not image.save(path, "PNG"):
            QMessageBox.warning(self, "保存失败", "无法导出当前精修画布。")
            return
        self.storyboard_refined_ready.emit(shot_id, path, make_video)
        self._close_storyboard_refine()

    def _close_storyboard_refine(self):
        self._storyboard_shot_id = ""
        self._storyboard_bar.hide()

    def _on_close_tab(self, idx):
        """关闭文档标签；至少保留一个（最后一张改为清空而非销毁）。"""
        if idx < 0 or idx >= self.tab.count():
            return
        w = self.tab.widget(idx)
        decision = w.close_project_decision()
        if decision is None or (decision and not w.discard_recovery()):
            return
        if self.tab.count() <= 1:
            w.new_project(1080, 1080)
            self.set_doc_name_of(w, "未命名")
            return
        self.tab.removeTab(idx)
        w.deleteLater()

    def prepare_close(self):
        discards = []
        for index in range(self.tab.count()):
            doc = self.tab.widget(index)
            decision = doc.close_project_decision()
            if decision is None:
                return False
            if decision:
                discards.append(doc)
        return all(doc.discard_recovery() for doc in discards)

    def _on_tab_rename(self, idx):
        """双击标签重命名画布。"""
        w = self.tab.widget(idx)
        if w is None:
            return
        cur = getattr(w, "doc_name", "未命名") or "未命名"
        text, ok = QInputDialog.getText(self, "重命名画布", "画布名称：", text=cur)
        if ok and text.strip():
            self.set_doc_name_of(w, text.strip())

    def set_doc_name_of(self, widget, name):
        widget.doc_name = name or "未命名"
        idx = self.tab.indexOf(widget)
        if idx >= 0:
            self.tab.setTabText(idx, widget.doc_name)


# ───────────────────────────── Mixin 接入主窗口 ─────────────────────────────
class ImageEditorHandler:
    """图片图层编辑器（Tab）混入"""

    def build_image_editor_module(self):
        w = ImageEditorContainer()
        self.image_editor = w
        return w
