import unittest

from ai.director_protocol import (
    planning_instructions, normalize_director_contract,
    compile_video_direction, director_gate_issues, endpoint_pair_requested,
)


class DirectorProtocolTests(unittest.TestCase):
    def test_planning_contract_requires_directing_fields(self):
        text = planning_instructions(8, "电影写实")
        self.assertIn("story_function", text)
        self.assertIn("dominant_camera_move", text)
        self.assertIn("action_start → primary_action → action_end", text)
        self.assertIn("对象写成单一实例", text)
        self.assertIn("action_start 必须与 frame_start", text)
        self.assertIn("正好 8 镜", text)

    def test_old_shot_is_upgraded_to_director_contract(self):
        shot = {
            "visual": "女孩站在窗前",
            "action_line": "女孩缓慢回头",
            "frame_start": "背对镜头",
            "frame_end": "侧脸停在窗框中",
            "camera_movement": "缓慢推近",
        }
        contract = normalize_director_contract(shot)
        self.assertEqual("first_frame", contract["keyframe_strategy"])
        self.assertFalse(endpoint_pair_requested(shot))
        self.assertEqual("缓慢推近", contract["dominant_camera_move"])
        self.assertEqual(contract, shot["director_contract"])
        self.assertEqual([], director_gate_issues(shot))

    def test_video_direction_has_one_action_endpoint_and_invariants(self):
        shot = {
            "story_function": "揭示女孩听见门外异响",
            "action_start": "女孩背对门站立",
            "primary_action": "女孩缓慢转头看向门缝",
            "action_end": "侧脸对准门缝后停住",
            "dominant_camera_move": "固定机位，极慢推近",
            "continuity_invariants": ["白色睡衣不变", "窗外冷光来自画面左侧"],
            "keyframe_strategy": "first_last",
            "endpoint_pair_enabled": True,
            "generation_risk": "脸部漂移",
        }
        text = compile_video_direction(shot, "K1 0s 起势；K2 4s 落幅")
        self.assertIn("只执行一个连续变化", text)
        self.assertIn("动作结束并稳定在", text)
        self.assertIn("禁止叠加第二种运镜", text)
        self.assertIn("白色睡衣不变", text)
        self.assertIn("脸部漂移", text)
        self.assertTrue(endpoint_pair_requested(shot))

    def test_explicit_first_frame_strategy_is_preserved(self):
        shot = {
            "action_start": "人物坐在桌前",
            "primary_action": "手指轻敲桌面一次",
            "action_end": "手指停在桌面",
            "dominant_camera_move": "固定机位",
            "keyframe_strategy": "first_frame",
        }
        contract = normalize_director_contract(shot)
        self.assertEqual("first_frame", contract["keyframe_strategy"])
        self.assertIn("从已批准首帧", compile_video_direction(shot))


if __name__ == "__main__":
    unittest.main()
