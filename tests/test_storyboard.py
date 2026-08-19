import unittest

from ai.storyboard import (
    apply_dialogue_audio_duration, build_shot_contract, normalize_storyboard,
    resolve_video_link_mode, route_shot_generation, sync_legacy_bindings,
)


class StoryboardNormalizationTests(unittest.TestCase):
    def test_legacy_selection_migrates_to_typed_final_slot(self):
        board = normalize_storyboard({
            "shots": [{
                "scene": "测试镜头", "duration": 4,
                "assets": [
                    {"path": "frame.png", "kind": "image"},
                    {"path": "clip.mp4", "kind": "video"},
                ],
                "selected_asset": "clip.mp4",
                "anchor_frame_id": "frame.png",
            }]
        })
        shot = board["shots"][0]
        self.assertEqual("frame.png", shot["selected_image_asset"])
        self.assertEqual("clip.mp4", shot["selected_video_asset"])
        self.assertEqual("clip.mp4", shot["preview_asset"])

    def test_migrates_legacy_bindings_to_schema_v2(self):
        board = normalize_storyboard({
            "shots": [{
                "scene": "客厅里交谈",
                "duration": 4,
                "scene_id": "scene-living-room",
                "character_id": "character-a",
                "character_ids": ["character-b"],
                "element_id": "prop-phone",
                "element_mode": "exact",
                "element_placement": "右手",
            }]
        })

        self.assertEqual(board["schema_version"], 3)
        shot = board["shots"][0]
        self.assertEqual(shot["scene_asset_id"], "scene-living-room")
        self.assertEqual(
            [item["asset_id"] for item in shot["character_bindings"]],
            ["character-a", "character-b"],
        )
        self.assertEqual(shot["element_bindings"][0]["asset_id"], "prop-phone")
        self.assertEqual(shot["element_bindings"][0]["placement"], "右手")

    def test_preserves_gpt_screenplay_and_builds_executable_shot_contract(self):
        board = normalize_storyboard({
            "summary": "女孩发现手机里的世界正在泄漏到现实。",
            "production_bible": {
                "logline": "女孩必须在午夜前关闭失控的壁纸世界。",
                "visual_style": "高对比霓虹动漫",
                "continuity_rules": ["女孩始终穿黄色雨衣"],
            },
            "screenplay": {
                "hook": "手机里的雨先落进了现实。",
                "conflict": "壁纸世界不断扩大。",
                "ending": "她关闭手机，身后却再次响起雨声。",
                "beats": [{
                    "id": "beat_01", "start": 0, "end": 4,
                    "purpose": "视觉钩子", "summary": "雨滴穿出屏幕",
                    "entry_state": "房间干燥", "exit_state": "桌面被雨打湿",
                }],
            },
            "shots": [{
                "duration": 4, "beat_id": "beat_01",
                "dramatic_purpose": "建立超自然钩子",
                "entry_state": "手机静止", "exit_state": "雨滴落上桌面",
                "scene": "手机屏幕中的雨滴落到现实桌面",
                "scene_name": "女孩卧室夜晚", "scene_asset_id": "scene_1",
                "character_names": ["艾米"],
                "character_bindings": [{"asset_id": "char_1", "name": "艾米"}],
                "element_names": ["红色手机"],
                "element_bindings": [{"asset_id": "prop_1", "name": "红色手机"}],
            }],
        }, 4)
        self.assertEqual(3, board["schema_version"])
        self.assertEqual(
            "女孩必须在午夜前关闭失控的壁纸世界。",
            board["production_bible"]["logline"])
        self.assertEqual("手机里的雨先落进了现实。", board["screenplay"]["hook"])
        self.assertEqual("beat_01", board["screenplay"]["beats"][0]["id"])
        shot = board["shots"][0]
        contract = build_shot_contract(shot)
        self.assertEqual("scene_1", contract["scene"]["asset_id"])
        self.assertEqual(["char_1"], [item["asset_id"] for item in contract["characters"]])
        self.assertEqual(["prop_1"], [item["asset_id"] for item in contract["elements"]])
        self.assertEqual("建立超自然钩子", contract["dramatic_purpose"])

    def test_preserves_structured_binding_state(self):
        board = normalize_storyboard({
            "shots": [{
                "scene": "雨夜街道",
                "duration": 4,
                "scene_asset_id": "scene-rain",
                "scene_version": 3,
                "character_bindings": [{
                    "asset_id": "monster-1",
                    "version": 4,
                    "role": "lead",
                    "outfit_state": "wet_coat",
                    "appearance_state": "injured",
                }],
            }]
        })
        shot = board["shots"][0]
        self.assertEqual(shot["scene_version"], 3)
        self.assertEqual(shot["character_bindings"][0]["version"], 4)
        self.assertEqual(shot["character_bindings"][0]["outfit_state"], "wet_coat")
        self.assertEqual(shot["character_id"], "monster-1")

    def test_preserves_unresolved_asset_names_for_one_click_preparation(self):
        board = normalize_storyboard({
            "shots": [{
                "scene": "白色机器人把旧录音机放到桌面中央",
                "duration": 4,
                "scene_name": "雨夜霓虹客厅",
                "character_bindings": [{
                    "asset_id": "", "name": "白色机器人", "role": "lead",
                }],
                "element_bindings": [{
                    "asset_id": "", "name": "旧录音机", "mode": "exact",
                }],
            }]
        })
        shot = board["shots"][0]
        self.assertEqual(["白色机器人"], shot["character_names"])
        self.assertEqual(["旧录音机"], shot["element_names"])
        self.assertEqual([], shot["character_bindings"])
        self.assertEqual([], shot["element_bindings"])

    def test_builds_anchor_chain_inside_continuity_group(self):
        board = normalize_storyboard({
            "shots": [
                {"scene": "室内远景", "duration": 4,
                 "scene_asset_id": "scene-a"},
                {"scene": "室内近景", "duration": 4,
                 "scene_asset_id": "scene-a"},
                {"scene": "室外", "duration": 4,
                 "scene_asset_id": "scene-b"},
            ]
        })
        first, second, third = board["shots"]
        self.assertEqual(second["previous_shot_id"], first["id"])
        self.assertEqual(second["anchor_source_shot_id"], first["id"])
        self.assertEqual(second["generation_mode"], "derive_from_anchor")
        self.assertEqual(first["next_shot_id"], second["id"])
        self.assertEqual(third["previous_shot_id"], "")
        self.assertEqual(third["generation_mode"], "compose_from_assets")

    def test_long_shot_split_keeps_one_continuity_group(self):
        board = normalize_storyboard({
            "shots": [{"scene": "连续追逐", "duration": 18}]
        })
        self.assertEqual([shot["duration"] for shot in board["shots"]], [8.0, 8.0, 2.0])
        groups = {shot["continuity_group"] for shot in board["shots"]}
        self.assertEqual(len(groups), 1)
        self.assertEqual(board["shots"][1]["previous_shot_id"], board["shots"][0]["id"])
        self.assertEqual(board["shots"][0]["video_link_mode"], "continue")
        self.assertEqual(board["shots"][1]["video_link_mode"], "continue")
        self.assertEqual(board["shots"][2]["video_link_mode"], "auto")
        self.assertEqual(
            resolve_video_link_mode(board["shots"][0], board["shots"][1]),
            "continue",
        )

    def test_same_scene_defaults_to_direct_cut(self):
        board = normalize_storyboard({
            "shots": [
                {"scene": "客厅全景", "duration": 4,
                 "scene_asset_id": "scene-a", "camera_slot": "MASTER"},
                {"scene": "人物特写", "duration": 4,
                 "scene_asset_id": "scene-a", "camera_slot": "A"},
            ]
        })
        first, second = board["shots"]
        self.assertEqual(first["video_link_mode"], "auto")
        self.assertEqual(resolve_video_link_mode(first, second), "cut")

    def test_explicit_first_last_bridge_is_preserved(self):
        board = normalize_storyboard({
            "shots": [
                {"scene": "白天房间", "duration": 4,
                 "video_link_mode": "bridge"},
                {"scene": "夜晚房间", "duration": 4},
            ]
        })
        first, second = board["shots"]
        self.assertEqual(first["video_link_mode"], "bridge")
        self.assertEqual(resolve_video_link_mode(first, second), "bridge")

    def test_binding_sync_updates_legacy_fields(self):
        shot = {
            "character_bindings": [{"asset_id": "a"}, {"asset_id": "b"}],
            "element_bindings": [{"asset_id": "p", "mode": "reference",
                                  "placement": "桌面"}],
            "scene_asset_id": "s",
        }
        sync_legacy_bindings(shot)
        self.assertEqual(shot["character_id"], "a")
        self.assertEqual(shot["character_ids"], ["b"])
        self.assertEqual(shot["element_mode"], "reference")
        self.assertEqual(shot["scene_id"], "s")

    def test_dialogue_performance_is_structured_and_routed(self):
        board = normalize_storyboard({
            "shots": [{
                "scene": "女孩看向朋友说话", "duration": 4,
                "character_names": ["女孩", "朋友"],
                "performance": {
                    "line_type": "dialogue", "speaker": "女孩",
                    "dialogue": "你听见了吗？", "emotion": "紧张",
                    "emotion_intensity": 0.8, "gaze_target": "朋友",
                    "gesture": "抓紧杯子",
                },
            }],
        })
        shot = board["shots"][0]
        self.assertEqual("dialogue", shot["performance"]["line_type"])
        self.assertEqual("女孩", shot["performance"]["speaker"])
        self.assertEqual("dialogue_performance", shot["generation_route"])
        self.assertEqual("dialogue_performance", route_shot_generation(shot))

    def test_legacy_speaker_label_is_treated_as_dialogue(self):
        board = normalize_storyboard({
            "shots": [{
                "scene": "机器人近景", "duration": 4,
                "voiceover": "机器人：别碰那个按钮。",
            }],
        })
        performance = board["shots"][0]["performance"]
        self.assertEqual("dialogue", performance["line_type"])
        self.assertEqual("机器人", performance["speaker"])

    def test_real_dialogue_duration_reflows_following_shots(self):
        board = normalize_storyboard({
            "shots": [
                {
                    "scene": "角色说话", "duration": 4,
                    "performance": {
                        "line_type": "dialogue", "dialogue": "你好。",
                        "pause_before": 0.2, "pause_after": 0.3,
                    },
                },
                {"scene": "听者反应", "duration": 4},
            ],
        }, 8)
        first = apply_dialogue_audio_duration(
            board, board["shots"][0]["id"], 2.5)
        self.assertIsNotNone(first)
        self.assertEqual(3.0, board["shots"][0]["duration"])
        self.assertEqual(3.0, board["shots"][1]["start"])
        self.assertEqual(7.0, board["duration"])
        self.assertFalse(first["performance"]["needs_dialogue_split"])

    def test_dialogue_longer_than_video_limit_is_flagged(self):
        board = normalize_storyboard({
            "shots": [{
                "scene": "角色长对白", "duration": 8,
                "performance": {
                    "line_type": "dialogue", "dialogue": "很长的台词",
                    "pause_after": 0.4,
                },
            }],
        })
        first = apply_dialogue_audio_duration(
            board, board["shots"][0]["id"], 8.1)
        self.assertTrue(first["performance"]["needs_dialogue_split"])
        self.assertEqual(8.5, first["duration"])


if __name__ == "__main__":
    unittest.main()
