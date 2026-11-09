import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from core.edit_engine import EditTimeline, FFmpegDirectExportWorker, SubtitleBlock, VideoClip
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


def test_plain_overlay_mp4_uses_fast_graph():
    timeline = EditTimeline()
    clip = _video_clip()
    worker = _worker(timeline)

    assert worker._needs_compositor(
        [_video_entry(clip), _video_entry(_video_clip(), track=1)],
        [False],
    ) is False
    parts, label = worker._build_video_graph(
        [_video_entry(clip), _video_entry(_video_clip(), track=1)], 2.0)
    assert label == "vout"
    assert any("split=2" in part for part in parts)
    assert any("overlay=x=(W-w)/2" in part for part in parts)


def test_static_video_position_uses_fast_graph_but_keyframes_do_not():
    timeline = EditTimeline()
    worker = _worker(timeline)

    assert worker._needs_compositor(
        [_video_entry(_video_clip(pos_x=120))], [False]
    ) is False
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


def test_gpu_export_has_software_fallback(monkeypatch):
    worker = _worker(EditTimeline())
    monkeypatch.setattr(worker, "_has_nvenc", lambda _ff: True)
    gpu_args, using_gpu = worker._video_encoder_args("ffmpeg")
    cpu_args, using_cpu = worker._video_encoder_args("ffmpeg", software=True)

    assert using_gpu is True
    assert "h264_nvenc" in gpu_args
    assert using_cpu is False
    assert "libx264" in cpu_args


def test_static_styled_subtitle_uses_fast_path_but_word_animation_does_not():
    timeline = EditTimeline()
    subtitle = SubtitleBlock(
        text="Hello", timeline_start=0, timeline_end=2,
        font_bold=True, outline_width=2, fill_enabled=True)
    timeline.add_subtitle(subtitle)
    worker = _worker(timeline)
    entries = [_video_entry(_video_clip())]
    assert worker._needs_compositor(entries, [False]) is False
    subtitle.word_animation = True
    assert worker._needs_compositor(entries, [False]) is True


def test_dynamic_mask_still_uses_compositor():
    worker = _worker(EditTimeline())
    clip = _video_clip(mask_enabled=True,
                       keyframes={"mask_width": [(0.0, 0.3), (1.0, 0.8)]})
    assert worker._needs_compositor([_video_entry(clip)], [False]) is True


def test_transition_audio_starts_with_second_clip_and_keeps_full_duration():
    timeline = EditTimeline()
    first = _video_clip(out_transition={"type": "fade", "duration": 0.5})
    second = _video_clip(timeline_start=2.0)
    timeline.add_video_clip(first, skip_overlap=True)
    timeline.add_video_clip(second, skip_overlap=True)
    worker = _worker(timeline)
    audio = [
        {"clip": first, "input_idx": 0, "track": 0},
        {"clip": second, "input_idx": 1, "track": 0},
    ]
    parts, label = worker._build_audio_graph(audio, 4.0)
    assert label == "aout"
    assert any("adelay=2000|2000" in part and "afade=t=in" in part
               for part in parts)
    assert any("apad=whole_dur=4.000" in part for part in parts)

