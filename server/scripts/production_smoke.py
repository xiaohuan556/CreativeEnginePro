"""Production smoke test for the company control plane.

Required environment variables:
  CEP_SMOKE_BASE_URL, CEP_SMOKE_ADMIN_USERNAME, CEP_SMOKE_ADMIN_PASSWORD

The test creates an auditable smoke project and a temporary reviewer account.
The reviewer is suspended and all of its sessions are revoked before exit.
Passwords are never printed.
"""
from __future__ import annotations

import base64
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
TIMEOUT = float(os.environ.get("CEP_SMOKE_TIMEOUT_SECONDS", "240"))
SKIP_GENERATION = os.environ.get("CEP_SMOKE_SKIP_GENERATION", "0") == "1"
PNG_1X1 = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nKAAAAAASUVORK5CYII=")


def fail(message: str) -> None:
    raise RuntimeError(message)


def expect(response: httpx.Response, status_code: int, label: str) -> dict[str, Any]:
    if response.status_code != status_code:
        fail(f"{label}: expected HTTP {status_code}, got {response.status_code}: {response.text[:500]}")
    if not response.content:
        return {}
    value = response.json()
    return value if isinstance(value, dict) else {"value": value}


def main() -> int:
    if not BASE_URL or not ADMIN_USERNAME or not ADMIN_PASSWORD:
        print("Missing CEP_SMOKE_BASE_URL / CEP_SMOKE_ADMIN_USERNAME / CEP_SMOKE_ADMIN_PASSWORD", file=sys.stderr)
        return 2
    api = f"{BASE_URL}{CONTROL_PATH}"
    admin = httpx.Client(follow_redirects=True, headers={"user-agent": "CreativeEngineProductionSmoke/1.0"})
    reviewer = httpx.Client(follow_redirects=True, headers={"user-agent": "CreativeEngineProductionSmoke/1.0"})
    created_user_id = ""
    csrf = ""
    checks: list[str] = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    suffix = secrets.token_hex(3)
    reviewer_username = f"smoke.{stamp[-8:]}.{suffix}"
    reviewer_password = f"Smoke-{secrets.token_urlsafe(12)}-Aa9!"

    def admin_headers() -> dict[str, str]:
        return {"x-csrf-token": csrf}

    try:
        expect(admin.get(f"{api}/health", timeout=15), 200, "liveness")
        expect(admin.get(f"{api}/ready", timeout=15), 200, "readiness")
        checks.extend(["api_liveness", "database_and_storage"])

        login = expect(admin.post(f"{api}/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}, timeout=20), 200, "admin login")
        if login.get("user", {}).get("role") != "admin": fail("smoke credentials are not an admin account")
        csrf = str(login.get("csrf_token") or "")
        if not csrf: fail("admin login returned no CSRF token")
        checks.append("admin_login")

        system = expect(admin.get(f"{api}/api/admin/readiness", timeout=15), 200, "admin readiness")
        if not system.get("database") or not system.get("storage"): fail("database or media storage is not ready")
        if not SKIP_GENERATION and int(system.get("active_workers") or 0) < 1: fail("no active generation worker heartbeat")
        checks.append("worker_heartbeat" if not SKIP_GENERATION else "worker_heartbeat_skipped")

        canvas = {
            "protocol": "creative-engine-canvas", "version": 1,
            "nodes": [
                {"id": "smoke-voice-1", "type": "studio", "position": {"x": 80, "y": 80}, "data": {"title": "冒烟配音 1", "description": "正式环境端到端验证第一段。", "kind": "audio", "specKey": "audio", "desktopType": "audio_node", "status": "待生成", "meta": "smoke", "accent": "#66d49a", "desktopPayload": {"provider_name": "edge_tts"}}},
                {"id": "smoke-voice-2", "type": "studio", "position": {"x": 420, "y": 80}, "data": {"title": "冒烟配音 2", "description": "正式环境端到端验证第二段。", "kind": "audio", "specKey": "audio", "desktopType": "audio_node", "status": "待生成", "meta": "smoke", "accent": "#66d49a", "desktopPayload": {"provider_name": "edge_tts"}}},
            ], "edges": [],
        }
        project = expect(admin.post(f"{api}/api/projects", headers=admin_headers(), json={"title": f"生产验收 {stamp}", "canvas": canvas}, timeout=20), 201, "create smoke project")["project"]
        project_id = str(project["id"]); checks.append("project_persistence")

        # Persist a representative desktop-compatible graph, not merely an
        # empty project shell.  The edge relation and director timeline are
        # contracts consumed by both desktop and web request compilation.
        roundtrip_canvas = {
            "protocol": "creative-engine-canvas", "version": 1,
            "nodes": [*canvas["nodes"],
                {"id": "smoke-image-1", "type": "studio", "position": {"x": 80, "y": 80}, "data": {"title": "首帧", "description": "一致性首帧", "kind": "image", "specKey": "image_asset", "desktopType": "image_node", "status": "待上传", "meta": "smoke", "accent": "#50b9dd", "desktopPayload": {}}},
                {"id": "smoke-director-1", "type": "studio", "position": {"x": 420, "y": 80}, "data": {"title": "多图导演", "description": "0-3 秒缓慢推近", "kind": "director", "specKey": "multi_director", "desktopType": "video_node", "status": "待生成", "meta": "smoke", "accent": "#6f8cff", "desktopPayload": {"multi_image_director": True, "duration": 6, "timeline_images": [{"source_node_id": "smoke-image-1", "purpose": "first_frame", "start": 0, "end": 3, "action": "抬头", "camera": "缓慢推近"}]}}},
            ],
            "edges": [{"id": "smoke-edge-1", "source": "smoke-image-1", "target": "smoke-director-1", "type": "pulse", "data": {"relation": "first_frame"}}],
        }
        synced = expect(admin.patch(f"{api}/api/projects/{project_id}", headers=admin_headers(), json={"title": project["title"], "canvas": roundtrip_canvas, "expectedVersion": int(project["version"])}, timeout=20), 200, "persist graph roundtrip")["project"]
        reloaded = expect(admin.get(f"{api}/api/projects/{project_id}", timeout=20), 200, "reload graph roundtrip")["project"]
        if reloaded.get("canvas") != roundtrip_canvas:
            fail("nodes, edges, relation or director timeline changed after persistence")
        stale = admin.patch(f"{api}/api/projects/{project_id}", headers=admin_headers(), json={"title": "stale-write-must-fail", "canvas": roundtrip_canvas, "expectedVersion": int(project["version"])}, timeout=20)
        if stale.status_code != 409:
            fail("stale canvas write was not rejected with a version conflict")
        project = synced
        checks.extend(["node_edge_roundtrip", "optimistic_write_conflict"])

        account = expect(admin.post(f"{api}/api/admin/users", headers=admin_headers(), json={
            "username": reviewer_username, "display_name": "冒烟审片账号", "password": reviewer_password,
            "role": "reviewer", "approved": False, "daily_tasks": 0, "daily_credits": 0,
            "concurrent_tasks": 0, "daily_asset_mb": 0, "storage_mb": 0, "allow_paid_models": False, "allowed_models": [],
        }, timeout=30), 201, "create pending reviewer")["user"]
        created_user_id = str(account["id"])
        denied = reviewer.post(f"{api}/api/auth/login", json={"username": reviewer_username, "password": reviewer_password}, timeout=20)
        if denied.status_code != 401: fail("pending account was able to log in")
        checks.append("pending_login_denied")

        expect(admin.patch(f"{api}/api/admin/users/{created_user_id}", headers=admin_headers(), json={"status": "active", "role": "reviewer", "daily_tasks": 0, "daily_credits": 0, "concurrent_tasks": 0, "daily_asset_mb": 0, "storage_mb": 0, "allow_paid_models": False, "allowed_models": []}, timeout=20), 200, "approve reviewer")
        expect(admin.post(f"{api}/api/projects/{project_id}/members", headers=admin_headers(), json={"username": reviewer_username, "role": "reviewer"}, timeout=20), 201, "add reviewer to project")
        reviewer_login = expect(reviewer.post(f"{api}/api/auth/login", json={"username": reviewer_username, "password": reviewer_password}, timeout=20), 200, "reviewer login")
        reviewer_csrf = str(reviewer_login["csrf_token"])
        visible = expect(reviewer.get(f"{api}/api/projects", timeout=20), 200, "reviewer projects")
        if project_id not in [str(item.get("id")) for item in visible.get("projects", [])]: fail("reviewer cannot see assigned project")
        denied_write = reviewer.patch(f"{api}/api/projects/{project_id}", headers={"x-csrf-token": reviewer_csrf}, json={"title": "must-not-save", "canvas": canvas, "expectedVersion": int(project["version"])}, timeout=20)
        if denied_write.status_code != 403: fail("reviewer unexpectedly modified the canvas")
        checks.extend(["admin_approval", "project_membership", "reviewer_write_denied"])

        upload = expect(admin.post(f"{api}/api/assets", headers=admin_headers(), data={"project_id": project_id, "node_id": "smoke-image", "metadata_json": '{"source":"production_smoke"}'}, files={"file": ("smoke.png", PNG_1X1, "image/png")}, timeout=30), 201, "asset upload")["asset"]
        expect(admin.post(f"{api}/api/assets/{upload['id']}/save-to-library", headers=admin_headers(), timeout=20), 200, "save asset library copy")
        library = expect(admin.get(f"{api}/api/assets", params={"project_id": project_id, "library_only": "true"}, timeout=20), 200, "list asset library")
        if str(upload["id"]) not in [str(item.get("id")) for item in library.get("assets", [])]: fail("asset library copy was not persisted")
        checks.extend(["validated_media_upload", "explicit_asset_library_copy"])

        template = expect(admin.post(f"{api}/api/workflow-templates", headers=admin_headers(), json={"name": f"冒烟模板 {stamp}", "definition": {"nodes": canvas["nodes"], "edges": []}}, timeout=20), 201, "workflow template")
        if not template.get("template", {}).get("id"): fail("workflow template was not persisted")
        checks.append("workflow_template")

        if not SKIP_GENERATION:
            items = [
                {"node_id": "smoke-voice-1", "kind": "text_to_speech", "provider": "edge_tts", "model": "", "input": {"inputs": {"prompt": "正式环境端到端验证第一段。"}, "params": {"voice": "zh-CN-XiaoxiaoNeural"}}},
                {"node_id": "smoke-voice-2", "kind": "text_to_speech", "provider": "edge_tts", "model": "", "input": {"inputs": {"prompt": "正式环境端到端验证第二段。"}, "params": {"voice": "zh-CN-XiaoxiaoNeural"}}},
            ]
            run = expect(admin.post(f"{api}/api/workflow-runs", headers=admin_headers(), json={"project_id": project_id, "node_id": "smoke-workflow", "items": items}, timeout=30), 201, "durable workflow")["run"]
            deadline = time.monotonic() + TIMEOUT
            while time.monotonic() < deadline:
                state = expect(admin.get(f"{api}/api/workflow-runs/{run['id']}", timeout=20), 200, "poll workflow")["run"]
                if state["status"] == "completed": break
                if state["status"] in ("failed", "cancelled", "paused"): fail(f"workflow stopped in {state['status']}: {state.get('error_message', '')}")
                time.sleep(2)
            else: fail("workflow did not complete before timeout")
            updated = expect(admin.get(f"{api}/api/projects/{project_id}", timeout=20), 200, "canvas result writeback")["project"]
            updated_nodes = {str(item.get("id")): item for item in updated.get("canvas", {}).get("nodes", [])}
            for node_id in ("smoke-voice-1", "smoke-voice-2"):
                payload = updated_nodes.get(node_id, {}).get("data", {}).get("desktopPayload", {})
                if not payload.get("server_task_id") or not payload.get("output_asset_ids"): fail(f"{node_id} result was not written back to the canvas")
            checks.extend(["durable_sequential_workflow", "worker_generation", "canvas_result_writeback"])

        expect(admin.post(f"{api}/api/admin/users/{created_user_id}/revoke-sessions", headers=admin_headers(), timeout=20), 200, "revoke reviewer sessions")
        if reviewer.get(f"{api}/api/auth/me", timeout=20).status_code != 401: fail("revoked reviewer session remained active")
        checks.append("forced_session_revocation")
    finally:
        if created_user_id and csrf:
            try:
                admin.patch(f"{api}/api/admin/users/{created_user_id}", headers={"x-csrf-token": csrf}, json={"status": "suspended"}, timeout=20)
            except httpx.RequestError:
                pass
        admin.close(); reviewer.close()

    print("Creative Engine production smoke passed")
    print("Checks: " + ", ".join(checks))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (httpx.RequestError, RuntimeError, ValueError) as error:
        print(f"Smoke failed: {error}", file=sys.stderr)
        raise SystemExit(1)
