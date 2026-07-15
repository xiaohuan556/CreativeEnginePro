"""
media_preview.py — 素材库双击预览弹窗
支持视频播放（逐帧 + 音频）、音频播放、图片显示
"""
from __future__ import annotations
import os
import logging
import subprocess
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QSlider, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap, QImage, QFont

import cv2


class MediaPreviewDialog(QDialog):
    """素材库双击预览弹窗：播放视频/音频/图片，不加入时间线"""

    def __init__(self, file_path: str, media_type: str, parent=None):
        super().__init__(parent)
        self._file_path = file_path
        self._media_type = media_type
        self._cap = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._playing = False
        self._current_sec = 0.0
        self._fps = 30.0
        self._total_frames = 0
        self._duration_sec = 0.0
        self._audio_proc = None
        self._seek_sliding = False  # 用户拖拽滑块时暂停计时器更新
        self._cached_frame = None   # 原始 QPixmap 缓存（缩放用）
        self._src_pix = None        # 图片原始 QPixmap

        self._setup_ui()
        self._load_media()

        # 确保关闭时停止所有资源
        self.finished.connect(self._cleanup)

    def _setup_ui(self):
        fname = os.path.basename(self._file_path)
        self.setWindowTitle(f"预览 — {fname}")
        self.setMinimumSize(560, 400)
        self.resize(720, 480)
        self.setStyleSheet("""
            QDialog { background: #1a1a1a; }
            QLabel { color: #ccc; }
            QPushButton {
                background: #333; color: #ccc; border: 1px solid #555;
                border-radius: 4px; padding: 6px 16px; font-size: 13px;
            }
            QPushButton:hover { background: #3d8ef8; border-color: #3d8ef8; color: #fff; }
            QPushButton:disabled { background: #222; color: #555; border-color: #333; }
            QSlider::groove:horizontal {
                background: #333; height: 6px; border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #3d8ef8; width: 14px; height: 14px;
                margin: -4px 0; border-radius: 7px;
            }
            QSlider::sub-page:horizontal { background: #3d8ef8; border-radius: 3px; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # 视频/图片显示区
        self._video_label = QLabel("加载中…")
        self._video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._video_label.setMinimumHeight(200)
        self._video_label.setStyleSheet(
            "QLabel { background: #111; border: 1px solid #333; border-radius: 4px; }")
        layout.addWidget(self._video_label, 1)

        # 音频信息标签（仅音频文件可见）
        self._audio_label = QLabel()
        self._audio_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._audio_label.setWordWrap(True)
        self._audio_label.setStyleSheet("QLabel { font-size: 14px; color: #aaa; }")
        self._audio_label.hide()
        layout.addWidget(self._audio_label)

        # 进度条行
        row_slider = QHBoxLayout()
        self._time_label = QLabel("00:00")
        self._time_label.setFixedWidth(45)
        self._time_label.setStyleSheet("QLabel { color: #888; font-size: 12px; }")
        row_slider.addWidget(self._time_label)

        self._seek_slider = QSlider(Qt.Orientation.Horizontal)
        self._seek_slider.setRange(0, 1000)  # 0–1000 映射到时长
        self._seek_slider.setValue(0)
        self._seek_slider.sliderPressed.connect(self._on_seek_pressed)
        self._seek_slider.sliderReleased.connect(self._on_seek_released)
        self._seek_slider.sliderMoved.connect(self._on_seek_moved)
        row_slider.addWidget(self._seek_slider, 1)

        self._dur_label = QLabel("00:00")
        self._dur_label.setFixedWidth(45)
        self._dur_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._dur_label.setStyleSheet("QLabel { color: #888; font-size: 12px; }")
        row_slider.addWidget(self._dur_label)
        layout.addLayout(row_slider)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._btn_rewind = QPushButton("⏪")
        self._btn_rewind.setToolTip("后退 5 秒")
        self._btn_rewind.setFixedWidth(40)
        self._btn_rewind.clicked.connect(self._rewind)
        btn_row.addWidget(self._btn_rewind)

        self._btn_play = QPushButton("▶  播放")
        self._btn_play.setToolTip("播放 / 暂停")
        self._btn_play.clicked.connect(self._toggle_play)
        btn_row.addWidget(self._btn_play)

        self._btn_stop = QPushButton("⏹  停止")
        self._btn_stop.setToolTip("停止")
        self._btn_stop.clicked.connect(self._stop)
        btn_row.addWidget(self._btn_stop)

        self._btn_forward = QPushButton("⏩")
        self._btn_forward.setToolTip("前进 5 秒")
        self._btn_forward.setFixedWidth(40)
        self._btn_forward.clicked.connect(self._forward)
        btn_row.addWidget(self._btn_forward)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 文件名行
        info_row = QHBoxLayout()
        self._info_label = QLabel(fname)
        self._info_label.setStyleSheet("QLabel { color: #666; font-size: 11px; }")
        self._info_label.setWordWrap(False)
        info_row.addWidget(self._info_label)
        info_row.addStretch()
        layout.addLayout(info_row)

    def _load_media(self):
        """加载媒体文件元数据"""
        if self._media_type == "video":
            self._cap = cv2.VideoCapture(self._file_path)
            if not self._cap.isOpened():
                self._video_label.setText("❌ 无法打开视频文件")
                self._btn_play.setEnabled(False)
                return
            self._fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
            self._total_frames = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            self._duration_sec = self._total_frames / max(self._fps, 0.01)
            self._dur_label.setText(self._fmt_time(self._duration_sec))
            self._show_first_frame()
            # 缓存首帧 QPixmap 用于窗口缩放时重绘
            self._cached_frame = self._video_label.pixmap()

        elif self._media_type == "audio":
            self._video_label.hide()
            from ui.media_library import _get_duration
            self._duration_sec = _get_duration(self._file_path, "audio")
            self._dur_label.setText(self._fmt_time(self._duration_sec))
            self._audio_label.setText(
                f"🎵  {os.path.basename(self._file_path)}\n\n"
                f"时长：{self._fmt_time(self._duration_sec)}"
            )
            self._audio_label.show()

        elif self._media_type == "image":
            self._duration_sec = 0.0
            self._seek_slider.setEnabled(False)
            self._src_pix = QPixmap(self._file_path)
            if not self._src_pix.isNull():
                self._resize_image()
            else:
                self._video_label.setText("❌ 无法加载图片")
            self._btn_play.setEnabled(False)
            self._btn_rewind.setEnabled(False)
            self._btn_forward.setEnabled(False)

    def _resize_image(self):
        """缩放图片以适应窗口"""
        if hasattr(self, '_src_pix') and self._src_pix and not self._src_pix.isNull():
            self._video_label.setPixmap(self._src_pix.scaled(
                self._video_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            ))

    def _show_first_frame(self):
        """显示视频第一帧"""
        if not self._cap:
            return
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ret, frame = self._cap.read()
        if ret:
            self._paint_frame(frame)

    def _toggle_play(self):
        if self._playing:
            self._pause()
        else:
            self._play()

    def _play(self):
        if self._media_type == "video":
            if not self._cap or not self._cap.isOpened():
                return
            self._playing = True
            self._btn_play.setText("⏸  暂停")
            self._timer.start(int(1000.0 / max(self._fps, 0.01)))
            self._start_audio()

        elif self._media_type == "audio":
            self._playing = True
            self._btn_play.setText("⏸  暂停")
            self._start_audio()
            # 音频播放时启动定时器更新进度
            self._timer.start(100)  # 100ms 刷新进度

    def _pause(self):
        self._playing = False
        self._btn_play.setText("▶  播放")
        self._timer.stop()
        self._stop_audio()

    def _stop(self):
        self._pause()
        self._current_sec = 0.0
        self._seek_slider.setValue(0)
        self._time_label.setText("00:00")
        if self._media_type == "video":
            self._show_first_frame()
        elif self._media_type == "audio":
            pass  # 音频无画面，仅重置进度

    def _rewind(self):
        new_sec = max(0.0, self._current_sec - 5.0)
        self._seek_to(new_sec)

    def _forward(self):
        new_sec = min(self._duration_sec, self._current_sec + 5.0)
        self._seek_to(new_sec)

    def _seek_to(self, sec: float):
        self._current_sec = sec
        self._update_slider()
        if self._media_type == "video" and self._cap:
            frame_idx = int(sec * self._fps)
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = self._cap.read()
            if ret:
                self._paint_frame(frame)
        if self._playing and self._media_type == "audio":
            # 重新启动音频从新位置播放
            self._stop_audio()
            self._start_audio()

    def _tick(self):
        """定时器回调：推进画面 / 更新进度"""
        if not self._playing:
            return
        if self._seek_sliding:
            return  # 用户正在拖拽滑块，跳过计时器

        if self._media_type == "video":
            self._current_sec += 1.0 / max(self._fps, 0.01)
            if self._current_sec >= self._duration_sec - 0.01:
                self._stop()
                return
            ret, frame = self._cap.read()
            if not ret:
                self._stop()
                return
            self._paint_frame(frame)

        elif self._media_type == "audio":
            self._current_sec += 0.1
            if self._current_sec >= self._duration_sec - 0.01:
                self._stop()
                return
            # 检查子进程是否还活着
            if self._audio_proc and self._audio_proc.poll() is not None:
                self._stop()
                return

        self._update_slider()

    def _paint_frame(self, frame):
        """将 OpenCV 帧绘制到 QLabel，同时缓存原始 pixmap 用于缩放"""
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = frame_rgb.shape
        qimg = QImage(frame_rgb.data, w, h, ch * w,
                      QImage.Format.Format_RGB888).copy()
        self._cached_frame = QPixmap.fromImage(qimg)
        self._video_label.setPixmap(self._cached_frame.scaled(
            self._video_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        ))

    def _update_slider(self):
        """更新进度条和文字（不触发 seek）"""
        self._seek_slider.blockSignals(True)
        if self._duration_sec > 0:
            pos = int(self._current_sec / self._duration_sec * 1000)
            self._seek_slider.setValue(min(pos, 1000))
        else:
            self._seek_slider.setValue(0)
        self._seek_slider.blockSignals(False)
        self._time_label.setText(self._fmt_time(self._current_sec))

    def _start_audio(self):
        """启动 ffmpeg 子进程播放音频"""
        try:
            from utils.ffmpeg_utils import get_ffmpeg_path
            ffmpeg = get_ffmpeg_path()
        except Exception:
            ffmpeg = "ffmpeg"

        if not os.path.exists(ffmpeg):
            import shutil
            ffmpeg = shutil.which("ffmpeg") or "ffmpeg"

        # 使用 ffplay 播放音频（ffplay 是 ffmpeg 自带的）
        ffplay = os.path.join(os.path.dirname(ffmpeg), "ffplay.exe")
        if not os.path.exists(ffplay):
            import shutil
            ffplay = shutil.which("ffplay")

        if not ffplay:
            logging.warning("ffplay 不可用，预览无音频")
            return

        try:
            # ffplay -nodisp -autoexit -ss start_sec -i file
            cmd = [
                ffplay, "-nodisp", "-autoexit",
                "-ss", str(self._current_sec),
                "-i", self._file_path,
                "-loglevel", "quiet"
            ]
            self._audio_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL
            )
        except Exception:
            logging.debug("ffplay 启动失败", exc_info=True)
            self._audio_proc = None

    def _stop_audio(self):
        if self._audio_proc:
            try:
                self._audio_proc.terminate()
                try:
                    self._audio_proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._audio_proc.kill()
            except Exception:
                pass
            self._audio_proc = None

    def _on_seek_pressed(self):
        self._seek_sliding = True

    def _on_seek_moved(self, val: int):
        """用户拖拽滑块时实时预览（视频）"""
        if self._duration_sec > 0:
            sec = val / 1000.0 * self._duration_sec
            self._time_label.setText(self._fmt_time(sec))
            if self._media_type == "video" and self._cap:
                frame_idx = int(sec * self._fps)
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ret, frame = self._cap.read()
                if ret:
                    self._paint_frame(frame)

    def _on_seek_released(self):
        self._seek_sliding = False
        if self._duration_sec > 0:
            val = self._seek_slider.value()
            sec = val / 1000.0 * self._duration_sec
            self._seek_to(sec)

    def _cleanup(self):
        """释放所有资源"""
        self._timer.stop()
        self._stop_audio()
        if self._cap:
            self._cap.release()
            self._cap = None

    @staticmethod
    def _fmt_time(sec: float) -> str:
        m = int(sec) // 60
        s = int(sec) % 60
        return f"{m:02d}:{s:02d}"

    def closeEvent(self, event):
        self._cleanup()
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._media_type == "image":
            self._resize_image()
        elif self._media_type == "video" and self._video_label.pixmap():
            # 暂停状态下缩放当前帧
            if not self._playing and hasattr(self, '_cached_frame') and self._cached_frame:
                self._video_label.setPixmap(self._cached_frame.scaled(
                    self._video_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                ))
