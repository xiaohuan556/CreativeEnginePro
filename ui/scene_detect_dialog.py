"""智能分镜参数弹窗。"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QLabel, QVBoxLayout,
)

from core.scene_detector import SCENE_PRESETS
from ui.widgets import CheckMarkBox


class SceneDetectDialog(QDialog):
    def __init__(self, clip_duration: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("智能分镜")
        self.setFixedWidth(440)
        self.setStyleSheet("""
            QDialog{background:#1a1a1a;color:#ccc;}
            QLabel{color:#bbb;}
            QComboBox,QDoubleSpinBox{background:#252525;color:#ddd;border:1px solid #444;
                border-radius:4px;padding:5px 8px;min-height:22px;}
            QPushButton{background:#292929;color:#ccc;border:1px solid #444;
                border-radius:4px;padding:6px 16px;}
            QPushButton:hover{background:#363636;color:#fff;}
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        title = QLabel("根据相邻画面的视觉差异自动截开视频")
        title.setStyleSheet("font-size:14px;font-weight:600;color:#eee;")
        root.addWidget(title)
        help_label = QLabel(
            "标准档适合大多数口播、搬运和混剪视频。画面运动很大时选保守；"
            "动画、轻微转场不容易识别时选灵敏。")
        help_label.setWordWrap(True)
        help_label.setStyleSheet("color:#7f8c98;font-size:11px;")
        root.addWidget(help_label)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(9)
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(SCENE_PRESETS.keys())
        self.preset_combo.setCurrentText("标准（推荐）")
        self.preset_combo.currentTextChanged.connect(self._on_preset_changed)
        form.addRow("检测灵敏度", self.preset_combo)

        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.08, 0.75)
        self.threshold_spin.setDecimals(2)
        self.threshold_spin.setSingleStep(0.02)
        self.threshold_spin.setValue(0.30)
        self.threshold_spin.setEnabled(False)
        self.threshold_spin.valueChanged.connect(self._update_explanation)
        form.addRow("画面差异阈值", self.threshold_spin)

        self.min_length_spin = QDoubleSpinBox()
        self.min_length_spin.setRange(0.3, max(0.3, min(30.0, clip_duration / 2)))
        self.min_length_spin.setDecimals(1)
        self.min_length_spin.setSingleStep(0.2)
        self.min_length_spin.setValue(min(0.8, self.min_length_spin.maximum()))
        self.min_length_spin.setSuffix(" 秒")
        form.addRow("最短片段", self.min_length_spin)
        root.addLayout(form)

        self.filter_flashes = CheckMarkBox("过滤单帧闪白、闪黑和相机闪光")
        self.filter_flashes.setChecked(True)
        root.addWidget(self.filter_flashes)

        self.range_label = QLabel("")
        self.range_label.setWordWrap(True)
        self.range_label.setStyleSheet(
            "background:#121820;color:#86a9c7;border:1px solid #263746;"
            "border-radius:4px;padding:7px 9px;font-size:11px;")
        root.addWidget(self.range_label)
        self._update_explanation()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("开始检测并截开")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _on_preset_changed(self, text: str):
        value = SCENE_PRESETS.get(text)
        self.threshold_spin.setEnabled(value is None)
        if value is not None:
            self.threshold_spin.setValue(value)
        self._update_explanation()

    def _update_explanation(self, _value=None):
        value = self.threshold_spin.value()
        if value >= 0.38:
            explanation = "只认明显硬切，误切最少；适合运动、手持、快速推拉镜头。"
        elif value >= 0.25:
            explanation = "明显换镜通常会被识别，运动画面一般不会误切。"
        else:
            explanation = "会识别较柔和的画面变化，也更可能把快速运动误认为换镜。"
        self.range_label.setText(
            f"当前阈值 {value:.2f}：{explanation} 数值越低越灵敏，建议范围 0.20～0.42。")

    def config(self) -> dict:
        return {
            "threshold": self.threshold_spin.value(),
            "min_length": self.min_length_spin.value(),
            "filter_flashes": self.filter_flashes.isChecked(),
        }
