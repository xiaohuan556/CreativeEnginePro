from ai.canvas_node_cleanup import prune_retired_canvas_nodes
from ai.canvas_registry import CREATION_ITEMS, NODE_SPECS, creation_payload
from ai.web_canvas_bridge import web_canvas_to_desktop


def test_retired_tasks_and_fake_progress_cards_removed_but_real_results_kept(tmp_path):
    media = tmp_path / "result.mp4"
    media.write_bytes(b"result")
    canvas = {"__custom_nodes__": [
        {"id": "source", "type": "image_node", "path": "original.png"},
        {"id": "agent", "type": "ai_editor_node", "agent_run": {
            "plan": {"steps": [{"id": "step_1", "tool": "inpaint"}]}}},
        {"id": "old-tool", "type": "ai_editor_node", "agent_tool": "inpaint"},
        {"id": "old-progress", "type": "text_node", "agent_parent_id": "agent"},
        {"id": "result", "type": "video_node", "source_node_id": "agent", "path": str(media)},
        {"id": "real-script", "type": "text_node", "content": "真正的剧本"}],
        "old-tool": [0, 0], "old-progress": [100, 0],
        "__workflow_edges__": [{"source": "source", "target": "old-tool"},
                               {"source": "agent", "target": "old-progress"},
                               {"source": "old-progress", "target": "result"}]}
    removed = prune_retired_canvas_nodes(canvas)
    assert removed == {"old-tool", "old-progress"}
    assert {row["id"] for row in canvas["__custom_nodes__"]} == {"source", "agent", "result", "real-script"}
    assert canvas["__workflow_edges__"] == [{"source": "agent", "target": "result", "type": "agent_result"}]
    assert media.is_file()
    assert "old-tool" not in canvas and "old-progress" not in canvas
    assert "plan" not in canvas["__custom_nodes__"][1]["agent_run"]
    assert not prune_retired_canvas_nodes(canvas)


def test_retired_and_unimplemented_web_nodes_are_not_imported():
    document = {"protocol": "creative-engine-canvas", "version": 1,
        "nodes": [{"id": "ae", "position": {"x": 0, "y": 0},
                   "data": {"specKey": "ae_template_render", "desktopType": "ae_template_render_node"}},
                  {"id": "unsupported", "position": {"x": 0, "y": 0},
                   "data": {"desktopType": "nonexistent_tool"}},
                  {"id": "text", "position": {"x": 0, "y": 0},
                   "data": {"desktopType": "text_node", "specKey": "text"}}],
        "edges": [{"source": "text", "target": "ae"}]}
    project = web_canvas_to_desktop(document)
    assert [row["id"] for row in project["canvas"]["__custom_nodes__"]] == ["text"]
    assert not project["canvas"]["__workflow_edges__"]


def test_creation_catalog_contains_no_retired_tools():
    removed = {"inpaint_tool", "video_packaging_tool", "media_download_tool", "ae_template_render", "ae_control_center_render"}
    assert not removed.intersection(NODE_SPECS)
    assert not removed.intersection(row[0] for row in CREATION_ITEMS)
    node_type, payload = creation_payload("image_upscale_tool")
    assert node_type == "ai_editor_node" and payload["agent_tool"] == "image_upscale"
