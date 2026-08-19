import tempfile
import unittest
from pathlib import Path

from PIL import Image

from ai.quality_gate import inspect_candidate_group, inspect_image


class QualityGateTests(unittest.TestCase):
    def test_blank_image_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "blank.png"
            Image.new("RGB", (1024, 1024), "white").save(path)
            report = inspect_image(str(path), "1:1")
            self.assertEqual("reject", report["status"])
            self.assertTrue(report["problems"])

    def test_valid_bound_candidate_waits_for_semantic_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "candidate.png"
            image = Image.new("RGB", (900, 1600))
            pixels = image.load()
            for y in range(image.height):
                for x in range(image.width):
                    pixels[x, y] = ((x * 3) % 255, (y * 2) % 255, (x + y) % 255)
            image.save(path)
            report = inspect_image(
                str(path), "9:16", [{"role": "character"}, {"role": "scene"}])
            self.assertEqual("pending", report["status"])
            self.assertIn("主体身份、服装和比例", report["manual_checks"])

    def test_near_duplicate_candidates_are_marked(self):
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "one.png"
            second = Path(temp) / "two.png"
            image = Image.new("RGB", (700, 700))
            pixels = image.load()
            for y in range(image.height):
                for x in range(image.width):
                    pixels[x, y] = ((x + y) % 255, (x * 2) % 255, (y * 3) % 255)
            image.save(first)
            image.save(second)
            reports = inspect_candidate_group([str(first), str(second)], "1:1")
            self.assertEqual("warn", reports[str(second)]["status"])


if __name__ == "__main__":
    unittest.main()
