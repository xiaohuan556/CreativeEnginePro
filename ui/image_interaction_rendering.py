"""Reuse static composition and source pixels during image pointer gestures."""
import copy
import time
import weakref
import numpy as np
from PyQt6.QtCore import Qt, QRectF, QTimer
from PyQt6.QtGui import QImage, QPainter, QPixmap, QPen, QBrush, QTransform
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsPixmapItem, QGraphicsRectItem
from core.image_layer_effects import has_layer_effects


class ImageInteractionRenderingOperations:
    def _request_full_resolution_render(self):
        hint = getattr(self, '_full_render_hint_project', None)
        self._full_render_hint_project = None
        if hint is not self.project:
            return False
        width, height = self._doc_bounds()
        heavy = any(layer.kind == 'adjust' or has_layer_effects(layer) or
                    layer.kind == 'group' and layer.opacity != 1 for layer in self._all_layers())
        if width*height < 1024*768 or not heavy:
            return False
        snapshot = self._snapshot(metadata_only=True)
        self._history_store.freeze_pending()
        ticket = (self.project, self._display_generation)
        if getattr(self, '_full_render_worker', None) is not None:
            self._full_render_pending = (snapshot, ticket)
        else:
            self._launch_full_render(snapshot, ticket)
        return True

    def _launch_full_render(self, snapshot, ticket):
        from core.image_render_worker import ImageCompositeWorker
        worker = ImageCompositeWorker(snapshot, ticket)
        self._full_render_worker = worker
        worker.rendered.connect(self._full_render_ready)
        worker.failed.connect(self._full_render_failed)
        worker.start()

    def _next_full_render(self):
        self._full_render_worker = None
        pending = getattr(self, '_full_render_pending', None)
        self._full_render_pending = None
        if pending is not None:
            snapshot, ticket = pending
            if ticket == (self.project, self._display_generation):
                self._launch_full_render(snapshot, ticket)

    def _full_render_ready(self, image, ticket):
        if ticket == (self.project, self._display_generation):
            display = QImage(image.size(), QImage.Format.Format_ARGB32)
            display.fill(Qt.GlobalColor.transparent)
            painter = QPainter(display)
            if self.project.artboards:
                for board in self.project.artboards:
                    if board.transparent:
                        x, y = self._ab_screen(board)
                        painter.save()
                        painter.translate(x, y)
                        self._paint_checker(painter, board.w, board.h)
                        painter.restore()
            elif self.project.transparent:
                self._paint_checker(painter, image.width(), image.height())
            painter.drawImage(0, 0, image)
            painter.end()
            self.view.composite_item.setTransform(QTransform())
            self.view.composite_item.setPixmap(QPixmap.fromImage(display))
            self.view.navigator.set_document(self.view.composite_item.pixmap())
            width, height = self._doc_bounds()
            margin = self.view.PAN_MARGIN
            self.view.scene.setSceneRect(-margin, -margin, width+2*margin, height+2*margin)
            self._redraw_selection()
            self._draw_guides_and_grid()
            if self.project.artboards:
                self._draw_artboards()
            self._draw_handles()
        self._next_full_render()

    def _full_render_failed(self, path, message):
        self._last_full_render_error = message
        worker = self._full_render_worker
        current = worker is not None and worker.ticket == (self.project, self._display_generation)
        self._next_full_render()
        if current:
            self._full_render_hint_project = None
            self._redraw()

    def _gesture_in_progress(self):
        return (bool(getattr(self, '_move_olds', None)) or self.view._handle_drag is not None
                or self.view._artboard_drag is not None)

    def _draft_array(self, source, width, height, key):
        import cv2
        cache = self._draft_source_cache
        old = cache.get(key)
        if old and old[0]() is source and old[1:3] == (width, height):
            return old[3]
        result = cv2.resize(source, (width, height), interpolation=cv2.INTER_AREA)
        cache[key] = (weakref.ref(source), width, height, result)
        return result

    def _draft_layer(self, layer, scale):
        from ui.image_editor import numpy_from_qimage
        clone = copy.copy(layer)
        clone.x, clone.y = layer.x*scale, layer.y*scale
        clone.w, clone.h = max(1, round(layer.w*scale)), max(1, round(layer.h*scale))
        clone.children = [self._draft_layer(child, scale) for child in layer.children]
        if layer.kind not in ('group', 'adjust'):
            if layer.kind in ('image', 'svg') and layer.pixels is not None:
                source = layer.pixels
            else:
                key = ('content', layer.id, layer.w, layer.h)
                source = self._draft_content_cache.get(key)
                if source is None:
                    content = self._layer_content(layer)
                    source = numpy_from_qimage(content)
                    self._draft_content_cache[key] = source
            clone.pixels = self._draft_array(source, clone.w, clone.h, ('pixels', layer.id))
            clone.kind, clone.svg_source = 'image', ''
        if layer.mask is not None:
            mh, mw = layer.mask.shape
            clone.mask = self._draft_array(layer.mask, max(1, round(mw*scale)),
                                            max(1, round(mh*scale)), ('mask', layer.id))
        lengths = ('shadow_dx', 'shadow_dy', 'shadow_blur', 'outer_glow_size',
                   'inner_shadow_dx', 'inner_shadow_dy', 'inner_shadow_blur',
                   'inner_glow_size', 'effect_stroke_width', 'bevel_size')
        for name in lengths:
            if hasattr(layer, name):
                setattr(clone, name, getattr(layer, name)*scale)
        clone.effects = dict(layer.effects)
        for name in lengths:
            if name in clone.effects:
                clone.effects[name] *= scale
        clone.adjust = copy.deepcopy(layer.adjust)
        if clone.adjust:
            params = clone.adjust.get('params', clone.adjust)
            for name in ('radius', 'offset', 'block', 'size', 'spacing'):
                if name in params:
                    params[name] = max(1, round(params[name]*scale))
        return clone

    def _render_draft(self):
        project, active, board = self.project, self.active, self.active_artboard
        bounds = getattr(self, '_cached_doc_bounds', None)
        margin = self._AB_MARGIN
        width, height = self._doc_bounds()
        edge = getattr(self, '_draft_edge', 768)
        scale = min(1., edge/max(width, height))
        clone = copy.copy(project)
        clone.w, clone.h = max(1, round(project.w*scale)), max(1, round(project.h*scale))
        clone.layers = [self._draft_layer(layer, scale) for layer in project.layers]
        clone.artboards = []
        cloned_active_board = None
        for original in project.artboards:
            item = copy.copy(original)
            item.x, item.y = original.x*scale, original.y*scale
            item.w, item.h = max(1, round(original.w*scale)), max(1, round(original.h*scale))
            item.layers = [self._draft_layer(layer, scale) for layer in original.layers]
            clone.artboards.append(item)
            if original is board:
                cloned_active_board = item
        brush = getattr(self, '_checker_brush', None)
        try:
            self.project, self.active_artboard, self.active = clone, cloned_active_board, None
            self._AB_MARGIN = margin*scale
            self._cached_doc_bounds = (max(1, round(width*scale)), max(1, round(height*scale)))
            if brush is not None:
                self._checker_brush = QBrush(brush)
                self._checker_brush.setTransform(QTransform.fromScale(scale, scale))
            image = self._render_composite(False)
        finally:
            self.project, self.active_artboard, self.active = project, board, active
            self._AB_MARGIN, self._cached_doc_bounds = margin, bounds
            if brush is not None:
                self._checker_brush = brush
        self.view.composite_item.setPixmap(QPixmap.fromImage(image))
        self.view.composite_item.setTransform(QTransform.fromScale(width/image.width(), height/image.height()))
        self._draw_handles()
        self.view.viewport().update()

    def _flush_draft_preview(self):
        if not self._gesture_in_progress():
            self._redraw()
            return
        started = time.perf_counter()
        try:
            self._render_draft()
        except Exception:
            self._draft_failed_gesture = self._gesture_key()
            self._clear_interaction_preview()
            self._redraw()
            return
        duration = time.perf_counter()-started
        self._draft_last_frame = time.perf_counter()
        if duration > .035:
            self._draft_edge = max(256, round(self._draft_edge * max(.6, (.025/duration)**.5)))

    def _redraw_draft_preview(self):
        if not self._gesture_in_progress() or self._text_editing or self._compare_active:
            self._clear_interaction_preview()
            return False
        if getattr(self, '_draft_failed_gesture', None) == self._gesture_key():
            return False
        if not getattr(self, '_draft_active', False):
            self._draft_active = True
            self._draft_source_cache, self._draft_content_cache = {}, {}
            self._draft_edge, self._draft_last_frame = 512, 0.
            if not hasattr(self, '_draft_timer'):
                self._draft_timer = QTimer(self)
                self._draft_timer.setSingleShot(True)
                self._draft_timer.timeout.connect(self._flush_draft_preview)
        elapsed = time.perf_counter()-self._draft_last_frame
        if elapsed < .016:
            if not self._draft_timer.isActive():
                self._draft_timer.start(max(1, round((.016-elapsed)*1000)))
        else:
            self._draft_timer.stop()
            self._flush_draft_preview()
        return True

    def _gesture_key(self):
        return (id(self.project), id(getattr(self, '_move_olds', None)),
                id(self.view._handle_orig), id(self.view._artboard_drag))

    def _preview_layer(self):
        if (self.project.artboards or self._text_editing or self._compare_active or
                len(self.selected) != 1):
            return None
        layer = self.active
        moving = id(layer) in (getattr(self, '_move_olds', None) or {})
        transforming = self.view._handle_drag is not None
        if not (moving or transforming) or layer is None:
            return None
        if (layer.kind != 'image' or layer.pixels is None or not layer.visible or
                layer.mask is not None or layer.clip or layer.blend != 'normal' or
                has_layer_effects(layer) or layer not in self.project.layers):
            return None
        index = self.project.layers.index(layer)
        # Dependent blends and adjustments must see the moving layer's pixels.
        if any(top.visible and (top.kind == 'adjust' or top.clip or top.blend != 'normal')
               for top in self.project.layers[index + 1:]):
            return None
        return layer

    def _clear_interaction_preview(self):
        cleared = False
        if getattr(self, '_draft_active', False):
            self._draft_active = False
            self._draft_timer.stop()
            self._draft_source_cache, self._draft_content_cache = {}, {}
            self._full_render_hint_project = self.project
            cleared = True
        preview = getattr(self, '_interaction_preview', None)
        if preview is None:
            return cleared
        self.view.scene.removeItem(preview['root'])
        self._interaction_preview = None
        self.view.composite_item.show()
        return True

    def _redraw_interaction_preview(self):
        layer = self._preview_layer()
        preview = getattr(self, '_interaction_preview', None)
        if preview is not None and preview['layer'] is not layer:
            self._clear_interaction_preview()
            preview = None
        if layer is None:
            return self._redraw_draft_preview()
        if preview is None:
            width, height = self.project.w, self.project.h
            index = self.project.layers.index(layer)
            below = self.render_stack(self.project.layers[:index], width, height,
                                      None if self.project.transparent else self.project.bg_color)
            if self.project.transparent:
                display = QImage(width, height, QImage.Format.Format_ARGB32)
                painter = QPainter(display)
                self._paint_checker(painter, width, height)
                painter.drawImage(0, 0, below)
                painter.end()
                below = display
            root = QGraphicsRectItem(QRectF(0, 0, width, height))
            root.setPen(QPen(Qt.PenStyle.NoPen))
            root.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            root.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape)
            root.setZValue(-.1)
            root.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.view.scene.addItem(root)

            def pixmap_item(image, z):
                item = QGraphicsPixmapItem(QPixmap.fromImage(image), root)
                item.setShapeMode(QGraphicsPixmapItem.ShapeMode.BoundingRectShape)
                item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                item.setZValue(z)
                return item

            backdrop = pixmap_item(below, 0)
            moving = pixmap_item(self._layer_content(layer), 1)
            above = None
            if index + 1 < len(self.project.layers):
                above = pixmap_item(self.render_stack(self.project.layers[index + 1:], width, height), 2)
            preview = dict(layer=layer, root=root, moving=moving, backdrop=backdrop, above=above)
            self._interaction_preview = preview
            self.view.composite_item.hide()
        preview['moving'].setTransform(layer.canvas_transform())
        preview['moving'].setOpacity(layer.opacity)
        self._draw_handles()
        self.view.viewport().update()
        return True
