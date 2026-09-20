import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication, QGroupBox
    from core.edit_engine import AudioClip, EditTimeline, SubtitleBlock, VideoClip
    from ui.clip_properties import ClipPropertiesPanel
    QT_AVAILABLE = True
except ModuleNotFoundError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 runtime is not available")
class ClipPropertiesTabsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = ClipPropertiesPanel(EditTimeline())

    def tearDown(self):
        self.panel.close()
        self.panel.deleteLater()
        self.app.processEvents()

    def _group_titles(self, tab_index):
        page = self.panel._tabs.widget(tab_index)
        return {group.title() for group in page.findChildren(QGroupBox)}

    def test_video_tools_are_split_into_top_level_tabs(self):
        clip = VideoClip(
            source_path="sample.mp4", source_duration=5.0,
            trim_start=0.0, trim_end=5.0)
        self.panel.set_selection(clip, "video")

        self.assertEqual(
            [self.panel._tabs.tabText(i) for i in range(5)],
            ["属性", "蒙版", "抠像", "变速", "转场"],
        )
        self.assertTrue(all(self.panel._tabs.isTabVisible(i) for i in range(5)))

        property_groups = self._group_titles(self.panel._TAB_PROPERTY)
        self.assertIn("位置 & 变换", property_groups)
        self.assertIn("不透明度", property_groups)
        self.assertNotIn("蒙版", property_groups)
        self.assertNotIn("绿幕抠像", property_groups)
        self.assertNotIn("速度 & 音量", property_groups)
        self.assertNotIn("转场（到下一段）", property_groups)

        self.assertEqual({"蒙版"}, self._group_titles(self.panel._TAB_MASK))
        self.assertEqual({"绿幕抠像"}, self._group_titles(self.panel._TAB_CHROMA))
        self.assertEqual({"速度 & 音量"}, self._group_titles(self.panel._TAB_SPEED))
        self.assertEqual(
            {"转场（到下一段）"},
            self._group_titles(self.panel._TAB_TRANSITION),
        )

    def test_non_video_selection_hides_video_only_tabs(self):
        clip = AudioClip(
            source_path="sample.wav", source_duration=3.0,
            trim_start=0.0, trim_end=3.0)
        self.panel.set_selection(clip, "audio")

        self.assertEqual(self.panel._TAB_PROPERTY, self.panel._tabs.currentIndex())
        for index in range(self.panel._TAB_MASK, self.panel._TAB_TRANSITION + 1):
            self.assertFalse(self.panel._tabs.isTabVisible(index))

    def test_rebuild_keeps_selected_video_category(self):
        clip = VideoClip(
            source_path="sample.mp4", source_duration=5.0,
            trim_start=0.0, trim_end=5.0)
        self.panel.set_selection(clip, "video")
        self.panel._tabs.setCurrentIndex(self.panel._TAB_MASK)

        self.panel._rebuild_ui()

        self.assertEqual(self.panel._TAB_MASK, self.panel._tabs.currentIndex())

    def test_subtitle_still_opens_dubbing_tab(self):
        self.panel.set_selection(
            SubtitleBlock(text="测试字幕", timeline_start=0.0, timeline_end=2.0),
            "subtitle",
        )

        self.assertTrue(self.panel._tabs.isTabVisible(self.panel._TAB_DUBBING))
        self.assertEqual(self.panel._TAB_DUBBING, self.panel._tabs.currentIndex())
        for index in range(self.panel._TAB_MASK, self.panel._TAB_TRANSITION + 1):
            self.assertFalse(self.panel._tabs.isTabVisible(index))

    def test_selected_subtitles_receive_style_changes_together(self):
        first = SubtitleBlock(text="一", timeline_start=0.0, timeline_end=1.0)
        second = SubtitleBlock(text="二", timeline_start=1.0, timeline_end=2.0)
        audio = AudioClip(source_path="other.wav", source_duration=1.0,
                          trim_start=0.0, trim_end=1.0)
        panel = ClipPropertiesPanel(
            EditTimeline(),
            get_selected_clips_cb=lambda: [
                (first, "subtitle", 0),
                (second, "subtitle", 0),
                (audio, "audio", 0),
            ],
        )
        try:
            panel.set_selection(first, "subtitle")
            panel._set(first, "font_size", 48)
            panel._set(first, "outline_width", 5)
            panel._set(first, "pos_y", 0.25)
            panel._set(first, "scale", 1.4)
            panel._set(first, "text", "只修改第一句")

            for subtitle in (first, second):
                self.assertEqual(48, subtitle.font_size)
                self.assertEqual(5, subtitle.outline_width)
                self.assertEqual(0.25, subtitle.pos_y)
                self.assertEqual(1.4, subtitle.scale)
            self.assertEqual("只修改第一句", first.text)
            self.assertEqual("二", second.text)
            self.assertEqual(1.0, audio.volume)
        finally:
            panel.close()
            panel.deleteLater()

    def test_selected_audio_clips_receive_volume_change_together(self):
        first = AudioClip(source_path="one.wav", source_duration=2.0,
                          trim_start=0.0, trim_end=2.0)
        second = AudioClip(source_path="two.wav", source_duration=2.0,
                           trim_start=0.0, trim_end=2.0)
        panel = ClipPropertiesPanel(
            EditTimeline(),
            get_selected_clips_cb=lambda: [
                (first, "audio", 0), (second, "audio", 1),
            ],
        )
        try:
            panel.set_selection(first, "audio")
            panel._set(first, "volume", 0.35)

            self.assertEqual(0.35, first.volume)
            self.assertEqual(0.35, second.volume)
        finally:
            panel.close()
            panel.deleteLater()


if __name__ == "__main__":
    unittest.main()
