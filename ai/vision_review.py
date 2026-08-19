"""Optional multimodal consistency review for storyboard candidates."""

from __future__ import annotations

import json
import re
from typing import Any

from .providers.ark_http import to_image_data_url


def build_review_messages(shot: dict, candidate_paths: list[str],
                          reference_assets: list[dict[str, Any]]) -> list[dict]:
    requirements = {
        "scene": shot.get("scene", ""),
        "shot_size": shot.get("shot_size", ""),
        "camera": shot.get("camera", ""),
        "blocking": shot.get("blocking", ""),
        "character_bindings": shot.get("character_bindings", []),
        "element_bindings": shot.get("element_bindings", []),
    }
    content: list[dict] = [{
        "type": "text",
        "text": (
            "You are a strict storyboard continuity inspector. Compare every candidate "
            "against the typed reference images and shot requirements. Do not judge art "
            "style preference. Check scene identity, each character's identity/body/outfit, "
            "required element presence and placement, obvious anatomy corruption, and wrong "
            "aspect/composition. Exact-mode UI/logo elements may be intentionally absent for "
            "later compositing; do not fail them.\n\n"
            "Return JSON only: {\"candidates\":[{\"index\":1,"
            "\"decision\":\"pass|warn|fail\",\"confidence\":0.0,"
            "\"missing_assets\":[],\"identity_errors\":[],\"reason\":\"\"}]}.\n\n"
            "Shot requirements:\n" + json.dumps(requirements, ensure_ascii=False))
    }]
    for index, item in enumerate(reference_assets[:6], 1):
        path = str(item.get("path") or "")
        if not path or path.startswith("asset://"):
            continue
        content.append({
            "type": "text",
            "text": (
                f"REFERENCE {index}: role={item.get('role', 'reference')}; "
                f"asset_id={item.get('asset_id', '')}; label={item.get('label', '')}"),
        })
        content.append({
            "type": "image_url",
            "image_url": {"url": to_image_data_url(path), "detail": "low"},
        })
    for index, path in enumerate(candidate_paths[:4], 1):
        content.append({"type": "text", "text": f"CANDIDATE {index}:"})
        content.append({
            "type": "image_url",
            "image_url": {"url": to_image_data_url(path), "detail": "low"},
        })
    return [
        {"role": "system", "content": "Return strict valid JSON only."},
        {"role": "user", "content": content},
    ]


def parse_review(value: Any, candidate_count: int) -> list[dict]:
    if isinstance(value, dict):
        data = value
    else:
        text = str(value or "").strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
        if fenced:
            text = fenced.group(1)
        else:
            first, last = text.find("{"), text.rfind("}")
            if first >= 0 and last > first:
                text = text[first:last + 1]
        data = json.loads(text)
    rows = data.get("candidates", []) if isinstance(data, dict) else []
    result = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        try:
            index = int(raw.get("index") or 0)
        except (TypeError, ValueError):
            continue
        if not 1 <= index <= candidate_count:
            continue
        decision = str(raw.get("decision") or "warn").lower()
        if decision not in {"pass", "warn", "fail"}:
            decision = "warn"
        try:
            confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0)))
        except (TypeError, ValueError):
            confidence = 0.0
        result.append({
            "index": index,
            "decision": decision,
            "confidence": confidence,
            "missing_assets": list(raw.get("missing_assets") or []),
            "identity_errors": list(raw.get("identity_errors") or []),
            "reason": str(raw.get("reason") or "").strip(),
        })
    return result
