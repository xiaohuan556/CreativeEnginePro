"""Canvas navigation and keyboard-friendly workbench controls."""
from PyQt6.QtCore import Qt, QRectF, QPointF, QEvent, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap, QPainterPath
from PyQt6.QtWidgets import QWidget, QLineEdit, QLabel
from core.image_effects import curve_table


class ScrubbableLabel(QLabel):
    def __init__(self,text,target,parent=None):
        super().__init__(text,parent)
        self.target = target
        self.start = None
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setToolTip('左右拖动调整数值；Shift 加速')

    def mousePressEvent(self,event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start = event.globalPosition().x(),self.target.value()
            event.accept()

    def mouseMoveEvent(self,event):
        if self.start is not None and event.buttons() & Qt.MouseButton.LeftButton:
            factor = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            delta = round((event.globalPosition().x()-self.start[0])*factor)
            self.target.setValue(self.start[1]+delta)
            event.accept()

    def mouseReleaseEvent(self,event):
        self.start = None
        event.accept()


class CurveEditor(QWidget):
    pointsChanged = pyqtSignal(object)
    selectionChanged = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.points = [[0., 0.], [255., 255.]]
        self.selected = None
        self.channel = 'rgb'
        self.setMinimumSize(340, 300)
        self.setMouseTracking(True)
        self.setToolTip('点击增加控制点，拖动调整；右键删除中间点')

    def graph_rect(self):
        return QRectF(30, 12, self.width() - 44, self.height() - 46)

    def set_points(self, points, channel='rgb'):
        curve_table(points)  # Reject corrupt state before painting.
        self.points = [list(map(float, point)) for point in points]
        self.channel = channel
        self.selected = None
        self.selectionChanged.emit(None)
        self.update()

    def screen_point(self, point):
        rect = self.graph_rect()
        return QPointF(rect.left() + point[0] / 255 * rect.width(), rect.bottom() - point[1] / 255 * rect.height())

    def curve_point(self, point):
        rect = self.graph_rect()
        return [min(255., max(0., (point.x() - rect.left()) / rect.width() * 255)),
                min(255., max(0., (rect.bottom() - point.y()) / rect.height() * 255))]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#1b1e24'))
        rect = self.graph_rect()
        painter.setPen(QColor('#3b414c'))
        for index in range(5):
            x, y = rect.x() + rect.width() * index / 4, rect.y() + rect.height() * index / 4
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        painter.setPen(QPen(QColor('#59616e'), 1, Qt.PenStyle.DashLine))
        painter.drawLine(rect.bottomLeft(), rect.topRight())
        table = curve_table(self.points)
        path = QPainterPath()
        path.moveTo(self.screen_point([0, table[0]]))
        for index, value in enumerate(table[1:], 1):
            path.lineTo(self.screen_point([index, value]))
        color = {'red': '#ff8383', 'green': '#83dc9a', 'blue': '#80afff'}.get(self.channel, '#e5ebf3')
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(color), 2))
        painter.drawPath(path)
        for index, point in enumerate(self.points):
            painter.setBrush(QColor('#58a6ff' if index == self.selected else '#1b1e24'))
            painter.drawEllipse(self.screen_point(point), 5, 5)
        painter.setPen(QColor('#b9c4d3'))
        painter.drawText(6, int(rect.bottom()), '0')
        painter.drawText(2, int(rect.top()) + 8, '255')
        label = '输入 → 输出'
        if self.selected is not None:
            point = self.points[self.selected]
            label = f'输入 {point[0]:.0f}  →  输出 {point[1]:.0f}'
        painter.drawText(30, self.height() - 8, label)
        painter.end()

    def _emit(self):
        self.pointsChanged.emit([point[:] for point in self.points])
        self.selectionChanged.emit(self.points[self.selected][:] if self.selected is not None else None)
        self.update()

    def update_selected_point(self, x=None, y=None):
        index = self.selected
        if index is None:
            return
        if x is not None and 0 < index < len(self.points) - 1:
            self.points[index][0] = min(self.points[index + 1][0] - 1, max(self.points[index - 1][0] + 1, float(x)))
        if y is not None:
            self.points[index][1] = min(255., max(0., float(y)))
        self._emit()

    def mousePressEvent(self, event):
        if not self.graph_rect().adjusted(-6, -6, 6, 6).contains(event.position()):
            self.selected = None
            return
        hit = next((i for i, point in enumerate(self.points)
                    if (self.screen_point(point) - event.position()).manhattanLength() < 13), None)
        if event.button() == Qt.MouseButton.RightButton:
            if hit is not None and 0 < hit < len(self.points) - 1:
                self.points.pop(hit)
                self.selected = None
                self._emit()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if hit is None and len(self.points) < 32:
            point = self.curve_point(event.position())
            if all(abs(point[0] - existing[0]) >= 1 for existing in self.points):
                self.points.append(point)
                self.points.sort(key=lambda p: p[0])
                hit = self.points.index(point)
                self._emit()
        self.selected = hit
        self.selectionChanged.emit(self.points[hit][:] if hit is not None else None)
        self.update()

    def mouseMoveEvent(self, event):
        if self.selected is None or not event.buttons() & Qt.MouseButton.LeftButton:
            return
        index = self.selected
        x, y = self.curve_point(event.position())
        if index in (0, len(self.points) - 1):
            x = self.points[index][0]
        else:
            x = min(self.points[index + 1][0] - 1, max(self.points[index - 1][0] + 1, x))
        self.points[index] = [x, y]
        self._emit()


def command_match_score(query, title):
    """Prefer complete names, then phrases, then ordered abbreviations."""
    query = ''.join(query.casefold().split())
    title = ''.join(title.casefold().split())
    if not query:
        return 0
    leaf = title.rsplit('›', 1)[-1]
    if query == leaf:
        return 10000
    if leaf.startswith(query):
        return 9000 - len(leaf)
    if query in title:
        return 8000 - title.index(query)
    position, score, previous = 0, 0, -2
    for char in query:
        found = title.find(char, position)
        if found < 0:
            return None
        score += 5 if found == previous + 1 else 1
        previous, position = found, found + 1
    return score - len(title)


class CommandSearchInput(QLineEdit):
    def __init__(self, results, parent=None):
        super().__init__(parent)
        self.results = results

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            step = 1 if event.key() == Qt.Key.Key_Down else -1
            row = self.results.currentRow()
            for _ in range(self.results.count()):
                row = (row + step) % self.results.count()
                if self.results.item(row).flags() & Qt.ItemFlag.ItemIsEnabled:
                    self.results.setCurrentRow(row)
                    self.results.scrollToItem(self.results.item(row))
                    break
            return
        super().keyPressEvent(event)


class CanvasNavigator(QWidget):
    """A viewport child; navigating never dispatches a canvas editing gesture."""
    def __init__(self, view):
        super().__init__(view.viewport())
        self.view = view
        self.enabled = True
        self.picture = QPixmap()
        self._source = QPixmap()
        self._thumbnail_dirty = False
        self._thumbnail_timer = QTimer(self)
        self._thumbnail_timer.setSingleShot(True)
        self._thumbnail_timer.setInterval(100)
        self._thumbnail_timer.timeout.connect(self._update_thumbnail)
        self.document = QRectF()
        self.setFixedSize(190, 150)
        self.setToolTip("导航：点击或拖动以定位画布")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("画布导航")
        view.viewport().installEventFilter(self)
        view.horizontalScrollBar().valueChanged.connect(self.refresh)
        view.verticalScrollBar().valueChanged.connect(self.refresh)
        self.hide()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize:
            self.refresh()
        return super().eventFilter(obj, event)

    def set_document(self, image):
        self.document = QRectF(0, 0, image.width(), image.height())
        self._source = image if isinstance(image, QPixmap) else QPixmap.fromImage(image)
        self._thumbnail_dirty = True
        self.refresh()

    def _update_thumbnail(self):
        if not self.isVisible() or not self._thumbnail_dirty:
            return
        self.picture = self._source.scaled(
            360, 260, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self._thumbnail_dirty = False
        self.update()

    def image_rect(self):
        if self.document.isEmpty():
            return QRectF()
        box = QRectF(9, 25, self.width() - 18, self.height() - 34)
        scale = min(box.width() / self.document.width(), box.height() / self.document.height())
        w, h = self.document.width() * scale, self.document.height() * scale
        return QRectF(box.center().x() - w / 2, box.center().y() - h / 2, w, h)

    def refresh(self, *_):
        visible = (self.enabled and not self._source.isNull() and
                   abs(self.view.transform().m11()) >= 3 and self.parentWidget().width() >= 260)
        was_visible = not self.isHidden()
        if visible != was_visible:
            self.setVisible(visible)
        if not visible:
            self._thumbnail_timer.stop()
            return
        self.move(max(22, self.parentWidget().width() - self.width() - 12), 30)
        if not was_visible:
            self.raise_()
            self._update_thumbnail()
        elif self._thumbnail_dirty and not self._thumbnail_timer.isActive():
            self._thumbnail_timer.start()
        self.update()

    def scene_point(self, pos):
        rect = self.image_rect()
        if rect.isEmpty():
            return QPointF()
        x = min(1., max(0., (pos.x() - rect.x()) / rect.width()))
        y = min(1., max(0., (pos.y() - rect.y()) / rect.height()))
        return QPointF(x * self.document.width(), y * self.document.height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor('#555963'))
        painter.setBrush(QColor('#20232a'))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 5, 5)
        painter.setPen(QColor('#d5dfed'))
        painter.drawText(9, 17, "导航  ·  %.0f%%" % (abs(self.view.transform().m11()) * 100))
        rect = self.image_rect()
        if not rect.isEmpty():
            painter.drawPixmap(rect, self.picture, QRectF(self.picture.rect()))
            shown = self.view.mapToScene(self.view.viewport().rect()).boundingRect().intersected(self.document)
            if not shown.isEmpty():
                scale = rect.width() / self.document.width()
                box = QRectF(rect.x() + shown.x() * scale, rect.y() + shown.y() * scale,
                             shown.width() * scale, shown.height() * scale)
                painter.setPen(QPen(QColor('#60acff'), 2))
                painter.setBrush(QColor(60, 150, 255, 35))
                painter.drawRect(box)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.image_rect().contains(event.position()):
            self.view.centerOn(self.scene_point(event.position()))
            self.refresh()
        event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.view.centerOn(self.scene_point(event.position()))
            self.refresh()
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()
