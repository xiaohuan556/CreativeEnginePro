from pathlib import Path
from types import SimpleNamespace

import pytest

from ai.canvas_agent_planner import PLAN_SCHEMA, PLAN_VERSION
from ai.canvas_agent_runtime import execute_tool, run_agent_plan
from ai.providers.base import TaskHandle, TaskResult, TaskStatus


def plan():
    return {"schema": PLAN_SCHEMA, "version": PLAN_VERSION, "status": "ready",
            "instruction": "生成图片然后生成视频", "summary": "图片到视频", "needs_input": [],
            "steps": [
                {"id": "picture", "tool": "image_generate", "inputs": [], "dependencies": [],
                 "params": {}, "prompt": "生成一张庭院图片"},
                {"id": "movie", "tool": "video_generate", "inputs": ["picture"], "dependencies": ["picture"],
                 "params": {}, "prompt": "让画面动起来"}]}


def test_dependencies_receive_outputs_and_retry_does_not_repeat_completed_step(tmp_path):
    calls = []
    def executor(step, refs, settings, folder, manager, cancelled, progress):
        calls.append((step["id"], refs))
        if step["id"] == "movie" and len(calls) == 2:
            raise RuntimeError("temporary failure")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ("result.png" if step["id"] == "picture" else "result.mp4")
        path.write_bytes(b"result")
        return [{"path": str(path), "kind": "image" if step["id"] == "picture" else "video"}]
    first = run_agent_plan(plan(), [], {}, tmp_path, None, executor=executor)
    assert first["status"] == "failed"
    assert first["steps"]["picture"]["status"] == "completed"
    second = run_agent_plan(plan(), [], {}, tmp_path, None, executor=executor, previous=first)
    assert second["status"] == "completed"
    assert [row[0] for row in calls] == ["picture", "movie", "movie"]
    assert calls[-1][1][0]["path"].endswith("result.png")


def test_cancel_keeps_completed_results_and_never_runs_downstream(tmp_path):
    stopped = [False]
    def executor(step, refs, settings, folder, manager, cancelled, progress):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "picture.png"
        path.write_bytes(b"result")
        return [{"path": str(path), "kind": "image"}]
    def observe(state):
        if state.get("steps", {}).get("picture", {}).get("status") == "completed":
            stopped[0] = True
    state = run_agent_plan(plan(), [], {}, tmp_path, None, executor=executor,
                           cancelled=lambda: stopped[0], on_state=observe)
    assert state["status"] == "cancelled"
    assert state["steps"]["picture"]["status"] == "completed"
    assert "movie" not in state["steps"]


def test_immediate_cancel_of_resume_keeps_already_completed_steps(tmp_path):
    output = tmp_path / "picture.png"
    output.write_bytes(b"valid result")
    previous = {"plan": plan(), "status": "failed", "steps": {
        "picture": {"status": "completed", "outputs": [{"path": str(output), "kind": "image"}]}}}
    stopped = run_agent_plan(plan(), [], {}, tmp_path, None, previous=previous, cancelled=lambda: True)
    assert stopped["status"] == "cancelled"
    assert stopped["steps"]["picture"] == previous["steps"]["picture"]
    calls = []

    def execute(step, *_args):
        calls.append(step["id"])
        movie = tmp_path / "movie.mp4"
        movie.write_bytes(b"valid movie")
        return [{"path": str(movie), "kind": "video"}]

    resumed = run_agent_plan(plan(), [], {}, tmp_path, None, previous=stopped, executor=execute)
    assert resumed["status"] == "completed"
    assert calls == ["movie"]


def test_empty_saved_output_is_regenerated_instead_of_used_by_downstream(tmp_path):
    output = tmp_path / "truncated.png"
    output.touch()
    stale_movie = tmp_path / "old-movie.mp4"
    stale_movie.write_bytes(b"old dependent result")
    previous = {"steps": {
        "picture": {"status": "completed", "outputs": [{"path": str(output), "kind": "image"}]},
        "movie": {"status": "completed", "outputs": [{"path": str(stale_movie), "kind": "video"}]}}}
    calls = []

    def execute(step, *_args):
        calls.append(step["id"])
        generated = tmp_path / (step["id"] + ".result")
        generated.write_bytes(b"valid output")
        return [{"path": str(generated), "kind": "image" if step["id"] == "picture" else "video"}]

    state = run_agent_plan(plan(), [], {}, tmp_path, None, previous=previous, executor=execute)
    assert state["status"] == "completed"
    assert calls == ["picture", "movie"]


def test_worker_cancel_before_start_keeps_plan_and_saved_progress(tmp_path):
    from ai.ui.canvas_agent_worker import CanvasAgentWorker
    previous = {"plan": plan(), "status": "failed", "steps": {"picture": {"status": "completed", "outputs": []}}}
    worker = CanvasAgentWorker("继续任务", [], {}, tmp_path, None, previous=previous)
    states = []
    worker.completed.connect(states.append)
    worker.cancel()
    worker.run()
    assert states[0]["status"] == "cancelled"
    assert states[0]["plan"] == previous["plan"]
    assert states[0]["steps"] == previous["steps"]


def test_multiple_images_are_all_sent_to_video_model(tmp_path):
    refs = []
    for index in range(4):
        path = tmp_path / f"image-{index}.png"
        path.write_bytes(b"image")
        refs.append({"path": str(path), "kind": "image", "role": "reference"})
    result = tmp_path / "result.mp4"
    result.write_bytes(b"movie")
    calls = []
    provider = SimpleNamespace(name="test-video", capability_profile=lambda: {"reference_assets": 9})
    def submit(name, request):
        calls.append(request)
        return TaskHandle("test", name, request.operation, status=TaskStatus.DONE,
                          result=TaskResult(True, str(result)))
    manager = SimpleNamespace(registry=SimpleNamespace(by_capability=lambda operation: [provider]), submit=submit)
    outputs = execute_tool({"tool": "video_generate", "prompt": "四张图片生成视频", "params": {}},
                           refs, {}, tmp_path / "outputs", manager, lambda: False, lambda value: None)
    assert outputs[0]["path"] == str(result)
    assert calls[0].operation == "text_to_video"
    assert len(calls[0].inputs["reference_assets"]) == 4
    assert "image" not in calls[0].inputs
    execute_tool({"tool": "video_generate", "prompt": "首尾帧生成视频", "params": {}},
                 refs[:2], {}, tmp_path / "endpoints", manager, lambda: False, lambda value: None)
    assert calls[-1].operation == "image_to_video"
    assert calls[-1].inputs["image"] == refs[0]["path"]
    assert calls[-1].inputs["last_frame"] == refs[1]["path"]
    with pytest.raises(ValueError, match="首尾帧"):
        execute_tool({"tool": "video_generate", "prompt": "首尾帧生成视频", "params": {}},
                     refs, {}, tmp_path / "too-many", manager, lambda: False, lambda value: None)


def test_nonready_plan_cannot_fake_completed_run(tmp_path):
    value = plan()
    value.update(status="needs_input", steps=[], needs_input=["请补充素材"])
    with pytest.raises(ValueError):
        run_agent_plan(value, [], {}, tmp_path, None)


def test_step_id_cannot_escape_run_folder(tmp_path):
    value = plan()
    value["steps"][0]["id"] = "../outside"
    with pytest.raises(ValueError):
        run_agent_plan(value, [], {}, tmp_path, None)


def test_local_resolution_presets_follow_reference_and_explicit_ratio(tmp_path):
    from PIL import Image
    from ai.canvas_agent_runtime import preset_size
    path = tmp_path / "image.png"
    Image.new("RGB", (64, 48)).save(path)
    refs = [{"kind": "image", "path": str(path)}]
    assert preset_size({"resolution": "480p", "ratio": "adaptive"}, refs) == (640, 480)
    assert preset_size({"resolution": "720p", "ratio": "4:5"}, refs) == (720, 900)


def test_real_local_image_processing_then_slideshow(tmp_path):
    import cv2
    from PIL import Image
    from ai.canvas_agent_planner import plan_agent

    references = []
    for index, color in enumerate(("red", "blue")):
        path = tmp_path / f"original-{index}.png"
        Image.new("RGB", (64, 48), color).save(path)
        references.append({"id": f"image-{index}", "path": str(path), "media_type": "image", "kind": "image"})
    settings = {"fps": 12, "resolution": "160x120", "target_duration": 1.2,
                "transition_frames": 0, "sequential_mode": True}
    value = plan_agent("把图片统一调整成160x120，然后制作图片轮播", references, settings)
    assert [step["tool"] for step in value["steps"]] == ["image_batch", "slideshow"]
    state = run_agent_plan(value, references, settings, tmp_path / "run", None)
    assert state["status"] == "completed", state.get("error")
    first, second = value["steps"]
    assert len(state["steps"][first["id"]]["outputs"]) == 2
    movie = state["steps"][second["id"]]["outputs"][0]["path"]
    capture = cv2.VideoCapture(movie)
    try:
        assert capture.isOpened()
        assert int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)) == 160
        assert int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)) == 120
        assert capture.get(cv2.CAP_PROP_FRAME_COUNT) > 0
    finally:
        capture.release()
