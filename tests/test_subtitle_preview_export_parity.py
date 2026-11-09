import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

from core.compositor import VideoCompositor
from core.edit_engine import EditTimeline, SubtitleBlock
from ui.preview_player import PreviewPlayer


APP = QApplication.instance() or QApplication([])


def _pixels(image):
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return np.frombuffer(image.bits().asstring(image.width() * image.height() * 4),
                         dtype=np.uint8).reshape(image.height(), image.width(), 4)


def _assert_same_frame(block, sec=0.5):
    timeline = EditTimeline()
    timeline.add_subtitle(block)
    preview = PreviewPlayer(timeline)
    compositor = VideoCompositor(timeline, (320, 180))
    try:
        preview._current_sec = sec
        base = QImage(320, 180, QImage.Format.Format_ARGB32)
        base.fill(QColor("#000000"))
        preview_image = preview._overlay_subtitles(base, [block], 320, 180)
        export_image = compositor.render_frame(sec)
        delta = np.abs(_pixels(preview_image).astype(np.int16)
                       - _pixels(export_image).astype(np.int16))
        assert delta.max() == 0, (
            int(np.count_nonzero(delta)),
            int(_pixels(preview_image)[:, :, :3].sum()),
            int(_pixels(export_image)[:, :, :3].sum()),
        )
    finally:
        compositor.close()
        preview.close()


def test_outlined_subtitle_preview_matches_compositor_frame():
    _assert_same_frame(SubtitleBlock(
        text="字幕 Test", timeline_start=0.0, timeline_end=2.0,
        font_family="Arial", font_size=24, outline_width=3,
        outline_color="#000000", color="#ffffff", pos_x=0.0, pos_y=0.0,
    ))


def test_animated_outlined_subtitle_matches_compositor_frame():
    _assert_same_frame(SubtitleBlock(
        text="hello world", timeline_start=0.0, timeline_end=2.0,
        font_family="Arial", font_size=24, outline_width=2,
        word_animation=True, word_anim_duration=0.4,
        color="#ffff00", pos_x=0.0, pos_y=0.0,
    ), sec=0.2)


def test_rotated_filled_subtitle_matches_compositor_frame():
    _assert_same_frame(SubtitleBlock(
        text="标题", timeline_start=0.0, timeline_end=2.0,
        font_family="Arial", font_size=24, fill_enabled=True,
        background_color="#8844bb", border_radius=8,
        rotation=15.0, pos_x=0.0, pos_y=0.0,
    ))


def test_keyframed_translucent_subtitle_matches_compositor_frame():
    _assert_same_frame(SubtitleBlock(
        text="Move", timeline_start=0.0, timeline_end=2.0,
        font_family="Arial", font_size=24, outline_width=2,
        opacity=0.5, pos_x=0.0, pos_y=0.0,
        keyframes={"pos_x": [(0.0, -0.3), (1.0, 0.3)],
                   "scale": [(0.0, 1.0), (1.0, 1.5)]},
    ))
