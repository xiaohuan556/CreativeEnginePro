"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { Activity, Check, KeyRound, LoaderCircle, ShieldCheck, UserPlus, Users, X } from "lucide-react";
import { useControlPlane } from "./ControlPlane";

type ManagedUser = {
  id: string; username: string; display_name: string; role: string; status: string;
  active_sessions?: number; last_login_at?: string | null;
  limits?: { daily_tasks: number; daily_credits: number; concurrent_tasks: number; allow_paid_models: boolean; allowed_models: string[] };
};
type UsageRow = { id: string; username: string; display_name: string; tasks: number; credits: number };
type AuditEvent = { id: string; action: string; target_type: string; target_id: string; ip_address: string; created_at: string };
type Readiness = { ready: boolean; control_ready?: boolean; generation_ready?: boolean; database: boolean; storage: boolean; active_workers: number; providers: string[]; missing_capabilities?: string[]; storage_error?: string };

const capabilityNames: Record<string, string> = {
  chat: "脚本/文案", text_to_image: "文生图", image_edit: "图生图",
  text_to_video: "文生视频", image_to_video: "图生视频/续拍", text_to_speech: "配音",
};

const roles = [
  ["producer", "制片人"], ["director", "导演"], ["editor", "编辑"],
  ["reviewer", "审片"], ["viewer", "只读"], ["admin", "管理员"],
];

export function AdminPanel({ onClose }: { onClose: () => void }) {
  const { apiFetch } = useControlPlane();
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [busy, setBusy] = useState(true);
  const [message, setMessage] = useState("");
  const [usage, setUsage] = useState<UsageRow[]>([]);
  const [statuses, setStatuses] = useState<Record<string, number>>({});
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [readiness, setReadiness] = useState<Readiness | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [response, usageResponse, auditResponse, readinessResponse] = await Promise.all([
        apiFetch("/api/admin/users", { cache: "no-store" }),
        apiFetch("/api/admin/usage", { cache: "no-store" }),
        apiFetch("/api/admin/audit?limit=20", { cache: "no-store" }),
        apiFetch("/api/admin/readiness", { cache: "no-store" }),
      ]);
      const data = await response.json() as { users?: ManagedUser[]; detail?: string };
      if (!response.ok) throw new Error(data.detail || "账号列表读取失败");
      setUsers(data.users || []);
      if (usageResponse.ok) {
        const payload = await usageResponse.json() as { users?: UsageRow[]; statuses?: Record<string, number> };
        setUsage(payload.users || []); setStatuses(payload.statuses || {});
      }
      if (auditResponse.ok) {
        const payload = await auditResponse.json() as { events?: AuditEvent[] };
        setEvents(payload.events || []);
      }
      if (readinessResponse.ok) setReadiness(await readinessResponse.json() as Readiness);
    } catch (error) { setMessage(error instanceof Error ? error.message : "账号列表读取失败"); }
    finally { setBusy(false); }
  }, [apiFetch]);

  useEffect(() => {
    const timer = window.setTimeout(() => { void load(); }, 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setBusy(true); setMessage("");
    const form = new FormData(event.currentTarget);
    const response = await apiFetch("/api/admin/users", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({
      username: form.get("username"), display_name: form.get("display_name"), password: form.get("password"), role: form.get("role"), approved: form.get("approved") === "on",
      daily_tasks: Number(form.get("daily_tasks")), daily_credits: Number(form.get("daily_credits")), concurrent_tasks: Number(form.get("concurrent_tasks")), allow_paid_models: form.get("allow_paid_models") === "on",
      allowed_models: String(form.get("allowed_models") || "").split(",").map((item) => item.trim()).filter(Boolean),
    }) });
    const data = await response.json() as { detail?: string };
    if (!response.ok) setMessage(data.detail || "账号创建失败");
    else { event.currentTarget.reset(); setMessage("账号已创建，状态和额度已生效"); await load(); }
    setBusy(false);
  };

  const save = async (user: ManagedUser, form: HTMLFormElement) => {
    setBusy(true); setMessage(""); const data = new FormData(form); const password = String(data.get("password") || "");
    const response = await apiFetch(`/api/admin/users/${user.id}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({
      display_name: data.get("display_name"), role: data.get("role"), status: data.get("status"), ...(password ? { password } : {}),
      daily_tasks: Number(data.get("daily_tasks")), daily_credits: Number(data.get("daily_credits")), concurrent_tasks: Number(data.get("concurrent_tasks")), allow_paid_models: data.get("allow_paid_models") === "on",
      allowed_models: String(data.get("allowed_models") || "").split(",").map((item) => item.trim()).filter(Boolean),
    }) });
    const payload = await response.json() as { detail?: string };
    setMessage(response.ok ? `账号 ${user.username} 已更新` : payload.detail || "账号更新失败");
    if (response.ok) await load(); setBusy(false);
  };

  const forceLogout = async (user: ManagedUser) => {
    setBusy(true); setMessage("");
    const response = await apiFetch(`/api/admin/users/${user.id}/revoke-sessions`, { method: "POST" });
    const data = await response.json() as { revoked?: number; detail?: string };
    setMessage(response.ok ? `${user.username} 的 ${data.revoked || 0} 个登录会话已下线` : data.detail || "会话下线失败");
    if (response.ok) await load(); setBusy(false);
  };

  const totalTasks = Object.values(statuses).reduce((sum, value) => sum + value, 0);
  const totalCredits = usage.reduce((sum, item) => sum + Number(item.credits), 0);
  const loginFailures = events.filter((item) => item.action === "auth.login_failed").length;

  return <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="公司账号管理">
    <section className="admin-panel">
      <header><div><ShieldCheck size={19} /><span><strong>公司账号与使用权限</strong><small>只有管理员可以创建、批准、停用或重置账号</small></span></div><button onClick={onClose} aria-label="关闭"><X size={18} /></button></header>
      <div className="admin-body">
        <section className="admin-observability">
          <div className="admin-section-title"><Activity size={15} /><span>最近 24 小时使用与安全记录</span></div>
          <div className="usage-cards"><article><strong>{totalTasks}</strong><span>任务总数</span></article><article><strong>{totalCredits}</strong><span>预估额度</span></article><article><strong>{statuses.failed || 0}</strong><span>失败任务</span></article><article><strong>{loginFailures}</strong><span>近期登录失败</span></article></div>
          <div className={`system-readiness ${readiness?.ready ? "is-ready" : "is-warning"}`}><strong>{readiness?.ready ? "完整制片能力已就绪" : readiness?.control_ready ? "控制层正常，生成能力未配齐" : "生产服务待检查"}</strong><span>数据库 {readiness?.database ? "正常" : "异常"} · 媒体存储 {readiness?.storage ? "正常" : "异常"} · Worker {readiness?.active_workers ?? 0} 个在线 · 已配置引擎 {readiness?.providers?.join("、") || "无外部引擎"}</span>{Boolean(readiness?.missing_capabilities?.length) && <small>尚缺：{readiness!.missing_capabilities!.map((item) => capabilityNames[item] || item).join("、")}</small>}{readiness?.storage_error && <small>{readiness.storage_error}</small>}</div>
          <div className="usage-detail"><div>{usage.length ? usage.slice(0, 8).map((item) => <span key={item.id}><strong>{item.display_name || item.username}</strong>{item.tasks} 次 · {item.credits} credits</span>) : <span>最近 24 小时暂无模型任务</span>}</div><div>{events.length ? events.slice(0, 8).map((item) => <span key={item.id}><strong>{item.action}</strong>{new Date(item.created_at).toLocaleString("zh-CN")} · {item.ip_address || "内部"}</span>) : <span>暂无审计记录</span>}</div></div>
        </section>

        <form className="admin-create" onSubmit={create}>
          <div className="admin-section-title"><UserPlus size={15} /><span>创建或预批准账号</span></div>
          <div className="admin-grid">
            <label><span>登录账号</span><input name="username" required placeholder="例如 editor.01" /></label><label><span>显示名称</span><input name="display_name" required /></label>
            <label className="wide"><span>初始密码</span><input name="password" type="password" minLength={12} required autoComplete="new-password" placeholder="12 位以上，至少三类字符" /></label>
            <label><span>角色</span><select name="role" defaultValue="producer">{roles.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label><span>每日任务</span><input name="daily_tasks" type="number" min="0" defaultValue="0" /></label><label><span>每日费用额度</span><input name="daily_credits" type="number" min="0" defaultValue="0" /></label><label><span>最大并发</span><input name="concurrent_tasks" type="number" min="0" max="50" defaultValue="0" /></label>
            <label className="wide"><span>允许引擎或 provider:model（逗号分隔；留空则禁止外部模型）</span><input name="allowed_models" placeholder="seedream, seedance, deepseek" /></label>
          </div>
          <div className="admin-checks"><label><input name="approved" type="checkbox" /> 我已确认账号和密码，创建后允许登录</label><label><input name="allow_paid_models" type="checkbox" /> 允许付费模型</label><button disabled={busy}>{busy ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />}确认创建</button></div>
        </form>

        <div className="admin-section-title"><Users size={15} /><span>已有账号 · {users.length}</span></div>
        {message && <p className="admin-message">{message}</p>}
        {busy && users.length === 0 ? <div className="admin-loading"><LoaderCircle className="spin" size={20} />读取账号…</div> : <div className="user-list">{users.map((user) =>
          <form key={user.id} className="user-row" onSubmit={(event) => { event.preventDefault(); void save(user, event.currentTarget); }}>
            <div className="user-identity"><span>{(user.display_name || user.username).slice(0, 1)}</span><div><strong>{user.username}</strong><small>{user.active_sessions || 0} 个在线会话{user.last_login_at ? ` · ${new Date(user.last_login_at).toLocaleDateString("zh-CN")}` : " · 尚未登录"}</small><input name="display_name" defaultValue={user.display_name} aria-label="显示名称" /></div></div>
            <select name="role" defaultValue={user.role} aria-label="角色">{roles.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
            <select name="status" defaultValue={user.status} aria-label="状态"><option value="active">允许登录</option><option value="pending">等待批准</option><option value="suspended">已停用</option></select>
            <div className="quota-fields"><input name="daily_tasks" type="number" min="0" defaultValue={user.limits?.daily_tasks ?? 0} title="每日任务" /><input name="daily_credits" type="number" min="0" defaultValue={user.limits?.daily_credits ?? 0} title="每日费用额度" /><input name="concurrent_tasks" type="number" min="0" defaultValue={user.limits?.concurrent_tasks ?? 0} title="并发任务" /></div>
            <input className="models-field" name="allowed_models" defaultValue={(user.limits?.allowed_models || []).join(", ")} placeholder="允许引擎；留空禁用" />
            <label className="paid-check"><input name="allow_paid_models" type="checkbox" defaultChecked={user.limits?.allow_paid_models} />付费</label>
            <div className="password-reset"><KeyRound size={13} /><input name="password" type="password" minLength={12} placeholder="新密码（可空）" autoComplete="new-password" /></div><div className="user-row-actions"><button type="button" disabled={busy || !user.active_sessions} onClick={() => void forceLogout(user)}>下线</button><button className="user-save" disabled={busy}>保存</button></div>
          </form>)}</div>}
      </div>
    </section>
  </div>;
}
