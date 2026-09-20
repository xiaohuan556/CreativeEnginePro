import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QEvent, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtWidgets import QApplication
    from core.edit_engine import AudioClip, EditTimeline, SubtitleBlock, VideoClip
    from ui.timeline_widget import TimelineWidget
    QT_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    QT_AVAILABLE = False


def _audio(name, start, duration=1.0):
    return AudioClip(
        source_path=name, source_duration=duration,
        trim_start=0.0, trim_end=duration, timeline_start=start)


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 runtime is not available")
class TimelineGroupMoveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dragging_one_marquee_member_moves_the_whole_selection(self):
        timeline = EditTimeline()
        first = _audio("one.wav", 1.0)
        second = _audio("two.wav", 4.0)
        timeline.add_audio_clip(first, track_idx=0, skip_overlap=True)
        timeline.add_audio_clip(second, track_idx=0, skip_overlap=True)
        widget = TimelineWidget(timeline)
        try:
            canvas = widget._canvas
            track = next(td for td in canvas._tracks
                         if td.kind == "audio" and td.idx == 0)
            canvas._marquee_selected = [(first, track), (second, track)]
            canvas._marquee_active = True
            rect = canvas._clip_rect(first, track)
            press = QPointF(rect.center())
            move = QPointF(press.x() + 200, press.y())  # +2s at 100 px/s

            canvas.mousePressEvent(QMouseEvent(
                QEvent.Type.MouseButtonPress, press, press,
                Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier))
            canvas.mouseMoveEvent(QMouseEvent(
                QEvent.Type.MouseMove, move, move,
                Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier))
            canvas.mouseReleaseEvent(QMouseEvent(
                QEvent.Type.MouseButtonRelease, move, move,
                Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier))

            self.assertAlmostEqual(3.0, first.timeline_start, places=2)
            self.assertAlmostEqual(6.0, second.timeline_start, places=2)
            self.assertAlmostEqual(3.0,
                                   second.timeline_start - first.timeline_start,
                                   places=2)
        finally:
            widget.close()

    def test_auto_align_does_not_close_audio_gaps(self):
        timeline = EditTimeline()
        first = _audio("one.wav", 0.0, 2.0)
        second = _audio("two.wav", 2.0, 2.0)
        timeline.add_audio_clip(first, track_idx=0, skip_overlap=True)
        timeline.add_audio_clip(second, track_idx=0, skip_overlap=True)

        timeline.remove_audio_clip(first.id)

        self.assertAlmostEqual(2.0, second.timeline_start)

    def test_selected_subtitles_move_together_and_keep_their_durations(self):
        timeline = EditTimeline()
        first = SubtitleBlock(text="一", timeline_start=1.0, timeline_end=2.0)
        second = SubtitleBlock(text="二", timeline_start=4.0, timeline_end=5.5)
        timeline.add_subtitle(first, track_idx=0)
        timeline.add_subtitle(second, track_idx=0)
        widget = TimelineWidget(timeline)
        try:
            canvas = widget._canvas
            track = next(td for td in canvas._tracks
                         if td.kind == "subtitle" and td.idx == 0)
            canvas._marquee_selected = [(first, track), (second, track)]
            canvas._marquee_active = True
            rect = canvas._clip_rect(first, track)
            press = QPointF(rect.center())
            move = QPointF(press.x() + 150, press.y())

            canvas.mousePressEvent(QMouseEvent(
                QEvent.Type.MouseButtonPress, press, press,
                Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier))
            canvas.mouseMoveEvent(QMouseEvent(
                QEvent.Type.MouseMove, move, move,
                Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier))
            canvas.mouseReleaseEvent(QMouseEvent(
                QEvent.Type.MouseButtonRelease, move, move,
                Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier))

            self.assertAlmostEqual(2.5, first.timeline_start, places=2)
            self.assertAlmostEqual(3.5, first.timeline_end, places=2)
            self.assertAlmostEqual(5.5, second.timeline_start, places=2)
            self.assertAlmostEqual(7.0, second.timeline_end, places=2)
        finally:
            widget.close()

    def test_auto_align_still_closes_video_gaps(self):
        timeline = EditTimeline()
        first = VideoClip(source_path="one.mp4", source_duration=2.0,
                          trim_start=0.0, trim_end=2.0, timeline_start=0.0)
        second = VideoClip(source_path="two.mp4", source_duration=2.0,
                           trim_start=0.0, trim_end=2.0, timeline_start=2.0)
        timeline.add_video_clip(first, track_idx=0, skip_overlap=True)
        timeline.add_video_clip(second, track_idx=0, skip_overlap=True)

        timeline.remove_video_clip(first.id)

        self.assertAlmostEqual(0.0, second.timeline_start)


if __name__ == "__main__":
    unittest.main()
