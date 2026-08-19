from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from .models import Asset
from .provider_catalog import available_providers


MEDIA_INPUT_OPERATIONS = {"image_edit", "image_to_video", "continue_video", "extract_video_frames", "video_breakdown"}
EXPECTED_REFERENCE_KINDS = {
    "image_edit": {"image"},
    "image_to_video": {"image"},
    "text_to_video": {"image"},
    "continue_video": {"video"},
    "extract_video_frames": {"video"},
    "video_breakdown": {"video"},
}


def validate_task_request(db: Session, project_id: str, kind: str, provider: str, task_input: dict) -> None:
    profile = next((item for item in available_providers() if item["name"] == provider), None)
    inputs = task_input.get("inputs", {}) if isinstance(task_input, dict) else {}
    references = inputs.get("references", []) if isinstance(inputs, dict) else []
    provider_operation = "image_to_video" if kind == "continue_video" else kind
    if provider != "local" and (not profile or provider_operation not in profile.get("capabilities", [])):
        raise HTTPException(status.HTTP_409_CONFLICT, f"已选择引擎 {provider or '空'} 当前不可用或不支持 {provider_operation}；任务未入队，系统不会静默切换")
    reference_limit = int((profile or {}).get("profile", {}).get("reference_assets") or 0)
    if reference_limit and isinstance(references, list) and len(references) > reference_limit:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{provider} 当前单次最多支持 {reference_limit} 张参考图；请减少输入或拆成连续段落，系统不会静默丢图")
    if kind in MEDIA_INPUT_OPERATIONS and (not isinstance(references, list) or not references):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "该操作必须连接一个已生成或已上传的媒体节点")
    if not isinstance(references, list):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "参考输入格式无效")
    allowed_kinds = EXPECTED_REFERENCE_KINDS.get(kind)
    for reference in references:
        asset_id = str(reference.get("asset_id") or "") if isinstance(reference, dict) else ""
        asset = db.get(Asset, asset_id) if asset_id else None
        if not asset or asset.project_id != project_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "参考节点尚无可用媒体，或媒体不属于当前项目；任务未入队")
        if allowed_kinds and asset.kind not in allowed_kinds:
            expected = "图片" if allowed_kinds == {"image"} else "视频"
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{kind} 只接受{expected}节点作为输入；任务未入队")
