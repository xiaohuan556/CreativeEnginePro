"""
replace_video_dialog.py — 替换视频片段对话框
参考 PR / 剪映替换功能：
  - 预览新视频
  - 底部缩略图条
  - 复用原视频效果（位置/缩放/旋转/速度/音量/静音）
  - 替换片段 / 取消
"""
from __future__ import annotations
import os
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QSlider, QWidget, QApplication
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap, QPainter, QColor, QPen

from core.edit_engine import VideoClip
from .widgets import CheckMarkBox


def _video_duration(path: str) -> float:
    """使用 cv2 获取视频时长（秒），失败返回 0"""
    try:
        import cv2
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        cap.release()
        return frames / fps if fps > 0 else 0.0
    except Exception:
        return 0.0


def _frame_at(path: str, t: float):
    """读取视频在 t 秒处的帧，返回 QPixmap 或 None"""
    try:
        import cv2
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ret, frame = cap.read()
        cap.release()
        if not ret:
            return None
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = frame_rgb.shape
        qimg = QImage(frame_rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
        return QPixmap.fromImage(qimg)
    except Exception:
        return None


class _ThumbnailStrip(QWidget):
    """简单缩略图条：显示视频缩略图、可选范围、当前播放头"""
    seek_requested = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thumbs: list[QPixmap] = []
        self._duration = 0.0
        self._playhead = 0.0
        self._range_start = 0.0
        self._range_end = 0.0
        self.setFixedHeight(54)
        self.setStyleSheet("background:#1a1a1a;")
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_thumbnails(self, thumbs: list[QPixmap], duration: float):
        self._thumbs = thumbs
        self._duration = duration
        self.update()

    def set_playhead(self, sec: float):
        self._playhead = sec
        self.update()

    def set_range(self, start: float, end: float):
        self._range_start = start
        self._range_end = end
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        painter.fillRect(self.rect(), QColor("#1a1a1a"))
        if not self._thumbs:
            painter.setPen(QColor("#555"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "无缩略图")
            painter.end()
            return
        count = len(self._thumbs)
        thumb_w = w / max(count, 1)
        for i, px in enumerate(self._thumbs):
            x = int(i * thumb_w)
            tw = int((i + 1) * thumb_w) - x
            painter.drawPixmap(x, 0, tw, h, px.scaled(tw, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))

        # 可选范围高亮
        if self._duration > 0 and self._range_end > self._range_start:
            sx = int((self._range_start / self._duration) * w)
            ex = int((self._range_end / self._duration) * w)
            painter.fillRect(sx, 0, ex - sx, h, QColor(0, 234, 255, 60))
            painter.setPen(QPen(QColor("#00eaff"), 1))
            painter.drawLine(sx, 0, sx, h)
            painter.drawLine(ex, 0, ex, h)

        # 当前播放头
        if self._duration > 0:
            x = int((self._playhead / self._duration) * w)
            painter.setPen(QPen(QColor("#ff6b6b"), 2))
            painter.drawLine(x, 0, x, h)
        painter.end()

    def mousePressEvent(self, e):
        if self._duration <= 0:
            return
        ratio = e.pos().x() / max(self.width(), 1)
        self.seek_requested.emit(ratio * self._duration)


class ReplaceVideoDialog(QDialog):
    """
    替换视频片段对话框。
    执行后可通过 new_path 和 keep_effects 读取用户选择。
    """

    def __init__(self, old_clip: VideoClip, parent=None):
        super().__init__(parent)
        self.old_clip = old_clip
        self.new_path = ""
        self.keep_effects = True

        self._cap = None
        self._fps = 30.0
        self._duration = 0.0
        self._playhead = 0.0
        self._trim_start = 0.0
        self._playing = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        self._setup_ui()
        # 不自动加载 — 由调用方通过 _load_video() 显式传入

    def _setup_ui(self):
        self.setWindowTitle("替换")
        self.setMinimumSize(720, 560)
        self.setStyleSheet(
            "QDialog { background:#1e1e1e; color:#ccc; }"
            "QLabel { color:#aaa; font-size:12px; }"
            "QPushButton { border-radius:4px; padding:6px 18px; font-size:12px; }"
            "QPushButton#primary { background:#3d8ef8; color:#fff; border:none; font-weight:bold; }"
            "QPushButton#primary:hover { background:#5aa0ff; }"
            "QPushButton#secondary { background:#252525; color:#aaa; border:1px solid #444; }"
            "QPushButton#secondary:hover { background:#333; color:#fff; }"
            "QPushButton#choose { background:#2a2a2a; color:#ccc; border:1px solid #444; }"
            "QPushButton#choose:hover { background:#333; }"
            "QCheckBox { color:#ccc; font-size:12px; }"
            "QCheckBox::indicator { width:16px; height:16px; }"
            "QSlider::groove:horizontal { height:4px; background:#333; border-radius:2px; }"
            "QSlider::handle:horizontal { width:12px; background:#00eaff; border-radius:6px; margin:-4px 0; }"
            "QSlider::sub-page:horizontal { background:#00eaff; border-radius:2px; }"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # 顶部提示
        hint = QLabel("选择新视频文件替换当前片段")
        hint.setStyleSheet("color:#888; font-size:12px;")
        root.addWidget(hint)

        # 文件选择行
        file_row = QHBoxLayout()
        self._path_lbl = QLabel(self.old_clip.source_path.split('/')[-1].split(chr(92))[-1])
        self._path_lbl.setStyleSheet("color:#ccc;")
        self._path_lbl.setWordWrap(True)
        choose_btn = QPushButton("选择文件")
        choose_btn.setObjectName("choose")
        choose_btn.clicked.connect(self._choose_file)
        file_row.addWidget(self._path_lbl, 1)
        file_row.addWidget(choose_btn)
        root.addLayout(file_row)

        # 时长状态提示
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color:#ff6b6b; font-size:11px;")
        root.addWidget(self._status_lbl)

        # 预览区
        self._preview = QLabel()
        self._preview.setMinimumSize(560, 315)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setStyleSheet("background:#0a0a0a;")
        root.addWidget(self._preview, 1)

        # 播放控制
        ctrl = QHBoxLayout()
        self._play_btn = QPushButton("▶")
        self._play_btn.setFixedWidth(36)
        self._play_btn.setObjectName("choose")
        self._play_btn.clicked.connect(self._toggle_play)
        self._time_lbl = QLabel("00:00:00 / 00:00:00")
        self._time_lbl.setStyleSheet("color:#888; font-family:Consolas,monospace;")
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 0)
        self._slider.sliderReleased.connect(self._on_slider_released)
        ctrl.addWidget(self._play_btn)
        ctrl.addWidget(self._time_lbl)
        ctrl.addWidget(self._slider, 1)
        root.addLayout(ctrl)

        # 缩略图条
        self._strip = _ThumbnailStrip()
        self._strip.seek_requested.connect(self._seek)
        root.addWidget(self._strip)

        # 截取范围控制
        range_hint = QLabel("在上方缩略图条点击可预览；拖动滑块选择替换起点")
        range_hint.setStyleSheet("color:#888; font-size:11px;")
        root.addWidget(range_hint)

        range_row = QHBoxLayout()
        self._trim_lbl = QLabel("替换起点：0.00s")
        self._trim_lbl.setStyleSheet("color:#ccc; font-family:Consolas,monospace;")
        self._trim_lbl.setFixedWidth(140)
        self._trim_slider = QSlider(Qt.Orientation.Horizontal)
        self._trim_slider.setRange(0, 0)
        self._trim_slider.valueChanged.connect(self._on_trim_changed)
        range_row.addWidget(self._trim_lbl)
        range_row.addWidget(self._trim_slider, 1)
        root.addLayout(range_row)

        self._range_lbl = QLabel("")
        self._range_lbl.setStyleSheet("color:#888; font-size:11px;")
        root.addWidget(self._range_lbl)

        # 底部按钮行
        bottom = QHBoxLayout()
        self._keep_cb = CheckMarkBox("复用原视频效果")
        self._keep_cb.setChecked(True)
        self._keep_cb.setToolTip("保留原片段的位置、缩放、旋转、速度、音量等效果")
        bottom.addWidget(self._keep_cb)
        bottom.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("secondary")
        cancel_btn.clicked.connect(self.reject)
        self._ok_btn = QPushButton("替换片段")
        self._ok_btn.setObjectName("primary")
        self._ok_btn.setDefault(True)
        self._ok_btn.clicked.connect(self._do_replace)
        bottom.addWidget(cancel_btn)
        bottom.addWidget(self._ok_btn)
        root.addLayout(bottom)

    def _choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择替换视频", "",
            "视频文件 (*.mp4 *.mov *.avi *.mkv *.flv *.wmv *.webm);;所有文件 (*.*)"
        )
        if path:
            self._load_video(path)

    def _load_video(self, path: str):
        self.new_path = path
        self._path_lbl.setText(Path(path).name)
        self._duration = _video_duration(path)
        old_dur = self.old_clip.duration
        if self._duration <= 0:
            self._duration = getattr(self.old_clip, "source_duration", 0.0) or 0.0

        if self._duration < old_dur:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "无法替换",
                f"新视频时长（{self._duration:.1f}s）短于原片段（{old_dur:.1f}s），无法替换"
            )
            self._path_lbl.setText("")
            self.new_path = ""
            self._duration = 0.0
            self._ok_btn.setEnabled(False)
            self._trim_slider.setRange(0, 0)
            self._strip.set_range(0, 0)
            return
        else:
            self._status_lbl.setText(
                f"原片段：{old_dur:.2f}s  |  新视频：{self._duration:.2f}s"
            )
            self._status_lbl.setStyleSheet("color:#5cdb5c; font-size:11px;")
            self._ok_btn.setEnabled(True)
            max_start = max(0.0, self._duration - old_dur)
            self._trim_start = 0.0
            self._trim_slider.setRange(0, int(max_start * 1000))
            self._trim_slider.setValue(0)
            self._strip.set_range(0, old_dur)
            self._range_lbl.setText(
                f"将使用新视频的 0.00s ~ {old_dur:.2f}s 替换原片段"
            )

        self._fps = 30.0
        self._playhead = self._trim_start
        self._slider.setRange(0, max(0, int(self._duration * 1000)))
        self._update_time()
        self._update_preview()
        # 缩略图后台生成，避免阻塞 UI
        class _ReplThumbWorker(QThread):
            def __init__(self, path, dur):
                super().__init__()
                self._path = path; self._dur = dur
                self._thumbs = []
            def run(self):
                try:
                    import cv2
                    cap = cv2.VideoCapture(self._path)
                    fps = cap.get(cv2.CAP_PROP_FPS) or 30
                    dur = self._dur
                    count = max(5, min(30, int(dur)))
                    for i in range(count):
                        t = i * dur / max(count - 1, 1)
                        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
                        ret, frame = cap.read()
                        if ret:
                            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                            h, w, ch = frame_rgb.shape
                            qimg = QImage(frame_rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
                            px = QPixmap.fromImage(qimg).scaled(
                                80, 45, Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation)
                            self._thumbs.append(px)
                    cap.release()
                except Exception:
                    pass
        self._thumb_worker = _ReplThumbWorker(path, self._duration)
        self._thumb_worker.finished.connect(self._on_thumbs_ready)
        self._thumb_worker.start()

    def _on_thumbs_ready(self):
        if self._thumb_worker and hasattr(self._thumb_worker, '_thumbs'):
            self._strip.set_thumbnails(self._thumb_worker._thumbs, self._duration)
            self._strip.set_playhead(self._playhead)
        # 交给 Qt 在下一事件循环安全析构，防止线程 finishing 阶段被 Python GC 提前销毁
        if self._thumb_worker is not None:
            self._thumb_worker.deleteLater()
            self._thumb_worker = None

    def _generate_thumbnails(self, path: str):
        thumbs = []
        try:
            import cv2
            cap = cv2.VideoCapture(path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            dur = self._duration
            count = max(5, min(30, int(dur)))
            for i in range(count):
                t = i * dur / max(count - 1, 1)
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
                ret, frame = cap.read()
                if ret:
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    h, w, ch = frame_rgb.shape
                    qimg = QImage(frame_rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
                    px = QPixmap.fromImage(qimg).scaled(
                        80, 45,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation
                    )
                    thumbs.append(px)
            cap.release()
        except Exception:
            pass
        self._strip.set_thumbnails(thumbs, self._duration)
        self._strip.set_playhead(self._playhead)

    def _update_preview(self):
        if not self.new_path:
            return
        px = _frame_at(self.new_path, self._playhead)
        if px is None:
            self._preview.setText("无法加载帧")
            return
        scaled = px.scaled(
            self._preview.width(), self._preview.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self._preview.setPixmap(scaled)

    def _update_time(self):
        cur = self._format_time(self._playhead)
        total = self._format_time(self._duration)
        self._time_lbl.setText(f"{cur} / {total}")
        self._slider.blockSignals(True)
        self._slider.setValue(int(self._playhead * 1000))
        self._slider.blockSignals(False)

    @staticmethod
    def _format_time(sec: float) -> str:
        sec = max(0.0, sec)
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _seek(self, sec: float):
        self._playhead = max(0.0, min(self._duration, sec))
        self._update_preview()
        self._update_time()
        self._strip.set_playhead(self._playhead)

    def _toggle_play(self):
        if self._playing:
            self._timer.stop()
            self._playing = False
            self._play_btn.setText("▶")
        else:
            if self._duration <= 0:
                return
            self._timer.start(33)
            self._playing = True
            self._play_btn.setText("⏸")

    def _tick(self):
        if self._duration <= 0:
            self._toggle_play()
            return
        old_dur = self.old_clip.duration
        end = min(self._duration, self._trim_start + old_dur)
        self._playhead += 0.033
        if self._playhead >= end:
            self._playhead = self._trim_start
        self._update_preview()
        self._update_time()
        self._strip.set_playhead(self._playhead)

    def _on_slider_released(self):
        self._seek(self._slider.value() / 1000.0)

    def _on_trim_changed(self, value_ms: int):
        if self._duration <= 0:
            return
        old_dur = self.old_clip.duration
        self._trim_start = value_ms / 1000.0
        end = min(self._duration, self._trim_start + old_dur)
        self._trim_lbl.setText(f"替换起点：{self._trim_start:.2f}s")
        self._range_lbl.setText(
            f"将使用新视频的 {self._trim_start:.2f}s ~ {end:.2f}s 替换原片段"
        )
        self._strip.set_range(self._trim_start, end)
        self._seek(self._trim_start)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_preview()

    def _do_replace(self):
        if not self.new_path or not os.path.exists(self.new_path):
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "提示", "请先选择一个有效的视频文件")
            return
        if self._duration < self.old_clip.duration:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "提示",
                f"新视频时长（{self._duration:.2f}s）短于原片段（{self.old_clip.duration:.2f}s），无法替换。")
            return
        self.keep_effects = self._keep_cb.isChecked()
        self.accept()

    def closeEvent(self, event):
        self._timer.stop()
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
        super().closeEvent(event)
