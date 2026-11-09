"""Real upscaling, isolated inference and cancellation checks."""
import copy
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from core import canvas_media_tools as media


@pytest.fixture
def picture(tmp_path):
    folder = tmp_path / "中文 素材"
    folder.mkdir()
    pixels = np.full((40, 48, 4), 170, np.uint8)
    pixels[:, :, 3] = np.arange(48, dtype=np.uint8)[None] * 5
    pixels[14:24, 16:28, :3] = 0
    path = folder / "超分 图片.png"
    Image.fromarray(pixels).save(path)
    return path, pixels


@pytest.fixture(scope="module")
def onnx_available():
    # Importing ONNX beside an already loaded Qt/OpenCV can fatally crash this
    # pytest process on Windows. Capability checks must also be isolated.
    completed = subprocess.run([sys.executable, "-c", "import onnxruntime; print(onnxruntime.__version__)"],
                               capture_output=True, timeout=30)
    if completed.returncode:
        pytest.skip("ONNX runtime unavailable in a clean subprocess")


def assert_error(row, fragment=None):
    assert row["status"] == "error", row
    assert row["path"] == ""
    assert row["metadata"]["status"] == "error"
    assert row["error"]
    if fragment:
        assert fragment in row["error"]


@pytest.mark.parametrize("scale", [2, 4])
def test_traditional_upscale_unicode_alpha_pixels_and_chain(picture, tmp_path, scale):
    path, pixels = picture
    refs = [{"path": str(path), "kind": "asset", "media_type": "image",
             "id": "source1", "purpose": "source"}]
    before = copy.deepcopy(refs)
    progress = []
    output = tmp_path / "中文输出"
    row = media.run_media_tool("image_upscale", refs,
        {"mode": "traditional", "upscale_scale": scale}, output,
        progress=lambda percent, message: progress.append((percent, message)))[0]
    assert row["status"] == "success", row
    with Image.open(row["path"]) as actual:
        expected = Image.fromarray(pixels).resize((48 * scale, 40 * scale), Image.Resampling.LANCZOS)
        np.testing.assert_array_equal(np.asarray(actual), np.asarray(expected))
        assert actual.mode == "RGBA"
    assert row["metadata"]["source_id"] == "source1"
    assert row["metadata"]["engine"] == "pillow-lanczos"
    assert row["metadata"]["ai"] is False
    assert refs == before
    assert progress[-1][0] == 100
    assert not list(output.glob(".canvas_media_*"))
    minimal = {key: row[key] for key in ("path", "kind")}
    chained = media.run_media_tool("image_upscale", [minimal],
        {"mode": "traditional", "scale": 2}, output)[0]
    assert chained["status"] == "success", chained
    assert chained["path"] != row["path"]
    with Image.open(chained["path"]) as image:
        assert image.size == (48 * scale * 2, 40 * scale * 2)


@pytest.mark.parametrize("params,fragment", [
    ({"mode": "unknown"}, "mode"),
    ({"scale": 3}, "scale"),
    ({"scale": float("nan")}, "必须在"),
    ({"engine": "unknown"}, "engine"),
    ({"engine": "ncnn", "model_path": "model.onnx"}, "NCNN"),
])
def test_invalid_upscale_options_never_publish(picture, tmp_path, params, fragment):
    output = tmp_path / "out"
    row = media.run_media_tool("image_upscale", [{"path": picture[0]}], params, output)[0]
    assert_error(row, fragment)
    assert not list(output.iterdir())


def test_upscale_batch_keeps_success_and_rejects_failed_inputs(picture, tmp_path):
    refs = [{"path": tmp_path / "missing.png", "kind": "image"},
            {"path": picture[0], "kind": "image", "status": "error"},
            {"path": picture[0], "kind": "image", "status": "cancelled"},
            {"path": picture[0], "kind": "video"},
            {"path": picture[0], "kind": "image", "id": "good"}]
    before = copy.deepcopy(refs)
    rows = media.run_media_tool("image_upscale", refs, {"mode": "traditional"}, tmp_path / "out")
    assert len(rows) == len(refs)
    for row in rows[:-1]:
        assert_error(row)
    assert rows[-1]["status"] == "success", rows
    assert rows[-1]["metadata"]["source_id"] == "good"
    assert refs == before


def test_no_model_does_not_fallback_or_download(picture, tmp_path, monkeypatch):
    monkeypatch.setattr(media.Path, "home", lambda: tmp_path / "no-models")
    monkeypatch.delenv("CEP_REALESRGAN_DIR", raising=False)
    def forbidden(*args, **kwargs):
        pytest.fail("Missing models must not start an installer or network process")
    monkeypatch.setattr(media.subprocess, "Popen", forbidden)
    row = media.run_media_tool("image_upscale", [{"path": picture[0], "kind": "image"}],
                               {"engine": "onnx"}, tmp_path / "out")[0]
    assert_error(row, "未下载、未降级")
    assert not list((tmp_path / "out").iterdir())


def test_corrupt_onnx_is_error(picture, tmp_path, onnx_available):
    model = tmp_path / "broken.onnx"
    model.write_bytes(b"not-a-model")
    row = media.run_media_tool("image_upscale", [{"path": picture[0], "kind": "image"}],
        {"model_path": model}, tmp_path / "out")[0]
    assert_error(row)
    assert not list((tmp_path / "out").iterdir())


@pytest.mark.parametrize("scale", [2, 4])
def test_real_local_realesrgan_if_available(tmp_path, onnx_available, monkeypatch, scale):
    model = Path.home() / ".cep_models" / "realesr_general_x4v3.onnx"
    if not model.is_file():
        pytest.skip("No local Real-ESRGAN model; never download during tests")
    source = tmp_path / "真实高清.png"
    pixels = np.zeros((9, 11, 4), np.uint8)
    pixels[:, :, :3] = np.arange(11, dtype=np.uint8)[None, :, None] * 20
    pixels[:, :, 3] = 190
    Image.fromarray(pixels).save(source)
    import builtins
    original_import = builtins.__import__
    def forbid_host_onnx(name, *args, **kwargs):
        if name == "onnxruntime" or name.startswith("onnxruntime."):
            pytest.fail("The Qt/OpenCV host must never import ONNX")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", forbid_host_onnx)
    row = media.run_media_tool("image_upscale", [{"path": source, "kind": "image"}],
        {"model_path": model, "scale": scale}, tmp_path / "out")[0]
    assert row["status"] == "success", row
    with Image.open(row["path"]) as image:
        assert image.size == (11 * scale, 9 * scale)
        assert image.mode == "RGBA"
        assert image.getchannel("A").getextrema() == (190, 190)
        assert np.asarray(image)[:, :, :3].std() > 0
    assert row["metadata"]["engine"] == "Real-ESRGAN ONNX"
    assert row["metadata"]["ai"] is True
    assert row["metadata"]["isolated_process"] is True


def test_onnx_process_crash_does_not_crash_host(picture, tmp_path, monkeypatch):
    model = tmp_path / "local.onnx"
    model.write_bytes(b"dummy")
    monkeypatch.setattr(media, "_onnx_command", lambda *args:
        [sys.executable, "-c", "import os; os._exit(139)"])
    row = media.run_media_tool("image_upscale", [{"path": picture[0], "kind": "image"}],
        {"model_path": model}, tmp_path / "out")[0]
    assert_error(row, "139")
    assert not list((tmp_path / "out").iterdir())


def test_onnx_zero_exit_without_output_is_not_success(picture, tmp_path, monkeypatch):
    model = tmp_path / "local.onnx"
    model.write_bytes(b"dummy")
    monkeypatch.setattr(media, "_onnx_command", lambda *args: [sys.executable, "-c", "pass"])
    row = media.run_media_tool("image_upscale", [{"path": picture[0], "kind": "image"}],
        {"model_path": model}, tmp_path / "out")[0]
    assert_error(row, "未生成图像")
    assert not list((tmp_path / "out").iterdir())


def test_cancel_kills_onnx_process_and_cleans_outputs(picture, tmp_path, monkeypatch):
    model = tmp_path / "local.onnx"
    model.write_bytes(b"dummy")
    monkeypatch.setattr(media, "_onnx_command", lambda *args:
        [sys.executable, "-c", "import time; time.sleep(30)"])
    processes = []
    original_popen = subprocess.Popen
    def capture_process(command, *args, **kwargs):
        process = original_popen(command, *args, **kwargs)
        if command[0] == sys.executable:
            processes.append(process)
        return process
    monkeypatch.setattr(media.subprocess, "Popen", capture_process)
    started = time.monotonic()
    rows = media.run_media_tool("image_upscale", [{"path": picture[0], "kind": "image"}],
        {"model_path": model}, tmp_path / "out", cancel_check=lambda: time.monotonic()-started > .3)
    assert rows[0]["status"] == "cancelled"
    assert rows[0]["path"] == ""
    assert processes and all(process.poll() is not None for process in processes)
    assert time.monotonic()-started < 6
    assert not list((tmp_path / "out").iterdir())


def test_frozen_onnx_command_uses_existing_early_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(media.sys, "frozen", True, raising=False)
    command = media._onnx_command("model", tmp_path / "in.npy", tmp_path / "out.npy")
    assert command[:2] == [sys.executable, "--realesr-worker"]


def test_cancel_before_start_and_between_items(picture, tmp_path):
    refs = [{"path": picture[0], "kind": "image"}] * 3
    result = media.run_media_tool("image_upscale", refs, {"mode": "traditional"},
                                  tmp_path / "never-created", cancel_check=lambda: True)
    assert all(row["status"] == "cancelled" and row["path"] == "" and row["error"] for row in result)
    assert not (tmp_path / "never-created").exists()
    state = {"cancel": False}
    def progress(percent, message):
        if message.endswith("success"):
            state["cancel"] = True
    result = media.run_media_tool("image_upscale", refs, {"mode": "traditional"}, tmp_path / "out",
                                  cancel_check=lambda: state["cancel"], progress=progress)
    assert [row["status"] for row in result] == ["success", "cancelled", "cancelled"]
    assert Path(result[0]["path"]).is_file()


def test_cancel_after_work_removes_unpublished_file(picture, tmp_path, monkeypatch):
    original = media._upscale
    state = {"cancel": False}
    def upscale(*args):
        details = original(*args)
        state["cancel"] = True
        return details
    monkeypatch.setattr(media, "_upscale", upscale)
    rows = media.run_media_tool("image_upscale", [{"path": picture[0], "kind": "image"}],
        {"mode": "traditional"}, tmp_path / "out", cancel_check=lambda: state["cancel"])
    assert rows[0]["status"] == "cancelled"
    assert not list((tmp_path / "out").iterdir())


def test_real_basic_command_error_timeout_and_silent_cancellation():
    assert media._run([sys.executable, "-c", "print('hello')"]).strip() == "hello"
    with pytest.raises(RuntimeError, match="failure detail"):
        media._run([sys.executable, "-c", "import sys; sys.stderr.write('failure detail'); sys.exit(3)"])
    with pytest.raises(TimeoutError):
        media._run([sys.executable, "-c", "import time; time.sleep(20)"], timeout=.2)
    started = time.monotonic()
    with pytest.raises(media.MediaToolCancelled):
        media._run([sys.executable, "-c", "import time; time.sleep(20)"],
                   cancel_check=lambda: time.monotonic() - started > .3)
    assert time.monotonic() - started < 6


@pytest.mark.parametrize("operation", [
    "inpaint", "video_packaging", "media_download", "ae_render", "ae_template_render", "fake",
])
@pytest.mark.parametrize("with_source", [False, True])
def test_removed_tools_are_explicitly_rejected(picture, tmp_path, monkeypatch, operation, with_source):
    def forbidden(*args, **kwargs):
        pytest.fail("Unsupported operations must never start processing")
    monkeypatch.setattr(media, "_upscale", forbidden)
    output = tmp_path / "never-created"
    refs = [{"path": picture[0], "kind": "image"}] if with_source else []
    before = copy.deepcopy(refs)
    with pytest.raises(ValueError, match=f"不支持.*{operation}.*仅支持 image_upscale"):
        media.run_media_tool(operation, refs, {}, output)
    assert refs == before
    assert not output.exists()


@pytest.mark.parametrize("references,params", [(None, {}), ([None], {}), ([{}], None)])
def test_invalid_upscale_schema(tmp_path, references, params):
    with pytest.raises(TypeError):
        media.run_media_tool("image_upscale", references, params, tmp_path / "out")


def test_upscale_requires_sources(tmp_path):
    with pytest.raises(ValueError, match="源素材"):
        media.run_media_tool("image_upscale", [], {}, tmp_path / "out")
