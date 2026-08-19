import { z } from "zod";

export const webCanvasSchema = z.object({
  protocol: z.literal("creative-engine-canvas"),
  version: z.number().int().positive(),
  nodes: z.array(z.object({
    id: z.string(),
    type: z.string(),
    position: z.object({ x: z.number(), y: z.number() }),
    data: z.record(z.string(), z.unknown()),
  }).passthrough()),
  edges: z.array(z.object({
    id: z.string(), source: z.string(), target: z.string(),
  }).passthrough()),
  storyboard: z.record(z.string(), z.unknown()).optional(),
  desktopSource: z.record(z.string(), z.unknown()).optional(),
});

export type WebCanvasDocument = z.infer<typeof webCanvasSchema>;

const accents: Record<string, string> = {
  storyboard: "#8b7cff", script: "#9aa6b5", copywriting: "#f07daf",
  image: "#50b9dd", director: "#6f8cff", video: "#f1a85b",
  audio: "#66d49a", analysis: "#b993ff",
};

function desktopKind(record: Record<string, unknown>) {
  const type = String(record.type || "");
  if (type === "storyboard_node") return "storyboard";
  if (type === "text_node") return record.copywriting_workbench ? "copywriting" : "script";
  if (type === "image_node") return "image";
  if (type === "video_node") return record.multi_image_director ? "director" : "video";
  if (type === "audio_node") return "audio";
  if (type === "video_analysis_node") return "analysis";
  return "script";
}

export function desktopProjectToWeb(input: unknown): { title: string; canvas: WebCanvasDocument } {
  const project = z.record(z.string(), z.unknown()).parse(input);
  if (project.format !== "creative-engine-production-project") {
    throw new Error("不是受支持的 Creative Engine 桌面工程文件");
  }
  const desktopCanvas = z.record(z.string(), z.unknown()).parse(project.canvas || {});
  const custom = Array.isArray(desktopCanvas.__custom_nodes__) ? desktopCanvas.__custom_nodes__ : [];
  const records = custom.filter((value): value is Record<string, unknown> => Boolean(value && typeof value === "object"));
  const nodes = records.map((record, index) => {
    const id = String(record.id || `desktop-${index}`);
    const position = Array.isArray(desktopCanvas[id]) ? desktopCanvas[id] as unknown[] : [];
    const kind = desktopKind(record);
    return {
      id, type: "studio",
      position: { x: Number(position[0] || 120 + (index % 4) * 340), y: Number(position[1] || 100 + Math.floor(index / 4) * 240) },
      data: {
        title: String(record.title || "未命名节点"),
        description: String(record.content || record.status || "从桌面工程导入"),
        kind,
        status: String(record.status || "已导入"),
        meta: "桌面工程节点",
        accent: accents[kind],
        desktopPayload: record,
      },
    };
  });
  const workflowEdges = Array.isArray(desktopCanvas.__workflow_edges__) ? desktopCanvas.__workflow_edges__ : [];
  const edges = workflowEdges.filter((value): value is Record<string, unknown> => Boolean(value && typeof value === "object"))
    .map((edge, index) => ({
      id: String(edge.id || `desktop-edge-${index}`),
      source: String(edge.source || ""), target: String(edge.target || ""),
      type: "smoothstep", animated: true,
      style: { stroke: "#66718a", strokeWidth: 1.6 },
    })).filter((edge) => edge.source && edge.target);
  return {
    title: String(project.title || "桌面导入工程"),
    canvas: webCanvasSchema.parse({
      protocol: "creative-engine-canvas", version: 1, nodes, edges,
      storyboard: project.storyboard && typeof project.storyboard === "object" ? project.storyboard : {},
      desktopSource: { format: project.format, version: project.version, projectId: project.project_id },
    }),
  };
}

export function toWebCanvas(nodes: unknown[], edges: unknown[]): WebCanvasDocument {
  return webCanvasSchema.parse({ protocol: "creative-engine-canvas", version: 1, nodes, edges });
}
