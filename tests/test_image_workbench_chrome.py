import os
import unittest
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
from PyQt6.QtCore import Qt, QPoint, QPointF, QRect
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog
from ui.image_editor import ImageEditorWidget, ImageLayer, Artboard, Tool
from ui.image_color_dialog import WorkbenchColorDialog, ScreenColorPicker


class WorkbenchChromeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = ImageEditorWidget()
        self.editor.new_project(600, 400)
        self.layers = []
        for index, x in enumerate((140, 300, 460)):
            pixels = np.empty((60, 40, 4), np.uint8)
            pixels[:] = [70+60*index, 130, 180, 255]
            layer = ImageLayer(str(index), pixels, 40, 60)
            layer.x, layer.y = x, 200
            self.editor.project.add_layer(layer)
            self.layers.append(layer)
        self.editor.set_active(self.layers[0])
        self.editor.set_tool(Tool.MOVE)
        self.editor.smart_guides_on = self.editor.snap_on = False
        self.editor.resize(1400, 900)
        self.editor.show()
        self.editor._redraw()
        self.app.processEvents()
        self.editor.view.resetTransform()
        self.editor.view.centerOn(300, 200)
        self.editor._show_handles = False
        self.app.processEvents()

    def tearDown(self):
        self.editor.close()
        self.editor.deleteLater()
        self.app.processEvents()

    def position(self, point):
        return self.editor.view.mapFromScene(QPointF(*point))

    def click(self, point, modifiers=Qt.KeyboardModifier.NoModifier):
        QTest.mouseClick(self.editor.view.viewport(), Qt.MouseButton.LeftButton,
                         modifier=modifiers, pos=self.position(point))
        self.app.processEvents()

    def marquee(self, start=(80, 120), end=(350, 250), modifiers=Qt.KeyboardModifier.NoModifier):
        surface = self.editor.view.viewport()
        QTest.mousePress(surface, Qt.MouseButton.LeftButton, modifier=modifiers, pos=self.position(start))
        QTest.mouseMove(surface, self.position(end), delay=5)
        state = self.editor.view._layer_marquee
        self.assertIsNotNone(state)
        self.assertTrue(state['dragged'])
        QTest.mouseRelease(surface, Qt.MouseButton.LeftButton, modifier=modifiers, pos=self.position(end))
        self.app.processEvents()

    def test_shift_click_adds_only_clicked_layer_and_control_toggles(self):
        self.click((460, 200), Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.editor.selected, [self.layers[0], self.layers[2]])
        self.click((140, 200), Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.editor.selected, [self.layers[2]])

    def test_each_tool_button_remains_reachable_and_has_one_active_state(self):
        for tool, button in self.editor._tool_buttons.items():
            with self.subTest(tool=tool):
                button.click()
                self.assertEqual(self.editor.tool, tool)
                self.assertTrue(button.isChecked())
                self.assertEqual(sum(item.isChecked() for item in self.editor._tool_buttons.values()), 1)
                self.assertEqual(button.accessibleName(), button.text())

    def test_tool_badge_tracks_shortcut_remapping_and_disable(self):
        button = self.editor._tool_buttons[Tool.BRUSH]
        self.editor.apply_shortcuts({'工具 › BRUSH': 'F9'}, persist=False)
        self.assertEqual(button.shortcut_label, 'F9')
        self.assertIn('F9', button.toolTip())
        QTest.keyClick(self.editor.view, Qt.Key.Key_F9)
        self.assertEqual(self.editor.tool, Tool.BRUSH)
        self.editor.apply_shortcuts({'工具 › BRUSH': ''}, persist=False)
        self.assertEqual(button.shortcut_label, '')
        self.assertEqual(self.editor._tool_actions[Tool.BRUSH].text(), '画笔')

    def test_dragging_splitter_collapses_tools_to_icons_and_expands_again(self):
        rail = self.editor._tool_rail
        splitter = self.editor.main_split
        handle = splitter.handle(1)
        initial_canvas_width = self.editor.view.width()
        self.assertFalse(rail.compact)
        position = handle.rect().center()
        QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=position)
        QTest.mouseMove(handle, position-QPoint(140, 0), delay=10)
        QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=handle.rect().center())
        self.app.processEvents()
        self.assertLessEqual(rail.width(), 80)
        self.assertTrue(rail.compact)
        self.assertTrue(all(button.compact for button in self.editor._tool_buttons.values()))
        self.assertTrue(self.editor._color_card.hex_label.isHidden())
        self.assertGreater(self.editor.view.width(), initial_canvas_width)
        self.editor._tool_buttons[Tool.BRUSH].click()
        self.assertEqual(self.editor.tool, Tool.BRUSH)
        position = handle.rect().center()
        QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=position)
        QTest.mouseMove(handle, position+QPoint(130, 0), delay=10)
        QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=handle.rect().center())
        self.app.processEvents()
        self.assertGreaterEqual(rail.width(), 130)
        self.assertFalse(rail.compact)
        self.assertFalse(self.editor._color_card.hex_label.isHidden())
        self.assertEqual(self.editor.tool, Tool.BRUSH)
        self.assertEqual(self.editor._tool_buttons[Tool.BRUSH].text(), '画笔')

    def test_rail_double_click_restores_previous_expanded_width(self):
        rail = self.editor._tool_rail
        rail.set_rail_width(190)
        self.app.processEvents()
        handle = self.editor.main_split.handle(1)
        QTest.mouseDClick(handle, Qt.MouseButton.LeftButton, pos=handle.rect().center())
        self.app.processEvents()
        self.assertTrue(rail.compact)
        self.assertEqual(rail.width(), 52)
        QTest.mouseDClick(handle, Qt.MouseButton.LeftButton, pos=handle.rect().center())
        self.app.processEvents()
        self.assertFalse(rail.compact)
        self.assertAlmostEqual(rail.width(), 190, delta=1)

    def test_narrow_rail_preserves_color_buttons_and_tooltip_shortcuts(self):
        self.editor._tool_rail.set_rail_width(52)
        self.app.processEvents()
        card = self.editor._color_card
        self.assertLessEqual(card.width(), 42)
        self.assertFalse(card.foreground.isHidden())
        self.assertFalse(card.background.isHidden())
        with patch('ui.image_editor.choose_color', return_value=QColor('#d07050')):
            card.foreground.click()
        self.assertEqual(self.editor.fg.name(), '#d07050')
        self.editor.apply_shortcuts({'工具 › BRUSH': 'F9'}, persist=False)
        self.assertIn('F9', self.editor._tool_buttons[Tool.BRUSH].toolTip())

    def test_marquee_selects_layers_without_pixel_mask_or_history_changes(self):
        count = len(self.editor._history)
        self.marquee()
        self.assertEqual(self.editor.selected, self.layers[:2])
        self.assertIsNone(self.editor.selection)
        self.assertEqual(len(self.editor._history), count)
        self.assertIsNone(self.editor.view._layer_marquee)

    def test_shift_marquee_adds_and_control_marquee_toggles(self):
        self.editor.set_active(self.layers[2])
        self.marquee(modifiers=Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(set(self.editor.selected), set(self.layers))
        self.marquee((90, 130), (180, 260), Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(set(self.editor.selected), set(self.layers[1:]))

    def test_reverse_drag_at_changed_zoom_selects_same_layers(self):
        self.editor.view.zoom_by(1.5)
        self.marquee((350, 250), (80, 120))
        self.assertEqual(self.editor.selected, self.layers[:2])

    def test_locked_hidden_and_adjustment_layers_are_not_marquee_targets(self):
        self.layers[0].locked = True
        self.layers[1].visible = False
        adjust = ImageLayer('adjust', kind='adjust')
        self.editor.project.add_layer(adjust)
        self.marquee((80, 120), (510, 270))
        self.assertEqual(self.editor.selected, [self.layers[2]])

    def test_escape_restores_original_layer_selection(self):
        self.editor.set_active(self.layers[2])
        surface = self.editor.view.viewport()
        QTest.mousePress(surface, Qt.MouseButton.LeftButton, pos=self.position((80, 120)))
        QTest.mouseMove(surface, self.position((350, 250)), delay=5)
        QTest.keyClick(self.editor.view, Qt.Key.Key_Escape)
        QTest.mouseRelease(surface, Qt.MouseButton.LeftButton, pos=self.position((350, 250)))
        self.assertIsNone(self.editor.view._layer_marquee)
        self.assertEqual(self.editor.selected, [self.layers[2]])

    def test_dragging_a_marquee_selected_layer_moves_all_selected_layers(self):
        self.marquee()
        surface = self.editor.view.viewport()
        QTest.mousePress(surface, Qt.MouseButton.LeftButton, pos=self.position((140, 200)))
        QTest.mouseMove(surface, self.position((170, 220)), delay=5)
        QTest.mouseRelease(surface, Qt.MouseButton.LeftButton, pos=self.position((170, 220)))
        self.assertEqual([(layer.x, layer.y) for layer in self.layers], [(170, 220), (330, 220), (460, 200)])

    def test_text_layer_does_not_intercept_modifier_multiselect(self):
        text = ImageLayer('text', kind='text', w=100, h=60)
        text.x, text.y = 470, 90
        self.editor.project.add_layer(text)
        self.editor.set_active(text)
        self.click((140, 200), Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.editor.selected, [text, self.layers[0]])
        self.assertIsNone(self.editor._text_frame_layer())

    def test_empty_artboard_drag_selects_contents_instead_of_moving_board(self):
        board = Artboard('board', 10, 20, 600, 400)
        board.layers = self.layers
        self.editor.project.layers = []
        self.editor.project.artboards = [board]
        self.editor.active_artboard = board
        self.editor._invalidate_doc_bounds()
        x, y = self.editor._ab_screen(board)
        self.editor.view.centerOn(x+300, y+200)
        self.marquee((x+80, y+120), (x+350, y+250))
        self.assertEqual((board.x, board.y), (10, 20))
        self.assertEqual(self.editor.selected, self.layers[:2])

    def test_color_card_accept_cancel_and_swap_stay_in_sync(self):
        original = QColor(self.editor.fg)
        with patch('ui.image_editor.choose_color', return_value=QColor()):
            self.editor._choose_fg()
        self.assertEqual(self.editor.fg, original)
        with patch('ui.image_editor.choose_color', return_value=QColor('#45a1e4')):
            self.editor._color_card.foreground.click()
        self.assertEqual(self.editor._color_card.hex_label.text(), '#45A1E4')
        self.assertEqual(self.editor.fg_btn.color, self.editor.fg)
        self.editor._color_card.swap_button.click()
        self.assertEqual(self.editor._color_card.foreground.color, self.editor.fg)
        self.assertEqual(self.editor._color_card.background.color, self.editor.bg)
        self.assertEqual(self.editor.bg.name(), '#45a1e4')

    def test_zoom_controls_have_drawn_icons_and_work_with_typed_percentage(self):
        for button in (self.editor.b_zoom_in, self.editor.b_zoom_out, self.editor.b_fit):
            self.assertFalse(button.icon().isNull())
            self.assertTrue(button.accessibleName())
        self.editor.b_zoom_in.click()
        self.assertAlmostEqual(self.editor.view.transform().m11(), 1.2)
        self.editor.b_zoom_out.click()
        self.assertAlmostEqual(self.editor.view.transform().m11(), 1)
        self.editor.zoom_label.setText('175%')
        self.editor._zoom_controls.apply_percentage()
        self.assertAlmostEqual(self.editor.view.transform().m11(), 1.75)
        self.editor.zoom_label.setText('invalid')
        self.editor._zoom_controls.apply_percentage()
        self.assertEqual(self.editor.zoom_label.text(), '175%')
        self.editor.b_fit.click()
        self.assertGreater(self.editor.view.transform().m11(), 0)

    def test_picker_rgb_hsv_hex_and_invalid_input(self):
        dialog = WorkbenchColorDialog(QColor('#3b82f6'), self.editor, '前景色')
        dialog.hex_edit.setText('#12abef')
        self.assertTrue(dialog.apply_hex())
        self.assertEqual(dialog.color.name(), '#12abef')
        dialog.rgb[0].setValue(200)
        self.assertEqual(dialog.color.red(), 200)
        dialog.hsv[0].setValue(120); dialog.hsv[1].setValue(100); dialog.hsv[2].setValue(100)
        self.assertEqual(dialog.color.name(), '#00ff00')
        dialog.hex_edit.setText('#GGGGGG'); dialog.accept()
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.color.name(), '#00ff00')
        dialog.hex_edit.setText('#4590f0'); dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.color.name(), '#4590f0')
        dialog.deleteLater()

    def test_picker_drag_changes_preview_and_reject_keeps_original(self):
        dialog = WorkbenchColorDialog(QColor('#336699'), self.editor)
        dialog.show(); self.app.processEvents()
        original = QColor(dialog.original)
        QTest.mouseClick(dialog.area, Qt.MouseButton.LeftButton,
                         pos=QPoint(dialog.area.width()-2, 2))
        self.assertNotEqual(dialog.color, original)
        self.assertEqual(dialog.new_preview.color, dialog.color)
        dialog.old_preview.click()
        self.assertEqual(dialog.color, original)
        dialog.reject()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        dialog.deleteLater()

    def test_screen_color_sampling_maps_high_dpi_screen_coordinates(self):
        image = QImage(200, 200, QImage.Format.Format_RGB32)
        image.fill(QColor('red')); image.setPixelColor(100, 100, QColor('blue'))
        class Screen:
            def geometry(self): return QRect(100, 100, 100, 100)
            def grabWindow(self, _): return QPixmap.fromImage(image)
        dialog = WorkbenchColorDialog(QColor('red'), self.editor)
        with patch('ui.image_color_dialog.QApplication.screens', return_value=[Screen()]):
            picker = ScreenColorPicker(dialog)
        self.assertEqual(picker.sample(QPoint(150, 150)).name(), '#0000ff')
        self.assertFalse(picker.sample(QPoint(0, 0)).isValid())
        picker.deleteLater(); dialog.deleteLater()


if __name__ == '__main__':
    unittest.main()
