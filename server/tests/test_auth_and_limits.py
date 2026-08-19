import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

data_dir = Path(tempfile.mkdtemp(prefix="creative-engine-server-tests-"))
os.environ["CEP_DATABASE_URL"] = f"sqlite:///{(data_dir / 'test.db').as_posix()}"
os.environ["CEP_PUBLIC_ORIGIN"] = "http://testserver"
os.environ["CEP_LOGIN_MAX_FAILURES"] = "3"
os.environ["CEP_STORAGE_DIR"] = str(data_dir / "media")

from fastapi.testclient import TestClient  # noqa: E402

from creative_server.database import SessionLocal, create_schema  # noqa: E402
from creative_server.main import app  # noqa: E402
from creative_server.models import Asset, GenerationTask, ProductionRun, Project, ServiceHeartbeat, WorkflowRun, UsageLimit, User  # noqa: E402
from creative_server.security import hash_password  # noqa: E402


def seed_admin() -> None:
    create_schema()
    with SessionLocal() as db:
        existing = db.query(User).filter(User.username == "admin").first()
        if existing: return
        admin = User(username="admin", display_name="管理员", password_hash=hash_password("Correct-Horse-42!"), role="admin", status="active")
        db.add(admin); db.flush()
        db.add(UsageLimit(user_id=admin.id, daily_tasks=100, daily_credits=100000, concurrent_tasks=10, allow_paid_models=True, allowed_models_json='["openai","seedream","seedream-v4","seedance","veo","gptimage"]'))
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
            "username": "artist.one", "display_name": "美术一号", "password": "Artist-Secure-42!", "role": "viewer", "approved": True,
            "daily_tasks": 2, "daily_credits": 20, "concurrent_tasks": 1, "allow_paid_models": False,
        })
        assert created.status_code == 201, created.text
        user_id = created.json()["user"]["id"]

        with SessionLocal() as db:
            project = Project(owner_id=user_id, title="测试项目", canvas_json="{}")
            db.add(project); db.commit(); project_id = project.id

        with TestClient(app) as user_client:
            user_csrf = login(user_client, "artist.one", "Artist-Secure-42!")
            forbidden = user_client.post("/api/tasks", headers={"x-csrf-token": user_csrf, "idempotency-key": "viewer-task-0001"}, json={"project_id": project_id, "node_id": "node-1", "kind": "text_to_image", "provider": "seedream", "estimated_credits": 0})
            assert forbidden.status_code == 403

        changed = admin_client.patch(f"/api/admin/users/{user_id}", headers={"x-csrf-token": admin_csrf}, json={"role": "producer", "allowed_models": ["seedream", "seedream-v4", "seedance"]})
        assert changed.status_code == 200

        with TestClient(app) as user_client:
            user_csrf = login(user_client, "artist.one", "Artist-Secure-42!")
            denied = user_client.post("/api/tasks", headers={"x-csrf-token": user_csrf, "idempotency-key": "producer-task-denied"}, json={"project_id": project_id, "node_id": "node-1", "kind": "text_to_image", "provider": "seedream", "model": "seedream-v4", "estimated_credits": 0})
            assert denied.status_code == 403

        enabled = admin_client.patch(f"/api/admin/users/{user_id}", headers={"x-csrf-token": admin_csrf}, json={"allow_paid_models": True})
        assert enabled.status_code == 200

        with TestClient(app) as user_client:
            user_csrf = login(user_client, "artist.one", "Artist-Secure-42!")
            headers = {"x-csrf-token": user_csrf, "idempotency-key": "producer-task-0001"}
            task = user_client.post("/api/tasks", headers=headers, json={"project_id": project_id, "node_id": "node-1", "kind": "text_to_image", "provider": "seedream", "model": "seedream-v4", "estimated_credits": 0})
            assert task.status_code == 202, task.text
            assert task.json()["task"]["id"]
            duplicate = user_client.post("/api/tasks", headers=headers, json={"project_id": project_id, "node_id": "node-1", "kind": "text_to_image", "provider": "seedream", "model": "seedream-v4", "estimated_credits": 0})
            assert duplicate.status_code == 202
            assert duplicate.json()["deduplicated"] is True
            with SessionLocal() as db:
                queued = db.get(GenerationTask, task.json()["task"]["id"])
                assert queued.estimated_credits == 10
                queued.status = "completed"
                db.commit()
            paid = user_client.post("/api/tasks", headers={"x-csrf-token": user_csrf, "idempotency-key": "producer-task-0002"}, json={"project_id": project_id, "node_id": "node-2", "kind": "text_to_video", "provider": "seedance", "estimated_credits": 1})
            assert paid.status_code == 429
        usage = admin_client.get("/api/admin/usage")
        audit = admin_client.get("/api/admin/audit?limit=20")
        assert usage.status_code == 200
        assert usage.json()["statuses"]["completed"] >= 1
        assert audit.status_code == 200
        assert any(event["action"] == "task.queued" for event in audit.json()["events"])


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
        missing_locks = client.post("/api/production-runs", headers={"x-csrf-token": csrf}, json={"project_id": project_id, "node_id": "storyboard-1", "automation_mode": "checkpoints"})
        assert missing_locks.status_code == 422
        profiles = [{"name": "openai", "capabilities": ["chat"]}, {"name": "seedream", "capabilities": ["text_to_image"]}, {"name": "seedance", "capabilities": ["text_to_video", "image_to_video"]}, {"name": "edge_tts", "capabilities": ["text_to_speech"]}]
        production_payload = {"project_id": project_id, "node_id": "storyboard-1", "automation_mode": "checkpoints", "provider_locks": {"planning": "openai", "planning_model": "locked-planning-model", "image": "seedream", "image_model": "locked-image-model", "video": "seedance", "video_model": "locked-video-model"}}
        text_only_profiles = [{**item, "capabilities": ["text_to_video"]} if item["name"] == "seedance" else item for item in profiles]
        with patch("creative_server.main.available_providers", return_value=text_only_profiles):
            incompatible = client.post("/api/production-runs/quote", json=production_payload)
            assert incompatible.status_code == 409
            assert "image_to_video" in incompatible.json()["detail"]
        with patch("creative_server.main.available_providers", return_value=profiles):
            quote = client.post("/api/production-runs/quote", json=production_payload)
            assert quote.status_code == 200, quote.text
            assert quote.json()["quote"]["tasks"] == 7
            assert quote.json()["quote"]["credits"] == 86
            run_response = client.post("/api/production-runs", headers={"x-csrf-token": csrf}, json=production_payload)
        assert run_response.status_code == 201
        run_id = run_response.json()["run"]["id"]
        with patch("creative_server.production.available_providers", return_value=[{"name": "openai", "capabilities": ["chat"]}]):
            started = client.post(f"/api/production-runs/{run_id}/command", headers={"x-csrf-token": csrf}, json={"command": "start"})
        assert started.status_code == 200, started.text
        task_id = started.json()["run"]["active_task_id"]
        assert task_id
        with SessionLocal() as db:
            assert db.get(GenerationTask, task_id).model == "locked-planning-model"
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
        project = db.get(Project, finished.project_id)
        canvas = __import__("json").loads(project.canvas_json)
        source = next(node for node in canvas["nodes"] if node["id"] == "script-1")
        assert source["data"]["status"] == "生成完成"
        assert source["data"]["desktopPayload"]["server_task_id"] == task_id
        assert project.version == 2


def test_text_results_preserve_script_original_and_update_copywriting() -> None:
    seed_admin()
    script_node = {"id": "script-result-1", "type": "studio", "position": {"x": 0, "y": 0}, "data": {"title": "剧本", "description": "原始剧本", "kind": "script", "specKey": "script", "desktopType": "text_node", "status": "草稿", "meta": "", "accent": "#fff", "desktopPayload": {}}}
    copy_node = {"id": "copy-result-1", "type": "studio", "position": {"x": 0, "y": 0}, "data": {"title": "口播", "description": "中文原文", "kind": "copywriting", "specKey": "copywriting", "desktopType": "text_node", "status": "草稿", "meta": "", "accent": "#fff", "desktopPayload": {"copywriting_workbench": True, "copy_language": "英语"}}}
    canvas = {"protocol": "creative-engine-canvas", "version": 1, "nodes": [script_node, copy_node], "edges": []}
    with SessionLocal() as db:
        admin = db.query(User).filter(User.username == "admin").first()
        project = Project(owner_id=admin.id, title="文本写回", canvas_json=__import__("json").dumps(canvas, ensure_ascii=False))
        db.add(project); db.flush()
        script_task = GenerationTask(project_id=project.id, node_id=script_node["id"], owner_id=admin.id, kind="chat", provider="openai", model="test", idempotency_key="script-result-writeback", input_json='{"action":"改写优化"}', output_json='{"data":"AI 修订稿"}', status="completed", progress=100)
        copy_task = GenerationTask(project_id=project.id, node_id=copy_node["id"], owner_id=admin.id, kind="chat", provider="openai", model="test", idempotency_key="copy-result-writeback", input_json='{"action":"翻译"}', output_json='{"data":"English voiceover"}', status="completed", progress=100)
        db.add_all([script_task, copy_task]); db.commit(); project_id, script_task_id, copy_task_id = project.id, script_task.id, copy_task.id
    from creative_server.canvas_sync import sync_task_to_canvas
    sync_task_to_canvas(script_task_id); sync_task_to_canvas(copy_task_id)
    with SessionLocal() as db:
        document = __import__("json").loads(db.get(Project, project_id).canvas_json)
        script = next(node for node in document["nodes"] if node["id"] == script_node["id"])["data"]
        copy = next(node for node in document["nodes"] if node["id"] == copy_node["id"])["data"]
        assert script["description"] == "原始剧本"
        assert script["desktopPayload"]["script_candidate"] == "AI 修订稿"
        assert script["status"].startswith("AI 候选稿待确认")
        assert copy["description"] == "English voiceover"
        assert copy["desktopPayload"]["original_text"] == "中文原文"


def test_media_operations_reject_wrong_asset_kinds() -> None:
    seed_admin()
    with SessionLocal() as db:
        admin = db.query(User).filter(User.username == "admin").first()
        project = Project(owner_id=admin.id, title="媒体类型校验", canvas_json='{"protocol":"creative-engine-canvas","version":1,"nodes":[],"edges":[]}')
        db.add(project); db.flush()
        image = Asset(project_id=project.id, owner_id=admin.id, node_id="image-source", name="frame.png", kind="image", object_key="media-validation/frame.png", content_type="image/png", size=10, sha256="b" * 64)
        video = Asset(project_id=project.id, owner_id=admin.id, node_id="video-source", name="clip.mp4", kind="video", object_key="media-validation/clip.mp4", content_type="video/mp4", size=10, sha256="c" * 64)
        db.add_all([image, video]); db.commit(); project_id, image_id, video_id = project.id, image.id, video.id
    with TestClient(app) as client:
        csrf = login(client, "admin", "Correct-Horse-42!")
        wrong_breakdown = client.post("/api/tasks", headers={"x-csrf-token": csrf, "idempotency-key": "wrong-breakdown-media"}, json={"project_id": project_id, "node_id": "analysis-1", "kind": "video_breakdown", "provider": "local", "input": {"inputs": {"references": [{"asset_id": image_id}]}}})
        assert wrong_breakdown.status_code == 422
        assert "只接受视频节点" in wrong_breakdown.json()["detail"]
        provider_profile = [{"name": "gptimage", "capabilities": ["image_edit"], "profile": {"reference_assets": 10}}]
        with patch("creative_server.task_validation.available_providers", return_value=provider_profile):
            wrong_image_edit = client.post("/api/tasks", headers={"x-csrf-token": csrf, "idempotency-key": "wrong-image-edit-media"}, json={"project_id": project_id, "node_id": "image-edit-1", "kind": "image_edit", "provider": "gptimage", "input": {"inputs": {"references": [{"asset_id": video_id}]}}})
        assert wrong_image_edit.status_code == 422
        assert "只接受图片节点" in wrong_image_edit.json()["detail"]


def test_asset_library_is_explicit_and_retry_is_a_new_queued_task() -> None:
    seed_admin()
    with SessionLocal() as db:
        admin = db.query(User).filter(User.username == "admin").first()
        project = Project(owner_id=admin.id, title="资产库测试", canvas_json='{"protocol":"creative-engine-canvas","version":1,"nodes":[],"edges":[]}')
        db.add(project); db.flush()
        asset = Asset(project_id=project.id, owner_id=admin.id, node_id="image-1", name="frame.png", kind="image", object_key="test/frame.png", content_type="image/png", size=128, sha256="a" * 64)
        failed = GenerationTask(project_id=project.id, node_id="image-1", owner_id=admin.id, kind="text_to_image", provider="seedream", model="seedream-v4", estimated_credits=10, idempotency_key="failed-task-for-retry", input_json='{"inputs":{"prompt":"test"}}', status="failed")
        db.add_all([asset, failed]); db.commit(); project_id, asset_id, failed_id = project.id, asset.id, failed.id
    with TestClient(app) as client:
        csrf = login(client, "admin", "Correct-Horse-42!")
        before = client.get(f"/api/assets?project_id={project_id}&library_only=true")
        assert before.json()["assets"] == []
        saved = client.post(f"/api/assets/{asset_id}/save-to-library", headers={"x-csrf-token": csrf})
        assert saved.status_code == 200
        assert saved.json()["asset"]["in_library"] is True
        after = client.get(f"/api/assets?project_id={project_id}&library_only=true")
        assert [item["id"] for item in after.json()["assets"]] == [asset_id]
        retried = client.post(f"/api/tasks/{failed_id}/retry", headers={"x-csrf-token": csrf})
        assert retried.status_code == 202, retried.text
        assert retried.json()["task"]["id"] != failed_id
        with SessionLocal() as db:
            queued = db.get(GenerationTask, retried.json()["task"]["id"])
            assert queued.status == "queued"
            assert queued.input_json == '{"inputs":{"prompt":"test"}}'


def test_provider_reference_limit_is_enforced_without_silent_truncation() -> None:
    seed_admin()
    with SessionLocal() as db:
        admin = db.query(User).filter(User.username == "admin").first()
        project = Project(owner_id=admin.id, title="参考图限制", canvas_json='{"protocol":"creative-engine-canvas","version":1,"nodes":[],"edges":[]}')
        db.add(project); db.commit(); project_id = project.id
    references = [{"asset_id": str(index)} for index in range(10)]
    profile = [{"name": "seedance", "capabilities": ["text_to_video", "image_to_video"], "profile": {"reference_assets": 9}}]
    with patch("creative_server.main.available_providers", return_value=profile):
        with TestClient(app) as client:
            csrf = login(client, "admin", "Correct-Horse-42!")
            response = client.post("/api/tasks", headers={"x-csrf-token": csrf, "idempotency-key": "too-many-references"}, json={"project_id": project_id, "node_id": "director-1", "kind": "text_to_video", "provider": "seedance", "input": {"inputs": {"prompt": "test", "references": references}}})
            assert response.status_code == 422
            assert "不会静默丢图" in response.json()["detail"]


def test_durable_workflow_reserves_all_children_and_runs_sequentially() -> None:
    seed_admin()
    with TestClient(app) as client:
        csrf = login(client, "admin", "Correct-Horse-42!")
        project = client.post("/api/projects", headers={"x-csrf-token": csrf}, json={"title": "持久工作流", "canvas": {"protocol": "creative-engine-canvas", "version": 1, "nodes": [], "edges": []}})
        project_id = project.json()["project"]["id"]
        items = [{"node_id": f"audio-{index}", "kind": "text_to_speech", "provider": "edge_tts", "input": {"inputs": {"prompt": f"第{index}段"}}} for index in range(1, 4)]
        created = client.post("/api/workflow-runs", headers={"x-csrf-token": csrf}, json={"project_id": project_id, "node_id": "workflow-1", "items": items})
        assert created.status_code == 201, created.text
        run_id = created.json()["run"]["id"]
        with SessionLocal() as db:
            run = db.get(WorkflowRun, run_id)
            tasks = db.query(GenerationTask).filter(GenerationTask.idempotency_key.like(f"workflow:{run_id}:%")).order_by(GenerationTask.created_at).all()
            assert len(tasks) == 3
            assert [task.status for task in tasks] == ["queued", "workflow_waiting", "workflow_waiting"]
            first_id, second_id, third_id = [task.id for task in tasks]
            assert run.active_task_id == first_id

        paused = client.post(f"/api/workflow-runs/{run_id}/command", headers={"x-csrf-token": csrf}, json={"command": "pause"})
        assert paused.json()["run"]["status"] == "paused"
        with SessionLocal() as db:
            assert db.get(GenerationTask, first_id).status == "paused"
        resumed = client.post(f"/api/workflow-runs/{run_id}/command", headers={"x-csrf-token": csrf}, json={"command": "resume"})
        assert resumed.json()["run"]["status"] == "running"
        with SessionLocal.begin() as db:
            db.get(GenerationTask, first_id).status = "completed"
        from creative_server.workflows import on_workflow_task_finished
        on_workflow_task_finished(first_id, True)
        with SessionLocal() as db:
            assert db.get(WorkflowRun, run_id).active_task_id == second_id
            assert db.get(GenerationTask, second_id).status == "queued"
            db.get(GenerationTask, second_id).status = "failed"; db.commit()
        on_workflow_task_finished(second_id, False, "模拟失败")
        failed = client.get(f"/api/workflow-runs/{run_id}").json()["run"]
        assert failed["status"] == "failed" and failed["current_index"] == 1
        retried = client.post(f"/api/workflow-runs/{run_id}/command", headers={"x-csrf-token": csrf}, json={"command": "retry"})
        assert retried.status_code == 200, retried.text
        retry_id = retried.json()["run"]["active_task_id"]
        assert retry_id not in (first_id, second_id, third_id)
        with SessionLocal.begin() as db:
            db.get(GenerationTask, retry_id).status = "completed"
        on_workflow_task_finished(retry_id, True)
        with SessionLocal() as db:
            run = db.get(WorkflowRun, run_id)
            assert run.active_task_id == third_id
            assert db.get(GenerationTask, third_id).status == "queued"
            db.get(GenerationTask, third_id).status = "completed"; db.commit()
        on_workflow_task_finished(third_id, True)
        assert client.get(f"/api/workflow-runs/{run_id}").json()["run"]["status"] == "completed"


def test_project_membership_reviewer_and_template_permissions() -> None:
    seed_admin()
    with TestClient(app) as admin_client:
        csrf = login(admin_client, "admin", "Correct-Horse-42!")
        review_node = {"id": "take-review-1", "type": "studio", "position": {"x": 10, "y": 10}, "data": {"title": "待审候选", "description": "候选", "kind": "result", "specKey": "result", "desktopType": "shot_take", "status": "待审", "meta": "test", "accent": "#fff", "desktopPayload": {}}}
        project = admin_client.post("/api/projects", headers={"x-csrf-token": csrf}, json={"title": "协作权限", "canvas": {"protocol": "creative-engine-canvas", "version": 1, "nodes": [review_node], "edges": []}})
        project_id = project.json()["project"]["id"]
        for username, role in (("review.one", "reviewer"), ("view.one", "viewer")):
            created = admin_client.post("/api/admin/users", headers={"x-csrf-token": csrf}, json={"username": username, "display_name": username, "password": "Member-Secure-42!", "role": role, "approved": True})
            assert created.status_code == 201, created.text
            member = admin_client.post(f"/api/projects/{project_id}/members", headers={"x-csrf-token": csrf}, json={"username": username, "role": "reviewer" if role == "reviewer" else "viewer"})
            assert member.status_code == 201, member.text
        template = admin_client.post("/api/workflow-templates", headers={"x-csrf-token": csrf}, json={"name": "两段式模板", "definition": {"nodes": [{"id": "a"}], "edges": []}})
        assert template.status_code == 201, template.text
        assert any(item["name"] == "两段式模板" for item in admin_client.get("/api/workflow-templates").json()["templates"])
        with SessionLocal() as db:
            admin = db.query(User).filter(User.username == "admin").first()
            run = ProductionRun(project_id=project_id, node_id="story-1", owner_id=admin.id, status="waiting_review", stage=2, completed_stage=1)
            db.add(run); db.commit(); run_id = run.id

    with TestClient(app) as reviewer:
        reviewer_csrf = login(reviewer, "review.one", "Member-Secure-42!")
        members = reviewer.get(f"/api/projects/{project_id}/members")
        assert members.status_code == 200 and members.json()["can_manage"] is False
        reviewed = reviewer.post(f"/api/projects/{project_id}/reviews", headers={"x-csrf-token": reviewer_csrf}, json={"node_id": "take-review-1", "decision": "adopt", "expected_version": 1})
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["project"]["canvas"]["nodes"][0]["data"]["status"] == "已采用"
        stale = reviewer.post(f"/api/projects/{project_id}/reviews", headers={"x-csrf-token": reviewer_csrf}, json={"node_id": "take-review-1", "decision": "reject", "expected_version": 1})
        assert stale.status_code == 409
        approved = reviewer.post(f"/api/production-runs/{run_id}/command", headers={"x-csrf-token": reviewer_csrf}, json={"command": "accept_risk"})
        assert approved.status_code == 200, approved.text
        forbidden = reviewer.patch(f"/api/projects/{project_id}", headers={"x-csrf-token": reviewer_csrf}, json={"title": "越权", "canvas": {"protocol": "creative-engine-canvas", "version": 1, "nodes": [], "edges": []}, "expectedVersion": 1})
        assert forbidden.status_code == 403

    with TestClient(app) as viewer:
        viewer_csrf = login(viewer, "view.one", "Member-Secure-42!")
        forbidden = viewer.post(f"/api/projects/{project_id}/members", headers={"x-csrf-token": viewer_csrf}, json={"username": "review.one", "role": "editor"})
        assert forbidden.status_code == 403


def test_readiness_heartbeat_and_revoked_worker_policy() -> None:
    seed_admin()
    with SessionLocal() as db:
        db.add(ServiceHeartbeat(id="worker:test", service="worker", instance="test:1", detail_json='{"pid":1}'))
        admin = db.query(User).filter(User.username == "admin").first()
        db.query(GenerationTask).filter(GenerationTask.status == "queued").update({"status": "completed"})
        project = Project(owner_id=admin.id, title="撤权队列测试", canvas_json='{"protocol":"creative-engine-canvas","version":1,"nodes":[],"edges":[]}')
        db.add(project); db.flush()
        revoked = User(username="revoked.worker", display_name="撤权账号", password_hash=hash_password("Revoked-Secure-42!"), role="producer", status="suspended")
        db.add(revoked); db.flush()
        db.add(UsageLimit(user_id=revoked.id, daily_tasks=5, daily_credits=5, concurrent_tasks=1, allow_paid_models=False, allowed_models_json="[]"))
        task = GenerationTask(project_id=project.id, node_id="voice-revoked", owner_id=revoked.id, kind="text_to_speech", provider="edge_tts", model="", idempotency_key="revoked-worker-policy", status="queued")
        db.add(task); db.commit(); task_id = task.id
    with TestClient(app) as client:
        ready = client.get("/ready")
        assert ready.status_code == 200 and ready.json()["storage"] == "ok"
        csrf = login(client, "admin", "Correct-Horse-42!")
        complete_profiles = [
            {"name": "llm", "capabilities": ["chat"]},
            {"name": "image", "capabilities": ["text_to_image", "image_edit"]},
            {"name": "video", "capabilities": ["text_to_video", "image_to_video"]},
            {"name": "voice", "capabilities": ["text_to_speech"]},
        ]
        with patch("creative_server.main.available_providers", return_value=complete_profiles):
            status_response = client.get("/api/admin/readiness")
        assert status_response.status_code == 200
        assert status_response.json()["active_workers"] >= 1
        assert status_response.json()["ready"] is True
        assert status_response.json()["missing_capabilities"] == []
        with patch("creative_server.main.available_providers", return_value=[{"name": "edge_tts", "capabilities": ["text_to_speech"]}]):
            incomplete = client.get("/api/admin/readiness").json()
        assert incomplete["control_ready"] is True
        assert incomplete["generation_ready"] is False
        assert "image_to_video" in incomplete["missing_capabilities"]
    from creative_server.worker import claim_task
    assert claim_task() is None
    with SessionLocal() as db:
        stopped = db.get(GenerationTask, task_id)
        assert stopped.status == "paused"
        assert stopped.error_code == "policy_revoked"
