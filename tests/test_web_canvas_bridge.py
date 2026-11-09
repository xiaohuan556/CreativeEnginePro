from copy import deepcopy

import pytest

from ai.creative_agent_bridge import resolve_agent_route
from ai.web_canvas_bridge import desktop_canvas_to_web, web_canvas_to_desktop


def sample_web_canvas():
    return {
        "protocol": "creative-engine-canvas", "version": 1,
        "storyboard": {"title": "原故事", "shots": []},
        "desktopSource": {"projectId": "server-123"},
        "nodes": [
            {"id": "video-1", "type": "studio", "position": {"x": 220, "y": 40},
             "data": {"specKey": "video", "desktopType": "video_node",
                      "title": "主视频", "desktopPayload": {"path": "D:/media/main.mov", "content": "原文"}}},
            {"id": "agent-1", "type": "studio", "position": {"x": 550, "y": 60},
             "data": {"specKey": "ai_editor", "desktopType": "ai_editor_node",
                      "title": "通用创作 Agent", "desktopPayload": {
                          "content": "送到剪辑台", "agent_route": "editor",
                          "agent_automation_enabled": True}}},
        ],
        "edges": [{"id": "e1", "source": "video-1", "target": "agent-1",
                   "relation": "video_source"}],
    }


def test_web_import_preserves_agent_configuration_and_links():
    source = sample_web_canvas()
    project = web_canvas_to_desktop(source, title="网页工程")
    assert project["storyboard"]["title"] == "网页工程"
    assert project["storyboard"]["id"].startswith("canvas_")
    assert project["canvas"]["video-1"] == [220.0, 40.0]
    assert project["canvas"]["__workflow_edges__"] == [
        {"id": "e1", "source": "video-1", "target": "agent-1", "type": "video_source"}]
    agent = next(v for v in project["canvas"]["__custom_nodes__"] if v["id"] == "agent-1")
    assert agent["type"] == "ai_editor_node"
    assert agent["agent_automation_enabled"] is True
    assert agent["content"] == "送到剪辑台"
    assert project["canvas"]["__web_source__"]["projectId"] == "server-123"
    assert source == sample_web_canvas()


def test_web_export_also_matches_the_existing_web_desktop_import_contract():
    project = web_canvas_to_desktop(sample_web_canvas())
    project["canvas"]["__canvas_groups__"] = [{"id": "g", "group_name": "素材", "group_nodes": ["video-1", "agent-1"], "collapsed": True}]
    exported = desktop_canvas_to_web(project["storyboard"], project["canvas"])
    # desktopProjectToWeb in web/lib/canvas-protocol.ts requires this envelope.
    assert exported["format"] == "creative-engine-production-project"
    assert isinstance(exported["canvas"]["__custom_nodes__"], list)
    assert isinstance(exported["canvas"]["__workflow_edges__"], list)
    assert exported["protocol"] == "creative-engine-canvas"
    group = next(row for row in exported["canvas"]["__custom_nodes__"] if row["id"] == "g")
    assert group["canvas_group"] is True and group["collapsed"] is True
    for _ in range(3):
        restored = web_canvas_to_desktop(exported)
        exported = desktop_canvas_to_web(restored["storyboard"], restored["canvas"])
        assert "canvas" not in restored["canvas"]["__web_document_extras__"]
        assert "__web_document_extras__" not in exported["canvas"]


def test_web_import_maps_supported_variants_and_retains_server_only_nodes():
    source = sample_web_canvas()
    source["nodes"].append({
        "id": "style", "position": {"x": 0, "y": 0},
        "data": {"desktopType": "video_style_transfer_node", "specKey": "video_style_transfer"}})
    source["nodes"].append({
        "id": "ae", "position": {"x": 0, "y": 0},
        "data": {"desktopType": "ae_template_render_node", "specKey": "ae_template_render",
                 "title": "AE 尾页", "desktopPayload": {"ae_template_id": "outro"}}})
    records = {v["id"]: v for v in web_canvas_to_desktop(source)["canvas"]["__custom_nodes__"]}
    assert records["style"]["type"] == "video_node"
    assert records["style"]["video_style_transfer"] is True
    assert "ae" not in records


def test_web_import_rejects_future_version_and_duplicate_ids():
    source = sample_web_canvas()
    source["version"] = 2
    with pytest.raises(ValueError, match="版本"):
        web_canvas_to_desktop(source)
    source["version"] = 1
    source["nodes"].append(deepcopy(source["nodes"][0]))
    with pytest.raises(ValueError, match="重复"):
        web_canvas_to_desktop(source)


def test_agent_route_only_returns_desktop_backed_actions():
    assert resolve_agent_route("把视频送到剪辑台") == "editor"
    assert resolve_agent_route("生成视频") == "video"
    assert resolve_agent_route("生成旁白配音") == "audio"
    assert resolve_agent_route("给视频加字幕", media_types=("video",)) == "editor"
    assert resolve_agent_route("优化一下", media_types=("video",)) == ""
    assert resolve_agent_route("优化一下", selected="image") == "image"


def test_web_round_trip_retains_web_metadata_and_desktop_edits():
    source = sample_web_canvas()
    source["nodes"][0]["data"]["reference_settings"] = [{"role": "content_video"}]
    source["nodes"][0]["dragHandle"] = ".header"
    source["edges"][0]["sourceHandle"] = "output"
    source["suppressedNodeIds"] = ["legacy"]
    project = web_canvas_to_desktop(source)
    agent = next(record for record in project["canvas"]["__custom_nodes__"]
                 if record["id"] == "agent-1")
    agent["content"] = "生成视频"
    exported = desktop_canvas_to_web(project["storyboard"], project["canvas"])
    assert exported["nodes"][0]["data"]["reference_settings"] == [{"role": "content_video"}]
    assert exported["nodes"][0]["dragHandle"] == ".header"
    assert exported["edges"][0]["sourceHandle"] == "output"
    assert exported["suppressedNodeIds"] == ["legacy"]
    restored_agent = next(node for node in exported["nodes"] if node["id"] == "agent-1")
    assert restored_agent["data"]["desktopPayload"]["content"] == "生成视频"
    assert restored_agent["data"]["description"] == "生成视频"


