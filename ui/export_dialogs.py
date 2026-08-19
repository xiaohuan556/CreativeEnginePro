"""
export_dialogs.py — 导出弹窗和Worker（从 editor_tab.py 提取）

包含：ExportDialog, AudioExportDialog, _ProgressDialog, _AudioExportWorker
"""
from __future__ import annotations
import os
import shutil
import subprocess
import tempfile

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QComboBox, QPushButton, QLabel, QLineEdit,
    QProgressBar, QFileDialog, QApplication,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QSettings

from core.edit_engine import EditTimeline


# ═══════════════════════════════════════════════
# ExportDialog — 视频导出设置
# ═══════════════════════════════════════════════

class ExportDialog(QDialog):
    """导出设置弹窗"""
    _SETTINGS_ORG = "CreativeEnginePro"
    _SETTINGS_APP = "EditorExport"

    def __init__(self, parent=None, canvas_size=None, default_name=""):
        """canvas_size: (w, h) 画布像素尺寸，用于预选分辨率
           default_name: 默认文件名（不含扩展名）"""
        super().__init__(parent)
        self.setWindowTitle("导出视频")
        self.setFixedWidth(420)
        self.setStyleSheet("""
            QDialog { background:#1e1e1e; color:#ccc; }
            QLabel { color:#ccc; font-size:12px; }
            QComboBox, QSpinBox, QLineEdit {
                background:#2a2a2a; color:#ccc; border:1px solid #444;
                border-radius:3px; padding:4px 8px; font-size:12px;
            }
            QComboBox QAbstractItemView { background:#2a2a2a; color:#ccc; selection-background-color:#3d8ef8; }
            QPushButton { border-radius:4px; padding:6px 16px; font-size:12px; }
        """)
        self._canvas_size = canvas_size
        self._default_name = default_name
        self._build()

    def _settings(self):
        return QSettings(self._SETTINGS_ORG, self._SETTINGS_APP)

    @staticmethod
    def _safe_output_name(name: str) -> str:
        import re
        return re.sub(r'[<>:"/\\|?*]', '_', name.strip() or "output")

    def _restore_settings(self):
        """恢复上次确认导出时使用的参数和目录。"""
        settings = self._settings()

        resolution = str(settings.value("resolution", "") or "")
        if resolution:
            resolution = resolution.replace("x", "×")
            idx = self.res_combo.findText(resolution)
            if idx < 0 and "×" in resolution:
                self.res_combo.insertItem(0, resolution)
                idx = 0
            if idx >= 0:
                self.res_combo.setCurrentIndex(idx)

        fps = str(settings.value("fps", "") or "")
        idx = self.fps_combo.findText(fps)
        if idx >= 0:
            self.fps_combo.setCurrentIndex(idx)

        try:
            quality_idx = int(settings.value("quality_index", 0))
        except (TypeError, ValueError):
            quality_idx = 0
        if 0 <= quality_idx < self.quality_combo.count():
            self.quality_combo.setCurrentIndex(quality_idx)

        last_dir = str(settings.value("last_directory", "") or "")
        name = self._safe_output_name(self._default_name or "output") + ".mp4"
        self.path_edit.setText(os.path.join(last_dir, name) if last_dir else name)

    def _save_settings(self, path: str):
        """仅在用户确认开始导出后保存，取消弹窗不会覆盖上次设置。"""
        settings = self._settings()
        settings.setValue("resolution", self.res_combo.currentText())
        settings.setValue("fps", self.fps_combo.currentText())
        settings.setValue("quality_index", self.quality_combo.currentIndex())
        if path:
            settings.setValue("last_directory", os.path.dirname(os.path.abspath(path)))
        settings.sync()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.setContentsMargins(20, 16, 20, 16)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.res_combo = QComboBox()
        self.res_combo.addItems([
            "3840×2160", "2560×1440", "1920×1080", "1600×900",
            "1280×720", "854×480",
            "2160×3840", "1440×2560", "1080×1920", "720×1280", "480×854",
            "1080×1080", "720×720",
            "3440×1440", "2560×1080",
        ])
        if self._canvas_size:
            cw, ch = self._canvas_size
            label = f"{cw}×{ch}"
            idx = self.res_combo.findText(label)
            if idx < 0:
                self.res_combo.insertItem(0, label)
                idx = 0
            self.res_combo.setCurrentIndex(idx)
        form.addRow("分辨率:", self.res_combo)

        self.fps_combo = QComboBox()
        self.fps_combo.addItems(["30", "60", "25", "24"])
        form.addRow("帧率:", self.fps_combo)

        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["高质量 (CRF 18)", "标准 (CRF 23)", "压缩 (CRF 28)"])
        form.addRow("画质:", self.quality_combo)

        lay.addLayout(form)

        # 输出路径
        path_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("输入文件名或点击浏览选择路径…")
        btn_browse = QPushButton("浏览")
        btn_browse.setStyleSheet("QPushButton{background:#2a2a2a;color:#ccc;border:1px solid #444;"
                                  "border-radius:3px;} QPushButton:hover{background:#3a3a3a;}")
        btn_browse.clicked.connect(self._browse)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(btn_browse)
        lay.addLayout(path_row)

        # 控件全部创建后恢复上次参数；文件名仍使用当前工程名。
        self._restore_settings()

        # 进度条
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._progress.setFixedHeight(8)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet(
            "QProgressBar{background:#2a2a2a;border:none;border-radius:4px;}"
            "QProgressBar::chunk{background:#3d8ef8;border-radius:4px;}")
        lay.addWidget(self._progress)

        self._status = QLabel("")
        self._status.setStyleSheet("color:#888; font-size:11px;")
        self._status.setVisible(False)
        lay.addWidget(self._status)

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_export = QPushButton("开始导出")
        self._btn_export.setStyleSheet(
            "QPushButton{background:#2a5fa8;color:#fff;border:none;font-weight:bold;}"
            "QPushButton:hover{background:#3d8ef8;}"
            "QPushButton:disabled{background:#333;color:#666;}")
        self._btn_export.clicked.connect(self.accept)
        btn_cancel = QPushButton("取消")
        btn_cancel.setStyleSheet(
            "QPushButton{background:#2a2a2a;color:#aaa;border:1px solid #444;}"
            "QPushButton:hover{background:#3a3a3a;color:#fff;}")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(self._btn_export)
        lay.addLayout(btn_row)

    def _browse(self):
        current = self.path_edit.text().strip()
        # 确保默认扩展名为 .mp4
        if current and not current.lower().endswith(".mp4"):
            current += ".mp4"
        p, _ = QFileDialog.getSaveFileName(self, "保存视频", current or "output.mp4", "视频文件 (*.mp4)")
        if p:
            self.path_edit.setText(p)

    def get_settings(self):
        text = self.res_combo.currentText().replace("×", "x")
        parts = text.split("x")
        W, H = int(parts[0]), int(parts[1])
        fps = int(self.fps_combo.currentText())
        crf_map = {0: 18, 1: 23, 2: 28}
        crf = crf_map.get(self.quality_combo.currentIndex(), 18)
        path = self.path_edit.text().strip()
        # 确保有 .mp4 扩展名
        if path and not path.lower().endswith(".mp4"):
            path += ".mp4"
        self._save_settings(path)
        return {"resolution": (W, H), "fps": fps, "crf": crf, "path": path}

    def show_progress(self, pct: int, msg: str):
        self._progress.setVisible(True)
        self._status.setVisible(True)
        self._progress.setValue(pct)
        self._status.setText(msg)
        self._btn_export.setEnabled(False)


# ═══════════════════════════════════════════════
# AudioExportDialog — 音频导出设置
# ═══════════════════════════════════════════════

class AudioExportDialog(QDialog):
    """音频导出设置弹窗"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导出音频")
        self.setFixedWidth(380)
        self.setStyleSheet("""
            QDialog { background:#1e1e1e; color:#ccc; }
            QLabel { color:#ccc; font-size:12px; }
            QComboBox, QSpinBox, QLineEdit {
                background:#2a2a2a; color:#ccc; border:1px solid #444;
                border-radius:3px; padding:4px 8px; font-size:12px;
            }
            QComboBox QAbstractItemView { background:#2a2a2a; color:#ccc; selection-background-color:#3d8ef8; }
            QPushButton { border-radius:4px; padding:6px 16px; font-size:12px; }
        """)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.setContentsMargins(20, 16, 20, 16)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._fmt_combo = QComboBox()
        self._fmt_combo.addItems(["MP3", "WAV"])
        self._fmt_combo.currentTextChanged.connect(self._on_fmt_changed)
        form.addRow("格式:", self._fmt_combo)

        self._sr_combo = QComboBox()
        self._sr_combo.addItems(["48000 Hz", "44100 Hz", "96000 Hz", "22050 Hz"])
        form.addRow("采样率:", self._sr_combo)

        self._br_combo = QComboBox()
        self._br_combo.addItems(["320 kbps", "256 kbps", "192 kbps", "128 kbps"])
        form.addRow("比特率:", self._br_combo)

        self._ch_combo = QComboBox()
        self._ch_combo.addItems(["立体声", "单声道"])
        form.addRow("声道:", self._ch_combo)

        lay.addLayout(form)

        # 输出路径
        path_row = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("点击选择输出路径…")
        self._path_edit.setReadOnly(True)
        btn_browse = QPushButton("浏览")
        btn_browse.setStyleSheet(
            "QPushButton{background:#2a2a2a;color:#ccc;border:1px solid #444;"
            "border-radius:3px;} QPushButton:hover{background:#3a3a3a;}")
        btn_browse.clicked.connect(self._browse)
        path_row.addWidget(self._path_edit, 1)
        path_row.addWidget(btn_browse)
        lay.addLayout(path_row)

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_export = QPushButton("开始导出")
        self._btn_export.setStyleSheet(
            "QPushButton{background:#2a5fa8;color:#fff;border:none;font-weight:bold;}"
            "QPushButton:hover{background:#3d8ef8;}"
            "QPushButton:disabled{background:#333;color:#666;}")
        self._btn_export.clicked.connect(self.accept)
        btn_cancel = QPushButton("取消")
        btn_cancel.setStyleSheet(
            "QPushButton{background:#2a2a2a;color:#aaa;border:1px solid #444;}"
            "QPushButton:hover{background:#3a3a3a;color:#fff;}")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(self._btn_export)
        lay.addLayout(btn_row)

    def _on_fmt_changed(self, fmt: str):
        """WAV 不需要比特率，灰掉比特率下拉"""
        self._br_combo.setEnabled(fmt == "MP3")

    def _browse(self):
        fmt = self._fmt_combo.currentText().lower()
        p, _ = QFileDialog.getSaveFileName(
            self, "保存音频", f"output.{fmt}",
            f"{fmt.upper()} (*.{fmt})")
        if p:
            self._path_edit.setText(p)

    def get_settings(self):
        fmt = self._fmt_combo.currentText().lower()
        sr = int(self._sr_combo.currentText().split()[0])
        br = self._br_combo.currentText().split()[0] + "k"
        channels = 2 if self._ch_combo.currentText() == "立体声" else 1
        return {
            "path": self._path_edit.text(),
            "format": fmt,
            "sample_rate": sr,
            "bitrate": br,
            "channels": channels,
        }


# ═══════════════════════════════════════════════
# _ProgressDialog — 导出进度弹窗
# ═══════════════════════════════════════════════

class _ProgressDialog(QDialog):
    cancelled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导出中…")
        self.setFixedSize(360, 120)
        self.setModal(True)
        self.setStyleSheet("QDialog{background:#1e1e1e;} QLabel{color:#ccc;} "
                           "QPushButton{background:#2a2a2a;color:#aaa;border:1px solid #444;"
                           "border-radius:3px;padding:5px 16px;font-size:12px;}"
                           "QPushButton:hover{background:#c0392b;color:#fff;border-color:#c0392b;}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(10)
        self._lbl = QLabel("准备导出…")
        self._lbl.setStyleSheet("color:#aaa; font-size:12px;")
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setStyleSheet(
            "QProgressBar{background:#2a2a2a;border:none;border-radius:4px;height:12px;}"
            "QProgressBar::chunk{background:#3d8ef8;border-radius:4px;}")
        lay.addWidget(self._lbl)
        lay.addWidget(self._bar)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_cancel = QPushButton("取消导出")
        self._btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._btn_cancel)
        lay.addLayout(btn_row)
        self._cancelling = False

    def closeEvent(self, event):
        if not self._cancelling:
            self._on_cancel()
        event.ignore()

    def _on_cancel(self):
        self._cancelling = True
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.setText("取消中…")
        self._lbl.setText("正在取消…")
        self.cancelled.emit()

    def update_progress(self, pct: int, msg: str):
        if self._cancelling:
            return
        self._bar.setValue(pct)
        self._lbl.setText(msg)
        QApplication.processEvents()


# ═══════════════════════════════════════════════
# _AudioExportWorker — 音频导出线程
# ═══════════════════════════════════════════════

class _AudioExportWorker(QThread):
    """纯音频导出：将所有非静音音频轨混音导出为 MP3/WAV"""
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str)

    def __init__(self, tl: EditTimeline, output: str, bitrate: str = "320k",
                 sample_rate: int = 48000, channels: int = 2):
        super().__init__()
        self._tl = tl
        self._output = output
        self._bitrate = bitrate
        self._sample_rate = sample_rate
        self._channels = channels
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _check_cancel(self):
        if self._cancelled:
            raise RuntimeError("用户取消")

    def run(self):
        try:
            from utils.ffmpeg_utils import get_ffmpeg_path
            ffmpeg = get_ffmpeg_path()
        except Exception:
            ffmpeg = "ffmpeg"
        if not os.path.exists(ffmpeg):
            import shutil
            alt = shutil.which("ffmpeg")
            if not alt:
                self.finished.emit(False, "FFmpeg 不可用，请检查安装")
                return
            ffmpeg = alt

        try:
            self._run_inner(ffmpeg)
        except RuntimeError:
            self.finished.emit(False, "用户取消")
        except Exception as e:
            self.finished.emit(False, f"导出失败: {e}")

    @staticmethod
    def _build_volume_expr(kf_volume, duration: float, speed: float) -> str:
        """由 volume 关键帧构建 ffmpeg volume 表达式。

        kf_volume: [(t_playback, volume), ...]  t=相对片段起始的播放秒数
        duration: 片段在时间线上的播放时长（秒）
        speed: 倍速（视频轨音频有倍速，专属音频轨 speed=1.0）

        volume 滤镜运行在 atrim 之后、atempo 之前，因此表达式中 t 的单位
        是「原始速率下的已修剪音频时间」：t_filter = t_playback * speed。
        """
        if not kf_volume or len(kf_volume) == 0:
            return None
        if len(kf_volume) == 1:
            return f"{kf_volume[0][1]:.4f}"

        kf = sorted(kf_volume, key=lambda x: x[0])
        # 从最后一个区间向前构建嵌套 if(lt(t, t2), lerp(v1,v2), ...)
        last_t, last_v = kf[-1]
        expr = f"{last_v:.4f}"

        for i in range(len(kf) - 2, -1, -1):
            t1, v1 = kf[i]
            t2, v2 = kf[i + 1]
            ft1 = t1 * speed
            ft2 = t2 * speed
            dt = ft2 - ft1
            if dt < 0.001:
                seg = f"{v1:.4f}"
            else:
                seg = f"({v1:.4f}+{v2 - v1:.4f}*(t-{ft1:.4f})/{dt:.4f})"
            expr = f"if(lt(t,{ft2:.4f}),{seg},{expr})"

        return expr

    def _run_inner(self, ffmpeg):
        self.progress.emit(5, "收集音频片段…")

        audio_parts = []
        max_end = 0.0

        # 视频轨音频
        for ti, track in enumerate(self._tl.video_tracks):
            info = self._tl.video_track_info[ti] if ti < len(getattr(self._tl, 'video_track_info', [])) else None
            muted = info.muted if info else False
            if muted:
                continue
            for c in track:
                if c.mute:
                    continue
                if not getattr(c, 'visible', True):
                    continue
                if os.path.exists(c.source_path):
                    dur = c.trim_end - c.trim_start
                    vol = getattr(c, 'volume', 1.0) or 1.0
                    kf = getattr(c, 'keyframes', None) or {}
                    audio_parts.append({
                        "path": c.source_path,
                        "trim_start": c.trim_start,
                        "duration": dur,
                        "timeline_start": c.timeline_start,
                        "volume": vol,
                        "kf_volume": kf.get("volume"),
                        "fade_in": getattr(c, 'fade_in', 0) or 0,
                        "fade_out": getattr(c, 'fade_out', 0) or 0,
                        "speed": c.speed,
                    })
                    max_end = max(max_end, c.timeline_start + dur / c.speed)

        # 音频轨
        for ti, track in enumerate(self._tl.audio_tracks):
            info = self._tl.audio_track_info[ti] if ti < len(getattr(self._tl, 'audio_track_info', [])) else None
            muted = info.muted if info else False
            if muted:
                continue
            for c in track:
                if c.mute:
                    continue
                if not getattr(c, 'visible', True):
                    continue
                if os.path.exists(c.source_path):
                    dur = c.trim_end - c.trim_start
                    vol = getattr(c, 'volume', 1.0) or 1.0
                    kf = getattr(c, 'keyframes', None) or {}
                    audio_parts.append({
                        "path": c.source_path,
                        "trim_start": c.trim_start,
                        "duration": dur,
                        "timeline_start": c.timeline_start,
                        "volume": vol,
                        "kf_volume": kf.get("volume"),
                        "fade_in": getattr(c, 'fade_in', 0) or 0,
                        "fade_out": getattr(c, 'fade_out', 0) or 0,
                        "speed": 1.0,
                    })
                    max_end = max(max_end, c.timeline_start + dur)

        if not audio_parts:
            self.finished.emit(False, "没有可导出的音频片段")
            return

        self.progress.emit(10, f"处理 {len(audio_parts)} 个片段…")

        temp_dir = tempfile.mkdtemp(prefix="ce_audio_")
        temp_files = []

        try:
            for i, part in enumerate(audio_parts):
                self._check_cancel()
                pct = 10 + int(60 * (i + 1) / len(audio_parts))
                self.progress.emit(pct, f"处理片段 {i + 1}/{len(audio_parts)}…")

                tmp_out = os.path.join(temp_dir, f"part_{i:04d}.wav")
                filters = []
                filters.append(
                    f"atrim=start={part['trim_start']}:duration={part['duration']}")
                # volume：优先使用关键帧表达式，否则用静态值
                kf_vol = part.get('kf_volume')
                if kf_vol and len(kf_vol) >= 1:
                    vol_expr = self._build_volume_expr(kf_vol, part['duration'], part['speed'])
                    if vol_expr:
                        filters.append(f"volume='{vol_expr}'")
                elif abs(part['volume'] - 1.0) > 0.01:
                    filters.append(f"volume={part['volume']}")
                if abs(part['speed'] - 1.0) > 0.001:
                    filters.append(f"atempo={part['speed']}")
                fdur = max(part['fade_in'], part['fade_out'])
                if fdur > 0.001:
                    fi = f"afade=t=in:d={part['fade_in']}" if part['fade_in'] > 0.001 else ""
                    fo = f"afade=t=out:st={part['duration'] - part['fade_out']}:d={part['fade_out']}" if part['fade_out'] > 0.001 else ""
                    fchain = ",".join(f for f in [fi, fo] if f)
                    if fchain:
                        filters.append(fchain)

                filter_str = ",".join(filters)

                cmd = [
                    ffmpeg, "-y", "-i", part["path"],
                    "-af", filter_str,
                    "-ar", str(self._sample_rate),
                    "-ac", str(self._channels),
                    tmp_out
                ]
                r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
                if r.returncode == 0:
                    temp_files.append((tmp_out, part['timeline_start']))
                else:
                    cmd2 = [
                        ffmpeg, "-y", "-i", part["path"],
                        "-ss", str(part['trim_start']),
                        "-t", str(part['duration']),
                        "-ar", str(self._sample_rate),
                        "-ac", str(self._channels),
                        tmp_out
                    ]
                    subprocess.run(cmd2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
                    temp_files.append((tmp_out, part['timeline_start']))

            if not temp_files:
                self.finished.emit(False, "所有片段处理失败")
                return

            self.progress.emit(75, "混音中…")
            self._check_cancel()

            if len(temp_files) == 1:
                final_in = temp_files[0][0]
            else:
                mix_file = os.path.join(temp_dir, "mixed.wav")
                inputs = []
                filters_complex = []
                for j, (tf, ts) in enumerate(temp_files):
                    inputs.extend(["-i", tf])
                    delay_ms = int(ts * 1000)
                    filters_complex.append(f"[{j}:a]adelay={delay_ms}|{delay_ms}[a{j}]")

                mix_inputs = "".join(f"[a{j}]" for j in range(len(temp_files)))
                filters_complex.append(
                    f"{mix_inputs}amix=inputs={len(temp_files)}:duration=longest:dropout_transition=3[aout]"
                )

                cmd_mix = [ffmpeg, "-y"] + inputs + [
                    "-filter_complex", ";".join(filters_complex),
                    "-map", "[aout]", "-ac", str(self._channels),
                    "-ar", str(self._sample_rate),
                    mix_file
                ]
                r_mix = subprocess.run(cmd_mix, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
                if r_mix.returncode != 0:
                    self.finished.emit(False, "混音失败，请检查 FFmpeg 和音频文件")
                    return
                final_in = mix_file

            self.progress.emit(85, "编码输出…")
            self._check_cancel()

            ext = os.path.splitext(self._output)[1].lower()
            if ext == ".mp3":
                codec_opts = ["-codec:a", "libmp3lame", "-b:a", self._bitrate]
            else:
                codec_opts = ["-codec:a", "pcm_s16le"]

            cmd_final = [
                ffmpeg, "-y", "-i", final_in,
                "-ar", str(self._sample_rate),
                "-ac", str(self._channels),
            ] + codec_opts + [self._output]
            r_final = subprocess.run(cmd_final, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)

            if r_final.returncode == 0:
                self.progress.emit(100, "导出完成")
                self.finished.emit(True, self._output)
            else:
                self.finished.emit(False, "编码失败，请检查 FFmpeg 和输出路径")

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
