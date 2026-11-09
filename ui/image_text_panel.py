"""Editable paragraph typography with the same layout for preview and export."""
import math
import numpy as np
from PyQt6.QtCore import Qt,QPointF
from PyQt6.QtGui import QFont,QImage,QPainter,QColor,QBrush,QPen,QLinearGradient,QTextLayout,QTextOption,QTextCharFormat
from PyQt6.QtWidgets import QWidget,QFormLayout,QFontComboBox,QSpinBox,QCheckBox,QComboBox,QPushButton,QColorDialog
from ui.image_workbench_widgets import ScrubbableLabel
from ui.image_color_dialog import choose_color


def text_font(layer,scale=1):
    font = QFont(layer.font_family,max(1,round(layer.font_size*scale)))
    font.setBold(layer.bold)
    font.setItalic(layer.italic)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing,layer.letter_spacing*scale)
    return font


def render_text(layer):
    image = QImage(max(1,layer.w),max(1,layer.h),QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    layout = QTextLayout(layer.text.replace('\n','\u2028'),text_font(layer))
    option = QTextOption()
    option.setAlignment(Qt.AlignmentFlag(layer.align))
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    style = QTextCharFormat()
    if layer.gradient:
        angle = math.radians(layer.grad_angle)
        dx,dy = math.cos(angle)*layer.w/2,math.sin(angle)*layer.h/2
        gradient = QLinearGradient(layer.w/2-dx,layer.h/2-dy,layer.w/2+dx,layer.h/2+dy)
        gradient.setColorAt(0,QColor(layer.grad_from))
        gradient.setColorAt(1,QColor(layer.grad_to))
        style.setForeground(QBrush(gradient))
    else:
        style.setForeground(QBrush(QColor(layer.color)))
    if layer.stroke_w>0:
        style.setTextOutline(QPen(QColor(layer.stroke_color),layer.stroke_w))
    region = QTextLayout.FormatRange()
    region.start,region.length,region.format = 0,len(layer.text.encode('utf-16-le'))//2,style
    layout.setFormats([region])
    layout.beginLayout()
    y = 0.
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(max(1,layer.w))
        line.setPosition(QPointF(0,y))
        y += line.height()*layer.line_spacing/100
    layout.endLayout()
    layout.draw(painter,QPointF(0,0))
    painter.end()
    return image


class TextPropertyPanel(QWidget):
    def __init__(self,editor):
        super().__init__()
        self.editor,self.current = editor,None
        form = QFormLayout(self)
        form.setContentsMargins(0,4,0,4)
        self.family = QFontComboBox()
        self.family.currentFontChanged.connect(lambda font:self.change('font_family',font.family()))
        form.addRow('字体',self.family)
        self.controls = {}
        for key,label,lo,hi in (('w','编辑框宽度',24,30000),('h','编辑框高度',20,30000),
                               ('font_size','字号（pt）',1,1000),('letter_spacing','字距',-100,200),('line_spacing','行距 %',50,400)):
            spin = QSpinBox()
            spin.setRange(lo,hi)
            spin.valueChanged.connect(lambda value,name=key:self.change(name,value))
            self.controls[key] = spin
            form.addRow(ScrubbableLabel(label,spin),spin)
        for key,label in (('bold','粗体'),('italic','斜体')):
            check = QCheckBox(label)
            check.toggled.connect(lambda value,name=key:self.change(name,value))
            self.controls[key] = check
            form.addRow(check)
        self.alignment = QComboBox()
        for label,value in (('左对齐',Qt.AlignmentFlag.AlignLeft),('居中',Qt.AlignmentFlag.AlignHCenter),('右对齐',Qt.AlignmentFlag.AlignRight),('两端对齐',Qt.AlignmentFlag.AlignJustify)):
            self.alignment.addItem(label,int(value))
        self.alignment.currentIndexChanged.connect(lambda _:self.change('align',self.alignment.currentData()))
        form.addRow('段落',self.alignment)
        color = QPushButton('文字颜色…')
        def choose():
            if self.current:
                value = choose_color(QColor(self.current.color),self,'文字颜色')
                if value.isValid():
                    self.change('color',value.name())
        color.clicked.connect(choose)
        form.addRow(color)
        self.hide()

    def change(self,key,value):
        if self.current is None or self.current.locked:
            return
        self.editor._queue_property_undo('文字样式')
        if key in ('w','h'):
            layer = self.current
            transform = layer.canvas_transform()
            width,height = (value,layer.h) if key=='w' else (layer.w,value)
            center = transform.map(QPointF(width/2,height/2))
            corners = [transform.map(QPointF(x,y)) for x,y in ((0,0),(width,0),(width,height),(0,height))]
            layer.w,layer.h,layer.x,layer.y = width,height,center.x(),center.y()
            self.editor.apply_world_corners(layer,[(p.x(),p.y()) for p in corners])
            if np.allclose(layer.quad,[[0,0],[1,0],[1,1],[0,1]],atol=1e-6):
                layer.quad = []
            layer.text_box_explicit = True
        else:
            setattr(self.current,key,value)
        self.editor._redraw()
        if self.editor._text_editing:
            self.editor._update_text_cursor_rect()

    def sync(self,layer):
        self.current = layer if layer and layer.kind=='text' else None
        self.setVisible(self.current is not None)
        if self.current is None:
            return
        self.setEnabled(not layer.locked)
        self.family.blockSignals(True)
        self.family.setCurrentFont(QFont(layer.font_family))
        self.family.blockSignals(False)
        for key,control in self.controls.items():
            control.blockSignals(True)
            control.setChecked(getattr(layer,key)) if isinstance(control,QCheckBox) else control.setValue(getattr(layer,key))
            control.blockSignals(False)
        self.alignment.blockSignals(True)
        self.alignment.setCurrentIndex(max(0,self.alignment.findData(layer.align & 15)))
        self.alignment.blockSignals(False)
