import os
import tempfile
import unittest
from pathlib import Path


_TEST_ROOT = Path(tempfile.mkdtemp(prefix="cep_multi_director_tests_"))
os.environ.setdefault("CEP_DATA_DIR", str(_TEST_ROOT / "data"))
os.environ.setdefault("CEP_PRODUCTION_LAYOUT_FILE", str(_TEST_ROOT / "layout.json"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QPointF
    from PyQt6.QtWidgets import QApplication
    from ai.canvas_registry import creation_payload
    import ai.ui.production_canvas as canvas_module
except ImportError:
    canvas_module = None


@unittest.skipIf(canvas_module is None, "PyQt6 unavailable")
class MultiDirectorStoryboardFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_registry_keeps_direct_video_default_and_adds_storyboard_contract(self):
        _node_type, payload = creation_payload("multi_director")
        self.assertEqual("direct_video", payload["director_mode"])
        self.assertEqual([], payload["asset_bindings"])
        self.assertEqual("checkpoints", payload["automation_mode"])
        self.assertEqual("16:9", payload["production_ratio"])

    def test_locked_inventory_groups_same_named_character_views(self):
        payload = {
            "references": ["a.png", "b.png", "room.png"],
            "asset_bindings": [
                {"path":"a.png", "asset_kind":"character",
                 "asset_name":"林默", "asset_role":"turnaround"},
                {"path":"b.png", "asset_kind":"character",
                 "asset_name":"林默", "asset_role":"face_closeup"},
                {"path":"room.png", "asset_kind":"scene",
                 "asset_name":"雨夜便利店", "asset_role":"master"},
            ],
        }
        text = canvas_module._director_locked_inventory_text(payload)
        self.assertIn("人物｜林默｜视图：turnaround、face_closeup｜2 张", text)
        self.assertIn("场景｜雨夜便利店｜视图：master｜1 张", text)
        self.assertIn("不得把资产图当成手绘分镜", text)

    def test_storyboard_mode_opens_the_full_production_controls(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        source_id = panel.create_custom_node("video_node", QPointF(40, 50), {
            "title":"多图导演", "content":"一场雨夜重逢",
            "multi_image_director":True, "director_mode":"storyboard_first",
            "references":[], "asset_bindings":[], "automation_mode":"checkpoints",
        })
        panel.show_inline_editor(panel._nodes[source_id])
        self.app.processEvents()
        combos = panel._inline_editor_proxy.widget().findChildren(canvas_module.QComboBox)
        self.assertTrue(any(combo.findData("direct_video") >= 0 and
                            combo.findData("storyboard_first") >= 0 for combo in combos))
        self.assertTrue(any(combo.findData(0) >= 0 and combo.findData(24) >= 0
                            for combo in combos))
        self.assertIn(panel._custom_record(source_id), panel._production_source_records())
        panel.close()

    def test_uploaded_assets_are_adopted_and_missing_assets_stay_generatable(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop(); panel._task_timer.stop()
        first = _TEST_ROOT / "linmo-turnaround.png"
        second = _TEST_ROOT / "linmo-face.png"
        first.touch(exist_ok=True); second.touch(exist_ok=True)
        source_id = panel.create_custom_node("video_node", QPointF(40, 50), {
            "title":"多图导演", "content":"林默在便利店发现一封信",
            "multi_image_director":True, "director_mode":"storyboard_first",
            "references":[str(first), str(second)],
            "asset_bindings":[
                {"path":str(first), "asset_kind":"character", "asset_name":"林默",
                 "asset_role":"turnaround", "source_node_id":"upload:1"},
                {"path":str(second), "asset_kind":"character", "asset_name":"林默",
                 "asset_role":"face_closeup", "source_node_id":"upload:2"},
            ],
        })
        character_id = panel.create_custom_node("image_node", QPointF(400, 50), {
            "title":"林默 · 角色立绘", "asset_kind":"character",
            "asset_name":"林默", "path":"", "locked":False,
        })
        scene_id = panel.create_custom_node("image_node", QPointF(400, 400), {
            "title":"雨夜便利店", "asset_kind":"scene",
            "asset_name":"雨夜便利店", "path":"", "locked":False,
        })
        panel._positions().setdefault("__workflow_edges__", []).extend([
            {"source":source_id, "target":character_id, "type":"character"},
            {"source":source_id, "target":scene_id, "type":"scene"},
        ])
        self.assertEqual(1, panel._adopt_director_uploaded_assets(source_id))
        character = panel._custom_record(character_id)
        scene = panel._custom_record(scene_id)
        self.assertTrue(character["locked"])
        self.assertTrue(character["uploaded_asset_group"])
        self.assertEqual([str(first), str(second)], character["uploaded_asset_paths"])
        self.assertFalse(bool(scene.get("locked")))
        self.assertFalse(bool(scene.get("path")))
        panel.close()


if __name__ == "__main__":
    unittest.main()
