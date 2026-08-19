import unittest

from utils.video_mask import build_mask_alpha, evaluate_mask_values

try:
    from core.edit_engine import VideoClip
except ModuleNotFoundError:  # 轻量测试运行时可能不含 PyQt6
    VideoClip = None
try:
    import cv2  # noqa: F401
    import numpy  # noqa: F401
    HAS_IMAGE_DEPS = True
except ModuleNotFoundError:
    HAS_IMAGE_DEPS = False


class VideoMaskTests(unittest.TestCase):
    @unittest.skipUnless(HAS_IMAGE_DEPS, "requires numpy and OpenCV")
    def test_all_basic_shapes_have_visible_and_hidden_regions(self):
        for mask_type in ("linear", "mirror", "circle", "rectangle", "star", "heart"):
            with self.subTest(mask_type=mask_type):
                alpha = build_mask_alpha(120, 100, {
                    "mask_type": mask_type, "mask_x": 0, "mask_y": 0,
                    "mask_width": 0.6, "mask_height": 0.6,
                    "mask_rotation": 12, "mask_feather": 0,
                    "mask_inverted": False,
                })
                self.assertGreater(int(alpha.max()), 0)
                self.assertEqual(int(alpha.min()), 0)

    @unittest.skipUnless(HAS_IMAGE_DEPS, "requires numpy and OpenCV")
    def test_rectangle_keeps_center_and_hides_corner(self):
        alpha = build_mask_alpha(100, 80, {
            "mask_type": "rectangle", "mask_x": 0, "mask_y": 0,
            "mask_width": 0.5, "mask_height": 0.5,
            "mask_rotation": 0, "mask_feather": 0, "mask_inverted": False,
        })
        self.assertEqual(int(alpha[40, 50]), 255)
        self.assertEqual(int(alpha[0, 0]), 0)

    @unittest.skipUnless(HAS_IMAGE_DEPS, "requires numpy and OpenCV")
    def test_inverted_circle_hides_center(self):
        alpha = build_mask_alpha(100, 100, {
            "mask_type": "circle", "mask_x": 0, "mask_y": 0,
            "mask_width": 0.5, "mask_height": 0.5,
            "mask_rotation": 0, "mask_feather": 0, "mask_inverted": True,
        })
        self.assertEqual(int(alpha[50, 50]), 0)
        self.assertEqual(int(alpha[0, 0]), 255)

    @unittest.skipUnless(HAS_IMAGE_DEPS, "requires numpy and OpenCV")
    def test_feather_creates_soft_alpha(self):
        alpha = build_mask_alpha(120, 120, {
            "mask_type": "rectangle", "mask_x": 0, "mask_y": 0,
            "mask_width": 0.5, "mask_height": 0.5,
            "mask_rotation": 0, "mask_feather": 0.4, "mask_inverted": False,
        })
        self.assertTrue(((alpha > 0) & (alpha < 255)).any())

    @unittest.skipIf(VideoClip is None, "requires project PyQt runtime")
    def test_keyframed_position_is_interpolated(self):
        clip = VideoClip(mask_enabled=True, mask_x=0.0)
        clip.keyframes["mask_x"] = [(0.0, -1.0), (2.0, 1.0)]
        self.assertAlmostEqual(evaluate_mask_values(clip, 1.0)["mask_x"], 0.0)

    @unittest.skipIf(VideoClip is None, "requires project PyQt runtime")
    def test_mask_fields_survive_project_serialization(self):
        clip = VideoClip(
            mask_enabled=True, mask_type="heart", mask_x=0.2,
            mask_y=-0.1, mask_width=0.8, mask_height=0.7,
            mask_rotation=15, mask_feather=0.3, mask_inverted=True)
        restored = VideoClip.from_dict(clip.to_dict())
        self.assertTrue(restored.mask_enabled)
        self.assertEqual(restored.mask_type, "heart")
        self.assertAlmostEqual(restored.mask_feather, 0.3)
        self.assertTrue(restored.mask_inverted)


if __name__ == "__main__":
    unittest.main()
