import os
import tempfile
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

from core.compositor import VideoCompositor
from core.edit_engine import EditTimeline, VideoClip
from ui.preview_player import PreviewPlayer


APP = QApplication.instance() or QApplication([])


def _pixels(image):
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return np.frombuffer(image.bits().asstring(image.width() * image.height() * 4),
                         dtype=np.uint8).reshape(image.height(), image.width(), 4)


def _source_image(path):
    image = QImage(64, 36, QImage.Format.Format_RGBA8888)
    for y in range(image.height()):
        for x in range(image.width()):
            color = QColor(0, 210, 0) if x < 20 else QColor(
                50 + x * 2, 20 + y * 5, 100 + x)
            image.setPixelColor(x, y, color)
    assert image.save(str(path))


@pytest.mark.parametrize("effects", [
    {},
    {"scale": 1.2, "rotation": 17.0, "blur_radius": 2.0,
     "pos_x": 11.0, "pos_y": -7.0, "opacity": 0.6},
    {"chroma_key_enabled": True, "mask_enabled": True,
     "mask_type": "circle", "mask_width": 0.7, "mask_height": 0.7},
])
def test_preview_and_export_paint_same_video_pixels(effects):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "source.png"
        _source_image(path)
        clip = VideoClip(
            source_path=str(path), source_duration=2.0,
            trim_start=0.0, trim_end=2.0, **effects)
        timeline = EditTimeline()
        timeline.add_video_clip(clip, skip_overlap=True)
        preview = PreviewPlayer(timeline)
        compositor = VideoCompositor(timeline, (320, 180))
        try:
            preview._canvas_w, preview._canvas_h = 320, 180
            preview._current_sec = 0.5
            preview._clip_src_w, preview._clip_src_h = 64, 36
            preview._last_raw_img = preview._qimage_from_path(str(path))
            preview_image = preview._compose_from_raw(320, 180)
            export_image = compositor.render_frame(0.5)
            delta = np.abs(_pixels(preview_image).astype(np.int16)
                           - _pixels(export_image).astype(np.int16))
            assert delta.max() == 0, (effects, int(np.count_nonzero(delta)))
        finally:
            compositor.close()
            preview.close()


def test_preview_and_export_paint_same_overlay_pixels():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "source.png"
        _source_image(path)
        timeline = EditTimeline()
        main = VideoClip(source_path=str(path), source_duration=2.0,
                         trim_start=0.0, trim_end=2.0)
        timeline.add_video_clip(main, skip_overlap=True)
        overlay_idx = timeline.add_video_track()
        overlay = VideoClip(source_path=str(path), source_duration=2.0,
                            trim_start=0.0, trim_end=2.0,
                            scale=0.5, pos_x=50.0, opacity=0.5)
        timeline.add_video_clip(overlay, track_idx=overlay_idx, skip_overlap=True)
        preview = PreviewPlayer(timeline)
        compositor = VideoCompositor(timeline, (320, 180))
        try:
            preview._canvas_w, preview._canvas_h = 320, 180
            preview._current_sec = 0.5
            preview._clip_src_w, preview._clip_src_h = 64, 36
            preview._last_raw_img = preview._qimage_from_path(str(path))
            preview._last_raw_overlays = [
                (overlay, preview._qimage_from_path(str(path)))]
            preview_image = preview._compose_from_raw(320, 180)
            export_image = compositor.render_frame(0.5)
            delta = np.abs(_pixels(preview_image).astype(np.int16)
                           - _pixels(export_image).astype(np.int16))
            assert delta.max() == 0, int(np.count_nonzero(delta))
        finally:
            compositor.close()
            preview.close()


def test_overlay_only_preview_and_export_match_after_effect_change():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "overlay.png"
        _source_image(path)
        timeline = EditTimeline()
        overlay_idx = timeline.add_video_track()
        overlay = VideoClip(source_path=str(path), source_duration=2.0,
                            trim_start=0.0, trim_end=2.0,
                            scale=0.6, pos_x=27.0, opacity=0.7,
                            chroma_key_enabled=True)
        timeline.add_video_clip(overlay, track_idx=overlay_idx,
                                skip_overlap=True)
        preview = PreviewPlayer(timeline)
        compositor = VideoCompositor(timeline, (320, 180))
        try:
            preview._canvas_w, preview._canvas_h = 320, 180
            preview._current_sec = 0.5
            preview._last_raw_img = preview._alloc_canvas(320, 180)
            preview._last_raw_overlays = [
                (overlay, preview._qimage_from_path(str(path)))]
            preview_image = preview._compose_from_raw(320, 180)
            export_image = compositor.render_frame(0.5)
            delta = np.abs(_pixels(preview_image).astype(np.int16)
                           - _pixels(export_image).astype(np.int16))
            assert delta.max() == 0, int(np.count_nonzero(delta))
        finally:
            compositor.close()
            preview.close()


def test_overlay_only_playhead_frame_matches_export():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "overlay.png"
        _source_image(path)
        timeline = EditTimeline()
        timeline.canvas_bg_color = "#4a1b72"
        overlay_idx = timeline.add_video_track()
        overlay = VideoClip(source_path=str(path), source_duration=2.0,
                            trim_start=0.0, trim_end=2.0,
                            scale=0.6, pos_x=27.0, opacity=0.7)
        timeline.add_video_clip(overlay, track_idx=overlay_idx,
                                skip_overlap=True)
        preview = PreviewPlayer(timeline)
        compositor = VideoCompositor(timeline, (320, 180))
        try:
            preview._aspect_ratio = (16, 9)
            preview._canvas_w, preview._canvas_h = 320, 180
            preview._current_sec = 0.5
            preview._compute_payload(0.5)
            preview._flush_frame(force=True)
            preview_image = preview._screen.pixmap().toImage()
            export_image = compositor.render_frame(0.5)
            assert preview_image.size() == export_image.size()
            delta = np.abs(_pixels(preview_image).astype(np.int16)
                           - _pixels(export_image).astype(np.int16))
            assert delta.max() == 0, int(np.count_nonzero(delta))
        finally:
            compositor.close()
            preview.close()


def test_canvas_background_color_survives_project_and_export():
    timeline = EditTimeline()
    timeline.canvas_bg_color = "#4a1b72"
    restored = EditTimeline.from_dict(timeline.to_dict())
    preview = PreviewPlayer(restored)
    compositor = VideoCompositor(restored, (320, 180))
    try:
        assert restored.canvas_bg_color == "#4a1b72"
        assert preview._canvas_bg_color == "#4a1b72"
        export_image = compositor.render_frame(0.5)
        assert export_image.pixelColor(0, 0) == QColor("#4a1b72")
        preview_image = preview._alloc_canvas(320, 180)
        assert preview_image.pixelColor(0, 0) == QColor("#4a1b72")
    finally:
        compositor.close()
        preview.close()
