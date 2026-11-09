import json
import os
import unittest
from unittest.mock import patch
import tempfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
from PyQt6.QtWidgets import QApplication, QFileDialog
from PyQt6.QtCore import QPointF
from ui.image_editor import ImageEditorWidget, ImageLayer, numpy_from_qimage, qimage_from_numpy


class WorkbenchOpsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = ImageEditorWidget()
        self.editor.new_project(24, 16)
        self.layer = ImageLayer('素材', np.full((4, 6, 4), 180, dtype=np.uint8), 6, 4)
        self.layer.x, self.layer.y = 12, 8
        self.editor.project.add_layer(self.layer)
        self.editor.set_active(self.layer)

    def tearDown(self):
        self.editor.close()
        self.editor.deleteLater()
        self.app.processEvents()

    def test_copy_layers_keeps_editable_properties_and_independent_pixels(self):
        self.layer.rotation = 37
        self.layer.mask = np.full((16, 24), 200, dtype=np.uint8)
        self.editor.copy_layers()
        self.assertTrue(self.editor.paste_layers())
        pasted = self.editor.active
        self.assertNotEqual(pasted.id, self.layer.id)
        self.assertEqual(pasted.rotation, 37)
        pasted.pixels.fill(0)
        self.assertTrue(self.layer.pixels.any())
        self.editor.undo()
        self.assertEqual(len(self.editor.project.layers), 1)

    def test_system_clipboard_change_prevents_stale_layer_paste(self):
        self.editor.copy_layers()
        QApplication.clipboard().setText('another application')
        self.assertFalse(self.editor.paste_layers())

    def test_selection_expand_contract_and_canvas_dimensions(self):
        self.editor.selection = np.zeros((16, 24), dtype=bool)
        self.editor.selection[5:10, 8:14] = True
        count = self.editor.selection.sum()
        self.editor.modify_selection(True, 1)
        self.assertGreater(self.editor.selection.sum(), count)
        self.editor.modify_selection(False, 2)
        self.assertLess(self.editor.selection.sum(), count)
        self.editor.select_canvas()
        self.assertEqual(self.editor.selection.shape, (16, 24))
        self.assertTrue(self.editor.selection.all())

    def test_mask_from_selection_inversion_and_undo(self):
        self.editor.select_canvas()
        self.editor.mask_operation('selection')
        self.assertTrue((self.layer.mask == 255).all())
        self.editor.mask_operation('invert')
        self.assertFalse(self.layer.mask.any())
        self.editor.undo()
        self.assertTrue((self.editor.active.mask == 255).all())

    def test_project_save_open_preserves_pixels_and_masks(self):
        from core.project_files import PIC_SUFFIX, read_project

        self.layer.mask = np.full((16, 24), 123, dtype=np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'project' + PIC_SUFFIX)
            with patch.object(QFileDialog, 'getSaveFileName', return_value=(os.path.join(directory, 'project'), '')):
                worker = self.editor.save_project()
                self.assertTrue(worker.wait(5000))
                self.app.processEvents()
            data = read_project(path, PIC_SUFFIX)
            self.assertGreaterEqual(data['format_version'], 2)
            self.editor.new_project(2, 2)
            with patch.object(QFileDialog, 'getOpenFileName', return_value=(path, '')):
                self.editor.open_project()
            np.testing.assert_array_equal(self.editor.active.mask, self.layer.mask)
            np.testing.assert_array_equal(self.editor.active.pixels, self.layer.pixels)
            legacy = os.path.join(directory, 'legacy.json')
            with open(legacy, 'w', encoding='utf-8') as stream:
                json.dump(data, stream)
            self.editor.new_project(2, 2)
            self.assertTrue(self.editor.open_project(legacy))
            np.testing.assert_array_equal(self.editor.active.pixels, self.layer.pixels)

    def test_apply_mask_preserves_visual_position_after_transform(self):
        self.layer.rotation = 25
        self.layer.scale = 1.4
        self.layer.opacity = .7
        self.layer.mask = np.full((16, 24), 200, dtype=np.uint8)
        before = numpy_from_qimage(self.editor._render_layer_canvas(self.layer))
        self.editor._apply_layer_mask(self.layer)
        after = numpy_from_qimage(self.editor._render_layer_canvas(self.layer))
        np.testing.assert_array_equal(before[:, :, 3], after[:, :, 3])
        visible = before[:, :, 3] > 0
        np.testing.assert_array_equal(before[visible], after[visible])

    def test_curve_and_extra_adjustments_render_non_square_images(self):
        image = qimage_from_numpy(np.full((16, 24, 4), 100, dtype=np.uint8))
        for kind in ('curves', 'exposure', 'black_white', 'invert', 'vignette', 'rgb_split', 'scanlines'):
            layer = ImageLayer('调整', kind='adjust')
            layer.adjust = {'type': kind, 'midtones': 20}
            rendered = numpy_from_qimage(self.editor._apply_adjustment(image, layer))
            self.assertEqual(rendered.shape, (16, 24, 4))
            np.testing.assert_array_equal(rendered[:, :, 3], 100)

    def test_adjustment_mask_limits_effect(self):
        image = qimage_from_numpy(np.full((16, 24, 4), 100, dtype=np.uint8))
        self.layer.adjust = {'type': 'invert'}
        self.layer.mask = np.zeros((16, 24), dtype=np.uint8)
        result = numpy_from_qimage(self.editor._apply_adjustment(image, self.layer))
        self.assertTrue((result[:, :, :3] == 100).all())

    def test_repeat_filter_is_undoable(self):
        self.editor._last_filter = ('invert', {})
        self.editor.repeat_filter()
        self.assertEqual(int(self.layer.pixels[0, 0, 0]), 75)
        self.editor.undo()
        self.assertEqual(int(self.editor.active.pixels[0, 0, 0]), 180)

    def test_soft_brush_respects_opacity_and_lock(self):
        self.layer.pixels.fill(0)
        self.editor.brush_size = 4
        self.editor.brush_hardness = 0
        self.editor.brush_opacity = 50
        self.editor.paint_stamp(QPointF(12, 8))
        self.assertEqual(int(self.layer.pixels[2, 3, 3]), 128)
        self.assertLess(int(self.layer.pixels[2, 4, 3]), 128)
        before = self.layer.pixels.copy()
        self.layer.locked = True
        self.editor.paint_stamp(QPointF(12, 8))
        np.testing.assert_array_equal(before, self.layer.pixels)

    def test_loaded_soft_selection_keeps_normalized_coverage(self):
        self.layer.mask = np.full((16, 24), 128, dtype=np.uint8)
        self.editor.load_layer_selection(mask=True)
        self.assertAlmostEqual(float(self.editor.sel_alpha[0, 0]), 128 / 255, places=6)
        self.editor.mask_operation('selection')
        self.assertEqual(int(self.layer.mask[0, 0]), 128)

    def test_trim_keeps_layer_editable_and_undo_restores_canvas(self):
        self.layer.pixels[:, :, 3] = 255
        self.editor.trim_transparent()
        self.assertEqual((self.editor.project.w, self.editor.project.h), (6, 4))
        self.assertEqual(self.layer.kind, 'image')
        self.assertEqual((self.layer.x, self.layer.y), (3, 2))
        self.editor.undo()
        self.assertEqual((self.editor.project.w, self.editor.project.h), (24, 16))


if __name__ == '__main__':
    unittest.main()
