import { ensureSchema } from "../../../../db/bootstrap";
import { getRawDb } from "../../../../db";
import { getRequestIdentity, unauthorized } from "../../_shared/identity";

async function canWrite(projectId: string, userId: string) {
  const row = await (await getRawDb()).prepare(
    "SELECT p.owner_id AS ownerId, pm.role AS memberRole FROM projects p LEFT JOIN project_members pm ON pm.project_id = p.id AND pm.user_id = ? WHERE p.id = ?"
  ).bind(userId, projectId).first<{ ownerId: string; memberRole: string | null }>();
  return Boolean(row && (row.ownerId === userId || row.memberRole === "owner" || row.memberRole === "editor"));
}

export async function GET(request: Request, context: { params: Promise<{ projectId: string }> }) {
  const identity = getRequestIdentity(request);
  if (!identity) return unauthorized();
  await ensureSchema();
  const { projectId } = await context.params;
  const row = await (await getRawDb()).prepare(
    "SELECT p.id, p.title, p.canvas_json AS canvasJson, p.source_format AS sourceFormat, p.version, p.updated_at AS updatedAt FROM projects p LEFT JOIN project_members pm ON pm.project_id = p.id AND pm.user_id = ? WHERE p.id = ? AND (p.owner_id = ? OR pm.user_id = ?) LIMIT 1"
  ).bind(identity.userId, projectId, identity.userId, identity.userId).first<Record<string, unknown>>();
  if (!row) return Response.json({ error: "项目不存在或无权访问" }, { status: 404 });
  return Response.json({ project: { ...row, canvas: JSON.parse(String(row.canvasJson || "{}")), canvasJson: undefined } });
}

export async function PATCH(request: Request, context: { params: Promise<{ projectId: string }> }) {
  const identity = getRequestIdentity(request);
  if (!identity) return unauthorized();
  await ensureSchema();
  const { projectId } = await context.params;
  if (!await canWrite(projectId, identity.userId)) return Response.json({ error: "只有项目编辑者可以保存" }, { status: 403 });
  const payload = await request.json() as { title?: string; canvas?: unknown; expectedVersion?: number };
  if (!payload.canvas || typeof payload.canvas !== "object") return Response.json({ error: "canvas is required" }, { status: 400 });
  const current = await (await getRawDb()).prepare("SELECT version FROM projects WHERE id = ?").bind(projectId).first<{ version: number }>();
  if (!current) return Response.json({ error: "项目不存在" }, { status: 404 });
  if (payload.expectedVersion && payload.expectedVersion !== current.version) {
    return Response.json({ error: "项目已被其他成员更新", currentVersion: current.version }, { status: 409 });
  }
  const version = current.version + 1;
  const now = Date.now();
  const canvasJson = JSON.stringify(payload.canvas);
  const db = await getRawDb();
  await db.batch([
    db.prepare("UPDATE projects SET title = COALESCE(?, title), canvas_json = ?, version = ?, updated_at = ? WHERE id = ?")
      .bind(payload.title?.trim() || null, canvasJson, version, now, projectId),
    db.prepare("INSERT INTO project_revisions (id, project_id, actor_id, version, canvas_json, created_at) VALUES (?, ?, ?, ?, ?, ?)")
      .bind(crypto.randomUUID(), projectId, identity.userId, version, canvasJson, now),
  ]);
  return Response.json({ project: { id: projectId, version, updatedAt: now } });
}
