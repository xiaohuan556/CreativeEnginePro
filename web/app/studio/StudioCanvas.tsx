"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  addEdge, Background, BackgroundVariant, Connection, Controls, Edge, Handle,
  MarkerType, MiniMap, Node, NodeProps, Position, ReactFlow, ReactFlowProvider,
  useEdgesState, useNodesState,
} from "@xyflow/react";
import {
  AudioLines, Boxes, ChevronDown, CirclePlay, Clapperboard, FileText, Film,
  FolderOpen, Image as ImageIcon, Import, LayoutGrid, Menu, Mic2,
  MoreHorizontal, Plus, Redo2, ScanSearch, Settings2, Sparkles, Upload, Users,
  WandSparkles, X, Zap, Camera, Shapes, Workflow, ListTodo, PackageCheck,
} from "lucide-react";
import "@xyflow/react/dist/style.css";
import { desktopProjectToWeb, toWebCanvas } from "../../lib/canvas-protocol";
import { canConnect, CREATION_GROUPS, NODE_SPEC_BY_KEY, NODE_SPECS, StudioNodeKind } from "../../lib/node-registry";
import { PulseEdge } from "./PulseEdge";
import { useControlPlane } from "./ControlPlane";
import { AdminPanel } from "./AdminPanel";

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

const creationItems = NODE_SPECS.filter((item) => item.creatable);

function StudioNodeCard({ data, selected }: NodeProps<StudioNode>) {
  const Icon = kindIcons[data.kind];
  return (
    <article className={`studio-node ${selected ? "is-selected" : ""}`}
      style={{ "--node-accent": data.accent } as React.CSSProperties}>
      <Handle type="target" position={Position.Left} className="node-handle" />
      <div className="node-topline">
        <span className="node-icon"><Icon size={15} strokeWidth={1.8} /></span>
        <span className="node-status">{data.status}</span>
        <button className="icon-button node-more" aria-label="节点菜单"><MoreHorizontal size={16} /></button>
      </div>
      <h3>{data.title}</h3>
      <p>{data.description}</p>
      {typeof data.progress === "number" && <div className="progress-track"><span style={{ width: `${data.progress}%` }} /></div>}
      <div className="node-meta">{data.meta}</div>
      <Handle type="source" position={Position.Right} className="node-handle" />
    </article>
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

function CanvasApp() {
  const { apiFetch, controlled, signOut, user } = useControlPlane();
  const [nodes, setNodes, onNodesChange] = useNodesState<StudioNode>(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);
  const [selectedId, setSelectedId] = useState("director");
  const [createOpen, setCreateOpen] = useState(false);
  const [notice, setNotice] = useState("所有更改已保存");
  const [adminOpen, setAdminOpen] = useState(false);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [projectTitle, setProjectTitle] = useState("雨夜最后一封信");
  const [projectId, setProjectId] = useState("");
  const [hydrated, setHydrated] = useState(false);
  const versionRef = useRef(1);
  const bootingRef = useRef(false);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const importInputRef = useRef<HTMLInputElement>(null);
  const projectInputRef = useRef<HTMLInputElement>(null);

  const selectedNode = useMemo(() => nodes.find((node) => node.id === selectedId) ?? nodes[0], [nodes, selectedId]);
  const incomingNodes = useMemo(() => edges.filter((edge) => edge.target === selectedId).map((edge) => nodes.find((node) => node.id === edge.source)).filter((node): node is StudioNode => Boolean(node)), [edges, nodes, selectedId]);

  useEffect(() => {
    if (bootingRef.current) return;
    bootingRef.current = true;
    void (async () => {
      try {
        const listing = await apiFetch("/api/projects", { cache: "no-store" });
        if (!listing.ok) throw new Error("项目列表不可用");
        const data = await listing.json() as { projects?: Array<{ id: string }> };
        const first = data.projects?.[0];
        if (first) {
          const response = await apiFetch(`/api/projects/${first.id}`, { cache: "no-store" });
          const detail = await response.json() as { project?: { id: string; title: string; version: number; canvas: { nodes: StudioNode[]; edges: Edge[] } } };
          if (detail.project) {
            setProjectId(detail.project.id); setProjectTitle(detail.project.title);
            versionRef.current = detail.project.version;
            setNodes(detail.project.canvas.nodes.map(normalizeStudioNode));
            setEdges(detail.project.canvas.edges.map((edge) => ({ ...edge, type: "pulse" })));
          }
        } else {
          const response = await apiFetch("/api/projects", {
            method: "POST", headers: { "content-type": "application/json" },
            body: JSON.stringify({ title: projectTitle, canvas: toWebCanvas(initialNodes, initialEdges) }),
          });
          const created = await response.json() as { project?: { id: string; version: number } };
          if (created.project) { setProjectId(created.project.id); versionRef.current = created.project.version; }
        }
        setNotice("服务器项目已同步");
      } catch {
        setNotice("本地预览 · 连接服务器后自动同步");
      } finally {
        setHydrated(true);
      }
    })();
  }, [apiFetch, projectTitle, setEdges, setNodes]);

  useEffect(() => {
    if (!controlled) return;
    void apiFetch("/api/providers", { cache: "no-store" }).then(async (response) => {
      if (!response.ok) return;
      const data = await response.json() as { providers?: ProviderInfo[] };
      setProviders(data.providers || []);
    });
  }, [apiFetch, controlled]);

  useEffect(() => {
    if (!hydrated || !projectId) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      setNotice("正在保存…");
      void apiFetch(`/api/projects/${projectId}`, {
        method: "PATCH", headers: { "content-type": "application/json" },
        body: JSON.stringify({ title: projectTitle, canvas: toWebCanvas(nodes, edges), expectedVersion: versionRef.current }),
      }).then(async (response) => {
        const data = await response.json() as { project?: { version: number }; currentVersion?: number };
        if (response.status === 409) { versionRef.current = data.currentVersion || versionRef.current; setNotice("检测到成员更新 · 请重新载入"); return; }
        if (!response.ok || !data.project) throw new Error("save failed");
        versionRef.current = data.project.version; setNotice("所有更改已保存");
      }).catch(() => setNotice("保存失败 · 将自动重试"));
    }, 700);
    return () => { if (saveTimerRef.current) clearTimeout(saveTimerRef.current); };
  }, [apiFetch, edges, hydrated, nodes, projectId, projectTitle]);

  const onConnect = useCallback((connection: Connection) => {
    const source = nodes.find((node) => node.id === connection.source);
    const target = nodes.find((node) => node.id === connection.target);
    if (!source || !target || source.id === target.id || !canConnect(source.data.specKey, target.data.specKey)) {
      setNotice("这两个节点的数据类型不兼容，连线未创建");
      return;
    }
    setEdges((current) => addEdge({
      ...connection, type: "pulse",
      markerEnd: { type: MarkerType.ArrowClosed, color: "#77839c" },
    }, current));
    setNotice("连线已保存");
  }, [nodes, setEdges]);

  const addNode = (item: (typeof creationItems)[number], uniqueId: string) => {
    const id = `${item.key}-${uniqueId}`;
    const offset = nodes.length * 24;
    setNodes((current) => [...current, {
      id, type: "studio", position: { x: 380 + offset, y: 260 + offset },
      data: { title: item.title, description: item.description, kind: item.kind, specKey: item.key, desktopType: item.desktopType, status: "待设置", meta: "新建节点", accent: item.accent, desktopPayload: { ...item.defaults, type: item.desktopType } },
    }]);
    setSelectedId(id); setCreateOpen(false); setNotice(`${item.title}已创建`);
  };

  const updateSelected = (key: "title" | "description", value: string) => {
    setNodes((current) => current.map((node) => node.id === selectedId ? { ...node, data: { ...node.data, [key]: value } } : node));
    setNotice("正在保存…");
    window.setTimeout(() => setNotice("所有更改已保存"), 450);
  };

  const updatePayload = (key: string, value: unknown) => {
    setNodes((current) => current.map((node) => node.id === selectedId ? { ...node, data: { ...node.data, desktopPayload: { ...(node.data.desktopPayload || {}), [key]: value } } } : node));
  };

  const pollTask = useCallback(async (taskId: string, nodeId: string) => {
    for (let attempt = 0; attempt < 1800; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
      try {
        const response = await apiFetch(`/api/tasks/${taskId}`, { cache: "no-store" });
        if (!response.ok) return;
        const data = await response.json() as { task: { status: string; progress: number; error_message?: string } };
        setNodes((current) => current.map((node) => node.id === nodeId ? { ...node, data: { ...node.data, status: data.task.status === "completed" ? "生成完成" : data.task.status === "failed" ? "生成失败" : `AI 制片中 · ${data.task.progress}%`, progress: data.task.progress } } : node));
        if (data.task.status === "completed") { setNotice("生成完成，结果已写回画布节点"); return; }
        if (["failed", "cancelled"].includes(data.task.status)) { setNotice(data.task.error_message || "任务已停止"); return; }
      } catch { return; }
    }
  }, [apiFetch, setNodes]);

  const pollProduction = useCallback(async (runId: string, nodeId: string) => {
    for (let attempt = 0; attempt < 3600; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
      const response = await apiFetch(`/api/production-runs/${runId}`, { cache: "no-store" });
      if (!response.ok) return;
      const data = await response.json() as { run: { status: string; stage: number; stage_name: string; completed_stage: number; active_task_id?: string; error_message?: string } };
      setNodes((current) => current.map((node) => node.id === nodeId ? { ...node, data: { ...node.data, status: data.run.status === "waiting_review" ? `等待确认 · ${data.run.stage_name}` : data.run.status === "complete" ? "全流程完成" : data.run.status === "paused" ? "流程已暂停" : data.run.status === "failed" ? "阶段失败" : `AI 制片中 · ${data.run.stage}/7`, progress: Math.round(data.run.completed_stage / 7 * 100), desktopPayload: { ...(node.data.desktopPayload || {}), production_run_id: runId, pipeline_stage: data.run.stage, production_status: data.run.status } } } : node));
      if (["waiting_review", "complete", "paused", "failed"].includes(data.run.status)) { setNotice(data.run.error_message || (data.run.status === "waiting_review" ? "到达确认节点：审片通过或接受风险后才会继续" : `制片流程：${data.run.status}`)); return; }
    }
  }, [apiFetch, setNodes]);

  const productionCommand = async (command: string, targetStage?: number) => {
    if (!selectedNode || !controlled || !projectId) return;
    let runId = String(selectedNode.data.desktopPayload?.production_run_id || "");
    if (!runId) {
      const planning = String(selectedNode.data.desktopPayload?.planning_provider || ""), image = String(selectedNode.data.desktopPayload?.image_provider || ""), video = String(selectedNode.data.desktopPayload?.video_provider || "");
      if (!planning || !image || !video) { setNotice("开始制片前请明确锁定拆镜、图片和视频引擎；系统不会静默切换模型"); return; }
      const created = await apiFetch("/api/production-runs", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ project_id: projectId, node_id: selectedNode.id, automation_mode: selectedNode.data.desktopPayload?.automation_mode || "checkpoints", provider_locks: { planning, image, video } }) });
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
    if (selectedNode.data.specKey === "storyboard") {
      await productionCommand("start");
      return;
    }
    const payload = selectedNode.data.desktopPayload || {};
    const specKey = selectedNode.data.specKey;
    const mapping: Record<string, { provider: string; operation: string; model: string }> = {
      storyboard: { provider: String(payload.provider_name || "openai"), operation: "chat", model: String(payload.model || payload.planning_model || "") },
      script: { provider: String(payload.provider_name || "openai"), operation: "chat", model: String(payload.model || "") },
      copywriting: { provider: String(payload.provider_name || "openai"), operation: "chat", model: String(payload.model || "") },
      multi_image: { provider: String(payload.provider_name || "seedream"), operation: incomingNodes.length ? "image_edit" : "text_to_image", model: String(payload.model || "") },
      scene_reference: { provider: String(payload.provider_name || "seedream"), operation: incomingNodes.length ? "image_edit" : "text_to_image", model: String(payload.model || "") },
      character_reference: { provider: String(payload.provider_name || "seedream"), operation: incomingNodes.length ? "image_edit" : "text_to_image", model: String(payload.model || "") },
      element_reference: { provider: String(payload.provider_name || "seedream"), operation: incomingNodes.length ? "image_edit" : "text_to_image", model: String(payload.model || "") },
      multi_director: { provider: String(payload.provider_name || "seedance"), operation: "text_to_video", model: String(payload.model || "") },
      video: { provider: String(payload.provider_name || "seedance"), operation: incomingNodes.length ? "image_to_video" : "text_to_video", model: String(payload.model || "") },
      audio: { provider: String(payload.provider_name || "edge_tts"), operation: "text_to_speech", model: String(payload.voice || "") },
      analysis: { provider: "local", operation: "video_breakdown", model: "local" },
      skill: { provider: String(payload.provider_name || "openai"), operation: "chat", model: String(payload.model || "") },
      shot: { provider: String(payload.provider_name || "seedream"), operation: "text_to_image", model: String(payload.model || "") },
    };
    const task = mapping[specKey];
    if (!task) { setNotice("该运行节点由上游工作流自动驱动，不能单独提交"); return; }
    if (task.provider !== "local" && !payload.provider_name) { setNotice("请先在节点设置中明确选择生成引擎；系统不会替你静默切换模型"); return; }
    if (task.provider !== "local" && providers.length && !providers.some((provider) => provider.name === task.provider && provider.capabilities.includes(task.operation))) { setNotice(`“${task.provider}”当前不可用或不支持 ${task.operation}，请在节点中明确选择可用模型`); return; }
    const action = String(payload.editor_action || NODE_SPEC_BY_KEY[specKey]?.actions[0] || "生成");
    if (!window.confirm(`确认提交“${action}”？\n模型：${task.model || task.provider}\n预计费用：由管理员额度控制，服务端不会静默换模型。`)) return;
    setNodes((current) => current.map((node) => node.id === selectedNode.id ? { ...node, data: { ...node.data, status: "正在排队", progress: 0 } } : node));
    const response = await apiFetch("/api/tasks", { method: "POST", headers: { "content-type": "application/json", "idempotency-key": crypto.randomUUID() }, body: JSON.stringify({ project_id: projectId, node_id: selectedNode.id, kind: task.operation, provider: task.provider, model: task.model, estimated_credits: 0, input: { inputs: { prompt: selectedNode.data.description, references: incomingNodes.map((node) => ({ node_id: node.id, title: node.data.title, asset_id: node.data.desktopPayload?.asset_id })) }, params: payload, action, use_cache: false } }) });
    const data = await response.json() as { task?: { id: string }; detail?: string };
    if (!response.ok || !data.task) { setNotice(data.detail || "任务提交失败"); return; }
    setNotice("任务已进入公司队列"); void pollTask(data.task.id, selectedNode.id);
  };

  const importDesktopProject = async (file: File) => {
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

  const importMedia = async (files: File[]) => {
    if (!projectId) { setNotice("项目尚未同步完成，请稍后导入"); return; }
    let imported = 0;
    for (const [index, file] of files.entries()) {
      const mediaKind = file.type.startsWith("video/") ? "video" : file.type.startsWith("audio/") ? "audio" : file.type.startsWith("image/") ? "image" : "";
      if (!mediaKind) continue;
      const nodeId = `imported-${mediaKind}-${crypto.randomUUID()}`;
      const form = new FormData();
      form.set("file", file); form.set("projectId", projectId); form.set("project_id", projectId); form.set("nodeId", nodeId); form.set("node_id", nodeId); form.set("kind", mediaKind); form.set("metadata_json", JSON.stringify({ source: "canvas_import" }));
      const response = await apiFetch("/api/assets", { method: "POST", body: form });
      const data = await response.json() as { asset?: { id: string; url?: string }; detail?: string; error?: string };
      if (!response.ok || !data.asset) { setNotice(data.detail || data.error || `${file.name} 导入失败`); continue; }
      const spec = mediaKind === "video" ? NODE_SPEC_BY_KEY.video : mediaKind === "audio" ? NODE_SPEC_BY_KEY.audio : NODE_SPEC_BY_KEY.image_asset;
      const offset = index * 34;
      setNodes((current) => [...current, { id: nodeId, type: "studio", position: { x: 320 + offset, y: 220 + offset }, data: { title: file.name.replace(/\.[^.]+$/, ""), description: `已导入画布 · ${Math.max(1, Math.round(file.size / 1024))} KB`, kind: spec.kind, specKey: spec.key, desktopType: spec.desktopType, status: "已导入画布", meta: file.type || mediaKind, accent: spec.accent, desktopPayload: { ...spec.defaults, asset_id: data.asset.id, asset_url: data.asset.url || `/api/assets/${data.asset.id}`, content_type: file.type, size: file.size } } }]);
      imported += 1;
    }
    setNotice(imported ? `已导入 ${imported} 个素材节点 · 未自动保存到资产库` : "没有可导入的媒体文件");
  };

  return (
    <main className="studio-shell">
      <header className="topbar">
        <div className="brand-mark"><Clapperboard size={18} /></div>
        <div className="project-heading"><strong>Creative Engine</strong><span className="project-separator">/</span><button className="project-name">{projectTitle} <ChevronDown size={14} /></button></div>
        <div className="topbar-center"><span className="live-dot" /><span>{notice}</span></div>
        <div className="topbar-actions"><button className="team-button"><Users size={15} /> {user.role === "admin" ? "管理员" : "制片组"}</button><button className="icon-button" aria-label={user.role === "admin" ? "账号与权限" : "设置"} onClick={() => { if (user.role === "admin" && controlled) setAdminOpen(true); }}><Settings2 size={17} /></button><button className="avatar" title={controlled ? `${user.display_name} · 点击退出` : user.display_name} onClick={() => { if (controlled) void signOut(); }}>{(user.display_name || user.username || "制").slice(0, 1)}</button></div>
      </header>

      <section className="workspace">
        <aside className="rail">
          <button className="rail-button is-active"><Boxes size={18} /><span>画布</span></button>
          <button className="rail-button"><FolderOpen size={18} /><span>资产</span></button>
          <button className="rail-button"><Zap size={18} /><span>任务</span></button>
          <div className="rail-spacer" />
          <button className="rail-button"><Menu size={18} /><span>菜单</span></button>
        </aside>

        <div className="canvas-wrap">
          <div className="canvas-context"><span>AI 制片画布</span><span className="context-divider" /><span>{nodes.length} 个节点</span><span>{edges.length} 条工作流连接</span></div>
          <ReactFlow<StudioNode, Edge>
            nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
            onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect}
            onNodeClick={(_, node) => setSelectedId(node.id)} onPaneClick={() => setCreateOpen(false)}
            fitView fitViewOptions={{ padding: 0.18 }} minZoom={0.18} maxZoom={1.8}
            proOptions={{ hideAttribution: true }} deleteKeyCode={["Backspace", "Delete"]}>
            <Background color="#2b2f38" gap={28} size={1} variant={BackgroundVariant.Dots} />
            <Controls position="bottom-left" showInteractive={false} />
            <MiniMap position="bottom-right" nodeColor={(node) => (node.data as StudioData).accent} maskColor="rgba(8, 10, 14, .72)" pannable zoomable />
          </ReactFlow>

          <div className="dock-wrap">
            <nav className="creation-dock" aria-label="画布程序坞">
              <button className="dock-primary" onClick={() => setCreateOpen((value) => !value)}><Plus size={17} /> 新建</button>
              <button><CirclePlay size={16} /> 开始制片</button><button><Redo2 size={16} /> 重做</button>
              <button onClick={() => projectInputRef.current?.click()}><FolderOpen size={16} /> 工程</button><button onClick={() => importInputRef.current?.click()}><Import size={16} /> 导入</button>
              <button><LayoutGrid size={16} /> 整理</button><button><Boxes size={16} /> 资产</button>
            </nav>
            <input ref={projectInputRef} className="hidden-file-input" type="file" accept=".cepstudio,.json,application/json" onChange={(event) => { const file = event.target.files?.[0]; if (file) void importDesktopProject(file); event.currentTarget.value = ""; }} />
            <input ref={importInputRef} className="hidden-file-input" type="file" multiple accept="image/*,video/*,audio/*" onChange={(event) => { const files = [...(event.target.files || [])]; if (files.length) void importMedia(files); event.currentTarget.value = ""; }} />
            {createOpen && <div className="create-popover">
              <div className="popover-heading"><div><strong>创建画布节点</strong><span>高频创作能力</span></div><button className="icon-button" onClick={() => setCreateOpen(false)}><X size={16} /></button></div>
              <div className="creation-list">{CREATION_GROUPS.map((group) => <section key={group.key} className="creation-group"><h4>{group.label}</h4>{creationItems.filter((item) => item.group === group.key).map((item) => { const Icon = kindIcons[item.kind]; return <button key={item.key} onClick={() => addNode(item, crypto.randomUUID())}><span className="creation-icon" style={{ color: item.accent }}><Icon size={17} /></span><span><strong>{item.title}</strong><small>{item.description}</small></span></button>; })}</section>)}</div>
            </div>}
          </div>
        </div>

        <aside className="inspector">
          <div className="inspector-heading"><div><span>节点设置</span><strong>{selectedNode?.data.title}</strong></div><button className="icon-button"><MoreHorizontal size={18} /></button></div>
          {selectedNode && <div className="inspector-body">
            <div className="selected-kind" style={{ "--node-accent": selectedNode.data.accent } as React.CSSProperties}>
              {(() => { const Icon = kindIcons[selectedNode.data.kind]; return <Icon size={18} />; })()}
              <div><strong>{selectedNode.data.status}</strong><span>{selectedNode.data.meta}</span></div>
            </div>
            <label><span>节点名称</span><input value={selectedNode.data.title} onChange={(event) => updateSelected("title", event.target.value)} /></label>
            <label><span>创作要求</span><textarea value={selectedNode.data.description} onChange={(event) => updateSelected("description", event.target.value)} rows={6} /></label>
            {(["storyboard", "multi_image", "multi_director", "video", "scene_reference", "character_reference", "element_reference"].includes(selectedNode.data.specKey)) && <div className="field-row"><label><span>画面比例</span><select value={String(selectedNode.data.desktopPayload?.ratio || selectedNode.data.desktopPayload?.production_ratio || "16:9")} onChange={(event) => updatePayload(selectedNode.data.specKey === "storyboard" ? "production_ratio" : "ratio", event.target.value)}><option>16:9</option><option>9:16</option><option>1:1</option><option>4:5</option></select></label><label><span>{selectedNode.data.specKey === "storyboard" ? "镜头数" : "候选数量"}</span><input type="number" min="0" max="50" value={Number(selectedNode.data.specKey === "storyboard" ? selectedNode.data.desktopPayload?.shot_count || 0 : selectedNode.data.desktopPayload?.candidate_count || 1)} onChange={(event) => updatePayload(selectedNode.data.specKey === "storyboard" ? "shot_count" : "candidate_count", Number(event.target.value))} /></label></div>}
            {selectedNode.data.specKey === "copywriting" && <><label><span>产品 / 品牌</span><input value={String(selectedNode.data.desktopPayload?.product_name || "")} onChange={(event) => updatePayload("product_name", event.target.value)} /></label><label><span>产品卖点与必须保留的信息</span><textarea rows={4} value={String(selectedNode.data.desktopPayload?.product_description || "")} onChange={(event) => updatePayload("product_description", event.target.value)} /></label><div className="field-row"><label><span>口播风格</span><select value={String(selectedNode.data.desktopPayload?.copy_style || "激情抓眼球")} onChange={(event) => updatePayload("copy_style", event.target.value)}><option>激情抓眼球</option><option>沉稳放松</option><option>幽默有趣</option><option>高端大气</option><option>情感共鸣</option><option>专业权威</option></select></label><label><span>目标秒数</span><input type="number" min="5" value={Number(selectedNode.data.desktopPayload?.copy_duration || 30)} onChange={(event) => updatePayload("copy_duration", event.target.value)} /></label></div></>}
            {controlled && selectedNode.data.specKey === "storyboard" && <div className="provider-locks"><label><span>拆镜 / 编剧引擎</span><select value={String(selectedNode.data.desktopPayload?.planning_provider || "")} onChange={(event) => updatePayload("planning_provider", event.target.value)}><option value="">明确选择</option>{providers.filter((item) => item.capabilities.includes("chat")).map((item) => <option key={item.name}>{item.name}</option>)}</select></label><label><span>图片引擎</span><select value={String(selectedNode.data.desktopPayload?.image_provider || "")} onChange={(event) => updatePayload("image_provider", event.target.value)}><option value="">明确选择</option>{providers.filter((item) => item.capabilities.includes("text_to_image")).map((item) => <option key={item.name}>{item.name}</option>)}</select></label><label><span>视频引擎</span><select value={String(selectedNode.data.desktopPayload?.video_provider || "")} onChange={(event) => updatePayload("video_provider", event.target.value)}><option value="">明确选择</option>{providers.filter((item) => item.capabilities.includes("text_to_video")).map((item) => <option key={item.name}>{item.name}</option>)}</select></label></div>}
            {controlled && !["storyboard", "analysis", "image_asset", "workflow", "task", "result", "project"].includes(selectedNode.data.specKey) && <label><span>生成引擎（明确锁定，不自动切换）</span><select value={String(selectedNode.data.desktopPayload?.provider_name || "")} onChange={(event) => updatePayload("provider_name", event.target.value)}><option value="">请选择可用引擎</option>{providers.map((provider) => <option key={provider.name} value={provider.name}>{provider.name} · {provider.capabilities.join(" / ")}</option>)}</select></label>}
            {selectedNode.data.specKey === "multi_director" && <div className="timeline-editor"><div><strong>导演时间轴</strong><span>{incomingNodes.length}/50 张图片</span></div>{incomingNodes.map((node, index) => <div className="timeline-row" key={node.id}><span>{index + 1}</span><div><strong>{node.data.title}</strong><small>{index * 3}–{Math.min(Number(selectedNode.data.desktopPayload?.duration || 10), index * 3 + 3)} 秒</small></div><input aria-label={`${node.data.title}动作与运镜`} value={String(((selectedNode.data.desktopPayload?.timeline_images as Array<Record<string, unknown>> | undefined)?.[index]?.instruction) || "保持构图与主体，推进一个清晰动作")} onChange={(event) => { const timeline = [...((selectedNode.data.desktopPayload?.timeline_images as Array<Record<string, unknown>> | undefined) || [])]; timeline[index] = { ...(timeline[index] || {}), source_node_id: node.id, start: index * 3, end: Math.min(Number(selectedNode.data.desktopPayload?.duration || 10), index * 3 + 3), instruction: event.target.value }; updatePayload("timeline_images", timeline); }} /></div>)}</div>}
            {selectedNode.data.specKey === "storyboard" && <div className="production-controls"><label><span>制片方式</span><select value={String(selectedNode.data.desktopPayload?.automation_mode || "checkpoints")} onChange={(event) => updatePayload("automation_mode", event.target.value)}><option value="checkpoints">关键节点确认（推荐）</option><option value="auto">全自动</option><option value="manual">逐步控制</option></select></label><div><button onClick={() => void productionCommand("approve")}>审片通过并继续</button><button onClick={() => void productionCommand("accept_risk")}>接受风险并继续</button></div><div><button onClick={() => void productionCommand(selectedNode.data.desktopPayload?.production_status === "paused" ? "resume" : "pause")}>{selectedNode.data.desktopPayload?.production_status === "paused" ? "继续已暂停流程" : "暂停流程"}</button><select aria-label="重做阶段" value={Number(selectedNode.data.desktopPayload?.rewind_stage || 1)} onChange={(event) => updatePayload("rewind_stage", Number(event.target.value))}>{[1,2,3,4,5,6,7].map((stage) => <option key={stage} value={stage}>从第 {stage} 阶段重做</option>)}</select><button onClick={() => void productionCommand("rewind", Number(selectedNode.data.desktopPayload?.rewind_stage || 1))}>确认重做</button></div></div>}
            <div className="reference-box"><div><strong>参考输入 · {incomingNodes.length}</strong><span>{incomingNodes.length ? incomingNodes.map((node) => node.data.title).join("、") : "从其他节点连线后自动出现"}</span></div><button onClick={() => importInputRef.current?.click()}><Upload size={15} /> 导入</button></div>
            <div className="node-actions"><span>桌面端一致操作</span>{(NODE_SPEC_BY_KEY[selectedNode.data.specKey]?.actions || []).map((action) => <button className={selectedNode.data.desktopPayload?.editor_action === action ? "is-active" : ""} key={action} onClick={() => { updatePayload("editor_action", action); setNotice(`已选择“${action}”`); }}>{action}</button>)}</div>
            <button className="generate-button" onClick={() => void submitSelected()}><WandSparkles size={17} /> 生成当前节点</button>
            <p className="cost-note">提交前会显示模型、预计耗时和费用，不会静默切换模型。</p>
          </div>}
        </aside>
      </section>
      {adminOpen && <AdminPanel onClose={() => setAdminOpen(false)} />}
    </main>
  );
}

export function StudioCanvas() {
  return <ReactFlowProvider><CanvasApp /></ReactFlowProvider>;
}
