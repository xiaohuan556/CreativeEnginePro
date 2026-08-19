"""AI 制片画布：把资产、候选、分镜和生成结果放到一个可执行无限画布。"""
from __future__ import annotations

import hashlib
import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QRectF, QPointF, QTimer, QMimeData, pyqtSignal, QThread
from PyQt6.QtGui import (
    QColor, QBrush, QDrag, QFont, QPainter, QPainterPath, QPainterPathStroker,
    QPen, QPixmap, QKeySequence, QShortcut, QCursor,
)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QLineEdit, QTextEdit, QFrame, QStackedWidget, QScrollArea, QMessageBox,
    QListWidget, QListWidgetItem, QFileDialog,
    QGraphicsItem, QGraphicsObject, QGraphicsPathItem, QGraphicsScene,
    QGraphicsView, QGraphicsProxyWidget, QSizePolicy, QTabBar, QInputDialog, QMenu, QDialog,
)

from ai.service import get_asset_db, get_ai_manager
from ai.providers.base import TaskRequest
from ai.assets import (
    Character, Scene, Element, approved_asset_path, asset_is_approved,
    approve_asset_version, assign_asset_view,
)
from ai.ui.resource_center import (
    AssetStudioDialog, PropertyInspector, KIND_META, DB_MAP,
)
from ai.storyboard import (
    extract_json, sync_legacy_bindings, rebuild_continuity,
    resolve_video_link_mode,
)
from ai.director_protocol import (
    normalize_director_contract,
    compile_video_direction, director_gate_issues, endpoint_pair_requested,
)
from ai.reference_assets import normalize_reference_assets
from ai.production_skills import (
    NEXT_ACTION_BY_STAGE,
    append_generation_event,
    append_workflow_event,
    build_repair_plan,
    evaluate_readiness,
    normalize_clip_qc,
    normalize_sequence_qc,
    plan_next_action,
    load_canvas_skill_specs,
    validate_skill_dependencies,
)
from ai.deterministic_qc import (
    compare_endpoint_paths, compare_fixed_regions, inspect_av_sync, inspect_frame_paths,
)
from ai.production_intelligence import rank_providers, shot_signature
from ai.production_runtime import recommend_provider, skill_runtime_issues
from ai.script_workbench import previous_script_version, save_script_version, script_metrics
from ai.generation_errors import moderation_failure, transient_gateway_failure
from ai.scene_contracts import consolidate_scene_specs, scene_location_key
from ai.scene_geometry import (
    SCENE_VIEW_SPECS, bind_scene_view, create_edit_region_mask,
    fixture_view_bboxes, normalize_bbox, normalize_scene_proxy,
    scene_proxy_signature, scene_proxy_issues,
)
from ai.scene_stage import active_camera, normalize_scene_stage, stage_shot_contract
from ai.storyboard_planning import (
    batch_key as storyboard_batch_key,
    checkpoint_matches as storyboard_checkpoint_matches,
    checkpoint_progress as storyboard_checkpoint_progress,
    foundation_repair_messages,
    foundation_messages as storyboard_foundation_messages,
    merge_checkpoint as merge_storyboard_checkpoint,
    new_planning_checkpoint,
    next_missing_batch as next_storyboard_batch,
    normalize_foundation as normalize_storyboard_foundation,
    normalize_shot_batch,
    parse_duration_seconds,
    planning_fingerprint,
    shot_batch_messages,
    shot_batch_repair_messages,
    shot_batch_ranges,
)
from ai.motion_storyboard import (
    assemble_motion_storyboard, inspect_motion_panels, motion_panel_prompt,
    motion_panels_ready,
)
from core.image_output_size import normalize_aspect_ratio, resolve_image_output_size


BG = "#101012"
PANEL = "#17171b"
CARD = "#1d1d22"
TEXT = "#e8e8ed"
MUTED = "#83838d"
ACCENT = "#6f8cff"
MOTION_STORYBOARD_CONTRACT_VERSION = 5
LAYOUT_FILE = Path(os.environ.get(
    "CEP_PRODUCTION_LAYOUT_FILE",
    str(Path(__file__).parents[2] / "work_temp" / "_production_canvas_layout.json"),
))
LAYOUT_SCHEMA = 2
PROJECT_FORMAT = "creative-engine-production-project"
PROJECT_VERSION = 1
ASSET_MIME = "application/x-creative-engine-asset"
_THUMB_CACHE: dict[tuple, QPixmap] = {}


class _VideoBreakdownWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, path: str, output_dir: str, parent=None):
        super().__init__(parent); self.path = path; self.output_dir = output_dir

    def run(self):
        try:
            from ai.video_breakdown import analyze_video
            self.completed.emit(analyze_video(self.path, self.output_dir))
        except Exception as error:
            self.failed.emit(str(error))

MANUAL_PRODUCTION_STEPS = (
    "1 · 拆解镜头（完成后停下确认）",
    "2 · 生成资产候选（逐项采用并锁定）",
    "3 · 生成调度与多帧运动分镜（完成后确认）",
    "4 · 确认调度并合成定稿提示词",
    "5 · 创建定稿图片生成器组",
    "6 · 确认定稿图片并生成视频",
    "7 · 创建对白音频组",
)

PRODUCTION_REWIND_STEPS = {
    1:("重新拆解镜头", "保留创意文字；清空镜头、生产资产、运动分镜及全部生成结果"),
    2:("重新生成资产", "保留镜头拆解；清空角色/场景/道具候选及其全部下游"),
    3:("重新生成调度与运动分镜", "保留已锁定资产；清空调度、运动分镜及其全部下游"),
    4:("重新编译定稿提示词", "保留运动分镜；清空定稿提示词、图片、视频和音频"),
    5:("重新生成定稿图片", "保留提示词与运动分镜；清空图片候选、定稿、视频和音频"),
    6:("重新生成视频", "保留定稿图片；清空旧视频和音频"),
    7:("重新生成对白音频", "保留定稿视频；只清空旧对白音频"),
}

CHARACTER_REFERENCE_SPECS = (
    ("portrait", "角色立绘",
     "单人全身角色立绘，正面自然站姿，从头到脚完整可见，中性纯色背景，清楚表现固定五官、发型、体型、服装、鞋履和配色"),
    ("face_closeup", "脸部近景",
     "同一角色的正面脸部高清近景，平静自然表情，无遮挡，准确保持脸型、五官比例、肤色、瞳色、发型与妆容"),
    ("expressions", "表情九宫格",
     "同一角色的脸部表情九宫格，依次展示平静、微笑、大笑、悲伤、哭泣、惊讶、愤怒、恐惧、怀疑；固定机位、光线、发型和身份"),
    ("turnaround", "完整多视角设定",
     "同一角色的权威多视角设定板：全身正面、全身左侧、全身背面、全身右侧，以及脸部正面、左右3/4、左右侧面；比例统一、无遮挡、中性背景"),
)

CHARACTER_REFERENCE_FORMATS = {
    "portrait": {"size": "1024x1536", "ratio": "2:3"},
    "face_closeup": {"size": "1024x1024", "ratio": "1:1"},
    "expressions": {"size": "1536x1024", "ratio": "3:2"},
    "turnaround": {"size": "1536x1024", "ratio": "3:2"},
}

SCENE_REFERENCE_FORMATS = {
    # Cinematic authority views share the final-frame aspect ratio so an edit
    # mask preserves pixel-space fixture positions without an implicit crop.
    "master":{"size":"1792x1024", "ratio":"16:9"},
    "reverse":{"size":"1792x1024", "ratio":"16:9"},
    "left":{"size":"1792x1024", "ratio":"16:9"},
    "right":{"size":"1792x1024", "ratio":"16:9"},
    "topdown":{"size":"1536x1024", "ratio":"3:2"},
}


def _cached_thumbnail(path: str, width: int, height: int) -> QPixmap:
    """缩略图在节点创建时读取一次；paintEvent 绝不触碰磁盘。"""
    if not path or not os.path.exists(path):
        return QPixmap()
    try:
        stat = os.stat(path)
        key = (os.path.abspath(path), stat.st_mtime_ns, stat.st_size, width, height)
    except OSError:
        return QPixmap()
    cached = _THUMB_CACHE.get(key)
    if cached is not None:
        return cached
    source = QPixmap(path)
    if source.isNull():
        result = QPixmap()
    else:
        # 保留原图比例与完整画面；节点绘制阶段再居中适配，禁止裁切和压扁。
        result = source.scaled(
            width, height, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
    if len(_THUMB_CACHE) >= 256:
        _THUMB_CACHE.pop(next(iter(_THUMB_CACHE)))
    _THUMB_CACHE[key] = result
    return result

NODE_STYLE = {
    "director": ("项目", "#ffb45c"),
    "scene": ("场景", "#48d597"),
    "character": ("主体", "#b98cff"),
    "element": ("元素", "#4fc4e8"),
    "shot": ("镜头", "#6f8cff"),
    "asset_view": ("视角", "#5aa7c8"),
    "asset_take": ("候选", "#d39a55"),
    "shot_take": ("结果", "#e06f9c"),
    "generation_task": ("AI 任务", "#f0a44b"),
    "text_node": ("剧本工作台", "#a7b0bd"),
    "storyboard_node": ("AI 故事板", "#89b8ff"),
    "workflow_group": ("工作流", "#65d6b2"),
    "image_node": ("图片", "#86a9c2"),
    "video_node": ("视频", "#8f87c9"),
    "video_analysis_node": ("AI 拉片", "#67c7d8"),
    "audio_node": ("音频", "#b887c9"),
    "skill_node": ("专业 Skill", "#f0b45f"),
}

# 大画布不能只靠一条细色边区分阶段；不同生产板块使用低饱和底色，
# 保持暗色界面同时让脚本、资产、图片、视频和音频在缩小后仍可辨认。
NODE_CARD_FILL = {
    "director":"#2b2219", "text_node":"#20252b", "storyboard_node":"#19283a",
    "workflow_group":"#172d28", "skill_node":"#2d2518",
    "scene":"#172a23", "character":"#271f31", "element":"#172830",
    "asset_view":"#192831", "asset_take":"#2c251a", "shot":"#1a2135",
    "shot_take":"#2d1d29", "generation_task":"#302518",
    "image_node":"#182830", "video_node":"#211f35",
    "video_analysis_node":"#173039", "audio_node":"#2b1e31",
}

CANVAS_SKILLS = load_canvas_skill_specs({
    "ai_director": {
        "title": "AI 导演质检（旧）", "description": "兼容旧工程；执行时使用视觉审片与局部修复流程",
        "hidden": True,
    },
    "storyboard": {
        "title": "故事板", "description": "把脚本展开为可执行的镜头故事板",
    },
    "blocking_storyboard": {
        "title": "调度与运动分镜", "description": "推演人物走位、视线、轴线、机位，并生成每镜多关键帧运动板",
    },
    "camera_grid_9": {
        "title": "多机位九宫格", "description": "同一场景生成 9 个不同机位与景别方案",
    },
    "continuity_grid_25": {
        "title": "25 宫格连贯分镜", "description": "生成连续动作与镜头节奏的 25 格低成本推演",
    },
    "character_sheet": {
        "title": "角色设定", "description": "生成正面、侧面、背面、表情与动作设定",
    },
    "relight": {
        "title": "电影级光影调整", "description": "保持内容不变，调整光位、反差、色温和氛围",
    },
    "emotion": {
        "title": "情绪调整", "description": "保持身份与构图，细化面部表情、姿态和情绪强度",
    },
})

VIEW_ROLE_LABELS = {
    "front": "正面", "three_quarter": "3/4视角", "side": "侧面", "back": "背面",
    "portrait":"角色立绘", "face_closeup":"脸部近景",
    "expression_sheet":"表情参考", "three_view_sheet":"完整三视图",
    "empty_plate": "无人空场", "camera_a": "A机位", "camera_b": "B机位",
    "reverse_a": "A反打", "reverse_b": "B反打", "detail": "特写",
}

DIRECT_REFERENCE_ROLES = {
    "character": "角色身份",
    "scene": "场景空间",
    "style": "视觉风格",
    "element": "指定元素",
    "reference": "普通参考",
}

IMAGE_EDIT_DEFAULTS = {
    "图片高清": "在不改变人物身份、内容和构图的前提下高清修复，提升真实细节、纹理和清晰度",
    "智能扩图": "扩展画面边界，保持原图主体、身份、动作、透视和光线不变，自然补全画面外的环境与细节",
    "移除背景": "只保留画面主体，完整保留发丝、半透明边缘、服装和物体细节，移除背景并输出透明背景 PNG",
}


class _InspectorPreviewLabel(QLabel):
    doubleClicked = pyqtSignal()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class _NodeTextEdit(QTextEdit):
    """阻止画布快捷键抢走文字编辑按键，并保持所属节点选中。"""
    editingStarted = pyqtSignal()
    editingStopped = pyqtSignal()
    canvasZoomRequested = pyqtSignal(float)

    def focusInEvent(self, event):
        self.editingStarted.emit()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self.editingStopped.emit()
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        # 明确保留系统文本快捷键，避免外层 QGraphicsView 抢走。
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            actions = {Qt.Key.Key_C:self.copy, Qt.Key.Key_V:self.paste,
                       Qt.Key.Key_X:self.cut, Qt.Key.Key_A:self.selectAll,
                       Qt.Key.Key_Z:self.undo, Qt.Key.Key_Y:self.redo}
            action = actions.get(event.key())
            if action is not None:
                action(); event.accept(); return
        # QTextEdit 在光标位于文本边界、没有字符可删时可能把按键继续交给
        # QGraphicsView；外层会把 Backspace/Delete 解释为删除节点。无论当前
        # 是否真的删到了字符，只要焦点在文字框里，这两个键都必须止步于此。
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            super().keyPressEvent(event)
            event.accept()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        """普通滚轮只阅读文字；Ctrl + 滚轮明确交还给画布缩放。

        QGraphicsProxyWidget 中 QTextEdit 的默认 wheelEvent 在滚动条到达边界时
        会把事件继续交给 QGraphicsView，结果是用户想读长文时画布突然缩放。
        这里直接驱动滚动条并始终消费普通滚轮，让交互边界保持稳定。
        """
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            angle = event.angleDelta().y() or event.angleDelta().x()
            pixel = event.pixelDelta().y() or event.pixelDelta().x()
            if angle:
                factor = 1.16 ** (float(angle) / 120.0)
            elif pixel:
                factor = 1.0025 ** float(pixel)
            else:
                event.ignore()
                return
            self.canvasZoomRequested.emit(factor)
            event.accept()
            return
        bar = self.verticalScrollBar()
        pixel = event.pixelDelta().y()
        angle = event.angleDelta().y()
        if pixel:
            bar.setValue(bar.value() - int(pixel))
        elif angle:
            steps = float(angle) / 120.0
            distance = max(24, int(bar.singleStep()) * 3)
            bar.setValue(bar.value() - round(steps * distance))
        event.accept()


class _EditorResizeHandle(QPushButton):
    """嵌入式编辑框的纵向拖拽手柄。"""
    heightCommitted = pyqtSignal(int)

    def __init__(self, editor: QTextEdit, panel: QWidget, parent=None):
        super().__init__("···  拖动调整文字区高度", parent)
        self.editor = editor
        self.panel = panel
        self._dragging = False
        self._start_y = 0.0
        self._start_height = 0
        self.setObjectName("editorResizeHandle")
        self.setFixedHeight(18)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip("上下拖动调整高度；双击自动适合当前文字")

    def _set_editor_height(self, height: int):
        height = max(140, min(520, int(height)))
        self.editor.setFixedHeight(height)
        self.panel.adjustSize()
        return height

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._start_y = float(event.globalPosition().y())
            self._start_height = self.editor.height()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            delta = float(event.globalPosition().y()) - self._start_y
            self._set_editor_height(self._start_height + int(delta))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            height = self._set_editor_height(self.editor.height())
            self.heightCommitted.emit(height)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            document_height = int(self.editor.document().size().height()) + 28
            height = self._set_editor_height(document_height)
            self.heightCommitted.emit(height)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


def _unique_existing(values) -> list[str]:
    return list(dict.fromkeys(
        str(value) for value in values
        if value and os.path.exists(str(value))))


def _asset_visual_entries(item) -> tuple[str, list[dict]]:
    """返回资产框缩略图和需要单独显示的固定视角/候选。

    主参考已经展示在资产框本身，不再重复生成一个“已采用候选”节点。
    """
    master = str(approved_asset_path(item) or "")
    views = dict(getattr(item, "reference_views", {}) or {})
    roles_by_path: dict[str, list[str]] = {}
    for role, value in views.items():
        path = str(value or "")
        if role == "master" or not path or not os.path.exists(path) or path == master:
            continue
        roles_by_path.setdefault(path, []).append(str(role))

    entries: list[dict] = []
    for path, roles in roles_by_path.items():
        entries.append({"path": path, "type": "fixed_view", "roles": roles})

    fixed_paths = set(roles_by_path)
    for path in _unique_existing(getattr(item, "reference_images", []) or []):
        if path == master or path in fixed_paths:
            continue
        entries.append({"path": path, "type": "candidate", "roles": []})
    thumbnail = master if master and os.path.exists(master) else (
        entries[0]["path"] if entries else "")
    return thumbnail, entries


def _short_id(path: str) -> str:
    return hashlib.sha1(str(path).encode("utf-8", "ignore")).hexdigest()[:12]


class CanvasEdgeItem(QGraphicsPathItem):
    def __init__(self, source, target, relation="dependency"):
        super().__init__()
        self.source = source
        self.target = target
        self.relation = relation
        self.setZValue(-20)
        self.setAcceptedMouseButtons(Qt.MouseButton.RightButton)
        self.update_path()

    def shape(self):
        # 连接线本身很细，扩大右键命中区域但不改变视觉宽度。
        stroker = QPainterPathStroker()
        stroker.setWidth(14.0)
        return stroker.createStroke(self.path())

    def contextMenuEvent(self, event):
        if self.relation == "workflow":
            menu = QMenu()
            action = menu.addAction("解除这条工作流连接")
            if menu.exec(event.screenPos()) == action:
                self.scene().owner.remove_workflow_edge(
                    self.source.node_id, self.target.node_id)
            event.accept()
            return
        asset_node = self.source if self.source.node_type in {
            "scene", "character", "element"} else (
            self.target if self.target.node_type in {"scene", "character", "element"} else None)
        shot_node = self.source if self.source.node_type == "shot" else (
            self.target if self.target.node_type == "shot" else None)
        if not asset_node or not shot_node:
            event.ignore()
            return
        menu = QMenu()
        action = menu.addAction("解除这条连接")
        chosen = menu.exec(event.screenPos())
        if chosen == action:
            self.scene().owner.unlink_asset_from_shot(
                asset_node.payload.get("kind", ""),
                asset_node.payload.get("asset_id", ""),
                shot_node.payload.get("shot_id", ""))
        event.accept()

    def update_path(self):
        start = self.source.port_scene_pos("output")
        end = self.target.port_scene_pos("input")
        dx = max(55.0, abs(end.x() - start.x()) * 0.42)
        path = QPainterPath(start)
        path.cubicTo(
            QPointF(start.x() + dx, start.y()),
            QPointF(end.x() - dx, end.y()), end)
        self.setPath(path)
        if self.relation == "sequence":
            pen = QPen(QColor("#69718c"), 2.0, Qt.PenStyle.DashLine)
        elif self.relation == "approved":
            pen = QPen(QColor("#4dbd86"), 2.2)
        elif self.relation == "result":
            pen = QPen(QColor("#b85f8d"), 1.8)
        elif self.relation == "workflow":
            pen = QPen(QColor("#7fa4ff"), 2.2)
        else:
            pen = QPen(QColor("#46506f"), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        self.setPen(pen)


class CanvasNodeItem(QGraphicsObject):
    """轻量节点；数据操作放在 ProductionCanvasTab，节点只负责呈现和手势。"""

    def __init__(self, owner, node_id: str, node_type: str, title: str,
                 subtitle: str = "", thumbnail: str = "", badge: str = "",
                 payload: dict | None = None):
        super().__init__()
        self.owner = owner
        self.node_id = node_id
        self.node_type = node_type
        self.title = title or "未命名"
        self.subtitle = subtitle or ""
        self.thumbnail = thumbnail or ""
        self.badge = badge or ""
        self.payload = dict(payload or {})
        self.connection_hover = False
        self.width = 270.0 if node_type == "shot" else 240.0
        self.height = 180.0 if node_type == "shot" else 156.0
        if node_type in ("asset_view", "asset_take", "shot_take"):
            self.width, self.height = 190.0, 148.0
        elif node_type in ("text_node", "storyboard_node", "workflow_group", "skill_node"):
            self.width, self.height = 360.0, 230.0
        elif node_type in ("image_node", "video_node"):
            self.width, self.height = 480.0, 318.0
        source_pixmap = QPixmap(self.thumbnail) if (
            self.thumbnail and os.path.exists(self.thumbnail)) else QPixmap()
        if not source_pixmap.isNull() and node_type in (
                "image_node", "video_node", "asset_view", "asset_take", "shot_take"):
            aspect = source_pixmap.width() / max(1, source_pixmap.height())
            if node_type in ("image_node", "video_node"):
                preview_width = 480.0 if aspect >= 1 else 340.0
                preview_height = max(210.0, min(440.0, preview_width / max(0.2, aspect)))
                self.width, self.height = preview_width, preview_height + 58.0
            else:
                preview_width = 240.0 if aspect >= 1 else 190.0
                preview_height = max(120.0, min(280.0, preview_width / max(0.2, aspect)))
                self.width, self.height = preview_width, preview_height + 58.0
        self._thumb_pixmap = _cached_thumbnail(
            self.thumbnail, max(320, int(self.width * 2)),
            max(240, int((self.height - 57) * 2)))
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
            QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        # 设备坐标缓存会在缩放倍率变化时按新分辨率重绘，文字不会被拉伸变糊。
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        tip = "双击打开" if node_type not in ("shot",) else "双击回到分镜详细编辑"
        if self.has_output_port():
            tip += "\n按住右侧连接点拖动，可自由连线"
        if self.has_input_port():
            tip += "\n拖线松开到卡片任意位置即可连接"
        self.setToolTip(tip)

    def boundingRect(self):
        # 给抗锯齿描边留出无效区域；否则拖动时旧位置边缘可能不会被重绘，形成残影。
        return QRectF(-3, -3, self.width + 6, self.height + 6)

    def has_input_port(self):
        return self.node_type in {
            "shot", "image_node", "video_node", "audio_node",
            "video_analysis_node",
        }

    def has_output_port(self):
        return self.node_type in {
            "director", "scene", "character", "element",
            "asset_view", "asset_take", "shot_take",
            "text_node", "storyboard_node", "workflow_group", "skill_node", "image_node", "video_node", "audio_node",
        }

    def port_local_pos(self, direction: str):
        return QPointF(0.0 if direction == "input" else self.width,
                       self.height * 0.5)

    def port_scene_pos(self, direction: str):
        return self.mapToScene(self.port_local_pos(direction))

    def hit_port(self, scene_pos: QPointF, direction: str, radius=20.0):
        if direction == "input" and not self.has_input_port():
            return False
        if direction == "output" and not self.has_output_port():
            return False
        local = self.mapFromScene(scene_pos)
        port = self.port_local_pos(direction)
        return ((local.x() - port.x()) ** 2 +
                (local.y() - port.y()) ** 2) <= radius ** 2

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        _label, accent = NODE_STYLE.get(self.node_type, ("节点", ACCENT))
        selected = self.isSelected()
        card_rect = QRectF(0, 0, self.width, self.height)
        fill_key = str(self.payload.get("asset_kind") or self.node_type)
        fill = QColor(NODE_CARD_FILL.get(fill_key, CARD))
        if selected:
            fill = fill.lighter(122)
        painter.setBrush(QBrush(fill))
        outline = "#8fb0ff" if self.connection_hover else (accent if selected else "#34343d")
        painter.setPen(QPen(QColor(outline),
                            2.8 if self.connection_hover else (2.2 if selected else 1.1)))
        painter.drawRoundedRect(card_rect, 10, 10)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(accent)))
        painter.drawRoundedRect(QRectF(0, 0, 7, self.height), 4, 4)

        # 强类型工作流端口。输入在左、输出在右；悬停命中区大于视觉圆点。
        painter.setPen(QPen(QColor("#111116"), 2.0))
        if self.has_input_port():
            painter.setBrush(QColor("#73a1ff"))
            painter.drawEllipse(self.port_local_pos("input"), 6.0, 6.0)
        if self.has_output_port():
            painter.setBrush(QColor(accent))
            painter.drawEllipse(self.port_local_pos("output"), 6.0, 6.0)

        if self.node_type in ("text_node", "storyboard_node", "workflow_group", "skill_node"):
            thumb_rect = QRectF()
        elif self.node_type in ("image_node", "video_node"):
            thumb_rect = QRectF(16, 42, self.width - 32, self.height - 58)
        else:
            thumb_rect = QRectF(17, 42, 82, self.height - 57)
        if self.node_type in ("text_node", "storyboard_node", "workflow_group", "skill_node"):
            pass
        elif not self._thumb_pixmap.isNull():
            pixmap_size = self._thumb_pixmap.size()
            scale = min(thumb_rect.width() / max(1, pixmap_size.width()),
                        thumb_rect.height() / max(1, pixmap_size.height()))
            draw_width = pixmap_size.width() * scale
            draw_height = pixmap_size.height() * scale
            fitted_rect = QRectF(
                thumb_rect.x() + (thumb_rect.width() - draw_width) / 2,
                thumb_rect.y() + (thumb_rect.height() - draw_height) / 2,
                draw_width, draw_height)
            painter.setBrush(QColor("#111116"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(thumb_rect, 7, 7)
            painter.drawPixmap(
                fitted_rect, self._thumb_pixmap, QRectF(self._thumb_pixmap.rect()))
            is_video_cover = (
                self.node_type == "video_node" or
                (self.node_type == "shot_take" and
                 str(self.payload.get("kind") or "") == "video") or
                (self.node_type == "shot" and bool(
                    (self.payload.get("shot") or {}).get("selected_video_asset"))))
            if is_video_cover:
                center = thumb_rect.center()
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(8, 8, 12, 165))
                painter.drawEllipse(center, 24.0, 24.0)
                play = QPainterPath()
                play.moveTo(center.x() - 6.0, center.y() - 10.0)
                play.lineTo(center.x() + 12.0, center.y())
                play.lineTo(center.x() - 6.0, center.y() + 10.0)
                play.closeSubpath()
                painter.setBrush(QColor("#f4f5fb"))
                painter.drawPath(play)
        else:
            self._paint_placeholder(painter, thumb_rect, accent)

        painter.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.DemiBold))
        painter.setPen(QColor(accent))
        painter.drawText(QRectF(17, 12, self.width - 34, 22),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         NODE_STYLE.get(self.node_type, ("节点", ACCENT))[0])
        if self.badge:
            badge_width = min(105.0, max(50.0, len(self.badge) * 9.0))
            badge_rect = QRectF(self.width - badge_width - 12, 10, badge_width, 22)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#18362c" if "定稿" in self.badge or "主参考" in self.badge or "采用" in self.badge
                                    else "#3a3020"))
            painter.drawRoundedRect(badge_rect, 6, 6)
            painter.setPen(QColor("#78d9ad" if "定稿" in self.badge or "主参考" in self.badge or "采用" in self.badge
                                  else "#d9b56b"))
            painter.setFont(QFont("Microsoft YaHei", 7))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, self.badge)

        if self.node_type in ("image_node", "video_node"):
            return
        text_x = 18.0 if self.node_type in ("text_node", "storyboard_node", "workflow_group", "skill_node") else 111.0
        text_width = self.width - text_x - 13
        painter.setPen(QColor(TEXT))
        painter.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.DemiBold))
        if self.node_type not in ("text_node", "storyboard_node", "workflow_group", "skill_node"):
            painter.drawText(QRectF(text_x, 46, text_width, 40),
                             Qt.TextFlag.TextWordWrap, self.title)
        painter.setPen(QColor(MUTED))
        painter.setFont(QFont("Microsoft YaHei UI", 8))
        painter.drawText(QRectF(text_x, 48 if self.node_type in ("text_node", "storyboard_node", "workflow_group", "skill_node") else 90,
                                text_width, self.height - (62 if self.node_type in ("text_node", "storyboard_node", "workflow_group", "skill_node") else 101)),
                         Qt.TextFlag.TextWordWrap, self.subtitle)

    @staticmethod
    def _paint_placeholder(painter, rect, accent):
        painter.setPen(QPen(QColor("#34343c"), 1))
        painter.setBrush(QColor(accent).darker(250))
        painter.drawRoundedRect(rect, 7, 7)
        painter.setPen(QColor(accent).lighter(125))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "AI")

    def itemChange(self, change, value):
        result = super().itemChange(change, value)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            scene = self.scene()
            if scene and hasattr(scene, "ensure_item_visible"):
                scene.ensure_item_visible(self)
            if scene and hasattr(scene, "update_edges"):
                scene.update_edges(self)
            if self.owner:
                self.owner.node_moved(self)
        return result

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.owner:
            self.owner.begin_node_move(self)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and self.owner:
            self.owner.end_node_move(self)

    def mouseDoubleClickEvent(self, event):
        self.owner.activate_node(self)
        event.accept()

    def contextMenuEvent(self, event):
        self.owner.show_node_context_menu(self, event.screenPos())
        event.accept()


class ProductionGraphicsScene(QGraphicsScene):
    BASE_RECT = QRectF(-2000, -1200, 16000, 9000)
    CONTENT_MARGIN = 1400.0

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.edges: list[CanvasEdgeItem] = []
        self.node_edges: dict[str, list[CanvasEdgeItem]] = {}
        self.selectionChanged.connect(owner.selection_changed)
        self.setSceneRect(QRectF(self.BASE_RECT))

    def ensure_rect(self, rect: QRectF, margin=0.0):
        """Grow the navigable scene to contain rect; never clip existing space."""
        if not isinstance(rect, QRectF) or not rect.isValid() or rect.isEmpty():
            return
        target = QRectF(rect)
        if margin:
            target.adjust(-margin, -margin, margin, margin)
        expanded = self.sceneRect().united(self.BASE_RECT).united(target)
        current = self.sceneRect()
        if (abs(expanded.left() - current.left()) > 0.5 or
                abs(expanded.top() - current.top()) > 0.5 or
                abs(expanded.right() - current.right()) > 0.5 or
                abs(expanded.bottom() - current.bottom()) > 0.5):
            self.setSceneRect(expanded)

    def ensure_item_visible(self, item):
        try:
            self.ensure_rect(item.sceneBoundingRect(), self.CONTENT_MARGIN)
        except RuntimeError:
            pass

    def ensure_content_bounds(self):
        bounds = self.itemsBoundingRect()
        if bounds.isValid() and not bounds.isEmpty():
            self.ensure_rect(bounds, self.CONTENT_MARGIN)

    def connect_nodes(self, source, target, relation="dependency"):
        edge = CanvasEdgeItem(source, target, relation)
        self.edges.append(edge)
        self.node_edges.setdefault(source.node_id, []).append(edge)
        self.node_edges.setdefault(target.node_id, []).append(edge)
        self.addItem(edge)

    def update_edges(self, node=None):
        edges = self.edges if node is None else self.node_edges.get(node.node_id, [])
        for edge in edges:
            edge.update_path()


class ProductionGraphicsView(QGraphicsView):
    zoomChanged = pyqtSignal(int)
    MIN_ZOOM = 0.05
    MAX_ZOOM = 3.5

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing |
            QPainter.RenderHint.SmoothPixmapTransform)
        # BoundingRect 会同时刷新节点移动前后的区域，避免 Minimal 模式留下旧帧残影。
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        self.setOptimizationFlags(
            QGraphicsView.OptimizationFlag.DontSavePainterState)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QColor(BG))
        self.setFrameShape(QFrame.Shape.NoFrame)
        # Infinite-canvas navigation uses Space + drag and wheel zoom.  Native
        # scrollbars consume screen space and suggest a bounded document, so
        # keep them hidden while retaining their internal values for panning.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setStyleSheet(
            "QGraphicsView{background:#101012;border:none;}"
            "QScrollBar:horizontal,QScrollBar:vertical{background:#151518;border:none;}"
            "QScrollBar::handle:horizontal,QScrollBar::handle:vertical{background:#34343c;"
            "border-radius:3px;min-width:30px;min-height:30px;}"
            "QScrollBar::add-line,QScrollBar::sub-line{width:0;height:0;}")
        self._panning = False
        self._pan_start = None
        self._pan_button = Qt.MouseButton.NoButton
        self._space_down = False
        self._wire_source = None
        self._wire_preview = None
        self._wire_hover_target = None

    def _set_interactive_quality(self, active: bool):
        self.setRenderHint(QPainter.RenderHint.Antialiasing, not active)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, not active)

    def _set_canvas_cursor(self, cursor):
        self.setCursor(cursor)
        self.viewport().setCursor(cursor)

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        minor = 32
        major = 160
        left = int(rect.left()) - int(rect.left()) % minor
        top = int(rect.top()) - int(rect.top()) % minor
        painter.setPen(QPen(QColor("#17171b"), 1))
        x = left
        while x < rect.right():
            painter.drawLine(QPointF(float(x), rect.top()),
                             QPointF(float(x), rect.bottom()))
            x += minor
        y = top
        while y < rect.bottom():
            painter.drawLine(QPointF(rect.left(), float(y)),
                             QPointF(rect.right(), float(y)))
            y += minor
        left = int(rect.left()) - int(rect.left()) % major
        top = int(rect.top()) - int(rect.top()) % major
        painter.setPen(QPen(QColor("#202027"), 1))
        x = left
        while x < rect.right():
            painter.drawLine(QPointF(float(x), rect.top()),
                             QPointF(float(x), rect.bottom()))
            x += major
        y = top
        while y < rect.bottom():
            painter.drawLine(QPointF(rect.left(), float(y)),
                             QPointF(rect.right(), float(y)))
            y += major

    def set_zoom(self, target: float, keep_center=False):
        current = float(self.transform().m11() or 1.0)
        target = max(self.MIN_ZOOM, min(self.MAX_ZOOM, float(target)))
        center = self.mapToScene(self.viewport().rect().center()) if keep_center else None
        if abs(target - current) > 0.0001:
            factor = target / current
            self.scale(factor, factor)
            if center is not None:
                self.centerOn(center)
            self._ensure_navigation_room()
        self.zoomChanged.emit(int(round(target * 100)))

    def zoom_by(self, factor: float, keep_center=True):
        self.set_zoom(self.transform().m11() * factor, keep_center=keep_center)

    def reset_zoom(self):
        center = self.mapToScene(self.viewport().rect().center())
        self.resetTransform()
        self.centerOn(center)
        self.zoomChanged.emit(100)

    def wheelEvent(self, event):
        # QGraphicsView 有时会先于 QGraphicsProxyWidget 收到滚轮事件。
        # 鼠标只要位于展开面板内，就优先滚动正文，避免必须精确对准滚动条。
        owner = getattr(self.scene(), "owner", None)
        if (not (event.modifiers() & Qt.KeyboardModifier.ControlModifier) and
                owner is not None and
                owner.scroll_inline_editor_at(self.mapToScene(event.position().toPoint()), event)):
            event.accept()
            return
        # 画布空白处直接滚轮缩放；Ctrl + 滚轮在任何位置都缩放。
        # 普通鼠标使用 angleDelta；触控板/高精度滚轮可能只提供 pixelDelta。
        angle = event.angleDelta().y() or event.angleDelta().x()
        pixel = event.pixelDelta().y() or event.pixelDelta().x()
        if angle:
            factor = 1.16 ** (float(angle) / 120.0)
        elif pixel:
            factor = 1.0025 ** float(pixel)
        else:
            event.ignore()
            return
        # 滚轮始终以指针位置为中心；达到边界时钳制，而不是忽略整次事件。
        self.zoom_by(factor, keep_center=False)
        event.accept()

    def _ensure_navigation_room(self):
        """Expand around the viewport so panning can continue in every direction."""
        scene = self.scene()
        if not hasattr(scene, "ensure_rect") or self.viewport().width() <= 0:
            return
        visible = self.mapToScene(self.viewport().rect()).boundingRect()
        padding = max(2000.0, visible.width(), visible.height())
        scene.ensure_rect(visible, padding)

    def mousePressEvent(self, event):
        scene_pos = self.mapToScene(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton:
            source = self.scene().owner.port_node_at(scene_pos, "output")
            if source is not None:
                self._wire_source = source
                self._wire_preview = QGraphicsPathItem()
                self._wire_preview.setZValue(-10)
                self._wire_preview.setPen(QPen(QColor("#7fa4ff"), 2.0,
                                               Qt.PenStyle.DashLine))
                self.scene().addItem(self._wire_preview)
                self._update_wire_preview(scene_pos)
                event.accept()
                return
        forced_space_pan = (
            event.button() == Qt.MouseButton.LeftButton and self._space_down)
        if (event.button() == Qt.MouseButton.MiddleButton or
                forced_space_pan):
            self._panning = True
            self._pan_button = event.button()
            self._pan_start = event.position().toPoint()
            self._set_interactive_quality(True)
            self._set_canvas_cursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._wire_source is not None:
            scene_pos = self.mapToScene(event.position().toPoint())
            self._update_wire_preview(scene_pos)
            self._set_wire_hover(self.scene().owner.port_node_at(scene_pos, "input"))
            event.accept()
            return
        if self._panning and self._pan_start is not None:
            delta = event.position().toPoint() - self._pan_start
            self._pan_start = event.position().toPoint()
            self._ensure_navigation_room()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._wire_source is not None:
            scene_pos = self.mapToScene(event.position().toPoint())
            target = self.scene().owner.port_node_at(scene_pos, "input")
            source = self._wire_source
            self._set_wire_hover(None)
            if self._wire_preview is not None:
                self.scene().removeItem(self._wire_preview)
            self._wire_source = None
            self._wire_preview = None
            if target is not None:
                self.scene().owner.connect_workflow_nodes(source, target)
            else:
                self.scene().owner.show_reference_generation_menu(
                    source, event.globalPosition().toPoint(), scene_pos)
            event.accept()
            return
        if event.button() == self._pan_button and self._panning:
            self._panning = False
            self._pan_start = None
            self._pan_button = Qt.MouseButton.NoButton
            self._set_interactive_quality(False)
            self._set_canvas_cursor(
                Qt.CursorShape.OpenHandCursor if self._space_down
                else Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _set_wire_hover(self, target):
        if target is self._wire_hover_target:
            return
        if self._wire_hover_target is not None:
            self._wire_hover_target.connection_hover = False
            self._wire_hover_target.update()
        self._wire_hover_target = target
        if target is not None:
            target.connection_hover = True
            target.update()

    def _update_wire_preview(self, end: QPointF):
        if self._wire_source is None or self._wire_preview is None:
            return
        start = self._wire_source.port_scene_pos("output")
        dx = max(55.0, abs(end.x() - start.x()) * 0.42)
        path = QPainterPath(start)
        path.cubicTo(QPointF(start.x() + dx, start.y()),
                     QPointF(end.x() - dx, end.y()), end)
        self._wire_preview.setPath(path)

    def contextMenuEvent(self, event):
        # 节点和连接线继续使用各自的右键菜单；空白区域提供新建资产入口。
        if self.itemAt(event.pos()) is None:
            self.scene().owner.show_new_asset_menu(
                event.globalPos(), self.mapToScene(event.pos()))
            event.accept()
            return
        super().contextMenuEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(ASSET_MIME):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(ASSET_MIME):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(ASSET_MIME):
            super().dropEvent(event)
            return
        try:
            payload = json.loads(bytes(event.mimeData().data(ASSET_MIME)).decode("utf-8"))
            kind = str(payload.get("kind") or "")
            asset_id = str(payload.get("asset_id") or "")
            if kind and asset_id:
                self.scene().owner.place_asset_on_canvas(
                    kind, asset_id,
                    self.mapToScene(event.position().toPoint()))
                event.acceptProposedAction()
                return
        except Exception:
            pass
        event.ignore()

    def keyPressEvent(self, event):
        owner = self.scene().owner
        editor = owner._inline_text_editor
        if owner._inline_editor_typing and editor is not None:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                actions = {Qt.Key.Key_C:editor.copy, Qt.Key.Key_V:editor.paste,
                           Qt.Key.Key_X:editor.cut, Qt.Key.Key_A:editor.selectAll,
                           Qt.Key.Key_Z:editor.undo, Qt.Key.Key_Y:editor.redo}
                action = actions.get(event.key())
                if action is not None:
                    action(); event.accept(); return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.key() == Qt.Key.Key_C:
                self.scene().owner.copy_selected_nodes()
                event.accept(); return
            if event.key() == Qt.Key.Key_V:
                self.scene().owner.paste_copied_nodes()
                event.accept(); return
            if event.key() == Qt.Key.Key_Z:
                self.scene().owner.undo_canvas_action()
                event.accept(); return
            if event.key() == Qt.Key.Key_Y:
                self.scene().owner.redo_canvas_move()
                event.accept(); return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            # 节点弹窗不只有主编辑器，还包含产品名、产品描述、创意提示词、
            # 自定义语言等输入控件。以真实键盘焦点为准，任何文字输入期间
            # 都禁止画布执行节点删除。
            focus = QApplication.focusWidget()
            if isinstance(focus, (QLineEdit, QTextEdit)):
                event.accept()
                return
            if self.scene().owner.handle_inline_text_delete(event.key()):
                event.accept()
                return
            self.scene().owner.delete_canvas_selection()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_down = True
            if not self._panning:
                self._set_canvas_cursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_down = False
            if not self._panning:
                self._set_canvas_cursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        self._space_down = False
        if not self._panning:
            self._set_canvas_cursor(Qt.CursorShape.ArrowCursor)
        super().focusOutEvent(event)

    def fit_nodes(self):
        if hasattr(self.scene(), "ensure_content_bounds"):
            self.scene().ensure_content_bounds()
        rect = self.scene().itemsBoundingRect().adjusted(-80, -80, 80, 80)
        if rect.isValid() and not rect.isEmpty():
            self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
            if self.transform().m11() > 1.0:
                self.resetTransform()
            self.zoomChanged.emit(int(round(self.transform().m11() * 100)))


class AssetLibraryCard(QFrame):
    """资产库卡片；既可点击放入，也可直接拖到画布指定位置。"""

    def __init__(self, owner, item, kind: str, on_canvas: bool, parent=None):
        super().__init__(parent)
        self.owner = owner
        self.item = item
        self.kind = kind
        self.on_canvas = on_canvas
        self._press_pos = None
        self.setObjectName("assetLibraryCard")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setStyleSheet(
            "QFrame#assetLibraryCard{background:#202026;border:1px solid #303039;"
            "border-radius:7px;}QFrame#assetLibraryCard:hover{border-color:#596da8;}"
            "QPushButton{padding:4px 7px;font-size:10px;}"
        )
        root = QHBoxLayout(self)
        root.setContentsMargins(7, 7, 7, 7)
        root.setSpacing(8)

        preview = QLabel()
        preview.setFixedSize(72, 58)
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        preview.setStyleSheet("background:#17171b;border:1px solid #2c2c33;border-radius:5px;color:#65708c;")
        path = approved_asset_path(item)
        if not path:
            refs = list((getattr(item, "reference_views", {}) or {}).values())
            refs.extend(getattr(item, "reference_images", []) or [])
            if isinstance(item, Element) and getattr(item, "master_image", ""):
                refs.insert(0, item.master_image)
            path = next((value for value in refs if value and os.path.exists(value)), "")
        pix = _cached_thumbnail(path, 72, 58)
        if pix.isNull():
            preview.setText("AI")
        else:
            preview.setPixmap(pix)
        root.addWidget(preview)

        info = QVBoxLayout()
        info.setSpacing(2)
        name = QLabel(str(getattr(item, "name", "") or "未命名资产"))
        name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        name.setToolTip(name.text())
        name.setStyleSheet("color:#eee;font-size:11px;font-weight:bold;border:none;background:transparent;")
        info.addWidget(name)
        kind_label = {"scene": "场景", "character": "主体", "element": "元素"}.get(kind, kind)
        approved = asset_is_approved(item, require_file=False)
        version = int(getattr(item, "version", 0) or 0)
        state = f"主参考 v{max(1, version)}" if approved else "还没有主参考"
        meta = QLabel(f"{kind_label} · {state}")
        meta.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        meta.setStyleSheet(
            "color:#63c996;font-size:9px;border:none;background:transparent;" if approved
            else "color:#b39a67;font-size:9px;border:none;background:transparent;")
        info.addWidget(meta)
        actions = QHBoxLayout()
        actions.setSpacing(4)
        place = QPushButton("定位" if on_canvas else "放入画布")
        place.clicked.connect(
            lambda: owner.focus_node(f"asset:{kind}:{item.id}") if on_canvas
            else owner.place_asset_on_canvas(kind, item.id))
        actions.addWidget(place)
        edit = QPushButton("编辑")
        edit.clicked.connect(lambda: owner.open_library_asset(kind, item.id))
        actions.addWidget(edit)
        delete = QPushButton("删除")
        delete.setToolTip("从资产库中删除这个资产")
        delete.setStyleSheet(
            "QPushButton{color:#d88b8b;}QPushButton:hover{border-color:#a95a5a;color:#ffaaaa;}"
        )
        delete.clicked.connect(
            lambda: owner.delete_library_asset(kind, item.id))
        actions.addWidget(delete)
        info.addLayout(actions)
        root.addLayout(info, 1)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self._press_pos is None or
                not event.buttons() & Qt.MouseButton.LeftButton or
                (event.position().toPoint() - self._press_pos).manhattanLength()
                < QApplication.startDragDistance()):
            super().mouseMoveEvent(event)
            return
        mime = QMimeData()
        mime.setData(ASSET_MIME, json.dumps({
            "kind": self.kind, "asset_id": self.item.id,
        }, ensure_ascii=False).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        path = approved_asset_path(self.item)
        pix = _cached_thumbnail(path, 120, 84)
        if not pix.isNull():
            drag.setPixmap(pix)
        drag.exec(Qt.DropAction.CopyAction)
        self._press_pos = None

    def mouseDoubleClickEvent(self, event):
        self.owner.open_library_asset(self.kind, self.item.id)
        event.accept()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        if self.on_canvas:
            menu.addAction(
                "在画布中定位",
                lambda: self.owner.focus_node(
                    f"asset:{self.kind}:{self.item.id}"))
        else:
            menu.addAction(
                "放入画布",
                lambda: self.owner.place_asset_on_canvas(
                    self.kind, self.item.id))
        menu.addAction(
            "编辑资产",
            lambda: self.owner.open_library_asset(
                self.kind, self.item.id))
        menu.addSeparator()
        menu.addAction(
            "从资产库删除…",
            lambda: self.owner.delete_library_asset(
                self.kind, self.item.id))
        menu.exec(event.globalPos())
        event.accept()


class AssetLibraryDrawer(QFrame):
    """画布内的持久资产库，不再把用户带去另一个工作台。"""

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner = owner
        self.setFixedWidth(292)
        self.setStyleSheet("QFrame{background:#17171b;border-right:1px solid #292930;}")
        root = QVBoxLayout(self)
        root.setContentsMargins(9, 9, 9, 9)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("资产库")
        title.setStyleSheet("color:#fff;font-size:14px;font-weight:bold;border:none;")
        header.addWidget(title)
        header.addStretch()
        close = QPushButton("收起")
        close.setFixedWidth(52)
        close.clicked.connect(lambda: owner.toggle_asset_library(False))
        header.addWidget(close)
        root.addLayout(header)

        hint = QLabel("只管理已从画布保存的资产，可拖回画布作为快照")
        hint.setStyleSheet("color:#74747f;font-size:9px;border:none;")
        root.addWidget(hint)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索资产名称或描述…")
        self.search.textChanged.connect(self.refresh)
        root.addWidget(self.search)

        filter_row = QHBoxLayout()
        self.tabs = QTabBar()
        self.tabs.setExpanding(True)
        for text, value in (("全部", "all"), ("场景", "scene"),
                            ("主体", "character"), ("元素", "element")):
            index = self.tabs.addTab(text)
            self.tabs.setTabData(index, value)
        self.tabs.currentChanged.connect(self.refresh)
        self.tabs.setStyleSheet(
            "QTabBar::tab{background:#202026;color:#8b8b95;padding:6px 8px;border:none;}"
            "QTabBar::tab:selected{background:#2a3044;color:#dfe7ff;}"
        )
        filter_row.addWidget(self.tabs, 1)
        root.addLayout(filter_row)

        self.scope = QComboBox()
        self.scope.addItem("全部资产", "all")
        self.scope.addItem("本项目资产", "project")
        self.scope.currentIndexChanged.connect(self.refresh)
        root.addWidget(self.scope)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget = QWidget()
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(7)
        self.list_layout.addStretch()
        self.scroll.setWidget(self.list_widget)
        root.addWidget(self.scroll, 1)


    def refresh(self, *_):
        while self.list_layout.count() > 1:
            entry = self.list_layout.takeAt(0)
            if entry.widget():
                entry.widget().deleteLater()
        kind_filter = self.tabs.tabData(self.tabs.currentIndex()) or "all"
        query = self.search.text().strip().lower()
        project_only = self.scope.currentData() == "project"
        project_ids = self.owner.canvas_asset_node_ids()
        count = 0
        for kind, items in self.owner.all_asset_groups().items():
            if kind_filter != "all" and kind != kind_filter:
                continue
            for item in items:
                node_id = f"asset:{kind}:{item.id}"
                if project_only and node_id not in project_ids:
                    continue
                haystack = " ".join((
                    str(getattr(item, "name", "") or ""),
                    str(getattr(item, "description", "") or ""),
                    " ".join(getattr(item, "tags", []) or []),
                )).lower()
                if query and query not in haystack:
                    continue
                self.list_layout.insertWidget(
                    self.list_layout.count() - 1,
                    AssetLibraryCard(self.owner, item, kind, node_id in project_ids))
                count += 1


class CanvasNavigatorPanel(QFrame):
    """左侧统一导航：画布节点目录 + 永久资产库。"""

    def __init__(self, owner, asset_library, parent=None):
        super().__init__(parent)
        self.owner = owner
        self.asset_library = asset_library
        self.setFixedWidth(292)
        self.setStyleSheet(
            "QFrame{background:#17171b;border-right:1px solid #292930;}"
            "QListWidget{background:#17171b;border:none;color:#d7d7dd;outline:none;}"
            "QListWidget::item{padding:9px 8px;border-radius:7px;margin:2px 0;}"
            "QListWidget::item:hover{background:#23232a;}"
            "QListWidget::item:selected{background:#2c3040;color:white;}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.tabs = QTabBar()
        self.tabs.setExpanding(True)
        self.tabs.addTab("画布")
        self.tabs.addTab("资产")
        self.tabs.setStyleSheet(
            "QTabBar{background:#151518;}"
            "QTabBar::tab{background:#151518;color:#8d8d96;padding:11px 18px;border:none;}"
            "QTabBar::tab:selected{color:white;background:#202026;"
            "border-bottom:2px solid #6f8cff;}"
        )
        root.addWidget(self.tabs)
        self.stack = QStackedWidget()
        outline = QWidget()
        ol = QVBoxLayout(outline)
        ol.setContentsMargins(10, 10, 10, 10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索画布节点…")
        self.search.textChanged.connect(self.refresh_outline)
        ol.addWidget(self.search)
        self.node_list = QListWidget()
        self.node_list.itemClicked.connect(
            lambda item: owner.focus_node(str(item.data(Qt.ItemDataRole.UserRole) or "")))
        ol.addWidget(self.node_list, 1)
        self.count_label = QLabel("0 个节点")
        self.count_label.setStyleSheet("color:#74747e;padding:4px;")
        ol.addWidget(self.count_label)
        self.stack.addWidget(outline)
        self.stack.addWidget(asset_library)
        root.addWidget(self.stack, 1)
        self.tabs.currentChanged.connect(self.stack.setCurrentIndex)

    def refresh_outline(self, *_):
        query = self.search.text().strip().casefold()
        selected_id = str(self.node_list.currentItem().data(
            Qt.ItemDataRole.UserRole)) if self.node_list.currentItem() else ""
        self.node_list.clear()
        labels = {
            "director": "▤", "text_node": "☰", "storyboard_node": "✦", "skill_node": "◆",
            "workflow_group": "⌘", "image_node": "▧",
            "video_node": "▶", "audio_node": "▥", "shot": "◫",
            "scene": "▣", "character": "◇", "element": "⬡",
            "shot_take": "◉", "asset_take": "◉", "asset_view": "◈",
            "generation_task": "✦",
        }
        count = 0
        for node in self.owner._nodes.values():
            text = f"{node.title} {node.subtitle}".casefold()
            if query and query not in text:
                continue
            item = QListWidgetItem(
                f"{labels.get(node.node_type, '•')}  {node.title}")
            item.setData(Qt.ItemDataRole.UserRole, node.node_id)
            item.setToolTip(node.subtitle)
            self.node_list.addItem(item)
            if node.node_id == selected_id:
                self.node_list.setCurrentItem(item)
            count += 1
        self.count_label.setText(f"{count} 个节点")
        if not count:
            empty = QListWidgetItem("画布为空 · 点击底部 ＋ 新建节点")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            empty.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.node_list.addItem(empty)


class CanvasContextInspector(QFrame):
    """候选、镜头和生成结果的上下文操作；资产表单沿用 PropertyInspector。"""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.setFixedWidth(340)
        self.setStyleSheet("QFrame{background:#17171b;border-left:1px solid #292930;}")
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(16, 16, 16, 16)
        self.root.setSpacing(10)
        self.show_hint("选择画布节点查看并执行下一步。")

    def _clear(self):
        while self.root.count():
            item = self.root.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def show_hint(self, text):
        self._clear()
        title = QLabel("制片检查器")
        title.setStyleSheet("color:#fff;font-size:15px;font-weight:bold;")
        self.root.addWidget(title)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet("color:#7e7e89;line-height:1.5;")
        self.root.addWidget(label)
        self.root.addStretch()

    def _title(self, title, sub=""):
        label = QLabel(title)
        label.setWordWrap(True)
        label.setStyleSheet("color:#fff;font-size:15px;font-weight:bold;")
        self.root.addWidget(label)
        if sub:
            desc = QLabel(sub)
            desc.setWordWrap(True)
            desc.setStyleSheet("color:#858590;font-size:11px;")
            self.root.addWidget(desc)

    def _button(self, text, callback, primary=False):
        button = QPushButton(text)
        button.setMinimumHeight(34)
        button.setStyleSheet(
            "QPushButton{background:%s;color:%s;border:1px solid %s;border-radius:6px;"
            "padding:7px;font-weight:%s;}QPushButton:hover{border-color:#6f8cff;}" %
            ("#315da8" if primary else "#24242b", "#fff" if primary else "#ddd",
             "#4678c7" if primary else "#393943", "bold" if primary else "normal"))
        button.clicked.connect(callback)
        self.root.addWidget(button)
        return button

    def _preview(self, path, kind="image"):
        label = _InspectorPreviewLabel()
        label.setFixedHeight(190)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(
            "background:#0d0d0f;border:1px solid #2d2d34;border-radius:7px;color:#777;")
        label.setCursor(Qt.CursorShape.PointingHandCursor)
        label.setToolTip("双击查看图片" if kind == "image" else "双击播放视频")
        if path and os.path.exists(path):
            if kind == "video":
                label.setText("▶ 视频结果\n\n双击播放")
            else:
                pixmap = QPixmap(path)
                if not pixmap.isNull():
                    label.setPixmap(pixmap.scaled(
                        300, 190, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation))
            label.doubleClicked.connect(
                lambda p=path, k=kind: self.owner.open_media_preview(p, k))
        else:
            label.setText("文件不存在")
        self.root.addWidget(label)
        return label

    def show_asset_take(self, node):
        self._clear()
        data = node.payload
        path = data.get("path", "")
        self._title(node.title, node.subtitle)
        self._preview(path, "image")
        is_fixed_view = data.get("reference_type") == "fixed_view"
        if data.get("approved"):
            status_text = "★ 当前主参考"
        elif is_fixed_view:
            roles = [VIEW_ROLE_LABELS.get(role, role)
                     for role in data.get("view_roles", [])]
            status_text = "固定视角 · " + (" / ".join(roles) or "已用于一致性参考")
        else:
            status_text = "候选图 · 尚未采用"
        status = QLabel(status_text)
        status.setStyleSheet("color:%s;font-weight:bold;" %
                             ("#67d8a2" if data.get("approved") else
                              "#67b9d8" if is_fixed_view else "#d1a867"))
        self.root.addWidget(status)
        if path and os.path.exists(path):
            self._button(
                "★ 设为主参考", lambda: self.owner.approve_take(node), primary=True)
        kind = data.get("kind")
        if kind in ("character", "scene"):
            row = QHBoxLayout()
            combo = QComboBox()
            options = (
                (("正面", "front"), ("3/4视角", "three_quarter"),
                 ("侧面", "side"), ("背面", "back"))
                if kind == "character" else
                (("无人空场", "empty_plate"), ("A机位", "camera_a"),
                 ("B机位", "camera_b"), ("A反打", "reverse_a"),
                 ("B反打", "reverse_b"), ("特写", "detail")))
            for text, value in options:
                combo.addItem(text, value)
            row.addWidget(combo, 1)
            save = QPushButton("保存视角")
            save.clicked.connect(
                lambda _=False, n=node, c=combo: self.owner.assign_take_view(
                    n, c.currentData(), c.currentText()))
            row.addWidget(save)
            box = QWidget(); box.setLayout(row)
            self.root.addWidget(box)
        self._button("用此图继续图生图", lambda: self.owner.open_take_in_studio(node))
        if not data.get("approved"):
            self._button(
                "移除这个固定视角" if is_fixed_view else "移除这个候选",
                lambda: self.owner.remove_asset_take(node))
        self._button("只保留主参考和固定视角",
                     lambda: self.owner.keep_only_asset_references(node))
        self.root.addStretch()

    def show_director(self, node):
        self._clear()
        self._title(node.title, node.subtitle)
        note = QLabel(
            "创意和导演负责生成结构；场景、主体、元素和镜头会继续留在当前画布。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#92929d;background:#202026;border-radius:6px;padding:10px;")
        self.root.addWidget(note)
        self._button("打开 AI 想法", lambda: self.owner.directorRequested.emit("idea"))
        self._button("打开 AI 导演", lambda: self.owner.directorRequested.emit("director"), True)
        self._button("打开详细分镜", lambda: self.owner.directorRequested.emit("storyboard"))
        self.root.addStretch()

    def show_shot(self, node):
        self._clear()
        shot = node.payload.get("shot", {})
        contract = normalize_director_contract(shot)
        number = int(shot.get("number", 0) or 0)
        self._title(f"镜头 {number:02d}", node.subtitle)
        scene = QLabel(str(shot.get("scene") or "未填写画面"))
        scene.setWordWrap(True)
        scene.setStyleSheet("color:#d7d7dc;background:#202026;border-radius:6px;padding:10px;")
        self.root.addWidget(scene)
        info = QLabel(
            f"时长 {float(shot.get('duration', 0) or 0):g}s\n"
            f"景别 {shot.get('shot_size', '—')}\n"
            f"机位 {shot.get('camera_slot') or shot.get('camera') or '—'}\n"
            f"连续组 {shot.get('continuity_group') or '—'}")
        info.setStyleSheet("color:#92929d;line-height:1.5;")
        self.root.addWidget(info)

        gate_issues = director_gate_issues(shot)
        gate = QLabel(
            "✓ 导演门禁已通过，可以进入关键帧生产"
            if not gate_issues else "⚠ 导演门禁未通过：" + "；".join(gate_issues))
        gate.setWordWrap(True)
        gate.setStyleSheet(
            "color:%s;background:%s;border:1px solid %s;border-radius:7px;padding:9px;"
            % (("#71d6a3", "#182b24", "#28533f") if not gate_issues else
               ("#efbd75", "#30271c", "#59452b")))
        self.root.addWidget(gate)

        motion_board = str(shot.get("motion_board_path") or shot.get("draft_panel") or "")
        if motion_board:
            board_note = QLabel(
                "分镜稿需要人工检查。若人物站位、场景结构、动作方向或画格数量错误，"
                "请先重新生成本镜；新稿会自动切换为当前版本，旧稿仍保留在历史中。")
            board_note.setWordWrap(True)
            board_note.setStyleSheet(
                "color:#efbd75;background:#30271c;border:1px solid #59452b;"
                "border-radius:7px;padding:9px;")
            self.root.addWidget(board_note)
            self._button(
                "↻ 重新生成本镜分镜稿",
                lambda: self.owner.reroll_canvas_storyboard_shot(node), True)

        section = QLabel("导演合同")
        section.setStyleSheet("color:#fff;font-size:13px;font-weight:bold;margin-top:5px;")
        self.root.addWidget(section)
        hint = QLabel("这些字段直接参与关键帧和视频提示词编译；每镜只保留一个主动作和一个主运镜。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#7f7f89;font-size:10px;")
        self.root.addWidget(hint)

        editor_style = (
            "QLineEdit,QTextEdit,QComboBox{background:#202026;color:#e3e3e8;"
            "border:1px solid #34343d;border-radius:5px;padding:6px;}"
            "QLineEdit:focus,QTextEdit:focus,QComboBox:focus{border-color:#6f8cff;}")
        editors = {}

        def add_line(label_text, key, placeholder=""):
            label = QLabel(label_text)
            label.setStyleSheet("color:#aaaab3;font-size:10px;margin-top:3px;")
            self.root.addWidget(label)
            edit = QLineEdit(str(contract.get(key) or ""))
            edit.setPlaceholderText(placeholder)
            edit.setStyleSheet(editor_style)
            self.root.addWidget(edit)
            editors[key] = edit

        def add_text(label_text, key, height=58, value=None):
            label = QLabel(label_text)
            label.setStyleSheet("color:#aaaab3;font-size:10px;margin-top:3px;")
            self.root.addWidget(label)
            edit = QTextEdit()
            edit.setPlainText(str(contract.get(key) if value is None else value))
            edit.setFixedHeight(height)
            edit.setStyleSheet(editor_style)
            self.root.addWidget(edit)
            editors[key] = edit

        add_line("故事功能", "story_function", "观众通过本镜新知道或感受到什么")
        add_text("视觉命题", "visual_thesis")
        add_line("动作起点", "action_start", "可见姿态、位置与朝向")
        add_line("唯一主动作", "primary_action", "一个动作、速度、方向")
        add_line("动作终点", "action_end", "动作结束后可见且稳定的状态")
        add_line("唯一主运镜", "dominant_camera_move", "固定机位，或一种推/拉/摇/移/跟")

        contract["scene_view_id"] = str(shot.get("scene_view_id") or "master")
        bbox = normalize_bbox(shot.get("editable_bbox_xy"))
        contract["editable_bbox_xy"] = ", ".join(f"{value:g}" for value in bbox)
        view_label = QLabel("绑定场景权威视角")
        view_label.setStyleSheet("color:#aaaab3;font-size:10px;margin-top:3px;")
        self.root.addWidget(view_label)
        scene_view = QComboBox()
        for role, label, _prompt in SCENE_VIEW_SPECS:
            if role != "topdown":
                scene_view.addItem(label, role)
        scene_view.setCurrentIndex(max(
            0, scene_view.findData(contract["scene_view_id"])))
        scene_view.setStyleSheet(editor_style)
        self.root.addWidget(scene_view)
        editors["scene_view_id"] = scene_view
        add_line("允许变化区域 x, y, w, h", "editable_bbox_xy",
                 "例如 0.2, 0.15, 0.5, 0.7；区域外固定结构不可改变")
        mask_hint = QLabel(
            "坐标以画面左上角为原点，范围 0–1。生成 K1/Klast 时只有此区域透明可编辑，"
            "区域外的洗衣机、桌子、吧台等固定结构将被空间 QC 比较。")
        mask_hint.setWordWrap(True)
        mask_hint.setStyleSheet("color:#7f7f89;font-size:10px;")
        self.root.addWidget(mask_hint)

        strategy_label = QLabel("关键帧策略")
        strategy_label.setStyleSheet("color:#aaaab3;font-size:10px;margin-top:3px;")
        self.root.addWidget(strategy_label)
        strategy = QComboBox()
        strategy.addItem("首帧驱动 · 简单稳定动作", "first_frame")
        strategy.addItem("首尾帧桥接 · 仅必要镜头（需保存确认）", "first_last")
        strategy.setCurrentIndex(max(0, strategy.findData(contract["keyframe_strategy"])))
        strategy.setStyleSheet(editor_style)
        self.root.addWidget(strategy)
        editors["keyframe_strategy"] = strategy

        add_text("连续性不变量（每行一条）", "continuity_invariants", 72,
                 "\n".join(contract["continuity_invariants"]))
        add_text("主要生成风险", "generation_risk")

        def save_contract():
            values = {}
            for key, editor in editors.items():
                if isinstance(editor, QComboBox):
                    values[key] = editor.currentData()
                elif isinstance(editor, QTextEdit):
                    values[key] = editor.toPlainText().strip()
                else:
                    values[key] = editor.text().strip()
            values["continuity_invariants"] = [
                value.strip() for value in
                str(values.get("continuity_invariants") or "").splitlines()
                if value.strip()]
            self.owner.update_shot_director_contract(data_id(node), values)

        self._button("保存导演合同并重新编译", save_contract, True)
        stage = shot.get("scene_stage") if isinstance(shot.get("scene_stage"), dict) else {}
        stage_status = (f"已绑定 v{int(stage.get('version') or 1)} · "
                        f"{len(stage.get('objects') or [])} 个对象 · "
                        f"{len(stage.get('cameras') or [])} 个机位"
                        if stage else "尚未建立 3D 权威站位")
        stage_note = QLabel("3D 导演台 · " + stage_status)
        stage_note.setWordWrap(True)
        stage_note.setStyleSheet(
            "color:#9fc2ff;background:#182334;border:1px solid #2f4c72;"
            "border-radius:7px;padding:8px;")
        self.root.addWidget(stage_note)
        self._button(
            "打开 3D 导演台 · 安排人物与机位",
            lambda: self.owner.open_scene_stage(data_id(node)), True)
        self._button(
            "识别并准备缺失素材",
            lambda: self.owner.prepareShotAssetsRequested.emit(data_id(node)), True)
        self._button("生成关键帧候选", lambda: self.owner.request_shot(node, "image"), True)
        self._button("基于采用图片继续图生图", lambda: self.owner.request_shot(node, "image_edit"))
        self._button("以采用图片生成视频", lambda: self.owner.request_shot(node, "video"))
        self._button("送到 PS 局部精修", lambda: self.owner.request_refine(node))
        self._button("打开详细分镜", lambda: self.owner.shotRequested.emit(data_id(node)))
        self.root.addStretch()

    def show_shot_take(self, node):
        self._clear()
        data = node.payload
        path = data.get("path", "")
        self._title(node.title, node.subtitle)
        self._preview(path, str(data.get("kind") or "image"))
        guard = getattr(self.owner, "_shot_take_block_reason", None)
        blocked_reason = str(guard(node) or "") if callable(guard) else ""
        asset = data.get("asset") if isinstance(data.get("asset"), dict) else {}
        is_motion_board = str(asset.get("subtype") or "") == "motion_storyboard"
        if is_motion_board:
            shot = self.owner._find_shot(data.get("shot_id")) or {}
            is_current = str(shot.get("motion_board_path") or "") == str(path or "")
            note = QLabel(
                "这是运动分镜候选，只控制动作、机位和节奏，不会被当作最终视频首帧。")
            note.setWordWrap(True)
            note.setStyleSheet(
                "color:#9ec5ff;background:#192635;border:1px solid #304b68;"
                "border-radius:7px;padding:8px;")
            self.root.addWidget(note)
            self._button(
                "✓ 当前采用的运动分镜" if is_current else "✓ 采用为当前运动分镜",
                lambda: self.owner.adopt_motion_storyboard_take(node), not is_current)
            self._button("移除这个候选", lambda: self.owner.remove_shot_result(node))
            self._button(
                "只保留这个候选",
                lambda: self.owner.keep_only_motion_storyboard_take(node))
            self.root.addStretch()
            return
        if blocked_reason:
            warning = QLabel(blocked_reason)
            warning.setWordWrap(True)
            warning.setStyleSheet(
                "color:#efbd75;background:#30271c;border:1px solid #59452b;"
                "border-radius:7px;padding:8px;")
            self.root.addWidget(warning)
        frame_role = str((data.get("asset") or {}).get("frame_role") or "")
        image_label = ("✓ 设为定稿结束帧" if frame_role == "end" else
                       "✓ 设为定稿起始帧" if frame_role == "start" else
                       "✓ 设为定稿图片")
        self._button(
            image_label if data.get("kind") == "image" else "✓ 设为定稿视频",
            lambda: self.owner.adopt_shot_take(node), not bool(blocked_reason))
        if data.get("kind") == "image" and not blocked_reason:
            self._button("用此图生成视频", lambda: self.owner.request_result_video(node))
            self._button("送到 PS 局部精修", lambda: self.owner.request_result_refine(node))
            self._button("保存到资产库…", lambda: self.owner.save_result_to_library(node))
        self._button("移除这个结果", lambda: self.owner.remove_shot_result(node))
        self._button("只保留这个结果", lambda: self.owner.keep_only_shot_result(node))
        self.root.addStretch()


def data_id(node: CanvasNodeItem) -> str:
    return str(node.payload.get("shot_id") or node.payload.get("asset_id") or "")


class ProductionCanvasTab(QWidget):
    """项目级 AI 制片画布；资源中心数据库成为其不可见的数据层。"""

    assetChanged = pyqtSignal(str)
    shotRequested = pyqtSignal(str)
    generateShotRequested = pyqtSignal(str, str)
    refineShotRequested = pyqtSignal(str)
    prepareShotAssetsRequested = pyqtSignal(str)
    storyboardMutated = pyqtSignal()
    shotTakeAdopted = pyqtSignal(str, str)
    directorRequested = pyqtSignal(str)
    sendToEditorRequested = pyqtSignal(object)
    projectLoaded = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db = get_asset_db()
        self._asset_studios: dict[str, AssetStudioDialog] = {}
        self._storyboard = None
        self._storyboard_override = False
        self._storyboard_provider = None
        self._task_provider = None
        self._last_task_signature = ()
        self._inline_editor_proxy = None
        self._inline_editor_node_id = ""
        self._inline_text_editor = None
        self._inline_editor_typing = False
        self._inline_editor_dirty = False
        self._standalone_tasks = {}
        self._canvas_storyboard_queue = []
        self._canvas_storyboard_previous = ""
        self._canvas_storyboard_source = ""
        self._canvas_character_queue = []
        self._canvas_storyboard_character_refs = []
        self._workflow_failed_nodes = set()
        self._serial_video_queues = {}
        self._auto_continue_pending = set()
        self._preview_render_process = None
        self._preview_render_output = ""
        self._preview_render_timer = QTimer(self)
        self._preview_render_timer.setInterval(500)
        self._preview_render_timer.timeout.connect(self._poll_combined_preview_render)
        self._canvas_clipboard = []
        self._delete_undo = []
        self._position_undo = []
        self._position_redo = []
        self._move_drag_before = None
        self._canvas_action_serial = 0
        self._layout_store = self._load_layout_store()
        self._restore_storyboard_snapshot()
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.setInterval(350)
        self._layout_timer.timeout.connect(self._save_layout_now)
        self._checkpoint_timer = QTimer(self)
        self._checkpoint_timer.setInterval(5000)
        self._checkpoint_timer.timeout.connect(self._save_layout_now)
        self._checkpoint_timer.start()
        self.storyboardMutated.connect(self._save_layout_now)
        self._nodes: dict[str, CanvasNodeItem] = {}
        self._default_positions: dict[str, QPointF] = {}
        self._active_filter = "all"
        self._initial_view_ready = False
        self._refreshing = False
        self._last_db_signature = None
        self._build_ui()
        self._save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self._save_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._save_shortcut.activated.connect(self.save_canvas_project)
        self._save_as_shortcut = QShortcut(QKeySequence("Ctrl+Shift+S"), self)
        self._save_as_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._save_as_shortcut.activated.connect(
            lambda: self.save_canvas_project(save_as=True))
        self._new_project_shortcut = QShortcut(QKeySequence.StandardKey.New, self)
        self._new_project_shortcut.setContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._new_project_shortcut.activated.connect(self.new_canvas_project)
        self._open_project_shortcut = QShortcut(QKeySequence.StandardKey.Open, self)
        self._open_project_shortcut.setContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._open_project_shortcut.activated.connect(self.open_canvas_project)
        self._task_timer = QTimer(self)
        self._task_timer.setInterval(650)
        self._task_timer.timeout.connect(self._poll_task_nodes)
        self._task_timer.timeout.connect(self._poll_standalone_tasks)
        self._task_timer.start()
        self.refresh()
        self._recover_production_batches()

    def _build_ui(self):
        self.setStyleSheet(
            "QWidget{font-family:'Microsoft YaHei';color:#ddd;}"
            "QLineEdit,QComboBox{background:#202026;color:#ddd;border:1px solid #383842;"
            "border-radius:5px;padding:5px 8px;}"
            "QPushButton{background:#24242a;color:#d8d8dd;border:1px solid #393942;"
            "border-radius:5px;padding:6px 10px;}QPushButton:hover{border-color:#6f8cff;}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        self.scene = ProductionGraphicsScene(self)
        self.view = ProductionGraphicsView(self.scene)
        self.asset_library = AssetLibraryDrawer(self)
        self.navigator_panel = CanvasNavigatorPanel(self, self.asset_library)
        content.addWidget(self.navigator_panel)
        content.addWidget(self.view, 1)

        self.asset_inspector = PropertyInspector(self.db, compact=True)
        self.asset_inspector.setFixedWidth(520)
        self.asset_inspector._collapse.hide()
        self.asset_inspector.saved.connect(self._asset_saved)
        self.asset_inspector.studioRequested.connect(self._open_asset_studio)
        self.asset_inspector.removeRequested.connect(
            lambda item, kind: self.remove_asset_from_canvas(
                kind, str(getattr(item, "id", "") or "")))
        self.asset_inspector.deleteRequested.connect(
            lambda item, kind: self.delete_library_asset(
                kind, str(getattr(item, "id", "") or "")))
        self.context_inspector = CanvasContextInspector(self)
        body = QWidget(); body.setLayout(content)
        root.addWidget(body, 1)

        self.canvas_drawer = self._build_canvas_drawer()
        self.create_dock = self._build_create_dock()
        root.addWidget(self.create_dock)

    def _build_canvas_drawer(self):
        drawer = QDialog(self)
        drawer.setObjectName("canvasPopup")
        drawer.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        drawer.setModal(False)
        drawer.resize(680, 640)
        drawer.setStyleSheet(
            "QDialog#canvasPopup{background:#18181e;border:1px solid #444451;"
            "border-radius:18px;}"
            "QLabel#drawerTitle{color:#f2f2f6;font-size:14px;font-weight:bold;}"
            "QPushButton{background:#25252d;color:#dedee5;border:1px solid #3a3a46;"
            "border-radius:8px;padding:7px 12px;}"
            "QPushButton:hover{background:#30303b;border-color:#6f8cff;color:white;}"
        )
        root = QVBoxLayout(drawer)
        root.setContentsMargins(22, 16, 22, 20)
        root.setSpacing(12)
        head = QHBoxLayout()
        self.drawer_title = QLabel("画布创作")
        self.drawer_title.setObjectName("drawerTitle")
        head.addWidget(self.drawer_title)
        head.addStretch()
        close = QPushButton("关闭  ×")
        close.clicked.connect(drawer.close)
        head.addWidget(close)
        root.addLayout(head)
        self.drawer_stack = QStackedWidget()
        self.drawer_asset_page = QWidget()
        asset_layout = QHBoxLayout(self.drawer_asset_page)
        asset_layout.setContentsMargins(0, 0, 0, 0)
        asset_layout.addStretch()
        asset_layout.addWidget(self.asset_inspector)
        asset_layout.addStretch()
        self.drawer_director_page = QWidget()
        self.drawer_director_layout = QVBoxLayout(self.drawer_director_page)
        self.drawer_director_layout.setContentsMargins(0, 0, 0, 0)
        self.drawer_stack.addWidget(self.drawer_asset_page)
        self.drawer_stack.addWidget(self.drawer_director_page)
        root.addWidget(self.drawer_stack, 1)
        drawer.hide()
        return drawer

    def _show_canvas_popup(self):
        parent_window = self.window()
        if parent_window:
            available = parent_window.rect()
            width = min(760, max(620, available.width() - 96))
            height = min(720, max(500, available.height() - 112))
            self.canvas_drawer.resize(width, height)
            center = parent_window.frameGeometry().center()
            rect = self.canvas_drawer.frameGeometry()
            rect.moveCenter(center)
            self.canvas_drawer.move(rect.topLeft())
        self.canvas_drawer.show()
        self.canvas_drawer.raise_()
        self.canvas_drawer.activateWindow()

    def set_director_widget(self, widget):
        """把旧“写故事/AI 分镜”入口收编为画布内导演抽屉。"""
        if widget is None:
            return
        widget.setParent(self.drawer_director_page)
        self.drawer_director_layout.addWidget(widget)

    def show_director_drawer(self):
        self.drawer_title.setText("AI 导演 · 故事与分镜")
        self.drawer_stack.setCurrentWidget(self.drawer_director_page)
        self._show_canvas_popup()

    def show_asset_drawer(self, title="资产设置"):
        self.drawer_title.setText(title)
        self.drawer_stack.setCurrentWidget(self.drawer_asset_page)
        self._show_canvas_popup()

    def _build_create_dock(self):
        """底部 LibTV 风格的新建坞栏，所有创作入口围绕画布组织。"""
        host = QFrame()
        host.setFixedHeight(94)
        host.setStyleSheet("QFrame{background:#101012;border:none;}")
        outer = QHBoxLayout(host)
        outer.setContentsMargins(24, 10, 24, 14)
        outer.addStretch()
        pill = QFrame()
        pill.setObjectName("createDockPill")
        pill.setStyleSheet(
            "QFrame#createDockPill{background:#1b1b21;border:1px solid #34343f;"
            "border-radius:16px;}"
            "QPushButton{background:transparent;border:none;border-radius:10px;"
            "padding:10px 16px;color:#e1e1e7;font-size:12px;font-weight:600;}"
            "QPushButton:hover{background:#2b2b34;color:#fff;}"
            "QPushButton#newPrimary{background:#6f8cff;color:white;font-weight:bold;"
            "padding:9px 18px;}QPushButton#newPrimary:hover{background:#819cff;}"
        )
        row = QHBoxLayout(pill)
        row.setContentsMargins(8, 7, 8, 7)
        row.setSpacing(3)

        primary = QPushButton("＋ 新建")
        primary.setObjectName("newPrimary")
        primary.clicked.connect(lambda: self.show_new_asset_menu(
            primary.mapToGlobal(primary.rect().topLeft())))
        row.addWidget(primary)

        def add(text, tip, callback):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.setMinimumHeight(42)
            button.clicked.connect(lambda _=False, fn=callback: fn())
            row.addWidget(button)
            return button

        self.production_continue_btn = add(
            "▶ 开始制片", "从创意开始自动推进；只在需要你挑选候选时暂停",
            self.continue_current_production)
        self.production_rewind_btn = add(
            "↶ 重做", "从第 1–7 步中的任一步重新开始；只清理该步及其下游",
            lambda: self.show_production_rewind_menu(
                self.production_rewind_btn.mapToGlobal(
                    self.production_rewind_btn.rect().topLeft())))
        self.project_menu_btn = add(
            "▣ 工程", "新建、另存为、打开或切换最近的制片工程",
            lambda: self.show_project_menu(
                self.project_menu_btn.mapToGlobal(
                    self.project_menu_btn.rect().topLeft())))
        self.import_menu_btn = add(
            "⇧ 导入", "把本地图片、视频或音频直接放到画布",
            lambda: self.show_canvas_import_menu(
                self.import_menu_btn.mapToGlobal(
                    self.import_menu_btn.rect().topLeft())))
        add("▦ 整理", "整理画布：每镜一列，分镜、图片、视频和音频归到对应镜头下方", self.auto_layout)
        add("▣ 资产", "展开或收起资产库",
            lambda: self.toggle_asset_library(self.navigator_panel.isHidden()))
        outer.addWidget(pill)
        outer.addStretch()
        return host

    def show_project_menu(self, screen_pos=None):
        """Compact project switcher; node creation remains in the ＋ menu."""
        menu = QMenu(self)
        self._style_popup_menu(menu)
        board = self.current_storyboard()
        current_id = str(board.get("id") or self._project_key())
        current_file = str(self._positions().get("__project_file__") or "")
        current = menu.addAction(
            f"当前 · {board.get('title') or '未命名制片工程'}")
        current.setEnabled(False)
        if current_file:
            current.setToolTip(current_file)
        menu.addSeparator()
        new_action = menu.addAction("＋  新建制片工程\tCtrl+N")
        rename_action = menu.addAction("✎  重命名当前工程…")
        save_action = menu.addAction("💾  保存工程\tCtrl+S")
        save_as_action = menu.addAction("另存为…\tCtrl+Shift+S")
        open_action = menu.addAction("📂  打开工程…\tCtrl+O")
        menu.addSeparator()
        recent_menu = menu.addMenu("◷  最近工程")
        recent_actions = {}
        recent = self._recent_projects()
        if not recent:
            empty = recent_menu.addAction("暂无最近工程")
            empty.setEnabled(False)
        current_path_key = self._normalized_project_path(current_file)
        for value in recent:
            path = str(value.get("path") or "")
            project_id = str(value.get("project_id") or "")
            local_available = bool(
                project_id and isinstance(self._layout_store.get(project_id), dict))
            file_available = bool(path and os.path.exists(path))
            label = str(value.get("title") or "未命名制片工程")
            if path:
                label = f"▤  {label}"
            else:
                label = f"◷  {label} · 本机快照"
            is_current = bool(
                project_id == current_id and
                ((not path and not current_file) or
                 (path and self._normalized_project_path(path) == current_path_key)))
            if is_current:
                label += "  ✓"
            elif path and not file_available and local_available:
                label += "  · 文件缺失，使用快照"
            elif not file_available and not local_available:
                label += "  · 不可用"
            action = recent_menu.addAction(label)
            action.setToolTip(path or "保存在本机自动恢复记录中")
            action.setEnabled(not is_current and (file_available or local_available))
            recent_actions[action] = value

        if screen_pos is None:
            button = getattr(self, "project_menu_btn", None)
            screen_pos = (button.mapToGlobal(button.rect().topLeft())
                          if button is not None else self.mapToGlobal(self.rect().center()))
        chosen = menu.exec(screen_pos)
        if chosen == new_action:
            self.new_canvas_project()
        elif chosen == rename_action:
            self.rename_canvas_project()
        elif chosen == save_action:
            self.save_canvas_project()
        elif chosen == save_as_action:
            self.save_canvas_project(save_as=True)
        elif chosen == open_action:
            self.open_canvas_project()
        elif chosen in recent_actions:
            self.open_recent_project(recent_actions[chosen])

    def _activate_canvas_project(self, board: dict, project_id: str):
        """Switch the visible graph without deleting any other stored project."""
        project_id = str(project_id or board.get("id") or "")
        if not project_id:
            return False
        board["id"] = project_id
        project = self._layout_store.setdefault(project_id, {})
        project["__storyboard_snapshot__"] = json.loads(
            json.dumps(board, ensure_ascii=False))
        self._layout_store["__last_project__"] = project_id
        self._storyboard = json.loads(json.dumps(board, ensure_ascii=False))
        self._storyboard_override = True
        self._initial_view_ready = False
        self._last_task_signature = ()
        self._delete_undo.clear()
        self._position_undo.clear()
        self._position_redo.clear()
        self._move_drag_before = None
        self._workflow_failed_nodes.clear()
        self.refresh()
        self._recover_production_batches()
        loaded_board = self.current_storyboard()
        self.projectLoaded.emit(loaded_board)
        project_file = str(self._positions().get("__project_file__") or "")
        self._remember_recent_project(
            project_file, str(loaded_board.get("title") or "未命名制片工程"),
            project_id)
        self._save_layout_now()
        self.view.fit_nodes()
        return True

    def new_canvas_project(self, confirm=True, show_message=True):
        """Create an isolated canvas while retaining the previous project."""
        if not self._prepare_project_switch("新建工程", confirm=confirm):
            return False
        self._stop_canvas_tasks_for_project_switch()
        import uuid
        project_id = f"canvas_{uuid.uuid4().hex[:12]}"
        starter_id = f"custom:{uuid.uuid4().hex[:12]}"
        board = {
            "id": project_id,
            "title": "未命名制片工程",
            "shots": [],
        }
        self._layout_store[project_id] = {
            "__custom_nodes__": [{
                "id": starter_id,
                "type": "storyboard_node",
                "title": "AI 制片项目",
                "content": "",
                "path": "",
                "style": "电影写实",
                "shot_count": 0,
                "automation_mode": "checkpoints",
                "candidate_count": 2,
                "video_candidate_count": 2,
            }],
            "__workflow_edges__": [],
            "__production_batches__": [],
            "__storyboard_snapshot__": json.loads(
                json.dumps(board, ensure_ascii=False)),
            starter_id: [0.0, 0.0],
        }
        activated = self._activate_canvas_project(board, project_id)
        if activated and starter_id in self._nodes:
            self.focus_node(starter_id)
        if activated and show_message:
            button = getattr(self, "project_menu_btn", None)
            if button is not None:
                button.setToolTip(
                    "已新建独立工程；上一张画布没有删除，可从最近工程切回")
        return activated

    def rename_canvas_project(self, title: str = ""):
        board = self.current_storyboard()
        if not board:
            return False
        if not title:
            title, accepted = QInputDialog.getText(
                self, "重命名工程", "工程名称：",
                text=str(board.get("title") or "未命名制片工程"))
            if not accepted:
                return False
        title = str(title or "").strip()
        if not title:
            QMessageBox.information(self, "重命名工程", "工程名称不能为空。")
            return False
        board["title"] = title
        project_file = str(self._positions().get("__project_file__") or "")
        self._remember_recent_project(
            project_file, title, str(board.get("id") or self._project_key()))
        self.projectLoaded.emit(board)
        self._save_layout_now()
        button = getattr(self, "project_menu_btn", None)
        if button is not None:
            button.setToolTip(f"当前工程：{title}")
        return True

    def switch_to_internal_project(self, project_id: str, confirm=True,
                                   show_message=True):
        """Restore a local autosave project that has not been written to a file."""
        project_id = str(project_id or "")
        project = self._layout_store.get(project_id)
        snapshot = (project.get("__storyboard_snapshot__")
                    if isinstance(project, dict) else None)
        if not isinstance(snapshot, dict) or not snapshot:
            if show_message:
                QMessageBox.warning(self, "工程不可用", "找不到这个工程的本机快照。")
            return False
        if project_id == self._project_key():
            return True
        if not self._prepare_project_switch("切换工程", confirm=confirm):
            return False
        self._stop_canvas_tasks_for_project_switch()
        board = json.loads(json.dumps(snapshot, ensure_ascii=False))
        return self._activate_canvas_project(board, project_id)

    def open_recent_project(self, value: dict):
        path = str(value.get("path") or "")
        if path and os.path.exists(path):
            return self.open_canvas_project(path)
        return self.switch_to_internal_project(str(value.get("project_id") or ""))

    def _viewport_center(self):
        return self.view.mapToScene(self.view.viewport().rect().center())

    def new_shot(self):
        board = self.current_storyboard()
        if not isinstance(board, dict) or not board:
            QMessageBox.information(self, "新建镜头", "请先从“新建”创建故事或进入 AI 导演。")
            self.directorRequested.emit("director")
            return
        shots = board.setdefault("shots", [])
        import uuid
        shot_id = uuid.uuid4().hex[:12]
        shots.append({
            "id": shot_id, "number": len(shots) + 1, "duration": 5.0,
            "scene": "新镜头", "shot_size": "中景", "camera_slot": "",
            "assets": [], "selected_asset": "", "preview_asset": "",
            "selected_image_asset": "", "selected_video_asset": "",
            "anchor_frame_id": "", "status": "draft",
        })
        rebuild_continuity(board)
        self._positions()[f"shot:{shot_id}"] = [
            round(self._viewport_center().x(), 2), round(self._viewport_center().y(), 2)]
        self._save_layout_now()
        self.storyboardMutated.emit()
        self.refresh()
        self.focus_node(f"shot:{shot_id}")

    def open_handdraw_storyboard(self):
        if not isinstance(self.current_storyboard(), dict) or not self.current_storyboard():
            import uuid
            self._storyboard = {"id":f"canvas_{uuid.uuid4().hex[:10]}",
                                "title":"未命名短片", "shots":[]}
        self.create_custom_node("storyboard_node", self._viewport_center(), {
            "title": "AI 故事板 · 画布制片中心", "content": "", "shot_count": 0,
            "style": "电影写实", "automation_mode":"checkpoints",
            "candidate_count":2, "video_candidate_count":2,
        })

    def set_storyboard_provider(self, provider):
        self._storyboard_provider = provider

    def set_task_provider(self, provider):
        """注入生成任务快照；画布只读，不接管底层调度。"""
        self._task_provider = provider
        self._last_task_signature = ()
        self._poll_task_nodes()

    def _task_snapshots(self):
        if not self._task_provider:
            return []
        try:
            values = self._task_provider() or []
            return [value for value in values if isinstance(value, dict)]
        except Exception:
            return []

    @staticmethod
    def _task_signature(tasks):
        result = []
        for task in tasks:
            handle = task.get("handle")
            result.append((
                str(getattr(handle, "id", "") or task.get("id", "")),
                str(getattr(getattr(handle, "status", None), "name", "")),
                int(float(getattr(handle, "progress", 0.0) or 0.0) * 10),
            ))
        return tuple(sorted(result))

    def _poll_task_nodes(self):
        tasks = self._task_snapshots()
        signature = self._task_signature(tasks)
        if signature != self._last_task_signature:
            self._last_task_signature = signature
            self.refresh()

    def set_storyboard(self, board):
        self._storyboard_override = False
        if board is self._storyboard:
            return
        self._position_undo.clear()
        self._position_redo.clear()
        self._move_drag_before = None
        self._storyboard = board
        self.refresh()

    def current_storyboard(self):
        if self._storyboard_provider and not self._storyboard_override:
            try:
                board = self._storyboard_provider()
                # An empty workbench on application startup must not erase a
                # crash-recovered canvas project.
                if isinstance(board, dict) and board:
                    self._storyboard = board
            except Exception:
                pass
        return self._storyboard if isinstance(self._storyboard, dict) else {}

    def _project_key(self):
        board = self.current_storyboard()
        return str(board.get("id") or "assets_only")

    @staticmethod
    def _load_layout_store():
        for path in (LAYOUT_FILE, LAYOUT_FILE.with_suffix(".json.bak")):
            try:
                if path.exists():
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and data.get("__schema__") == LAYOUT_SCHEMA:
                        return data
            except Exception:
                continue
        return {"__schema__": LAYOUT_SCHEMA}

    def _restore_storyboard_snapshot(self):
        """Restore the last durable board before any UI/provider refresh."""
        project_key = str(self._layout_store.get("__last_project__") or "")
        project = self._layout_store.get(project_key, {}) if project_key else {}
        snapshot = project.get("__storyboard_snapshot__") if isinstance(project, dict) else None
        if isinstance(snapshot, dict) and snapshot:
            # Detach runtime mutations from the parsed persistence structure.
            self._storyboard = json.loads(json.dumps(snapshot, ensure_ascii=False))

    def _positions(self):
        return self._layout_store.setdefault(self._project_key(), {})

    def _recent_projects(self):
        """Return normalized recent entries without coupling them to one canvas."""
        result = []
        for value in self._layout_store.get("__recent_projects__", []):
            if not isinstance(value, dict):
                continue
            project_id = str(value.get("project_id") or "")
            path = str(value.get("path") or "")
            if not project_id and not path:
                continue
            result.append({
                "project_id": project_id,
                "title": str(value.get("title") or "未命名制片工程"),
                "path": path,
                "last_opened": str(value.get("last_opened") or ""),
            })
        return result[:10]

    @staticmethod
    def _normalized_project_path(path: str):
        if not path:
            return ""
        try:
            return os.path.normcase(os.path.abspath(path))
        except Exception:
            return str(path).casefold()

    def _remember_recent_project(self, path: str = "", title: str = "",
                                 project_id: str = ""):
        board = self.current_storyboard()
        project_id = str(project_id or board.get("id") or self._project_key())
        title = str(title or board.get("title") or "未命名制片工程")
        path = str(path or "")
        normalized = self._normalized_project_path(path)
        kept = []
        for value in self._recent_projects():
            old_path = str(value.get("path") or "")
            same_path = bool(normalized and
                             self._normalized_project_path(old_path) == normalized)
            # When an untitled local snapshot receives its first file path,
            # replace that snapshot entry.  A real Save As keeps both files.
            same_local_snapshot = bool(
                path and project_id and value.get("project_id") == project_id and
                not old_path)
            same_internal = bool(
                not path and project_id and value.get("project_id") == project_id)
            if same_path or same_local_snapshot or same_internal:
                continue
            kept.append(value)
        kept.insert(0, {
            "project_id": project_id,
            "title": title,
            "path": path,
            "last_opened": datetime.now().astimezone().isoformat(timespec="seconds"),
        })
        self._layout_store["__recent_projects__"] = kept[:10]

    def _project_has_meaningful_content(self):
        board = self.current_storyboard()
        if board.get("shots"):
            return True
        project = self._positions()
        custom = [value for value in project.get("__custom_nodes__", [])
                  if isinstance(value, dict)]
        if not custom:
            return False
        if len(custom) != 1:
            return True
        starter = custom[0]
        if starter.get("type") != "storyboard_node":
            return True
        if str(starter.get("content") or "").strip():
            return True
        if any(starter.get(key) for key in (
                "pipeline_stage", "candidates", "group_nodes", "path", "status")):
            return True
        starter_id = str(starter.get("id") or "")
        positioned_nodes = [key for key in project
                            if not str(key).startswith("__") and key != starter_id]
        return bool(positioned_nodes or project.get("__workflow_edges__"))

    def _has_active_canvas_tasks(self):
        for task in self._standalone_tasks.values():
            handle = task.get("handle")
            try:
                if handle and not handle.is_finished:
                    return True
            except Exception:
                if handle:
                    return True
        return bool(self._canvas_storyboard_queue or self._canvas_character_queue or
                    any(self._serial_video_queues.values()))

    def _prepare_project_switch(self, action_name: str, confirm=True):
        """Checkpoint the old project and optionally ask how to keep an untitled one."""
        if confirm and self._has_active_canvas_tasks():
            answer = QMessageBox.question(
                self, "切换制片工程",
                f"当前仍有生成任务。{action_name}会停止这些画布任务，继续吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return False

        board = self.current_storyboard()
        project_id = str(board.get("id") or self._project_key())
        project_file = str(self._positions().get("__project_file__") or "")
        meaningful = self._project_has_meaningful_content()
        if confirm and meaningful and not project_file:
            answer = QMessageBox.question(
                self, "保存当前工程？",
                "当前画布还没有另存为工程文件。\n\n"
                "选择“保存”会先创建 .cepstudio 工程；选择“不保存”仍会保留本机自动快照，可从“最近工程”找回。",
                QMessageBox.StandardButton.Save |
                QMessageBox.StandardButton.Discard |
                QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save)
            if answer == QMessageBox.StandardButton.Cancel:
                return False
            if answer == QMessageBox.StandardButton.Save:
                if not self.save_canvas_project(show_message=False):
                    return False
                project_file = str(self._positions().get("__project_file__") or "")
        elif project_file:
            if not self.save_canvas_project(project_file, show_message=False):
                return False

        if meaningful:
            self._remember_recent_project(
                project_file, str(board.get("title") or "未命名制片工程"), project_id)
        self._save_layout_now(write_project=False)
        return True

    def node_moved(self, node):
        if not self._refreshing and node.node_id in self._nodes:
            self.scene.ensure_item_visible(node)
            self._positions()[node.node_id] = [round(node.pos().x(), 2), round(node.pos().y(), 2)]
            if (self._inline_editor_proxy is not None and
                    self._inline_editor_node_id == node.node_id):
                self._inline_editor_proxy.setPos(
                    node.pos() + QPointF(0, node.height + 18))
            self._layout_timer.start()

    def _next_canvas_action_serial(self):
        self._canvas_action_serial += 1
        return self._canvas_action_serial

    def begin_node_move(self, node):
        """Capture one drag transaction, including every selected node."""
        if self._refreshing or self._move_drag_before is not None:
            return
        selected = [item for item in self.scene.selectedItems()
                    if isinstance(item, CanvasNodeItem)]
        if node not in selected:
            selected.append(node)
        self._move_drag_before = {
            item.node_id:[round(item.pos().x(), 2), round(item.pos().y(), 2)]
            for item in selected if item.node_id in self._nodes
        }

    def end_node_move(self, _node=None):
        """Commit one history entry only if a completed drag changed position."""
        before = self._move_drag_before
        self._move_drag_before = None
        if not isinstance(before, dict) or not before:
            return False
        after = {
            node_id:[round(self._nodes[node_id].pos().x(), 2),
                     round(self._nodes[node_id].pos().y(), 2)]
            for node_id in before if node_id in self._nodes
        }
        changed_ids = [node_id for node_id in before
                       if node_id in after and before[node_id] != after[node_id]]
        if not changed_ids:
            return False
        self._position_undo.append({
            "serial":self._next_canvas_action_serial(),
            "before":{node_id:before[node_id] for node_id in changed_ids},
            "after":{node_id:after[node_id] for node_id in changed_ids},
        })
        self._position_undo = self._position_undo[-100:]
        self._position_redo.clear()
        return True

    def _apply_node_positions(self, values):
        if not isinstance(values, dict):
            return False
        applied = False
        for node_id, position in values.items():
            node = self._nodes.get(str(node_id))
            if (node is None or not isinstance(position, (list, tuple)) or
                    len(position) != 2):
                continue
            point = QPointF(float(position[0]), float(position[1]))
            node.setPos(point)
            self._positions()[node.node_id] = [round(point.x(), 2), round(point.y(), 2)]
            applied = True
        if applied:
            self.scene.update_edges()
            self.scene.ensure_content_bounds()
            self._layout_timer.start()
            if hasattr(self, "navigator_panel"):
                self.navigator_panel.refresh_outline()
        return applied

    def undo_canvas_move(self):
        if not self._position_undo:
            return False
        command = self._position_undo.pop()
        if not self._apply_node_positions(command.get("before")):
            return False
        self._position_redo.append(command)
        return True

    def redo_canvas_move(self):
        if not self._position_redo:
            return False
        command = self._position_redo.pop()
        if not self._apply_node_positions(command.get("after")):
            return False
        command["serial"] = self._next_canvas_action_serial()
        self._position_undo.append(command)
        return True

    def undo_canvas_action(self):
        """Undo the latest position change or deletion in chronological order."""
        move_serial = int((self._position_undo[-1] if self._position_undo else {}).get(
            "serial") or 0)
        delete_top = self._delete_undo[-1] if self._delete_undo else {}
        delete_serial = int(delete_top.get("serial") or 0) \
            if isinstance(delete_top, dict) and "positions" in delete_top else 0
        if move_serial >= delete_serial and move_serial:
            return self.undo_canvas_move()
        return self.undo_canvas_delete()

    def _save_layout_now(self, write_project=True):
        try:
            board = self.current_storyboard()
            if isinstance(board, dict) and board:
                project_key = str(board.get("id") or self._layout_store.get(
                    "__last_project__") or "assets_only")
                project = self._layout_store.setdefault(project_key, {})
                project["__storyboard_snapshot__"] = json.loads(
                    json.dumps(board, ensure_ascii=False))
                self._layout_store["__last_project__"] = project_key
            LAYOUT_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = LAYOUT_FILE.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(self._layout_store, ensure_ascii=False, indent=2), encoding="utf-8")
            # Keep the previous valid checkpoint. os.replace makes the final
            # hand-off atomic, so a process crash cannot leave half a JSON file.
            if LAYOUT_FILE.exists():
                shutil.copy2(LAYOUT_FILE, LAYOUT_FILE.with_suffix(".json.bak"))
            os.replace(temporary, LAYOUT_FILE)
            project_file = str(self._positions().get("__project_file__") or "")
            if write_project and project_file:
                try:
                    self._write_project_document(project_file)
                except Exception:
                    # The internal checkpoint must keep working even when an
                    # external drive or user-selected project path disappears.
                    pass
        except Exception:
            pass

    @staticmethod
    def _looks_like_media_path(value: str):
        return Path(str(value or "")).suffix.lower() in {
            ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff",
            ".mp4", ".mov", ".mkv", ".webm", ".avi",
            ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg",
        }

    def _project_media_manifest(self, board: dict, canvas: dict):
        paths = set()

        def visit(value):
            if isinstance(value, dict):
                for child in value.values():
                    visit(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    visit(child)
            elif isinstance(value, str) and self._looks_like_media_path(value):
                paths.add(value)

        visit(board); visit(canvas)
        result = []
        for value in sorted(paths):
            exists = os.path.exists(value)
            try:
                size = os.path.getsize(value) if exists else 0
            except OSError:
                size = 0
            result.append({"path":value, "exists":exists, "size":size})
        return result

    def _project_document(self):
        board = json.loads(json.dumps(self.current_storyboard(), ensure_ascii=False))
        canvas = json.loads(json.dumps(self._positions(), ensure_ascii=False))
        return {
            "format":PROJECT_FORMAT,
            "version":PROJECT_VERSION,
            "saved_at":datetime.now().astimezone().isoformat(timespec="seconds"),
            "project_id":str(board.get("id") or self._project_key()),
            "title":str(board.get("title") or "未命名制片工程"),
            "storyboard":board,
            "canvas":canvas,
            "media_manifest":self._project_media_manifest(board, canvas),
        }

    def _write_project_document(self, path: str):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self._project_document(), ensure_ascii=False, indent=2),
            encoding="utf-8")
        if target.exists():
            shutil.copy2(target, target.with_suffix(target.suffix + ".bak"))
        os.replace(temporary, target)

    def _show_project_saved(self, path: str):
        button = getattr(self, "save_project_btn", None)
        if button is None:
            return
        button.setText("✓ 已保存")
        button.setToolTip(str(path))
        QTimer.singleShot(1800, lambda b=button: b.setText("💾 保存工程"))

    def save_canvas_project(self, path: str = "", save_as=False, show_message=True):
        """Persist the complete production graph to a user-owned project file."""
        if not self.current_storyboard() and not self._positions().get("__custom_nodes__"):
            if show_message:
                QMessageBox.information(self, "保存工程", "当前画布还没有可保存的内容。")
            return False
        current = str(self._positions().get("__project_file__") or "")
        if not path and current and not save_as:
            path = current
        if not path:
            suggested = re.sub(
                r"[\\/:*?\"<>|]+", "_",
                str(self.current_storyboard().get("title") or "AI制片工程"))
            path, _ = QFileDialog.getSaveFileName(
                self, "保存 AI 制片工程", f"{suggested}.cepstudio",
                "Creative Engine 制片工程 (*.cepstudio);;JSON 工程 (*.json)")
        if not path:
            return False
        target = Path(path)
        if not target.suffix:
            target = target.with_suffix(".cepstudio")
        self._positions()["__project_file__"] = str(target)
        try:
            self._save_layout_now(write_project=False)
            self._write_project_document(str(target))
        except Exception as error:
            QMessageBox.warning(self, "保存工程失败", str(error))
            return False
        board = self.current_storyboard()
        self._remember_recent_project(
            str(target), str(board.get("title") or "未命名制片工程"),
            str(board.get("id") or self._project_key()))
        self._save_layout_now(write_project=False)
        self._show_project_saved(str(target))
        if show_message:
            media = self._project_document().get("media_manifest", [])
            missing = sum(not value.get("exists") for value in media)
            message = f"工程已保存：\n{target}\n\n已记录 {len(media)} 个媒体文件引用。"
            if missing:
                message += f"\n其中 {missing} 个源文件当前不存在，重新打开时会显示缺失。"
            QMessageBox.information(self, "工程已保存", message)
        return True

    def _stop_canvas_tasks_for_project_switch(self):
        for task in self._standalone_tasks.values():
            handle = task.get("handle")
            try:
                if handle and not handle.is_finished:
                    handle.cancel()
            except Exception:
                pass
        self._standalone_tasks.clear()
        self._serial_video_queues.clear()
        self._canvas_storyboard_queue = []
        self._canvas_character_queue = []
        self._auto_continue_pending.clear()
        process = self._preview_render_process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        self._preview_render_process = None
        self._preview_render_output = ""
        self._preview_render_timer.stop()

    def open_canvas_project(self, path: str = "", show_message=True,
                            confirm_switch=True):
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, "打开 AI 制片工程", "",
                "Creative Engine 制片工程 (*.cepstudio *.json)")
        if not path:
            return False
        target = Path(path)
        document = None
        load_error = None
        recovered_backup = False
        for candidate in (target, target.with_suffix(target.suffix + ".bak")):
            try:
                value = json.loads(candidate.read_text(encoding="utf-8"))
                if (not isinstance(value, dict) or
                        value.get("format") != PROJECT_FORMAT or
                        int(value.get("version") or 0) > PROJECT_VERSION):
                    raise ValueError("不是受支持的 Creative Engine 制片工程文件。")
                if (not isinstance(value.get("storyboard"), dict) or
                        not isinstance(value.get("canvas"), dict)):
                    raise ValueError("工程缺少故事板或画布数据。")
                document = value
                recovered_backup = candidate != target
                break
            except Exception as error:
                load_error = error
        if document is None:
            QMessageBox.warning(self, "打开工程失败", str(load_error or "无法读取工程"))
            return False
        board = document["storyboard"]
        canvas = document["canvas"]
        incoming_id = str(document.get("project_id") or board.get("id") or "")
        current_file = str(self._positions().get("__project_file__") or "")
        same_project = bool(
            incoming_id and incoming_id == self._project_key() and current_file and
            self._normalized_project_path(current_file) ==
            self._normalized_project_path(str(target)))
        if (not same_project and
                not self._prepare_project_switch(
                    "打开其他工程", confirm=confirm_switch)):
            return False
        self._stop_canvas_tasks_for_project_switch()
        project_key = incoming_id
        if not project_key:
            import uuid
            project_key = f"canvas_{uuid.uuid4().hex[:10]}"
            board["id"] = project_key
        loaded_canvas = json.loads(json.dumps(canvas, ensure_ascii=False))
        loaded_canvas["__storyboard_snapshot__"] = json.loads(
            json.dumps(board, ensure_ascii=False))
        loaded_canvas["__project_file__"] = str(target)
        self._layout_store[project_key] = loaded_canvas
        self._activate_canvas_project(board, project_key)
        if show_message:
            manifest = document.get("media_manifest") or []
            missing = [value.get("path") for value in manifest
                       if isinstance(value, dict) and
                       not os.path.exists(str(value.get("path") or ""))]
            message = f"已恢复工程：{document.get('title') or Path(path).stem}"
            if recovered_backup:
                message += "\n主工程文件损坏，已自动使用上一版备份恢复。"
            message += (f"\n节点和连线已恢复；有 {len(missing)} 个媒体文件缺失。"
                        if missing else "\n节点、连线、候选、定稿选择和生产状态已恢复。")
            QMessageBox.information(self, "工程已打开", message)
        return True

    def closeEvent(self, event):
        self._layout_timer.stop()
        self._checkpoint_timer.stop()
        self._commit_inline_editor_text()
        self._save_layout_now()
        self._stop_canvas_tasks_for_project_switch()
        super().closeEvent(event)

    def refresh(self, *_):
        # A refresh can be triggered by a model/ratio control while the text
        # editor is still open.  Commit the live widget before scene.clear()
        # destroys its QGraphicsProxyWidget.
        self._commit_inline_editor_text()
        self._refreshing = True
        try:
            self._inline_editor_proxy = None
            self._inline_editor_node_id = ""
            self.current_storyboard()
            selected_id = next((node.node_id for node in self.scene.selectedItems()
                                if isinstance(node, CanvasNodeItem)), "")
            self.scene.clear()
            self.scene.edges = []
            self.scene.node_edges = {}
            self._nodes.clear()
            self._default_positions.clear()
            self._build_nodes()
            self._build_edges()
            self.scene.update_edges()
            self._apply_visibility()
            self.scene.ensure_content_bounds()
            if selected_id in self._nodes:
                self._nodes[selected_id].setSelected(True)
            self._last_db_signature = self._db_signature()
            if hasattr(self, "asset_library"):
                self.asset_library.refresh()
            if hasattr(self, "navigator_panel"):
                self.navigator_panel.refresh_outline()
            self._update_production_continue_button()
        finally:
            self._refreshing = False
        if not self._initial_view_ready and self._nodes:
            self._initial_view_ready = True
            QTimer.singleShot(0, self._center_initial_view)

    def _center_initial_view(self):
        node = next(iter(self._nodes.values()), None)
        if not node:
            return
        self.view.resetTransform()
        self.view.set_zoom(0.9, keep_center=True)
        self.view.centerOn(node)

    def all_asset_groups(self):
        return {
            "scene": self.db.list_scenes(limit=5000),
            "character": self.db.list_characters(limit=5000),
            "element": self.db.list_elements(limit=5000),
        }

    def _bound_asset_node_ids(self):
        """返回当前分镜真正引用的资产，绑定资产始终留在项目画布。"""
        result = set()
        board = self.current_storyboard()
        bible = board.get("visual_bible", {}) if isinstance(board, dict) else {}
        for kind in ("scene", "character", "element"):
            value = bible.get(f"{kind}_id", "")
            if value:
                result.add(f"asset:{kind}:{value}")
        for shot in board.get("shots", []) if isinstance(board, dict) else []:
            scene_id = shot.get("scene_asset_id") or shot.get("scene_id")
            if scene_id:
                result.add(f"asset:scene:{scene_id}")
            character_ids = list(shot.get("character_ids", []) or [])
            if shot.get("character_id"):
                character_ids.append(shot["character_id"])
            character_ids.extend(
                value.get("asset_id") for value in shot.get("character_bindings", [])
                if isinstance(value, dict) and value.get("asset_id"))
            result.update(f"asset:character:{value}" for value in character_ids if value)
            element_ids = list(shot.get("element_ids", []) or [])
            if shot.get("element_id"):
                element_ids.append(shot["element_id"])
            element_ids.extend(
                value.get("asset_id") for value in shot.get("element_bindings", [])
                if isinstance(value, dict) and value.get("asset_id"))
            result.update(f"asset:element:{value}" for value in element_ids if value)
        return result

    def _explicit_canvas_asset_ids(self):
        values = self._positions().get("__assets__", [])
        return {str(value) for value in values if str(value).startswith("asset:")}

    def canvas_asset_node_ids(self):
        return self._explicit_canvas_asset_ids() | self._bound_asset_node_ids()

    def _asset_groups(self):
        wanted = self.canvas_asset_node_ids()
        return {
            kind: [item for item in items
                   if f"asset:{kind}:{item.id}" in wanted]
            for kind, items in self.all_asset_groups().items()
        }

    def _db_signature(self):
        values = []
        for path in (Path(self.db.db_path), Path(str(self.db.db_path) + "-wal")):
            try:
                stat = path.stat()
                values.append((stat.st_mtime_ns, stat.st_size))
            except OSError:
                values.append((0, 0))
        return tuple(values)

    def _add_node(self, node: CanvasNodeItem, default_pos: QPointF):
        self.scene.addItem(node)
        self._nodes[node.node_id] = node
        self._default_positions[node.node_id] = default_pos
        saved = self._positions().get(node.node_id)
        node.setPos(QPointF(float(saved[0]), float(saved[1])) if (
            isinstance(saved, list) and len(saved) == 2) else default_pos)
        return node

    @staticmethod
    def _video_thumbnail_path(record: dict, fallback_record: dict | None = None):
        """Choose a generated video still suitable for an in-node cover."""
        records = [record or {}]
        if isinstance(fallback_record, dict) and fallback_record is not record:
            records.append(fallback_record)
        candidates = []
        for value in records:
            candidates.extend((
                str(value.get("video_thumbnail") or ""),
                str(value.get("thumbnail_path") or ""),
            ))
            frames = [str(path) for path in (
                value.get("video_review_frames") or value.get("review_frames") or [])
                      if path]
            if frames:
                # Three review frames are ordered first/middle/last.  The
                # middle frame is usually more informative than the input
                # anchor and makes the generated result immediately visible.
                candidates.append(frames[1] if len(frames) >= 3 else frames[0])
                candidates.extend(frames)
            candidates.extend((
                str(value.get("first_frame") or ""),
                str(value.get("input_first_frame") or ""),
            ))
        return next((path for path in candidates
                     if path and os.path.isfile(path) and
                     ProductionCanvasTab._is_image_path(path)), "")

    @classmethod
    def _media_thumbnail_for_record(cls, record: dict, node_type: str):
        if str(node_type or "") == "video_node":
            return cls._video_thumbnail_path(record)
        return str(record.get("path") or "")

    def _build_nodes(self):
        if self._migrate_explicit_library_assets():
            self._save_layout_now()
        if self._consolidate_legacy_scene_assets():
            self._save_layout_now()
        groups = self._asset_groups()
        board = self.current_storyboard()

        # Migrate yesterday's duplicated portrait child and make incomplete
        # legacy three-view assets visibly request the authoritative 4-panel set.
        for data in list(self._positions().get("__custom_nodes__", [])):
            if not isinstance(data, dict) or data.get("asset_kind") != "character":
                continue
            node_id = str(data.get("id") or "")
            self._remove_legacy_character_portrait_view(node_id)
            data["title"] = f"{data.get('asset_name') or '角色'} · 角色立绘"
            data["ratio"] = "2:3"
            data["reference_role"] = "character"
            active = any(str(task.get("node_id") or "") == node_id
                         for task in self._standalone_tasks.values())
            if not active and not data.get("locked"):
                reference_set = dict(data.get("character_reference_set") or {})
                completed = sum(os.path.exists(str(reference_set.get(role) or ""))
                                for role, _label, _prompt in CHARACTER_REFERENCE_SPECS)
                data["status"] = (f"角色设定 {completed}/4 · 待锁定" if completed == 4 else
                                  f"角色设定 {completed}/4 · 点击补齐")

        for index, data in enumerate(self._positions().get("__custom_nodes__", [])):
            if not isinstance(data, dict):
                continue
            node_id = str(data.get("id") or "")
            node_type = str(data.get("type") or "text_node")
            if not node_id:
                continue
            thumbnail = self._media_thumbnail_for_record(data, node_type)
            node = CanvasNodeItem(
                self, node_id, node_type,
                (str(data.get("title") or "").replace("文本节点", "剧本工作台").replace("项目脚本", "剧本工作台")
                 if node_type == "text_node" else
                 str(data.get("title") or NODE_STYLE.get(node_type, ("节点", ""))[0])),
                subtitle=str(data.get("content") or "点击节点，在下方编辑"),
                thumbnail=thumbnail,
                badge=str(data.get("status") or ""),
                payload={"custom": True, **data})
            self._add_node(node, QPointF(80.0 + index * 290.0, -260.0))

        def lane_height(items):
            if not items:
                return 170.0
            max_takes = max((min(10, len(_asset_visual_entries(item)[1]))
                             for item in items), default=0)
            rows = (max_takes + 1) // 2
            return max(410.0, 220.0 + rows * 172.0)

        lane_y = {}
        cursor_y = 90.0
        for lane_kind in ("scene", "character", "element"):
            lane_y[lane_kind] = cursor_y
            cursor_y += lane_height(groups[lane_kind]) + 75.0
        shot_y = cursor_y + 40.0
        for kind, items in groups.items():
            for index, item in enumerate(items):
                x = 70.0 + index * 520.0
                y = lane_y[kind]
                approved = asset_is_approved(item, require_file=False)
                version = max(1, int(getattr(item, "version", 0) or 0)) if approved else 0
                master = approved_asset_path(item)
                thumbnail, entries = _asset_visual_entries(item)
                fixed_count = sum(entry["type"] == "fixed_view" for entry in entries)
                candidate_count = sum(entry["type"] == "candidate" for entry in entries)
                if approved:
                    parts = ["主参考已就绪"]
                    if fixed_count:
                        parts.append(f"{fixed_count} 个固定视角")
                    if candidate_count:
                        parts.append(f"{candidate_count} 个待选候选")
                elif candidate_count:
                    parts = [f"{candidate_count} 个候选", "请选择主参考"]
                else:
                    parts = ["尚无视觉图片"]
                node_id = f"asset:{kind}:{item.id}"
                node = CanvasNodeItem(
                    self, node_id, kind, getattr(item, "name", "未命名"),
                    subtitle=" · ".join(parts) + " · 双击生成/调整",
                    thumbnail=thumbnail,
                    badge=f"主参考 v{version}" if approved else "待选图",
                    payload={"asset_id": item.id, "kind": kind})
                self._add_node(node, QPointF(x, y))
                candidate_index = 0
                for take_index, entry in enumerate(entries[:10]):
                    path = entry["path"]
                    roles = list(entry.get("roles", []))
                    is_fixed_view = entry.get("type") == "fixed_view"
                    if not is_fixed_view:
                        candidate_index += 1
                    role_labels = [VIEW_ROLE_LABELS.get(role, role) for role in roles]
                    take_id = f"take:{kind}:{item.id}:{_short_id(path)}"
                    take = CanvasNodeItem(
                        self, take_id, "asset_view" if is_fixed_view else "asset_take",
                        (("固定视角 · " + " / ".join(role_labels))
                         if is_fixed_view else f"候选 {candidate_index}"),
                        subtitle=Path(path).name,
                        thumbnail=path,
                        badge="固定视角" if is_fixed_view else "待选择",
                        payload={"asset_id": item.id, "kind": kind, "path": path,
                                 "approved": False,
                                 "reference_type": entry.get("type", "candidate"),
                                 "view_roles": roles})
                    tx = x + 20.0 + (take_index % 2) * 210.0
                    ty = y + 188.0 + (take_index // 2) * 172.0
                    self._add_node(take, QPointF(tx, ty))

        shots = board.get("shots", []) if isinstance(board.get("shots"), list) else []
        for index, shot in enumerate(shots):
            x = 70.0 + index * 470.0
            selected_image = str(shot.get("selected_image_asset") or
                                 shot.get("anchor_frame_id") or "")
            selected_video = str(shot.get("selected_video_asset") or "")
            draft_panel = str(shot.get("draft_panel") or "")
            selected = selected_video or selected_image or str(
                shot.get("preview_asset") or shot.get("selected_asset") or draft_panel)
            shot_thumbnail = (self._video_thumbnail_path(shot)
                              if selected_video else selected)
            node_id = f"shot:{shot.get('id')}"
            node = CanvasNodeItem(
                self, node_id, "shot",
                f"镜头 {int(shot.get('number', index + 1)):02d}",
                subtitle=(f"{float(shot.get('duration', 0) or 0):g}s · "
                          f"{shot.get('shot_size', '中景')} · {shot.get('camera_slot') or '自由机位'}\n"
                          f"{('运动分镜 ' + str(len(shot.get('motion_keyframes') or [])) + ' 帧 · ') if shot.get('motion_keyframes') else ''}"
                          f"{str(shot.get('blocking') or shot.get('scene') or '')[:72]}"),
                thumbnail=shot_thumbnail,
                badge=(f"导演退回 · {int(shot.get('quality_score') or 0)}分"
                       if shot.get("quality_passed") is False else
                       f"导演通过 · {int(shot.get('quality_score') or 0)}分"
                       if shot.get("quality_passed") is True else
                       "视频已定稿" if selected_video else
                       "图片已定稿" if selected_image else
                        "旧分镜合同 · 请重新生成"
                        if self._motion_board_contract_stale(shot) else
                        f"运动分镜 · {len(shot.get('motion_keyframes') or [])} 帧"
                       if draft_panel and selected == draft_panel and
                       shot.get("draft_source") == "ai" and shot.get("motion_keyframes") else
                       "AI 分镜稿" if draft_panel and selected == draft_panel and shot.get("draft_source") == "ai" else
                       "手绘稿" if draft_panel and selected == draft_panel else
                       "有候选" if selected else "待生成"),
                payload={"shot_id": str(shot.get("id") or ""), "shot": shot})
            self._add_node(node, QPointF(x, shot_y))
            assets = [value for value in shot.get("assets", []) if isinstance(value, dict)]
            motion_candidate_index = 0
            for take_index, asset in enumerate(assets[:12]):
                path = str(asset.get("path") or "")
                asset_kind = str(asset.get("kind") or "image")
                # One director-timeline video is intentionally registered on
                # every member shot so approval, offsets and downstream audio
                # stay in sync.  It is still one clip, though: only render its
                # result card beneath the first shot in the segment instead of
                # showing the same file once per member shot.
                if asset_kind == "video":
                    generator = self._custom_record(str(
                        asset.get("generator_node_id") or "")) or {}
                    segment_shot_ids = [str(value) for value in
                                        generator.get("shot_ids", []) if value]
                    if (len(segment_shot_ids) > 1 and
                            str(shot.get("id") or "") != segment_shot_ids[0]):
                        continue
                is_motion_board = str(asset.get("subtype") or "") == "motion_storyboard"
                if is_motion_board:
                    motion_candidate_index += 1
                take_id = f"shot_take:{shot.get('id')}:{_short_id(path)}"
                take_thumbnail = (
                    self._video_thumbnail_path(asset, shot)
                    if asset_kind == "video" else path)
                take = CanvasNodeItem(
                    self, take_id, "shot_take",
                    (f"运动分镜候选 {motion_candidate_index} · "
                     f"{int(asset.get('frame_count') or len(shot.get('motion_keyframes') or []))} 帧"
                     if is_motion_board else
                     (f"{'起始帧' if asset.get('frame_role') == 'start' else '结束帧'}候选 {take_index + 1}"
                      if asset_kind == "image" and asset.get("frame_role") in ("start", "end") else
                      f"{'图片' if asset.get('kind') == 'image' else '视频'}版本 {take_index + 1}")),
                    subtitle=Path(path).name if path else "无文件",
                    thumbnail=take_thumbnail,
                    badge=(("已采用为当前分镜"
                            if path == str(shot.get("motion_board_path") or "")
                            else "待选择") if is_motion_board else
                           "定稿结束帧" if path == shot.get("selected_end_image_asset") else
                           "定稿起始帧" if path == selected_image else
                           "定稿视频" if path == selected_video else
                           "当前预览" if path == shot.get("preview_asset") else "结果"),
                    payload={"shot_id": str(shot.get("id") or ""), "path": path,
                             "kind":asset_kind, "asset": asset,
                             "video_thumbnail":take_thumbnail if asset_kind == "video" else ""})
                tx = x + (take_index % 2) * 205.0
                ty = shot_y + 220.0 + (take_index // 2) * 165.0
                self._add_node(take, QPointF(tx, ty))

        # 运行中的生成任务是画布上的一等节点。任务完成后，既有分镜逻辑会把它
        # 替换为 shot_take 结果节点，因此 UI 与底层 TaskManager 保持松耦合。
        shot_indexes = {
            str(shot.get("id") or ""): index for index, shot in enumerate(shots)
        }
        kind_labels = {
            "image": "关键帧生成", "video": "视频生成",
            "dialogue_audio": "对白音频", "quality_review": "一致性检查",
        }
        for task_index, task in enumerate(self._task_snapshots()):
            handle = task.get("handle")
            task_id = str(getattr(handle, "id", "") or task.get("id", "") or task_index)
            shot_id = str(task.get("shot_id") or "")
            kind = str(task.get("kind") or "task")
            progress = max(0, min(100, int(float(getattr(handle, "progress", 0.0) or 0.0) * 100)))
            state = str(getattr(getattr(handle, "status", None), "name", "QUEUED"))
            provider = str(task.get("provider") or getattr(handle, "provider_name", "") or "自动选择")
            node = CanvasNodeItem(
                self, f"task:{task_id}", "generation_task",
                kind_labels.get(kind, "生成任务"),
                subtitle=f"{provider} · {state}\n进度 {progress}%",
                badge=f"{progress}%",
                payload={"task_id": task_id, "shot_id": shot_id,
                         "kind": kind, "handle": handle})
            index = shot_indexes.get(shot_id, task_index)
            self._add_node(node, QPointF(70.0 + index * 470.0, shot_y + 205.0))

    def _add_lane_label(self, text, y):
        label = self.scene.addText(text, QFont("Microsoft YaHei", 12, QFont.Weight.DemiBold))
        label.setDefaultTextColor(QColor("#6f7180"))
        label.setPos(70, y)
        label.setZValue(-5)

    def _build_edges(self):
        board = self.current_storyboard()
        bible = board.get("visual_bible", {}) if isinstance(board, dict) else {}
        director = self._nodes.get("director:project")
        if director:
            for kind in ("scene", "character", "element"):
                first = next((node for node in self._nodes.values()
                              if node.node_type == kind), None)
                if first:
                    self.scene.connect_nodes(director, first, "dependency")
        for node in list(self._nodes.values()):
            if node.node_type in ("asset_view", "asset_take"):
                parent = self._nodes.get(
                    f"asset:{node.payload.get('kind')}:{node.payload.get('asset_id')}")
                if parent:
                    self.scene.connect_nodes(parent, node,
                                             "approved" if node.payload.get("approved") else "dependency")
            elif node.node_type == "shot_take":
                parent = self._nodes.get(f"shot:{node.payload.get('shot_id')}")
                if parent:
                    self.scene.connect_nodes(parent, node, "result")
            elif node.node_type == "generation_task":
                parent = self._nodes.get(f"shot:{node.payload.get('shot_id')}")
                if parent:
                    self.scene.connect_nodes(parent, node, "result")

        previous = None
        for shot in board.get("shots", []) if isinstance(board, dict) else []:
            shot_node = self._nodes.get(f"shot:{shot.get('id')}")
            if not shot_node:
                continue
            scene_ids = self._shot_asset_ids(shot, "scene")
            if not scene_ids and bible.get("scene_id"):
                scene_ids = [bible["scene_id"]]
            character_ids = self._shot_asset_ids(shot, "character")
            if not character_ids and bible.get("character_id"):
                character_ids = [bible["character_id"]]
            element_ids = self._shot_asset_ids(shot, "element")
            if not element_ids and bible.get("element_id"):
                element_ids = [bible["element_id"]]
            asset_ids = [("scene", value) for value in scene_ids]
            asset_ids.extend(("character", value) for value in character_ids)
            asset_ids.extend(("element", value) for value in element_ids)
            for kind, asset_id in dict.fromkeys(asset_ids):
                asset_node = self._nodes.get(f"asset:{kind}:{asset_id}")
                if asset_node:
                    self.scene.connect_nodes(asset_node, shot_node, "dependency")
            if previous:
                self.scene.connect_nodes(previous, shot_node, "sequence")
            elif director:
                self.scene.connect_nodes(director, shot_node, "sequence")
            previous = shot_node

        # 用户拖拽创建、且不属于资产绑定模型的工作流引用。
        for value in self._positions().get("__workflow_edges__", []):
            if not isinstance(value, dict):
                continue
            source = self._nodes.get(str(value.get("source") or ""))
            target = self._nodes.get(str(value.get("target") or ""))
            if source and target:
                self.scene.connect_nodes(source, target, "workflow")

    def selection_changed(self):
        selected = [item for item in self.scene.selectedItems()
                    if isinstance(item, CanvasNodeItem)]
        if len(selected) == 1:
            self.show_inline_editor(selected[0])
        elif len(selected) > 1:
            self.hide_inline_editor()
        else:
            # 点击 QGraphicsProxyWidget 内部编辑器时，Scene 会短暂清空节点选择；
            # 延后一拍判断焦点，避免把用户正在输入的编辑器立刻销毁。
            QTimer.singleShot(0, self._hide_inline_editor_if_unfocused)

    def _hide_inline_editor_if_unfocused(self):
        proxy = self._inline_editor_proxy
        if proxy is None:
            return
        panel = proxy.widget()
        focus = QApplication.focusWidget()
        current = focus
        while current is not None:
            if current is panel:
                node = self._nodes.get(self._inline_editor_node_id)
                if node is not None and not node.isSelected():
                    node.setSelected(True)
                return
            current = current.parentWidget()
        if not self.scene.selectedItems():
            self.hide_inline_editor()

    def scroll_inline_editor_at(self, scene_pos: QPointF, event) -> bool:
        """按鼠标位置滚动展开面板中的文本，不只处理主编辑框。"""
        proxy = self._inline_editor_proxy
        if (proxy is None or not proxy.sceneBoundingRect().contains(scene_pos)):
            return False
        panel = proxy.widget()
        if panel is None:
            return False

        # scene -> proxy/widget 坐标，找出鼠标真正悬停的 QTextEdit。childAt
        # 通常会返回其 viewport，因此需要沿父级向上找到文本控件本身。
        local = proxy.mapFromScene(scene_pos)
        hovered = panel.childAt(round(local.x()), round(local.y()))
        editor = hovered
        while editor is not None and not isinstance(editor, QTextEdit):
            if editor is panel:
                editor = None
                break
            editor = editor.parentWidget()

        # 面板留白处仍按主正文滚动；报告、体检、候选稿等区域则各滚各的。
        if editor is None:
            editor = self._inline_text_editor
        if editor is None or not editor.isVisible():
            return False
        bar = editor.verticalScrollBar()
        pixel = event.pixelDelta().y()
        angle = event.angleDelta().y()
        if pixel:
            bar.setValue(bar.value() - int(pixel))
        elif angle:
            distance = max(24, int(bar.singleStep()) * 3)
            bar.setValue(bar.value() - round(float(angle) / 120.0 * distance))
        return bool(pixel or angle)

    def hide_inline_editor(self):
        self._commit_inline_editor_text()
        proxy = self._inline_editor_proxy
        self._inline_editor_proxy = None
        self._inline_editor_node_id = ""
        self._inline_text_editor = None
        self._inline_editor_typing = False
        self._inline_editor_dirty = False
        if proxy is not None and proxy.scene() is not None:
            self.scene.removeItem(proxy)
            proxy.deleteLater()

    def show_inline_editor(self, node: CanvasNodeItem):
        if self._refreshing or node.node_type in ("director", "generation_task", "workflow_group"):
            self.hide_inline_editor()
            return
        if (node.node_type == "image_node" and
                not bool(node.payload.get("multi_image_composer")) and
                not bool(node.payload.get("image_workbench")) and
                str(node.payload.get("generator_kind") or "") != "image"):
            # 普通图片是素材，不在节点内重复提供生成参数。
            self.hide_inline_editor()
            return
        if self._inline_editor_node_id == node.node_id:
            return
        self.hide_inline_editor()
        panel = QFrame()
        panel.setObjectName("inlineNodeEditor")
        panel.setFixedWidth(
            760 if node.node_type == "storyboard_node" else
            680 if node.node_type in ("image_node", "video_node") else
            max(420, int(node.width)))
        panel.setStyleSheet(
            "QFrame#inlineNodeEditor{font-family:'Microsoft YaHei UI','Microsoft YaHei';"
            "background:#232329;border:1px solid #41414c;"
            "border-radius:14px;}QTextEdit{background:#232329;border:none;color:#e7e7ec;"
            "font-family:'Microsoft YaHei UI','Microsoft YaHei';font-size:13px;"
            "line-height:1.55;padding:10px;}"
            "QScrollBar:vertical{background:#1b1b20;width:9px;margin:2px;}"
            "QScrollBar::handle:vertical{background:#555563;border-radius:4px;min-height:28px;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
            "QComboBox{background:#1b1b20;border:none;"
            "padding:6px;color:#ddd;}QPushButton{background:#303038;border:none;"
            "border-radius:8px;padding:7px 10px;color:#ddd;}"
            "QPushButton:hover{background:#3a3a46;color:white;}"
            "QPushButton#runNode{background:#d7d7d9;color:#202024;font-weight:bold;}"
            "QPushButton#editorResizeHandle{background:transparent;color:#777783;"
            "font-size:10px;padding:0;border-radius:3px;}"
            "QPushButton#editorResizeHandle:hover{background:#2d2d35;color:#b8b8c2;}"
        )
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        is_copywriting = bool(
            node.node_type == "text_node" and node.payload.get("copywriting_workbench"))
        # 图片拥有参考职责、标记和风格；视频把首帧、尾帧和普通参考明确分开。
        if node.node_type == "image_node":
            chips = QHBoxLayout()
            references = list(node.payload.get("references") or [])
            reference_btn = QPushButton(
                f"＋参考 {len(references)}" if references else "＋参考")
            reference_btn.clicked.connect(
                lambda _=False, n=node, b=reference_btn: self.choose_node_references(n, b))
            reference_btn.setToolTip(
                "\n".join(Path(value).name for value in references)
                if references else "添加图片或视频参考素材；右键可清空")
            reference_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            reference_btn.customContextMenuRequested.connect(
                lambda _pos, n=node, b=reference_btn: self.show_reference_menu(n, b))
            chips.addWidget(reference_btn)
            mark_btn = QPushButton("● 已标记" if node.payload.get("marked") else "◎ 标记")
            mark_btn.setCheckable(True)
            mark_btn.setChecked(bool(node.payload.get("marked")))
            mark_btn.clicked.connect(
                lambda checked, n=node, b=mark_btn: self.toggle_node_mark(n, checked, b))
            chips.addWidget(mark_btn)
            style_name = str(node.payload.get("style") or "")
            style_btn = QPushButton(f"◇ {style_name}" if style_name else "◇ 风格")
            style_btn.clicked.connect(
                lambda _=False, n=node, b=style_btn: self.show_node_style_menu(n, b))
            chips.addWidget(style_btn)
            role = str(node.payload.get("reference_role") or "reference")
            role_btn = QPushButton(f"⌾ {DIRECT_REFERENCE_ROLES.get(role, '普通参考')}")
            role_btn.setToolTip("指定这张图片被下游模型用作角色、场景、风格或元素参考")
            role_btn.clicked.connect(
                lambda _=False, n=node, b=role_btn: self.show_image_reference_role_menu(n, b))
            chips.addWidget(role_btn)
            if (bool(node.payload.get("multi_image_composer")) or
                    bool(node.payload.get("image_workbench"))):
                # 参考职责属于生成任务内的每张输入，不再挂在整个节点上。
                mark_btn.hide(); style_btn.hide(); role_btn.hide()
            chips.addStretch()
            layout.addLayout(chips)
            if bool(node.payload.get("multi_image_composer")):
                reference_count = len(node.payload.get("references") or [])
                mapping_note = QLabel(
                    f"多图合成 · {reference_count}/9 张参考 · "
                    "每张图可单独指定主体、场景、构图、元素或风格")
                mapping_note.setWordWrap(True)
                mapping_note.setStyleSheet(
                    "color:#b8d8df;background:#192a30;border:1px solid #355866;"
                    "border-radius:7px;padding:7px;")
                layout.addWidget(mapping_note)
                edit_mapping = QPushButton("设置每张图片的用途…")
                edit_mapping.setEnabled(reference_count > 0)
                edit_mapping.setToolTip(
                    "先从其他图片节点连线，或点击上方“＋参考”选择图片")
                edit_mapping.clicked.connect(
                    lambda _=False, n=node: self.edit_multi_image_composer(n))
                layout.addWidget(edit_mapping)
        elif node.node_type == "video_node":
            chips = QHBoxLayout()
            first_frame = str(node.payload.get("first_frame") or "")
            last_frame = str(node.payload.get("last_frame") or "")
            first_btn = QPushButton(
                f"首帧 · {Path(first_frame).name}" if first_frame else "＋ 首帧")
            first_btn.setToolTip(
                f"点击预览\n{first_frame}\n右键可替换或清空"
                if first_frame else "点击选择视频开始画面")
            first_btn.clicked.connect(
                lambda _=False, n=node, b=first_btn:
                self.open_or_choose_video_frame(n, "first_frame", b))
            first_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            first_btn.customContextMenuRequested.connect(
                lambda _pos, n=node, b=first_btn:
                self.show_video_frame_menu(n, "first_frame", b))
            chips.addWidget(first_btn)
            last_btn = QPushButton(
                f"尾帧 · {Path(last_frame).name}" if last_frame else "＋ 尾帧")
            last_btn.setToolTip(
                f"点击预览\n{last_frame}\n右键可替换或清空"
                if last_frame else "点击选择可选的视频结束画面")
            last_btn.clicked.connect(
                lambda _=False, n=node, b=last_btn:
                self.open_or_choose_video_frame(n, "last_frame", b))
            last_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            last_btn.customContextMenuRequested.connect(
                lambda _pos, n=node, b=last_btn:
                self.show_video_frame_menu(n, "last_frame", b))
            chips.addWidget(last_btn)
            references = list(node.payload.get("references") or [])
            reference_btn = QPushButton(
                f"＋资产参考 {len(references)}" if references else "＋资产参考")
            reference_btn.setToolTip(
                "\n".join(Path(value).name for value in references)
                if references else "添加角色、场景、元素或风格参考；不会替代首尾帧")
            reference_btn.clicked.connect(
                lambda _=False, n=node, b=reference_btn: self.choose_node_references(n, b))
            reference_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            reference_btn.customContextMenuRequested.connect(
                lambda _pos, n=node, b=reference_btn: self.show_reference_menu(n, b))
            chips.addWidget(reference_btn)
            chips.addStretch()
            layout.addLayout(chips)
        if is_copywriting:
            product_row = QHBoxLayout()
            product_name = QLineEdit(str(node.payload.get("product_name") or ""))
            product_name.setPlaceholderText("产品 / 品牌名称")
            product_name.textChanged.connect(
                lambda value, n=node: self.update_custom_setting(n, "product_name", value))
            product_row.addWidget(product_name, 2)
            copy_style = QComboBox()
            copy_style.addItems([
                "激情抓眼球", "沉稳放松", "幽默有趣", "紧迫急迫",
                "高端大气", "网感爆棚", "情感共鸣", "专业权威",
            ])
            copy_style.setCurrentText(str(node.payload.get("copy_style") or "激情抓眼球"))
            copy_style.currentTextChanged.connect(
                lambda value, n=node: self.update_custom_setting(n, "copy_style", value))
            product_row.addWidget(copy_style, 1)
            copy_duration = QComboBox()
            copy_duration.setEditable(True)
            copy_duration.addItems(["15", "20", "30", "45", "60"])
            copy_duration.setCurrentText(str(node.payload.get("copy_duration") or "30"))
            copy_duration.currentTextChanged.connect(
                lambda value, n=node: self.update_custom_setting(n, "copy_duration", value))
            copy_duration.setToolTip("目标口播时长（秒），也可直接输入")
            product_row.addWidget(copy_duration, 1)
            layout.addLayout(product_row)
            product_desc = _NodeTextEdit()
            product_desc.setAcceptRichText(False)
            product_desc.setPlaceholderText("产品卖点、目标人群、使用场景、优惠信息和必须保留的表述…")
            product_desc.setPlainText(str(node.payload.get("product_description") or ""))
            product_desc.setFixedHeight(112)
            product_desc.textChanged.connect(
                lambda n=node, e=product_desc: self.update_custom_setting(
                    n, "product_description", e.toPlainText()))
            layout.addWidget(product_desc)
            output_label = QLabel("生成结果（可直接修改）")
            output_label.setStyleSheet("color:#8fb5ff;font-weight:600;")
            layout.addWidget(output_label)
        editor = _NodeTextEdit()
        editor.editingStarted.connect(
            lambda n=node: self._inline_editing_started(n))
        editor.editingStopped.connect(
            lambda nid=str(node.node_id), e=editor:
            self._inline_editing_stopped(nid, e))
        editor.canvasZoomRequested.connect(
            lambda factor: self.view.zoom_by(factor, keep_center=False))
        editor.setAcceptRichText(False)
        editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.setPlaceholderText(
            "只写一句故事想法，AI 会在画布上自动拆镜并逐镜生成…"
            if node.node_type == "storyboard_node" else
            ("填写 Skill 的目标、限制或希望调整的效果…"
             if node.node_type == "skill_node" else
             (("生成信息流口播文案，结果可翻译、恢复原文或继续接入配音…"
               if is_copywriting else
               "写作、诊断和定稿剧本；版本会随 AI 操作和制片交接自动保存…")
              if node.node_type == "text_node" else "描述你想生成或修改的内容…")))
        if node.node_type == "shot":
            initial_text = str((node.payload.get("shot") or {}).get("visual") or "")
        elif "content" in node.payload:
            # An explicitly saved empty string is meaningful: it must not fall
            # back to the node's default subtitle when the editor reopens.
            initial_text = str(node.payload.get("content") or "")
        else:
            overrides = self._positions().get("__inline_text_overrides__", {})
            initial_text = str(
                overrides.get(str(node.node_id), node.subtitle)
                if isinstance(overrides, dict) else node.subtitle or "")
        editor.setPlainText(initial_text)
        editor.textChanged.connect(
            lambda nid=str(node.node_id), e=editor:
            self._update_inline_editor_draft(nid, e.toPlainText()))
        editor.setFixedHeight(self._inline_editor_saved_height(node, editor.toPlainText()))
        layout.addWidget(editor)
        resize_handle = _EditorResizeHandle(editor, panel)
        resize_handle.heightCommitted.connect(
            lambda height, node_id=str(node.node_id):
            self._store_inline_editor_height(node_id, height))
        layout.addWidget(resize_handle)
        self._inline_text_editor = editor
        if node.node_type == "text_node" and not is_copywriting:
            review_text = str(node.payload.get("script_review") or "").strip()
            candidate_text = str(node.payload.get("script_candidate") or "").strip()
            result_text = review_text or candidate_text
            if result_text:
                result_title = QLabel(
                    "AI 剧本审阅报告" if review_text else "AI 候选稿（尚未替换当前剧本）")
                result_title.setStyleSheet("color:#8fb5ff;font-weight:600;padding-top:6px;")
                layout.addWidget(result_title)
                result_editor = _NodeTextEdit()
                result_editor.setReadOnly(True)
                result_editor.setPlainText(result_text)
                result_editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
                result_editor.setFixedHeight(190)
                result_editor.setStyleSheet(
                    "QTextEdit{background:#151a22;color:#dce6f7;border:1px solid #33445f;"
                    "border-radius:8px;padding:8px;}")
                result_editor.canvasZoomRequested.connect(
                    lambda factor: self.view.zoom_by(factor, keep_center=False))
                layout.addWidget(result_editor)
                result_controls = QHBoxLayout()
                if candidate_text:
                    adopt = QPushButton("采用候选稿")
                    adopt.clicked.connect(
                        lambda _=False, nid=str(node.node_id), e=editor:
                        self.queue_inline_action(nid, e.toPlainText(), "采用AI候选稿"))
                    result_controls.addWidget(adopt)
                dismiss = QPushButton("关闭报告" if review_text else "丢弃候选稿")
                dismiss.clicked.connect(
                    lambda _=False, nid=str(node.node_id), e=editor:
                    self.queue_inline_action(nid, e.toPlainText(), "清除AI结果"))
                result_controls.addWidget(dismiss)
                result_controls.addStretch()
                layout.addLayout(result_controls)
        if node.node_type == "video_node":
            if bool(node.payload.get("multi_image_director")):
                timeline_count = len(node.payload.get("timeline_images") or [])
                timeline_note = QLabel(
                    f"多图导演时间轴 · {timeline_count} 张图片 · "
                    f"{float(node.payload.get('duration') or 10):g} 秒")
                timeline_note.setStyleSheet(
                    "color:#b8c8ff;background:#20243a;border:1px solid #46517a;"
                    "border-radius:7px;padding:7px;")
                layout.addWidget(timeline_note)
                edit_timeline = QPushButton("编辑图片时间、动作、运镜与用途…")
                edit_timeline.clicked.connect(
                    lambda _=False, n=node: self.edit_multi_image_director(n))
                layout.addWidget(edit_timeline)
            creative_label = QLabel("创意提示词（仅图生视频）")
            creative_label.setStyleSheet("color:#74aee5;font-size:11px;")
            layout.addWidget(creative_label)
            creative_editor = _NodeTextEdit()
            creative_editor.setAcceptRichText(False)
            creative_editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            creative_editor.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            creative_editor.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            creative_editor.setFixedHeight(120)
            creative_editor.canvasZoomRequested.connect(
                lambda factor: self.view.zoom_by(factor, keep_center=False))
            creative_editor.setPlainText(str(node.payload.get("creative_prompt") or ""))
            creative_editor.setPlaceholderText(
                "补充你的想法：人物动作、镜头运动、节奏、氛围、光影变化……"
                "首尾帧仍作为画面约束，不会变成文生视频。")
            creative_editor.textChanged.connect(
                lambda n=node, e=creative_editor:
                self.update_custom_setting(n, "creative_prompt", e.toPlainText().strip()))
            layout.addWidget(creative_editor)
        controls = QHBoxLayout()
        secondary_controls = None
        mode = None
        action_getter = None
        options = {
            "image_node": (["AI 编辑", "图片高清", "智能扩图", "移除背景", "替换背景"]
                           if bool(node.payload.get("image_workbench")) else
                           (["AI 编辑"] if bool(node.payload.get("multi_image_composer"))
                            else ["图生图"])),
            "video_node": ["图生视频", "文生视频", "提取首中尾帧", "基于尾帧续拍"],
            "audio_node": ["对白配音", "音效"],
            "shot": ["保存镜头修改", "生成关键帧", "参考图再生成", "生成视频", "生成对白"],
        }.get(node.node_type, ["编辑节点", "继续生成"])
        if is_copywriting:
            record = self._custom_record(str(node.node_id)) or node.payload
            def copy_status(value):
                chars = len("".join(str(value or "").split()))
                return f"口播文案 · {chars} 字 · 预计 {chars / 4.0:.0f} 秒"
            copy_note = QLabel(copy_status(initial_text))
            copy_note.setStyleSheet(
                "color:#aebbd0;background:#1c2430;border:1px solid #31415a;"
                "border-radius:8px;padding:7px 9px;")
            layout.addWidget(copy_note)
            editor.textChanged.connect(
                lambda e=editor, label=copy_note: label.setText(copy_status(e.toPlainText())))
            mode = QComboBox()
            mode.addItems(["生成口播文案", "改写优化", "压缩精简", "增强开场钩子"])
            mode.setCurrentText(str(node.payload.get("editor_action") or "生成口播文案"))
            mode.currentTextChanged.connect(
                lambda value, n=node: self.update_custom_setting(n, "editor_action", value))
            controls.addWidget(mode, 1)
            model_combo = QComboBox()
            for label, provider, model in self._available_script_models():
                model_combo.addItem(label, (provider, model))
            saved_model = str(node.payload.get("model") or "")
            for index in range(model_combo.count()):
                if str((model_combo.itemData(index) or ("", ""))[1]) == saved_model:
                    model_combo.setCurrentIndex(index); break
            controls.addWidget(model_combo, 1)
            secondary_controls = QHBoxLayout()
            language = QComboBox()
            language.addItems([
                "英语", "日语", "韩语", "法语", "德语", "西班牙语",
                "葡萄牙语", "俄语", "泰语", "越南语", "印尼语", "阿拉伯语",
            ])
            language.setEditable(True)
            language.setCurrentText(str(node.payload.get("copy_language") or "英语"))
            language.currentTextChanged.connect(
                lambda value, n=node: self.update_custom_setting(n, "copy_language", value))
            secondary_controls.addWidget(language, 1)
            translate = QPushButton("翻译")
            translate.clicked.connect(
                lambda _=False, nid=str(node.node_id), e=editor, mc=model_combo, lang=language:
                self.queue_inline_action(nid, e.toPlainText(), f"翻译为{lang.currentText()}",
                                         mc.currentData(), translate))
            secondary_controls.addWidget(translate)
            copy_button = QPushButton("复制文案")
            copy_button.clicked.connect(
                lambda _=False, e=editor: QApplication.clipboard().setText(e.toPlainText()))
            secondary_controls.addWidget(copy_button)
            restore = QPushButton("恢复原文")
            restore.setEnabled(bool(record.get("copy_original")))
            restore.clicked.connect(
                lambda _=False, nid=str(node.node_id), e=editor:
                self.queue_inline_action(nid, e.toPlainText(), "恢复口播原文"))
            secondary_controls.addWidget(restore)
        elif node.node_type == "text_node":
            record = self._custom_record(str(node.node_id)) or node.payload
            def workbench_status(value):
                metrics = script_metrics(value)
                version = int(record.get("script_version") or 0)
                script_state = "已定稿" if record.get("script_locked") else "草稿"
                return (f"剧本工作台 · {script_state} · V{version or 1} · "
                        f"{metrics['characters']} 字 · {metrics['scenes']} 场 · "
                        f"{metrics['dialogue_lines']} 条对白")
            workbench_note = QLabel(workbench_status(initial_text))
            workbench_note.setStyleSheet(
                "color:#aebbd0;background:#1c2430;border:1px solid #31415a;"
                "border-radius:8px;padding:7px 9px;")
            layout.addWidget(workbench_note)
            editor.textChanged.connect(
                lambda e=editor, label=workbench_note:
                label.setText(workbench_status(e.toPlainText())))
            mode = QComboBox()
            mode.addItems([
                "生成完整脚本", "续写脚本", "改写优化", "剧本体检",
                "强化人物弧光", "对白润色", "制片可行性检查",
            ])
            saved_action = str(node.payload.get("editor_action") or "生成完整脚本")
            mode.setCurrentIndex(max(0, mode.findText(saved_action)))
            mode.currentTextChanged.connect(
                lambda value, n=node: self.update_custom_setting(n, "editor_action", value))
            controls.addWidget(mode, 1)
            model_combo = QComboBox()
            for label, provider, model in self._available_script_models():
                seconds = self._script_model_seconds(label, model)
                model_combo.addItem(f"{label}                                      {seconds}s",
                                    (provider, model))
            saved_model = str(node.payload.get("model") or "")
            for index in range(model_combo.count()):
                if str((model_combo.itemData(index) or ("", ""))[1]) == saved_model:
                    model_combo.setCurrentIndex(index)
                    break
            model_combo.currentIndexChanged.connect(
                lambda _index, n=node, c=model_combo:
                self.update_custom_setting(n, "model", (c.currentData() or ("", ""))[1]))
            model_combo.setMinimumWidth(190)
            controls.addWidget(model_combo, 1)
            secondary_controls = QHBoxLayout()
            save_version = QPushButton("保存版本")
            save_version.clicked.connect(
                lambda _=False, nid=str(node.node_id), e=editor:
                self.queue_inline_action(nid, e.toPlainText(), "保存剧本版本"))
            secondary_controls.addWidget(save_version)
            restore_version = QPushButton("上一版")
            restore_version.setEnabled(previous_script_version(record) is not None)
            restore_version.setToolTip("恢复到上一个剧本版本；当前内容仍会先保存")
            restore_version.clicked.connect(
                lambda _=False, nid=str(node.node_id), e=editor:
                self.queue_inline_action(nid, e.toPlainText(), "恢复上一版"))
            secondary_controls.addWidget(restore_version)
            lock_script = QPushButton("解除定稿" if record.get("script_locked") else "定稿")
            lock_script.clicked.connect(
                lambda _=False, nid=str(node.node_id), e=editor:
                self.queue_inline_action(nid, e.toPlainText(), "切换剧本定稿"))
            secondary_controls.addWidget(lock_script)
            to_production = QPushButton("创建制片项目")
            to_production.setMinimumWidth(138)
            to_production.setToolTip(
                "只保存剧本并创建制片项目，不调用模型；进入项目后确认拆镜模型和参数")
            to_production.clicked.connect(
                lambda _=False, nid=str(node.node_id), e=editor,
                b=to_production, mc=model_combo: self.queue_inline_action(
                    nid, e.toPlainText(), "创建制片项目", mc.currentData(), b))
            secondary_controls.addStretch()
            secondary_controls.addWidget(to_production)
        elif node.node_type == "storyboard_node":
            planning_controls = QHBoxLayout()
            planning_controls.addWidget(QLabel("拆镜模型"))
            planning_model_combo = QComboBox()
            planning_model_combo.setToolTip(
                "阶段 1 只使用这里确认的文本模型；系统不会静默切换到其他模型")
            for label, provider_name, model_name in self._available_script_models():
                planning_model_combo.addItem(label, (provider_name, model_name))
            saved_planning = (
                str(node.payload.get("planning_provider") or ""),
                str(node.payload.get("planning_model") or ""),
            )
            planning_index = next((
                index for index in range(planning_model_combo.count())
                if tuple(planning_model_combo.itemData(index) or ("", "")) == saved_planning
            ), -1)
            if planning_index < 0 and saved_planning[1]:
                planning_index = next((
                    index for index in range(planning_model_combo.count())
                    if str((planning_model_combo.itemData(index) or ("", ""))[1]) ==
                    saved_planning[1]), -1)
            planning_model_combo.setCurrentIndex(max(0, planning_index))
            planning_model_combo.currentIndexChanged.connect(
                lambda _index, n=node, c=planning_model_combo: (
                    self.update_custom_setting(
                        n, "planning_provider", (c.currentData() or ("", ""))[0]),
                    self.update_custom_setting(
                        n, "planning_model", (c.currentData() or ("", ""))[1])))
            planning_controls.addWidget(planning_model_combo, 1)
            planning_controls.addWidget(QLabel("拆镜取向"))
            planning_temperature = QComboBox()
            for label, value in (("严格执行", 0.2), ("平衡（推荐）", 0.5), ("更有创意", 0.8)):
                planning_temperature.addItem(label, value)
            planning_temperature.setCurrentIndex(max(
                0, planning_temperature.findData(float(
                    node.payload.get("planning_temperature") or 0.5))))
            planning_temperature.currentIndexChanged.connect(
                lambda _index, n=node, c=planning_temperature:
                self.update_custom_setting(
                    n, "planning_temperature", float(c.currentData() or 0.5)))
            planning_controls.addWidget(planning_temperature)
            layout.addLayout(planning_controls)

            automation_combo = QComboBox()
            automation_combo.addItem("关键节点确认（推荐）", "checkpoints")
            automation_combo.addItem("全自动", "auto")
            automation_combo.addItem("逐步控制", "manual")
            saved_automation = str(node.payload.get("automation_mode") or "checkpoints")
            automation_combo.setCurrentIndex(max(
                0, automation_combo.findData(saved_automation)))
            automation_combo.setToolTip(
                "关键节点确认：技术步骤自动完成，只在资产和定稿图片候选处暂停；"
                "全自动：自动采用当前候选并完成视频；逐步控制：显示原始阶段。")
            controls.addWidget(QLabel("制片方式")); controls.addWidget(automation_combo, 1)

            mode = QComboBox()
            mode.addItems(list(MANUAL_PRODUCTION_STEPS))
            mode.setCurrentIndex(self._manual_combo_index(node.payload))
            mode.setToolTip("每个阶段都由你手动放行；第 3 步会按镜头时长生成 3–6 格运动关键帧板")
            manual_label = QLabel("高级步骤")
            manual_label.setVisible(saved_automation == "manual")
            mode.setVisible(saved_automation == "manual")
            controls.addWidget(manual_label); controls.addWidget(mode, 1)
            action_getter = lambda c=automation_combo, m=mode: (
                m.currentText() if c.currentData() == "manual" else "自动开始 / 继续")

            gate_note = QLabel(self._production_stage_message(node.payload))
            gate_note.setWordWrap(True)
            gate_note.setStyleSheet(
                "color:#9fb5d8;background:#1c2430;border:1px solid #31415a;"
                "border-radius:8px;padding:8px 10px;")
            layout.addWidget(gate_note)
            automation_combo.currentIndexChanged.connect(
                lambda _index, n=node, c=automation_combo, combo=mode,
                label=manual_label, note=gate_note:
                self._set_production_automation_mode(
                    n, str(c.currentData() or "checkpoints"), combo, label, note))
            style_combo = QComboBox()
            style_combo.addItems(["电影写实", "黑白手绘分镜", "动画电影", "商业广告", "纪录片", "复古胶片"])
            style_combo.setCurrentText(str(node.payload.get("style") or "电影写实"))
            style_combo.currentTextChanged.connect(
                lambda value, n=node: self.update_custom_setting(n, "style", value))
            controls.addWidget(QLabel("画面风格")); controls.addWidget(style_combo)
            count_combo = QComboBox()
            count_combo.addItem("自动（推荐）", 0)
            # The planning/checkpoint pipeline accepts every positive count,
            # including a partial final batch.  Keep the UI continuous so a
            # seven-beat script is not forced into six or eight shots.
            for count in range(1, 25):
                count_combo.addItem(f"{count} 镜", count)
            raw_saved_count = node.payload.get("shot_count")
            saved_count = int(raw_saved_count) if raw_saved_count is not None else 0
            saved_index = count_combo.findData(saved_count)
            if saved_index < 0:
                count_combo.addItem(f"{saved_count} 镜（已有工程）", saved_count)
                saved_index = count_combo.count() - 1
            count_combo.setCurrentIndex(saved_index)
            count_combo.currentIndexChanged.connect(
                lambda _index, n=node, c=count_combo:
                self.update_custom_setting(n, "shot_count", int(c.currentData() or 0)))
            count_combo.setToolTip(
                "自动模式会根据定稿、动作边界和可生成性决定镜头数；1–24镜为手动覆盖")
            controls.addWidget(QLabel("镜头数")); controls.addWidget(count_combo)
            model_combo = planning_model_combo

            production_controls = QHBoxLayout()
            production_controls.addWidget(QLabel("生产范围"))
            scope_combo = QComboBox()
            for label, value in (("全部镜头", "all"), ("未定稿", "missing"),
                                 ("已选择", "selected")):
                scope_combo.addItem(label, value)
            scope_combo.setCurrentIndex(max(
                0, scope_combo.findData(str(node.payload.get("production_scope") or "all"))))
            scope_combo.currentIndexChanged.connect(
                lambda _index, n=node, c=scope_combo:
                self.update_custom_setting(n, "production_scope", c.currentData() or "all"))
            scope_combo.setToolTip("创建生成器组时要处理的镜头范围")
            production_controls.addWidget(scope_combo)

            production_controls.addWidget(QLabel("画幅"))
            ratio_combo = QComboBox(); ratio_combo.addItems(["16:9", "9:16", "1:1", "4:5"])
            ratio_combo.setCurrentText(str(node.payload.get("production_ratio") or "16:9"))
            ratio_combo.currentTextChanged.connect(
                lambda value, n=node: self.update_custom_setting(n, "production_ratio", value))
            production_controls.addWidget(ratio_combo)
            production_controls.addWidget(QLabel("图片候选"))
            candidate_combo = QComboBox()
            for count in range(1, 5):
                candidate_combo.addItem(str(count), count)
            candidate_combo.setCurrentIndex(max(
                0, candidate_combo.findData(int(node.payload.get("candidate_count") or 2))))
            candidate_combo.currentIndexChanged.connect(
                lambda _index, n=node, c=candidate_combo:
                self.update_custom_setting(n, "candidate_count", int(c.currentData() or 2)))
            production_controls.addWidget(candidate_combo)
            production_controls.addWidget(QLabel("视频候选"))
            video_candidate_combo = QComboBox()
            for count in range(1, 5):
                video_candidate_combo.addItem(str(count), count)
            video_candidate_combo.setCurrentIndex(max(
                0, video_candidate_combo.findData(int(
                    node.payload.get("video_candidate_count") or 2))))
            video_candidate_combo.currentIndexChanged.connect(
                lambda _index, n=node, c=video_candidate_combo:
                self.update_custom_setting(
                    n, "video_candidate_count", int(c.currentData() or 2)))
            production_controls.addWidget(video_candidate_combo)
            production_controls.addStretch()
            layout.addLayout(production_controls)

            routing_controls = QHBoxLayout()

            manager = get_ai_manager()
            image_provider = QComboBox()
            image_provider.setToolTip(
                "项目级图片模型锁：角色/场景资产、手绘运动分镜、重抽分镜和定稿图统一使用此模型")
            for provider in manager.registry.by_capability("text_to_image"):
                image_provider.addItem(f"图 · {provider.name}", provider.name)
            # The board-level production contract is authoritative.  This also
            # migrates a lock selected in the script/storyboard workbench onto
            # older source nodes that still contain a stale provider value.
            saved_image_provider = str(
                self._storyboard_model_lock(str(node.node_id), "image_provider") or
                node.payload.get("image_provider") or "")
            if image_provider.count() and image_provider.findData(saved_image_provider) < 0:
                if saved_image_provider:
                    image_provider.addItem(
                        f"图 · {saved_image_provider}（当前不可用）",
                        saved_image_provider)
                else:
                    saved_image_provider = str(image_provider.itemData(0) or "")
                    self._store_storyboard_model_lock(
                        str(node.node_id), "image_provider", saved_image_provider)
            image_provider.setCurrentIndex(max(0, image_provider.findData(saved_image_provider)))
            image_provider.currentIndexChanged.connect(
                lambda _index, n=node, c=image_provider:
                self.update_custom_setting(n, "image_provider", c.currentData() or ""))
            routing_controls.addWidget(QLabel("图片模型")); routing_controls.addWidget(image_provider, 1)

            video_provider = QComboBox()
            video_provider.setToolTip(
                "项目级视频模型锁：故事板视频生成器统一使用此模型")
            video_providers = []
            for capability in ("image_to_video", "text_to_video"):
                for provider in manager.registry.by_capability(capability):
                    if provider.name not in [value.name for value in video_providers]:
                        video_providers.append(provider)
            for provider in video_providers:
                video_provider.addItem(f"视频 · {provider.name}", provider.name)
            saved_video_provider = str(
                self._storyboard_model_lock(str(node.node_id), "video_provider") or
                node.payload.get("video_provider") or "")
            if video_provider.count() and video_provider.findData(saved_video_provider) < 0:
                if saved_video_provider:
                    video_provider.addItem(
                        f"视频 · {saved_video_provider}（当前不可用）",
                        saved_video_provider)
                else:
                    saved_video_provider = str(video_provider.itemData(0) or "")
                    self._store_storyboard_model_lock(
                        str(node.node_id), "video_provider", saved_video_provider)
            video_provider.setCurrentIndex(max(0, video_provider.findData(saved_video_provider)))
            video_provider.currentIndexChanged.connect(
                lambda _index, n=node, c=video_provider:
                self.update_custom_setting(n, "video_provider", c.currentData() or ""))
            routing_controls.addWidget(QLabel("视频模型")); routing_controls.addWidget(video_provider, 1)
            layout.addLayout(routing_controls)

            video_mode_controls = QHBoxLayout()
            video_mode_controls.addWidget(QLabel("视频组织"))
            video_mode_combo = QComboBox()
            video_mode_combo.addItem("导演多镜头时间轴（Seedance 推荐）", "director_timeline")
            video_mode_combo.addItem("AI 智能分段（推荐）", "smart")
            video_mode_combo.addItem("整段 15 秒", "single_15")
            video_mode_combo.addItem("严格逐镜", "per_shot")
            saved_video_mode = str(node.payload.get("video_generation_mode") or "smart")
            video_mode_combo.setCurrentIndex(max(
                0, video_mode_combo.findData(saved_video_mode)))
            video_mode_combo.currentIndexChanged.connect(
                lambda _index, n=node, c=video_mode_combo:
                self.update_custom_setting(
                    n, "video_generation_mode", c.currentData() or "smart"))
            video_mode_combo.setToolTip(
                "导演时间轴允许一次生成内部按秒切镜、安排空镜/反打和推拉摇移；"
                "智能分段仅合并同机位连续动作；严格逐镜用于兼容旧模型。")
            video_mode_controls.addWidget(video_mode_combo, 1)
            video_mode_controls.addStretch()
            layout.addLayout(video_mode_controls)
        elif node.node_type == "skill_node":
            mode = QComboBox()
            is_auto_qc = str(node.payload.get("auto_qc_kind") or "") == "post_sequence"
            if is_auto_qc:
                mode.addItem("重新运行自动审片", "run")
            else:
                mode.addItem("执行 Skill", "run")
                mode.addItem("仅生成工作流，不执行", "build")
            controls.addWidget(mode, 1)
            if is_auto_qc:
                qc_source_id = str(node.payload.get("source_node_id") or "")
                qc_source = self._custom_record(qc_source_id) or {}
                qc_blocked = str(qc_source.get("pipeline_stage") or "") == "video_qc_review"
                explanation = QLabel(
                    "问题未处理前不会自动放行。你可以重做相关镜头，或明确接受风险。"
                    if qc_blocked else "本轮审片没有处于阻断状态；可以重新运行审片更新报告。")
                explanation.setWordWrap(True)
                explanation.setStyleSheet(
                    ("color:#efbd75;background:#30271c;border:1px solid #59452b;"
                     if qc_blocked else
                     "color:#71d6a3;background:#182b24;border:1px solid #28533f;") +
                    "border-radius:7px;padding:8px;")
                layout.addWidget(explanation)
                if qc_blocked:
                    secondary_controls = QHBoxLayout()
                    accept = QPushButton("接受本轮审片风险并继续…")
                    accept.clicked.connect(
                        lambda _=False, sid=qc_source_id:
                        QTimer.singleShot(0, lambda: self.accept_video_qc_risk(sid)))
                    secondary_controls.addWidget(accept, 1)
            else:
                strength = QComboBox()
                for label, value in (("轻度", 0.35), ("中度", 0.65), ("强烈", 0.9)):
                    strength.addItem(label, value)
                strength.setCurrentIndex(max(
                    0, strength.findData(float(node.payload.get("strength") or 0.65))))
                strength.currentIndexChanged.connect(
                    lambda _index, n=node, c=strength:
                    self.update_custom_setting(n, "strength", float(c.currentData() or 0.65)))
                controls.addWidget(strength)
            model_combo = None
        else:
            mode = QComboBox()
            mode.addItems(options)
            saved_action = str(node.payload.get("editor_action") or "")
            saved_action_index = mode.findText(saved_action)
            if saved_action_index >= 0:
                mode.setCurrentIndex(saved_action_index)
            mode.currentTextChanged.connect(
                lambda value, n=node: self.update_custom_setting(n, "editor_action", value))
            controls.addWidget(mode, 1)
            model_combo = None
            ratio = QComboBox(); ratio.addItems(["16:9", "9:16", "1:1", "4:5"])
            ratio.setCurrentText(str(node.payload.get("ratio") or "16:9"))
            ratio.currentTextChanged.connect(
                lambda value, n=node: self.update_custom_setting(n, "ratio", value))
            controls.addWidget(ratio)
            if node.node_type in ("image_node", "video_node"):
                capabilities = (("text_to_image", "image_edit")
                                if node.node_type == "image_node" else
                                ("image_to_video", "text_to_video"))
                provider_combo = QComboBox()
                seen_providers = set()
                for capability in capabilities:
                    for provider in get_ai_manager().registry.by_capability(capability):
                        if provider.name not in seen_providers:
                            provider_combo.addItem(provider.name, provider.name)
                            seen_providers.add(provider.name)
                source_id = self._production_source_for_generator(node.node_id)
                project_locked = ""
                if source_id:
                    project_locked = self._storyboard_model_lock(
                        source_id,
                        "image_provider" if node.node_type == "image_node"
                        else "video_provider")
                saved_provider = str(
                    project_locked or node.payload.get("provider_name") or "")
                provider_combo.setCurrentIndex(max(
                    0, provider_combo.findData(saved_provider)))
                provider_combo.currentIndexChanged.connect(
                    lambda _index, n=node, c=provider_combo:
                    self.update_custom_setting(n, "provider_name", c.currentData() or ""))
                if project_locked:
                    provider_combo.setEnabled(False)
                    provider_combo.setToolTip(
                        "此节点属于故事板生产组，模型由故事板的项目级模型锁统一控制")
                else:
                    provider_combo.setToolTip("当前节点使用的生成模型")
                controls.addWidget(provider_combo)
            if node.node_type == "video_node":
                duration_combo = QComboBox()
                for seconds in (5, 6, 8, 10, 12, 15):
                    duration_combo.addItem(f"{seconds}s", seconds)
                saved_duration = int(float(node.payload.get("duration") or 5))
                duration_combo.setCurrentIndex(max(
                    0, duration_combo.findData(saved_duration)))
                duration_combo.currentIndexChanged.connect(
                    lambda _index, n=node, c=duration_combo:
                    self.update_custom_setting(n, "duration", int(c.currentData() or 5)))
                duration_combo.setToolTip("不同模型会在提交时自动限制到其支持的时长")
                controls.addWidget(duration_combo)
            if node.node_type == "audio_node":
                provider_combo = QComboBox()
                for provider in get_ai_manager().registry.by_capability("text_to_speech"):
                    provider_combo.addItem(provider.name, provider.name)
                saved_provider = str(node.payload.get("provider_name") or "")
                provider_combo.setCurrentIndex(max(0, provider_combo.findData(saved_provider)))
                saved_provider = str(provider_combo.currentData() or saved_provider)
                controls.addWidget(provider_combo)
                try:
                    from ui.voice_picker import VoiceSelectButton
                    voice_picker = VoiceSelectButton()
                    voice_picker._voice_id = str(node.payload.get("voice") or "zh-CN-XiaoxiaoNeural")
                    voice_picker._voice_name = str(node.payload.get("voice_name") or "选择音色")
                    voice_picker.setText(f"🎵  {voice_picker._voice_name}")
                    voice_picker.set_engine("edge" if saved_provider == "edge_tts" else saved_provider)
                    voice_picker.voice_changed.connect(
                        lambda voice_id, name, n=node:
                        (self.update_custom_setting(n, "voice", voice_id),
                         self.update_custom_setting(n, "voice_name", name)))
                    provider_combo.currentIndexChanged.connect(
                        lambda _index, n=node, c=provider_combo, picker=voice_picker:
                        (self.update_custom_setting(n, "provider_name", c.currentData() or ""),
                         picker.set_engine("edge" if c.currentData() == "edge_tts" else
                                           str(c.currentData() or "auto_lang"))))
                    controls.addWidget(voice_picker)
                except ImportError:
                    voice_edit = QLineEdit(str(node.payload.get("voice") or ""))
                    voice_edit.setPlaceholderText("音色 ID")
                    voice_edit.editingFinished.connect(
                        lambda n=node, e=voice_edit:
                        self.update_custom_setting(n, "voice", e.text().strip()))
                    controls.addWidget(voice_edit)
                rate_combo = QComboBox()
                for label, value in (("0.8x", 0.8), ("0.9x", 0.9), ("1.0x", 1.0),
                                     ("1.1x", 1.1), ("1.2x", 1.2)):
                    rate_combo.addItem(label, value)
                rate_combo.setCurrentIndex(max(0, rate_combo.findData(float(node.payload.get("speed") or 1))))
                rate_combo.currentIndexChanged.connect(
                    lambda _index, n=node, c=rate_combo:
                    self.update_custom_setting(n, "speed", float(c.currentData() or 1)))
                controls.addWidget(rate_combo)
                inserts = QHBoxLayout()
                for label, token in (("停顿 0.5s", "[停顿:0.5]"), ("停顿 1s", "[停顿:1]"),
                                     ("叹气", "[叹气]"), ("轻笑", "[轻笑]"),
                                     ("犹豫", "[犹豫]"), ("语气", "[语气:克制]")):
                    button = QPushButton(label)
                    button.clicked.connect(lambda _=False, e=editor, value=token: e.insertPlainText(value))
                    inserts.addWidget(button)
                inserts.addStretch()
                layout.addLayout(inserts)
        if node.node_type == "shot":
            shot_value = self._find_shot(node.payload.get("shot_id")) or {}
            stage_value = (shot_value.get("scene_stage")
                           if isinstance(shot_value.get("scene_stage"), dict) else {})
            stage_row = QHBoxLayout()
            stage_label = QLabel(
                (f"3D站位 v{int(stage_value.get('version') or 1)} · "
                 f"{len(stage_value.get('objects') or [])} 对象 / "
                 f"{len(stage_value.get('cameras') or [])} 机位")
                if stage_value else "3D站位 · 尚未建立")
            stage_label.setStyleSheet(
                "color:#9fc2ff;background:#182334;border:1px solid #2f4c72;"
                "border-radius:7px;padding:7px 9px;")
            stage_row.addWidget(stage_label, 1)
            stage_button = QPushButton("打开 3D 导演台")
            stage_button.setToolTip("拖动人物和固定物、设置摄影机与 FOV，并保存构图控制图")
            stage_button.clicked.connect(
                lambda _=False, sid=str(node.payload.get("shot_id") or ""):
                (self.hide_inline_editor(), self.open_scene_stage(sid)))
            stage_row.addWidget(stage_button)
            layout.addLayout(stage_row)
        node_has_active_task = any(
            str(task.get("node_id") or "") == str(node.node_id) and
            not task["handle"].is_finished
            for task in self._standalone_tasks.values())
        run = QPushButton("停止" if node_has_active_task else "↑")
        run.setObjectName("runNode")
        run.setFixedWidth(58 if node_has_active_task else 42)
        run.clicked.connect(
            lambda _=False, nid=str(node.node_id), e=editor, m=mode,
            mc=model_combo, b=run, get_action=action_getter,
            stop=node_has_active_task: self.queue_inline_action(
                nid, e.toPlainText(),
                ("停止生成" if stop else get_action() if get_action is not None else
                 (m.currentText() if m is not None else "生成完整脚本")),
                mc.currentData() if mc is not None else None, b))
        if node.node_type == "storyboard_node" and not node_has_active_task:
            run.setText(
                "确认参数并拆镜"
                if not str(node.payload.get("pipeline_stage") or "") else
                "开始 / 继续")
            run.setFixedWidth(92)
        controls.addWidget(run)
        layout.addLayout(controls)
        if secondary_controls is not None:
            layout.addLayout(secondary_controls)
        proxy = QGraphicsProxyWidget()
        proxy.setWidget(panel)
        proxy.setZValue(200)
        proxy.setPos(node.pos() + QPointF(0, node.height + 18))
        self.scene.addItem(proxy)
        self.scene.ensure_item_visible(proxy)
        self._inline_editor_proxy = proxy
        self._inline_editor_node_id = node.node_id
        self._inline_editor_dirty = False

    def _inline_editing_started(self, node):
        self._inline_editor_typing = True
        node.setSelected(True)

    def _inline_editing_stopped(self, node_id: str, editor):
        self._commit_inline_editor_text(node_id, editor)
        self._inline_editor_typing = False

    def _update_inline_editor_draft(self, node_id: str, text: str):
        """Write live text to the node model without rebuilding the scene."""
        node_id = str(node_id or "")
        node = self._nodes.get(node_id)
        if node is None:
            return False
        text = str(text)
        changed = False
        if node.node_type == "shot":
            shot = self._find_shot(node.payload.get("shot_id"))
            if shot is not None and str(shot.get("visual") or "") != text:
                shot["visual"] = text
                node.payload["shot"] = shot
                # The previously compiled prompts no longer describe this
                # shot.  Keep generated media, but require prompt recompilation
                # before another production request.
                shot["production_ready"] = False
                shot.pop("final_image_prompt", None)
                shot.pop("final_video_prompt", None)
                node.badge = "提示词待重编译"
                changed = True
        else:
            record = self._custom_record(node_id)
            if record is not None:
                if str(record.get("content") or "") != text or "content" not in record:
                    record["content"] = text
                    changed = True
            else:
                overrides = self._positions().setdefault(
                    "__inline_text_overrides__", {})
                if not isinstance(overrides, dict):
                    overrides = {}
                    self._positions()["__inline_text_overrides__"] = overrides
                if str(overrides.get(node_id, "")) != text or node_id not in overrides:
                    overrides[node_id] = text
                    changed = True
            if str(node.payload.get("content") or "") != text or "content" not in node.payload:
                node.payload["content"] = text
                changed = True
            if node.payload.get("custom"):
                node.subtitle = text or "点击节点，在下方编辑"
        if not changed:
            return False
        self._inline_editor_dirty = True
        node.update()
        self._layout_timer.start()
        return True

    def _commit_inline_editor_text(self, node_id: str = "", editor=None):
        """Persist the current editor before hiding, switching or refreshing."""
        node_id = str(node_id or self._inline_editor_node_id or "")
        editor = editor or self._inline_text_editor
        if not node_id or editor is None:
            return False
        try:
            text = editor.toPlainText()
        except RuntimeError:
            return False
        changed_now = self._update_inline_editor_draft(node_id, text)
        dirty = bool(self._inline_editor_dirty or changed_now)
        if dirty:
            node = self._nodes.get(node_id)
            if node is not None and node.node_type == "shot":
                self.storyboardMutated.emit()
            self._save_layout_now()
            self._inline_editor_dirty = False
        return dirty

    def queue_inline_action(self, node_id: str, content: str, action: str,
                            model_data=None, button=None):
        """Leave the emitting Qt widget alive until its clicked signal returns.

        Several canvas actions rebuild the QGraphicsScene.  Executing them
        directly from a button embedded in QGraphicsProxyWidget destroys the
        signal sender while Qt is still dispatching the click, which can cause
        an unrecoverable Qt6Core access violation rather than a Python error.
        Only immutable Python data crosses this event-loop boundary.
        """
        if button is not None:
            button.setEnabled(False)
        payload = (
            str(node_id), str(content), str(action),
            model_data if isinstance(model_data, (str, int, float, bool, tuple, list, dict,
                                                   type(None))) else None,
        )
        QTimer.singleShot(0, lambda data=payload: self._run_queued_inline_action(*data))

    def _run_queued_inline_action(self, node_id: str, content: str, action: str,
                                  model_data=None):
        # Remove the proxy from the scene first.  deleteLater() then runs only
        # after this callback returns, so scene.clear() cannot destroy the
        # currently dispatching editor or button.
        self.hide_inline_editor()
        node = self._nodes.get(str(node_id))
        if node is None:
            return
        try:
            self.run_inline_action(node, content, action, model_data)
        except Exception as error:
            QMessageBox.warning(self, "画布操作失败", str(error))

    def handle_inline_text_delete(self, key):
        editor = self._inline_text_editor
        if not self._inline_editor_typing or editor is None:
            return False
        cursor = editor.textCursor()
        if cursor.hasSelection():
            cursor.removeSelectedText()
        elif key == Qt.Key.Key_Backspace:
            cursor.deletePreviousChar()
        else:
            cursor.deleteChar()
        editor.setTextCursor(cursor)
        return True

    def _custom_record(self, node_id):
        return next((value for value in self._positions().get("__custom_nodes__", [])
                     if isinstance(value, dict) and value.get("id") == node_id), None)

    def _inline_editor_saved_height(self, node, content: str) -> int:
        """恢复用户高度；首次打开时按内容量给出舒服的阅读面积。"""
        heights = self._positions().get("__inline_editor_heights__", {})
        if isinstance(heights, dict):
            try:
                saved = int(heights.get(str(node.node_id)) or 0)
            except (TypeError, ValueError):
                saved = 0
            if saved:
                return max(140, min(520, saved))
        logical_lines = sum(
            max(1, (len(line) + 59) // 60)
            for line in str(content or "").splitlines() or [""])
        minimum = 220 if node.node_type in ("storyboard_node", "text_node", "shot") else 180
        return max(minimum, min(360, 44 + logical_lines * 23))

    def _store_inline_editor_height(self, node_id: str, height: int):
        heights = self._positions().setdefault("__inline_editor_heights__", {})
        if not isinstance(heights, dict):
            heights = {}
            self._positions()["__inline_editor_heights__"] = heights
        heights[str(node_id)] = max(140, min(520, int(height)))
        self._save_layout_now()

    def update_custom_setting(self, node, key: str, value):
        record = self._custom_record(node.node_id)
        if record is None:
            return
        old_value = record.get(key)
        if node.node_type == "storyboard_node" and key == "production_ratio":
            value = normalize_aspect_ratio(value)
        record[key] = value
        node.payload[key] = value
        if node.node_type == "storyboard_node" and key in {
                "image_provider", "video_provider"}:
            self._store_storyboard_model_lock(str(node.node_id), key, str(value or ""))
        if (node.node_type == "storyboard_node" and key == "production_ratio" and
                normalize_aspect_ratio(old_value) != value):
            board = self.current_storyboard()
            board["production_ratio"] = value
            stale = False
            self._canvas_storyboard_queue = []
            for shot in board.get("shots", []):
                if not isinstance(shot, dict):
                    continue
                had_board = bool(shot.get("motion_board_path") or
                                 shot.get("motion_panel_paths"))
                shot.pop("motion_panel_pending_paths", None)
                shot.pop("motion_panel_pending_generation_id", None)
                shot.pop("motion_panel_pending_aspect_ratio", None)
                if not had_board:
                    continue
                stale = True
                shot["motion_board_review_status"] = "stale_aspect_ratio"
                shot["production_ready"] = False
                for prompt_key in (
                        "final_image_prompt", "final_start_image_prompt",
                        "final_end_image_prompt", "final_video_prompt"):
                    shot.pop(prompt_key, None)
                for asset in shot.get("assets", []):
                    if (isinstance(asset, dict) and
                            str(asset.get("subtype") or "") == "motion_storyboard"):
                        asset["approved"] = False
            if stale:
                record["pipeline_stage"] = "assets_ready"
                record["approval_required"] = "storyboard_panels"
                record["auto_run_enabled"] = False
                record["status"] = (
                    f"画幅已改为 {value} · 原运动分镜已保留为历史版本，"
                    "请重新生成原生画幅分镜")
                self.storyboardMutated.emit()
        self._save_layout_now()

    def _storyboard_production_ratio(self, source_id: str = "") -> str:
        source_id = str(source_id or self._canvas_storyboard_source or
                        self._current_production_source_id() or "")
        source = self._custom_record(source_id) or {}
        board = self.current_storyboard()
        ratio = source.get("production_ratio") or board.get("production_ratio") or "16:9"
        ratio = normalize_aspect_ratio(ratio)
        board["production_ratio"] = ratio
        return ratio

    def _storyboard_model_lock(self, source_id: str, key: str) -> str:
        """Return one project-level model lock, migrating old node-only values."""
        source_id = str(source_id or self._current_production_source_id() or "")
        source = self._custom_record(source_id) or {}
        board = self.current_storyboard()
        locks = board.get("production_models")
        if not isinstance(locks, dict):
            locks = {}; board["production_models"] = locks
        bible = board.get("visual_bible")
        if not isinstance(bible, dict):
            bible = {}
        # Cross-module contract first, then the legacy visual-bible value, and
        # only then the old canvas-node setting.  Otherwise a stale GPT Image
        # value on the node overrides Seedream selected in the storyboard UI.
        value = str(locks.get(key) or bible.get(key) or source.get(key) or "")
        if value:
            self._store_storyboard_model_lock(source_id, key, value)
        return value

    def _store_storyboard_model_lock(self, source_id: str, key: str, value: str):
        source_id, value = str(source_id or ""), str(value or "")
        source = self._custom_record(source_id)
        if source is not None:
            source[key] = value
        board = self.current_storyboard()
        locks = board.setdefault("production_models", {})
        if not isinstance(locks, dict):
            locks = {}; board["production_models"] = locks
        locks[key] = value
        bible = board.setdefault("visual_bible", {})
        if not isinstance(bible, dict):
            bible = {}; board["visual_bible"] = bible
        bible[key] = value
        # Existing final-generator nodes must obey a later project model
        # change as well; do not leave a mixed-model batch on the canvas.
        generator_kind = "image" if key == "image_provider" else "video"
        groups = [row for row in self._positions().get("__custom_nodes__", [])
                  if isinstance(row, dict) and
                  str(row.get("source_node_id") or "") == source_id and
                  row.get("generator_kind") == generator_kind]
        member_ids = {str(member) for group in groups
                      for member in group.get("group_nodes", [])}
        for row in self._positions().get("__custom_nodes__", []):
            if (isinstance(row, dict) and row.get("id") in member_ids and
                    row.get("generator_kind") == generator_kind):
                row["provider_name"] = value
                live_node = self._nodes.get(str(row.get("id") or ""))
                if live_node is not None:
                    live_node.payload["provider_name"] = value

    def _locked_storyboard_image_provider(self, operation: str,
                                          source_id: str = ""):
        """Resolve the project's explicit image model without silent fallback."""
        source_id = str(source_id or self._canvas_storyboard_source or
                        self._current_production_source_id() or "")
        providers = get_ai_manager().registry.by_capability(operation)
        preferred = self._storyboard_model_lock(source_id, "image_provider")
        if preferred:
            provider = next((item for item in providers
                             if item.name == preferred), None)
            if provider is None:
                raise RuntimeError(
                    f"故事板已统一锁定图片模型“{preferred}”，但该模型当前不支持或未配置 "
                    f"{operation}。系统已停止，不会静默切换到 GPT Image。")
            return provider
        if not providers:
            raise RuntimeError(f"当前没有支持 {operation} 的图片模型")
        # Legacy projects had no persisted project lock. Adopt the visible
        # registry default once, then persist it as an explicit project lock.
        provider = providers[0]
        self._store_storyboard_model_lock(source_id, "image_provider", provider.name)
        return provider

    @staticmethod
    def _manual_combo_index(payload: dict):
        stage = str(payload.get("pipeline_stage") or "")
        if stage in ("", "planning", "shots_ready"):
            return 0 if stage in ("", "planning") else 1
        if stage in ("assets_generating",):
            return 1
        if stage in ("assets_generated", "assets_changed", "assets_ready",
                     "blocking_generating", "storyboard_panels_generating"):
            return 2
        if stage == "storyboard_panels_ready":
            return 3
        if stage in ("prompts_ready", "generators_ready", "images_generating"):
            return 4
        if stage in ("start_image_candidates_ready", "image_candidates_ready",
                     "video_generating", "video_candidates_ready",
                     "video_handoff_blocked", "video_qc_pending", "video_qc_review"):
            return 5
        if stage in ("video_ready", "audio_generators_ready", "audio_generating",
                     "production_ready"):
            return 6
        if stage == "production_interrupted":
            return {"image":4, "video":5, "audio":6}.get(
                str(payload.get("interrupted_kind") or ""), 0)
        return 0

    def _manual_production_control(self, payload: dict):
        """Describe the one useful action for the current manual stage."""
        stage = str(payload.get("pipeline_stage") or "")
        index = self._manual_combo_index(payload)
        action = MANUAL_PRODUCTION_STEPS[index]
        running_labels = {
            "planning":"第 1 步 · AI 正在拆解镜头",
            "assets_generating":"第 2 步 · AI 正在生成资产",
            "blocking_generating":"第 3 步 · AI 正在计算空间调度",
            "storyboard_panels_generating":"第 3 步 · AI 正在生成多帧运动分镜",
            "images_generating":"第 5 步 · AI 正在生成图片候选",
            "video_generating":"第 6 步 · AI 正在生成视频",
            "video_qc_pending":"第 6 步 · AI 正在自动审片",
            "audio_generating":"第 7 步 · AI 正在生成对白",
        }
        if stage in running_labels:
            label = "… " + running_labels[stage]
            return label, "", False, "当前任务完成后，按钮会自动变成下一步。"
        if stage in ("assets_generated", "assets_changed"):
            source_id = str(payload.get("id") or "")
            unlocked = [value for value in self._storyboard_asset_node_ids(source_id)
                        if not bool((self._custom_record(value) or {}).get("locked"))]
            if unlocked:
                return (f"锁定资产后继续 · 剩 {len(unlocked)} 项",
                        "focus_unlocked_asset", True,
                        "先挑选并锁定角色、场景和道具；点击定位到第一个未锁定资产。")
        if stage == "start_image_candidates_ready":
            return ("▶ 第 5 步 · 确认 K1 并生成 Klast",
                    "prepare_end_frames", True,
                    "采用每镜当前起始帧候选，再以它和场景母版生成同空间结束帧。")
        if stage == "video_candidates_ready":
            return ("请选择当前段定稿视频", "", False,
                    "画布已显示本段全部视频候选；采用一条后会自动审片，"
                    "通过后才生成下一连续段。")
        if stage == "video_handoff_blocked":
            return ("连续续接已暂停", "", False,
                    "上一段必须先采用候选、通过审片并成功提取真实尾帧。"
                    "系统不会回退到另一张计划首帧。")
        if stage == "production_interrupted":
            return "↻ 恢复中断步骤", "resume", True, "从未完成的生成器继续，不会重做已完成结果。"
        if stage == "video_qc_review":
            node_exists = f"auto-qc:{str(payload.get('id') or '')}" in self._nodes
            return (("⚠ 查看审片问题" if node_exists else "↻ 恢复审片报告"),
                    "focus_video_qc", True,
                    ("单段或相邻镜头连续性没有通过；点击定位审片节点后可局部重做，或明确接受风险继续。"
                     if node_exists else
                     "审片报告节点已被隐藏，但质量问题仍然有效；点击重建报告并定位。"))
        if stage == "production_ready":
            return "✓ 本轮已完成", "", False, "可以替换候选或局部重做；无需再次推进。"
        labels = {
            0:"▶ 第 1 步 · 拆解镜头",
            1:"▶ 第 2 步 · 生成资产",
            2:"▶ 第 3 步 · 调度与运动分镜",
            3:"▶ 第 4 步 · 合成定稿提示词",
            4:("▶ 第 5 步 · 执行图片候选" if stage == "generators_ready"
               else "▶ 第 5 步 · 生成图片候选"),
            5:"▶ 第 6 步 · 用定稿图生成视频",
            6:("▶ 第 7 步 · 执行对白音频" if stage == "audio_generators_ready"
               else "▶ 第 7 步 · 生成对白音频"),
        }
        tips = {
            0:"把一句创意拆成可制作镜头；完成后停下让你确认。",
            1:"生成角色四件套、场景和道具候选；完成后由你挑选。",
            2:"只有资产锁定后才会生成站位、轴线、运镜和每镜 3–6 格运动关键帧。",
            3:"把确认过的调度与资产编译成每镜定稿提示词。",
            4:"创建并立即执行图片生成器；完成后由你逐镜选择定稿。",
            5:"只使用你已经定稿的图片生成连续视频段。",
            6:"创建并执行外部 TTS 对白节点；无对白时直接完成。",
        }
        return labels[index], action, True, tips[index]

    def _production_stage_message(self, payload: dict):
        mode = str(payload.get("automation_mode") or "checkpoints")
        stage = str(payload.get("pipeline_stage") or "")
        status = str(payload.get("status") or "")
        readiness = self._readiness_message(payload)
        if readiness:
            return readiness + (f"\n{status}" if status and status not in readiness else "")
        if mode == "manual":
            _label, _action, _enabled, tip = self._manual_production_control(payload)
            return f"{status}\n{tip}" if status else tip
        friendly = {
            "planning": "AI 正在理解创意并规划镜头。",
            "shots_ready": "镜头规划已完成，正在准备角色、场景和道具。",
            "assets_generating": "角色、场景和道具候选正在生成。",
            "assets_ready": "资产已确认，正在建立空间、站位、轴线和机位约束。",
            "storyboard_panels_ready": "调度分镜已完成，正在自动整理成定稿生成指令。",
            "prompts_ready": "定稿生成指令已就绪，正在准备图片候选。",
            "generators_ready": "图片任务已就绪，即将开始生成候选。",
            "images_generating": "每个镜头的定稿图片候选正在生成。",
            "start_image_candidates_ready": (
                "起始帧候选已就绪；确认每镜 K1 后，系统会用它生成同空间结束帧。"),
            "video_ready": "视频已完成，正在检查是否需要对白音频。",
            "video_candidates_ready": (
                "当前视频段的候选已就绪；请选择一条定稿，审片通过后才会继续。"),
            "video_handoff_blocked": (
                "连续段缺少上一段已批准的真实尾帧，生产线已暂停且不会静默跳帧。"),
            "video_qc_pending": "视频已经生成，AI 正在检查每段质量和相邻镜头连续性。",
            "video_qc_review": "自动审片发现阻断问题；只会标记相关镜头，不会自动删除或重生成。",
            "audio_generators_ready": "对白音频任务已准备，正在选择默认音色。",
            "production_interrupted": "上次生产被中断；点击程序坞“继续制片”会从未完成处恢复。",
        }
        if stage in friendly:
            return friendly[stage]
        if stage in ("blocking_generating", "storyboard_panels_generating"):
            return "AI 正在完成空间调度、轴线和多帧运动分镜，结束后会自动继续。"
        if stage == "assets_generated":
            return ("请选择满意的角色 / 场景资产并锁定；全部锁定后会自动继续。"
                    if mode == "checkpoints" else "AI 正在采用当前资产候选并继续。")
        if stage == "image_candidates_ready":
            return ("结束帧候选已放到各镜头旁：确认每镜 Klast 后，首尾双锚点会一起送入视频模型。"
                    if mode == "checkpoints" else "AI 正在采用当前首尾帧并生成视频。")
        if stage in ("video_generating", "audio_generating"):
            return "生成任务正在后台执行，完成后会自动进入下一项。"
        if stage in ("video_ready", "production_ready"):
            return "本轮生产已完成；你仍可在画布替换候选并局部重做。"
        if status and stage:
            return status
        if not stage and status:
            return status + "。确认下方设置后点击“确认参数并拆镜”，此时才会调用文本模型。"
        return ("输入创意后点“开始 / 继续”。系统会自动完成技术步骤，"
                "在资产、定稿图片和视频候选处暂停。")

    def _set_production_automation_mode(self, node, value: str,
                                        manual_combo=None, manual_label=None,
                                        note_label=None):
        value = value if value in ("checkpoints", "auto", "manual") else "checkpoints"
        self.update_custom_setting(node, "automation_mode", value)
        record = self._custom_record(node.node_id)
        if record is not None and value == "manual":
            record["auto_run_enabled"] = False
            record.pop("awaiting_gate", None)
        if manual_combo is not None:
            manual_combo.setVisible(value == "manual")
            if value == "manual":
                manual_combo.setCurrentIndex(
                    self._manual_combo_index(record or node.payload))
        if manual_label is not None:
            manual_label.setVisible(value == "manual")
        if note_label is not None:
            note_label.setText(self._production_stage_message(record or node.payload))
        self._update_production_continue_button()

    def choose_node_references(self, node, button):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择参考素材", "",
            "媒体文件 (*.png *.jpg *.jpeg *.webp *.bmp *.mp4 *.mov *.mkv *.webm);;所有文件 (*)")
        if not paths:
            return
        record = self._custom_record(node.node_id)
        if record is None:
            return
        values = list(dict.fromkeys(
            [str(value) for value in record.get("references", []) if value] + paths))[:9]
        record["references"] = values
        node.payload["references"] = values
        button.setText(
            f"＋资产参考 {len(values)}" if node.node_type == "video_node"
            else f"＋参考 {len(values)}")
        button.setToolTip("\n".join(Path(value).name for value in values))
        self._save_layout_now()

    def show_reference_menu(self, node, button):
        menu = QMenu(self)
        self._style_popup_menu(menu)
        clear_action = menu.addAction("清空参考素材")
        clear_action.setEnabled(bool(node.payload.get("references")))
        if menu.exec(button.mapToGlobal(button.rect().bottomLeft())) != clear_action:
            return
        record = self._custom_record(node.node_id)
        if record is None:
            return
        record["references"] = []
        record["reference_assets"] = []
        node.payload["references"] = []
        node.payload["reference_assets"] = []
        button.setText("＋资产参考" if node.node_type == "video_node" else "＋参考")
        button.setToolTip(
            "添加角色、场景、元素或风格参考；不会替代首尾帧"
            if node.node_type == "video_node" else
            "添加图片或视频参考素材；右键可清空")
        self._save_layout_now()

    def choose_video_frame(self, node, field: str, button=None):
        label = "首帧" if field == "first_frame" else "尾帧"
        path, _ = QFileDialog.getOpenFileName(
            self, f"选择视频{label}", "",
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp);;所有文件 (*)")
        if not path:
            return
        self.set_video_frame(node, field, path, button)

    def open_or_choose_video_frame(self, node, field: str, button=None):
        """Preview an assigned frame; only an empty slot opens the file picker."""
        if field not in ("first_frame", "last_frame"):
            return
        path = str(node.payload.get(field) or "")
        if path and os.path.exists(path) and self._is_image_path(path):
            self.open_media_preview(path, "image")
            return
        if path:
            label = "首帧" if field == "first_frame" else "尾帧"
            QMessageBox.information(
                self, f"{label}文件不可用",
                "这张图片已被移动或删除，请重新选择。")
        self.choose_video_frame(node, field, button)

    def set_video_frame(self, node, field: str, path: str, button=None):
        if field not in ("first_frame", "last_frame"):
            return False
        path = str(path or "")
        if path and (not os.path.exists(path) or not self._is_image_path(path)):
            QMessageBox.information(self, "视频参考帧", "请选择存在的图片文件。")
            return False
        record = self._custom_record(node.node_id)
        if record is None:
            return False
        record[field] = path
        node.payload[field] = path
        if field == "first_frame":
            record["first_frame_override"] = bool(path)
            node.payload["first_frame_override"] = bool(path)
        else:
            record["last_frame_override"] = bool(path)
            node.payload["last_frame_override"] = bool(path)
        first = bool(record.get("first_frame"))
        last = bool(record.get("last_frame"))
        record["status"] = "首尾帧已就绪" if first and last else (
            "首帧已就绪" if first else "尾帧待补首帧" if last else "待设置参考帧")
        node.badge = record["status"]
        node.update()
        if button is not None:
            label = "首帧" if field == "first_frame" else "尾帧"
            button.setText(f"{label} · {Path(path).name}" if path else f"＋ {label}")
            button.setToolTip(
                f"点击预览\n{path}\n右键可替换或清空" if path else
                ("点击选择视频开始画面" if field == "first_frame"
                 else "点击选择可选的视频结束画面"))
        self._save_layout_now()
        return True

    def show_video_frame_menu(self, node, field: str, button):
        menu = QMenu(self)
        self._style_popup_menu(menu)
        path = str(node.payload.get(field) or "")
        preview = menu.addAction("预览图片")
        preview.setEnabled(bool(
            path and os.path.exists(path) and self._is_image_path(path)))
        menu.addSeparator()
        replace = menu.addAction("重新选择")
        clear = menu.addAction("清空")
        clear.setEnabled(bool(path))
        chosen = menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        if chosen == preview:
            self.open_media_preview(path, "image")
        elif chosen == replace:
            self.choose_video_frame(node, field, button)
        elif chosen == clear:
            self.set_video_frame(node, field, "", button)

    def show_image_reference_role_menu(self, node, button=None):
        menu = QMenu(self)
        self._style_popup_menu(menu)
        actions = {
            menu.addAction(label): role
            for role, label in DIRECT_REFERENCE_ROLES.items()
        }
        chosen = menu.exec(
            button.mapToGlobal(button.rect().bottomLeft()) if button is not None
            else self.mapToGlobal(self.rect().center()))
        role = actions.get(chosen)
        if role:
            self.set_image_reference_role(node, role, button)

    def set_image_reference_role(self, node, role: str, button=None):
        role = role if role in DIRECT_REFERENCE_ROLES else "reference"
        record = self._custom_record(node.node_id)
        if record is None:
            return False
        record["reference_role"] = role
        node.payload["reference_role"] = role
        label = DIRECT_REFERENCE_ROLES[role]
        record["status"] = f"{label}参考"
        node.badge = record["status"]
        node.update()
        if button is not None:
            button.setText(f"⌾ {label}")
        self._save_layout_now()
        return True

    def toggle_node_mark(self, node, checked, button):
        record = self._custom_record(node.node_id)
        if record is None:
            return
        record["marked"] = bool(checked)
        node.payload["marked"] = bool(checked)
        node.badge = "已标记" if checked else ""
        if button is not None:
            button.setText("● 已标记" if checked else "◎ 标记")
        node.update()
        self._save_layout_now()

    def show_node_style_menu(self, node, button):
        menu = QMenu(self)
        self._style_popup_menu(menu)
        styles = ["无预设", "电影感", "写实摄影", "动漫", "商业广告", "纪录片", "复古胶片"]
        actions = {menu.addAction(value): value for value in styles}
        chosen = menu.exec(button.mapToGlobal(button.rect().topLeft()))
        style = actions.get(chosen)
        if not style:
            return
        style = "" if style == "无预设" else style
        record = self._custom_record(node.node_id)
        if record is None:
            return
        record["style"] = style
        node.payload["style"] = style
        button.setText(f"◇ {style}" if style else "◇ 风格")
        self._save_layout_now()

    @staticmethod
    def _apply_style_to_prompt(prompt: str, style: str):
        suffix = {
            "电影感": "电影级构图与光影，层次丰富，叙事镜头语言",
            "写实摄影": "真实摄影质感，自然光线，可信材质与细节",
            "动漫": "高质量动画美术，清晰线条，统一角色设计",
            "商业广告": "高级商业广告视觉，产品质感，干净构图",
            "纪录片": "纪实摄影，自然环境光，真实现场感",
            "复古胶片": "复古胶片色彩，细腻颗粒，柔和高光",
        }.get(style, "")
        return f"{prompt}\n\n视觉风格：{suffix}" if suffix else prompt

    def _available_script_models(self):
        manager = get_ai_manager()
        try:
            from api_config import get as api_get
            default_model = api_get("llm").default_model or "gpt-5.5"
        except Exception:
            default_model = "gpt-5.5"
        result = []
        for provider in manager.registry.by_capability("chat"):
            if provider.name == "deepseek":
                result.append(("DeepSeek Chat", provider.name, "deepseek-chat"))
            else:
                result.append((f"{provider.name} · {default_model}",
                               provider.name, default_model))
        return result or [("未配置文本模型", "", "")]

    @staticmethod
    def _script_model_seconds(label: str, model: str) -> int:
        value = f"{label} {model}".lower()
        if "gvlm 3.1 flash" in value:
            return 15
        if "gvlm 3.1" in value:
            return 20
        if "cvlm 5.5" in value or "qwen" in value or "deepseek" in value:
            return 10
        if "flash" in value:
            return 10
        return 20

    def run_inline_action(self, node, content: str, action: str, model_data=None):
        if action == "停止生成":
            cancelled = 0
            for task in self._standalone_tasks.values():
                if (str(task.get("node_id") or "") == str(node.node_id) and
                        not task["handle"].is_finished):
                    task["handle"].cancel(); cancelled += 1
            record = self._custom_record(str(node.node_id)) or node.payload
            record["status"] = ("已停止生成 · 原内容已保留"
                                if cancelled else "当前没有运行中的生成任务")
            self._save_layout_now(); self.refresh(); self.focus_node(str(node.node_id))
            return
        if node.payload.get("custom"):
            for data in self._positions().get("__custom_nodes__", []):
                if isinstance(data, dict) and data.get("id") == node.node_id:
                    data["content"] = content.strip()
                    node.payload["content"] = content.strip()
                    node.subtitle = content.strip() or "点击节点，在下方编辑"
                    node.update()
                    self._save_layout_now()
                    break
        if node.node_type == "shot":
            if action == "保存镜头修改":
                shot = self._find_shot(node.payload.get("shot_id"))
                if shot is not None:
                    shot["visual"] = content.strip()
                    shot["production_ready"] = False
                    for key in ("final_image_prompt", "final_start_image_prompt",
                                "final_end_image_prompt", "final_video_prompt"):
                        shot.pop(key, None)
                    node.subtitle = content.strip(); node.badge = "提示词待重编译"
                    node.update(); self.storyboardMutated.emit(); self.refresh()
                return
            operation = {
                "生成关键帧": "image", "参考图再生成": "image_edit",
                "生成视频": "video", "生成对白": "dialogue_audio",
            }.get(action)
            if operation:
                self.request_shot(node, operation)
                return
        if node.node_type == "text_node":
            if node.payload.get("copywriting_workbench") and action == "恢复口播原文":
                record = self._custom_record(str(node.node_id)) or node.payload
                original = str(record.get("copy_original") or "")
                if original:
                    record["content"] = original
                    record["status"] = "已恢复中文原文"
                    record.pop("copy_original", None)
                self._save_layout_now(); self.refresh(); self.focus_node(str(node.node_id))
                return
            if action == "采用AI候选稿":
                record = self._custom_record(str(node.node_id)) or node.payload
                candidate = str(record.get("script_candidate") or "").strip()
                if candidate:
                    save_script_version(record, content, "采用候选稿前")
                    record["content"] = candidate
                    snapshot = save_script_version(record, candidate, "采用AI候选稿")
                    record.pop("script_candidate", None)
                    record["script_locked"] = False
                    record["status"] = f"剧本 V{snapshot['version']} · 候选稿已采用"
                self._save_layout_now(); self.refresh(); self.focus_node(str(node.node_id))
                return
            if action == "清除AI结果":
                record = self._custom_record(str(node.node_id)) or node.payload
                record.pop("script_review", None); record.pop("script_candidate", None)
                record["status"] = "剧本草稿 · 可继续编辑"
                self._save_layout_now(); self.refresh(); self.focus_node(str(node.node_id))
                return
            if action == "保存剧本版本":
                record = self._custom_record(str(node.node_id)) or node.payload
                snapshot = save_script_version(record, content)
                record["content"] = str(content or "").strip()
                record["status"] = f"剧本 V{snapshot['version']} · 已保存"
                self._save_layout_now(); self.refresh(); self.focus_node(str(node.node_id))
                return
            if action == "切换剧本定稿":
                record = self._custom_record(str(node.node_id)) or node.payload
                snapshot = save_script_version(record, content, "定稿" if not record.get("script_locked") else "解除定稿")
                record["content"] = str(content or "").strip()
                record["script_locked"] = not bool(record.get("script_locked"))
                record["status"] = f"剧本 V{snapshot['version']} · " + ("已定稿" if record["script_locked"] else "编辑中")
                self._save_layout_now(); self.refresh(); self.focus_node(str(node.node_id))
                return
            if action == "恢复上一版":
                record = self._custom_record(str(node.node_id)) or node.payload
                save_script_version(record, content, "恢复前自动保存")
                target = previous_script_version(record)
                if target is not None:
                    record["content"] = str(target.get("content") or "")
                    snapshot = save_script_version(record, record["content"], "恢复上一版")
                    record["script_locked"] = False
                    record["status"] = f"剧本 V{snapshot['version']} · 已恢复"
                self._save_layout_now(); self.refresh(); self.focus_node(str(node.node_id))
                return
            if action in ("送入制片流程", "创建项目并拆镜", "创建制片项目"):
                self.create_storyboard_from_script(
                    node, content, auto_start=False,
                    planning_model_data=model_data)
                return
            self.submit_script_generation(node, content, action, model_data)
            return
        if node.node_type == "storyboard_node":
            if isinstance(model_data, (tuple, list)) and len(model_data) >= 2:
                record = self._custom_record(node.node_id) or node.payload
                record["planning_provider"] = str(model_data[0] or "")
                record["planning_model"] = str(model_data[1] or "")
                node.payload["planning_provider"] = record["planning_provider"]
                node.payload["planning_model"] = record["planning_model"]
            if action == "自动开始 / 继续":
                self.continue_canvas_production(
                    node, from_async=False, planning_confirmed=True)
                return
            record = self._custom_record(node.node_id) or node.payload
            if (str(record.get("automation_mode") or "checkpoints") == "manual" and
                    action in MANUAL_PRODUCTION_STEPS):
                self.advance_manual_production(node.node_id, action)
                return
            self.submit_canvas_storyboard(node, content, action)
            return
        if node.node_type == "skill_node":
            self.update_custom_setting(node, "content", content.strip())
            self.execute_canvas_skill(node, execute=not action.startswith("仅"))
            return
        if node.node_type in ("image_node", "video_node", "audio_node"):
            if node.node_type == "video_node" and action == "提取首中尾帧":
                self.extract_video_frames_to_canvas(node); return
            if node.node_type == "video_node" and action == "基于尾帧续拍":
                self.continue_video_from_tail(node, content); return
            self.submit_standalone_generation(node, content, action)

    @staticmethod
    def _is_image_path(path: str):
        return Path(str(path or "")).suffix.lower() in {
            ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}

    @staticmethod
    def _is_video_path(path: str):
        return Path(str(path or "")).suffix.lower() in {
            ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpeg", ".mpg"}

    def _upstream_media_path(self, node_id: str, image_only=False):
        node = self._nodes.get(node_id)
        if node:
            direct_path = str(node.payload.get("path") or node.thumbnail or "")
            if (direct_path and os.path.exists(direct_path) and
                    (not image_only or self._is_image_path(direct_path))):
                return direct_path
            own_refs = [str(value) for value in node.payload.get("references", []) if value]
            own_path = next((value for value in own_refs if os.path.exists(value) and
                             (not image_only or self._is_image_path(value))), "")
            if own_path:
                return own_path
        for edge in self._positions().get("__workflow_edges__", []):
            if not isinstance(edge, dict) or edge.get("target") != node_id:
                continue
            source = self._nodes.get(str(edge.get("source") or ""))
            if source:
                path = str(source.payload.get("path") or source.thumbnail or "")
                if (path and os.path.exists(path) and
                        (not image_only or self._is_image_path(path))):
                    return path
        return ""

    def _reference_assets_for_node(self, node, primary: str = ""):
        """Build typed, persisted references for provider prompt manifests."""
        raw = [dict(value) for value in node.payload.get("reference_assets", [])
               if isinstance(value, dict)]
        seen = {str(value.get("path") or "") for value in raw}
        for index, path in enumerate(node.payload.get("references", []) or [], 1):
            path = str(path or "")
            if path and path not in seen:
                raw.append({"path": path, "role": "reference",
                            "label": f"普通参考 {index}"})
                seen.add(path)
        if primary and primary not in seen:
            role = "composition" if node.node_type == "image_node" else "reference"
            label = "当前图片（构图底图）" if role == "composition" else "上游参考"
            for edge in self._positions().get("__workflow_edges__", []):
                if not isinstance(edge, dict) or edge.get("target") != node.node_id:
                    continue
                source = self._nodes.get(str(edge.get("source") or ""))
                if source is None:
                    continue
                source_path = str(source.payload.get("path") or source.thumbnail or "")
                if source_path != primary:
                    continue
                source_role = str(source.payload.get("reference_role") or "reference")
                role = source_role if source_role in DIRECT_REFERENCE_ROLES else "reference"
                label = DIRECT_REFERENCE_ROLES.get(role, "上游参考")
                break
            raw.insert(0, {"path": primary, "role": role, "label": label,
                           "required": role in ("composition", "character", "scene", "element")})
        return normalize_reference_assets(raw)

    def _production_source_records(self):
        return [value for value in self._positions().get("__custom_nodes__", [])
                if isinstance(value, dict) and value.get("type") == "storyboard_node"]

    def _production_skill_records(self):
        """Return the persisted records that readiness gates may inspect."""
        return [value for value in self._positions().get("__custom_nodes__", [])
                if isinstance(value, dict)]

    def _evaluate_production_gate(self, source_id: str, gate: str, *,
                                  require_end_frame=False, shot_ids=None):
        linked_assets = set(self._storyboard_asset_node_ids(str(source_id or "")))
        records = [value for value in self._production_skill_records()
                   if str(value.get("asset_kind") or "") not in
                   {"scene", "character", "element"} or
                   str(value.get("id") or "") in linked_assets]
        report = evaluate_readiness(
            gate, self.current_storyboard(), records,
            shot_ids=shot_ids, require_end_frame=require_end_frame)
        source = self._custom_record(str(source_id or ""))
        if source is not None:
            source["readiness_report"] = report.as_dict()
        return report

    def _run_production_gate(self, source_id: str, gate: str, *,
                             require_end_frame=False, shot_ids=None):
        """Run one technical gate and persist an explainable orchestration trace."""
        source = self._custom_record(str(source_id or ""))
        if source is None:
            return None
        report = self._evaluate_production_gate(
            source_id, gate, require_end_frame=require_end_frame,
            shot_ids=shot_ids)
        decision = plan_next_action(
            str(source.get("pipeline_stage") or ""), report,
            str(source.get("automation_mode") or "checkpoints"))
        append_workflow_event(
            source, decision, status="blocked" if report.blocked else "passed")
        source["orchestrator_decision"] = decision
        if report.blocked:
            source["awaiting_gate"] = "readiness"
            source["auto_run_enabled"] = False
            source["status"] = "就绪检查拦截 · " + report.summary()
        elif source.get("awaiting_gate") == "readiness":
            source.pop("awaiting_gate", None)
        self._save_layout_now(); self._update_production_continue_button()
        return report

    @staticmethod
    def _readiness_message(payload: dict):
        report = payload.get("readiness_report")
        if not isinstance(report, dict) or report.get("ready", True):
            return ""
        issues = [str(value.get("message") or "") for value in
                  report.get("issues", []) if isinstance(value, dict) and
                  value.get("severity") == "block" and value.get("message")]
        if not issues:
            return ""
        suffix = f"；另有 {len(issues) - 3} 项" if len(issues) > 3 else ""
        return "就绪检查未通过：" + "；".join(issues[:3]) + suffix

    def _current_production_source_id(self):
        records = self._production_source_records()
        if not records:
            return ""
        active = [value for value in records
                  if str(value.get("pipeline_stage") or "") not in
                  ("production_ready",)]
        return str((active or records)[-1].get("id") or "")

    def _has_storyboard_planning_task(self, source_id: str) -> bool:
        """Return whether this process still owns a planning task for source."""
        planning_kinds = {
            "storyboard_plan", "storyboard_plan_foundation",
            "storyboard_plan_batch",
        }
        return any(
            str(task.get("node_id") or "") == str(source_id) and
            str(task.get("kind") or "") in planning_kinds
            for task in self._standalone_tasks.values()
        )

    def _update_production_continue_button(self):
        button = getattr(self, "production_continue_btn", None)
        if button is None:
            return
        source_id = self._current_production_source_id()
        record = self._custom_record(source_id) if source_id else None
        rewind = getattr(self, "production_rewind_btn", None)
        if rewind is not None:
            rewind.setEnabled(record is not None)
            rewind.setToolTip(
                "从第 1–7 步中的任一步重新开始；只清理该步及其下游"
                if record is not None else "当前没有可回退的制片项目")
        if record is None:
            button.setText("▶ 开始制片")
            button.setToolTip("先在“＋ 新建”中创建 AI 制片项目，或把 AI 脚本送入制片")
            button.setEnabled(False)
            preview = getattr(self, "combined_preview_btn", None)
            if preview is not None:
                preview.setEnabled(False)
            return
        mode = str(record.get("automation_mode") or "checkpoints")
        stage = str(record.get("pipeline_stage") or "")
        if mode == "manual":
            label, _action, enabled, tip = self._manual_production_control(record)
            button.setText(label)
            button.setToolTip(tip)
            button.setEnabled(enabled)
            preview = getattr(self, "combined_preview_btn", None)
            if preview is not None:
                has_video = any(os.path.exists(str(shot.get("selected_video_asset") or ""))
                                for shot in self.current_storyboard().get("shots", []))
                rendering = (self._preview_render_process is not None and
                             self._preview_render_process.poll() is None)
                preview.setEnabled(has_video and not rendering)
                preview.setText("… 合成预览中" if rendering else "▶ 联合预览")
            return
        labels = {
            "": "⚙ 确认拆镜设置",
            "assets_generated": "✓ 采用当前资产并继续",
            "start_image_candidates_ready": "✓ 采用起始帧并生成结束帧",
            "image_candidates_ready": "✓ 采用候选并生成视频",
            "video_candidates_ready": "请选择当前段定稿视频",
            "video_handoff_blocked": "连续续接已暂停",
            "video_ready": "▶ 完成对白并收尾",
            "video_qc_pending": "… 自动审片中",
            "video_qc_review": (
                "⚠ 查看审片问题" if f"auto-qc:{source_id}" in self._nodes
                else "↻ 恢复审片报告"),
            "production_ready": "✓ 本轮已完成",
            "production_interrupted": "↻ 继续上次生产",
        }
        recoverable_planning = (
            stage == "planning" and
            isinstance(record.get("storyboard_plan_checkpoint"), dict) and
            not self._has_storyboard_planning_task(source_id)
        )
        recoverable_video_qc = (
            stage == "video_qc_pending" and not any(
                str(task.get("source_id") or "") == source_id and
                str(task.get("kind") or "") in {"clip_qc", "sequence_qc"}
                for task in self._standalone_tasks.values()))
        video_resume_label = "↻ 继续已暂停的视频流程"
        video_resume_tip = (
            "后台没有正在运行的审片任务；点击后会从已保存节点恢复剩余视频或整片审片。")
        if recoverable_video_qc:
            video_group = self._latest_production_group(source_id, "video")
            video_generators = [
                self._custom_record(str(value)) or {}
                for value in (video_group or {}).get("group_nodes", [])]
            approved_count = sum(bool(value.get("adopted") and
                                      value.get("handoff_approved"))
                                 for value in video_generators)
            total_count = len(video_generators)
            if total_count and approved_count < total_count:
                video_resume_label = (
                    f"↻ 继续剩余视频（{approved_count}/{total_count} 已通过）")
                video_resume_tip = (
                    f"当前共有 {total_count} 个视频段，{approved_count} 个已由你通过；"
                    "点击后从下一个未完成视频段继续。")
            elif total_count:
                video_resume_label = "▶ 完成审片并进入下一步"
                video_resume_tip = "全部视频段均已通过；点击后执行整片汇总并进入对白或收尾。"
        running = stage in {
            "planning", "assets_generating", "blocking_generating",
            "storyboard_panels_generating", "images_generating",
            "video_generating", "audio_generating",
            "video_qc_pending",
        } and not recoverable_planning and not recoverable_video_qc
        waiting_for_video_choice = stage in {
            "video_candidates_ready", "video_handoff_blocked"}
        button.setText(
            "↻ 继续已保存的拆镜" if recoverable_planning else
            video_resume_label if recoverable_video_qc else
            "… AI 制片中" if running else labels.get(stage, "▶ 继续制片"))
        button.setToolTip(
            "上次拆镜已中断；继续时会从已保存的镜头批次恢复，不会重新生成已完成部分。"
            if recoverable_planning else
            video_resume_tip
            if recoverable_video_qc else self._production_stage_message(record))
        button.setEnabled(
            not running and not waiting_for_video_choice and stage != "production_ready")
        preview = getattr(self, "combined_preview_btn", None)
        if preview is not None:
            has_video = any(os.path.exists(str(shot.get("selected_video_asset") or ""))
                            for shot in self.current_storyboard().get("shots", []))
            rendering = (self._preview_render_process is not None and
                         self._preview_render_process.poll() is None)
            preview.setEnabled(has_video and not rendering)
            preview.setText("… 合成预览中" if rendering else "▶ 联合预览")

    def continue_current_production(self):
        source_id = self._current_production_source_id()
        node = self._nodes.get(source_id)
        if node is None:
            return
        record = self._custom_record(source_id) or {}
        if str(record.get("automation_mode") or "checkpoints") == "manual":
            self.advance_manual_production(source_id)
            return
        self.continue_canvas_production(node, from_async=False)

    def show_production_rewind_menu(self, screen_pos=None, source_id=""):
        source_id = str(source_id or self._current_production_source_id())
        source = self._custom_record(source_id) if source_id else None
        menu = QMenu(self)
        self._style_popup_menu(menu)
        title = menu.addAction("从哪一步重新开始？")
        title.setEnabled(False)
        actions = {}
        for step, (label, description) in PRODUCTION_REWIND_STEPS.items():
            action = menu.addAction(f"↶ 第 {step} 步 · {label}")
            action.setToolTip(description)
            action.setEnabled(source is not None)
            actions[action] = step
        menu.addSeparator()
        undo_action = menu.addAction("撤销上一次阶段回退")
        backup = self._positions().get("__stage_rewind_backup__")
        undo_action.setEnabled(isinstance(backup, dict) and bool(backup))
        if screen_pos is None:
            button = getattr(self, "production_rewind_btn", None)
            screen_pos = (button.mapToGlobal(button.rect().topLeft())
                          if button is not None else self.mapToGlobal(self.rect().center()))
        chosen = menu.exec(screen_pos)
        if chosen in actions:
            self.rewind_production_to_step(actions[chosen], source_id)
        elif chosen == undo_action:
            self.undo_last_production_rewind()

    def _capture_stage_rewind_backup(self, source_id: str, step: int):
        project = self._positions()
        canvas = {key:value for key, value in project.items()
                  if key != "__stage_rewind_backup__"}
        project["__stage_rewind_backup__"] = {
            "source_id":str(source_id), "step":int(step),
            "created_at":datetime.now().astimezone().isoformat(timespec="seconds"),
            "board":json.loads(json.dumps(self.current_storyboard(), ensure_ascii=False)),
            "canvas":json.loads(json.dumps(canvas, ensure_ascii=False)),
        }

    def undo_last_production_rewind(self, show_message=True):
        project_key = self._project_key()
        backup = self._positions().get("__stage_rewind_backup__")
        if not isinstance(backup, dict) or not isinstance(backup.get("board"), dict):
            if show_message:
                QMessageBox.information(self, "撤销阶段回退", "当前没有可撤销的阶段回退。")
            return False
        canvas = json.loads(json.dumps(backup.get("canvas") or {}, ensure_ascii=False))
        board = json.loads(json.dumps(backup["board"], ensure_ascii=False))
        canvas["__storyboard_snapshot__"] = json.loads(
            json.dumps(board, ensure_ascii=False))
        self._stop_canvas_tasks_for_project_switch()
        self._layout_store[project_key] = canvas
        self._storyboard = board
        self._storyboard_override = True
        self._initial_view_ready = False
        self.refresh(); self._recover_production_batches()
        self.projectLoaded.emit(self.current_storyboard())
        self._save_layout_now(); self.view.fit_nodes()
        return True

    def _cancel_tasks_for_stage_rewind(self, source_id: str, affected_ids: set[str],
                                       shot_ids: set[str]):
        source_id = str(source_id)
        task_ids = []
        for task_id, task in self._standalone_tasks.items():
            node_id = str(task.get("node_id") or "")
            group_id = str(task.get("workflow_group_id") or "")
            if (node_id == source_id or node_id in affected_ids or
                    node_id.removeprefix("shot:") in shot_ids or
                    group_id in affected_ids):
                try:
                    handle = task.get("handle")
                    if handle and not handle.is_finished:
                        handle.cancel()
                except Exception:
                    pass
                task_ids.append(task_id)
        for task_id in task_ids:
            self._standalone_tasks.pop(task_id, None)
        for group_id in list(self._serial_video_queues):
            if group_id in affected_ids:
                self._serial_video_queues.pop(group_id, None)
        if self._canvas_storyboard_source == source_id:
            self._canvas_storyboard_queue = []
            self._canvas_character_queue = []
            self._canvas_storyboard_previous = ""
            self._canvas_storyboard_character_refs = []
        self._auto_continue_pending.discard(source_id)

    @staticmethod
    def _clear_shot_video(shot: dict):
        video_path = str(shot.get("selected_video_asset") or "")
        for key in ("selected_video_asset", "video_segment_node_id",
                    "video_segment_offset", "video_segment_duration",
                    "video_review_frames", "video_tail_frame", "clip_qc",
                    "sequence_reviews", "spatial_review"):
            shot.pop(key, None)
        shot["assets"] = [
            value for value in shot.get("assets", [])
            if not (isinstance(value, dict) and value.get("kind") == "video")]
        if str(shot.get("selected_asset") or "") == video_path:
            shot["selected_asset"] = str(shot.get("selected_image_asset") or "")
        if str(shot.get("preview_asset") or "") == video_path:
            shot["preview_asset"] = str(shot.get("selected_image_asset") or "")
        if shot.get("selected_image_asset"):
            shot["asset_type"] = "image"

    @staticmethod
    def _clear_shot_audio(shot: dict):
        shot.pop("dialogue_audio", None)
        shot["assets"] = [
            value for value in shot.get("assets", [])
            if not (isinstance(value, dict) and value.get("kind") == "audio")]

    def rewind_production_to_step(self, step: int, source_id="", confirm=True,
                                  show_message=True):
        """Discard exactly one stage and everything downstream, never media files."""
        try:
            step = int(step)
        except (TypeError, ValueError):
            return False
        if step not in PRODUCTION_REWIND_STEPS:
            return False
        source_id = str(source_id or self._current_production_source_id())
        source = self._custom_record(source_id)
        if source is None:
            if show_message:
                QMessageBox.information(self, "阶段重做", "当前画布没有可回退的制片项目。")
            return False
        label, description = PRODUCTION_REWIND_STEPS[step]
        if confirm:
            answer = QMessageBox.question(
                self, f"从第 {step} 步重新开始",
                f"{description}。\n\n旧文件不会从磁盘删除；清理仅作用于当前工程画布。"
                "\n系统会保留一次撤销机会。继续吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return False

        self._capture_stage_rewind_backup(source_id, step)
        project = self._positions()
        board = self.current_storyboard()
        shots = list(board.get("shots", []))
        shot_ids = {str(shot.get("id") or "") for shot in shots}
        records = [value for value in project.get("__custom_nodes__", [])
                   if isinstance(value, dict)]
        asset_ids = set(self._storyboard_asset_node_ids(source_id))
        asset_view_ids = {str(value.get("id") or "") for value in records
                          if str(value.get("reference_parent_id") or "") in asset_ids}
        remove_kinds = ({"image", "video", "audio"} if step <= 5 else
                        {"video", "audio"} if step == 6 else {"audio"})
        group_records = [value for value in records
                         if value.get("type") == "workflow_group" and
                         str(value.get("source_node_id") or "") == source_id and
                         str(value.get("generator_kind") or "") in remove_kinds]
        remove_ids = {str(value.get("id") or "") for value in group_records}
        for value in group_records:
            remove_ids.update(str(node_id) for node_id in value.get("group_nodes", []))
        remove_ids.update(
            str(value.get("id") or "") for value in records
            if str(value.get("generator_kind") or "") in remove_kinds and
            bool(shot_ids & {str(item) for item in
                 (value.get("shot_ids") or [value.get("shot_id")]) if item}))
        if step == 1:
            remove_ids.update(asset_ids); remove_ids.update(asset_view_ids)
        elif step == 2:
            remove_ids.update(asset_view_ids)
        if step <= 6:
            remove_ids.add(f"auto-qc:{source_id}")

        self._cancel_tasks_for_stage_rewind(
            source_id, remove_ids | asset_ids | asset_view_ids, shot_ids)
        if remove_ids:
            project["__custom_nodes__"] = [
                value for value in records
                if str(value.get("id") or "") not in remove_ids]
            for node_id in remove_ids:
                project.pop(node_id, None)
            project["__workflow_edges__"] = [
                edge for edge in project.get("__workflow_edges__", [])
                if not (isinstance(edge, dict) and
                        (str(edge.get("source") or "") in remove_ids or
                         str(edge.get("target") or "") in remove_ids))]
            self._workflow_failed_nodes.difference_update(remove_ids)
        project["__production_batches__"] = [
            batch for batch in project.get("__production_batches__", [])
            if not (isinstance(batch, dict) and
                    (str(batch.get("source_node_id") or "") == source_id and
                     str(batch.get("kind") or "") in remove_kinds or
                     str(batch.get("group_id") or "") in remove_ids))]

        if step == 2:
            for asset_id in asset_ids:
                asset = self._custom_record(asset_id)
                if asset is None:
                    continue
                asset.update({
                    "path":"", "candidates":[], "asset_version":0,
                    "locked":False, "adopted":False,
                    "status":("角色设定 0/4 · 待生成"
                              if asset.get("asset_kind") == "character" else "待生成 V1"),
                })
                asset.pop("character_reference_set", None)

        for shot in shots:
            self._clear_shot_audio(shot)
            if step <= 6:
                self._clear_shot_video(shot)
            if step <= 5:
                motion_paths = self._motion_board_paths(shot)
                image_path = str(shot.get("selected_image_asset") or "")
                shot["assets"] = [
                    value for value in shot.get("assets", [])
                    if isinstance(value, dict) and
                    str(value.get("subtype") or "") == "motion_storyboard"]
                for key in ("selected_image_asset", "anchor_frame_id",
                            "selected_end_image_asset", "end_anchor_frame_id",
                            "endpoint_pair_required"):
                    shot.pop(key, None)
                if str(shot.get("selected_asset") or "") == image_path:
                    shot["selected_asset"] = ""
                if str(shot.get("preview_asset") or "") == image_path:
                    shot["preview_asset"] = ""
                motion_path = next((value for value in motion_paths
                                    if os.path.exists(value)), "")
                shot["preview_asset"] = motion_path
                shot["selected_asset"] = motion_path
                shot["asset_type"] = "image" if motion_path else ""
            if step <= 4:
                for key in ("final_image_prompt", "final_start_image_prompt",
                            "final_end_image_prompt", "final_video_prompt",
                            "asset_manifest", "continuity_reference",
                            "continuity_source_shot_id", "scene_master_path",
                            "scene_master_id", "space_geometry_contract",
                            "production_ready"):
                    shot.pop(key, None)
            if step <= 3:
                motion_paths = self._motion_board_paths(shot)
                shot["assets"] = [
                    value for value in shot.get("assets", [])
                    if not (isinstance(value, dict) and
                            str(value.get("path") or "") in motion_paths)]
                for key in ("blocking_ready", "blocking", "eyeline",
                            "continuity_note", "motion_keyframes",
                            "motion_frame_count", "motion_hero_frame",
                            "motion_board_path", "draft_panel"):
                    shot.pop(key, None)
                shot["preview_asset"] = ""
                shot["selected_asset"] = ""
                shot["asset_type"] = ""
            for key in ("quality_score", "quality_passed", "director_review",
                        "repair_plan", "repair_target"):
                if step <= 6:
                    shot.pop(key, None)
            shot["status"] = "draft" if step <= 5 else (
                "image_ready" if step == 6 else "video_ready")

        if step == 1:
            for shot_id in shot_ids:
                project.pop(f"shot:{shot_id}", None)
            project["__workflow_edges__"] = [
                edge for edge in project.get("__workflow_edges__", [])
                if not (isinstance(edge, dict) and
                        (str(edge.get("source") or "").removeprefix("shot:") in shot_ids or
                         str(edge.get("target") or "").removeprefix("shot:") in shot_ids))]
            board["shots"] = []
            board.pop("summary", None); board.pop("visual_bible", None)

        source = self._custom_record(source_id)
        if source is None:
            return False
        stage_map = {
            1:"", 2:"shots_ready", 3:"assets_ready",
            4:"storyboard_panels_ready", 5:"prompts_ready",
            6:"image_candidates_ready", 7:"video_ready",
        }
        source["pipeline_stage"] = stage_map[step]
        source["status"] = f"已回到第 {step} 步 · {label}"
        source["auto_run_enabled"] = False
        source.pop("awaiting_gate", None); source.pop("interrupted_kind", None)
        if step <= 6:
            for key in ("automatic_qc", "sequence_qc", "sequence_qc_signature",
                        "quality_risk_accepted", "repair_plan"):
                source.pop(key, None)
        source["approval_required"] = {
            1:"shots", 2:"assets", 3:"storyboard_panels",
            4:"final_prompts", 5:"final_images",
            6:"approved_images", 7:"dialogue_audio",
        }[step]
        rebuild_continuity(board)
        self.storyboardMutated.emit(); self._save_layout_now()
        self.refresh(); self.focus_node(source_id); self._update_production_continue_button()
        if show_message:
            QMessageBox.information(
                self, "阶段已回退",
                f"已回到第 {step} 步：{label}。\n{description}。\n\n"
                "旧媒体文件仍保留在磁盘；如有误操作，可在“↶ 重做”中撤销上一次回退。")
        return True

    def advance_manual_production(self, source_id: str, action_override: str = ""):
        """Run exactly one stage from the dock, instead of merely focusing it."""
        source_id = str(source_id or "")
        record = self._custom_record(source_id)
        node = self._nodes.get(source_id)
        if record is None or node is None:
            return False
        _label, action, enabled, _tip = self._manual_production_control(record)
        if action_override in MANUAL_PRODUCTION_STEPS:
            action = action_override
            enabled = True
        if not enabled or not action:
            return False
        if action == "focus_unlocked_asset":
            target = next((value for value in self._storyboard_asset_node_ids(source_id)
                           if not bool((self._custom_record(value) or {}).get("locked"))), "")
            if target:
                self.focus_node(target)
            return bool(target)
        if action == "focus_video_qc":
            target = f"auto-qc:{source_id}"
            if target not in self._nodes and not self._restore_auto_qc_node(source_id):
                QMessageBox.information(
                    self, "无法恢复审片报告",
                    "审片结果仍处于阻断状态，但报告快照已经缺失。请重新执行自动审片，"
                    "或在制片项目节点选择“接受审片风险并继续”。")
                return False
            self.focus_node(target)
            return True
        if action == "resume":
            kind = str(record.get("interrupted_kind") or "")
            group = self._latest_production_group(source_id, kind) if kind else None
            group_id = str((group or {}).get("id") or "")
            if not group_id or group_id not in self._nodes:
                record["status"] = "找不到可恢复的生成器组，请从对应步骤重新执行"
                self._save_layout_now(); self._update_production_continue_button()
                return False
            record["pipeline_stage"] = {
                "image":"images_generating", "video":"video_generating",
                "audio":"audio_generating",
            }.get(kind, "production_interrupted")
            record["status"] = "正在从未完成处恢复生产"
            record.pop("interrupted_kind", None)
            self._save_layout_now(); self._update_production_continue_button()
            self.execute_workflow_group(self._nodes[group_id], pending_only=True)
            return True
        if action == "prepare_end_frames":
            if not self._auto_adopt_image_candidates(source_id):
                self.refresh()
                return False
            report = self._run_production_gate(source_id, "start_frames")
            if report is not None and report.blocked:
                self.refresh()
                return False
            return self._prepare_and_execute_end_frame_generators(source_id)
        gate = ""
        require_end_frame = False
        if action.startswith("2"):
            gate = "shot_plan"
        elif action.startswith("3"):
            gate = "locked_assets"
        elif action.startswith("4"):
            gate = "blocking"
        elif action.startswith("5"):
            gate = "prompts"
        elif action.startswith("6"):
            gate = "video_anchors"
            require_end_frame = self._production_requires_end_frames(source_id)
        elif action.startswith("7"):
            gate = "videos"
        if gate:
            report = self._run_production_gate(
                source_id, gate, require_end_frame=require_end_frame)
            if report is not None and report.blocked:
                self.refresh()
                return False
        if action.startswith("1"):
            idea = str(record.get("content") or "").strip()
            if not idea:
                self.submit_canvas_storyboard(node, idea, action)
                return False
            before = set(self._standalone_tasks)
            record["pipeline_stage"] = "planning"
            record["status"] = "第 1 步 · AI 正在拆解镜头"
            self._save_layout_now(); self._update_production_continue_button()
            self.submit_canvas_storyboard(node, idea, action)
            if set(self._standalone_tasks) == before:
                record = self._custom_record(source_id)
                if record is not None and record.get("pipeline_stage") == "planning":
                    record["pipeline_stage"] = ""
                    record["status"] = "第 1 步启动失败 · 请检查文本模型配置"
                self._save_layout_now(); self._update_production_continue_button()
                return False
            return True
        if action.startswith("2"):
            record["pipeline_stage"] = "assets_generating"
            record["status"] = "第 2 步 · AI 正在生成角色、场景和道具候选"
            self._save_layout_now(); self._update_production_continue_button()
        self.submit_canvas_storyboard(
            node, str(record.get("content") or ""), action)
        self._update_production_continue_button()
        return True

    def _latest_production_group(self, source_id: str, kind: str):
        return next((value for value in reversed(
            self._positions().get("__custom_nodes__", []))
            if isinstance(value, dict) and value.get("type") == "workflow_group" and
            str(value.get("source_node_id") or "") == str(source_id) and
            str(value.get("generator_kind") or "") == kind and
            not value.get("invalidated")), None)

    def _production_group_shots(self, source_id: str, kind="image"):
        group = self._latest_production_group(source_id, kind)
        shot_ids = []
        for node_id in (group or {}).get("group_nodes", []):
            generator = self._custom_record(str(node_id)) or {}
            shot_ids.extend(str(value) for value in
                            (generator.get("shot_ids") or [generator.get("shot_id")])
                            if value)
        if shot_ids:
            wanted = set(shot_ids)
            return [shot for shot in self.current_storyboard().get("shots", [])
                    if str(shot.get("id") or "") in wanted]
        return list(self.current_storyboard().get("shots", []))

    def _production_requires_end_frames(self, source_id: str):
        return any(endpoint_pair_requested(shot) and
                   bool(shot.get("endpoint_pair_required"))
                   for shot in self._production_group_shots(source_id, "image"))

    def _auto_lock_production_assets(self, source_id: str):
        missing = []
        asset_ids = self._storyboard_asset_node_ids(source_id)
        for asset_id in asset_ids:
            record = self._custom_record(asset_id) or {}
            path = str(record.get("path") or "")
            if not path or not os.path.exists(path):
                missing.append(str(record.get("title") or "资产"))
                continue
            if str(record.get("asset_kind") or "") == "character":
                refs = dict(record.get("character_reference_set") or {})
                if not all(os.path.exists(str(refs.get(role) or ""))
                           for role, _label, _prompt in CHARACTER_REFERENCE_SPECS):
                    missing.append(str(record.get("title") or "角色"))
                    continue
            if str(record.get("asset_kind") or "") == "scene":
                refs = dict(record.get("scene_reference_set") or {})
                if not all(os.path.exists(str(refs.get(role) or ""))
                           for role, _label, _prompt in SCENE_VIEW_SPECS):
                    missing.append(str(record.get("title") or "场景"))
                    continue
            record["locked"] = True
            record["adopted"] = True
            record["status"] = f"V{int(record.get('asset_version') or 1)} 已锁定"
        source = self._custom_record(source_id)
        if source is not None:
            if missing:
                source["status"] = "以下资产尚未生成完整：" + "、".join(missing)
                source["pipeline_stage"] = "assets_generated"
                source["awaiting_gate"] = "assets"
            else:
                source["status"] = "资产已确认 · 正在进入空间调度"
                source["pipeline_stage"] = "assets_ready"
                source.pop("awaiting_gate", None)
        self._save_layout_now()
        return not missing

    def _auto_adopt_image_candidates(self, source_id: str):
        group = self._latest_production_group(source_id, "image")
        if group is None:
            return False
        source_record = self._custom_record(source_id) or {}
        source_stage = str(source_record.get("pipeline_stage") or "")
        has_explicit_end = any(str(
            (self._custom_record(str(value)) or {}).get("frame_role") or "") == "end"
            for value in group.get("group_nodes", []))
        desired_role = (("start" if source_stage == "start_image_candidates_ready" else
                         "end" if source_stage == "image_candidates_ready" else "")
                        if has_explicit_end else "")
        missing = []
        for node_id in group.get("group_nodes", []):
            generator = self._custom_record(str(node_id)) or {}
            frame_role = str(generator.get("frame_role") or "start")
            if desired_role and frame_role != desired_role:
                continue
            selected_key = ("selected_end_image_asset"
                            if frame_role == "end" else "selected_image_asset")
            candidates = [str(value) for value in generator.get("candidates", [])
                          if value and os.path.exists(str(value))]
            current = str(generator.get("path") or "")
            if current and os.path.exists(current):
                candidates.insert(0, current)
            candidates = list(dict.fromkeys(candidates))
            shot_ids = [str(value) for value in
                        (generator.get("shot_ids") or [generator.get("shot_id")]) if value]
            for shot_id in shot_ids:
                shot = self._find_shot(shot_id)
                if shot is None or os.path.exists(str(shot.get(selected_key) or "")):
                    continue
                spatial_results = dict(generator.get("candidate_spatial_qc") or {})
                clean_candidates = [
                    path for path in candidates
                    if not self._path_has_motion_board_lineage(shot, path) and
                    (str((spatial_results.get(path) or {}).get("status") or "") == "pass"
                     if frame_role == "end" else
                     str((spatial_results.get(path) or {}).get("status") or "") != "fail")
                ]
                if not clean_candidates:
                    if frame_role == "end":
                        if shot.get("endpoint_pair_forced"):
                            missing.append(int(shot.get("number") or 0))
                            shot["endpoint_pair_required"] = True
                            shot["endpoint_pair_runtime_mode"] = "first_last_blocked"
                            shot["endpoint_pair_fallback_reason"] = (
                                "复杂动作镜头的结束帧未通过一致性检查，禁止降级为单首帧")
                            continue
                        # Never force an inconsistent Klast into a video. A
                        # single approved K1 is safer than a full-scene morph.
                        shot["endpoint_pair_required"] = False
                        shot["endpoint_pair_runtime_mode"] = "first_frame_fallback"
                        shot["endpoint_pair_fallback_reason"] = (
                            "结束帧未通过首尾一致性检查，已自动改用单首帧")
                        continue
                    missing.append(int(shot.get("number") or 0))
                    continue
                path = clean_candidates[0]
                shot[selected_key] = path
                if frame_role == "end":
                    shot["end_anchor_frame_id"] = path
                    shot["endpoint_pair_qc"] = json.loads(json.dumps(
                        spatial_results.get(path) or {}, ensure_ascii=False))
                    shot["endpoint_pair_required"] = True
                    shot["endpoint_pair_runtime_mode"] = "first_last"
                else:
                    shot["selected_asset"] = path
                    shot["preview_asset"] = path
                    shot["anchor_frame_id"] = path
                shot["asset_type"] = "image"
                shot["status"] = "ready"
        source = self._custom_record(source_id)
        if source is not None:
            if missing:
                source["status"] = ("这些镜头还没有可用图片候选：" +
                                    "、".join(f"{value:02d}" for value in missing))
                source["awaiting_gate"] = "images"
            else:
                source.pop("awaiting_gate", None)
        self.storyboardMutated.emit()
        self._save_layout_now()
        return not missing

    @staticmethod
    def _shot_endpoint_pair_ready(shot: dict):
        start = str(shot.get("selected_image_asset") or "")
        if not start or not os.path.exists(start):
            return False
        if not endpoint_pair_requested(shot) or not shot.get("endpoint_pair_required"):
            return True
        end = str(shot.get("selected_end_image_asset") or "")
        qc = dict(shot.get("endpoint_pair_qc") or {})
        return bool(end and os.path.exists(end) and qc.get("status") == "pass")

    @staticmethod
    def _shot_uses_endpoint_pair(shot: dict):
        """Whether Klast is safe enough to be submitted to a video provider."""
        return bool(
            endpoint_pair_requested(shot) and
            shot.get("endpoint_pair_required") and
            ProductionCanvasTab._shot_endpoint_pair_ready(shot)
        )

    def _prepare_and_execute_end_frame_generators(self, source_id: str):
        """Bind each approved K1 as the sole pixel source, then run only Klast.

        Asset sheets are useful while designing K1, but feeding them again beside
        an approved K1 makes image-edit providers re-compose the room.  Klast is
        a state edit of K1, not another asset synthesis pass.
        """
        group = self._latest_production_group(source_id, "image")
        if group is None:
            return False
        end_ids = []
        missing = []
        for node_id in group.get("group_nodes", []):
            generator = self._custom_record(str(node_id)) or {}
            if str(generator.get("frame_role") or "") != "end":
                continue
            shot = self._find_shot(str(generator.get("shot_id") or ""))
            if shot is not None and not endpoint_pair_requested(shot):
                generator["status"] = "已跳过 · 本镜使用单首帧驱动"
                generator["invalidated"] = True
                continue
            start = str((shot or {}).get("selected_image_asset") or "")
            if shot is None or not start or not os.path.exists(start):
                missing.append(int((shot or {}).get("number") or 0))
                continue
            generator["references"] = [start]
            generator["reference_assets"] = [{
                "path":start, "role":"composition", "required":True,
                "label":"已确认 K1（唯一像素底图）：禁止重构场景，只推进动作",
            }]
            generator["endpoint_source_path"] = start
            generator["content"] = (
                "【结束帧严格编辑合同】这是对输入开始帧的同镜头状态编辑，不是重新生成场景。"
                "输入图是唯一像素与构图底图。保持摄影机机位、画幅、透视、墙地边界、门窗、"
                "洗衣机及全部家具的数量、位置、尺寸、朝向和遮挡关系不变；不得把背景物体移到"
                "前景，不得增删、复制或重新排列任何设备。保持人物身份、服装、道具和光线来源。"
                "只允许发生下文明确规定的人物动作终点与必要的局部状态变化。\n\n" +
                str(generator.get("content") or ""))
            generator["status"] = "K1 已锁定 · 待生成结束帧"
            end_ids.append(str(node_id))
        source = self._custom_record(source_id)
        if missing:
            if source is not None:
                source["pipeline_stage"] = "start_image_candidates_ready"
                source["awaiting_gate"] = "start_images"
                source["status"] = ("以下镜头还没有确认起始帧：" +
                                    "、".join(f"{value:02d}" for value in sorted(set(missing))))
            self._save_layout_now()
            return False
        if not end_ids:
            group["endpoint_phase"] = "complete"
            group["status"] = "单首帧候选已确认 · 未生成多余结束帧"
            if source is not None:
                source["pipeline_stage"] = "image_candidates_ready"
                source["status"] = "起始帧已确认 · 全部镜头采用单首帧驱动"
                source.pop("awaiting_gate", None)
            self._save_layout_now(); self.refresh()
            self._schedule_auto_continue(source_id, from_async=False)
            return True
        group["endpoint_phase"] = "end"
        group["status"] = f"结束帧生成中 · 0/{len(end_ids)}"
        if source is not None:
            source["pipeline_stage"] = "images_generating"
            source["status"] = "已锁定起始帧 · 正在生成同空间结束帧"
            source.pop("awaiting_gate", None)
        self._save_layout_now(); self.refresh()
        launched = 0
        for node_id in end_ids:
            node = self._nodes.get(node_id)
            record = self._custom_record(node_id) or {}
            if node is None or os.path.exists(str(record.get("path") or "")):
                continue
            self.submit_standalone_generation(
                node, str(record.get("content") or ""), "图生图")
            launched += 1
        return bool(launched or all(
            os.path.exists(str((self._custom_record(value) or {}).get("path") or ""))
            for value in end_ids))

    def _schedule_auto_continue(self, source_id: str, from_async=True):
        source_id = str(source_id or "")
        record = self._custom_record(source_id)
        if (not source_id or record is None or
                str(record.get("automation_mode") or "checkpoints") == "manual" or
                not record.get("auto_run_enabled") or
                source_id in self._auto_continue_pending):
            return
        self._auto_continue_pending.add(source_id)

        def run():
            self._auto_continue_pending.discard(source_id)
            node = self._nodes.get(source_id)
            if node is not None:
                self.continue_canvas_production(node, from_async=from_async)

        QTimer.singleShot(0, run)

    def continue_canvas_production(self, node, from_async=False,
                                   planning_confirmed=False):
        """Advance the production state machine and expose only aesthetic gates."""
        source_id = str(node.node_id if hasattr(node, "node_id") else node)
        source = self._custom_record(source_id)
        if source is None:
            return
        mode = str(source.get("automation_mode") or "checkpoints")
        if mode == "manual":
            self.focus_node(source_id)
            return
        source["automation_mode"] = mode
        source["auto_run_enabled"] = True
        if not from_async:
            source.pop("awaiting_gate", None)
        stage = str(source.get("pipeline_stage") or "")

        if (stage == "planning" and
                isinstance(source.get("storyboard_plan_checkpoint"), dict) and
                not self._has_storyboard_planning_task(source_id)):
            idea = str(source.get("content") or "").strip()
            before = set(self._standalone_tasks)
            self.submit_canvas_storyboard(node, idea, "1 · 拆解镜头")
            if set(self._standalone_tasks) == before:
                source = self._custom_record(source_id)
                if source is not None:
                    source["pipeline_stage"] = ""
                    source["status"] = "拆镜恢复提交失败 · 已保存进度仍保留，请检查文本模型配置后重试"
                    source["auto_run_enabled"] = False
                self._save_layout_now()
                self._update_production_continue_button()
            return
        if stage == "video_qc_pending" and not any(
                str(task.get("source_id") or "") == source_id and
                str(task.get("kind") or "") in {"clip_qc", "sequence_qc"}
                for task in self._standalone_tasks.values()):
            group = self._latest_production_group(source_id, "video")
            if group is not None:
                source["status"] = "正在恢复已暂停的视频生成队列"
                self._save_layout_now(); self._update_production_continue_button()
                self._submit_next_serial_video(str(group.get("id") or ""))
            elif not self._maybe_start_sequence_qc(source_id):
                source["pipeline_stage"] = "video_ready"
                source["status"] = "视频已人工通过 · 自动审片无活动任务，继续收尾"
                self._save_layout_now()
                self._schedule_auto_continue(source_id, from_async=False)
            return
        if stage in {"planning", "assets_generating", "blocking_generating",
                     "storyboard_panels_generating", "images_generating",
                     "video_generating", "video_qc_pending", "audio_generating"}:
            self._update_production_continue_button()
            return
        if stage == "video_qc_review":
            target = f"auto-qc:{source_id}"
            if target not in self._nodes:
                self._restore_auto_qc_node(source_id)
            if target in self._nodes:
                self.focus_node(target)
            else:
                QMessageBox.information(
                    self, "自动审片待确认",
                    "视频质量或相邻镜头连续性没有通过，但审片报告无法恢复。请重新审片，"
                    "或在制片项目节点右键选择“接受审片风险并继续”。")
            return
        if stage == "production_interrupted":
            kind = str(source.get("interrupted_kind") or "")
            group = self._latest_production_group(source_id, kind) if kind else None
            if group is not None and str(group.get("id") or "") in self._nodes:
                source["pipeline_stage"] = {
                    "image":"images_generating", "video":"video_generating",
                    "audio":"audio_generating",
                }.get(kind, "production_interrupted")
                source["status"] = "正在从未完成处恢复生产"
                source.pop("interrupted_kind", None)
                self._save_layout_now(); self._update_production_continue_button()
                self.execute_workflow_group(
                    self._nodes[str(group.get("id") or "")], pending_only=True)
            return
        if not stage:
            if not planning_confirmed:
                source["auto_run_enabled"] = False
                source["status"] = "等待确认拆镜模型、自动拆镜（可手动覆盖）、画风、画幅与制片方式"
                self._save_layout_now(); self.refresh(); self.focus_node(source_id)
                self._update_production_continue_button()
                return
            idea = str(source.get("content") or "").strip()
            if not idea:
                source["auto_run_enabled"] = False
                QMessageBox.information(self, "开始制片", "先在制片项目节点里写一句故事想法。")
                self._update_production_continue_button()
                return
            source["pipeline_stage"] = "planning"
            source["status"] = "AI 正在理解创意并拆解镜头"
            self._save_layout_now(); self._update_production_continue_button()
            fresh = self._nodes.get(source_id)
            if fresh is not None:
                before = set(self._standalone_tasks)
                self.submit_canvas_storyboard(
                    fresh, idea, "1 · 拆解镜头")
                if set(self._standalone_tasks) == before:
                    source = self._custom_record(source_id)
                    if source is not None:
                        source["pipeline_stage"] = ""
                        source["status"] = "制片启动失败 · 请检查文本模型配置"
                        source["auto_run_enabled"] = False
                    self._save_layout_now(); self._update_production_continue_button()
            return
        if stage == "shots_ready":
            report = self._run_production_gate(source_id, "shot_plan")
            if report is not None and report.blocked:
                self.refresh()
                return
            source["pipeline_stage"] = "assets_generating"
            source["status"] = "AI 正在生成角色、场景和关键道具候选"
            self._save_layout_now(); self._update_production_continue_button()
            fresh = self._nodes.get(source_id)
            if fresh is not None:
                self.prepare_canvas_storyboard_assets(fresh)
                self._schedule_auto_continue(source_id, from_async=True)
            return
        if stage in ("assets_generated", "assets_changed"):
            if mode == "checkpoints" and from_async:
                source["awaiting_gate"] = "assets"
                source["status"] = ("资产候选已生成 · 可换图；逐项锁定后自动继续，"
                                    "也可在程序坞采用当前候选")
                self._save_layout_now(); self._update_production_continue_button()
                return
            if not self._auto_lock_production_assets(source_id):
                self.refresh()
                return
            stage = "assets_ready"
        if stage == "assets_ready":
            report = self._run_production_gate(source_id, "locked_assets")
            if report is not None and report.blocked:
                self.refresh()
                return
            fresh = self._nodes.get(source_id)
            if fresh is not None:
                self.prepare_canvas_blocking_storyboards(fresh)
            return
        if stage == "storyboard_panels_ready":
            report = self._run_production_gate(source_id, "blocking")
            if report is not None and report.blocked:
                self.refresh()
                return
            fresh = self._nodes.get(source_id)
            if fresh is not None:
                self.compile_canvas_storyboard_prompts(fresh)
            source = self._custom_record(source_id) or source
            stage = str(source.get("pipeline_stage") or "")
        if stage == "prompts_ready":
            report = self._run_production_gate(source_id, "prompts")
            if report is not None and report.blocked:
                self.refresh()
                return
            fresh = self._nodes.get(source_id)
            if fresh is not None:
                self.create_canvas_generator_group(fresh, "image")
            self._schedule_auto_continue(source_id, from_async=True)
            return
        if stage == "generators_ready":
            report = self._run_production_gate(source_id, "prompts")
            if report is not None and report.blocked:
                self.refresh()
                return
            group = self._latest_production_group(source_id, "image")
            if group is None:
                source["status"] = "未找到图片生成器组，请重新继续"
                self._save_layout_now(); self._update_production_continue_button()
                return
            source["pipeline_stage"] = "images_generating"
            source["status"] = "定稿图片候选生成中"
            self._save_layout_now(); self._update_production_continue_button()
            self._execute_workflow_group_by_id(str(group.get("id") or ""))
            return
        if stage == "start_image_candidates_ready":
            if mode == "checkpoints" and from_async:
                source["awaiting_gate"] = "start_images"
                source["status"] = ("起始帧候选已生成 · 逐镜选择 K1；"
                                    "最后一镜选完后自动生成结束帧")
                self._save_layout_now(); self._update_production_continue_button()
                return
            if not self._auto_adopt_image_candidates(source_id):
                self.refresh()
                return
            report = self._run_production_gate(source_id, "start_frames")
            if report is not None and report.blocked:
                self.refresh()
                return
            self._prepare_and_execute_end_frame_generators(source_id)
            return
        if stage == "image_candidates_ready":
            if mode == "checkpoints" and from_async:
                source["awaiting_gate"] = "images"
                source["status"] = ("结束帧候选已生成 · 选中满意 Klast 并设为定稿；"
                                    "首尾帧齐全后自动生成视频")
                self._save_layout_now(); self._update_production_continue_button()
                return
            if not self._auto_adopt_image_candidates(source_id):
                self.refresh()
                return
            require_end_frame = self._production_requires_end_frames(source_id)
            report = self._run_production_gate(
                source_id, "video_anchors", require_end_frame=require_end_frame)
            if report is not None and report.blocked:
                self.refresh()
                return
            fresh = self._nodes.get(source_id)
            if fresh is not None:
                self.create_and_execute_video_group(fresh)
            return
        if stage == "video_ready":
            report = self._run_production_gate(source_id, "videos")
            if report is not None and report.blocked:
                self.refresh()
                return
            dialogue = any(str(shot.get("dialogue") or "").strip()
                           for shot in self.current_storyboard().get("shots", []))
            providers = get_ai_manager().registry.by_capability("text_to_speech")
            if not dialogue or not providers:
                source["pipeline_stage"] = "production_ready"
                source["status"] = ("视频生产完成 · 无对白任务" if not dialogue else
                                    "视频生产完成 · 配音模型未配置，可稍后补做")
                source["auto_run_enabled"] = False
                self._save_layout_now(); self.refresh()
                return
            group = self._latest_production_group(source_id, "audio")
            if group is None:
                fresh = self._nodes.get(source_id)
                if fresh is not None:
                    self.create_dialogue_audio_group(fresh)
                group = self._latest_production_group(source_id, "audio")
            if group is not None:
                source = self._custom_record(source_id) or source
                source["pipeline_stage"] = "audio_generating"
                source["status"] = "对白音频生成中"
                self._save_layout_now(); self._update_production_continue_button()
                self._execute_workflow_group_by_id(str(group.get("id") or ""))
            return
        self._update_production_continue_button()

    @staticmethod
    def _storyboard_llm_params(model: str, temperature: float) -> dict:
        """Use the original storyboard model parameters without output limits."""
        return {
            "model": str(model),
            "temperature": float(temperature),
        }

    def _submit_storyboard_planning_task(self, source_id: str, provider_name: str,
                                         model: str, temperature: float,
                                         messages: list[dict], kind: str,
                                         purpose: str, **task_fields):
        manager = get_ai_manager()
        provider = next((item for item in manager.registry.by_capability("chat")
                         if item.name == provider_name), None)
        if provider is None:
            raise RuntimeError(f"拆镜模型提供方“{provider_name}”当前不可用")
        request = TaskRequest(
            operation="chat",
            inputs={"messages": messages},
            params=self._storyboard_llm_params(model, temperature),
            metadata={
                "canvas_node_id": source_id,
                "purpose": purpose,
                **{key: task_fields[key] for key in (
                    "batch_start", "batch_end", "planning_fingerprint",
                    "contract_repair_attempt")
                   if key in task_fields},
            },
            use_cache=False,
        )
        handle = manager.submit(provider.name, request)
        self._standalone_tasks[handle.id] = {
            "handle": handle,
            "node_id": source_id,
            "provider": provider.name,
            "kind": kind,
            "planning_model": model,
            "planning_temperature": temperature,
            **task_fields,
        }
        return handle

    @staticmethod
    def _storyboard_response_excerpt(data, limit=6000) -> str:
        if isinstance(data, (dict, list)):
            try:
                value = json.dumps(data, ensure_ascii=False)
            except (TypeError, ValueError):
                value = str(data or "")
        else:
            value = str(data or "")
        value = value.strip()
        return value[:limit] + ("…" if len(value) > limit else "")

    def _repair_storyboard_contract(self, task: dict, data, error: Exception) -> bool:
        """Persist diagnostics and submit at most one bounded schema repair."""
        source_id = str(task.get("node_id") or "")
        record = self._custom_record(source_id)
        if record is None:
            return False
        kind = str(task.get("kind") or "")
        attempt = int(task.get("contract_repair_attempt") or 0)
        excerpt = self._storyboard_response_excerpt(data)
        diagnostic = {
            "kind": kind,
            "error": str(error or "未知合同错误"),
            "response_excerpt": excerpt,
            "repair_attempt": attempt,
            "task_id": str(getattr(task.get("handle"), "id", "") or ""),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        record["storyboard_plan_diagnostic"] = diagnostic
        if attempt >= 1:
            self._save_layout_now()
            return False
        checkpoint = record.get("storyboard_plan_checkpoint")
        if not isinstance(checkpoint, dict):
            self._save_layout_now()
            return False
        idea = str(record.get("content") or "").strip()
        provider_name = str(checkpoint.get("provider") or task.get("provider") or "openai")
        model = str(checkpoint.get("model") or task.get("planning_model") or "gpt-5.5")
        style = str(checkpoint.get("style") or record.get("style") or "电影写实")
        fingerprint = str(checkpoint.get("fingerprint") or
                          task.get("planning_fingerprint") or "")
        if kind == "storyboard_plan_foundation":
            messages = foundation_repair_messages(
                idea, excerpt, str(error), int(checkpoint.get("shot_count", 0)), style)
            purpose = "canvas_storyboard_foundation_repair"
            fields = {"planning_fingerprint": fingerprint}
            label = "基础合同"
        elif kind == "storyboard_plan_batch" and checkpoint.get("foundation"):
            start = int(task.get("batch_start") or 1)
            end = int(task.get("batch_end") or start)
            messages = shot_batch_repair_messages(
                idea, checkpoint["foundation"], excerpt, str(error), start, end, style)
            purpose = "canvas_storyboard_shot_batch_repair"
            fields = {
                "planning_fingerprint": fingerprint,
                "batch_start": start, "batch_end": end,
            }
            label = f"镜头 {start}-{end} 合同"
        else:
            self._save_layout_now()
            return False
        record["pipeline_stage"] = "planning"
        record["auto_run_enabled"] = False
        record["status"] = f"第 1 步 · {label}不完整 · 正在自动修复结构（仅 1 次）"
        self._save_layout_now()
        try:
            self._submit_storyboard_planning_task(
                source_id, provider_name, model, 0.1, messages, kind, purpose,
                contract_repair_attempt=1, **fields)
            return True
        except Exception as submit_error:
            diagnostic["repair_submit_error"] = str(submit_error)
            self._save_layout_now()
            return False

    def _start_resumable_storyboard_plan(self, node, idea: str, provider,
                                          model: str, temperature: float,
                                          count: int, style: str):
        source_id = str(node.node_id)
        record = self._custom_record(source_id)
        if record is None:
            raise RuntimeError("故事板节点不存在")
        fingerprint = planning_fingerprint(
            idea, count, style, provider.name, model, temperature)
        checkpoint = record.get("storyboard_plan_checkpoint")
        if not storyboard_checkpoint_matches(checkpoint, fingerprint):
            checkpoint = new_planning_checkpoint(
                fingerprint=fingerprint,
                shot_count=count,
                style=style,
                provider=provider.name,
                model=model,
                temperature=temperature,
            )
            record["storyboard_plan_checkpoint"] = checkpoint
        record.update({
            "planning_provider": provider.name,
            "planning_model": model,
            "planning_temperature": temperature,
            "pipeline_stage": "planning",
            "auto_run_enabled": False,
        })
        record.pop("generation_blocked", None)
        record.pop("blocked_input", None)
        record.pop("last_failure_code", None)
        record.pop("last_request_id", None)
        if checkpoint.get("foundation"):
            self._resume_canvas_storyboard_plan(source_id)
            return
        total_calls = (None if count <= 0 else 1 + len(shot_batch_ranges(
            count, int(checkpoint.get("batch_size") or 2))))
        record["status"] = (
            "第 1 步 · 正在分析定稿并自动决定镜头数"
            if total_calls is None else
            f"第 1 步 · 正在建立项目基础合同（1/{total_calls}）")
        node.badge = "AI 拆镜 · 基础合同"
        node.update()
        self._save_layout_now()
        self._submit_storyboard_planning_task(
            source_id, provider.name, model, temperature,
            storyboard_foundation_messages(idea, count, style, temperature),
            "storyboard_plan_foundation", "canvas_storyboard_foundation",
            planning_fingerprint=fingerprint,
        )

    def _resume_canvas_storyboard_plan(self, source_id: str):
        record = self._custom_record(str(source_id))
        if record is None:
            raise RuntimeError("故事板节点不存在")
        checkpoint = record.get("storyboard_plan_checkpoint")
        if not isinstance(checkpoint, dict) or not checkpoint.get("foundation"):
            raise RuntimeError("项目基础合同尚未完成")
        missing = next_storyboard_batch(checkpoint)
        if missing is None:
            merged = merge_storyboard_checkpoint(checkpoint)
            self._apply_canvas_storyboard_plan(
                str(source_id), json.dumps(merged, ensure_ascii=False))
            fresh = self._custom_record(str(source_id))
            if fresh is not None:
                fresh.pop("storyboard_plan_checkpoint", None)
                fresh["planning_protocol_version"] = int(
                    checkpoint.get("version") or 0)
            self._save_layout_now()
            return
        start, end = missing
        idea = str(record.get("content") or "").strip()
        provider_name = str(checkpoint.get("provider") or
                            record.get("planning_provider") or "openai")
        model = str(checkpoint.get("model") or
                    record.get("planning_model") or "gpt-5.5")
        temperature = float(checkpoint.get("temperature", 0.5))
        style = str(checkpoint.get("style") or record.get("style") or "电影写实")
        completed, total = storyboard_checkpoint_progress(checkpoint)
        record["status"] = (
            f"第 1 步 · 已保存 {completed}/{total} 镜 · "
            f"正在细化镜头 {start}-{end}")
        node = self._nodes.get(str(source_id))
        if node is not None:
            node.badge = f"AI 拆镜 · {start}-{end}/{total}"
            node.update()
        self._save_layout_now()
        self._submit_storyboard_planning_task(
            str(source_id), provider_name, model, temperature,
            shot_batch_messages(idea, checkpoint["foundation"],
                                start, end, style),
            "storyboard_plan_batch", "canvas_storyboard_shot_batch",
            planning_fingerprint=str(checkpoint.get("fingerprint") or ""),
            batch_start=start, batch_end=end,
        )

    def _accept_storyboard_foundation(self, task: dict, data):
        source_id = str(task.get("node_id") or "")
        record = self._custom_record(source_id)
        if record is None:
            raise RuntimeError("故事板节点不存在")
        checkpoint = record.get("storyboard_plan_checkpoint")
        if (not isinstance(checkpoint, dict) or
                str(checkpoint.get("fingerprint") or "") !=
                str(task.get("planning_fingerprint") or "")):
            raise RuntimeError("拆镜参数已改变，已忽略过期的基础合同")
        value = extract_json(str(data or ""))
        requested_count = int(checkpoint.get("shot_count", 0))
        checkpoint["foundation"] = normalize_storyboard_foundation(
            value, requested_count)
        resolved_count = len(checkpoint["foundation"].get("shot_outline") or [])
        if requested_count <= 0:
            checkpoint["shot_count"] = resolved_count
            record["shot_count_resolved"] = resolved_count
            record["status"] = (
                f"第 1 步 · 已根据定稿自动确定 {resolved_count} 镜 · 准备逐批细化")
        checkpoint["batches"] = {}
        record.pop("storyboard_plan_diagnostic", None)
        if requested_count > 0:
            record["status"] = "第 1 步 · 基础合同已保存 · 准备逐批细化镜头"
        self._save_layout_now()
        self._resume_canvas_storyboard_plan(source_id)

    def _accept_storyboard_batch(self, task: dict, data):
        source_id = str(task.get("node_id") or "")
        record = self._custom_record(source_id)
        if record is None:
            raise RuntimeError("故事板节点不存在")
        checkpoint = record.get("storyboard_plan_checkpoint")
        if (not isinstance(checkpoint, dict) or
                str(checkpoint.get("fingerprint") or "") !=
                str(task.get("planning_fingerprint") or "")):
            raise RuntimeError("拆镜参数已改变，已忽略过期的镜头批次")
        start = int(task.get("batch_start") or 1)
        end = int(task.get("batch_end") or start)
        value = extract_json(str(data or ""))
        rows = normalize_shot_batch(
            value, checkpoint["foundation"], start, end)
        checkpoint.setdefault("batches", {})[
            storyboard_batch_key(start, end)] = rows
        record.pop("storyboard_plan_diagnostic", None)
        completed, total = storyboard_checkpoint_progress(checkpoint)
        record["status"] = f"第 1 步 · 已保存 {completed}/{total} 镜"
        self._save_layout_now()
        self._resume_canvas_storyboard_plan(source_id)

    def submit_canvas_storyboard(self, node, idea: str, action="1 · 拆解镜头"):
        # Old projects may still persist the former automatic action.  Treat it
        # as stage 1 only: production now always stops for human approval.
        if action.startswith("0"):
            action = "1 · 拆解镜头"
        if action.startswith("2"):
            self.prepare_canvas_storyboard_assets(node); return
        if action.startswith("3"):
            self.prepare_canvas_blocking_storyboards(node); return
        if action.startswith("4"):
            self.compile_canvas_storyboard_prompts(node); return
        if action.startswith("5"):
            self.create_and_execute_image_group(node); return
        if action.startswith("6"):
            source = self._custom_record(str(node.node_id)) or {}
            if str(source.get("pipeline_stage") or "") == "start_image_candidates_ready":
                if self._auto_adopt_image_candidates(str(node.node_id)):
                    self._prepare_and_execute_end_frame_generators(str(node.node_id))
                return
            self.create_and_execute_video_group(node); return
        if action.startswith("7"):
            self.create_and_execute_audio_group(node); return
        idea = idea.strip()
        if not idea:
            QMessageBox.information(self, "AI 故事板", "先在节点下方写一句故事想法。")
            return
        manager = get_ai_manager()
        providers = manager.registry.by_capability("chat")
        settings = self._custom_record(str(node.node_id)) or node.payload
        planned_provider = str(settings.get("planning_provider") or "")
        provider = next((item for item in providers
                         if item.name == planned_provider), None)
        if not planned_provider:
            provider = next((item for item in providers if item.name == "openai"),
                            providers[0] if providers else None)
        if provider is None:
            QMessageBox.warning(
                self, "拆镜模型不可用",
                (f"项目锁定的拆镜模型提供方“{planned_provider}”当前不可用。"
                 if planned_provider else "请先在设置中配置文本模型。") +
                "\n系统不会擅自切换到其他模型，请重新选择后再拆镜。")
            return
        model = str(settings.get("planning_model") or "")
        if not model:
            try:
                from api_config import get as api_get
                model = api_get("llm").default_model or "gpt-5.5"
            except Exception:
                model = "gpt-5.5"
        try:
            planning_temperature = max(
                0.0, min(1.0, float(settings.get("planning_temperature") or 0.5)))
        except (TypeError, ValueError):
            planning_temperature = 0.5
        raw_count = settings.get("shot_count")
        count = int(raw_count) if raw_count is not None else 0
        style = str(node.payload.get("style") or "电影写实")
        try:
            self._start_resumable_storyboard_plan(
                node, idea, provider, model, planning_temperature, count, style)
        except Exception as error:
            QMessageBox.warning(self, "AI 故事板提交失败", str(error))
        return

    def _apply_canvas_storyboard_plan(self, source_node_id: str, data):
        plan = extract_json(str(data or ""))
        raw_shots = plan.get("shots") or []
        if not raw_shots:
            raise ValueError("GPT 没有返回镜头")
        board = self.current_storyboard()
        board["title"] = str(plan.get("title") or board.get("title") or "AI 故事板")
        board["summary"] = str(plan.get("summary") or "")
        bible = board.setdefault("visual_bible", {})
        if not isinstance(bible, dict):
            bible = {}; board["visual_bible"] = bible
        bible["ai_storyboard"] = str(plan.get("visual_bible") or "")
        scene_specs, scene_aliases = consolidate_scene_specs(plan.get("scenes") or [])
        plan["scenes"] = scene_specs
        import uuid
        shots = []
        source = self._nodes.get(source_node_id)
        source_pos = source.pos() if source else QPointF(0, 0)
        edges = self._positions().setdefault("__workflow_edges__", [])
        old_character_ids = {str(edge.get("target") or "") for edge in edges
                             if isinstance(edge, dict) and
                             edge.get("source") == source_node_id and
                             edge.get("type") in ("character", "scene", "element")}
        old_reference_ids = {str(value.get("id") or "") for value in
                             self._positions().get("__custom_nodes__", [])
                             if isinstance(value, dict) and
                             str(value.get("reference_parent_id") or "") in old_character_ids}
        if old_character_ids:
            self._positions()["__custom_nodes__"] = [value for value in
                self._positions().get("__custom_nodes__", []) if not (
                    isinstance(value, dict) and value.get("id") in
                    (old_character_ids | old_reference_ids))]
            for old_id in old_character_ids | old_reference_ids:
                self._positions().pop(old_id, None)
        edges[:] = [edge for edge in edges if not (
            isinstance(edge, dict) and (
                edge.get("source") == source_node_id or
                edge.get("source") in old_character_ids or
                edge.get("target") in old_reference_ids))]
        custom_values = self._positions().setdefault("__custom_nodes__", [])
        character_ids = []
        asset_specs = (("character", "角色", "characters"),
                       ("scene", "场景", "scenes"),
                       ("element", "道具", "elements"))
        asset_row = 0
        for asset_kind, kind_label, plan_key in asset_specs:
            for asset_index, asset in enumerate((plan.get(plan_key) or [])[:8]):
                asset_id = f"custom:{uuid.uuid4().hex[:12]}"
                character_ids.append(asset_id)
                asset_name = str(asset.get("name") or asset_index + 1)
                identity_description = str(asset.get("description") or "")
                content = str(asset.get("image_prompt") or identity_description)
                scene_proxy = (normalize_scene_proxy(asset)
                               if asset_kind == "scene" else {})
                custom_values.append({
                    "id":asset_id, "type":"image_node",
                    "title":(f"{asset_name} · 角色立绘" if asset_kind == "character"
                             else f"{kind_label}资产 · {asset_name}"),
                    "content":content, "identity_description":identity_description,
                    "path":"", "ratio":"2:3" if asset_kind == "character" else "16:9",
                    "reference_role":"character" if asset_kind == "character" else asset_kind,
                    "asset_role":f"{asset_kind}_reference",
                    "asset_kind":asset_kind, "asset_name":str(asset.get("name") or ""),
                    "location_id":str(asset.get("location_id") or ""),
                    "scene_states":json.loads(json.dumps(
                        asset.get("scene_states") or asset.get("states") or [],
                        ensure_ascii=False)),
                    "scene_proxy":scene_proxy,
                    "scene_proxy_signature":scene_proxy_signature(scene_proxy)
                    if scene_proxy else "",
                    "scene_reference_set":{},
                    "asset_version":0, "locked":False, "adopted":False,
                    "candidates":[],
                    "status":("角色设定 0/4 · 待生成" if asset_kind == "character" else
                              "场景视图 0/5 · 待生成" if asset_kind == "scene" else
                              "待生成 V1"),
                })
                self._positions()[asset_id] = [round(source_pos.x() + 430, 2),
                                               round(source_pos.y() + asset_row * 330, 2)]
                edges.append({"source":source_node_id, "target":asset_id, "type":asset_kind})
                asset_row += 3 if asset_kind == "character" else 1
        for index, value in enumerate(raw_shots):
            shot_id = f"shot-{uuid.uuid4().hex[:10]}"
            visual = str(value.get("visual") or "")
            action = str(value.get("action_line") or "")
            transition = str(value.get("transition") or "")
            character_names = value.get("character_names") or []
            if isinstance(character_names, str):
                character_names = [character_names]
            element_names = value.get("element_names") or []
            if isinstance(element_names, str):
                element_names = [element_names]
            original_scene_name = str(value.get("scene_name") or value.get("scene") or "")
            scene_binding = scene_aliases.get(original_scene_name, {})
            shot = {
                "id":shot_id, "number":index + 1,
                "duration":parse_duration_seconds(value.get("duration"), 5),
                "shot_size":str(value.get("shot_size") or "中景"),
                "scene_name":str(scene_binding.get("master_name") or original_scene_name),
                "scene_state":str(value.get("scene_state") or
                                  scene_binding.get("state") or "默认状态"),
                "scene_view_id":str(value.get("scene_view_id") or ""),
                "editable_bbox_xy":value.get("editable_bbox_xy") or
                                   [0.15, 0.12, 0.7, 0.78],
                "character_names":[str(item) for item in character_names if str(item)],
                "element_names":[str(item) for item in element_names if str(item)],
                "visual":visual, "action_line":action,
                "camera_slot":str(value.get("camera") or "待确认机位"),
                "spatial_layout":str(value.get("spatial_layout") or visual),
                "character_positions":[item for item in
                    (value.get("character_positions") or []) if isinstance(item, dict)],
                "camera_position":str(value.get("camera_position") or
                                      value.get("camera") or "待明确摄影机位置"),
                "camera_movement":str(value.get("camera_movement") or
                                      value.get("camera") or "固定机位"),
                "axis_rule":str(value.get("axis_rule") or "保持既有动作轴，不越轴"),
                "ground_plane":str(value.get("ground_plane") or "地面结构待空间调度确认"),
                "ground_lines":[item for item in
                    (value.get("ground_lines") or []) if isinstance(item, dict)],
                "horizon_y":value.get("horizon_y", 0.4),
                "vanishing_point_xy":value.get("vanishing_point_xy") or [0.5, 0.4],
                "foreground":str(value.get("foreground") or "无明确前景遮挡"),
                "midground":str(value.get("midground") or visual),
                "background":str(value.get("background") or "场景空间延伸"),
                "frame_start":str(value.get("frame_start") or visual),
                "frame_end":str(value.get("frame_end") or visual),
                "video_segment":str(value.get("video_segment") or ""),
                "segment_break_after":bool(value.get("segment_break_after", False)),
                "segment_reason":str(value.get("segment_reason") or ""),
                "transition":transition, "dialogue":str(value.get("dialogue") or ""),
                "image_prompt":str(value.get("image_prompt") or visual),
                "draft_panel":"", "motion_panel_paths":[],
                "draft_source":"ai", "assets":[],
                "story_function":str(value.get("story_function") or ""),
                "visual_thesis":str(value.get("visual_thesis") or ""),
                "action_start":str(value.get("action_start") or ""),
                "primary_action":str(value.get("primary_action") or ""),
                "action_end":str(value.get("action_end") or ""),
                "dominant_camera_move":str(value.get("dominant_camera_move") or ""),
                "continuity_invariants":value.get("continuity_invariants") or [],
                "keyframe_strategy":str(value.get("keyframe_strategy") or ""),
                "generation_risk":str(value.get("generation_risk") or ""),
            }
            normalize_director_contract(shot)
            shots.append(shot)
            shot_node_id = f"shot:{shot_id}"
            self._positions()[shot_node_id] = [
                round(source_pos.x() + 430 + index * 410, 2),
                round(source_pos.y() + asset_row * 330 + 180, 2)]
            edges.append({"source":source_node_id, "target":shot_node_id,
                          "type":"storyboard"})
        board["shots"] = shots
        for shot in shots:
            self._normalize_motion_keyframes(shot)
        rebuild_continuity(board); self.storyboardMutated.emit()
        self._canvas_storyboard_queue = list(range(len(shots)))
        self._canvas_storyboard_previous = ""
        self._canvas_storyboard_source = source_node_id
        # 阶段 1 只创建资产节点；阶段 2 再按角色四件套展开真实生成队列。
        self._canvas_character_queue = []
        self._canvas_storyboard_character_refs = []
        record = self._custom_record(source_node_id)
        if record is not None:
            record["status"] = (f"阶段 1/6 · 已拆解 {len(shots)} 个镜头 · "
                                "请确认后执行阶段 2")
            record["pipeline_stage"] = "shots_ready"
            record["approval_required"] = "assets"
        self._save_layout_now(); self.refresh()
        self._schedule_auto_continue(source_node_id, from_async=True)

    @staticmethod
    def _character_identity_prompt(record: dict):
        """Strip legacy layout words so a role-specific panel stays role-specific."""
        prompt = str(record.get("identity_description") or record.get("content") or "").strip()
        for legacy in (
                "白底角色三视图提示词", "角色三视图提示词", "白底角色三视图",
                "角色三视图", "人物三视图", "三视图",
                "包含正面、侧面、背面", "包含正面、侧面和背面",
                "同一人物正面、四分之三侧面和背面"):
            prompt = prompt.replace(legacy, "人物身份与造型")
        prompt = re.sub(r"[，。、；]{2,}", "。", prompt).strip("，。、； ")
        return prompt

    def _submit_next_canvas_character(self):
        if not self._canvas_character_queue:
            record = self._custom_record(self._canvas_storyboard_source)
            if record is not None:
                record["status"] = ("阶段 2/6 · 资产候选已生成 · 请逐项采用并锁定，"
                                    "然后执行阶段 3")
                record["pipeline_stage"] = "assets_generated"
                record["approval_required"] = "blocking"
                self._save_layout_now()
            self.refresh()
            self._schedule_auto_continue(
                self._canvas_storyboard_source, from_async=True)
            return
        job = self._canvas_character_queue.pop(0)
        node_id = str(job.get("node_id") if isinstance(job, dict) else job)
        character_role = str(job.get("role") or "") if isinstance(job, dict) else ""
        scene_role = str(job.get("scene_role") or "") if isinstance(job, dict) else ""
        record = self._custom_record(node_id)
        if record is None:
            self._submit_next_canvas_character(); return
        manager = get_ai_manager()
        existing_set = dict(record.get("character_reference_set") or {})
        identity_anchor = str(existing_set.get("portrait") or record.get("path") or "")
        scene_set = dict(record.get("scene_reference_set") or {})
        scene_anchor = str(scene_set.get("master") or record.get("path") or "")
        operation = ("image_edit" if character_role and identity_anchor and
                     os.path.exists(identity_anchor) else
                     "image_edit" if scene_role and scene_role != "master" and
                     os.path.exists(scene_anchor) else "text_to_image")
        source_id = str(self._canvas_storyboard_source or
                        self._source_storyboard_for_asset(node_id) or "")
        provider = self._locked_storyboard_image_provider(operation, source_id)
        suffix = {
            "character": "电影角色身份设定，固定五官、发型、体型、服装、鞋履、材质和配色",
            "scene": ("电影场景空间母版，无人物；同一张设定板同时展示主透视空镜与俯视平面示意，"
                      "固定入口出口、门窗、墙体、家具、关键道具、人物可活动区域、主要光源和南北方位；"
                      "后续所有镜头必须继承完全相同的空间结构、物体方位、时间天气与色彩"),
            "element": "电影道具产品设定图，中性背景，多角度展示固定造型、材质、颜色、比例和细节",
        }.get(str(record.get("asset_kind") or "character"), "电影制作资产设定图")
        if character_role:
            role_prompt = next((prompt for role, _label, prompt in CHARACTER_REFERENCE_SPECS
                                if role == character_role), suffix)
            suffix = (role_prompt + "。这是影视生产用权威角色设定资料，白色或浅灰中性背景，"
                      "画面中不要出现说明文字、水印、边框和无关人物")
        elif scene_role:
            view_prompt = next((prompt for role, _label, prompt in SCENE_VIEW_SPECS
                                if role == scene_role), suffix)
            proxy = dict(record.get("scene_proxy") or {})
            suffix = (
                f"{view_prompt}。这是同一物理空间的权威视图，不是新场景。"
                f"统一俯视坐标与3D代理：{json.dumps(proxy, ensure_ascii=False)}。"
                "所有墙体、入口、窗户、固定机器、桌子、柜台的ID、数量、世界坐标、尺寸、"
                "朝向和相互距离必须与主视角一致。空无人物，不要拼图、文字、箭头或边框。")
        base_prompt = (self._character_identity_prompt(record)
                       if str(record.get("asset_kind") or "") == "character"
                       else str(record.get("content") or "").strip())
        prompt = f"{base_prompt}。{suffix}。"
        inputs = {"prompt":prompt}
        if operation == "image_edit":
            active_anchor = identity_anchor if character_role else scene_anchor
            active_role = "character" if character_role else "scene"
            inputs.update({
                "image":active_anchor, "images":[active_anchor],
                "reference_assets":[{
                    "path":active_anchor, "role":active_role,
                    "label":"角色立绘身份锚点" if character_role else
                            "场景主视角与空间坐标锚点", "required":True}],
            })
        output_format = (CHARACTER_REFERENCE_FORMATS.get(character_role)
                         if character_role else SCENE_REFERENCE_FORMATS.get(scene_role)) or {
                             "size":"1536x1024", "ratio":"16:9"}
        handle = manager.submit(provider.name, TaskRequest(
            operation=operation, inputs=inputs,
            params={"size":output_format["size"], "quality":"high", "n":1},
            metadata={"purpose":"canvas_character_sheet", "node_id":node_id,
                      "character_role":character_role,
                      "scene_role":scene_role}, use_cache=False))
        self._standalone_tasks[handle.id] = {
            "handle":handle, "node_id":node_id, "provider":provider.name,
            "kind":"storyboard_character", "character_role":character_role,
            "scene_role":scene_role,
        }
        role_label = next((label for role, label, _prompt in
                           (CHARACTER_REFERENCE_SPECS if character_role else SCENE_VIEW_SPECS)
                           if role == (character_role or scene_role)), "资产设定")
        record["status"] = f"生成中 · {role_label}"; self._save_layout_now()

    def _storyboard_character_node_ids(self, source_node_id):
        return self._storyboard_asset_node_ids(source_node_id, "character")

    def _storyboard_asset_node_ids(self, source_node_id, kind=""):
        return [str(edge.get("target") or "") for edge in
                self._positions().get("__workflow_edges__", [])
                if isinstance(edge, dict) and edge.get("source") == source_node_id and
                edge.get("type") in ("character", "scene", "element") and
                (not kind or edge.get("type") == kind)]

    def _bind_scene_contracts_to_shots(self, source_id: str):
        scenes = [self._custom_record(value) for value in
                  self._storyboard_asset_node_ids(source_id, "scene")]
        scenes = [value for value in scenes if isinstance(value, dict)]
        for shot in self.current_storyboard().get("shots", []):
            name = str(shot.get("scene_name") or "")
            scene = next((value for value in scenes
                          if str(value.get("asset_name") or "") == name),
                         scenes[0] if len(scenes) == 1 else None)
            if scene is None:
                continue
            views = {key:str(path) for key, path in
                     dict(scene.get("scene_reference_set") or {}).items()
                     if key in {"master", "reverse", "left", "right"} and
                     os.path.exists(str(path or ""))}
            view_id = bind_scene_view(shot, views)
            shot["scene_master_id"] = str(scene.get("id") or "")
            shot["scene_master_path"] = str(scene.get("path") or "")
            shot["scene_view_id"] = view_id or "master"
            shot["scene_view_path"] = str(views.get(view_id) or scene.get("path") or "")
            shot["scene_proxy"] = json.loads(json.dumps(
                scene.get("scene_proxy") or {}, ensure_ascii=False))
            if not shot.get("editable_bbox_xy"):
                shot["editable_bbox_xy"] = list(
                    (scene.get("scene_proxy") or {}).get("activity_bbox_xy") or
                    [0.15, 0.12, 0.7, 0.78])

    def _consolidate_legacy_scene_assets(self):
        """Migrate same-location scene states to one authoritative master.

        Older plans created normal light, emergency light and doorway crops as
        separately lockable scene assets.  Keeping their files is useful, but
        only the neutral/master record may participate in production gates.
        """
        changed = False
        edges = self._positions().get("__workflow_edges__", [])
        records = [value for value in self._positions().get("__custom_nodes__", [])
                   if isinstance(value, dict)]
        by_id = {str(value.get("id") or ""): value for value in records}
        source_ids = {str(edge.get("source") or "") for edge in edges
                      if isinstance(edge, dict) and edge.get("type") == "scene"}
        for source_id in source_ids:
            source_changed = False
            scene_ids = [str(edge.get("target") or "") for edge in edges
                         if isinstance(edge, dict) and edge.get("source") == source_id and
                         edge.get("type") == "scene"]
            groups = {}
            for scene_id in scene_ids:
                record = by_id.get(scene_id)
                if record is not None:
                    groups.setdefault(scene_location_key(record), []).append(record)
            for location_id, values in groups.items():
                if len(values) < 2:
                    record = values[0]
                    if str(record.get("location_id") or "") != location_id:
                        record["location_id"] = location_id; changed = True
                    proxy = normalize_scene_proxy(record)
                    if record.get("scene_proxy") != proxy:
                        record["scene_proxy"] = proxy
                        record["scene_proxy_signature"] = scene_proxy_signature(proxy)
                        changed = True
                    refs = dict(record.get("scene_reference_set") or {})
                    path = str(record.get("path") or "")
                    if path and os.path.exists(path) and not refs.get("master"):
                        refs["master"] = path
                        record["scene_reference_set"] = refs
                        record["locked"] = False
                        record["status"] = f"场景视图 {len(refs)}/5 · 待补齐"
                        changed = True
                    continue
                masters, _aliases = consolidate_scene_specs(values)
                master_name = str(masters[0].get("name") or
                                  masters[0].get("asset_name") or "")
                master = next((value for value in values if
                               str(value.get("asset_name") or value.get("name") or "") == master_name),
                              values[0])
                master["location_id"] = location_id
                master["scene_states"] = masters[0].get("scene_states", [])
                master["scene_master"] = True
                proxy = normalize_scene_proxy({**master, "location_id":location_id})
                master["scene_proxy"] = proxy
                master["scene_proxy_signature"] = scene_proxy_signature(proxy)
                master_refs = dict(master.get("scene_reference_set") or {})
                master_path = str(master.get("path") or "")
                if master_path and os.path.exists(master_path):
                    master_refs.setdefault("master", master_path)
                master["scene_reference_set"] = master_refs
                if not all(os.path.exists(str(master_refs.get(role) or ""))
                           for role, _label, _prompt in SCENE_VIEW_SPECS):
                    master["locked"] = False
                    master["status"] = f"场景视图 {len(master_refs)}/5 · 待补齐"
                master_id = str(master.get("id") or "")
                master_asset_name = str(master.get("asset_name") or master_name)
                variants = [value for value in values if value is not master]
                variant_names = {str(value.get("asset_name") or "") for value in variants}
                for variant in variants:
                    variant_id = str(variant.get("id") or "")
                    variant["scene_variant_of"] = master_id
                    variant["location_id"] = location_id
                    variant["state_preview_path"] = str(variant.get("path") or "")
                    variant["asset_kind"] = "scene_state"
                    variant["asset_role"] = "scene_state_preview"
                    variant["reference_role"] = "reference"
                    variant["locked"] = False
                    variant["status"] = f"状态预览 · 空间继承 {master_asset_name}"
                    for edge in edges:
                        if (isinstance(edge, dict) and edge.get("source") == source_id and
                                str(edge.get("target") or "") == variant_id and
                                edge.get("type") == "scene"):
                            edge["type"] = "scene_state"
                for shot in self.current_storyboard().get("shots", []):
                    old_name = str(shot.get("scene_name") or "")
                    if old_name in variant_names:
                        shot["scene_name"] = master_asset_name
                        shot["scene_state"] = old_name
                        shot["production_ready"] = False
                changed = True; source_changed = True
            if source_changed:
                shots = list(self.current_storyboard().get("shots", []))
                shot_ids = {str(shot.get("id") or "") for shot in shots}
                for shot in shots:
                    shot["production_ready"] = False
                    shot["invalidated_by"] = "旧场景状态已归并为唯一空间母版"
                    for key in ("final_image_prompt", "final_start_image_prompt",
                                "final_end_image_prompt", "final_video_prompt",
                                "space_geometry_contract", "scene_master_path",
                                "scene_master_id"):
                        shot.pop(key, None)
                for value in records:
                    if (value.get("generator_kind") in ("image", "video") and
                            (str(value.get("shot_id") or "") in shot_ids or
                             value.get("type") == "workflow_group")):
                        value["status"] = "场景母版归并 · 旧结果已失效"
                        value["invalidated"] = True
                source = by_id.get(source_id)
                if source is not None:
                    source["status"] = "场景已归并为唯一空间母版 · 请重新执行阶段 3"
                    source["pipeline_stage"] = "assets_changed"
        return changed

    def _remove_legacy_character_portrait_view(self, parent_id: str):
        """The character asset parent is the portrait; remove old duplicate portrait nodes."""
        values = self._positions().get("__custom_nodes__", [])
        duplicate_ids = {
            str(value.get("id") or "") for value in values
            if isinstance(value, dict) and
            str(value.get("reference_parent_id") or "") == parent_id and
            str(value.get("character_panel_role") or value.get("reference_role") or "") == "portrait"
        }
        if not duplicate_ids:
            return False
        self._positions()["__custom_nodes__"] = [
            value for value in values
            if not (isinstance(value, dict) and value.get("id") in duplicate_ids)]
        self._positions()["__workflow_edges__"] = [
            edge for edge in self._positions().get("__workflow_edges__", [])
            if not (isinstance(edge, dict) and (
                edge.get("source") in duplicate_ids or edge.get("target") in duplicate_ids))]
        for node_id in duplicate_ids:
            self._positions().pop(node_id, None)
        return True

    def _character_reference_position(self, parent_id: str, role: str):
        parent = self._positions().get(parent_id, [0, 0])
        offsets = {
            "face_closeup": (650.0, 0.0),
            "expressions": (1300.0, 0.0),
            "turnaround": (650.0, 620.0),
        }
        dx, dy = offsets.get(role, (520.0, 540.0))
        return [float(parent[0]) + dx, float(parent[1]) + dy]

    def _scene_reference_position(self, parent_id: str, role: str):
        parent = self._positions().get(parent_id, [0, 0])
        offsets = {
            "reverse":(650.0, 0.0), "left":(1300.0, 0.0),
            "right":(650.0, 500.0), "topdown":(1300.0, 500.0),
        }
        dx, dy = offsets.get(role, (650.0, 0.0))
        return [float(parent[0]) + dx, float(parent[1]) + dy]

    def prepare_canvas_storyboard_assets(self, node):
        if not self.current_storyboard().get("shots"):
            QMessageBox.information(self, "准备资产", "请先完成阶段 1：拆解镜头。")
            return
        source_id = str(node.node_id)
        character_ids = self._storyboard_asset_node_ids(source_id)
        if not character_ids:
            record = self._custom_record(node.node_id)
            if record is not None:
                record["status"] = "阶段 2/6 · 无生产资产 · 请确认后执行阶段 3"
                record["pipeline_stage"] = "assets_ready"
                record["approval_required"] = "blocking"
                self._save_layout_now(); self.refresh()
                self._schedule_auto_continue(source_id, from_async=True)
            return
        self._canvas_storyboard_source = node.node_id
        self._canvas_storyboard_character_refs = []
        self._canvas_character_queue = []
        for node_id in character_ids:
            record = self._custom_record(node_id)
            path = str((record or {}).get("path") or "")
            kind = str((record or {}).get("asset_kind") or "")
            if kind == "character" and record is not None:
                self._remove_legacy_character_portrait_view(node_id)
                record["title"] = f"{record.get('asset_name') or '角色'} · 角色立绘"
                record["ratio"] = "2:3"
                record["reference_role"] = "character"
            reference_set = dict((record or {}).get("character_reference_set") or {})
            scene_reference_set = dict((record or {}).get("scene_reference_set") or {})
            complete_character = (kind != "character" or all(
                os.path.exists(str(reference_set.get(role) or ""))
                for role, _label, _prompt in CHARACTER_REFERENCE_SPECS))
            complete_scene = (kind != "scene" or all(
                os.path.exists(str(scene_reference_set.get(role) or ""))
                for role, _label, _prompt in SCENE_VIEW_SPECS))
            if path and os.path.exists(path) and complete_character and complete_scene:
                if kind != "scene":
                    self._canvas_storyboard_character_refs.append(path)
            else:
                if kind == "character":
                    for role, _label, _prompt in CHARACTER_REFERENCE_SPECS:
                        if not os.path.exists(str(reference_set.get(role) or "")):
                            self._canvas_character_queue.append(
                                {"node_id":node_id, "role":role})
                elif kind == "scene":
                    for role, _label, _prompt in SCENE_VIEW_SPECS:
                        if not os.path.exists(str(scene_reference_set.get(role) or "")):
                            self._canvas_character_queue.append(
                                {"node_id":node_id, "scene_role":role})
                else:
                    self._canvas_character_queue.append(node_id)
        if self._canvas_character_queue:
            source_record = self._custom_record(node.node_id)
            if source_record is not None:
                source_record["status"] = f"阶段 2/6 · 生成 {len(self._canvas_character_queue)} 个资产候选"
                self._save_layout_now()
            try:
                self._submit_next_canvas_character()
            except Exception as error:
                QMessageBox.warning(self, "资产生成失败", str(error))
        else:
            record = self._custom_record(node.node_id)
            if record is not None:
                all_locked = all(bool((self._custom_record(value) or {}).get("locked"))
                                 for value in character_ids)
                record["status"] = ("阶段 2/6 · 资产已锁定 · 请执行阶段 3" if all_locked else
                                    "阶段 2/6 · 资产已生成 · 待逐项采用并锁定")
                record["pipeline_stage"] = "assets_ready" if all_locked else "assets_generated"
                record["approval_required"] = "blocking"
                self._save_layout_now(); self.refresh()
                self._schedule_auto_continue(source_id, from_async=True)

    @staticmethod
    def _motion_frame_target(duration):
        seconds = max(0.1, float(duration or 0))
        if seconds <= 3:
            return 3
        if seconds <= 6:
            return 4
        if seconds <= 10:
            return 5
        return 6

    def _normalize_motion_keyframes(self, shot: dict):
        """Guarantee a reviewable 3–6 frame motion contract for every shot."""
        normalize_director_contract(shot)
        duration = max(0.1, float(shot.get("duration") or 5))
        target = self._motion_frame_target(duration)
        raw = [value for value in shot.get("motion_keyframes", [])
               if isinstance(value, dict)]
        if len(raw) != target:
            raw = []
        labels_by_count = {
            3:("起始", "动作峰值", "结束"),
            4:("起始", "动作发展", "动作峰值", "结束"),
            5:("起始", "动作发展", "动作峰值", "动作延续", "结束"),
            6:("起始", "蓄势", "动作发展", "动作峰值", "收势", "结束"),
        }
        count = len(raw) if raw else target
        labels = labels_by_count[count]
        positions = [value for value in shot.get("character_positions", [])
                     if isinstance(value, dict)]
        movement_contract = "；".join(
            f"{value.get('name') or '人物'}：{value.get('start') or '起点'} → "
            f"{value.get('end') or '终点'}，{value.get('movement') or '按动作线移动'}"
            for value in positions) or str(shot.get("action_line") or shot.get("blocking") or "")
        # frame_start/frame_end are the visible state authority shared with the
        # neighbouring shots.  Model-authored motion frames are useful for the
        # middle of an action, but must never silently replace those endpoints.
        authoritative_start = str(
            shot.get("frame_start") or shot.get("action_start") or
            shot.get("visual") or "").strip()
        authoritative_end = str(
            shot.get("frame_end") or shot.get("action_end") or
            authoritative_start).strip()
        primary_action = self._normal_speed_action_text(
            shot.get("primary_action") or shot.get("action_line") or
            shot.get("blocking") or "主体完成规定动作").strip()
        frames = []
        for index in range(count):
            source = dict(raw[index]) if raw else {}
            ratio = index / max(1, count - 1)
            seconds = round(duration * ratio, 2)
            raw_seconds = source.get("time_seconds")
            try:
                parsed_seconds = float(raw_seconds)
            except (TypeError, ValueError):
                match = re.search(r"-?\d+(?:\.\d+)?", str(raw_seconds or ""))
                parsed_seconds = float(match.group()) if match else seconds
            parsed_seconds = max(0.0, min(duration, parsed_seconds))
            hero_value = source.get("is_hero")
            source_is_hero = (hero_value if isinstance(hero_value, bool) else
                              str(hero_value or "").strip().lower() in
                              ("1", "true", "yes", "是"))
            if index == 0:
                fallback_composition = authoritative_start
            elif index == count - 1:
                fallback_composition = authoritative_end
            else:
                fallback_composition = (
                    f"{shot.get('visual') or shot.get('image_prompt') or ''}；"
                    f"动作推进约 {int(ratio * 100)}%")
            composition = str(source.get("composition") or fallback_composition)
            character_state = str(source.get("character_state") or movement_contract)
            frame_action = str(source.get("action") or shot.get("action_line") or
                               shot.get("blocking") or "")
            if index == 0 and authoritative_start:
                composition = authoritative_start
                character_state = authoritative_start
                frame_action = f"动作尚未开始；下一格才开始：{primary_action}"
            elif index == count - 1 and authoritative_end:
                composition = authoritative_end
                character_state = authoritative_end
                frame_action = f"主动作已经完成：{primary_action}；保持结束状态"
            frames.append({
                "index": index + 1,
                "label": str(source.get("label") or source.get("frame_label") or labels[index]),
                "time_seconds": parsed_seconds,
                "time_ratio": round(ratio, 3),
                "composition": composition,
                "character_state": character_state,
                "action": frame_action,
                "camera_state": str(source.get("camera_state") or
                                    shot.get("camera_position") or shot.get("camera_slot") or ""),
                "character_arrow": str(source.get("character_arrow") or movement_contract),
                "camera_arrow": str(source.get("camera_arrow") or
                                   shot.get("camera_movement") or "固定机位，无摄影机箭头"),
                "gaze_arrow": str(source.get("gaze_arrow") or shot.get("eyeline") or ""),
                "screen_direction": str(source.get("screen_direction") or
                                        shot.get("axis_rule") or shot.get("action_line") or ""),
                "is_hero": source_is_hero,
            })
        times = [float(value["time_seconds"]) for value in frames]
        if (times[0] != 0 or times[-1] != duration or
                any(times[index] <= times[index - 1]
                    for index in range(1, len(times)))):
            for index, value in enumerate(frames):
                value["time_seconds"] = round(
                    duration * index / max(1, len(frames) - 1), 2)
        hero = next((index for index, value in enumerate(frames)
                     if value.get("is_hero")), max(0, min(count - 1, count // 2)))
        for index, value in enumerate(frames):
            value["is_hero"] = index == hero
        shot["motion_keyframes"] = frames
        shot["motion_frame_count"] = len(frames)
        shot["motion_hero_frame"] = hero + 1
        return frames

    @staticmethod
    def _xy_from_position_text(value):
        text = str(value or "")
        match_x = re.search(r"\bx\s*[=:：]\s*(-?\d+(?:\.\d+)?)", text, re.I)
        match_y = re.search(r"\by\s*[=:：]\s*(-?\d+(?:\.\d+)?)", text, re.I)
        if not match_x or not match_y:
            return None
        return float(match_x.group(1)), float(match_y.group(1))

    def _motion_visibility_contract(self, shot: dict) -> str:
        duration = max(.5, float(shot.get("duration") or 5))
        source = " ".join(str(shot.get(key) or "") for key in (
            "visual", "blocking", "primary_action", "action_line"))
        walking = bool(re.search(r"走|步|进入|穿过|靠近|跑|walk|step|enter|cross|run", source, re.I))
        positions = shot.get("character_positions")
        if isinstance(positions, dict):
            positions = [positions]
        rows = [row for row in (positions or []) if isinstance(row, dict)]
        distances = []
        for row in rows:
            start = self._xy_from_position_text(row.get("start"))
            end = self._xy_from_position_text(row.get("end"))
            movement = str(row.get("movement") or "")
            if start and end and not re.search(r"无位移|无移动|保持|不动", movement):
                distances.append(((end[0] - start[0]) ** 2 +
                                  (end[1] - start[1]) ** 2) ** .5)
        measured = max(distances, default=0.0)
        if walking:
            required = min(.30, max(.18, duration * .045))
            correction = (f"原合同人物画面位移约{measured:.2f}，低于可见门槛；"
                          if measured and measured < required else "")
            return (
                f"{correction}步行动作必须在K1到K末形成至少画面对角线{required:.0%}的可见位移，"
                f"约每0.55秒一个完整步幅，{duration:g}秒内展示不同脚步相位；"
                "不得用四个近似静止姿势、滑行或龟速移动代替。这个动作可见度门槛高于旧的“缓慢/两小步”措辞。")
        return (
            "相邻画格的主体姿势、重心或道具状态必须有肉眼可辨的推进；"
            "禁止仅改变箭头、标签、光线或轻微推镜来伪装动作进度。")

    def _ensure_shot_stage_capture(self, shot: dict,
                                   aspect_ratio: str | None = None) -> str:
        """Create one camera-specific composition authority before drawing.

        Manual captures remain authoritative.  Old projects without one get a
        deterministic proxy render so a wide scene plate is never asked to
        invent a close camera and duplicate its foreground fixtures.
        """
        aspect = normalize_aspect_ratio(
            aspect_ratio or self._storyboard_production_ratio())
        existing_path = str(shot.get("scene_stage_capture") or "")
        if existing_path and os.path.exists(existing_path) and not shot.get(
                "scene_stage_capture_auto"):
            return existing_path
        proxy = normalize_scene_proxy(shot.get("scene_proxy") or {})
        issues = scene_proxy_issues(proxy)
        shot["scene_proxy"] = proxy
        shot["scene_proxy_issues"] = issues
        signature_payload = json.dumps({
            "shot_id":shot.get("id"), "proxy":scene_proxy_signature(proxy),
            "view":shot.get("scene_view_id"), "size":shot.get("shot_size"),
            "camera":shot.get("camera_position"),
            "positions":shot.get("character_positions"),
            "aspect_ratio":aspect,
        }, ensure_ascii=False, sort_keys=True)
        signature = hashlib.sha1(signature_payload.encode("utf-8")).hexdigest()[:16]
        if (existing_path and os.path.exists(existing_path) and
                str(shot.get("scene_stage_capture_signature") or "") == signature):
            return existing_path
        try:
            from ai.ui.scene_stage_dialog import SceneStageViewport
            stage = normalize_scene_stage(
                shot.get("scene_stage") or {}, proxy=proxy, shot=shot)
            compiled = stage_shot_contract(stage)
            for key in ("scene_stage", "scene_stage_id", "scene_stage_version",
                        "camera_id", "camera_object"):
                shot[key] = json.loads(json.dumps(compiled.get(key), ensure_ascii=False))
            folder = Path(__file__).parents[2] / "work_temp" / "scene_stage_auto"
            path = folder / f"{shot.get('id') or 'shot'}_{signature}.png"
            viewport = SceneStageViewport(stage)
            viewport.set_view_camera(str((active_camera(stage) or {}).get("id") or ""))
            saved = viewport.render_clean(path, aspect)
            viewport.deleteLater()
            if saved:
                shot["scene_stage_capture"] = str(path)
                shot["composition_reference_path"] = str(path)
                shot["scene_stage_capture_auto"] = True
                shot["scene_stage_capture_signature"] = signature
                shot["scene_stage_capture_aspect_ratio"] = aspect
                return str(path)
        except (ImportError, OSError, ValueError, TypeError, RuntimeError):
            pass
        return ""

    @staticmethod
    def _motion_board_endpoint_crop(shot: dict) -> str:
        clean_panels = [str(value) for value in
                        (shot.get("motion_panel_paths") or []) if value]
        if clean_panels and os.path.exists(clean_panels[-1]):
            # V5 panels contain no labels, arrows or neighbouring frames.  They
            # are the safe continuity endpoint; cropping the contact sheet is a
            # legacy fallback only.
            return clean_panels[-1]
        source = str(shot.get("motion_board_path") or shot.get("draft_panel") or "")
        frames = [row for row in shot.get("motion_keyframes", [])
                  if isinstance(row, dict)]
        count = len(frames)
        if not source or not os.path.exists(source) or not 3 <= count <= 6:
            return ""
        try:
            from PIL import Image
            columns = 2 if count <= 4 else 3
            rows = 2
            index = count - 1
            column, row = index % columns, index // columns
            stat = os.stat(source)
            digest = hashlib.sha1(
                f"{os.path.abspath(source)}|{stat.st_mtime_ns}|{count}|{index}".encode()
            ).hexdigest()[:14]
            folder = Path(__file__).parents[2] / "work_temp" / "storyboard_endpoints"
            folder.mkdir(parents=True, exist_ok=True)
            output = folder / f"endpoint_{digest}.png"
            if output.exists():
                return str(output)
            with Image.open(source) as image:
                width, height = image.size
                cell_w, cell_h = width / columns, height / rows
                margin_x, margin_y = cell_w * .035, cell_h * .055
                box = (
                    int(column * cell_w + margin_x),
                    int(row * cell_h + margin_y),
                    int((column + 1) * cell_w - margin_x),
                    int((row + 1) * cell_h - margin_y),
                )
                image.crop(box).convert("RGB").save(output)
            return str(output)
        except (ImportError, OSError, ValueError):
            return ""

    @staticmethod
    def _inspect_motion_board(path: str, frame_count: int) -> dict:
        """Reject high-resolution boards whose panels are effectively clones."""
        if not path or not os.path.exists(path) or not 3 <= int(frame_count or 0) <= 6:
            return {"status":"unavailable", "issues":["MOTION_BOARD_INPUT_INVALID"]}
        try:
            from PIL import Image, ImageChops, ImageStat
            with Image.open(path) as source:
                image = source.convert("RGB")
            width, height = image.size
            # Tiny synthetic fixtures used by tests and legacy thumbnails do
            # not contain enough pixels for a meaningful motion verdict.
            if width < 900 or height < 550:
                return {"status":"unavailable", "issues":["MOTION_BOARD_TOO_SMALL_FOR_QC"]}
            columns = 2 if frame_count <= 4 else 3
            rows = 2
            panels = []
            for index in range(frame_count):
                column, row = index % columns, index // columns
                cell_w, cell_h = width / columns, height / rows
                panel = image.crop((
                    int(column * cell_w + cell_w * .07),
                    int(row * cell_h + cell_h * .12),
                    int((column + 1) * cell_w - cell_w * .05),
                    int((row + 1) * cell_h - cell_h * .05),
                )).resize((160, 90)).convert("L")
                panels.append(panel)
            deltas = []
            for left, right in zip(panels, panels[1:]):
                mean = ImageStat.Stat(ImageChops.difference(left, right)).mean[0] / 255.0
                deltas.append(round(mean, 5))
            issues = []
            if deltas and max(deltas) < .025:
                issues.append("MOTION_PANELS_NEAR_DUPLICATE")
            if deltas and sum(delta < .018 for delta in deltas) >= max(2, len(deltas) - 1):
                issues.append("MOTION_PROGRESS_INVISIBLE")
            return {
                "status":"fail" if issues else "pass",
                "issues":list(dict.fromkeys(issues)),
                "adjacent_visual_deltas":deltas,
            }
        except (ImportError, OSError, ValueError, TypeError):
            return {"status":"unavailable", "issues":["MOTION_BOARD_QC_UNAVAILABLE"]}

    def _storyboard_authority_references(self, shot: dict, shot_index: int) -> list[str]:
        references = []
        stage_capture = self._ensure_shot_stage_capture(shot)
        if stage_capture:
            references.append(stage_capture)
        scene_view = str(shot.get("scene_view_path") or shot.get("scene_master_path") or "")
        if scene_view and os.path.exists(scene_view):
            references.append(scene_view)
        shots = self.current_storyboard().get("shots", [])
        if shot_index > 0:
            previous = shots[shot_index - 1]
            # Only inherit pixels across the same camera family.  A new angle
            # inherits state in text/stage, not the previous perspective.
            if str(previous.get("scene_view_id") or "master") == str(
                    shot.get("scene_view_id") or "master"):
                endpoint = self._motion_board_endpoint_crop(previous)
                if endpoint:
                    references.append(endpoint)
                    shot["continuity_endpoint_reference"] = endpoint
        references.extend(self._canvas_storyboard_character_refs)
        board_paths = set()
        for value in shots:
            board_paths.update(self._motion_board_paths(value))
        return list(dict.fromkeys(
            value for value in references if value and os.path.exists(value)
            and value not in board_paths))[:9]

    def _motion_panel_reference_assets(self, shot: dict, shot_index: int,
                                       frame_index: int,
                                       aspect_ratio: str | None = None) -> list[dict]:
        """Bind at most three references to explicit, non-overlapping roles."""
        assets = []
        pending = list(shot.get("motion_panel_pending_paths") or [])
        current = list(shot.get("motion_panel_paths") or [])
        prior_panel = ""
        if frame_index > 0:
            for source in (pending, current):
                if len(source) >= frame_index and os.path.exists(str(source[frame_index - 1] or "")):
                    prior_panel = str(source[frame_index - 1]); break
        if prior_panel:
            assets.append({
                "path":prior_panel, "role":"composition",
                "label":f"K{frame_index} 已确认的上一动作状态", "required":True,
                "weight":1.35, "priority":0,
            })
        else:
            stage_capture = self._ensure_shot_stage_capture(shot, aspect_ratio)
            if stage_capture:
                assets.append({
                    "path":stage_capture, "role":"composition",
                    "label":"本镜摄影机、透视、站位与固定物构图权威", "required":True,
                    "weight":1.35, "priority":0,
                })
        scene_view = str(shot.get("scene_view_path") or shot.get("scene_master_path") or "")
        if scene_view and os.path.exists(scene_view) and all(
                str(value.get("path") or "") != scene_view for value in assets):
            assets.append({
                "path":scene_view, "role":"scene", "label":"场景外观、材质与光线权威",
                "required":True, "weight":1.0, "priority":10,
            })
        used = {str(value.get("path") or "") for value in assets}
        character = next((
            str(value) for value in self._canvas_storyboard_character_refs
            if value and os.path.exists(str(value)) and str(value) not in used
        ), "")
        if character:
            assets.append({
                "path":character, "role":"character", "label":"出镜人物身份与服装权威",
                "required":True, "weight":1.15, "priority":20,
            })
        return normalize_reference_assets(assets)[:3]

    def _queue_motion_storyboard_panels(self, shot_indices=None, *,
                                        frame_index=None,
                                        kind="storyboard_panel") -> str:
        shots = self.current_storyboard().get("shots", [])
        indices = list(range(len(shots))) if shot_indices is None else [
            int(value) for value in shot_indices]
        aspect = self._storyboard_production_ratio()
        generation_id = hashlib.sha1(
            f"{datetime.now().isoformat()}|{indices}|{frame_index}|{kind}|{aspect}".encode()
        ).hexdigest()[:14]
        queue = []
        for shot_index in indices:
            if not 0 <= shot_index < len(shots):
                continue
            shot = shots[shot_index]
            frames = self._normalize_motion_keyframes(shot)
            existing = list(shot.get("motion_panel_paths") or [])
            if len(existing) != len(frames):
                existing = [""] * len(frames)
            pending = list(existing)
            targets = ([int(frame_index)] if frame_index is not None else
                       list(range(len(frames))))
            if frame_index is None:
                pending = [""] * len(frames)
            shot["motion_panel_pending_paths"] = pending
            shot["motion_panel_pending_generation_id"] = generation_id
            shot["motion_panel_pending_aspect_ratio"] = aspect
            shot["motion_board_review_status"] = "regenerating"
            for panel_index in targets:
                if 0 <= panel_index < len(frames):
                    queue.append({
                        "shot_index":shot_index, "frame_index":panel_index,
                        "kind":kind, "generation_id":generation_id,
                        "aspect_ratio":aspect,
                    })
        self._canvas_storyboard_queue = queue
        return generation_id

    def _commit_motion_storyboard_panels(self, shot: dict, shot_index: int,
                                         provider: str, *, reroll: bool) -> str:
        frames = self._normalize_motion_keyframes(shot)
        paths = [str(value) for value in
                 (shot.get("motion_panel_pending_paths") or [])]
        if len(paths) != len(frames) or not all(
                value and os.path.exists(value) for value in paths):
            return ""
        aspect = normalize_aspect_ratio(
            shot.get("motion_panel_pending_aspect_ratio") or
            self._storyboard_production_ratio())
        board_path = assemble_motion_storyboard(
            paths, frames, Path(__file__).parents[2] / "work_temp" / "storyboard_boards",
            shot_id=str(shot.get("id") or shot_index + 1),
            contract_version=MOTION_STORYBOARD_CONTRACT_VERSION,
            aspect_ratio=aspect)
        assets = shot.setdefault("assets", [])
        for asset in assets:
            if isinstance(asset, dict) and str(asset.get("subtype") or "") == "motion_storyboard":
                asset["approved"] = False
        assets.append({
            "path":board_path, "kind":"image",
            "source":f"{provider or 'project-image-model'}-panel-composite",
            "provider":str(provider or ""), "subtype":"motion_storyboard",
            "frame_count":len(frames),
            "contract_version":MOTION_STORYBOARD_CONTRACT_VERSION,
            "aspect_ratio":aspect,
            "version":1 + sum(
                1 for value in assets if isinstance(value, dict) and
                str(value.get("subtype") or "") == "motion_storyboard"),
            "approved":True, "assembled_locally":True,
            "panel_paths":list(paths),
        })
        shot["motion_panel_paths"] = list(paths)
        shot.pop("motion_panel_pending_paths", None)
        shot.pop("motion_panel_pending_generation_id", None)
        shot.pop("motion_panel_pending_aspect_ratio", None)
        shot["draft_panel"] = board_path
        shot["motion_board_path"] = board_path
        shot["preview_asset"] = board_path
        shot["selected_asset"] = board_path
        shot["asset_type"] = "image"
        shot["draft_source"] = "ai"
        shot["motion_board_contract_version"] = MOTION_STORYBOARD_CONTRACT_VERSION
        shot["motion_board_aspect_ratio"] = aspect
        qc = inspect_motion_panels(paths, shot, aspect)
        shot["motion_board_qc"] = qc
        auto_rejected = qc.get("status") == "fail"
        shot["motion_board_review_status"] = (
            "auto_rejected" if auto_rejected else "pending_review")
        if auto_rejected:
            assets[-1]["approved"] = False
        if reroll:
            shot["motion_board_reroll_count"] = int(
                shot.get("motion_board_reroll_count") or 0) + 1
        return board_path

    def _motion_storyboard_prompt(self, shot: dict, shot_index: int, bible: str,
                                  redraw=False):
        frames = self._normalize_motion_keyframes(shot)
        count = len(frames)
        grid = "2 列 × 2 行" if count <= 4 else "3 列 × 2 行"
        frame_lines = []
        for frame in frames:
            frame_lines.append(
                f"K{frame['index']}｜{frame['time_seconds']:g}s｜{frame['label']}："
                f"构图={frame['composition']}；人物状态={frame['character_state']}；"
                f"动作={frame['action']}；机位={frame['camera_state']}；"
                f"人物箭头={frame['character_arrow']}；摄影机箭头={frame['camera_arrow']}；"
                f"视线箭头={frame['gaze_arrow']}；屏幕方向={frame['screen_direction']}")
        invariants = "；".join(
            str(value).strip() for value in
            (shot.get("continuity_invariants") or []) if str(value).strip())
        start_state = str(shot.get("frame_start") or
                          shot.get("action_start") or "").strip()
        end_state = str(shot.get("frame_end") or
                        shot.get("action_end") or start_state).strip()
        geometry_contract = str(shot.get("space_geometry_contract") or "").strip()
        motion_visibility = self._motion_visibility_contract(shot)
        stage_rule = (
            "第一张参考图是本镜摄影机专属3D构图控制图；只继承其机位、人物站位、固定物数量和遮挡，"
            "不得输出其中的网格、几何框或代理材质。" if shot.get("scene_stage_capture") else "")
        return (
            f"{'重新绘制并完善' if redraw else '绘制'}专业影视帧运动分镜板。"
            f"这是第 {shot_index + 1} 镜的一镜多帧动作拆解，绝对不是单张全景图。\n"
            f"画布为 16:9 横向分镜板，严格使用 {grid}，只画 {count} 个有效画框，"
            "按从左到右、从上到下排列；空余格保持空白，不得增加剧情。"
            f"统一设定：{bible}。景别：{shot.get('shot_size')}。"
            f"空间：{shot.get('spatial_layout')}；前景：{shot.get('foreground')}；"
            f"中景：{shot.get('midground')}；后景：{shot.get('background')}。"
            f"摄影机位置：{shot.get('camera_position')}；轴线：{shot.get('axis_rule')}。"
            f"绑定场景视角：{shot.get('scene_view_id') or 'master'}。"
            f"统一俯视坐标与3D代理：{json.dumps(shot.get('scene_proxy') or {}, ensure_ascii=False)}。\n"
            f"起始状态权威：{start_state}。结束状态权威：{end_state}。"
            f"连续性不变量：{invariants or '保持身份、场景、光线和屏幕方向'}。"
            f"空间硬约束：{geometry_contract or '固定设施数量、外形、尺度和位置以绑定场景权威图为准'}。\n"
            f"动作可见度硬门槛：{motion_visibility}\n{stage_rule}\n"
            "逐帧合同：\n" + "\n".join(frame_lines) + "\n"
            "状态优先级：起始状态权威和结束状态权威高于逐帧合同中的旧描述；若冲突，必须服从起止状态权威。"
            "所有画格必须保持同一人物身份、脸、发型、未参与动作的服装、场景结构、道具尺度、"
            "人物左右关系和轴线侧一致；但主动作明确改变的服装或道具状态必须真实发生，不能被“不变”规则覆盖。"
            "对象守恒：同一人物、单件服装和单件道具全程只有一个实例；解下、拿起、放下、移动时，"
            "只能把原对象从起点转移到终点，原位置随后必须为空，严禁复制、分身或凭空新增。"
            "固定设施数量与位置只服从绑定场景权威图；不得复制桌、门、窗、钟、台阶或其他家具，"
            "不得把不同画格中的同一固定物误画成多个物体。"
            "相邻画格必须明显展示动作进度变化，禁止每格重复同一姿势或都画成建立全景。"
            "在每一格内部直接画调度符号：人物移动用粗实线箭头，摄影机平移/推拉/摇移用双线箭头，"
            "视线用虚线箭头；跨格动作要让上一格终点严格等于下一格起点。"
            "每格左上角只标 K编号、时间点和极短动作词；人物可用 A/B/C 小标记，摄影机标 CAM。"
            "使用清晰黑白铅笔线稿、灰阶明暗块、明确画框边界和透视深度。"
            "不要对白字幕、长段说明、彩色成片、写实渲染、水印、漫画对白框或多余画格。")

    def compile_canvas_storyboard_prompts(self, node):
        shots = self.current_storyboard().get("shots", [])
        if not shots:
            QMessageBox.information(self, "合成提示词", "请先完成阶段 1：拆解镜头。")
            return
        self._bind_scene_contracts_to_shots(str(node.node_id))
        delivery_ratio = self._storyboard_production_ratio(str(node.node_id))
        character_records = [self._custom_record(value)
                             for value in self._storyboard_asset_node_ids(node.node_id)]
        missing = [str(value.get("title") or "资产") for value in character_records
                   if value and (not os.path.exists(str(value.get("path") or "")) or
                                 not value.get("locked"))]
        if missing:
            QMessageBox.information(
                self, "生产尚未就绪",
                "以下资产尚未生成并锁定：\n" + "\n".join(f"• {value}" for value in missing))
            return
        missing_blocking = [int(shot.get("number") or index + 1)
                            for index, shot in enumerate(shots)
                            if not shot.get("blocking_ready")]
        missing_panels = [
            int(shot.get("number") or index + 1)
            for index, shot in enumerate(shots)
            if (not motion_panels_ready(shot, delivery_ratio) or
                not os.path.exists(str(shot.get("motion_board_path") or "")) or
                not 3 <= len(shot.get("motion_keyframes") or []) <= 6 or
                self._motion_board_contract_stale(shot) or
                str(shot.get("motion_board_review_status") or "") ==
                "regenerating")]
        rejected_panels = [
            int(shot.get("number") or index + 1)
            for index, shot in enumerate(shots)
            if (int(shot.get("number") or index + 1) not in missing_panels and
                str(shot.get("motion_board_review_status") or "") ==
                "auto_rejected")]
        if missing_blocking or missing_panels or rejected_panels:
            details = []
            if missing_blocking:
                details.append("缺少调度合同：" + "、".join(
                    f"{value:02d}" for value in missing_blocking))
            if missing_panels:
                details.append("缺少运动分镜板：" + "、".join(
                    f"{value:02d}" for value in missing_panels))
            if rejected_panels:
                details.append(
                    "运动分镜自动质检未通过：" + "、".join(
                        f"{value:02d}" for value in rejected_panels) +
                    "\n请右键对应镜头重新生成；若你确认现有版本可用，"
                    "请在该运动分镜版本上选择“设为主参考”。")
            QMessageBox.information(
                self, "请先确认调度分镜",
                "阶段 4 只会在调度合同和多帧运动分镜齐全后放行。\n" + "\n".join(details))
            record = self._custom_record(node.node_id)
            if record is not None and (missing_panels or rejected_panels):
                if missing_panels:
                    record["pipeline_stage"] = "assets_ready"
                    record["status"] = ("检测到旧版单帧调度稿或缺失运动板 · "
                                        "请重新执行第 3 步生成多格关键帧")
                else:
                    record["pipeline_stage"] = "storyboard_panels_ready"
                    record["status"] = (
                        "运动分镜自动质检未通过：" + "、".join(
                            f"{value:02d}" for value in rejected_panels) +
                        " · 可重新生成，或明确采用现有版本")
                record["auto_run_enabled"] = False
                self._save_layout_now(); self._update_production_continue_button()
            return
        bible = str((self.current_storyboard().get("visual_bible") or {}).get("ai_storyboard") or "")
        style = str(node.payload.get("style") or "电影写实")
        asset_manifest = []
        for value in character_records:
            if not value:
                continue
            reference_paths = [str(path) for path in
                               (value.get("character_reference_set") or {}).values()
                               if path and os.path.exists(str(path))]
            asset_manifest.append({
                "id":value.get("id"), "kind":value.get("asset_kind"),
                "name":value.get("asset_name"),
                "version":int(value.get("asset_version") or 0),
                "path":value.get("path"),
                "reference_paths":list(dict.fromkeys(reference_paths)),
                "location_id":str(value.get("location_id") or ""),
                "scene_states":json.loads(json.dumps(
                    value.get("scene_states") or [], ensure_ascii=False)),
                "scene_reference_set":json.loads(json.dumps(
                    value.get("scene_reference_set") or {}, ensure_ascii=False)),
                "scene_proxy":json.loads(json.dumps(
                    value.get("scene_proxy") or {}, ensure_ascii=False)),
            })
        for index, shot in enumerate(shots):
            normalize_director_contract(shot)
            self._apply_video_action_policy(shot)
            gate_issues = director_gate_issues(shot)
            shot["director_gate"] = {
                "passed": not gate_issues, "issues": gate_issues,
            }
            motion_frames = self._normalize_motion_keyframes(shot)
            start_frame = motion_frames[0]
            end_frame = motion_frames[-1]
            motion_timeline = "；".join(
                f"K{value['index']} {value['time_seconds']:g}s {value['label']}："
                f"{value['composition']} / {value['action']} / {value['camera_state']}"
                for value in motion_frames)
            searchable = " ".join(str(shot.get(key) or "") for key in
                                  ("visual", "action_line", "dialogue", "image_prompt",
                                   "scene", "scene_name", "character_names", "element_names"))
            shot_assets = []
            for kind in ("character", "scene", "element"):
                candidates = [value for value in asset_manifest if value.get("kind") == kind]
                matched = [value for value in candidates
                           if str(value.get("name") or "") and
                           str(value.get("name") or "") in searchable]
                if not matched and len(candidates) == 1 and kind in ("character", "scene"):
                    matched = candidates
                shot_assets.extend(matched)
            character_names = "、".join(str(value.get("name") or "") for value in shot_assets
                                       if value.get("kind") == "character")
            scene_names = "、".join(str(value.get("name") or "") for value in shot_assets
                                   if value.get("kind") == "scene")
            element_names = "、".join(str(value.get("name") or "") for value in shot_assets
                                     if value.get("kind") == "element")
            scene_master = next((value for value in shot_assets
                                 if value.get("kind") == "scene" and
                                 os.path.exists(str(value.get("path") or ""))), None)
            scene_views = dict((scene_master or {}).get("scene_reference_set") or {})
            cinematic_views = {key:value for key, value in scene_views.items()
                               if key in {"master", "reverse", "left", "right"} and
                               os.path.exists(str(value or ""))}
            bound_view_id = bind_scene_view(shot, cinematic_views)
            bound_view_path = str(cinematic_views.get(bound_view_id) or
                                  (scene_master or {}).get("path") or "")
            scene_state_name = str(shot.get("scene_state") or "默认状态")
            scene_state = next((value for value in
                                (scene_master or {}).get("scene_states", [])
                                if isinstance(value, dict) and
                                str(value.get("name") or "") == scene_state_name), {})
            scene_state_description = str(
                scene_state.get("description") or scene_state_name)
            shot["scene_master_path"] = str((scene_master or {}).get("path") or "")
            shot["scene_master_id"] = str((scene_master or {}).get("id") or "")
            shot["scene_view_id"] = bound_view_id or "master"
            shot["scene_view_path"] = bound_view_path
            shot["scene_proxy"] = json.loads(json.dumps(
                (scene_master or {}).get("scene_proxy") or {}, ensure_ascii=False))
            previous = shots[index - 1] if index else None
            continuity_path = str((previous or {}).get("video_tail_frame") or
                                  (previous or {}).get("selected_image_asset") or "")
            shot["continuity_source_shot_id"] = str((previous or {}).get("id") or "")
            shot["continuity_reference"] = continuity_path if os.path.exists(continuity_path) else ""
            geometry_contract = (
                f"地面空间母版：{shot.get('ground_plane') or '保持场景资产的固定地面结构'}；"
                f"固定地面线：{json.dumps(shot.get('ground_lines') or [], ensure_ascii=False)}；"
                f"地平线y={shot.get('horizon_y', 0.4)}；"
                f"消失点={json.dumps(shot.get('vanishing_point_xy') or [0.5, 0.4], ensure_ascii=False)}。"
                "地面标线、墙地交界、台阶、门柱与家具属于同一空间母版，"
                "不得弯曲、复制、消失、换边或改变彼此连接关系。")
            shot["space_geometry_contract"] = geometry_contract
            stage_contract = ""
            if isinstance(shot.get("scene_stage"), dict):
                compiled_stage = stage_shot_contract(shot["scene_stage"])
                # Keep actor/camera data edited by the user authoritative while
                # the rest of the production contract is being rebuilt.
                for stage_key in (
                        "scene_stage", "scene_stage_id", "scene_stage_version",
                        "camera_id", "camera_object", "camera_position",
                        "character_positions", "blocking_ready"):
                    shot[stage_key] = compiled_stage.get(stage_key)
                stage_contract = (
                    f"3D导演台是构图与空间权威：舞台={shot.get('scene_stage_id')} "
                    f"v{shot.get('scene_stage_version')}，机位={shot.get('camera_id')}；"
                    f"世界坐标人物站位={json.dumps(shot.get('character_positions') or [], ensure_ascii=False)}。"
                    "不得擅自改变人物左右关系、相对距离、遮挡顺序、固定物位置或摄影机FOV。")
            base = (
                f"全片视觉设定：{bible}。风格：{style}。"
                f"项目交付画幅：原生 {delivery_ratio}；所有构图、主体安全区和前导空间均以此画幅为准，"
                "不得先按其他比例构图后再裁切。"
                f"镜头 {index + 1}，{shot.get('shot_size')}。"
                f"画面与走位：{shot.get('visual')}。角色：{character_names or '按剧情'}。"
                f"场景资产：{scene_names or '按剧情'}。道具资产：{element_names or '按剧情'}。"
                f"本镜场景状态：{scene_state_name}；仅允许的状态变化：{scene_state_description}。"
                f"绑定权威场景视角：{shot.get('scene_view_id') or 'master'}；"
                f"动作与视线：{shot.get('action_line')}。机位：{shot.get('camera_slot')}。"
                f"空间调度：{shot.get('spatial_layout')}；人物站位："
                f"{json.dumps(shot.get('character_positions') or [], ensure_ascii=False)}；"
                f"执行走位：{shot.get('blocking')}；视线匹配：{shot.get('eyeline')}；"
                f"摄影机位置：{shot.get('camera_position')}；运镜路径：{shot.get('camera_movement')}；"
                f"{stage_contract}"
                f"轴线：{shot.get('axis_rule')}；前中后景：{shot.get('foreground')} / "
                f"{shot.get('midground')} / {shot.get('background')}；"
                f"{geometry_contract}"
                f"起止构图：{shot.get('frame_start')} → {shot.get('frame_end')}。"
                f"运动关键帧时间线：{motion_timeline}。"
                f"连续性合同：{shot.get('continuity_note')}；继承上一镜的站位、朝向、"
                "服装状态、光线和动作进度。")
            clean_frame_rules = (
                "运动分镜仅作为上述结构化文字合同，不提供其线稿像素作为生图参考。"
                "绝对不要输出多格版式、分镜框线、箭头、运动轨迹、K编号、时间点、CAM、"
                "A/B/C 标记或任何调度说明文字。"
                "保持资产参考中的人物身份、服装、场景结构和道具一致，不要字幕与水印。")
            shot["final_start_image_prompt"] = (
                base + f"只生成一张原生 {delivery_ratio} 单帧定稿（干净视频起始帧），"
                "严格对应动作时间线 K1："
                f"{start_frame['composition']}；人物状态：{start_frame['character_state']}；"
                f"动作尚未开始或处于规定起势：{start_frame['action']}。"
                "这是视频真实第0秒，不得采用动作高潮、结束姿势或重新设计机位。" +
                clean_frame_rules)
            shot["final_end_image_prompt"] = (
                base + f"只生成一张原生 {delivery_ratio} 干净视频结束帧，严格对应动作时间线 "
                f"K{end_frame['index']}：{end_frame['composition']}；"
                f"人物状态：{end_frame['character_state']}；结束动作：{end_frame['action']}。"
                "必须与本镜起始帧使用完全相同的场景空间母版、地面线身份、人物身份、服装、"
                "道具和光线，只执行合同规定的动作与运镜后状态；不得另造场景。" +
                clean_frame_rules)
            # Legacy callers still read final_image_prompt; it now correctly
            # means the K1 video anchor rather than a mid-action hero frame.
            shot["final_image_prompt"] = shot["final_start_image_prompt"]
            shot["final_video_prompt"] = (
                base + compile_video_direction(shot, motion_timeline) +
                f"镜头时长 {float(shot.get('duration') or 5):g} 秒。"
                f"严格按 K1–K{len(motion_frames)} 的时间点连续演出，逐帧经过规定站位、姿势、"
                f"屏幕方向和摄影机位置；动作和运镜连续执行，衔接方式：{shot.get('transition')}。"
                "运动分镜的箭头、轨迹线、分镜框、K编号、CAM 和人物字母只是不可见控制信息，"
                "严禁出现在视频任何一帧。"
                f"对白：{shot.get('dialogue') or '无'}。人物动作自然，禁止身份漂移和画面闪烁。" +
                ("对白文字只用于演员口型、停顿和表演节奏参考；禁止生成可听的人声、对白或旁白，"
                 "只保留环境声与动作声，正式对白由外部 TTS 轨提供。"
                 if str(shot.get("dialogue") or "").strip() else ""))
            shot["production_ready"] = True
            shot["asset_manifest"] = json.loads(json.dumps(shot_assets, ensure_ascii=False))
        record = self._custom_record(node.node_id)
        if record is not None:
            record["status"] = (f"阶段 4/6 · {len(shots)} 镜空间合同已合成 · "
                                "请确认后执行阶段 5")
            record["pipeline_stage"] = "prompts_ready"
            record["approval_required"] = "final_images"
        self._save_layout_now(); self.storyboardMutated.emit(); self.refresh()

    @staticmethod
    def _video_scene_key(shot):
        for value in shot.get("asset_manifest", []) or []:
            if isinstance(value, dict) and value.get("kind") == "scene":
                return str(value.get("id") or value.get("name") or "")
        return str(shot.get("scene_asset_id") or shot.get("scene_name") or "")

    @staticmethod
    def _video_camera_key(shot):
        """Stable camera identity used to decide whether shots are one take."""
        slot = str(shot.get("camera_slot") or shot.get("camera") or "").strip()
        position = str(shot.get("camera_position") or "").strip()
        raw = slot or position
        return re.sub(r"[\s，。；、,:：/\\]+", "", raw).lower()

    def _shots_share_continuous_take(self, previous, current):
        """Only merge shots when the model can execute them as one camera take."""
        previous = previous or {}
        current = current or {}
        if previous.get("segment_break_after"):
            return False
        previous_scene = self._video_scene_key(previous)
        current_scene = self._video_scene_key(current)
        if previous_scene and current_scene and previous_scene != current_scene:
            return False
        previous_segment = str(previous.get("video_segment") or "").strip()
        current_segment = str(current.get("video_segment") or "").strip()
        if (previous_segment and current_segment and
                previous_segment != current_segment):
            return False
        transition = re.sub(
            r"[\s，。；、,:：/\\]+", "",
            str(previous.get("transition") or "")).lower()
        editorial_terms = (
            "硬切", "直接切", "跳切", "切镜", "切到", "反打", "正反打",
            "动作接切", "动作匹配", "视线匹配", "匹配剪辑", "叠化", "淡入",
            "淡出", "溶解", "擦除", "闪白", "黑场", "cut", "matchcut",
            "dissolve", "fade", "wipe",
        )
        if any(term in transition for term in editorial_terms):
            return False
        camera_before = self._video_camera_key(previous)
        camera_after = self._video_camera_key(current)
        if camera_before and camera_after and camera_before != camera_after:
            return False
        continuous_terms = (
            "无缝", "连续动作", "动作延续", "运镜延续", "跟拍延续", "继续跟拍",
            "承接上一镜", "一镜到底", "不切镜", "不切", "sametake",
            "seamless", "continuous",
        )
        if any(term in transition for term in continuous_terms):
            return True
        # The director's explicit segment ID is authoritative only when the
        # camera identity is also compatible.  This prevents a same-scene hard
        # cut from being hidden inside one Seedance request.
        if previous_segment and previous_segment == current_segment:
            return True
        return bool(camera_before and camera_before == camera_after and not transition)

    def _video_handoff_contract(self, previous, current, across_segments=False):
        """Describe how the ending state of one shot is handed to the next.

        A cut still needs semantic continuity, but it must not inherit the
        previous camera pixels.  A genuinely continuous take split only by a
        provider duration limit is different: the previous rendered tail is
        the authoritative first frame of the next request.
        """
        previous = previous or {}
        current = current or {}
        transition = str(previous.get("transition") or "").strip()
        normalized = transition.lower().replace(" ", "")
        previous_scene = self._video_scene_key(previous)
        current_scene = self._video_scene_key(current)
        scene_changed = bool(
            previous_scene and current_scene and previous_scene != current_scene)
        previous_segment = str(previous.get("video_segment") or "")
        current_segment = str(current.get("video_segment") or "")
        explicit_segment_changed = bool(
            previous_segment and current_segment and
            previous_segment != current_segment)

        continuous_terms = (
            "无缝", "连续动作", "动作延续", "运镜延续", "跟拍延续", "继续跟拍",
            "承接上一镜", "一镜到底", "不切镜", "不切", "sametake",
            "seamless", "continuous",
        )
        match_terms = (
            "动作接切", "动作匹配", "匹配剪辑", "视线匹配", "形状匹配",
            "matchcut", "matchonaction", "正反打", "反打",
        )
        transition_terms = (
            "叠化", "淡入", "淡出", "溶解", "闪白", "黑场", "擦除",
            "dissolve", "fade", "wipe",
        )
        strong_cut_terms = ("硬切", "直接切", "跳切")
        cut_terms = ("切镜", "切到", "cut")

        if (previous.get("segment_break_after") or scene_changed or
                explicit_segment_changed):
            mode = "hard_cut"
        elif any(term in normalized for term in continuous_terms):
            mode = "continuous_tail"
        elif any(term in normalized for term in strong_cut_terms):
            mode = "hard_cut"
        elif any(term in normalized for term in match_terms):
            mode = "match_state"
        elif any(term in normalized for term in transition_terms):
            mode = "transition_state"
        elif any(term in normalized for term in cut_terms):
            mode = "hard_cut"
        elif (across_segments and not scene_changed and
              not explicit_segment_changed and
              self._shots_share_continuous_take(previous, current)):
            # The boundary exists only because of the provider duration limit;
            # preserve the same physical take with a real rendered tail.
            mode = "continuous_tail"
        elif across_segments:
            mode = "hard_cut"
        else:
            mode = "state_match"

        labels = {
            "continuous_tail":"真实尾帧无缝续接",
            "match_state":"匹配剪辑交接",
            "transition_state":"转场状态交接",
            "hard_cut":"独立构图切镜",
            "state_match":"状态连续交接",
        }
        previous_end = self._clean_final_video_text(
            previous.get("frame_end") or previous.get("visual") or "保持上一镜结束状态")
        current_start = self._clean_final_video_text(
            current.get("frame_start") or current.get("visual") or "承接上一镜状态")
        shared_state = (
            "角色身份、服装、发型、道具持握、伤痕/污渍、场景陈设、光线时间、"
            "人物站位、身体朝向、视线、屏幕运动方向和动作进度")
        if mode == "continuous_tail":
            instruction = (
                "不得重置人物或摄影机；从上一段真实尾帧的姿势、位置、速度和运镜惯性"
                "继续，首帧不得重新摆拍。")
        elif mode == "hard_cut":
            instruction = (
                "允许更换景别、构图和机位；不要复制上一镜像素，但切后必须继承叙事状态，"
                "并遵守轴线、视线和屏幕方向。")
        elif mode == "match_state":
            instruction = (
                "使用下一镜定稿构图，在动作/视线匹配点切换；切前与切后的动作相位和屏幕"
                "方向必须吻合。")
        elif mode == "transition_state":
            instruction = (
                "使用下一镜定稿构图完成影像过渡；过渡过程中不得变脸、换装、改变道具或"
                "无故重置人物位置。")
        else:
            instruction = (
                "使用下一镜定稿构图，同时严格继承上一镜结束时的叙事与空间状态。")
        return {
            "mode":mode,
            "label":labels[mode],
            "transition":transition or "未指定",
            "previous_end":previous_end,
            "current_start":current_start,
            "uses_previous_tail":mode == "continuous_tail",
            "prompt":(
                f"{labels[mode]}；衔接说明：{transition or '未指定'}；"
                f"上一镜结束状态：{previous_end}；下一镜开始状态：{current_start}；"
                f"连续字段：{shared_state}。{instruction}"),
        }

    @staticmethod
    def _video_prompt_with_handoff(prompt: str, record: dict):
        handoff = str(record.get("handoff_contract") or "").strip()
        if not handoff:
            return str(prompt or "")
        return f"【跨视频段交接合同】\n{handoff}\n\n{prompt}"

    def _smart_video_segments(self, production_shots, all_shots, mode="smart",
                              provider_name=""):
        """Convert editorial shots into model-sized continuous performance units."""
        indexed = [(all_shots.index(shot), shot) for shot in production_shots]
        if mode == "per_shot":
            return [[value] for value in indexed]

        director_timeline = bool(
            mode == "director_timeline" or
            (mode == "smart" and str(provider_name).lower() == "seedance"))
        max_shots = 8 if director_timeline else (999 if mode == "single_15" else 3)
        segments = []
        current = []
        current_duration = 0.0
        for shot_index, shot in indexed:
            duration = max(0.1, float(shot.get("duration") or 5))
            previous_index, previous = current[-1] if current else (-2, None)
            explicit_current = str(shot.get("video_segment") or "")
            explicit_previous = str((previous or {}).get("video_segment") or "")
            scene_changed = bool(
                previous and self._video_scene_key(previous) and self._video_scene_key(shot) and
                self._video_scene_key(previous) != self._video_scene_key(shot))
            explicit_changed = bool(
                previous and explicit_current and explicit_previous and
                explicit_current != explicit_previous)
            forced_break = bool(previous and previous.get("segment_break_after"))
            non_contiguous = bool(previous and shot_index != previous_index + 1)
            take_changed = bool(
                previous and not self._shots_share_continuous_take(previous, shot))
            capacity_break = bool(
                current and (current_duration + duration > 15.0 or
                             len(current) >= max_shots))
            # A timestamp-aware Seedance request is an edited sequence, not a
            # continuous camera take. Camera changes, hard cuts, reactions and
            # scene-changing cutaways belong inside the same generation unit.
            # In director-timeline mode the model receives one timestamped edit
            # plan.  Planner-generated segment labels are only hints and can
            # drift between otherwise continuous adjacent shots, so they must
            # not fragment the request.  An explicit segment_break_after is the
            # authoritative way to force a new generation unit.
            structural_break = (
                forced_break or non_contiguous or capacity_break or
                (explicit_changed and not director_timeline))
            legacy_break = scene_changed or take_changed
            if current and (structural_break or
                            (legacy_break and not director_timeline)):
                segments.append(current)
                current = []
                current_duration = 0.0
            current.append((shot_index, shot))
            current_duration += duration
        if current:
            segments.append(current)
        return segments

    def _video_segment_prompt(self, segment):
        if len(segment) == 1:
            return self._compact_single_shot_video_prompt(*segment[0])
        total_duration = sum(float(shot.get("duration") or 5)
                             for _index, shot in segment)
        bible = self._video_bible_without_speed_bias(self._clean_final_video_text(
            (self.current_storyboard().get("visual_bible") or {}).get(
                "ai_storyboard") or ""))
        cursor = 0.0
        beats = []
        for shot_index, shot in segment:
            duration = float(shot.get("duration") or 5)
            motion_frames = self._normalize_motion_keyframes(shot)
            motion_line = " → ".join(
                f"K{value['index']}@{value['time_seconds']:g}s "
                f"{self._normal_speed_action_text(value['character_state'])} / "
                f"{self._normal_speed_action_text(value['action'])} / "
                f"{self._clean_final_video_text(value['camera_state'])}"
                for value in motion_frames)
            speed_contract = self._shot_motion_speed_contract(shot, duration)
            start_stamp = f"00:{int(round(cursor)):02d}"
            end_stamp = f"00:{int(round(cursor + duration)):02d}"
            cut_instruction = (
                "从上一镜硬切" if beats and "切" in str(shot.get("transition") or "")
                else "按导演时间点切入" if beats else "开场")
            beats.append(
                f"[{start_stamp}-{end_stamp}] 镜头{shot_index + 1:02d}｜{cut_instruction}｜"
                f"{shot.get('shot_size') or '中景'}｜画面：{self._clean_final_video_text(shot.get('visual') or shot.get('image_prompt'))}｜"
                f"表演与方向：{self._normal_speed_action_text(shot.get('action_line'))}｜"
                f"机位：{self._clean_final_video_text(shot.get('camera_slot'))}｜"
                f"运镜：{self._clean_final_video_text(shot.get('camera_movement') or shot.get('camera_slot'))}｜"
                f"站位：{json.dumps(shot.get('character_positions') or [], ensure_ascii=False)}｜"
                f"运动关键帧：{motion_line}｜"
                f"动作速度：{speed_contract}｜"
                f"对白：{shot.get('dialogue') or '无'}｜"
                f"转场：{self._clean_final_video_text(shot.get('transition') or '连续剪接')}")
            cursor += duration
        first = segment[0][1]
        last = segment[-1][1]
        has_dialogue = any(str(shot.get("dialogue") or "").strip()
                           for _index, shot in segment)
        handoffs = []
        for pair_index in range(1, len(segment)):
            previous_index, previous = segment[pair_index - 1]
            current_index, current = segment[pair_index]
            contract = self._video_handoff_contract(previous, current)
            handoffs.append(
                f"分镜{previous_index + 1:02d}→分镜{current_index + 1:02d}："
                f"{contract['prompt']}")
        handoff_section = (
            "\n【段内镜头交接合同】\n" + "\n".join(handoffs)
            if handoffs else "")
        geometry_section = "\n【不可漂移的空间母版】\n" + "\n".join(
            f"分镜{shot_index + 1:02d}："
            f"{self._clean_final_video_text(shot.get('space_geometry_contract'))}"
            for shot_index, shot in segment)
        endpoint_rule = (
            "首尾输入帧是已经通过一致性检查的同一构图两个时刻；地面标线、墙地交界、"
            "门柱、台阶和家具不得弯曲、复制、消失、换边或改变连接关系。"
            if self._shot_uses_endpoint_pair(last) else
            "只以已批准首帧作为像素锚点，按动作与运镜指令连续演进；不得自行切换成另一"
            "场景、另一机位或另一套家具布局，不得在结尾强行匹配一张独立图片。")
        return (
            "【输出类型】纯净电影成片，只包含场景、角色、道具、自然光影和真实运动。"
            "空间调度信息只转换成人物走位与摄影机运动，不渲染任何图形或文字覆盖层。\n"
            f"生成一个总长约 {total_duration:g} 秒的连续多镜头视频段，内部包含 "
            f"{len(segment)} 个导演分镜。全片设定：{bible}\n"
            f"空间与轴线：{self._clean_final_video_text(first.get('spatial_layout'))}；"
            f"{self._clean_final_video_text(first.get('axis_rule'))}。"
            f"起始状态：{self._clean_final_video_text(first.get('frame_start') or first.get('visual'))}。\n" +
            "\n".join(beats) +
            geometry_section +
            handoff_section +
            f"\n结束状态：{self._clean_final_video_text(last.get('frame_end') or last.get('visual'))}。"
            "严格按上述时间顺序完成内部切镜，保持角色身份、服装、场景结构、屏幕方向、"
            "人物站位和光线连续；不要把多镜头压成一个长镜头，不要新增剧情。"
            + endpoint_rule +
            "人物动作一律按正常现实时间执行；庄重、克制只表示表演幅度，不表示慢动作。"
            "全段保持恒定自然速度，禁止慢动作、漂浮式移动、无故停顿和速度渐变。" +
            "最终输出保持纯净，不含字幕、水印、图形覆盖层或界面元素。" +
            ("对白内容只约束口型、停顿和表演节奏；禁止生成可听人声、对白或旁白，"
             "只生成环境声和动作声，正式人声由外部 TTS 对白轨提供。"
             if has_dialogue else ""))

    @classmethod
    def _video_action_complexity(cls, shot: dict) -> dict:
        """Classify actions that are unsafe for unconstrained K1-only video."""
        source = " ".join(str(shot.get(key) or "") for key in (
            "primary_action", "action_line", "visual", "blocking",
            "frame_start", "frame_end", "generation_risk"))
        categories = []
        patterns = (
            ("道具形态变化", r"打开|合拢|撑开|展开|收拢|折叠|穿上|脱下|解开|扣上|open|close|unfold|fold"),
            ("道具位置转移", r"拿起|放下|取出|掏出|递给|交给|丢下|捡起|移到|带走|pick|place|take out|hand over"),
            ("人物明显位移", r"走|跑|冲|奔|离开|出画|进入|进门|跨过|起身|坐下|站起|walk|run|leave|exit|enter|stand up|sit down"),
            ("身体方向变化", r"转身|回头|转向|俯身|蹲下|抬头|低头|turn|look back|bend|crouch"),
        )
        for label, pattern in patterns:
            if re.search(pattern, source, re.I):
                categories.append(label)
        prop_risk = any(value in categories for value in (
            "道具形态变化", "道具位置转移"))
        entering_or_exiting = bool(re.search(
            r"离开|出画|进入|进门|跨过|leave|exit|enter", source, re.I))
        overloaded = len(categories) >= 2
        reasons = list(categories)
        if overloaded:
            reasons.append("同镜包含多个连续动作阶段")
        return {
            "score":len(categories) + int(prop_risk) + int(entering_or_exiting),
            "categories":categories,
            "reasons":reasons,
            "requires_endpoint_pair":bool(prop_risk or entering_or_exiting or overloaded),
            "overloaded":overloaded,
        }

    @classmethod
    def _apply_video_action_policy(cls, shot: dict) -> dict:
        complexity = cls._video_action_complexity(shot)
        shot["video_action_complexity"] = complexity
        if complexity["requires_endpoint_pair"]:
            # State-changing props, entrances/exits and compound body motion
            # are unsafe to infer from K1 alone. Klast is a hard requirement.
            shot["keyframe_strategy"] = "first_last"
            shot["endpoint_pair_enabled"] = True
            shot["endpoint_pair_required"] = True
            shot["endpoint_pair_forced"] = True
            shot["endpoint_pair_force_reason"] = "、".join(complexity["reasons"])
            shot["endpoint_pair_runtime_mode"] = "first_last_pending"
        return complexity

    def _compact_single_shot_video_prompt(self, shot_index: int,
                                          shot: dict) -> str:
        """Compile only executable motion facts for a single model request."""
        duration = float(shot.get("duration") or 5)
        complexity = dict(shot.get("video_action_complexity") or
                          self._video_action_complexity(shot))
        start = self._clean_final_video_text(
            shot.get("frame_start") or shot.get("action_start") or shot.get("visual"))
        action = self._normal_speed_action_text(
            shot.get("primary_action") or shot.get("action_line") or shot.get("blocking"))
        end = self._clean_final_video_text(
            shot.get("frame_end") or shot.get("action_end") or shot.get("visual"))
        camera = self._clean_final_video_text(
            shot.get("dominant_camera_move") or shot.get("camera_movement") or
            shot.get("camera_slot") or "固定机位")
        invariants = "、".join(str(value).strip() for value in
                              (shot.get("continuity_invariants") or [])
                              if str(value).strip())
        object_contract = ""
        if any(value in complexity.get("categories", []) for value in
               ("道具形态变化", "道具位置转移")):
            object_contract = (
                "\n【单件道具守恒】画面中的每件单数道具始终只有一个实例。"
                "状态变化必须发生在原物体上；原位置随物体移动后必须为空。"
                "严禁复制、分身、残留第二件或用新物体替换原物体。")
        endpoint = (
            "首帧与尾帧都是权威像素锚点；从首帧连续变化并准确稳定在尾帧，"
            "中途不得跳切、瞬移或重置构图。"
            if self._shot_uses_endpoint_pair(shot) else
            "首帧是唯一像素锚点；保持同一场景、人物身份和机位连续演进。")
        dialogue_rule = (
            "\n对白只约束口型与节奏；禁止生成可听人声，正式人声由外部 TTS 提供。"
            if str(shot.get("dialogue") or "").strip() else "")
        return (
            "【任务】生成一段纯净电影成片、连续的电影镜头，不含字幕、水印、分镜线、箭头或界面。\n"
            f"生成一个总长约 {duration:g} 秒的连续视频段，内部包含 1 个导演分镜。\n"
            f"【时长】{duration:g}秒。人物动作按正常现实时间完成。\n"
            f"【开始状态】{start}。\n"
            f"【唯一主要表演】{action}。\n"
            f"【结束状态】{end}。\n"
            f"【摄影机唯一运动】{camera}。禁止叠加第二种运镜。\n"
            f"【锚点规则】{endpoint}"
            f"{object_contract}\n"
            f"【全程不变】{invariants or '人物身份、服装、场景结构、光线与固定设施数量'}。\n"
            f"【动作速度】{self._shot_motion_speed_contract(shot, duration)}。\n"
            "【稳定性】禁止物体复制、肢体突变、身份漂移、帧间闪烁、画面跳变和无故停顿。"
            f"{dialogue_rule}")

    @staticmethod
    def _video_request_duration(total_duration: float) -> int:
        """Preserve short-shot timing instead of padding 3 s motion to 4 s."""
        return max(2, min(15, int(round(float(total_duration or 0)))))

    @staticmethod
    def _video_bible_without_speed_bias(value) -> str:
        """Keep dramatic tone while preventing it from slowing body mechanics."""
        text = str(value or "")
        replacement = "庄重、内敛；表演幅度克制，人物动作按正常现实时间完成"
        text = re.sub(
            r"('pace'\s*:\s*)'[^']*'", lambda match: match.group(1) + repr(replacement),
            text, flags=re.I)
        text = re.sub(
            r'("pace"\s*:\s*)"[^"]*"',
            lambda match: match.group(1) + json.dumps(replacement, ensure_ascii=False),
            text, flags=re.I)
        return text

    @classmethod
    def _normal_speed_action_text(cls, value) -> str:
        text = cls._clean_final_video_text(value)
        return re.sub(r"缓慢地?|慢慢地?", "以正常现实速度", text)

    @classmethod
    def _shot_motion_speed_contract(cls, shot: dict, duration: float) -> str:
        """Compile physical pace references instead of mood adjectives."""
        duration = max(0.5, float(duration or 0.5))
        source = " ".join(str(shot.get(key) or "") for key in (
            "visual", "action_line", "blocking", "frame_start", "frame_end"))
        cues = []
        if re.search(r"走|步|跑|冲|奔|walk|run|jog", source, re.I):
            cues.append("行走约每0.55秒一步；跑动约每0.35秒一步，脚掌落地清楚，不滑行")
        if re.search(r"转身|回头|抬头|低头|转向|turn|look", source, re.I):
            cues.append("转身或头部转向在0.6–1.0秒内完成")
        if re.search(r"拿|取|放|递|握|插|按|拉|推|手|pick|place|grab|hand", source, re.I):
            cues.append("单次手部取放或接触动作在0.8–1.4秒内完成，接触点明确")
        if str(shot.get("dialogue") or "").strip():
            cues.append("对白按正常会话语速表演，口型与身体动作不停滞")
        if re.search(r"翅|翼|展开|收拢|wing|unfold", source, re.I):
            cues.append("单次展开或收拢在1.5–2.5秒内完成，随后稳定停住")
        finish = max(0.5, round(duration - min(0.6, duration * 0.15), 1))
        cues.append(f"主要动作最迟在第{finish:g}秒完成，余下时间只保持清晰结束姿势")
        return "；".join(cues) + "；恒定自然速度，禁止慢动作、速度渐变和漂浮感"

    @staticmethod
    def _clean_final_video_text(value) -> str:
        """Remove drawing-board render instructions from final-video prose.

        Directional meaning stays in blocking/keyframe text, but explicit requests
        to draw arrows, labels or camera diagrams must never reach a video model.
        """
        text = str(value or "").strip()
        if not text:
            return ""
        overlay_terms = (
            "手绘标注", "手绘调度", "手绘攻击箭头", "手绘线", "移动粗实线箭头",
            "攻击方向箭头", "视线虚线箭头", "摄影机运动双线箭头", "运动箭头",
            "闪避箭头", "跌落粗箭头", "画面标注", "画面叠加", "可叠加",
            "起点终点圆点", "CAM摄影机", "CAM 摄影机", "空间线框", "平面线框",
            "分镜框", "K编号", "A/B字母标记", "A/B/C 标记",
        )
        parts = re.split(r"(?<=[。！？；\n])", text)
        cleaned = [part for part in parts
                   if not any(term in part for term in overlay_terms)]
        return "".join(cleaned).strip(" ；。\n")

    def _segment_for_generator(self, record: dict):
        shot_ids = [str(value) for value in
                    (record.get("shot_ids") or [record.get("shot_id")]) if value]
        wanted = set(shot_ids)
        return [(index, shot) for index, shot in enumerate(
            self.current_storyboard().get("shots", []))
                if str(shot.get("id") or "") in wanted]

    def _refresh_video_generator_contract(self, record: dict):
        """Recompile a clean prompt and pin a clean authoritative first frame."""
        if str(record.get("generator_kind") or "") != "video":
            return
        segment = self._segment_for_generator(record)
        if segment:
            record["content"] = self._video_prompt_with_handoff(
                self._video_segment_prompt(segment), record)
            current_title = str(record.get("title") or "")
            output_stem = Path(str(record.get("path") or "")).stem
            if (current_title == output_stem or
                    re.match(r"^(?:seedance|veo|kling)[_-]", current_title, re.I)):
                start = segment[0][0] + 1; end = segment[-1][0] + 1
                record["title"] = (f"连续段 · 镜头 {start:02d}–{end:02d}"
                                   if start != end else f"连续段 · 镜头 {start:02d}")
            first_shot = segment[0][1]
            last_shot = segment[-1][1]
            manual_anchor = str(record.get("first_frame") or "")
            planned_anchor = str(
                record.get("planned_first_frame") or
                first_shot.get("selected_image_asset") or "")
            record["planned_first_frame"] = planned_anchor
            continuity_anchor = str(record.get("continuity_first_frame") or "")
            if (record.get("first_frame_override") and
                    os.path.exists(manual_anchor)):
                anchor = manual_anchor
                record["first_frame_source"] = "manual_override"
            elif str(record.get("handoff_mode") or "") == "continuous_tail":
                # A continuous segment may only start from the approved real
                # tail of the previous rendered take.  Falling back to the
                # planned still creates the exact visible jump this contract
                # is meant to prevent.
                anchor = continuity_anchor if os.path.exists(continuity_anchor) else ""
                record["first_frame_source"] = (
                    "previous_video_tail" if anchor else "awaiting_previous_video_tail")
            else:
                anchor = planned_anchor
                record["first_frame_source"] = "planned_still"
            if anchor and os.path.exists(anchor) and not self._path_has_motion_board_lineage(
                    first_shot, anchor):
                record["first_frame"] = anchor
            else:
                record["first_frame"] = ""
            manual_last = str(record.get("last_frame") or "")
            planned_last = str(
                record.get("planned_last_frame") or
                (last_shot.get("selected_end_image_asset")
                 if self._shot_uses_endpoint_pair(last_shot) else "") or "")
            if not self._shot_uses_endpoint_pair(last_shot):
                planned_last = ""
            record["planned_last_frame"] = planned_last
            if record.get("last_frame_override") and os.path.exists(manual_last):
                record["last_frame"] = manual_last
                record["last_frame_source"] = "manual_override"
            elif planned_last and os.path.exists(planned_last) and not self._path_has_motion_board_lineage(
                    last_shot, planned_last):
                record["last_frame"] = planned_last
                record["last_frame_source"] = "planned_end_still"
            else:
                record["last_frame"] = ""
                record["last_frame_source"] = ""
            record["scene_master_path"] = str(first_shot.get("scene_master_path") or "")
            record["space_geometry_contract"] = str(
                first_shot.get("space_geometry_contract") or "")
        record["prompt_contract"] = "clean_endpoint_video_v3"

    def _prepare_video_handoff_anchor(self, record: dict,
                                      completed_node_id: str = ""):
        """Resolve the previous rendered tail before a continuous segment runs."""
        if (str(record.get("generator_kind") or "") != "video" or
                str(record.get("handoff_mode") or "") != "continuous_tail" or
                record.get("first_frame_override")):
            return False
        previous_id = str(
            record.get("previous_segment_node_id") or completed_node_id or "")
        previous = self._custom_record(previous_id) if previous_id else None
        if previous is None:
            record["continuity_first_frame"] = ""
            record["first_frame"] = ""
            record["handoff_blocked"] = True
            record["handoff_status"] = "上一段不存在 · 已暂停连续续接"
            return False
        previous_qc = (previous.get("clip_qc")
                       if isinstance(previous.get("clip_qc"), dict) else {})
        if (not previous.get("adopted") or
                not previous.get("handoff_approved") or
                not (previous_qc.get("passed") or
                     previous_qc.get("risk_accepted"))):
            record["continuity_first_frame"] = ""
            record["first_frame"] = ""
            record["handoff_blocked"] = True
            record["handoff_status"] = "上一段尚未定稿并通过审片 · 已暂停"
            return False
        tail = str(previous.get("video_tail_frame") or "")
        if not tail or not os.path.exists(tail):
            path = str(previous.get("path") or "")
            frames = (self._extract_video_review_frames(path)
                      if path and os.path.exists(path) else [])
            if frames:
                previous["video_review_frames"] = frames
                previous["video_tail_frame"] = frames[-1]
                tail = frames[-1]
        if tail and os.path.exists(tail):
            record["continuity_first_frame"] = tail
            record["first_frame"] = tail
            record["first_frame_source"] = "previous_video_tail"
            record["handoff_blocked"] = False
            record["handoff_status"] = "已锁定上一段真实尾帧"
            return True
        record["continuity_first_frame"] = ""
        record["first_frame"] = ""
        record["handoff_blocked"] = True
        record["handoff_status"] = "上一段真实尾帧不可用 · 已暂停，禁止回退定稿首帧"
        return False

    def create_canvas_generator_group(self, node, kind="image"):
        shots = self.current_storyboard().get("shots", [])
        settings = self._custom_record(node.node_id) or node.payload
        scope = str(settings.get("production_scope") or "all")
        if scope == "selected":
            production_shots = [shot for shot in shots if shot.get("production_selected")]
        elif scope == "missing":
            if kind == "image":
                production_shots = [shot for shot in shots
                                    if not self._shot_endpoint_pair_ready(shot)]
            else:
                production_shots = [shot for shot in shots
                                    if not shot.get("selected_video_asset")]
        else:
            production_shots = list(shots)
        if not production_shots:
            QMessageBox.information(self, "创建生成器组", "当前生产范围内没有需要处理的镜头。")
            return
        if kind == "video":
            for shot in production_shots:
                if (endpoint_pair_requested(shot) and
                        shot.get("endpoint_pair_required") and
                        not self._shot_uses_endpoint_pair(shot)):
                    if shot.get("endpoint_pair_forced"):
                        QMessageBox.information(
                            self, "复杂动作需要首尾帧",
                            f"镜头 {int(shot.get('number') or 0):02d} 包含"
                            f"{shot.get('endpoint_pair_force_reason') or '高风险状态变化'}，"
                            "结束帧尚未生成或未通过一致性检查，已阻止提交视频。\n"
                            "请先重新生成并采用合格的结束帧。")
                        return
                    shot["endpoint_pair_required"] = False
                    shot["endpoint_pair_runtime_mode"] = "first_frame_fallback"
                    shot["endpoint_pair_fallback_reason"] = (
                        "结束帧缺失或未通过一致性检查，已自动改用单首帧")
        if kind == "video" and self._show_video_anchor_issues(production_shots):
            return
        if any(not shot.get("production_ready") for shot in production_shots):
            QMessageBox.information(self, "创建生成器组", "请先完成阶段 3：合成最终提示词。")
            return
        import uuid
        group_id = f"custom:{uuid.uuid4().hex[:12]}"
        generator_ids = []
        source_pos = node.pos()
        custom_values = self._positions().setdefault("__custom_nodes__", [])
        edges = self._positions().setdefault("__workflow_edges__", [])
        ratio = str(settings.get("production_ratio") or "16:9")
        candidate_key = "video_candidate_count" if kind == "video" else "candidate_count"
        candidate_count = max(1, min(4, int(settings.get(candidate_key) or 2)))
        provider_name = str(settings.get(
            "image_provider" if kind == "image" else "video_provider") or
            self._storyboard_model_lock(
                str(node.node_id),
                "image_provider" if kind == "image" else "video_provider") or "")
        if kind == "image" and not provider_name:
            provider_name = self._locked_storyboard_image_provider(
                "text_to_image", str(node.node_id)).name
        video_mode = str(settings.get("video_generation_mode") or "smart")
        effective_video_mode = (
            "director_timeline"
            if kind == "video" and provider_name.lower() == "seedance" and
            video_mode == "smart" else video_mode)
        # A director-timeline node is already a complete edited sequence.
        # Generating the ordinary two default candidates doubles both cost and
        # the number of clips shown to the user, so make one coherent take. A
        # user can still regenerate that node deliberately when another take is
        # wanted.
        if kind == "video" and effective_video_mode == "director_timeline":
            candidate_count = 1
        units = ([[ (shots.index(shot), shot) ] for shot in production_shots]
                 if kind == "image" else
                 self._smart_video_segments(
                     production_shots, shots, effective_video_mode, provider_name))
        work_units = (
            [(unit, frame_role) for unit in units
             for frame_role in (("start", "end")
                                if endpoint_pair_requested(unit[0][1]) else ("start",))]
            if kind == "image" else [(unit, "") for unit in units])
        if kind == "image":
            for shot in production_shots:
                required = endpoint_pair_requested(shot)
                shot["endpoint_pair_required"] = required
                shot["endpoint_pair_runtime_mode"] = (
                    "first_last_pending" if required else "first_frame")
                if not required:
                    shot.pop("endpoint_pair_qc", None)
        for layout_index, (unit, frame_role) in enumerate(work_units):
            shot_index, shot = unit[0]
            last_shot_index, last_shot = unit[-1]
            unit_shots = [value for _index, value in unit]
            shot_ids = [str(value.get("id") or "") for value in unit_shots]
            generator_id = f"custom:{uuid.uuid4().hex[:12]}"
            previous_generator_id = generator_ids[-1] if generator_ids else ""
            generator_ids.append(generator_id)
            handoff = None
            if kind == "video" and layout_index > 0:
                previous_unit = units[layout_index - 1]
                handoff = self._video_handoff_contract(
                    previous_unit[-1][1], unit[0][1], across_segments=True)
            node_refs = []
            typed_node_refs = []
            timeline_images = []
            timeline_cursor = 0.0
            # Motion-board pixels must never enter final-image generation.
            # Its complete keyframe contract is already embedded in the text
            # prompt; sending the sheet as image_edit input leaks arrows, CAM
            # labels and panel borders into the final still and then the video.
            for unit_shot in unit_shots:
                shot_ref = str(unit_shot.get("selected_image_asset") or "")
                if kind == "video" and shot_ref:
                    node_refs.append(shot_ref)
                    shot_duration = float(unit_shot.get("duration") or 5)
                    shot_number = int(unit_shot.get("number") or
                                      unit_shot.get("shot_number") or
                                      len(timeline_images) + 1)
                    instruction = (
                        str(unit_shot.get("final_video_prompt") or
                            unit_shot.get("action") or
                            unit_shot.get("blocking") or
                            unit_shot.get("visual") or "").strip())
                    timeline_images.append({
                        "path":shot_ref,
                        "start":timeline_cursor,
                        "end":timeline_cursor + shot_duration,
                        "role":"composition",
                        "shot_id":str(unit_shot.get("id") or ""),
                        "shot_number":shot_number,
                        "instruction":instruction,
                    })
                    typed_node_refs.append({
                        "path":shot_ref, "role":"composition", "required":True,
                        "order":len(timeline_images) - 1,
                        "label":(
                            f"镜头 {shot_number:02d} 定稿构图 · "
                            f"{timeline_cursor:g}–{timeline_cursor + shot_duration:g} 秒"),
                    })
                elif kind == "image" and frame_role == "end" and shot_ref:
                    node_refs.append(shot_ref)
                    typed_node_refs.append({
                        "path":shot_ref, "role":"composition", "required":True,
                        "label":"本镜已定稿起始帧：保持空间母版与身份，只推进动作",
                    })
                if kind == "video":
                    timeline_cursor += float(unit_shot.get("duration") or 5)
                manifests = [value for value in unit_shot.get("asset_manifest", [])
                             if isinstance(value, dict)]
                for asset_kind in ("scene", "character", "element"):
                    for value in manifests:
                        if str(value.get("kind") or "") != asset_kind:
                            continue
                        # Final K1 composition is not a character-sheet collage.
                        # One authoritative image per matched asset is enough;
                        # feeding portrait/close-up/expression/turnaround together
                        # encourages the image model to rebuild the location.
                        asset_paths = [str(value.get("path") or "")]
                        for asset_path in asset_paths:
                            if not asset_path:
                                continue
                            node_refs.append(asset_path)
                            typed_node_refs.append({
                                "path":asset_path, "role":asset_kind,
                                "required":True,
                                "label":f"{asset_kind} 权威资产参考",
                            })
            node_refs = [value for value in node_refs
                         if value and os.path.exists(value)]
            continuity_ref = str(shot.get("continuity_reference") or "")
            if continuity_ref and os.path.exists(continuity_ref):
                if kind == "image":
                    node_refs.append(continuity_ref)
                    typed_node_refs.append({
                        "path":continuity_ref, "role":"reference", "required":False,
                        "label":"上一镜干净定稿：只参考连续性，不复制构图",
                    })
                else:
                    node_refs.append(continuity_ref)
            node_refs = list(dict.fromkeys(node_refs))[:9]
            if kind == "image" and frame_role == "start":
                stage_capture_path = str(shot.get("scene_stage_capture") or "")
                scene_view_path = str(stage_capture_path or shot.get("scene_view_path") or
                                      shot.get("scene_master_path") or "")
                if scene_view_path and os.path.exists(scene_view_path):
                    node_refs = [scene_view_path] + [
                        value for value in node_refs if value != scene_view_path]
                    typed_node_refs.append({
                        "path":scene_view_path,
                        "role":"composition" if stage_capture_path else "scene",
                        "required":True,
                        "label":("3D 导演台摄影机构图：严格保持人物站位、左右关系与遮挡"
                                 if stage_capture_path else "绑定场景权威视角"),
                    })
            typed_by_path = {}
            for value in typed_node_refs:
                path = str(value.get("path") or "")
                if path and path in node_refs and path not in typed_by_path:
                    typed_by_path[path] = value
            typed_node_refs = list(typed_by_path.values())
            total_duration = sum(float(value.get("duration") or 5)
                                 for value in unit_shots)
            request_duration = (self._video_request_duration(total_duration)
                                if kind == "video" else float(shot.get("duration") or 5))
            title = ((f"镜头 {shot_index + 1:02d} · "
                      f"{'起始帧' if frame_role == 'start' else '结束帧'}生成器")
                     if kind == "image" else
                     (f"连续段 {layout_index + 1:02d} · 镜头 {shot_index + 1:02d}–"
                      f"{last_shot_index + 1:02d}" if len(unit) > 1 else
                      f"连续段 {layout_index + 1:02d} · 镜头 {shot_index + 1:02d}"))
            planned_first_frame = (
                str(shot.get("selected_image_asset") or "")
                if kind == "video" else "")
            planned_last_frame = (
                str(last_shot.get("selected_end_image_asset") or "")
                if kind == "video" and self._shot_uses_endpoint_pair(last_shot) else "")
            video_prompt = self._video_segment_prompt(unit) if kind == "video" else ""
            handoff_contract = str((handoff or {}).get("prompt") or "")
            if handoff_contract:
                video_prompt = self._video_prompt_with_handoff(
                    video_prompt, {"handoff_contract":handoff_contract})
            image_prompt = str(shot.get(
                "final_end_image_prompt" if frame_role == "end" else
                "final_start_image_prompt") or shot.get("final_image_prompt") or "")
            if kind == "image" and frame_role == "start":
                stage_capture_path = str(shot.get("scene_stage_capture") or "")
                image_prompt = (
                    "【起始帧场景一致性合同】以场景母版作为空间拓扑、材质与固定设备权威，"
                    "但必须按照本镜景别、焦段和摄影机位置重新构图，不得把人物或局部物体"
                    "以矩形贴片方式粘在母版上。"
                    "保持房间尺寸、墙地边界、门窗、柜台、洗衣机及全部固定设备的数量、位置、"
                    "尺寸、朝向、排列顺序和遮挡关系；不得移动、复制、删除设备，不得用相似但"
                    "不同的房间替换。角色参考只约束人物身份和服装，道具参考只约束道具外观，"
                    "二者都无权改变场景布局。只按镜头合同加入人物、姿态与动作起点。\n\n" +
                    (("【3D导演台构图控制】3D快照只控制人物站位、摄影机透视、画面占比、"
                      "左右关系和遮挡顺序；其中的简模、方块、网格和线框都不是最终美术，"
                      "不得原样渲染。最终人物身份与服装服从角色资产，环境材质与固定设备"
                      "外观服从场景资产。\n\n") if stage_capture_path else "") +
                    image_prompt)
            custom_values.append({
                "id":generator_id, "type":"image_node" if kind == "image" else "video_node",
                "title":title,
                "content":image_prompt if kind == "image" else video_prompt,
                "path":"", "references":node_refs,
                # The first selected final still is the authoritative video anchor.
                # Do not rediscover it through the shot edge: a shot preview may
                # still point at a hand-drawn motion board.
                "first_frame":planned_first_frame,
                "planned_first_frame":planned_first_frame,
                "last_frame":planned_last_frame,
                "planned_last_frame":planned_last_frame,
                "continuity_first_frame":"",
                "first_frame_source":"planned_still" if kind == "video" else "",
                "last_frame_source":"planned_end_still" if kind == "video" and planned_last_frame else "",
                "reference_assets":typed_node_refs,
                "timeline_images":timeline_images if kind == "video" else [],
                "auto_image_timeline":bool(
                    kind == "video" and effective_video_mode == "director_timeline"),
                "motion_board_path":str(shot.get("motion_board_path") or ""),
                "motion_keyframes":json.loads(json.dumps(
                    shot.get("motion_keyframes") or [], ensure_ascii=False)),
                "motion_hero_frame":int(shot.get("motion_hero_frame") or 1),
                "ratio":ratio, "shot_id":shot.get("id"),
                "shot_ids":shot_ids, "shot_range":[shot_index + 1, last_shot_index + 1],
                "frame_role":frame_role,
                "spatial_qc_mode":"pixel_lock" if frame_role == "end" else "recompose",
                "generator_kind":kind, "provider_name":provider_name,
                "candidate_count":candidate_count,
                "duration":request_duration, "timeline_duration":total_duration,
                "video_generation_mode":effective_video_mode if kind == "video" else "per_shot",
                "prompt_contract":"clean_endpoint_video_v3" if kind == "video" else "",
                "scene_master_path":str(shot.get("scene_master_path") or ""),
                "scene_view_id":str(shot.get("scene_view_id") or "master"),
                "scene_view_path":str(shot.get("scene_view_path") or ""),
                "scene_stage_capture":str(shot.get("scene_stage_capture") or ""),
                "scene_stage_id":str(shot.get("scene_stage_id") or ""),
                "camera_id":str(shot.get("camera_id") or ""),
                "camera_object":json.loads(json.dumps(
                    shot.get("camera_object") or {}, ensure_ascii=False)),
                "scene_proxy":json.loads(json.dumps(
                    shot.get("scene_proxy") or {}, ensure_ascii=False)),
                "editable_bbox_xy":json.loads(json.dumps(
                    shot.get("editable_bbox_xy") or [0.15, 0.12, 0.7, 0.78],
                    ensure_ascii=False)),
                "space_geometry_contract":str(shot.get("space_geometry_contract") or ""),
                "status":((f"待执行 · {handoff['label']}" if handoff else "待执行")
                          if kind == "video" else "待执行"),
                "continuity_source_shot_id":shot.get("continuity_source_shot_id") or "",
                "previous_segment_node_id":previous_generator_id if kind == "video" else "",
                "handoff_mode":str((handoff or {}).get("mode") or
                                   ("origin" if kind == "video" else "")),
                "handoff_label":str((handoff or {}).get("label") or "首段起点"),
                "handoff_contract":handoff_contract,
                "handoff_status":(
                    "等待上一段真实尾帧" if (handoff or {}).get("uses_previous_tail")
                    else ("使用本段定稿首帧" if kind == "video" else "")),
            })
            columns = 3
            image_rows = max(1, (len(work_units) + columns - 1) // columns)
            kind_offset = (0.0 if kind == "image" else image_rows * 560.0 + 680.0)
            self._positions()[generator_id] = [
                round(source_pos.x() + 1120 + (layout_index % columns) * 660, 2),
                round(source_pos.y() + 720 + kind_offset +
                      (layout_index // columns) * 560, 2)]
            for unit_shot in unit_shots:
                edges.append({"source":f"shot:{unit_shot.get('id')}",
                              "target":generator_id,
                              "type":(f"{kind}:{frame_role}" if frame_role else kind)})
            if kind == "video" and previous_generator_id and handoff:
                edges.append({
                    "source":previous_generator_id, "target":generator_id,
                    "type":f"handoff:{handoff['mode']}",
                    "label":handoff["label"],
                })
        custom_values.append({
            "id":group_id, "type":"workflow_group",
            "title":f"{'图片' if kind == 'image' else '视频'}生成器组",
            "content":((f"{len(production_shots)} 镜 · "
                        f"{sum(endpoint_pair_requested(value) for value in production_shots)} 镜启用首尾帧 · "
                        f"{len(generator_ids)} 个图片节点" if kind == "image" else
                        f"{len(generator_ids)} 个连续段 · {len(production_shots)} 个分镜") +
                       f" · {ratio} · {candidate_count} 候选 · {provider_name or '自动模型'}"),
            "group_nodes":generator_ids, "generator_kind":kind, "status":"待检查并执行",
            "candidate_count":candidate_count,
            "video_generation_mode":effective_video_mode if kind == "video" else "per_shot",
            "source_node_id":node.node_id,
        })
        batch_id = f"batch:{uuid.uuid4().hex[:12]}"
        estimated_units = (len(generator_ids) * candidate_count if kind == "image" else
                           sum(float(shot.get("duration") or 5)
                               for shot in production_shots) * candidate_count)
        self._positions().setdefault("__production_batches__", []).append({
            "id":batch_id, "group_id":group_id, "source_node_id":node.node_id,
            "kind":kind, "scope":scope, "provider":provider_name,
            "node_ids":generator_ids, "status":"ready", "completed":0, "failed":0,
            "estimated_units":estimated_units,
            "estimate_label":(f"{int(estimated_units)} 张图片调用" if kind == "image" else
                              f"{estimated_units:g} 视频秒"),
        })
        estimate_label = (f"{int(estimated_units)} 张图片调用" if kind == "image" else
                          f"{estimated_units:g} 视频秒")
        custom_values[-1]["content"] += f" · 预计 {estimate_label}"
        group_kind_offset = (0.0 if kind == "image" else
                             max(1, (len(production_shots) + 2) // 3) * 560.0 + 680.0)
        self._positions()[group_id] = [round(source_pos.x() + 560, 2),
                                      round(source_pos.y() + 720 + group_kind_offset, 2)]
        for generator_id in generator_ids:
            edges.append({"source":group_id, "target":generator_id, "type":"group"})
        record = self._custom_record(node.node_id)
        if record is not None:
            record["status"] = ("阶段 5/6 · 定稿图片生成器已创建 · 请检查后执行"
                                if kind == "image" else
                                "阶段 6/6 · 视频生成器组已创建")
            record["pipeline_stage"] = "generators_ready"
            record["approval_required"] = ("approved_images" if kind == "image" else "")
        self._save_layout_now(); self.refresh(); self.focus_node(group_id)

    def create_and_execute_image_group(self, node):
        """Manual stage 5: create the image group if needed and actually run it."""
        source_id = str(node.node_id)
        group = self._latest_production_group(source_id, "image")
        if group is None:
            self.create_canvas_generator_group(node, "image")
            group = self._latest_production_group(source_id, "image")
        group_id = str((group or {}).get("id") or "")
        if not group_id or group_id not in self._nodes:
            source = self._custom_record(source_id)
            if source is not None:
                source["status"] = "第 5 步启动失败 · 未能创建图片生成器组"
            self._save_layout_now(); self._update_production_continue_button()
            return False
        source = self._custom_record(source_id)
        if source is not None:
            source["pipeline_stage"] = "images_generating"
            source["status"] = "第 5 步 · 定稿图片候选生成中"
        self._save_layout_now(); self._update_production_continue_button()
        QTimer.singleShot(
            0, lambda gid=group_id: self._execute_workflow_group_by_id(gid))
        return True

    def create_and_execute_video_group(self, node):
        """以每镜已采用图片为首帧，创建并立即执行对应的视频生成器组。"""
        # Creating a generator group rebuilds the scene.  Keep plain data only:
        # the incoming QGraphicsItem is deleted by refresh() and must never be
        # dereferenced afterwards.
        source_id = str(node.node_id)
        shots = self.current_storyboard().get("shots", [])
        settings = self._custom_record(source_id) or node.payload
        scope = str(settings.get("production_scope") or "all")
        if scope == "selected":
            targets = [shot for shot in shots if shot.get("production_selected")]
        elif scope == "missing":
            targets = [shot for shot in shots if not shot.get("selected_video_asset")]
        else:
            targets = list(shots)
        if self._show_video_anchor_issues(targets):
            return
        if not targets:
            QMessageBox.information(self, "生成定稿视频", "当前范围内没有需要生成的视频镜头。")
            return
        self.create_canvas_generator_group(node, "video")
        group_record = next((value for value in reversed(
            self._positions().get("__custom_nodes__", []))
            if isinstance(value, dict) and value.get("type") == "workflow_group" and
            value.get("source_node_id") == source_id and
            value.get("generator_kind") == "video"), None)
        if group_record and group_record.get("id") in self._nodes:
            group_id = str(group_record["id"])
            source = self._custom_record(source_id)
            if source is not None:
                source["status"] = "定稿图片已送入视频模型 · 视频生成中"
                source["pipeline_stage"] = "video_generating"
            self._save_layout_now()
            # Let scene.clear()/refresh() finish destroying the previous scene
            # items before providers and task callbacks begin touching the UI.
            QTimer.singleShot(
                0, lambda gid=group_id: self._execute_workflow_group_by_id(gid))

    def create_and_execute_audio_group(self, node):
        """Manual stage 7: create TTS nodes and run them in the same click."""
        source_id = str(node.node_id)
        dialogue = any(str(shot.get("dialogue") or "").strip()
                       for shot in self.current_storyboard().get("shots", []))
        providers = get_ai_manager().registry.by_capability("text_to_speech")
        source = self._custom_record(source_id)
        if not dialogue or not providers:
            if source is not None:
                source["pipeline_stage"] = "production_ready"
                source["status"] = ("视频生产完成 · 无对白任务" if not dialogue else
                                    "视频生产完成 · 配音模型未配置，可稍后补做")
                source["auto_run_enabled"] = False
            self._save_layout_now(); self.refresh()
            return True
        group = self._latest_production_group(source_id, "audio")
        if group is None:
            self.create_dialogue_audio_group(node)
            group = self._latest_production_group(source_id, "audio")
        group_id = str((group or {}).get("id") or "")
        if not group_id or group_id not in self._nodes:
            source = self._custom_record(source_id)
            if source is not None:
                source["status"] = "第 7 步启动失败 · 未能创建对白音频组"
            self._save_layout_now(); self._update_production_continue_button()
            return False
        source = self._custom_record(source_id)
        if source is not None:
            source["pipeline_stage"] = "audio_generating"
            source["status"] = "第 7 步 · 对白音频生成中"
        self._save_layout_now(); self._update_production_continue_button()
        QTimer.singleShot(
            0, lambda gid=group_id: self._execute_workflow_group_by_id(gid))
        return True

    def _execute_workflow_group_by_id(self, group_id):
        """Resolve a fresh graphics item and safely start its workflow."""
        group_node = self._nodes.get(str(group_id))
        if group_node is None:
            return
        try:
            self.execute_workflow_group(group_node)
        except Exception as error:
            record = self._custom_record(str(group_id))
            if record is not None:
                record["status"] = f"启动失败 · {error}"
            self._save_layout_now()
            QMessageBox.warning(self, "生成任务启动失败", str(error))

    def create_dialogue_audio_group(self, node):
        source_id = str(node.node_id)
        shots = [shot for shot in self.current_storyboard().get("shots", [])
                 if str(shot.get("dialogue") or "").strip()]
        if not shots:
            QMessageBox.information(self, "对白音频", "当前故事板没有需要配音的对白。")
            return
        import uuid
        values = self._positions().setdefault("__custom_nodes__", [])
        edges = self._positions().setdefault("__workflow_edges__", [])
        node_ids = []
        source_pos = node.pos()
        related_group_ids = [str(value.get("id") or "") for value in values
                             if isinstance(value, dict) and
                             str(value.get("source_node_id") or "") == source_id and
                             value.get("type") == "workflow_group"]
        related_y = [float(self._positions().get(value, [0, source_pos.y()])[1])
                     for value in related_group_ids]
        audio_y = max([source_pos.y() + 900.0] + related_y) + 720.0
        providers = get_ai_manager().registry.by_capability("text_to_speech")
        provider_name = providers[0].name if providers else "edge_tts"
        for index, shot in enumerate(shots):
            node_id = f"custom:{uuid.uuid4().hex[:12]}"; node_ids.append(node_id)
            values.append({"id":node_id, "type":"audio_node",
                           "title":f"镜头 {int(shot.get('number') or index + 1):02d} · 对白",
                           "content":str(shot.get("dialogue") or ""), "path":"",
                           "provider_name":provider_name,
                           "voice":("zh-CN-XiaoxiaoNeural"
                                    if provider_name == "edge_tts" else ""),
                           "speed":1.0,
                           "shot_id":shot.get("id"), "generator_kind":"audio",
                           "status":"请选择音色并检查停顿"})
            self._positions()[node_id] = [round(source_pos.x() + 620 + index * 380, 2),
                                         round(audio_y, 2)]
            edges.append({"source":f"shot:{shot.get('id')}", "target":node_id, "type":"dialogue"})
        group_id = f"custom:{uuid.uuid4().hex[:12]}"
        values.append({"id":group_id, "type":"workflow_group", "title":"对白音频组",
                       "content":f"{len(node_ids)} 条对白 · 可逐节点选择音色、插入停顿和语气词",
                       "group_nodes":node_ids, "generator_kind":"audio", "status":"待检查并执行",
                       "source_node_id":source_id})
        self._positions()[group_id] = [round(source_pos.x(), 2), round(audio_y, 2)]
        for node_id in node_ids:
            edges.append({"source":group_id, "target":node_id, "type":"group"})
        source = self._custom_record(source_id)
        if source is not None:
            source["pipeline_stage"] = "audio_generators_ready"
            source["status"] = "对白音频节点已准备 · 即将自动生成"
        self._save_layout_now(); self.refresh(); self.focus_node(group_id)
        return group_id

    def production_readiness_report(self):
        shots = self.current_storyboard().get("shots", [])
        duration = sum(float(shot.get("duration") or 0) for shot in shots)
        report = evaluate_readiness(
            "delivery", self.current_storyboard(), self._production_skill_records())
        issues = [value.message for value in report.blockers]
        missing_images = [shot.get("number") for shot in shots
                          if not shot.get("selected_image_asset")]
        rejected = [shot.get("number") for shot in shots if shot.get("quality_passed") is False]
        dialogue_shots = [shot for shot in shots if str(shot.get("dialogue") or "").strip()]
        for label, values in (("未定稿图片", missing_images), ("视觉审片退回", rejected)):
            if values:
                issues.append(f"{label}：" + "、".join(str(value) for value in values[:20]))
        issues = list(dict.fromkeys(issues))
        summary = (f"镜头 {len(shots)} 个 · 总时长 {duration:g}s · "
                   f"对白镜头 {len(dialogue_shots)} 个\n\n")
        summary += ("可以进入成片阶段。" if not issues else "还不能完整产出：\n" +
                    "\n".join(f"• {value}" for value in issues))
        QMessageBox.information(self, "成片产出检查", summary)
        return {"ready":not issues, "duration":duration, "issues":issues}

    def _submit_next_canvas_storyboard_image(self):
        if not self._canvas_storyboard_queue:
            source = self._nodes.get(self._canvas_storyboard_source)
            record = self._custom_record(self._canvas_storyboard_source)
            aspect = self._storyboard_production_ratio(self._canvas_storyboard_source)
            incomplete = [
                int(shot.get("number") or index + 1)
                for index, shot in enumerate(self.current_storyboard().get("shots", []))
                if not motion_panels_ready(shot, aspect)]
            if incomplete:
                if record is not None:
                    record["pipeline_stage"] = "assets_ready"
                    record["approval_required"] = "storyboard_panels"
                    record["auto_run_enabled"] = False
                    record["status"] = (
                        f"原生 {aspect} 运动分镜尚未完成：" + "、".join(
                            f"{value:02d}" for value in incomplete))
                    self._save_layout_now()
                if source:
                    source.badge = f"缺少 {aspect} 分镜"; source.update()
                self.refresh()
                return
            if record is not None:
                total_frames = sum(len(shot.get("motion_keyframes") or [])
                                   for shot in self.current_storyboard().get("shots", []))
                record["status"] = (f"阶段 3/6 · {total_frames} 帧运动分镜已生成 · "
                                    "请逐镜检查，确认后执行阶段 4")
                record["pipeline_stage"] = "storyboard_panels_ready"
                record["approval_required"] = "final_prompts"
                self._save_layout_now()
            if source:
                source.badge = "待确认运动分镜"; source.update()
            self.refresh()
            self._schedule_auto_continue(
                self._canvas_storyboard_source, from_async=True)
            return
        manager = get_ai_manager()
        item = self._canvas_storyboard_queue.pop(0)
        # Old in-memory queues created before V5 contained shot indices. Expand
        # them lazily so a running legacy session upgrades without a crash.
        if isinstance(item, int):
            shots = self.current_storyboard().get("shots", [])
            if not 0 <= item < len(shots):
                return self._submit_next_canvas_storyboard_image()
            frames = self._normalize_motion_keyframes(shots[item])
            aspect = self._storyboard_production_ratio()
            generation_id = hashlib.sha1(
                f"legacy-upgrade|{item}|{datetime.now().isoformat()}|{aspect}".encode()
            ).hexdigest()[:14]
            shots[item]["motion_panel_pending_paths"] = [""] * len(frames)
            shots[item]["motion_panel_pending_generation_id"] = generation_id
            shots[item]["motion_panel_pending_aspect_ratio"] = aspect
            expanded = [{
                "shot_index":item, "frame_index":frame_index,
                "kind":"storyboard_panel", "generation_id":generation_id,
                "aspect_ratio":aspect,
            } for frame_index in range(len(frames))]
            item, self._canvas_storyboard_queue = expanded[0], (
                expanded[1:] + self._canvas_storyboard_queue)
        index = int(item.get("shot_index", -1))
        frame_index = int(item.get("frame_index", -1))
        kind = str(item.get("kind") or "storyboard_panel")
        aspect = normalize_aspect_ratio(
            item.get("aspect_ratio") or self._storyboard_production_ratio())
        if aspect != self._storyboard_production_ratio():
            return self._submit_next_canvas_storyboard_image()
        shots = self.current_storyboard().get("shots", [])
        if not 0 <= index < len(shots):
            return self._submit_next_canvas_storyboard_image()
        shot = shots[index]
        frames = self._normalize_motion_keyframes(shot)
        if not 0 <= frame_index < len(frames):
            return self._submit_next_canvas_storyboard_image()
        references = self._motion_panel_reference_assets(
            shot, index, frame_index, aspect)
        operation = "image_edit" if references else "text_to_image"
        provider = self._locked_storyboard_image_provider(
            operation, self._canvas_storyboard_source)
        bible = str((self.current_storyboard().get("visual_bible") or {}).get("ai_storyboard") or "")
        prompt = motion_panel_prompt(
            shot, index, frame_index, bible, provider_name=provider.name,
            redraw=kind == "storyboard_panel_reroll", aspect_ratio=aspect)
        inputs = {"prompt":prompt}
        if references:
            paths = [str(value.get("path") or "") for value in references]
            inputs.update({
                "image":paths[0], "images":paths,
                "reference_assets":references,
            })
        size = resolve_image_output_size(provider.name, "2K", aspect)
        handle = manager.submit(provider.name, TaskRequest(
            operation=operation, inputs=inputs,
            params={"size":size, "quality":"high", "n":1},
            metadata={
                "shot_id":shot["id"], "frame_index":frame_index,
                "purpose":"canvas_storyboard_panel",
                "contract_version":MOTION_STORYBOARD_CONTRACT_VERSION,
                "aspect_ratio":aspect,
            },
            use_cache=False))
        self._standalone_tasks[handle.id] = {
            "handle":handle, "node_id":f"shot:{shot['id']}",
            "provider":provider.name, "kind":kind, "shot_index":index,
            "frame_index":frame_index,
            "generation_id":str(item.get("generation_id") or ""),
            "aspect_ratio":aspect,
            "source_id":str(self._canvas_storyboard_source or ""),
        }
        node = self._nodes.get(f"shot:{shot['id']}")
        if node:
            node.badge = f"{provider.name} · K{frame_index + 1}/{len(frames)} · 0%"
            node.update()

    def reroll_canvas_storyboard_shot(self, node):
        return self.reroll_canvas_storyboard_panel(node, None)

    def reroll_canvas_storyboard_panel(self, node, frame_index=None):
        shot = self._find_shot(node.payload.get("shot_id"))
        if not shot:
            return False
        if any(str(task.get("kind") or "") in {
                    "storyboard_reroll", "storyboard_panel_reroll"} and
               str(task.get("node_id") or "") == str(node.node_id)
               for task in self._standalone_tasks.values()):
            QMessageBox.information(
                self, "分镜重生进行中", "这个镜头已经在重新生成，请等待当前任务完成。")
            return False
        shots = self.current_storyboard().get("shots", [])
        shot_index = shots.index(shot) if shot in shots else 0
        source_id = self._current_production_source_id()
        try:
            frames = self._normalize_motion_keyframes(shot)
            if frame_index is not None and not 0 <= int(frame_index) < len(frames):
                raise ValueError("指定的运动画格不存在")
            self._canvas_storyboard_source = str(source_id)
            self._queue_motion_storyboard_panels(
                [shot_index], frame_index=frame_index,
                kind="storyboard_panel_reroll")
            self._submit_next_canvas_storyboard_image()
        except Exception as error:
            QMessageBox.warning(self, "重新生成", str(error))
            return False
        shot["motion_board_review_status"] = "regenerating"
        label = (f"K{int(frame_index) + 1}" if frame_index is not None else "全部画格")
        node.badge = f"本镜 {label} 重生中 0%"; node.update()
        source = self._custom_record(source_id)
        if source is not None:
            source["pipeline_stage"] = "storyboard_panels_ready"
            source["approval_required"] = "final_prompts"
            source["auto_run_enabled"] = False
            source["status"] = (
                f"镜头 {int(shot.get('number') or shot_index + 1):02d} {label} 重生中 · "
                "完成并人工检查前不会进入定稿提示词")
        self._save_layout_now()
        return True

    def expand_shot_history(self, node):
        shot_id = str(node.payload.get("shot_id") or "")
        takes = [item for item in self._nodes.values()
                 if item.node_type == "shot_take" and
                 str(item.payload.get("shot_id") or "") == shot_id]
        if not takes:
            return
        self.scene.clearSelection(); node.setSelected(True)
        for index, take in enumerate(takes):
            take.setVisible(True); take.setSelected(True)
            take.setPos(node.pos() + QPointF((index % 3) * 215.0,
                                             node.height + 55.0 + (index // 3) * 165.0))
            self._positions()[take.node_id] = [take.pos().x(), take.pos().y()]
        self._save_layout_now(); self.scene.update_edges()

    def create_workflow_group(self):
        import uuid
        selected = [item for item in self.scene.selectedItems()
                    if isinstance(item, CanvasNodeItem) and
                    item.node_type not in ("generation_task", "workflow_group", "director")]
        if len(selected) < 2:
            QMessageBox.information(self, "创建工作流", "请至少选择两个节点。")
            return
        group_id = f"custom:{uuid.uuid4().hex[:12]}"
        group_nodes = [node.node_id for node in selected]
        center_x = min(node.pos().x() for node in selected) - 430
        center_y = sum(node.pos().y() for node in selected) / len(selected)
        self._positions().setdefault("__custom_nodes__", []).append({
            "id":group_id, "type":"workflow_group", "title":"工作流组",
            "content":f"{len(selected)} 个节点 · 可整组执行、暂停和重试",
            "group_nodes":group_nodes, "status":"已就绪",
        })
        self._positions()[group_id] = [round(center_x, 2), round(center_y, 2)]
        edges = self._positions().setdefault("__workflow_edges__", [])
        for node_id in group_nodes:
            edge = {"source":group_id, "target":node_id, "type":"group"}
            if edge not in edges: edges.append(edge)
        self._save_layout_now(); self.refresh(); self.focus_node(group_id)

    def _source_storyboard_for_asset(self, asset_node_id):
        for edge in self._positions().get("__workflow_edges__", []):
            if (isinstance(edge, dict) and edge.get("target") == asset_node_id and
                    edge.get("type") in ("character", "scene", "element")):
                return str(edge.get("source") or "")
        return ""

    def set_production_asset_lock(self, node, locked: bool):
        node_id = str(node.node_id)
        record = self._custom_record(node_id)
        if record is None or (locked and not os.path.exists(str(record.get("path") or ""))):
            return
        if locked and record.get("scene_variant_of"):
            QMessageBox.information(
                self, "场景状态不能独立锁定",
                "灯光、天气和局部取景状态必须继承同一场景母版。请锁定其空间母版，"
                "状态图只作为预览，不能成为另一套空间权威。")
            return
        if locked and str(record.get("asset_kind") or "") == "character":
            reference_set = dict(record.get("character_reference_set") or {})
            missing = [label for role, label, _prompt in CHARACTER_REFERENCE_SPECS
                       if not os.path.exists(str(reference_set.get(role) or ""))]
            if missing:
                QMessageBox.information(
                    self, "角色设定尚未完整",
                    "角色必须完成四项权威参考后才能锁定：\n" +
                    "\n".join(f"• {value}" for value in missing))
                return
        if locked and str(record.get("asset_kind") or "") == "scene":
            reference_set = dict(record.get("scene_reference_set") or {})
            missing = [label for role, label, _prompt in SCENE_VIEW_SPECS
                       if not os.path.exists(str(reference_set.get(role) or ""))]
            if missing:
                QMessageBox.information(
                    self, "场景权威视图尚未完整",
                    "场景必须完成五项空间参考后才能锁定：\n" +
                    "\n".join(f"• {value}" for value in missing))
                return
        was_locked = bool(record.get("locked"))
        record["locked"] = bool(locked); record["adopted"] = bool(record.get("path"))
        version = int(record.get("asset_version") or 0)
        record["status"] = f"V{version} 已锁定" if locked else f"V{version} 已解锁"
        if was_locked and not locked:
            self.invalidate_asset_dependents(node_id)
        source_id = self._source_storyboard_for_asset(node_id)
        all_locked = False
        if source_id:
            asset_ids = self._storyboard_asset_node_ids(source_id)
            all_locked = bool(asset_ids) and all(
                bool((self._custom_record(value) or {}).get("locked")) for value in asset_ids)
            source = self._custom_record(source_id)
            if source is not None:
                source["status"] = ("阶段 2/6 · 全部资产已锁定 · 请执行阶段 3" if all_locked else
                                    "阶段 2/6 · 等待逐项采用并锁定")
                source["pipeline_stage"] = "assets_ready" if all_locked else "assets_generated"
                source["approval_required"] = "blocking"
                if all_locked:
                    source.pop("awaiting_gate", None)
        self._save_layout_now(); self.refresh(); self.focus_node(node_id)
        if source_id and all_locked:
            self._schedule_auto_continue(source_id, from_async=False)

    def regenerate_production_asset(self, node):
        record = self._custom_record(node.node_id)
        if record is None: return
        if record.get("locked"):
            answer = QMessageBox.question(
                self, "生成资产新版本",
                "当前资产已锁定。生成新候选会解锁资产，并使下游提示词和生成器失效，继续吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes: return
            record["locked"] = False
        source_id = self._source_storyboard_for_asset(node.node_id)
        self._canvas_storyboard_source = source_id
        if str(record.get("asset_kind") or "") == "character":
            self._remove_legacy_character_portrait_view(node.node_id)
            self._canvas_character_queue = [
                {"node_id":node.node_id, "role":role}
                for role, _label, _prompt in CHARACTER_REFERENCE_SPECS]
            record["status"] = "角色设定 0/4 · 正在生成新套件"
        elif str(record.get("asset_kind") or "") == "scene":
            record["scene_reference_set"] = {}
            self._canvas_character_queue = [
                {"node_id":node.node_id, "scene_role":role}
                for role, _label, _prompt in SCENE_VIEW_SPECS]
            record["status"] = "场景视图 0/5 · 正在生成新套件"
        else:
            self._canvas_character_queue = [node.node_id]
        self.invalidate_asset_dependents(node.node_id)
        try:
            self._submit_next_canvas_character()
        except Exception as error:
            QMessageBox.warning(self, "资产新版本生成失败", str(error))

    def show_production_asset_versions(self, node):
        record = self._custom_record(node.node_id)
        if record is None: return
        candidates = [str(value) for value in record.get("candidates", [])
                      if value and os.path.exists(str(value))]
        if not candidates:
            QMessageBox.information(self, "资产版本", "当前还没有可用候选版本。")
            return
        menu = QMenu(self); self._style_popup_menu(menu)
        actions = {}
        current = str(record.get("path") or "")
        for index, path in enumerate(candidates):
            action = menu.addAction(
                f"{'✓ ' if path == current else ''}V{index + 1} · {Path(path).name}")
            actions[action] = (index + 1, path)
        anchor = getattr(self, "dock_generate_btn", self)
        chosen = menu.exec(anchor.mapToGlobal(anchor.rect().topLeft()))
        value = actions.get(chosen)
        if not value: return
        version, path = value
        if path != current:
            record["path"] = path; record["asset_version"] = version
            record["locked"] = False; record["adopted"] = True
            record["status"] = f"V{version} 已采用 · 待锁定"
            self.invalidate_asset_dependents(node.node_id)
            self._save_layout_now(); self.refresh(); self.focus_node(node.node_id)

    def invalidate_asset_dependents(self, asset_node_id):
        source_id = self._source_storyboard_for_asset(asset_node_id)
        if not source_id: return
        record = self._custom_record(asset_node_id) or {}
        reason = f"{record.get('title') or '资产'}版本发生变化"
        shot_ids = set()
        for shot in self.current_storyboard().get("shots", []):
            shot["production_ready"] = False
            shot["invalidated_by"] = reason
            for key in ("final_image_prompt", "final_start_image_prompt",
                        "final_end_image_prompt", "final_video_prompt",
                        "space_geometry_contract", "scene_master_path",
                        "scene_master_id"):
                shot.pop(key, None)
            shot_ids.add(str(shot.get("id") or ""))
        for value in self._positions().get("__custom_nodes__", []):
            if (isinstance(value, dict) and value.get("generator_kind") in ("image", "video") and
                    (str(value.get("shot_id") or "") in shot_ids or
                     value.get("type") == "workflow_group")):
                value["status"] = "上游资产变化 · 已失效"
                value["invalidated"] = True
        source = self._custom_record(source_id)
        if source is not None:
            source["status"] = "资产变化 · 需要重新合成提示词"
            source["pipeline_stage"] = "assets_changed"
        self.storyboardMutated.emit(); self._save_layout_now()

    def execute_workflow_group(self, group_node, failed_only=False, pending_only=False):
        record = self._custom_record(group_node.node_id)
        if record is None:
            return
        node_ids = list(record.get("group_nodes") or [])
        if failed_only:
            node_ids = [value for value in node_ids if value in self._workflow_failed_nodes]
        if pending_only:
            node_ids = [value for value in node_ids
                        if not os.path.exists(str((self._custom_record(value) or {}).get("path") or ""))]
        if record.get("generator_kind") == "image":
            start_ids = [value for value in node_ids if str(
                (self._custom_record(value) or {}).get("frame_role") or "") == "start"]
            end_ids = [value for value in node_ids if str(
                (self._custom_record(value) or {}).get("frame_role") or "") == "end"]
            # A fresh dual-anchor group intentionally stops after K1. Klast
            # must see the K1 candidate that the user actually approved.
            if start_ids and end_ids:
                pending_starts = [value for value in start_ids if not os.path.exists(str(
                    (self._custom_record(value) or {}).get("path") or ""))]
                if pending_starts:
                    node_ids = pending_starts
                    record["endpoint_phase"] = "start"
                    record["status"] = f"起始帧生成中 · 0/{len(node_ids)}"
        if record.get("generator_kind") == "video":
            group_id = str(group_node.node_id)
            expanded_queue = []
            for node_id in node_ids:
                generator = self._custom_record(str(node_id)) or {}
                count = max(1, min(4, int(
                    generator.get("candidate_count") or
                    record.get("candidate_count") or 2)))
                generator["candidate_batch_paths"] = []
                generator["awaiting_candidate_selection"] = False
                generator["handoff_approved"] = False
                generator["workflow_group_id"] = group_id
                expanded_queue.extend([str(node_id)] * count)
            self._serial_video_queues[group_id] = expanded_queue
            record["total_candidate_attempts"] = len(expanded_queue)
            record["awaiting_video_node_id"] = ""
            record["status"] = (
                f"视频候选串行生成 · 0/{len(expanded_queue)} 次调用")
            for batch in self._positions().get("__production_batches__", []):
                if isinstance(batch, dict) and batch.get("group_id") == group_id:
                    batch["status"] = "running" if expanded_queue else "complete"
                    batch["pending_node_ids"] = list(expanded_queue)
            group_node.badge = record["status"]
            group_node.update()
            self._save_layout_now()
            self._submit_next_serial_video(group_id)
            return
        launched = 0
        for node_id in node_ids:
            node = self._nodes.get(node_id)
            if node is None: continue
            if node.node_type == "storyboard_node":
                self.submit_canvas_storyboard(node, str(node.payload.get("content") or "")); launched += 1
            elif node.node_type == "image_node":
                self.submit_standalone_generation(node, str(node.payload.get("content") or ""), "图生图")
                launched += 1
            elif node.node_type == "video_node":
                self.submit_standalone_generation(node, str(node.payload.get("content") or ""), "图生视频")
                launched += 1
            elif node.node_type == "audio_node":
                self.submit_standalone_generation(node, str(node.payload.get("content") or ""), "对白配音")
                launched += 1
            elif node.node_type == "shot":
                self.reroll_canvas_storyboard_shot(node); launched += 1
        record["status"] = f"执行中 · {launched} 项" if launched else "没有可执行节点"
        for batch in self._positions().get("__production_batches__", []):
            if isinstance(batch, dict) and batch.get("group_id") == group_node.node_id:
                batch["status"] = "running" if launched else "complete"
        group_node.badge = record["status"]; group_node.update(); self._save_layout_now()

    def _submit_next_serial_video(self, group_id, completed_node_id=""):
        """Generate candidates serially and stop at every approval/QC gate.

        Repeated node ids in the queue are separate candidate pulls for one
        segment.  The next segment is never submitted until one candidate from
        the current segment has been explicitly adopted and passed clip QC.
        """
        group_id = str(group_id)
        record = self._custom_record(group_id)
        if record is None:
            self._serial_video_queues.pop(group_id, None)
            return
        queue = self._serial_video_queues.get(group_id)
        if queue is None:
            # This queue normally lives only in memory. Reconstruct its
            # ungenerated tail after restart/approval pause from saved nodes.
            queue = []
            group_nodes = [str(value) for value in record.get("group_nodes", [])]
            awaiting_id = str(record.get("awaiting_video_node_id") or
                              completed_node_id or "")
            start_index = (group_nodes.index(awaiting_id) + 1
                           if awaiting_id in group_nodes else 0)
            for node_id in group_nodes[start_index:]:
                generator = self._custom_record(node_id) or {}
                if generator.get("adopted"):
                    continue
                # Recovery is about resuming unfinished segments, not silently
                # spending more calls to recreate every optional candidate pull.
                # Generate one candidate, stop for adoption, then advance.
                queue.append(node_id)
            self._serial_video_queues[group_id] = queue
            record["total_candidate_attempts"] = max(
                int(record.get("total_candidate_attempts") or 0), len(queue))
            if queue:
                record["status"] = f"已恢复剩余视频生成队列 · {len(queue)} 次调用"
        if completed_node_id and queue and queue[0] == completed_node_id:
            queue.pop(0)
        total_segments = len(record.get("group_nodes") or [])
        total_attempts = int(record.get("total_candidate_attempts") or len(queue))
        completed_attempts = max(0, total_attempts - len(queue))
        for batch in self._positions().get("__production_batches__", []):
            if isinstance(batch, dict) and batch.get("group_id") == group_id:
                batch["pending_node_ids"] = list(queue)
                batch["completed"] = completed_attempts
                batch["status"] = "running" if queue else "awaiting_approval"

        # Finishing the last pull for a segment is an approval gate, not an
        # instruction to feed its arbitrary tail into the next request.
        if completed_node_id and (not queue or queue[0] != completed_node_id):
            completed_record = self._custom_record(completed_node_id) or {}
            batch_paths = [str(value) for value in
                           completed_record.get("candidate_batch_paths", [])
                           if value and os.path.exists(str(value))]
            completed_record["awaiting_candidate_selection"] = bool(batch_paths)
            completed_record["handoff_approved"] = False
            record["awaiting_video_node_id"] = completed_node_id
            if batch_paths:
                completed_record["status"] = (
                    f"{len(batch_paths)} 个视频候选待选 · 采用后自动审片")
                record["status"] = (
                    f"暂停 · 当前段 {len(batch_paths)} 个候选待定稿")
                source = self._custom_record(str(record.get("source_node_id") or ""))
                if source is not None:
                    source["pipeline_stage"] = "video_candidates_ready"
                    source["approval_required"] = "approved_video_candidate"
                    source["status"] = (
                        "视频候选已生成 · 请在当前段选择定稿；审片通过后才会继续下一段")
            else:
                completed_record["status"] = "本段所有候选生成失败 · 已暂停"
                record["status"] = "暂停 · 当前视频段没有可用候选"
            for batch in self._positions().get("__production_batches__", []):
                if isinstance(batch, dict) and batch.get("group_id") == group_id:
                    batch["status"] = "awaiting_approval" if batch_paths else "failed"
            self._save_layout_now(); self.refresh()
            return

        if not queue:
            record["status"] = "全部视频段已定稿并通过逐段审片 · 正在序列审片"
            self._serial_video_queues.pop(group_id, None)
            source_id = str(record.get("source_node_id") or "")
            source = self._custom_record(source_id)
            if source is not None:
                failed = len(set(record.get("group_nodes") or []) &
                             self._workflow_failed_nodes)
                source["status"] = (f"连续视频段完成 · {failed} 段失败"
                                    if failed else "逐段审片通过 · 正在检查相邻镜头连续性")
                source["pipeline_stage"] = "video_qc_pending"
                source.pop("approval_required", None)
            node = self._nodes.get(group_id)
            if node:
                node.badge = record["status"]; node.update()
            self._save_layout_now()
            QTimer.singleShot(0, lambda sid=source_id: self._maybe_start_sequence_qc(sid))
            return
        node_id = str(queue[0])
        node = self._nodes.get(node_id)
        if node is None:
            self._workflow_failed_nodes.add(node_id)
            self._submit_next_serial_video(group_id, node_id)
            return
        next_record = self._custom_record(node_id)
        if next_record is not None:
            generated = len(next_record.get("candidate_batch_paths") or [])
            if (generated == 0 and
                    str(next_record.get("handoff_mode") or "") == "continuous_tail" and
                    not self._prepare_video_handoff_anchor(
                        next_record, completed_node_id=completed_node_id)):
                record["status"] = "暂停 · 连续段缺少已批准的真实尾帧"
                record["awaiting_video_node_id"] = node_id
                source = self._custom_record(str(record.get("source_node_id") or ""))
                if source is not None:
                    source["pipeline_stage"] = "video_handoff_blocked"
                    source["status"] = str(next_record.get("handoff_status") or record["status"])
                self._save_layout_now(); self.refresh()
                return
            self._refresh_video_generator_contract(next_record)
            node.payload.update(next_record)
        candidate_number = len((next_record or {}).get("candidate_batch_paths") or []) + 1
        candidate_total = max(1, min(4, int(
            (next_record or {}).get("candidate_count") or
            record.get("candidate_count") or 2)))
        segment_number = ((record.get("group_nodes") or []).index(node_id) + 1
                          if node_id in (record.get("group_nodes") or []) else 1)
        record["status"] = (
            f"视频段 {segment_number}/{total_segments} · "
            f"候选 {candidate_number}/{candidate_total} 正在提交")
        self._save_layout_now()
        before = set(self._standalone_tasks)
        self.submit_standalone_generation(
            node, str(node.payload.get("content") or ""), "图生视频")
        created = [task_id for task_id in self._standalone_tasks if task_id not in before]
        if not created:
            self._workflow_failed_nodes.add(node_id)
            QTimer.singleShot(
                0, lambda gid=group_id, nid=node_id:
                self._submit_next_serial_video(gid, nid))
            return
        for task_id in created:
            self._standalone_tasks[task_id]["workflow_group_id"] = group_id
            self._standalone_tasks[task_id]["video_candidate_number"] = candidate_number
            self._standalone_tasks[task_id]["video_candidate_total"] = candidate_total
        record["status"] = (
            f"视频段 {segment_number}/{total_segments} · "
            f"候选 {candidate_number}/{candidate_total} 生成中")
        self._save_layout_now()

    def _recover_production_batches(self):
        changed = False
        interrupted_sources = set()
        for batch in self._positions().get("__production_batches__", []):
            if not isinstance(batch, dict) or batch.get("status") != "running":
                continue
            batch["status"] = "interrupted"
            group = self._custom_record(str(batch.get("group_id") or ""))
            if group is not None:
                group["status"] = "上次运行中断 · 可继续未完成项"
            source_id = str(batch.get("source_node_id") or "")
            source = self._custom_record(source_id)
            if source is not None:
                source["pipeline_stage"] = "production_interrupted"
                source["interrupted_kind"] = str(batch.get("kind") or "")
                source["status"] = "上次生产被中断 · 点击继续会只恢复未完成项"
                interrupted_sources.add(source_id)
            changed = True
        recover_stages = {
            "planning":"", "assets_generating":"shots_ready",
            "blocking_generating":"assets_ready",
            "storyboard_panels_generating":"assets_ready",
            "images_generating":"generators_ready",
            "audio_generating":"video_ready",
        }
        for source in self._production_source_records():
            source_id = str(source.get("id") or "")
            stage = str(source.get("pipeline_stage") or "")
            if source_id in interrupted_sources or stage not in recover_stages:
                continue
            source["pipeline_stage"] = recover_stages[stage]
            source["status"] = "上次操作中断 · 点击继续会从最近安全节点恢复"
            source["auto_run_enabled"] = False
            changed = True
        if changed:
            self._save_layout_now(); self.refresh()

    def resume_production_batch(self, group_node):
        self.execute_workflow_group(group_node, pending_only=True)

    def pause_workflow_group(self, group_node):
        record = self._custom_record(group_node.node_id)
        if record is None: return
        node_ids = set(record.get("group_nodes") or [])
        count = 0
        for task in self._standalone_tasks.values():
            if task.get("node_id") in node_ids and not task["handle"].is_finished:
                task["handle"].cancel(); count += 1
        record["status"] = f"已暂停 · 取消 {count} 项"
        group_node.badge = "已暂停"; group_node.update(); self._save_layout_now()

    def create_storyboard_from_script(self, node, content: str, auto_start: bool = False,
                                      planning_model_data=None):
        """Create a production source without spending a model call.

        ``auto_start`` is retained for old callers/projects but intentionally
        ignored: model and planning parameters must be confirmed on the newly
        created project node before stage 1 may submit.
        """
        script = str(content or "").strip()
        if not script:
            QMessageBox.information(
                self, "创建项目并拆镜", "请先在剧本工作台写入故事想法或完整剧本。")
            return
        source_id = str(node.node_id)
        source_record = self._custom_record(source_id) or node.payload
        snapshot = save_script_version(source_record, script, "送入制片")
        source_record["content"] = script
        source_pos = QPointF(node.pos())
        title = str(node.title or "剧本工作台").strip()
        planning_provider, planning_model = (
            tuple(planning_model_data) if isinstance(planning_model_data, (tuple, list)) and
            len(planning_model_data) >= 2 else ("", ""))
        if not planning_provider:
            available = self._available_script_models()
            if available:
                _label, planning_provider, planning_model = available[0]
        storyboard_id = self.create_custom_node(
            "storyboard_node", source_pos + QPointF(430, 0), {
                "title":f"制片项目 · {title}", "content":script,
                "style":str(node.payload.get("style") or "电影写实"),
                "shot_count":int(node.payload.get("shot_count") or 0),
                "automation_mode":"checkpoints", "candidate_count":2,
                "video_candidate_count":2,
                "planning_provider":str(planning_provider or ""),
                "planning_model":str(planning_model or ""),
                "planning_temperature":0.5,
                "production_ratio":"16:9",
                "pipeline_stage":"",
                "status":"项目已创建 · 请确认拆镜模型和参数",
                "source_script_id":source_id, "source_script_version":snapshot["version"],
            })
        self._remember_workflow_edge(source_id, storyboard_id, "script")
        record = self._custom_record(source_id)
        if record is not None:
            record["status"] = "已送入制片画布 · 等待阶段 1"
        self._save_layout_now(); self.refresh(); self.focus_node(storyboard_id)

    def submit_script_generation(self, node, content: str, action: str, model_data):
        provider_name, model = model_data or ("", "")
        if not provider_name:
            QMessageBox.warning(self, "没有文本模型", "请先在设置中配置 LLM API。")
            return
        instructions = {
            "生成完整脚本": "根据用户的题材或要求，生成可直接制作的完整短视频脚本，包含场景、人物、动作、对白和镜头节奏。",
            "续写脚本": "延续现有脚本的角色、语气和情节，继续写作，不要重复已有内容。",
            "改写优化": "优化现有脚本的开场钩子、冲突、节奏、对白和结尾，保留核心创意。",
            "拆分镜头": "把脚本拆成编号镜头，每镜包含时长、景别、画面、动作、对白和转场。",
            "转成画面提示词": "把脚本转换成按镜头排列的高质量 AI 图片/视频画面提示词。",
            "剧本体检": "以专业剧本编辑身份诊断当前剧本。保留原文并输出：一句话故事、结构节拍、人物动机、冲突升级、逻辑漏洞、节奏问题和按优先级排列的修改建议。不要擅自重写全文。",
            "强化人物弧光": "在保留题材和主要情节的前提下，强化主角目标、阻力、选择、代价与结尾变化，输出可直接制作的修订版完整剧本。",
            "对白润色": "保留事件和场景，只润色对白，使角色声音可区分、潜台词明确、口语自然且适合表演，输出完整修订稿。",
            "制片可行性检查": "以执行制片身份检查当前剧本，输出场景、角色、道具、特效、声音、连续性与生成难点清单，标注高风险内容并给出不损害剧情的降本替代方案。不要重写原稿。",
        }
        if node.payload.get("copywriting_workbench"):
            product_name = str(node.payload.get("product_name") or "").strip()
            description = str(node.payload.get("product_description") or "").strip()
            style = str(node.payload.get("copy_style") or "激情抓眼球")
            duration = str(node.payload.get("copy_duration") or "30").strip()
            translating = action.startswith("翻译为")
            if translating:
                language = action.removeprefix("翻译为").strip() or "英语"
                if not content.strip():
                    QMessageBox.information(self, "口播文案", "请先生成或输入需要翻译的文案。")
                    return
                instructions[action] = (
                    f"把用户提供的短视频口播文案准确翻译为{language}。保留品牌名、产品名、"
                    "数字、优惠信息、逐行节奏和结尾行动号召；不要解释，不要添加标题，只输出译文。")
                raw_text = content.strip()
            else:
                if not description:
                    QMessageBox.information(self, "口播文案", "请先填写产品描述和核心卖点。")
                    return
                target = max(1, int(float(duration))) if duration.replace('.', '', 1).isdigit() else 30
                instructions[action] = (
                    "你是信息流短视频广告口播文案专家。只输出可直接朗读的纯文案，不写镜头、"
                    "旁白标记、标题或解释。开头3秒必须有钩子，中段自然呈现核心卖点，结尾有明确CTA；"
                    "每句约8至12个汉字并逐句换行，口语自然，避免无法证实的绝对化承诺。"
                    f"目标时长约{target}秒，按每秒约4个汉字控制总长度；文案风格为“{style}”。")
                if action == "压缩精简":
                    instructions[action] += "在保留关键信息的前提下进一步压缩，删除重复和空话。"
                elif action == "增强开场钩子":
                    instructions[action] += "重点重写前3秒，使其更具体、更有停留价值，但不要标题党。"
                elif action == "改写优化":
                    instructions[action] += "结合现有文案重写优化；若现有文案为空，则根据产品信息新写。"
                raw_text = (f"产品/品牌：{product_name or '未填写'}\n产品信息：{description}\n"
                            f"现有文案：{content.strip() or '无'}")
        else:
            raw_text = content.strip()
        if not raw_text:
            QMessageBox.information(self, "脚本节点", "请先输入题材、要求或已有脚本。")
            return
        user_text = self._apply_style_to_prompt(
            raw_text, str(node.payload.get("style") or ""))
        manager = get_ai_manager()
        try:
            handle = manager.submit(provider_name, TaskRequest(
                operation="chat",
                inputs={"messages": [
                    {"role": "system", "content": instructions.get(action, instructions["生成完整脚本"])},
                    {"role": "user", "content": user_text},
                ]},
                params={"model": model},
                metadata={"canvas_node_id": node.node_id, "purpose": "script_node"},
                use_cache=False,
            ))
            self._standalone_tasks[handle.id] = {
                "handle": handle, "node_id": node.node_id,
                "provider": provider_name, "kind": "script", "script_action": action,
                "copywriting": bool(node.payload.get("copywriting_workbench")),
            }
            node.badge = "口播生成中" if node.payload.get("copywriting_workbench") else "脚本生成中"
            node.update()
        except Exception as error:
            QMessageBox.warning(self, "脚本提交失败", str(error))

    def _submit_skill_chat(self, node, system: str, user, kind: str,
                           auto_retry=False, provider_name: str = "",
                           model: str = ""):
        manager = get_ai_manager()
        providers = manager.registry.by_capability("chat")
        requested_provider = str(provider_name or "").strip()
        provider = next((value for value in providers
                         if value.name == requested_provider), None)
        if provider is None and requested_provider:
            QMessageBox.warning(
                self, "专业 Skill",
                f"项目锁定的文本模型提供方“{requested_provider}”当前不可用。")
            return None
        if provider is None:
            provider = next((value for value in providers if value.name == "openai"),
                            providers[0] if providers else None)
        if provider is None:
            QMessageBox.warning(self, "专业 Skill", "没有可用的文本模型。")
            return None
        requested_model = str(model or "").strip()
        if not requested_model:
            try:
                from config import LLM_MODEL_NAME
                requested_model = str(LLM_MODEL_NAME or "").strip()
            except Exception:
                requested_model = ""
        if not requested_model:
            requested_model = "gpt-5.5"
        handle = manager.submit(provider.name, TaskRequest(
            operation="chat", inputs={"messages":[
                {"role":"system", "content":system}, {"role":"user", "content":user}]},
            params={"model":requested_model, "timeout_seconds":300},
            metadata={"canvas_node_id":node.node_id, "purpose":kind,
                      "retry_count":1, "retry_transient_only":True},
            use_cache=False))
        self._standalone_tasks[handle.id] = {
            "handle":handle, "node_id":node.node_id, "provider":provider.name,
            "kind":kind, "auto_retry":auto_retry,
        }
        record = self._custom_record(node.node_id)
        if record is not None:
            record["status"] = "AI 导演分析中" if kind == "director_review" else "调度分析中"
        self._save_layout_now()
        return handle

    @staticmethod
    def _local_image_data_url(path: str):
        try:
            mime = mimetypes.guess_type(path)[0] or "image/png"
            encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
            return f"data:{mime};base64,{encoded}"
        except OSError:
            return ""

    def submit_image_description(self, node):
        path = str(node.payload.get("path") or node.thumbnail or "")
        if not path or not os.path.exists(path) or not self._is_image_path(path):
            QMessageBox.information(self, "AI 看图", "当前图片文件不存在。")
            return
        manager = get_ai_manager()
        providers = manager.registry.by_capability("chat")
        provider = next((value for value in providers if value.name == "openai"), None)
        if provider is None:
            QMessageBox.warning(
                self, "没有视觉模型",
                "AI 看图需要支持图片输入的 OpenAI 多模态模型；其他图片编辑功能仍可正常使用。")
            return
        data_url = self._local_image_data_url(path)
        if not data_url:
            QMessageBox.warning(self, "AI 看图", "无法读取这张图片。")
            return
        try:
            from api_config import get as api_get
            model = api_get("llm").default_model or "gpt-5.5"
        except Exception:
            model = "gpt-5.5"
        messages = [
            {"role": "system", "content": (
                "你是影视视觉分析师。用中文描述用户图片，输出可直接用于图生图或图生视频的提示词。"
                "依次写清主体身份、服装与外观、场景空间、前中后景、构图景别、机位、光线色彩，"
                "最后补一行建议的动作与运镜。不要虚构画面中不存在的关键物体，不要使用 Markdown 标题。")},
            {"role": "user", "content": [
                {"type": "text", "text": "分析这张图片并生成可继续创作的准确画面描述。"},
                {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
            ]},
        ]
        try:
            handle = manager.submit(provider.name, TaskRequest(
                operation="chat", inputs={"messages": messages}, params={"model": model},
                metadata={"canvas_node_id": node.node_id,
                          "purpose": "image_description"}, use_cache=False))
            self._standalone_tasks[handle.id] = {
                "handle": handle, "node_id": node.node_id,
                "provider": provider.name, "kind": "image_description",
            }
            record = self._custom_record(node.node_id)
            if record is not None:
                record["status"] = "AI 正在识图"
            node.badge = "AI 正在识图"
            node.update(); self._save_layout_now()
        except Exception as error:
            QMessageBox.warning(self, "AI 看图提交失败", str(error))

    @staticmethod
    def _extract_video_review_frames(path: str):
        """用项目内置 FFmpeg 抽取首/中/尾帧；失败时安全返回空列表。"""
        if not path or not os.path.exists(path):
            return []
        try:
            from utils.ffmpeg_utils import get_ffmpeg_path
            ffmpeg = get_ffmpeg_path()
            probe = subprocess.run([ffmpeg, "-i", path], capture_output=True,
                                   timeout=15, check=False)
            stderr = (probe.stderr or b"").decode("utf-8", "replace")
            match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
            duration = ((int(match.group(1)) * 60 + int(match.group(2))) * 60 +
                        float(match.group(3))) if match else 0.0
            times = [0.05]
            if duration > 0.3:
                times.extend([duration * 0.5, max(0.05, duration - 0.08)])
            folder = LAYOUT_FILE.parent / "video_review_frames"
            folder.mkdir(parents=True, exist_ok=True)
            stem = hashlib.sha1(os.path.abspath(path).encode("utf-8")).hexdigest()[:12]
            frames = []
            for label, second in zip(("first", "middle", "last"), times):
                output = folder / f"{stem}_{label}.jpg"
                result = subprocess.run(
                    [ffmpeg, "-y", "-ss", f"{second:.3f}", "-i", path,
                     "-frames:v", "1", "-q:v", "3", str(output)],
                    capture_output=True, timeout=20, check=False)
                if result.returncode == 0 and output.exists():
                    frames.append(str(output))
            return frames
        except (OSError, subprocess.SubprocessError, ValueError):
            return []

    @staticmethod
    def _ground_line_signature(path: str):
        """Return dominant lower-frame geometry for endpoint continuity review."""
        if not path or not os.path.exists(path):
            return {}
        try:
            import cv2
            import numpy as np
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None or image.size == 0:
                return {}
            height, width = image.shape[:2]
            scale = min(1.0, 720.0 / max(width, height))
            if scale < 1.0:
                image = cv2.resize(image, None, fx=scale, fy=scale,
                                   interpolation=cv2.INTER_AREA)
                height, width = image.shape[:2]
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (5, 5), 0)
            edges = cv2.Canny(gray, 55, 150)
            # Ground structure lives mainly below the horizon. Excluding the
            # upper third avoids hair, windows and ceiling trim dominating.
            edges[:int(height * 0.34), :] = 0
            lines = cv2.HoughLinesP(
                edges, 1, np.pi / 180, threshold=max(28, width // 18),
                minLineLength=max(28, width // 7), maxLineGap=max(12, width // 30))
            values = []
            if lines is not None:
                for raw in lines[:, 0, :]:
                    x1, y1, x2, y2 = (float(value) for value in raw)
                    length = float(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5)
                    if length < width * 0.14:
                        continue
                    angle = float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
                    while angle > 90:
                        angle -= 180
                    while angle < -90:
                        angle += 180
                    # Near-vertical character silhouettes are not ground lines.
                    if abs(angle) > 78:
                        continue
                    values.append({
                        "angle":round(angle, 2),
                        "mid_y":round(((y1 + y2) * 0.5) / height, 4),
                        "mid_x":round(((x1 + x2) * 0.5) / width, 4),
                        "length":round(length / width, 4),
                    })
            values.sort(key=lambda value: value["length"], reverse=True)
            return {"width":width, "height":height, "lines":values[:8]}
        except (ImportError, OSError, ValueError, TypeError):
            return {}

    @staticmethod
    def _compare_ground_line_signatures(expected: dict, actual: dict):
        expected_lines = list((expected or {}).get("lines") or [])
        actual_lines = list((actual or {}).get("lines") or [])
        if not expected_lines or not actual_lines:
            return None
        best = None
        # Compare the strongest expected floor edges against every actual edge.
        # Midpoint position participates, so a same-angle line on the opposite
        # side of frame does not count as the same piece of spatial geometry.
        for wanted in expected_lines[:4]:
            for found in actual_lines[:8]:
                angle_delta = abs(float(wanted["angle"]) - float(found["angle"]))
                angle_delta = min(angle_delta, 180.0 - angle_delta)
                y_delta = abs(float(wanted["mid_y"]) - float(found["mid_y"]))
                x_delta = abs(float(wanted["mid_x"]) - float(found["mid_x"]))
                cost = angle_delta / 30.0 + y_delta * 1.8 + x_delta * 0.35
                candidate = {
                    "angle_delta":round(angle_delta, 2),
                    "vertical_delta":round(y_delta, 4),
                    "horizontal_delta":round(x_delta, 4),
                    "cost":round(cost, 4),
                }
                if best is None or candidate["cost"] < best["cost"]:
                    best = candidate
        return best

    def _run_spatial_consistency_review(self, record: dict, frames: list[str]):
        """Compare rendered video endpoints with the approved clean anchors."""
        frames = [str(value) for value in frames if value and os.path.exists(str(value))]
        pairs = []
        first = str(record.get("first_frame") or "")
        last = str(record.get("last_frame") or "")
        if first and frames:
            pairs.append(("起始帧", first, frames[0]))
        if last and frames:
            pairs.append(("结束帧", last, frames[-1]))
        checks = []
        issues = []
        for label, anchor, rendered in pairs:
            comparison = self._compare_ground_line_signatures(
                self._ground_line_signature(anchor),
                self._ground_line_signature(rendered))
            if comparison is None:
                checks.append({"endpoint":label, "status":"no_line_evidence"})
                continue
            status = "pass"
            if (comparison["angle_delta"] > 18.0 or
                    comparison["vertical_delta"] > 0.20 or
                    comparison["horizontal_delta"] > 0.34):
                status = "warn"
                issues.append(
                    f"{label}地面结构偏移：方向差 {comparison['angle_delta']:g}°，"
                    f"垂直位置差 {comparison['vertical_delta']:.0%}")
            checks.append({"endpoint":label, "status":status, **comparison})
        comparable = [value for value in checks if "angle_delta" in value]
        status = "warn" if issues else "pass"
        score = max(0, 100 - sum(
            min(45, int(value.get("angle_delta", 0) * 1.2 +
                        value.get("vertical_delta", 0) * 90 +
                        value.get("horizontal_delta", 0) * 25))
            for value in comparable))
        result = {
            "status":status, "score":score, "issues":issues,
            "checks":checks,
            "evidence":("endpoint_ground_geometry" if comparable else
                        "no_stable_ground_line_detected"),
            "reviewed_at":datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        record["spatial_review"] = result
        return result

    def _run_image_spatial_qc(self, record: dict, candidate_path: str):
        frame_role = str(record.get("frame_role") or "start")
        if frame_role == "end":
            reference = str(record.get("endpoint_source_path") or "")
        else:
            reference = str(record.get("scene_view_path") or
                            record.get("scene_master_path") or "")
        if not reference or not os.path.exists(reference):
            return {"status":"unavailable", "issues":["SCENE_AUTHORITY_MISSING"]}
        protected = fixture_view_bboxes(
            record.get("scene_proxy") or {}, str(record.get("scene_view_id") or "master"))
        result = compare_fixed_regions(
            reference, candidate_path, record.get("editable_bbox_xy"), protected)
        if frame_role == "end":
            endpoint = compare_endpoint_paths(reference, candidate_path)
            result["endpoint_comparison"] = endpoint
            if endpoint.get("status") != "pass":
                if result.get("status") != "fail":
                    result["status"] = endpoint.get("status") or "unavailable"
                result.setdefault("issues", []).extend(
                    issue for issue in endpoint.get("issues", [])
                    if issue not in result.get("issues", []))
        if (str(record.get("spatial_qc_mode") or "") == "recompose" and
                result.get("status") == "fail"):
            result["status"] = "review"
            result.setdefault("issues", []).append(
                "镜头按新机位重新构图，请确认场景拓扑与固定设备关系")
        result["protected_fixture_count"] = len(protected)
        result["reference_path"] = reference
        result["frame_role"] = frame_role
        return result

    @staticmethod
    def _media_signature(path: str):
        try:
            stat = os.stat(path)
            return hashlib.sha1(
                f"{os.path.abspath(path)}|{stat.st_mtime_ns}|{stat.st_size}".encode(
                    "utf-8")).hexdigest()
        except OSError:
            return ""

    @staticmethod
    def _qc_chat_model():
        try:
            from api_config import get as api_get
            return api_get("llm").default_model or "gpt-5.5"
        except Exception:
            return "gpt-5.5"

    def _openai_chat_provider(self):
        providers = get_ai_manager().registry.by_capability("chat")
        return next((value for value in providers if value.name == "openai"), None)

    def _source_for_workflow_group(self, group_id: str):
        group = self._custom_record(str(group_id or ""))
        return str((group or {}).get("source_node_id") or
                   self._current_production_source_id())

    def _production_source_for_generator(self, node_id: str) -> str:
        """Resolve the storyboard project that owns a generated media node."""
        node_id = str(node_id or "")
        for edge in self._positions().get("__workflow_edges__", []):
            if (not isinstance(edge, dict) or
                    str(edge.get("target") or "") != node_id or
                    str(edge.get("type") or "") != "group"):
                continue
            group = self._custom_record(str(edge.get("source") or "")) or {}
            source_id = str(group.get("source_node_id") or "")
            if source_id and group.get("generator_kind") in {"image", "video"}:
                return source_id
        return ""

    def _mark_clip_qc_unavailable(self, generator_id: str, source_id: str,
                                  reason: str):
        record = self._custom_record(generator_id)
        if record is None:
            return
        record["clip_qc"] = {
            "kind":"clip_qc", "status":"unavailable", "passed":False,
            "score":0, "shots":[], "summary":str(reason or "视觉模型不可用"),
            "reviewed_at":datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        record["handoff_approved"] = False
        provider = str(record.get("actual_provider") or "视频模型")
        record["status"] = f"生成完成 · {provider} · 自动审片暂不可用"
        source = self._custom_record(source_id)
        if source is not None:
            append_generation_event(source, {
                "provider":record.get("actual_provider") or record.get("provider_name"),
                "model":record.get("actual_model") or record.get("model") or
                        record.get("actual_provider") or record.get("provider_name"),
                "operation":"video", "prompt":record.get("content") or "",
                "references":record.get("references") or [],
                "outcome":"qc_unavailable", "failure_codes":["QC_UNAVAILABLE"],
            })
            source["pipeline_stage"] = "video_qc_pending"
            source["status"] = "视频已生成 · 自动审片部分不可用，正在汇总其余证据"

    def _submit_video_clip_qc(self, record: dict, workflow_group_id=""):
        """Submit POST-QC immediately after a rendered video has real frame evidence."""
        generator_id = str(record.get("id") or "")
        path = str(record.get("path") or "")
        source_id = self._source_for_workflow_group(workflow_group_id)
        if workflow_group_id:
            record["workflow_group_id"] = str(workflow_group_id)
        signature = self._media_signature(path)
        if not generator_id or not signature:
            return False
        current = record.get("clip_qc") if isinstance(record.get("clip_qc"), dict) else {}
        if (record.get("clip_qc_signature") == signature and
                str(current.get("status") or "") in {"complete", "unavailable"}):
            return False
        if any(str(task.get("kind") or "") == "clip_qc" and
               str(task.get("node_id") or "") == generator_id
               for task in self._standalone_tasks.values()):
            return False
        frames = [str(value) for value in record.get("video_review_frames", [])
                  if value and os.path.exists(str(value))]
        if not frames:
            frames = self._extract_video_review_frames(path)
            record["video_review_frames"] = frames
            if frames:
                record["video_tail_frame"] = frames[-1]
        record["deterministic_qc"] = inspect_frame_paths(frames)
        av_sync = inspect_av_sync(path)
        record["deterministic_qc"]["av_sync"] = av_sync
        if av_sync.get("status") == "fail":
            record["deterministic_qc"]["status"] = "fail"
            record["deterministic_qc"].setdefault("issues", []).extend(
                value for value in av_sync.get("issues", [])
                if value not in record["deterministic_qc"].get("issues", []))
        provider = self._openai_chat_provider()
        if provider is None or not frames:
            self._mark_clip_qc_unavailable(
                generator_id, source_id,
                "没有可用的 OpenAI 多模态审片模型" if provider is None else
                "视频抽帧失败，无法建立视觉证据")
            return False
        shot_ids = [str(value) for value in
                    (record.get("shot_ids") or [record.get("shot_id")]) if value]
        shots = [self._find_shot(value) for value in shot_ids]
        shots = [value for value in shots if value is not None]
        payload = [{
            "id":shot.get("id"), "number":shot.get("number"),
            "visual":shot.get("visual"), "action":shot.get("action_line"),
            "duration":shot.get("duration"), "dialogue":shot.get("dialogue"),
            "blocking":shot.get("blocking"), "axis_rule":shot.get("axis_rule"),
            "frame_start":shot.get("frame_start"), "frame_end":shot.get("frame_end"),
            "character_positions":shot.get("character_positions"),
            "camera_position":shot.get("camera_position"),
            "camera_movement":shot.get("camera_movement"),
            "continuity_invariants":shot.get("continuity_invariants"),
        } for shot in shots]
        system = (
            "你是电影成片 POST-QC 审片师。只输出 JSON 对象："
            '{"summary":"总评","score":0,"passed":false,"shots":['
            '{"id":"镜头id","score":0,"passed":false,'
            '"categories":{"G1":0,"G2":0,"G3":0,"G4":0,"G5":0,"G6":0},'
            '"blockers":["F1"],"issues":["可验证问题"],'
            '"issue_codes":["IDENTITY_DRIFT"],'
            '"repair_target":"asset|blocking|prompt|image|video|audio",'
            '"revision":"最小修复要求"}]}。'
            "G1身份服装道具25分，G2空间轴线视线20分，G3动作时序20分，"
            "G4构图运镜10分，G5畸形闪烁文字水印参考线污染15分，G6叙事对白口型10分。"
            "F1身份错乱、F2空间/越轴阻断、F3严重肢体或画面污染、F4动作断裂、"
            "F5关键叙事缺失、F6声音/口型不可用均为一票否决。80分且无 blocker 才通过。"
            "必须依据附带的首中尾真实视频帧和批准锚点，不能只看提示词。"
            "repair_target 指向最早能阻止复发的阶段；不要建议重做无关镜头。")
        content = [{"type":"text", "text":json.dumps({
            "shots":payload, "deterministic_qc":record.get("deterministic_qc", {})
        }, ensure_ascii=False)}]
        evidence = [
            ("场景母版", record.get("scene_master_path")),
            ("批准 K1", record.get("first_frame")),
            ("批准 Klast", record.get("last_frame")),
        ]
        evidence.extend(zip(("视频首帧", "视频中帧", "视频尾帧"), frames))
        seen = set()
        for label, raw_path in evidence:
            frame = str(raw_path or "")
            if not frame or frame in seen or not os.path.exists(frame):
                continue
            seen.add(frame)
            data_url = self._local_image_data_url(frame)
            if data_url:
                content.append({"type":"text", "text":label})
                content.append({"type":"image_url",
                                "image_url":{"url":data_url, "detail":"low"}})
        try:
            handle = get_ai_manager().submit(provider.name, TaskRequest(
                operation="chat", inputs={"messages":[
                    {"role":"system", "content":system},
                    {"role":"user", "content":content},
                ]}, params={"model":self._qc_chat_model()},
                metadata={"canvas_node_id":generator_id, "purpose":"clip_qc"},
                use_cache=False))
            self._standalone_tasks[handle.id] = {
                "handle":handle, "node_id":generator_id, "provider":provider.name,
                "kind":"clip_qc", "source_id":source_id,
                "workflow_group_id":str(workflow_group_id or ""),
                "qc_signature":signature, "shot_ids":shot_ids,
            }
            record["clip_qc_signature"] = signature
            record["clip_qc"] = {"status":"pending", "kind":"clip_qc"}
            record["status"] = "视频已生成 · 自动审片中"
            source = self._custom_record(source_id)
            if source is not None:
                source["pipeline_stage"] = "video_qc_pending"
                source["status"] = "视频已生成 · 正在逐段自动审片"
            self._save_layout_now()
            return True
        except Exception as error:
            self._mark_clip_qc_unavailable(generator_id, source_id, str(error))
            return False

    def _apply_qc_repair_rows(self, rows: list[dict]):
        shots = self.current_storyboard().get("shots", [])
        plan = build_repair_plan({"summary":"自动审片局部修复", "shots":rows}, shots)
        by_id = {str(value.get("shot_id") or ""):value
                 for value in plan.get("items", [])}
        for shot in shots:
            item = by_id.get(str(shot.get("id") or ""))
            if item is None:
                continue
            shot["production_selected"] = True
            shot["repair_plan"] = item
            shot["repair_target"] = item["target"]
            revision = str(item.get("revision") or "").strip()
            if item["target"] == "image" and revision:
                base = str(shot.get("final_image_prompt") or shot.get("visual") or "")
                addition = "自动审片修复：" + revision
                if addition not in base:
                    shot["final_image_prompt"] = base + ("。" if base else "") + addition
            elif item["target"] == "video" and revision:
                base = str(shot.get("final_video_prompt") or shot.get("action_line") or "")
                addition = "自动审片修复：" + revision
                if addition not in base:
                    shot["final_video_prompt"] = base + ("。" if base else "") + addition
            elif item["target"] == "prompt":
                shot["prompt_repair_instruction"] = revision
                shot["production_ready"] = False
            elif item["target"] in {"asset", "blocking"}:
                shot["production_ready"] = False
        return plan

    def _apply_clip_qc_result(self, generator_id: str, source_id: str,
                              review: dict, shot_ids: list[str]):
        record = self._custom_record(generator_id)
        # Choosing/adopting a candidate is the producer's explicit approval.
        # Automatic QC remains useful evidence, but must not overrule that
        # human decision or trap the serial production workflow.
        human_approved = bool((record or {}).get("adopted"))
        normalized = normalize_clip_qc(
            review, shot_ids, deterministic_qc=(record or {}).get("deterministic_qc"))
        normalized["status"] = "complete"
        if human_approved:
            normalized["risk_accepted"] = True
            normalized["human_approved"] = True
        if record is not None:
            record["clip_qc"] = normalized
            record["handoff_approved"] = bool(
                record.get("adopted") and (normalized["passed"] or human_approved))
            provider = str(record.get("actual_provider") or "视频模型")
            record["status"] = (
                f"生成完成 · {provider} · 审片 {normalized['score']} 分"
                if normalized["passed"] else
                f"生成完成 · {provider} · 审片未通过 {normalized['score']} 分")
        rows = normalized.get("shots", [])
        by_id = {str(value.get("id") or ""):value for value in rows}
        for shot_id in shot_ids:
            shot = self._find_shot(shot_id)
            row = by_id.get(shot_id) or (rows[0] if len(rows) == 1 else None)
            if shot is None or row is None:
                continue
            shot["clip_qc"] = row
            shot["quality_score"] = int(row.get("score") or 0)
            shot["quality_passed"] = bool(row.get("passed"))
        failed = [value for value in rows if not value.get("passed")]
        repair_plan = (
            build_repair_plan(
                {"summary":"人工通过视频的自动审片建议",
                 "score":normalized.get("score", 0), "shots":failed},
                self.current_storyboard().get("shots", []))
            if failed and human_approved else
            self._apply_qc_repair_rows(failed) if failed else {
            "summary":"逐段审片通过", "score":normalized.get("score", 0),
            "items":[], "counts":{}, "ready":True,
        })
        severity = str(normalized.get("severity") or
                       ("info" if normalized.get("passed") else "block"))
        retry_stop = False
        if record is not None:
            if normalized["passed"]:
                record["qc_failure_count"] = 0
                record["retry_stop"] = False
            elif severity == "block" and not human_approved:
                record["qc_failure_count"] = int(record.get("qc_failure_count") or 0) + 1
                retry_stop = record["qc_failure_count"] >= 3
                record["retry_stop"] = retry_stop
            else:
                record["qc_review_count"] = int(record.get("qc_review_count") or 0) + 1
            record["status"] = (
                "审片通过 · 可继续交接" if normalized["passed"] else
                "人工已通过 · 自动审片意见已保留 · 可继续交接" if human_approved else
                "连续3次硬阻断 · 停止抽卡并重新设计镜头" if retry_stop else
                "人工复核 · 可明确接受或局部修复" if severity == "review" else
                f"硬阻断 · 第 {int(record.get('qc_failure_count') or 1)}/3 次")
        if retry_stop:
            stop_instruction = (
                "同一视频段已连续3次硬阻断：停止继续抽卡。请缩短时长、减少动作、"
                "拆成两个镜头，或重新制作关键帧后再生成。")
            for item in repair_plan.get("items", []):
                if item.get("target") not in {"asset", "image"}:
                    item["target"] = "blocking"
                    item["rewind_step"] = 3
                    item["generator_kind"] = ""
                    item["preserve"] = ["locked_assets"]
                item["revision"] = (str(item.get("revision") or "").strip() +
                                    ("；" if item.get("revision") else "") +
                                    stop_instruction)
                shot = self._find_shot(str(item.get("shot_id") or ""))
                if shot is not None:
                    shot["retry_stop"] = True
                    shot["production_ready"] = False
                    shot["repair_target"] = item["target"]
                    shot["repair_plan"] = item
                    shot["retry_stop_instruction"] = stop_instruction
        source = self._custom_record(source_id)
        if source is not None:
            generator = record or {}
            append_generation_event(source, {
                "provider":generator.get("actual_provider") or generator.get("provider_name"),
                "model":generator.get("actual_model") or generator.get("model") or
                        generator.get("actual_provider") or generator.get("provider_name"),
                "operation":"video",
                "prompt":generator.get("content") or generator.get("prompt"),
                "prompt_version":generator.get("prompt_version") or
                                 generator.get("director_contract_version"),
                "references":generator.get("references") or [],
                "seed":generator.get("seed"),
                "attempt":generator.get("attempt") or generator.get("retry_count") or 1,
                "duration_ms":generator.get("duration_ms") or generator.get("elapsed_ms") or 0,
                "cost":generator.get("cost") or 0,
                "currency":generator.get("currency") or "",
                "outcome":"passed" if normalized["passed"] else "qc_failed",
                "failure_codes":[code for row in failed
                                 for code in row.get("issue_codes", [])],
                "adopted":bool(normalized["passed"] and generator.get("adopted", False)),
                "shot_signature":shot_signature(
                    self._find_shot(shot_ids[0]) or {}) if shot_ids else {},
            })
            known_providers = sorted({str(item.get("provider") or "") for item in
                                     source.get("generation_trace", []) if item.get("provider")})
            source["model_routing"] = rank_providers(
                source.get("generation_trace", []), known_providers)
            source["pipeline_stage"] = (
                "video_qc_pending" if normalized["passed"] or human_approved
                else "video_qc_review")
            source["status"] = (
                "当前定稿视频审片通过 · 正在继续下一段"
                if normalized["passed"] else
                "当前视频已由你通过 · 审片意见仅供参考 · 正在继续下一段"
                if human_approved else
                "当前视频段需要人工复核 · 可明确接受后继续"
                if severity == "review" else
                "同一视频段连续3次硬阻断 · 已停止抽卡并要求重新设计"
                if retry_stop else
                f"当前视频段硬阻断 · 第 {int((record or {}).get('qc_failure_count') or 1)}/3 次 · 请改选候选")
            if normalized["passed"] or human_approved:
                source.pop("approval_required", None)
                source.pop("awaiting_gate", None)
                source["auto_run_enabled"] = True
            else:
                source["approval_required"] = (
                    "shot_redesign" if retry_stop else
                    "video_review_decision" if severity == "review" else
                    "replacement_video_candidate")
                source["awaiting_gate"] = "video_qc"
                source["auto_run_enabled"] = False
        group_id = str((record or {}).get("workflow_group_id") or "")
        group = self._custom_record(group_id) if group_id else None
        if group is not None:
            group["status"] = (
                "当前段审片通过 · 准备下一段" if normalized["passed"] else
                "当前段人工已通过 · 准备下一段" if human_approved else
                "当前段需人工复核 · 已暂停" if severity == "review" else
                "已触发3次止损 · 必须重新设计镜头" if retry_stop else
                "当前段硬阻断 · 已暂停，等待改选候选")
        if source is not None and not normalized["passed"]:
            issue_lines = [
                ("审片结论：硬阻断" if severity == "block" else
                 "审片结论：人工复核"),
                f"当前段得分：{int(normalized.get('score') or 0)}",
            ]
            if record is not None and severity == "block":
                issue_lines.append(
                    f"连续硬阻断：{int(record.get('qc_failure_count') or 0)}/3")
            if retry_stop:
                issue_lines.append("止损动作：停止抽卡，回退关键帧/调度/拆镜。")
            for row in failed[:6]:
                issue_lines.extend(str(value) for value in row.get("issues", [])[:2])
            qc_node_id = self._upsert_auto_qc_node(
                source_id, "\n".join(issue_lines),
                ("人工已通过 · 审片意见仅供参考" if human_approved else
                 "已触发3次止损 · 必须重新设计" if retry_stop else
                 "硬阻断 · 请改选或修复" if severity == "block" else
                 "人工复核 · 可接受风险或修复"),
                {"clips":[normalized], "score":normalized.get("score", 0),
                 "severity":severity, "retry_stop":retry_stop},
                repair_plan)
            source["repair_plan"] = repair_plan
            source["automatic_qc"] = {
                "score":int(normalized.get("score") or 0),
                "passed":False, "severity":severity,
                "failed_clips":1, "failed_transitions":0,
                "unavailable":0, "node_id":qc_node_id,
                "retry_stop":retry_stop and not human_approved,
                "human_approved":human_approved,
            }
        self.storyboardMutated.emit(); self._save_layout_now()
        return normalized

    def _sequence_transitions(self):
        clips = self._combined_preview_inputs()
        values = []
        for left, right in zip(clips, clips[1:]):
            left_record = self._custom_record(left.get("generator_id")) or {}
            right_record = self._custom_record(right.get("generator_id")) or {}
            left_frames = [str(value) for value in left_record.get("video_review_frames", [])
                           if value and os.path.exists(str(value))]
            right_frames = [str(value) for value in right_record.get("video_review_frames", [])
                            if value and os.path.exists(str(value))]
            if not left_frames:
                left_frames = self._extract_video_review_frames(left["path"])
            if not right_frames:
                right_frames = self._extract_video_review_frames(right["path"])
            if not left_frames or not right_frames:
                continue
            from_shot = left["shots"][-1]
            to_shot = right["shots"][0]
            link_mode = resolve_video_link_mode(from_shot, to_shot)
            endpoint_qc = (compare_endpoint_paths(left_frames[-1], right_frames[0])
                           if link_mode in {"continue", "bridge"} else
                           {"status":"skipped", "issues":[],
                            "reason":"hard_cut_allows_composition_change"})
            values.append({
                "from_id":str(from_shot.get("id") or ""),
                "to_id":str(to_shot.get("id") or ""),
                "from_number":from_shot.get("number"),
                "to_number":to_shot.get("number"),
                "out_frame":left_frames[-1], "in_frame":right_frames[0],
                "from_state":from_shot.get("frame_end"),
                "to_state":to_shot.get("frame_start"),
                "from_axis":from_shot.get("axis_rule"),
                "to_axis":to_shot.get("axis_rule"),
                "from_positions":from_shot.get("character_positions"),
                "to_positions":to_shot.get("character_positions"),
                "transition":to_shot.get("transition"),
                "video_link_mode":link_mode,
                "deterministic_qc":endpoint_qc,
            })
        return values

    def _maybe_start_sequence_qc(self, source_id: str):
        source_id = str(source_id or "")
        source = self._custom_record(source_id)
        if source is None:
            return False
        if any(str(task.get("kind") or "") in {"clip_qc", "sequence_qc"} and
               str(task.get("source_id") or "") == source_id
               for task in self._standalone_tasks.values()):
            return False
        group = self._latest_production_group(source_id, "video")
        if group is not None:
            generators = [self._custom_record(str(value)) or {}
                          for value in group.get("group_nodes", [])]
            if (not generators or any(
                    not value.get("adopted") or
                    not value.get("handoff_approved") or
                    not bool((value.get("clip_qc") or {}).get("passed") or
                             (value.get("clip_qc") or {}).get("risk_accepted"))
                    for value in generators)):
                return False
        clips = self._combined_preview_inputs()
        if not clips:
            return False
        pending = []
        for clip in clips:
            record = self._custom_record(str(clip.get("generator_id") or "")) or {}
            qc = record.get("clip_qc") if isinstance(record.get("clip_qc"), dict) else {}
            if str(qc.get("status") or "") not in {"complete", "unavailable"}:
                pending.append(str(clip.get("generator_id") or clip["path"]))
        if pending:
            return False
        transitions = self._sequence_transitions()
        source["sequence_deterministic_qc"] = [{
            "from_id":item.get("from_id"), "to_id":item.get("to_id"),
            "deterministic_qc":item.get("deterministic_qc", {})
        } for item in transitions]
        signature = self._preview_file_signature(
            [clip["path"] for clip in clips] +
            [value for item in transitions for value in
             (item["out_frame"], item["in_frame"])])
        if (source.get("sequence_qc_signature") == signature and
                isinstance(source.get("sequence_qc"), dict)):
            self._finalize_video_qc(source_id, source["sequence_qc"])
            return True
        source["sequence_qc_signature"] = signature
        source.pop("quality_risk_accepted", None)
        if not transitions:
            self._finalize_video_qc(source_id, normalize_sequence_qc({
                "summary":"只有一个连续视频段，无片段边界需要检查",
                "score":100, "passed":True, "transitions":[],
            }))
            return True
        provider = self._openai_chat_provider()
        if provider is None:
            unavailable = normalize_sequence_qc({
                "summary":"OpenAI 多模态审片模型不可用，序列检查未执行",
                "score":100, "passed":True, "transitions":[],
            })
            unavailable["status"] = "unavailable"
            self._finalize_video_qc(source_id, unavailable)
            return True
        payload = [{key:value for key, value in item.items()
                    if key not in {"out_frame", "in_frame"}}
                   for item in transitions[:12]]
        system = (
            "你是电影 SEQUENCE-QC 连续性监督。只输出 JSON 对象："
            '{"summary":"总评","score":0,"passed":false,"transitions":['
            '{"from_id":"前镜id","to_id":"后镜id","score":0,"passed":false,'
            '"blockers":["F2"],"issues":["问题"],'
            '"issue_codes":["SCREEN_DIRECTION_FLIP"],'
            '"repair_target":"asset|blocking|prompt|image|video|audio",'
            '"revision":"最小修复要求"}]}。'
            "逐对比较前段真实尾帧与后段真实首帧，检查角色身份服装、道具状态、光线时刻、"
            "动作相位、站位深度、左右关系、180度轴线、屏幕运动方向、视线和场景几何。"
            "硬切允许构图变化，但不允许世界状态凭空改变。85分且无 F1-F6 blocker 才通过。"
            "只标记确有视觉证据的问题，不要要求重做无关片段。")
        content = [{"type":"text", "text":json.dumps(payload, ensure_ascii=False)}]
        for item in transitions[:12]:
            for label, path in (("前段真实尾帧", item["out_frame"]),
                                ("后段真实首帧", item["in_frame"])):
                data_url = self._local_image_data_url(path)
                if data_url:
                    content.append({"type":"text", "text":(
                        f"{item['from_id']} → {item['to_id']} · {label}")})
                    content.append({"type":"image_url",
                                    "image_url":{"url":data_url, "detail":"low"}})
        try:
            handle = get_ai_manager().submit(provider.name, TaskRequest(
                operation="chat", inputs={"messages":[
                    {"role":"system", "content":system},
                    {"role":"user", "content":content},
                ]}, params={"model":self._qc_chat_model()},
                metadata={"canvas_node_id":source_id, "purpose":"sequence_qc"},
                use_cache=False))
            self._standalone_tasks[handle.id] = {
                "handle":handle, "node_id":source_id, "provider":provider.name,
                "kind":"sequence_qc", "source_id":source_id,
                "qc_signature":signature,
            }
            source["pipeline_stage"] = "video_qc_pending"
            source["status"] = f"单段审片完成 · 正在检查 {len(transitions)} 个片段边界"
            self._save_layout_now(); self._update_production_continue_button()
            return True
        except Exception as error:
            unavailable = normalize_sequence_qc({
                "summary":f"序列审片提交失败：{error}",
                "score":100, "passed":True, "transitions":[],
            })
            unavailable["status"] = "unavailable"
            self._finalize_video_qc(source_id, unavailable)
            return False

    def _upsert_auto_qc_node(self, source_id: str, content: str,
                             status: str, review: dict, repair_plan: dict):
        node_id = f"auto-qc:{source_id}"
        values = self._positions().setdefault("__custom_nodes__", [])
        record = self._custom_record(node_id)
        payload = {
            "id":node_id, "type":"skill_node", "title":"自动审片 · POST + SEQUENCE",
            "content":content, "status":status, "skill_id":"vision_qc_repair",
            "source_node_id":source_id, "auto_qc_kind":"post_sequence",
            "review":review, "repair_plan":repair_plan,
        }
        if record is None:
            values.append(payload)
            source_pos = self._positions().get(source_id, [0, 0])
            self._positions()[node_id] = [float(source_pos[0]) + 560.0,
                                          float(source_pos[1]) + 300.0]
        else:
            record.update(payload)
        edge = {"source":source_id, "target":node_id, "type":"quality_review"}
        edges = self._positions().setdefault("__workflow_edges__", [])
        if edge not in edges:
            edges.append(edge)
        source = self._custom_record(source_id)
        if source is not None:
            source["automatic_qc_node_snapshot"] = json.loads(json.dumps(
                payload, ensure_ascii=False))
            source["automatic_qc_hidden"] = False
        return node_id

    def _restore_auto_qc_node(self, source_id: str) -> bool:
        """Restore a hidden QC report without clearing its blocking result."""
        source_id = str(source_id or "")
        node_id = f"auto-qc:{source_id}"
        if node_id in self._nodes:
            return True
        source = self._custom_record(source_id)
        if source is None:
            return False
        snapshot = source.get("automatic_qc_node_snapshot")
        if isinstance(snapshot, dict):
            payload = json.loads(json.dumps(snapshot, ensure_ascii=False))
        else:
            automatic = (source.get("automatic_qc")
                         if isinstance(source.get("automatic_qc"), dict) else {})
            repair_plan = (source.get("repair_plan")
                           if isinstance(source.get("repair_plan"), dict) else {})
            failed_clips = int(automatic.get("failed_clips") or 0)
            failed_transitions = int(automatic.get("failed_transitions") or 0)
            unavailable = int(automatic.get("unavailable") or 0)
            score = int(automatic.get("score") or 0)
            lines = [
                f"综合审片：{score} 分",
                f"未通过视频段：{failed_clips}",
                f"未通过镜头边界：{failed_transitions}",
            ]
            if unavailable:
                lines.append(f"证据不可用：{unavailable} 段")
            lines.append("详细报告节点曾被删除，已依据项目内保留的审片状态重建。")
            payload = {
                "id":node_id, "type":"skill_node",
                "title":"自动审片 · POST + SEQUENCE",
                "content":"\n".join(lines),
                "status":"存在阻断问题 · 报告已恢复",
                "skill_id":"vision_qc_repair", "source_node_id":source_id,
                "auto_qc_kind":"post_sequence",
                "review":{"score":score}, "repair_plan":repair_plan,
            }
        payload.update({
            "id":node_id, "type":"skill_node", "source_node_id":source_id,
            "auto_qc_kind":"post_sequence", "skill_id":"vision_qc_repair",
        })
        values = self._positions().setdefault("__custom_nodes__", [])
        values[:] = [value for value in values
                     if not (isinstance(value, dict) and
                             str(value.get("id") or "") == node_id)]
        values.append(payload)
        source_pos = self._positions().get(source_id, [0, 0])
        self._positions()[node_id] = [float(source_pos[0]) + 560.0,
                                      float(source_pos[1]) + 300.0]
        edge = {"source":source_id, "target":node_id, "type":"quality_review"}
        edges = self._positions().setdefault("__workflow_edges__", [])
        if edge not in edges:
            edges.append(edge)
        source["automatic_qc_hidden"] = False
        source["status"] = str(
            source.pop("automatic_qc_status_before_hide", "") or
            "自动审片未通过 · 请查看报告并选择局部重做或接受风险")
        self._save_layout_now()
        self.refresh()
        return node_id in self._nodes

    def _finalize_video_qc(self, source_id: str, sequence_review: dict):
        source = self._custom_record(source_id)
        if source is None:
            return
        sequence = (sequence_review if sequence_review.get("kind") == "sequence_qc"
                    else normalize_sequence_qc(sequence_review))
        deterministic_by_pair = {
            (str(item.get("from_id") or ""), str(item.get("to_id") or "")):
            item.get("deterministic_qc", {})
            for item in source.get("sequence_deterministic_qc", [])
            if isinstance(item, dict)
        }
        raw_transitions = [dict(item) for item in sequence.get("transitions", [])
                           if isinstance(item, dict)]
        present = {(str(item.get("from_id") or ""), str(item.get("to_id") or ""))
                   for item in raw_transitions}
        for item in raw_transitions:
            item["deterministic_qc"] = deterministic_by_pair.get(
                (str(item.get("from_id") or ""), str(item.get("to_id") or "")), {})
        for pair, deterministic in deterministic_by_pair.items():
            if pair not in present and deterministic.get("status") == "fail":
                raw_transitions.append({
                    "from_id":pair[0], "to_id":pair[1], "score":0,
                    "passed":False, "issues":["确定性端点连续性检测未通过"],
                    "repair_target":"video", "deterministic_qc":deterministic,
                })
        sequence = normalize_sequence_qc({
            "summary":sequence.get("summary"), "score":sequence.get("score"),
            "passed":sequence.get("passed"), "transitions":raw_transitions,
        })
        source["sequence_qc"] = sequence
        clip_reviews = []
        failed_rows = []
        unavailable = 0
        preview_clips = self._combined_preview_inputs()
        all_clips_human_approved = bool(preview_clips)
        for clip in preview_clips:
            record = self._custom_record(str(clip.get("generator_id") or "")) or {}
            all_clips_human_approved = bool(
                all_clips_human_approved and record.get("adopted"))
            qc = record.get("clip_qc") if isinstance(record.get("clip_qc"), dict) else {}
            if qc:
                clip_reviews.append(qc)
            if qc.get("status") == "unavailable":
                unavailable += 1
            else:
                failed_rows.extend(value for value in qc.get("shots", [])
                                   if isinstance(value, dict) and not value.get("passed"))
        failed_transitions = [value for value in sequence.get("transitions", [])
                              if isinstance(value, dict) and not value.get("passed")]
        for transition in failed_transitions:
            row = {
                "id":str(transition.get("to_id") or transition.get("from_id") or ""),
                "passed":False, "issues":transition.get("issues", []),
                "issue_codes":transition.get("issue_codes", []),
                "repair_target":transition.get("repair_target") or "blocking",
                "revision":transition.get("revision") or "",
            }
            failed_rows.append(row)
            for shot_id in (transition.get("from_id"), transition.get("to_id")):
                shot = self._find_shot(str(shot_id or ""))
                if shot is not None:
                    shot.setdefault("sequence_reviews", []).append(transition)
                    shot["production_selected"] = True
        human_approved = bool(all_clips_human_approved or
                              source.get("quality_risk_accepted"))
        repair_plan = (
            build_repair_plan(
                {"summary":"人工通过成片的自动审片建议",
                 "score":sequence.get("score", 0), "shots":failed_rows},
                self.current_storyboard().get("shots", []))
            if failed_rows and human_approved else
            self._apply_qc_repair_rows(failed_rows) if failed_rows else {
            "summary":"自动审片通过", "score":sequence.get("score", 0),
            "items":[], "counts":{}, "ready":True,
        })
        failed_clip_count = sum(
            not value.get("passed", True) for value in clip_reviews
            if value.get("status") != "unavailable")
        sequence_failed = (sequence.get("status") != "unavailable" and
                           not sequence.get("passed", True))
        blocked = bool(failed_rows or failed_transitions or failed_clip_count or
                       sequence_failed or unavailable or
                       sequence.get("status") == "unavailable")
        hard_blocked = bool(
            unavailable or sequence.get("status") == "unavailable" or
            str(sequence.get("severity") or "") == "block" or
            any(str(value.get("severity") or "") == "block"
                for value in clip_reviews))
        overall_severity = "block" if hard_blocked else (
            "review" if blocked else "info")
        scores = [int(value.get("score") or 0) for value in clip_reviews
                  if value.get("status") != "unavailable"]
        if sequence.get("status") != "unavailable":
            scores.append(int(sequence.get("score") or 0))
        overall = int(round(sum(scores) / max(1, len(scores)))) if scores else 0
        lines = [
            f"POST 单段审片：{len(clip_reviews)} 段 · "
            f"{sum(not value.get('passed', True) for value in clip_reviews)} 段未通过",
            f"SEQUENCE 连续性：{len(sequence.get('transitions', []))} 个边界 · "
            f"{len(failed_transitions)} 个未通过",
            f"综合审片：{overall} 分" + (f" · {unavailable} 段缺少视觉模型证据" if unavailable else ""),
        ]
        for value in failed_rows[:10]:
            shot = self._find_shot(str(value.get("id") or "")) or {}
            issue = "、".join(value.get("issues", [])[:2]) or "需要局部复核"
            lines.append(f"镜头 {int(shot.get('number') or 0):02d} · {issue}")
        qc_node_id = self._upsert_auto_qc_node(
            source_id, "\n".join(lines),
            ("存在硬阻断 · 必须修复" if hard_blocked else
             "需要人工复核 · 可明确接受" if blocked else "自动审片通过"),
            {"clips":clip_reviews, "sequence":sequence, "score":overall},
            repair_plan)
        source["automatic_qc"] = {
            "score":overall, "passed":not blocked, "clip_reviews":len(clip_reviews),
            "failed_clips":failed_clip_count,
            "failed_transitions":len(failed_transitions), "unavailable":unavailable,
            "node_id":qc_node_id, "severity":overall_severity,
        }
        if blocked and not human_approved:
            source["pipeline_stage"] = "video_qc_review"
            source["awaiting_gate"] = "video_qc"
            source["auto_run_enabled"] = False
            source["status"] = (
                f"自动审片硬阻断 · {len(failed_rows)} 个问题 · 必须局部修复"
                if hard_blocked else
                f"自动审片需人工复核 · {len(failed_rows)} 个问题 · 可明确接受或修复")
        else:
            source["pipeline_stage"] = "video_ready"
            source.pop("awaiting_gate", None)
            source["status"] = (
                f"人工已通过 · 自动审片 {overall} 分仅供参考"
                if blocked and human_approved else
                f"自动审片通过 · {overall} 分" if not unavailable else
                f"自动审片完成 · {unavailable} 段证据不可用 · 已保留提示")
        self.storyboardMutated.emit(); self._save_layout_now(); self.refresh()
        self._update_production_continue_button()
        if str(source.get("pipeline_stage") or "") == "video_ready":
            self._schedule_auto_continue(source_id, from_async=True)

    def accept_video_qc_risk(self, source_id: str):
        source = self._custom_record(str(source_id or ""))
        if source is None or str(source.get("pipeline_stage") or "") != "video_qc_review":
            return False
        group = self._latest_production_group(str(source_id or ""), "video")
        awaiting_id = str((group or {}).get("awaiting_video_node_id") or "")
        generator = self._custom_record(awaiting_id) if awaiting_id else None
        current_qc = (generator.get("clip_qc")
                      if isinstance((generator or {}).get("clip_qc"), dict) else {})
        sequence_qc = (source.get("sequence_qc")
                       if isinstance(source.get("sequence_qc"), dict) else {})
        automatic_qc = (source.get("automatic_qc")
                        if isinstance(source.get("automatic_qc"), dict) else {})
        severity = str(current_qc.get("severity") or sequence_qc.get("severity") or "review")
        answer = QMessageBox.question(
            self, "接受审片风险并继续",
            ("当前问题属于人工复核项。采用后会继续下一视频段，"
             if generator is not None else
             "审片标记的镜头会保留，但生产线将继续生成对白音频。") +
            "这不会删除修复计划，也不会把问题标记为已修复。继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return False
        if generator is not None and group is not None:
            generator["retry_stop"] = False
            generator["handoff_approved"] = True
            generator["risk_accepted"] = True
            current_qc["risk_accepted"] = True
            generator["status"] = "人工复核项已明确接受 · 准备下一段"
            group["awaiting_video_node_id"] = ""
            group["status"] = "人工复核项已接受 · 准备下一段"
            source["pipeline_stage"] = "video_qc_pending"
            source["status"] = "已接受当前段人工复核项 · 继续下一视频段"
        else:
            source["quality_risk_accepted"] = True
            source["pipeline_stage"] = "video_ready"
            source["status"] = "已接受本轮审片风险 · 继续对白与联合预览"
        source.pop("awaiting_gate", None)
        source["auto_run_enabled"] = True
        self._save_layout_now(); self._update_production_continue_button()
        if generator is not None and group is not None:
            # Discard any stale in-memory multi-candidate tail. The resume
            # path reconstructs one unfinished segment at a time.
            self._serial_video_queues.pop(str(group.get("id") or ""), None)
            self._submit_next_serial_video(str(group.get("id") or ""))
        else:
            self._schedule_auto_continue(str(source_id), from_async=False)
        return True

    def _apply_vision_repair_plan(self, node_id: str, review: dict):
        """Persist a shot-scoped repair plan without deleting approved work."""
        shots = self.current_storyboard().get("shots", [])
        plan = build_repair_plan(review, shots)
        items = {str(value.get("shot_id") or ""): value
                 for value in plan.get("items", [])}
        results = {str(value.get("id") or ""): value
                   for value in review.get("shots", []) if isinstance(value, dict)}
        for shot in shots:
            shot_id = str(shot.get("id") or "")
            result = results.get(shot_id)
            if result is None:
                continue
            shot["director_review"] = result
            shot["quality_score"] = int(result.get("score") or 0)
            shot["quality_passed"] = bool(result.get("passed"))
            item = items.get(shot_id)
            if item is None:
                shot.pop("repair_plan", None)
                continue
            shot["production_selected"] = True
            shot["repair_plan"] = item
            shot["repair_target"] = item["target"]
            revision = str(item.get("revision") or "").strip()
            if item["target"] == "image" and revision:
                base = str(shot.get("final_image_prompt") or
                           shot.get("image_prompt") or shot.get("visual") or "")
                addition = "视觉修复：" + revision
                if addition not in base:
                    shot["final_image_prompt"] = base + ("。" if base else "") + addition
            elif item["target"] == "video" and revision:
                base = str(shot.get("final_video_prompt") or
                           shot.get("video_prompt") or shot.get("action_line") or "")
                addition = "视觉修复：" + revision
                if addition not in base:
                    shot["final_video_prompt"] = base + ("。" if base else "") + addition
            elif item["target"] == "prompt":
                shot["prompt_repair_instruction"] = revision
                shot["production_ready"] = False
            elif item["target"] in {"asset", "blocking"}:
                shot["production_ready"] = False

        record = self._custom_record(str(node_id or ""))
        if record is not None:
            lines = [f"审片 {plan.get('score', 0)} 分 · {len(items)} 镜需要局部修复"]
            for item in plan.get("items", [])[:12]:
                issue = "、".join(item.get("issues", [])[:2]) or "需要复核"
                lines.append(
                    f"镜头 {int(item.get('shot_number') or 0):02d} → 第 {item['rewind_step']} 步"
                    f"/{item['target']} · {issue}")
            record["title"] = "视觉审片与局部修复"
            record["content"] = "\n".join(lines)
            record["status"] = ("全部通过" if plan.get("ready") else
                                f"待确认 {len(items)} 个局部修复项")
            record["review"] = review
            record["repair_plan"] = plan

        source_id = str((record or {}).get("source_node_id") or
                        self._current_production_source_id())
        source = self._custom_record(source_id) if source_id else None
        if source is not None:
            source["repair_plan"] = plan
            if items:
                source["production_scope"] = "selected"
                source["approval_required"] = "repair_plan"
                source["status"] = f"视觉审片完成 · {len(items)} 镜待确认局部修复"
        self.storyboardMutated.emit(); self._save_layout_now()
        return plan

    def submit_ai_director_review(self, node, auto_retry=False):
        shots = self.current_storyboard().get("shots", [])
        if not shots:
            QMessageBox.information(self, "视觉审片与局部修复", "画布上还没有可质检的镜头。")
            return
        payload = [{"id":shot.get("id"), "number":shot.get("number"),
                    "visual":shot.get("visual"), "camera":shot.get("camera_slot"),
                    "action":shot.get("action_line"), "transition":shot.get("transition"),
                    "dialogue":shot.get("dialogue"), "duration":shot.get("duration"),
                    "shot_contract":shot.get("shot_contract"),
                    "blocking":shot.get("blocking"), "axis_rule":shot.get("axis_rule"),
                    "frame_start":shot.get("frame_start"), "frame_end":shot.get("frame_end"),
                    "has_image":bool(shot.get("selected_image_asset")),
                    "has_video":bool(shot.get("selected_video_asset"))} for shot in shots]
        system = (
            "你是严格的电影导演和 AI 生成视觉审片师。只输出 JSON 对象："
            '{"summary":"总评","score":0,"shots":[{"id":"镜头id","score":0,'
            '"passed":false,"issues":["问题"],"issue_codes":["AXIS_CROSS"],'
            '"repair_target":"asset|blocking|prompt|image|video|audio",'
            '"revision":"可直接用于对应阶段重新生成的修正要求"}]}。'
            "检查叙事信息、构图、景别节奏、180度轴线、动作与视线连续性、时长、角色/场景一致性，"
            "以及变脸、肢体畸形、文字水印、闪烁等生成风险。必须结合附带图片真实审片；"
            "没有图片的镜头只能评价设计，不能声称视觉通过。80分及以上才通过。"
            "repair_target 必须指向最早能阻止问题复发的阶段：身份源错选 asset；站位/越轴/空间 blocking；"
            "指令污染 prompt；静帧瑕疵 image；运动/闪烁/端点 video；声音或口型 audio。")
        content = [{"type":"text", "text":json.dumps(payload, ensure_ascii=False)}]
        for shot in shots[:12]:
            stills = [
                ("场景母版", shot.get("scene_master_path")),
                ("K1 已批准起始帧", shot.get("selected_image_asset")),
                ("Klast 已批准结束帧", shot.get("selected_end_image_asset")),
            ]
            seen_stills = set()
            for label, raw_path in stills:
                path = str(raw_path or "")
                if not path or path in seen_stills or not os.path.exists(path):
                    continue
                seen_stills.add(path)
                data_url = self._local_image_data_url(path)
                if data_url:
                    content.append({
                        "type":"text",
                        "text":f"镜头 {shot.get('number')}，id={shot.get('id')} · {label}"})
                    content.append({
                        "type":"image_url", "image_url":{"url":data_url, "detail":"low"}})
            video_path = str(shot.get("selected_video_asset") or "")
            frames = self._extract_video_review_frames(video_path)
            if frames:
                shot["video_review_frames"] = frames
                shot["video_tail_frame"] = frames[-1]
                for label, frame in zip(("首帧", "中帧", "尾帧"), frames):
                    frame_url = self._local_image_data_url(frame)
                    if frame_url:
                        content.append({"type":"text", "text":f"镜头 {shot.get('number')} 视频{label}"})
                        content.append({"type":"image_url", "image_url":{"url":frame_url, "detail":"low"}})
        self._submit_skill_chat(node, system, content,
                                "director_review", auto_retry)

    def submit_blocking_storyboard(self, node, generate_panels=False,
                                   _batch_index=None, _batch_size=2):
        shots = self.current_storyboard().get("shots", [])
        if not shots:
            QMessageBox.information(self, "调度故事板", "请先生成故事板镜头。")
            return
        self._bind_scene_contracts_to_shots(str(node.node_id))
        record = self._custom_record(str(node.node_id)) or {}
        batch_size = max(1, int(_batch_size or 2))
        batch_total = max(1, (len(shots) + batch_size - 1) // batch_size)
        requested_batch = (record.get("blocking_batch_next")
                           if _batch_index is None else _batch_index)
        batch_index = max(0, min(int(requested_batch or 0), batch_total - 1))
        batch_start = batch_index * batch_size
        batch_shots = shots[batch_start:batch_start + batch_size]
        system = (
            "你是电影场面调度师。把每一镜变成能约束 AI 生图的空间合同。"
            "只输出 JSON 对象，不得增删镜头："
            '{"shots":[{"id":"镜头id","spatial_layout":"固定场景结构、入口出口、家具和前中后景",'
            '"character_positions":[{"name":"角色名","start":"起点与画面归一化坐标x/y/depth",'
            '"end":"终点与画面归一化坐标x/y/depth","movement":"路径与屏幕方向",'
            '"gaze":"视线目标","facing":"身体朝向"}],'
            '"blocking":"可执行的走位说明","eyeline":"视线方向",'
            '"camera_position":"摄影机空间位置、高度、朝向和焦段",'
            '"scene_view_id":"必须绑定master/reverse/left/right中的一个权威视角",'
            '"editable_bbox_xy":[0.0,0.0,1.0,1.0],'
            '"camera_movement":"运动路径与起止构图","axis_rule":"人物关系轴/运动轴以及机位所在侧",'
            '"ground_plane":"地面材质、边界、台阶及固定标线的世界空间关系",'
            '"ground_lines":[{"name":"标线名称","start_xy":[0.0,0.0],'
            '"end_xy":[1.0,1.0],"world_relation":"与门、柱、墙等固定物的关系"}],'
            '"horizon_y":0.4,"vanishing_point_xy":[0.5,0.4],'
            '"foreground":"前景固定物","midground":"人物与动作区","background":"背景固定物",'
            '"frame_start":"本镜入场状态","frame_end":"本镜离场状态",'
            '"motion_keyframes":[{"index":1,"time_seconds":0,"label":"起始/发展/峰值/收势/结束",'
            '"composition":"该时点的景别、人物画面坐标、遮挡和前中后景",'
            '"character_state":"各人物在该时点的站位、身体朝向、姿态和动作进度",'
            '"action":"从上一关键帧到本帧发生的动作",'
            '"camera_state":"该时点摄影机位置、高度、焦段和构图",'
            '"character_arrow":"人物运动箭头的起点、终点、曲直和屏幕方向",'
            '"camera_arrow":"摄影机运动箭头的起点、终点和方向",'
            '"gaze_arrow":"视线虚线箭头起点与目标","screen_direction":"屏幕运动方向",'
            '"is_hero":false}],'
            '"continuity":"必须继承前镜、交给后镜的状态"}]}。'
            "每镜 motion_keyframes 必须严格等于输入的 motion_frame_target，按时间递增，"
            "覆盖起始、动作发展、动作峰值和结束；每帧都要有可画的不同姿势与构图，"
            "不能把同一张全景重复多次，并且只能有一个 is_hero=true。"
            "同一场景必须复用同一套地面平面、固定标线名称和世界关系；start_xy/end_xy、"
            "horizon_y、vanishing_point_xy 都是0到1画面归一化坐标，必须结合当前机位透视填写。"
            "同一场景必须使用同一套空间结构和物体方位；后一镜入场状态必须等于前一镜离场状态；"
            "反打镜头也要保持180度轴线、人物左右关系、视线和屏幕运动方向。不得使用‘自然站位’等模糊词。")
        payload = [{
            "id":shot.get("id"), "number":shot.get("number"),
            "visual":shot.get("visual"), "action":shot.get("action_line"),
            "scene":shot.get("scene_name") or shot.get("scene"),
            "scene_view_id":shot.get("scene_view_id"),
            "scene_proxy":shot.get("scene_proxy") or {},
            "editable_bbox_xy":shot.get("editable_bbox_xy"),
            "spatial_layout":shot.get("spatial_layout"),
            "character_positions":shot.get("character_positions"),
            "camera_position":shot.get("camera_position"),
            "camera_movement":shot.get("camera_movement"),
            "axis_rule":shot.get("axis_rule"),
            "ground_plane":shot.get("ground_plane"),
            "ground_lines":shot.get("ground_lines"),
            "horizon_y":shot.get("horizon_y"),
            "vanishing_point_xy":shot.get("vanishing_point_xy"),
            "frame_start":shot.get("frame_start"), "frame_end":shot.get("frame_end"),
            "duration":shot.get("duration"),
            "motion_frame_target":self._motion_frame_target(shot.get("duration")),
        } for shot in batch_shots]
        handle = self._submit_skill_chat(
            node, system, json.dumps(payload, ensure_ascii=False),
            "blocking_storyboard",
            provider_name=str(record.get("planning_provider") or ""),
            model=str(record.get("planning_model") or ""))
        if handle is not None:
            task = self._standalone_tasks.get(handle.id)
            if task is not None:
                task["generate_panels_after"] = bool(generate_panels)
                task["blocking_batch_index"] = batch_index
                task["blocking_batch_total"] = batch_total
                task["blocking_batch_size"] = batch_size
            if record is not None:
                record["status"] = (
                    f"阶段 3/6 · 正在计算站位、轴线与机位 "
                    f"({batch_index + 1}/{batch_total})")

    def prepare_canvas_blocking_storyboards(self, node):
        """Human-approved bridge from locked assets to blocking panels."""
        shots = self.current_storyboard().get("shots", [])
        if not shots:
            QMessageBox.information(self, "调度与运动分镜", "请先完成阶段 1：拆解镜头。")
            return
        asset_ids = self._storyboard_asset_node_ids(node.node_id)
        unlocked = [str((self._custom_record(value) or {}).get("title") or "资产")
                    for value in asset_ids
                    if not bool((self._custom_record(value) or {}).get("locked"))]
        if unlocked:
            QMessageBox.information(
                self, "请先确认资产",
                "阶段 3 不会自动采用候选。请先在画布逐项采用并锁定：\n" +
                "\n".join(f"• {value}" for value in unlocked))
            return
        references = []
        asset_records = [self._custom_record(node_id) or {} for node_id in asset_ids]
        for asset_kind in ("scene", "character", "element"):
            for record in asset_records:
                if str(record.get("asset_kind") or "") != asset_kind:
                    continue
                path = str(record.get("path") or "")
                if path and os.path.exists(path):
                    references.append(path)
                references.extend(
                    str(value) for value in
                    (record.get("character_reference_set") or {}).values()
                    if value and os.path.exists(str(value)))
        self._canvas_storyboard_source = str(node.node_id)
        self._canvas_storyboard_character_refs = list(dict.fromkeys(references))
        self._canvas_storyboard_previous = ""
        self._canvas_storyboard_queue = []
        record = self._custom_record(node.node_id)
        if record is not None:
            record["status"] = "阶段 3/6 · AI 正在计算站位、轴线与机位"
            record["pipeline_stage"] = "blocking_generating"
            record["approval_required"] = "storyboard_panels"
        self._save_layout_now()
        self.submit_blocking_storyboard(node, generate_panels=True)

    def extract_video_frames_to_canvas(self, node):
        path = str(node.payload.get("path") or node.thumbnail or "")
        frames = self._extract_video_review_frames(path)
        if not frames:
            QMessageBox.warning(self, "提取视频帧", "无法读取这个视频，或 FFmpeg 未能成功抽帧。")
            return
        labels = ("首帧", "中帧", "尾帧")
        edges = self._positions().setdefault("__workflow_edges__", [])
        for index, frame in enumerate(frames):
            frame_id = self.create_custom_node("image_node",
                node.pos() + QPointF(350 + index * 330, 0),
                {"title":f"{node.title} · {labels[index]}", "path":frame,
                 "content":f"来自 {Path(path).name} 的{labels[index]}"})
            edges.append({"source":node.node_id, "target":frame_id, "type":"video_frame"})
        record = self._custom_record(node.node_id)
        if record is not None:
            record["review_frames"] = frames; record["tail_frame"] = frames[-1]
            record["status"] = "首中尾帧已提取"
        self._save_layout_now(); self.refresh()

    def continue_video_from_tail(self, node, content: str):
        path = str(node.payload.get("path") or node.thumbnail or "")
        record = self._custom_record(node.node_id)
        tail = str((record or {}).get("tail_frame") or "")
        if not tail or not os.path.exists(tail):
            frames = self._extract_video_review_frames(path)
            tail = frames[-1] if frames else ""
        if not tail:
            QMessageBox.warning(self, "视频续拍", "无法取得视频尾帧。")
            return
        target_id = self.create_custom_node("video_node", node.pos() + QPointF(390, 0), {
            "title":f"{node.title} · 续拍", "content":content.strip() or "自然延续上一段动作与运镜",
            "references":[tail], "ratio":node.payload.get("ratio") or "16:9",
            "duration":node.payload.get("duration") or 5,
            "provider_name":node.payload.get("provider_name") or "",
        })
        self._positions().setdefault("__workflow_edges__", []).append(
            {"source":node.node_id, "target":target_id, "type":"tail_continuity"})
        self._save_layout_now(); self.refresh()
        if target_id in self._nodes:
            self.submit_standalone_generation(self._nodes[target_id],
                str((self._custom_record(target_id) or {}).get("content") or ""), "图生视频")

    @staticmethod
    def _image_video_prompt(base_prompt: str, creative_prompt: str) -> str:
        """Combine separately stored user intent for an image-guided video request."""
        base = str(base_prompt or "").strip()
        creative = str(creative_prompt or "").strip()
        if not creative:
            return base
        return (
            f"{base}\n\n"
            "【用户创意与动态意图】\n"
            f"{creative}\n"
            "以输入的首帧（以及提供时的尾帧）作为严格画面与主体约束；"
            "只实现上述动作、运镜、节奏和氛围变化，不重新设计关键帧内容。"
        ).strip()

    @staticmethod
    def _multi_image_director_prompt(overall: str, timeline: list[dict]) -> str:
        role_labels = {
            "composition":"主构图", "character":"人物身份", "scene":"场景结构",
            "element":"道具外观", "style":"视觉风格", "reference":"普通参考",
        }
        rows = [f"Overall: {str(overall or '').strip()}"]
        for index, item in enumerate(sorted(
                timeline, key=lambda value: (float(value.get("start") or 0),
                                             float(value.get("end") or 0))), 1):
            rows.append(
                f"[{float(item.get('start') or 0):05.2f}-"
                f"{float(item.get('end') or 0):05.2f}] 参考图{index:02d}作为"
                f"{role_labels.get(str(item.get('role') or 'reference'), '普通参考')}："
                f"{str(item.get('instruction') or '').strip()}；"
                "只执行一个主要动作和一种主要运镜，结尾停在清晰可见的状态。")
        rows.append(
            "全片禁止把参考图做成幻灯片、静态贴片或逐图展示；按时间轴生成真实连续运动。"
            "切镜时继承主体身份、场景结构、道具状态、光线方向和动作进度。")
        return "\n".join(rows)

    def edit_multi_image_composer(self, node):
        """编辑多图生成节点中每张参考图的明确职责。"""
        record = self._custom_record(str(node.node_id))
        if record is None:
            return
        paths = [str(path) for path in record.get("references", []) if path][:9]
        existing = {
            str(value.get("path") or ""): dict(value)
            for value in record.get("reference_assets", [])
            if isinstance(value, dict) and value.get("path")}
        allowed_roles = {"character", "scene", "composition", "element"}
        default_roles = ("character", "scene", "composition", "element")
        assets = []
        for index, path in enumerate(paths):
            value = existing.get(path, {})
            role = str(value.get("role") or default_roles[min(index, len(default_roles) - 1)])
            if role not in allowed_roles:
                role = "composition"
            assets.append({
                "path":path, "role":role,
                "label":str(value.get("label") or f"参考图{index + 1:02d}"),
                "detail":str(value.get("detail") or ""),
                "order":index,
            })
        if not assets:
            QMessageBox.information(self, "多图生成图片", "请先连接或选择至少一张参考图。")
            return

        dialog = QDialog(self)
        dialog.setObjectName("multiImagePurposeDialog")
        dialog.setWindowTitle("多图生成 · 设置图片用途")
        dialog.setMinimumSize(860, 560)
        dialog.resize(900, 590)
        dialog.setStyleSheet("""
            QDialog#multiImagePurposeDialog{background:#15161a;color:#ededf2;}
            QLabel{color:#d9d9df;background:transparent;}
            QListWidget{background:#111216;border:1px solid #30323a;border-radius:12px;
                padding:7px;color:#c9cad1;font-size:12px;outline:none;}
            QListWidget::item{padding:12px 10px;margin:3px;border-radius:8px;}
            QListWidget::item:selected{background:#28354a;color:#fff;border:1px solid #42638e;}
            QLineEdit{background:#111216;color:#eee;border:1px solid #363943;
                border-radius:9px;padding:10px 12px;font-size:12px;}
            QLineEdit:focus{border-color:#5b83bd;}
            QPushButton{background:#282a31;color:#dedee5;border:1px solid #393c46;
                border-radius:9px;padding:9px 14px;}
            QPushButton:hover{background:#32353e;border-color:#515663;}
            QPushButton#purposeConfirm{background:#3e6fae;color:white;border-color:#5689ca;
                font-weight:600;min-width:120px;}
        """)
        root = QVBoxLayout(dialog)
        root.setContentsMargins(22, 20, 22, 18); root.setSpacing(14)
        title = QLabel("为每张图指定一个明确职责")
        title.setStyleSheet("font-size:18px;font-weight:700;color:#fff;")
        root.addWidget(title)
        note = QLabel(
            "不再使用会漂移的下拉菜单。选择左侧图片，再点击右侧用途即可。")
        note.setStyleSheet("color:#8f929d;font-size:12px;")
        note.setWordWrap(True); root.addWidget(note)

        body = QHBoxLayout(); body.setSpacing(16)
        image_list = QListWidget(); image_list.setFixedWidth(310)
        body.addWidget(image_list)
        detail_panel = QFrame(); detail_panel.setObjectName("purposeDetail")
        detail_panel.setStyleSheet(
            "QFrame#purposeDetail{background:#1b1c21;border:1px solid #30323a;border-radius:12px;}")
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(18, 18, 18, 18); detail_layout.setSpacing(13)
        preview = QLabel("选择一张图片")
        preview.setFixedHeight(210); preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.setStyleSheet("background:#0e0f12;border-radius:10px;color:#686b75;")
        detail_layout.addWidget(preview)
        role_title = QLabel("这张图负责什么？")
        role_title.setStyleSheet("font-size:13px;font-weight:600;color:#f1f1f4;")
        detail_layout.addWidget(role_title)
        role_grid = QHBoxLayout(); role_grid.setSpacing(8)
        role_buttons = {}
        role_specs = (("主体", "character", "人物身份与外观"),
                      ("场景", "scene", "空间与环境结构"),
                      ("构图", "composition", "机位与画面布局"),
                      ("元素", "element", "服装、道具或产品"))
        for label, role, tip in role_specs:
            button = QPushButton(label); button.setCheckable(True)
            button.setToolTip(tip); button.setMinimumWidth(86)
            role_buttons[role] = button; role_grid.addWidget(button)
        role_grid.addStretch(); detail_layout.addLayout(role_grid)
        req_title = QLabel("本图的补充要求（可选）")
        req_title.setStyleSheet("color:#a8aab3;font-size:11px;")
        detail_layout.addWidget(req_title)
        requirement = QLineEdit()
        requirement.setPlaceholderText("例如：只使用红色外套，不复制这张图的背景")
        detail_layout.addWidget(requirement); detail_layout.addStretch()
        body.addWidget(detail_panel, 1); root.addLayout(body, 1)
        current = {"row":-1, "role":"composition"}

        def paint_roles(role):
            current["role"] = role if role in allowed_roles else "composition"
            for key, button in role_buttons.items():
                selected = key == current["role"]
                button.setChecked(selected)
                button.setStyleSheet(
                    "QPushButton{background:#315b8f;color:#fff;border:1px solid #5689ca;"
                    "border-radius:9px;padding:9px 14px;font-weight:600;}"
                    if selected else
                    "QPushButton{background:#26282f;color:#c7c8cf;border:1px solid #393c46;"
                    "border-radius:9px;padding:9px 14px;}")

        for role, button in role_buttons.items():
            button.clicked.connect(lambda _checked=False, value=role: paint_roles(value))

        def save_row():
            row = current["row"]
            if 0 <= row < len(assets):
                role = str(current["role"] or "composition")
                detail = requirement.text().strip()
                role_name = {"character":"主体", "scene":"场景",
                             "composition":"构图", "element":"元素"}[role]
                assets[row]["role"] = role
                assets[row]["label"] = (
                    f"参考图{row + 1:02d} · {role_name}" +
                    (f" · {detail}" if detail else ""))
                assets[row]["detail"] = detail

        def load_row(row):
            save_row(); current["row"] = row
            if not 0 <= row < len(assets):
                return
            value = assets[row]
            paint_roles(str(value.get("role") or "composition"))
            requirement.setText(str(value.get("detail") or ""))
            pixmap = QPixmap(str(value.get("path") or ""))
            if pixmap.isNull():
                preview.setPixmap(QPixmap()); preview.setText("图片无法预览")
            else:
                preview.setText("")
                preview.setPixmap(pixmap.scaled(
                    500, 210, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))

        for index, value in enumerate(assets, 1):
            image_list.addItem(f"图 {index:02d}\n{Path(value['path']).name}")
        image_list.currentRowChanged.connect(load_row)
        image_list.setCurrentRow(0)
        buttons = QHBoxLayout()
        summary = QLabel(f"共 {len(assets)} 张图片 · 最多 9 张")
        summary.setStyleSheet("color:#777a85;font-size:11px;")
        buttons.addWidget(summary); buttons.addStretch()
        cancel = QPushButton("取消"); cancel.clicked.connect(dialog.reject)
        confirm = QPushButton("保存用途设置")
        confirm.setObjectName("purposeConfirm")
        confirm.clicked.connect(lambda _=False: (save_row(), dialog.accept()))
        buttons.addWidget(cancel); buttons.addWidget(confirm); root.addLayout(buttons)
        dialog.move(self.window().frameGeometry().center() - dialog.rect().center())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        record["reference_assets"] = assets
        record["references"] = [value["path"] for value in assets]
        record["editor_action"] = "AI 编辑"
        record["status"] = f"多图用途已配置 · {len(assets)} 张参考"
        node.payload.update(record)
        self._save_layout_now()
        node.badge = f"多图 {len(assets)} 张"
        node.subtitle = str(record["status"])
        node.update()

    def edit_multi_image_director(self, node):
        record = self._custom_record(str(node.node_id))
        if record is None:
            return
        duration = float(record.get("duration") or 10)
        timeline = [dict(value) for value in record.get("timeline_images", [])
                    if isinstance(value, dict) and value.get("path")]
        by_path = {str(value.get("path") or ""): value for value in timeline}
        typed = {str(value.get("path") or ""): dict(value)
                 for value in record.get("reference_assets", [])
                 if isinstance(value, dict) and value.get("path")}
        connected_paths = list(dict.fromkeys(
            [str(value) for value in record.get("references", []) if value] +
            list(typed)))[:50]
        segment = max(0.5, duration / max(1, len(connected_paths)))
        allowed_roles = {"character", "scene", "composition", "element"}
        default_roles = ("character", "scene", "composition", "element")
        for index, path in enumerate(connected_paths):
            if path in by_path:
                continue
            role = str(typed.get(path, {}).get("role") or
                       default_roles[index % len(default_roles)])
            if role not in allowed_roles:
                role = default_roles[index % len(default_roles)]
            value = {
                "path":path, "start":round(index * segment, 2),
                "end":round(min(duration, (index + 1) * segment), 2),
                "role":role,
                "instruction":"保持主体与场景连续，完成一个清晰动作并停在明确结束状态。",
            }
            timeline.append(value); by_path[path] = value
        for index, value in enumerate(timeline):
            role = str(value.get("role") or default_roles[index % len(default_roles)])
            if role not in allowed_roles:
                value["role"] = default_roles[index % len(default_roles)]

        dialog = QDialog(self)
        dialog.setObjectName("multiImageDirectorDialog")
        dialog.setWindowTitle("多图导演 · 时间轴")
        dialog.setMinimumSize(900, 610); dialog.resize(940, 640)
        dialog.setStyleSheet("""
            QDialog#multiImageDirectorDialog{background:#15161a;color:#ededf2;}
            QLabel{color:#d9d9df;background:transparent;}
            QListWidget{background:#111216;border:1px solid #30323a;border-radius:12px;
                padding:7px;color:#c9cad1;font-size:12px;outline:none;}
            QListWidget::item{padding:12px 10px;margin:3px;border-radius:8px;}
            QListWidget::item:selected{background:#28354a;color:#fff;border:1px solid #42638e;}
            QLineEdit,QTextEdit{background:#111216;color:#eee;border:1px solid #363943;
                border-radius:9px;padding:9px 11px;font-size:12px;}
            QLineEdit:focus,QTextEdit:focus{border-color:#5b83bd;}
            QPushButton{background:#282a31;color:#dedee5;border:1px solid #393c46;
                border-radius:9px;padding:9px 14px;}
            QPushButton:hover{background:#32353e;border-color:#515663;}
            QPushButton#directorConfirm{background:#3e6fae;color:white;border-color:#5689ca;
                font-weight:600;min-width:120px;}
        """)
        root = QVBoxLayout(dialog); root.setContentsMargins(22, 20, 22, 18); root.setSpacing(14)
        title = QLabel("安排每张图在视频中的时间、职责和动作")
        title.setStyleSheet("font-size:18px;font-weight:700;color:#fff;"); root.addWidget(title)
        note = QLabel(
            f"已读取 {len(connected_paths)} 张连入图片 · 视频总时长 {duration:g} 秒。"
            "左侧选图，右侧直接设置，不再使用漂移下拉菜单。")
        note.setStyleSheet("color:#8f929d;font-size:12px;"); root.addWidget(note)
        body = QHBoxLayout(); body.setSpacing(16)
        image_list = QListWidget(); image_list.setFixedWidth(310); body.addWidget(image_list)
        detail_panel = QFrame(); detail_panel.setObjectName("directorDetail")
        detail_panel.setStyleSheet(
            "QFrame#directorDetail{background:#1b1c21;border:1px solid #30323a;border-radius:12px;}")
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(18, 18, 18, 18); detail_layout.setSpacing(12)
        preview = QLabel("选择一张图片"); preview.setFixedHeight(210)
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.setStyleSheet("background:#0e0f12;border-radius:10px;color:#686b75;")
        detail_layout.addWidget(preview)
        time_title = QLabel("在视频中出现的时间段")
        time_title.setStyleSheet("font-size:13px;font-weight:600;color:#f1f1f4;")
        detail_layout.addWidget(time_title)
        fields = QHBoxLayout()
        start_edit = QLineEdit(); start_edit.setPlaceholderText("开始秒")
        end_edit = QLineEdit(); end_edit.setPlaceholderText("结束秒")
        fields.addWidget(QLabel("从")); fields.addWidget(start_edit)
        fields.addWidget(QLabel("秒到")); fields.addWidget(end_edit); fields.addWidget(QLabel("秒"))
        detail_layout.addLayout(fields)
        role_title = QLabel("这张图负责什么？")
        role_title.setStyleSheet("font-size:13px;font-weight:600;color:#f1f1f4;")
        detail_layout.addWidget(role_title)
        role_row = QHBoxLayout(); role_row.setSpacing(8); role_buttons = {}
        for label, role, tip in (("主体","character","人物身份与外观"),
                                 ("场景","scene","空间与环境结构"),
                                 ("构图","composition","机位与画面布局"),
                                 ("元素","element","服装、道具或产品")):
            button = QPushButton(label); button.setCheckable(True); button.setToolTip(tip)
            role_buttons[role] = button; role_row.addWidget(button)
        role_row.addStretch(); detail_layout.addLayout(role_row)
        instruction_title = QLabel("动作与运镜")
        instruction_title.setStyleSheet("font-size:13px;font-weight:600;color:#f1f1f4;")
        detail_layout.addWidget(instruction_title)
        instruction = QTextEdit()
        instruction.setPlaceholderText("例如：主体向门口走两步停下，镜头缓慢推近，结尾保持正面中景")
        instruction.setMaximumHeight(95); detail_layout.addWidget(instruction)
        body.addWidget(detail_panel, 1); root.addLayout(body, 1)
        current = {"row":-1, "role":"composition"}

        def paint_roles(role):
            current["role"] = role if role in allowed_roles else "composition"
            for key, button in role_buttons.items():
                selected = key == current["role"]; button.setChecked(selected)
                button.setStyleSheet(
                    "QPushButton{background:#315b8f;color:#fff;border:1px solid #5689ca;"
                    "border-radius:9px;padding:9px 14px;font-weight:600;}"
                    if selected else
                    "QPushButton{background:#26282f;color:#c7c8cf;border:1px solid #393c46;"
                    "border-radius:9px;padding:9px 14px;}")

        for role, button in role_buttons.items():
            button.clicked.connect(lambda _checked=False, value=role: paint_roles(value))

        def list_text(row):
            value = timeline[row]
            role_name = {"character":"主体", "scene":"场景",
                         "composition":"构图", "element":"元素"}.get(
                             str(value.get("role") or ""), "构图")
            return (f"图 {row + 1:02d} · {role_name} · "
                    f"{float(value.get('start') or 0):g}-{float(value.get('end') or 0):g}s\n"
                    f"{Path(str(value['path'])).name}")

        def save_row(quiet=True):
            row = current["row"]
            if not 0 <= row < len(timeline):
                return True
            try:
                start, end = float(start_edit.text()), float(end_edit.text())
            except ValueError:
                if not quiet:
                    QMessageBox.information(dialog, "时间错误", "开始和结束必须填写秒数。")
                return False
            if start < 0 or end <= start:
                if not quiet:
                    QMessageBox.information(dialog, "时间错误", "结束时间必须大于开始时间。")
                return False
            timeline[row].update({
                "start":start, "end":end,
                "role":str(current["role"] or "composition"),
                "instruction":instruction.toPlainText().strip(),
            })
            list_item = image_list.item(row)
            if list_item is not None:
                list_item.setText(list_text(row))
            return True

        def load_row(row):
            save_row(True)
            current["row"] = row
            if not 0 <= row < len(timeline):
                return
            item = timeline[row]
            start_edit.setText(f"{float(item.get('start') or 0):g}")
            end_edit.setText(f"{float(item.get('end') or 0):g}")
            paint_roles(str(item.get("role") or "composition"))
            instruction.setPlainText(str(item.get("instruction") or ""))
            pixmap = QPixmap(str(item.get("path") or ""))
            if pixmap.isNull():
                preview.setPixmap(QPixmap()); preview.setText("图片无法预览")
            else:
                preview.setText("")
                preview.setPixmap(pixmap.scaled(
                    520, 210, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))

        for index, _item in enumerate(timeline):
            image_list.addItem(list_text(index))
        image_list.currentRowChanged.connect(load_row)
        if timeline:
            image_list.setCurrentRow(0)
        buttons = QHBoxLayout()
        summary = QLabel(f"共 {len(timeline)} 张图片 · {duration:g} 秒")
        summary.setStyleSheet("color:#777a85;font-size:11px;")
        buttons.addWidget(summary); buttons.addStretch()
        cancel = QPushButton("取消"); cancel.clicked.connect(dialog.reject)
        confirm = QPushButton("保存时间轴"); confirm.clicked.connect(
            lambda _=False: dialog.accept() if save_row(False) else None)
        confirm.setObjectName("directorConfirm")
        buttons.addWidget(cancel); buttons.addWidget(confirm); root.addLayout(buttons)
        dialog.move(self.window().frameGeometry().center() - dialog.rect().center())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if any(float(value.get("end") or 0) > duration for value in timeline):
            QMessageBox.information(
                self, "时间轴超过视频时长",
                f"节点时长为 {duration:g} 秒，请缩短图片时间或先调整节点时长。")
            return
        record["timeline_images"] = timeline
        record["reference_assets"] = [
            {"path":value["path"], "role":value.get("role") or "reference",
             "label":f"时间轴参考图{index:02d}", "order":index - 1}
            for index, value in enumerate(timeline, 1)]
        record["references"] = [value["path"] for value in timeline]
        record["status"] = f"多图时间轴已配置 · {len(timeline)} 张图片"
        node.payload.update(record)
        self._save_layout_now()
        # The editor is opened from a button inside the expanded node panel.
        # Rebuilding the canvas here deletes that native QPushButton before its
        # clicked signal returns and crashes Qt6Widgets. Update in place only.
        node.badge = f"时间轴 {len(timeline)} 图"
        node.subtitle = str(record.get("status") or "多图时间轴已保存")
        node.update()

    def submit_standalone_generation(self, node, content: str, action: str):
        """独立节点直接生成；镜头仅提供可选的故事、资产和连续性上下文。"""
        record = self._custom_record(node.node_id)
        if (node.node_type == "video_node" and record is not None and
                str(record.get("generator_kind") or "") == "video"):
            # Old projects may contain prompts that explicitly asked the video
            # model to render storyboard arrows. Recompile at every submission,
            # so a single-node retry repairs legacy records automatically.
            self._prepare_video_handoff_anchor(record)
            self._refresh_video_generator_contract(record)
            node.payload.update(record)
            content = str(record.get("content") or content)
        if (node.node_type == "image_node" and
                str(node.payload.get("generator_kind") or "") == "image"):
            if record is not None and self._sanitize_motion_board_pixel_references(record):
                node.payload.update(record)
                self._save_layout_now()
        raw_prompt = content.strip()
        if node.node_type == "image_node" and not raw_prompt:
            raw_prompt = IMAGE_EDIT_DEFAULTS.get(action, "")
        if (node.node_type == "video_node" and action == "图生视频" and
                not raw_prompt and str(node.payload.get("creative_prompt") or "").strip()):
            raw_prompt = "基于输入的首尾关键帧生成连贯视频。"
        if not raw_prompt:
            QMessageBox.information(self, "生成节点", "请先填写生成描述。")
            return
        prompt = self._apply_style_to_prompt(
            raw_prompt, str(node.payload.get("style") or ""))
        manager = get_ai_manager()
        reference = self._upstream_media_path(
            node.node_id, image_only=node.node_type in ("image_node", "video_node"))
        references = [str(value) for value in node.payload.get("references", [])
                      if value and os.path.exists(str(value)) and self._is_image_path(str(value))]
        if (node.node_type == "image_node" and
                bool(node.payload.get("multi_image_composer")) and references):
            # 多图节点的权威输入始终是用户配置的参考集，
            # 不要在第二次生成时把上一张输出当成新的构图底图。
            reference = references[0]
        if (node.node_type == "image_node" and
                str(node.payload.get("generator_kind") or "") == "image" and
                action == "图生图" and references):
            # Re-running a final-image generator must keep using the approved
            # blocking panel, not drift by recursively editing its last output.
            reference = references[0]
        if node.node_type == "image_node":
            edit_actions = {"AI 编辑", "图生图", "图片高清", "智能扩图", "移除背景", "替换背景"}
            if action in edit_actions and not reference:
                QMessageBox.information(self, "图片编辑", "请先上传图片，或连接一个图片节点作为参考。")
                return
            operation = "image_edit" if reference and action in edit_actions else "text_to_image"
            inputs = {"prompt": prompt}
            if operation == "image_edit":
                typed_references = self._reference_assets_for_node(node, reference)
                if str(node.payload.get("frame_role") or "") == "start":
                    scene_master = str(node.payload.get("scene_view_path") or
                                       node.payload.get("scene_master_path") or "")
                    if scene_master and os.path.exists(scene_master):
                        # Repair stale generator nodes created before the scene-
                        # master contract: one spatial base, at most two identity
                        # anchors and one prop anchor.  Never send scene variants.
                        limited = [{
                            "path":scene_master, "role":"composition", "required":True,
                            "label":"场景母版（唯一空间底图）",
                        }]
                        role_limits = {"character":2, "element":1}
                        role_counts = {key:0 for key in role_limits}
                        for value in typed_references:
                            role = str(value.get("role") or "reference")
                            path = str(value.get("path") or "")
                            if (role not in role_limits or path == scene_master or
                                    role_counts[role] >= role_limits[role]):
                                continue
                            limited.append(value); role_counts[role] += 1
                        typed_references = limited
                if str(node.payload.get("frame_role") or "") == "end":
                    endpoint_source = str(
                        node.payload.get("endpoint_source_path") or reference or "")
                    if not endpoint_source or not os.path.exists(endpoint_source):
                        QMessageBox.warning(
                            self, "结束帧缺少开始帧",
                            "结束帧必须从已确认的开始帧生成，请先确认该镜头的 K1。")
                        return
                    # Submission-level invariant: even legacy nodes or stale UI
                    # payloads cannot leak asset sheets into an endpoint edit.
                    typed_references = [{
                        "path":endpoint_source, "role":"composition", "required":True,
                        "label":"已确认 K1（唯一像素底图）",
                    }]
                all_references = [value["path"] for value in typed_references]
                inputs.update({"image": all_references[0], "images": all_references})
                inputs["reference_assets"] = typed_references
                frame_role = str(node.payload.get("frame_role") or "")
                if frame_role == "end":
                    mask_source = str(
                        node.payload.get("endpoint_source_path") or all_references[0])
                    mask_path = create_edit_region_mask(
                        mask_source, node.payload.get("editable_bbox_xy"),
                        str(Path(__file__).parents[2] / "work_temp" / "scene_masks"),
                        protected_bboxes=fixture_view_bboxes(
                            node.payload.get("scene_proxy") or {},
                            str(node.payload.get("scene_view_id") or "master")))
                    if mask_path:
                        inputs["mask"] = mask_path
                        if record is not None:
                            record["edit_mask_path"] = mask_path
            ratio = str(node.payload.get("ratio") or "1:1")
            sizes = {"16:9": "2048x1152", "9:16": "1152x2048",
                     "1:1": "2048x2048", "4:5": "1638x2048"}
            params = {"size": sizes.get(ratio, "2048x2048"),
                      "n": max(1, min(4, int(node.payload.get("candidate_count") or 1))),
                      "quality": "high",
                      "watermark": False}
            if action in ("图片高清", "智能扩图", "移除背景"):
                params["quality"] = "high"
        elif node.node_type == "video_node":
            first_frame = str(node.payload.get("first_frame") or "")
            if not first_frame or not os.path.exists(first_frame):
                first_frame = reference
            generator_shots = [shot for _index, shot in
                               self._segment_for_generator(node.payload)]
            contaminated_anchor = next((shot for shot in generator_shots
                if first_frame and self._path_has_motion_board_lineage(
                    shot, first_frame)), None)
            if contaminated_anchor is not None:
                QMessageBox.warning(
                    self, "已阻止参考线污染",
                    "当前视频首帧来自运动分镜板或其旧版衍生图。系统已停止提交，"
                    "请选择干净定稿图片后再重试这个视频段。")
                return
            last_frame = str(node.payload.get("last_frame") or "")
            if last_frame and not os.path.exists(last_frame):
                last_frame = ""
            if (last_frame and generator_shots and
                    str(node.payload.get("prompt_contract") or "").startswith(
                        "clean_endpoint_video") and
                    str(node.payload.get("last_frame_source") or "") != "manual_override" and
                    not self._shot_uses_endpoint_pair(generator_shots[-1])):
                last_frame = ""
                node.payload["last_frame"] = ""
                node.payload["planned_last_frame"] = ""
                node.payload["last_frame_source"] = ""
                node.payload["endpoint_pair_fallback"] = (
                    "尾帧未获明确批准或未通过一致性检查，已使用单首帧")
            if last_frame and not first_frame:
                QMessageBox.information(self, "首尾帧视频", "尾帧不能单独生成视频，请先设置首帧。")
                return
            multi_image_director = bool(node.payload.get("multi_image_director"))
            if multi_image_director:
                timeline = [dict(value) for value in node.payload.get("timeline_images", [])
                            if isinstance(value, dict) and value.get("path")]
                if not timeline:
                    QMessageBox.information(
                        self, "多图导演", "请先把图片节点连接进来并配置图片时间轴。")
                    return
                if len(timeline) > 9:
                    QMessageBox.information(
                        self, "Seedance 参考图上限",
                        f"当前 Seedance 接口一次最多接收 9 张参考图；本节点已有 {len(timeline)} 张。"
                        "请删除或断开本次不需要的图片。")
                    return
                prompt = self._multi_image_director_prompt(prompt, timeline)
            director_multishot = bool(
                str(node.payload.get("provider_name") or "").lower() == "seedance" and
                str(node.payload.get("video_generation_mode") or "") == "director_timeline" and
                len(generator_shots) > 1)
            operation = (
                "text_to_video" if director_multishot or multi_image_director else
                "image_to_video" if first_frame and action == "图生视频" else
                "text_to_video")
            if operation == "image_to_video":
                prompt = self._image_video_prompt(
                    prompt, str(node.payload.get("creative_prompt") or ""))
            inputs = {"prompt": prompt}
            if operation == "image_to_video":
                inputs["image"] = first_frame
                if last_frame:
                    inputs["last_frame"] = last_frame
                typed_references = self._reference_assets_for_node(node)
                if typed_references:
                    inputs["reference_assets"] = typed_references
            elif director_multishot or multi_image_director:
                typed_references = self._reference_assets_for_node(node)
                if typed_references:
                    inputs["reference_assets"] = typed_references[:9]
            ratio = str(node.payload.get("ratio") or "16:9")
            params = {"duration": float(node.payload.get("duration") or 5), "aspect_ratio": ratio,
                      "ratio": ratio, "resolution": "720p"}
        else:
            operation = "text_to_speech"
            inputs = {"text": prompt}
            params = {"voice":str(node.payload.get("voice") or ""),
                      "speed":float(node.payload.get("speed") or 1),
                      "emotion":str(node.payload.get("emotion") or "")}
        providers = manager.registry.by_capability(operation)
        if not providers:
            QMessageBox.warning(self, "没有可用模型", f"当前没有支持 {operation} 的生成引擎。")
            return
        preferred_provider = str(node.payload.get("provider_name") or "")
        production_source = self._production_source_for_generator(node.node_id)
        if production_source and str(node.payload.get("generator_kind") or "") in {
                "image", "video"}:
            lock_key = ("image_provider" if node.node_type == "image_node"
                        else "video_provider")
            project_provider = self._storyboard_model_lock(
                production_source, lock_key)
            if project_provider:
                # Submission-time invariant: even an old generator node or a
                # project lock changed in the script workbench cannot execute
                # with its stale per-node provider.
                preferred_provider = project_provider
                node.payload["provider_name"] = project_provider
                if record is not None:
                    record["provider_name"] = project_provider
        if not preferred_provider and operation in {"image_to_video", "text_to_video"}:
            source = self._custom_record(self._current_production_source_id()) or {}
            routing_shot = self._find_shot(str((record or {}).get("shot_id") or "")) or {}
            decision = recommend_provider(
                source.get("generation_trace", []),
                [provider.name for provider in providers], routing_shot)
            recommended = str(decision.get("provider") or "")
            if recommended:
                providers = sorted(providers, key=lambda value:value.name != recommended)
        provider = next((value for value in providers
                         if value.name == preferred_provider), None)
        if preferred_provider and provider is None:
            media_label = "图片" if node.node_type == "image_node" else "视频"
            QMessageBox.warning(
                self, "指定模型不可用",
                f"这个节点已锁定使用 {preferred_provider}，但它当前不支持或未配置 "
                f"{operation}。\n\n系统不会擅自切换到其他{media_label}模型。")
            return
        if provider is None and action == "移除背景":
            provider = next((value for value in providers if value.name == "gptimage"), None)
        provider = provider or providers[0]
        try:
            if record is not None:
                current_path = str(record.get("path") or "")
                if operation == "image_edit" and current_path and os.path.exists(current_path):
                    record["candidates"] = list(dict.fromkeys(
                        list(record.get("candidates") or []) + [current_path]))
                record["last_action"] = action
                record["status"] = "正在提交"
                self._save_layout_now()
            request = TaskRequest(operation=operation, inputs=inputs, params=params,
                                  metadata={"canvas_node_id": node.node_id,
                                            "canvas_action": action,
                                            "director_multishot": bool(
                                                node.node_type == "video_node" and
                                                locals().get("director_multishot", False)),
                                            "creative_prompt": (
                                                str(node.payload.get("creative_prompt") or "")
                                                if operation == "image_to_video" else "")},
                                  use_cache=False)
            handle = manager.submit(
                provider.name,
                request)
            self._standalone_tasks[handle.id] = {
                "handle": handle, "node_id": node.node_id,
                "provider": provider.name,
                "request": request,
                # An explicit model choice is a production constraint. Silent
                # Seedance -> Veo switching changes style, audio and billing.
                "fallback_providers": ([] if preferred_provider else
                                       [value.name for value in providers
                                        if value.name != provider.name]),
                "provider_locked": bool(preferred_provider),
            }
            node.badge = "生成中 0%"
            node.update()
        except Exception as error:
            QMessageBox.warning(self, "提交失败", str(error))

    @staticmethod
    def _is_real_person_privacy_error(error) -> bool:
        text = str(error or "").lower()
        return any(value in text for value in (
            "privacyinformation", "may contain real person",
            "真人隐私", "可识别的真实人物"))

    def _choose_replacement_video_frame(self, node_id: str):
        node = self._nodes.get(str(node_id or ""))
        if node is not None:
            self.choose_video_frame(node, "first_frame")

    def _show_video_privacy_block(self, node_id: str, error: str):
        """Offer compliant recovery actions for Ark's real-person rejection."""
        record = self._custom_record(node_id)
        if record is None:
            QMessageBox.warning(self, "Seedance 已拒绝输入图", error)
            return
        blocked_path = str(record.get("blocked_input") or record.get("first_frame") or "")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Seedance 已拒绝当前首帧")
        box.setText("当前首帧被 Ark 判定为可能包含可识别真人，因此视频任务未提交。")
        box.setInformativeText(
            (f"被拦截图片：{Path(blocked_path).name}\n\n" if blocked_path else "") +
            "可换成原创的非真人化/已获授权角色图，或者由你明确改用其他已配置模型。"
            "系统不会自动绕过审核，也不会偷偷切换模型。")
        replace_button = box.addButton("更换首帧", QMessageBox.ButtonRole.ActionRole)
        veo_available = any(
            provider.name == "veo" for provider in
            get_ai_manager().registry.by_capability("image_to_video"))
        veo_button = box.addButton("明确改用 VEO", QMessageBox.ButtonRole.ActionRole)
        veo_button.setEnabled(veo_available)
        close_button = box.addButton("关闭", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(replace_button)
        box.setDetailedText(str(error))
        box.exec()
        clicked = box.clickedButton()
        if clicked is replace_button:
            QTimer.singleShot(
                0, lambda value=str(node_id):
                self._choose_replacement_video_frame(value))
        elif clicked is veo_button and veo_available:
            record["provider_name"] = "veo"
            record["status"] = "已明确切换到 VEO · 请重新生成当前视频段"
            record.pop("generation_blocked", None)
            record.pop("blocked_input", None)
            self._save_layout_now(); self.refresh(); self.focus_node(node_id)
        else:
            _ = close_button

    def _poll_standalone_tasks(self):
        finished = []
        continue_storyboard = False
        continue_character = False
        for task_id, task in list(self._standalone_tasks.items()):
            handle = task["handle"]
            node = self._nodes.get(task["node_id"])
            if node:
                task_kind = str(task.get("kind") or "")
                prefix = "审片中" if task_kind in {"clip_qc", "sequence_qc"} else "生成中"
                node.badge = f"{prefix} {int(float(handle.progress or 0) * 100)}%"
                node.update()
            if not handle.is_finished:
                continue
            finished.append(task_id)
            if handle.is_success and handle.result:
                self._workflow_failed_nodes.discard(str(task.get("node_id") or ""))
                data = handle.result.data
                if task.get("kind") == "clip_qc":
                    review = extract_json(str(data or ""))
                    self._apply_clip_qc_result(
                        str(task.get("node_id") or ""),
                        str(task.get("source_id") or ""), review,
                        [str(value) for value in task.get("shot_ids", []) if value])
                elif task.get("kind") == "sequence_qc":
                    review = normalize_sequence_qc(extract_json(str(data or "")))
                    self._finalize_video_qc(
                        str(task.get("source_id") or task.get("node_id") or ""), review)
                elif task.get("kind") == "storyboard_character":
                    value = data[0] if isinstance(data, (list, tuple)) and data else data
                    path = str(value or "")
                    record = self._custom_record(task["node_id"])
                    if record is not None and path and os.path.exists(path):
                        character_role = str(task.get("character_role") or "")
                        scene_role = str(task.get("scene_role") or "")
                        old_path = str(record.get("path") or "")
                        if scene_role:
                            reference_set = record.setdefault("scene_reference_set", {})
                            reference_set[scene_role] = path
                            role_label = next((label for role, label, _prompt
                                               in SCENE_VIEW_SPECS
                                               if role == scene_role), scene_role)
                            if scene_role == "master":
                                record["path"] = path
                                record["title"] = f"场景母版 · {record.get('asset_name') or '场景'}"
                                record["ratio"] = SCENE_REFERENCE_FORMATS["master"]["ratio"]
                                record["reference_role"] = "scene"
                                record["candidates"] = list(dict.fromkeys(
                                    list(record.get("candidates") or []) + [path]))
                                record["asset_version"] = int(record.get("asset_version") or 0) + 1
                            else:
                                view_id = f"{task['node_id']}:scene-view:{scene_role}"
                                view_record = self._custom_record(view_id)
                                view_payload = {
                                    "id":view_id, "type":"image_node",
                                    "title":f"{record.get('asset_name') or '场景'} · {role_label}",
                                    "content":role_label, "path":path,
                                    "ratio":SCENE_REFERENCE_FORMATS.get(
                                        scene_role, {}).get("ratio", "3:2"),
                                    "reference_role":"scene", "scene_view_role":scene_role,
                                    "reference_parent_id":task["node_id"],
                                    "location_id":record.get("location_id"),
                                    "scene_proxy":record.get("scene_proxy") or {},
                                    "status":"权威场景视图", "adopted":True, "locked":True,
                                    "candidates":[path],
                                }
                                if view_record is None:
                                    self._positions().setdefault("__custom_nodes__", []).append(view_payload)
                                else:
                                    view_record.update(view_payload)
                                self._positions()[view_id] = self._scene_reference_position(
                                    task["node_id"], scene_role)
                                edge = {"source":task["node_id"], "target":view_id,
                                        "type":"scene_reference"}
                                edges = self._positions().setdefault("__workflow_edges__", [])
                                if edge not in edges:
                                    edges.append(edge)
                        elif character_role:
                            reference_set = record.setdefault("character_reference_set", {})
                            reference_set[character_role] = path
                            role_label = next((label for role, label, _prompt
                                               in CHARACTER_REFERENCE_SPECS
                                               if role == character_role), character_role)
                            if character_role == "portrait":
                                # The parent asset is itself the portrait/identity anchor.
                                record["path"] = path
                                record["title"] = f"{record.get('asset_name') or '角色'} · 角色立绘"
                                record["ratio"] = CHARACTER_REFERENCE_FORMATS["portrait"]["ratio"]
                                record["reference_role"] = "character"
                                record["candidates"] = list(dict.fromkeys(
                                    list(record.get("candidates") or []) + [path]))
                                record["asset_version"] = int(record.get("asset_version") or 0) + 1
                                self._remove_legacy_character_portrait_view(task["node_id"])
                            else:
                                view_id = f"{task['node_id']}:view:{character_role}"
                                view_record = self._custom_record(view_id)
                                view_payload = {
                                    "id":view_id, "type":"image_node",
                                    "title":f"{record.get('asset_name') or '角色'} · {role_label}",
                                    "content":role_label, "path":path,
                                    "ratio":CHARACTER_REFERENCE_FORMATS.get(
                                        character_role, {}).get("ratio", "1:1"),
                                    "reference_role":"character",
                                    "character_panel_role":character_role,
                                    "reference_parent_id":task["node_id"],
                                    "status":"权威角色参考", "adopted":True, "locked":True,
                                    "candidates":[path],
                                }
                                if view_record is None:
                                    self._positions().setdefault("__custom_nodes__", []).append(view_payload)
                                else:
                                    view_record.update({
                                        **view_payload,
                                        "candidates":list(dict.fromkeys(
                                            list(view_record.get("candidates") or []) + [path])),
                                    })
                                self._positions()[view_id] = self._character_reference_position(
                                    task["node_id"], character_role)
                                edge = {"source":task["node_id"], "target":view_id,
                                        "type":"character_reference"}
                                edges = self._positions().setdefault("__workflow_edges__", [])
                                if edge not in edges:
                                    edges.append(edge)
                        else:
                            record["candidates"] = list(dict.fromkeys(
                                list(record.get("candidates") or []) + [path]))
                            record["path"] = path
                            record["asset_version"] = int(record.get("asset_version") or 0) + 1
                        record["adopted"] = True; record["locked"] = False
                        completed = len(record.get("character_reference_set") or {})
                        scene_completed = len(record.get("scene_reference_set") or {})
                        record["status"] = (f"角色设定 {completed}/4 · 待锁定"
                                            if character_role else
                                            f"场景视图 {scene_completed}/5 · 待锁定"
                                            if scene_role else
                                            f"V{record['asset_version']} 待锁定")
                        if not scene_role:
                            self._canvas_storyboard_character_refs.append(path)
                        if old_path and old_path != str(record.get("path") or ""):
                            self.invalidate_asset_dependents(task["node_id"])
                        self._save_layout_now(); continue_character = True
                elif task.get("kind") == "storyboard_plan_foundation":
                    try:
                        self._accept_storyboard_foundation(task, data)
                    except Exception as error:
                        repairing = self._repair_storyboard_contract(task, data, error)
                        if not repairing:
                            record = self._custom_record(str(task.get("node_id") or ""))
                            if record is not None:
                                summary = str(error or "未知错误").replace("\n", " ")[:100]
                                record["pipeline_stage"] = ""
                                record["status"] = (
                                    f"第 1 步基础合同不完整：{summary} · 诊断已保存，可点击重试")
                                record["auto_run_enabled"] = False
                                self._save_layout_now()
                            QMessageBox.warning(
                                self, "项目基础合同未通过校验",
                                f"{error}\n\n系统已尝试一次自动结构修复，并保存了诊断信息。"
                                "剧本和已完成进度没有丢失。")
                elif task.get("kind") == "storyboard_plan_batch":
                    try:
                        self._accept_storyboard_batch(task, data)
                    except Exception as error:
                        repairing = self._repair_storyboard_contract(task, data, error)
                        if not repairing:
                            record = self._custom_record(str(task.get("node_id") or ""))
                            if record is not None:
                                checkpoint = record.get("storyboard_plan_checkpoint") or {}
                                completed, total = storyboard_checkpoint_progress(checkpoint)
                                summary = str(error or "未知错误").replace("\n", " ")[:100]
                                record["pipeline_stage"] = ""
                                record["status"] = (
                                    f"第 1 步镜头合同不完整：{summary} · 已保存 {completed}/{total} 镜 · "
                                    "诊断已保存，可点击重试")
                                record["auto_run_enabled"] = False
                                self._save_layout_now()
                            QMessageBox.warning(
                                self, "镜头批次合同未通过校验",
                                f"{error}\n\n系统已尝试一次自动结构修复。"
                                "此前成功的镜头批次仍然保留。")
                elif task.get("kind") == "storyboard_plan":
                    try:
                        self._apply_canvas_storyboard_plan(task["node_id"], data)
                    except Exception as error:
                        record = self._custom_record(str(task.get("node_id") or ""))
                        if record is not None:
                            record["pipeline_stage"] = ""
                            record["status"] = "第 1 步解析失败 · 可点击重试"
                            record["auto_run_enabled"] = False
                            self._save_layout_now()
                        QMessageBox.warning(self, "AI 故事板解析失败", str(error))
                elif task.get("kind") in ("storyboard_panel", "storyboard_panel_reroll"):
                    values = list(data) if isinstance(data, (list, tuple)) else [data]
                    paths = list(dict.fromkeys(
                        str(value or "") for value in values
                        if value and os.path.exists(str(value))))
                    shots = self.current_storyboard().get("shots", [])
                    index = int(task.get("shot_index", -1))
                    frame_index = int(task.get("frame_index", -1))
                    if 0 <= index < len(shots) and paths:
                        shot = shots[index]
                        frames = self._normalize_motion_keyframes(shot)
                        generation_id = str(task.get("generation_id") or "")
                        task_aspect = normalize_aspect_ratio(
                            task.get("aspect_ratio") or "16:9")
                        current_aspect = self._storyboard_production_ratio(
                            str(task.get("source_id") or ""))
                        active_generation = str(
                            shot.get("motion_panel_pending_generation_id") or "")
                        if task_aspect != current_aspect:
                            # Project ratio changed while the provider was running.
                            # Keep the paid output in task history, but never let
                            # stale horizontal pixels enter a vertical contract.
                            continue_storyboard = True
                        elif generation_id and active_generation and generation_id != active_generation:
                            # A late provider response from an abandoned generation
                            # stays in history but can never replace the active board.
                            continue_storyboard = True
                        elif 0 <= frame_index < len(frames):
                            panel_path = paths[0]
                            pending = list(shot.get("motion_panel_pending_paths") or [])
                            if len(pending) != len(frames):
                                pending = list(shot.get("motion_panel_paths") or [])
                            if len(pending) != len(frames):
                                pending = [""] * len(frames)
                            pending[frame_index] = panel_path
                            shot["motion_panel_pending_paths"] = pending
                            assets = shot.setdefault("assets", [])
                            assets.append({
                                "path":panel_path, "kind":"image",
                                "source":f"{task.get('provider') or 'project-image-model'}-motion-panel",
                                "provider":str(task.get("provider") or ""),
                                "subtype":"motion_storyboard_panel",
                                "frame_index":frame_index,
                                "frame_label":f"K{frame_index + 1}",
                                "contract_version":MOTION_STORYBOARD_CONTRACT_VERSION,
                                "aspect_ratio":task_aspect,
                                "version":1 + sum(
                                    1 for value in assets if isinstance(value, dict) and
                                    str(value.get("subtype") or "") == "motion_storyboard_panel" and
                                    int(value.get("frame_index", -1)) == frame_index),
                                "approved":False,
                            })
                            complete = len(pending) == len(frames) and all(
                                value and os.path.exists(str(value)) for value in pending)
                            board_path = ""
                            if complete:
                                board_path = self._commit_motion_storyboard_panels(
                                    shot, index, str(task.get("provider") or ""),
                                    reroll=task.get("kind") == "storyboard_panel_reroll")
                                if board_path:
                                    for asset in assets:
                                        if (isinstance(asset, dict) and
                                                str(asset.get("subtype") or "") ==
                                                "motion_storyboard_panel"):
                                            asset["approved"] = str(asset.get("path") or "") in set(
                                                shot.get("motion_panel_paths") or [])
                                    self._canvas_storyboard_previous = board_path
                            record = self._custom_record(str(
                                task.get("source_id") or self._canvas_storyboard_source or ""))
                            if record is not None:
                                completed_count = sum(
                                    bool(value and os.path.exists(str(value))) for value in pending)
                                if board_path:
                                    qc = shot.get("motion_board_qc") or {}
                                    record["status"] = (
                                        f"镜头 {index + 1:02d} 运动分镜被自动退回："
                                        f"{'、'.join(qc.get('issues') or ['画格QC未通过'])}"
                                        if qc.get("status") == "fail" else
                                        f"镜头 {index + 1:02d} 独立画格已拼板 · 请人工检查")
                                else:
                                    record["status"] = (
                                        f"镜头 {index + 1:02d} 独立画格 "
                                        f"{completed_count}/{len(frames)} 已生成")
                                self._save_layout_now()
                            rebuild_continuity(self.current_storyboard())
                            self.storyboardMutated.emit()
                            continue_storyboard = True
                elif task.get("kind") in ("storyboard_image", "storyboard_reroll"):
                    values = list(data) if isinstance(data, (list, tuple)) else [data]
                    paths = list(dict.fromkeys(
                        str(value or "") for value in values
                        if value and os.path.exists(str(value))))
                    shots = self.current_storyboard().get("shots", [])
                    index = int(task.get("shot_index", -1))
                    if 0 <= index < len(shots) and paths:
                        shot = shots[index]
                        assets = shot.setdefault("assets", [])
                        existing = {str(value.get("path") or "")
                                    for value in assets if isinstance(value, dict)}
                        current = str(shot.get("motion_board_path") or "")
                        # A normal batch keeps an already chosen board. A user-
                        # requested reroll must visibly become the new current
                        # board; the rejected version remains in assets/history.
                        chosen = (paths[0] if task.get("kind") == "storyboard_reroll"
                                  else current if current and os.path.exists(current)
                                  else paths[0])
                        for path in paths:
                            if path in existing:
                                continue
                            assets.append({
                                "path":path, "kind":"image",
                                "source":f"{task.get('provider') or 'project-image-model'}-motion-board",
                                "provider":str(task.get("provider") or ""),
                                "subtype":"motion_storyboard",
                                "frame_count":len(shot.get("motion_keyframes") or []),
                                "contract_version":MOTION_STORYBOARD_CONTRACT_VERSION,
                                "version":len(assets) + 1,
                                "approved":path == chosen,
                            })
                        for asset in assets:
                            if (isinstance(asset, dict) and
                                    str(asset.get("subtype") or "") == "motion_storyboard"):
                                asset["approved"] = str(asset.get("path") or "") == chosen
                        # The first result is a provisional current board. A
                        # reroll or an extra provider output remains a visible
                        # candidate until the user explicitly adopts it.
                        shot["draft_panel"] = chosen
                        shot["motion_board_path"] = chosen
                        shot["preview_asset"] = chosen
                        shot["selected_asset"] = chosen
                        shots[index]["asset_type"] = "image"
                        shots[index]["draft_source"] = "ai"
                        shots[index]["motion_board_contract_version"] = (
                            MOTION_STORYBOARD_CONTRACT_VERSION)
                        motion_qc = self._inspect_motion_board(
                            chosen, len(shot.get("motion_keyframes") or []))
                        shots[index]["motion_board_qc"] = motion_qc
                        auto_rejected = motion_qc.get("status") == "fail"
                        shots[index]["motion_board_review_status"] = (
                            "auto_rejected" if auto_rejected else "pending_review")
                        if auto_rejected:
                            for asset in assets:
                                if (isinstance(asset, dict) and
                                        str(asset.get("path") or "") == chosen):
                                    asset["approved"] = False
                        if task.get("kind") == "storyboard_reroll":
                            shots[index]["motion_board_reroll_count"] = int(
                                shots[index].get("motion_board_reroll_count") or 0) + 1
                        self._canvas_storyboard_previous = chosen
                        source_id = str(task.get("source_id") or
                                        self._canvas_storyboard_source or "")
                        record = self._custom_record(source_id)
                        if record is not None:
                            candidate_suffix = (
                                f" · 本镜 {len(paths)} 个候选待选" if len(paths) > 1 else "")
                            record["status"] = (
                                f"镜头 {index + 1:02d} 分镜被自动退回：动作格过于相似 · 请重新生成"
                                if auto_rejected else
                                f"镜头 {index + 1:02d} 新分镜已切换 · 请人工检查后再进入下一步"
                                if task.get("kind") == "storyboard_reroll" else
                                f"生成图片 {index + 1}/{len(shots)}{candidate_suffix}")
                            self._save_layout_now()
                        rebuild_continuity(self.current_storyboard())
                        self.storyboardMutated.emit()
                        continue_storyboard = task.get("kind") == "storyboard_image"
                elif task.get("kind") == "image_description":
                    text = str(data or "").strip()
                    record = self._custom_record(task["node_id"])
                    if record is not None:
                        record["content"] = text
                        record["editor_action"] = "AI 编辑"
                        record["status"] = "AI 描述已写入"
                    self._save_layout_now()
                elif task.get("kind") == "script":
                    text = str(data or "").strip()
                    for record in self._positions().get("__custom_nodes__", []):
                        if isinstance(record, dict) and record.get("id") == task["node_id"]:
                            if task.get("copywriting"):
                                script_action = str(task.get("script_action") or "")
                                if (script_action.startswith("翻译为") and
                                        not record.get("copy_original")):
                                    record["copy_original"] = str(record.get("content") or "")
                                record["content"] = text
                                record["status"] = f"{script_action}完成"
                                record.pop("script_candidate", None)
                                record.pop("script_review", None)
                                break
                            record["title"] = str(record.get("title") or "剧本工作台")
                            script_action = str(task.get("script_action") or "")
                            if script_action in {"剧本体检", "制片可行性检查"}:
                                record["script_review"] = text
                                record.pop("script_candidate", None)
                                record["status"] = f"{script_action}完成 · 展开节点查看报告"
                            else:
                                record["script_candidate"] = text
                                record.pop("script_review", None)
                                record["status"] = "AI 候选稿待确认 · 原稿未修改"
                            break
                    self._save_layout_now()
                elif task.get("kind") == "director_review":
                    review = extract_json(str(data or ""))
                    repair_plan = self._apply_vision_repair_plan(
                        str(task["node_id"]), review)
                    if task.get("auto_retry"):
                        source_id = self._current_production_source_id()
                        source_node = self._nodes.get(source_id)
                        targets = {str(value.get("target") or "") for value in
                                   repair_plan.get("items", [])}
                        if source_node is not None and targets:
                            if targets <= {"image"}:
                                self.create_canvas_generator_group(source_node, "image")
                                group = next((value for value in reversed(
                                    self._positions().get("__custom_nodes__", []))
                                    if isinstance(value, dict) and
                                    value.get("type") == "workflow_group" and
                                    value.get("source_node_id") == source_node.node_id and
                                    value.get("generator_kind") == "image"), None)
                                if group and group.get("id") in self._nodes:
                                    self.execute_workflow_group(self._nodes[group["id"]])
                            elif targets <= {"video"}:
                                self.create_and_execute_video_group(source_node)
                elif task.get("kind") == "blocking_storyboard":
                    plan = extract_json(str(data or ""))
                    for result in plan.get("shots", []):
                        shot = self._find_shot(str(result.get("id") or ""))
                        if shot is None:
                            continue
                        shot["blocking"] = str(result.get("blocking") or "")
                        shot["eyeline"] = str(result.get("eyeline") or "")
                        shot["spatial_layout"] = str(
                            result.get("spatial_layout") or shot.get("spatial_layout") or "")
                        positions = result.get("character_positions")
                        if isinstance(positions, list):
                            shot["character_positions"] = [
                                value for value in positions if isinstance(value, dict)]
                        shot["camera_position"] = str(
                            result.get("camera_position") or result.get("camera") or
                            shot.get("camera_position") or "")
                        shot["scene_view_id"] = str(
                            result.get("scene_view_id") or shot.get("scene_view_id") or "master")
                        if isinstance(result.get("editable_bbox_xy"), list):
                            shot["editable_bbox_xy"] = result["editable_bbox_xy"]
                        shot["camera_movement"] = str(
                            result.get("camera_movement") or shot.get("camera_movement") or "")
                        shot["axis_rule"] = str(
                            result.get("axis_rule") or result.get("axis") or
                            shot.get("axis_rule") or "")
                        shot["axis"] = shot["axis_rule"]
                        shot["ground_plane"] = str(
                            result.get("ground_plane") or
                            shot.get("ground_plane") or "")
                        if isinstance(result.get("ground_lines"), list):
                            shot["ground_lines"] = [
                                value for value in result["ground_lines"]
                                if isinstance(value, dict)]
                        for field, fallback in (
                                ("horizon_y", 0.4),
                                ("vanishing_point_xy", [0.5, 0.4])):
                            value = result.get(field, shot.get(field, fallback))
                            shot[field] = value
                        shot["camera_slot"] = str(
                            result.get("camera") or shot.get("camera_slot") or
                            shot["camera_position"])
                        for field in ("foreground", "midground", "background",
                                      "frame_start", "frame_end"):
                            shot[field] = str(result.get(field) or shot.get(field) or "")
                        if isinstance(result.get("motion_keyframes"), list):
                            shot["motion_keyframes"] = [
                                value for value in result["motion_keyframes"]
                                if isinstance(value, dict)]
                        self._normalize_motion_keyframes(shot)
                        shot["continuity_note"] = str(result.get("continuity") or "")
                        shot["blocking_ready"] = True
                        shot["production_ready"] = False
                        for key in ("final_image_prompt", "final_start_image_prompt",
                                    "final_end_image_prompt", "final_video_prompt",
                                    "space_geometry_contract"):
                            shot.pop(key, None)
                        if task.get("generate_panels_after"):
                            shot["draft_panel"] = ""
                            shot["motion_board_path"] = ""
                            shot["motion_panel_paths"] = []
                            shot.pop("motion_panel_pending_paths", None)
                            shot.pop("motion_panel_pending_generation_id", None)
                            shot.pop("motion_panel_pending_aspect_ratio", None)
                            shot["preview_asset"] = ""
                    record = self._custom_record(task["node_id"])
                    batch_index = int(task.get("blocking_batch_index") or 0)
                    batch_total = max(1, int(
                        task.get("blocking_batch_total") or 1))
                    if batch_index + 1 < batch_total:
                        if record is not None:
                            record["blocking_batch_next"] = batch_index + 1
                            record["content"] = (
                                f"已完成调度批次 {batch_index + 1}/{batch_total}")
                            record["status"] = (
                                f"阶段 3/6 · 调度已完成 "
                                f"{batch_index + 1}/{batch_total} 批，正在继续")
                        source_node = self._nodes.get(str(task["node_id"]))
                        if source_node is None:
                            raise RuntimeError("调度故事板源节点不存在")
                        self.storyboardMutated.emit(); self._save_layout_now()
                        self.submit_blocking_storyboard(
                            source_node,
                            generate_panels=bool(task.get("generate_panels_after")),
                            _batch_index=batch_index + 1,
                            _batch_size=int(task.get("blocking_batch_size") or 2))
                        continue
                    if record is not None:
                        record.pop("blocking_batch_next", None)
                        record["content"] = (
                            f"已完成 {len(self.current_storyboard().get('shots', []))} "
                            "镜场面调度")
                        record["status"] = ("阶段 3/6 · 调度完成，正在生成多帧运动分镜"
                                            if task.get("generate_panels_after") else
                                            "调度故事板已写回镜头")
                        if task.get("generate_panels_after"):
                            record["pipeline_stage"] = "storyboard_panels_generating"
                    self._bind_scene_contracts_to_shots(str(task["node_id"]))
                    rebuild_continuity(self.current_storyboard())
                    self.storyboardMutated.emit(); self._save_layout_now()
                    if task.get("generate_panels_after"):
                        self._canvas_storyboard_source = str(task["node_id"])
                        self._queue_motion_storyboard_panels(
                            range(len(self.current_storyboard().get("shots", []))))
                        self._canvas_storyboard_previous = ""
                        self._submit_next_canvas_storyboard_image()
                else:
                    values = list(data) if isinstance(data, (list, tuple)) else [data]
                    paths = [str(value or "") for value in values
                             if value and os.path.exists(str(value))]
                    if paths:
                        for record in self._positions().get("__custom_nodes__", []):
                            if isinstance(record, dict) and record.get("id") == task["node_id"]:
                                path = paths[0]
                                record["path"] = path
                                if not record.get("generator_kind"):
                                    record["title"] = Path(path).stem
                                record["actual_provider"] = str(
                                    task.get("provider") or "")
                                record["candidates"] = list(dict.fromkeys(
                                    list(record.get("candidates") or []) + paths))
                                if str(record.get("generator_kind") or "") == "video":
                                    record["candidate_batch_paths"] = list(dict.fromkeys(
                                        list(record.get("candidate_batch_paths") or []) + paths))
                                provider_label = str(task.get("provider") or "自动模型")
                                record["status"] = (
                                    f"生成完成 · {provider_label} · {len(paths)} 个候选")
                                shot_ids = [str(value) for value in
                                            (record.get("shot_ids") or
                                             [record.get("shot_id")]) if value]
                                target_shots = [self._find_shot(shot_id)
                                                for shot_id in shot_ids]
                                target_shots = [shot for shot in target_shots
                                                if shot is not None]
                                kind = str(record.get("generator_kind") or {
                                    "video_node":"video", "audio_node":"audio",
                                }.get(str(record.get("type") or ""), "image"))
                                image_spatial_qc = {}
                                if kind == "image" and record.get("generator_kind") == "image":
                                    image_spatial_qc = {
                                        candidate:self._run_image_spatial_qc(record, candidate)
                                        for candidate in paths}
                                    record["candidate_spatial_qc"] = image_spatial_qc
                                    failed_spatial = sum(
                                        value.get("status") == "fail"
                                        for value in image_spatial_qc.values())
                                    if failed_spatial:
                                        record["status"] = (
                                            f"生成完成 · {failed_spatial} 个候选固定设备漂移 · 已阻止定稿")
                                frames = (self._extract_video_review_frames(path)
                                          if kind == "video" else [])
                                candidate_deterministic = (
                                    inspect_frame_paths(frames) if kind == "video" else {})
                                if kind == "video":
                                    av_sync = inspect_av_sync(path)
                                    candidate_deterministic["av_sync"] = av_sync
                                    if av_sync.get("status") == "fail":
                                        candidate_deterministic["status"] = "fail"
                                        candidate_deterministic.setdefault("issues", []).extend(
                                            value for value in av_sync.get("issues", [])
                                            if value not in candidate_deterministic.get("issues", []))
                                spatial_review = (
                                    self._run_spatial_consistency_review(record, frames)
                                    if kind == "video" and frames else {})
                                if kind == "video" and frames:
                                    record["video_review_frames"] = frames
                                    record["video_tail_frame"] = frames[-1]
                                    record["video_thumbnail"] = (
                                        frames[1] if len(frames) >= 3 else frames[0])
                                    if spatial_review.get("status") == "warn":
                                        record["status"] = (
                                            f"生成完成 · {provider_label} · 空间待复核")
                                segment_offset = 0.0
                                for target_index, shot in enumerate(target_shots):
                                    existing = {str(asset.get("path") or "")
                                                for asset in shot.setdefault("assets", [])
                                                if isinstance(asset, dict)}
                                    for candidate in paths:
                                        if candidate not in existing:
                                            asset_record = {
                                                "path":candidate, "kind":kind,
                                                "source":task.get("provider") or "generator_group",
                                                "generator_node_id":str(record.get("id") or ""),
                                                "frame_role":str(record.get("frame_role") or "")
                                                if kind == "image" else "",
                                                "input_first_frame":str(
                                                    record.get("first_frame") or "")
                                                if kind == "video" else "",
                                                "input_last_frame":str(
                                                    record.get("last_frame") or "")
                                                if kind == "video" else "",
                                                "version":len(shot["assets"]) + 1,
                                            }
                                            if kind == "video" and frames:
                                                asset_record["video_thumbnail"] = (
                                                    frames[1] if len(frames) >= 3 else frames[0])
                                                asset_record["video_review_frames"] = list(frames)
                                                asset_record["spatial_review"] = json.loads(
                                                    json.dumps(spatial_review,
                                                               ensure_ascii=False))
                                                asset_record["deterministic_qc"] = json.loads(
                                                    json.dumps(candidate_deterministic,
                                                               ensure_ascii=False))
                                                asset_record["candidate_number"] = int(
                                                    task.get("video_candidate_number") or 1)
                                                asset_record["approved"] = False
                                            if kind == "image":
                                                asset_record["spatial_qc"] = json.loads(json.dumps(
                                                    image_spatial_qc.get(candidate) or {},
                                                    ensure_ascii=False))
                                            shot["assets"].append(asset_record)
                                    # Image outputs are candidates, not approvals.
                                    # The user must explicitly click “设为定稿图片”
                                    # before stage 6 can consume the frame.
                                    if kind == "audio":
                                        shot[f"selected_{kind}_asset"] = path
                                    if kind == "audio":
                                        shot["dialogue_audio"] = path
                                    elif kind == "image":
                                        shot["preview_asset"] = path
                                        shot["asset_type"] = "image"
                                        shot["status"] = "image_candidates_ready"
                                    elif kind == "video":
                                        shot["status"] = "video_candidates_ready"
                                        # Candidates remain visible beside the
                                        # shot, but no candidate becomes an
                                        # editorial/continuity source until the
                                        # user explicitly adopts it.
                                        segment_offset += float(shot.get("duration") or 5)
                                break
                        self._save_layout_now()
            else:
                task_kind = str(task.get("kind") or "")
                if task_kind in {"clip_qc", "sequence_qc"}:
                    error = handle.result.error if handle.result else "自动审片失败"
                    source_id = str(task.get("source_id") or "")
                    if task_kind == "clip_qc":
                        self._mark_clip_qc_unavailable(
                            str(task.get("node_id") or ""), source_id, str(error))
                    else:
                        unavailable = normalize_sequence_qc({
                            "summary":f"序列审片失败：{error}", "score":100,
                            "passed":True, "transitions":[],
                        })
                        unavailable["status"] = "unavailable"
                        self._finalize_video_qc(source_id, unavailable)
                    self._save_layout_now()
                    continue
                fallbacks = list(task.get("fallback_providers") or [])
                request = task.get("request")
                if fallbacks and request is not None:
                    fallback_name = fallbacks.pop(0)
                    try:
                        fallback_handle = get_ai_manager().submit(fallback_name, request)
                        self._standalone_tasks[fallback_handle.id] = {
                            **task, "handle":fallback_handle, "provider":fallback_name,
                            "fallback_providers":fallbacks,
                        }
                        record = self._custom_record(str(task.get("node_id") or ""))
                        if record is not None:
                            record["status"] = f"{task.get('provider')} 失败 · 已降级到 {fallback_name}"
                        continue
                    except Exception:
                        pass
                self._workflow_failed_nodes.add(str(task.get("node_id") or ""))
                error = handle.result.error if handle.result else "生成失败"
                task_kind = str(task.get("kind") or "")
                task_node_id = str(task.get("node_id") or "")
                failed_record = self._custom_record(task_node_id)
                privacy_blocked = self._is_real_person_privacy_error(error)
                if failed_record is not None:
                    failed_record["status"] = (
                        "Seedance 已拦截首帧 · 疑似可识别真人"
                        if privacy_blocked else f"生成失败 · {str(error)[:120]}")
                    failed_record["generation_blocked"] = (
                        "real_person_privacy" if privacy_blocked else "provider_error")
                    request = task.get("request")
                    inputs = getattr(request, "inputs", {}) if request is not None else {}
                    failed_record["blocked_input"] = str(
                        inputs.get("image") or "") if isinstance(inputs, dict) else ""
                if task_kind in {
                        "storyboard_plan", "storyboard_plan_foundation",
                        "storyboard_plan_batch"}:
                    source = self._custom_record(task_node_id)
                    if source is not None:
                        checkpoint = source.get("storyboard_plan_checkpoint") or {}
                        completed, total = storyboard_checkpoint_progress(checkpoint)
                        source["pipeline_stage"] = ""
                        source["status"] = (
                            f"第 1 步中断 · 已保存 {completed}/{total} 镜 · 点击重试"
                            if task_kind != "storyboard_plan" else
                            f"第 1 步失败 · {error} · 点击重试")
                        source["auto_run_enabled"] = False
                elif task_kind == "storyboard_character":
                    source = self._custom_record(self._canvas_storyboard_source)
                    if source is not None:
                        source["pipeline_stage"] = "shots_ready"
                        source["status"] = f"第 2 步中断 · {error} · 点击重试缺失资产"
                        source["auto_run_enabled"] = False
                elif task_kind in ("blocking_storyboard", "storyboard_image", "storyboard_panel"):
                    source_id = (task_node_id if task_kind == "blocking_storyboard"
                                 else self._canvas_storyboard_source)
                    source = self._custom_record(source_id)
                    if source is not None:
                        source["pipeline_stage"] = "assets_ready"
                        source["status"] = f"第 3 步中断 · {error} · 点击重试"
                        source["auto_run_enabled"] = False
                elif task_kind in ("storyboard_reroll", "storyboard_panel_reroll"):
                    shots = self.current_storyboard().get("shots", [])
                    shot_index = int(task.get("shot_index", -1))
                    if 0 <= shot_index < len(shots):
                        shots[shot_index]["motion_board_review_status"] = "reroll_failed"
                        shots[shot_index].pop("motion_panel_pending_paths", None)
                        shots[shot_index].pop("motion_panel_pending_generation_id", None)
                        shots[shot_index].pop("motion_panel_pending_aspect_ratio", None)
                    source = self._custom_record(str(task.get("source_id") or ""))
                    if source is not None:
                        source["pipeline_stage"] = "storyboard_panels_ready"
                        source["status"] = (
                            f"镜头 {shot_index + 1:02d} 分镜重生失败 · 原版本仍保留，可重试")
                        source["auto_run_enabled"] = False
                self._save_layout_now()
                if (privacy_blocked and failed_record is not None and
                        str(failed_record.get("generator_kind") or "") == "video"):
                    self._show_video_privacy_block(task_node_id, str(error))
                else:
                    friendly = moderation_failure(error) or transient_gateway_failure(error)
                    if friendly is not None:
                        if failed_record is not None:
                            if friendly["code"] == "IMAGE_SAFETY_REVIEW":
                                failed_record["status"] = (
                                    "安全审核未通过 · 修改描述或参考图后重试")
                            elif task_kind in {
                                    "storyboard_plan_foundation",
                                    "storyboard_plan_batch"}:
                                checkpoint = failed_record.get(
                                    "storyboard_plan_checkpoint") or {}
                                completed, total = storyboard_checkpoint_progress(
                                    checkpoint)
                                failed_record["status"] = (
                                    f"AI 服务超时 · 已保存 {completed}/{total} 镜 · "
                                    "点击重试继续")
                            else:
                                failed_record["status"] = (
                                    "AI 服务超时 · 原内容已保留，可直接重试")
                            failed_record["last_failure_code"] = friendly["code"]
                            failed_record["last_request_id"] = friendly["request_id"]
                        QMessageBox.warning(self, friendly["title"], friendly["message"])
                    else:
                        QMessageBox.warning(self, "节点生成失败", str(error))
        finished_video_groups = []
        finished_qc_sources = []
        finished_qc_groups = []
        for task_id in finished:
            task = self._standalone_tasks.pop(task_id, None) or {}
            group_id = str(task.get("workflow_group_id") or "")
            task_kind = str(task.get("kind") or "")
            if group_id and task_kind not in {"clip_qc", "sequence_qc"}:
                finished_video_groups.append((group_id, str(task.get("node_id") or "")))
            if task_kind == "clip_qc":
                finished_qc_sources.append(str(task.get("source_id") or ""))
                if group_id:
                    finished_qc_groups.append(
                        (group_id, str(task.get("node_id") or "")))
        if finished:
            self._refresh_workflow_group_statuses()
            self.refresh()
        for group_id, node_id in finished_video_groups:
            # A provider fallback carries the same group id.  Do not advance
            # until that replacement task has also reached a terminal state.
            if any(str(task.get("workflow_group_id") or "") == group_id
                   for task in self._standalone_tasks.values()):
                continue
            self._submit_next_serial_video(group_id, node_id)
        for group_id, node_id in finished_qc_groups:
            generator = self._custom_record(node_id) or {}
            if generator.get("handoff_approved"):
                group = self._custom_record(group_id)
                if group is not None:
                    group["awaiting_video_node_id"] = ""
                self._submit_next_serial_video(group_id)
        for source_id in dict.fromkeys(value for value in finished_qc_sources if value):
            self._maybe_start_sequence_qc(source_id)
        if continue_storyboard:
            try:
                self._submit_next_canvas_storyboard_image()
            except Exception as error:
                QMessageBox.warning(self, "AI 分镜图片生成失败", str(error))
        elif continue_character:
            try:
                self._submit_next_canvas_character()
            except Exception as error:
                QMessageBox.warning(self, "角色设定生成失败", str(error))

    def _refresh_workflow_group_statuses(self):
        active_nodes = {str(task.get("node_id") or "") for task in self._standalone_tasks.values()
                        if not task["handle"].is_finished}
        auto_sources = []
        qc_sources = []
        for record in self._positions().get("__custom_nodes__", []):
            if not isinstance(record, dict) or record.get("type") != "workflow_group":
                continue
            members = set(record.get("group_nodes") or [])
            generator_kind = str(record.get("generator_kind") or "image")
            source_id = str(record.get("source_node_id") or "")
            if generator_kind == "image":
                start_members = [value for value in members if str(
                    (self._custom_record(value) or {}).get("frame_role") or "") == "start"]
                end_members = [value for value in members if str(
                    (self._custom_record(value) or {}).get("frame_role") or "") == "end" and
                    not bool((self._custom_record(value) or {}).get("invalidated"))]
                if start_members and end_members:
                    active = members & active_nodes
                    if active:
                        phase = str(record.get("endpoint_phase") or "start")
                        record["status"] = (
                            f"{'起始帧' if phase == 'start' else '结束帧'}生成中 · "
                            f"{len(active)} 项")
                    else:
                        starts_done = all(os.path.exists(str(
                            (self._custom_record(value) or {}).get("path") or ""))
                            for value in start_members)
                        ends_done = all(os.path.exists(str(
                            (self._custom_record(value) or {}).get("path") or ""))
                            for value in end_members)
                        source = self._custom_record(source_id)
                        failed_members = members & self._workflow_failed_nodes
                        if failed_members:
                            record["status"] = f"首尾帧生成中断 · {len(failed_members)} 项失败"
                            if source is not None:
                                source["pipeline_stage"] = "production_interrupted"
                                source["interrupted_kind"] = "image"
                                source["auto_run_enabled"] = False
                                source["status"] = "图片生成中断 · 可从未完成节点继续"
                        elif starts_done and not ends_done and str(
                                record.get("endpoint_phase") or "start") == "start":
                            record["status"] = "起始帧候选就绪 · 等待确认 K1"
                            if source is not None:
                                source["pipeline_stage"] = "start_image_candidates_ready"
                                source["status"] = "起始帧候选已生成 · 请逐镜确认 K1"
                            auto_sources.append(source_id)
                        elif ends_done:
                            record["status"] = "首尾帧候选全部生成"
                            if source is not None:
                                source["pipeline_stage"] = "image_candidates_ready"
                                source["status"] = "结束帧候选已生成 · 请逐镜确认 Klast"
                            auto_sources.append(source_id)
                    for batch in self._positions().get("__production_batches__", []):
                        if not isinstance(batch, dict) or batch.get("group_id") != record.get("id"):
                            continue
                        batch["completed"] = sum(os.path.exists(str(
                            (self._custom_record(node_id) or {}).get("path") or ""))
                            for node_id in members)
                        batch["failed"] = len(members & self._workflow_failed_nodes)
                        batch["status"] = "running" if active else (
                            "complete" if ends_done else "awaiting_start_approval")
                    continue
            if members & active_nodes:
                record["status"] = f"执行中 · {len(members & active_nodes)} 项"
            elif str(record.get("status") or "").startswith("执行中"):
                failed = len(members & self._workflow_failed_nodes)
                record["status"] = f"完成但有 {failed} 项失败" if failed else "整组执行完成"
                source_id = str(record.get("source_node_id") or "")
                source = self._custom_record(source_id)
                if source is not None:
                    media_label = {"video":"视频", "audio":"音频"}.get(
                        generator_kind, "图片")
                    source["status"] = (f"{media_label}生产完成 · {failed} 镜失败，右键生成器组重试"
                                        if failed else
                                        ("视频镜头已全部生成" if generator_kind == "video" else
                                         ("对白音频已全部生成" if generator_kind == "audio" else
                                          "图片候选已生成 · 请在画布逐镜定稿")))
                    source["pipeline_stage"] = (
                        "production_interrupted" if failed and generator_kind in {"video", "audio"} else
                        ("video_qc_pending" if generator_kind == "video" else
                         ("production_ready" if generator_kind == "audio" else
                          "image_candidates_ready")))
                    if failed and generator_kind in {"video", "audio"}:
                        source["interrupted_kind"] = generator_kind
                        source["auto_run_enabled"] = False
                    if generator_kind == "audio":
                        source["auto_run_enabled"] = False
                    if generator_kind == "video" and not failed:
                        source["status"] = "视频段已全部生成 · 正在执行自动审片"
                        qc_sources.append(source_id)
                    elif not failed:
                        auto_sources.append(source_id)
            for batch in self._positions().get("__production_batches__", []):
                if not isinstance(batch, dict) or batch.get("group_id") != record.get("id"):
                    continue
                completed = sum(os.path.exists(str((self._custom_record(node_id) or {}).get("path") or ""))
                                for node_id in members)
                failed = len(members & self._workflow_failed_nodes)
                batch["completed"] = completed; batch["failed"] = failed
                if not (members & active_nodes):
                    batch["status"] = "failed" if failed else (
                        "complete" if completed == len(members) else "ready")
        self._save_layout_now()
        for source_id in dict.fromkeys(qc_sources):
            self._maybe_start_sequence_qc(source_id)
        for source_id in dict.fromkeys(auto_sources):
            self._schedule_auto_continue(source_id, from_async=True)

    def activate_node(self, node):
        if node.node_type in ("scene", "character", "element"):
            kind = node.payload["kind"]
            asset_id = str(node.payload.get("asset_id") or "")
            # 旧分镜绑定节点只是兼容投影，不在画布内直接改 DB。
            # 双击时进入（或创建）独立画布快照，之后所有修改只落画布。
            existing = next((record for record in
                             self._positions().get("__custom_nodes__", [])
                             if isinstance(record, dict) and
                             str(record.get("source_library_id") or "") == asset_id and
                             str(record.get("source_library_kind") or "") == kind), None)
            if existing:
                self.focus_node(str(existing.get("id") or ""))
                return
            item = getattr(self.db, f"get_{kind}")(asset_id)
            if item:
                snapshot_id = self.create_custom_node(
                    "image_node", node.pos() + QPointF(36.0, 36.0),
                    self._library_asset_snapshot(item, kind))
                self.focus_node(snapshot_id)
        elif node.node_type == "director":
            self.directorRequested.emit("director")
        elif node.node_type in ("asset_view", "asset_take"):
            path = node.payload.get("path", "")
            if path and os.path.exists(path):
                self.open_media_preview(path, "image")
        elif node.node_type == "shot":
            self.shotRequested.emit(data_id(node))
        elif node.node_type == "shot_take":
            path = str(node.payload.get("path") or "")
            if path and os.path.exists(path):
                self.open_media_preview(
                    path, str(node.payload.get("kind") or "image"))
        elif node.node_type == "video_analysis_node":
            self.run_video_breakdown(node)
        elif node.node_type in ("image_node", "video_node", "audio_node"):
            path = str(node.payload.get("path") or "")
            if path and os.path.exists(path):
                kind = {"image_node": "image", "video_node": "video",
                        "audio_node": "audio"}[node.node_type]
                self.open_media_preview(path, kind)

    def run_video_breakdown(self, node):
        path = str(node.payload.get("path") or "")
        if not path or not os.path.exists(path):
            QMessageBox.information(self, "AI 自动拉片", "源视频文件不存在。")
            return
        if getattr(self, "_video_breakdown_worker", None) is not None:
            worker = self._video_breakdown_worker
            if worker.isRunning():
                QMessageBox.information(self, "AI 自动拉片", "已有拉片任务正在运行。")
                return
        record = self._custom_record(str(node.node_id))
        if record is not None:
            record["status"] = "正在检测切镜与节奏"
        node.badge = "分析中"; node.update(); self._save_layout_now()
        output_dir = str(Path(__file__).parents[2] / "work_temp" / "video_breakdown" /
                         _short_id(path))
        worker = _VideoBreakdownWorker(path, output_dir, self)
        self._video_breakdown_worker = worker
        worker.completed.connect(
            lambda result, nid=str(node.node_id): self._finish_video_breakdown(nid, result))
        worker.failed.connect(
            lambda error, nid=str(node.node_id): self._fail_video_breakdown(nid, error))
        worker.start()

    def _finish_video_breakdown(self, node_id: str, result: dict):
        record = self._custom_record(node_id)
        if record is None:
            return
        shots = list(result.get("shots") or [])
        rows = [
            f"视频时长 {float(result.get('duration') or 0):.2f}s · "
            f"检测到 {len(shots)} 镜 · 平均镜长 "
            f"{float(result.get('average_shot_length') or 0):.2f}s · "
            f"{result.get('rhythm') or '节奏待定'}"
        ]
        source_pos = self._positions().get(node_id, [80.0, -260.0])
        for index, shot in enumerate(shots):
            rows.append(
                f"镜头 {int(shot['number']):02d}｜{float(shot['start']):.2f}–"
                f"{float(shot['end']):.2f}s｜{shot['motion_label']}｜"
                f"运镜：{shot.get('camera_motion') or '待判断'}｜"
                f"主体轨迹：{shot.get('subject_trajectory') or '待判断'}｜"
                f"轨迹置信度 {float(shot.get('trajectory_confidence') or 0):.0%}")
            child_id = self.create_custom_node("image_node", QPointF(
                float(source_pos[0]) + 430 + (index % 3) * 310,
                float(source_pos[1]) + (index // 3) * 300), {
                    "title":f"拉片镜头 {int(shot['number']):02d}",
                    "path":str(shot.get("keyframe") or ""),
                    "content":rows[-1], "status":"代表帧",
                })
            self._remember_workflow_edge(node_id, child_id, "breakdown_shot")
        record = self._custom_record(node_id)
        if record is not None:
            record["analysis_result"] = result
            record["content"] = "\n".join(rows)
            record["status"] = f"拉片完成 · {len(shots)} 镜"
        self._save_layout_now(); self.refresh(); self.focus_node(node_id)
        self._video_breakdown_worker = None

    def _fail_video_breakdown(self, node_id: str, error: str):
        record = self._custom_record(node_id)
        if record is not None:
            record["status"] = "拉片失败"
        self._save_layout_now(); self._video_breakdown_worker = None
        QMessageBox.warning(self, "AI 自动拉片失败", str(error)[:500])

    def open_media_preview(self, path: str, kind: str = "image"):
        if not path or not os.path.exists(path):
            QMessageBox.information(self, "无法预览", "结果文件不存在或已经被移动。")
            return
        try:
            from ui.media_preview import open_single_media_preview
            open_single_media_preview(path, kind, self)
        except Exception as error:
            QMessageBox.warning(self, "无法预览", f"打开预览失败：{error}")

    def _combined_preview_inputs(self):
        """Collect unique rendered segments and shot-timed TTS without mutating approvals."""
        clips = []
        by_key = {}
        for shot in self.current_storyboard().get("shots", []):
            path = str(shot.get("selected_video_asset") or "")
            if not path or not os.path.exists(path):
                continue
            generator_id = str(shot.get("video_segment_node_id") or "")
            key = generator_id or os.path.normcase(os.path.abspath(path))
            clip = by_key.get(key)
            if clip is None:
                record = self._custom_record(generator_id) or {}
                clip = {
                    "key":key, "path":path, "generator_id":generator_id,
                    "duration":float(record.get("timeline_duration") or
                                     record.get("duration") or 0),
                    "shots":[],
                }
                by_key[key] = clip
                clips.append(clip)
            clip["shots"].append(shot)
        for clip in clips:
            if clip["duration"] <= 0:
                clip["duration"] = sum(float(shot.get("duration") or 0)
                                       for shot in clip["shots"])
            clip["duration"] = max(0.25, float(clip["duration"] or 0.25))
        return clips

    @staticmethod
    def _preview_file_signature(paths: list[str]):
        values = []
        for path in paths:
            try:
                stat = os.stat(path)
                values.append((os.path.abspath(path), stat.st_mtime_ns, stat.st_size))
            except OSError:
                values.append((os.path.abspath(path), 0, 0))
        return hashlib.sha1(json.dumps(values, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]

    def preview_current_production(self, source_id=""):
        """Render a cached canvas review movie with shot TTS replacing model audio."""
        if self._preview_render_process is not None and self._preview_render_process.poll() is None:
            return
        clips = self._combined_preview_inputs()
        if not clips:
            QMessageBox.information(self, "联合预览", "当前工程还没有已定稿的视频段。")
            return
        audio_events = []
        timeline = 0.0
        for clip in clips:
            local_offset = 0.0
            for shot in clip["shots"]:
                audio = str(shot.get("dialogue_audio") or "")
                stored_offset = shot.get("video_segment_offset")
                try:
                    offset = float(stored_offset) if stored_offset is not None else local_offset
                except (TypeError, ValueError):
                    offset = local_offset
                if audio and os.path.exists(audio):
                    audio_events.append({"path":audio, "start":timeline + max(0.0, offset)})
                local_offset += float(shot.get("duration") or 0)
            timeline += float(clip["duration"])

        all_paths = [clip["path"] for clip in clips] + [event["path"] for event in audio_events]
        signature = self._preview_file_signature(all_paths)
        folder = LAYOUT_FILE.parent / "production_previews"
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / f"canvas_review_{signature}.mp4"
        if output.exists() and output.stat().st_size > 1024:
            self.open_media_preview(str(output), "video")
            return
        try:
            from utils.ffmpeg_utils import get_ffmpeg_path
            ffmpeg = get_ffmpeg_path()
            if not os.path.exists(ffmpeg):
                raise FileNotFoundError(ffmpeg)
            source = self._custom_record(str(source_id or self._current_production_source_id())) or {}
            ratio = str(source.get("production_ratio") or "16:9")
            width, height = ((720, 1280) if ratio == "9:16" else (1280, 720))
            command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
            for clip in clips:
                command.extend(["-i", clip["path"]])
            for event in audio_events:
                command.extend(["-i", event["path"]])
            filters = []
            video_labels = []
            for index in range(len(clips)):
                label = f"v{index}"
                filters.append(
                    f"[{index}:v]fps=30,scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,"
                    f"format=yuv420p,setpts=PTS-STARTPTS[{label}]")
                video_labels.append(f"[{label}]")
            filters.append("".join(video_labels) +
                           f"concat=n={len(clips)}:v=1:a=0[review_video]")
            total_duration = max(0.25, timeline)
            filters.append(
                f"anullsrc=r=48000:cl=stereo:d={total_duration:.3f}[review_silence]")
            mix_labels = ["[review_silence]"]
            audio_input_start = len(clips)
            for index, event in enumerate(audio_events):
                label = f"tts{index}"
                delay = max(0, int(round(float(event["start"]) * 1000)))
                filters.append(
                    f"[{audio_input_start + index}:a]aresample=48000,"
                    f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
                    f"adelay={delay}|{delay}[{label}]")
                mix_labels.append(f"[{label}]")
            filters.append(
                "".join(mix_labels) +
                f"amix=inputs={len(mix_labels)}:duration=longest:dropout_transition=0,"
                f"atrim=0:{total_duration:.3f}[review_audio]")
            command.extend([
                "-filter_complex", ";".join(filters),
                "-map", "[review_video]", "-map", "[review_audio]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                "-shortest", str(output),
            ])
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            self._preview_render_process = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=creationflags)
            self._preview_render_output = str(output)
            self._preview_render_timer.start()
            self._update_production_continue_button()
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            self._preview_render_process = None
            QMessageBox.warning(self, "联合预览失败", f"无法合成画布预览：{error}")

    def _poll_combined_preview_render(self):
        process = self._preview_render_process
        if process is None:
            self._preview_render_timer.stop()
            return
        code = process.poll()
        if code is None:
            return
        output = self._preview_render_output
        self._preview_render_process = None
        self._preview_render_output = ""
        self._preview_render_timer.stop()
        self._update_production_continue_button()
        if code == 0 and output and os.path.exists(output):
            self.open_media_preview(output, "video")
        else:
            QMessageBox.warning(
                self, "联合预览失败",
                "FFmpeg 没有完成联合预览。原视频和 TTS 文件均未被修改。")

    def toggle_asset_library(self, visible=None):
        if visible is None:
            visible = self.navigator_panel.isHidden()
        visible = bool(visible)
        self.navigator_panel.setVisible(visible)
        if visible:
            self.asset_library.refresh()
            self.navigator_panel.refresh_outline()

    def _remember_canvas_asset(self, node_id: str, scene_pos: QPointF | None = None):
        positions = self._positions()
        values = [str(value) for value in positions.get("__assets__", [])
                  if str(value).startswith("asset:")]
        if node_id not in values:
            values.append(node_id)
        positions["__assets__"] = values
        if scene_pos is not None:
            positions[node_id] = [round(scene_pos.x(), 2), round(scene_pos.y(), 2)]
        self._save_layout_now()

    def place_asset_on_canvas(self, kind: str, asset_id: str,
                              scene_pos: QPointF | None = None):
        """把资产库项复制为画布快照。

        快照不保留可回写的数据库绑定；资产库后续修改或删除时，
        已放入画布的节点不会变化。
        """
        if kind not in ("scene", "character", "element"):
            return
        item = getattr(self.db, f"get_{kind}")(asset_id)
        if not item:
            return
        self._positions()["__canvas_authority__"] = 1
        if scene_pos is None:
            center = self.view.mapToScene(self.view.viewport().rect().center())
            offset = len(self._positions().get("__custom_nodes__", [])) % 6
            scene_pos = center + QPointF(offset * 28.0, offset * 22.0)
        node_id = self.create_custom_node(
            "image_node", scene_pos, self._library_asset_snapshot(item, kind))
        self.focus_node(node_id)

    def _library_asset_snapshot(self, item, kind: str) -> dict:
        refs = [str(path) for path in
                (getattr(item, "reference_images", []) or []) if path]
        master = str(approved_asset_path(item) or "")
        if master:
            refs = [master] + [path for path in refs if path != master]
        label = {"scene": "场景", "character": "主体", "element": "元素"}[kind]
        return {
            "title": str(getattr(item, "name", "") or f"未命名{label}"),
            "content": str(getattr(item, "description", "") or ""),
            "path": master or (refs[0] if refs else ""),
            "references": refs[:50],
            "reference_assets": [
                {"path": path, "role": kind, "label": f"{label}参考"}
                for path in refs[:50]],
            "reference_role": kind,
            "asset_kind": kind,
            "source_library_id": str(getattr(item, "id", "") or ""),
            "source_library_kind": kind,
            "library_snapshot": True,
            "ratio": "16:9" if kind == "scene" else ("2:3" if kind == "character" else "1:1"),
            "editor_action": "AI 编辑" if refs else "文生图",
            "status": "资产库快照 · 已与原资产解耦",
        }

    def _migrate_explicit_library_assets(self) -> bool:
        """将旧工程中手动铺到画布的 DB 资产一次性转成快照。"""
        positions = self._positions()
        legacy_ids = [str(value) for value in positions.get("__assets__", [])
                      if str(value).startswith("asset:")]
        if not legacy_ids:
            return False
        import uuid
        records = positions.setdefault("__custom_nodes__", [])
        changed = False
        for legacy_id in legacy_ids:
            parts = legacy_id.split(":", 2)
            if len(parts) != 3 or parts[1] not in ("scene", "character", "element"):
                continue
            kind, asset_id = parts[1], parts[2]
            item = getattr(self.db, f"get_{kind}")(asset_id)
            if item is None:
                continue
            record = {
                "id": f"custom:{uuid.uuid4().hex[:12]}",
                "type": "image_node",
                **self._library_asset_snapshot(item, kind),
            }
            records.append(record)
            if legacy_id in positions:
                positions[record["id"]] = list(positions[legacy_id])
            changed = True
        positions["__assets__"] = []
        positions["__canvas_authority__"] = 1
        # 即使旧资产已从 DB 删除，也要持久化清理结果，避免每次打开重复迁移。
        return changed or bool(legacy_ids)

    def open_library_asset(self, kind: str, asset_id: str):
        if kind not in ("scene", "character", "element"):
            return
        item = getattr(self.db, f"get_{kind}")(asset_id)
        if item:
            self.asset_inspector.load(item, kind, KIND_META[kind]["accent"])
            self.asset_inspector.set_visible(True)
            self.show_asset_drawer(f"编辑{KIND_META[kind]['label']} · {getattr(item, 'name', '')}")

    def remove_selected_from_canvas(self):
        nodes = [item for item in self.scene.selectedItems()
                 if isinstance(item, CanvasNodeItem) and
                 item.node_type in ("scene", "character", "element")]
        if len(nodes) != 1:
            QMessageBox.information(self, "移出画布", "请选择一个场景、主体或元素资产节点。")
            return
        node = nodes[0]
        self.remove_asset_from_canvas(
            str(node.payload.get("kind") or ""),
            str(node.payload.get("asset_id") or ""))

    def _detach_generator_outputs(self, generator_id: str, clear_record=True):
        """Detach only one generator's media from its target shots."""
        generator_id = str(generator_id or "")
        record = self._custom_record(generator_id)
        if record is None:
            return 0
        kind = str(record.get("generator_kind") or "")
        if kind not in ("image", "video", "audio"):
            return 0
        output_paths = {str(record.get("path") or "")}
        output_paths.update(str(value) for value in record.get("candidates", []) if value)
        output_paths.discard("")
        shot_ids = {str(value) for value in
                    (record.get("shot_ids") or [record.get("shot_id")]) if value}
        removed = 0
        for shot in self.current_storyboard().get("shots", []):
            if str(shot.get("id") or "") not in shot_ids:
                continue
            old_assets = [value for value in shot.get("assets", [])
                          if isinstance(value, dict)]
            new_assets = []
            removed_paths = set()
            for asset in old_assets:
                asset_path = str(asset.get("path") or "")
                same_generator = str(asset.get("generator_node_id") or "") == generator_id
                legacy_match = asset_path in output_paths and str(asset.get("kind") or "") == kind
                if same_generator or legacy_match:
                    removed += 1
                    removed_paths.add(asset_path)
                else:
                    new_assets.append(asset)
            shot["assets"] = new_assets
            for key in ("preview_asset", "selected_asset", "selected_image_asset",
                        "anchor_frame_id", "selected_end_image_asset",
                        "end_anchor_frame_id", "selected_video_asset", "dialogue_audio"):
                if str(shot.get(key) or "") in removed_paths:
                    shot[key] = ""
            if kind == "video" and str(shot.get("video_segment_node_id") or "") == generator_id:
                for key in ("video_segment_node_id", "video_segment_offset",
                            "video_segment_duration", "video_review_frames",
                            "video_tail_frame"):
                    shot.pop(key, None)
            if not shot.get("selected_asset"):
                replacement = str(shot.get("selected_video_asset") or
                                  shot.get("selected_image_asset") or "")
                shot["selected_asset"] = replacement
                shot["preview_asset"] = replacement
                shot["asset_type"] = ("video" if shot.get("selected_video_asset") else
                                      "image" if shot.get("selected_image_asset") else "")
        if clear_record:
            record["path"] = ""
            record["candidates"] = []
            record["status"] = "当前分支候选已清理 · 待重新生成"
            record.pop("actual_provider", None)
        return removed

    def _submit_media_generator_by_id(self, generator_id: str, kind: str):
        node = self._nodes.get(str(generator_id or ""))
        record = self._custom_record(str(generator_id or ""))
        if node is None or record is None:
            return
        self.submit_standalone_generation(
            node, str(record.get("content") or ""),
            "图生视频" if kind == "video" else "图生图")

    def regenerate_media_generator(self, node, kind: str, discard_current=False):
        """Retry exactly one image/video generator without rebuilding its group."""
        generator_id = str(node.node_id)
        record = self._custom_record(generator_id)
        media_label = "视频段" if kind == "video" else "图片节点"
        if record is None or str(record.get("generator_kind") or "") != kind:
            QMessageBox.information(
                self, "单节点重生", f"没有找到这个结果对应的{media_label}生成器。")
            return False
        if kind == "video" and record.get("retry_stop"):
            QMessageBox.information(
                self, "已触发3次止损",
                "这个视频段已经连续3次硬阻断，系统不会继续抽卡。"
                "请执行审片报告中的局部修复：缩短时长、减少动作、拆镜，"
                "或重做关键帧后创建新的视频生成器。")
            return False
        if any(str(task.get("node_id") or "") == generator_id and
               not task["handle"].is_finished for task in self._standalone_tasks.values()):
            QMessageBox.information(
                self, "单节点重生", f"这个{media_label}仍在生成中，请等待完成或先暂停任务。")
            return False
        if discard_current:
            answer = QMessageBox.question(
                self, f"清理并重生当前{media_label}",
                f"只清理这个{media_label}的旧候选并重新生成吗？\n\n"
                f"其他{'视频段和定稿图片' if kind == 'video' else '镜头图片和视频结果'}"
                "以及本地媒体文件都会保留。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return False
            self._detach_generator_outputs(generator_id, clear_record=True)
        if kind == "video":
            self._refresh_video_generator_contract(record)
        elif self._sanitize_motion_board_pixel_references(record):
            node.payload.update(record)
        record["status"] = ("正在重新生成 · 旧版本已保留"
                            if not discard_current else f"正在重新生成当前{media_label}")
        self._workflow_failed_nodes.discard(generator_id)
        self.storyboardMutated.emit()
        self._save_layout_now(); self.refresh(); self.focus_node(generator_id)
        QTimer.singleShot(
            0, lambda node_id=generator_id, media_kind=kind:
            self._submit_media_generator_by_id(node_id, media_kind))
        return True

    def regenerate_video_generator(self, node, discard_current=False):
        return self.regenerate_media_generator(
            node, "video", discard_current=discard_current)

    def regenerate_image_generator(self, node, discard_current=False):
        return self.regenerate_media_generator(
            node, "image", discard_current=discard_current)

    def regenerate_video_result(self, node):
        """Resolve a visible shot result back to its one segment generator."""
        generator = self._result_generator(node, "video")
        if generator is None:
            QMessageBox.information(
                self, "单段重生",
                "这个旧结果没有保存生成器关联。请在对应的视频生成器节点上右键重生；"
                "新生成的结果以后会自动保留关联。")
            return False
        return self.regenerate_video_generator(generator, discard_current=True)

    def regenerate_image_result(self, node):
        """Regenerate only the image generator that produced this candidate."""
        generator = self._result_generator(node, "image")
        if generator is None:
            QMessageBox.information(
                self, "单节点重生",
                "这个旧图片没有保存生成器关联。请在对应的图片生成器节点上右键重生；"
                "新生成的结果以后会自动保留关联。")
            return False
        return self.regenerate_image_generator(generator, discard_current=True)

    def _result_generator(self, node, kind: str):
        shot = self._find_shot(node.payload.get("shot_id"))
        asset = node.payload.get("asset") if isinstance(node.payload.get("asset"), dict) else {}
        generator_id = str(asset.get("generator_node_id") or "")
        if not generator_id and kind == "video":
            generator_id = str((shot or {}).get("video_segment_node_id") or "")
        if generator_id and generator_id in self._nodes:
            return self._nodes[generator_id]
        # Legacy projects did not write generator_node_id into shot assets.
        path = str(node.payload.get("path") or "")
        shot_id = str(node.payload.get("shot_id") or "")
        for record in reversed(self._positions().get("__custom_nodes__", [])):
            if not isinstance(record, dict) or str(record.get("generator_kind") or "") != kind:
                continue
            record_shots = {str(value) for value in
                            (record.get("shot_ids") or [record.get("shot_id")]) if value}
            record_paths = {str(record.get("path") or "")}
            record_paths.update(str(value) for value in record.get("candidates", []) if value)
            if shot_id in record_shots and path in record_paths:
                return self._nodes.get(str(record.get("id") or ""))
        return None

    def delete_video_result_branch(self, node):
        generator = self._result_generator(node, "video")
        if generator is None:
            QMessageBox.information(
                self, "删除视频分支", "这个旧结果没有保存生成器关联，只能移除当前结果。")
            return False
        return self.delete_canvas_branch(generator)

    def delete_image_result_branch(self, node):
        generator = self._result_generator(node, "image")
        if generator is None:
            QMessageBox.information(
                self, "删除图片分支", "这个旧结果没有保存生成器关联，只能移除当前结果。")
            return False
        return self.delete_canvas_branch(generator)

    @staticmethod
    def _record_media_paths(record: dict):
        paths = {str(record.get("path") or "")}
        paths.update(str(value) for value in record.get("candidates", []) if value)
        return {value for value in paths if value}

    @staticmethod
    def _record_input_paths(record: dict):
        paths = {str(record.get("first_frame") or ""),
                 str(record.get("last_frame") or "")}
        paths.update(str(value) for value in record.get("references", []) if value)
        paths.update(str(value.get("path") or "")
                     for value in record.get("reference_assets", [])
                     if isinstance(value, dict))
        return {value for value in paths if value}

    def _custom_branch_ids(self, root_id: str):
        """Resolve explicit graph edges plus media-lineage dependencies."""
        records = {str(value.get("id") or ""): value for value in
                   self._positions().get("__custom_nodes__", [])
                   if isinstance(value, dict) and value.get("id")}
        branch_ids = {str(root_id)}
        changed = True
        while changed:
            changed = False
            output_paths = set()
            for node_id in branch_ids:
                record = records.get(node_id)
                if record is not None:
                    output_paths.update(self._record_media_paths(record))
            for edge in self._positions().get("__workflow_edges__", []):
                if not isinstance(edge, dict) or str(edge.get("source") or "") not in branch_ids:
                    continue
                target = str(edge.get("target") or "")
                if target in records and target not in branch_ids:
                    branch_ids.add(target); changed = True
            if output_paths:
                for node_id, record in records.items():
                    if node_id in branch_ids:
                        continue
                    if output_paths & self._record_input_paths(record):
                        branch_ids.add(node_id); changed = True
        return branch_ids

    def delete_canvas_branch(self, node):
        """Delete a custom node and every graph/media descendant from it."""
        branch_ids = self._custom_branch_ids(str(node.node_id))
        answer = QMessageBox.question(
            self, "删除节点分支",
            f"删除这个节点及其下游 {max(0, len(branch_ids) - 1)} 个节点吗？\n\n"
            "对应镜头中的这一分支候选会移除；其他分支和本地媒体文件会保留。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return False
        for node_id in branch_ids:
            self._detach_generator_outputs(node_id, clear_record=False)
        self.storyboardMutated.emit()
        self.scene.clearSelection()
        for node_id in branch_ids:
            item = self._nodes.get(node_id)
            if item is not None:
                item.setSelected(True)
        self._skip_next_delete_confirmation = True
        self.delete_canvas_selection()
        return True

    def delete_canvas_selection(self):
        """画布对象一律可删；只删除项目引用，不删除本地媒体文件。"""
        nodes = [item for item in self.scene.selectedItems()
                 if isinstance(item, CanvasNodeItem)]
        if not nodes:
            return
        removable = nodes
        skip_confirmation = bool(getattr(self, "_skip_next_delete_confirmation", False))
        self._skip_next_delete_confirmation = False
        if not skip_confirmation:
            hides_qc_report = any(
                str(node.payload.get("auto_qc_kind") or "") == "post_sequence"
                for node in removable)
            warning = (
                "\n\n注意：删除自动审片节点只会隐藏报告，不会清除已经发现的质量问题。"
                "主按钮会变成“恢复审片报告”。"
                if hides_qc_report else "")
            answer = QMessageBox.question(
                self, "删除选中节点",
                f"从画布/项目中移除 {len(removable)} 个选中节点吗？\n\n"
                f"本地图片、视频和音频文件不会删除。{warning}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return

        # 保存删除前的画布结构，Ctrl+Z 可恢复节点、位置和连线。
        self._delete_undo.append({
            "serial":self._next_canvas_action_serial(),
            "positions":json.loads(json.dumps(self._positions(), ensure_ascii=False)),
        })
        self._delete_undo = self._delete_undo[-20:]
        self._position_redo.clear()

        removed_ids = {node.node_id for node in removable}
        changed_storyboard = False
        changed_kinds = set()
        positions = self._positions()
        explicit_assets = list(positions.get("__assets__", []))

        for node in removable:
            if node.node_type in ("scene", "character", "element"):
                kind = str(node.payload.get("kind") or "")
                asset_id = str(node.payload.get("asset_id") or "")
                for shot in self.current_storyboard().get("shots", []):
                    sync_legacy_bindings(shot)
                    if kind == "scene" and asset_id in self._shot_asset_ids(shot, kind):
                        shot["scene_asset_id"] = ""; shot["scene_id"] = ""; shot["scene_version"] = 0
                    elif kind in ("character", "element"):
                        key = f"{kind}_bindings"
                        shot[key] = [value for value in shot.get(key, [])
                                     if not isinstance(value, dict) or value.get("asset_id") != asset_id]
                        ids = [value.get("asset_id") for value in shot[key]
                               if isinstance(value, dict) and value.get("asset_id")]
                        shot[f"{kind}_id"] = ids[0] if ids else ""
                        shot[f"{kind}_ids"] = ids[1:]
                    sync_legacy_bindings(shot)
                explicit_assets = [value for value in explicit_assets
                                   if value != node.node_id]
                positions.pop(node.node_id, None)
                changed_storyboard = True
            elif node.node_type == "shot":
                shot_id = str(node.payload.get("shot_id") or "")
                board = self.current_storyboard()
                board["shots"] = [shot for shot in board.get("shots", [])
                                  if str(shot.get("id") or "") != shot_id]
                for index, shot in enumerate(board.get("shots", [])):
                    shot["number"] = index + 1
                positions.pop(node.node_id, None)
                removed_ids.update(item.node_id for item in self._nodes.values()
                                   if item.node_type in ("shot_take", "generation_task") and
                                   str(item.payload.get("shot_id") or "") == shot_id)
                changed_storyboard = True
            elif node.node_type == "director":
                positions["__hide_director__"] = True
                positions.pop(node.node_id, None)
            elif node.node_type in ("asset_view", "asset_take"):
                data = node.payload
                kind = str(data.get("kind") or "")
                item = getattr(self.db, f"get_{kind}")(
                    data.get("asset_id")) if kind else None
                path = str(data.get("path") or "")
                if item and path and path != approved_asset_path(item):
                    item.reference_images = [value for value in
                        (getattr(item, "reference_images", []) or []) if value != path]
                    item.reference_views = {role: value for role, value in
                        (getattr(item, "reference_views", {}) or {}).items()
                        if value != path}
                    getattr(self.db, f"save_{kind}")(item)
                    changed_kinds.add(kind)
            elif node.node_type == "shot_take":
                shot = self._find_shot(node.payload.get("shot_id"))
                path = str(node.payload.get("path") or "")
                if shot and path:
                    shot["assets"] = [asset for asset in shot.get("assets", [])
                                      if not isinstance(asset, dict) or
                                      str(asset.get("path") or "") != path]
                    for key in ("preview_asset", "selected_asset",
                                "selected_image_asset", "anchor_frame_id",
                                "selected_end_image_asset", "end_anchor_frame_id",
                                "selected_video_asset"):
                        if str(shot.get(key) or "") == path:
                            shot[key] = ""
                    changed_storyboard = True
            elif node.node_type == "generation_task":
                handle = node.payload.get("handle")
                if handle and not handle.is_finished:
                    handle.cancel()
            elif node.payload.get("custom"):
                if str(node.payload.get("auto_qc_kind") or "") == "post_sequence":
                    source_id = str(node.payload.get("source_node_id") or "")
                    source = self._custom_record(source_id) if source_id else None
                    qc_record = self._custom_record(str(node.node_id)) or node.payload
                    if source is not None:
                        source["automatic_qc_node_snapshot"] = json.loads(json.dumps(
                            qc_record, ensure_ascii=False))
                        source["automatic_qc_hidden"] = True
                        source["automatic_qc_status_before_hide"] = str(
                            source.get("status") or "")
                        source["status"] = (
                            "审片报告节点已隐藏 · 质量问题仍未解除 · 点击恢复审片报告")
                positions["__custom_nodes__"] = [data for data in
                    positions.get("__custom_nodes__", []) if not (
                        isinstance(data, dict) and data.get("id") == node.node_id)]
                positions.pop(node.node_id, None)

        positions["__assets__"] = explicit_assets
        positions["__workflow_edges__"] = [value for value in
            positions.get("__workflow_edges__", []) if not (
                isinstance(value, dict) and
                (value.get("source") in removed_ids or value.get("target") in removed_ids))]
        for record in positions.get("__custom_nodes__", []):
            if not isinstance(record, dict) or record.get("type") != "workflow_group":
                continue
            record["group_nodes"] = [value for value in record.get("group_nodes", [])
                                     if str(value) not in removed_ids]
        positions["__production_batches__"] = [batch for batch in
            positions.get("__production_batches__", []) if not (
                isinstance(batch, dict) and str(batch.get("group_id") or "") in removed_ids)]
        for batch in positions.get("__production_batches__", []):
            if not isinstance(batch, dict):
                continue
            batch["node_ids"] = [value for value in batch.get("node_ids", [])
                                 if str(value) not in removed_ids]
            batch["pending_node_ids"] = [value for value in
                batch.get("pending_node_ids", []) if str(value) not in removed_ids]
        self._save_layout_now()
        if changed_storyboard:
            rebuild_continuity(self.current_storyboard())
            self.storyboardMutated.emit()
        for kind in changed_kinds:
            self.assetChanged.emit(kind)
        self.refresh()

    def remove_asset_from_canvas(self, kind: str, asset_id: str):
        """只移除项目画布上的节点，不删除资产库记录。"""
        if kind not in ("scene", "character", "element") or not asset_id:
            return
        node_id = f"asset:{kind}:{asset_id}"
        if node_id in self._bound_asset_node_ids():
            QMessageBox.information(
                self, "资产仍在使用",
                "这个资产已经连接到分镜。请先选择它和对应镜头并点击“解除连接”。")
            return
        positions = self._positions()
        positions["__assets__"] = [
            value for value in positions.get("__assets__", []) if value != node_id]
        positions.pop(node_id, None)
        self._save_layout_now()
        self.refresh()

    @staticmethod
    def _image_ratio(path: str):
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return "16:9"
        ratio = pixmap.width() / max(1, pixmap.height())
        if ratio < 0.72:
            return "9:16"
        if ratio < 0.92:
            return "4:5"
        if ratio < 1.18:
            return "1:1"
        return "16:9"

    def open_image_action(self, node, action: str, prompt: str = ""):
        record = self._custom_record(node.node_id)
        if record is None:
            return
        record["editor_action"] = action
        if prompt:
            record["content"] = prompt
        self._save_layout_now(); self.refresh(); self.focus_node(node.node_id)

    def send_image_to_composer(self, node):
        source_id = str(node.node_id)
        path = str(node.payload.get("path") or node.thumbnail or "")
        if not path or not os.path.exists(path) or not self._is_image_path(path):
            QMessageBox.information(self, "多图生成图片", "当前图片文件不存在。")
            return ""
        target_id = self.create_custom_node(
            "image_node", node.pos() + QPointF(node.width + 150, 0), {
                "title":"多图生成图片",
                "content":"描述基于参考图最终要生成的画面。",
                "multi_image_composer":True,
                "references":[path],
                "reference_assets":[{
                    "path":path, "role":"composition", "label":"参考图01 · 主构图",
                }],
                "editor_action":"AI 编辑", "ratio":self._image_ratio(path),
                "status":"已连接 1 张参考 · 请设置图片用途",
            })
        self._remember_workflow_edge(source_id, target_id, "composition")
        self._save_layout_now(); self.refresh(); self.focus_node(target_id)
        return target_id

    def send_image_to_workbench(self, node):
        source_id = str(node.node_id)
        path = str(node.payload.get("path") or node.thumbnail or "")
        if not path or not os.path.exists(path) or not self._is_image_path(path):
            QMessageBox.information(self, "图片工作台", "当前图片文件不存在。")
            return ""
        target_id = self.create_custom_node(
            "image_node", node.pos() + QPointF(node.width + 150, 0), {
                "title":f"{node.title} · 图片工作台",
                "content":"描述希望如何精修这张图，原图不会被覆盖。",
                "image_workbench":True,
                "references":[path],
                "reference_assets":[{
                    "path":path, "role":"composition", "label":"待精修原图", "required":True,
                }],
                "editor_action":"AI 编辑", "ratio":self._image_ratio(path),
                "status":"图片工作台 · 原图已就绪",
            })
        self._remember_workflow_edge(source_id, target_id, "workbench_source")
        self._save_layout_now(); self.refresh(); self.focus_node(target_id)
        return target_id

    def create_video_from_image(self, node, frame_field="first_frame"):
        path = str(node.payload.get("path") or node.thumbnail or "")
        if not path or not os.path.exists(path) or not self._is_image_path(path):
            QMessageBox.information(self, "图片转视频", "请先让图片节点拥有一个有效结果。")
            return ""
        field = frame_field if frame_field in ("first_frame", "last_frame") else "first_frame"
        target_id = self.create_custom_node(
            "video_node", node.pos() + QPointF(node.width + 150, 0), {
                "title": f"{node.title} · 动态视频",
                "content": "主体自然运动，保持人物身份、服装、场景结构、光线与画面方向一致",
                field: path,
                "ratio": self._image_ratio(path),
                "duration": 5,
                "editor_action": "图生视频",
            })
        relation = "first_frame" if field == "first_frame" else "last_frame"
        self._remember_workflow_edge(node.node_id, target_id, relation)
        record = self._custom_record(target_id)
        if record is not None:
            record["status"] = "首帧已就绪" if field == "first_frame" else "尾帧待补首帧"
        self._save_layout_now(); self.refresh(); self.focus_node(target_id)
        return target_id

    def run_image_quick_action(self, node, action: str):
        if action == "发送到多图生成图片":
            self.send_image_to_composer(node)
            return
        if action == "发送到图片工作台":
            self.send_image_to_workbench(node)
            return
        if action == "AI 编辑":
            self.open_image_action(node, "AI 编辑")
            return
        if action == "图片高清":
            self.submit_standalone_generation(node, "", "图片高清")
            return
        if action == "智能扩图":
            ratios = ["16:9 横屏", "9:16 竖屏", "1:1 方形", "4:5 竖幅"]
            value, ok = QInputDialog.getItem(
                self, "智能扩图", "扩展到什么画幅？", ratios, 0, False)
            if not ok:
                return
            ratio = value.split()[0]
            self.update_custom_setting(node, "ratio", ratio)
            prompt = f"{IMAGE_EDIT_DEFAULTS['智能扩图']}。目标画幅 {ratio}。"
            self.submit_standalone_generation(node, prompt, "智能扩图")
            return
        if action == "移除背景":
            self.submit_standalone_generation(node, "", "移除背景")
            return
        if action == "替换背景":
            background, ok = QInputDialog.getMultiLineText(
                self, "替换背景", "描述新背景；主体身份、姿态和服装会保持不变：", "")
            if not ok or not background.strip():
                return
            prompt = (
                "保持原图主体身份、五官、发型、服装、姿态、比例和前景遮挡完全不变，"
                f"只把背景替换为：{background.strip()}。匹配合理的透视、接触阴影、景深和环境光。")
            self.submit_standalone_generation(node, prompt, "替换背景")
            return
        if action == "AI 看图写描述":
            self.submit_image_description(node)
            return
        if action == "作为视频首帧":
            self.create_video_from_image(node, "first_frame")
            return
        if action == "作为视频尾帧":
            self.create_video_from_image(node, "last_frame")
            return
        role = {
            "设为角色参考": "character", "设为场景参考": "scene",
            "设为风格参考": "style", "设为元素参考": "element",
        }.get(action)
        if role:
            self.set_image_reference_role(node, role)
            return
        if action == "保存到资产库":
            self.save_result_to_library(node)

    def show_image_quick_actions(self, node, screen_pos):
        if node is None or node.node_type != "image_node":
            return
        menu = QMenu(self)
        self._style_popup_menu(menu)
        heading = menu.addAction("这张图片可以做什么")
        heading.setEnabled(False)
        action_map = {}
        action_map[menu.addAction("▦  发送到多图生成图片")] = "发送到多图生成图片"
        action_map[menu.addAction("⌬  发送到图片工作台")] = "发送到图片工作台"
        for label, key in (
                ("✦  让图片动起来", "作为视频首帧"),
                ("☷  AI 看图写描述", "AI 看图写描述")):
            action_map[menu.addAction(label)] = key
        menu.addSeparator()
        reference_menu = menu.addMenu("⌾  设为参考")
        for label in ("设为角色参考", "设为场景参考", "设为风格参考", "设为元素参考"):
            action_map[reference_menu.addAction(label)] = label
        reference_menu.menuAction().setVisible(False)
        frame_menu = menu.addMenu("▹  用作视频帧")
        action_map[frame_menu.addAction("作为视频首帧")] = "作为视频首帧"
        action_map[frame_menu.addAction("作为视频尾帧")] = "作为视频尾帧"
        menu.addSeparator()
        action_map[menu.addAction("保存到资产库…")] = "保存到资产库"
        chosen = menu.exec(screen_pos)
        action = action_map.get(chosen)
        if action:
            self.run_image_quick_action(node, action)

    def show_canvas_import_menu(self, screen_pos=None, scene_pos: QPointF | None = None):
        """程序坞的统一素材入口；导入只写入画布，不自动进入资产库。"""
        menu = QMenu(self)
        self._style_popup_menu(menu)
        image_action = menu.addAction("▧   导入图片到画布")
        video_action = menu.addAction("▹   导入视频到画布")
        audio_action = menu.addAction("▥   导入音频到画布")
        chosen = menu.exec(screen_pos or QCursor.pos())
        upload_spec = {
            image_action:("image_node", "图片 (*.png *.jpg *.jpeg *.webp *.bmp)"),
            video_action:("video_node", "视频 (*.mp4 *.mov *.mkv *.webm *.avi)"),
            audio_action:("audio_node", "音频 (*.mp3 *.wav *.m4a *.aac *.flac *.ogg)"),
        }.get(chosen)
        if not upload_spec:
            return
        path, _ = QFileDialog.getOpenFileName(self, "导入资源到画布", "", upload_spec[1])
        if not path:
            return
        node_id = self.create_custom_node(
            upload_spec[0], scene_pos or self._viewport_center(), {
                "title":Path(path).stem, "path":path, "content":"",
            })
        record = self._custom_record(node_id)
        if record is not None:
            record["status"] = "已导入画布"
            if upload_spec[0] == "video_node":
                frames = self._extract_video_review_frames(path)
                if frames:
                    record["video_review_frames"] = frames
                    record["video_tail_frame"] = frames[-1]
                    record["video_thumbnail"] = (
                        frames[1] if len(frames) >= 3 else frames[0])
        self._save_layout_now(); self.refresh(); self.focus_node(node_id)
        if upload_spec[0] == "image_node":
            QTimer.singleShot(
                0, lambda nid=node_id, pos=screen_pos:
                self.show_image_quick_actions(self._nodes.get(nid), pos))

    def show_new_asset_menu(self, screen_pos, scene_pos: QPointF | None = None):
        menu = QMenu(self)
        self._style_popup_menu(menu)
        title = menu.addAction("创建画布节点")
        title.setEnabled(False)
        sketch_action = menu.addAction("✦   AI 故事板")
        text_action = menu.addAction("☰   剧本工作台")
        copywriting_action = menu.addAction("◉   信息流口播文案")
        multi_image_action = menu.addAction("▦   多图生成图片")
        multi_director_action = menu.addAction("▦   多图导演视频")
        menu.addSeparator()
        basic_menu = menu.addMenu("基础节点")
        video_action = basic_menu.addAction("▹   视频节点")
        audio_action = basic_menu.addAction("▥   音频节点")
        shot_action = basic_menu.addAction("▤   镜头节点")
        tool_menu = menu.addMenu("分析与专业工具")
        breakdown_action = tool_menu.addAction("⌁   AI 自动拉片")
        skill_menu = tool_menu.addMenu("✦   专业 Skill")
        skill_actions = {}
        for skill_id, spec in CANVAS_SKILLS.items():
            if spec.get("hidden"):
                continue
            action = skill_menu.addAction(spec["title"])
            action.setToolTip(spec["description"])
            skill_actions[action] = skill_id
        reference_menu = menu.addMenu("参考节点")
        scene_action = reference_menu.addAction("▣   场景参考")
        character_action = reference_menu.addAction("◇   主体参考")
        element_action = reference_menu.addAction("⬡   元素参考")
        templates = list(self._positions().get("__workflow_templates__", []))
        template_menu = menu.addMenu("⌘   复用工作流")
        template_menu.setEnabled(bool(templates))
        template_actions = {}
        for template in templates:
            action = template_menu.addAction(str(template.get("name") or "未命名工作流"))
            template_actions[action] = template
        chosen = menu.exec(screen_pos)
        if chosen is None:
            # 点击菜单外部只是取消；不能与已隐藏的普通图片入口共用 None。
            return
        if chosen in skill_actions:
            self.create_canvas_skill(skill_actions[chosen], scene_pos or self._viewport_center())
            return
        if chosen in template_actions:
            self.instantiate_workflow_template(template_actions[chosen],
                                               scene_pos or self._viewport_center())
            return
        custom_type = {
            text_action: "text_node",
            video_action: "video_node", audio_action: "audio_node",
        }.get(chosen)
        if custom_type:
            self.create_custom_node(custom_type, scene_pos or self._viewport_center())
            return
        if chosen == copywriting_action:
            self.create_custom_node("text_node", scene_pos or self._viewport_center(), {
                "title":"信息流口播文案", "content":"", "copywriting_workbench":True,
                "product_name":"", "product_description":"", "copy_style":"激情抓眼球",
                "copy_duration":"30", "copy_language":"英语",
                "editor_action":"生成口播文案", "status":"填写产品信息后生成",
            })
            return
        if chosen == multi_image_action:
            self.create_custom_node("image_node", scene_pos or self._viewport_center(), {
                "title":"多图生成图片",
                "content":"说明最终要生成的画面，并为每张参考图指定用途。",
                "multi_image_composer":True,
                "references":[], "reference_assets":[],
                "editor_action":"AI 编辑", "ratio":"16:9",
                "status":"等待连接参考图节点",
            })
            return
        if chosen == multi_director_action:
            self.create_custom_node("video_node", scene_pos or self._viewport_center(), {
                "title":"多图导演视频", "content":"保持参考图片中的主体身份、场景结构、"
                "道具外观、光线方向和视觉风格连续。",
                "multi_image_director":True, "timeline_images":[],
                "references":[], "reference_assets":[], "provider_name":"seedance",
                "duration":10, "ratio":"16:9", "generator_kind":"video",
                "editor_action":"图生视频", "status":"等待连接图片节点",
            })
            return
        if chosen == breakdown_action:
            self.create_custom_node(
                "video_analysis_node", scene_pos or self._viewport_center(), {
                    "title":"AI 自动拉片",
                    "content":"把上传或生成的视频节点连接到这里，然后双击本节点开始拉片。",
                    "status":"等待连接视频节点", "analysis_result":{},
                })
            return
        if chosen == shot_action:
            self.new_shot()
            return
        if chosen == sketch_action:
            self.open_handdraw_storyboard()
            return
        kind = {
            scene_action: "scene",
            character_action: "character",
            element_action: "element",
        }.get(chosen)
        if kind:
            self.new_asset(kind, scene_pos)

    def create_custom_node(self, node_type: str, scene_pos: QPointF,
                           initial_payload: dict | None = None):
        import uuid
        labels = {
            "text_node": "剧本工作台", "image_node": "图片节点",
            "video_node": "视频节点", "audio_node": "音频节点",
            "video_analysis_node": "AI 自动拉片",
            "storyboard_node": "AI 故事板",
            "skill_node": "专业 Skill",
        }
        node_id = f"custom:{uuid.uuid4().hex[:12]}"
        values = self._positions().setdefault("__custom_nodes__", [])
        record = {
            "id": node_id, "type": node_type,
            "title": f"{labels.get(node_type, '节点')} {len(values) + 1}",
            "content": "", "path": "",
        }
        if initial_payload:
            for key in ("title", "content", "path", "references", "reference_assets",
                        "reference_role", "first_frame", "last_frame", "first_frame_override",
                        "last_frame_override", "planned_first_frame", "planned_last_frame",
                        "editor_action", "script_versions", "script_version", "script_locked",
                        "script_review", "script_candidate",
                        "source_script_id", "source_script_version",
                        "marked", "style",
                        "ratio", "model", "shot_count", "skill_id", "strength",
                        "provider_name", "candidate_count", "video_candidate_count",
                        "voice", "speed", "emotion",
                        "duration", "voice_name", "automation_mode", "auto_run_enabled",
                        "pipeline_stage", "status", "asset_kind", "asset_version",
                        "source_library_id", "source_library_kind", "library_snapshot",
                        "locked", "adopted", "shot_id", "shot_ids", "generator_kind",
                        "source_node_id", "group_nodes", "candidates",
                        "source_script_id", "source_script_version",
                        "planning_provider", "planning_model", "planning_temperature",
                        "production_scope", "production_ratio",
                        "image_provider", "video_provider", "video_generation_mode",
                        "frame_role", "scene_master_path", "space_geometry_contract",
                        "location_id", "scene_states", "scene_master", "scene_variant_of",
                        "state_preview_path", "scene_reference_set", "scene_proxy",
                        "scene_proxy_signature", "scene_view_role", "scene_view_id",
                        "editable_bbox_xy", "edit_mask_path", "spatial_qc",
                        "video_thumbnail", "video_review_frames", "video_tail_frame",
                        "multi_image_director", "timeline_images", "multi_image_composer",
                        "image_workbench", "copywriting_workbench", "product_name",
                        "product_description", "copy_style", "copy_duration",
                        "copy_language", "copy_original",
                        "analysis_result"):
                if key in initial_payload:
                    record[key] = json.loads(json.dumps(initial_payload[key], ensure_ascii=False))
        values.append(record)
        self._positions()[node_id] = [round(scene_pos.x(), 2), round(scene_pos.y(), 2)]
        self._save_layout_now()
        self.refresh()
        self.focus_node(node_id)
        return node_id

    def create_canvas_skill(self, skill_id: str, scene_pos: QPointF):
        spec = CANVAS_SKILLS.get(skill_id)
        if spec is None:
            return ""
        references = []
        source_node_id = ""
        for item in self.scene.selectedItems():
            if not isinstance(item, CanvasNodeItem):
                continue
            if item.node_type == "storyboard_node":
                source_node_id = str(item.node_id)
            path = str(item.payload.get("path") or item.thumbnail or "")
            if path and os.path.exists(path):
                references.append(path)
        return self.create_custom_node("skill_node", scene_pos, {
            "title": spec["title"], "content": spec["description"],
            "skill_id": skill_id, "strength": 0.65,
            "source_node_id": source_node_id,
            "references": list(dict.fromkeys(references))[:9],
        })

    def _skill_prompt_variants(self, skill_id: str, instruction: str):
        if skill_id == "camera_grid_9":
            shots = ("超远景建立空间", "全景人物与环境", "中全景调度", "中景表演",
                     "近景情绪", "面部特写", "肩后反打", "低机位仰拍", "高机位俯拍")
            return [f"{instruction}。机位方案 {index + 1}/9：{shot}。保持同一角色、服装、场景、时刻与轴线。"
                    for index, shot in enumerate(shots)]
        if skill_id == "continuity_grid_25":
            return [f"{instruction}。连贯分镜第 {index + 1}/25 格，表现动作进度 {index / 24:.0%}，"
                    "严格继承上一格人物位置、朝向、服装、光线和场景，只推进一个清晰动作节拍。"
                    for index in range(25)]
        if skill_id == "character_sheet":
            views = ("正面全身", "左侧面全身", "背面全身", "四分之三正面", "四分之三背面",
                     "面部近景", "喜悦表情", "悲伤表情", "愤怒表情")
            return [f"{instruction}。角色设定第 {index + 1}/9：{view}，中性背景，固定五官、发型、体型、服装、材质和配色。"
                    for index, view in enumerate(views)]
        if skill_id == "relight":
            looks = ("柔和窗光", "伦勃朗侧光", "阴天漫射光", "黄金时刻逆光", "蓝调夜景",
                     "霓虹双色光", "硬质顶光", "烛火暖光", "高反差黑色电影")
            return [f"{instruction}。光影方案 {index + 1}/9：{look}。保持人物身份、动作、构图和场景结构完全不变。"
                    for index, look in enumerate(looks)]
        if skill_id == "emotion":
            emotions = ("克制平静", "轻微喜悦", "明显喜悦", "隐忍悲伤", "崩溃悲伤",
                        "警觉恐惧", "强烈恐惧", "压抑愤怒", "爆发愤怒")
            return [f"{instruction}。情绪方案 {index + 1}/9：{emotion}。保持人物身份、服装、镜头与背景不变，"
                    "只调整眼神、眉眼、嘴角、肌肉紧张和身体姿态。"
                    for index, emotion in enumerate(emotions)]
        return []

    def execute_canvas_skill(self, node, execute=True):
        record = self._custom_record(node.node_id)
        if record is None:
            return
        skill_id = str(record.get("skill_id") or "")
        spec = CANVAS_SKILLS.get(skill_id) or {}
        registry = get_ai_manager().registry
        list_all = getattr(registry, "list_all", None)
        providers = list(list_all()) if callable(list_all) else []
        if not providers:
            seen = set()
            for capability in ("chat", "text_to_image", "image_edit",
                               "image_to_video", "text_to_video", "tts"):
                for provider in registry.by_capability(capability):
                    if id(provider) not in seen:
                        providers.append(provider); seen.add(id(provider))
        capabilities = {capability for provider in providers
                        for capability in getattr(provider, "capabilities", [])}
        if any(getattr(provider, "name", "") == "openai" and
               "chat" in getattr(provider, "capabilities", ("chat",))
               for provider in providers):
            capabilities.add("vision")
        artifacts = {"storyboard"} if self.current_storyboard().get("shots") else set()
        source_id = str(record.get("source_node_id") or self._current_production_source_id())
        if any(str(value.get("source_node_id") or "") == source_id and
               str(value.get("generator_kind") or "") == "video" and
               os.path.exists(str(value.get("path") or ""))
               for value in self._production_skill_records()):
            artifacts.add("rendered_media")
        dependency_issues = skill_runtime_issues(
            skill_id, CANVAS_SKILLS,
            provider_capabilities=capabilities, artifacts=artifacts)
        if dependency_issues:
            record["status"] = "Skill 前置条件未满足"
            record["content"] = "\n".join(dependency_issues)
            self._save_layout_now(); self.refresh()
            return
        instruction = str(record.get("content") or CANVAS_SKILLS.get(skill_id, {}).get("description") or "")
        handler = str(spec.get("handler") or "")
        if skill_id == "production_orchestrator" or handler.endswith(":plan_next_action"):
            source_id = str(record.get("source_node_id") or
                            self._current_production_source_id())
            source = self._custom_record(source_id) if source_id else None
            source_node = self._nodes.get(source_id) if source_id else None
            if source is None or source_node is None:
                record["status"] = "没有可编排的制片项目"
                record["content"] = "请先用“＋ 新建”创建 AI 制片项目，或把 AI 脚本送入制片。"
                self._save_layout_now(); self.refresh()
                return
            stage = str(source.get("pipeline_stage") or "")
            _action, gate = NEXT_ACTION_BY_STAGE.get(stage, ("wait", ""))
            report = (self._evaluate_production_gate(source_id, gate)
                      if gate and stage else None)
            decision = plan_next_action(
                stage, report, str(source.get("automation_mode") or "checkpoints"))
            append_workflow_event(source, decision, status="inspected")
            record["orchestrator_decision"] = decision
            record["content"] = (
                f"当前阶段：{stage or '尚未开始'}\n"
                f"下一动作：{decision.get('intended_action') or '等待'}\n"
                f"技术门禁：{decision.get('reason') or '开始条件可用'}")
            record["status"] = "可以继续" if decision.get("allowed") else "需要先修复输入"
            self._save_layout_now(); self.refresh()
            if execute and decision.get("allowed"):
                source_node = self._nodes.get(source_id)
                if source_node is not None:
                    self.continue_canvas_production(source_node, from_async=False)
            return
        if skill_id == "shot_readiness" or handler.endswith(":evaluate_readiness"):
            source_id = str(record.get("source_node_id") or
                            self._current_production_source_id())
            source = self._custom_record(source_id) if source_id else None
            if source is None:
                record["status"] = "没有可检查的制片项目"
                self._save_layout_now(); self.refresh(); return
            stage = str(source.get("pipeline_stage") or "")
            gate = {
                "":"shot_plan", "shots_ready":"shot_plan",
                "assets_generated":"locked_assets", "assets_changed":"locked_assets",
                "assets_ready":"locked_assets", "storyboard_panels_ready":"blocking",
                "prompts_ready":"prompts", "generators_ready":"prompts",
                "start_image_candidates_ready":"start_frames",
                "image_candidates_ready":"video_anchors", "video_ready":"videos",
                "production_ready":"delivery",
            }.get(stage, "shot_plan")
            strict_end = (gate == "video_anchors" and
                          self._production_requires_end_frames(source_id))
            report = self._evaluate_production_gate(
                source_id, gate, require_end_frame=strict_end)
            decision = plan_next_action(
                stage, report, str(source.get("automation_mode") or "checkpoints"))
            append_workflow_event(source, decision, status="inspected")
            record["readiness_report"] = report.as_dict()
            record["content"] = report.summary(limit=12)
            record["status"] = "就绪检查通过" if not report.blocked else "就绪检查未通过"
            self._save_layout_now(); self.refresh()
            return
        if (skill_id in {"ai_director", "vision_qc_repair"} or
                handler.endswith(":build_repair_plan")):
            # Review is a gate, not an automatic regeneration loop.  Failed
            # shots stay selected so the producer can decide whether to retry.
            self.submit_ai_director_review(node, auto_retry=False)
            return
        if skill_id == "storyboard":
            storyboard_id = self.create_custom_node("storyboard_node", node.pos() + QPointF(420, 0), {
                "title":"故事板生成器", "content":instruction, "style":"电影写实", "shot_count":0})
            self._positions().setdefault("__workflow_edges__", []).append(
                {"source":node.node_id, "target":storyboard_id, "type":"skill"})
            self._save_layout_now(); self.refresh()
            if execute and storyboard_id in self._nodes:
                self.submit_canvas_storyboard(self._nodes[storyboard_id], instruction, "1 · 拆解镜头")
            return
        if skill_id == "blocking_storyboard":
            self.submit_blocking_storyboard(node)
            return
        prompts = self._skill_prompt_variants(skill_id, instruction)
        if not prompts:
            return
        import uuid
        values = self._positions().setdefault("__custom_nodes__", [])
        edges = self._positions().setdefault("__workflow_edges__", [])
        child_ids = []
        references = [str(value) for value in record.get("references", [])
                      if value and os.path.exists(str(value))]
        ratio = "1:1" if skill_id in ("camera_grid_9", "character_sheet") else "16:9"
        columns = 5 if len(prompts) == 25 else 3
        for index, prompt in enumerate(prompts):
            child_id = f"custom:{uuid.uuid4().hex[:12]}"
            child_ids.append(child_id)
            values.append({"id":child_id, "type":"image_node",
                           "title":f"{CANVAS_SKILLS[skill_id]['title']} {index + 1:02d}",
                           "content":prompt, "path":"", "references":references,
                           "ratio":ratio, "candidate_count":1, "status":"待执行",
                           "skill_source":node.node_id, "skill_id":skill_id})
            self._positions()[child_id] = [
                round(node.pos().x() + 560 + (index % columns) * 620, 2),
                round(node.pos().y() + (index // columns) * 440, 2)]
            edges.append({"source":node.node_id, "target":child_id, "type":"skill"})
        group_id = f"custom:{uuid.uuid4().hex[:12]}"
        values.append({"id":group_id, "type":"workflow_group",
                       "title":CANVAS_SKILLS[skill_id]["title"],
                       "content":f"可复用专业 Skill · {len(child_ids)} 个生成节点",
                       "group_nodes":child_ids, "generator_kind":"image",
                       "skill_id":skill_id, "status":"待检查并执行"})
        self._positions()[group_id] = [round(node.pos().x(), 2), round(node.pos().y() + 310, 2)]
        for child_id in child_ids:
            edges.append({"source":group_id, "target":child_id, "type":"group"})
        record["status"] = f"已创建 {len(child_ids)} 个生成节点"
        self._save_layout_now(); self.refresh(); self.focus_node(group_id)
        if execute and group_id in self._nodes:
            self.execute_workflow_group(self._nodes[group_id])

    def show_reference_generation_menu(self, source, screen_pos, scene_pos):
        menu = QMenu(self)
        self._style_popup_menu(menu)
        heading = menu.addAction("引用该节点生成")
        heading.setEnabled(False)
        source_path = str(source.payload.get("path") or source.thumbnail or "")
        source_is_image = bool(
            source_path and os.path.exists(source_path) and self._is_image_path(source_path))
        text_action = menu.addAction("☰   剧本工作台")
        image_action = menu.addAction(
            "▧   基于这张图继续编辑" if source_is_image else "▧   图片")
        video_action = menu.addAction(
            "▹   让这张图动起来（首帧）" if source_is_image else "▹   视频")
        tail_action = menu.addAction("▹   作为尾帧创建视频") if source_is_image else None
        audio_action = menu.addAction("▥   音频")
        chosen = menu.exec(screen_pos)
        action_types = {
            text_action: "text_node", image_action: "image_node",
            video_action: "video_node", audio_action: "audio_node",
        }
        if tail_action is not None:
            action_types[tail_action] = "video_node"
        node_type = action_types.get(chosen)
        if not node_type:
            return
        inherited = {}
        if source.payload.get("custom"):
            inherited["ratio"] = source.payload.get("ratio") or "16:9"
            inherited["model"] = source.payload.get("model") or ""
        if source_is_image and node_type == "image_node":
            source_role = str(source.payload.get("reference_role") or "reference")
            inherited.update({
                "references": [source_path],
                "reference_assets": [{
                    "path": source_path,
                    "role": source_role if source_role in DIRECT_REFERENCE_ROLES else "reference",
                    "label": DIRECT_REFERENCE_ROLES.get(source_role, "上游参考"),
                }],
                "editor_action": "AI 编辑",
            })
        elif source_is_image and node_type == "video_node":
            inherited["editor_action"] = "图生视频"
            inherited["first_frame" if chosen == video_action else "last_frame"] = source_path
        target_id = self.create_custom_node(node_type, scene_pos, inherited)
        relation = ("first_frame" if source_is_image and chosen == video_action else
                    "last_frame" if source_is_image and chosen == tail_action else
                    node_type.removesuffix("_node"))
        self._remember_workflow_edge(source.node_id, target_id, relation)
        self.refresh()
        self.focus_node(target_id)

    @staticmethod
    def _style_popup_menu(menu: QMenu):
        menu.setStyleSheet(
            "QMenu{background:#1b1b22;color:#e5e5eb;border:1px solid #41414d;"
            "border-radius:12px;padding:8px;min-width:290px;}"
            "QMenu::item{padding:11px 18px;border-radius:8px;margin:2px;}"
            "QMenu::item:selected{background:#30303c;color:white;}"
            "QMenu::item:disabled{color:#6f6f78;}"
            "QMenu::separator{height:1px;background:#33333c;margin:7px 10px;}"
        )

    def new_asset(self, kind, scene_pos: QPointF | None = None):
        """在画布上直接创建轻量参考节点。

        资产只在用户主动执行“保存到资产库”时入库，不再为一个
        空资产打开完整 AssetStudioDialog。
        """
        labels = {"scene": "场景参考", "character": "主体参考", "element": "元素参考"}
        ratios = {"scene": "16:9", "character": "2:3", "element": "1:1"}
        label = labels[kind]
        self._positions()["__canvas_authority__"] = 1
        node_id = self.create_custom_node(
            "image_node", scene_pos or self._viewport_center(), {
                "title": label,
                "content": f"描述{label}，或直接上传参考图。",
                "asset_kind": kind,
                "reference_role": kind,
                "ratio": ratios[kind],
                "editor_action": "文生图",
                "status": "画布参考 · 待描述或上传图片",
            })
        self.focus_node(node_id)

    def delete_selected(self):
        nodes = [item for item in self.scene.selectedItems()
                 if isinstance(item, CanvasNodeItem)]
        if len(nodes) != 1 or nodes[0].node_type not in ("scene", "character", "element"):
            QMessageBox.information(self, "删除节点", "请选择一个场景、主体或元素资产节点。")
            return
        node = nodes[0]
        kind = node.payload["kind"]
        self.delete_library_asset(kind, node.payload["asset_id"])

    def delete_library_asset(self, kind: str, asset_id: str):
        """删除资产库记录；可从画布节点、资产卡或检查器调用。"""
        if kind not in ("scene", "character", "element") or not asset_id:
            return
        item = getattr(self.db, f"get_{kind}")(asset_id)
        if not item:
            self.refresh()
            return
        linked = self.linked_shots_for_asset(kind, asset_id)
        impact = (
            f"\n\n它正在 {len(linked)} 个分镜中使用，删除后这些分镜会显示缺少参考素材。"
            if linked else "")
        answer = QMessageBox.question(
            self, "从资产库删除",
            f"确定删除“{getattr(item, 'name', '未命名')}”吗？"
            f"{impact}\n\n资产记录会被删除，已生成的本地图片文件不会被删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        getattr(self.db, DB_MAP[kind][2])(item.id)
        positions = self._positions()
        node_id = f"asset:{kind}:{asset_id}"
        positions["__assets__"] = [
            value for value in positions.get("__assets__", []) if value != node_id]
        positions.pop(node_id, None)
        self._save_layout_now()
        self.scene.clearSelection()
        self.assetChanged.emit(kind)
        self.refresh()

    def _selected_asset_and_shot(self):
        selected = [item for item in self.scene.selectedItems()
                    if isinstance(item, CanvasNodeItem)]
        asset_node = next((item for item in selected if item.node_type in {
            "scene", "character", "element"}), None)
        shot_node = next((item for item in selected if item.node_type == "shot"), None)
        return asset_node, shot_node

    def port_node_at(self, scene_pos: QPointF, direction: str):
        """返回命中连接区域的最上层节点。

        输出从卡片右缘附近起拖；输入允许松开到整张镜头卡片，降低精确操作成本。
        """
        candidates = sorted(self._nodes.values(), key=lambda item: item.zValue(), reverse=True)
        direct = next((node for node in candidates
                       if node.isVisible() and node.hit_port(scene_pos, direction)), None)
        if direct is not None:
            return direct
        if direction == "input":
            return next((node for node in candidates
                         if node.isVisible() and node.has_input_port() and
                         node.sceneBoundingRect().contains(scene_pos)), None)
        # 输出热区覆盖右缘 28px、高度中心上下 32px；圆点仍是视觉锚点。
        for node in candidates:
            if not node.isVisible() or not node.has_output_port():
                continue
            local = node.mapFromScene(scene_pos)
            if (node.width - 28.0 <= local.x() <= node.width + 14.0 and
                    node.height * 0.5 - 32.0 <= local.y() <= node.height * 0.5 + 32.0):
                return node
        return None

    def _remember_workflow_edge(self, source_id: str, target_id: str,
                                data_type: str = "image"):
        edges = self._positions().setdefault("__workflow_edges__", [])
        record = {"source": source_id, "target": target_id, "type": data_type}
        if record not in edges:
            edges.append(record)
            self._save_layout_now()

    def remove_workflow_edge(self, source_id: str, target_id: str):
        edges = self._positions().get("__workflow_edges__", [])
        remaining = [value for value in edges if not (
            isinstance(value, dict) and value.get("source") == source_id and
            value.get("target") == target_id)]
        if len(remaining) == len(edges):
            return False
        self._positions()["__workflow_edges__"] = remaining
        self._save_layout_now()
        self.refresh()
        return True

    def connect_workflow_nodes(self, source: CanvasNodeItem, target: CanvasNodeItem):
        """校验并应用一条用户拖出的强类型连接。"""
        if source is target:
            QMessageBox.information(self, "无法连接", "节点不能连接到自身。")
            return False

        source_path = str(source.payload.get("path") or source.thumbnail or "")
        if (target.payload.get("custom") and
                target.node_type == "video_analysis_node"):
            if (not source_path or not os.path.exists(source_path) or
                    not self._is_video_path(source_path)):
                QMessageBox.information(
                    self, "无法连接",
                    "AI 自动拉片节点只接受已有视频文件的视频节点或视频结果节点。")
                return False
            record = self._custom_record(str(target.node_id))
            if record is None:
                return False
            self._positions()["__workflow_edges__"] = [
                value for value in self._positions().get("__workflow_edges__", [])
                if not (isinstance(value, dict) and
                        value.get("target") == target.node_id and
                        value.get("type") == "breakdown_source")]
            record["path"] = source_path
            record["source_video_node_id"] = str(source.node_id)
            record["title"] = f"AI 自动拉片 · {Path(source_path).stem}"
            record["content"] = "双击节点开始分析切镜、镜长、节奏、运镜和主体运动轨迹。"
            record["status"] = "视频已连接 · 双击开始拉片"
            record["analysis_result"] = {}
            self._remember_workflow_edge(
                str(source.node_id), str(target.node_id), "breakdown_source")
            target.payload.update(record)
            target.title = record["title"]
            target.subtitle = record["content"]
            target.badge = record["status"]
            target.update()
            self._save_layout_now()
            return True
        if (target.payload.get("custom") and target.node_type in ("image_node", "video_node") and
                source_path and os.path.exists(source_path) and self._is_image_path(source_path)):
            record = self._custom_record(target.node_id)
            if record is None:
                return False
            source_role = str(source.payload.get("reference_role") or "reference")
            source_role = source_role if source_role in DIRECT_REFERENCE_ROLES else "reference"
            if target.node_type == "image_node":
                references = list(dict.fromkeys(
                    list(record.get("references") or []) + [source_path]))[:9]
                record["references"] = references
                typed = [dict(value) for value in record.get("reference_assets", [])
                         if isinstance(value, dict) and value.get("path") != source_path]
                if bool(record.get("multi_image_composer")) and source_role == "reference":
                    auto_roles = ("character", "scene", "composition", "element")
                    source_role = auto_roles[len(typed) % len(auto_roles)]
                typed.append({"path": source_path, "role": source_role,
                              "label": DIRECT_REFERENCE_ROLES.get(source_role, "普通参考")})
                record["reference_assets"] = typed
                record["editor_action"] = "AI 编辑"
                record["status"] = (
                    f"已连接 {len(references)} 张参考 · 请设置每张图用途"
                    if bool(record.get("multi_image_composer")) else
                    f"已连接 {len(references)} 张参考")
                relation = source_role
            else:
                if bool(record.get("multi_image_director")):
                    references = list(dict.fromkeys(
                        list(record.get("references") or []) + [source_path]))[:50]
                    record["references"] = references
                    typed = [dict(value) for value in record.get("reference_assets", [])
                             if isinstance(value, dict) and value.get("path") != source_path]
                    typed.append({"path":source_path, "role":source_role,
                                  "label":DIRECT_REFERENCE_ROLES.get(
                                      source_role, f"时间轴图片 {len(typed) + 1}")})
                    record["reference_assets"] = typed
                    timeline = [dict(value) for value in record.get("timeline_images", [])
                                if isinstance(value, dict) and value.get("path") != source_path]
                    index = len(timeline)
                    timeline.append({
                        "path":source_path, "start":float(index * 3),
                        "end":float(min(float(record.get("duration") or 10), index * 3 + 3)),
                        "role":source_role, "instruction":
                        "保持这张图的构图与主体，执行一个清晰动作并停在明确结束状态。",
                    })
                    record["timeline_images"] = timeline
                    record["status"] = f"已连接 {len(references)} 张时间轴图片"
                    record["editor_action"] = "图生视频"
                    self._remember_workflow_edge(
                        source.node_id, target.node_id, "timeline_reference")
                    self._save_layout_now()
                    # Never rebuild the scene from its active mouse-release
                    # handler. Even a zero-delay callback can run before Qt has
                    # released every native event reference. Persist and update
                    # the existing node; edges rebuild on the next normal refresh.
                    target.payload.update(record)
                    target.badge = f"{len(references)} 张参考"
                    target.subtitle = str(record.get("status") or "")
                    # 当前鼠标事件中只增加连线图元，不重建场景；
                    # 这样松手后立即看到连线，又不会删除正在处理事件的节点。
                    if not any(edge.source is source and edge.target is target
                               for edge in self.scene.edges):
                        self.scene.connect_nodes(source, target, "workflow")
                    target.update()
                    return True
                if source_role in ("character", "scene", "style", "element"):
                    references = list(dict.fromkeys(
                        list(record.get("references") or []) + [source_path]))[:9]
                    record["references"] = references
                    typed = [dict(value) for value in record.get("reference_assets", [])
                             if isinstance(value, dict) and value.get("path") != source_path]
                    typed.append({"path": source_path, "role": source_role,
                                  "label": DIRECT_REFERENCE_ROLES[source_role]})
                    record["reference_assets"] = typed
                    record["status"] = f"已连接{DIRECT_REFERENCE_ROLES[source_role]}参考"
                    relation = source_role
                else:
                    if not record.get("first_frame"):
                        field, relation = "first_frame", "first_frame"
                    elif record.get("first_frame") == source_path:
                        field, relation = "first_frame", "first_frame"
                    else:
                        field, relation = "last_frame", "last_frame"
                    record[field] = source_path
                    first = bool(record.get("first_frame")); last = bool(record.get("last_frame"))
                    record["status"] = "首尾帧已就绪" if first and last else "首帧已就绪"
                record["editor_action"] = "图生视频"
                # A frame slot has one authoritative source edge.
                if relation in ("first_frame", "last_frame"):
                    self._positions()["__workflow_edges__"] = [
                        value for value in self._positions().get("__workflow_edges__", [])
                        if not (isinstance(value, dict) and value.get("target") == target.node_id and
                                value.get("type") == relation)]
            self._remember_workflow_edge(source.node_id, target.node_id, relation)
            self._save_layout_now(); self.refresh(); self.focus_node(target.node_id)
            return True

        if target.node_type != "shot":
            QMessageBox.information(self, "无法连接", "当前输入端口只接受镜头生成输入。")
            return False

        if source.node_type in ("scene", "character", "element"):
            self.scene.clearSelection()
            source.setSelected(True)
            target.setSelected(True)
            self.bind_selected()
            return True

        if source.node_type in ("asset_view", "asset_take", "shot_take"):
            path = str(source.payload.get("path") or "")
            kind = str(source.payload.get("kind") or "image")
            if kind != "image" or not path or not os.path.exists(path):
                QMessageBox.information(
                    self, "无法连接", "镜头参考输入目前只接受存在的图片结果。")
                return False
            shot = self._find_shot(target.payload.get("shot_id"))
            if not shot:
                return False
            if source.node_type == "shot_take":
                blocked_reason = self._shot_take_block_reason(source)
                if blocked_reason:
                    QMessageBox.information(self, "不能作为镜头定稿", blocked_reason)
                    return False
            assets = [item for item in shot.get("assets", []) if isinstance(item, dict)]
            if not any(str(item.get("path") or "") == path for item in assets):
                assets.append({"path": path, "kind": "image",
                               "source": "workflow_reference"})
            shot["assets"] = assets
            shot["selected_image_asset"] = path
            shot["anchor_frame_id"] = path
            shot["selected_asset"] = path
            shot["preview_asset"] = path
            self._remember_workflow_edge(source.node_id, target.node_id, "image")
            self.storyboardMutated.emit()
            self.refresh()
            self.focus_node(target.node_id)
            return True

        QMessageBox.information(
            self, "无法连接",
            "支持的连接：资产 → 镜头，或图片候选/图片结果 → 镜头。")
        return False

    @staticmethod
    def _shot_asset_ids(shot: dict, kind: str):
        sync_legacy_bindings(shot)
        if kind == "scene":
            value = shot.get("scene_asset_id") or shot.get("scene_id")
            return [value] if value else []
        key = "character_bindings" if kind == "character" else "element_bindings"
        return list(dict.fromkeys(
            value.get("asset_id") for value in shot.get(key, [])
            if isinstance(value, dict) and value.get("asset_id")))

    def linked_shots_for_asset(self, kind: str, asset_id: str):
        return [shot for shot in self.current_storyboard().get("shots", [])
                if asset_id in self._shot_asset_ids(shot, kind)]

    def linked_assets_for_shot(self, shot: dict):
        result = []
        labels = {"scene": "场景", "character": "主体", "element": "元素"}
        for kind in ("scene", "character", "element"):
            for asset_id in self._shot_asset_ids(shot, kind):
                item = getattr(self.db, f"get_{kind}")(asset_id)
                name = getattr(item, "name", "") if item else asset_id
                result.append((kind, asset_id, f"{labels[kind]}：{name or asset_id}"))
        return result

    def _editor_payload_for_node(self, node: CanvasNodeItem, audio_policy="replace"):
        """Build a detached editor handoff without changing canvas approvals."""
        board = self.current_storyboard()
        all_shots = list(board.get("shots", [])) if isinstance(board, dict) else []
        node_type = str(node.node_type or "")
        source_id = str(node.payload.get("source_node_id") or "")
        is_project = node_type == "storyboard_node" or (
            node_type == "workflow_group" and bool(source_id))
        if is_project:
            return {
                "mode":"storyboard", "board":json.loads(json.dumps(board, ensure_ascii=False)),
                "audio_policy":audio_policy, "title":str(node.title or "AI 制片项目"),
            } if any(str(shot.get("selected_video_asset") or
                         shot.get("selected_image_asset") or "") for shot in all_shots) else None

        shot_ids = [str(value) for value in
                    (node.payload.get("shot_ids") or [node.payload.get("shot_id")]) if value]
        if node_type == "shot":
            shot_ids = [str(node.payload.get("shot_id") or "")]
        selected_shots = [json.loads(json.dumps(shot, ensure_ascii=False))
                          for shot in all_shots
                          if str(shot.get("id") or "") in set(shot_ids)]
        path = str(node.payload.get("path") or node.thumbnail or "")
        media_kind = str(node.payload.get("kind") or
                         node.payload.get("generator_kind") or "")
        if not media_kind:
            media_kind = {"image_node":"image", "video_node":"video",
                          "audio_node":"audio", "shot_take":"image"}.get(node_type, "")
        if selected_shots:
            for shot in selected_shots:
                if path and os.path.exists(path):
                    if media_kind == "video":
                        shot["selected_video_asset"] = path
                        shot["selected_asset"] = path
                        shot["asset_type"] = "video"
                    elif media_kind == "image":
                        shot["selected_image_asset"] = path
                        shot["selected_asset"] = path
                        shot["asset_type"] = "image"
                    elif media_kind == "audio":
                        shot["dialogue_audio"] = path
                if not str(shot.get("selected_video_asset") or
                           shot.get("selected_image_asset") or ""):
                    continue
            ready = [shot for shot in selected_shots
                     if str(shot.get("selected_video_asset") or
                            shot.get("selected_image_asset") or "")]
            if ready:
                mini_board = {key:json.loads(json.dumps(value, ensure_ascii=False))
                              for key, value in board.items() if key != "shots"}
                mini_board["shots"] = ready
                return {"mode":"storyboard", "board":mini_board,
                        "audio_policy":audio_policy, "title":str(node.title or "镜头")}
        if path and os.path.exists(path) and media_kind in ("image", "video", "audio"):
            return {"mode":"media", "path":path, "media_type":media_kind,
                    "title":str(node.title or Path(path).stem)}
        return None

    def send_node_to_editor(self, node: CanvasNodeItem, audio_policy="replace"):
        payload = self._editor_payload_for_node(node, audio_policy)
        if payload is None:
            QMessageBox.information(
                self, "送到剪辑台", "这个节点还没有可送入剪辑台的定稿图片、视频或音频。")
            return
        self.sendToEditorRequested.emit(payload)

    def _local_media_path_for_node(self, node: CanvasNodeItem) -> str | None:
        """Return a revealable image/video path, or None for non-media nodes."""
        if node.node_type == "shot":
            shot = self._find_shot(node.payload.get("shot_id")) or {}
            return str(
                shot.get("selected_video_asset") or
                shot.get("selected_image_asset") or
                shot.get("selected_asset") or shot.get("preview_asset") or "")
        if node.node_type in ("image_node", "video_node"):
            return str(node.payload.get("path") or "")
        if node.node_type in ("asset_view", "asset_take"):
            return str(node.payload.get("path") or "")
        if node.node_type == "shot_take" and str(
                node.payload.get("kind") or "image") in ("image", "video"):
            return str(node.payload.get("path") or "")
        return None

    def reveal_local_media_file(self, path: str):
        """Open Explorer and select the generated file without executing it."""
        path = os.path.abspath(os.path.expanduser(str(path or "")))
        if not path or not os.path.isfile(path):
            QMessageBox.information(
                self, "找不到本地文件",
                "这个图片或视频文件已被移动、删除，或者还没有生成完成。")
            return False
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path])
            else:
                subprocess.Popen(["xdg-open", str(Path(path).parent)])
            return True
        except (OSError, subprocess.SubprocessError) as error:
            QMessageBox.warning(
                self, "无法打开本地文件",
                f"文件存在，但无法打开所在文件夹：\n{error}")
            return False

    def toggle_shot_production_selection(self, node_or_shot_id):
        """Toggle by stable shot id so a menu never retains a deleted Qt item."""
        if isinstance(node_or_shot_id, str):
            shot_id = node_or_shot_id
        else:
            try:
                shot_id = str(node_or_shot_id.payload.get("shot_id") or "")
            except RuntimeError:
                # The canvas may have refreshed between opening the context
                # menu and triggering its action. Resolve through the model.
                return False
        shot = self._find_shot(shot_id)
        if shot is None:
            return False
        shot["production_selected"] = not bool(shot.get("production_selected"))
        self._save_layout_now()
        self.storyboardMutated.emit()
        self.refresh()
        return True

    def show_node_context_menu(self, node: CanvasNodeItem, screen_pos):
        if not node.isSelected():
            self.scene.clearSelection()
            node.setSelected(True)
        menu = QMenu(self)
        if node.node_type in ("scene", "character", "element"):
            kind = node.payload.get("kind", "")
            asset_id = node.payload.get("asset_id", "")
            edit = menu.addAction("编辑资产")
            edit.triggered.connect(lambda: self.open_library_asset(kind, asset_id))
            linked = self.linked_shots_for_asset(kind, asset_id)
            unlink_menu = menu.addMenu("解除镜头连接")
            if not linked:
                empty = unlink_menu.addAction("当前没有连接")
                empty.setEnabled(False)
            for shot in linked:
                number = int(shot.get("number", 0) or 0)
                action = unlink_menu.addAction(f"解除与镜头 {number:02d} 的连接")
                action.triggered.connect(
                    lambda _=False, k=kind, aid=asset_id, sid=shot.get("id", ""):
                    self.unlink_asset_from_shot(k, aid, sid))
            menu.addSeparator()
            remove = menu.addAction("移出当前画布")
            remove.triggered.connect(self.remove_selected_from_canvas)
            delete = menu.addAction("从资产库永久删除…")
            delete.triggered.connect(self.delete_selected)
        elif node.node_type == "shot":
            shot = self._find_shot(node.payload.get("shot_id"))
            stable_shot_id = str((shot or {}).get("id") or
                                 node.payload.get("shot_id") or "")
            production_selected = bool((shot or {}).get("production_selected"))
            stage_action = menu.addAction("3D 导演台 · 安排人物与摄影机")
            stage_action.triggered.connect(
                lambda _=False, sid=str(node.payload.get("shot_id") or ""):
                self.open_scene_stage(sid))
            menu.addSeparator()
            production = menu.addAction(
                "移出本次批量生产" if production_selected else "加入本次批量生产")
            production.triggered.connect(
                lambda _=False, sid=stable_shot_id:
                self.toggle_shot_production_selection(sid))
            menu.addSeparator()
            versions = [asset for asset in (shot or {}).get("assets", [])
                        if isinstance(asset, dict) and asset.get("path")]
            reroll = menu.addAction("重新生成本镜分镜稿（不沿用错误稿）")
            reroll.triggered.connect(lambda: self.reroll_canvas_storyboard_shot(node))
            frames = [value for value in ((shot or {}).get("motion_keyframes") or [])
                      if isinstance(value, dict)]
            if frames:
                panel_menu = menu.addMenu("只重新生成错误画格")
                for frame_index, frame in enumerate(frames):
                    label = str(frame.get("label") or frame.get("action") or "").strip()
                    action = panel_menu.addAction(
                        f"K{frame_index + 1}" + (f" · {label[:18]}" if label else ""))
                    action.triggered.connect(
                        lambda _=False, n=node, value=frame_index:
                        self.reroll_canvas_storyboard_panel(n, value))
            history = menu.addAction(f"画布展开全部版本（{len(versions)}）")
            history.setEnabled(bool(versions))
            history.triggered.connect(lambda: self.expand_shot_history(node))
            menu.addSeparator()
            prepare = menu.addAction("识别并准备缺失素材…")
            prepare.triggered.connect(
                lambda: self.prepareShotAssetsRequested.emit(data_id(node)))
            open_shot = menu.addAction("打开详细分镜")
            open_shot.triggered.connect(lambda: self.shotRequested.emit(data_id(node)))
            unlink_menu = menu.addMenu("解除资产连接")
            linked = self.linked_assets_for_shot(shot or {})
            if not linked:
                empty = unlink_menu.addAction("当前没有连接")
                empty.setEnabled(False)
            for kind, asset_id, label in linked:
                action = unlink_menu.addAction(f"解除 {label}")
                action.triggered.connect(
                    lambda _=False, k=kind, aid=asset_id,
                    sid=(shot or {}).get("id", ""):
                    self.unlink_asset_from_shot(k, aid, sid))
            menu.addSeparator()
            menu.addAction("删除镜头节点", self.delete_canvas_selection)
        elif node.node_type in ("asset_view", "asset_take"):
            is_fixed_view = node.payload.get("reference_type") == "fixed_view"
            menu.addAction("设为主参考", lambda: self.approve_take(node))
            if not node.payload.get("approved"):
                menu.addAction(
                    "移除这个固定视角…" if is_fixed_view else "移除这个候选…",
                    lambda: self.remove_asset_take(node))
            menu.addAction("只保留主参考和固定视角…",
                           lambda: self.keep_only_asset_references(node))
        elif node.node_type == "shot_take":
            asset = (node.payload.get("asset")
                     if isinstance(node.payload.get("asset"), dict) else {})
            is_motion_board = str(asset.get("subtype") or "") == "motion_storyboard"
            if is_motion_board:
                shot = self._find_shot(node.payload.get("shot_id")) or {}
                is_current = str(shot.get("motion_board_path") or "") == str(
                    node.payload.get("path") or "")
                adopt_action = menu.addAction(
                    "当前采用的运动分镜" if is_current else "采用为当前运动分镜",
                    lambda: self.adopt_motion_storyboard_take(node))
                adopt_action.setEnabled(not is_current)
                menu.addSeparator()
                menu.addAction("移除这个候选…", lambda: self.remove_shot_result(node))
                menu.addAction(
                    "只保留这个候选…",
                    lambda: self.keep_only_motion_storyboard_take(node))
                menu.exec(screen_pos)
                return
            blocked_reason = self._shot_take_block_reason(node)
            adopt_action = menu.addAction(
                "设为定稿图片" if node.payload.get("kind") == "image" else "设为定稿视频",
                lambda: self.adopt_shot_take(node))
            adopt_action.setEnabled(not bool(blocked_reason))
            if blocked_reason:
                reason_action = menu.addAction("⚠ " + blocked_reason)
                reason_action.setEnabled(False)
            if node.payload.get("kind") == "image" and not blocked_reason:
                menu.addAction("保存到资产库…", lambda: self.save_result_to_library(node))
                menu.addAction(
                    "只重新生成这个图片节点…",
                    lambda _=False, n=node: self.regenerate_image_result(n))
                menu.addAction(
                    "删除这个图片节点的生成器分支…",
                    lambda _=False, n=node: self.delete_image_result_branch(n))
            if node.payload.get("kind") == "video":
                menu.addAction(
                    "只重新生成这个视频段…",
                    lambda _=False, n=node: self.regenerate_video_result(n))
                menu.addAction(
                    "删除这个视频段的生成器分支…",
                    lambda _=False, n=node: self.delete_video_result_branch(n))
            menu.addSeparator()
            menu.addAction("移除这个结果…", lambda: self.remove_shot_result(node))
            menu.addAction("只保留这个结果…", lambda: self.keep_only_shot_result(node))
        elif node.payload.get("custom"):
            menu.addAction("打开", lambda: self.activate_node(node))
            if (node.node_type == "image_node" and
                    not bool(node.payload.get("multi_image_composer")) and
                    not bool(node.payload.get("image_workbench")) and
                    str(node.payload.get("generator_kind") or "") != "image"):
                menu.addSeparator()
                menu.addAction("发送到多图生成图片",
                               lambda _=False, n=node: self.send_image_to_composer(n))
                menu.addAction("发送到图片工作台",
                               lambda _=False, n=node: self.send_image_to_workbench(n))
                menu.addAction("取消标记" if node.payload.get("marked") else "标记图片",
                               lambda _=False, n=node:
                               self.toggle_node_mark(
                                   n, not bool(n.payload.get("marked")), None))
                menu.addAction("让图片动起来（作为首帧）",
                               lambda _=False, n=node:
                               self.run_image_quick_action(n, "作为视频首帧"))
                menu.addAction("AI 看图写描述",
                               lambda _=False, n=node:
                               self.run_image_quick_action(n, "AI 看图写描述"))
                menu.addAction("保存到资产库…",
                               lambda _=False, n=node:
                               self.run_image_quick_action(n, "保存到资产库"))
            elif node.node_type == "image_node":
                menu.addSeparator()
                if str(node.payload.get("generator_kind") or "") == "image":
                    menu.addAction(
                        "重新生成当前图片节点（保留旧版本）",
                        lambda _=False, n=node:
                        self.regenerate_image_generator(n, discard_current=False))
                    menu.addAction(
                        "清理当前镜头图片候选并重生…",
                        lambda _=False, n=node:
                        self.regenerate_image_generator(n, discard_current=True))
                    menu.addSeparator()
                if bool(node.payload.get("multi_image_composer")):
                    menu.addAction("设置每张参考图的用途…",
                                   lambda _=False, n=node:
                                   self.edit_multi_image_composer(n))
                edit_labels = (("AI 编辑", "图片高清", "智能扩图", "移除背景",
                                "替换背景", "AI 看图写描述")
                               if bool(node.payload.get("image_workbench")) else
                               ("AI 看图写描述",))
                for label in edit_labels:
                    menu.addAction(label, lambda _=False, n=node, value=label:
                                   self.run_image_quick_action(n, value))
                menu.addAction("让图片动起来（作为首帧）",
                               lambda _=False, n=node:
                               self.run_image_quick_action(n, "作为视频首帧"))
                frame_menu = menu.addMenu("用作视频帧")
                frame_menu.addAction("作为视频首帧",
                                     lambda _=False, n=node:
                                     self.run_image_quick_action(n, "作为视频首帧"))
                frame_menu.addAction("作为视频尾帧",
                                     lambda _=False, n=node:
                                     self.run_image_quick_action(n, "作为视频尾帧"))
                reference_menu = menu.addMenu("参考职责")
                for label in ("设为角色参考", "设为场景参考", "设为风格参考", "设为元素参考"):
                    reference_menu.addAction(
                        label, lambda _=False, n=node, value=label:
                        self.run_image_quick_action(n, value))
                reference_menu.menuAction().setVisible(False)
                menu.addAction("保存到资产库…",
                               lambda _=False, n=node:
                               self.run_image_quick_action(n, "保存到资产库"))
            elif node.node_type == "video_node":
                menu.addSeparator()
                if str(node.payload.get("generator_kind") or "") == "video":
                    menu.addAction(
                        "重新生成当前视频段（保留旧版本）",
                        lambda _=False, n=node:
                        self.regenerate_video_generator(n, discard_current=False))
                    menu.addAction(
                        "清理当前视频段候选并重生…",
                        lambda _=False, n=node:
                        self.regenerate_video_generator(n, discard_current=True))
                    menu.addSeparator()
                menu.addAction("选择首帧…",
                               lambda _=False, n=node: self.choose_video_frame(n, "first_frame"))
                menu.addAction("选择尾帧…",
                               lambda _=False, n=node: self.choose_video_frame(n, "last_frame"))
                clear_frames = menu.addAction("清空首尾帧")
                clear_frames.setEnabled(bool(
                    node.payload.get("first_frame") or node.payload.get("last_frame")))
                clear_frames.triggered.connect(
                    lambda _=False, n=node: (
                        self.set_video_frame(n, "first_frame", ""),
                        self.set_video_frame(n, "last_frame", "")))
            if node.node_type == "skill_node":
                menu.addSeparator()
                menu.addAction("执行专业 Skill", lambda _=False, n=node: self.execute_canvas_skill(n, True))
                menu.addAction("只展开工作流", lambda _=False, n=node: self.execute_canvas_skill(n, False))
            if node.node_type == "storyboard_node":
                menu.addSeparator()
                menu.addAction(
                    "逐阶段制片 · 每步人工确认",
                    lambda _=False, n=node: self.activate_node(n))
                rewind_menu = menu.addMenu("↶ 从指定阶段重新开始")
                for rewind_step, (rewind_label, rewind_tip) in PRODUCTION_REWIND_STEPS.items():
                    rewind_action = rewind_menu.addAction(
                        f"第 {rewind_step} 步 · {rewind_label}")
                    rewind_action.setToolTip(rewind_tip)
                    rewind_action.triggered.connect(
                        lambda _=False, value=rewind_step, sid=str(node.node_id):
                        self.rewind_production_to_step(value, sid))
                undo_rewind = rewind_menu.addAction("撤销上一次阶段回退")
                undo_rewind.setEnabled(isinstance(
                    self._positions().get("__stage_rewind_backup__"), dict))
                undo_rewind.triggered.connect(self.undo_last_production_rewind)
                menu.addAction(
                    "用定稿图片生成视频",
                    lambda _=False, n=node: self.create_and_execute_video_group(n))
                has_video = any(os.path.exists(str(shot.get("selected_video_asset") or ""))
                                for shot in self.current_storyboard().get("shots", []))
                preview_action = menu.addAction(
                    "▶ 联合预览 · 视频 + TTS",
                    lambda _=False, sid=str(node.node_id):
                    self.preview_current_production(sid))
                preview_action.setEnabled(has_video)
                if str(node.payload.get("pipeline_stage") or "") == "video_qc_review":
                    menu.addAction(
                        "⚠ 接受审片风险并继续…",
                        lambda _=False, sid=str(node.node_id):
                        self.accept_video_qc_risk(sid))
                menu.addAction("创建对白音频组",
                               lambda _=False, n=node: self.create_dialogue_audio_group(n))
                menu.addAction("检查 3 分钟短剧完整性",
                               lambda _=False: self.production_readiness_report())
            if node.payload.get("asset_role"):
                menu.addSeparator()
                locked = bool(node.payload.get("locked"))
                lock_action = menu.addAction("解锁资产" if locked else "采用并锁定当前版本")
                lock_action.setEnabled(bool(node.payload.get("path")))
                lock_action.triggered.connect(
                    lambda _=False, n=node, value=not locked:
                    self.set_production_asset_lock(n, value))
                reference_set = dict(node.payload.get("character_reference_set") or {})
                completed_panels = sum(
                    os.path.exists(str(reference_set.get(role) or ""))
                    for role, _label, _prompt in CHARACTER_REFERENCE_SPECS)
                regenerate_label = (
                    "补齐完整角色四件套" if node.payload.get("asset_kind") == "character" and
                    completed_panels < 4 else
                    "重新生成完整角色四件套" if node.payload.get("asset_kind") == "character" else
                    "生成新候选版本")
                menu.addAction(regenerate_label,
                               lambda _=False, n=node: self.regenerate_production_asset(n))
                menu.addAction(
                    f"版本历史 · {len(node.payload.get('candidates') or [])} 个",
                    lambda _=False, n=node: self.show_production_asset_versions(n))
            menu.addSeparator()
            menu.addAction("复制", self.copy_selected_nodes)
            menu.addAction("创建副本（保留连线）",
                           lambda: self.duplicate_custom_nodes([node], keep_edges=True))
            menu.addSeparator()
            menu.addAction("仅删除这个节点", self.delete_canvas_selection)
            menu.addAction(
                "删除这个节点及下游分支…",
                lambda _=False, n=node: self.delete_canvas_branch(n))
        else:
            menu.addAction("打开", lambda: self.activate_node(node))
        selected_nodes = [item for item in self.scene.selectedItems()
                          if isinstance(item, CanvasNodeItem)]
        if len(selected_nodes) >= 2:
            menu.addSeparator()
            ordered_nodes = [node] + [value for value in selected_nodes if value is not node]
            grouped_paths = self._selected_asset_image_paths(ordered_nodes)
            if len(grouped_paths) >= 2:
                menu.addAction(
                    f"合并保存为一个资产…（{len(grouped_paths)} 张）",
                    lambda _=False, values=ordered_nodes:
                    self.save_selected_images_as_asset(values))
            menu.addAction("将选中节点建立工作流组", self.create_workflow_group)
        if node.node_type == "workflow_group":
            menu.addSeparator()
            menu.addAction("整组执行", lambda: self.execute_workflow_group(node))
            resume = menu.addAction("继续未完成项")
            resume.setEnabled(any(
                isinstance(value, dict) and value.get("group_id") == node.node_id and
                value.get("status") in ("interrupted", "ready", "failed")
                for value in self._positions().get("__production_batches__", [])))
            resume.triggered.connect(lambda: self.resume_production_batch(node))
            if node.payload.get("generator_kind") == "image":
                source_id = str(node.payload.get("source_node_id") or "")
                source_node = self._nodes.get(source_id)
                video_action = menu.addAction("用定稿图片继续生成视频")
                video_action.setEnabled(source_node is not None)
                video_action.triggered.connect(
                    lambda _=False, n=source_node:
                    self.create_and_execute_video_group(n) if n is not None else None)
            menu.addAction("暂停整组任务", lambda: self.pause_workflow_group(node))
            menu.addAction("保存为可复用工作流…", lambda: self.save_workflow_template(node))
            retry = menu.addAction("只重试失败节点")
            retry.setEnabled(bool(self._workflow_failed_nodes))
            retry.triggered.connect(lambda: self.execute_workflow_group(node, True))
        local_media_path = self._local_media_path_for_node(node)
        if local_media_path is not None:
            menu.addSeparator()
            reveal = menu.addAction(
                "查看本地文件",
                lambda _=False, path=local_media_path:
                self.reveal_local_media_file(path))
            reveal.setEnabled(bool(local_media_path))
            reveal.setToolTip(local_media_path or "当前节点还没有生成本地文件")
        editor_payload = self._editor_payload_for_node(node, "replace")
        if editor_payload is not None:
            menu.addSeparator()
            default_label = ("送到剪辑台 · TTS 替换 VEO 原声"
                             if editor_payload.get("mode") == "storyboard" else
                             "送到剪辑台")
            menu.addAction(
                default_label,
                lambda _=False, n=node: self.send_node_to_editor(n, "replace"))
            if editor_payload.get("mode") == "storyboard":
                menu.addAction(
                    "送到剪辑台 · VEO 原声压到 12%",
                    lambda _=False, n=node: self.send_node_to_editor(n, "duck"))
        menu.exec(screen_pos)

    def copy_selected_nodes(self):
        selected = [item for item in self.scene.selectedItems()
                    if isinstance(item, CanvasNodeItem) and item.payload.get("custom")]
        copied = []
        for node in selected:
            record = self._custom_record(node.node_id)
            if record:
                copied.append({"record": json.loads(json.dumps(record, ensure_ascii=False)),
                               "position": [node.pos().x(), node.pos().y()]})
        self._canvas_clipboard = copied

    def paste_copied_nodes(self):
        if not self._canvas_clipboard:
            return
        self.scene.clearSelection()
        created = []
        for value in self._canvas_clipboard:
            record = value["record"]
            position = value["position"]
            node_id = self.create_custom_node(
                str(record.get("type") or "text_node"),
                QPointF(float(position[0]) + 42.0, float(position[1]) + 42.0), record)
            created.append(node_id)
        self._canvas_clipboard = [
            {"record": json.loads(json.dumps(self._custom_record(node_id), ensure_ascii=False)),
             "position": list(self._positions().get(node_id, [0, 0]))}
            for node_id in created]
        self.refresh()
        for node_id in created:
            if node_id in self._nodes:
                self._nodes[node_id].setSelected(True)

    def duplicate_custom_nodes(self, nodes, keep_edges=False):
        import uuid
        originals = [node for node in nodes if node.payload.get("custom")]
        if not originals:
            return
        mapping = {}
        values = self._positions().setdefault("__custom_nodes__", [])
        for node in originals:
            source = self._custom_record(node.node_id)
            if source is None:
                continue
            duplicate = json.loads(json.dumps(source, ensure_ascii=False))
            new_id = f"custom:{uuid.uuid4().hex[:12]}"
            duplicate["id"] = new_id
            duplicate["title"] = f"{source.get('title') or '节点'} 副本"
            values.append(duplicate)
            self._positions()[new_id] = [round(node.pos().x() + 42.0, 2),
                                         round(node.pos().y() + 42.0, 2)]
            mapping[node.node_id] = new_id
        if keep_edges and mapping:
            edges = list(self._positions().get("__workflow_edges__", []))
            for edge in edges:
                if not isinstance(edge, dict):
                    continue
                source = mapping.get(edge.get("source"), edge.get("source"))
                target = mapping.get(edge.get("target"), edge.get("target"))
                if edge.get("source") in mapping or edge.get("target") in mapping:
                    cloned = {**edge, "source": source, "target": target}
                    if cloned not in self._positions().setdefault("__workflow_edges__", []):
                        self._positions()["__workflow_edges__"].append(cloned)
        self._save_layout_now()
        self.refresh()
        for node_id in mapping.values():
            if node_id in self._nodes:
                self._nodes[node_id].setSelected(True)

    def save_workflow_template(self, group_node):
        record = self._custom_record(group_node.node_id)
        if record is None:
            return
        name, accepted = QInputDialog.getText(
            self, "保存工作流", "模板名称：", text=str(record.get("title") or "工作流模板"))
        if not accepted or not name.strip():
            return
        member_ids = list(record.get("group_nodes") or [])
        members = [self._custom_record(node_id) for node_id in member_ids]
        members = [json.loads(json.dumps(value, ensure_ascii=False))
                   for value in members if value]
        for value in members:
            value.pop("id", None); value["path"] = ""; value["status"] = "待执行"
        self._positions().setdefault("__workflow_templates__", []).append({
            "name":name.strip(), "group_title":record.get("title") or name.strip(),
            "members":members, "generator_kind":record.get("generator_kind") or "image",
        })
        self._save_layout_now()

    def instantiate_workflow_template(self, template: dict, scene_pos: QPointF):
        import uuid
        values = self._positions().setdefault("__custom_nodes__", [])
        edges = self._positions().setdefault("__workflow_edges__", [])
        member_ids = []
        for index, source in enumerate(template.get("members") or []):
            node_id = f"custom:{uuid.uuid4().hex[:12]}"
            record = json.loads(json.dumps(source, ensure_ascii=False))
            record["id"] = node_id; member_ids.append(node_id); values.append(record)
            self._positions()[node_id] = [round(scene_pos.x() + 420 + index * 330, 2),
                                         round(scene_pos.y(), 2)]
        group_id = f"custom:{uuid.uuid4().hex[:12]}"
        values.append({"id":group_id, "type":"workflow_group",
                       "title":str(template.get("group_title") or template.get("name") or "工作流"),
                       "content":"从可复用模板创建", "group_nodes":member_ids,
                       "generator_kind":template.get("generator_kind") or "image",
                       "status":"待检查并执行"})
        self._positions()[group_id] = [round(scene_pos.x(), 2), round(scene_pos.y(), 2)]
        for node_id in member_ids:
            edges.append({"source":group_id, "target":node_id, "type":"group"})
        self._save_layout_now(); self.refresh(); self.focus_node(group_id)

    def undo_canvas_delete(self):
        if not self._delete_undo:
            return False
        entry = self._delete_undo.pop()
        restored = (entry.get("positions") if isinstance(entry, dict) and
                    isinstance(entry.get("positions"), dict) else entry)
        if not isinstance(restored, dict):
            return False
        project_key = self._project_key()
        self._layout_store[project_key] = restored
        self._save_layout_now()
        self.refresh()
        return True

    def bind_selected(self):
        asset_node, shot_node = self._selected_asset_and_shot()
        if not asset_node or not shot_node:
            QMessageBox.information(
                self, "连接节点", "请按住 Ctrl，同时选择一个资产节点和一个镜头节点。")
            return
        shot = self._find_shot(shot_node.payload.get("shot_id"))
        kind = asset_node.payload.get("kind")
        asset_id = asset_node.payload.get("asset_id")
        item = getattr(self.db, f"get_{kind}")(asset_id) if kind and asset_id else None
        if not shot or not item:
            return
        if kind == "scene":
            shot["scene_asset_id"] = asset_id
            shot["scene_id"] = asset_id
            shot["scene_version"] = int(getattr(item, "version", 0) or 0)
        elif kind == "character":
            bindings = [value for value in shot.get("character_bindings", [])
                        if (isinstance(value, dict) and value.get("asset_id") and
                            self.db.get_character(value.get("asset_id")) is not None)]
            if not any(value.get("asset_id") == asset_id for value in bindings):
                bindings.append({
                    "asset_id": asset_id,
                    "version": int(getattr(item, "version", 0) or 0),
                    "role": "subject", "outfit_state": "",
                    "appearance_state": "", "required": True,
                })
            shot["character_bindings"] = bindings
            # V2 列表是权威数据；同步前覆盖旧字段，避免失效的 AI 占位 ID 被重新加入。
            ids = [value["asset_id"] for value in bindings]
            shot["character_id"] = ids[0] if ids else ""
            shot["character_ids"] = ids[1:]
        elif kind == "element":
            bindings = [value for value in shot.get("element_bindings", [])
                        if (isinstance(value, dict) and value.get("asset_id") and
                            self.db.get_element(value.get("asset_id")) is not None)]
            if not any(value.get("asset_id") == asset_id for value in bindings):
                bindings.append({
                    "asset_id": asset_id,
                    "version": int(getattr(item, "version", 0) or 0),
                    "mode": getattr(item, "default_mode", "exact") or "exact",
                    "placement": getattr(item, "placement_hint", "") or "",
                    "required": True,
                })
            shot["element_bindings"] = bindings
            ids = [value["asset_id"] for value in bindings]
            shot["element_id"] = ids[0] if ids else ""
            shot["element_ids"] = ids[1:]
        sync_legacy_bindings(shot)
        rebuild_continuity(self.current_storyboard())
        self.storyboardMutated.emit()
        self.refresh()
        self.focus_node(f"shot:{shot.get('id')}")

    def unbind_selected(self):
        asset_node, shot_node = self._selected_asset_and_shot()
        if not asset_node or not shot_node:
            QMessageBox.information(
                self, "解除连接", "请按住 Ctrl，同时选择一个资产节点和一个镜头节点。")
            return
        kind = asset_node.payload.get("kind")
        asset_id = asset_node.payload.get("asset_id")
        self.unlink_asset_from_shot(
            kind, asset_id, shot_node.payload.get("shot_id"))

    def unlink_asset_from_shot(self, kind: str, asset_id: str, shot_id: str):
        shot = self._find_shot(shot_id)
        if not shot:
            return False
        sync_legacy_bindings(shot)
        changed = False
        if kind == "scene" and (shot.get("scene_asset_id") or shot.get("scene_id")) == asset_id:
            shot["scene_asset_id"] = ""
            shot["scene_id"] = ""
            shot["scene_version"] = 0
            changed = True
        elif kind == "character":
            before = len(shot.get("character_bindings", []))
            shot["character_bindings"] = [
                value for value in shot.get("character_bindings", [])
                if not isinstance(value, dict) or value.get("asset_id") != asset_id]
            ids = [value.get("asset_id") for value in shot["character_bindings"]
                   if isinstance(value, dict) and value.get("asset_id")]
            shot["character_id"] = ids[0] if ids else ""
            shot["character_ids"] = ids[1:]
            changed = len(shot["character_bindings"]) != before
        elif kind == "element":
            before = len(shot.get("element_bindings", []))
            shot["element_bindings"] = [
                value for value in shot.get("element_bindings", [])
                if not isinstance(value, dict) or value.get("asset_id") != asset_id]
            ids = [value.get("asset_id") for value in shot["element_bindings"]
                   if isinstance(value, dict) and value.get("asset_id")]
            shot["element_id"] = ids[0] if ids else ""
            shot["element_ids"] = ids[1:]
            changed = len(shot["element_bindings"]) != before
        if not changed:
            return False
        sync_legacy_bindings(shot)
        rebuild_continuity(self.current_storyboard())
        self.storyboardMutated.emit()
        self.refresh()
        self.focus_node(f"shot:{shot.get('id')}")
        return True

    def _asset_saved(self, item):
        kind = "character" if isinstance(item, Character) else (
            "scene" if isinstance(item, Scene) else "element")
        self._remember_canvas_asset(
            f"asset:{kind}:{item.id}",
            getattr(self, "_pending_new_asset_pos", None))
        self._pending_new_asset_pos = None
        self.canvas_drawer.hide()
        self.assetChanged.emit(kind)
        self.refresh()
        self.focus_node(f"asset:{kind}:{item.id}")

    def _open_asset_studio(self, item, kind):
        # 旧入口会打开庞大的资产制作台。画布内统一使用轻量检查器。
        self.asset_inspector.load(item, kind, KIND_META[kind]["accent"])
        self.asset_inspector.set_visible(True)
        if not self.asset_inspector._edit:
            self.asset_inspector._toggle_edit()
        self.show_asset_drawer(f"编辑{KIND_META[kind]['label']}")

    def approve_take(self, node):
        data = node.payload
        item = getattr(self.db, f"get_{data['kind']}")(data["asset_id"])
        path = data.get("path", "")
        if not item or not path or not os.path.exists(path):
            return
        changed = approve_asset_version(item, path, source="production_canvas")
        getattr(self.db, f"save_{data['kind']}")(item)
        self.assetChanged.emit(data["kind"])
        self.refresh()
        self.focus_node(f"asset:{data['kind']}:{data['asset_id']}")
        QMessageBox.information(
            self, "已选定主参考",
            f"已使用这张作为主参考 v{max(1, int(getattr(item, 'version', 0) or 0))}。"
            if changed else "这张图片已经是当前主参考。")

    def remove_asset_take(self, node):
        data = node.payload
        kind = str(data.get("kind") or "")
        asset_id = str(data.get("asset_id") or "")
        path = str(data.get("path") or "")
        item = getattr(self.db, f"get_{kind}")(asset_id) if kind and asset_id else None
        if not item or not path:
            return
        if path == approved_asset_path(item):
            QMessageBox.information(
                self, "不能移除主参考",
                "这张图正在作为主参考。请先对另一张点击“使用这张”，再移除它。")
            return
        views = dict(getattr(item, "reference_views", {}) or {})
        roles = [role for role, value in views.items() if value == path]
        is_fixed_view = bool(roles) or data.get("reference_type") == "fixed_view"
        role_labels = [VIEW_ROLE_LABELS.get(role, role) for role in roles]
        role_note = (
            f"\n固定视角：{'、'.join(role_labels)}，这些视角会一起取消。"
            if roles else "")
        answer = QMessageBox.question(
            self, "移除固定视角" if is_fixed_view else "移除候选",
            ("要从这个资产中移除当前固定视角吗？" if is_fixed_view
             else "要从这个资产中移除当前候选吗？") + f"{role_note}\n\n"
            "本地图片文件会保留。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        item.reference_images = [
            value for value in (getattr(item, "reference_images", []) or [])
            if value != path]
        item.reference_views = {
            role: value for role, value in views.items() if value != path}
        if isinstance(item, Element) and getattr(item, "master_image", "") == path:
            item.master_image = ""
        getattr(self.db, f"save_{kind}")(item)
        self.assetChanged.emit(kind)
        self.refresh()
        self.focus_node(f"asset:{kind}:{asset_id}")

    def keep_only_asset_references(self, node):
        data = node.payload
        kind = str(data.get("kind") or "")
        asset_id = str(data.get("asset_id") or "")
        item = getattr(self.db, f"get_{kind}")(asset_id) if kind and asset_id else None
        if not item:
            return
        approved = approved_asset_path(item)
        fixed_views = [value for value in
                       (getattr(item, "reference_views", {}) or {}).values() if value]
        keep = list(dict.fromkeys(([approved] if approved else []) + fixed_views))
        refs = list(dict.fromkeys(getattr(item, "reference_images", []) or []))
        removed = [path for path in refs if path not in keep]
        if not removed:
            QMessageBox.information(self, "清理候选", "当前没有多余候选需要清理。")
            return
        answer = QMessageBox.question(
            self, "清理其他候选",
            f"要移除 {len(removed)} 张多余候选吗？\n\n"
            "主参考和已保存的固定视角会保留；本地图片文件不会删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        item.reference_images = keep
        getattr(self.db, f"save_{kind}")(item)
        self.assetChanged.emit(kind)
        self.refresh()
        self.focus_node(f"asset:{kind}:{asset_id}")

    def assign_take_view(self, node, role, role_label):
        data = node.payload
        item = getattr(self.db, f"get_{data['kind']}")(data["asset_id"])
        path = data.get("path", "")
        if not item or not path:
            return
        assign_asset_view(item, role, path)
        getattr(self.db, f"save_{data['kind']}")(item)
        self.assetChanged.emit(data["kind"])
        self.refresh()
        QMessageBox.information(self, "固定视角", f"已保存为“{role_label}”，主参考没有改变。")

    def open_take_in_studio(self, node):
        data = node.payload
        item = getattr(self.db, f"get_{data['kind']}")(data["asset_id"])
        if not item:
            return
        key = f"{data['kind']}:{item.id}"
        existing = self._asset_studios.get(key)
        if existing is not None:
            existing.showNormal()
            existing.raise_()
            existing.activateWindow()
            return
        dialog = AssetStudioDialog(item, data["kind"], self.db, self)
        path = data.get("path", "")
        if path:
            dialog._select_reference(path)
        self._asset_studios[key] = dialog

        def _finished(_result=0, key=key, dialog=dialog):
            self._asset_studios.pop(key, None)
            if dialog.asset_saved:
                self.assetChanged.emit(data["kind"])
                self.refresh()

        dialog.finished.connect(_finished)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _find_shot(self, shot_id):
        return next((shot for shot in self.current_storyboard().get("shots", [])
                     if str(shot.get("id") or "") == str(shot_id)), None)

    def open_scene_stage(self, shot_id: str):
        """Open the authoritative blocking stage and bind its camera capture."""
        shot = self._find_shot(shot_id)
        if shot is None:
            QMessageBox.information(self, "3D 导演台", "没有找到当前镜头。")
            return False
        try:
            from ai.ui.scene_stage_dialog import SceneStageDialog
            stage = normalize_scene_stage(
                shot.get("scene_stage") or {},
                proxy=shot.get("scene_proxy") or {}, shot=shot)
            output_dir = Path(__file__).parents[2] / "work_output" / "scene_stage"
            dialog = SceneStageDialog(stage, output_dir=str(output_dir), parent=self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            compiled = stage_shot_contract(dialog.stage)
            for key, value in compiled.items():
                shot[key] = json.loads(json.dumps(value, ensure_ascii=False))
            if dialog.capture_path and os.path.exists(dialog.capture_path):
                shot["scene_stage_capture"] = dialog.capture_path
                shot["composition_reference_path"] = dialog.capture_path
            shot["production_ready"] = False
            shot["director_gate"] = {
                "passed": not director_gate_issues(shot),
                "issues": director_gate_issues(shot),
            }
            for key in ("final_image_prompt", "final_start_image_prompt",
                        "final_end_image_prompt", "final_video_prompt"):
                shot.pop(key, None)
            rebuild_continuity(self.current_storyboard())
            self.storyboardMutated.emit()
            self._save_layout_now()
            self.refresh()
            self.focus_node(f"shot:{shot_id}")
            QMessageBox.information(
                self, "3D 站位已绑定",
                "人物世界坐标、固定设备、摄影机位置、FOV 和构图快照已写入当前镜头。\n"
                "重新执行“确认调度并合成定稿提示词”后，图片生成器会优先使用该构图。")
            return True
        except Exception as error:
            QMessageBox.warning(self, "3D 导演台打开失败", str(error))
            return False

    def update_shot_director_contract(self, shot_id: str, values: dict):
        """保存检查器中的导演合同，并使旧的生成提示词失效。"""
        shot = self._find_shot(shot_id)
        if shot is None:
            return False
        for key in (
                "story_function", "visual_thesis", "action_start",
                "primary_action", "action_end", "dominant_camera_move",
                "keyframe_strategy", "generation_risk"):
            if key in values:
                shot[key] = str(values.get(key) or "").strip()
        if "keyframe_strategy" in values:
            explicit_pair = str(values.get("keyframe_strategy") or "") == "first_last"
            shot["endpoint_pair_enabled"] = explicit_pair
            shot["endpoint_pair_required"] = explicit_pair
            shot["endpoint_pair_runtime_mode"] = (
                "first_last_pending" if explicit_pair else "first_frame")
            shot.pop("endpoint_pair_recommended", None)
            if not explicit_pair:
                shot.pop("endpoint_pair_qc", None)
                shot.pop("endpoint_pair_fallback_reason", None)
        invariants = values.get("continuity_invariants")
        if isinstance(invariants, str):
            invariants = invariants.splitlines()
        if invariants is not None:
            shot["continuity_invariants"] = list(dict.fromkeys(
                str(value).strip() for value in invariants if str(value).strip()))
        scene_view_id = str(values.get("scene_view_id") or "").strip()
        if scene_view_id in {"master", "reverse", "left", "right"}:
            shot["scene_view_id"] = scene_view_id
        raw_bbox = values.get("editable_bbox_xy")
        if raw_bbox is not None:
            if isinstance(raw_bbox, str):
                parts = [part.strip() for part in raw_bbox.replace("，", ",").split(",")]
                raw_bbox = parts if len(parts) == 4 else shot.get("editable_bbox_xy")
            shot["editable_bbox_xy"] = normalize_bbox(
                raw_bbox, tuple(normalize_bbox(shot.get("editable_bbox_xy"))))
        normalize_director_contract(shot)
        issues = director_gate_issues(shot)
        shot["director_gate"] = {"passed": not issues, "issues": issues}
        for key in ("final_image_prompt", "final_start_image_prompt",
                    "final_end_image_prompt", "final_video_prompt"):
            shot.pop(key, None)
        shot["production_ready"] = False
        rebuild_continuity(self.current_storyboard())
        self.storyboardMutated.emit()
        self._save_layout_now()
        self.refresh()
        self.focus_node(f"shot:{shot_id}")
        return True

    @staticmethod
    def _motion_board_paths(shot: dict):
        paths = {str(shot.get("motion_board_path") or "")}
        paths.update(str(value or "") for value in
                     (shot.get("motion_panel_paths") or []))
        paths.update(str(value or "") for value in
                     (shot.get("motion_panel_pending_paths") or []))
        if 3 <= len(shot.get("motion_keyframes") or []) <= 6:
            paths.add(str(shot.get("draft_panel") or ""))
        paths.update(
            str(value.get("path") or "") for value in shot.get("assets", [])
            if isinstance(value, dict) and
            str(value.get("subtype") or "") in {
                "motion_storyboard", "motion_storyboard_panel"})
        return {value for value in paths if value}

    def _motion_board_contract_stale(self, shot: dict) -> bool:
        if not (str(shot.get("motion_board_path") or shot.get("draft_panel") or "") and
                str(shot.get("draft_source") or "") == "ai"):
            return False
        version_stale = (int(shot.get("motion_board_contract_version") or 0) <
                         MOTION_STORYBOARD_CONTRACT_VERSION)
        stored_aspect = str(shot.get("motion_board_aspect_ratio") or "").strip()
        aspect_stale = (not stored_aspect or
                        normalize_aspect_ratio(stored_aspect) !=
                        self._storyboard_production_ratio())
        return version_stale or aspect_stale

    def _path_has_motion_board_lineage(self, shot: dict, path: str):
        """Identify direct boards and old outputs generated from board pixels."""
        path = str(path or "")
        if not path:
            return False
        board_paths = self._motion_board_paths(shot)
        if path in board_paths:
            return True
        shot_id = str(shot.get("id") or "")
        for record in self._positions().get("__custom_nodes__", []):
            if not isinstance(record, dict) or record.get("generator_kind") != "image":
                continue
            record_shots = {str(value) for value in
                            (record.get("shot_ids") or [record.get("shot_id")]) if value}
            if shot_id not in record_shots:
                continue
            outputs = {str(record.get("path") or "")}
            outputs.update(str(value) for value in record.get("candidates", []) if value)
            legacy_outputs = {str(value) for value in
                              record.get("legacy_motion_board_outputs", []) if value}
            reference_paths = {str(value) for value in record.get("references", []) if value}
            reference_paths.update(
                str(value.get("path") or "")
                for value in record.get("reference_assets", []) if isinstance(value, dict))
            if path in legacy_outputs or (
                    path in outputs and bool(board_paths & reference_paths)):
                return True
        return False

    def _sanitize_motion_board_pixel_references(self, record: dict):
        """Detach legacy motion-board bitmaps while preserving their text contract."""
        shot_ids = [str(value) for value in
                    (record.get("shot_ids") or [record.get("shot_id")]) if value]
        board_paths = set()
        for shot_id in shot_ids:
            shot = self._find_shot(shot_id)
            if shot is not None:
                board_paths.update(self._motion_board_paths(shot))
        if not board_paths:
            return False
        old_references = [str(value) for value in record.get("references", []) if value]
        leaked = board_paths & set(old_references)
        leaked.update(
            str(value.get("path") or "") for value in record.get("reference_assets", [])
            if isinstance(value, dict) and str(value.get("path") or "") in board_paths)
        if not leaked:
            return False
        old_outputs = [str(record.get("path") or "")]
        old_outputs.extend(str(value) for value in record.get("candidates", []) if value)
        record["legacy_motion_board_outputs"] = list(dict.fromkeys(
            list(record.get("legacy_motion_board_outputs") or []) +
            [value for value in old_outputs if value]))
        record["references"] = [value for value in old_references if value not in board_paths]
        record["reference_assets"] = [
            value for value in record.get("reference_assets", [])
            if isinstance(value, dict) and str(value.get("path") or "") not in board_paths]
        record["status"] = "已隔离运动分镜像素 · 新结果不会携带箭头标记"
        return True

    def _shot_take_block_reason(self, node):
        shot = self._find_shot(node.payload.get("shot_id"))
        path = str(node.payload.get("path") or "")
        if shot is None:
            return ""
        asset = node.payload.get("asset")
        if (isinstance(asset, dict) and
                str(asset.get("subtype") or "") == "motion_storyboard"):
            return "这是运动分镜板，只用于控制动作和机位，不能作为定稿图片或视频首帧。"
        spatial_qc = dict(asset.get("spatial_qc") or {}) if isinstance(asset, dict) else {}
        frame_role = str(asset.get("frame_role") or "") if isinstance(asset, dict) else ""
        if frame_role == "end" and str(spatial_qc.get("status") or "") != "pass":
            return (
                "这个结束帧没有通过首尾画面一致性检查，不能绑定到视频；"
                "请重新生成，或把镜头策略改为单首帧驱动。")
        if str(spatial_qc.get("status") or "") == "fail":
            score = spatial_qc.get("fixed_structure_similarity")
            suffix = (f"（固定结构相似度 {float(score):.2f}）"
                      if isinstance(score, (int, float)) else "")
            return ("固定设备或场景结构相对权威视图发生漂移" + suffix +
                    "；请重新生成，或在局部编辑中缩小可变区域后修复。")
        if self._path_has_motion_board_lineage(shot, path):
            return "这个候选来自旧版运动分镜像素参考，可能含箭头或标记；请重新执行第 5 步生成干净定稿。"
        return ""

    def _video_anchor_issues(self, shots):
        missing = []
        contaminated = []
        for index, shot in enumerate(shots):
            number = int(shot.get("number") or index + 1)
            paths = [str(shot.get("selected_image_asset") or "")]
            if endpoint_pair_requested(shot) and shot.get("endpoint_pair_required"):
                paths.append(str(shot.get("selected_end_image_asset") or ""))
            if any(not path or not os.path.exists(path) for path in paths):
                missing.append(number)
            elif any(self._path_has_motion_board_lineage(shot, path) for path in paths):
                contaminated.append(number)
        return sorted(set(missing)), sorted(set(contaminated))

    def _show_video_anchor_issues(self, shots):
        missing, contaminated = self._video_anchor_issues(shots)
        if not missing and not contaminated:
            return False
        if contaminated:
            contaminated_set = set(contaminated)
            for index, shot in enumerate(shots):
                number = int(shot.get("number") or index + 1)
                if number not in contaminated_set:
                    continue
                start_path = str(shot.get("selected_image_asset") or "")
                end_path = str(shot.get("selected_end_image_asset") or "")
                if self._path_has_motion_board_lineage(shot, start_path):
                    shot["selected_image_asset"] = ""
                    shot["anchor_frame_id"] = ""
                    if str(shot.get("selected_asset") or "") == start_path:
                        shot["selected_asset"] = ""
                if self._path_has_motion_board_lineage(shot, end_path):
                    shot["selected_end_image_asset"] = ""
                    shot["end_anchor_frame_id"] = ""
                shot["status"] = "旧版箭头污染候选已取消定稿 · 待重新生图"
            source_id = self._current_production_source_id()
            source = self._custom_record(source_id) if source_id else None
            if source is not None:
                source["pipeline_stage"] = "prompts_ready"
                source["status"] = "已隔离旧版箭头污染候选 · 点击继续重新执行第 5 步"
                source["auto_run_enabled"] = False
                source.pop("awaiting_gate", None)
            self.storyboardMutated.emit()
            self._save_layout_now(); self._update_production_continue_button()
        lines = []
        if missing:
            lines.append("没有完整的干净起止帧：" + "、".join(
                f"{number:02d}" for number in missing))
        if contaminated:
            lines.append("定稿仍来自旧版运动分镜像素参考：" + "、".join(
                f"{number:02d}" for number in contaminated))
        QMessageBox.information(
            self, "视频首帧检查未通过",
            "视频不会再使用手绘运动板或可能携带箭头的旧候选作为首尾帧。\n\n" +
            "\n".join(lines) +
            ("\n\n系统已取消受污染的定稿选择。点击程序坞继续重新执行第 5 步，"
             "选择新生成的干净定稿图后再生成视频。" if contaminated else
             "\n\n请先选择干净定稿图后再生成视频。"))
        return True

    def request_shot(self, node, operation):
        if operation == "video":
            shot = self._find_shot(node.payload.get("shot_id"))
            if shot is not None and self._show_video_anchor_issues([shot]):
                return
        self.generateShotRequested.emit(data_id(node), operation)

    def show_generation_menu(self):
        """按当前节点上下文集中展示全部可用生成动作。"""
        selected = [item for item in self.scene.selectedItems()
                    if isinstance(item, CanvasNodeItem)]
        node = selected[0] if len(selected) == 1 else None
        menu = QMenu(self)
        self._style_popup_menu(menu)

        def add(text, callback, enabled=True):
            action = menu.addAction(text)
            action.setEnabled(enabled)
            action.triggered.connect(callback)
            return action

        if node is None:
            add("先选中一个导演、资产、镜头或结果节点", lambda: None, False)
        elif node.node_type == "director":
            add("生成 / 调整剧本与分镜", lambda: self.directorRequested.emit("director"))
            add("回到创意输入", lambda: self.directorRequested.emit("idea"))
        elif node.node_type in ("scene", "character", "element"):
            add("生成视觉资产候选", lambda n=node: self.activate_node(n))
            add("基于主参考继续生成", lambda n=node: self.activate_node(n))
        elif node.node_type in ("asset_view", "asset_take"):
            add("基于这张图继续生成", lambda n=node: self.open_take_in_studio(n))
        elif node.node_type == "shot":
            add("生成关键帧候选", lambda n=node: self.request_shot(n, "image"))
            add("基于定稿图片继续生图", lambda n=node: self.request_shot(n, "image_edit"))
            add("用定稿图片生成视频", lambda n=node: self.request_shot(n, "video"))
            menu.addSeparator()
            add("生成对白音频", lambda n=node: self.request_shot(n, "dialogue_audio"))
            add("准备缺失的角色 / 场景 / 元素",
                lambda n=node: self.prepareShotAssetsRequested.emit(data_id(n)))
        elif node.node_type == "shot_take":
            blocked_reason = self._shot_take_block_reason(node)
            if blocked_reason:
                add(blocked_reason, lambda: None, False)
            elif str(node.payload.get("kind") or "image") == "image":
                add("只重新生成这个图片节点", lambda n=node: self.regenerate_image_result(n))
                add("用这个结果生成视频", lambda n=node: self.request_result_video(n))
                add("基于这个结果继续生图", lambda n=node: self.request_shot(n, "image_edit"))
            elif str(node.payload.get("kind") or "") == "video":
                add("只重新生成这个视频段", lambda n=node: self.regenerate_video_result(n))
            if not blocked_reason:
                add("送到 PS 局部精修", lambda n=node: self.request_refine(n))
        elif node.node_type == "generation_task":
            handle = node.payload.get("handle")
            add("取消这个生成任务", lambda h=handle: h.cancel() if h else None,
                bool(handle and not handle.is_finished))
        else:
            add("当前节点没有可用生成动作", lambda: None, False)

        anchor = getattr(self, "dock_generate_btn", None)
        screen_pos = (anchor.mapToGlobal(anchor.rect().topLeft())
                      if anchor is not None else self.mapToGlobal(self.rect().center()))
        menu.exec(screen_pos)

    def request_refine(self, node):
        self.refineShotRequested.emit(data_id(node))

    def _adopt_video_candidate(self, node, shot: dict, asset: dict,
                               generator: dict, path: str):
        """Promote one take, then QC it before any downstream handoff."""
        frames = [str(value) for value in asset.get("video_review_frames", [])
                  if value and os.path.exists(str(value))]
        if not frames:
            frames = self._extract_video_review_frames(path)
        generator_id = str(generator.get("id") or
                           asset.get("generator_node_id") or "")
        target_ids = [str(value) for value in
                      (generator.get("shot_ids") or [shot.get("id")]) if value]
        targets = [self._find_shot(value) for value in target_ids]
        targets = [value for value in targets if value is not None]
        if not targets:
            targets = [shot]

        segment_offset = 0.0
        for target in targets:
            target["selected_video_asset"] = path
            target["selected_asset"] = path
            target["preview_asset"] = path
            target["asset_type"] = "video"
            target["status"] = "video_qc_pending"
            target["video_segment_node_id"] = generator_id
            target["video_segment_offset"] = segment_offset
            target["video_segment_duration"] = float(target.get("duration") or 5)
            segment_offset += float(target.get("duration") or 5)
            if frames:
                target["video_review_frames"] = list(frames)
            target["spatial_review"] = json.loads(json.dumps(
                asset.get("spatial_review") or {}, ensure_ascii=False))
            for value in target.get("assets", []):
                if (isinstance(value, dict) and
                        str(value.get("kind") or "") == "video" and
                        str(value.get("generator_node_id") or "") == generator_id):
                    value["approved"] = str(value.get("path") or "") == path
        if frames:
            targets[-1]["video_tail_frame"] = frames[-1]

        if generator:
            generator["path"] = path
            generator["selected_candidate_path"] = path
            generator["video_review_frames"] = list(frames)
            generator["video_tail_frame"] = frames[-1] if frames else ""
            generator["video_thumbnail"] = (
                frames[1] if len(frames) >= 3 else frames[0] if frames else "")
            generator["deterministic_qc"] = json.loads(json.dumps(
                asset.get("deterministic_qc") or {}, ensure_ascii=False))
            generator["spatial_review"] = json.loads(json.dumps(
                asset.get("spatial_review") or {}, ensure_ascii=False))
            generator["adopted"] = True
            generator["human_approved"] = True
            generator["retry_stop"] = False
            generator["qc_failure_count"] = 0
            generator["awaiting_candidate_selection"] = False
            generator["handoff_approved"] = False
            generator.pop("clip_qc_signature", None)
            generator["clip_qc"] = {"status":"pending", "kind":"clip_qc"}
            generator["status"] = "定稿候选已采用 · 正在逐段审片"

        group_id = str(generator.get("workflow_group_id") or "")
        group = self._custom_record(group_id) if group_id else None
        if group is not None:
            group["awaiting_video_node_id"] = generator_id
            group["status"] = "当前段已定稿 · 自动审片中"
        source_id = (str((group or {}).get("source_node_id") or "") or
                     self._current_production_source_id())
        source = self._custom_record(source_id) if source_id else None
        if source is not None:
            source["pipeline_stage"] = "video_qc_pending"
            source["approval_required"] = "approved_video_qc"
            source["status"] = "视频候选已采用 · 审片通过后才会生成下一连续段"

        self.storyboardMutated.emit(); self._save_layout_now()
        self.refresh(); self.focus_node(f"shot:{shot.get('id')}")
        if generator:
            self._submit_video_clip_qc(generator, group_id)
        self.shotTakeAdopted.emit(str(shot.get("id") or ""), path)
        return True

    def adopt_shot_take(self, node):
        shot = self._find_shot(node.payload.get("shot_id"))
        path = node.payload.get("path", "")
        if not shot or not path or not os.path.exists(path):
            return False
        blocked_reason = self._shot_take_block_reason(node)
        if blocked_reason:
            QMessageBox.information(self, "不能设为定稿", blocked_reason)
            return False
        kind = str(node.payload.get("kind") or "image")
        asset = node.payload.get("asset") if isinstance(node.payload.get("asset"), dict) else {}
        generator = self._custom_record(str(asset.get("generator_node_id") or "")) or {}
        if kind == "video":
            return self._adopt_video_candidate(node, shot, asset, generator, str(path))
        frame_role = str(asset.get("frame_role") or generator.get("frame_role") or "start")
        shot["preview_asset"] = path
        shot["asset_type"] = kind
        shot["status"] = "ready"
        if kind == "image":
            if frame_role == "end":
                shot["selected_end_image_asset"] = path
                shot["end_anchor_frame_id"] = path
                shot["endpoint_pair_qc"] = json.loads(json.dumps(
                    asset.get("spatial_qc") or {}, ensure_ascii=False))
                shot["endpoint_pair_required"] = True
                shot["endpoint_pair_runtime_mode"] = "first_last"
            else:
                shot["selected_image_asset"] = path
                shot["anchor_frame_id"] = path
                shot["selected_asset"] = path
        else:
            shot["selected_video_asset"] = path
            shot["selected_asset"] = path
        shot_id = str(shot.get("id") or "")
        source_id = self._current_production_source_id()
        source = self._custom_record(source_id) if source_id else None
        should_continue = False
        source_stage = str((source or {}).get("pipeline_stage") or "")
        if (kind == "image" and source is not None and
                source_stage == "start_image_candidates_ready"):
            targets = self._production_group_shots(source_id, "image")
            should_continue = bool(targets) and all(
                os.path.exists(str(value.get("selected_image_asset") or ""))
                for value in targets)
            if should_continue:
                source.pop("awaiting_gate", None)
                source["status"] = "起始帧已选齐 · 正在生成同空间结束帧"
        elif (kind == "image" and source is not None and
              source_stage == "image_candidates_ready"):
            targets = self._production_group_shots(source_id, "image")
            should_continue = bool(targets) and all(
                self._shot_endpoint_pair_ready(value) for value in targets)
            if should_continue:
                source.pop("awaiting_gate", None)
                source["status"] = "首尾帧已选齐 · 正在生成视频"
        self.storyboardMutated.emit()
        # The legacy script workbench only understands one image anchor and
        # would overwrite K1 when Klast is adopted. Keep the second endpoint
        # canvas-native until that signal gains an explicit frame role.
        if kind != "image" or frame_role != "end":
            self.shotTakeAdopted.emit(str(shot.get("id") or ""), path)
        self.refresh()
        self.focus_node(f"shot:{shot_id}")
        if should_continue:
            if source_stage == "start_image_candidates_ready":
                self._prepare_and_execute_end_frame_generators(source_id)
            else:
                self._schedule_auto_continue(source_id, from_async=False)
        return True

    def adopt_motion_storyboard_take(self, node):
        """Choose a motion-board candidate without promoting it to a final still."""
        shot = self._find_shot(node.payload.get("shot_id"))
        path = str(node.payload.get("path") or "")
        asset = node.payload.get("asset")
        if (not shot or not path or not os.path.exists(path) or
                not isinstance(asset, dict) or
                str(asset.get("subtype") or "") != "motion_storyboard"):
            return False
        for value in shot.get("assets", []):
            if (isinstance(value, dict) and
                    str(value.get("subtype") or "") == "motion_storyboard"):
                value["approved"] = str(value.get("path") or "") == path
        shot["draft_panel"] = path
        shot["motion_board_path"] = path
        shot["preview_asset"] = path
        shot["selected_asset"] = path
        shot["asset_type"] = "image"
        shot["draft_source"] = "ai"
        version = int(asset.get("contract_version") or 0)
        shot["motion_board_contract_version"] = version
        raw_asset_aspect = str(asset.get("aspect_ratio") or "").strip()
        asset_aspect = (normalize_aspect_ratio(raw_asset_aspect)
                        if raw_asset_aspect else "")
        panel_paths = [str(value) for value in (asset.get("panel_paths") or [])]
        if (version >= MOTION_STORYBOARD_CONTRACT_VERSION and
                asset_aspect == self._storyboard_production_ratio() and
                len(panel_paths) == len(shot.get("motion_keyframes") or []) and
                all(os.path.exists(value) for value in panel_paths)):
            shot["motion_panel_paths"] = panel_paths
            shot["motion_board_aspect_ratio"] = asset_aspect
            shot["motion_board_qc"] = inspect_motion_panels(
                panel_paths, shot, asset_aspect)
            # Selecting a motion-board take is an explicit human decision.
            # Preserve the automatic QC evidence, but do not leave a stale
            # auto_rejected flag blocking the next production stage.
            shot["motion_board_review_status"] = "manually_approved"
            shot["motion_board_risk_accepted"] = (
                str((shot.get("motion_board_qc") or {}).get("status") or "") ==
                "fail")
        else:
            shot["motion_panel_paths"] = []
            shot["motion_board_review_status"] = "stale_aspect_ratio"
            shot["production_ready"] = False
        self._canvas_storyboard_previous = path
        rebuild_continuity(self.current_storyboard())
        self.storyboardMutated.emit()
        self._save_layout_now()
        self.refresh()
        self.focus_node(f"shot:{shot.get('id')}")
        return True

    def keep_only_motion_storyboard_take(self, node):
        shot = self._find_shot(node.payload.get("shot_id"))
        path = str(node.payload.get("path") or "")
        if not shot or not path:
            return False
        assets = [value for value in shot.get("assets", [])
                  if isinstance(value, dict)]
        selected = next((value for value in assets
                         if str(value.get("path") or "") == path and
                         str(value.get("subtype") or "") == "motion_storyboard"), None)
        if selected is None:
            return False
        shot["assets"] = [
            value for value in assets
            if str(value.get("subtype") or "") != "motion_storyboard" or
            str(value.get("path") or "") == path]
        return self.adopt_motion_storyboard_take(node)

    def remove_shot_result(self, node):
        shot = self._find_shot(node.payload.get("shot_id"))
        path = str(node.payload.get("path") or "")
        if not shot or not path:
            return
        answer = QMessageBox.question(
            self, "移除生成结果",
            "要从这个镜头中移除当前结果吗？\n\n"
            "本地图片或视频文件会保留。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed_asset = next((asset for asset in shot.get("assets", [])
                              if isinstance(asset, dict) and
                              str(asset.get("path") or "") == path), {})
        removed_kind = str(removed_asset.get("kind") or node.payload.get("kind") or "")
        assets = [asset for asset in shot.get("assets", [])
                  if isinstance(asset, dict) and asset.get("path") != path]
        shot["assets"] = assets
        removed_motion_board = (
            str(removed_asset.get("subtype") or "") == "motion_storyboard")
        replacement_motion_path = ""
        if removed_motion_board and str(shot.get("motion_board_path") or "") == path:
            replacement_motion = next((
                asset for asset in reversed(assets)
                if str(asset.get("subtype") or "") == "motion_storyboard" and
                os.path.exists(str(asset.get("path") or ""))), None)
            replacement_motion_path = str(
                (replacement_motion or {}).get("path") or "")
            shot["motion_board_path"] = replacement_motion_path
            shot["draft_panel"] = replacement_motion_path
            for value in assets:
                if str(value.get("subtype") or "") == "motion_storyboard":
                    value["approved"] = str(value.get("path") or "") == replacement_motion_path
        if (shot.get("selected_image_asset") == path or
                shot.get("anchor_frame_id") == path):
            shot["selected_image_asset"] = ""
            shot["anchor_frame_id"] = ""
        if (shot.get("selected_end_image_asset") == path or
                shot.get("end_anchor_frame_id") == path):
            shot["selected_end_image_asset"] = ""
            shot["end_anchor_frame_id"] = ""
        if shot.get("selected_video_asset") == path:
            shot["selected_video_asset"] = ""
        if (shot.get("preview_asset") == path or shot.get("selected_asset") == path):
            same_kind = next((asset for asset in reversed(assets)
                              if str(asset.get("kind") or "") == removed_kind and
                              str(asset.get("subtype") or "") != "motion_storyboard"), None)
            replacement_path = str(
                replacement_motion_path or
                shot.get("selected_video_asset") or shot.get("selected_image_asset") or
                shot.get("selected_end_image_asset") or
                (same_kind or {}).get("path") or "")
            replacement_kind = ("video" if replacement_path == shot.get("selected_video_asset") else
                                "image" if replacement_motion_path or
                                replacement_path == shot.get("selected_image_asset") else
                                str((same_kind or {}).get("kind") or ""))
            shot["selected_asset"] = replacement_path
            shot["preview_asset"] = replacement_path
            shot["asset_type"] = replacement_kind
        if not assets:
            shot["status"] = "pending"
        self.storyboardMutated.emit()
        self._save_layout_now()
        self.refresh()
        self.focus_node(f"shot:{shot.get('id')}")

    def keep_only_shot_result(self, node):
        shot = self._find_shot(node.payload.get("shot_id"))
        path = str(node.payload.get("path") or "")
        if not shot or not path:
            return
        assets = [asset for asset in shot.get("assets", []) if isinstance(asset, dict)]
        selected = next((asset for asset in assets if asset.get("path") == path), None)
        if not selected:
            return
        blocked_reason = self._shot_take_block_reason(node)
        if blocked_reason:
            QMessageBox.information(self, "不能设为唯一结果", blocked_reason)
            return
        selected_kind = str(selected.get("kind") or "image")
        selected_role = str(selected.get("frame_role") or "")
        removed_count = sum(
            1 for asset in assets
            if str(asset.get("kind") or "image") == selected_kind and
            (selected_kind != "image" or
             str(asset.get("frame_role") or "") == selected_role) and
            asset.get("path") != path)
        if removed_count <= 0:
            return
        answer = QMessageBox.question(
            self, "清理同类候选",
            f"要移除其他 {removed_count} 个{'图片' if selected_kind == 'image' else '视频'}候选吗？\n\n"
            "另一种类型的结果和本地文件会保留。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        shot["assets"] = [
            asset for asset in assets
            if str(asset.get("kind") or "image") != selected_kind or
            (selected_kind == "image" and
             str(asset.get("frame_role") or "") != selected_role) or
            asset.get("path") == path]
        shot["preview_asset"] = path
        kind = str(selected.get("kind") or "image")
        shot["asset_type"] = kind
        if kind == "image":
            if selected_role == "end":
                shot["selected_end_image_asset"] = path
                shot["end_anchor_frame_id"] = path
            else:
                shot["selected_image_asset"] = path
                shot["anchor_frame_id"] = path
                shot["selected_asset"] = path
        else:
            shot["selected_video_asset"] = path
            shot["selected_asset"] = path
        shot["status"] = "ready"
        self.storyboardMutated.emit()
        if kind != "image" or selected_role != "end":
            self.shotTakeAdopted.emit(str(shot.get("id") or ""), path)
        self.refresh()
        self.focus_node(f"shot:{shot.get('id')}")

    def request_result_video(self, node):
        if self.adopt_shot_take(node):
            self.generateShotRequested.emit(node.payload.get("shot_id", ""), "video")

    def request_result_refine(self, node):
        if self.adopt_shot_take(node):
            self.refineShotRequested.emit(node.payload.get("shot_id", ""))

    def save_result_to_library(self, node):
        path = str(node.payload.get("path") or "")
        if not path or not os.path.exists(path):
            QMessageBox.information(self, "保存到资产库", "当前结果文件不存在。")
            return
        choices = ["主体（人物 / 动物 / 怪物）", "场景", "元素（道具 / Logo / 产品）"]
        choice, ok = QInputDialog.getItem(
            self, "保存到资产库", "这张图片属于哪一类资产？", choices, 0, False)
        if not ok:
            return
        kind = {
            choices[0]: "character", choices[1]: "scene", choices[2]: "element",
        }[choice]
        try:
            from ai.ui.resource_center import import_assets_to_resource_center
            result = import_assets_to_resource_center(self, [path], default_kind=kind)
            if not result:
                return
            saved_kind, items = result
            self.assetChanged.emit(saved_kind)
            self._last_db_signature = self._db_signature()
            self.asset_library.refresh()
            if items:
                QMessageBox.information(
                    self, "已保存到资产库",
                    f"已保存“{getattr(items[0], 'name', '未命名资产')}”。\n"
                    "它不会自动铺到画布上，需要时从左侧资产库拖入即可。")
        except Exception as error:
            QMessageBox.warning(self, "保存失败", str(error))

    def _selected_asset_image_paths(self, nodes=None):
        """Collect only source/result images, never video-cover thumbnails."""
        nodes = list(nodes) if nodes is not None else [
            item for item in self.scene.selectedItems()
            if isinstance(item, CanvasNodeItem)]
        paths = []
        for node in nodes:
            path = ""
            if node.node_type == "image_node":
                path = str(node.payload.get("path") or "")
            elif node.node_type in ("asset_view", "asset_take"):
                path = str(node.payload.get("path") or "")
            elif node.node_type == "shot_take" and str(
                    node.payload.get("kind") or "image") == "image":
                path = str(node.payload.get("path") or "")
            elif node.node_type == "shot":
                shot = self._find_shot(node.payload.get("shot_id")) or {}
                path = str(shot.get("selected_image_asset") or "")
            if (path and path not in paths and os.path.isfile(path) and
                    self._is_image_path(path)):
                paths.append(path)
        return paths

    def save_selected_images_as_asset(self, nodes=None):
        """Persist several canvas image nodes as one multi-reference asset."""
        nodes = list(nodes) if nodes is not None else [
            item for item in self.scene.selectedItems()
            if isinstance(item, CanvasNodeItem)]
        paths = self._selected_asset_image_paths(nodes)
        if len(paths) < 2:
            QMessageBox.information(
                self, "合并保存资产",
                "请框选或按住 Ctrl 选择至少两个已有图片文件的节点。")
            return False
        kinds = {
            str(node.payload.get("asset_kind") or node.payload.get("kind") or "")
            for node in nodes}
        kinds &= {"character", "scene", "element"}
        default_kind = next(iter(kinds)) if len(kinds) == 1 else "character"
        try:
            from ai.ui.resource_center import import_assets_to_resource_center
            result = import_assets_to_resource_center(
                self, paths, default_kind=default_kind, force_same=True)
            if not result:
                return False
            saved_kind, items = result
            self.assetChanged.emit(saved_kind)
            self._last_db_signature = self._db_signature()
            self.asset_library.refresh()
            if items:
                QMessageBox.information(
                    self, "组合资产已保存",
                    f"已把 {len(paths)} 张图片合并保存为一个资产：\n"
                    f"“{getattr(items[0], 'name', '未命名资产')}”。\n\n"
                    "它们会作为同一人物、场景或元素的多张权威参考使用。")
            return True
        except Exception as error:
            QMessageBox.warning(self, "保存失败", str(error))
            return False

    def focus_node(self, node_id):
        node = self._nodes.get(node_id)
        if not node:
            return
        self.scene.clearSelection()
        node.setSelected(True)
        self.view.centerOn(node)

    def focus_kind(self, kind=""):
        mapping = {"prompt": "all", "": "all"}
        kind = mapping.get(kind, kind)
        if kind in ("all", "scene", "character", "element"):
            self.toggle_asset_library(True)
            self.navigator_panel.tabs.setCurrentIndex(1)
            for tab_index in range(self.asset_library.tabs.count()):
                if self.asset_library.tabs.tabData(tab_index) == kind:
                    self.asset_library.tabs.setCurrentIndex(tab_index)
                    break
        self._active_filter = (kind if kind in {
            "all", "scene", "character", "element", "shot", "take"} else "all")
        self._apply_visibility()
        node = next((node for node in self._nodes.values()
                     if node.node_type == kind), None)
        if node:
            self.focus_node(node.node_id)

    def _filter_changed(self):
        combo = getattr(self, "filter_combo", None)
        self._active_filter = combo.currentData() if combo is not None else "all"
        self._active_filter = self._active_filter or "all"
        self._apply_visibility()

    def _apply_visibility(self):
        query = self.search_edit.text().strip().casefold() if hasattr(self, "search_edit") else ""
        mode = self._active_filter
        for node in self._nodes.values():
            category = ("take" if node.node_type in (
                "asset_view", "asset_take", "shot_take", "generation_task") else node.node_type)
            type_ok = mode == "all" or category == mode
            text_ok = not query or query in f"{node.title} {node.subtitle}".casefold()
            node.setVisible(type_ok and text_ok)
        for edge in self.scene.edges:
            edge.setVisible(edge.source.isVisible() and edge.target.isVisible())

    def auto_layout(self):
        self.hide_inline_editor()
        nodes = list(self._nodes.values())
        indegree = {node.node_id: 0 for node in nodes}
        outgoing = {node.node_id: [] for node in nodes}
        for edge in self.scene.edges:
            sid, tid = edge.source.node_id, edge.target.node_id
            if sid in outgoing and tid in indegree and tid not in outgoing[sid]:
                outgoing[sid].append(tid)
                indegree[tid] += 1
        level = {node_id: 0 for node_id in indegree}
        queue = [node_id for node_id, value in indegree.items() if value == 0]
        visited = set()
        while queue:
            node_id = queue.pop(0)
            visited.add(node_id)
            for target in outgoing[node_id]:
                level[target] = max(level[target], level[node_id] + 1)
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)
        # 环或孤立异常关系不会阻塞整理，按类型放到合理列。
        defaults = {
            "director": 0, "text_node": 0, "storyboard_node": 0, "skill_node": 0,
            "workflow_group": 0, "scene": 1, "character": 1,
            "element": 1, "asset_view": 2, "asset_take": 2,
            "image_node": 2, "shot": 3, "video_node": 4,
            "audio_node": 4, "shot_take": 4, "generation_task": 4,
        }
        for node in nodes:
            if node.node_id not in visited or not self.scene.node_edges.get(node.node_id):
                level[node.node_id] = defaults.get(node.node_type, level[node.node_id])
        # 镜头是制片画布的总览索引，不能混在依赖列里被资产和生成结果
        # 向下挤压。无论镜头何时创建、如何连线，整理后都固定在第一行，
        # 严格按镜头号从左到右排列。
        shot_nodes = [node for node in nodes if node.node_type == "shot"]

        def shot_order(node):
            shot = node.payload.get("shot") if isinstance(node.payload, dict) else {}
            try:
                number = int((shot or {}).get("number"))
            except (TypeError, ValueError):
                match = re.search(r"\d+", str(node.title or ""))
                number = int(match.group()) if match else 10 ** 9
            return number, str(node.node_id)

        shot_nodes.sort(key=shot_order)
        shot_ids_in_order = [
            str(node.payload.get("shot_id") or
                node.node_id.removeprefix("shot:"))
            for node in shot_nodes]
        shot_id_set = set(shot_ids_in_order)

        def related_shot_id(node):
            payload = node.payload if isinstance(node.payload, dict) else {}
            values = payload.get("shot_ids")
            shot_ids = ([str(value) for value in values if value]
                        if isinstance(values, (list, tuple)) else [])
            direct = str(payload.get("shot_id") or "")
            if direct:
                shot_ids.insert(0, direct)
            return next((value for value in shot_ids
                         if value in shot_id_set), "")

        def lane_node_order(node):
            payload = node.payload if isinstance(node.payload, dict) else {}
            asset = payload.get("asset") if isinstance(payload.get("asset"), dict) else {}
            subtype = str(asset.get("subtype") or payload.get("subtype") or "")
            kind = str(payload.get("generator_kind") or payload.get("kind") or "")
            if node.node_type == "shot_take" and subtype == "motion_storyboard":
                stage = 0
            elif node.node_type == "generation_task":
                stage = 1
            elif node.node_type == "image_node" or kind == "image":
                stage = 2
            elif node.node_type == "video_node" or kind == "video":
                stage = 3
            elif node.node_type == "audio_node" or kind in {"audio", "dialogue_audio"}:
                stage = 4
            else:
                stage = 5
            return stage, str(node.title), str(node.node_id)

        lane_nodes = {shot_id:[] for shot_id in shot_ids_in_order}
        unassigned = []
        for node in nodes:
            if node.node_type == "shot":
                continue
            shot_id = related_shot_id(node)
            if shot_id:
                lane_nodes[shot_id].append(node)
            else:
                unassigned.append(node)

        # 先整理左侧的项目与资产树，再从资产区最右边开始铺镜头。
        # 压缩空的依赖层级，避免 level=0/2 之间留下无意义的大空列。
        columns = {}
        for node in unassigned:
            columns.setdefault(level[node.node_id], []).append(node)
        global_right = 0.0
        for display_column, (_level, column_nodes) in enumerate(
                sorted(columns.items())):
            column_nodes.sort(key=lambda node: (node.node_type, node.title))
            x = 90.0 + display_column * 620.0
            y = 80.0
            for node in column_nodes:
                node.setPos(QPointF(x, y))
                self._positions()[node.node_id] = [node.pos().x(), node.pos().y()]
                global_right = max(global_right, x + node.width)
                y += max(260.0, node.height + 70.0)

        lane_width = 620.0
        lane_content_width = 500.0
        shot_lane_start = global_right + 220.0 if global_right else 90.0
        lane_left_by_shot = {}
        for index, (shot_id, node) in enumerate(zip(shot_ids_in_order, shot_nodes)):
            lane_left = shot_lane_start + index * lane_width
            lane_left_by_shot[shot_id] = lane_left
            shot_x = lane_left + (lane_content_width - node.width) / 2.0
            node.setPos(QPointF(shot_x, 80.0))
            self._positions()[node.node_id] = [node.pos().x(), node.pos().y()]

        first_lane_y = (
            80.0 + max((node.height for node in shot_nodes), default=0.0) + 180.0
            if shot_nodes else 80.0)
        for shot_id, related in lane_nodes.items():
            y = first_lane_y
            lane_left = lane_left_by_shot[shot_id]
            for node in sorted(related, key=lane_node_order):
                x = lane_left + (lane_content_width - node.width) / 2.0
                node.setPos(QPointF(x, y))
                self._positions()[node.node_id] = [node.pos().x(), node.pos().y()]
                y += max(240.0, node.height + 70.0)
        self.scene.update_edges()
        self._save_layout_now()
        self.view.fit_nodes()
        if hasattr(self, "navigator_panel"):
            self.navigator_panel.refresh_outline()

    def showEvent(self, event):
        super().showEvent(event)
        if self._last_db_signature != self._db_signature():
            # 画布是工程权威。资产库变化只刷新导航库，不重建画布节点。
            self._last_db_signature = self._db_signature()
            if hasattr(self, "asset_library"):
                self.asset_library.refresh()
            if hasattr(self, "navigator_panel"):
                self.navigator_panel.refresh_outline()
