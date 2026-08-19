import json
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# UI construction reads the shared asset service during refresh. Never let a
# local test run attach WAL/SHM files to the user's real production database.
_TEST_AI_DATA_ROOT = Path(tempfile.mkdtemp(prefix="cep_ai_data_tests_"))
os.environ.setdefault("CEP_DATA_DIR", str(_TEST_AI_DATA_ROOT))

try:
    from PyQt6.QtCore import QPoint, QPointF, Qt
    from PyQt6.QtGui import QColor, QImage, QKeyEvent, QPainter, QPen, QWheelEvent
    from PyQt6.QtWidgets import QApplication, QMessageBox
    from ai.providers.base import TaskHandle, TaskRequest, TaskResult, TaskStatus
    import ai.ui.production_canvas as canvas_module
except ImportError:  # 普通无 Qt 测试环境跳过，桌面运行时执行完整测试。
    canvas_module = None


# ProductionCanvasTab owns checkpoint timers and some widgets can outlive an
# individual test method. Keep its persistence path isolated for the entire
# interpreter lifetime; restoring the real path in tearDown can let a late Qt
# callback overwrite a user's active canvas file.
_TEST_CANVAS_LAYOUT_ROOT = Path(tempfile.mkdtemp(prefix="cep_canvas_tests_"))
if canvas_module is not None:
    canvas_module.LAYOUT_FILE = (
        _TEST_CANVAS_LAYOUT_ROOT / "_production_canvas_layout.json")


@unittest.skipIf(canvas_module is None, "PyQt6 unavailable")
class ProductionAssetPipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_storyboard_shot_count_ui_supports_every_count_including_seven(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        node_id = panel.create_custom_node(
            "storyboard_node", QPointF(100, 100),
            {"content":"黎明之钟", "shot_count":7, "style":"电影写实"})
        panel.show_inline_editor(panel._nodes[node_id])
        self.app.processEvents()
        combos = panel._inline_editor_proxy.widget().findChildren(
            canvas_module.QComboBox)
        count_combo = next(combo for combo in combos
                           if combo.findData(1) >= 0 and combo.findData(24) >= 0)
        self.assertEqual(25, count_combo.count())
        self.assertEqual(0, count_combo.itemData(0))
        self.assertEqual("自动（推荐）", count_combo.itemText(0))
        self.assertEqual(7, count_combo.currentData())
        self.assertEqual("7 镜", count_combo.currentText())
        panel.close()

    def test_storyboard_shot_count_defaults_to_automatic(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        node_id = panel.create_custom_node(
            "storyboard_node", QPointF(100, 100),
            {"content":"雨夜归人", "style":"电影写实"})
        panel.show_inline_editor(panel._nodes[node_id])
        self.app.processEvents()
        combos = panel._inline_editor_proxy.widget().findChildren(
            canvas_module.QComboBox)
        count_combo = next(combo for combo in combos
                           if combo.findData(0) >= 0 and combo.findData(24) >= 0)
        self.assertEqual(0, count_combo.currentData())
        self.assertEqual("自动（推荐）", count_combo.currentText())
        panel.close()

    def test_node_move_can_be_undone_and_redone_as_one_drag(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        node_id = panel.create_custom_node(
            "image_node", QPointF(100, 120), {"content":"测试节点"})
        node = panel._nodes[node_id]
        origin = QPointF(node.pos())

        panel.begin_node_move(node)
        node.setPos(QPointF(460, 380))
        self.assertTrue(panel.end_node_move(node))
        self.assertEqual(1, len(panel._position_undo))
        self.assertTrue(panel.undo_canvas_action())
        self.assertEqual(origin, node.pos())
        self.assertTrue(panel.redo_canvas_move())
        self.assertEqual(QPointF(460, 380), node.pos())

        # A click without movement must not consume an undo step.
        history_size = len(panel._position_undo)
        panel.begin_node_move(node)
        self.assertFalse(panel.end_node_move(node))
        self.assertEqual(history_size, len(panel._position_undo))
        panel.close()

    def test_multi_selected_node_move_undoes_together(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        first_id = panel.create_custom_node("image_node", QPointF(80, 90))
        second_id = panel.create_custom_node("image_node", QPointF(360, 90))
        first, second = panel._nodes[first_id], panel._nodes[second_id]
        first.setSelected(True); second.setSelected(True)
        before = {first_id:QPointF(first.pos()), second_id:QPointF(second.pos())}

        panel.begin_node_move(first)
        first.setPos(first.pos() + QPointF(100, 50))
        second.setPos(second.pos() + QPointF(100, 50))
        self.assertTrue(panel.end_node_move(first))
        self.assertTrue(panel.undo_canvas_move())
        self.assertEqual(before[first_id], first.pos())
        self.assertEqual(before[second_id], second.pos())
        panel.close()

    def test_auto_layout_keeps_every_shot_in_number_order_on_top_row(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        panel.set_storyboard({"id":"shot-lane-project", "shots":[
            {"id":"s2", "number":2, "duration":3, "scene":"第二镜",
             "assets":[{"path":"s2-board.png", "kind":"image",
                        "subtype":"motion_storyboard"}]},
            {"id":"s1", "number":1, "duration":3, "scene":"第一镜",
             "assets":[{"path":"s1-board.png", "kind":"image",
                        "subtype":"motion_storyboard"}]},
            {"id":"s3", "number":3, "duration":3, "scene":"第三镜",
             "assets":[{"path":"s3-board.png", "kind":"image",
                        "subtype":"motion_storyboard"}]},
        ]})
        workflow_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {"content":"测试工程"})
        asset_id = panel.create_custom_node(
            "image_node", QPointF(0, 0), {
                "title":"场景权威资产", "asset_kind":"scene"})

        panel.auto_layout()

        first = panel._nodes["shot:s1"].pos()
        second = panel._nodes["shot:s2"].pos()
        third = panel._nodes["shot:s3"].pos()
        self.assertEqual(first.y(), second.y())
        self.assertEqual(second.y(), third.y())
        self.assertLess(first.x(), second.x())
        self.assertLess(second.x(), third.x())
        first_take = panel._nodes[
            f"shot_take:s1:{canvas_module._short_id('s1-board.png')}"]
        second_take = panel._nodes[
            f"shot_take:s2:{canvas_module._short_id('s2-board.png')}"]
        third_take = panel._nodes[
            f"shot_take:s3:{canvas_module._short_id('s3-board.png')}"]
        self.assertAlmostEqual(first.x() + panel._nodes["shot:s1"].width / 2,
                               first_take.pos().x() + first_take.width / 2)
        self.assertAlmostEqual(second.x() + panel._nodes["shot:s2"].width / 2,
                               second_take.pos().x() + second_take.width / 2)
        self.assertAlmostEqual(third.x() + panel._nodes["shot:s3"].width / 2,
                               third_take.pos().x() + third_take.width / 2)
        self.assertEqual(first_take.pos().y(), second_take.pos().y())
        self.assertEqual(second_take.pos().y(), third_take.pos().y())
        self.assertLess(panel._nodes[workflow_id].pos().x() +
                        panel._nodes[workflow_id].width, first.x())
        self.assertLess(panel._nodes[asset_id].pos().x() +
                        panel._nodes[asset_id].width, first.x())
        self.assertEqual([first.x(), first.y()],
                         panel._positions()["shot:s1"])
        panel.close()

    def test_storyboard_image_model_lock_routes_assets_and_motion_boards(self):
        submitted = []
        providers = {
            "text_to_image":[SimpleNamespace(name="gptimage"),
                             SimpleNamespace(name="seedream")],
            "image_edit":[SimpleNamespace(name="gptimage"),
                          SimpleNamespace(name="seedream")],
        }

        def submit(name, request):
            submitted.append((name, request))
            return TaskHandle(
                id=f"locked-image-{len(submitted)}", provider_name=name,
                operation=request.operation, status=TaskStatus.RUNNING)

        manager = SimpleNamespace(
            registry=SimpleNamespace(
                by_capability=lambda operation: providers.get(operation, [])),
            submit=submit)
        with patch.object(canvas_module, "get_ai_manager", return_value=manager):
            panel = canvas_module.ProductionCanvasTab()
            panel._checkpoint_timer.stop(); panel._task_timer.stop()
            panel._save_layout_now = lambda: None
            shot = {
                "id":"locked-shot", "number":1, "duration":4,
                "visual":"王子走入大厅", "shot_size":"全景",
                "motion_keyframes":[{"index":1}, {"index":2}, {"index":3}],
            }
            # The script/storyboard workbench historically persisted the
            # selection only in visual_bible, while the canvas source could
            # still contain an older GPT Image value.  The project lock must
            # migrate and win across that boundary.
            panel.set_storyboard({
                "id":"locked-board", "shots":[shot],
                "visual_bible":{"image_provider":"seedream"},
            })
            source_id = panel.create_custom_node(
                "storyboard_node", QPointF(0, 0),
                {"content":"王子与龙", "image_provider":"gptimage"})
            self.assertEqual(
                "seedream",
                panel._storyboard_model_lock(source_id, "image_provider"))
            self.assertEqual(
                "seedream", panel.current_storyboard()["production_models"]["image_provider"])
            self.assertEqual(
                "seedream", panel._custom_record(source_id)["image_provider"])

            legacy_generator_id = panel.create_custom_node(
                "image_node", QPointF(500, 300), {
                    "title":"旧定稿图生成器", "generator_kind":"image",
                    "provider_name":"gptimage", "content":"旧节点",
                })
            legacy_group_id = panel.create_custom_node(
                "workflow_group", QPointF(250, 300), {
                    "title":"旧图片生成器组", "generator_kind":"image",
                    "source_node_id":source_id,
                    "group_nodes":[legacy_generator_id],
                })
            panel._positions().setdefault("__workflow_edges__", []).append({
                "source":legacy_group_id, "target":legacy_generator_id,
                "type":"group",
            })

            asset_id = panel.create_custom_node(
                "image_node", QPointF(300, 0), {"content":"银色王室徽章"})
            asset = panel._custom_record(asset_id)
            asset.update({"asset_kind":"element", "asset_name":"王室徽章"})
            panel._positions().setdefault("__workflow_edges__", []).append({
                "source":source_id, "target":asset_id, "type":"element"})
            panel._canvas_storyboard_source = source_id
            panel._canvas_character_queue = [asset_id]
            panel._submit_next_canvas_character()
            self.assertEqual("seedream", submitted[-1][0])
            self.assertEqual("canvas_character_sheet",
                             submitted[-1][1].metadata["purpose"])
            self.assertEqual(
                "seedream",
                panel._custom_record(legacy_generator_id)["provider_name"])
            self.assertEqual(
                "seedream",
                panel._nodes[legacy_generator_id].payload["provider_name"])

            panel._canvas_storyboard_queue = [0]
            panel._canvas_storyboard_previous = ""
            panel._canvas_storyboard_character_refs = []
            panel._submit_next_canvas_storyboard_image()
            self.assertEqual("seedream", submitted[-1][0])
            self.assertEqual("canvas_storyboard_panel",
                             submitted[-1][1].metadata["purpose"])
            self.assertIn("seedream", panel._nodes["shot:locked-shot"].badge)
            panel.close()

    def test_explicit_storyboard_image_lock_never_falls_back(self):
        manager = SimpleNamespace(registry=SimpleNamespace(
            by_capability=lambda _operation: [SimpleNamespace(name="gptimage")]))
        with patch.object(canvas_module, "get_ai_manager", return_value=manager):
            panel = canvas_module.ProductionCanvasTab()
            panel._checkpoint_timer.stop(); panel._task_timer.stop()
            panel._save_layout_now = lambda: None
            panel.set_storyboard({"id":"no-fallback-board", "shots":[]})
            source_id = panel.create_custom_node(
                "storyboard_node", QPointF(0, 0),
                {"image_provider":"seedream"})
            panel.update_custom_setting(
                panel._nodes[source_id], "image_provider", "seedream")
            with self.assertRaisesRegex(RuntimeError, "不会静默切换"):
                panel._locked_storyboard_image_provider(
                    "text_to_image", source_id)
            panel.close()

    def test_motion_storyboard_outputs_are_selectable_candidates(self):
        folder = Path(tempfile.mkdtemp())
        first = folder / "motion_first.png"
        second = folder / "motion_second.png"
        for path, color in ((first, "#294968"), (second, "#784b38")):
            image = QImage(160, 90, QImage.Format.Format_RGB32)
            image.fill(QColor(color)); self.assertTrue(image.save(str(path)))

        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._save_layout_now = lambda: None
        shot = {
            "id":"motion-choice", "number":1, "duration":4,
            "motion_keyframes":[{"index":1}, {"index":2}, {"index":3}],
            "assets":[],
        }
        panel.set_storyboard({"id":"motion-choice-board", "shots":[shot]})
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {"content":"候选测试"})
        panel._canvas_storyboard_source = source_id
        handle = TaskHandle(
            id="motion-candidates", provider_name="seedream",
            operation="text_to_image", status=TaskStatus.DONE, progress=1,
            result=TaskResult(True, [str(first), str(second)]))
        panel._standalone_tasks[handle.id] = {
            "handle":handle, "node_id":"shot:motion-choice",
            "provider":"seedream", "kind":"storyboard_reroll", "shot_index":0,
        }

        panel._poll_standalone_tasks()

        candidates = [value for value in shot["assets"]
                      if value.get("subtype") == "motion_storyboard"]
        self.assertEqual([str(first), str(second)],
                         [value["path"] for value in candidates])
        self.assertEqual(str(first), shot["motion_board_path"])
        self.assertEqual(1, sum(bool(value.get("approved")) for value in candidates))
        second_node = panel._nodes[
            f"shot_take:motion-choice:{canvas_module._short_id(str(second))}"]
        self.assertTrue(panel.adopt_motion_storyboard_take(second_node))
        self.assertEqual(str(second), shot["motion_board_path"])
        self.assertEqual(str(second), shot["draft_panel"])
        self.assertFalse(shot.get("selected_image_asset"))
        self.assertTrue(next(value for value in shot["assets"]
                             if value["path"] == str(second))["approved"])
        panel.close()

    def test_explicit_motion_board_adoption_clears_auto_rejected_gate(self):
        folder = Path(tempfile.mkdtemp())
        panel_paths = []
        for index in range(3):
            path = folder / f"panel-{index}.png"
            image = QImage(160, 90, QImage.Format.Format_RGB32)
            image.fill(QColor("#38526a")); self.assertTrue(image.save(str(path)))
            panel_paths.append(str(path))
        board_path = folder / "motion-board.png"
        image = QImage(480, 90, QImage.Format.Format_RGB32)
        image.fill(QColor("#38526a")); self.assertTrue(image.save(str(board_path)))
        asset = {
            "path":str(board_path), "kind":"image",
            "subtype":"motion_storyboard", "approved":False,
            "contract_version":canvas_module.MOTION_STORYBOARD_CONTRACT_VERSION,
            "aspect_ratio":"16:9", "panel_paths":panel_paths,
        }
        shot = {
            "id":"manual-motion-approval", "number":1, "duration":4,
            "motion_keyframes":[{"index":1}, {"index":2}, {"index":3}],
            "motion_board_review_status":"auto_rejected", "assets":[asset],
        }
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._save_layout_now = lambda *args, **kwargs: None
        panel.set_storyboard({"id":"manual-motion-board", "shots":[shot]})
        panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {"production_ratio":"16:9"})
        take = SimpleNamespace(payload={
            "shot_id":shot["id"], "path":str(board_path), "asset":asset})

        with patch.object(canvas_module, "inspect_motion_panels", return_value={
                "status":"fail", "issues":["动作格过于相似"]}):
            self.assertTrue(panel.adopt_motion_storyboard_take(take))

        self.assertEqual("manually_approved",
                         shot["motion_board_review_status"])
        self.assertTrue(shot["motion_board_risk_accepted"])
        self.assertTrue(asset["approved"])
        self.assertEqual(panel_paths, shot["motion_panel_paths"])
        panel.close()

    def test_production_selection_toggle_uses_stable_shot_id(self):
        shot = {"id":"stable-toggle", "number":1,
                "production_selected":False}
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._save_layout_now = lambda *args, **kwargs: None
        panel.set_storyboard({"id":"stable-toggle-board", "shots":[shot]})

        self.assertTrue(panel.toggle_shot_production_selection(shot["id"]))
        self.assertTrue(shot["production_selected"])
        self.assertTrue(panel.toggle_shot_production_selection(shot["id"]))
        self.assertFalse(shot["production_selected"])
        panel.close()

    def test_storyboard_reroll_is_visible_and_excludes_rejected_board_reference(self):
        folder = Path(tempfile.mkdtemp())
        rejected = folder / "rejected-board.png"
        scene = folder / "scene-authority.png"
        character = folder / "character-authority.png"
        for path, color in ((rejected, "#442222"), (scene, "#224444"),
                            (character, "#444422")):
            image = QImage(160, 90, QImage.Format.Format_RGB32)
            image.fill(QColor(color)); self.assertTrue(image.save(str(path)))
        submitted = []

        def submit(name, request):
            submitted.append((name, request))
            return TaskHandle(
                id="reroll-authority", provider_name=name,
                operation=request.operation, status=TaskStatus.RUNNING)

        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._save_layout_now = lambda *args, **kwargs: None
        shot = {
            "id":"reroll-shot", "number":1, "duration":4,
            "motion_keyframes":[{"index":1}, {"index":2}, {"index":3}],
            "draft_panel":str(rejected), "motion_board_path":str(rejected),
            "preview_asset":str(rejected), "scene_view_path":str(scene),
            "assets":[{"path":str(rejected), "kind":"image",
                       "subtype":"motion_storyboard", "approved":True}],
        }
        panel.set_storyboard({"id":"reroll-authority-board", "shots":[shot]})
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {"content":"重生测试"})
        panel._canvas_storyboard_character_refs = [str(character)]
        node = panel._nodes["shot:reroll-shot"]
        panel.context_inspector.show_shot(node)
        button_texts = [button.text() for button in
                        panel.context_inspector.findChildren(canvas_module.QPushButton)]
        self.assertIn("↻ 重新生成本镜分镜稿", button_texts)

        manager = SimpleNamespace(submit=submit)
        with patch.object(canvas_module, "get_ai_manager", return_value=manager), \
                patch.object(panel, "_locked_storyboard_image_provider",
                             return_value=SimpleNamespace(name="seedream")), \
                patch.object(panel, "_current_production_source_id",
                             return_value=source_id):
            self.assertTrue(panel.reroll_canvas_storyboard_shot(node))

        request = submitted[0][1]
        self.assertEqual("image_edit", request.operation)
        self.assertTrue(os.path.exists(request.inputs["image"]))
        self.assertEqual(request.inputs["image"], shot["scene_stage_capture"])
        self.assertIn(str(scene), request.inputs["images"])
        self.assertIn(str(character), request.inputs["images"])
        self.assertNotIn(str(rejected), request.inputs["images"])
        self.assertEqual("regenerating", shot["motion_board_review_status"])
        self.assertFalse(panel._custom_record(source_id)["auto_run_enabled"])
        panel.close()

    def test_storyboard_reroll_switches_to_new_board_and_keeps_old_history(self):
        folder = Path(tempfile.mkdtemp())
        old = folder / "old-board.png"
        new = folder / "new-board.png"
        for path, color in ((old, "#553333"), (new, "#335555")):
            image = QImage(160, 90, QImage.Format.Format_RGB32)
            image.fill(QColor(color)); self.assertTrue(image.save(str(path)))
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._save_layout_now = lambda *args, **kwargs: None
        shot = {
            "id":"reroll-current", "number":1, "duration":4,
            "motion_keyframes":[{"index":1}, {"index":2}, {"index":3}],
            "draft_panel":str(old), "motion_board_path":str(old),
            "preview_asset":str(old), "selected_asset":str(old),
            "assets":[{"path":str(old), "kind":"image",
                       "subtype":"motion_storyboard", "approved":True}],
        }
        panel.set_storyboard({"id":"reroll-current-board", "shots":[shot]})
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {"content":"版本测试"})
        handle = TaskHandle(
            id="reroll-new-current", provider_name="seedream",
            operation="text_to_image", status=TaskStatus.DONE, progress=1,
            result=TaskResult(True, [str(new)]))
        panel._standalone_tasks[handle.id] = {
            "handle":handle, "node_id":"shot:reroll-current",
            "provider":"seedream", "kind":"storyboard_reroll", "shot_index":0,
            "source_id":source_id,
        }

        panel._poll_standalone_tasks()

        self.assertEqual(str(new), shot["motion_board_path"])
        self.assertEqual(str(new), shot["draft_panel"])
        self.assertEqual("pending_review", shot["motion_board_review_status"])
        self.assertEqual(1, shot["motion_board_reroll_count"])
        by_path = {value["path"]:value for value in shot["assets"]}
        self.assertIn(str(old), by_path)
        self.assertFalse(by_path[str(old)]["approved"])
        self.assertTrue(by_path[str(new)]["approved"])
        panel.close()

    def test_director_inspector_save_invalidates_compiled_prompts(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        shot = {
            "id":"director-shot", "number":1, "visual":"女孩站在门前",
            "final_image_prompt":"旧图片提示词", "final_video_prompt":"旧视频提示词",
            "production_ready":True,
        }
        panel.set_storyboard({"id":"director-board", "shots":[shot]})
        panel._save_layout_now = lambda: None
        self.assertTrue(panel.update_shot_director_contract("director-shot", {
            "story_function":"揭示门后有人",
            "visual_thesis":"女孩从画面中心退到门框阴影中",
            "action_start":"女孩面对门站立",
            "primary_action":"女孩后退一步",
            "action_end":"女孩停在门框阴影中",
            "dominant_camera_move":"固定机位",
            "keyframe_strategy":"first_last",
            "continuity_invariants":["红色外套不变", "主光来自画面左侧"],
            "generation_risk":"人物身份漂移",
        }))
        self.assertFalse(shot["production_ready"])
        self.assertNotIn("final_image_prompt", shot)
        self.assertNotIn("final_video_prompt", shot)
        self.assertTrue(shot["director_gate"]["passed"])
        self.assertEqual("揭示门后有人", shot["director_contract"]["story_function"])

    def test_shot_inspector_saves_scene_view_and_edit_region_contract(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._save_layout_now = lambda: None
        shot = {
            "id":"scene-contract-shot", "number":1,
            "scene_view_id":"master", "editable_bbox_xy":[0.1, 0.1, 0.8, 0.8],
        }
        panel.set_storyboard({"id":"scene-contract-board", "shots":[shot]})
        self.assertTrue(panel.update_shot_director_contract(
            "scene-contract-shot", {
                "scene_view_id":"reverse",
                "editable_bbox_xy":"0.25，0.2，0.4，0.5",
            }))
        self.assertEqual("reverse", shot["scene_view_id"])
        self.assertEqual([0.25, 0.2, 0.4, 0.5], shot["editable_bbox_xy"])

    def test_smart_video_segmentation_respects_continuity_and_limits(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        shots = [
            {"id": "s1", "duration": 6, "scene_name": "roof",
             "video_segment": "A"},
            {"id": "s2", "duration": 6, "scene_name": "roof",
             "video_segment": "A"},
            {"id": "s3", "duration": 6, "scene_name": "roof",
             "video_segment": "A", "segment_break_after": True},
            {"id": "s4", "duration": 4, "scene_name": "roof",
             "video_segment": "A"},
            {"id": "s5", "duration": 4, "scene_name": "street",
             "video_segment": "B"},
        ]

        smart = panel._smart_video_segments(shots, shots, "smart")
        self.assertEqual([["s1", "s2"], ["s3"], ["s4"], ["s5"]],
                         [[shot["id"] for _index, shot in segment]
                          for segment in smart])
        self.assertTrue(all(
            sum(float(shot["duration"]) for _index, shot in segment) <= 15
            and len(segment) <= 3 for segment in smart))

        per_shot = panel._smart_video_segments(shots[:2], shots, "per_shot")
        self.assertEqual(2, len(per_shot))
        single = panel._smart_video_segments(shots[:2], shots, "single_15")
        self.assertEqual(1, len(single))

    def test_smart_segmentation_never_hides_a_camera_cut_in_one_request(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        shots = [
            {"id":"wide", "duration":5, "scene_name":"room",
             "camera_slot":"南侧全景", "transition":"硬切到近景"},
            {"id":"close", "duration":5, "scene_name":"room",
             "camera_slot":"北侧近景", "transition":""},
        ]
        segments = panel._smart_video_segments(shots, shots, "smart")
        self.assertEqual([["wide"], ["close"]], [
            [shot["id"] for _index, shot in segment] for segment in segments])

    def test_seedance_director_timeline_keeps_cuts_inside_one_generation(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        shots = [
            {"id":"plate", "duration":3, "scene_name":"room",
             "shot_size":"远景", "camera_slot":"门口机位",
             "camera_movement":"缓慢推镜", "transition":"硬切"},
            {"id":"actor", "duration":4, "scene_name":"room",
             "shot_size":"中景", "camera_slot":"侧面机位",
             "camera_movement":"横移跟拍", "transition":"硬切到特写"},
            {"id":"insert", "duration":3, "scene_name":"room",
             "shot_size":"特写", "camera_slot":"桌面俯拍",
             "camera_movement":"固定机位", "transition":""},
        ]
        segments = panel._smart_video_segments(
            shots, shots, "director_timeline", "seedance")
        self.assertEqual([["plate", "actor", "insert"]], [
            [shot["id"] for _index, shot in segment] for segment in segments])
        prompt = panel._video_segment_prompt(list(enumerate(shots)))
        self.assertIn("[00:00-00:03]", prompt)
        self.assertIn("[00:03-00:07]", prompt)
        self.assertIn("[00:07-00:10]", prompt)
        self.assertIn("缓慢推镜", prompt)
        self.assertIn("横移跟拍", prompt)
        panel.close()

    def test_seedance_director_timeline_ignores_planner_segment_label_drift(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        shots = [
            {"id":"establish", "duration":3, "scene_name":"post office",
             "video_segment":"seg_001_letter"},
            {"id":"walk", "duration":3, "scene_name":"post office",
             "video_segment":"seg_001_letter"},
            {"id":"pickup", "duration":2, "scene_name":"post office",
             "video_segment":"seg_001_continuous"},
            {"id":"bag", "duration":4, "scene_name":"post office",
             "video_segment":"seg_001_continuous", "segment_break_after":True},
        ]
        segments = panel._smart_video_segments(
            shots, shots, "director_timeline", "seedance")
        self.assertEqual([["establish", "walk", "pickup", "bag"]], [
            [shot["id"] for _index, shot in segment] for segment in segments])
        panel.close()

    def test_spatial_review_flags_endpoint_ground_line_drift(self):
        folder = Path(tempfile.mkdtemp())

        def draw_line(path, y):
            image = QImage(640, 360, QImage.Format.Format_RGB32)
            image.fill(QColor("#181818"))
            painter = QPainter(image)
            painter.setPen(QPen(QColor("#f2c14e"), 8))
            painter.drawLine(30, y, 610, y)
            painter.end(); image.save(str(path))

        anchor = folder / "anchor.png"
        matching = folder / "matching.png"
        drifted = folder / "drifted.png"
        draw_line(anchor, 292); draw_line(matching, 292); draw_line(drifted, 150)
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()

        passed = panel._run_spatial_consistency_review(
            {"first_frame":str(anchor)}, [str(matching)])
        self.assertEqual("pass", passed["status"])
        warned = panel._run_spatial_consistency_review(
            {"first_frame":str(anchor)}, [str(drifted)])
        self.assertEqual("warn", warned["status"])
        self.assertTrue(warned["issues"])

    def test_fixed_scene_geometry_failure_blocks_manual_and_auto_adoption(self):
        folder = Path(tempfile.mkdtemp())
        candidate = folder / "candidate.png"
        candidate.touch()
        shot = {"id":"scene-qc-shot", "number":1, "assets":[]}
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel.set_storyboard({"id":"scene-qc-board", "shots":[shot]})
        panel._save_layout_now = lambda: None

        asset = {
            "path":str(candidate), "kind":"image", "frame_role":"start",
            "spatial_qc":{
                "status":"fail", "issues":["FIXED_SCENE_GEOMETRY_DRIFT"],
                "fixed_structure_similarity":0.31,
            },
        }
        node = SimpleNamespace(payload={
            "shot_id":"scene-qc-shot", "path":str(candidate), "asset":asset,
        })
        self.assertIn("固定设备", panel._shot_take_block_reason(node))

        generator = {
            "id":"generator", "frame_role":"start", "shot_id":"scene-qc-shot",
            "candidates":[str(candidate)],
            "candidate_spatial_qc":{str(candidate):asset["spatial_qc"]},
        }
        source = {"id":"scene-qc-source", "pipeline_stage":"start_image_candidates_ready"}
        panel._latest_production_group = lambda *_args: {
            "id":"group", "group_nodes":["generator"]}
        panel._custom_record = lambda node_id: (
            generator if node_id == "generator" else source)
        panel._path_has_motion_board_lineage = lambda *_args: False
        self.assertFalse(panel._auto_adopt_image_candidates("scene-qc-source"))
        self.assertNotIn("selected_image_asset", shot)

    def test_video_handoff_distinguishes_continuation_from_editorial_transitions(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        previous = {
            "id":"s1", "scene_name":"roof", "video_segment":"A",
            "transition":"无缝继续跟拍", "frame_end":"女孩位于画面右侧，仍向右奔跑",
            "visual":"女孩奔跑",
        }
        current = {
            "id":"s2", "scene_name":"roof", "video_segment":"A",
            "frame_start":"女孩从画面右侧继续向右奔跑", "visual":"继续追逐",
        }
        continuous = panel._video_handoff_contract(
            previous, current, across_segments=True)
        self.assertEqual("continuous_tail", continuous["mode"])
        self.assertTrue(continuous["uses_previous_tail"])
        self.assertIn("上一镜结束状态", continuous["prompt"])
        self.assertIn("下一镜开始状态", continuous["prompt"])

        previous["transition"] = "动作接切到侧面近景"
        matched = panel._video_handoff_contract(
            previous, current, across_segments=True)
        self.assertEqual("match_state", matched["mode"])
        self.assertFalse(matched["uses_previous_tail"])

        previous["transition"] = "叠化进入下一镜"
        dissolved = panel._video_handoff_contract(
            previous, current, across_segments=True)
        self.assertEqual("transition_state", dissolved["mode"])
        self.assertFalse(dissolved["uses_previous_tail"])

        previous["transition"] = "硬切到反打"
        cut = panel._video_handoff_contract(
            previous, current, across_segments=True)
        self.assertEqual("hard_cut", cut["mode"])
        self.assertFalse(cut["uses_previous_tail"])

    def test_continuous_video_segment_uses_previous_real_tail_as_next_anchor(self):
        folder = Path(tempfile.mkdtemp())
        planned = folder / "planned.png"
        tail = folder / "previous-tail.jpg"
        for path, color in ((planned, "#334455"), (tail, "#556677")):
            image = QImage(160, 90, QImage.Format.Format_RGB32)
            image.fill(QColor(color)); image.save(str(path))
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        previous_record = {
            "id":"video-a", "type":"video_node", "generator_kind":"video",
            "video_tail_frame":str(tail),
            "adopted":True, "handoff_approved":True,
            "clip_qc":{"status":"complete", "passed":True},
        }
        next_record = {
            "id":"video-b", "type":"video_node", "generator_kind":"video",
            "handoff_mode":"continuous_tail",
            "previous_segment_node_id":"video-a",
            "planned_first_frame":str(planned), "first_frame":str(planned),
        }
        with patch.object(panel, "_custom_record",
                          return_value=previous_record):
            self.assertTrue(panel._prepare_video_handoff_anchor(next_record))
        self.assertEqual(str(tail), next_record["first_frame"])
        self.assertEqual("previous_video_tail", next_record["first_frame_source"])
        self.assertEqual("已锁定上一段真实尾帧", next_record["handoff_status"])

    def test_continuous_video_segment_rejects_unapproved_tail_and_never_falls_back(self):
        folder = Path(tempfile.mkdtemp())
        planned = folder / "planned.png"
        tail = folder / "unapproved-tail.jpg"
        for path, color in ((planned, "#334455"), (tail, "#556677")):
            image = QImage(160, 90, QImage.Format.Format_RGB32)
            image.fill(QColor(color)); image.save(str(path))
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        previous_record = {
            "id":"video-a", "type":"video_node", "generator_kind":"video",
            "video_tail_frame":str(tail), "adopted":False,
            "handoff_approved":False,
            "clip_qc":{"status":"pending", "passed":False},
        }
        next_record = {
            "id":"video-b", "type":"video_node", "generator_kind":"video",
            "handoff_mode":"continuous_tail",
            "previous_segment_node_id":"video-a",
            "planned_first_frame":str(planned), "first_frame":str(planned),
        }
        with patch.object(panel, "_custom_record", return_value=previous_record):
            self.assertFalse(panel._prepare_video_handoff_anchor(next_record))
        self.assertEqual("", next_record["first_frame"])
        self.assertTrue(next_record["handoff_blocked"])
        self.assertIn("尚未定稿并通过审片", next_record["handoff_status"])

    def test_human_accepted_clip_can_handoff_its_real_tail(self):
        folder = Path(tempfile.mkdtemp())
        tail = folder / "accepted-tail.jpg"
        image = QImage(160, 90, QImage.Format.Format_RGB32)
        image.fill(QColor("#667788")); image.save(str(tail))
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        previous_record = {
            "id":"video-a", "type":"video_node", "generator_kind":"video",
            "video_tail_frame":str(tail), "adopted":True,
            "handoff_approved":True,
            "clip_qc":{"status":"complete", "passed":False,
                       "severity":"review", "risk_accepted":True},
        }
        next_record = {
            "id":"video-b", "type":"video_node", "generator_kind":"video",
            "handoff_mode":"continuous_tail",
            "previous_segment_node_id":"video-a",
        }
        with patch.object(panel, "_custom_record", return_value=previous_record):
            self.assertTrue(panel._prepare_video_handoff_anchor(next_record))
        self.assertEqual(str(tail), next_record["first_frame"])
        self.assertFalse(next_record["handoff_blocked"])

    def test_adopting_video_candidate_updates_whole_segment_before_qc(self):
        folder = Path(tempfile.mkdtemp())
        video = folder / "candidate.mp4"
        frame = folder / "tail.jpg"
        video.write_bytes(b"candidate")
        image = QImage(160, 90, QImage.Format.Format_RGB32)
        image.fill(QColor("#446688")); image.save(str(frame))
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._save_layout_now = lambda: None
        asset = {
            "path":str(video), "kind":"video", "generator_node_id":"video-gen",
            "video_review_frames":[str(frame)], "spatial_review":{"status":"pass"},
            "deterministic_qc":{"status":"pass"}, "approved":False,
        }
        shots = [
            {"id":"s1", "number":1, "duration":3, "assets":[dict(asset)]},
            {"id":"s2", "number":2, "duration":4, "assets":[dict(asset)]},
        ]
        panel.set_storyboard({"id":"candidate-project", "shots":shots})
        panel._positions().setdefault("__custom_nodes__", []).extend([{
            "id":"video-group", "type":"workflow_group", "generator_kind":"video",
            "source_node_id":"source", "group_nodes":["video-gen"],
        }, {
            "id":"video-gen", "type":"video_node", "generator_kind":"video",
            "shot_ids":["s1", "s2"], "workflow_group_id":"video-group",
            "candidate_batch_paths":[str(video)], "candidates":[str(video)],
        }])
        node = SimpleNamespace(payload={
            "shot_id":"s1", "path":str(video), "kind":"video", "asset":asset,
        })
        with patch.object(panel, "_submit_video_clip_qc", return_value=True) as submit_qc, \
                patch.object(panel, "refresh"), patch.object(panel, "focus_node"):
            self.assertTrue(panel.adopt_shot_take(node))
        generator = panel._custom_record("video-gen")
        self.assertTrue(generator["adopted"])
        self.assertFalse(generator["handoff_approved"])
        self.assertEqual(str(video), generator["selected_candidate_path"])
        self.assertEqual(str(video), shots[0]["selected_video_asset"])
        self.assertEqual(str(video), shots[1]["selected_video_asset"])
        self.assertEqual(0.0, shots[0]["video_segment_offset"])
        self.assertEqual(3.0, shots[1]["video_segment_offset"])
        self.assertEqual(str(frame), shots[1]["video_tail_frame"])
        submit_qc.assert_called_once_with(generator, "video-group")

    def test_segment_prompt_contains_explicit_inter_shot_state_handoff(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        shots = [
            {"id":"s1", "duration":4, "scene_name":"巷道", "visual":"女孩冲向门口",
             "frame_start":"女孩在画面左侧", "frame_end":"右脚踏入门框",
             "transition":"动作接切", "character_positions":[]},
            {"id":"s2", "duration":4, "scene_name":"巷道", "visual":"女孩穿门而过",
             "frame_start":"右脚落在门内", "frame_end":"女孩停在画面右侧",
             "transition":"硬切", "character_positions":[]},
        ]
        prompt = panel._video_segment_prompt(list(enumerate(shots)))
        self.assertIn("【段内镜头交接合同】", prompt)
        self.assertIn("分镜01→分镜02", prompt)
        self.assertIn("右脚踏入门框", prompt)
        self.assertIn("右脚落在门内", prompt)

    def test_video_prompt_separates_dramatic_tone_from_real_motion_speed(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel.set_storyboard({
            "id":"normal-speed", "visual_bible":{"ai_storyboard":{
                "pace":"缓慢、庄重、内敛，避免快速剪辑"}},
            "shots":[]})
        shot = {
            "id":"s1", "number":1, "duration":3, "shot_size":"中景",
            "visual":"人物缓慢走到桌边并回头", "action_line":"慢慢走三步后回头",
            "blocking":"从门口走到桌边", "frame_start":"站在门口",
            "frame_end":"在桌边站稳", "camera_movement":"缓慢推镜",
            "character_positions":[], "motion_keyframes":[],
        }

        prompt = panel._video_segment_prompt([(0, shot)])

        self.assertIn("人物动作按正常现实时间完成", prompt)
        self.assertIn("行走约每0.55秒一步", prompt)
        self.assertIn("转身或头部转向在0.6–1.0秒内完成", prompt)
        self.assertIn("禁止慢动作、速度渐变和漂浮感", prompt)
        self.assertNotIn("'pace': '缓慢", prompt)
        self.assertEqual(3, panel._video_request_duration(3))
        self.assertEqual(2, panel._video_request_duration(1.6))
        panel.close()

    def test_compound_prop_action_forces_endpoint_pair_and_compact_prompt(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        shot = {
            "id":"umbrella-exit", "number":5, "duration":5,
            "frame_start":"阿青坐在长椅上，手中只有一把合拢红伞",
            "primary_action":"阿青起身，撑开同一把红伞，向右走出画面",
            "frame_end":"阿青和同一把打开的红伞已从右侧出画，原位置为空",
            "camera_movement":"固定机位", "continuity_invariants":[
                "红伞始终只有一把", "长椅位置不变"],
        }

        complexity = panel._apply_video_action_policy(shot)
        self.assertTrue(complexity["requires_endpoint_pair"])
        self.assertTrue(complexity["overloaded"])
        self.assertIn("道具形态变化", complexity["categories"])
        self.assertIn("人物明显位移", complexity["categories"])
        self.assertEqual("first_last", shot["keyframe_strategy"])
        self.assertTrue(shot["endpoint_pair_enabled"])
        self.assertTrue(shot["endpoint_pair_forced"])

        prompt = panel._video_segment_prompt([(4, shot)])
        self.assertIn("【单件道具守恒】", prompt)
        self.assertIn("原位置随物体移动后必须为空", prompt)
        self.assertIn("【唯一主要表演】", prompt)
        self.assertNotIn("运动关键帧：", prompt)
        self.assertLess(len(prompt), 2200)
        panel.close()

    @staticmethod
    def _wheel_event(delta: int, modifiers=None):
        if modifiers is None:
            modifiers = canvas_module.Qt.KeyboardModifier.NoModifier
        return QWheelEvent(
            QPointF(20, 20), QPointF(20, 20), QPoint(), QPoint(0, delta),
            canvas_module.Qt.MouseButton.NoButton, modifiers,
            canvas_module.Qt.ScrollPhase.ScrollUpdate, False)

    def test_plain_wheel_zooms_canvas_and_reads_text_when_over_editor(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()

        initial_zoom = panel.view.transform().m11()
        panel.view.wheelEvent(self._wheel_event(120))
        self.assertGreater(panel.view.transform().m11(), initial_zoom)

        editor = canvas_module._NodeTextEdit()
        editor.resize(420, 140)
        editor.setPlainText("\n".join(f"第 {index} 行镜头说明" for index in range(40)))
        editor.show(); self.app.processEvents()
        self.assertGreater(editor.verticalScrollBar().maximum(), 0)
        before_scroll = editor.verticalScrollBar().value()
        editor.wheelEvent(self._wheel_event(-120))
        self.assertGreater(editor.verticalScrollBar().value(), before_scroll)

        # 即使已经滚到边界，也必须消费事件，不能冒泡让画布缩放。
        editor.verticalScrollBar().setValue(editor.verticalScrollBar().maximum())
        boundary_event = self._wheel_event(-120)
        editor.wheelEvent(boundary_event)
        self.assertTrue(boundary_event.isAccepted())

        zoom_requests = []
        editor.canvasZoomRequested.connect(zoom_requests.append)
        editor.wheelEvent(self._wheel_event(
            -120, canvas_module.Qt.KeyboardModifier.ControlModifier))
        self.assertEqual(1, len(zoom_requests))
        self.assertLess(zoom_requests[0], 1.0)
        editor.close()

    def test_canvas_expands_for_far_nodes_and_allows_five_percent_zoom(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__":panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        panel.set_storyboard({"id":"infinite-canvas", "shots":[]})
        node_id = panel.create_custom_node(
            "text_node", QPointF(200, 12000), {"content":"远处节点"})
        node = panel._nodes[node_id]

        self.assertGreater(panel.scene.sceneRect().bottom(),
                           node.sceneBoundingRect().bottom())
        node.setPos(QPointF(-9000, 30000))
        self.app.processEvents()
        self.assertLess(panel.scene.sceneRect().left(),
                        node.sceneBoundingRect().left())
        self.assertGreater(panel.scene.sceneRect().bottom(),
                           node.sceneBoundingRect().bottom())

        panel.view.set_zoom(0.001)
        self.assertAlmostEqual(0.05, panel.view.transform().m11(), places=3)
        panel.close()

    def test_asset_editor_popup_is_wide_and_height_is_viewport_bounded(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel.resize(1100, 760)
        panel.show(); self.app.processEvents()
        panel._show_canvas_popup(); self.app.processEvents()

        self.assertGreaterEqual(panel.asset_inspector.width(), 500)
        self.assertLessEqual(panel.canvas_drawer.height(), panel.height())
        self.assertTrue(panel.asset_inspector._scroll.widgetResizable())
        panel.close()

    def test_canvas_forwards_wheel_anywhere_over_expanded_editor(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        node_id = panel.create_custom_node(
            "text_node", QPointF(100, 100),
            {"content":"\n".join(f"长内容第 {index} 行" for index in range(80))})
        panel.refresh()
        node = panel._nodes[node_id]
        panel.show_inline_editor(node)
        self.app.processEvents()
        editor = panel._inline_text_editor
        self.assertGreater(editor.verticalScrollBar().maximum(), 0)
        proxy_center = panel._inline_editor_proxy.sceneBoundingRect().center()
        before_zoom = panel.view.transform().m11()
        event = self._wheel_event(-120)

        self.assertTrue(panel.scroll_inline_editor_at(proxy_center, event))
        self.assertGreater(editor.verticalScrollBar().value(), 0)
        self.assertEqual(before_zoom, panel.view.transform().m11())
        panel.close()

    def test_canvas_scrolls_hovered_review_report_not_primary_editor(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        report = "\n".join(f"体检问题 {index}: 需要修正" for index in range(80))
        node_id = panel.create_custom_node(
            "text_node", QPointF(100, 100),
            {"content":"主剧本", "script_review":report})
        panel.refresh()
        panel.show_inline_editor(panel._nodes[node_id])
        self.app.processEvents()

        editors = panel._inline_editor_proxy.widget().findChildren(
            canvas_module.QTextEdit)
        review = next(editor for editor in editors
                      if "体检问题 79" in editor.toPlainText())
        self.assertGreater(review.verticalScrollBar().maximum(), 0)
        primary_before = panel._inline_text_editor.verticalScrollBar().value()
        top_left = review.mapTo(panel._inline_editor_proxy.widget(), QPoint(0, 0))
        scene_pos = panel._inline_editor_proxy.mapToScene(
            QPointF(top_left.x() + review.width() / 2,
                    top_left.y() + review.height() / 2))

        self.assertTrue(panel.scroll_inline_editor_at(scene_pos, self._wheel_event(-120)))
        self.assertGreater(review.verticalScrollBar().value(), 0)
        self.assertEqual(primary_before,
                         panel._inline_text_editor.verticalScrollBar().value())
        panel.close()

    def test_canvas_has_no_legacy_top_toolbar(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        button_texts = {
            button.text() for button in panel.findChildren(canvas_module.QPushButton)}
        label_texts = {
            label.text() for label in panel.findChildren(canvas_module.QLabel)}
        for legacy in (
                "连接选中", "解除连接", "移出画布", "删除选中",
                "从资产库删除", "刷新", "自动排版", "适应画布"):
            self.assertNotIn(legacy, button_texts)
        self.assertNotIn("AI 制片画布", label_texts)
        self.assertNotIn("资产 → 选择主参考 → 镜头画面 → 视频结果", label_texts)

        # Toolbar removal must not remove the externally used focus/filter API.
        panel.focus_kind("all")
        self.assertEqual("all", panel._active_filter)

    def test_canvas_scrollbars_are_hidden_but_text_editor_can_scroll(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        self.assertEqual(
            canvas_module.Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
            panel.view.horizontalScrollBarPolicy())
        self.assertEqual(
            canvas_module.Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
            panel.view.verticalScrollBarPolicy())

        editor = canvas_module._NodeTextEdit()
        editor.resize(360, 120)
        editor.setPlainText("\n".join(f"第 {index} 行" for index in range(80)))
        editor.show(); self.app.processEvents()
        self.assertGreater(editor.verticalScrollBar().maximum(), 0)
        editor.close()

    def test_inline_editor_is_tall_resizable_and_remembers_node_height(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        panel.set_storyboard({"id":"editor-size-project", "shots":[]})
        node_id = panel.create_custom_node("text_node", QPointF(0, 0), {
            "title":"长脚本", "content":"第一行\n第二行\n第三行\n第四行\n第五行"})

        panel.show_inline_editor(panel._nodes[node_id])
        inline_panel = panel._inline_editor_proxy.widget()
        editor = inline_panel.findChild(canvas_module._NodeTextEdit)
        handle = inline_panel.findChild(canvas_module._EditorResizeHandle)
        self.assertGreaterEqual(editor.height(), 220)
        self.assertIsNotNone(handle)
        handle._set_editor_height(340)
        handle.heightCommitted.emit(340)
        self.assertEqual(340, panel._positions()["__inline_editor_heights__"][node_id])

        panel.hide_inline_editor()
        panel.show_inline_editor(panel._nodes[node_id])
        restored_editor = panel._inline_editor_proxy.widget().findChild(
            canvas_module._NodeTextEdit)
        self.assertEqual(340, restored_editor.height())

    def test_inline_editor_text_persists_when_hidden_switched_and_cleared(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__":panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        panel.set_storyboard({"id":"editor-persist-project", "shots":[]})
        first_id = panel.create_custom_node("text_node", QPointF(0, 0), {
            "title":"脚本 A", "content":"默认文字 A"})
        second_id = panel.create_custom_node("image_node", QPointF(500, 0), {
            "title":"图片 B", "content":"默认文字 B"})

        panel.show_inline_editor(panel._nodes[first_id])
        panel._inline_text_editor.setPlainText("我修改后的脚本")
        # Switching nodes closes the old editor and must commit its live text.
        panel.show_inline_editor(panel._nodes[second_id])
        self.assertEqual("我修改后的脚本",
                         panel._custom_record(first_id)["content"])

        panel.show_inline_editor(panel._nodes[first_id])
        self.assertEqual("我修改后的脚本",
                         panel._inline_text_editor.toPlainText())
        panel._inline_text_editor.clear()
        panel.hide_inline_editor()
        panel.show_inline_editor(panel._nodes[first_id])
        self.assertEqual("", panel._inline_text_editor.toPlainText())
        self.assertEqual("", panel._custom_record(first_id)["content"])

    def test_inline_shot_edit_persists_and_invalidates_compiled_prompts(self):
        shot = {
            "id":"shot-edit", "number":1, "duration":5,
            "visual":"原始镜头说明", "production_ready":True,
            "final_image_prompt":"旧图片提示词",
            "final_video_prompt":"旧视频提示词",
        }
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__":panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        panel.set_storyboard({"id":"shot-editor-persist", "shots":[shot]})
        node_id = "shot:shot-edit"
        panel.show_inline_editor(panel._nodes[node_id])
        panel._inline_text_editor.setPlainText("女孩从左向右穿过门口")
        panel.hide_inline_editor()

        self.assertEqual("女孩从左向右穿过门口", shot["visual"])
        self.assertFalse(shot["production_ready"])
        self.assertNotIn("final_image_prompt", shot)
        self.assertNotIn("final_video_prompt", shot)
        panel.show_inline_editor(panel._nodes[node_id])
        self.assertEqual("女孩从左向右穿过门口",
                         panel._inline_text_editor.toPlainText())

    def test_project_script_is_optional_input_to_canvas_production(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda: None
        panel.set_storyboard({"id": "script-bridge", "shots": []})
        script_id = panel.create_custom_node(
            "text_node", QPointF(0, 0), {
                "title":"20秒项目脚本", "content":"女孩在雨夜进入便利店寻找钥匙。"})

        panel.create_storyboard_from_script(
            panel._nodes[script_id], "女孩在雨夜进入便利店寻找钥匙。")

        storyboards = [record for record in panel._positions()["__custom_nodes__"]
                       if record.get("type") == "storyboard_node"]
        self.assertEqual(1, len(storyboards))
        self.assertEqual("女孩在雨夜进入便利店寻找钥匙。",
                         storyboards[0]["content"])
        self.assertFalse(panel._standalone_tasks)
        self.assertIn(
            {"source":script_id, "target":storyboards[0]["id"], "type":"script"},
            panel._positions()["__workflow_edges__"])

    def test_script_creates_project_without_spending_and_preserves_planning_model(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        panel.set_storyboard({"id":"planning-confirm", "shots":[]})
        script_id = panel.create_custom_node(
            "text_node", QPointF(0, 0), {
                "title":"正式剧本", "content":"女孩在雨中推开仓库门。"})
        panel.create_storyboard_from_script(
            panel._nodes[script_id], "女孩在雨中推开仓库门。",
            auto_start=True, planning_model_data=("deepseek", "deepseek-chat"))
        projects = [value for value in panel._positions()["__custom_nodes__"]
                    if value.get("type") == "storyboard_node"]
        self.assertEqual(1, len(projects))
        self.assertFalse(panel._standalone_tasks)
        self.assertEqual("deepseek", projects[0]["planning_provider"])
        self.assertEqual("deepseek-chat", projects[0]["planning_model"])
        self.assertEqual("", projects[0]["pipeline_stage"])
        self.assertIn("请确认拆镜模型", projects[0]["status"])

    def test_unconfirmed_dock_start_only_opens_planning_settings(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        panel.set_storyboard({"id":"planning-gate", "shots":[]})
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {
                "content":"女孩推门", "pipeline_stage":"",
                "planning_provider":"openai", "planning_model":"gpt-5.5",
            })
        with patch.object(panel, "submit_canvas_storyboard") as submit, \
                patch.object(panel, "show_inline_editor") as show:
            panel.continue_canvas_production(
                panel._nodes[source_id], from_async=False,
                planning_confirmed=False)
            self.app.processEvents()
            submit.assert_not_called()
            self.assertGreaterEqual(show.call_count, 1)
        source = panel._custom_record(source_id)
        self.assertEqual("", source["pipeline_stage"])
        self.assertIn("等待确认拆镜模型", source["status"])

    def test_confirmed_storyboard_uses_locked_provider_model_and_temperature(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        panel.set_storyboard({"id":"planning-submit", "shots":[]})
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {
                "content":"女孩推门", "pipeline_stage":"",
                "planning_provider":"deepseek", "planning_model":"deepseek-chat",
                "planning_temperature":0.2, "shot_count":4,
            })
        provider = SimpleNamespace(name="deepseek")
        submitted = []
        manager = SimpleNamespace(
            registry=SimpleNamespace(
                by_capability=lambda capability: [provider] if capability == "chat" else []),
            submit=lambda name, request: (
                submitted.append((name, request)) or TaskHandle(
                    id="planning-task", provider_name=name, operation=request.operation)),
        )
        with patch.object(canvas_module, "get_ai_manager", return_value=manager):
            panel.continue_canvas_production(
                panel._nodes[source_id], from_async=False,
                planning_confirmed=True)
        self.assertEqual(1, len(submitted))
        name, request = submitted[0]
        self.assertEqual("deepseek", name)
        self.assertEqual("deepseek-chat", request.params["model"])
        self.assertEqual(0.2, request.params["temperature"])
        self.assertIn("严格执行原剧本",
                      request.inputs["messages"][0]["content"])
        self.assertEqual("planning", panel._custom_record(source_id)["pipeline_stage"])

    def test_checkpoint_mode_pauses_only_for_asset_choice_then_continues(self):
        folder = Path(tempfile.mkdtemp())
        asset_path = folder / "scene.png"
        image = QImage(160, 90, QImage.Format.Format_RGB32)
        image.fill(QColor("#496173")); image.save(str(asset_path))

        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda: None
        panel.set_storyboard({"id":"auto-checkpoints", "shots":[{
            "id":"s1", "number":1, "duration":5, "visual":"走进房间",
            "assets":[],
        }]})
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {
                "title":"自动制片", "content":"人物走进房间",
                "automation_mode":"checkpoints", "auto_run_enabled":True,
                "pipeline_stage":"assets_generated"})
        asset_id = panel.create_custom_node(
            "image_node", QPointF(400, 0), {
                "title":"房间场景", "asset_kind":"scene",
                "path":str(asset_path), "locked":False, "asset_version":1,
                "scene_reference_set":{
                    role:str(asset_path) for role, _label, _prompt
                    in canvas_module.SCENE_VIEW_SPECS},
            })
        panel._positions().setdefault("__workflow_edges__", []).append(
            {"source":source_id, "target":asset_id, "type":"scene"})
        panel.refresh()

        with patch.object(panel, "prepare_canvas_blocking_storyboards") as advance:
            panel.continue_canvas_production(panel._nodes[source_id], from_async=True)
            self.assertEqual("assets", panel._custom_record(source_id)["awaiting_gate"])
            self.assertFalse(panel._custom_record(asset_id)["locked"])
            advance.assert_not_called()

            panel.continue_canvas_production(panel._nodes[source_id], from_async=False)
            self.assertTrue(panel._custom_record(asset_id)["locked"])
            advance.assert_called_once()

    def test_image_candidates_can_be_chosen_or_auto_adopted(self):
        folder = Path(tempfile.mkdtemp())
        candidate = folder / "candidate.png"
        image = QImage(160, 90, QImage.Format.Format_RGB32)
        image.fill(QColor("#815b61")); image.save(str(candidate))
        shot = {"id":"s1", "number":1, "duration":5, "visual":"转身",
                "assets":[{"path":str(candidate), "kind":"image"}]}

        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda: None
        panel.set_storyboard({"id":"candidate-choice", "shots":[shot]})
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {
                "title":"自动制片", "content":"人物转身",
                "automation_mode":"checkpoints", "auto_run_enabled":True,
                "pipeline_stage":"image_candidates_ready"})
        generator_id = panel.create_custom_node(
            "image_node", QPointF(400, 0), {
                "title":"镜头 01 · 图片生成器", "generator_kind":"image",
                "shot_id":"s1", "shot_ids":["s1"], "path":str(candidate),
                "candidates":[str(candidate)]})
        panel.create_custom_node(
            "workflow_group", QPointF(200, 250), {
                "title":"图片生成器组", "generator_kind":"image",
                "source_node_id":source_id, "group_nodes":[generator_id],
                "status":"整组执行完成"})
        panel.refresh()

        with patch.object(panel, "create_and_execute_video_group") as advance:
            panel.continue_canvas_production(panel._nodes[source_id], from_async=True)
            self.assertEqual("images", panel._custom_record(source_id)["awaiting_gate"])
            self.assertFalse(shot.get("selected_image_asset"))
            advance.assert_not_called()

            panel.continue_canvas_production(panel._nodes[source_id], from_async=False)
            self.assertEqual(str(candidate), shot["selected_image_asset"])
            advance.assert_called_once()

    def test_generated_nodes_handoff_video_and_tts_to_editor(self):
        folder = Path(tempfile.mkdtemp())
        video = folder / "shot.mp4"
        audio = folder / "dialogue.wav"
        video.write_bytes(b"video"); audio.write_bytes(b"audio")
        shot = {"id":"s1", "number":1, "start":0, "duration":5,
                "visual":"人物开口", "selected_video_asset":str(video),
                "dialogue_audio":str(audio), "assets":[{
                    "path":str(video), "kind":"video", "actual_duration":5}]}

        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda: None
        panel.set_storyboard({"id":"editor-handoff", "shots":[shot]})
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {"title":"制片项目"})
        video_id = panel.create_custom_node(
            "video_node", QPointF(500, 0), {
                "title":"镜头视频", "path":str(video), "shot_id":"s1",
                "generator_kind":"video"})

        project_payload = panel._editor_payload_for_node(
            panel._nodes[source_id], "replace")
        self.assertEqual("storyboard", project_payload["mode"])
        self.assertEqual("replace", project_payload["audio_policy"])
        self.assertEqual(str(audio),
                         project_payload["board"]["shots"][0]["dialogue_audio"])

        single_payload = panel._editor_payload_for_node(
            panel._nodes[video_id], "duck")
        self.assertEqual("storyboard", single_payload["mode"])
        self.assertEqual("duck", single_payload["audio_policy"])
        self.assertEqual(str(video),
                         single_payload["board"]["shots"][0]["selected_video_asset"])

        emitted = []
        panel.sendToEditorRequested.connect(emitted.append)
        panel.send_node_to_editor(panel._nodes[video_id], "replace")
        self.assertEqual("replace", emitted[0]["audio_policy"])

    def test_direct_image_workflow_supports_typed_refs_and_video_endpoints(self):
        folder = Path(tempfile.mkdtemp())
        paths = []
        for name, color in (("character", "#8f6655"), ("first", "#345f91"),
                            ("last", "#4f8c61")):
            path = folder / f"{name}.png"
            image = QImage(160, 90, QImage.Format.Format_RGB32)
            image.fill(QColor(color)); image.save(str(path)); paths.append(path)

        submitted = []
        providers = {
            "image_to_video": [SimpleNamespace(name="seedance")],
            "text_to_video": [SimpleNamespace(name="seedance")],
            "image_edit": [SimpleNamespace(name="gptimage")],
            "text_to_image": [SimpleNamespace(name="gptimage")],
        }

        def submit(name, request):
            submitted.append((name, request))
            return TaskHandle(
                id=f"direct-{len(submitted)}", provider_name=name,
                operation=request.operation, status=TaskStatus.RUNNING)

        manager = SimpleNamespace(
            registry=SimpleNamespace(by_capability=lambda value: providers.get(value, [])),
            submit=submit)
        with patch.object(canvas_module, "get_ai_manager", return_value=manager), \
                patch.object(QMessageBox, "information",
                             return_value=QMessageBox.StandardButton.Ok):
            panel = canvas_module.ProductionCanvasTab()
            panel._checkpoint_timer.stop(); panel._task_timer.stop()
            panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
            panel._save_layout_now = lambda: None
            panel.set_storyboard({"id": "direct-image", "shots": []})

            character_id = panel.create_custom_node(
                "image_node", QPointF(0, 0), {"path": str(paths[0])})
            first_id = panel.create_custom_node(
                "image_node", QPointF(0, 300), {"path": str(paths[1])})
            last_id = panel.create_custom_node(
                "image_node", QPointF(0, 600), {"path": str(paths[2])})
            video_id = panel.create_custom_node(
                "video_node", QPointF(500, 300), {"content": "人物向前走"})

            panel.set_image_reference_role(panel._nodes[character_id], "character")
            panel.connect_workflow_nodes(panel._nodes[character_id], panel._nodes[video_id])
            video = panel._custom_record(video_id)
            self.assertFalse(video.get("first_frame"))
            self.assertEqual("character", video["reference_assets"][0]["role"])

            panel.connect_workflow_nodes(panel._nodes[first_id], panel._nodes[video_id])
            panel.connect_workflow_nodes(panel._nodes[last_id], panel._nodes[video_id])
            video = panel._custom_record(video_id)
            self.assertEqual(str(paths[1]), video["first_frame"])
            self.assertEqual(str(paths[2]), video["last_frame"])
            panel.update_custom_setting(
                panel._nodes[video_id], "creative_prompt", "镜头缓慢环绕，雨势逐渐增强")

            panel.submit_standalone_generation(
                panel._nodes[video_id], "人物向前走，固定身份和空间", "图生视频")
            request = submitted[-1][1]
            self.assertEqual("image_to_video", request.operation)
            self.assertEqual(str(paths[1]), request.inputs["image"])
            self.assertEqual(str(paths[2]), request.inputs["last_frame"])
            self.assertEqual("character", request.inputs["reference_assets"][0]["role"])
            self.assertIn("用户创意与动态意图", request.inputs["prompt"])
            self.assertIn("镜头缓慢环绕，雨势逐渐增强", request.inputs["prompt"])
            self.assertEqual(
                "镜头缓慢环绕，雨势逐渐增强",
                request.metadata["creative_prompt"])

            edit_id = panel.create_custom_node(
                "image_node", QPointF(900, 300), {"path": str(paths[1]), "ratio": "16:9"})
            panel.submit_standalone_generation(
                panel._nodes[edit_id], "扩展为更宽的街道环境", "智能扩图")
            edit_request = submitted[-1][1]
            self.assertEqual("image_edit", edit_request.operation)
            self.assertEqual("composition", edit_request.inputs["reference_assets"][0]["role"])
            self.assertIn(str(paths[1]), panel._custom_record(edit_id)["candidates"])

    def test_character_regeneration_upgrades_legacy_three_view_to_four_nodes(self):
        folder = Path(tempfile.mkdtemp())
        paths = []
        for index in range(5):
            path = folder / f"character_{index}.png"
            image = QImage(160, 120, QImage.Format.Format_RGB32)
            image.fill(QColor("#79556f")); image.save(str(path)); paths.append(path)
        outputs = iter(paths[1:])
        submitted = []

        def submit(name, request):
            submitted.append(request)
            return TaskHandle(
                id=f"character-suite-{len(submitted)}", provider_name=name,
                operation=request.operation, status=TaskStatus.DONE, progress=1,
                result=TaskResult(True, next(outputs)))

        manager = SimpleNamespace(
            registry=SimpleNamespace(by_capability=lambda _value: [
                SimpleNamespace(name="gptimage")]),
            submit=submit)
        with patch.object(canvas_module, "get_ai_manager", return_value=manager), \
                patch.object(QMessageBox, "information",
                             return_value=QMessageBox.StandardButton.Ok):
            panel = canvas_module.ProductionCanvasTab()
            panel._checkpoint_timer.stop(); panel._task_timer.stop()
            panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
            panel._save_layout_now = lambda: None
            panel.set_storyboard({"id":"legacy-character", "shots":[]})
            node_id = panel.create_custom_node(
                "image_node", QPointF(0, 0), {
                    "title":"旧人物三视图", "content":"角色三视图，粉色长发少女",
                    "path":str(paths[0])})
            record = panel._custom_record(node_id)
            record.update({
                "asset_kind":"character", "asset_role":"character_reference",
                "asset_name":"阿青", "asset_version":1, "locked":False,
                "adopted":True, "candidates":[str(paths[0])],
            })
            panel.refresh()

            panel.regenerate_production_asset(panel._nodes[node_id])
            for _ in range(5):
                panel._poll_standalone_tasks()

            self.assertEqual(
                ["portrait", "face_closeup", "expressions", "turnaround"],
                [request.metadata["character_role"] for request in submitted])
            self.assertTrue(all("三视图" not in request.inputs["prompt"]
                                for request in submitted))
            views = [value for value in panel._positions()["__custom_nodes__"]
                     if value.get("reference_parent_id") == node_id]
            self.assertEqual(3, len(views))
            self.assertEqual(4, 1 + len(views))
            self.assertEqual(str(paths[1]), panel._custom_record(node_id)["path"])
            self.assertEqual("阿青 · 角色立绘", panel._custom_record(node_id)["title"])

    def test_asset_versions_lock_and_invalidation(self):
        folder = Path(tempfile.mkdtemp())
        paths = []
        # Motion storyboards are generated as independent clean native-ratio panels
        # before local assembly, so this end-to-end fixture needs enough
        # provider outputs for the later final-image and video stages too.
        for index in range(50):
            path = folder / f"asset_{index}.png"
            image = QImage(1024, 576, QImage.Format.Format_RGB32)
            image.fill(QColor.fromHsv((index * 47) % 360, 145, 175))
            image.save(str(path)); paths.append(path)
        outputs = iter(paths)
        plan = {
            "title": "资产测试", "summary": "", "visual_bible": "电影写实",
            "characters": [{"name": "阿青", "description": "蓝衣女孩",
                            "image_prompt": "角色三视图"}],
            "scenes": [{"name": "雨夜车站", "description": "夜雨",
                        "image_prompt": "车站空镜"}],
            "elements": [{"name": "红伞", "description": "红色木柄伞",
                          "image_prompt": "红伞多角度"}],
            "shots": [
                {"shot_size": "全景", "duration": 4, "visual": "女孩撑伞",
                 "action_line": "向右", "camera": "跟拍", "transition": "切",
                 "dialogue": "", "image_prompt": "女孩在车站"},
                {"shot_size": "近景", "duration": 7, "visual": "女孩回头",
                 "action_line": "停下", "camera": "固定", "transition": "切",
                 "dialogue": "谁？", "image_prompt": "女孩在雨中回头"},
            ],
        }
        registry = SimpleNamespace(by_capability=lambda operation: [
            SimpleNamespace(name="openai" if operation == "chat" else "gptimage")])
        submitted = []

        def submit(name, request):
            submitted.append((name, request))
            if request.operation == "chat" and request.metadata.get("purpose") == "blocking_storyboard":
                input_shots = json.loads(request.inputs["messages"][1]["content"])
                blocking = {"shots": [{
                    "id": value["id"],
                    "spatial_layout": "车站入口在左，长椅在右，月台在后景",
                    "character_positions": [{
                        "name": "阿青", "start": "x=.30,y=.70,中景，朝右",
                        "end": "x=.55,y=.70,中景，朝右",
                        "movement": "沿月台向屏幕右侧", "gaze": "月台右侧",
                        "facing": "身体朝右"}],
                    "blocking": "阿青保持在长椅左侧并向右移动",
                    "eyeline": "视线始终朝屏幕右侧",
                    "camera_position": "轴线南侧，眼平，50mm",
                    "camera_movement": "固定机位",
                    "axis_rule": "机位始终位于人物运动轴南侧",
                    "foreground": "雨丝", "midground": "阿青和长椅",
                    "background": "月台出口", "frame_start": "阿青位于画面左三分之一",
                    "frame_end": "阿青位于画面中央", "continuity": "保持向右运动",
                } for value in input_shots]}
                data = json.dumps(blocking, ensure_ascii=False)
            elif (request.operation == "chat" and request.metadata.get("purpose") ==
                  "canvas_storyboard_shot_batch"):
                start = int(request.metadata["batch_start"])
                end = int(request.metadata["batch_end"])
                batch = {"shots": [{
                    **plan["shots"][number - 1], "shot_number":number,
                } for number in range(start, end + 1)]}
                data = json.dumps(batch, ensure_ascii=False)
            elif request.operation == "chat":
                data = json.dumps(plan, ensure_ascii=False)
            else:
                data = next(outputs)
            return TaskHandle(
                id=f"{name}-{request.operation}-{uuid.uuid4().hex}", provider_name=name,
                operation=request.operation, status=TaskStatus.DONE, progress=1,
                result=TaskResult(True, data))

        manager = SimpleNamespace(registry=registry, submit=submit)
        with patch.object(canvas_module, "get_ai_manager", return_value=manager), \
                patch.object(QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok), \
                patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok):
            panel = canvas_module.ProductionCanvasTab()
            panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
            panel._save_layout_now = lambda: None
            board = {"id": "asset-version-test", "shots": []}
            panel.set_storyboard(board)
            self.assertNotIn("director:project", panel._nodes)
            source_id = panel.create_custom_node(
                "storyboard_node", QPointF(0, 0),
                {"content": "雨夜", "shot_count": 2, "style": "电影写实"})
            panel.submit_canvas_storyboard(
                panel._nodes[source_id], "雨夜", "1 · 拆解镜头")
            for _ in range(2):
                panel._poll_standalone_tasks()
            planning_requests = [
                request for _provider, request in submitted
                if str(request.metadata.get("purpose") or "").startswith(
                    "canvas_storyboard_")]
            self.assertEqual(
                ["canvas_storyboard_foundation",
                 "canvas_storyboard_shot_batch"],
                [request.metadata["purpose"] for request in planning_requests])
            self.assertTrue(all(
                request.params == {"model":"gpt-5.5", "temperature":0.5} and
                "retry_count" not in request.metadata and
                "retry_transient_only" not in request.metadata
                for request in planning_requests))
            panel.prepare_canvas_storyboard_assets(panel._nodes[source_id])
            for _ in range(12):
                panel._poll_standalone_tasks()
            self.assertEqual([], [request for _provider, request in submitted
                                  if request.metadata.get("purpose") ==
                                  "canvas_storyboard_image"])
            self.assertEqual("assets_generated",
                             panel._custom_record(source_id)["pipeline_stage"])

            assets = [record for record in panel._positions()["__custom_nodes__"]
                      if record.get("asset_kind")]
            self.assertEqual({"character", "scene", "element"},
                             {record["asset_kind"] for record in assets})
            character_asset = next(record for record in assets
                                   if record["asset_kind"] == "character")
            self.assertEqual(
                {"portrait", "face_closeup", "expressions", "turnaround"},
                set(character_asset["character_reference_set"]))
            self.assertEqual(1, character_asset["asset_version"])
            self.assertTrue(all(not record["locked"] for record in assets))
            character_views = [record for record in panel._positions()["__custom_nodes__"]
                               if record.get("reference_parent_id") == character_asset["id"]]
            self.assertEqual(3, len(character_views))
            self.assertEqual(
                {"face_closeup", "expressions", "turnaround"},
                {record["character_panel_role"] for record in character_views})
            self.assertEqual(4, 1 + len(character_views))
            self.assertFalse(any(record.get("character_panel_role") == "portrait"
                                 for record in character_views))
            character_requests = [
                request for _provider, request in submitted
                if request.metadata.get("purpose") == "canvas_character_sheet" and
                request.metadata.get("character_role")]
            self.assertEqual(
                {"portrait", "face_closeup", "expressions", "turnaround"},
                {request.metadata["character_role"] for request in character_requests})
            self.assertEqual(
                {"portrait":"1024x1536", "face_closeup":"1024x1024",
                 "expressions":"1536x1024", "turnaround":"1536x1024"},
                {request.metadata["character_role"]:request.params["size"]
                 for request in character_requests})
            self.assertTrue(all("三视图" not in request.inputs["prompt"]
                                for request in character_requests))
            scene_requests = [
                request for _provider, request in submitted
                if request.metadata.get("purpose") == "canvas_character_sheet" and
                request.metadata.get("scene_role")]
            self.assertEqual(
                {"master", "reverse", "left", "right", "topdown"},
                {request.metadata["scene_role"] for request in scene_requests})
            self.assertEqual("text_to_image", scene_requests[0].operation)
            self.assertTrue(all(
                request.operation == "image_edit" for request in scene_requests[1:]))
            self.assertTrue(all(
                "同一物理空间的权威视图" in request.inputs["prompt"]
                for request in scene_requests))
            scene_asset = next(record for record in assets
                               if record["asset_kind"] == "scene")
            self.assertEqual(5, len(scene_asset["scene_reference_set"]))
            scene_views = [record for record in panel._positions()["__custom_nodes__"]
                           if record.get("reference_parent_id") == scene_asset["id"]]
            self.assertEqual(
                {"reverse", "left", "right", "topdown"},
                {record["scene_view_role"] for record in scene_views})

            panel.compile_canvas_storyboard_prompts(panel._nodes[source_id])
            self.assertFalse(board["shots"][0].get("production_ready"))
            for record in assets:
                panel.set_production_asset_lock(panel._nodes[record["id"]], True)
            panel.prepare_canvas_blocking_storyboards(panel._nodes[source_id])
            for _ in range(12):
                panel._poll_standalone_tasks()

            self.assertTrue(all(shot["blocking_ready"] for shot in board["shots"]))
            self.assertIn("x=.30", board["shots"][0]["character_positions"][0]["start"])
            storyboard_requests = [request for _provider, request in submitted
                                   if request.metadata.get("purpose") ==
                                   "canvas_storyboard_panel"]
            self.assertEqual(9, len(storyboard_requests))
            self.assertTrue(all(
                "原生 16:9 横向画面" in request.inputs["prompt"] and
                "不是拼图" in request.inputs["prompt"] and
                len(request.inputs.get("reference_assets") or []) <= 3
                for request in storyboard_requests))
            self.assertEqual([0, 1, 2, 3, 0, 1, 2, 3, 4], [
                request.metadata["frame_index"] for request in storyboard_requests])
            self.assertEqual(4, len(board["shots"][0]["motion_keyframes"]))
            self.assertEqual(5, len(board["shots"][1]["motion_keyframes"]))
            self.assertEqual(1, sum(frame["is_hero"]
                                    for frame in board["shots"][0]["motion_keyframes"]))
            self.assertTrue(board["shots"][0]["motion_board_path"])
            motion_asset = next(value for value in board["shots"][0]["assets"]
                                if value.get("subtype") == "motion_storyboard")
            self.assertEqual(4, motion_asset["frame_count"])

            panel.compile_canvas_storyboard_prompts(panel._nodes[source_id])
            self.assertTrue(board["shots"][0]["production_ready"])
            self.assertIn("运动分镜仅作为上述结构化文字合同",
                          board["shots"][0]["final_image_prompt"])
            self.assertIn("严禁出现在视频任何一帧",
                          board["shots"][0]["final_video_prompt"])
            self.assertIn("只生成一张原生 16:9 单帧定稿",
                          board["shots"][0]["final_image_prompt"])
            self.assertIn("严格按 K1–K5", board["shots"][1]["final_video_prompt"])
            self.assertIn("外部 TTS", board["shots"][1]["final_video_prompt"])
            self.assertEqual({"character", "scene"},
                             {value["kind"] for value in board["shots"][0]["asset_manifest"]})
            character_manifest = next(value for value in board["shots"][0]["asset_manifest"]
                                      if value["kind"] == "character")
            self.assertEqual(4, len(character_manifest["reference_paths"]))
            board["shots"][0]["selected_image_asset"] = str(paths[15])
            panel.compile_canvas_storyboard_prompts(panel._nodes[source_id])
            self.assertEqual(board["shots"][0]["id"],
                             board["shots"][1]["continuity_source_shot_id"])
            self.assertEqual(str(paths[15]), board["shots"][1]["continuity_reference"])

            source = panel._custom_record(source_id)
            source.update({"production_scope": "selected", "production_ratio": "9:16",
                           "candidate_count": 3, "image_provider": "gptimage"})
            panel._nodes[source_id].payload.update(source)

            board["shots"][1]["production_selected"] = True
            board["shots"][1]["keyframe_strategy"] = "first_last"
            board["shots"][1]["endpoint_pair_enabled"] = True
            panel.create_canvas_generator_group(panel._nodes[source_id], "image")
            generators = [record for record in panel._positions()["__custom_nodes__"]
                          if record.get("generator_kind") == "image" and
                          record.get("type") == "image_node"]
            self.assertEqual(2, len(generators))
            generators_by_role = {value["frame_role"]:value for value in generators}
            start_generator = generators_by_role["start"]
            end_generator = generators_by_role["end"]
            self.assertEqual(board["shots"][1]["id"], start_generator["shot_id"])
            self.assertEqual("9:16", start_generator["ratio"])
            self.assertEqual(3, start_generator["candidate_count"])
            self.assertEqual(7, start_generator["duration"])
            self.assertEqual("gptimage", start_generator["provider_name"])
            self.assertNotIn(board["shots"][1]["draft_panel"],
                             start_generator["references"])
            self.assertEqual(board["shots"][1]["scene_stage_capture"],
                             start_generator["references"][0])
            self.assertIn(board["shots"][1]["scene_master_path"],
                          start_generator["references"])
            self.assertIn(str(paths[15]), start_generator["references"])
            self.assertFalse(any(
                value.get("path") == board["shots"][1]["draft_panel"]
                for value in start_generator["reference_assets"]))
            self.assertEqual(board["shots"][1]["draft_panel"],
                             start_generator["motion_board_path"])
            self.assertEqual(5, len(start_generator["motion_keyframes"]))
            batches = panel._positions()["__production_batches__"]
            self.assertEqual("ready", batches[-1]["status"])
            self.assertEqual(6, batches[-1]["estimated_units"])
            group = next(record for record in panel._positions()["__custom_nodes__"]
                         if record.get("type") == "workflow_group" and
                         record.get("generator_kind") == "image")
            panel.execute_workflow_group(panel._nodes[group["id"]])
            provider_name, request = submitted[-1]
            self.assertEqual("gptimage", provider_name)
            self.assertEqual(3, request.params["n"])
            self.assertEqual("1152x2048", request.params["size"])
            self.assertNotIn("mask", request.inputs)
            self.assertEqual("recompose", start_generator["spatial_qc_mode"])
            panel._poll_standalone_tasks()
            self.assertEqual("image", board["shots"][1]["asset_type"])
            self.assertFalse(board["shots"][1].get("selected_image_asset"))
            board["shots"][1]["selected_image_asset"] = start_generator["path"]
            board["shots"][1]["selected_asset"] = start_generator["path"]
            self.assertTrue(panel._prepare_and_execute_end_frame_generators(source_id))
            self.assertEqual(start_generator["path"], end_generator["references"][0])
            self.assertEqual([start_generator["path"]], end_generator["references"])
            self.assertEqual(1, len(end_generator["reference_assets"]))
            self.assertEqual(start_generator["path"], end_generator["endpoint_source_path"])
            self.assertEqual("pixel_lock", end_generator["spatial_qc_mode"])
            self.assertIn("mask", submitted[-1][1].inputs)
            panel._poll_standalone_tasks()
            board["shots"][1]["selected_end_image_asset"] = end_generator["path"]
            board["shots"][1]["endpoint_pair_qc"] = {"status":"pass"}
            panel._custom_record(source_id)["production_scope"] = "all"
            panel._nodes[source_id].payload["production_scope"] = "all"
            submitted_before_video = len(submitted)
            panel._task_timer.stop()
            panel.create_and_execute_video_group(panel._nodes[source_id])
            # Video execution is intentionally deferred until the scene rebuild
            # has completed, so no deleted QGraphicsItem can be reused.
            self.app.processEvents()
            video_generators = [record for record in panel._positions()["__custom_nodes__"]
                                if record.get("generator_kind") == "video" and
                                record.get("type") == "video_node"]
            self.assertEqual(2, len(video_generators))
            self.assertEqual([[board["shots"][0]["id"]], [board["shots"][1]["id"]]],
                             [value["shot_ids"] for value in video_generators])
            self.assertTrue(all(value["video_generation_mode"] == "smart"
                                for value in video_generators))
            self.assertEqual(submitted_before_video + 1, len(submitted))
            self.assertEqual(board["shots"][0]["selected_image_asset"],
                             video_generators[0]["references"][0])
            provider_name, request = submitted[-1]
            self.assertEqual("gptimage", provider_name)
            self.assertEqual("image_to_video", request.operation)
            self.assertEqual(4, request.params["duration"])
            self.assertIn("内部包含 1 个导演分镜", request.inputs["prompt"])
            panel._poll_standalone_tasks()
            self.assertEqual(submitted_before_video + 2, len(submitted))
            first_segment_second_take = submitted[-1][1]
            self.assertEqual(board["shots"][0]["selected_image_asset"],
                             first_segment_second_take.inputs["image"])
            panel._poll_standalone_tasks()
            self.assertFalse(board["shots"][0].get("selected_video_asset"))
            self.assertEqual("video_candidates_ready",
                             panel._custom_record(source_id)["pipeline_stage"])
            video_group = next(
                record for record in panel._positions()["__custom_nodes__"]
                if record.get("type") == "workflow_group" and
                record.get("generator_kind") == "video")
            first_generator = video_generators[0]
            first_path = first_generator["candidate_batch_paths"][0]
            first_generator.update({
                "path":first_path, "adopted":True, "handoff_approved":True,
                "clip_qc":{"status":"complete", "passed":True},
            })
            board["shots"][0].update({
                "selected_video_asset":first_path,
                "selected_asset":first_path, "preview_asset":first_path,
                "video_segment_node_id":first_generator["id"],
                "video_segment_offset":0.0,
            })
            panel._submit_next_serial_video(video_group["id"])
            self.assertEqual(submitted_before_video + 3, len(submitted))
            second_request = submitted[-1][1]
            self.assertEqual(board["shots"][1]["selected_image_asset"],
                             second_request.inputs["image"])
            self.assertEqual(board["shots"][1]["selected_end_image_asset"],
                             second_request.inputs["last_frame"])
            panel._poll_standalone_tasks()
            self.assertEqual(submitted_before_video + 4, len(submitted))
            panel._poll_standalone_tasks()
            second_generator = video_generators[1]
            second_path = second_generator["candidate_batch_paths"][0]
            second_generator.update({
                "path":second_path, "adopted":True, "handoff_approved":True,
                "clip_qc":{"status":"complete", "passed":True},
            })
            board["shots"][1].update({
                "selected_video_asset":second_path,
                "selected_asset":second_path, "preview_asset":second_path,
                "video_segment_node_id":second_generator["id"],
                "video_segment_offset":0.0,
            })
            panel._submit_next_serial_video(video_group["id"])
            self.assertTrue(board["shots"][0]["selected_video_asset"])
            self.assertTrue(board["shots"][1]["selected_video_asset"])
            self.assertEqual(0.0, board["shots"][0]["video_segment_offset"])
            self.assertEqual(0.0, board["shots"][1]["video_segment_offset"])
            panel.set_production_asset_lock(panel._nodes[assets[0]["id"]], False)
            self.assertTrue(all(not shot["production_ready"] for shot in board["shots"]))
            self.assertTrue(all(record["invalidated"] for record in generators))

            skill_id = panel.create_canvas_skill("camera_grid_9", QPointF(200, 900))
            panel.execute_canvas_skill(panel._nodes[skill_id], execute=False)
            camera_nodes = [record for record in panel._positions()["__custom_nodes__"]
                            if record.get("skill_id") == "camera_grid_9" and
                            record.get("type") == "image_node"]
            self.assertEqual(9, len(camera_nodes))
            skill_group = next(record for record in panel._positions()["__custom_nodes__"]
                               if record.get("skill_id") == "camera_grid_9" and
                               record.get("type") == "workflow_group")
            template = {"name":"九宫格模板", "group_title":"九宫格模板",
                        "generator_kind":"image", "members":camera_nodes[:2]}
            panel.instantiate_workflow_template(template, QPointF(200, 1400))
            reused = [record for record in panel._positions()["__custom_nodes__"]
                      if record.get("type") == "workflow_group" and
                      record.get("title") == "九宫格模板"]
            self.assertEqual(1, len(reused))
            self.assertEqual(2, len(reused[0]["group_nodes"]))

            director_id = panel.create_canvas_skill("ai_director", QPointF(700, 1400))
            self.assertTrue(panel._local_image_data_url(str(paths[0])).startswith("data:image/png;base64,"))
            review = {"summary":"第二镜轴线有风险", "score":72, "shots":[{
                "id":board["shots"][1]["id"], "score":65, "passed":False,
                "issues":["跳轴"], "revision":"保持人物向右运动并将机位留在轴线同侧"}]}
            review_handle = TaskHandle(
                id="director-review", provider_name="openai", operation="chat",
                status=TaskStatus.DONE, progress=1,
                result=TaskResult(True, json.dumps(review, ensure_ascii=False)))
            panel._standalone_tasks[review_handle.id] = {
                "handle":review_handle, "node_id":director_id,
                "provider":"openai", "kind":"director_review", "auto_retry":False}
            panel._poll_standalone_tasks()
            self.assertEqual(65, board["shots"][1]["quality_score"])
            self.assertFalse(board["shots"][1]["quality_passed"])
            self.assertEqual("blocking", board["shots"][1]["repair_target"])
            self.assertEqual(3, board["shots"][1]["repair_plan"]["rewind_step"])
            self.assertNotIn("视觉修复", board["shots"][1].get("final_image_prompt", ""))

            source_node = panel._nodes[source_id]
            panel.create_dialogue_audio_group(source_node)
            dialogue_nodes = [record for record in panel._positions()["__custom_nodes__"]
                              if record.get("generator_kind") == "audio" and
                              record.get("type") == "audio_node"]
            self.assertEqual(1, len(dialogue_nodes))
            self.assertEqual("谁？", dialogue_nodes[0]["content"])

            upscale_id = panel.create_custom_node("image_node", QPointF(1200, 1400), {
                "title":"待高清", "path":str(paths[0]), "content":""})
            panel.submit_standalone_generation(panel._nodes[upscale_id], "", "图片高清")
            _provider, upscale_request = submitted[-1]
            self.assertEqual("image_edit", upscale_request.operation)
            self.assertEqual(str(paths[0]), upscale_request.inputs["image"])

            before = len([record for record in panel._positions()["__custom_nodes__"]
                          if record.get("type") == "storyboard_node"])
            panel.open_handdraw_storyboard()
            after = len([record for record in panel._positions()["__custom_nodes__"]
                         if record.get("type") == "storyboard_node"])
            self.assertEqual(before + 1, after)

            editor = canvas_module._NodeTextEdit()
            QApplication.clipboard().setText("可复制粘贴的剧情")
            editor.keyPressEvent(QKeyEvent(
                QKeyEvent.Type.KeyPress, canvas_module.Qt.Key.Key_V,
                canvas_module.Qt.KeyboardModifier.ControlModifier))
            self.assertEqual("可复制粘贴的剧情", editor.toPlainText())

            panel.view.keyPressEvent(QKeyEvent(
                QKeyEvent.Type.KeyPress, canvas_module.Qt.Key.Key_Space,
                canvas_module.Qt.KeyboardModifier.NoModifier))
            self.assertEqual(canvas_module.Qt.CursorShape.OpenHandCursor,
                             panel.view.viewport().cursor().shape())
            panel.view.keyReleaseEvent(QKeyEvent(
                QKeyEvent.Type.KeyRelease, canvas_module.Qt.Key.Key_Space,
                canvas_module.Qt.KeyboardModifier.NoModifier))
            self.assertEqual(canvas_module.Qt.CursorShape.ArrowCursor,
                             panel.view.viewport().cursor().shape())

            # Embedded editor actions must cross an event-loop boundary before
            # they can rebuild the scene; otherwise Qt may destroy the button
            # while its clicked signal is still being dispatched.
            queued_calls = []
            panel.show_inline_editor(panel._nodes[source_id])
            inline_panel = panel._inline_editor_proxy.widget()
            combos = inline_panel.findChildren(canvas_module.QComboBox)
            automation_combo = next(combo for combo in combos
                                    if combo.findData("checkpoints") >= 0 and
                                    combo.findData("manual") >= 0)
            self.assertEqual("checkpoints", automation_combo.currentData())
            automation_combo.setCurrentIndex(automation_combo.findData("manual"))
            stage_combo = next(combo for combo in combos if combo.findText(
                "6 · 确认定稿图片并生成视频") >= 0)
            stage_combo.setCurrentText("6 · 确认定稿图片并生成视频")
            run_button = inline_panel.findChild(canvas_module.QPushButton, "runNode")
            with patch.object(panel, "run_inline_action",
                              side_effect=lambda *args: queued_calls.append(args)):
                run_button.click()
                self.assertEqual([], queued_calls)
                self.app.processEvents()
            self.assertEqual("6 · 确认定稿图片并生成视频", queued_calls[0][2])
            self.assertIsNone(panel._inline_editor_proxy)

            portrait = folder / "portrait.png"
            portrait_image = QImage(200, 400, QImage.Format.Format_RGB32)
            portrait_image.fill(QColor("#665599")); portrait_image.save(str(portrait))
            portrait_node = canvas_module.CanvasNodeItem(
                panel, "portrait", "image_node", "竖图", thumbnail=str(portrait))
            self.assertGreater(portrait_node.height, portrait_node.width)
            self.assertAlmostEqual(0.5,
                portrait_node._thumb_pixmap.width() / portrait_node._thumb_pixmap.height(), places=1)

            panel.toggle_asset_library(False)
            self.assertTrue(panel.navigator_panel.isHidden())
            dock_asset = next(button for button in panel.create_dock.findChildren(
                              canvas_module.QPushButton) if button.text() == "▣ 资产")
            dock_asset.click()
            self.assertFalse(panel.navigator_panel.isHidden())
            dock_asset.click()
            self.assertTrue(panel.navigator_panel.isHidden())
            self.assertFalse(any(button.text() == "✦ AI 分镜"
                                 for button in panel.create_dock.findChildren(
                                     canvas_module.QPushButton)))

            shot_count = len(board["shots"])
            shot_node = panel._nodes[f"shot:{board['shots'][0]['id']}"]
            panel.scene.clearSelection(); shot_node.setSelected(True)
            with patch.object(QMessageBox, "question",
                              return_value=QMessageBox.StandardButton.Yes):
                panel.delete_canvas_selection()
            self.assertEqual(shot_count - 1, len(board["shots"]))

    def test_blocking_storyboard_is_batched_and_uses_project_model(self):
        shots = [{
            "id":f"shot-{number}", "number":number,
            "visual":f"镜头 {number}", "action_line":"固定动作",
            "scene_name":"雨夜车站", "duration":4,
        } for number in range(1, 6)]
        submitted = []

        def submit(name, request):
            submitted.append((name, request))
            payload = json.loads(request.inputs["messages"][1]["content"])
            result = {"shots":[{
                "id":row["id"], "blocking":"固定站位", "eyeline":"看向右侧",
                "camera_position":"眼平机位", "camera_movement":"固定",
                "axis_rule":"轴线北侧", "frame_start":"起始",
                "frame_end":"结束", "motion_keyframes":[],
            } for row in payload]}
            return TaskHandle(
                id=f"blocking-{len(submitted)}", provider_name=name,
                operation="chat", status=TaskStatus.DONE, progress=1,
                result=TaskResult(True, json.dumps(result, ensure_ascii=False)))

        manager = SimpleNamespace(
            registry=SimpleNamespace(by_capability=lambda _operation: [
                SimpleNamespace(name="openai"), SimpleNamespace(name="deepseek")]),
            submit=submit)
        with patch.object(canvas_module, "get_ai_manager", return_value=manager):
            panel = canvas_module.ProductionCanvasTab()
            panel._checkpoint_timer.stop(); panel._task_timer.stop()
            panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
            panel._save_layout_now = lambda *args, **kwargs: None
            panel.set_storyboard({"id":"blocking-batches", "shots":shots})
            source_id = panel.create_custom_node(
                "storyboard_node", QPointF(0, 0), {
                    "content":"雨夜等待", "planning_provider":"deepseek",
                    "planning_model":"deepseek-chat",
                })

            panel.submit_blocking_storyboard(panel._nodes[source_id])
            for _ in range(3):
                panel._poll_standalone_tasks()

            self.assertEqual([2, 2, 1], [
                len(json.loads(request.inputs["messages"][1]["content"]))
                for _provider, request in submitted])
            self.assertTrue(all(provider == "deepseek"
                                for provider, _request in submitted))
            self.assertTrue(all(
                request.params["model"] == "deepseek-chat" and
                request.params["timeout_seconds"] == 300
                for _provider, request in submitted))
            self.assertTrue(all(shot["blocking_ready"] for shot in shots))
            self.assertNotIn(
                "blocking_batch_next", panel._custom_record(source_id))

    def test_canvas_project_checkpoint_survives_restart_and_corrupt_primary(self):
        folder = Path(tempfile.mkdtemp())
        checkpoint = folder / "canvas.json"
        board = {
            "id": "recover-project", "title": "可恢复短片",
            "shots": [{"id": "shot-1", "number": 1, "duration": 5,
                       "selected_image_asset": "final.png",
                       "selected_video_asset": "final.mp4"}],
        }
        with patch.object(canvas_module, "LAYOUT_FILE", checkpoint):
            first = canvas_module.ProductionCanvasTab()
            first.set_storyboard(board)
            first.create_custom_node(
                "text_node", QPointF(120, 240), {"content": "保留这个节点"})
            first._save_layout_now()
            self.assertTrue(checkpoint.exists())

            # A second valid write creates the previous-version backup.
            board["title"] = "恢复后的短片"
            first._save_layout_now()
            checkpoint.write_text("{broken", encoding="utf-8")
            first._checkpoint_timer.stop(); first._task_timer.stop()

            restored = canvas_module.ProductionCanvasTab()
            self.assertEqual("recover-project", restored.current_storyboard()["id"])
            self.assertEqual("可恢复短片", restored.current_storyboard()["title"])
            records = restored._positions().get("__custom_nodes__", [])
            self.assertTrue(any(record.get("content") == "保留这个节点"
                                for record in records))
            restored._checkpoint_timer.stop(); restored._task_timer.stop()

    def test_manual_project_file_restores_graph_candidates_and_final_choices(self):
        folder = Path(tempfile.mkdtemp())
        checkpoint = folder / "autosave.json"
        project_file = folder / "fight_short.cepstudio"
        image_path = folder / "final.png"
        image = QImage(160, 90, QImage.Format.Format_RGB32)
        image.fill(QColor("#6b4250")); image.save(str(image_path))
        board = {
            "id":"manual-save-project", "title":"15秒打斗测试",
            "shots":[{"id":"s1", "number":1, "duration":3,
                      "selected_image_asset":str(image_path),
                      "assets":[{"path":str(image_path), "kind":"image"}]}],
        }
        with patch.object(canvas_module, "LAYOUT_FILE", checkpoint):
            first = canvas_module.ProductionCanvasTab()
            first._checkpoint_timer.stop(); first._task_timer.stop()
            first.set_storyboard(board)
            source_id = first.create_custom_node(
                "storyboard_node", QPointF(100, 200), {
                    "title":"打斗制片项目", "content":"两人在停车场交手",
                    "pipeline_stage":"image_candidates_ready"})
            candidate_id = first.create_custom_node(
                "image_node", QPointF(900, 620), {
                    "title":"镜头01候选", "path":str(image_path),
                    "shot_id":"s1", "generator_kind":"image",
                    "candidates":[str(image_path)]})
            first._positions().setdefault("__workflow_edges__", []).append(
                {"source":source_id, "target":candidate_id, "type":"image"})
            self.assertTrue(first.save_canvas_project(
                str(project_file), show_message=False))
            document = json.loads(project_file.read_text(encoding="utf-8"))
            self.assertEqual(canvas_module.PROJECT_FORMAT, document["format"])
            self.assertEqual("15秒打斗测试", document["title"])
            self.assertTrue(document["media_manifest"][0]["exists"])

            restored = canvas_module.ProductionCanvasTab()
            restored._checkpoint_timer.stop(); restored._task_timer.stop()
            self.assertTrue(restored.open_canvas_project(
                str(project_file), show_message=False))
            self.assertEqual("manual-save-project", restored.current_storyboard()["id"])
            self.assertEqual(str(image_path), restored.current_storyboard()["shots"][0][
                "selected_image_asset"])
            self.assertEqual([900.0, 620.0], restored._positions()[candidate_id])
            self.assertIn(
                {"source":source_id, "target":candidate_id, "type":"image"},
                restored._positions()["__workflow_edges__"])
            self.assertEqual(str(project_file),
                             restored._positions()["__project_file__"])

            # A second save keeps the previous valid manual project.  If the
            # main file is truncated, Open Project transparently uses it.
            self.assertTrue(restored.save_canvas_project(
                str(project_file), show_message=False))
            project_file.write_text("{broken", encoding="utf-8")
            backup_restored = canvas_module.ProductionCanvasTab()
            backup_restored._checkpoint_timer.stop(); backup_restored._task_timer.stop()
            self.assertTrue(backup_restored.open_canvas_project(
                str(project_file), show_message=False))
            self.assertEqual("15秒打斗测试", backup_restored.current_storyboard()["title"])

    def test_new_project_keeps_old_canvas_and_recent_snapshot_can_switch_back(self):
        folder = Path(tempfile.mkdtemp())
        checkpoint = folder / "multi-project.json"
        old_board = {
            "id":"old-canvas", "title":"停车场打斗短片",
            "shots":[{"id":"fight-1", "number":1, "duration":5}],
        }
        with patch.object(canvas_module, "LAYOUT_FILE", checkpoint):
            panel = canvas_module.ProductionCanvasTab()
            panel._checkpoint_timer.stop(); panel._task_timer.stop()
            panel.set_storyboard(old_board)
            old_node = panel.create_custom_node(
                "text_node", QPointF(320, 180), {"content":"旧工程脚本"})
            panel._save_layout_now()

            self.assertTrue(panel.new_canvas_project(
                confirm=False, show_message=False))
            new_id = panel.current_storyboard()["id"]
            self.assertNotEqual("old-canvas", new_id)
            self.assertIn("old-canvas", panel._layout_store)
            self.assertEqual([320.0, 180.0],
                             panel._layout_store["old-canvas"][old_node])
            new_nodes = panel._positions().get("__custom_nodes__", [])
            self.assertEqual(1, len(new_nodes))
            self.assertEqual("storyboard_node", new_nodes[0]["type"])
            self.assertFalse(panel.current_storyboard().get("shots"))
            self.assertTrue(panel.rename_canvas_project("雨夜追逐 · 第二版"))
            self.assertEqual("雨夜追逐 · 第二版",
                             panel.current_storyboard()["title"])

            recent_ids = [value["project_id"]
                          for value in panel._recent_projects()]
            self.assertIn("old-canvas", recent_ids)
            self.assertIn(new_id, recent_ids)
            self.assertTrue(panel.switch_to_internal_project(
                "old-canvas", confirm=False, show_message=False))
            self.assertEqual("停车场打斗短片",
                             panel.current_storyboard()["title"])
            self.assertIn(old_node, panel._nodes)
            self.assertEqual("旧工程脚本",
                             panel._nodes[old_node].payload["content"])

    def test_manual_control_dock_runs_the_current_stage_and_selects_next_step(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        panel.set_storyboard({"id":"manual-stage-project", "shots":[{
            "id":"manual-s1", "number":1, "duration":4,
            "visual":"两名特工在雨夜车站短暂交手", "scene_name":"雨夜车站",
        }]})
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {
                "content":"雨夜车站里，两名特工短暂交手",
                "automation_mode":"manual", "shot_count":4,
            })

        with patch.object(panel, "submit_canvas_storyboard") as submit:
            panel.continue_current_production()
        self.assertTrue(submit.call_args.args[2].startswith("1"))

        record = panel._custom_record(source_id)
        record["pipeline_stage"] = "shots_ready"
        record["status"] = "第 1 步完成"
        panel.refresh()
        panel._update_production_continue_button()
        self.assertIn("第 2 步", panel.production_continue_btn.text())
        with patch.object(panel, "submit_canvas_storyboard") as submit:
            panel.continue_current_production()
        self.assertTrue(submit.call_args.args[2].startswith("2"))

        record["pipeline_stage"] = "shots_ready"
        panel.refresh()
        panel.show_inline_editor(panel._nodes[source_id])
        inline_panel = panel._inline_editor_proxy.widget()
        stage_combo = next(combo for combo in inline_panel.findChildren(
            canvas_module.QComboBox) if combo.findText(
                "6 · 确认定稿图片并生成视频") >= 0)
        self.assertTrue(stage_combo.currentText().startswith("2"))

    def test_failed_manual_first_stage_unlocks_retry_instead_of_staying_stuck(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        panel.set_storyboard({"id":"manual-retry-project", "shots":[]})
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {
                "content":"一场雨夜追逐", "automation_mode":"manual",
                "pipeline_stage":"planning",
            })
        failed = TaskHandle(
            id="failed-plan", provider_name="openai", operation="chat",
            status=TaskStatus.FAILED, progress=1,
            result=TaskResult(False, error="模型暂时不可用"))
        panel._standalone_tasks[failed.id] = {
            "handle":failed, "node_id":source_id, "provider":"openai",
            "kind":"storyboard_plan",
        }
        with patch.object(QMessageBox, "warning",
                          return_value=QMessageBox.StandardButton.Ok):
            panel._poll_standalone_tasks()
        record = panel._custom_record(source_id)
        self.assertEqual("", record["pipeline_stage"])
        self.assertIn("点击重试", record["status"])
        panel._update_production_continue_button()
        self.assertTrue(panel.production_continue_btn.isEnabled())
        self.assertIn("第 1 步", panel.production_continue_btn.text())

    def test_storyboard_gateway_retry_resumes_saved_batch_checkpoint(self):
        foundation = {
            "title":"断点测试", "summary":"", "visual_bible":"电影写实",
            "characters":[{"name":"罗文", "description":"蓝色斗篷"}],
            "scenes":[{"name":"信标大厅", "location_id":"beacon_hall",
                       "description":"中央石台", "states":[{"name":"夜", "description":"火光"}]}],
            "elements":[],
            "shot_outline":[
                {"shot_number":1, "scene_name":"信标大厅", "scene_state":"夜",
                 "visual":"王子进入", "duration":4},
                {"shot_number":2, "scene_name":"信标大厅", "scene_state":"夜",
                 "visual":"巨龙抬头", "duration":4},
            ],
        }
        detail = {"shots":[
            {"shot_number":1, "scene_name":"信标大厅", "scene_state":"夜",
             "visual":"王子进入", "camera":"固定", "primary_action":"王子停下"},
            {"shot_number":2, "scene_name":"信标大厅", "scene_state":"夜",
             "visual":"巨龙抬头", "camera":"缓慢推近", "primary_action":"巨龙抬头"},
        ]}
        purposes = []
        batch_attempts = 0

        def submit(name, request):
            nonlocal batch_attempts
            purpose = request.metadata.get("purpose")
            purposes.append(purpose)
            if purpose == "canvas_storyboard_foundation":
                return TaskHandle(
                    id="foundation-ok", provider_name=name, operation="chat",
                    status=TaskStatus.DONE, progress=1,
                    result=TaskResult(True, json.dumps(foundation, ensure_ascii=False)))
            batch_attempts += 1
            if batch_attempts == 1:
                return TaskHandle(
                    id="batch-timeout", provider_name=name, operation="chat",
                    status=TaskStatus.FAILED, progress=1,
                    result=TaskResult(False, error="504 Gateway Timeout by CloudFront"))
            return TaskHandle(
                id=f"batch-ok-{batch_attempts}", provider_name=name, operation="chat",
                status=TaskStatus.DONE, progress=1,
                result=TaskResult(True, json.dumps(detail, ensure_ascii=False)))

        manager = SimpleNamespace(
            registry=SimpleNamespace(by_capability=lambda _operation: [
                SimpleNamespace(name="openai")]),
            submit=submit)
        with patch.object(canvas_module, "get_ai_manager", return_value=manager), \
                patch.object(QMessageBox, "warning",
                             return_value=QMessageBox.StandardButton.Ok):
            panel = canvas_module.ProductionCanvasTab()
            panel._checkpoint_timer.stop(); panel._task_timer.stop()
            panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
            panel._save_layout_now = lambda *args, **kwargs: None
            panel.set_storyboard({"id":"resume-storyboard", "shots":[]})
            source_id = panel.create_custom_node(
                "storyboard_node", QPointF(0, 0), {
                    "content":"王子进入信标大厅，巨龙抬头",
                    "shot_count":2, "style":"电影写实",
                    "planning_provider":"openai", "planning_model":"gpt-5.5",
                })
            node = panel._nodes[source_id]
            panel.submit_canvas_storyboard(node, node.payload["content"])
            panel._poll_standalone_tasks()  # foundation -> first batch
            panel._poll_standalone_tasks()  # first batch -> 504

            record = panel._custom_record(source_id)
            self.assertEqual(
                ["canvas_storyboard_foundation", "canvas_storyboard_shot_batch"],
                purposes)
            self.assertTrue(record.get("storyboard_plan_checkpoint", {}).get("foundation"))
            self.assertIn("已保存 0/2 镜", record["status"])

            panel.submit_canvas_storyboard(node, node.payload["content"])
            self.assertEqual("canvas_storyboard_shot_batch", purposes[-1])
            self.assertEqual(1, purposes.count("canvas_storyboard_foundation"))
            panel._poll_standalone_tasks()

            self.assertEqual(2, len(panel.current_storyboard()["shots"]))
            self.assertNotIn("storyboard_plan_checkpoint",
                             panel._custom_record(source_id))

    def test_restart_can_continue_a_persisted_storyboard_checkpoint(self):
        idea = "王子进入信标大厅，巨龙抬头"
        foundation = {
            "title":"重启续跑", "summary":"", "visual_bible":"电影写实",
            "characters":[{"name":"罗文", "description":"蓝色斗篷"}],
            "scenes":[{"name":"信标大厅", "location_id":"beacon_hall",
                       "description":"中央石台", "states":[]}],
            "elements":[],
            "shot_outline":[
                {"shot_number":1, "scene_name":"信标大厅", "visual":"王子进入"},
                {"shot_number":2, "scene_name":"信标大厅", "visual":"巨龙抬头"},
            ],
        }
        fingerprint = canvas_module.planning_fingerprint(
            idea, 2, "电影写实", "openai", "gpt-5.5", 0.5)
        checkpoint = canvas_module.new_planning_checkpoint(
            fingerprint=fingerprint, shot_count=2, style="电影写实",
            provider="openai", model="gpt-5.5", temperature=0.5)
        checkpoint["foundation"] = foundation
        submitted = []

        def submit(name, request):
            submitted.append(request.metadata.get("purpose"))
            return TaskHandle(
                id="resumed-batch", provider_name=name, operation="chat",
                status=TaskStatus.RUNNING, progress=0)

        manager = SimpleNamespace(
            registry=SimpleNamespace(by_capability=lambda _operation: [
                SimpleNamespace(name="openai")]),
            submit=submit)
        with patch.object(canvas_module, "get_ai_manager", return_value=manager):
            panel = canvas_module.ProductionCanvasTab()
            panel._checkpoint_timer.stop(); panel._task_timer.stop()
            panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
            panel._save_layout_now = lambda *args, **kwargs: None
            panel.set_storyboard({"id":"restart-resume", "shots":[]})
            source_id = panel.create_custom_node(
                "storyboard_node", QPointF(0, 0), {
                    "content":idea, "shot_count":2, "style":"电影写实",
                    "automation_mode":"checkpoints",
                    "planning_provider":"openai", "planning_model":"gpt-5.5",
                    "planning_temperature":0.5, "pipeline_stage":"planning",
                })
            panel._custom_record(source_id)["storyboard_plan_checkpoint"] = checkpoint
            node = panel._nodes[source_id]

            panel._update_production_continue_button()
            self.assertTrue(panel.production_continue_btn.isEnabled())
            self.assertIn("继续已保存的拆镜", panel.production_continue_btn.text())
            panel.continue_canvas_production(node)

            self.assertEqual(["canvas_storyboard_shot_batch"], submitted)
            self.assertNotIn("canvas_storyboard_foundation", submitted)
            self.assertTrue(panel._has_storyboard_planning_task(source_id))

    def test_invalid_foundation_is_repaired_once_and_pipeline_continues(self):
        idea = "王子进入大厅，巨龙抬头"
        valid_foundation = {
            "title":"自动修复", "summary":"", "visual_bible":"电影写实",
            "characters":[{"name":"罗文", "description":"蓝色斗篷"}],
            "scenes":[{"name":"信标大厅", "location_id":"beacon_hall",
                       "description":"中央石台", "states":[]}],
            "elements":[],
            "shot_outline":[
                {"shot_number":1, "scene_name":"信标大厅", "visual":"王子进入"},
                {"shot_number":2, "scene_name":"信标大厅", "visual":"巨龙抬头"},
            ],
        }
        detail = {"shots":[
            {"shot_number":1, "scene_name":"信标大厅", "visual":"王子进入",
             "primary_action":"王子停下", "camera_position":"中景"},
            {"shot_number":2, "scene_name":"信标大厅", "visual":"巨龙抬头",
             "primary_action":"巨龙抬头", "camera_position":"近景"},
        ]}
        purposes = []

        def submit(name, request):
            purpose = request.metadata.get("purpose")
            purposes.append(purpose)
            if purpose == "canvas_storyboard_foundation":
                data = '{"title":"坏合同","scenes":[],"shot_outline":[]}'
            elif purpose == "canvas_storyboard_foundation_repair":
                data = json.dumps(valid_foundation, ensure_ascii=False)
            else:
                data = json.dumps(detail, ensure_ascii=False)
            return TaskHandle(
                id=f"repair-{len(purposes)}", provider_name=name, operation="chat",
                status=TaskStatus.DONE, progress=1,
                result=TaskResult(True, data))

        manager = SimpleNamespace(
            registry=SimpleNamespace(by_capability=lambda _operation: [
                SimpleNamespace(name="openai")]),
            submit=submit)
        with patch.object(canvas_module, "get_ai_manager", return_value=manager):
            panel = canvas_module.ProductionCanvasTab()
            panel._checkpoint_timer.stop(); panel._task_timer.stop()
            panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
            panel._save_layout_now = lambda *args, **kwargs: None
            panel.set_storyboard({"id":"repair-storyboard", "shots":[]})
            source_id = panel.create_custom_node(
                "storyboard_node", QPointF(0, 0), {
                    "content":idea, "shot_count":2, "style":"电影写实",
                    "planning_provider":"openai", "planning_model":"gpt-5.5",
                })
            node = panel._nodes[source_id]
            panel.submit_canvas_storyboard(node, idea)
            panel._poll_standalone_tasks()  # invalid foundation -> one repair
            record = panel._custom_record(source_id)
            self.assertIn("正在自动修复结构", record["status"])
            self.assertEqual("镜头骨架应为 2 镜，实际返回 0 镜",
                             record["storyboard_plan_diagnostic"]["error"])
            panel._poll_standalone_tasks()  # repaired foundation -> batch
            panel._poll_standalone_tasks()  # batch -> complete plan

            self.assertEqual([
                "canvas_storyboard_foundation",
                "canvas_storyboard_foundation_repair",
                "canvas_storyboard_shot_batch",
            ], purposes)
            self.assertEqual(2, len(panel.current_storyboard()["shots"]))
            self.assertNotIn("storyboard_plan_diagnostic",
                             panel._custom_record(source_id))

    def test_failed_contract_repair_stops_without_an_infinite_retry(self):
        purposes = []

        def submit(name, request):
            purposes.append(request.metadata.get("purpose"))
            return TaskHandle(
                id=f"invalid-{len(purposes)}", provider_name=name, operation="chat",
                status=TaskStatus.DONE, progress=1,
                result=TaskResult(True, "not json"))

        manager = SimpleNamespace(
            registry=SimpleNamespace(by_capability=lambda _operation: [
                SimpleNamespace(name="openai")]),
            submit=submit)
        with patch.object(canvas_module, "get_ai_manager", return_value=manager), \
                patch.object(QMessageBox, "warning",
                             return_value=QMessageBox.StandardButton.Ok):
            panel = canvas_module.ProductionCanvasTab()
            panel._checkpoint_timer.stop(); panel._task_timer.stop()
            panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
            panel._save_layout_now = lambda *args, **kwargs: None
            panel.set_storyboard({"id":"failed-repair", "shots":[]})
            source_id = panel.create_custom_node(
                "storyboard_node", QPointF(0, 0), {
                    "content":"王子与龙", "shot_count":2,
                    "planning_provider":"openai", "planning_model":"gpt-5.5",
                })
            panel.submit_canvas_storyboard(panel._nodes[source_id], "王子与龙")
            panel._poll_standalone_tasks()
            panel._poll_standalone_tasks()

            self.assertEqual(2, len(purposes))
            self.assertEqual("", panel._custom_record(source_id)["pipeline_stage"])
            diagnostic = panel._custom_record(source_id)["storyboard_plan_diagnostic"]
            self.assertEqual(1, diagnostic["repair_attempt"])
            self.assertIn("not json", diagnostic["response_excerpt"])

    def test_seedance_real_person_block_is_actionable_and_frame_override_persists(self):
        folder = Path(tempfile.mkdtemp())
        original = folder / "realistic-face.png"
        replacement = folder / "stylized-character.png"
        for path, color in ((original, "#775566"), (replacement, "#557766")):
            image = QImage(160, 90, QImage.Format.Format_RGB32)
            image.fill(QColor(color)); image.save(str(path))
        shot = {
            "id":"privacy-shot", "number":1, "duration":5,
            "selected_image_asset":str(original), "production_ready":True,
            "visual":"角色回头", "action_line":"停下", "camera_slot":"中景",
            "camera_movement":"固定", "spatial_layout":"室内", "axis_rule":"不越轴",
            "frame_start":"角色在左", "frame_end":"角色在左",
        }
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        panel.set_storyboard({"id":"privacy-project", "shots":[shot]})
        node_id = panel.create_custom_node("video_node", QPointF(0, 0), {
            "title":"镜头 01 视频", "content":"角色回头", "generator_kind":"video",
            "provider_name":"seedance", "shot_id":"privacy-shot",
            "shot_ids":["privacy-shot"], "first_frame":str(original),
            "references":[str(original)],
        })
        error = (
            'Ark HTTP 400: {"error":{"code":"InputImageSensitiveContentDetected.'
            'PrivacyInformation","message":"The input image may contain real person."}}')
        failed = TaskHandle(
            id="privacy-failed", provider_name="seedance", operation="image_to_video",
            status=TaskStatus.FAILED, progress=1,
            result=TaskResult(False, error=error))
        panel._standalone_tasks[failed.id] = {
            "handle":failed, "node_id":node_id, "provider":"seedance",
            "request":TaskRequest(operation="image_to_video",
                                  inputs={"image":str(original), "prompt":"角色回头"}),
            "fallback_providers":[], "provider_locked":True,
        }
        with patch.object(panel, "_show_video_privacy_block") as privacy_dialog, \
                patch.object(QMessageBox, "warning") as warning:
            panel._poll_standalone_tasks()
        privacy_dialog.assert_called_once()
        warning.assert_not_called()
        record = panel._custom_record(node_id)
        self.assertEqual("real_person_privacy", record["generation_blocked"])
        self.assertEqual(str(original), record["blocked_input"])
        self.assertIn("疑似可识别真人", record["status"])

        panel.set_video_frame(panel._nodes[node_id], "first_frame", str(replacement))
        panel._refresh_video_generator_contract(record)
        self.assertTrue(record["first_frame_override"])
        self.assertEqual(str(replacement), record["first_frame"])

    def test_assigned_video_frame_click_previews_and_empty_slot_chooses_file(self):
        folder = Path(tempfile.mkdtemp())
        first_frame = folder / "first-frame.png"
        image = QImage(160, 90, QImage.Format.Format_RGB32)
        image.fill(QColor("#45566f")); image.save(str(first_frame))
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__":panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        node_id = panel.create_custom_node("video_node", QPointF(0, 0), {
            "title":"测试视频", "content":"测试", "first_frame":str(first_frame),
        })
        node = panel._nodes[node_id]

        with patch.object(panel, "open_media_preview") as preview, \
                patch.object(panel, "choose_video_frame") as choose:
            panel.open_or_choose_video_frame(node, "first_frame")
            preview.assert_called_once_with(str(first_frame), "image")
            choose.assert_not_called()

            panel.open_or_choose_video_frame(node, "last_frame")
            choose.assert_called_once_with(node, "last_frame", None)

    def test_generated_media_context_menu_can_reveal_local_file(self):
        folder = Path(tempfile.mkdtemp())
        image_path = folder / "generated-result.png"
        image = QImage(160, 90, QImage.Format.Format_RGB32)
        image.fill(QColor("#354861")); image.save(str(image_path))
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__":panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        panel.set_storyboard({"id":"reveal-local-project", "shots":[]})
        node_id = panel.create_custom_node("image_node", QPointF(0, 0), {
            "title":"生成图片", "content":"测试图片", "path":str(image_path),
            "generator_kind":"image",
        })
        captured_actions = []

        def capture_menu(menu, *_args, **_kwargs):
            captured_actions.extend(action.text() for action in menu.actions())

        with patch.object(canvas_module.QMenu, "exec", new=capture_menu):
            panel.show_node_context_menu(panel._nodes[node_id], QPoint(10, 10))
        self.assertIn("查看本地文件", captured_actions)
        self.assertEqual(str(image_path), panel._local_media_path_for_node(
            panel._nodes[node_id]))

        with patch.object(canvas_module.subprocess, "Popen") as popen:
            self.assertTrue(panel.reveal_local_media_file(str(image_path)))
        self.assertIn(str(image_path), popen.call_args.args[0])

        with patch.object(QMessageBox, "information") as information:
            self.assertFalse(panel.reveal_local_media_file(
                str(folder / "missing-video.mp4")))
        information.assert_called_once()

    def test_generated_video_node_displays_middle_frame_thumbnail(self):
        folder = Path(tempfile.mkdtemp())
        video_path = folder / "generated.mp4"
        video_path.write_bytes(b"test-video-placeholder")
        frames = []
        for index, color in enumerate(("#334455", "#d28c45", "#556677")):
            frame = folder / f"frame-{index}.jpg"
            image = QImage(320, 180, QImage.Format.Format_RGB32)
            image.fill(QColor(color)); image.save(str(frame))
            frames.append(str(frame))
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__":panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        panel.set_storyboard({"id":"video-thumbnail-project", "shots":[]})
        node_id = panel.create_custom_node("video_node", QPointF(0, 0), {
            "title":"首尾帧生成视频", "path":str(video_path),
            "first_frame":frames[0], "last_frame":frames[2],
            "video_review_frames":frames, "generator_kind":"video",
        })
        node = panel._nodes[node_id]
        self.assertEqual(frames[1], node.thumbnail)
        self.assertFalse(node._thumb_pixmap.isNull())
        self.assertEqual(frames[1], node.payload["video_review_frames"][1])

        # A legacy/in-progress video without extracted output frames still
        # shows its input first frame instead of the empty AI placeholder.
        fallback_id = panel.create_custom_node("video_node", QPointF(520, 0), {
            "title":"旧视频节点", "path":str(video_path),
            "first_frame":frames[0],
        })
        self.assertEqual(frames[0], panel._nodes[fallback_id].thumbnail)

    def test_multiple_image_nodes_can_be_saved_as_one_asset(self):
        folder = Path(tempfile.mkdtemp())
        paths = []
        for index, color in enumerate(("#845a55", "#557584", "#75608d")):
            path = folder / f"character-reference-{index}.png"
            image = QImage(180, 240, QImage.Format.Format_RGB32)
            image.fill(QColor(color)); image.save(str(path))
            paths.append(str(path))
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__":panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        panel.set_storyboard({"id":"grouped-asset-project", "shots":[]})
        node_ids = [panel.create_custom_node("image_node", QPointF(index * 420, 0), {
            "title":f"角色参考 {index + 1}", "path":path,
            "asset_kind":"character",
        }) for index, path in enumerate(paths)]
        nodes = [panel._nodes[node_id] for node_id in node_ids]
        self.assertEqual(paths, panel._selected_asset_image_paths(nodes))
        for node in nodes:
            node.setSelected(True)

        menu_actions = []

        def capture_menu(menu, *_args, **_kwargs):
            menu_actions.extend(action.text() for action in menu.actions())

        with patch.object(canvas_module.QMenu, "exec", new=capture_menu):
            panel.show_node_context_menu(nodes[0], QPoint(20, 20))
        self.assertIn("合并保存为一个资产…（3 张）", menu_actions)

        saved_item = SimpleNamespace(name="岚 · 完整角色资产")
        with patch(
                "ai.ui.resource_center.import_assets_to_resource_center",
                return_value=("character", [saved_item])) as importer, \
                patch.object(QMessageBox, "information"):
            self.assertTrue(panel.save_selected_images_as_asset(nodes))
        importer.assert_called_once_with(
            panel, paths, default_kind="character", force_same=True)

    def test_motion_storyboard_contract_scales_frames_with_shot_duration(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        short = {
            "duration":2.5, "visual":"女孩起跑", "frame_start":"弓步蓄力",
            "frame_end":"冲出画面右侧", "action_line":"向右冲刺",
            "camera_position":"侧面中景", "camera_movement":"向右跟拍",
        }
        self.assertEqual(3, len(panel._normalize_motion_keyframes(short)))
        self.assertEqual([0.0, 1.25, 2.5],
                         [value["time_seconds"] for value in short["motion_keyframes"]])

        long_shot = dict(short, duration=8)
        long_shot["motion_keyframes"] = [
            {"time_seconds":f"{index * 2}秒", "composition":f"构图{index}",
             "is_hero":"true" if index == 2 else "false"}
            for index in range(5)]
        frames = panel._normalize_motion_keyframes(long_shot)
        self.assertEqual(5, len(frames))
        self.assertEqual(1, sum(value["is_hero"] for value in frames))
        self.assertEqual("构图2", frames[2]["composition"])
        prompt = panel._motion_storyboard_prompt(long_shot, 0, "雨夜写实")
        self.assertIn("3 列 × 2 行", prompt)
        self.assertIn("只画 5 个有效画框", prompt)
        self.assertIn("跨格动作", prompt)
        self.assertIn("动作可见度硬门槛", prompt)

    def test_motion_storyboard_endpoints_and_object_transfer_are_authoritative(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        shot = {
            "duration":4,
            "frame_start":"王子肩上只有一条蓝色披带，桌面没有披带",
            "frame_end":"王子肩上没有披带；同一条披带折好放在桌面",
            "action_start":"与桌面无关的旧错误起点",
            "primary_action":"王子解下蓝色披带并放到桌面",
            "action_end":"与肩带状态冲突的旧错误终点",
            "continuity_invariants":["王子身份不变", "石桌位置不变"],
            "motion_keyframes":[
                {"composition":"错误的旧起始构图", "character_state":"肩上没有披带"},
                {"composition":"动作中段"},
                {"composition":"错误的旧结束构图", "character_state":"肩上仍有披带"},
            ],
        }

        frames = panel._normalize_motion_keyframes(shot)
        self.assertEqual(shot["frame_start"], frames[0]["composition"])
        self.assertEqual(shot["frame_start"], frames[0]["character_state"])
        self.assertEqual(shot["frame_end"], frames[-1]["composition"])
        self.assertEqual(shot["frame_end"], frames[-1]["character_state"])
        prompt = panel._motion_storyboard_prompt(shot, 4, "欧美奇幻写实")
        self.assertIn("对象守恒", prompt)
        self.assertIn("原位置随后必须为空", prompt)
        self.assertIn("未参与动作的服装", prompt)
        self.assertIn("不得复制桌", prompt)
        self.assertIn(shot["frame_start"], prompt)
        self.assertIn(shot["frame_end"], prompt)
        panel.close()

    def test_storyboard_generation_never_uses_previous_multi_panel_as_pixel_reference(self):
        folder = Path(tempfile.mkdtemp())
        scene = folder / "scene-authority.png"
        character = folder / "character-authority.png"
        previous_board = folder / "previous-motion-board.png"
        for path, color in ((scene, "#224466"), (character, "#886644"),
                            (previous_board, "#444444")):
            image = QImage(160, 90, QImage.Format.Format_RGB32)
            image.fill(QColor(color)); self.assertTrue(image.save(str(path)))
        submitted = []

        def submit(name, request):
            submitted.append((name, request))
            return TaskHandle(
                id="authority-only-storyboard", provider_name=name,
                operation=request.operation, status=TaskStatus.RUNNING)

        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._save_layout_now = lambda *args, **kwargs: None
        shot = {
            "id":"authority-only-shot", "number":2, "duration":4,
            "scene_view_path":str(scene),
            "frame_start":"人物站在唯一石桌右侧",
            "frame_end":"人物仍在唯一石桌右侧",
        }
        panel.set_storyboard({"id":"authority-only-board", "shots":[shot]})
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {
                "content":"权威参考测试", "production_ratio":"9:16"})
        panel._canvas_storyboard_source = source_id
        panel._canvas_storyboard_queue = [0]
        panel._canvas_storyboard_previous = str(previous_board)
        panel._canvas_storyboard_character_refs = [str(character)]

        manager = SimpleNamespace(submit=submit)
        with patch.object(canvas_module, "get_ai_manager", return_value=manager), \
                patch.object(panel, "_locked_storyboard_image_provider",
                             return_value=SimpleNamespace(name="seedream")):
            panel._submit_next_canvas_storyboard_image()

        request = submitted[0][1]
        self.assertTrue(os.path.exists(request.inputs["image"]))
        self.assertEqual(request.inputs["image"], shot["scene_stage_capture"])
        self.assertIn(str(scene), request.inputs["images"])
        self.assertIn(str(character), request.inputs["images"])
        self.assertNotIn(str(previous_board), request.inputs["images"])
        self.assertLessEqual(len(request.inputs["reference_assets"]), 3)
        self.assertEqual(["composition", "scene", "character"], [
            value["role"] for value in request.inputs["reference_assets"]])
        self.assertEqual("1152x2048", request.params["size"])
        self.assertIn("原生 9:16 竖向画面", request.inputs["prompt"])
        stage_capture = QImage(shot["scene_stage_capture"])
        self.assertEqual((720, 1280), (stage_capture.width(), stage_capture.height()))
        panel.close()

    def test_changing_project_ratio_invalidates_old_motion_panels(self):
        folder = Path(tempfile.mkdtemp())
        panel_path = folder / "old-horizontal-panel.png"
        image = QImage(1024, 576, QImage.Format.Format_RGB32)
        image.fill(QColor("#314761")); self.assertTrue(image.save(str(panel_path)))
        shot = {
            "id":"ratio-shot", "number":1, "duration":3,
            "motion_keyframes":[{"index":index + 1, "time_seconds":index}
                                for index in range(3)],
            "motion_panel_paths":[str(panel_path)] * 3,
            "motion_board_path":str(panel_path), "draft_source":"ai",
            "motion_board_contract_version":canvas_module.MOTION_STORYBOARD_CONTRACT_VERSION,
            "motion_board_aspect_ratio":"16:9", "production_ready":True,
            "final_image_prompt":"old", "final_video_prompt":"old",
        }
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._save_layout_now = lambda *args, **kwargs: None
        board = {"id":"ratio-board", "shots":[shot]}
        panel.set_storyboard(board)
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {
                "content":"画幅切换", "production_ratio":"16:9",
                "pipeline_stage":"prompts_ready"})
        panel.update_custom_setting(
            panel._nodes[source_id], "production_ratio", "9:16")
        source = panel._custom_record(source_id)
        self.assertEqual("9:16", board["production_ratio"])
        self.assertEqual("assets_ready", source["pipeline_stage"])
        self.assertEqual("stale_aspect_ratio", shot["motion_board_review_status"])
        self.assertFalse(shot["production_ready"])
        self.assertNotIn("final_image_prompt", shot)
        self.assertNotIn("final_video_prompt", shot)
        panel.close()

    def test_same_view_inherits_only_previous_endpoint_crop(self):
        folder = Path(tempfile.mkdtemp())
        scene = folder / "scene.png"
        board = folder / "previous-board.png"
        image = QImage(600, 400, QImage.Format.Format_RGB32)
        image.fill(QColor("#335577")); self.assertTrue(image.save(str(scene)))
        image.fill(QColor("#775533")); self.assertTrue(image.save(str(board)))
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        previous = {
            "id":"previous", "scene_view_id":"master",
            "motion_board_path":str(board),
            "motion_keyframes":[{"index":1}, {"index":2}, {"index":3}],
        }
        current = {
            "id":"current", "scene_view_id":"master", "duration":3,
            "scene_view_path":str(scene), "frame_start":"承接上一镜", "frame_end":"站定",
        }
        panel.set_storyboard({"id":"endpoint-board", "shots":[previous, current]})
        refs = panel._storyboard_authority_references(current, 1)
        endpoint = current.get("continuity_endpoint_reference")
        self.assertTrue(endpoint and os.path.exists(endpoint))
        self.assertIn(endpoint, refs)
        self.assertNotIn(str(board), refs)
        panel.close()

    def test_walking_motion_contract_rejects_turtle_displacement(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        shot = {
            "duration":4, "primary_action":"人物缓慢走进大厅",
            "character_positions":[{
                "start":"x=0.07/y=0.24", "end":"x=0.17/y=0.33",
                "movement":"向厅内走",
            }],
        }
        contract = panel._motion_visibility_contract(shot)
        self.assertIn("低于可见门槛", contract)
        self.assertIn("每0.55秒一个完整步幅", contract)
        self.assertIn("龟速", contract)
        panel.close()

    def test_motion_board_qc_rejects_four_identical_panels(self):
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            self.skipTest("Pillow unavailable")
        folder = Path(tempfile.mkdtemp())
        path = folder / "identical-board.png"
        image = Image.new("RGB", (1200, 800), "white")
        draw = ImageDraw.Draw(image)
        for row in range(2):
            for column in range(2):
                left, top = column * 600, row * 400
                draw.rectangle((left + 40, top + 50, left + 560, top + 360),
                               fill="#27364c")
                draw.ellipse((left + 250, top + 130, left + 350, top + 300),
                             fill="#d7b27b")
        image.save(path)
        qc = canvas_module.ProductionCanvasTab._inspect_motion_board(str(path), 4)
        self.assertEqual("fail", qc["status"])
        self.assertIn("MOTION_PANELS_NEAR_DUPLICATE", qc["issues"])

    def test_legacy_single_panel_is_not_accepted_as_motion_storyboard(self):
        folder = Path(tempfile.mkdtemp())
        old_panel = folder / "legacy_single_panel.png"
        image = QImage(160, 90, QImage.Format.Format_RGB32)
        image.fill(QColor("#555555")); image.save(str(old_panel))
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        shot = {
            "id":"legacy-shot", "number":1, "duration":5,
            "blocking_ready":True, "draft_panel":str(old_panel),
            "frame_start":"人物在左", "frame_end":"人物在右",
        }
        panel.set_storyboard({"id":"legacy-motion-project", "shots":[shot]})
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {
                "content":"旧工程", "pipeline_stage":"storyboard_panels_ready",
            })
        with patch.object(QMessageBox, "information",
                          return_value=QMessageBox.StandardButton.Ok):
            panel.compile_canvas_storyboard_prompts(panel._nodes[source_id])
        self.assertFalse(shot.get("production_ready"))
        record = panel._custom_record(source_id)
        self.assertEqual("assets_ready", record["pipeline_stage"])
        self.assertIn("重新执行第 3 步", record["status"])

    def test_motion_board_and_legacy_derived_images_cannot_be_video_anchors(self):
        folder = Path(tempfile.mkdtemp())
        board_path = folder / "motion_board.png"
        old_output = folder / "old_arrow_output.png"
        clean_output = folder / "clean_output.png"
        for path, color in ((board_path, "#777777"),
                            (old_output, "#884444"),
                            (clean_output, "#448844")):
            image = QImage(160, 90, QImage.Format.Format_RGB32)
            image.fill(QColor(color)); image.save(str(path))
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        shot = {
            "id":"arrow-shot", "number":1, "duration":5,
            "motion_board_path":str(board_path), "draft_panel":str(board_path),
            "motion_keyframes":[{"index":index + 1} for index in range(4)],
            "selected_image_asset":str(old_output),
            "assets":[{"path":str(board_path), "kind":"image",
                       "subtype":"motion_storyboard", "frame_count":4}],
        }
        panel.set_storyboard({"id":"arrow-guard-project", "shots":[shot]})
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {"content":"箭头隔离测试"})
        generator_id = panel.create_custom_node(
            "image_node", QPointF(500, 0), {
                "path":str(old_output), "references":[str(board_path)],
                "candidates":[str(old_output)], "shot_id":"arrow-shot",
                "generator_kind":"image",
            })
        panel.refresh()

        board_node = next(node for node in panel._nodes.values()
                          if node.node_type == "shot_take" and
                          node.payload.get("path") == str(board_path))
        with patch.object(QMessageBox, "information",
                          return_value=QMessageBox.StandardButton.Ok):
            self.assertFalse(panel.adopt_shot_take(board_node))
            self.assertTrue(panel._show_video_anchor_issues([shot]))

        generator = panel._custom_record(generator_id)
        self.assertTrue(panel._sanitize_motion_board_pixel_references(generator))
        self.assertNotIn(str(board_path), generator["references"])
        self.assertIn(str(old_output), generator["legacy_motion_board_outputs"])
        self.assertTrue(panel._path_has_motion_board_lineage(shot, str(old_output)))
        generator["path"] = str(clean_output)
        generator["candidates"].append(str(clean_output))
        shot["selected_image_asset"] = str(clean_output)
        self.assertFalse(panel._path_has_motion_board_lineage(shot, str(clean_output)))
        self.assertEqual(([], []), panel._video_anchor_issues([shot]))

    def test_video_segments_lock_provider_strip_overlay_prompts_and_detach_individually(self):
        folder = Path(tempfile.mkdtemp())
        clean_a = folder / "clean-a.png"; clean_b = folder / "clean-b.png"
        video_a = folder / "segment-a.mp4"; video_b = folder / "segment-b.mp4"
        for path, color in ((clean_a, "#335577"), (clean_b, "#775533")):
            image = QImage(160, 90, QImage.Format.Format_RGB32)
            image.fill(QColor(color)); image.save(str(path))
        video_a.write_bytes(b"video-a"); video_b.write_bytes(b"video-b")
        shots = [{
            "id":"segment-a", "number":1, "duration":5, "production_ready":True,
            "selected_image_asset":str(clean_a), "selected_video_asset":str(video_a),
            "selected_asset":str(video_a), "preview_asset":str(video_a),
            "visual":"角色向右闪避。画面标注红色手绘攻击箭头。",
            "action_line":"角色向右闪避", "camera_slot":"南侧中景",
            "camera_movement":"向右跟拍", "spatial_layout":"柱子在左。可叠加CAM摄影机箭头。",
            "axis_rule":"机位不越轴", "frame_start":"角色在左", "frame_end":"角色在右",
            "motion_keyframes":[],
            "video_segment_node_id":"generator-a",
            "assets":[{"path":str(clean_a), "kind":"image"},
                      {"path":str(video_a), "kind":"video",
                       "generator_node_id":"generator-a"}],
        }, {
            "id":"segment-b", "number":2, "duration":5, "production_ready":True,
            "selected_image_asset":str(clean_b), "selected_video_asset":str(video_b),
            "selected_asset":str(video_b), "preview_asset":str(video_b),
            "visual":"角色停下", "action_line":"停下", "camera_slot":"固定中景",
            "camera_movement":"固定", "spatial_layout":"车辆在右", "axis_rule":"不越轴",
            "frame_start":"站立", "frame_end":"站立", "motion_keyframes":[],
            "video_segment_node_id":"generator-b",
            "assets":[{"path":str(clean_b), "kind":"image"},
                      {"path":str(video_b), "kind":"video",
                       "generator_node_id":"generator-b"}],
        }]
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        panel.set_storyboard({
            "id":"single-segment-retry",
            "visual_bible":{"ai_storyboard":"电影写实。所有镜头可叠加导演手绘调度标注。"},
            "shots":shots,
        })
        generator_a = panel.create_custom_node("video_node", QPointF(400, 0), {
            "title":"连续段 01", "content":"旧污染提示词", "path":str(video_a),
            "candidates":[str(video_a)], "generator_kind":"video",
            "provider_name":"seedance", "shot_id":"segment-a", "shot_ids":["segment-a"],
            "references":[str(clean_a)],
        })
        generator_b = panel.create_custom_node("video_node", QPointF(800, 0), {
            "title":"连续段 02", "content":"干净提示词", "path":str(video_b),
            "candidates":[str(video_b)], "generator_kind":"video",
            "provider_name":"seedance", "shot_id":"segment-b", "shot_ids":["segment-b"],
            "references":[str(clean_b)],
        })
        image_generator_a = panel.create_custom_node("image_node", QPointF(200, 0), {
            "title":"镜头 01 · 图片生成器", "content":"干净定稿图片",
            "path":str(clean_a), "candidates":[str(clean_a)],
            "generator_kind":"image", "provider_name":"gptimage",
            "shot_id":"segment-a", "shot_ids":["segment-a"],
        })
        # Match the ids stored in the legacy shot records for this fixture.
        for shot, generator_id in zip(shots, (generator_a, generator_b)):
            shot["video_segment_node_id"] = generator_id
            shot["assets"][-1]["generator_node_id"] = generator_id
        shots[0]["assets"][0]["generator_node_id"] = image_generator_a
        panel.refresh()

        record_a = panel._custom_record(generator_a)
        panel._refresh_video_generator_contract(record_a)
        self.assertEqual(str(clean_a), record_a["first_frame"])
        self.assertIn("纯净电影成片", record_a["content"])
        self.assertNotIn("手绘攻击箭头", record_a["content"])
        self.assertNotIn("可叠加", record_a["content"])
        image_take = next(node for node in panel._nodes.values()
                          if node.node_type == "shot_take" and
                          node.payload.get("path") == str(clean_a))
        self.assertEqual(image_generator_a,
                         panel._result_generator(image_take, "image").node_id)
        image_branch = panel._custom_branch_ids(image_generator_a)
        self.assertIn(generator_a, image_branch)
        self.assertNotIn(generator_b, image_branch)

        submitted = []
        providers = [SimpleNamespace(name="seedance"), SimpleNamespace(name="veo")]
        manager = SimpleNamespace(
            registry=SimpleNamespace(by_capability=lambda _operation: providers),
            submit=lambda name, request: (
                submitted.append((name, request)) or TaskHandle(
                    id="locked-provider-task", provider_name=name,
                    operation=request.operation, status=TaskStatus.QUEUED)))
        with patch.object(canvas_module, "get_ai_manager", return_value=manager):
            panel.submit_standalone_generation(
                panel._nodes[generator_a], record_a["content"], "图生视频")
        self.assertEqual("seedance", submitted[-1][0])
        task = panel._standalone_tasks["locked-provider-task"]
        self.assertEqual([], task["fallback_providers"])
        self.assertTrue(task["provider_locked"])
        self.assertEqual(str(clean_a), submitted[-1][1].inputs["image"])

        removed = panel._detach_generator_outputs(generator_a, clear_record=True)
        self.assertEqual(1, removed)
        self.assertFalse(shots[0].get("selected_video_asset"))
        self.assertEqual(str(clean_a), shots[0]["selected_asset"])
        self.assertEqual(str(video_b), shots[1]["selected_video_asset"])
        self.assertEqual(str(video_b), panel._custom_record(generator_b)["path"])
        removed_images = panel._detach_generator_outputs(
            image_generator_a, clear_record=True)
        self.assertEqual(1, removed_images)
        self.assertFalse(shots[0].get("selected_image_asset"))
        self.assertEqual(str(clean_b), shots[1]["selected_image_asset"])

    def test_every_production_stage_can_rewind_without_deleting_upstream_work(self):
        folder = Path(tempfile.mkdtemp())
        motion = folder / "motion.png"
        final_image = folder / "final.png"
        for path, color in ((motion, "#666666"), (final_image, "#448866")):
            image = QImage(160, 90, QImage.Format.Format_RGB32)
            image.fill(QColor(color)); image.save(str(path))
        video = folder / "final.mp4"; video.write_bytes(b"video")
        audio = folder / "dialogue.wav"; audio.write_bytes(b"audio")

        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        shot = {
            "id":"rewind-shot", "number":1, "duration":5,
            "blocking_ready":True, "blocking":"从左向右",
            "motion_board_path":str(motion), "draft_panel":str(motion),
            "motion_keyframes":[{"index":index + 1} for index in range(4)],
            "final_image_prompt":"干净定稿提示词", "final_video_prompt":"视频提示词",
            "production_ready":True,
            "selected_image_asset":str(final_image), "anchor_frame_id":str(final_image),
            "selected_video_asset":str(video), "dialogue_audio":str(audio),
            "selected_asset":str(video), "preview_asset":str(video), "asset_type":"video",
            "assets":[
                {"path":str(motion), "kind":"image", "subtype":"motion_storyboard"},
                {"path":str(final_image), "kind":"image"},
                {"path":str(video), "kind":"video"},
                {"path":str(audio), "kind":"audio"},
            ],
        }
        panel.set_storyboard({
            "id":"all-stage-rewind", "title":"阶段回退测试",
            "summary":"完整镜头", "visual_bible":{"ai_storyboard":"统一设定"},
            "shots":[shot],
        })
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {
                "content":"保留这句创意", "pipeline_stage":"production_ready",
            })
        asset_id = panel.create_custom_node(
            "image_node", QPointF(400, 0), {
                "path":str(final_image), "asset_kind":"character",
                "asset_version":2, "locked":True, "adopted":True,
            })
        asset = panel._custom_record(asset_id)
        asset.update({"asset_role":"character_reference", "asset_name":"阿青",
                      "character_reference_set":{"portrait":str(final_image)}})
        view_id = panel.create_custom_node(
            "image_node", QPointF(700, 0), {"path":str(final_image)})
        panel._custom_record(view_id)["reference_parent_id"] = asset_id
        panel._positions().setdefault("__workflow_edges__", []).extend([
            {"source":source_id, "target":asset_id, "type":"character"},
            {"source":asset_id, "target":view_id, "type":"character_reference"},
            {"source":source_id, "target":"shot:rewind-shot", "type":"storyboard"},
        ])

        group_ids = {}
        child_ids = {}
        for index, kind in enumerate(("image", "video", "audio")):
            child_id = panel.create_custom_node(
                f"{kind}_node", QPointF(500 + index * 300, 600), {
                    "path":{"image":str(final_image), "video":str(video),
                            "audio":str(audio)}[kind],
                    "generator_kind":kind, "shot_id":"rewind-shot",
                })
            group_id = panel.create_custom_node(
                "workflow_group", QPointF(300 + index * 300, 500), {
                    "source_node_id":source_id, "generator_kind":kind,
                    "group_nodes":[child_id],
                })
            child_ids[kind] = child_id; group_ids[kind] = group_id
            panel._positions().setdefault("__production_batches__", []).append({
                "id":f"batch-{kind}", "group_id":group_id,
                "source_node_id":source_id, "kind":kind, "status":"complete",
            })
        panel.refresh()

        def current_shot():
            return panel.current_storyboard()["shots"][0]

        self.assertTrue(panel.rewind_production_to_step(
            7, source_id, confirm=False, show_message=False))
        self.assertIsNotNone(panel._custom_record(group_ids["video"]))
        self.assertIsNone(panel._custom_record(group_ids["audio"]))
        self.assertEqual(str(video), current_shot()["selected_video_asset"])
        self.assertNotIn("dialogue_audio", current_shot())
        self.assertTrue(panel.undo_last_production_rewind(show_message=False))

        self.assertTrue(panel.rewind_production_to_step(
            6, source_id, confirm=False, show_message=False))
        self.assertIsNotNone(panel._custom_record(group_ids["image"]))
        self.assertIsNone(panel._custom_record(group_ids["video"]))
        self.assertEqual(str(final_image), current_shot()["selected_image_asset"])
        self.assertNotIn("selected_video_asset", current_shot())
        self.assertTrue(panel.undo_last_production_rewind(show_message=False))

        self.assertTrue(panel.rewind_production_to_step(
            5, source_id, confirm=False, show_message=False))
        self.assertTrue(all(panel._custom_record(value) is None
                            for value in group_ids.values()))
        self.assertEqual(str(motion), current_shot()["motion_board_path"])
        self.assertNotIn("selected_image_asset", current_shot())
        self.assertEqual("干净定稿提示词", current_shot()["final_image_prompt"])
        self.assertEqual("prompts_ready",
                         panel._custom_record(source_id)["pipeline_stage"])
        self.assertTrue(panel.undo_last_production_rewind(show_message=False))

        self.assertTrue(panel.rewind_production_to_step(
            4, source_id, confirm=False, show_message=False))
        self.assertEqual(str(motion), current_shot()["motion_board_path"])
        self.assertNotIn("final_image_prompt", current_shot())
        self.assertEqual("storyboard_panels_ready",
                         panel._custom_record(source_id)["pipeline_stage"])
        self.assertTrue(panel.undo_last_production_rewind(show_message=False))

        self.assertTrue(panel.rewind_production_to_step(
            3, source_id, confirm=False, show_message=False))
        self.assertTrue(panel._custom_record(asset_id)["locked"])
        self.assertNotIn("motion_board_path", current_shot())
        self.assertEqual("assets_ready",
                         panel._custom_record(source_id)["pipeline_stage"])
        self.assertTrue(panel.undo_last_production_rewind(show_message=False))

        self.assertTrue(panel.rewind_production_to_step(
            2, source_id, confirm=False, show_message=False))
        self.assertEqual(1, len(panel.current_storyboard()["shots"]))
        self.assertEqual("", panel._custom_record(asset_id)["path"])
        self.assertFalse(panel._custom_record(asset_id)["locked"])
        self.assertIsNone(panel._custom_record(view_id))
        self.assertEqual("shots_ready",
                         panel._custom_record(source_id)["pipeline_stage"])
        self.assertTrue(panel.undo_last_production_rewind(show_message=False))

        self.assertTrue(panel.rewind_production_to_step(
            1, source_id, confirm=False, show_message=False))
        self.assertEqual([], panel.current_storyboard()["shots"])
        self.assertIsNone(panel._custom_record(asset_id))
        self.assertEqual("保留这句创意", panel._custom_record(source_id)["content"])
        self.assertEqual("", panel._custom_record(source_id)["pipeline_stage"])

    def test_canvas_readiness_skill_writes_a_visible_blocking_report(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        panel.set_storyboard({"id":"readiness-skill-project", "shots":[{
            "id":"ready-s1", "number":1, "duration":4,
            "visual":"女孩进入仓库", "scene_name":"仓库",
        }]})
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {
                "content":"女孩进入仓库", "pipeline_stage":"assets_ready",
                "automation_mode":"checkpoints",
            })
        skill_id = panel.create_canvas_skill("shot_readiness", QPointF(500, 0))
        panel.refresh()
        panel.execute_canvas_skill(panel._nodes[skill_id], execute=False)
        skill = panel._custom_record(skill_id)
        source = panel._custom_record(source_id)
        self.assertEqual("就绪检查未通过", skill["status"])
        self.assertIn("尚未建立角色、场景或关键道具资产", skill["content"])
        self.assertFalse(source["readiness_report"]["ready"])

    def test_visual_repair_routes_video_issue_without_touching_image_prompt(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        shot = {
            "id":"repair-s1", "number":1, "duration":5, "visual":"人物奔跑",
            "final_image_prompt":"保留的定稿图片提示词",
            "final_video_prompt":"向右跟拍",
            "selected_video_asset":"existing.mp4",
        }
        panel.set_storyboard({"id":"repair-skill-project", "shots":[shot]})
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {
                "content":"人物奔跑", "pipeline_stage":"production_ready"})
        skill_id = panel.create_canvas_skill("vision_qc_repair", QPointF(500, 0))
        plan = panel._apply_vision_repair_plan(skill_id, {
            "summary":"第二镜运动闪烁", "score":72,
            "shots":[{
                "id":"repair-s1", "score":72, "passed":False,
                "issues":["运动中出现闪烁"], "issue_codes":["TEMPORAL_FLICKER"],
                "repair_target":"video", "revision":"保持身份并消除帧间闪烁",
            }],
        })
        self.assertEqual("video", plan["items"][0]["target"])
        self.assertEqual("保留的定稿图片提示词", shot["final_image_prompt"])
        self.assertIn("消除帧间闪烁", shot["final_video_prompt"])
        self.assertEqual("selected", panel._custom_record(source_id)["production_scope"])
        self.assertEqual("待确认 1 个局部修复项",
                         panel._custom_record(skill_id)["status"])

    def test_automatic_post_and_sequence_qc_pause_only_failed_branch(self):
        folder = Path(tempfile.mkdtemp())
        first_video = folder / "first.mp4"
        second_video = folder / "second.mp4"
        first_video.write_bytes(b"video-a")
        second_video.write_bytes(b"video-b")
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        shots = [{
            "id":"qc-s1", "number":1, "duration":5, "visual":"人物向右跑",
            "selected_video_asset":str(first_video),
            "video_segment_node_id":"qc-video-1",
        }, {
            "id":"qc-s2", "number":2, "duration":5, "visual":"人物继续跑",
            "selected_video_asset":str(second_video),
            "video_segment_node_id":"qc-video-2",
        }]
        panel.set_storyboard({"id":"qc-project", "shots":shots})
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {
                "content":"连续奔跑", "pipeline_stage":"video_qc_pending",
                "automation_mode":"checkpoints", "auto_run_enabled":True,
            })
        for node_id, path, shot_id in (
                ("qc-video-1", first_video, "qc-s1"),
                ("qc-video-2", second_video, "qc-s2")):
            panel._positions().setdefault("__custom_nodes__", []).append({
                "id":node_id, "type":"video_node", "generator_kind":"video",
                "path":str(path), "shot_ids":[shot_id], "status":"生成完成",
                "clip_qc":{"kind":"clip_qc", "status":"complete", "score":92,
                           "passed":True, "shots":[{
                               "id":shot_id, "score":92, "passed":True,
                               "issues":[], "issue_codes":[],
                           }]},
            })
        panel._finalize_video_qc(source_id, canvas_module.normalize_sequence_qc({
            "summary":"第二段方向反了", "score":70, "passed":False,
            "transitions":[{
                "from_id":"qc-s1", "to_id":"qc-s2", "score":70,
                "passed":False, "issues":["屏幕运动方向反转"],
                "issue_codes":["SCREEN_DIRECTION_FLIP"],
                "repair_target":"blocking", "revision":"保持左进右出",
            }],
        }))
        source = panel._custom_record(source_id)
        self.assertEqual("video_qc_review", source["pipeline_stage"])
        self.assertFalse(source["auto_run_enabled"])
        self.assertIsNotNone(panel._custom_record(f"auto-qc:{source_id}"))
        self.assertTrue(shots[1]["production_selected"])
        self.assertEqual("blocking", shots[1]["repair_target"])
        self.assertNotIn("repair_target", shots[0])

    def test_sequence_qc_is_advisory_after_all_clips_are_human_approved(self):
        folder = Path(tempfile.mkdtemp())
        video = folder / "approved.mp4"
        video.write_bytes(b"video")
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        shot = {"id":"approved-s1", "number":1, "duration":5,
                "selected_video_asset":str(video),
                "video_segment_node_id":"approved-video",
                "production_ready":True}
        panel.set_storyboard({"id":"approved-project", "shots":[shot]})
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {
                "pipeline_stage":"video_qc_pending", "auto_run_enabled":True,
            })
        panel._positions().setdefault("__custom_nodes__", []).append({
            "id":"approved-video", "type":"video_node", "generator_kind":"video",
            "path":str(video), "shot_ids":["approved-s1"], "adopted":True,
            "handoff_approved":True,
            "clip_qc":{"kind":"clip_qc", "status":"complete", "score":45,
                       "passed":False, "severity":"block", "shots":[{
                           "id":"approved-s1", "score":45, "passed":False,
                           "severity":"block", "issues":["动作连续性存疑"],
                           "issue_codes":["ACTION_DRIFT"],
                       }]},
        })
        with patch.object(panel, "_schedule_auto_continue") as resume:
            panel._finalize_video_qc(source_id, canvas_module.normalize_sequence_qc({
                "summary":"连续性存疑", "score":50, "passed":False,
                "transitions":[],
            }))
        source = panel._custom_record(source_id)
        self.assertEqual("video_ready", source["pipeline_stage"])
        self.assertTrue(source["auto_run_enabled"])
        self.assertIn("仅供参考", source["status"])
        self.assertTrue(shot["production_ready"])
        resume.assert_called_once()
        panel.close()

    def test_accepting_qc_risk_preserves_report_and_resumes(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        panel.set_storyboard({"id":"qc-risk-project", "shots":[]})
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {
                "pipeline_stage":"video_qc_review", "auto_run_enabled":False,
            })
        panel._custom_record(source_id)["repair_plan"] = {"items":[{"shot_id":"s1"}]}
        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.StandardButton.Yes), \
                patch.object(panel, "_schedule_auto_continue") as resume:
            self.assertTrue(panel.accept_video_qc_risk(source_id))
        source = panel._custom_record(source_id)
        self.assertEqual("video_ready", source["pipeline_stage"])
        self.assertTrue(source["quality_risk_accepted"])
        self.assertEqual(1, len(source["repair_plan"]["items"]))
        resume.assert_called_once()

    def test_accepting_one_clip_review_is_scoped_and_resumes_serial_group(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        panel.set_storyboard({"id":"clip-review-project", "shots":[]})
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {
                "pipeline_stage":"video_qc_review", "auto_run_enabled":False,
            })
        panel._positions().setdefault("__custom_nodes__", []).extend([{
            "id":"review-group", "type":"workflow_group",
            "generator_kind":"video", "source_node_id":source_id,
            "group_nodes":["review-video"],
            "awaiting_video_node_id":"review-video",
        }, {
            "id":"review-video", "type":"video_node", "generator_kind":"video",
            "workflow_group_id":"review-group", "adopted":True,
            "clip_qc":{"kind":"clip_qc", "status":"complete", "score":75,
                       "passed":False, "severity":"review"},
        }])
        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.StandardButton.Yes), \
                patch.object(panel, "_submit_next_serial_video") as resume:
            self.assertTrue(panel.accept_video_qc_risk(source_id))
        source = panel._custom_record(source_id)
        generator = panel._custom_record("review-video")
        self.assertNotIn("quality_risk_accepted", source)
        self.assertEqual("video_qc_pending", source["pipeline_stage"])
        self.assertTrue(generator["handoff_approved"])
        self.assertTrue(generator["clip_qc"]["risk_accepted"])
        resume.assert_called_once_with("review-group")

    def test_human_approved_clip_keeps_qc_as_advice_and_never_hard_blocks(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        shot = {"id":"stop-s1", "number":1, "duration":5,
                "visual":"骑士跃上石台", "production_ready":True}
        panel.set_storyboard({"id":"stop-project", "shots":[shot]})
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {
                "pipeline_stage":"video_qc_pending", "auto_run_enabled":True,
            })
        panel._positions().setdefault("__custom_nodes__", []).extend([{
            "id":"stop-group", "type":"workflow_group",
            "generator_kind":"video", "source_node_id":source_id,
            "group_nodes":["stop-video"],
        }, {
            "id":"stop-video", "type":"video_node", "generator_kind":"video",
            "workflow_group_id":"stop-group", "shot_ids":["stop-s1"],
            "adopted":True,
        }])
        review = {"score":52, "passed":False, "shots":[{
            "id":"stop-s1", "score":52, "passed":False,
            "blockers":["F3"], "issues":["肢体结构断裂"],
            "issue_codes":["ANATOMY_BREAK"], "repair_target":"video",
            "revision":"简化动作并缩短时长",
        }]}
        with patch.object(panel, "_upsert_auto_qc_node",
                          return_value="auto-qc-stop"):
            for _ in range(3):
                panel._apply_clip_qc_result(
                    "stop-video", source_id, review, ["stop-s1"])
        source = panel._custom_record(source_id)
        generator = panel._custom_record("stop-video")
        self.assertEqual(0, int(generator.get("qc_failure_count") or 0))
        self.assertFalse(generator.get("retry_stop", False))
        self.assertTrue(generator["handoff_approved"])
        self.assertEqual("video_qc_pending", source["pipeline_stage"])
        self.assertNotIn("approval_required", source)
        self.assertFalse(source["automatic_qc"]["retry_stop"])
        self.assertTrue(source["automatic_qc"]["human_approved"])
        self.assertTrue(shot["production_ready"])
        self.assertNotIn("repair_target", shot)

    def test_deleted_qc_report_can_be_restored_without_clearing_blocker(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._layout_store = {"__schema__": panel._layout_store.get("__schema__")}
        panel._save_layout_now = lambda *args, **kwargs: None
        panel.set_storyboard({"id":"qc-hidden-project", "shots":[]})
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {
                "pipeline_stage":"video_qc_review", "auto_run_enabled":False,
                "automation_mode":"checkpoints", "status":"审片未通过",
            })
        qc_id = panel._upsert_auto_qc_node(
            source_id, "镜头 02 · 运动方向反转", "存在阻断问题",
            {"score":68}, {"items":[{"shot_id":"s2"}]})
        panel.refresh()
        panel.scene.clearSelection(); panel._nodes[qc_id].setSelected(True)
        with patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes):
            panel.delete_canvas_selection()

        source = panel._custom_record(source_id)
        self.assertNotIn(qc_id, panel._nodes)
        self.assertEqual("video_qc_review", source["pipeline_stage"])
        self.assertTrue(source["automatic_qc_hidden"])
        self.assertIn("恢复审片报告", panel.production_continue_btn.text())

        self.assertTrue(panel._restore_auto_qc_node(source_id))
        self.assertIn(qc_id, panel._nodes)
        self.assertFalse(source["automatic_qc_hidden"])
        self.assertEqual("video_qc_review", source["pipeline_stage"])
        panel.show_inline_editor(panel._nodes[qc_id])
        button_texts = [button.text() for button in
                        panel._inline_editor_proxy.widget().findChildren(
                            canvas_module.QPushButton)]
        self.assertIn("接受本轮审片风险并继续…", button_texts)
        panel.close()

    def test_combined_preview_deduplicates_multi_shot_video_segment(self):
        folder = Path(tempfile.mkdtemp())
        video = folder / "continuous.mp4"
        video.write_bytes(b"video")
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel.set_storyboard({"id":"preview-project", "shots":[
            {"id":"p1", "duration":4, "selected_video_asset":str(video),
             "video_segment_node_id":"segment-a", "video_segment_offset":0},
            {"id":"p2", "duration":5, "selected_video_asset":str(video),
             "video_segment_node_id":"segment-a", "video_segment_offset":4},
        ]})
        panel._positions().setdefault("__custom_nodes__", []).append({
            "id":"segment-a", "type":"video_node", "generator_kind":"video",
            "path":str(video), "timeline_duration":9,
        })
        clips = panel._combined_preview_inputs()
        self.assertEqual(1, len(clips))
        self.assertEqual(2, len(clips[0]["shots"]))
        self.assertEqual(9, clips[0]["duration"])

    def test_combined_preview_ffmpeg_mix_is_playable_and_non_destructive(self):
        from utils.ffmpeg_utils import get_ffmpeg_path
        ffmpeg = get_ffmpeg_path()
        if not os.path.exists(ffmpeg):
            self.skipTest("bundled FFmpeg unavailable")
        folder = Path(tempfile.mkdtemp())
        video_a = folder / "a.mp4"
        video_b = folder / "b.mp4"
        voice = folder / "voice.wav"
        for path, color in ((video_a, "red"), (video_b, "blue")):
            subprocess.run([
                ffmpeg, "-y", "-f", "lavfi", "-i",
                f"color=c={color}:s=320x180:d=0.5:r=30",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
            ], check=True, capture_output=True, timeout=20)
        subprocess.run([
            ffmpeg, "-y", "-f", "lavfi", "-i",
            "sine=frequency=660:duration=0.25", str(voice),
        ], check=True, capture_output=True, timeout=20)
        before = {path:path.read_bytes() for path in (video_a, video_b, voice)}

        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        panel._save_layout_now = lambda *args, **kwargs: None
        panel.set_storyboard({"id":"mix-project", "shots":[
            {"id":"m1", "duration":0.5, "selected_video_asset":str(video_a),
             "video_segment_node_id":"mix-a", "video_segment_offset":0,
             "dialogue_audio":str(voice)},
            {"id":"m2", "duration":0.5, "selected_video_asset":str(video_b),
             "video_segment_node_id":"mix-b", "video_segment_offset":0},
        ]})
        panel._positions().setdefault("__custom_nodes__", []).extend([
            {"id":"mix-a", "type":"video_node", "generator_kind":"video",
             "path":str(video_a), "timeline_duration":0.5},
            {"id":"mix-b", "type":"video_node", "generator_kind":"video",
             "path":str(video_b), "timeline_duration":0.5},
        ])
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(0, 0), {"production_ratio":"16:9"})
        with patch.object(canvas_module, "LAYOUT_FILE", folder / "layout.json"), \
                patch.object(panel, "open_media_preview") as preview:
            panel.preview_current_production(source_id)
            self.assertIsNotNone(panel._preview_render_process)
            panel._preview_render_process.wait(timeout=30)
            panel._poll_combined_preview_render()
            preview.assert_called_once()
            output = Path(preview.call_args.args[0])
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 1024)
        self.assertEqual(before, {path:path.read_bytes()
                                  for path in (video_a, video_b, voice)})


if __name__ == "__main__":
    unittest.main()
