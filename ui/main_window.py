import cv2
import sys
import os
import subprocess
import concurrent.futures
from datetime import datetime
from PyQt6.QtWidgets import *
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QTimer, QUrl, QSettings
from PyQt6.QtGui import *
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink
from .image_handler import ImageHandler
from .mix_handler import MixHandler
from .slideshow_handler import SlideshowHandler
from .image_editor import ImageEditorHandler
from .widgets import CheckMarkBox

# ═══════════════ 侧边栏折叠分组 ═══════════════
class SidebarGroup(QFrame):
    """可折叠的侧边栏分组"""
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("SidebarGroup")
        self._title = title
        self._collapsed = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setSpacing(0)

        self._header = QPushButton(f"  ▼ {title}")
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.setStyleSheet(
            "QPushButton { text-align:left; padding:6px 10px; border:none;"
            "font-size:12px; color:#777; background:transparent; }"
            "QPushButton:hover { color:#aaa; background:rgba(255,255,255,0.03); }"
        )
        self._header.clicked.connect(self._toggle)
        lay.addWidget(self._header)

        self._child_widget = QWidget()
        cl = QVBoxLayout(self._child_widget)
        cl.setContentsMargins(12, 0, 0, 0)
        cl.setSpacing(0)
        self._child_layout = cl
        lay.addWidget(self._child_widget)

    def add_button(self, text: str, index: int):
        if index is None:
            return None
        btn = QPushButton(f"  {text}")
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setProperty("tab_index", index)
        btn.setStyleSheet(
            "QPushButton[checkable=\"true\"] {"
            "text-align:left; padding:8px 10px; border:none; border-left:3px solid transparent;"
            "font-size:12px; color:#999; background:transparent; }"
            "QPushButton[checkable=\"true\"]:hover {"
            "color:#ccc; background:rgba(255,255,255,0.05); border-left:3px solid #555; }"
            "QPushButton[checkable=\"true\"]:checked {"
            "color:#fff; background:rgba(255,255,255,0.08); border-left:3px solid #3d8ef8; font-weight:bold; }"
        )
        self._child_layout.addWidget(btn)
        return btn

    def _toggle(self):
        self._collapsed = not self._collapsed
        self._child_widget.setVisible(not self._collapsed)
        arrow = "▶" if self._collapsed else "▼"
        self._header.setText(f"  {arrow} {self._title}")


# ── 小欢语音 工作台 ──
from .voice_workbench import VoiceWorkbench
from .script_workbench import ScriptWorkbench
from .editor_tab import EditorTab

# 设置 MoviePy 临时目录到系统临时文件夹
import tempfile
import moviepy.config as mp_config
mp_config.change_settings({"TEMP_DIR": tempfile.gettempdir()})

def resource_path(relative_path):
    """ 获取资源绝对路径,兼容 PyInstaller 的单文件模式 """
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)
# ==================== 自定义时间线控件 (自由手动版) ====================
# ==================== 实时虚化预览画布 ====================
class CanvasPreviewContainer(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.target_ratio = 0.0
        self.canvas_mode  = "fit"
        self.blur_enabled = True
        self.current_frame = None
        self.bg_pixmap     = None
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet("background:#000;")

    def set_canvas(self, ratio: float, mode: str):
        self.target_ratio = ratio
        self.canvas_mode  = mode
        self.update()

    def set_current_frame(self, frame_image: QImage):
        self.current_frame = frame_image
        if self.canvas_mode in ["blur", "crop"] and self.blur_enabled:
            W, H = self.width() or 400, self.height() or 300
            # 极速虚化算法:缩小再放大
            tiny = frame_image.scaled(20, 20,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            blurred = tiny.scaled(W, H,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            bp = QPixmap.fromImage(blurred)
            painter = QPainter(bp)
            painter.fillRect(bp.rect(), QColor(0, 0, 0, 150)) # 压暗背景
            painter.end()
            self.bg_pixmap = bp
        self.update()

    def _canvas_rect(self):
        W, H = self.width(), self.height()
        if self.target_ratio <= 0:
            return 0, 0, W, H
        cw, ch = W, int(W / self.target_ratio)
        if ch > H:
            ch = H; cw = int(H * self.target_ratio)
        return (W - cw) // 2, (H - ch) // 2, cw, ch

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        W, H = self.width(), self.height()
        cx, cy, cw, ch = self._canvas_rect()

        # 1. 画背景
        if self.canvas_mode in ["blur", "crop"] and self.blur_enabled and self.bg_pixmap:
            p.drawPixmap(0, 0, W, H, self.bg_pixmap)
        else:
            p.fillRect(0, 0, W, H, Qt.GlobalColor.black)

        # 2. 画前景视频
        if self.current_frame and not self.current_frame.isNull():
            iw, ih = self.current_frame.width(), self.current_frame.height()
            if iw > 0 and ih > 0:
                vr = iw / ih

                if self.canvas_mode == "crop":
                    # 裁剪模式:撑满高度,居中裁剪宽度
                    vch = ch
                    vcw = int(ch * vr)
                    vcx = cx + (cw - vcw) // 2
                    vcy = cy
                else:
                    # 默认 blur/fit 模式:等比缩小居中
                    vcw, vch = cw, int(cw / vr)
                    if vch > ch:
                        vch = ch; vcw = int(ch * vr)
                    vcx = cx + (cw - vcw) // 2
                    vcy = cy + (ch - vch) // 2

                scaled = self.current_frame.scaled(vcw, vch,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                p.drawImage(vcx, vcy, scaled)

        # 3. 遮罩四周多余部分 & 画参考线
        p.setBrush(QColor(0, 0, 0, 255))
        p.setPen(Qt.PenStyle.NoPen)
        if cx > 0:
            p.drawRect(0, 0, cx, H)
            p.drawRect(cx + cw, 0, W - cx - cw, H)
        if cy > 0:
            p.drawRect(0, 0, W, cy)
            p.drawRect(0, cy + ch, W, H - cy - ch)

        if self.target_ratio > 0:
            p.setPen(QPen(QColor("#00eaff"), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(cx, cy, cw - 1, ch - 1)
        p.end()
class SmartTimeline(QWidget):
    cut_changed = pyqtSignal(float)
    drag_started = pyqtSignal()
    def __init__(self):
        super().__init__()
        self.setFixedHeight(50)
        self.cut_ratio = 0.8
        self.setCursor(Qt.CursorShape.SplitHCursor)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # 绘制蓝色区域(保留部分)
        p.setBrush(QColor("#007acc"))
        p.drawRoundedRect(0, 10, int(w * self.cut_ratio), 30, 3, 3)
        # 绘制灰色区域(剪掉/尾页部分)
        p.setBrush(QColor("#444"))
        p.drawRoundedRect(int(w * self.cut_ratio) + 2, 10, max(0, int(w * (1-self.cut_ratio)) - 2), 30, 3, 3)

        # 绘制分割线(白色竖线)
        p.setPen(QPen(QColor("white"), 2))
        line_x = int(w * self.cut_ratio)
        p.drawLine(line_x, 5, line_x, 45)

    def mousePressEvent(self, event):
        self.drag_started.emit()
        self.update_ratio(event.pos().x())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.update_ratio(event.pos().x())

    def update_ratio(self, mouse_x):
        w = self.width()
        if w > 0:
            new_ratio = max(0.0, min(1.0, mouse_x / w))
            self.cut_ratio = new_ratio
            self.update()
            self.cut_changed.emit(new_ratio)

# ==================== 主工作站 ====================
class UltimateEngine(QMainWindow, ImageHandler, MixHandler, SlideshowHandler, ImageEditorHandler):

    def select_tail_video(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择标准尾页视频", "", "Video Files (*.mp4 *.mov *.avi)")
        if file_path:
            self.v_tail_path.setText(file_path)

    def __init__(self):
        super().__init__()
        self.tasks = []
        self.undo_stack = []
        self.config = {
            'out_dir': '',
            'tail_path': '',
            'rename': '',
            'is_single': False
        }
        self._is_running = False
        self.export_thread_running = False   # ← 新增这行(必须加)

        self.setWindowTitle("小欢ovo | 信息流批量剪辑工具")
        icon_path = resource_path("assets/icon.ico")
        self.setWindowIcon(QIcon(icon_path))
        self.resize(1500, 900)
        self.setAcceptDrops(True)

        self.init_style()
        self.init_ui()
        QApplication.instance().installEventFilter(self)

    def init_style(self):
        self.setStyleSheet("""
            /* ── 全局底色 ── */
            QMainWindow, QWidget {
                background-color: #1e1e1e;
                color: #cccccc;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
                font-size: 12px;
            }

            /* ── 侧边栏 ── */
            QFrame#SideBar {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #252525, stop:1 #1a1a1a);
                border-right: 1px solid #333333;
            }

            /* ── GroupBox ── */
            QGroupBox {
                border: 1px solid #383838;
                border-radius: 4px;
                margin-top: 15px;
                padding-top: 10px;
                color: #aaaaaa;
                font-weight: bold;
                background: rgba(255,255,255,0.01);
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
                color: #bbbbbb;
            }

            /* ── 普通按钮 ── */
            QPushButton {
                background: #2d2d2d;
                border: 1px solid #444444;
                padding: 7px 14px;
                border-radius: 3px;
                color: #cccccc;
            }
            QPushButton:hover {
                background: #383838;
                border: 1px solid #666666;
                color: #ffffff;
            }
            QPushButton:pressed {
                background: #252525;
                border: 1px solid #3d8ef8; /* 替换:选中蓝色 */
            }

            /* ── 主操作按钮 ── */
            QPushButton#PrimaryBtn {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #3d8ef8, stop:1 #1c6ed8); /* 替换:渐变蓝色 */
                color: white;
                border: none;
                font-weight: bold;
                font-size: 14px;
                border-radius: 4px;
            }
            QPushButton#PrimaryBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #64b5f6, stop:1 #2684ff); /* 替换:悬停浅蓝 */
            }
            QPushButton#PrimaryBtn:pressed {
                background: #0b5ed7; /* 替换:按下的深蓝 */
            }

            /* ── 危险按钮 ── */
            QPushButton#DangerBtn {
                background: #2d1a1a;
                color: #e06060;
                border: 1px solid #6b2020;
                border-radius: 3px;
            }
            QPushButton#DangerBtn:hover {
                background: #3a2020;
                border: 1px solid #e06060;
            }

            /* ── 表格 ── */
            QTableWidget {
                background-color: #1a1a1a;
                alternate-background-color: #1e1e1e;
                border: 1px solid #333333;
                gridline-color: #2a2a2a;
                border-radius: 3px;
            }
            QHeaderView::section {
                background: #2d2d2d;
                padding: 6px;
                border: none;
                border-bottom: 1px solid #3a3a3a;
                border-right: 1px solid #333333;
                color: #aaaaaa;
                font-weight: bold;
            }
            QTableWidget::item:hover {
                background: #2a2a2a;
            }
            QTableWidget::item:selected {
                background: #3d3d3d;
                color: #ffffff;
            }

            /* ── 输入框/下拉框 ── */
            QLineEdit, QComboBox, QDoubleSpinBox {
                background: #252525;
                border: 1px solid #444444;
                padding: 5px 8px;
                border-radius: 3px;
                color: #cccccc;
                selection-background-color: #3d8ef8; /* 替换:选择文字底色 */
            }
            QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {
                border: 1px solid #3d8ef8; /* 替换:焦点蓝色 */
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox QAbstractItemView {
                background: #2a2a2a;
                border: 1px solid #444444;
                selection-background-color: #3d8ef8;
                color: #cccccc;
            }

            /* ── 进度条 ── */
            QProgressBar {
                border: 1px solid #444444;
                border-radius: 3px;
                text-align: center;
                color: white;
                font-weight: bold;
                background: #1a1a1a;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #1c6ed8, stop:1 #3d8ef8); /* 替换:进度条蓝色渐变 */
                border-radius: 2px;
            }

            /* ── 滚动条 ── */
            QScrollBar:vertical {
                background: #1e1e1e;
                width: 10px;
                border-radius: 5px;
                margin: 2px;
            }
            QScrollBar::handle:vertical {
                background: #5a6a8a;
                border-radius: 5px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover { background: #7a8fad; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar:horizontal {
                background: #1e1e1e;
                height: 10px;
                border-radius: 5px;
                margin: 2px;
            }
            QScrollBar::handle:horizontal {
                background: #5a6a8a;
                border-radius: 5px;
                min-width: 30px;
            }
            QScrollBar::handle:horizontal:hover { background: #7a8fad; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

            /* ── TabWidget ── */
            QTabWidget::pane {
                border: 1px solid #333333;
                background: #1e1e1e;
                border-radius: 3px;
            }
            QTabBar::tab {
                background: #252525;
                color: #888888;
                padding: 6px 14px;
                border: 1px solid #333333;
                border-bottom: none;
                border-radius: 3px 3px 0 0;
            }
            QTabBar::tab:selected {
                background: #1e1e1e;
                color: #3d8ef8; /* 替换:Tab 激活色 */
                border-bottom: 2px solid #3d8ef8;
            }
            QTabBar::tab:hover { color: #cccccc; }
            QTabBar::tab:disabled { color: #444444; }

            /* ── 文本框 ── */
            QTextEdit, QPlainTextEdit {
                background: #141414;
                border: 1px solid #2a2a2a;
                color: #aaaaaa;
                border-radius: 3px;
                font-family: Consolas, monospace;
                font-size: 11px;
            }

            /* ── 复选框 ── */
            QCheckBox { color: #aaaaaa; spacing: 6px; }
            QCheckBox::indicator {
                width: 16px; height: 16px;
                border: 2px solid #555555;
                border-radius: 3px;
                background: #1a1a1a;
            }
            QCheckBox::indicator:checked {
                background: #3d8ef8;
                border: 2px solid #3d8ef8;
            }
            QCheckBox::indicator:hover {
                border: 2px solid #777777;
            }
            QCheckBox::indicator:checked:hover {
                border: 2px solid #64b5f6;
            }

            /* ── 状态栏 ── */
            QStatusBar {
                background: #161616;
                color: #666666;
                border-top: 1px solid #2a2a2a;
                font-size: 11px;
            }

            /* ── 弹窗对话框 ── */
            QMessageBox {
                background-color: #252525;
                color: #cccccc;
            }
            QMessageBox QLabel {
                color: #cccccc;
                font-size: 13px;
            }
            QMessageBox QPushButton {
                background: #3d8ef8;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                padding: 6px 20px;
                font-weight: bold;
                min-width: 60px;
            }
            QMessageBox QPushButton:hover {
                background: #4a9df9;
            }
            QMessageBox QPushButton:pressed {
                background: #2d7ee8;
            }
        """)
        # 同时把同一套暗色主题应用到整个应用程序（QApplication），
        # 否则顶层弹窗（QMessageBox / QDialog）不会继承本窗口的样式表，
        # 在系统深色主题下会变成「黑底黑字」看不清。Qt 中控件的样式表不会
        # 自动传递给顶层子窗口，必须显式设置到 QApplication 上才能覆盖全部弹窗。
        _app = QApplication.instance()
        if _app is not None:
            _app.setStyleSheet(self.styleSheet())

    def keyPressEvent(self, event):
        # 视频处理模块(tab 0)快捷键
        if self.stacked.currentIndex() == 0:
            if hasattr(self, '_edit_handle_key') and self._edit_handle_key(event):
                event.accept()
                return

        # 混剪模块(tab 2)
        if self.stacked.currentIndex() == 2:
            if hasattr(self, 'mix_key_press') and self.mix_key_press(event):
                return

        # 其他 tab 的 Ctrl+Z
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_Z:
            self.undo()
            return

        # 视频处理模块(tab 0)原有逻辑
        if event.key() == Qt.Key.Key_Space:
            self.toggle_play()
            return

        elif event.key() == Qt.Key.Key_Left:
            if self.player.duration() > 0:
                pos = max(0, self.player.position() - 100)
                self.player.setPosition(pos)
                self.player.pause()
                self.btn_play.setText("▶ 播放")
                self.statusBar().showMessage(f"逐帧微调:{pos/1000:.1f}s", 500)
            return

        elif event.key() == Qt.Key.Key_Right:
            if self.player.duration() > 0:
                pos = min(self.player.duration(), self.player.position() + 100)
                self.player.setPosition(pos)
                self.player.pause()
                self.btn_play.setText("▶ 播放")
                self.statusBar().showMessage(f"逐帧微调:{pos/1000:.1f}s", 500)
            return

        elif event.key() in [Qt.Key.Key_Delete, Qt.Key.Key_Backspace]:
            self.delete_selected_video()
            return

        super().keyPressEvent(event)
    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if event.type() == QEvent.Type.KeyPress:
            if self.stacked.currentIndex() == 2:
                # Ctrl+Z 走全局 undo,不交给 mix_key_press
                if (event.modifiers() == Qt.KeyboardModifier.ControlModifier
                        and event.key() == Qt.Key.Key_Z):
                    self.undo()
                    return True
                if self.mix_key_press(event):
                    return True
        return super().eventFilter(obj, event)
    def keyReleaseEvent(self, event):
        """空格松手 → 剪辑模块暂停"""
                # 剪辑模块空格松手
        if self.stacked.currentIndex() == 3:
            if hasattr(self, 'edit_key_release') and self.edit_key_release(event):
                event.accept()
                return
        super().keyReleaseEvent(event)

    def toggle_timeline(self):
        if self.v_table.currentRow() == -1:
            return
        visible = self.timeline.isVisible()
        self.timeline.setVisible(not visible)
        self.btn_show_cut.setText("保存并隐藏" if not visible else "手动修正剪切点")

    def on_timeline_moved(self, ratio):
        row = self.v_table.currentRow()
        if row != -1:
            self.v_table.setItem(row, 4, QTableWidgetItem(f"{ratio*100:.1f}% (手动)"))
            self.v_table.item(row, 4).setData(Qt.ItemDataRole.UserRole, ratio)

            status_item = QTableWidgetItem("手动指定")
            status_item.setForeground(QColor("#00eaff"))
            # AI状态在第 3 列
            self.v_table.setItem(row, 3, status_item)

            if row < len(self.tasks):
                duration = self.tasks[row].get('duration', 0)
                self.tasks[row]['precise_cut_time'] = duration * ratio

            if self.player.duration() > 0:
                self.player.pause()
                self.btn_play.setText("▶ 播放")
                target_ms = int(self.player.duration() * ratio)
                self.player.setPosition(target_ms)

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("SideBar")
        sidebar.setFixedWidth(150)
        side_lay = QVBoxLayout(sidebar)

        logo = QLabel()
        logo.setText('<span style="font-size:24px; font-weight:900;">'
                    '<font color="#3da8f5">小欢</font>'
                    '<font color="#cccccc">ovo</font>'
                    '</span>')
        logo.setStyleSheet("padding: 20px 10px; background: transparent;")
        side_lay.addWidget(logo)

        self.nav_btns = []
        self._tab_index = {}  # btn → stacked index

        # ── ✂ 剪辑工作台（首位）──
        btn_editor = QPushButton("  ✂ 剪辑工作台")
        btn_editor.setCheckable(True)
        btn_editor.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_editor.setProperty("tab_index", 6)
        btn_editor.setStyleSheet(
            "QPushButton[checkable=\"true\"] {"
            "text-align:left; padding:10px 12px; border:none; border-left:3px solid transparent;"
            "font-size:12px; color:#999; background:transparent; }"
            "QPushButton[checkable=\"true\"]:hover {"
            "color:#ccc; background:rgba(255,255,255,0.05); border-left:3px solid #555; }"
            "QPushButton[checkable=\"true\"]:checked {"
            "color:#fff; background:rgba(255,255,255,0.08); border-left:3px solid #00eaff; font-weight:bold; }"
        )
        side_lay.addWidget(btn_editor)
        side_lay.addSpacing(6)

        # ── 图片工作台（紧随剪辑工作台下方）──
        IMAGE_ACCENT = "#a78bfa"  # 与剪辑工作台(青色)区分的左侧颜色条
        btn_image_edit = QPushButton("  🎨 图片工作台")
        btn_image_edit.setCheckable(True)
        btn_image_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_image_edit.setProperty("tab_index", 7)
        btn_image_edit.setStyleSheet(
            "QPushButton[checkable=\"true\"] {"
            "text-align:left; padding:10px 12px; border:none; border-left:3px solid transparent;"
            "font-size:12px; color:#999; background:transparent; }"
            "QPushButton[checkable=\"true\"]:hover {"
            "color:#ccc; background:rgba(255,255,255,0.05); border-left:3px solid #555; }"
            "QPushButton[checkable=\"true\"]:checked {"
            "color:#fff; background:rgba(255,255,255,0.08); border-left:3px solid "
            + IMAGE_ACCENT + "; font-weight:bold; }"
        )
        side_lay.addWidget(btn_image_edit)
        side_lay.addSpacing(6)

        # ── 📹 视频部分 ──
        grp_video = SidebarGroup("视频部分")
        btn_tail = grp_video.add_button("尾页处理", 0)
        btn_mix  = grp_video.add_button("视频混剪", 2)
        btn_slide = grp_video.add_button("图片轮播", 3)
        side_lay.addWidget(grp_video)

        # ── 🖼️ 图片部分 ──
        grp_image = SidebarGroup("图片部分")
        btn_image = grp_image.add_button("图片处理", 1)
        side_lay.addWidget(grp_image)

        # ── 🎤 语音部分 ──
        grp_voice = SidebarGroup("语音部分")
        btn_voice  = grp_voice.add_button("语音配音", 4)
        btn_script = grp_voice.add_button("AI脚本", 5)
        side_lay.addWidget(grp_voice)

        # 注册所有按钮（剪辑工作台在最前）
        for btn in [btn_editor, btn_tail, btn_image, btn_image_edit, btn_mix, btn_slide, btn_voice, btn_script]:
            if btn:
                idx = btn.property("tab_index")
                self._tab_index[btn] = idx
                self.nav_btns.append(btn)
                btn.clicked.connect(self.switch_tab)
        side_lay.addStretch()

        # ── 🧹 系统工具 ──
        btn_clean = QPushButton("  🧹 清理缓存")
        btn_clean.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_clean.setStyleSheet(
            "QPushButton { text-align:left; padding:8px 12px; border:none; border-left:3px solid transparent;"
            "font-size:12px; color:#888; background:transparent; }"
            "QPushButton:hover { color:#ff6b6b; background:rgba(255,107,107,0.08); border-left:3px solid #ff6b6b; }"
        )
        btn_clean.clicked.connect(self._clean_cache)
        side_lay.addWidget(btn_clean)

        main_layout.addWidget(sidebar)

        # ── 语音工作室 ──
        self.voice_workbench = VoiceWorkbench()
        # ── AI脚本 ──
        self.script_workbench = ScriptWorkbench()
        # ── 剪辑工作台（index=7）──
        self.editor_tab = EditorTab()

        self.stacked = QStackedWidget()
        self.stacked.addWidget(self.build_video_module())    # 0
        self.stacked.addWidget(self.build_image_module())    # 1
        self.stacked.addWidget(self.build_mix_module())      # 2
        self.stacked.addWidget(self.build_slideshow_module()) # 3
        self.stacked.addWidget(self.voice_workbench)         # 4
        self.stacked.addWidget(self.script_workbench)        # 5
        self.stacked.addWidget(self.editor_tab)              # 6
        self.stacked.addWidget(self.build_image_editor_module())  # 7 图片工作台
        main_layout.addWidget(self.stacked)
        # 默认打开剪辑工作台
        self.stacked.setCurrentIndex(6)
        btn_editor.setChecked(True)

        # ── 跨功能信号连接 ──
        self.voice_workbench.script_to_polish.connect(self.script_workbench.load_raw_text)
        self.script_workbench.polished_text.connect(self._on_script_to_voice)
        self.voice_workbench.status_msg.connect(self._log_xh)
        self.script_workbench.status_msg.connect(self._log_xh)
        self.editor_tab.status_msg.connect(self._log_xh)
        # 语音配音 → 推送选中音频到剪辑工作台素材库
        self.voice_workbench.voice_pushed.connect(
            lambda path: self.editor_tab.media_lib.add_file(path))
        # 图层编辑 → 添加图层到视频素材库（保留透明通道）
        self.image_editor.add_layer_to_media_requested.connect(
            lambda path: (self.editor_tab.media_lib.add_file(path),
                          self.statusBar().showMessage("图层已添加到视频素材库", 3000)))

        # 恢复窗口几何
        s = QSettings("CreativeEnginePro", "MainWindow")
        if s.contains("geometry"):
            self.restoreGeometry(s.value("geometry"))
        else:
            self.resize(1280, 800)
            qr = self.frameGeometry(); cp = QApplication.primaryScreen().availableGeometry().center()
            qr.moveCenter(cp); self.move(qr.topLeft())
    def save_state(self):
        """记录当前任务和UI状态,用于撤销"""
        import copy
        # 防止在任务运行时记录零碎状态
        if getattr(self, '_is_running', False) or not getattr(self, 'btn_add_v', None) or not self.btn_add_v.isEnabled():
            return

        state = {
            'tasks': copy.deepcopy(self.tasks),
            'ui': []
        }
        for r in range(self.v_table.rowCount()):
            state['ui'].append({
                'check': self.v_table.item(r, 0).checkState(),
                'name': self.v_table.item(r, 1).text(),
                'path': self.v_table.item(r, 1).data(Qt.ItemDataRole.UserRole),
                'duration': self.v_table.item(r, 2).text(),
                'ai_text': self.v_table.item(r, 3).text(),
                'ai_color': self.v_table.item(r, 3).foreground().color(),
                'ratio_text': self.v_table.item(r, 4).text(),
                'ratio_data': self.v_table.item(r, 4).data(Qt.ItemDataRole.UserRole),
            })

        self.undo_stack.append(state)
        if len(self.undo_stack) > 50: # 最大保留50步撤销
            self.undo_stack.pop(0)

    def undo(self):
        """执行撤销 (Ctrl+Z)"""
        # 防止在多线程运行期间撤销导致数据错乱崩溃
        if not self.btn_add_v.isEnabled() or not self.btn_analyze.isEnabled() or not self.btn_export_all.isEnabled():
            self.update_log("后台正在处理任务,暂时无法撤销。")
            return

        if not self.undo_stack:
            self.update_log("已经是最初状态,没有可以撤销的操作了。")
            return

        import copy
        state = self.undo_stack.pop()

        self.player.stop()
        self.tasks = copy.deepcopy(state['tasks'])

        # 禁用信号,防止触发多余的变更事件
        self.v_table.blockSignals(True)
        self.v_table.setRowCount(0)

        for r, row_data in enumerate(state['ui']):
            self.v_table.insertRow(r)

            chk = QTableWidgetItem()
            chk.setCheckState(row_data['check'])
            self.v_table.setItem(r, 0, chk)

            name_item = QTableWidgetItem(row_data['name'])
            name_item.setData(Qt.ItemDataRole.UserRole, row_data['path'])
            self.v_table.setItem(r, 1, name_item)

            self.v_table.setItem(r, 2, QTableWidgetItem(row_data['duration']))

            ai_item = QTableWidgetItem(row_data['ai_text'])
            ai_item.setForeground(row_data['ai_color'])
            self.v_table.setItem(r, 3, ai_item)

            ratio_item = QTableWidgetItem(row_data['ratio_text'])
            ratio_item.setData(Qt.ItemDataRole.UserRole, row_data['ratio_data'])
            self.v_table.setItem(r, 4, ratio_item)

        self.v_table.blockSignals(False)
        self.update_log("已成功撤销上一步操作 (Ctrl+Z)。")
    def build_video_module(self):
        w = QWidget()
        lay = QHBoxLayout(w)

        left_p = QWidget()
        ll = QVBoxLayout(left_p)
        ll.addWidget(QLabel("视频队列 (Video Queue)", styleSheet="font-weight:bold; font-size:16px;"))

        ctrl_lay = QHBoxLayout()

        self.btn_add_v = QPushButton("➕ 导入视频")
        self.btn_add_v.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_add_v.setMinimumWidth(100)
        self.btn_add_v.clicked.connect(self.mock_import_video)
        self.btn_select_all = QPushButton("全选/反选")
        self.btn_select_all.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_select_all.clicked.connect(self.toggle_select_all)

        self.btn_analyze = QPushButton("智能解析所有断点")
        self.btn_analyze.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_analyze.setMinimumHeight(35)
        self.btn_analyze.setMinimumWidth(160)
        self.btn_analyze.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_analyze.setStyleSheet("""
            QPushButton {
                background-color: #409eff;
                color: white;
                font-weight: bold;
                border-radius: 4px;
                border: 1px solid #3a8ee6;
            }
            QPushButton:hover {
                background-color: #66b1ff;
            }
            QPushButton:pressed {
                background-color: #3a8ee6;
            }
            QPushButton:disabled {
                background-color: #a0cfff;
            }
        """)

        self.btn_analyze.clicked.connect(self.start_batch_analysis)

        self.btn_clear_v = QPushButton("清空列表")
        self.btn_clear_v.clicked.connect(self.clear_video_list)

        btn_del_v = QPushButton("删除选中条目")
        btn_del_v.clicked.connect(self.delete_selected_video)

        ctrl_lay.addWidget(self.btn_add_v)
        ctrl_lay.addWidget(self.btn_select_all)
        ctrl_lay.addWidget(self.btn_clear_v)
        ctrl_lay.addWidget(btn_del_v)
        ctrl_lay.addStretch()
        ctrl_lay.addWidget(self.btn_analyze)
        ctrl_lay.addStretch()
        ll.addLayout(ctrl_lay)

        self.v_table = QTableWidget(0, 5)
        self.v_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.v_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.v_table.setHorizontalHeaderLabels(["选择", "文件名", "原比例", "AI解析状态", "导出执行状态"])
        self.v_table.setColumnWidth(0, 40)
        self.v_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.v_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.v_table.cellClicked.connect(self.on_cell_clicked)
        self.v_table.itemSelectionChanged.connect(self.on_video_selected)
        ll.addWidget(self.v_table)

        lay.addWidget(left_p, 2)

        right_p = QWidget()
        rl = QVBoxLayout(right_p)

        rl.addWidget(QLabel("单条视频精修 (Inspector)", styleSheet="color:#00eaff; font-weight:bold;"))
        # 替换为这些行
        self.video_widget = CanvasPreviewContainer(self)
        self.video_widget.setMinimumHeight(400)
        rl.addWidget(self.video_widget, stretch=1)

        self.player = QMediaPlayer()
        self.audio = QAudioOutput()
        self.player.setAudioOutput(self.audio)

        # 使用 QVideoSink 拦截视频帧
        self._video_sink = QVideoSink()
        self.player.setVideoSink(self._video_sink)
        self._video_sink.videoFrameChanged.connect(self._on_video_frame_changed)

        play_lay = QHBoxLayout()
        self.btn_play = QPushButton("▶ 播放/暂停")
        self.btn_play.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_play.clicked.connect(self.toggle_play)
        play_lay.addWidget(self.btn_play)

        self.btn_re_analyze = QPushButton("重新分析")
        self.btn_re_analyze.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_re_analyze.setToolTip("如果AI分析不准,点击此按钮强制重新扫描该视频")
        self.btn_re_analyze.clicked.connect(self.re_analyze_current)
        play_lay.addWidget(self.btn_re_analyze)

        self.btn_show_cut = QPushButton("手动修正剪切点")
        self.btn_show_cut.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_show_cut.clicked.connect(self.toggle_timeline)
        play_lay.addWidget(self.btn_show_cut)
        rl.addLayout(play_lay)

        self.timeline = SmartTimeline()
        self.timeline.setVisible(False)
        self.timeline.cut_changed.connect(self.on_timeline_moved)
        self.timeline.drag_started.connect(self.save_state)
        rl.addWidget(self.timeline)

        # --- 输出参数设定 (压扁布局版) ---
        vg = QGroupBox("输出参数设定")
        vgl = QGridLayout() # 使用网格布局
        vgl.setContentsMargins(10, 8, 10, 8)
        vgl.setSpacing(10)

        # 1. 尺寸适配 (第0行, 前两列)
        self.v_ratio = QComboBox()
        self.v_ratio.addItems(["保持原样 (极速模式 - 不重编码)",
            "自动适配 (适应 9:16 - 竖屏)",
            "自动适配 (适应 4:5 - 社交媒体)",
            "自动适配 (适应 1:1 - 正方形)",
            "自动适配 (适应 16:9 - 横屏)"
            ])
        self.v_ratio.currentTextChanged.connect(self.on_ratio_changed)
        self.v_ratio.setFocusPolicy(Qt.FocusPolicy.NoFocus) # 锁死焦点
        vgl.addWidget(QLabel("尺寸适配:"), 0, 0)
        vgl.addWidget(self.v_ratio, 0, 1)

        # 2. 统一新文件名 (并排在第0行, 后两列)
        self.v_base_name = QLineEdit()
        self.v_base_name.setPlaceholderText("留空则使用原名")
        self.v_base_name.returnPressed.connect(lambda: self.setFocus())
        vgl.addWidget(QLabel("统一命名:"), 0, 2)
        vgl.addWidget(self.v_base_name, 0, 3)

        # 3. 统一替换尾页 (第1行, 横跨4列)
        self.global_tail_path = ""
        self.btn_browse_tail = QPushButton("点击选择尾页视频文件")
        self.btn_browse_tail.setFocusPolicy(Qt.FocusPolicy.NoFocus) # 锁死焦点
        self.btn_browse_tail.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_browse_tail.setStyleSheet("""
            QPushButton {
                text-align: left;
                padding-left: 15px;
                height: 30px;
                border: 1px dashed #444;
                border-radius: 4px;
                background-color: #1e1e1e;
                color: #888;
            }
            QPushButton:hover {
                border: 1px solid #00eaff;
                background-color: #252525;
                color: #00eaff;
            }
        """)
        self.btn_browse_tail.clicked.connect(self.on_select_global_tail)

        vgl.addWidget(QLabel("替换尾页:"), 1, 0)
        vgl.addWidget(self.btn_browse_tail, 1, 1, 1, 3) # 从1行1列开始,占据1行3列

        vg.setLayout(vgl)
        rl.addWidget(vg)

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(80)
        self.console.setStyleSheet("background-color: #000; color: #00eaff; border: 1px solid #333; font-family: 'Consolas';")
        rl.addWidget(self.console)

        rl.addStretch()

        # --- 新增:自动打开文件夹勾选框 ---
        self.cb_open_dir = CheckMarkBox("导出完成后自动打开文件夹")
        self.cb_open_dir.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cb_open_dir.setChecked(True)
        rl.addWidget(self.cb_open_dir)

        # --- 新增:醒目的导出进度条 ---
        self.export_progress = QProgressBar()
        self.export_progress.setValue(0)
        self.export_progress.setVisible(False) # 默认隐藏,导出时显示
        rl.addWidget(self.export_progress)

        self.btn_export_single = QPushButton("仅应用并导出当前选中")
        self.btn_export_single.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_export_single.clicked.connect(self.export_single_video)

        self.btn_apply_all = QPushButton("同步当前比例到所有视频")
        self.btn_apply_all.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_apply_all.clicked.connect(self.apply_to_all)

        self.btn_stop_export = QPushButton("停止/取消输出")
        self.btn_stop_export.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_stop_export.setObjectName("DangerBtn")
        self.btn_stop_export.clicked.connect(self.stop_export_task)
        self.btn_stop_export.setEnabled(False)

        self.btn_export_all = QPushButton("一键批量导出队列所有视频")
        self.btn_export_all.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_export_all.clicked.connect(self.start_batch_export)
        self.btn_export_all.setObjectName("PrimaryBtn")
        self.btn_export_all.setFixedHeight(50)

        rl.addWidget(self.btn_export_single)
        rl.addWidget(self.btn_apply_all)
        rl.addWidget(self.btn_export_all)
        rl.addWidget(self.btn_stop_export)
        lay.addWidget(right_p, 8)
        return w
    def _on_video_frame_changed(self, frame):
        if frame.isValid():
            img = frame.toImage()
            if not img.isNull() and hasattr(self, 'video_widget'):
                self.video_widget.set_current_frame(img)
    def on_ratio_changed(self, text):
        if not hasattr(self, 'video_widget'):
            return

        # 匹配你 self.v_ratio 里的具体文本内容
        if "9:16" in text:
            self.video_widget.set_canvas(9/16, "blur")
        elif "16:9" in text:
            self.video_widget.set_canvas(16/9, "blur")
        elif "4:5" in text:
            self.video_widget.set_canvas(4/5, "blur")
        elif "1:1" in text:
            self.video_widget.set_canvas(1.0, "blur")
        else:
            # "保持原样"或其他情况,切换回普通自适应模式
            self.video_widget.set_canvas(0.0, "fit")

        # 强制刷新预览画面(针对暂停状态)
        if hasattr(self, 'player') and self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            curr = self.player.position()
            if curr > 0:
                self.player.setPosition(curr - 1)
                self.player.setPosition(curr)
    # main_window.py 里的 MainWindow 类

    def stop_export_task(self):
        """立即终止并清理一切"""
        if hasattr(self, 'thread') and self.thread.isRunning():
            # 1. 通知处理器停止
            if hasattr(self.thread, 'processor'):
                self.thread.processor.is_cancelled = True

                # 如果有正在运行的 ffmpeg 进程,终止它
                if hasattr(self.thread.processor, 'current_subprocess') and self.thread.processor.current_subprocess:
                    try:
                        self.thread.processor.current_subprocess.terminate()
                        self.thread.processor.current_subprocess.wait(timeout=2)
                    except:
                        pass

            # 2. 等待线程自然结束
            self.thread.quit()
            if not self.thread.wait(3000):  # 等3秒
                self.thread.terminate()
                self.thread.wait()

            # 3. 删除未完成的输出文件
            if hasattr(self.thread, 'processor') and hasattr(self.thread.processor, 'current_out_path'):
                out_path = self.thread.processor.current_out_path
                if out_path and os.path.exists(out_path):
                    try:
                        os.remove(out_path)
                        self.update_log("已清理未完成的残损视频。")
                    except Exception as e:
                        self.update_log(f"无法删除临时文件: {e}")

            # 4. 恢复 UI
            self.export_thread_running = False
            self.v_table.setEnabled(True)
            self.btn_export_all.setEnabled(True)
            self.btn_stop_export.setEnabled(False)
            self.export_progress.setVisible(False)
            self.update_log("渲染已强制停止。")

    def apply_to_all(self):
        row = self.v_table.currentRow()
        if row == -1:
            QMessageBox.warning(self, "提醒", "请先在左侧列表中选择一个视频作为基准!")
            return

        current_ratio = self.v_table.item(row, 4).data(Qt.ItemDataRole.UserRole)
        res = QMessageBox.question(self, "确认同步",
                                 f"确定要将 {current_ratio*100:.1f}% 的剪切点应用到所有视频吗?",
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

        if res == QMessageBox.StandardButton.Yes:
            self.save_state()
            for r in range(self.v_table.rowCount()):
                self.v_table.setItem(r, 4, QTableWidgetItem(f"{current_ratio*100:.1f}% (强制同步)"))
                self.v_table.item(r, 4).setData(Qt.ItemDataRole.UserRole, current_ratio)

                status_item = QTableWidgetItem("已同步")
                status_item.setForeground(QColor("#00eaff"))
                self.v_table.setItem(r, 3, status_item)

                if r < len(self.tasks):
                    duration = self.tasks[r].get('duration', 0)
                    self.tasks[r]['precise_cut_time'] = duration * current_ratio

            self.update_log(f"已批量同步剪切点比例: {current_ratio*100:.1f}%")

    def start_batch_analysis(self):
        """一键批量解析所有未分析的视频 (多线程并发优化版 - 联动复选框)"""
        if not self.tasks:
            QMessageBox.warning(self, "提醒", "请先导入视频!")
            return

        pending = []
        for row, task in enumerate(self.tasks):
            # 只处理勾选状态的视频
            if self.v_table.item(row, 0).checkState() == Qt.CheckState.Checked:
                if task.get('precise_cut_time') is None:
                    pending.append((row, task['path']))

        if not pending:
            self.update_log("选中的视频均已解析(或未勾选任何未解析视频)。")
            return

        self.update_log(f"开始多核并发分析 {len(pending)} 个视频,请稍候...")
        self.btn_analyze.setEnabled(False) # 暂时禁用按钮防重复点击

        # --- 使用并发线程池代替主线程排队卡死 ---
        self.batch_analysis_worker = BatchAnalysisThread(pending)
        self.batch_analysis_worker.result_signal.connect(self.on_analysis_done)
        self.batch_analysis_worker.log_signal.connect(self.update_log)
        self.batch_analysis_worker.finished_signal.connect(self.on_batch_analysis_finished)
        self.batch_analysis_worker.start()

    def on_batch_analysis_finished(self):
        self.btn_analyze.setEnabled(True)
        self.update_log("批量并发分析全部完成!")
    def toggle_select_all(self):
        """全选/反选逻辑:根据第一行的状态来决定整体反转"""
        if self.v_table.rowCount() == 0:
            return
        self.save_state()

        # 获取第一行的当前状态
        first_item = self.v_table.item(0, 0)
        current_state = first_item.checkState()

        # 如果第一行是选中,就全部取消;否则全部选中
        new_state = Qt.CheckState.Unchecked if current_state == Qt.CheckState.Checked else Qt.CheckState.Checked

        for r in range(self.v_table.rowCount()):
            item = self.v_table.item(r, 0)
            if item:
                item.setCheckState(new_state)

    def clear_video_list(self):
        if self.v_table.rowCount() == 0: return
        res = QMessageBox.question(self, "确认", "确定要清空所有待处理视频吗?")
        if res == QMessageBox.StandardButton.Yes:
            self.save_state()
            self.player.stop()
            self.v_table.setRowCount(0)
            self.tasks = []
            self.update_log("队列已清空")

    def export_single_video(self):
        # 强制校验是否有点击选中并且高亮的行
        selected_items = self.v_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "提醒", "请先在列表中点击选中(背景高亮)一个视频!")
            return

        row = selected_items[0].row()

        path = self.v_table.item(row, 1).data(Qt.ItemDataRole.UserRole)
        name = self.v_table.item(row, 1).text()
        duration_str = self.v_table.item(row, 2).text().replace('s', '')
        ratio = self.v_table.item(row, 4).data(Qt.ItemDataRole.UserRole)

        tasks = [{
            "row_index": row,
            "path": path,
            "name": name,
            "precise_cut_time": float(duration_str) * ratio
        }]

        self.run_export_process(tasks, is_single=True)

    def run_export_process(self, tasks, is_single=False):
        # 如果线程正在运行,直接返回防止重复点击
        if getattr(self, 'export_thread_running', False):
            return

        # 唯一弹出选择文件夹的地方
        out_dir = QFileDialog.getExistingDirectory(self, "选择保存文件夹")
        if not out_dir:
            return # 用户取消,不锁定按钮,直接退出
        self._last_out_dir = out_dir

        # 锁定 UI 状态
        self.export_thread_running = True
        self.btn_export_all.setEnabled(False)
        self.btn_export_single.setEnabled(False)
        self.btn_clear_v.setEnabled(False)
        self.v_table.setEnabled(False)
        self.btn_stop_export.setEnabled(True)

        # 显示进度条
        self.export_progress.setVisible(True)
        self.export_progress.setValue(0)

        # 组织配置参数 (使用你正确的变量名 self.v_base_name)
        config = {
            "ratio_mode": self.v_ratio.currentText() if hasattr(self, 'v_ratio') else "默认",
            "rename": self.v_base_name.text().strip(),
            "tail_path": getattr(self, 'global_tail_path', None),
            "out_dir": out_dir,
            "is_single": is_single
        }

        # 启动线程
        try:
            # 注意:根据你之前的代码,线程类名可能是 VideoExportThread 或 ExportThread
            # 请确保这里的类名和你代码中定义的一致
            self.thread = VideoExportThread(tasks, config)
            self.thread.log_signal.connect(self.update_log)
            self.thread.progress_signal.connect(self.update_progress)
            self.thread.finished_signal.connect(self.on_export_finished)
            self.thread.start()
        except Exception as e:
            # 万一启动报错,立即恢复 UI,防止点不动
            self.on_export_finished()
            QMessageBox.critical(self, "错误", f"导出线程启动失败: {str(e)}")
    def start_batch_export(self):
        row_count = self.v_table.rowCount()
        if row_count == 0: return

        # 1. 收集勾选的任务
        tasks_to_export = []
        for row in range(row_count):
            if self.v_table.item(row, 0).checkState() == Qt.CheckState.Checked:
                # --- 安全获取时长 ---
                try:
                    duration_text = self.v_table.item(row, 2).text().replace('s', '')
                    duration = float(duration_text)
                except:
                    duration = 0.0

                # --- 安全获取比例 (核心修复:防止 NoneType 报错) ---
                ratio_item = self.v_table.item(row, 4)
                final_ratio = 1.0
                if ratio_item:
                    val = ratio_item.data(Qt.ItemDataRole.UserRole)
                    # 即使没有分析过,val 可能是 None,这里强制兜底
                    final_ratio = float(val) if val is not None else 1.0

                tasks_to_export.append({
                    "row_index": row,
                    "path": self.v_table.item(row, 1).data(Qt.ItemDataRole.UserRole),
                    "name": self.v_table.item(row, 1).text(),
                    "precise_cut_time": duration * final_ratio,
                })

        if not tasks_to_export:
            QMessageBox.warning(self, "提醒", "没有勾选任何需要导出的视频!")
            return

        self.run_export_process(tasks_to_export, is_single=False)

    def on_export_finished(self):
        if getattr(self, 'export_thread_running', False) == False:
            return

        self.export_thread_running = False
        self.v_table.setEnabled(True)
        self.btn_clear_v.setEnabled(True)
        self.btn_export_all.setEnabled(True)
        self.btn_export_single.setEnabled(True)
        self.btn_stop_export.setEnabled(False)

        self.export_progress.setVisible(False)
        self.export_progress.setValue(0)

        QMessageBox.information(self, "完成", "所有视频处理完毕!")

        if hasattr(self, 'cb_open_dir') and self.cb_open_dir.isChecked():
            out_dir = getattr(self, '_last_out_dir', '')
            if out_dir and os.path.exists(out_dir):
                os.startfile(out_dir)

    def update_log(self, text):
        if text.startswith("RENDER_PROGRESS:"):
            try:
                percent = int(text.split(":")[1])
                self.export_progress.setValue(percent)
                QApplication.processEvents()
                return
            except: pass

        elif text.startswith("STATUS_UPDATE:"):
            try:
                _, row_idx, status_text = text.split(":")
                item = QTableWidgetItem(status_text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                old_item = self.v_table.item(int(row_idx), 4)
                old_ratio = old_item.data(Qt.ItemDataRole.UserRole) if old_item else None
                self.v_table.setItem(int(row_idx), 4, item)
                if old_ratio is not None:
                    self.v_table.item(int(row_idx), 4).setData(Qt.ItemDataRole.UserRole, old_ratio)
                return
            except: pass

        self.console.appendPlainText(f"[{datetime.now().strftime('%H:%M:%S')}] {text}")
        self.console.moveCursor(QTextCursor.MoveOperation.End)

    def update_progress(self, value):
        self.statusBar().showMessage(f"批量处理进度: {value}%")
        # 同步更新UI上的主进度条
        if hasattr(self, 'export_progress'):
            self.export_progress.setValue(value)

    def switch_tab(self):
        btn = self.sender()
        idx = self._tab_index.get(btn, 0)
        for b in self.nav_btns:
            b.setChecked(b == btn)
        self.stacked.setCurrentIndex(idx)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        files = [u.toLocalFile() for u in event.mimeData().urls()]
        if not files:
            return
        tab = self.stacked.currentIndex()

        # 图片轮播 Tab → 导入图片
        if tab == 3:
            self._ss_add_images(files)
            return

        # 图片处理 Tab → 导入图片
        if tab == 1:
            from core.slideshow_engine import is_image as _is_image
            imgs = [f for f in files if _is_image(f)]
            if imgs:
                self._execute_img_import(imgs)
            return

        # 剪辑工作台 Tab → 导入到素材库
        if tab == 7:
            for f in files:
                self.editor_tab.media_lib.add_file(f)
            return

        # 其他 Tab → 按视频导入
        video_files = [f for f in files if f.lower().endswith(('.mp4', '.mov', '.avi'))]
        if video_files:
            self.execute_import(video_files)

    def execute_import(self, files):
        """统一的导入执行函数,供按钮和拖拽共同调用,包含重复防呆"""
        # --- 新增:过滤重复导入的防呆逻辑 ---
        existing_paths = {task['path'] for task in self.tasks}
        new_files = [f for f in files if f not in existing_paths]

        if not new_files:
            self.update_log("导入的视频已存在于队列中，已自动过滤。")
            return

        if len(new_files) < len(files):
            self.update_log(f"自动过滤了 {len(files) - len(new_files)} 个重复视频。")
        self.save_state()  
        self.btn_add_v.setEnabled(False)
        self.update_log(f"正在载入 {len(new_files)} 个新视频基础信息...")

        self.import_thread = VideoImportThread(new_files)
        self.import_thread.item_ready.connect(self.add_video_item_to_table)
        self.import_thread.log_signal.connect(self.update_log)
        self.import_thread.finished.connect(lambda: self.btn_add_v.setEnabled(True))
        self.import_thread.start()

    def mock_import_video(self):
        files, _ = QFileDialog.getOpenFileNames(self, "导入视频", "", "Videos (*.mp4 *.mov *.avi)")
        if files:
            self.execute_import(files)

    def add_video_item_to_table(self, path, duration, ai_info):
        r = self.v_table.rowCount()
        self.v_table.insertRow(r)

        # --- 第 0 列:新增复选框 (解决不能取消选中的 BUG) ---
        chk_item = QTableWidgetItem()
        chk_item.setCheckState(Qt.CheckState.Checked) # 默认勾选
        self.v_table.setItem(r, 0, chk_item)

        # --- 第 1 列:文件名 (原先是 0) ---
        name_item = QTableWidgetItem(os.path.basename(path))
        name_item.setData(Qt.ItemDataRole.UserRole, path)
        self.v_table.setItem(r, 1, name_item)

        # --- 第 2 列:时长/比例 (原先是 1) ---
        self.v_table.setItem(r, 2, QTableWidgetItem(f"{duration:.1f}s"))

        # --- 第 3 列:AI解析状态 (原先是 2) ---
        status_item = QTableWidgetItem("待解析")
        status_item.setToolTip("点击:恢复AI建议点\n右键:强制重新分析")
        status_item.setForeground(QColor("#aaaaaa"))
        self.v_table.setItem(r, 3, status_item)

        # --- 第 4 列:导出执行状态 (原先是 3,且现在改为显示导出进度) ---
        item_ratio = QTableWidgetItem("等待执行...")
        item_ratio.setData(Qt.ItemDataRole.UserRole, 1.0)
        self.v_table.setItem(r, 4, item_ratio)

        # 任务列表保持不变
        self.tasks.append({
            "path": path,
            "name": os.path.basename(path),
            "duration": duration,
            "precise_cut_time": None
        })

    def delete_selected_video(self):
        rows_to_delete = []
        for r in range(self.v_table.rowCount()):
            if self.v_table.item(r, 0).checkState() == Qt.CheckState.Checked:
                rows_to_delete.append(r)

        if not rows_to_delete:
            QMessageBox.information(self, "提示", "请先勾选左侧复选框再执行删除")
            return
        self.save_state()
        # 倒序删除
        for r in reversed(rows_to_delete):
            self.v_table.removeRow(r)
            if r < len(self.tasks):
                self.tasks.pop(r)

        self.player.stop()
        self.update_log(f"已删除 {len(rows_to_delete)} 个勾选项目")

    def on_video_selected(self):
        selected_items = self.v_table.selectedItems()
        if not selected_items:
            return

        row = selected_items[0].row()
        if row >= len(self.tasks):
            return

        task = self.tasks[row]
        path = task['path']

        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.pause()

        if hasattr(self, 'analysis_worker') and self.analysis_worker.isRunning():
            try:
                self.analysis_worker.finished_signal.disconnect()
            except:
                pass

        # --- 修改后的逻辑:只显示,不分析 ---
        if task.get('precise_cut_time') is None:
            # 仅仅输出日志,告诉用户已选中,但不要 self.analysis_worker.start()
            self.update_log(f"已选中: {task['name']} (等待手动点击智能解析)")

            # 同步 UI:传入视频总时长作为默认断点,置信度传 -1 表示"未分析"
            # 这样时间轴会显示在最后,不会乱跳
            duration = task.get('duration', 0)
            self.sync_analysis_to_ui(row, duration, -1)
        else:
            # 如果之前已经点过"解析"有了结果,再显示结果
            self.update_log(f"已加载历史分析结果: {task['name']}")
            self.sync_analysis_to_ui(row, task['precise_cut_time'], task.get('confidence', 100))

        self.setFocus()

    def show_table_context_menu(self, pos):
        menu = QMenu()
        re_act = menu.addAction("重新智能分析此视频")
        reset_act = menu.addAction("⏪ 恢复到AI建议点")

        action = menu.exec(self.v_table.mapToGlobal(pos))
        if action == re_act:
            self.re_analyze_current()
        elif action == reset_act:
            row = self.v_table.currentRow()
            if row != -1: self.on_cell_clicked(row, 3)

    def re_analyze_current(self):
        row = self.v_table.currentRow()
        if row == -1:
            QMessageBox.warning(self, "提醒", "请先选择一个视频!")
            return

        task = self.tasks[row]
        self.update_log(f"正在重新扫描断点: {task['name']}...")

        item = self.v_table.item(row, 3)
        if item:
            item.setText("正在重算...")
            item.setForeground(QColor("#00eaff"))

        self.analysis_worker = SingleAnalysisThread(row, task['path'])
        self.analysis_worker.finished_signal.connect(self.on_analysis_done)
        self.analysis_worker.start()

    def on_cell_clicked(self, row, column):
        AI_STATUS_COLUMN = 3

        if column == AI_STATUS_COLUMN:
            self.save_state()
            task = self.tasks[row]

            if 'ai_suggest_time' in task:
                ai_time = task['ai_suggest_time']
                conf = task.get('confidence', 0)

                task['precise_cut_time'] = ai_time
                self.sync_analysis_to_ui(row, ai_time, conf)

                self.statusBar().showMessage("✨ 已恢复 AI 建议位置", 2000)

                self.player.setPosition(int(ai_time * 1000))

    def on_analysis_done(self, row, cut_time, confidence):
        self.tasks[row]['precise_cut_time'] = cut_time
        self.tasks[row]['ai_suggest_time'] = cut_time
        self.tasks[row]['confidence'] = confidence
        self.sync_analysis_to_ui(row, cut_time, confidence)

    def sync_analysis_to_ui(self, row, cut_time, confidence):
        task = self.tasks[row]
        duration = task.get('duration', 1)

        ratio = cut_time / duration if duration > 0 else 0.8

        # 待解析状态判断
        if confidence == -1:
            status_text = "待解析"
            status_color = "#aaaaaa"
        elif confidence > 80:
            status_text = "极准"
            status_color = "#00ff00"
        elif confidence > 50:
            status_text = "AI建议"
            status_color = "#ffcc00"
        elif confidence > 0:
            status_text = "需确认"
            status_color = "#ff4444"
        else:
            status_text = "原片(无尾页)"
            status_color = "#888888"
            ratio = 1.0

        status_item = QTableWidgetItem(status_text)
        status_item.setForeground(QColor(status_color))
        self.v_table.setItem(row, 3, status_item) # 更新在第3列

        display_text = "100% (不剪切)" if ratio == 1.0 else f"{ratio*100:.1f}% (AI推荐)"
        item_ratio = QTableWidgetItem(display_text)
        item_ratio.setData(Qt.ItemDataRole.UserRole, ratio)
        self.v_table.setItem(row, 4, item_ratio) # 更新在第4列

        self.timeline.cut_ratio = ratio
        self.timeline.update()
        self.player.setPosition(int(cut_time * 1000))

    def force_seek(self, ratio):
        if self.player.duration() > 0:
            target_ms = int(self.player.duration() * ratio)
            self.player.setPosition(target_ms)
            self.player.pause()

    def on_select_global_tail(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择统一尾页视频", "", "Video Files (*.mp4 *.mov *.avi)"
        )
        if file_path:
            self.global_tail_path = file_path
            file_name = os.path.basename(file_path)
            self.btn_browse_tail.setText(f"已选: {file_name}")
            self.btn_browse_tail.setToolTip(file_path)

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.btn_play.setText("▶ 播放")
        else:
            self.player.play()
            self.btn_play.setText("⏸ 暂停")




    def on_import_finished(self):
        for btn in self.findChildren(QPushButton):
            if "导入" in btn.text():
                btn.setEnabled(True)

    # ═══════════ 小欢语音 跨Tab联动 ═══════════
    def _xh_jump_tab(self, index: int):
        """跳转Tab并同步高亮"""
        for btn, idx in self._tab_index.items():
            btn.setChecked(idx == index)
        self.stacked.setCurrentIndex(index)

    def _on_script_to_voice(self, text: str):
        """AI脚本 → 语音台"""
        self.voice_workbench.load_text(text)
        self._xh_jump_tab(5)  # 跳转到语音配音 Tab

    def _log_xh(self, msg: str, level: str = "info"):
        """小欢语音模块状态消息 → 控制台"""
        print(f"[小欢.{level}] {msg}")

    def _clean_cache(self):
        """清理临时缓存文件"""
        import shutil
        from pathlib import Path

        cleaned = []
        total_size = 0

        # 1. 清理 work_temp/
        work_temp = Path("work_temp")
        if work_temp.exists():
            for f in work_temp.iterdir():
                try:
                    if f.is_file():
                        total_size += f.stat().st_size
                        f.unlink()
                        cleaned.append(f.name)
                    elif f.is_dir():
                        total_size += sum(p.stat().st_size for p in f.rglob("*") if p.is_file())
                        shutil.rmtree(f)
                        cleaned.append(f.name + "/")
                except Exception as e:
                    print(f"[清理缓存] 删除失败 {f}: {e}")

        # 2. 清理系统临时目录中的 xh_mix_ 混音文件
        import tempfile
        tmp_dir = Path(tempfile.gettempdir())
        for f in tmp_dir.glob("xh_mix_*.mp3"):
            try:
                total_size += f.stat().st_size
                f.unlink()
                cleaned.append(f.name)
            except Exception as e:
                print(f"[清理缓存] 删除失败 {f}: {e}")

        # 3. 清理系统临时目录中的 xh_preview_ 预览文件
        for f in tmp_dir.glob("xh_preview_*.mp4"):
            try:
                total_size += f.stat().st_size
                f.unlink()
                cleaned.append(f.name)
            except Exception as e:
                print(f"[清理缓存] 删除失败 {f}: {e}")

        # 显示结果
        size_mb = total_size / (1024 * 1024)
        if cleaned:
            QMessageBox.information(
                self, "清理完成",
                f"已清理 {len(cleaned)} 个临时文件\n"
                f"释放空间: {size_mb:.1f} MB\n\n"
                f"包括: work_temp/ 目录、混音缓存、预览缓存"
            )
        else:
            QMessageBox.information(self, "清理完成", "没有需要清理的缓存文件")

    def closeEvent(self, event):
        """保存窗口几何 + 检查编辑器是否有未保存更改 + 清理自动保存"""
        if hasattr(self, 'editor_tab') and not self.editor_tab._check_save_before_close():
            event.ignore()
            return
        if hasattr(self, 'editor_tab'):
            self.editor_tab._cleanup_autosave()
        QSettings("CreativeEnginePro", "MainWindow").setValue("geometry", self.saveGeometry())
        super().closeEvent(event)



class VideoImportThread(QThread):
    item_ready = pyqtSignal(str, float, object)
    finished = pyqtSignal()
    log_signal = pyqtSignal(str)

    def __init__(self, file_paths):
        super().__init__()
        self.file_paths = file_paths

    def run(self):
        for f in self.file_paths:
            cap = cv2.VideoCapture(f)
            if cap.isOpened():
                fps = cap.get(cv2.CAP_PROP_FPS)
                count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                duration = count / fps if fps > 0 else 0
                cap.release()
                self.item_ready.emit(f, duration, (0, 0))
        self.finished.emit()

class SingleAnalysisThread(QThread):
    finished_signal = pyqtSignal(int, float, float)
    error_signal = pyqtSignal(str)

    def __init__(self, row, path):
        super().__init__()
        self.row = row
        self.path = path

    def run(self):
        try:
            from core.video_engine import VideoProcessor
            processor = VideoProcessor()
            point, confidence = processor.analyze_tail_breakpoint(self.path)
            self.finished_signal.emit(self.row, point, confidence)
        except Exception as e:
            self.error_signal.emit(str(e))

# --- 新增:多线程并发批处理任务分析类 ---
class BatchAnalysisThread(QThread):
    result_signal = pyqtSignal(int, float, float) # row, point, confidence
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, pending_tasks):
        super().__init__()
        self.pending_tasks = pending_tasks

    def run(self):
        try:
            from core.video_engine import VideoProcessor
            processor = VideoProcessor()
        except ImportError:
            self.log_signal.emit("无法导入 VideoProcessor,分析终止。")
            self.finished_signal.emit()
            return

        def _analyze(item):
            row, path = item
            try:
                point, confidence = processor.analyze_tail_breakpoint(path)
                return (row, point, confidence, None)
            except Exception as e:
                return (row, None, None, str(e))

        # 使用 ThreadPoolExecutor 控制 4 个并发,既能充分利用 CPU,又不会卡死
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            for result in executor.map(_analyze, self.pending_tasks):
                row, point, confidence, err = result
                if err is None:
                    # 每一条解析完毕后立刻发射信号更新 UI
                    self.result_signal.emit(row, point, confidence)
                else:
                    self.log_signal.emit(f"行 {row+1} 分析出错: {err}")

        self.finished_signal.emit()

class VideoExportThread(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal()

    def __init__(self, tasks, config):
        super().__init__()
        self.tasks = tasks
        self.config = config
        self._is_running = True

    def stop(self):
        self._is_running = False

    def run(self):
        from core.video_engine import VideoProcessor
        self.processor = VideoProcessor(self.log_signal)
        total = len(self.tasks)

        for i, task in enumerate(self.tasks):
            if not self._is_running or self.processor.is_cancelled:
                break

            row_idx = task.get('row_index', i)
            self.log_signal.emit(f"STATUS_UPDATE:{row_idx}:处理中...")

            # --- 保持你原有的命名逻辑 ---
            base_new_name = self.config.get('rename', '').strip()
            if base_new_name == "":
                final_name = os.path.splitext(task['name'])[0]
            else:
                if self.config.get('is_single'):
                    final_name = base_new_name
                else:
                    final_name = f"{base_new_name}_{str(i+1).zfill(2)}"

            current_task_config = self.config.copy()
            current_task_config['final_save_name'] = final_name

            out_dir = self.config.get('out_dir', '')
            self.current_out_path = os.path.join(out_dir, f"{final_name}.mp4")

            if task.get('precise_cut_time') is None:
                task['precise_cut_time'] = task.get('duration', 0)

            # 调用处理任务
            success = self.processor.process_task(task, current_task_config)

            # --- 后续逻辑保持不变 ---
            if success:
                self.log_signal.emit(f"STATUS_UPDATE:{row_idx}:已完成")
                self.progress_signal.emit(int((i + 1) / total * 100))
            else:
                if self.processor.is_cancelled:
                    self.log_signal.emit(f"STATUS_UPDATE:{row_idx}:已取消")
                else:
                    self.log_signal.emit(f"STATUS_UPDATE:{row_idx}:失败")

        self.finished_signal.emit()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    font = QFont("Microsoft YaHei", 9)
    app.setFont(font)

    window = UltimateEngine()
    window.show()
    sys.exit(app.exec())