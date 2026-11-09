import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QImage, QColor
from PyQt6.QtWidgets import QApplication

from core.edit_engine import EditTimeline, FFmpegDirectExportWorker, VideoClip


APP = QApplication.instance() or QApplication([])


def test_encoder_failure_reports_ffmpeg_reason(tmp_path, monkeypatch):
    source = tmp_path / "source.png"
    image = QImage(160, 90, QImage.Format.Format_RGB32)
    image.fill(QColor("#4c8ac2"))
    assert image.save(str(source))

    timeline = EditTimeline()
    track = timeline.add_video_track()
    timeline.add_video_clip(
        VideoClip(source_path=str(source), source_duration=0.5,
                  trim_end=0.5, scale=0.5, mask_enabled=True,
                  keyframes={"mask_width": [(0.0, 0.3), (0.4, 0.8)]}),
        track_idx=track, skip_overlap=True)
    worker = FFmpegDirectExportWorker(
        timeline, str(tmp_path / "output.mp4"), (160, 90), fps=10)
    monkeypatch.setattr(
        worker, "_video_encoder_args",
        lambda _ff, software=False: (["-c:v", "missing_test_encoder"], False))
    result = []
    worker.finished.connect(lambda ok, value: result.append((ok, value)))

    worker._do_export()

    assert result and result[-1][0] is False
    assert "missing_test_encoder" in result[-1][1]


def test_failed_gpu_encode_retries_with_software(tmp_path, monkeypatch):
    source = tmp_path / "source.png"
    image = QImage(160, 90, QImage.Format.Format_RGB32)
    image.fill(QColor("#4c8ac2"))
    assert image.save(str(source))

    timeline = EditTimeline()
    track = timeline.add_video_track()
    timeline.add_video_clip(
        VideoClip(source_path=str(source), source_duration=0.5,
                  trim_end=0.5, scale=0.5, mask_enabled=True,
                  keyframes={"mask_width": [(0.0, 0.3), (0.4, 0.8)]}),
        track_idx=track, skip_overlap=True)
    output = tmp_path / "output.mp4"
    worker = FFmpegDirectExportWorker(
        timeline, str(output), (160, 90), fps=10)
    choices = []

    def encoder_args(_ff, software=False):
        choices.append(software)
        if software:
            return (["-c:v", "libx264", "-preset", "ultrafast",
                     "-pix_fmt", "yuv420p"], False)
        return (["-c:v", "missing_gpu_test_encoder"], True)

    monkeypatch.setattr(worker, "_video_encoder_args", encoder_args)
    result = []
    worker.finished.connect(lambda ok, value: result.append((ok, value)))

    worker._do_export()

    assert choices == [False, True]
    assert result and result[-1][0] is True, result
    assert output.exists() and output.stat().st_size > 0
