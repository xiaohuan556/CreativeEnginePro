import unittest

from ai.scene_stage import (
    COORDINATE_SYSTEM, STAGE_SCHEMA, active_camera, append_stage_capture,
    normalize_scene_stage, project_world_point, stage_shot_contract,
)


class SceneStageContractTest(unittest.TestCase):
    def test_proxy_migrates_to_world_space_and_keeps_fixture_locked(self):
        shot = {
            "scene_view_id": "master",
            "character_names": ["王子"],
            "character_positions": [{"name": "王子", "start": "x=0.25 y=0.75"}],
        }
        stage = normalize_scene_stage({}, proxy={
            "location_id": "castle",
            "fixtures": [{
                "id": "throne", "label": "王座", "bbox_xy": [.7, .1, .2, .25],
                "height": 1.8, "fixed": True,
            }],
        }, shot=shot)
        self.assertEqual(STAGE_SCHEMA, stage["schema"])
        self.assertEqual(COORDINATE_SYSTEM, stage["coordinate_system"])
        fixture = next(row for row in stage["objects"] if row["kind"] == "fixture")
        actor = next(row for row in stage["objects"] if row["kind"] == "actor")
        self.assertTrue(fixture["locked"])
        self.assertEqual("王座", fixture["name"])
        self.assertEqual([-2.5, 0.0, 2.0], actor["transform"]["position"])
        self.assertEqual(1, len(stage["cameras"]))

    def test_existing_stage_is_dirty_data_safe(self):
        stage = normalize_scene_stage({
            "room": {"width": "bad", "depth": None},
            "objects": [{
                "name": "A", "kind": "actor",
                "transform": {"position": ["bad", None, float("nan")]},
            }],
            "cameras": [{"name": "CAM", "fov": "bad", "target": True}],
        })
        self.assertEqual(10.0, stage["room"]["width"])
        self.assertEqual([0.0, 0.0, 0.0], stage["objects"][0]["transform"]["position"])
        self.assertEqual(45.0, stage["cameras"][0]["fov"])

    def test_camera_projection_places_target_near_frame_center(self):
        stage = normalize_scene_stage({})
        camera = active_camera(stage)
        projected = project_world_point(camera["target"], camera, (1280, 720))
        self.assertTrue(projected["visible"])
        self.assertAlmostEqual(.5, projected["x"], places=3)
        self.assertAlmostEqual(.5, projected["y"], places=3)
        right = project_world_point([1, 1.1, 0], camera, (1280, 720))
        high = project_world_point([0, 2.1, 0], camera, (1280, 720))
        self.assertGreater(right["x"], projected["x"])
        self.assertLess(high["y"], projected["y"])

    def test_stage_compiles_actor_and_camera_contract(self):
        stage = normalize_scene_stage({}, shot={"character_names": ["A", "B"]})
        contract = stage_shot_contract(stage)
        self.assertTrue(contract["blocking_ready"])
        self.assertEqual(2, len(contract["character_positions"]))
        self.assertEqual(active_camera(stage)["id"], contract["camera_id"])
        self.assertIn("FOV=", contract["camera_position"])

    def test_empty_establishing_stage_is_blocking_ready(self):
        contract = stage_shot_contract(normalize_scene_stage({}))
        self.assertTrue(contract["blocking_ready"])
        self.assertEqual([], contract["character_positions"])

    def test_shot_size_changes_camera_fov_and_named_fixture_changes_target(self):
        proxy = {"fixtures":[{
            "id":"table", "label":"左侧石桌", "bbox_xy":[.1, .5, .25, .2],
            "height":1.1,
        }]}
        wide = normalize_scene_stage({}, proxy=proxy, shot={
            "shot_size":"大全景", "scene_view_id":"master", "visual":"完整石厅"})
        close = normalize_scene_stage({}, proxy=proxy, shot={
            "shot_size":"近中景", "scene_view_id":"left", "visual":"左侧石桌占据前景"})
        self.assertGreater(active_camera(wide)["fov"], active_camera(close)["fov"])
        self.assertNotEqual([0.0, 1.1, 0.0], active_camera(close)["target"])

    def test_capture_versions_and_snapshots_stage(self):
        stage = normalize_scene_stage({})
        updated = append_stage_capture(stage, "C:/tmp/frame.png")
        self.assertEqual(2, updated["version"])
        self.assertEqual("C:/tmp/frame.png", updated["captures"][-1]["path"])
        self.assertTrue(updated["captures"][-1]["object_transforms"] == {})


if __name__ == "__main__":
    unittest.main()
