import unittest

from core.edit_engine import AudioClip, EditTimeline, SubtitleBlock, VideoClip


def _item(clip, kind, track_idx=0, offset=0.0):
    return {
        "clip": clip,
        "kind": kind,
        "track_idx": track_idx,
        "offset": offset,
    }


class TimelineClipboardTests(unittest.TestCase):
    def test_audio_same_time_uses_another_track_but_free_time_reuses_track(self):
        timeline = EditTimeline()
        existing = AudioClip(
            source_path="existing.wav", source_duration=3,
            trim_start=0, trim_end=3, timeline_start=5)
        timeline.add_audio_clip(existing, track_idx=0, skip_overlap=True)
        copied = AudioClip(
            source_path="copied.wav", source_duration=2,
            trim_start=0, trim_end=2, timeline_start=1)
        clipboard = [_item(copied, "audio")]

        overlapping = timeline.paste_clips(clipboard, 5)
        self.assertEqual(1, overlapping[0][2])
        self.assertEqual(2, len(timeline.audio_tracks))
        self.assertEqual(5, overlapping[0][0].timeline_start)

        free = timeline.paste_clips(clipboard, 10)
        self.assertEqual(0, free[0][2])
        self.assertEqual(10, free[0][0].timeline_start)
        self.assertNotEqual(copied.id, overlapping[0][0].id)
        self.assertNotEqual(overlapping[0][0].id, free[0][0].id)

    def test_video_and_subtitle_follow_the_same_overlap_rule(self):
        timeline = EditTimeline()
        timeline.add_video_clip(VideoClip(
            source_path="base.mp4", source_duration=4,
            trim_start=0, trim_end=4, timeline_start=0),
            track_idx=0, skip_overlap=True)
        timeline.add_subtitle(SubtitleBlock(
            text="原字幕", timeline_start=0, timeline_end=4), track_idx=-1)

        video = VideoClip(
            source_path="copy.mp4", source_duration=2,
            trim_start=0, trim_end=2, timeline_start=0)
        subtitle = SubtitleBlock(
            text="复制字幕", timeline_start=0, timeline_end=2)
        pasted = timeline.paste_clips([
            _item(video, "video"),
            _item(subtitle, "subtitle"),
        ], 0)

        by_kind = {kind: (clip, track_idx) for clip, kind, track_idx in pasted}
        self.assertEqual(1, by_kind["video"][1])
        self.assertEqual(1, by_kind["subtitle"][1])
        self.assertEqual("复制字幕", by_kind["subtitle"][0].text)

    def test_multi_clip_copy_preserves_relative_spacing_on_another_timeline(self):
        target = EditTimeline()
        first = AudioClip(
            source_path="one.wav", source_duration=2,
            trim_start=0, trim_end=2, timeline_start=3)
        second = AudioClip(
            source_path="two.wav", source_duration=1,
            trim_start=0, trim_end=1, timeline_start=8)

        pasted = target.paste_clips([
            _item(first, "audio", offset=0),
            _item(second, "audio", offset=5),
        ], 20)

        self.assertEqual([20, 25], [item[0].timeline_start for item in pasted])
        self.assertEqual([0, 0], [item[2] for item in pasted])
        self.assertEqual(["one.wav", "two.wav"],
                         [item[0].source_path for item in pasted])


if __name__ == "__main__":
    unittest.main()
