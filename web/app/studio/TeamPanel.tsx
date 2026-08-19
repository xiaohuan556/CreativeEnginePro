"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { LoaderCircle, Trash2, UserPlus, Users, X } from "lucide-react";
import { useControlPlane } from "./ControlPlane";

type ProjectMember = { id: string; user_id: string; username: string; display_name: string; account_status: string; role: string };
const roleLabels: Record<string, string> = { owner: "所有者", editor: "可编辑", reviewer: "仅审片", viewer: "只读" };

export function TeamPanel({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const { apiFetch } = useControlPlane();
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [canManage, setCanManage] = useState(false);
  const [busy, setBusy] = useState(true);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    if (!projectId) return;
    setBusy(true);
    const response = await apiFetch(`/api/projects/${projectId}/members`, { cache: "no-store" });
    const data = await response.json() as { members?: ProjectMember[]; can_manage?: boolean; detail?: string };
    if (response.ok) { setMembers(data.members || []); setCanManage(Boolean(data.can_manage)); } else setMessage(data.detail || "成员读取失败");
    setBusy(false);
  }, [apiFetch, projectId]);

  useEffect(() => { const timer = window.setTimeout(() => { void load(); }, 0); return () => window.clearTimeout(timer); }, [load]);

  const addMember = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setBusy(true); setMessage(""); const form = new FormData(event.currentTarget);
    const response = await apiFetch(`/api/projects/${projectId}/members`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username: form.get("username"), role: form.get("role") }) });
    const data = await response.json() as { detail?: string };
    if (!response.ok) setMessage(data.detail || "成员添加失败"); else { event.currentTarget.reset(); setMessage("成员权限已保存"); await load(); }
    setBusy(false);
  };

  const changeRole = async (member: ProjectMember, role: string) => {
    setBusy(true); const response = await apiFetch(`/api/projects/${projectId}/members/${member.id}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ role }) });
    const data = await response.json() as { detail?: string }; if (!response.ok) setMessage(data.detail || "权限更新失败"); else await load(); setBusy(false);
  };

  const remove = async (member: ProjectMember) => {
    setBusy(true); const response = await apiFetch(`/api/projects/${projectId}/members/${member.id}`, { method: "DELETE" });
    const data = await response.json() as { detail?: string }; if (!response.ok) setMessage(data.detail || "移除失败"); else { setMessage(`${member.display_name || member.username} 已移出项目`); await load(); } setBusy(false);
  };

  return <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="项目协作成员"><section className="team-panel"><header><div><Users size={19} /><span><strong>项目协作成员</strong><small>账号必须先由管理员创建并批准；资产和任务权限仍由服务端校验</small></span></div><button onClick={onClose} aria-label="关闭"><X size={18} /></button></header><div className="team-body">{canManage && <form className="team-invite" onSubmit={addMember}><div><UserPlus size={15} /><span>添加已有公司账号</span></div><input name="username" required placeholder="登录账号，例如 editor.01" /><select name="role" defaultValue="editor"><option value="editor">可编辑</option><option value="reviewer">仅审片</option><option value="viewer">只读</option></select><button disabled={busy}>添加</button></form>}{!canManage && <p className="team-readonly">你可以查看项目成员；只有项目所有者或管理员能调整权限。</p>}{message && <p className="admin-message">{message}</p>}{busy && !members.length ? <div className="admin-loading"><LoaderCircle className="spin" size={19} />读取成员…</div> : <div className="member-list">{members.map((member) => <article key={member.id}><span className="member-avatar">{(member.display_name || member.username).slice(0, 1)}</span><div><strong>{member.display_name || member.username}</strong><small>@{member.username} · {member.account_status === "active" ? "账号正常" : "账号已停用"}</small></div>{member.role === "owner" || !canManage ? <span className="owner-badge">{roleLabels[member.role]}</span> : <><select value={member.role} aria-label={`${member.username}项目权限`} disabled={busy} onChange={(event) => void changeRole(member, event.target.value)}><option value="editor">可编辑</option><option value="reviewer">仅审片</option><option value="viewer">只读</option></select><button className="member-remove" aria-label={`移除${member.username}`} onClick={() => void remove(member)}><Trash2 size={14} /></button></>}</article>)}</div>}</div></section></div>;
}
