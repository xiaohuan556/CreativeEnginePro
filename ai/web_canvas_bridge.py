"""Import the server Web canvas document into the desktop production model.

The Web canvas uses React Flow coordinates and stores the desktop node payload
inside each node's ``data.desktopPayload``.  This module has no Qt dependency
so project compatibility can be checked before switching the live canvas.
"""
from __future__ import annotations

from copy import deepcopy
from math import isfinite
from pathlib import Path
from uuid import uuid4

from ai.canvas_registry import NODE_SPECS, LOCAL_TOOL_SPECS
from ai.canvas_node_cleanup import prune_retired_canvas_nodes


WEB_CANVAS_PROTOCOL = "creative-engine-canvas"
SUPPORTED_WEB_VERSION = 1
NATIVE_DISPLAY_TYPES = {
    "workflow_group", "generation_task", "shot_take", "asset_view",
    "asset_take", "director", "scene", "character", "element",
}


def web_canvas_to_desktop(document: dict, *, title: str = "") -> dict:
    if not isinstance(document, dict) or document.get("protocol") != WEB_CANVAS_PROTOCOL:
        raise ValueError("不是受支持的网页 AI 画布工程。")
    version = document.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or not 0 < version <= SUPPORTED_WEB_VERSION:
        raise ValueError("网页 AI 画布工程版本不受支持。")
    if not isinstance(document.get("nodes"), list) or not isinstance(document.get("edges"), list):
        raise ValueError("网页 AI 画布工程缺少节点或连线。")

    board = deepcopy(document.get("storyboard")) if isinstance(document.get("storyboard"), dict) else {}
    source = document.get("desktopSource") if isinstance(document.get("desktopSource"), dict) else {}
    board["id"] = f"canvas_{uuid4().hex[:12]}"
    board["title"] = str(title or document.get("title") or board.get("title") or "网页 AI 画布导入")
    if not isinstance(board.get("shots"), list):
        board["shots"] = []
    canvas = {"__custom_nodes__": [], "__workflow_edges__": [],
              "__web_source__": deepcopy(source),
              "__web_protocol_version__": version,
              "__web_document_extras__": deepcopy({key: value for key, value in document.items()
                                                    if key not in {"protocol", "version", "title", "nodes", "edges", "storyboard", "desktopSource", "suppressedNodeIds", "suppressedProductionRunIds", "format", "canvas", "project_id", "media_manifest"}}),
              "__web_edges__": deepcopy(document["edges"]),
              "__web_shot_nodes__": [],
              "__web_suppressed_node_ids__": deepcopy(document.get("suppressedNodeIds") or []),
              "__web_suppressed_production_run_ids__": deepcopy(document.get("suppressedProductionRunIds") or [])}
    ids = set()
    for index, node in enumerate(document["nodes"]):
        if not isinstance(node, dict) or not isinstance(node.get("data"), dict):
            continue
        node_id = str(node.get("id") or "").strip()
        if not node_id or node_id in ids:
            raise ValueError("网页工程包含空白或重复的节点 ID。")
        ids.add(node_id)
        data = node["data"]
        # desktopProjectToWeb also serializes storyboard shots as view nodes.
        # They are reconstructed from storyboard.shots by the desktop canvas.
        if node_id.startswith("shot:") and isinstance(data.get("desktopPayload"), dict) and isinstance(data["desktopPayload"].get("shot"), dict):
            canvas["__web_shot_nodes__"].append(deepcopy(node))
            shot = deepcopy(data["desktopPayload"]["shot"])
            if not any(str(value.get("id")) == str(shot.get("id")) for value in board["shots"] if isinstance(value, dict)):
                board["shots"].append(shot)
        else:
            spec_key = str(data.get("specKey") or "")
            spec = NODE_SPECS.get(spec_key, {})
            payload = deepcopy(data.get("desktopPayload")) if isinstance(data.get("desktopPayload"), dict) else {}
            # Fresh Web nodes have no desktopPayload; use the shared defaults.
            record = deepcopy(spec.get("defaults") or {})
            record.update(payload)
            record["id"] = node_id
            web_type = str(data.get("desktopType") or record.get("type") or spec.get("node_type") or "web_node")
            desktop_type = web_type
            if spec_key in LOCAL_TOOL_SPECS or web_type == "image_upscale_tool":
                tool_key = spec_key if spec_key in LOCAL_TOOL_SPECS else web_type.removesuffix("_node")
                if tool_key in LOCAL_TOOL_SPECS:
                    defaults = NODE_SPECS[tool_key]["defaults"]
                    record = {**deepcopy(defaults), **record}
                    record["agent_tool"] = defaults["agent_tool"]
                record["web_desktop_type"] = web_type
                desktop_type = "ai_editor_node"
            elif web_type == "video_style_transfer_node":
                desktop_type = "video_node"
                record["video_style_transfer"] = True
            elif web_type == "voice_clone_node":
                desktop_type = "audio_node"
                record["voice_clone"] = True
            elif web_type not in ({value["node_type"] for value in NODE_SPECS.values()}
                                    | NATIVE_DISPLAY_TYPES):
                desktop_type = "web_node"
                record["web_desktop_type"] = web_type
                record["status"] = "当前桌面版本未实现此网页节点"
            record["type"] = desktop_type
            record["title"] = str(data.get("title") or record.get("title") or spec.get("title") or "网页节点")
            record["content"] = str(data.get("description") or record.get("content") or "")
            record.setdefault("path", "")
            record["status"] = str(data.get("status") or record.get("status") or "")
            if desktop_type == "web_node":
                record["status"] = "当前桌面版本未实现此网页节点"
            # Preserve Web-specific node configuration for a future round trip.
            record["web_canvas_data"] = deepcopy({key: value for key, value in data.items() if key != "desktopPayload"})
            record["web_canvas_node"] = deepcopy({key: value for key, value in node.items()
                                                   if key not in {"position", "data"}})
            canvas["__custom_nodes__"].append(record)
        position = node.get("position") if isinstance(node.get("position"), dict) else {}
        x, y = position.get("x"), position.get("y")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and isfinite(v) for v in (x, y)):
            x, y = 120 + index % 4 * 340, 100 + index // 4 * 240
        canvas[node_id] = [float(x), float(y)]

    for edge in document["edges"]:
        if not isinstance(edge, dict):
            continue
        start, end = str(edge.get("source") or ""), str(edge.get("target") or "")
        if start in ids and end in ids and start != end:
            edge_data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
            canvas["__workflow_edges__"].append({
                "id": str(edge.get("id") or f"web-edge-{len(canvas['__workflow_edges__'])}"),
                "source": start, "target": end,
                "type": str(edge.get("relation") or edge_data.get("relation") or
                            edge.get("type") or "workflow"),
            })
    for record in canvas["__custom_nodes__"]:
        outputs = record.get("output_asset_ids")
        if not isinstance(outputs, (list, tuple)):
            outputs = []
        if (record.get("type") != "web_node" and
                (record.get("asset_id") or outputs) and
                not Path(str(record.get("path") or "")).is_file()):
            record["status"] = "缺少本地素材 · 请右键关联文件"
    prune_retired_canvas_nodes(canvas)
    manifest = []
    for record in canvas["__custom_nodes__"]:
        path = str(record.get("path") or "")
        if path and not path.startswith(("http://", "https://")) and Path(path).suffix.lower() in {
                ".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov",
                ".mkv", ".webm", ".wav", ".mp3", ".m4a", ".flac"}:
            local = Path(path)
            manifest.append({"path": path, "exists": local.is_file(),
                             "size": local.stat().st_size if local.is_file() else 0})
    return {"storyboard": board, "canvas": canvas, "title": board["title"],
            "project_id": board["id"], "media_manifest": manifest}


def desktop_canvas_to_web(storyboard: dict, canvas: dict) -> dict:
    """Export edited desktop nodes while restoring Web-only presentation data."""
    if not isinstance(storyboard, dict) or not isinstance(canvas, dict):
        raise ValueError("桌面工程缺少故事板或画布数据。")
    document = deepcopy(canvas.get("__web_document_extras__") or {})
    document.update({
        "protocol": WEB_CANVAS_PROTOCOL,
        "version": SUPPORTED_WEB_VERSION,
        "title": str(storyboard.get("title") or "桌面 AI 画布"),
        "storyboard": deepcopy(storyboard),
        "desktopSource": deepcopy(canvas.get("__web_source__") or {"client": "CreativeEnginePro"}),
        "suppressedNodeIds": deepcopy(canvas.get("__web_suppressed_node_ids__") or []),
        "suppressedProductionRunIds": deepcopy(canvas.get("__web_suppressed_production_run_ids__") or []),
    })
    nodes = []
    ids = set()
    records = list(canvas.get("__custom_nodes__", []))
    records.extend({**deepcopy(group), "type": "workflow_group", "canvas_group": True,
                    "title": str(group.get("group_name") or "节点分组")}
                   for group in canvas.get("__canvas_groups__", []) if isinstance(group, dict))
    for record in records:
        if not isinstance(record, dict) or not record.get("id"):
            continue
        node_id = str(record["id"])
        if node_id in ids:
            continue
        ids.add(node_id)
        extra = deepcopy(record.get("web_canvas_node") or {})
        data = deepcopy(record.get("web_canvas_data") or {})
        desktop_type = str(record.get("web_desktop_type") or record.get("type") or "")
        if record.get("agent_tool") and not record.get("web_desktop_type"):
            tool_key = next((key for key, (tool, _title, _defaults) in LOCAL_TOOL_SPECS.items()
                             if tool == record["agent_tool"]), "")
            if tool_key:
                data["specKey"] = tool_key
                desktop_type = tool_key
        payload = deepcopy({key: value for key, value in record.items()
                            if key not in {"web_canvas_node", "web_canvas_data", "web_desktop_type"}})
        if desktop_type != payload.get("type"):
            payload["type"] = desktop_type
        data.update({"title": str(record.get("title") or "网页节点"),
                     "desktopType": desktop_type, "desktopPayload": payload,
                     "description": str(record.get("content") or ""),
                     "status": str(record.get("status") or "")})
        data.setdefault("specKey", next((key for key, spec in NODE_SPECS.items()
                                         if spec["node_type"] == desktop_type), ""))
        position = canvas.get(node_id)
        if record.get("canvas_group") and not isinstance(position, (list, tuple)):
            position = [record.get("x", 0), record.get("y", 0)]
        if not isinstance(position, (list, tuple)) or len(position) < 2:
            position = [120 + len(nodes) % 4 * 340, 100 + len(nodes) // 4 * 240]
        extra.update({"id": node_id, "type": str(extra.get("type") or "studio"),
                      "position": {"x": float(position[0]), "y": float(position[1])},
                      "data": data})
        nodes.append(extra)
    for shot_node in canvas.get("__web_shot_nodes__", []):
        if not isinstance(shot_node, dict) or not shot_node.get("id"):
            continue
        node_id = str(shot_node["id"])
        if node_id in ids:
            continue
        shot_id = node_id.removeprefix("shot:")
        shot = next((value for value in storyboard.get("shots", [])
                     if isinstance(value, dict) and str(value.get("id")) == shot_id), None)
        if shot is None:
            continue
        restored = deepcopy(shot_node)
        restored.setdefault("data", {}).setdefault("desktopPayload", {})["shot"] = deepcopy(shot)
        position = canvas.get(node_id)
        if isinstance(position, (list, tuple)) and len(position) >= 2:
            restored["position"] = {"x": float(position[0]), "y": float(position[1])}
        nodes.append(restored)
        ids.add(node_id)
    original_edges = {str(value.get("id")): value for value in canvas.get("__web_edges__", [])
                      if isinstance(value, dict) and value.get("id")}
    edges = []
    for index, value in enumerate(canvas.get("__workflow_edges__", [])):
        if not isinstance(value, dict):
            continue
        start, end = str(value.get("source") or ""), str(value.get("target") or "")
        if start not in ids or end not in ids:
            continue
        edge_id = str(value.get("id") or f"desktop-edge-{index}")
        edge = deepcopy(original_edges.get(edge_id) or {})
        edge.update({"id": edge_id, "source": start, "target": end})
        edge["relation"] = str(value.get("type") or value.get("relation") or "dependency")
        edge.setdefault("type", "smoothstep")
        edges.append(edge)
    document["nodes"] = nodes
    document["edges"] = edges
    # The Web app's current import button accepts the desktop envelope rather
    # than its own Web protocol. Carry both representations so existing Web
    # installations and newer protocol readers can open the same JSON.
    native = deepcopy(canvas)
    native.pop("__web_document_extras__", None)
    custom = native.setdefault("__custom_nodes__", [])
    for group in native.get("__canvas_groups__", []):
        if isinstance(group, dict) and group.get("id") and not any(row.get("id") == group["id"] for row in custom if isinstance(row, dict)):
            custom.append({**deepcopy(group), "type": "workflow_group", "canvas_group": True,
                           "title": str(group.get("group_name") or "节点分组")})
            native[group["id"]] = [float(group.get("x") or 0), float(group.get("y") or 0)]
    document.update(format="creative-engine-production-project", project_id=str(storyboard.get("id") or ""), canvas=native)
    return document


