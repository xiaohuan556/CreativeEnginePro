"""Transactional live editor for persistent layer effects."""
import copy
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QDialog,QVBoxLayout,QTabWidget,QWidget,QFormLayout,QCheckBox,
                            QSpinBox,QPushButton,QColorDialog,QDialogButtonBox)
from ui.image_color_dialog import choose_color
from ui.image_chrome import ColorSwatchButton

EFFECT_PAGES = [
 ('投影',[('shadow','启用','bool',False),('shadow_dx','偏移 X','int',4),('shadow_dy','偏移 Y','int',4),
          ('shadow_blur','柔化','size',8),('shadow_opacity','不透明度','percent',.5),('shadow_color','颜色','color','#000000')]),
 ('描边',[('effect_stroke','启用','bool',False),('effect_stroke_width','宽度','size',3),('effect_stroke_color','颜色','color','#ffffff')]),
 ('叠色',[('color_overlay','启用','bool',False),('color_overlay_color','颜色','color','#3d8ef8'),('color_overlay_opacity','不透明度','percent',1.)]),
 ('内阴影',[('inner_shadow','启用','bool',False),('inner_shadow_dx','偏移 X','int',3),('inner_shadow_dy','偏移 Y','int',3),
          ('inner_shadow_blur','柔化','size',4),('inner_shadow_opacity','不透明度','percent',.4),('inner_shadow_color','颜色','color','#000000')]),
 ('外发光',[('outer_glow','启用','bool',False),('outer_glow_size','范围','size',8),('outer_glow_opacity','不透明度','percent',.5),('outer_glow_color','颜色','color','#00eaff')]),
 ('内发光',[('inner_glow','启用','bool',False),('inner_glow_size','范围','size',8),('inner_glow_opacity','不透明度','percent',.5),('inner_glow_color','颜色','color','#ffffff')]),
 ('浮雕',[('bevel_emboss','启用','bool',False),('bevel_size','范围','size',3),('bevel_depth','深度','percent',.5),
          ('bevel_highlight','亮部颜色','color','#ffffff'),('bevel_shadow','暗部颜色','color','#000000')]),
]


def edit_layer_effects(editor):
    layer = editor.active
    if layer is None or layer.locked or layer.kind == 'adjust':
        return
    before = editor._snapshot()
    old_effects = copy.deepcopy(layer.effects)
    original = {key:copy.deepcopy(getattr(layer,key)) for _,fields in EFFECT_PAGES for key,_,_,_ in fields if hasattr(layer,key)}
    dialog = QDialog(editor)
    dialog.setWindowTitle('图层效果 · '+layer.name)
    dialog.resize(620,350)
    layout = QVBoxLayout(dialog)
    tabs = QTabWidget()
    layout.addWidget(tabs)
    timer = QTimer(dialog)
    timer.setSingleShot(True)
    timer.timeout.connect(editor._redraw)
    def change(key,value):
        if hasattr(layer,key):
            setattr(layer,key,value)
        else:
            layer.effects[key] = value
        timer.start(50)
    for title,fields in EFFECT_PAGES:
        page = QWidget()
        form = QFormLayout(page)
        for key,label,kind,default in fields:
            value = getattr(layer,key,layer.effects.get(key,default))
            if kind == 'bool':
                control = QCheckBox(label)
                control.setChecked(bool(value))
                control.toggled.connect(lambda v,name=key:change(name,v))
                form.addRow(control)
            elif kind == 'color':
                control = ColorSwatchButton(QColor(value), '选择效果颜色')
                control.setFixedSize(90, 28)
                def choose(_checked=False,name=key,button=control,fallback=default):
                    value = getattr(layer,name,layer.effects.get(name,fallback))
                    color = choose_color(QColor(value),dialog,'选择效果颜色')
                    if color.isValid():
                        change(name,color.name())
                        button.set_color(color)
                control.clicked.connect(choose)
                form.addRow(label,control)
            else:
                control = QSpinBox()
                control.setObjectName('effect_'+key)
                control.setRange(0,100 if kind == 'percent' else 200) if kind != 'int' else control.setRange(-200,200)
                control.setValue(round(value*100) if kind == 'percent' else int(value))
                control.valueChanged.connect(lambda v,name=key,percent=kind=='percent':change(name,v/100 if percent else v))
                form.addRow(label,control)
        tabs.addTab(page,title)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    timer.stop()
    if not accepted:
        layer.effects = old_effects
        for key,value in original.items():
            setattr(layer,key,value)
    else:
        changed = layer.effects != old_effects or any(getattr(layer,key) != value for key,value in original.items())
        if changed:
            editor._push_undo_snapshot('修改图层效果',before)
    editor._sync_props()
    editor._redraw()
