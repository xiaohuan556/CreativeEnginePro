import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CEP_DATA_DIR", tempfile.mkdtemp(prefix="cep_shortcuts_"))
from PyQt6.QtCore import Qt, QPointF, QEvent
from PyQt6.QtGui import QKeyEvent, QPixmap
from PyQt6.QtWidgets import QApplication, QLineEdit, QTextEdit, QComboBox, QDoubleSpinBox, QWidget
from PyQt6.QtTest import QTest
import ai.ui.production_canvas as canvas


CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier


class CanvasShortcutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.root = Path(tempfile.mkdtemp(prefix="cep_shortcut_tests_"))

    def setUp(self):
        self.layout_patch = patch.object(canvas, "LAYOUT_FILE", self.root / (self._testMethodName + ".json"))
        self.layout_patch.start()
        self.panel = canvas.ProductionCanvasTab()
        self.panel._checkpoint_timer.stop()
        self.panel._task_timer.stop()
        self.panel._layout_timer.stop()
        self.panel._layout_timer.timeout.disconnect()
        self.panel._save_layout_now = lambda *a, **k: None
        self.ids = [self.panel.create_custom_node("text_node", QPointF(i * 320, 100),
                    {"content": f"节点{i}"}) for i in range(4)]
        self.panel.resize(1400, 900)
        self.panel.show()
        self.panel.activateWindow()
        self.canvas_focus()

    def tearDown(self):
        self.panel.close()
        self.app.processEvents()
        self.panel.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.layout_patch.stop()

    def canvas_focus(self):
        self.panel.hide_inline_editor()
        self.panel.scene.clearFocus()
        self.panel.view.clearFocus()
        self.panel.view.setFocus()
        self.app.processEvents()

    def select(self, ids):
        self.panel._defer_inline_editor_until_pointer_release = True
        self.panel.scene.clearSelection()
        for item_id in ids:
            (self.panel._nodes.get(item_id) or self.panel._canvas_groups[item_id]).setSelected(True)
        self.panel._defer_inline_editor_until_pointer_release = False
        self.canvas_focus()

    def key(self, key, modifiers=Qt.KeyboardModifier.NoModifier, target=None):
        QTest.keyClick(target or self.panel.view.viewport(), key, modifiers)
        self.app.processEvents()

    def make_group(self):
        self.select(self.ids[:2])
        with patch.object(canvas.QInputDialog, "getText", return_value=("测试分组", True)):
            self.key(Qt.Key.Key_G, CTRL)
        self.assertEqual(1, len(self.panel._canvas_groups))
        return next(iter(self.panel._canvas_groups))

    def test_group_and_dissolve_are_real_key_actions_and_undoable(self):
        group_id = self.make_group()
        self.select([group_id])
        self.key(Qt.Key.Key_G, CTRL | SHIFT)
        self.assertFalse(self.panel._canvas_groups)
        self.assertTrue(self.panel.undo_canvas_action())
        self.assertIn(group_id, self.panel._canvas_groups)

    def test_move_in_and_out_keys_preserve_other_members(self):
        group_id = self.make_group()
        self.select([self.ids[2]])
        with patch.object(canvas.QMenu, "exec", new=lambda menu, pos: menu.actions()[0]):
            self.key(Qt.Key.Key_M, CTRL)
        self.assertIn(self.ids[2], self.panel._canvas_groups[group_id].member_ids)
        self.select([self.ids[2]])
        self.key(Qt.Key.Key_M, CTRL | SHIFT)
        self.assertEqual(set(self.ids[:2]), set(self.panel._canvas_groups[group_id].member_ids))

    def test_control_space_arranges_without_starting_pan_and_old_key_is_free(self):
        self.panel._nodes[self.ids[1]].setPos(0, 100)
        self.select(self.ids[:2])
        with patch.object(self.panel, "auto_layout", wraps=self.panel.auto_layout) as arrange:
            self.key(Qt.Key.Key_L, CTRL)
            arrange.assert_not_called()
            self.key(Qt.Key.Key_Space, CTRL)
            arrange.assert_called_once()
        rects = sorted((self.panel._nodes[n].sceneBoundingRect() for n in self.ids[:2]), key=lambda r: r.left())
        self.assertGreater(rects[1].left(), rects[0].right())
        self.assertFalse(self.panel.view._space_down)
        with patch.object(self.panel, "edit_canvas_layout_spacing") as spacing:
            self.key(Qt.Key.Key_Space, CTRL | SHIFT)
            spacing.assert_called_once_with()

    def test_focus_and_return_keys_change_visibility(self):
        self.select([self.ids[0]])
        self.key(Qt.Key.Key_F)
        self.assertTrue(self.panel._focused_node_ids)
        self.assertFalse(self.panel._nodes[self.ids[3]].isVisible())
        self.key(Qt.Key.Key_F, SHIFT)
        self.assertFalse(self.panel._focused_node_ids)
        self.assertTrue(self.panel._nodes[self.ids[3]].isVisible())

    def test_creation_keys_use_same_node_defaults_as_menu(self):
        for key, kind, mode in (
                (Qt.Key.Key_1, "image_node", "text_to_image"),
                (Qt.Key.Key_2, "video_node", "generate_video"),
                (Qt.Key.Key_3, "audio_node", "text_to_speech"),
                (Qt.Key.Key_4, "text_node", "plain_text")):
            with self.subTest(kind=kind):
                self.canvas_focus()
                before = set(self.panel._nodes)
                self.key(key, CTRL)
                created = set(self.panel._nodes) - before
                self.assertEqual(1, len(created))
                node = self.panel._nodes[created.pop()]
                self.assertEqual(kind, node.node_type)
                self.assertEqual(mode, node.payload["beginner_mode"])

    def test_new_menu_and_import_export_shortcuts(self):
        with patch.object(self.panel, "show_new_asset_menu") as new:
            self.key(Qt.Key.Key_N, CTRL | SHIFT)
            self.assertEqual(1, new.call_count)
        with patch.object(self.panel, "import_canvas_media_dialog") as importing:
            self.key(Qt.Key.Key_I, CTRL)
            importing.assert_called_once_with()
        self.select(self.ids[:2])
        with patch.object(self.panel, "export_canvas_selection") as exporting:
            self.key(Qt.Key.Key_E, CTRL)
            exporting.assert_called_once_with()

    def test_import_dialog_accepts_mixed_media_and_cancel_creates_nothing(self):
        picture = self.root / "图片.png"
        pixmap = QPixmap(20, 20)
        pixmap.fill(Qt.GlobalColor.red)
        self.assertTrue(pixmap.save(str(picture)))
        video, audio = self.root / "视频.mov", self.root / "音频.wav"
        video.write_bytes(b"video")
        audio.write_bytes(b"audio")
        before = set(self.panel._nodes)
        with patch.object(canvas.QFileDialog, "getOpenFileNames", return_value=([], "")):
            self.key(Qt.Key.Key_I, CTRL)
        self.assertEqual(before, set(self.panel._nodes))
        with patch.object(canvas.QFileDialog, "getOpenFileNames", return_value=([str(picture), str(video), str(audio)], "")), \
                patch.object(self.panel, "_extract_video_review_frames", return_value=[]), \
                patch("ui.media_library._get_duration", return_value=2):
            self.key(Qt.Key.Key_I, CTRL)
        self.assertEqual({"image_node", "video_node", "audio_node"},
                         {self.panel._nodes[n].node_type for n in set(self.panel._nodes) - before})

    def test_toolbar_focus_and_help_show_shortcuts(self):
        self.panel.arrange_button.setFocus()
        with patch.object(self.panel, "auto_layout") as arrange:
            self.key(Qt.Key.Key_Space, CTRL, self.panel.arrange_button)
            arrange.assert_called_once_with()
        labels = []
        def inspect_menu(menu, pos):
            labels.extend(action.text() for action in menu.actions())
            return None
        with patch.object(canvas.QMenu, "exec", new=inspect_menu):
            self.key(Qt.Key.Key_F1, target=self.panel.arrange_button)
        self.assertIn("横向整理\tCtrl+空格", labels)
        self.assertIn("导入素材\tCtrl+I", labels)
        self.assertIn("新建图片节点\tCtrl+1", labels)

    def test_typing_and_input_controls_do_not_trigger_canvas_actions(self):
        for widget in (QLineEdit(self.panel), QTextEdit(self.panel),
                       QComboBox(self.panel), QDoubleSpinBox(self.panel)):
            widget.show()
            widget.setFocus()
            self.app.processEvents()
            with patch.object(self.panel, "auto_layout") as arrange, \
                    patch.object(self.panel, "focus_selected_canvas_branch") as focus, \
                    patch.object(self.panel, "create_canvas_group") as group, \
                    patch.object(self.panel, "_create_shortcut_node") as create:
                self.key(Qt.Key.Key_F, target=widget)
                self.key(Qt.Key.Key_G, CTRL, widget)
                self.key(Qt.Key.Key_1, CTRL, widget)
                self.key(Qt.Key.Key_Space, CTRL, widget)
                arrange.assert_not_called()
                focus.assert_not_called()
                group.assert_not_called()
                create.assert_not_called()
            if isinstance(widget, QLineEdit):
                self.assertEqual("f", widget.text())
            if isinstance(widget, QTextEdit):
                self.assertEqual("f", widget.toPlainText())
            widget.hide()
            widget.deleteLater()

    def test_embedded_prompt_receives_f_instead_of_focusing_branch(self):
        self.select([self.ids[0]])
        self.panel.show_inline_editor(self.panel._nodes[self.ids[0]])
        editor = self.panel._inline_text_editor
        editor.setFocus()
        self.app.processEvents()
        with patch.object(self.panel, "focus_selected_canvas_branch") as focus, \
                patch.object(self.panel, "auto_layout") as arrange:
            self.key(Qt.Key.Key_F, target=editor)
            self.key(Qt.Key.Key_Space, CTRL, editor)
            focus.assert_not_called()
            arrange.assert_not_called()
        self.assertIn("f", editor.toPlainText())

    def test_auto_repeat_and_other_windows_do_not_trigger_actions(self):
        repeat = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_G, CTRL, "", True)
        with patch.object(self.panel, "create_canvas_group") as group:
            self.app.sendEvent(self.panel.view.viewport(), repeat)
            self.app.processEvents()
            group.assert_not_called()
        other = QWidget()
        other.show()
        other.activateWindow()
        other.setFocus()
        self.app.processEvents()
        with patch.object(self.panel, "auto_layout") as arrange:
            self.key(Qt.Key.Key_Space, CTRL, other)
            arrange.assert_not_called()
        other.close()
        other.deleteLater()

    def test_shortcuts_detach_when_hidden_and_restore_when_shown(self):
        self.assertTrue(self.panel._canvas_shortcuts_installed)
        self.panel.hide()
        self.assertFalse(self.panel._canvas_shortcuts_installed)
        self.app.processEvents()
        self.panel.show()
        self.panel.activateWindow()
        self.assertTrue(QTest.qWaitForWindowActive(self.panel, 1000))
        self.canvas_focus()
        # Offscreen has no window manager to complete native reactivation.
        self.app.setActiveWindow(self.panel)
        self.panel.view.setFocus()
        self.assertTrue(self.panel._canvas_shortcuts_installed)
        self.assertTrue(self.panel._canvas_shortcuts_allowed())
        with patch.object(self.panel, "auto_layout") as arrange:
            self.key(Qt.Key.Key_Space, CTRL)
            arrange.assert_called_once_with()
