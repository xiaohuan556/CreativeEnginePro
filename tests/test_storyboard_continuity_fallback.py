import tempfile
import unittest
from pathlib import Path

try:
    from ui.script_workbench import ScriptWorkbench
    UI_AVAILABLE = True
except ModuleNotFoundError:
    UI_AVAILABLE = False


@unittest.skipUnless(UI_AVAILABLE, "script workbench dependencies are unavailable")
class StoryboardContinuityFallbackTests(unittest.TestCase):
    @staticmethod
    def _owner(board):
        owner = type("Owner", (), {})()
        owner._storyboard = board
        owner._find_shot = lambda shot_id: next(
            (shot for shot in board["shots"] if shot["id"] == shot_id), None)
        owner._selected_image_path = ScriptWorkbench._selected_image_path
        owner._shot_scene_identity = lambda shot: (
            ScriptWorkbench._shot_scene_identity(owner, shot))
        return owner

    def test_adjacent_same_scene_cut_does_not_reuse_previous_composition(self):
        with tempfile.TemporaryDirectory() as temp:
            first_path = str(Path(temp) / "first.png")
            Path(first_path).touch()
            first = {
                "id": "shot_1", "number": 1, "scene_asset_id": "scene_real",
                "continuity_group": "wrong_group_a",
                "video_link_mode": "cut",
                "selected_asset": first_path,
                "assets": [{"path": first_path, "kind": "image"}],
            }
            second = {
                "id": "shot_2", "number": 2, "scene_asset_id": "scene_real",
                "continuity_group": "wrong_group_b", "assets": [],
                "previous_shot_id": "shot_1",
            }
            board = {"shots": [first, second], "visual_bible": {}}
            owner = self._owner(board)
            path, shot_id = ScriptWorkbench._continuity_anchor_for_shot(owner, second)
            self.assertEqual("", path)
            self.assertEqual("", shot_id)

    def test_continue_shot_reuses_previous_keyframe_even_if_groups_differ(self):
        with tempfile.TemporaryDirectory() as temp:
            first_path = str(Path(temp) / "first.png")
            Path(first_path).touch()
            first = {
                "id": "shot_1", "number": 1, "scene_asset_id": "scene_real",
                "continuity_group": "group_a", "video_link_mode": "continue",
                "selected_asset": first_path,
                "assets": [{"path": first_path, "kind": "image"}],
            }
            second = {
                "id": "shot_2", "number": 2, "scene_asset_id": "scene_real",
                "continuity_group": "group_b", "generation_mode": "derive_from_anchor",
                "previous_shot_id": "shot_1", "assets": [],
            }
            board = {"shots": [first, second], "visual_bible": {}}
            owner = self._owner(board)
            path, shot_id = ScriptWorkbench._continuity_anchor_for_shot(owner, second)
            self.assertEqual(first_path, path)
            self.assertEqual("shot_1", shot_id)

    def test_image_anchor_survives_when_generated_video_is_selected(self):
        with tempfile.TemporaryDirectory() as temp:
            image_path = str(Path(temp) / "approved.png")
            video_path = str(Path(temp) / "generated.mp4")
            Path(image_path).touch()
            Path(video_path).touch()
            shot = {
                "selected_asset": video_path,
                "anchor_frame_id": image_path,
                "assets": [
                    {"path": image_path, "kind": "image"},
                    {"path": video_path, "kind": "video"},
                ],
            }
            self.assertEqual(
                image_path, ScriptWorkbench._selected_image_path(shot))

    def test_image_and_video_final_selections_are_independent(self):
        with tempfile.TemporaryDirectory() as temp:
            image_path = str(Path(temp) / "approved.png")
            video_path = str(Path(temp) / "approved.mp4")
            Path(image_path).touch()
            Path(video_path).touch()
            shot = {
                "selected_asset": video_path,
                "preview_asset": video_path,
                "selected_image_asset": image_path,
                "selected_video_asset": video_path,
                "anchor_frame_id": image_path,
                "assets": [
                    {"path": image_path, "kind": "image"},
                    {"path": video_path, "kind": "video"},
                ],
            }
            self.assertEqual(image_path, ScriptWorkbench._selected_image_path(shot))
            self.assertEqual(video_path, ScriptWorkbench._selected_video_path(shot))

    def test_binding_snapshot_ignores_dynamic_continuity_anchor(self):
        snapshot = {
            "scene_id": "scene_1",
            "scene_version": 1,
            "character_ids": ["robot_1"],
            "anchor_source_shot_id": "shot_1",
            "anchor_frame_path": "old-first-frame.png",
        }
        expected = {
            "scene_id": "scene_1",
            "scene_version": 1,
            "character_ids": ["robot_1"],
            "anchor_source_shot_id": "",
            "anchor_frame_path": "",
        }
        self.assertTrue(
            ScriptWorkbench._binding_snapshot_matches(snapshot, expected))

        expected["scene_version"] = 2
        self.assertFalse(
            ScriptWorkbench._binding_snapshot_matches(snapshot, expected))

    def test_video_end_frame_requires_explicit_bridge_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            second_path = str(Path(temp) / "second.png")
            Path(second_path).touch()
            first = {
                "id": "shot_1", "scene_asset_id": "scene_real",
                "continuity_group": "wrong_group_a", "assets": [],
                "video_link_mode": "bridge",
            }
            second = {
                "id": "shot_2", "scene_asset_id": "scene_real",
                "continuity_group": "wrong_group_b",
                "selected_asset": second_path,
                "assets": [{"path": second_path, "kind": "image"}],
            }
            board = {"shots": [first, second], "visual_bible": {}}
            owner = self._owner(board)
            path, shot_id = ScriptWorkbench._next_keyframe_for_shot(owner, first)
            self.assertEqual(second_path, path)
            self.assertEqual("shot_2", shot_id)

    def test_same_scene_cut_does_not_force_next_keyframe(self):
        with tempfile.TemporaryDirectory() as temp:
            second_path = str(Path(temp) / "second.png")
            Path(second_path).touch()
            first = {
                "id": "shot_1", "scene_asset_id": "scene_real",
                "continuity_group": "same_group", "assets": [],
                "video_link_mode": "cut",
            }
            second = {
                "id": "shot_2", "scene_asset_id": "scene_real",
                "continuity_group": "same_group",
                "selected_asset": second_path,
                "assets": [{"path": second_path, "kind": "image"}],
            }
            board = {"shots": [first, second], "visual_bible": {}}
            owner = self._owner(board)
            path, shot_id = ScriptWorkbench._next_keyframe_for_shot(owner, first)
            self.assertEqual("", path)
            self.assertEqual("", shot_id)


if __name__ == "__main__":
    unittest.main()
