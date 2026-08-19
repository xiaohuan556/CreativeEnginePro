"""
AI 图片生成面板 —— 注入到图片编辑器右侧第 4 个分栏。

按参考设计实现：
- 引擎选择（Seedream 5.0 Pro / GPT-Image-2）
- Prompt + 负向词（折叠）
- 参考图 1（单张，主体/结构参考；点击 / 拖拽 / 粘贴链接）
- 参考图 2（单张，风格参考；布局与参考图 1 完全一致）
- 图片比例芯片（9:16 / 1:1 / 16:9 / 4:5 / 3:4）
- 风格芯片（内置 14 种 preset + 用户自定义预设，均支持图标/Prompt）
- 质量芯片（标准 / 高清 / 超清 4K）+ 数量芯片（1 / 2 / 4 张）
- 参考强度滑杆（0~1）
- 参考原图 / AI 发挥 二选一
- 立即生成（满宽主按钮）
- 生成结果：不再在面板内展示，直接生成到画板（host.add_image_from_path）

自定义风格预设：
- 持久化在 ~/.cep_models/ai_style_presets.json
- 面板风格区右上角「＋ 新建预设」打开编辑对话框
- 自定义芯片右击 → 编辑 / 删除
- 生成时优先使用预设自己的 Prompt；文生图优先使用 text2img_prompt

线程安全：TaskManager 在后台线程执行 Provider，进度通过 QTimer 轮询 handle，
完成通过 handle._on_done 回调回到 GUI 线程。
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import uuid
from pathlib import Path

import numpy as np
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QByteArray, QBuffer, QSize, QUrl
from PyQt6.QtGui import QImage, QPixmap, QIcon, QDesktopServices
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QTextEdit,
    QLineEdit, QComboBox, QPushButton, QLabel, QButtonGroup,
    QFrame, QFileDialog, QSizePolicy, QProgressBar, QSlider, QScrollArea,
    QToolButton, QMenu, QDialog, QDialogButtonBox,
)

from ai import TaskRequest, ProviderDomain
from ai.service import get_ai_manager


# ── 静态配置 ──
PROVIDER_LABELS = {
    "seedream": "Seedream 5.0 Pro（火山方舟）",
    "gptimage": "GPT-Image-2（OpenAI / ModelHub）",
}

SIZE_OPTIONS = {
    "seedream": ["2048x2048", "1024x1024", "4096x4096"],
    "gptimage": ["1024x1024", "1792x1024", "1024x1792"],
}

QUALITY_OPTIONS = {
    "gptimage": ["high", "standard"],
}

CUSTOM_STYLE_FILE = Path.home() / ".cep_models" / "ai_style_presets.json"

# 内置风格 preset（单选）
# prompt / text2img_prompt 为空时，按旧逻辑「label + 风格」注入
STYLE_PRESETS = [
    {"key": "none",       "label": "不限",       "icon": "✨", "prompt": "", "text2img_prompt": ""},
    {"key": "lego",       "label": "乐高",       "icon": "🧱", "prompt": "Convert the image content elements to LEGO shapes. Do not add extra elements.", "text2img_prompt": "LEGO style, plastic bricks, playful"},
    {"key": "glass",      "label": "玻璃风",     "icon": "🪟", "prompt": "Glass morphism style, translucent glossy surfaces, soft reflections.", "text2img_prompt": "glass morphism, translucent, glossy"},
    {"key": "frosted",    "label": "磨砂风",     "icon": "❄", "prompt": "Frosted glass texture, blurred translucent surface, elegant light diffusion.", "text2img_prompt": "frosted glass, matte, soft focus"},
    {"key": "anime",      "label": "动漫",       "icon": "🎌", "prompt": "Anime illustration style, vibrant colors, clean line art.", "text2img_prompt": "anime style, vibrant, cel shaded"},
    {"key": "pixel",      "label": "像素风",     "icon": "👾", "prompt": "Pixel art style, retro 8-bit or 16-bit look, sharp square pixels.", "text2img_prompt": "pixel art, retro game style"},
    {"key": "oil",        "label": "油画",       "icon": "🎨", "prompt": "Oil painting style, rich brush strokes, classical art texture.", "text2img_prompt": "oil painting, canvas texture, brush strokes"},
    {"key": "sketch",     "label": "素描",       "icon": "✏", "prompt": "Pencil sketch style, monochrome hatching, hand-drawn lines.", "text2img_prompt": "pencil sketch, monochrome, hand drawn"},
    {"key": "cyber",      "label": "赛博朋克",   "icon": "🌃", "prompt": "Cyberpunk style, neon lights, high-tech dystopian atmosphere.", "text2img_prompt": "cyberpunk, neon lights, futuristic"},
    {"key": "clay",       "label": "黏土风",     "icon": "🟤", "prompt": "Claymation style, matte clay texture, soft rounded forms.", "text2img_prompt": "claymation, plasticine, stop motion"},
    {"key": "flat",       "label": "扁平插画",   "icon": "🟦", "prompt": "Flat illustration style, minimal shapes, bold colors, no gradients.", "text2img_prompt": "flat illustration, minimal, vector"},
    {"key": "photo",      "label": "写实",       "icon": "📷", "prompt": "Photorealistic style, highly detailed, natural lighting.", "text2img_prompt": "photorealistic, highly detailed"},
    {"key": "watercolor", "label": "水彩",       "icon": "🖌", "prompt": "Watercolor painting style, soft color bleeds, paper texture.", "text2img_prompt": "watercolor, soft bleeds, paper texture"},
    {"key": "3d",         "label": "3D 渲染",    "icon": "🧊", "prompt": "3D render style, octane/C4D look, soft studio lighting.", "text2img_prompt": "3D render, octane, studio lighting"},
]

# 比例 chip
ASPECT_OPTIONS = [
    {"key": "9:16",  "label": "9:16",  "sub": "竖屏"},
    {"key": "1:1",   "label": "1:1",   "sub": "方形"},
    {"key": "16:9",  "label": "16:9",  "sub": "横屏"},
    {"key": "4:5",   "label": "4:5",   "sub": "广告"},
    {"key": "3:4",   "label": "3:4",   "sub": "肖像"},
]

# 质量 chip
QUALITY_PRESETS = [
    {"key": "std",   "label": "标准"},
    {"key": "high",  "label": "高清"},
    {"key": "ultra", "label": "超清 4K"},
]

# 数量 chip
QUANTITY_PRESETS = [
    {"key": "1", "label": "1 张"},
    {"key": "2", "label": "2 张"},
    {"key": "4", "label": "4 张"},
]

# 尺寸（宽 × 高）映射（由比例 + provider 共同决定）
SIZE_BY_ASPECT = {
    "gptimage": {
        "1:1":  "1024x1024",
        "16:9": "1792x1024",
        "9:16": "1024x1792",
        "4:5":  "1024x1280",
        "3:4":  "1024x1365",
    },
    "seedream": {
        "1:1":  "1024x1024",
        "16:9": "1280x720",
        "9:16": "720x1280",
        "4:5":  "1024x1280",
        "3:4":  "1024x1365",
    },
}


# ═══════ 工具函数 ═══════

def _layer_pixels_to_png_bytes(arr) -> bytes:
    """numpy(H×W×4 uint8) → PNG bytes（自包含，避免依赖编辑器内部转换）。"""
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    img = QImage(arr.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba.data())


def _scale_pixmap(pm: QPixmap, w: int, h: int) -> QPixmap:
    if pm.isNull():
        return pm
    return pm.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)


def _load_custom_styles() -> list[dict]:
    """加载用户自定义风格预设。"""
    try:
        if not CUSTOM_STYLE_FILE.exists():
            return []
        with open(CUSTOM_STYLE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:  # noqa: BLE001
        pass
    return []


def _save_custom_styles(styles: list[dict]):
    """保存用户自定义风格预设。"""
    CUSTOM_STYLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CUSTOM_STYLE_FILE, "w", encoding="utf-8") as f:
        json.dump(styles, f, ensure_ascii=False, indent=2)




# ═══════ ChipGroup：单选 chip 行 ═══════

class _ChipButton(QToolButton):
    """单选 chip 按钮。"""
    def __init__(self, key: str, label: str, sub: str = "",
                 custom: bool = False, icon: str = "", parent=None):
        super().__init__(parent)
        self._key = key
        self._custom = custom
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(28)
        self.setIconSize(QSize(16, 16))
        # 图标：emoji 直接拼进文字；file:// 图片路径则作为图标渲染；为空则不显示
        if icon and icon.startswith("file://"):
            pm = QPixmap(icon[7:])
            if not pm.isNull():
                self.setIcon(QIcon(_scale_pixmap(pm, 16, 16)))
                self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                self.setText(label + (f"  {sub}" if sub else ""))
            else:
                self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
                self.setText(f"{icon} {label}" + (f"  {sub}" if sub else ""))
        elif icon:
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            self.setText(f"{icon} {label}" + (f"  {sub}" if sub else ""))
        else:
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            self.setText(label + (f"  {sub}" if sub else ""))
        self._apply_style(selected=False)

    def _apply_style(self, selected: bool):
        if selected:
            self.setStyleSheet(
                "QToolButton{background:#3d8ef8;color:#fff;border:1px solid #3d8ef8;"
                "border-radius:14px;padding:2px 12px;font-weight:bold;}"
            )
        else:
            border = "#5aa0ff" if self._custom else "#3a3a3a"
            self.setStyleSheet(
                f"QToolButton{{background:#2a2a2a;color:#ddd;border:1px solid {border};"
                f"border-radius:14px;padding:2px 12px;}}"
                f"QToolButton:hover{{background:#333;border-color:#3d8ef8;}}"
            )

    def setSelected(self, selected: bool):
        self.setChecked(selected)
        self._apply_style(selected)

    @property
    def key(self) -> str:
        return self._key


class _ChipGroup(QWidget):
    """单选 chip 行（用 FlowLayout 手撸，保证换行）。

    扩展：
    - custom_keys: 自定义 chip 集合，会显示蓝色边框并在右击时发出 context_menu_requested(key, action)
    - action_keys: 动作 chip 集合，不参与单选，点击时发出 action_triggered(key)
    """
    context_menu_requested = pyqtSignal(str, str)  # key, action("edit"/"delete")
    action_triggered = pyqtSignal(str)

    def __init__(self, options: list[dict], default_key: str = None,
                 on_change=None, custom_keys: set | None = None,
                 action_keys: set | None = None, parent=None):
        super().__init__(parent)
        self._on_change = on_change
        self._custom_keys = custom_keys or set()
        self._action_keys = action_keys or set()
        self._buttons: list[_ChipButton] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        self._flow = QWidget()
        self._flow_layout = QHBoxLayout(self._flow)
        self._flow_layout.setContentsMargins(0, 0, 0, 0)
        self._flow_layout.setSpacing(6)
        self._flow_layout.addStretch(1)
        outer.addWidget(self._flow)

        for opt in options:
            key = opt["key"]
            is_action = key in self._action_keys
            is_custom = key in self._custom_keys
            btn = _ChipButton(key, opt.get("label", key),
                              opt.get("sub", ""), custom=is_custom,
                              icon=opt.get("icon"))
            btn.setCheckable(not is_action)
            if is_action:
                btn.clicked.connect(lambda _=False, k=key: self.action_triggered.emit(k))
            else:
                btn.clicked.connect(lambda _=False, b=btn: self._on_click(b))
            if is_custom:
                btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda pos, b=btn: self._show_context_menu(b, pos))
            self._buttons.append(btn)
            # 在 stretch 之前插入
            self._flow_layout.insertWidget(self._flow_layout.count() - 1, btn)
            if default_key and key == default_key and not is_action:
                btn.setSelected(True)

    def _on_click(self, btn: _ChipButton):
        for b in self._buttons:
            if b is not btn:
                b.setSelected(False)
        btn.setSelected(True)
        if self._on_change:
            try:
                self._on_change(btn.key)
            except Exception:  # noqa: BLE001
                pass

    def _show_context_menu(self, btn: _ChipButton, pos):
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#1e1e1e;color:#ddd;border:1px solid #3a3a3a;}"
            "QMenu::item{padding:6px 18px;}"
            "QMenu::item:selected{background:#3d8ef8;}"
        )
        edit_action = menu.addAction("编辑预设")
        delete_action = menu.addAction("删除预设")
        action = menu.exec(btn.mapToGlobal(pos))
        if action == edit_action:
            self.context_menu_requested.emit(btn.key, "edit")
        elif action == delete_action:
            self.context_menu_requested.emit(btn.key, "delete")

    @property
    def selected_key(self) -> str | None:
        for b in self._buttons:
            if b.isChecked():
                return b.key
        return None

    def set_selected_key(self, key: str):
        for b in self._buttons:
            b.setSelected(b.key == key)

    def set_enabled_keys(self, keys: set[str] | None,
                         fallback_key: str | None = None):
        """按 Provider 能力启用 chip，并修正已经失效的选择。

        ``keys=None`` 表示全部启用。这个方法只改变可选状态，不重建控件，
        因此切换模型时不会破坏布局或已有信号连接。
        """
        allowed = set(keys) if keys is not None else None
        for button in self._buttons:
            button.setEnabled(allowed is None or button.key in allowed)
        selected = self.selected_key
        if selected and (allowed is None or selected in allowed):
            return
        target = fallback_key
        if target is None or (allowed is not None and target not in allowed):
            target = next(
                (button.key for button in self._buttons
                 if allowed is None or button.key in allowed),
                None,
            )
        if target:
            self.set_selected_key(target)


# ═══════ 风格预设编辑对话框 ═══════

class StylePresetDialog(QDialog):
    """新建 / 编辑自定义风格预设。

    字段：
    - 风格名称（20 字）
    - 风格图标（emoji 或本地图片路径 file://...）
    - 风格 Prompt（500 字，图生图用）
    - 文生图风格提示词（500 字，可选，留空则从风格 Prompt 推导）
    """

    def __init__(self, preset: dict | None = None, parent=None):
        super().__init__(parent)
        self._preset = dict(preset) if preset else {}
        self.setWindowTitle("编辑风格预设")
        self.setMinimumWidth(380)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        # ── 风格名称 ──
        root.addWidget(self._label("风格名称 *"))
        name_row = QHBoxLayout()
        self._name_edit = QLineEdit()
        self._name_edit.setMaxLength(20)
        self._name_edit.setPlaceholderText("输入风格名称")
        self._name_edit.setText(self._preset.get("label", ""))
        self._name_edit.textChanged.connect(self._update_name_counter)
        name_row.addWidget(self._name_edit)
        self._name_counter = QLabel("0 / 20")
        self._name_counter.setStyleSheet("color:#888;font-size:10px;min-width:48px;")
        name_row.addWidget(self._name_counter)
        root.addLayout(name_row)

        # ── 风格图标 ──
        root.addWidget(self._label("风格图标 *"))
        icon_row = QHBoxLayout()
        icon_row.setSpacing(10)

        self._icon_preview = QLabel("🎨")
        self._icon_preview.setFixedSize(64, 64)
        self._icon_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_preview.setStyleSheet(
            "QLabel{background:#1a1a1c;border:1px dashed #3a3a3a;border-radius:6px;"
            "font-size:28px;}")
        icon_row.addWidget(self._icon_preview)

        icon_right = QVBoxLayout()
        icon_right.setSpacing(4)
        self._icon_edit = QLineEdit()
        self._icon_edit.setPlaceholderText("输入 emoji 或上传正方形图片")
        self._icon_edit.setText(self._preset.get("icon", "🎨"))
        self._icon_edit.textChanged.connect(self._update_icon_preview)
        icon_right.addWidget(self._icon_edit)

        upload_btn = QPushButton("📁 上传图标")
        upload_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        upload_btn.setStyleSheet(
            "QPushButton{background:#2a2a2a;color:#ddd;border:1px solid #3a3a3a;"
            "border-radius:4px;padding:4px 10px;}"
            "QPushButton:hover{background:#333;border-color:#3d8ef8;}")
        upload_btn.clicked.connect(self._upload_icon)
        icon_right.addWidget(upload_btn, 0, Qt.AlignmentFlag.AlignLeft)
        icon_row.addLayout(icon_right, 1)
        root.addLayout(icon_row)
        root.addWidget(self._hint("支持 PNG、JPG、WebP，建议正方形"))

        # ── 风格 Prompt ──
        root.addWidget(self._label("风格 Prompt *"))
        self._prompt_edit = QTextEdit()
        self._prompt_edit.setPlaceholderText(
            "描述风格特征，例如：Convert the image content elements to LEGO shapes. "
            "Do not add any extra elements or phone frames.")
        self._prompt_edit.setText(self._preset.get("prompt", ""))
        self._prompt_edit.setMaximumHeight(90)
        self._prompt_edit.setAcceptRichText(False)
        self._prompt_edit.setStyleSheet(
            "QTextEdit{background:#1a1a1c;color:#ddd;border:1px solid #2c2c2c;"
            "border-radius:4px;padding:6px;}")
        self._prompt_edit.textChanged.connect(self._update_prompt_counter)
        root.addWidget(self._prompt_edit)
        self._prompt_counter = QLabel("0 / 500")
        self._prompt_counter.setStyleSheet("color:#888;font-size:10px;")
        self._prompt_counter.setAlignment(Qt.AlignmentFlag.AlignRight)
        root.addWidget(self._prompt_counter)

        # ── 文生图风格提示词（折叠）──
        self._t2i_toggle = QToolButton()
        self._t2i_toggle.setText("∨ 文生图风格提示词（可选）")
        self._t2i_toggle.setCheckable(True)
        self._t2i_toggle.setChecked(bool(self._preset.get("text2img_prompt")))
        self._t2i_toggle.setStyleSheet(
            "QToolButton{background:transparent;color:#888;border:0;text-align:left;"
            "padding:4px 0;}")
        self._t2i_toggle.toggled.connect(self._on_t2i_toggle)
        root.addWidget(self._t2i_toggle)

        self._t2i_edit = QTextEdit()
        self._t2i_edit.setPlaceholderText("留空则自动从风格 Prompt 推导")
        self._t2i_edit.setText(self._preset.get("text2img_prompt", ""))
        self._t2i_edit.setMaximumHeight(80)
        self._t2i_edit.setAcceptRichText(False)
        self._t2i_edit.setStyleSheet(self._prompt_edit.styleSheet())
        self._t2i_edit.textChanged.connect(self._update_t2i_counter)
        root.addWidget(self._t2i_edit)
        self._t2i_counter = QLabel("0 / 500")
        self._t2i_counter.setStyleSheet("color:#888;font-size:10px;")
        self._t2i_counter.setAlignment(Qt.AlignmentFlag.AlignRight)
        root.addWidget(self._t2i_counter)

        self._on_t2i_toggle(self._t2i_toggle.isChecked())

        # ── 按钮 ──
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        btns.setStyleSheet(
            "QPushButton{background:#2a2a2a;color:#ddd;border:1px solid #3a3a3a;"
            "border-radius:4px;padding:6px 16px;min-width:64px;}"
            "QPushButton:hover{background:#333;border-color:#3d8ef8;}")
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self._on_save)
        root.addWidget(btns)

        self._update_name_counter()
        self._update_icon_preview()
        self._update_prompt_counter()
        self._update_t2i_counter()

    def _label(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setStyleSheet("color:#ddd;font-size:12px;font-weight:bold;")
        return lab

    def _hint(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setStyleSheet("color:#777;font-size:10px;margin-top:-6px;")
        return lab

    def _update_name_counter(self):
        n = len(self._name_edit.text())
        self._name_counter.setText(f"{n} / 20")

    def _update_icon_preview(self):
        text = self._icon_edit.text().strip()
        if text.startswith("file://"):
            path = text[7:]
            pm = QPixmap(path)
            if not pm.isNull():
                self._icon_preview.setPixmap(_scale_pixmap(pm, 56, 56))
                return
        self._icon_preview.setText(text or "🎨")
        self._icon_preview.setPixmap(QPixmap())

    def _upload_icon(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择风格图标", "",
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp)")
        if path:
            self._icon_edit.setText(f"file://{path}")

    def _update_prompt_counter(self):
        n = len(self._prompt_edit.toPlainText())
        self._prompt_counter.setText(f"{n} / 500")

    def _update_t2i_counter(self):
        n = len(self._t2i_edit.toPlainText())
        self._t2i_counter.setText(f"{n} / 500")

    def _on_t2i_toggle(self, checked: bool):
        self._t2i_edit.setVisible(checked)
        self._t2i_counter.setVisible(checked)
        self._t2i_toggle.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)

    def _on_save(self):
        name = self._name_edit.text().strip()
        prompt = self._prompt_edit.toPlainText().strip()
        if not name:
            self._name_edit.setPlaceholderText("名称不能为空")
            self._name_edit.setFocus()
            return
        if not prompt:
            self._prompt_edit.setPlaceholderText("风格 Prompt 不能为空")
            self._prompt_edit.setFocus()
            return
        self._preset["label"] = name
        self._preset["icon"] = self._icon_edit.text().strip() or "🎨"
        self._preset["prompt"] = prompt
        self._preset["text2img_prompt"] = self._t2i_edit.toPlainText().strip()
        self.accept()

    def get_preset(self) -> dict:
        return self._preset



# ═══════ 参考图横向滚动区 ═══════

class _ReferenceScrollArea(QScrollArea):
    """参考图区：滚轮优先横向移动，避免事件被外层纵向滚动区抢走。"""

    def wheelEvent(self, event):
        bar = self.horizontalScrollBar()
        if bar.maximum() > bar.minimum():
            pixel = event.pixelDelta()
            angle = event.angleDelta()
            delta = pixel.x() or pixel.y()
            if not delta:
                delta = angle.x() or angle.y()
            if delta:
                # 触控板像素位移直接使用；普通滚轮每格移动约一张卡片。
                step = delta if not pixel.isNull() else delta / 120 * 120
                bar.setValue(bar.value() - int(step))
                event.accept()
                return
        super().wheelEvent(event)


# ═══════ 单张参考图：参考图 1 ═══════

class _Ref1Slot(QFrame):
    """参考图 1 单张：拖拽 / 选择文件 / 粘贴链接（可折叠）。"""
    changed = pyqtSignal(str)  # path or ""
    remove_requested = pyqtSignal(object)

    def __init__(self, title: str = "参考图 1", parent=None, removable: bool = False):
        super().__init__(parent)
        self._path: str | None = None
        self.setStyleSheet(
            "QFrame{background:#161618;border:1px dashed #3a3a3a;border-radius:6px;}"
        )
        self.setMinimumHeight(200)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(2)
        self._title = QLabel(title)
        self._title.setStyleSheet("color:#bbb; font-size:11px; background:transparent; border:0;")
        title_row.addWidget(self._title)
        title_row.addStretch(1)
        self._remove_btn = QToolButton()
        self._remove_btn.setText("删除框")
        self._remove_btn.setToolTip("删除这个参考图框")
        self._remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._remove_btn.setVisible(removable)
        self._remove_btn.setStyleSheet(
            "QToolButton{background:transparent;color:#777;border:0;font-size:9px;padding:0 2px;}"
            "QToolButton:hover{color:#ff6666;}")
        self._remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        title_row.addWidget(self._remove_btn)
        lay.addLayout(title_row)

        self._preview = QLabel("点击或拖拽")
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setStyleSheet(
            "QLabel{background:transparent;color:#666;font-size:11px;border:0;}")
        self._preview.setScaledContents(False)
        lay.addWidget(self._preview, 1)

        # × 删除按钮（右上角悬浮，有图时显示）
        self._del_btn = QPushButton("×")
        self._del_btn.setFixedSize(20, 20)
        self._del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._del_btn.setStyleSheet(
            "QPushButton{background:rgba(0,0,0,0.78);color:#fff;border:0;"
            "border-radius:10px;font-weight:bold;font-size:14px;}"
            "QPushButton:hover{background:#e44;}"
        )
        self._del_btn.clicked.connect(self.clear)
        self._del_btn.setVisible(False)
        self._del_btn.setParent(self)
        self._del_btn.raise_()

        # 可折叠的「粘贴链接」
        self._url_toggle = QToolButton()
        self._url_toggle.setText("🔗 粘贴图片链接")
        self._url_toggle.setCheckable(True)
        self._url_toggle.setStyleSheet(
            "QToolButton{background:transparent;color:#5aa0ff;border:0;"
            "text-align:left;padding:2px 0;font-size:10px;}"
            "QToolButton:hover{color:#8ac8ff;}")
        self._url_toggle.toggled.connect(self._on_url_toggle)
        lay.addWidget(self._url_toggle)

        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("粘帖图片链接…（按 Enter）")
        self._url_input.setStyleSheet(
            "QLineEdit{background:#1a1a1c;color:#ddd;border:1px solid #2c2c2c;"
            "border-radius:3px;padding:3px 6px;font-size:10px;}")
        self._url_input.returnPressed.connect(self._on_url_paste)
        self._url_input.setVisible(False)
        lay.addWidget(self._url_input)

        self.setAcceptDrops(True)

    def _on_url_toggle(self, checked: bool):
        self._url_input.setVisible(checked)
        if checked:
            self._url_input.setFocus()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if urls:
            local = urls[0].toLocalFile()
            if local and Path(local).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                self.set_path(local)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            path, _ = QFileDialog.getOpenFileName(
                self, f"选择{self._title.text()}", "",
                "图片 (*.png *.jpg *.jpeg *.webp *.bmp)")
            if path:
                self.set_path(path)

    def set_path(self, path: str):
        self._path = path
        pm = QPixmap(path)
        if not pm.isNull():
            self._preview.setPixmap(_scale_pixmap(pm, self._preview.width() - 4,
                                                   self._preview.height() - 4))
        else:
            self._preview.setText("无法加载图片")
        self._del_btn.setVisible(bool(path))
        if path:
            self._del_btn.move(self.width() - self._del_btn.width() - 4, 27)
            self._del_btn.raise_()
        self.changed.emit(path or "")

    def clear(self):
        self._path = None
        self._preview.clear()
        self._preview.setText("点击或拖拽")
        self._del_btn.setVisible(False)
        self.changed.emit("")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 删除按钮始终悬浮右上角
        if self._del_btn.isVisible():
            self._del_btn.move(self.width() - self._del_btn.width() - 4, 27)

    def set_number(self, number: int):
        """删除中间槽位后保持参考图编号连续。"""
        self._title.setText(f"参考图 {number}")

    @property
    def path(self) -> str | None:
        return self._path

    def _on_url_paste(self):
        url = self._url_input.text().strip()
        if not url:
            return
        # 本地路径
        if Path(url).exists() and Path(url).is_file():
            self.set_path(url)
            self._url_input.clear()
            return
        # 远程 URL：尝试下载到临时目录
        try:
            import urllib.request
            tmp = Path(tempfile.gettempdir()) / f"cep_ref1_{os.getpid()}_{len(os.listdir(tempfile.gettempdir()))}.png"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r, open(tmp, "wb") as f:
                f.write(r.read())
            self.set_path(str(tmp))
            self._url_input.clear()
        except Exception as e:  # noqa: BLE001
            self._preview.setText(f"下载失败：{e}")


# ═══════ 主面板 ═══════

class ImageAIPanel(QWidget):
    """AI 图片生成面板（双参考图 + 芯片式参数；结果直接生成到画板）。"""

    image_ready = pyqtSignal(str)  # 单张完成（供历史记录）

    def __init__(self, parent=None, host=None, on_status=None):
        super().__init__(parent)
        self._host = host
        self._on_status = on_status
        self._mgr = None
        self._handle = None
        self._result_paths: list[str] = []   # 当前任务的所有结果路径
        self._reference_slots: list[_Ref1Slot] = []  # 参考图 1、2、3……（不限制数量）
        self._custom_styles: list[dict] = []  # 用户自定义风格
        self._style_options: list[dict] = []  # 内置 + 自定义 合并后的选项
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(400)
        self._poll_timer.timeout.connect(self._poll_progress)
        self._is_inpaint = False          # 当前任务是否为选区局部编辑
        self._inpaint_selection = None     # 框选的 bool mask (numpy, 画布空间)
        self._inpaint_scale = 1.0          # 为满足 API 最小尺寸的缩放系数
        self._last_prompt = ""             # 最近一次生成的 prompt（用于画板命名）

        self._init_manager()
        self._load_styles()
        self._build_ui()
        if self._provider.count():
            self._refresh_provider_params()

    # ── manager ──
    def _init_manager(self):
        try:
            self._mgr = get_ai_manager()
            imgs = self._mgr.registry.by_domain(ProviderDomain.IMAGE)
        except Exception as e:  # noqa: BLE001
            self._mgr = None
            imgs = []
        self._provider = QComboBox()
        self._provider.setMinimumWidth(140)
        self._provider.blockSignals(True)
        for p in imgs:
            self._provider.addItem(PROVIDER_LABELS.get(p.name, p.name), p.name)
        self._provider.blockSignals(False)
        if not imgs:
            self._init_error = "未检测到可用的图片生成引擎。\n请配置 SEEDREAM_API_KEY 或 OPENAI_API_KEY 后重启。"

    # ── 风格预设 ──
    def _load_styles(self):
        self._custom_styles = _load_custom_styles()
        self._rebuild_style_options()

    def _rebuild_style_options(self):
        """合并内置 preset 与自定义 preset。"""
        opts = []
        for p in STYLE_PRESETS:
            opts.append({
                "key": p["key"],
                "label": p["label"],
                "icon": p["icon"],   # emoji 或 file:// 图片路径
                "prompt": p.get("prompt", ""),
                "text2img_prompt": p.get("text2img_prompt", ""),
                "built_in": True,
            })
        for c in self._custom_styles:
            key = c.get("key") or f"custom_{c.get('id', uuid.uuid4().hex[:8])}"
            c["key"] = key  # 确保 key 存在
            opts.append({
                "key": key,
                "label": c.get("label", "未命名"),
                "icon": c.get("icon", "🎨"),   # emoji 或 file:// 图片路径
                "prompt": c.get("prompt", ""),
                "text2img_prompt": c.get("text2img_prompt", ""),
                "built_in": False,
            })
        self._style_options = opts

    def prefill_prompt(self, prompt: str, aspect: str | None = None):
        """外部分镜入口：填入生成描述和画幅，不自动提交任务。"""
        self._prompt.setPlainText((prompt or "").strip())
        if aspect and hasattr(self, "_aspect_group"):
            valid = {item["key"] for item in ASPECT_OPTIONS}
            if aspect in valid:
                self._aspect_group.set_selected_key(aspect)
        self._prompt.setFocus()

    def _save_custom_styles(self):
        _save_custom_styles(self._custom_styles)

    def _find_style_option(self, key: str) -> dict | None:
        for o in self._style_options:
            if o["key"] == key:
                return o
        return None

    def _find_custom_style_index(self, key: str) -> int:
        for i, c in enumerate(self._custom_styles):
            if c.get("key") == key:
                return i
        return -1

    # ── UI ──
    def _build_ui(self):
        # 外层滚动容器：内容超长时可滚动，底部「立即生成」不再被挤压
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea{background:#1e1e1e;border:0;}"
            "QScrollBar:vertical{background:#141414;width:6px;border-radius:3px;}"
            "QScrollBar::handle:vertical{background:#3a3a3a;border-radius:3px;}"
            "QScrollBar::handle:vertical:hover{background:#4a4a4a;}")
        outer.addWidget(scroll)

        content = QWidget()
        content.setStyleSheet("background:#1e1e1e;")
        scroll.setWidget(content)

        root = QVBoxLayout(content)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ── 标题 + 引擎 ──
        title_row = QHBoxLayout()
        title = QLabel("🎨 AI 图片生成")
        title.setStyleSheet("font-weight:bold; font-size:14px; color:#00eaff;")
        title_row.addWidget(title)
        title_row.addStretch(1)
        root.addLayout(title_row)

        prov_row = QHBoxLayout()
        prov_row.addWidget(QLabel("引擎"))
        self._provider.currentTextChanged.connect(self._on_provider_changed)
        prov_row.addWidget(self._provider, 1)
        root.addLayout(prov_row)

        if not self._provider.count():
            self._notice = QLabel(getattr(self, "_init_error", "未检测到图片引擎"))
            self._notice.setStyleSheet("color:#e08; font-size:11px;")
            self._notice.setWordWrap(True)
            root.addWidget(self._notice)
            root.addStretch(1)
            return

        # ── 描述 ──
        desc_label = QLabel("生图配置")
        desc_label.setStyleSheet("font-weight:bold; color:#ddd; font-size:12px;")
        root.addWidget(desc_label)

        self._prompt = QTextEdit()
        self._prompt.setPlaceholderText("输入描述或参考图生成新图…")
        self._prompt.setMaximumHeight(72)
        self._prompt.setAcceptRichText(False)
        self._prompt.setStyleSheet(
            "QTextEdit{background:#1a1a1c;color:#ddd;border:1px solid #2c2c2c;"
            "border-radius:4px;padding:6px;}")
        root.addWidget(self._prompt)

        # ── 负向词（折叠）──
        self._neg_toggle = QToolButton()
        self._neg_toggle.setText("∨ 负向词（排除内容）")
        self._neg_toggle.setCheckable(True)
        self._neg_toggle.setStyleSheet(
            "QToolButton{background:transparent;color:#888;border:0;text-align:left;"
            "padding:4px 0;}")
        self._neg_toggle.setArrowType(Qt.ArrowType.DownArrow)
        self._neg_toggle.toggled.connect(self._on_neg_toggle)
        root.addWidget(self._neg_toggle)
        self._neg_edit = QTextEdit()
        self._neg_edit.setPlaceholderText("不希望出现的元素，如：模糊、畸形、低质量")
        self._neg_edit.setMaximumHeight(56)
        self._neg_edit.setStyleSheet(self._prompt.styleSheet())
        self._neg_edit.setVisible(False)
        root.addWidget(self._neg_edit)

        # ── 参考图：横向滚动，可无限添加 ──
        self._refs_scroll = _ReferenceScrollArea()
        self._refs_scroll.setWidgetResizable(False)
        self._refs_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._refs_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._refs_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._refs_scroll.setFixedHeight(232)
        self._refs_scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:0;}"
            "QScrollBar:horizontal{background:#141414;height:13px;border-radius:6px;margin:0 14px;}"
            "QScrollBar::handle:horizontal{background:#4a4a4a;border-radius:6px;min-width:40px;}"
            "QScrollBar::handle:horizontal:hover{background:#686868;}"
            "QScrollBar::sub-line:horizontal{background:#303034;width:13px;subcontrol-position:left;}"
            "QScrollBar::add-line:horizontal{background:#303034;width:13px;subcontrol-position:right;}"
            "QScrollBar::sub-line:horizontal:hover,QScrollBar::add-line:horizontal:hover{background:#505058;}"
            "QScrollBar::add-page:horizontal,QScrollBar::sub-page:horizontal{background:transparent;}")
        self._refs_scroll.horizontalScrollBar().setSingleStep(120)
        self._refs_inner = QWidget()
        self._refs_inner.setStyleSheet("background:transparent;")
        self._refs_layout = QHBoxLayout(self._refs_inner)
        self._refs_layout.setContentsMargins(0, 0, 0, 0)
        self._refs_layout.setSpacing(8)
        self._refs_scroll.setWidget(self._refs_inner)
        root.addWidget(self._refs_scroll)

        self._add_reference_slot()
        self._add_reference_slot()

        self._add_ref_btn = QPushButton("＋\n新建参考图")
        self._add_ref_btn.setFixedSize(150, 200)
        self._add_ref_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_ref_btn.setStyleSheet(
            "QPushButton{background:#161618;color:#888;border:1px dashed #3a3a3a;"
            "border-radius:6px;font-size:12px;}"
            "QPushButton:hover{color:#5aa0ff;border-color:#5aa0ff;background:#191b20;}")
        self._add_ref_btn.clicked.connect(self._add_reference_slot)
        self._refs_layout.addWidget(self._add_ref_btn)
        self._sync_refs_width()

        # ── 图片比例 ──
        root.addWidget(self._section_label("图片比例"))
        self._aspect_group = _ChipGroup(ASPECT_OPTIONS, default_key="1:1",
                                        on_change=lambda _k: None)
        root.addWidget(self._aspect_group)

        # ── 风格 ──
        style_header = QHBoxLayout()
        style_header.addWidget(self._section_label("风格"))
        style_header.addStretch(1)
        self._style_add_btn = QToolButton()
        self._style_add_btn.setText("＋ 新建预设")
        self._style_add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._style_add_btn.setStyleSheet(
            "QToolButton{background:transparent;color:#5aa0ff;border:0;"
            "font-size:10px;text-align:right;padding:0;}"
            "QToolButton:hover{color:#8ac8ff;}")
        self._style_add_btn.clicked.connect(self._on_style_add)
        style_header.addWidget(self._style_add_btn)
        root.addLayout(style_header)

        self._style_scroll = QScrollArea()
        self._style_scroll.setWidgetResizable(True)
        self._style_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._style_scroll.setStyleSheet("QScrollArea{background:transparent;border:0;}")
        self._style_scroll.setMaximumHeight(96)
        self._style_inner = QWidget()
        self._style_inner_layout = QVBoxLayout(self._style_inner)
        self._style_inner_layout.setContentsMargins(0, 0, 0, 0)
        self._style_scroll.setWidget(self._style_inner)
        root.addWidget(self._style_scroll)

        self._refresh_style_chips()

        # ── 质量 + 数量 ──
        qa = QHBoxLayout()
        qa.setSpacing(8)
        qa_left = QVBoxLayout()
        qa_left.setSpacing(2)
        qa_left.addWidget(self._section_label("质量"))
        self._quality_group = _ChipGroup(QUALITY_PRESETS, default_key="high",
                                         on_change=lambda _k: None)
        qa_left.addWidget(self._quality_group)
        qa.addLayout(qa_left, 1)

        qa_right = QVBoxLayout()
        qa_right.setSpacing(2)
        qa_right.addWidget(self._section_label("数量"))
        self._quantity_group = _ChipGroup(QUANTITY_PRESETS, default_key="1",
                                         on_change=lambda k: self._on_quantity_change(k))
        qa_right.addWidget(self._quantity_group)
        qa.addLayout(qa_right, 1)
        root.addLayout(qa)

        # ── 参考强度 + 二选一 ──
        sr = QVBoxLayout()
        sr.setSpacing(2)
        sr.addWidget(self._section_label("参考强度"))
        self._strength = QSlider(Qt.Orientation.Horizontal)
        self._strength.setRange(0, 100)
        self._strength.setValue(70)
        self._strength.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._strength.setTickInterval(10)
        self._strength.setStyleSheet(
            "QSlider::groove:horizontal{height:4px;background:#2a2a2a;border-radius:2px;}"
            "QSlider::sub-page:horizontal{background:#3d8ef8;border-radius:2px;}"
            "QSlider::handle:horizontal{background:#fff;width:14px;height:14px;"
            "margin:-5px 0;border-radius:7px;border:1px solid #3d8ef8;}")
        sr.addWidget(self._strength)
        self._strength_value = QLabel("70%")
        self._strength_value.setStyleSheet("color:#888; font-size:10px;")
        self._strength_value.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._strength.valueChanged.connect(
            lambda v: self._strength_value.setText(f"{v}%"))
        sr.addWidget(self._strength_value)

        # 参照原图 / AI发挥 段控件
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(6)
        self._mode_seg = QButtonGroup(self)
        self._rb_ref_strict = QPushButton("参照原图")
        self._rb_ai_free = QPushButton("AI 发挥")
        self._rb_ref_strict.setCheckable(True)
        self._rb_ai_free.setCheckable(True)
        self._rb_ref_strict.setChecked(True)
        self._mode_seg.addButton(self._rb_ref_strict, 0)
        self._mode_seg.addButton(self._rb_ai_free, 1)
        for rb in (self._rb_ref_strict, self._rb_ai_free):
            rb.setMinimumHeight(32)
            rb.setCursor(Qt.CursorShape.PointingHandCursor)
            rb.setStyleSheet(
                "QPushButton{background:#191b20;color:#9da5b2;border:1px solid #30343c;"
                "border-radius:7px;padding:6px 16px;font-size:12px;font-weight:500;}"
                "QPushButton:hover{background:#22262d;color:#e3e7ed;border-color:#4d5664;}"
                "QPushButton:pressed{background:#15171b;}"
                "QPushButton:checked{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                "stop:0 #2878e8,stop:1 #4899ff);color:#fff;border-color:#5ba5ff;"
                "font-weight:600;}"
                "QPushButton:checked:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                "stop:0 #3184f5,stop:1 #5aa6ff);border-color:#78b7ff;}")
            toggle_row.addWidget(rb)
        toggle_row.addStretch(1)
        sr.addLayout(toggle_row)
        root.addLayout(sr)

        # ── 立即生成 ──
        self._gen_btn = QPushButton("✨ 立即生成")
        self._gen_btn.setMinimumHeight(40)
        self._gen_btn.setStyleSheet(
            "QPushButton{background:#3d8ef8;color:#fff;font-weight:bold;"
            "border-radius:6px;font-size:13px;}"
            "QPushButton:hover{background:#5aa0ff;}"
            "QPushButton:disabled{background:#2a3a55;color:#888;}")
        self._gen_btn.clicked.connect(self._on_generate)
        root.addWidget(self._gen_btn)

        self._push_resource_btn = QPushButton("📚 保存本次结果到 AI 资产库")
        self._push_resource_btn.setMinimumHeight(34)
        self._push_resource_btn.setEnabled(False)
        self._push_resource_btn.setStyleSheet(
            "QPushButton{background:#24202f;color:#bfa9f5;border:1px solid #493d63;"
            "border-radius:6px;font-size:12px;}"
            "QPushButton:hover{background:#302940;color:#ddcffd;}"
            "QPushButton:disabled{background:#202023;color:#62626a;border-color:#303036;}"
        )
        self._push_resource_btn.clicked.connect(self._push_results_to_resource_center)
        root.addWidget(self._push_resource_btn)

        # ── 进度 + 状态 ──
        self._prog = QProgressBar()
        self._prog.setRange(0, 100)
        self._prog.setTextVisible(True)
        self._prog.setStyleSheet(
            "QProgressBar{background:#1a1a1c;border:1px solid #2c2c2c;border-radius:3px;"
            "text-align:center;color:#ddd;height:16px;}"
            "QProgressBar::chunk{background:#3d8ef8;border-radius:3px;}")
        root.addWidget(self._prog)

        self._status = QLabel("生成结果将直接出现在左侧画板上")
        self._status.setStyleSheet("color:#888; font-size:11px;")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        root.addStretch(1)

    def _refresh_style_chips(self, keep_key: str | None = None):
        """重新构建风格芯片组。"""
        # 移除旧组件
        while self._style_inner_layout.count():
            item = self._style_inner_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

        self._rebuild_style_options()
        custom_keys = {o["key"] for o in self._style_options if not o.get("built_in", True)}
        default_key = keep_key or "none"
        if default_key not in {o["key"] for o in self._style_options}:
            default_key = "none"

        self._style_group = _ChipGroup(
            self._style_options,
            default_key=default_key,
            on_change=lambda _k: None,
            custom_keys=custom_keys,
            parent=self._style_inner,
        )
        self._style_group.context_menu_requested.connect(self._on_style_context_menu)
        self._style_inner_layout.addWidget(self._style_group)
        self._style_inner_layout.addStretch(1)

    # ── 小工具 ──
    def _section_label(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setStyleSheet("color:#aaa; font-size:11px; padding-top:4px;")
        return lab

    def _on_neg_toggle(self, checked: bool):
        self._neg_edit.setVisible(checked)
        self._neg_toggle.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)

    def _add_reference_slot(self):
        """在新建卡片前追加一个与参考图 1/2 相同的槽位。"""
        idx = len(self._reference_slots) + 1
        slot = _Ref1Slot(f"参考图 {idx}", removable=idx > 2)
        slot.setFixedSize(150, 200)
        slot.remove_requested.connect(self._remove_reference_slot)
        self._reference_slots.append(slot)
        insert_at = (self._refs_layout.count() - 1
                     if hasattr(self, "_add_ref_btn") else self._refs_layout.count())
        self._refs_layout.insertWidget(insert_at, slot)
        self._sync_refs_width()
        QTimer.singleShot(0, lambda: self._refs_scroll.horizontalScrollBar().setValue(
            self._refs_scroll.horizontalScrollBar().maximum()))

    def _remove_reference_slot(self, slot):
        """移除新增参考图框，并重排后续编号。"""
        if slot not in self._reference_slots or len(self._reference_slots) <= 2:
            return
        self._reference_slots.remove(slot)
        self._refs_layout.removeWidget(slot)
        slot.setParent(None)
        slot.deleteLater()
        for number, item in enumerate(self._reference_slots, start=1):
            item.set_number(number)
        self._sync_refs_width()

    def _sync_refs_width(self):
        """QScrollArea 非自适应内容需要显式宽度，保证横向滚动范围持续增长。"""
        count = len(self._reference_slots) + (1 if hasattr(self, "_add_ref_btn") else 0)
        width = count * 150 + max(0, count - 1) * self._refs_layout.spacing()
        self._refs_inner.setFixedSize(width, 200)

    # ── 风格预设交互 ──
    def _on_style_add(self):
        dlg = StylePresetDialog(parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        preset = dlg.get_preset()
        preset["id"] = uuid.uuid4().hex
        preset["key"] = f"custom_{preset['id']}"
        self._custom_styles.append(preset)
        self._save_custom_styles()
        self._refresh_style_chips(keep_key=preset["key"])
        self._set_status(f"已保存风格预设：{preset.get('label', '未命名')}")

    def _on_style_context_menu(self, key: str, action: str):
        idx = self._find_custom_style_index(key)
        if idx < 0:
            return
        if action == "edit":
            old = self._custom_styles[idx]
            dlg = StylePresetDialog(preset=dict(old), parent=self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            updated = dlg.get_preset()
            updated["id"] = old.get("id", uuid.uuid4().hex)
            updated["key"] = old.get("key", f"custom_{updated['id']}")
            self._custom_styles[idx] = updated
            self._save_custom_styles()
            self._refresh_style_chips(keep_key=key)
            self._set_status(f"已更新：{updated.get('label', '未命名')}")
        elif action == "delete":
            removed = self._custom_styles.pop(idx)
            self._save_custom_styles()
            # 如果删的是当前选中，回退到「不限」
            if self._style_group.selected_key == key:
                self._style_group.set_selected_key("none")
            self._refresh_style_chips(keep_key="none")
            self._set_status(f"已删除：{removed.get('label', '未命名')}")


    def _download_url(self, url: str) -> str | None:
        try:
            import urllib.request
            tmp_dir = Path(tempfile.gettempdir())
            tmp = tmp_dir / f"cep_ref_{os.getpid()}_{len(os.listdir(tmp_dir))}.png"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r, open(tmp, "wb") as f:
                f.write(r.read())
            return str(tmp)
        except Exception as e:  # noqa: BLE001
            self._set_status(f"下载失败：{e}")
            return None

    # ── provider 切换 ──
    def _on_provider_changed(self, _):
        self._refresh_provider_params()

    def _refresh_provider_params(self):
        prov = self._provider.currentData() or ""
        # 注意：模型由引擎（provider）决定，这里不再单独提供模型下拉。
        # 数量切换仅影响下一次生成（结果直接上画板，无需预排网格）

    def _on_quantity_change(self, _k):
        pass

    def _apply_style_prompt(self, prompt: str, operation: str) -> str:
        """根据当前选中的风格 preset 注入 Prompt。"""
        key = self._style_group.selected_key
        if not key or key == "none":
            return prompt
        opt = self._find_style_option(key)
        if opt is None:
            return prompt

        # 文生图优先 text2img_prompt；图生图 / 通用用 prompt
        style_prompt = ""
        if operation == "text_to_image":
            style_prompt = opt.get("text2img_prompt", "") or opt.get("prompt", "")
        else:
            style_prompt = opt.get("prompt", "") or opt.get("text2img_prompt", "")

        if not style_prompt:
            # 无详细 prompt 时退化为旧逻辑：label + 风格
            label = opt.get("label", "").split(" ", 1)[-1] if opt.get("label") else ""
            if label:
                if "（" not in prompt:
                    return f"{prompt}（{label}风格）" if prompt else f"{label}风格"
            return prompt

        # 合并：用户 prompt 在前，风格 prompt 在后，避免覆盖主体描述
        if prompt:
            return f"{prompt}. {style_prompt}"
        return style_prompt

    # ── 生成 ──
    def generate_inpaint(self, prompt: str) -> bool:
        """供画布右键菜单直接发起选区局部修改。"""
        prompt = (prompt or "").strip()
        if not prompt:
            self._set_status("请输入局部修改描述")
            return False
        if self._handle is not None and not self._handle.is_finished:
            self._set_status("已有图片任务正在生成，请稍候")
            return False
        self._prompt.setPlainText(prompt)
        self._on_generate()
        return self._handle is not None and not self._handle.is_finished

    def _on_generate(self):
        if self._mgr is None or not self._provider.count():
            self._set_status("未配置图片生成引擎")
            return
        prompt = self._prompt.toPlainText().strip()
        self._last_prompt = prompt
        neg = self._neg_edit.toPlainText().strip()
        reference_images = [slot.path for slot in self._reference_slots if slot.path]
        any_ref = bool(reference_images)
        if not prompt and not any_ref:
            self._set_status("请输入描述或上传参考图")
            return

        prov = self._provider.currentData() or self._provider.currentText()
        aspect = self._aspect_group.selected_key or "1:1"
        quality = self._quality_group.selected_key or "high"
        quantity = int(self._quantity_group.selected_key or "1")
        strength = self._strength.value() / 100.0

        params = {
            "size": SIZE_BY_ASPECT.get(prov, SIZE_BY_ASPECT["gptimage"]).get(aspect, "1024x1024"),
            "n": quantity,
            "strength": strength,
            "strict": self._rb_ref_strict.isChecked(),
            "quality": quality,
        }
        inputs: dict = {"prompt": ""}    # 提前初始化，下方各分支按需填充

        # 决定 operation 与 inputs
        # 检查画板是否有激活的选区 → 自动进入「局部编辑」模式
        sel = self._host.selection if self._host is not None else None
        self._is_inpaint = sel is not None and sel.any()
        self._inpaint_selection = sel.copy() if self._is_inpaint else None
        auto_routed_from = ""

        # 框选任务必须交给支持 inpaint 的引擎。当前引擎不支持时，优先自动
        # 切换到 GPT-Image，避免把必然失败的任务提交给 Seedream。
        if self._is_inpaint:
            current_provider = self._mgr.registry.get(prov)
            if current_provider is None or not current_provider.supports("inpaint"):
                candidates = self._mgr.registry.by_capability("inpaint")
                target_provider = next(
                    (item for item in candidates if item.name == "gptimage"),
                    candidates[0] if candidates else None,
                )
                if target_provider is None:
                    self._set_status(
                        "当前引擎不支持选区局部修改；请先配置 GPT-Image 引擎。")
                    self._is_inpaint = False
                    self._inpaint_selection = None
                    return
                auto_routed_from = prov
                prov = target_provider.name
                provider_index = self._provider.findData(prov)
                if provider_index >= 0:
                    self._provider.setCurrentIndex(provider_index)

        if self._is_inpaint:
            operation = "inpaint"
            # 提取画布合成图
            composite = self._host._render_composite(for_export=True)
            cw, ch = self._host.project.w, self._host.project.h
            # 转 QImage → bytes（统一用 PIL 便于后续缩放）
            from PIL import Image as PILImage
            buf = QByteArray()
            qbuf = QBuffer(buf)
            qbuf.open(QBuffer.OpenModeFlag.WriteOnly)
            composite.save(qbuf, "PNG")
            comp_pil = PILImage.open(io.BytesIO(bytes(buf))).convert("RGB")
            # 蒙版：OpenAI 规定透明区域 = 待编辑区域
            mask_alpha = np.where(sel, 0, 255).astype(np.uint8)
            mask_arr = np.full((*mask_alpha.shape, 4), 255, dtype=np.uint8)
            mask_arr[:, :, 3] = mask_alpha
            mask_pil = PILImage.fromarray(mask_arr, mode="RGBA")
            # GPT-Image 最短边至少 1024，且宽高都必须是 16 的倍数。
            # 先按最短边放大，再统一向上对齐，避免 1024×1833 这类尺寸被 API 拒绝。
            MIN_PX = 1024
            ALIGN = 16
            original_cw, original_ch = cw, ch
            scale = max(1.0, MIN_PX / min(cw, ch))
            target_cw = int(np.ceil(cw * scale / ALIGN) * ALIGN)
            target_ch = int(np.ceil(ch * scale / ALIGN) * ALIGN)
            if (target_cw, target_ch) != (cw, ch):
                comp_pil = comp_pil.resize((target_cw, target_ch), PILImage.LANCZOS)
                mask_pil = mask_pil.resize((target_cw, target_ch), PILImage.NEAREST)
                cw, ch = target_cw, target_ch
            # 该字段在完成回调中也作为“需要缩回原画布”的标志。
            self._inpaint_scale = max(cw / original_cw, ch / original_ch)
            comp_io = io.BytesIO()
            comp_pil.save(comp_io, "PNG")
            inputs["image"] = comp_io.getvalue()
            mask_io = io.BytesIO()
            mask_pil.save(mask_io, "PNG")
            inputs["mask"] = mask_io.getvalue()
            inputs["canvas_size"] = (cw, ch)
            params["size"] = f"{cw}x{ch}"
            route_note = "，已自动切换至 GPT-Image" if auto_routed_from else ""
            self._set_status(f"选区局部编辑（{cw}×{ch}{route_note}）…")
        elif reference_images:
            operation = "image_edit"
            # 保留 image 兼容旧 provider，同时用 images 将 1、2、3……全部传给模型。
            inputs["image"] = reference_images[0]
            inputs["images"] = reference_images
            inputs["style_images"] = reference_images[1:]
        else:
            operation = "text_to_image"
            self._inpaint_selection = None

        # 注入风格 Prompt
        prompt = self._apply_style_prompt(prompt, operation)
        if operation == "inpaint":
            prompt = (
                f"{prompt}\n\n"
                "仅修改透明蒙版指定的区域。严格保持原图的镜头、构图、主体位置、"
                "大小、轮廓、姿态、透视和光照不变；不要移动或重复主体；"
                "让修改内容与蒙版边缘及周围画面自然、无缝衔接。"
            )
        inputs["prompt"] = prompt
        if neg:
            inputs["negative_prompt"] = neg

        # 参照原图 / AI 发挥：当前图片 provider 无原生 strict 字段，
        # 将意图编码进 prompt 指令，使该开关真正影响出图（图生图时生效）
        if operation == "image_edit":
            if self._rb_ref_strict.isChecked():
                inputs["prompt"] = f"{inputs['prompt']}（严格参照参考图的内容与构图）"
            else:
                inputs["prompt"] = f"{inputs['prompt']}（在参考图基础上自由发挥创意，仅保留风格）"

        # style key 也带上，便于 provider 识别
        style_key = self._style_group.selected_key
        if style_key and style_key != "none":
            inputs["style"] = style_key

        req = TaskRequest(operation=operation, inputs=inputs, params=params)
        self._set_status("提交生成任务…")
        self._gen_btn.setEnabled(False)
        self._prog.setValue(0)
        self._result_paths = []
        self._push_resource_btn.setEnabled(False)
        self._done_called = False
        try:
            h = self._mgr.submit(prov, req)
            self._handle = h
            # notify_done 在后台 Python 线程触发 → QTimer.singleShot 切回主线程
            h._on_done.append(lambda hh: QTimer.singleShot(0, lambda: self._on_done(hh)))
            self._poll_timer.start()
        except Exception as e:  # noqa: BLE001
            self._set_status(f"提交失败：{e}")
            self._gen_btn.setEnabled(True)

    def _poll_progress(self):
        if self._handle is None:
            self._poll_timer.stop()
            return
        prog = int(self._handle.progress * 100)
        self._prog.setValue(max(self._prog.value(), prog))
        # 安全网：如果回调因任何原因没触发，轮询也能驱动完成
        if self._handle.is_finished:
            self._poll_timer.stop()
            if not self._done_called:
                self._on_done(self._handle)

    def _on_done(self, h):
        if self._done_called:
            return
        self._done_called = True
        self._gen_btn.setEnabled(True)
        self._poll_timer.stop()
        self._prog.setValue(100 if h.is_success else self._prog.value())
        if h.is_success and h.result:
            data = h.result.data
            # 兼容：单路径 str 或 list[str]
            if isinstance(data, str):
                paths = [data]
            elif isinstance(data, (list, tuple)):
                paths = [str(x) for x in data]
            else:
                paths = [str(data)]
            self._result_paths = paths
            self._push_resource_btn.setEnabled(bool(paths))

            inserted = 0
            container = None
            if self._host is not None and hasattr(self._host, "host"):
                container = self._host.host  # ImageEditorContainer
            for p in paths:
                add_path = p
                # 若 inpaint 结果被放大过，先缩小回画布原始尺寸
                if self._is_inpaint and self._inpaint_scale != 1.0 \
                        and self._host is not None:
                    try:
                        from PIL import Image as PILImage
                        oh, ow = self._host.project.h, self._host.project.w
                        res_pil = PILImage.open(p).convert("RGBA")
                        res_pil = res_pil.resize((ow, oh), PILImage.LANCZOS)
                        downscaled = Path(p).with_stem(Path(p).stem + "_resized")
                        res_pil.save(downscaled, "PNG")
                        add_path = str(downscaled)
                    except Exception:
                        pass
                # 局部重绘直接作为透明选区图层叠回当前画板，便于撤销和继续编辑。
                if self._is_inpaint and self._host is not None \
                        and self._inpaint_selection is not None:
                    try:
                        name = f"AI 局部修改 · {(self._last_prompt or '未命名')[:14]}"
                        self._host.add_ai_inpaint_result(
                            add_path, self._inpaint_selection, name=name)
                        inserted += 1
                    except Exception:
                        pass
                # 普通生图仍新建画板，尺寸匹配生成图。
                elif container is not None:
                    try:
                        from PIL import Image as PILImage
                        img = PILImage.open(add_path)
                        iw, ih = img.size
                        name = (self._last_prompt or "AI 生成")[:18]
                        new_doc = container.new_document(iw, ih, name=name)
                        new_doc.add_image_from_path(add_path)
                        inserted += 1
                    except Exception:
                        pass  # 极端情况下回退：仅发信号，不入画板
                self.image_ready.emit(p)
            if inserted:
                self._set_status(f"完成：{len(paths)} 张，已生成到画板 ✓")
            else:
                if self._is_inpaint and self._host is not None \
                        and hasattr(self._host, "_cancel_ai_compare"):
                    self._host._cancel_ai_compare()
                self._set_status(f"完成：{len(paths)} 张 ✓（未连接画板，已写入历史）")
        else:
            err = h.result.error if h.result else "未知错误"
            if self._is_inpaint and self._host is not None \
                    and hasattr(self._host, "_cancel_ai_compare"):
                self._host._cancel_ai_compare()
            self._set_status(f"失败：{err}")
        self._is_inpaint = False
        self._inpaint_selection = None

    def _push_results_to_resource_center(self):
        paths = [path for path in self._result_paths if path and os.path.exists(path)]
        if not paths:
            self._set_status("当前没有可推送的生成结果")
            return
        try:
            from ai.ui.resource_center import import_assets_to_resource_center
            result = import_assets_to_resource_center(self, paths, default_kind="character")
            if result:
                kind, items = result
                label = "角色" if kind == "character" else "场景"
                self._set_status(f"已保存到 AI 资产库：{len(items)} 个{label}资产 ✓")
        except Exception as error:
            self._set_status(f"保存到 AI 资产库失败：{error}")

    # ── 工具 ──
    def _set_status(self, msg: str):
        self._status.setText(msg)
        if self._on_status:
            try:
                self._on_status(msg)
            except Exception:  # noqa: BLE001
                pass
