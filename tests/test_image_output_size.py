import unittest

from core.image_output_size import normalize_aspect_ratio, resolve_image_output_size


class ImageOutputSizeTests(unittest.TestCase):
    def test_gpt_aspects(self):
        self.assertEqual("auto", resolve_image_output_size("gptimage", "", "original"))
        self.assertEqual("1024x1792", resolve_image_output_size("gptimage", "", "9:16"))
        self.assertEqual("1024x1024", resolve_image_output_size("gptimage", "", "1:1"))
        self.assertEqual("1792x1024", resolve_image_output_size("gptimage", "", "16:9"))
        self.assertEqual("1024x1280", resolve_image_output_size("gptimage", "", "4:5"))

    def test_seedream_aspects_and_tiers(self):
        self.assertEqual("2K", resolve_image_output_size("seedream", "2K", "original"))
        self.assertEqual("1152x2048", resolve_image_output_size("seedream", "2K", "9:16"))
        self.assertEqual("4096x4096", resolve_image_output_size("seedream", "4K", "1:1"))
        self.assertEqual("1280x720", resolve_image_output_size("seedream", "1K", "16:9"))
        self.assertEqual("1632x2048", resolve_image_output_size("seedream", "2K", "4:5"))

    def test_legacy_aspect_labels_are_canonicalized(self):
        self.assertEqual("9:16", normalize_aspect_ratio("9：16"))
        self.assertEqual("9:16", normalize_aspect_ratio("竖屏"))
        self.assertEqual("16:9", normalize_aspect_ratio("unknown"))


if __name__ == "__main__":
    unittest.main()
