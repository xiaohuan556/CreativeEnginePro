from core.edit_engine import EditTimeline, VideoClip, AudioClip, SubtitleBlock


def _video(start=0.0, duration=10.0):
    return VideoClip(
        source_path="fixture.mp4",
        source_duration=duration,
        trim_start=0.0,
        trim_end=duration,
        timeline_start=start,
    )


def test_undo_snapshot_restores_track_metadata():
    timeline = EditTimeline()
    snapshot = timeline._snapshot()
    timeline.video_track_info[0].muted = True
    timeline.video_track_info[0].locked = True
    timeline.auto_align = False

    timeline._restore(snapshot)

    assert timeline.video_track_info[0].muted is False
    assert timeline.video_track_info[0].locked is False
    assert timeline.auto_align is True


def test_insert_splits_crossing_clip_and_ripples_tail():
    timeline = EditTimeline()
    original = _video()
    timeline.add_video_clip(original, skip_overlap=True)
    inserted = _video(duration=2.0)

    assert timeline.insert_video_clip(inserted, 0, 3.0)
    clips = sorted(timeline.video_tracks[0], key=lambda c: c.timeline_start)

    assert [(round(c.timeline_start, 3), round(c.timeline_end, 3)) for c in clips] == [
        (0.0, 3.0), (3.0, 5.0), (5.0, 12.0)
    ]
    assert round(clips[2].trim_start, 3) == 3.0


def test_overwrite_trims_both_sides_of_existing_clip():
    timeline = EditTimeline()
    timeline.add_video_clip(_video(), skip_overlap=True)
    replacement = _video(duration=4.0)

    assert timeline.overwrite_video_clip(replacement, 0, 3.0)
    clips = sorted(timeline.video_tracks[0], key=lambda c: c.timeline_start)

    assert [(round(c.timeline_start, 3), round(c.timeline_end, 3)) for c in clips] == [
        (0.0, 3.0), (3.0, 7.0), (7.0, 10.0)
    ]


def test_subtitle_auto_placement_skips_locked_track_and_resolves_gaps():
    timeline = EditTimeline()
    first = SubtitleBlock(text="A", timeline_start=0.0, timeline_end=2.0)
    assert timeline.add_subtitle(first)
    timeline.subtitle_track_info[0].locked = True

    second = SubtitleBlock(text="B", timeline_start=0.5, timeline_end=1.5)
    assert timeline.add_subtitle(second)
    assert len(timeline.subtitle_tracks) == 2
    assert second in timeline.subtitle_tracks[0]
    assert first in timeline.subtitle_tracks[1]

    third = SubtitleBlock(text="C", timeline_start=0.5, timeline_end=1.5)
    start = timeline.subtitle_free_start(0, third.timeline_start,
                                         third.duration)
    assert start == 1.5


def test_subtitle_split_preserves_and_rebases_animation_state():
    timeline = EditTimeline()
    block = SubtitleBlock(text="hello world", timeline_start=0.0,
                          timeline_end=4.0, from_asr=True,
                          word_animation=True, word_timings=[0.2, 2.5],
                          opacity=0.7, keyframes={"scale": [(1.0, 1.0),
                                                               (3.0, 1.2)]})
    assert timeline.add_subtitle(block)

    assert timeline.split_subtitle(block.id, 2.0)
    left, right = sorted(timeline.subtitle_tracks[0],
                         key=lambda item: item.timeline_start)
    assert left.timeline_end == 2.0
    assert right.timeline_start == 2.0
    assert right.from_asr and right.word_animation
    assert right.opacity == 0.7
    assert right.word_timings == [0.5]
    assert right.keyframes["scale"] == [(1.0, 1.2)]


def test_edit_transaction_groups_nested_mutations_into_one_undo_step():
    timeline = EditTimeline()
    first = _video(0.0, 1.0)
    second = _video(1.0, 1.0)
    timeline.add_video_clip(first, skip_overlap=True)
    timeline.add_video_clip(second, skip_overlap=True)
    history_before = len(timeline._history)

    with timeline.edit_transaction():
        assert timeline.remove_video_clip(first.id)
        assert timeline.remove_video_clip(second.id)

    assert len(timeline._history) == history_before + 1
    timeline.undo()
    assert len(timeline.video_tracks[0]) == 2


def test_groups_and_media_links_round_trip_through_project_data():
    timeline = EditTimeline()
    video = _video(0.0, 2.0)
    audio = AudioClip(source_path="voice.wav", source_duration=2.0,
                      trim_start=0.0, trim_end=2.0, timeline_start=0.0)
    timeline.add_video_clip(video, skip_overlap=True)
    timeline.add_audio_clip(audio, skip_overlap=True)

    assert timeline.group_items([video.id, audio.id])
    assert timeline.link_media_items([video.id, audio.id])
    restored = EditTimeline.from_dict(timeline.to_dict())

    rv = restored.video_tracks[0][0]
    ra = restored.audio_tracks[0][0]
    assert rv.group_id and rv.group_id == ra.group_id
    assert rv.link_id and rv.link_id == ra.link_id


def test_named_timeline_command_has_independent_undo_and_redo():
    timeline = EditTimeline()
    clip = _video(0.0, 2.0)
    timeline.add_video_clip(clip, skip_overlap=True)

    command = timeline.execute_command(
        "移动片段", lambda: setattr(clip, "timeline_start", 4.0))

    assert command.name == "移动片段"
    assert timeline._last_command_name == "移动片段"
    assert clip.timeline_start == 4.0
    assert command.undo()
    assert timeline.video_tracks[0][0].timeline_start == 0.0
    assert command.redo()
    assert timeline.video_tracks[0][0].timeline_start == 4.0


def test_track_structure_operations_respect_background_and_lock_rules():
    timeline = EditTimeline()
    timeline.add_video_track()
    timeline.add_video_track()
    timeline.video_tracks[2].append(_video(4.0, 1.0))

    assert not timeline.move_track("video", 1, -1)
    assert timeline.move_track("video", 2, -1)
    timeline.video_track_info[1].locked = True
    assert not timeline.move_track("video", 1, 1)

    assert timeline.remove_empty_track("audio", 0) is False
    timeline.add_audio_track()
    assert timeline.remove_empty_track("audio", 1)

