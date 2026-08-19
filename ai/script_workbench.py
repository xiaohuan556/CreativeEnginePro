"""Small, UI-neutral contracts for the canvas script workbench."""
from __future__ import annotations

from datetime import datetime, timezone


def script_metrics(text: str) -> dict:
    value = str(text or "").strip()
    return {
        "characters": len(value),
        "scenes": sum(1 for line in value.splitlines()
                      if line.strip().upper().startswith(("INT.", "EXT.", "内景", "外景", "场景"))),
        "dialogue_lines": sum(1 for line in value.splitlines()
                              if ("：" in line or ":" in line) and len(line.strip()) > 2),
    }


def save_script_version(record: dict, text: str, reason: str = "手动保存",
                        *, limit: int = 30) -> dict:
    """Append a deduplicated, bounded snapshot and return it."""
    value = str(text or "").strip()
    versions = record.setdefault("script_versions", [])
    if versions and str(versions[-1].get("content") or "") == value:
        return versions[-1]
    snapshot = {
        "version": int(versions[-1].get("version") or len(versions)) + 1 if versions else 1,
        "content": value,
        "reason": str(reason or "保存"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    versions.append(snapshot)
    del versions[:-max(1, int(limit or 30))]
    record["script_version"] = snapshot["version"]
    return snapshot


def previous_script_version(record: dict) -> dict | None:
    versions = [item for item in record.get("script_versions", [])
                if isinstance(item, dict)]
    return versions[-2] if len(versions) >= 2 else None
