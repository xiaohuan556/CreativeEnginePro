"""Editing operations shared by the image workbench UI.

Independent Qt implementation of compositing workflows; no platform code copied.
"""
import copy
import base64
import zlib
import json
import numpy as np
from ui.image_folders import walk_layers
from ui.image_selection_ops import selection_history
from core.image_adjustments import EXTRA_ADJUSTMENTS, apply_development
from PyQt6.QtCore import Qt, QRectF, QPointF, QTimer, QMimeData
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QApplication, QDialog, QFormLayout, QDoubleSpinBox,
                            QDialogButtonBox, QInputDialog, QLabel, QSpinBox, QCheckBox,
                            QPushButton)

ADJUSTMENTS = {
    'exposure': ('曝光', [('exposure', '曝光 EV ×100', -500, 500, 0)]),
    'black_white': ('黑白', [('red', '红色权重', 0, 200, 30), ('green', '绿色权重', 0, 200, 59),
                             ('blue', '蓝色权重', 0, 200, 11)]),
    'invert': ('反相', []),
    'vignette': ('暗角', [('amount', '强度', 0, 100, 35), ('softness', '范围', 10, 100, 70)]),
    'rgb_split': ('RGB 色散', [('offset', '偏移像素', 0, 100, 3)]),
    'scanlines': ('扫描线', [('spacing', '间隔', 2, 50, 4), ('amount', '强度', 0, 100, 30)]),
}
ADJUSTMENTS.update(EXTRA_ADJUSTMENTS)

def apply_extra_adjustment(rgb, kind, params, alpha=None):
    if kind in EXTRA_ADJUSTMENTS:
        return apply_development(rgb, kind, params, alpha)
    if kind == 'exposure':
        return rgb * (2.0 ** (params.get('exposure', 0) / 100.0))
    if kind == 'black_white':
        weights = np.array([params.get('red', 30), params.get('green', 59), params.get('blue', 11)])
        weights = weights / max(1, weights.sum())
        return np.repeat((rgb @ weights)[:, :, None], 3, axis=2)
    if kind == 'invert':
        return 255 - rgb
    if kind == 'vignette':
        h, w = rgb.shape[:2]
        yy, xx = np.ogrid[-1:1:complex(h), -1:1:complex(w)]
        radius = np.sqrt(xx * xx + yy * yy)
        edge = params.get('softness', 70) / 100
        fade = np.clip((radius - (1 - edge)) / max(edge, .01), 0, 1)
        return rgb * (1 - fade[:, :, None] * params.get('amount', 35) / 100)
    if kind == 'rgb_split':
        result = rgb.copy()
        offset = min(int(params.get('offset', 3)), rgb.shape[1] - 1)
        if offset > 0:
            result[:, offset:, 0] = rgb[:, :-offset, 0]
            result[:, :-offset, 2] = rgb[:, offset:, 2]
        return result
    if kind == 'scanlines':
        result = rgb.copy()
        result[::max(2, int(params.get('spacing', 4)))] *= 1 - params.get('amount', 30) / 100
        return result
    return rgb


class WorkbenchOperations:
    _layer_clipboard = None

    def trim_history_memory(self):
        from core.image_history import retained_bytes
        limit = getattr(self,'_history_byte_limit',256*1024*1024)
        live = {id(value) for layer in self._all_layers()
                for value in [layer.pixels, layer.mask, *layer.frames] if value is not None}
        live.update(id(value) for value in (self.selection, self.sel_alpha, self._sel_base) if value is not None)
        while len(self._history)>2 and self._history_idx>1 and retained_bytes(self._history, set(live))>limit:
            self._history.pop(0)
            self._history_idx -= 1

    def set_line_option(self,name,value):
        setattr(self,'shape_'+name,value)
        if self.active and self.active.kind=='shape' and self.active.shape=='line' and not self.active.locked:
            self._queue_property_undo('直线样式')
            setattr(self.active,name,value)
            self._redraw()

    def toggle_mask_link(self):
        if self.active is not None and self.active.mask is not None and not self.active.locked:
            self._push_undo('切换蒙版联动')
            self.active.mask_linked = not self.active.mask_linked
            self.status_tool.setText('蒙版跟随图层变换' if self.active.mask_linked else '蒙版保持在画布原位置')

    def add_filter_layer(self, kind):
        from ui.image_editor import ImageLayer
        name, specs = self.FILTERS[kind]
        before = self._snapshot()
        previous_active, previous_selected = self.active, self.selected[:]
        layer = ImageLayer(name + ' · 滤镜', kind='adjust')
        layer.adjust = {'type': 'filter', 'filter': kind,
                        'params': {key: default for key, _, _, _, default in specs}}
        if self.selection is not None and self.selection.any():
            coverage = self.sel_alpha if self.sel_alpha is not None else self.selection.astype(np.float32)
            layer.mask = np.rint(np.clip(coverage, 0, 1) * 255).astype(np.uint8)
        layers = self._ctx_layers()
        index = layers.index(self.active) + 1 if self.active in layers else len(layers)
        layers.insert(index, layer)
        self._select_layer_only(layer)
        self._refresh_layers()
        if not self.edit_filter_layer(layer, before, new=True):
            layers.remove(layer)
            self.active, self.selected = previous_active, previous_selected
            self._refresh_layers()
            self._redraw()

    def edit_filter_layer(self, layer, before=None, new=False):
        if layer.locked:
            return False
        kind = layer.adjust['filter']
        name, specs = self.FILTERS[kind]
        before = self._snapshot() if before is None else before
        backup = copy.deepcopy(layer.adjust)
        original_opacity, original_visible = layer.opacity, layer.visible
        dialog = QDialog(self)
        dialog.setObjectName('liveFilterDialog')
        dialog.setWindowTitle(name + ' · 无损滤镜层')
        dialog.setMinimumWidth(380)
        form = QFormLayout(dialog)
        hint = QLabel('作用于下方可见图层；原图保留。\n双击滤镜层可重新编辑；有选区时会自动创建蒙版。')
        hint.setWordWrap(True)
        form.addRow(hint)
        controls = {}
        timer = QTimer(dialog)
        timer.setSingleShot(True)
        error = QLabel('')
        error.setWordWrap(True)
        error.setStyleSheet('color:#ff9b89')
        def preview():
            layer.adjust['params'] = {key: control.value() for key, control in controls.items()}
            layer.opacity = strength.value() / 100
            layer.visible = original_visible and enabled.isChecked()
            try:
                self._redraw()
                error.setText('')
                return True
            except Exception as exc:
                error.setText(str(exc))
                return False
        for key, label, lo, hi, default in specs:
            control = QSpinBox()
            control.setObjectName('filter_' + key)
            control.setRange(lo, hi)
            control.setValue(int(layer.adjust.get('params', {}).get(key, default)))
            control.valueChanged.connect(lambda _: timer.start(100))
            controls[key] = control
            form.addRow(label, control)
        strength = QSpinBox()
        strength.setRange(0, 100)
        strength.setSuffix('%')
        strength.setValue(round(original_opacity * 100))
        strength.valueChanged.connect(lambda _: timer.start(100))
        form.addRow('强度', strength)
        enabled = QCheckBox('预览效果')
        enabled.setChecked(True)
        enabled.toggled.connect(lambda _: timer.start(0))
        form.addRow(enabled)
        reset = QPushButton('重置参数')
        def reset_values():
            for key, _, _, _, default in specs:
                controls[key].setValue(default)
            strength.setValue(100)
        reset.clicked.connect(reset_values)
        form.addRow(reset)
        form.addRow(error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('应用')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        def accept():
            timer.stop()
            enabled.setChecked(True)
            timer.stop()
            if preview():
                dialog.accept()
        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        timer.timeout.connect(preview)
        preview()
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        timer.stop()
        layer.visible = original_visible
        if not accepted:
            layer.adjust, layer.opacity = backup, original_opacity
        elif new or layer.adjust != backup or layer.opacity != original_opacity:
            self._push_undo_snapshot('新建无损滤镜层' if new else '修改无损滤镜层', before)
        self._refresh_layers()
        if accepted or not new:
            self._redraw()
        return accepted

    def _filter_layer_list(self, *_):
        if not hasattr(self, 'layer_list'):
            return
        query = self._layer_search.text().strip().casefold()
        kind = self._layer_kind.currentData()
        visible = 0
        for row in range(self.layer_list.count()):
            item = self.layer_list.item(row)
            layer = item.data(Qt.ItemDataRole.UserRole)
            if layer is None:
                continue
            matches = query in layer.name.casefold() and (not kind or layer.kind == kind)
            item.setHidden(not matches)
            visible += int(matches)
        total = len(self._all_layers())
        self.layer_count.setText(f'{visible} / {total}' if query or kind else str(total))

    def _set_navigator_enabled(self, enabled):
        self.view.navigator.enabled = enabled
        self.view.navigator.refresh()

    def crop_geometry(self, start, end, symmetric=False):
        w, h = self._ctx_size()
        ox, oy = self._ab_screen(self.active_artboard) if self.active_artboard else (0, 0)
        sx = min(ox + w, max(ox, start.x()))
        sy = min(oy + h, max(oy, start.y()))
        dx, dy = end.x() - sx, end.y() - sy
        ratio = self._crop_ratio.currentData()
        if ratio:
            if abs(dx) > abs(dy) * ratio:
                dy = (-1 if dy < 0 else 1) * abs(dx) / ratio
            else:
                dx = (-1 if dx < 0 else 1) * abs(dy) * ratio
        max_x = min(sx - ox, ox + w - sx) if symmetric else (ox + w - sx if dx >= 0 else sx - ox)
        max_y = min(sy - oy, oy + h - sy) if symmetric else (oy + h - sy if dy >= 0 else sy - oy)
        factor = min(1., max_x / abs(dx) if dx else 1., max_y / abs(dy) if dy else 1.)
        dx, dy = dx * factor, dy * factor
        if symmetric:
            return QRectF(sx - abs(dx), sy - abs(dy), abs(dx) * 2, abs(dy) * 2)
        return QRectF(QPointF(sx, sy), QPointF(sx + dx, sy + dy)).normalized()

    def crop_from_selection(self):
        if self.selection is None or not self.selection.any():
            return
        ys, xs = np.nonzero(self.selection)
        ox, oy = self._ab_screen(self.active_artboard) if self.active_artboard else (0, 0)
        self._crop_rect = self.crop_geometry(QPointF(xs.min() + ox, ys.min() + oy),
                                            QPointF(xs.max() + 1 + ox, ys.max() + 1 + oy))
        self._preview_crop(self._crop_rect)

    def cancel_crop(self):
        self._crop_rect = None
        self.view.sel_item.setPixmap(QPixmap())
        self._redraw_selection()

    def apply_crop(self):
        rect = getattr(self, '_crop_rect', None)
        if rect is None or rect.width() < 1 or rect.height() < 1:
            return
        if self._crop_output.currentIndex() == 1:
            self._commit_crop(rect)
            self.cancel_crop()
            return
        ox, oy = self._ab_screen(self.active_artboard) if self.active_artboard else (0, 0)
        cw, ch = self._ctx_size()
        x, y = max(0, round(rect.x() - ox)), max(0, round(rect.y() - oy))
        right, bottom = min(cw, round(rect.right() - ox)), min(ch, round(rect.bottom() - oy))
        w, h = right - x, bottom - y
        if w < 1 or h < 1:
            return
        self._push_undo('裁剪画布（保留图层）')
        for layer in walk_layers(self._root_layers()):
            layer.x -= x
            layer.y -= y
            if layer.mask is not None:
                layer.mask = layer.mask[y:bottom, x:right].copy()
        if self.active_artboard:
            self.active_artboard.w, self.active_artboard.h = w, h
        else:
            self.project.w, self.project.h = w, h
            self.project.h_guides = [value - y for value in self.project.h_guides if y <= value <= bottom]
            self.project.v_guides = [value - x for value in self.project.v_guides if x <= value <= right]
        self.selection = self.sel_alpha = self._sel_base = None
        self._invalidate_doc_bounds()
        self.cw.setValue(w)
        self.ch.setValue(h)
        self.cancel_crop()
        self._refresh_layers()
        self._redraw()
        self.view.fit_view()

    def paint_stamp(self, point, erase=False, redraw=True):
        """Vectorized round brush with soft falloff and straight-alpha blending."""
        layer = self.active
        if layer is None or layer.locked:
            return
        mask_mode = self._mask_edit and layer.mask is not None
        if not mask_mode and (layer.kind != 'image' or layer.pixels is None):
            return
        point = self._doc_to_local(point)
        pressure = getattr(self, '_tablet_pressure', 1.)
        radius = max(.5, self.brush_size / 2 * (pressure if getattr(self, 'pressure_size', True) else 1))
        rect = QRectF(point.x() - radius, point.y() - radius, radius * 2, radius * 2)
        if not mask_mode:
            inverse, valid = layer.canvas_transform().inverted()
            if not valid:
                return
            rect = inverse.mapRect(rect)
        height, width = (layer.mask.shape if mask_mode else layer.pixels.shape[:2])
        x0, y0 = max(0, int(np.floor(rect.left()))), max(0, int(np.floor(rect.top())))
        x1, y1 = min(width, int(np.ceil(rect.right())) + 1), min(height, int(np.ceil(rect.bottom())) + 1)
        if x0 >= x1 or y0 >= y1:
            return
        h, w = y1 - y0, x1 - x0
        yy, xx = np.ogrid[y0:y1, x0:x1]
        if mask_mode:
            cx, cy = xx, yy
        else:
            transform = layer.canvas_transform()
            from ui.image_painting import map_grid
            cx, cy = map_grid(transform, xx, yy)
        distance = np.sqrt((cx - point.x()) ** 2 + (cy - point.y()) ** 2) / radius
        hardness = getattr(self, 'brush_hardness', 100) / 100
        weight = ((distance <= 1).astype(np.float32) if hardness >= 1
                  else np.clip((1 - distance) / max(.001, 1 - hardness), 0, 1))
        weight *= getattr(self, 'brush_opacity', 100) / 100
        if getattr(self, 'pressure_opacity', True):
            weight *= pressure
        if self.selection is not None:
            sx = np.broadcast_to(np.rint(cx).astype(int), (h, w))
            sy = np.broadcast_to(np.rint(cy).astype(int), (h, w))
            sh, sw = self.selection.shape
            valid = (sx >= 0) & (sx < sw) & (sy >= 0) & (sy < sh)
            coverage = (self.sel_alpha.astype(np.float32) if self.sel_alpha is not None
                        else self.selection.astype(np.float32))
            weight *= coverage[np.clip(sy, 0, sh - 1), np.clip(sx, 0, sw - 1)] * valid
        if mask_mode:
            target = 0 if erase else 255
            region = layer.mask[y0:y1, x0:x1]
            region[:] = np.rint(region * (1 - weight) + target * weight).astype(np.uint8)
        else:
            affected = weight > 0
            region = layer.pixels[y0:y1, x0:x1]
            source = region[affected].astype(np.float32)
            strength = weight[affected]
            alpha = source[:, 3] / 255
            if erase:
                source[:, 3] = np.rint(alpha * (1 - strength) * 255)
            else:
                out_alpha = strength + alpha * (1 - strength)
                color = np.array([self.fg.red(), self.fg.green(), self.fg.blue()])
                source[:, :3] = (color * strength[:, None] + source[:, :3] *
                                (alpha * (1 - strength))[:, None]) / np.maximum(out_alpha[:, None], 1e-8)
                source[:, 3] = np.rint(out_alpha * 255)
            region[affected] = np.clip(source, 0, 255).astype(np.uint8)
        if redraw:
            self._redraw()

    @staticmethod
    def pack_project(value):
        from core.image_history import FrozenArray
        if isinstance(value, (np.ndarray, FrozenArray)):
            value = np.asarray(value)
            return {'__rgba_array__': True, 'shape': list(value.shape),
                    'dtype': value.dtype.str,
                    'data': base64.b64encode(zlib.compress(value.tobytes())).decode('ascii')}
        if isinstance(value, dict):
            return {key: WorkbenchOperations.pack_project(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [WorkbenchOperations.pack_project(item) for item in value]
        return value

    @staticmethod
    def unpack_project(value):
        if isinstance(value, dict):
            if value.get('__rgba_array__') is True:
                shape = tuple(int(n) for n in value['shape'])
                if not shape or len(shape) > 3 or any(n < 1 for n in shape):
                    raise ValueError('无效的图像尺寸')
                dtype = np.dtype(value.get('dtype', 'uint8'))
                if dtype.kind not in 'bufi' or dtype.itemsize > 8:
                    raise ValueError('无效的图像数据类型')
                return np.frombuffer(zlib.decompress(base64.b64decode(value['data'])),
                                     dtype=dtype).reshape(shape).copy()
            return {key: WorkbenchOperations.unpack_project(item) for key, item in value.items()}
        if isinstance(value, list):
            return [WorkbenchOperations.unpack_project(item) for item in value]
        return value

    def copy_layers(self):
        layers = [layer for layer in self._ctx_layers()
                  if layer in self.selected or layer is self.active]
        if not layers:
            return False
        WorkbenchOperations._layer_clipboard = copy.deepcopy(layers)
        # A rendered image keeps copy useful in other applications too.
        image = self._render_layer_canvas(self.active or layers[-1])
        mime = QMimeData()
        mime.setImageData(image)
        payload = self.pack_project({'layers':[self._ser_layer(layer,with_pixels=True) for layer in layers]})
        mime.setData('application/x-creativeenginepro-layers-v2',json.dumps(payload,ensure_ascii=False).encode('utf-8'))
        QApplication.clipboard().setMimeData(mime)
        return True

    def paste_layers(self):
        mime = QApplication.clipboard().mimeData()
        if mime is None or not mime.hasFormat('application/x-creativeenginepro-layers-v2'):
            return False
        try:
            payload = self.unpack_project(json.loads(bytes(mime.data('application/x-creativeenginepro-layers-v2')).decode('utf-8')))
            layers = [self._deser_layer(item) for item in payload['layers']]
        except (ValueError,TypeError,KeyError):
            return False
        from ui.image_editor import ImageLayer
        self._push_undo("粘贴可编辑图层")
        group_ids = {}
        for layer in walk_layers(layers):
            ImageLayer._counter += 1
            layer.id = ImageLayer._counter
            layer.name += " 副本"
            if layer.group_id is not None:
                if layer.group_id not in group_ids:
                    self._group_counter = getattr(self, '_group_counter', 0) + 1
                    group_ids[layer.group_id] = self._group_counter
                layer.group_id = group_ids[layer.group_id]
        self._ctx_layers().extend(layers)
        self.active = layers[-1]
        self.selected = layers
        self._mask_edit = False
        self._refresh_layers()
        self._redraw()
        return True

    def copy_merged(self):
        image = (self._render_artboard(self.active_artboard) if self.active_artboard
                 else self._render_composite(for_export=True))
        QApplication.clipboard().setImage(image)

    @selection_history('全选画布')
    def select_canvas(self):
        w, h = self._ctx_size()
        self.selection = np.ones((h, w), dtype=bool)
        self.sel_alpha = None
        self._sel_base = None
        self._start_sel_anim()
        self._redraw_selection()

    @selection_history('载入选区')
    def load_layer_selection(self, mask=False):
        if self.active is None:
            return
        from ui.image_editor import numpy_from_qimage
        if mask:
            if self.active.mask is None:
                return
            alpha = self.active.mask.copy()
        else:
            alpha = numpy_from_qimage(self._render_layer_canvas(self.active))[:, :, 3].copy()
        self.selection = alpha > 0
        self.sel_alpha = alpha.astype(np.float32) / 255
        self._sel_base = None
        self._start_sel_anim()
        self._redraw_selection()

    @selection_history('扩展 / 收缩选区')
    def modify_selection(self, expand=True, radius=None):
        if self.selection is None:
            return
        if radius is None:
            radius, ok = QInputDialog.getInt(self, "扩展选区" if expand else "收缩选区",
                                            "像素", 5, 1, 500)
            if not ok:
                return
        import cv2
        size = int(radius) * 2 + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        fn = cv2.dilate if expand else cv2.erode
        self.selection = fn(self.selection.astype(np.uint8), kernel,
                            borderType=cv2.BORDER_CONSTANT, borderValue=0).astype(bool)
        self.sel_alpha = None
        self._sel_base = None
        self._redraw_selection()

    def mask_operation(self, operation):
        layer = self.active
        if layer is None or layer.locked:
            return
        if operation == 'selection':
            if self.selection is None:
                return
            self._push_undo("选区转蒙版")
            layer.mask = (np.clip(self.sel_alpha * 255, 0, 255).astype(np.uint8) if self.sel_alpha is not None
                          else self.selection.astype(np.uint8) * 255)
        elif layer.mask is not None:
            self._push_undo("修改蒙版")
            if operation == 'invert':
                layer.mask = 255 - layer.mask
            elif operation == 'white':
                layer.mask.fill(255)
            elif operation == 'black':
                layer.mask.fill(0)
        self._refresh_layers()
        self._redraw()

    def exact_transform(self):
        layer = self.active
        if layer is None or layer.locked or layer.kind == 'adjust':
            return
        targets = self._expanded_selection() or [layer]
        box = self._layer_bbox(layer)
        for node in self.selected:
            box = box.united(self._layer_bbox(node))
        origin = box.center() if layer.kind == 'group' or len(self.selected) > 1 else QPointF(layer.x, layer.y)
        old_scale, old_rotation = layer.scale, layer.rotation
        states = [(node, node.x, node.y, node.scale, node.rotation, node.mask.copy() if node.mask is not None else None)
                  for node in targets]
        dialog = QDialog(self)
        dialog.setWindowTitle("精确变换 · " + layer.name)
        form = QFormLayout(dialog)
        controls = {}
        for key, label, lo, hi, value in (
            ('x', '中心 X', -100000, 100000, origin.x()),
            ('y', '中心 Y', -100000, 100000, origin.y()),
            ('scale', '缩放 %', 0.01, 100000, layer.scale * 100),
            ('rotation', '角度 °', -3600, 3600, layer.rotation)):
            control = QDoubleSpinBox()
            control.setRange(lo, hi)
            control.setDecimals(2)
            control.setValue(value)
            form.addRow(label, control)
            controls[key] = control
        if layer.kind != 'group' and len(self.selected)<=1:
            for key,label in (('stretch_x','横向比例 %'),('stretch_y','纵向比例 %')):
                control = QDoubleSpinBox()
                control.setRange(.01,100000)
                control.setValue(getattr(layer,key)*100)
                form.addRow(label,control)
                controls[key] = control
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._push_undo("精确变换")
        self.transform_nodes(states, origin, QPointF(controls['x'].value(), controls['y'].value()),
                             controls['scale'].value() / 100 / max(old_scale, .0001),
                             controls['rotation'].value() - old_rotation)
        for key in ('stretch_x','stretch_y'):
            if key in controls:
                setattr(layer,key,controls[key].value()/100)
        self._redraw()

    def trim_transparent(self):
        from ui.image_editor import numpy_from_qimage
        image = (self._render_artboard(self.active_artboard) if self.active_artboard
                 else self._render_composite(for_export=True))
        alpha = numpy_from_qimage(image)[:, :, 3]
        ys, xs = np.nonzero(alpha)
        if not len(xs):
            self.status_tool.setText("画布完全透明，没有可裁切的内容")
            return
        x, y = int(xs.min()), int(ys.min())
        w, h = int(xs.max()) - x + 1, int(ys.max()) - y + 1
        if (x, y, w, h) == (0, 0, image.width(), image.height()):
            return
        self._push_undo("裁切透明边缘")
        for layer in walk_layers(self._root_layers()):
            layer.x -= x
            layer.y -= y
            if layer.mask is not None:
                layer.mask = layer.mask[y:y+h, x:x+w].copy()
        if self.active_artboard:
            self.active_artboard.w, self.active_artboard.h = w, h
        else:
            self.project.w, self.project.h = w, h
        self.selection = None
        self.sel_alpha = None
        self._sel_base = None
        self._invalidate_doc_bounds()
        self.cw.setValue(w)
        self.ch.setValue(h)
        self._refresh_layers()
        self._redraw()
        self.view.fit_view()

    def configure_grid(self):
        value, ok = QInputDialog.getInt(self, "网格设置", "网格间距（像素）",
                                       self.project.grid_size, 2, 2000)
        if ok:
            self._push_undo("设置网格间距")
            self.project.grid_size = value
            self._redraw()

    def blur_mask(self):
        if self.active is None or self.active.mask is None or self.active.locked:
            return
        radius, ok = QInputDialog.getInt(self, "柔化蒙版边缘", "半径（像素）", 5, 1, 200)
        if ok:
            import cv2
            self._push_undo("柔化蒙版")
            self.active.mask = cv2.GaussianBlur(self.active.mask, (0, 0), radius)
            self._redraw()

    def toggle_focus_canvas(self):
        focused = not getattr(self, '_canvas_focused', False)
        self._canvas_focused = focused
        if focused:
            self._focus_sizes = self.main_split.sizes()
        for widget in self._focus_panels:
            widget.setVisible(not focused)
        if not focused:
            self.main_split.setSizes(self._focus_sizes)
        self.view.setFocus()

    def repeat_filter(self):
        last = getattr(self, '_last_filter', None)
        layer = self.active
        if not last or layer is None or layer.kind != 'image' or layer.locked:
            return
        kind, params = last
        original = layer.pixels.copy()
        result = self._filter_apply(original, kind, params)
        self._push_undo("再次应用滤镜")
        layer.pixels = self._confine_to_selection(layer, original, result)
        self._redraw()
