"""Painted editor controls independent of symbol fonts and system themes."""
from PyQt6.QtCore import Qt, QRectF, QPointF, QSize, QTimer, QEvent, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap, QIcon, QPainterPath, QFont, QFontMetrics, QLinearGradient
from PyQt6.QtWidgets import (QApplication, QPushButton, QToolButton, QFrame, QWidget,
                            QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QLayout)

from ui.widgets.workbench_controls import IMAGE_ACCENT, workbench_icon


_RAIL_ICONS = {'move': 'move', 'select_rect': 'square-dashed', 'crop': 'crop',
               'brush': 'paintbrush', 'text': 'type'}


def editor_icon(name, color='#d9e2f0', size=16):
    ratio = max(2, QApplication.primaryScreen().devicePixelRatio()) if QApplication.primaryScreen() else 2
    pixmap = QPixmap(round(size*ratio), round(size*ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size/16, size/16)
    painter.setPen(QPen(QColor(color), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    def line(x, y, a, b):
        painter.drawLine(QPointF(x, y), QPointF(a, b))
    if name in ('minus', 'plus'):
        line(4, 8, 12, 8)
        if name == 'plus':
            line(8, 4, 8, 12)
    elif name == 'fit':
        for x, y, dx, dy in ((2.5, 2.5, 1, 1), (13.5, 2.5, -1, 1), (2.5, 13.5, 1, -1), (13.5, 13.5, -1, -1)):
            line(x, y+3*dy, x, y)
            line(x, y, x+3*dx, y)
        painter.drawRect(QRectF(5.5, 5.5, 5, 5))
    elif name == 'swap':
        line(3, 5, 13, 5); line(10, 2, 13, 5); line(13, 5, 10, 8)
        line(13, 11, 3, 11); line(6, 8, 3, 11); line(3, 11, 6, 14)
    elif name == 'default':
        painter.setBrush(QColor('#e5eaf2')); painter.drawRoundedRect(QRectF(6, 6, 7, 7), 1, 1)
        painter.setBrush(QColor('#151920')); painter.drawRoundedRect(QRectF(2, 2, 7, 7), 1, 1)
    elif name == 'close':
        line(4, 4, 12, 12); line(12, 4, 4, 12)
    elif name == 'eyedropper':
        path = QPainterPath(QPointF(4, 10))
        for point in (QPointF(10, 4), QPointF(12, 6), QPointF(6, 12), QPointF(3, 13), QPointF(4, 10)):
            path.lineTo(point)
        painter.drawPath(path)
        line(9, 3, 13, 7)
    elif name == 'move':
        line(8, 2, 8, 14); line(2, 8, 14, 8)
        for x, y, a, b, c, d in ((8, 2, 6, 4, 10, 4), (8, 14, 6, 12, 10, 12),
                                 (2, 8, 4, 6, 4, 10), (14, 8, 12, 6, 12, 10)):
            line(a, b, x, y); line(x, y, c, d)
    elif name in ('select_rect', 'select_ellipse'):
        pen = painter.pen(); pen.setStyle(Qt.PenStyle.DashLine); pen.setDashPattern([1.2, 1.8]); painter.setPen(pen)
        if name == 'select_rect':
            painter.drawRoundedRect(QRectF(2.5, 3, 11, 10), 1, 1)
        else:
            painter.drawEllipse(QRectF(2.5, 2.5, 11, 11))
    elif name == 'lasso':
        path = QPainterPath(QPointF(5, 11))
        path.cubicTo(0, 9, 2, 3, 8, 3); path.cubicTo(15, 3, 15, 10, 9, 11)
        path.cubicTo(5, 13, 3, 8, 6, 9); path.cubicTo(9, 10, 6, 13, 4, 14)
        painter.drawPath(path)
    elif name == 'polygon':
        path = QPainterPath(QPointF(3, 4))
        for x, y in ((11, 2.5), (14, 9), (7, 13), (3, 9), (3, 4)):
            path.lineTo(x, y)
        painter.drawPath(path); line(4, 11, 2.5, 14)
    elif name == 'wand':
        line(3, 13, 10, 6); line(4.8, 11.2, 6.8, 13.2)
        for x, y, length in ((10.5, 3, 1.8), (13, 7.5, 1.3), (5, 4, 1.1)):
            line(x-length, y, x+length, y); line(x, y-length, x, y+length)
    elif name == 'object':
        for x, y, dx, dy in ((2, 2, 1, 1), (13, 2, -1, 1), (2, 13, 1, -1)):
            line(x, y+3*dy, x, y); line(x, y, x+3*dx, y)
        path = QPainterPath(QPointF(7, 6)); path.lineTo(13, 9); path.lineTo(10, 10)
        path.lineTo(9, 13); path.closeSubpath(); painter.drawPath(path)
    elif name in ('brush', 'quick_select'):
        path = QPainterPath(QPointF(6, 9)); path.lineTo(11.5, 3)
        path.cubicTo(13, 1.5, 15, 3.5, 13, 5); path.lineTo(8, 11); path.closeSubpath()
        painter.drawPath(path)
        path = QPainterPath(QPointF(6, 9)); path.cubicTo(2, 8, 5, 13, 1.5, 13.5)
        path.cubicTo(6, 15, 10, 12, 8, 11); painter.drawPath(path)
        if name == 'quick_select':
            pen = painter.pen(); pen.setWidthF(1.1); pen.setStyle(Qt.PenStyle.DashLine); painter.setPen(pen)
            painter.drawArc(QRectF(1, 1, 8, 8), 70*16, 210*16)
    elif name == 'eraser':
        path = QPainterPath(QPointF(2, 9)); path.lineTo(8, 3); path.lineTo(14, 8)
        path.lineTo(9, 13); path.lineTo(6, 13); path.closeSubpath(); painter.drawPath(path)
        line(5, 6, 11, 11); line(8, 13, 14, 13)
    elif name == 'text':
        line(3, 3, 13, 3); line(8, 3, 8, 13); line(5, 13, 11, 13)
        line(3, 3, 3, 5); line(13, 3, 13, 5)
    elif name == 'rectangle':
        painter.drawRoundedRect(QRectF(2, 3, 12, 10), 1.5, 1.5)
    elif name == 'ellipse':
        painter.drawEllipse(QRectF(2, 3, 12, 10))
    elif name == 'line':
        line(3, 13, 13, 3)
        painter.setBrush(QColor(color)); painter.drawEllipse(QPointF(3, 13), 1, 1)
        painter.drawEllipse(QPointF(13, 3), 1, 1)
    elif name == 'crop':
        line(4, 1.5, 4, 12); line(4, 12, 14.5, 12)
        line(1.5, 4, 12, 4); line(12, 4, 12, 14.5)
    elif name == 'gradient':
        shade = QLinearGradient(2, 0, 14, 0)
        faint = QColor(color); faint.setAlpha(30)
        shade.setColorAt(0, faint); shade.setColorAt(1, QColor(color))
        painter.setBrush(shade); painter.drawRoundedRect(QRectF(2, 3, 12, 10), 1.5, 1.5)
    elif name == 'heal':
        painter.translate(8, 8); painter.rotate(-45)
        painter.drawRoundedRect(QRectF(-6, -3, 12, 6), 3, 3)
        line(-2, -3, -2, 3); line(2, -3, 2, 3)
        painter.setBrush(QColor(color)); painter.drawEllipse(QPointF(0, 0), .6, .6)
    elif name == 'clone':
        path = QPainterPath(QPointF(5, 10)); path.lineTo(6, 8)
        path.cubicTo(8, 6, 4, 3, 7, 2); path.cubicTo(13, 1, 7, 7, 10, 8)
        path.lineTo(11, 10); path.closeSubpath(); painter.drawPath(path)
        painter.drawRoundedRect(QRectF(2, 10, 12, 3.5), 1, 1)
    painter.end()
    return QIcon(pixmap)


class EditorToolButton(QPushButton):
    """One aligned tool row: drawn icon, readable label, separate key badge."""
    def __init__(self, label, icon_name, shortcut, description=''):
        super().__init__(label)
        self.label, self.icon_name, self.description = label, icon_name, description
        self.shortcut_label = shortcut
        self.compact = False
        lucide_name = _RAIL_ICONS.get(icon_name)
        self.icons = {
            state: (workbench_icon(lucide_name, color, 18) if lucide_name
                    else editor_icon(icon_name, color, 18))
            for state, color in (('normal', '#a8abb8'), ('hover', '#ecedf3'), ('active', IMAGE_ACCENT))
        }
        self.setCheckable(True)
        self.setFixedHeight(28)
        self.setMinimumWidth(32)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(label)
        self.set_shortcut(shortcut)
        self.toggled.connect(lambda _: self.update())

    def sizeHint(self):
        return QSize(136, 28)

    def minimumSizeHint(self):
        return QSize(32, 28)

    def set_compact(self, compact):
        if self.compact != compact:
            self.compact = compact
            self.update()

    def set_shortcut(self, shortcut):
        self.shortcut_label = str(shortcut)
        heading = self.label + ('  '+self.shortcut_label if self.shortcut_label else '')
        self.setToolTip(heading + ('\n'+self.description if self.description else ''))
        self.setAccessibleDescription(heading)
        self.update()

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        active, hover = self.isChecked(), self.underMouse()
        rect = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        painter.setPen(QPen(QColor('#68588f'), 1) if active else Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#342c49' if active else '#2b2c34' if hover else '#1d1d20'))
        painter.drawRoundedRect(rect, 6, 6)
        if active:
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(IMAGE_ACCENT))
            painter.drawRoundedRect(QRectF(1, 8, 2, 12), 1, 1)
        icon = self.icons['active' if active else 'hover' if hover else 'normal']
        icon.paint(painter, (self.width()-18)//2 if self.compact else 10, 5, 18, 18)
        if self.compact:
            if self.hasFocus():
                painter.setBrush(Qt.BrushStyle.NoBrush); painter.setPen(QPen(QColor('#88888e'), 1))
                painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 4, 4)
            painter.end()
            return
        font = QFont(self.font()); font.setPixelSize(12); font.setBold(active)
        painter.setFont(font)
        painter.setPen(QColor('#ededf0' if active else '#d9d9dd' if hover else '#b7b7be'))
        shortcut = self.shortcut_label if self.width() >= 132 else ''
        shift = shortcut.lower().startswith('shift+') and len(shortcut) == 7
        key_font = QFont(font); key_font.setPixelSize(10); key_font.setBold(False)
        key_width = (24 if shift else min(40, max(18, QFontMetrics(key_font).horizontalAdvance(shortcut)+8))) if shortcut else 0
        label_rect = QRectF(35, 0, self.width()-43-key_width, self.height())
        label = QFontMetrics(font).elidedText(self.label, Qt.TextElideMode.ElideRight, int(label_rect.width()))
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)
        if shortcut:
            badge = QRectF(self.width()-key_width-6, 5, key_width, 18)
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor('#28282c' if active else '#19191c'))
            painter.drawRoundedRect(badge, 3, 3)
            painter.setPen(QColor('#b4b4bb' if active else '#7e7e87')); painter.setFont(key_font)
            if shift:
                x, y = badge.left()+7, badge.center().y()
                painter.setPen(QPen(QColor('#b4b4bb' if active else '#7e7e87'), 1))
                painter.drawLine(QPointF(x, y+3), QPointF(x, y-3))
                painter.drawLine(QPointF(x-2, y-1), QPointF(x, y-3))
                painter.drawLine(QPointF(x, y-3), QPointF(x+2, y-1))
                painter.drawText(badge.adjusted(10, 0, -2, 0), Qt.AlignmentFlag.AlignCenter, shortcut[-1])
            else:
                text = QFontMetrics(key_font).elidedText(shortcut, Qt.TextElideMode.ElideRight, int(badge.width()-4))
                painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush); painter.setPen(QPen(QColor('#88888e'), 1))
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 4, 4)
        painter.end()


class ColorSwatchButton(QPushButton):
    colorChanged = pyqtSignal(object)

    def __init__(self, color, tooltip='', parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.setFixedSize(32, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip or '选择颜色')

    def set_color(self, color):
        self.color = QColor(color)
        self.setAccessibleDescription(self.color.name().upper())
        self.colorChanged.emit(QColor(self.color))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outer = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(QColor('#99999f' if self.underMouse() or self.hasFocus() else '#55555d'), 1.3))
        painter.setBrush(QColor('#19191c'))
        painter.drawRoundedRect(outer, 5, 5)
        inside = outer.adjusted(3, 3, -3, -3)
        path = QPainterPath(); path.addRoundedRect(inside, 2.5, 2.5)
        painter.setClipPath(path)
        painter.fillRect(inside, QColor('#a0a5ad'))
        for y in range(int(inside.top()), int(inside.bottom())+1, 5):
            for x in range(int(inside.left()), int(inside.right())+1, 5):
                if ((x-int(inside.left()))//5 + (y-int(inside.top()))//5) % 2:
                    painter.fillRect(x, y, 5, 5, QColor('#d6d8de'))
        painter.fillRect(inside, self.color)
        painter.end()


class EditorColorCard(QFrame):
    def __init__(self, editor):
        super().__init__()
        self.setMinimumWidth(34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setObjectName('editorColorCard')
        self.setStyleSheet('QFrame#editorColorCard{background:#242427;border:1px solid #39393f;border-radius:7px;}'
                          'QLabel{color:#a6a6ad;background:transparent;border:0;font-size:10px;}'
                          'QToolButton{background:transparent;border:0;border-radius:3px;}'
                          'QToolButton:hover{background:#3a3a40;}')
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        layout.setContentsMargins(8, 7, 8, 8); layout.setSpacing(5)
        title = QHBoxLayout(); title.setSpacing(2)
        self.title_label = QLabel('颜色')
        title.addWidget(self.title_label); title.addStretch()
        self.reset_button = QToolButton()
        self.reset_button.setIcon(editor_icon('default', size=14))
        self.reset_button.setFixedSize(18, 20); self.reset_button.setToolTip('恢复默认颜色  D')
        self.reset_button.clicked.connect(editor._default_colors)
        self.swap_button = QToolButton()
        self.swap_button.setIcon(editor_icon('swap', size=14))
        self.swap_button.setFixedSize(18, 20); self.swap_button.setToolTip('交换前景色与背景色  X')
        self.swap_button.clicked.connect(editor._swap_colors)
        title.addWidget(self.reset_button); title.addWidget(self.swap_button)
        layout.addLayout(title)
        stack = QWidget(); stack.setFixedSize(76, 58)
        self.stack = stack
        self.background = ColorSwatchButton(editor.bg, '背景色 · 点击选择备用颜色', stack)
        self.background.setGeometry(28, 19, 40, 38)
        self.background.setFixedSize(40, 38)
        self.background.clicked.connect(editor._choose_secondary_color)
        self.foreground = ColorSwatchButton(editor.fg, '前景色 · 画笔使用的颜色', stack)
        self.foreground.setGeometry(2, 0, 40, 38)
        self.foreground.setFixedSize(40, 38)
        self.foreground.clicked.connect(editor._choose_fg)
        self.foreground.raise_()
        layout.addWidget(stack, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.hex_label = QLabel(editor.fg.name().upper())
        self.hex_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.foreground.colorChanged.connect(lambda color: self.hex_label.setText(color.name().upper()))
        layout.addWidget(self.hex_label)
        fill = QHBoxLayout(); fill.setContentsMargins(0, 3, 0, 0)
        self.fill_row = fill
        self.fill_label = QLabel('填充')
        fill.addWidget(self.fill_label); fill.addStretch()
        self.fill = ColorSwatchButton(editor.shape_fill_color, '形状填充色')
        self.fill.setFixedSize(30, 24)
        self.fill.clicked.connect(editor._choose_shape_fill_color)
        fill.addWidget(self.fill)
        layout.addLayout(fill)
        self.set_compact(False)

    def sizeHint(self):
        return QSize(128, 148)

    def minimumSizeHint(self):
        return QSize(34, 100)

    def set_compact(self, compact):
        self.title_label.setVisible(not compact)
        self.hex_label.setVisible(not compact)
        self.fill_label.setVisible(not compact)
        self.layout().setContentsMargins(3, 5, 3, 5) if compact else self.layout().setContentsMargins(8, 7, 8, 8)
        self.layout().setSpacing(4 if compact else 5)
        self.reset_button.setFixedSize(16 if compact else 18, 18 if compact else 20)
        self.swap_button.setFixedSize(16 if compact else 18, 18 if compact else 20)
        self.stack.setFixedSize(34 if compact else 76, 42 if compact else 58)
        self.foreground.setFixedSize(26 if compact else 40, 26 if compact else 38)
        self.background.setFixedSize(26 if compact else 40, 26 if compact else 38)
        self.foreground.move(0, 0) if compact else self.foreground.move(2, 0)
        self.background.move(8, 14) if compact else self.background.move(28, 19)
        self.fill.setFixedSize(26 if compact else 30, 22 if compact else 24)
        self.fill_row.setContentsMargins(6, 2, 6, 0) if compact else self.fill_row.setContentsMargins(0, 3, 0, 0)
        self.setFixedHeight(104 if compact else 148)

    def sync(self, editor):
        self.foreground.set_color(editor.fg)
        self.background.set_color(editor.bg)
        self.fill.set_color(editor.shape_fill_color)


class AdaptiveToolRail(QWidget):
    """A real splitter pane that switches presentation as its width changes."""
    compact_threshold = 112

    def __init__(self, scroll, content, buttons, headings, color_card):
        super().__init__()
        self.setMinimumWidth(52)
        self.setMaximumWidth(240)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.setStyleSheet('background:#1d1d20;')
        self.scroll, self.content = scroll, content
        self.buttons, self.headings, self.color_card = list(buttons), headings, color_card
        self.compact, self.last_expanded_width = None, 164
        self.splitter = None
        self._initial_width_applied = False
        content.setMinimumWidth(0)
        content.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        scroll.setMinimumWidth(0); scroll.setMaximumWidth(16777215)
        scroll.setMinimumSize(0, 0)
        scroll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        layout.setContentsMargins(5, 0, 5, 6); layout.setSpacing(8)
        layout.addWidget(scroll, 1); layout.addWidget(color_card)
        self.update_presentation()

    def sizeHint(self):
        return QSize(164, 700)

    def minimumSizeHint(self):
        return QSize(52, 200)

    def update_presentation(self):
        compact = self.width() < self.compact_threshold
        if self.compact == compact:
            return
        self.compact = compact
        for button in self.buttons:
            button.set_compact(compact)
        for header, label in self.headings:
            label.setVisible(not compact)
            header.setFixedHeight(10 if compact else 20)
        self.content.layout().setContentsMargins(1 if compact else 2, 6, 1 if compact else 2, 8)
        self.color_card.set_compact(compact)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'buttons'):
            self.update_presentation()
            if self.width() >= self.compact_threshold:
                self.last_expanded_width = self.width()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._initial_width_applied:
            self._initial_width_applied = True
            QTimer.singleShot(0, self._apply_initial_width)

    def _apply_initial_width(self):
        self.set_rail_width(164)

    def set_rail_width(self, target):
        from PyQt6 import sip
        if sip.isdeleted(self) or self.splitter is None or sip.isdeleted(self.splitter):
            return
        sizes = self.splitter.sizes()
        target = min(240, max(52, target))
        sizes[1] += sizes[0]-target
        sizes[0] = target
        self.splitter.setSizes(sizes)

    def attach_splitter(self, splitter):
        self.splitter = splitter
        handle = splitter.handle(1)
        handle.setToolTip('左右拖动调整工具栏宽度；收窄显示图标，拉宽显示名称。双击切换收起 / 展开。')
        handle.setCursor(Qt.CursorShape.SplitHCursor)
        handle.installEventFilter(self)

    def eventFilter(self, obj, event):
        if (event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease) and
                self.splitter is not None and obj is self.splitter.handle(1) and
                event.button() == Qt.MouseButton.LeftButton):
            if event.type() == QEvent.Type.MouseButtonPress:
                self._drag_origin_width = self.width()
            elif self.compact and getattr(self, '_drag_origin_width', 0) >= self.compact_threshold:
                self.last_expanded_width = self._drag_origin_width
        if (event.type() == QEvent.Type.MouseButtonDblClick and self.splitter is not None and
                obj is self.splitter.handle(1) and event.button() == Qt.MouseButton.LeftButton):
            target = self.last_expanded_width if self.compact else 52
            self.set_rail_width(target)
            return True
        return super().eventFilter(obj, event)


class ZoomControls(QFrame):
    def __init__(self, view):
        super().__init__()
        self.view = view
        self.setObjectName('zoomControls')
        self.setFixedSize(190, 30)
        self.setStyleSheet('QFrame#zoomControls{background:#272432;border:1px solid #464052;border-radius:7px;}'
                          'QToolButton{color:#dce5f3;background:transparent;border:0;border-radius:4px;padding:0;}'
                          'QToolButton:hover{background:#423653;}QToolButton:pressed{background:#55436d;}'
                          'QLineEdit{color:#e4ebf5;background:transparent;border:0;padding:0;font-size:11px;}'
                          'QLineEdit:focus{background:#25202f;border:1px solid #c3aaff;border-radius:4px;}')
        layout = QHBoxLayout(self); layout.setContentsMargins(3, 2, 3, 2); layout.setSpacing(1)
        self.out_button = QToolButton(); self.out_button.setIcon(editor_icon('minus'))
        self.in_button = QToolButton(); self.in_button.setIcon(editor_icon('plus'))
        self.fit_button = QToolButton(); self.fit_button.setIcon(editor_icon('fit'))
        for button, title in ((self.out_button, '缩小  Ctrl+-'), (self.in_button, '放大  Ctrl+='), (self.fit_button, '适应画布  Ctrl+0')):
            button.setFixedSize(27, 24); button.setToolTip(title); button.setAccessibleName(title.split('  ')[0])
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.level = QLineEdit('100%'); self.level.setFixedSize(57, 23)
        self.level.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.level.setToolTip('输入缩放比例，按 Enter 确认；支持 1%～3200%')
        self.level.setAccessibleName('缩放比例')
        self.level.editingFinished.connect(self.apply_percentage)
        self.fit_button.setText('适应'); self.fit_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.fit_button.setFixedWidth(58)
        self.out_button.clicked.connect(lambda: view.zoom_by(1/1.2))
        self.in_button.clicked.connect(lambda: view.zoom_by(1.2))
        self.fit_button.clicked.connect(view.fit_view)
        layout.addWidget(self.out_button); layout.addWidget(self.level); layout.addWidget(self.in_button)
        separator = QFrame(); separator.setFixedSize(1, 16); separator.setStyleSheet('background:#414958;border:0;')
        layout.addWidget(separator); layout.addSpacing(2); layout.addWidget(self.fit_button)

    def apply_percentage(self):
        try:
            value = float(self.level.text().strip().rstrip('%'))
            if not 1 <= value <= 3200:
                raise ValueError()
            current = max(abs(self.view.transform().m11()), 1e-9)
            self.view.zoom_by(value/100/current)
        except ValueError:
            self.view._update_zoom_label()
        self.level.clearFocus()
