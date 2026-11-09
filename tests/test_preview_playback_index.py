import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

from core.edit_engine import EditTimeline, VideoClip
from ui.preview_player import PreviewPlayer


APP = QApplication.instance() or QApplication([])


def test_multitrack_preview_finds_late_clip_and_rebuilds_after_edit(tmp_path):
    source = tmp_path / "frame.png"
    frame = QImage(32, 18, QImage.Format.Format_RGB32)
    frame.fill(QColor("#4080c0"))
    assert frame.save(str(source))

    timeline = EditTimeline()
    clips = [VideoClip(source_path=str(source), source_duration=1,
                       trim_end=1, timeline_start=float(i * 2))
             for i in range(1000)]
    timeline.video_tracks[0].extend(clips)
    overlay_track = timeline.add_video_track()
    overlay = VideoClip(source_path=str(source), source_duration=1,
                        trim_end=1, timeline_start=1800.0)
    timeline.video_tracks[overlay_track].append(overlay)
    preview = PreviewPlayer(timeline)
    try:
        payload = preview._compute_payload(1800.5)
        assert payload["clip"] is clips[900]
        assert [item[0] for item in payload["ovs"]] == [overlay]

        clips[900].timeline_start = 2200.0
        timeline.changed.emit()
        payload = preview._compute_payload(1800.5)
        assert payload["clip"] is None
        assert [item[0] for item in payload["ovs"]] == [overlay]
    finally:
        preview.close()
