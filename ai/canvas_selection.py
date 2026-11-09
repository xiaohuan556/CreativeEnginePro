"""Portable export of selected production nodes, links and local media."""
from __future__ import annotations

import json
import os
import re
import zipfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from ai.web_canvas_bridge import desktop_canvas_to_web
from ai.canvas_groups import descendants
from core.project_files import AI_SUFFIX, write_project


MEDIA_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif",
                  ".tif", ".tiff", ".mp4", ".mov", ".mkv", ".webm",
                  ".avi", ".m4v", ".mpeg", ".mpg", ".wav", ".mp3", ".m4a", ".aac",
                  ".flac", ".ogg", ".opus", ".wma"}


def selected_project(storyboard: dict, canvas: dict, selected_ids: set[str],
                     extra_records: list[dict] | None = None) -> dict:
    ids = {str(value) for value in selected_ids if value}
    board = deepcopy(storyboard)
    board["id"] = f"canvas_{uuid4().hex[:12]}"
    board["title"] = f"{board.get('title') or 'AI 制片工程'} · 选区"
    board["shots"] = [deepcopy(shot) for shot in board.get("shots", [])
                      if isinstance(shot, dict) and f"shot:{shot.get('id')}" in ids]
    subset = {key: deepcopy(value) for key, value in canvas.items()
              if str(key).startswith("__web_") or key in ids}
    if isinstance(subset.get("__web_source__"), dict):
        subset["__web_source__"]["projectId"] = board["id"]
        subset["__web_source__"].pop("mediaManifest", None)
    subset["__web_suppressed_node_ids__"] = [
        value for value in subset.get("__web_suppressed_node_ids__", [])
        if str(value) in ids]
    subset["__web_suppressed_production_run_ids__"] = []
    subset["__custom_nodes__"] = [deepcopy(record) for record in canvas.get("__custom_nodes__", [])
                                  if isinstance(record, dict) and str(record.get("id")) in ids]
    known = {str(value["id"]) for value in subset["__custom_nodes__"]}
    for record in extra_records or []:
        if isinstance(record, dict) and str(record.get("id")) in ids and str(record["id"]) not in known:
            subset["__custom_nodes__"].append(deepcopy(record))
            known.add(str(record["id"]))
    subset["__workflow_edges__"] = [deepcopy(edge) for edge in canvas.get("__workflow_edges__", [])
                                    if isinstance(edge, dict) and
                                    str(edge.get("source")) in ids and str(edge.get("target")) in ids]
    subset["__web_edges__"] = [deepcopy(edge) for edge in canvas.get("__web_edges__", [])
                               if isinstance(edge, dict) and
                               str(edge.get("source")) in ids and str(edge.get("target")) in ids]
    subset["__web_shot_nodes__"] = [deepcopy(node) for node in canvas.get("__web_shot_nodes__", [])
                                    if isinstance(node, dict) and str(node.get("id")) in ids]
    subset["__canvas_groups__"] = []
    groups = canvas.get("__canvas_groups__", [])
    group_ids = {str(row.get("id")) for row in groups if isinstance(row, dict)}
    eligible = {str(row["id"]) for row in groups if isinstance(row, dict) and row.get("id") and
                (len((descendants(row["id"], groups) - group_ids) & ids) >= 2 or str(row["id"]) in ids)}
    for group in groups:
        if not isinstance(group, dict):
            continue
        members = [value for value in group.get("group_nodes", []) if str(value) in ids | eligible]
        if str(group.get("id")) in eligible and members:
            subset["__canvas_groups__"].append({**deepcopy(group), "group_nodes": members})
    for record in subset["__custom_nodes__"]:
        if record.get("type") == "workflow_group":
            record["group_nodes"] = [value for value in record.get("group_nodes", []) if str(value) in ids]
    for key in ("__inline_editor_heights__", "__inline_text_overrides__"):
        if isinstance(canvas.get(key), dict):
            subset[key] = {node_id: deepcopy(value) for node_id, value in canvas[key].items() if str(node_id) in ids}
    if isinstance(canvas.get("__layout_settings__"), dict):
        subset["__layout_settings__"] = deepcopy(canvas["__layout_settings__"])
    return {"format": "creative-engine-production-project", "version": 1,
            "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "project_id": board["id"], "title": board["title"],
            "storyboard": board, "canvas": subset}


def _media_paths(value, found: set[str]) -> None:
    if isinstance(value, dict):
        for child in value.values():
            _media_paths(child, found)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _media_paths(child, found)
    elif isinstance(value, str) and Path(value).suffix.lower() in MEDIA_SUFFIXES and os.path.isfile(value):
        found.add(os.path.abspath(value))


def _replace_paths(value, mapping: dict[str, str]):
    if isinstance(value, dict):
        return {key: _replace_paths(child, mapping) for key, child in value.items()}
    if isinstance(value, list):
        return [_replace_paths(child, mapping) for child in value]
    if isinstance(value, str):
        return mapping.get(os.path.abspath(value), value) if os.path.isabs(value) else value
    return value


def write_selection_archive(path: str | Path, project: dict) -> dict:
    """Store an editable canvas subgraph plus its referenced media files."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    media: set[str] = set()
    _media_paths(project.get("storyboard"), media)
    _media_paths((project.get("canvas") or {}).get("__custom_nodes__"), media)
    mapping = {}
    for index, source in enumerate(sorted(media), 1):
        name = re.sub(r"[\\/:*?\"<>|]+", "_", Path(source).name)
        mapping[source] = f"media/{index:03d}_{name}"
    portable = _replace_paths(deepcopy(project), mapping)
    portable["media_manifest"] = [
        {"path": value, "exists": True, "size": os.path.getsize(source)}
        for source, value in mapping.items()]
    web = desktop_canvas_to_web(portable["storyboard"], portable["canvas"])
    temporary = target.with_suffix(target.suffix + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED,
                             allowZip64=True) as archive:
            with archive.open(f"selection{AI_SUFFIX}", "w", force_zip64=True) as stream:
                write_project(stream, portable, AI_SUFFIX)
            archive.writestr("selection.webcanvas.json", json.dumps(web, ensure_ascii=False, indent=2))
            for source, member in mapping.items():
                archive.write(source, member)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"nodes": len(portable["canvas"]["__custom_nodes__"]),
            "edges": len(portable["canvas"]["__workflow_edges__"]),
            "media": len(mapping)}


def write_result_archive(path: str | Path, paths: list[str]) -> dict:
    """Export explicit current results, never their thumbnails/history/references."""
    sources = []
    seen = set()
    for value in paths:
        source = Path(value).resolve()
        key = os.path.normcase(str(source))
        if key in seen:
            continue
        if source.suffix.lower() not in MEDIA_SUFFIXES or not source.is_file():
            raise ValueError(f"素材文件不存在或不支持：{value}")
        seen.add(key)
        sources.append(source)
    if not sources:
        raise ValueError("所选节点还没有可导出的本地图片、视频或音频。")
    target = Path(path)
    if any(source == target.resolve() for source in sources):
        raise ValueError("导出压缩包不能覆盖素材原文件。")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    used_names = set()
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED,
                             allowZip64=True) as archive:
            for source in sources:
                name = re.sub(r"[\\/:*?\"<>|]+", "_", source.name)
                base = Path(name)
                index = 2
                while name.casefold() in used_names:
                    name = f"{base.stem}_{index}{base.suffix}"
                    index += 1
                used_names.add(name.casefold())
                archive.write(source, name)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"media": len(sources), "videos": sum(p.suffix.lower() in {
        ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpeg", ".mpg"} for p in sources)}


def resolve_portable_media(document: dict, directory: str | Path) -> dict:
    """Resolve media/ references after unpacking a selected-node archive."""
    root = Path(directory)

    def visit(value):
        if isinstance(value, dict):
            return {key: visit(child) for key, child in value.items()}
        if isinstance(value, list):
            return [visit(child) for child in value]
        if isinstance(value, str) and value.startswith("media/"):
            candidate = (root / value).resolve()
            if candidate.is_file() and candidate.is_relative_to(root.resolve()):
                return str(candidate)
        return value

    return visit(document)
