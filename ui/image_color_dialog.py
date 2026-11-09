"""Compact workbench color picker with consistent Chinese controls."""
from PyQt6.QtCore import Qt, QRectF, QPointF, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QLinearGradient, QPainterPath, QCursor
from PyQt6.QtWidgets import (QApplication, QWidget, QDialog, QFrame, QVBoxLayout, QHBoxLayout,
                            QLabel, QToolButton, QPushButton, QLineEdit, QSpinBox, QGridLayout)
from ui.image_chrome import ColorSwatchButton, editor_icon


class SaturationValueArea(QWidget):
    changed = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hue, self.saturation, self.value = 0., 1., 1.
        self.setMinimumSize(280, 260)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setAccessibleName('饱和度与明度，点击或拖动选择颜色')

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath(); path.addRoundedRect(rect, 7, 7)
        painter.setClipPath(path)
        painter.fillRect(rect, QColor.fromHsvF(self.hue, 1., 1.))
        white = QLinearGradient(rect.topLeft(), rect.topRight())
        white.setColorAt(0, QColor('white')); white.setColorAt(1, QColor(255, 255, 255, 0))
        painter.fillRect(rect, white)
        black = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        black.setColorAt(0, QColor(0, 0, 0, 0)); black.setColorAt(1, QColor('black'))
        painter.fillRect(rect, black)
        painter.setClipping(False)
        painter.setPen(QPen(QColor('#566171'), 1)); painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 7, 7)
        point = QPointF(rect.x()+self.saturation*rect.width(), rect.y()+(1-self.value)*rect.height())
        painter.setPen(QPen(QColor(0, 0, 0, 160), 3.5)); painter.drawEllipse(point, 5, 5)
        painter.setPen(QPen(QColor('white'), 1.7)); painter.drawEllipse(point, 5, 5)
        painter.end()

    def select(self, point):
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        self.saturation = min(1., max(0., (point.x()-rect.x())/rect.width()))
        self.value = 1-min(1., max(0., (point.y()-rect.y())/rect.height()))
        self.changed.emit(self.saturation, self.value)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.select(event.position())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.select(event.position())


class HueRail(QWidget):
    changed = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hue = 0.
        self.setFixedWidth(22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName('色相，拖动调整')

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(4, 1, -4, -1)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        for index in range(7):
            gradient.setColorAt(index/6, QColor.fromHsvF(index/6, 1., 1.))
        painter.setPen(QPen(QColor('#566171'), 1)); painter.setBrush(gradient)
        painter.drawRoundedRect(rect, 4, 4)
        y = rect.y()+self.hue*rect.height()
        painter.setPen(QPen(QColor('#111722'), 3.5)); painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(1, y-3, self.width()-2, 6), 2, 2)
        painter.setPen(QPen(QColor('white'), 1.8))
        painter.drawRoundedRect(QRectF(1, y-3, self.width()-2, 6), 2, 2)
        painter.end()

    def select(self, point):
        self.hue = min(1., max(0., (point.y()-1)/max(1, self.height()-2)))
        self.changed.emit(self.hue)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.select(event.position())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.select(event.position())


class ScreenColorPicker(QWidget):
    picked = pyqtSignal(object)
    cancelled = pyqtSignal()

    def __init__(self, parent):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        screens = QApplication.screens()
        geometry = screens[0].geometry()
        for screen in screens[1:]:
            geometry = geometry.united(screen.geometry())
        self.images = [(screen.geometry(), screen.grabWindow(0).toImage()) for screen in screens]
        self.setGeometry(geometry)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.position = QPointF(self.mapFromGlobal(QCursor.pos()))
        self.setWindowOpacity(1)

    def sample(self, global_point):
        for rect, image in self.images:
            if rect.contains(global_point) and not image.isNull():
                x = int((global_point.x()-rect.x())*image.width()/rect.width())
                y = int((global_point.y()-rect.y())*image.height()/rect.height())
                return image.pixelColor(min(image.width()-1, x), min(image.height()-1, y))
        return QColor()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#171b23'))
        for rect, image in self.images:
            local = rect.translated(-self.geometry().topLeft())
            painter.drawImage(QRectF(local), image)
        color = self.sample(self.mapToGlobal(self.position.toPoint()))
        x, y = min(self.width()-190, self.position.x()+20), min(self.height()-78, self.position.y()+20)
        box = QRectF(max(4, x), max(4, y), 180, 68)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#71809b'), 1)); painter.setBrush(QColor('#202735'))
        painter.drawRoundedRect(box, 7, 7)
        painter.fillRect(box.adjusted(9, 12, -137, -12), color if color.isValid() else QColor('black'))
        painter.setPen(QColor('#edf2fa'))
        painter.drawText(box.adjusted(54, 8, -5, -28), Qt.AlignmentFlag.AlignVCenter, color.name().upper())
        painter.setPen(QColor('#aab8cc'))
        painter.drawText(box.adjusted(54, 31, -5, -5), Qt.AlignmentFlag.AlignVCenter, '点击取色 · Esc 取消')
        painter.end()

    def mouseMoveEvent(self, event):
        self.position = event.position()
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            color = self.sample(self.mapToGlobal(event.position().toPoint()))
            if color.isValid():
                self.picked.emit(color)
            else:
                self.cancelled.emit()
            self.close()
        elif event.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit(); self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit(); self.close()


class WorkbenchColorDialog(QDialog):
    recent_colors = []
    presets = ['#FFFFFF', '#CBD5E1', '#64748B', '#111827', '#EF4444', '#F59E0B',
               '#EAB308', '#22C55E', '#14B8A6', '#3B82F6', '#8B5CF6', '#EC4899']

    def __init__(self, color, parent=None, title='选择颜色'):
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(660, 466)
        self.original, self.color = QColor(color), QColor(color)
        self._hue = max(0., self.color.hsvHueF())
        self._drag_origin = None
        self.setStyleSheet('QFrame#colorPanel{background:#20242d;border:1px solid #4b5568;border-radius:12px;}'
                          'QLabel{color:#b3bfd2;background:transparent;font-size:11px;}'
                          'QLabel#colorTitle{color:#f0f4fb;font-size:18px;font-weight:600;}'
                          'QLineEdit,QSpinBox{background:#171c25;color:#e8eef9;border:1px solid #414b5f;'
                          'border-radius:5px;padding:5px;font-size:12px;selection-background-color:#356aba;}'
                          'QLineEdit:focus,QSpinBox:focus{border-color:#6c9fea;}'
                          'QSpinBox::up-button,QSpinBox::down-button{width:0;height:0;}'
                          'QPushButton,QToolButton{color:#d7e1f2;background:#2c3341;border:1px solid #485369;'
                          'border-radius:5px;padding:6px 12px;font-size:12px;}'
                          'QPushButton:hover,QToolButton:hover{background:#39465d;border-color:#6b87ad;}'
                          'QPushButton#confirmColor{background:#397ee8;border-color:#5c9bff;color:white;font-weight:600;}'
                          'QPushButton#confirmColor:hover{background:#4d90f6;}')
        outer = QVBoxLayout(self); outer.setContentsMargins(2, 2, 2, 2)
        panel = QFrame(); panel.setObjectName('colorPanel'); outer.addWidget(panel)
        layout = QVBoxLayout(panel); layout.setContentsMargins(22, 18, 22, 18); layout.setSpacing(17)
        header = QHBoxLayout()
        heading = QVBoxLayout(); heading.setSpacing(3)
        label = QLabel(title); label.setObjectName('colorTitle'); heading.addWidget(label)
        heading.addWidget(QLabel('拖动选色，或输入精确的颜色值'))
        header.addLayout(heading); header.addStretch()
        close = QToolButton(); close.setIcon(editor_icon('close')); close.setFixedSize(28, 28)
        close.setStyleSheet('QToolButton{padding:0;border:0;background:transparent;}'
                           'QToolButton:hover{background:#3b465a;}')
        close.setToolTip('关闭'); close.setAccessibleName('关闭取色窗口')
        close.clicked.connect(self.reject); header.addWidget(close)
        layout.addLayout(header)
        body = QHBoxLayout(); body.setSpacing(22)
        left = QVBoxLayout(); left.setSpacing(9)
        selection = QHBoxLayout(); selection.setSpacing(10)
        self.area, self.hue_rail = SaturationValueArea(), HueRail()
        selection.addWidget(self.area, 1); selection.addWidget(self.hue_rail)
        left.addLayout(selection)
        left.addWidget(QLabel('常用颜色'))
        palette = QHBoxLayout(); palette.setSpacing(3)
        for value in self.presets:
            button = ColorSwatchButton(QColor(value), value)
            button.setFixedSize(25, 24)
            button.clicked.connect(lambda checked, selected=value: self.set_color(QColor(selected)))
            palette.addWidget(button)
        left.addLayout(palette)
        body.addLayout(left, 1)
        right = QVBoxLayout(); right.setSpacing(10)
        preview = QHBoxLayout(); preview.setSpacing(8)
        for name, attribute, value in (('当前', 'old_preview', self.original), ('新颜色', 'new_preview', self.color)):
            column = QVBoxLayout(); column.setSpacing(4); column.addWidget(QLabel(name))
            swatch = ColorSwatchButton(value, '点击还原当前颜色' if name == '当前' else '新颜色预览')
            swatch.setFixedSize(87, 50); setattr(self, attribute, swatch); column.addWidget(swatch)
            preview.addLayout(column)
        self.old_preview.clicked.connect(lambda: self.set_color(self.original))
        self.new_preview.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        right.addLayout(preview)
        right.addWidget(QLabel('十六进制'))
        self.hex_edit = QLineEdit(); self.hex_edit.setMaxLength(7); self.hex_edit.setAccessibleName('十六进制颜色')
        self.hex_edit.editingFinished.connect(self.apply_hex); right.addWidget(self.hex_edit)
        self.rgb, self.hsv = [], []
        for labels, controls, maxima, callback in ((('R', 'G', 'B'), self.rgb, (255, 255, 255), self.apply_rgb),
                                                   (('H', 'S', 'V'), self.hsv, (360, 100, 100), self.apply_hsv)):
            grid = QGridLayout(); grid.setHorizontalSpacing(6); grid.setVerticalSpacing(4)
            for index, (name, maximum) in enumerate(zip(labels, maxima)):
                label = QLabel(name); label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                grid.addWidget(label, 0, index)
                spin = QSpinBox(); spin.setRange(0, maximum); spin.setFixedWidth(58)
                spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
                spin.setAlignment(Qt.AlignmentFlag.AlignCenter); spin.setAccessibleName(name)
                spin.valueChanged.connect(callback); controls.append(spin); grid.addWidget(spin, 1, index)
            right.addLayout(grid)
        right.addWidget(QLabel('最近使用'))
        recent = QHBoxLayout(); recent.setSpacing(3)
        for value in (self.recent_colors[:6] or self.presets[6:12]):
            button = ColorSwatchButton(QColor(value), value); button.setFixedSize(28, 24)
            button.clicked.connect(lambda checked, selected=value: self.set_color(QColor(selected)))
            recent.addWidget(button)
        recent.addStretch(); right.addLayout(recent); right.addStretch()
        body.addLayout(right); layout.addLayout(body)
        footer = QHBoxLayout()
        screen = QPushButton('屏幕取色'); screen.setIcon(editor_icon('eyedropper'))
        screen.clicked.connect(self.start_screen_pick); footer.addWidget(screen); footer.addStretch()
        cancel = QPushButton('取消'); cancel.clicked.connect(self.reject); footer.addWidget(cancel)
        self.confirm = QPushButton('确定'); self.confirm.setObjectName('confirmColor')
        self.confirm.setDefault(True); self.confirm.clicked.connect(self.accept); footer.addWidget(self.confirm)
        layout.addLayout(footer)
        self.area.changed.connect(self.apply_sv)
        self.hue_rail.changed.connect(self.apply_hue)
        self.set_color(self.color)

    def set_color(self, color, keep_hue=False):
        color = QColor(color)
        if not color.isValid():
            return
        self.color = color
        if not keep_hue and color.hsvHueF() >= 0:
            self._hue = color.hsvHueF()
        self.area.hue, self.hue_rail.hue = self._hue, self._hue
        self.area.saturation, self.area.value = color.hsvSaturationF(), color.valueF()
        self.area.update(); self.hue_rail.update(); self.new_preview.set_color(color)
        values = (color.red(), color.green(), color.blue(), round(self._hue*360),
                  round(color.hsvSaturationF()*100), round(color.valueF()*100))
        for control, value in zip(self.rgb+self.hsv, values):
            control.blockSignals(True); control.setValue(value); control.blockSignals(False)
        self.hex_edit.setText(color.name().upper())
        self.hex_edit.setStyleSheet('')

    def apply_hex(self):
        text = self.hex_edit.text().strip().lstrip('#')
        color = QColor('#'+text)
        if len(text) in (3, 6) and color.isValid():
            self.set_color(color)
            return True
        self.hex_edit.setStyleSheet('border:1px solid #e7777d;')
        return False

    def apply_rgb(self, *_):
        self.set_color(QColor(*(control.value() for control in self.rgb)))

    def apply_hsv(self, *_):
        h, s, v = (control.value() for control in self.hsv)
        self._hue = h/360
        self.set_color(QColor.fromHsvF(self._hue, s/100, v/100), keep_hue=True)

    def apply_sv(self, saturation, value):
        self.set_color(QColor.fromHsvF(self._hue, saturation, value), keep_hue=True)

    def apply_hue(self, hue):
        self._hue = hue
        self.set_color(QColor.fromHsvF(hue, self.area.saturation, self.area.value), keep_hue=True)

    def accept(self):
        if not self.apply_hex():
            self.hex_edit.setFocus(); return
        value = self.color.name().upper()
        type(self).recent_colors = [value] + [item for item in self.recent_colors if item != value][:11]
        super().accept()

    def start_screen_pick(self):
        self.setWindowOpacity(0)
        QTimer.singleShot(120, self._open_screen_picker)

    def _open_screen_picker(self):
        from PyQt6 import sip
        if sip.isdeleted(self) or not self.isVisible():
            return
        self._picker = ScreenColorPicker(self)
        self._picker.picked.connect(lambda color: self._finish_screen_pick(color))
        self._picker.cancelled.connect(lambda: self._finish_screen_pick(None))
        self._picker.show(); self._picker.raise_(); self._picker.activateWindow()
        self._picker.setFocus()

    def _finish_screen_pick(self, color):
        if color is not None:
            self.set_color(color)
        self.setWindowOpacity(1)
        self.raise_(); self.activateWindow()
        self._picker.deleteLater()
        self._picker = None

    def reject(self):
        from PyQt6 import sip
        picker = getattr(self, '_picker', None)
        if picker is not None and not sip.isdeleted(picker):
            picker.close(); picker.deleteLater()
            self._picker = None
        self.setWindowOpacity(1)
        super().reject()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 77:
            self._drag_origin = event.globalPosition()-QPointF(self.pos())

    def mouseMoveEvent(self, event):
        if self._drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move((event.globalPosition()-self._drag_origin).toPoint())

    def mouseReleaseEvent(self, event):
        self._drag_origin = None


def choose_color(color, parent=None, title='选择颜色'):
    dialog = WorkbenchColorDialog(color, parent, title)
    result = QColor(dialog.color) if dialog.exec() == QDialog.DialogCode.Accepted else QColor()
    dialog.deleteLater()
    return result
