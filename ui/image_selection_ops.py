"""Subject masks and point-guided object selection."""
import numpy as np
from functools import wraps
from PyQt6.QtCore import QPointF, Qt


SELECTION_MODES = (
    ('new', '新选区', '替换原有选区'),
    ('add', '+ 添加', '添加到选区；按住 Shift 临时使用'),
    ('sub', '− 减去', '从选区中减去；按住 Alt 临时使用'),
    ('intersect', '∩ 交叉', '只保留重叠部分；按住 Shift+Alt 临时使用'),
)


def combine_selection_masks(base, mask, mode):
    mask = np.asarray(mask, dtype=bool)
    if mode not in {item[0] for item in SELECTION_MODES}:
        raise ValueError('Unknown selection mode: ' + str(mode))
    if base is None or base.shape != mask.shape:
        base = np.zeros_like(mask)
    if mode == 'new':
        return mask.copy()
    if mode == 'add':
        return base | mask
    if mode == 'sub':
        return base & ~mask
    return base & mask


def selection_history(name):
    def decorate(operation):
        @wraps(operation)
        def wrapped(self, *args, **kwargs):
            if getattr(self, '_selection_history_depth', 0):
                return operation(self, *args, **kwargs)
            before = self._snapshot(metadata_only=True)
            self._selection_history_depth = 1
            try:
                result = operation(self, *args, **kwargs)
            finally:
                self._selection_history_depth = 0
            self.commit_selection_history(before, name(self, *args, **kwargs) if callable(name) else name)
            return result
        return wrapped
    return decorate


class ImageSelectionOperations:
    def quick_select_brush_point(self, point):
        import cv2
        from PyQt6.QtGui import QPainterPath, QPainterPathStroker
        from core.image_effects import pixel_matrix
        layer = self.active
        if layer is None or layer.kind != 'image' or layer.pixels is None:
            return
        point = self._doc_to_local(point)
        inverse, valid = layer.canvas_transform().inverted()
        if not valid:
            return
        radius = max(.5, self.brush_size / 2.)
        previous = getattr(self, '_qs_last_point', None) or point
        path = QPainterPath(previous)
        path.lineTo(point)
        stroker = QPainterPathStroker()
        stroker.setWidth(radius * 2)
        stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
        footprint = stroker.createStroke(path)
        if previous == point:
            footprint.addEllipse(point, radius, radius)
        polygon = inverse.map(footprint).toFillPolygon()
        if not polygon:
            return
        height, width = layer.pixels.shape[:2]
        box = polygon.boundingRect().toAlignedRect()
        x0, y0 = max(0, box.left()), max(0, box.top())
        x1, y1 = min(width, box.right()+1), min(height, box.bottom()+1)
        self._qs_last_point = QPointF(point)
        if x1 <= x0 or y1 <= y0:
            return
        touched = np.zeros((y1-y0, x1-x0), np.uint8)
        vertices = np.rint([[p.x()-x0, p.y()-y0] for p in polygon]).astype(np.int32)
        cv2.fillPoly(touched, [vertices], 1)
        touched &= (layer.pixels[y0:y1, x0:x1, 3] > 0).astype(np.uint8)
        if not touched.any():
            return
        if getattr(self, '_qs_source_layer', None) is not layer:
            self._qs_source_layer = layer
            self._qs_rgb = np.ascontiguousarray(layer.pixels[:, :, :3])
            self._qs_flood = np.zeros((height+2, width+2), np.uint8)
            self._qs_flood[1:-1, 1:-1][layer.pixels[:, :, 3] == 0] = 1
            self._qs_layer_mask = np.zeros((height, width), bool)
        # Sample across the actual brush footprint; preserve thin features swept
        # between mouse events even if their color is not one of the seeds.
        ys, xs = np.where(touched)
        indices = np.linspace(0, len(xs)-1, min(9, len(xs)), dtype=int)
        tolerance = max(self.tolerance, 24)
        flags = 4 | (255 << 8) | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE
        for index in indices:
            x, y = int(xs[index]+x0), int(ys[index]+y0)
            if self._qs_flood[y+1, x+1]:
                continue
            cv2.floodFill(self._qs_rgb, self._qs_flood, (x, y), 0,
                          (tolerance,)*3, (tolerance,)*3, flags)
        self._qs_layer_mask |= self._qs_flood[1:-1, 1:-1] == 255
        self._qs_layer_mask[y0:y1, x0:x1] |= touched.astype(bool)
        transform = layer.canvas_transform()
        matrix = np.array([[transform.m11(), transform.m21(), transform.m31()],
                           [transform.m12(), transform.m22(), transform.m32()],
                           [transform.m13(), transform.m23(), transform.m33()]])
        w, h = self._ctx_size()
        self._qs_mask = cv2.warpPerspective(self._qs_layer_mask.astype(np.uint8), pixel_matrix(matrix),
                                           (w, h), flags=cv2.INTER_NEAREST).astype(bool)
        self.selection = combine_selection_masks(self._qs_base, self._qs_mask, self._qs_mode)
        self.sel_alpha = None
        self._sel_base = self.selection.copy()
        self._redraw_selection()

    def commit_selection_history(self, before, name):
        def same(old, current):
            return (old is None and current is None or
                    old is not None and current is not None and np.array_equal(np.asarray(old), current))
        if (any(not same(before.get(key), getattr(self, attr)) for key, attr in
                (('selection', 'selection'), ('sel_alpha', 'sel_alpha'), ('sel_base', '_sel_base')))
                or before.get('feather', self.feather) != self.feather):
            self._push_undo_snapshot(name, before)

    def build_selection_mode_controls(self):
        from PyQt6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QButtonGroup
        controls = QWidget()
        layout = QHBoxLayout(controls)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self._selection_buttons = {}
        self._selection_button_group = QButtonGroup(controls)
        self._selection_button_group.setExclusive(True)
        for mode, label, tip in SELECTION_MODES:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(mode == self.selection_mode)
            button.setFixedHeight(28)
            button.setToolTip(tip)
            button.setAccessibleName(tip.split('；')[0])
            button.setStyleSheet('QPushButton{color:#ddd;background:#303238;border:1px solid #50535a;'
                                'border-radius:3px;padding:3px 7px;}'
                                'QPushButton:checked{color:white;background:#296ccc;border-color:#74b1ff;}')
            button.clicked.connect(lambda checked, value=mode: self.set_selection_mode(value))
            self._selection_button_group.addButton(button)
            self._selection_buttons[mode] = button
            layout.addWidget(button)
        controls.setToolTip('Shift 添加 · Alt 减去 · Shift+Alt 交叉；松开按键恢复按钮选定的模式')
        return controls

    def set_selection_mode(self, mode):
        if mode not in self._selection_buttons:
            raise ValueError('Unknown selection mode: ' + str(mode))
        self.selection_mode = mode
        self._selection_buttons[mode].setChecked(True)
        self.status_tool.setText(dict((value, tip) for value, _, tip in SELECTION_MODES)[mode])

    def selection_mode_for_modifiers(self, modifiers):
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)
        if shift and alt:
            return 'intersect'
        if shift:
            return 'add'
        if alt:
            return 'sub'
        return self.selection_mode

    def wand_region(self, pixels, y, x, tolerance):
        h, w = pixels.shape[:2]
        if not (0 <= x < w and 0 <= y < h):
            return np.zeros((h, w), bool)
        if self.wand_contiguous:
            return self._flood(pixels, y, x, tolerance)
        if pixels[y, x, 3] == 0:
            return pixels[:, :, 3] == 0
        delta = np.abs(pixels[:, :, :3].astype(np.int16) - pixels[y, x, :3].astype(np.int16))
        return np.all(delta <= tolerance, axis=2) & (pixels[:, :, 3] > 0)

    def select_subject(self):
        from ui.image_editor import _RembgWorker,numpy_from_qimage
        if getattr(self,'_subject_busy',False) or not self._ensure_rembg():
            return
        image = self._render_artboard(self.active_artboard) if self.active_artboard else self._render_composite(True)
        pixels = numpy_from_qimage(image)
        self._subject_busy = True
        self._subject_project = self.project
        self._subject_board = self.active_artboard
        self._subject_worker = _RembgWorker(pixels,use_subprocess=self._rembg_subproc,python_exe=self._rembg_python)
        self._subject_worker.finished.connect(self._subject_finished)
        self._subject_worker.error.connect(self._subject_failed)
        self._subject_worker.finished.connect(self._subject_worker.deleteLater)
        self._subject_worker.error.connect(self._subject_worker.deleteLater)
        self.status_tool.setText('正在识别主体…')
        self._subject_worker.start()

    @selection_history('选择主体')
    def _subject_finished(self,pixels):
        self._subject_busy = False
        if self.project is not self._subject_project or self.active_artboard is not self._subject_board:
            self.status_tool.setText('主体结果已忽略：当前文档或画板已切换')
            return
        self.sel_alpha = pixels[:, :, 3].astype(np.float32)/255
        self.selection = self.sel_alpha>.05
        self._sel_base = self.selection.copy()
        self._start_sel_anim()
        self._redraw_selection()
        self.status_tool.setText('已选择主体')

    def _subject_failed(self,message):
        self._subject_busy = False
        self.status_tool.setText('主体识别失败：'+str(message))

    @selection_history('选择对象')
    def object_select(self,point):
        import cv2
        from ui.image_editor import numpy_from_qimage
        image = self._render_artboard(self.active_artboard) if self.active_artboard else self._render_composite(True)
        rgba = numpy_from_qimage(image)
        rgb = rgba[:, :, :3].copy()
        h,w = rgb.shape[:2]
        point = self._doc_to_local(point)
        x,y = round(point.x()),round(point.y())
        if not 0<=x<w or not 0<=y<h or min(w,h)<4:
            return
        delta = np.linalg.norm(rgb.astype(np.float32)-rgb[y,x].astype(np.float32),axis=2)
        mask = np.full((h,w),cv2.GC_PR_BGD,np.uint8)
        mask[delta<max(10,self.tolerance)] = cv2.GC_PR_FGD
        mask[[0,-1],:] = cv2.GC_BGD
        mask[:,[0,-1]] = cv2.GC_BGD
        radius = max(1,min(w,h)//100)
        cv2.circle(mask,(x,y),radius,cv2.GC_FGD,-1)
        try:
            cv2.grabCut(rgb,mask,None,np.zeros((1,65),np.float64),np.zeros((1,65),np.float64),3,cv2.GC_INIT_WITH_MASK)
            selected = (mask==cv2.GC_FGD)|(mask==cv2.GC_PR_FGD)
        except cv2.error:
            selected = delta<max(10,self.tolerance)
        components,labels = cv2.connectedComponents(selected.astype(np.uint8))
        label = labels[y,x]
        selected = labels==label if label else selected
        selected &= rgba[:, :, 3]>0
        self._combine_mask(selected,self._sel_combine)
        self._start_sel_anim()
        self._redraw_selection()

