"""Layer selection gestures for the move tool; independent of pixel masks."""
from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen
from PyQt6.QtWidgets import QApplication


class LayerMarqueeOperations:
    def _begin_layer_marquee(self, position, modifiers):
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)
        control = bool(modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
        mode = 'intersect' if shift and alt else 'sub' if alt else 'toggle' if control else 'add' if shift else 'new'
        self.view._layer_marquee = dict(start=QPointF(position), end=QPointF(position),
            original=self.selected[:], active=self.active, mode=mode, dragged=False)
        self.view._dragging = True

    def _update_layer_marquee(self, position):
        state = getattr(self.view, '_layer_marquee', None)
        if state is None:
            return False
        state['end'] = QPointF(position)
        delta = state['end']-state['start']
        if abs(delta.x())+abs(delta.y()) < QApplication.startDragDistance() and not state['dragged']:
            return True
        state['dragged'] = True
        rectangle = QRectF(state['start'], state['end']).normalized()
        scene = self.view.mapToScene(rectangle.toAlignedRect()).boundingRect()
        if self.active_artboard is not None:
            x, y = self._ab_screen(self.active_artboard)
            scene.translate(-x, -y)
        hits = []
        def visit(layers):
            for layer in layers:
                if not layer.visible or layer.locked or layer.opacity <= 0 or layer.kind == 'adjust':
                    continue
                if layer.kind == 'group' and not layer.collapsed:
                    visit(layer.children)
                else:
                    box = self._layer_bbox(layer)
                    if box is not None and scene.intersects(box):
                        hits.append(layer)
        visit(self._root_layers())
        original, mode = state['original'], state['mode']
        if mode == 'new':
            selected = hits
        elif mode == 'add':
            selected = original+[layer for layer in hits if layer not in original]
        elif mode == 'sub':
            selected = [layer for layer in original if layer not in hits]
        elif mode == 'intersect':
            selected = [layer for layer in original if layer in hits]
        else:
            selected = [layer for layer in original if layer not in hits]+[layer for layer in hits if layer not in original]
        self.selected = selected
        self.active = selected[-1] if selected else None
        self._show_handles = len(selected) == 1
        self.view.viewport().update()
        return True

    def _finish_layer_marquee(self, position=None, cancel=False):
        state = getattr(self.view, '_layer_marquee', None)
        if state is None:
            return False
        if position is not None:
            self._update_layer_marquee(position)
        if cancel:
            self.selected, self.active = state['original'], state['active']
        elif not state['dragged'] and state['mode'] == 'new':
            self.selected, self.active = [], None
        self.view._layer_marquee = None
        self.view._dragging = False
        self._anchor = self.active
        self._show_handles = len(self.selected) == 1
        self._refresh_layers()
        self._draw_handles()
        if self.selected:
            self.status_tool.setText(f'已选择 {len(self.selected)} 个图层 · 拖动可一起移动')
        else:
            self.status_tool.setText('移动：点击选择，空白处拖动框选；Shift / Ctrl 点击多选')
        return True

    def paint_layer_selection(self):
        from ui.image_editor import Tool
        if self.tool != Tool.MOVE:
            return
        state = getattr(self.view, '_layer_marquee', None)
        painter = QPainter(self.view.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if len(self.selected) > 1 and not self._ft_active:
            combined = QRectF()
            x, y = self._ab_screen(self.active_artboard) if self.active_artboard else (0, 0)
            for layer in self.selected:
                box = self._layer_bbox(layer)
                if box is None:
                    continue
                scene = box.translated(x, y)
                mapped = QRectF(self.view.mapFromScene(scene).boundingRect())
                combined = combined.united(mapped)
                painter.setPen(QPen(QColor('#659bdd'), 1, Qt.PenStyle.DashLine))
                painter.setBrush(Qt.BrushStyle.NoBrush); painter.drawRect(mapped)
            if not combined.isEmpty():
                painter.setPen(QPen(QColor('#8cbdff'), 1.3, Qt.PenStyle.DashLine))
                painter.drawRect(combined.adjusted(-3, -3, 3, 3))
        if state and state['dragged']:
            rectangle = QRectF(state['start'], state['end']).normalized()
            painter.setBrush(QColor(73, 143, 245, 32))
            painter.setPen(QPen(QColor('#99c5ff'), 1.2, Qt.PenStyle.DashLine))
            painter.drawRect(rectangle)
        painter.end()
