import copy
import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
from PyQt6.QtCore import Qt, QTimer, QPointF
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialogButtonBox, QSpinBox
from core.image_effects import curve_table, apply_curves, blur_rgba
from ui.image_editor import ImageEditorWidget, ImageLayer, numpy_from_qimage
from ui.image_workbench_widgets import CurveEditor


class EditableEffectsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = ImageEditorWidget()
        self.editor.new_project(24, 16)
        pixels = np.zeros((16, 24, 4), np.uint8)
        pixels[4:12, 8:16] = [240, 80, 40, 255]
        self.source = ImageLayer('原始图片', pixels, 24, 16)
        self.source.x, self.source.y = 12, 8
        self.editor.project.add_layer(self.source)
        self.editor.set_active(self.source)
        self.editor._refresh_layers()

    def tearDown(self):
        self.editor.close()
        self.editor.deleteLater()
        self.app.processEvents()

    def dialog_operation(self, operation, callback, accept=True):
        errors = []
        def interact():
            dialog = QApplication.activeModalWidget()
            try:
                self.assertIsNotNone(dialog)
                callback(dialog)
                box = dialog.findChild(QDialogButtonBox)
                box.button(QDialogButtonBox.StandardButton.Ok if accept else QDialogButtonBox.StandardButton.Cancel).click()
            except Exception as exc:
                errors.append(exc)
                if dialog:
                    dialog.reject()
        QTimer.singleShot(0, interact)
        operation()
        if errors:
            raise errors[0]

    def test_identity_curve_and_invalid_points(self):
        np.testing.assert_allclose(curve_table([[0, 0], [255, 255]]), np.arange(256), atol=1e-10)
        for points in ([[0, 0], [0, 100], [255, 255]], [[5, 0], [255, 255]], [[0, float('nan')], [255, 255]]):
            with self.assertRaises(ValueError):
                curve_table(points)

    def test_curve_hits_control_points_without_overshoot(self):
        table = curve_table([[0, 0], [64, 20], [128, 220], [255, 255]])
        self.assertEqual(table[64], 20)
        self.assertEqual(table[128], 220)
        self.assertTrue((np.diff(table) >= -1e-8).all())
        self.assertTrue(((table >= 0) & (table <= 255)).all())

    def test_per_channel_curve_and_master_order(self):
        rgb = np.full((3, 5, 3), 100, np.float32)
        result = apply_curves(rgb, {'red': [[0, 0], [100, 180], [255, 255]]})
        self.assertEqual(result[0, 0, 0], 180)
        np.testing.assert_allclose(result[:, :, 1:], 100)
        result = apply_curves(rgb, {'rgb': [[0, 255], [255, 0]], 'red': [[0, 0], [100, 180], [255, 255]]})
        self.assertEqual(result[0, 0, 0], 75)

    def test_curve_editor_add_and_remove_control_point(self):
        widget = CurveEditor()
        widget.resize(360, 320)
        point = widget.screen_point([128, 190]).toPoint()
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(len(widget.points), 3)
        QTest.mouseClick(widget, Qt.MouseButton.RightButton, pos=point)
        self.assertEqual(len(widget.points), 2)
        QTest.mouseClick(widget, Qt.MouseButton.RightButton, pos=widget.screen_point([0, 0]).toPoint())
        self.assertEqual(len(widget.points), 2)
        widget.close()

    def test_curve_dialog_accept_undo_redo_and_cancel(self):
        layer = ImageLayer('曲线', kind='adjust')
        layer.adjust = {'type': 'curves'}
        self.editor.project.add_layer(layer)
        self.editor.set_active(layer)
        def modify(dialog):
            graph = dialog.findChild(CurveEditor)
            graph.pointsChanged.emit([[0, 0], [128, 200], [255, 255]])
        self.dialog_operation(lambda: self.editor._edit_adjust_layer(layer), modify)
        self.assertEqual(layer.adjust['channels']['rgb'][1], [128, 200])
        self.editor.undo()
        self.assertNotIn('channels', self.editor.active.adjust)
        self.editor.redo()
        layer = self.editor.active
        saved = copy.deepcopy(layer.adjust)
        self.dialog_operation(lambda: self.editor._edit_adjust_layer(layer),
                              lambda dialog: dialog.findChild(CurveEditor).pointsChanged.emit([[0, 255], [255, 0]]), False)
        self.assertEqual(layer.adjust, saved)

    def test_curve_point_numeric_output_changes_selected_point(self):
        layer = ImageLayer('曲线', kind='adjust')
        layer.adjust = {'type': 'curves'}
        self.editor.project.add_layer(layer)
        def configure(dialog):
            graph = dialog.findChild(CurveEditor)
            QTest.mouseClick(graph, Qt.MouseButton.LeftButton, pos=graph.screen_point([128, 160]).toPoint())
            dialog.findChild(QSpinBox, 'curveOutput').setValue(210)
        self.dialog_operation(lambda: self.editor._edit_adjust_layer(layer), configure)
        self.assertEqual(layer.adjust['channels']['rgb'][1][1], 210)

    def test_filter_layer_keeps_source_and_can_be_disabled(self):
        original = self.source.pixels.copy()
        before = numpy_from_qimage(self.editor._render_composite(True))
        def configure(dialog):
            dialog.findChild(QSpinBox, 'filter_radius').setValue(2)
        self.dialog_operation(lambda: self.editor.add_filter_layer('gaussian_blur'), configure)
        effect = self.editor.active
        np.testing.assert_array_equal(original, self.source.pixels)
        after = numpy_from_qimage(self.editor._render_composite(True))
        self.assertFalse(np.array_equal(before, after))
        effect.visible = False
        np.testing.assert_array_equal(before, numpy_from_qimage(self.editor._render_composite(True)))
        effect.visible = True
        self.editor.undo()
        self.assertEqual(len(self.editor.project.layers), 1)
        self.editor.redo()
        self.assertEqual(self.editor.active.adjust['filter'], 'gaussian_blur')

    def test_cancel_new_filter_does_not_leave_layer_or_history(self):
        count = len(self.editor._history)
        self.dialog_operation(lambda: self.editor.add_filter_layer('motion_blur'), lambda _: None, False)
        QTest.qWait(150)
        self.assertEqual(len(self.editor.project.layers), 1)
        self.assertIs(self.editor.active, self.source)
        self.assertEqual(len(self.editor._history), count)

    def test_filter_edit_final_value_and_cancel_stop_preview_timer(self):
        self.dialog_operation(lambda: self.editor.add_filter_layer('gaussian_blur'), lambda _: None)
        layer = self.editor.active
        self.dialog_operation(lambda: self.editor.edit_filter_layer(layer),
                              lambda dlg: dlg.findChild(QSpinBox, 'filter_radius').setValue(7))
        self.assertEqual(layer.adjust['params']['radius'], 7)
        self.dialog_operation(lambda: self.editor.edit_filter_layer(layer),
                              lambda dlg: dlg.findChild(QSpinBox, 'filter_radius').setValue(15), False)
        QTest.qWait(150)
        self.assertEqual(layer.adjust['params']['radius'], 7)

    def test_selection_filter_mask_and_project_roundtrip(self):
        self.editor.selection = np.zeros((16, 24), bool)
        self.editor.selection[:, :12] = True
        before = numpy_from_qimage(self.editor._render_composite(True))
        self.dialog_operation(lambda: self.editor.add_filter_layer('invert'), lambda _: None)
        after = numpy_from_qimage(self.editor._render_composite(True))
        np.testing.assert_array_equal(before[:, 12:], after[:, 12:])
        self.assertFalse(np.array_equal(before[:, :12], after[:, :12]))
        self.editor._restore(self.editor.unpack_project(self.editor.pack_project(self.editor._snapshot())))
        np.testing.assert_array_equal(after, numpy_from_qimage(self.editor._render_composite(True)))

    def test_blur_transparent_edges_keeps_color_without_black_fringe(self):
        pixels = self.source.pixels
        for kind, params in (('gaussian_blur', {'radius': 3}), ('motion_blur', {'size': 7, 'angle': 20})):
            result = blur_rgba(pixels, kind, params)
            visible = result[:, :, 3] > 0
            expected = np.broadcast_to([240, 80, 40], result[:, :, :3].shape)
            np.testing.assert_array_equal(result[:, :, :3][visible], expected[visible])

    def test_noise_layer_is_stable_between_redraws(self):
        self.dialog_operation(lambda: self.editor.add_filter_layer('noise'), lambda _: None)
        a = numpy_from_qimage(self.editor._render_composite(True))
        b = numpy_from_qimage(self.editor._render_composite(True))
        np.testing.assert_array_equal(a, b)

    def test_all_registered_filters_support_editable_layer_rendering(self):
        for kind in self.editor.FILTERS:
            with self.subTest(kind=kind):
                self.editor.set_active(self.source)
                self.dialog_operation(lambda: self.editor.add_filter_layer(kind), lambda _: None)
                layer = self.editor.active
                output = numpy_from_qimage(self.editor._render_composite(True))
                self.assertEqual(output.shape, (16, 24, 4))
                self.assertEqual(layer.adjust['filter'], kind)
                self.editor.project.layers.remove(layer)

    def test_preview_grid_does_not_enter_effect_computation(self):
        from PyQt6.QtGui import QImage, QPainter
        self.dialog_operation(lambda: self.editor.add_filter_layer('gaussian_blur'), lambda _: None)
        export = self.editor._render_composite(True)
        expected = QImage(24, 16, QImage.Format.Format_ARGB32)
        expected.fill(Qt.GlobalColor.transparent)
        painter = QPainter(expected)
        self.editor._paint_checker(painter, 24, 16)
        painter.drawImage(0, 0, export)
        painter.end()
        np.testing.assert_array_equal(numpy_from_qimage(expected),
                                      numpy_from_qimage(self.editor._render_composite(False)))

    def test_filter_mask_can_be_painted_without_rasterizing_effect(self):
        self.dialog_operation(lambda: self.editor.add_filter_layer('invert'), lambda _: None)
        effect = self.editor.active
        self.editor._add_layer_mask(effect)
        self.editor._mask_edit = True
        self.editor.brush_size = 4
        self.editor.paint_stamp(QPointF(12, 8), erase=True)
        self.assertEqual(effect.mask[8, 12], 0)
        self.assertEqual(effect.mask[0, 0], 255)
        self.assertEqual(effect.kind, 'adjust')
        self.editor.set_active(self.source)
        self.assertFalse(self.editor._mask_edit)


if __name__ == '__main__':
    unittest.main()
