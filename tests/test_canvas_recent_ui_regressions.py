import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CEP_DATA_DIR", tempfile.mkdtemp(prefix="cep_recent_ui_"))

try:
    from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtWidgets import QApplication
    import ai.ui.production_canvas as canvas_module
except ImportError:
    canvas_module = None


@unittest.skipIf(canvas_module is None, "PyQt6 unavailable")
class CanvasRecentUIRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.layout_root = Path(tempfile.mkdtemp(prefix="cep_recent_canvas_"))
        canvas_module.LAYOUT_FILE = cls.layout_root / "layout.json"

    def make_panel(self):
        panel = canvas_module.ProductionCanvasTab()
        panel._checkpoint_timer.stop()
        panel._task_timer.stop()
        panel._save_layout_now = lambda *args, **kwargs: None
        return panel

    def test_canceling_new_menu_never_creates_an_image_node(self):
        panel = self.make_panel()
        before = len(panel._positions().get("__custom_nodes__", []))
        with patch.object(canvas_module.QMenu, "exec", return_value=None):
            panel.show_new_asset_menu(QPoint(10, 10))
        after = len(panel._positions().get("__custom_nodes__", []))
        self.assertEqual(before, after)
        panel.close()

    def test_beginner_menu_creates_first_last_frame_video_contract(self):
        panel = self.make_panel()

        def choose_first_last(menu, _position):
            for action in menu.actions():
                child = action.menu()
                if child is None:
                    continue
                for child_action in child.actions():
                    if child_action.text() == "首尾帧生成视频":
                        return child_action
            return None

        with patch.object(canvas_module.QMenu, "exec", new=choose_first_last):
            panel.show_new_asset_menu(QPoint(10, 10), QPointF(320, 240))

        record = next(
            value for value in panel._positions().get("__custom_nodes__", [])
            if value.get("beginner_mode") == "first_last_frame_video")
        self.assertEqual("video_node", record["type"])
        self.assertEqual("图生视频", record["editor_action"])
        self.assertTrue(record["generate_audio"])
        self.assertEqual("720p", record["resolution"])
        panel.close()

    def test_text_controls_consume_delete_keys_at_document_boundary(self):
        editor = canvas_module._NodeTextEdit()
        editor.setPlainText("")
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Backspace,
                          Qt.KeyboardModifier.NoModifier)
        editor.keyPressEvent(event)
        self.assertTrue(event.isAccepted())
        editor.close()

    def test_canvas_does_not_delete_selected_node_while_line_edit_has_focus(self):
        panel = self.make_panel()
        node_id = panel.create_custom_node(
            "text_node", QPointF(100, 100), {"content":"保留节点"})
        panel._nodes[node_id].setSelected(True)
        field = canvas_module.QLineEdit(panel)
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete,
                          Qt.KeyboardModifier.NoModifier)
        with patch.object(canvas_module.QApplication, "focusWidget", return_value=field), \
                patch.object(panel, "delete_canvas_selection") as delete_selection:
            panel.view.keyPressEvent(event)
            delete_selection.assert_not_called()
        self.assertIn(node_id, panel._nodes)
        field.close(); panel.close()

    def test_copywriting_node_opens_its_compact_workbench(self):
        panel = self.make_panel()
        node_id = panel.create_custom_node("text_node", QPointF(100, 100), {
            "title":"信息流口播文案", "copywriting_workbench":True,
            "product_name":"咖啡豆", "product_description":"低温烘焙，适合上班族",
            "copy_style":"专业权威", "copy_duration":"20", "content":"测试口播",
        })
        panel.show_inline_editor(panel._nodes[node_id])
        self.app.processEvents()
        widget = panel._inline_editor_proxy.widget()
        line_edits = widget.findChildren(canvas_module.QLineEdit)
        buttons = [button.text() for button in widget.findChildren(canvas_module.QPushButton)]
        self.assertTrue(any(field.text() == "咖啡豆" for field in line_edits))
        self.assertIn("翻译", buttons)
        self.assertIn("复制文案", buttons)
        self.assertIn("恢复原文", buttons)
        panel.close()

    def test_copywriting_workbench_accepts_web_original_text_field(self):
        panel = self.make_panel()
        node_id = panel.create_custom_node("text_node", QPointF(100, 100), {
            "copywriting_workbench":True, "content":"Translated copy",
            "original_text":"中文原文",
        })
        panel.show_inline_editor(panel._nodes[node_id])
        self.app.processEvents()
        restore = next(
            button for button in panel._inline_editor_proxy.widget().findChildren(
                canvas_module.QPushButton)
            if button.text() == "恢复原文")
        self.assertTrue(restore.isEnabled())
        panel.queue_inline_action(
            node_id, "Translated copy", "恢复口播原文")
        self.app.processEvents()
        self.assertEqual("中文原文", panel._custom_record(node_id)["content"])
        panel.close()

    def test_dock_exposes_import_without_renaming_asset_contract(self):
        panel = self.make_panel()
        labels = [button.text() for button in
                  panel.create_dock.findChildren(canvas_module.QPushButton)]
        self.assertIn("⇧ 导入", labels)
        self.assertIn("▣ 资产", labels)
        panel.close()

    def test_top_selection_toolbar_exposes_web_style_delete_action(self):
        panel = self.make_panel()
        node_id = panel.create_custom_node(
            "text_node", QPointF(100, 100), {"content":"待删除节点"})
        panel.scene.clearSelection()
        panel._nodes[node_id].setSelected(True)
        self.app.processEvents()
        self.assertFalse(panel.selection_toolbar.isHidden())
        self.assertEqual("已选择 1 个节点", panel.selection_count_label.text())
        self.assertEqual("删除", panel.selection_delete_button.text())
        self.assertFalse(panel.selection_delete_button.icon().isNull())
        panel.close()

    def test_local_images_and_videos_become_nodes_at_drop_position(self):
        panel = self.make_panel()
        media_root = Path(tempfile.mkdtemp(prefix="cep_drop_media_"))
        image = media_root / "参考图.png"
        video = media_root / "镜头.mp4"
        unsupported = media_root / "说明.txt"
        for path in (image, video, unsupported):
            path.touch()

        with patch.object(panel, "_extract_video_review_frames", return_value=[]):
            node_ids = panel.import_media_paths(
                [str(image), str(video), str(unsupported)], QPointF(120, 240))

        self.assertEqual(len(node_ids), 2)
        image_node, video_node = (panel._nodes[node_id] for node_id in node_ids)
        self.assertEqual(image_node.node_type, "image_node")
        self.assertEqual(video_node.node_type, "video_node")
        self.assertEqual(image_node.payload["path"], str(image.resolve()))
        self.assertEqual(video_node.payload["path"], str(video.resolve()))
        self.assertEqual(image_node.pos(), QPointF(120, 240))
        self.assertEqual(video_node.pos(), QPointF(420, 276))
        panel.close()

    def test_inline_media_editors_have_no_independent_reference_picker(self):
        panel = self.make_panel()
        for node_type in ("image_node", "video_node"):
            payload = {"image_workbench":True} if node_type == "image_node" else {}
            node_id = panel.create_custom_node(node_type, QPointF(100, 100), payload)
            panel.show_inline_editor(panel._nodes[node_id])
            self.app.processEvents()
            buttons = [button.text() for button in
                       panel._inline_editor_proxy.widget().findChildren(
                           canvas_module.QPushButton)]
            self.assertFalse(any(text.startswith("＋参考") for text in buttons))
            self.assertFalse(any(text.startswith("＋资产参考") for text in buttons))
            panel.hide_inline_editor()
        panel.close()

    def test_text_connection_inherits_content_even_when_target_editor_is_open(self):
        panel = self.make_panel()
        source_id = panel.create_custom_node(
            "text_node", QPointF(100, 100), {"content":"一只小猫"})
        target_id = panel.create_custom_node("image_node", QPointF(500, 100), {
            "multi_image_composer":True, "content":"",
            "references":[], "reference_assets":[],
        })
        panel.show_inline_editor(panel._nodes[target_id])

        self.assertTrue(panel.connect_workflow_nodes(
            panel._nodes[source_id], panel._nodes[target_id]))
        self.assertEqual("一只小猫", panel._custom_record(target_id)["content"])
        panel.close()

    def test_voice_clone_editor_hides_normal_tts_and_visual_controls(self):
        panel = self.make_panel()
        node_id = panel.create_custom_node("audio_node", QPointF(100, 100), {
            "voice_clone":True, "content":"请朗读", "reference_assets":[],
            "reference_transcript":"", "voice_consent":False,
        })
        panel.show_inline_editor(panel._nodes[node_id])
        self.app.processEvents()
        widget = panel._inline_editor_proxy.widget()
        combo_items = {
            combo.itemText(index)
            for combo in widget.findChildren(canvas_module.QComboBox)
            if not combo.isHidden()
            for index in range(combo.count())
        }
        visible_buttons = [
            button.text() for button in widget.findChildren(canvas_module.QPushButton)
            if not button.isHidden()
        ]
        self.assertFalse({"16:9", "9:16", "1:1", "4:5"} & combo_items)
        self.assertFalse(any(text.startswith("🎵") for text in visible_buttons))
        self.assertNotIn("＋ 停顿 / 语气", visible_buttons)
        panel.close()

    def test_normal_audio_keeps_compact_emotion_selector(self):
        panel = self.make_panel()
        node_id = panel.create_custom_node("audio_node", QPointF(100, 100), {
            "content":"请朗读", "emotion":"温暖",
        })
        panel.show_inline_editor(panel._nodes[node_id])
        self.app.processEvents()
        emotion_combo = next(
            combo for combo in panel._inline_editor_proxy.widget().findChildren(
                canvas_module.QComboBox)
            if combo.toolTip() == "说话情绪")
        self.assertEqual("温暖", emotion_combo.currentText())
        emotion_combo.setCurrentText("开心")
        self.app.processEvents()
        self.assertEqual("开心", panel._custom_record(node_id)["emotion"])
        panel.close()

    def test_seedance_model_selector_switches_to_25_endpoint(self):
        panel = self.make_panel()
        node_id = panel.create_custom_node("video_node", QPointF(100, 100), {
            "provider_name":"seedance", "model":"doubao-seedance-2-0-260128",
        })
        panel.show_inline_editor(panel._nodes[node_id])
        widget = panel._inline_editor_proxy.widget()
        model_combo = next(
            combo for combo in widget.findChildren(canvas_module.QComboBox)
            if combo.findData("doubao-seedance-2-5-260628") >= 0)
        self.assertFalse(model_combo.isEditable())
        model_combo.setCurrentIndex(
            model_combo.findData("doubao-seedance-2-5-260628"))
        self.app.processEvents()
        self.assertEqual(
            "doubao-seedance-2-5-260628",
            panel._custom_record(node_id)["model"])
        panel.close()

    def test_project_video_generator_can_override_lock_with_seedance_25(self):
        panel = self.make_panel()
        source_id = panel.create_custom_node(
            "storyboard_node", QPointF(100, 100), {
                "video_provider":"seedance",
                "video_model":"doubao-seedance-2-0-260128",
            })
        node_id = panel.create_custom_node("video_node", QPointF(500, 100), {
            "provider_name":"seedance", "model":"doubao-seedance-2-0-260128",
            "editor_action":"文生视频",
        })
        panel.hide_inline_editor()
        with patch.object(panel, "_production_source_for_generator",
                          return_value=source_id):
            panel.show_inline_editor(panel._nodes[node_id])
        widget = panel._inline_editor_proxy.widget()
        model_combo = next(
            combo for combo in widget.findChildren(canvas_module.QComboBox)
            if combo.findData("doubao-seedance-2-5-260628") >= 0)
        self.assertTrue(model_combo.isEnabled())
        model_combo.setCurrentIndex(
            model_combo.findData("doubao-seedance-2-0-260128"))
        self.app.processEvents()
        model_combo.setCurrentIndex(
            model_combo.findData("doubao-seedance-2-5-260628"))
        self.app.processEvents()
        self.assertEqual(
            "doubao-seedance-2-5-260628",
            panel._custom_record(source_id)["video_model"])
        self.assertEqual(
            "doubao-seedance-2-5-260628",
            panel._custom_record(node_id)["model"])
        panel.close()

    def test_web_style_swatch_renders_as_native_icon(self):
        icon = canvas_module._style_swatch_icon(
            "linear-gradient(135deg,#16100f,#ff4d12 55%,#ffc247)")
        self.assertFalse(icon.isNull())
        self.assertFalse(icon.pixmap(38, 38).isNull())

    def test_style_explorer_attaches_web_swatches_to_every_preset(self):
        panel = self.make_panel()
        node_id = panel.create_custom_node("image_node", QPointF(100, 100), {
            "multi_image_composer":True,
        })
        captured = {}

        def inspect_dialog(dialog):
            preset_list = max(
                dialog.findChildren(canvas_module.QListWidget),
                key=lambda widget: widget.count())
            captured["count"] = preset_list.count()
            captured["icons"] = [
                not preset_list.item(index).icon().isNull()
                for index in range(preset_list.count())]
            return 0

        with patch.object(canvas_module.QDialog, "exec", new=inspect_dialog):
            panel.edit_image_style_explorer(panel._nodes[node_id])
        self.assertGreater(captured["count"], 40)
        self.assertTrue(all(captured["icons"]))
        panel.close()

    def test_video_style_transfer_keeps_settings_in_compact_dialog_entry(self):
        panel = self.make_panel()
        node_id = panel.create_custom_node("video_node", QPointF(100, 100), {
            "video_style_transfer":True, "provider_name":"seedance",
            "model":"doubao-seedance-2-5-260628", "style_strength":80,
            "style_preserve":["人物身份", "动作时序", "原始声音"],
        })
        panel.show_inline_editor(panel._nodes[node_id])
        self.app.processEvents()
        widget = panel._inline_editor_proxy.widget()
        buttons = [button.text() for button in widget.findChildren(
            canvas_module.QPushButton)]
        labels = [label.text() for label in widget.findChildren(
            canvas_module.QLabel)]
        self.assertIn("输入角色与迁移设置…", buttons)
        self.assertIn("风格迁移强度 80% · 锁定 3 项", labels)
        panel.close()

    def test_multi_image_editor_exposes_web_batch_modes(self):
        panel = self.make_panel()
        node_id = panel.create_custom_node("image_node", QPointF(100, 100), {
            "multi_image_composer":True, "references":[], "reference_assets":[],
        })
        panel.show_inline_editor(panel._nodes[node_id])
        widget = panel._inline_editor_proxy.widget()
        batch_combo = next(
            combo for combo in widget.findChildren(canvas_module.QComboBox)
            if combo.findData("paired") >= 0 and combo.findData("style") >= 0)
        batch_combo.setCurrentIndex(batch_combo.findData("style"))
        self.app.processEvents()
        record = panel._custom_record(node_id)
        self.assertTrue(record["batch_mode"])
        self.assertEqual("style", record["batch_strategy"])
        self.assertEqual("批量换风格", record["editor_action"])
        panel.close()

    def test_web_creative_role_is_used_by_desktop_reference_contract(self):
        panel = self.make_panel()
        node_id = panel.create_custom_node("image_node", QPointF(100, 100), {
            "image_workbench":True, "creative_role":"subject",
        })
        self.assertEqual(
            "character",
            canvas_module._payload_reference_role(panel._nodes[node_id].payload))
        panel.set_image_reference_role(panel._nodes[node_id], "style")
        record = panel._custom_record(node_id)
        self.assertEqual("style", record["reference_role"])
        self.assertEqual("style", record["creative_role"])
        panel.close()

    def test_video_analysis_exposes_and_exports_report(self):
        panel = self.make_panel()
        node_id = panel.create_custom_node("video_analysis_node", QPointF(100, 100), {
            "content":"拉片完成", "analysis_result":{
                "duration":4.0, "average_shot_length":4.0, "rhythm":"舒缓",
                "shots":[{"number":1, "start":0, "end":4,
                          "motion_label":"缓慢移动", "camera_motion":"推进",
                          "subject_trajectory":"从左到右", "trajectory_confidence":0.9}],
            },
        })
        panel.show_inline_editor(panel._nodes[node_id])
        self.app.processEvents()
        widget = panel._inline_editor_proxy.widget()
        buttons = [button.text() for button in widget.findChildren(
            canvas_module.QPushButton)]
        self.assertIn("重新分析", buttons)
        self.assertIn("导出拉片报告", buttons)
        output = self.layout_root / "analysis-report.md"
        with patch.object(canvas_module.QFileDialog, "getSaveFileName",
                          return_value=(str(output), "Markdown 报告 (*.md)")):
            self.assertTrue(panel.export_video_analysis_report(panel._nodes[node_id]))
        exported = output.read_text(encoding="utf-8")
        self.assertIn("AI 拉片报告", exported)
        self.assertIn("0.00–4.00 秒", exported)
        panel.close()

    def test_video_node_persists_web_canvas_audio_and_output_contract(self):
        panel = self.make_panel()
        node_id = panel.create_custom_node("video_node", QPointF(100, 100), {
            "provider_name":"seedance", "model":"doubao-seedance-2-5-260628",
            "duration":30, "resolution":"480p", "generate_audio":False,
            "audio_prompt":"只保留环境风声", "beginner_mode":"text_to_video",
        })
        record = panel._custom_record(node_id)
        self.assertEqual("480p", record["resolution"])
        self.assertFalse(record["generate_audio"])
        self.assertEqual("只保留环境风声", record["audio_prompt"])
        self.assertEqual("text_to_video", record["beginner_mode"])
        durations, resolutions, ratios = panel._video_output_options(
            "seedance", "doubao-seedance-2-5-260628")
        self.assertIn(30, durations)
        self.assertEqual(["480p", "720p"], resolutions)
        self.assertEqual(["adaptive", "16:9", "9:16"], ratios)
        veo_durations, veo_resolutions, veo_ratios = panel._video_output_options(
            "veo", "veo-3.1-generate-preview")
        self.assertEqual([4, 6, 8], veo_durations)
        self.assertEqual(["720p", "1080p"], veo_resolutions)
        self.assertEqual(["16:9", "9:16"], veo_ratios)
        panel.close()

    def test_deleting_source_node_removes_multi_image_reference(self):
        panel = self.make_panel()
        media_root = Path(tempfile.mkdtemp(prefix="cep_reference_sync_"))
        base = media_root / "base.png"
        element = media_root / "element.png"
        base.touch(); element.touch()
        base_id = panel.create_custom_node(
            "image_node", QPointF(100, 100), {"path":str(base)})
        composer_id = panel.send_image_to_composer(panel._nodes[base_id])
        element_id = panel.create_custom_node(
            "image_node", QPointF(100, 400), {"path":str(element)})
        panel.connect_workflow_nodes(
            panel._nodes[element_id], panel._nodes[composer_id])

        panel.scene.clearSelection()
        panel._nodes[element_id].setSelected(True)
        with patch.object(canvas_module.QMessageBox, "question",
                          return_value=canvas_module.QMessageBox.StandardButton.Yes):
            panel.delete_canvas_selection()

        record = panel._custom_record(composer_id)
        self.assertEqual(record["references"], [str(base.resolve())])
        self.assertEqual(len(record["reference_assets"]), 1)
        self.assertEqual(record["reference_assets"][0]["source_node_id"], base_id)
        self.assertEqual(record["status"], "已连接 1 张参考 · 请设置每张图用途")
        panel.close()


if __name__ == "__main__":
    unittest.main()
