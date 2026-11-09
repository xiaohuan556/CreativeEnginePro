"""Document-aware grids, snapping and edge trimming."""
import numpy as np
from core.image_effects import pixel_matrix
from PyQt6.QtCore import Qt,QRectF,QPointF
from PyQt6.QtGui import QImage,QPainter,QPen,QColor,QPixmap
from PyQt6.QtWidgets import QDialog,QFormLayout,QSpinBox,QCheckBox,QComboBox,QDialogButtonBox


class ImageLayoutOperations:
    def toggle_pixel_grid(self):
        self.show_pixel_grid = not self.show_pixel_grid
        self.view.viewport().update()

    def _rotate_canvas(self,angle_deg,swap=False):
        import math,cv2
        from ui.image_folders import walk_layers
        self._push_undo('旋转画布')
        width,height = self._ctx_size()
        new_width,new_height = (height,width) if swap else (width,height)
        angle = math.radians(angle_deg)
        a,b = math.cos(angle),math.sin(angle)
        tx,ty = new_width/2-a*width/2+b*height/2,new_height/2-b*width/2-a*height/2
        matrix = np.float32([[a,-b,tx],[b,a,ty]])
        for layer in walk_layers(self._root_layers()):
            x,y = layer.x,layer.y
            layer.x,layer.y = a*x-b*y+tx,b*x+a*y+ty
            layer.rotation += angle_deg
            if layer.mask is not None:
                layer.mask = cv2.warpAffine(layer.mask,pixel_matrix(matrix),(new_width,new_height))
        if self.active_artboard:
            self.active_artboard.w,self.active_artboard.h = new_width,new_height
        else:
            self.project.w,self.project.h = new_width,new_height
            old_h,old_v = self.project.h_guides,self.project.v_guides
            if abs(a)<.0001:
                self.project.h_guides = [b*x+ty for x in old_v]
                self.project.v_guides = [-b*y+tx for y in old_h]
            elif abs(b)<.0001:
                self.project.h_guides = [a*y+ty for y in old_h]
                self.project.v_guides = [a*x+tx for x in old_v]
            else:
                self.project.h_guides,self.project.v_guides = [],[]
        self.selection = self.sel_alpha = self._sel_base = None
        self.cancel_crop()
        self.cw.setValue(new_width)
        self.ch.setValue(new_height)
        self._invalidate_doc_bounds()
        self._refresh_layers()
        self._redraw()
        self.view.fit_view()

    def _flip_horizontal(self,layer):
        self._flip_nodes(layer,True)

    def _flip_vertical(self,layer):
        self._flip_nodes(layer,False)

    def _flip_nodes(self,layer,horizontal):
        import cv2
        from ui.image_folders import walk_layers
        if layer.locked:
            return
        self._push_undo('水平翻转' if horizontal else '垂直翻转')
        box = self._layer_bbox(layer)
        cx,cy = box.center().x(),box.center().y()
        matrix = np.float32([[-1,0,2*cx],[0,1,0]]) if horizontal else np.float32([[1,0,0],[0,-1,2*cy]])
        for node in walk_layers([layer]):
            if horizontal:
                node.x = 2*cx-node.x
                node.flip_x = not node.flip_x
            else:
                node.y = 2*cy-node.y
                node.flip_y = not node.flip_y
            node.rotation = -node.rotation
            if node.mask is not None and node.mask_linked:
                mask = node.mask
                node.mask = cv2.warpAffine(mask,pixel_matrix(matrix),(mask.shape[1],mask.shape[0]))
        self._redraw()

    def resize_image_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('缩放图像尺寸')
        form = QFormLayout(dialog)
        old_w,old_h = self._ctx_size()
        width,height = QSpinBox(),QSpinBox()
        for spin,value in ((width,old_w),(height,old_h)):
            spin.setRange(1,30000)
            spin.setValue(value)
        linked = QCheckBox('保持宽高比')
        linked.setChecked(True)
        def update_other(value,target,ratio):
            if linked.isChecked():
                target.blockSignals(True)
                target.setValue(max(1,round(value*ratio)))
                target.blockSignals(False)
        width.valueChanged.connect(lambda value:update_other(value,height,old_h/old_w))
        height.valueChanged.connect(lambda value:update_other(value,width,old_w/old_h))
        form.addRow('宽度',width)
        form.addRow('高度',height)
        form.addRow(linked)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self.resize_image(width.value(),height.value())

    def resize_image(self,width,height):
        import cv2
        from core.image_import import check_size
        from ui.image_folders import walk_layers
        check_size(width,height)
        old_w,old_h = self._ctx_size()
        if (width,height)==(old_w,old_h):
            return
        sx,sy = width/old_w,height/old_h
        self._push_undo('缩放图像尺寸')
        for layer in walk_layers(self._root_layers()):
            corners = None
            if layer.kind!='group' and (layer.rotation%180 or layer.quad):
                transform = layer.canvas_transform()
                corners = [transform.map(QPointF(x,y)) for x,y in ((0,0),(layer.w,0),(layer.w,layer.h),(0,layer.h))]
            layer.x,layer.y = layer.x*sx,layer.y*sy
            if layer.kind!='group':
                if corners is not None:
                    self.apply_world_corners(layer,[(p.x()*sx,p.y()*sy) for p in corners])
                else:
                    layer.stretch_x *= sx
                    layer.stretch_y *= sy
            if layer.mask is not None:
                layer.mask = cv2.resize(layer.mask,(width,height),interpolation=cv2.INTER_LINEAR)
        if self.active_artboard:
            self.active_artboard.w,self.active_artboard.h = width,height
        else:
            self.project.w,self.project.h = width,height
            self.project.v_guides = [value*sx for value in self.project.v_guides]
            self.project.h_guides = [value*sy for value in self.project.h_guides]
        self.selection = self.sel_alpha = self._sel_base = None
        self.cancel_crop()
        self.cw.setValue(width)
        self.ch.setValue(height)
        self._invalidate_doc_bounds()
        self._refresh_layers()
        self._redraw()
        self.view.fit_view()

    def configure_grid(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('网格与吸附')
        form = QFormLayout(dialog)
        spacing,subdivisions = QSpinBox(),QSpinBox()
        spacing.setRange(2,2000)
        spacing.setValue(self.project.grid_size)
        subdivisions.setRange(1,20)
        subdivisions.setValue(self.project.grid_subdivisions)
        form.addRow('主网格间距（像素）',spacing)
        form.addRow('细分',subdivisions)
        controls = {}
        for key,label in (('grid','吸附网格'),('guides','吸附参考线'),('layers','吸附图层边缘与中心'),('canvas','吸附画布边缘与中心')):
            check = QCheckBox(label)
            check.setChecked(self.project.snap_targets[key])
            controls[key] = check
            form.addRow(check)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._push_undo('网格与吸附设置')
            self.project.grid_size,self.project.grid_subdivisions = spacing.value(),subdivisions.value()
            self.project.snap_targets = {key:check.isChecked() for key,check in controls.items()}
            self._redraw()

    def _draw_guides_and_grid(self):
        width,height = self._ctx_size()
        ox,oy = self._ab_screen(self.active_artboard) if self.active_artboard else (0,0)
        key = (width, height, ox, oy, self.project.show_grid, self.project.grid_size,
               self.project.grid_subdivisions, tuple(self.project.h_guides), tuple(self.project.v_guides))
        if getattr(self, '_guide_render_key', None) == key:
            return
        self._guide_render_key = key
        if not self.project.show_grid and not self.project.h_guides and not self.project.v_guides:
            self.view.guide_item.setPixmap(QPixmap())
            return
        image = QImage(width,height,QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        if self.project.show_grid:
            divisions = self.project.grid_subdivisions
            step = max(1.,self.project.grid_size/divisions)
            for axis,extent in ((0,width),(1,height)):
                for index,position in enumerate(np.arange(step,extent,step),1):
                    painter.setPen(QPen(QColor(255,255,255,55 if index%divisions==0 else 20),.7))
                    if axis==0:
                        painter.drawLine(QPointF(position,0),QPointF(position,height))
                    else:
                        painter.drawLine(QPointF(0,position),QPointF(width,position))
        painter.setPen(QPen(QColor(0,234,255,180),1))
        for value in self.project.h_guides:
            painter.drawLine(QPointF(0,value-oy),QPointF(width,value-oy))
        for value in self.project.v_guides:
            painter.drawLine(QPointF(value-ox,0),QPointF(value-ox,height))
        painter.end()
        self.view.guide_item.setOffset(ox,oy)
        self.view.guide_item.setPixmap(QPixmap.fromImage(image))

    def _snap_pt(self,point,snap_dist=8):
        if not self.snap_on:
            return point
        ox,oy = self._ab_screen(self.active_artboard) if self.active_artboard else (0,0)
        x,y = point.x(),point.y()
        xs,ys = [],[]
        flags = self.project.snap_targets
        if flags['grid'] and self.project.show_grid:
            step = max(1.,self.project.grid_size/self.project.grid_subdivisions)
            xs.append(round((x-ox)/step)*step+ox)
            ys.append(round((y-oy)/step)*step+oy)
        if flags['guides']:
            xs.extend(self.project.v_guides)
            ys.extend(self.project.h_guides)
        tolerance = snap_dist/max(abs(self.view.transform().m11()),.1)
        def nearest(value,candidates):
            close = min(candidates,key=lambda candidate:abs(candidate-value)) if candidates else value
            return close if abs(close-value)<=tolerance else value
        return QPointF(nearest(x,xs),nearest(y,ys))

    def _snap_bbox_lines(self,candidate_x,candidate_y,exclude=None):
        if not self.snap_on:
            return 0.,0.,[]
        flags = self.project.snap_targets
        width,height = self._ctx_size()
        ox,oy = self._ab_screen(self.active_artboard) if self.active_artboard else (0,0)
        xs,ys = ([0,width/2,width],[0,height/2,height]) if flags['canvas'] else ([],[])
        if flags['guides']:
            xs += [value-ox for value in self.project.v_guides]
            ys += [value-oy for value in self.project.h_guides]
        exclude = set() if exclude is None else exclude
        if flags['layers']:
            def add(layers):
                for layer in layers:
                    if not layer.visible or layer in exclude:
                        continue
                    if layer.kind != 'adjust':
                        box = self._layer_bbox(layer)
                        xs.extend([box.left(),box.center().x(),box.right()])
                        ys.extend([box.top(),box.center().y(),box.bottom()])
                    add(layer.children)
            add(self._root_layers())
        if flags['grid'] and self.project.show_grid:
            step = max(1.,self.project.grid_size/self.project.grid_subdivisions)
            xs += [round(value/step)*step for value in candidate_x]
            ys += [round(value/step)*step for value in candidate_y]
        tolerance = 8/max(abs(self.view.transform().m11()),.1)
        def delta(candidates,targets):
            choices = [(target-value,target) for value in candidates for target in targets if abs(target-value)<=tolerance]
            return min(choices,key=lambda item:abs(item[0])) if choices else (0.,None)
        dx,x = delta(candidate_x,xs)
        dy,y = delta(candidate_y,ys)
        return dx,dy,([('v',x)] if x is not None else [])+([('h',y)] if y is not None else [])

    def trim_edges_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('裁切画布边缘')
        form = QFormLayout(dialog)
        basis = QComboBox()
        basis.addItems(['透明像素','左上角颜色','右下角颜色'])
        tolerance = QSpinBox()
        tolerance.setRange(0,255)
        form.addRow('依据',basis)
        form.addRow('容差',tolerance)
        sides = []
        for name in ('上','下','左','右'):
            check = QCheckBox('裁切'+name+'边')
            check.setChecked(True)
            form.addRow(check)
            sides.append(check)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.trim_canvas(basis.currentIndex(),tolerance.value(),[check.isChecked() for check in sides])

    def trim_canvas(self,basis=0,tolerance=0,sides=(True,True,True,True)):
        from ui.image_editor import numpy_from_qimage
        image = self._render_artboard(self.active_artboard) if self.active_artboard else self._render_composite(True)
        pixels = numpy_from_qimage(image)
        if basis == 0:
            keep = pixels[:, :, 3]>tolerance
        else:
            reference = pixels[0,0] if basis==1 else pixels[-1,-1]
            keep = np.any(abs(pixels.astype(np.int16)-reference.astype(np.int16))>tolerance,axis=2)
        ys,xs = np.nonzero(keep)
        if not len(xs) or not any(sides):
            self.status_tool.setText('没有可裁切的边缘')
            return
        x0,x1 = (int(xs.min()) if sides[2] else 0),(int(xs.max())+1 if sides[3] else image.width())
        y0,y1 = (int(ys.min()) if sides[0] else 0),(int(ys.max())+1 if sides[1] else image.height())
        ox,oy = self._ab_screen(self.active_artboard) if self.active_artboard else (0,0)
        self._crop_rect = QRectF(x0+ox,y0+oy,x1-x0,y1-y0)
        previous = self._crop_output.currentIndex()
        self._crop_output.setCurrentIndex(0)
        try:
            self.apply_crop()
        finally:
            self._crop_output.setCurrentIndex(previous)
