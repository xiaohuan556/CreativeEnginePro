import unittest

from core.text_rough_cut import (
    build_cut_plan,
    choose_highlight_indices,
    is_filler_sentence,
)


class TextRoughCutTests(unittest.TestCase):
    def test_only_independent_fillers_are_removed(self):
        self.assertTrue(is_filler_sentence("嗯"))
        self.assertTrue(is_filler_sentence("那个那个"))
        self.assertFalse(is_filler_sentence("嗯，这个方法确实有效"))

    def test_highlight_selection_respects_target(self):
        rows = [
            {"start": 0, "end": 1, "text": "嗯"},
            {"start": 1, "end": 4, "text": "最重要的方法是先验证结果"},
            {"start": 4, "end": 7, "text": "普通的补充说明"},
        ]
        self.assertEqual(choose_highlight_indices(rows, 3), [1])

    def test_compact_plan_keeps_source_time_and_rebuilds_timeline(self):
        rows = [
            {"start": 10, "end": 12, "source_start": 2, "source_end": 4,
             "text": "第一句"},
            {"start": 16, "end": 18, "source_start": 8, "source_end": 10,
             "text": "第二句"},
        ]
        ranges, subtitles = build_cut_plan(
            rows, source_offset=8, trim_start=0, trim_end=20,
            timeline_start=10, speed=1, padding=0.1, compact=True)
        self.assertEqual(len(ranges), 2)
        self.assertAlmostEqual(ranges[0]["source_start"], 1.9)
        self.assertAlmostEqual(ranges[1]["timeline_start"], 12.2)
        self.assertAlmostEqual(subtitles[1]["start"], 12.3)

    def test_speed_is_applied_to_timeline_duration(self):
        rows = [{"start": 5, "end": 7, "source_start": 4, "source_end": 8,
                 "text": "加速片段"}]
        ranges, subtitles = build_cut_plan(
            rows, source_offset=0, trim_start=0, trim_end=20,
            timeline_start=5, speed=2, padding=0, compact=True)
        self.assertAlmostEqual(ranges[0]["timeline_start"], 5)
        self.assertAlmostEqual(subtitles[0]["end"] - subtitles[0]["start"], 2)


if __name__ == "__main__":
    unittest.main()
