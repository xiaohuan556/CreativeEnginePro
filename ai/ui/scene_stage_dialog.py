"""Native PyQt 3D previs editor for actor blocking and camera composition."""
from __future__ import annotations

import math
import os
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QPointF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget, QInputDialog,
)

from ai.scene_stage import (
    active_camera, append_stage_capture, normalize_scene_stage,
    project_world_point,
)


BG = "#101217"
PANEL = "#181b22"
TEXT = "#e8eaf0"
MUTED = "#858b98"
ACCENT = "#6f8cff"


class SceneStageViewport(QWidget):
    selectionChanged = pyqtSignal(str)
    stageChanged = pyqtSignal()

    def __init__(self, stage, parent=None):
        super().__init__(parent)
        self.stage = stage
        self.view_camera_id = ""
        self.selected_id = ""
        self.zoom = 1.0
        self._drag_origin = None
        self._object_origin = None
        self._screen_points = {}
        self._background_cache = ("", QPixmap())
        self.setMinimumSize(640, 520)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_stage(self, stage):
        self.stage = stage
        self.update()

    def set_view_camera(self, camera_id):
        self.view_camera_id = str(camera_id or "")
        self.update()

    def set_selected(self, object_id):
        self.selected_id = str(object_id or "")
        self.update()

    def _director_point(self, point, width, height):
        room = self.stage.get("room") or {}
        span = max(float(room.get("width") or 10), float(room.get("depth") or 8))
        scale = min(width, height) / max(4.0, span * 1.55) * self.zoom
        x, y, z = [float(value) for value in point]
        return QPointF(width * .5 + (x - z) * .72 * scale,
                       height * .64 + (x + z) * .34 * scale - y * scale)

    def _camera_point(self, point, camera, width, height):
        projected = project_world_point(point, camera, (width, height))
        if not projected.get("visible"):
            return None
        return QPointF(projected["x"] * width, projected["y"] * height)

    def _point(self, point, width, height):
        camera = next((row for row in self.stage.get("cameras", [])
                       if row.get("id") == self.view_camera_id), None)
        return (self._camera_point(point, camera, width, height)
                if camera else self._director_point(point, width, height))

    def _draw_grid(self, painter, width, height):
        room = self.stage.get("room") or {}
        room_width = float(room.get("width") or 10)
        room_depth = float(room.get("depth") or 8)
        painter.setPen(QPen(QColor("#28303d"), 1))
        step = 1.0
        x = -math.floor(room_width / 2)
        while x <= room_width / 2 + .001:
            a = self._point([x, 0, -room_depth / 2], width, height)
            b = self._point([x, 0, room_depth / 2], width, height)
            if a is not None and b is not None:
                painter.drawLine(a, b)
            x += step
        z = -math.floor(room_depth / 2)
        while z <= room_depth / 2 + .001:
            a = self._point([-room_width / 2, 0, z], width, height)
            b = self._point([room_width / 2, 0, z], width, height)
            if a is not None and b is not None:
                painter.drawLine(a, b)
            z += step
        corners = [
            [-room_width / 2, 0, -room_depth / 2],
            [room_width / 2, 0, -room_depth / 2],
            [room_width / 2, 0, room_depth / 2],
            [-room_width / 2, 0, room_depth / 2],
        ]
        points = [self._point(value, width, height) for value in corners]
        if all(point is not None for point in points):
            painter.setPen(QPen(QColor("#596276"), 2))
            for index, point in enumerate(points):
                painter.drawLine(point, points[(index + 1) % len(points)])

    def _draw_actor(self, painter, row, point, width, height, clean=False):
        transform = row.get("transform") or {}
        scale = transform.get("scale") or [1, 1, 1]
        body_height = max(.8, float(scale[1] if len(scale) > 1 else 1) * 1.75)
        top = self._point([
            transform["position"][0], transform["position"][1] + body_height,
            transform["position"][2]], width, height)
        if top is None:
            return
        selected = row.get("id") == self.selected_id and not clean
        color = QColor(row.get("color") or "#f0aa65")
        painter.setPen(QPen(QColor("#ffffff") if selected else color, 3 if selected else 2))
        head_radius = max(5.0, min(13.0, abs(point.y() - top.y()) * .09))
        head = QPointF(top.x(), top.y() + head_radius)
        shoulder = QPointF(top.x(), top.y() + head_radius * 3.0)
        hip = QPointF(point.x(), point.y() - max(12.0, head_radius * 3.2))
        painter.drawEllipse(head, head_radius, head_radius)
        painter.drawLine(shoulder, hip)
        painter.drawLine(shoulder, QPointF(shoulder.x() - head_radius * 1.8,
                                           shoulder.y() + head_radius * 2.3))
        painter.drawLine(shoulder, QPointF(shoulder.x() + head_radius * 1.8,
                                           shoulder.y() + head_radius * 2.3))
        painter.drawLine(hip, QPointF(point.x() - head_radius * 1.2, point.y()))
        painter.drawLine(hip, QPointF(point.x() + head_radius * 1.2, point.y()))

    def _draw_box(self, painter, row, point, width, height, clean=False):
        transform = row.get("transform") or {}
        scale = transform.get("scale") or [1, 1, 1]
        position = transform.get("position") or [0, 0, 0]
        dx, dy, dz = [max(.05, float(value)) / 2 for value in scale]
        corners = []
        for y in (-dy, dy):
            for z in (-dz, dz):
                for x in (-dx, dx):
                    corners.append(self._point(
                        [position[0] + x, position[1] + y, position[2] + z],
                        width, height))
        visible = [value for value in corners if value is not None]
        if not visible:
            return
        selected = row.get("id") == self.selected_id and not clean
        color = QColor(row.get("color") or "#6f8cff")
        color.setAlpha(90 if not selected else 150)
        path = QPainterPath()
        left = min(value.x() for value in visible)
        right = max(value.x() for value in visible)
        top = min(value.y() for value in visible)
        bottom = max(value.y() for value in visible)
        path.addRoundedRect(left, top, max(5.0, right - left), max(5.0, bottom - top), 4, 4)
        painter.fillPath(path, color)
        painter.setPen(QPen(QColor("#ffffff") if selected else QColor(row.get("color") or "#6f8cff"),
                            2 if selected else 1))
        painter.drawPath(path)

    def _draw_camera(self, painter, row, width, height, clean=False):
        if self.view_camera_id and self.view_camera_id == row.get("id"):
            return
        position = (row.get("transform") or {}).get("position") or [0, 1.65, 6]
        target = row.get("target") or [0, 1, 0]
        a = self._point(position, width, height)
        b = self._point(target, width, height)
        if a is None or b is None:
            return
        selected = row.get("id") == self.selected_id and not clean
        painter.setPen(QPen(QColor("#ffffff") if selected else QColor("#66d8c2"),
                            3 if selected else 2))
        painter.drawEllipse(a, 7, 7)
        painter.drawLine(a, b)
        delta = b - a
        length = math.hypot(delta.x(), delta.y()) or 1
        normal = QPointF(-delta.y() / length, delta.x() / length)
        tip = a + delta * min(.32, 55 / length)
        painter.drawLine(a, tip + normal * 18)
        painter.drawLine(a, tip - normal * 18)
        self._screen_points[row.get("id")] = a

    def _paint_stage(self, painter, width, height, clean=False):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(0, 0, width, height, QColor(
            (self.stage.get("environment") or {}).get("background_color") or BG))
        background_path = str((self.stage.get("environment") or {}).get(
            "panorama_path") or "")
        if self.view_camera_id and background_path and os.path.exists(background_path):
            if self._background_cache[0] != background_path:
                self._background_cache = (background_path, QPixmap(background_path))
            background = self._background_cache[1]
            if not background.isNull():
                scaled = background.scaled(
                    width, height, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)
                source_x = max(0, (scaled.width() - width) // 2)
                source_y = max(0, (scaled.height() - height) // 2)
                painter.save(); painter.setOpacity(.38)
                painter.drawPixmap(0, 0, scaled, source_x, source_y, width, height)
                painter.restore()
        self._screen_points = {}
        self._draw_grid(painter, width, height)
        rows = [row for row in self.stage.get("objects", [])
                if isinstance(row, dict) and row.get("visible", True)]
        # Painter order follows depth in director view and camera depth in camera view.
        rows.sort(key=lambda row: sum((row.get("transform") or {}).get(
            "position") or [0, 0, 0]))
        for row in rows:
            position = (row.get("transform") or {}).get("position") or [0, 0, 0]
            point = self._point(position, width, height)
            if point is None:
                continue
            self._screen_points[row.get("id")] = point
            if row.get("kind") == "actor":
                self._draw_actor(painter, row, point, width, height, clean)
            else:
                self._draw_box(painter, row, point, width, height, clean)
            if not clean:
                painter.setPen(QColor("#d9dce6"))
                painter.drawText(point + QPointF(10, -8), str(row.get("name") or "对象"))
        for camera in self.stage.get("cameras", []):
            if isinstance(camera, dict):
                self._draw_camera(painter, camera, width, height, clean)
        if not clean:
            painter.setPen(QColor(MUTED))
            label = "导演自由视角" if not self.view_camera_id else "摄影机画面 · 3D 构图预演"
            painter.drawText(18, 28, label)
            painter.drawText(18, height - 18, "拖动对象调整 X/Z 站位 · 滚轮缩放 · 固定设备不可移动")

    def paintEvent(self, _event):
        painter = QPainter(self)
        self._paint_stage(painter, self.width(), self.height())
        painter.end()

    def render_clean(self, path, aspect_ratio="16:9"):
        from core.image_output_size import normalize_aspect_ratio
        aspect = normalize_aspect_ratio(aspect_ratio)
        width, height = {
            "16:9":(1280, 720), "9:16":(720, 1280),
            "1:1":(1024, 1024), "4:5":(1024, 1280),
        }[aspect]
        image = QImage(width, height, QImage.Format.Format_RGB32)
        painter = QPainter(image)
        self._paint_stage(painter, width, height, clean=True)
        painter.end()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        return bool(image.save(str(path), "PNG"))

    def _selected_row(self):
        for row in list(self.stage.get("objects", [])) + list(self.stage.get("cameras", [])):
            if isinstance(row, dict) and row.get("id") == self.selected_id:
                return row
        return None

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        point = event.position()
        candidates = [(math.hypot(point.x() - screen.x(), point.y() - screen.y()), object_id)
                      for object_id, screen in self._screen_points.items()]
        distance, object_id = min(candidates, default=(9999, ""))
        if distance <= 34:
            self.set_selected(object_id)
            self.selectionChanged.emit(object_id)
            row = self._selected_row()
            if row and not row.get("locked"):
                self._drag_origin = point
                self._object_origin = list((row.get("transform") or {}).get("position") or [0, 0, 0])
        else:
            self.set_selected("")
            self.selectionChanged.emit("")

    def mouseMoveEvent(self, event):
        if self._drag_origin is None or self._object_origin is None:
            return super().mouseMoveEvent(event)
        row = self._selected_row()
        if not row or row.get("locked"):
            return
        room = self.stage.get("room") or {}
        span = max(float(room.get("width") or 10), float(room.get("depth") or 8))
        meters_per_pixel = span * 1.55 / max(200, min(self.width(), self.height())) / self.zoom
        dx = (event.position().x() - self._drag_origin.x()) * meters_per_pixel
        dy = (event.position().y() - self._drag_origin.y()) * meters_per_pixel
        position = row["transform"]["position"]
        position[0] = round(self._object_origin[0] + dx * .7 + dy * .7, 3)
        position[2] = round(self._object_origin[2] - dx * .7 + dy * .7, 3)
        self.stageChanged.emit()
        self.update()

    def mouseReleaseEvent(self, event):
        if self._drag_origin is not None:
            self.stageChanged.emit()
        self._drag_origin = None
        self._object_origin = None
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        self.zoom = max(.45, min(2.6, self.zoom * (1.12 if event.angleDelta().y() > 0 else .89)))
        self.update()
        event.accept()


class SceneStageDialog(QDialog):
    """A lightweight director desk: object tree, 3D viewport and properties."""

    def __init__(self, stage, *, output_dir="", parent=None):
        super().__init__(parent)
        self.stage = normalize_scene_stage(stage)
        self.output_dir = str(output_dir or Path.cwd() / "work_output" / "scene_stage")
        self.capture_path = ""
        self._syncing = False
        self.setWindowTitle("3D 导演台 · 人物站位与机位")
        self.resize(1280, 790)
        self.setMinimumSize(1020, 680)
        self.setStyleSheet(
            f"QDialog{{background:{BG};color:{TEXT};}}QFrame{{background:{PANEL};}}"
            "QLabel{color:#d9dce6;}QListWidget,QLineEdit,QComboBox,QDoubleSpinBox{"
            "background:#11141a;color:#e5e7ed;border:1px solid #343a48;border-radius:5px;padding:5px;}"
            "QPushButton{background:#282d38;color:#e6e8ee;border:1px solid #3a414f;"
            "border-radius:6px;padding:7px 10px;}QPushButton:hover{border-color:#6f8cff;}"
            "QPushButton#primary{background:#315da8;border-color:#4a78c7;font-weight:bold;}"
            "QCheckBox{color:#c8ccd6;}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        toolbar = QHBoxLayout()
        title = QLabel("3D 导演台")
        title.setStyleSheet("font-size:16px;font-weight:bold;color:white;")
        toolbar.addWidget(title)
        toolbar.addWidget(QLabel("视图"))
        self.view_combo = QComboBox()
        self.view_combo.setMinimumWidth(190)
        toolbar.addWidget(self.view_combo)
        toolbar.addStretch()
        hint = QLabel("3D 决定几何和站位，生成模型只负责质感、身份和光影")
        hint.setStyleSheet(f"color:{MUTED};")
        toolbar.addWidget(hint)
        root.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.left = QFrame(); left_layout = QVBoxLayout(self.left)
        left_layout.addWidget(QLabel("舞台对象"))
        self.object_list = QListWidget()
        self.object_list.currentItemChanged.connect(self._list_selected)
        left_layout.addWidget(self.object_list, 1)
        add_actor = QPushButton("＋ 人物")
        add_actor.clicked.connect(self._add_actor)
        add_prop = QPushButton("＋ 几何道具")
        add_prop.clicked.connect(self._add_prop)
        add_camera = QPushButton("＋ 摄影机")
        add_camera.clicked.connect(self._add_camera)
        remove = QPushButton("删除选中")
        remove.clicked.connect(self._remove_selected)
        for button in (add_actor, add_prop, add_camera, remove):
            left_layout.addWidget(button)

        self.viewport = SceneStageViewport(self.stage)
        self.viewport.selectionChanged.connect(self._viewport_selected)
        self.viewport.stageChanged.connect(self._viewport_changed)

        self.right = QFrame(); right_layout = QVBoxLayout(self.right)
        right_layout.addWidget(QLabel("对象属性"))
        self.name_edit = QLineEdit()
        self.name_edit.editingFinished.connect(self._apply_fields)
        right_layout.addWidget(QLabel("名称")); right_layout.addWidget(self.name_edit)
        grid = QGridLayout()
        self.position_spins = []
        self.rotation_spins = []
        self.scale_spins = []
        for row_index, (title_text, target, limit, step) in enumerate((
                ("位置 XYZ（米）", self.position_spins, 1000, .1),
                ("旋转 XYZ（度）", self.rotation_spins, 3600, 5),
                ("缩放 XYZ", self.scale_spins, 100, .1))):
            grid.addWidget(QLabel(title_text), row_index * 2, 0, 1, 3)
            for axis in range(3):
                spin = QDoubleSpinBox(); spin.setRange(-limit if target is not self.scale_spins else .01, limit)
                spin.setDecimals(2); spin.setSingleStep(step)
                spin.valueChanged.connect(self._apply_fields)
                target.append(spin); grid.addWidget(spin, row_index * 2 + 1, axis)
        right_layout.addLayout(grid)
        right_layout.addWidget(QLabel("人物姿势"))
        self.pose_combo = QComboBox()
        self.pose_combo.addItems(["自然站立", "警觉站立", "迈步", "坐姿", "跪姿", "防御姿态", "伸手", "回头"])
        self.pose_combo.currentTextChanged.connect(self._apply_fields)
        right_layout.addWidget(self.pose_combo)
        self.locked_check = QCheckBox("锁定位置（固定设备）")
        self.locked_check.toggled.connect(self._apply_fields)
        right_layout.addWidget(self.locked_check)
        right_layout.addWidget(QLabel("摄影机 FOV"))
        self.fov_spin = QDoubleSpinBox(); self.fov_spin.setRange(10, 120); self.fov_spin.setValue(45)
        self.fov_spin.valueChanged.connect(self._apply_fields)
        right_layout.addWidget(self.fov_spin)
        right_layout.addWidget(QLabel("摄影机目标 XYZ"))
        target_grid = QHBoxLayout(); self.target_spins = []
        for _axis in range(3):
            spin = QDoubleSpinBox(); spin.setRange(-1000, 1000); spin.setDecimals(2); spin.setSingleStep(.1)
            spin.valueChanged.connect(self._apply_fields); self.target_spins.append(spin); target_grid.addWidget(spin)
        right_layout.addLayout(target_grid)
        right_layout.addStretch()
        status = QLabel("固定场景物默认锁定；解锁后才能移动。人物拖动只改变地面 X/Z，Y 高度在属性中调整。")
        status.setWordWrap(True); status.setStyleSheet(f"color:{MUTED};")
        right_layout.addWidget(status)

        splitter.addWidget(self.left); splitter.addWidget(self.viewport); splitter.addWidget(self.right)
        splitter.setSizes([210, 780, 260])
        root.addWidget(splitter, 1)

        actions = QHBoxLayout()
        self.capture_btn = QPushButton("保存摄影机构图快照")
        self.capture_btn.clicked.connect(self._capture)
        actions.addWidget(self.capture_btn)
        export_btn = QPushButton("另存构图图…")
        export_btn.clicked.connect(self._export_capture)
        actions.addWidget(export_btn)
        actions.addStretch()
        cancel = QPushButton("取消"); cancel.clicked.connect(self.reject)
        save = QPushButton("保存并绑定当前镜头"); save.setObjectName("primary"); save.clicked.connect(self._accept_stage)
        actions.addWidget(cancel); actions.addWidget(save)
        root.addLayout(actions)

        self.view_combo.currentIndexChanged.connect(self._view_changed)
        self._rebuild_lists()
        if self.object_list.count():
            self.object_list.setCurrentRow(0)

    def _all_rows(self):
        return list(self.stage.get("objects", [])) + list(self.stage.get("cameras", []))

    def _selected(self):
        selected_id = self.viewport.selected_id
        return next((row for row in self._all_rows()
                     if str(row.get("id") or "") == selected_id), None)

    def _rebuild_lists(self, select_id=""):
        select_id = str(select_id or self.viewport.selected_id or "")
        self._syncing = True
        self.object_list.clear()
        icons = {"actor": "人物", "fixture": "固定", "prop": "道具", "primitive": "几何"}
        for row in self.stage.get("objects", []):
            item = QListWidgetItem(f"{icons.get(row.get('kind'), '对象')} · {row.get('name')}")
            item.setData(Qt.ItemDataRole.UserRole, row.get("id")); self.object_list.addItem(item)
            if row.get("id") == select_id:
                self.object_list.setCurrentItem(item)
        for row in self.stage.get("cameras", []):
            item = QListWidgetItem(f"摄影机 · {row.get('name')}")
            item.setData(Qt.ItemDataRole.UserRole, row.get("id")); self.object_list.addItem(item)
            if row.get("id") == select_id:
                self.object_list.setCurrentItem(item)
        current_view = self.view_combo.currentData()
        self.view_combo.clear(); self.view_combo.addItem("导演自由视角", "")
        for camera in self.stage.get("cameras", []):
            self.view_combo.addItem(f"摄影机 · {camera.get('name')}", camera.get("id"))
        index = self.view_combo.findData(current_view)
        self.view_combo.setCurrentIndex(max(0, index))
        self._syncing = False
        self._load_fields()

    def _list_selected(self, current, _previous):
        if self._syncing:
            return
        object_id = str(current.data(Qt.ItemDataRole.UserRole) or "") if current else ""
        self.viewport.set_selected(object_id); self._load_fields()

    def _viewport_selected(self, object_id):
        self._syncing = True
        for index in range(self.object_list.count()):
            item = self.object_list.item(index)
            if str(item.data(Qt.ItemDataRole.UserRole) or "") == str(object_id or ""):
                self.object_list.setCurrentItem(item); break
        else:
            self.object_list.clearSelection()
        self._syncing = False; self._load_fields()

    def _view_changed(self):
        if not self._syncing:
            camera_id = str(self.view_combo.currentData() or "")
            self.viewport.set_view_camera(camera_id)
            if camera_id:
                self.stage["active_camera_id"] = camera_id

    def _load_fields(self):
        row = self._selected()
        self._syncing = True
        enabled = row is not None
        for widget in ([self.name_edit, self.pose_combo, self.locked_check, self.fov_spin] +
                       self.position_spins + self.rotation_spins + self.scale_spins + self.target_spins):
            widget.setEnabled(enabled)
        if row:
            self.name_edit.setText(str(row.get("name") or ""))
            transform = row.get("transform") or {}
            for spins, values in ((self.position_spins, transform.get("position") or [0, 0, 0]),
                                  (self.rotation_spins, transform.get("rotation") or [0, 0, 0]),
                                  (self.scale_spins, transform.get("scale") or [1, 1, 1])):
                for index, spin in enumerate(spins): spin.setValue(float(values[index]))
            is_camera = any(row is camera for camera in self.stage.get("cameras", []))
            is_actor = row.get("kind") == "actor"
            self.pose_combo.setEnabled(is_actor)
            self.pose_combo.setCurrentText(str(row.get("pose") or "自然站立"))
            self.locked_check.setChecked(bool(row.get("locked")))
            self.fov_spin.setEnabled(is_camera); self.fov_spin.setValue(float(row.get("fov") or 45))
            target = row.get("target") or [0, 1, 0]
            for index, spin in enumerate(self.target_spins):
                spin.setEnabled(is_camera); spin.setValue(float(target[index]))
        self._syncing = False

    def _apply_fields(self, *_):
        if self._syncing:
            return
        row = self._selected()
        if not row:
            return
        row["name"] = self.name_edit.text().strip() or row.get("name") or "对象"
        transform = row.setdefault("transform", {})
        transform["position"] = [round(spin.value(), 3) for spin in self.position_spins]
        transform["rotation"] = [round(spin.value(), 3) for spin in self.rotation_spins]
        transform["scale"] = [round(max(.01, spin.value()), 3) for spin in self.scale_spins]
        row["locked"] = self.locked_check.isChecked()
        if row.get("kind") == "actor": row["pose"] = self.pose_combo.currentText()
        if any(row is camera for camera in self.stage.get("cameras", [])):
            row["fov"] = round(self.fov_spin.value(), 2)
            row["target"] = [round(spin.value(), 3) for spin in self.target_spins]
        self.viewport.update()

    def _viewport_changed(self):
        self._load_fields()

    def _add_actor(self):
        name, ok = QInputDialog.getText(self, "添加人物", "人物名称：", text="人物")
        if not ok or not name.strip(): return
        from ai.scene_stage import _normalize_object
        row = _normalize_object({"name": name.strip(), "kind": "actor", "position": [0, 0, 0],
                                 "color": "#f0aa65"}, len(self.stage["objects"]))
        self.stage["objects"].append(row); self.viewport.set_selected(row["id"])
        self._rebuild_lists(row["id"]); self.viewport.update()

    def _add_prop(self):
        from ai.scene_stage import _normalize_object
        row = _normalize_object({"name": "几何道具", "kind": "primitive", "position": [0, .5, 0],
                                 "scale": [1, 1, 1], "color": "#8f77d8"}, len(self.stage["objects"]))
        self.stage["objects"].append(row); self.viewport.set_selected(row["id"])
        self._rebuild_lists(row["id"]); self.viewport.update()

    def _add_camera(self):
        from ai.scene_stage import _normalize_camera
        row = _normalize_camera({"name": f"摄影机 {len(self.stage['cameras']) + 1}",
                                 "position": [0, 1.65, 6], "target": [0, 1.1, 0]},
                                len(self.stage["cameras"]))
        self.stage["cameras"].append(row); self.stage["active_camera_id"] = row["id"]
        self.viewport.set_selected(row["id"]); self._rebuild_lists(row["id"]); self.viewport.update()

    def _remove_selected(self):
        row = self._selected()
        if not row: return
        if row.get("locked"):
            QMessageBox.information(self, "对象已锁定", "固定设备默认不可删除；请先取消“锁定位置”。")
            return
        if row in self.stage.get("cameras", []) and len(self.stage.get("cameras", [])) <= 1:
            QMessageBox.information(self, "保留摄影机", "舞台至少需要一台摄影机。")
            return
        self.stage["objects"] = [value for value in self.stage.get("objects", []) if value is not row]
        self.stage["cameras"] = [value for value in self.stage.get("cameras", []) if value is not row]
        if self.stage.get("active_camera_id") == row.get("id"):
            self.stage["active_camera_id"] = self.stage["cameras"][0]["id"]
        self.viewport.set_selected(""); self._rebuild_lists(); self.viewport.update()

    def _capture_to(self, path):
        camera_id = str(self.view_combo.currentData() or self.stage.get("active_camera_id") or "")
        if not camera_id:
            camera_id = str((active_camera(self.stage) or {}).get("id") or "")
        self.stage["active_camera_id"] = camera_id
        old_view = self.viewport.view_camera_id
        self.viewport.set_view_camera(camera_id)
        ok = self.viewport.render_clean(path)
        self.viewport.set_view_camera(old_view)
        if ok:
            self.capture_path = str(path)
            self.stage = append_stage_capture(self.stage, self.capture_path)
            self.viewport.set_stage(self.stage)
        return ok

    def _capture(self):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = Path(self.output_dir) / f"{self.stage.get('id', 'stage')}_{stamp}.png"
        if self._capture_to(path):
            QMessageBox.information(self, "构图已保存", f"已保存摄影机视角控制图：\n{path}")
        else:
            QMessageBox.warning(self, "保存失败", "无法写入构图快照。")

    def _export_capture(self):
        path, _ = QFileDialog.getSaveFileName(self, "另存构图图", "3D_构图.png", "PNG 图片 (*.png)")
        if path and not self._capture_to(path):
            QMessageBox.warning(self, "保存失败", "无法写入选择的位置。")

    def _accept_stage(self):
        self._apply_fields()
        if not self.capture_path:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = Path(self.output_dir) / f"{self.stage.get('id', 'stage')}_{stamp}.png"
            self._capture_to(path)
        self.accept()
