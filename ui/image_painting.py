"""Continuous, pressure-aware brush and clone gestures."""
import math
import numpy as np
from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtWidgets import QMessageBox


def map_grid(transform, x, y):
    denominator = transform.m13()*x + transform.m23()*y + transform.m33()
    denominator = np.where(np.abs(denominator) < 1e-8, 1e-8, denominator)
    return ((transform.m11()*x + transform.m21()*y + transform.dx()) / denominator,
            (transform.m12()*x + transform.m22()*y + transform.dy()) / denominator)


class ImagePaintingOperations:
    def _confine_to_selection(self,layer,original,transformed):
        if self.selection is None or not self.selection.any():
            return transformed
        import cv2
        from core.image_effects import mix_rgba
        height,width = original.shape[:2]
        yy,xx = np.mgrid[:height,:width]
        cx,cy = map_grid(layer.canvas_transform(),xx,yy)
        coverage = self.sel_alpha if self.sel_alpha is not None else self.selection.astype(np.float32)
        local = cv2.remap(coverage.astype(np.float32),cx.astype(np.float32),cy.astype(np.float32),
                          cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT,borderValue=0)
        return mix_rgba(original,transformed,local[:, :, None])

    def _paint_segment(self, point, stamp, force=False):
        last = getattr(self, '_brush_last_point', None)
        if last is None:
            target = QPointF(point)
            stamp(target)
        else:
            smooth = 0 if force else getattr(self, 'brush_smoothing', 0)
            factor = 1 - smooth/125
            target = QPointF(last.x() + (point.x()-last.x())*factor, last.y() + (point.y()-last.y())*factor)
            distance = math.hypot(target.x()-last.x(), target.y()-last.y())
            spacing = max(.5, self.brush_size * .15 * getattr(self, '_tablet_pressure', 1.))
            count = max(1, math.ceil(distance / spacing))
            for index in range(1, count+1):
                fraction = index/count
                stamp(QPointF(last.x()+(target.x()-last.x())*fraction, last.y()+(target.y()-last.y())*fraction))
        self._brush_last_point = target
        self._redraw()

    def _stroke_point(self, point, event=None, force=False):
        from ui.image_editor import Tool
        self._paint_segment(point, lambda position:self.paint_stamp(position, self.tool == Tool.ERASER, redraw=False), force)

    def _set_clone_source(self, point):
        if self.active is None or self.active.kind != 'image':
            return
        self._clone_source_canvas = self._doc_to_local(point)
        self._clone_offset = None
        self._clone_source_warned = False

    def begin_clone_stroke(self, point):
        if not getattr(self, 'clone_aligned', True):
            self._clone_offset = None
        self._brush_last_point = None
        if getattr(self, 'clone_sample_all', False):
            from ui.image_editor import numpy_from_qimage
            image = self._render_artboard(self.active_artboard) if self.active_artboard else self._render_composite(True)
            self._clone_sample = numpy_from_qimage(image)
        else:
            self._clone_sample = self.active.pixels.copy() if self.active and self.active.pixels is not None else None

    def _stroke_clone(self, point, event=None):
        if self.active is None or self.active.kind != 'image' or self.active.locked:
            return
        if not hasattr(self, '_clone_source_canvas'):
            if not getattr(self, '_clone_source_warned', False):
                self._clone_source_warned = True
                QMessageBox.information(self, '克隆取样', '按住 Alt 点击源位置，然后在目标位置绘制。')
            return
        self._paint_segment(point, self._clone_stamp)

    def _clone_stamp(self, point):
        import cv2
        from core.image_effects import mix_rgba
        from ui.image_editor import Tool
        layer = self.active
        sample = getattr(self, '_clone_sample', None)
        if sample is None:
            return
        point = self._doc_to_local(point)
        if self._clone_offset is None:
            self._clone_offset = self._clone_source_canvas - point
        inverse, valid = layer.canvas_transform().inverted()
        if not valid:
            return
        radius = max(.5, self.brush_size/2)
        rect = inverse.mapRect(QRectF(point.x()-radius, point.y()-radius, radius*2, radius*2))
        x0, y0 = max(0, math.floor(rect.left())), max(0, math.floor(rect.top()))
        x1, y1 = min(layer.w, math.ceil(rect.right())+1), min(layer.h, math.ceil(rect.bottom())+1)
        if x0 >= x1 or y0 >= y1:
            return
        yy, xx = np.mgrid[y0:y1, x0:x1]
        canvas_x, canvas_y = map_grid(layer.canvas_transform(), xx, yy)
        source_x, source_y = canvas_x+self._clone_offset.x(), canvas_y+self._clone_offset.y()
        if not getattr(self, 'clone_sample_all', False):
            source_x, source_y = map_grid(inverse, source_x, source_y)
        sampled = cv2.remap(sample, source_x.astype(np.float32), source_y.astype(np.float32), cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=(0,0,0,0))
        distance = np.hypot(canvas_x-point.x(), canvas_y-point.y()) / radius
        hardness = self.brush_hardness / 100
        weight = (distance <= 1).astype(np.float32) if hardness == 1 else np.clip((1-distance)/max(.001,1-hardness),0,1)
        weight *= self.brush_opacity/100
        if self.selection is not None:
            h, w = self.selection.shape
            cx, cy = np.rint(canvas_x).astype(int), np.rint(canvas_y).astype(int)
            valid = (cx >= 0) & (cx < w) & (cy >= 0) & (cy < h)
            coverage = self.sel_alpha if self.sel_alpha is not None else self.selection
            weight *= coverage[np.clip(cy,0,h-1), np.clip(cx,0,w-1)] * valid
        region = layer.pixels[y0:y1, x0:x1]
        if self.tool == Tool.HEAL:
            source_lum = sampled[:, :, :3].mean(axis=2, keepdims=True)
            target_lum = region[:, :, :3].mean(axis=2, keepdims=True)
            ratio = np.where(target_lum > 0, target_lum/np.maximum(source_lum,1), 1)
            sampled[:, :, :3] = np.clip(sampled[:, :, :3]*ratio,0,255).astype(np.uint8)
        region[:] = mix_rgba(region, sampled, weight[:, :, None])
