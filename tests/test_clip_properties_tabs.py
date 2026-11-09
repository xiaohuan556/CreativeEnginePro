import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication, QGroupBox, QLineEdit, QTextEdit, QSlider
    from core.edit_engine import (AudioClip, EditTimeline, SubtitleBlock,
                                  VideoClip, FFmpegDirectExportWorker)
    from ui.clip_properties import ClipPropertiesPanel
    from ui.editor_tab import EditorTab
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

    def test_subtitle_opens_properties_with_dubbing_available(self):
        first = SubtitleBlock(text="测试字幕", timeline_start=0.0, timeline_end=2.0)
        second = SubtitleBlock(text="第二句", timeline_start=2.0, timeline_end=4.0)
        self.panel.set_selection(first, "subtitle")

        self.assertTrue(self.panel._tabs.isTabVisible(self.panel._TAB_DUBBING))
        self.assertEqual(self.panel._TAB_PROPERTY, self.panel._tabs.currentIndex())
        self.panel._tabs.setCurrentIndex(self.panel._TAB_DUBBING)
        self.panel.set_selection(second, "subtitle")
        self.assertEqual(self.panel._TAB_PROPERTY, self.panel._tabs.currentIndex())
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

    def test_effect_slider_drag_is_one_undo_step_and_restores_pre_state(self):
        timeline = EditTimeline()
        clip = VideoClip(
            source_path="sample.mp4", source_duration=5.0,
            trim_start=0.0, trim_end=5.0)
        timeline.add_video_clip(clip, skip_overlap=True)
        panel = ClipPropertiesPanel(timeline)
        try:
            panel.set_selection(clip, "video")
            slider = panel._tx_sliders["scale"]
            history_before = len(timeline._history)

            slider.sliderPressed.emit()
            slider.setValue(125)
            slider.setValue(150)
            slider.sliderReleased.emit()

            self.assertEqual(1.5, clip.scale)
            self.assertEqual(history_before + 1, len(timeline._history))
            timeline.undo()
            self.assertEqual(1.0, timeline.video_tracks[0][0].scale)
        finally:
            panel.close()
            panel.deleteLater()

    def test_audio_volume_text_accepts_percent_and_undoes_once(self):
        timeline = EditTimeline()
        clip = AudioClip(source_path="sample.wav", source_duration=3.0,
                         trim_start=0.0, trim_end=3.0)
        timeline.add_audio_clip(clip, skip_overlap=True)
        panel = ClipPropertiesPanel(timeline)
        try:
            panel.set_selection(clip, "audio")
            edit = panel._tabs.widget(panel._TAB_PROPERTY).findChild(QLineEdit)
            before = len(timeline._history)
            edit.setText("35%")
            edit.editingFinished.emit()
            edit.editingFinished.emit()  # Return + focus-out must not duplicate history
            self.assertEqual(0.35, clip.volume)
            self.assertEqual(before + 1, len(timeline._history))
            timeline.undo()
            self.assertEqual(1.0, timeline.audio_tracks[0][0].volume)
        finally:
            panel.close()
            panel.deleteLater()

    def test_speed_text_retimes_keyframes_and_undoes(self):
        timeline = EditTimeline()
        clip = VideoClip(source_path="sample.mp4", source_duration=4.0,
                         trim_start=0.0, trim_end=4.0,
                         keyframes={"scale": [(2.0, 1.5)]})
        timeline.add_video_clip(clip, skip_overlap=True)
        panel = ClipPropertiesPanel(timeline)
        try:
            panel.set_selection(clip, "video")
            edit = panel._tabs.widget(panel._TAB_SPEED).findChild(QLineEdit)
            edit.setText("2.0x")
            edit.editingFinished.emit()
            self.assertEqual(2.0, clip.speed)
            self.assertEqual(1.0, clip.keyframes["scale"][0][0])
            timeline.undo()
            restored = timeline.video_tracks[0][0]
            self.assertEqual(1.0, restored.speed)
            self.assertEqual(2.0, restored.keyframes["scale"][0][0])
        finally:
            panel.close()
            panel.deleteLater()

    def test_video_volume_slider_has_one_undo_step(self):
        timeline = EditTimeline()
        clip = VideoClip(source_path="sample.mp4", source_duration=3.0,
                         trim_start=0.0, trim_end=3.0)
        timeline.add_video_clip(clip, skip_overlap=True)
        panel = ClipPropertiesPanel(timeline)
        try:
            panel.set_selection(clip, "video")
            edit = next(e for e in panel._tabs.widget(panel._TAB_SPEED)
                        .findChildren(QLineEdit) if e.text().endswith("%"))
            before = len(timeline._history)
            edit.setText("65%")
            edit.editingFinished.emit()
            self.assertEqual(0.65, clip.volume)
            self.assertEqual(before + 1, len(timeline._history))
            timeline.undo()
            self.assertEqual(1.0, timeline.video_tracks[0][0].volume)
        finally:
            panel.close()
            panel.deleteLater()

    def test_transition_duration_text_accepts_seconds_and_undoes(self):
        timeline = EditTimeline()
        clip = VideoClip(source_path="sample.mp4", source_duration=4.0,
                         trim_start=0.0, trim_end=4.0,
                         out_transition={"type": "fade", "duration": 0.5})
        timeline.add_video_clip(clip, skip_overlap=True)
        panel = ClipPropertiesPanel(timeline)
        try:
            panel.set_selection(clip, "video")
            edit = panel._tabs.widget(panel._TAB_TRANSITION).findChild(QLineEdit)
            before = len(timeline._history)
            edit.setText("1.25s")
            edit.editingFinished.emit()
            self.assertEqual(1.25, clip.out_transition["duration"])
            self.assertEqual(before + 1, len(timeline._history))
            timeline.undo()
            self.assertEqual(0.5, timeline.video_tracks[0][0].out_transition["duration"])
        finally:
            panel.close()
            panel.deleteLater()

    def test_visual_effect_refresh_does_not_restart_audio(self):
        preview = SimpleNamespace(
            _seq_state=None, seek=Mock(), stop_audio=Mock(),
            play_all_audio=Mock())
        tab = SimpleNamespace(
            preview=preview,
            props_panel=SimpleNamespace(
                _track="video", _last_property_attr="chroma_key_similarity"),
            timeline_widget=SimpleNamespace(
                get_playhead=lambda: 1.0, is_playing=lambda: True),
        )
        EditorTab._do_property_seek(tab)
        preview.seek.assert_called_once_with(1.0, force=True)
        preview.stop_audio.assert_not_called()

    def test_volume_refresh_skips_video_decode(self):
        preview = SimpleNamespace(
            _seq_state=None, seek=Mock(), stop_audio=Mock(),
            play_all_audio=Mock())
        timer = SimpleNamespace(isActive=lambda: False, start=Mock())
        tab = SimpleNamespace(
            preview=preview,
            _audio_property_timer=timer,
            props_panel=SimpleNamespace(
                _track="audio", _last_property_attr="volume"),
            timeline_widget=SimpleNamespace(
                get_playhead=lambda: 1.0, is_playing=lambda: True),
        )
        EditorTab._do_property_seek(tab)
        preview.seek.assert_not_called()
        preview.stop_audio.assert_not_called()
        preview.play_all_audio.assert_not_called()
        timer.start.assert_called_once_with()

    def test_volume_slider_release_applies_final_value(self):
        tab = SimpleNamespace(
            _prop_debounce=SimpleNamespace(stop=Mock()),
            _audio_property_timer=SimpleNamespace(stop=Mock()),
            _refresh_audio_property=Mock(),
        )
        EditorTab._on_property_interaction_finished(tab, "volume")
        tab._prop_debounce.stop.assert_called_once_with()
        tab._audio_property_timer.stop.assert_called_once_with()
        tab._refresh_audio_property.assert_called_once_with()

    def test_speed_change_refreshes_playback_boundary_index(self):
        index = SimpleNamespace(rebuild=Mock())
        canvas = SimpleNamespace(_timeline_index=index, _update_width=Mock())
        preview = SimpleNamespace(
            _seq_state=None, seek=Mock(), stop_audio=Mock(),
            play_all_audio=Mock(), rebuild_playback_index=Mock())
        tab = SimpleNamespace(
            preview=preview,
            props_panel=SimpleNamespace(_track="video", _last_property_attr="speed"),
            timeline_widget=SimpleNamespace(
                _canvas=canvas,
                get_playhead=lambda: 1.0,
                is_playing=lambda: True),
        )
        EditorTab._do_property_seek(tab)
        index.rebuild.assert_called_once_with()
        preview.rebuild_playback_index.assert_called_once_with()
        canvas._update_width.assert_called_once_with()
        preview.seek.assert_called_once_with(1.0, force=True)
        preview.play_all_audio.assert_called_once_with(1.0)

    def test_subtitle_text_edit_is_undoable_after_selection_changes(self):
        timeline = EditTimeline()
        first = SubtitleBlock(text="原句", timeline_start=0.0, timeline_end=2.0)
        second = SubtitleBlock(text="下一句", timeline_start=2.0, timeline_end=4.0)
        timeline.add_subtitle(first)
        timeline.add_subtitle(second)
        panel = ClipPropertiesPanel(timeline)
        try:
            panel.set_selection(first, "subtitle")
            edit = panel._tabs.widget(panel._TAB_PROPERTY).findChild(QTextEdit)
            before = len(timeline._history)
            edit.setPlainText("新句")
            panel.set_selection(second, "subtitle")
            self.assertEqual(before + 1, len(timeline._history))
            timeline.undo()
            self.assertEqual("原句", timeline.subtitle_tracks[0][0].text)
        finally:
            panel.close()
            panel.deleteLater()

    def test_zero_opacity_stays_zero_in_video_and_subtitle_controls(self):
        for clip, kind in (
            (VideoClip(source_path="sample.mp4", source_duration=2.0,
                       trim_start=0.0, trim_end=2.0, opacity=0.0), "video"),
            (SubtitleBlock(text="透明", timeline_start=0.0,
                           timeline_end=2.0, opacity=0.0), "subtitle"),
        ):
            with self.subTest(kind=kind):
                self.panel.set_selection(clip, kind)
                groups = self.panel._tabs.widget(self.panel._TAB_PROPERTY)
                opacity_group = next(g for g in groups.findChildren(QGroupBox)
                                     if g.title() == "不透明度")
                self.assertEqual(0, opacity_group.findChild(QSlider).value())

    def test_zero_opacity_subtitle_uses_static_raster_renderer(self):
        timeline = EditTimeline()
        timeline.add_subtitle(SubtitleBlock(
            text="透明", timeline_start=0.0, timeline_end=2.0, opacity=0.0))
        worker = FFmpegDirectExportWorker(timeline, "out.mp4", (1920, 1080))
        self.assertFalse(worker._needs_compositor([], False))


if __name__ == "__main__":
    unittest.main()
