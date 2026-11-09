"""Inline property controls for selected adjustment and filter layers."""
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QTabWidget, QLabel,
                            QSpinBox, QPushButton)
from ui.image_workbench_widgets import ScrubbableLabel

CLASSIC = {
 'brightness_contrast':[('brightness','亮度',-100,100,0),('contrast','对比度',-100,100,0)],
 'hsl':[('hue','色相',-180,180,0),('saturation','饱和度',-100,100,0),('lightness','明度',-100,100,0)],
 'levels':[('black','黑场',0,255,0),('gamma','灰度 ×100',10,300,100),('white','白场',0,255,255)],
 'white_balance':[('temperature','色温',-100,100,0),('tint','色调',-100,100,0)],
 'color_balance':[('cyan_red','青 / 红',-100,100,0),('mag_green','洋红 / 绿',-100,100,0),('yel_blue','黄 / 蓝',-100,100,0)],
}


class AdjustmentPropertyPanel(QWidget):
    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.current = None
        self.signature = None
        self.controls = {}
        self.layout_box = QVBoxLayout(self)
        self.layout_box.setContentsMargins(0,0,0,0)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(editor._redraw)
        self.hide()

    def sync(self, layer):
        from ui.image_workbench_ops import ADJUSTMENTS
        if layer is None or layer.kind != 'adjust' or not layer.adjust:
            self.current = None
            self.hide()
            return
        self.show()
        self.current = layer
        adjustment = layer.adjust
        kind = adjustment['type']
        specs = (self.editor.FILTERS[adjustment['filter']][1] if kind == 'filter'
                 else ADJUSTMENTS[kind][1] if kind in ADJUSTMENTS else CLASSIC.get(kind, []))
        signature = (id(layer), kind, adjustment.get('filter'))
        if signature != self.signature:
            self.signature, self.current = signature, layer
            self.controls = {}
            while self.layout_box.count():
                item = self.layout_box.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            title = QLabel('效果参数 · ' + layer.name)
            title.setWordWrap(True)
            self.layout_box.addWidget(title)
            tabs = QTabWidget()
            self.layout_box.addWidget(tabs)
            categories = [('参数', specs)]
            if kind == 'camera_raw':
                categories = [('光线', specs[:6]), ('颜色', specs[6:10]), ('细节', specs[10:])]
            for name, fields in categories:
                page = QWidget()
                form = QFormLayout(page)
                form.setContentsMargins(5,6,5,6)
                for key, label, lo, hi, default in fields:
                    control = QSpinBox()
                    control.setRange(lo,hi)
                    control.setValue(int((adjustment.get('params', {}) if kind == 'filter' else adjustment).get(key,default)))
                    control.valueChanged.connect(lambda value, parameter=key:self.change(parameter,value))
                    self.controls[key] = control
                    form.addRow(ScrubbableLabel(label,control),control)
                tabs.addTab(page,name)
            edit = QPushButton('打开曲线图…' if kind == 'curves' else '打开参数窗口…')
            edit.clicked.connect(lambda:self.editor._edit_adjust_layer(self.current) if self.current else None)
            self.layout_box.addWidget(edit)
            reset = QPushButton('重置此效果')
            def restore_defaults():
                if self.current is None or self.current.locked:
                    return
                self.editor._push_undo('重置调整 / 滤镜')
                if kind == 'curves':
                    self.current.adjust = {'type':'curves'}
                else:
                    target = self.current.adjust.setdefault('params',{}) if kind == 'filter' else self.current.adjust
                    target.update({key:default for key,_,_,_,default in specs})
                self.sync(self.current)
                self.editor._redraw()
            reset.clicked.connect(restore_defaults)
            self.layout_box.addWidget(reset)
        values = adjustment.get('params',{}) if kind == 'filter' else adjustment
        for key,_,_,_,default in specs:
            control = self.controls[key]
            control.blockSignals(True)
            control.setValue(int(values.get(key,default)))
            control.blockSignals(False)
        self.setEnabled(not layer.locked)

    def change(self, key, value):
        layer = self.current
        if layer is None or layer.locked:
            return
        self.editor._queue_property_undo('调整效果参数')
        target = layer.adjust.setdefault('params',{}) if layer.adjust['type'] == 'filter' else layer.adjust
        target[key] = value
        self.timer.start(75)
