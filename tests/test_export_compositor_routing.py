import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from core.edit_engine import EditTimeline, FFmpegDirectExportWorker, VideoClip
    _IMPORT_ERROR = None
except (ImportError, ModuleNotFoundError) as exc:
    EditTimeline = FFmpegDirectExportWorker = VideoClip = None
    _IMPORT_ERROR = exc


pytestmark = pytest.mark.skipif(
    _IMPORT_ERROR is not None,
    reason="PyQt6 runtime is not available",
)


def _worker(timeline):
    return FFmpegDirectExportWorker(
        timeline, "output.mp4", resolution=(1280, 720), fps=30, crf=18,
    )


def _video_clip(**kwargs):
    values = dict(
        source_path="clip.mp4",
        source_duration=2.0,
        trim_start=0.0,
        trim_end=2.0,
        timeline_start=0.0,
    )
    values.update(kwargs)
    return VideoClip(**values)


def _video_entry(clip, track=0):
    return {
        "track": track,
        "clip": clip,
        "input_idx": 0,
        "is_image": False,
        "playback_dur": clip.duration,
    }


def test_overlay_mp4_is_routed_through_compositor():
    timeline = EditTimeline()
    clip = _video_clip()
    worker = _worker(timeline)

    assert worker._needs_compositor(
        [_video_entry(clip), _video_entry(_video_clip(), track=1)],
        [False],
    ) is True


def test_video_transform_is_routed_through_compositor():
    timeline = EditTimeline()
    worker = _worker(timeline)

    assert worker._needs_compositor(
        [_video_entry(_video_clip(pos_x=120))], [False]
    ) is True
    assert worker._needs_compositor(
        [_video_entry(_video_clip(keyframes={"pos_x": [(0.0, 0), (1.0, 120)]}))],
        [False],
    ) is True


def test_xfade_path_requires_absolute_timeline_to_start_at_zero():
    timeline = EditTimeline()
    worker = _worker(timeline)
    worker._input_alpha = [False, False]
    first = _video_clip(timeline_start=1.0)
    second = _video_clip(timeline_start=3.0)
    entries = [_video_entry(first), _video_entry(second)]

    parts, label = worker._build_video_graph(entries, total_dur=5.0)

    assert label == "vout"
    assert any("setpts=PTS+1.000000/TB" in part for part in parts)
    assert not any("xfade=" in part for part in parts)

