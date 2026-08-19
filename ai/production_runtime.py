"""UI-neutral runtime decisions for skills, routing and rough-cut assembly."""
from __future__ import annotations

from typing import Iterable

from .production_intelligence import rank_providers, shot_signature
from .production_skills import validate_skill_dependencies


def _number(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)


def recommend_provider(events: list[dict], candidates: Iterable[str],
                       shot: dict | None = None) -> dict:
    names = [str(value) for value in candidates if value]
    ranked = rank_providers(events, names, context=shot_signature(shot or {}))
    if not ranked or int(ranked[0].get("samples") or 0) < 3:
        return {"provider":"", "confidence":0.0, "reason":"insufficient_evidence"}
    return ranked[0]


def skill_runtime_issues(skill_id: str, specs: dict[str, dict], *,
                         provider_capabilities: Iterable[str] = (),
                         artifacts: Iterable[str] = ()) -> list[str]:
    return validate_skill_dependencies(
        skill_id, specs, capabilities=provider_capabilities, artifacts=artifacts)


def compile_rough_cut(shots: Iterable[dict], *, genre_profile: dict | None = None) -> dict:
    """Create an executable edit decision list with J/L-cut recommendations."""
    values = [dict(value) for value in shots if isinstance(value, dict)]
    cursor = 0.0
    timeline = []
    for index, shot in enumerate(values):
        duration = max(0.1, _number(shot.get("duration"), 0.1))
        dialogue = str(shot.get("dialogue") or shot.get("voiceover") or "").strip()
        previous_dialogue = str(values[index - 1].get("dialogue") or "").strip() if index else ""
        audio_bridge = "j_cut" if dialogue and index else "l_cut" if previous_dialogue else "continuous_bed"
        transition = shot.get("transition") or {}
        transition_type = (str(transition.get("type") or "cut") if isinstance(
            transition, dict) else str(transition or "cut"))
        timeline.append({
            "shot_id":str(shot.get("id") or ""), "order":index + 1,
            "source_in":0.0, "source_out":duration,
            "timeline_start":round(cursor, 3),
            "transition_in":transition_type,
            "audio_bridge":audio_bridge,
            "cut_reason":str(shot.get("story_function") or
                             shot.get("dramatic_purpose") or "推进叙事信息"),
        })
        cursor += duration
    profile = genre_profile or {}
    return {
        "version":1, "auto_generated":True,
        "rhythm":f"{profile.get('genre') or 'drama'} · 按故事功能和动作终点剪辑",
        "target_average_shot_length":round(cursor / max(1, len(timeline)), 3),
        "timeline":timeline, "subtitle_safe_margin":0.08,
        "total_duration":round(cursor, 3),
    }


def compile_sound_plan(shots: Iterable[dict], *, genre_profile: dict | None = None) -> dict:
    """Build a five-layer sound plan from executable shot actions and dialogue."""
    values = [dict(value) for value in shots if isinstance(value, dict)]
    rows = []
    for shot in values:
        action = str(shot.get("primary_action") or shot.get("action") or "")
        supplied = str(shot.get("sound") or "").strip()
        foley = []
        for cue, label in (("走", "脚步"), ("跑", "急促脚步"), ("门", "门体与把手"),
                           ("雨", "雨滴击打表面"), ("衣", "衣料摩擦"),
                           ("拿", "手部接触物体"), ("放", "物体落位")):
            if cue in action:
                foley.append(label)
        rows.append({
            "shot_id":str(shot.get("id") or ""),
            "room_tone":"保持同场景连续底噪",
            "ambience":[supplied] if supplied else [],
            "foley":foley, "spot_effects":[],
            "score":"克制使用，服从对白和叙事转折",
            "dialogue_mode":"sync" if shot.get("dialogue") else
                            "voiceover" if shot.get("voiceover") else "none",
            "audio_path":str(shot.get("dialogue_audio") or ""),
            "loudness_target_lufs":-14.0,
        })
    genre = str((genre_profile or {}).get("genre") or "drama")
    return {"version":1, "auto_generated":True,
            "continuous_bed":"按场景保持连续环境底噪，跨切点不断裂",
            "music_arc":f"{genre}：音乐只在情绪转折进入，避免覆盖对白",
            "master_lufs":-14.0, "true_peak_dbtp":-1.0, "shots":rows}
