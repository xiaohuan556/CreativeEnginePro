import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QEvent, QMimeData, QPointF, Qt
    from PyQt6.QtGui import QColor, QDropEvent, QImage, QMouseEvent, QPainter, QPixmap
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

    def test_insert_drop_over_a_clip_uses_the_selected_placement_mode(self):
        timeline = EditTimeline()
        clip = VideoClip(source_path="existing.mp4", source_duration=10,
                         trim_start=0, trim_end=10, timeline_start=0)
        timeline.add_video_clip(clip, skip_overlap=True)
        widget = TimelineWidget(timeline)
        try:
            canvas = widget._canvas
            track = next(td for td in canvas._tracks if td.kind == "video" and td.idx == 0)
            drop_position = QPointF(canvas._clip_rect(clip, track).center())
            mime = QMimeData()
            mime.setText("new.mp4||video||2.0")
            dropped, replaced = [], []
            widget.drop_media_requested.connect(lambda *args: dropped.append(args))
            widget.replace_video_requested.connect(lambda *args: replaced.append(args))

            for mode in ("insert", "overwrite", "normal"):
                widget._edit_mode_combo.setCurrentIndex(widget._edit_mode_combo.findData(mode))
                canvas.dropEvent(QDropEvent(
                    drop_position, Qt.DropAction.CopyAction, mime,
                    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                ))

            self.assertEqual(2, len(dropped))
            self.assertTrue(all(item[:5] == ("new.mp4", "video", 2.0, "video", 0)
                                for item in dropped))
            self.assertEqual([(clip, "new.mp4")], replaced)
        finally:
            widget.close()

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

            # Dragging must only update the transient canvas position.  The
            # timeline model is committed on release, like Kdenlive's fake
            # move path.
            self.assertAlmostEqual(1.0, first.timeline_start, places=2)
            self.assertAlmostEqual(4.0, second.timeline_start, places=2)

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

    def test_two_occupied_video_tracks_swap_on_drop(self):
        timeline = EditTimeline()
        timeline.auto_align = False
        first = VideoClip(source_path="first.mp4", source_duration=2.0,
                          trim_start=0.0, trim_end=2.0, timeline_start=1.0)
        second = VideoClip(source_path="second.mp4", source_duration=2.0,
                           trim_start=0.0, trim_end=2.0, timeline_start=1.0)
        timeline.add_video_clip(first, track_idx=0, skip_overlap=True)
        timeline.add_video_track()
        timeline.add_video_clip(second, track_idx=1, skip_overlap=True)
        widget = TimelineWidget(timeline)
        try:
            canvas = widget._canvas
            source = next(td for td in canvas._tracks
                          if td.kind == "video" and td.idx == 0)
            target = next(td for td in canvas._tracks
                          if td.kind == "video" and td.idx == 1)
            rect = canvas._clip_rect(first, source)
            press = QPointF(rect.center())
            moved = QPointF(press.x(), target.y + target.h / 2)
            canvas.mousePressEvent(QMouseEvent(
                QEvent.Type.MouseButtonPress, press, press,
                Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier))
            canvas.mouseMoveEvent(QMouseEvent(
                QEvent.Type.MouseMove, moved, moved,
                Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier))
            self.assertIs(canvas._drag_swap_clip, second)
            self.assertIs(first, timeline.video_tracks[0][0])
            canvas.mouseReleaseEvent(QMouseEvent(
                QEvent.Type.MouseButtonRelease, moved, moved,
                Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier))
            self.assertIs(second, timeline.video_tracks[0][0])
            self.assertIs(first, timeline.video_tracks[1][0])
            self.assertAlmostEqual(1.0, first.timeline_start)
            self.assertAlmostEqual(1.0, second.timeline_start)
            timeline.undo()
            self.assertEqual("first.mp4", timeline.video_tracks[0][0].source_path)
            self.assertEqual("second.mp4", timeline.video_tracks[1][0].source_path)
        finally:
            widget.close()

    def test_rejected_cross_track_drop_preserves_original_clip_position(self):
        timeline = EditTimeline()
        timeline.auto_align = False
        first = VideoClip(source_path="first.mp4", source_duration=2.0,
                          trim_start=0.0, trim_end=2.0, timeline_start=1.0)
        occupied = VideoClip(source_path="occupied.mp4", source_duration=2.0,
                             trim_start=0.0, trim_end=2.0, timeline_start=2.0)
        neighbor = VideoClip(source_path="neighbor.mp4", source_duration=2.0,
                             trim_start=0.0, trim_end=2.0, timeline_start=3.0)
        timeline.add_video_clip(first, track_idx=0, skip_overlap=True)
        timeline.add_video_clip(neighbor, track_idx=0, skip_overlap=True)
        timeline.add_video_track()
        timeline.add_video_clip(occupied, track_idx=1, skip_overlap=True)
        widget = TimelineWidget(timeline)
        try:
            canvas = widget._canvas
            source = next(td for td in canvas._tracks
                          if td.kind == "video" and td.idx == 0)
            target = next(td for td in canvas._tracks
                          if td.kind == "video" and td.idx == 1)
            press = QPointF(canvas._clip_rect(first, source).center())
            moved = QPointF(press.x() + 100, target.y + target.h / 2)
            canvas.mousePressEvent(QMouseEvent(
                QEvent.Type.MouseButtonPress, press, press,
                Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier))
            canvas.mouseMoveEvent(QMouseEvent(
                QEvent.Type.MouseMove, moved, moved,
                Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier))
            self.assertTrue(canvas._drag_target_conflict)
            canvas.mouseReleaseEvent(QMouseEvent(
                QEvent.Type.MouseButtonRelease, moved, moved,
                Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier))
            self.assertAlmostEqual(1.0, first.timeline_start)
            self.assertIn(first, timeline.video_tracks[0])
            self.assertIn(occupied, timeline.video_tracks[1])
        finally:
            widget.close()

    def test_drag_top_video_into_upper_drop_zone_creates_visible_track(self):
        timeline = EditTimeline()
        timeline.auto_align = False
        clip = VideoClip(source_path="top.mp4", source_duration=2.0,
                         trim_start=0.0, trim_end=2.0, timeline_start=1.0)
        timeline.add_video_track()
        timeline.add_video_clip(clip, track_idx=1, skip_overlap=True)
        widget = TimelineWidget(timeline)
        try:
            canvas = widget._canvas
            source = next(td for td in canvas._tracks
                          if td.kind == "video" and td.idx == 1)
            press = QPointF(canvas._clip_rect(clip, source).center())
            zone = QPointF(press.x(), sum(canvas._vid_drop_zone) / 2)
            canvas.mousePressEvent(QMouseEvent(
                QEvent.Type.MouseButtonPress, press, press,
                Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier))
            canvas.mouseMoveEvent(QMouseEvent(
                QEvent.Type.MouseMove, zone, zone,
                Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier))
            canvas.mouseReleaseEvent(QMouseEvent(
                QEvent.Type.MouseButtonRelease, zone, zone,
                Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier))
            self.assertEqual(3, len(timeline.video_tracks))
            self.assertIn(clip, timeline.video_tracks[2])
            self.assertTrue(any(td.kind == "video" and td.idx == 2
                                for td in canvas._tracks))
        finally:
            widget.close()

    def test_cross_track_drag_proxy_retains_thumbnail(self):
        timeline = EditTimeline()
        widget = TimelineWidget(timeline)
        try:
            clip = VideoClip(source_path="red.mp4", source_duration=1.0,
                             trim_start=0.0, trim_end=1.0, timeline_start=0.0)
            thumb = QPixmap(20, 20)
            thumb.fill(QColor("red"))
            clip.thumbnail = thumb
            image = QImage(120, 200, QImage.Format.Format_ARGB32)
            image.fill(QColor("blue"))
            painter = QPainter(image)
            rect = widget._canvas._clip_rect(
                clip, next(td for td in widget._canvas._tracks
                           if td.kind == "video" and td.idx == 0))
            widget._canvas._draw_drag_proxy(painter, clip, rect)
            painter.end()
            self.assertGreater(image.pixelColor(95, rect.center().y()).red(),
                               image.pixelColor(95, rect.center().y()).blue())
        finally:
            widget.close()

    def test_cross_track_move_keeps_full_sharp_thumbnail_strips(self):
        timeline = EditTimeline()
        overlay_idx = timeline.add_video_track()
        moving = VideoClip(source_path="moving.mp4", source_duration=2.0,
                           trim_end=2.0, timeline_start=0.0)
        stationary = VideoClip(source_path="still.mp4", source_duration=2.0,
                               trim_end=2.0, timeline_start=3.0)
        timeline.add_video_clip(moving, track_idx=0, skip_overlap=True)
        timeline.add_video_clip(stationary, track_idx=0, skip_overlap=True)
        timeline.add_video_clip(
            VideoClip(source_path="overlay.mp4", source_duration=1.0,
                      trim_end=1.0, timeline_start=10.0),
            track_idx=overlay_idx, skip_overlap=True)
        red = QPixmap(32, 32)
        red.fill(QColor("red"))
        blue = QPixmap(32, 32)
        blue.fill(QColor("blue"))
        moving.thumbnails = [red, blue]
        stationary.thumbnails = [red, blue]
        widget = TimelineWidget(timeline)
        try:
            canvas = widget._canvas
            source = next(td for td in canvas._tracks
                          if td.kind == "video" and td.idx == 0)
            target = next(td for td in canvas._tracks
                          if td.kind == "video" and td.idx == overlay_idx)
            canvas._drag_mode = "move"
            canvas._drag_clip = moving
            canvas._drag_track_desc = source
            canvas._drag_target_track = target
            image = QImage(800, 260, QImage.Format.Format_ARGB32)
            image.fill(QColor("black"))
            painter = QPainter(image)
            canvas._draw_clips(painter, image.rect())
            painter.end()

            for clip, track in ((moving, target), (stationary, source)):
                rect = canvas._clip_rect(clip, track)
                left = image.pixelColor(rect.left() + 15, rect.top() + 8)
                right = image.pixelColor(rect.right() - 15, rect.top() + 8)
                self.assertGreater(left.red(), 240)
                self.assertLess(left.blue(), 15)
                self.assertGreater(right.blue(), 240)
                self.assertLess(right.red(), 15)
        finally:
            widget.close()

    def test_scrubbing_playhead_keeps_full_timeline_thumbnail_strip(self):
        timeline = EditTimeline()
        clip = VideoClip(source_path="strip.mp4", source_duration=2.0,
                         trim_start=0.0, trim_end=2.0, timeline_start=0.0)
        timeline.add_video_clip(clip, track_idx=0, skip_overlap=True)
        red = QPixmap(32, 32)
        red.fill(QColor("red"))
        blue = QPixmap(32, 32)
        blue.fill(QColor("blue"))
        clip.thumbnails = [red, blue]
        widget = TimelineWidget(timeline)
        try:
            canvas = widget._canvas
            canvas._drag_mode = "playhead"
            image = QImage(350, 200, QImage.Format.Format_ARGB32)
            image.fill(QColor("black"))
            painter = QPainter(image)
            canvas._draw_clips(painter, image.rect())
            painter.end()
            track = next(td for td in canvas._tracks
                         if td.kind == "video" and td.idx == 0)
            rect = canvas._clip_rect(clip, track)
            first = image.pixelColor(rect.left() + 15, rect.center().y())
            second = image.pixelColor(rect.right() - 15, rect.center().y())
            self.assertGreater(first.red(), first.blue())
            self.assertGreater(second.blue(), second.red())
        finally:
            widget.close()

    def test_v_hiding_overlay_video_resyncs_its_sound_immediately(self):
        timeline = EditTimeline()
        timeline.add_video_track()
        clip = VideoClip(source_path="overlay.mp4", source_duration=2.0,
                         trim_start=0.0, trim_end=2.0, timeline_start=0.0)
        timeline.add_video_clip(clip, track_idx=1, skip_overlap=True)
        widget = TimelineWidget(timeline)
        class Preview:
            def __init__(self):
                self.synced = []

            def play_all_audio(self, sec):
                self.synced.append(sec)
        preview = Preview()
        try:
            widget._preview_player = preview
            widget._playing = True
            canvas = widget._canvas
            canvas._selected_clip = clip
            canvas._selected_td = next(td for td in canvas._tracks
                                       if td.kind == "video" and td.idx == 1)
            widget._do_toggle_visibility()
            self.assertFalse(clip.visible)
            self.assertEqual([0.0], preview.synced)
            widget._do_toggle_visibility()
            self.assertTrue(clip.visible)
            self.assertEqual([0.0, 0.0], preview.synced)
        finally:
            widget._playing = False
            widget.close()

    def test_v_hiding_video_also_hides_linked_audio(self):
        timeline = EditTimeline()
        video = VideoClip(source_path="video.mp4", source_duration=2.0,
                          trim_start=0.0, trim_end=2.0, timeline_start=0.0)
        audio = AudioClip(source_path="audio.wav", source_duration=2.0,
                          trim_start=0.0, trim_end=2.0, timeline_start=0.0)
        video.link_id = audio.link_id = "linked-av"
        timeline.add_video_clip(video, track_idx=0, skip_overlap=True)
        timeline.add_audio_clip(audio, track_idx=0, skip_overlap=True)
        widget = TimelineWidget(timeline)
        try:
            canvas = widget._canvas
            canvas._selected_clip = video
            canvas._selected_td = next(td for td in canvas._tracks
                                       if td.kind == "video" and td.idx == 0)
            widget._do_toggle_visibility()
            self.assertFalse(video.visible)
            self.assertFalse(audio.visible)
            widget._do_toggle_visibility()
            self.assertTrue(video.visible)
            self.assertTrue(audio.visible)
        finally:
            widget.close()


if __name__ == "__main__":
    unittest.main()
