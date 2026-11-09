import os
import subprocess

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

from core.compositor import VideoCompositor
from core.edit_engine import EditTimeline, FFmpegDirectExportWorker, SubtitleBlock, VideoClip
from utils.ffmpeg_utils import get_ffmpeg_path


APP = QApplication.instance() or QApplication([])


def _rgb(image):
    rgb = image.convertToFormat(QImage.Format.Format_RGB888)
    return np.frombuffer(rgb.constBits().asstring(rgb.sizeInBytes()), np.uint8).reshape(180, 320, 3)


def _exported_frame(ff, path, sec):
    decoded = subprocess.run(
        [ff, "-loglevel", "error", "-ss", str(sec), "-i", str(path),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True, timeout=30)
    return np.frombuffer(decoded.stdout, np.uint8).reshape(180, 320, 3)


def test_styled_static_subtitle_uses_fast_export_and_matches_preview(tmp_path):
    ff = get_ffmpeg_path()
    source = tmp_path / "blue.png"
    output = tmp_path / "out.mp4"
    image = QImage(320, 180, QImage.Format.Format_RGB32)
    image.fill(QColor("#24508a"))
    assert image.save(str(source))

    timeline = EditTimeline()
    timeline.add_video_clip(
        VideoClip(source_path=str(source), source_duration=1.5,
                  trim_end=1.5), skip_overlap=True)
    timeline.add_subtitle(SubtitleBlock(
        text="Hello", timeline_start=0.4, timeline_end=1.1,
        position="center", font_size=24, font_bold=True,
        outline_width=2, fill_enabled=True, border_radius=4,
        background_color="#352747"))
    worker = FFmpegDirectExportWorker(timeline, str(output), (320, 180), fps=12)
    entries = worker._collect()[1]
    assert worker._needs_compositor(entries, [False]) is False
    layers, _, inputs, temporary = worker._build_raster_subtitle_layers(
        [timeline.subtitle_tracks[0][0]], "vout", 1)
    try:
        assert len(layers) == 1
        cropped = QImage(inputs[-1])
        assert cropped.width() < 320 and cropped.height() < 180
    finally:
        temporary.cleanup()
    result = []
    worker.finished.connect(lambda ok, value: result.append((ok, value)))
    worker.start()
    assert worker.wait(30_000)
    APP.processEvents()
    assert result and result[-1][0], result

    compositor = VideoCompositor(timeline, (320, 180), fps=12)
    try:
        before = _exported_frame(ff, output, 0.2)
        during = _exported_frame(ff, output, 0.7)
        reference_before = _rgb(compositor.render_frame(0.2))
        reference_during = _rgb(compositor.render_frame(0.7))
        assert np.abs(before.astype(np.int16) - reference_before.astype(np.int16)).mean() < 5
        assert np.abs(during.astype(np.int16) - reference_during.astype(np.int16)).mean() < 7
        assert np.abs(during.astype(np.int16) - before.astype(np.int16)).sum() > 100_000
    finally:
        compositor.close()
