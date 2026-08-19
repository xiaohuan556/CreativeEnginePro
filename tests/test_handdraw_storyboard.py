import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QPointF
    from PyQt6.QtWidgets import QApplication
    from ai.ui.handdraw_storyboard import HanddrawStoryboardDialog, StoryboardSheet
    QT_AVAILABLE = True
except ModuleNotFoundError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 runtime is not available")
class HanddrawStoryboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_professional_fields_are_captured_back_to_shot(self):
        board = {"shots": [{"duration": 3, "shot_size": "中景", "visual": "人物停下"}]}
        dialog = HanddrawStoryboardDialog(board)
        dialog.camera_angle.setText("低机位")
        dialog.lens.setText("35mm")
        dialog.entry_state.setText("人物从画左入镜")
        dialog.exit_state.setText("人物停在门前")
        dialog.continuity.setPlainText("右手持伞，主光来自画左")
        dialog._capture_state(); dialog._apply_states()
        shot = board["shots"][0]
        self.assertEqual("低机位", shot["camera_angle"])
        self.assertEqual("35mm", shot["lens"])
        self.assertEqual("人物停在门前", shot["exit_state"])
        self.assertIn("主光", shot["continuity_notes"])
        dialog.deleteLater()

    def test_action_board_uses_four_columns(self):
        dialog = HanddrawStoryboardDialog({"shots": [{"duration": 2}] * 8})
        dialog.set_board_mode("action")
        self.assertEqual("action", dialog.board["storyboard_mode"])
        self.assertEqual(4, dialog.sheet.columns)
        dialog.deleteLater()

    def test_sheet_exports_structured_annotation_layer(self):
        sheet = StoryboardSheet()
        sheet.set_shots([{
            "duration": 2, "shot_size": "特写",
            "annotations": [{"type": "camera", "points": [[.1, .5], [.8, .5]]}],
        }])
        image = sheet.render_image(900, 600)
        self.assertFalse(image.isNull())
        self.assertGreater(image.width(), 0)
        sheet.deleteLater()


if __name__ == "__main__":
    unittest.main()
