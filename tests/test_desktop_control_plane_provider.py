from pathlib import Path

from ai.providers.base import TaskRequest, TaskStatus
from ai.providers.control_plane import ControlPlaneProvider
from core.release_gate import configure_desktop_control_client


class FakeControlClient:
    def __init__(self, result_path: Path):
        self.result_path = result_path
        self.uploads = []
        self.submissions = []

    def ensure_desktop_project(self):
        return "project-desktop"

    def upload_asset(self, path, node_id=""):
        self.uploads.append((Path(path), node_id))
        return {"id": f"asset-input-{len(self.uploads)}"}

    def request_json(self, method, path, payload=None, **kwargs):
        if method == "POST":
            self.submissions.append((path, payload, kwargs))
            return {"task": {"id": "task-server-1", "status": "queued"}}
        return {"task": {
            "id": "task-server-1", "status": "completed", "progress": 100,
            "output": {"asset_ids": ["asset-output-1"]},
        }}

    def download_asset(self, asset_id):
        assert asset_id == "asset-output-1"
        return self.result_path


def test_remote_provider_uploads_local_reference_and_returns_download(tmp_path):
    reference = tmp_path / "first.png"
    reference.write_bytes(b"png")
    output = tmp_path / "result.png"
    output.write_bytes(b"result")
    client = FakeControlClient(output)
    provider = ControlPlaneProvider(client, {
        "name": "seedance",
        "capabilities": ["text_to_video", "image_to_video"],
        "profile": {"model": "doubao-seedance-2-5"},
    })

    result = provider.execute(TaskRequest(
        operation="image_to_video",
        inputs={"prompt": "向前推进", "image": str(reference)},
        params={"model": "doubao-seedance-2-5", "duration": 5},
        metadata={"canvas_node_id": "video-1"}, use_cache=False,
    ))

    assert result.status is TaskStatus.DONE
    assert result.result.data == output
    assert client.uploads == [(reference, "video-1")]
    _, payload, options = client.submissions[0]
    assert payload["provider"] == "seedance"
    assert payload["model"] == "doubao-seedance-2-5"
    assert "image" not in payload["input"]["inputs"]
    assert payload["input"]["inputs"]["references"] == [{
        "asset_id": "asset-input-1",
        "role": "first_frame",
        "title": "first.png",
    }]
    assert options["write"] is True
    assert options["extra_headers"]["idempotency-key"].startswith("desktop:")


def test_formal_registry_uses_only_server_approved_providers(tmp_path):
    class ProviderClient(FakeControlClient):
        def request_json(self, method, path, payload=None, **kwargs):
            assert path == "/api/providers"
            return {"providers": [{
                "name": "seedance", "capabilities": ["text_to_video"],
                "profile": {"model": "doubao-seedance-2-5"},
            }]}

    client = ProviderClient(tmp_path / "unused")
    configure_desktop_control_client(client)
    try:
        from ai.service import _build_registry
        registry = _build_registry()
        assert registry.get("seedance") is not None
        assert registry.get("seedream") is None
        assert registry.get("gptimage") is None
        assert registry.get("seedance").capability_profile()["model"] == "doubao-seedance-2-5"
    finally:
        configure_desktop_control_client(None)
