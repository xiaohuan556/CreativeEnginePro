import unittest

from ai.deterministic_qc import (
    audio_metrics, av_sync_offset, compare_fixed_regions, cosine_similarity, frame_metrics,
    histogram_similarity, inspect_av_sync, run_syncnet_onnx,
    screen_motion_direction, subtitle_safe_area,
)
from ai.production_skills import normalize_sequence_qc


class DeterministicQCTest(unittest.TestCase):
    def test_fixed_scene_device_movement_is_blocked(self):
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            self.skipTest("Pillow unavailable")
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp:
            reference = Path(temp) / "reference.png"
            stable = Path(temp) / "stable.png"
            moved = Path(temp) / "moved.png"

            def scene(path, washer_x):
                image = Image.new("RGB", (512, 288), "white")
                draw = ImageDraw.Draw(image)
                draw.rectangle((washer_x, 30, washer_x + 90, 150), outline="black", width=6)
                draw.rectangle((300, 180, 470, 250), outline="black", width=6)
                draw.line((0, 265, 512, 265), fill="black", width=5)
                image.save(path)

            scene(reference, 30)
            scene(stable, 30)
            scene(moved, 165)
            self.assertEqual("pass", compare_fixed_regions(
                str(reference), str(stable), [0.4, 0.4, 0.1, 0.1])["status"])
            result = compare_fixed_regions(
                str(reference), str(moved), [0.4, 0.4, 0.1, 0.1])
            self.assertEqual("fail", result["status"])
            self.assertIn("FIXED_SCENE_GEOMETRY_DRIFT", result["issues"])

            protected = [[30 / 512, 30 / 288, 90 / 512, 120 / 288]]
            protected_result = compare_fixed_regions(
                str(reference), str(moved), [0, 0, 1, 1], protected)
            self.assertEqual("fail", protected_result["status"])
            self.assertIn("FIXED_SCENE_GEOMETRY_DRIFT",
                          protected_result["issues"])

    def test_freeze_and_audio_clipping_are_objective_failures(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy unavailable")
        still = np.zeros((32, 32, 3), dtype=np.uint8)
        result = frame_metrics([still.copy() for _ in range(5)])
        self.assertIn("FREEZE_FRAME", result["issues"])
        audio = audio_metrics(np.ones(4800), 48000)
        self.assertIn("AUDIO_CLIPPING", audio["issues"])

    def test_subtitle_safe_area(self):
        result = subtitle_safe_area([
            {"x":5, "y":900, "width":500, "height":100}
        ], 1920, 1080)
        self.assertEqual("fail", result["status"])

    def test_identity_clothing_motion_and_av_sync_signals(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy unavailable")
        self.assertGreater(cosine_similarity([1, 0], [0.99, 0.01]), 0.99)
        red = np.zeros((16, 16, 3), dtype=np.uint8); red[..., 0] = 255
        blue = np.zeros((16, 16, 3), dtype=np.uint8); blue[..., 2] = 255
        self.assertLess(histogram_similarity(red, blue), 0.8)
        self.assertEqual("right", screen_motion_direction([(0.1, 0.5), (0.8, 0.5)]))
        audio = np.array([0, 0, 1, 0, 0, 0], dtype=float)
        visual = np.array([1, 0, 0, 0, 0, 0], dtype=float)
        self.assertEqual("fail", av_sync_offset(audio, visual, 10)["status"])

    def test_endpoint_deterministic_failure_blocks_sequence_qc(self):
        result = normalize_sequence_qc({"transitions":[{
            "from_id":"s1", "to_id":"s2", "score":99, "passed":True,
            "deterministic_qc":{"status":"fail", "issues":["ENDPOINT_LIGHTING_DRIFT"]},
        }]})
        self.assertFalse(result["passed"])
        self.assertIn("F7", result["transitions"][0]["blockers"])

    def test_syncnet_is_explicitly_unavailable_without_weights(self):
        result = run_syncnet_onnx([0, 1, 0], [0, 1, 0], "missing.onnx")
        self.assertEqual("unavailable", result["status"])
        self.assertEqual("SYNCNET_MODEL_NOT_CONFIGURED", result["reason"])

    def test_av_sync_pipeline_combines_lightweight_and_syncnet_results(self):
        from unittest.mock import patch
        with patch("ai.deterministic_qc.extract_audio_envelope", return_value={
                "status":"pass", "envelope":[0, 0, 1, 0, 0], "duration":0.4}), \
             patch("ai.deterministic_qc.extract_mouth_motion", return_value={
                "status":"pass", "motion":[1, 0, 0, 0, 0],
                "tracked_frames":5, "sampled_frames":5}), \
             patch("ai.deterministic_qc.run_syncnet_onnx", return_value={
                "status":"fail", "confidence":0.2, "issue":"SYNCNET_MISMATCH"}):
            result = inspect_av_sync("fake.mp4", sample_hz=10)
        self.assertEqual("fail", result["status"])
        self.assertIn("SYNCNET_MISMATCH", result["issues"])


if __name__ == "__main__":
    unittest.main()
