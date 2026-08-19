"""AI 制片画布的导演协议。

把导演规则固化为产品内可保存、可编译、可测试的数据，而不是依赖模型临场发挥。
规则来自实际生成管线：故事功能优先、单一主运镜、动作必须有起止状态、
连续性不变量逐镜复用，以及首帧/首尾帧两种视频提示词形态。
"""
from __future__ import annotations

from typing import Iterable


DIRECTOR_PROTOCOL_VERSION = 2


def planning_instructions(shot_count: int, style: str) -> str:
    """返回附加到故事板 system prompt 的结构化导演合同。"""
    return (
        f"\n导演协议 v{DIRECTOR_PROTOCOL_VERSION}（必须执行）："
        "先决定每镜的故事功能，再决定走位，最后决定机位；没有新增信息或情绪变化的镜头应删除。"
        "每镜只允许一个 dominant_camera_move（固定机位也算有效选择），不得堆叠多个运镜。"
        "每镜必须把动作写成 action_start → primary_action → action_end，结束状态必须可见、可拍。"
        "action_start 必须与 frame_start 表达同一个可见状态，action_end 必须与 frame_end 表达同一个可见状态；"
        "不得在 blocking、visual 或运动关键帧中另写与起止状态冲突的位置。"
        "行为必须是身体可观察动作，不使用‘害怕、悲伤、电影感’代替表演。"
        "同场镜头逐字复用 continuity_invariants，包括身份、服装、场景、主光方向、屏幕方向和时代。"
        "连续性不变量只锁定未参与动作的属性；若主动作明确包含解下、脱下、拿起、放下或移动道具，"
        "必须把该对象写成单一实例的起始位置→结束位置，原位置在动作后必须为空，严禁复制出第二件。"
        "固定设施的数量、外形、方位与尺度以场景资产为权威，同一命名设施不得复制。"
        "每镜补充 generation_risk 与 keyframe_strategy；默认使用 first_frame。"
        "只有同一机位、同一构图内存在必须精确命中的可见动作终点或受控形变时，才可建议 first_last；"
        "换景、反打、明显改构图、普通走动或仅为身份稳定，均不得建议 first_last。"
        f"本次正好 {int(shot_count)} 镜，项目风格为 {style}。"
        "shots 每项必须额外包含："
        '"story_function":"本镜让观众新知道/感受到什么",'
        '"visual_thesis":"可被摄影验证的画面变化",'
        '"action_start":"可见起始姿态与位置",'
        '"primary_action":"一个主动作及速度方向",'
        '"action_end":"可见结束姿态与位置",'
        '"dominant_camera_move":"固定/推/拉/摇/移/跟之一及速度",'
        '"continuity_invariants":["逐字复用的不变量"],'
        '"keyframe_strategy":"first_frame或first_last",'
        '"generation_risk":"本镜最可能的生成失败"。')


def normalize_director_contract(shot: dict) -> dict:
    """补齐并保存单镜导演合同，兼容旧工程与不完整模型输出。"""
    positions = [value for value in shot.get("character_positions", []) or []
                 if isinstance(value, dict)]
    first = positions[0] if positions else {}
    start = str(shot.get("action_start") or first.get("start") or
                shot.get("frame_start") or shot.get("visual") or "镜头既定起幅").strip()
    action = str(shot.get("primary_action") or first.get("movement") or
                 shot.get("action_line") or "主体保持既定动作").strip()
    end = str(shot.get("action_end") or first.get("end") or
              shot.get("frame_end") or start).strip()
    camera = str(shot.get("dominant_camera_move") or
                 shot.get("camera_movement") or "固定机位").strip()
    invariants = shot.get("continuity_invariants") or []
    if isinstance(invariants, str):
        invariants = [invariants]
    invariants = list(dict.fromkeys(
        str(value).strip() for value in invariants if str(value).strip()))
    if not invariants:
        invariants = [
            "人物身份与服装不变", "场景结构与道具尺度不变",
            "主光方向与屏幕方向不变",
        ]
    strategy = str(shot.get("keyframe_strategy") or "").strip().lower()
    if strategy not in {"first_frame", "first_last"}:
        strategy = "first_frame"
    # A model recommendation is not permission to bind two independently
    # generated images to a video. Endpoint interpolation is opt-in because a
    # mismatched Klast makes the renderer morph the whole set.
    if strategy == "first_last" and not bool(shot.get("endpoint_pair_enabled")):
        shot["endpoint_pair_recommended"] = True
        strategy = "first_frame"
    contract = {
        "version": DIRECTOR_PROTOCOL_VERSION,
        "story_function": str(shot.get("story_function") or
                              shot.get("dramatic_purpose") or "推进当前剧情信息").strip(),
        "visual_thesis": str(shot.get("visual_thesis") or
                             shot.get("visual") or "画面状态发生可见变化").strip(),
        "action_start": start,
        "primary_action": action,
        "action_end": end,
        "dominant_camera_move": camera,
        "continuity_invariants": invariants,
        "keyframe_strategy": strategy,
        "generation_risk": str(shot.get("generation_risk") or
                               "身份漂移、动作终点不明确或画面闪烁").strip(),
    }
    shot.update(contract)
    shot["director_contract"] = dict(contract)
    return contract


def endpoint_pair_requested(shot: dict) -> bool:
    """True only for an explicitly approved, not merely AI-suggested pair."""
    return bool(
        shot.get("endpoint_pair_enabled") and
        str(shot.get("keyframe_strategy") or "").strip().lower() == "first_last"
    )


def compile_video_direction(shot: dict, timeline: str = "") -> str:
    """按 S1（首帧驱动）或 S3（首尾帧桥接）编译纯运动导演指令。"""
    contract = normalize_director_contract(shot)
    invariants = "、".join(contract["continuity_invariants"])
    camera = contract["dominant_camera_move"]
    if contract["keyframe_strategy"] == "first_last":
        lead = (
            "从已批准的首帧开始，并准确结束在已批准的尾帧。"
            f"两帧之间只执行一个连续变化：{contract['primary_action']}。")
    else:
        lead = (
            f"从已批准首帧中的状态开始：{contract['action_start']}；"
            f"随后只执行一个主动作：{contract['primary_action']}。")
    timeline_text = f"动作时间线：{timeline}。" if timeline else ""
    return (
        f"故事功能：{contract['story_function']}。{lead}"
        f"摄影机只执行：{camera}，禁止叠加第二种运镜。"
        f"动作结束并稳定在：{contract['action_end']}。{timeline_text}"
        f"全过程保持不变：{invariants}。"
        f"重点避免：{contract['generation_risk']}。")


def director_gate_issues(shot: dict) -> list[str]:
    """返回阻碍生成的导演合同问题；空列表表示可以进入关键帧生产。"""
    contract = normalize_director_contract(shot)
    issues = []
    for key, label in (("story_function", "故事功能"),
                       ("action_start", "动作起点"),
                       ("primary_action", "主动作"),
                       ("action_end", "动作终点"),
                       ("dominant_camera_move", "唯一主运镜")):
        if not str(contract.get(key) or "").strip():
            issues.append(f"缺少{label}")
    if not contract["continuity_invariants"]:
        issues.append("缺少连续性不变量")
    return issues
