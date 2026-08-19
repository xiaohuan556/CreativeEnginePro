from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from sqlalchemy import select

from .config import get_settings
from .database import SessionLocal, create_schema
from .models import Asset, GenerationTask
from .storage import import_generated_file, media_kind, resolve_object
from .request_compiler import compile_request


def _desktop_api():
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from ai.providers.base import TaskRequest
    from ai.service import get_ai_manager
    return get_ai_manager(), TaskRequest


def claim_task() -> str | None:
    with SessionLocal.begin() as db:
        query = select(GenerationTask).where(GenerationTask.status == "queued").order_by(GenerationTask.created_at).with_for_update(skip_locked=True).limit(1)
        task = db.scalar(query)
        if not task: return None
        task.status = "running"; task.progress = 1
        return task.id


def serialize_result(task: GenerationTask, result) -> dict:
    data = result.data
    candidates = data if isinstance(data, list) else [data]
    asset_ids: list[str] = []
    with SessionLocal.begin() as db:
        for index, value in enumerate(candidates):
            if not isinstance(value, (str, Path)) or not Path(value).is_file():
                continue
            asset = Asset(project_id=task.project_id, owner_id=task.owner_id, node_id=task.node_id, name=Path(value).name, kind="file", object_key="pending", content_type="application/octet-stream", size=0, sha256="", status="processing", metadata_json=json.dumps({"task_id": task.id, "candidate": index}, ensure_ascii=False))
            db.add(asset); db.flush()
            key, size, digest, content_type = import_generated_file(value, task.project_id, asset.id)
            asset.object_key = key; asset.size = size; asset.sha256 = digest; asset.content_type = content_type; asset.kind = media_kind(content_type); asset.status = "ready"; asset_ids.append(asset.id)
    if asset_ids: return {"asset_ids": asset_ids}
    try: json.dumps(data, ensure_ascii=False); return {"data": data}
    except TypeError: return {"data": str(data)}


def execute_task(task_id: str) -> None:
    with SessionLocal() as db:
        task = db.get(GenerationTask, task_id)
        if not task: return
        provider, operation, raw = task.provider, task.kind, json.loads(task.input_json or "{}")
        hydrated_inputs = dict(raw.get("inputs", raw))
        references = hydrated_inputs.pop("references", [])
        typed_references = []
        for reference in references if isinstance(references, list) else []:
            if not isinstance(reference, dict) or not reference.get("asset_id"): continue
            asset = db.get(Asset, str(reference["asset_id"]))
            if asset and asset.project_id == task.project_id:
                typed_references.append({"path": str(resolve_object(asset.object_key)), "role": str(reference.get("role") or "reference"), "label": str(reference.get("title") or asset.name)})
        paths = [item["path"] for item in typed_references]
        if operation == "image_edit" and paths:
            hydrated_inputs.update({"image": paths[0], "images": paths, "reference_assets": typed_references})
        elif operation == "image_to_video" and paths:
            hydrated_inputs["image"] = paths[0]
            if len(paths) > 1: hydrated_inputs["last_frame"] = paths[1]
            hydrated_inputs["reference_assets"] = typed_references
        elif operation == "text_to_video" and paths:
            hydrated_inputs["reference_assets"] = typed_references[:9]
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
            output_dir = Path(get_settings().storage_dir) / task.project_id[:12] / f"frames-{task.id}"; output_dir.mkdir(parents=True, exist_ok=True)
            frame_paths = []
            for index, frame_index in enumerate(indices):
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index); ok, frame = cap.read()
                if not ok: continue
                target = output_dir / f"frame-{index + 1}.jpg"; cv2.imwrite(str(target), frame); frame_paths.append(target)
            cap.release()
            if not frame_paths: raise RuntimeError("视频抽帧失败")
            if operation == "extract_video_frames":
                fake_result = type("FrameResult", (), {"data": frame_paths, "cost_credits": 0})()
                with SessionLocal.begin() as db:
                    current = db.get(GenerationTask, task_id)
                    current.output_json = json.dumps(serialize_result(current, fake_result), ensure_ascii=False); current.status = "completed"; current.progress = 100
                from .production import on_task_finished
                on_task_finished(task_id, True, ""); return
            operation = "image_to_video"
            hydrated_inputs["image"] = str(frame_paths[-1])
            hydrated_inputs["reference_assets"] = [{"path": str(frame_paths[-1]), "role": "composition", "label": "上一段视频尾帧"}]
            hydrated_inputs["prompt"] = str(hydrated_inputs.get("prompt") or "从上一段视频的最后状态自然继续，动作、方向、速度、角色身份、场景和光线无缝衔接。")
        except Exception as error:
            with SessionLocal.begin() as db:
                current = db.get(GenerationTask, task_id)
                if current: current.status = "failed"; current.error_code = "video_frame_failed"; current.error_message = str(error)[:4000]
            from .production import on_task_finished
            on_task_finished(task_id, False, str(error)); return
    if operation == "video_breakdown":
        breakdown_success = False; breakdown_error = ""
        try:
            if not paths: raise ValueError("AI 拉片节点必须连接一个视频资产节点")
            from ai.video_breakdown import analyze_video
            output_dir = Path(get_settings().storage_dir) / task.project_id[:12] / f"breakdown-{task.id}"
            result = analyze_video(paths[0], output_dir)
            with SessionLocal.begin() as db:
                current = db.get(GenerationTask, task_id)
                if current: current.output_json = json.dumps({"analysis": result}, ensure_ascii=False, default=str); current.status = "completed"; current.progress = 100
            breakdown_success = True
        except Exception as error:
            breakdown_error = str(error)[:4000]
            with SessionLocal.begin() as db:
                current = db.get(GenerationTask, task_id)
                if current: current.status = "failed"; current.error_code = "breakdown_failed"; current.error_message = breakdown_error
        from .production import on_task_finished
        on_task_finished(task_id, breakdown_success, breakdown_error)
        return
    manager, request_type = _desktop_api()
    succeeded = False; final_error = ""
    try:
        compiled_inputs, compiled_params = compile_request(operation, hydrated_inputs, raw.get("params", {}), str(raw.get("action") or ""), task.model)
        handle = manager.submit(provider, request_type(operation=operation, inputs=compiled_inputs, params=compiled_params, metadata={"server_task_id": task_id, "retry_transient_only": True}, use_cache=bool(raw.get("use_cache", False))))
        while not handle.is_finished:
            with SessionLocal.begin() as db:
                current = db.get(GenerationTask, task_id)
                if not current or current.status == "cancelled":
                    handle.cancel(); return
                current.progress = max(1, min(99, int(handle.progress * 100)))
            time.sleep(0.5)
        with SessionLocal.begin() as db:
            current = db.get(GenerationTask, task_id)
            if handle.is_success and handle.result:
                current.output_json = json.dumps(serialize_result(current, handle.result), ensure_ascii=False); current.status = "completed"; current.progress = 100; current.charged_credits = int(round(handle.result.cost_credits or 0))
                succeeded = True
            else:
                current.status = "failed"; current.error_code = "provider_failed"; current.error_message = (handle.result.error if handle.result else "任务未返回结果")[:4000]
                final_error = current.error_message
    except Exception as error:
        final_error = str(error)[:4000]
        with SessionLocal.begin() as db:
            current = db.get(GenerationTask, task_id)
            if current: current.status = "failed"; current.error_code = "worker_error"; current.error_message = final_error
    from .production import on_task_finished
    on_task_finished(task_id, succeeded, final_error)


def main() -> None:
    create_schema(); settings = get_settings()
    print("Creative Engine worker started")
    while True:
        task_id = claim_task()
        if task_id: execute_task(task_id)
        else: time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
