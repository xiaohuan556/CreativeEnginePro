import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QApplication

from core.edit_engine import EditTimeline, VideoClip
from ui.timeline_widget import TimelineWidget


APP = QApplication.instance() or QApplication([])


def test_zoom_keeps_time_under_pointer_for_in_and_out():
    timeline = EditTimeline()
    timeline.add_video_clip(
        VideoClip(source_path="fixture.mp4", source_duration=120,
                  trim_end=120), skip_overlap=True)
    widget = TimelineWidget(timeline)
    widget.resize(800, 300)
    widget.show()
    APP.processEvents()
    try:
        canvas = widget._canvas
        scroll = widget._scroll.horizontalScrollBar()
        scroll.setValue(3000)
        pointer_x = 160

        def time_under_pointer():
            return canvas._x_to_sec(scroll.value() + pointer_x)

        original = time_under_pointer()
        widget._zoom_at(160.0, pointer_x)
        assert abs(time_under_pointer() - original) < 1 / canvas.zoom
        widget._zoom_at(70.0, pointer_x)
        assert abs(time_under_pointer() - original) < 1 / canvas.zoom

        wheel_x = scroll.value() + pointer_x
        wheel = QWheelEvent(
            QPointF(wheel_x, 100), QPointF(wheel_x, 100),
            QPoint(0, 0), QPoint(0, 120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
            Qt.ScrollPhase.ScrollUpdate, False)
        canvas.wheelEvent(wheel)
        assert wheel.isAccepted()
        assert abs(time_under_pointer() - original) < 1 / canvas.zoom
    finally:
        widget.close()
