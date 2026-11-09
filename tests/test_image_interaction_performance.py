import os
import unittest
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
from PyQt6.QtCore import Qt, QPoint, QPointF, QRectF
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
from ui.image_editor import ImageEditorWidget, ImageLayer, Tool, numpy_from_qimage


class InteractionRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = ImageEditorWidget()
        self.editor.new_project(160, 120)
        self.editor.resize(1400, 900)
        self.editor.show()
        self.editor.smart_guides_on = False
        self.layers = []
        for name, color, width, height, x, y in (
                ('back', [40, 100, 180, 255], 160, 120, 80, 60),
                ('moving', [240, 90, 20, 180], 70, 50, 65, 55),
                ('front', [20, 220, 80, 150], 50, 40, 95, 65)):
            pixels = np.empty((height, width, 4), np.uint8)
            pixels[:] = color
            layer = ImageLayer(name, pixels, width, height)
            layer.x, layer.y = x, y
            self.editor.project.add_layer(layer)
            self.layers.append(layer)
        self.layer = self.layers[1]
        self.editor.set_active(self.layer)
        self.editor._redraw()
        self.app.processEvents()

    def tearDown(self):
        self.editor._clear_interaction_preview()
        self.editor.close()
        self.editor.deleteLater()
        self.app.processEvents()

    def begin(self):
        self.editor.view._start = QPointF(30, 30)
        self.editor._begin_move(self.editor.view._start)

    def scene_image(self):
        image = QImage(160, 120, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        self.editor.view.scene.render(painter, QRectF(0, 0, 160, 120), QRectF(0, 0, 160, 120))
        painter.end()
        return image

    def test_pointer_moves_reuse_static_layers_and_compose_once_on_release(self):
        original = self.layer.pixels.copy()
        self.begin()
        with patch.object(self.editor, '_render_composite', wraps=self.editor._render_composite) as compose:
            for x in range(35, 60, 5):
                self.editor._move_to(QPointF(x, 40))
            self.assertEqual(compose.call_count, 0)
            self.assertFalse(self.editor.view.composite_item.isVisible())
            self.editor._end_move()
            self.assertEqual(compose.call_count, 1)
        self.assertTrue(self.editor.view.composite_item.isVisible())
        self.assertIsNone(self.editor._interaction_preview)
        np.testing.assert_array_equal(self.layer.pixels, original)
        expected = numpy_from_qimage(self.editor._render_composite(False))
        actual = numpy_from_qimage(self.editor.view.composite_item.pixmap().toImage())
        np.testing.assert_array_equal(actual, expected)
        self.editor.undo()
        self.assertEqual((self.editor.active.x, self.editor.active.y), (65, 55))

    def test_preview_preserves_transparency_overlap_and_document_clipping(self):
        self.layer.opacity = .6
        self.begin()
        self.editor._move_to(QPointF(85, 72))
        actual = numpy_from_qimage(self.scene_image()).astype(int)
        expected = numpy_from_qimage(self.editor._render_composite(False)).astype(int)
        self.assertLessEqual(np.abs(actual-expected).max(), 2)
        self.assertTrue(self.editor._render_composite(True).pixelColor(140, 100).isValid())

    def test_handle_transform_reuses_source_without_compositing_each_step(self):
        self.editor.view._handle_drag = 'se'
        with patch.object(self.editor, '_render_composite', wraps=self.editor._render_composite) as compose:
            for scale in (1.1, 1.4, 1.8):
                self.layer.scale = scale
                self.layer.rotation = 20
                self.editor._redraw()
            self.assertEqual(compose.call_count, 0)
        preview = self.editor._interaction_preview
        self.assertEqual(preview['moving'].transform(), self.layer.canvas_transform())
        self.editor.view._handle_drag = None
        self.editor._redraw()
        self.assertIsNone(self.editor._interaction_preview)

    def test_dependent_adjustments_and_blends_keep_exact_compositor(self):
        self.layers[2].blend = 'multiply'
        self.begin()
        with patch.object(self.editor, '_render_composite', wraps=self.editor._render_composite) as compose:
            self.editor._move_to(QPointF(40, 40))
            self.assertEqual(compose.call_count, 1)
        self.assertIsNone(getattr(self.editor, '_interaction_preview', None))

    def test_actual_resize_release_clears_preview_and_keeps_full_quality(self):
        self.editor.set_tool(Tool.MOVE)
        self.editor.view.resetTransform()
        self.editor.view.centerOn(80, 60)
        self.editor._show_handles = True
        self.app.processEvents()
        box = self.editor._layer_bbox(self.layer)
        start = self.editor.view.mapFromScene(box.bottomRight())
        end = start + QPoint(30, 20)
        surface = self.editor.view.viewport()
        before = self.layer.scale
        QTest.mousePress(surface, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(surface, end, delay=5)
        self.assertIsNotNone(self.editor._interaction_preview)
        QTest.mouseRelease(surface, Qt.MouseButton.LeftButton, pos=end)
        self.app.processEvents()
        self.assertIsNone(self.editor._interaction_preview)
        self.assertGreater(self.layer.scale, before)
        np.testing.assert_array_equal(
            numpy_from_qimage(self.editor.view.composite_item.pixmap().toImage()),
            numpy_from_qimage(self.editor._render_composite(False)))

    def test_linked_mask_applied_before_single_redraw(self):
        self.layer.mask = np.zeros((120, 160), np.uint8)
        self.layer.mask[25:70, 35:90] = 255
        original_mask = self.layer.mask.copy()
        self.begin()
        with patch.object(self.editor, '_redraw', wraps=self.editor._redraw) as redraw:
            self.editor._move_to(QPointF(40, 40))
            self.assertEqual(redraw.call_count, 1)
        np.testing.assert_array_equal(self.layer.mask[10:, 10:], original_mask[:-10, :-10])

    def test_normal_layers_skip_numpy_canvas_roundtrip(self):
        with patch('ui.image_editor.numpy_from_qimage', side_effect=AssertionError('unneeded pixel copy')):
            self.editor._render_layer_canvas(self.layer)

    def test_checker_pattern_keeps_original_colors(self):
        image = QImage(25, 25, QImage.Format.Format_ARGB32)
        painter = QPainter(image)
        self.editor._paint_checker(painter, 25, 25)
        painter.end()
        for x, y, value in ((0, 0, 58), (12, 0, 40), (0, 12, 40), (12, 12, 58), (24, 24, 58)):
            self.assertEqual(image.pixelColor(x, y).red(), value)

    def test_hidden_navigator_does_not_generate_thumbnail(self):
        self.editor.view.resetTransform()
        self.editor.view._update_zoom_label()
        navigator = self.editor.view.navigator
        with patch.object(navigator, '_update_thumbnail', wraps=navigator._update_thumbnail) as thumbnail:
            self.editor._redraw()
            QTest.qWait(130)
            self.assertEqual(thumbnail.call_count, 0)
        self.assertTrue(navigator._thumbnail_dirty)
        self.editor.view.zoom_by(4)
        self.app.processEvents()
        self.assertFalse(navigator.isHidden())
        self.assertFalse(navigator.picture.isNull())

    def test_high_zoom_stays_idle_and_does_not_recompose(self):
        self.editor.view.resetTransform()
        self.editor.view.zoom_by(4)
        self.app.processEvents()
        QTest.qWait(150)
        with patch.object(self.editor.view.navigator, 'paintEvent', wraps=self.editor.view.navigator.paintEvent) as paint:
            QTest.qWait(180)
            self.assertEqual(paint.call_count, 0)
        with patch.object(self.editor, '_render_composite', wraps=self.editor._render_composite) as compose:
            self.editor.view.zoom_by(1.15)
            self.editor.view.zoom_by(1/1.15)
            self.app.processEvents()
            self.assertEqual(compose.call_count, 0)

    def test_zoom_is_bounded_at_extreme_input(self):
        self.editor.view.resetTransform()
        for _ in range(100):
            self.editor.view.zoom_by(10)
        self.assertAlmostEqual(self.editor.view.transform().m11(), 32)
        for _ in range(100):
            self.editor.view.zoom_by(.01)
        self.assertAlmostEqual(self.editor.view.transform().m11(), .01)


if __name__ == '__main__':
    unittest.main()
