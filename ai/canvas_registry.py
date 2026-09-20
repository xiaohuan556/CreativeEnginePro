"""Shared node and connection contracts for the desktop production canvas.

The WebAI canvas treats a link as a production transition instead of a loose
media-type match.  Keeping the same registry on desktop makes imported WebAI
projects and locally-created graphs behave the same way without coupling the
Qt UI to the React implementation.
"""
from __future__ import annotations

from copy import deepcopy


SEEDANCE_25_MODEL = "doubao-seedance-2-5-260628"


def _spec(node_type, title, kind, group, defaults=None):
    return {
        "node_type": node_type,
        "title": title,
        "kind": kind,
        "group": group,
        "defaults": dict(defaults or {}),
    }


NODE_SPECS = {
    "storyboard": _spec("storyboard_node", "短片", "storyboard", "more", {
        "style": "电影写实", "automation_mode": "checkpoints",
        "production_scope": "all", "production_ratio": "16:9",
        "candidate_count": 2, "image_candidate_count": 2,
        "video_candidate_count": 2, "final_render_mode":"live_action",
        "pipeline_stage": "",
    }),
    "text": _spec("text_node", "文本", "text", "primary", {
        "plain_text": True,
    }),
    "script": _spec("text_node", "脚本", "script", "primary", {
        "editor_action": "生成完整脚本", "script_versions": [],
        "script_version": 1, "script_locked": False,
    }),
    "copywriting": _spec("text_node", "口播", "copywriting", "more", {
        "copywriting_workbench": True, "product_name": "",
        "product_description": "", "copy_style": "激情抓眼球",
        "copy_duration": "30", "copy_language": "英语",
        "editor_action": "生成口播文案",
    }),
    "multi_image": _spec("image_node", "图片", "image", "primary", {
        "multi_image_composer": True, "references": [],
        "reference_assets": [], "reference_settings": [],
        # An empty image node is text-to-image.  Connecting its first image
        # promotes the action to AI edit at the connection boundary.
        "editor_action": "文生图", "ratio": "16:9",
        "candidate_count": 1, "batch_mode": False, "batch_strategy":"paired",
        "style_preset":"", "style_recipe":None, "style_custom":"",
        "style_scope":"whole", "style_strength":70,
        "style_preserve":["identity", "pose", "composition", "background", "lighting"],
    }),
    "image_asset": _spec("image_node", "图片", "image", "system"),
    "multi_director": _spec("video_node", "时间轴", "director", "more", {
        "multi_image_director": True, "timeline_images": [],
        "director_mode": "direct_video", "asset_bindings": [],
        "references": [], "reference_assets": [], "duration": 10,
        "ratio": "16:9", "resolution": "720p", "generate_audio": True,
        "audio_prompt": "对白、环境声和动作声与画面同步，不使用背景音乐掩盖对白",
        "generator_kind": "video", "editor_action": "图生视频",
        "provider_name": "seedance", "model": SEEDANCE_25_MODEL,
        "style": "电影写实", "automation_mode": "checkpoints",
        "production_scope": "all", "production_ratio": "16:9",
        "candidate_count": 2, "image_candidate_count": 2,
        "video_candidate_count": 2, "final_render_mode": "live_action",
        "pipeline_stage": "",
    }),
    "long_timeline": _spec(
        "director_timeline", "Seedance 2.5 长叙事时间轴", "director", "system", {
            "production_generated":True, "timeline_mode":"native_long_form",
            "shots":[],
        }),
    "video": _spec("video_node", "视频", "video", "primary", {
        "editor_action": "文生视频", "ratio": "16:9", "duration": 10,
        "resolution": "720p", "generate_audio": True,
        "audio_prompt": "对白、环境声和动作声与画面同步，不使用背景音乐掩盖对白",
        "references": [], "reference_assets": [],
    }),
    # Qt reuses the mature video/audio editors and records the specialized
    # mode in payload, rather than introducing presentation-only node classes.
    "video_style_transfer": _spec("video_node", "风格迁移", "video", "more", {
        "video_style_transfer": True, "editor_action": "视频风格迁移",
        "provider_name": "seedance", "model": SEEDANCE_25_MODEL,
        "ratio": "16:9", "duration": 10, "resolution": "720p",
        "generate_audio": True, "references": [], "reference_assets": [],
        "audio_prompt": "保留内容视频的对白、环境声、动作声与节奏，不从风格参考视频复制声音",
        "style_strength":70,
        "style_preserve":["人物身份", "动作时序", "镜头运动", "画面构图", "原始声音"],
        "reference_settings": [],
    }),
    "audio": _spec("audio_node", "音频", "audio", "primary", {
        "editor_action": "对白配音", "provider_name": "edge_tts",
        "voice": "zh-CN-XiaoxiaoNeural", "voice_name": "晓晓",
        "speed": 1, "emotion": "",
    }),
    "voice_clone": _spec("audio_node", "克隆", "audio", "more", {
        "voice_clone": True, "editor_action": "克隆声音生成语音",
        "provider_name": "cosyvoice", "voice": "", "voice_name": "",
        "speed": 1, "emotion": "", "voice_consent": False,
        "reference_transcript": "", "references": [],
        "reference_assets": [],
    }),
    "analysis": _spec("video_analysis_node", "拉片", "analysis", "more", {
        "analysis_result": {},
    }),
    "shot": _spec("shot", "镜头", "shot", "more", {
        "editor_action": "生成关键帧", "assets": [],
    }),
    "skill": _spec("skill_node", "导演工具", "skill", "more", {
        "strength": 0.65, "references": [],
    }),
    "scene_reference": _spec("image_node", "场景参考", "reference", "reference", {
        "asset_kind": "scene", "reference_role": "scene", "ratio": "16:9",
        "editor_action": "文生图",
    }),
    "character_reference": _spec("image_node", "主体参考", "reference", "reference", {
        "asset_kind": "character", "reference_role": "character", "ratio": "2:3",
        "editor_action": "文生图",
    }),
    "element_reference": _spec("image_node", "元素参考", "reference", "reference", {
        "asset_kind": "element", "reference_role": "element", "ratio": "1:1",
        "editor_action": "文生图",
    }),
}


CREATION_ITEMS = (
    ("text", "文本", "写提示词、想法或制作说明", "文字 → 继续创建", "plain_text"),
    ("script", "脚本", "编写可继续拆分镜头的分镜脚本", "故事 → 分镜脚本", "write_script"),
    ("multi_image", "图片", "用文字或参考图生成图片", "文字 / 图片 → 图片", "text_to_image"),
    ("video", "视频", "用文字或图片生成视频", "文字 / 图片 → 视频", "generate_video"),
    ("audio", "音频", "生成配音或音效", "文字 → 音频", "text_to_speech"),
    ("storyboard", "短片", "从故事或脚本自动完成多镜头短片", "故事 / 脚本 → 短片", "idea_to_movie"),
    ("copywriting", "口播", "生成、改写或翻译口播文案", "产品信息 → 口播", "write_copy"),
    ("multi_director", "多图导演", "直接多图成视频，或先拆镜和运动分镜再成片", "多张图片 → 视频 / 短片", "multi_image_director"),
    ("analysis", "拉片", "分析切镜、运镜、节奏、动作和声音", "视频 → 拉片报告", "break_down_video"),
    ("shot", "镜头", "控制一个镜头的画面、动作、运镜和对白", "镜头设计 → 多种结果", "make_shot"),
)


NODE_CONNECTION_TARGETS = {
    "text": ("script", "multi_image", "video", "audio", "shot", "skill"),
    "script": ("storyboard", "multi_image", "video", "audio", "shot", "skill"),
    "copywriting": ("multi_image", "video", "audio"),
    "multi_image": ("multi_image", "video", "multi_director", "shot", "storyboard"),
    "image_asset": ("multi_image", "video", "multi_director", "shot", "storyboard"),
    "scene_reference": ("multi_image", "video", "multi_director", "shot", "storyboard"),
    "character_reference": ("multi_image", "video", "multi_director", "shot", "storyboard"),
    "element_reference": ("multi_image", "video", "multi_director", "shot", "storyboard"),
    "video": ("analysis", "video_style_transfer"),
    "multi_director": ("analysis", "video_style_transfer"),
    "video_style_transfer": ("analysis", "video_style_transfer"),
    "long_timeline": ("analysis", "video_style_transfer"),
    "audio": (),
    "analysis": ("script", "shot", "skill"),
    "shot": ("multi_image", "video", "audio", "shot"),
    "skill": ("multi_image", "video", "shot"),
}


def infer_node_spec(node_type: str, payload: dict | None = None) -> str:
    payload = payload or {}
    if node_type == "storyboard_node":
        return "storyboard"
    if node_type == "text_node":
        if payload.get("plain_text"):
            return "text"
        if payload.get("copywriting_workbench"):
            return "copywriting"
        return "script"
    if node_type == "image_node":
        if payload.get("multi_image_composer"):
            return "multi_image"
        asset_kind = str(payload.get("asset_kind") or "")
        if asset_kind in {"scene", "character", "element"}:
            return f"{asset_kind}_reference"
        return "image_asset"
    if node_type == "video_node":
        if payload.get("video_style_transfer"):
            return "video_style_transfer"
        if payload.get("multi_image_director"):
            return "multi_director"
        return "video"
    if node_type == "director_timeline":
        return "long_timeline"
    if node_type == "audio_node":
        return "voice_clone" if payload.get("voice_clone") else "audio"
    if node_type == "video_analysis_node":
        return "analysis"
    if node_type == "shot":
        return "shot"
    if node_type == "skill_node":
        return "skill"
    # Imported desktop asset/result families behave as visual sources.
    if node_type in {"scene", "character", "element"}:
        return f"{node_type}_reference"
    if node_type in {"asset_view", "asset_take", "shot_take"}:
        path = str(payload.get("path") or "").lower()
        return "video" if path.endswith((".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v")) else "image_asset"
    return ""


def can_connect(source_key: str, target_key: str) -> bool:
    return bool(target_key and target_key in NODE_CONNECTION_TARGETS.get(source_key, ()))


def creation_payload(spec_key: str, beginner_mode: str = "", **overrides):
    spec = NODE_SPECS[spec_key]
    payload = deepcopy(spec["defaults"])
    payload.update(overrides)
    payload.setdefault("title", spec["title"])
    if beginner_mode:
        payload["beginner_mode"] = beginner_mode
    return spec["node_type"], payload


def connection_create_choices(source_key: str, source_kind: str = ""):
    """Return WebAI-equivalent next-step choices for a dangling output link."""
    def choice(target, label, hint, relation, **overrides):
        return {
            "target": target, "label": label, "hint": hint,
            "relation": relation, "overrides": overrides,
        }

    rows = []
    if source_key == "text":
        rows = [
            choice("script", "写成分镜脚本", "继续扩写人物、场景和分镜", "text_source", beginner_mode="write_script"),
            choice("multi_image", "用文字生成图片", "当前文字自动带入图片提示词", "text_source", beginner_mode="text_to_image", editor_action="文生图"),
            choice("video", "用文字生成视频", "当前文字作为画面、动作和运镜描述", "text_source", beginner_mode="text_to_video", editor_action="文生视频"),
            choice("audio", "把文字生成配音", "当前文字作为要朗读的台词", "text_source", beginner_mode="text_to_speech", editor_action="对白配音"),
            choice("shot", "设计一个镜头", "继续设置景别、动作、运镜和对白", "text_source", beginner_mode="make_shot"),
            choice("skill", "使用导演工具", "制作机位、调度或连续性方案", "text_source"),
        ]
    elif source_key == "script":
        rows = [
            choice("storyboard", "开始自动制片", "读取完整脚本并生成多镜头短片", "script_source", beginner_mode="idea_to_movie"),
            choice("multi_image", "生成分镜画面", "使用脚本内容生成分镜图片", "text_source", beginner_mode="text_to_image", editor_action="文生图"),
            choice("video", "生成单段视频", "生成一个独立视频片段", "text_source", beginner_mode="text_to_video", editor_action="文生视频"),
            choice("audio", "生成脚本对白", "读取台词并生成配音", "text_source", beginner_mode="text_to_speech", editor_action="对白配音"),
            choice("shot", "拆成单个镜头", "带入镜头节点继续精细控制", "text_source", beginner_mode="make_shot"),
            choice("skill", "继续导演设计", "制作调度、机位和连续性方案", "text_source"),
        ]
    elif source_key == "copywriting":
        rows = [
            choice("multi_image", "生成口播配图", "使用口播文案生成配套画面", "text_source", beginner_mode="text_to_image", editor_action="文生图"),
            choice("video", "生成口播视频", "把文案作为内容和节奏依据", "text_source", beginner_mode="text_to_video", editor_action="文生视频"),
            choice("audio", "生成口播配音", "把文案直接生成朗读音频", "text_source", beginner_mode="text_to_speech", editor_action="对白配音"),
        ]
    elif source_key == "skill":
        rows = [
            choice("multi_image", "把方案生成图片", "将导演方案生成画面", "text_source", beginner_mode="text_to_image", editor_action="文生图"),
            choice("video", "把方案生成视频", "将导演方案落实成动态镜头", "text_source", beginner_mode="text_to_video", editor_action="文生视频"),
            choice("shot", "把方案落实为镜头", "继续设置动作和运镜", "text_source", beginner_mode="make_shot"),
        ]
    elif source_kind in {"image", "reference"} or source_key in {
            "multi_image", "image_asset", "scene_reference",
            "character_reference", "element_reference"}:
        rows = [
            choice("multi_image", "基于此图继续编辑", "保留主体并修改背景、元素或风格", "reference", beginner_mode="image_edit"),
            choice("video", "设为视频首帧", "新视频从当前图片开始运动", "first_frame", beginner_mode="first_frame_video", editor_action="图生视频"),
            choice("multi_director", "加入多图导演", "直接排时间轴，或把图片作为人物、场景和元素资产制片", "timeline_image", beginner_mode="multi_image_director"),
            choice("shot", "作为镜头视觉参考", "参考人物、场景和构图", "shot_reference", beginner_mode="make_shot"),
            choice("storyboard", "作为短片分镜草图", "交给自动制片流程参考", "storyboard_reference", beginner_mode="idea_to_movie"),
        ]
    elif source_key in {"video", "multi_director", "video_style_transfer"}:
        rows = [
            choice("analysis", "分析这个视频", "分析切镜、运镜、节奏和声音", "video_source", beginner_mode="break_down_video"),
            choice("video_style_transfer", "作为内容视频迁移风格", "保留动作镜头，只改变画风与质感", "content_video"),
        ]
    elif source_key == "analysis":
        rows = [
            choice("script", "根据报告重写脚本", "把拉片报告带入脚本工作台", "analysis_source", beginner_mode="write_script"),
            choice("shot", "根据分析设计镜头", "参考原视频节奏、动作和运镜", "analysis_source", beginner_mode="make_shot"),
            choice("skill", "继续导演分析", "进一步处理机位、调度或连续性", "analysis_source"),
        ]
    elif source_key == "shot":
        rows = [
            choice("multi_image", "生成本镜关键帧", "按景别、构图和动作生成图片", "shot_source", beginner_mode="text_to_image", editor_action="文生图"),
            choice("video", "生成本镜视频", "只生成当前镜头", "shot_source", beginner_mode="text_to_video", editor_action="文生视频"),
            choice("audio", "生成本镜对白", "只生成当前镜头对白或声音", "shot_source", beginner_mode="text_to_speech", editor_action="对白配音"),
            choice("shot", "创建连续下一镜", "继承人物、场景、方向和动作连续性", "shot_source", beginner_mode="make_shot"),
        ]
    return [row for row in rows if can_connect(source_key, row["target"])]
