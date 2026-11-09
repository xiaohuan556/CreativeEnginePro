import zipfile
import pytest

from ai.canvas_selection import (
    resolve_portable_media, selected_project, write_selection_archive, write_result_archive,
)
from core.project_files import AI_SUFFIX, read_project


def test_selection_archive_contains_only_selected_graph_and_portable_media(tmp_path):
    media = tmp_path / "frame.png"
    media.write_bytes(b"fixture-image")
    board = {"id": "old", "title": "完整工程", "shots": []}
    canvas = {
        "__custom_nodes__": [
            {"id": "a", "type": "image_node", "title": "图", "path": str(media)},
            {"id": "b", "type": "ai_editor_node", "title": "Agent", "content": "生成视频"},
            {"id": "c", "type": "video_node", "title": "不选", "path": ""},
        ],
        "__workflow_edges__": [
            {"source": "a", "target": "b", "type": "reference"},
            {"source": "b", "target": "c", "type": "workflow"},
        ],
        "a": [100, 80], "b": [400, 80], "c": [700, 80],
    }
    project = selected_project(board, canvas, {"a", "b"})
    assert [row["id"] for row in project["canvas"]["__custom_nodes__"]] == ["a", "b"]
    assert len(project["canvas"]["__workflow_edges__"]) == 1
    archive_path = tmp_path / "selection.zip"
    result = write_selection_archive(archive_path, project)
    assert result == {"nodes": 2, "edges": 1, "media": 1}
    with zipfile.ZipFile(archive_path) as archive:
        assert "selection.xiaohuanai" in archive.namelist()
        assert "selection.webcanvas.json" in archive.namelist()
        assert "media/001_frame.png" in archive.namelist()
        with archive.open("selection.xiaohuanai") as stream:
            saved = read_project(stream, AI_SUFFIX)
        assert saved["canvas"]["__custom_nodes__"][0]["path"] == "media/001_frame.png"
        assert saved["media_manifest"][0]["path"] == "media/001_frame.png"
        extracted = tmp_path / "unpacked"
        archive.extractall(extracted)
    restored = resolve_portable_media(saved, extracted)
    assert restored["canvas"]["__custom_nodes__"][0]["path"] == str(
        extracted / "media" / "001_frame.png")
    assert board["id"] == "old"
    assert canvas["__custom_nodes__"][0]["path"] == str(media)


def test_selection_keeps_groups_and_editor_settings_without_unselected_members():
    canvas = {
        "__custom_nodes__": [{"id": "a", "type": "image_node"}, {"id": "b", "type": "video_node"},
                             {"id": "flow", "type": "workflow_group", "group_nodes": ["a", "b", "outside"]}],
        "__canvas_groups__": [{"id": "g1", "group_name": "主分支", "collapsed": True, "group_nodes": ["a", "b", "outside"]},
                              {"id": "g2", "group_nodes": ["outside", "a"]}],
        "__inline_editor_heights__": {"a": 240, "outside": 520},
        "__inline_text_overrides__": {"b": "视频要求", "outside": "不导出"},
    }
    result = selected_project({"id": "original", "title": "工程"}, canvas, {"a", "b", "flow"})["canvas"]
    assert result["__canvas_groups__"] == [{"id": "g1", "group_name": "主分支", "collapsed": True, "group_nodes": ["a", "b"]}]
    assert result["__custom_nodes__"][-1]["group_nodes"] == ["a", "b"]
    assert result["__inline_editor_heights__"] == {"a": 240}
    assert result["__inline_text_overrides__"] == {"b": "视频要求"}
    assert canvas["__canvas_groups__"][0]["group_nodes"] == ["a", "b", "outside"]


def test_result_archive_contains_exactly_five_videos_and_no_project_files(tmp_path):
    videos = [tmp_path / f"视频{index}.mov" for index in range(5)]
    for index, video in enumerate(videos):
        video.write_bytes(f"video-{index}".encode())
    (tmp_path / "thumbnail.png").write_bytes(b"thumbnail")
    target = tmp_path / "results.zip"
    assert write_result_archive(target, [str(path) for path in videos]) == {"media": 5, "videos": 5}
    with zipfile.ZipFile(target) as archive:
        assert archive.namelist() == [path.name for path in videos]
        assert [archive.read(path.name) for path in videos] == [path.read_bytes() for path in videos]


def test_result_archive_keeps_same_named_files_and_deduplicates_same_path(tmp_path):
    a, b = tmp_path / "a" / "video.mp4", tmp_path / "b" / "VIDEO.mp4"
    a.parent.mkdir()
    b.parent.mkdir()
    a.write_bytes(b"first")
    b.write_bytes(b"second")
    target = tmp_path / "results.zip"
    result = write_result_archive(target, [str(a), str(b), str(a)])
    assert result["media"] == 2
    with zipfile.ZipFile(target) as archive:
        assert archive.namelist() == ["video.mp4", "VIDEO_2.mp4"]
        assert archive.read("video.mp4") == b"first"
        assert archive.read("VIDEO_2.mp4") == b"second"


def test_missing_result_does_not_replace_existing_archive(tmp_path):
    target = tmp_path / "results.zip"
    target.write_bytes(b"previous-export")
    with pytest.raises(ValueError, match="不存在"):
        write_result_archive(target, [str(tmp_path / "missing.mp4")])
    assert target.read_bytes() == b"previous-export"
    assert not target.with_suffix(".zip.tmp").exists()
