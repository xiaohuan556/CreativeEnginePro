import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QListWidget, QListWidgetItem
from ui.image_editor import ImageEditorWidget, ImageLayer, Artboard, Tool
from ui.image_workbench_widgets import CommandSearchInput, command_match_score


class NavigationCropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = ImageEditorWidget()
        self.editor.new_project(120, 80)
        self.layer = ImageLayer('测试素材', np.full((80, 120, 4), 255, np.uint8), 120, 80)
        self.layer.x, self.layer.y = 60, 40
        self.editor.project.add_layer(self.layer)
        self.editor.set_active(self.layer)
        self.editor._refresh_layers()

    def tearDown(self):
        self.editor.close()
        self.editor.deleteLater()
        self.app.processEvents()

    def ratio(self, label):
        self.editor._crop_ratio.setCurrentText(label)

    def test_ratio_stays_inside_canvas_near_edge(self):
        self.ratio('9:16')
        rect = self.editor.crop_geometry(QPointF(10, 10), QPointF(110, 75))
        self.assertAlmostEqual(rect.width() / rect.height(), 9 / 16)
        self.assertTrue(QRectF(0, 0, 120, 80).contains(rect))

    def test_symmetric_crop_grows_from_center_in_all_directions(self):
        self.ratio('3:4')
        for x, y in ((100, 70), (10, 10), (10, 70), (100, 10)):
            rect = self.editor.crop_geometry(QPointF(60, 40), QPointF(x, y), True)
            self.assertEqual(rect.center(), QPointF(60, 40))
            self.assertAlmostEqual(rect.width() / rect.height(), 3 / 4)
            self.assertTrue(QRectF(0, 0, 120, 80).contains(rect))

    def test_crop_keeps_original_pixels_and_is_undoable(self):
        pixels = self.layer.pixels.copy()
        self.layer.mask = np.full((80, 120), 170, np.uint8)
        self.editor._crop_rect = QRectF(20, 10, 60, 40)
        self.editor.apply_crop()
        self.assertEqual((self.editor.project.w, self.editor.project.h), (60, 40))
        self.assertEqual((self.layer.x, self.layer.y), (40, 30))
        np.testing.assert_array_equal(self.layer.pixels, pixels)
        self.assertEqual(self.layer.mask.shape, (40, 60))
        self.editor.undo()
        self.assertEqual((self.editor.project.w, self.editor.project.h), (120, 80))
        self.assertEqual(self.editor.active.mask.shape, (80, 120))
        self.editor.redo()
        self.assertEqual((self.editor.project.w, self.editor.project.h), (60, 40))

    def test_cropping_artboard_does_not_move_other_artboard(self):
        a = Artboard('A', 0, 0, 120, 80)
        b = Artboard('B', 200, 0, 120, 80)
        a.layers = [self.layer]
        self.editor.project.layers = []
        self.editor.project.artboards = [a, b]
        self.editor.active_artboard = a
        ox, oy = self.editor._ab_screen(a)
        self.editor._crop_rect = QRectF(ox + 20, oy + 10, 60, 40)
        self.editor.apply_crop()
        self.assertEqual((a.w, a.h), (60, 40))
        self.assertEqual((b.x, b.y, b.w, b.h), (200, 0, 120, 80))

    def test_crop_starts_from_selection_and_escape_does_not_change_document(self):
        self.editor.selection = np.zeros((80, 120), bool)
        self.editor.selection[10:40, 20:70] = True
        self.editor.set_tool(Tool.CROP)
        self.assertEqual(self.editor._crop_rect, QRectF(20, 10, 50, 30))
        QTest.keyClick(self.editor.view, Qt.Key.Key_Escape)
        self.assertIsNone(self.editor._crop_rect)
        self.assertEqual((self.editor.project.w, self.editor.project.h), (120, 80))

    def test_drag_preview_requires_confirmation(self):
        self.editor.resize(1000, 700)
        self.editor.show()
        self.app.processEvents()
        self.editor.set_tool(Tool.CROP)
        view = self.editor.view
        start = view.mapFromScene(QPointF(20, 20))
        end = view.mapFromScene(QPointF(80, 60))
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(view.viewport(), end)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        self.assertEqual(self.editor.project.w, 120)
        self.assertIsNotNone(self.editor._crop_rect)
        QTest.keyClick(view.viewport(), Qt.Key.Key_Return)
        self.assertAlmostEqual(self.editor.project.w, 60, delta=1)
        self.assertAlmostEqual(self.editor.project.h, 40, delta=1)

    def test_navigator_click_moves_view_without_editing(self):
        self.editor.resize(1000, 700)
        self.editor.show()
        self.app.processEvents()
        view = self.editor.view
        view.resetTransform()
        view.scale(8, 8)
        view.navigator.refresh()
        self.assertTrue(view.navigator.isVisible())
        nav = view.navigator
        rect = nav.image_rect()
        history_count = len(self.editor._history)
        target = QPointF(rect.x() + rect.width() * .8, rect.y() + rect.height() * .7)
        QTest.mouseClick(nav, Qt.MouseButton.LeftButton, pos=target.toPoint())
        center = view.mapToScene(view.viewport().rect().center())
        self.assertAlmostEqual(center.x(), 96, delta=1)
        self.assertAlmostEqual(center.y(), 56, delta=1)
        self.assertEqual(len(self.editor.project.layers), 1)
        self.assertEqual(len(self.editor._history), history_count)
        view.resetTransform()
        nav.refresh()
        self.assertFalse(nav.isVisible())

    def test_layer_filter_does_not_change_selection_and_survives_rebuild(self):
        text = ImageLayer('标题', kind='text')
        self.editor.project.add_layer(text)
        self.editor._refresh_layers()
        self.editor._layer_search.setText('标题')
        self.assertEqual(self.editor.layer_count.text(), '1 / 2')
        self.assertIs(self.editor.active, self.layer)
        self.editor._refresh_layers()
        self.assertEqual(self.editor.layer_count.text(), '1 / 2')
        self.editor._layer_kind.setCurrentText('图片')
        self.assertEqual(self.editor.layer_count.text(), '0 / 2')

    def test_lock_button_undo(self):
        widget = self.editor.layer_list.itemWidget(self.editor.layer_list.item(0))
        widget.lock_button.click()
        self.assertTrue(self.layer.locked)
        self.editor.undo()
        self.assertFalse(self.editor.active.locked)

    def test_command_search_arrows_skip_disabled_results(self):
        results = QListWidget()
        for label in ('one', 'disabled', 'three'):
            results.addItem(QListWidgetItem(label))
        results.item(1).setFlags(Qt.ItemFlag.NoItemFlags)
        search = CommandSearchInput(results)
        results.setCurrentRow(0)
        QTest.keyClick(search, Qt.Key.Key_Down)
        self.assertEqual(results.currentRow(), 2)
        QTest.keyClick(search, Qt.Key.Key_Up)
        self.assertEqual(results.currentRow(), 0)
        search.close()
        results.close()

    def test_search_ranks_complete_name_above_abbreviation(self):
        self.assertGreater(command_match_score('蒙版', '图层 › 蒙版'),
                           command_match_score('蒙版', '图层 › 从选区创建蒙版'))
        self.assertIsNotNone(command_match_score('gb', 'Filter › Gaussian Blur'))
        self.assertIsNone(command_match_score('abc', '图层 › 蒙版'))

    def test_restore_clears_stale_crop_and_mask_mode(self):
        snapshot = self.editor._snapshot()
        self.editor._crop_rect = QRectF(1, 1, 3, 3)
        self.editor._mask_edit = True
        self.editor._restore(snapshot)
        self.assertIsNone(self.editor._crop_rect)
        self.assertFalse(self.editor._mask_edit)

    def test_crop_roundtrip_preserves_off_canvas_pixels_and_new_masks(self):
        self.editor._crop_rect = QRectF(20, 10, 60, 40)
        self.editor.apply_crop()
        packed = self.editor.pack_project(self.editor._snapshot())
        self.editor._restore(self.editor.unpack_project(packed))
        self.assertEqual((self.editor.active.w, self.editor.active.h), (120, 80))
        self.assertEqual((self.editor.project.w, self.editor.project.h), (60, 40))


if __name__ == '__main__':
    unittest.main()
