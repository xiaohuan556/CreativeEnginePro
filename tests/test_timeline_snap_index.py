import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from core.edit_engine import EditTimeline, VideoClip
from ui.timeline_widget import TimelineCanvas


APP = QApplication.instance() or QApplication([])


def test_large_timeline_snaps_only_to_nearby_points_and_respects_exclusion():
    timeline = EditTimeline()
    clips = [VideoClip(source_path="fixture.mp4", source_duration=0.5,
                       trim_end=0.5, timeline_start=float(i * 2))
             for i in range(1200)]
    timeline.video_tracks[0].extend(clips)
    clips[100].keyframes = {"scale": [(0.4, 1.2)]}
    canvas = TimelineCanvas(timeline)
    try:
        canvas._rebuild_tracks()
        canvas._grid_step = lambda: 0.0
        canvas._ensure_snap_cache()

        assert canvas._snap_boundary_cache == sorted(canvas._snap_boundary_cache)
        assert canvas._nearby_snap_points(
            canvas._snap_boundary_cache, 200.03, 0.08) == [
                (200.0, id(clips[100]))]
        assert canvas._snap_sec(200.03) == 200.0
        assert canvas._snap_sec(200.03, exclude_clip=clips[100]) == 200.03
        assert canvas._snap_to_keyframes(200.35) == 200.4

        clips[100].timeline_start = 3000.0
        canvas._rebuild_tracks()
        assert canvas._snap_sec(200.03) == 200.03
        assert canvas._snap_sec(3000.03) == 3000.0
    finally:
        canvas.close()
