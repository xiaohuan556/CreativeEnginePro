import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CEP_DATA_DIR", tempfile.mkdtemp(prefix="cep_recent_ui_"))

try:
    from PyQt6.QtCore import QEvent, QObject, QPoint, QPointF, Qt
    from PyQt6.QtGui import QKeyEvent, QPixmap
    from PyQt6.QtTest import QTest
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

    def test_navigator_middle_handle_collapses_and_restores_sidebar(self):
        panel = self.make_panel()
        panel.resize(1100, 760)
        panel.show()
        self.app.processEvents()
        handle = panel.navigator_collapse_handle
        self.assertFalse(handle.isHidden())
        self.assertEqual("‹", handle.text())
        self.assertLessEqual(
            abs(handle.geometry().center().y() - handle.parentWidget().rect().center().y()),
            2)

        handle.click()
        self.app.processEvents()
        self.assertTrue(panel.navigator_panel.isHidden())
        self.assertFalse(handle.isHidden())
        self.assertEqual("›", handle.text())
        self.assertEqual("展开左侧画布/资产栏", handle.toolTip())

        handle.click()
        self.app.processEvents()
        self.assertFalse(panel.navigator_panel.isHidden())
        self.assertEqual("‹", handle.text())
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

    def test_selection_toolbar_is_a_persistent_canvas_overlay(self):
        panel = self.make_panel()
        panel.resize(1100, 760)
        panel.show()
        self.app.processEvents()
        toolbar = panel.selection_toolbar
        initial_geometry = toolbar.geometry()

        self.assertIs(toolbar.parentWidget(), panel.view)
        self.assertFalse(toolbar.isHidden())
        self.assertEqual("未选择节点", panel.selection_count_label.text())
        self.assertFalse(panel.selection_delete_button.isEnabled())
        self.assertIn(
            "QFrame#canvasSelectionToolbar{background:transparent;",
            toolbar.styleSheet())
        self.assertIn(
            "QLabel#canvasSelectionCount{background:transparent;",
            toolbar.styleSheet())
        self.assertFalse(toolbar.autoFillBackground())
        self.assertTrue(panel.selection_count_label.testAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents))

        node_id = panel.create_custom_node(
            "text_node", QPointF(100, 100), {"content":"固定工具栏测试"})
        panel.scene.clearSelection()
        panel._nodes[node_id].setSelected(True)
        self.app.processEvents()
        self.assertEqual("已选择 1 个节点", panel.selection_count_label.text())
        self.assertTrue(panel.selection_delete_button.isEnabled())
        self.assertEqual(initial_geometry, toolbar.geometry())

        panel.scene.clearSelection()
        self.app.processEvents()
        self.assertFalse(toolbar.isHidden())
        self.assertEqual("未选择节点", panel.selection_count_label.text())
        self.assertFalse(panel.selection_delete_button.isEnabled())
        self.assertEqual(initial_geometry, toolbar.geometry())
        panel.close()

    def test_delete_confirmation_can_remember_never_ask_again(self):
        panel = self.make_panel()

        def confirm(box):
            self.assertEqual("删除选中节点", box.windowTitle())
            self.assertEqual("以后删除节点不再提醒", box.checkBox().text())
            box.checkBox().setChecked(True)
            return int(canvas_module.QMessageBox.StandardButton.Yes)

        with patch.object(
                panel, "_node_delete_confirmation_suppressed", return_value=False), \
                patch.object(canvas_module.QMessageBox, "exec", new=confirm), \
                patch.object(
                    panel, "_set_node_delete_confirmation_suppressed") as remember:
            self.assertTrue(panel._confirm_canvas_node_deletion(2))
        remember.assert_called_once_with(True)
        panel.close()

    def test_suppressed_delete_confirmation_does_not_open_dialog(self):
        panel = self.make_panel()
        with patch.object(
                panel, "_node_delete_confirmation_suppressed", return_value=True), \
                patch.object(canvas_module.QMessageBox, "exec") as execute:
            self.assertTrue(panel._confirm_canvas_node_deletion(1))
        execute.assert_not_called()
        panel.close()

    def test_image_and_video_editors_never_become_native_popup_windows(self):
        panel = self.make_panel()
        for node_type in ("image_node", "video_node"):
            with self.subTest(node_type=node_type):
                node_id = panel.create_custom_node(
                    node_type, QPointF(100, 100), {"content":"原生窗口闪烁回归"})
                panel.show_inline_editor(panel._nodes[node_id])
                self.app.processEvents()
                proxy = panel._inline_editor_proxy
                editor = proxy.widget()
                self.assertIs(editor.graphicsProxyWidget(), proxy)
                self.assertTrue(editor.property(
                    "canvasEditorConstructedAsViewportChild"))
                media_combos = editor.findChildren(canvas_module._CanvasComboBox)
                self.assertTrue(media_combos)
                self.assertTrue(all(
                    combo.parentWidget() is not None and not combo.isWindow()
                    for combo in media_combos))
                self.assertTrue(proxy.isVisible())
                panel.hide_inline_editor()
                panel.scene.clearSelection()
        panel.close()

    def test_new_image_and_video_wait_for_native_menu_to_close_before_editor(self):
        panel = self.make_panel()

        def choose(kind):
            def choose_action(menu, _position):
                for action in menu.actions():
                    if action.text().strip().endswith(kind):
                        return action
                return None
            return choose_action

        for label, node_type in (("图片", "image_node"), ("视频", "video_node")):
            with self.subTest(label=label):
                panel.hide_inline_editor()
                panel.scene.clearSelection()
                with patch.object(canvas_module.QMenu, "exec", new=choose(label)):
                    panel.show_new_asset_menu(QPoint(10, 10), QPointF(320, 240))
                self.assertIsNone(panel._inline_editor_proxy)
                self.assertTrue(panel._deferred_new_media_editor_id)
                QTest.qWait(100)
                self.app.processEvents()
                self.assertIsNotNone(panel._inline_editor_proxy)
                self.assertEqual(
                    node_type, panel._nodes[panel._inline_editor_node_id].node_type)
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

    def test_batch_image_results_create_independent_nodes_without_overwriting_parent(self):
        panel = self.make_panel()
        media_root = Path(tempfile.mkdtemp(prefix="cep_batch_results_"))
        original = media_root / "original.png"
        results = [media_root / f"result_{index}.png" for index in range(3)]
        for path in [original, *results]:
            path.touch()
        parent_id = panel.create_custom_node("image_node", QPointF(120, 180), {
            "title":"批量生图", "path":str(original),
            "multi_image_composer":True, "batch_mode":True,
        })

        task = {"provider":"test-provider", "batch_item_index":0,
                "batch_item_count":2}
        panel._materialize_image_batch_results(
            parent_id, [str(results[0]), str(results[1])], task)
        task["batch_item_index"] = 1
        panel._materialize_image_batch_results(
            parent_id, [str(results[1]), str(results[2])], task)

        parent = panel._custom_record(parent_id)
        self.assertEqual(str(original), parent["path"])
        output_nodes = [
            value for value in panel._positions().get("__custom_nodes__", [])
            if value.get("batch_result_parent_id") == parent_id]
        self.assertEqual(3, len(output_nodes))
        self.assertEqual(
            {str(path) for path in results},
            {value["path"] for value in output_nodes})
        self.assertEqual(3, len({value["id"] for value in output_nodes}))
        self.assertEqual(3, len({tuple(panel._positions()[value["id"]])
                                 for value in output_nodes}))
        ordered_outputs = sorted(
            output_nodes, key=lambda value: int(value["batch_result_index"]))
        result_edges = [
            edge for edge in panel._positions().get("__workflow_edges__", [])
            if edge.get("source") == parent_id and
            edge.get("type") == "batch_result"]
        self.assertEqual(
            [value["id"] for value in ordered_outputs],
            [edge["target"] for edge in result_edges])
        panel.refresh()
        self.assertTrue(all(
            panel._nodes[value["id"]].has_input_port()
            for value in ordered_outputs))
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
        record = panel._custom_record(target_id)
        self.assertEqual("一只小猫", record["content"])
        self.assertEqual("文生图", record["editor_action"])
        self.assertEqual("已同步文字 · 可直接生成图片", record["status"])
        panel.close()

    def test_text_connections_repair_video_and_audio_generation_modes(self):
        panel = self.make_panel()
        source_id = panel.create_custom_node(
            "text_node", QPointF(100, 100), {"content":"雨夜里缓慢推镜"})
        video_id = panel.create_custom_node("video_node", QPointF(500, 100), {
            "content":"", "editor_action":"图生视频",
            "first_frame":"", "last_frame":"",
        })
        audio_id = panel.create_custom_node("audio_node", QPointF(500, 450), {
            "content":"", "editor_action":"音效",
        })

        self.assertTrue(panel.connect_workflow_nodes(
            panel._nodes[source_id], panel._nodes[video_id]))
        video = panel._custom_record(video_id)
        self.assertEqual("雨夜里缓慢推镜", video["content"])
        self.assertEqual("文生视频", video["editor_action"])
        self.assertEqual("已同步文字 · 可直接生成视频", video["status"])

        self.assertTrue(panel.connect_workflow_nodes(
            panel._nodes[source_id], panel._nodes[audio_id]))
        audio = panel._custom_record(audio_id)
        self.assertEqual("雨夜里缓慢推镜", audio["content"])
        self.assertEqual("对白配音", audio["editor_action"])
        self.assertEqual("已同步文字 · 可直接生成配音", audio["status"])
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

    def test_multi_image_editor_uses_plain_colored_reference_text(self):
        panel = self.make_panel()
        media_root = Path(tempfile.mkdtemp(prefix="cep_inline_refs_"))
        images = [media_root / "reference-1.png", media_root / "reference-2.png"]
        for image in images:
            image.touch()
        node_id = panel.create_custom_node("image_node", QPointF(100, 100), {
            "multi_image_composer":True,
            "content":"让第一张图穿上第二张图的衣服",
            "references":[str(image) for image in images],
            "reference_assets":[
                {"path":str(image), "role":"composition",
                 "source_node_id":f"source-{index}"}
                for index, image in enumerate(images)],
        })

        panel.show_inline_editor(panel._nodes[node_id])
        self.app.processEvents()
        widget = panel._inline_editor_proxy.widget()
        button_texts = [button.text() for button in widget.findChildren(
            canvas_module.QPushButton)]
        self.assertNotIn("设置每张图片的用途…", button_texts)
        self.assertIsNone(widget.findChild(
            canvas_module.QScrollArea, "inlineImageReferences"))
        editor = panel._inline_text_editor
        self.assertNotIn("\ufffc", editor.toPlainText())
        self.assertIn("【图片1】", editor.toPlainText())
        self.assertIn("【图片2】", editor.toPlainText())
        self.assertNotIn("\n", editor.toPlainText())
        serialized = panel._custom_record(node_id)["content"]
        self.assertIn("【图片1】", serialized)
        self.assertIn("【图片2】", serialized)
        mentions = panel._custom_record(node_id)["reference_mentions"]
        self.assertNotEqual(mentions[0]["color"], mentions[1]["color"])
        objects = panel._inline_reference_objects(editor)
        self.assertEqual([str(image) for image in images], [
            value["path"] for value in objects])
        for mention, reference in zip(mentions, objects):
            self.assertEqual(mention["token"], reference["text"])
            cursor = canvas_module.QTextCursor(editor.document())
            cursor.setPosition(reference["position"])
            cursor.movePosition(
                canvas_module.QTextCursor.MoveOperation.NextCharacter,
                canvas_module.QTextCursor.MoveMode.KeepAnchor)
            self.assertEqual(
                mention["path"],
                str(cursor.charFormat().property(
                    canvas_module._REFERENCE_PATH_PROPERTY)))
            self.assertEqual(
                canvas_module.QColor(mention["color"]),
                cursor.charFormat().foreground().color())
            self.assertEqual(
                Qt.BrushStyle.NoBrush,
                cursor.charFormat().background().style())
        panel.close()

    def test_ctrl_reference_is_inserted_at_cursor_and_click_locates_source(self):
        panel = self.make_panel()
        media_root = Path(tempfile.mkdtemp(prefix="cep_cursor_ref_"))
        image = media_root / "coat.png"
        image.touch()
        source_id = panel.create_custom_node(
            "image_node", QPointF(100, 100), {"path":str(image)})
        target_id = panel.create_custom_node(
            "image_node", QPointF(500, 100), {
                "multi_image_composer":True, "content":"让角色穿上这件衣服",
                "references":[], "reference_assets":[],
            })
        panel.show_inline_editor(panel._nodes[target_id])
        editor = panel._inline_text_editor
        cursor = editor.textCursor()
        cursor.setPosition(1)
        editor.setTextCursor(cursor)

        self.assertTrue(panel._attach_image_nodes_as_references(
            target_id, [source_id]))
        self.app.processEvents()
        self.assertIn("让 【图片1】 角色", editor.toPlainText())
        self.assertIn(
            "让 【图片1】 角色",
            panel._custom_record(target_id)["content"])
        self.assertFalse(bool(editor.currentCharFormat().property(
            canvas_module._REFERENCE_PATH_PROPERTY)))
        editor.insertPlainText("继续输入")
        typed_cursor = editor.textCursor()
        typed_cursor.movePosition(
            canvas_module.QTextCursor.MoveOperation.PreviousCharacter,
            canvas_module.QTextCursor.MoveMode.KeepAnchor)
        self.assertFalse(bool(typed_cursor.charFormat().property(
            canvas_module._REFERENCE_PATH_PROPERTY)))
        editor.referenceActivated.emit(str(image), source_id)
        self.app.processEvents()
        self.assertTrue(panel._nodes[source_id].isSelected())

        token = "【图片1】"
        object_position = editor.toPlainText().index(token)
        cursor = canvas_module.QTextCursor(editor.document())
        cursor.setPosition(object_position)
        cursor.setPosition(
            object_position + len(token),
            canvas_module.QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        panel._sync_inline_reference_mentions(
            target_id, editor, allow_removal=True)
        self.assertEqual([], panel._custom_record(target_id)["references"])
        panel.close()

    def test_ctrl_reference_helper_uses_plain_reference_role(self):
        panel = self.make_panel()
        media_root = Path(tempfile.mkdtemp(prefix="cep_ctrl_refs_"))
        image = media_root / "reference.png"
        image.touch()
        source_id = panel.create_custom_node(
            "image_node", QPointF(100, 100), {"path":str(image)})
        target_id = panel.create_custom_node(
            "image_node", QPointF(500, 100), {
                "multi_image_composer":True,
                "references":[], "reference_assets":[],
            })

        self.assertTrue(panel._attach_image_nodes_as_references(
            target_id, [source_id]))
        record = panel._custom_record(target_id)
        self.assertEqual([str(image.resolve())], record["references"])
        self.assertEqual("reference", record["reference_assets"][0]["role"])
        self.assertEqual("已引用 1 张图片", record["status"])
        self.assertTrue(any(
            edge.get("source") == source_id and
            edge.get("target") == target_id and
            edge.get("type") == "reference"
            for edge in panel._positions()["__workflow_edges__"]))
        panel.close()

    def test_inline_combo_popups_keep_proxy_alive_and_commit_selection(self):
        panel = self.make_panel()
        panel.resize(1200, 800)
        panel.show()
        node_id = panel.create_custom_node("image_node", QPointF(100, 100), {
            "multi_image_composer":True, "references":[], "reference_assets":[],
        })
        panel.show_inline_editor(panel._nodes[node_id])
        self.app.processEvents()
        widget = panel._inline_editor_proxy.widget()
        batch_combo = next(
            combo for combo in widget.findChildren(canvas_module.QComboBox)
            if combo.findData("paired") >= 0 and combo.findData("style") >= 0)

        # Exercise the real press/release path: placing the menu too close to
        # the pointer used to make mouse-up close it immediately, which looked
        # like the size/model dropdown could not be clicked.
        QTest.mouseClick(
            batch_combo, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier, batch_combo.rect().center())
        self.app.processEvents()
        self.assertTrue(batch_combo._canvas_popup_menu.isVisible())
        popup_actions = batch_combo._canvas_popup_menu.actions()
        self.assertTrue(
            popup_actions[batch_combo.currentIndex()].text().startswith("✓  "))
        self.assertTrue(all(not action.isCheckable() for action in popup_actions))
        self.assertFalse(any(
            action.text().startswith("✓  ")
            for index, action in enumerate(popup_actions)
            if index != batch_combo.currentIndex()))
        self.assertTrue(widget.property("canvasComboPopupOpen"))
        panel.scene.clearSelection()
        panel._hide_inline_editor_if_unfocused()
        self.assertIsNotNone(panel._inline_editor_proxy)

        style_index = batch_combo.findData("style")
        batch_combo._canvas_popup_menu.actions()[style_index].trigger()
        batch_combo.hidePopup()
        self.app.processEvents()
        self.assertEqual("style", batch_combo.currentData())
        self.assertEqual("style", panel._custom_record(node_id)["batch_strategy"])

        ratio_combo = next(
            combo for combo in widget.findChildren(canvas_module.QComboBox)
            if combo.findText("16:9") >= 0 and combo.findText("9:16") >= 0)
        ratio_combo.showPopup()
        self.app.processEvents()
        vertical_index = ratio_combo.findText("9:16")
        ratio_combo._canvas_popup_menu.actions()[vertical_index].trigger()
        ratio_combo.hidePopup()
        self.app.processEvents()
        self.assertEqual("9:16", panel._custom_record(node_id)["ratio"])
        panel.close()

    def test_inline_combo_popup_is_below_its_control_after_zoom_and_pan(self):
        panel = self.make_panel()
        panel.resize(1200, 800)
        panel.show()
        node_id = panel.create_custom_node("image_node", QPointF(420, 260), {
            "multi_image_composer":True, "references":[], "reference_assets":[],
        })
        panel.show_inline_editor(panel._nodes[node_id])
        self.app.processEvents()
        panel.view.scale(1.35, 1.35)
        panel.view.centerOn(panel._inline_editor_proxy.sceneBoundingRect().center())
        self.app.processEvents()

        widget = panel._inline_editor_proxy.widget()
        ratio_combo = next(
            combo for combo in widget.findChildren(canvas_module.QComboBox)
            if combo.findText("16:9") >= 0 and combo.findText("9:16") >= 0)
        proxy = widget.graphicsProxyWidget()
        panel_point = ratio_combo.mapTo(
            widget, QPoint(0, ratio_combo.height()))
        expected = panel.view.viewport().mapToGlobal(
            panel.view.mapFromScene(proxy.mapToScene(QPointF(panel_point))))
        self.assertEqual(expected, ratio_combo._popup_anchor())

        QTest.mouseClick(
            ratio_combo, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier, ratio_combo.rect().center())
        self.app.processEvents()
        menu = ratio_combo._canvas_popup_menu
        self.assertTrue(menu.isVisible())
        # A parentless QMenu remains a real screen popup instead of becoming
        # a second proxy widget with a duplicate scene-coordinate transform.
        self.assertIsNone(menu.parent())
        popup_size = menu.sizeHint()
        screen = QApplication.screenAt(expected) or QApplication.primaryScreen()
        available = screen.availableGeometry()
        bounded_x = max(available.left(), min(
            expected.x(), available.right() - popup_size.width() + 1))
        bounded_y = expected.y()
        if bounded_y + popup_size.height() > available.bottom() + 1:
            above_y = (
                expected.y() - ratio_combo.height() - popup_size.height())
            if above_y >= available.top():
                bounded_y = above_y
            else:
                bounded_y = max(
                    available.top(),
                    available.bottom() - popup_size.height() + 1)
        bounded_y = max(available.top(), min(
            bounded_y, available.bottom() - popup_size.height() + 1))
        position_debug = (
            f"menu={menu.pos()}, raw={expected}, bounded="
            f"QPoint({bounded_x}, {bounded_y}), size={popup_size}, "
            f"screen={available}")
        self.assertLessEqual(
            abs(menu.pos().x() - bounded_x), 2, position_debug)
        self.assertLessEqual(
            abs(menu.pos().y() - bounded_y), 2, position_debug)
        menu.close()

        model_combo = next(
            combo for combo in widget.findChildren(canvas_module.QComboBox)
            if combo is not ratio_combo and combo.isVisible() and combo.count())
        QTest.mouseClick(
            model_combo, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier, model_combo.rect().center())
        self.app.processEvents()
        self.assertTrue(model_combo._canvas_popup_menu.isVisible())
        self.assertIsNone(model_combo._canvas_popup_menu.parent())
        model_combo._canvas_popup_menu.close()
        panel.close()

    def test_multi_image_composer_repairs_stale_scene_asset_badge(self):
        panel = self.make_panel()
        media_root = Path(tempfile.mkdtemp(prefix="cep_stale_scene_badge_"))
        reference = media_root / "reference.png"
        result = media_root / "result.png"
        reference.touch(); result.touch()
        node_id = panel.create_custom_node("image_node", QPointF(100, 100), {
            "title":"图片", "multi_image_composer":True,
            "references":[str(reference)], "path":str(result),
            "asset_kind":"scene", "scene_reference_set":{"master":str(result)},
            "status":"场景视图 1/5 · 待补齐",
        })

        record = panel._custom_record(node_id)
        self.assertNotIn("asset_kind", record)
        self.assertNotIn("scene_reference_set", record)
        self.assertEqual("已引用 1 张图片 · 已生成结果", record["status"])
        self.assertEqual(record["status"], panel._nodes[node_id].badge)
        panel.close()

    def test_every_inline_node_combo_uses_proxy_safe_popup(self):
        panel = self.make_panel()
        media_root = Path(tempfile.mkdtemp(prefix="cep_all_inline_combos_"))
        imported_image = media_root / "uploaded.png"
        imported_image.touch()
        cases = (
            ("text_node", {"content":"一句文字", "plain_text":True}),
            ("text_node", {"content":"一段脚本", "script_versions":[]}),
            ("image_node", {"content":"修改图片", "path":str(imported_image),
                            "editor_action":"AI 编辑"}),
            ("image_node", {"content":"生成画面", "multi_image_composer":True}),
            ("video_node", {"content":"生成视频", "editor_action":"文生视频"}),
            ("audio_node", {"content":"生成对白", "editor_action":"对白配音"}),
            ("skill_node", {"content":"检查连续性", "skill_id":"continuity"}),
        )
        for index, (node_type, payload) in enumerate(cases):
            node_id = panel.create_custom_node(
                node_type, QPointF(100 + index * 20, 100), payload)
            panel.show_inline_editor(panel._nodes[node_id])
            self.app.processEvents()
            widget = panel._inline_editor_proxy.widget()
            combos = widget.findChildren(canvas_module.QComboBox)
            self.assertTrue(all(isinstance(
                combo, canvas_module._CanvasComboBox) for combo in combos))
            panel.hide_inline_editor()
        panel.close()

    def test_single_image_generation_materializes_every_result_with_own_edge(self):
        panel = self.make_panel()
        parent_id = panel.create_custom_node(
            "image_node", QPointF(100, 100), {
                "title":"图片", "multi_image_composer":True,
                "references":[], "candidates":[],
            })
        media_root = Path(tempfile.mkdtemp(prefix="cep_single_image_results_"))
        paths = [media_root / f"candidate-{index}.png" for index in range(3)]
        for path in paths:
            path.touch()

        child_ids = panel._materialize_standalone_generation_results(
            parent_id, [str(path) for path in paths], "image",
            {"provider":"gptimage"})

        self.assertEqual(3, len(child_ids))
        parent = panel._custom_record(parent_id)
        self.assertEqual(child_ids, parent["materialized_result_node_ids"])
        for index, (child_id, path) in enumerate(zip(child_ids, paths)):
            child = panel._custom_record(child_id)
            self.assertEqual(str(path), child["path"])
            self.assertEqual(parent_id, child["generation_result_parent_id"])
            self.assertEqual(index, child["generation_result_index"])
            self.assertGreater(
                panel._positions()[child_id][0], panel._positions()[parent_id][0])
            self.assertTrue(any(
                edge.get("source") == parent_id and
                edge.get("target") == child_id and
                edge.get("type") == "generation_result"
                for edge in panel._positions()["__workflow_edges__"]))
        panel.refresh()
        self.assertEqual("", panel._nodes[parent_id].thumbnail)
        self.assertTrue(all(child_id in panel._nodes for child_id in child_ids))
        panel.close()

    def test_single_video_generation_materializes_every_result_with_own_edge(self):
        panel = self.make_panel()
        parent_id = panel.create_custom_node(
            "video_node", QPointF(100, 100), {
                "title":"视频", "candidates":[],
            })
        media_root = Path(tempfile.mkdtemp(prefix="cep_single_video_results_"))
        paths = [media_root / f"candidate-{index}.mp4" for index in range(2)]
        for path in paths:
            path.touch()

        with patch.object(panel, "_extract_video_review_frames", return_value=[]):
            child_ids = panel._materialize_standalone_generation_results(
                parent_id, [str(path) for path in paths], "video",
                {"provider":"seedance"}, primary_video_frames=[])

        self.assertEqual(2, len(child_ids))
        for child_id, path in zip(child_ids, paths):
            child = panel._custom_record(child_id)
            self.assertEqual("video", child["kind"])
            self.assertEqual(str(path), child["path"])
            self.assertEqual(parent_id, child["generation_result_parent_id"])
            self.assertTrue(any(
                edge.get("source") == parent_id and
                edge.get("target") == child_id
                for edge in panel._positions()["__workflow_edges__"]))
        panel.close()

    def test_imported_video_keeps_its_cover_after_result_nodes_are_created(self):
        panel = self.make_panel()
        media_root = Path(tempfile.mkdtemp(prefix="cep_imported_video_parent_"))
        source = media_root / "uploaded.mp4"
        cover = media_root / "uploaded-middle.jpg"
        result = media_root / "generated.mp4"
        for path in (source, cover, result):
            path.touch()
        parent_id = panel.create_custom_node(
            "video_node", QPointF(100, 100), {
                "title":"uploaded", "path":str(source),
                "video_thumbnail":str(cover), "media_origin":"uploaded",
                "source_media_path":str(source), "source_duration":10,
            })

        with patch.object(panel, "_extract_video_review_frames", return_value=[]):
            panel._materialize_standalone_generation_results(
                parent_id, [str(result)], "video", {"provider":"seedance"})
        panel.refresh()

        self.assertEqual(str(cover), panel._nodes[parent_id].thumbnail)
        self.assertTrue(panel._is_imported_media_record(
            panel._custom_record(parent_id), "video_node"))
        panel.close()

    def test_completed_video_edit_does_not_overwrite_uploaded_source_node(self):
        panel = self.make_panel()
        media_root = Path(tempfile.mkdtemp(prefix="cep_video_source_result_"))
        source = media_root / "uploaded.mp4"
        cover = media_root / "uploaded-middle.jpg"
        result = media_root / "edited.mp4"
        generated_cover = media_root / "edited-middle.jpg"
        for path in (source, cover, result, generated_cover):
            path.touch()
        parent_id = panel.create_custom_node(
            "video_node", QPointF(100, 100), {
                "title":"uploaded", "path":str(source),
                "video_thumbnail":str(cover), "media_origin":"uploaded",
                "source_media_path":str(source), "source_duration":10,
            })
        handle = SimpleNamespace(
            progress=1.0, is_finished=True, is_success=True,
            result=SimpleNamespace(data=str(result), provider_raw={}))
        panel._standalone_tasks["finished-edit"] = {
            "handle":handle, "node_id":parent_id, "provider":"seedance",
            "kind":"", "preserve_source_media":True,
            "source_media_path":str(source),
        }

        with (
            patch.object(
                panel, "_extract_video_review_frames",
                return_value=[str(generated_cover)] * 3),
            patch.object(panel, "_run_spatial_consistency_review", return_value={}),
            patch.object(canvas_module, "inspect_frame_paths", return_value={}),
            patch.object(canvas_module, "inspect_av_sync", return_value={}),
        ):
            panel._poll_standalone_tasks()

        parent = panel._custom_record(parent_id)
        self.assertEqual(str(source), parent["path"])
        self.assertEqual("uploaded", parent["title"])
        self.assertEqual(str(cover), parent["video_thumbnail"])
        child_ids = list(parent.get("materialized_result_node_ids") or [])
        self.assertEqual(1, len(child_ids))
        self.assertEqual(str(result), panel._custom_record(child_ids[0])["path"])
        panel.close()

    def test_batch_style_does_not_require_outer_prompt(self):
        panel = self.make_panel()
        node_id = panel.create_custom_node("image_node", QPointF(100, 100), {
            "multi_image_composer":True, "batch_mode":True,
            "batch_strategy":"style", "editor_action":"批量换风格",
        })
        node = panel._nodes[node_id]
        with patch.object(panel, "submit_multi_image_batch", return_value=True) as submit:
            panel.submit_standalone_generation(node, "", "批量换风格")
        submit.assert_called_once_with(node, "", "批量换风格")
        panel.close()

    def test_batch_import_creates_source_nodes_and_edges_in_one_action(self):
        panel = self.make_panel()
        node_id = panel.create_custom_node("image_node", QPointF(600, 300), {
            "multi_image_composer":True, "references":[],
            "reference_assets":[], "batch_mode":True,
            "batch_strategy":"style",
        })
        media_root = Path(tempfile.mkdtemp(prefix="cep_batch_import_"))
        paths = [media_root / f"image-{index}.png" for index in range(3)]
        for path in paths:
            path.touch()

        created = panel.import_images_into_node(
            node_id, [str(path) for path in paths])

        self.assertEqual(3, len(created))
        record = panel._custom_record(node_id)
        self.assertEqual(3, len(record["references"]))
        self.assertEqual(3, len(record["reference_assets"]))
        self.assertTrue(record["batch_mode"])
        self.assertEqual("批量换风格", record["editor_action"])
        incoming = [edge for edge in panel._positions()["__workflow_edges__"]
                    if edge.get("target") == node_id]
        self.assertEqual(set(created), {edge["source"] for edge in incoming})
        panel.close()

    def test_imported_video_only_exposes_existing_video_actions(self):
        panel = self.make_panel()
        source = Path(tempfile.mkdtemp(prefix="cep_video_actions_")) / "clip.mp4"
        source.touch()
        node_id = panel.create_custom_node(
            "video_node", QPointF(100, 100), {"path":str(source)})
        node = panel._nodes[node_id]
        self.assertEqual(
            ["按时间戳修改", "提取首中尾帧", "基于完整视频续长"],
            panel._node_action_options(node))
        panel.show_inline_editor(node)
        self.app.processEvents()
        self.assertTrue(panel._inline_text_editor.isHidden())
        visible_labels = [label.text() for label in
                          panel._inline_editor_proxy.widget().findChildren(
                              canvas_module.QLabel) if not label.isHidden()]
        self.assertIn(
            "修改提示词、时间范围和画面选区都在“时间与画面选区”中填写。",
            visible_labels)
        panel.close()

    def test_empty_and_imported_image_nodes_expose_only_relevant_actions(self):
        panel = self.make_panel()
        empty_id = panel.create_custom_node(
            "image_node", QPointF(100, 100), {
                "multi_image_composer":True, "beginner_mode":"text_to_image",
                "references":[], "reference_assets":[],
            })
        self.assertEqual(
            ["文生图"], panel._node_action_options(panel._nodes[empty_id]))

        source = Path(tempfile.mkdtemp(prefix="cep_image_actions_")) / "frame.png"
        source.touch()
        imported_id = panel.create_custom_node(
            "image_node", QPointF(500, 100), {"path":str(source)})
        actions = panel._node_action_options(panel._nodes[imported_id])
        self.assertEqual(
            ["AI 编辑", "图片高清", "智能扩图", "移除背景", "替换背景"],
            actions)
        self.assertNotIn("文生图", actions)
        self.assertNotIn("图生图", actions)
        panel.close()

    def test_imported_audio_is_a_source_without_generation_menu(self):
        panel = self.make_panel()
        source = Path(tempfile.mkdtemp(prefix="cep_audio_actions_")) / "voice.wav"
        source.touch()
        node_id = panel.create_custom_node(
            "audio_node", QPointF(100, 100), {"path":str(source)})
        panel.show_inline_editor(panel._nodes[node_id])
        self.assertIsNone(panel._inline_editor_proxy)
        panel.close()

    def test_video_region_preview_keeps_source_aspect_ratio(self):
        frame = canvas_module._VideoRegionLabel()
        frame.resize(520, 400)
        frame.setPixmap(QPixmap(1600, 900))
        frame.set_region({"x":0.2, "y":0.2, "width":0.35, "height":0.35})
        rect = frame._content_rect()
        self.assertAlmostEqual(16 / 9, rect.width() / rect.height(), places=3)
        self.assertLess(rect.height(), frame.height())
        self.assertTrue(frame._inside_region((0.3, 0.3)))
        frame.close()

    def test_video_region_has_no_default_box(self):
        frame = canvas_module._VideoRegionLabel()
        self.assertEqual({}, frame.region())
        self.assertFalse(frame._inside_region((0.3, 0.3)))
        frame.set_region({})
        self.assertEqual({}, frame.region())
        frame.close()

    def test_timestamp_dialog_uses_timeline_only_without_helper_rows(self):
        panel = self.make_panel()
        node_id = panel.create_custom_node(
            "video_node", QPointF(100, 100), {
                "source_duration":12, "duration":12,
                "video_edit_region":{"x":0.2, "y":0.2,
                                     "width":0.35, "height":0.35},
            })
        captured = {}

        def inspect_dialog(dialog):
            texts = [label.text() for label in dialog.findChildren(
                canvas_module.QLabel)]
            captured["buttons"] = [button.text() for button in dialog.findChildren(
                canvas_module.QPushButton)]
            captured["texts"] = texts
            captured["spinboxes"] = len(dialog.findChildren(
                canvas_module.QDoubleSpinBox))
            captured["region"] = dialog.findChild(
                canvas_module._VideoRegionLabel).region()
            return 0

        with patch.object(canvas_module.QDialog, "exec", new=inspect_dialog):
            panel.edit_video_timestamp_settings(panel._nodes[node_id])
        joined = "\n".join(captured["texts"])
        self.assertNotIn("实时预览", joined)
        self.assertNotIn("拖动蓝色片段", joined)
        self.assertNotIn("精确数值", joined)
        self.assertNotIn("开始秒", joined)
        self.assertNotIn("结束秒", joined)
        self.assertEqual(0, captured["spinboxes"])
        self.assertEqual({}, captured["region"])
        self.assertTrue(any(text.startswith("▶ 播放选中片段")
                            for text in captured["buttons"]))
        panel.close()

    def test_batch_image_submission_is_serial_instead_of_flooding_provider(self):
        panel = self.make_panel()
        media_root = Path(tempfile.mkdtemp(prefix="cep_batch_queue_"))
        sources = [media_root / f"source-{index}.png" for index in range(3)]
        for source in sources:
            source.touch()
        node_id = panel.create_custom_node("image_node", QPointF(100, 100), {
            "multi_image_composer":True, "batch_mode":True,
            "batch_strategy":"style", "provider_name":"gpt-image",
            "reference_assets":[{"path":str(source), "source_node_id":f"s{index}"}
                                for index, source in enumerate(sources)],
        })

        class Provider:
            name = "gpt-image"

        class Registry:
            @staticmethod
            def by_capability(_capability):
                return [Provider()]

        class Handle:
            id = "only-first-item"
            is_finished = False
            progress = 0.0

        class Manager:
            registry = Registry()

            def __init__(self):
                self.submissions = []

            def submit(self, provider, request):
                self.submissions.append((provider, request))
                return Handle()

        manager = Manager()
        with patch.object(canvas_module, "get_ai_manager", return_value=manager):
            self.assertTrue(panel.submit_multi_image_batch(
                panel._nodes[node_id], "保持构图并转换统一风格", "批量换风格"))

        self.assertEqual(1, len(manager.submissions))
        self.assertEqual(1, len(panel._standalone_tasks))
        self.assertEqual(2, len(panel._image_batch_queues[node_id]["pending"]))
        panel.close()

    def test_image_rate_limit_retry_delay_honors_server_wait(self):
        self.assertEqual(
            37.0,
            canvas_module.ProductionCanvasTab._image_batch_retry_seconds(
                "GPT-Image 编辑失败 429: Please retry after 36 seconds."))
        self.assertEqual(
            0.0,
            canvas_module.ProductionCanvasTab._image_batch_retry_seconds(
                "invalid prompt"))

    def test_batch_rate_limit_is_requeued_without_error_popup(self):
        panel = self.make_panel()
        node_id = panel.create_custom_node("image_node", QPointF(100, 100), {
            "multi_image_composer":True, "batch_mode":True,
            "batch_item_count":2, "batch_completed_items":0,
        })

        class Result:
            success = False
            error = "429 RateLimitReached: Please retry after 36 seconds."

        class Handle:
            id = "limited-item"
            is_finished = True
            is_success = False
            progress = 1.0
            result = Result()

        queued_tail = {
            "node_id":node_id, "provider":"gpt-image", "request":object(),
            "kind":"image_batch", "batch_item_index":1,
            "batch_item_count":2, "batch_retry_count":0,
        }
        panel._image_batch_queues[node_id] = {
            "pending":[queued_tail], "active_task_id":"limited-item",
            "total":2, "failed":0, "last_error":"",
        }
        panel._active_image_batch_node_id = node_id
        panel._standalone_tasks["limited-item"] = {
            "handle":Handle(), "node_id":node_id, "provider":"gpt-image",
            "request":object(), "fallback_providers":[],
            "kind":"image_batch", "batch_item_index":0,
            "batch_item_count":2, "batch_retry_count":0,
            "batch_queue_parent_id":node_id,
        }

        with patch.object(canvas_module.QMessageBox, "warning") as warning, \
                patch.object(canvas_module.QTimer, "singleShot") as single_shot:
            panel._poll_standalone_tasks()

        warning.assert_not_called()
        self.assertNotIn("limited-item", panel._standalone_tasks)
        queue = panel._image_batch_queues[node_id]
        self.assertEqual(2, len(queue["pending"]))
        self.assertEqual(1, queue["pending"][0]["batch_retry_count"])
        self.assertIn("37 秒后自动重试", panel._custom_record(node_id)["status"])
        self.assertTrue(any(call.args[0] == 37000 for call in single_shot.call_args_list))
        panel.close()

    def test_video_timeline_clip_can_be_dragged_as_a_block(self):
        timeline = canvas_module._VideoTimeRange(20)
        timeline.resize(600, 78)
        timeline.set_range(2, 6)

        class PointerEvent:
            def __init__(self, x, kind):
                self._point = QPointF(x, 37)
                self._kind = kind
                self.accepted = False

            def position(self):
                return self._point

            def button(self):
                return Qt.MouseButton.LeftButton

            def accept(self):
                self.accepted = True

        timeline.mousePressEvent(PointerEvent(timeline._x_for_time(4), "press"))
        timeline.mouseMoveEvent(PointerEvent(timeline._x_for_time(8), "move"))
        timeline.mouseReleaseEvent(PointerEvent(timeline._x_for_time(8), "release"))
        start, end = timeline.range()
        self.assertAlmostEqual(6.0, start, places=2)
        self.assertAlmostEqual(10.0, end, places=2)
        timeline.close()

    def test_video_timeline_never_exceeds_thirty_seconds(self):
        timeline = canvas_module._VideoTimeRange(90)
        timeline.set_range(5, 80)
        start, end = timeline.range()
        self.assertEqual(5, start)
        self.assertEqual(35, end)
        timeline.close()

    def test_extracting_video_frames_never_reuses_deleted_node_item(self):
        panel = self.make_panel()
        media_root = Path(tempfile.mkdtemp(prefix="cep_extract_frames_"))
        video = media_root / "source.mp4"
        video.touch()
        frames = [media_root / f"frame-{index}.jpg" for index in range(3)]
        for frame in frames:
            frame.touch()
        source_id = panel.create_custom_node(
            "video_node", QPointF(100, 100), {"path":str(video), "title":"原视频"})

        with patch.object(
                panel, "_extract_video_review_frames",
                return_value=[str(frame) for frame in frames]):
            panel.extract_video_frames_to_canvas(panel._nodes[source_id])

        record = panel._custom_record(source_id)
        self.assertEqual([str(frame) for frame in frames], record["video_review_frames"])
        extracted = [value for value in panel._positions()["__custom_nodes__"]
                     if value.get("path") in {str(frame) for frame in frames}]
        self.assertEqual(3, len(extracted))
        edges = [edge for edge in panel._positions()["__workflow_edges__"]
                 if edge.get("source") == source_id and
                 edge.get("type") == "video_frame"]
        self.assertEqual(3, len(edges))
        panel.close()

    def test_connection_port_snaps_within_screen_distance(self):
        panel = self.make_panel()
        source_id = panel.create_custom_node(
            "text_node", QPointF(100, 100), {"plain_text":True})
        target_id = panel.create_custom_node(
            "image_node", QPointF(600, 100), {"multi_image_composer":True})
        source, target = panel._nodes[source_id], panel._nodes[target_id]
        near = target.port_scene_pos("input") - QPointF(45, 0)
        self.assertIs(target, panel.port_node_at(near, "input", source))
        panel.close()

    def test_clicking_image_right_side_does_not_start_a_wire(self):
        panel = self.make_panel()
        node_id = panel.create_custom_node(
            "image_node", QPointF(100, 100), {"multi_image_composer":True})
        node = panel._nodes[node_id]
        output = node.port_scene_pos("output")
        self.assertIs(node, panel.port_node_at(output, "output"))
        self.assertIsNone(panel.port_node_at(output - QPointF(45, 0), "output"))
        self.assertIsNone(panel.port_node_at(output + QPointF(0, 45), "output"))
        panel.close()

    def test_node_click_jitter_cannot_change_position(self):
        panel = self.make_panel()
        node_id = panel.create_custom_node(
            "text_node", QPointF(240, 180), {"plain_text":True})
        node = panel._nodes[node_id]
        self.assertFalse(bool(
            node.flags() &
            canvas_module.QGraphicsItem.GraphicsItemFlag.ItemIsMovable))
        origin = QPointF(node.pos())
        press = QPointF(300, 240)
        node._move_press_scene_pos = press
        node._move_press_screen_pos = QPoint(300, 240)
        node._move_origin_positions = {node_id:origin}

        class MoveEvent:
            def __init__(self, scene_pos, screen_pos=None):
                self._scene_pos = scene_pos
                self._screen_pos = screen_pos or scene_pos.toPoint()
                self.accepted = False

            def scenePos(self):
                return self._scene_pos

            def buttons(self):
                return Qt.MouseButton.LeftButton

            def screenPos(self):
                return self._screen_pos

            def accept(self):
                self.accepted = True

        tiny_move = MoveEvent(press + QPointF(1, 1))
        node.mouseMoveEvent(tiny_move)
        self.assertEqual(origin, node.pos())
        self.assertFalse(node._move_drag_started)

        # Opening the inline editor may change scene coordinates under the
        # stationary pointer; screen-space jitter must still count as a click.
        remapped_scene = MoveEvent(QPointF(900, 700), QPoint(301, 241))
        node.mouseMoveEvent(remapped_scene)
        self.assertEqual(origin, node.pos())
        self.assertFalse(node._move_drag_started)

        real_drag = MoveEvent(
            press + QPointF(30, 20), QPoint(330, 260))
        node.mouseMoveEvent(real_drag)
        self.assertEqual(origin + QPointF(30, 20), node.pos())
        self.assertTrue(node._move_drag_started)
        panel.close()

    def test_pointer_selection_does_not_open_editor_until_click_release(self):
        panel = self.make_panel()
        node_id = panel.create_custom_node(
            "text_node", QPointF(240, 180), {"plain_text":True})
        node = panel._nodes[node_id]

        panel.hide_inline_editor()
        panel.scene.clearSelection()
        panel._defer_inline_editor_until_pointer_release = True
        node.setSelected(True)
        self.app.processEvents()

        self.assertIsNone(panel._inline_editor_proxy)
        panel._defer_inline_editor_until_pointer_release = False
        panel.show_inline_editor(node)
        first_proxy = panel._inline_editor_proxy
        panel.show_inline_editor(node)
        self.assertIs(first_proxy, panel._inline_editor_proxy)
        panel.close()

    def test_node_editor_cannot_flash_as_native_window_before_embedding(self):
        panel = self.make_panel()
        node_id = panel.create_custom_node(
            "text_node", QPointF(240, 180), {"plain_text":True, "content":"测试"})
        node = panel._nodes[node_id]
        panel.resize(1200, 800)
        panel.show()
        panel.view.centerOn(node)
        self.app.processEvents()
        panel.hide_inline_editor()
        panel.scene.clearSelection()
        self.app.processEvents()

        class ShowProbe(QObject):
            def __init__(self):
                super().__init__()
                self.native_show_states = []

            def eventFilter(self, obj, event):
                if (event.type() == QEvent.Type.Show and
                        getattr(obj, "objectName", lambda: "")() == "inlineNodeEditor" and
                        getattr(obj, "isWindow", lambda: False)()):
                    self.native_show_states.append(obj.testAttribute(
                        Qt.WidgetAttribute.WA_DontShowOnScreen))
                return False

        probe = ShowProbe()
        self.app.installEventFilter(probe)
        point = panel.view.mapFromScene(node.sceneBoundingRect().center())
        QTest.mouseClick(panel.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.app.processEvents()
        self.app.removeEventFilter(probe)

        # Best case: an editor embedded before child construction never emits
        # a native Show event.  If a Qt backend still emits one, it must remain
        # protected from being mapped on screen for the editor's lifetime.
        self.assertTrue(all(probe.native_show_states))
        self.assertIsNotNone(panel._inline_editor_proxy)
        panel.close()

    def test_node_context_menu_hides_editor_and_suppresses_reopening(self):
        panel = self.make_panel()
        node_id = panel.create_custom_node(
            "text_node", QPointF(240, 180), {"plain_text":True})
        node = panel._nodes[node_id]
        panel.show_inline_editor(node)
        self.assertIsNotNone(panel._inline_editor_proxy)
        observed = {}

        class ContextEvent:
            accepted = False

            @staticmethod
            def screenPos():
                return QPoint(300, 300)

            def accept(self):
                self.accepted = True

        def inspect_menu(_node, _position):
            observed["suppressed"] = panel._suppress_inline_editor_for_context_menu
            observed["editor"] = panel._inline_editor_proxy

        event = ContextEvent()
        with patch.object(panel, "show_node_context_menu", side_effect=inspect_menu):
            node.contextMenuEvent(event)

        self.assertTrue(event.accepted)
        self.assertTrue(observed["suppressed"])
        self.assertIsNone(observed["editor"])
        self.assertFalse(panel._suppress_inline_editor_for_context_menu)
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
        with patch.object(panel, "_confirm_canvas_node_deletion", return_value=True):
            panel.delete_canvas_selection()

        record = panel._custom_record(composer_id)
        self.assertEqual(record["references"], [str(base.resolve())])
        self.assertEqual(len(record["reference_assets"]), 1)
        self.assertEqual(record["reference_assets"][0]["source_node_id"], base_id)
        self.assertEqual(record["status"], "已引用 1 张图片")
        panel.close()


if __name__ == "__main__":
    unittest.main()
