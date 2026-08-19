from __future__ import annotations

import json

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .models import GenerationTask, ProductionEvent, ProductionRun, Project, User
from .production_state import STAGES, State, approve, pause, resume, rewind, start, task_finished
from .provider_catalog import available_providers, resolve_provider_model
from .task_policy import enforce_task_policy, estimate_task_credits


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


def _task_output(db: Session, run_id: str, stage: int) -> dict:
    task = db.scalars(select(GenerationTask).where(GenerationTask.production_run_id == run_id, GenerationTask.production_stage == stage, GenerationTask.status == "completed").order_by(GenerationTask.updated_at.desc())).first()
    if not task or not task.output_json:
        return {}
    try: return json.loads(task.output_json)
    except json.JSONDecodeError: return {}


def _node_brief(project: Project, node_id: str) -> str:
    try: document = json.loads(project.canvas_json or "{}")
    except json.JSONDecodeError: return project.title
    node = next((item for item in document.get("nodes", []) if isinstance(item, dict) and item.get("id") == node_id), {})
    data = node.get("data", {}) if isinstance(node, dict) else {}
    return str(data.get("description") or project.title)


def _stage_inputs(db: Session, run: ProductionRun, project: Project) -> tuple[dict, dict]:
    brief = _node_brief(project, run.node_id)
    previous = _task_output(db, run.id, max(1, run.stage - 1))
    previous_text = json.dumps(previous.get("data", previous), ensure_ascii=False)
    prompts = {
        1: f"把以下定稿故事拆成可执行镜头表。必须包含镜号、秒数、景别、机位、人物动作、运镜、对白/声音、首尾状态与连续性锚点；只输出结构化 JSON。\n故事：{brief}",
        2: f"依据镜头拆解生成统一角色、场景、道具的视觉资产候选总览图。动漫电影风格，避免真人肖像；同一主体身份、服装、材质和配色必须固定。\n镜头拆解：{previous_text}",
        3: f"依据镜头拆解和已生成资产，输出逐秒调度与运动分镜：人物起点/终点、屏幕方向、动作轨迹、镜头轨迹、速度变化、切镜点、空镜和首尾衔接。只输出结构化 JSON。\n镜头拆解：{json.dumps(_task_output(db, run.id, 1), ensure_ascii=False)}",
        4: f"把调度结果合成一份可直接交给视频模型的最终导演提示词。按秒写明切镜、动作、运镜、声音与连续性约束，不解释。\n调度结果：{previous_text}",
        5: f"根据最终导演提示词生成定稿关键帧组。动漫电影风格；严格固定角色身份、场景结构、道具、光线方向与轴线。\n导演提示词：{previous_text}",
        6: f"一次生成完整连续视频，严格按导演时间线执行切镜、动作、推拉摇移和空镜；每个镜头结束状态必须衔接下一个镜头起点。\n导演方案：{json.dumps(_task_output(db, run.id, 4), ensure_ascii=False)}",
        7: f"从镜头拆解中提取对白并生成自然配音；无对白则生成克制的旁白，不朗读镜头说明。\n镜头拆解：{json.dumps(_task_output(db, run.id, 1), ensure_ascii=False)}",
    }
    inputs: dict = {"prompt": prompts[run.stage], "project_canvas": json.loads(project.canvas_json)}
    params: dict = {"production_stage": run.stage}
    if run.stage in (2, 5): params.update({"candidate_count": 4, "ratio": "16:9"})
    reference_stage = 2 if run.stage == 3 else 5 if run.stage == 6 else None
    if reference_stage:
        asset_ids = _task_output(db, run.id, reference_stage).get("asset_ids", [])
        inputs["references"] = [{"asset_id": asset_id, "role": "reference", "title": "已锁定视觉资产"} for asset_id in asset_ids[:50]]
    return inputs, params


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
    if run.stage in (1, 3, 4): requested_model = str(locks.get("planning_model") or "")
    elif run.stage in (2, 5): requested_model = str(locks.get("image_model") or "")
    elif run.stage == 6: requested_model = str(locks.get("video_model") or "")
    else: requested_model = ""
    model = resolve_provider_model(provider, requested_model)
    profile = next((item for item in available_providers() if item["name"] == provider), None)
    if not profile or operation not in profile["capabilities"]:
        raise HTTPException(status.HTTP_409_CONFLICT, f"已锁定引擎 {provider} 当前不可用或不支持 {operation}；流程已停止，不会静默切换")
    credits = estimate_task_credits(operation, provider)
    enforce_task_policy(db, user, provider, model, credits)
    attempt = (db.scalar(select(func.count()).select_from(GenerationTask).where(GenerationTask.production_run_id == run.id, GenerationTask.production_stage == run.stage)) or 0) + 1
    inputs, params = _stage_inputs(db, run, project)
    task = GenerationTask(project_id=run.project_id, node_id=run.node_id, owner_id=run.owner_id, production_run_id=run.id, production_stage=run.stage, kind=operation, provider=provider, model=model, estimated_credits=credits, idempotency_key=f"production:{run.id}:stage:{run.stage}:attempt:{attempt}", input_json=json.dumps({"inputs": inputs, "params": params, "use_cache": False}, ensure_ascii=False))
    db.add(task); db.flush(); apply_state(run, start(state_of(run), task.id)); event(db, run, "stage.queued", detail={"task_id": task.id, "stage_name": STAGES[run.stage], "provider": provider, "model": model})
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
