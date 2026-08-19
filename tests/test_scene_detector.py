import unittest

from core.scene_detector import filter_scene_candidates, parse_scene_metadata


class SceneDetectorTests(unittest.TestCase):
    def test_metadata_parser_pairs_time_and_score(self):
        output = """
frame:0 pts:61440 pts_time:2.000000
lavfi.scene_score=0.510000
frame:1 pts:122880 pts_time:4.000000
lavfi.scene_score=0.330000
"""
        self.assertEqual(parse_scene_metadata(output), [
            {"time": 2.0, "score": 0.51},
            {"time": 4.0, "score": 0.33},
        ])

    def test_single_frame_flash_pair_is_removed(self):
        rows = [
            {"time": 2.000, "score": 0.8},
            {"time": 2.033, "score": 0.7},
            {"time": 4.000, "score": 0.4},
        ]
        self.assertEqual(
            filter_scene_candidates(
                rows, duration=6, min_length=0.5, filter_flashes=True),
            [{"time": 4.0, "score": 0.4}],
        )

    def test_nearby_candidates_keep_stronger_score(self):
        rows = [
            {"time": 2.0, "score": 0.31},
            {"time": 2.5, "score": 0.55},
            {"time": 5.0, "score": 0.40},
        ]
        result = filter_scene_candidates(
            rows, duration=8, min_length=0.8, filter_flashes=False)
        self.assertEqual([row["time"] for row in result], [2.5, 5.0])

    def test_last_fragment_cannot_be_too_short(self):
        result = filter_scene_candidates(
            [{"time": 5.6, "score": 0.8}], duration=6, min_length=0.8)
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
