"""Physical-location contracts for production scene assets."""
from __future__ import annotations

import re


_STATE_WORDS = re.compile(
    r"雨夜|夜晚|白天|清晨|黄昏|冷白(?:灯)?|暖白(?:灯)?|橙色(?:应急灯)?|"
    r"应急灯|停电后?|雨停前的?|雨停|灯光状态|门边|门口|街角|局部|特写|"
    r"内景|外景|室内|室外|场景资产|场景|空间母版|固定版")


def scene_location_key(scene: dict) -> str:
    """Return a stable-enough physical-space key for legacy scene specs."""
    name = str(scene.get("name") or scene.get("asset_name") or "").strip()
    description = str(scene.get("description") or
                      scene.get("identity_description") or "").strip()
    explicit = str(scene.get("location_id") or scene.get("physical_location_id") or "").strip()
    if explicit:
        return explicit.lower()
    desc_lead = description.lstrip()
    exterior = bool(re.search(r"外景|店外|门外|室外", name) or
                    re.match(r"外景|室外", desc_lead))
    interior = bool(re.search(r"内景|店内|室内", name) or
                    re.match(r"内景|室内", desc_lead))
    # The exterior description may mention light visible "inside the shop";
    # the explicit scene heading/name owns the realm.
    realm = "exterior" if exterior else "interior" if interior else "space"
    base = name.replace("店外", "店").replace("店内", "店")
    base = _STATE_WORDS.sub("", base)
    base = re.sub(r"[·•\-—_（）()\s]+", "", base).strip("，,。；;")
    if not base:
        # Last-resort key remains deterministic without merging every unnamed set.
        base = re.sub(r"\s+", "", name) or "未命名地点"
    return f"{base.lower()}:{realm}"


def _master_score(scene: dict) -> int:
    text = (f"{scene.get('name', '')} {scene.get('asset_name', '')} "
            f"{scene.get('description', '')} {scene.get('identity_description', '')}")
    score = 0
    if any(word in text for word in ("空间母版", "主场景", "正常", "冷白灯", "全景")):
        score += 4
    if any(word in text for word in ("停电", "应急灯", "雨停", "门边", "局部", "特写")):
        score -= 5
    return score


def consolidate_scene_specs(raw_scenes) -> tuple[list[dict], dict[str, dict]]:
    """Collapse lighting/weather/crop variants into one master per location."""
    groups: dict[str, list[dict]] = {}
    for raw in raw_scenes or []:
        if not isinstance(raw, dict):
            continue
        scene = dict(raw)
        scene["location_id"] = scene_location_key(scene)
        groups.setdefault(scene["location_id"], []).append(scene)
    masters = []
    aliases = {}
    for location_id, values in groups.items():
        master = dict(max(enumerate(values), key=lambda pair: (_master_score(pair[1]), -pair[0]))[1])
        master_name = str(master.get("name") or "场景")
        states = []
        for value in values:
            explicit_states = [item for item in
                               (value.get("states") or value.get("scene_states") or [])
                               if isinstance(item, dict)]
            if explicit_states:
                for item in explicit_states:
                    state = dict(item)
                    state_name = str(state.get("name") or "默认状态")
                    state["name"] = state_name
                    states.append(state)
                default_state = str(explicit_states[0].get("name") or "默认状态")
                aliases[str(value.get("name") or master_name)] = {
                    "master_name":master_name, "state":default_state,
                    "location_id":location_id}
            else:
                state_name = str(value.get("name") or "默认状态")
                states.append({
                    "name":state_name,
                    "description":str(value.get("description") or ""),
                    "image_prompt":str(value.get("image_prompt") or ""),
                })
                aliases[state_name] = {"master_name":master_name, "state":state_name,
                                       "location_id":location_id}
        master["location_id"] = location_id
        master["scene_states"] = states
        masters.append(master)
    return masters, aliases
