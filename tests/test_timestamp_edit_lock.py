import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ai.providers.video.timestamp_edit_lock import (
    _ffmpeg_tools,
    _hard_lock_filter,
    enforce_timestamp_edit_lock,
)


def test_whole_frame_lock_is_enabled_only_inside_selected_time():
    graph, region = _hard_lock_filter(
        source_duration=20, generated_duration=20,
        width=1920, height=1080, start=5.1, end=12.8, region={})

    assert region == {}
    assert "overlay=0:0" in graph
    assert "gte(t,5.100000)*lt(t,12.800000)" in graph
    assert "repeatlast=0" in graph


def test_region_lock_crops_ai_pixels_and_keeps_the_original_base():
    graph, region = _hard_lock_filter(
        source_duration=10, generated_duration=8,
        width=1000, height=600, start=2, end=6,
        region={"x":0.2, "y":0.25, "width":0.35, "height":0.4})

    assert region == {"x":0.2, "y":0.25, "width":0.35, "height":0.4}
    assert "crop=350:240:200:150" in graph
    assert "overlay=200:150" in graph
    assert "setpts=1.25*(PTS-STARTPTS)" in graph


def test_enforcement_preserves_original_audio_and_writes_locked_result():
    root = Path(tempfile.mkdtemp(prefix="cep_timestamp_lock_"))
    original = root / "original.mov"
    generated = root / "generated.mp4"
    output = root / "locked.mp4"
    original.write_bytes(b"original")
    generated.write_bytes(b"generated")
    probes = [
        {"duration":18.0, "width":1080, "height":1920, "has_audio":True},
        {"duration":18.0, "width":1080, "height":1920, "has_audio":True},
    ]
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"x" * 2048)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with (
        patch(
            "ai.providers.video.timestamp_edit_lock._probe_video",
            side_effect=probes),
        patch(
            "ai.providers.video.timestamp_edit_lock._ffmpeg_tools",
            return_value=("ffmpeg", "ffprobe")),
        patch(
            "ai.providers.video.timestamp_edit_lock.subprocess.run",
            side_effect=fake_run),
    ):
        metadata = enforce_timestamp_edit_lock(
            original, generated, output, start=4, end=9,
            region={"x":0.1, "y":0.1, "width":0.5, "height":0.5})

    assert output.is_file()
    assert metadata["outside_time_preserved"] is True
    assert metadata["outside_region_preserved"] is True
    assert metadata["original_audio_preserved"] is True
    command = commands[0]
    assert command[command.index("-map", command.index("-map") + 1) + 1] == "0:a:0"
    assert "+faststart" in command


def test_real_ffmpeg_keeps_pixels_outside_time_and_rectangle():
    ffmpeg, ffprobe = _ffmpeg_tools()
    if not Path(ffmpeg).is_file() or not shutil.which(ffprobe):
        import pytest
        pytest.skip("FFmpeg/ffprobe are not available")
    from PIL import Image

    root = Path(tempfile.mkdtemp(prefix="cep_timestamp_lock_real_"))
    original = root / "blue.mp4"
    generated = root / "red.mp4"
    output = root / "locked.mp4"
    for color, target in (("blue", original), ("red", generated)):
        completed = subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", f"color=c={color}:s=64x64:d=4:r=10",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(target),
            ], capture_output=True, timeout=60, check=False)
        assert completed.returncode == 0

    enforce_timestamp_edit_lock(
        original, generated, output, start=1, end=3,
        region={"x":0.25, "y":0.25, "width":0.5, "height":0.5})
    before = root / "before.png"
    inside = root / "inside.png"
    for second, target in ((0.5, before), (2.0, inside)):
        completed = subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-ss", str(second), "-i", str(output),
                "-frames:v", "1", str(target),
            ], capture_output=True, timeout=60, check=False)
        assert completed.returncode == 0

    before_image = Image.open(before).convert("RGB")
    inside_image = Image.open(inside).convert("RGB")
    before_center = before_image.getpixel((32, 32))
    inside_center = inside_image.getpixel((32, 32))
    inside_corner = inside_image.getpixel((4, 4))
    assert before_center[2] > 180 and before_center[0] < 70
    assert inside_center[0] > 180 and inside_center[2] < 70
    assert inside_corner[2] > 180 and inside_corner[0] < 70
