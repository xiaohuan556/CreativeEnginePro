from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from PIL import Image

from ai.canvas_agent_planner import plan_agent, validate_plan
from ai.canvas_agent_runtime import run_agent_plan


def test_pinterest_retheme_keeps_original_first_and_feeds_downloaded_elements(tmp_path, monkeypatch):
    original = tmp_path / "original.png"
    element = tmp_path / "element.png"
    output = tmp_path / "result.png"
    for path in (original, element, output):
        Image.new("RGB", (60, 100)).save(path)

    references = [{"id": "original", "kind": "image", "path": str(original)}]
    instruction = "换成蜘蛛侠元素"
    settings = {"agent_tool": "pinterest_import", "pinterest_count": 1,
                "candidate_count": 3, "provider_name": "gptimage"}
    plan = plan_agent(instruction, references, settings)
    assert plan["status"] == "ready"
    collect, generate = plan["steps"]
    assert collect["tool"] == "pinterest_import"
    assert collect["params"] == {"query": "蜘蛛侠", "reference_count": 1}
    assert generate["inputs"] == ["original", collect["id"]]
    assert generate["dependencies"] == [collect["id"]]
    assert validate_plan(plan, references, instruction, settings)

    modified = deepcopy(plan)
    modified["steps"][0]["params"]["query"] = "其他元素"
    with pytest.raises(ValueError, match="用户明确"):
        validate_plan(modified, references, instruction, settings)

    downloads = []

    def import_page(_self, url, desired, folder, **options):
        downloads.append(url)
        assert desired == 1
        assert not options["stop_event"].is_set()
        return {"items": [{"path": str(element), "page_url": url}]}

    monkeypatch.setattr("core.pinterest_importer.PinterestImporter.import_page", import_page)
    requests = []

    def submit(provider, request):
        assert provider == "gptimage"
        requests.append(request)
        if len(requests) == 2:
            return SimpleNamespace(is_finished=True, is_success=False, result=SimpleNamespace(
                error="moderation_blocked request ID 01234567-1234-1234-1234-0123456789ab"))
        generated = tmp_path / f"generated-{len(requests)}.png"
        generated.write_bytes(output.read_bytes())
        return SimpleNamespace(is_finished=True, is_success=True, result=SimpleNamespace(data=[str(generated)]))

    provider = SimpleNamespace(name="gptimage")
    manager = SimpleNamespace(registry=SimpleNamespace(by_capability=lambda _: [provider]), submit=submit)
    state = run_agent_plan(plan, references, settings, tmp_path, manager)
    assert state["status"] == "completed", state.get("error")
    assert parse_qs(urlsplit(downloads[0]).query) == {"q": ["蜘蛛侠"]}
    assert len(requests) == 3 and all(request.params["n"] == 1 for request in requests)
    generation = state["steps"][generate["id"]]
    assert [Path(row["path"]).name for row in generation["outputs"]] == [
        "generated-1.png", "generated-3.png"]
    assert generation["errors"][0].startswith("第 2 张")
    assert "01234567-1234-1234-1234-0123456789ab" in generation["errors"][0]
    request = requests[0]
    assert request.operation == "image_edit"
    assert request.inputs["image"] == str(original)
    assert [(ref["path"], ref["role"]) for ref in request.inputs["reference_assets"]] == [
        (str(original), "composition"), (str(element), "element")]
    assert "60:100" in request.inputs["prompt"]


def test_pinterest_needs_original_and_rejects_non_pinterest_links():
    assert plan_agent("从 Pinterest 找蜘蛛侠元素", [])["status"] == "needs_input"
    reference = [{"id": "original", "kind": "image", "path": "original.png"}]
    settings = {"agent_tool": "pinterest_import"}
    direct = plan_agent("去Pinterest上找蜘蛛侠图片，然后按原图换元素", reference)
    assert direct["steps"][0]["params"] == {"query": "蜘蛛侠"}
    invalid = plan_agent("换成蜘蛛侠元素 https://pinterest.com.example.org/pin/123", reference, settings)
    assert invalid["status"] == "needs_input"
    assert plan_agent("下载视频", reference)["status"] == "needs_input"


def test_link_download_only_respects_count_and_needs_no_model(tmp_path, monkeypatch):
    url = "https://www.pinterest.com/search/pins/?q=glass"
    settings = {"agent_tool": "pinterest_import", "pinterest_download_only": True,
                "pinterest_url": url, "pinterest_count": 5}
    plan = plan_agent("", [], settings)
    assert len(plan["steps"]) == 1
    assert plan["steps"][0]["params"] == {"page_url": url, "reference_count": 5}

    def import_page(_self, page, count, folder, **options):
        assert page == url and count == 5
        assert options["candidate_factor"] == 3
        folder.mkdir()
        items = []
        for i in range(5):
            path = folder / f"{i}.png"
            Image.new("RGB", (5, 5)).save(path)
            options["item_ready"](str(path), "")
            items.append({"path": str(path)})
        return {"items": items}

    monkeypatch.setattr("core.pinterest_importer.PinterestImporter.import_page", import_page)
    state = run_agent_plan(plan, [], settings, tmp_path, None)
    assert state["status"] == "completed", state.get("error")
    assert len(state["steps"][plan["steps"][0]["id"]]["outputs"]) == 5


def test_style_converter_uses_preset_and_requested_model(tmp_path, monkeypatch):
    original = tmp_path / "original.png"
    result = tmp_path / "result.png"
    for path in (original, result):
        Image.new("RGB", (90, 120)).save(path)
    refs = [{"id": "original", "kind": "image", "path": str(original)}]
    settings = {"agent_tool": "image_generate", "image_style_transfer": True,
                "style_preset": "blown-glass", "candidate_count": 4,
                "provider_name": "seedream"}
    plan = plan_agent("", refs, settings)
    assert plan["status"] == "ready"
    assert "glass" in plan["steps"][0]["prompt"]
    providers = [SimpleNamespace(name="gptimage"), SimpleNamespace(name="seedream")]

    calls = []

    def submit(provider, request):
        calls.append(request)
        assert provider == "seedream"
        assert request.params["n"] == 1
        assert request.inputs["image"] == str(original)
        return SimpleNamespace(is_finished=True, is_success=True, result=SimpleNamespace(data=[str(result)]))

    manager = SimpleNamespace(registry=SimpleNamespace(by_capability=lambda _: providers), submit=submit)
    state = run_agent_plan(plan, refs, settings, tmp_path, manager)
    assert state["status"] == "completed", state.get("error")
    assert len(calls) == 4


def test_pinterest_reference_cards_survive_generation_rejection(tmp_path):
    from ai.ui.production_canvas import ProductionCanvasTab

    image = tmp_path / "reference.png"
    Image.new("RGB", (5, 5)).save(image)
    agent_id = "agent"
    project = {agent_id: [0, 0]}
    state = {
        "status": "failed",
        "plan": {"steps": [{"id": "step_1", "tool": "pinterest_import", "params": {"reference_count": 4}},
                           {"id": "step_2", "tool": "image_generate", "params": {}}]},
        "steps": {"step_1": {"status": "completed", "outputs": [{"kind": "image", "path": str(image)}]},
                  "step_2": {"status": "failed"}},
    }
    ProductionCanvasTab._materialize_agent_run(None, project, agent_id, "test", state)
    assert [node["title"] for node in project["__custom_nodes__"]] == ["Pinterest 参考 · 01"]
    assert "1/4 张" in ProductionCanvasTab._agent_step_status(state["plan"]["steps"][0], state["steps"]["step_1"])



def test_link_and_instruction_cannot_both_drive_pinterest():
    link = "https://www.pinterest.com/pin/123/"
    settings = {"agent_tool": "pinterest_import", "pinterest_url": link}
    refs = [{"id": "original", "kind": "image", "path": "original.png"}]
    conflict = plan_agent("换成复古元素", refs, settings)
    assert conflict["status"] == "needs_input"
    assert not conflict["steps"]
    assert "指令和链接只能使用一项" in conflict["summary"]
    plan = plan_agent("", refs, settings)
    assert plan["status"] == "ready"
    assert plan["steps"][0]["params"]["page_url"] == link
    assert "目标主题从链接中取得的参考图片判断" in plan["steps"][1]["prompt"]
