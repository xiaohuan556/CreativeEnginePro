import unittest

try:
    from PyQt6.QtWidgets import QApplication, QHBoxLayout
    from core.edit_engine import EditTimeline
    from ui.timeline_widget import TimelineWidget
    QT_AVAILABLE = True
except ModuleNotFoundError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 runtime is not available")
class TimelineToolbarLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_new_timeline_plus_is_left_of_play(self):
        widget = TimelineWidget(EditTimeline())
        toolbar_layout = widget._btn_new_timeline.parentWidget().layout()
        self.assertIsInstance(toolbar_layout, QHBoxLayout)
        self.assertIs(widget._btn_new_timeline, toolbar_layout.itemAt(0).widget())
        self.assertIs(widget._btn_play, toolbar_layout.itemAt(1).widget())
        self.assertEqual("+", widget._btn_new_timeline.text())
        self.assertEqual("新建时间线", widget._btn_new_timeline.toolTip())
        widget.close()


if __name__ == "__main__":
    unittest.main()
