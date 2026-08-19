"""AI 导演分镜的数据模型、旧数据迁移与连续性归一化。"""
from __future__ import annotations

import json
import re
import uuid
import ast

from .production_contracts import (
    normalize_edit_plan, normalize_genre_profile, normalize_sound_plan,
)
from .production_runtime import compile_rough_cut, compile_sound_plan

MAX_GENERATED_SHOT_DURATION = 8.0
STORYBOARD_SCHEMA_VERSION = 3


def _json_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        if set(value).intersection({"type", "text", "content"}):
            text = value.get("text") or value.get("content") or ""
            if isinstance(text, dict):
                text = text.get("value") or text.get("text") or ""
            return str(text or "")
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        pieces = []
        for part in value:
            if isinstance(part, str):
                pieces.append(part)
            elif isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if isinstance(text, dict):
                    text = text.get("value") or text.get("text")
                if text:
                    pieces.append(str(text))
            else:
                text = getattr(part, "text", None)
                if text:
                    pieces.append(str(text))
        if pieces:
            return "".join(pieces)
    return str(value or "")


def _json_object(value):
    if isinstance(value, dict):
        if (set(value).intersection({"type", "text", "content"}) and
                not set(value).intersection({
                    "shots", "shot_outline", "scenes", "characters", "storyboard"})):
            return None
        return value
    if (isinstance(value, list) and len(value) == 1 and
            isinstance(value[0], dict) and
            not set(value[0]).intersection({"type", "text", "content"})):
        return value[0]
    return None


def extract_json(text: str) -> dict:
    """从模型回复中提取 JSON 对象，兼容代码块、内容分片和常见轻微格式错误。"""
    direct = _json_object(text)
    if direct is not None:
        return direct
    raw = _json_text(text).strip().lstrip("\ufeff")
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    candidates = [raw]
    without_trailing_commas = re.sub(r",\s*([}\]])", r"\1", raw)
    if without_trailing_commas != raw:
        candidates.append(without_trailing_commas)
    decoder = json.JSONDecoder()
    # Prefer a complete (possibly lightly repaired) document before scanning
    # embedded objects; otherwise a valid nested scene can be mistaken for the
    # whole contract when the outer object only has a trailing comma.
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            # Some compatible gateways JSON-encode the assistant content twice.
            if isinstance(value, str):
                value = json.loads(value)
            result = _json_object(value)
            if result is not None:
                return result
        except (json.JSONDecodeError, TypeError):
            pass
    for candidate in candidates:
        for match in re.finditer(r"{", candidate):
            try:
                value, _end = decoder.raw_decode(candidate[match.start():])
            except json.JSONDecodeError:
                continue
            result = _json_object(value)
            if result is not None:
                return result
    for candidate in candidates:
        try:
            result = _json_object(ast.literal_eval(candidate))
            if result is not None:
                return result
        except (ValueError, SyntaxError):
            pass
    raise ValueError("AI 没有返回可识别的分镜 JSON，请重试")


def _text(value, default: str = "") -> str:
    return str(value if value is not None else default).strip()


def _unique_texts(values) -> list[str]:
    if not isinstance(values, (list, tuple)):
        values = [values] if values else []
    return list(dict.fromkeys(_text(value) for value in values if _text(value)))


def _normalize_production_bible(raw: dict) -> dict:
    """保存 GPT 的全片创作约束；旧工程缺少时也给出稳定空结构。"""
    source = raw.get("production_bible") or raw.get("story_bible") or {}
    source = dict(source) if isinstance(source, dict) else {}
    return {
        "logline": _text(source.get("logline") or raw.get("summary")),
        "audience": _text(source.get("audience")),
        "format": _text(source.get("format")),
        "tone": _text(source.get("tone")),
        "visual_style": _text(source.get("visual_style")),
        "color_script": _text(source.get("color_script")),
        "dialogue_style": _text(source.get("dialogue_style")),
        "world_rules": _unique_texts(source.get("world_rules", [])),
        "continuity_rules": _unique_texts(
            source.get("continuity_rules", [])),
    }


def _normalize_screenplay(raw: dict) -> dict:
    """保存先于逐镜头分解的故事剧本，供界面审阅和后续分段共同引用。"""
    source = raw.get("screenplay") or raw.get("story_script") or {}
    source = dict(source) if isinstance(source, dict) else {}
    beats = []
    for index, value in enumerate(source.get("beats", []) or []):
        if not isinstance(value, dict):
            continue
        beats.append({
            "id": _text(value.get("id") or f"beat_{index + 1:02d}"),
            "start": round(max(0.0, _number(value.get("start"), 0.0)), 3),
            "end": round(max(0.0, _number(value.get("end"), 0.0)), 3),
            "purpose": _text(value.get("purpose")),
            "summary": _text(value.get("summary")),
            "entry_state": _text(value.get("entry_state")),
            "exit_state": _text(value.get("exit_state")),
        })
    return {
        "hook": _text(source.get("hook")),
        "setup": _text(source.get("setup")),
        "conflict": _text(source.get("conflict")),
        "turn": _text(source.get("turn") or source.get("twist")),
        "ending": _text(source.get("ending")),
        "dialogue_style": _text(source.get("dialogue_style")),
        "sound_direction": _text(source.get("sound_direction")),
        "beats": beats,
    }


def build_shot_contract(shot: dict) -> dict:
    """把一个镜头转换为可校验的拍摄合同，而不是松散的一段 Prompt。"""
    character_bindings = [
        {
            "asset_id": _text(value.get("asset_id")),
            "name": _text(value.get("name")),
            "version": _version(value.get("version")),
            "role": _text(value.get("role") or "subject"),
            "outfit_state": _text(value.get("outfit_state")),
            "appearance_state": _text(value.get("appearance_state")),
            "required": bool(value.get("required", True)),
        }
        for value in shot.get("character_bindings", []) or []
        if isinstance(value, dict) and _text(value.get("asset_id"))
    ]
    element_bindings = [
        {
            "asset_id": _text(value.get("asset_id")),
            "name": _text(value.get("name")),
            "version": _version(value.get("version")),
            "mode": _text(value.get("mode") or "exact"),
            "placement": _text(value.get("placement")),
            "required": bool(value.get("required", True)),
        }
        for value in shot.get("element_bindings", []) or []
        if isinstance(value, dict) and _text(value.get("asset_id"))
    ]
    performance = dict(shot.get("performance") or {})
    visual_performance = {
        key: performance.get(key)
        for key in (
            "line_type", "speaker", "speaker_asset_id", "dialogue",
            "emotion", "emotion_intensity", "gaze_target", "expression",
            "gesture", "body_action", "pause_before", "pause_after", "mode")
        if performance.get(key) not in (None, "")
    }
    return {
        "version": 1,
        "beat_id": _text(shot.get("beat_id")),
        "dramatic_purpose": _text(shot.get("dramatic_purpose")),
        "entry_state": _text(shot.get("entry_state")),
        "exit_state": _text(shot.get("exit_state")),
        "continuity_notes": _text(shot.get("continuity_notes")),
        "scene": {
            "asset_id": _text(shot.get("scene_asset_id") or shot.get("scene_id")),
            "name": _text(shot.get("scene_name")),
            "required": True,
        },
        "characters": character_bindings,
        "elements": element_bindings,
        "required_character_names": _unique_texts(
            shot.get("character_names", [])),
        "required_element_names": _unique_texts(
            shot.get("element_names", [])),
        "composition": {
            "shot_size": _text(shot.get("shot_size")),
            "camera_slot": _text(shot.get("camera_slot")),
            "screen_direction": _text(shot.get("screen_direction")),
            "blocking": _text(shot.get("blocking")),
        },
        "action": _text(shot.get("action")),
        "performance": visual_performance,
    }


def _version(value, default: int = 1) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _nonnegative_int(value, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _normalize_character_bindings(raw: dict) -> list[dict]:
    """把 V2 绑定和旧 character_id(s) 合并为稳定的主体绑定列表。"""
    bindings = []
    source = raw.get("character_bindings")
    if isinstance(source, list):
        for value in source:
            if isinstance(value, str):
                value = {"asset_id": value}
            if not isinstance(value, dict):
                continue
            asset_id = _text(value.get("asset_id") or value.get("character_id")
                             or value.get("id"))
            if not asset_id:
                continue
            bindings.append({
                "asset_id": asset_id,
                "name": _text(value.get("name")),
                "version": _version(value.get("version")),
                "role": _text(value.get("role") or "subject"),
                "outfit_state": _text(value.get("outfit_state")
                                      or value.get("wardrobe_state")),
                "appearance_state": _text(value.get("appearance_state")),
                "required": bool(value.get("required", True)),
            })

    legacy_ids = _unique_texts(raw.get("character_id"))
    legacy_ids.extend(
        item for item in _unique_texts(raw.get("character_ids"))
        if item not in legacy_ids)
    known = {item["asset_id"] for item in bindings}
    for asset_id in legacy_ids:
        if asset_id not in known:
            bindings.append({
                "asset_id": asset_id,
                "name": "",
                "version": 1,
                "role": "subject",
                "outfit_state": _text(raw.get("outfit_state")
                                      or raw.get("wardrobe_state")),
                "appearance_state": "",
                "required": True,
            })
    return bindings


def _normalize_element_bindings(raw: dict) -> list[dict]:
    """把指定元素/道具绑定统一成可版本化的列表。"""
    bindings = []
    source = raw.get("element_bindings") or raw.get("prop_bindings")
    if isinstance(source, list):
        for value in source:
            if isinstance(value, str):
                value = {"asset_id": value}
            if not isinstance(value, dict):
                continue
            asset_id = _text(value.get("asset_id") or value.get("element_id")
                             or value.get("id"))
            if not asset_id:
                continue
            bindings.append({
                "asset_id": asset_id,
                "name": _text(value.get("name")),
                "version": _version(value.get("version")),
                "mode": _text(value.get("mode") or "exact"),
                "placement": _text(value.get("placement")),
                "required": bool(value.get("required", True)),
            })

    primary = _text(raw.get("element_id"))
    legacy_ids = _unique_texts(primary)
    legacy_ids.extend(
        item for item in _unique_texts(raw.get("element_ids"))
        if item not in legacy_ids)
    known = {item["asset_id"] for item in bindings}
    for asset_id in legacy_ids:
        if asset_id not in known:
            bindings.append({
                "asset_id": asset_id,
                "name": "",
                "version": 1,
                "mode": (_text(raw.get("element_mode") or "exact")
                         if asset_id == primary else "exact"),
                "placement": (_text(raw.get("element_placement"))
                              if asset_id == primary else ""),
                "required": True,
            })
    return bindings


def sync_legacy_bindings(shot: dict) -> dict:
    """让新旧 UI 字段双向兼容；V2 列表是权威数据。"""
    character_bindings = _normalize_character_bindings(shot)
    element_bindings = _normalize_element_bindings(shot)
    shot["character_bindings"] = character_bindings
    shot["element_bindings"] = element_bindings

    character_ids = [item["asset_id"] for item in character_bindings]
    shot["character_id"] = character_ids[0] if character_ids else ""
    shot["character_ids"] = character_ids[1:]

    element_ids = [item["asset_id"] for item in element_bindings]
    shot["element_id"] = element_ids[0] if element_ids else ""
    shot["element_ids"] = element_ids[1:]
    if element_bindings:
        shot["element_mode"] = element_bindings[0]["mode"]
        shot["element_placement"] = element_bindings[0]["placement"]

    scene_id = _text(shot.get("scene_asset_id") or shot.get("scene_id"))
    shot["scene_asset_id"] = scene_id
    shot["scene_id"] = scene_id
    shot["scene_version"] = _version(shot.get("scene_version"))
    shot["shot_contract"] = build_shot_contract(shot)
    return shot


def rebuild_continuity(board: dict) -> dict:
    """按连续场景建立前后镜头与锚点关系，不覆盖导演明确给出的分组。"""
    shots = board.get("shots") if isinstance(board.get("shots"), list) else []
    previous = None
    group_index = 0
    current_group = ""
    current_scene_key = None

    for shot in shots:
        sync_legacy_bindings(shot)
        explicit_group = _text(shot.get("continuity_group")
                               or shot.get("scene_block_id"))
        scene_key = (_text(shot.get("scene_asset_id"))
                     or _text(shot.get("location"))
                     or _text(shot.get("scene_name")))
        if explicit_group:
            group = explicit_group
            current_group = group
            current_scene_key = scene_key
        else:
            # 已绑定场景变化时切分连续段；没有结构化场景时保持逐镜独立，
            # 防止仅凭长段自然语言把两个不同地点错误串联。
            if not scene_key:
                group_index += 1
                group = f"scene_block_{group_index:03d}"
            else:
                if scene_key != current_scene_key:
                    group_index += 1
                    current_group = f"scene_block_{group_index:03d}"
                    current_scene_key = scene_key
                group = current_group
        shot["continuity_group"] = group

        same_group = bool(previous and previous.get("continuity_group") == group)
        if not _text(shot.get("previous_shot_id")) and same_group:
            shot["previous_shot_id"] = _text(previous.get("id"))
        elif not same_group:
            shot["previous_shot_id"] = ""
        if not _text(shot.get("anchor_source_shot_id")) and same_group:
            shot["anchor_source_shot_id"] = _text(previous.get("id"))
        elif not same_group:
            shot["anchor_source_shot_id"] = ""
        if not _text(shot.get("generation_mode")):
            shot["generation_mode"] = (
                "derive_from_anchor" if same_group else "compose_from_assets")
        shot["next_shot_id"] = ""
        if same_group:
            previous["next_shot_id"] = _text(shot.get("id"))
        previous = shot
    return board


VIDEO_LINK_MODES = {"auto", "cut", "continue", "bridge"}
PERFORMANCE_LINE_TYPES = {"none", "dialogue", "voiceover"}
PERFORMANCE_MODES = {"auto", "native", "lipsync", "driving", "none"}


def _bounded_number(value, default: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(_number(value, default), maximum))


def normalize_performance(raw: dict) -> dict:
    """把对白、情绪和动作意图归一成可执行的单镜头表演数据。"""
    source = raw.get("performance")
    source = dict(source) if isinstance(source, dict) else {}
    dialogue = _text(
        source.get("dialogue") or raw.get("dialogue") or raw.get("voiceover"))
    explicit_type = _text(source.get("line_type") or raw.get("line_type")).lower()
    speaker = _text(source.get("speaker") or raw.get("speaker"))
    if explicit_type not in PERFORMANCE_LINE_TYPES:
        if source.get("dialogue") is not None or raw.get("dialogue") is not None:
            line_type = "dialogue" if dialogue else "none"
        else:
            match = re.match(r"^\s*([^：:\n]{1,18})[：:]", dialogue)
            line_type = "dialogue" if match else "voiceover" if dialogue else "none"
            if match and not speaker:
                speaker = _text(match.group(1))
    else:
        line_type = explicit_type
    mode = _text(source.get("mode") or raw.get("performance_mode") or "auto").lower()
    if mode not in PERFORMANCE_MODES:
        mode = "auto"
    return {
        "line_type": line_type,
        "speaker": speaker,
        "speaker_asset_id": _text(
            source.get("speaker_asset_id") or raw.get("speaker_asset_id")),
        "dialogue": dialogue,
        "emotion": _text(source.get("emotion") or raw.get("emotion") or "自然"),
        "emotion_intensity": round(_bounded_number(
            source.get("emotion_intensity", raw.get("emotion_intensity")),
            0.5, 0.0, 1.0), 2),
        "gaze_target": _text(source.get("gaze_target") or raw.get("gaze_target")),
        "expression": _text(source.get("expression") or raw.get("expression")),
        "gesture": _text(source.get("gesture") or raw.get("gesture")),
        "body_action": _text(source.get("body_action") or raw.get("body_action")),
        "pause_before": round(_bounded_number(
            source.get("pause_before"), 0.0, 0.0, 3.0), 2),
        "pause_after": round(_bounded_number(
            source.get("pause_after", raw.get("pause")), 0.2, 0.0, 3.0), 2),
        "mode": mode,
        "driving_video": _text(source.get("driving_video")),
        "audio_duration": round(_bounded_number(
            source.get("audio_duration", raw.get("dialogue_audio_duration")),
            0.0, 0.0, 60.0), 3),
        "needs_dialogue_split": bool(source.get("needs_dialogue_split", False)),
    }


def route_shot_generation(shot: dict) -> str:
    """根据镜头内容选择生产路线；该结果用于调度，不暴露模型细节给用户。"""
    performance = shot.get("performance") or {}
    line_type = _text(performance.get("line_type")).lower()
    if line_type == "dialogue" and _text(performance.get("dialogue")):
        return "dialogue_performance"
    if line_type == "voiceover" and _text(performance.get("dialogue")):
        return "narration"
    combined = " ".join(_text(shot.get(key)) for key in (
        "scene", "action", "video_prompt")).lower()
    if any(cue in combined for cue in (
            "反应", "愣住", "震惊", "皱眉", "微笑", "落泪",
            "reaction", "surprised", "smiles", "frowns", "tears")):
        return "reaction"
    character_count = len(_unique_texts(shot.get("character_names", [])))
    exact_elements = [
        value for value in shot.get("element_bindings", []) or []
        if isinstance(value, dict) and value.get("required", True) and
        _text(value.get("mode") or "exact") == "exact"]
    if exact_elements and character_count == 0:
        return "exact_insert"
    if any(cue in combined for cue in (
            "奔跑", "追逐", "跳跃", "打斗", "爆炸", "running", "chase",
            "jumps", "fight", "explosion")):
        return "cinematic_action"
    return "cinematic"


def reflow_storyboard_timing(board: dict, start: float = 0.0) -> dict:
    """按当前镜头时长重排时间线，供对白真实时长回写后使用。"""
    cursor = max(0.0, float(start or 0.0))
    for shot in board.get("shots", []) or []:
        duration = max(0.5, _number(shot.get("duration"), 3.0))
        shot["start"] = round(cursor, 3)
        shot["duration"] = round(duration, 3)
        cursor += duration
    board["duration"] = round(cursor, 3)
    return rebuild_continuity(board)


def apply_dialogue_audio_duration(board: dict, shot_id: str,
                                  audio_duration: float) -> dict | None:
    """用真实对白长度校准一个镜头，并重排后续镜头起点。"""
    target = next((shot for shot in board.get("shots", []) or []
                   if _text(shot.get("id")) == _text(shot_id)), None)
    if target is None:
        return None
    performance = target.setdefault("performance", normalize_performance(target))
    duration = max(0.0, _number(audio_duration, 0.0))
    performance["audio_duration"] = round(duration, 3)
    required = (
        duration + _number(performance.get("pause_before"), 0.0) +
        _number(performance.get("pause_after"), 0.2))
    required = max(0.5, round(required, 3))
    target["duration"] = required
    target["dialogue_audio_duration"] = round(duration, 3)
    performance["needs_dialogue_split"] = required > MAX_GENERATED_SHOT_DURATION
    reflow_storyboard_timing(board)
    return target


def normalize_video_link_mode(value) -> str:
    """把导演/旧工程中的镜头衔接写法归一为四种稳定模式。"""
    raw = _text(value).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "": "auto", "automatic": "auto", "智能": "auto", "自动": "auto",
        "direct": "cut", "hard_cut": "cut", "直接切镜": "cut", "硬切": "cut",
        "continuation": "continue", "extend": "continue", "续拍": "continue",
        "连续续拍": "continue", "承接上镜": "continue",
        "keyframe": "bridge", "keyframes": "bridge", "first_last": "bridge",
        "首尾帧": "bridge", "首尾过渡": "bridge", "匹配过渡": "bridge",
    }
    mode = aliases.get(raw, raw)
    return mode if mode in VIDEO_LINK_MODES else "auto"


def resolve_video_link_mode(shot: dict, next_shot: dict | None = None) -> str:
    """解析当前镜头到下一镜的实际视频衔接方式。

    默认采用影视剪辑中的直接切镜。只有拆分出的同一长镜头、明确写出不中断
    的连续镜头，或明确要求画面变形/匹配过渡时才启用生成式承接。
    """
    mode = normalize_video_link_mode(shot.get("video_link_mode"))
    if mode != "auto":
        return mode
    if not next_shot:
        return "cut"

    same_group = bool(
        _text(shot.get("continuity_group"))
        and _text(shot.get("continuity_group")) ==
        _text(next_shot.get("continuity_group")))
    same_scene = bool(
        _text(shot.get("scene_asset_id") or shot.get("scene_id"))
        and _text(shot.get("scene_asset_id") or shot.get("scene_id")) ==
        _text(next_shot.get("scene_asset_id") or next_shot.get("scene_id")))

    part = _nonnegative_int(shot.get("continuation_part"))
    next_part = _nonnegative_int(next_shot.get("continuation_part"))
    if part and next_part == part + 1 and same_group:
        return "continue"

    transition = shot.get("transition") or {}
    transition_type = _text(
        transition.get("type") if isinstance(transition, dict) else transition
    ).lower().replace("-", "_").replace(" ", "_")
    if transition_type in {
        "morph", "match", "match_cut", "transform", "bridge",
        "generated_transition", "seamless_transform",
    }:
        return "bridge"

    current_text = " ".join(_text(shot.get(key)) for key in (
        "scene", "action", "camera", "video_prompt"))
    next_text = " ".join(_text(next_shot.get(key)) for key in (
        "scene", "action", "camera", "video_prompt"))
    combined = f"{current_text} {next_text}".lower()
    if any(cue in combined for cue in (
        "首尾过渡", "匹配过渡", "逐渐变为", "渐变成", "变身为",
        "morph into", "transforms into", "seamless transform",
    )):
        return "bridge"
    if (same_group or same_scene) and any(cue in combined for cue in (
        "一镜到底", "不中断", "连续长镜头", "连续续拍", "承接上一段",
        "same take", "without a cut", "continuous take",
        "continue the same shot", "continues from the previous clip",
    )):
        return "continue"
    return "cut"


def normalize_storyboard(data: dict, requested_duration: float = 30.0) -> dict:
    """迁移并补齐镜头字段；工程保存统一使用 Schema V2。"""
    board = dict(data or {})
    shots = board.get("shots") if isinstance(board.get("shots"), list) else []
    expanded = []
    for raw in shots:
        if not isinstance(raw, dict):
            continue
        raw_duration = max(0.5, _number(raw.get("duration"), 3.0))
        if raw_duration <= MAX_GENERATED_SHOT_DURATION:
            expanded.append(dict(raw))
            continue
        base_start = _number(raw.get("start"), 0.0)
        remaining = raw_duration
        offset = 0.0
        part = 1
        total_parts = int((raw_duration + MAX_GENERATED_SHOT_DURATION - 0.001)
                          // MAX_GENERATED_SHOT_DURATION)
        split_group = (_text(raw.get("continuity_group"))
                       or f"continuous_{uuid.uuid4().hex[:8]}")
        while remaining > 0.001:
            chunk = min(MAX_GENERATED_SHOT_DURATION, remaining)
            item = dict(raw)
            item["id"] = ""  # 每个连续段必须拥有独立镜头 id
            item["start"] = base_start + offset
            item["duration"] = chunk
            item["scene"] = (
                f"{raw.get('scene') or raw.get('visual') or '连续画面'}"
                f"（连续段 {part}/{total_parts}）")
            item["continuity_group"] = split_group
            item["continuation_part"] = part
            item["continuation_total"] = total_parts
            # 自动拆开的前半段必须续拍到下一段；最后一段再恢复原镜头原本的
            # 出镜方式，避免导演把一个超长镜头标成 cut 后拆段失去连续性。
            item["video_link_mode"] = (
                "continue" if remaining > MAX_GENERATED_SHOT_DURATION
                else raw.get("video_link_mode", "auto"))
            if part > 1:
                item["voiceover"] = ""
                item["dialogue"] = ""
                performance = dict(item.get("performance") or {})
                performance.update({"dialogue": "", "line_type": "none"})
                item["performance"] = performance
                item["pause"] = 0
            if remaining > MAX_GENERATED_SHOT_DURATION:
                item["transition"] = {"type": "cut", "duration": 0.0}
            expanded.append(item)
            remaining -= chunk
            offset += chunk
            part += 1

    normalized = []
    cursor = 0.0
    for index, raw in enumerate(expanded):
        duration = max(0.5, min(_number(raw.get("duration"), 3.0), 30.0))
        start = _number(raw.get("start"), cursor)
        if start < cursor - 0.05:
            start = cursor
        pause = max(0.0, min(_number(raw.get("pause"), 0.0), duration - 0.1))
        transition = raw.get("transition", {})
        if isinstance(transition, str):
            transition = {"type": transition, "duration": 0.2}
        if not isinstance(transition, dict):
            transition = {}
        transition = {
            "type": _text(transition.get("type") or "cut"),
            "duration": max(0.0, min(_number(transition.get("duration"), 0.0), 2.0)),
        }
        assets = raw.get("assets") if isinstance(raw.get("assets"), list) else []
        asset_kinds = {
            _text(item.get("path")): _text(item.get("kind") or "image")
            for item in assets if isinstance(item, dict) and item.get("path")
        }
        legacy_selected = _text(raw.get("selected_asset"))
        preview_asset = _text(raw.get("preview_asset") or legacy_selected)
        selected_image_asset = _text(
            raw.get("selected_image_asset") or raw.get("anchor_frame_id"))
        selected_video_asset = _text(raw.get("selected_video_asset"))
        # 旧项目只有 selected_asset：按结果类型迁移到独立的图片/视频定稿槽。
        if legacy_selected and asset_kinds.get(legacy_selected) == "image":
            selected_image_asset = selected_image_asset or legacy_selected
        if legacy_selected and asset_kinds.get(legacy_selected) == "video":
            selected_video_asset = selected_video_asset or legacy_selected
        character_names = _unique_texts(raw.get("character_names", []))
        for value in (raw.get("character_bindings") or []):
            if isinstance(value, dict) and _text(value.get("name")):
                character_names.extend(
                    name for name in [_text(value.get("name"))]
                    if name not in character_names)
        element_names = _unique_texts(raw.get("element_names", []))
        for value in (raw.get("element_bindings") or raw.get("prop_bindings") or []):
            if isinstance(value, dict) and _text(value.get("name")):
                element_names.extend(
                    name for name in [_text(value.get("name"))]
                    if name not in element_names)
        performance = normalize_performance(raw)
        shot = {
            "id": _text(raw.get("id") or f"shot_{index + 1:03d}_{uuid.uuid4().hex[:6]}"),
            "number": index + 1,
            "start": round(start, 2),
            "duration": round(duration, 2),
            "scene": _text(raw.get("scene") or raw.get("visual") or "待补充画面"),
            "beat_id": _text(raw.get("beat_id")),
            "dramatic_purpose": _text(
                raw.get("dramatic_purpose") or raw.get("shot_purpose")),
            "entry_state": _text(raw.get("entry_state")),
            "exit_state": _text(raw.get("exit_state")),
            "continuity_notes": _text(raw.get("continuity_notes")),
            "draft_panel": _text(
                raw.get("draft_panel") or raw.get("storyboard_draft")),
            "scene_name": _text(raw.get("scene_name") or raw.get("location")),
            "shot_size": _text(raw.get("shot_size") or "中景"),
            "camera": _text(raw.get("camera") or "固定镜头"),
            "camera_slot": _text(raw.get("camera_slot")),
            "screen_direction": _text(raw.get("screen_direction")),
            "blocking": _text(raw.get("blocking")),
            "character": _text(raw.get("character")),
            "character_names": character_names,
            "action": _text(raw.get("action")),
            "voiceover": _text(raw.get("voiceover") or performance.get("dialogue")),
            "performance": performance,
            "dialogue_audio": _text(raw.get("dialogue_audio")),
            "dialogue_audio_status": _text(raw.get("dialogue_audio_status")),
            "dialogue_audio_source_text": _text(
                raw.get("dialogue_audio_source_text")),
            "dialogue_audio_duration": round(_bounded_number(
                raw.get("dialogue_audio_duration") or
                performance.get("audio_duration"), 0.0, 0.0, 60.0), 3),
            "pause": round(pause, 2),
            "transition": transition,
            "sound": _text(raw.get("sound")),
            "asset_type": _text(raw.get("asset_type") or "image"),
            "image_prompt": _text(raw.get("image_prompt") or raw.get("visual_prompt")),
            "video_prompt": _text(raw.get("video_prompt") or raw.get("visual_prompt")),
            "negative_prompt": _text(raw.get("negative_prompt")),
            "assets": assets,
            # selected_asset 保留为旧代码兼容字段，语义等同“当前预览”。
            "selected_asset": preview_asset,
            "preview_asset": preview_asset,
            "selected_image_asset": selected_image_asset,
            "selected_video_asset": selected_video_asset,
            "status": _text(raw.get("status") or "draft"),
            "scene_asset_id": _text(raw.get("scene_asset_id") or raw.get("scene_id")),
            "scene_version": _version(raw.get("scene_version")),
            "character_bindings": raw.get("character_bindings", []),
            "element_bindings": raw.get("element_bindings")
                                or raw.get("prop_bindings", []),
            "character_id": _text(raw.get("character_id")),
            "character_ids": _unique_texts(raw.get("character_ids", [])),
            "element_id": _text(raw.get("element_id")),
            "element_ids": _unique_texts(raw.get("element_ids", [])),
            "element_names": element_names,
            "element_mode": _text(raw.get("element_mode") or "exact"),
            "element_placement": _text(raw.get("element_placement")),
            "continuity_group": _text(raw.get("continuity_group")
                                      or raw.get("scene_block_id")),
            "previous_shot_id": _text(raw.get("previous_shot_id")),
            "next_shot_id": _text(raw.get("next_shot_id")),
            "anchor_source_shot_id": _text(raw.get("anchor_source_shot_id")),
            "anchor_frame_id": selected_image_asset,
            "generation_mode": (_text(raw.get("generation_mode"))
                                if _text(raw.get("generation_mode")) in {
                                    "compose_from_assets", "derive_from_anchor"}
                                else ""),
            "video_link_mode": normalize_video_link_mode(
                raw.get("video_link_mode") or raw.get("shot_link_mode")),
            "continuation_part": _nonnegative_int(raw.get("continuation_part")),
            "continuation_total": _nonnegative_int(raw.get("continuation_total")),
        }
        shot["generation_route"] = (
            _text(raw.get("generation_route")) or route_shot_generation(shot))
        shot["performance"]["route"] = shot["generation_route"]
        sync_legacy_bindings(shot)
        normalized.append(shot)
        cursor = start + duration

    board["schema_version"] = STORYBOARD_SCHEMA_VERSION
    board["id"] = _text(board.get("id") or f"director_{uuid.uuid4().hex[:10]}")
    board["title"] = _text(board.get("title") or "未命名分镜")
    board["summary"] = _text(board.get("summary"))
    board["production_bible"] = _normalize_production_bible(board)
    board["screenplay"] = _normalize_screenplay(board)
    board["duration"] = round(cursor if normalized else requested_duration, 2)
    board["characters"] = (board.get("characters")
                           if isinstance(board.get("characters"), list) else [])
    board["asset_inventory"] = (board.get("asset_inventory")
                                if isinstance(board.get("asset_inventory"), dict) else {})
    board["visual_bible"] = (board.get("visual_bible")
                             if isinstance(board.get("visual_bible"), dict) else {})
    board["continuity_policy"] = (board.get("continuity_policy")
                                  if isinstance(board.get("continuity_policy"), dict)
                                  else {"prefer_image_to_video": True,
                                        "inherit_previous_keyframe": True})
    board["shots"] = normalized
    board["genre_profile"] = normalize_genre_profile(board.get("genre_profile") or {
        "genre":board["production_bible"].get("format") or "drama"})
    board["sound_plan"] = (normalize_sound_plan(board.get("sound_plan"), normalized)
                           if board.get("sound_plan") else compile_sound_plan(
                               normalized, genre_profile=board["genre_profile"]))
    incoming_edit = board.get("edit_plan") or board.get("edit_timeline")
    board["edit_plan"] = (normalize_edit_plan(incoming_edit, normalized)
                          if incoming_edit else compile_rough_cut(
                              normalized, genre_profile=board["genre_profile"]))
    return rebuild_continuity(board)


def _number(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)
