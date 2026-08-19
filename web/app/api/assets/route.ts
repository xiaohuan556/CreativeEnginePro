import { env } from "cloudflare:workers";
import { ensureSchema } from "../../../db/bootstrap";
import { getRawDb } from "../../../db";
import { getRequestIdentity, unauthorized } from "../_shared/identity";

function mediaBucket() {
  return (env as unknown as { MEDIA: R2Bucket }).MEDIA;
}

async function canAccessProject(projectId: string, userId: string) {
  return getRawDb().prepare(
    "SELECT 1 AS allowed FROM projects p LEFT JOIN project_members pm ON pm.project_id = p.id AND pm.user_id = ? WHERE p.id = ? AND (p.owner_id = ? OR pm.user_id = ?) LIMIT 1"
  ).bind(userId, projectId, userId, userId).first();
}

export async function GET(request: Request) {
  const identity = getRequestIdentity(request);
  if (!identity) return unauthorized();
  const projectId = new URL(request.url).searchParams.get("projectId") || "";
  if (!projectId) return Response.json({ error: "projectId is required" }, { status: 400 });
  await ensureSchema();
  if (!await canAccessProject(projectId, identity.userId)) return Response.json({ error: "无权访问该项目" }, { status: 403 });
  const result = await getRawDb().prepare(
    "SELECT id, project_id AS projectId, node_id AS nodeId, name, kind, content_type AS contentType, size, status, metadata_json AS metadataJson, created_at AS createdAt FROM assets WHERE project_id = ? ORDER BY created_at DESC LIMIT 500"
  ).bind(projectId).all<Record<string, unknown>>();
  return Response.json({ assets: (result.results || []).map((row) => ({ ...row, metadata: JSON.parse(String(row.metadataJson || "{}")), metadataJson: undefined })) });
}

export async function POST(request: Request) {
  const identity = getRequestIdentity(request);
  if (!identity) return unauthorized();
  await ensureSchema();
  const form = await request.formData();
  const file = form.get("file");
  const projectId = String(form.get("projectId") || "");
  const nodeId = String(form.get("nodeId") || "");
  const kind = String(form.get("kind") || "reference");
  if (!(file instanceof File) || !projectId) return Response.json({ error: "file and projectId are required" }, { status: 400 });
  if (file.size > 100 * 1024 * 1024) return Response.json({ error: "网页直传暂时限制为 100MB；长视频将由独立服务器分片上传。" }, { status: 413 });
  if (!await canAccessProject(projectId, identity.userId)) return Response.json({ error: "无权写入该项目" }, { status: 403 });
  const id = crypto.randomUUID();
  const safeName = file.name.replace(/[^a-zA-Z0-9._-]+/g, "_").slice(-100) || "asset.bin";
  const objectKey = `projects/${projectId}/${id}/${safeName}`;
  await mediaBucket().put(objectKey, file.stream(), { httpMetadata: { contentType: file.type || "application/octet-stream" } });
  const now = Date.now();
  await getRawDb().prepare(
    "INSERT INTO assets (id, project_id, owner_id, node_id, name, kind, object_key, content_type, size, status, metadata_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', '{}', ?, ?)"
  ).bind(id, projectId, identity.userId, nodeId || null, file.name, kind, objectKey, file.type || "application/octet-stream", file.size, now, now).run();
  return Response.json({ asset: { id, projectId, nodeId, name: file.name, kind, contentType: file.type, size: file.size, status: "ready" } }, { status: 201 });
}
