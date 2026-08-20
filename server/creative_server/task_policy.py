import json
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Asset, GenerationTask, Project, ProjectMember, UsageLimit, User
from .security import utcnow


ACTIVE_TASKS = ("queued", "running", "paused")
TASK_ROLES = ("admin", "producer", "director", "editor")
FREE_PROVIDERS = ("local", "edge_tts")
BYTES_PER_MB = 1024 * 1024

MINIMUM_CREDITS = {
    "chat": 2,
    "json": 2,
    "text_to_image": 10,
    "image_edit": 10,
    "inpaint": 10,
    "text_to_video": 60,
    "image_to_video": 60,
    "continue_video": 60,
    "text_to_speech": 1,
    "image": 10,
    "video": 60,
}


def estimate_task_credits(kind: str, provider: str, requested: int = 0) -> int:
    """Return a server-owned quota estimate; clients cannot quote zero to bypass limits."""
    if provider in ("local", "edge_tts") or kind in ("video_breakdown", "extract_video_frames"):
        return 0
    return max(int(requested or 0), MINIMUM_CREDITS.get(kind, 5))


def enforce_asset_policy(db: Session, user: User, incoming_bytes: int) -> UsageLimit:
    """Serialize per-user asset reservations and enforce daily/total storage caps."""
    limits = db.scalar(select(UsageLimit).where(UsageLimit.user_id == user.id).with_for_update())
    if not limits:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号尚未配置素材容量")
    incoming = max(0, int(incoming_bytes or 0))
    day_start = utcnow() - timedelta(hours=24)
    daily_bytes = db.scalar(select(func.coalesce(func.sum(Asset.size), 0)).where(Asset.owner_id == user.id, Asset.created_at >= day_start, Asset.status.in_(("ready", "processing", "uploading")))) or 0
    total_bytes = db.scalar(select(func.coalesce(func.sum(Asset.size), 0)).where(Asset.owner_id == user.id, Asset.status.in_(("ready", "processing", "uploading")))) or 0
    if int(limits.daily_asset_mb or 0) <= 0 or daily_bytes + incoming > int(limits.daily_asset_mb) * BYTES_PER_MB:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "今日新增素材容量已达到管理员设置的上限")
    if int(limits.storage_mb or 0) <= 0 or total_bytes + incoming > int(limits.storage_mb) * BYTES_PER_MB:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "个人素材总容量不足，请清理素材或联系管理员扩容")
    return limits


def _model_allowed(allowed: list[str], provider: str, model: str) -> bool:
    if provider in FREE_PROVIDERS:
        return True
    requested = {value for value in (provider, model, f"{provider}:{model}" if provider and model else "") if value}
    return bool(requested.intersection(allowed))


def require_project_write(db: Session, project_id: str, user: User) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "项目不存在")
    if project.owner_id == user.id or user.role == "admin":
        return project
    membership = db.scalar(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id))
    if not membership or membership.role not in ("owner", "editor"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "没有该项目的写入权限")
    return project


def require_project_read(db: Session, project_id: str, user: User) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "项目不存在")
    if project.owner_id == user.id or user.role == "admin":
        return project
    if not db.scalar(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "没有该项目的访问权限")
    return project


def enforce_task_policy(db: Session, user: User, provider: str, model: str, estimated_credits: int) -> UsageLimit:
    if user.role not in TASK_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "当前角色不能提交生成任务")
    limits = db.get(UsageLimit, user.id)
    if not limits:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号尚未配置任务额度")
    allowed = json.loads(limits.allowed_models_json or "[]")
    if not _model_allowed(allowed, provider, model):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "该模型不在管理员允许列表中")
    if estimated_credits > 0 and not limits.allow_paid_models:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "管理员尚未为此账号启用付费模型")
    day_start = utcnow() - timedelta(hours=24)
    daily_count = db.scalar(select(func.count()).select_from(GenerationTask).where(GenerationTask.owner_id == user.id, GenerationTask.created_at >= day_start)) or 0
    daily_credits = db.scalar(select(func.coalesce(func.sum(GenerationTask.estimated_credits), 0)).where(GenerationTask.owner_id == user.id, GenerationTask.created_at >= day_start)) or 0
    running = db.scalar(select(func.count()).select_from(GenerationTask).where(GenerationTask.owner_id == user.id, GenerationTask.status.in_(ACTIVE_TASKS))) or 0
    if daily_count >= limits.daily_tasks:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "今日任务次数已达到管理员设置的上限")
    if daily_credits + estimated_credits > limits.daily_credits:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "今日费用额度不足")
    if running >= limits.concurrent_tasks:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "并发任务已满，请等待当前任务完成")
    return limits


def enforce_workflow_policy(db: Session, user: User, requests: list[tuple[str, str, int]]) -> UsageLimit:
    """Reserve policy capacity for a durable sequential workflow.

    Only one child task runs at a time, but the whole workflow's task count and
    credits are checked before the first paid request is queued.
    """
    if user.role not in TASK_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "当前角色不能执行工作流")
    limits = db.get(UsageLimit, user.id)
    if not limits:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号尚未配置任务额度")
    allowed = json.loads(limits.allowed_models_json or "[]")
    for provider, model, credits in requests:
        if not _model_allowed(allowed, provider, model):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"工作流中的 {provider}:{model or '默认模型'} 不在管理员允许列表中")
        if credits > 0 and not limits.allow_paid_models:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "管理员尚未为此账号启用付费模型")
    day_start = utcnow() - timedelta(hours=24)
    daily_count = db.scalar(select(func.count()).select_from(GenerationTask).where(GenerationTask.owner_id == user.id, GenerationTask.created_at >= day_start)) or 0
    daily_credits = db.scalar(select(func.coalesce(func.sum(GenerationTask.estimated_credits), 0)).where(GenerationTask.owner_id == user.id, GenerationTask.created_at >= day_start)) or 0
    running = db.scalar(select(func.count()).select_from(GenerationTask).where(GenerationTask.owner_id == user.id, GenerationTask.status.in_(ACTIVE_TASKS))) or 0
    total_credits = sum(item[2] for item in requests)
    if daily_count + len(requests) > limits.daily_tasks:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "工作流任务数会超过今日管理员额度")
    if daily_credits + total_credits > limits.daily_credits:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "工作流预计费用会超过今日管理员额度")
    if running >= limits.concurrent_tasks:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "并发任务已满，请等待当前任务完成")
    return limits


def enforce_existing_task_policy(db: Session, user: User, provider: str, model: str, estimated_credits: int) -> UsageLimit:
    """Recheck revocable permissions without charging/counting an existing task twice."""
    if user.role not in TASK_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "当前角色不能恢复生成任务")
    limits = db.get(UsageLimit, user.id)
    if not limits:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号尚未配置任务额度")
    allowed = json.loads(limits.allowed_models_json or "[]")
    if not _model_allowed(allowed, provider, model):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "该模型已被管理员移出允许列表")
    if estimated_credits > 0 and not limits.allow_paid_models:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "管理员已关闭此账号的付费模型权限")
    if limits.concurrent_tasks <= 0:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "管理员已暂停此账号的生成并发")
    return limits


def require_project_review(db: Session, project_id: str, user: User) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "项目不存在")
    if project.owner_id == user.id or user.role == "admin":
        return project
    membership = db.scalar(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id))
    if not membership or membership.role not in ("owner", "editor", "reviewer"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "没有该项目的审片权限")
    return project
