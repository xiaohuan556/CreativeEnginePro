import tempfile
import unittest
from pathlib import Path

from ai.production_skills import (
    append_workflow_event,
    append_generation_event,
    build_repair_plan,
    discover_canvas_skills,
    evaluate_readiness,
    normalize_clip_qc,
    normalize_sequence_qc,
    plan_next_action,
    validate_skill_dependencies,
)


class ProductionSkillContractsTest(unittest.TestCase):
    def test_video_anchor_gate_requires_end_frame_per_shot_not_globally(self):
        folder = Path(tempfile.mkdtemp())
        start = folder / "start.png"
        end = folder / "end.png"
        start.write_bytes(b"image")
        end.write_bytes(b"image")
        board = {"shots": [{
            "id":"plate", "number":1, "keyframe_strategy":"first_frame",
            "endpoint_pair_required":False,
            "selected_image_asset":str(start),
        }, {
            "id":"action", "number":2, "keyframe_strategy":"first_last",
            "endpoint_pair_required":True,
            "selected_image_asset":str(start),
            "selected_end_image_asset":str(end),
        }]}
        report = evaluate_readiness(
            "video_anchors", board, require_end_frame=True)
        self.assertFalse(report.blocked)
        self.assertNotIn("END_FRAME_MISSING", {
            value.code for value in report.issues})

    def test_video_anchor_gate_still_blocks_required_shot_without_end(self):
        folder = Path(tempfile.mkdtemp())
        start = folder / "start.png"
        start.write_bytes(b"image")
        board = {"shots": [{
            "id":"action", "number":1, "keyframe_strategy":"first_last",
            "endpoint_pair_required":True,
            "selected_image_asset":str(start),
        }]}
        report = evaluate_readiness(
            "video_anchors", board, require_end_frame=True)
        self.assertTrue(report.blocked)
        self.assertIn("END_FRAME_MISSING", {
            value.code for value in report.blockers})

    def test_shot_plan_blocks_only_missing_executable_contracts(self):
        board = {"shots": [
            {"id": "s1", "number": 1, "duration": 4, "visual": "女孩推门"},
            {"id": "s2", "number": 2, "duration": 0, "visual": ""},
        ]}
        report = evaluate_readiness("shot_plan", board)
        self.assertTrue(report.blocked)
        self.assertIn("DURATION_INVALID", {value.code for value in report.blockers})
        self.assertIn("SCENE_UNSPECIFIED", {value.code for value in report.warnings})

    def test_character_authority_is_a_real_asset_gate(self):
        folder = Path(tempfile.mkdtemp())
        portrait = folder / "portrait.png"
        portrait.write_bytes(b"image")
        board = {"shots": [{"id": "s1", "duration": 3, "visual": "近景"}]}
        assets = [{
            "id": "character-1", "title": "阿青", "asset_kind": "character",
            "locked": True, "path": str(portrait),
            "character_reference_set": {"portrait": str(portrait)},
        }]
        report = evaluate_readiness("locked_assets", board, assets)
        self.assertTrue(report.blocked)
        self.assertEqual("CHARACTER_AUTHORITY_INCOMPLETE", report.blockers[0].code)

    def test_orchestrator_routes_blocked_gate_to_repair_and_keeps_trace(self):
        report = evaluate_readiness("prompts", {
            "shots": [{"id": "s1", "duration": 4, "visual": "跑", "production_ready": False}]
        })
        decision = plan_next_action("prompts_ready", report)
        self.assertFalse(decision["allowed"])
        self.assertEqual("repair", decision["action"])
        source = {}
        append_workflow_event(source, decision, status="blocked")
        append_workflow_event(source, decision, status="blocked")
        self.assertEqual(1, len(source["workflow_trace"]))

    def test_visual_qc_routes_only_the_failed_media_branch(self):
        shots = [
            {"id": "s1", "number": 1, "selected_video_asset": "a.mp4"},
            {"id": "s2", "number": 2, "selected_video_asset": "b.mp4"},
        ]
        review = {"score": 76, "shots": [
            {"id": "s1", "passed": False, "issues": ["人物越轴，站位反了"],
             "revision": "恢复左进右出"},
            {"id": "s2", "passed": True},
        ]}
        plan = build_repair_plan(review, shots)
        self.assertEqual(1, len(plan["items"]))
        self.assertEqual("blocking", plan["items"][0]["target"])
        self.assertEqual(3, plan["items"][0]["rewind_step"])
        self.assertNotIn("s2", {value["shot_id"] for value in plan["items"]})

    def test_clip_qc_uses_weighted_categories_and_blockers_override_score(self):
        review = {"shots": [{
            "id":"s1", "score":96, "passed":True,
            "categories":{"G1":100, "G2":90, "G3":90, "G4":90,
                          "G5":95, "G6":100},
            "blockers":["F2"], "issues":["人物越轴"],
            "repair_target":"blocking",
        }, {
            "id":"s2", "categories":{"G1":80, "G2":80, "G3":80,
                                       "G4":80, "G5":80, "G6":80},
        }]}
        result = normalize_clip_qc(review, ["s1", "s2"])
        self.assertFalse(result["passed"])
        self.assertFalse(result["shots"][0]["passed"])
        self.assertEqual(80, result["shots"][1]["score"])
        self.assertTrue(result["shots"][1]["passed"])

    def test_ambiguous_deterministic_qc_requires_human_review(self):
        result = normalize_clip_qc({"shots":[{
            "id":"s1", "score":99, "passed":True,
            "categories":{"G1":99, "G2":99, "G3":99, "G4":99, "G5":99, "G6":99},
        }]}, ["s1"], deterministic_qc={
            "status":"fail", "issues":["FREEZE_FRAME"]})
        self.assertFalse(result["passed"])
        self.assertEqual("review", result["severity"])
        self.assertTrue(result["review_required"])
        self.assertFalse(result["requires_regeneration"])
        self.assertNotIn("F9", result["shots"][0]["blockers"])
        self.assertIn("FREEZE_FRAME", result["shots"][0]["issue_codes"])

    def test_temporal_deterministic_failure_remains_a_hard_gate(self):
        result = normalize_clip_qc({"shots":[{
            "id":"s1", "score":99, "passed":True,
        }]}, ["s1"], deterministic_qc={
            "status":"fail", "issues":["TEMPORAL_FLICKER"]})
        self.assertFalse(result["passed"])
        self.assertEqual("block", result["severity"])
        self.assertTrue(result["requires_regeneration"])
        self.assertIn("F9", result["shots"][0]["blockers"])
        self.assertLess(result["score"], 65)

    def test_av_sync_failure_routes_to_f10(self):
        result = normalize_clip_qc({"shots":[{
            "id":"s1", "score":98, "passed":True,
            "categories":{"G1":98, "G2":98, "G3":98, "G4":98, "G5":98, "G6":98},
        }]}, ["s1"], deterministic_qc={
            "status":"fail", "issues":["AV_SYNC_OFFSET"]})
        self.assertFalse(result["passed"])
        self.assertIn("F10", result["shots"][0]["blockers"])

    def test_sequence_qc_requires_85_and_marks_only_failed_transition(self):
        result = normalize_sequence_qc({"transitions": [
            {"from_id":"s1", "to_id":"s2", "score":84, "passed":True,
             "issues":["出入画方向不连续"]},
            {"from_id":"s2", "to_id":"s3", "score":92, "passed":True},
        ]})
        self.assertFalse(result["passed"])
        self.assertFalse(result["transitions"][0]["passed"])
        self.assertEqual("review", result["transitions"][0]["severity"])
        self.assertFalse(result["transitions"][0]["requires_regeneration"])
        self.assertTrue(result["transitions"][1]["passed"])

    def test_score_below_65_is_a_hard_qc_block(self):
        result = normalize_clip_qc({"shots":[{
            "id":"s1", "score":64, "passed":True,
        }]}, ["s1"])
        self.assertEqual("block", result["severity"])
        self.assertTrue(result["requires_regeneration"])

    def test_orchestrator_exposes_video_qc_stages(self):
        self.assertEqual("wait_for_video_qc",
                         plan_next_action("video_qc_pending")["action"])
        self.assertEqual("review_video_qc",
                         plan_next_action("video_qc_review")["action"])

    def test_repository_skills_are_discovered_from_standard_metadata(self):
        skills = discover_canvas_skills()
        self.assertIn("production_orchestrator", skills)
        self.assertEqual("AI 制片编排", skills["production_orchestrator"]["title"])
        self.assertTrue(skills["production_orchestrator"]["skill_path"].endswith("SKILL.md"))
        self.assertEqual("1.1.0", skills["vision_qc_repair"]["version"])
        self.assertIn("vision", skills["vision_qc_repair"]["requires"]["capabilities"])

    def test_skill_manifest_dependencies_and_capabilities_are_enforced(self):
        skills = discover_canvas_skills()
        issues = validate_skill_dependencies(
            "vision_qc_repair", skills, artifacts=["rendered_media"])
        self.assertIn("CAPABILITY_MISSING:vision", issues)
        self.assertEqual([], validate_skill_dependencies(
            "vision_qc_repair", skills, capabilities=["vision"],
            artifacts=["rendered_media"]))

    def test_generation_trace_is_reproducible_without_storing_prompt(self):
        source = {}
        event = append_generation_event(source, {
            "provider":"openai", "model":"veo", "operation":"video",
            "prompt":"a private production prompt", "prompt_version":"v3",
            "references":["a.png", "b.png"], "attempt":2,
            "duration_ms":1200, "cost":0.5, "currency":"USD",
            "outcome":"passed", "adopted":True,
        })
        self.assertNotIn("prompt", event)
        self.assertEqual(64, len(event["prompt_sha256"]))
        self.assertEqual(2, event["reference_count"])
        self.assertTrue(event["adopted"])

    def test_delivery_surfaces_sound_and_edit_contract_gaps(self):
        folder = Path(tempfile.mkdtemp())
        videos = []
        shots = []
        for index in range(2):
            path = folder / f"v{index}.mp4"
            path.write_bytes(b"video")
            videos.append(path)
            shots.append({"id":f"s{index}", "number":index + 1,
                          "selected_video_asset":str(path)})
        report = evaluate_readiness("delivery", {"shots":shots})
        codes = {value.code for value in report.blockers}
        self.assertIn("SOUND_PLAN_MISSING", codes)
        self.assertIn("EDIT_PLAN_MISSING", codes)
        self.assertTrue(report.blocked)

    def test_generation_telemetry_tolerates_malformed_provider_values(self):
        event = append_generation_event({}, {
            "attempt":"not-a-number", "duration_ms":None,
            "cost":"unknown", "outcome":"failed",
        })
        self.assertEqual(1, event["attempt"])
        self.assertEqual(0, event["duration_ms"])
        self.assertEqual(0.0, event["cost"])


if __name__ == "__main__":
    unittest.main()
