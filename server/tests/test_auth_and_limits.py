import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

data_dir = Path(tempfile.mkdtemp(prefix="creative-engine-server-tests-"))
os.environ["CEP_DATABASE_URL"] = f"sqlite:///{(data_dir / 'test.db').as_posix()}"
os.environ["CEP_PUBLIC_ORIGIN"] = "http://testserver"
os.environ["CEP_LOGIN_MAX_FAILURES"] = "3"

from fastapi.testclient import TestClient  # noqa: E402

from creative_server.database import SessionLocal, create_schema  # noqa: E402
from creative_server.main import app  # noqa: E402
from creative_server.models import GenerationTask, Project, UsageLimit, User  # noqa: E402
from creative_server.security import hash_password  # noqa: E402


def seed_admin() -> None:
    create_schema()
    with SessionLocal() as db:
        existing = db.query(User).filter(User.username == "admin").first()
        if existing: return
        admin = User(username="admin", display_name="管理员", password_hash=hash_password("Correct-Horse-42!"), role="admin", status="active")
        db.add(admin); db.flush()
        db.add(UsageLimit(user_id=admin.id, daily_tasks=100, daily_credits=100000, concurrent_tasks=10, allow_paid_models=True, allowed_models_json="[]"))
        db.commit()


def login(client: TestClient, username: str, password: str) -> str:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def test_admin_controls_accounts_and_task_limits() -> None:
    seed_admin()
    with TestClient(app) as admin_client:
        admin_csrf = login(admin_client, "admin", "Correct-Horse-42!")
        created = admin_client.post("/api/admin/users", headers={"x-csrf-token": admin_csrf}, json={
            "username": "artist.one", "display_name": "美术一号", "password": "Artist-Secure-42!", "role": "viewer",
            "daily_tasks": 2, "daily_credits": 20, "concurrent_tasks": 1, "allow_paid_models": False,
        })
        assert created.status_code == 201, created.text
        user_id = created.json()["user"]["id"]

        with SessionLocal() as db:
            project = Project(owner_id=user_id, title="测试项目", canvas_json="{}")
            db.add(project); db.commit(); project_id = project.id

        with TestClient(app) as user_client:
            user_csrf = login(user_client, "artist.one", "Artist-Secure-42!")
            forbidden = user_client.post("/api/tasks", headers={"x-csrf-token": user_csrf, "idempotency-key": "viewer-task-0001"}, json={"project_id": project_id, "node_id": "node-1", "kind": "image", "estimated_credits": 0})
            assert forbidden.status_code == 403

        changed = admin_client.patch(f"/api/admin/users/{user_id}", headers={"x-csrf-token": admin_csrf}, json={"role": "producer", "allowed_models": ["seedream-v4"]})
        assert changed.status_code == 200

        with TestClient(app) as user_client:
            user_csrf = login(user_client, "artist.one", "Artist-Secure-42!")
            headers = {"x-csrf-token": user_csrf, "idempotency-key": "producer-task-0001"}
            task = user_client.post("/api/tasks", headers=headers, json={"project_id": project_id, "node_id": "node-1", "kind": "image", "model": "seedream-v4", "estimated_credits": 0})
            assert task.status_code == 202, task.text
            duplicate = user_client.post("/api/tasks", headers=headers, json={"project_id": project_id, "node_id": "node-1", "kind": "image", "model": "seedream-v4", "estimated_credits": 0})
            assert duplicate.status_code == 202
            assert duplicate.json()["deduplicated"] is True
            paid = user_client.post("/api/tasks", headers={"x-csrf-token": user_csrf, "idempotency-key": "producer-task-0002"}, json={"project_id": project_id, "node_id": "node-2", "kind": "video", "model": "seedream-v4", "estimated_credits": 1})
            assert paid.status_code == 403


def test_login_is_rate_limited_without_revealing_account_state() -> None:
    seed_admin()
    with TestClient(app) as client:
        for _ in range(3):
            response = client.post("/api/auth/login", json={"username": "nobody", "password": "wrong"})
            assert response.status_code == 401
            assert "账号或密码错误" in response.json()["detail"]
        blocked = client.post("/api/auth/login", json={"username": "nobody", "password": "wrong"})
        assert blocked.status_code == 429


def test_production_pause_resume_and_rewind_do_not_duplicate_active_task() -> None:
    seed_admin()
    with TestClient(app) as client:
        csrf = login(client, "admin", "Correct-Horse-42!")
        canvas = {"protocol": "creative-engine-canvas", "version": 1, "nodes": [], "edges": []}
        project_response = client.post("/api/projects", headers={"x-csrf-token": csrf}, json={"title": "流程测试", "canvas": canvas})
        assert project_response.status_code == 201, project_response.text
        project_id = project_response.json()["project"]["id"]
        run_response = client.post("/api/production-runs", headers={"x-csrf-token": csrf}, json={"project_id": project_id, "node_id": "storyboard-1", "automation_mode": "checkpoints"})
        assert run_response.status_code == 201
        run_id = run_response.json()["run"]["id"]
        with patch("creative_server.production.available_providers", return_value=[{"name": "openai", "capabilities": ["chat"]}]):
            started = client.post(f"/api/production-runs/{run_id}/command", headers={"x-csrf-token": csrf}, json={"command": "start"})
        assert started.status_code == 200, started.text
        task_id = started.json()["run"]["active_task_id"]
        assert task_id
        paused = client.post(f"/api/production-runs/{run_id}/command", headers={"x-csrf-token": csrf}, json={"command": "pause"})
        assert paused.json()["run"]["status"] == "paused"
        resumed = client.post(f"/api/production-runs/{run_id}/command", headers={"x-csrf-token": csrf}, json={"command": "resume"})
        assert resumed.json()["run"]["active_task_id"] == task_id
        repeated = client.post(f"/api/production-runs/{run_id}/command", headers={"x-csrf-token": csrf}, json={"command": "continue"})
        assert repeated.json()["run"]["active_task_id"] == task_id
        rewound = client.post(f"/api/production-runs/{run_id}/command", headers={"x-csrf-token": csrf}, json={"command": "rewind", "target_stage": 1})
        assert rewound.json()["run"]["active_task_id"] is None
        assert rewound.json()["run"]["status"] == "ready"


def test_worker_completes_a_persisted_task_and_writes_result() -> None:
    seed_admin()
    with SessionLocal() as db:
        admin = db.query(User).filter(User.username == "admin").first()
        project = Project(owner_id=admin.id, title="工作进程测试", canvas_json='{"protocol":"creative-engine-canvas","version":1,"nodes":[],"edges":[]}')
        db.add(project); db.flush()
        task = GenerationTask(project_id=project.id, node_id="script-1", owner_id=admin.id, kind="chat", provider="fake", model="fake", idempotency_key="fake-worker-task-0001", input_json='{"inputs":{"prompt":"test"}}', status="running")
        db.add(task); db.commit(); task_id = task.id
    result = SimpleNamespace(data={"text": "完成"}, cost_credits=0)
    handle = SimpleNamespace(is_finished=True, is_success=True, result=result, progress=1.0)
    manager = SimpleNamespace(submit=lambda provider, request: handle)
    request_type = lambda **values: values
    from creative_server import worker
    with patch.object(worker, "_desktop_api", return_value=(manager, request_type)):
        worker.execute_task(task_id)
    with SessionLocal() as db:
        finished = db.get(GenerationTask, task_id)
        assert finished.status == "completed"
        assert "完成" in finished.output_json
