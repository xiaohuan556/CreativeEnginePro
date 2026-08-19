"""精确元素植入：静态图片四角贴图 + 视频平面跟踪逐帧贴图。"""
from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import cv2
import numpy as np
from PyQt6.QtCore import Qt, QPointF, pyqtSignal, QThread
from PyQt6.QtGui import QImage, QPainter, QPen, QColor, QPixmap, QBrush
from PyQt6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QDialogButtonBox, QProgressBar, QMessageBox,
)


def _read_cv(path: str, flags=cv2.IMREAD_UNCHANGED):
    data = np.fromfile(path, dtype=np.uint8)
    image = cv2.imdecode(data, flags)
    if image is None:
        raise ValueError(f"无法读取图片：{path}")
    return image


def _write_cv(path: str, image) -> str:
    suffix = Path(path).suffix or ".png"
    ok, encoded = cv2.imencode(suffix, image)
    if not ok:
        raise ValueError("无法编码植入结果")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    encoded.tofile(path)
    return path


def _element_rgba(path: str):
    image = _read_cv(path, cv2.IMREAD_UNCHANGED)
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGRA)
    elif image.shape[2] == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    return image


def _composite_array(base_bgr, element_bgra, corners):
    h, w = base_bgr.shape[:2]
    eh, ew = element_bgra.shape[:2]
    src = np.float32([[0, 0], [ew - 1, 0], [ew - 1, eh - 1], [0, eh - 1]])
    dst = np.float32(corners)
    matrix = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(
        element_bgra[:, :, :3], matrix, (w, h), flags=cv2.INTER_LANCZOS4,
        borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    alpha = cv2.warpPerspective(
        element_bgra[:, :, 3], matrix, (w, h), flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT, borderValue=0).astype(np.float32) / 255.0
    alpha = alpha[:, :, None]
    out = warped.astype(np.float32) * alpha + base_bgr.astype(np.float32) * (1.0 - alpha)
    return np.clip(out, 0, 255).astype(np.uint8)


def compose_element_image(base_path: str, element_path: str, corners, output_path: str) -> str:
    base = _read_cv(base_path, cv2.IMREAD_UNCHANGED)
    base_alpha = None
    if base.ndim == 2:
        base = cv2.cvtColor(base, cv2.COLOR_GRAY2BGR)
    elif base.shape[2] == 4:
        base_alpha = base[:, :, 3].copy()
        base = base[:, :, :3]
    result = _composite_array(base, _element_rgba(element_path), corners)
    if base_alpha is not None:
        result = np.dstack([result, base_alpha])
    return _write_cv(output_path, result)


class CornerCanvas(QWidget):
    pointsChanged = pyqtSignal()

    def __init__(self, image_path: str, parent=None):
        super().__init__(parent)
        self._original_path = image_path
        self._display_path = image_path
        self._image = QImage(image_path)
        if self._image.isNull():
            raise ValueError(f"无法打开画面：{image_path}")
        self.points: list[QPointF] = []
        self._drag_index = -1
        self.setMinimumSize(720, 430)
        self.setMouseTracking(True)
        self.setStyleSheet("background:#09090b;border:1px solid #303038;")

    def set_display_image(self, path: str):
        image = QImage(path)
        if not image.isNull():
            self._display_path = path
            self._image = image
            self.update()

    def reset_display(self):
        self.set_display_image(self._original_path)

    def reset_points(self):
        self.points.clear()
        self._drag_index = -1
        self.pointsChanged.emit()
        self.update()

    def _fit_rect(self):
        iw, ih = self._image.width(), self._image.height()
        scale = min(self.width() / max(1, iw), self.height() / max(1, ih))
        dw, dh = iw * scale, ih * scale
        return (self.width() - dw) / 2, (self.height() - dh) / 2, dw, dh, scale

    def _to_widget(self, point: QPointF):
        x, y, _w, _h, scale = self._fit_rect()
        return QPointF(x + point.x() * scale, y + point.y() * scale)

    def _to_image(self, point: QPointF):
        x, y, w, h, scale = self._fit_rect()
        px = max(0.0, min(float(self._image.width() - 1), (point.x() - x) / scale))
        py = max(0.0, min(float(self._image.height() - 1), (point.y() - y) / scale))
        if not (x <= point.x() <= x + w and y <= point.y() <= y + h):
            return None
        return QPointF(px, py)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#09090b"))
        x, y, w, h, _scale = self._fit_rect()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(QPointF(x, y), self._image.scaled(
            int(w), int(h), Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        widget_points = [self._to_widget(point) for point in self.points]
        if len(widget_points) >= 2:
            painter.setPen(QPen(QColor("#58d8f0"), 2))
            for index in range(len(widget_points) - 1):
                painter.drawLine(widget_points[index], widget_points[index + 1])
            if len(widget_points) == 4:
                painter.drawLine(widget_points[-1], widget_points[0])
        labels = ("左上", "右上", "右下", "左下")
        for index, point in enumerate(widget_points):
            painter.setPen(QPen(QColor("#ffffff"), 1))
            painter.setBrush(QBrush(QColor("#2faac2")))
            painter.drawEllipse(point, 7, 7)
            painter.drawText(point + QPointF(9, -8), labels[index])

    def mousePressEvent(self, event):
        pos = event.position()
        for index, point in enumerate(self.points):
            wp = self._to_widget(point)
            if abs(wp.x() - pos.x()) <= 13 and abs(wp.y() - pos.y()) <= 13:
                self._drag_index = index
                return
        image_point = self._to_image(pos)
        if image_point is not None and len(self.points) < 4:
            self.points.append(image_point)
            self.pointsChanged.emit()
            self.update()

    def mouseMoveEvent(self, event):
        if self._drag_index < 0:
            return
        image_point = self._to_image(event.position())
        if image_point is not None:
            self.points[self._drag_index] = image_point
            self.pointsChanged.emit()
            self.update()

    def mouseReleaseEvent(self, _event):
        self._drag_index = -1

    def corner_array(self):
        return np.float32([[point.x(), point.y()] for point in self.points])


def _output_folder(subfolder="ai_images") -> Path:
    try:
        from config import OUTPUT_DIR
        folder = Path(OUTPUT_DIR) / subfolder
    except Exception:
        folder = Path(__file__).parent.parent / "work_temp" / subfolder
    folder.mkdir(parents=True, exist_ok=True)
    return folder


class ImageElementCompositorDialog(QDialog):
    def __init__(self, base_path: str, element_path: str, element_name="元素", parent=None):
        super().__init__(parent)
        self.base_path = base_path
        self.element_path = element_path
        self.element_name = element_name
        self.result_path = ""
        self._preview_path = ""
        self.setWindowTitle(f"精确植入 · {element_name}")
        self.resize(980, 700)
        self.setStyleSheet(
            "QDialog{background:#141417;color:#eee;} QLabel{color:#aaa;}"
            "QPushButton{background:#29292f;color:#ddd;border:1px solid #41414a;"
            "border-radius:5px;padding:6px 12px;}QPushButton:hover{background:#35353d;}"
        )
        root = QVBoxLayout(self)
        title = QLabel("依次点击承载区域四个角：左上 → 右上 → 右下 → 左下；拖动圆点可修正")
        title.setStyleSheet("color:#8ee4f3;font-size:12px;font-weight:bold;")
        root.addWidget(title)
        self.canvas = CornerCanvas(base_path)
        root.addWidget(self.canvas, 1)
        controls = QHBoxLayout()
        reset = QPushButton("重置四角")
        reset.clicked.connect(self._reset)
        controls.addWidget(reset)
        preview = QPushButton("预览精确植入")
        preview.clicked.connect(self._preview)
        controls.addWidget(preview)
        controls.addStretch()
        self.status = QLabel("等待选择四角")
        controls.addWidget(self.status)
        root.addLayout(controls)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存植入结果")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.canvas.pointsChanged.connect(self._on_points)

    def _on_points(self):
        self.canvas.reset_display()
        self.status.setText(f"已选择 {len(self.canvas.points)} / 4 个角")

    def _reset(self):
        self.canvas.reset_display()
        self.canvas.reset_points()

    def _make_result(self, final=False):
        if len(self.canvas.points) != 4:
            QMessageBox.information(self, "精确植入", "请先按顺序选择完整的四个角。")
            return ""
        if final:
            out = _output_folder("ai_images") / f"element_exact_{uuid.uuid4().hex[:10]}.png"
        else:
            out = _output_folder("element_previews") / f"preview_{uuid.uuid4().hex[:8]}.png"
        return compose_element_image(
            self.base_path, self.element_path, self.canvas.corner_array(), str(out))

    def _preview(self):
        try:
            path = self._make_result(False)
            if path:
                self._preview_path = path
                self.canvas.set_display_image(path)
                self.status.setText("预览已更新；拖动角点可继续修正")
        except Exception as error:
            QMessageBox.warning(self, "预览失败", str(error))

    def _save(self):
        try:
            path = self._make_result(True)
            if path:
                self.result_path = path
                self.accept()
        except Exception as error:
            QMessageBox.warning(self, "保存失败", str(error))


class _VideoTrackWorker(QThread):
    progress = pyqtSignal(int)
    completed = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, video_path, element_path, corners, output_path):
        super().__init__()
        self.video_path = video_path
        self.element_path = element_path
        self.corners = np.float32(corners)
        self.output_path = output_path

    @staticmethod
    def _features(gray, quad):
        mask = np.zeros(gray.shape, np.uint8)
        cv2.fillConvexPoly(mask, np.int32(quad), 255)
        return cv2.goodFeaturesToTrack(
            gray, mask=mask, maxCorners=120, qualityLevel=0.01,
            minDistance=6, blockSize=7)

    def run(self):
        cap = cv2.VideoCapture(self.video_path)
        try:
            if not cap.isOpened():
                raise ValueError("无法打开待处理视频")
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            total = max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
            ok, first = cap.read()
            if not ok:
                raise ValueError("无法读取视频首帧")
            element = _element_rgba(self.element_path)
            silent = str(Path(self.output_path).with_stem(Path(self.output_path).stem + "_silent"))
            writer = cv2.VideoWriter(
                silent, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
            if not writer.isOpened():
                raise ValueError("无法创建视频输出文件")

            quad = self.corners.copy()
            prev_gray = cv2.cvtColor(first, cv2.COLOR_BGR2GRAY)
            features = self._features(prev_gray, quad)
            writer.write(_composite_array(first, element, quad))
            frame_index = 1
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                if features is not None and len(features) >= 4:
                    tracked, status, _err = cv2.calcOpticalFlowPyrLK(
                        prev_gray, gray, features, None,
                        winSize=(25, 25), maxLevel=3,
                        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))
                    good_old = features[status.flatten() == 1]
                    good_new = tracked[status.flatten() == 1]
                    if len(good_old) >= 4:
                        matrix, _mask = cv2.findHomography(
                            good_old.reshape(-1, 2), good_new.reshape(-1, 2), cv2.RANSAC, 3.0)
                        if matrix is not None:
                            proposed = cv2.perspectiveTransform(
                                quad.reshape(1, 4, 2), matrix).reshape(4, 2)
                            if (np.isfinite(proposed).all() and
                                    proposed[:, 0].min() > -width and proposed[:, 0].max() < width * 2 and
                                    proposed[:, 1].min() > -height and proposed[:, 1].max() < height * 2):
                                quad = quad * 0.25 + proposed * 0.75
                        features = good_new.reshape(-1, 1, 2)
                if frame_index % 12 == 0 or features is None or len(features) < 12:
                    refreshed = self._features(gray, quad)
                    if refreshed is not None and len(refreshed) >= 4:
                        features = refreshed
                writer.write(_composite_array(frame, element, quad))
                prev_gray = gray
                frame_index += 1
                if frame_index % 3 == 0:
                    self.progress.emit(min(96, int(frame_index / total * 96)))
            writer.release()
            cap.release()

            final_path = self.output_path
            try:
                from utils.ffmpeg_utils import get_ffmpeg_path
                ffmpeg = get_ffmpeg_path()
                command = [
                    ffmpeg, "-y", "-i", silent, "-i", self.video_path,
                    "-map", "0:v:0", "-map", "1:a?", "-c:v", "copy",
                    "-c:a", "aac", "-shortest", final_path,
                ]
                result = subprocess.run(command, capture_output=True, creationflags=0x08000000)
                if result.returncode != 0:
                    raise RuntimeError(result.stderr.decode("utf-8", "ignore")[-500:])
                Path(silent).unlink(missing_ok=True)
            except Exception:
                shutil.move(silent, final_path)
            self.progress.emit(100)
            self.completed.emit(final_path)
        except Exception as error:
            try:
                cap.release()
            except Exception:
                pass
            self.failed.emit(str(error))


class VideoElementTrackerDialog(QDialog):
    def __init__(self, video_path: str, element_path: str, element_name="元素", parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self.element_path = element_path
        self.element_name = element_name
        self.result_path = ""
        self._worker = None
        cap = cv2.VideoCapture(video_path)
        ok, first = cap.read()
        cap.release()
        if not ok:
            raise ValueError("无法读取视频首帧")
        first_path = _output_folder("element_previews") / f"first_{uuid.uuid4().hex[:8]}.png"
        _write_cv(str(first_path), first)
        self.first_path = str(first_path)
        self.setWindowTitle(f"平面跟踪植入 · {element_name}")
        self.resize(980, 720)
        self.setStyleSheet(
            "QDialog{background:#141417;color:#eee;} QLabel{color:#aaa;}"
            "QPushButton{background:#29292f;color:#ddd;border:1px solid #41414a;"
            "border-radius:5px;padding:6px 12px;}QPushButton:hover{background:#35353d;}"
        )
        root = QVBoxLayout(self)
        title = QLabel("在首帧依次点击跟踪平面的左上 → 右上 → 右下 → 左下")
        title.setStyleSheet("color:#8ee4f3;font-size:12px;font-weight:bold;")
        root.addWidget(title)
        self.canvas = CornerCanvas(self.first_path)
        root.addWidget(self.canvas, 1)
        row = QHBoxLayout()
        reset = QPushButton("重置四角")
        reset.clicked.connect(self.canvas.reset_points)
        row.addWidget(reset)
        self.start_btn = QPushButton("开始跟踪并植入")
        self.start_btn.clicked.connect(self._start)
        row.addWidget(self.start_btn)
        row.addStretch()
        self.status = QLabel("等待选择四角")
        row.addWidget(self.status)
        root.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        root.addWidget(self.progress)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        close.rejected.connect(self.reject)
        root.addWidget(close)
        self.canvas.pointsChanged.connect(
            lambda: self.status.setText(f"已选择 {len(self.canvas.points)} / 4 个角"))

    def _start(self):
        if len(self.canvas.points) != 4:
            QMessageBox.information(self, "平面跟踪", "请先选择完整的四个角。")
            return
        output = _output_folder("ai_videos") / f"element_tracked_{uuid.uuid4().hex[:10]}.mp4"
        self.start_btn.setEnabled(False)
        self.status.setText("正在跟踪平面并逐帧植入…")
        self._worker = _VideoTrackWorker(
            self.video_path, self.element_path,
            self.canvas.corner_array(), str(output))
        self._worker.progress.connect(self.progress.setValue)
        self._worker.completed.connect(self._done)
        self._worker.failed.connect(self._failed)
        self._worker.start()

    def _done(self, path):
        self.result_path = path
        self.status.setText("跟踪植入完成")
        self.accept()

    def _failed(self, error):
        self.start_btn.setEnabled(True)
        self.status.setText("跟踪失败")
        QMessageBox.warning(self, "跟踪失败", error)
