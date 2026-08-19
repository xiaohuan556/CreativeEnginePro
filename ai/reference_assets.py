"""Typed reference assets shared by storyboard workflows and AI providers.

The UI stores scene, character and element identities separately.  Providers,
however, used to receive only an untyped list of paths, so that identity was
lost at the API boundary.  This module keeps the path list compatible while
also carrying stable asset ids, roles and prompt labels.
"""

from __future__ import annotations

import os
from typing import Any, Iterable


_ROLE_ORDER = {
    "composition": 0,
    "scene": 10,
    "character": 20,
    "element": 30,
    "style": 40,
    "reference": 50,
}


def normalize_reference_assets(
        values: Iterable[dict[str, Any]] | None,
        fallback_paths: Iterable[Any] | None = None) -> list[dict[str, Any]]:
    """Return de-duplicated, ordered reference descriptors.

    Local paths must exist.  ``asset://`` identifiers are preserved for
    providers that support remote trusted assets.
    """
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    source = list(values or [])
    if not source:
        source = [
            {"path": path, "role": "reference", "label": f"参考图{index}"}
            for index, path in enumerate(fallback_paths or [], 1)
        ]
    for index, raw in enumerate(source):
        if not isinstance(raw, dict):
            raw = {"path": raw}
        path = str(raw.get("path") or raw.get("url") or "").strip()
        if not path or path in seen:
            continue
        is_remote = path.startswith(("asset://", "data:", "http://", "https://"))
        if not is_remote and not os.path.exists(path):
            continue
        seen.add(path)
        role = str(raw.get("role") or "reference").strip().lower()
        normalized.append({
            "path": path,
            "asset_id": str(raw.get("asset_id") or ""),
            "role": role,
            "label": str(raw.get("label") or f"参考图{index + 1}"),
            "name": str(raw.get("name") or ""),
            "version": max(0, int(raw.get("version") or 0)),
            "weight": max(0.0, min(2.0, float(raw.get("weight") or 1.0))),
            "required": bool(raw.get("required", raw.get("critical", False))),
            "mode": str(raw.get("mode") or "reference"),
            "priority": int(raw.get("priority") or _ROLE_ORDER.get(role, 50)),
            "order": int(raw.get("order") or index),
        })
    normalized.sort(key=lambda item: (
        item["priority"], _ROLE_ORDER.get(item["role"], 50), item["order"]))
    return normalized


def reference_paths(values: Iterable[dict[str, Any]] | None,
                    fallback_paths: Iterable[Any] | None = None) -> list[str]:
    return [item["path"] for item in normalize_reference_assets(
        values, fallback_paths)]


def prompt_manifest(values: Iterable[dict[str, Any]] | None) -> str:
    """Describe the exact upload order without changing the user's prompt."""
    assets = normalize_reference_assets(values)
    if not assets:
        return ""
    role_labels = {
        "composition": "构图底图",
        "scene": "场景",
        "character": "主体身份",
        "element": "指定元素",
        "style": "风格",
        "reference": "普通参考",
    }
    rows = []
    for index, item in enumerate(assets, 1):
        role = role_labels.get(item["role"], item["role"])
        version = f" v{item['version']}" if item["version"] else ""
        stable_id = f"；资产ID={item['asset_id']}" if item["asset_id"] else ""
        rows.append(
            f"参考图{index}：{role}；{item['label']}{version}{stable_id}；"
            f"权重={item['weight']:.2g}")
    return (
        "参考资产身份表（编号与上传顺序严格一致）：\n- " +
        "\n- ".join(rows) +
        "\n场景、主体和元素是不同身份，禁止交换、融合或把一个参考的特征复制给另一个。"
    )


def append_manifest(prompt: str,
                    values: Iterable[dict[str, Any]] | None) -> str:
    manifest = prompt_manifest(values)
    if not manifest or "参考资产身份表（编号与上传顺序严格一致）" in prompt:
        return prompt
    return f"{prompt.rstrip()}\n\n{manifest}"
