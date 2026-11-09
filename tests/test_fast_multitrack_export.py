import os
import subprocess

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QColor, QImage, QPainter

from core.edit_engine import EditTimeline, FFmpegDirectExportWorker, VideoClip
from core.slideshow_engine import TRANSITIONS
from utils.alpha_video import probe_has_alpha, probe_has_audio
from utils.ffmpeg_utils import get_ffmpeg_path


APP = QApplication.instance() or QApplication([])


def test_fast_multitrack_export_keeps_layers_and_audio(tmp_path):
    ff = get_ffmpeg_path()
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    output = tmp_path / "output.mp4"
    subprocess.run(
        [ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=red:s=160x90:r=12:d=1", "-f", "lavfi", "-i",
         "sine=frequency=660:duration=1", "-c:v", "libx264",
         "-c:a", "aac", "-shortest", str(first)],
        check=True, capture_output=True, timeout=30)
    subprocess.run(
        [ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=lime:s=160x90:r=12:d=1", "-c:v", "libx264", str(second)],
        check=True, capture_output=True, timeout=30)

    timeline = EditTimeline()
    main = VideoClip(source_path=str(first), source_duration=1, trim_end=1)
    overlay = VideoClip(source_path=str(second), source_duration=1,
                        trim_end=1, scale=0.5, pos_x=50, opacity=0.5)
    timeline.add_video_clip(main, skip_overlap=True)
    track = timeline.add_video_track()
    timeline.add_video_clip(overlay, track_idx=track, skip_overlap=True)
    worker = FFmpegDirectExportWorker(timeline, str(output), (320, 180), fps=12)
    result = []
    worker.finished.connect(lambda ok, value: result.append((ok, value)))

    worker._do_export()

    assert result and result[-1][0], result
    assert output.exists() and probe_has_audio(str(output))
    decoded = subprocess.run(
        [ff, "-loglevel", "error", "-ss", "0.5", "-i", str(output),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True, timeout=30)
    frame = np.frombuffer(decoded.stdout, np.uint8).reshape(180, 320, 3)
    red = frame[90, 20]
    green = frame[90, 200]
    assert red[0] > 180 and red[1] < 100, red
    assert 85 < green[0] < 170 and 85 < green[1] < 170, green


@pytest.mark.parametrize("codec_args", [
    ["-c:v", "qtrle", "-pix_fmt", "argb"],
    ["-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le"],
])
def test_alpha_mov_overlay_uses_fast_graph_and_keeps_transparency(tmp_path, codec_args):
    ff = get_ffmpeg_path()
    base = tmp_path / "base.mp4"
    alpha_png = tmp_path / "alpha.png"
    overlay = tmp_path / "overlay.mov"
    output = tmp_path / "output.mp4"
    subprocess.run(
        [ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=red:s=160x90:r=12:d=1", "-c:v", "libx264", str(base)],
        check=True, capture_output=True, timeout=30)
    image = QImage(160, 90, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.fillRect(40, 20, 80, 50, QColor("lime"))
    painter.end()
    assert image.save(str(alpha_png))
    subprocess.run(
        [ff, "-y", "-loglevel", "error", "-loop", "1", "-i",
         str(alpha_png), "-t", "1", "-r", "12", *codec_args, str(overlay)],
        check=True, capture_output=True, timeout=30)
    assert probe_has_alpha(str(overlay))

    timeline = EditTimeline()
    timeline.add_video_clip(
        VideoClip(source_path=str(base), source_duration=1, trim_end=1),
        skip_overlap=True)
    track = timeline.add_video_track()
    timeline.add_video_clip(
        VideoClip(source_path=str(overlay), source_duration=1, trim_end=1,
                  scale=0.5), track_idx=track, skip_overlap=True)
    worker = FFmpegDirectExportWorker(timeline, str(output), (320, 180), fps=12)
    inputs, clips, _audio, _subs, total = worker._collect()
    worker._input_alpha = [probe_has_alpha(path) for path in inputs]
    assert worker._needs_compositor(clips, worker._input_alpha) is False
    assert any("format=rgba" in part for part in worker._build_video_graph(clips, total)[0])
    result = []
    worker.finished.connect(lambda ok, value: result.append((ok, value)))
    worker._do_export()
    assert result and result[-1][0], result

    decoded = subprocess.run(
        [ff, "-loglevel", "error", "-ss", "0.5", "-i", str(output),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True, timeout=30)
    frame = np.frombuffer(decoded.stdout, np.uint8).reshape(180, 320, 3)
    assert frame[90, 160, 1] > 150  # opaque green center
    assert frame[90, 25, 0] > 150 and frame[90, 25, 1] < 90  # transparent edge


def test_static_circle_mask_on_mov_overlay_uses_fast_graph(tmp_path):
    ff = get_ffmpeg_path()
    base = tmp_path / "base.mp4"
    overlay = tmp_path / "overlay.mov"
    output = tmp_path / "output.mp4"
    for path, color, codec in ((base, "red", "libx264"),
                               (overlay, "lime", "qtrle")):
        cmd = [ff, "-y", "-loglevel", "error", "-f", "lavfi",
               "-i", f"color=c={color}:s=160x90:r=12:d=1",
               "-c:v", codec]
        if codec == "qtrle":
            cmd.extend(["-pix_fmt", "argb"])
        subprocess.run(cmd + [str(path)], check=True, capture_output=True, timeout=30)
    timeline = EditTimeline()
    timeline.add_video_clip(VideoClip(
        source_path=str(base), source_duration=1, trim_end=1),
        skip_overlap=True)
    track = timeline.add_video_track()
    masked = VideoClip(
        source_path=str(overlay), source_duration=1, trim_end=1,
        scale=0.5, mask_enabled=True, mask_type="circle",
        mask_width=0.5, mask_height=0.5)
    timeline.add_video_clip(masked, track_idx=track, skip_overlap=True)
    worker = FFmpegDirectExportWorker(timeline, str(output), (320, 180), fps=12)
    inputs, clips, _audio, _subs, _total = worker._collect()
    assert worker._needs_compositor(clips, [probe_has_alpha(p) for p in inputs]) is False
    worker._export_via_compositor = lambda *_args, **_kwargs: pytest.fail(
        "static mask unexpectedly used the frame compositor")
    result = []
    worker.finished.connect(lambda ok, value: result.append((ok, value)))
    worker._do_export()
    assert result and result[-1][0], result

    decoded = subprocess.run(
        [ff, "-loglevel", "error", "-ss", "0.5", "-i", str(output),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True, timeout=30)
    frame = np.frombuffer(decoded.stdout, np.uint8).reshape(180, 320, 3)
    assert frame[90, 160, 1] > 150
    assert frame[90, 110, 0] > 150 and frame[90, 110, 1] < 90


@pytest.mark.parametrize("transition_type", sorted(set(TRANSITIONS.values())))
def test_background_transition_uses_fast_graph_at_original_timeline_time(
        tmp_path, transition_type):
    ff = get_ffmpeg_path()
    first_path = tmp_path / "first.mp4"
    second_path = tmp_path / "second.mp4"
    output = tmp_path / "output.mp4"
    for path, color in ((first_path, "red"), (second_path, "blue")):
        subprocess.run(
            [ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
             f"color=c={color}:s=160x90:r=12:d=1", "-c:v", "libx264",
             str(path)], check=True, capture_output=True, timeout=30)
    timeline = EditTimeline()
    timeline.add_video_clip(VideoClip(
        source_path=str(first_path), source_duration=1, trim_end=1,
        out_transition={"type": transition_type, "duration": 0.5}),
        skip_overlap=True)
    timeline.add_video_clip(VideoClip(
        source_path=str(second_path), source_duration=1, trim_end=1,
        timeline_start=1), skip_overlap=True)
    worker = FFmpegDirectExportWorker(timeline, str(output), (320, 180), fps=12)
    _inputs, clips, _audio, _subs, _total = worker._collect()
    assert worker._needs_compositor(clips, [False, False]) is False
    worker._export_via_compositor = lambda *_args, **_kwargs: pytest.fail(
        "simple transition unexpectedly used frame rendering")
    result = []
    worker.finished.connect(lambda ok, value: result.append((ok, value)))
    worker._do_export()
    assert result and result[-1][0], result

    def sample(sec, x, y=90):
        decoded = subprocess.run(
            [ff, "-loglevel", "error", "-ss", str(sec), "-i", str(output),
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            check=True, capture_output=True, timeout=30)
        return np.frombuffer(decoded.stdout, np.uint8).reshape(180, 320, 3)[y, x]

    assert sample(0.5, 160)[0] > 170
    if transition_type in ("fade", "zoom_push"):
        middle = sample(1.25, 160)
        assert middle[0] > 65 and middle[2] > 65, middle
    elif transition_type == "wipe_left":
        assert sample(1.25, 40)[2] > 150  # left side has incoming blue
        assert sample(1.25, 280)[0] > 150  # right side holds red
    elif transition_type == "wipe_right":
        assert sample(1.25, 40)[0] > 150
        assert sample(1.25, 280)[2] > 150
    elif transition_type == "wipe_up":
        assert sample(1.25, 160, 20)[2] > 150
        assert sample(1.25, 160, 160)[0] > 150
    elif transition_type == "wipe_down":
        assert sample(1.25, 160, 20)[0] > 150
        assert sample(1.25, 160, 160)[2] > 150
    assert sample(1.75, 160)[2] > 170


@pytest.mark.parametrize("transition_type", ["fade", "zoom_push"])
def test_masked_mov_overlay_and_transition_share_fast_export(
        tmp_path, transition_type):
    ff = get_ffmpeg_path()
    sources = [(tmp_path / "first.mp4", "red", 1),
               (tmp_path / "second.mp4", "blue", 1),
               (tmp_path / "overlay.mov", "lime", 2)]
    for path, color, duration in sources:
        cmd = [ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
               f"color=c={color}:s=160x90:r=12:d={duration}",
               "-c:v", "qtrle" if path.suffix == ".mov" else "libx264"]
        if path.suffix == ".mov":
            cmd.extend(["-pix_fmt", "argb"])
        subprocess.run(cmd + [str(path)], check=True, capture_output=True, timeout=30)
    timeline = EditTimeline()
    timeline.add_video_clip(VideoClip(
        source_path=str(sources[0][0]), source_duration=1, trim_end=1,
        out_transition={"type": transition_type, "duration": 0.5}),
        skip_overlap=True)
    timeline.add_video_clip(VideoClip(
        source_path=str(sources[1][0]), source_duration=1, trim_end=1,
        timeline_start=1), skip_overlap=True)
    track = timeline.add_video_track()
    timeline.add_video_clip(VideoClip(
        source_path=str(sources[2][0]), source_duration=2, trim_end=2,
        scale=0.5, mask_enabled=True, mask_type="circle",
        mask_width=0.5, mask_height=0.5),
        track_idx=track, skip_overlap=True)
    output = tmp_path / "output.mp4"
    worker = FFmpegDirectExportWorker(timeline, str(output), (320, 180), fps=12)
    _inputs, clips, _audio, _subs, _total = worker._collect()
    assert worker._needs_compositor(clips, [False, False, True]) is False
    worker._export_via_compositor = lambda *_args, **_kwargs: pytest.fail(
        "combined effects unexpectedly used frame rendering")
    result = []
    worker.finished.connect(lambda ok, value: result.append((ok, value)))
    worker._do_export()
    assert result and result[-1][0], result
    decoded = subprocess.run(
        [ff, "-loglevel", "error", "-ss", "1.25", "-i", str(output),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True, timeout=30)
    frame = np.frombuffer(decoded.stdout, np.uint8).reshape(180, 320, 3)
    assert frame[90, 160, 1] > 150  # masked overlay center
    outside = frame[90, 40]
    assert outside[0] > 65 and outside[2] > 65, outside  # background fade


def test_transition_keeps_second_clip_audio_at_its_timeline_start(tmp_path):
    ff = get_ffmpeg_path()
    paths = [tmp_path / "first.mp4", tmp_path / "second.mp4"]
    for path, color, frequency in ((paths[0], "red", 440),
                                   (paths[1], "blue", 880)):
        subprocess.run(
            [ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
             f"color=c={color}:s=160x90:r=12:d=1",
             "-f", "lavfi", "-i",
             f"sine=frequency={frequency}:duration=1",
             "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path)],
            check=True, capture_output=True, timeout=30)
    timeline = EditTimeline()
    timeline.add_video_clip(VideoClip(
        source_path=str(paths[0]), source_duration=1, trim_end=1,
        out_transition={"type": "fade", "duration": 0.5}),
        skip_overlap=True)
    timeline.add_video_clip(VideoClip(
        source_path=str(paths[1]), source_duration=1, trim_end=1,
        timeline_start=1), skip_overlap=True)
    output = tmp_path / "output.mp4"
    worker = FFmpegDirectExportWorker(timeline, str(output), (160, 90), fps=12)
    result = []
    worker.finished.connect(lambda ok, value: result.append((ok, value)))
    worker._do_export()
    assert result and result[-1][0], result
    assert probe_has_audio(str(output))

    def dominant_frequency(sec):
        decoded = subprocess.run(
            [ff, "-loglevel", "error", "-ss", str(sec), "-t", "0.2",
             "-i", str(output), "-vn", "-ac", "1", "-ar", "16000",
             "-f", "s16le", "-"],
            check=True, capture_output=True, timeout=30)
        samples = np.frombuffer(decoded.stdout, np.int16).astype(np.float32)
        spectrum = np.abs(np.fft.rfft(samples))
        return np.fft.rfftfreq(len(samples), 1 / 16000)[np.argmax(spectrum)]

    assert abs(dominant_frequency(0.7) - 440) < 20
    assert abs(dominant_frequency(1.7) - 880) < 20


def test_fast_overlay_respects_later_timeline_start(tmp_path):
    ff = get_ffmpeg_path()
    main_path = tmp_path / "main.mp4"
    overlay_path = tmp_path / "overlay.mp4"
    output = tmp_path / "output.mp4"
    for path, color, duration in ((main_path, "red", 3),
                                  (overlay_path, "lime", 1)):
        subprocess.run(
            [ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
             f"color=c={color}:s=160x90:r=12:d={duration}",
             "-c:v", "libx264", str(path)],
            check=True, capture_output=True, timeout=30)
    timeline = EditTimeline()
    timeline.add_video_clip(
        VideoClip(source_path=str(main_path), source_duration=3, trim_end=3),
        skip_overlap=True)
    track = timeline.add_video_track()
    timeline.add_video_clip(
        VideoClip(source_path=str(overlay_path), source_duration=1,
                  trim_end=1, timeline_start=1, scale=0.5),
        track_idx=track, skip_overlap=True)
    worker = FFmpegDirectExportWorker(timeline, str(output), (320, 180), fps=12)
    result = []
    worker.finished.connect(lambda ok, value: result.append((ok, value)))
    worker._do_export()
    assert result and result[-1][0], result

    for sec, expected_green in ((0.5, False), (1.5, True), (2.5, False)):
        decoded = subprocess.run(
            [ff, "-loglevel", "error", "-ss", str(sec), "-i", str(output),
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            check=True, capture_output=True, timeout=30)
        pixel = np.frombuffer(decoded.stdout, np.uint8).reshape(180, 320, 3)[90, 160]
        assert bool(pixel[1] > pixel[0]) == expected_green, (sec, pixel)


def test_fast_export_with_empty_main_track(tmp_path):
    ff = get_ffmpeg_path()
    source = tmp_path / "overlay.png"
    output = tmp_path / "output.mp4"
    image = QImage(160, 90, QImage.Format.Format_RGB32)
    image.fill(QColor("#40d080"))
    assert image.save(str(source))
    timeline = EditTimeline()
    timeline.canvas_bg_color = "#202040"
    track = timeline.add_video_track()
    timeline.add_video_clip(
        VideoClip(source_path=str(source), source_duration=1,
                  trim_end=1, scale=0.5),
        track_idx=track, skip_overlap=True)
    worker = FFmpegDirectExportWorker(timeline, str(output), (320, 180), fps=12)
    result = []
    worker.finished.connect(lambda ok, value: result.append((ok, value)))
    worker._do_export()
    assert result and result[-1][0], result

    decoded = subprocess.run(
        [ff, "-loglevel", "error", "-ss", "0.5", "-i", str(output),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True, timeout=30)
    frame = np.frombuffer(decoded.stdout, np.uint8).reshape(180, 320, 3)
    assert frame[90, 160, 1] > 150
    assert np.max(np.abs(frame[90, 20].astype(np.int16)
                         - np.array([32, 32, 64]))) < 8


def test_fast_export_positions_main_clip_without_covering_background(tmp_path):
    ff = get_ffmpeg_path()
    source = tmp_path / "main.png"
    output = tmp_path / "output.mp4"
    image = QImage(160, 90, QImage.Format.Format_RGB32)
    image.fill(QColor("#e03040"))
    assert image.save(str(source))
    timeline = EditTimeline()
    timeline.add_video_clip(
        VideoClip(source_path=str(source), source_duration=1,
                  trim_end=1, pos_x=60), skip_overlap=True)
    worker = FFmpegDirectExportWorker(timeline, str(output), (320, 180), fps=12)
    result = []
    worker.finished.connect(lambda ok, value: result.append((ok, value)))
    worker._do_export()
    assert result and result[-1][0], result

    decoded = subprocess.run(
        [ff, "-loglevel", "error", "-ss", "0.5", "-i", str(output),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True, timeout=30)
    frame = np.frombuffer(decoded.stdout, np.uint8).reshape(180, 320, 3)
    assert frame[90, 20].max() < 30
    assert frame[90, 160, 0] > 170
