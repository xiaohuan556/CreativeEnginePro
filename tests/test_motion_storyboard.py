import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from ai.motion_storyboard import (
    assemble_motion_storyboard, inspect_motion_panels, motion_panel_prompt,
    motion_panels_ready,
)


class MotionStoryboardTests(unittest.TestCase):
    def _shot(self):
        return {
            "id":"shot-1", "shot_size":"中景", "camera_position":"南侧固定机位",
            "axis_rule":"人物始终向画面右侧移动", "spatial_layout":"石桌在左后方",
            "foreground":"门框", "midground":"人物", "background":"唯一石桌",
            "frame_start":"人物在门内左侧，披带在肩上",
            "frame_end":"人物到达画面中央，披带仍在肩上",
            "continuity_invariants":["同一人物", "唯一石桌位置不变"],
            "scene_proxy":{"fixtures":[{"id":"table", "name":"石桌"}]},
            "motion_keyframes":[
                {"index":1, "time_seconds":0.0, "label":"起步",
                 "composition":"人物位于左三分之一", "character_state":"左脚准备迈出",
                 "action":"后脚蹬地，前脚抬起", "camera_state":"固定机位",
                 "gaze_arrow":"看向画面右侧", "screen_direction":"向右"},
                {"index":2, "time_seconds":1.5, "label":"跨步",
                 "composition":"人物位于画面中央偏左", "character_state":"跨步中",
                 "action":"后脚离地，衣摆落后身体", "camera_state":"固定机位",
                 "gaze_arrow":"看向画面右侧", "screen_direction":"向右"},
                {"index":3, "time_seconds":3.0, "label":"站定",
                 "composition":"人物到达画面中央", "character_state":"双脚站稳",
                 "action":"重心落到前脚并停止", "camera_state":"固定机位",
                 "gaze_arrow":"看向画面右侧", "screen_direction":"向右"},
            ],
        }

    def test_prompt_describes_one_clean_native_panel(self):
        prompt = motion_panel_prompt(
            self._shot(), 0, 1, "欧美奇幻写实，冷蓝黎明", provider_name="seedream")
        self.assertIn("独立运动分镜画格 K2", prompt)
        self.assertIn("原生 16:9 横向画面", prompt)
        self.assertIn("参考图1是上一动作画格", prompt)
        self.assertNotIn("2 列", prompt)
        self.assertNotIn("3 列", prompt)
        self.assertNotIn("scene_proxy", prompt)
        self.assertLess(len(prompt), 1800)

    def test_local_composite_preserves_clean_panel_files(self):
        folder = Path(tempfile.mkdtemp())
        shot = self._shot()
        paths = []
        for index, color in enumerate(("#25354c", "#3d526e", "#576f8b")):
            path = folder / f"panel-{index}.png"
            image = Image.new("RGB", (1792, 1024), color)
            ImageDraw.Draw(image).rectangle(
                (160 + index * 180, 220, 430 + index * 180, 820), fill="#d0b080")
            image.save(path); paths.append(str(path))
        board = assemble_motion_storyboard(
            paths, shot["motion_keyframes"], folder, shot_id="shot-1",
            contract_version=5)
        self.assertTrue(Path(board).exists())
        self.assertNotIn(board, paths)
        shot["motion_panel_paths"] = paths
        self.assertTrue(motion_panels_ready(shot))

    def test_panel_qc_rejects_clone_sequence_and_wrong_ratio(self):
        folder = Path(tempfile.mkdtemp())
        shot = self._shot()
        paths = []
        for index in range(3):
            path = folder / f"clone-{index}.png"
            Image.new("RGB", (1200, 800), "#39495f").save(path)
            paths.append(str(path))
        result = inspect_motion_panels(paths, shot)
        self.assertEqual("fail", result["status"])
        self.assertIn("MOTION_PANELS_NEAR_DUPLICATE", result["issues"])
        self.assertIn("MOTION_PANEL_ASPECT_MISMATCH", result["issues"])

    def test_vertical_project_generates_and_checks_native_vertical_panels(self):
        folder = Path(tempfile.mkdtemp())
        shot = self._shot()
        paths = []
        for index, color in enumerate(("#273b52", "#465e76", "#6b8297")):
            path = folder / f"vertical-{index}.png"
            image = Image.new("RGB", (1152, 2048), color)
            ImageDraw.Draw(image).rectangle(
                (260, 240 + index * 230, 880, 720 + index * 230), fill="#d2aa73")
            image.save(path); paths.append(str(path))
        prompt = motion_panel_prompt(
            shot, 0, 0, "写实", aspect_ratio="9:16")
        self.assertIn("原生 9:16 竖向画面", prompt)
        self.assertIn("不得把横屏参考图居中裁成竖屏", prompt)
        result = inspect_motion_panels(paths, shot, "9:16")
        self.assertNotIn("MOTION_PANEL_ASPECT_MISMATCH", result["issues"])
        board = assemble_motion_storyboard(
            paths, shot["motion_keyframes"], folder, shot_id="vertical",
            contract_version=5, aspect_ratio="9:16")
        self.assertTrue(Path(board).exists())
        shot["motion_panel_paths"] = paths
        shot["motion_board_aspect_ratio"] = "9:16"
        self.assertTrue(motion_panels_ready(shot, "9:16"))
        self.assertFalse(motion_panels_ready(shot, "16:9"))

    def test_dirty_legacy_values_degrade_without_crashing(self):
        shot = self._shot()
        shot["motion_keyframes"][0]["time_seconds"] = "unknown"
        shot["scene_proxy"]["fixtures"] = True
        prompt = motion_panel_prompt(shot, 0, 0, "写实")
        self.assertIn("时间 0 秒", prompt)
        shot["motion_panel_paths"] = True
        self.assertFalse(motion_panels_ready(shot))
        shot["motion_keyframes"] = None
        self.assertEqual(
            "fail", inspect_motion_panels([], shot)["status"])


if __name__ == "__main__":
    unittest.main()
