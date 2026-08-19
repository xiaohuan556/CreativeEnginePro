"""Persistent 3D previs contracts used by the production canvas.

The stage is deliberately renderer-neutral.  PyQt owns the first editor, but
the JSON can later be consumed by Three.js, Blender or a model control adapter.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid

from ai.scene_geometry import normalize_scene_proxy


STAGE_SCHEMA = "scene_stage_v2"
COORDINATE_SYSTEM = "right_handed_y_up_meters"


def _number(value, default=0.0, minimum=None, maximum=None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = float(default)
    if not math.isfinite(result):
        result = float(default)
    if minimum is not None:
        result = max(float(minimum), result)
    if maximum is not None:
        result = min(float(maximum), result)
    return round(result, 4)


def _vector(value, default, *, minimum=None, maximum=None) -> list[float]:
    rows = list(value) if isinstance(value, (list, tuple)) else []
    return [
        _number(rows[index] if index < len(rows) else fallback, fallback,
                minimum, maximum)
        for index, fallback in enumerate(default)
    ]


def _safe_rows(value) -> list[dict]:
    return [dict(row) for row in value if isinstance(row, dict)] \
        if isinstance(value, (list, tuple)) else []


def _slug(value: str, fallback="stage") -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value or "")).strip("_")
    return clean[:48] or fallback


def normalize_transform(value, *, position=(0.0, 0.0, 0.0),
                        rotation=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0)) -> dict:
    value = value if isinstance(value, dict) else {}
    return {
        "position": _vector(value.get("position"), position, minimum=-10000, maximum=10000),
        "rotation": _vector(value.get("rotation"), rotation, minimum=-3600, maximum=3600),
        "scale": _vector(value.get("scale"), scale, minimum=0.01, maximum=1000),
    }


def _position_from_legacy(value, room_width: float, room_depth: float,
                          fallback_index=0) -> list[float]:
    if isinstance(value, dict):
        xyz = value.get("position") or value.get("xyz") or value.get("world_position")
        if isinstance(xyz, (list, tuple)):
            return _vector(xyz, (0, 0, 0), minimum=-10000, maximum=10000)
        x = value.get("x")
        z = value.get("z", value.get("depth", value.get("y")))
        if x is not None or z is not None:
            # Values inside 0..1 are old normalized top-down coordinates.
            x_value, z_value = _number(x, 0.5), _number(z, 0.5)
            if 0 <= x_value <= 1 and 0 <= z_value <= 1:
                return [round((x_value - .5) * room_width, 4), 0.0,
                        round((z_value - .5) * room_depth, 4)]
            return [x_value, 0.0, z_value]
        value = value.get("start") or ""
    text = str(value or "")
    pairs = {
        key.lower(): _number(number)
        for key, number in re.findall(
            r"\b(x|y|z|depth)\s*[=:：]\s*(-?\d+(?:\.\d+)?)", text,
            flags=re.IGNORECASE)
    }
    x_value = pairs.get("x")
    z_value = pairs.get("z", pairs.get("depth", pairs.get("y")))
    if x_value is not None and z_value is not None:
        if 0 <= x_value <= 1 and 0 <= z_value <= 1:
            return [round((x_value - .5) * room_width, 4), 0.0,
                    round((z_value - .5) * room_depth, 4)]
        return [x_value, 0.0, z_value]
    column = fallback_index % 4
    row = fallback_index // 4
    return [round((column - 1.5) * 1.25, 4), 0.0,
            round((row - .5) * 1.4, 4)]


def _default_camera(view_id: str, room_width: float, room_depth: float) -> dict:
    distance = max(room_width, room_depth) * .78
    presets = {
        "master": ([0.0, 1.65, distance], [0.0, 1.1, 0.0]),
        "reverse": ([0.0, 1.65, -distance], [0.0, 1.1, 0.0]),
        "left": ([-distance, 1.65, 0.0], [0.0, 1.1, 0.0]),
        "right": ([distance, 1.65, 0.0], [0.0, 1.1, 0.0]),
        "topdown": ([0.0, max(room_width, room_depth), 0.01], [0.0, 0.0, 0.0]),
    }
    position, target = presets.get(str(view_id or "master"), presets["master"])
    return {
        "id": f"camera_{_slug(view_id or 'master')}",
        "name": f"{view_id or 'master'} 机位",
        "fov": 45.0,
        "aspect_ratio": "16:9",
        "transform": normalize_transform({"position": position}),
        "target": _vector(target, (0, 1.1, 0)),
        "locked": False,
    }


def _shot_fov(shot: dict) -> float:
    text = str((shot or {}).get("shot_size") or "").lower()
    if any(value in text for value in ("特写", "close-up", "close up", "insert", "细节")):
        return 22.0
    if any(value in text for value in ("近中", "中近", "medium close")):
        return 30.0
    if any(value in text for value in ("中景", "medium shot")):
        return 38.0
    if any(value in text for value in ("中远", "medium wide")):
        return 48.0
    if any(value in text for value in ("大全", "远景", "全景", "wide", "establish")):
        return 58.0
    return 45.0


def _normalize_object(raw, index=0) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    kind = str(raw.get("kind") or raw.get("type") or "prop")
    return {
        "id": str(raw.get("id") or f"object_{uuid.uuid4().hex[:10]}"),
        "name": str(raw.get("name") or raw.get("label") or f"对象 {index + 1}"),
        "kind": kind if kind in {"actor", "fixture", "prop", "primitive"} else "prop",
        "visible": bool(raw.get("visible", True)),
        "locked": bool(raw.get("locked", raw.get("fixed", kind == "fixture"))),
        "color": str(raw.get("color") or ("#f0aa65" if kind == "actor" else "#6f8cff")),
        "transform": normalize_transform(raw.get("transform"),
                                         position=raw.get("position") or (0, 0, 0),
                                         rotation=raw.get("rotation") or (0, 0, 0),
                                         scale=raw.get("scale") or (1, 1, 1)),
        "pose": str(raw.get("pose") or "自然站立"),
        "asset_id": str(raw.get("asset_id") or ""),
        "source_fixture_id": str(raw.get("source_fixture_id") or ""),
    }


def _normalize_camera(raw, index=0) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    fallback = _default_camera("master", 10, 8)
    return {
        "id": str(raw.get("id") or f"camera_{uuid.uuid4().hex[:10]}"),
        "name": str(raw.get("name") or f"摄影机 {index + 1}"),
        "fov": _number(raw.get("fov"), 45, 10, 120),
        "aspect_ratio": str(raw.get("aspect_ratio") or "16:9"),
        "transform": normalize_transform(
            raw.get("transform"), position=raw.get("position") or
            fallback["transform"]["position"]),
        "target": _vector(raw.get("target"), fallback["target"],
                          minimum=-10000, maximum=10000),
        "locked": bool(raw.get("locked", False)),
    }


def stage_from_proxy(proxy, *, shot=None, existing=None) -> dict:
    """Build or migrate one authoritative world-space stage for a shot."""
    shot = shot if isinstance(shot, dict) else {}
    existing = existing if isinstance(existing, dict) else {}
    proxy = normalize_scene_proxy(proxy if isinstance(proxy, dict) else {})
    room_raw = existing.get("room") if isinstance(existing.get("room"), dict) else {}
    room = {
        "width": _number(room_raw.get("width"), 10, 2, 100),
        "depth": _number(room_raw.get("depth"), 8, 2, 100),
        "height": _number(room_raw.get("height"), 3.2, 1.8, 30),
        "ground_y": _number(room_raw.get("ground_y"), 0, -100, 100),
    }
    objects = [_normalize_object(row, index)
               for index, row in enumerate(_safe_rows(existing.get("objects")))]
    existing_fixture_ids = {row.get("source_fixture_id") or row.get("id")
                            for row in objects if row.get("kind") == "fixture"}
    for index, fixture in enumerate(_safe_rows(proxy.get("fixtures"))):
        fixture_id = str(fixture.get("id") or f"fixture_{index + 1}")
        if fixture_id in existing_fixture_ids:
            continue
        x, z, width, depth = fixture.get("bbox_xy") or (.1, .1, .15, .15)
        height = _number(fixture.get("height"), 1, .05, room["height"])
        objects.append(_normalize_object({
            "id": f"fixture:{fixture_id}", "source_fixture_id": fixture_id,
            "name": fixture.get("label") or fixture_id, "kind": "fixture",
            "locked": bool(fixture.get("fixed", True)), "color": "#5a77a8",
            "transform": {
                "position": [(_number(x) + _number(width) / 2 - .5) * room["width"],
                             height / 2,
                             (_number(z) + _number(depth) / 2 - .5) * room["depth"]],
                "scale": [_number(width, .15) * room["width"], height,
                          _number(depth, .15) * room["depth"]],
            },
        }, len(objects)))

    existing_actor_names = {str(row.get("name") or "") for row in objects
                            if row.get("kind") == "actor"}
    positions = _safe_rows(shot.get("character_positions"))
    names = [str(value) for value in shot.get("character_names", []) if value] \
        if isinstance(shot.get("character_names"), (list, tuple)) else []
    if not names:
        names = [str(row.get("name") or row.get("character") or "")
                 for row in positions]
    for index, name in enumerate(value for value in names if value):
        if name in existing_actor_names:
            continue
        position_row = positions[index] if index < len(positions) else {}
        objects.append(_normalize_object({
            "id": f"actor:{_slug(name, 'actor')}:{index + 1}",
            "name": name, "kind": "actor", "color": "#f0aa65",
            "position": _position_from_legacy(
                position_row, room["width"], room["depth"], index),
            "pose": position_row.get("pose") or position_row.get("facing") or "自然站立",
        }, len(objects)))

    cameras = [_normalize_camera(row, index)
               for index, row in enumerate(_safe_rows(existing.get("cameras")))]
    view_id = str(shot.get("scene_view_id") or "master")
    if not cameras:
        camera = _default_camera(view_id, room["width"], room["depth"])
        camera["fov"] = _shot_fov(shot)
        # Close coverage must aim at the named subject/fixture instead of
        # digitally inventing a second foreground copy while the wide plate
        # remains centred.  The proxy decides the camera target first.
        searchable = " ".join(str(shot.get(key) or "") for key in (
            "visual", "spatial_layout", "foreground", "midground",
            "primary_action", "element_names"))
        focus_rows = []
        for row in objects:
            name = str(row.get("name") or "")
            if name and name in searchable and row.get("kind") in {"actor", "fixture", "prop"}:
                focus_rows.append(row)
        if not focus_rows and _shot_fov(shot) <= 38:
            focus_rows = [row for row in objects if row.get("kind") == "actor"][:1]
        if focus_rows:
            points = [(row.get("transform") or {}).get("position") or [0, 0, 0]
                      for row in focus_rows[:3]]
            camera["target"] = [
                round(sum(point[index] for point in points) / len(points), 4)
                for index in range(3)
            ]
            camera["target"][1] = max(.8, camera["target"][1])
        cameras.append(camera)
    active = str(existing.get("active_camera_id") or "")
    if active not in {camera["id"] for camera in cameras}:
        active = cameras[0]["id"]
    stage_id = str(existing.get("id") or
                   f"stage_{_slug(proxy.get('location_id') or shot.get('scene') or 'scene')}")
    return {
        "schema": STAGE_SCHEMA,
        "id": stage_id,
        "coordinate_system": COORDINATE_SYSTEM,
        "version": max(1, int(_number(existing.get("version"), 1, 1, 1000000))),
        "location_id": str(proxy.get("location_id") or existing.get("location_id") or ""),
        "room": room,
        "environment": {
            "panorama_path": str((existing.get("environment") or {}).get("panorama_path") or
                                 shot.get("scene_view_path") or shot.get("scene_master_path") or ""),
            "background_color": str((existing.get("environment") or {}).get(
                "background_color") or "#11151d"),
        },
        "objects": objects,
        "cameras": cameras,
        "active_camera_id": active,
        "captures": _safe_rows(existing.get("captures"))[-50:],
        "source_proxy_signature": hashlib.sha1(json.dumps(
            proxy, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16],
    }


def normalize_scene_stage(value, *, proxy=None, shot=None) -> dict:
    return stage_from_proxy(proxy or (shot or {}).get("scene_proxy") or {},
                            shot=shot, existing=value)


def active_camera(stage) -> dict:
    stage = stage if isinstance(stage, dict) else {}
    cameras = _safe_rows(stage.get("cameras"))
    active_id = str(stage.get("active_camera_id") or "")
    return next((row for row in cameras if str(row.get("id") or "") == active_id),
                cameras[0] if cameras else {})


def camera_basis(camera) -> tuple[list[float], list[float], list[float]]:
    camera = camera if isinstance(camera, dict) else {}
    position = _vector((camera.get("transform") or {}).get("position"), (0, 1.65, 6))
    target = _vector(camera.get("target"), (0, 1.1, 0))
    forward = [target[index] - position[index] for index in range(3)]
    length = math.sqrt(sum(value * value for value in forward)) or 1.0
    forward = [value / length for value in forward]
    world_up = [0.0, 1.0, 0.0]
    # right = forward x world-up.  The opposite sign mirrors the stage and
    # also flips the derived up vector, which breaks screen direction.
    right = [-forward[2], 0.0, forward[0]]
    length = math.sqrt(sum(value * value for value in right)) or 1.0
    right = [value / length for value in right]
    up = [
        right[1] * forward[2] - right[2] * forward[1],
        right[2] * forward[0] - right[0] * forward[2],
        right[0] * forward[1] - right[1] * forward[0],
    ]
    if sum(value * value for value in up) < .0001:
        up = world_up
    return right, up, forward


def project_world_point(point, camera, viewport=(1280, 720)) -> dict:
    """Project a world point into the active camera's image coordinates."""
    width = max(1.0, _number(viewport[0], 1280))
    height = max(1.0, _number(viewport[1], 720))
    position = _vector((camera.get("transform") or {}).get("position"), (0, 1.65, 6))
    point = _vector(point, (0, 0, 0))
    delta = [point[index] - position[index] for index in range(3)]
    right, up, forward = camera_basis(camera)
    x_cam = sum(delta[index] * right[index] for index in range(3))
    y_cam = sum(delta[index] * up[index] for index in range(3))
    depth = sum(delta[index] * forward[index] for index in range(3))
    fov = math.radians(_number(camera.get("fov"), 45, 10, 120))
    focal = height / (2 * math.tan(fov / 2))
    visible = depth > .01
    safe_depth = max(.01, depth)
    return {
        "x": round(.5 + (x_cam * focal / safe_depth) / width, 6),
        "y": round(.5 - (y_cam * focal / safe_depth) / height, 6),
        "depth": round(depth, 4),
        "visible": visible,
    }


def stage_shot_contract(stage) -> dict:
    """Compile editor state into fields consumed by the existing shot pipeline."""
    stage = normalize_scene_stage(stage)
    camera = active_camera(stage)
    actors = [row for row in stage["objects"] if row.get("kind") == "actor"]
    actor_rows = []
    for actor in actors:
        position = actor["transform"]["position"]
        projected = project_world_point(position, camera)
        actor_rows.append({
            "name": actor.get("name") or "角色",
            "object_id": actor.get("id"),
            "world_position": position,
            "world_rotation": actor["transform"]["rotation"],
            "pose": actor.get("pose") or "自然站立",
            "start": f"world x={position[0]:g}m, y={position[1]:g}m, z={position[2]:g}m",
            "end": f"保持 {actor.get('pose') or '自然站立'}，终点另由动作合同决定",
            "screen_xy": [projected["x"], projected["y"]],
            "camera_depth": projected["depth"],
        })
    camera_position = (camera.get("transform") or {}).get("position") or [0, 1.65, 6]
    return {
        "scene_stage": stage,
        "scene_stage_id": stage.get("id"),
        "scene_stage_version": stage.get("version"),
        "camera_id": camera.get("id"),
        "camera_object": camera,
        "camera_position": (
            f"3D权威机位 {camera.get('name') or camera.get('id')}："
            f"x={camera_position[0]:g}m, y={camera_position[1]:g}m, "
            f"z={camera_position[2]:g}m，FOV={camera.get('fov', 45):g}°"),
        "character_positions": actor_rows,
        # Empty establishing/insert shots are valid stage plans; a camera is
        # the only universal requirement. Character presence is validated by
        # the existing asset/director gates when the script requires actors.
        "blocking_ready": bool(camera),
    }


def append_stage_capture(stage, path: str, *, label="构图快照") -> dict:
    stage = normalize_scene_stage(stage)
    camera = active_camera(stage)
    capture = {
        "id": f"capture_{uuid.uuid4().hex[:12]}",
        "label": str(label or "构图快照"),
        "path": str(path or ""),
        "camera_id": str(camera.get("id") or ""),
        "camera": json.loads(json.dumps(camera, ensure_ascii=False)),
        "object_transforms": {
            str(row.get("id")): json.loads(json.dumps(row.get("transform"), ensure_ascii=False))
            for row in stage.get("objects", []) if isinstance(row, dict)
        },
    }
    stage["captures"] = (_safe_rows(stage.get("captures")) + [capture])[-50:]
    stage["version"] = int(stage.get("version") or 1) + 1
    return stage
