"""Paragraph-frame movement and resizing, distinct from glyph scaling."""
import copy
import math
import numpy as np
from PyQt6.QtCore import Qt,QPointF,QRectF
from PyQt6.QtGui import QPainter,QPen,QColor,QPolygonF,QTransform,QTextLayout,QTextOption
from ui.image_text_panel import text_font


class TextInteractionOperations:
    def stop_text_session(self):
        if getattr(self,'_text_editing',False):
            self._edit_blink_timer.stop()
            self._text_editing = False
            self._text_edit_layer = None
            self._text_edit_added = False
            self._ime_active = False
            self._ime_preedit = ''
        if hasattr(self,'view'):
            self.view._text_pointer = None

    def text_pointer_cursor(self,position):
        from ui.image_editor import Tool
        layer = self._text_frame_layer()
        if layer is None or layer.locked or self._ft_active or self.tool not in (Tool.TEXT,Tool.MOVE):
            return None
        for name,point in self.text_frame_handles(layer).items():
            if abs(position.x()-point.x())<=7 and abs(position.y()-point.y())<=7:
                return {'n':Qt.CursorShape.SizeVerCursor,'s':Qt.CursorShape.SizeVerCursor,
                        'e':Qt.CursorShape.SizeHorCursor,'w':Qt.CursorShape.SizeHorCursor,
                        'nw':Qt.CursorShape.SizeFDiagCursor,'se':Qt.CursorShape.SizeFDiagCursor,
                        'ne':Qt.CursorShape.SizeBDiagCursor,'sw':Qt.CursorShape.SizeBDiagCursor}[name]
        inverse,valid = layer.canvas_transform().inverted()
        if valid:
            point = self._doc_to_local(self.view.mapToScene(round(position.x()),round(position.y())))
            if QRectF(0,0,layer.w,layer.h).contains(inverse.map(point)):
                return Qt.CursorShape.IBeamCursor if self._text_editing else Qt.CursorShape.SizeAllCursor
        return None

    def _text_frame_layer(self):
        if not self._text_editing and len(self.selected) > 1:
            return None
        layer = self._text_edit_layer if self._text_editing else self.active
        return layer if layer is not None and layer.kind=='text' and layer.visible else None

    def _text_scene_point(self,layer,point):
        transformed = layer.canvas_transform().map(point)
        if self.active_artboard:
            ox,oy = self._ab_screen(self.active_artboard)
            transformed += QPointF(ox,oy)
        return transformed

    def text_frame_handles(self,layer):
        w,h = layer.w,layer.h
        return {name:self.view.mapFromScene(self._text_scene_point(layer,QPointF(x,y)))
                for name,x,y in (('nw',0,0),('n',w/2,0),('ne',w,0),('e',w,h/2),
                                 ('se',w,h),('s',w/2,h),('sw',0,h),('w',0,h/2))}

    def paint_text_frame(self):
        from ui.image_editor import Tool
        layer = self._text_frame_layer()
        if layer is None or layer.locked or self._ft_active or not (self._text_editing or self.tool in (Tool.TEXT,Tool.MOVE)):
            return
        handles = self.text_frame_handles(layer)
        painter = QPainter(self.view.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#65adff'),1.3,Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolygon(QPolygonF([QPointF(handles[name]) for name in ('nw','ne','se','sw')]))
        painter.setPen(QPen(QColor('#17263b'),1))
        painter.setBrush(QColor('#75b6ff'))
        for point in handles.values():
            painter.drawRect(QRectF(point.x()-4,point.y()-4,8,8))
        painter.end()

    def begin_text_pointer(self,event):
        from ui.image_editor import Tool
        if self.tool == Tool.MOVE and event.modifiers() & (Qt.KeyboardModifier.ShiftModifier |
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            return False
        if self._ft_active or self.tool not in (Tool.TEXT,Tool.MOVE) or event.button()!=Qt.MouseButton.LeftButton:
            return False
        scene = self.view.canvas_pos(event)
        layer = self._text_frame_layer()
        if layer is None and self.tool==Tool.TEXT:
            layer = self._hit_test_at(scene,kinds=('text',))
        if layer is None or layer.locked:
            return False
        handles = self.text_frame_handles(layer)
        handle = next((name for name,point in handles.items()
                       if abs(point.x()-event.position().x())<=7 and abs(point.y()-event.position().y())<=7),None)
        inverse,valid = layer.canvas_transform().inverted()
        if not valid:
            return False
        local = inverse.map(self._doc_to_local(scene))
        inside = QRectF(0,0,layer.w,layer.h).contains(local)
        if handle is None and not inside:
            hit = self._hit_test_at(scene,kinds=('text',))
            if hit is not None and hit is not layer:
                if self._text_editing:
                    self._end_text_edit(save=True)
                self.set_active(hit)
                return self.begin_text_pointer(event)
            return False
        if layer in self._all_layers():
            self._select_layer_only(layer)
        self._show_handles = True
        transform = layer.canvas_transform()
        self.view._text_pointer = dict(layer=layer,mode='resize' if handle else 'pending',handle=handle,
                                       start=QPointF(scene),screen_start=QPointF(event.position()),
                                       transform=transform,inverse=inverse,width=layer.w,height=layer.h,
                                       x=layer.x,y=layer.y,quad=copy.deepcopy(layer.quad),
                                       before=self._snapshot(metadata_only=True),added=layer in self._all_layers(),changed=False)
        self.view._start = QPointF(scene)
        self.view._dragging = False
        if not handle and self._text_editing:
            self._place_text_cursor(layer,local)
        self.view.setFocus()
        self.view.viewport().update()
        event.accept()
        return True

    def update_text_pointer(self,event):
        state = getattr(self.view,'_text_pointer',None)
        if state is None:
            return False
        scene = self.view.canvas_pos(event)
        layer = state['layer']
        if state['mode']=='pending':
            delta = event.position()-state['screen_start']
            if math.hypot(delta.x(),delta.y())<3:
                return True
            state['mode'] = 'move'
            if state['added']:
                self._begin_move(state['start'])
        if state['mode']=='move':
            if state['added']:
                self._move_to(scene)
            else:
                layer.x = state['x']+scene.x()-state['start'].x()
                layer.y = state['y']+scene.y()-state['start'].y()
                self._redraw()
            state['changed'] = (layer.x,layer.y)!=(state['x'],state['y'])
        elif state['mode']=='resize':
            local = state['inverse'].map(self._doc_to_local(scene))
            left,top,right,bottom = 0.,0.,float(state['width']),float(state['height'])
            handle = state['handle']
            if 'w' in handle:
                left = min(local.x(),right-24)
            if 'e' in handle:
                right = max(local.x(),24)
            if 'n' in handle:
                top = min(local.y(),bottom-20)
            if 's' in handle:
                bottom = max(local.y(),20)
            layer.w,layer.h = max(24,round(right-left)),max(20,round(bottom-top))
            center = state['transform'].map(QPointF((left+right)/2,(top+bottom)/2))
            layer.x,layer.y = center.x(),center.y()
            corners = [state['transform'].map(QPointF(x,y)) for x,y in ((left,top),(right,top),(right,bottom),(left,bottom))]
            self.apply_world_corners(layer,[(p.x(),p.y()) for p in corners])
            if np.allclose(layer.quad,[[0,0],[1,0],[1,1],[0,1]],atol=1e-6):
                layer.quad = []
            layer.text_box_explicit = True
            state['changed'] = (layer.w,layer.h)!=(state['width'],state['height'])
            self._redraw()
        self.view.viewport().update()
        event.accept()
        return True

    def finish_text_pointer(self,event):
        state = getattr(self.view,'_text_pointer',None)
        if state is None:
            return False
        if event.button()!=Qt.MouseButton.LeftButton:
            return True
        self.update_text_pointer(event)
        if state['mode']=='move' and state['added']:
            self._end_move()
        elif state['mode']=='resize' and state['added'] and state['changed']:
            self._push_undo_snapshot('调整文字编辑框',state['before'])
        self.view._text_pointer = None
        self._sync_props()
        self._redraw()
        self.view.viewport().update()
        event.accept()
        return True

    def _build_text_layout(self,text,box_width):
        layer = self._text_edit_layer
        layout = QTextLayout(text.replace('\n','\u2028'),text_font(layer))
        option = QTextOption()
        option.setAlignment(Qt.AlignmentFlag(layer.align))
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        y = 0.
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(1.,box_width))
            line.setPosition(QPointF(0,y))
            y += line.height()*layer.line_spacing/100
        layout.endLayout()
        return layout

    def _text_display(self):
        text = self._edit_flat
        if self._ime_preedit:
            position = max(0,min(self._ime_compose_start,len(text)))
            return text[:position]+self._ime_preedit+text[position:],position+len(self._ime_preedit)
        return text,self._edit_cursor

    def _place_text_cursor(self,layer,local):
        text,_ = self._text_display()
        layout = self._build_text_layout(text,layer.w)
        if layout.lineCount():
            line = layout.lineAt(layout.lineCount()-1)
            for index in range(layout.lineCount()):
                candidate = layout.lineAt(index)
                if local.y()<candidate.y()+candidate.height():
                    line = candidate
                    break
            utf16_position = line.xToCursor(local.x())
            self._edit_cursor = len(text.encode('utf-16-le')[:utf16_position*2].decode('utf-16-le',errors='ignore'))
        self._edit_blink = True
        self._redraw()

    def _draw_text_editor(self,image):
        layer = self._text_edit_layer
        if layer is None or not layer.visible:
            return
        text,cursor = self._text_display()
        layout = self._build_text_layout(text,layer.w)
        transform = layer.canvas_transform()
        if self.active_artboard:
            ox,oy = self._ab_screen(self.active_artboard)
            transform = transform*QTransform.fromTranslate(ox,oy)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setTransform(transform)
        painter.fillRect(QRectF(0,0,layer.w,layer.h),QColor(28,28,32,190))
        painter.setClipRect(QRectF(0,0,layer.w,layer.h))
        painter.setPen(QColor(layer.color))
        layout.draw(painter,QPointF(0,0))
        position = len(text[:cursor].encode('utf-16-le'))//2
        caret_x,caret_y,caret_h = 0.,0.,max(12.,layer.font_size)
        for index in range(layout.lineCount()):
            line = layout.lineAt(index)
            if line.textStart()<=position<=line.textStart()+line.textLength():
                x = line.cursorToX(position)
                caret_x,caret_y,caret_h = (x[0] if isinstance(x,tuple) else x),line.y(),line.height()
                break
        if self._edit_blink:
            painter.setPen(QPen(QColor('#00eaff'),1))
            painter.drawLine(QPointF(caret_x,caret_y),QPointF(caret_x,caret_y+caret_h))
        painter.end()
        top = self.view.mapFromScene(transform.map(QPointF(caret_x,caret_y)))
        bottom = self.view.mapFromScene(transform.map(QPointF(caret_x,caret_y+caret_h)))
        self._edit_cursor_rect = QRectF(top.x(),top.y(),2,max(10,abs(bottom.y()-top.y())))

    def _update_text_cursor_rect(self):
        layer = self._text_edit_layer
        if layer is None:
            self._edit_cursor_rect = QRectF(0,0,2,12)
            return
        point = self.view.mapFromScene(self._text_scene_point(layer,QPointF(0,0)))
        self._edit_cursor_rect = QRectF(point.x(),point.y(),2,12)
