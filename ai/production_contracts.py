"""Stable production contracts shared by planning, generation, QC and delivery."""
from __future__ import annotations

from copy import deepcopy
from typing import Iterable


CONTRACT_VERSION = 1


def _number(value: object, default: float) -> float:
    try:
        result = float(value)
        return result if result == result and abs(result) != float("inf") else float(default)
    except (TypeError, ValueError, OverflowError):
        return float(default)


def _rows(value: object) -> list[dict]:
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(
        value, (list, tuple)) else []


def _texts(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item) for item in value if str(item).strip()]

GENRE_DEFAULTS = {
    "drama": {"average_shot_length": 4.5, "camera_energy": "restrained", "performance": "naturalistic"},
    "action": {"average_shot_length": 2.2, "camera_energy": "motivated_dynamic", "performance": "physical"},
    "comedy": {"average_shot_length": 5.0, "camera_energy": "mostly_locked", "performance": "timing_first"},
    "horror": {"average_shot_length": 5.5, "camera_energy": "controlled_stillness", "performance": "suppressed"},
    "commercial": {"average_shot_length": 2.5, "camera_energy": "product_motivated", "performance": "precise"},
    "documentary": {"average_shot_length": 5.0, "camera_energy": "observational", "performance": "authentic"},
}

MODEL_CAPABILITY_PROFILES = {
    "veo": {"operations":["text_to_video", "image_to_video"], "first_frame":True,
            "last_frame":True, "reference_assets":3, "native_audio":True,
            "durations":[4, 6, 8], "ratios":["16:9", "9:16"], "seed":False},
    "seedance": {"operations":["text_to_video", "image_to_video"], "first_frame":True,
                 "last_frame":True, "reference_assets":9, "native_audio":True,
                 "durations":[5, 10], "ratios":["adaptive", "16:9", "9:16"], "seed":False},
    "kling": {"operations":["text_to_video", "image_to_video"], "first_frame":True,
              "last_frame":False, "reference_assets":1, "native_audio":False,
              "durations":[], "ratios":[], "seed":False},
}

FAILURE_CATALOG = {
    "F1": {"category":"identity", "repair_target":"asset", "label":"人物身份漂移"},
    "F2": {"category":"space", "repair_target":"blocking", "label":"空间、轴线或视线错误"},
    "F3": {"category":"continuity", "repair_target":"asset", "label":"服装或场景连续性漂移"},
    "F4": {"category":"motion", "repair_target":"prompt", "label":"动作预算过载"},
    "F5": {"category":"endpoint", "repair_target":"prompt", "label":"动作终点缺失"},
    "F6": {"category":"camera", "repair_target":"blocking", "label":"摄影机逻辑错误"},
    "F7": {"category":"edit", "repair_target":"video", "label":"镜头衔接状态不匹配"},
    "F8": {"category":"anatomy", "repair_target":"image", "label":"肢体或面部畸形"},
    "F9": {"category":"flicker", "repair_target":"video", "label":"时间闪烁或纹理跳变"},
    "F10": {"category":"audio", "repair_target":"audio", "label":"音画或口型不同步"},
    "F11": {"category":"tool", "repair_target":"prompt", "label":"模型能力不匹配"},
    "F12": {"category":"subtitle", "repair_target":"edit", "label":"字幕安全区或可读性失败"},
}


def normalize_genre_profile(value: object) -> dict:
    raw = dict(value) if isinstance(value, dict) else {}
    genre = str(raw.get("genre") or "drama").lower()
    defaults = GENRE_DEFAULTS.get(genre, GENRE_DEFAULTS["drama"])
    return {"version":CONTRACT_VERSION, "genre":genre, **defaults, **raw}


def normalize_sound_plan(value: object, shots: Iterable[dict] = ()) -> dict:
    raw = dict(value) if isinstance(value, dict) else {}
    shot_list = list(shots)
    has_program_sound = any(str(shot.get("dialogue") or shot.get("sound") or "").strip()
                            for shot in shot_list)
    shot_rows = {str(item.get("shot_id") or ""):item
                 for item in _rows(raw.get("shots"))}
    normalized = []
    for shot in shot_list:
        shot_id = str(shot.get("id") or "")
        row = shot_rows.get(shot_id, {})
        normalized.append({
            "shot_id":shot_id,
            "room_tone":str(row.get("room_tone") or
                            ("保持场景连续底噪" if has_program_sound else "intentional_silence")),
            "ambience":_texts(row.get("ambience")),
            "foley":_texts(row.get("foley")),
            "spot_effects":_texts(row.get("spot_effects")),
            "score":str(row.get("score") or ""),
            "dialogue_mode":str(row.get("dialogue_mode") or
                                ("sync" if shot.get("dialogue") else "none")),
            "audio_path":str(row.get("audio_path") or shot.get("dialogue_audio") or ""),
            "loudness_target_lufs":_number(row.get("loudness_target_lufs"), -14.0),
        })
    return {"version":CONTRACT_VERSION,
            "auto_generated":not bool(raw),
            "continuous_bed":str(raw.get("continuous_bed") or
                                  ("保持场景连续底噪" if has_program_sound else "intentional_silence")),
            "music_arc":str(raw.get("music_arc") or ""),
            "master_lufs":_number(raw.get("master_lufs"), -14.0),
            "true_peak_dbtp":_number(raw.get("true_peak_dbtp"), -1.0),
            "shots":normalized}


def normalize_edit_plan(value: object, shots: Iterable[dict] = ()) -> dict:
    if isinstance(value, list):
        raw = {"timeline":value}
    else:
        raw = dict(value) if isinstance(value, dict) else {}
    existing = {str(item.get("shot_id") or ""):item
                for item in _rows(raw.get("timeline"))}
    timeline = []
    cursor = 0.0
    for shot in shots:
        shot_id = str(shot.get("id") or "")
        row = existing.get(shot_id, {})
        duration = max(0.0, _number(shot.get("duration"), 0.0))
        timeline.append({
            "shot_id":shot_id, "order":len(timeline) + 1,
            "source_in":_number(row.get("source_in"), 0.0),
            "source_out":_number(row.get("source_out"), duration),
            "timeline_start":_number(row.get("timeline_start"), cursor),
            "transition_in":str(row.get("transition_in") or "cut"),
            "audio_bridge":str(row.get("audio_bridge") or "none"),
        })
        cursor += duration
    average = cursor / max(1, len(timeline))
    return {"version":CONTRACT_VERSION, "auto_generated":not bool(raw),
            "rhythm":str(raw.get("rhythm") or "按故事节拍切换，动作完成后出点，连续音床桥接"),
            "target_average_shot_length":_number(raw.get("target_average_shot_length"), average),
            "timeline":timeline,
            "subtitle_safe_margin":_number(raw.get("subtitle_safe_margin"), 0.08)}


def sound_plan_issues(plan: object, shots: Iterable[dict] = ()) -> list[str]:
    value = plan if isinstance(plan, dict) else {}
    rows = {str(row.get("shot_id") or ""):row for row in _rows(value.get("shots"))}
    issues = []
    if not str(value.get("continuous_bed") or "").strip():
        issues.append("SOUND_CONTINUOUS_BED_MISSING")
    for shot in shots:
        row = rows.get(str(shot.get("id") or ""), {})
        if not any((str(row.get("room_tone") or "").strip(), row.get("ambience"),
                    row.get("foley"), row.get("spot_effects"),
                    str(row.get("score") or "").strip())):
            issues.append(f"SOUND_SHOT_EMPTY:{shot.get('id') or ''}")
    return issues


def edit_plan_issues(plan: object, shots: Iterable[dict] = ()) -> list[str]:
    value = plan if isinstance(plan, dict) else {}
    shot_list = list(shots)
    timeline = _rows(value.get("timeline"))
    issues = []
    wanted = [str(shot.get("id") or "") for shot in shot_list]
    actual = [str(row.get("shot_id") or "") for row in timeline]
    if actual != wanted:
        issues.append("EDIT_TIMELINE_ORDER_MISMATCH")
    if len(shot_list) > 1 and not str(value.get("rhythm") or "").strip():
        issues.append("EDIT_RHYTHM_MISSING")
    if not 0.03 <= _number(value.get("subtitle_safe_margin"), 0) <= 0.2:
        issues.append("SUBTITLE_SAFE_MARGIN_INVALID")
    return issues


def model_profile(name: str, overrides: dict | None = None) -> dict:
    key = str(name or "").lower()
    profile = deepcopy(MODEL_CAPABILITY_PROFILES.get(key, {
        "operations":[], "first_frame":False, "last_frame":False,
        "reference_assets":0, "native_audio":False, "durations":[],
        "ratios":[], "seed":False,
    }))
    profile.update(overrides or {})
    profile["name"] = key
    profile["version"] = CONTRACT_VERSION
    return profile


def capability_issues(profile: dict, request: dict) -> list[str]:
    issues = []
    operation = str(request.get("operation") or "")
    if operation and operation not in profile.get("operations", []):
        issues.append("F11:模型不支持请求的操作")
    if request.get("last_frame") and not profile.get("last_frame"):
        issues.append("F11:模型不支持尾帧控制")
    count = int(request.get("reference_count") or 0)
    if count > int(profile.get("reference_assets") or 0):
        issues.append("F11:参考资产数量超过模型控制面")
    if request.get("native_audio") and not profile.get("native_audio"):
        issues.append("F11:模型不支持原生音频")
    return issues
