"""Paid, end-to-end acceptance test for the complete AI canvas media chain.

This script intentionally refuses to run unless the operator supplies explicit
provider locks and confirms paid execution. It leaves a timestamped project in
the control plane as auditable acceptance evidence and never prints passwords
or provider secrets.
"""
from __future__ import annotations

import os
import secrets
import sys
import time
from datetime import datetime, timezone
from typing import Any

import httpx


BASE_URL = os.environ.get("CEP_SMOKE_BASE_URL", "").rstrip("/")
CONTROL_PATH = os.environ.get("CEP_SMOKE_CONTROL_PATH", "/control").rstrip("/")
ADMIN_USERNAME = os.environ.get("CEP_SMOKE_ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.environ.get("CEP_SMOKE_ADMIN_PASSWORD", "")
LLM_PROVIDER = os.environ.get("CEP_SMOKE_LLM_PROVIDER", "")
LLM_MODEL = os.environ.get("CEP_SMOKE_LLM_MODEL", "")
IMAGE_PROVIDER = os.environ.get("CEP_SMOKE_IMAGE_PROVIDER", "")
IMAGE_MODEL = os.environ.get("CEP_SMOKE_IMAGE_MODEL", "")
VIDEO_PROVIDER = os.environ.get("CEP_SMOKE_VIDEO_PROVIDER", "")
VIDEO_MODEL = os.environ.get("CEP_SMOKE_VIDEO_MODEL", "")
CONFIRM = os.environ.get("CEP_SMOKE_PAID_CONFIRM", "")
TIMEOUT = float(os.environ.get("CEP_SMOKE_TIMEOUT_SECONDS", "1200"))


def fail(message: str) -> None:
    raise RuntimeError(message)


def expect(response: httpx.Response, status_code: int, label: str) -> dict[str, Any]:
    if response.status_code != status_code:
        fail(f"{label}: expected HTTP {status_code}, got {response.status_code}: {response.text[:500]}")
    value = response.json() if response.content else {}
    return value if isinstance(value, dict) else {"value": value}


def node(node_id: str, title: str, spec_key: str, desktop_type: str, kind: str, description: str, provider: str = "") -> dict[str, Any]:
    return {
        "id": node_id, "type": "studio", "position": {"x": 80, "y": 80},
        "data": {
            "title": title, "description": description, "kind": kind,
            "specKey": spec_key, "desktopType": desktop_type, "status": "待验收",
            "meta": "full-pipeline-smoke", "accent": "#6f8cff",
            "desktopPayload": {"provider_name": provider},
        },
    }


def main() -> int:
    required = {
        "CEP_SMOKE_BASE_URL": BASE_URL,
        "CEP_SMOKE_ADMIN_USERNAME": ADMIN_USERNAME,
        "CEP_SMOKE_ADMIN_PASSWORD": ADMIN_PASSWORD,
        "CEP_SMOKE_LLM_PROVIDER": LLM_PROVIDER,
        "CEP_SMOKE_IMAGE_PROVIDER": IMAGE_PROVIDER,
        "CEP_SMOKE_VIDEO_PROVIDER": VIDEO_PROVIDER,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        print("Missing: " + ", ".join(missing), file=sys.stderr)
        return 2
    if CONFIRM != "RUN_PAID_PIPELINE":
        print("Refusing paid model calls. Set CEP_SMOKE_PAID_CONFIRM=RUN_PAID_PIPELINE after reviewing provider billing.", file=sys.stderr)
        return 3

    api = f"{BASE_URL}{CONTROL_PATH}"
    client = httpx.Client(follow_redirects=True, headers={"user-agent": "CreativeEngineFullPipelineSmoke/1.0"})
    csrf = ""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    checks: list[str] = []

    def headers(idempotency: str = "") -> dict[str, str]:
        result = {"x-csrf-token": csrf}
        if idempotency:
            result["idempotency-key"] = idempotency
        return result

    def submit(project_id: str, node_id: str, kind: str, provider: str, model: str, task_input: dict[str, Any]) -> str:
        idem = f"full-smoke:{stamp}:{kind}:{secrets.token_hex(4)}"
        payload = {
            "project_id": project_id, "node_id": node_id, "kind": kind,
            "provider": provider, "model": model, "input": task_input,
        }
        task = expect(client.post(f"{api}/api/tasks", headers=headers(idem), json=payload, timeout=30), 202, f"submit {kind}")["task"]
        return str(task["id"])

    def wait_task(task_id: str, label: str) -> dict[str, Any]:
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            task = expect(client.get(f"{api}/api/tasks/{task_id}", timeout=30), 200, f"poll {label}")["task"]
            if task["status"] == "completed":
                return task
            if task["status"] in ("failed", "cancelled", "paused"):
                fail(f"{label} stopped in {task['status']}: {task.get('error_message') or task.get('error_code') or ''}")
            time.sleep(2)
        fail(f"{label} exceeded {TIMEOUT:g} seconds")

    def first_asset(task: dict[str, Any], expected_kind: str, project_id: str, label: str) -> str:
        asset_ids = (task.get("output") or {}).get("asset_ids") or []
        if not asset_ids:
            fail(f"{label} returned no persisted asset")
        assets = expect(client.get(f"{api}/api/assets", params={"project_id": project_id}, timeout=30), 200, f"list assets after {label}").get("assets", [])
        asset = next((item for item in assets if str(item.get("id")) == str(asset_ids[0])), None)
        if not asset or asset.get("kind") != expected_kind or asset.get("status") != "ready":
            fail(f"{label} asset is not a ready {expected_kind}")
        return str(asset["id"])

    try:
        expect(client.get(f"{api}/health", timeout=15), 200, "liveness")
        login = expect(client.post(f"{api}/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}, timeout=30), 200, "admin login")
        if login.get("user", {}).get("role") != "admin":
            fail("full pipeline credentials are not an admin account")
        csrf = str(login.get("csrf_token") or "")
        if not csrf:
            fail("admin login returned no CSRF token")

        readiness = expect(client.get(f"{api}/api/admin/readiness", timeout=20), 200, "generation readiness")
        if not readiness.get("control_ready") or int(readiness.get("active_workers") or 0) < 1:
            fail("control plane or worker is not ready")
        providers = expect(client.get(f"{api}/api/providers", timeout=20), 200, "allowed providers").get("providers", [])
        provider_caps = {str(item.get("name")): set(item.get("capabilities") or []) for item in providers}
        required_locks = {
            LLM_PROVIDER: {"chat"},
            IMAGE_PROVIDER: {"text_to_image", "image_edit"},
            VIDEO_PROVIDER: {"image_to_video"},
        }
        for provider, capabilities in required_locks.items():
            missing_caps = capabilities - provider_caps.get(provider, set())
            if missing_caps:
                fail(f"locked provider {provider} is unavailable, disallowed, or lacks: {', '.join(sorted(missing_caps))}")
        checks.extend(["explicit_provider_locks", "worker_ready"])

        canvas_nodes = [
            node("accept-script", "验收脚本", "script", "text_node", "script", "写一个12秒动漫机器人雨夜送信短片，不出现真人。", LLM_PROVIDER),
            node("accept-image", "验收关键帧", "multi_image", "image_node", "image", "二维电影动画，蓝色圆头送信机器人站在雨巷，手持唯一一封黄色信。", IMAGE_PROVIDER),
            node("accept-edit", "验收图生图", "multi_image", "image_node", "image", "保持机器人身份与构图，把时间改成清晨。", IMAGE_PROVIDER),
            node("accept-video", "验收视频", "video", "video_node", "video", "机器人向前走三步，把唯一信封放入邮箱。", VIDEO_PROVIDER),
            node("accept-continue", "验收续拍", "video", "video_node", "video", "邮箱亮起暖光，机器人停步回头。", VIDEO_PROVIDER),
            node("accept-breakdown", "验收拉片", "analysis", "video_analysis_node", "analysis", "分析切镜、运镜、动作轨迹、节奏和声音。"),
            node("accept-voice", "验收配音", "audio", "audio_node", "audio", "信，终于送到了。", "edge_tts"),
        ]
        canvas = {"protocol": "creative-engine-canvas", "version": 1, "nodes": canvas_nodes, "edges": []}
        project = expect(client.post(f"{api}/api/projects", headers=headers(), json={"title": f"完整模型链路验收 {stamp}", "canvas": canvas}, timeout=30), 201, "create acceptance project")["project"]
        project_id = str(project["id"])

        chat = wait_task(submit(project_id, "accept-script", "chat", LLM_PROVIDER, LLM_MODEL, {"inputs": {"prompt": canvas_nodes[0]["data"]["description"]}, "params": {}, "action": "生成完整脚本"}), "script generation")
        if not (chat.get("output") or {}).get("data"):
            fail("script generation returned no text")
        checks.append("script_generation")

        image = wait_task(submit(project_id, "accept-image", "text_to_image", IMAGE_PROVIDER, IMAGE_MODEL, {"inputs": {"prompt": canvas_nodes[1]["data"]["description"]}, "params": {"ratio": "16:9", "candidate_count": 1}, "action": "生成图片"}), "text to image")
        image_id = first_asset(image, "image", project_id, "text to image")
        checks.append("text_to_image")

        edited = wait_task(submit(project_id, "accept-edit", "image_edit", IMAGE_PROVIDER, IMAGE_MODEL, {"inputs": {"prompt": canvas_nodes[2]["data"]["description"], "references": [{"asset_id": image_id, "role": "subject", "title": "身份与构图参考"}]}, "params": {"ratio": "16:9", "candidate_count": 1}, "action": "图生图"}), "image edit")
        edited_id = first_asset(edited, "image", project_id, "image edit")
        checks.append("image_edit")

        video = wait_task(submit(project_id, "accept-video", "image_to_video", VIDEO_PROVIDER, VIDEO_MODEL, {"inputs": {"prompt": canvas_nodes[3]["data"]["description"], "references": [{"asset_id": edited_id, "role": "first_frame", "title": "视频首帧"}]}, "params": {"duration": 5, "ratio": "16:9", "resolution": "720p", "generate_audio": True, "audio_prompt": "雨声与脚步声同步，信封放入邮箱时有轻微金属声"}, "action": "图生视频"}), "image to video")
        video_id = first_asset(video, "video", project_id, "image to video")
        checks.append("image_to_video_with_audio")

        continued = wait_task(submit(project_id, "accept-continue", "continue_video", VIDEO_PROVIDER, VIDEO_MODEL, {"inputs": {"prompt": canvas_nodes[4]["data"]["description"], "references": [{"asset_id": video_id, "role": "reference", "title": "上一段成片"}]}, "params": {"duration": 5, "ratio": "16:9", "resolution": "720p", "generate_audio": True, "audio_prompt": "延续雨声；邮箱亮起时出现轻柔提示音"}, "action": "基于尾帧续拍"}), "tail frame continuation")
        continued_id = first_asset(continued, "video", project_id, "tail frame continuation")
        checks.append("tail_frame_continuation")

        breakdown = wait_task(submit(project_id, "accept-breakdown", "video_breakdown", "local", "", {"inputs": {"references": [{"asset_id": continued_id, "role": "reference", "title": "续拍成片"}]}, "params": {}, "action": "开始拉片"}), "video breakdown")
        if not (breakdown.get("output") or {}).get("analysis"):
            fail("video breakdown returned no analysis")
        checks.append("video_breakdown")

        voice = wait_task(submit(project_id, "accept-voice", "text_to_speech", "edge_tts", "", {"inputs": {"prompt": canvas_nodes[6]["data"]["description"]}, "params": {"voice": "zh-CN-XiaoxiaoNeural", "speed": 1}, "action": "对白配音"}), "voice generation")
        first_asset(voice, "audio", project_id, "voice generation")
        checks.append("voice_generation")

        updated = expect(client.get(f"{api}/api/projects/{project_id}", timeout=30), 200, "canvas writeback")["project"]
        updated_nodes = {str(item.get("id")): item for item in updated.get("canvas", {}).get("nodes", [])}
        for node_id in ("accept-script", "accept-image", "accept-edit", "accept-video", "accept-continue", "accept-breakdown", "accept-voice"):
            payload = updated_nodes.get(node_id, {}).get("data", {}).get("desktopPayload", {})
            if not payload.get("server_task_id"):
                fail(f"{node_id} result was not written back to its canvas node")
        checks.append("all_results_written_to_canvas")
    finally:
        client.close()

    print("Creative Engine full model pipeline smoke passed")
    print("Checks: " + ", ".join(checks))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (httpx.RequestError, RuntimeError, ValueError) as error:
        print(f"Full pipeline smoke failed: {error}", file=sys.stderr)
        raise SystemExit(1)
