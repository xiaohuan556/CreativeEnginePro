"""Executable workflow skills for the AI production canvas.

The UI owns rendering and provider submission.  This module owns the stable,
testable contracts shared by automatic, checkpoint, and manual production.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Callable, Iterable

from .production_contracts import edit_plan_issues, sound_plan_issues


PathExists = Callable[[str], bool]


QC_CATEGORY_WEIGHTS = {
    "G1": 25,  # identity / asset continuity
    "G2": 20,  # space / axis / eyeline
    "G3": 20,  # action / temporal continuity
    "G4": 10,  # composition / camera
    "G5": 15,  # render defects / contamination
    "G6": 10,  # story / dialogue / sync
}
QC_BLOCKER_CODES = {f"F{index}" for index in range(1, 7)}
QC_REVIEW_SCORE = 65
QC_SOFT_DETERMINISTIC_CODES = {
    # These signals can be intentional in a locked-off shot or a designed
    # light change.  They require a human look, but are not one-vote rejects.
    "FREEZE_FRAME", "LIGHTING_DRIFT",
}


def _qc_verdict(score: int, blockers: Iterable[str], pass_score: int,
                explicitly_failed: bool = False) -> tuple[str, str]:
    """Return a stable three-tier verdict and user-facing severity.

    block  — must repair/regenerate; review — usable only after an explicit
    human decision; pass/info — may advance and retains any non-blocking notes.
    """
    if list(blockers) or int(score) < QC_REVIEW_SCORE:
        return "block", "block"
    if explicitly_failed or int(score) < int(pass_score):
        return "review", "review"
    return "pass", "info"


def _frontmatter_value(text: str, key: str) -> str:
    """Read one scalar from the deliberately small SKILL.md frontmatter."""
    match = re.match(r"^---\s*\n(.*?)\n---", text or "", re.S)
    if not match:
        return ""
    value = re.search(rf"(?m)^{re.escape(key)}:\s*(.+?)\s*$", match.group(1))
    return str(value.group(1) if value else "").strip().strip('"\'')


def _interface_value(text: str, key: str) -> str:
    value = re.search(rf"(?m)^\s{{2}}{re.escape(key)}:\s*(.+?)\s*$", text or "")
    return str(value.group(1) if value else "").strip().strip('"\'')


def discover_canvas_skills(root: str | os.PathLike | None = None) -> dict[str, dict]:
    """Discover repository skills without importing UI or requiring PyYAML.

    Folder names remain Codex-compatible kebab-case. Canvas IDs use underscores
    for backward compatibility with already persisted projects.
    """
    skill_root = Path(root) if root else Path(__file__).with_name("skills")
    result: dict[str, dict] = {}
    if not skill_root.is_dir():
        return result
    for folder in sorted(skill_root.iterdir(), key=lambda value: value.name):
        skill_file = folder / "SKILL.md"
        if not folder.is_dir() or not skill_file.is_file():
            continue
        try:
            body = skill_file.read_text(encoding="utf-8")
            interface_file = folder / "agents" / "openai.yaml"
            interface = (interface_file.read_text(encoding="utf-8")
                         if interface_file.is_file() else "")
            manifest_file = folder / "manifest.json"
            manifest = (json.loads(manifest_file.read_text(encoding="utf-8"))
                        if manifest_file.is_file() else {})
        except OSError:
            continue
        except (json.JSONDecodeError, TypeError):
            continue
        name = _frontmatter_value(body, "name") or folder.name
        description = _frontmatter_value(body, "description")
        skill_id = name.replace("-", "_")
        if skill_id in result and result[skill_id].get("skill_name") != name:
            continue
        result[skill_id] = {
            "title": _interface_value(interface, "display_name") or name,
            "description": (_interface_value(interface, "short_description") or
                            description or name),
            "default_prompt": _interface_value(interface, "default_prompt"),
            "skill_name": name,
            "skill_path": str(skill_file),
            "source": "repository",
            "version": str(manifest.get("version") or "1.0.0"),
            "handler": str(manifest.get("handler") or ""),
            "inputs": dict(manifest.get("inputs") or {}),
            "outputs": dict(manifest.get("outputs") or {}),
            "requires": dict(manifest.get("requires") or {}),
            "depends_on": list(manifest.get("depends_on") or []),
            "migrations": list(manifest.get("migrations") or []),
        }
    return result


def load_canvas_skill_specs(builtins: dict[str, dict] | None = None,
                            root: str | os.PathLike | None = None) -> dict[str, dict]:
    """Merge code-native canvas tools with auto-discovered repository skills."""
    result = {str(key): dict(value) for key, value in (builtins or {}).items()}
    for skill_id, spec in discover_canvas_skills(root).items():
        result[skill_id] = {**result.get(skill_id, {}), **spec}
    return result


def validate_skill_dependencies(skill_id: str, specs: dict[str, dict], *,
                                capabilities: Iterable[str] = (),
                                artifacts: Iterable[str] = ()) -> list[str]:
    spec = specs.get(skill_id) or {}
    issues = []
    available_caps, available_artifacts = set(capabilities), set(artifacts)
    for dependency in spec.get("depends_on", []):
        dep_id = str(dependency).replace("-", "_")
        if dep_id not in specs:
            issues.append(f"SKILL_DEPENDENCY_MISSING:{dependency}")
    required = spec.get("requires") if isinstance(spec.get("requires"), dict) else {}
    for capability in required.get("capabilities", []) or []:
        if capability not in available_caps:
            issues.append(f"CAPABILITY_MISSING:{capability}")
    for artifact in required.get("artifacts", []) or []:
        if artifact not in available_artifacts:
            issues.append(f"ARTIFACT_MISSING:{artifact}")
    return issues


def _bounded_score(value: object, default: int = 0) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return int(default)


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if result == result and abs(result) != float("inf") else float(default)
    except (TypeError, ValueError, OverflowError):
        return float(default)


def _normalized_codes(values: object) -> list[str]:
    return list(dict.fromkeys(
        re.sub(r"[^A-Z0-9_]+", "_", str(value).upper()).strip("_")
        for value in (values if isinstance(values, (list, tuple, set)) else [])
        if str(value).strip()))


def _normalize_categories(raw: object) -> dict[str, int]:
    values = raw if isinstance(raw, dict) else {}
    return {key: _bounded_score(values.get(key), 0)
            for key in QC_CATEGORY_WEIGHTS}


def _weighted_category_score(categories: dict[str, int]) -> int:
    return int(round(sum(categories[key] * weight
                         for key, weight in QC_CATEGORY_WEIGHTS.items()) / 100))


def normalize_clip_qc(review: dict, shot_ids: Iterable[str] = (),
                      pass_score: int = 80, deterministic_qc: dict | None = None) -> dict:
    """Normalize one rendered clip review into stable, blocker-aware rows.

    Providers are allowed to omit a top-level score or return a single clip row.
    A blocker always wins over a numeric score, which prevents a serious identity,
    geography, anatomy, contamination, motion, or dialogue defect from averaging out.
    """
    raw = review if isinstance(review, dict) else {}
    requested_ids = [str(value) for value in shot_ids if value]
    rows = raw.get("shots") if isinstance(raw.get("shots"), list) else []
    if not rows:
        rows = [{**raw, "id": requested_ids[0] if requested_ids else str(raw.get("id") or "")}]
    normalized = []
    for index, value in enumerate(rows):
        if not isinstance(value, dict):
            continue
        categories = _normalize_categories(value.get("categories"))
        supplied_category_score = any(categories.values())
        score = _bounded_score(
            value.get("score"),
            _weighted_category_score(categories) if supplied_category_score else 0)
        codes = _normalized_codes(value.get("issue_codes"))
        blockers = _normalized_codes(value.get("blockers"))
        blockers.extend(code for code in codes if code in QC_BLOCKER_CODES)
        blockers = list(dict.fromkeys(blockers))
        verdict, severity = _qc_verdict(
            score, blockers, pass_score, value.get("passed") is False)
        passed = verdict == "pass"
        normalized.append({
            "id": str(value.get("id") or
                      (requested_ids[index] if index < len(requested_ids) else "")),
            "score": score,
            "passed": passed,
            "verdict": verdict,
            "severity": severity,
            "review_required": verdict == "review",
            "requires_regeneration": verdict == "block",
            "categories": categories,
            "blockers": blockers,
            "issues": [str(item).strip() for item in value.get("issues", []) or []
                       if str(item).strip()],
            "issue_codes": codes,
            "repair_target": str(value.get("repair_target") or "video").lower(),
            "revision": str(value.get("revision") or "").strip(),
        })
    deterministic = deterministic_qc if isinstance(deterministic_qc, dict) else {}
    if deterministic.get("status") == "fail" and normalized:
        issues = _normalized_codes(deterministic.get("issues"))
        if not issues:
            issues = ["DETERMINISTIC_QC_FAILED"]
        hard_issues = [value for value in issues
                       if value not in QC_SOFT_DETERMINISTIC_CODES]
        soft_issues = [value for value in issues
                       if value in QC_SOFT_DETERMINISTIC_CODES]
        for row in normalized:
            if hard_issues:
                row["score"] = min(row["score"], QC_REVIEW_SCORE - 1)
                blocker = "F10" if any(
                    code in {"AV_SYNC_OFFSET", "SYNCNET_MISMATCH"}
                    for code in hard_issues) else "F9"
                row["blockers"] = list(dict.fromkeys(row["blockers"] + [blocker]))
                row["verdict"] = "block"
                row["severity"] = "block"
                row["requires_regeneration"] = True
                row["review_required"] = False
            elif soft_issues:
                row["score"] = min(row["score"], int(pass_score) - 1)
                row["verdict"] = "review"
                row["severity"] = "review"
                row["requires_regeneration"] = False
                row["review_required"] = True
            row["passed"] = False
            row["issue_codes"] = list(dict.fromkeys(row["issue_codes"] + issues))
            row["issues"] = list(dict.fromkeys(
                row["issues"] + [
                    ("确定性检测硬阻断：" if hard_issues else
                     "确定性检测需人工复核：") + "、".join(issues)]))
    row_score = int(round(sum(value["score"] for value in normalized) /
                          max(1, len(normalized))))
    score = (_bounded_score(raw.get("score"), 0) if raw.get("score") is not None
             else row_score)
    if deterministic.get("status") == "fail":
        score = min(score, row_score)
    if raw.get("passed") is False and normalized and all(
            value["passed"] for value in normalized):
        normalized[0]["passed"] = False
        normalized[0]["verdict"] = "review"
        normalized[0]["severity"] = "review"
        normalized[0]["review_required"] = True
        normalized[0]["requires_regeneration"] = False
    passed = bool(normalized) and all(value["passed"] for value in normalized)
    if raw.get("passed") is False:
        passed = False
    severity = ("block" if any(value["severity"] == "block" for value in normalized)
                else "review" if any(value["severity"] == "review"
                                     for value in normalized) else "info")
    if raw.get("passed") is False and severity == "info":
        severity = "review"
    return {
        "kind": "clip_qc",
        "summary": str(raw.get("summary") or "单段视频自动审片完成"),
        "score": score,
        "passed": passed,
        "verdict": "block" if severity == "block" else (
            "review" if severity == "review" else "pass"),
        "severity": severity,
        "review_required": severity == "review",
        "requires_regeneration": severity == "block",
        "shots": normalized,
        "deterministic_qc": deterministic,
        "reviewed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def normalize_sequence_qc(review: dict, pass_score: int = 85) -> dict:
    """Normalize adjacent-clip continuity findings for the sequence gate."""
    raw = review if isinstance(review, dict) else {}
    transitions = []
    for value in raw.get("transitions", []) if isinstance(
            raw.get("transitions"), list) else []:
        if not isinstance(value, dict):
            continue
        score = _bounded_score(value.get("score"), 0)
        codes = _normalized_codes(value.get("issue_codes"))
        blockers = _normalized_codes(value.get("blockers"))
        blockers.extend(code for code in codes if code in QC_BLOCKER_CODES)
        blockers = list(dict.fromkeys(blockers))
        deterministic = value.get("deterministic_qc") if isinstance(
            value.get("deterministic_qc"), dict) else {}
        if deterministic.get("status") == "fail":
            codes = list(dict.fromkeys(codes + [str(code) for code in
                                               deterministic.get("issues", [])]))
            blockers = list(dict.fromkeys(blockers + ["F7"]))
            score = min(score, int(pass_score) - 1)
        verdict, severity = _qc_verdict(
            score, blockers, pass_score, value.get("passed") is False)
        passed = verdict == "pass"
        transitions.append({
            "from_id": str(value.get("from_id") or ""),
            "to_id": str(value.get("to_id") or ""),
            "score": score,
            "passed": passed,
            "verdict": verdict,
            "severity": severity,
            "review_required": verdict == "review",
            "requires_regeneration": verdict == "block",
            "blockers": blockers,
            "issues": [str(item).strip() for item in value.get("issues", []) or []
                       if str(item).strip()],
            "issue_codes": codes,
            "repair_target": str(value.get("repair_target") or "video").lower(),
            "revision": str(value.get("revision") or "").strip(),
            "deterministic_qc":deterministic,
        })
    transition_score = int(round(sum(value["score"] for value in transitions) /
                                 max(1, len(transitions))))
    score = (_bounded_score(raw.get("score"), 0) if raw.get("score") is not None
             else transition_score)
    if any((value.get("deterministic_qc") or {}).get("status") == "fail"
           for value in transitions):
        score = min(score, transition_score)
    if raw.get("passed") is False and transitions and all(
            value["passed"] for value in transitions):
        transitions[0]["passed"] = False
        transitions[0]["verdict"] = "review"
        transitions[0]["severity"] = "review"
        transitions[0]["review_required"] = True
        transitions[0]["requires_regeneration"] = False
    passed = all(value["passed"] for value in transitions)
    if raw.get("passed") is False:
        passed = False
    severity = ("block" if any(value["severity"] == "block" for value in transitions)
                else "review" if any(value["severity"] == "review"
                                     for value in transitions) else "info")
    if raw.get("passed") is False and severity == "info":
        severity = "review"
    return {
        "kind": "sequence_qc",
        "summary": str(raw.get("summary") or "相邻视频段连续性审查完成"),
        "score": score,
        "passed": passed,
        "verdict": "block" if severity == "block" else (
            "review" if severity == "review" else "pass"),
        "severity": severity,
        "review_required": severity == "review",
        "requires_regeneration": severity == "block",
        "transitions": transitions,
        "reviewed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


@dataclass(frozen=True)
class ReadinessIssue:
    code: str
    message: str
    severity: str = "block"
    shot_id: str = ""
    node_id: str = ""
    repair_step: int = 0

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "shot_id": self.shot_id,
            "node_id": self.node_id,
            "repair_step": self.repair_step,
        }


@dataclass
class ReadinessReport:
    gate: str
    issues: list[ReadinessIssue] = field(default_factory=list)
    checked_shots: int = 0

    @property
    def blocked(self) -> bool:
        return any(value.severity == "block" for value in self.issues)

    @property
    def warnings(self) -> list[ReadinessIssue]:
        return [value for value in self.issues if value.severity == "warn"]

    @property
    def blockers(self) -> list[ReadinessIssue]:
        return [value for value in self.issues if value.severity == "block"]

    def as_dict(self) -> dict:
        return {
            "gate": self.gate,
            "ready": not self.blocked,
            "checked_shots": self.checked_shots,
            "issues": [value.as_dict() for value in self.issues],
        }

    def summary(self, limit: int = 4) -> str:
        if not self.issues:
            return f"{self.gate} 就绪检查通过"
        prefix = "未通过" if self.blocked else "通过（有提醒）"
        rows = [value.message for value in self.issues[:limit]]
        suffix = f"；另有 {len(self.issues) - limit} 项" if len(self.issues) > limit else ""
        return f"{self.gate} {prefix}：" + "；".join(rows) + suffix


GATE_REPAIR_STEPS = {
    "shot_plan": 1,
    "locked_assets": 2,
    "blocking": 3,
    "prompts": 4,
    "start_frames": 5,
    "video_anchors": 5,
    "videos": 6,
    "delivery": 7,
}


def _path_ready(path: object, exists: PathExists) -> bool:
    value = str(path or "")
    return bool(value and exists(value))


def _selected_shots(board: dict, shot_ids: Iterable[str] | None) -> list[dict]:
    shots = [value for value in board.get("shots", []) if isinstance(value, dict)]
    wanted = {str(value) for value in shot_ids or [] if value}
    return ([value for value in shots if str(value.get("id") or "") in wanted]
            if wanted else shots)


def _add(report: ReadinessReport, code: str, message: str, *, severity="block",
         shot: dict | None = None, node_id="", repair_step=0) -> None:
    report.issues.append(ReadinessIssue(
        code=code, message=message, severity=severity,
        shot_id=str((shot or {}).get("id") or ""), node_id=str(node_id or ""),
        repair_step=int(repair_step or GATE_REPAIR_STEPS.get(report.gate, 0)),
    ))


def evaluate_readiness(
        gate: str, board: dict, asset_records: Iterable[dict] = (), *,
        shot_ids: Iterable[str] | None = None,
        path_exists: PathExists = os.path.exists,
        require_end_frame: bool = False) -> ReadinessReport:
    """Evaluate a production gate without mutating the project.

    Gates intentionally separate blockers from warnings.  A missing approved
    source frame is a blocker; a missing optional ground-plane description is a
    warning because the existing prompt compiler can still repair it.
    """
    gate = str(gate or "shot_plan")
    shots = _selected_shots(board or {}, shot_ids)
    report = ReadinessReport(gate=gate, checked_shots=len(shots))
    step = GATE_REPAIR_STEPS.get(gate, 0)

    if not shots:
        _add(report, "NO_SHOTS", "没有可生产的镜头", repair_step=1)
        return report

    if gate == "shot_plan":
        for index, shot in enumerate(shots, 1):
            label = int(shot.get("number") or index)
            if not str(shot.get("id") or "").strip():
                _add(report, "SHOT_ID_MISSING", f"镜头 {label} 缺少稳定 ID",
                     shot=shot, repair_step=1)
            try:
                duration = float(shot.get("duration") or 0)
            except (TypeError, ValueError):
                duration = 0
            if duration <= 0:
                _add(report, "DURATION_INVALID", f"镜头 {label} 时长无效",
                     shot=shot, repair_step=1)
            if not any(str(shot.get(key) or "").strip()
                       for key in ("visual", "action_line", "action")):
                _add(report, "ACTION_MISSING", f"镜头 {label} 没有可执行画面或动作",
                     shot=shot, repair_step=1)
            if not any(str(shot.get(key) or "").strip()
                       for key in ("scene_name", "scene", "location", "scene_asset_id")):
                _add(report, "SCENE_UNSPECIFIED", f"镜头 {label} 未明确场景",
                     severity="warn", shot=shot, repair_step=1)
        return report

    assets = [value for value in asset_records if isinstance(value, dict) and
              str(value.get("asset_kind") or "") in {"scene", "character", "element"}]
    if gate == "locked_assets":
        if not assets:
            _add(report, "ASSETS_MISSING", "尚未建立角色、场景或关键道具资产",
                 repair_step=2)
            return report
        for asset in assets:
            label = str(asset.get("title") or asset.get("asset_name") or "未命名资产")
            node_id = str(asset.get("id") or "")
            if not bool(asset.get("locked")):
                _add(report, "ASSET_UNLOCKED", f"{label} 尚未锁定",
                     node_id=node_id, repair_step=2)
            if not _path_ready(asset.get("path"), path_exists):
                _add(report, "ASSET_FILE_MISSING", f"{label} 没有可用定稿文件",
                     node_id=node_id, repair_step=2)
            if str(asset.get("asset_kind") or "") == "character":
                refs = dict(asset.get("character_reference_set") or {})
                required = ("portrait", "face_closeup", "expressions", "turnaround")
                missing = [role for role in required
                           if not _path_ready(refs.get(role), path_exists)]
                if missing:
                    _add(report, "CHARACTER_AUTHORITY_INCOMPLETE",
                         f"{label} 的角色立绘、脸部近景、表情板或多视角设定不完整",
                         node_id=node_id, repair_step=2)
        return report

    if gate == "blocking":
        for index, shot in enumerate(shots, 1):
            label = int(shot.get("number") or index)
            frames = [value for value in shot.get("motion_keyframes", [])
                      if isinstance(value, dict)]
            if not bool(shot.get("blocking_ready")):
                _add(report, "BLOCKING_NOT_APPROVED", f"镜头 {label} 的空间调度尚未确认",
                     shot=shot, repair_step=3)
            if len(frames) < 3:
                _add(report, "MOTION_FRAMES_INCOMPLETE",
                     f"镜头 {label} 少于 3 个运动关键帧", shot=shot, repair_step=3)
            if not _path_ready(shot.get("motion_board_path") or shot.get("draft_panel"),
                               path_exists):
                _add(report, "MOTION_BOARD_MISSING", f"镜头 {label} 缺少多帧运动分镜图",
                     shot=shot, repair_step=3)
            if not str(shot.get("frame_start") or "").strip() or not str(
                    shot.get("frame_end") or "").strip():
                _add(report, "ENTRY_EXIT_MISSING", f"镜头 {label} 缺少入场或离场状态",
                     shot=shot, repair_step=3)
            missing_spatial = [key for key in (
                "spatial_layout", "camera_position", "axis_rule", "ground_plane")
                if not str(shot.get(key) or "").strip()]
            if missing_spatial:
                _add(report, "SPATIAL_CONTRACT_THIN",
                     f"镜头 {label} 的空间合同仍缺 {len(missing_spatial)} 项",
                     severity="warn", shot=shot, repair_step=3)
        return report

    if gate == "prompts":
        for index, shot in enumerate(shots, 1):
            label = int(shot.get("number") or index)
            if not str(shot.get("final_image_prompt") or "").strip():
                _add(report, "IMAGE_PROMPT_MISSING", f"镜头 {label} 缺少定稿图片提示词",
                     shot=shot, repair_step=4)
            if not str(shot.get("final_video_prompt") or "").strip():
                _add(report, "VIDEO_PROMPT_MISSING", f"镜头 {label} 缺少纯净视频提示词",
                     severity="warn", shot=shot, repair_step=4)
            if shot.get("production_ready") is False:
                _add(report, "SHOT_CONTRACT_REJECTED", f"镜头 {label} 的生成合同未通过",
                     shot=shot, repair_step=4)
        return report

    if gate in {"start_frames", "video_anchors"}:
        has_per_shot_endpoint_contract = any(
            "endpoint_pair_required" in shot or
            "endpoint_pair_forced" in shot or
            str(shot.get("keyframe_strategy") or "").lower() in {
                "first_frame", "first_last", "first+last", "single_first"}
            for shot in shots)
        for index, shot in enumerate(shots, 1):
            label = int(shot.get("number") or index)
            if not _path_ready(shot.get("selected_image_asset"), path_exists):
                _add(report, "START_FRAME_MISSING", f"镜头 {label} 尚未采用 K1 起始帧",
                     shot=shot, repair_step=5)
            shot_requires_end = (
                bool(shot.get("endpoint_pair_required") or
                     shot.get("endpoint_pair_forced") or
                     str(shot.get("keyframe_strategy") or "").lower() in {
                         "first_last", "first+last"})
                if has_per_shot_endpoint_contract else bool(require_end_frame))
            if (gate == "video_anchors" and shot_requires_end and
                    not _path_ready(shot.get("selected_end_image_asset"), path_exists)):
                _add(report, "END_FRAME_MISSING", f"镜头 {label} 尚未采用 Klast 结束帧",
                     severity="block",
                     shot=shot, repair_step=5)
        return report

    if gate in {"videos", "delivery"}:
        audio_by_shot = {
            str(value.get("shot_id") or "") for value in asset_records
            if isinstance(value, dict) and value.get("generator_kind") == "audio" and
            _path_ready(value.get("path"), path_exists)
        }
        for index, shot in enumerate(shots, 1):
            label = int(shot.get("number") or index)
            if not _path_ready(shot.get("selected_video_asset"), path_exists):
                _add(report, "VIDEO_MISSING", f"镜头 {label} 尚未定稿视频",
                     shot=shot, repair_step=6)
            dialogue = str(shot.get("dialogue") or "").strip()
            has_audio = (str(shot.get("id") or "") in audio_by_shot or
                         _path_ready(shot.get("dialogue_audio"), path_exists))
            if gate == "delivery" and dialogue and not has_audio:
                _add(report, "DIALOGUE_AUDIO_MISSING", f"镜头 {label} 有对白但没有独立音频",
                     shot=shot, repair_step=7)
        if gate == "delivery":
            sound_plan = board.get("sound_plan")
            edit_plan = board.get("edit_plan") or board.get("edit_timeline")
            sound_issues = sound_plan_issues(sound_plan, shots)
            edit_issues = edit_plan_issues(edit_plan, shots)
            if sound_issues:
                _add(report, "SOUND_PLAN_MISSING",
                     f"声音计划不完整（{len(sound_issues)} 项）：需补齐连续音床及逐镜声音层",
                     repair_step=7)
            if edit_issues:
                _add(report, "EDIT_PLAN_MISSING",
                     f"剪辑计划不完整（{len(edit_issues)} 项）：需校准镜头顺序、节奏和字幕安全区",
                     repair_step=7)
            master = str(board.get("delivery_path") or board.get("master_path") or "")
            if master and not path_exists(master):
                _add(report, "DELIVERY_FILE_MISSING", "记录的交付成片文件不存在",
                     repair_step=7)
        return report

    _add(report, "UNKNOWN_GATE", f"未知生产门禁：{gate}", repair_step=step)
    return report


NEXT_ACTION_BY_STAGE = {
    "": ("plan_shots", "shot_plan"),
    "shots_ready": ("generate_assets", "shot_plan"),
    "assets_generated": ("approve_assets", "locked_assets"),
    "assets_changed": ("approve_assets", "locked_assets"),
    "assets_ready": ("generate_blocking", "locked_assets"),
    "storyboard_panels_ready": ("compile_prompts", "blocking"),
    "prompts_ready": ("create_image_generators", "prompts"),
    "generators_ready": ("generate_start_frames", "prompts"),
    "start_image_candidates_ready": ("generate_end_frames", "start_frames"),
    "image_candidates_ready": ("generate_videos", "video_anchors"),
    "video_qc_pending": ("wait_for_video_qc", "videos"),
    "video_qc_review": ("review_video_qc", "videos"),
    "video_ready": ("generate_audio", "videos"),
    "audio_generators_ready": ("generate_audio", "videos"),
    "production_interrupted": ("resume", ""),
    "production_ready": ("complete", "delivery"),
}


def plan_next_action(stage: str, report: ReadinessReport | None = None,
                     mode: str = "checkpoints") -> dict:
    """Return the next orchestration decision in a UI-neutral form."""
    action, gate = NEXT_ACTION_BY_STAGE.get(str(stage or ""), ("wait", ""))
    blocked = bool(report and report.blocked)
    checkpoints = {
        "generate_blocking": "assets",
        "generate_end_frames": "start_images",
        "generate_videos": "images",
    }
    return {
        "stage": str(stage or ""),
        "action": "repair" if blocked else action,
        "intended_action": action,
        "gate": gate,
        "allowed": not blocked,
        "checkpoint": checkpoints.get(action, "") if mode == "checkpoints" else "",
        "reason": report.summary() if report else "",
    }


def append_workflow_event(source: dict, decision: dict, *, status="planned",
                          max_events: int = 80, telemetry: dict | None = None) -> dict:
    """Persist a compact, resumable orchestration trace on the source node."""
    event = {
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": str(status),
        "stage": str(decision.get("stage") or ""),
        "action": str(decision.get("action") or ""),
        "intended_action": str(decision.get("intended_action") or ""),
        "gate": str(decision.get("gate") or ""),
        "allowed": bool(decision.get("allowed", True)),
        "reason": str(decision.get("reason") or ""),
    }
    if telemetry:
        event["telemetry"] = normalize_generation_telemetry(telemetry)
    values = source.setdefault("workflow_trace", [])
    if not isinstance(values, list):
        values = []
        source["workflow_trace"] = values
    fingerprint = (event["stage"], event["action"], event["status"], event["reason"])
    if values:
        last = values[-1]
        last_fingerprint = tuple(last.get(key) for key in
                                 ("stage", "action", "status", "reason"))
        if fingerprint == last_fingerprint:
            return last
    values.append(event)
    if len(values) > max_events:
        del values[:-max_events]
    return event


def normalize_generation_telemetry(value: dict | None) -> dict:
    """Normalize reproducibility and cost evidence without storing prompt text."""
    raw = value if isinstance(value, dict) else {}
    prompt = str(raw.get("prompt") or "")
    references = [str(item) for item in raw.get("references", []) or [] if item]
    return {
        "provider": str(raw.get("provider") or ""),
        "model": str(raw.get("model") or ""),
        "operation": str(raw.get("operation") or ""),
        "prompt_version": str(raw.get("prompt_version") or ""),
        "prompt_sha256": (hashlib.sha256(prompt.encode("utf-8")).hexdigest()
                          if prompt else str(raw.get("prompt_sha256") or "")),
        "reference_count": len(references) if references else max(
            0, _safe_int(raw.get("reference_count"), 0)),
        "reference_sha256": [hashlib.sha256(item.encode("utf-8")).hexdigest()
                             for item in references],
        "seed": raw.get("seed"),
        "attempt": max(1, _safe_int(raw.get("attempt"), 1)),
        "duration_ms": max(0, _safe_int(raw.get("duration_ms"), 0)),
        "cost": max(0.0, _safe_float(raw.get("cost"), 0.0)),
        "currency": str(raw.get("currency") or ""),
        "outcome": str(raw.get("outcome") or "unknown"),
        "failure_codes": _normalized_codes(raw.get("failure_codes")),
        "adopted": bool(raw.get("adopted", False)),
        "shot_signature": (dict(raw.get("shot_signature"))
                           if isinstance(raw.get("shot_signature"), dict) else {}),
    }


def append_generation_event(source: dict, telemetry: dict, *,
                            max_events: int = 200) -> dict:
    """Persist one compact generation observation for later routing analysis."""
    event = {
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        **normalize_generation_telemetry(telemetry),
    }
    values = source.setdefault("generation_trace", [])
    if not isinstance(values, list):
        values = []
        source["generation_trace"] = values
    values.append(event)
    if len(values) > max_events:
        del values[:-max_events]
    return event


REPAIR_STEP_BY_TARGET = {
    "asset": 2,
    "blocking": 3,
    "prompt": 4,
    "image": 5,
    "video": 6,
    "audio": 7,
}

_REPAIR_KEYWORDS = (
    ("audio", ("音频", "对白", "配音", "口型", "lipsync", "voice", "audio")),
    ("asset", ("身份", "换脸", "服装", "角色不一致", "场景不一致", "identity", "outfit")),
    ("blocking", ("越轴", "站位", "方位", "视线", "空间", "地面线", "轴线", "blocking")),
    ("video", ("闪烁", "运动", "速度", "镜头抖动", "temporal", "flicker", "motion")),
    ("image", ("畸形", "多指", "文字", "水印", "构图", "anatomy", "watermark")),
)


def _infer_repair_target(result: dict, shot: dict | None) -> str:
    explicit = str(result.get("repair_target") or "").lower()
    if explicit in REPAIR_STEP_BY_TARGET:
        return explicit
    text = " ".join([
        " ".join(str(value) for value in result.get("issues", []) or []),
        " ".join(str(value) for value in result.get("issue_codes", []) or []),
        str(result.get("revision") or ""),
    ]).lower()
    for target, words in _REPAIR_KEYWORDS:
        if any(word.lower() in text for word in words):
            return target
    return "video" if (shot or {}).get("selected_video_asset") else "image"


def build_repair_plan(review: dict, shots: Iterable[dict]) -> dict:
    """Normalize a multimodal review into minimal, shot-scoped repair work."""
    shot_map = {str(value.get("id") or ""): value for value in shots
                if isinstance(value, dict) and value.get("id")}
    items = []
    for raw in review.get("shots", []) if isinstance(review, dict) else []:
        if not isinstance(raw, dict) or bool(raw.get("passed")):
            continue
        shot_id = str(raw.get("id") or "")
        shot = shot_map.get(shot_id, {})
        target = _infer_repair_target(raw, shot)
        revision = str(raw.get("revision") or "").strip()
        issues = [str(value).strip() for value in raw.get("issues", []) or []
                  if str(value).strip()]
        issue_codes = [re.sub(r"[^A-Z0-9_]+", "_", str(value).upper()).strip("_")
                       for value in raw.get("issue_codes", []) or [] if str(value).strip()]
        items.append({
            "shot_id": shot_id,
            "shot_number": int(shot.get("number") or 0),
            "target": target,
            "rewind_step": REPAIR_STEP_BY_TARGET[target],
            "generator_kind": target if target in {"image", "video", "audio"} else "",
            "issues": issues,
            "issue_codes": issue_codes,
            "revision": revision,
            "preserve": {
                "asset": [],
                "blocking": ["locked_assets"],
                "prompt": ["locked_assets", "blocking"],
                "image": ["locked_assets", "blocking", "prompts"],
                "video": ["locked_assets", "blocking", "prompts", "approved_images"],
                "audio": ["locked_assets", "blocking", "prompts", "approved_images", "videos"],
            }[target],
        })
    counts = {target: sum(1 for value in items if value["target"] == target)
              for target in REPAIR_STEP_BY_TARGET}
    return {
        "summary": str(review.get("summary") or "视觉审片完成"),
        "score": int(review.get("score") or 0),
        "items": items,
        "counts": counts,
        "ready": not items,
    }
