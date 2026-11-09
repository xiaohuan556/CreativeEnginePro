import os
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ui.editor_tab import EditorTab


def _editor_stub(*, playing=False, attr="scale", track="video"):
    preview = Mock()
    timeline_widget = SimpleNamespace(
        get_playhead=lambda: 1.0,
        is_playing=lambda: playing,
        _canvas=Mock(),
    )
    editor = SimpleNamespace(
        props_panel=SimpleNamespace(_track=track, _last_property_attr=attr),
        timeline_widget=timeline_widget,
        preview=preview,
        _mark_dirty=Mock(),
        _prop_debounce=Mock(),
    )
    editor._prop_debounce.isActive.return_value = False
    return editor


def test_effect_drag_does_not_repaint_timeline_or_recompose_old_playback_frame():
    editor = _editor_stub(playing=True)
    EditorTab._on_property_changed(editor)
    assert not editor.timeline_widget._canvas.update.called

    EditorTab._do_property_seek(editor)
    editor.preview.invalidate_overlay_cache.assert_called_once()
    editor.preview._recompose_overlays.assert_not_called()
    editor.preview.seek.assert_not_called()


def test_paused_effect_drag_recomposes_cached_frame_and_speed_refreshes_index():
    editor = _editor_stub()
    editor.preview._recompose_overlays.return_value = True
    EditorTab._do_property_seek(editor)
    editor.preview._recompose_overlays.assert_called_once_with(include_video=True)
    editor.preview.seek.assert_not_called()

    editor.props_panel._last_property_attr = "speed"
    EditorTab._on_property_changed(editor)
    editor.timeline_widget._canvas.update.assert_called_once()
    EditorTab._do_property_seek(editor)
    editor.preview.rebuild_playback_index.assert_called_once()
