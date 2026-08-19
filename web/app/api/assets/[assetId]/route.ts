import { env } from "cloudflare:workers";
import { ensureSchema } from "../../../../db/bootstrap";
import { getRawDb } from "../../../../db";
import { getRequestIdentity, unauthorized } from "../../_shared/identity";

export async function GET(request: Request, context: { params: Promise<{ assetId: string }> }) {
  const identity = getRequestIdentity(request);
  if (!identity) return unauthorized();
  await ensureSchema();
  const { assetId } = await context.params;
  const row = await getRawDb().prepare(
    "SELECT a.object_key AS objectKey, a.name, a.content_type AS contentType FROM assets a JOIN projects p ON p.id = a.project_id LEFT JOIN project_members pm ON pm.project_id = p.id AND pm.user_id = ? WHERE a.id = ? AND (p.owner_id = ? OR pm.user_id = ?) LIMIT 1"
  ).bind(identity.userId, assetId, identity.userId, identity.userId).first<{ objectKey: string; name: string; contentType: string }>();
  if (!row) return Response.json({ error: "素材不存在或无权访问" }, { status: 404 });
  const bucket = (env as unknown as { MEDIA: R2Bucket }).MEDIA;
  const object = await bucket.get(row.objectKey);
  if (!object) return Response.json({ error: "素材文件不存在" }, { status: 404 });
  const headers = new Headers();
  object.writeHttpMetadata(headers);
  headers.set("content-type", row.contentType);
  headers.set("content-disposition", `inline; filename*=UTF-8''${encodeURIComponent(row.name)}`);
  headers.set("etag", object.httpEtag);
  return new Response(object.body, { headers });
}
