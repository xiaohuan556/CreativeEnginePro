"""Resumable, bounded LLM planning for the production canvas.

The production contract is deliberately generated in two layers:
1. a compact project foundation (assets, scene authority and shot outline),
2. detailed directing contracts in small shot batches.

Keeping the calls bounded prevents a proxy timeout from discarding an entire
storyboard while preserving the same final data contract used by the canvas.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .director_protocol import DIRECTOR_PROTOCOL_VERSION, planning_instructions


STORYBOARD_PLANNING_VERSION = 2
DEFAULT_SHOT_BATCH_SIZE = 2


def _chinese_integer(text: str) -> int | None:
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
              "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    match = re.search(r"([零〇一二两三四五六七八九]?十?[零〇一二三四五六七八九]?)\s*秒", text)
    token = match.group(1) if match else ""
    if not token:
        return None
    if "十" in token:
        left, right = token.split("十", 1)
        return (digits.get(left, 1) * 10) + digits.get(right, 0)
    return digits.get(token)


def parse_duration_seconds(value: Any, default: float = 5.0) -> float:
    """Normalize common LLM duration forms such as ``6秒`` or ``about 6s``."""
    if value is None or value == "":
        return float(default)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
    else:
        text = str(value).strip().replace("，", ".")
        match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
        chinese = _chinese_integer(text) if match is None else None
        if match is None and chinese is None:
            raise ValueError(f"镜头时长“{text}”无法识别，请使用数字秒数，例如 6 或 6秒")
        seconds = float(match.group(0)) if match is not None else float(chinese)
    if not 0.1 <= seconds <= 600:
        raise ValueError(f"镜头时长 {seconds:g} 秒超出有效范围（0.1–600 秒）")
    return seconds


def planning_fingerprint(idea: str, shot_count: int, style: str,
                         provider: str, model: str,
                         temperature: float) -> str:
    payload = json.dumps({
        "version": STORYBOARD_PLANNING_VERSION,
        "idea": str(idea or "").strip(),
        "shot_count": int(shot_count),
        "style": str(style or "").strip(),
        "provider": str(provider or "").strip(),
        "model": str(model or "").strip(),
        "temperature": round(float(temperature), 3),
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def shot_batch_ranges(shot_count: int,
                      batch_size: int = DEFAULT_SHOT_BATCH_SIZE) -> list[tuple[int, int]]:
    count = max(1, int(shot_count))
    size = max(1, int(batch_size))
    return [(start, min(count, start + size - 1))
            for start in range(1, count + 1, size)]


def batch_key(start: int, end: int) -> str:
    return f"{int(start)}-{int(end)}"


def new_planning_checkpoint(*, fingerprint: str, shot_count: int,
                            style: str, provider: str, model: str,
                            temperature: float,
                            batch_size: int = DEFAULT_SHOT_BATCH_SIZE) -> dict:
    return {
        "version": STORYBOARD_PLANNING_VERSION,
        "fingerprint": str(fingerprint),
        "shot_count": int(shot_count),
        "style": str(style),
        "provider": str(provider),
        "model": str(model),
        "temperature": float(temperature),
        "batch_size": max(1, int(batch_size)),
        "foundation": {},
        "batches": {},
    }


def checkpoint_matches(checkpoint: Any, fingerprint: str) -> bool:
    return bool(
        isinstance(checkpoint, dict)
        and int(checkpoint.get("version") or 0) == STORYBOARD_PLANNING_VERSION
        and str(checkpoint.get("fingerprint") or "") == str(fingerprint)
    )


def _dict_rows(value: Any) -> list[dict]:
    if isinstance(value, dict):
        rows = []
        for name, row in value.items():
            if isinstance(row, dict):
                item = dict(row)
                item.setdefault("name", str(name))
            else:
                item = {"name": str(name), "description": str(row or "")}
            rows.append(item)
        return rows
    if isinstance(value, list):
        return [dict(row) for row in value if isinstance(row, dict)]
    return []


def _unwrap_contract(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    current = value
    for _ in range(3):
        if any(key in current for key in (
                "shot_outline", "shots", "shot_list", "scenes", "locations")):
            return current
        nested = next((current.get(key) for key in (
            "foundation", "storyboard", "project", "contract", "result", "data")
            if isinstance(current.get(key), dict)), None)
        if nested is None:
            break
        current = nested
    return current


def normalize_foundation(value: dict, shot_count: int) -> dict:
    value = _unwrap_contract(value)
    if not isinstance(value, dict) or not value:
        raise ValueError("项目基础合同不是 JSON 对象")
    requested_count = int(shot_count)
    auto_count = requested_count <= 0
    count = max(1, requested_count)
    outlines = _dict_rows(
        value.get("shot_outline") or value.get("shots") or
        value.get("shot_list") or value.get("outline"))
    if auto_count and not 1 <= len(outlines) <= 24:
        raise ValueError(
            f"自动拆镜应返回 1–24 镜，实际返回 {len(outlines)} 镜")
    if not auto_count and len(outlines) != count:
        raise ValueError(f"镜头骨架应为 {count} 镜，实际返回 {len(outlines)} 镜")
    if auto_count:
        count = len(outlines)
    normalized = {
        "title": str(value.get("title") or "AI 故事板").strip(),
        "summary": str(value.get("summary") or "").strip(),
        "visual_bible": str(value.get("visual_bible") or "").strip(),
        "characters": _dict_rows(
            value.get("characters") or value.get("cast") or value.get("roles")),
        "scenes": _dict_rows(
            value.get("scenes") or value.get("locations") or value.get("sets")),
        "elements": _dict_rows(
            value.get("elements") or value.get("props") or value.get("assets")),
        "shot_outline": [],
    }
    if not normalized["scenes"]:
        raise ValueError("项目基础合同缺少场景母版")
    for index, row in enumerate(outlines, 1):
        item = dict(row)
        item["shot_number"] = index
        if not item.get("scene_name"):
            item["scene_name"] = item.get("scene") or item.get("location") or ""
        if not item.get("visual"):
            item["visual"] = (item.get("description") or item.get("action") or
                              item.get("content") or "")
        item.setdefault("duration", 5)
        item["duration"] = parse_duration_seconds(item.get("duration"), 5)
        item.setdefault("shot_size", "中景")
        item.setdefault("character_names", [])
        item.setdefault("element_names", [])
        item.setdefault("dialogue", "")
        item.setdefault("transition", "切")
        normalized["shot_outline"].append(item)
    return normalized


def normalize_shot_batch(value: dict, foundation: dict,
                         start: int, end: int) -> list[dict]:
    value = _unwrap_contract(value)
    if not isinstance(value, dict) or not value:
        raise ValueError("镜头细化结果不是 JSON 对象")
    raw = _dict_rows(
        value.get("shots") or value.get("shot_list") or
        value.get("shot_details") or value.get("details"))
    expected_numbers = list(range(int(start), int(end) + 1))
    by_number = {}
    for row in raw:
        try:
            number = int(row.get("shot_number") or row.get("number") or 0)
        except (TypeError, ValueError):
            number = 0
        if number in expected_numbers and number not in by_number:
            by_number[number] = row
    if len(by_number) != len(expected_numbers):
        if len(raw) == len(expected_numbers):
            by_number = dict(zip(expected_numbers, raw))
        else:
            raise ValueError(
                f"镜头批次 {start}-{end} 应返回 {len(expected_numbers)} 镜，"
                f"实际返回 {len(raw)} 镜")
    outlines = _dict_rows(foundation.get("shot_outline"))
    result = []
    for number in expected_numbers:
        outline = outlines[number - 1] if number - 1 < len(outlines) else {}
        merged = {**outline, **dict(by_number[number])}
        merged["shot_number"] = number
        merged["duration"] = parse_duration_seconds(merged.get("duration"), 5)
        result.append(merged)
    return result


def next_missing_batch(checkpoint: dict) -> tuple[int, int] | None:
    count = max(1, int(checkpoint.get("shot_count") or 1))
    size = max(1, int(checkpoint.get("batch_size") or DEFAULT_SHOT_BATCH_SIZE))
    batches = checkpoint.get("batches") if isinstance(checkpoint.get("batches"), dict) else {}
    for start, end in shot_batch_ranges(count, size):
        rows = batches.get(batch_key(start, end))
        if not isinstance(rows, list) or len(rows) != end - start + 1:
            return start, end
    return None


def checkpoint_progress(checkpoint: dict) -> tuple[int, int]:
    count = max(1, int(checkpoint.get("shot_count") or 1))
    batches = checkpoint.get("batches") if isinstance(checkpoint.get("batches"), dict) else {}
    completed = sum(len(rows) for rows in batches.values() if isinstance(rows, list))
    return min(count, completed), count


def merge_checkpoint(checkpoint: dict) -> dict:
    foundation = checkpoint.get("foundation")
    if not isinstance(foundation, dict) or not foundation:
        raise ValueError("尚未完成项目基础合同")
    missing = next_missing_batch(checkpoint)
    if missing is not None:
        raise ValueError(f"镜头批次 {missing[0]}-{missing[1]} 尚未完成")
    rows: list[dict] = []
    batches = checkpoint.get("batches") or {}
    count = max(1, int(checkpoint.get("shot_count") or 1))
    size = max(1, int(checkpoint.get("batch_size") or DEFAULT_SHOT_BATCH_SIZE))
    for start, end in shot_batch_ranges(count, size):
        rows.extend(dict(row) for row in batches[batch_key(start, end)])
    result = {key: value for key, value in foundation.items()
              if key != "shot_outline"}
    result["shots"] = rows
    return result


def _temperature_direction(temperature: float) -> str:
    if temperature <= 0.3:
        return ("严格执行原剧本，不增加原文没有的事件、角色、道具或反转；"
                "优先保证可拍性、空间合同和动作连续性。")
    if temperature >= 0.7:
        return ("可在不改变剧情事实、人物动机和对白含义的前提下丰富视觉节拍，"
                "但不得擅自改写故事。")
    return ("平衡忠实与视觉表达，保持全部剧情事实，只补足拍摄所需的动作衔接、"
            "站位、空间、视线和镜头节奏。")


def foundation_messages(idea: str, shot_count: int, style: str,
                        temperature: float) -> list[dict]:
    requested_count = int(shot_count)
    auto_count = requested_count <= 0
    count = max(1, requested_count)
    count_instruction = (
        "shot_outline 镜头数由你根据定稿自动决定，范围 1–24 镜。"
        "先识别叙事节拍与可执行动作边界：每镜只有一个主要动作；道具开合/拿放、"
        "人物进出画、明显机位变化和场景变化应形成拆镜边界；单镜通常 3–6 秒。"
        "不要为了凑数拆分静止状态，也不要把多个状态变化硬塞进同一镜。"
        "允许为建立空间、遮盖动作断点或调节节奏安排有叙事功能的空镜、反应镜和细节镜，"
        "但禁止加入纯装饰、与故事无关的镜头。"
        if auto_count else
        f"shot_outline 必须正好 {count} 项。")
    system = (
        "你是电影导演与制片架构师。此调用只建立项目基础合同和简洁镜头骨架，"
        "不要展开逐镜摄影、地平线、消失点、首尾帧或生图提示词。只输出 JSON 对象，不要 Markdown。"
        "输出字段：title、summary、visual_bible、characters、scenes、elements、shot_outline。"
        "characters 每项含 name、description、image_prompt；description 只写不可变身份与服装。"
        "scenes 每项代表唯一物理地点，含 name、location_id、description、image_prompt、fixtures、"
        "walls、activity_bbox_xy、camera_zones、states。灯光、天气和时间变化必须进入 states，"
        "不得拆成多个地点。fixtures 必须给稳定 id、label、type、bbox_xy、height、fixed=true 和"
        " master/reverse/left/right 的 view_bboxes。所有 bbox_xy、view_bboxes、activity_bbox_xy 和"
        "camera_zones.bbox_xy 必须严格采用 [x,y,width,height]，四个值均为0到1小数，禁止百分数和"
        "[left,top,right,bottom] 写法。elements 每项含 name、description、image_prompt。"
        f"{count_instruction}每项仅含 shot_number、story_function、scene_name、"
        "scene_state、character_names、element_names、shot_size、duration、visual、dialogue、transition。"
        "名称必须精确引用前面定义的角色、场景和道具。"
        f"项目风格：{style}。拆镜取向：{_temperature_direction(float(temperature))}"
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": str(idea or "").strip()}]


def shot_batch_messages(idea: str, foundation: dict, start: int, end: int,
                        style: str) -> list[dict]:
    outlines = _dict_rows(foundation.get("shot_outline"))[int(start) - 1:int(end)]
    compact_foundation = {
        "visual_bible": foundation.get("visual_bible") or "",
        "characters": foundation.get("characters") or [],
        "scenes": foundation.get("scenes") or [],
        "elements": foundation.get("elements") or [],
        # The full compact outline preserves edit rhythm and continuity across
        # batch boundaries without asking this call to regenerate other shots.
        "full_shot_outline": foundation.get("shot_outline") or [],
        "requested_outline": outlines,
    }
    batch_count = int(end) - int(start) + 1
    system = (
        "你是电影导演与分镜师。只细化指定的少量镜头，不得修改项目基础合同。"
        "只输出 JSON 对象 {\"shots\":[...]}，不要 Markdown。"
        f"必须返回全局镜号 {start} 到 {end}，共 {batch_count} 镜。每镜保留 shot_number，"
        "并完整输出 scene_name、scene_state、scene_view_id、editable_bbox_xy、character_names、"
        "element_names、shot_size、duration、visual、spatial_layout、character_positions、action_line、"
        "camera_position、camera_movement、axis_rule、ground_plane、ground_lines、horizon_y、"
        "vanishing_point_xy、foreground、midground、background、frame_start、frame_end、video_segment、"
        "segment_break_after、segment_reason、camera、transition、dialogue、image_prompt、story_function、"
        "visual_thesis、action_start、primary_action、action_end、dominant_camera_move、"
        "continuity_invariants、keyframe_strategy、generation_risk。"
        "每镜只有一个主要动作和一个主运镜；站位、朝向、移动路径、视线和轴线必须可执行；"
        "庄重、克制、犹豫等情绪不得自动翻译成慢动作；除非剧本明确要求慢动作，人物按现实正常速度行动。"
        "步行约每0.55秒一步，3到4秒镜头应完成约3到6个可见步幅；相邻关键帧必须使用不同脚步相位，"
        "K1到K末人物中心通常至少移动画面对角线18%，不得用轻微推镜掩盖人物几乎不动。"
        "scene_view_id 只能为 master/reverse/left/right；固定场景设施不得移动。"
        "action_start 与 frame_start 必须是同一可见起始状态，action_end 与 frame_end 必须是同一可见结束状态；"
        "blocking、visual、character_positions 和 motion_keyframes 不得另写冲突的起点、终点或物体位置。"
        "服装或道具发生解下、拿起、放置等状态变化时必须保持对象守恒：全程只有同一件对象，"
        "它从起始位置移动到结束位置，绝不能在原位置保留同时又在新位置复制一件。"
        "场景资产中以单数命名的固定设施严格只有一个，不得因景别、视角或参考图变化而复制。"
        "批次边界不是自动切镜点；必须参考 full_shot_outline 保持相邻镜头的动作、轴线、"
        "screen direction、video_segment 和转场连续。不得输出本批次之外的镜头。"
        "video_segment 表示一次多镜头视频生成单元，不是单一机位长镜头：同一单元内允许"
        "硬切、空镜、反打、景别变化和推拉摇移。应把叙事上连续、角色与美术合同一致且"
        "总时长不超过15秒的相邻镜头赋予同一 video_segment；只有明确换场、段落结束、"
        "身份/时代/美术合同改变或总时长超限时才设置 segment_break_after=true。"
        f"项目风格：{style}。"
        + planning_instructions(batch_count, style)
    )
    user = json.dumps({
        "original_script": str(idea or "").strip(),
        "approved_foundation": compact_foundation,
        "requested_global_shot_range": [int(start), int(end)],
        "director_protocol_version": DIRECTOR_PROTOCOL_VERSION,
    }, ensure_ascii=False)
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def foundation_repair_messages(idea: str, invalid_response: Any, error: str,
                               shot_count: int, style: str) -> list[dict]:
    requested_count = int(shot_count)
    count = max(1, requested_count)
    count_rule = (
        "shot_outline 必须包含 1–24 项，并按 1..N 连续编号；镜头数根据原稿的"
        "叙事节拍与单一主动作原则自动决定；"
        if requested_count <= 0 else
        f"shot_outline 必须正好 {count} 项并按 1..{count} 编号；")
    system = (
        "你是 JSON 合同修复器。上一份项目基础合同没有通过本地校验。"
        "只修复结构与缺失字段，不改剧情事实，不增加攻击、伤害或新角色。"
        "只输出一个 JSON 对象，不要解释、Markdown 或代码围栏。"
        "顶层必须含 title、summary、visual_bible、characters、scenes、elements、shot_outline；"
        f"{count_rule}scenes 至少一项。"
        "characters/scenes/elements 必须是数组；名称引用必须一致。"
    )
    user = json.dumps({
        "validation_error": str(error or ""),
        "required_style": str(style or ""),
        "original_script": str(idea or "")[:16000],
        "invalid_response": str(invalid_response or "")[:24000],
    }, ensure_ascii=False)
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def shot_batch_repair_messages(idea: str, foundation: dict,
                               invalid_response: Any, error: str,
                               start: int, end: int, style: str) -> list[dict]:
    requested = _dict_rows(foundation.get("shot_outline"))[int(start) - 1:int(end)]
    system = (
        "你是 JSON 镜头合同修复器。上一份镜头批次没有通过本地校验。"
        "只修复 JSON 结构与缺失字段，不改项目基础合同和剧情。"
        "只输出 {\"shots\":[...]}，不要解释、Markdown 或代码围栏。"
        f"shots 必须正好包含全局镜号 {start} 到 {end}，不得输出范围外镜头。"
        "每镜至少保留 shot_number、scene_name、visual、duration、primary_action、"
        "camera_position、camera_movement、frame_start、frame_end 和 continuity_invariants。"
    )
    user = json.dumps({
        "validation_error": str(error or ""),
        "required_style": str(style or ""),
        "original_script": str(idea or "")[:12000],
        "approved_foundation": {
            "characters": foundation.get("characters") or [],
            "scenes": foundation.get("scenes") or [],
            "elements": foundation.get("elements") or [],
            "requested_outline": requested,
        },
        "invalid_response": str(invalid_response or "")[:24000],
    }, ensure_ascii=False)
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]
