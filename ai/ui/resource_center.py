"""
AI 资源中心 V4 — 商业级 IDE 工作流布局。

架构（无缝三列 QHBoxLayout，用明度而非物理边框区分 Z 轴）：
- 左 SidebarNav (200px)：类别导航 + 标签树 + 平台过滤
- 中 MainCanvas (stretch)：顶部 Action Bar(搜索+新建/复制/删除/检查器开关)
                        + 人物/场景=自适应缩略图网格，Prompt=紧凑列表视图
- 右 PropertyInspector (320px, 可折叠)：默认隐藏，选中资产时滑出；
                        阅览模式(无边框高对比) / 编辑模式(边框输入)；
                        人物/场景 Hero 图(点击灯箱) / Prompt 代码高亮块。

设计令牌：画布 #121214(最深) / 侧栏·检查器 #18181a(隆起) / 卡片 #1b1b1e。
强调色仅用于：左侧选中高亮条、卡片 hover 微光、Prompt 变量高亮；保存按钮统一蓝 #3d8ef8。
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
import copy
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap, QIcon
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QLineEdit, QTextEdit,
    QSpinBox, QPushButton, QScrollArea, QFrame, QGridLayout, QFormLayout,
    QCheckBox, QSizePolicy, QDialog, QComboBox, QDialogButtonBox,
    QFileDialog, QMessageBox, QProgressBar, QToolButton, QTabWidget,
)

from ai.service import get_asset_db, get_ai_manager
from ai.assets import (
    Character, Scene, Element, PromptTemplate,
    approved_asset_path, asset_is_approved, approve_asset_version,
    assign_asset_view,
)
from ai import TaskRequest, ProviderDomain


# ── 设计令牌 ──
BG_PAGE = "#121214"      # 画布（最深）
BG_PANEL = "#18181a"     # 侧栏 / 检查器（隆起）
BG_CARD = "#1b1b1e"
BG_INPUT = "#1a1a1d"
BORDER = "#232327"
TEXT_MAIN = "#e8e8ec"
TEXT_DIM = "#9a9aa2"
TEXT_MUTED = "#5c5c64"
ACCENT_BLUE = "#3d8ef8"  # 保存 / 全局强调（不随类别变）

KIND_ORDER = ["character", "scene", "element", "prompt"]
KIND_META = {
    "character": {"label": "角色", "accent": "#b98cff"},
    "scene":     {"label": "场景", "accent": "#48d597"},
    "element":   {"label": "元素", "accent": "#4fc4e8"},
    "prompt":    {"label": "Prompt", "accent": "#ffa53d"},
}
DB_MAP = {
    "character": ("list_characters", "save_character", "delete_character", Character),
    "scene":     ("list_scenes", "save_scene", "delete_scene", Scene),
    "element":   ("list_elements", "save_element", "delete_element", Element),
    "prompt":    ("list_prompts", "save_prompt", "delete_prompt", PromptTemplate),
}
PROVIDERS = ["Seedream", "Veo", "GPT Image", "Kling", "FLUX"]

BASE_QSS = """
#ResourceCenterRoot { background-color: #121214; }
QWidget { font-family: "Inter", -apple-system, "Microsoft YaHei", sans-serif; color: #e8e8ec; font-size: 13px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { border: none; background: transparent; width: 6px; margin: 0; }
QScrollBar::handle:vertical { background: rgba(255,255,255,0.10); border-radius: 3px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: rgba(255,255,255,0.20); }
QScrollBar:horizontal { border: none; background: transparent; height: 6px; }
QScrollBar::handle:horizontal { background: rgba(255,255,255,0.10); border-radius: 3px; min-height: 30px; }
QLineEdit, QTextEdit, QSpinBox {
    background-color: #1a1a1d; border: 1px solid transparent; border-radius: 6px;
    padding: 6px 12px; color: #e8e8ec;
}
QLineEdit:hover, QTextEdit:hover { background-color: #202024; }
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus { background-color: #121215; border: 1px solid #3d8ef8; }
QCheckBox { color: #9a9aa2; spacing: 6px; }
QCheckBox::indicator { width: 14px; height: 14px; border-radius: 3px; background: #232327; }
QCheckBox::indicator:checked { background: #3d8ef8; }
"""


# ── 图像工具 ──
def _cover(pm: QPixmap, size: int) -> QPixmap:
    if pm.isNull():
        return pm
    if pm.width() == pm.height():
        return pm.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    side = min(pm.width(), pm.height())
    x = (pm.width() - side) // 2
    y = (pm.height() - side) // 2
    return pm.copy(x, y, side, side).scaled(
        size, size, Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation)


def _fit(pm: QPixmap, max_w: int, max_h: int) -> QPixmap:
    if pm.isNull():
        return pm
    return pm.scaled(max_w, max_h, Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)


def _thumb(path: str, size: int, accent: str) -> QPixmap:
    if path and os.path.exists(path):
        pm = QPixmap(path)
        if not pm.isNull():
            return _cover(pm, size)
    pm = QPixmap(size, size)
    pm.fill(QColor(accent).darker(170))
    p = QPainter(pm)
    p.setPen(QColor("#ffffff"))
    f = QFont(); f.setPointSize(int(size * 0.26))
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "无图")
    p.end()
    return pm


def _highlight_prompt(text: str) -> str:
    """把 {var} 渲染成橙色高亮的 HTML。"""
    esc = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    esc = re.sub(r"(\{[^{}]*\})", '<span style="color:#ffa53d;font-weight:600;">\\1</span>', esc)
    return ("<div style='font-family:Consolas,Menlo,monospace;font-size:13px;"
            "line-height:1.6;white-space:pre-wrap;'>%s</div>" % esc)


# ── 文本省略标签 ──
class ElideLabel(QLabel):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._mode = Qt.TextElideMode.ElideRight

    def setElideMode(self, m):
        self._mode = m
        self.update()

    def paintEvent(self, ev):
        if self.text():
            fm = self.fontMetrics()
            txt = fm.elidedText(self.text(), self._mode, self.width() - 2)
            p = QPainter(self)
            p.setPen(self.palette().color(self.foregroundRole()))
            p.drawText(self.rect(), self.alignment(), txt)
        else:
            super().paintEvent(ev)


def _read_list(w) -> list:
    if hasattr(w, "text"):
        txt = w.text()
    else:
        txt = w.toPlainText() if hasattr(w, "toPlainText") else ""
    return [x.strip() for x in txt.split(",") if x.strip()]


def _read_list_val(val) -> str:
    return ", ".join(val) if isinstance(val, list) else ""


# ── 灯箱 ──
class Lightbox(QDialog):
    def __init__(self, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background:rgba(0,0,0,0.92);")
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lab = QLabel()
        pm = _fit(pixmap, 1100, 760)
        lab.setPixmap(pm)
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(lab)
        self.resize(pm.width() + 40, pm.height() + 40)
        self._lab = lab

    def mousePressEvent(self, ev):
        self.accept()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.accept()


# ── 左侧导航 ──
class SidebarNav(QWidget):
    categoryChanged = pyqtSignal(str)
    filterChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(200)
        self.setStyleSheet("SidebarNav{background-color:%s;}" % BG_PANEL)
        self._kind = "character"
        self._accent = KIND_META["character"]["accent"]
        self._tag_btns: dict = {}
        self._prov_checks: dict = {}
        self._selected_tags: set = set()
        self._selected_prov: set = set()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # 标题
        title = QLabel("资产管理（兼容界面）")
        title.setStyleSheet("color:%s;font-weight:bold;font-size:14px;padding:16px 16px 12px;" % TEXT_MAIN)
        lay.addWidget(title)

        # 类别导航
        nav = QWidget()
        nl = QVBoxLayout(nav)
        nl.setContentsMargins(8, 0, 8, 0)
        nl.setSpacing(2)
        self._cat_btns: dict = {}
        for k in KIND_ORDER:
            b = QPushButton(KIND_META[k]["label"])
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, kk=k: self._set_cat(kk))
            self._cat_btns[k] = b
            nl.addWidget(b)
        lay.addWidget(nav)

        # 标签树
        tag_title = QLabel("标签")
        tag_title.setStyleSheet("color:%s;font-size:11px;text-transform:uppercase;"
                               "letter-spacing:1px;padding:18px 16px 6px;" % TEXT_MUTED)
        lay.addWidget(tag_title)
        self._tag_box = QWidget()
        self._tag_lay = QVBoxLayout(self._tag_box)
        self._tag_lay.setContentsMargins(8, 0, 8, 0)
        self._tag_lay.setSpacing(2)
        lay.addWidget(self._tag_box)

        # 平台过滤
        prov_title = QLabel("平台 / Provider")
        prov_title.setStyleSheet("color:%s;font-size:11px;text-transform:uppercase;"
                                 "letter-spacing:1px;padding:18px 16px 6px;" % TEXT_MUTED)
        lay.addWidget(prov_title)
        self._prov_box = QWidget()
        pl = QVBoxLayout(self._prov_box)
        pl.setContentsMargins(14, 0, 8, 0)
        pl.setSpacing(4)
        for p in PROVIDERS:
            cb = QCheckBox(p)
            cb.stateChanged.connect(self._on_prov)
            self._prov_checks[p] = cb
            pl.addWidget(cb)
        lay.addWidget(self._prov_box)

        lay.addStretch(1)
        self._prov_box.setVisible(False)

    def _style_cat(self, k, on):
        accent = KIND_META[k]["accent"]
        self._cat_btns[k].setStyleSheet(
            "QPushButton{background-color:%s;border:none;color:%s;text-align:left;"
            "padding:8px 12px;border-radius:6px;font-size:13px;font-weight:%s;}"
            % ("#23232a" if on else "transparent",
               accent if on else TEXT_DIM, "bold" if on else "normal")
        )

    def _set_cat(self, k):
        if k == self._kind:
            return
        self._kind = k
        self._accent = KIND_META[k]["accent"]
        for kk in KIND_ORDER:
            self._style_cat(kk, kk == k)
        self._selected_tags.clear()
        self._prov_box.setVisible(k == "prompt")
        self._selected_prov.clear()
        for cb in self._prov_checks.values():
            cb.setChecked(False)
        self.categoryChanged.emit(k)

    def set_kind(self, k):
        self._kind = k
        self._accent = KIND_META[k]["accent"]
        for kk in KIND_ORDER:
            self._style_cat(kk, kk == k)
        self._prov_box.setVisible(k == "prompt")

    def set_tags(self, tags: list):
        # 重建标签按钮
        while self._tag_lay.count():
            w = self._tag_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._tag_btns.clear()
        for t in sorted(set(tags)):
            b = QPushButton("· " + t)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setProperty("tag", t)
            b.clicked.connect(self._on_tag)
            self._tag_btns[t] = b
            self._tag_lay.addWidget(b)
        self._refresh_tag_styles()

    def _on_tag(self):
        t = self.sender().property("tag")
        if t in self._selected_tags:
            self._selected_tags.discard(t)
        else:
            self._selected_tags.add(t)
        self._refresh_tag_styles()
        self.filterChanged.emit()

    def _refresh_tag_styles(self):
        for t, b in self._tag_btns.items():
            on = t in self._selected_tags
            b.setStyleSheet(
                "QPushButton{background-color:%s;border:none;color:%s;text-align:left;"
                "padding:5px 12px;border-radius:5px;font-size:12px;}"
                % ("#2a2a32" if on else "transparent",
                   self._accent if on else TEXT_DIM)
            )

    def _on_prov(self):
        self._selected_prov = {p for p, cb in self._prov_checks.items() if cb.isChecked()}
        self.filterChanged.emit()

    def current_filter(self) -> tuple:
        return (set(self._selected_tags), set(self._selected_prov))


# ── 中间画布：卡片 ──
class ResourceCard(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, item, kind: str, accent: str, parent=None):
        super().__init__(parent)
        self._id = item.id
        self._accent = accent
        self.setFixedSize(140, 190)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("ResourceCard")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 8)
        lay.setSpacing(6)

        cover = QLabel()
        cover.setFixedSize(132, 132)
        cover.setPixmap(_thumb(self._first_img(item), 132, accent))
        cover.setStyleSheet("border-radius:6px;background-color:#232328;")
        lay.addWidget(cover)

        name = ElideLabel(getattr(item, "name", "") or "未命名")
        name.setStyleSheet("color:%s;font-weight:600;padding-left:4px;font-size:12px;" % TEXT_MAIN)
        lay.addWidget(name)

        meta = ElideLabel(self._meta(kind, item))
        meta.setStyleSheet("color:%s;font-size:11px;padding-left:4px;" % accent)
        lay.addWidget(meta)

        self._apply_style()

    def _apply_style(self):
        self.setStyleSheet(
            "#ResourceCard{background-color:#1b1b1e;border:1px solid transparent;border-radius:10px;}"
            "#ResourceCard:hover{background-color:#222226;border:1px solid #3f3f46;}"
            "#ResourceCard[selected=\"true\"]{background-color:#202028;border:1px solid %s;}"
            % self._accent
        )

    @staticmethod
    def _first_img(item) -> str:
        master = approved_asset_path(item)
        if master:
            return master
        imgs = getattr(item, "reference_images", None)
        if isinstance(imgs, list) and imgs:
            return imgs[0]
        return ""

    @staticmethod
    def _meta(kind: str, it) -> str:
        approval = ""
        if kind in ("character", "scene", "element"):
            approval = (f"主参考 v{max(1, int(getattr(it, 'version', 0) or 0))}"
                        if asset_is_approved(it, require_file=False) else "草稿")
        if kind == "character":
            parts = [approval]
            if getattr(it, "age", 0):
                parts.append("%d岁" % it.age)
            if getattr(it, "gender", ""):
                parts.append(it.gender)
            if getattr(it, "tags", []):
                parts.append("%d标签" % len(it.tags))
            return " · ".join(parts) or "未填写"
        if kind == "scene":
            parts = [approval] + list(getattr(it, "tags", []) or [])
            return " · ".join(parts)
        if kind == "prompt":
            return it.category or "未分类"
        return approval

    def set_selected(self, on: bool):
        self.setProperty("selected", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, ev):
        self.clicked.emit(self._id)
        super().mousePressEvent(ev)


# ── 中间画布：Prompt 列表行 ──
class PromptRow(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, item, accent: str, parent=None):
        super().__init__(parent)
        self._id = item.id
        self._accent = accent
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("PromptRow")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(12)

        badge = QLabel(item.category or "未分类")
        badge.setFixedSize(64, 22)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet("background-color:#23232a;color:%s;border-radius:4px;font-size:11px;"
                            "font-weight:600;" % accent)
        lay.addWidget(badge)

        mid = QVBoxLayout()
        mid.setSpacing(3)
        name = ElideLabel(getattr(item, "name", "") or "未命名")
        name.setStyleSheet("color:%s;font-weight:600;font-size:13px;" % TEXT_MAIN)
        mid.addWidget(name)
        snip = ElideLabel(getattr(item, "template", "") or "")
        snip.setStyleSheet("color:%s;font-size:11px;" % TEXT_DIM)
        mid.addWidget(snip)
        lay.addLayout(mid, 1)

        self._apply_style()

    def _apply_style(self):
        self.setStyleSheet(
            "#PromptRow{background-color:#1b1b1e;border:1px solid transparent;"
            "border-left:3px solid transparent;border-radius:6px;}"
            "#PromptRow:hover{background-color:#222226;}"
            "#PromptRow[selected=\"true\"]{background-color:#202028;border-left:3px solid %s;}"
            % self._accent
        )

    def set_selected(self, on: bool):
        self.setProperty("selected", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, ev):
        self.clicked.emit(self._id)
        super().mousePressEvent(ev)


# ── 中间画布 ──
class MainCanvas(QWidget):
    itemSelected = pyqtSignal(str)
    duplicate = pyqtSignal()
    deleteCurrent = pyqtSignal()
    toggleInspector = pyqtSignal()

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setStyleSheet("MainCanvas{background-color:%s;}" % BG_PAGE)
        self._kind = "character"
        self._accent = KIND_META["character"]["accent"]
        self._items: list = []
        self._cards: list = []
        self._selected_id: str | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Action Bar
        bar = QWidget()
        bar.setStyleSheet("QWidget{background-color:%s;border-bottom:1px solid %s;}" % (BG_PANEL, BORDER))
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(16, 10, 16, 10)
        bl.setSpacing(8)
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索名称 / 标签 / 模板…")
        self._search.setFixedHeight(32)
        self._search.textChanged.connect(self._apply_filter)
        bl.addWidget(self._search, 1)
        for label, sig in (("复制", self.duplicate), ("删除", self.deleteCurrent)):
            b = QPushButton(label)
            b.setFixedHeight(32)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton{background-color:#23232a;color:%s;border:1px solid %s;"
                "border-radius:6px;padding:0 14px;font-size:12px;}"
                "QPushButton:hover{background-color:#2c2c34;color:#fff;}"
                % (TEXT_DIM, BORDER)
            )
            b.clicked.connect(sig)
            bl.addWidget(b)
        self._ins_btn = QPushButton("检查器")
        self._ins_btn.setFixedHeight(32)
        self._ins_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ins_btn.setStyleSheet(
            "QPushButton{background-color:#23232a;color:%s;border:1px solid %s;"
            "border-radius:6px;padding:0 14px;font-size:12px;}"
            "QPushButton:hover{background-color:#2c2c34;color:#fff;}"
            "QPushButton:checked{background-color:%s;color:#fff;}" % (TEXT_DIM, BORDER, ACCENT_BLUE)
        )
        self._ins_btn.setCheckable(True)
        self._ins_btn.clicked.connect(self.toggleInspector)
        bl.addWidget(self._ins_btn)

        lay.addWidget(bar)

        # 内容区
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("QScrollArea{background-color:%s;}" % BG_PAGE)
        self._content = QWidget()
        self._content.setStyleSheet("QWidget{background-color:%s;}" % BG_PAGE)
        self._glay = QGridLayout(self._content)
        self._glay.setContentsMargins(16, 16, 16, 16)
        self._glay.setSpacing(12)
        self._glay.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(self._content)
        lay.addWidget(self._scroll, 1)

    def set_kind(self, kind, accent):
        self._kind = kind
        self._accent = accent
        self._selected_id = None
        self._ins_btn.setChecked(False)

    def load(self, items):
        self._items = items
        self._apply_filter()

    def set_tag_filter(self, tags, providers):
        self._filter_tags = tags
        self._filter_prov = providers
        self._apply_filter()

    def _apply_filter(self):
        q = self._search.text().strip().lower()
        tags = getattr(self, "_filter_tags", set())
        provs = getattr(self, "_filter_prov", set())
        out = []
        for it in self._items:
            hay = (getattr(it, "name", "") + " " + " ".join(getattr(it, "tags", []))
                   + " " + getattr(it, "template", "")).lower()
            if q and q not in hay:
                continue
            if tags and not (tags & set(getattr(it, "tags", []))):
                continue
            if provs and self._kind == "prompt":
                item_provs = {x.strip() for x in getattr(it, "provider", "").split(",") if x.strip()}
                if not (provs & item_provs):
                    continue
            out.append(it)
        self._render(out)

    def _render(self, items):
        self._glay.setSpacing(8 if self._kind == "prompt" else 12)
        for c in self._cards:
            c.deleteLater()
        self._cards.clear()
        if not items:
            empty = QLabel("暂无资产，请先在 AI 制片画布创作并保存到资产库")
            empty.setStyleSheet("color:%s;font-size:13px;padding:40px 0;" % TEXT_MUTED)
            self._glay.addWidget(empty, 0, 0)
            self._cards.append(empty)
            return
        if self._kind == "prompt":
            for i, it in enumerate(items):
                row = PromptRow(it, self._accent)
                row.clicked.connect(self._on_pick)
                row.set_selected(it.id == self._selected_id)
                self._cards.append(row)
                self._glay.addWidget(row, i, 0)
        else:
            cols = max(1, (self._content.width() - 16) // 152)
            for i, it in enumerate(items):
                card = ResourceCard(it, self._kind, self._accent)
                card.clicked.connect(self._on_pick)
                card.set_selected(it.id == self._selected_id)
                self._cards.append(card)
                self._glay.addWidget(card, i // cols, i % cols)

    def _on_pick(self, cid):
        self._selected_id = cid
        for c in self._cards:
            if isinstance(c, (ResourceCard, PromptRow)):
                c.set_selected(c._id == cid)
        self.itemSelected.emit(cid)

    def mark_selected(self, cid):
        self._selected_id = cid
        for c in self._cards:
            if isinstance(c, (ResourceCard, PromptRow)):
                c.set_selected(c._id == cid)

    def set_inspector_btn(self, on: bool):
        self._ins_btn.setChecked(on)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self._cards and self._kind != "prompt":
            self._apply_filter()


# ── 右侧检查器 ──
class PropertyInspector(QFrame):
    saved = pyqtSignal(object)
    studioRequested = pyqtSignal(object, str)
    removeRequested = pyqtSignal(object, str)
    deleteRequested = pyqtSignal(object, str)

    def __init__(self, db, parent=None, compact: bool = False):
        super().__init__(parent)
        self.db = db
        self._compact = bool(compact)
        self.setFixedWidth(320)
        self.setStyleSheet("PropertyInspector{background-color:%s;}" % BG_PANEL)
        self._kind = "character"
        self._accent = KIND_META["character"]["accent"]
        self._current_id = None
        self._fields: dict = {}
        self._edit = False
        self._hero_pixmap = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # 标题栏
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(20, 16, 16, 16)
        self._h_title = QLabel("检查器")
        self._h_title.setStyleSheet("color:%s;font-weight:bold;font-size:14px;" % TEXT_MAIN)
        hl.addWidget(self._h_title)
        hl.addStretch(1)
        self._edit_btn = QPushButton("编辑")
        self._edit_btn.setFixedHeight(28)
        self._edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._edit_btn.setStyleSheet(
            "QPushButton{background-color:#23232a;color:%s;border:1px solid %s;"
            "border-radius:5px;padding:0 12px;font-size:12px;}"
            "QPushButton:hover{background-color:#2c2c34;color:#fff;}" % (TEXT_DIM, BORDER))
        self._edit_btn.clicked.connect(self._toggle_edit)
        hl.addWidget(self._edit_btn)
        self._collapse = QPushButton("›")
        self._collapse.setFixedSize(28, 28)
        self._collapse.setCursor(Qt.CursorShape.PointingHandCursor)
        self._collapse.setStyleSheet(
            "QPushButton{background-color:transparent;color:%s;border:none;font-size:16px;}"
            "QPushButton:hover{color:#fff;}" % TEXT_DIM)
        self._collapse.clicked.connect(lambda: self.set_visible(False))
        hl.addWidget(self._collapse)
        lay.addWidget(head)

        # 滚动区
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("QScrollArea{background-color:%s;border:none;}" % BG_PANEL)
        self._body = QWidget()
        self._body.setStyleSheet("QWidget{background-color:%s;}" % BG_PANEL)
        self._blay = QVBoxLayout(self._body)
        self._blay.setContentsMargins(20, 8, 20, 20)
        self._blay.setSpacing(14)
        self._scroll.setWidget(self._body)
        lay.addWidget(self._scroll, 1)

        # 保存栏
        save_bar = QWidget()
        save_bar.setStyleSheet("QWidget{background-color:%s;border-top:1px solid %s;}" % (BG_PANEL, BORDER))
        sl = QHBoxLayout(save_bar)
        sl.setContentsMargins(20, 12, 20, 12)
        sl.addStretch(1)
        self._save_btn = QPushButton("保存修改")
        self._save_btn.setFixedSize(100, 32)
        self._save_btn.setStyleSheet(
            "QPushButton{background-color:%s;color:#fff;border:none;border-radius:5px;"
            "font-weight:bold;font-size:12px;}"
            "QPushButton:hover{background-color:#2b7ce6;}"
            "QPushButton:disabled{background-color:#26262c;color:#666;}" % ACCENT_BLUE)
        self._save_btn.clicked.connect(self._on_save)
        sl.addWidget(self._save_btn)
        lay.addWidget(save_bar)

    def set_visible(self, on: bool):
        self.setVisible(on)
        if not on:
            self._current_id = None

    def show_hint(self, text):
        self._clear()
        self._h_title.setText("检查器")
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setStyleSheet("color:%s;font-size:13px;padding:20px 4px;" % TEXT_MUTED)
        self._blay.addWidget(lab)

    def load(self, item, kind, accent):
        self._kind = kind
        self._accent = accent
        self._current_id = getattr(item, "id", "") or None
        self._orig_item = item
        self._edit = False
        self._clear()
        if self._compact:
            self._h_title.setText({
                "character": "角色 / 主体", "scene": "场景", "element": "元素",
            }.get(kind, KIND_META[kind]["label"]))
        else:
            self._h_title.setText(KIND_META[kind]["label"] + " · 检查器")
        self._edit_btn.setText("编辑")
        self._save_btn.setEnabled(False)
        self._build_hero(item)
        self._build_body(item)
        self._apply_mode()

    def _clear(self):
        while self._blay.count():
            w = self._blay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._fields.clear()
        getattr(self, "_prov_checks", {}).clear()

    # ── Hero / 预览 ──
    def _build_hero(self, item):
        if self._kind in ("character", "scene", "element"):
            imgs = getattr(item, "reference_images", []) or []
            master = approved_asset_path(item)
            if master and os.path.exists(master):
                imgs = [master] + [path for path in imgs if path != master]
            hero = QLabel()
            hero_height = 132 if self._compact else 180
            hero.setFixedHeight(hero_height)
            hero.setStyleSheet("background-color:#0e0e10;border-radius:8px;")
            hero.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if imgs:
                pm = QPixmap(imgs[0])
                if not pm.isNull():
                    fit = _fit(pm, 480 if self._compact else 280, hero_height)
                    hero.setPixmap(fit)
                    self._hero_pixmap = pm
                    hero.setCursor(Qt.CursorShape.PointingHandCursor)
                    hero.mousePressEvent = lambda _e: Lightbox(pm, self).exec()
            else:
                hero.setText("暂无参考图")
                hero.setStyleSheet("background-color:#0e0e10;border-radius:8px;color:%s;font-size:12px;" % TEXT_MUTED)
                self._hero_pixmap = None
            self._blay.addWidget(hero)
        elif self._kind == "prompt":
            prev = QTextEdit()
            prev.setReadOnly(True)
            prev.setHtml(_highlight_prompt(getattr(item, "template", "")))
            prev.setMinimumHeight(120)
            prev.setStyleSheet(
                "QTextEdit{background-color:#09090b;border-left:3px solid %s;border-radius:4px;"
                "font-family:Consolas,Menlo,monospace;color:%s;padding:12px;}" % (self._accent, TEXT_MAIN))
            self._blay.addWidget(prev)

    # ── 表单 ──
    def _build_body(self, item):
        if self._compact and self._kind in ("character", "scene", "element"):
            self._build_compact_body(item)
            self._blay.addStretch(1)
            return
        if self._kind in ("character", "scene", "element"):
            self._build_approval_summary(item)
        self._add_line("name", "名称", getattr(item, "name", ""))
        if self._kind == "character":
            self._add_combo("entity_type", "角色类型", [
                ("人类", "human"), ("动物", "animal"), ("怪物 / 生物", "monster"),
                ("机器人 / 机械", "robot"), ("拟人物体", "object"), ("其他", "other"),
            ], getattr(item, "entity_type", "human"))
            self._add_line("life_stage", "年龄 / 生命阶段（可选）",
                           getattr(item, "life_stage", ""))
            self._add_line("gender", "性别 / 性别表达（可选）", getattr(item, "gender", ""))
            self._add_text("description", "角色描述", getattr(item, "description", ""), 72)
            self._add_text("design_notes", "不可漂移的设计特征",
                           getattr(item, "design_notes", ""), 72)
            self._add_text("seedream_prompt", "固定生图 Prompt",
                           getattr(item, "seedream_prompt", ""), 82)
            self._add_text("veo_prompt", "固定视频 Prompt",
                           getattr(item, "veo_prompt", ""), 72)
        elif self._kind == "scene":
            self._add_text("description", "描述", getattr(item, "description", ""), 72)
            self._add_text("seedream_prompt", "固定生图 Prompt",
                           getattr(item, "seedream_prompt", ""), 82)
        elif self._kind == "element":
            self._add_combo("element_type", "元素类型", [
                ("手机 / 电脑壁纸", "wallpaper"), ("Logo", "logo"),
                ("App / UI界面", "ui"), ("产品 / 包装", "product"),
                ("普通道具", "prop"), ("贴纸 / 海报", "sticker"), ("其他", "other"),
            ], getattr(item, "element_type", "wallpaper"))
            self._add_combo("default_mode", "默认植入方式", [
                ("精确植入（不允许AI重画）", "exact"),
                ("AI参考（允许模型重绘）", "reference"),
            ], getattr(item, "default_mode", "exact"))
            self._add_text("description", "元素描述", getattr(item, "description", ""), 64)
            self._add_text("seedream_prompt", "固定生图 Prompt",
                           getattr(item, "seedream_prompt", ""), 82)
            self._add_line("master_image", "候选原图（定稿请进入制作台）",
                           getattr(item, "master_image", ""))
            self._add_line("mask_path", "可选蒙版文件", getattr(item, "mask_path", ""))
            self._add_text("placement_hint", "默认放置说明",
                           getattr(item, "placement_hint", ""), 58)
        elif self._kind == "prompt":
            box = QWidget()
            bl = QHBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(12)
            sel = {x.strip() for x in getattr(item, "provider", "").split(",") if x.strip()}
            self._prov_checks = {}
            for t in PROVIDERS:
                cb = QCheckBox(t)
                cb.setChecked(t in sel)
                cb.setEnabled(False)
                self._prov_checks[t] = cb
                bl.addWidget(cb)
            bl.addStretch(1)
            self._add_custom("适用于", box)
            self._add_line("category", "分类", getattr(item, "category", ""))
            self._add_line("vars", "变量 (逗号分隔)", _read_list_val(getattr(item, "defaults", {}).get("vars", [])))
            self._add_text("template", "模板 (支持 {var})", getattr(item, "template", ""), 90)
        # Prompt / 高级
        if self._kind in ("character", "scene", "element", "prompt"):
            self._add_list("tags", "标签 (逗号分隔)", getattr(item, "tags", []))
        if self._kind in ("character", "scene", "element"):
            self._add_list("reference_images", "参考图路径 (逗号分隔)", getattr(item, "reference_images", []))
            self._build_asset_actions()
        if self._kind == "character":
            views = getattr(item, "reference_views", {}) or {}
            self._add_line("view_front", "三视图 · 正面", views.get("front", ""))
            self._add_line("view_side", "三视图 · 侧面", views.get("side", ""))
            self._add_line("view_back", "三视图 · 背面", views.get("back", ""))
            self._add_line("view_three_quarter", "补充 · 3/4 视角",
                           views.get("three_quarter", ""))
            self._add_line("embedding_path", "embedding / LoRA 路径", getattr(item, "embedding_path", ""))
        elif self._kind == "scene":
            views = getattr(item, "reference_views", {}) or {}
            self._add_line("scene_view_empty_plate", "场景视角 · 无人空场",
                           views.get("empty_plate", ""))
            self._add_line("scene_view_camera_a", "固定机位 · A机位",
                           views.get("camera_a", ""))
            self._add_line("scene_view_camera_b", "固定机位 · B机位",
                           views.get("camera_b", ""))
            self._add_line("scene_view_reverse_a", "固定机位 · A反打",
                           views.get("reverse_a", ""))
            self._add_line("scene_view_reverse_b", "固定机位 · B反打",
                           views.get("reverse_b", ""))
            self._add_line("scene_view_detail", "固定机位 · 特写 / 插入",
                           views.get("detail", ""))
        self._blay.addStretch(1)

    def _build_compact_body(self, item):
        """制片画布只展示会直接影响生成的信息，把可选资料折叠。"""
        self._add_line("name", "名称", getattr(item, "name", ""))
        if self._kind == "character":
            self._add_combo("entity_type", "这是什么", [
                ("人类", "human"), ("动物", "animal"),
                ("怪物 / 生物", "monster"), ("机器人 / 机械", "robot"),
                ("拟人物体", "object"), ("其他", "other"),
            ], getattr(item, "entity_type", "human"))
            self._add_text("description", "外观和风格",
                           getattr(item, "description", ""), 76)
            self._add_text("design_notes", "每个镜头都要保持的特征",
                           getattr(item, "design_notes", ""), 76)
        elif self._kind == "scene":
            self._add_text("description", "场景描述",
                           getattr(item, "description", ""), 86)
        else:
            self._add_combo("element_type", "元素类型", [
                ("手机 / 电脑壁纸", "wallpaper"), ("Logo", "logo"),
                ("App / UI 界面", "ui"), ("产品 / 包装", "product"),
                ("普通道具", "prop"), ("贴纸 / 海报", "sticker"),
                ("其他", "other"),
            ], getattr(item, "element_type", "wallpaper"))
            self._add_combo("default_mode", "出现方式", [
                ("保持原样（不允许 AI 重画）", "exact"),
                ("作为参考（允许 AI 重画）", "reference"),
            ], getattr(item, "default_mode", "exact"))
            self._add_text("description", "元素描述",
                           getattr(item, "description", ""), 70)
            self._add_text("placement_hint", "默认出现在哪里",
                           getattr(item, "placement_hint", ""), 58)

        refs = list(getattr(item, "reference_images", []) or [])
        views = getattr(item, "reference_views", {}) or {}
        approved = approved_asset_path(item)
        version = max(1, int(getattr(item, "version", 0) or 0)) if approved else 0
        summary = QLabel(
            f"参考图 {len(refs)} 张 · 固定视角 {len(views)} 个 · "
            + (f"主参考 v{version} 已锁定" if approved else "尚未选定主参考"))
        summary.setWordWrap(True)
        summary.setStyleSheet(
            "color:#73c69c;background:#17221d;border-radius:6px;padding:8px;font-size:10px;"
            if approved else
            "color:#c5a66a;background:#221e16;border-radius:6px;padding:8px;font-size:10px;")
        self._blay.addWidget(summary)
        self._build_asset_actions()
        self._build_compact_advanced(item)
        self._build_compact_management(item)

    def _build_compact_advanced(self, item):
        toggle = QPushButton("更多资料 ▾")
        toggle.setStyleSheet(
            "QPushButton{background:transparent;color:#8d8d98;border:1px solid #303038;"
            "border-radius:5px;padding:6px;text-align:left;}"
        )
        box = QFrame()
        box.setStyleSheet(
            "QFrame{background:#151519;border:1px solid #292930;border-radius:7px;}"
        )
        layout = QVBoxLayout(box)
        layout.setContentsMargins(9, 9, 9, 9)
        layout.setSpacing(9)
        self._field_layout = layout
        if self._kind == "character":
            self._add_line("life_stage", "年龄 / 生命阶段（可选）",
                           getattr(item, "life_stage", ""))
            self._add_line("gender", "性别表达（可选）", getattr(item, "gender", ""))
            self._add_text("seedream_prompt", "图片提示词",
                           getattr(item, "seedream_prompt", ""), 82)
            self._add_text("veo_prompt", "视频提示词",
                           getattr(item, "veo_prompt", ""), 72)
        else:
            self._add_text("seedream_prompt", "图片提示词",
                           getattr(item, "seedream_prompt", ""), 82)
        self._add_list("tags", "标签（可选）", getattr(item, "tags", []))
        self._field_layout = None
        box.setVisible(False)

        def toggle_box():
            show = box.isHidden()
            box.setVisible(show)
            toggle.setText("收起资料 ▴" if show else "更多资料 ▾")

        toggle.clicked.connect(toggle_box)
        self._blay.addWidget(toggle)
        self._blay.addWidget(box)

    def _build_compact_management(self, item):
        if not getattr(item, "id", ""):
            return
        box = QFrame()
        box.setStyleSheet(
            "QFrame{background:#151519;border:1px solid #292930;border-radius:7px;}"
        )
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        title = QLabel("资产管理")
        title.setStyleSheet("color:#8c8c96;font-size:10px;")
        layout.addWidget(title)
        remove = QPushButton("从当前画布移出")
        remove.setToolTip("仅整理当前项目，资产仍保留在资产库")
        remove.clicked.connect(
            lambda _=False, current=item, kind=self._kind:
            self.removeRequested.emit(current, kind))
        layout.addWidget(remove)
        delete = QPushButton("从资产库删除…")
        delete.setStyleSheet(
            "QPushButton{background:#2b1d20;color:#e69a9a;border:1px solid #573238;"
            "border-radius:5px;padding:7px;}QPushButton:hover{background:#3a2529;}"
        )
        delete.clicked.connect(
            lambda _=False, current=item, kind=self._kind:
            self.deleteRequested.emit(current, kind))
        layout.addWidget(delete)
        self._blay.addWidget(box)

    def _build_approval_summary(self, item):
        approved = asset_is_approved(item, require_file=False)
        version = max(1, int(getattr(item, "version", 0) or 0)) if approved else 0
        box = QFrame()
        box.setStyleSheet(
            "QFrame{background:#151519;border:1px solid #292930;border-radius:7px;}")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        if self._compact:
            badge_text = (f"● 已选定主参考 · v{version}"
                          if approved else "○ 还没有选定主参考图")
        else:
            badge_text = (f"● 已有主参考 · v{version}"
                          if approved else "○ 草稿 · 尚未进入生产链")
        badge = QLabel(badge_text)
        badge.setStyleSheet(
            "color:%s;font-size:12px;font-weight:bold;" %
            ("#67d8a2" if approved else "#d1a867"))
        layout.addWidget(badge)
        path = approved_asset_path(item)
        if self._compact:
            detail_text = (
                f"生成分镜时优先使用：{Path(path).name}" if path else
                "先上传或生成图片，再选一张作为主参考。")
        else:
            detail_text = (
                f"分镜使用：{path}" if path else
                "还没有主参考；单图导入会自动选定，AI 生成多张时请选择一张。")
        detail = QLabel(detail_text)
        detail.setWordWrap(True)
        detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail.setStyleSheet("color:#7f8490;font-size:10px;")
        layout.addWidget(detail)
        history_count = len(getattr(item, "version_history", []) or [])
        if history_count and not self._compact:
            history = QLabel(f"已保留 {history_count} 个批准版本记录")
            history.setStyleSheet("color:#62626c;font-size:10px;")
            layout.addWidget(history)
        self._blay.addWidget(box)

    def _build_asset_actions(self):
        box = QFrame()
        box.setStyleSheet(
            "QFrame{background:#151519;border:1px solid #292930;border-radius:7px;}"
            "QPushButton{background:#24242b;color:#d9d9df;border:1px solid #393943;"
            "border-radius:5px;padding:7px;}QPushButton:hover{border-color:#3d8ef8;}"
        )
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        title = QLabel("参考图与生成" if self._compact else "视觉资产制作")
        title.setStyleSheet("color:#b9a8ed;font-size:11px;font-weight:bold;")
        layout.addWidget(title)
        upload = QPushButton("＋ 上传参考图" if self._compact else "＋ 上传 / 追加参考图")
        upload.setToolTip("编辑时可直接选择多张图片，不需要手填文件路径")
        upload.clicked.connect(self._upload_reference_images)
        layout.addWidget(upload)
        studio = QPushButton("编辑参考图和说明")
        studio.setStyleSheet(
            "QPushButton{background:#24242b;color:#d9d9df;border:1px solid #393943;"
            "border-radius:5px;padding:7px;}"
            "QPushButton:hover{border-color:#62626f;background:#292930;}"
        )
        studio.clicked.connect(self._open_asset_studio)
        layout.addWidget(studio)
        self._blay.addWidget(box)

    def _upload_reference_images(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择参考图", "",
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)")
        if not paths:
            return
        if not self._edit:
            self._toggle_edit()
        field = self._fields.get("reference_images")
        refs = (_read_list(field) if field is not None else
                list(getattr(self._orig_item, "reference_images", []) or []))
        refs.extend(path for path in paths if path not in refs)
        if field is not None:
            field.setText(", ".join(refs))
        else:
            self._orig_item.reference_images = refs
        # “上传”就是一次提交动作：连同当前表单修改立即保存，并刷新 Hero 预览。
        item = self._on_save()
        if item is not None:
            if not asset_is_approved(item, require_file=False) and paths:
                approve_asset_version(
                    item, paths[0], source="upload:auto_first")
                getattr(self.db, f"save_{self._kind}")(item)
                self.saved.emit(item)
            self.load(item, self._kind, self._accent)

    def _open_asset_studio(self):
        item = None
        if self._edit or not self._current_id:
            item = self._on_save()
        if item is None and self._current_id:
            item = getattr(self.db, f"get_{self._kind}")(self._current_id)
        if item is not None:
            self.studioRequested.emit(item, self._kind)

    def _add_line(self, key, label, val):
        w = QLineEdit(str(val) if val else "")
        self._fields[key] = w
        self._add_custom(label, w)

    def _add_int(self, key, label, val):
        w = QSpinBox()
        w.setRange(0, 200)
        w.setValue(int(val) if isinstance(val, int) else 0)
        self._fields[key] = w
        self._add_custom(label, w)

    def _add_combo(self, key, label, options, selected):
        w = QComboBox()
        for text, value in options:
            w.addItem(text, value)
        index = w.findData(selected)
        w.setCurrentIndex(index if index >= 0 else 0)
        self._fields[key] = w
        self._add_custom(label, w)

    def _add_text(self, key, label, val, h=72):
        w = QTextEdit()
        w.setPlainText(str(val) if val else "")
        w.setAcceptRichText(False)
        w.setMaximumHeight(h)
        self._fields[key] = w
        self._add_custom(label, w)

    def _add_list(self, key, label, val):
        w = QLineEdit(_read_list_val(val))
        self._fields[key] = w
        self._add_custom(label, w)

    def _add_custom(self, label, widget):
        row = QWidget()
        rl = QVBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(5)
        lab = QLabel(label)
        lab.setStyleSheet("color:%s;font-size:11px;" % TEXT_DIM)
        rl.addWidget(lab)
        rl.addWidget(widget)
        target = getattr(self, "_field_layout", None) or self._blay
        target.addWidget(row)

    # ── 阅览 / 编辑 双态 ──
    def _toggle_edit(self):
        self._edit = not self._edit
        self._edit_btn.setText("阅览" if self._edit else "编辑")
        self._save_btn.setEnabled(self._edit)
        self._apply_mode()

    def _apply_mode(self):
        for w in self._fields.values():
            if isinstance(w, QSpinBox):
                w.setReadOnly(not self._edit)
                w.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons if not self._edit else
                                   QSpinBox.ButtonSymbols.UpDownArrows)
            elif isinstance(w, QTextEdit):
                w.setReadOnly(not self._edit)
            elif isinstance(w, QLineEdit):
                w.setReadOnly(not self._edit)
            elif isinstance(w, QComboBox):
                w.setEnabled(self._edit)
        for cb in getattr(self, "_prov_checks", {}).values():
            cb.setEnabled(self._edit)
        self._style_fields()

    def _style_fields(self):
        if self._edit:
            st = ("background-color:%s;border:1px solid %s;border-radius:6px;padding:6px 10px;"
                  "color:%s;font-size:12px;" % (BG_INPUT, BORDER, TEXT_MAIN))
        else:
            st = ("background-color:transparent;border:1px solid transparent;border-radius:6px;"
                  "padding:6px 10px;color:%s;font-size:13px;" % TEXT_MAIN)
        for w in self._fields.values():
            if isinstance(w, (QLineEdit, QTextEdit, QSpinBox, QComboBox)):
                w.setStyleSheet(st)

    # ── 保存 ──
    def _on_save(self):
        cls = DB_MAP[self._kind][3]
        vals: dict = {}
        for f in cls.__dataclass_fields__:
            if f in ("id", "created_at", "updated_at"):
                continue
            if self._kind == "prompt":
                if f == "provider":
                    vals[f] = ",".join(t for t, cb in self._prov_checks.items() if cb.isChecked())
                    continue
                if f == "defaults":
                    d = dict(getattr(self._orig_item, "defaults", {}) or {})
                    vv = self._get("vars")
                    if vv:
                        d["vars"] = _read_list(self._fields.get("vars"))
                    else:
                        d.pop("vars", None)
                    vals[f] = d
                    continue
                if f == "vars":
                    continue
            if f == "reference_views":
                if self._compact:
                    vals[f] = dict(getattr(self._orig_item, f, {}) or {})
                    continue
                if self._kind == "character":
                    vals[f] = {
                        key: self._get(f"view_{key}") for key in
                        ("front", "side", "back", "three_quarter")
                        if self._get(f"view_{key}")
                    }
                elif self._kind == "scene":
                    original = dict(getattr(self._orig_item, "reference_views", {}) or {})
                    values = {
                        key: self._get(f"scene_view_{key}") for key in
                        ("empty_plate", "camera_a", "camera_b", "reverse_a",
                         "reverse_b", "detail")
                        if self._get(f"scene_view_{key}")
                    }
                    master = original.get("master") or approved_asset_path(self._orig_item)
                    if master:
                        values["master"] = master
                    vals[f] = values
                else:
                    vals[f] = getattr(self._orig_item, f, {})
                continue
            if f in ("reference_images", "tags"):
                if f in self._fields:
                    vals[f] = _read_list(self._fields.get(f))
                else:
                    vals[f] = list(getattr(self._orig_item, f, []) or [])
            elif f not in self._fields:
                vals[f] = getattr(self._orig_item, f, "")
            else:
                vals[f] = self._get(f)
        item = cls(**vals)
        item.id = self._current_id or uuid.uuid4().hex
        item.updated_at = time.time()
        getattr(self.db, DB_MAP[self._kind][1])(item)
        self._current_id = item.id
        self._orig_item = item
        self._edit = False
        self._edit_btn.setText("编辑")
        self._save_btn.setEnabled(False)
        self._apply_mode()
        self.saved.emit(item)
        return item

    def _get(self, key, default=""):
        w = self._fields.get(key)
        if w is None:
            return default
        if isinstance(w, QSpinBox):
            return w.value()
        if isinstance(w, QComboBox):
            return w.currentData()
        if isinstance(w, QTextEdit):
            return w.toPlainText()
        return w.text()


class AssetStudioDialog(QDialog):
    """资源中心内的视觉资产制作台：辅助 Prompt、文生图、图生图与候选定稿。"""

    assetSaved = pyqtSignal(str, str)

    def __init__(self, item, kind: str, db, parent=None, embedded: bool = False):
        super().__init__(parent)
        self.item = item
        self.kind = kind
        self.db = db
        self.manager = get_ai_manager()
        self._generation_handle = None
        self._prompt_handle = None
        self._candidate_paths: list[str] = []
        self._selected_candidate = ""
        self._working_reference = ""
        self.asset_saved = False
        self.embedded = bool(embedded)
        self.setWindowTitle(f"AI 视觉资产制作 · {getattr(item, 'name', '未命名')}")
        if self.embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
            self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        else:
            # 生成任务在后台运行，资产窗口可以最小化而不阻塞主工作台。
            self.setModal(False)
            self.setWindowModality(Qt.WindowModality.NonModal)
            self.setWindowFlags(
                Qt.WindowType.Window |
                Qt.WindowType.WindowMinimizeButtonHint |
                Qt.WindowType.WindowMaximizeButtonHint |
                Qt.WindowType.WindowCloseButtonHint)
            self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(1080, 800)
        self.setStyleSheet(
            "QDialog{background:#121214;color:#e8e8ec;} QLabel{color:#aaa;}"
            "QLineEdit,QTextEdit,QComboBox{background:#1b1b20;color:#eee;"
            "border:1px solid #34343d;border-radius:6px;padding:6px;}"
            "QPushButton{background:#25252c;color:#ddd;border:1px solid #3a3a44;"
            "border-radius:5px;padding:7px 11px;}QPushButton:hover{border-color:#3d8ef8;}"
            "QPushButton:disabled{color:#666;background:#202024;border-color:#29292f;}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel(
            f"{KIND_META.get(kind, {}).get('label', '资产')} · "
            f"{getattr(item, 'name', '未命名')}")
        title.setStyleSheet("color:#fff;font-size:17px;font-weight:bold;")
        header.addWidget(title)
        header.addStretch()
        self.approval_badge = QLabel()
        header.addWidget(self.approval_badge)
        if not self.embedded:
            note = QLabel("可最小化此窗口，主工作台仍可继续生成场景、主体和元素")
            note.setStyleSheet("color:#7b8090;font-size:11px;")
            header.addWidget(note)
        root.addLayout(header)

        content = QHBoxLayout()
        content.setSpacing(12)
        left = QFrame()
        left.setFixedWidth(390)
        left.setStyleSheet("QFrame{background:#18181c;border-radius:8px;}")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(12, 12, 12, 12)
        ll.setSpacing(8)

        prompt_head = QHBoxLayout()
        prompt_head.addWidget(QLabel("生成要求（参考生图时写希望如何变化）"))
        prompt_head.addStretch()
        self.prompt_assist_btn = QPushButton("✨ AI辅助填写")
        self.prompt_assist_btn.clicked.connect(self._assist_prompt)
        prompt_head.addWidget(self.prompt_assist_btn)
        save_prompt_btn = QPushButton("保存Prompt")
        save_prompt_btn.clicked.connect(self._save_prompt)
        prompt_head.addWidget(save_prompt_btn)
        ll.addLayout(prompt_head)
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setAcceptRichText(False)
        self.prompt_edit.setMinimumHeight(180)
        self.prompt_edit.setPlaceholderText(
            "文生图可描述完整画面；参考生图只需写希望如何变化。留空时会自动保持参考图生成一致性变体。")
        self.prompt_edit.setPlainText(
            getattr(item, "seedream_prompt", "") or
            getattr(item, "description", ""))
        ll.addWidget(self.prompt_edit)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("生成方式"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("文生图 · 从设定生成", "text_to_image")
        self.mode_combo.addItem("图生图 · 基于参考继续设计", "image_edit")
        self.mode_combo.currentIndexChanged.connect(self._refresh_providers)
        mode_row.addWidget(self.mode_combo, 1)
        ll.addLayout(mode_row)

        settings = QHBoxLayout()
        self.provider_combo = QComboBox()
        settings.addWidget(self.provider_combo, 2)
        self.aspect_combo = QComboBox()
        for text, data in (("原图比例", "original"), ("9:16", "9:16"),
                           ("1:1", "1:1"), ("16:9", "16:9")):
            self.aspect_combo.addItem(text, data)
        settings.addWidget(self.aspect_combo, 1)
        self.count_combo = QComboBox()
        for count in (1, 2, 4, 6):
            self.count_combo.addItem(f"{count} 张", count)
        self.count_combo.setCurrentIndex(self.count_combo.findData(4))
        settings.addWidget(self.count_combo, 1)
        ll.addLayout(settings)

        ref_head = QHBoxLayout()
        ref_head.addWidget(QLabel("图生图参考（点击缩略图选择）"))
        ref_head.addStretch()
        upload = QPushButton("上传参考")
        upload.clicked.connect(self._upload_references)
        ref_head.addWidget(upload)
        ll.addLayout(ref_head)
        self.ref_scroll = QScrollArea()
        self.ref_scroll.setWidgetResizable(True)
        self.ref_scroll.setFixedHeight(108)
        self.ref_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.ref_scroll.setStyleSheet(
            "QScrollArea{background:#101012;border:1px solid #292930;border-radius:6px;}"
            "QScrollArea QWidget{background:#101012;}")
        self.ref_widget = QWidget()
        self.ref_widget.setStyleSheet("background:#101012;")
        self.ref_layout = QHBoxLayout(self.ref_widget)
        self.ref_layout.setContentsMargins(2, 2, 2, 2)
        self.ref_layout.setSpacing(6)
        self.ref_layout.addStretch()
        self.ref_scroll.setWidget(self.ref_widget)
        ll.addWidget(self.ref_scroll)

        self.generate_btn = QPushButton("开始生成候选")
        self.generate_btn.setMinimumHeight(38)
        self.generate_btn.setStyleSheet(
            "QPushButton{background:#2867bd;color:#fff;border:none;border-radius:6px;"
            "font-weight:bold;}QPushButton:hover{background:#3378d3;}"
            "QPushButton:disabled{background:#252b35;color:#677080;}"
        )
        self.generate_btn.clicked.connect(self._generate)
        ll.addWidget(self.generate_btn)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setFixedHeight(5)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet(
            "QProgressBar{background:#282830;border:none;}"
            "QProgressBar::chunk{background:#3d8ef8;}")
        ll.addWidget(self.progress)
        self.status = QLabel("可直接文生图；图生图前先选择一张参考图")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#7f8490;font-size:11px;")
        ll.addWidget(self.status)
        ll.addStretch()
        content.addWidget(left)

        right = QFrame()
        right.setStyleSheet("QFrame{background:#161619;border-radius:8px;}")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(12, 12, 12, 12)
        candidate_head = QHBoxLayout()
        candidate_title = QLabel("生成候选")
        candidate_title.setStyleSheet("color:#fff;font-size:14px;font-weight:bold;")
        candidate_head.addWidget(candidate_title)
        candidate_head.addStretch()
        self.selected_label = QLabel("尚未选择候选")
        self.selected_label.setStyleSheet("color:#7f8490;font-size:11px;")
        candidate_head.addWidget(self.selected_label)
        rl.addLayout(candidate_head)
        self.candidate_scroll = QScrollArea()
        self.candidate_scroll.setWidgetResizable(True)
        self.candidate_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.candidate_scroll.setStyleSheet(
            "QScrollArea{background:#101012;border:none;}"
            "QScrollArea QWidget{background:#101012;}")
        self.candidate_widget = QWidget()
        self.candidate_widget.setStyleSheet("background:#101012;")
        self.candidate_grid = QGridLayout(self.candidate_widget)
        self.candidate_grid.setContentsMargins(2, 2, 2, 2)
        self.candidate_grid.setSpacing(8)
        self.candidate_scroll.setWidget(self.candidate_widget)
        rl.addWidget(self.candidate_scroll, 1)
        actions = QHBoxLayout()
        self.finalize_btn = QPushButton("✓ 使用这张")
        self.finalize_btn.setEnabled(False)
        self.finalize_btn.setStyleSheet(
            "QPushButton{background:#275f49;color:#b9f3d7;border:1px solid #398466;"
            "border-radius:5px;padding:8px;}QPushButton:disabled{color:#666;"
            "background:#222;border-color:#333;}")
        self.finalize_btn.clicked.connect(lambda: self._save_candidate(True))
        actions.addWidget(self.finalize_btn)
        self.add_ref_btn = QPushButton("追加为补充参考")
        self.add_ref_btn.setEnabled(False)
        self.add_ref_btn.clicked.connect(lambda: self._save_candidate(False))
        actions.addWidget(self.add_ref_btn)
        self.continue_btn = QPushButton("用选中图继续图生图")
        self.continue_btn.setEnabled(False)
        self.continue_btn.clicked.connect(self._continue_from_candidate)
        actions.addWidget(self.continue_btn)
        rl.addLayout(actions)
        cleanup_actions = QHBoxLayout()
        self.remove_candidate_btn = QPushButton("移除选中候选")
        self.remove_candidate_btn.setEnabled(False)
        self.remove_candidate_btn.setToolTip("只从当前候选列表移除，不删除本地图片")
        self.remove_candidate_btn.clicked.connect(self._remove_selected_candidate)
        cleanup_actions.addWidget(self.remove_candidate_btn)
        self.finalize_clean_btn = QPushButton("使用这张并清理其他")
        self.finalize_clean_btn.setEnabled(False)
        self.finalize_clean_btn.setStyleSheet(
            "QPushButton{background:#273b34;color:#b9f3d7;border:1px solid #3d6656;"
            "border-radius:5px;padding:8px;}QPushButton:disabled{color:#666;"
            "background:#222;border-color:#333;}"
        )
        self.finalize_clean_btn.clicked.connect(self._finalize_and_clean_candidates)
        cleanup_actions.addWidget(self.finalize_clean_btn)
        rl.addLayout(cleanup_actions)
        if self.kind in ("character", "scene"):
            view_actions = QHBoxLayout()
            view_actions.addWidget(QLabel("保存为固定视角"))
            self.view_role_combo = QComboBox()
            options = (
                (("正面", "front"), ("3/4 视角", "three_quarter"),
                 ("侧面", "side"), ("背面", "back"))
                if self.kind == "character" else
                (("无人空场", "empty_plate"), ("A机位", "camera_a"),
                 ("B机位", "camera_b"), ("A反打", "reverse_a"),
                 ("B反打", "reverse_b"), ("特写 / 插入", "detail")))
            for text_value, role in options:
                self.view_role_combo.addItem(text_value, role)
            view_actions.addWidget(self.view_role_combo, 1)
            self.save_view_btn = QPushButton("保存当前图到槽位")
            self.save_view_btn.setToolTip(
                "使用选中的生成结果；没有候选时使用左侧当前参考图。不会替换现有主参考。")
            self.save_view_btn.clicked.connect(self._save_candidate_view)
            view_actions.addWidget(self.save_view_btn)
            rl.addLayout(view_actions)
        content.addWidget(right, 1)
        root.addLayout(content, 1)

        if not self.embedded:
            close_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            close_buttons.rejected.connect(self.accept)
            root.addWidget(close_buttons)

        self._generation_timer = QTimer(self)
        self._generation_timer.setInterval(400)
        self._generation_timer.timeout.connect(self._poll_generation)
        self._prompt_timer = QTimer(self)
        self._prompt_timer.setInterval(350)
        self._prompt_timer.timeout.connect(self._poll_prompt)
        self._update_approval_badge()
        self._refresh_references()
        self._refresh_providers()
        approved = approved_asset_path(self.item)
        existing_candidates = [
            path for path in getattr(self.item, "reference_images", []) or []
            if path and path != approved and os.path.exists(path)]
        if existing_candidates:
            self._candidate_paths = list(dict.fromkeys(existing_candidates))
            self._render_candidates()

    def _update_approval_badge(self):
        approved = asset_is_approved(self.item, require_file=False)
        version = max(1, int(getattr(self.item, "version", 0) or 0)) if approved else 0
        self.approval_badge.setText(f"● 主参考 v{version}" if approved else "○ 还没有主参考")
        self.approval_badge.setStyleSheet(
            "color:%s;font-size:11px;font-weight:bold;padding:4px 8px;"
            "background:#1b1b20;border-radius:5px;" %
            ("#67d8a2" if approved else "#d1a867"))

    def _reference_paths(self) -> list[str]:
        refs = []
        master = approved_asset_path(self.item)
        if master:
            refs.append(master)
        refs.extend(getattr(self.item, "reference_images", []) or [])
        for path in (getattr(self.item, "reference_views", {}) or {}).values():
            if path:
                refs.append(path)
        return [path for path in dict.fromkeys(refs)
                if path and os.path.exists(path)]

    def _refresh_references(self):
        while self.ref_layout.count() > 1:
            entry = self.ref_layout.takeAt(0)
            if entry.widget():
                entry.widget().deleteLater()
        refs = self._reference_paths()
        if self._working_reference not in refs:
            self._working_reference = refs[0] if refs else ""
        for path in refs:
            button = QToolButton()
            button.setIconSize(QSize(72, 64))
            button.setFixedSize(82, 76)
            pix = QPixmap(path)
            if not pix.isNull():
                button.setIcon(QIcon(_fit(pix, 72, 64)))
            button.setToolTip(path)
            button.setStyleSheet(
                "QToolButton{background:#202025;border:%s;border-radius:5px;}"
                "QToolButton:hover{border-color:#3d8ef8;}" %
                ("2px solid #3d8ef8" if path == self._working_reference
                 else "1px solid #34343c"))
            button.clicked.connect(lambda _=False, p=path: self._select_reference(p))
            self.ref_layout.insertWidget(self.ref_layout.count() - 1, button)

    def _select_reference(self, path: str):
        self._working_reference = path
        image_edit_index = self.mode_combo.findData("image_edit")
        if image_edit_index >= 0:
            self.mode_combo.setCurrentIndex(image_edit_index)
        self._refresh_references()
        self.generate_btn.setText("开始参考生图")
        self.status.setText(
            f"已选参考图：{os.path.basename(path)} · 将按参考图生成，不是文生图")

    def _upload_references(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "上传参考图", "",
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)")
        if not paths:
            return
        refs = list(getattr(self.item, "reference_images", []) or [])
        refs.extend(path for path in paths if path not in refs)
        self.item.reference_images = refs
        getattr(self.db, f"save_{self.kind}")(self.item)
        self.asset_saved = True
        # 上传就是明确的“我要用这张图继续生成”动作：自动选中首张并切到图生图，
        # 避免图片虽然已上传，任务却仍按默认文生图提交。
        self._select_reference(paths[0])
        self.status.setText(
            f"已上传 {len(paths)} 张参考图 · 已自动切换为参考生图")

    def _refresh_providers(self, *_):
        operation = self.mode_combo.currentData() or "text_to_image"
        current = self.provider_combo.currentData()
        self.provider_combo.clear()
        labels = {"seedream": "Seedream 5.0 Pro", "gptimage": "GPT-Image-2",
                  "flux": "FLUX"}
        try:
            providers = self.manager.registry.by_capability(operation)
        except Exception:
            providers = []
        for provider in providers:
            self.provider_combo.addItem(labels.get(provider.name, provider.name), provider.name)
        index = self.provider_combo.findData(current or "seedream")
        self.provider_combo.setCurrentIndex(index if index >= 0 else 0)

    def _assist_prompt(self):
        if self._prompt_handle is not None and not self._prompt_handle.is_finished:
            return
        providers = self.manager.registry.by_domain(ProviderDomain.LLM)
        provider = next((p for p in providers if p.name == "openai"),
                        providers[0] if providers else None)
        if provider is None:
            QMessageBox.information(
                self, "AI辅助 Prompt", "没有可用文本模型，请先在设置中配置 LLM。")
            return
        draft = self.prompt_edit.toPlainText().strip()
        description = getattr(self.item, "description", "") or ""
        kind_label = KIND_META.get(self.kind, {}).get("label", self.kind)
        system = (
            "你是影视视觉资产设定师。把用户的简短描述扩写为稳定、可复用的中文生图Prompt。"
            "必须明确主体/空间结构、固定材质、固定颜色、光线、镜头展示方式以及不可变化特征；"
            "避免空泛形容词，不写解释、标题或引号，只输出最终Prompt，控制在500字内。"
        )
        if self.kind == "scene":
            system += "这是场景母版：不要出现人物，重点固定空间布局、陈设、时间、主光方向和色调。"
        elif self.kind == "character":
            system += "这是主体母版：明确轮廓、器官数量、身体比例、服装、配件、纹理和配色。"
        else:
            system += "这是元素母版：明确形状、材质、文字/Logo位置、正视图和干净边缘。"
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content":
             f"资产类别：{kind_label}\n名称：{getattr(self.item, 'name', '')}"
             f"\n已有描述：{description}\n用户草稿：{draft}"},
        ]
        params = {}
        try:
            from config import LLM_MODEL_NAME
            if LLM_MODEL_NAME:
                params["model"] = LLM_MODEL_NAME
        except Exception:
            pass
        self._prompt_handle = self.manager.submit(
            provider.name, TaskRequest("chat", {"messages": messages}, params))
        self.prompt_assist_btn.setEnabled(False)
        self.prompt_assist_btn.setText("AI填写中…")
        self._prompt_timer.start()

    def _save_prompt(self):
        prompt = self.prompt_edit.toPlainText().strip()
        if not prompt:
            return
        if hasattr(self.item, "seedream_prompt"):
            self.item.seedream_prompt = prompt
        if not getattr(self.item, "description", ""):
            self.item.description = prompt
        getattr(self.db, f"save_{self.kind}")(self.item)
        self.asset_saved = True
        self.assetSaved.emit(self.kind, str(getattr(self.item, "id", "")))
        self.status.setText("✓ Prompt 已保存到当前资产")

    def _poll_prompt(self):
        handle = self._prompt_handle
        if handle is None or not handle.is_finished:
            return
        self._prompt_timer.stop()
        self.prompt_assist_btn.setEnabled(True)
        self.prompt_assist_btn.setText("✨ AI辅助填写")
        if handle.is_success and handle.result:
            text = str(handle.result.data or "").strip().strip('"').strip("'")
            if text:
                self.prompt_edit.setPlainText(text[:1000])
                self.status.setText("AI 已补全 Prompt，可继续手动修改后生成")
        else:
            error = handle.result.error if handle.result else "未知错误"
            QMessageBox.warning(self, "AI辅助失败", str(error))
        self._prompt_handle = None

    def _generate(self):
        if self._generation_handle is not None and not self._generation_handle.is_finished:
            return
        operation = self.mode_combo.currentData() or "text_to_image"
        prompt = self.prompt_edit.toPlainText().strip()
        if not prompt and operation == "image_edit":
            kind_label = KIND_META.get(self.kind, {}).get("label", "视觉资产")
            prompt = (
                f"以参考图为唯一视觉基准，保持{kind_label}的核心外观、结构、材质、"
                "配色和风格一致，生成一张高清、自然、可继续用于镜头制作的一致性变体。")
            self.prompt_edit.setPlainText(prompt)
            self.status.setText("未填写修改要求，已自动使用“保持参考图一致”的生成要求")
        elif not prompt:
            QMessageBox.information(self, "生成视觉资产", "请先填写 Prompt，或使用 AI辅助填写。")
            return
        if operation == "image_edit" and not self._working_reference:
            QMessageBox.information(self, "图生图", "请先上传并选择一张参考图。")
            return
        provider = self.provider_combo.currentData() or ""
        if not provider:
            QMessageBox.warning(self, "生成视觉资产", "没有支持当前生成方式的图片模型。")
            return
        inputs = {"prompt": prompt}
        if operation == "image_edit":
            refs = [self._working_reference] + [
                path for path in self._reference_paths() if path != self._working_reference]
            inputs.update({"image": refs[0], "images": refs[:9],
                           "style_images": refs[1:9]})
        from core.image_output_size import resolve_image_output_size
        size = resolve_image_output_size(
            provider, "2K", self.aspect_combo.currentData() or "original")
        request = TaskRequest(
            operation=operation, inputs=inputs,
            params={"size": size, "n": int(self.count_combo.currentData() or 4),
                    "quality": "high", "watermark": False},
            metadata={"resource_kind": self.kind, "resource_id": self.item.id},
            use_cache=False,
        )
        try:
            self._generation_handle = self.manager.submit(provider, request)
            self.generate_btn.setEnabled(False)
            self.generate_btn.setText("生成中…")
            self.progress.setValue(5)
            self.status.setText(
                f"正在{'图生图' if operation == 'image_edit' else '文生图'} · {provider}")
            self._generation_timer.start()
        except Exception as error:
            QMessageBox.warning(self, "提交失败", str(error))

    def _poll_generation(self):
        handle = self._generation_handle
        if handle is None:
            return
        self.progress.setValue(max(5, int(float(handle.progress or 0) * 100)))
        if not handle.is_finished:
            return
        self._generation_timer.stop()
        self.generate_btn.setEnabled(True)
        self.generate_btn.setText(
            "开始参考生图"
            if (self.mode_combo.currentData() or "text_to_image") == "image_edit"
            else "开始文生图")
        if handle.is_success and handle.result:
            data = handle.result.data
            values = list(data) if isinstance(data, (list, tuple)) else [data]
            self._candidate_paths = [
                path for path in (self._materialize(value) for value in values) if path]
            # 候选生成成功就写回资产草稿，关闭/重新打开窗口后仍能继续定稿；
            # 这里只保存候选，不会擅自把任何一张设为主参考。
            if self._candidate_paths:
                refs = list(getattr(self.item, "reference_images", []) or [])
                self.item.reference_images = list(dict.fromkeys(
                    refs + self._candidate_paths))
                getattr(self.db, f"save_{self.kind}")(self.item)
                self.asset_saved = True
                self.assetSaved.emit(
                    self.kind, str(getattr(self.item, "id", "")))
            self._selected_candidate = ""
            self._render_candidates()
            self.progress.setValue(100)
            self.status.setText(
                f"生成完成：{len(self._candidate_paths)} 张候选。选择一张点“使用这张”，或继续参考它生成。")
        else:
            error = handle.result.error if handle.result else "未知错误"
            self.status.setText(f"生成失败：{str(error)[:120]}")
            QMessageBox.warning(self, "生成失败", str(error))
        self._generation_handle = None

    @staticmethod
    def _materialize(value) -> str:
        if isinstance(value, Path):
            value = str(value)
        if isinstance(value, str) and os.path.exists(value):
            return value
        if isinstance(value, (bytes, bytearray)):
            try:
                from config import OUTPUT_DIR
                folder = Path(OUTPUT_DIR) / "ai_images"
            except Exception:
                folder = Path(__file__).parents[2] / "work_temp" / "ai_assets"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"resource_{uuid.uuid4().hex[:10]}.png"
            path.write_bytes(bytes(value))
            return str(path)
        return ""

    def _render_candidates(self):
        while self.candidate_grid.count():
            entry = self.candidate_grid.takeAt(0)
            if entry.widget():
                entry.widget().deleteLater()
        if not self._candidate_paths:
            self.remove_candidate_btn.setEnabled(False)
            self.finalize_clean_btn.setEnabled(False)
            empty = QLabel("候选图会显示在这里")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color:#62626c;font-size:13px;")
            self.candidate_grid.addWidget(empty, 0, 0)
            return
        for index, path in enumerate(self._candidate_paths):
            button = QToolButton()
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setIconSize(QSize(260, 180))
            button.setMinimumSize(280, 215)
            pix = QPixmap(path)
            if not pix.isNull():
                button.setIcon(QIcon(_fit(pix, 260, 180)))
            button.setText(f"候选 {index + 1}")
            button.setToolTip(path)
            selected = path == self._selected_candidate
            button.setStyleSheet(
                "QToolButton{background:#202025;color:#ddd;border:%s;border-radius:7px;"
                "padding:7px;}QToolButton:hover{border-color:#3d8ef8;}" %
                ("2px solid #3d8ef8" if selected else "1px solid #34343d"))
            button.clicked.connect(lambda _=False, p=path: self._select_candidate(p))
            self.candidate_grid.addWidget(button, index // 2, index % 2)

    def _select_candidate(self, path: str):
        self._selected_candidate = path
        self.selected_label.setText(Path(path).name)
        self.finalize_btn.setEnabled(True)
        self.add_ref_btn.setEnabled(True)
        self.continue_btn.setEnabled(True)
        self.remove_candidate_btn.setEnabled(True)
        self.finalize_clean_btn.setEnabled(True)
        self._render_candidates()

    def _remove_selected_candidate(self):
        path = self._selected_candidate
        if not path:
            return
        self._candidate_paths = [value for value in self._candidate_paths if value != path]
        self._selected_candidate = ""
        self.selected_label.setText("尚未选择候选")
        self.finalize_btn.setEnabled(False)
        self.add_ref_btn.setEnabled(False)
        self.continue_btn.setEnabled(False)
        self.remove_candidate_btn.setEnabled(False)
        self.finalize_clean_btn.setEnabled(False)
        if self._working_reference == path:
            refs = self._reference_paths()
            self._working_reference = refs[0] if refs else ""
        self._render_candidates()
        self.status.setText("已从候选列表移除；本地图片文件仍保留。")

    def _finalize_and_clean_candidates(self):
        path = self._selected_candidate
        if not path:
            return
        other_count = sum(1 for value in self._candidate_paths if value != path)
        if other_count:
            answer = QMessageBox.question(
                self, "使用并清理候选",
                f"要使用当前图作为主参考，并从列表移除其他 {other_count} 张候选吗？\n\n"
                "其他本地图片文件会保留。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._save_candidate(True)
        self._candidate_paths = [path]
        self._selected_candidate = path
        self._render_candidates()
        self.status.setText("✓ 已设为主参考并清理其他候选；本地文件仍保留。")

    def _save_candidate(self, as_master: bool):
        path = self._selected_candidate
        if not path or not os.path.exists(path):
            return
        refs = list(getattr(self.item, "reference_images", []) or [])
        refs = [ref for ref in refs if ref != path]
        changed = False
        if as_master:
            changed = approve_asset_version(
                self.item, path, source=f"resource_studio:{self.kind}")
        else:
            self.item.reference_images = refs + [path]
        prompt = self.prompt_edit.toPlainText().strip()
        if hasattr(self.item, "seedream_prompt"):
            self.item.seedream_prompt = prompt
        if not getattr(self.item, "description", ""):
            self.item.description = prompt
        getattr(self.db, f"save_{self.kind}")(self.item)
        self.asset_saved = True
        self.assetSaved.emit(self.kind, str(getattr(self.item, "id", "")))
        if as_master:
            self._working_reference = path
        self._update_approval_badge()
        self._refresh_references()
        if as_master:
            version = max(1, int(getattr(self.item, "version", 0) or 0))
            self.status.setText(
                f"✓ 已使用这张作为主参考 v{version}"
                if changed else f"✓ 当前图片已经是主参考 v{version}")
        else:
            self.status.setText("✓ 已追加为补充参考，现有主参考保持不变")

    def _save_candidate_view(self):
        path = self._selected_candidate or self._working_reference
        if not path or not os.path.exists(path):
            self.status.setText("请先选择一张候选图或左侧参考图")
            return
        role = self.view_role_combo.currentData() or ""
        try:
            assign_asset_view(self.item, role, path)
            getattr(self.db, f"save_{self.kind}")(self.item)
        except Exception as error:
            self.status.setText(f"保存固定视角失败：{error}")
            return
        self.asset_saved = True
        self.assetSaved.emit(self.kind, str(getattr(self.item, "id", "")))
        self._working_reference = path
        self._refresh_references()
        self.status.setText(
            f"✓ 已保存到“{self.view_role_combo.currentText()}”槽位；主参考未改变")

    def _continue_from_candidate(self):
        if not self._selected_candidate:
            return
        self._working_reference = self._selected_candidate
        index = self.mode_combo.findData("image_edit")
        self.mode_combo.setCurrentIndex(index if index >= 0 else 0)
        self.status.setText("已把选中候选设为图生图输入，可修改 Prompt 后继续生成")


class ShotAssetPreparationDialog(QDialog):
    """把单镜头所需的多个资产制作台合并到一个可确认的标签页窗口。"""

    assetSaved = pyqtSignal(str, str)

    def __init__(self, shot_number: int | None, entries: list[dict], db,
                 aspect: str = "9:16", ready_count: int = 0, parent=None,
                 context_title: str = ""):
        super().__init__(parent)
        self.entries = list(entries)
        self.db = db
        self._studios: list[tuple[AssetStudioDialog, dict]] = []
        self._pending_launches = 0
        context = context_title or (
            f"镜头 {int(shot_number or 0):02d}" if shot_number is not None
            else "全部镜头")
        self.setWindowTitle(f"{context} · 确认并准备素材")
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowMinimizeButtonHint |
            Qt.WindowType.WindowMaximizeButtonHint |
            Qt.WindowType.WindowCloseButtonHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(1180, 860)
        self.setStyleSheet(
            "QDialog{background:#111114;color:#eee;}"
            "QTabWidget::pane{border:1px solid #303038;background:#121214;}"
            "QTabBar::tab{background:#202025;color:#999;padding:8px 16px;}"
            "QTabBar::tab:selected{background:#30294a;color:#fff;}"
            "QPushButton{background:#25252c;color:#ddd;border:1px solid #3a3a44;"
            "border-radius:5px;padding:8px 12px;}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        pending_count = sum(1 for entry in self.entries if entry.get("generate"))
        review_count = len(self.entries) - pending_count
        title = QLabel("先检查每个标签页的 Prompt、模型、比例和候选数量，再确认生成")
        title.setStyleSheet("color:#fff;font-size:15px;font-weight:bold;")
        root.addWidget(title)
        summary = QLabel(
            f"已识别 {ready_count + len(self.entries)} 项素材："
            f"跳过已定稿 {ready_count} 项 · 待生成 {pending_count} 项 · "
            f"已有候选待定稿 {review_count} 项。默认每项 2 张，可在标签页单独修改。")
        summary.setWordWrap(True)
        summary.setStyleSheet(
            "color:#aeb4c0;background:#19191e;border:1px solid #2d2d35;"
            "border-radius:6px;padding:8px;")
        root.addWidget(summary)

        global_settings = QHBoxLayout()
        global_settings.addWidget(QLabel("统一候选数量"))
        self.global_count_combo = QComboBox()
        for count in (1, 2, 4, 6):
            self.global_count_combo.addItem(f"每项 {count} 张", count)
        self.global_count_combo.setCurrentIndex(
            self.global_count_combo.findData(2))
        self.global_count_combo.setToolTip(
            "先统一设置全部标签页；之后仍可在单个标签页中单独调整")
        global_settings.addWidget(self.global_count_combo)
        global_settings.addWidget(QLabel("确认前不会调用图片接口"))
        global_settings.addStretch()
        root.addLayout(global_settings)

        self.tabs = QTabWidget()
        labels = {"scene": "场景", "character": "主体", "element": "元素"}
        for entry in self.entries:
            item = entry["item"]
            kind = entry["kind"]
            studio = AssetStudioDialog(item, kind, db, self, embedded=True)
            count_index = studio.count_combo.findData(2)
            if count_index >= 0:
                studio.count_combo.setCurrentIndex(count_index)
            target_aspect = "1:1" if kind == "element" else aspect
            aspect_index = studio.aspect_combo.findData(target_aspect)
            if aspect_index >= 0:
                studio.aspect_combo.setCurrentIndex(aspect_index)
            studio.assetSaved.connect(self._on_asset_saved)
            state = "待生成" if entry.get("generate") else "待定稿"
            self.tabs.addTab(
                studio, f"{labels.get(kind, kind)} · {getattr(item, 'name', '未命名')} · {state}")
            self._studios.append((studio, entry))
        root.addWidget(self.tabs, 1)
        self.global_count_combo.currentIndexChanged.connect(
            self._apply_global_count)

        actions = QHBoxLayout()
        hint = QLabel("点击确认前不会产生图片请求；生成中可以最小化此窗口。")
        hint.setStyleSheet("color:#777d8a;font-size:11px;")
        actions.addWidget(hint)
        actions.addStretch()
        self.start_btn = QPushButton(
            f"确认并开始生成 {pending_count} 项" if pending_count
            else "已有候选，请逐项选择主参考")
        self.start_btn.setEnabled(bool(pending_count))
        self.start_btn.setStyleSheet(
            "QPushButton{background:#2867bd;color:white;border:none;border-radius:6px;"
            "font-weight:bold;padding:10px 18px;}"
            "QPushButton:hover{background:#3479d1;}"
            "QPushButton:disabled{background:#252934;color:#767b88;}")
        self.start_btn.clicked.connect(self._start_missing)
        actions.addWidget(self.start_btn)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        actions.addWidget(close_btn)
        root.addLayout(actions)

    def _apply_global_count(self):
        count = int(self.global_count_combo.currentData() or 2)
        for studio, _entry in self._studios:
            index = studio.count_combo.findData(count)
            if index >= 0:
                studio.count_combo.setCurrentIndex(index)

    def _start_missing(self):
        pending = [(studio, entry) for studio, entry in self._studios
                   if entry.get("generate")]
        if not pending:
            return
        self.start_btn.setEnabled(False)
        self.start_btn.setText(f"已提交 {len(pending)} 项生成")
        self._pending_launches = len(pending)
        for index, (studio, entry) in enumerate(pending):
            entry["generate"] = False
            tab_index = self.tabs.indexOf(studio)
            if tab_index >= 0:
                self.tabs.setTabText(
                    tab_index, self.tabs.tabText(tab_index).replace("待生成", "生成中"))
            QTimer.singleShot(
                index * 80, lambda target=studio: self._launch_studio(target))

    def _launch_studio(self, studio: AssetStudioDialog):
        studio._generate()
        self._pending_launches = max(0, self._pending_launches - 1)

    def _on_asset_saved(self, kind: str, asset_id: str):
        for studio, _entry in self._studios:
            if str(getattr(studio.item, "id", "")) != asset_id:
                continue
            tab_index = self.tabs.indexOf(studio)
            if tab_index >= 0 and asset_is_approved(studio.item, require_file=True):
                text = self.tabs.tabText(tab_index)
                text = re.sub(r" · (待生成|生成中|待定稿|已定稿)$", "", text)
                self.tabs.setTabText(tab_index, text + " · 已定稿 ✓")
            break
        self.assetSaved.emit(kind, asset_id)

    def closeEvent(self, event):
        running = self._pending_launches > 0 or any(
            studio._generation_handle is not None and
            not studio._generation_handle.is_finished
            for studio, _entry in self._studios)
        if running:
            QMessageBox.information(
                self, "素材仍在生成",
                "请等待当前素材生成完成后再关闭。你可以先最小化这个合并窗口，"
                "主工作台仍可继续使用。")
            event.ignore()
            return
        super().closeEvent(event)


class AssetImportDialog(QDialog):
    """把图片工作台或分镜结果导入/追加到角色、场景资源。"""

    ENTITY_TYPES = [
        ("人类", "human"), ("动物", "animal"), ("怪物 / 生物", "monster"),
        ("机器人 / 机械", "robot"), ("拟人物体", "object"), ("其他", "other"),
    ]
    CHARACTER_VIEW_ROLES = [
        ("普通参考", "reference"),
        ("角色立绘 / 全身", "portrait"),
        ("脸部近景", "face_closeup"),
        ("表情参考表", "expression_sheet"),
        ("完整三视图", "three_view_sheet"),
        ("正面", "front"), ("侧面", "side"),
        ("背面", "back"), ("3/4 视角", "three_quarter"),
    ]
    SCENE_VIEW_ROLES = [
        ("普通参考", "reference"), ("无人空场", "empty_plate"),
        ("A 机位", "camera_a"), ("B 机位", "camera_b"),
        ("A 反打", "reverse_a"), ("B 反打", "reverse_b"),
        ("空间全景", "wide_master"), ("环境细节", "detail"),
    ]
    VIEW_ROLES = CHARACTER_VIEW_ROLES

    def __init__(self, paths: list[str], default_kind="character", existing_id="", parent=None):
        super().__init__(parent)
        self.db = get_asset_db()
        self.paths = [os.path.abspath(path) for path in dict.fromkeys(paths)
                      if path and os.path.exists(path)]
        self.saved_kind = ""
        self.saved_items = []
        self._existing_id = existing_id
        self._view_combos = []
        self.setWindowTitle("保存到 AI 资产库")
        self.setMinimumSize(620, 560)
        self.setStyleSheet(
            "QDialog{background:#151518;color:#eee;} QLabel{color:#aaa;}"
            "QLineEdit,QTextEdit,QComboBox{background:#202025;color:#eee;"
            "border:1px solid #383840;border-radius:5px;padding:6px;}"
            "QPushButton{background:#27272d;color:#ddd;border:1px solid #414149;"
            "border-radius:5px;padding:6px 10px;}"
            "QPushButton:hover{background:#33333b;color:#fff;}"
            "QPushButton:disabled{background:#202023;color:#666;border-color:#303035;}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(9)

        title = QLabel(f"将 {len(self.paths)} 张图片保存到 AI 资产库")
        title.setStyleSheet("color:#fff;font-size:15px;font-weight:bold;")
        root.addWidget(title)

        top = QHBoxLayout()
        top.addWidget(QLabel("资产类别"))
        self.kind_combo = QComboBox()
        self.kind_combo.addItem("角色（人物 / 动物 / 怪物 / 机器人）", "character")
        self.kind_combo.addItem("场景 / 环境", "scene")
        self.kind_combo.addItem("必须出现的元素（壁纸 / Logo / UI / 产品）", "element")
        self.kind_combo.setCurrentIndex(max(0, self.kind_combo.findData(default_kind)))
        top.addWidget(self.kind_combo, 1)
        top.addWidget(QLabel("导入方式"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("作为同一资产的多张参考 / 三视图", "same")
        self.mode_combo.addItem("每张图片分别新建一个资产", "separate")
        self.mode_combo.setEnabled(len(self.paths) > 1)
        top.addWidget(self.mode_combo, 1)
        root.addLayout(top)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("保存到"))
        self.target_combo = QComboBox()
        target_row.addWidget(self.target_combo, 1)
        root.addLayout(target_row)

        form = QFormLayout()
        self.name_edit = QLineEdit(Path(self.paths[0]).stem if self.paths else "")
        form.addRow("名称 / 名称前缀", self.name_edit)
        self.entity_combo = QComboBox()
        for text, value in self.ENTITY_TYPES:
            self.entity_combo.addItem(text, value)
        form.addRow("角色类型", self.entity_combo)
        self.entity_label = form.labelForField(self.entity_combo)
        self.life_stage_edit = QLineEdit()
        self.life_stage_edit.setPlaceholderText("可留空，例如：幼年、成年、古老、不适用")
        form.addRow("生命阶段（可选）", self.life_stage_edit)
        self.life_stage_label = form.labelForField(self.life_stage_edit)
        self.element_type_combo = QComboBox()
        for text, value in (
                ("手机 / 电脑壁纸", "wallpaper"), ("Logo", "logo"),
                ("App / UI界面", "ui"), ("产品 / 包装", "product"),
                ("普通道具", "prop"), ("贴纸 / 海报", "sticker"), ("其他", "other")):
            self.element_type_combo.addItem(text, value)
        form.addRow("元素类型", self.element_type_combo)
        self.element_type_label = form.labelForField(self.element_type_combo)
        self.placement_edit = QLineEdit()
        self.placement_edit.setPlaceholderText("例如：手机屏幕四角区域、桌面中央、墙上画框")
        form.addRow("放置说明", self.placement_edit)
        self.placement_label = form.labelForField(self.placement_edit)
        self.description_edit = QTextEdit()
        self.description_edit.setMaximumHeight(74)
        self.description_edit.setPlaceholderText(
            "写清不可变化的轮廓、材质、颜色、服装、角、翅膀、纹理等识别特征")
        form.addRow("固定设计描述", self.description_edit)
        root.addLayout(form)

        image_head = QHBoxLayout()
        self.image_title = QLabel("图片视角归类（同一资产模式下生效）")
        self.image_title.setStyleSheet("color:#d4c9f5;font-weight:bold;")
        image_head.addWidget(self.image_title)
        image_head.addStretch()
        self.assign_views_btn = QPushButton("前三张按 正 / 侧 / 背 标记")
        self.assign_views_btn.setEnabled(len(self.paths) >= 3)
        self.assign_views_btn.clicked.connect(self._assign_three_views)
        image_head.addWidget(self.assign_views_btn)
        root.addLayout(image_head)
        image_scroll = QScrollArea()
        image_scroll.setWidgetResizable(True)
        image_scroll.setFrameShape(QFrame.Shape.NoFrame)
        image_scroll.setMaximumHeight(230)
        image_scroll.setStyleSheet(
            "QScrollArea{background:#101013;border:1px solid #292930;border-radius:6px;}"
            "QScrollArea QWidget{background:#101013;}"
        )
        image_body = QWidget()
        image_layout = QVBoxLayout(image_body)
        image_layout.setContentsMargins(0, 0, 0, 0)
        for index, path in enumerate(self.paths):
            row = QHBoxLayout()
            thumb = QLabel()
            thumb.setFixedSize(64, 52)
            thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pix = QPixmap(path)
            if not pix.isNull():
                thumb.setPixmap(_fit(pix, 64, 52))
            row.addWidget(thumb)
            name = QLabel(Path(path).name)
            name.setToolTip(path)
            row.addWidget(name, 1)
            view = QComboBox()
            for text, value in self.VIEW_ROLES:
                view.addItem(text, value)
            self._view_combos.append(view)
            row.addWidget(view)
            image_layout.addLayout(row)
        image_layout.addStretch()
        image_scroll.setWidget(image_body)
        root.addWidget(image_scroll)

        self.import_note = QLabel(
            "新资产会自动使用第一张作为主参考；追加到已有资产时不会覆盖现有主参考。")
        self.import_note.setWordWrap(True)
        self.import_note.setStyleSheet("color:#7f8490;font-size:11px;")
        root.addWidget(self.import_note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("导入并使用")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.kind_combo.currentIndexChanged.connect(self._refresh_targets)
        self.target_combo.currentIndexChanged.connect(self._load_target)
        self.mode_combo.currentIndexChanged.connect(self._sync_mode)
        self._refresh_targets()

    def _refresh_targets(self, *_):
        kind = self.kind_combo.currentData()
        role_options = (self.CHARACTER_VIEW_ROLES if kind == "character" else
                        self.SCENE_VIEW_ROLES if kind == "scene" else
                        [("普通参考", "reference")])
        for combo in self._view_combos:
            saved_role = combo.currentData() or "reference"
            combo.blockSignals(True)
            combo.clear()
            for text, value in role_options:
                combo.addItem(text, value)
            combo.setCurrentIndex(max(0, combo.findData(saved_role)))
            combo.blockSignals(False)
        if kind == "character":
            items = self.db.list_characters(limit=5000)
        elif kind == "scene":
            items = self.db.list_scenes(limit=5000)
        else:
            items = self.db.list_elements(limit=5000)
        self.target_combo.blockSignals(True)
        self.target_combo.clear()
        self.target_combo.addItem("＋ 新建资产", "")
        for item in items:
            self.target_combo.addItem(getattr(item, "name", "未命名"), item.id)
        index = self.target_combo.findData(self._existing_id)
        self.target_combo.setCurrentIndex(index if index >= 0 else 0)
        self.target_combo.blockSignals(False)
        is_character = kind == "character"
        is_element = kind == "element"
        self.entity_combo.setVisible(is_character)
        self.entity_label.setVisible(is_character)
        self.life_stage_edit.setVisible(is_character)
        self.life_stage_label.setVisible(is_character)
        self.element_type_combo.setVisible(is_element)
        self.element_type_label.setVisible(is_element)
        self.placement_edit.setVisible(is_element)
        self.placement_label.setVisible(is_element)
        self.assign_views_btn.setVisible(is_character)
        for combo in self._view_combos:
            combo.setVisible(is_character or kind == "scene")
        if is_character:
            self.image_title.setText("图片视角归类（同一资产模式下生效）")
            self.import_note.setText(
                "第一张会自动成为新角色的主参考；其余图片可标记为正面、侧面或背面。")
        elif is_element:
            self.image_title.setText("元素主参考与补充图片")
            self.import_note.setText(
                "第一张会自动成为新元素的主参考；精确植入时 AI 不会重绘其中的文字、Logo 或 UI。")
        else:
            self.image_title.setText("场景参考图与固定机位归类")
            self.import_note.setText(
                "第一张会自动成为新场景的主参考；其余图片可标记为空场、A/B机位、反打或空间全景。")
        self._load_target()

    def _load_target(self, *_):
        item_id = self.target_combo.currentData() or ""
        if not item_id:
            return
        kind = self.kind_combo.currentData()
        item = getattr(self.db, f"get_{kind}")(item_id)
        if not item:
            return
        self.name_edit.setText(getattr(item, "name", ""))
        self.description_edit.setPlainText(getattr(item, "description", ""))
        if kind == "character":
            index = self.entity_combo.findData(getattr(item, "entity_type", "human"))
            self.entity_combo.setCurrentIndex(index if index >= 0 else 0)
            self.life_stage_edit.setText(getattr(item, "life_stage", ""))
        elif kind == "element":
            index = self.element_type_combo.findData(
                getattr(item, "element_type", "wallpaper"))
            self.element_type_combo.setCurrentIndex(index if index >= 0 else 0)
            self.placement_edit.setText(getattr(item, "placement_hint", ""))

    def _sync_mode(self, *_):
        separate = self.mode_combo.currentData() == "separate"
        self.target_combo.setEnabled(not separate)
        for combo in self._view_combos:
            combo.setEnabled(not separate)
        self.assign_views_btn.setEnabled(not separate and len(self.paths) >= 3)

    def _assign_three_views(self):
        for combo, role in zip(self._view_combos[:3], ("front", "side", "back")):
            index = combo.findData(role)
            if index >= 0:
                combo.setCurrentIndex(index)

    def _save(self):
        if not self.paths:
            return
        kind = self.kind_combo.currentData()
        separate = self.mode_combo.currentData() == "separate" and len(self.paths) > 1
        default_name = {"character": "未命名角色", "scene": "未命名场景",
                        "element": "未命名元素"}[kind]
        base_name = self.name_edit.text().strip() or default_name
        description = self.description_edit.toPlainText().strip()
        saved = []

        if separate:
            for index, path in enumerate(self.paths, 1):
                name = f"{base_name} {index:02d}" if len(self.paths) > 1 else base_name
                if kind == "character":
                    item = Character(
                        id=uuid.uuid4().hex, name=name,
                        entity_type=self.entity_combo.currentData() or "other",
                        life_stage=self.life_stage_edit.text().strip(),
                        description=description, design_notes=description,
                        seedream_prompt=description, reference_images=[path])
                    approve_asset_version(item, path, source="import:auto_first")
                    self.db.save_character(item)
                elif kind == "scene":
                    item = Scene(id=uuid.uuid4().hex, name=name,
                                 description=description, seedream_prompt=description,
                                 reference_images=[path])
                    approve_asset_version(item, path, source="import:auto_first")
                    self.db.save_scene(item)
                else:
                    item = Element(
                        id=uuid.uuid4().hex, name=name,
                        element_type=self.element_type_combo.currentData() or "other",
                        description=description, seedream_prompt=description,
                        reference_images=[path],
                        placement_hint=self.placement_edit.text().strip(),
                        default_mode="exact")
                    approve_asset_version(item, path, source="import:auto_first")
                    self.db.save_element(item)
                saved.append(item)
        else:
            item_id = self.target_combo.currentData() or ""
            item = getattr(self.db, f"get_{kind}")(item_id) if item_id else None
            if kind == "character":
                item = item or Character(id=uuid.uuid4().hex)
                item.name = base_name
                item.entity_type = self.entity_combo.currentData() or "other"
                item.life_stage = self.life_stage_edit.text().strip()
                if description:
                    item.description = description
                    item.design_notes = description
                    item.seedream_prompt = description
                refs = list(item.reference_images or [])
                views = dict(item.reference_views or {})
                for path, combo in zip(self.paths, self._view_combos):
                    if path not in refs:
                        refs.append(path)
                    role = combo.currentData()
                    if role != "reference":
                        views[role] = path
                item.reference_images = refs
                item.reference_views = views
                if not asset_is_approved(item, require_file=False):
                    approve_asset_version(
                        item, self.paths[0], source="import:auto_first")
                self.db.save_character(item)
            elif kind == "scene":
                item = item or Scene(id=uuid.uuid4().hex)
                item.name = base_name
                if description:
                    item.description = description
                    item.seedream_prompt = description
                refs = list(item.reference_images or [])
                refs.extend(path for path in self.paths if path not in refs)
                item.reference_images = refs
                views = dict(item.reference_views or {})
                for path, combo in zip(self.paths, self._view_combos):
                    role = combo.currentData()
                    if role != "reference":
                        views[role] = path
                item.reference_views = views
                if not asset_is_approved(item, require_file=False):
                    approve_asset_version(
                        item, self.paths[0], source="import:auto_first")
                self.db.save_scene(item)
            else:
                item = item or Element(id=uuid.uuid4().hex)
                item.name = base_name
                item.element_type = self.element_type_combo.currentData() or "other"
                item.description = description or item.description
                item.seedream_prompt = description or getattr(item, "seedream_prompt", "")
                item.placement_hint = self.placement_edit.text().strip()
                refs = list(item.reference_images or [])
                refs.extend(path for path in self.paths if path not in refs)
                item.reference_images = refs
                if not asset_is_approved(item, require_file=False):
                    approve_asset_version(
                        item, self.paths[0], source="import:auto_first")
                self.db.save_element(item)
            saved.append(item)

        self.saved_kind = kind
        self.saved_items = saved
        self.accept()


def import_assets_to_resource_center(parent, paths: list[str], default_kind="character",
                                     existing_id="", force_same=False):
    dialog = AssetImportDialog(paths, default_kind=default_kind,
                               existing_id=existing_id, parent=parent)
    if force_same and len(dialog.paths) > 1:
        same_index = dialog.mode_combo.findData("same")
        dialog.mode_combo.setCurrentIndex(max(0, same_index))
        dialog.mode_combo.setEnabled(False)
        dialog.mode_combo.setToolTip("从画布执行“合并保存”时，所有图片固定归入同一资产")
        dialog._sync_mode()
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.saved_kind, dialog.saved_items
    return None


# ── 主 Tab ──
class ResourceCenterTab(QWidget):
    assetChanged = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ResourceCenterRoot")
        self.db = get_asset_db()
        self._asset_studios: dict[str, AssetStudioDialog] = {}
        self._kind = "character"
        self._clipboard = None
        self.setStyleSheet(BASE_QSS)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._sidebar = SidebarNav()
        self._sidebar.categoryChanged.connect(self._set_kind)
        self._sidebar.filterChanged.connect(self._on_filter)
        lay.addWidget(self._sidebar)

        self._canvas = MainCanvas(self.db)
        self._canvas.itemSelected.connect(self._on_pick)
        self._canvas.duplicate.connect(self._on_dup)
        self._canvas.deleteCurrent.connect(self._on_del)
        self._canvas.toggleInspector.connect(self._toggle_inspector)
        lay.addWidget(self._canvas, 1)

        self._inspector = PropertyInspector(self.db)
        self._inspector.saved.connect(self._on_saved)
        self._inspector.studioRequested.connect(self._open_asset_studio)
        self._inspector.set_visible(False)
        lay.addWidget(self._inspector)

        self._set_kind("character")

    def _set_kind(self, kind):
        self._kind = kind
        accent = KIND_META[kind]["accent"]
        self._sidebar.set_kind(kind)
        self._canvas.set_kind(kind, accent)
        self._reload_browser()
        self._inspector.set_visible(False)
        self._canvas.set_inspector_btn(False)

    def _reload_browser(self):
        list_fn = getattr(self.db, DB_MAP[self._kind][0])
        items = list_fn(limit=5000)
        self._canvas.load(items)
        tags = []
        for it in items:
            tags.extend(getattr(it, "tags", []))
        self._sidebar.set_tags(tags)

    def _on_filter(self):
        tags, provs = self._sidebar.current_filter()
        self._canvas.set_tag_filter(tags, provs)

    def _on_pick(self, cid):
        item = getattr(self.db, "get_" + self._kind)(cid)
        if item:
            if not self._inspector.isVisible():
                self._show_inspector(True)
            self._inspector.load(item, self._kind, KIND_META[self._kind]["accent"])

    def _on_dup(self):
        cur = self._canvas._selected_id
        if not cur:
            return
        item = getattr(self.db, "get_" + self._kind)(cur)
        if not item:
            return
        item.id = uuid.uuid4().hex
        item.name = (getattr(item, "name", "") or "") + " (副本)"
        item.created_at = 0.0
        item.updated_at = 0.0
        getattr(self.db, DB_MAP[self._kind][1])(item)
        self._reload_browser()
        self._canvas.mark_selected(item.id)
        self._on_pick(item.id)

    def _on_del(self):
        cur = self._canvas._selected_id
        if not cur:
            return
        getattr(self.db, DB_MAP[self._kind][2])(cur)
        self._reload_browser()
        self._inspector.set_visible(False)
        self.assetChanged.emit(self._kind)

    def _on_saved(self, item):
        self._reload_browser()
        self._canvas.mark_selected(item.id)
        self.assetChanged.emit(self._kind)

    def _open_asset_studio(self, item, kind: str):
        # 资产库内也不再跳转完整制作台，就地使用轻量检查器。
        self._show_inspector(True)
        self._inspector.load(item, kind, KIND_META[kind]["accent"])
        if not self._inspector._edit:
            self._inspector._toggle_edit()

    def _toggle_inspector(self):
        self._show_inspector(not self._inspector.isVisible())

    def _show_inspector(self, on):
        self._inspector.set_visible(on)
        self._canvas.set_inspector_btn(on)
        if on and not self._inspector._current_id:
            self._inspector.show_hint("在中间选择资产查看详情。新资产请先在 AI 制片画布创作。")

    def showEvent(self, event):
        super().showEvent(event)
        self._reload_browser()

    # ── 键盘快捷流 ──
    def keyPressEvent(self, ev):
        fw = self.focusWidget()
        in_text = isinstance(fw, (QLineEdit, QTextEdit, QSpinBox))
        if ev.key() == Qt.Key.Key_Space and self._canvas._selected_id and not in_text:
            item = getattr(self.db, "get_" + self._kind)(self._canvas._selected_id)
            imgs = getattr(item, "reference_images", []) if item else []
            if imgs and os.path.exists(imgs[0]):
                Lightbox(QPixmap(imgs[0]), self).exec()
        elif ev.key() == Qt.Key.Key_C and ev.modifiers() & Qt.KeyboardModifier.ControlModifier and \
                self._canvas._selected_id and not in_text:
            item = getattr(self.db, "get_" + self._kind)(self._canvas._selected_id)
            if item:
                self._clipboard = copy.deepcopy(item)
                self._clipboard.id = ""
        elif ev.key() == Qt.Key.Key_V and ev.modifiers() & Qt.KeyboardModifier.ControlModifier and \
                self._clipboard and not in_text:
            item = copy.deepcopy(self._clipboard)
            item.id = uuid.uuid4().hex
            item.name = (getattr(item, "name", "") or "") + " (副本)"
            getattr(self.db, DB_MAP[self._kind][1])(item)
            self._reload_browser()
            self._canvas.mark_selected(item.id)
            self._on_pick(item.id)
        else:
            super().keyPressEvent(ev)
