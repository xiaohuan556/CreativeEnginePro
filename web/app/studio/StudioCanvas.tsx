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
  WandSparkles, X, Zap,
} from "lucide-react";
import "@xyflow/react/dist/style.css";
import { desktopProjectToWeb, toWebCanvas } from "../../lib/canvas-protocol";

type NodeKind = "storyboard" | "script" | "copywriting" | "image" |
  "director" | "video" | "audio" | "analysis";

type StudioData = {
  title: string;
  description: string;
  kind: NodeKind;
  status: string;
  meta: string;
  accent: string;
  progress?: number;
};

type StudioNode = Node<StudioData, "studio">;

const kindIcons: Record<NodeKind, typeof Sparkles> = {
  storyboard: Sparkles,
  script: FileText,
  copywriting: Mic2,
  image: ImageIcon,
  director: Clapperboard,
  video: Film,
  audio: AudioLines,
  analysis: ScanSearch,
};

const initialNodes: StudioNode[] = [
  {
    id: "story", type: "studio", position: { x: 90, y: 95 },
    data: { title: "AI 故事板", description: "雨夜，一台送货机器人发现最后一封没有寄出的信。", kind: "storyboard", status: "已定稿", meta: "6 镜 · 42 秒", accent: "#8b7cff" },
  },
  {
    id: "images", type: "studio", position: { x: 430, y: 70 },
    data: { title: "多图生成图片", description: "锁定机器人身份、雨夜街道和暖黄色信封，生成统一视觉资产。", kind: "image", status: "4 张已采用", meta: "16:9 · Seedream", accent: "#50b9dd" },
  },
  {
    id: "director", type: "studio", position: { x: 790, y: 92 },
    data: { title: "多图导演视频", description: "0–4 秒推进，4 秒切近景，8 秒摇镜跟随机器人穿过积水。", kind: "director", status: "等待生成", meta: "12 秒 · Seedance", accent: "#6f8cff" },
  },
  {
    id: "video", type: "studio", position: { x: 1150, y: 74 },
    data: { title: "成片候选 01", description: "主体和场景连续性通过，镜头节奏与声音仍待审片。", kind: "video", status: "AI 审片中", meta: "1080p · 00:12", accent: "#f1a85b", progress: 72 },
  },
  {
    id: "copy", type: "studio", position: { x: 190, y: 430 },
    data: { title: "信息流口播文案", description: "开头 3 秒抓住注意力，中段呈现核心卖点，结尾保留行动号召。", kind: "copywriting", status: "中文原稿", meta: "98 字 · 预计 24 秒", accent: "#f07daf" },
  },
  {
    id: "audio", type: "studio", position: { x: 540, y: 450 },
    data: { title: "对白配音", description: "克制、温暖，句间停顿 0.5 秒，保留结尾轻微呼吸感。", kind: "audio", status: "可试听", meta: "女声 · 1.0×", accent: "#66d49a" },
  },
  {
    id: "analysis", type: "studio", position: { x: 935, y: 430 },
    data: { title: "AI 自动拉片", description: "识别切镜、景别、人物运动轨迹、运镜、节奏与声音事件。", kind: "analysis", status: "等待视频", meta: "运动轨迹增强版", accent: "#b993ff" },
  },
];

const initialEdges: Edge[] = [
  { id: "e-story-images", source: "story", target: "images" },
  { id: "e-images-director", source: "images", target: "director" },
  { id: "e-director-video", source: "director", target: "video" },
  { id: "e-copy-audio", source: "copy", target: "audio" },
  { id: "e-video-analysis", source: "video", target: "analysis" },
].map((edge) => ({
  ...edge, type: "smoothstep", animated: true,
  style: { stroke: "#66718a", strokeWidth: 1.6 },
  markerEnd: { type: MarkerType.ArrowClosed, color: "#66718a" },
}));

const creationItems: Array<{ kind: NodeKind; title: string; description: string; accent: string }> = [
  { kind: "storyboard", title: "AI 故事板", description: "一句故事自动拆镜并制片", accent: "#8b7cff" },
  { kind: "script", title: "剧本工作台", description: "写作、诊断、定稿和版本", accent: "#9aa6b5" },
  { kind: "copywriting", title: "信息流口播文案", description: "口播生成、改写和翻译", accent: "#f07daf" },
  { kind: "image", title: "多图生成图片", description: "按用途组合多张参考图", accent: "#50b9dd" },
  { kind: "director", title: "多图导演视频", description: "一次生成完整多镜头视频", accent: "#6f8cff" },
];

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

function CanvasApp() {
  const [nodes, setNodes, onNodesChange] = useNodesState<StudioNode>(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);
  const [selectedId, setSelectedId] = useState("director");
  const [createOpen, setCreateOpen] = useState(false);
  const [notice, setNotice] = useState("所有更改已保存");
  const [projectTitle, setProjectTitle] = useState("雨夜最后一封信");
  const [projectId, setProjectId] = useState("");
  const [hydrated, setHydrated] = useState(false);
  const versionRef = useRef(1);
  const bootingRef = useRef(false);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const importInputRef = useRef<HTMLInputElement>(null);

  const selectedNode = useMemo(() => nodes.find((node) => node.id === selectedId) ?? nodes[0], [nodes, selectedId]);

  useEffect(() => {
    if (bootingRef.current) return;
    bootingRef.current = true;
    void (async () => {
      try {
        const listing = await fetch("/api/projects", { cache: "no-store" });
        if (!listing.ok) throw new Error("项目列表不可用");
        const data = await listing.json() as { projects?: Array<{ id: string }> };
        const first = data.projects?.[0];
        if (first) {
          const response = await fetch(`/api/projects/${first.id}`, { cache: "no-store" });
          const detail = await response.json() as { project?: { id: string; title: string; version: number; canvas: { nodes: StudioNode[]; edges: Edge[] } } };
          if (detail.project) {
            setProjectId(detail.project.id); setProjectTitle(detail.project.title);
            versionRef.current = detail.project.version;
            setNodes(detail.project.canvas.nodes); setEdges(detail.project.canvas.edges);
          }
        } else {
          const response = await fetch("/api/projects", {
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
  }, [projectTitle, setEdges, setNodes]);

  useEffect(() => {
    if (!hydrated || !projectId) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    setNotice("正在保存…");
    saveTimerRef.current = setTimeout(() => {
      void fetch(`/api/projects/${projectId}`, {
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
  }, [edges, hydrated, nodes, projectId, projectTitle]);

  const onConnect = useCallback((connection: Connection) => {
    setEdges((current) => addEdge({
      ...connection, type: "smoothstep", animated: true,
      style: { stroke: "#77839c", strokeWidth: 1.6 },
      markerEnd: { type: MarkerType.ArrowClosed, color: "#77839c" },
    }, current));
    setNotice("连线已保存");
  }, [setEdges]);

  const addNode = (item: (typeof creationItems)[number]) => {
    const id = `${item.kind}-${Date.now()}`;
    const offset = nodes.length * 24;
    setNodes((current) => [...current, {
      id, type: "studio", position: { x: 380 + offset, y: 260 + offset },
      data: { title: item.title, description: item.description, kind: item.kind, status: "待设置", meta: "新建节点", accent: item.accent },
    }]);
    setSelectedId(id); setCreateOpen(false); setNotice(`${item.title}已创建`);
  };

  const updateSelected = (key: "title" | "description", value: string) => {
    setNodes((current) => current.map((node) => node.id === selectedId ? { ...node, data: { ...node.data, [key]: value } } : node));
    setNotice("正在保存…");
    window.setTimeout(() => setNotice("所有更改已保存"), 450);
  };

  const importDesktopProject = async (file: File) => {
    try {
      const imported = desktopProjectToWeb(JSON.parse(await file.text()));
      setNodes(imported.canvas.nodes as StudioNode[]);
      setEdges(imported.canvas.edges as Edge[]);
      setProjectTitle(imported.title);
      setSelectedId(String(imported.canvas.nodes[0]?.id || ""));
      setNotice("桌面工程已导入 · 正在保存");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "工程导入失败");
    }
  };

  return (
    <main className="studio-shell">
      <header className="topbar">
        <div className="brand-mark"><Clapperboard size={18} /></div>
        <div className="project-heading"><strong>Creative Engine</strong><span className="project-separator">/</span><button className="project-name">{projectTitle} <ChevronDown size={14} /></button></div>
        <div className="topbar-center"><span className="live-dot" /><span>{notice}</span></div>
        <div className="topbar-actions"><button className="team-button"><Users size={15} /> 制片组 · 8</button><button className="icon-button" aria-label="设置"><Settings2 size={17} /></button><div className="avatar">欢</div></div>
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
            nodes={nodes} edges={edges} nodeTypes={nodeTypes}
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
              <button><FolderOpen size={16} /> 工程</button><button onClick={() => importInputRef.current?.click()}><Import size={16} /> 导入</button>
              <button><LayoutGrid size={16} /> 整理</button><button><Boxes size={16} /> 资产</button>
            </nav>
            <input ref={importInputRef} className="hidden-file-input" type="file" accept=".cepstudio,.json,application/json" onChange={(event) => { const file = event.target.files?.[0]; if (file) void importDesktopProject(file); event.currentTarget.value = ""; }} />
            {createOpen && <div className="create-popover">
              <div className="popover-heading"><div><strong>创建画布节点</strong><span>高频创作能力</span></div><button className="icon-button" onClick={() => setCreateOpen(false)}><X size={16} /></button></div>
              <div className="creation-list">{creationItems.map((item) => { const Icon = kindIcons[item.kind]; return <button key={item.kind} onClick={() => addNode(item)}><span className="creation-icon" style={{ color: item.accent }}><Icon size={17} /></span><span><strong>{item.title}</strong><small>{item.description}</small></span></button>; })}</div>
              <div className="popover-submenus"><button>基础节点 <ChevronDown size={14} /></button><button>分析与专业工具 <ChevronDown size={14} /></button><button>参考节点 <ChevronDown size={14} /></button></div>
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
            <div className="field-row"><label><span>画面比例</span><button className="select-like">16:9 <ChevronDown size={13} /></button></label><label><span>候选数量</span><button className="select-like">2 个 <ChevronDown size={13} /></button></label></div>
            <div className="reference-box"><div><strong>参考输入</strong><span>从其他节点连线后自动出现</span></div><button><Upload size={15} /> 添加参考</button></div>
            <button className="generate-button"><WandSparkles size={17} /> 生成当前节点</button>
            <p className="cost-note">提交前会显示模型、预计耗时和费用，不会静默切换模型。</p>
          </div>}
        </aside>
      </section>
    </main>
  );
}

export function StudioCanvas() {
  return <ReactFlowProvider><CanvasApp /></ReactFlowProvider>;
}
