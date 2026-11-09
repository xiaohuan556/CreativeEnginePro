import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

from core.edit_engine import EditTimeline, VideoClip
from ui.preview_player import PreviewPlayer


APP = QApplication.instance() or QApplication([])


def _timeline_with_image(path, color):
    image = QImage(64, 36, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    assert image.save(str(path))
    timeline = EditTimeline()
    timeline.add_video_clip(
        VideoClip(source_path=str(path), source_duration=1, trim_end=1),
        skip_overlap=True)
    return timeline


def test_switch_rebinds_cache_signals_and_replaces_visible_frame(tmp_path):
    first = _timeline_with_image(tmp_path / "first.png", "#d93030")
    second = _timeline_with_image(tmp_path / "second.png", "#36bc50")
    preview = PreviewPlayer(first)
    try:
        preview._aspect_ratio = (16, 9)
        preview._canvas_w, preview._canvas_h = 320, 180
        preview._current_sec = 0.5
        preview._compute_payload(0.5)
        preview._flush_frame(force=True)
        before = preview._screen.pixmap().toImage().pixelColor(160, 90)
        assert before.red() > before.green()

        old_revision = preview._timeline_revision
        preview.set_timeline(second)
        revision = preview._timeline_revision
        assert not preview._payload_ring
        assert preview._compute_payload(
            0.5, expected_revision=old_revision) is None
        assert preview._pending_sec is None
        first.changed.emit()
        assert preview._timeline_revision == revision
        second.changed.emit()
        assert preview._timeline_revision == revision + 1

        preview._compute_payload(0.5)
        preview._flush_frame(force=True)
        after = preview._screen.pixmap().toImage().pixelColor(160, 90)
        assert after.green() > after.red()

        preview.set_timeline(first)
        revision = preview._timeline_revision
        first.changed.emit()
        assert preview._timeline_revision == revision + 1
        second.changed.emit()
        assert preview._timeline_revision == revision + 1
    finally:
        preview.close()
