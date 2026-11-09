import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CEP_DATA_DIR", tempfile.mkdtemp(prefix="cep_generation_safety_"))

from PyQt6.QtCore import QPointF, QEvent
from PyQt6.QtWidgets import QApplication, QPushButton
import ai.ui.production_canvas as canvas


class CanvasGenerationSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.root = Path(tempfile.mkdtemp(prefix="cep_generation_safety_"))

    def setUp(self):
        canvas.LAYOUT_FILE = self.root / (self._testMethodName + ".json")
        self.panel = canvas.ProductionCanvasTab()
        self.panel._checkpoint_timer.stop()
        self.panel._task_timer.stop()
        self.panel._layout_timer.stop()
        self.panel._layout_timer.timeout.disconnect()
        self.panel._save_layout_now = lambda *args, **kwargs: None

    def tearDown(self):
        self.panel._image_batch_queues.clear()
        self.panel._standalone_tasks.clear()
        self.panel.close()
        self.app.processEvents()
        self.panel.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def make_node(self, **settings):
        node_id = self.panel.create_custom_node("image_node", QPointF(0, 0), {
            "multi_image_composer": True, **settings,
        })
        return node_id, self.panel._nodes[node_id]

    def test_stop_cancels_pending_batch_and_invalidates_its_delayed_retry(self):
        node_id, node = self.make_node(batch_mode=True, batch_strategy="style")
        handle = SimpleNamespace(is_finished=False)
        handle.cancel = Mock(side_effect=lambda: setattr(handle, "is_finished", True))
        self.panel._standalone_tasks["old-request"] = {
            "node_id": node_id, "kind": "image_batch", "handle": handle,
        }
        self.panel._image_batch_queues[node_id] = {"pending": [{"old": True}], "active_task_id": "old-request"}
        self.panel._active_image_batch_node_id = node_id
        with patch.object(canvas.QTimer, "singleShot") as timer:
            self.panel._schedule_next_image_batch(node_id, 37000)
            old_retry = timer.call_args.args[1]
            self.panel.run_inline_action(node, "", "停止生成")
        handle.cancel.assert_called_once()
        self.assertNotIn("old-request", self.panel._standalone_tasks)
        self.assertNotIn(node_id, self.panel._image_batch_queues)
        self.assertEqual("", self.panel._active_image_batch_node_id)
        self.assertIn("已停止生成", self.panel._custom_record(node_id)["status"])
        new_queue = {"pending": [{"new": True}], "active_task_id": ""}
        self.panel._image_batch_queues[node_id] = new_queue
        with patch.object(self.panel, "_submit_next_image_batch") as submit:
            old_retry()
        submit.assert_not_called()
        self.assertEqual([{"new": True}], new_queue["pending"])
        self.panel._image_batch_queues.clear()

    def test_batch_waiting_for_retry_still_exposes_stop_button(self):
        node_id, node = self.make_node(batch_mode=True, batch_strategy="style")
        self.panel._image_batch_queues[node_id] = {"pending": [{"retry": True}], "active_task_id": ""}
        self.panel.hide_inline_editor()
        self.panel.show_inline_editor(node)
        run = self.panel._inline_editor_proxy.widget().findChild(QPushButton, "runNode")
        self.assertEqual("停止", run.text())
        self.panel._image_batch_queues.clear()

    def test_deleting_generator_cancels_its_pending_batch(self):
        node_id, node = self.make_node(batch_mode=True, batch_strategy="style")
        handle = SimpleNamespace(is_finished=False, cancel=Mock())
        self.panel._standalone_tasks["deleted-batch"] = {"node_id": node_id, "kind": "image_batch", "handle": handle}
        self.panel._image_batch_queues[node_id] = {"pending": [{"old": True}], "active_task_id": "deleted-batch"}
        self.panel._active_image_batch_node_id = node_id
        self.panel.scene.clearSelection()
        node.setSelected(True)
        with patch.object(self.panel, "_confirm_canvas_node_deletion", return_value=True), \
                patch.object(canvas.QTimer, "singleShot"):
            self.panel.delete_canvas_selection()
        handle.cancel.assert_called_once()
        self.assertNotIn(node_id, self.panel._image_batch_queues)
        self.assertNotIn("deleted-batch", self.panel._standalone_tasks)
        self.assertIsNone(self.panel._custom_record(node_id))

    def test_missing_master_file_never_substitutes_another_image(self):
        other = self.root / "remaining-reference.png"
        other.touch()
        missing = self.root / "master-was-moved.png"
        _node_id, node = self.make_node(
            batch_mode=True, batch_strategy="paired", batch_subject_source_id="master",
            reference_assets=[{"path": str(missing), "source_node_id": "master"},
                              {"path": str(other), "source_node_id": "other"}],
        )
        with patch.object(canvas.QMessageBox, "warning") as warning, \
                patch.object(canvas, "get_ai_manager") as manager:
            self.assertFalse(self.panel.submit_multi_image_batch(node, "搭配场景", "批量搭配生成"))
        manager.assert_not_called()
        self.assertIn(str(missing), warning.call_args.args[2])

    def test_stale_master_selection_requires_reselection(self):
        paths = [self.root / f"stale-master-other-{i}.png" for i in range(2)]
        for path in paths:
            path.touch()
        _node_id, node = self.make_node(batch_mode=True, batch_strategy="paired", batch_subject_source_id="deleted-master",
            reference_assets=[{"path": str(path), "source_node_id": f"other-{i}"} for i, path in enumerate(paths)])
        with patch.object(canvas.QMessageBox, "warning") as warning, \
                patch.object(canvas, "get_ai_manager") as manager:
            self.assertFalse(self.panel.submit_multi_image_batch(node, "搭配场景", "批量搭配生成"))
        manager.assert_not_called()
        self.assertIn("重新选择主图", warning.call_args.args[1])

    def test_style_only_generation_does_not_require_duplicate_prompt(self):
        _node_id, node = self.make_node(style_preset="knitted-wool", provider_name="gpt-image", content="")
        requests = []
        manager = SimpleNamespace(
            registry=SimpleNamespace(by_capability=lambda _op: [SimpleNamespace(name="gpt-image")]),
            submit=lambda _provider, request: (requests.append(request) or SimpleNamespace(id="style-only", is_finished=False)),
        )
        with patch.object(canvas, "get_ai_manager", return_value=manager), \
                patch.object(canvas.QMessageBox, "information") as info:
            self.panel.submit_standalone_generation(node, "", "文生图")
        info.assert_not_called()
        self.assertEqual(1, len(requests))
        self.assertIn("knitted wool", requests[0].inputs["prompt"])
        self.panel._standalone_tasks.clear()

    def test_removing_reference_clears_all_mentions_before_reopening(self):
        image = self.root / "removed-reference.png"
        image.touch()
        source = self.panel.create_custom_node("image_node", QPointF(-500, 0), {"path": str(image)})
        node_id, node = self.make_node(content="保留描述", batch_mode=True, batch_strategy="paired")
        self.panel.show_inline_editor(node)
        self.panel._attach_image_nodes_as_references(node_id, [source])
        self.panel._inline_text_editor.insertPlainText(" 和【图片1】保持一致")
        self.panel.remove_image_reference(node_id, str(image))
        record = self.panel._custom_record(node_id)
        self.assertEqual([], record["references"])
        self.assertEqual([], record["reference_mentions"])
        self.assertNotIn("【图片1】", record["content"])
        self.assertIn("保留描述", record["content"])
        self.assertNotIn("【图片1】", self.panel._inline_text_editor.toPlainText())
        self.assertEqual("", record.get("batch_subject_source_id", ""))
        self.assertTrue(image.exists())

    def test_batch_master_and_canvas_tokens_survive_provider_reference_sorting(self):
        from ai.reference_assets import normalize_reference_assets, prompt_manifest
        paths = [self.root / f"provider-order-{i}.png" for i in range(3)]
        for path in paths:
            path.touch()
        node_id, node = self.make_node(batch_mode=True, batch_strategy="paired", batch_subject_source_id="s2",
            references=[str(path) for path in paths],
            reference_assets=[{"path": str(path), "source_node_id": f"s{i}", "order": i,
                               "role": "style" if i == 0 else "reference", "priority": 40}
                              for i, path in enumerate(paths)])
        requests = []
        manager = SimpleNamespace(registry=SimpleNamespace(by_capability=lambda _op: [SimpleNamespace(name="gpt-image")]),
            submit=lambda _provider, request: (requests.append(request) or SimpleNamespace(id="batch-order", is_finished=False)))
        with patch.object(canvas, "get_ai_manager", return_value=manager):
            self.assertTrue(self.panel.submit_multi_image_batch(node, "主图搭配其他图片", "批量搭配生成"))
        normalized = normalize_reference_assets(requests[0].inputs["reference_assets"])
        self.assertEqual([str(paths[2]), str(paths[0])], [row["path"] for row in normalized])
        self.assertIn("【图片3】", normalized[0]["label"])
        self.assertIn("【图片1】", normalized[1]["label"])
        self.assertIn("固定主图", prompt_manifest(normalized))
        second = self.panel._image_batch_queues[node_id]["pending"][0]["request"]
        self.assertIn("【图片2】", second.inputs["reference_assets"][1]["label"])
        self.assertIn("【图片3】", second.inputs["prompt"])

    def test_image_manifest_keeps_nonconsecutive_canvas_reference_numbers(self):
        paths = [self.root / f"stable-number-{i}.png" for i in range(3)]
        for path in paths:
            path.touch()
        node_id, node = self.make_node(references=[str(path) for path in paths],
            reference_assets=[{"path": str(path), "source_node_id": f"s{i}", "order": 3 - i}
                              for i, path in enumerate(paths)])
        record = self.panel._custom_record(node_id)
        self.panel._ensure_reference_mentions(record)
        record["references"].remove(str(paths[1]))
        record["reference_assets"] = [row for row in record["reference_assets"] if row["path"] != str(paths[1])]
        node.payload.update(record)
        refs = self.panel._reference_assets_for_node(node)
        self.assertEqual([str(paths[0]), str(paths[2])], [row["path"] for row in refs])
        self.assertIn("【图片1】", refs[0]["label"])
        self.assertIn("【图片3】", refs[1]["label"])

    def test_video_analysis_connection_is_visible_immediately_and_after_refresh(self):
        source = self.root / "analysis-source.mp4"
        source.touch()
        source_id = self.panel.create_custom_node("video_node", QPointF(0, 0), {"path": str(source)})
        target_id = self.panel.create_custom_node("video_analysis_node", QPointF(600, 0), {})
        self.assertTrue(self.panel.connect_workflow_nodes(self.panel._nodes[source_id], self.panel._nodes[target_id]))
        self.assertTrue(any(edge.source.node_id == source_id and edge.target.node_id == target_id for edge in self.panel.scene.edges))
        self.panel._finish_video_breakdown(target_id, {"duration": 1, "shots": []}, str(source), self.panel._project_key())
        self.assertTrue(any(edge.source.node_id == source_id and edge.target.node_id == target_id for edge in self.panel.scene.edges))
        replacement = self.root / "analysis-replacement.mp4"
        replacement.touch()
        replacement_id = self.panel.create_custom_node("video_node", QPointF(0, 300), {"path": str(replacement)})
        self.panel.connect_workflow_nodes(self.panel._nodes[replacement_id], self.panel._nodes[target_id])
        edges = [edge for edge in self.panel.scene.edges if edge.target.node_id == target_id]
        self.assertEqual([replacement_id], [edge.source.node_id for edge in edges])
        saved = [edge for edge in self.panel._positions()["__workflow_edges__"] if edge["target"] == target_id]
        self.assertEqual([replacement_id], [edge["source"] for edge in saved])
        self.panel._finish_video_breakdown(target_id, {"shots": [], "duration": 99}, str(source), self.panel._project_key())
        self.assertEqual({}, self.panel._custom_record(target_id)["analysis_result"])
