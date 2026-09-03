"""Deterministic temporal/spatial locks for AI video-edit results.

Seedance accepts timestamp and region directions as natural-language guidance,
not as a server-side mask.  The model may therefore redraw frames outside the
requested interval or rectangle.  This module composites the returned video
over the original locally, making the desktop selection authoritative.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ..ark_http import ArkHTTPError


def _ffmpeg_tools() -> tuple[str, str]:
    from utils.ffmpeg_utils import get_ffmpeg_path

    ffmpeg = str(get_ffmpeg_path())
    ffprobe_path = Path(ffmpeg).with_name("ffprobe.exe")
    ffprobe = str(ffprobe_path) if ffprobe_path.is_file() else "ffprobe"
    return ffmpeg, ffprobe


def _probe_video(path: str | Path) -> dict:
    _ffmpeg, ffprobe = _ffmpeg_tools()
    completed = subprocess.run(
        [
            ffprobe, "-v", "error", "-print_format", "json",
            "-show_streams", "-show_format", str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "ffprobe 失败").strip()
        raise ArkHTTPError(f"读取视频信息失败：{detail[-800:]}")
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as error:
        raise ArkHTTPError("读取视频信息失败：ffprobe 返回了无效数据") from error
    streams = list(payload.get("streams") or [])
    video = next(
        (value for value in streams if value.get("codec_type") == "video"), {})
    duration = float(
        (payload.get("format") or {}).get("duration") or
        video.get("duration") or 0)
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if duration <= 0 or width <= 0 or height <= 0:
        raise ArkHTTPError("读取视频信息失败：缺少有效的时长或画面尺寸")
    return {
        "duration":duration,
        "width":width,
        "height":height,
        "has_audio":any(value.get("codec_type") == "audio" for value in streams),
    }


def _normalized_region(region: dict | None) -> dict:
    value = region if isinstance(region, dict) else {}
    try:
        x = max(0.0, min(1.0, float(value.get("x") or 0)))
        y = max(0.0, min(1.0, float(value.get("y") or 0)))
        width = max(0.0, min(1.0 - x, float(value.get("width") or 0)))
        height = max(0.0, min(1.0 - y, float(value.get("height") or 0)))
    except (TypeError, ValueError):
        return {}
    if width <= 0 or height <= 0:
        return {}
    return {"x":x, "y":y, "width":width, "height":height}


def _hard_lock_filter(*, source_duration: float, generated_duration: float,
                      width: int, height: int, start: float, end: float,
                      region: dict | None) -> tuple[str, dict]:
    """Build the FFmpeg graph and return the effective pixel lock metadata."""
    start = max(0.0, min(float(source_duration), float(start)))
    end = max(start, min(float(source_duration), float(end)))
    if end - start < 0.04:
        raise ArkHTTPError("视频局部编辑范围过短，至少需要一个有效画面区间")
    width = max(2, int(width) - int(width) % 2)
    height = max(2, int(height) - int(height) % 2)
    speed = float(source_duration) / max(0.001, float(generated_duration))
    timing = (
        f"setpts={speed:.12g}*(PTS-STARTPTS),"
        f"scale={int(width)}:{int(height)}:force_original_aspect_ratio=disable,"
        "setsar=1"
    )
    effective = _normalized_region(region)
    enable = f"gte(t,{start:.6f})*lt(t,{end:.6f})"
    if effective:
        x = min(max(0, int(round(effective["x"] * width))), max(0, width - 2))
        y = min(max(0, int(round(effective["y"] * height))), max(0, height - 2))
        crop_w = min(width - x, max(2, int(round(effective["width"] * width))))
        crop_h = min(height - y, max(2, int(round(effective["height"] * height))))
        # H.264/yuv420p needs even crop dimensions and offsets.
        x -= x % 2
        y -= y % 2
        crop_w = max(2, min(width - x, crop_w - crop_w % 2))
        crop_h = max(2, min(height - y, crop_h - crop_h % 2))
        effective = {
            "x":x / width, "y":y / height,
            "width":crop_w / width, "height":crop_h / height,
        }
        graph = (
            f"[0:v]setpts=PTS-STARTPTS,scale={width}:{height},setsar=1[base];"
            f"[1:v]{timing},crop={crop_w}:{crop_h}:{x}:{y}[edit];"
            f"[base][edit]overlay={x}:{y}:eof_action=pass:repeatlast=0:"
            f"enable='{enable}'[locked]"
        )
    else:
        graph = (
            f"[0:v]setpts=PTS-STARTPTS,scale={width}:{height},setsar=1[base];"
            f"[1:v]{timing}[edit];"
            f"[base][edit]overlay=0:0:eof_action=pass:repeatlast=0:"
            f"enable='{enable}'[locked]"
        )
    return graph, effective


def enforce_timestamp_edit_lock(original: str | Path, generated: str | Path,
                                output: str | Path, *, start: float, end: float,
                                region: dict | None = None) -> dict:
    """Keep original frames outside time/region and preserve original audio."""
    source = Path(original).expanduser().resolve()
    candidate = Path(generated).expanduser().resolve()
    target = Path(output).expanduser().resolve()
    if not source.is_file() or not candidate.is_file():
        raise ArkHTTPError("视频局部编辑合成失败：原视频或 AI 结果不存在")
    source_info = _probe_video(source)
    candidate_info = _probe_video(candidate)
    graph, effective_region = _hard_lock_filter(
        source_duration=source_info["duration"],
        generated_duration=candidate_info["duration"],
        width=source_info["width"], height=source_info["height"],
        start=start, end=end, region=region,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".building.mp4")
    temporary.unlink(missing_ok=True)
    ffmpeg, _ffprobe = _ffmpeg_tools()
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source), "-i", str(candidate),
        "-filter_complex", graph,
        "-map", "[locked]",
    ]
    if source_info["has_audio"]:
        # A visual region edit must never silently replace dialogue/music outside
        # the selection.  Mapping source audio also avoids regenerated timing drift.
        command.extend(["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k"])
    else:
        command.append("-an")
    command.extend([
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-t", f"{source_info['duration']:.6f}", str(temporary),
    ])
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=1800, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if (completed.returncode != 0 or not temporary.is_file() or
            temporary.stat().st_size <= 1024):
        temporary.unlink(missing_ok=True)
        detail = (completed.stderr or completed.stdout or "FFmpeg 合成失败").strip()
        raise ArkHTTPError(f"视频局部编辑硬约束合成失败：{detail[-1000:]}")
    os.replace(temporary, target)
    return {
        "applied":True,
        "start":max(0.0, float(start)),
        "end":min(source_info["duration"], float(end)),
        "region":effective_region,
        "outside_time_preserved":True,
        "outside_region_preserved":bool(effective_region),
        "original_audio_preserved":True,
    }
