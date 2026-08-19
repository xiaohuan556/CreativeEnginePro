"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { Check, KeyRound, LoaderCircle, ShieldCheck, UserPlus, Users, X } from "lucide-react";
import { useControlPlane } from "./ControlPlane";

type ManagedUser = {
  id: string; username: string; display_name: string; role: string; status: string;
  limits?: { daily_tasks: number; daily_credits: number; concurrent_tasks: number; allow_paid_models: boolean; allowed_models: string[] };
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

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const response = await apiFetch("/api/admin/users", { cache: "no-store" });
      const data = await response.json() as { users?: ManagedUser[]; detail?: string };
      if (!response.ok) throw new Error(data.detail || "账号列表读取失败");
      setUsers(data.users || []);
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

  return <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="公司账号管理"><section className="admin-panel"><header><div><ShieldCheck size={19} /><span><strong>公司账号与使用权限</strong><small>只有管理员可以创建、批准、停用或重置账号</small></span></div><button onClick={onClose} aria-label="关闭"><X size={18} /></button></header><div className="admin-body"><form className="admin-create" onSubmit={create}><div className="admin-section-title"><UserPlus size={15} /><span>创建或预批准账号</span></div><div className="admin-grid"><label><span>登录账号</span><input name="username" required placeholder="例如 editor.01" /></label><label><span>显示名称</span><input name="display_name" required /></label><label className="wide"><span>初始密码</span><input name="password" type="password" minLength={12} required autoComplete="new-password" placeholder="12 位以上，至少三类字符" /></label><label><span>角色</span><select name="role" defaultValue="producer">{roles.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label><span>每日任务</span><input name="daily_tasks" type="number" min="0" defaultValue="50" /></label><label><span>每日费用额度</span><input name="daily_credits" type="number" min="0" defaultValue="5000" /></label><label><span>最大并发</span><input name="concurrent_tasks" type="number" min="0" max="50" defaultValue="2" /></label><label className="wide"><span>允许模型（逗号分隔；留空为不限）</span><input name="allowed_models" placeholder="seedream-v4, seedance-2.0" /></label></div><div className="admin-checks"><label><input name="approved" type="checkbox" defaultChecked /> 创建后立即允许登录</label><label><input name="allow_paid_models" type="checkbox" /> 允许付费模型</label><button disabled={busy}>{busy ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />}确认创建</button></div></form><div className="admin-section-title"><Users size={15} /><span>已有账号 · {users.length}</span></div>{message && <p className="admin-message">{message}</p>}{busy && users.length === 0 ? <div className="admin-loading"><LoaderCircle className="spin" size={20} />读取账号…</div> : <div className="user-list">{users.map((user) => <form key={user.id} className="user-row" onSubmit={(event) => { event.preventDefault(); void save(user, event.currentTarget); }}><div className="user-identity"><span>{(user.display_name || user.username).slice(0, 1)}</span><div><strong>{user.username}</strong><input name="display_name" defaultValue={user.display_name} aria-label="显示名称" /></div></div><select name="role" defaultValue={user.role} aria-label="角色">{roles.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><select name="status" defaultValue={user.status} aria-label="状态"><option value="active">允许登录</option><option value="pending">等待批准</option><option value="suspended">已停用</option></select><div className="quota-fields"><input name="daily_tasks" type="number" min="0" defaultValue={user.limits?.daily_tasks ?? 0} title="每日任务" /><input name="daily_credits" type="number" min="0" defaultValue={user.limits?.daily_credits ?? 0} title="每日费用额度" /><input name="concurrent_tasks" type="number" min="0" defaultValue={user.limits?.concurrent_tasks ?? 0} title="并发任务" /></div><input className="models-field" name="allowed_models" defaultValue={(user.limits?.allowed_models || []).join(", ")} placeholder="允许模型；留空不限" /><label className="paid-check"><input name="allow_paid_models" type="checkbox" defaultChecked={user.limits?.allow_paid_models} />付费</label><div className="password-reset"><KeyRound size={13} /><input name="password" type="password" minLength={12} placeholder="新密码（可空）" autoComplete="new-password" /></div><button className="user-save" disabled={busy}>保存</button></form>)}</div>}</div></section></div>;
}
