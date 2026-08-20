"use client";

import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import NextImage from "next/image";
import {
  addEdge, Background, BackgroundVariant, Connection, Controls, Edge, Handle,
  MarkerType, MiniMap, Node, NodeProps, Position, ReactFlow, ReactFlowProvider, SelectionMode,
  useEdgesState, useNodesState, useReactFlow,
} from "@xyflow/react";
import {
  AudioLines, Boxes, ChevronDown, CirclePlay, Clapperboard, FileText, Film,
  FolderOpen, Image as ImageIcon, Import, LayoutGrid, Menu, Mic2,
  MoreHorizontal, Plus, Redo2, ScanSearch, Settings2, Sparkles, Upload, Users,
  WandSparkles, X, Zap, Camera, Shapes, Workflow, ListTodo, PackageCheck, Trash2,
  Download, Maximize2, Link2, Link2Off, Map as MapIcon, Copy, ClipboardCopy, PackagePlus,
} from "lucide-react";
import "@xyflow/react/dist/style.css";
import { desktopProjectToWeb, toWebCanvas } from "../../lib/canvas-protocol";
import { buildSkillPrompts, canConnect, CREATION_GROUPS, NODE_SPEC_BY_KEY, StudioNodeKind } from "../../lib/node-registry";
import { PulseEdge } from "./PulseEdge";
import { useControlPlane } from "./ControlPlane";
import { AdminPanel } from "./AdminPanel";
import { TeamPanel } from "./TeamPanel";

type StudioData = {
  title: string;
  description: string;
  kind: StudioNodeKind;
  specKey: string;
  desktopType: string;
  status: string;
  meta: string;
  accent: string;
  progress?: number;
  desktopPayload?: Record<string, unknown>;
};

type StudioNode = Node<StudioData, "studio">;
type ProviderInfo = { name: string; capabilities: string[]; profile?: Record<string, unknown> };
type LibraryAsset = { id: string; name: string; kind: string; size: number; content_type?: string; contentType?: string; in_library?: boolean };
type QueueTask = { id: string; node_id: string; kind: string; provider: string; status: string; progress: number; managed_by?: "task" | "workflow" | "production"; error_message?: string };
type PendingTask = { action: string; model: string; credits: number; run: () => Promise<void> };
type WorkflowTemplate = { id: string; name: string; definition: { nodes?: StudioNode[]; edges?: Edge[] } };
type ProjectSummary = { id: string; title: string; version: number; owner_id?: string; updated_at?: string; updatedAt?: string | number };
type SyncedProjectDraft = { title: string; nodes: StudioNode[]; edges: Edge[] };

const kindIcons: Record<StudioNodeKind, typeof Sparkles> = {
  project: Clapperboard,
  storyboard: Sparkles,
  script: FileText,
  copywriting: Mic2,
  image: ImageIcon,
  director: Clapperboard,
  video: Film,
  audio: AudioLines,
  analysis: ScanSearch,
  shot: Camera,
  reference: Shapes,
  skill: WandSparkles,
  workflow: Workflow,
  task: ListTodo,
  result: PackageCheck,
};

const initialNodes: StudioNode[] = [
  {
    id: "story", type: "studio", position: { x: 90, y: 95 },
    data: { title: "AI 故事板", description: "雨夜，一台送货机器人发现最后一封没有寄出的信。", kind: "storyboard", specKey: "storyboard", desktopType: "storyboard_node", status: "已定稿", meta: "6 镜 · 42 秒", accent: "#8b7cff" },
  },
  {
    id: "images", type: "studio", position: { x: 430, y: 70 },
    data: { title: "多图生成图片", description: "锁定机器人身份、雨夜街道和暖黄色信封，生成统一视觉资产。", kind: "image", specKey: "multi_image", desktopType: "image_node", status: "4 张已采用", meta: "16:9 · Seedream", accent: "#50b9dd" },
  },
  {
    id: "director", type: "studio", position: { x: 790, y: 92 },
    data: { title: "多图导演视频", description: "0–4 秒推进，4 秒切近景，8 秒摇镜跟随机器人穿过积水。", kind: "director", specKey: "multi_director", desktopType: "video_node", status: "等待生成", meta: "12 秒 · Seedance", accent: "#6f8cff" },
  },
  {
    id: "video", type: "studio", position: { x: 1150, y: 74 },
    data: { title: "成片候选 01", description: "主体和场景连续性通过，镜头节奏与声音仍待审片。", kind: "video", specKey: "video", desktopType: "video_node", status: "AI 审片中", meta: "1080p · 00:12", accent: "#f1a85b", progress: 72 },
  },
  {
    id: "copy", type: "studio", position: { x: 190, y: 430 },
    data: { title: "信息流口播文案", description: "开头 3 秒抓住注意力，中段呈现核心卖点，结尾保留行动号召。", kind: "copywriting", specKey: "copywriting", desktopType: "text_node", status: "中文原稿", meta: "98 字 · 预计 24 秒", accent: "#f07daf" },
  },
  {
    id: "audio", type: "studio", position: { x: 540, y: 450 },
    data: { title: "对白配音", description: "克制、温暖，句间停顿 0.5 秒，保留结尾轻微呼吸感。", kind: "audio", specKey: "audio", desktopType: "audio_node", status: "可试听", meta: "女声 · 1.0×", accent: "#66d49a" },
  },
  {
    id: "analysis", type: "studio", position: { x: 935, y: 430 },
    data: { title: "AI 自动拉片", description: "识别切镜、景别、人物运动轨迹、运镜、节奏与声音事件。", kind: "analysis", specKey: "analysis", desktopType: "video_analysis_node", status: "等待视频", meta: "运动轨迹增强版", accent: "#b993ff" },
  },
];

const initialEdges: Edge[] = [
  { id: "e-story-images", source: "story", target: "images" },
  { id: "e-images-director", source: "images", target: "director" },
  { id: "e-director-video", source: "director", target: "video" },
  { id: "e-copy-audio", source: "copy", target: "audio" },
  { id: "e-video-analysis", source: "video", target: "analysis" },
].map((edge) => ({
  ...edge, type: "pulse",
  markerEnd: { type: MarkerType.ArrowClosed, color: "#66718a" },
}));

function beginnerPreset(key: string, runtimeKey: string, title: string, description: string, group: "primary" | "basic" | "analysis", flow: string, defaults: Record<string, unknown> = {}) {
  const base = NODE_SPEC_BY_KEY[runtimeKey];
  return { ...base, key, runtimeKey, title, description, group, flow, defaults: { ...base.defaults, beginner_mode: key, ...defaults } };
}

const creationItems = [
  beginnerPreset("idea_to_movie", "storyboard", "一句话完成短片", "输入一个故事想法，自动完成拆镜、画面、视频和声音", "primary", "一句故事 → 完整短片"),
  beginnerPreset("write_script", "script", "写剧本", "生成、续写或修改剧本，并保留版本", "primary", "故事要求 → 剧本"),
  beginnerPreset("write_copy", "copywriting", "写口播文案", "生成信息流口播、改写、压缩或翻译", "primary", "产品信息 → 口播文案"),
  beginnerPreset("text_to_image", "multi_image", "文字生成图片", "写清楚想要的画面，不需要连接图片", "basic", "一段描述 → 新图片", { beginner_mode: "text_to_image" }),
  beginnerPreset("image_to_image", "multi_image", "图片生成图片", "先连接一张或多张图片，再写要保留和修改的内容", "basic", "参考图片 + 修改要求 → 新图片", { beginner_mode: "image_edit" }),
  beginnerPreset("text_to_video", "video", "一句话生成视频", "直接描述人物、动作、场景和镜头，不需要连接图片", "basic", "一段描述 → 视频", { beginner_mode: "text_to_video", editor_action: "文生视频" }),
  beginnerPreset("first_frame_video", "video", "一张图生成视频", "连接一张图片作为开场画面，再描述它怎么动", "basic", "1 张首帧 + 动作描述 → 视频", { beginner_mode: "first_frame_video", editor_action: "图生视频" }),
  beginnerPreset("first_last_frame_video", "video", "首尾帧生成视频", "连接开场图和结尾图，系统自动安排中间运动", "basic", "首帧 + 尾帧 + 动作描述 → 视频", { beginner_mode: "first_last_frame_video", editor_action: "图生视频" }),
  beginnerPreset("text_to_speech", "audio", "文字生成语音", "输入需要朗读的台词，再选择声音和语速", "basic", "台词 → 配音音频", { beginner_mode: "text_to_speech", editor_action: "对白配音" }),
  beginnerPreset("multi_image_director", "multi_director", "多图导演视频", "连接多张图片，逐段设置时间、动作和运镜", "analysis", "多张图片 + 时间轴 → 完整视频"),
  beginnerPreset("break_down_video", "analysis", "上传视频自动拉片", "连接一个视频，自动分析切镜、运镜、节奏、动作和声音", "analysis", "视频 → 详细拉片报告"),
  beginnerPreset("make_shot", "shot", "制作单个镜头", "精确控制一个镜头的画面、动作、运镜和对白", "analysis", "镜头要求 → 图片 / 视频 / 对白"),
  beginnerPreset("professional_tools", "skill", "专业导演工具", "需要九宫格、多机位、连续性或角色设定时使用", "analysis", "创作要求 → 专业方案"),
];

const BEGINNER_PLACEHOLDERS: Record<string, string> = {
  idea_to_movie: "例如：一只迷路的小机器人，在雨夜帮老人送出一封迟到十年的信。",
  write_script: "写下故事、人物、时长和你想要的情绪；不完整也没关系。",
  write_copy: "写下产品名称、核心卖点、目标用户和希望的口播风格。",
  text_to_image: "例如：动漫风格，雨夜街道，一只黄色送货机器人撑着红伞，电影光影。",
  image_edit: "例如：保留人物和服装，把白天改成雨夜，并增加霓虹灯倒影。",
  text_to_video: "例如：小机器人穿过雨夜街道，镜头缓慢跟随，路面积水反射霓虹。",
  first_frame_video: "描述图片中的主体怎么动，以及镜头怎么移动。",
  first_last_frame_video: "描述首帧如何自然运动到尾帧，中间不要突然跳变。",
  text_to_speech: "输入要朗读的台词，可以补充情绪、停顿和语速。",
  multi_image_director: "先概括整段视频发生什么，再到导演时间轴逐段设置动作。",
  break_down_video: "可选：写下你最关心的内容，例如人物运动轨迹、运镜或切镜节奏。",
  make_shot: "描述这个镜头要表达什么、人物做什么、镜头怎么拍。",
  professional_tools: "描述需要解决的导演问题，例如九宫格机位、连续动作或角色设定。",
};

function beginnerSteps(mode: string, incomingCount: number) {
  if (mode === "text_to_image") return ["写下想要的画面", "选择图片模型", "点击生成"];
  if (mode === "image_edit") return [incomingCount ? `已连接 ${incomingCount} 张图片` : "先连接至少 1 张图片", "写下保留和修改要求", "点击生成"];
  if (mode === "text_to_video") return ["用一句话描述画面和动作", "选择视频模型与时长", "点击生成"];
  if (mode === "first_frame_video") return [incomingCount ? "首帧图片已连接" : "先连接 1 张图片作为首帧", "描述画面怎么动", "点击生成"];
  if (mode === "first_last_frame_video") return [incomingCount >= 2 ? "首帧和尾帧已连接" : `连接首帧和尾帧（当前 ${incomingCount}/2）`, "描述中间如何运动", "点击生成"];
  if (mode === "text_to_speech") return ["输入需要朗读的台词", "选择声音、情绪和语速", "点击生成"];
  if (mode === "multi_image_director") return [incomingCount ? `已连接 ${incomingCount} 张图片` : "先连接多张图片", "逐段设置时间、动作和运镜", "一次生成完整视频"];
  if (mode === "break_down_video") return [incomingCount ? "视频已连接" : "先连接 1 个视频节点", "点击开始拉片", "得到镜头与运动分析"];
  if (mode === "idea_to_movie") return ["输入一句故事", "锁定脚本、图片和视频模型", "开始自动制片"];
  return ["写下你的要求", "选择模型和参数", "点击生成"];
}
const PRODUCTION_STAGE_NAMES = ["拆解镜头", "生成资产候选", "运动分镜", "合成定稿提示词", "生成定稿图片", "生成视频", "生成对白音频"];

// Direct actions mutate canvas/server state. Other actions are generation
// tasks and must pass quote -> confirmation -> queue before execution.
const DIRECT_NODE_ACTIONS = new Set([
  "保存到资产库", "保存版本", "恢复上一版", "切换剧本定稿", "创建制片项目",
  "采用AI候选稿", "清除AI结果", "复制文案", "恢复原文", "导出拉片报告",
  "采用", "驳回", "接受风险并继续", "执行工作流", "暂停工作流", "继续工作流",
  "取消工作流", "保存工作流模板", "暂停", "继续", "取消", "重试",
  "基于这张图继续编辑", "让这张图动起来（首帧）", "作为视频尾帧", "保存镜头修改",
  "设置每张图片的用途", "编辑图片时间、动作、运镜与用途",
]);

const ASSET_INPUT_MODES = new Set(["image_edit", "first_frame_video", "first_last_frame_video", "multi_image_director", "break_down_video"]);
const PROVIDER_SPEC_KEYS = new Set(["storyboard", "script", "copywriting", "audio", "video", "multi_director", "shot", "scene_reference", "character_reference", "element_reference"]);
const ASSET_INPUT_SPEC_KEYS = new Set(["script", "copywriting", "audio", "video", "multi_director", "analysis", "shot", "skill", "scene_reference", "character_reference", "element_reference"]);

function NodeEditorPortal({ nodeId, children }: { nodeId: string; children: ReactNode }) {
  const [target, setTarget] = useState<HTMLElement | null>(null);

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      setTarget(nodeId ? document.getElementById(`node-editor-slot-${nodeId}`) : null);
    });
    return () => cancelAnimationFrame(frame);
  }, [nodeId]);

  return target ? createPortal(children, target) : null;
}

function StudioNodeCard({ id, data, selected }: NodeProps<StudioNode>) {
  const Icon = kindIcons[data.kind];
  const { controlled } = useControlPlane();
  const payload = data.desktopPayload || {};
  const assetId = String(payload.asset_id || (Array.isArray(payload.output_asset_ids) ? payload.output_asset_ids[0] : "") || "");
  const explicitUrl = String(payload.asset_url || "");
  const controlBase = controlled ? (process.env.NEXT_PUBLIC_CONTROL_PLANE_URL || "").replace(/\/$/, "") : "";
  const mediaUrl = explicitUrl ? (controlBase && explicitUrl.startsWith("/api/") ? `${controlBase}${explicitUrl}` : explicitUrl) : assetId ? `${controlBase}/api/assets/${assetId}` : "";
  const downloadUrl = assetId ? `${controlBase}/api/assets/${assetId}?download=true` : mediaUrl;
  const hasImage = mediaUrl && (["image", "reference"].includes(data.kind) || data.desktopType === "image_node");
  const hasVideo = mediaUrl && data.kind === "video";
  return (
    <div className="studio-node-stack" style={{ "--node-accent": data.accent } as React.CSSProperties}>
      <article className={`studio-node ${selected ? "is-selected" : ""}`}>
        <Handle type="target" position={Position.Left} className="node-handle" />
        <div className="node-topline">
          <span className="node-icon"><Icon size={15} strokeWidth={1.8} /></span>
          <span className="node-status">{data.status}</span>
          <button className="icon-button node-more" aria-label="节点菜单"><MoreHorizontal size={16} /></button>
        </div>
        <h3>{data.title}</h3>
        {(hasImage || hasVideo) && <div className="node-media-frame nodrag nowheel">
          {hasImage && <NextImage className="node-media" src={mediaUrl} alt={data.title} width={320} height={180} unoptimized />}
          {hasVideo && <video className="node-media" src={mediaUrl} controls preload="metadata" aria-label={data.title}><track kind="captions" src="data:text/vtt,WEBVTT%0A%0A" srcLang="zh" label="当前视频没有字幕轨" default /></video>}
          <div className="node-media-actions">
            <a href={mediaUrl} target="_blank" rel="noreferrer" aria-label={`查看${data.title}`} title="原尺寸查看" onClick={(event) => event.stopPropagation()}><Maximize2 size={14} /></a>
            <a href={downloadUrl} download={data.title} aria-label={`下载${data.title}`} title="下载素材" onClick={(event) => event.stopPropagation()}><Download size={14} /></a>
          </div>
        </div>}
        {mediaUrl && data.kind === "audio" && <audio className="node-audio nodrag nowheel" src={mediaUrl} controls preload="metadata" aria-label={data.title}><track kind="captions" src="data:text/vtt,WEBVTT%0A%0A" srcLang="zh" label="当前音频没有字幕轨" default /></audio>}
        <p>{data.description}</p>
        {typeof data.progress === "number" && <div className="progress-track"><span style={{ width: `${data.progress}%` }} /></div>}
        <div className="node-meta">{data.meta}</div>
        <Handle type="source" position={Position.Right} className="node-handle" />
      </article>
      <div id={`node-editor-slot-${id}`} className="node-editor-slot nodrag nowheel nopan" />
    </div>
  );
}

const nodeTypes = { studio: StudioNodeCard };
const edgeTypes = { pulse: PulseEdge };

function normalizeStudioNode(node: StudioNode): StudioNode {
  const legacyByKind: Partial<Record<StudioNodeKind, string>> = {
    storyboard: "storyboard", script: "script", copywriting: "copywriting",
    image: "multi_image", director: "multi_director", video: "video",
    audio: "audio", analysis: "analysis", shot: "shot", skill: "skill",
  };
  const specKey = node.data.specKey || legacyByKind[node.data.kind] || "script";
  const current = NODE_SPEC_BY_KEY[specKey] || NODE_SPEC_BY_KEY.script;
  return {
    ...node,
    data: {
      ...node.data,
      specKey: current.key,
      desktopType: node.data.desktopType || current.desktopType,
      accent: node.data.accent || current.accent,
      desktopPayload: { ...current.defaults, ...(node.data.desktopPayload || {}) },
    },
  };
}

function requiredCapability(node: StudioNode, incomingCount: number) {
  const payload = node.data.desktopPayload || {}, action = String(payload.editor_action || "");
  if (["script", "copywriting", "skill"].includes(node.data.specKey)) return "chat";
  if (node.data.specKey === "multi_image") return incomingCount ? "image_edit" : "text_to_image";
  if (["scene_reference", "character_reference", "element_reference"].includes(node.data.specKey)) return action === "图生图" ? "image_edit" : "text_to_image";
  if (node.data.specKey === "video") return action === "文生视频" ? "text_to_video" : "image_to_video";
  if (node.data.specKey === "multi_director") {
    const timeline = Array.isArray(payload.timeline_images) ? payload.timeline_images : [];
    return action === "基于尾帧续拍" || timeline.some((item) => item && typeof item === "object" && ["first_frame", "last_frame"].includes(String((item as Record<string, unknown>).purpose || ""))) ? "image_to_video" : "text_to_video";
  }
  if (node.data.specKey === "audio") return "text_to_speech";
  if (node.data.specKey === "shot") return action === "生成对白" ? "text_to_speech" : action === "生成视频" ? (incomingCount ? "image_to_video" : "text_to_video") : incomingCount ? "image_edit" : "text_to_image";
  return "";
}

function lockedModel(payload: Record<string, unknown>, providers: ProviderInfo[]) {
  const provider = String(payload.provider_name || "");
  return String(payload.model || providers.find((item) => item.name === provider)?.profile?.model || "");
}

function compileShotPrompt(node: StudioNode) {
  const shot = (node.data.desktopPayload?.shot || {}) as Record<string, unknown>;
  const invariants = Array.isArray(shot.continuity_invariants) ? shot.continuity_invariants.join("；") : String(shot.continuity_invariants || "");
  return [
    node.data.description,
    shot.story_function && `故事功能：${shot.story_function}`,
    shot.visual_thesis && `视觉命题：${shot.visual_thesis}`,
    shot.shot_size && `景别：${shot.shot_size}`,
    shot.duration && `时长：${shot.duration} 秒`,
    shot.action_start && `动作起点：${shot.action_start}`,
    shot.primary_action && `唯一主动作：${shot.primary_action}`,
    shot.action_end && `动作终点：${shot.action_end}`,
    shot.dominant_camera_move && `唯一主运镜：${shot.dominant_camera_move}`,
    invariants && `连续性不变量：${invariants}`,
    shot.dialogue && `对白：${shot.dialogue}`,
    shot.generation_risk && `主要生成风险：${shot.generation_risk}`,
  ].filter(Boolean).join("\n");
}

function CanvasApp() {
  const { apiFetch, controlled, signOut, user } = useControlPlane();
  const { screenToFlowPosition } = useReactFlow<StudioNode, Edge>();
  const [nodes, setNodes, onNodesChange] = useNodesState<StudioNode>(controlled ? [] : initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(controlled ? [] : initialEdges);
  const [selectedId, setSelectedId] = useState(controlled ? "" : "director");
  const [createOpen, setCreateOpen] = useState(false);
  const [projectMenuOpen, setProjectMenuOpen] = useState(false);
  const [notice, setNotice] = useState("所有更改已保存");
  const [adminOpen, setAdminOpen] = useState(false);
  const [teamOpen, setTeamOpen] = useState(false);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [sideView, setSideView] = useState<"canvas" | "assets" | "tasks">("canvas");
  const [libraryAssets, setLibraryAssets] = useState<LibraryAsset[]>([]);
  const [queueTasks, setQueueTasks] = useState<QueueTask[]>([]);
  const [pendingTask, setPendingTask] = useState<PendingTask | null>(null);
  const [redoStage, setRedoStage] = useState<number | null>(null);
  const [edgesVisible, setEdgesVisible] = useState(true);
  const [mapVisible, setMapVisible] = useState(true);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; nodeId?: string } | null>(null);
  const [workflowTemplates, setWorkflowTemplates] = useState<WorkflowTemplate[]>([]);
  const [projectRole, setProjectRole] = useState("");
  const [projectConflict, setProjectConflict] = useState(false);
  const [fileDragActive, setFileDragActive] = useState(false);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [projectTitle, setProjectTitle] = useState(controlled ? "未命名项目" : "雨夜最后一封信");
  const [projectId, setProjectId] = useState("");
  const [hydrated, setHydrated] = useState(false);
  const versionRef = useRef(1);
  const bootingRef = useRef(false);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const suppressNextSaveRef = useRef(false);
  const lastSyncedProjectRef = useRef<SyncedProjectDraft>({ title: controlled ? "未命名项目" : "雨夜最后一封信", nodes: controlled ? [] : initialNodes, edges: controlled ? [] : initialEdges });
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  const projectTitleRef = useRef(projectTitle);
  const importInputRef = useRef<HTMLInputElement>(null);
  const projectInputRef = useRef<HTMLInputElement>(null);
  const fileDragDepthRef = useRef(0);

  const selectedNode = useMemo(() => nodes.find((node) => node.id === selectedId), [nodes, selectedId]);
  const selectedNodeIds = useMemo(() => nodes.filter((node) => node.selected).map((node) => node.id), [nodes]);
  const incomingNodes = useMemo(() => edges.filter((edge) => edge.target === selectedId).map((edge) => nodes.find((node) => node.id === edge.source)).filter((node): node is StudioNode => Boolean(node)), [edges, nodes, selectedId]);
  const beginnerMode = String(selectedNode?.data.desktopPayload?.beginner_mode || "");
  const currentBeginnerSteps = beginnerMode ? beginnerSteps(beginnerMode, incomingNodes.length) : [];
  const hasScriptResult = Boolean(selectedNode?.data.desktopPayload?.script_candidate || selectedNode?.data.desktopPayload?.script_review);
  const selectedActions = selectedNode ? NODE_SPEC_BY_KEY[selectedNode.data.specKey]?.actions || [] : [];
  const selectedAction = String(selectedNode?.data.desktopPayload?.editor_action || selectedActions[0] || "");
  const actionNeedsGenerationConfig = Boolean(selectedNode && (selectedNode.data.specKey === "storyboard" || !DIRECT_NODE_ACTIONS.has(selectedAction)));
  const hasGenerationParameters = Boolean(actionNeedsGenerationConfig && selectedNode && ["storyboard", "copywriting", "audio", "video", "multi_director", "shot", "scene_reference", "character_reference", "element_reference"].includes(selectedNode.data.specKey));
  const hasNodeCommand = Boolean(selectedNode && (selectedNode.data.specKey === "storyboard" || selectedActions.length));
  const showAssetInput = Boolean(selectedNode && ASSET_INPUT_SPEC_KEYS.has(selectedNode.data.specKey) && (incomingNodes.length || ASSET_INPUT_MODES.has(beginnerMode)));
  const hasProviderSelection = Boolean(actionNeedsGenerationConfig && selectedNode && PROVIDER_SPEC_KEYS.has(selectedNode.data.specKey) && selectedAction !== "提取首中尾帧");
  const canWrite = !controlled || (["admin", "producer", "director", "editor"].includes(user.role) && ["owner", "editor"].includes(projectRole));
  const canReview = canWrite || (user.role === "reviewer" && projectRole === "reviewer");
  const canCreateProject = !controlled || ["admin", "producer", "director", "editor"].includes(user.role);

  const deleteSelectedNodes = useCallback(() => {
    if (!canWrite) { setNotice("当前项目是只读状态，不能删除节点"); return; }
    const ids = new Set(selectedNodeIds.length ? selectedNodeIds : selectedId ? [selectedId] : []);
    if (!ids.size) return;
    setNodes((current) => current.filter((node) => !ids.has(node.id)));
    setEdges((current) => current.filter((edge) => !ids.has(edge.source) && !ids.has(edge.target)));
    setSelectedId("");
    setNotice(`已删除 ${ids.size} 个节点`);
  }, [canWrite, selectedId, selectedNodeIds, setEdges, setNodes]);

  const duplicateNode = (nodeId: string) => {
    if (!canWrite) { setNotice("当前项目是只读状态，不能复制节点"); return; }
    const source = nodesRef.current.find((node) => node.id === nodeId);
    if (!source) return;
    const id = `${source.data.specKey}-${crypto.randomUUID()}`;
    const payload = structuredClone(source.data.desktopPayload || {});
    delete payload.asset_id; delete payload.output_asset_ids; delete payload.production_run_id;
    setNodes((current) => [...current, { ...source, id, selected: false, position: { x: source.position.x + 42, y: source.position.y + 42 }, data: { ...source.data, title: `${source.data.title} 副本`, status: "节点副本", desktopPayload: payload } }]);
    setSelectedId(id); setNotice("节点已复制；生成结果没有被重复绑定");
  };

  const saveContextNodeToLibrary = async (nodeId: string) => {
    const node = nodesRef.current.find((item) => item.id === nodeId);
    const payload = node?.data.desktopPayload || {};
    const ids = [payload.asset_id, ...(Array.isArray(payload.output_asset_ids) ? payload.output_asset_ids : [])].filter(Boolean).map(String);
    if (!controlled || !ids.length) { setNotice(ids.length ? "公司服务器接入后才能保存资产库副本" : "该节点还没有可保存的媒体结果"); return; }
    for (const id of ids) {
      const response = await apiFetch(`/api/assets/${id}/save-to-library`, { method: "POST" });
      if (!response.ok) { setNotice("保存到资产库失败"); return; }
    }
    setNotice(`已保存 ${ids.length} 个媒体副本到资产库`);
  };

  useEffect(() => { nodesRef.current = nodes; }, [nodes]);
  useEffect(() => { edgesRef.current = edges; }, [edges]);
  useEffect(() => { projectTitleRef.current = projectTitle; }, [projectTitle]);

  useEffect(() => {
    if (!selectedNode || selectedNode.data.specKey !== "multi_director" || !incomingNodes.length) return;
    const existing = Array.isArray(selectedNode.data.desktopPayload?.timeline_images) ? selectedNode.data.desktopPayload.timeline_images as Array<Record<string, unknown>> : [];
    const duration = Number(selectedNode.data.desktopPayload?.duration || 10), step = duration / incomingNodes.length;
    const timeline = incomingNodes.map((node, index) => ({ source_node_id: node.id, start: Number((index * step).toFixed(2)), end: Number(((index + 1) * step).toFixed(2)), purpose: "continuity", action: "推进一个清晰动作", camera: "", ...(existing.find((item) => item.source_node_id === node.id) || {}) }));
    if (JSON.stringify(timeline) === JSON.stringify(existing)) return;
    setNodes((current) => current.map((node) => node.id === selectedNode.id ? { ...node, data: { ...node.data, desktopPayload: { ...(node.data.desktopPayload || {}), timeline_images: timeline } } } : node));
  }, [incomingNodes, selectedNode, setNodes]);

  useEffect(() => {
    if (bootingRef.current) return;
    bootingRef.current = true;
    void (async () => {
      try {
        const listing = await apiFetch("/api/projects", { cache: "no-store" });
        if (!listing.ok) throw new Error("项目列表不可用");
        const data = await listing.json() as { projects?: ProjectSummary[] };
        setProjects(data.projects || []);
        const first = data.projects?.[0];
        if (first) {
          const response = await apiFetch(`/api/projects/${first.id}`, { cache: "no-store" });
          const detail = await response.json() as { project?: { id: string; title: string; version: number; canvas: { nodes: StudioNode[]; edges: Edge[] } } };
          if (!response.ok || !detail.project) throw new Error("工程载入失败");
          suppressNextSaveRef.current = true;
          setProjectId(detail.project.id); setProjectTitle(detail.project.title);
          versionRef.current = detail.project.version;
          const nextNodes = detail.project.canvas.nodes.map(normalizeStudioNode);
          const nextEdges = detail.project.canvas.edges.map((edge) => ({ ...edge, type: "pulse" }));
          lastSyncedProjectRef.current = { title: detail.project.title, nodes: nextNodes, edges: nextEdges };
          setNodes(nextNodes); setSelectedId(nextNodes[0]?.id || ""); setEdges(nextEdges);
        } else {
          if (!canCreateProject) {
            setNodes([]); setEdges([]); setSelectedId("");
            setNotice("还没有分配给你的工程，请联系制片人或管理员");
            return;
          }
          const response = await apiFetch("/api/projects", {
            method: "POST", headers: { "content-type": "application/json" },
            body: JSON.stringify({ title: "未命名项目", canvas: toWebCanvas([], []) }),
          });
          const created = await response.json() as { project?: ProjectSummary };
          if (!response.ok || !created.project) throw new Error("创建工程失败");
          suppressNextSaveRef.current = true;
          setProjects([created.project]); setProjectId(created.project.id); setProjectTitle(created.project.title);
          lastSyncedProjectRef.current = { title: created.project.title, nodes: [], edges: [] };
          setNodes([]); setEdges([]); setSelectedId(""); versionRef.current = created.project.version;
        }
        setNotice("服务器项目已同步");
      } catch {
        setNotice("本地预览 · 连接服务器后自动同步");
      } finally {
        setHydrated(true);
      }
    })();
  }, [apiFetch, canCreateProject, setEdges, setNodes]);

  useEffect(() => {
    if (!controlled) return;
    void apiFetch("/api/providers", { cache: "no-store" }).then(async (response) => {
      if (!response.ok) return;
      const data = await response.json() as { providers?: ProviderInfo[] };
      setProviders(data.providers || []);
    });
  }, [apiFetch, controlled]);

  useEffect(() => {
    if (!controlled || !projectId) return;
    void apiFetch(`/api/projects/${projectId}/members`, { cache: "no-store" }).then(async (response) => {
      if (!response.ok) return;
      const data = await response.json() as { members?: Array<{ user_id: string; role: string }> };
      setProjectRole(data.members?.find((member) => member.user_id === user.id)?.role || (user.role === "admin" ? "owner" : "viewer"));
    });
  }, [apiFetch, controlled, projectId, user.id, user.role]);

  useEffect(() => {
    if (!controlled || !createOpen) return;
    void apiFetch("/api/workflow-templates", { cache: "no-store" }).then(async (response) => {
      if (!response.ok) return;
      const data = await response.json() as { templates?: WorkflowTemplate[] };
      setWorkflowTemplates(data.templates || []);
    });
  }, [apiFetch, controlled, createOpen]);

  useEffect(() => {
    if (!hydrated || !projectId) return;
    if (suppressNextSaveRef.current) { suppressNextSaveRef.current = false; return; }
    if (!canWrite) return;
    if (!projectTitle.trim()) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    let cancelled = false;
    const save = async () => {
      if (cancelled) return;
      setNotice("正在保存…");
      try {
        const response = await apiFetch(`/api/projects/${projectId}`, {
          method: "PATCH", headers: { "content-type": "application/json" },
          body: JSON.stringify({ title: projectTitle, canvas: toWebCanvas(nodes, edges), expectedVersion: versionRef.current }),
        });
        const data = await response.json() as { project?: { version: number }; currentVersion?: number };
        if (response.status === 409) { versionRef.current = data.currentVersion || versionRef.current; setProjectConflict(true); setNotice("检测到其他成员更新 · 当前自动保存已暂停"); return; }
        if (!response.ok || !data.project) throw new Error("save failed");
        versionRef.current = data.project.version;
        lastSyncedProjectRef.current = { title: projectTitle, nodes, edges };
        setProjects((current) => current.map((item) => item.id === projectId ? { ...item, title: projectTitle, version: data.project!.version } : item));
        setProjectConflict(false); setNotice("所有更改已保存");
      } catch {
        if (cancelled) return;
        setNotice("保存失败 · 3 秒后自动重试");
        saveTimerRef.current = setTimeout(() => void save(), 3000);
      }
    };
    saveTimerRef.current = setTimeout(() => void save(), 700);
    return () => { cancelled = true; if (saveTimerRef.current) clearTimeout(saveTimerRef.current); };
  }, [apiFetch, canWrite, edges, hydrated, nodes, projectId, projectTitle]);

  const saveCurrentProjectNow = async () => {
    if (!controlled || !projectId || !canWrite) return true;
    if (!projectTitle.trim()) { setNotice("请先填写工程名称"); return false; }
    if (projectConflict) { setNotice("当前工程存在版本冲突，请先重新载入再切换"); return false; }
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    setNotice("正在保存当前工程…");
    try {
      const response = await apiFetch(`/api/projects/${projectId}`, {
        method: "PATCH", headers: { "content-type": "application/json" },
        body: JSON.stringify({ title: projectTitle, canvas: toWebCanvas(nodes, edges), expectedVersion: versionRef.current }),
      });
      const data = await response.json() as { project?: ProjectSummary; currentVersion?: number; detail?: string };
      if (response.status === 409) {
        versionRef.current = data.currentVersion || versionRef.current; setProjectConflict(true);
        setNotice("检测到其他成员更新，请先重新载入再切换"); return false;
      }
      if (!response.ok || !data.project) { setNotice(data.detail || "当前工程保存失败，已取消切换"); return false; }
      versionRef.current = data.project.version;
      lastSyncedProjectRef.current = { title: projectTitle, nodes, edges };
      setProjects((current) => current.map((item) => item.id === projectId ? { ...item, ...data.project } : item));
      setNotice("当前工程已保存");
      return true;
    } catch {
      setNotice("当前工程保存失败，已取消切换"); return false;
    }
  };

  const loadProject = async (targetId: string) => {
    const response = await apiFetch(`/api/projects/${targetId}`, { cache: "no-store" });
    const data = await response.json() as { project?: ProjectSummary & { canvas: { nodes: StudioNode[]; edges: Edge[] } }; detail?: string };
    if (!response.ok || !data.project) { setNotice(data.detail || "工程载入失败"); return false; }
    suppressNextSaveRef.current = true;
    setProjectRole(""); setProjectId(data.project.id); setProjectTitle(data.project.title);
    versionRef.current = data.project.version;
    const nextNodes = (data.project.canvas.nodes || []).map(normalizeStudioNode);
    const nextEdges = (data.project.canvas.edges || []).map((edge) => ({ ...edge, type: "pulse" }));
    lastSyncedProjectRef.current = { title: data.project.title, nodes: nextNodes, edges: nextEdges };
    setNodes(nextNodes); setEdges(nextEdges);
    setSelectedId(nextNodes[0]?.id || ""); setProjectConflict(false);
    setProjects((current) => current.map((item) => item.id === data.project!.id ? { ...item, ...data.project } : item));
    setNotice(`已打开工程：${data.project.title}`);
    return true;
  };

  const switchProject = async (targetId: string) => {
    setProjectMenuOpen(false);
    if (!targetId || targetId === projectId) return;
    if (!await saveCurrentProjectNow()) return;
    await loadProject(targetId);
  };

  const createBlankProject = async () => {
    setProjectMenuOpen(false);
    if (!canCreateProject) { setNotice("当前账号没有创建工程的权限"); return; }
    if (!await saveCurrentProjectNow()) return;
    const sequence = projects.length + 1;
    const response = await apiFetch("/api/projects", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ title: `未命名项目 ${sequence}`, canvas: toWebCanvas([], []) }),
    });
    const data = await response.json() as { project?: ProjectSummary & { canvas?: { nodes: StudioNode[]; edges: Edge[] } }; detail?: string };
    if (!response.ok || !data.project) { setNotice(data.detail || "新建工程失败"); return; }
    setProjects((current) => [data.project!, ...current]);
    suppressNextSaveRef.current = true; setProjectRole(""); setProjectId(data.project.id); setProjectTitle(data.project.title);
    lastSyncedProjectRef.current = { title: data.project.title, nodes: [], edges: [] };
    versionRef.current = data.project.version; setNodes([]); setEdges([]); setSelectedId(""); setProjectConflict(false);
    setNotice("空白工程已创建，请从“新建”添加第一个节点");
  };

  const reloadProject = async () => {
    if (!projectId) return;
    const response = await apiFetch(`/api/projects/${projectId}`, { cache: "no-store" });
    const data = await response.json() as { project?: { title: string; version: number; canvas: { nodes: StudioNode[]; edges: Edge[] } }; detail?: string };
    if (!response.ok || !data.project) { setNotice(data.detail || "项目重新载入失败"); return; }
    suppressNextSaveRef.current = true;
    const nextNodes = data.project.canvas.nodes.map(normalizeStudioNode), nextEdges = data.project.canvas.edges.map((edge) => ({ ...edge, type: "pulse" }));
    lastSyncedProjectRef.current = { title: data.project.title, nodes: nextNodes, edges: nextEdges };
    versionRef.current = data.project.version; setProjectTitle(data.project.title); setNodes(nextNodes); setEdges(nextEdges); setSelectedId(nextNodes[0]?.id || ""); setProjectConflict(false); setNotice("已载入服务器上的最新版本");
  };

  const onConnect = useCallback((connection: Connection) => {
    if (!canWrite) { setNotice("当前项目是只读状态，不能创建连线"); return; }
    const source = nodes.find((node) => node.id === connection.source);
    const target = nodes.find((node) => node.id === connection.target);
    if (!source || !target || source.id === target.id || !canConnect(source.data.specKey, target.data.specKey)) {
      setNotice("这两个节点的数据类型不兼容，连线未创建");
      return;
    }
    const targetPayload = target.data.desktopPayload || {};
    const existingInputs = edges.filter((edge) => edge.target === target.id);
    let relation = "reference";
    if (target.data.specKey === "video" && String(targetPayload.editor_action || "") === "图生视频" && ["image_asset", "multi_image", "scene_reference", "character_reference", "element_reference"].includes(source.data.specKey)) {
      relation = existingInputs.length === 0 ? "first_frame" : String(targetPayload.beginner_mode || "") === "first_last_frame_video" && existingInputs.length === 1 ? "last_frame" : "reference";
    }
    setEdges((current) => addEdge({
      ...connection, type: "pulse",
      data: { relation },
      markerEnd: { type: MarkerType.ArrowClosed, color: "#77839c" },
    }, current));
    setNotice(relation === "first_frame" ? "图片已自动设为视频首帧" : relation === "last_frame" ? "第二张图片已自动设为视频尾帧" : "连线已保存");
  }, [canWrite, edges, nodes, setEdges]);

  const addNode = (item: (typeof creationItems)[number], uniqueId: string) => {
    if (!canWrite) { setNotice("当前项目是只读状态，不能新建节点"); return; }
    const id = `${item.key}-${uniqueId}`;
    const offset = nodes.length * 24;
    setNodes((current) => [...current, {
      id, type: "studio", position: { x: 380 + offset, y: 260 + offset },
      data: { title: item.title, description: "", kind: item.kind, specKey: item.runtimeKey, desktopType: item.desktopType, status: "等待输入", meta: item.flow, accent: item.accent, desktopPayload: { ...item.defaults, beginner_description: item.description, beginner_flow: item.flow, type: item.desktopType } },
    }]);
    setSelectedId(id); setCreateOpen(false); setNotice(`${item.title}已创建`);
  };

  const updateSelected = (key: "title" | "description", value: string) => {
    if (!canWrite) { setNotice("当前项目是只读状态"); return; }
    setNodes((current) => current.map((node) => node.id === selectedId ? { ...node, data: { ...node.data, [key]: value } } : node));
    setNotice("有未保存更改");
  };

  const updatePayload = (key: string, value: unknown) => {
    if (!canWrite) { setNotice("当前项目是只读状态"); return; }
    setNodes((current) => current.map((node) => node.id === selectedId ? { ...node, data: { ...node.data, desktopPayload: { ...(node.data.desktopPayload || {}), [key]: value } } } : node));
    setNotice("有未保存更改");
  };

  const updateReferenceRow = (key: "reference_settings" | "timeline_images", sourceNodeId: string, patch: Record<string, unknown>) => {
    if (!selectedNode) return;
    const current = Array.isArray(selectedNode.data.desktopPayload?.[key]) ? [...selectedNode.data.desktopPayload[key] as Array<Record<string, unknown>>] : [];
    const index = current.findIndex((item) => item.source_node_id === sourceNodeId);
    const value = { ...(index >= 0 ? current[index] : {}), source_node_id: sourceNodeId, ...patch };
    if (index >= 0) current[index] = value; else current.push(value);
    updatePayload(key, current);
  };

  const createDerivedNode = (source: StudioNode, targetKey: string, relation: string, overrides: Record<string, unknown> = {}) => {
    if (!canWrite) { setNotice("当前项目是只读状态，不能创建衍生节点"); return; }
    const target = NODE_SPEC_BY_KEY[targetKey], id = `${targetKey}-${crypto.randomUUID()}`;
    const displayTitle = String(overrides.beginner_title || target.title);
    setNodes((current) => [...current, { id, type: "studio", position: { x: source.position.x + 380, y: source.position.y + 30 }, data: { title: displayTitle, description: "", kind: target.kind, specKey: target.key, desktopType: target.desktopType, status: relation === "last_frame" ? "尾帧已设置 · 还需连接首帧" : "已连接参考图片", meta: "从图片节点创建", accent: target.accent, desktopPayload: { ...target.defaults, ...overrides } } }]);
    setEdges((current) => addEdge({ id: `${relation}-${source.id}-${id}`, source: source.id, target: id, type: "pulse", data: { relation } }, current)); setSelectedId(id); setNotice(`${displayTitle}已创建并连接`);
  };

  const buildWorkflowItems = (group: StudioNode) => {
    const childIds = Array.isArray(group.data.desktopPayload?.group_nodes) ? group.data.desktopPayload.group_nodes.map(String) : [];
    return childIds.map((childId) => nodes.find((node) => node.id === childId)).filter((node): node is StudioNode => Boolean(node)).map((node) => {
      const payload = node.data.desktopPayload || {};
      const nodeModel = lockedModel(payload, providers);
      const sourceEdges = edges.filter((edge) => edge.target === node.id);
      const sources = sourceEdges.map((edge) => nodes.find((item) => item.id === edge.source)).filter((item): item is StudioNode => Boolean(item));
      const references = sources.map((source) => { const relation = String((sourceEdges.find((edge) => edge.source === source.id)?.data as Record<string, unknown> | undefined)?.relation || "reference"); return { node_id: source.id, title: source.data.title, asset_id: source.data.desktopPayload?.asset_id || (Array.isArray(source.data.desktopPayload?.output_asset_ids) ? source.data.desktopPayload.output_asset_ids[0] : undefined), role: relation }; }).filter((item) => item.asset_id);
      const selectedAction = String(payload.editor_action || "");
      const mappings: Record<string, { kind: string; provider: string; model: string }> = {
        script: { kind: "chat", provider: String(payload.provider_name || ""), model: nodeModel },
        copywriting: { kind: "chat", provider: String(payload.provider_name || ""), model: nodeModel },
        multi_image: { kind: references.length ? "image_edit" : "text_to_image", provider: String(payload.provider_name || ""), model: nodeModel },
        scene_reference: { kind: selectedAction === "图生图" ? "image_edit" : "text_to_image", provider: String(payload.provider_name || ""), model: nodeModel },
        character_reference: { kind: selectedAction === "图生图" ? "image_edit" : "text_to_image", provider: String(payload.provider_name || ""), model: nodeModel },
        element_reference: { kind: selectedAction === "图生图" ? "image_edit" : "text_to_image", provider: String(payload.provider_name || ""), model: nodeModel },
        video: { kind: selectedAction === "基于尾帧续拍" ? "continue_video" : selectedAction === "提取首中尾帧" ? "extract_video_frames" : selectedAction === "图生视频" ? "image_to_video" : "text_to_video", provider: selectedAction === "提取首中尾帧" ? "local" : String(payload.provider_name || ""), model: nodeModel },
        multi_director: { kind: selectedAction === "基于尾帧续拍" ? "continue_video" : selectedAction === "提取首中尾帧" ? "extract_video_frames" : references.some((item) => ["first_frame", "last_frame"].includes(item.role)) ? "image_to_video" : "text_to_video", provider: selectedAction === "提取首中尾帧" ? "local" : String(payload.provider_name || ""), model: nodeModel },
        audio: { kind: "text_to_speech", provider: String(payload.provider_name || ""), model: nodeModel },
        analysis: { kind: "video_breakdown", provider: "local", model: "local" },
        shot: { kind: selectedAction === "生成视频" ? (references.length ? "image_to_video" : "text_to_video") : selectedAction === "生成对白" ? "text_to_speech" : selectedAction === "参考图再生成" ? "image_edit" : references.length ? "image_edit" : "text_to_image", provider: String(payload.provider_name || ""), model: nodeModel },
      };
      const mapping = mappings[node.data.specKey];
      if (!mapping) return null;
      const taskReferences = [...references];
      if (["continue_video", "extract_video_frames"].includes(mapping.kind)) {
        const ownIds = [payload.asset_id, ...(Array.isArray(payload.output_asset_ids) ? payload.output_asset_ids : [])].filter(Boolean);
        taskReferences.unshift(...ownIds.map((assetId) => ({ node_id: node.id, title: node.data.title, asset_id: assetId, role: "video_source" })));
      }
      return { node_id: node.id, kind: mapping.kind, provider: mapping.provider, model: mapping.model, input: { inputs: { prompt: node.data.specKey === "shot" ? compileShotPrompt(node) : node.data.description, references: taskReferences }, params: payload, action: selectedAction || "生成", use_cache: false } };
    }).filter((item): item is NonNullable<typeof item> => Boolean(item));
  };

  async function pollWorkflowRun(runId: string, group: StudioNode) {
    for (let attempt = 0; attempt < 7200; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
      const response = await apiFetch(`/api/workflow-runs/${runId}`, { cache: "no-store" });
      if (!response.ok) return;
      const data = await response.json() as { run: { status: string; current_index: number; total_items: number; progress: number; error_message?: string } };
      setNodes((current) => current.map((node) => node.id === group.id ? { ...node, data: { ...node.data, status: data.run.status === "completed" ? "工作流完成" : data.run.status === "paused" ? "工作流已暂停" : data.run.status === "failed" ? "工作流失败" : `顺序执行 · ${data.run.current_index + 1}/${data.run.total_items}`, progress: data.run.progress, desktopPayload: { ...(node.data.desktopPayload || {}), workflow_run_id: runId, workflow_status: data.run.status } } } : node));
      if (["completed", "failed", "paused", "cancelled"].includes(data.run.status)) {
        for (const childId of Array.isArray(group.data.desktopPayload?.group_nodes) ? group.data.desktopPayload.group_nodes.map(String) : []) await mergeServerResult(childId);
        setNotice(data.run.error_message || (data.run.status === "completed" ? "工作流已完成，所有结果已写回画布" : `工作流：${data.run.status}`)); return;
      }
    }
  }

  const instantiateWorkflowTemplate = (template: WorkflowTemplate) => {
    const sourceNodes = (template.definition.nodes || []).map(normalizeStudioNode);
    if (!sourceNodes.length) { setNotice("该模板没有可用节点"); return; }
    const idMap = new Map(sourceNodes.map((node) => [node.id, `${node.data.specKey}-${crypto.randomUUID()}`]));
    const minX = Math.min(...sourceNodes.map((node) => node.position.x)), minY = Math.min(...sourceNodes.map((node) => node.position.y));
    const created = sourceNodes.map((node) => ({ ...node, id: idMap.get(node.id)!, position: { x: 320 + node.position.x - minX, y: 180 + node.position.y - minY }, data: { ...node.data, status: "由模板创建", desktopPayload: { ...(node.data.desktopPayload || {}), ...(Array.isArray(node.data.desktopPayload?.group_nodes) ? { group_nodes: node.data.desktopPayload.group_nodes.map((id) => idMap.get(String(id))).filter(Boolean) } : {}) } } }));
    const templateEdges = (template.definition.edges || []).filter((edge) => idMap.has(edge.source) && idMap.has(edge.target)).map((edge) => ({ ...edge, id: `template-edge-${crypto.randomUUID()}`, source: idMap.get(edge.source)!, target: idMap.get(edge.target)!, type: "pulse" }));
    setNodes((current) => [...current, ...created]); setEdges((current) => [...current, ...templateEdges]); setSelectedId(created[0].id); setCreateOpen(false); setNotice(`工作流模板“${template.name}”已写入画布`);
  };

  const handleNodeAction = async (action: string) => {
    if (!selectedNode) return;
    const payload = selectedNode.data.desktopPayload || {};
    const reviewAction = ["采用", "驳回", "接受风险并继续"].includes(action);
    const readOnlyAction = ["复制文案", "导出拉片报告"].includes(action);
    if (reviewAction && !canReview) { setNotice("当前账号没有该项目的审片权限"); return; }
    if (!reviewAction && !readOnlyAction && !canWrite) { setNotice("当前项目是只读状态，不能修改或提交生成任务"); return; }
    if (action === "保存到资产库") {
      const ids = [payload.asset_id, ...(Array.isArray(payload.output_asset_ids) ? payload.output_asset_ids : [])].filter(Boolean).map(String);
      if (!controlled || !ids.length) { setNotice(ids.length ? "公司服务器接入后才能同步资产库副本" : "该节点还没有可保存的媒体结果"); return; }
      for (const id of ids) {
        const response = await apiFetch(`/api/assets/${id}/save-to-library`, { method: "POST" });
        if (!response.ok) { const data = await response.json() as { detail?: string }; setNotice(data.detail || "保存到资产库失败"); return; }
      }
      updatePayload("saved_to_library", true); setNotice(`已将 ${ids.length} 个媒体副本保存到资产库，画布仍是流程权威`); return;
    }
    if (selectedNode.data.specKey === "script") {
      const versions = Array.isArray(payload.script_versions) ? [...payload.script_versions] : [];
      if (action === "采用AI候选稿") {
        const candidate = String(payload.script_candidate || "").trim();
        if (!candidate) { setNotice("当前没有可采用的 AI 候选稿"); return; }
        const now = new Date().toISOString();
        const nextVersions = [
          ...versions,
          { version: versions.length + 1, content: selectedNode.data.description, saved_at: now, note: "采用候选稿前" },
          { version: versions.length + 2, content: candidate, saved_at: now, note: "采用AI候选稿" },
        ];
        setNodes((current) => current.map((node) => node.id === selectedNode.id ? { ...node, data: { ...node.data, description: candidate, status: `剧本 V${nextVersions.length} · 候选稿已采用`, desktopPayload: { ...(node.data.desktopPayload || {}), script_versions: nextVersions, script_version: nextVersions.length, script_locked: false, script_candidate: "", script_review: "" } } } : node));
        setNotice("AI 候选稿已采用，原稿已保存在节点版本历史中"); return;
      }
      if (action === "清除AI结果") {
        updatePayload("script_candidate", ""); updatePayload("script_review", ""); setNotice("AI 候选稿 / 报告已关闭，原稿未修改"); return;
      }
      if (action === "保存版本") { versions.push({ version: versions.length + 1, content: selectedNode.data.description, saved_at: new Date().toISOString() }); updatePayload("script_versions", versions); updatePayload("script_version", versions.length); setNotice(`剧本版本 ${versions.length} 已保存到画布节点`); return; }
      if (action === "恢复上一版") { const latest = versions.at(-1) as { content?: string } | undefined; const previous = latest?.content === selectedNode.data.description ? versions.at(-2) as { content?: string } | undefined : latest; if (!previous?.content) { setNotice("还没有可恢复的上一版"); return; } updateSelected("description", previous.content); setNotice("已恢复上一版，历史版本没有删除"); return; }
      if (action === "切换剧本定稿") { updatePayload("script_locked", !payload.script_locked); setNotice(payload.script_locked ? "剧本已解除定稿" : "剧本已定稿锁定"); return; }
      if (action === "创建制片项目") { createDerivedNode(selectedNode, "storyboard", "script_source", { source_script_node_id: selectedNode.id }); return; }
    }
    if (selectedNode.data.specKey === "copywriting") {
      if (action === "复制文案") { try { await navigator.clipboard.writeText(selectedNode.data.description); setNotice("文案已复制"); } catch { setNotice("浏览器未授予剪贴板权限，请手动复制"); } return; }
      if (action === "恢复原文") { const original = String(payload.original_text || ""); if (!original) { setNotice("当前节点没有保存原文"); return; } updateSelected("description", original); setNotice("已恢复原文"); return; }
      if (action === "翻译" && !payload.original_text) updatePayload("original_text", selectedNode.data.description);
    }
    if (selectedNode.data.specKey === "analysis" && action === "导出拉片报告") {
      const report = payload.analysis_result || payload.analysis;
      if (!report || (typeof report === "object" && !Object.keys(report as object).length)) { setNotice("请先完成 AI 拉片"); return; }
      const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json;charset=utf-8" }));
      const link = document.createElement("a"); link.href = url; link.download = `${selectedNode.data.title || "拉片报告"}.json`; link.click(); URL.revokeObjectURL(url); setNotice("拉片报告已导出"); return;
    }
    if (["asset_view", "asset_take", "result"].includes(selectedNode.data.specKey) && ["采用", "驳回", "接受风险并继续"].includes(action)) {
      const statusText = action === "采用" ? "已采用" : action === "驳回" ? "已驳回" : "风险已接受";
      if (controlled && projectId) {
        const decision = action === "采用" ? "adopt" : action === "驳回" ? "reject" : "accept_risk";
        const response = await apiFetch(`/api/projects/${projectId}/reviews`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ node_id: selectedNode.id, decision, expected_version: versionRef.current }) });
        const result = await response.json() as { project?: { version: number; canvas: { nodes: StudioNode[]; edges: Edge[] } }; currentVersion?: number; detail?: string };
        if (response.status === 409) { versionRef.current = result.currentVersion || versionRef.current; setProjectConflict(true); setNotice("其他成员已更新画布，请重新载入后再审片"); return; }
        if (!response.ok || !result.project) { setNotice(result.detail || "审片决定保存失败"); return; }
        versionRef.current = result.project.version; suppressNextSaveRef.current = true;
        const nextNodes = result.project.canvas.nodes.map(normalizeStudioNode), nextEdges = result.project.canvas.edges.map((edge) => ({ ...edge, type: "pulse" }));
        lastSyncedProjectRef.current = { title: projectTitle, nodes: nextNodes, edges: nextEdges };
        setNodes(nextNodes); setEdges(nextEdges);
        setNotice(`${selectedNode.data.title} · ${statusText}，审片决定已写入服务器`); return;
      }
      setNodes((current) => current.map((node) => node.id === selectedNode.id ? { ...node, data: { ...node.data, status: statusText, desktopPayload: { ...(node.data.desktopPayload || {}), review_decision: action, review_at: new Date().toISOString() } } } : node));
      setNotice(`${selectedNode.data.title} · ${statusText}`); return;
    }
    if (selectedNode.data.specKey === "workflow") {
      const groupIds = Array.isArray(payload.group_nodes) ? payload.group_nodes.map(String) : [];
      if (action === "保存工作流模板") {
        if (!controlled) { setNotice("接入公司服务器后才能保存持久模板"); return; }
        const allIds = new Set([selectedNode.id, ...groupIds]);
        const definition = { nodes: nodes.filter((node) => allIds.has(node.id)), edges: edges.filter((edge) => allIds.has(edge.source) && allIds.has(edge.target)) };
        const response = await apiFetch("/api/workflow-templates", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name: selectedNode.data.title, definition }) });
        const data = await response.json() as { detail?: string };
        setNotice(response.ok ? "工作流模板已保存，可从“新建”再次使用" : data.detail || "工作流模板保存失败"); return;
      }
      const runId = String(payload.workflow_run_id || "");
      const commandByAction: Record<string, string> = { "暂停工作流": "pause", "继续工作流": "resume", "取消工作流": "cancel" };
      if (commandByAction[action]) {
        if (!runId) { setNotice("该工作流还没有运行记录"); return; }
        const response = await apiFetch(`/api/workflow-runs/${runId}/command`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ command: commandByAction[action] }) });
        const data = await response.json() as { run?: { status: string }; detail?: string };
        if (!response.ok || !data.run) { setNotice(data.detail || `${action}失败`); return; }
        setNotice(`工作流状态：${data.run.status}`); if (action === "继续工作流") void pollWorkflowRun(runId, selectedNode); return;
      }
      if (action === "执行工作流") {
        if (!controlled || !projectId) { setNotice("接入公司服务器后才能执行持久工作流"); return; }
        if (!await saveCurrentProjectNow()) return;
        if (payload.workflow_status === "failed" && runId) {
          const response = await apiFetch(`/api/workflow-runs/${runId}/command`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ command: "retry" }) });
          const data = await response.json() as { run?: { status: string }; detail?: string }; if (!response.ok || !data.run) { setNotice(data.detail || "工作流重试失败"); return; } void pollWorkflowRun(runId, selectedNode); return;
        }
        const unsupportedAudio = groupIds.map((id) => nodes.find((node) => node.id === id)).find((node) => node?.data.specKey === "audio" && node.data.desktopPayload?.editor_action === "音效");
        if (unsupportedAudio) { setNotice(`“${unsupportedAudio.data.title}”选择了音效，但当前没有独立音效模型；任务未提交，避免错误扣费`); return; }
        const items = buildWorkflowItems(selectedNode);
        if (!items.length) { setNotice("工作流中没有可执行的生成节点"); return; }
        if (items.some((item) => item.provider !== "local" && !item.provider)) { setNotice("工作流中有节点尚未锁定生成引擎"); return; }
        const requestPayload = { project_id: projectId, node_id: selectedNode.id, items };
        const quoteResponse = await apiFetch("/api/workflow-runs/quote", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(requestPayload) });
        const quoteData = await quoteResponse.json() as { quote?: { items: number; credits: number }; detail?: string };
        if (!quoteResponse.ok || !quoteData.quote) { setNotice(quoteData.detail || "工作流额度校验失败"); return; }
        setPendingTask({ action: `顺序执行 ${quoteData.quote.items} 个工作流节点`, model: "按节点锁定引擎，不自动切换", credits: quoteData.quote.credits, run: async () => {
          setPendingTask(null); const response = await apiFetch("/api/workflow-runs", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(requestPayload) });
          const data = await response.json() as { run?: { id: string; status: string }; detail?: string }; if (!response.ok || !data.run) { setNotice(data.detail || "工作流启动失败"); return; }
          setNodes((current) => current.map((node) => node.id === selectedNode.id ? { ...node, data: { ...node.data, status: "工作流已启动", progress: 0, desktopPayload: { ...(node.data.desktopPayload || {}), workflow_run_id: data.run!.id, workflow_status: data.run!.status } } } : node)); setNotice("持久工作流已启动，关闭网页也会继续"); void pollWorkflowRun(data.run.id, selectedNode);
        } }); return;
      }
    }
    if (selectedNode.data.specKey === "task" && ["暂停", "继续", "取消", "重试"].includes(action)) {
      const taskId = String(payload.server_task_id || "");
      if (!taskId || !controlled) { setNotice("该任务没有可操作的公司队列记录"); return; }
      const operation = action === "暂停" ? "pause" : action === "继续" ? "resume" : action === "取消" ? "cancel" : "retry";
      const response = await apiFetch(`/api/tasks/${taskId}/${operation}`, { method: "POST" });
      const data = await response.json() as { task?: { id: string; status: string }; detail?: string };
      if (!response.ok || !data.task) { setNotice(data.detail || `${action}失败`); return; }
      setNotice(action === "重试" ? "重试任务已进入队列" : action === "继续" ? "任务已恢复排队" : action === "暂停" ? "任务已暂停" : "任务已取消");
      return;
    }
    if (selectedNode.data.specKey === "image_asset") {
      const assetId = String(payload.asset_id || (Array.isArray(payload.output_asset_ids) ? payload.output_asset_ids[0] : "") || "");
      if (action === "基于这张图继续编辑") { createDerivedNode(selectedNode, "multi_image", "reference", { beginner_mode: "image_edit", beginner_title: "图片生成图片", beginner_flow: "参考图片 + 修改要求 → 新图片", beginner_description: "图片已连接，请写下要保留和修改的内容" }); return; }
      if (action === "让这张图动起来（首帧）") { createDerivedNode(selectedNode, "video", "first_frame", { beginner_mode: "first_frame_video", beginner_title: "一张图生成视频", beginner_flow: "1 张首帧 + 动作描述 → 视频", beginner_description: "图片已自动设为首帧，请描述它怎么动", editor_action: "图生视频" }); return; }
      if (action === "作为视频尾帧") { createDerivedNode(selectedNode, "video", "last_frame", { beginner_mode: "first_last_frame_video", beginner_title: "首尾帧生成视频", beginner_flow: "首帧 + 尾帧 + 动作描述 → 视频", beginner_description: "尾帧已连接，请再连接一张图片作为首帧", editor_action: "图生视频", last_frame_asset_id: assetId }); return; }
    }
    if (selectedNode.data.specKey === "multi_image" && action === "设置每张图片的用途") { setNotice(incomingNodes.length ? "请展开“参考”按钮，逐张设置主体、场景、构图或元素用途" : "请先把图片节点连入，或点击“参考”导入图片"); return; }
    if (selectedNode.data.specKey === "multi_director" && action === "编辑图片时间、动作、运镜与用途") { setNotice(incomingNodes.length ? "请展开“时间轴”角标，按顺序设置每段时间、动作和运镜" : "请先把图片节点连接到多图导演节点"); return; }
    if (selectedNode.data.specKey === "shot" && action === "保存镜头修改") { setNotice("镜头修改已保存到画布节点"); return; }
    updatePayload("editor_action", action); setNotice(`已选择“${action}”`);
  };

  const openSideView = async (view: "canvas" | "assets" | "tasks") => {
    setSideView(view);
    if (view === "canvas" || !projectId) return;
    if (view === "assets") {
      const response = await apiFetch(`/api/assets?project_id=${encodeURIComponent(projectId)}&projectId=${encodeURIComponent(projectId)}&library_only=${controlled ? "true" : "false"}`, { cache: "no-store" });
      if (!response.ok) { setNotice("资产库读取失败"); return; }
      const data = await response.json() as { assets?: LibraryAsset[] }; setLibraryAssets(data.assets || []);
    } else if (controlled) {
      const response = await apiFetch(`/api/tasks?project_id=${encodeURIComponent(projectId)}`, { cache: "no-store" });
      if (!response.ok) { setNotice("任务列表读取失败"); return; }
      const data = await response.json() as { tasks?: QueueTask[] }; setQueueTasks(data.tasks || []);
    } else setNotice("公司任务队列会在接入自建服务器后显示");
  };

  const copyAssetToCanvas = (asset: LibraryAsset) => {
    if (!canWrite) { setNotice("当前项目是只读状态，不能把资产复制到画布"); return; }
    const spec = asset.kind === "video" ? NODE_SPEC_BY_KEY.video : asset.kind === "audio" ? NODE_SPEC_BY_KEY.audio : NODE_SPEC_BY_KEY.image_asset;
    const id = `library-${asset.kind}-${crypto.randomUUID()}`;
    setNodes((current) => [...current, { id, type: "studio", position: { x: 330, y: 240 }, data: { title: asset.name.replace(/\.[^.]+$/, ""), description: "从资产库复制到画布；后续修改不会反向改变资产库副本", kind: spec.kind, specKey: spec.key, desktopType: spec.desktopType, status: "资产副本", meta: asset.content_type || asset.contentType || asset.kind, accent: spec.accent, desktopPayload: { ...spec.defaults, asset_id: asset.id, size: asset.size } } }]);
    setSelectedId(id); setSideView("canvas"); setNotice("资产副本已写入画布节点");
  };

  async function mergeServerResult(nodeId: string) {
    if (!projectId) return;
    const response = await apiFetch(`/api/projects/${projectId}`, { cache: "no-store" });
    if (!response.ok) return;
    const detail = await response.json() as { project?: { title: string; version: number; canvas: { nodes: StudioNode[]; edges: Edge[] } } };
    if (!detail.project) return;
    const serverNodes = detail.project.canvas.nodes.map(normalizeStudioNode);
    const serverEdges = detail.project.canvas.edges.map((edge) => ({ ...edge, type: "pulse" }));
    const serverSource = serverNodes.find((node) => node.id === nodeId);
    const base = lastSyncedProjectRef.current, latestNodes = nodesRef.current, latestEdges = edgesRef.current;
    const baseById = new Map(base.nodes.map((node) => [node.id, node])), latestById = new Map(latestNodes.map((node) => [node.id, node]));
    const changedIds = new Set(latestNodes.filter((node) => JSON.stringify(node) !== JSON.stringify(baseById.get(node.id))).map((node) => node.id));
    const deletedIds = new Set(base.nodes.filter((node) => !latestById.has(node.id)).map((node) => node.id));
    const mergedNodes = serverNodes.filter((node) => !deletedIds.has(node.id)).map((serverNode) => {
      const local = latestById.get(serverNode.id);
      if (!local) return serverNode;
      if (serverNode.id === nodeId && serverSource) return {
        ...local,
        data: {
          ...local.data,
          description: serverSource.data.specKey === "copywriting" ? serverSource.data.description : local.data.description,
          status: serverSource.data.status,
          progress: serverSource.data.progress,
          desktopPayload: { ...(local.data.desktopPayload || {}), ...(serverSource.data.desktopPayload || {}) },
        },
      };
      return changedIds.has(serverNode.id) ? local : serverNode;
    });
    const serverIds = new Set(serverNodes.map((node) => node.id));
    mergedNodes.push(...latestNodes.filter((node) => !serverIds.has(node.id) && !baseById.has(node.id)));
    const edgesChanged = JSON.stringify(latestEdges) !== JSON.stringify(base.edges);
    const mergedEdges = edgesChanged ? [...latestEdges, ...serverEdges.filter((edge) => !latestEdges.some((current) => current.id === edge.id))] : serverEdges;
    const latestTitle = projectTitleRef.current;
    const titleChanged = latestTitle !== base.title;
    const hasLocalChanges = changedIds.size > 0 || deletedIds.size > 0 || edgesChanged || titleChanged;
    lastSyncedProjectRef.current = { title: detail.project.title, nodes: serverNodes, edges: serverEdges };
    if (!hasLocalChanges) suppressNextSaveRef.current = true;
    versionRef.current = detail.project.version; setProjectTitle(titleChanged ? latestTitle : detail.project.title);
    setNodes(mergedNodes); setEdges(mergedEdges); setProjectConflict(false);
    setProjects((current) => current.map((project) => project.id === projectId ? { ...project, title: titleChanged ? latestTitle : detail.project!.title, version: detail.project!.version } : project));
  }

  async function pollTask(taskId: string, nodeId: string) {
    for (let attempt = 0; attempt < 1800; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
      try {
        const response = await apiFetch(`/api/tasks/${taskId}`, { cache: "no-store" });
        if (!response.ok) return;
        const data = await response.json() as { task: { status: string; progress: number; output?: { data?: unknown; asset_ids?: string[]; analysis?: unknown }; error_message?: string } };
        setNodes((current) => current.map((node) => {
          if (node.id !== nodeId) return node;
          const output = data.task.output || {};
          return { ...node, data: { ...node.data, status: data.task.status === "completed" ? "生成完成" : data.task.status === "failed" ? "生成失败" : `AI 制片中 · ${data.task.progress}%`, progress: data.task.progress, desktopPayload: { ...(node.data.desktopPayload || {}), ...(output.asset_ids ? { output_asset_ids: output.asset_ids } : {}), ...(output.analysis ? { analysis_result: output.analysis } : {}) } } };
        }));
        if (data.task.status === "completed") { await new Promise((resolve) => window.setTimeout(resolve, 200)); await mergeServerResult(nodeId); setNotice("生成完成，结果已写回画布节点"); return; }
        if (["failed", "cancelled"].includes(data.task.status)) { await mergeServerResult(nodeId); setNotice(data.task.error_message || "任务已停止"); return; }
      } catch { return; }
    }
  }

  async function pollProduction(runId: string, nodeId: string) {
    for (let attempt = 0; attempt < 3600; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
      const response = await apiFetch(`/api/production-runs/${runId}`, { cache: "no-store" });
      if (!response.ok) return;
      const data = await response.json() as { run: { status: string; stage: number; stage_name: string; completed_stage: number; active_task_id?: string; error_message?: string } };
      setNodes((current) => current.map((node) => node.id === nodeId ? { ...node, data: { ...node.data, status: data.run.status === "waiting_review" ? `等待确认 · ${data.run.stage_name}` : data.run.status === "complete" ? "全流程完成" : data.run.status === "paused" ? "流程已暂停" : data.run.status === "failed" ? "阶段失败" : `AI 制片中 · ${data.run.stage}/7`, progress: Math.round(data.run.completed_stage / 7 * 100), desktopPayload: { ...(node.data.desktopPayload || {}), production_run_id: runId, pipeline_stage: data.run.stage, production_completed_stage: data.run.completed_stage, production_status: data.run.status } } } : node));
      if (["waiting_review", "complete", "paused", "failed"].includes(data.run.status)) { await mergeServerResult(nodeId); setNotice(data.run.error_message || (data.run.status === "waiting_review" ? "到达确认节点：审片通过或接受风险后才会继续" : `制片流程：${data.run.status}`)); return; }
    }
  }

  const productionCommand = async (command: string, targetStage?: number, confirmed = false) => {
    if (!selectedNode || !controlled || !projectId) return;
    if (["approve", "accept_risk"].includes(command) ? !canReview : !canWrite) { setNotice(["approve", "accept_risk"].includes(command) ? "当前账号没有该项目的审片权限" : "当前账号没有该项目的编辑权限"); return; }
    let runId = String(selectedNode.data.desktopPayload?.production_run_id || "");
    if (!runId) {
      const boardPayload = selectedNode.data.desktopPayload || {};
      const planning = String(boardPayload.planning_provider || ""), image = String(boardPayload.image_provider || ""), video = String(boardPayload.video_provider || "");
      const planningModel = String(boardPayload.planning_model || providers.find((item) => item.name === planning)?.profile?.model || "");
      const imageModel = String(boardPayload.image_model || providers.find((item) => item.name === image)?.profile?.model || "");
      const videoModel = String(boardPayload.video_model || providers.find((item) => item.name === video)?.profile?.model || "");
      if (!planning || !image || !video || !planningModel || !imageModel || !videoModel) { setNotice("开始制片前请明确锁定拆镜、图片、视频的引擎和模型版本；系统不会静默切换"); return; }
      const createPayload = { project_id: projectId, node_id: selectedNode.id, automation_mode: boardPayload.automation_mode || "checkpoints", provider_locks: { planning, planning_model: planningModel, image, image_model: imageModel, video, video_model: videoModel } };
      if (!confirmed) {
        const quoteResponse = await apiFetch("/api/production-runs/quote", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(createPayload) });
        const quoteData = await quoteResponse.json() as { quote?: { credits: number; tasks: number }; detail?: string };
        if (!quoteResponse.ok || !quoteData.quote) { setNotice(quoteData.detail || "七阶段制片额度校验失败"); return; }
        setPendingTask({ action: `启动七阶段 AI 制片（最多 ${quoteData.quote.tasks} 个任务）`, model: `拆镜 ${planning}:${planningModel} · 图片 ${image}:${imageModel} · 视频 ${video}:${videoModel}`, credits: quoteData.quote.credits, run: async () => { setPendingTask(null); await productionCommand(command, targetStage, true); } });
        return;
      }
      const created = await apiFetch("/api/production-runs", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(createPayload) });
      const data = await created.json() as { run?: { id: string }; detail?: string };
      if (!created.ok || !data.run) { setNotice(data.detail || "制片流程创建失败"); return; }
      runId = data.run.id; updatePayload("production_run_id", runId);
    }
    const response = await apiFetch(`/api/production-runs/${runId}/command`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ command, target_stage: targetStage }) });
    const data = await response.json() as { run?: { status: string; stage: number; stage_name: string }; detail?: string };
    if (!response.ok || !data.run) { setNotice(data.detail || "流程操作失败"); return; }
    setNotice(`${data.run.stage}/7 · ${data.run.stage_name} · ${data.run.status}`); void pollProduction(runId, selectedNode.id);
  };

  const submitSelected = async () => {
    if (!selectedNode || !projectId) { setNotice("项目尚未同步完成，请稍后再试"); return; }
    if (!controlled) { setNotice("当前是私有预览；连接公司服务器后即可提交真实生成任务"); return; }
    if (!canWrite) { setNotice("当前项目是只读状态，不能提交生成任务"); return; }
    if (!await saveCurrentProjectNow()) return;
    if (selectedNode.data.specKey === "storyboard") {
      const boardPayload = selectedNode.data.desktopPayload || {};
      const runId = String(boardPayload.production_run_id || "");
      const status = String(boardPayload.production_status || "ready");
      if (status === "complete") { setNotice("该故事板已经完成；需要重新生成时请使用程序坞的“重做”"); return; }
      const command = !runId ? "start" : status === "waiting_review" ? "approve" : status === "paused" ? "resume" : "continue";
      await productionCommand(command);
      return;
    }
    const payload = selectedNode.data.desktopPayload || {};
    const nodeModel = lockedModel(payload, providers);
    const specKey = selectedNode.data.specKey;
    const selectedAction = String(payload.editor_action || NODE_SPEC_BY_KEY[specKey]?.actions[0] || "生成");
    if (specKey === "audio" && selectedAction === "音效") {
      setNotice("当前已接入的是配音引擎，不会把文字伪装成音效；请使用带原生声音的视频模型，或由管理员接入独立音效模型"); return;
    }
    const directorTimeline = Array.isArray(payload.timeline_images) ? payload.timeline_images as Array<Record<string, unknown>> : [];
    const directorDuration = Number(payload.duration || 10), directorStep = incomingNodes.length ? directorDuration / incomingNodes.length : directorDuration;
    const effectiveDirectorTimeline = incomingNodes.map((node, index) => ({ source_node_id: node.id, start: Number((index * directorStep).toFixed(2)), end: Number(((index + 1) * directorStep).toFixed(2)), purpose: "continuity", action: "推进一个清晰动作", camera: "", ...(directorTimeline.find((item) => item.source_node_id === node.id) || {}) }));
    const hasDirectorFrame = effectiveDirectorTimeline.some((item) => ["first_frame", "last_frame"].includes(String(item.purpose || "")));
    if (specKey === "skill") {
      const action = String(payload.editor_action || "");
      if (action === "故事板") {
        const target = NODE_SPEC_BY_KEY.storyboard, id = `storyboard-${crypto.randomUUID()}`;
        setNodes((current) => [...current, { id, type: "studio", position: { x: selectedNode.position.x + 380, y: selectedNode.position.y }, data: { title: "故事板生成器", description: selectedNode.data.description, kind: target.kind, specKey: target.key, desktopType: target.desktopType, status: "等待设置", meta: "专业 Skill 输出", accent: target.accent, desktopPayload: { ...target.defaults } } }]);
        setEdges((current) => addEdge({ id: `skill-${selectedNode.id}-${id}`, source: selectedNode.id, target: id, type: "pulse" }, current)); setSelectedId(id); setNotice("已创建故事板节点，请锁定三个引擎后开始制片"); return;
      }
      const prompts = buildSkillPrompts(action, selectedNode.data.description);
      if (prompts.length) {
        const groupId = `workflow-${crypto.randomUUID()}`, imageSpec = NODE_SPEC_BY_KEY.multi_image;
        const children = prompts.map((prompt, index) => ({ id: `skill-image-${crypto.randomUUID()}`, type: "studio" as const, position: { x: selectedNode.position.x + 420 + (index % 5) * 330, y: selectedNode.position.y + Math.floor(index / 5) * 225 }, data: { title: `${action} ${String(index + 1).padStart(2, "0")}`, description: prompt, kind: imageSpec.kind, specKey: imageSpec.key, desktopType: imageSpec.desktopType, status: "待检查并执行", meta: "专业 Skill 生成节点", accent: imageSpec.accent, desktopPayload: { ...imageSpec.defaults, provider_name: "", model: "", skill_source: selectedNode.id } } }));
        const workflow = NODE_SPEC_BY_KEY.workflow;
        setNodes((current) => [...current, { id: groupId, type: "studio", position: { x: selectedNode.position.x, y: selectedNode.position.y + 250 }, data: { title: action, description: `可复用专业 Skill · ${children.length} 个生成节点`, kind: workflow.kind, specKey: workflow.key, desktopType: workflow.desktopType, status: "已展开", meta: `${children.length} 个节点`, accent: workflow.accent, desktopPayload: { group_nodes: children.map((item) => item.id) } } }, ...children]);
        setEdges((current) => [...current, ...children.flatMap((child) => [{ id: `skill-${selectedNode.id}-${child.id}`, source: selectedNode.id, target: child.id, type: "pulse" }, { id: `group-${groupId}-${child.id}`, source: groupId, target: child.id, type: "pulse" }])]);
        setSelectedId(groupId); setNotice(`已展开 ${children.length} 个生成节点；先检查提示词，再逐项或批量执行，避免误扣费`); return;
      }
    }
    const mapping: Record<string, { provider: string; operation: string; model: string }> = {
      storyboard: { provider: String(payload.provider_name || ""), operation: "chat", model: nodeModel || String(payload.planning_model || "") },
      script: { provider: String(payload.provider_name || ""), operation: "chat", model: nodeModel },
      copywriting: { provider: String(payload.provider_name || ""), operation: "chat", model: nodeModel },
      multi_image: { provider: String(payload.provider_name || ""), operation: incomingNodes.length ? "image_edit" : "text_to_image", model: nodeModel },
      scene_reference: { provider: String(payload.provider_name || ""), operation: selectedAction === "图生图" ? "image_edit" : "text_to_image", model: nodeModel },
      character_reference: { provider: String(payload.provider_name || ""), operation: selectedAction === "图生图" ? "image_edit" : "text_to_image", model: nodeModel },
      element_reference: { provider: String(payload.provider_name || ""), operation: selectedAction === "图生图" ? "image_edit" : "text_to_image", model: nodeModel },
      multi_director: { provider: selectedAction === "提取首中尾帧" ? "local" : String(payload.provider_name || ""), operation: selectedAction === "基于尾帧续拍" ? "continue_video" : selectedAction === "提取首中尾帧" ? "extract_video_frames" : hasDirectorFrame ? "image_to_video" : "text_to_video", model: nodeModel },
      video: { provider: selectedAction === "提取首中尾帧" ? "local" : String(payload.provider_name || ""), operation: selectedAction === "基于尾帧续拍" ? "continue_video" : selectedAction === "提取首中尾帧" ? "extract_video_frames" : selectedAction === "图生视频" ? "image_to_video" : "text_to_video", model: nodeModel },
      audio: { provider: String(payload.provider_name || ""), operation: "text_to_speech", model: nodeModel },
      analysis: { provider: "local", operation: "video_breakdown", model: "local" },
      skill: { provider: String(payload.provider_name || ""), operation: "chat", model: nodeModel },
      shot: { provider: String(payload.provider_name || ""), operation: selectedAction === "生成视频" ? (incomingNodes.length ? "image_to_video" : "text_to_video") : selectedAction === "生成对白" ? "text_to_speech" : selectedAction === "参考图再生成" ? "image_edit" : incomingNodes.length ? "image_edit" : "text_to_image", model: nodeModel },
    };
    const task = mapping[specKey];
    if (!task) { setNotice("该运行节点由上游工作流自动驱动，不能单独提交"); return; }
    if (task.provider !== "local" && !task.provider) { setNotice("请先在节点设置中明确选择生成引擎；系统不会替你静默切换模型"); return; }
    const ownAssetIds = [payload.asset_id, ...(Array.isArray(payload.output_asset_ids) ? payload.output_asset_ids : [])].filter(Boolean).map(String);
    const usableIncoming = incomingNodes.filter((node) => node.data.desktopPayload?.asset_id || (Array.isArray(node.data.desktopPayload?.output_asset_ids) && node.data.desktopPayload.output_asset_ids.length));
    if (specKey === "multi_image" && payload.beginner_mode === "image_edit" && !usableIncoming.length) { setNotice("“图片生成图片”需要先连接至少一张已上传或已生成的图片"); return; }
    if (task.operation === "image_edit" && !usableIncoming.length) { setNotice("图生图需要先连接至少一张已上传或已生成的图片"); return; }
    if (task.operation === "image_to_video" && !usableIncoming.length) { setNotice("图生视频必须连接一个已有媒体的图片节点，并明确首帧职责"); return; }
    if (specKey === "video" && task.operation === "image_to_video") {
      const settings = Array.isArray(payload.reference_settings) ? payload.reference_settings as Array<Record<string, unknown>> : [];
      const roles = incomingNodes.map((node) => { const configured = settings.find((item) => item.source_node_id === node.id); const relation = String((edges.find((edge) => edge.source === node.id && edge.target === selectedNode.id)?.data as Record<string, unknown> | undefined)?.relation || ""); const assetId = node.data.desktopPayload?.asset_id || (Array.isArray(node.data.desktopPayload?.output_asset_ids) ? node.data.desktopPayload.output_asset_ids[0] : ""); return String(configured?.purpose || (relation === "first_frame" ? "first_frame" : relation === "last_frame" || String(assetId || "") === String(payload.last_frame_asset_id || "") ? "last_frame" : "reference")); });
      const firstCount = roles.filter((role) => role === "first_frame").length, lastCount = roles.filter((role) => role === "last_frame").length;
      if (firstCount !== 1) { setNotice(firstCount ? "图生视频只能指定一个首帧" : "图生视频需要把一张输入图片明确设为“视频首帧”"); return; }
      if (lastCount > 1) { setNotice("图生视频只能指定一个尾帧"); return; }
      if (payload.beginner_mode === "first_last_frame_video" && lastCount !== 1) { setNotice("“首尾帧生成视频”还需要连接第二张图片作为视频尾帧"); return; }
    }
    if (["continue_video", "extract_video_frames"].includes(task.operation) && !ownAssetIds.length) { setNotice(task.operation === "continue_video" ? "当前视频节点还没有可用成片，无法取得尾帧续拍" : "当前视频节点还没有可用成片，无法抽帧"); return; }
    if (task.operation === "video_breakdown" && !usableIncoming.length) { setNotice("请先把一个已上传或已生成的视频节点连接到 AI 拉片节点"); return; }
    if (specKey === "shot" && selectedAction === "参考图再生成" && !usableIncoming.length) { setNotice("参考图再生成必须先连接一张已有媒体的图片节点"); return; }
    if (specKey === "video" && payload.last_frame_asset_id) {
      const firstAvailable = incomingNodes.some((node) => String(node.data.desktopPayload?.asset_id || "") !== String(payload.last_frame_asset_id));
      if (!firstAvailable) { setNotice("尾帧不能单独生成视频，请再连接一张图片作为首帧"); return; }
    }
    if (specKey === "multi_director" && incomingNodes.length) {
      const rows = effectiveDirectorTimeline.map((item) => ({ start: Number(item.start), end: Number(item.end) })).sort((left, right) => left.start - right.start);
      if (rows.some((item) => !Number.isFinite(item.start) || !Number.isFinite(item.end) || item.start < 0 || item.end <= item.start)) { setNotice("导演时间轴存在无效时间段：结束秒必须大于开始秒"); return; }
      if (rows.some((item, index) => index > 0 && item.start < rows[index - 1].end)) { setNotice("导演时间轴存在重叠，请调整每张图的开始与结束秒数"); return; }
      if (rows.some((item) => item.end > Number(payload.duration || 10))) { setNotice("导演时间轴超出视频总时长，请先调整节点时长或时间段"); return; }
      if (effectiveDirectorTimeline.filter((item) => item.purpose === "first_frame").length > 1 || effectiveDirectorTimeline.filter((item) => item.purpose === "last_frame").length > 1) { setNotice("首帧和尾帧各只能指定一张图片"); return; }
      if (effectiveDirectorTimeline.some((item) => item.purpose === "last_frame") && !effectiveDirectorTimeline.some((item) => item.purpose === "first_frame")) { setNotice("多图导演指定尾帧时也必须指定一个首帧"); return; }
    }
    const providerOperation = task.operation === "continue_video" ? "image_to_video" : task.operation === "extract_video_frames" ? "" : task.operation;
    if (providerOperation && task.provider !== "local" && providers.length && !providers.some((provider) => provider.name === task.provider && provider.capabilities.includes(providerOperation))) { setNotice(`“${task.provider}”当前不可用或不支持 ${providerOperation}，请在节点中明确选择可用模型`); return; }
    const selectedProvider = providers.find((provider) => provider.name === task.provider);
    const referenceLimit = Number(selectedProvider?.profile?.reference_assets || 0);
    if (referenceLimit && incomingNodes.length > referenceLimit) { setNotice(`${task.provider} 单次最多支持 ${referenceLimit} 张参考图；当前连接 ${incomingNodes.length} 张。请减少输入或拆成连续段落，系统不会静默丢图`); return; }
    const action = selectedAction;
    const quoteResponse = await apiFetch(`/api/tasks/quote?kind=${encodeURIComponent(task.operation)}&provider=${encodeURIComponent(task.provider)}&model=${encodeURIComponent(task.model)}`, { cache: "no-store" });
    const quoteData = await quoteResponse.json() as { quote?: { credits: number }; detail?: string };
    if (!quoteResponse.ok || !quoteData.quote) { setNotice(quoteData.detail || "当前账号没有该任务的可用额度"); return; }
    const run = async () => {
      setPendingTask(null);
      setNodes((current) => current.map((node) => node.id === selectedNode.id ? { ...node, data: { ...node.data, status: "正在排队", progress: 0 } } : node));
      const ownAssets = ownAssetIds.map((assetId) => ({ node_id: selectedNode.id, title: selectedNode.data.title, asset_id: assetId }));
      const referenceSettings = Array.isArray(payload.reference_settings) ? payload.reference_settings as Array<Record<string, unknown>> : [];
      const timelineSettings = effectiveDirectorTimeline;
      let references = incomingNodes.map((node) => {
        const settings = (specKey === "multi_director" ? timelineSettings : referenceSettings).find((item) => item.source_node_id === node.id) || {};
        const relation = String((edges.find((edge) => edge.source === node.id && edge.target === selectedNode.id)?.data as Record<string, unknown> | undefined)?.relation || "");
        const assetId = node.data.desktopPayload?.asset_id || (Array.isArray(node.data.desktopPayload?.output_asset_ids) ? node.data.desktopPayload.output_asset_ids[0] : undefined);
        const inferredRole = relation === "first_frame" ? "first_frame" : relation === "last_frame" || String(assetId || "") === String(payload.last_frame_asset_id || "") ? "last_frame" : "reference";
        return { node_id: node.id, title: node.data.title, asset_id: assetId, role: String(settings.purpose || inferredRole), instruction: settings.instruction || settings.action, camera: settings.camera };
      });
      if (specKey === "video" && payload.last_frame_asset_id) references = [...references.filter((item) => String(item.asset_id || "") !== String(payload.last_frame_asset_id)), ...references.filter((item) => String(item.asset_id || "") === String(payload.last_frame_asset_id))];
      if (["continue_video", "extract_video_frames"].includes(task.operation)) references.unshift(...ownAssets);
      const taskParams = specKey === "multi_director" ? { ...payload, timeline_images: effectiveDirectorTimeline } : payload;
      const prompt = specKey === "shot" ? compileShotPrompt(selectedNode) : selectedNode.data.description;
      const response = await apiFetch("/api/tasks", { method: "POST", headers: { "content-type": "application/json", "idempotency-key": crypto.randomUUID() }, body: JSON.stringify({ project_id: projectId, node_id: selectedNode.id, kind: task.operation, provider: task.provider, model: task.model, estimated_credits: quoteData.quote!.credits, input: { inputs: { prompt, references }, params: taskParams, action, use_cache: false } }) });
      const data = await response.json() as { task?: { id: string }; detail?: string };
      if (!response.ok || !data.task) { setNotice(data.detail || "任务提交失败"); return; }
      setNotice("任务已进入公司队列"); void pollTask(data.task.id, selectedNode.id);
    };
    setPendingTask({ action, model: task.model || task.provider, credits: quoteData.quote.credits, run });
  };

  const importDesktopProject = async (file: File) => {
    if (!canWrite) { setNotice("当前项目是只读状态，不能导入工程"); return; }
    try {
      const imported = desktopProjectToWeb(JSON.parse(await file.text()));
      setNodes((imported.canvas.nodes as StudioNode[]).map(normalizeStudioNode));
      setEdges((imported.canvas.edges as Edge[]).map((edge) => ({ ...edge, type: "pulse" })));
      setProjectTitle(imported.title);
      setSelectedId(String(imported.canvas.nodes[0]?.id || ""));
      setNotice("桌面工程已导入 · 正在保存");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "工程导入失败");
    }
  };

  const importMedia = async (files: File[], dropPosition?: { x: number; y: number }) => {
    if (!canWrite) { setNotice("当前项目是只读状态，不能导入素材"); return; }
    if (!projectId) { setNotice("项目尚未同步完成，请稍后导入"); return; }
    let imported = 0;
    let unsupported = 0;
    let firstImportedId = "";
    const existing = nodesRef.current;
    const importStartY = existing.length ? Math.max(...existing.map((node) => node.position.y)) + 330 : 120;
    for (const [fileIndex, file] of files.entries()) {
      const extension = file.name.split(".").pop()?.toLowerCase() || "";
      const mediaKind = file.type.startsWith("video/") || ["mp4", "mov", "webm", "mkv", "avi", "m4v"].includes(extension) ? "video" : file.type.startsWith("audio/") || ["mp3", "wav", "m4a", "aac", "flac", "ogg"].includes(extension) ? "audio" : file.type.startsWith("image/") || ["png", "jpg", "jpeg", "webp", "gif", "avif", "bmp"].includes(extension) ? "image" : "";
      if (!mediaKind) { unsupported += 1; continue; }
      setNotice(`正在导入 ${fileIndex + 1}/${files.length} · ${file.name}`);
      const nodeId = `imported-${mediaKind}-${crypto.randomUUID()}`;
      const form = new FormData();
      form.set("file", file); form.set("projectId", projectId); form.set("project_id", projectId); form.set("nodeId", nodeId); form.set("node_id", nodeId); form.set("kind", mediaKind); form.set("metadata_json", JSON.stringify({ source: "canvas_import" }));
      const response = await apiFetch("/api/assets", { method: "POST", body: form });
      const data = await response.json() as { asset?: { id: string; url?: string }; detail?: string; error?: string };
      if (!response.ok || !data.asset) { setNotice(data.detail || data.error || `${file.name} 导入失败`); continue; }
      const spec = mediaKind === "video" ? NODE_SPEC_BY_KEY.video : mediaKind === "audio" ? NODE_SPEC_BY_KEY.audio : NODE_SPEC_BY_KEY.image_asset;
      const slot = imported;
      const position = dropPosition ? { x: dropPosition.x + (slot % 3) * 340, y: dropPosition.y + Math.floor(slot / 3) * 270 } : { x: 110 + (slot % 4) * 380, y: importStartY + Math.floor(slot / 4) * 300 };
      setNodes((current) => [...current, { id: nodeId, type: "studio", position, data: { title: file.name.replace(/\.[^.]+$/, ""), description: `已导入画布 · ${Math.max(1, Math.round(file.size / 1024))} KB`, kind: spec.kind, specKey: spec.key, desktopType: spec.desktopType, status: "已导入画布", meta: file.type || mediaKind, accent: spec.accent, desktopPayload: { ...spec.defaults, asset_id: data.asset.id, asset_url: data.asset.url || `/api/assets/${data.asset.id}`, content_type: file.type, size: file.size, imported_by_drop: Boolean(dropPosition) } } }]);
      if (!firstImportedId) firstImportedId = nodeId;
      imported += 1;
    }
    if (firstImportedId) setSelectedId(firstImportedId);
    setNotice(imported ? `已导入 ${imported} 个素材节点${unsupported ? ` · 跳过 ${unsupported} 个不支持的文件` : ""} · 未自动保存到资产库` : unsupported ? "仅支持常见图片、视频和音频文件" : "没有可导入的媒体文件");
  };

  const acceptsDraggedFiles = (event: React.DragEvent) => Array.from(event.dataTransfer.types).includes("Files");
  const handleFileDragEnter = (event: React.DragEvent<HTMLDivElement>) => {
    if (!acceptsDraggedFiles(event)) return;
    event.preventDefault(); fileDragDepthRef.current += 1; setFileDragActive(true);
  };
  const handleFileDragOver = (event: React.DragEvent<HTMLDivElement>) => {
    if (!acceptsDraggedFiles(event)) return;
    event.preventDefault(); event.dataTransfer.dropEffect = canWrite ? "copy" : "none";
  };
  const handleFileDragLeave = (event: React.DragEvent<HTMLDivElement>) => {
    if (!acceptsDraggedFiles(event)) return;
    fileDragDepthRef.current = Math.max(0, fileDragDepthRef.current - 1);
    if (!fileDragDepthRef.current) setFileDragActive(false);
  };
  const handleFileDrop = (event: React.DragEvent<HTMLDivElement>) => {
    if (!acceptsDraggedFiles(event)) return;
    event.preventDefault(); event.stopPropagation(); fileDragDepthRef.current = 0; setFileDragActive(false);
    const files = Array.from(event.dataTransfer.files);
    if (!files.length) { setNotice("没有读取到可导入文件"); return; }
    const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });
    void importMedia(files, position);
  };

  const arrangeCanvas = () => {
    if (!canWrite) { setNotice("当前项目是只读状态，不能整理节点"); return; }
    setNodes((current) => current.map((node, index) => ({ ...node, position: { x: 90 + (index % 4) * 350, y: 90 + Math.floor(index / 4) * 245 } })));
    setNotice("节点已按网格整理并保存");
  };

  const commandQueueTask = async (task: QueueTask, operation: "pause" | "resume" | "cancel" | "retry") => {
    if (!canWrite) { setNotice("当前项目是只读状态，不能操作任务"); return; }
    const response = await apiFetch(`/api/tasks/${task.id}/${operation}`, { method: "POST" });
    const data = await response.json() as { task?: { id: string; status: string }; detail?: string };
    if (!response.ok || !data.task) { setNotice(data.detail || "任务操作失败"); return; }
    const labels = { pause: "任务已暂停", resume: "任务已恢复排队", cancel: "任务已取消", retry: "重试任务已进入队列" };
    setNotice(labels[operation]);
    if (["resume", "retry"].includes(operation)) void pollTask(data.task.id, task.node_id);
    void openSideView("tasks");
  };

  return (
    <main className="studio-shell">
      <header className="topbar">
        <div className="brand-mark"><Clapperboard size={18} /></div>
        <div className="project-heading">
          <strong>AI 无限画布</strong><span className="project-separator">/</span>
          <button className="project-name" aria-expanded={projectMenuOpen} onClick={() => setProjectMenuOpen((value) => !value)}>{projectTitle} <ChevronDown size={14} /></button>
          {projectMenuOpen && <div className="project-menu">
            <div className="project-menu-heading"><span>公司工程</span>{canCreateProject && <button onClick={() => void createBlankProject()}><Plus size={13} /> 新建空白工程</button>}</div>
            {projectId && <label className="project-menu-rename"><span>当前工程名称</span><input value={projectTitle} disabled={!canWrite} maxLength={200} onChange={(event) => setProjectTitle(event.target.value)} onBlur={() => { if (!projectTitle.trim()) setProjectTitle("未命名项目"); }} /></label>}
            <div className="project-menu-list">
              {projects.length ? projects.map((project) => <button key={project.id} className={project.id === projectId ? "is-active" : ""} onClick={() => void switchProject(project.id)}><span><strong>{project.title}</strong><small>{project.id === projectId ? "当前工程" : project.updated_at || project.updatedAt ? new Date(project.updated_at || project.updatedAt!).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "可打开"}</small></span>{project.id === projectId && <span className="project-current-dot" />}</button>) : <p>当前账号还没有可访问的工程。</p>}
            </div>
          </div>}
        </div>
        <div className="topbar-center"><span className="live-dot" /><span>{notice}</span>{projectConflict && <button onClick={() => void reloadProject()}>重新载入</button>}</div>
        <div className="topbar-actions"><button className="team-button" onClick={() => { if (controlled && projectId) setTeamOpen(true); else setNotice("连接公司服务器后可管理项目成员"); }}><Users size={15} /> {user.role === "admin" ? "管理员" : "制片组"}</button><button className="icon-button" aria-label={user.role === "admin" ? "账号与权限" : "设置"} onClick={() => { if (user.role === "admin" && controlled) setAdminOpen(true); }}><Settings2 size={17} /></button><button className="avatar" title={controlled ? `${user.display_name} · 点击退出` : user.display_name} onClick={() => { if (controlled) void signOut(); }}>{(user.display_name || user.username || "制").slice(0, 1)}</button></div>
      </header>

      <section className="workspace">
        <aside className="rail">
          <button className={`rail-button ${sideView === "canvas" ? "is-active" : ""}`} onClick={() => void openSideView("canvas")}><Boxes size={18} /><span>画布</span></button>
          <button className={`rail-button ${sideView === "assets" ? "is-active" : ""}`} onClick={() => void openSideView("assets")}><FolderOpen size={18} /><span>资产</span></button>
          <button className={`rail-button ${sideView === "tasks" ? "is-active" : ""}`} onClick={() => void openSideView("tasks")}><Zap size={18} /><span>任务</span></button>
          <div className="rail-spacer" />
          <button className="rail-button" onClick={() => { if (controlled && projectId) setTeamOpen(true); }}><Menu size={18} /><span>成员</span></button>
        </aside>

        <div className={`canvas-wrap ${fileDragActive ? "is-file-dragging" : ""}`} onDragEnter={handleFileDragEnter} onDragOver={handleFileDragOver} onDragLeave={handleFileDragLeave} onDrop={handleFileDrop}>
          <div className="canvas-context"><span>AI 制片画布</span><span className="context-divider" /><span>{nodes.length} 个节点</span><span>{edges.length} 条连接</span><span className="canvas-shortcut">拖拽框选 · 空格拖动画布</span></div>
          {fileDragActive && <div className="canvas-file-drop" aria-hidden="true"><Upload size={24} /><strong>{canWrite ? "松开即可导入画布" : "当前项目只读，不能导入"}</strong><span>图片、视频和音频会自动生成对应节点</span></div>}
          <ReactFlow<StudioNode, Edge>
            nodes={nodes} edges={edgesVisible ? edges : []} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
            onNodesChange={canWrite ? onNodesChange : undefined} onEdgesChange={canWrite ? onEdgesChange : undefined} onConnect={onConnect}
            onNodeClick={(_, node) => { setSelectedId(node.id); setContextMenu(null); }}
            onNodeContextMenu={(event, node) => { event.preventDefault(); setSelectedId(node.id); setContextMenu({ x: Math.min(event.clientX, window.innerWidth - 224), y: Math.min(event.clientY, window.innerHeight - 260), nodeId: node.id }); }}
            onPaneContextMenu={(event) => { event.preventDefault(); setContextMenu({ x: Math.min(event.clientX, window.innerWidth - 224), y: Math.min(event.clientY, window.innerHeight - 150) }); }}
            onSelectionChange={({ nodes: selected }) => setSelectedId(selected.length === 1 ? selected[0].id : "")}
            onNodesDelete={(deleted) => { if (deleted.some((node) => node.id === selectedId)) setSelectedId(""); setNotice(`已删除 ${deleted.length} 个节点`); }}
            onPaneClick={() => { setSelectedId(""); setCreateOpen(false); setProjectMenuOpen(false); setContextMenu(null); }}
            fitView fitViewOptions={{ padding: 0.18 }} minZoom={0.18} maxZoom={1.8}
            nodesDraggable={canWrite} nodesConnectable={canWrite} edgesReconnectable={canWrite}
            selectionOnDrag={canWrite} selectionMode={SelectionMode.Partial} panOnDrag={[1, 2]} panActivationKeyCode="Space" multiSelectionKeyCode="Shift"
            proOptions={{ hideAttribution: true }} deleteKeyCode={canWrite ? "Delete" : null}>
            <Background color="#444444" gap={30} size={1} variant={BackgroundVariant.Dots} />
            <Controls position="bottom-left" showInteractive={false} />
            {mapVisible && <MiniMap position="bottom-left" nodeColor={(node) => (node.data as StudioData).accent} maskColor="rgba(8, 10, 14, .72)" pannable zoomable style={{ width: 164, height: 102 }} />}
          </ReactFlow>
          <div className="canvas-view-toolbar" aria-label="画布显示控制"><button className={mapVisible ? "is-active" : ""} onClick={() => setMapVisible((value) => !value)} title={mapVisible ? "隐藏小地图" : "显示小地图"}><MapIcon size={16} /><span>{mapVisible ? "小地图" : "地图已隐藏"}</span></button><span className="view-toolbar-divider" /><button className={edgesVisible ? "is-active" : ""} onClick={() => setEdgesVisible((value) => !value)} title={edgesVisible ? "隐藏连线" : "显示连线"}>{edgesVisible ? <Link2 size={16} /> : <Link2Off size={16} />}<span>{edgesVisible ? "连线" : "连线已隐藏"}</span></button></div>
          {contextMenu && <div className="canvas-context-menu" style={{ left: contextMenu.x, top: contextMenu.y }} onContextMenu={(event) => event.preventDefault()}>
            {contextMenu.nodeId ? <>{(() => { const node = nodes.find((item) => item.id === contextMenu.nodeId); if (!node) return null; const hasMedia = Boolean(node.data.desktopPayload?.asset_id || (Array.isArray(node.data.desktopPayload?.output_asset_ids) && node.data.desktopPayload.output_asset_ids.length)); return <><button disabled={!hasMedia} onClick={() => { setContextMenu(null); void saveContextNodeToLibrary(node.id); }}><PackagePlus size={15} />保存到我的资产</button><button onClick={() => { void navigator.clipboard.writeText(node.data.description); setContextMenu(null); setNotice("节点文字已复制"); }}><ClipboardCopy size={15} />复制文字</button><button onClick={() => { duplicateNode(node.id); setContextMenu(null); }}><Copy size={15} />复制节点</button><span className="context-menu-divider" /><button className="is-danger" onClick={() => { setSelectedId(""); setNodes((current) => current.filter((item) => item.id !== node.id)); setEdges((current) => current.filter((edge) => edge.source !== node.id && edge.target !== node.id)); setContextMenu(null); }}><Trash2 size={15} />删除</button></>; })()}</> : <><button onClick={() => { arrangeCanvas(); setContextMenu(null); }}><LayoutGrid size={15} />优化工作流布局</button><button onClick={() => { importInputRef.current?.click(); setContextMenu(null); }}><Import size={15} />导入素材</button></>}
          </div>}
          {selectedNodeIds.length > 0 && <div className="selection-toolbar"><span>已选择 {selectedNodeIds.length} 个节点</span>{canWrite && <button onClick={deleteSelectedNodes}><Trash2 size={15} /> 删除</button>}</div>}
          {sideView !== "canvas" && <aside className="canvas-side-panel"><header><div><strong>{sideView === "assets" ? "资产库副本" : "公司任务队列"}</strong><span>{sideView === "assets" ? "只有主动保存的媒体才进入这里" : "查看生成进度、失败原因并阻止重复提交"}</span></div><button onClick={() => setSideView("canvas")}><X size={16} /></button></header>{sideView === "assets" ? <div className="side-list">{libraryAssets.length ? libraryAssets.map((asset) => <article key={asset.id}><span className="side-kind">{asset.kind}</span><div><strong>{asset.name}</strong><small>{Math.max(1, Math.round(asset.size / 1024))} KB</small></div><button onClick={() => copyAssetToCanvas(asset)}>复制到画布</button></article>) : <p>还没有保存到资产库的媒体。</p>}</div> : <div className="side-list">{queueTasks.length ? queueTasks.map((task) => <article key={task.id}><span className={`task-state state-${task.status}`}>{task.progress}%</span><div><strong>{task.kind} · {task.provider || "local"}</strong><small>{task.status === "workflow_waiting" ? "工作流中等待前序节点" : task.status}{task.error_message ? ` · ${task.error_message}` : ""}{task.managed_by !== "task" && ["failed", "cancelled"].includes(task.status) ? " · 请在所属流程节点恢复" : ""}</small></div><span className="task-actions">{task.managed_by === "task" && task.status === "queued" && <button onClick={() => void commandQueueTask(task, "pause")}>暂停</button>}{task.managed_by === "task" && task.status === "paused" && <button onClick={() => void commandQueueTask(task, "resume")}>继续</button>}{task.managed_by === "task" && ["queued", "running", "paused"].includes(task.status) && <button onClick={() => void commandQueueTask(task, "cancel")}>取消</button>}{task.managed_by === "task" && ["failed", "cancelled"].includes(task.status) && <button onClick={() => void commandQueueTask(task, "retry")}>重试</button>}</span></article>) : <p>当前项目还没有任务。</p>}</div>}</aside>}

          <div className="dock-wrap">
            <nav className="creation-dock" aria-label="画布程序坞">
              <button className="dock-primary" onClick={() => setCreateOpen((value) => !value)}><Plus size={17} /> 新建</button>
              <button onClick={() => { if (selectedNode?.data.specKey === "storyboard") void submitSelected(); else { const story = nodes.find((node) => node.data.specKey === "storyboard"); if (story) { setSelectedId(story.id); setNotice("已定位 AI 故事板，请锁定三个引擎后开始制片"); } else setNotice("请先新建 AI 故事板节点"); } }}><CirclePlay size={16} /> 开始制片</button><button onClick={() => { const story = selectedNode?.data.specKey === "storyboard" ? selectedNode : nodes.find((node) => node.data.specKey === "storyboard"); if (!story) { setNotice("请先新建 AI 故事板节点"); return; } const payload = story.data.desktopPayload || {}; if (!payload.production_run_id) { setSelectedId(story.id); setNotice("这个故事板还没有开始制片，不需要重做"); return; } if (payload.production_status === "running") { setSelectedId(story.id); setNotice("当前阶段正在生成，完成后再重做可避免重复扣费"); return; } setSelectedId(story.id); setRedoStage(Math.max(1, Math.min(7, Number(payload.pipeline_stage || payload.production_completed_stage || 1)))); }}><Redo2 size={16} /> 重做</button>
              <button onClick={() => projectInputRef.current?.click()}><FolderOpen size={16} /> 工程</button><button onClick={() => importInputRef.current?.click()}><Import size={16} /> 导入</button>
              <button onClick={arrangeCanvas}><LayoutGrid size={16} /> 整理</button><button onClick={() => void openSideView("assets")}><Boxes size={16} /> 资产</button>
            </nav>
            <input ref={projectInputRef} className="hidden-file-input" type="file" accept=".cepstudio,.json,application/json" onChange={(event) => { const file = event.target.files?.[0]; if (file) void importDesktopProject(file); event.currentTarget.value = ""; }} />
            <input ref={importInputRef} className="hidden-file-input" type="file" multiple accept="image/*,video/*,audio/*" onChange={(event) => { const files = [...(event.target.files || [])]; if (files.length) void importMedia(files); event.currentTarget.value = ""; }} />
            {createOpen && <div className="create-popover">
              <div className="popover-heading"><div><strong>你想做什么？</strong><span>选择结果，不需要先理解节点术语</span></div><button className="icon-button" onClick={() => setCreateOpen(false)}><X size={16} /></button></div>
              <div className="creation-list">{workflowTemplates.length > 0 && <section className="creation-group workflow-template-group"><h4>我的工作流模板</h4>{workflowTemplates.map((template) => <button key={template.id} onClick={() => instantiateWorkflowTemplate(template)}><span className="creation-icon template-icon"><Workflow size={17} /></span><span><strong>{template.name}</strong><small>恢复整组节点与内部连线</small></span></button>)}</section>}{CREATION_GROUPS.map((group) => <section key={group.key} className="creation-group"><h4>{group.label}</h4>{creationItems.filter((item) => item.group === group.key).map((item) => { const Icon = kindIcons[item.kind]; return <button key={item.key} onClick={() => addNode(item, crypto.randomUUID())}><span className="creation-icon" style={{ color: item.accent }}><Icon size={17} /></span><span><strong>{item.title}</strong><small>{item.description}</small><em>{item.flow}</em></span></button>; })}</section>)}</div>
            </div>}
          </div>
        </div>

        <NodeEditorPortal nodeId={selectedNode?.id || ""}>
        <section className="node-inline-editor nodrag nowheel nopan" style={{ "--node-accent": selectedNode?.data.accent || "#9b8cff" } as React.CSSProperties}>
          <div className="inspector-heading"><div><span>NODE WORKSPACE · 节点工作台</span><strong>{selectedNode?.data.title}</strong></div><button className="icon-button" aria-label="节点工作台菜单"><MoreHorizontal size={18} /></button></div>
          {selectedNode && <div className={`inspector-body ${!canWrite ? "is-readonly" : ""}`}>
            {!canWrite && <p className="readonly-banner">当前权限：{canReview ? "仅审片" : "只读"}。生成、连线和修改均由服务器禁止。</p>}
            {beginnerMode && <details className="beginner-node-guide compact-corner"><summary title="查看使用步骤"><ListTodo size={14} /><span>步骤</span><small>{String(selectedNode.data.desktopPayload?.beginner_flow || "按步骤完成")}</small></summary><div className="beginner-guide-body"><p>{String(selectedNode.data.desktopPayload?.beginner_description || "按照下面三步即可完成")}</p><ol>{currentBeginnerSteps.map((step, index) => <li key={step} className={index === 0 && incomingNodes.length === 0 && ["image_edit", "first_frame_video", "first_last_frame_video", "multi_image_director", "break_down_video"].includes(beginnerMode) ? "is-waiting" : ""}><span>{index + 1}</span>{step}</li>)}</ol></div></details>}
            <label><span>创作要求</span><textarea placeholder={BEGINNER_PLACEHOLDERS[beginnerMode] || "写下希望 AI 完成的内容"} value={selectedNode.data.description} onChange={(event) => updateSelected("description", event.target.value)} rows={4} /></label>
            {selectedNode.data.specKey === "multi_image" && <div className="multi-image-compact">
              <details className="quick-reference-menu"><summary><Plus size={14} />参考{incomingNodes.length ? ` · ${incomingNodes.length}` : ""}</summary><div className="quick-popover reference-popover">{incomingNodes.length ? <div className="reference-purpose-editor"><div><strong>参考图用途</strong><span>{incomingNodes.length} 张</span></div>{incomingNodes.map((node) => { const settings = ((selectedNode.data.desktopPayload?.reference_settings as Array<Record<string, unknown>> | undefined) || []).find((item) => item.source_node_id === node.id) || {}; return <div className="purpose-row" key={node.id}><span>{node.data.title}</span><select aria-label={`${node.data.title}用途`} value={String(settings.purpose || "subject")} onChange={(event) => updateReferenceRow("reference_settings", node.id, { purpose: event.target.value })}><option value="subject">主体</option><option value="scene">场景</option><option value="composition">构图</option><option value="element">元素</option><option value="style">风格</option></select><input aria-label={`${node.data.title}补充要求`} placeholder="补充要求（可选）" value={String(settings.instruction || "")} onChange={(event) => updateReferenceRow("reference_settings", node.id, { instruction: event.target.value })} /></div>; })}</div> : <button className="quick-import" onClick={() => importInputRef.current?.click()}><Upload size={15} />导入图片或把图片节点连进来</button>}</div></details>
              <div className="multi-image-command-bar">
                <select aria-label="生成引擎" value={String(selectedNode.data.desktopPayload?.provider_name || "")} onChange={(event) => { const name = event.target.value; updatePayload("provider_name", name); updatePayload("model", String(providers.find((item) => item.name === name)?.profile?.model || "")); }}><option value="">选择图片模型</option>{providers.filter((provider) => provider.capabilities.includes(incomingNodes.length ? "image_edit" : "text_to_image")).map((provider) => <option key={provider.name} value={provider.name}>{provider.name}</option>)}</select>
                <details className="quick-settings-menu"><summary><Settings2 size={14} />{String(selectedNode.data.desktopPayload?.ratio || "16:9")} · {Number(selectedNode.data.desktopPayload?.candidate_count || 1)}张</summary><div className="quick-popover image-settings-popover"><strong>画面比例</strong><div className="ratio-grid">{["1:1","4:3","3:2","16:9","9:16"].map((ratio) => <button key={ratio} className={selectedNode.data.desktopPayload?.ratio === ratio ? "is-selected" : ""} onClick={() => updatePayload("ratio", ratio)}>{ratio}</button>)}</div><strong>生成数量</strong><div className="count-grid">{[1,2,4].map((count) => <button key={count} className={Number(selectedNode.data.desktopPayload?.candidate_count || 1) === count ? "is-selected" : ""} onClick={() => updatePayload("candidate_count", count)}>{count}张</button>)}</div></div></details>
                {Boolean(selectedNode.data.desktopPayload?.asset_id || (Array.isArray(selectedNode.data.desktopPayload?.output_asset_ids) && selectedNode.data.desktopPayload.output_asset_ids.length)) && <button className="quick-save" onClick={() => void handleNodeAction("保存到资产库")} aria-label="保存到资产库" title="保存副本到资产库"><PackagePlus size={16} /></button>}
                <button className="quick-generate" onClick={() => void submitSelected()} aria-label="生成图片"><WandSparkles size={16} /><span>生成图片</span></button>
              </div>
            </div>}
            {hasScriptResult && <details className="editor-section result-corner compact-corner"><summary title="查看 AI 结果"><FileText size={14} /><span>AI 结果</span></summary><div className="script-result-panel"><strong>{selectedNode.data.desktopPayload?.script_review ? "AI 剧本审阅报告" : "AI 候选稿 · 原稿尚未修改"}</strong><textarea readOnly rows={8} value={String(selectedNode.data.desktopPayload?.script_review || selectedNode.data.desktopPayload?.script_candidate || "")} /><div>{selectedNode.data.desktopPayload?.script_candidate && canWrite && <button onClick={() => void handleNodeAction("采用AI候选稿")}>采用候选稿</button>}{canWrite && <button onClick={() => void handleNodeAction("清除AI结果")}>关闭结果</button>}</div></div></details>}
            {hasGenerationParameters && <details className="editor-section compact-corner">
              <summary title="生成参数"><Settings2 size={14} /><span>参数</span></summary>
            {(["storyboard", "multi_image", "multi_director", "video", "scene_reference", "character_reference", "element_reference"].includes(selectedNode.data.specKey)) && <div className="field-row"><label><span>画面比例</span><select value={String(selectedNode.data.desktopPayload?.ratio || selectedNode.data.desktopPayload?.production_ratio || "16:9")} onChange={(event) => updatePayload(selectedNode.data.specKey === "storyboard" ? "production_ratio" : "ratio", event.target.value)}><option>16:9</option><option>9:16</option><option>1:1</option><option>4:5</option></select></label><label><span>{selectedNode.data.specKey === "storyboard" ? "镜头数" : "候选数量"}</span><input type="number" min="0" max="50" value={Number(selectedNode.data.specKey === "storyboard" ? selectedNode.data.desktopPayload?.shot_count || 0 : selectedNode.data.desktopPayload?.candidate_count || 1)} onChange={(event) => updatePayload(selectedNode.data.specKey === "storyboard" ? "shot_count" : "candidate_count", Number(event.target.value))} /></label></div>}
            {selectedNode.data.specKey === "copywriting" && <><label><span>产品 / 品牌</span><input value={String(selectedNode.data.desktopPayload?.product_name || "")} onChange={(event) => updatePayload("product_name", event.target.value)} /></label><label><span>产品卖点与必须保留的信息</span><textarea rows={4} value={String(selectedNode.data.desktopPayload?.product_description || "")} onChange={(event) => updatePayload("product_description", event.target.value)} /></label><div className="field-row"><label><span>口播风格</span><select value={String(selectedNode.data.desktopPayload?.copy_style || "激情抓眼球")} onChange={(event) => updatePayload("copy_style", event.target.value)}><option>激情抓眼球</option><option>沉稳放松</option><option>幽默有趣</option><option>高端大气</option><option>情感共鸣</option><option>专业权威</option></select></label><label><span>目标秒数</span><input type="number" min="5" value={Number(selectedNode.data.desktopPayload?.copy_duration || 30)} onChange={(event) => updatePayload("copy_duration", event.target.value)} /></label></div><label><span>翻译目标语言</span><select value={String(selectedNode.data.desktopPayload?.copy_language || "英语")} onChange={(event) => updatePayload("copy_language", event.target.value)}><option>英语</option><option>日语</option><option>韩语</option><option>西班牙语</option><option>法语</option><option>德语</option><option>泰语</option><option>阿拉伯语</option></select></label></>}
            {selectedNode.data.specKey === "audio" && <><label><span>声音 / 音色 ID</span><input placeholder="例如：系统默认女声；选择配音引擎后可填写指定音色" value={String(selectedNode.data.desktopPayload?.voice || "")} onChange={(event) => updatePayload("voice", event.target.value)} /></label><div className="field-row"><label><span>语速</span><select value={String(selectedNode.data.desktopPayload?.speed || 1)} onChange={(event) => updatePayload("speed", Number(event.target.value))}><option value="0.8">较慢 · 0.8×</option><option value="1">正常 · 1.0×</option><option value="1.2">较快 · 1.2×</option></select></label><label><span>说话情绪</span><select value={String(selectedNode.data.desktopPayload?.emotion || "自然")} onChange={(event) => updatePayload("emotion", event.target.value)}><option>自然</option><option>温暖</option><option>开心</option><option>悲伤</option><option>严肃</option><option>激动</option></select></label></div></>}
            {["video", "multi_director"].includes(selectedNode.data.specKey) && <><div className="field-row"><label><span>视频时长（秒）</span><input type="number" min="2" max="15" step="1" value={Number(selectedNode.data.desktopPayload?.duration || 10)} onChange={(event) => updatePayload("duration", Number(event.target.value))} /></label><label><span>输出清晰度</span><select value={String(selectedNode.data.desktopPayload?.resolution || "720p")} onChange={(event) => updatePayload("resolution", event.target.value)}><option>720p</option><option>1080p</option></select></label></div><label className="toggle-line" aria-label="同时生成声音"><input type="checkbox" checked={selectedNode.data.desktopPayload?.generate_audio !== false} onChange={(event) => updatePayload("generate_audio", event.target.checked)} /><span><strong>同时生成声音</strong><small>对白、环境声和动作声；仅支持具备原生音频能力的模型</small></span></label>{selectedNode.data.desktopPayload?.generate_audio !== false && <label><span>声音怎么安排</span><textarea rows={3} placeholder="例如：0–3 秒雨声；4 秒人物说话；结尾传来关门声" value={String(selectedNode.data.desktopPayload?.audio_prompt || "")} onChange={(event) => updatePayload("audio_prompt", event.target.value)} /></label>}</>}
            {selectedNode.data.specKey === "shot" && <div className="shot-contract"><div><strong>导演合同</strong><span>字段会直接进入关键帧和视频提示词</span></div>{([['story_function','故事功能','观众通过本镜新知道或感受到什么'],['visual_thesis','视觉命题','本镜的核心画面表达'],['action_start','动作起点','姿态、位置与朝向'],['primary_action','唯一主动作','一个动作、速度与方向'],['action_end','动作终点','动作结束后的稳定状态'],['dominant_camera_move','唯一主运镜','固定，或一种推拉摇移跟'],['dialogue','对白','本镜需要生成的对白'],['generation_risk','主要生成风险','身份、空间、动作或物理风险']] as const).map(([key,label,placeholder]) => <label key={key}><span>{label}</span><input placeholder={placeholder} value={String(((selectedNode.data.desktopPayload?.shot || {}) as Record<string, unknown>)[key] || '')} onChange={(event) => updatePayload('shot', { ...((selectedNode.data.desktopPayload?.shot || {}) as Record<string, unknown>), [key]: event.target.value })} /></label>)}<div className="field-row"><label><span>景别</span><select value={String(((selectedNode.data.desktopPayload?.shot || {}) as Record<string, unknown>).shot_size || '中景')} onChange={(event) => updatePayload('shot', { ...((selectedNode.data.desktopPayload?.shot || {}) as Record<string, unknown>), shot_size: event.target.value })}><option>远景</option><option>全景</option><option>中景</option><option>近景</option><option>特写</option></select></label><label><span>镜头时长</span><input type="number" min="0.5" max="15" step="0.5" value={Number(((selectedNode.data.desktopPayload?.shot || {}) as Record<string, unknown>).duration || 5)} onChange={(event) => updatePayload('shot', { ...((selectedNode.data.desktopPayload?.shot || {}) as Record<string, unknown>), duration: Number(event.target.value) })} /></label></div><label><span>连续性不变量（每行一条）</span><textarea rows={4} value={Array.isArray(((selectedNode.data.desktopPayload?.shot || {}) as Record<string, unknown>).continuity_invariants) ? (((selectedNode.data.desktopPayload?.shot || {}) as Record<string, unknown>).continuity_invariants as unknown[]).join('\n') : String(((selectedNode.data.desktopPayload?.shot || {}) as Record<string, unknown>).continuity_invariants || '')} onChange={(event) => updatePayload('shot', { ...((selectedNode.data.desktopPayload?.shot || {}) as Record<string, unknown>), continuity_invariants: event.target.value.split('\n').map((value) => value.trim()).filter(Boolean) })} /></label></div>}
            </details>}
            {controlled && selectedNode.data.specKey !== "multi_image" && hasProviderSelection && <details className="editor-section compact-corner">
              <summary title="选择生成模型"><Sparkles size={14} /><span>模型</span></summary>
            {controlled && selectedNode.data.specKey === "storyboard" && <div className="provider-locks">
              {([{"key":"planning","label":"拆镜 / 编剧","capability":"chat"},{"key":"image","label":"图片","capability":"text_to_image"},{"key":"video","label":"视频","capability":"text_to_video"}] as const).map((lock) => { const providerName = String(selectedNode.data.desktopPayload?.[`${lock.key}_provider`] || ""); const modelName = String(selectedNode.data.desktopPayload?.[`${lock.key}_model`] || providers.find((item) => item.name === providerName)?.profile?.model || ""); return <div className="provider-lock" key={lock.key}><label><span>{lock.label}引擎</span><select value={providerName} onChange={(event) => { const name = event.target.value; updatePayload(`${lock.key}_provider`, name); updatePayload(`${lock.key}_model`, String(providers.find((item) => item.name === name)?.profile?.model || "")); }}><option value="">明确选择</option>{providers.filter((item) => item.capabilities.includes(lock.capability)).map((item) => <option key={item.name}>{item.name}</option>)}</select></label><label><span>{lock.label}模型版本</span><input value={modelName} onChange={(event) => updatePayload(`${lock.key}_model`, event.target.value)} placeholder="必须明确锁定模型 ID" /></label></div>; })}
            </div>}
            {controlled && selectedNode.data.specKey !== "storyboard" && hasProviderSelection && (() => { const providerName = String(selectedNode.data.desktopPayload?.provider_name || ""); const modelName = lockedModel(selectedNode.data.desktopPayload || {}, providers); return <><label><span>生成引擎（明确锁定，不自动切换）</span><select value={providerName} onChange={(event) => { const name = event.target.value; updatePayload("provider_name", name); updatePayload("model", String(providers.find((item) => item.name === name)?.profile?.model || "")); }}><option value="">请选择支持当前操作的引擎</option>{providers.filter((provider) => { const capability = requiredCapability(selectedNode, incomingNodes.length); return !capability || provider.capabilities.includes(capability); }).map((provider) => <option key={provider.name} value={provider.name}>{provider.name} · {provider.capabilities.join(" / ")}</option>)}</select></label><label><span>模型版本 / 端点 ID</span><input value={modelName} onChange={(event) => updatePayload("model", event.target.value)} placeholder="选择引擎后自动锁定，可按管理员配置修改" /></label></>; })()}
            </details>}
            {(selectedNode.data.specKey === "multi_director" || (selectedNode.data.specKey === "video" && incomingNodes.length > 0)) && <details className="editor-section compact-corner">
              <summary title={selectedNode.data.specKey === "multi_director" ? "导演时间轴" : "设置首尾帧和参考图片"}>{selectedNode.data.specKey === "multi_director" ? <ListTodo size={14} /> : <ImageIcon size={14} />}<span>{selectedNode.data.specKey === "multi_director" ? "时间轴" : "参考"}</span><b>{incomingNodes.length}</b></summary>
            {selectedNode.data.specKey === "multi_image" && <div className="reference-purpose-editor"><div><strong>逐张定义参考用途</strong><span>{incomingNodes.length} 张输入</span></div>{incomingNodes.length ? incomingNodes.map((node) => { const settings = ((selectedNode.data.desktopPayload?.reference_settings as Array<Record<string, unknown>> | undefined) || []).find((item) => item.source_node_id === node.id) || {}; return <div className="purpose-row" key={node.id}><span>{node.data.title}</span><select aria-label={`${node.data.title}用途`} value={String(settings.purpose || "subject")} onChange={(event) => updateReferenceRow("reference_settings", node.id, { purpose: event.target.value })}><option value="subject">主体身份</option><option value="scene">场景结构</option><option value="composition">构图机位</option><option value="element">道具元素</option><option value="style">视觉风格</option></select><input aria-label={`${node.data.title}补充要求`} placeholder="例如：只参考服装和五官" value={String(settings.instruction || "")} onChange={(event) => updateReferenceRow("reference_settings", node.id, { instruction: event.target.value })} /></div>; }) : <p>把图片节点连入后，会在这里逐张设置用途。</p>}</div>}
            {selectedNode.data.specKey === "video" && incomingNodes.length > 0 && <div className="reference-purpose-editor"><div><strong>视频输入职责</strong><span>首帧、尾帧与资产参考必须明确</span></div>{incomingNodes.map((node) => { const settings = ((selectedNode.data.desktopPayload?.reference_settings as Array<Record<string, unknown>> | undefined) || []).find((item) => item.source_node_id === node.id) || {}; const relation = String((edges.find((edge) => edge.source === node.id && edge.target === selectedNode.id)?.data as Record<string, unknown> | undefined)?.relation || ""); const assetId = node.data.desktopPayload?.asset_id || (Array.isArray(node.data.desktopPayload?.output_asset_ids) ? node.data.desktopPayload.output_asset_ids[0] : ""); const inferred = relation === "first_frame" ? "first_frame" : relation === "last_frame" || String(assetId || "") === String(selectedNode.data.desktopPayload?.last_frame_asset_id || "") ? "last_frame" : "reference"; return <div className="purpose-row" key={node.id}><span>{node.data.title}</span><select aria-label={`${node.data.title}视频职责`} value={String(settings.purpose || inferred)} onChange={(event) => updateReferenceRow("reference_settings", node.id, { purpose: event.target.value })}><option value="first_frame">视频首帧</option><option value="last_frame">视频尾帧</option><option value="reference">普通参考</option><option value="subject">主体身份</option><option value="scene">场景结构</option><option value="composition">构图机位</option><option value="element">道具元素</option></select><input aria-label={`${node.data.title}视频补充要求`} placeholder="例如：仅保持人物身份" value={String(settings.instruction || "")} onChange={(event) => updateReferenceRow("reference_settings", node.id, { instruction: event.target.value })} /></div>; })}</div>}
            {selectedNode.data.specKey === "multi_director" && <div className="timeline-editor director-timeline"><div className="timeline-guide"><div><strong>按时间安排每张图</strong><span>依次填写：何时出现、画面怎么动、镜头怎么拍</span></div><small>{incomingNodes.length} 段 · 最长 {Number(selectedNode.data.desktopPayload?.duration || 10)} 秒 · 引擎参考上限 {Number(providers.find((item) => item.name === selectedNode.data.desktopPayload?.provider_name)?.profile?.reference_assets || 0) || "选择后显示"}</small></div>{incomingNodes.length ? incomingNodes.map((node, index) => { const settings = ((selectedNode.data.desktopPayload?.timeline_images as Array<Record<string, unknown>> | undefined) || []).find((item) => item.source_node_id === node.id) || {}; const start = Number(settings.start ?? index * 3), end = Number(settings.end ?? Math.min(Number(selectedNode.data.desktopPayload?.duration || 10), index * 3 + 3)); return <article className="director-segment" key={node.id}><header><span>{index + 1}</span><div><strong>{node.data.title}</strong><small>第 {start}–{end} 秒</small></div></header><div className="segment-time"><label><span>从第几秒开始</span><input type="number" min="0" step="0.1" aria-label={`${node.data.title}开始秒`} value={start} onChange={(event) => updateReferenceRow("timeline_images", node.id, { start: Number(event.target.value), end })} /></label><label><span>到第几秒结束</span><input type="number" min="0" step="0.1" aria-label={`${node.data.title}结束秒`} value={end} onChange={(event) => updateReferenceRow("timeline_images", node.id, { start, end: Number(event.target.value) })} /></label></div><label><span>这段画面怎么动</span><input aria-label={`${node.data.title}动作`} placeholder="例如：角色抬头，随后走向窗边" value={String(settings.action || settings.instruction || "")} onChange={(event) => updateReferenceRow("timeline_images", node.id, { action: event.target.value, start, end })} /></label><label><span>镜头怎么拍</span><input aria-label={`${node.data.title}运镜`} placeholder="例如：缓慢推近；留空则保持稳定" value={String(settings.camera || "")} onChange={(event) => updateReferenceRow("timeline_images", node.id, { camera: event.target.value, start, end })} /></label><label><span>这张图主要负责</span><select aria-label={`${node.data.title}参考用途`} value={String(settings.purpose || "continuity")} onChange={(event) => updateReferenceRow("timeline_images", node.id, { purpose: event.target.value, start, end })}><option value="continuity">衔接上一段</option><option value="first_frame">作为开场画面</option><option value="last_frame">作为结尾画面</option><option value="subject">保持人物 / 主体</option><option value="scene">保持场景</option><option value="composition">保持构图</option></select></label></article>; }) : <div className="timeline-empty"><ImageIcon size={18} /><div><strong>还没有时间轴画面</strong><span>把图片节点连到这个节点，它们会按连入顺序出现在这里。</span></div></div>}</div>}
            </details>}
            {selectedNode.data.specKey !== "multi_image" && <>
              {showAssetInput && !["text_to_video", "text_to_speech", "idea_to_movie", "write_script", "write_copy"].includes(beginnerMode) && <details className="asset-chip compact-corner"><summary title="查看已连接素材"><Link2 size={14} /><span>素材</span><b>{incomingNodes.length}</b></summary><div className="asset-chip-popover"><span>{incomingNodes.length ? incomingNodes.map((node) => node.data.title).join("、") : "拖动素材节点右侧圆点连接到当前节点"}</span><button onClick={() => importInputRef.current?.click()}><Upload size={14} />导入素材</button></div></details>}
              {hasNodeCommand && <div className="editor-command-bar">
                {selectedNode.data.specKey !== "storyboard" && selectedActions.length > 0 && !["text_to_video", "first_frame_video", "first_last_frame_video", "text_to_speech", "multi_image_director", "break_down_video"].includes(beginnerMode) && <label className="compact-action-selector"><select aria-label="当前操作" value={selectedAction} onChange={(event) => updatePayload("editor_action", event.target.value)}>{selectedActions.map((action) => <option key={action} value={action}>{action}</option>)}</select></label>}
                <button className="generate-button" onClick={() => DIRECT_NODE_ACTIONS.has(selectedAction) ? void handleNodeAction(selectedAction) : void submitSelected()}>{DIRECT_NODE_ACTIONS.has(selectedAction) ? <Zap size={17} /> : <WandSparkles size={17} />} {selectedNode.data.specKey === "storyboard" ? "开始 / 继续制片" : beginnerMode === "text_to_speech" ? "生成语音" : ["text_to_video", "first_frame_video", "first_last_frame_video", "multi_image_director"].includes(beginnerMode) ? "生成视频" : beginnerMode === "break_down_video" ? "开始拉片" : DIRECT_NODE_ACTIONS.has(selectedAction) ? selectedAction : selectedAction || "生成"}</button>
              </div>}
              {hasNodeCommand && !DIRECT_NODE_ACTIONS.has(selectedAction) && <p className="cost-note">点击后先显示所用模型和预计费用，确认后才会提交。</p>}
            </>}
          </div>}
        </section>
        </NodeEditorPortal>
      </section>
      {pendingTask && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setPendingTask(null); }}><section className="task-confirm" role="dialog" aria-modal="true" aria-labelledby="task-confirm-title"><header><div><Zap size={18} /><span><strong id="task-confirm-title">确认提交生成任务</strong><small>由公司服务器校验账号、模型和额度</small></span></div><button onClick={() => setPendingTask(null)} aria-label="关闭"><X size={17} /></button></header><div className="task-confirm-body"><dl><div><dt>操作</dt><dd>{pendingTask.action}</dd></div><div><dt>锁定模型</dt><dd>{pendingTask.model}</dd></div><div><dt>额度预估</dt><dd>{pendingTask.credits} credits</dd></div></dl><p>提交后不会静默换模型；重复请求会通过幂等键拦截。生成结果先进入画布，只有手动点击才会复制到资产库。</p><div><button onClick={() => setPendingTask(null)}>取消</button><button className="confirm-primary" onClick={() => void pendingTask.run()}>确认并提交</button></div></div></section></div>}
      {redoStage !== null && selectedNode?.data.specKey === "storyboard" && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setRedoStage(null); }}><section className="redo-dialog" role="dialog" aria-modal="true" aria-labelledby="redo-dialog-title"><header><div><Redo2 size={18} /><span><strong id="redo-dialog-title">重做制片阶段</strong><small>选择从哪一步重新生成</small></span></div><button onClick={() => setRedoStage(null)} aria-label="关闭"><X size={17} /></button></header><div className="redo-body"><div className="redo-stage-list">{PRODUCTION_STAGE_NAMES.slice(0, Math.max(1, Number(selectedNode.data.desktopPayload?.pipeline_stage || 1))).map((name, index) => <button key={name} className={redoStage === index + 1 ? "is-selected" : ""} onClick={() => setRedoStage(index + 1)}><span>{index + 1}</span><strong>{name}</strong>{redoStage === index + 1 && <small>从这里重做</small>}</button>)}</div><p>所选阶段及其后续结果会失效，并重新调用模型，可能产生费用。</p><div className="redo-actions"><button onClick={() => setRedoStage(null)}>取消</button><button className="redo-confirm" onClick={() => { const target = redoStage; setRedoStage(null); void (async () => { await productionCommand("rewind", target); await productionCommand("continue"); })(); }}>确认重做并继续</button></div></div></section></div>}
      {adminOpen && <AdminPanel onClose={() => setAdminOpen(false)} />}
      {teamOpen && <TeamPanel projectId={projectId} onClose={() => setTeamOpen(false)} />}
    </main>
  );
}

export function StudioCanvas() {
  return <ReactFlowProvider><CanvasApp /></ReactFlowProvider>;
}
