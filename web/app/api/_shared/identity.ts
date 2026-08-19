export type RequestIdentity = { userId: string; email: string; name: string };

export function getRequestIdentity(request: Request): RequestIdentity | null {
  const userId = request.headers.get("oai-authenticated-user-id");
  const email = request.headers.get("oai-authenticated-user-email");
  if (userId && email) {
    const encodedName = request.headers.get("oai-authenticated-user-full-name");
    let name = email;
    if (encodedName && request.headers.get("oai-authenticated-user-full-name-encoding") === "percent-encoded-utf-8") {
      try { name = decodeURIComponent(encodedName); } catch { name = email; }
    }
    return { userId, email, name };
  }
  if (process.env.NODE_ENV !== "production") {
    return { userId: "local-developer", email: "local@creative.engine", name: "本地开发者" };
  }
  return null;
}

export function unauthorized() {
  return Response.json({ error: "请先登录后再访问制片项目。" }, { status: 401 });
}
