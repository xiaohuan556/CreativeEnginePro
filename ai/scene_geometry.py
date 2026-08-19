"""Scene-space contracts, view binding and edit-region masks."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


SCENE_VIEW_SPECS = (
    ("master", "主视角", "入口侧主透视空镜，完整展示固定空间结构和主要设备"),
    ("reverse", "反向视角", "与主视角相反方向的权威空镜，保持同一设备坐标和朝向"),
    ("left", "左侧视角", "从空间左侧看向右侧的权威空镜，保持同一固定布局"),
    ("right", "右侧视角", "从空间右侧看向左侧的权威空镜，保持同一固定布局"),
    ("topdown", "俯视平面", "正交俯视空间代理，清楚展示墙体、门窗、固定设备和活动区坐标"),
)


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _dict_rows(value) -> list[dict]:
    return [dict(item) for item in value
            if isinstance(item, dict)] if isinstance(value, (list, tuple)) else []


def normalize_bbox(value, default=(0.15, 0.12, 0.7, 0.78)) -> list[float]:
    values = list(value or []) if isinstance(value, (list, tuple)) else []
    if len(values) != 4:
        values = list(default)
    # Planning models sometimes return percentages even though the contract is
    # normalized.  Clamping 18..82 directly to 1 destroyed old proxy layouts;
    # migrate the whole quad before applying x/y/w/h bounds.
    numeric = [_number(item) for item in values]
    if any(abs(item) > 1.0 for item in numeric):
        numeric = [item / 100.0 for item in numeric]
    values = numeric
    x, y, w, h = (_number(item) for item in values)
    x, y = max(0.0, min(1.0, x)), max(0.0, min(1.0, y))
    w, h = max(0.01, min(1.0 - x, w)), max(0.01, min(1.0 - y, h))
    return [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]


def _collapsed_edge_box(box) -> bool:
    return bool(len(box) == 4 and box[0] >= .98 and box[1] >= .98
                and box[2] <= .02 and box[3] <= .02)


def _proxy_box_from_view(view_bboxes: dict, index: int) -> list[float]:
    """Recover old proxies whose percentage coordinates were already clamped.

    A perspective view is not a world plan, but its centre/depth is a much
    better stable fallback than collapsing every fixture into the lower-right
    one-pixel corner.  Future contracts retain their real normalized plan.
    """
    view = next((view_bboxes.get(role) for role in
                 ("master", "left", "right", "reverse")
                 if isinstance(view_bboxes.get(role), list)), None)
    if view:
        x, y, w, h = view
        width = max(.06, min(.24, w * .55))
        depth = max(.05, min(.20, h * .28))
        cx = max(width / 2, min(1 - width / 2, x + w / 2))
        cz = max(depth / 2, min(1 - depth / 2, y + h / 2))
        return normalize_bbox([cx - width / 2, cz - depth / 2, width, depth])
    column, row = index % 4, index // 4
    return normalize_bbox([.1 + column * .21, .12 + row * .22, .14, .12])


def normalize_scene_proxy(scene: dict) -> dict:
    scene = scene if isinstance(scene, dict) else {}
    nested = scene.get("scene_proxy")
    if isinstance(nested, dict):
        source = dict(nested)
        source.setdefault("location_id", scene.get("location_id"))
        for key in ("fixtures", "walls", "activity_bbox_xy", "camera_zones"):
            if key in scene:
                source[key] = scene[key]
        scene = source
    fixtures = []
    for index, raw in enumerate(_dict_rows(scene.get("fixtures"))):
        if not isinstance(raw, dict):
            continue
        view_bboxes = {
            str(role):normalize_bbox(box, (0.1, 0.1, 0.15, 0.15))
            for role, box in dict(raw.get("view_bboxes") or {}).items()
            if str(role) in {"master", "reverse", "left", "right"}
            and isinstance(box, (list, tuple)) and len(box) == 4
        } if isinstance(raw.get("view_bboxes"), dict) else {}
        bbox = normalize_bbox(raw.get("bbox_xy"), (0.1, 0.1, 0.15, 0.15))
        if _collapsed_edge_box(bbox):
            bbox = _proxy_box_from_view(view_bboxes, index)
        fixtures.append({
            "id":str(raw.get("id") or f"fixture_{index + 1}"),
            "label":str(raw.get("label") or raw.get("name") or f"固定物 {index + 1}"),
            "type":str(raw.get("type") or "fixture"),
            "bbox_xy":bbox,
            "view_bboxes":view_bboxes,
            "height":round(max(0.0, _number(raw.get("height"), 1.0)), 3),
            "fixed":bool(raw.get("fixed", True)),
        })
    walls = _dict_rows(scene.get("walls"))
    activity = normalize_bbox(scene.get("activity_bbox_xy"), (0.15, 0.12, 0.7, 0.78))
    if _collapsed_edge_box(activity) and fixtures:
        left = min(row["bbox_xy"][0] for row in fixtures)
        top = min(row["bbox_xy"][1] for row in fixtures)
        right = max(row["bbox_xy"][0] + row["bbox_xy"][2] for row in fixtures)
        bottom = max(row["bbox_xy"][1] + row["bbox_xy"][3] for row in fixtures)
        activity = normalize_bbox([
            max(0.0, left - .08), max(0.0, top - .08),
            min(1.0, right + .08) - max(0.0, left - .08),
            min(1.0, bottom + .08) - max(0.0, top - .08),
        ])
    camera_zones = []
    for row in _dict_rows(scene.get("camera_zones")):
        normalized = dict(row)
        normalized["bbox_xy"] = normalize_bbox(
            row.get("bbox_xy"), (0.1, 0.1, 0.25, 0.2))
        camera_zones.append(normalized)
    return {
        "schema":"scene_proxy_v1",
        "coordinate_system":"normalized_topdown_xy_origin_top_left",
        "location_id":str(scene.get("location_id") or ""),
        "fixtures":fixtures,
        "walls":walls,
        "activity_bbox_xy":activity,
        "camera_zones":camera_zones,
    }


def scene_proxy_issues(proxy: dict) -> list[str]:
    """Return deterministic geometry blockers before stage/image generation."""
    normalized = normalize_scene_proxy(proxy)
    issues = []
    fixtures = normalized.get("fixtures") or []
    ids = [str(row.get("id") or "") for row in fixtures]
    if len(ids) != len(set(ids)):
        issues.append("SCENE_FIXTURE_ID_DUPLICATE")
    if any(_collapsed_edge_box(row.get("bbox_xy") or []) for row in fixtures):
        issues.append("SCENE_FIXTURE_COORDINATES_COLLAPSED")
    if _collapsed_edge_box(normalized.get("activity_bbox_xy") or []):
        issues.append("SCENE_ACTIVITY_COORDINATES_COLLAPSED")
    return issues


def bind_scene_view(shot: dict, available_views) -> str:
    available = {str(value) for value in available_views or [] if value}
    if not available:
        return ""
    explicit = str(shot.get("scene_view_id") or "")
    if explicit in available:
        return explicit
    text = f"{shot.get('camera_position', '')} {shot.get('camera_slot', '')}"
    candidates = []
    if any(word in text for word in ("反打", "反向", "对面", "背向入口")):
        candidates.append("reverse")
    if any(word in text for word in ("左侧", "左边", "从左")):
        candidates.append("left")
    if any(word in text for word in ("右侧", "右边", "从右")):
        candidates.append("right")
    if any(word in text for word in ("俯视", "顶视", "鸟瞰")):
        candidates.append("topdown")
    candidates.extend(("master", "left", "right", "reverse"))
    return next((value for value in candidates if value in available),
                sorted(available)[0])


def scene_proxy_signature(proxy: dict) -> str:
    payload = json.dumps(proxy or {}, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def fixture_view_bboxes(proxy: dict, view_id: str) -> list[list[float]]:
    boxes = []
    for fixture in _dict_rows((proxy or {}).get("fixtures")):
        if not bool(fixture.get("fixed", True)):
            continue
        views = fixture.get("view_bboxes")
        box = views.get(view_id) if isinstance(views, dict) else None
        if isinstance(box, (list, tuple)) and len(box) == 4:
            boxes.append(normalize_bbox(box, (0.1, 0.1, 0.15, 0.15)))
    return boxes


def create_edit_region_mask(image_path: str, bbox_xy, output_dir: str,
                            *, protected_bboxes=None, feather: float = 0.025) -> str:
    """Create OpenAI-compatible RGBA mask: transparent area may change."""
    try:
        from PIL import Image, ImageDraw, ImageFilter
        with Image.open(image_path) as source:
            width, height = source.size
        x, y, w, h = normalize_bbox(bbox_xy)
        margin = max(2, int(min(width, height) * max(0.0, feather)))
        box = [int(x * width) - margin, int(y * height) - margin,
               int((x + w) * width) + margin, int((y + h) * height) + margin]
        alpha = Image.new("L", (width, height), 255)
        ImageDraw.Draw(alpha).rectangle(box, fill=0)
        if margin > 2:
            alpha = alpha.filter(ImageFilter.GaussianBlur(radius=max(1, margin // 3)))
        protect = ImageDraw.Draw(alpha)
        for protected in protected_bboxes or []:
            px, py, pw, ph = normalize_bbox(protected, (0.0, 0.0, 0.01, 0.01))
            protect.rectangle([
                int(px * width), int(py * height),
                int((px + pw) * width), int((py + ph) * height),
            ], fill=255)
        mask = Image.new("RGBA", (width, height), (255, 255, 255, 255))
        mask.putalpha(alpha)
        folder = Path(output_dir); folder.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(
            f"{os.path.abspath(image_path)}|{normalize_bbox(bbox_xy)}|"
            f"{list(protected_bboxes or [])}".encode()).hexdigest()[:12]
        path = folder / f"scene_edit_mask_{digest}.png"
        mask.save(path)
        return str(path)
    except (ImportError, OSError, ValueError):
        return ""
