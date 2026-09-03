"""Authenticated desktop proxy for centrally managed AI providers."""
from __future__ import annotations

import copy
import time
import uuid
from pathlib import Path
from typing import Any

from core.release_gate import DesktopAuthClient

from .base import (
    AIProvider, ProviderDomain, TaskHandle, TaskRequest, TaskResult, TaskStatus,
)


def _provider_domain(capabilities: list[str]) -> ProviderDomain:
    values = set(capabilities)
    if values & {"text_to_image", "image_edit", "inpaint"}:
        return ProviderDomain.IMAGE
    if values & {"text_to_video", "image_to_video", "continue_video", "video_edit"}:
        return ProviderDomain.VIDEO
    if values & {"text_to_speech", "clone_voice"}:
        return ProviderDomain.VOICE
    return ProviderDomain.LLM


class ControlPlaneProvider(AIProvider):
    """Expose one server-approved provider through the existing TaskManager."""

    requires_auth = False

    def __init__(self, client: DesktopAuthClient, descriptor: dict[str, Any]):
        self.client = client
        self.name = str(descriptor.get("name") or "").strip()
        self.capabilities = [str(value) for value in descriptor.get("capabilities", [])]
        self.domain = _provider_domain(self.capabilities)
        self.profile = dict(descriptor.get("profile") or {})
        super().__init__(api_key="", control_plane=True)

    def capability_profile(self) -> dict:
        return {"name": self.name, "operations": list(self.capabilities), **self.profile}

    def _prepare_inputs(self, request: TaskRequest, node_id: str) -> dict[str, Any]:
        inputs = copy.deepcopy(request.inputs)
        references: list[dict[str, str]] = []
        uploaded: dict[str, str] = {}

        def add_path(value: Any, role: str, title: str = "") -> bool:
            if not isinstance(value, (str, Path)):
                return False
            source = Path(str(value)).expanduser()
            if not source.is_file():
                return False
            key = str(source.resolve()).casefold()
            asset_id = uploaded.get(key)
            if not asset_id:
                asset = self.client.upload_asset(source, node_id)
                asset_id = str(asset.get("id") or "")
                if not asset_id:
                    raise RuntimeError("中心服务没有返回参考素材 ID")
                uploaded[key] = asset_id
            references.append({
                "asset_id": asset_id, "role": role,
                "title": title or source.name,
            })
            return True

        role_keys = {
            "image": "first_frame" if request.operation == "image_to_video" else "reference",
            "last_frame": "last_frame",
            "mask": "mask",
            "reference_audio": "reference_audio",
        }
        for key, role in role_keys.items():
            if key in inputs and add_path(inputs.get(key), role):
                inputs.pop(key, None)

        for key, role in (("images", "reference"), ("style_images", "style")):
            remote_values = []
            for value in list(inputs.get(key) or []):
                if not add_path(value, role):
                    remote_values.append(value)
            if remote_values:
                inputs[key] = remote_values
            else:
                inputs.pop(key, None)

        typed = list(inputs.pop("reference_assets", []) or [])
        retained = []
        for item in typed:
            if isinstance(item, dict):
                role = str(item.get("role") or item.get("purpose") or "reference")
                title = str(item.get("title") or item.get("label") or "")
                if add_path(item.get("path"), role, title):
                    continue
            retained.append(item)
        if retained:
            inputs["reference_assets"] = retained
        if references:
            inputs["references"] = references
        return inputs

    def execute(self, request: TaskRequest) -> TaskHandle:
        handle = TaskHandle(
            id=f"remote_{uuid.uuid4().hex[:12]}", provider_name=self.name,
            operation=request.operation, status=TaskStatus.RUNNING,
        )
        server_task_id = ""
        try:
            project_id = self.client.ensure_desktop_project()
            node_id = str(request.metadata.get("canvas_node_id") or handle.id)[:128]
            inputs = self._prepare_inputs(request, node_id)
            model = str(request.params.get("model") or self.profile.get("model") or "")
            created = self.client.request_json(
                "POST", "/api/tasks", {
                    "project_id": project_id,
                    "node_id": node_id,
                    "kind": request.operation,
                    "provider": self.name,
                    "model": model,
                    "input": {
                        "inputs": inputs,
                        "params": copy.deepcopy(request.params),
                        "use_cache": bool(request.use_cache),
                    },
                }, write=True,
                extra_headers={"idempotency-key": f"desktop:{uuid.uuid4().hex}"},
            )
            server_task_id = str((created.get("task") or {}).get("id") or "")
            if not server_task_id:
                raise RuntimeError("中心服务没有返回任务 ID")

            deadline = time.monotonic() + 7200
            task: dict[str, Any] = {}
            while time.monotonic() < deadline:
                task = dict((self.client.request_json(
                    "GET", f"/api/tasks/{server_task_id}").get("task") or {}))
                status = str(task.get("status") or "")
                handle.progress = max(0.0, min(0.99, float(task.get("progress") or 0) / 100.0))
                if status in {"completed", "failed", "cancelled", "paused"}:
                    break
                time.sleep(0.75)
            else:
                raise TimeoutError("中心生成任务等待超过 2 小时")

            status = str(task.get("status") or "")
            if status == "completed":
                output = dict(task.get("output") or {})
                asset_ids = [str(value) for value in output.get("asset_ids", []) if value]
                if asset_ids:
                    paths = [self.client.download_asset(asset_id) for asset_id in asset_ids]
                    data: Any = paths[0] if len(paths) == 1 else paths
                else:
                    data = output.get("data")
                    if data is None and "analysis" in output:
                        data = output["analysis"]
                handle.status = TaskStatus.DONE
                handle.progress = 1.0
                handle.result = TaskResult(
                    success=True, data=data,
                    provider_raw={"server_task_id": server_task_id, "output": output},
                )
            elif status == "cancelled":
                handle.status = TaskStatus.CANCELLED
                handle.result = TaskResult(success=False, error="中心生成任务已取消")
            else:
                handle.status = TaskStatus.FAILED
                handle.result = TaskResult(
                    success=False,
                    error=str(task.get("error_message") or "中心生成任务失败"),
                    provider_raw={"server_task_id": server_task_id},
                )
        except Exception as error:
            handle.status = TaskStatus.FAILED
            handle.result = TaskResult(
                success=False, error=str(error),
                provider_raw={"server_task_id": server_task_id},
            )
        handle.finished_at = time.time()
        return handle
