"""
小欢语音 - AI 脚本生成器 (Tab 3)
产品描述 → AI 生成广告脚本 + 翻译功能
支持产品名标签记忆，情感风格词
"""
import json
import html
import os
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QComboBox, QProgressBar, QLineEdit, QTabWidget,
    QScrollArea, QFrame, QSpinBox, QMessageBox, QTextBrowser, QToolButton,
    QSplitter, QDialog, QDialogButtonBox, QCheckBox, QInputDialog,
    QMenu,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QTimer, QSize, QUrl, QEvent
from PyQt6.QtGui import QPixmap, QIcon, QImage, QDesktopServices

from ai.storyboard import (
    extract_json, normalize_storyboard, rebuild_continuity,
    sync_legacy_bindings, normalize_video_link_mode, resolve_video_link_mode,
    apply_dialogue_audio_duration, route_shot_generation, normalize_performance,
    build_shot_contract,
)
from ai import TaskRequest, ProviderDomain
from ai.service import get_ai_manager
from ai.assets import approved_asset_path, asset_is_approved

# 标签存储
_TAGS_FILE = Path(__file__).parent.parent / "work_temp" / "_script_tags.json"

def _load_tags() -> list:
    if _TAGS_FILE.exists():
        try:
            return json.loads(_TAGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []

def _save_tags(tags: list):
    _TAGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TAGS_FILE.write_text(json.dumps(tags, ensure_ascii=False), encoding="utf-8")

# 翻译语种
LANGUAGES = [
    ("中文","zh"), ("英语","en"),("日语","ja"),("韩语","ko"),("泰语","th"),
    ("越南语","vi"),("西语","es"),("葡语","pt"),("阿语","ar"),("印尼语","id"),
]


class _DoubleClickToolButton(QToolButton):
    doubleClicked = pyqtSignal()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class _DoubleClickPreviewLabel(QLabel):
    doubleClicked = pyqtSignal()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


def _scene_camera_role(value: str) -> str:
    slot = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return {
        "a": "camera_a", "camera_a": "camera_a", "a机位": "camera_a",
        "b": "camera_b", "camera_b": "camera_b", "b机位": "camera_b",
        "a_reverse": "reverse_a", "reverse_a": "reverse_a", "a反打": "reverse_a",
        "b_reverse": "reverse_b", "reverse_b": "reverse_b", "b反打": "reverse_b",
        "insert": "detail", "detail": "detail", "特写": "detail",
    }.get(slot, "")


class _MultiAssetSelectDialog(QDialog):
    """给单个镜头选择多个主体或元素；主绑定仍由卡片下拉框控制。"""

    def __init__(self, title: str, items: list, selected_ids: list[str],
                 kind_attr: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(420, 520)
        self.setStyleSheet(
            "QDialog{background:#17171b;color:#eee;}QCheckBox{color:#ddd;padding:6px;}"
            "QScrollArea{background:#111114;border:1px solid #303038;border-radius:6px;}"
        )
        root = QVBoxLayout(self)
        hint = QLabel("可同时选择多个；生成时每个资产会获得独立的参考图编号和名称。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#858591;font-size:11px;")
        root.addWidget(hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        self._checks: list[tuple[QCheckBox, str]] = []
        selected = set(selected_ids or [])
        for item in items:
            item_id = str(getattr(item, "id", ""))
            suffix = getattr(item, kind_attr, "") if kind_attr else ""
            text = getattr(item, "name", "未命名")
            if suffix:
                text += f" · {suffix}"
            check = QCheckBox(text)
            check.setChecked(item_id in selected)
            layout.addWidget(check)
            self._checks.append((check, item_id))
        layout.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def selected_ids(self) -> list[str]:
        return [item_id for check, item_id in self._checks if check.isChecked()]


class StoryboardShotCard(QFrame):
    """单个镜头卡片：可编辑核心描述，并发出对应素材生成请求。"""
    generate_image = pyqtSignal(str)
    generate_image_edit = pyqtSignal(str)
    generate_video = pyqtSignal(str)
    image_to_video = pyqtSignal(str)
    refine_image = pyqtSignal(str)
    preview_requested = pyqtSignal(str, str)
    selected = pyqtSignal(str)
    asset_selected = pyqtSignal(str, str)
    binding_changed = pyqtSignal(str)
    inspect_bindings = pyqtSignal(str)
    change_image_model = pyqtSignal(str)
    video_link_mode_changed = pyqtSignal(str)
    prepare_assets = pyqtSignal(str)

    def __init__(self, shot: dict, characters: list | None = None,
                 scenes: list | None = None, elements: list | None = None,
                 parent=None):
        super().__init__(parent)
        self.shot = shot
        self._assets_ready = False
        self.setObjectName("StoryboardShotCard")
        self.setStyleSheet(
            "#StoryboardShotCard{background:#151517;border:1px solid #29292d;"
            "border-radius:8px;} QLabel{background:transparent;}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(7)

        head = QHBoxLayout()
        self._title = QLabel()
        self._title.setStyleSheet("color:#fff;font-size:13px;font-weight:bold;")
        head.addWidget(self._title)
        head.addStretch()
        self._asset_status = QLabel()
        self._asset_status.setStyleSheet("color:#8f96a5;font-size:11px;")
        head.addWidget(self._asset_status)
        inspect_btn = QPushButton("查看结果 →")
        inspect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        inspect_btn.setStyleSheet(
            "QPushButton{background:transparent;color:#9d86eb;border:none;padding:2px 4px;}"
            "QPushButton:hover{color:#b6a4f0;}"
        )
        inspect_btn.clicked.connect(lambda: self.selected.emit(self.shot["id"]))
        head.addWidget(inspect_btn)
        inspect_btn.hide()
        root.addLayout(head)

        self._task_progress = QProgressBar()
        self._task_progress.setRange(0, 100)
        self._task_progress.setFixedHeight(3)
        self._task_progress.setTextVisible(False)
        self._task_progress.setVisible(False)
        self._task_progress.setStyleSheet(
            "QProgressBar{background:#222228;border:none;}"
            "QProgressBar::chunk{background:#8b6cf0;}"
        )
        root.addWidget(self._task_progress)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(7)
        meta = QLabel(
            f"{float(shot.get('duration', 0) or 0):g} 秒  ·  "
            f"{shot.get('shot_size', '中景')}  ·  {shot.get('camera', '固定镜头')}"
        )
        meta.setStyleSheet("color:#7e8797;font-size:11px;")
        meta_row.addWidget(meta)
        meta_row.addStretch()
        self._video_link_combo = QComboBox()
        self._video_link_combo.addItem("自动判断", "auto")
        self._video_link_combo.addItem("直接切镜", "cut")
        self._video_link_combo.addItem("连续续拍", "continue")
        self._video_link_combo.addItem("首尾过渡", "bridge")
        self._video_link_combo.setToolTip(
            "当前镜头如何接到下一镜：普通换机位用直接切镜；同一长镜头用连续续拍；"
            "只有变身或画面渐变才用首尾过渡。")
        self._video_link_combo.setStyleSheet(
            "QComboBox{background:#202026;color:#c9c9d0;border:1px solid #383842;"
            "border-radius:5px;padding:3px 7px;font-size:10px;}"
            "QComboBox::drop-down{border:none;}QComboBox QAbstractItemView{"
            "background:#202026;color:#ddd;selection-background-color:#7657dd;}")
        link_index = self._video_link_combo.findData(
            normalize_video_link_mode(shot.get("video_link_mode")))
        self._video_link_combo.setCurrentIndex(link_index if link_index >= 0 else 0)
        self._video_link_combo.currentIndexChanged.connect(self._sync_video_link_mode)
        root.addLayout(meta_row)

        purpose_bits = []
        if shot.get("dramatic_purpose"):
            purpose_bits.append(f"镜头目的：{shot['dramatic_purpose']}")
        if shot.get("entry_state") or shot.get("exit_state"):
            purpose_bits.append(
                f"状态：{shot.get('entry_state') or '承接上镜'} → "
                f"{shot.get('exit_state') or '进入下镜'}")
        self._contract_summary = QLabel("  ·  ".join(purpose_bits))
        self._contract_summary.setWordWrap(True)
        self._contract_summary.setStyleSheet("color:#9d86c9;font-size:10px;")
        self._contract_summary.setVisible(bool(purpose_bits))
        root.addWidget(self._contract_summary)

        scene_label = QLabel("这个镜头要拍什么")
        scene_label.setStyleSheet("color:#d7d7de;font-size:11px;font-weight:bold;")
        root.addWidget(scene_label)
        self.scene_edit = QTextEdit()
        self.scene_edit.setPlainText(shot.get("scene", ""))
        self.scene_edit.setMaximumHeight(66)
        self.scene_edit.setPlaceholderText("用一句话说清画面中的人、场景和动作……")
        self.scene_edit.setStyleSheet(_EDITOR)
        self.scene_edit.textChanged.connect(self._sync)
        root.addWidget(self.scene_edit)

        performance = shot.get("performance") or normalize_performance(shot)
        shot["performance"] = performance
        self._performance_summary = QLabel()
        self._performance_summary.setWordWrap(True)
        self._performance_summary.setStyleSheet("color:#c8a97e;font-size:11px;")
        root.addWidget(self._performance_summary)
        self._refresh_performance_summary()

        self._binding_summary = QLabel()
        self._binding_summary.setWordWrap(True)
        preparation_row = QHBoxLayout()
        preparation_row.setSpacing(7)
        preparation_row.addWidget(self._binding_summary, 1)
        self._prepare_assets_btn = QPushButton("一键识别并准备素材")
        self._prepare_assets_btn.setToolTip(
            "自动识别本镜头需要的场景、主体和元素；已定稿的跳过，只生成缺失项")
        self._prepare_assets_btn.setStyleSheet(
            "QPushButton{background:#20382f;color:#8be0b5;border:1px solid #32614d;"
            "border-radius:5px;padding:5px 10px;font-size:10px;}"
            "QPushButton:hover{background:#28503f;}"
            "QPushButton:disabled{background:#202522;color:#6d8177;border-color:#2c3732;}")
        self._prepare_assets_btn.clicked.connect(
            lambda: self.prepare_assets.emit(str(self.shot.get("id", ""))))
        preparation_row.addWidget(self._prepare_assets_btn)
        self._prepare_assets_btn.hide()
        root.addLayout(preparation_row)

        main_actions = QHBoxLayout()
        self._primary_btn = QPushButton("生成这张画面")
        self._primary_btn.setMinimumHeight(36)
        self._primary_btn.setStyleSheet(_DIRECTOR_IMG)
        self._primary_btn.clicked.connect(self._run_primary_action)
        main_actions.addWidget(self._primary_btn)
        self._regenerate_btn = QPushButton("再生成一组")
        self._regenerate_btn.setStyleSheet(_GHOST)
        self._regenerate_btn.clicked.connect(
            lambda: self.generate_image.emit(self.shot["id"]))
        self._change_model_btn = QPushButton("换模型再生成 ▾")
        self._change_model_btn.setStyleSheet(_GHOST)
        self._change_model_btn.clicked.connect(
            lambda: self.change_image_model.emit(self.shot["id"]))
        main_actions.addStretch()
        self._more_btn = QPushButton("更多设置 ▾")
        self._more_btn.setStyleSheet(_GHOST)
        self._more_btn.clicked.connect(self._toggle_more_settings)
        main_actions.addWidget(self._more_btn)
        root.addLayout(main_actions)

        self._advanced_box = QFrame()
        self._advanced_box.setObjectName("ShotAdvancedBox")
        self._advanced_box.setStyleSheet(
            "#ShotAdvancedBox{background:#111115;border:1px solid #2a2a31;"
            "border-radius:7px;}#ShotAdvancedBox QLabel{background:transparent;}"
        )
        advanced = QVBoxLayout(self._advanced_box)
        advanced.setContentsMargins(10, 9, 10, 9)
        advanced.setSpacing(7)

        performance_head = QLabel("对白与表演（生成视频前会先生成对白并自动校准时长）")
        performance_head.setStyleSheet(
            "color:#d4b078;font-size:11px;font-weight:bold;")
        advanced.addWidget(performance_head)
        performance_row = QHBoxLayout()
        performance_row.addWidget(QLabel("类型"))
        self._performance_type = QComboBox()
        self._performance_type.addItem("无台词", "none")
        self._performance_type.addItem("角色对白", "dialogue")
        self._performance_type.addItem("画外旁白", "voiceover")
        type_index = self._performance_type.findData(
            performance.get("line_type", "none"))
        self._performance_type.setCurrentIndex(type_index if type_index >= 0 else 0)
        performance_row.addWidget(self._performance_type)
        performance_row.addWidget(QLabel("说话者"))
        self._performance_speaker = QLineEdit(
            str(performance.get("speaker") or ""))
        self._performance_speaker.setPlaceholderText("角色名称")
        self._performance_speaker.setMaximumWidth(150)
        performance_row.addWidget(self._performance_speaker)
        performance_row.addWidget(QLabel("情绪"))
        self._performance_emotion = QComboBox()
        self._performance_emotion.addItems(
            ["自然", "开心", "愤怒", "紧张", "悲伤", "害怕", "怀疑", "冷漠"])
        emotion = str(performance.get("emotion") or "自然")
        emotion_index = self._performance_emotion.findText(emotion)
        self._performance_emotion.setCurrentIndex(
            emotion_index if emotion_index >= 0 else 0)
        performance_row.addWidget(self._performance_emotion)
        performance_row.addWidget(QLabel("强度"))
        self._performance_intensity = QComboBox()
        for label, value in (("轻微", 0.25), ("自然", 0.5),
                             ("明显", 0.75), ("强烈", 1.0)):
            self._performance_intensity.addItem(label, value)
        target_intensity = float(performance.get("emotion_intensity", 0.5) or 0.5)
        closest = min(
            range(self._performance_intensity.count()),
            key=lambda index: abs(float(
                self._performance_intensity.itemData(index)) - target_intensity))
        self._performance_intensity.setCurrentIndex(closest)
        performance_row.addWidget(self._performance_intensity)
        performance_row.addStretch()
        advanced.addLayout(performance_row)

        self._dialogue_edit = QTextEdit()
        self._dialogue_edit.setPlainText(str(performance.get("dialogue") or ""))
        self._dialogue_edit.setMaximumHeight(54)
        self._dialogue_edit.setPlaceholderText("必须原样说出的对白；无台词时留空")
        self._dialogue_edit.setStyleSheet(_EDITOR)
        advanced.addWidget(self._dialogue_edit)
        acting_row = QHBoxLayout()
        acting_row.addWidget(QLabel("视线"))
        self._performance_gaze = QLineEdit(
            str(performance.get("gaze_target") or ""))
        self._performance_gaze.setPlaceholderText("看向谁或哪里")
        acting_row.addWidget(self._performance_gaze)
        acting_row.addWidget(QLabel("表情"))
        self._performance_expression = QLineEdit(
            str(performance.get("expression") or ""))
        self._performance_expression.setPlaceholderText("如：先克制，随后震惊")
        acting_row.addWidget(self._performance_expression)
        acting_row.addWidget(QLabel("动作"))
        self._performance_gesture = QLineEdit(
            str(performance.get("gesture") or performance.get("body_action") or ""))
        self._performance_gesture.setPlaceholderText("手势或身体动作")
        acting_row.addWidget(self._performance_gesture)
        advanced.addLayout(acting_row)

        binding_head = QHBoxLayout()
        binding_title = QLabel("参考素材（不选时沿用项目默认）")
        binding_title.setStyleSheet("color:#b9a8ed;font-size:11px;font-weight:bold;")
        binding_head.addWidget(binding_title)
        binding_head.addStretch()
        inspect_binding_btn = QPushButton("检查参考素材")
        inspect_binding_btn.setStyleSheet(_GHOST)
        inspect_binding_btn.clicked.connect(
            lambda: self.inspect_bindings.emit(str(self.shot.get("id", ""))))
        binding_head.addWidget(inspect_binding_btn)
        advanced.addLayout(binding_head)

        binding_row = QHBoxLayout()
        binding_row.setSpacing(6)
        binding_row.addWidget(QLabel("场景"))
        self._scene_combo = QComboBox()
        self._scene_combo.setMinimumWidth(135)
        binding_row.addWidget(self._scene_combo)
        binding_row.addWidget(QLabel("主体"))
        self._character_combo = QComboBox()
        self._character_combo.setMinimumWidth(135)
        binding_row.addWidget(self._character_combo)
        self._more_characters_btn = QPushButton("多主体")
        self._more_characters_btn.setStyleSheet(_GHOST)
        self._more_characters_btn.clicked.connect(self._pick_more_characters)
        binding_row.addWidget(self._more_characters_btn)
        binding_row.addStretch()
        advanced.addLayout(binding_row)

        element_row = QHBoxLayout()
        element_row.setSpacing(6)
        element_row.addWidget(QLabel("指定元素"))
        self._element_combo = QComboBox()
        self._element_combo.setMinimumWidth(135)
        element_row.addWidget(self._element_combo)
        self._more_elements_btn = QPushButton("多元素")
        self._more_elements_btn.setStyleSheet(_GHOST)
        self._more_elements_btn.setToolTip("附加元素使用 AI 制片画布中设置的默认方式和位置")
        self._more_elements_btn.clicked.connect(self._pick_more_elements)
        element_row.addWidget(self._more_elements_btn)
        self._element_mode = QComboBox()
        self._element_mode.addItem("精确植入（不重画）", "exact")
        self._element_mode.addItem("AI 参考（可重画）", "reference")
        mode_index = self._element_mode.findData(shot.get("element_mode", "exact"))
        self._element_mode.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        element_row.addWidget(self._element_mode)
        self._element_placement = QLineEdit(shot.get("element_placement", ""))
        self._element_placement.setPlaceholderText("元素位置，如：手机屏幕 / 桌面中央")
        self._element_placement.setMinimumWidth(155)
        element_row.addWidget(self._element_placement, 1)
        advanced.addLayout(element_row)

        continuity_row = QHBoxLayout()
        continuity_row.addWidget(QLabel("与下一镜连接"))
        continuity_row.addWidget(self._video_link_combo)
        continuity_row.addStretch()
        continuity_hint = QLabel("默认自动判断；只有续拍或变形过渡时需要修改")
        continuity_hint.setStyleSheet("color:#70707a;font-size:10px;")
        continuity_row.addWidget(continuity_hint)
        advanced.addLayout(continuity_row)

        binding_style = (
            "QComboBox,QLineEdit{background:#202026;color:#ddd;border:1px solid #383842;"
            "border-radius:5px;padding:4px 7px;}QComboBox::drop-down{border:none;}"
            "QComboBox QAbstractItemView{background:#202026;color:#ddd;"
            "selection-background-color:#7657dd;}"
        )
        for widget in (self._scene_combo, self._character_combo,
                       self._element_combo, self._element_mode,
                       self._element_placement, self._performance_type,
                       self._performance_speaker, self._performance_emotion,
                       self._performance_intensity, self._performance_gaze,
                       self._performance_expression, self._performance_gesture):
            widget.setStyleSheet(binding_style)

        prompt_stack = QVBoxLayout()
        prompt_stack.setSpacing(4)
        image_prompt_label = QLabel("画面提示词（高级）")
        image_prompt_label.setStyleSheet("color:#70bd98;font-size:10px;")
        prompt_stack.addWidget(image_prompt_label)
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setPlainText(
            shot.get("image_prompt") or shot.get("scene", ""))
        self.prompt_edit.setMaximumHeight(58)
        self.prompt_edit.setPlaceholderText("画面的构图、姿态、表情和光线")
        self.prompt_edit.setStyleSheet(_EDITOR)
        self.prompt_edit.textChanged.connect(self._sync)
        prompt_stack.addWidget(self.prompt_edit)
        video_prompt_label = QLabel("动画提示词（高级）")
        video_prompt_label.setStyleSheet("color:#74aee5;font-size:10px;")
        prompt_stack.addWidget(video_prompt_label)
        self.video_prompt_edit = QTextEdit()
        self.video_prompt_edit.setPlainText(
            shot.get("video_prompt") or shot.get("action") or "")
        self.video_prompt_edit.setMaximumHeight(58)
        self.video_prompt_edit.setPlaceholderText("这张图如何动起来：人物动作、运镜和环境运动")
        self.video_prompt_edit.setStyleSheet(_EDITOR)
        self.video_prompt_edit.textChanged.connect(self._sync)
        prompt_stack.addWidget(self.video_prompt_edit)
        advanced.addLayout(prompt_stack)

        advanced_actions = QHBoxLayout()
        advanced_actions.addWidget(self._regenerate_btn)
        advanced_actions.addWidget(self._change_model_btn)
        self._btn_i2i = QPushButton("参考当前图再生成")
        self._btn_i2i.setStyleSheet(_DIRECTOR_IMG)
        self._btn_i2i.clicked.connect(
            lambda: self.generate_image_edit.emit(self.shot["id"]))
        advanced_actions.addWidget(self._btn_i2i)
        self._btn_i2v = QPushButton("用当前图生成视频")
        self._btn_i2v.setStyleSheet(_DIRECTOR_VIDEO)
        self._btn_i2v.clicked.connect(lambda: self.image_to_video.emit(self.shot["id"]))
        advanced_actions.addWidget(self._btn_i2v)
        btn_vid = QPushButton("跳过图片，直接生成视频")
        btn_vid.setStyleSheet(_GHOST)
        btn_vid.setToolTip("不用定稿图作为首帧，人物和场景更容易变化")
        btn_vid.clicked.connect(lambda: self.generate_video.emit(self.shot["id"]))
        advanced_actions.addWidget(btn_vid)
        self._btn_refine = QPushButton("送到 PS 精修")
        self._btn_refine.setStyleSheet(_GHOST)
        self._btn_refine.clicked.connect(lambda: self.refine_image.emit(self.shot["id"]))
        advanced_actions.addWidget(self._btn_refine)
        advanced.addLayout(advanced_actions)
        root.addWidget(self._advanced_box)
        self._advanced_box.setVisible(False)

        self._assets_widget = QWidget(self)
        self._assets_layout = QHBoxLayout(self._assets_widget)
        self._assets_layout.setContentsMargins(0, 0, 0, 0)
        self._assets_layout.setSpacing(7)
        self._assets_layout.addStretch()
        self._assets_widget.setVisible(False)

        self.set_resources(characters or [], scenes or [], elements or [])
        self._scene_combo.currentIndexChanged.connect(self._sync_bindings)
        self._character_combo.currentIndexChanged.connect(self._sync_bindings)
        self._element_combo.currentIndexChanged.connect(self._sync_bindings)
        self._element_mode.currentIndexChanged.connect(self._sync_bindings)
        self._element_placement.editingFinished.connect(self._sync_bindings)
        self._performance_type.currentIndexChanged.connect(self._sync_performance)
        self._performance_speaker.editingFinished.connect(self._sync_performance)
        self._performance_emotion.currentIndexChanged.connect(self._sync_performance)
        self._performance_intensity.currentIndexChanged.connect(self._sync_performance)
        self._dialogue_edit.textChanged.connect(self._sync_performance)
        self._performance_gaze.editingFinished.connect(self._sync_performance)
        self._performance_expression.editingFinished.connect(self._sync_performance)
        self._performance_gesture.editingFinished.connect(self._sync_performance)
        self.refresh_status()

        # 点击卡片本身或描述/提示词编辑区，都直接切换当前镜头；控件原本的
        # 编辑、下拉和按钮行为继续执行，不再要求用户瞄准“查看结果”。
        for target in self.findChildren(QWidget):
            target.installEventFilter(self)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(str(self.shot.get("id", "")))
        super().mousePressEvent(event)

    def eventFilter(self, watched, event):
        if (event.type() == QEvent.Type.MouseButtonPress and
                event.button() == Qt.MouseButton.LeftButton):
            self.selected.emit(str(self.shot.get("id", "")))
        return super().eventFilter(watched, event)

    def _sync(self):
        self.shot["scene"] = self.scene_edit.toPlainText().strip()
        self.shot["image_prompt"] = self.prompt_edit.toPlainText().strip()
        self.shot["video_prompt"] = self.video_prompt_edit.toPlainText().strip()

    def _refresh_performance_summary(self):
        performance = self.shot.get("performance") or normalize_performance(self.shot)
        dialogue = str(performance.get("dialogue") or "").strip()
        if not dialogue:
            self._performance_summary.hide()
            return
        self._performance_summary.show()
        if performance.get("line_type") == "dialogue":
            speaker = str(performance.get("speaker") or "当前角色")
            emotion = str(performance.get("emotion") or "自然")
            prefix = f"对白 · {speaker} · {emotion}"
        else:
            prefix = "旁白"
        self._performance_summary.setText(f"{prefix}：{dialogue}")

    def _sync_performance(self, *_):
        performance = self.shot.setdefault(
            "performance", normalize_performance(self.shot))
        performance.update({
            "line_type": self._performance_type.currentData() or "none",
            "speaker": self._performance_speaker.text().strip(),
            "dialogue": self._dialogue_edit.toPlainText().strip(),
            "emotion": self._performance_emotion.currentText(),
            "emotion_intensity": float(
                self._performance_intensity.currentData() or 0.5),
            "gaze_target": self._performance_gaze.text().strip(),
            "expression": self._performance_expression.text().strip(),
            "gesture": self._performance_gesture.text().strip(),
            "body_action": self._performance_gesture.text().strip(),
        })
        self.shot["voiceover"] = performance["dialogue"]
        self.shot["generation_route"] = route_shot_generation(self.shot)
        performance["route"] = self.shot["generation_route"]
        # 修改对白后旧语音不再可信，生成视频时会自动重新准备。
        if performance["dialogue"] != str(
                self.shot.get("dialogue_audio_source_text") or ""):
            self.shot["dialogue_audio_status"] = ""
            self.shot["dialogue_audio"] = ""
            self.shot["dialogue_audio_duration"] = 0.0
            self.shot["dialogue_audio_source_text"] = ""
        self._refresh_performance_summary()
        self.binding_changed.emit(str(self.shot.get("id", "")))

    def _sync_video_link_mode(self, *_):
        self.shot["video_link_mode"] = (
            self._video_link_combo.currentData() or "auto")
        self.video_link_mode_changed.emit(str(self.shot.get("id", "")))

    def set_resolved_video_link_mode(self, mode: str, has_next: bool = True):
        if not has_next:
            text = "自动：自然结束"
        else:
            text = {
                "cut": "自动：直接切镜",
                "continue": "自动：连续续拍",
                "bridge": "自动：首尾过渡",
            }.get(mode, "自动判断")
        self._video_link_combo.setItemText(0, text)

    def set_asset_preparation_status(self, text: str, ready: bool = False):
        self._assets_ready = bool(ready)
        self._prepare_assets_btn.setText(
            text + (" · 点击检查" if ready and "点击" not in text else ""))
        # 保持可点击；已就绪时再次点击会明确说明当前素材状态。
        self._prepare_assets_btn.setEnabled(True)
        self._binding_summary.setText(text)
        self._binding_summary.setStyleSheet(
            "color:#66c398;font-size:10px;" if ready
            else "color:#d1a263;font-size:10px;")
        self._refresh_primary_action()

    def _toggle_more_settings(self):
        show = self._advanced_box.isHidden()
        self._advanced_box.setVisible(show)
        self._more_btn.setText("收起设置 ▴" if show else "更多设置 ▾")

    def _selected_asset_kind(self) -> str:
        assets = [asset for asset in self.shot.get("assets", [])
                  if isinstance(asset, dict)]
        selected = (self.shot.get("preview_asset") or
                    self.shot.get("selected_asset", ""))
        if not selected and assets:
            selected = str(assets[-1].get("path") or "")
            self.shot["preview_asset"] = selected
            self.shot["selected_asset"] = selected
        for asset in assets:
            if isinstance(asset, dict) and asset.get("path") == selected:
                return str(asset.get("kind") or "image")
        return ""

    def _run_primary_action(self):
        if self.shot.get("selected_video_asset"):
            self.selected.emit(self.shot["id"])
        elif self.shot.get("selected_image_asset") or self.shot.get("anchor_frame_id"):
            self.image_to_video.emit(self.shot["id"])
        elif any(isinstance(asset, dict) and asset.get("kind") == "image"
                 for asset in self.shot.get("assets", [])):
            QMessageBox.information(
                self, "请先定稿图片",
                "当前镜头已有图片候选，但还没有定稿图片。\n\n"
                "请在右侧结果面板点击候选下方的“设为定稿图片”。")
        elif not self._assets_ready:
            self.prepare_assets.emit(str(self.shot.get("id", "")))
        else:
            self.generate_image.emit(self.shot["id"])

    def _refresh_primary_action(self):
        assets = [asset for asset in self.shot.get("assets", [])
                  if isinstance(asset, dict) and
                  str(asset.get("kind") or "image") in {"image", "video"}]
        final_image = bool(self.shot.get("selected_image_asset") or
                           self.shot.get("anchor_frame_id"))
        final_video = bool(self.shot.get("selected_video_asset"))
        if final_video:
            self._primary_btn.setText("视频已定稿 · 查看结果")
            self._primary_btn.setStyleSheet(_PRIMARY)
        elif final_image:
            self._primary_btn.setText("下一步：用定稿图片生成视频")
            self._primary_btn.setStyleSheet(_DIRECTOR_VIDEO)
        elif any(str(asset.get("kind") or "image") == "image" for asset in assets):
            self._primary_btn.setText("请先在右侧把一张图片设为定稿")
            self._primary_btn.setStyleSheet(_DIRECTOR_IMG)
        elif not self._assets_ready:
            self._primary_btn.setText("准备本镜头素材")
            self._primary_btn.setStyleSheet(_DIRECTOR_IMG)
        else:
            self._primary_btn.setText("生成这张画面")
            self._primary_btn.setStyleSheet(_DIRECTOR_IMG)
        self._primary_btn.setEnabled(True)

    def _update_binding_summary(self):
        def chosen_name(combo: QComboBox) -> str:
            if not combo.currentData():
                return ""
            return combo.currentText().split(" · ", 1)[0].strip()

        parts = []
        for label, combo in (("场景", self._scene_combo),
                             ("主体", self._character_combo),
                             ("元素", self._element_combo)):
            name = chosen_name(combo)
            if name:
                parts.append(f"{label}：{name}")
        if parts:
            self._binding_summary.setText("已准备参考素材  ·  " + "  ·  ".join(parts))
            self._binding_summary.setStyleSheet("color:#66c398;font-size:10px;")
        else:
            self._binding_summary.setText(
                "参考素材：沿用项目默认设置（需修改时点“更多设置”）")
            self._binding_summary.setStyleSheet("color:#858593;font-size:10px;")

    @staticmethod
    def _fill_binding_combo(combo: QComboBox, items: list, selected: str,
                            empty_text: str, kind_attr: str = ""):
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(empty_text, "")
        for item in items:
            name = getattr(item, "name", "未命名")
            suffix = getattr(item, kind_attr, "") if kind_attr else ""
            state = (f"主参考 v{max(1, int(getattr(item, 'version', 0) or 0))}"
                     if asset_is_approved(item, require_file=True) else "草稿")
            meta = " · ".join(value for value in (str(suffix or ""), state) if value)
            combo.addItem(f"{name} · {meta}" if meta else name,
                          getattr(item, "id", ""))
        index = combo.findData(selected)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def set_resources(self, characters: list, scenes: list, elements: list):
        self._characters = list(characters or [])
        self._scenes = list(scenes or [])
        self._elements = list(elements or [])
        self._fill_binding_combo(
            self._scene_combo, self._scenes,
            self.shot.get("scene_asset_id") or self.shot.get("scene_id", ""),
            "沿用项目场景")
        self._fill_binding_combo(
            self._character_combo, self._characters, self.shot.get("character_id", ""),
            "沿用项目主体", "entity_type")
        self._fill_binding_combo(
            self._element_combo, self._elements, self.shot.get("element_id", ""),
            "不绑定指定元素", "element_type")
        self._refresh_multi_counts()
        self._update_binding_summary()

    def _pick_more_characters(self):
        primary = self._character_combo.currentData() or ""
        items = [item for item in self._characters if getattr(item, "id", "") != primary]
        if not items:
            QMessageBox.information(
                self, "没有可选主体",
                "资产库中没有其他主体。请点击页面上方“智能准备全部镜头”自动创建。")
            return
        selected = [item_id for item_id in self.shot.get("character_ids", [])
                    if item_id != primary]
        dialog = _MultiAssetSelectDialog(
            "选择本镜头的更多主体", items, selected, "entity_type", self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.shot["character_ids"] = dialog.selected_ids()
            self._refresh_multi_counts()
            self._sync_bindings()

    def _pick_more_elements(self):
        primary = self._element_combo.currentData() or ""
        items = [item for item in self._elements if getattr(item, "id", "") != primary]
        if not items:
            QMessageBox.information(
                self, "没有可选元素",
                "资产库中没有其他元素。请点击页面上方“智能准备全部镜头”自动创建。")
            return
        selected = [item_id for item_id in self.shot.get("element_ids", [])
                    if item_id != primary]
        dialog = _MultiAssetSelectDialog(
            "选择本镜头的更多指定元素", items, selected, "element_type", self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.shot["element_ids"] = dialog.selected_ids()
            self._refresh_multi_counts()
            self._sync_bindings()

    def _refresh_multi_counts(self):
        primary_character = self._character_combo.currentData() or ""
        character_ids = [item_id for item_id in self.shot.get("character_ids", [])
                         if item_id and item_id != primary_character]
        self.shot["character_ids"] = list(dict.fromkeys(character_ids))
        self._more_characters_btn.setText(
            f"多主体 +{len(character_ids)}" if character_ids else "多主体")
        primary_element = self._element_combo.currentData() or ""
        element_ids = [item_id for item_id in self.shot.get("element_ids", [])
                       if item_id and item_id != primary_element]
        self.shot["element_ids"] = list(dict.fromkeys(element_ids))
        self._more_elements_btn.setText(
            f"多元素 +{len(element_ids)}" if element_ids else "多元素")

    def _sync_bindings(self, *_):
        previous_scene_id = (self.shot.get("scene_asset_id")
                             or self.shot.get("scene_id") or "")
        self.shot["scene_id"] = self._scene_combo.currentData() or ""
        self.shot["character_id"] = self._character_combo.currentData() or ""
        self.shot["element_id"] = self._element_combo.currentData() or ""
        self.shot["element_mode"] = self._element_mode.currentData() or "exact"
        self.shot["element_placement"] = self._element_placement.text().strip()
        self._refresh_multi_counts()
        self.shot["scene_asset_id"] = self.shot["scene_id"]
        scene_item = next((item for item in self._scenes
                           if getattr(item, "id", "") == self.shot["scene_asset_id"]), None)
        self.shot["scene_version"] = int(getattr(scene_item, "version", 0) or 0)
        if scene_item is not None:
            self.shot["scene_name"] = str(getattr(scene_item, "name", "") or "")
        if previous_scene_id != self.shot["scene_asset_id"]:
            self.shot["continuity_group"] = ""
            self.shot["previous_shot_id"] = ""
            self.shot["next_shot_id"] = ""
            self.shot["anchor_source_shot_id"] = ""
            self.shot["generation_mode"] = ""

        old_characters = {
            item.get("asset_id"): item
            for item in self.shot.get("character_bindings", [])
            if isinstance(item, dict) and item.get("asset_id")}
        character_ids = ([self.shot["character_id"]]
                         if self.shot["character_id"] else []) + list(
                             self.shot.get("character_ids", []))
        self.shot["character_bindings"] = []
        for asset_id in dict.fromkeys(item for item in character_ids if item):
            previous = old_characters.get(asset_id, {})
            resource = next((item for item in self._characters
                             if getattr(item, "id", "") == asset_id), None)
            self.shot["character_bindings"].append({
                "asset_id": asset_id,
                "name": str(getattr(resource, "name", "") or
                            previous.get("name") or ""),
                "version": int(getattr(resource, "version", 0) or
                               previous.get("version") or 0),
                "role": previous.get("role") or "subject",
                "outfit_state": previous.get("outfit_state") or "",
                "appearance_state": previous.get("appearance_state") or "",
                "required": bool(previous.get("required", True)),
            })
        selected_character_names = [
            str(value.get("name") or "")
            for value in self.shot["character_bindings"] if value.get("name")]
        if selected_character_names:
            self.shot["character_names"] = selected_character_names
            self.shot["character"] = selected_character_names[0]

        old_elements = {
            item.get("asset_id"): item
            for item in self.shot.get("element_bindings", [])
            if isinstance(item, dict) and item.get("asset_id")}
        element_ids = ([self.shot["element_id"]]
                       if self.shot["element_id"] else []) + list(
                           self.shot.get("element_ids", []))
        self.shot["element_bindings"] = []
        for asset_id in dict.fromkeys(item for item in element_ids if item):
            previous = old_elements.get(asset_id, {})
            is_primary = asset_id == self.shot["element_id"]
            resource = next((item for item in self._elements
                             if getattr(item, "id", "") == asset_id), None)
            self.shot["element_bindings"].append({
                "asset_id": asset_id,
                "name": str(getattr(resource, "name", "") or
                            previous.get("name") or ""),
                "version": int(getattr(resource, "version", 0) or
                               previous.get("version") or 0),
                "mode": (self.shot["element_mode"] if is_primary
                         else previous.get("mode") or "exact"),
                "placement": (self.shot["element_placement"] if is_primary
                              else previous.get("placement") or ""),
                "required": bool(previous.get("required", True)),
            })
        selected_element_names = [
            str(value.get("name") or "")
            for value in self.shot["element_bindings"] if value.get("name")]
        if selected_element_names:
            self.shot["element_names"] = selected_element_names
        sync_legacy_bindings(self.shot)
        self.shot["shot_contract"] = build_shot_contract(self.shot)
        self._update_binding_summary()
        self.binding_changed.emit(str(self.shot.get("id", "")))

    def refresh_status(self):
        start = float(self.shot.get("start", 0))
        duration = float(self.shot.get("duration", 0))
        self._title.setText(
            f"镜头 {int(self.shot.get('number', 0)):02d}   "
            f"{start:05.1f}s — {start + duration:05.1f}s   ({duration:g}s)"
        )
        assets = [asset for asset in self.shot.get("assets", [])
                  if isinstance(asset, dict) and
                  str(asset.get("kind") or "image") in {"image", "video"}]
        if assets:
            selected = (self.shot.get("preview_asset") or
                        self.shot.get("selected_asset") or assets[-1].get("path", ""))
            final_image = bool(self.shot.get("selected_image_asset") or
                               self.shot.get("anchor_frame_id"))
            final_video = bool(self.shot.get("selected_video_asset"))
            final_text = (" · 图片已定稿" if final_image else "") + (
                " · 视频已定稿" if final_video else "")
            if self.shot.get("dialogue_audio_status") == "ready":
                final_text += " · 对白音频已就绪"
            self._asset_status.setText(f"已有 {len(assets)} 个结果{final_text}")
            self._asset_status.setToolTip(str(selected))
        else:
            self._asset_status.setText("尚未生成")
        self._refresh_assets()

    def _refresh_assets(self):
        while self._assets_layout.count() > 1:
            item = self._assets_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        assets = [a for a in self.shot.get("assets", [])
                  if isinstance(a, dict) and
                  str(a.get("kind") or "image") in {"image", "video"}]
        selected_kind = self._selected_asset_kind()
        selected = self.shot.get("preview_asset") or self.shot.get("selected_asset")
        final_image = str(self.shot.get("selected_image_asset") or
                          self.shot.get("anchor_frame_id") or "")
        final_video = str(self.shot.get("selected_video_asset") or "")
        selected_index = -1
        for index, asset in enumerate(assets):
            path = str(asset.get("path") or "")
            kind = str(asset.get("kind") or "image")
            button = _DoubleClickToolButton()
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setIconSize(QSize(104, 66))
            button.setFixedSize(116, 92)
            if kind == "image" and path and os.path.exists(path):
                pix = QPixmap(path)
                if not pix.isNull():
                    button.setIcon(QIcon(pix.scaled(
                        104, 66, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)))
            else:
                actual = float(asset.get("actual_duration", 0) or 0)
                button.setText(
                    f"▶ 视频 {index + 1}" + (f" · {actual:g}s" if actual > 0 else ""))
            if not button.text():
                button.setText(f"图片 {index + 1}")
            is_selected = path == selected
            is_final_image = kind == "image" and path == final_image
            is_final_video = kind == "video" and path == final_video
            if is_selected:
                selected_index = index
                button.setText("● 预览 · " + button.text())
            if is_final_image:
                button.setText("✓ 定稿图 · " + button.text())
            elif is_final_video:
                button.setText("✓ 定稿视频 · " + button.text())
            border = ("2px solid #55c794" if is_final_image else
                      "2px solid #5aa9ee" if is_final_video else
                      "2px solid #8b6cf0" if is_selected else
                      "1px solid #34343a")
            button.setStyleSheet(
                "QToolButton{background:#202025;color:#bbb;border:%s;border-radius:6px;"
                "padding:3px;font-size:10px;}QToolButton:hover{background:#292930;}" %
                border
            )
            button.setToolTip(path)
            button.clicked.connect(lambda _=False, p=path: self._select_asset(p))
            button.doubleClicked.connect(
                lambda p=path, k=kind: self.preview_requested.emit(p, k))
            self._assets_layout.insertWidget(self._assets_layout.count() - 1, button)
        image_selected = selected_kind == "image"
        # 操作按钮始终允许点击；缺少图片时由处理函数显示具体原因。
        self._btn_i2v.setEnabled(True)
        self._btn_refine.setEnabled(True)
        self._btn_refine.setText(
            f"送图片 {selected_index + 1} 到 PS 精修"
            if image_selected and selected_index >= 0 else "送到 PS 精修")
        self._btn_i2i.setEnabled(True)
        self._refresh_primary_action()
        self._regenerate_btn.setVisible(bool(assets))
        self._change_model_btn.setVisible(bool(assets))

    def _select_asset(self, path: str):
        # 单击候选只改变预览，不再暗中覆盖定稿图片或定稿视频。
        self.shot["preview_asset"] = path
        self.shot["selected_asset"] = path
        for asset in self.shot.get("assets", []):
            if isinstance(asset, dict) and asset.get("path") == path:
                self.shot["asset_type"] = asset.get("kind", "image")
                break
        self._refresh_assets()
        self.asset_selected.emit(self.shot["id"], path)

    def set_active(self, active: bool):
        border = "#8b6cf0" if active else "#29292d"
        width = "2px" if active else "1px"
        self.setStyleSheet(
            "#StoryboardShotCard{background:#151517;border:%s solid %s;"
            "border-radius:8px;} QLabel{background:transparent;}" % (width, border)
        )

    def set_task_status(self, text: str, progress: int | None = None,
                        running: bool = True):
        self._asset_status.setText(text)
        self._task_progress.setVisible(running)
        if progress is not None:
            self._task_progress.setValue(max(0, min(100, progress)))


class StoryboardProductionPanel(QFrame):
    """分镜页常驻结果面板：集中显示当前镜头、版本、任务与输出位置。"""
    asset_selected = pyqtSignal(str, str)
    asset_approved = pyqtSignal(str, str)
    preview_requested = pyqtSignal(str, str)
    image_to_video_requested = pyqtSignal(str)
    reference_anchor_requested = pyqtSignal(str, str)
    exact_element_requested = pyqtSignal(str)
    remove_result_requested = pyqtSignal(str, str)
    keep_result_only_requested = pyqtSignal(str, str)
    refine_requested = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._board = {}
        self._shot = None
        self._asset_path = ""
        self._asset_kind = ""
        self._source_pixmap = None
        self.setObjectName("StoryboardProductionPanel")
        self.setMinimumWidth(330)
        self.setMaximumWidth(470)
        self.setStyleSheet(
            "#StoryboardProductionPanel{background:#121216;border:1px solid #303037;"
            "border-radius:9px;} QLabel{background:transparent;}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 13, 14, 13)
        root.setSpacing(9)

        head = QHBoxLayout()
        title = QLabel("当前镜头结果")
        title.setStyleSheet("color:#f1f1f4;font-size:15px;font-weight:bold;")
        head.addWidget(title)
        head.addStretch()
        self.summary_badge = QLabel("0 / 0 镜头就绪")
        self.summary_badge.setStyleSheet(
            "color:#a995ee;background:#211b31;border-radius:9px;padding:3px 7px;font-size:10px;"
        )
        head.addWidget(self.summary_badge)
        root.addLayout(head)

        self.shot_label = QLabel("先在左侧选择一个镜头")
        self.shot_label.setStyleSheet("color:#d8d8dd;font-size:12px;font-weight:bold;")
        root.addWidget(self.shot_label)
        self.element_label = QLabel("这个镜头没有指定必须出现的元素")
        self.element_label.setWordWrap(True)
        self.element_label.setStyleSheet("color:#70707c;font-size:10px;")
        root.addWidget(self.element_label)

        self.preview = _DoubleClickPreviewLabel("生成后的图片或视频会显示在这里")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(230)
        self.preview.setStyleSheet(
            "background:#09090b;color:#686874;border:1px dashed #34343d;border-radius:8px;"
        )
        self.preview.setToolTip("双击查看图片或播放视频")
        self.preview.doubleClicked.connect(self._request_preview)
        root.addWidget(self.preview, 1)

        self.asset_meta = QLabel("尚无生成结果")
        self.asset_meta.setWordWrap(True)
        self.asset_meta.setStyleSheet("color:#a9a9b2;font-size:11px;")
        root.addWidget(self.asset_meta)

        task_box = QFrame()
        task_box.setStyleSheet("QFrame{background:#19191e;border-radius:7px;}")
        task_layout = QVBoxLayout(task_box)
        task_layout.setContentsMargins(10, 8, 10, 8)
        task_layout.setSpacing(5)
        self.task_label = QLabel("当前镜头没有运行中的生成任务")
        self.task_label.setStyleSheet("color:#858590;font-size:11px;")
        task_layout.addWidget(self.task_label)
        self.task_progress = QProgressBar()
        self.task_progress.setRange(0, 100)
        self.task_progress.setFixedHeight(4)
        self.task_progress.setTextVisible(False)
        self.task_progress.setVisible(False)
        self.task_progress.setStyleSheet(
            "QProgressBar{background:#282830;border:none;}"
            "QProgressBar::chunk{background:#8b6cf0;}"
        )
        task_layout.addWidget(self.task_progress)
        root.addWidget(task_box)

        version_head = QHBoxLayout()
        version_title = QLabel("候选结果（单击只预览，定稿请点候选下方按钮）")
        version_title.setStyleSheet("color:#d5d5da;font-size:12px;font-weight:bold;")
        version_head.addWidget(version_title)
        version_head.addStretch()
        self.keep_result_btn = QPushButton("只保留选中")
        self.keep_result_btn.setToolTip("清理这个镜头的其他候选；本地文件仍保留")
        self.keep_result_btn.setStyleSheet(_GHOST)
        self.keep_result_btn.clicked.connect(self._request_keep_result_only)
        version_head.addWidget(self.keep_result_btn)
        self.version_count = QLabel("0 个")
        self.version_count.setStyleSheet("color:#777783;font-size:10px;")
        version_head.addWidget(self.version_count)
        root.addLayout(version_head)

        self.version_scroll = QScrollArea()
        self.version_scroll.setWidgetResizable(True)
        self.version_scroll.setFixedHeight(194)
        self.version_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.version_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.version_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.version_scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self.version_widget = QWidget()
        self.version_layout = QHBoxLayout(self.version_widget)
        self.version_layout.setContentsMargins(0, 0, 0, 0)
        self.version_layout.setSpacing(7)
        self.version_layout.addStretch()
        self.version_scroll.setWidget(self.version_widget)
        root.addWidget(self.version_scroll)

        actions = QHBoxLayout()
        self.refine_btn = QPushButton("送当前图片到 PS 精修")
        self.refine_btn.setStyleSheet(_DIRECTOR_IMG)
        self.refine_btn.setToolTip("只会发送上方标有“已选”的那张图片")
        self.refine_btn.clicked.connect(self._request_refine)
        actions.addWidget(self.refine_btn)
        root.addLayout(actions)

        self.use_image_btn = QPushButton("下一步：用这张图生成视频")
        self.use_image_btn.setMinimumHeight(36)
        self.use_image_btn.setStyleSheet(_DIRECTOR_VIDEO)
        self.use_image_btn.clicked.connect(self._request_image_to_video)
        root.addWidget(self.use_image_btn)

        self.details_toggle = QPushButton("更多操作 ▾")
        self.details_toggle.setStyleSheet(_GHOST)
        self.details_toggle.clicked.connect(self._toggle_details)
        root.addWidget(self.details_toggle)

        self.details_box = QFrame()
        self.details_box.setObjectName("StoryboardResultDetails")
        self.details_box.setStyleSheet(
            "#StoryboardResultDetails{background:#0f0f13;border:1px solid #292930;"
            "border-radius:7px;}#StoryboardResultDetails QLabel{background:transparent;}"
        )
        details = QVBoxLayout(self.details_box)
        details.setContentsMargins(9, 8, 9, 8)
        details.setSpacing(7)

        output_title = QLabel("文件位置")
        output_title.setStyleSheet("color:#d5d5da;font-size:11px;font-weight:bold;")
        details.addWidget(output_title)
        self.output_path = QLabel("生成完成后会在这里显示文件路径")
        self.output_path.setWordWrap(True)
        self.output_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.output_path.setStyleSheet(
            "color:#83838e;background:#0c0c0f;border-radius:6px;padding:7px;font-size:10px;"
        )
        details.addWidget(self.output_path)

        self.folder_btn = QPushButton("打开所在文件夹")
        self.folder_btn.setStyleSheet(_GHOST)
        self.folder_btn.clicked.connect(self._open_folder)
        details.addWidget(self.folder_btn)

        anchor_actions = QHBoxLayout()
        self.character_anchor_btn = QPushButton("保存为主体参考")
        self.character_anchor_btn.setStyleSheet(_GHOST)
        self.character_anchor_btn.clicked.connect(
            lambda: self._request_reference_anchor("character"))
        anchor_actions.addWidget(self.character_anchor_btn)
        self.scene_anchor_btn = QPushButton("保存为场景参考")
        self.scene_anchor_btn.setStyleSheet(_GHOST)
        self.scene_anchor_btn.clicked.connect(
            lambda: self._request_reference_anchor("scene"))
        anchor_actions.addWidget(self.scene_anchor_btn)
        details.addLayout(anchor_actions)
        self.exact_element_btn = QPushButton("精确植入绑定元素")
        self.exact_element_btn.setMinimumHeight(34)
        self.exact_element_btn.setStyleSheet(
            "QPushButton{background:#16404a;color:#8be3f3;border:1px solid #287080;"
            "border-radius:5px;padding:6px;}QPushButton:hover{background:#205763;}"
            "QPushButton:disabled{background:#242429;color:#666;border-color:#333;}"
        )
        self.exact_element_btn.clicked.connect(self._request_exact_element)
        details.addWidget(self.exact_element_btn)
        root.addWidget(self.details_box)
        self.details_box.setVisible(False)
        self._set_action_enabled(False)

    def set_storyboard(self, board: dict):
        self._board = board or {}
        shots = self._board.get("shots", [])
        ready = sum(1 for shot in shots if (
            shot.get("selected_video_asset") or shot.get("selected_image_asset") or
            shot.get("anchor_frame_id")))
        image_count = sum(
            1 for shot in shots for asset in shot.get("assets", [])
            if isinstance(asset, dict) and asset.get("kind") == "image")
        video_count = sum(
            1 for shot in shots for asset in shot.get("assets", [])
            if isinstance(asset, dict) and asset.get("kind") == "video")
        self.summary_badge.setText(
            f"{ready} / {len(shots)} 镜头已定稿 · 候选图 {image_count} / 视频 {video_count}")

    def _toggle_details(self):
        show = self.details_box.isHidden()
        self.details_box.setVisible(show)
        self.details_toggle.setText("收起操作 ▴" if show else "更多操作 ▾")

    def set_shot(self, shot: dict | None, preferred_path: str = ""):
        self._shot = shot
        if not shot:
            self.shot_label.setText("先在左侧选择一个镜头")
            self.element_label.setText("这个镜头没有指定必须出现的元素")
            self._show_asset(None)
            self._refresh_versions([])
            return
        start = float(shot.get("start", 0) or 0)
        duration = float(shot.get("duration", 0) or 0)
        self.shot_label.setText(
            f"镜头 {int(shot.get('number', 0)):02d}  ·  {start:g}s—{start + duration:g}s  ·  目标 {duration:g}s")
        binding_summary = shot.get("_binding_summary", "")
        if binding_summary:
            self.element_label.setText(binding_summary)
            self.element_label.setStyleSheet("color:#63c5dc;font-size:10px;")
        else:
            self.element_label.setText("这个镜头沿用项目默认参考素材")
            self.element_label.setStyleSheet("color:#70707c;font-size:10px;")
        assets = [a for a in shot.get("assets", [])
                  if isinstance(a, dict) and
                  str(a.get("kind") or "image") in {"image", "video"}]
        selected = (preferred_path or shot.get("preview_asset") or
                    shot.get("selected_asset") or
                    (assets[-1].get("path") if assets else ""))
        if selected:
            shot["preview_asset"] = selected
        if selected and not shot.get("selected_asset"):
            shot["selected_asset"] = selected
        asset = next((a for a in assets if a.get("path") == selected), None)
        self._refresh_versions(assets)
        self._show_asset(asset)

    def set_task(self, text: str = "", progress: int = 0, running: bool = False):
        self.task_label.setText(text or "当前镜头没有运行中的生成任务")
        self.task_progress.setVisible(running)
        self.task_progress.setValue(max(0, min(100, int(progress))))

    def _refresh_versions(self, assets: list):
        while self.version_layout.count() > 1:
            item = self.version_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.version_count.setText(f"{len(assets)} 个")
        selected = ((self._shot.get("preview_asset") or
                    self._shot.get("selected_asset", "")) if self._shot else "")
        final_image = str((self._shot or {}).get("selected_image_asset") or
                          (self._shot or {}).get("anchor_frame_id") or "")
        final_video = str((self._shot or {}).get("selected_video_asset") or "")
        selected_meta = next(
            (asset for asset in assets if str(asset.get("path") or "") == selected), None)
        selected_kind = str((selected_meta or {}).get("kind") or "")
        same_kind_count = sum(
            1 for asset in assets if str(asset.get("kind") or "image") == selected_kind)
        self.keep_result_btn.setEnabled(True)
        for index, asset in enumerate(assets):
            path = str(asset.get("path") or "")
            kind = str(asset.get("kind") or "image")
            quality = asset.get("quality_report") or {}
            quality_status = str(quality.get("status") or "")
            quality_summary = str(quality.get("summary") or "")
            is_selected = path == selected
            tile = QFrame()
            tile.setFixedSize(132, 170)
            is_final_image = kind == "image" and path == final_image
            is_final_video = kind == "video" and path == final_video
            border = ("2px solid #55c794" if is_final_image else
                      "2px solid #5aa9ee" if is_final_video else
                      "2px solid #b9515a" if quality_status == "reject" else
                      "2px solid #8b6cf0" if is_selected else
                      "1px solid #303038")
            tile.setStyleSheet(
                "QFrame{background:#18181d;border:%s;border-radius:7px;}" %
                border
            )
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(4, 4, 4, 4)
            tile_layout.setSpacing(3)
            button = _DoubleClickToolButton()
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setIconSize(QSize(108, 58))
            button.setFixedSize(116, 86)
            pix = self._asset_pixmap(path, kind)
            if pix and not pix.isNull():
                button.setIcon(QIcon(pix.scaled(
                    108, 58, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)))
            label = ("图片" if kind == "image" else "视频") + f" {index + 1}"
            quality_prefix = {
                "pass": "✓质检 · ",
                "pending": "◐待确认 · ",
                "warn": "⚠检查 · ",
                "reject": "✕异常 · ",
            }.get(quality_status, "")
            prefix = ("✓ 定稿图片 · " if is_final_image else
                      "✓ 定稿视频 · " if is_final_video else
                      "● 当前预览 · " if is_selected else "")
            button.setText(prefix + quality_prefix + label)
            quality_details = list(quality.get("problems") or []) + list(
                quality.get("warnings") or [])
            manual = list(quality.get("manual_checks") or [])
            if manual:
                quality_details.append("定稿时确认：" + "、".join(manual))
            button.setToolTip(
                path + (("\n\n" + quality_summary +
                         ("\n" + "\n".join(quality_details)
                          if quality_details else ""))
                        if quality_summary else ""))
            button.setStyleSheet(
                "QToolButton{background:#202025;color:%s;border:none;border-radius:5px;"
                "padding:2px;font-size:10px;}QToolButton:hover{background:#292930;}" %
                ("#c7b8ff" if is_selected else "#bbb")
            )
            button.clicked.connect(lambda _=False, p=path: self._choose_asset(p))
            button.doubleClicked.connect(
                lambda p=path, k=kind: self.preview_requested.emit(p, k))
            tile_layout.addWidget(button)

            approve = QPushButton(
                "已是定稿图片" if is_final_image else
                "已是定稿视频" if is_final_video else
                "文件异常，不能定稿" if quality_status == "reject" else
                "设为定稿图片" if kind == "image" else "设为定稿视频")
            approve.setEnabled(
                not (is_final_image or is_final_video or
                     quality_status == "reject"))
            approve.setStyleSheet(
                "QPushButton{background:%s;color:%s;border:1px solid %s;"
                "border-radius:4px;padding:4px;font-size:10px;font-weight:bold;}"
                "QPushButton:hover{background:#294b3e;}QPushButton:disabled{color:#b8c8c0;}" % (
                    "#1d3a30" if kind == "image" else "#193249",
                    "#83ddb5" if kind == "image" else "#82bff3",
                    "#32614d" if kind == "image" else "#315b80"))
            approve.clicked.connect(
                lambda _=False, p=path: self._request_approve_asset(p))
            tile_layout.addWidget(approve)

            tile_actions = QHBoxLayout()
            tile_actions.setContentsMargins(0, 0, 0, 0)
            tile_actions.setSpacing(4)
            if kind == "image":
                refine = QPushButton("PS 精修")
                refine.setToolTip(f"把{label}送到 PS 精修")
                refine.setStyleSheet(
                    "QPushButton{background:#17352b;color:#75d8ad;border:none;"
                    "border-radius:4px;padding:3px 5px;font-size:10px;}"
                    "QPushButton:hover{background:#214b3d;}"
                )
                refine.clicked.connect(
                    lambda _=False, p=path: self._request_refine(p))
                tile_actions.addWidget(refine, 1)
            remove = QPushButton("删除")
            remove.setToolTip(f"从当前镜头移除{label}；不会删除本地文件")
            remove.setStyleSheet(
                "QPushButton{background:#302022;color:#e6979d;border:none;"
                "border-radius:4px;padding:3px 5px;font-size:10px;}"
                "QPushButton:hover{background:#4a292d;}"
            )
            remove.clicked.connect(
                lambda _=False, p=path: self._request_remove_result(p))
            tile_actions.addWidget(remove, 1)
            tile_layout.addLayout(tile_actions)
            self.version_layout.insertWidget(self.version_layout.count() - 1, tile)

    def _choose_asset(self, path: str):
        if not self._shot:
            return
        # 候选单击只切换预览；定稿由独立按钮显式完成。
        self._shot["preview_asset"] = path
        self._shot["selected_asset"] = path
        asset = next((a for a in self._shot.get("assets", [])
                      if isinstance(a, dict) and a.get("path") == path), None)
        if asset:
            self._shot["asset_type"] = asset.get("kind", "image")
        self._refresh_versions([
            a for a in self._shot.get("assets", []) if isinstance(a, dict)])
        self._show_asset(asset)
        self.asset_selected.emit(str(self._shot.get("id", "")), path)

    def _request_approve_asset(self, path: str):
        if self._shot and path:
            self.asset_approved.emit(str(self._shot.get("id", "")), path)

    def _request_remove_result(self, path: str = ""):
        target_path = path or self._asset_path
        if self._shot and target_path:
            self.remove_result_requested.emit(
                str(self._shot.get("id", "")), target_path)

    def _request_keep_result_only(self):
        if not self._shot or not self._asset_path:
            self._warn_action("请先选择一个镜头，并在候选结果中预览一张图片或视频。")
            return
        assets = [item for item in self._shot.get("assets", [])
                  if isinstance(item, dict)]
        same_kind = [item for item in assets
                     if str(item.get("kind") or "image") == self._asset_kind]
        if len(same_kind) <= 1:
            self._warn_action(
                f"当前只有一个{'图片' if self._asset_kind == 'image' else '视频'}结果，没有同类候选需要清理。")
            return
        self.keep_result_only_requested.emit(
            str(self._shot.get("id", "")), self._asset_path)

    def _show_asset(self, asset: dict | None):
        self._asset_path = str(asset.get("path") or "") if asset else ""
        self._asset_kind = str(asset.get("kind") or "image") if asset else ""
        if not asset:
            self._source_pixmap = None
            if self._shot is not None:
                self._shot["_remaining_exact_element_ids"] = list(
                    self._shot.get("_exact_element_ids", []) or [])
            self.preview.clear()
            self.preview.setText("这个镜头还没有结果\n\n在左侧点击“生成这张画面”")
            self.asset_meta.setText("尚无生成结果")
            self.refine_btn.setText("送当前图片到 PS 精修")
            self.output_path.setText("生成完成后会在这里显示文件路径")
            self._set_action_enabled(False)
            return
        path = self._asset_path
        kind = self._asset_kind
        if self._shot is not None:
            applied_key = ("exact_elements_tracked" if kind == "video"
                           else "exact_elements_applied")
            applied = set(asset.get(applied_key, []) or [])
            self._shot["_remaining_exact_element_ids"] = [
                item_id for item_id in (self._shot.get("_exact_element_ids", []) or [])
                if item_id not in applied]
        pix = self._asset_pixmap(path, kind)
        if pix and not pix.isNull():
            self._source_pixmap = pix
            self._update_preview_pixmap()
        else:
            self._source_pixmap = None
            self.preview.clear()
            self.preview.setText("视频结果已生成\n点击下方“打开大预览”播放" if kind == "video"
                                 else "结果文件无法读取")
        actual = float(asset.get("actual_duration", 0) or 0)
        duration_note = f" · 实际 {actual:g}s" if actual > 0 else ""
        final_image = str((self._shot or {}).get("selected_image_asset") or
                          (self._shot or {}).get("anchor_frame_id") or "")
        final_video = str((self._shot or {}).get("selected_video_asset") or "")
        final_note = (" · 已定稿图片" if kind == "image" and path == final_image else
                      " · 已定稿视频" if kind == "video" and path == final_video else
                      " · 仅预览，尚未定稿")
        remaining_exact = len((self._shot or {}).get(
            "_remaining_exact_element_ids", []) or [])
        exact_note = (
            f" · ⚠ 待精确植入 {remaining_exact} 个元素"
            if remaining_exact else "")
        quality = asset.get("quality_report") or {}
        quality_note = (
            f" · {quality.get('summary')}" if quality.get("summary") else "")
        self.asset_meta.setText(
            f"当前预览：{'图片' if kind == 'image' else '视频'}{duration_note}"
            f"{final_note}{exact_note}{quality_note} · {Path(path).name}")
        assets = [item for item in (self._shot or {}).get("assets", [])
                  if isinstance(item, dict)]
        selected_index = next(
            (index for index, item in enumerate(assets)
             if str(item.get("path") or "") == path), 0)
        self.refine_btn.setText(
            f"送图片 {selected_index + 1} 到 PS 精修"
            if kind == "image" else "视频不能送到 PS 精修")
        self.output_path.setText(path or "结果路径为空")
        self.output_path.setToolTip(path)
        self._set_action_enabled(bool(path and os.path.exists(path)))
        self.use_image_btn.setText("下一步：用定稿图片生成视频")
        self.use_image_btn.setEnabled(True)

    @staticmethod
    def _asset_pixmap(path: str, kind: str) -> QPixmap | None:
        if not path or not os.path.exists(path):
            return None
        if kind == "image":
            pix = QPixmap(path)
            return pix if not pix.isNull() else None
        try:
            import cv2
            capture = cv2.VideoCapture(path)
            ok, frame = capture.read()
            capture.release()
            if not ok or frame is None:
                return None
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            height, width, channels = frame.shape
            image = QImage(frame.data, width, height, channels * width,
                           QImage.Format.Format_RGB888).copy()
            return QPixmap.fromImage(image)
        except Exception:
            return None

    def _request_preview(self):
        if self._asset_path:
            self.preview_requested.emit(self._asset_path, self._asset_kind)
        else:
            self._warn_action("当前镜头还没有可以预览的生成结果。")

    def _request_refine(self, path: str = ""):
        target_path = path or self._asset_path
        if not self._shot or not target_path:
            self._warn_action("请先选择一个镜头，并预览一张需要精修的图片。")
            return
        asset = next((item for item in self._shot.get("assets", [])
                      if isinstance(item, dict) and
                      item.get("path") == target_path), None)
        if not asset or asset.get("kind") != "image":
            self._warn_action("PS 精修只支持图片；请先在候选区预览一张图片。")
            return
        if target_path != self._asset_path:
            self._choose_asset(target_path)
        self.refine_requested.emit(str(self._shot.get("id", "")), target_path)

    def _open_folder(self):
        if self._asset_path and os.path.exists(self._asset_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self._asset_path).parent)))
        else:
            self._warn_action("当前没有可定位的生成文件，可能尚未生成或文件已被移动。")

    def _request_image_to_video(self):
        if self._shot and (self._shot.get("selected_image_asset") or
                           self._shot.get("anchor_frame_id")):
            self.image_to_video_requested.emit(str(self._shot.get("id", "")))
        else:
            self._warn_action("请先在候选结果下方点击“设为定稿图片”，再生成视频。")

    def _request_reference_anchor(self, kind: str):
        if self._shot and self._asset_kind == "image" and self._asset_path:
            self.reference_anchor_requested.emit(
                str(self._shot.get("id", "")), kind)
        else:
            self._warn_action("请先在候选区预览一张图片，再把它保存为主体或场景参考。")

    def _request_exact_element(self):
        if not self._shot:
            self._warn_action("请先选择一个镜头。")
            return
        exact_count = len(self._shot.get(
            "_remaining_exact_element_ids",
            self._shot.get("_exact_element_ids", [])) or [])
        if exact_count <= 0:
            self._warn_action(
                "当前镜头没有绑定“精确植入”元素。请在更多设置或制片画布中先绑定元素。")
            return
        if not self._asset_path:
            self._warn_action("请先生成并预览一张图片或一个视频结果。")
            return
        self.exact_element_requested.emit(str(self._shot.get("id", "")))

    def _warn_action(self, message: str):
        """结果面板的操作门槛必须显式可见，不能表现成按钮失效。"""
        self.task_label.setText(message)
        self.task_label.setStyleSheet("color:#e2ad69;font-size:11px;")
        QMessageBox.information(self, "暂时无法执行", message)

    def _set_action_enabled(self, enabled: bool):
        # 不使用 disabled 隐藏原因；按钮保持可点击，由各处理函数给出提示。
        self.refine_btn.setEnabled(True)
        self.folder_btn.setEnabled(True)
        self.use_image_btn.setEnabled(True)
        self.character_anchor_btn.setEnabled(True)
        self.scene_anchor_btn.setEnabled(True)
        exact_count = len((self._shot or {}).get(
            "_remaining_exact_element_ids",
            (self._shot or {}).get("_exact_element_ids", [])) or [])
        has_exact_element = exact_count > 0
        self.exact_element_btn.setEnabled(True)
        self.exact_element_btn.setText(
            (("跟踪并植入" if self._asset_kind == "video" else "精确植入") +
             (f"绑定元素（{exact_count}个）" if exact_count > 1 else "绑定元素")))
        self.exact_element_btn.setStyleSheet(
            "QPushButton{background:#6a4520;color:#ffd9a0;border:1px solid #a66b2d;"
            "border-radius:5px;padding:7px;}QPushButton:hover{background:#815528;}"
            if has_exact_element else _GHOST)

    def _update_preview_pixmap(self):
        if self._source_pixmap is not None and not self._source_pixmap.isNull():
            self.preview.setPixmap(self._source_pixmap.scaled(
                max(1, self.preview.width() - 12), max(1, self.preview.height() - 12),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_preview_pixmap()


class _AssetExtractWorker(QThread):
    """从创意方案中提取角色和场景，直接写入 AI 资源中心数据库。"""
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, creative_brief: str):
        super().__init__()
        self.brief = creative_brief

    def run(self):
        try:
            from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME
            if not LLM_API_KEY:
                self.error.emit("未配置 LLM API Key")
                return
            from openai import OpenAI
            client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=45.0)
            system = """你是影视前期策划，负责从创意方案中提取角色和场景列表。
角色格式: {"name":"角色名","description":"外貌、服装、气质、年龄、性别","entity_type":"human/animal/monster/robot","life_stage":"幼年/青年/成年/中年/老年","gender":"male/female","seedream_prompt":"用于生成角色定妆照的英文描述，含面部特征、发型、服装、光线、风格"}
场景格式: {"name":"场景名","description":"环境描述、氛围、时间","seedream_prompt":"用于生成场景概念图的英文描述，含构图、光线、色调、风格"}
只返回合法JSON，不要Markdown。结构:
{"characters":[...], "scenes":[...]}"""
            response = client.chat.completions.create(
                model=LLM_MODEL_NAME,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": self.brief},
                ],
            )
            raw = response.choices[0].message.content.strip()
            import json as _json
            raw = raw.replace("```json", "").replace("```", "").strip()
            data = _json.loads(raw)
            characters = data.get("characters", [])
            scenes = data.get("scenes", [])

            import uuid as _uuid
            import time as _time
            from ai.assets.db import Character, Scene
            from ai.service import get_asset_db

            db = get_asset_db()
            char_count = 0
            scene_count = 0

            for ch in characters:
                char = Character(
                    id=str(_uuid.uuid4()),
                    name=ch.get("name", "未命名"),
                    entity_type=ch.get("entity_type", "human"),
                    life_stage=ch.get("life_stage", ""),
                    gender=ch.get("gender", ""),
                    description=ch.get("description", ""),
                    design_notes=ch.get("description", ""),
                    seedream_prompt=ch.get("seedream_prompt", ""),
                    created_at=_time.time(),
                    updated_at=_time.time(),
                )
                db.save_character(char)
                char_count += 1

            for sc in scenes:
                scene = Scene(
                    id=str(_uuid.uuid4()),
                    name=sc.get("name", "未命名"),
                    description=sc.get("description", ""),
                    seedream_prompt=sc.get("seedream_prompt", ""),
                )
                db.save_scene(scene)
                scene_count += 1

            summary = f"已提取并入库 {char_count} 个角色、{scene_count} 个场景"
            if char_count + scene_count > 0:
                summary += "。可在「AI制片画布」查看并生成定妆照"
            self.finished.emit(summary)
        except Exception as e:
            self.error.emit(str(e))


class ScriptWorkbench(QWidget):
    polished_text = pyqtSignal(str)
    status_msg = pyqtSignal(str, str)
    ps_refine_requested = pyqtSignal(str, str)
    resource_center_requested = pyqtSignal(str)
    import_storyboard_requested = pyqtSignal(object)
    storyboard_changed = pyqtSignal()

    STYLES = [
        "激情抓眼球", "沉稳放松", "幽默有趣", "紧迫急迫",
        "高端大气", "网感爆棚", "情感共鸣", "专业权威",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._tags = _load_tags()
        self._last_original = ""  # 翻译前的原文，用于恢复
        self._idea_worker = None
        self._idea_messages: list[dict] = []
        self._ai_manager = None
        self._asset_tasks: dict[str, dict] = {}
        self._shot_asset_studios: dict[str, object] = {}
        self._asset_timer = QTimer(self)
        self._asset_timer.setInterval(450)
        self._asset_timer.timeout.connect(self._poll_asset_tasks)
        try:
            self._ai_manager = get_ai_manager()
        except Exception:
            self._ai_manager = None
        self._storyboard: dict | None = None
        self._shot_cards: dict[str, StoryboardShotCard] = {}
        self._active_shot_id = ""
        self._resource_db = None
        self._character_resources = []
        self._scene_resources = []
        self._element_resources = []
        self._asset_extract_worker = None
        self._storyboard_batch_queue: list[str] = []
        self._storyboard_batch_active = False
        self._storyboard_batch_waiting_shot_id = ""
        self._pending_video_requests: dict[str, dict] = {}
        self._build()

    def _build(self):
        self.setStyleSheet("background:#1a1a1a;")
        root = QVBoxLayout(self); root.setContentsMargins(16,12,16,12); root.setSpacing(8)
        title = self._lbl("AI 脚本", "#e8e8ec")
        root.addWidget(title)
        self.mode_tabs = QTabWidget()
        self.mode_tabs.setDocumentMode(True)
        self.mode_tabs.setStyleSheet(_MODE_TABS)
        root.addWidget(self.mode_tabs, 1)

        copy_page = QWidget()
        self.script_page = copy_page
        copy_root = QVBoxLayout(copy_page)
        copy_root.setContentsMargins(12, 12, 12, 12)
        copy_root.setSpacing(10)
        self.mode_tabs.addTab(copy_page, "AI 脚本")

        copy_root.addWidget(self._lbl("✨ AI 广告脚本生成器", "#ccc"))

        # 产品名 + 标签记忆
        nr = QHBoxLayout(); nr.addWidget(QLabel("产品名称")); nr.addStretch()
        copy_root.addLayout(nr)
        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText("例：AI配音助手、智能扫地机器人…"); self.edit_name.setFixedHeight(32)
        self.edit_name.setStyleSheet(_INPUT); self.edit_name.returnPressed.connect(self._add_tag)
        copy_root.addWidget(self.edit_name)

        # 标签行
        self.tags_layout = QHBoxLayout()
        self.tags_layout.setSpacing(4)
        self.tags_layout.addStretch()
        copy_root.addLayout(self.tags_layout)
        self._refresh_tags()
        btn_add_tag = QPushButton("+ 保存标签")
        btn_add_tag.setFixedHeight(24); btn_add_tag.setStyleSheet(_TAG_BTN)
        btn_add_tag.clicked.connect(self._add_tag)
        self.tags_layout.insertWidget(self.tags_layout.count()-1, btn_add_tag)

        copy_root.addWidget(QLabel("产品描述"))
        self.editor_desc = QTextEdit()
        self.editor_desc.setPlaceholderText("描述产品功能、卖点、目标用户…\n面向短视频创作者的一键AI配音工具，支持50+语言…")
        self.editor_desc.setStyleSheet(_EDITOR); self.editor_desc.setMaximumHeight(120)
        copy_root.addWidget(self.editor_desc)

        # 参数行
        pr = QHBoxLayout(); pr.setSpacing(12)
        pr.addWidget(QLabel("风格"))
        self.combo_style = QComboBox(); self.combo_style.addItems(self.STYLES)
        self.combo_style.setStyleSheet(_COMBO); self.combo_style.setFixedWidth(120)
        pr.addWidget(self.combo_style)
        pr.addWidget(QLabel("时长"))
        self.combo_dur = QComboBox(); self.combo_dur.addItems(["15s","20s","30s","45s","60s","自定义"])
        self.combo_dur.setStyleSheet(_COMBO); self.combo_dur.setFixedWidth(80)
        self.combo_dur.currentTextChanged.connect(lambda t: self.edit_dur.setVisible(t=="自定义"))
        pr.addWidget(self.combo_dur)
        self.edit_dur = QLineEdit(); self.edit_dur.setFixedWidth(50); self.edit_dur.setFixedHeight(28)
        self.edit_dur.setPlaceholderText("秒"); self.edit_dur.setStyleSheet(_INPUT); self.edit_dur.hide()
        pr.addWidget(self.edit_dur)
        pr.addStretch()
        self.btn_gen = QPushButton("✨ 生成脚本"); self.btn_gen.setStyleSheet(_PRIMARY); self.btn_gen.setFixedHeight(34)
        self.btn_gen.clicked.connect(self._generate); pr.addWidget(self.btn_gen)
        copy_root.addLayout(pr)

        self.progress = QProgressBar(); self.progress.setRange(0,100); self.progress.setFixedHeight(2)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet("QProgressBar{background:#1a1a1a;border:none;border-radius:1px;}QProgressBar::chunk{background:#2a4a70;border-radius:1px;}")
        copy_root.addWidget(self.progress)

        # 结果
        rh = QHBoxLayout()
        rh.addWidget(QLabel("📝 生成结果"))
        # 翻译
        rh.addWidget(QLabel("  翻译:"))
        self._lang_btns = {}
        for label, code in LANGUAGES:
            b = QPushButton(label)
            b.setFixedHeight(22)
            b.setStyleSheet(_LANG_OFF)
            b.clicked.connect(lambda _,c=code: self._translate_result(c))
            self._lang_btns[code] = b
            rh.addWidget(b)
        # 自定义语种
        self.btn_custom_lang = QPushButton("自定义")
        self.btn_custom_lang.setFixedHeight(22)
        self.btn_custom_lang.setStyleSheet(_LANG_OFF)
        self.btn_custom_lang.clicked.connect(self._pick_custom_trans)
        rh.addWidget(self.btn_custom_lang)
        rh.addStretch()
        btn_copy = QPushButton("📋 复制"); btn_copy.setStyleSheet(_GHOST)
        btn_copy.clicked.connect(self._copy); rh.addWidget(btn_copy)
        self.btn_restore = QPushButton("↩ 恢复原文")
        self.btn_restore.setStyleSheet(_GHOST)
        self.btn_restore.setFixedHeight(22)
        self.btn_restore.setFixedWidth(90)
        self.btn_restore.clicked.connect(self._restore_original)
        self.btn_restore.hide()
        rh.addWidget(self.btn_restore)
        copy_root.addLayout(rh)

        self.editor_result = QTextEdit()
        self.editor_result.setPlaceholderText("AI 生成的广告脚本…"); self.editor_result.setStyleSheet(_EDITOR)
        copy_root.addWidget(self.editor_result, 1)

        self._build_idea_page()
        self._build_director_page()
        self._build_storyboard_page()
        # The production canvas is now the only public story/director/
        # storyboard workspace.  Keep the old widgets detached as a temporary
        # backend compatibility layer for existing generation handlers, but do
        # not expose duplicate AI Director or AI Storyboard pages in the UI.
        for legacy_page in (self.idea_page, self.storyboard_page):
            legacy_index = self.mode_tabs.indexOf(legacy_page)
            if legacy_index >= 0:
                self.mode_tabs.removeTab(legacy_index)
        self.mode_tabs.setCurrentWidget(self.script_page)

    def _build_idea_page(self):
        page = QWidget()
        self.idea_page = page
        root = QVBoxLayout(page)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("1  写故事")
        title.setStyleSheet("color:#f0f0f4;font-size:16px;font-weight:bold;")
        head.addWidget(title)
        intro = QLabel("写一句大概方向也可以，AI 会帮你补成能够直接拆分镜头的故事。")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#8993a4;font-size:12px;")
        head.addWidget(intro, 1)
        root.addLayout(head)

        quick = QHBoxLayout()
        quick.addWidget(QLabel("没有思路？"))
        for label, request in (
            ("给我 3 个创意", "根据我接下来提供的大概方向，先发散出3个差异明显、适合短视频的创意。"),
            ("想一个完整故事", "请帮我构思一个有钩子、有冲突、有转折和明确结尾的完整短视频故事。"),
            ("优化这版故事", "请检查我现有的故事，强化开场钩子、人物动机、冲突、转折和结尾。"),
        ):
            button = QPushButton(label)
            button.setStyleSheet(_IDEA_CHIP)
            button.clicked.connect(lambda _=False, text=request: self._idea_quick(text))
            quick.addWidget(button)
        quick.addStretch()
        root.addLayout(quick)

        story_label = QLabel("故事内容")
        story_label.setStyleSheet("color:#d8d8df;font-size:12px;font-weight:bold;")
        root.addWidget(story_label)
        self.director_idea = QTextEdit()
        self.director_idea.setPlaceholderText(
            "例：雨夜里，一名女孩发现手机壁纸中的人物突然动了起来。"
            "她试着触碰屏幕，房间随即变成壁纸里的世界。最后自然展示 Themepack。\n\n"
            "可以直接粘贴完整剧本，也可以只写一句方向后点击“AI 写完整故事”。"
        )
        self.director_idea.setMinimumHeight(180)
        self.director_idea.setStyleSheet(_EDITOR)
        root.addWidget(self.director_idea, 1)

        action = QHBoxLayout()
        self.btn_toggle_idea = QPushButton("帮我想创意")
        self.btn_toggle_idea.setStyleSheet(_GHOST)
        self.btn_toggle_idea.clicked.connect(self._toggle_idea_assistant)
        action.addWidget(self.btn_toggle_idea)
        btn_full_story = QPushButton("AI 写完整故事")
        btn_full_story.setStyleSheet(_DIRECTOR_IMG)
        btn_full_story.clicked.connect(self._draft_story_with_ai)
        action.addWidget(btn_full_story)
        self.btn_director_advanced = QPushButton("高级设置 ▾")
        self.btn_director_advanced.setStyleSheet(_GHOST)
        self.btn_director_advanced.clicked.connect(self._toggle_director_advanced)
        action.addWidget(self.btn_director_advanced)
        action.addStretch()
        self.director_progress = QProgressBar()
        self.director_progress.setRange(0, 100)
        self.director_progress.setFixedWidth(160)
        self.director_progress.setTextVisible(False)
        self.director_progress.setStyleSheet(
            "QProgressBar{background:#101012;border:none;height:4px;}"
            "QProgressBar::chunk{background:#8b6cf0;}"
        )
        action.addWidget(self.director_progress)
        self.btn_director = QPushButton("生成剧本和分镜  →")
        self.btn_director.setStyleSheet(_DIRECTOR_PRIMARY)
        self.btn_director.setFixedHeight(38)
        self.btn_director.setMinimumWidth(168)
        self.btn_director.clicked.connect(self._generate_storyboard)
        action.addWidget(self.btn_director)
        root.addLayout(action)

        self.director_status = QLabel(
            "先生成文字剧本和可编辑分镜草稿；这一步不会调用图片或视频额度。")
        self.director_status.setWordWrap(True)
        self.director_status.setStyleSheet("color:#74747f;font-size:11px;padding:2px 0;")
        root.addWidget(self.director_status)

        self.director_advanced_box = QFrame()
        self.director_advanced_box.setObjectName("DirectorAdvancedBox")
        self.director_advanced_box.setStyleSheet(
            "#DirectorAdvancedBox{background:#151519;border:1px solid #303038;"
            "border-radius:8px;}#DirectorAdvancedBox QLabel{background:transparent;}"
        )
        advanced = QHBoxLayout(self.director_advanced_box)
        advanced.setContentsMargins(12, 8, 12, 8)
        advanced.setSpacing(10)
        advanced.addWidget(QLabel("画面比例"))
        self.director_ratio = QComboBox()
        self.director_ratio.addItems(["9:16", "16:9", "1:1", "4:5", "3:4"])
        self.director_ratio.setStyleSheet(_COMBO)
        advanced.addWidget(self.director_ratio)
        advanced.addWidget(QLabel("整体节奏"))
        self.director_pace = QComboBox()
        self.director_pace.addItems(["适中", "快速", "舒缓", "强节奏"])
        self.director_pace.setStyleSheet(_COMBO)
        advanced.addWidget(self.director_pace)
        btn_use_script = QPushButton("引用广告文案")
        btn_use_script.setStyleSheet(_GHOST)
        btn_use_script.clicked.connect(self._use_copy_as_director_idea)
        advanced.addWidget(btn_use_script)
        advanced.addStretch()
        note = QLabel("时长会根据故事和对白自动判断")
        note.setStyleSheet("color:#74747f;font-size:11px;")
        advanced.addWidget(note)
        self.director_advanced_box.hide()
        root.addWidget(self.director_advanced_box)

        self.idea_assistant_box = QFrame()
        self.idea_assistant_box.setObjectName("IdeaAssistantBox")
        self.idea_assistant_box.setStyleSheet(
            "#IdeaAssistantBox{background:#141417;border:1px solid #303038;"
            "border-radius:8px;}#IdeaAssistantBox QLabel{background:transparent;}"
        )
        assistant = QVBoxLayout(self.idea_assistant_box)
        assistant.setContentsMargins(10, 8, 10, 10)
        assistant.setSpacing(7)
        assistant_head = QHBoxLayout()
        assistant_title = QLabel("创意助手")
        assistant_title.setStyleSheet("color:#d8d8df;font-size:12px;font-weight:bold;")
        assistant_head.addWidget(assistant_title)
        assistant_head.addStretch()
        clear_btn = QPushButton("清空对话")
        clear_btn.setStyleSheet(_GHOST)
        clear_btn.clicked.connect(self._clear_idea_chat)
        assistant_head.addWidget(clear_btn)
        close_btn = QPushButton("收起")
        close_btn.setStyleSheet(_GHOST)
        close_btn.clicked.connect(self._toggle_idea_assistant)
        assistant_head.addWidget(close_btn)
        assistant.addLayout(assistant_head)

        self.idea_chat = QTextBrowser()
        self.idea_chat.setOpenExternalLinks(False)
        self.idea_chat.setMinimumHeight(170)
        self.idea_chat.setMaximumHeight(280)
        self.idea_chat.setStyleSheet(
            "QTextBrowser{background:#101012;border:1px solid #252529;border-radius:8px;"
            "color:#ddd;padding:10px;font-size:13px;}"
        )
        assistant.addWidget(self.idea_chat)

        self.idea_input = QTextEdit()
        self.idea_input.setPlaceholderText("继续补充方向，或者让 AI 修改刚才的故事……")
        self.idea_input.setMaximumHeight(76)
        self.idea_input.setStyleSheet(_EDITOR)
        assistant.addWidget(self.idea_input)

        bottom = QHBoxLayout()
        self.idea_status = QLabel("只给一个题材、产品或情绪也可以。")
        self.idea_status.setStyleSheet("color:#6f7480;font-size:11px;")
        bottom.addWidget(self.idea_status, 1)
        self.btn_adopt_idea = QPushButton("采用为故事")
        self.btn_adopt_idea.setStyleSheet(_DIRECTOR_IMG)
        self.btn_adopt_idea.clicked.connect(self._adopt_idea)
        bottom.addWidget(self.btn_adopt_idea)
        self.btn_send_idea = QPushButton("发送")
        self.btn_send_idea.setStyleSheet(_DIRECTOR_PRIMARY)
        self.btn_send_idea.setFixedWidth(90)
        self.btn_send_idea.clicked.connect(self._send_idea)
        bottom.addWidget(self.btn_send_idea)
        assistant.addLayout(bottom)
        self.idea_assistant_box.hide()
        root.addWidget(self.idea_assistant_box)

        self.mode_tabs.addTab(page, "写故事")
        self._render_idea_chat()

    def _build_director_page(self):
        """兼容旧入口；导演输入已经合并进“写故事”页。"""
        self.director_page = self.idea_page

    def _build_storyboard_page(self):
        page = QWidget()
        self.storyboard_page = page
        root = QVBoxLayout(page)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        bar = QHBoxLayout()
        step_title = QLabel("2  GPT 剧本与分镜草稿")
        step_title.setStyleSheet("color:#f0f0f4;font-size:16px;font-weight:bold;")
        bar.addWidget(step_title)
        self.storyboard_title = QLabel("尚未生成分镜")
        self.storyboard_title.setStyleSheet("color:#8f96a5;font-size:12px;")
        bar.addWidget(self.storyboard_title)
        bar.addStretch()
        btn_assets = QPushButton("资产库")
        btn_assets.setStyleSheet(_GHOST)
        btn_assets.clicked.connect(lambda: self.resource_center_requested.emit(""))
        bar.addWidget(btn_assets)
        self.storyboard_settings_btn = QPushButton("高级设置 ▾")
        self.storyboard_settings_btn.setStyleSheet(_GHOST)
        self.storyboard_settings_btn.clicked.connect(self._toggle_storyboard_settings)
        bar.addWidget(self.storyboard_settings_btn)
        root.addLayout(bar)

        self.storyboard_summary = QLabel("请先让 AI 导演生成分镜。")
        self.storyboard_summary.setWordWrap(True)
        self.storyboard_summary.setMaximumHeight(42)
        self.storyboard_summary.setStyleSheet("color:#8993a4;font-size:11px;")
        root.addWidget(self.storyboard_summary)

        self.storyboard_plan_box = QFrame()
        self.storyboard_plan_box.setObjectName("StoryboardPlanBox")
        self.storyboard_plan_box.setStyleSheet(
            "#StoryboardPlanBox{background:#17151d;border:1px solid #382f49;"
            "border-radius:8px;}#StoryboardPlanBox QLabel{background:transparent;}"
        )
        plan_root = QVBoxLayout(self.storyboard_plan_box)
        plan_root.setContentsMargins(10, 7, 10, 8)
        plan_root.setSpacing(6)
        plan_head = QHBoxLayout()
        plan_title = QLabel("GPT 前期制作方案")
        plan_title.setStyleSheet("color:#b7a1e9;font-size:11px;font-weight:bold;")
        plan_head.addWidget(plan_title)
        self.storyboard_plan_status = QLabel("生成分镜后会在这里看到故事剧本和连续性规则")
        self.storyboard_plan_status.setStyleSheet("color:#8f8999;font-size:10px;")
        plan_head.addWidget(self.storyboard_plan_status, 1)
        self.storyboard_plan_toggle = QPushButton("查看文字剧本 ▾")
        self.storyboard_plan_toggle.setStyleSheet(_GHOST)
        self.storyboard_plan_toggle.clicked.connect(self._toggle_storyboard_plan)
        plan_head.addWidget(self.storyboard_plan_toggle)
        plan_root.addLayout(plan_head)
        self.storyboard_plan_view = QTextBrowser()
        self.storyboard_plan_view.setOpenExternalLinks(False)
        self.storyboard_plan_view.setMaximumHeight(260)
        self.storyboard_plan_view.setStyleSheet(
            "QTextBrowser{background:#101012;border:1px solid #2b2634;"
            "border-radius:6px;color:#d3d0d8;padding:8px;font-size:11px;}"
        )
        self.storyboard_plan_view.hide()
        plan_root.addWidget(self.storyboard_plan_view)
        root.addWidget(self.storyboard_plan_box)

        prep = QFrame()
        prep.setObjectName("StoryboardPreparation")
        prep.setStyleSheet(
            "#StoryboardPreparation{background:#131b18;border:1px solid #294237;"
            "border-radius:8px;}#StoryboardPreparation QLabel{background:transparent;}"
        )
        prep_layout = QHBoxLayout(prep)
        prep_layout.setContentsMargins(10, 7, 10, 7)
        prep_title = QLabel("3  正式生成准备")
        prep_title.setStyleSheet("color:#83d7ad;font-size:11px;font-weight:bold;")
        prep_layout.addWidget(prep_title)
        self.visual_lock_status = QLabel("还没有选择场景参考")
        self.visual_lock_status.setStyleSheet("color:#9aa49f;font-size:10px;")
        prep_layout.addWidget(self.visual_lock_status)
        prep_layout.addStretch()
        self.btn_prepare_all_assets = QPushButton("智能准备全部镜头")
        self.btn_prepare_all_assets.setStyleSheet(_DIRECTOR_IMG)
        self.btn_prepare_all_assets.setToolTip(
            "识别全片需要的场景、主体和元素；去重后在一个窗口中确认，已定稿素材自动跳过")
        self.btn_prepare_all_assets.clicked.connect(
            self._prepare_all_storyboard_assets)
        prep_layout.addWidget(self.btn_prepare_all_assets)
        self.btn_batch_keyframes = QPushButton("生成所有缺失画面")
        self.btn_batch_keyframes.setStyleSheet(_DIRECTOR_VIDEO)
        self.btn_batch_keyframes.setToolTip(
            "逐镜头生成候选；每个镜头定稿一张后自动继续下一镜")
        self.btn_batch_keyframes.clicked.connect(self._start_batch_keyframes)
        prep_layout.addWidget(self.btn_batch_keyframes)
        btn_import = QPushButton("导入剪辑台")
        btn_import.setStyleSheet(_PRIMARY)
        btn_import.clicked.connect(self._import_storyboard)
        prep_layout.addWidget(btn_import)
        root.addWidget(prep)

        self.storyboard_settings_box = QFrame()
        self.storyboard_settings_box.setObjectName("StoryboardSettingsBox")
        self.storyboard_settings_box.setStyleSheet(
            "#StoryboardSettingsBox{background:#151519;border:1px solid #303038;"
            "border-radius:8px;}#StoryboardSettingsBox QLabel{background:transparent;}"
        )
        settings_root = QVBoxLayout(self.storyboard_settings_box)
        settings_root.setContentsMargins(10, 8, 10, 8)
        settings_root.setSpacing(7)

        settings_head = QHBoxLayout()
        settings_title = QLabel("生成设置（平时无需修改）")
        settings_title.setStyleSheet("color:#d0d0d8;font-size:11px;font-weight:bold;")
        settings_head.addWidget(settings_title)
        settings_head.addStretch()
        audit_btn = QPushButton("检查素材是否准备好")
        audit_btn.setStyleSheet(_GHOST)
        audit_btn.clicked.connect(self._show_consistency_audit)
        settings_head.addWidget(audit_btn)
        settings_root.addLayout(settings_head)

        model_fields = QHBoxLayout()
        model_fields.setSpacing(7)
        model_fields.addWidget(QLabel("图片模型"))
        self.storyboard_image_provider = QComboBox()
        self.storyboard_image_provider.setMinimumWidth(145)
        model_fields.addWidget(self.storyboard_image_provider)
        model_fields.addWidget(QLabel("视频模型"))
        self.storyboard_video_provider = QComboBox()
        self.storyboard_video_provider.setMinimumWidth(135)
        model_fields.addWidget(self.storyboard_video_provider)
        model_fields.addWidget(QLabel("每个镜头生成"))
        self.storyboard_image_count = QComboBox()
        for count in (1, 2, 4, 6):
            self.storyboard_image_count.addItem(f"{count} 张候选", count)
        self.storyboard_image_count.setCurrentIndex(
            self.storyboard_image_count.findData(2))
        self.storyboard_image_count.setToolTip("候选越多，调用费用也会相应增加")
        model_fields.addWidget(self.storyboard_image_count)
        model_fields.addStretch()
        settings_root.addLayout(model_fields)

        asset_fields = QHBoxLayout()
        asset_fields.setSpacing(7)
        asset_fields.addWidget(QLabel("项目默认场景"))
        self.storyboard_scene_resource = QComboBox()
        self.storyboard_scene_resource.setMinimumWidth(170)
        asset_fields.addWidget(self.storyboard_scene_resource, 1)
        asset_fields.addWidget(QLabel("项目默认主体"))
        self.storyboard_character_resource = QComboBox()
        self.storyboard_character_resource.setMinimumWidth(170)
        asset_fields.addWidget(self.storyboard_character_resource, 1)
        settings_root.addLayout(asset_fields)
        root.addWidget(self.storyboard_settings_box)
        self.storyboard_settings_box.setVisible(False)

        for combo in (self.storyboard_image_provider,
                      self.storyboard_video_provider,
                      self.storyboard_character_resource,
                      self.storyboard_scene_resource,
                      self.storyboard_image_count):
            combo.setStyleSheet(
                "QComboBox{background:#202026;color:#ddd;border:1px solid #383842;"
                "border-radius:5px;padding:4px 7px;}"
                "QComboBox::drop-down{border:none;}"
                "QComboBox QAbstractItemView{background:#202026;color:#ddd;"
                "selection-background-color:#7657dd;}"
            )
        self.storyboard_image_provider.currentIndexChanged.connect(
            self._save_visual_lock)
        self.storyboard_video_provider.currentIndexChanged.connect(
            self._save_visual_lock)
        self.storyboard_character_resource.currentIndexChanged.connect(
            self._save_visual_lock)
        self.storyboard_scene_resource.currentIndexChanged.connect(
            self._save_visual_lock)
        self._refresh_image_providers()
        self._refresh_video_providers()
        self.refresh_resource_links("")

        self.storyboard_scroll = QScrollArea()
        self.storyboard_scroll.setWidgetResizable(True)
        self.storyboard_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.storyboard_scroll.setStyleSheet("QScrollArea{background:#101012;border:none;}")
        self.storyboard_content = QWidget()
        self.storyboard_layout = QVBoxLayout(self.storyboard_content)
        # 给最后一个镜头保留足够的“越界滚动”空间，避免主按钮贴在
        # 滚动区底边或被系统滚动条遮住而无法点击。
        self.storyboard_layout.setContentsMargins(4, 4, 4, 104)
        self.storyboard_layout.setSpacing(8)
        self.storyboard_layout.addStretch()
        self.storyboard_scroll.setWidget(self.storyboard_content)

        self.storyboard_production = StoryboardProductionPanel()
        self.storyboard_production.asset_selected.connect(self._on_panel_asset_selected)
        self.storyboard_production.asset_approved.connect(
            self._approve_storyboard_asset)
        self.storyboard_production.preview_requested.connect(self._preview_storyboard_asset)
        self.storyboard_production.image_to_video_requested.connect(
            self._request_image_video_for_shot)
        self.storyboard_production.reference_anchor_requested.connect(
            self._save_reference_anchor)
        self.storyboard_production.exact_element_requested.connect(
            self._compose_exact_element)
        self.storyboard_production.remove_result_requested.connect(
            self._remove_storyboard_result)
        self.storyboard_production.keep_result_only_requested.connect(
            self._keep_only_storyboard_result)
        self.storyboard_production.refine_requested.connect(
            self._request_ps_refine_path)

        self.storyboard_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.storyboard_splitter.setChildrenCollapsible(False)
        self.storyboard_splitter.setHandleWidth(6)
        self.storyboard_splitter.setStyleSheet(
            "QSplitter::handle{background:#202026;border-radius:2px;}"
            "QSplitter::handle:hover{background:#6651a7;}"
        )
        self.storyboard_splitter.addWidget(self.storyboard_scroll)
        self.storyboard_splitter.addWidget(self.storyboard_production)
        self.storyboard_splitter.setStretchFactor(0, 1)
        self.storyboard_splitter.setStretchFactor(1, 0)
        self.storyboard_splitter.setSizes([760, 390])
        root.addWidget(self.storyboard_splitter, 1)
        self.mode_tabs.addTab(page, "AI 分镜")

    def _toggle_storyboard_settings(self):
        show = self.storyboard_settings_box.isHidden()
        self.storyboard_settings_box.setVisible(show)
        self.storyboard_settings_btn.setText(
            "收起高级设置 ▴" if show else "高级设置 ▾")

    def _toggle_storyboard_plan(self):
        show = self.storyboard_plan_view.isHidden()
        self.storyboard_plan_view.setVisible(show)
        self.storyboard_plan_toggle.setText(
            "收起文字剧本 ▴" if show else "查看文字剧本 ▾")

    def _render_storyboard_plan(self):
        board = self._storyboard or {}
        production = board.get("production_bible", {}) or {}
        screenplay = board.get("screenplay", {}) or {}
        beats = screenplay.get("beats", []) or []
        shots = board.get("shots", []) or []
        if not shots:
            self.storyboard_plan_status.setText(
                "生成分镜后会在这里看到故事剧本和连续性规则")
            self.storyboard_plan_view.setHtml("")
            return

        contract_count = sum(
            1 for shot in shots if isinstance(shot.get("shot_contract"), dict))
        self.storyboard_plan_status.setText(
            f"已建立故事剧本 · {len(beats)} 个剧情段 · "
            f"{contract_count}/{len(shots)} 个镜头拍摄合同")

        def block(title: str, value: str):
            value = str(value or "").strip()
            if not value:
                return ""
            return (
                f"<div style='margin:7px 0 2px;color:#ab96dd;font-weight:600'>"
                f"{html.escape(title)}</div><div style='color:#d0cdd5;line-height:1.55'>"
                f"{html.escape(value).replace(chr(10), '<br>')}</div>")

        parts = [block("一句话故事", production.get("logline") or board.get("summary"))]
        for title, key in (
                ("开场钩子", "hook"), ("人物与铺垫", "setup"),
                ("核心冲突", "conflict"), ("转折", "turn"),
                ("结尾", "ending")):
            parts.append(block(title, screenplay.get(key)))
        style = "；".join(value for value in (
            production.get("tone", ""), production.get("visual_style", ""),
            production.get("color_script", "")) if value)
        parts.append(block("视觉与情绪", style))
        rules = production.get("continuity_rules", []) or []
        if rules:
            parts.append(
                "<div style='margin:7px 0 2px;color:#ab96dd;font-weight:600'>"
                "全片连续性规则</div><ul style='margin:3px 0 6px;padding-left:20px'>" +
                "".join(
                    f"<li style='margin:2px 0;color:#c8c4cd'>{html.escape(str(rule))}</li>"
                    for rule in rules) + "</ul>")
        if beats:
            beat_rows = []
            for index, beat in enumerate(beats, 1):
                start = float(beat.get("start", 0) or 0)
                end = float(beat.get("end", 0) or 0)
                label = f"{start:g}—{end:g}s" if end > start else f"段落 {index}"
                description = str(beat.get("summary") or beat.get("purpose") or "")
                state = ""
                if beat.get("entry_state") or beat.get("exit_state"):
                    state = (
                        f"（{beat.get('entry_state') or '承接前段'} → "
                        f"{beat.get('exit_state') or '进入后段'}）")
                beat_rows.append(
                    f"<li style='margin:3px 0;color:#c8c4cd'><b>{html.escape(label)}</b> "
                    f"{html.escape(description)} {html.escape(state)}</li>")
            parts.append(
                "<div style='margin:7px 0 2px;color:#ab96dd;font-weight:600'>"
                "剧情节拍</div><ol style='margin:3px 0 6px;padding-left:22px'>" +
                "".join(beat_rows) + "</ol>")
        self.storyboard_plan_view.setHtml("".join(part for part in parts if part))

    def _toggle_idea_assistant(self, *_):
        show = self.idea_assistant_box.isHidden()
        self.idea_assistant_box.setVisible(show)
        self.btn_toggle_idea.setText("收起创意助手" if show else "帮我想创意")
        if show:
            self.idea_input.setFocus()

    def _toggle_director_advanced(self, *_):
        show = self.director_advanced_box.isHidden()
        self.director_advanced_box.setVisible(show)
        self.btn_director_advanced.setText(
            "收起高级设置 ▴" if show else "高级设置 ▾")

    def _draft_story_with_ai(self, *_):
        direction = self.director_idea.toPlainText().strip()
        if direction:
            prompt = (
                "请把下面的方向直接完善成一版完整、可拍摄的短视频故事。"
                "必须包含开场钩子、人物与动机、冲突、转折、结尾记忆点；"
                "如含推广内容，要自然融入，不要拆逐秒分镜，也不要继续提问。\n\n"
                f"当前方向：\n{direction}"
            )
        else:
            prompt = (
                "请先给我3个差异明显、适合短视频的故事创意。"
                "每个都写清开场钩子、冲突、转折和结尾；我选定后再完善完整故事。"
            )
        if self.idea_assistant_box.isHidden():
            self._toggle_idea_assistant()
        self.idea_input.setPlainText(prompt)
        self._send_idea()

    def _use_copy_as_director_idea(self):
        text = self.editor_result.toPlainText().strip()
        if not text:
            self.status_msg.emit("请先生成或填写文案", "warn")
            return
        current = self.director_idea.toPlainText().strip()
        prefix = f"请根据下面的旁白设计完整分镜：\n{text}"
        self.director_idea.setPlainText(f"{current}\n\n{prefix}".strip())
        self.mode_tabs.setCurrentWidget(self.director_page)

    def _idea_quick(self, text: str):
        direction = self.director_idea.toPlainText().strip()
        if direction:
            text = f"{text}\n\n这是我目前写的方向：\n{direction}"
        if self.idea_assistant_box.isHidden():
            self._toggle_idea_assistant()
        self.idea_input.setPlainText(text)
        self.idea_input.setFocus()

    def _send_idea(self):
        user_text = self.idea_input.toPlainText().strip()
        if not user_text:
            self.status_msg.emit("先说一个大概方向或点击快速开始", "warn")
            return
        if self._idea_worker is not None and self._idea_worker.isRunning():
            self.status_msg.emit("AI 创意搭档正在回复，请稍候", "warn")
            return
        self._idea_messages.append({"role": "user", "content": user_text})
        self.idea_input.clear()
        self._render_idea_chat()
        self.btn_send_idea.setEnabled(False)
        self.btn_send_idea.setText("思考中…")
        self.idea_status.setText("AI 正在梳理创意方向……")
        self._idea_worker = _IdeaWorker(self._idea_messages)
        self._idea_worker.finished.connect(self._on_idea_reply)
        self._idea_worker.error.connect(self._on_idea_error)
        self._idea_worker.start()

    def _on_idea_reply(self, text: str):
        self._idea_messages.append({"role": "assistant", "content": text})
        self._render_idea_chat()
        self.btn_send_idea.setEnabled(True)
        self.btn_send_idea.setText("发送")
        self.idea_status.setText("可以继续修改；满意后点击“采用为故事”。")
        self.status_msg.emit("AI 创意回复完成", "success")
        if self._idea_worker:
            self._idea_worker.deleteLater()
        self._idea_worker = None

    def _on_idea_error(self, error: str):
        self.btn_send_idea.setEnabled(True)
        self.btn_send_idea.setText("发送")
        self.idea_status.setText("回复失败，可以修改问题后重试。")
        self.status_msg.emit(f"AI 创意对话失败：{error.splitlines()[0]}", "error")
        if self._idea_worker:
            self._idea_worker.deleteLater()
        self._idea_worker = None

    def _render_idea_chat(self):
        if not self._idea_messages:
            self.idea_chat.setHtml(
                "<div style='color:#888;line-height:1.8;font-size:13px'>"
                "告诉我一个题材、产品、受众、情绪，甚至只是一句话，"
                "我会先发散创意，再和你一起收敛成可交给导演的故事方案。</div>"
            )
            return
        blocks = []
        for message in self._idea_messages:
            is_user = message.get("role") == "user"
            name = "你" if is_user else "AI 创意搭档"
            raw = str(message.get("content", ""))
            if is_user:
                content = html.escape(raw).replace("\n", "<br>")
                blocks.append(
                    f"<div style='margin:8px 2px;padding:10px 14px;background:#1a2733;"
                    f"border-radius:8px;line-height:1.7'><b style='color:#5a9ff9'>{name}</b>"
                    f"<div style='color:#d0d4dc;margin-top:4px'>{content}</div></div>"
                )
            else:
                content = self._markdown_to_html(raw)
                blocks.append(
                    f"<div style='margin:8px 2px;padding:12px 16px;background:#1c1c22;"
                    f"border-radius:8px;line-height:1.7'><b style='color:#7a7a85'>{name}</b>"
                    f"<div style='color:#d0d4dc;margin-top:6px'>{content}</div></div>"
                )
        self.idea_chat.setHtml("".join(blocks))
        bar = self.idea_chat.verticalScrollBar()
        bar.setValue(bar.maximum())

    @staticmethod
    def _markdown_to_html(text: str) -> str:
        import re
        lines = text.split("\n")
        result: list[str] = []
        in_ul = False
        in_ol = False

        def _flush_lists():
            nonlocal in_ul, in_ol
            if in_ul:
                result.append("</ul>")
                in_ul = False
            if in_ol:
                result.append("</ol>")
                in_ol = False

        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            if not stripped:
                _flush_lists()
                prev_ok = i > 0 and lines[i - 1].strip()
                next_ok = i + 1 < len(lines) and lines[i + 1].strip()
                if prev_ok and next_ok:
                    result.append("<div style='height:6px'></div>")
                i += 1
                continue

            # 分隔线
            if stripped in ("---", "***", "___", "* * *"):
                _flush_lists()
                result.append(
                    "<hr style='border:none;border-top:1px solid #2a2a34;margin:8px 0'>")
                i += 1
                continue

            # 标题
            heading_match = re.match(r"^(#{1,3})\s+(.+)", stripped)
            if heading_match:
                _flush_lists()
                level = len(heading_match.group(1))
                sizes = {1: 15, 2: 14, 3: 13}
                margins = {1: "12px 0 6px", 2: "10px 0 5px", 3: "8px 0 4px"}
                body = _inline_md(heading_match.group(2))
                result.append(
                    f"<div style='color:#d8d8e0;font-size:{sizes.get(level, 13)}px;"
                    f"font-weight:500;margin:{margins.get(level, '8px 0 4px')}'>{body}</div>")
                i += 1
                continue

            # 无序列表
            ul_match = re.match(r"^[\-\*]\s+(.+)", stripped)
            if ul_match:
                if in_ol:
                    result.append("</ol>")
                    in_ol = False
                if not in_ul:
                    result.append(
                        "<ul style='margin:3px 0;padding-left:20px;list-style-type:disc'>")
                    in_ul = True
                body = _inline_md(ul_match.group(1))
                result.append(
                    f"<li style='color:#b0b4be;margin:2px 0;line-height:1.65'>{body}</li>")
                i += 1
                continue

            # 有序列表
            ol_match = re.match(r"^(\d+)[\.\)]\s+(.+)", stripped)
            if ol_match:
                if in_ul:
                    result.append("</ul>")
                    in_ul = False
                if not in_ol:
                    result.append(
                        "<ol style='margin:3px 0;padding-left:20px'>")
                    in_ol = True
                body = _inline_md(ol_match.group(2))
                result.append(
                    f"<li style='color:#b0b4be;margin:2px 0;line-height:1.65'>{body}</li>")
                i += 1
                continue

            # 引用
            quote_match = re.match(r"^>\s?(.*)", stripped)
            if quote_match:
                _flush_lists()
                body = _inline_md(quote_match.group(1) or "&nbsp;")
                result.append(
                    f"<div style='border-left:2px solid #3a3a46;padding:3px 0 3px 10px;"
                    f"color:#95959e;margin:3px 0'>{body}</div>")
                i += 1
                continue

            # 普通段落
            _flush_lists()
            body = _inline_md(stripped)
            result.append(
                f"<div style='color:#c8cad2;line-height:1.7;margin:3px 0'>{body}</div>")
            i += 1

        _flush_lists()
        return "".join(result)


    def _clear_idea_chat(self):
        if self._idea_worker is not None and self._idea_worker.isRunning():
            self.status_msg.emit("AI 正在回复，完成后再清空", "warn")
            return
        self._idea_messages.clear()
        self._render_idea_chat()
        self.idea_status.setText("新对话已准备好。")

    def _adopt_idea(self):
        replies = [m.get("content", "") for m in self._idea_messages
                   if m.get("role") == "assistant" and m.get("content")]
        if not replies:
            self.status_msg.emit("请先和 AI 创意搭档聊出一个方案", "warn")
            return
        adopted = replies[-1]
        self.director_idea.setPlainText(adopted)
        self.idea_assistant_box.hide()
        self.btn_toggle_idea.setText("帮我想创意")
        self.director_status.setText("故事已采用，可以继续修改或直接生成分镜。")
        self.director_status.setStyleSheet(
            "color:#68d5a2;font-size:11px;padding:2px 0;")
        self.status_msg.emit("故事已放入编辑区", "success")
        self.director_idea.setFocus()

    def _on_assets_piped(self, summary: str):
        self.status_msg.emit(summary, "success")
        self._show_asset_ready_dialog(summary)

    def _show_asset_ready_dialog(self, summary: str):
        dlg = QDialog(self)
        dlg.setWindowTitle("角色/场景资产已就绪")
        dlg.setFixedSize(460, 260)
        dlg.setStyleSheet("QDialog{background:#1a1a20;color:#d0d4dc;}")
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        title = QLabel("资产提取完成")
        title.setStyleSheet("font-size:15px;font-weight:500;color:#d8d8e0;")
        layout.addWidget(title)

        msg = QLabel(
            f"{summary}。\n\n"
            "为了确保后续所有镜头中角色长相和场景风格一致，\n"
            "建议先为每个角色和场景生成参考图（定妆照/概念图），\n"
            "再做AI导演分镜。")
        msg.setWordWrap(True)
        msg.setStyleSheet("font-size:13px;line-height:1.7;color:#b0b4be;")
        layout.addWidget(msg)

        hint = QLabel(
            "已自动提取的角色/场景已放入「AI制片画布」，"
            "其中包含可直接使用的生图提示词。")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size:11px;color:#7a7a85;")
        layout.addWidget(hint)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_skip = QPushButton("先跳到AI导演")
        btn_skip.setStyleSheet(
            "QPushButton{background:transparent;color:#7a7a85;border:1px solid #383842;"
            "border-radius:6px;padding:8px 16px;font-size:12px;}"
            "QPushButton:hover{color:#aaa;border-color:#555;}")
        btn_skip.clicked.connect(dlg.reject)
        btn_row.addWidget(btn_skip)

        btn_row.addStretch()

        btn_go = QPushButton("去AI制片画布生成定妆照")
        btn_go.setStyleSheet(
            "QPushButton{background:#3d8ef8;color:#fff;border:none;border-radius:6px;"
            "padding:10px 20px;font-size:13px;font-weight:500;}"
            "QPushButton:hover{background:#5a9ff9;}")
        btn_go.clicked.connect(lambda: dlg.accept())
        btn_row.addWidget(btn_go)

        layout.addLayout(btn_row)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.resource_center_requested.emit("")
        else:
            self.mode_tabs.setCurrentWidget(self.director_page)
            self.director_idea.setFocus()

    @staticmethod
    def _infer_director_duration(text: str) -> int:
        """优先采用用户写明的时长，否则按内容体量估算，避免额外设置项。"""
        import re
        value = str(text or "")
        combined = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:分钟|分|min(?:ute)?s?)\s*"
            r"(\d+(?:\.\d+)?)\s*(?:秒|秒钟|s(?:ec(?:ond)?s?)?)",
            value, re.IGNORECASE)
        if combined:
            return max(5, min(300, int(round(
                float(combined.group(1)) * 60 + float(combined.group(2))))))
        minutes = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:分钟|min(?:ute)?s?)",
            value, re.IGNORECASE)
        if minutes:
            return max(5, min(300, int(round(float(minutes.group(1)) * 60))))
        seconds = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:秒|秒钟|s(?:ec(?:ond)?s?)?)",
            value, re.IGNORECASE)
        if seconds:
            return max(5, min(300, int(round(float(seconds.group(1))))))
        compact_length = len(re.sub(r"\s+", "", value))
        estimated = max(30, min(300, compact_length / 4.0))
        return int(round(estimated / 5.0) * 5)

    def _generate_storyboard(self):
        idea = self.director_idea.toPlainText().strip()
        if not idea:
            self.director_status.setText("请先在上方填写视频内容或故事方向。")
            self.director_status.setStyleSheet("color:#efb36b;font-size:11px;padding:2px 0;")
            self.director_idea.setFocus()
            self.status_msg.emit("请先描述你想制作的视频", "warn")
            return
        if self._worker is not None and self._worker.isRunning():
            self.director_status.setText("已有导演任务运行中，请等待当前任务结束。")
            self.status_msg.emit("当前 AI 任务正在运行，请稍候", "warn")
            return
        target_duration = self._infer_director_duration(idea)
        self.btn_director.setEnabled(False)
        self.btn_director.setText("导演构思中…")
        self.director_progress.setValue(10)
        self.director_status.setText(
            f"自动判断约 {target_duration} 秒 · 正在读取项目资产并连接文本模型…")
        self.director_status.setStyleSheet("color:#a997ee;font-size:11px;padding:2px 0;")

        asset_context = ""
        try:
            from ai.service import get_asset_db
            db = get_asset_db()
            # 导演只需要目录摘要；限制条数和描述长度，避免资产库越用越大后
            # 每次都把无关长 Prompt 塞进文本模型请求。
            chars = db.list_characters(limit=30)
            scenes = db.list_scenes(limit=30)
            elements = db.list_elements(limit=30)

            def ready(item) -> bool:
                return asset_is_approved(item, require_file=True)

            if chars or scenes or elements:
                catalog = {
                    "characters": [{
                        "asset_id": ch.id,
                        "name": ch.name,
                        "entity_type": getattr(ch, "entity_type", "human"),
                        "description": str(ch.description or "")[:300],
                        "design_lock": str(getattr(ch, "design_notes", "") or "")[:240],
                        "ready": ready(ch),
                        "approved_version": int(getattr(ch, "version", 0) or 0),
                    } for ch in chars],
                    "scenes": [{
                        "asset_id": sc.id,
                        "name": sc.name,
                        "description": str(sc.description or "")[:300],
                        "ready": ready(sc),
                        "approved_version": int(getattr(sc, "version", 0) or 0),
                    } for sc in scenes],
                    "elements": [{
                        "asset_id": item.id,
                        "name": item.name,
                        "element_type": getattr(item, "element_type", "object"),
                        "description": str(item.description or "")[:300],
                        "ready": ready(item),
                        "approved_version": int(getattr(item, "version", 0) or 0),
                    } for item in elements],
                }
                asset_context = json.dumps(catalog, ensure_ascii=False)
        except Exception:
            pass

        generation_state = (self._storyboard or {}).get("_director_generation", {})
        resume_board = None
        if (isinstance(generation_state, dict) and
                generation_state.get("status") == "partial" and
                generation_state.get("idea") == idea and
                int(generation_state.get("target_duration", 0) or 0) ==
                target_duration):
            resume_board = self._storyboard
            self.btn_director.setText("继续生成剩余分镜…")
            completed = int(generation_state.get("completed_segments", 0) or 0)
            total = len(generation_state.get("segments", []) or [])
            self.director_status.setText(f"正在从第 {completed + 1}/{total} 段继续…")

        self._worker = _DirectorWorker(
            idea=idea,
            ratio=self.director_ratio.currentText(),
            duration=target_duration,
            pace=self.director_pace.currentText(),
            asset_context=asset_context,
            resume_board=resume_board,
        )
        self._worker.progress.connect(self.director_progress.setValue)
        self._worker.progress.connect(self._on_director_progress)
        self._worker.stage.connect(self._on_director_stage)
        self._worker.partial.connect(self._on_storyboard_partial)
        self._worker.finished.connect(self._on_storyboard_done)
        self._worker.error.connect(self._on_storyboard_error)
        self._worker.start()

    def _on_director_stage(self, text: str):
        self.director_status.setText(text)

    def _on_storyboard_partial(self, board: dict, message: str):
        """长分镜每完成一段就落到界面；后续失败时已完成内容不会丢。"""
        self._storyboard = board
        self._auto_bind_assets_to_storyboard(board)
        self._render_storyboard()
        self.storyboard_changed.emit()
        self.mode_tabs.setCurrentWidget(self.storyboard_page)
        self.director_status.setText(message)
        self.director_status.setStyleSheet(
            "color:#a997ee;font-size:11px;padding:2px 0;")

    def _on_storyboard_done(self, board: dict):
        self.btn_director.setEnabled(True)
        self.btn_director.setText("生成剧本和分镜  →")
        self.director_progress.setValue(100)
        self.director_status.setText(f"生成完成：{len(board.get('shots', []))} 个镜头")
        self.director_status.setStyleSheet("color:#68d5a2;font-size:11px;padding:2px 0;")
        self._storyboard = board
        # 自动绑定已生成的资产到分镜
        self._auto_bind_assets_to_storyboard(board)
        self._render_storyboard()
        self.storyboard_changed.emit()
        self.mode_tabs.setCurrentWidget(self.storyboard_page)
        status = f"导演分镜完成：{len(board.get('shots', []))} 个镜头"
        self.status_msg.emit(status, "success")
        if self._worker:
            self._worker.deleteLater()
        self._worker = None

    def _auto_bind_assets_to_storyboard(self, board: dict):
        """校验导演给出的稳定 ID；仅为旧分镜执行名称兼容匹配。"""
        try:
            from ai.service import get_asset_db
            db = get_asset_db()
            all_characters = db.list_characters(limit=5000)
            all_scenes = db.list_scenes(limit=5000)
            all_elements = db.list_elements(limit=5000)
        except Exception:
            return

        bible = board.setdefault("visual_bible", {})
        chars_by_id = {item.id: item for item in all_characters}
        scenes_by_id = {item.id: item for item in all_scenes}
        elements_by_id = {item.id: item for item in all_elements}
        char_map = {c.name.strip().casefold(): c for c in all_characters if c.name}
        scene_map = {s.name.strip().casefold(): s for s in all_scenes if s.name}
        element_map = {e.name.strip().casefold(): e for e in all_elements if e.name}

        for shot in board.get("shots", []):
            sync_legacy_bindings(shot)

            # V2 绑定直接使用 ID；模型幻觉出的未知 ID 不允许进入生产链。
            char_bindings = [
                item for item in shot.get("character_bindings", [])
                if item.get("asset_id") in chars_by_id]
            for binding in char_bindings:
                resource = chars_by_id[binding["asset_id"]]
                binding["name"] = str(getattr(resource, "name", "") or "")
                binding["version"] = int(getattr(resource, "version", 0) or 0)
            if not char_bindings:
                raw_names = list(shot.get("character_names", []) or [])
                raw_names.extend(__import__("re").split(
                    r"[、,，/&]+", str(shot.get("character", ""))))
                names = [str(part).strip().casefold() for part in raw_names
                         if str(part).strip()]
                for name in names:
                    item = char_map.get(name)
                    if item:
                        char_bindings.append({
                            "asset_id": item.id,
                            "version": int(getattr(item, "version", 0) or 0),
                            "role": "subject",
                            "outfit_state": "", "appearance_state": "", "required": True,
                        })
            shot["character_bindings"] = char_bindings
            # 清空旧兼容字段后再同步，防止模型幻觉出的未知 ID 被恢复。
            char_ids = [item["asset_id"] for item in char_bindings]
            shot["character_id"] = char_ids[0] if char_ids else ""
            shot["character_ids"] = char_ids[1:]
            if char_bindings:
                shot["character_names"] = [
                    item.get("name", "") for item in char_bindings if item.get("name")]
                shot["character"] = (
                    shot["character_names"][0] if shot["character_names"] else "")

            scene_id = str(shot.get("scene_asset_id") or shot.get("scene_id") or "")
            if scene_id not in scenes_by_id:
                scene_id = ""
                scene_name = str(shot.get("scene_name") or "").strip().casefold()
                if scene_name in scene_map:
                    scene_id = scene_map[scene_name].id
                elif not scene_name:
                    scene_text = str(shot.get("scene", "")).casefold()
                    matches = [scene for name, scene in scene_map.items()
                               if name and name in scene_text]
                    if len(matches) == 1:
                        scene_id = matches[0].id
            shot["scene_asset_id"] = scene_id
            shot["scene_id"] = scene_id
            scene = scenes_by_id.get(scene_id)
            shot["scene_version"] = int(getattr(scene, "version", 0) or 0)
            if scene is not None:
                shot["scene_name"] = str(getattr(scene, "name", "") or "")

            element_bindings = [
                item for item in shot.get("element_bindings", [])
                if item.get("asset_id") in elements_by_id]
            for binding in element_bindings:
                resource = elements_by_id[binding["asset_id"]]
                binding["name"] = str(getattr(resource, "name", "") or "")
                binding["version"] = int(getattr(resource, "version", 0) or 0)
            if not element_bindings:
                element_names = shot.get("element_names") or []
                if isinstance(element_names, str):
                    element_names = __import__("re").split(
                        r"[、,，/&]+", element_names)
                for name in element_names:
                    element = element_map.get(str(name).strip().casefold())
                    if element:
                        element_bindings.append({
                            "asset_id": element.id,
                            "version": int(getattr(element, "version", 0) or 0),
                            "mode": getattr(element, "default_mode", "exact") or "exact",
                            "placement": getattr(element, "placement_hint", "") or "",
                            "required": True,
                        })
            shot["element_bindings"] = element_bindings
            element_ids = [item["asset_id"] for item in element_bindings]
            shot["element_id"] = element_ids[0] if element_ids else ""
            shot["element_ids"] = element_ids[1:]
            if element_bindings:
                shot["element_names"] = [
                    item.get("name", "") for item in element_bindings if item.get("name")]
            sync_legacy_bindings(shot)
            shot["shot_contract"] = build_shot_contract(shot)

        # 全局默认只适用于整部短片确实只有一个场景/主体的情况。
        scene_ids = list(dict.fromkeys(
            shot.get("scene_asset_id", "") for shot in board.get("shots", [])
            if shot.get("scene_asset_id")))
        character_ids = list(dict.fromkeys(
            binding.get("asset_id", "")
            for shot in board.get("shots", [])
            for binding in shot.get("character_bindings", [])
            if binding.get("asset_id")))
        if len(scene_ids) == 1:
            bible["scene_id"] = scene_ids[0]
        elif len(scene_ids) > 1:
            bible["scene_id"] = ""
        if len(character_ids) == 1:
            bible["character_id"] = character_ids[0]
        elif len(character_ids) > 1:
            bible["character_id"] = ""
        rebuild_continuity(board)

    def _on_director_progress(self, value: int):
        text = (
            "正在连接文本模型…" if value <= 25 else
            "正在编写制作圣经、故事剧本和逐镜头草稿…" if value < 85 else
            "正在解析并校验分镜结构…"
        )
        self.director_status.setText(text)

    @staticmethod
    def _friendly_director_error(error: str):
        raw = str(error or "未知错误")
        lowered = raw.lower()
        if "401" in lowered or "invalid or expired api key" in lowered or "authentication" in lowered:
            return (
                "文本模型 Key 无效或已经过期。\n"
                "请打开左下角“设置”→“大模型”，更新 ModelHub / OpenAI Key；"
                "Ark 的 Seedream/Seedance Key 不能代替文本模型 Key。")
        if "timeout" in lowered or "timed out" in lowered:
            return "文本模型连接超时，请检查网络或 Base URL 后重试。"
        if "model" in lowered and ("not found" in lowered or "does not exist" in lowered):
            return "当前文本模型名称不可用，请在设置中更换模型后重试。"
        return raw.splitlines()[0][:300]

    def _on_storyboard_error(self, error: str):
        self.btn_director.setEnabled(True)
        state = (self._storyboard or {}).get("_director_generation", {})
        has_partial = (isinstance(state, dict) and
                       state.get("status") == "partial" and
                       int(state.get("completed_segments", 0) or 0) > 0)
        self.btn_director.setText(
            "继续生成剩余分镜" if has_partial else "生成剧本和分镜  →")
        if has_partial:
            completed = int(state.get("completed_segments", 0) or 0)
            total = len(state.get("segments", []) or [])
            self.director_progress.setValue(int(100 * completed / max(1, total)))
        else:
            self.director_progress.setValue(0)
        message = self._friendly_director_error(error)
        if has_partial:
            completed = int(state.get("completed_segments", 0) or 0)
            total = len(state.get("segments", []) or [])
            message += (
                f"\n\n已保留前 {completed}/{total} 段。再次点击“继续生成剩余分镜”"
                "会从断点继续，不会重新生成前面的镜头。")
        self.director_status.setText(message)
        self.director_status.setStyleSheet("color:#ef8585;font-size:11px;padding:2px 0;")
        self.status_msg.emit(f"分镜生成失败：{message.splitlines()[0]}", "error")
        QMessageBox.warning(self, "导演分镜生成失败", message)
        if self._worker:
            self._worker.deleteLater()
        self._worker = None

    def _refresh_image_providers(self):
        current = self.storyboard_image_provider.currentData()
        self.storyboard_image_provider.blockSignals(True)
        self.storyboard_image_provider.clear()
        labels = {
            "seedream": "Seedream 5.0 Pro",
            "gptimage": "GPT-Image-2",
            "flux": "FLUX",
        }
        providers = []
        if self._ai_manager is not None:
            try:
                providers = self._ai_manager.registry.by_capability("text_to_image")
            except Exception:
                providers = []
        for provider in providers:
            self.storyboard_image_provider.addItem(
                labels.get(provider.name, provider.name), provider.name)
        preferred = current or "seedream"
        index = self.storyboard_image_provider.findData(preferred)
        if index < 0 and current:
            self.storyboard_image_provider.addItem(
                f"{labels.get(str(current), str(current))}（当前不可用）", current)
            index = self.storyboard_image_provider.findData(current)
        elif index < 0 and self.storyboard_image_provider.count():
            index = 0
        self.storyboard_image_provider.setCurrentIndex(index)
        self.storyboard_image_provider.blockSignals(False)

    def _refresh_video_providers(self):
        current = self.storyboard_video_provider.currentData()
        self.storyboard_video_provider.blockSignals(True)
        self.storyboard_video_provider.clear()
        labels = {"seedance": "Seedance 2.0", "veo": "Veo 3.1", "kling": "可灵"}
        providers = []
        if self._ai_manager is not None:
            try:
                providers = self._ai_manager.registry.by_capability("text_to_video")
            except Exception:
                providers = []
        for provider in providers:
            self.storyboard_video_provider.addItem(
                labels.get(provider.name, provider.name), provider.name)
        preferred = current or "seedance"
        index = self.storyboard_video_provider.findData(preferred)
        if index < 0 and current:
            self.storyboard_video_provider.addItem(
                f"{labels.get(str(current), str(current))}（当前不可用）", current)
            index = self.storyboard_video_provider.findData(current)
        elif index < 0 and self.storyboard_video_provider.count():
            index = 0
        self.storyboard_video_provider.setCurrentIndex(index)
        self.storyboard_video_provider.blockSignals(False)

    def refresh_resource_links(self, _kind: str = ""):
        """从 AI 资源中心重新载入人物/场景，并保留当前项目绑定。"""
        try:
            from ai.service import get_asset_db
            self._resource_db = self._resource_db or get_asset_db()
            characters = self._resource_db.list_characters(limit=5000)
            scenes = self._resource_db.list_scenes(limit=5000)
            elements = self._resource_db.list_elements(limit=5000)
        except Exception as error:
            self.visual_lock_status.setText(f"制片画布资产读取失败：{str(error)[:60]}")
            return

        bible = (self._storyboard or {}).get("visual_bible", {})
        character_id = bible.get("character_id", "") if self._storyboard else ""
        scene_id = bible.get("scene_id", "") if self._storyboard else ""
        if not character_id:
            character_id = self.storyboard_character_resource.currentData() or ""
        if not scene_id:
            scene_id = self.storyboard_scene_resource.currentData() or ""

        def fill(combo: QComboBox, items: list, selected_id: str, empty_text: str):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(empty_text, "")
            for item in items:
                if asset_is_approved(item, require_file=True):
                    suffix = f" · 主参考 v{max(1, int(getattr(item, 'version', 0) or 0))}"
                elif asset_is_approved(item, require_file=False):
                    suffix = " · 主参考文件丢失"
                else:
                    candidates = [path for path in getattr(item, "reference_images", [])
                                  if path and os.path.exists(path)]
                    suffix = (f" · 草稿（{len(candidates)}候选）"
                              if candidates else " · 仅描述")
                combo.addItem(f"{getattr(item, 'name', '未命名')}{suffix}", item.id)
            index = combo.findData(selected_id)
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(False)

        fill(self.storyboard_character_resource, characters, character_id, "不锁定角色")
        fill(self.storyboard_scene_resource, scenes, scene_id, "不锁定场景")
        self._character_resources = characters
        self._scene_resources = scenes
        self._element_resources = elements
        for card in self._shot_cards.values():
            card.set_resources(characters, scenes, elements)
        self._refresh_shot_asset_preparation_statuses()
        self._save_visual_lock()

    @staticmethod
    def _split_asset_names(values) -> list[str]:
        import re
        if isinstance(values, str):
            values = re.split(r"[、,，/&]+", values)
        elif not isinstance(values, (list, tuple)):
            values = [values] if values else []
        return list(dict.fromkeys(
            str(value).strip() for value in values if str(value).strip()))

    def _shot_asset_requirements(self, shot: dict) -> list[dict]:
        """从单镜头、导演角色表和缺失清单汇总真实素材需求。"""
        board = self._storyboard or {}
        inventory = board.get("asset_inventory", {}) or {}
        inventory_entries = []
        for bucket in ("missing", "not_ready"):
            for value in inventory.get(bucket, []) or []:
                if isinstance(value, dict):
                    copied = dict(value)
                    kind = str(copied.get("kind") or "").lower()
                    copied["kind"] = {
                        "subject": "character", "role": "character",
                        "prop": "element", "object": "element",
                    }.get(kind, kind)
                    inventory_entries.append(copied)

        character_specs = [value for value in board.get("characters", [])
                           if isinstance(value, dict)]
        requirements: dict[str, dict] = {}

        def description_for(kind: str, name: str) -> str:
            folded = str(name or "").strip().casefold()
            for entry in inventory_entries:
                if (entry.get("kind") == kind and
                        str(entry.get("name") or "").strip().casefold() == folded):
                    return str(entry.get("description") or "").strip()
            if kind == "character":
                for entry in character_specs:
                    if str(entry.get("name") or "").strip().casefold() == folded:
                        return str(entry.get("description") or "").strip()
            return ""

        def add(kind: str, asset_id: str = "", name: str = "",
                description: str = "", **metadata):
            kind = {"subject": "character", "prop": "element"}.get(kind, kind)
            if kind not in {"scene", "character", "element"}:
                return
            asset_id = str(asset_id or "").strip()
            name = str(name or "").strip()
            if not asset_id and not name:
                return
            name_key = f"{kind}:name:{name.casefold()}" if name else ""
            id_key = f"{kind}:id:{asset_id}" if asset_id else ""
            # 同一个需求经常同时来自绑定 ID 和名称清单。优先复用已有项，
            # 避免同一资产被统计两次或打开两个制作窗口。
            key = id_key or name_key
            if id_key and name_key and name_key in requirements:
                current = requirements.pop(name_key)
                current["asset_id"] = asset_id
                requirements[id_key] = current
                key = id_key
            elif name_key:
                matched_key = next((existing_key for existing_key, value in requirements.items()
                                    if value.get("kind") == kind and
                                    str(value.get("name") or "").casefold() == name.casefold()), "")
                if matched_key:
                    key = matched_key
            current = requirements.setdefault(key, {
                "kind": kind, "asset_id": asset_id, "name": name,
                "description": description or description_for(kind, name),
            })
            if not current.get("name") and name:
                current["name"] = name
            if not current.get("description"):
                current["description"] = description or description_for(kind, name)
            current.update({key: value for key, value in metadata.items() if value})

        scene_id = str(shot.get("scene_asset_id") or shot.get("scene_id") or "")
        scene_name = str(shot.get("scene_name") or "").strip()
        add("scene", scene_id, scene_name,
            description_for("scene", scene_name) or str(shot.get("scene") or ""))

        for binding in shot.get("character_bindings", []) or []:
            if isinstance(binding, dict):
                add("character", binding.get("asset_id", ""), binding.get("name", ""),
                    role=binding.get("role", "subject"),
                    outfit_state=binding.get("outfit_state", ""),
                    appearance_state=binding.get("appearance_state", ""))
        character_names = self._split_asset_names(shot.get("character_names", []))
        character_names = list(dict.fromkeys(
            character_names + self._split_asset_names(shot.get("character", ""))))
        for name in character_names:
            spec = next((value for value in character_specs
                         if str(value.get("name") or "").strip().casefold() ==
                         name.casefold()), {})
            add("character", spec.get("asset_id", ""), name,
                str(spec.get("description") or ""))

        for binding in shot.get("element_bindings", []) or []:
            if isinstance(binding, dict):
                add("element", binding.get("asset_id", ""), binding.get("name", ""),
                    mode=binding.get("mode", ""),
                    placement=binding.get("placement", ""))
        for name in self._split_asset_names(shot.get("element_names", [])):
            add("element", name=name)

        searchable = " ".join(str(shot.get(key) or "") for key in (
            "scene", "scene_name", "character", "action", "image_prompt",
            "video_prompt", "voiceover", "sound"))
        searchable += " " + " ".join(character_names)
        searchable += " " + " ".join(
            self._split_asset_names(shot.get("element_names", [])))
        folded_search = searchable.casefold()
        for entry in inventory_entries:
            name = str(entry.get("name") or "").strip()
            if name and name.casefold() in folded_search:
                add(entry.get("kind", ""), entry.get("asset_id", ""), name,
                    str(entry.get("description") or ""))

        # 兼容旧分镜：没有 scene_name 时，若全片只缺一个场景，就把它归给本镜头。
        if not any(value["kind"] == "scene" for value in requirements.values()):
            scene_entries = [value for value in inventory_entries
                             if value.get("kind") == "scene"]
            if len(scene_entries) == 1:
                entry = scene_entries[0]
                add("scene", entry.get("asset_id", ""), entry.get("name", ""),
                    str(entry.get("description") or shot.get("scene") or ""))
        values = list(requirements.values())
        if self._resource_db is None:
            return values
        deduped: dict[str, dict] = {}
        for requirement in values:
            item = self._find_requirement_asset(requirement)
            if item is not None:
                key = f"{requirement['kind']}:id:{getattr(item, 'id', '')}"
                requirement["asset_id"] = str(getattr(item, "id", ""))
                requirement["name"] = str(
                    requirement.get("name") or getattr(item, "name", ""))
            else:
                key = (f"{requirement['kind']}:id:{requirement.get('asset_id')}"
                       if requirement.get("asset_id") else
                       f"{requirement['kind']}:name:"
                       f"{str(requirement.get('name') or '').casefold()}")
            current = deduped.setdefault(key, requirement)
            if not current.get("description") and requirement.get("description"):
                current["description"] = requirement["description"]
        return list(deduped.values())

    def _find_requirement_asset(self, requirement: dict):
        if self._resource_db is None:
            return None
        kind = requirement["kind"]
        item_id = str(requirement.get("asset_id") or "")
        if item_id:
            item = getattr(self._resource_db, f"get_{kind}")(item_id)
            if item:
                return item
        name = str(requirement.get("name") or "").strip().casefold()
        if not name:
            return None
        items = getattr(self._resource_db, f"list_{'characters' if kind == 'character' else kind + 's'}")(
            limit=5000)
        return next((item for item in items
                     if str(getattr(item, "name", "")).strip().casefold() == name), None)

    @staticmethod
    def _asset_generation_prompt(kind: str, name: str, description: str) -> str:
        if kind == "scene":
            return (
                f"影视场景母版，无人物空镜。场景名称：{name}。{description}。"
                "固定空间结构、门窗位置、主要家具和道具布局、时间、主光方向与综合色调；"
                "构图清楚、电影写实、无人物、无文字、无水印。")
        if kind == "character":
            return (
                f"影视主体设定母版。主体名称：{name}。{description}。"
                "只展示一个主体，全身完整，正面略带3/4角度，中性站姿，"
                "固定头部与身体轮廓、器官数量、比例、材质、颜色、服装和配件；"
                "干净中性背景，无文字、无水印。")
        return (
            f"影视指定元素母版。元素名称：{name}。{description}。"
            "只展示单个完整物体，正面产品视图，固定轮廓、材质、颜色、按钮和结构细节；"
            "边缘清楚、无遮挡、干净中性背景、无额外物体、无水印。")

    def _create_requirement_asset(self, requirement: dict):
        import time
        import uuid
        from ai.assets import Character, Scene, Element

        kind = requirement["kind"]
        name = str(requirement.get("name") or f"未命名{kind}").strip()
        description = str(requirement.get("description") or "").strip()
        prompt = self._asset_generation_prompt(kind, name, description)
        if kind == "scene":
            item = Scene(id=uuid.uuid4().hex, name=name,
                         description=description, seedream_prompt=prompt)
        elif kind == "character":
            identity = f"{name} {description}".lower()
            entity_type = (
                "robot" if any(word in identity for word in ("机器人", "机械", "robot"))
                else "animal" if any(word in identity for word in ("动物", "猫", "狗", "鸟"))
                else "monster" if any(word in identity for word in ("怪物", "生物", "monster"))
                else "human")
            item = Character(
                id=uuid.uuid4().hex, name=name, entity_type=entity_type,
                description=description, design_notes=description,
                seedream_prompt=prompt, created_at=time.time(), updated_at=time.time())
        else:
            identity = f"{name} {description}".lower()
            element_type = (
                "wallpaper" if "壁纸" in identity else
                "logo" if "logo" in identity or "标志" in identity else
                "ui" if "界面" in identity or "ui" in identity else
                "product" if "产品" in identity or "包装" in identity else "prop")
            default_mode = "exact" if element_type in {
                "wallpaper", "logo", "ui", "product"} else "reference"
            item = Element(
                id=uuid.uuid4().hex, name=name, element_type=element_type,
                description=description, seedream_prompt=prompt,
                placement_hint=str(requirement.get("placement") or ""),
                default_mode=default_mode, created_at=time.time(), updated_at=time.time())
        getattr(self._resource_db, f"save_{kind}")(item)
        return item

    def _bind_requirement_to_shot(self, shot: dict, requirement: dict, item):
        kind = requirement["kind"]
        item_id = str(getattr(item, "id", ""))
        version = int(getattr(item, "version", 0) or 0)
        name = str(getattr(item, "name", requirement.get("name", "")))
        if kind == "scene":
            shot["scene_asset_id"] = item_id
            shot["scene_id"] = item_id
            shot["scene_name"] = name
            shot["scene_version"] = version
        elif kind == "character":
            bindings = [value for value in shot.get("character_bindings", [])
                        if isinstance(value, dict)]
            binding = next((value for value in bindings
                            if value.get("asset_id") == item_id), None)
            if binding is None:
                bindings.append({
                    "asset_id": item_id, "name": name, "version": version,
                    "role": requirement.get("role", "subject"),
                    "outfit_state": requirement.get("outfit_state", ""),
                    "appearance_state": requirement.get("appearance_state", ""),
                    "required": True,
                })
            else:
                binding.update({"name": name, "version": version})
            shot["character_bindings"] = bindings
            names = self._split_asset_names(shot.get("character_names", []))
            shot["character_names"] = list(dict.fromkeys(names + [name]))
        else:
            bindings = [value for value in shot.get("element_bindings", [])
                        if isinstance(value, dict)]
            binding = next((value for value in bindings
                            if value.get("asset_id") == item_id), None)
            if binding is None:
                bindings.append({
                    "asset_id": item_id, "name": name, "version": version,
                    "mode": requirement.get("mode") or
                            getattr(item, "default_mode", "reference"),
                    "placement": requirement.get("placement") or
                                 getattr(item, "placement_hint", ""),
                    "required": True,
                })
            else:
                binding.update({"name": name, "version": version})
            shot["element_bindings"] = bindings
            names = self._split_asset_names(shot.get("element_names", []))
            shot["element_names"] = list(dict.fromkeys(names + [name]))
        sync_legacy_bindings(shot)

    def _mark_inventory_asset_created(self, requirement: dict, item):
        inventory = (self._storyboard or {}).setdefault("asset_inventory", {})
        missing = list(inventory.get("missing", []) or [])
        not_ready = list(inventory.get("not_ready", []) or [])
        folded = str(requirement.get("name") or "").strip().casefold()
        moved = None
        remaining = []
        for entry in missing:
            entry_kind = ""
            if isinstance(entry, dict):
                raw_kind = str(entry.get("kind") or "").lower()
                entry_kind = {
                    "subject": "character", "role": "character",
                    "prop": "element", "object": "element",
                }.get(raw_kind, raw_kind)
            if (isinstance(entry, dict) and
                    entry_kind == requirement["kind"] and
                    str(entry.get("name") or "").strip().casefold() == folded):
                moved = dict(entry)
            else:
                remaining.append(entry)
        if moved is not None:
            moved["asset_id"] = str(getattr(item, "id", ""))
            not_ready.append(moved)
            inventory["missing"] = remaining
            inventory["not_ready"] = not_ready

    def _open_shot_asset_preparation_dialog(self, shot: dict, entries: list[dict],
                                            ready_count: int = 0):
        shot_id = str(shot.get("id", ""))
        return self._open_asset_preparation_dialog(
            key=f"shot:{shot_id}",
            shot_number=int(shot.get("number", 0) or 0),
            entries=entries,
            ready_count=ready_count,
        )

    def _open_asset_preparation_dialog(self, key: str, shot_number: int | None,
                                       entries: list[dict], ready_count: int = 0,
                                       context_title: str = ""):
        from ai.ui.resource_center import ShotAssetPreparationDialog
        existing = self._shot_asset_studios.get(key)
        try:
            if existing is not None and existing.isVisible():
                existing.showNormal()
                existing.raise_()
                existing.activateWindow()
                return existing
        except RuntimeError:
            self._shot_asset_studios.pop(key, None)

        dialog = ShotAssetPreparationDialog(
            shot_number, entries, self._resource_db,
            aspect=self.director_ratio.currentText(), ready_count=ready_count,
            parent=self, context_title=context_title)
        dialog.assetSaved.connect(self._on_prepared_asset_saved)
        self._shot_asset_studios[key] = dialog

        def finished(_result=0, key=key):
            self._shot_asset_studios.pop(key, None)
            self.refresh_resource_links("")

        dialog.finished.connect(finished)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return dialog

    def _prepare_all_storyboard_assets(self, *_):
        """识别全片素材、去重、自动绑定，并在一个窗口中让用户确认生成。"""
        shots = (self._storyboard or {}).get("shots", [])
        if not shots:
            QMessageBox.information(
                self, "还没有分镜", "请先在“写故事”页面生成分镜。")
            return
        if self._resource_db is None:
            try:
                from ai.service import get_asset_db
                self._resource_db = get_asset_db()
            except Exception as error:
                self.status_msg.emit(f"资产库不可用：{error}", "error")
                return

        unique_assets: dict[tuple[str, str], dict] = {}
        ready_keys: set[tuple[str, str]] = set()
        created_count = 0
        requirement_count = 0
        for shot in shots:
            for requirement in self._shot_asset_requirements(shot):
                requirement_count += 1
                item = self._find_requirement_asset(requirement)
                if item is None:
                    item = self._create_requirement_asset(requirement)
                    requirement["asset_id"] = str(getattr(item, "id", ""))
                    self._mark_inventory_asset_created(requirement, item)
                    created_count += 1
                self._bind_requirement_to_shot(shot, requirement, item)
                key = (requirement["kind"], str(getattr(item, "id", "")))
                if key in unique_assets or key in ready_keys:
                    continue
                if asset_is_approved(item, require_file=True):
                    ready_keys.add(key)
                    continue
                candidates = [
                    path for path in getattr(item, "reference_images", []) or []
                    if path and os.path.exists(path)]
                unique_assets[key] = {
                    "item": item,
                    "kind": requirement["kind"],
                    "generate": not bool(candidates),
                }

        rebuild_continuity(self._storyboard or {})
        self.refresh_resource_links("")
        self.storyboard_changed.emit()
        if requirement_count == 0:
            QMessageBox.information(
                self, "没有识别到素材",
                "当前分镜没有写明场景、主体或元素。请返回“写故事”补充内容，"
                "或在镜头的“更多设置”中手动选择。")
            return
        if not unique_assets:
            QMessageBox.information(
                self, "全部素材已经准备好",
                f"全片需要的 {len(ready_keys)} 项场景、主体和元素都有定稿参考图，"
                "可以直接点击“生成所有缺失画面”。")
            return

        entries = list(unique_assets.values())
        self._open_asset_preparation_dialog(
            key="storyboard:all",
            shot_number=None,
            entries=entries,
            ready_count=len(ready_keys),
            context_title="全部镜头",
        )
        pending = sum(1 for entry in entries if entry.get("generate"))
        review = len(entries) - pending
        details = [f"已就绪 {len(ready_keys)} 项", f"待生成 {pending} 项"]
        if review:
            details.append(f"待定稿 {review} 项")
        if created_count:
            details.append(f"新建资产 {created_count} 项")
        self.status_msg.emit(
            "全片素材已识别并自动绑定：" + " · ".join(details), "success")

    def _on_prepared_asset_saved(self, kind: str, _asset_id: str):
        updated_shots = self._sync_prepared_asset_bindings(kind, _asset_id)
        if self._storyboard:
            rebuild_continuity(self._storyboard)
        self.refresh_resource_links(kind)
        self._refresh_shot_asset_preparation_statuses()
        if self._active_shot_id in updated_shots:
            self._select_storyboard_shot(self._active_shot_id)
        self.storyboard_changed.emit()

    def _sync_prepared_asset_bindings(self, kind: str, asset_id: str) -> list[str]:
        """资产生成/定稿后，把最新名称和版本同步到所有已连接镜头。"""
        if self._resource_db is None or kind not in {"scene", "character", "element"}:
            return []
        item = getattr(self._resource_db, f"get_{kind}")(asset_id)
        if item is None:
            return []
        version = int(getattr(item, "version", 0) or 0)
        name = str(getattr(item, "name", "") or "")
        changed_shots = []
        for shot in (self._storyboard or {}).get("shots", []):
            changed = False
            if kind == "scene":
                if (shot.get("scene_asset_id") or shot.get("scene_id")) == asset_id:
                    shot["scene_asset_id"] = asset_id
                    shot["scene_id"] = asset_id
                    shot["scene_name"] = name
                    shot["scene_version"] = version
                    changed = True
            else:
                key = "character_bindings" if kind == "character" else "element_bindings"
                for binding in shot.get(key, []) or []:
                    if (isinstance(binding, dict) and
                            binding.get("asset_id") == asset_id):
                        binding["name"] = name
                        binding["version"] = version
                        changed = True
                if changed:
                    names_key = "character_names" if kind == "character" else "element_names"
                    names = self._split_asset_names(shot.get(names_key, []))
                    shot[names_key] = list(dict.fromkeys(names + ([name] if name else [])))
            if changed:
                sync_legacy_bindings(shot)
                changed_shots.append(str(shot.get("id", "")))
        return changed_shots

    def _prepare_assets_for_shot(self, shot_id: str):
        shot = self._find_shot(shot_id)
        if not shot:
            return
        if self._resource_db is None:
            try:
                from ai.service import get_asset_db
                self._resource_db = get_asset_db()
            except Exception as error:
                self.status_msg.emit(f"资产库不可用：{error}", "error")
                return
        requirements = self._shot_asset_requirements(shot)
        if not requirements:
            QMessageBox.information(
                self, "没有识别到素材",
                "当前分镜没有结构化的场景、主体或元素名称。请重新生成导演分镜，"
                "或在“更多设置”中手动选择素材。")
            return

        ready_names = []
        missing_names = []
        review_names = []
        preparation_entries = []
        created_count = 0
        processed_assets = set()
        for requirement in requirements:
            item = self._find_requirement_asset(requirement)
            if item is None:
                item = self._create_requirement_asset(requirement)
                requirement["asset_id"] = item.id
                self._mark_inventory_asset_created(requirement, item)
                created_count += 1
            asset_key = (requirement["kind"], str(getattr(item, "id", "")))
            if asset_key in processed_assets:
                self._bind_requirement_to_shot(shot, requirement, item)
                continue
            processed_assets.add(asset_key)
            self._bind_requirement_to_shot(shot, requirement, item)
            label = {
                "scene": "场景", "character": "主体", "element": "元素",
            }[requirement["kind"]] + f"“{item.name}”"
            if asset_is_approved(item, require_file=True):
                ready_names.append(label)
                continue
            candidates = [path for path in getattr(item, "reference_images", []) or []
                          if path and os.path.exists(path)]
            preparation_entries.append({
                "item": item,
                "kind": requirement["kind"],
                "generate": not bool(candidates),
            })
            if candidates:
                review_names.append(label)
            else:
                missing_names.append(label)

        rebuild_continuity(self._storyboard or {})
        self.refresh_resource_links("")
        self._select_storyboard_shot(shot_id)
        self.storyboard_changed.emit()
        parts = []
        if ready_names:
            parts.append(f"跳过已就绪 {len(ready_names)} 项")
        if missing_names:
            parts.append(f"待确认生成 {len(missing_names)} 项")
        if review_names:
            parts.append(f"已有参考待定稿 {len(review_names)} 项")
        if created_count:
            parts.append(f"新建资产 {created_count} 项")
        self.status_msg.emit("本镜头素材：" + " · ".join(parts), "success")
        if preparation_entries:
            self._open_shot_asset_preparation_dialog(
                shot, preparation_entries, ready_count=len(ready_names))
            self.status_msg.emit(
                "已识别并绑定素材；请在合并窗口确认设置后再开始生成", "success")
        else:
            QMessageBox.information(
                self, "素材已经准备好",
                "当前镜头需要的场景、主体和元素都已有主参考，可以直接生成画面。")

    def _refresh_shot_asset_preparation_statuses(self):
        if self._resource_db is None:
            return
        for shot_id, card in self._shot_cards.items():
            shot = self._find_shot(shot_id)
            requirements = self._shot_asset_requirements(shot or {})
            ready = 0
            for requirement in requirements:
                item = self._find_requirement_asset(requirement)
                if item is not None and asset_is_approved(item, require_file=True):
                    ready += 1
            total = len(requirements)
            problems = self._shot_readiness_problems(shot or {})
            if not problems:
                card.set_asset_preparation_status("素材已就绪 ✓", True)
            elif total:
                card.set_asset_preparation_status(f"准备缺失素材 {ready}/{total}")
            else:
                card.set_asset_preparation_status("识别并准备素材")

    @staticmethod
    def _asset_reference_paths(item) -> list[str]:
        path = approved_asset_path(item)
        return [path] if path and os.path.exists(path) else []

    def _repair_stale_shot_bindings(self, shot: dict) -> bool:
        """有真实绑定时移除 AI 导演遗留的未知占位 ID。"""
        if self._resource_db is None:
            return False
        sync_legacy_bindings(shot)
        changed = False

        character_bindings = [
            value for value in shot.get("character_bindings", [])
            if isinstance(value, dict) and value.get("asset_id")]
        valid_characters = [
            value for value in character_bindings
            if self._resource_db.get_character(value["asset_id"]) is not None]
        if valid_characters and len(valid_characters) != len(character_bindings):
            shot["character_bindings"] = valid_characters
            ids = [value["asset_id"] for value in valid_characters]
            shot["character_id"] = ids[0]
            shot["character_ids"] = ids[1:]
            changed = True

        element_bindings = [
            value for value in shot.get("element_bindings", [])
            if isinstance(value, dict) and value.get("asset_id")]
        valid_elements = [
            value for value in element_bindings
            if self._resource_db.get_element(value["asset_id"]) is not None]
        if valid_elements and len(valid_elements) != len(element_bindings):
            shot["element_bindings"] = valid_elements
            ids = [value["asset_id"] for value in valid_elements]
            shot["element_id"] = ids[0]
            shot["element_ids"] = ids[1:]
            changed = True

        if changed:
            sync_legacy_bindings(shot)
        return changed

    def _shot_readiness_problems(self, shot: dict) -> list[str]:
        """生成付费任务前的资产门槛；空镜不强制绑定主体。"""
        if self._resource_db is None:
            return ["制片画布资产库不可用"]
        sync_legacy_bindings(shot)
        bible = (self._storyboard or {}).get("visual_bible", {})
        problems = []
        scene_id = (shot.get("scene_asset_id") or shot.get("scene_id")
                    or bible.get("scene_id", ""))
        scene = self._resource_db.get_scene(scene_id) if scene_id else None
        if not scene:
            problems.append("未绑定场景")
        elif not self._asset_reference_paths(scene):
            problems.append(f"场景“{scene.name}”还没有选定参考图")

        primary_character = shot.get("character_id") or bible.get("character_id", "")
        character_ids = ([primary_character] if primary_character else []) + list(
            shot.get("character_ids", []) or [])
        required_character_names = self._split_asset_names(
            shot.get("character_names", []))
        if not required_character_names and str(shot.get("character") or "").strip():
            required_character_names = self._split_asset_names(shot.get("character"))
        if required_character_names and not character_ids:
            problems.append(
                "未绑定主体：" + "、".join(required_character_names))
        bound_character_names = []
        for item_id in dict.fromkeys(item for item in character_ids if item):
            item = self._resource_db.get_character(item_id)
            if not item:
                problems.append(f"主体资源不存在：{item_id}")
            else:
                bound_character_names.append(str(item.name or "").strip().casefold())
                if not self._asset_reference_paths(item):
                    problems.append(f"主体“{item.name}”没有主参考图")
        for name in required_character_names:
            if (character_ids and str(name).strip().casefold() not in
                    bound_character_names):
                problems.append(f"拍摄合同中的主体“{name}”未绑定")

        required_element_names = self._split_asset_names(
            shot.get("element_names", []))
        bound_element_names = []
        for binding in shot.get("element_bindings", []):
            if not isinstance(binding, dict) or not binding.get("asset_id"):
                continue
            item = self._resource_db.get_element(binding["asset_id"])
            if not item:
                problems.append(f"元素资源不存在：{binding['asset_id']}")
            else:
                bound_element_names.append(str(item.name or "").strip().casefold())
                if (binding.get("required", True) and
                        not self._asset_reference_paths(item)):
                    problems.append(f"元素“{item.name}”还没有选定参考图")
        if required_element_names and not bound_element_names:
            problems.append("未绑定指定元素：" + "、".join(required_element_names))
        else:
            for name in required_element_names:
                if str(name).strip().casefold() not in bound_element_names:
                    problems.append(f"拍摄合同中的元素“{name}”未绑定")
        return problems

    def _storyboard_readiness(self) -> tuple[int, int, list[str]]:
        shots = (self._storyboard or {}).get("shots", [])
        missing = []
        ready_count = 0
        repaired = False
        for shot in shots:
            repaired = self._repair_stale_shot_bindings(shot) or repaired
            problems = self._shot_readiness_problems(shot)
            if problems:
                missing.append(
                    f"镜头{int(shot.get('number', 0)):02d}：" + "；".join(problems))
            else:
                ready_count += 1
        if repaired and self._storyboard:
            rebuild_continuity(self._storyboard)
            QTimer.singleShot(0, self.storyboard_changed.emit)
        return ready_count, len(shots), missing

    def _save_visual_lock(self, *_):
        provider = self.storyboard_image_provider.currentData() or ""
        video_provider = self.storyboard_video_provider.currentData() or ""
        character_id = self.storyboard_character_resource.currentData() or ""
        scene_id = self.storyboard_scene_resource.currentData() or ""
        if self._storyboard is not None:
            bible = self._storyboard.setdefault("visual_bible", {})
            bible.update({
                "image_provider": provider,
                "video_provider": video_provider,
                "character_id": character_id,
                "scene_id": scene_id,
            })
            # `visual_bible` describes the creative lock, while
            # `production_models` is the executable routing contract shared
            # with the production canvas.  Keep both in sync so choosing
            # Seedream here cannot become GPT Image after sending to
            # production.
            models = self._storyboard.setdefault("production_models", {})
            if not isinstance(models, dict):
                models = {}
                self._storyboard["production_models"] = models
            models.update({
                "image_provider": provider,
                "video_provider": video_provider,
            })

        if self._storyboard and self._storyboard.get("shots") and self._resource_db:
            ready_count, total, missing = self._storyboard_readiness()
            if ready_count == total:
                self.visual_lock_status.setText(
                    f"所有镜头的参考素材已准备好 {ready_count}/{total} ✓")
                self.visual_lock_status.setStyleSheet("color:#73c69c;font-size:10px;")
                self.btn_prepare_all_assets.setText("素材已准备好 ✓")
            else:
                self.visual_lock_status.setText(
                    f"已准备 {ready_count}/{total} · 还有 {len(missing)} 个镜头需要场景或主体参考")
                self.visual_lock_status.setStyleSheet("color:#d1a867;font-size:10px;")
                self.btn_prepare_all_assets.setText("智能准备全部镜头")
            return

        locked = []
        reference_count = 0
        scene_reference_count = 0
        character_reference_count = 0
        if self._resource_db is not None:
            for kind, item_id, label in (
                    ("character", character_id, "角色"),
                    ("scene", scene_id, "场景")):
                if not item_id:
                    continue
                item = getattr(self._resource_db, f"get_{kind}")(item_id)
                if item:
                    refs = self._asset_reference_paths(item)
                    reference_count += len(refs)
                    if kind == "scene":
                        scene_reference_count = len(refs)
                    else:
                        character_reference_count = len(refs)
                    locked.append(f"{label}：{getattr(item, 'name', '未命名')}")
        if not scene_id:
            self.visual_lock_status.setText("还没有场景参考 · 点击“智能准备全部镜头”")
            self.visual_lock_status.setStyleSheet("color:#d1a867;font-size:10px;")
        elif scene_reference_count == 0:
            self.visual_lock_status.setText("场景已创建，但还需要选定一张参考图")
            self.visual_lock_status.setStyleSheet("color:#d1a867;font-size:10px;")
        elif not character_id:
            self.visual_lock_status.setText("场景参考已准备 ✓ · 如果有角色，再准备主体参考")
            self.visual_lock_status.setStyleSheet("color:#67d8a2;font-size:10px;")
        elif character_reference_count == 0:
            self.visual_lock_status.setText("场景已准备 ✓ · 主体还需要选定一张参考图")
            self.visual_lock_status.setStyleSheet("color:#d1a867;font-size:10px;")
        elif locked:
            suffix = f" · 共 {reference_count} 张参考图" if reference_count else " · 仅固定描述"
            self.visual_lock_status.setText(
                "场景和主体参考已准备 ✓" + suffix)
            self.visual_lock_status.setStyleSheet("color:#73c69c;font-size:10px;")
        else:
            self.visual_lock_status.setText("还没有选择场景参考")
            self.visual_lock_status.setStyleSheet("color:#777783;font-size:10px;")

    def _apply_visual_lock(self, shot: dict, prompt: str,
                           primary_reference: str = "",
                           attach_asset_refs: bool = True,
                           applied_exact_element_ids: list[str] | None = None,
                           reference_budget: int = 6,
                           ) -> tuple[str, list[str], dict]:
        """构造带类型、稳定顺序和预算校验的镜头参考图清单。"""
        bible = (self._storyboard or {}).get("visual_bible", {})
        if self._resource_db is None:
            try:
                from ai.service import get_asset_db
                self._resource_db = get_asset_db()
            except Exception:
                return prompt, [], {"missing": ["AI制片画布资产库不可用"], "entries": []}

        blocks = []
        candidates = []
        missing = []
        exact_element_ids = []
        applied_exact = set(applied_exact_element_ids or [])
        sequence = 0

        def add_candidate(path: str, label: str, priority: int,
                          critical: bool = False, asset_id: str = "",
                          role: str = "reference", name: str = "",
                          version: int = 0, weight: float = 1.0,
                          mode: str = "reference"):
            nonlocal sequence
            if path and os.path.exists(path):
                candidates.append({
                    "path": path, "label": label, "priority": priority,
                    "critical": critical, "asset_id": asset_id,
                    "role": role, "name": name, "version": int(version or 0),
                    "weight": float(weight or 1.0), "mode": mode,
                    "required": bool(critical),
                    "order": sequence,
                })
                sequence += 1

        if primary_reference and os.path.exists(primary_reference):
            add_candidate(primary_reference, "当前镜头底图（保持构图、姿态和透视）",
                          0, True, "shot_base", "composition", "当前镜头底图",
                          weight=1.25)

        # 1. 场景只发一个最匹配的机位。已有镜头底图时，底图本身已经包含
        # 场景构图，不再额外发送场景母版与它竞争。
        scene_id = (shot.get("scene_asset_id") or shot.get("scene_id")
                    or bible.get("scene_id", ""))
        if scene_id:
            scene = self._resource_db.get_scene(scene_id)
            if scene:
                fixed = scene.seedream_prompt or scene.description
                scene_version = max(1, int(getattr(scene, "version", 0) or 0))
                blocks.append(
                    f"场景一致性锁：{scene.name}（已批准 v{scene_version}）。固定环境：{fixed}。"
                    "必须保持空间结构、家具陈设、主光方向、时间和整体色调一致。")
                scene_master = approved_asset_path(scene)
                if scene_master and os.path.exists(scene_master):
                    views = getattr(scene, "reference_views", {}) or {}
                    role = _scene_camera_role(shot.get("camera_slot"))
                    role_path = views.get(role, "") if role else ""
                    chosen_scene = (role_path if role_path and os.path.exists(role_path)
                                    else scene_master)
                    if not primary_reference:
                        label = (
                            f"场景固定机位：{scene.name}·{shot.get('camera_slot')}"
                            if chosen_scene == role_path else
                            f"场景选定参考图：{scene.name}·v{scene_version}")
                        add_candidate(
                            chosen_scene, label, 10, True, scene_id,
                            "scene", scene.name, scene_version, 1.0)
                else:
                    missing.append(f"场景“{scene.name}”没有主参考图")
            else:
                missing.append(f"场景资源不存在：{scene_id}")
        else:
            missing.append("本镜头未绑定场景")

        # 2. 每个主体只发送一张最匹配的身份参考。多角度图用于建立资产，
        # 不应在每个镜头全部塞入，否则人物与视角会互相竞争。
        character_binding_map = {
            item.get("asset_id"): item
            for item in shot.get("character_bindings", [])
            if isinstance(item, dict) and item.get("asset_id")}
        primary_character = shot.get("character_id") or bible.get("character_id", "")
        character_ids = ([primary_character] if primary_character else []) + list(
            shot.get("character_ids", []) or [])
        character_ids = [item_id for item_id in dict.fromkeys(character_ids) if item_id]
        for subject_index, character_id in enumerate(character_ids, 1):
            character = self._resource_db.get_character(character_id)
            if not character:
                missing.append(f"主体资源不存在：{character_id}")
                continue
            fixed = character.seedream_prompt or character.description
            design = getattr(character, "design_notes", "") or ""
            entity_type = getattr(character, "entity_type", "other") or "other"
            character_version = max(1, int(getattr(character, "version", 0) or 0))
            binding = character_binding_map.get(character_id, {})
            outfit_state = str(binding.get("outfit_state") or "固定定稿服装")
            appearance_state = str(binding.get("appearance_state") or "常规状态")
            role = str(binding.get("role") or "subject")
            blocks.append(
                f"主体{subject_index}一致性锁：{character.name}"
                f"（类型：{entity_type}；已批准 v{character_version}）。"
                f"固定设定：{fixed}。不可漂移特征：{design}。"
                f"本镜头角色功能：{role}；服装状态：{outfit_state}；外观状态：{appearance_state}。"
                "必须保持头部与身体轮廓、器官数量、五官或面部结构、材质、花纹、"
                "服装配色、配件和身体比例一致；不得与其他主体交换特征。")
            views = getattr(character, "reference_views", {}) or {}
            master_path = approved_asset_path(character)
            if not master_path or not os.path.exists(master_path):
                missing.append(f"主体“{character.name}”没有主参考图")
                continue
            direction_hint = " ".join(str(shot.get(key) or "") for key in (
                "shot_size", "camera", "blocking", "screen_direction", "scene"))
            direction_lower = direction_hint.lower()
            preferred_roles = []
            if any(value in direction_lower for value in
                   ("背面", "背对", "back view", "from behind")):
                preferred_roles.append(("back", "背面"))
            elif any(value in direction_lower for value in
                     ("侧面", "侧脸", "profile", "side view")):
                preferred_roles.append(("side", "侧面"))
            elif any(value in direction_lower for value in
                     ("3/4", "three-quarter", "three quarter")):
                preferred_roles.append(("three_quarter", "3/4视角"))
            elif any(value in direction_lower for value in
                     ("正面", "front view", "facing camera")):
                preferred_roles.append(("front", "正面"))
            chosen_path, chosen_view = master_path, "选定参考图"
            for role_key, role_label in preferred_roles:
                candidate_path = views.get(role_key, "")
                if candidate_path and os.path.exists(candidate_path):
                    chosen_path, chosen_view = candidate_path, role_label
                    break
            add_candidate(
                chosen_path,
                f"主体{subject_index}身份参考：{character.name}·v{character_version}·{chosen_view}",
                20 + subject_index, True, character_id, "character",
                character.name, character_version, 1.2)

        # 3. 元素也支持多个。主元素可覆盖默认模式；附加元素使用资源默认模式/位置。
        element_binding_map = {
            item.get("asset_id"): item
            for item in shot.get("element_bindings", [])
            if isinstance(item, dict) and item.get("asset_id")}
        primary_element = shot.get("element_id", "")
        element_ids = ([primary_element] if primary_element else []) + list(
            shot.get("element_ids", []) or [])
        element_ids = [item_id for item_id in dict.fromkeys(element_ids) if item_id]
        for element_index, element_id in enumerate(element_ids, 1):
            element = self._resource_db.get_element(element_id)
            if not element:
                missing.append(f"指定元素资源不存在：{element_id}")
                continue
            binding = element_binding_map.get(element_id, {})
            if element_id == primary_element:
                placement = (binding.get("placement") or shot.get("element_placement") or
                             getattr(element, "placement_hint", "") or "指定区域")
                mode = (binding.get("mode") or shot.get("element_mode") or
                        getattr(element, "default_mode", "exact"))
            else:
                placement = (binding.get("placement") or
                             getattr(element, "placement_hint", "") or "指定区域")
                mode = (binding.get("mode") or
                        getattr(element, "default_mode", "exact") or "exact")
            valid_master = approved_asset_path(element)
            if valid_master and not os.path.exists(valid_master):
                valid_master = ""
            if not valid_master:
                missing.append(f"指定元素“{element.name}”没有选定参考图")
                continue
            if mode == "reference":
                element_version = max(1, int(getattr(element, "version", 0) or 0))
                element_prompt = (getattr(element, "seedream_prompt", "") or
                                  element.description)
                blocks.append(
                    f"指定元素{element_index}：{element.name}（已批准 v{element_version}）。"
                    f"固定描述：{element_prompt}。"
                    f"必须出现在{placement}，不得与其他元素混淆。")
                add_candidate(
                    valid_master,
                    f"指定元素{element_index}参考图：{element.name}·v{element_version}",
                    28 + element_index, True, element_id, "element",
                    element.name, element_version, 1.0, mode)
            else:
                exact_element_ids.append(element_id)
                if element_id in applied_exact:
                    blocks.append(
                        f"精确元素{element_index}“{element.name}”已经植入输入底图的{placement}。"
                        "必须逐像素保持其可见内容、文字、Logo、UI、颜色和边界，不得擦除、"
                        "重画、替换或变形；只允许跟随承载平面运动。")
                else:
                    blocks.append(
                        f"精确元素{element_index}占位：最终将在{placement}植入“{element.name}”。"
                        "此阶段不要绘制其具体Logo、文字、壁纸或UI内容；必须生成无遮挡、"
                        "边界完整、四角清楚的空白承载平面，供后期透视/跟踪植入。")

        # 同一路径只发送一次；排序后再编号，Prompt索引不会与真实请求错位。
        candidates.sort(key=lambda item: (item["priority"], item["order"]))
        unique = []
        by_path = {}
        for entry in candidates:
            existing = by_path.get(entry["path"])
            if existing:
                if entry["label"] not in existing["label"]:
                    existing["label"] += f" / {entry['label']}"
                existing["critical"] = existing["critical"] or entry["critical"]
                continue
            copied = dict(entry)
            unique.append(copied)
            by_path[copied["path"]] = copied

        if attach_asset_refs:
            # 云端多图模型虽允许更多输入，但镜头生产中超过六张后，场景、身份、
            # 道具的注意力会明显互相稀释。宁可显式报预算不足，也不静默抽卡。
            safe_budget = max(1, int(reference_budget or 6))
            sent_entries = unique[:safe_budget]
            omitted_entries = unique[safe_budget:]
        else:
            sent_entries = [entry for entry in unique
                            if entry.get("asset_id") == "shot_base"][:1]
            omitted_entries = []
        refs = [entry["path"] for entry in sent_entries]
        critical_omitted = [entry["label"] for entry in omitted_entries
                            if entry.get("critical")]
        continuity_bits = []
        if shot.get("camera_slot"):
            continuity_bits.append(f"固定机位槽：{shot['camera_slot']}")
        if shot.get("screen_direction"):
            continuity_bits.append(f"屏幕方向：{shot['screen_direction']}")
        if shot.get("blocking"):
            continuity_bits.append(f"场面调度：{shot['blocking']}")
        if shot.get("continuity_group"):
            continuity_bits.append(f"连续场景组：{shot['continuity_group']}")
        if continuity_bits:
            blocks.append("连续性约束：" + "；".join(continuity_bits) + "。")

        if blocks:
            prompt = f"{prompt}\n\n" + "\n".join(blocks)
            if refs:
                if attach_asset_refs:
                    manifest = [
                        f"参考图{index}={entry['label']}"
                        for index, entry in enumerate(sent_entries, 1)]
                    prompt += (
                        "\n\n参考图绑定清单（编号严格对应实际上传顺序）：\n- " +
                        "\n- ".join(manifest) +
                        "\n不得把不同编号的场景、主体、服装、纹理或元素互相交换。")
                else:
                    prompt += (
                        "\n视频输入首帧已经包含本镜头定稿的场景、主体和元素布局；"
                        "只允许生成动作和镜头运动，不得重新设计首帧内容。")
            elif not attach_asset_refs:
                prompt += (
                    "\n当前是纯文生视频，没有上传参考图；只能依赖文字约束，"
                    "不能视为严格一致性生成。")
        report = {
            "entries": sent_entries,
            "omitted": [entry["label"] for entry in omitted_entries],
            "critical_omitted": critical_omitted,
            "missing": missing,
            "character_ids": character_ids,
            "element_ids": element_ids,
            "exact_element_ids": exact_element_ids,
            "scene_id": scene_id,
        }
        return prompt, refs, report

    def _save_reference_anchor(self, shot_id: str, kind: str):
        """把当前采用图片新建或追加到资源中心角色/场景，并绑定回当前项目。"""
        shot = self._find_shot(shot_id)
        path = self._selected_image_path(shot)
        selected_kind = next(
            (asset.get("kind") for asset in (shot or {}).get("assets", [])
             if isinstance(asset, dict) and asset.get("path") == path), "")
        if not path or selected_kind != "image" or not os.path.exists(path):
            self.status_msg.emit("请先选择一张已生成图片作为一致性参考", "warn")
            return
        bible = (self._storyboard or {}).get("visual_bible", {})
        item_id = bible.get(f"{kind}_id", "")
        label = "角色" if kind == "character" else "场景"
        try:
            from ai.ui.resource_center import import_assets_to_resource_center
            result = import_assets_to_resource_center(
                self, [path], default_kind=kind, existing_id=item_id)
            if not result:
                return
            saved_kind, items = result
            if not items:
                return
            saved_item = items[0]
            bible[f"{saved_kind}_id"] = saved_item.id
            self._resource_db = None
            self.refresh_resource_links(saved_kind)
            self.status_msg.emit(
                f"已保存并绑定“{getattr(saved_item, 'name', label)}”，后续镜头会自动携带参考", "success")
        except Exception as error:
            self.status_msg.emit(f"保存到 AI 资产库失败：{error}", "error")

    def _render_storyboard(self):
        while self.storyboard_layout.count() > 1:
            item = self.storyboard_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._shot_cards.clear()
        board = self._storyboard or {}
        shots = board.get("shots", [])
        bible = board.get("visual_bible", {})
        models = board.get("production_models")
        if not isinstance(models, dict):
            models = {}
        provider_name = models.get("image_provider") or bible.get("image_provider", "")
        if provider_name:
            provider_index = self.storyboard_image_provider.findData(provider_name)
            self.storyboard_image_provider.blockSignals(True)
            if provider_index < 0:
                self.storyboard_image_provider.addItem(
                    f"{provider_name}（当前不可用）", provider_name)
                provider_index = self.storyboard_image_provider.findData(provider_name)
            self.storyboard_image_provider.setCurrentIndex(provider_index)
            self.storyboard_image_provider.blockSignals(False)
        video_provider_name = (
            models.get("video_provider") or bible.get("video_provider", ""))
        if video_provider_name:
            provider_index = self.storyboard_video_provider.findData(video_provider_name)
            self.storyboard_video_provider.blockSignals(True)
            if provider_index < 0:
                self.storyboard_video_provider.addItem(
                    f"{video_provider_name}（当前不可用）", video_provider_name)
                provider_index = self.storyboard_video_provider.findData(video_provider_name)
            self.storyboard_video_provider.setCurrentIndex(provider_index)
            self.storyboard_video_provider.blockSignals(False)
        self.refresh_resource_links("")
        if not any(shot.get("id") == self._active_shot_id for shot in shots):
            self._active_shot_id = str(shots[0].get("id", "")) if shots else ""
        self.storyboard_title.setText(
            f"{board.get('title', '未命名分镜')}  ·  {len(shots)} 镜头  ·  "
            f"约 {board.get('duration', 0):g} 秒  ·  {self.director_ratio.currentText()}"
        )
        summary = board.get("summary", "")
        inventory = board.get("asset_inventory", {})
        missing_assets = inventory.get("missing", []) if isinstance(inventory, dict) else []
        not_ready_assets = inventory.get("not_ready", []) if isinstance(inventory, dict) else []
        full_summary = summary
        if missing_assets or not_ready_assets:
            full_summary += (
                f"\n素材提醒：缺少 {len(missing_assets)} 项，"
                f"未选定参考图 {len(not_ready_assets)} 项。")
        display_summary = summary or "分镜已准备好：按镜头生成画面，再用选中的图生成视频。"
        if len(display_summary) > 150:
            display_summary = display_summary[:147].rstrip() + "…"
        self.storyboard_summary.setText(display_summary)
        self.storyboard_summary.setToolTip(full_summary or display_summary)
        for shot in shots:
            sync_legacy_bindings(shot)
            shot["shot_contract"] = build_shot_contract(shot)
            card = StoryboardShotCard(
                shot, self._character_resources, self._scene_resources,
                self._element_resources)
            card.generate_image.connect(self._request_image_for_shot)
            card.generate_image_edit.connect(self._request_image_edit_for_shot)
            card.generate_video.connect(self._request_video_for_shot)
            card.image_to_video.connect(self._request_image_video_for_shot)
            card.refine_image.connect(self._request_ps_refine_for_shot)
            card.preview_requested.connect(self._preview_storyboard_asset)
            card.selected.connect(self._select_storyboard_shot)
            card.asset_selected.connect(self._on_card_asset_selected)
            card.binding_changed.connect(self._on_binding_changed)
            card.inspect_bindings.connect(self._inspect_shot_bindings)
            card.change_image_model.connect(self._show_image_model_menu)
            card.video_link_mode_changed.connect(self._on_video_link_mode_changed)
            card.prepare_assets.connect(self._prepare_assets_for_shot)
            self._shot_cards[shot["id"]] = card
            self.storyboard_layout.insertWidget(self.storyboard_layout.count() - 1, card)
        self._render_storyboard_plan()
        self._refresh_video_link_labels()
        self._refresh_shot_asset_preparation_statuses()
        self.storyboard_production.set_storyboard(board)
        self._select_storyboard_shot(self._active_shot_id)
        self._save_visual_lock()

    def _select_storyboard_shot(self, shot_id: str, preferred_path: str = ""):
        shot = self._find_shot(shot_id)
        if not shot:
            self._active_shot_id = ""
            self.storyboard_production.set_shot(None)
            return
        self._active_shot_id = shot_id
        shot["_element_name"] = ""
        shot["_exact_element_ids"] = []
        shot["_binding_summary"] = ""
        if self._resource_db is not None:
            try:
                bible = (self._storyboard or {}).get("visual_bible", {})
                scene_id = (shot.get("scene_asset_id") or shot.get("scene_id")
                            or bible.get("scene_id", ""))
                scene = self._resource_db.get_scene(scene_id) if scene_id else None
                primary_character = (shot.get("character_id") or
                                     bible.get("character_id", ""))
                character_ids = ([primary_character] if primary_character else []) + list(
                    shot.get("character_ids", []) or [])
                character_ids = [item_id for item_id in dict.fromkeys(character_ids) if item_id]
                character_names = []
                for item_id in character_ids:
                    item = self._resource_db.get_character(item_id)
                    if item:
                        character_names.append(item.name)
                primary_element = shot.get("element_id", "")
                element_ids = ([primary_element] if primary_element else []) + list(
                    shot.get("element_ids", []) or [])
                element_ids = [item_id for item_id in dict.fromkeys(element_ids) if item_id]
                element_names = []
                exact_ids = []
                for item_id in element_ids:
                    element = self._resource_db.get_element(item_id)
                    if not element:
                        continue
                    element_names.append(element.name)
                exact_ids = self._exact_element_ids_for_shot(shot)
                shot["_element_name"] = "、".join(element_names)
                shot["_exact_element_ids"] = exact_ids
                parts = []
                if scene:
                    parts.append(f"场景：{scene.name}")
                if character_names:
                    parts.append("主体：" + "、".join(character_names))
                if element_names:
                    parts.append("元素：" + "、".join(element_names))
                if exact_ids:
                    parts.append(f"精确植入 {len(exact_ids)} 个")
                shot["_binding_summary"] = " · ".join(parts)
            except Exception:
                pass
        for card_id, card in self._shot_cards.items():
            card.set_active(card_id == shot_id)
        self.storyboard_production.set_storyboard(self._storyboard or {})
        self.storyboard_production.set_shot(shot, preferred_path)
        self._refresh_production_task()

    def _on_card_asset_selected(self, shot_id: str, path: str):
        self._select_storyboard_shot(shot_id, path)
        self.storyboard_changed.emit()

    def _on_panel_asset_selected(self, shot_id: str, path: str):
        card = self._shot_cards.get(shot_id)
        if card:
            card.refresh_status()
        self._select_storyboard_shot(shot_id, path)
        self.storyboard_changed.emit()

    def _approve_storyboard_asset(self, shot_id: str, path: str):
        """分别定稿关键帧和成片；两者互不覆盖。"""
        shot = self._find_shot(shot_id)
        if not shot or not path or not os.path.exists(path):
            return
        asset = next((item for item in shot.get("assets", [])
                      if isinstance(item, dict) and item.get("path") == path), None)
        if not asset:
            return
        kind = str(asset.get("kind") or "image")
        quality = asset.get("quality_report") or {}
        quality_status = str(quality.get("status") or "")
        if kind == "image" and quality_status == "reject":
            details = "\n".join(quality.get("problems") or [])
            QMessageBox.warning(
                self, "这张图片不能定稿",
                "自动检查发现图片文件存在明确问题。\n\n" +
                (details or "文件损坏、画幅或画面内容异常。") +
                "\n\n请删除该候选并重新生成。")
            self._set_shot_generation_feedback(
                shot, "未定稿：候选没有通过技术检查，请重新生成", "error")
            return
        if kind == "image" and quality_status == "warn":
            details = "\n".join(quality.get("warnings") or [])
            answer = QMessageBox.question(
                self, "候选存在检查提醒",
                (details or "这张候选存在可能影响成片的问题。") +
                "\n\n你已经人工确认画面正确，仍要把它设为定稿图片吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        shot["preview_asset"] = path
        shot["selected_asset"] = path
        shot["asset_type"] = kind
        if kind == "image":
            shot["selected_image_asset"] = path
            shot["anchor_frame_id"] = path
            message = "已设为定稿图片；它将作为本镜头视频首帧和后续镜头参考"
            missing_exact = self._missing_exact_element_ids(shot, asset)
            if missing_exact:
                message = (
                    f"已定稿基础图片；还有 {len(missing_exact)} 个精确元素未植入，"
                    "请在右侧点击“精确植入绑定元素”后再生成视频")
        else:
            shot["selected_video_asset"] = path
            message = "已设为定稿视频；导入剪辑台时将优先使用它"
        shot["status"] = "ready"
        card = self._shot_cards.get(shot_id)
        if card:
            card.refresh_status()
        self.storyboard_production.set_storyboard(self._storyboard or {})
        self._select_storyboard_shot(shot_id, path)
        self.storyboard_changed.emit()
        self._set_shot_generation_feedback(shot, message, "success")
        if (kind == "image" and self._storyboard_batch_active and
                self._storyboard_batch_waiting_shot_id == shot_id and
                not self._missing_exact_element_ids(shot, asset)):
            self._storyboard_batch_waiting_shot_id = ""
            self.btn_batch_keyframes.setText(
                f"正在生成全部画面 · 剩余 {len(self._storyboard_batch_queue)}")
            QTimer.singleShot(0, self._submit_next_batch_keyframe)

    def _exact_element_ids_for_shot(self, shot: dict | None) -> list[str]:
        """返回本镜头必须后期逐像素植入的元素，不把普通参考元素算进来。"""
        if not shot:
            return []
        sync_legacy_bindings(shot)
        bindings = {
            item.get("asset_id"): item
            for item in shot.get("element_bindings", [])
            if isinstance(item, dict) and item.get("asset_id")}
        primary = str(shot.get("element_id") or "")
        ids = ([primary] if primary else []) + list(shot.get("element_ids", []) or [])
        exact = []
        for item_id in dict.fromkeys(value for value in ids if value):
            binding = bindings.get(item_id, {})
            mode = str(binding.get("mode") or "")
            if not mode and item_id == primary:
                mode = str(shot.get("element_mode") or "")
            if not mode and self._resource_db is not None:
                try:
                    item = self._resource_db.get_element(item_id)
                    mode = str(getattr(item, "default_mode", "exact") or "exact")
                except Exception:
                    mode = "exact"
            if (mode or "exact") == "exact":
                exact.append(item_id)
        return exact

    def _missing_exact_element_ids(self, shot: dict | None,
                                   asset: dict | None) -> list[str]:
        required = self._exact_element_ids_for_shot(shot)
        applied = set((asset or {}).get("exact_elements_applied", []) or [])
        return [item_id for item_id in required if item_id not in applied]

    def _remove_storyboard_result(self, shot_id: str, path: str):
        shot = self._find_shot(shot_id)
        if not shot or not path:
            return
        assets = [asset for asset in shot.get("assets", [])
                  if isinstance(asset, dict)]
        target = next((asset for asset in assets if asset.get("path") == path), None)
        if target is None:
            return
        answer = QMessageBox.question(
            self, "移除生成结果",
            f"要从这个镜头的结果列表中移除“{Path(path).name}”吗？\n\n"
            "本地图片或视频文件会保留，不会从磁盘删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        remaining = [asset for asset in assets if asset.get("path") != path]
        shot["assets"] = remaining
        if (shot.get("preview_asset") == path or
                shot.get("selected_asset") == path):
            replacement = remaining[-1] if remaining else None
            shot["selected_asset"] = str(replacement.get("path") or "") if replacement else ""
            shot["preview_asset"] = shot["selected_asset"]
            shot["asset_type"] = str(replacement.get("kind") or "") if replacement else ""
        if (shot.get("selected_image_asset") == path or
                shot.get("anchor_frame_id") == path):
            shot["selected_image_asset"] = ""
            shot["anchor_frame_id"] = ""
        if shot.get("selected_video_asset") == path:
            shot["selected_video_asset"] = ""
        if not remaining:
            shot["status"] = "pending"
        self._refresh_after_result_cleanup(shot)

    def _keep_only_storyboard_result(self, shot_id: str, path: str):
        shot = self._find_shot(shot_id)
        if not shot or not path:
            return
        assets = [asset for asset in shot.get("assets", [])
                  if isinstance(asset, dict)]
        selected = next((asset for asset in assets if asset.get("path") == path), None)
        if selected is None:
            return
        selected_kind = str(selected.get("kind") or "image")
        removed_count = sum(
            1 for asset in assets
            if str(asset.get("kind") or "image") == selected_kind and
            asset.get("path") != path)
        if removed_count <= 0:
            return
        answer = QMessageBox.question(
            self, "清理同类候选",
            f"要移除其他 {removed_count} 个{'图片' if selected_kind == 'image' else '视频'}候选吗？\n\n"
            "另一种类型的定稿和候选会保留；本地文件也会保留。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        shot["assets"] = [
            asset for asset in assets
            if str(asset.get("kind") or "image") != selected_kind or
            asset.get("path") == path]
        shot["preview_asset"] = path
        shot["selected_asset"] = path
        shot["asset_type"] = str(selected.get("kind") or "image")
        if selected.get("kind") == "image":
            shot["selected_image_asset"] = path
            shot["anchor_frame_id"] = path
        else:
            shot["selected_video_asset"] = path
        shot["status"] = "ready"
        self._refresh_after_result_cleanup(shot)
        if (selected_kind == "image" and self._storyboard_batch_active and
                self._storyboard_batch_waiting_shot_id == shot_id and
                not self._missing_exact_element_ids(shot, selected)):
            self._storyboard_batch_waiting_shot_id = ""
            self.btn_batch_keyframes.setText(
                f"正在生成全部画面 · 剩余 {len(self._storyboard_batch_queue)}")
            QTimer.singleShot(0, self._submit_next_batch_keyframe)

    def _refresh_after_result_cleanup(self, shot: dict):
        shot_id = str(shot.get("id", ""))
        card = self._shot_cards.get(shot_id)
        if card:
            card.refresh_status()
        self.storyboard_production.set_storyboard(self._storyboard or {})
        self._select_storyboard_shot(
            shot_id, str(shot.get("preview_asset") or
                         shot.get("selected_asset") or ""))
        self.storyboard_changed.emit()

    def _on_binding_changed(self, shot_id: str):
        if self._storyboard:
            rebuild_continuity(self._storyboard)
        self._save_visual_lock()
        if shot_id == self._active_shot_id:
            self._select_storyboard_shot(shot_id)
        self.storyboard_changed.emit()

    def _on_video_link_mode_changed(self, shot_id: str):
        self._refresh_video_link_labels()
        self._save_visual_lock()
        if shot_id == self._active_shot_id:
            self._select_storyboard_shot(shot_id)
        self.storyboard_changed.emit()

    def _refresh_video_link_labels(self):
        shots = (self._storyboard or {}).get("shots", [])
        for index, shot in enumerate(shots):
            next_shot = shots[index + 1] if index + 1 < len(shots) else None
            mode = resolve_video_link_mode(shot, next_shot)
            card = self._shot_cards.get(str(shot.get("id", "")))
            if card:
                card.set_resolved_video_link_mode(mode, next_shot is not None)

    def _inspect_shot_bindings(self, shot_id: str):
        shot = self._find_shot(shot_id)
        if not shot:
            return
        _prompt, _refs, report = self._apply_visual_lock(
            shot, shot.get("image_prompt") or shot.get("scene", ""),
            attach_asset_refs=True)
        lines = []
        entries = report.get("entries", [])
        for index, entry in enumerate(entries, 1):
            lines.append(f"参考图{index}：{entry.get('label', '未标记')}")
        if not lines:
            lines.append("没有可发送的参考图")
        missing = report.get("missing", [])
        omitted = report.get("omitted", [])
        critical = report.get("critical_omitted", [])
        if missing:
            lines.append("\n绑定缺失：\n- " + "\n- ".join(missing))
        if critical:
            lines.append("\n关键参考图超过6图预算：\n- " + "\n- ".join(critical))
        elif omitted:
            lines.append(
                f"\n已优先保留关键参考图；将省略 {len(omitted)} 张低优先级参考。")
        title = "绑定检查通过" if not missing and not critical else "绑定检查未通过"
        message = "\n".join(lines)
        if missing or critical:
            QMessageBox.warning(self, title, message)
        else:
            QMessageBox.information(self, title, message)

    def _consistency_audit(self) -> dict:
        """对资产批准状态、连续场景和已生成结果做零成本本地检查。"""
        board = self._storyboard or {}
        shots = board.get("shots", []) if isinstance(board.get("shots"), list) else []
        errors: list[str] = []
        warnings: list[str] = []
        if not shots:
            return {"errors": ["还没有可检查的导演分镜"], "warnings": []}
        rebuild_continuity(board)
        bible = board.get("visual_bible", {}) or {}
        groups: dict[str, list[dict]] = {}
        for shot in shots:
            number = int(shot.get("number", 0) or 0)
            prefix = f"镜头{number:02d}"
            errors.extend(f"{prefix}：{problem}"
                          for problem in self._shot_readiness_problems(shot))
            scene_id = (shot.get("scene_asset_id") or shot.get("scene_id")
                        or bible.get("scene_id", ""))
            scene = self._resource_db.get_scene(scene_id) if (
                self._resource_db is not None and scene_id) else None
            camera_role = _scene_camera_role(shot.get("camera_slot"))
            if camera_role and scene:
                camera_path = (getattr(scene, "reference_views", {}) or {}).get(
                    camera_role, "")
                if not camera_path or not os.path.exists(camera_path):
                    warnings.append(
                        f"{prefix}：场景“{scene.name}”尚未制作 {shot.get('camera_slot')} 固定机位，"
                        "本次只能依赖场景参考图重新构图")

            selected = self._selected_image_path(shot) or self._selected_video_path(shot)
            if selected:
                selected_asset = next((
                    item for item in shot.get("assets", [])
                    if isinstance(item, dict) and item.get("path") == selected), None)
                if not os.path.exists(selected):
                    errors.append(f"{prefix}：当前采用的生成结果文件已丢失")
                elif selected_asset is None:
                    warnings.append(f"{prefix}：当前结果没有版本元数据")
                else:
                    snapshot = selected_asset.get("binding_snapshot") or {}
                    if snapshot and not self._binding_snapshot_matches(
                            snapshot, self._binding_signature(shot)):
                        warnings.append(
                            f"{prefix}：当前结果使用的是旧资产版本；图生视频前需要重新生成关键帧")

            group = str(shot.get("continuity_group") or "")
            if group:
                groups.setdefault(group, []).append(shot)

        for group, group_shots in groups.items():
            previous = None
            for shot in group_shots:
                if previous is not None:
                    number = int(shot.get("number", 0) or 0)
                    prefix = f"镜头{number:02d}"
                    previous_scene = (previous.get("scene_asset_id") or
                                      previous.get("scene_id") or bible.get("scene_id", ""))
                    current_scene = (shot.get("scene_asset_id") or shot.get("scene_id")
                                     or bible.get("scene_id", ""))
                    if previous_scene != current_scene:
                        errors.append(
                            f"{prefix}：连续组“{group}”中途更换了场景资产")
                    if (shot.get("previous_shot_id") and
                            shot.get("previous_shot_id") != previous.get("id")):
                        warnings.append(f"{prefix}：连续镜头锚点顺序与当前分镜顺序不一致")
                    old_states = {
                        item.get("asset_id"): item.get("outfit_state", "")
                        for item in previous.get("character_bindings", [])
                        if isinstance(item, dict) and item.get("asset_id")}
                    for binding in shot.get("character_bindings", []):
                        if not isinstance(binding, dict):
                            continue
                        asset_id = binding.get("asset_id", "")
                        before = str(old_states.get(asset_id, "") or "")
                        after = str(binding.get("outfit_state", "") or "")
                        if asset_id and before and after and before != after:
                            warnings.append(
                                f"{prefix}：连续组“{group}”的服装状态从“{before}”变为“{after}”，请确认是剧情变化")
                previous = shot
        return {
            "errors": list(dict.fromkeys(errors)),
            "warnings": list(dict.fromkeys(warnings)),
        }

    def _show_consistency_audit(self):
        report = self._consistency_audit()
        errors = report["errors"]
        warnings = report["warnings"]
        if not errors and not warnings:
            QMessageBox.information(
                self, "一致性体检通过",
                "场景、主体和元素均已定稿；固定版本、连续镜头锚点和已有结果均未发现冲突。")
            return
        sections = []
        if errors:
            sections.append("必须修复：\n- " + "\n- ".join(errors[:12]))
        if warnings:
            sections.append("建议确认：\n- " + "\n- ".join(warnings[:12]))
        remaining = max(0, len(errors) - 12) + max(0, len(warnings) - 12)
        if remaining:
            sections.append(f"另有 {remaining} 项未显示。")
        QMessageBox.warning(self, "一致性体检", "\n\n".join(sections))

    def _refresh_production_task(self):
        task = next((item for item in self._asset_tasks.values()
                     if item.get("shot_id") == self._active_shot_id), None)
        if not task:
            self.storyboard_production.set_task()
            return
        handle = task["handle"]
        progress = max(5, int(float(handle.progress or 0) * 100))
        kind_text = {
            "image": "图片", "video": "视频",
            "dialogue_audio": "对白音频",
            "quality_review": "一致性检查",
        }.get(task.get("kind"), "生成任务")
        continuity_note = ""
        anchor_id = str(task.get("anchor_source_shot_id") or "")
        if task.get("kind") == "image" and anchor_id:
            anchor = self._find_shot(anchor_id)
            continuity_note = (
                f" · 继承镜头{int(anchor.get('number', 0)):02d}"
                if anchor else " · 继承前镜关键帧")
        elif task.get("kind") == "video":
            if anchor_id:
                anchor = self._find_shot(anchor_id)
                continuity_note += (
                    f" · 续拍镜头{int(anchor.get('number', 0)):02d}真实尾帧"
                    if anchor else " · 续拍上一镜真实尾帧")
            if task.get("last_frame_path"):
                continuity_note += " · 首尾过渡"
        self.storyboard_production.set_task(
            f"{kind_text}生成中 · {task.get('provider', '')}{continuity_note}",
            progress, not handle.is_finished)

    def _preview_storyboard_asset(self, path: str, kind: str):
        if not path or not os.path.exists(path):
            self.status_msg.emit("生成结果文件不存在，可能已被移动或删除", "warn")
            return
        try:
            from ui.media_preview import open_single_media_preview
            open_single_media_preview(path, kind, self)
        except Exception as error:
            self.status_msg.emit(f"打开预览失败：{error}", "error")

    def _compose_exact_element(self, shot_id: str):
        """把元素母版透视贴入图片，或跟踪视频平面后逐帧贴入。"""
        shot = self._find_shot(shot_id)
        exact_ids = list((shot or {}).get(
            "_remaining_exact_element_ids",
            (shot or {}).get("_exact_element_ids", [])) or [])
        if (shot and "_remaining_exact_element_ids" not in shot and not exact_ids and
                shot.get("element_id") and
                shot.get("element_mode", "exact") == "exact"):
            exact_ids = [shot["element_id"]]
        if not shot or not exact_ids:
            self.status_msg.emit("请先给这个镜头绑定一个指定元素", "warn")
            return
        path = str(shot.get("selected_asset") or "")
        asset = next((item for item in shot.get("assets", [])
                      if isinstance(item, dict) and item.get("path") == path), None)
        if not asset or not os.path.exists(path):
            self.status_msg.emit("请先选择一张图片或一个视频结果", "warn")
            return
        try:
            if self._resource_db is None:
                from ai.service import get_asset_db
                self._resource_db = get_asset_db()
            elements = [self._resource_db.get_element(item_id) for item_id in exact_ids]
            elements = [item for item in elements if item is not None]
            if not elements:
                raise ValueError("绑定的精确元素资源不存在")
            element = elements[0]
            if len(elements) > 1:
                labels = [item.name for item in elements]
                selected, accepted = QInputDialog.getItem(
                    self, "选择要精确植入的元素",
                    "可依次植入多个元素；本次请选择一个：", labels, 0, False)
                if not accepted:
                    return
                element = elements[labels.index(selected)]
            element_path = approved_asset_path(element)
            if not element_path or not os.path.exists(element_path):
                raise ValueError("绑定元素没有选定参考图，请先到制片画布选择一张候选图")
            kind = str(asset.get("kind") or "image")
            if kind == "video":
                from ui.element_compositor import VideoElementTrackerDialog
                dialog = VideoElementTrackerDialog(
                    path, element_path, getattr(element, "name", "元素"), self)
            else:
                from ui.element_compositor import ImageElementCompositorDialog
                dialog = ImageElementCompositorDialog(
                    path, element_path, getattr(element, "name", "元素"), self)
            if dialog.exec() != dialog.DialogCode.Accepted or not dialog.result_path:
                return
            actual_duration = 0.0
            if kind == "video":
                try:
                    from ui.media_library import _get_duration
                    actual_duration = float(
                        _get_duration(dialog.result_path, "video") or 0.0)
                except Exception:
                    pass
            applied_key = ("exact_elements_tracked" if kind == "video"
                           else "exact_elements_applied")
            applied_ids = list(dict.fromkeys(
                list(asset.get(applied_key, []) or []) + [element.id]))
            source_was_final = (
                path == str(shot.get("selected_image_asset") or
                            shot.get("anchor_frame_id") or "")
                if kind == "image" else
                path == str(shot.get("selected_video_asset") or ""))
            self.attach_generated_asset(
                shot_id, dialog.result_path, kind,
                actual_duration=actual_duration,
                metadata={"element_id": element.id, "element_exact": True,
                          "source_asset": path,
                          applied_key: applied_ids})
            if source_was_final:
                # 用户是在定稿版本上植入：新版本自动接替旧定稿，避免还要再找一次
                # “设为定稿”，同时让连续生成在所有精确元素完成后自动继续。
                self._approve_storyboard_asset(shot_id, dialog.result_path)
            self.status_msg.emit("指定元素已精确植入，并作为新版本回到当前镜头", "success")
        except Exception as error:
            self.status_msg.emit(f"精确植入失败：{error}", "error")

    def _find_shot(self, shot_id: str) -> dict | None:
        for shot in (self._storyboard or {}).get("shots", []):
            if shot.get("id") == shot_id:
                return shot
        return None

    @staticmethod
    def _selected_image_path(shot: dict | None) -> str:
        if not shot:
            return ""
        selected = str(shot.get("selected_asset") or "")
        assets = [item for item in shot.get("assets", []) if isinstance(item, dict)]
        # 独立的图片定稿槽优先级最高；当前预览视频不会再覆盖它。
        anchor = str(shot.get("selected_image_asset") or
                     shot.get("anchor_frame_id") or "")
        if anchor and os.path.exists(anchor):
            asset = next((item for item in assets if item.get("path") == anchor), None)
            if asset and asset.get("kind") == "image":
                return anchor

        if "selected_image_asset" in shot:
            return ""

        # 兼容尚未迁移的旧分镜数据。
        if selected and os.path.exists(selected):
            asset = next((item for item in assets if item.get("path") == selected), None)
            if asset and asset.get("kind") == "image":
                return selected

        for asset in reversed(assets):
            path = str(asset.get("path") or "")
            if asset.get("kind") == "image" and path and os.path.exists(path):
                return path
        return ""

    def _continuity_anchor_for_shot(self, shot: dict) -> tuple[str, str]:
        """只给真正的连续续拍返回上一关键帧。

        普通 cut/反打/换景别必须根据场景与主体资产重新构图；若把上一镜整图
        当底图并要求保持透视，模型会拒绝执行新机位，反而造成高抽卡率。
        """
        group = str(shot.get("continuity_group") or "")
        source_id = str(shot.get("anchor_source_shot_id")
                        or shot.get("previous_shot_id") or "")
        visited = set()
        target = shot
        while source_id and source_id not in visited:
            visited.add(source_id)
            source = self._find_shot(source_id)
            if not source:
                break
            link_mode = resolve_video_link_mode(source, target)
            if (group and source.get("continuity_group") != group and
                    link_mode != "continue"):
                break
            same_camera = bool(
                str(source.get("camera_slot") or "").strip() and
                str(source.get("camera_slot") or "").strip() ==
                str(target.get("camera_slot") or "").strip())
            explicit_same_take = (
                str(target.get("generation_mode") or "") == "derive_from_anchor" and
                same_camera and link_mode != "cut")
            if link_mode == "continue" or explicit_same_take:
                path = self._selected_image_path(source)
                if path:
                    return path, source_id
            else:
                break
            target = source
            source_id = str(source.get("previous_shot_id") or "")
        return "", ""

    def _next_keyframe_for_shot(self, shot: dict) -> tuple[str, str]:
        """只有“首尾过渡”才把下一镜定稿图作为当前视频尾帧。"""
        shots = (self._storyboard or {}).get("shots", [])
        try:
            index = shots.index(shot)
        except ValueError:
            return "", ""
        if index + 1 < len(shots):
            next_shot = shots[index + 1]
            if resolve_video_link_mode(shot, next_shot) == "bridge":
                return (self._selected_image_path(next_shot),
                        str(next_shot.get("id") or ""))
        return "", ""

    def _resolved_video_link_mode_for_shot(self, shot: dict) -> str:
        shots = (self._storyboard or {}).get("shots", [])
        try:
            index = shots.index(shot)
        except ValueError:
            return normalize_video_link_mode(shot.get("video_link_mode"))
        next_shot = shots[index + 1] if index + 1 < len(shots) else None
        return resolve_video_link_mode(shot, next_shot)

    @staticmethod
    def _selected_video_path(shot: dict | None) -> str:
        if not shot:
            return ""
        assets = [item for item in shot.get("assets", [])
                  if isinstance(item, dict)]
        selected = str(shot.get("selected_video_asset") or "")
        selected_asset = next(
            (item for item in assets if item.get("path") == selected), None)
        if (selected_asset and selected_asset.get("kind") == "video"
                and selected and os.path.exists(selected)):
            return selected
        if "selected_video_asset" in shot:
            return ""
        # 兼容旧项目：旧 selected_asset 可能就是当时采用的视频。
        selected = str(shot.get("selected_asset") or "")
        selected_asset = next(
            (item for item in assets if item.get("path") == selected), None)
        if (selected_asset and selected_asset.get("kind") == "video"
                and selected and os.path.exists(selected)):
            return selected
        for item in reversed(assets):
            path = str(item.get("path") or "")
            if item.get("kind") == "video" and path and os.path.exists(path):
                return path
        return ""

    @staticmethod
    def _extract_video_last_frame(video_path: str) -> str:
        """提取生成视频的真实尾部画面并缓存，供长镜头下一段续拍。"""
        import hashlib
        import cv2
        from config import WORK_DIR

        source = Path(video_path)
        stat = source.stat()
        cache_key = hashlib.sha1(
            f"{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")
        ).hexdigest()[:16]
        folder = Path(WORK_DIR) / "continuity_frames"
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / f"{cache_key}_last.jpg"
        if output.exists() and output.stat().st_size > 0:
            return str(output)

        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            raise ValueError("无法读取上一镜生成视频")
        last = None
        try:
            frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if frame_count > 0:
                capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_count - 4))
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                last = frame
        finally:
            capture.release()
        if last is None or not cv2.imwrite(str(output), last,
                                           [cv2.IMWRITE_JPEG_QUALITY, 94]):
            raise ValueError("上一镜视频没有可用尾帧")
        return str(output)

    def _continuation_start_frame_for_shot(self, shot: dict) -> tuple[str, str]:
        """连续续拍时返回上一镜真实视频尾帧及上一镜 ID。"""
        shots = (self._storyboard or {}).get("shots", [])
        try:
            index = shots.index(shot)
        except ValueError:
            return "", ""
        if index <= 0:
            return "", ""
        previous = shots[index - 1]
        if resolve_video_link_mode(previous, shot) != "continue":
            return "", ""
        previous_id = str(previous.get("id") or "")
        video_path = self._selected_video_path(previous)
        if not video_path:
            return "", previous_id
        return self._extract_video_last_frame(video_path), previous_id

    def _shot_scene_identity(self, shot: dict | None) -> str:
        if not shot:
            return ""
        bible = (self._storyboard or {}).get("visual_bible", {})
        return str(shot.get("scene_asset_id") or shot.get("scene_id")
                   or bible.get("scene_id") or "")

    def _binding_signature(self, shot: dict) -> dict:
        sync_legacy_bindings(shot)
        bible = (self._storyboard or {}).get("visual_bible", {})
        anchor_path, anchor_shot_id = self._continuity_anchor_for_shot(shot)
        scene_id = (shot.get("scene_asset_id") or shot.get("scene_id")
                    or bible.get("scene_id", ""))
        primary_character = shot.get("character_id") or bible.get("character_id", "")
        character_ids = ([primary_character] if primary_character else []) + list(
            shot.get("character_ids", []) or [])
        character_ids = [item_id for item_id in dict.fromkeys(character_ids) if item_id]
        primary_element = shot.get("element_id", "")
        element_ids = ([primary_element] if primary_element else []) + list(
            shot.get("element_ids", []) or [])
        element_ids = [item_id for item_id in dict.fromkeys(element_ids) if item_id]
        element_modes = {}
        for item_id in element_ids:
            if item_id == primary_element:
                element_modes[item_id] = shot.get("element_mode", "exact")
            elif self._resource_db is not None:
                item = self._resource_db.get_element(item_id)
                element_modes[item_id] = getattr(item, "default_mode", "exact") if item else "exact"

        def approved_version(kind: str, asset_id: str, fallback: int) -> int:
            if self._resource_db is not None and asset_id:
                try:
                    item = getattr(self._resource_db, f"get_{kind}")(asset_id)
                    if item is not None:
                        return int(getattr(item, "version", 0) or 0)
                except Exception:
                    pass
            return int(fallback or 0)

        return {
            "shot_contract": build_shot_contract(shot),
            "scene_id": scene_id,
            "scene_version": approved_version(
                "scene", scene_id, int(shot.get("scene_version") or 0)),
            "character_ids": character_ids,
            "character_bindings": [
                {
                    "asset_id": item.get("asset_id", ""),
                    "version": approved_version(
                        "character", item.get("asset_id", ""),
                        int(item.get("version") or 0)),
                    "outfit_state": item.get("outfit_state", ""),
                    "appearance_state": item.get("appearance_state", ""),
                }
                for item in shot.get("character_bindings", [])
                if isinstance(item, dict) and item.get("asset_id")
            ],
            "element_ids": element_ids,
            "element_modes": element_modes,
            "element_bindings": [
                {
                    "asset_id": item.get("asset_id", ""),
                    "version": approved_version(
                        "element", item.get("asset_id", ""),
                        int(item.get("version") or 0)),
                    "mode": item.get("mode", "exact"),
                    "placement": item.get("placement", ""),
                }
                for item in shot.get("element_bindings", [])
                if isinstance(item, dict) and item.get("asset_id")
            ],
            "continuity_group": shot.get("continuity_group", ""),
            "anchor_source_shot_id": anchor_shot_id,
            "anchor_frame_path": anchor_path,
        }

    @staticmethod
    def _binding_snapshot_matches(snapshot: dict | None,
                                  expected: dict | None) -> bool:
        """判断画面绑定是否仍兼容，忽略只影响镜头衔接的动态锚点。

        首镜生成视频后会把当前选择切到视频；这不应使后续镜头已经定稿的
        图片失效。旧项目中缺少新字段时，只校验它实际保存过的字段。
        """
        if not snapshot:
            return True
        expected = expected or {}
        ignored = {"anchor_source_shot_id", "anchor_frame_path"}
        for key, value in snapshot.items():
            if key in ignored or key not in expected:
                continue
            if value != expected.get(key):
                return False
        return True

    def _request_image_for_shot(self, shot_id: str):
        shot = self._find_shot(shot_id)
        if not shot:
            return
        if not self._shot_has_scene(shot):
            self._set_shot_generation_feedback(
                shot,
                "未生成：缺少已定稿的场景参考图。请点“准备缺失素材”或上方素材管理",
                "warn")
            return
        prompt = shot.get("image_prompt") or shot.get("scene", "")
        anchor_path, anchor_shot_id = self._continuity_anchor_for_shot(shot)
        self._submit_asset_task(
            shot, "image", prompt, reference_image=anchor_path or None,
            anchor_source_shot_id=anchor_shot_id)

    def _show_image_model_menu(self, shot_id: str):
        """在镜头卡旁直接选择模型，并用所选模型创建一组全新候选。"""
        card = self._shot_cards.get(shot_id)
        if card is None:
            return
        menu = QMenu(card)
        menu.setStyleSheet(
            "QMenu{background:#202025;color:#eee;border:1px solid #383840;"
            "padding:5px;}QMenu::item{padding:7px 24px 7px 10px;}"
            "QMenu::item:selected{background:#403568;}"
        )
        current = self.storyboard_image_provider.currentData()
        for index in range(self.storyboard_image_provider.count()):
            label = self.storyboard_image_provider.itemText(index)
            provider_name = self.storyboard_image_provider.itemData(index)
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(provider_name == current)
            action.triggered.connect(
                lambda _checked=False, name=provider_name:
                self._regenerate_image_with_provider(shot_id, name))
        if self.storyboard_image_provider.count():
            menu.addSeparator()
        settings_action = menu.addAction("打开生成设置（比例 / 数量）")
        settings_action.triggered.connect(self._show_storyboard_generation_settings)
        button = card._change_model_btn
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _show_storyboard_generation_settings(self):
        self.storyboard_settings_box.setVisible(True)
        self.storyboard_settings_btn.setText("收起高级设置 ▴")
        self.storyboard_image_provider.setFocus()

    def _regenerate_image_with_provider(self, shot_id: str, provider_name: str):
        index = self.storyboard_image_provider.findData(provider_name)
        if index < 0:
            self._set_shot_generation_feedback(
                self._find_shot(shot_id), "未生成：选择的图片模型不可用，请刷新模型列表", "warn")
            return
        self.storyboard_image_provider.setCurrentIndex(index)
        self._save_visual_lock()
        self._request_image_for_shot(shot_id)

    def _request_image_edit_for_shot(self, shot_id: str):
        shot = self._find_shot(shot_id)
        if not shot:
            return
        selected = str(shot.get("selected_asset") or "")
        selected_kind = next((a.get("kind") for a in shot.get("assets", [])
                              if isinstance(a, dict) and a.get("path") == selected), "")
        if selected_kind != "image" or not os.path.exists(selected):
            self._set_shot_generation_feedback(
                shot, "未生成：请先在右侧候选区预览一张存在的图片", "warn")
            return
        if not self._shot_has_scene(shot):
            self._set_shot_generation_feedback(
                shot, "未生成：当前镜头缺少已定稿的场景参考图", "warn")
            return
        prompt = shot.get("image_prompt") or shot.get("scene", "")
        self._submit_asset_task(shot, "image", prompt, reference_image=selected)

    def _shot_has_scene(self, shot: dict) -> bool:
        bible = (self._storyboard or {}).get("visual_bible", {})
        scene_id = (shot.get("scene_asset_id") or shot.get("scene_id")
                    or bible.get("scene_id"))
        if not scene_id or self._resource_db is None:
            return False
        try:
            scene = self._resource_db.get_scene(scene_id)
            return asset_is_approved(scene, require_file=True)
        except Exception:
            return False

    def _start_batch_keyframes(self):
        if self._storyboard_batch_active:
            answer = QMessageBox.question(
                self, "停止连续生成",
                "连续关键帧流程正在运行或等待定稿。要停止后续镜头生成吗？\n\n"
                "已经生成的候选会保留。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer == QMessageBox.StandardButton.Yes:
                self._stop_batch_keyframes("已停止连续关键帧生成")
            return
        if any(not task.get("nonblocking")
               for task in self._asset_tasks.values()):
            QMessageBox.information(
                self, "已有生成任务",
                "当前已有图片或视频生成任务运行中。请等待任务完成后，再启动逐镜头生成。")
            self.status_msg.emit("已有生成任务运行中，请等待完成后再启动连续生成", "warn")
            return
        if not self._storyboard or not self._storyboard.get("shots"):
            self.status_msg.emit("请先生成导演分镜", "warn")
            return
        rebuild_continuity(self._storyboard)
        ready_count, total, missing = self._storyboard_readiness()
        if missing:
            QMessageBox.warning(
                self, "素材还没有准备好",
                f"当前有 {ready_count}/{total} 个镜头可以生成。\n\n" +
                "\n".join(missing[:10]) +
                (f"\n……另有 {len(missing) - 10} 项" if len(missing) > 10 else "") +
                "\n\n请点击页面上方“智能准备全部镜头”补齐参考图。")
            return
        queue = [
            str(shot.get("id", ""))
            for shot in self._storyboard.get("shots", [])
            if not self._selected_image_path(shot)]
        if not queue:
            self.status_msg.emit("所有镜头都已经有采用的关键帧", "success")
            return
        self._storyboard_batch_queue = queue
        self._storyboard_batch_active = True
        self._storyboard_batch_waiting_shot_id = ""
        self.btn_batch_keyframes.setEnabled(True)
        self.btn_batch_keyframes.setText(f"正在生成全部画面 · 剩余 {len(queue)} · 点击停止")
        self.status_msg.emit(
            f"开始按场景顺序生成 {len(queue)} 个缺失关键帧；每个镜头会等待你定稿后再继续",
            "info")
        self._submit_next_batch_keyframe()

    def _submit_next_batch_keyframe(self):
        if (not self._storyboard_batch_active or
                any(not task.get("nonblocking")
                    for task in self._asset_tasks.values()) or
                self._storyboard_batch_waiting_shot_id):
            return
        if not self._storyboard_batch_queue:
            self._stop_batch_keyframes("连续关键帧生成完成", success=True)
            return
        shot_id = self._storyboard_batch_queue.pop(0)
        shot = self._find_shot(shot_id)
        if not shot:
            QTimer.singleShot(0, self._submit_next_batch_keyframe)
            return
        self.btn_batch_keyframes.setText(
            f"正在生成全部画面 · 剩余 {len(self._storyboard_batch_queue) + 1} · 点击停止")
        self._select_storyboard_shot(shot_id)
        self._request_image_for_shot(shot_id)
        if not any(task.get("shot_id") == shot_id
                   for task in self._asset_tasks.values()):
            self._stop_batch_keyframes(
                f"镜头 {shot.get('number')} 未能提交，连续生成已暂停", success=False)

    def _stop_batch_keyframes(self, message: str = "", success: bool = False):
        self._storyboard_batch_active = False
        self._storyboard_batch_queue = []
        self._storyboard_batch_waiting_shot_id = ""
        self.btn_batch_keyframes.setEnabled(True)
        self.btn_batch_keyframes.setText("生成所有缺失画面")
        if message:
            self.status_msg.emit(message, "success" if success else "warn")

    def _request_video_for_shot(self, shot_id: str):
        shot = self._find_shot(shot_id)
        if not shot:
            return
        answer = QMessageBox.warning(
            self, "直接生成视频可能会变样",
            "跳过图片直接生成视频时，只能依赖文字，人物和环境更容易变化。\n\n"
            "推荐先生成并选中一张画面，再点击“用这张图生成视频”。\n\n"
            "仍要跳过图片继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        prompt = shot.get("video_prompt") or shot.get("scene", "")
        self._submit_video_with_audio_first(
            shot, prompt, reference_image=None)

    def _request_image_video_for_shot(self, shot_id: str):
        shot = self._find_shot(shot_id)
        if not shot:
            return
        self._set_shot_generation_feedback(
            shot, "正在检查定稿图和视频生成参数…", "info")
        selected = self._selected_image_path(shot)
        selected_asset = next((a for a in shot.get("assets", [])
                               if isinstance(a, dict) and a.get("path") == selected), None)
        selected_kind = selected_asset.get("kind") if selected_asset else ""
        if selected_kind != "image" or not os.path.exists(selected):
            self._set_shot_generation_feedback(
                shot, "未提交：请先在结果区点击“设为定稿图片”", "warn")
            return
        missing_exact = self._missing_exact_element_ids(shot, selected_asset)
        if missing_exact:
            self._select_storyboard_shot(shot_id, selected)
            self._set_shot_generation_feedback(
                shot,
                f"未提交：定稿图片还有 {len(missing_exact)} 个精确元素未植入；"
                "请在右侧点击“精确植入绑定元素”",
                "warn")
            return
        expected = self._binding_signature(shot)
        snapshot = selected_asset.get("binding_snapshot") or {}
        if not self._binding_snapshot_matches(snapshot, expected):
            answer = QMessageBox.question(
                self,
                "参考素材已更新",
                "这张图片生成后，当前镜头绑定的场景、主体或元素发生过变化。\n\n"
                "仍然要直接使用眼前这张图片生成视频吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._set_shot_generation_feedback(
                    shot, "已取消：当前图片与最新参考素材不一致", "warn")
                return
        prompt = shot.get("video_prompt") or shot.get("scene", "")
        last_frame, next_shot_id = self._next_keyframe_for_shot(shot)
        reference_image = selected
        anchor_source_shot_id = ""
        try:
            continuation_frame, previous_shot_id = (
                self._continuation_start_frame_for_shot(shot))
        except Exception as error:
            self.status_msg.emit(f"提取上一镜尾帧失败：{error}", "warn")
            continuation_frame, previous_shot_id = "", ""
        if continuation_frame:
            reference_image = continuation_frame
            anchor_source_shot_id = previous_shot_id
        elif previous_shot_id:
            previous = self._find_shot(previous_shot_id)
            number = previous.get("number", "") if previous else ""
            self.status_msg.emit(
                f"这是连续续拍镜头，请先生成镜头{number}的视频；本次暂用当前定稿图起拍",
                "warn")
        self._submit_video_with_audio_first(
            shot, prompt, reference_image=reference_image,
            anchor_source_shot_id=anchor_source_shot_id,
            last_frame=last_frame, end_anchor_shot_id=next_shot_id)

    def _set_shot_generation_feedback(self, shot: dict | None, message: str,
                                      level: str = "warn"):
        """把生成反馈放在镜头附近，避免只写全局日志造成按钮像是没反应。"""
        if not shot:
            self.status_msg.emit(message, level)
            return
        shot_id = str(shot.get("id") or "")
        card = self._shot_cards.get(shot_id)
        if card:
            card.set_task_status(message, 0, False)
        if shot_id == self._active_shot_id:
            self.storyboard_production.set_task(message, 0, False)
        number = shot.get("number", "")
        prefix = f"镜头 {number}：" if number != "" else ""
        self.status_msg.emit(prefix + message, level)

    def _request_ps_refine_for_shot(self, shot_id: str):
        shot = self._find_shot(shot_id)
        if not shot:
            return
        selected = str(shot.get("selected_asset") or "")
        self._request_ps_refine_path(shot_id, selected)

    def _request_ps_refine_path(self, shot_id: str, path: str):
        """按明确路径发送图片，避免多候选时误用上一张选中图。"""
        shot = self._find_shot(shot_id)
        if not shot:
            return
        asset = next((item for item in shot.get("assets", [])
                      if isinstance(item, dict) and item.get("path") == path), None)
        if not asset or asset.get("kind") != "image" or not os.path.exists(path):
            self._set_shot_generation_feedback(
                shot, "无法精修：请先在候选区预览一张存在的图片", "warn")
            return
        shot["preview_asset"] = path
        shot["selected_asset"] = path
        shot["asset_type"] = "image"
        shot["selected_image_asset"] = path
        shot["anchor_frame_id"] = path
        card = self._shot_cards.get(shot_id)
        if card:
            card.refresh_status()
        self._select_storyboard_shot(shot_id, path)
        self.storyboard_changed.emit()
        self.ps_refine_requested.emit(shot_id, path)

    @staticmethod
    def _dialogue_text_for_tts(shot: dict) -> str:
        """Remove short speaker labels while keeping the spoken lines intact."""
        performance = shot.get("performance") or normalize_performance(shot)
        if str(performance.get("line_type") or "none") == "none":
            return ""
        raw = str(performance.get("dialogue") or shot.get("voiceover") or "").strip()
        spoken = []
        for line in raw.splitlines():
            value = line.strip().strip('"“”')
            split_at = next((value.find(mark) for mark in ("：", ":")
                             if 0 <= value.find(mark) <= 18), -1)
            if split_at >= 0:
                value = value[split_at + 1:].strip().strip('"“”')
            if value:
                spoken.append(value)
        return "\n".join(spoken)

    @classmethod
    def _video_prompt_with_dialogue(cls, shot: dict, prompt: str) -> str:
        """Make dialogue and sound first-class video requirements."""
        dialogue = cls._dialogue_text_for_tts(shot)
        performance = shot.get("performance") or normalize_performance(shot)
        line_type = str(performance.get("line_type") or "voiceover")
        sound = str(shot.get("sound") or "").strip()
        blocks = [prompt.rstrip()]
        if dialogue:
            if line_type == "dialogue":
                speaker = str(performance.get("speaker") or "the visible speaker")
                emotion = str(performance.get("emotion") or "natural")
                intensity = float(performance.get("emotion_intensity", 0.5) or 0.5)
                gaze = str(performance.get("gaze_target") or "the scene partner")
                gesture = str(performance.get("gesture") or "restrained natural gestures")
                body = str(performance.get("body_action") or "subtle natural body movement")
                blocks.append(
                    f"Character performance: {speaker} speaks with {emotion} emotion "
                    f"at intensity {intensity:.2f}, looking toward {gaze}; {gesture}; {body}. "
                    "Say the following dialogue verbatim with natural timing and visible "
                    "lip synchronization. Other visible characters remain silent and only "
                    "react. Do not add, omit, translate, or paraphrase any words:\n"
                    f'“{dialogue}”')
            else:
                blocks.append(
                    "Voice-over narration (not spoken by any visible character):\n"
                    f'“{dialogue}”')
        if sound:
            blocks.append(
                "Audio direction: " + sound +
                ". Keep dialogue intelligible above ambience and music.")
        return "\n\n".join(block for block in blocks if block)

    def _submit_video_with_audio_first(self, shot: dict, prompt: str,
                                       reference_image: str | None,
                                       anchor_source_shot_id: str = "",
                                       last_frame: str = "",
                                       end_anchor_shot_id: str = ""):
        """对白镜头先准备声音并校准时长，再提交视频生成。"""
        shot_id = str(shot.get("id") or "")
        dialogue = self._dialogue_text_for_tts(shot)
        if dialogue:
            audio_path = str(shot.get("dialogue_audio") or "")
            audio_ready = (
                shot.get("dialogue_audio_status") == "ready" and
                audio_path and os.path.exists(audio_path))
            if not audio_ready:
                self._pending_video_requests[shot_id] = {
                    "prompt": prompt,
                    "reference_image": reference_image,
                    "anchor_source_shot_id": anchor_source_shot_id,
                    "last_frame": last_frame,
                    "end_anchor_shot_id": end_anchor_shot_id,
                }
                if shot.get("dialogue_audio_status") == "generating":
                    self._set_shot_generation_feedback(
                        shot, "正在先生成对白；完成并校准镜头时长后会自动生成视频", "info")
                    return
                if not self._submit_dialogue_audio_task(
                        shot, "对白先行：使用真实语音时长校准镜头"):
                    self._pending_video_requests.pop(shot_id, None)
                    self._set_shot_generation_feedback(
                        shot, "未提交视频：没有可用的对白语音引擎", "error")
                    return
                self._set_shot_generation_feedback(
                    shot, "第一步：正在生成对白；完成后会自动进入视频生成", "info")
                return
            if not float(shot.get("dialogue_audio_duration", 0) or 0):
                try:
                    from ui.media_library import _get_duration
                    audio_duration = float(_get_duration(audio_path, "audio") or 0.0)
                except Exception:
                    audio_duration = 0.0
                if audio_duration > 0 and self._storyboard:
                    apply_dialogue_audio_duration(self._storyboard, shot_id, audio_duration)
            if (shot.get("performance") or {}).get("needs_dialogue_split"):
                self._set_shot_generation_feedback(
                    shot,
                    "对白超过单个视频镜头的 8 秒上限，需要让 AI 导演拆成两个正反打镜头",
                    "warn")
                return
        self._submit_asset_task(
            shot, "video", prompt, reference_image=reference_image,
            anchor_source_shot_id=anchor_source_shot_id,
            last_frame=last_frame, end_anchor_shot_id=end_anchor_shot_id)

    def _resume_pending_video_request(self, shot_id: str):
        pending = self._pending_video_requests.pop(shot_id, None)
        shot = self._find_shot(shot_id)
        if not pending or not shot:
            return
        if (shot.get("performance") or {}).get("needs_dialogue_split"):
            self._set_shot_generation_feedback(
                shot,
                "对白真实时长超过 8 秒，已停止视频提交；请把这句对白拆成两个镜头",
                "warn")
            return
        self._submit_asset_task(
            shot, "video", pending["prompt"],
            reference_image=pending.get("reference_image"),
            anchor_source_shot_id=pending.get("anchor_source_shot_id", ""),
            last_frame=pending.get("last_frame", ""),
            end_anchor_shot_id=pending.get("end_anchor_shot_id", ""))

    def _restart_pending_dialogue_audio(self, shot_id: str):
        shot = self._find_shot(shot_id)
        if not shot or shot_id not in self._pending_video_requests:
            return
        if not self._submit_dialogue_audio_task(
                shot, "对白内容已修改，重新生成后再继续视频"):
            self._pending_video_requests.pop(shot_id, None)
            self._set_shot_generation_feedback(
                shot, "对白重新生成失败，视频没有提交", "error")

    def _submit_dialogue_audio_task(self, shot: dict, reason: str = "") -> bool:
        """Create an external dialogue track when reference video has no native audio."""
        text = self._dialogue_text_for_tts(shot)
        if not text or self._ai_manager is None:
            return False
        shot_id = str(shot.get("id") or "")
        existing_audio = str(shot.get("dialogue_audio") or "")
        if (shot.get("dialogue_audio_status") == "ready" and
                existing_audio and os.path.exists(existing_audio)):
            return True
        if any(task.get("shot_id") == shot_id and
               task.get("kind") == "dialogue_audio"
               for task in self._asset_tasks.values()):
            return True
        providers = self._ai_manager.registry.by_capability("text_to_speech")
        provider = next((item for item in providers if item.name == "edge_tts"),
                        providers[0] if providers else None)
        if provider is None:
            shot["dialogue_audio_status"] = "unavailable"
            return False
        try:
            from config import OUTPUT_DIR
            folder = Path(OUTPUT_DIR) / "ai_audio"
        except Exception:
            folder = Path(__file__).parent.parent / "work_temp" / "ai_audio"
        folder.mkdir(parents=True, exist_ok=True)
        output_path = folder / (
            f"dialogue_{shot_id}_{__import__('uuid').uuid4().hex[:8]}.mp3")
        request = TaskRequest(
            operation="text_to_speech",
            inputs={"text": text},
            params={"output_path": str(output_path), "speed": 1.0},
            metadata={"shot_id": shot_id, "purpose": "storyboard_dialogue"},
            use_cache=False,
        )
        try:
            handle = self._ai_manager.submit(provider.name, request)
        except Exception as error:
            shot["dialogue_audio_status"] = "failed"
            shot["dialogue_audio_error"] = str(error)[:300]
            return False
        self._asset_tasks[handle.id] = {
            "handle": handle, "shot_id": shot_id, "kind": "dialogue_audio",
            "provider": provider.name, "reason": reason, "source_text": text,
        }
        shot["dialogue_audio_status"] = "generating"
        self._asset_timer.start()
        self._refresh_production_task()
        return True

    def _submit_visual_review_task(self, shot: dict, paths: list[str],
                                   reference_assets: list[dict]):
        """Run optional VLM review without blocking generation or final selection."""
        if self._ai_manager is None or not paths or not reference_assets:
            return
        providers = [
            item for item in self._ai_manager.registry.by_capability("json")
            if item.name == "openai"]
        if not providers:
            return
        try:
            from ai.vision_review import build_review_messages
            messages = build_review_messages(shot, paths, reference_assets)
            from api_config import get as get_api_config
            model = get_api_config("llm").default_model or "gpt-4o"
            request = TaskRequest(
                operation="json",
                inputs={"messages": messages},
                params={"model": model},
                metadata={"shot_id": shot.get("id", ""),
                          "purpose": "storyboard_visual_review"},
                use_cache=False,
            )
            handle = self._ai_manager.submit(providers[0].name, request)
            self._asset_tasks[handle.id] = {
                "handle": handle,
                "shot_id": str(shot.get("id") or ""),
                "kind": "quality_review",
                "provider": providers[0].name,
                "paths": list(paths),
                "nonblocking": True,
            }
        except Exception:
            # Local technical checks remain available when no vision endpoint is
            # configured or a gateway rejects multimodal chat.
            return

    def _submit_asset_task(self, shot: dict, kind: str, prompt: str,
                           reference_image: str | None,
                           anchor_source_shot_id: str = "",
                           last_frame: str = "",
                           end_anchor_shot_id: str = ""):
        if self._ai_manager is None:
            self._set_shot_generation_feedback(
                shot, "未提交：没有可用的 AI 生成引擎，请检查设置", "error")
            return
        sync_legacy_bindings(shot)
        shot["shot_contract"] = build_shot_contract(shot)
        contract_problems = self._shot_readiness_problems(shot)
        if contract_problems:
            self._set_shot_generation_feedback(
                shot,
                "未提交：拍摄合同未满足：" + "；".join(contract_problems[:4]),
                "warn")
            return
        if not prompt.strip():
            self._set_shot_generation_feedback(
                shot,
                "未提交：当前镜头没有画面提示词" if kind == "image"
                else "未提交：当前镜头没有视频动作提示词",
                "warn")
            return
        shot_id = shot["id"]
        if any(t.get("shot_id") == shot_id and not t.get("nonblocking")
               for t in self._asset_tasks.values()):
            self._set_shot_generation_feedback(
                shot, "已有生成任务运行中，请等待完成后再点", "warn")
            return
        ratio = self.director_ratio.currentText()
        visual_refs = []
        reference_asset = next(
            (item for item in shot.get("assets", [])
             if isinstance(item, dict) and item.get("path") == reference_image), None)
        applied_exact = list((reference_asset or {}).get("exact_elements_applied", []) or [])
        try:
            if kind == "image":
                prompt, visual_refs, binding_report = self._apply_visual_lock(
                    shot, prompt, primary_reference=reference_image or "",
                    attach_asset_refs=True,
                    applied_exact_element_ids=applied_exact)
                shot["_binding_report"] = binding_report
                problems = list(binding_report.get("missing", []))
                problems.extend(
                    f"参考图预算不足，关键资产未发送：{label}"
                    for label in binding_report.get("critical_omitted", []))
                if problems:
                    raise ValueError("绑定校验失败：" + "；".join(problems))
                omitted = binding_report.get("omitted", [])
                if omitted:
                    self.status_msg.emit(
                        f"镜头参考预算为6张，已省略{len(omitted)}张低优先级参考",
                        "warn")
                operation = "image_edit" if visual_refs else "text_to_image"
                providers = self._ai_manager.registry.by_capability(operation)
                selected_provider = str(
                    ((self._storyboard or {}).get("production_models") or {}).get(
                        "image_provider") or
                    self.storyboard_image_provider.currentData() or "seedream")
                provider = next(
                    (p for p in providers if p.name == selected_provider), None)
                if provider is None:
                    raise ValueError(
                        f"故事板已锁定图片模型“{selected_provider}”，但它当前不支持或未配置 "
                        f"{operation}。系统已停止，不会静默切换到其他图片模型")
                from core.image_output_size import resolve_image_output_size
                size = resolve_image_output_size(provider.name, "2K", ratio)
                inputs = {"prompt": prompt}
                if visual_refs:
                    inputs["image"] = visual_refs[0]
                    inputs["images"] = visual_refs
                    inputs["style_images"] = visual_refs[1:]
                    inputs["reference_assets"] = binding_report.get("entries", [])
                request = TaskRequest(
                    operation=operation,
                    inputs=inputs,
                    params={"size": size,
                            "n": int(self.storyboard_image_count.currentData() or 4),
                            "quality": "high", "watermark": False},
                    metadata={"shot_id": shot_id,
                              "generation_route": route_shot_generation(shot)},
                    use_cache=False,
                )
            else:
                operation = "image_to_video" if reference_image else "text_to_video"
                providers = self._ai_manager.registry.by_capability(operation)
                selected_provider = str(
                    ((self._storyboard or {}).get("production_models") or {}).get(
                        "video_provider") or
                    self.storyboard_video_provider.currentData() or "seedance")
                provider = next(
                    (p for p in providers if p.name == selected_provider), None)
                if provider is None:
                    raise ValueError(
                        f"故事板已锁定视频模型“{selected_provider}”，但它当前不支持或未配置 "
                        f"{operation}。系统已停止，不会静默切换到其他视频模型")
                prompt = self._video_prompt_with_dialogue(shot, prompt)
                # Seedance 的纯参考模式可以直接携带资产图；已有首帧时让首帧作为
                # 唯一画面基底，避免模型在角色母版与首帧构图之间发生竞争。
                # Veo 3.1 Preview supports up to three typed asset references.
                attach_video_refs = (
                    not reference_image and provider.name in {"seedance", "veo"})
                video_reference_budget = 3 if provider.name == "veo" else 6
                prompt, video_visual_refs, binding_report = self._apply_visual_lock(
                    shot, prompt, primary_reference=reference_image or "",
                    attach_asset_refs=attach_video_refs,
                    applied_exact_element_ids=applied_exact,
                    reference_budget=video_reference_budget)
                shot["_binding_report"] = binding_report
                if reference_image or attach_video_refs:
                    problems = list(binding_report.get("missing", []))
                    if problems:
                        raise ValueError("绑定校验失败：" + "；".join(problems))
                if provider.name == "veo" and ratio not in {"16:9", "9:16"}:
                    raise ValueError(
                        f"Veo 3.1 不支持 {ratio} 视频，请把导演比例改为 16:9 或 9:16，"
                        "或切换 Seedance")
                inputs = {"prompt": prompt}
                if reference_image:
                    inputs["image"] = reference_image
                if last_frame and os.path.exists(last_frame):
                    inputs["last_frame"] = last_frame
                if attach_video_refs and video_visual_refs:
                    inputs["style_images"] = video_visual_refs
                if binding_report.get("entries"):
                    inputs["reference_assets"] = binding_report["entries"]
                target_duration = float(shot.get("duration", 4) or 4)
                duration = self._generation_duration(target_duration)
                if provider.name == "veo" and attach_video_refs and video_visual_refs:
                    duration = 8
                request = TaskRequest(
                    operation=operation,
                    inputs=inputs,
                    params={"duration": duration, "ratio": ratio,
                            "generate_audio": True, "strength": 0.7,
                            "resolution": "720p"},
                    metadata={"shot_id": shot_id,
                              "generation_route": route_shot_generation(shot)},
                    use_cache=False,
                )
            handle = self._ai_manager.submit(provider.name, request)
            self._asset_tasks[handle.id] = {
                "handle": handle, "shot_id": shot_id, "kind": kind,
                "provider": provider.name,
                "binding_snapshot": self._binding_signature(shot),
                "binding_manifest": [entry.get("label", "")
                                     for entry in binding_report.get("entries", [])],
                "reference_assets": binding_report.get("entries", []),
                "expected_ratio": ratio,
                "exact_elements_applied": applied_exact,
                "requested_duration": (duration if kind == "video" else 0),
                "target_duration": float(shot.get("duration", 0) or 0),
                "parent_asset_path": reference_image or "",
                "anchor_source_shot_id": anchor_source_shot_id,
                "last_frame_path": last_frame or "",
                "end_anchor_shot_id": end_anchor_shot_id,
                "video_link_mode": self._resolved_video_link_mode_for_shot(shot),
                "audio_mode": (
                    "external_tts" if kind == "video" and
                    shot.get("dialogue_audio_status") == "ready" and
                    bool(shot.get("dialogue_audio")) else
                    "external_tts" if kind == "video" and provider.name == "seedance"
                    and bool(reference_image or last_frame or
                             binding_report.get("entries"))
                    and bool(self._dialogue_text_for_tts(shot)) else
                    "native" if kind == "video" else ""),
            }
            if (kind == "video" and
                    self._asset_tasks[handle.id]["audio_mode"] == "external_tts"):
                self._submit_dialogue_audio_task(
                    shot, "Seedance 参考图模式不返回可靠原生对白")
            card = self._shot_cards.get(shot_id)
            mode = (("图生图" if kind == "image" else "图生视频") if reference_image else
                    ("一致性候选图" if kind == "image" and visual_refs else
                     ("候选图" if kind == "image" else "文生视频")))
            if card:
                duration_note = ""
                if kind == "image":
                    duration_note = (
                        f" · {int(self.storyboard_image_count.currentData() or 4)} 张候选"
                        f" · 参考{len(binding_report.get('entries', []))}张")
                if kind == "video":
                    duration_note = f" · 生成{duration}s，成片{float(shot.get('duration', 0)):g}s"
                    if anchor_source_shot_id:
                        anchor_shot = self._find_shot(anchor_source_shot_id)
                        duration_note += (
                            f" · 续拍镜头{anchor_shot.get('number', '')}真实尾帧"
                            if anchor_shot else " · 续拍上一镜真实尾帧")
                    if last_frame:
                        duration_note += " · 首尾过渡"
                elif anchor_source_shot_id:
                    anchor_shot = self._find_shot(anchor_source_shot_id)
                    duration_note += (
                        f" · 继承镜头{anchor_shot.get('number', '')}" if anchor_shot
                        else " · 继承前镜关键帧")
                card.set_task_status(
                    f"{mode}已提交 · {provider.name}{duration_note}", 5, True)
            self._select_storyboard_shot(shot_id)
            self._refresh_production_task()
            self._asset_timer.start()
            self.status_msg.emit(f"镜头 {shot.get('number')}：{mode}任务已提交", "info")
        except Exception as error:
            self._set_shot_generation_feedback(
                shot, f"提交失败：{str(error)[:140]}", "error")

    @staticmethod
    def _generation_duration(target: float) -> int:
        """模型只支持固定档位，向上取档以保证后续可以裁剪到目标秒数。"""
        try:
            target = float(target)
        except (TypeError, ValueError):
            target = 4.0
        for duration in (4, 6, 8):
            if target <= duration + 1e-6:
                return duration
        return 8

    def _poll_asset_tasks(self):
        finished = []
        for task_id, task in list(self._asset_tasks.items()):
            handle = task["handle"]
            card = self._shot_cards.get(task["shot_id"])
            if card and not handle.is_finished:
                progress = max(5, int(float(handle.progress or 0) * 100))
                kind_label = {
                    "image": "图片", "video": "视频",
                    "dialogue_audio": "对白音频",
                    "quality_review": "一致性检查",
                }.get(task["kind"], str(task["kind"]))
                card.set_task_status(
                    f"{kind_label}生成中 · {task['provider']}", progress, True)
                if task["shot_id"] == self._active_shot_id:
                    kind_text = kind_label
                    self.storyboard_production.set_task(
                        f"{kind_text}生成中 · {task['provider']}", progress, True)
            if handle.is_finished:
                finished.append(task_id)
        for task_id in finished:
            task = self._asset_tasks.pop(task_id)
            self._finish_asset_task(task)
        if not self._asset_tasks:
            self._asset_timer.stop()
        self._refresh_production_task()

    def _finish_asset_task(self, task: dict):
        handle = task["handle"]
        shot = self._find_shot(task["shot_id"])
        card = self._shot_cards.get(task["shot_id"])
        if not shot:
            return
        if not handle.is_success or not handle.result:
            error = handle.result.error if handle.result else "未知错误"
            if task.get("kind") == "quality_review":
                for path in task.get("paths", []):
                    asset = next((item for item in shot.get("assets", [])
                                  if isinstance(item, dict) and
                                  item.get("path") == path), None)
                    if asset:
                        report = asset.setdefault("quality_report", {})
                        report["semantic_status"] = "unavailable"
                        report["semantic_error"] = error[:180]
                if card:
                    card.refresh_status()
                if task.get("shot_id") == self._active_shot_id:
                    self._select_storyboard_shot(task["shot_id"])
                return
            if task.get("kind") == "dialogue_audio":
                shot["dialogue_audio_status"] = "failed"
                shot["dialogue_audio_error"] = error[:300]
                self._pending_video_requests.pop(str(shot.get("id") or ""), None)
                self.status_msg.emit(
                    f"镜头 {shot.get('number')} 对白音频生成失败，视频没有提交："
                    f"{error[:100]}", "warn")
                if card:
                    card.refresh_status()
                return
            if card:
                card.set_task_status(f"生成失败：{error[:80]}", running=False)
            if task["shot_id"] == self._active_shot_id:
                self.storyboard_production.set_task(
                    f"生成失败：{error[:100]}", 0, False)
            self.status_msg.emit(
                f"镜头 {shot.get('number')} 生成失败：{error[:120]}", "error")
            if self._storyboard_batch_active and task.get("kind") == "image":
                self._stop_batch_keyframes(
                    f"镜头 {shot.get('number')} 生成失败，连续生成已暂停")
            return
        if task.get("kind") == "dialogue_audio":
            current_text = self._dialogue_text_for_tts(shot)
            submitted_text = str(task.get("source_text") or "")
            if submitted_text and current_text != submitted_text:
                shot["dialogue_audio"] = ""
                shot["dialogue_audio_status"] = ""
                shot["dialogue_audio_duration"] = 0.0
                self.status_msg.emit(
                    f"镜头 {shot.get('number')} 的对白在生成期间被修改，正在重新生成最新版本",
                    "info")
                QTimer.singleShot(
                    0, lambda sid=str(shot.get("id") or ""):
                    self._restart_pending_dialogue_audio(sid))
                if card:
                    card.refresh_status()
                return
            path = str(handle.result.data or "")
            if path and os.path.exists(path):
                try:
                    from ui.media_library import _get_duration
                    audio_duration = float(_get_duration(path, "audio") or 0.0)
                except Exception:
                    audio_duration = 0.0
                if audio_duration <= 0:
                    shot["dialogue_audio_status"] = "failed"
                    shot["dialogue_audio_error"] = "无法读取生成语音的真实时长"
                    self._pending_video_requests.pop(str(shot.get("id") or ""), None)
                    self.status_msg.emit(
                        f"镜头 {shot.get('number')} 对白文件无法读取，视频没有提交",
                        "warn")
                else:
                    shot["dialogue_audio"] = path
                    shot["dialogue_audio_status"] = "ready"
                    shot["dialogue_audio_source_text"] = self._dialogue_text_for_tts(shot)
                    shot["dialogue_audio_reason"] = task.get("reason", "")
                    if self._storyboard:
                        apply_dialogue_audio_duration(
                            self._storyboard, str(shot.get("id") or ""), audio_duration)
                    for value in self._shot_cards.values():
                        value.refresh_status()
                    board = self._storyboard or {}
                    self.storyboard_title.setText(
                        f"{board.get('title', '未命名分镜')}  ·  "
                        f"{len(board.get('shots', []))} 镜头  ·  "
                        f"约 {board.get('duration', 0):g} 秒  ·  "
                        f"{self.director_ratio.currentText()}")
                    self.status_msg.emit(
                        f"镜头 {shot.get('number')} 对白已准备（{audio_duration:.1f} 秒），"
                        f"镜头时长已自动校准 ✓", "success")
                    QTimer.singleShot(
                        0, lambda sid=str(shot.get("id") or ""):
                        self._resume_pending_video_request(sid))
            else:
                shot["dialogue_audio_status"] = "failed"
                shot["dialogue_audio_error"] = "语音服务没有返回可用文件"
                self._pending_video_requests.pop(str(shot.get("id") or ""), None)
            if card:
                card.refresh_status()
            self.storyboard_production.set_storyboard(self._storyboard or {})
            if str(shot.get("id") or "") == self._active_shot_id:
                self._select_storyboard_shot(self._active_shot_id)
            self.storyboard_changed.emit()
            return
        if task.get("kind") == "quality_review":
            try:
                from ai.vision_review import parse_review
                paths = list(task.get("paths") or [])
                verdicts = parse_review(handle.result.data, len(paths))
                for verdict in verdicts:
                    path = paths[int(verdict["index"]) - 1]
                    asset = next((item for item in shot.get("assets", [])
                                  if isinstance(item, dict) and
                                  item.get("path") == path), None)
                    if not asset:
                        continue
                    report = asset.setdefault("quality_report", {})
                    report["semantic_status"] = verdict["decision"]
                    report["semantic_review"] = verdict
                    reasons = ([verdict.get("reason", "")] +
                               list(verdict.get("missing_assets") or []) +
                               list(verdict.get("identity_errors") or []))
                    reasons = [str(value) for value in reasons if str(value).strip()]
                    if verdict["decision"] == "pass":
                        if report.get("status") == "pending":
                            report["status"] = "pass"
                        report["summary"] = "AI 一致性检查通过"
                    elif verdict["decision"] == "fail":
                        if report.get("status") != "reject":
                            report["status"] = "warn"
                        report["summary"] = "AI 检查：疑似未符合绑定素材"
                        report.setdefault("warnings", []).extend(reasons)
                    else:
                        if report.get("status") in {"pass", "pending"}:
                            report["status"] = "warn"
                        report["summary"] = "AI 检查：建议人工确认"
                        report.setdefault("warnings", []).extend(reasons)
            except Exception as error:
                for path in task.get("paths", []):
                    asset = next((item for item in shot.get("assets", [])
                                  if isinstance(item, dict) and
                                  item.get("path") == path), None)
                    if asset:
                        report = asset.setdefault("quality_report", {})
                        report["semantic_status"] = "invalid_response"
                        report["semantic_error"] = str(error)[:180]
            if card:
                card.refresh_status()
            self.storyboard_production.set_storyboard(self._storyboard or {})
            if task.get("shot_id") == self._active_shot_id:
                self._select_storyboard_shot(task["shot_id"])
            self.storyboard_changed.emit()
            return
        data = handle.result.data
        values = list(data) if isinstance(data, (list, tuple)) else [data]
        paths = []
        durations = {}
        for value in values:
            path = self._materialize_asset(value, task["kind"])
            if path:
                paths.append(path)
                actual_duration = 0.0
                if task["kind"] == "video":
                    try:
                        from ui.media_library import _get_duration
                        actual_duration = float(_get_duration(path, "video") or 0.0)
                    except Exception:
                        actual_duration = 0.0
                durations[path] = actual_duration
        quality_reports = {}
        if task.get("kind") == "image" and paths:
            try:
                from ai.quality_gate import inspect_candidate_group
                quality_reports = inspect_candidate_group(
                    paths,
                    expected_ratio=str(task.get("expected_ratio") or ""),
                    reference_assets=list(task.get("reference_assets") or []),
                )
            except Exception as error:
                quality_reports = {
                    path: {
                        "status": "warn",
                        "summary": "自动检查暂不可用",
                        "warnings": [str(error)[:120]],
                    } for path in paths}
        for path in paths:
            actual_duration = float(durations.get(path, 0) or 0)
            self.attach_generated_asset(
                task["shot_id"], path, task["kind"],
                actual_duration=actual_duration,
                requested_duration=float(task.get("requested_duration", 0) or 0),
                metadata={
                        # 图片生成返回的是待选择候选，不能把接口返回的第一张
                        # 悄悄当成定稿并继续污染后续镜头。
                        "candidate_only": task.get("kind") == "image",
                        "binding_snapshot": task.get("binding_snapshot", {}),
                        "binding_manifest": task.get("binding_manifest", []),
                        "reference_assets": task.get("reference_assets", []),
                        "quality_report": quality_reports.get(path, {}),
                        "exact_elements_applied": task.get(
                            "exact_elements_applied", []),
                        "parent_asset_path": task.get("parent_asset_path", ""),
                        "anchor_source_shot_id": task.get(
                            "anchor_source_shot_id", ""),
                        "last_frame_path": task.get("last_frame_path", ""),
                        "end_anchor_shot_id": task.get("end_anchor_shot_id", ""),
                        "video_link_mode": task.get("video_link_mode", ""),
                        "audio_mode": task.get("audio_mode", ""),
                        "generation_mode": shot.get("generation_mode", ""),
                        "continuity_group": shot.get("continuity_group", ""),
                },
            )
        if task.get("kind") == "image" and paths:
            self._submit_visual_review_task(
                shot, paths, list(task.get("reference_assets") or []))
        if card:
            card.set_task_status(
                f"生成完成 · 新增 {len(paths)} 个版本", 100, False)
            card.refresh_status()
        self.storyboard_production.set_storyboard(self._storyboard or {})
        if task["shot_id"] == self._active_shot_id:
            self._select_storyboard_shot(task["shot_id"])
        self.status_msg.emit(
            f"镜头 {shot.get('number')} {task['kind']}生成完成 ✓", "success")
        if self._storyboard_batch_active and task.get("kind") == "image":
            if paths:
                self._storyboard_batch_waiting_shot_id = str(task["shot_id"])
                self.btn_batch_keyframes.setText(
                    f"等待定稿镜头{int(shot.get('number', 0)):02d} · "
                    f"剩余 {len(self._storyboard_batch_queue)} · 点击停止")
                self._set_shot_generation_feedback(
                    shot,
                    "候选已生成：请在右侧选择一张“设为定稿图片”；"
                    "确认后才会继续下一镜头",
                    "warn")
            else:
                self._stop_batch_keyframes(
                    f"镜头 {shot.get('number')} 没有返回可用图片，连续生成已暂停")

    @staticmethod
    def _materialize_asset(value, kind: str) -> str:
        if isinstance(value, Path):
            value = str(value)
        if isinstance(value, str) and os.path.exists(value):
            return value
        if isinstance(value, (bytes, bytearray)):
            try:
                from config import OUTPUT_DIR
                folder = Path(OUTPUT_DIR) / ("ai_images" if kind == "image" else "ai_videos")
            except Exception:
                folder = Path(__file__).parent.parent / "work_temp" / "ai_assets"
            folder.mkdir(parents=True, exist_ok=True)
            suffix = ".png" if kind == "image" else ".mp4"
            path = folder / f"storyboard_{__import__('uuid').uuid4().hex[:10]}{suffix}"
            path.write_bytes(bytes(value))
            return str(path)
        return ""

    def attach_generated_asset(self, shot_id: str, path: str, kind: str,
                               actual_duration: float = 0.0,
                               requested_duration: float = 0.0,
                               metadata: dict | None = None):
        """由图片/视频生成页回调，将结果绑定回原镜头。"""
        shot = self._find_shot(shot_id)
        if not shot or not path:
            return
        assets = shot.setdefault("assets", [])
        existing = next((a for a in assets if isinstance(a, dict) and
                         a.get("path") == path), None)
        if existing is None:
            existing = {"path": path, "kind": kind}
            assets.append(existing)
        existing["kind"] = kind
        if actual_duration > 0:
            existing["actual_duration"] = round(float(actual_duration), 3)
        if requested_duration > 0:
            existing["requested_duration"] = round(float(requested_duration), 3)
        default_metadata = {"binding_snapshot": self._binding_signature(shot)}
        if metadata:
            default_metadata.update(metadata)
        existing.update(default_metadata)
        # 新结果先进入预览。AI 图片一律只是候选；外部精修回传等明确结果
        # 仍可沿用首次自动采用，兼容旧工作流。
        shot["preview_asset"] = path
        shot["selected_asset"] = path
        shot["asset_type"] = kind
        shot["status"] = "ready"
        if kind == "image":
            candidate_only = bool((metadata or {}).get("candidate_only", False))
            if candidate_only:
                # 显式空槽可阻止旧项目兼容逻辑把当前预览误认为定稿图。
                shot.setdefault("selected_image_asset", "")
                shot.setdefault("anchor_frame_id", "")
            current_image = str(shot.get("selected_image_asset") or
                                shot.get("anchor_frame_id") or "")
            if (not candidate_only and
                    (not current_image or not os.path.exists(current_image))):
                shot["selected_image_asset"] = path
                shot["anchor_frame_id"] = path
        elif kind == "video":
            current_video = str(shot.get("selected_video_asset") or "")
            if not current_video or not os.path.exists(current_video):
                shot["selected_video_asset"] = path
        card = self._shot_cards.get(shot_id)
        if card:
            card.refresh_status()
        self.storyboard_production.set_storyboard(self._storyboard or {})
        self._select_storyboard_shot(shot_id, path)
        self.storyboard_changed.emit()
        self.status_msg.emit(f"镜头 {shot.get('number')} 已接收生成的{kind}", "success")

    def _import_storyboard(self):
        board = self._storyboard or {}
        ready = [s for s in board.get("shots", [])
                 if (s.get("selected_video_asset") or
                     s.get("selected_image_asset") or
                     s.get("anchor_frame_id") or
                     ("selected_video_asset" not in s and
                      "selected_image_asset" not in s and s.get("selected_asset")))]
        if not ready:
            QMessageBox.information(
                self, "导入时间线",
                "请至少先为一个镜头定稿图片或视频。\n\n"
                "导入时优先使用定稿视频，没有视频才使用定稿图片。")
            return
        self.import_storyboard_requested.emit(board)

    def _lbl(self, t, c):
        l = QLabel(t); l.setStyleSheet(f"color:{c};font-size:14px;font-weight:bold;"); return l

    def _refresh_tags(self):
        # 清除旧标签
        for i in reversed(range(self.tags_layout.count())):
            w = self.tags_layout.itemAt(i).widget()
            if w and hasattr(w, 'property') and w.property('_tag_widget'):
                self.tags_layout.removeWidget(w); w.deleteLater()
        # 重建
        for tag in self._tags:
            from PyQt6.QtWidgets import QWidget
            tw = QWidget()
            tw.setProperty('_tag_widget', True)
            tl = QHBoxLayout(tw); tl.setContentsMargins(0,0,0,0); tl.setSpacing(1)
            b = QPushButton(tag)
            b.setFixedHeight(22); b.setStyleSheet(_TAG_STYLE)
            b.clicked.connect(lambda _,t=tag: (self.edit_name.setText(t), self._load_tag_desc(t)))
            tl.addWidget(b)
            # 删除按钮
            db = QPushButton("×")
            db.setFixedSize(18, 18); db.setToolTip(f"删除标签「{tag}」")
            db.setStyleSheet("QPushButton{background:transparent;color:#e74c3c;border:none;font-size:12px;font-weight:bold;border-radius:9px;}QPushButton:hover{background:#3a1a1a;color:#ff4444;}")
            db.clicked.connect(lambda _,t=tag: self._remove_tag(t))
            tl.addWidget(db)
            self.tags_layout.insertWidget(self.tags_layout.count()-1, tw)

    def _remove_tag(self, tag: str):
        if tag in self._tags:
            self._tags.remove(tag)
            _save_tags(self._tags)
            self._refresh_tags()
            self.status_msg.emit(f"已删除标签: {tag}", "info")

    def _add_tag(self):
        name = self.edit_name.text().strip()
        desc = self.editor_desc.toPlainText().strip()
        if name and name not in self._tags:
            self._tags.append(name)
            _save_tags(self._tags)
            self._refresh_tags()
            self.status_msg.emit(f"已保存标签: {name}", "success")

    def _load_tag_desc(self, name: str):
        self.edit_name.setText(name)
        # 尝试加载标签描述
        tf = _TAGS_FILE.parent / f"_tag_{name}.txt"
        if tf.exists():
            self.editor_desc.setPlainText(tf.read_text(encoding="utf-8"))
        self.status_msg.emit(f"已加载: {name}", "info")

    def _save_tag_desc(self, name: str, desc: str):
        try:
            (_TAGS_FILE.parent / f"_tag_{name}.txt").write_text(desc, encoding="utf-8")
        except Exception:
            pass  # 标签保存失败不阻塞主流程

    def load_raw_text(self, text: str):
        self.editor_desc.setPlainText(text)

    def _generate(self):
        name = self.edit_name.text().strip()
        desc = self.editor_desc.toPlainText().strip()
        if not desc:
            self.status_msg.emit("请先描述产品", "warn"); return
        # 自动保存描述
        if name:
            self._save_tag_desc(name, desc)
        style = self.combo_style.currentText()
        dur = self.combo_dur.currentText()
        if dur == "自定义":
            raw = self.edit_dur.text().strip() or "30"
            dur = f"{raw}秒" if raw.isdigit() else raw

        # 清理旧 worker
        if self._worker is not None:
            old = self._worker
            try: old.finished.disconnect(); old.error.disconnect(); old.progress.disconnect()
            except Exception: pass
            if old.isRunning():
                old.finished.connect(old.deleteLater)
            else:
                old.deleteLater()

        self.btn_gen.setEnabled(False); self.btn_gen.setText("生成中..."); self.progress.setValue(10)
        self._worker = _ScriptWorker(name, desc, style, dur)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.finished.connect(self._on_done); self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, r):
        self.btn_gen.setEnabled(True); self.btn_gen.setText("✨ 生成脚本"); self.progress.setValue(100)
        self.editor_result.setPlainText(r); self._last_original = ""; self.btn_restore.hide()
        self.status_msg.emit("生成完成", "success")
        if self._worker: self._worker.deleteLater()
        self._worker = None

    def _on_error(self, e):
        self.btn_gen.setEnabled(True); self.btn_gen.setText("✨ 生成脚本")
        self.status_msg.emit(f"生成失败: {e}", "error")
        if self._worker: self._worker.deleteLater()
        self._worker = None

    def _translate_result(self, code: str):
        text = self.editor_result.toPlainText().strip()
        if not text:
            self.status_msg.emit("请先生成脚本", "warn"); return
        if self._worker is not None and self._worker.isRunning():
            self.status_msg.emit("正在生成中，请稍候", "warn"); return
        # 首次翻译时保存原文
        if not self._last_original:
            self._last_original = text
        # 清理旧 worker
        if self._worker is not None:
            old = self._worker
            try: old.finished.disconnect(); old.error.disconnect()
            except Exception: pass
            if old.isRunning():
                old.finished.connect(old.deleteLater)
            else:
                old.deleteLater()
        self._worker = _TransWorker(text, code)
        self._worker.finished.connect(self._on_trans_done); self._worker.error.connect(self._on_trans_error)
        self.status_msg.emit("翻译中...", "info"); self._worker.start()

    def _on_trans_done(self, r):
        self.editor_result.setPlainText(r); self.btn_restore.show()
        self.status_msg.emit("翻译完成", "success")
        if self._worker: self._worker.deleteLater()
        self._worker = None

    def _restore_original(self):
        """恢复翻译前的原文"""
        if self._last_original:
            self.editor_result.setPlainText(self._last_original)
            self._last_original = ""
            self.btn_restore.hide()
            self.status_msg.emit("已恢复原文", "info")

    def _on_trans_error(self, e):
        self.status_msg.emit(f"翻译失败: {e}", "error")
        if self._worker: self._worker.deleteLater()
        self._worker = None

    def _pick_custom_trans(self):
        from PyQt6.QtWidgets import QInputDialog
        lang, ok = QInputDialog.getText(self, "自定义翻译语种",
            "请输入目标语言（如：法语、德语、意大利语…）")
        if ok and lang.strip():
            self._translate_result(lang.strip())

    def _copy(self):
        t = self.editor_result.toPlainText()
        if t:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(t); self.status_msg.emit("已复制", "info")


def _inline_md(text: str) -> str:
    import re
    text = html.escape(text)
    text = re.sub(
        r"\*\*(.+?)\*\*",
        r'<b style="color:#e0e0e6">\1</b>', text)
    text = re.sub(
        r"\*(.+?)\*",
        r'<i style="color:#95959e">\1</i>', text)
    text = re.sub(
        r"`([^`]+)`",
        r'<code style="background:#1a1a20;color:#a0a0a8;'
        r'padding:1px 5px;border-radius:3px;font-size:12px">\1</code>',
        text)
    return text


class _ScriptWorker(QThread):
    finished=pyqtSignal(str); error=pyqtSignal(str); progress=pyqtSignal(int)
    def __init__(self, n,d,s,r):
        super().__init__(); self._n=n; self._d=d; self._s=s; self._r=r
    def run(self):
        try:
            from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME
            if not LLM_API_KEY: self.error.emit("未配置 API Key，请在设置中填写"); return
            self.progress.emit(30)
            from openai import OpenAI
            c=OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=45.0)
            p=self._n or "产品"
            sys=f"你是顶级短视频广告文案专家。为「{p}」写{self._s}风格口播脚本。\n严格约束：脚本朗读时长必须控制在{self._r}左右，每句8-12字为宜，按语速每秒4字计算总字数。\n要求：前3秒强钩子、卖点突出、CTA结尾、口语化配音感、纯文案无标注、中文输出每句一行。"
            self.progress.emit(60)
            r=c.chat.completions.create(model=LLM_MODEL_NAME,messages=[{"role":"system","content":sys},{"role":"user","content":self._d}])
            self.progress.emit(90)
            self.finished.emit(r.choices[0].message.content.strip())
        except Exception as e:
            import traceback; self.error.emit(f"{e}\n{traceback.format_exc()}")


class _IdeaWorker(QThread):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, messages: list[dict]):
        super().__init__()
        self.messages = [dict(m) for m in messages[-14:]]

    def run(self):
        try:
            from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME
            if not LLM_API_KEY:
                self.error.emit("未配置 API Key，请在设置中填写")
                return
            from openai import OpenAI
            client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=75.0)
            system = """你是短视频创意总监，也是愿意多轮交流的头脑风暴搭档。
用户可能只有一个非常模糊的方向。你的任务不是立即拆分镜头，而是帮用户找到值得拍的故事创意。
工作方式：
1. 信息不足时最多提出2个真正必要的问题，同时先给出可用的初步方向，不能只反问。
2. 需要发散时给出2-4个差异明显的创意，每个写清核心钩子、故事冲突或反差、情绪、适合的视觉表现。
3. 用户选择或要求完善后，收敛成一版完整创意方案：一句话概念、目标观众、开场钩子、故事起承转合、人物、情绪曲线、视觉风格、结尾记忆点。
4. 不要写逐秒分镜，不要虚构无法使用的产品事实，不要堆砌空洞营销词。
5. 使用自然、清楚的中文，像真正的创意搭档一样可以继续被追问和修改。
最终方案要足够具体，使下一步的AI导演能够直接据此拆分镜头。"""
            messages = [{"role": "system", "content": system}] + self.messages
            response = client.chat.completions.create(
                model=LLM_MODEL_NAME,
                messages=messages,
            )
            text = response.choices[0].message.content.strip()
            if not text:
                raise ValueError("AI 返回了空内容")
            self.finished.emit(text)
        except Exception as e:
            self.error.emit(str(e))


class _DirectorWorker(QThread):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(int)
    stage = pyqtSignal(str)
    partial = pyqtSignal(object, str)

    def __init__(self, idea: str, ratio: str, duration: int, pace: str,
                 asset_context: str = "", resume_board: dict | None = None):
        super().__init__()
        self.idea = idea
        self.ratio = ratio
        self.duration = duration
        self.pace = pace
        self.asset_context = asset_context
        self.resume_board = resume_board if isinstance(resume_board, dict) else None

    @staticmethod
    def _segment_schedule(duration: float) -> list[dict]:
        import math
        count = max(1, int(math.ceil(float(duration) / 24.0)))
        base = float(duration) / count
        result = []
        cursor = 0.0
        for index in range(count):
            end = float(duration) if index == count - 1 else base * (index + 1)
            result.append({
                "index": index + 1,
                "start": round(cursor, 3),
                "duration": round(end - cursor, 3),
            })
            cursor = end
        return result

    def _request_json(self, client, model: str, system: str, user: str,
                      label: str, validator=None) -> dict:
        last_error = None
        for attempt in range(2):
            if attempt:
                self.stage.emit(f"{label}响应异常，正在自动重试（2/2）…")
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
                raw = response.choices[0].message.content.strip()
                value = extract_json(raw)
                if validator is not None:
                    validator(value)
                return value
            except Exception as error:
                last_error = error
        raise last_error or RuntimeError(f"{label}没有返回可用内容")

    @staticmethod
    def _merge_inventory(*inventories) -> dict:
        merged = {
            "used": {"characters": [], "scenes": [], "elements": []},
            "not_ready": [], "missing": [],
        }
        seen_entries = {"not_ready": set(), "missing": set()}
        for inventory in inventories:
            if not isinstance(inventory, dict):
                continue
            used = inventory.get("used", {}) or {}
            for kind in ("characters", "scenes", "elements"):
                for value in used.get(kind, []) or []:
                    value = str(value or "").strip()
                    if value and value not in merged["used"][kind]:
                        merged["used"][kind].append(value)
            for bucket in ("not_ready", "missing"):
                for entry in inventory.get(bucket, []) or []:
                    if not isinstance(entry, dict):
                        continue
                    key = (
                        str(entry.get("kind") or "").casefold(),
                        str(entry.get("asset_id") or "").casefold(),
                        str(entry.get("name") or "").strip().casefold(),
                    )
                    if key not in seen_entries[bucket]:
                        seen_entries[bucket].add(key)
                        merged[bucket].append(dict(entry))
        return merged

    @staticmethod
    def _merge_characters(*groups) -> list[dict]:
        result = []
        seen = set()
        for group in groups:
            for entry in group or []:
                if not isinstance(entry, dict):
                    continue
                key = (str(entry.get("asset_id") or "").casefold() or
                       str(entry.get("name") or "").strip().casefold())
                if key and key not in seen:
                    seen.add(key)
                    result.append(dict(entry))
        return result

    @staticmethod
    def _fit_segment_shots(shots: list[dict], start: float,
                           target_duration: float) -> list[dict]:
        """保留模型节奏比例，同时把每段严格贴合计划时间且单镜不超过8秒。"""
        values = [dict(value) for value in shots if isinstance(value, dict)]
        if not values:
            return []
        durations = []
        for value in values:
            try:
                duration = float(value.get("duration", 3) or 3)
            except (TypeError, ValueError):
                duration = 3.0
            durations.append(max(0.5, min(8.0, duration)))
        total = sum(durations) or 1.0
        durations = [max(0.5, min(8.0, value * target_duration / total))
                     for value in durations]
        for _ in range(20):
            delta = target_duration - sum(durations)
            if abs(delta) < 0.001:
                break
            candidates = [index for index, value in enumerate(durations)
                          if (delta > 0 and value < 7.999) or
                          (delta < 0 and value > 0.501)]
            if not candidates:
                break
            share = delta / len(candidates)
            for index in candidates:
                durations[index] = max(0.5, min(8.0, durations[index] + share))
        cursor = float(start)
        result = []
        for index, (value, duration) in enumerate(zip(values, durations)):
            if index == len(values) - 1:
                duration = max(0.5, min(8.0, start + target_duration - cursor))
            value["start"] = round(cursor, 3)
            value["duration"] = round(duration, 3)
            cursor += duration
            result.append(value)
        return result

    def _build_partial_board(self, plan: dict, schedule: list[dict], shots: list[dict],
                             inventory: dict, characters: list[dict],
                             completed: int, status: str) -> dict:
        board = normalize_storyboard({
            "title": plan.get("title") or "未命名分镜",
            "summary": plan.get("summary") or "",
            "production_bible": plan.get("production_bible") or {},
            "screenplay": plan.get("screenplay") or {},
            "asset_inventory": inventory,
            "characters": characters,
            "shots": shots,
        }, self.duration)
        board["duration"] = float(self.duration)
        board["_director_generation"] = {
            "status": status,
            "idea": self.idea,
            "target_duration": int(self.duration),
            "ratio": self.ratio,
            "segments": schedule,
            "completed_segments": completed,
            "plan": plan,
            "asset_inventory": inventory,
            "characters": characters,
        }
        return board

    def _run_segmented(self, client, model: str, asset_hint: str):
        import math
        resumed = self.resume_board is not None
        if resumed:
            state = self.resume_board.get("_director_generation", {})
            plan = dict(state.get("plan") or {})
            schedule = list(state.get("segments") or [])
            completed = max(0, int(state.get("completed_segments", 0) or 0))
            all_shots = [dict(value) for value in self.resume_board.get("shots", [])
                         if isinstance(value, dict)]
            inventory = dict(state.get("asset_inventory") or
                             self.resume_board.get("asset_inventory") or {})
            characters = list(state.get("characters") or
                              self.resume_board.get("characters") or [])
        else:
            schedule = self._segment_schedule(self.duration)
            self.progress.emit(22)
            self.stage.emit(
                f"正在规划 {self.duration} 秒全片结构（随后分 {len(schedule)} 段生成）…")
            plan_system = (
                "你是短剧总导演。先建立全片制作圣经和故事剧本，再规划时间段；"
                "本步骤不写逐镜头提示词。严格返回JSON，不要Markdown。"
                "人物、场景、服装、道具、情绪和因果状态必须前后一致。"
            )
            plan_user = (
                f"故事需求：{self.idea}\n画幅：{self.ratio}；节奏：{self.pace}；"
                f"总时长：{self.duration}秒。\n"
                f"固定分段时间表：{json.dumps(schedule, ensure_ascii=False)}\n"
                f"{asset_hint}\n"
                "返回结构：{\"title\":\"片名\",\"summary\":\"全片叙事与视觉原则\","
                "\"production_bible\":{\"logline\":\"一句话故事\",\"audience\":\"目标观众\","
                "\"format\":\"短剧形式\",\"tone\":\"情绪基调\",\"visual_style\":\"固定画风\","
                "\"color_script\":\"色彩和灯光走向\",\"dialogue_style\":\"对白风格\","
                "\"world_rules\":[],\"continuity_rules\":[]},"
                "\"screenplay\":{\"hook\":\"开场钩子\",\"setup\":\"人物与铺垫\","
                "\"conflict\":\"核心冲突\",\"turn\":\"转折\",\"ending\":\"结尾\","
                "\"dialogue_style\":\"对白原则\",\"sound_direction\":\"声音方向\","
                "\"beats\":[{\"id\":\"beat_01\",\"start\":0,\"end\":8,"
                "\"purpose\":\"剧情功能\",\"summary\":\"事件\","
                "\"entry_state\":\"开始状态\",\"exit_state\":\"结束状态\"}]},"
                "\"asset_inventory\":{\"used\":{\"characters\":[],\"scenes\":[],"
                "\"elements\":[]},\"not_ready\":[],\"missing\":[]},"
                "\"characters\":[{\"asset_id\":\"真实ID或空\",\"name\":\"名称\","
                "\"description\":\"不可漂移特征\"}],"
                "\"segments\":[{\"index\":1,\"beat_id\":\"beat_01\","
                "\"dramatic_purpose\":\"本段剧情功能\",\"summary\":\"本段事件\","
                "\"opening_state\":\"起始状态\",\"ending_state\":\"结束状态\","
                "\"continuity_group\":\"连续性组\",\"scene_names\":[],"
                "\"character_names\":[],\"element_names\":[]}]}。"
                "segments数量和顺序必须与固定时间表完全一致。")

            def validate_plan(value):
                if not isinstance(value.get("segments"), list):
                    raise ValueError("全片规划缺少segments")

            plan = self._request_json(
                client, model, plan_system, plan_user, "全片规划", validate_plan)
            planned_segments = plan.get("segments", []) or []
            for index, scheduled in enumerate(schedule):
                if index < len(planned_segments) and isinstance(planned_segments[index], dict):
                    scheduled.update({
                        key: value for key, value in planned_segments[index].items()
                        if key not in {"index", "start", "duration"}
                    })
            completed = 0
            all_shots = []
            inventory = self._merge_inventory(plan.get("asset_inventory", {}))
            characters = self._merge_characters(plan.get("characters", []))

        total_segments = len(schedule)
        if not schedule or completed >= total_segments:
            board = self._build_partial_board(
                plan, schedule, all_shots, inventory, characters,
                total_segments, "complete")
            self.progress.emit(100)
            self.finished.emit(board)
            return

        for segment_index in range(completed, total_segments):
            segment = schedule[segment_index]
            target_duration = float(segment.get("duration", 0) or 0)
            minimum_shots = max(1, int(math.ceil(target_duration / 8.0)))
            self.progress.emit(32 + int(53 * segment_index / max(1, total_segments)))
            self.stage.emit(
                f"正在生成第 {segment_index + 1}/{total_segments} 段 "
                f"（{target_duration:g}秒，已完成内容会自动保存）…")
            batch_system = f"""你是短剧分镜导演。现在只生成全片中的一个时间段，不得重写其他段。
严格返回合法JSON，不要Markdown。当前段必须生成至少{minimum_shots}个镜头；单镜0.5—8秒，镜头时长总和必须等于{target_duration:g}秒，start从0开始连续排列。
每镜必须写清场景、主体、元素、动作、景别、机位、屏幕方向、调度、声音和转场。
每镜必须继承当前段的 beat_id，并写 dramatic_purpose、entry_state、exit_state、continuity_notes 和 draft_panel；这些字段是后续生成必须遵守的拍摄合同。
同一人物名称、场景名称、服装状态、外貌状态、持有物和continuity_group必须服从全片规划。
对白镜头必须一次只安排一个角色开口；双人对话必须拆成说话者近景、听者反应、反打回答，禁止两个人在同一镜同时说话。
performance必须写清line_type、speaker、原句、情绪强度、视线、表情、手势和身体动作；纯旁白填写line_type=voiceover。
image_prompt使用英文60—120词，只写构图、动作、表情、光线和摄影参数；video_prompt使用英文40—90词，只写动作路径、运镜、环境运动和声音。
每镜必须填写scene_name、character_names、element_names；已有资产使用真实asset_id，缺失资产ID留空，禁止虚构ID。
video_link_mode普通切镜填cut；同一长镜头拆段填continue；只有明确生成式变化填bridge。
普通切镜、反打、换机位或换景别的generation_mode填compose_from_assets；只有video_link_mode=continue的同一长镜头后段才填derive_from_anchor。
返回结构：{{"asset_inventory":{{"used":{{"characters":[],"scenes":[],"elements":[]}},"not_ready":[],"missing":[]}},"characters":[],"shots":[{{"start":0,"duration":4,"beat_id":"beat_01","dramatic_purpose":"剧情功能","entry_state":"镜头开始状态","exit_state":"镜头结束状态","continuity_notes":"必须继承和保持的状态","draft_panel":"给用户看的构图草稿说明","scene":"具体画面","scene_name":"场景名","scene_asset_id":"真实ID或空","scene_version":1,"continuity_group":"组名","shot_size":"角度+景别","camera_slot":"MASTER/A/B/A_REVERSE/B_REVERSE/INSERT/POV","camera":"精确运镜","screen_direction":"屏幕方向","blocking":"位置关系","character":"主体名或空","character_names":[],"character_bindings":[{{"asset_id":"真实ID或空","name":"主体名","version":1,"role":"lead/support/background","outfit_state":"服装状态","appearance_state":"外观状态","required":true}}],"element_names":[],"element_bindings":[{{"asset_id":"真实ID或空","name":"元素名","version":1,"mode":"exact/reference","placement":"位置","required":true}}],"generation_mode":"compose_from_assets/derive_from_anchor","video_link_mode":"cut/continue/bridge","action":"动作","voiceover":"对白旁白或空","performance":{{"line_type":"dialogue/voiceover/none","speaker":"说话者或空","speaker_asset_id":"真实ID或空","dialogue":"必须原样说出的文本或空","emotion":"情绪","emotion_intensity":0.5,"gaze_target":"视线目标","expression":"表情变化","gesture":"手势","body_action":"身体动作","pause_before":0.1,"pause_after":0.2,"mode":"auto"}},"pause":0.2,"transition":{{"type":"cut/dissolve/fade/wipe","duration":0.2}},"sound":"环境音与音乐","asset_type":"image","image_prompt":"英文提示词","video_prompt":"英文提示词"}}]}}。"""
            previous = schedule[segment_index - 1] if segment_index else {}
            batch_user = (
                f"原始故事：{self.idea}\n"
                f"全片规划：{json.dumps(plan, ensure_ascii=False)}\n"
                f"当前段：{json.dumps(segment, ensure_ascii=False)}\n"
                f"上一段结束状态：{previous.get('ending_state', '故事开场')}\n"
                f"可用资产：{asset_hint}")

            def validate_batch(value):
                shots = value.get("shots")
                if not isinstance(shots, list) or len(shots) < minimum_shots:
                    raise ValueError(
                        f"本段至少需要{minimum_shots}个镜头，模型返回不足")

            batch = self._request_json(
                client, model, batch_system, batch_user,
                f"第 {segment_index + 1} 段", validate_batch)
            fitted = self._fit_segment_shots(
                batch.get("shots", []), float(segment.get("start", 0) or 0),
                target_duration)
            all_shots.extend(fitted)
            inventory = self._merge_inventory(
                inventory, batch.get("asset_inventory", {}))
            characters = self._merge_characters(
                characters, batch.get("characters", []))
            completed = segment_index + 1
            status = "complete" if completed == total_segments else "partial"
            board = self._build_partial_board(
                plan, schedule, all_shots, inventory, characters, completed, status)
            self.progress.emit(32 + int(53 * completed / max(1, total_segments)))
            if status == "partial":
                self.partial.emit(
                    board,
                    f"已保存第 {completed}/{total_segments} 段 · "
                    f"共 {len(board.get('shots', []))} 个镜头，继续生成下一段…")
            else:
                self.progress.emit(92)
                self.stage.emit("所有分段完成，正在合并时间线并校验连续性…")
                self.finished.emit(board)

    def run(self):
        try:
            from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME
            if not LLM_API_KEY:
                self.error.emit("未配置 API Key，请在设置中填写")
                return
            self.progress.emit(25)
            from openai import OpenAI
            client = OpenAI(
                api_key=LLM_API_KEY, base_url=LLM_BASE_URL,
                timeout=180.0, max_retries=0)
            asset_hint = ""
            if self.asset_context:
                asset_hint = (
                    "\n\n## 可用资产目录（JSON）\n"
                    f"{self.asset_context}\n"
                    "- 已存在资产必须使用目录中的原始 asset_id，禁止改写或虚构 ID。\n"
                    "- ready=false 的资产可以进入规划，但必须列入 asset_inventory.not_ready。\n"
                    "- 故事需要但目录不存在的资产，ID 留空，并列入 asset_inventory.missing。"
                )
            else:
                asset_hint = (
                    "\n\n当前没有可用资产目录。所有 asset_id 必须留空，并把需要的角色、"
                    "场景和道具列入 asset_inventory.missing，禁止虚构 ID。")
            if self.duration > 30:
                self._run_segmented(client, LLM_MODEL_NAME, asset_hint)
                return
            system = f"""你是资深电影摄影师、场面调度导演和短剧连续性监督。

## 项目参数
- 总时长约 {self.duration} 秒，画幅 {self.ratio}，节奏 {self.pace}
- 时间必须连续，所有镜头总时长尽量等于 {self.duration} 秒
- 镜头时长不得低于0.5秒，单个镜头不得超过8秒（超时场景拆成连续镜头）
- 停顿是镜头内为旁白或情绪保留的无对白时间；转场时长必须单独给出
{asset_hint}

## 连续性规则（优先级高于华丽描述）
1. 同一地点、同一时间和同一事件段必须使用相同 continuity_group。
2. 每镜直接绑定 scene_asset_id、character_bindings、element_bindings；不能只写名字等待系统猜测。
3. 每个出镜主体指定 asset_id、outfit_state、appearance_state 和 role。
4. 同一 continuity_group 内服装、伤痕、持有物、屏幕方向和主光方向保持连续；变化必须明确写出。
5. camera_slot 使用 MASTER/A/B/A_REVERSE/B_REVERSE/INSERT/POV；正反打遵守180度轴线。
6. 每次普通切镜、反打、换机位或换景别都填 generation_mode=compose_from_assets；只有同一长镜头因时长上限拆段、video_link_mode=continue 时，后段才填 derive_from_anchor。
7. image_prompt 只写本镜头构图、表情、动作和光线变化，不重复编造人物五官、服装和场景设计。
8. video_prompt 只写从定稿首帧开始的动作、运镜、环境运动和声音，不重新设计首帧内容。
9. video_link_mode 默认填写 cut。只有同一长镜头因8秒上限被拆段时填写 continue；只有变身、昼夜渐变、匹配画面等明确生成式过渡才填写 bridge。普通换景别、正反打、换机位、dissolve/fade/wipe 均填写 cut。
10. 每镜必须填写 scene_name、character_names、element_names。即使资产目录里不存在、asset_id 为空，也不能省略实际需要生成的素材名称；没有主体或元素时才返回空数组。
11. 对话一次只允许一个角色开口。双人对话必须拆成说话者近景、听者反应镜头、反打回答；其他出镜角色只能反应，不能同时说话。
12. 每镜填写 performance。角色开口用 line_type=dialogue；画外旁白用 voiceover；无台词用 none。对白必须逐字保留，并写清情绪强度、视线、表情、手势和身体动作。
13. 返回 shots 前先完成 production_bible 和 screenplay。每镜必须引用 screenplay.beats 中的 beat_id，并填写 dramatic_purpose、entry_state、exit_state、continuity_notes、draft_panel；后续生图和视频会把这些字段当作拍摄合同。

## 摄影机运动 — 必须使用精确术语
推 (dolly in / push in) | 拉 (dolly out / pull back) | 摇 (pan left/right) | 移 (truck/tracking shot) | 跟 (follow shot) | 升 (crane up / boom up) | 降 (crane down / boom down) | 变焦 (zoom in/out) | 手持 (handheld shake) | 斯坦尼康 (steadicam glide) | 环绕 (orbit) | 俯拍 (bird's eye / overhead) | 仰拍 (low angle) | 过肩 (over-the-shoulder) | POV主观视角 | 荷兰角 (dutch angle)

## 景别 — 必须精确到角度
- 大远景 (extreme wide) / 全景 (wide) / 中全景 (medium wide) / 中景 (medium)
- 中近景 (medium close-up) / 近景 (close-up) / 大特写 (extreme close-up)
- 格式: "shot_size":"低角度仰拍 + 大特写" 或 "平视 + 中近景"

## 生成提示词
- image_prompt 使用英文，控制构图、姿态、表情、景别、焦段、景深和本镜头光线；建议80—160词。
- video_prompt 使用英文，控制起幅、动作路径、速度曲线、落幅、镜头运动和环境运动；建议50—120词。
- 资产固定外貌和场景设定由系统在提交模型时注入，两个 Prompt 都不要重复长篇复制资产描述。

## 声音设计
sound 字段: 环境音/拟音/音效 + 情绪方向（如: "雨声淅沥 + 远处隐约雷鸣 + 紧张弦乐渐入"）

只返回合法 JSON，不要 Markdown，不要解释。结构严格如下：
{{
  "title":"片名",
  "summary":"导演思路、节奏和整体视觉风格",
  "production_bible":{{
    "logline":"一句话故事","audience":"目标观众","format":"短剧形式",
    "tone":"全片情绪基调","visual_style":"不可漂移的固定画风",
    "color_script":"色彩与灯光走向","dialogue_style":"对白风格",
    "world_rules":["世界规则"],"continuity_rules":["全片必须保持的连续性规则"]
  }},
  "screenplay":{{
    "hook":"开场钩子","setup":"人物与铺垫","conflict":"核心冲突",
    "turn":"转折","ending":"结尾","dialogue_style":"对白原则",
    "sound_direction":"音乐和声音方向",
    "beats":[{{"id":"beat_01","start":0,"end":6,"purpose":"剧情功能",
      "summary":"本段事件","entry_state":"开始状态","exit_state":"结束状态"}}]
  }},
  "asset_inventory":{{
    "used":{{"characters":["真实asset_id"],"scenes":["真实asset_id"],"elements":["真实asset_id"]}},
    "not_ready":[{{"asset_id":"真实asset_id","name":"名称","kind":"character/scene/element"}}],
    "missing":[{{"name":"待创建名称","kind":"character/scene/element","description":"设计要求"}}]
  }},
  "characters":[{{"asset_id":"真实ID或空","name":"人物名","description":"剧情身份和不可漂移特征"}}],
  "shots":[{{
    "start":0,"duration":3,"beat_id":"beat_01","dramatic_purpose":"本镜头剧情功能",
    "entry_state":"镜头开始时人物与物体状态","exit_state":"镜头结束状态",
    "continuity_notes":"从前后镜必须继承的服装、位置、持有物和情绪",
    "draft_panel":"给用户看的简洁构图草稿说明",
    "scene":"画面具体内容","scene_name":"资产目录中的场景名",
    "scene_asset_id":"真实ID或空","scene_version":1,"continuity_group":"scene_001_night",
    "shot_size":"低角度仰拍 + 大特写","camera_slot":"A",
    "camera":"dolly in 缓慢推进 + 轻微 handheld 晃动",
    "screen_direction":"主体从画面左向右","blocking":"人物与道具的空间位置",
    "character":"人物名或空","character_names":["本镜头实际出镜主体名称"],
    "character_bindings":[{{"asset_id":"真实ID或空","name":"主体名称","version":1,"role":"lead/support/background","outfit_state":"服装状态","appearance_state":"伤痕/湿身等状态","required":true}}],
    "element_names":["本镜头必须出现的元素名称"],
    "element_bindings":[{{"asset_id":"真实ID或空","name":"元素名称","version":1,"mode":"exact/reference","placement":"出现位置","required":true}}],
    "generation_mode":"compose_from_assets/derive_from_anchor",
    "video_link_mode":"cut/continue/bridge",
    "action":"具体动作描述",
    "voiceover":"对白/旁白原文或空",
    "performance":{{"line_type":"dialogue/voiceover/none","speaker":"说话者或空", "speaker_asset_id":"真实ID或空","dialogue":"必须原样说出的文本或空","emotion":"自然/开心/愤怒/紧张/悲伤","emotion_intensity":0.5,"gaze_target":"视线目标","expression":"表情变化","gesture":"手势","body_action":"身体动作","pause_before":0.1,"pause_after":0.2,"mode":"auto"}},
    "pause":0.3,
    "transition":{{"type":"cut/dissolve/fade/wipe","duration":0.2}},
    "sound":"音乐或音效","asset_type":"image/video/library",
    "image_prompt":"本镜头的英文构图、动作、表情、光线和镜头参数",
    "video_prompt":"从定稿首帧出发的英文动作、运镜、环境运动和声音"
  }}]
}}"""
            self.progress.emit(55)
            response = client.chat.completions.create(
                model=LLM_MODEL_NAME,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": self.idea},
                ],
            )
            self.progress.emit(85)
            raw = response.choices[0].message.content.strip()
            board = normalize_storyboard(extract_json(raw), self.duration)
            if not board.get("shots"):
                raise ValueError("AI 返回的分镜中没有镜头")
            self.finished.emit(board)
        except Exception as e:
            self.error.emit(str(e))

class _TransWorker(QThread):
    finished=pyqtSignal(str); error=pyqtSignal(str)
    def __init__(self, t,l): super().__init__(); self._t=t; self._l=l
    def run(self):
        try:
            from core.builtin_translator import translate_text
            result = translate_text(self._t, self._l)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))
        # 不打印 traceback，避免控制台噪音


_EDITOR="QTextEdit{background:#0e0e0e;border:1px solid #222;border-radius:6px;color:#ccc;font-size:13px;padding:10px;}QTextEdit:focus{border-color:#3d8ef8;}"
_INPUT="QLineEdit{background:#0e0e0e;border:1px solid #222;border-radius:6px;color:#ccc;font-size:13px;padding:6px 10px;}QLineEdit:focus{border-color:#3d8ef8;}"
_COMBO="QComboBox{background:#0e0e0e;border:1px solid #2a2a2a;border-radius:5px;color:#ccc;font-size:12px;padding:4px 8px;}QComboBox QAbstractItemView{background:#1a1a1a;color:#ccc;}QComboBox::drop-down{border:none;}"
_PRIMARY="QPushButton{background:#3d8ef8;color:#fff;border:none;border-radius:6px;font-size:13px;font-weight:bold;padding:8px 20px;}QPushButton:hover{background:#5a9ff9;}QPushButton:disabled{background:#333;color:#666;}"
_ACCENT="QPushButton{background:#3d8ef8;color:#fff;border:none;border-radius:6px;font-size:13px;font-weight:bold;padding:8px 20px;}QPushButton:hover{background:#5a9ff9;}"
_GHOST="QPushButton{background:transparent;color:#888;border:1px solid #333;border-radius:6px;font-size:12px;padding:6px 14px;}QPushButton:hover{color:#ccc;border-color:#555;}"
_TAG_BTN="QPushButton{background:transparent;color:#3d8ef8;border:1px dashed #3d8ef8;border-radius:4px;font-size:11px;padding:2px 8px;}QPushButton:hover{background:#1a3050;}"
_TAG_STYLE="QPushButton{background:#1a2a3a;color:#3d8ef8;border:1px solid #2a4a5a;border-radius:4px;font-size:11px;padding:2px 8px;}QPushButton:hover{background:#1a3050;color:#5a9ff9;}"
_LANG_ON="QPushButton{background:#1a3050;color:#3d8ef8;border:1px solid #3d8ef8;border-radius:3px;font-size:10px;font-weight:bold;padding:2px 6px;}"
_LANG_OFF="QPushButton{background:#1e1e1e;color:#666;border:1px solid #2a2a2a;border-radius:3px;font-size:10px;padding:2px 6px;}QPushButton:hover{color:#ccc;border-color:#555;}"
_MODE_TABS="""
QTabWidget::pane{background:#121214;border:1px solid #252529;border-radius:7px;}
QTabBar::tab{background:#1b1b1e;color:#777;border:none;padding:8px 20px;font-size:12px;}
QTabBar::tab:selected{color:#fff;border-bottom:2px solid #8b6cf0;background:#202024;}
QTabBar::tab:hover{color:#ccc;}
"""
_DIRECTOR_PRIMARY="QPushButton{background:#7657dd;color:#fff;border:none;border-radius:6px;font-size:13px;font-weight:bold;padding:8px 20px;}QPushButton:hover{background:#8b6cf0;}QPushButton:disabled{background:#34303f;color:#777;}"
_DIRECTOR_IMG="QPushButton{background:#17352b;color:#75d8ad;border:1px solid #285c49;border-radius:5px;padding:5px 12px;}QPushButton:hover{background:#214b3d;color:#a4f0cf;}"
_DIRECTOR_VIDEO="QPushButton{background:#172b43;color:#79b5ef;border:1px solid #28517a;border-radius:5px;padding:5px 12px;}QPushButton:hover{background:#203d5e;color:#abd5ff;}"
_IDEA_CHIP="QPushButton{background:#1d1d24;color:#8b8b96;border:1px solid #2e2e38;border-radius:12px;padding:4px 10px;font-size:11px;}QPushButton:hover{background:#25252e;color:#aaa;border-color:#444;}"
