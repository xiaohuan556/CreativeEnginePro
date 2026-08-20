import { ensureSchema } from "../../../db/bootstrap";
import { getRawDb } from "../../../db";
import { getRequestIdentity, unauthorized } from "../_shared/identity";

export async function GET(request: Request) {
  const identity = getRequestIdentity(request);
  if (!identity) return unauthorized();
  await ensureSchema();
  const result = await (await getRawDb()).prepare(
    "SELECT id, title, source_format AS sourceFormat, version, created_at AS createdAt, updated_at AS updatedAt FROM projects WHERE owner_id = ? OR id IN (SELECT project_id FROM project_members WHERE user_id = ?) ORDER BY updated_at DESC LIMIT 100"
  ).bind(identity.userId, identity.userId).all();
  return Response.json({ projects: result.results ?? [], user: identity });
}

export async function POST(request: Request) {
  const identity = getRequestIdentity(request);
  if (!identity) return unauthorized();
  const payload = await request.json() as { id?: string; title?: string; canvas?: unknown; sourceFormat?: string };
  if (!payload.canvas || typeof payload.canvas !== "object") {
    return Response.json({ error: "canvas is required" }, { status: 400 });
  }
  await ensureSchema();
  const id = payload.id?.trim() || crypto.randomUUID();
  const title = payload.title?.trim() || "未命名制片工程";
  const now = Date.now();
  const canvasJson = JSON.stringify(payload.canvas);
  const db = await getRawDb();
  await db.batch([
    db.prepare("INSERT INTO projects (id, owner_id, title, canvas_json, source_format, version, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)")
      .bind(id, identity.userId, title, canvasJson, payload.sourceFormat || "creative-engine-web", now, now),
    db.prepare("INSERT OR IGNORE INTO project_members (id, project_id, user_id, email, role, created_at) VALUES (?, ?, ?, ?, 'owner', ?)")
      .bind(`${id}:${identity.userId}`, id, identity.userId, identity.email, now),
    db.prepare("INSERT INTO project_revisions (id, project_id, actor_id, version, canvas_json, created_at) VALUES (?, ?, ?, 1, ?, ?)")
      .bind(crypto.randomUUID(), id, identity.userId, canvasJson, now),
  ]);
  return Response.json({ project: { id, title, version: 1, updatedAt: now } }, { status: 201 });
}
