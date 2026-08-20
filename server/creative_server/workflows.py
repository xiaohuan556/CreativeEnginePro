from __future__ import annotations

import json

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import GenerationTask, User, WorkflowRun
from .schemas import WorkflowRunCreate
from .task_policy import enforce_existing_task_policy, enforce_task_policy, enforce_workflow_policy, estimate_task_credits
from .task_validation import validate_task_request
from .provider_catalog import resolve_provider_model


def public_workflow_run(run: WorkflowRun) -> dict:
    return {
        "id": run.id,
        "project_id": run.project_id,
        "node_id": run.node_id,
        "status": run.status,
        "current_index": run.current_index,
        "total_items": run.total_items,
        "progress": round(run.current_index / run.total_items * 100) if run.total_items else 0,
        "active_task_id": run.active_task_id,
        "error_message": run.error_message,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
    }


def prepare_workflow_items(db: Session, user: User, payload: WorkflowRunCreate) -> tuple[list[dict], int]:
    prepared: list[dict] = []
    policy_requests: list[tuple[str, str, int]] = []
    for item in payload.items:
        value = item.model_dump()
        validate_task_request(db, payload.project_id, item.kind, item.provider, item.input)
        value["model"] = resolve_provider_model(item.provider, item.model)
        credits = estimate_task_credits(item.kind, item.provider)
        value["estimated_credits"] = credits
        prepared.append(value)
        policy_requests.append((item.provider, str(value["model"]), credits))
    enforce_workflow_policy(db, user, policy_requests)
    return prepared, sum(item[2] for item in policy_requests)


def _new_workflow_task(db: Session, run: WorkflowRun, item: dict, index: int, status_value: str) -> GenerationTask:
    attempt = (db.scalar(select(func.count()).select_from(GenerationTask).where(
        GenerationTask.owner_id == run.owner_id,
        GenerationTask.idempotency_key.like(f"workflow:{run.id}:{index}:%"),
    )) or 0) + 1
    task = GenerationTask(
        project_id=run.project_id,
        node_id=str(item["node_id"]),
        owner_id=run.owner_id,
        kind=str(item["kind"]),
        provider=str(item["provider"]),
        model=str(item.get("model") or ""),
        status=status_value,
        estimated_credits=int(item.get("estimated_credits") or 0),
        idempotency_key=f"workflow:{run.id}:{index}:{attempt}",
        input_json=json.dumps(item.get("input") or {}, ensure_ascii=False, separators=(",", ":")),
    )
    db.add(task); db.flush()
    return task


def initialize_workflow_tasks(db: Session, run: WorkflowRun) -> GenerationTask | None:
    """Reserve every child against quota now, while releasing only the first to the worker."""
    items = json.loads(run.items_json or "[]")
    first: GenerationTask | None = None
    for index, item in enumerate(items):
        task = _new_workflow_task(db, run, item, index, "queued" if index == 0 else "workflow_waiting")
        item["task_id"] = task.id
        if index == 0:
            first = task
    run.items_json = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    if first:
        run.active_task_id = first.id; run.status = "running"; run.error_message = ""
    else:
        run.status = "completed"; run.active_task_id = None
    return first


def enqueue_next_workflow_item(db: Session, run: WorkflowRun, retry: bool = False) -> GenerationTask | None:
    if run.active_task_id:
        task = db.get(GenerationTask, run.active_task_id)
        if task and task.status in ("queued", "running", "paused"):
            return task
    items = json.loads(run.items_json or "[]")
    if run.current_index >= len(items):
        run.status = "completed"; run.active_task_id = None; run.error_message = ""
        return None
    item = items[run.current_index]
    task = db.get(GenerationTask, item.get("task_id")) if item.get("task_id") and not retry else None
    if not task:
        user = db.get(User, run.owner_id)
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "工作流账号不存在")
        enforce_task_policy(db, user, str(item.get("provider") or ""), str(item.get("model") or ""), int(item.get("estimated_credits") or 0))
        task = _new_workflow_task(db, run, item, run.current_index, "queued")
        item["task_id"] = task.id
        run.items_json = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    elif task.status in ("workflow_waiting", "paused"):
        user = db.get(User, run.owner_id)
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "工作流账号不存在")
        enforce_existing_task_policy(db, user, task.provider, task.model, task.estimated_credits)
        task.status = "queued"; task.worker_id = None; task.lease_expires_at = None
    elif task.status not in ("queued", "running", "paused"):
        return None
    run.active_task_id = task.id; run.status = "running"; run.error_message = ""
    return task


def command_workflow(db: Session, run: WorkflowRun, command: str, user: User) -> WorkflowRun:
    if command == "pause":
        if run.status not in ("running", "ready"):
            raise HTTPException(status.HTTP_409_CONFLICT, "当前工作流不能暂停")
        if run.active_task_id:
            task = db.get(GenerationTask, run.active_task_id)
            if task and task.status == "queued":
                task.status = "paused"; task.worker_id = None; task.lease_expires_at = None
        run.status = "paused"
    elif command == "resume":
        if run.status != "paused":
            raise HTTPException(status.HTTP_409_CONFLICT, "当前工作流没有暂停")
        if run.active_task_id:
            task = db.get(GenerationTask, run.active_task_id)
            if task and task.status in ("queued", "running", "paused"):
                enforce_existing_task_policy(db, user, task.provider, task.model, task.estimated_credits)
                if task.status == "paused":
                    task.status = "queued"; task.worker_id = None; task.lease_expires_at = None
                run.status = "running"
            else:
                run.active_task_id = None; enqueue_next_workflow_item(db, run)
        else:
            items = json.loads(run.items_json or "[]")
            if run.current_index < len(items):
                next_task = db.get(GenerationTask, items[run.current_index].get("task_id"))
                if next_task:
                    enforce_existing_task_policy(db, user, next_task.provider, next_task.model, next_task.estimated_credits)
            enqueue_next_workflow_item(db, run)
    elif command == "cancel":
        if run.status in ("completed", "cancelled"):
            return run
        if run.active_task_id:
            task = db.get(GenerationTask, run.active_task_id)
            if task and task.status in ("queued", "running", "paused"):
                task.status = "cancelled"; task.worker_id = None; task.lease_expires_at = None
        items = json.loads(run.items_json or "[]")
        waiting_ids = [str(item.get("task_id")) for item in items[run.current_index:] if item.get("task_id")]
        for task in db.scalars(select(GenerationTask).where(GenerationTask.id.in_(waiting_ids))).all() if waiting_ids else []:
            if task.status in ("queued", "workflow_waiting", "paused"):
                task.status = "cancelled"; task.worker_id = None; task.lease_expires_at = None
        run.status = "cancelled"; run.active_task_id = None
    elif command == "retry":
        if run.status != "failed":
            raise HTTPException(status.HTTP_409_CONFLICT, "只有失败的工作流可以重试")
        items = json.loads(run.items_json or "[]")
        item = items[run.current_index]
        credits = int(item.get("estimated_credits") or 0)
        enforce_task_policy(db, user, str(item.get("provider") or ""), str(item.get("model") or ""), credits)
        run.status = "ready"; run.active_task_id = None; enqueue_next_workflow_item(db, run, retry=True)
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "不支持的工作流操作")
    return run


def on_workflow_task_finished(task_id: str, success: bool, error: str = "") -> None:
    from .database import SessionLocal
    with SessionLocal.begin() as db:
        run = db.scalar(select(WorkflowRun).where(WorkflowRun.active_task_id == task_id).with_for_update())
        if not run:
            return
        run.active_task_id = None
        if not success:
            run.status = "failed"; run.error_message = error
            return
        run.current_index += 1; run.error_message = ""
        if run.current_index >= run.total_items:
            run.status = "completed"
        elif run.status == "paused":
            return
        else:
            try:
                enqueue_next_workflow_item(db, run)
            except HTTPException as policy_error:
                run.status = "paused"; run.error_message = str(policy_error.detail)
