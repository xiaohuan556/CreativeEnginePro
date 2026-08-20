from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import and_, or_, select

from .config import get_settings
from .database import SessionLocal, create_schema
from .models import Asset, GenerationTask, ProductionRun, ServiceHeartbeat, User, WorkflowRun
from .storage import import_generated_file, media_kind, resolve_object
from .request_compiler import compile_request
from .task_policy import enforce_asset_policy, enforce_existing_task_policy


class TaskLeaseLost(RuntimeError):
    pass


def worker_instance() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _lease_deadline() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=max(30, get_settings().task_lease_seconds))


def renew_task_lease(task_id: str, worker_id: str) -> bool:
    with SessionLocal.begin() as db:
        task = db.get(GenerationTask, task_id)
        if not task or task.status != "running" or task.worker_id != worker_id:
            return False
        task.lease_expires_at = _lease_deadline()
        return True


def _lease_heartbeat(task_id: str, worker_id: str, stop: threading.Event) -> None:
    interval = max(10, min(30, get_settings().task_lease_seconds // 3))
    while not stop.wait(interval):
        if not renew_task_lease(task_id, worker_id):
            return


def _task_scratch(task_id: str, category: str = "") -> Path:
    from config import WORK_DIR
    root = (Path(WORK_DIR) / "server_tasks" / task_id).resolve()
    target = root / category if category else root
    target.mkdir(parents=True, exist_ok=True)
    return target


def _cleanup_task_scratch(task_id: str) -> None:
    from config import WORK_DIR
    root = (Path(WORK_DIR) / "server_tasks" / task_id).resolve()
    work_root = Path(WORK_DIR).resolve()
    if work_root in root.parents:
        shutil.rmtree(root, ignore_errors=True)


def _cleanup_provider_outputs(paths: list[Path]) -> None:
    """Remove copied provider outputs only from server-owned scratch roots."""
    from config import OUTPUT_DIR, WORK_DIR
    storage = Path(get_settings().storage_dir).resolve()
    roots = (Path(OUTPUT_DIR).resolve(), Path(WORK_DIR).resolve())
    for path in paths:
        candidate = path.resolve()
        if storage == candidate or storage in candidate.parents:
            continue
        if any(root == candidate or root in candidate.parents for root in roots):
            candidate.unlink(missing_ok=True)


def _sync_canvas(task_id: str) -> None:
    from .canvas_sync import sync_task_to_canvas
    sync_task_to_canvas(task_id)


def _finish_orchestrators(task_id: str, success: bool, error: str = "") -> None:
    from .production import on_task_finished
    from .workflows import on_workflow_task_finished
    on_task_finished(task_id, success, error)
    on_workflow_task_finished(task_id, success, error)


def record_heartbeat() -> None:
    instance = worker_instance()
    with SessionLocal.begin() as db:
        item = db.get(ServiceHeartbeat, f"worker:{instance}")
        if item:
            item.updated_at = datetime.now(timezone.utc)
            item.detail_json = json.dumps({"pid": os.getpid()}, separators=(",", ":"))
        else:
            db.add(ServiceHeartbeat(id=f"worker:{instance}", service="worker", instance=instance, detail_json=json.dumps({"pid": os.getpid()}, separators=(",", ":"))))


def _desktop_api():
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from ai.providers.base import TaskRequest
    from ai.service import get_ai_manager
    return get_ai_manager(), TaskRequest


def claim_task(worker_id: str | None = None) -> str | None:
    worker_id = worker_id or worker_instance()
    now = datetime.now(timezone.utc)
    with SessionLocal.begin() as db:
        query = select(GenerationTask).where(or_(
            GenerationTask.status == "queued",
            and_(GenerationTask.status == "running", or_(GenerationTask.lease_expires_at.is_(None), GenerationTask.lease_expires_at < now)),
        )).order_by(GenerationTask.created_at).with_for_update(skip_locked=True).limit(1)
        task = db.scalar(query)
        if not task: return None
        recovered = task.status == "running"
        user = db.get(User, task.owner_id)
        try:
            if not user or user.status != "active":
                raise HTTPException(403, "账号已被管理员暂停")
            enforce_existing_task_policy(db, user, task.provider, task.model, task.estimated_credits)
        except HTTPException as policy_error:
            task.status = "paused"; task.worker_id = None; task.lease_expires_at = None; task.error_code = "policy_revoked"; task.error_message = str(policy_error.detail)
            workflow = db.scalar(select(WorkflowRun).where(WorkflowRun.active_task_id == task.id))
            if workflow: workflow.status = "paused"; workflow.error_message = task.error_message
            production = db.get(ProductionRun, task.production_run_id) if task.production_run_id else None
            if production: production.status = "paused"; production.error_message = task.error_message
            return None
        task.status = "running"; task.progress = max(1, task.progress); task.worker_id = worker_id; task.lease_expires_at = _lease_deadline()
        if recovered:
            task.error_code = "worker_recovered"; task.error_message = "上一个 Worker 租约已过期，任务已安全接管"
        return task.id


def serialize_result(task: GenerationTask, result, worker_id: str = "") -> dict:
    data = result.data
    candidates = data if isinstance(data, list) else [data]
    files = [Path(value) for value in candidates if isinstance(value, (str, Path)) and Path(value).is_file()]
    asset_ids: list[str] = []
    try:
        with SessionLocal.begin() as db:
            if worker_id:
                owned = db.scalar(select(GenerationTask).where(GenerationTask.id == task.id).with_for_update())
                if not owned or owned.status != "running" or owned.worker_id != worker_id:
                    raise TaskLeaseLost("任务租约已被其他 Worker 接管")
            if files:
                user = db.get(User, task.owner_id)
                if not user: raise HTTPException(404, "素材所属账号不存在")
                enforce_asset_policy(db, user, sum(path.stat().st_size for path in files))
            for index, value in enumerate(files):
                asset = Asset(project_id=task.project_id, owner_id=task.owner_id, node_id=task.node_id, name=value.name, kind="file", object_key="pending", content_type="application/octet-stream", size=0, sha256="", status="processing", metadata_json=json.dumps({"task_id": task.id, "candidate": index}, ensure_ascii=False))
                db.add(asset); db.flush()
                key, size, digest, content_type = import_generated_file(value, task.project_id, asset.id)
                asset.object_key = key; asset.size = size; asset.sha256 = digest; asset.content_type = content_type; asset.kind = media_kind(content_type); asset.status = "ready"; asset_ids.append(asset.id)
    finally:
        _cleanup_provider_outputs(files)
    if asset_ids: return {"asset_ids": asset_ids}
    try: json.dumps(data, ensure_ascii=False); return {"data": data}
    except TypeError: return {"data": str(data)}


def _execute_task(task_id: str, worker_id: str) -> None:
    with SessionLocal() as db:
        task = db.get(GenerationTask, task_id)
        if not task or task.status != "running" or task.worker_id != worker_id: return
        provider, operation, raw = task.provider, task.kind, json.loads(task.input_json or "{}")
        hydrated_inputs = dict(raw.get("inputs", raw))
        references = hydrated_inputs.pop("references", [])
        typed_references = []
        for reference in references if isinstance(references, list) else []:
            if not isinstance(reference, dict) or not reference.get("asset_id"): continue
            asset = db.get(Asset, str(reference["asset_id"]))
            if asset and asset.project_id == task.project_id:
                role = str(reference.get("role") or "reference")
                typed_references.append({"path": str(resolve_object(asset.object_key)), "role": "character" if role == "subject" else role, "label": str(reference.get("title") or asset.name)})
        paths = [item["path"] for item in typed_references]
        if operation == "image_edit" and paths:
            hydrated_inputs.update({"image": paths[0], "images": paths, "reference_assets": typed_references})
        elif operation == "image_to_video" and paths:
            first = next((item for item in typed_references if item["role"] == "first_frame"), typed_references[0])
            last = next((item for item in typed_references if item["role"] == "last_frame"), None)
            hydrated_inputs["image"] = first["path"]
            if last and last["path"] != first["path"]: hydrated_inputs["last_frame"] = last["path"]
            hydrated_inputs["reference_assets"] = typed_references
        elif operation == "text_to_video" and paths:
            hydrated_inputs["reference_assets"] = typed_references[:50]
        elif operation == "text_to_speech":
            hydrated_inputs["text"] = hydrated_inputs.pop("prompt", "")
    if operation in {"extract_video_frames", "continue_video"}:
        try:
            if not paths: raise ValueError("请先连接或生成一个可用视频")
            import cv2
            source = paths[0]; cap = cv2.VideoCapture(source)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if frame_count <= 0: raise RuntimeError("无法读取视频帧")
            indices = [0, max(0, frame_count // 2), max(0, frame_count - 1)] if operation == "extract_video_frames" else [max(0, frame_count - 1)]
            output_dir = _task_scratch(task.id, "frames")
            frame_paths = []
            for index, frame_index in enumerate(indices):
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index); ok, frame = cap.read()
                if not ok: continue
                target = output_dir / f"frame-{index + 1}.jpg"; cv2.imwrite(str(target), frame); frame_paths.append(target)
            cap.release()
            if not frame_paths: raise RuntimeError("视频抽帧失败")
            if operation == "extract_video_frames":
                fake_result = type("FrameResult", (), {"data": frame_paths, "cost_credits": 0})()
                serialized = serialize_result(task, fake_result, worker_id)
                with SessionLocal.begin() as db:
                    current = db.get(GenerationTask, task_id)
                    if not current or current.status != "running" or current.worker_id != worker_id: raise TaskLeaseLost("任务租约已被其他 Worker 接管")
                    current.output_json = json.dumps(serialized, ensure_ascii=False); current.status = "completed"; current.progress = 100; current.error_code = None; current.error_message = None
                _cleanup_task_scratch(task_id)
                _sync_canvas(task_id)
                _finish_orchestrators(task_id, True, ""); return
            operation = "image_to_video"
            hydrated_inputs["image"] = str(frame_paths[-1])
            hydrated_inputs["reference_assets"] = [{"path": str(frame_paths[-1]), "role": "composition", "label": "上一段视频尾帧"}]
            continuation_contract = "从上一段视频的最后状态自然继续；第一帧必须匹配尾帧，保持角色身份、空间位置、动作方向与速度、道具、场景结构、光线和镜头轴线连续，不得重新开场或重复上一段动作。"
            user_direction = str(hydrated_inputs.get("prompt") or "").strip()
            hydrated_inputs["prompt"] = continuation_contract + (f"\n续写要求：{user_direction}" if user_direction else "")
        except TaskLeaseLost:
            _cleanup_task_scratch(task_id); return
        except Exception as error:
            with SessionLocal.begin() as db:
                current = db.get(GenerationTask, task_id)
                if current and current.status == "running" and current.worker_id == worker_id: current.status = "failed"; current.error_code = "video_frame_failed"; current.error_message = str(error)[:4000]
            _cleanup_task_scratch(task_id)
            _sync_canvas(task_id)
            _finish_orchestrators(task_id, False, str(error)); return
    if operation == "video_breakdown":
        breakdown_success = False; breakdown_error = ""
        try:
            if not paths: raise ValueError("AI 拉片节点必须连接一个视频资产节点")
            from ai.video_breakdown import analyze_video
            output_dir = _task_scratch(task.id, "breakdown")
            result = analyze_video(paths[0], output_dir)
            keyframe_shots = [shot for shot in result.get("shots", []) if shot.get("keyframe")]
            keyframe_paths = [shot["keyframe"] for shot in keyframe_shots]
            serialized = serialize_result(task, type("BreakdownFrames", (), {"data": keyframe_paths})(), worker_id) if keyframe_paths else {}
            for shot, asset_id in zip(keyframe_shots, serialized.get("asset_ids", [])):
                shot["keyframe_asset_id"] = asset_id; shot["keyframe_url"] = f"/api/assets/{asset_id}"; shot.pop("keyframe", None)
            result["source_asset_id"] = str(references[0].get("asset_id") or "") if references and isinstance(references[0], dict) else ""
            result.pop("source", None)
            with SessionLocal.begin() as db:
                current = db.get(GenerationTask, task_id)
                if current and current.status == "running" and current.worker_id == worker_id:
                    current.output_json = json.dumps({"analysis": result, "asset_ids": serialized.get("asset_ids", [])}, ensure_ascii=False, default=str); current.status = "completed"; current.progress = 100; current.error_code = None; current.error_message = None
                else: raise TaskLeaseLost("任务租约已被其他 Worker 接管")
            _sync_canvas(task_id)
            breakdown_success = True
        except TaskLeaseLost:
            _cleanup_task_scratch(task_id); return
        except Exception as error:
            breakdown_error = str(error)[:4000]
            with SessionLocal.begin() as db:
                current = db.get(GenerationTask, task_id)
                if current and current.status == "running" and current.worker_id == worker_id: current.status = "failed"; current.error_code = "breakdown_failed"; current.error_message = breakdown_error
            _sync_canvas(task_id)
        _cleanup_task_scratch(task_id)
        _finish_orchestrators(task_id, breakdown_success, breakdown_error)
        return
    manager, request_type = _desktop_api()
    succeeded = False; final_error = ""
    try:
        compiled_inputs, compiled_params = compile_request(operation, hydrated_inputs, raw.get("params", {}), str(raw.get("action") or ""), task.model)
        handle = manager.submit(provider, request_type(operation=operation, inputs=compiled_inputs, params=compiled_params, metadata={"server_task_id": task_id, "retry_transient_only": True}, use_cache=bool(raw.get("use_cache", False))))
        while not handle.is_finished:
            with SessionLocal.begin() as db:
                current = db.get(GenerationTask, task_id)
                if not current or current.status == "cancelled" or current.worker_id != worker_id:
                    handle.cancel(); _cleanup_task_scratch(task_id); return
                current.progress = max(1, min(99, int(handle.progress * 100)))
            time.sleep(0.5)
        serialized = serialize_result(task, handle.result, worker_id) if handle.is_success and handle.result else None
        with SessionLocal.begin() as db:
            current = db.get(GenerationTask, task_id)
            if not current or current.status != "running" or current.worker_id != worker_id: raise TaskLeaseLost("任务租约已被其他 Worker 接管")
            if handle.is_success and handle.result:
                current.output_json = json.dumps(serialized, ensure_ascii=False); current.status = "completed"; current.progress = 100; current.charged_credits = int(round(handle.result.cost_credits or 0)); current.error_code = None; current.error_message = None
                succeeded = True
            else:
                current.status = "failed"; current.error_code = "provider_failed"; current.error_message = (handle.result.error if handle.result else "任务未返回结果")[:4000]
                final_error = current.error_message
    except TaskLeaseLost:
        _cleanup_task_scratch(task_id); return
    except Exception as error:
        final_error = str(error)[:4000]
        with SessionLocal.begin() as db:
            current = db.get(GenerationTask, task_id)
            if current and current.status == "running" and current.worker_id == worker_id: current.status = "failed"; current.error_code = "worker_error"; current.error_message = final_error
    _sync_canvas(task_id)
    _cleanup_task_scratch(task_id)
    _finish_orchestrators(task_id, succeeded, final_error)


def execute_task(task_id: str, worker_id: str | None = None) -> None:
    worker_id = worker_id or worker_instance()
    with SessionLocal.begin() as db:
        task = db.get(GenerationTask, task_id)
        if not task or task.status != "running": return
        if task.worker_id and task.worker_id != worker_id: return
        task.worker_id = worker_id; task.lease_expires_at = _lease_deadline()
    stop = threading.Event()
    heartbeat = threading.Thread(target=_lease_heartbeat, args=(task_id, worker_id, stop), daemon=True)
    heartbeat.start()
    try:
        _execute_task(task_id, worker_id)
    finally:
        stop.set(); heartbeat.join(timeout=2)
        with SessionLocal.begin() as db:
            task = db.get(GenerationTask, task_id)
            if task and task.worker_id == worker_id and task.status != "running":
                task.worker_id = None; task.lease_expires_at = None


def main() -> None:
    create_schema(); settings = get_settings()
    instance = worker_instance()
    print(f"Creative Engine worker started: {instance}")
    last_heartbeat = 0.0
    while True:
        try:
            if time.monotonic() - last_heartbeat >= 10:
                record_heartbeat(); last_heartbeat = time.monotonic()
            task_id = claim_task(instance)
            if task_id: execute_task(task_id, instance)
            else: time.sleep(settings.worker_poll_seconds)
        except KeyboardInterrupt:
            raise
        except Exception as error:
            print(f"Worker loop error: {error}", file=sys.stderr, flush=True)
            time.sleep(max(1.0, settings.worker_poll_seconds))


if __name__ == "__main__":
    main()
