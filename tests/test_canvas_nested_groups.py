import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CEP_DATA_DIR", tempfile.mkdtemp(prefix="cep_groups_"))
from PyQt6.QtCore import Qt, QPoint, QPointF, QEvent
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPixmap
from PyQt6.QtTest import QTest
import ai.ui.production_canvas as canvas
from ai.canvas_groups import descendants, normalize_groups
from ai.canvas_selection import selected_project
from ai.web_canvas_bridge import desktop_canvas_to_web, web_canvas_to_desktop


class NestedCanvasGroupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.root = Path(tempfile.mkdtemp(prefix="cep_nested_groups_"))

    def setUp(self):
        canvas.LAYOUT_FILE = self.root / (self._testMethodName + ".json")
        self.panel = canvas.ProductionCanvasTab()
        self.panel._checkpoint_timer.stop()
        self.panel._task_timer.stop()
        self.panel._layout_timer.stop()
        self.panel._layout_timer.timeout.disconnect()
        self.panel._save_layout_now = lambda *a, **k: None
        self.ids = [self.panel.create_custom_node("text_node", QPointF(i * 320, 100), {"content": f"节点{i}"}) for i in range(4)]

    def tearDown(self):
        self.panel.close()
        self.app.processEvents()
        self.panel.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def select(self, ids):
        self.panel._defer_inline_editor_until_pointer_release = True
        self.panel.scene.clearSelection()
        for item_id in ids:
            item = self.panel._nodes.get(item_id) or self.panel._canvas_groups.get(item_id)
            item.setSelected(True)
        self.panel._defer_inline_editor_until_pointer_release = False

    def create_group(self, ids, name):
        before = set(self.panel._canvas_groups)
        self.select(ids)
        with patch.object(canvas.QInputDialog, "getText", return_value=(name, True)) as prompt:
            self.assertTrue(self.panel.create_canvas_group())
        self.assertEqual(1, prompt.call_count)
        return (set(self.panel._canvas_groups) - before).pop()

    def nested(self):
        outer = self.create_group(self.ids, "外层")
        inner = self.create_group(self.ids[:2], "内层")
        return outer, inner

    def test_nested_creation_is_undoable_and_only_asks_for_one_name(self):
        outer, inner = self.nested()
        self.assertIn(inner, self.panel._canvas_groups[outer].member_ids)
        self.assertEqual(set(self.ids), set(self.panel._canvas_groups[outer].leaf_ids))
        self.assertTrue(self.panel.undo_canvas_action())
        self.assertNotIn(inner, self.panel._canvas_groups)
        self.assertEqual(set(self.ids), set(self.panel._canvas_groups[outer].member_ids))
        self.assertTrue(self.panel.redo_canvas_move())
        self.assertIn(inner, self.panel._canvas_groups)
        self.assertTrue(self.panel._canvas_groups[outer].sceneBoundingRect().contains(
            self.panel._canvas_groups[inner].sceneBoundingRect()))

    def test_rename_collapse_and_dissolve_can_be_undone(self):
        outer, inner = self.nested()
        with patch.object(canvas.QInputDialog, "getText", return_value=("新名称", True)) as prompt:
            self.panel.edit_canvas_group(inner)
        self.assertEqual(1, prompt.call_count)
        self.panel.undo_canvas_action()
        self.assertEqual("内层", self.panel._canvas_groups[inner].record["group_name"])
        self.panel.toggle_canvas_group(inner)
        self.panel.toggle_canvas_group(outer)
        self.assertFalse(self.panel._canvas_groups[inner].isVisible())
        self.assertTrue(all(not self.panel._nodes[item].isVisible() for item in self.ids))
        self.panel.undo_canvas_action()
        self.assertTrue(self.panel._canvas_groups[inner].isVisible())
        self.assertFalse(self.panel._nodes[self.ids[0]].isVisible())
        self.assertTrue(self.panel._nodes[self.ids[2]].isVisible())
        self.panel.dissolve_canvas_group(inner)
        self.assertNotIn(inner, self.panel._canvas_groups)
        self.assertEqual(set(self.ids), set(self.panel._canvas_groups[outer].member_ids))
        self.panel.undo_canvas_action()
        self.assertIn(inner, self.panel._canvas_groups)

    def test_selected_whole_group_can_be_wrapped_again(self):
        outer, inner = self.nested()
        wrapper = self.create_group([outer], "再一层")
        self.assertEqual([outer], self.panel._canvas_groups[wrapper].member_ids)
        self.assertIn(inner, self.panel._group_descendant_ids(wrapper))
        self.assertEqual(set(self.ids), set(self.panel._canvas_groups[wrapper].leaf_ids))

    def test_copy_and_export_preserve_hierarchy_and_undo_copy(self):
        outer, inner = self.nested()
        self.panel._positions()["__workflow_edges__"] = [{"id": "original-edge", "source": self.ids[0], "target": self.ids[1], "type": "text_source"}]
        self.assertTrue(self.panel.duplicate_canvas_group(outer))
        self.assertEqual(8, len(self.panel._nodes))
        self.assertEqual(4, len(self.panel._canvas_groups))
        clone = next(group for group in self.panel._canvas_groups.values() if group.record["group_name"] == "外层 副本")
        self.assertEqual(4, len(clone.leaf_ids))
        self.assertTrue(set(clone.leaf_ids).isdisjoint(self.ids))
        edges = self.panel._positions()["__workflow_edges__"]
        self.assertEqual(2, len({row["id"] for row in edges}))
        exported = selected_project(self.panel.current_storyboard(), self.panel._positions(), set(clone.leaf_ids))
        self.assertEqual(2, len(exported["canvas"]["__canvas_groups__"]))
        document = desktop_canvas_to_web(exported["storyboard"], exported["canvas"])
        self.assertEqual("creative-engine-production-project", document["format"])
        imported = web_canvas_to_desktop(document)
        group_records = [row for row in imported["canvas"]["__custom_nodes__"] if row.get("canvas_group")]
        self.assertEqual(2, len(group_records))
        self.panel.undo_canvas_action()
        self.assertEqual(set(self.ids), set(self.panel._nodes))
        self.assertEqual({outer, inner}, set(self.panel._canvas_groups))

    def test_outer_group_drag_moves_all_descendants_then_undo_moves_only(self):
        outer, inner = self.nested()
        self.panel.resize(1400, 1000)
        self.panel.show()
        self.panel.view.set_zoom(.7, keep_center=True)
        group = self.panel._canvas_groups[outer]
        self.panel.view.centerOn(group.sceneBoundingRect().center())
        self.app.processEvents()
        before = {item: QPointF(self.panel._nodes[item].pos()) for item in self.ids}
        point = self.panel.view.mapFromScene(group.mapToScene(QPointF(100, 20)))
        QTest.mousePress(self.panel.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
        QTest.mouseMove(self.panel.view.viewport(), point + QPoint(35, 28))
        QTest.mouseRelease(self.panel.view.viewport(), Qt.MouseButton.LeftButton, pos=point + QPoint(35, 28))
        self.app.processEvents()
        delta = self.panel._nodes[self.ids[0]].pos() - before[self.ids[0]]
        self.assertGreater(abs(delta.x()) + abs(delta.y()), 1)
        for item in self.ids:
            self.assertEqual(delta, self.panel._nodes[item].pos() - before[item])
        self.panel.undo_canvas_action()
        self.assertIn(inner, self.panel._canvas_groups)
        for item in self.ids:
            self.assertEqual(before[item], self.panel._nodes[item].pos())

    def test_select_all_arranges_mixed_size_cards_in_one_horizontal_row(self):
        image = self.root / "wide-layout.png"
        pixmap = QPixmap(800, 400)
        pixmap.fill(Qt.GlobalColor.blue)
        pixmap.save(str(image))
        image_id = self.panel.create_custom_node("image_node", QPointF(0, 0), {"path": str(image)})
        for node in self.panel._nodes.values():
            node.setPos(QPointF(20, 20))
        before = {item: QPointF(node.pos()) for item, node in self.panel._nodes.items()}
        self.panel.select_all_canvas_nodes()
        self.panel.auto_layout()
        bounds = sorted((node.sceneBoundingRect() for node in self.panel._nodes.values()), key=lambda rect: rect.left())
        self.assertGreater(self.panel._nodes[image_id].width, 390)
        self.assertEqual(1, len({round(rect.top(), 2) for rect in bounds}))
        for left, right in zip(bounds, bounds[1:]):
            self.assertGreaterEqual(right.left() - left.right(), 79.9)
        self.panel.undo_canvas_action()
        for item in before:
            self.assertEqual(before[item], self.panel._nodes[item].pos())

    def test_group_layout_respects_spacing_and_leaves_other_nodes_in_place(self):
        group_id = self.create_group(self.ids[:2], "整理测试")
        self.panel._nodes[self.ids[1]].setPos(self.panel._nodes[self.ids[0]].pos())
        outside = {item: QPointF(self.panel._nodes[item].pos()) for item in self.ids[2:]}
        self.panel._positions()["__layout_settings__"] = {"node_gap": 160, "group_gap": 220}
        self.select([group_id])
        self.panel.auto_layout()
        bounds = sorted((self.panel._nodes[item].sceneBoundingRect() for item in self.ids[:2]), key=lambda rect: rect.left())
        self.assertAlmostEqual(160, bounds[1].left() - bounds[0].right(), places=2)
        for item in outside:
            self.assertEqual(outside[item], self.panel._nodes[item].pos())
        self.panel.undo_canvas_action()
        self.assertEqual(self.panel._nodes[self.ids[0]].pos(), self.panel._nodes[self.ids[1]].pos())

    def test_spacing_dialog_applies_to_the_target_group_and_persists(self):
        group_id = self.create_group(self.ids[:2], "间距设置")
        def configure(dialog):
            dialog.findChild(canvas.QDoubleSpinBox, "node_gap").setValue(200)
            dialog.findChild(canvas.QDoubleSpinBox, "group_gap").setValue(260)
            return int(canvas.QDialog.DialogCode.Accepted)
        with patch.object(canvas.QDialog, "exec", new=configure):
            self.panel.edit_canvas_layout_spacing(group_id)
        self.assertEqual({"node_gap": 200, "group_gap": 260}, self.panel._positions()["__layout_settings__"])
        bounds = sorted((self.panel._nodes[item].sceneBoundingRect() for item in self.ids[:2]), key=lambda rect: rect.left())
        self.assertAlmostEqual(200, bounds[1].left() - bounds[0].right(), places=2)
        self.panel.refresh()
        self.assertEqual(200, self.panel._positions()["__layout_settings__"]["node_gap"])

    def test_nested_group_horizontal_layout_and_collapsed_all_selection(self):
        outer, inner = self.nested()
        for node in self.panel._nodes.values():
            node.setPos(QPointF(0, 0))
        self.panel.select_all_canvas_nodes()
        self.panel.auto_layout()
        self.assertIn(inner, self.panel._canvas_groups[outer].member_ids)
        bounds = [node.sceneBoundingRect() for node in self.panel._nodes.values()]
        for index, first in enumerate(bounds):
            self.assertTrue(all(not first.intersects(second) for second in bounds[index + 1:]))
        snapshot = {item: QPointF(node.pos()) for item, node in self.panel._nodes.items()}
        self.panel.auto_layout()
        self.assertEqual(snapshot, {item: node.pos() for item, node in self.panel._nodes.items()})
        self.panel.toggle_canvas_group(outer)
        self.panel.select_all_canvas_nodes()
        self.assertIn(self.panel._canvas_groups[outer], self.panel.scene.selectedItems())
        self.panel.auto_layout()
        self.assertTrue(self.panel._canvas_groups[outer].collapsed)

    def test_move_into_out_of_group_and_dissolve_toolbar_are_undoable(self):
        group_id = self.create_group(self.ids[:2], "现有组")
        self.assertTrue(self.panel.move_canvas_items_to_group([self.ids[2]], group_id))
        self.assertIn(self.ids[2], self.panel._canvas_groups[group_id].leaf_ids)
        self.panel.undo_canvas_action()
        self.assertNotIn(self.ids[2], self.panel._canvas_groups[group_id].leaf_ids)
        self.assertTrue(self.panel.move_canvas_items_to_group([self.ids[0]], ""))
        self.assertIsNone(self.panel._canvas_group_for_member(self.ids[0]))
        self.panel.undo_canvas_action()
        self.select([group_id])
        self.assertFalse(self.panel.selection_dissolve_group_button.isHidden())
        self.panel.selection_dissolve_group_button.click()
        self.assertNotIn(group_id, self.panel._canvas_groups)
        self.assertEqual(set(self.ids), set(self.panel._nodes))
        self.panel.undo_canvas_action()
        self.assertIn(group_id, self.panel._canvas_groups)

    def test_drag_material_into_group_is_one_undo_step(self):
        group_id = self.create_group(self.ids[:2], "拖入组")
        group = self.panel._canvas_groups[group_id]
        group.record.update(manual_width=1100, manual_height=500)
        self.panel._sync_canvas_group_geometry()
        moving_id = self.ids[2]
        self.panel._nodes[moving_id].setPos(QPointF(1450, 400))
        self.panel._nodes[self.ids[3]].setPos(QPointF(1900, 700))
        before = QPointF(self.panel._nodes[moving_id].pos())
        self.panel.resize(1500, 1000)
        self.panel.show()
        self.select([moving_id])
        self.panel.view.set_zoom(.5, keep_center=True)
        self.panel.view.centerOn(800, 300)
        self.app.processEvents()
        start = self.panel.view.mapFromScene(self.panel._nodes[moving_id].sceneBoundingRect().center())
        end = self.panel.view.mapFromScene(group.mapToScene(QPointF(850, 350)))
        QTest.mousePress(self.panel.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(self.panel.view.viewport(), end)
        QTest.mouseRelease(self.panel.view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        self.app.processEvents()
        self.assertIn(moving_id, self.panel._canvas_groups[group_id].leaf_ids)
        self.panel.undo_canvas_action()
        self.assertNotIn(moving_id, self.panel._canvas_groups[group_id].leaf_ids)
        self.assertEqual(before, self.panel._nodes[moving_id].pos())

    def test_resize_group_corner_preserves_nodes_and_can_be_undone(self):
        group_id = self.create_group(self.ids[:2], "调整大小")
        self.panel.resize(1400, 1000)
        self.panel.show()
        self.panel.view.set_zoom(.7, keep_center=True)
        group = self.panel._canvas_groups[group_id]
        self.panel.view.centerOn(group.sceneBoundingRect().center())
        self.app.processEvents()
        before = group.boundingRect().size()
        positions = {item: QPointF(self.panel._nodes[item].pos()) for item in self.ids}
        point = self.panel.view.mapFromScene(group.mapToScene(group.boundingRect().bottomRight() - QPointF(10, 10)))
        QTest.mousePress(self.panel.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
        QTest.mouseMove(self.panel.view.viewport(), point + QPoint(70, 56))
        QTest.mouseRelease(self.panel.view.viewport(), Qt.MouseButton.LeftButton, pos=point + QPoint(70, 56))
        self.app.processEvents()
        self.assertGreater(group.boundingRect().width(), before.width() + 50)
        self.assertGreater(group.boundingRect().height(), before.height() + 30)
        self.assertEqual(positions, {item: self.panel._nodes[item].pos() for item in self.ids})
        self.panel.undo_canvas_action()
        self.assertEqual(before, self.panel._canvas_groups[group_id].boundingRect().size())


def test_group_normalization_rejects_cycles_and_duplicate_ownership():
    rows = normalize_groups([
        {"id": "outer", "group_nodes": ["inner", "a"]},
        {"id": "inner", "group_nodes": ["outer", "b", "a"]},
    ], {"a", "b"})
    assert descendants("outer", rows) == {"inner", "a", "b"}
    assert descendants("inner", rows) == {"b"}
