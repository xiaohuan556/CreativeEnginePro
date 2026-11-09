"""Nested layer folders with isolated compositing and recursive persistence."""
import copy
import math
import numpy as np
from core.image_effects import pixel_matrix
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QListWidgetItem, QMessageBox,QApplication


def walk_layers(layers):
    for layer in layers:
        yield layer
        yield from walk_layers(getattr(layer, 'children', []))


def find_parent(layers, target):
    for layer in layers:
        if layer is target:
            return layers
        found = find_parent(getattr(layer, 'children', []), target)
        if found is not None:
            return found
    return None


class ImageFolderOperations:
    def _find_next_after(self,removed):
        excluded = set(walk_layers(removed))
        remaining = [layer for layer in self._ctx_layers() if layer not in excluded]
        return remaining[-1] if remaining else None

    def _top_selected(self):
        selected = set(self.selected)
        def visit(layers,ancestor_selected=False):
            for layer in layers:
                if layer in selected and not ancestor_selected and not layer.locked:
                    yield layer
                yield from visit(layer.children,ancestor_selected or layer in selected)
        return list(visit(self._root_layers()))

    def _translate_node(self,layer,dx,dy):
        import cv2
        for node in walk_layers([layer]):
            node.x,node.y = node.x+dx,node.y+dy
            if node.mask is not None and node.mask_linked:
                mask = node.mask
                node.mask = cv2.warpAffine(mask,np.float32([[1,0,dx],[0,1,dy]]),(mask.shape[1],mask.shape[0]))

    def _align_layers(self,mode):
        selected = [layer for layer in self._top_selected() if layer.kind!='adjust']
        if not selected:
            return
        boxes = [self._layer_bbox(layer) for layer in selected]
        setting = self._align_reference.currentData() if hasattr(self,'_align_reference') else 'auto'
        if setting=='selection' and self.selection is not None and self.selection.any():
            ys,xs = np.nonzero(self.selection)
            reference = QRectF(int(xs.min()),int(ys.min()),int(xs.max()-xs.min()+1),int(ys.max()-ys.min()+1))
        elif setting=='canvas' or setting=='auto' and len(selected)==1:
            width,height = self._ctx_size()
            reference = QRectF(0,0,width,height)
        else:
            reference = QRectF(boxes[0])
            for box in boxes[1:]:
                reference = reference.united(box)
        self._push_undo('对齐图层 / 文件夹')
        for layer,box in zip(selected,boxes):
            dx = {'left':reference.left()-box.left(),'hcenter':reference.center().x()-box.center().x(),
                  'right':reference.right()-box.right()}.get(mode,0)
            dy = {'top':reference.top()-box.top(),'vcenter':reference.center().y()-box.center().y(),
                  'bottom':reference.bottom()-box.bottom()}.get(mode,0)
            self._translate_node(layer,dx,dy)
        self._redraw()

    def _distribute_layers(self,axis):
        selected = [layer for layer in self._top_selected() if layer.kind!='adjust']
        if len(selected)<3:
            return
        horizontal = axis=='h'
        selected.sort(key=lambda layer:self._layer_bbox(layer).left() if horizontal else self._layer_bbox(layer).top())
        boxes = [self._layer_bbox(layer) for layer in selected]
        start,end = (boxes[0].left(),boxes[-1].right()) if horizontal else (boxes[0].top(),boxes[-1].bottom())
        sizes = [box.width() if horizontal else box.height() for box in boxes]
        gap = (end-start-sum(sizes))/(len(selected)-1)
        self._push_undo('均匀分布图层 / 文件夹')
        position = start
        for layer,box,size in zip(selected,boxes,sizes):
            delta = position-(box.left() if horizontal else box.top())
            self._translate_node(layer,delta if horizontal else 0,0 if horizontal else delta)
            position += size+gap
        self._redraw()

    def _root_layers(self):
        return self.active_artboard.layers if self.active_artboard else self.project.layers

    def _ctx_layers(self):
        parent = find_parent(self._root_layers(), self.active)
        return self._root_layers() if parent is None else parent

    def _all_layers(self):
        roots = [ab.layers for ab in self.project.artboards] if self.project.artboards else [self.project.layers]
        return [layer for root in roots for layer in walk_layers(root)]

    def _active_artboard_of(self, layer):
        return next((ab for ab in self.project.artboards if find_parent(ab.layers, layer) is not None), None)

    def _parent_list_of(self, layer):
        for roots in [self.project.layers] + [ab.layers for ab in self.project.artboards]:
            parent = find_parent(roots, layer)
            if parent is not None:
                return parent
        return self._root_layers()

    def _group_selected(self):
        from ui.image_editor import ImageLayer
        parent = self._ctx_layers()
        selected = [layer for layer in parent if layer in self.selected]
        if not selected or any(layer.locked for layer in selected):
            return
        if any(self._parent_list_of(layer) is not parent for layer in self.selected):
            QMessageBox.information(self, '图层文件夹', '请先选择同一文件夹内的图层。')
            return
        self._push_undo('创建图层文件夹')
        folder = ImageLayer('图层组', kind='group')
        index = min(parent.index(layer) for layer in selected)
        folder.children = selected
        for layer in selected:
            layer.group_id = None
            parent.remove(layer)
        parent.insert(index, folder)
        box = self._layer_bbox(folder)
        folder.x, folder.y = box.center().x(), box.center().y()
        self._select_layer_only(folder)
        self._refresh_layers()
        self._redraw()

    def _ungroup_selected(self):
        folders = [layer for layer in self.selected if layer.kind == 'group']
        if not folders:
            return self._ungroup_legacy()
        self._push_undo('解散图层文件夹')
        children = []
        for folder in folders:
            parent = self._parent_list_of(folder)
            if folder not in parent:
                continue
            index = parent.index(folder)
            parent[index:index+1] = folder.children
            children.extend(folder.children)
        self.selected = children
        self.active = children[-1] if children else None
        self._refresh_layers()
        self._redraw()

    def move_layer_into(self, layer, folder=None):
        if layer is folder or layer.locked:
            return False
        if folder is not None and (folder.kind != 'group' or folder.locked or folder in list(walk_layers([layer]))):
            return False
        parent = self._parent_list_of(layer)
        destination = folder.children if folder else self._root_layers()
        if parent is destination or layer not in parent:
            return False
        self._push_undo('移入图层文件夹' if folder else '移出图层文件夹')
        parent.remove(layer)
        destination.append(layer)
        if folder:
            folder.collapsed = False
        self._refresh_layers()
        self._redraw()
        return True

    def _drop_layer_at(self, layer, target_row):
        item = self.layer_list.item(target_row) if target_row < self.layer_list.count() else None
        target = item.data(Qt.ItemDataRole.UserRole) if item else None
        if target is layer or (target is not None and target in list(walk_layers([layer]))):
            return
        if target is not None and target.kind == 'group':
            self.move_layer_into(layer, target)
            return
        destination = self._parent_list_of(target) if target else self._root_layers()
        parent = self._parent_list_of(layer)
        if layer.locked or layer not in parent:
            return
        self._push_undo('调整图层顺序')
        parent.remove(layer)
        index = destination.index(target) + 1 if target in destination else 0
        destination.insert(index, layer)
        board = self._active_artboard_of(layer)
        if board:
            self.active_artboard = board
        self._refresh_layers()
        self._redraw()

    def _duplicate_layer(self, layer):
        self._push_undo('复制图层')
        duplicate = copy.deepcopy(layer)
        self._renew_layer_ids([duplicate])
        duplicate.name += ' 副本'
        parent = self._parent_list_of(layer)
        parent.insert(parent.index(layer) + 1, duplicate)
        self.set_active(duplicate)
        self._redraw()

    def _renew_layer_ids(self, layers):
        from ui.image_editor import ImageLayer
        for layer in walk_layers(layers):
            ImageLayer._counter += 1
            layer.id = ImageLayer._counter
            layer.group_id = None

    def _layer_bbox(self, layer):
        if layer is not None and layer.kind == 'group':
            boxes = [self._layer_bbox(child) for child in layer.children if child.kind != 'adjust']
            result = QRectF()
            for box in boxes:
                if box is not None:
                    result = result.united(box)
            return result if not result.isEmpty() else QRectF(layer.x - .5, layer.y - .5, 1, 1)
        return self._leaf_bbox(layer)

    def render_stack(self, layers, width, height, background=None, initial=None):
        from ui.image_editor import numpy_from_qimage, qimage_from_numpy
        out = initial.copy() if initial is not None else QImage(width, height, QImage.Format.Format_ARGB32)
        if initial is None:
            out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        if background is not None:
            painter.fillRect(out.rect(), background)
        base = None
        for layer in layers:
            if not layer.visible:
                continue
            if layer.kind == 'group' and layer.blend == 'pass_through':
                from core.image_effects import mix_rgba
                painter.end()
                combined = self.render_stack(layer.children, width, height, initial=out)
                strength = layer.opacity
                if layer.mask is not None and layer.mask.shape == (height, width):
                    strength *= layer.mask[:, :, None].astype(np.float32) / 255
                out = qimage_from_numpy(mix_rgba(numpy_from_qimage(out), numpy_from_qimage(combined), strength))
                painter = QPainter(out)
                continue
            if layer.kind == 'adjust':
                painter.end()
                out = self._apply_adjustment(out, layer)
                painter = QPainter(out)
                continue
            image = self._render_layer_canvas(layer, width, height)
            if layer.clip and base is not None:
                pixels = numpy_from_qimage(image)
                alpha = numpy_from_qimage(base)[:, :, 3].astype(np.uint16)
                pixels[:, :, 3] = (pixels[:, :, 3].astype(np.uint16) * alpha // 255).astype(np.uint8)
                image = qimage_from_numpy(pixels)
            elif not layer.clip:
                base = image
            out, painter = self._draw_blended(out, painter, image, layer)
        painter.end()
        return out

    def render_folder(self, folder, width=None, height=None):
        from ui.image_editor import numpy_from_qimage, qimage_from_numpy
        if width is None:
            width, height = self._ctx_size()
        image = self.render_stack(folder.children, width, height)
        from core.image_layer_effects import has_layer_effects
        if folder.mask is None and folder.opacity == 1. and not has_layer_effects(folder):
            return image
        pixels = numpy_from_qimage(image)
        alpha = pixels[:, :, 3].astype(np.float32)
        if folder.mask is not None and folder.mask.shape == alpha.shape:
            alpha *= folder.mask.astype(np.float32) / 255
        pixels[:, :, 3] = np.rint(np.clip(alpha, 0, 255)).astype(np.uint8)
        from core.image_layer_effects import apply_layer_effects
        return qimage_from_numpy(apply_layer_effects(pixels,folder))

    def _render_composite(self, for_export=False):
        if self.project.artboards:
            return self._render_doc_composite(for_export)
        width, height = self.project.w, self.project.h
        image = self.render_stack(self.project.layers, width, height,
                                  None if self.project.transparent else self.project.bg_color)
        if for_export or not self.project.transparent:
            return image
        display = QImage(width, height, QImage.Format.Format_ARGB32)
        display.fill(Qt.GlobalColor.transparent)
        painter = QPainter(display)
        self._paint_checker(painter, width, height)
        painter.drawImage(0, 0, image)
        painter.end()
        return display

    def _render_artboard(self, board):
        return self.render_stack(board.layers, board.w, board.h, None if board.transparent else board.bg_color)

    def _refresh_layers(self):
        from ui.image_editor import LayerItemWidget, ArtboardHeaderWidget
        size = self._ctx_size()
        if getattr(self,'_displayed_canvas_size',None)!=size:
            for control,value in ((self.cw,size[0]),(self.ch,size[1])):
                control.blockSignals(True)
                control.setMaximum(max(30000,value))
                control.setValue(value)
                control.blockSignals(False)
            self._displayed_canvas_size = size
        self.layer_list.clear()
        query = self._layer_search.text().strip().casefold()
        kind = self._layer_kind.currentData()
        def add_layers(layers, depth=0):
            for layer in reversed(layers):
                item = QListWidgetItem()
                widget = LayerItemWidget(layer, self)
                widget.layout().setContentsMargins(6 + depth * 14, 4, 6, 4)
                item.setSizeHint(widget.sizeHint())
                item.setData(Qt.ItemDataRole.UserRole, layer)
                self.layer_list.addItem(item)
                self.layer_list.setItemWidget(item, widget)
                widget.refresh()
                if layer.kind == 'group' and (not layer.collapsed or query or kind):
                    add_layers(layer.children, depth + 1)
        if self.project.artboards:
            for board in reversed(self.project.artboards):
                item = QListWidgetItem()
                widget = ArtboardHeaderWidget(board, self)
                item.setSizeHint(widget.sizeHint())
                self.layer_list.addItem(item)
                self.layer_list.setItemWidget(item, widget)
                widget.refresh()
                if not board.collapsed or query or kind:
                    add_layers(board.layers)
        else:
            add_layers(self.project.layers)
        width, height = self._ctx_size()
        self.status_size.setText(f'{width} × {height}')
        self._filter_layer_list()
        self._sync_props()

    def _hit_test_at(self, point, kinds=None):
        local = self._doc_to_local(point)
        def hit(layers):
            for layer in reversed(layers):
                if not layer.visible or layer.opacity <= 0 or layer.locked or layer.kind == 'adjust':
                    continue
                if layer.kind == 'group':
                    child = hit(layer.children)
                    if child is not None:
                        return child if kinds is not None or not layer.collapsed else layer
                    continue
                if kinds is not None and layer.kind not in kinds:
                    continue
                position = layer.canvas_to_layer(local.x(), local.y())
                if position is None:
                    continue
                x, y = map(lambda value: int(round(value)), position)
                if 0 <= x < layer.w and 0 <= y < layer.h:
                    if layer.kind != 'image' or layer.pixels is not None and layer.pixels[y, x, 3] > 0:
                        return layer
            return None
        return hit(self._root_layers())

    def _hit_test(self, point):
        if self.selection is not None:
            return self.active
        layer = self._hit_test_at(point)
        if layer:
            self.set_active(layer)
        return layer

    def _expanded_selection(self):
        selected = set(self.selected)
        result = []
        def visit(layers, inherited=False):
            for layer in layers:
                included = inherited or layer in selected
                if layer.locked:
                    continue
                if included:
                    result.append(layer)
                visit(getattr(layer, 'children', []), included)
        visit(self._root_layers())
        return result

    def _begin_move(self, point):
        self._move_targets = self._expanded_selection()
        self._move_masks = {id(layer): layer.mask.copy() for layer in self._move_targets if layer.mask is not None and layer.mask_linked}
        self._move_olds = {id(layer): (layer.x, layer.y) for layer in self._move_targets}
        self._move_pre_snap = self._snapshot(metadata_only=True)

    def _move_to(self, point):
        if not self._move_olds:
            return
        import cv2
        if not any(layer.kind == 'group' for layer in self.selected):
            self._move_leaf_to(point, redraw=False)
            for layer in self._move_targets:
                if id(layer) in self._move_masks:
                    x, y = self._move_olds[id(layer)]
                    mask = self._move_masks[id(layer)]
                    layer.mask = cv2.warpAffine(mask, np.float32([[1, 0, layer.x-x], [0, 1, layer.y-y]]), (mask.shape[1], mask.shape[0]))
            self._redraw()
            return
        dx, dy = point.x() - self.view._start.x(), point.y() - self.view._start.y()
        for layer in self._move_targets:
            x, y = self._move_olds[id(layer)]
            layer.x, layer.y = x + dx, y + dy
            if id(layer) in self._move_masks:
                mask = self._move_masks[id(layer)]
                layer.mask = cv2.warpAffine(mask, np.float32([[1, 0, dx], [0, 1, dy]]), (mask.shape[1], mask.shape[0]))
        self._redraw()

    def _end_move(self):
        if self._move_olds and any((layer.x, layer.y) != self._move_olds[id(layer)] for layer in self._move_targets):
            self._push_undo_snapshot('移动图层 / 文件夹', self._move_pre_snap)
        self._move_olds = None
        self._move_targets = []
        self._move_masks = {}
        self._smart_guides = []
        if self._clear_interaction_preview():
            self._redraw()
        self.view.viewport().update()

    def _handle_move(self, name, point, original):
        if name in ('nw','ne','se','sw') and QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier:
            self.distort_corner(name,point,original)
            return
        if self.active is None or self.active.kind != 'group':
            layer = self.active
            if layer is not None and layer.mask is not None and layer.mask_linked and 'linked_mask' not in original:
                original['linked_mask'] = layer.mask.copy()
                original['mask_transform'] = layer.canvas_transform()
            self._handle_leaf_move(name, point, original)
            if layer is not None and 'linked_mask' in original:
                import cv2
                local = [QPointF(0,0),QPointF(layer.w,0),QPointF(layer.w,layer.h),QPointF(0,layer.h)]
                source = np.float32([[original['mask_transform'].map(p).x(),original['mask_transform'].map(p).y()] for p in local])
                transform = layer.canvas_transform()
                target = np.float32([[transform.map(p).x(),transform.map(p).y()] for p in local])
                matrix = cv2.getPerspectiveTransform(source,target)
                mask = original['linked_mask']
                layer.mask = cv2.warpPerspective(mask,pixel_matrix(matrix),(mask.shape[1],mask.shape[0]))
                self._redraw()
            return
        folder = self.active
        if 'folder_states' not in original:
            original['folder_states'] = [(layer, layer.x, layer.y, layer.scale, layer.rotation,
                                          layer.mask.copy() if layer.mask is not None else None)
                                         for layer in walk_layers([folder])]
        proxy = copy.copy(folder)
        proxy.x, proxy.y = original['bbox'].center().x(), original['bbox'].center().y()
        proxy.kind = 'image'
        proxy.quad = []
        self.active = proxy
        guides = self.smart_guides_on
        self.smart_guides_on = False
        try:
            self._handle_leaf_move(name, point, original)
        finally:
            self.active = folder
            self.smart_guides_on = guides
        center = original['bbox'].center()
        factor = proxy.scale / max(original['scale'], .0001)
        angle = proxy.rotation - original['rotation']
        self.transform_nodes(original['folder_states'], center, QPointF(proxy.x, proxy.y), factor, angle)
        self._redraw()

    def transform_nodes(self, states, origin, destination, factor, angle):
        import cv2
        radians = math.radians(angle)
        a, b = math.cos(radians) * factor, math.sin(radians) * factor
        tx = destination.x() - a * origin.x() + b * origin.y()
        ty = destination.y() - b * origin.x() - a * origin.y()
        matrix = np.float32([[a, -b, tx], [b, a, ty]])
        for layer, x, y, scale, rotation, mask in states:
            layer.x, layer.y = a*x - b*y + tx, b*x + a*y + ty
            layer.scale, layer.rotation = scale * factor, rotation + angle
            if mask is not None and layer.mask_linked:
                layer.mask = cv2.warpAffine(mask, pixel_matrix(matrix), (mask.shape[1], mask.shape[0]))

    def apply_world_corners(self,layer,corners):
        base = copy.copy(layer)
        base.quad = []
        inverse,valid = base.canvas_transform().inverted()
        if not valid:
            return
        layer.quad = [[inverse.map(QPointF(float(x),float(y))).x()/layer.w,
                       inverse.map(QPointF(float(x),float(y))).y()/layer.h] for x,y in corners]

    def distort_corner(self,name,point,original):
        import cv2
        layer = self.active
        if layer is None or layer.locked:
            return
        index = ('nw','ne','se','sw').index(name)
        local = self._doc_to_local(point)
        if 'distortion_states' not in original:
            states = []
            for node in walk_layers([layer]):
                transform = node.canvas_transform()
                corners = [transform.map(QPointF(x,y)) for x,y in ((0,0),(node.w,0),(node.w,node.h),(0,node.h))]
                states.append((node,np.float32([[p.x(),p.y()] for p in corners]),node.mask.copy() if node.mask is not None else None))
            original['distortion_states'] = states
            box = original.get('bbox',self._layer_bbox(layer))
            original['distortion_box'] = np.float32([[box.left(),box.top()],[box.right(),box.top()],[box.right(),box.bottom()],[box.left(),box.bottom()]])
        if layer.kind=='group':
            source = original['distortion_box']
        else:
            source = original['distortion_states'][0][1]
        target = source.copy()
        target[index] = [local.x(),local.y()]
        if not cv2.isContourConvex(target) or abs(cv2.contourArea(target))<1:
            return
        matrix = cv2.getPerspectiveTransform(source,target)
        for node,corners,mask in original['distortion_states']:
            if node.kind!='group':
                projected = cv2.perspectiveTransform(corners[None,:,:],matrix)[0]
                self.apply_world_corners(node,projected)
            if mask is not None and node.mask_linked:
                node.mask = cv2.warpPerspective(mask,pixel_matrix(matrix),(mask.shape[1],mask.shape[0]))
        if layer.kind=='group':
            box = original.get('bbox',self._layer_bbox(layer))
            layer.quad = [[(x-box.left())/max(1,box.width()),(y-box.top())/max(1,box.height())] for x,y in target.tolist()]
        self._redraw()
