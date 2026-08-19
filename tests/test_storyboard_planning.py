import unittest

from ai.storyboard import extract_json
from ai.storyboard_planning import (
    batch_key,
    checkpoint_matches,
    checkpoint_progress,
    foundation_messages,
    foundation_repair_messages,
    merge_checkpoint,
    new_planning_checkpoint,
    next_missing_batch,
    normalize_foundation,
    normalize_shot_batch,
    parse_duration_seconds,
    planning_fingerprint,
    shot_batch_messages,
    shot_batch_repair_messages,
    shot_batch_ranges,
)


class StoryboardPlanningTests(unittest.TestCase):
    def _foundation(self, count=8):
        return normalize_foundation({
            "title": "最后一条锁链",
            "summary": "王子释放古龙",
            "visual_bible": "欧美写实奇幻",
            "characters": [{"name": "罗文", "description": "深蓝斗篷"}],
            "scenes": [{
                "name": "信标大厅", "location_id": "beacon_hall",
                "description": "中央石台，北侧信标",
                "states": [{"name": "暴风夜", "description": "橙色火光"}],
            }],
            "elements": [{"name": "王室印章", "description": "银色"}],
            "shot_outline": [{
                "shot_number": index,
                "scene_name": "信标大厅",
                "scene_state": "暴风夜",
                "visual": f"镜头 {index}",
            } for index in range(1, count + 1)],
        }, count)

    def test_eight_shots_are_bounded_into_four_detail_calls(self):
        self.assertEqual([(1, 2), (3, 4), (5, 6), (7, 8)],
                         shot_batch_ranges(8, 2))
        messages = foundation_messages("王子与龙", 8, "电影写实", 0.5)
        self.assertIn("不要展开逐镜摄影", messages[0]["content"])
        self.assertIn("正好 8 项", messages[0]["content"])

    def test_odd_shot_count_keeps_partial_final_batch(self):
        self.assertEqual([(1, 2), (3, 4), (5, 6), (7, 7)],
                         shot_batch_ranges(7, 2))
        messages = foundation_messages("黎明之钟", 7, "电影写实", 0.5)
        self.assertIn("正好 7 项", messages[0]["content"])

    def test_auto_shot_count_accepts_model_decision_and_explains_constraints(self):
        foundation = self._foundation(5)
        normalized = normalize_foundation(foundation, 0)
        self.assertEqual(5, len(normalized["shot_outline"]))
        messages = foundation_messages("雨夜归人", 0, "电影写实", 0.5)
        self.assertIn("根据定稿自动决定", messages[0]["content"])
        self.assertIn("1–24", messages[0]["content"])

        with self.assertRaises(ValueError):
            normalize_foundation(foundation, 4)

    def test_checkpoint_resumes_only_the_missing_batch(self):
        fingerprint = planning_fingerprint(
            "王子与龙", 8, "电影写实", "openai", "gpt-5.5", 0.5)
        checkpoint = new_planning_checkpoint(
            fingerprint=fingerprint, shot_count=8, style="电影写实",
            provider="openai", model="gpt-5.5", temperature=0.5)
        checkpoint["foundation"] = self._foundation()
        checkpoint["batches"][batch_key(1, 2)] = [
            {"shot_number": 1}, {"shot_number": 2}]
        checkpoint["batches"][batch_key(3, 4)] = [
            {"shot_number": 3}, {"shot_number": 4}]

        self.assertTrue(checkpoint_matches(checkpoint, fingerprint))
        self.assertEqual((4, 8), checkpoint_progress(checkpoint))
        self.assertEqual((5, 6), next_missing_batch(checkpoint))

    def test_batch_merge_preserves_outline_and_global_order(self):
        foundation = self._foundation(4)
        checkpoint = new_planning_checkpoint(
            fingerprint="same", shot_count=4, style="电影写实",
            provider="openai", model="gpt-5.5", temperature=0.5)
        checkpoint["foundation"] = foundation
        for start, end in shot_batch_ranges(4, 2):
            value = {"shots": [{
                "shot_number": number,
                "camera_position": f"机位 {number}",
                "primary_action": f"动作 {number}",
            } for number in range(start, end + 1)]}
            checkpoint["batches"][batch_key(start, end)] = normalize_shot_batch(
                value, foundation, start, end)

        merged = merge_checkpoint(checkpoint)
        self.assertEqual([1, 2, 3, 4],
                         [row["shot_number"] for row in merged["shots"]])
        self.assertEqual("镜头 3", merged["shots"][2]["visual"])
        self.assertEqual("机位 3", merged["shots"][2]["camera_position"])
        self.assertNotIn("shot_outline", merged)

    def test_detail_prompt_has_global_context_but_requests_only_one_batch(self):
        foundation = self._foundation(8)
        messages = shot_batch_messages(
            "完整剧本", foundation, 3, 4, "电影写实")
        self.assertIn("全局镜号 3 到 4", messages[0]["content"])
        self.assertIn("不得输出本批次之外的镜头", messages[0]["content"])
        self.assertIn('"shot_number": 3', messages[1]["content"])
        self.assertIn('"shot_number": 4', messages[1]["content"])
        self.assertIn('"full_shot_outline"', messages[1]["content"])
        self.assertIn('"requested_outline"', messages[1]["content"])

    def test_incomplete_foundation_is_rejected_before_asset_creation(self):
        with self.assertRaisesRegex(ValueError, "应为 8 镜"):
            normalize_foundation({
                "scenes": [{"name": "大厅"}],
                "shot_outline": [{"visual": "只有一镜"}],
            }, 8)

    def test_json_extractor_accepts_content_parts_trailing_comma_and_wrappers(self):
        value = extract_json([{
            "type":"text",
            "text":"```json\n{\"storyboard\":{\"scenes\":[{\"name\":\"大厅\"}],\"shots\":[{\"visual\":\"进入\"}],},}\n```",
        }])
        normalized = normalize_foundation(value, 1)
        self.assertEqual("大厅", normalized["scenes"][0]["name"])
        self.assertEqual("进入", normalized["shot_outline"][0]["visual"])

    def test_foundation_alias_maps_are_normalized_without_another_request(self):
        normalized = normalize_foundation({
            "project": {
                "cast": {"罗文":{"description":"蓝斗篷"}},
                "locations": {"信标大厅":{"description":"中央石台"}},
                "props": {"印章":"银色"},
                "shot_list": [{"location":"信标大厅", "action":"王子进入"}],
            }
        }, 1)
        self.assertEqual("罗文", normalized["characters"][0]["name"])
        self.assertEqual("信标大厅", normalized["shot_outline"][0]["scene_name"])
        self.assertEqual("王子进入", normalized["shot_outline"][0]["visual"])

    def test_repair_prompts_are_bounded_and_keep_the_required_range(self):
        foundation = self._foundation(4)
        foundation_messages_value = foundation_repair_messages(
            "原剧本", "x" * 50000, "缺少 scenes", 4, "电影写实")
        self.assertIn("正好 4 项", foundation_messages_value[0]["content"])
        self.assertLess(len(foundation_messages_value[1]["content"]), 26000)
        batch_messages_value = shot_batch_repair_messages(
            "原剧本", foundation, "坏结果", "少一镜", 3, 4, "电影写实")
        self.assertIn("全局镜号 3 到 4", batch_messages_value[0]["content"])

    def test_duration_with_human_units_is_normalized_before_checkpoint_save(self):
        self.assertEqual(6.0, parse_duration_seconds("6秒"))
        self.assertEqual(6.0, parse_duration_seconds("约 6s"))
        self.assertEqual(6.0, parse_duration_seconds("六秒"))
        self.assertEqual(12.0, parse_duration_seconds("十二秒"))
        self.assertEqual(2.5, parse_duration_seconds("2.5 秒左右"))
        foundation = self._foundation(2)
        rows = normalize_shot_batch({"shots": [
            {"shot_number":1, "duration":"6秒"},
            {"shot_number":2, "duration":"约 2.5s"},
        ]}, foundation, 1, 2)
        self.assertEqual([6.0, 2.5], [row["duration"] for row in rows])

    def test_unreadable_duration_has_chinese_validation_message(self):
        with self.assertRaisesRegex(ValueError, "镜头时长.*无法识别"):
            parse_duration_seconds("几秒")


if __name__ == "__main__":
    unittest.main()
