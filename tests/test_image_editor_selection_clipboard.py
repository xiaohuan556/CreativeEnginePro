import os
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QPointF, QRectF
    from PyQt6.QtWidgets import QApplication
    from ui.image_editor import (
        Artboard,
        ImageEditorWidget,
        ImageLayer,
        Tool,
        _extract_selected_rgba,
        _trim_transparent_rgba,
        numpy_from_qimage,
    )
    QT_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 runtime is not available")
class ImageEditorSelectionClipboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = ImageEditorWidget()
        self.editor.new_project(20, 20)

    def tearDown(self):
        self.editor.close()
        self.editor.deleteLater()
        self.app.processEvents()

    @staticmethod
    def _canvas_layer():
        pixels = np.zeros((20, 20, 4), dtype=np.uint8)
        pixels[6:10, 8:12] = (220, 30, 40, 255)
        layer = ImageLayer("透明素材", pixels=pixels, w=20, h=20, kind="image")
        layer.x = 10
        layer.y = 10
        return layer

    def test_selection_extraction_keeps_shape_and_real_alpha(self):
        pixels = np.full((10, 12, 4), 255, dtype=np.uint8)
        mask = np.zeros((10, 12), dtype=bool)
        mask[2:8, 3:10] = True
        mask[2, 3] = False

        selected, origin = _extract_selected_rgba(pixels, mask)

        self.assertEqual((3, 2), origin)
        self.assertEqual((6, 7, 4), selected.shape)
        self.assertEqual(0, int(selected[0, 0, 3]))
        self.assertEqual(255, int(selected[1, 1, 3]))

    def test_transparent_trim_uses_visible_pixel_bounds(self):
        pixels = np.zeros((10, 12, 4), dtype=np.uint8)
        pixels[3:7, 4:9] = (10, 20, 30, 128)

        trimmed, offset = _trim_transparent_rgba(pixels)

        self.assertEqual((4, 3), offset)
        self.assertEqual((4, 5, 4), trimmed.shape)
        self.assertTrue(np.all(trimmed[:, :, 3] == 128))

    def test_copy_and_paste_use_selected_pixels_and_original_position(self):
        layer = self._canvas_layer()
        self.editor.project.add_layer(layer)
        self.editor.set_active(layer)
        self.editor.selection = np.zeros((20, 20), dtype=bool)
        self.editor.selection[5:12, 7:14] = True

        self.assertTrue(self.editor.copy_selection())
        copied = numpy_from_qimage(QApplication.clipboard().image())
        self.assertEqual((7, 7, 4), copied.shape)
        self.assertEqual(0, int(copied[0, 0, 3]))
        self.assertEqual(255, int(copied[1, 1, 3]))

        self.editor.paste_image()
        pasted = self.editor.project.layers[-1]
        self.assertEqual(10.5, pasted.x)
        self.assertEqual(8.5, pasted.y)
        self.assertEqual(1.0, pasted.scale)
        self.assertTrue(np.array_equal(copied, pasted.pixels))

    def test_crop_does_not_bake_checkerboard_and_trims_transparent_edges(self):
        layer = self._canvas_layer()
        self.editor.project.add_layer(layer)
        self.editor.set_active(layer)

        self.editor._commit_crop(QRectF(0, 0, 20, 20))

        self.assertEqual((4, 4), (self.editor.project.w, self.editor.project.h))
        self.assertEqual((4, 4, 4), layer.pixels.shape)
        self.assertTrue(np.all(layer.pixels[:, :, 3] == 255))
        self.assertTrue(self.editor.project.transparent)

    def test_artboard_rectangle_selection_uses_artboard_local_coordinates(self):
        artboard = Artboard("画板 1", 50, 60, 40, 30)
        artboard.transparent = True
        self.editor.project.artboards = [artboard]
        self.editor.active_artboard = artboard

        self.editor._commit_rect_sel(
            QPointF(255, 267), QPointF(265, 277), Tool.SELECT_RECT, "new")

        self.assertEqual((30, 40), self.editor.selection.shape)
        self.assertTrue(self.editor.selection[7:17, 5:15].all())
        self.assertEqual(100, int(self.editor.selection.sum()))


if __name__ == "__main__":
    unittest.main()
