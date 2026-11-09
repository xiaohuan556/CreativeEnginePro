from core.edit_engine import EditTimeline, VideoClip, SubtitleBlock
from core.timeline_index import TimelineIndex


def _clip(start, duration):
    return VideoClip(source_path="fixture.mp4", source_duration=duration,
                     trim_start=0.0, trim_end=duration,
                     timeline_start=start)


def test_index_returns_only_visible_intervals_and_hit_test_is_topmost_first():
    timeline = EditTimeline()
    first = _clip(0.0, 2.0)
    second = _clip(4.0, 2.0)
    timeline.add_video_clip(first, skip_overlap=True)
    timeline.add_video_clip(second, skip_overlap=True)
    index = TimelineIndex(timeline)

    assert index.clips("video", 0, 2.5, 4.2) == [second]
    assert index.hit_test("video", 0, 1.0) == [first]
    assert index.hit_test("video", 0, 3.0) == []


def test_index_rebuilds_boundaries_and_keyframes_after_model_edit():
    timeline = EditTimeline()
    clip = _clip(1.0, 3.0)
    clip.keyframes = {"scale": [(0.5, 1.0), (2.0, 1.2)]}
    timeline.add_video_clip(clip, skip_overlap=True)
    index = TimelineIndex(timeline)
    assert (1.0, id(clip)) in index.boundaries
    assert (4.0, id(clip)) in index.boundaries
    assert (1.5, id(clip)) in index.keyframes

    clip.timeline_start = 6.0
    index.rebuild()
    assert (6.0, id(clip)) in index.boundaries
    assert (1.0, id(clip)) not in index.boundaries


def test_index_handles_subtitle_end_times():
    timeline = EditTimeline()
    block = SubtitleBlock(text="caption", timeline_start=2.0, timeline_end=3.5)
    timeline.add_subtitle(block)
    index = TimelineIndex(timeline)
    assert index.hit_test("subtitle", 0, 2.5) == [block]


def test_audio_boundary_lookup_uses_open_start_closed_end():
    timeline = EditTimeline()
    clip = _clip(2.0, 3.0)
    timeline.add_video_clip(clip, skip_overlap=True)
    index = TimelineIndex(timeline)

    assert index.total_duration == 5.0
    assert not index.has_boundary_between("video", 0, 1.0, 1.9)
    assert index.has_boundary_between("video", 0, 1.9, 2.0)
    assert not index.has_boundary_between("video", 0, 2.0, 2.1)
    assert index.has_boundary_between("video", 0, 4.9, 5.0)

    clip.speed = 2.0
    index.rebuild()
    assert index.total_duration == 3.5
    assert index.has_boundary_between("video", 0, 3.4, 3.5)


def test_active_lookup_preserves_track_order_visibility_and_half_open_ends():
    timeline = EditTimeline()
    first = _clip(10.0, 10.0)
    second = _clip(8.0, 12.0)
    timeline.video_tracks[0].extend((first, second))
    index = TimelineIndex(timeline)

    assert index.active_at("video", 0, 12.0) == [first, second]
    first.visible = False
    assert index.active_at("video", 0, 12.0) == [second]
    assert index.active_at("video", 0, 20.0) == []


def test_active_lookup_updates_after_timing_edit_on_long_track():
    timeline = EditTimeline()
    clips = [_clip(float(i * 2), 1.0) for i in range(1200)]
    timeline.video_tracks[0].extend(clips)
    index = TimelineIndex(timeline)

    assert index.active_at("video", 0, 1800.5) == [clips[900]]
    assert index.active_at("video", 0, 1801.0) == []
    clips[900].speed = 2.0
    index.rebuild()
    assert index.active_at("video", 0, 1800.5) == []

