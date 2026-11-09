import os
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QColor, QImage
from core.compositor import VideoCompositor
from core.edit_engine import EditTimeline, SubtitleBlock, VideoClip
from core.preview_effects import evaluate_video_effects


APP = QApplication.instance() or QApplication([])


def test_compositor_uses_shared_video_effect_state():
    clip = VideoClip(
        timeline_start=1.0,
        trim_end=3.0,
        opacity=0.8,
        keyframes={
            "scale": [(0.0, 1.0), (2.0, 2.0)],
            "opacity": [(0.0, 0.8), (2.0, 0.2)],
        },
    )
    compositor = VideoCompositor(EditTimeline(), (160, 90))
    try:
        expected = evaluate_video_effects(clip, 2.0)
        assert compositor._clip_opacity(clip, 2.0) == expected["opacity"]
    finally:
        compositor.close()


def test_compositor_renders_manual_word_animation_like_preview():
    block = SubtitleBlock(
        text="hello world", timeline_start=0.0, timeline_end=2.0,
        word_animation=True, from_asr=False,
    )
    timeline = EditTimeline()
    timeline.add_subtitle(block)
    compositor = VideoCompositor(timeline, (320, 180))
    try:
        frame = compositor.render_frame(0.5)
        assert not frame.isNull()
        assert frame.width() == 320
        assert frame.height() == 180
    finally:
        compositor.close()


def test_export_reuses_still_image_and_frozen_transition_frame():
    with tempfile.TemporaryDirectory() as directory:
        first_path = Path(directory) / "first.png"
        second_path = Path(directory) / "second.png"
        for path, color in ((first_path, "#da3050"), (second_path, "#317cce")):
            image = QImage(64, 36, QImage.Format.Format_RGB32)
            image.fill(QColor(color))
            assert image.save(str(path))
        timeline = EditTimeline()
        first = VideoClip(source_path=str(first_path), source_duration=1,
                          trim_end=1, out_transition={"type": "fade", "duration": 0.5})
        second = VideoClip(source_path=str(second_path), source_duration=1,
                           trim_end=1, timeline_start=1)
        timeline.add_video_clip(first, skip_overlap=True)
        timeline.add_video_clip(second, skip_overlap=True)
        compositor = VideoCompositor(timeline, (320, 180))
        try:
            with patch.object(compositor, "_extract_frame",
                              wraps=compositor._extract_frame) as extracted:
                for sec in (1.1, 1.2, 1.3):
                    assert not compositor.render_frame(sec).isNull()
            assert sum(call.args[0] is first for call in extracted.call_args_list) == 1
            assert sum(call.args[0] is second for call in extracted.call_args_list) == 3
            assert len(compositor._image_frames) == 2
            assert len(compositor._transition_freeze) == 1
        finally:
            compositor.close()

