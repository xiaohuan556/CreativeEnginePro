"""
小欢语音 - 视频预览播放器组件
封装 QMediaPlayer + QVideoWidget，支持快捷键响应
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QSlider, QFrame,
)
from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget


class VideoPreview(QWidget):
    """
    视频预览播放器

    用法:
        preview = VideoPreview()
        preview.load("/path/to/video.mp4")
        preview.toggle()   # 播放/暂停
        preview.seek_relative(5)  # 前进5秒
    """

    # 信号
    position_changed = pyqtSignal(int)   # 毫秒
    duration_changed = pyqtSignal(int)   # 毫秒
    playback_toggled = pyqtSignal(bool)  # is_playing

    SEEK_SECONDS = 5  # 默认跳转秒数

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration_ms = 0
        self._seeking = False
        self._build()
        self._setup_player()

    def _build(self):
        """构建布局：视频区域 + 控制条"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # -- 视频显示区 --
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(200)
        self.video_widget.setStyleSheet(
            "border-radius: 8px; background: #000;"
        )
        layout.addWidget(self.video_widget, 1)

        # -- 播放控制条 --
        ctrl = QWidget()
        ctrl.setFixedHeight(40)
        ctrl.setStyleSheet("background: #252525; border-radius: 6px;")
        ctrl_layout = QHBoxLayout(ctrl)
        ctrl_layout.setContentsMargins(8, 4, 8, 4)
        ctrl_layout.setSpacing(6)

        self.btn_play = QPushButton("▶")
        self.btn_play.setFixedSize(32, 28)
        self.btn_play.setStyleSheet(
            "QPushButton{background:#3a3a3a;color:#fff;border:none;"
            "border-radius:4px;font-size:14px;}"
            "QPushButton:hover{background:#4a4a4a;}"
        )
        self.btn_play.clicked.connect(self.toggle)
        ctrl_layout.addWidget(self.btn_play)

        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.sliderPressed.connect(
            lambda: setattr(self, '_seeking', True))
        self.seek_slider.sliderReleased.connect(self._on_seek)
        ctrl_layout.addWidget(self.seek_slider, 1)

        self.lbl_time = QLabel("00:00 / 00:00")
        self.lbl_time.setFixedWidth(110)
        self.lbl_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_time.setStyleSheet("color:#aaa; font-size:11px;")
        ctrl_layout.addWidget(self.lbl_time)

        layout.addWidget(ctrl)

    def _setup_player(self):
        """初始化 QMediaPlayer"""
        self._player = QMediaPlayer()
        self._audio_out = QAudioOutput()
        self._player.setAudioOutput(self._audio_out)
        self._player.setVideoOutput(self.video_widget)

        self._player.durationChanged.connect(self._on_duration)
        self._player.positionChanged.connect(self._on_position)
        self._player.playbackStateChanged.connect(self._on_state)

    # ------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------
    def load(self, path: str):
        """加载视频文件"""
        self._player.setSource(QUrl.fromLocalFile(path))
        self.btn_play.setText("▶")

    def toggle(self):
        """播放/暂停切换"""
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def play(self):
        self._player.play()

    def pause(self):
        self._player.pause()

    def seek(self, ms: int):
        """跳转到指定毫秒"""
        ms = max(0, min(ms, self._duration_ms))
        self._player.setPosition(ms)

    def seek_relative(self, seconds: int):
        """相对跳转 N 秒"""
        new_pos = self._player.position() + seconds * 1000
        self.seek(new_pos)

    def adjust_volume(self, delta: int):
        """调整音量 delta 百分比"""
        current = int(self._audio_out.volume() * 100)
        new_vol = max(0, min(100, current + delta))
        self._audio_out.setVolume(new_vol / 100.0)

    @property
    def is_playing(self) -> bool:
        return (self._player.playbackState()
                == QMediaPlayer.PlaybackState.PlayingState)

    @property
    def player(self):
        """暴露底层 QMediaPlayer 供高级操作"""
        return self._player

    @property
    def duration(self) -> int:
        return self._duration_ms

    # ------------------------------------------------------------
    # 内部槽
    # ------------------------------------------------------------
    def _on_duration(self, ms: int):
        self._duration_ms = ms
        self.seek_slider.setRange(0, ms)
        self.duration_changed.emit(ms)
        self._update_time_label()

    def _on_position(self, pos: int):
        if not self._seeking:
            self.seek_slider.setValue(pos)
        self._update_time_label()
        self.position_changed.emit(pos)

    def _on_state(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.btn_play.setText("⏸")
            self.playback_toggled.emit(True)
        else:
            self.btn_play.setText("▶")
            self.playback_toggled.emit(False)

    def _on_seek(self):
        self._player.setPosition(self.seek_slider.value())
        self._seeking = False

    def _update_time_label(self):
        pos = self._player.position()
        dur = self._duration_ms
        self.lbl_time.setText(
            f"{self._fmt(pos)} / {self._fmt(dur)}"
        )

    @staticmethod
    def _fmt(ms: int) -> str:
        s = ms // 1000
        return f"{s // 60:02d}:{s % 60:02d}"
