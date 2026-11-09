from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import headless_tools
from core.headless_tools import (
    _image_pipeline_context, classify_instruction, resolve_slideshow_audio, resolve_slideshow_video_roles, slideshow_mentioned_image_order,
    slideshow_schedule,
)
import pytest


def test_desktop_batch_tools_are_selected_only_from_connected_media() -> None:
    images = [{"kind": "image"}, {"kind": "image"}]
    videos = [{"kind": "video"}, {"kind": "video"}]
    audio = [{"kind": "audio"}]

    assert classify_instruction("把这些图片批量改尺寸并重命名", images) == "image_batch"
    assert classify_instruction("调用桌面端批量修改图片尺寸功能，改成1080×1920", images) == "image_batch"
    assert classify_instruction("把所有图片的大小统一调整为9:16", images) == "image_batch"
    assert classify_instruction("把 @图片1 和 @图片2 都调整为1080×1920", images) == "image_batch"
    assert classify_instruction("把所有连接图片批量缩放2倍", images) == "image_batch"
    assert classify_instruction("把这些照片做成9:16图片轮播", images) == "slideshow"
    assert classify_instruction(
        "@图片1出现2秒，@图片2出现3秒，@图片3出现1秒，按顺序轮播",
        images,
    ) == "slideshow"
    assert classify_instruction(
        "图1出现2秒，图2出现3秒，图3出现1秒，图4出现3秒，图5出现三秒。按顺序剪辑",
        images,
    ) == "slideshow"
    assert classify_instruction("按模式C批量混剪10条", videos) == "video_mix"
    assert classify_instruction("分离人声和背景音乐", videos + audio) == "audio_separate"
    assert classify_instruction("批量混剪10条", images) == ""
    assert classify_instruction("生成一张1080×1920的图片", images) == ""
    assert classify_instruction("把全局视频都混剪", []) == ""


def test_desktop_image_resize_parses_common_natural_language_targets() -> None:
    operations, context = _image_pipeline_context("批量修改图片尺寸为 1080×1920", {})
    assert operations == ["resize"]
    assert context["resize"] == {"enabled": True, "mode": "size", "width": 1080, "height": 1920}

    _operations, context = _image_pipeline_context("把所有图片统一调整为4:5", {})
    assert context["resize"] == {"enabled": True, "mode": "preset", "preset": "4:5"}

    _operations, context = _image_pipeline_context("这些照片批量修改大小，宽1080高1920", {})
    assert context["resize"] == {"enabled": True, "mode": "size", "width": 1080, "height": 1920}


def test_desktop_image_resize_rejects_missing_target_instead_of_succeeding_without_change() -> None:
    with pytest.raises(ValueError, match="没有找到目标尺寸"):
        _image_pipeline_context("批量修改图片尺寸", {})


def test_desktop_image_resize_processes_every_connected_image(tmp_path) -> None:
    from PIL import Image

    sources = [tmp_path / "first.png", tmp_path / "second.png"]
    Image.new("RGB", (320, 240), "red").save(sources[0])
    Image.new("RGB", (240, 320), "blue").save(sources[1])

    outputs, metadata = headless_tools.run_image_batch(
        sources, tmp_path / "outputs", "批量修改图片尺寸为 1080×1920",
        {}, lambda: False, lambda _value: None,
    )

    assert len(outputs) == 2
    assert [Image.open(path).size for path in outputs] == [(1080, 1920), (1080, 1920)]
    assert metadata["steps"] == ["resize"]
    assert metadata["failures"] == []


def test_slideshow_schedule_preserves_alias_order_and_spoken_durations(tmp_path) -> None:
    images = [tmp_path / f"image-{index}.png" for index in range(1, 6)]
    for path in images:
        path.write_bytes(b"source")
    sources = [
        {"kind": "image", "path": str(path), "alias": f"图片{index}"}
        for index, path in enumerate(images, 1)
    ]

    schedule = slideshow_schedule(
        "图1出现2秒， 图2出现3秒， 图3出现1秒，图4出现3秒， 图5出现三秒。按顺序剪辑。",
        sources,
    )

    assert [path for path, _duration in schedule] == images
    assert [duration for _path, duration in schedule] == [2, 3, 1, 3, 3]


def test_slideshow_parses_written_mention_order_for_explicit_order_commands(tmp_path) -> None:
    images = [tmp_path / f"image-{index}.png" for index in range(1, 6)]
    for path in images:
        path.write_bytes(b"source")
    sources = [
        {"kind": "image", "path": str(path), "alias": f"图片{index}"}
        for index, path in enumerate(images, 1)
    ]

    order = slideshow_mentioned_image_order(
        "@图片1 @图片2 @图片3 @图片4 @图片5 做图片轮播。素材对应关系：图片1=a，图片2=b。",
        sources,
    )

    assert order == images


def test_plain_slideshow_keeps_shuffle_mode_even_when_images_are_mentioned(tmp_path, monkeypatch) -> None:
    images = [tmp_path / f"image-{index}.png" for index in range(1, 6)]
    for path in images:
        path.write_bytes(b"source")
    sources = [
        {"kind": "image", "path": str(path), "alias": f"图片{index}"}
        for index, path in enumerate(images, 1)
    ]
    calls = []
    def fake_render(selected, output, cfg, **_kwargs):
        calls.append({"images": [Path(path) for path in selected], "config": dict(cfg)})
        Path(output).write_bytes(b"video")
    monkeypatch.setattr("core.slideshow_engine.render_video", fake_render)

    _outputs, metadata = headless_tools.run_slideshow(
        images, [], [], tmp_path / "out",
        "@图片1 @图片2 @图片3 @图片4 @图片5 做图片轮播",
        {"batch_seed": 123}, lambda: False, lambda _value: None, sources,
    )

    assert metadata["config"]["sequential_mode"] is False
    assert metadata["config"]["shuffle_mode"] is True
    assert calls[0]["images"] != images
    assert set(calls[0]["images"]) == set(images)


def test_explicit_order_command_uses_mentioned_image_order(tmp_path, monkeypatch) -> None:
    images = [tmp_path / f"image-{index}.png" for index in range(1, 4)]
    for path in images:
        path.write_bytes(b"source")
    sources = [
        {"kind": "image", "path": str(path), "alias": f"图片{index}"}
        for index, path in enumerate(images, 1)
    ]
    calls = []
    def fake_render(selected, output, cfg, **_kwargs):
        calls.append([Path(path) for path in selected])
        Path(output).write_bytes(b"video")
    monkeypatch.setattr("core.slideshow_engine.render_video", fake_render)

    headless_tools.run_slideshow(
        images, [], [], tmp_path / "out",
        "@图片3 @图片1 @图片2 按顺序做图片轮播",
        {}, lambda: False, lambda _value: None, sources,
    )

    assert calls[0] == [images[2], images[0], images[1]]


def test_slideshow_keeps_desktop_random_transition_music_and_selection_modes(tmp_path, monkeypatch) -> None:
    images = [tmp_path / f"image-{index}.png" for index in range(4)]
    audios = [tmp_path / f"music-{index}.mp3" for index in range(3)]
    for path in [*images, *audios]:
        path.write_bytes(b"source")

    calls: list[dict] = []

    def fake_render(selected, output, cfg, progress_callback=None, stop_event=None):
        assert stop_event is not None and not stop_event.is_set()
        calls.append({"images": list(selected), "transition": cfg["transition_type"]})
        Path(output).write_bytes(b"video")

    def fake_mix(video, bgm, output, volume_pct=80, ffmpeg_path=None, stop_event=None):
        assert stop_event is not None and not stop_event.is_set()
        calls[-1]["bgm"] = Path(bgm).name
        Path(output).write_bytes(b"mixed")

    monkeypatch.setattr("core.slideshow_engine.render_video", fake_render)
    monkeypatch.setattr("core.slideshow_engine.mix_audio", fake_mix)

    outputs, metadata = headless_tools.run_slideshow(
        images, audios, [], tmp_path / "out",
        "做图片轮播，生成3条，每条2张，随机转场，随机音乐，纯随机",
        {"batch_seed": 123}, lambda: False, lambda _: None,
    )

    assert len(outputs) == 3
    assert metadata["config"]["random_transition"] is True
    assert metadata["config"]["random_bgm"] is True
    assert metadata["config"]["shuffle_mode"] is False
    assert all(len(call["images"]) == 2 for call in calls)
    assert all(call["bgm"] in {path.name for path in audios} for call in calls)
    assert [item["transition"] for item in metadata["items"]] == [call["transition"] for call in calls]


def test_slideshow_passes_exact_per_image_durations_to_renderer(tmp_path, monkeypatch) -> None:
    images = [tmp_path / f"image-{index}.png" for index in range(1, 6)]
    for path in images:
        path.write_bytes(b"source")
    sources = [
        {"kind": "image", "path": str(path), "alias": f"图片{index}"}
        for index, path in enumerate(images, 1)
    ]
    calls: list[dict] = []

    def fake_render(selected, output, cfg, progress_callback=None, stop_event=None):
        calls.append({"images": list(selected), "config": dict(cfg)})
        Path(output).write_bytes(b"video")

    monkeypatch.setattr("core.slideshow_engine.render_video", fake_render)
    outputs, metadata = headless_tools.run_slideshow(
        images, [], [], tmp_path / "out",
        "@图片1出现2秒，@图片2出现3秒，@图片3出现1秒，@图片4出现3秒，@图片5出现三秒。按顺序轮播",
        {}, lambda: False, lambda _: None, sources,
    )

    assert len(outputs) == 1
    assert calls[0]["images"] == [str(path) for path in images]
    assert calls[0]["config"]["image_durations"] == [2, 3, 1, 3, 3]
    assert calls[0]["config"]["video_duration"] == 12
    assert metadata["items"][0]["image_durations"] == [2, 3, 1, 3, 3]


def test_slideshow_resolves_watermark_and_outro_by_meaning_not_video_order(tmp_path, monkeypatch) -> None:
    image = tmp_path / "image.png"
    audio = tmp_path / "audio.mp3"
    outro = tmp_path / "outro.mp4"
    watermark = tmp_path / "watermark.mov"
    for path in (image, audio, outro, watermark):
        path.write_bytes(b"source")
    instruction = (
        "@图片1 @图片2 @图片3 @图片4 @图片5 做图片轮播，使用@音频1，"
        "加上尾页视频 @视频1，这 @视频2 是水印视频也加上"
    )
    video_sources = [
        {"kind": "video", "path": str(outro), "alias": "视频1"},
        {"kind": "video", "path": str(watermark), "alias": "视频2"},
    ]
    roles = resolve_slideshow_video_roles(instruction, video_sources)

    assert Path(roles["outro"]["path"]) == outro
    assert Path(roles["watermark"]["path"]) == watermark

    calls = []
    monkeypatch.setattr("core.slideshow_engine.render_video", lambda _images, output, _cfg, **_kwargs: Path(output).write_bytes(b"video"))
    monkeypatch.setattr("core.slideshow_engine.mix_audio", lambda _video, _audio, output, *_args, **_kwargs: Path(output).write_bytes(b"audio-video"))
    def fake_package(_engine, _cancelled, source, output, **kwargs):
        calls.append({"source": Path(source), **kwargs})
        Path(output).write_bytes(b"packaged")
        return ["watermark", "outro"]
    monkeypatch.setattr("core.headless_support._package_video", fake_package)

    outputs, metadata = headless_tools.run_slideshow(
        [image], [audio], [outro, watermark], tmp_path / "out", instruction,
        {}, lambda: False, lambda _value: None,
        [{"kind": "image", "path": str(image), "alias": "图片1"}], video_sources,
    )

    assert outputs[0].read_bytes() == b"packaged"
    assert calls[0]["watermark_path"] == watermark
    assert calls[0]["outro_path"] == outro
    assert metadata["items"][0]["packaging"] == ["watermark", "outro"]


def test_slideshow_selects_explicitly_mentioned_audio(tmp_path) -> None:
    first = tmp_path / "first.mp3"
    second = tmp_path / "second.mp3"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    selected = resolve_slideshow_audio(
        "做图片轮播，使用 @音频2",
        [
            {"kind": "audio", "path": str(first), "alias": "音频1"},
            {"kind": "audio", "path": str(second), "alias": "音频2"},
        ],
    )

    assert selected == second


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("Headless tools must not connect to a server")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def _image(tmp_path, name="image.png", color="red"):
    from PIL import Image

    path = tmp_path / name
    Image.new("RGB", (80, 60), color).save(path)
    return path


def _wave(tmp_path, channels=1):
    import math
    import struct
    import wave

    path = tmp_path / "tone.wav"
    samples = [round(3000 * math.sin(2 * math.pi * 440 * i / 8000)) for i in range(8000)]
    with wave.open(str(path), "wb") as output:
        output.setparams((channels, 2, 8000, 0, "NONE", "not compressed"))
        output.writeframes(b"".join(struct.pack("<h", sample) * channels for sample in samples))
    return path


def test_agent_entrypoint_returns_absolute_records_and_progress(tmp_path):
    from PIL import Image

    source = _image(tmp_path)
    progress = []
    result = headless_tools.execute_desktop_tool(
        "image_batch", "批量修改图片尺寸为64x48", [{"local_path": str(source), "media_type": "image"}],
        tmp_path / "output", params={}, progress=progress.append, cancelled=lambda: False,
    )
    assert len(result) == 1 and result[0]["kind"] == "image"
    assert Path(result[0]["path"]).is_absolute()
    with Image.open(result[0]["path"]) as image:
        assert image.size == (64, 48)
    assert progress[-1] == 100 and progress == sorted(progress)


def test_image_outputs_do_not_overwrite_sources_or_duplicate_basenames(tmp_path):
    first = _image(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    second = _image(other, color="blue")
    original = first.read_bytes()
    records = headless_tools.execute_desktop_tool(
        "image_batch", "批量修改图片尺寸为64x48",
        [{"path": str(path), "kind": "image"} for path in (first, second)], tmp_path,
    )
    assert first.read_bytes() == original
    assert len({item["path"] for item in records}) == 2
    assert all(Path(item["path"]) != first for item in records)


def test_image_four_five_preset_does_not_use_old_square_fallback(tmp_path):
    from PIL import Image

    source = _image(tmp_path)
    records = headless_tools.execute_desktop_tool(
        "image_batch", "统一修改图片比例为4:5", [{"path": str(source), "kind": "image"}], tmp_path / "out",
    )
    with Image.open(records[0]["path"]) as image:
        assert image.size == (1080, 1350)


def test_partial_image_failures_are_returned_as_error_records(tmp_path):
    source = _image(tmp_path)
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"broken")
    result = headless_tools.execute_desktop_tool(
        "image_batch", "批量修改图片尺寸为64x48",
        [{"path": str(path), "kind": "image"} for path in (source, broken)], tmp_path / "out",
    )
    assert len(result) == 2
    assert result[0]["kind"] == "image"
    assert result[1]["error"] and result[1]["source"] == broken.name


@pytest.mark.parametrize("path", ["https://example.invalid/image.png", "http://127.0.0.1:8000/media/a", "missing.png", ""])
def test_remote_and_missing_references_fail_before_output_creation(tmp_path, path):
    output = tmp_path / "out"
    with pytest.raises(ValueError):
        headless_tools.execute_desktop_tool("image_batch", "压缩图片", [{"path": path, "kind": "image"}], output)
    assert not output.exists()


def test_invalid_operation_and_empty_inputs_fail(tmp_path):
    with pytest.raises(ValueError, match="不支持"):
        headless_tools.execute_desktop_tool("shell", "echo test", [], tmp_path / "out")
    for operation in headless_tools.CAPABILITIES:
        with pytest.raises(ValueError):
            headless_tools.execute_desktop_tool(operation, "", [], tmp_path / operation)


def test_rename_cannot_write_outside_output_folder(tmp_path):
    source = _image(tmp_path)
    with pytest.raises(ValueError, match="输出名称"):
        headless_tools.execute_desktop_tool(
            "image_batch", "批量重命名", [{"path": str(source), "kind": "image"}],
            tmp_path / "out", params={"rename_pattern": "../escape_{num}"},
        )
    assert not (tmp_path / "escape_0001.png").exists()


def test_missing_superres_model_fails_without_silent_success(tmp_path, monkeypatch):
    from core.plugins import superres

    monkeypatch.setattr(superres, "_model_path", lambda: str(tmp_path / "missing.onnx"))
    source = _image(tmp_path)
    with pytest.raises(ValueError, match="超分模型未安装"):
        headless_tools.execute_desktop_tool(
            "image_batch", "批量图片高清化", [{"path": str(source), "kind": "image"}], tmp_path / "out",
        )


def test_superres_reuses_installed_underscore_model_name(tmp_path, monkeypatch):
    import numpy as np
    from core.plugins import superres

    configured = tmp_path / "realesr-general-x4v3.onnx"
    installed = tmp_path / "realesr_general_x4v3.onnx"
    installed.write_bytes(b"weights")
    monkeypatch.setattr(superres, "_model_path", lambda: str(configured))
    paths = []

    def upscale(rgb, model_path):
        paths.append(model_path)
        return np.repeat(np.repeat(rgb, 4, axis=0), 4, axis=1)

    monkeypatch.setattr(superres, "_run_realesr_subproc", upscale)
    source = _image(tmp_path)
    result = headless_tools.execute_desktop_tool(
        "image_batch", "图片高清化", [{"path": str(source), "kind": "image"}], tmp_path / "out",
    )
    assert paths == [str(installed)]
    assert result[0]["kind"] == "image"
    assert not configured.exists()


def test_superres_local_subprocess_is_also_cancellable(tmp_path, monkeypatch):
    import threading
    from core.headless_support import local_superres
    from core.plugins import superres
    import numpy as np

    model = tmp_path / "model.onnx"
    model.write_bytes(b"weights")
    monkeypatch.setattr(superres, "_model_path", lambda: str(model))

    def upscale(rgb, model_path):
        import subprocess
        subprocess.run([sys.executable, "-c", "import time; time.sleep(60)"], capture_output=True)
        return rgb

    monkeypatch.setattr(superres, "_run_realesr_subproc", upscale)
    event = threading.Event()
    timer = threading.Timer(0.3, event.set)
    timer.start()
    try:
        with pytest.raises(headless_tools.DesktopToolCancelled):
            local_superres(event.is_set).run(np.zeros((16, 16, 4), dtype=np.uint8), {})
    finally:
        timer.cancel()


def test_superres_development_worker_receives_x4_scale_and_original_arguments(tmp_path):
    from core.headless_support import _SuperresSubprocess

    script = tmp_path / "run_esr.py"
    content = "import sys; print(scale, sys.argv[1:])"
    script.write_text(content, encoding="utf-8")
    result = _SuperresSubprocess(lambda: False).run(
        [sys.executable, str(script), "model.onnx", "in.npy", "out.npy"],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "4 ['model.onnx', 'in.npy', 'out.npy']"
    assert script.read_text(encoding="utf-8") == content


@pytest.mark.parametrize("operation", list(headless_tools.CAPABILITIES))
def test_pre_cancelled_tools_do_not_create_outputs(tmp_path, operation):
    output = tmp_path / "out"
    with pytest.raises(headless_tools.DesktopToolCancelled):
        headless_tools.execute_desktop_tool(operation, "", [], output, cancelled=lambda: True)
    assert not output.exists()


def test_cancellation_inside_image_plugin_is_not_a_partial_failure(tmp_path, monkeypatch):
    from core.plugins.resize import Resize

    source = _image(tmp_path)
    state = {"cancelled": False}

    def stop(self, array, ctx):
        state["cancelled"] = True
        return array

    monkeypatch.setattr(Resize, "run", stop)
    with pytest.raises(headless_tools.DesktopToolCancelled):
        headless_tools.execute_desktop_tool(
            "image_batch", "批量修改图片尺寸为64x48", [{"path": str(source), "kind": "image"}],
            tmp_path / "out", cancelled=lambda: state["cancelled"],
        )
    assert not list((tmp_path / "out").glob("*.png"))


def test_slideshow_cancel_during_frames_cleans_temporary_files(tmp_path):
    source = _image(tmp_path)
    state = {"cancelled": False}

    def progress(value):
        if value > 10:
            state["cancelled"] = True

    output = tmp_path / "out"
    with pytest.raises(headless_tools.DesktopToolCancelled):
        headless_tools.execute_desktop_tool(
            "slideshow", "图片轮播", [{"path": str(source), "kind": "image"}], output,
            params={"resolution": "160x120", "target_duration": 1, "fps": 12},
            progress=progress, cancelled=lambda: state["cancelled"],
        )
    assert list(output.iterdir()) == []


def test_cancellable_subprocess_kills_child_without_patching_engine_globals(monkeypatch):
    import subprocess
    import threading
    import time
    from core import headless_support, mix_engine, slideshow_engine

    original_run = subprocess.run
    event = threading.Event()
    timer = threading.Timer(0.3, event.set)
    children = []
    original_popen = subprocess.Popen

    def track(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", track)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(headless_tools.DesktopToolCancelled):
            headless_support._Subprocess(event.is_set).run(
                [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(60)"], capture_output=True,
            )
    finally:
        timer.cancel()
    assert time.monotonic() - started < 5
    assert children and all(process.poll() is not None for process in children)
    assert mix_engine.subprocess.run is original_run
    assert slideshow_engine.subprocess.run is original_run


def test_subprocess_timeout_still_terminates_child():
    import subprocess
    from core.headless_support import _Subprocess

    with pytest.raises(subprocess.TimeoutExpired):
        _Subprocess(lambda: False).run([sys.executable, "-c", "import time; time.sleep(60)"], timeout=0.15)


def test_cancel_swallowed_by_existing_mixer_is_reraised(tmp_path, monkeypatch):
    from core import mix_engine

    video = tmp_path / "source.mp4"
    video.write_bytes(b"video")
    state = {"cancelled": False}

    def fail(self, *args, **kwargs):
        state["cancelled"] = True
        Path(args[2]).write_bytes(b"partial")
        return False

    monkeypatch.setattr(mix_engine.FFmpegMixer, "render_task", fail)
    monkeypatch.setattr("core.slideshow_engine.get_video_duration", lambda *_args: 5)
    with pytest.raises(headless_tools.DesktopToolCancelled):
        headless_tools.execute_desktop_tool(
            "video_mix", "模式C批量混剪", [{"path": str(video), "kind": "video"}],
            tmp_path / "out", cancelled=lambda: state["cancelled"],
        )
    assert list((tmp_path / "out").iterdir()) == []


def test_import_is_headless_and_independent_of_web_server():
    import subprocess

    code = """
import sys
from core.headless_tools import execute_desktop_tool
assert callable(execute_desktop_tool)
assert not any(name.startswith(('creative_server', 'server.', 'PyQt6', 'requests', 'openai', 'ai.service')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-B", "-c", code], check=True, capture_output=True, timeout=15)


def test_real_small_slideshow_preserves_duration_order_and_resolution(tmp_path):
    import cv2

    sources = [_image(tmp_path, "红色.png"), _image(tmp_path, "蓝色.png", "blue")]
    records = headless_tools.execute_desktop_tool(
        "slideshow", "@图片1出现半秒，@图片2出现一秒，按顺序轮播",
        [{"path": str(path), "kind": "image", "alias": f"图片{index}"} for index, path in enumerate(sources, 1)],
        tmp_path / "out", params={"resolution": "160x120", "fps": 12, "transition_frames": 0},
    )
    assert len(records) == 1 and records[0]["kind"] == "video"
    video = cv2.VideoCapture(records[0]["path"])
    try:
        assert video.isOpened()
        assert (video.get(cv2.CAP_PROP_FRAME_WIDTH), video.get(cv2.CAP_PROP_FRAME_HEIGHT)) == (160, 120)
        assert video.get(cv2.CAP_PROP_FPS) == pytest.approx(12)
        assert video.get(cv2.CAP_PROP_FRAME_COUNT) == 18
        ok, red = video.read()
        assert ok and red[..., 2].mean() > 200 and red[..., 0].mean() < 30
        video.set(cv2.CAP_PROP_POS_FRAMES, 9)
        ok, blue = video.read()
        assert ok and blue[..., 0].mean() > 200 and blue[..., 2].mean() < 30
    finally:
        video.release()
    assert len(list((tmp_path / "out").iterdir())) == 1


def test_real_audio_separation_accepts_mono_and_requires_no_model(tmp_path, monkeypatch):
    import wave
    from core.separator import AudioSeparator

    def no_detection(self):
        raise AssertionError("Optional model detection must not run")

    monkeypatch.setattr(AudioSeparator, "_detect_engine", no_detection)
    source = _wave(tmp_path)
    original = source.read_bytes()
    records = headless_tools.execute_desktop_tool(
        "audio_separate", "分离人声和伴奏", [{"path": str(source), "kind": "audio"}], tmp_path / "out",
    )
    assert len(records) == 2 and all(row["kind"] == "audio" for row in records)
    assert all(row["metadata"]["neural_separation"] is False for row in records)
    for row in records:
        with wave.open(row["path"]) as audio:
            assert audio.getframerate() == 44100
            assert audio.getnframes() / 44100 == pytest.approx(1, abs=0.01)
    assert source.read_bytes() == original
    assert len(list((tmp_path / "out").iterdir())) == 2


def test_real_mix_uses_local_generator_and_ffmpeg(tmp_path):
    import cv2

    source = _image(tmp_path)
    video = headless_tools.execute_desktop_tool(
        "slideshow", "图片轮播", [{"path": str(source), "kind": "image"}], tmp_path / "source",
        params={"resolution": "160x120", "fps": 12, "target_duration": 2},
    )[0]
    records = headless_tools.execute_desktop_tool(
        "video_mix", "模式C批量混剪，生成1条，每条1秒，160x120", [video], tmp_path / "out",
    )
    assert len(records) == 1 and records[0]["kind"] == "video"
    capture = cv2.VideoCapture(records[0]["path"])
    try:
        assert capture.isOpened()
        assert capture.get(cv2.CAP_PROP_FRAME_COUNT) / capture.get(cv2.CAP_PROP_FPS) == pytest.approx(1, abs=0.1)
        assert (capture.get(cv2.CAP_PROP_FRAME_WIDTH), capture.get(cv2.CAP_PROP_FRAME_HEIGHT)) == (160, 120)
    finally:
        capture.release()


@pytest.mark.parametrize("mode", list("ABCDEF"))
def test_all_mix_modes_dispatch_to_existing_generator(tmp_path, monkeypatch, mode):
    import core.headless_support

    videos = []
    for i in range(3):
        video = tmp_path / f"source{i}.mp4"
        video.write_bytes(b"video")
        videos.append({"path": str(video), "kind": "video"})
    calls = []

    class Mixer:
        def render_task(self, slots, audio, out, **kwargs):
            calls.append(slots)
            Path(out).write_bytes(b"video")
            return True

    monkeypatch.setattr(core.headless_support, "local_mixer", lambda *args: Mixer())
    monkeypatch.setattr("core.slideshow_engine.get_video_duration", lambda *args: 10)
    result = headless_tools.execute_desktop_tool("video_mix", f"模式{mode}批量混剪", videos, tmp_path / "out")
    assert result[0]["metadata"]["mode"] == mode
    assert calls and all(slot is not None for slot in calls[0])
    if mode == "F":
        assert [Path(slot["mat"].path).name for slot in calls[0]] == ["source0.mp4", "source1.mp4", "source2.mp4"]
