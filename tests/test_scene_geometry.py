import tempfile
import unittest
from pathlib import Path

from ai.scene_geometry import (
    bind_scene_view, create_edit_region_mask, fixture_view_bboxes, normalize_scene_proxy,
    scene_proxy_signature, scene_proxy_issues,
)


class SceneGeometryTest(unittest.TestCase):
    def test_proxy_normalizes_fixed_fixture_coordinates(self):
        proxy = normalize_scene_proxy({
            "location_id":"laundry_interior",
            "fixtures":[{
                "id":"washer_back_01", "label":"后墙洗衣机",
                "bbox_xy":[0.7, 0.1, 0.22, 0.2], "height":"1.4",
                "view_bboxes":{"master":[0.65, 0.15, 0.2, 0.35]},
            }],
            "activity_bbox_xy":[0.2, 0.25, 0.5, 0.6],
        })
        self.assertEqual("normalized_topdown_xy_origin_top_left",
                         proxy["coordinate_system"])
        self.assertEqual("washer_back_01", proxy["fixtures"][0]["id"])
        self.assertEqual(1.4, proxy["fixtures"][0]["height"])
        self.assertEqual([[0.65, 0.15, 0.2, 0.35]],
                         fixture_view_bboxes(proxy, "master"))
        self.assertEqual(16, len(scene_proxy_signature(proxy)))

    def test_proxy_is_dirty_data_safe_and_preserves_nested_contract(self):
        proxy = normalize_scene_proxy({
            "location_id":"room_a",
            "scene_proxy":{
                "fixtures":[{"id":"desk", "bbox_xy":[0.2, 0.2, 0.3, 0.2]}],
                "walls":True,
                "camera_zones":5,
            },
        })
        self.assertEqual("room_a", proxy["location_id"])
        self.assertEqual("desk", proxy["fixtures"][0]["id"])
        self.assertEqual([], proxy["walls"])
        self.assertEqual([], proxy["camera_zones"])

    def test_percent_coordinates_migrate_and_collapsed_old_proxy_recovers(self):
        proxy = normalize_scene_proxy({
            "fixtures":[{
                "id":"table", "bbox_xy":[1, 1, .01, .01],
                "view_bboxes":{"master":[.08, .44, .32, .56]},
            }],
            "activity_bbox_xy":[1, 1, .01, .01],
            "camera_zones":[{"id":"front", "bbox_xy":[18, 0, 82, 22]}],
        })
        self.assertNotEqual([1.0, 1.0, .01, .01],
                            proxy["fixtures"][0]["bbox_xy"])
        self.assertNotEqual([1.0, 1.0, .01, .01],
                            proxy["activity_bbox_xy"])
        self.assertEqual([.18, 0.0, .82, .22],
                         proxy["camera_zones"][0]["bbox_xy"])
        self.assertEqual([], scene_proxy_issues(proxy))

    def test_shot_binds_to_declared_or_inferred_authority_view(self):
        views = {"master", "reverse", "left", "right"}
        self.assertEqual("right", bind_scene_view({"scene_view_id":"right"}, views))
        self.assertEqual("reverse", bind_scene_view({"camera_position":"门口反打"}, views))
        self.assertEqual("master", bind_scene_view({"camera_position":"入口正面"}, views))

    def test_edit_mask_only_opens_declared_activity_region(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow unavailable")
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.png"
            Image.new("RGB", (100, 100), "white").save(source)
            mask_path = create_edit_region_mask(
                str(source), [0.3, 0.3, 0.4, 0.4], temp,
                protected_bboxes=[[0.45, 0.45, 0.1, 0.1]], feather=0)
            self.assertTrue(mask_path)
            with Image.open(mask_path) as mask:
                alpha = mask.getchannel("A")
                self.assertEqual(0, alpha.getpixel((35, 35)))
                self.assertEqual(255, alpha.getpixel((50, 50)))
                self.assertEqual(255, alpha.getpixel((5, 5)))


if __name__ == "__main__":
    unittest.main()
