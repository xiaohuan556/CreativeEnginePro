"""基于 FFmpeg scene score 的镜头跳变检测。"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable


SCENE_PRESETS = {
    "保守（只切明显跳变）": 0.42,
    "标准（推荐）": 0.30,
    "灵敏（细微变化也切）": 0.20,
    "自定义": None,
}

_TIME_RE = re.compile(r"pts_time:([+-]?\d+(?:\.\d+)?)")
_SCORE_RE = re.compile(r"lavfi\.scene_score=([+-]?\d+(?:\.\d+)?)")


def parse_scene_metadata(output: str) -> list[dict]:
    """解析 metadata=print 的相邻 pts_time / scene_score 输出。"""
    candidates = []
    pending_time = None
    for line in (output or "").splitlines():
        time_match = _TIME_RE.search(line)
        if time_match:
            pending_time = float(time_match.group(1))
        score_match = _SCORE_RE.search(line)
        if score_match and pending_time is not None:
            candidates.append({"time": pending_time, "score": float(score_match.group(1))})
            pending_time = None
    return candidates


def filter_scene_candidates(
    candidates: list[dict], *, duration: float, min_length: float = 0.8,
    edge_guard: float = 0.25, filter_flashes: bool = True,
    flash_window: float = 0.10,
) -> list[dict]:
    """过滤端点、单帧闪光和距离过近的切点，并在近邻中保留分数最高者。"""
    duration = max(0.0, float(duration))
    min_length = max(0.15, float(min_length))
    edge_guard = max(0.0, min(float(edge_guard), duration / 2 if duration else 0.0))
    rows = sorted(
        ({"time": float(row["time"]), "score": float(row.get("score", 0.0))}
         for row in candidates if "time" in row),
        key=lambda row: row["time"],
    )
    rows = [row for row in rows if edge_guard <= row["time"] <= duration - edge_guard]

    if filter_flashes and len(rows) > 1:
        # 单帧闪白/闪黑通常产生一进一出两个极近的高分点；成对剔除避免切出一帧碎片。
        flashed = set()
        for index in range(len(rows) - 1):
            if rows[index + 1]["time"] - rows[index]["time"] <= flash_window:
                flashed.update((index, index + 1))
        rows = [row for index, row in enumerate(rows) if index not in flashed]

    kept: list[dict] = []
    for row in rows:
        if not kept or row["time"] - kept[-1]["time"] >= min_length:
            kept.append(row)
        elif row["score"] > kept[-1]["score"]:
            kept[-1] = row

    # 最后一段也必须达到最短时长，否则丢掉末尾候选点。
    while kept and duration - kept[-1]["time"] < min_length:
        kept.pop()
    return kept


def detect_scene_changes(
    video_path: str, *, ffmpeg_path: str, source_start: float,
    source_end: float, threshold: float = 0.30, min_length: float = 0.8,
    filter_flashes: bool = True, timeout: int = 1200,
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict]:
    """检测裁剪源区间内的镜头切点，返回相对于该区间起点的时间和分数。"""
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"视频文件不存在：{video_path}")
    source_start = max(0.0, float(source_start))
    source_end = max(source_start, float(source_end))
    duration = source_end - source_start
    if duration < max(0.3, min_length * 2):
        return []
    threshold = max(0.05, min(0.90, float(threshold)))

    # metadata 会打印 select 命中的帧时间与 scene score；不需要输出实际视频。
    video_filter = f"select=gte(scene\\,{threshold:.4f}),metadata=print"
    command = [
        ffmpeg_path, "-hide_banner", "-nostats", "-ss", f"{source_start:.6f}",
        "-t", f"{duration:.6f}", "-i", str(path), "-map", "0:v:0",
        "-vf", video_filter, "-an", "-sn", "-dn", "-f", "null", "-",
    ]
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL)
    elapsed = 0.0
    while True:
        try:
            stdout, stderr = process.communicate(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            elapsed += 0.25
            if cancel_check is not None and cancel_check():
                process.terminate()
                stdout, stderr = process.communicate(timeout=3)
                raise RuntimeError("场景检测已取消")
            if elapsed >= timeout:
                process.kill()
                stdout, stderr = process.communicate()
                raise TimeoutError("场景检测超时")
    output = (stdout + b"\n" + stderr).decode("utf-8", errors="replace")
    if process.returncode != 0:
        raise RuntimeError(f"场景检测失败：{output[-700:]}")
    rows = parse_scene_metadata(output)
    return filter_scene_candidates(
        rows, duration=duration, min_length=min_length,
        filter_flashes=filter_flashes)
