export type StudioNodeKind =
  | "project" | "storyboard" | "script" | "copywriting" | "image"
  | "director" | "video" | "audio" | "analysis" | "shot"
  | "reference" | "skill" | "workflow" | "task" | "result";

export type NodeGroup = "primary" | "basic" | "analysis" | "reference" | "system";

export type StudioNodeSpec = {
  key: string;
  desktopType: string;
  variant?: string;
  title: string;
  description: string;
  kind: StudioNodeKind;
  group: NodeGroup;
  accent: string;
  creatable: boolean;
  input: Array<"text" | "image" | "video" | "audio" | "asset" | "shot" | "result" | "any">;
  output: Array<"text" | "image" | "video" | "audio" | "asset" | "shot" | "result" | "any">;
  actions: string[];
  defaults: Record<string, unknown>;
};

const spec = (value: StudioNodeSpec) => value;

/**
 * The browser canvas registry mirrors NODE_STYLE, show_new_asset_menu and the
 * inline action tables in ai/ui/production_canvas.py. Runtime-only desktop
 * nodes remain in the registry so imported projects never lose their meaning.
 */
export const NODE_SPECS = [
  spec({ key: "storyboard", desktopType: "storyboard_node", title: "AI 故事板", description: "一句故事自动拆镜并完成制片", kind: "storyboard", group: "primary", accent: "#89b8ff", creatable: true, input: ["text"], output: ["shot", "image", "video", "audio"], actions: ["自动开始 / 继续", "1 · 拆解镜头（完成后停下确认）", "2 · 生成资产候选（逐项采用并锁定）", "3 · 生成调度与多帧运动分镜（完成后确认）", "4 · 确认调度并合成定稿提示词", "5 · 创建定稿图片生成器组", "6 · 确认定稿图片并生成视频", "7 · 创建对白音频组"], defaults: { style: "电影写实", shot_count: 0, automation_mode: "checkpoints", production_scope: "all", production_ratio: "16:9", pipeline_stage: 0 } }),
  spec({ key: "script", desktopType: "text_node", title: "剧本工作台", description: "写作、诊断、定稿和版本管理", kind: "script", group: "primary", accent: "#a7b0bd", creatable: true, input: ["text", "any"], output: ["text"], actions: ["生成完整脚本", "续写脚本", "改写优化", "剧本体检", "强化人物弧光", "对白润色", "制片可行性检查", "保存版本", "恢复上一版", "切换剧本定稿", "创建制片项目"], defaults: { editor_action: "生成完整脚本", script_versions: [], script_version: 1, script_locked: false } }),
  spec({ key: "copywriting", desktopType: "text_node", variant: "copywriting_workbench", title: "信息流口播文案", description: "口播生成、改写、压缩和翻译", kind: "copywriting", group: "primary", accent: "#f07daf", creatable: true, input: ["text", "image", "video"], output: ["text"], actions: ["生成口播文案", "改写优化", "压缩精简", "增强开场钩子", "翻译", "复制文案", "恢复原文"], defaults: { copywriting_workbench: true, product_name: "", product_description: "", copy_style: "激情抓眼球", copy_duration: "30", copy_language: "英语", editor_action: "生成口播文案" } }),
  spec({ key: "multi_image", desktopType: "image_node", variant: "multi_image_composer", title: "多图生成图片", description: "为每张参考图指定主体、场景、构图、元素或风格用途", kind: "image", group: "primary", accent: "#50b9dd", creatable: true, input: ["image", "asset"], output: ["image"], actions: ["设置每张图片的用途", "AI 编辑", "保存到资产库"], defaults: { multi_image_composer: true, references: [], reference_assets: [], reference_settings: [], editor_action: "AI 编辑", ratio: "16:9", candidate_count: 1 } }),
  spec({ key: "image_asset", desktopType: "image_node", variant: "imported", title: "图片节点", description: "画布中的上传或生成图片", kind: "image", group: "system", accent: "#86a9c2", creatable: false, input: ["image"], output: ["image", "asset"], actions: ["基于这张图继续编辑", "让这张图动起来（首帧）", "作为视频尾帧", "保存到资产库"], defaults: {} }),
  spec({ key: "multi_director", desktopType: "video_node", variant: "multi_image_director", title: "多图导演视频", description: "按时间段、动作、运镜和用途一次生成完整视频", kind: "director", group: "primary", accent: "#6f8cff", creatable: true, input: ["image", "asset"], output: ["video"], actions: ["编辑图片时间、动作、运镜与用途", "图生视频", "基于尾帧续拍", "提取首中尾帧"], defaults: { multi_image_director: true, timeline_images: [], references: [], reference_assets: [], duration: 10, ratio: "16:9", resolution: "720p", generate_audio: true, audio_prompt: "对白、环境声和动作声与画面同步，不使用背景音乐掩盖对白", generator_kind: "video", editor_action: "图生视频" } }),
  spec({ key: "video", desktopType: "video_node", title: "视频节点", description: "文生视频、图生视频、抽帧和续拍", kind: "video", group: "basic", accent: "#8f87c9", creatable: true, input: ["text", "image", "asset"], output: ["video", "image"], actions: ["图生视频", "文生视频", "提取首中尾帧", "基于尾帧续拍", "保存到资产库"], defaults: { editor_action: "文生视频", ratio: "16:9", duration: 10, resolution: "720p", generate_audio: true, audio_prompt: "对白、环境声和动作声与画面同步，不使用背景音乐掩盖对白", references: [], reference_assets: [] } }),
  spec({ key: "audio", desktopType: "audio_node", title: "音频节点", description: "生成对白配音或音效", kind: "audio", group: "basic", accent: "#b887c9", creatable: true, input: ["text", "video"], output: ["audio"], actions: ["对白配音", "音效", "保存到资产库"], defaults: { editor_action: "对白配音", voice: "", speed: 1, emotion: "" } }),
  spec({ key: "shot", desktopType: "shot", title: "镜头节点", description: "镜头提示词、参考、关键帧、视频与对白", kind: "shot", group: "basic", accent: "#6f8cff", creatable: true, input: ["image", "asset", "shot"], output: ["shot", "image", "video", "audio"], actions: ["保存镜头修改", "生成关键帧", "参考图再生成", "生成视频", "生成对白"], defaults: { shot: {}, assets: [] } }),
  spec({ key: "analysis", desktopType: "video_analysis_node", title: "AI 自动拉片", description: "识别切镜、镜长、节奏、运镜、主体运动轨迹和声音事件", kind: "analysis", group: "analysis", accent: "#67c7d8", creatable: true, input: ["video"], output: ["text", "shot"], actions: ["开始拉片", "重新拉片", "导出拉片报告"], defaults: { analysis_result: {} } }),
  spec({ key: "skill", desktopType: "skill_node", title: "专业 Skill", description: "故事板、调度、多机位、连续性、角色、光影和情绪工具", kind: "skill", group: "analysis", accent: "#f0b45f", creatable: true, input: ["text", "image", "shot", "any"], output: ["text", "image", "shot"], actions: ["故事板", "调度与运动分镜", "多机位九宫格", "25 宫格连贯分镜", "角色设定", "电影级光影调整", "情绪调整"], defaults: { strength: 0.65, references: [] } }),
  spec({ key: "scene_reference", desktopType: "image_node", variant: "scene", title: "场景参考", description: "描述场景或上传参考图", kind: "reference", group: "reference", accent: "#48d597", creatable: true, input: ["image"], output: ["asset", "image"], actions: ["图生图", "保存到资产库"], defaults: { asset_kind: "scene", reference_role: "scene", ratio: "16:9", editor_action: "文生图" } }),
  spec({ key: "character_reference", desktopType: "image_node", variant: "character", title: "主体参考", description: "描述角色主体或上传参考图", kind: "reference", group: "reference", accent: "#b98cff", creatable: true, input: ["image"], output: ["asset", "image"], actions: ["图生图", "保存到资产库"], defaults: { asset_kind: "character", reference_role: "character", ratio: "2:3", editor_action: "文生图" } }),
  spec({ key: "element_reference", desktopType: "image_node", variant: "element", title: "元素参考", description: "描述道具元素或上传参考图", kind: "reference", group: "reference", accent: "#4fc4e8", creatable: true, input: ["image"], output: ["asset", "image"], actions: ["图生图", "保存到资产库"], defaults: { asset_kind: "element", reference_role: "element", ratio: "1:1", editor_action: "文生图" } }),
  spec({ key: "project", desktopType: "director", title: "项目", description: "桌面制片项目根节点", kind: "project", group: "system", accent: "#ffb45c", creatable: false, input: ["text", "asset"], output: ["shot"], actions: [], defaults: {} }),
  spec({ key: "legacy_scene", desktopType: "scene", title: "场景资产", description: "旧工程中的场景资产节点", kind: "reference", group: "system", accent: "#48d597", creatable: false, input: ["image"], output: ["asset", "image"], actions: ["保存到资产库"], defaults: { asset_kind: "scene" } }),
  spec({ key: "legacy_character", desktopType: "character", title: "主体资产", description: "旧工程中的主体资产节点", kind: "reference", group: "system", accent: "#b98cff", creatable: false, input: ["image"], output: ["asset", "image"], actions: ["保存到资产库"], defaults: { asset_kind: "character" } }),
  spec({ key: "legacy_element", desktopType: "element", title: "元素资产", description: "旧工程中的元素资产节点", kind: "reference", group: "system", accent: "#4fc4e8", creatable: false, input: ["image"], output: ["asset", "image"], actions: ["保存到资产库"], defaults: { asset_kind: "element" } }),
  spec({ key: "asset_view", desktopType: "asset_view", title: "资产视角", description: "场景或角色的权威视角", kind: "result", group: "system", accent: "#5aa7c8", creatable: false, input: ["asset"], output: ["image", "asset"], actions: ["采用", "驳回"], defaults: {} }),
  spec({ key: "asset_take", desktopType: "asset_take", title: "资产候选", description: "AI 生成的资产候选", kind: "result", group: "system", accent: "#d39a55", creatable: false, input: ["any"], output: ["image", "asset"], actions: ["采用", "驳回", "接受风险并继续"], defaults: {} }),
  spec({ key: "workflow", desktopType: "workflow_group", title: "工作流", description: "一组可复用的生成节点", kind: "workflow", group: "system", accent: "#65d6b2", creatable: false, input: ["any"], output: ["any"], actions: ["执行工作流", "暂停工作流", "继续工作流", "取消工作流", "保存工作流模板"], defaults: {} }),
  spec({ key: "task", desktopType: "generation_task", title: "AI 任务", description: "队列中的生成任务", kind: "task", group: "system", accent: "#f0a44b", creatable: false, input: ["any"], output: ["result"], actions: ["暂停", "继续", "取消", "重试"], defaults: {} }),
  spec({ key: "result", desktopType: "shot_take", title: "生成结果", description: "图片、视频或音频候选结果", kind: "result", group: "system", accent: "#e06f9c", creatable: false, input: ["any"], output: ["image", "video", "audio"], actions: ["采用", "驳回", "接受风险并继续", "保存到资产库"], defaults: {} }),
] as const;

export const NODE_SPEC_BY_KEY = Object.fromEntries(NODE_SPECS.map((item) => [item.key, item])) as Record<string, StudioNodeSpec>;

export function inferNodeSpec(data: Record<string, unknown>): StudioNodeSpec {
  const desktopType = String(data.desktopType || data.type || "");
  if (desktopType === "text_node" && data.copywriting_workbench) return NODE_SPEC_BY_KEY.copywriting;
  if (desktopType === "image_node" && data.multi_image_composer) return NODE_SPEC_BY_KEY.multi_image;
  if (desktopType === "video_node" && data.multi_image_director) return NODE_SPEC_BY_KEY.multi_director;
  if (desktopType === "image_node" && data.asset_kind) return NODE_SPEC_BY_KEY[`${data.asset_kind}_reference`] || NODE_SPEC_BY_KEY.image_asset;
  return NODE_SPECS.find((item) => item.desktopType === desktopType) || NODE_SPEC_BY_KEY.script;
}

export function canConnect(sourceKey: string, targetKey: string) {
  const source = NODE_SPEC_BY_KEY[sourceKey];
  const target = NODE_SPEC_BY_KEY[targetKey];
  if (!source || !target) return false;
  if (source.output.includes("any") || target.input.includes("any")) return true;
  return source.output.some((type) => target.input.includes(type));
}

export const CREATION_GROUPS: Array<{ key: NodeGroup; label: string }> = [
  { key: "primary", label: "高频创作能力" },
  { key: "basic", label: "基础节点" },
  { key: "analysis", label: "分析与专业工具" },
  { key: "reference", label: "参考节点" },
];

export function buildSkillPrompts(action: string, instruction: string) {
  if (action === "多机位九宫格") {
    return ["超远景建立空间", "全景人物与环境", "中全景调度", "中景表演", "近景情绪", "面部特写", "肩后反打", "低机位仰拍", "高机位俯拍"].map((shot, index) => `${instruction}。机位方案 ${index + 1}/9：${shot}。保持同一角色、服装、场景、时刻与轴线。`);
  }
  if (action === "25 宫格连贯分镜") {
    return Array.from({ length: 25 }, (_, index) => `${instruction}。连贯分镜第 ${index + 1}/25 格，表现动作进度 ${Math.round(index / 24 * 100)}%，严格继承上一格人物位置、朝向、服装、光线和场景，只推进一个清晰动作节拍。`);
  }
  if (action === "角色设定") {
    return ["正面全身", "左侧面全身", "背面全身", "四分之三正面", "四分之三背面", "面部近景", "喜悦表情", "悲伤表情", "愤怒表情"].map((view, index) => `${instruction}。角色设定第 ${index + 1}/9：${view}，中性背景，固定五官、发型、体型、服装、材质和配色。`);
  }
  if (action === "电影级光影调整") {
    return ["柔和窗光", "伦勃朗侧光", "阴天漫射光", "黄金时刻逆光", "蓝调夜景", "霓虹双色光", "硬质顶光", "烛火暖光", "高反差黑色电影"].map((look, index) => `${instruction}。光影方案 ${index + 1}/9：${look}。保持人物身份、动作、构图和场景结构完全不变。`);
  }
  if (action === "情绪调整") {
    return ["克制平静", "轻微喜悦", "明显喜悦", "隐忍悲伤", "崩溃悲伤", "警觉恐惧", "强烈恐惧", "压抑愤怒", "爆发愤怒"].map((emotion, index) => `${instruction}。情绪方案 ${index + 1}/9：${emotion}。保持人物身份、服装、镜头与背景不变，只调整眼神、眉眼、嘴角、肌肉紧张和身体姿态。`);
  }
  return [];
}
