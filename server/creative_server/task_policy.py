import json
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import GenerationTask, Project, ProjectMember, UsageLimit, User
from .security import utcnow


ACTIVE_TASKS = ("queued", "running", "paused")
TASK_ROLES = ("admin", "producer", "director", "editor")

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
    requested = {value for value in (provider, model, f"{provider}:{model}" if provider and model else "") if value}
    if provider != "local" and allowed and not requested.intersection(allowed):
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
