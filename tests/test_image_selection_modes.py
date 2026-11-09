import os
import unittest
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import cv2
import numpy as np
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
from ui.image_editor import ImageEditorWidget, ImageLayer, Tool
from ui.image_selection_ops import combine_selection_masks


class SelectionModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = ImageEditorWidget()
        self.editor.new_project(120, 80)
        pixels = np.empty((80, 120, 4), np.uint8)
        pixels[:] = [230, 30, 40, 255]
        pixels[:, 40:80] = [20, 70, 210, 255]
        self.layer = ImageLayer('colors', pixels, 120, 80)
        self.layer.x, self.layer.y = 60, 40
        self.editor.project.add_layer(self.layer)
        self.editor.set_active(self.layer)
        self.editor.set_tool(Tool.WAND)
        self.editor.resize(1400, 900)
        self.editor.show()
        self.app.processEvents()
        self.editor.view.resetTransform()
        self.editor.view.centerOn(60, 40)
        self.editor.snap_on = False
        self.app.processEvents()

    def tearDown(self):
        self.editor.close()
        self.editor.deleteLater()
        self.app.processEvents()

    def click(self, x, modifiers=Qt.KeyboardModifier.NoModifier):
        position = self.editor.view.mapFromScene(QPointF(x, 40))
        QTest.mouseClick(self.editor.view.viewport(), Qt.MouseButton.LeftButton,
                         modifier=modifiers, pos=position)
        self.app.processEvents()

    def test_boolean_modes_and_empty_initial_selection(self):
        base = np.array([[1, 0], [1, 0]], bool)
        mask = np.array([[1, 1], [0, 0]], bool)
        for mode, expected in (
                ('new', mask), ('add', [[1, 1], [1, 0]]),
                ('sub', [[0, 0], [1, 0]]), ('intersect', [[1, 0], [0, 0]])):
            with self.subTest(mode=mode):
                np.testing.assert_array_equal(combine_selection_masks(base, mask, mode), expected)
                empty_expected = mask if mode in ('new', 'add') else np.zeros_like(mask)
                np.testing.assert_array_equal(combine_selection_masks(None, mask, mode), empty_expected)
        np.testing.assert_array_equal(base, [[1, 0], [1, 0]])

    def test_wand_four_buttons_act_on_real_mouse_clicks(self):
        self.click(20)
        self.assertEqual(self.editor.selection.sum(), 3200)
        self.editor._selection_buttons['add'].click()
        self.click(100)
        self.assertEqual(self.editor.selection.sum(), 6400)
        self.editor._selection_buttons['intersect'].click()
        self.click(100)
        self.assertEqual(self.editor.selection.sum(), 3200)
        self.assertFalse(self.editor.selection[:, :40].any())
        self.editor._selection_buttons['sub'].click()
        self.click(100)
        self.assertFalse(self.editor.selection.any())

    def test_temporary_modifier_modes_restore_button_choice(self):
        self.click(20)
        self.click(100, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.editor.selection.sum(), 6400)
        self.click(20, Qt.KeyboardModifier.AltModifier)
        self.assertEqual(self.editor.selection.sum(), 3200)
        self.click(20, Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier)
        self.assertFalse(self.editor.selection.any())
        self.assertEqual(self.editor.selection_mode, 'new')
        self.assertTrue(self.editor._selection_buttons['new'].isChecked())
        self.click(60)
        self.assertEqual(self.editor.selection.sum(), 3200)

    def test_mode_persists_across_selection_tools(self):
        self.editor.set_selection_mode('intersect')
        for tool in (Tool.SELECT_RECT, Tool.SELECT_ELLIPSE, Tool.SELECT_LASSO,
                     Tool.POLY_LASSO, Tool.QUICK_SELECT, Tool.OBJECT_SELECT, Tool.WAND):
            self.editor.set_tool(tool)
            self.assertFalse(self.editor._selection_controls.isHidden())
            self.assertEqual(self.editor.selection_mode_for_modifiers(Qt.KeyboardModifier.NoModifier), 'intersect')
        self.editor.set_tool(Tool.MOVE)
        self.assertTrue(self.editor._selection_controls.isHidden())

    def test_rectangular_intersection_preserves_only_overlap(self):
        self.editor._commit_rect_sel(QPointF(10, 10), QPointF(70, 60), Tool.SELECT_RECT, 'new')
        self.editor._commit_rect_sel(QPointF(40, 30), QPointF(100, 70), Tool.SELECT_RECT, 'intersect')
        expected = np.zeros((80, 120), bool)
        expected[30:60, 40:70] = True
        np.testing.assert_array_equal(self.editor.selection, expected)

    def test_polygon_mode_is_captured_at_first_vertex(self):
        self.editor.set_tool(Tool.POLY_LASSO)
        self.click(20, Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier)
        self.assertEqual(self.editor._sel_combine, 'intersect')
        self.click(60)
        self.assertEqual(self.editor._sel_combine, 'intersect')

    def test_quick_select_intersects_whole_stroke_with_original_mask(self):
        self.editor.selection = np.ones((80, 120), bool)
        self.editor._quick_select_begin(mode='intersect')
        self.editor._quick_select_point(QPointF(20, 40))
        self.assertEqual(self.editor.selection.sum(), 3200)
        self.editor._quick_select_point(QPointF(100, 40))
        # The continuous brush also sweeps the middle color between endpoints.
        self.assertEqual(self.editor.selection.sum(), 9600)
        self.editor._quick_select_end()
        np.testing.assert_array_equal(self.editor._sel_base, self.editor.selection)

    def test_quick_select_new_stroke_replaces_and_subtract_empty_stays_empty(self):
        self.editor.selection = np.ones((80, 120), bool)
        self.editor._quick_select_begin(mode='new')
        self.editor._quick_select_point(QPointF(20, 40))
        self.assertEqual(self.editor.selection.sum(), 3200)
        self.editor._quick_select_end()
        self.editor.selection = None
        self.editor._quick_select_begin(mode='sub')
        self.editor._quick_select_point(QPointF(20, 40))
        self.assertFalse(self.editor.selection.any())

    def test_contiguous_switch_selects_disconnected_matching_colors(self):
        self.click(20)
        self.assertEqual(self.editor.selection.sum(), 3200)
        self.editor.wand_contiguous = False
        self.click(20)
        self.assertEqual(self.editor.selection.sum(), 6400)

    def test_flood_uses_native_path_with_contiguous_rgb_pixels(self):
        native = cv2.floodFill
        seen = []
        def flood(pixels, *args):
            result = native(pixels, *args)
            seen.append(pixels.flags.c_contiguous)
            return result
        with patch('cv2.floodFill', side_effect=flood):
            mask = self.editor._flood(self.layer.pixels, 40, 20, 10)
        self.assertEqual(seen, [True])
        self.assertEqual(mask.sum(), 3200)

    def test_transparency_blocks_color_flood_and_ignores_hidden_rgb(self):
        pixels = np.full((10, 30, 4), 255, np.uint8)
        pixels[:, 10:20, 3] = 0
        pixels[:, 10:15, :3] = 0
        self.assertEqual(self.editor._flood(pixels, 5, 5, 10).sum(), 100)
        self.assertEqual(self.editor._flood(pixels, 5, 12, 10).sum(), 100)

    def test_sample_all_uses_visible_composite_instead_of_active_layer(self):
        top_pixels = np.full((80, 40, 4), 255, np.uint8)
        top = ImageLayer('front', top_pixels, 40, 80)
        top.x, top.y = 20, 40
        self.editor.project.add_layer(top)
        self.editor.wand_contiguous = False
        self.editor._wand(QPointF(20, 40), 10, 'new')
        self.assertEqual(self.editor.selection.sum(), 6400)
        self.editor.wand_sample_all = True
        self.editor._wand(QPointF(20, 40), 10, 'new')
        self.assertEqual(self.editor.selection.sum(), 3200)

    def test_intersection_outside_document_stays_empty(self):
        self.editor.selection = np.ones((80, 120), bool)
        self.editor._commit_rect_sel(QPointF(-50, -50), QPointF(-10, -10), Tool.SELECT_RECT, 'intersect')
        self.assertFalse(self.editor.selection.any())


if __name__ == '__main__':
    unittest.main()
