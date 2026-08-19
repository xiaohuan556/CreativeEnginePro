"use client";

import { createContext, FormEvent, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { Clapperboard, KeyRound, LoaderCircle, LockKeyhole, ShieldCheck } from "lucide-react";

type ControlUser = { id: string; username: string; display_name: string; role: string; status: string };
type ControlContextValue = {
  controlled: boolean;
  user: ControlUser;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  signOut: () => Promise<void>;
};

const defaultUser: ControlUser = { id: "platform", username: "platform", display_name: "制片人", role: "owner", status: "active" };
const ControlContext = createContext<ControlContextValue>({ controlled: false, user: defaultUser, apiFetch: fetch, signOut: async () => {} });

export function useControlPlane() { return useContext(ControlContext); }

export function ControlPlane({ children }: { children: ReactNode }) {
  const base = (process.env.NEXT_PUBLIC_CONTROL_PLANE_URL || "").replace(/\/$/, "");
  const [state, setState] = useState<"checking" | "signed-out" | "ready">(base ? "checking" : "ready");
  const [user, setUser] = useState<ControlUser>(defaultUser);
  const [csrf, setCsrf] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const rawFetch = useCallback((path: string, init: RequestInit = {}) => fetch(`${base}${path}`, { ...init, credentials: "include" }), [base]);
  const apiFetch = useCallback((path: string, init: RequestInit = {}) => {
    if (!base) return fetch(path, init);
    const headers = new Headers(init.headers);
    const method = String(init.method || "GET").toUpperCase();
    if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrf) headers.set("x-csrf-token", csrf);
    return rawFetch(path, { ...init, headers });
  }, [base, csrf, rawFetch]);

  useEffect(() => {
    if (!base) return;
    void (async () => {
      try {
        const me = await rawFetch("/api/auth/me", { cache: "no-store" });
        if (!me.ok) { setState("signed-out"); return; }
        const meData = await me.json() as { user: ControlUser };
        const token = await rawFetch("/api/auth/csrf", { cache: "no-store" });
        if (!token.ok) { setState("signed-out"); return; }
        const tokenData = await token.json() as { csrf_token: string };
        setUser(meData.user); setCsrf(tokenData.csrf_token); setState("ready");
      } catch { setError("暂时无法连接公司制片服务器"); setState("signed-out"); }
    })();
  }, [base, rawFetch]);

  const login = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setBusy(true); setError("");
    const form = new FormData(event.currentTarget);
    try {
      const response = await rawFetch("/api/auth/login", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username: form.get("username"), password: form.get("password") }) });
      const data = await response.json() as { user?: ControlUser; csrf_token?: string; detail?: string };
      if (!response.ok || !data.user || !data.csrf_token) throw new Error(data.detail || "登录失败");
      setUser(data.user); setCsrf(data.csrf_token); setState("ready");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "登录失败"); }
    finally { setBusy(false); }
  };

  const signOut = useCallback(async () => {
    if (!base) return;
    await apiFetch("/api/auth/logout", { method: "POST" });
    setCsrf(""); setUser(defaultUser); setState("signed-out");
  }, [apiFetch, base]);

  const value = useMemo(() => ({ controlled: Boolean(base), user, apiFetch, signOut }), [apiFetch, base, signOut, user]);
  if (state === "checking") return <main className="auth-shell"><div className="auth-checking"><LoaderCircle className="spin" size={24} /><span>正在验证公司账号…</span></div></main>;
  if (state === "signed-out") return <main className="auth-shell"><section className="login-card"><div className="login-brand"><span><Clapperboard size={21} /></span><div><strong>Creative Engine</strong><small>公司 AI 制片画布</small></div></div><div className="login-title"><ShieldCheck size={19} /><div><h1>登录制片工作区</h1><p>账号由管理员创建或批准，不开放自行注册。</p></div></div><form onSubmit={login}><label><span>账号</span><div><KeyRound size={15} /><input name="username" autoComplete="username" required /></div></label><label><span>密码</span><div><LockKeyhole size={15} /><input name="password" type="password" autoComplete="current-password" required /></div></label>{error && <p className="login-error">{error}</p>}<button disabled={busy}>{busy ? <LoaderCircle className="spin" size={16} /> : <ShieldCheck size={16} />}{busy ? "正在登录…" : "安全登录"}</button></form><p className="login-policy">登录、模型调用、费用和资产操作会记入审计日志；模型密钥不会发送到浏览器。</p></section></main>;
  return <ControlContext.Provider value={value}>{children}</ControlContext.Provider>;
}
