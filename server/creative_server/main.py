from __future__ import annotations

import hashlib
import json
import tempfile
from contextlib import asynccontextmanager
from datetime import timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .audit import client_ip, record_audit
from .config import Settings, get_settings
from .database import create_schema, get_db
from .dependencies import current_user, require_admin, require_csrf
from .models import Asset, AuditLog, GenerationTask, LoginSession, LoginThrottle, ProductionEvent, ProductionRun, Project, ProjectMember, ProjectRevision, ServiceHeartbeat, UsageLimit, User, WorkflowRun, WorkflowTemplate
from .production import handle_command
from .production_state import STAGES
from .provider_catalog import available_providers, resolve_provider_model
from .schemas import LoginRequest, ProductionCommand, ProductionRunCreate, ProjectCreate, ProjectMemberCreate, ProjectMemberUpdate, ProjectReviewCreate, ProjectUpdate, TaskCreate, UserCreate, UserUpdate, WorkflowCommand, WorkflowRunCreate, WorkflowTemplateCreate
from .security import DUMMY_PASSWORD_HASH, expiry, hash_password, opaque_token, token_hash, utcnow, validate_password, validate_username, verify_password
from .storage import media_kind, resolve_object, save_upload
from .task_policy import enforce_existing_task_policy, enforce_task_policy, enforce_workflow_policy, estimate_task_credits, require_project_read, require_project_review, require_project_write
from .task_validation import validate_task_request
from .workflows import command_workflow, initialize_workflow_tasks, prepare_workflow_items, public_workflow_run


REQUIRED_GENERATION_CAPABILITIES = ("chat", "text_to_image", "image_edit", "text_to_video", "image_to_video", "text_to_speech")


def _aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def public_user(user: User, limits: UsageLimit | None = None) -> dict:
    payload = {"id": user.id, "username": user.username, "display_name": user.display_name, "role": user.role, "status": user.status, "created_at": user.created_at.isoformat()}
    if limits:
        payload["limits"] = {"daily_tasks": limits.daily_tasks, "daily_credits": limits.daily_credits, "concurrent_tasks": limits.concurrent_tasks, "allow_paid_models": limits.allow_paid_models, "allowed_models": json.loads(limits.allowed_models_json or "[]")}
    return payload


def public_project(project: Project) -> dict:
    return {"id": project.id, "title": project.title, "owner_id": project.owner_id, "version": project.version, "canvas": json.loads(project.canvas_json), "created_at": project.created_at.isoformat(), "updated_at": project.updated_at.isoformat()}


def public_asset(asset: Asset) -> dict:
    return {"id": asset.id, "project_id": asset.project_id, "node_id": asset.node_id, "name": asset.name, "kind": asset.kind, "content_type": asset.content_type, "size": asset.size, "sha256": asset.sha256, "status": asset.status, "in_library": asset.in_library, "metadata": json.loads(asset.metadata_json or "{}"), "url": f"/api/assets/{asset.id}", "created_at": asset.created_at.isoformat()}


def public_run(run: ProductionRun) -> dict:
    return {"id": run.id, "project_id": run.project_id, "node_id": run.node_id, "automation_mode": run.automation_mode, "provider_locks": json.loads(run.provider_locks_json or "{}"), "status": run.status, "resume_status": run.resume_status, "stage": run.stage, "stage_name": STAGES.get(run.stage, ""), "completed_stage": run.completed_stage, "active_task_id": run.active_task_id, "risk_accepted_stages": json.loads(run.risk_accepted_json or "[]"), "error_message": run.error_message, "updated_at": run.updated_at.isoformat()}


def quote_production_plan(db: Session, user: User, supplied_locks: dict[str, str]) -> dict:
    """Validate and quote the complete seven-stage run before any paid task exists."""
    locks = {str(key): str(value or "").strip() for key, value in supplied_locks.items()}
    required_locks = ("planning", "planning_model", "image", "image_model", "video", "video_model")
    missing_locks = [key for key in required_locks if not locks.get(key)]
    if missing_locks:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"开始制片前必须明确锁定引擎和模型：{', '.join(missing_locks)}")
    profiles = {item["name"]: item for item in available_providers()}
    stages = []
    requests = []
    stage_specs = {
        1: (locks["planning"], locks["planning_model"], "chat"),
        2: (locks["image"], locks["image_model"], "text_to_image"),
        3: (locks["planning"], locks["planning_model"], "chat"),
        4: (locks["planning"], locks["planning_model"], "chat"),
        5: (locks["image"], locks["image_model"], "text_to_image"),
        6: (locks["video"], locks["video_model"], "image_to_video"),
        7: ("edge_tts", "", "text_to_speech"),
    }
    for stage, (provider, requested_model, operation) in stage_specs.items():
        profile = profiles.get(provider)
        if not profile or operation not in profile.get("capabilities", []):
            raise HTTPException(status.HTTP_409_CONFLICT, f"第 {stage} 阶段锁定引擎 {provider} 当前不可用或不支持 {operation}；流程未创建")
        model = resolve_provider_model(provider, requested_model)
        credits = estimate_task_credits(operation, provider)
        stages.append({"stage": stage, "name": STAGES[stage], "provider": provider, "model": model, "operation": operation, "credits": credits})
        requests.append((provider, model, credits))
    enforce_workflow_policy(db, user, requests)
    return {"credits": sum(item[2] for item in requests), "tasks": len(stages), "locks": locks, "stages": stages}


def require_project_membership_admin(db: Session, project_id: str, user: User) -> Project:
    project = db.get(Project, project_id)
    if not project: raise HTTPException(status.HTTP_404_NOT_FOUND, "项目不存在")
    if project.owner_id != user.id and user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只有项目所有者或管理员可以管理成员")
    return project


@asynccontextmanager
async def lifespan(_: FastAPI):
    create_schema()
    yield


settings = get_settings()
app = FastAPI(title="Creative Engine Control Plane", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[settings.public_origin], allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["content-type", "x-csrf-token", "idempotency-key"])


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "creative-engine-control-plane"}


@app.get("/ready")
def readiness(db: Session = Depends(get_db), config: Settings = Depends(get_settings)) -> dict:
    try:
        db.execute(select(1))
        storage = Path(config.storage_dir); storage.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".readiness-", dir=storage, delete=True) as probe:
            probe.write(b"ok"); probe.flush()
    except Exception as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"数据库或媒体存储尚未就绪：{str(error)[:240]}") from error
    return {"status": "ready", "database": "ok", "storage": "ok"}


@app.post("/api/auth/login")
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db), config: Settings = Depends(get_settings)) -> dict:
    username = payload.username.strip().lower()
    ip = client_ip(request)
    throttle_key = hashlib.sha256(f"account\0{username}\0{ip}".encode()).hexdigest()
    ip_throttle_key = hashlib.sha256(f"ip\0{ip}".encode()).hexdigest()
    throttle = db.get(LoginThrottle, throttle_key)
    ip_throttle = db.get(LoginThrottle, ip_throttle_key)
    now = utcnow()
    if any(item and item.blocked_until and _aware(item.blocked_until) > now for item in (throttle, ip_throttle)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "登录尝试过多，请稍后再试")
    user = db.scalar(select(User).where(User.username == username))
    valid = verify_password(user.password_hash if user else DUMMY_PASSWORD_HASH, payload.password)
    if not valid or not user or user.status != "active" or (user.locked_until and _aware(user.locked_until) > now):
        throttle = throttle or LoginThrottle(key_hash=throttle_key, window_started_at=now, failures=0)
        if _aware(throttle.window_started_at) < now - timedelta(minutes=config.login_lock_minutes):
            throttle.failures = 0; throttle.window_started_at = now
        throttle.failures = int(throttle.failures or 0) + 1
        if throttle.failures >= config.login_max_failures:
            throttle.blocked_until = now + timedelta(minutes=config.login_lock_minutes)
        db.add(throttle)
        ip_throttle = ip_throttle or LoginThrottle(key_hash=ip_throttle_key, window_started_at=now, failures=0)
        if _aware(ip_throttle.window_started_at) < now - timedelta(minutes=config.login_lock_minutes):
            ip_throttle.failures = 0; ip_throttle.window_started_at = now
        ip_throttle.failures = int(ip_throttle.failures or 0) + 1
        if ip_throttle.failures >= config.login_max_failures * 5:
            ip_throttle.blocked_until = now + timedelta(minutes=config.login_lock_minutes)
        db.add(ip_throttle)
        if user:
            user.failed_logins += 1
            if user.failed_logins >= config.login_max_failures:
                user.locked_until = now + timedelta(minutes=config.login_lock_minutes)
        record_audit(db, request, "auth.login_failed", user, "user", user.id if user else "", {"username": username})
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "账号或密码错误，或账号尚未获准使用")
    user.failed_logins = 0; user.locked_until = None
    if throttle:
        db.delete(throttle)
    if ip_throttle:
        db.delete(ip_throttle)
    session_token, csrf_token = opaque_token(), opaque_token()
    db.add(LoginSession(user_id=user.id, token_hash=token_hash(session_token), csrf_hash=token_hash(csrf_token), ip_address=ip, user_agent=request.headers.get("user-agent", "")[:320], expires_at=expiry(config.session_days)))
    record_audit(db, request, "auth.login", user, "user", user.id)
    db.commit()
    response.set_cookie(config.session_cookie, session_token, max_age=config.session_days * 86400, httponly=True, secure=config.public_origin.startswith("https://"), samesite="strict", path="/")
    return {"user": public_user(user, db.get(UsageLimit, user.id)), "csrf_token": csrf_token}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, user: User = Depends(require_csrf), db: Session = Depends(get_db), config: Settings = Depends(get_settings)) -> dict:
    raw = request.cookies.get(config.session_cookie)
    if raw:
        session = db.scalar(select(LoginSession).where(LoginSession.token_hash == token_hash(raw)))
        if session:
            session.revoked_at = utcnow()
    record_audit(db, request, "auth.logout", user, "user", user.id)
    db.commit(); response.delete_cookie(config.session_cookie, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return {"user": public_user(user, db.get(UsageLimit, user.id))}


@app.get("/api/auth/csrf")
def csrf_token(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db), config: Settings = Depends(get_settings)) -> dict:
    raw_session = request.cookies.get(config.session_cookie)
    session = db.scalar(select(LoginSession).where(LoginSession.token_hash == token_hash(raw_session or "")))
    if not session: raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已失效")
    raw_csrf = opaque_token(); session.csrf_hash = token_hash(raw_csrf); db.commit()
    return {"csrf_token": raw_csrf, "user_id": user.id}


@app.get("/api/providers")
def providers(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    limits = db.get(UsageLimit, user.id)
    allowed = set(json.loads(limits.allowed_models_json or "[]")) if limits else set()
    values = available_providers()
    values = [item for item in values if item["name"] == "edge_tts" or item["name"] in allowed or any(str(value).startswith(f"{item['name']}:") for value in allowed)]
    return {"providers": values, "paid_enabled": bool(limits and limits.allow_paid_models)}


@app.get("/api/projects")
def list_projects(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    member_ids = select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
    query = select(Project).where(or_(Project.owner_id == user.id, Project.id.in_(member_ids))).order_by(Project.updated_at.desc())
    if user.role == "admin": query = select(Project).order_by(Project.updated_at.desc())
    return {"projects": [public_project(item) for item in db.scalars(query).all()]}


@app.post("/api/projects", status_code=201)
def create_project(payload: ProjectCreate, request: Request, user: User = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    if user.role not in ("admin", "producer", "director", "editor"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "当前角色不能创建项目，请由制片人创建后再加入成员")
    project = Project(owner_id=user.id, title=payload.title.strip(), canvas_json=payload.canvas.model_dump_json(by_alias=True))
    db.add(project); db.flush(); db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
    record_audit(db, request, "project.created", user, "project", project.id); db.commit()
    return {"project": public_project(project)}


@app.get("/api/projects/{project_id}")
def get_project(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return {"project": public_project(require_project_read(db, project_id, user))}


@app.patch("/api/projects/{project_id}")
def update_project(project_id: str, payload: ProjectUpdate, request: Request, user: User = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    project = require_project_write(db, project_id, user)
    if project.version != payload.expected_version:
        return Response(content=json.dumps({"error": "version_conflict", "currentVersion": project.version}), media_type="application/json", status_code=409)
    db.add(ProjectRevision(project_id=project.id, actor_id=user.id, version=project.version, canvas_json=project.canvas_json))
    project.canvas_json = payload.canvas.model_dump_json(by_alias=True)
    if payload.title is not None: project.title = payload.title.strip()
    project.version += 1; project.updated_at = utcnow()
    record_audit(db, request, "project.updated", user, "project", project.id, {"version": project.version}); db.commit()
    return {"project": public_project(project)}


@app.post("/api/projects/{project_id}/reviews")
def review_project_node(project_id: str, payload: ProjectReviewCreate, request: Request, user: User = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    require_project_review(db, project_id, user)
    project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if not project: raise HTTPException(status.HTTP_404_NOT_FOUND, "项目不存在")
    if project.version != payload.expected_version:
        return Response(content=json.dumps({"error": "version_conflict", "currentVersion": project.version}), media_type="application/json", status_code=409)
    try: document = json.loads(project.canvas_json or "{}")
    except json.JSONDecodeError as error: raise HTTPException(status.HTTP_409_CONFLICT, "项目画布数据已损坏，不能写入审片决定") from error
    nodes = document.get("nodes", []) if isinstance(document, dict) else []
    node = next((item for item in nodes if isinstance(item, dict) and str(item.get("id")) == payload.node_id), None)
    if not node: raise HTTPException(status.HTTP_404_NOT_FOUND, "待审节点不存在")
    data = node.setdefault("data", {}); desktop_payload = data.setdefault("desktopPayload", {})
    labels = {"adopt": "已采用", "reject": "已驳回", "accept_risk": "风险已接受"}
    data["status"] = labels[payload.decision]
    desktop_payload["review_decision"] = payload.decision
    desktop_payload["reviewed_by"] = user.id
    desktop_payload["reviewed_at"] = utcnow().isoformat()
    db.add(ProjectRevision(project_id=project.id, actor_id=user.id, version=project.version, canvas_json=project.canvas_json))
    project.canvas_json = json.dumps(document, ensure_ascii=False, separators=(",", ":")); project.version += 1; project.updated_at = utcnow()
    record_audit(db, request, "project.node_reviewed", user, "project_node", payload.node_id, {"project_id": project.id, "decision": payload.decision, "version": project.version}); db.commit()
    return {"project": public_project(project), "review": {"node_id": payload.node_id, "decision": payload.decision, "status": labels[payload.decision]}}


@app.get("/api/projects/{project_id}/members")
def list_project_members(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    project = require_project_read(db, project_id, user)
    rows = db.execute(select(ProjectMember, User).join(User, User.id == ProjectMember.user_id).where(ProjectMember.project_id == project_id).order_by(ProjectMember.role, User.display_name)).all()
    return {"can_manage": user.role == "admin" or project.owner_id == user.id, "members": [{"id": member.id, "user_id": account.id, "username": account.username, "display_name": account.display_name, "account_status": account.status, "role": member.role} for member, account in rows]}


@app.post("/api/projects/{project_id}/members", status_code=201)
def add_project_member(project_id: str, payload: ProjectMemberCreate, request: Request, user: User = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    project = require_project_membership_admin(db, project_id, user)
    account = db.scalar(select(User).where(User.username == payload.username.strip().lower()))
    if not account: raise HTTPException(status.HTTP_404_NOT_FOUND, "账号不存在，请先由管理员创建账号")
    if account.status != "active": raise HTTPException(status.HTTP_409_CONFLICT, "该账号尚未获准登录，不能加入项目")
    if account.id == project.owner_id: raise HTTPException(status.HTTP_409_CONFLICT, "项目所有者已经在成员列表中")
    member = db.scalar(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == account.id))
    if member: member.role = payload.role
    else: member = ProjectMember(project_id=project_id, user_id=account.id, role=payload.role); db.add(member); db.flush()
    record_audit(db, request, "project.member_saved", user, "project_member", member.id, {"project_id": project_id, "username": account.username, "role": member.role}); db.commit()
    return {"member": {"id": member.id, "user_id": account.id, "username": account.username, "display_name": account.display_name, "account_status": account.status, "role": member.role}}


@app.patch("/api/projects/{project_id}/members/{member_id}")
def update_project_member(project_id: str, member_id: str, payload: ProjectMemberUpdate, request: Request, user: User = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    require_project_membership_admin(db, project_id, user)
    member = db.get(ProjectMember, member_id)
    if not member or member.project_id != project_id: raise HTTPException(status.HTTP_404_NOT_FOUND, "项目成员不存在")
    if member.role == "owner": raise HTTPException(status.HTTP_409_CONFLICT, "不能修改项目所有者权限")
    member.role = payload.role; record_audit(db, request, "project.member_role_updated", user, "project_member", member.id, {"project_id": project_id, "role": member.role}); db.commit()
    account = db.get(User, member.user_id)
    return {"member": {"id": member.id, "user_id": account.id, "username": account.username, "display_name": account.display_name, "account_status": account.status, "role": member.role}}


@app.delete("/api/projects/{project_id}/members/{member_id}")
def remove_project_member(project_id: str, member_id: str, request: Request, user: User = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    require_project_membership_admin(db, project_id, user)
    member = db.get(ProjectMember, member_id)
    if not member or member.project_id != project_id: raise HTTPException(status.HTTP_404_NOT_FOUND, "项目成员不存在")
    if member.role == "owner": raise HTTPException(status.HTTP_409_CONFLICT, "不能移除项目所有者")
    record_audit(db, request, "project.member_removed", user, "project_member", member.id, {"project_id": project_id, "user_id": member.user_id}); db.delete(member); db.commit()
    return {"ok": True}


@app.get("/api/assets")
def list_assets(project_id: str, library_only: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    require_project_read(db, project_id, user)
    query = select(Asset).where(Asset.project_id == project_id)
    if library_only: query = query.where(Asset.in_library.is_(True))
    items = db.scalars(query.order_by(Asset.created_at.desc())).all()
    return {"assets": [public_asset(item) for item in items]}


@app.post("/api/assets", status_code=201)
async def upload_asset(request: Request, project_id: str = Form(...), node_id: str = Form(""), metadata_json: str = Form("{}"), file: UploadFile = File(...), user: User = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    require_project_write(db, project_id, user)
    try: metadata = json.loads(metadata_json or "{}")
    except json.JSONDecodeError as error: raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "资产元数据不是有效 JSON") from error
    asset = Asset(project_id=project_id, owner_id=user.id, node_id=node_id, name=(file.filename or "未命名资源")[:260], kind="file", object_key="pending", content_type=file.content_type or "application/octet-stream", size=0, sha256="", status="uploading", metadata_json=json.dumps(metadata, ensure_ascii=False))
    db.add(asset); db.flush()
    object_key, size, digest, content_type = await save_upload(file, project_id, asset.id)
    asset.object_key = object_key; asset.size = size; asset.sha256 = digest; asset.content_type = content_type; asset.kind = media_kind(content_type); asset.status = "ready"
    record_audit(db, request, "asset.uploaded", user, "asset", asset.id, {"kind": asset.kind, "size": size}); db.commit()
    return {"asset": public_asset(asset)}


@app.get("/api/assets/{asset_id}")
def get_asset(asset_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    asset = db.get(Asset, asset_id)
    if not asset: raise HTTPException(status.HTTP_404_NOT_FOUND, "资产不存在")
    require_project_read(db, asset.project_id, user)
    path = resolve_object(asset.object_key)
    if not path.is_file(): raise HTTPException(status.HTTP_404_NOT_FOUND, "资产文件已丢失")
    return FileResponse(path, media_type=asset.content_type, filename=asset.name,
                        content_disposition_type="inline",
                        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


@app.post("/api/assets/{asset_id}/save-to-library")
def save_asset_to_library(asset_id: str, request: Request, user: User = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    asset = db.get(Asset, asset_id)
    if not asset: raise HTTPException(status.HTTP_404_NOT_FOUND, "资产不存在")
    require_project_write(db, asset.project_id, user)
    asset.in_library = True; record_audit(db, request, "asset.saved_to_library", user, "asset", asset.id); db.commit()
    return {"asset": public_asset(asset)}


@app.post("/api/production-runs", status_code=201)
def create_production_run(payload: ProductionRunCreate, request: Request, user: User = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    require_project_write(db, payload.project_id, user)
    run = db.scalar(select(ProductionRun).where(ProductionRun.project_id == payload.project_id, ProductionRun.node_id == payload.node_id))
    supplied_locks = payload.provider_locks or (json.loads(run.provider_locks_json or "{}") if run else {})
    locks = quote_production_plan(db, user, supplied_locks)["locks"]
    if not run:
        run = ProductionRun(project_id=payload.project_id, node_id=payload.node_id, owner_id=user.id, automation_mode=payload.automation_mode, provider_locks_json=json.dumps(locks, ensure_ascii=False))
        db.add(run); db.flush(); record_audit(db, request, "production.created", user, "production_run", run.id)
    else:
        run.automation_mode = payload.automation_mode
        run.provider_locks_json = json.dumps(locks, ensure_ascii=False)
    db.commit(); return {"run": public_run(run)}


@app.post("/api/production-runs/quote")
def quote_production_run(payload: ProductionRunCreate, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    require_project_write(db, payload.project_id, user)
    return {"quote": quote_production_plan(db, user, payload.provider_locks)}


@app.get("/api/production-runs/{run_id}")
def get_production_run(run_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    run = db.get(ProductionRun, run_id)
    if not run: raise HTTPException(status.HTTP_404_NOT_FOUND, "制片流程不存在")
    require_project_read(db, run.project_id, user)
    events = db.scalars(select(ProductionEvent).where(ProductionEvent.run_id == run.id).order_by(ProductionEvent.created_at.desc()).limit(100)).all()
    return {"run": public_run(run), "events": [{"event": item.event, "stage": item.stage, "detail": json.loads(item.detail_json or "{}"), "created_at": item.created_at.isoformat()} for item in events]}


@app.post("/api/production-runs/{run_id}/command")
def command_production_run(run_id: str, payload: ProductionCommand, request: Request, user: User = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    run = db.get(ProductionRun, run_id)
    if not run: raise HTTPException(status.HTTP_404_NOT_FOUND, "制片流程不存在")
    if payload.command in ("approve", "accept_risk"):
        require_project_review(db, run.project_id, user)
    else:
        require_project_write(db, run.project_id, user)
    handle_command(db, run, payload.command, user.id, payload.target_stage)
    record_audit(db, request, f"production.{payload.command}", user, "production_run", run.id, {"stage": run.stage, "target_stage": payload.target_stage}); db.commit()
    return {"run": public_run(run)}


@app.get("/api/admin/users")
def list_users(_: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    # Reading the whole account list is also admin-only, without requiring CSRF on GET.
    if _.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只有管理员可以查看账号")
    users = db.scalars(select(User).order_by(User.created_at.desc())).all()
    now = utcnow()
    result = []
    for user in users:
        payload = public_user(user, db.get(UsageLimit, user.id))
        payload["active_sessions"] = db.scalar(select(func.count()).select_from(LoginSession).where(LoginSession.user_id == user.id, LoginSession.revoked_at.is_(None), LoginSession.expires_at > now)) or 0
        last_login = db.scalar(select(func.max(LoginSession.created_at)).where(LoginSession.user_id == user.id))
        payload["last_login_at"] = last_login.isoformat() if last_login else None
        result.append(payload)
    return {"users": result}


@app.post("/api/admin/users", status_code=201)
def create_user(payload: UserCreate, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db), config: Settings = Depends(get_settings)) -> dict:
    try:
        username = validate_username(payload.username)
        password = validate_password(payload.password, username)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
    user = User(username=username, display_name=payload.display_name.strip() or username, password_hash=hash_password(password), role=payload.role, status="active" if payload.approved else "pending")
    db.add(user)
    try:
        db.flush()
    except IntegrityError as error:
        db.rollback(); raise HTTPException(status.HTTP_409_CONFLICT, "该账号已存在") from error
    limits = UsageLimit(user_id=user.id, daily_tasks=payload.daily_tasks, daily_credits=payload.daily_credits, concurrent_tasks=payload.concurrent_tasks, allow_paid_models=payload.allow_paid_models, allowed_models_json=json.dumps(payload.allowed_models, ensure_ascii=False))
    db.add(limits); record_audit(db, request, "admin.user_created", admin, "user", user.id, {"role": user.role, "status": user.status}); db.commit()
    return {"user": public_user(user, limits)}


@app.patch("/api/admin/users/{user_id}")
def update_user(user_id: str, payload: UserUpdate, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "账号不存在")
    if user.id == admin.id and payload.status == "suspended":
        raise HTTPException(status.HTTP_409_CONFLICT, "不能停用当前管理员自己")
    removing_admin = user.role == "admin" and ((payload.role is not None and payload.role != "admin") or (payload.status is not None and payload.status != "active"))
    if removing_admin:
        other_admins = db.scalar(select(func.count()).select_from(User).where(User.id != user.id, User.role == "admin", User.status == "active")) or 0
        if other_admins == 0:
            raise HTTPException(status.HTTP_409_CONFLICT, "不能停用或降级最后一个有效管理员")
    values = payload.model_dump(exclude_unset=True)
    for key in ("display_name", "role", "status"):
        if key in values:
            setattr(user, key, values[key])
    revoke_sessions = bool(payload.password or "role" in values or "status" in values)
    if payload.password:
        try: user.password_hash = hash_password(validate_password(payload.password, user.username))
        except ValueError as error: raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
        user.password_changed_at = utcnow()
    if revoke_sessions:
        db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    limits = db.get(UsageLimit, user.id) or UsageLimit(user_id=user.id)
    for key in ("daily_tasks", "daily_credits", "concurrent_tasks", "allow_paid_models"):
        if values.get(key) is not None: setattr(limits, key, values[key])
    if payload.allowed_models is not None: limits.allowed_models_json = json.dumps(payload.allowed_models, ensure_ascii=False)
    db.add(limits); record_audit(db, request, "admin.user_updated", admin, "user", user.id, {"fields": sorted(values)}); db.commit()
    return {"user": public_user(user, limits)}


@app.post("/api/admin/users/{user_id}/revoke-sessions")
def revoke_user_sessions(user_id: str, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id)
    if not user: raise HTTPException(status.HTTP_404_NOT_FOUND, "账号不存在")
    removed = db.execute(delete(LoginSession).where(LoginSession.user_id == user.id)).rowcount or 0
    record_audit(db, request, "admin.sessions_revoked", admin, "user", user.id, {"sessions": removed}); db.commit()
    return {"ok": True, "revoked": removed}


@app.get("/api/admin/usage")
def admin_usage(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    if user.role != "admin": raise HTTPException(status.HTTP_403_FORBIDDEN, "只有管理员可以查看使用统计")
    day_start = utcnow() - timedelta(hours=24)
    rows = db.execute(select(User.id, User.username, User.display_name, func.count(GenerationTask.id), func.coalesce(func.sum(GenerationTask.estimated_credits), 0)).join(GenerationTask, GenerationTask.owner_id == User.id).where(GenerationTask.created_at >= day_start).group_by(User.id, User.username, User.display_name).order_by(func.count(GenerationTask.id).desc())).all()
    status_rows = db.execute(select(GenerationTask.status, func.count()).where(GenerationTask.created_at >= day_start).group_by(GenerationTask.status)).all()
    return {"window_hours": 24, "users": [{"id": row[0], "username": row[1], "display_name": row[2], "tasks": row[3], "credits": row[4]} for row in rows], "statuses": {row[0]: row[1] for row in status_rows}}


@app.get("/api/admin/readiness")
def admin_readiness(user: User = Depends(current_user), db: Session = Depends(get_db), config: Settings = Depends(get_settings)) -> dict:
    if user.role != "admin": raise HTTPException(status.HTTP_403_FORBIDDEN, "只有管理员可以查看系统就绪状态")
    cutoff = utcnow() - timedelta(seconds=45)
    workers = db.scalars(select(ServiceHeartbeat).where(ServiceHeartbeat.service == "worker", ServiceHeartbeat.updated_at >= cutoff).order_by(ServiceHeartbeat.updated_at.desc())).all()
    try:
        storage = Path(config.storage_dir); storage.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".admin-readiness-", dir=storage, delete=True) as probe:
            probe.write(b"ok"); probe.flush()
        storage_ok, storage_error = True, ""
    except Exception as error:
        storage_ok, storage_error = False, str(error)[:240]
    provider_rows = available_providers()
    provider_names = [item["name"] for item in provider_rows]
    capability_providers = {
        capability: [item["name"] for item in provider_rows if capability in item.get("capabilities", [])]
        for capability in REQUIRED_GENERATION_CAPABILITIES
    }
    missing_capabilities = [capability for capability, names in capability_providers.items() if not names]
    control_ready = bool(workers and storage_ok)
    return {
        "ready": bool(control_ready and not missing_capabilities),
        "control_ready": control_ready,
        "generation_ready": not missing_capabilities,
        "database": True,
        "storage": storage_ok,
        "storage_error": storage_error,
        "active_workers": len(workers),
        "workers": [{"instance": item.instance, "updated_at": item.updated_at.isoformat(), "detail": json.loads(item.detail_json or "{}")} for item in workers],
        "providers": provider_names,
        "capability_providers": capability_providers,
        "missing_capabilities": missing_capabilities,
    }


@app.get("/api/admin/audit")
def admin_audit(limit: int = 100, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    if user.role != "admin": raise HTTPException(status.HTTP_403_FORBIDDEN, "只有管理员可以查看审计日志")
    items = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(max(1, min(limit, 500)))).all()
    return {"events": [{"id": item.id, "actor_id": item.actor_id, "action": item.action, "target_type": item.target_type, "target_id": item.target_id, "ip_address": item.ip_address, "detail": json.loads(item.detail_json or "{}"), "created_at": item.created_at.isoformat()} for item in items]}


@app.post("/api/workflow-runs/quote")
def quote_workflow_run(payload: WorkflowRunCreate, user: User = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    require_project_write(db, payload.project_id, user)
    prepared, total_credits = prepare_workflow_items(db, user, payload)
    return {"quote": {"items": len(prepared), "credits": total_credits}}


@app.post("/api/workflow-runs", status_code=201)
def create_workflow_run(payload: WorkflowRunCreate, request: Request, user: User = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    require_project_write(db, payload.project_id, user)
    prepared, total_credits = prepare_workflow_items(db, user, payload)
    run = WorkflowRun(project_id=payload.project_id, node_id=payload.node_id, owner_id=user.id, status="ready", current_index=0, total_items=len(prepared), items_json=json.dumps(prepared, ensure_ascii=False, separators=(",", ":")))
    db.add(run); db.flush(); initialize_workflow_tasks(db, run)
    record_audit(db, request, "workflow.created", user, "workflow_run", run.id, {"items": len(prepared), "credits": total_credits}); db.commit()
    return {"run": public_workflow_run(run)}


@app.get("/api/workflow-runs")
def list_workflow_runs(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    require_project_read(db, project_id, user)
    items = db.scalars(select(WorkflowRun).where(WorkflowRun.project_id == project_id).order_by(WorkflowRun.created_at.desc()).limit(100)).all()
    return {"runs": [public_workflow_run(item) for item in items]}


@app.get("/api/workflow-runs/{run_id}")
def get_workflow_run(run_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    run = db.get(WorkflowRun, run_id)
    if not run: raise HTTPException(status.HTTP_404_NOT_FOUND, "工作流不存在")
    require_project_read(db, run.project_id, user)
    return {"run": public_workflow_run(run)}


@app.post("/api/workflow-runs/{run_id}/command")
def command_workflow_run(run_id: str, payload: WorkflowCommand, request: Request, user: User = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    run = db.get(WorkflowRun, run_id)
    if not run: raise HTTPException(status.HTTP_404_NOT_FOUND, "工作流不存在")
    require_project_write(db, run.project_id, user)
    command_workflow(db, run, payload.command, user)
    record_audit(db, request, f"workflow.{payload.command}", user, "workflow_run", run.id, {"current_index": run.current_index}); db.commit()
    return {"run": public_workflow_run(run)}


@app.get("/api/workflow-templates")
def list_workflow_templates(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    items = db.scalars(select(WorkflowTemplate).where(WorkflowTemplate.owner_id == user.id).order_by(WorkflowTemplate.updated_at.desc())).all()
    return {"templates": [{"id": item.id, "name": item.name, "definition": json.loads(item.definition_json or "{}"), "created_at": item.created_at.isoformat(), "updated_at": item.updated_at.isoformat()} for item in items]}


@app.post("/api/workflow-templates", status_code=201)
def create_workflow_template(payload: WorkflowTemplateCreate, request: Request, user: User = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    encoded = json.dumps(payload.definition, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 1_000_000: raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "工作流模板超过 1 MB")
    nodes = payload.definition.get("nodes", []) if isinstance(payload.definition, dict) else []
    if not isinstance(nodes, list) or not nodes or len(nodes) > 50: raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "工作流模板必须包含 1–50 个节点")
    item = WorkflowTemplate(owner_id=user.id, name=payload.name.strip(), definition_json=encoded)
    db.add(item); db.flush(); record_audit(db, request, "workflow.template_created", user, "workflow_template", item.id, {"name": item.name, "nodes": len(nodes)}); db.commit()
    return {"template": {"id": item.id, "name": item.name, "definition": payload.definition, "created_at": item.created_at.isoformat(), "updated_at": item.updated_at.isoformat()}}


@app.delete("/api/workflow-templates/{template_id}")
def delete_workflow_template(template_id: str, request: Request, user: User = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    item = db.get(WorkflowTemplate, template_id)
    if not item or item.owner_id != user.id: raise HTTPException(status.HTTP_404_NOT_FOUND, "工作流模板不存在")
    record_audit(db, request, "workflow.template_deleted", user, "workflow_template", item.id); db.delete(item); db.commit()
    return {"ok": True}


@app.post("/api/tasks", status_code=202)
def create_task(payload: TaskCreate, request: Request, user: User = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    idem = (request.headers.get("idempotency-key") or "").strip()
    if len(idem) < 12 or len(idem) > 100:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "提交任务必须包含 12–100 位幂等键")
    existing = db.scalar(select(GenerationTask).where(GenerationTask.owner_id == user.id, GenerationTask.idempotency_key == idem))
    if existing:
        return {"task": {"id": existing.id, "status": existing.status, "progress": existing.progress}, "deduplicated": True}
    require_project_write(db, payload.project_id, user)
    validate_task_request(db, payload.project_id, payload.kind, payload.provider, payload.input)
    resolved_model = resolve_provider_model(payload.provider, payload.model)
    credits = estimate_task_credits(payload.kind, payload.provider, payload.estimated_credits)
    enforce_task_policy(db, user, payload.provider, resolved_model, credits)
    task = GenerationTask(project_id=payload.project_id, node_id=payload.node_id, owner_id=user.id, kind=payload.kind, provider=payload.provider, model=resolved_model, estimated_credits=credits, idempotency_key=idem, input_json=json.dumps(payload.input, ensure_ascii=False, separators=(",", ":")))
    db.add(task); record_audit(db, request, "task.queued", user, "task", task.id, {"kind": task.kind, "provider": task.provider, "model": task.model, "credits": task.estimated_credits}); db.commit()
    return {"task": {"id": task.id, "status": task.status, "progress": task.progress}}


@app.get("/api/tasks/quote")
def quote_task(kind: str, provider: str = "", model: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    model = resolve_provider_model(provider, model)
    credits = estimate_task_credits(kind, provider)
    enforce_task_policy(db, user, provider, model, credits)
    return {"quote": {"credits": credits, "kind": kind, "provider": provider, "model": model}}


@app.get("/api/tasks")
def list_tasks(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    require_project_read(db, project_id, user)
    items = db.scalars(select(GenerationTask).where(GenerationTask.project_id == project_id).order_by(GenerationTask.created_at.desc()).limit(200)).all()
    return {"tasks": [{"id": item.id, "node_id": item.node_id, "kind": item.kind, "provider": item.provider, "model": item.model, "status": item.status, "progress": item.progress, "managed_by": "production" if item.production_run_id else "workflow" if item.idempotency_key.startswith("workflow:") else "task", "output": json.loads(item.output_json) if item.output_json else None, "error_code": item.error_code, "error_message": item.error_message, "created_at": item.created_at.isoformat()} for item in items]}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    item = db.get(GenerationTask, task_id)
    if not item: raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    require_project_read(db, item.project_id, user)
    return {"task": {"id": item.id, "node_id": item.node_id, "kind": item.kind, "provider": item.provider, "model": item.model, "status": item.status, "progress": item.progress, "output": json.loads(item.output_json) if item.output_json else None, "error_code": item.error_code, "error_message": item.error_message}}


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str, request: Request, user: User = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    item = db.get(GenerationTask, task_id)
    if not item: raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    require_project_write(db, item.project_id, user)
    if item.status in ("queued", "running", "paused"):
        item.status = "cancelled"
        workflow = db.scalar(select(WorkflowRun).where(WorkflowRun.active_task_id == item.id))
        if workflow: workflow.status = "cancelled"; workflow.active_task_id = None; workflow.error_message = "子任务已取消"
        production = db.scalar(select(ProductionRun).where(ProductionRun.active_task_id == item.id))
        if production: production.status = "paused"; production.resume_status = "ready"; production.active_task_id = None; production.error_message = "当前阶段任务已取消"
        record_audit(db, request, "task.cancelled", user, "task", item.id); db.commit()
        from .canvas_sync import sync_task_to_canvas
        sync_task_to_canvas(item.id)
    return {"task": {"id": item.id, "status": item.status}}


@app.post("/api/tasks/{task_id}/pause")
def pause_task(task_id: str, request: Request, user: User = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    item = db.get(GenerationTask, task_id)
    if not item: raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    require_project_write(db, item.project_id, user)
    if item.status == "running": raise HTTPException(status.HTTP_409_CONFLICT, "供应商已开始执行，单任务不能安全暂停；请暂停所属工作流，或取消任务")
    if item.status != "queued": raise HTTPException(status.HTTP_409_CONFLICT, "只有排队中的任务可以暂停")
    item.status = "paused"; record_audit(db, request, "task.paused", user, "task", item.id); db.commit()
    from .canvas_sync import sync_task_to_canvas
    sync_task_to_canvas(item.id)
    return {"task": {"id": item.id, "status": item.status}}


@app.post("/api/tasks/{task_id}/resume")
def resume_task(task_id: str, request: Request, user: User = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    item = db.get(GenerationTask, task_id)
    if not item: raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    require_project_write(db, item.project_id, user)
    if item.status != "paused": raise HTTPException(status.HTTP_409_CONFLICT, "当前任务没有暂停")
    enforce_existing_task_policy(db, user, item.provider, item.model, item.estimated_credits)
    item.status = "queued"; record_audit(db, request, "task.resumed", user, "task", item.id); db.commit()
    from .canvas_sync import sync_task_to_canvas
    sync_task_to_canvas(item.id)
    return {"task": {"id": item.id, "status": item.status}}


@app.post("/api/tasks/{task_id}/retry", status_code=202)
def retry_task(task_id: str, request: Request, user: User = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    item = db.get(GenerationTask, task_id)
    if not item: raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    require_project_write(db, item.project_id, user)
    if item.status not in ("failed", "cancelled"):
        raise HTTPException(status.HTTP_409_CONFLICT, "只有失败或已取消的任务可以重试")
    if item.production_run_id or item.idempotency_key.startswith("workflow:"):
        raise HTTPException(status.HTTP_409_CONFLICT, "该任务属于持久流程，请在对应的制片或工作流节点中恢复，不能脱离流程单独重试")
    credits = estimate_task_credits(item.kind, item.provider, item.estimated_credits)
    enforce_task_policy(db, user, item.provider, item.model, credits)
    attempt = (db.scalar(select(func.count()).select_from(GenerationTask).where(GenerationTask.project_id == item.project_id, GenerationTask.node_id == item.node_id)) or 0) + 1
    retry = GenerationTask(project_id=item.project_id, node_id=item.node_id, owner_id=user.id, kind=item.kind, provider=item.provider, model=item.model, estimated_credits=credits, idempotency_key=f"retry:{item.id}:{attempt}", input_json=item.input_json)
    db.add(retry); record_audit(db, request, "task.retried", user, "task", retry.id, {"source_task_id": item.id}); db.commit()
    return {"task": {"id": retry.id, "status": retry.status, "progress": retry.progress}}
