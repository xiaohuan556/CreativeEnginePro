"""Compact native controls shared by the creative workbenches."""

from functools import lru_cache
from pathlib import Path

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QScrollArea, QWidget


_ICON_DIR = Path(__file__).resolve().parents[2] / "assets" / "lucide"
CUT_ACCENT = "#65def1"
IMAGE_ACCENT = "#c3aaff"
AI_ACCENT = "#91bcff"


@lru_cache(maxsize=128)
def workbench_icon(name, color="#dce4f4", size=16):
    """Render a bundled Lucide SVG in the workbench's foreground color."""
    svg = (_ICON_DIR / f"{name}.svg").read_bytes()
    svg = svg.replace(b'currentColor', QColor(color).name().encode("ascii"))
    renderer = QSvgRenderer(svg)
    icon = QIcon()
    for scale in (1, 2):
        extent = size * scale
        pixmap = QPixmap(extent, extent)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, extent, extent))
        painter.end()
        pixmap.setDevicePixelRatio(scale)
        icon.addPixmap(pixmap)
    return icon


def workbench_button(text, icon_name, tooltip="", *, accent=AI_ACCENT, quiet=False, primary=False):
    button = QPushButton(text)
    if icon_name:
        button.setIcon(workbench_icon(icon_name, "#161b24" if primary else "#dce4f4"))
        button.setIconSize(QSize(16, 16))
    button.setAccessibleName(text or tooltip)
    button.setToolTip(tooltip)
    background = accent if primary else "transparent" if quiet else "#272a32"
    foreground = "#161b24" if primary else "#ecedf3"
    hover = QColor(accent).lighter(110).name() if primary else "#343844"
    pressed = QColor(accent).darker(115).name() if primary else "#20242b"
    padding = "3px 7px" if text else "3px"
    checked_background = QColor(accent).darker(430).name()
    button.setStyleSheet(
        f"QPushButton{{background:{background};color:{foreground};border:1px solid transparent;"
        f"border-radius:7px;padding:{padding};font-size:12px;}}"
        f"QPushButton:hover{{background:{hover};}}"
        f"QPushButton:checked{{background:{checked_background};border-color:{accent};color:{accent};}}"
        f"QPushButton:pressed{{background:{pressed};}}"
        f"QPushButton:focus{{border-color:{'#edf4ff' if primary else accent};}}"
        "QPushButton:disabled{background:transparent;border-color:transparent;color:#777e8d;}"
    )
    return button


def set_workbench_playback(button, playing, idle_text="播放"):
    label = "暂停" if playing else idle_text
    button.setText(label)
    button.setAccessibleName(label)
    button.setIcon(workbench_icon("pause" if playing else "play"))


def workbench_field_style(accent):
    return (
        "QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox{background:#20232b;color:#ecedf3;"
        "border:1px solid #3b404d;border-radius:6px;padding:3px 6px;}"
        "QLineEdit:hover,QComboBox:hover,QSpinBox:hover,QDoubleSpinBox:hover{border-color:#535a6b;}"
        f"QLineEdit:focus,QComboBox:focus,QSpinBox:focus,QDoubleSpinBox:focus{{border-color:{accent};}}"
        "QLineEdit:disabled,QComboBox:disabled,QSpinBox:disabled,QDoubleSpinBox:disabled{"
        "background:#1a1c22;color:#777e8d;}"
        "QComboBox QAbstractItemView{background:#272a32;color:#ecedf3;selection-background-color:#3c4c70;}"
    )


def workbench_tabs_style(accent):
    return (
        "QTabWidget::pane{border:0;border-top:1px solid #30323b;}"
        "QTabBar::tab{background:#1c1e23;color:#a8abb8;border:1px solid transparent;"
        "border-radius:6px;padding:5px 7px;margin:3px 1px;font-size:12px;}"
        "QTabBar::tab:hover{background:#2b2e38;color:#ecedf3;}"
        f"QTabBar::tab:selected{{background:#2b2e38;color:{accent};border-color:#414654;}}"
        f"QTabBar::tab:focus{{border-color:{accent};}}"
    )


def workbench_horizontal_strip(content):
    """Keep a row of tools reachable when the workspace is narrower than the row."""
    strip = QScrollArea()
    strip.setObjectName("workbenchHorizontalStrip")
    strip.setWidget(content)
    strip.setWidgetResizable(True)
    strip.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    strip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    strip.setFixedHeight(content.minimumHeight() + 8)
    strip.setStyleSheet(
        "QScrollArea#workbenchHorizontalStrip{border:0;background:#1c1e23;}"
        "QScrollBar:horizontal{height:8px;background:#17191e;}"
        "QScrollBar::handle:horizontal{background:#535a6b;border-radius:4px;min-width:20px;}"
        "QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{width:0;}"
    )
    return strip


def workbench_section_heading(title, icon_name, accent):
    heading = QWidget()
    row = QHBoxLayout(heading)
    row.setContentsMargins(0, 2, 0, 2)
    row.setSpacing(8)
    icon = QLabel()
    icon.setPixmap(workbench_icon(icon_name, accent).pixmap(16, 16))
    row.addWidget(icon)
    text = QLabel(title)
    text.setStyleSheet("color:#ecedf3;font-size:14px;font-weight:600;")
    row.addWidget(text, 1)
    return heading


def batch_options_scroll(content):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setMinimumWidth(content.minimumSizeHint().width() + 8)
    scroll.setWidget(content)
    return scroll


def batch_workbench_style(accent):
    selection = QColor(accent).darker(350).name()
    chevron = (_ICON_DIR.parent / "batch-combo-chevron.svg").as_posix()
    return (
        "QWidget#batchWorkbench{background:#181a20;}"
        "QLabel,QCheckBox,QRadioButton{background:transparent;color:#d5d9e3;}"
        "QGroupBox{background:#20232b;color:#ecedf3;border:1px solid #383e4b;"
        "border-radius:8px;margin-top:10px;padding:7px 4px 3px;font-weight:600;}"
        "QGroupBox::title{subcontrol-origin:margin;left:9px;padding:0 4px;}"
        "QTableWidget,QListWidget{background:#1a1d24;alternate-background-color:#1e2129;"
        "border:1px solid #343946;border-radius:7px;color:#e2e7f1;gridline-color:#2b303a;"
        f"selection-background-color:{selection};selection-color:#f1f4fa;}}"
        "QHeaderView::section{background:#262a34;color:#bdc4d4;border:0;"
        "border-bottom:1px solid #3a404e;padding:5px 7px;font-size:11px;}"
        "QTableCornerButton::section{background:#262a34;border:0;}"
        "QListWidget::item{padding:4px;}"
        "QTextEdit,QPlainTextEdit{background:#161921;color:#bfc9dc;border:1px solid #343946;"
        "border-radius:7px;padding:4px;}"
        "QScrollArea{background:#181a20;border:0;}"
        "QScrollBar:vertical{background:#181a20;width:8px;}"
        "QScrollBar::handle:vertical{background:#505768;border-radius:4px;min-height:24px;}"
        "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
        "QPushButton{background:#272a32;color:#ecedf3;border:1px solid transparent;"
        "border-radius:7px;padding:3px 7px;font-size:12px;}"
        "QPushButton:hover{background:#343844;}QPushButton:disabled{color:#777e8d;}"
        f"QPushButton:focus{{border-color:{accent};}}"
        f"QPushButton#PrimaryBtn{{background:{accent};color:#161b24;font-weight:600;}}"
        f"QPushButton#PrimaryBtn:hover{{background:{QColor(accent).lighter(110).name()};}}"
        "QPushButton#PrimaryBtn:disabled{background:#2b3040;color:#7c8496;}"
        "QPushButton#DangerBtn{background:#33262e;color:#ffb1b8;}"
        "QPushButton#DangerBtn:hover{background:#462d37;}"
        "QPushButton#DangerBtn:disabled{background:#25262d;color:#777e8d;}"
        "QProgressBar{background:#2a2e38;color:#ecedf3;border:1px solid #3b404d;"
        "border-radius:6px;text-align:center;}"
        f"QProgressBar::chunk{{background:{accent};border-radius:5px;}}"
        f'QComboBox::down-arrow{{image:url("{chevron}");width:12px;height:12px;}}'
        + workbench_field_style(accent) + workbench_slider_style(accent)
    )


def workbench_slider_style(accent):
    return (
        "QSlider::groove:horizontal{height:4px;background:#343740;border-radius:2px;}"
        f"QSlider::sub-page:horizontal{{background:{accent};border-radius:2px;}}"
        f"QSlider::handle:horizontal{{background:{accent};border:1px solid #1c1e23;"
        "width:12px;height:12px;margin:-5px 0;border-radius:7px;}"
        "QSlider::handle:horizontal:focus{border-color:#ffffff;}"
    )
