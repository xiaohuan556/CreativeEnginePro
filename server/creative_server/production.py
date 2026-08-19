from __future__ import annotations

import json

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .models import GenerationTask, ProductionEvent, ProductionRun, Project, User
from .production_state import STAGES, State, approve, pause, resume, rewind, start, task_finished
from .provider_catalog import available_providers
from .task_policy import enforce_task_policy


STAGE_TASKS = {
    1: ("openai", "chat"), 2: ("seedream", "text_to_image"),
    3: ("openai", "chat"), 4: ("openai", "chat"),
    5: ("seedream", "text_to_image"), 6: ("seedance", "image_to_video"),
    7: ("edge_tts", "text_to_speech"),
}


def state_of(run: ProductionRun) -> State:
    return State(run.stage, run.completed_stage, run.status, run.active_task_id)


def apply_state(run: ProductionRun, state: State) -> None:
    run.stage, run.completed_stage, run.status, run.active_task_id = state.stage, state.completed_stage, state.status, state.active_task_id


def event(db: Session, run: ProductionRun, name: str, actor_id: str | None = None, detail: dict | None = None) -> None:
    db.add(ProductionEvent(run_id=run.id, actor_id=actor_id, event=name, stage=run.stage, detail_json=json.dumps(detail or {}, ensure_ascii=False)))


def enqueue_current_stage(db: Session, run: ProductionRun) -> GenerationTask:
    if run.active_task_id:
        task = db.get(GenerationTask, run.active_task_id)
        if task and task.status in ("queued", "running", "paused"): return task
    user = db.get(User, run.owner_id)
    project = db.get(Project, run.project_id)
    if not user or not project: raise HTTPException(status.HTTP_404_NOT_FOUND, "制片项目或账号不存在")
    provider, operation = STAGE_TASKS[run.stage]
    locks = json.loads(run.provider_locks_json or "{}")
    if run.stage in (1, 3, 4): provider = str(locks.get("planning") or provider)
    elif run.stage in (2, 5): provider = str(locks.get("image") or provider)
    elif run.stage == 6: provider = str(locks.get("video") or provider)
    profile = next((item for item in available_providers() if item["name"] == provider), None)
    if not profile or operation not in profile["capabilities"]:
        raise HTTPException(status.HTTP_409_CONFLICT, f"已锁定引擎 {provider} 当前不可用或不支持 {operation}；流程已停止，不会静默切换")
    enforce_task_policy(db, user, provider, "", 0)
    attempt = (db.scalar(select(func.count()).select_from(GenerationTask).where(GenerationTask.production_run_id == run.id, GenerationTask.production_stage == run.stage)) or 0) + 1
    inputs = {"prompt": f"执行 AI 制片阶段 {run.stage}：{STAGES[run.stage]}。", "project_canvas": json.loads(project.canvas_json)}
    if run.stage == 6:
        previous = db.scalars(select(GenerationTask).where(GenerationTask.production_run_id == run.id, GenerationTask.production_stage == 5, GenerationTask.status == "completed").order_by(GenerationTask.updated_at.desc())).first()
        if previous and previous.output_json:
            asset_ids = json.loads(previous.output_json).get("asset_ids", [])
            inputs["references"] = [{"asset_id": asset_id, "role": "reference", "title": "定稿图片"} for asset_id in asset_ids[:2]]
    task = GenerationTask(project_id=run.project_id, node_id=run.node_id, owner_id=run.owner_id, production_run_id=run.id, production_stage=run.stage, kind=operation, provider=provider, model="", estimated_credits=0, idempotency_key=f"production:{run.id}:stage:{run.stage}:attempt:{attempt}", input_json=json.dumps({"inputs": inputs, "params": {"production_stage": run.stage}, "use_cache": False}, ensure_ascii=False))
    db.add(task); db.flush(); apply_state(run, start(state_of(run), task.id)); event(db, run, "stage.queued", detail={"task_id": task.id, "stage_name": STAGES[run.stage]})
    return task


def handle_command(db: Session, run: ProductionRun, command: str, actor_id: str, target_stage: int | None = None) -> ProductionRun:
    if command in {"start", "continue"}:
        if run.status == "waiting_review": raise HTTPException(status.HTTP_409_CONFLICT, "当前阶段等待审片，请先通过或接受风险")
        if run.status == "paused": apply_state(run, resume(state_of(run)))
        if not run.active_task_id: enqueue_current_stage(db, run)
    elif command in {"approve", "accept_risk"}:
        if run.status != "waiting_review": raise HTTPException(status.HTTP_409_CONFLICT, "当前没有等待确认的阶段")
        if command == "accept_risk":
            accepted = set(json.loads(run.risk_accepted_json or "[]")); accepted.add(run.stage); run.risk_accepted_json = json.dumps(sorted(accepted))
        apply_state(run, approve(state_of(run))); event(db, run, f"stage.{command}", actor_id)
        enqueue_current_stage(db, run)
    elif command == "pause":
        run.resume_status = run.status
        apply_state(run, pause(state_of(run))); event(db, run, "run.paused", actor_id)
    elif command == "resume":
        if run.status == "paused" and run.active_task_id:
            run.status = "running"
        elif run.status == "paused":
            run.status = run.resume_status or "ready"
        event(db, run, "run.resumed", actor_id)
        if run.status == "ready" and not run.active_task_id: enqueue_current_stage(db, run)
    elif command == "rewind":
        if target_stage is None: raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "请选择重做阶段")
        db.execute(update(GenerationTask).where(GenerationTask.production_run_id == run.id, GenerationTask.production_stage >= target_stage, GenerationTask.status.in_(("queued", "running", "paused"))).values(status="cancelled"))
        apply_state(run, rewind(state_of(run), target_stage)); event(db, run, "run.rewound", actor_id, {"target_stage": target_stage})
        run.resume_status = "ready"
    else: raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "不支持的流程操作")
    return run


def on_task_finished(task_id: str, success: bool, error: str = "") -> None:
    from .database import SessionLocal
    with SessionLocal.begin() as db:
        task = db.get(GenerationTask, task_id)
        if not task or not task.production_run_id: return
        run = db.get(ProductionRun, task.production_run_id)
        if not run or run.active_task_id != task.id: return
        was_paused = run.status == "paused"
        next_state = task_finished(State(run.stage, run.completed_stage, "running", run.active_task_id), run.automation_mode, success)
        if was_paused:
            run.stage, run.completed_stage, run.active_task_id = next_state.stage, next_state.completed_stage, None
            run.status, run.resume_status = "paused", next_state.status
        else:
            apply_state(run, next_state)
        run.error_message = error if not success else ""; event(db, run, "stage.completed" if success else "stage.failed", detail={"task_id": task.id, "error": error})
        if success and run.status == "ready":
            try: enqueue_current_stage(db, run)
            except HTTPException as policy_error: run.status = "paused"; run.error_message = str(policy_error.detail); event(db, run, "run.quota_paused", detail={"reason": run.error_message})
