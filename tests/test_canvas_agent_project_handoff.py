import json
import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_agent_project_opens_in_real_editor_without_file_picker(tmp_path):
    from PIL import Image
    from PyQt6.QtWidgets import QApplication
    from core.edit_engine import EditTimeline, VideoClip, SubtitleBlock
    from core.project_files import VID_SUFFIX, read_project
    from ui.editor_tab import EditorTab

    app = QApplication.instance() or QApplication([])
    source = tmp_path / "picture.png"
    Image.new("RGB", (160, 120), "blue").save(source)
    timeline = EditTimeline()
    timeline.video_tracks[0].append(VideoClip(source_path=str(source), source_duration=1, trim_end=1))
    timeline.add_subtitle(SubtitleBlock(text="可编辑字幕", timeline_start=0, timeline_end=1))
    project = tmp_path / "agent.cep"
    project.write_text(json.dumps({"version": 1, "project_name": "Agent 成片",
        "canvas_ratio": [160, 120], "canvas_custom_size": [160, 120],
        "active_timeline": 0, "timelines": [timeline.to_dict()], "media_library": [str(source)]},
        ensure_ascii=False), encoding="utf-8")
    with patch.object(EditorTab, "_maybe_restore_autosave"), \
            patch("ui.editor_tab.QFileDialog.getOpenFileName", side_effect=AssertionError("unexpected file picker")):
        editor = EditorTab()
        try:
            assert editor.open_canvas_agent_project(str(project))
            assert editor.timeline.video_tracks[0][0].source_path == str(source)
            assert editor.timeline.subtitle_tracks[0][0].text == "可编辑字幕"
            assert editor._project_path == str(project)
            assert editor._custom_size == (160, 120)
            assert not editor._project_dirty
            original = project.read_bytes()
            upgraded = project.with_suffix(VID_SUFFIX)
            with patch("ui.editor_tab.QFileDialog.getSaveFileName",
                       return_value=(str(upgraded), "")) as picker, \
                    patch.object(editor, "_write_autosave"):
                editor._save_project()
            assert picker.call_args.args[2] == str(upgraded)
            assert project.read_bytes() == original
            assert read_project(upgraded, VID_SUFFIX)["project_name"] == "Agent 成片"
            assert editor.open_canvas_agent_project(str(upgraded))
            assert editor.timeline.subtitle_tracks[0][0].text == "可编辑字幕"
        finally:
            editor.close()
            for worker in list(editor._thumb_workers) + list(editor._waveform_workers):
                worker.wait(5000)
            app.processEvents()
