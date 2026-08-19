"""Evidence-based provider routing learned from persisted generation traces."""
from __future__ import annotations

from collections import defaultdict
import math


def shot_signature(shot: dict) -> dict:
    people = len(shot.get("character_ids") or shot.get("character_names") or [])
    duration = float(shot.get("duration") or 0)
    strategy = str(shot.get("keyframe_strategy") or "first_frame")
    camera = str(shot.get("dominant_camera_move") or shot.get("camera") or "locked").lower()
    return {
        "shot_type":str(shot.get("shot_size") or "unknown").lower(),
        "people_bucket":"3+" if people >= 3 else str(people),
        "duration_bucket":"short" if duration <= 4 else "medium" if duration <= 8 else "long",
        "strategy":strategy,
        "camera_complexity":"dynamic" if any(word in camera for word in
                              ("pan", "tilt", "track", "orbit", "摇", "移", "跟", "环绕")) else "stable",
    }


def aggregate_generation_history(events: list[dict]) -> dict[str, dict]:
    groups = defaultdict(list)
    for event in events or []:
        if not isinstance(event, dict) or not event.get("model"):
            continue
        groups[str(event["model"])].append(event)
    result = {}
    for model, rows in groups.items():
        passed = sum(str(row.get("outcome")) in {"passed", "adopted"} for row in rows)
        adopted = sum(bool(row.get("adopted")) for row in rows)
        result[model] = {
            "samples":len(rows), "pass_rate":passed / len(rows),
            "adoption_rate":adopted / len(rows),
            "average_cost":sum(float(row.get("cost") or 0) for row in rows) / len(rows),
            "average_duration_ms":sum(float(row.get("duration_ms") or 0) for row in rows) / len(rows),
            "failure_codes":sorted({code for row in rows for code in row.get("failure_codes", [])}),
        }
    return result


def rank_models(events: list[dict], candidates: list[str], *, min_samples: int = 3) -> list[dict]:
    stats = aggregate_generation_history(events)
    ranked = []
    for model in candidates:
        row = stats.get(model, {"samples":0, "pass_rate":0.5, "adoption_rate":0.0,
                                "average_cost":0.0, "average_duration_ms":0.0,
                                "failure_codes":[]})
        confidence = min(1.0, row["samples"] / max(1, min_samples))
        quality = row["pass_rate"] * 0.75 + row["adoption_rate"] * 0.25
        score = (0.5 * (1 - confidence) + quality * confidence) * 100
        ranked.append({"model":model, **row, "confidence":round(confidence, 3),
                       "routing_score":round(score, 2)})
    return sorted(ranked, key=lambda item:(-item["routing_score"], item["average_cost"],
                                           item["average_duration_ms"], item["model"]))


def _signature_match(event: dict, context: dict) -> bool:
    signature = event.get("shot_signature") if isinstance(event.get("shot_signature"), dict) else {}
    return bool(context and signature and all(signature.get(key) == value
                for key, value in context.items()))


def rank_providers(events: list[dict], candidates: list[str], *, min_samples: int = 3,
                   context: dict | None = None) -> list[dict]:
    """Rank registry provider IDs while preserving concrete model versions in traces."""
    rows = [event for event in events or [] if isinstance(event, dict)]
    matched = [event for event in rows if _signature_match(event, context or {})]
    selected = matched if len(matched) >= min_samples else rows
    projected = [{**event, "model":str(event.get("provider") or event.get("model") or "")}
                 for event in selected]
    return [{"provider":row.pop("model"), **row}
            for row in rank_models(projected, candidates, min_samples=min_samples)]
