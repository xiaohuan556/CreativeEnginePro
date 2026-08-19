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

    def test_dock_exposes_import_without_renaming_asset_contract(self):
        panel = self.make_panel()
        labels = [button.text() for button in
                  panel.create_dock.findChildren(canvas_module.QPushButton)]
        self.assertIn("⇧ 导入", labels)
        self.assertIn("▣ 资产", labels)
        panel.close()


if __name__ == "__main__":
    unittest.main()
