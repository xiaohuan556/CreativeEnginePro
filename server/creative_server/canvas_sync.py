from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from .database import SessionLocal
from .models import Asset, GenerationTask, Project, ProjectRevision


MEDIA_SPECS = {
    "image": ("image", "image_asset", "image_node", "#50b9dd"),
    "video": ("video", "video", "video_node", "#f1a85b"),
    "audio": ("audio", "audio", "audio_node", "#66d49a"),
}


def _document(value: str) -> dict[str, Any]:
    try:
        document = json.loads(value or "{}")
    except json.JSONDecodeError:
        document = {}
    if not isinstance(document, dict):
        document = {}
    document.setdefault("protocol", "creative-engine-canvas")
    document.setdefault("version", 1)
    document.setdefault("nodes", [])
    document.setdefault("edges", [])
    return document


def _task_node(task: GenerationTask) -> dict[str, Any]:
    return {
        "id": task.node_id,
        "type": "studio",
        "position": {"x": 160, "y": 120},
        "data": {
            "title": "生成任务",
            "description": "由服务端任务写入画布",
            "kind": "task",
            "specKey": "task",
            "desktopType": "generation_task",
            "status": "正在处理",
            "meta": task.kind,
            "accent": "#9aa6b5",
            "desktopPayload": {},
        },
    }


def sync_task_to_canvas(task_id: str) -> None:
    """Persist a task result in its canvas before it can enter the asset library."""
    with SessionLocal.begin() as db:
        task = db.get(GenerationTask, task_id)
        if not task:
            return
        project = db.scalar(select(Project).where(Project.id == task.project_id).with_for_update())
        if not project:
            return
        document = _document(project.canvas_json)
        nodes = document["nodes"] if isinstance(document.get("nodes"), list) else []
        edges = document["edges"] if isinstance(document.get("edges"), list) else []
        source = next((node for node in nodes if isinstance(node, dict) and node.get("id") == task.node_id), None)
        if source is None:
            source = _task_node(task)
            nodes.append(source)

        output = json.loads(task.output_json) if task.output_json else {}
        data = source.setdefault("data", {})
        payload = data.setdefault("desktopPayload", {})
        data["status"] = {
            "completed": "生成完成",
            "failed": "生成失败",
            "cancelled": "已取消",
            "paused": "任务已暂停",
            "queued": "正在排队",
        }.get(task.status, "正在处理")
        data["progress"] = task.progress
        payload["server_task_id"] = task.id
        payload["task_status"] = task.status
        payload["output_asset_ids"] = list(output.get("asset_ids") or [])
        if "analysis" in output:
            payload["analysis"] = output["analysis"]
        if "data" in output:
            payload["generated_data"] = output["data"]
        if task.error_message:
            payload["error_message"] = task.error_message

        asset_ids = [str(item) for item in output.get("asset_ids") or []]
        assets = db.scalars(select(Asset).where(Asset.id.in_(asset_ids))).all() if asset_ids else []
        asset_by_id = {asset.id: asset for asset in assets}
        source_position = source.get("position") if isinstance(source.get("position"), dict) else {"x": 160, "y": 120}
        for index, asset_id in enumerate(asset_ids):
            asset = asset_by_id.get(asset_id)
            if not asset:
                continue
            child_id = f"result-{task.id}-{asset.id}"
            if not any(isinstance(node, dict) and node.get("id") == child_id for node in nodes):
                kind, spec_key, desktop_type, accent = MEDIA_SPECS.get(asset.kind, ("result", "result", "asset_take", "#9aa6b5"))
                nodes.append({
                    "id": child_id,
                    "type": "studio",
                    "position": {"x": float(source_position.get("x", 160)) + 360, "y": float(source_position.get("y", 120)) + index * 210},
                    "data": {
                        "title": asset.name,
                        "description": "生成结果已写入画布；点击“保存到资产库”才会同步资产库副本。",
                        "kind": kind,
                        "specKey": spec_key,
                        "desktopType": desktop_type,
                        "status": "生成完成",
                        "meta": asset.content_type,
                        "accent": accent,
                        "desktopPayload": {"asset_id": asset.id, "source_task_id": task.id, "saved_to_library": False},
                    },
                })
            edge_id = f"result-edge-{task.id}-{asset.id}"
            if not any(isinstance(edge, dict) and edge.get("id") == edge_id for edge in edges):
                edges.append({"id": edge_id, "source": task.node_id, "target": child_id, "type": "pulse", "data": {"relation": "generated_result"}})

        document["nodes"], document["edges"] = nodes, edges
        db.add(ProjectRevision(project_id=project.id, actor_id=task.owner_id, version=project.version, canvas_json=project.canvas_json))
        project.canvas_json = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
        project.version += 1
