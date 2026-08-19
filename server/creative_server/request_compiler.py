from __future__ import annotations

from typing import Any


IMAGE_SIZES = {"16:9": "2048x1152", "9:16": "1152x2048", "1:1": "2048x2048", "4:5": "1638x2048"}
IMAGE_ACTION_DEFAULTS = {
    "图片高清": "在不改变人物身份、内容和构图的前提下高清修复，提升真实细节、纹理和清晰度。",
    "智能扩图": "扩展画面边界，保持原图主体、身份、动作、透视和光线不变，自然补全环境。",
    "移除背景": "只保留画面主体，完整保留发丝、半透明边缘、服装和物体细节，移除背景。",
    "替换背景": "严格保留主体身份、姿态、服装和边缘，只按要求替换背景。",
}
SCRIPT_ACTION_INSTRUCTIONS = {
    "生成完整脚本": "根据要求生成可直接制作的完整短视频脚本，包含场景、人物、动作、对白和镜头节奏。",
    "续写脚本": "延续现有角色、语气、事实和情节继续写作，不重复已有内容。",
    "改写优化": "优化开场钩子、冲突、节奏、对白和结尾，保留核心创意与事实。",
    "剧本体检": "保留原文，只输出故事结构、人物动机、逻辑、节奏和修改优先级的专业诊断，不重写全文。",
    "强化人物弧光": "保留题材和主要事件，强化主角目标、阻力、选择、代价与结尾变化，输出完整修订稿。",
    "对白润色": "保留事件和场景，只润色对白，使角色声音可区分、潜台词明确、口语自然，输出完整修订稿。",
    "制片可行性检查": "保留原稿，输出场景、角色、道具、特效、声音、连续性和生成难点清单，并给出降本替代方案。",
}


def _director_prompt(prompt: str, timeline: list[dict]) -> str:
    lines = [prompt.strip(), "按以下导演时间轴一次生成完整连续视频；切镜点必须准确，主体身份、场景结构、道具和光线保持连续："]
    for index, item in enumerate(timeline[:50]):
        purpose = item.get("purpose") or "连续性参考"
        action = item.get("action") or item.get("instruction") or "推进一个清晰动作并停在明确结束状态"
        camera = item.get("camera") or "保持镜头稳定并服从动作"
        lines.append(f"{float(item.get('start', index * 3)):g}–{float(item.get('end', index * 3 + 3)):g}秒｜参考图{index + 1}（{purpose}）｜动作：{action}｜运镜：{camera}")
    return "\n".join(value for value in lines if value)


def _chat_messages(prompt: str, action: str, params: dict[str, Any]) -> list[dict[str, str]]:
    if params.get("copywriting_workbench"):
        if action == "翻译":
            language = str(params.get("copy_language") or "英语")
            system = f"你是专业广告译者。把口播准确翻译为{language}，保留品牌名、数字、优惠信息、逐句节奏和行动号召；只输出译文。"
            brief = "不要添加标题、解释或原文中不存在的事实。"
        else:
            system = "你是信息流广告口播文案导演。只输出可直接朗读的纯文案：开头三秒有具体钩子，中段自然呈现卖点，结尾有明确行动号召；不写镜头、标题或解释，不捏造产品事实。"
            brief = f"产品：{params.get('product_name','')}\n卖点与限制：{params.get('product_description','')}\n风格：{params.get('copy_style','激情抓眼球')}\n目标时长：{params.get('copy_duration','30')}秒"
    elif params.get("production_stage"):
        system = "你是影视制片流程引擎。严格执行指定阶段，只输出可供下一阶段消费的结构化结果，不跳阶段。"
        brief = f"制片阶段：{params['production_stage']}。"
    else:
        system = "你是专业影视编剧与制片顾问。" + SCRIPT_ACTION_INSTRUCTIONS.get(action, "按用户选择的动作处理内容，保留事实和已锁定设定。")
        brief = ""
    return [{"role": "system", "content": system}, {"role": "user", "content": f"操作：{action or '生成'}\n{brief}\n原始内容：\n{prompt}"}]


def compile_request(operation: str, hydrated_inputs: dict[str, Any], raw_params: dict[str, Any] | None, action: str = "", model: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    inputs, source = dict(hydrated_inputs), dict(raw_params or {})
    prompt = str(inputs.get("prompt") or source.get("content") or "").strip()
    if operation in {"chat", "json"}:
        inputs = {"messages": _chat_messages(prompt, action, source)}
        params = {"model": model or str(source.get("model") or ""), "temperature": float(source.get("planning_temperature") or 0.5), "timeout_seconds": 300}
        return inputs, {key: value for key, value in params.items() if value not in (None, "")}
    if operation in {"text_to_image", "image_edit"}:
        inputs["prompt"] = prompt or IMAGE_ACTION_DEFAULTS.get(action, "")
        ratio = str(source.get("ratio") or source.get("production_ratio") or "1:1")
        return inputs, {"size": IMAGE_SIZES.get(ratio, "2048x2048"), "n": max(1, min(4, int(source.get("candidate_count") or 1))), "quality": "high", "watermark": False, **({"model": model} if model else {})}
    if operation in {"text_to_video", "image_to_video"}:
        if source.get("multi_image_director") or source.get("timeline_images"):
            prompt = _director_prompt(prompt, [item for item in source.get("timeline_images", []) if isinstance(item, dict)])
        if source.get("generate_audio", True) and str(source.get("audio_prompt") or "").strip():
            prompt = f"{prompt}\n声音计划：{str(source['audio_prompt']).strip()}".strip()
        inputs["prompt"] = prompt
        ratio = str(source.get("ratio") or "16:9")
        return inputs, {"duration": float(source.get("duration") or 5), "aspect_ratio": ratio, "ratio": ratio, "resolution": str(source.get("resolution") or "720p"), "generate_audio": bool(source.get("generate_audio", True)), **({"model": model} if model else {})}
    if operation == "text_to_speech":
        inputs["text"] = str(inputs.get("text") or prompt)
        return inputs, {"voice": str(source.get("voice") or source.get("voice_name") or ""), "speed": float(source.get("speed") or 1), "emotion": str(source.get("emotion") or "")}
    return inputs, source
