import os
import unittest
from types import MethodType, SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QHBoxLayout, QPushButton, QWidget

from ui.editor_tab import EditorTab


class TimelineTabOverflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _harness(self, width=320, count=10, active=9):
        bar = QWidget()
        bar.resize(width, 28)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(2)
        layout.addStretch()
        more = QPushButton("☰")
        more.setFixedSize(52, 22)
        more.hide()
        layout.addWidget(more)
        owner = SimpleNamespace(
            _tl_tab_bar=bar,
            _tl_tab_layout=layout,
            _tl_more_btn=more,
            _active_tl_idx=active,
        )
        owner._timeline_tab_buttons = MethodType(
            EditorTab._timeline_tab_buttons, owner)
        for index in range(count):
            button = QPushButton(f"时间线 {index + 1}")
            button.setProperty("timeline_index", index)
            layout.insertWidget(layout.count() - 2, button)
        return owner

    def test_extra_tabs_are_collected_and_active_tab_stays_visible(self):
        owner = self._harness()

        EditorTab._refresh_timeline_tab_overflow(owner)

        buttons = owner._timeline_tab_buttons()
        hidden = [button for button in buttons if button.isHidden()]
        self.assertTrue(hidden)
        self.assertFalse(buttons[owner._active_tl_idx].isHidden())
        self.assertFalse(owner._tl_more_btn.isHidden())
        self.assertEqual(f"☰ {len(hidden)}", owner._tl_more_btn.text())

    def test_all_tabs_return_when_the_bar_is_wide_enough(self):
        owner = self._harness(width=1600, count=5, active=4)

        EditorTab._refresh_timeline_tab_overflow(owner)

        self.assertTrue(all(not button.isHidden()
                            for button in owner._timeline_tab_buttons()))
        self.assertTrue(owner._tl_more_btn.isHidden())


if __name__ == "__main__":
    unittest.main()
