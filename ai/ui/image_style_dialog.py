"""Focused style browser for the production canvas image editor."""
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFrame, QLabel, QLineEdit, QTextEdit, QPushButton, QComboBox,
    QDoubleSpinBox, QListWidget, QListWidgetItem, QScrollArea,
)

from ai.style_presets import STYLE_PRESETS, STYLE_PRESET_BY_ID, compile_style_prompt


def style_summary(payload):
    preset = STYLE_PRESET_BY_ID.get(str(payload.get("style_preset") or ""), {})
    name = str(preset.get("label") or "")
    custom = str(payload.get("style_custom") or "").strip()
    legacy = str(payload.get("style") or "").strip()
    if not name and not custom and not legacy:
        return "未设置风格 · 按提示词生成"
    name = name or ("自定义风格" if custom else legacy)
    if preset and custom:
        name += " + 自定义补充"
    scope = {"whole": "整张画面", "subject": "只改主体", "background": "只改背景"}.get(
        payload.get("style_scope"), "整张画面")
    return f"{name} · {scope} · 强度 {int(payload.get('style_strength') or preset.get('strength') or 70)}%"


class ImageStyleDialog(QDialog):
    def __init__(self, payload, has_reference, icon_factory, checkbox_type, parent=None):
        super().__init__(parent)
        self.payload = dict(payload)
        self.has_reference = has_reference
        self.icon_factory = icon_factory
        self.selected_id = str(payload.get("style_preset") or "")
        self.setWindowTitle("风格探索器")
        self.setMinimumSize(880, 640)
        self.resize(1000, 730)
        self.setStyleSheet("""
            QDialog{background:#1a1a1a;color:#edf1fa;}
            QLabel{color:#c5cee0;background:transparent;}
            QLabel[sectionTitle=true]{color:#edf1fa;font-size:13px;font-weight:600;}
            QFrame#styleCatalog,QFrame#styleSettings{background:#232323;border:1px solid #414958;border-radius:12px;}
            QFrame#styleSettingsForm{background:#232323;border:none;}
            QLineEdit,QTextEdit,QComboBox,QDoubleSpinBox{background:#191919;color:#e4ebf8;
                border:1px solid #465167;border-radius:7px;padding:7px;selection-background-color:#526bb0;}
            QLineEdit:focus,QTextEdit:focus,QComboBox:focus,QDoubleSpinBox:focus{border-color:#91a7ff;}
            QComboBox QAbstractItemView{background:#252b38;color:#e4ebf8;selection-background-color:#43547c;}
            QListWidget{background:transparent;border:none;outline:none;}
            QListWidget::item{padding:9px 7px;margin:3px 0;border-radius:8px;color:#bdc9df;}
            QListWidget::item:hover{background:#303030;}
            QListWidget::item:selected{background:#353535;color:#fff;}
            QScrollArea{background:transparent;border:none;}
            QScrollBar:vertical{background:#202020;width:7px;}
            QScrollBar::handle:vertical{background:#52617a;min-height:28px;border-radius:3px;}
            QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}
            QPushButton{background:#303030;color:#dce5f8;border:1px solid #4d5a72;border-radius:7px;padding:8px 12px;}
            QPushButton:hover{background:#3a3a3a;border-color:#91a7ff;}
            QPushButton#saveStyle{background:#3d8ef8;color:#ffffff;font-weight:600;border-color:#3d8ef8;}
            QPushButton#stylePromptToggle{background:transparent;text-align:left;border:none;color:#9cafd6;padding:6px 0;}
            QCheckBox{color:#cbd5e9;spacing:8px;padding:5px 0;}
            QCheckBox:disabled{color:#778298;}
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)
        heading = QLabel("选择风格，再调整应用方式")
        heading.setStyleSheet("font-size:19px;font-weight:600;color:#f0f4ff;")
        root.addWidget(heading)
        note = QLabel("色板帮助区分风格，实际效果以生成结果为准。保存后会应用到当前图片节点。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#929fb8;")
        root.addWidget(note)
        body = QHBoxLayout()
        body.setSpacing(14)
        catalog = QFrame()
        catalog.setObjectName("styleCatalog")
        left = QVBoxLayout(catalog)
        left.setContentsMargins(14, 14, 14, 14)
        left.setSpacing(10)
        self.search = QLineEdit()
        self.search.setObjectName("styleSearch")
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText("搜索名称、材质或效果…")
        left.addWidget(self.search)
        self.category = QComboBox()
        self.category.setObjectName("styleCategory")
        self.category.addItem("全部分类", "")
        for value in dict.fromkeys(item["category"] for item in STYLE_PRESETS):
            self.category.addItem(value, value)
        left.addWidget(self.category)
        self.presets = QListWidget()
        self.presets.setObjectName("stylePresets")
        self.presets.setIconSize(QSize(38, 38))
        self.presets.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left.addWidget(self.presets, 1)
        self.catalog_count = QLabel()
        self.catalog_count.setStyleSheet("color:#929fb8;font-size:11px;")
        left.addWidget(self.catalog_count)
        body.addWidget(catalog, 4)
        settings = QFrame()
        settings.setObjectName("styleSettings")
        right = QVBoxLayout(settings)
        right.setContentsMargins(16, 16, 16, 16)
        right.setSpacing(12)
        chosen = QHBoxLayout()
        self.swatch = QLabel()
        self.swatch.setFixedSize(48, 48)
        chosen.addWidget(self.swatch)
        info = QVBoxLayout()
        self.style_name = QLabel()
        self.style_name.setObjectName("selectedStyleName")
        self.style_name.setStyleSheet("color:#edf1fa;font-size:17px;font-weight:600;")
        self.style_description = QLabel()
        self.style_description.setWordWrap(True)
        self.style_description.setStyleSheet("color:#9faec7;font-size:12px;")
        info.addWidget(self.style_name)
        info.addWidget(self.style_description)
        chosen.addLayout(info, 1)
        right.addLayout(chosen)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QFrame()
        content.setObjectName("styleSettingsForm")
        form = QVBoxLayout(content)
        form.setContentsMargins(0, 0, 5, 0)
        form.setSpacing(12)

        def title(text):
            label = QLabel(text)
            label.setProperty("sectionTitle", True)
            form.addWidget(label)

        title("应用范围与强度")
        controls = QHBoxLayout()
        self.scope = QComboBox()
        self.scope.setObjectName("styleScope")
        for label, value in (("整张画面", "whole"), ("只改主体", "subject"), ("只改背景", "background")):
            self.scope.addItem(label, value)
        self.scope.setCurrentIndex(max(0, self.scope.findData(payload.get("style_scope", "whole"))))
        controls.addWidget(self.scope, 1)
        controls.addWidget(QLabel("强度"))
        self.strength = QDoubleSpinBox()
        self.strength.setObjectName("styleStrength")
        self.strength.setRange(20, 100)
        self.strength.setDecimals(0)
        self.strength.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.strength.setToolTip("输入 20–100，数值越高，风格变化越明显")
        self.strength.setSuffix("%")
        preset = STYLE_PRESET_BY_ID.get(self.selected_id, {})
        self.strength.setValue(float(payload.get("style_strength") or preset.get("strength") or 70))
        controls.addWidget(self.strength)
        form.addLayout(controls)
        self.strength_hint = QLabel()
        self.strength_hint.setWordWrap(True)
        self.strength_hint.setStyleSheet("color:#929fb8;font-size:11px;")
        form.addWidget(self.strength_hint)
        title("保留原图内容" if has_reference else "保留原图内容 · 接入参考图后生效")
        preserve = payload.get("style_preserve")
        if not isinstance(preserve, list):
            preserve = ["identity", "pose", "composition", "background", "lighting"]
        checks = QGridLayout()
        self.preserve_checks = {}
        for index, (key, label) in enumerate((("identity", "主体身份"), ("pose", "动作姿势"),
                ("composition", "构图机位"), ("background", "背景结构"), ("lighting", "光线方向"))):
            check = checkbox_type(label)
            check.setObjectName("stylePreserve:" + key)
            check.setChecked(key in preserve)
            self.preserve_checks[key] = check
            checks.addWidget(check, index // 2, index % 2)
        form.addLayout(checks)
        title("补充要求（可选）")
        self.custom = QTextEdit()
        self.custom.setObjectName("styleCustom")
        self.custom.setAcceptRichText(False)
        self.custom.setFixedHeight(86)
        self.custom.setPlaceholderText("例如：边缘加一点蓝色荧光，保持产品上的文字清晰。")
        self.custom.setPlainText(str(payload.get("style_custom") or ""))
        form.addWidget(self.custom)
        self.prompt_toggle = QPushButton("查看完整生成描述")
        self.prompt_toggle.setObjectName("stylePromptToggle")
        self.prompt_toggle.setCheckable(True)
        form.addWidget(self.prompt_toggle)
        self.preview = QTextEdit()
        self.preview.setObjectName("stylePromptPreview")
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(150)
        self.preview.hide()
        form.addWidget(self.preview)
        form.addStretch()
        scroll.setWidget(content)
        right.addWidget(scroll, 1)
        body.addWidget(settings, 6)
        root.addLayout(body, 1)
        footer = QHBoxLayout()
        clear = QPushButton("清除风格")
        clear.setObjectName("clearStyle")
        clear.clicked.connect(self.clear_style)
        footer.addWidget(clear)
        footer.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("应用到图片节点")
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("saveStyle")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        root.addLayout(footer)
        self.search.textChanged.connect(self.populate_presets)
        self.category.currentIndexChanged.connect(self.populate_presets)
        self.presets.currentItemChanged.connect(self.choose_preset)
        self.custom.textChanged.connect(self.update_preview)
        self.scope.currentIndexChanged.connect(self.update_preview)
        self.strength.valueChanged.connect(self.update_preview)
        for check in self.preserve_checks.values():
            check.toggled.connect(self.update_preview)
        self.prompt_toggle.toggled.connect(self.toggle_preview)
        self.populate_presets()
        self.update_preview()

    def populate_presets(self, *_):
        # Filtering never changes the user's selected style, including when it
        # is temporarily absent from the visible results.
        self.presets.blockSignals(True)
        self.presets.clear()
        values = [{"id": "", "label": "不使用预设", "summary": "仅使用你的补充要求", "category": "", "swatch": ""}, *STYLE_PRESETS]
        query = self.search.text().strip().casefold()
        category = str(self.category.currentData() or "")
        count = 0
        for preset in values:
            if preset["id"] and category and preset["category"] != category:
                continue
            if query and not all(term in f"{preset['label']} {preset['summary']} {preset['category']}".casefold() for term in query.split()):
                continue
            item = QListWidgetItem(f"{preset['label']}\n{preset['summary']}")
            item.setIcon(self.icon_factory(str(preset.get("swatch") or "linear-gradient(135deg,#252832,#5a6070)")))
            item.setSizeHint(QSize(0, 64))
            item.setData(Qt.ItemDataRole.UserRole, preset["id"])
            item.setToolTip(f"{preset['label']} · {preset['category']}\n{preset['summary']}")
            self.presets.addItem(item)
            if preset["id"] == self.selected_id:
                self.presets.setCurrentItem(item)
            count += bool(preset["id"])
        self.presets.blockSignals(False)
        self.catalog_count.setText(f"找到 {count} 种风格" if query or category else f"{count} 种风格 · 可搜索名称或效果")

    def choose_preset(self, item, _old):
        if item is not None:
            self.selected_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
            self.update_preview()

    def settings(self):
        return {
            "style_preset": self.selected_id,
            "style_custom": self.custom.toPlainText().strip(),
            "style_scope": str(self.scope.currentData() or "whole"),
            "style_strength": int(self.strength.value()),
            "style_preserve": [key for key, check in self.preserve_checks.items() if check.isChecked()],
            "style": str(STYLE_PRESET_BY_ID.get(self.selected_id, {}).get("label") or ""),
        }

    def update_preview(self, *_):
        preset = STYLE_PRESET_BY_ID.get(self.selected_id, {})
        self.style_name.setText(str(preset.get("label") or "自定义风格"))
        self.style_description.setText(str(preset.get("summary") or "选择左侧预设，或在下方填写自己的风格要求。"))
        self.swatch.setPixmap(self.icon_factory(str(preset.get("swatch") or "linear-gradient(135deg,#252832,#5a6070)"), 48).pixmap(48, 48))
        value = self.strength.value()
        self.strength_hint.setText("轻度：更多保留原图质感" if value < 50 else "中度：材质和工艺清晰可辨" if value < 78 else "强烈：明显重塑风格，仍保留勾选的原图内容")
        self.preview.setPlainText(compile_style_prompt(self.settings(), self.has_reference) or "选择预设或填写补充要求后，这里会显示生成描述。")

    def clear_style(self):
        self.selected_id = ""
        self.custom.clear()
        self.populate_presets()
        self.update_preview()

    def toggle_preview(self, checked):
        self.preview.setVisible(checked)
        self.prompt_toggle.setText("收起完整生成描述" if checked else "查看完整生成描述")
