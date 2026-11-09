"""Remove retired canvas tools and obsolete progress-only cards."""
RETIRED_TOOLS = frozenset({"inpaint", "video_packaging", "media_download", "ae_template_render"})
RETIRED_SPECS = frozenset({"inpaint_tool", "video_packaging_tool", "media_download_tool",
                            "ae_template_render", "ae_control_center_render",
                            "ae_template_render_node", "ae_control_center_render_node", "web_node"})
RETIRED_TITLES = frozenset({"批量智能消除", "智能消除", "视频包装", "Agent 视频包装", "链接下载",
                             "AE 控制中心", "AE控制中心", "AE 水印／尾页", "AE 水印 / 尾页", "AE水印尾页"})


def is_retired_node(record):
    if not isinstance(record, dict):
        return False
    data = record.get("web_canvas_data") if isinstance(record.get("web_canvas_data"), dict) else {}
    return bool(record.get("agent_tool") in RETIRED_TOOLS or record.get("type") in RETIRED_SPECS
                or record.get("web_desktop_type") in RETIRED_SPECS
                or data.get("specKey") in RETIRED_SPECS or data.get("desktopType") in RETIRED_SPECS
                or (record.get("type") == "ai_editor_node" and record.get("title") in RETIRED_TITLES))


def prune_retired_canvas_nodes(canvas):
    if not isinstance(canvas, dict):
        return set()
    records = canvas.get("__custom_nodes__", [])
    if not isinstance(records, list):
        return set()
    removed = {str(row["id"]) for row in records if isinstance(row, dict) and row.get("id") and
               (is_retired_node(row) or row.get("agent_parent_id"))}
    changed = bool(removed)
    removed_paths = {str(row.get("path")) for row in records if isinstance(row, dict)
                     and str(row.get("id")) in removed and is_retired_node(row) and row.get("path")}
    for record in records:
        if not isinstance(record, dict):
            continue
        run = record.get("agent_run") if isinstance(record.get("agent_run"), dict) else {}
        plan = run.get("plan") if isinstance(run.get("plan"), dict) else {}
        if any(isinstance(step, dict) and step.get("tool") in RETIRED_TOOLS for step in plan.get("steps", [])):
            record["agent_run"] = {"status": "interrupted", "steps": {}, "error": "旧计划包含已取消的节点，请重新规划任务。"}
            record.pop("agent_run_signature", None)
            changed = True
        for key in ("reference_assets", "reference_settings", "reference_mentions", "timeline_images"):
            if isinstance(record.get(key), list):
                record[key] = [row for row in record[key] if not isinstance(row, dict) or
                               str(row.get("source_node_id") or "") not in removed]
        if isinstance(record.get("group_nodes"), list):
            record["group_nodes"] = [value for value in record["group_nodes"] if str(value) not in removed]
        if isinstance(record.get("references"), list):
            record["references"] = [value for value in record["references"] if str(value) not in removed_paths]
        for field in ("first_frame", "last_frame"):
            if str(record.get(field) or "") in removed_paths:
                record[field] = ""
    if changed:
        canvas["__custom_nodes__"] = [row for row in records if not isinstance(row, dict) or str(row.get("id")) not in removed]
        for key in ("__workflow_edges__", "__web_edges__"):
            edges = []
            for edge in canvas.get(key, []):
                if not isinstance(edge, dict) or str(edge.get("target")) in removed:
                    continue
                source = str(edge.get("source") or "")
                if source in removed:
                    result = next((row for row in records if isinstance(row, dict) and str(row.get("id")) == str(edge.get("target"))), {})
                    parent = str(result.get("source_node_id") or "")
                    if not parent or parent in removed:
                        continue
                    edge = {**edge, "source": parent, "type": "agent_result"}
                if edge not in edges:
                    edges.append(edge)
            canvas[key] = edges
        for node_id in removed:
            canvas.pop(node_id, None)
    return removed
