import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication
    from ai.ui.ai_assistant import VideoGenPanel
    from ai.ui.video_gen_dialog import VideoGenDialog
    QT_AVAILABLE = True
except ModuleNotFoundError:
    QT_AVAILABLE = False


class _Provider:
    def __init__(self, name):
        self.name = name


class _Registry:
    def by_domain(self, _domain):
        return [_Provider("seedance"), _Provider("veo")]


class _Manager:
    registry = _Registry()


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 runtime is not available")
class VeoUiConstraintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_assistant_disables_unsupported_veo_ratios(self):
        with patch("ai.ui.ai_assistant.get_ai_manager", return_value=_Manager()):
            panel = VideoGenPanel()
        panel._provider.setCurrentIndex(panel._provider.findData("veo"))
        self.app.processEvents()

        enabled = {
            button.key for button in panel._ratio_group._buttons
            if button.isEnabled()
        }
        self.assertEqual({"16:9", "9:16"}, enabled)
        self.assertIn(panel._ratio_group.selected_key, enabled)
        panel.deleteLater()

    def test_assistant_keeps_creative_prompt_separate_for_image_video(self):
        combined = VideoGenPanel._image_video_prompt(
            "人物站在窗前", "镜头缓慢环绕，窗外雨势增强")
        self.assertIn("人物站在窗前", combined)
        self.assertIn("用户创意与动态意图", combined)
        self.assertIn("镜头缓慢环绕，窗外雨势增强", combined)
        self.assertEqual(
            "人物站在窗前",
            VideoGenPanel._image_video_prompt("人物站在窗前", ""))

    def test_dialog_switches_to_real_veo_durations_and_ratios(self):
        with patch("ai.ui.video_gen_dialog.get_ai_manager", return_value=_Manager()):
            dialog = VideoGenDialog()
        dialog._provider.setCurrentIndex(dialog._provider.findData("veo"))
        self.app.processEvents()

        ratios = [dialog._ratio.itemText(i) for i in range(dialog._ratio.count())]
        durations = [dialog._duration.itemText(i)
                     for i in range(dialog._duration.count())]
        self.assertEqual(["16:9", "9:16"], ratios)
        self.assertEqual(["4", "6", "8"], durations)
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
