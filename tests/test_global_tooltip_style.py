import unittest

try:
    from PyQt6.QtGui import QPalette
    from PyQt6.QtWidgets import QApplication
    from ui.main_window import GLOBAL_TOOLTIP_STYLE, _apply_global_tooltip_palette
    QT_AVAILABLE = True
except ModuleNotFoundError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 runtime is not available")
class GlobalTooltipStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_global_tooltip_uses_white_text(self):
        normalized = GLOBAL_TOOLTIP_STYLE.replace(" ", "").lower()
        self.assertIn("qtooltip{", normalized)
        self.assertIn("color:#ffffff", normalized)

        _apply_global_tooltip_palette(self.app)
        tooltip_text = self.app.palette().color(QPalette.ColorRole.ToolTipText)
        self.assertEqual("#ffffff", tooltip_text.name().lower())


if __name__ == "__main__":
    unittest.main()
