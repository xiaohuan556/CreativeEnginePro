import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication
    from ui.dubbing_panel import DubbingPanel
    QT_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 runtime is not available")
class DubbingPanelDurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_failed_probe_uses_subtitle_duration_instead_of_zero_width_clip(self):
        added = []
        panel = DubbingPanel(
            add_audio_cb=lambda path, duration, start, end:
                added.append((path, duration, start, end)))
        try:
            panel._gen_cancelled = False
            panel._gen_queue = [(object(), "字幕", 4.0, 6.5)]
            panel._gen_total = 1
            panel._gen_idx = 0
            panel._subtitle_start = 4.0
            panel._subtitle_end = 6.5
            with patch("ui.dubbing_panel._audio_duration", return_value=0.0), \
                    patch.object(panel, "_on_play"):
                panel._on_done("千语种配音.mp3")

            self.assertEqual(1, len(added))
            self.assertEqual(2.5, added[0][1])
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
