import unittest

from ai.production_contracts import (
    capability_issues, edit_plan_issues, model_profile,
    normalize_edit_plan, normalize_sound_plan, sound_plan_issues,
)
from ai.production_intelligence import aggregate_generation_history, rank_models, rank_providers
from ai.production_runtime import compile_rough_cut, compile_sound_plan, recommend_provider


class ProductionContractsTest(unittest.TestCase):
    def test_sound_and_edit_contracts_are_executable_not_placeholders(self):
        shots = [{"id":"s1", "duration":3}, {"id":"s2", "duration":4}]
        sound = normalize_sound_plan({}, shots)
        edit = normalize_edit_plan({}, shots)
        self.assertEqual([], sound_plan_issues(sound, shots))
        self.assertEqual([], edit_plan_issues(edit, shots))
        sound["continuous_bed"] = ""
        edit["rhythm"] = ""
        self.assertTrue(sound_plan_issues(sound, shots))
        self.assertIn("EDIT_RHYTHM_MISSING", edit_plan_issues(edit, shots))
        sound["continuous_bed"] = "夜雨环境音贯穿"
        for row in sound["shots"]:
            row["room_tone"] = "室内底噪"
        edit["rhythm"] = "前慢后快"
        self.assertEqual([], sound_plan_issues(sound, shots))
        self.assertEqual([], edit_plan_issues(edit, shots))

    def test_model_adapter_rejects_unsupported_control_surface(self):
        profile = model_profile("kling")
        issues = capability_issues(profile, {
            "operation":"image_to_video", "last_frame":True,
            "reference_count":3, "native_audio":True,
        })
        self.assertEqual(3, len(issues))
        self.assertTrue(all(value.startswith("F11") for value in issues))

    def test_history_changes_model_routing_after_enough_evidence(self):
        events = ([{"model":"a", "outcome":"qc_failed", "cost":1}] * 4 +
                  [{"model":"b", "outcome":"passed", "adopted":True, "cost":2}] * 4)
        stats = aggregate_generation_history(events)
        ranked = rank_models(events, ["a", "b"])
        self.assertEqual(0.0, stats["a"]["pass_rate"])
        self.assertEqual("b", ranked[0]["model"])

    def test_provider_routing_keeps_model_version_separate(self):
        events = ([{"provider":"veo", "model":"veo-3.1", "outcome":"passed"}] * 4 +
                  [{"provider":"seedance", "model":"doubao-seedance-2.0",
                    "outcome":"qc_failed"}] * 4)
        ranked = rank_providers(events, ["veo", "seedance"])
        self.assertEqual("veo", ranked[0]["provider"])

    def test_routing_prefers_matching_shot_signature(self):
        context = {"shot_type":"近景", "people_bucket":"1", "duration_bucket":"short",
                   "strategy":"first_frame", "camera_complexity":"stable"}
        other = {**context, "shot_type":"全景"}
        events = ([{"provider":"veo", "model":"v", "outcome":"passed",
                    "shot_signature":context}] * 3 +
                  [{"provider":"seedance", "model":"s", "outcome":"passed",
                    "shot_signature":other}] * 10)
        decision = recommend_provider(events, ["veo", "seedance"], {
            "shot_size":"近景", "character_names":["A"], "duration":3,
            "keyframe_strategy":"first_frame", "camera":"固定"})
        self.assertEqual("veo", decision["provider"])

    def test_routing_keeps_registry_default_without_evidence(self):
        decision = recommend_provider([], ["seedance", "veo"], {"duration":3})
        self.assertEqual("", decision["provider"])
        self.assertEqual("insufficient_evidence", decision["reason"])

    def test_sound_and_rough_cut_compilers_produce_executable_layers(self):
        shots = [{"id":"s1", "duration":3, "action":"她跑向门口", "dialogue":"等等"},
                 {"id":"s2", "duration":2, "action":"门关闭"}]
        sound = compile_sound_plan(shots, genre_profile={"genre":"thriller"})
        edit = compile_rough_cut(shots, genre_profile={"genre":"thriller"})
        self.assertIn("急促脚步", sound["shots"][0]["foley"])
        self.assertEqual("l_cut", edit["timeline"][1]["audio_bridge"])
        dirty = compile_rough_cut([{"id":"dirty", "duration":"unknown"}])
        self.assertEqual(0.1, dirty["timeline"][0]["source_out"])

    def test_dirty_collection_fields_do_not_break_migration(self):
        shots = [{"id":"s1", "duration":"bad"}]
        sound = normalize_sound_plan({"shots":7}, shots)
        edit = normalize_edit_plan({"timeline":True}, shots)
        self.assertEqual([], sound_plan_issues(sound, shots))
        self.assertEqual([], edit_plan_issues(edit, shots))


if __name__ == "__main__":
    unittest.main()
