from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ai.creative_agent_bridge import plan_local_edit, resolve_agent_route
from core.edit_engine import VideoClip
from ui.editor_tab import EditorTab


def test_local_agent_parser_refuses_unavailable_operations():
    assert resolve_agent_route("给视频加字幕", media_types=("video",)) == "editor"
    assert plan_local_edit("给视频静音、模糊 12、缩放 150%、添加字幕：你好、淡入淡出") == {
        "import": True, "mute": True, "blur_radius": 12,
        "scale": 1.5, "subtitle": "你好", "fade": True,
    }
    with pytest.raises(ValueError, match="暂不支持"):
        plan_local_edit("自动识别产品并替换画面")


def test_local_agent_applies_effects_to_imported_clip(tmp_path):
    media = tmp_path / "input.mp4"
    media.write_bytes(b"fixture")
    clip = VideoClip(source_path=str(media), source_duration=4,
                     trim_end=4, timeline_start=0)
    subtitles = []
    timeline = SimpleNamespace(video_tracks=[[]], audio_tracks=[[]],
                               changed=SimpleNamespace(emit=Mock()),
                               add_subtitle=lambda block: subtitles.append(block))
    editor = SimpleNamespace(
        timeline=timeline, timeline_widget=SimpleNamespace(
            rebuild_canvas=Mock(), get_playhead=lambda: 0.0),
        preview=SimpleNamespace(seek=Mock()), _mark_dirty=Mock(),
        status_msg=SimpleNamespace(emit=Mock()))

    def import_media(_item):
        timeline.video_tracks[0].append(clip)
        return True

    editor.import_canvas_media = import_media
    result = EditorTab.apply_canvas_agent_edit(editor, {
        "items": [{"path": str(media), "media_type": "video"}],
        "edit_plan": plan_local_edit("静音、模糊 12、缩放 150%、添加字幕：你好、淡入淡出"),
    })
    assert result == 1
    assert clip.mute and clip.blur_radius == 12 and clip.scale == 1.5
    assert clip.keyframes["opacity"] == [
        (0.0, 0.0), (0.5, 1.0), (3.5, 1.0), (4.0, 0.0)]
    assert len(subtitles) == 1 and subtitles[0].text == "你好"
    assert subtitles[0].timeline_end == 4
    editor.timeline_widget.rebuild_canvas.assert_called_once()
