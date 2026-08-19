"""Pure helpers for panel-first motion storyboard production.

Image models generate one clean delivery-ratio panel at a time.  The application owns
labels, layout and QC, so generated pixels are never asked to be a contact
sheet, a diagram and a continuity frame at the same time.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from core.image_output_size import aspect_ratio_value, normalize_aspect_ratio


def _compact(value, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[:max(0, limit - 1)].rstrip() + "…"


def _number(value, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return result if result == result and abs(result) != float("inf") else default


def _dict_rows(value) -> list[dict]:
    return [row for row in value if isinstance(row, dict)] if isinstance(
        value, (list, tuple)) else []


def motion_panel_prompt(shot: dict, shot_index: int, frame_index: int,
                        bible: str, *, provider_name: str = "",
                        redraw: bool = False,
                        aspect_ratio: str = "16:9") -> str:
    """Compile a concise prompt for one clean, native-ratio panel."""
    frames = _dict_rows(shot.get("motion_keyframes"))
    if not frames:
        raise ValueError("运动分镜缺少关键帧合同")
    frame_index = max(0, min(int(frame_index), len(frames) - 1))
    frame = frames[frame_index]
    first, last = frame_index == 0, frame_index == len(frames) - 1
    job = "动作即将开始" if first else "动作刚完成" if last else "动作正在明确推进"
    authority = (shot.get("frame_start") if first else
                 shot.get("frame_end") if last else
                 frame.get("character_state") or frame.get("composition"))
    invariants = [
        _compact(value, 120) for value in (shot.get("continuity_invariants") or [])
        if str(value or "").strip()
    ][:3]
    proxy = shot.get("scene_proxy") if isinstance(shot.get("scene_proxy"), dict) else {}
    fixtures = [
        str(row.get("name") or row.get("id") or "").strip()
        for row in _dict_rows(proxy.get("fixtures"))
    ]
    fixtures = [value for value in fixtures if value][:6]
    continuation = (
        "参考图1是上一动作画格：保持其脸、服装、镜头、背景、光线和固定物完全一致，"
        "只推进下面指定的一个动作状态。" if frame_index else
        "参考图1是本镜构图权威图：继承机位、透视、人物起始站位和固定物位置。")
    provider_rule = (
        "使用简洁自然语言所描述的可见事实，不自行补充剧情。" if provider_name == "seedream"
        else "严格保持各参考资产在身份表中的独立用途，不融合不同参考的对象。")
    aspect = normalize_aspect_ratio(aspect_ratio)
    ratio_value = aspect_ratio_value(aspect)
    orientation = "竖向" if ratio_value < .95 else "方形" if ratio_value <= 1.05 else "横向"
    framing_rule = (
        "按竖屏重新构图：人物、动作终点和关键道具保持在竖向叙事带内，利用前后景深度，"
        "不得把横屏参考图居中裁成竖屏。" if ratio_value < 1 else
        "按方形画幅重新平衡人物和关键道具，不得截断动作终点。" if ratio_value == 1 else
        "按横屏画幅保留动作方向所需的前导空间。")
    fixture_rule = (
        f"固定设施：{'、'.join(fixtures)}；每种设施在本画格中数量不增不减、位置不变。"
        if fixtures else "固定设施的数量、尺度和位置服从构图与场景参考图。")
    return (
        f"{'重新绘制' if redraw else '绘制'}第 {shot_index + 1} 镜的独立运动分镜画格 "
        f"K{int(_number(frame.get('index'), frame_index + 1))}，"
        f"时间 {_number(frame.get('time_seconds')):g} 秒。\n"
        f"输出一张完整的原生 {aspect} {orientation}画面；不是拼图，不画其他时间点，"
        f"不画边框、编号、文字或箭头。{framing_rule}\n"
        f"景别与机位：{_compact(shot.get('shot_size'), 50)}；"
        f"{_compact(shot.get('camera_position') or shot.get('camera_slot'), 120)}；"
        f"镜头状态={_compact(frame.get('camera_state'), 100)}；"
        f"轴线与屏幕方向={_compact(shot.get('axis_rule'), 100)} / "
        f"{_compact(frame.get('screen_direction'), 60)}。\n"
        f"画格任务：{job}。人物与物体当前状态：{_compact(authority, 220)}。"
        f"可见姿势/动作：{_compact(frame.get('action'), 180)}。"
        f"构图：{_compact(frame.get('composition'), 220)}。"
        f"视线：{_compact(frame.get('gaze_arrow'), 80)}。\n"
        f"空间：{_compact(shot.get('spatial_layout'), 240)}。"
        f"前景={_compact(shot.get('foreground'), 80)}；"
        f"中景={_compact(shot.get('midground'), 100)}；"
        f"后景={_compact(shot.get('background'), 100)}。{fixture_rule}\n"
        f"连续性：{'；'.join(invariants) if invariants else '身份、服装、场景结构、光线方向保持不变'}。"
        "只有本动作明确涉及的人体、服装或道具可以改变状态；被移动的原物体离开原位，不能复制。\n"
        f"{continuation}{provider_rule}\n"
        f"统一视觉设定：{_compact(bible, 520)}。"
        "清晰黑白铅笔分镜线稿，灰阶明暗块，透视明确。无字幕、无水印、无额外人物或额外家具。"
    )


def motion_panels_ready(shot: dict, aspect_ratio: str | None = None) -> bool:
    frames = _dict_rows(shot.get("motion_keyframes"))
    raw_paths = shot.get("motion_panel_paths")
    paths = list(raw_paths) if isinstance(raw_paths, (list, tuple)) else []
    aspect_matches = True
    if aspect_ratio is not None:
        stored_aspect = str(shot.get("motion_board_aspect_ratio") or "").strip()
        aspect_matches = bool(stored_aspect) and (
            normalize_aspect_ratio(stored_aspect) ==
            normalize_aspect_ratio(aspect_ratio))
    return bool(aspect_matches and 3 <= len(frames) <= 6 and
                len(paths) == len(frames) and
                all(path and os.path.exists(str(path)) for path in paths))


def assemble_motion_storyboard(panel_paths: list[str], frames: list[dict],
                               output_dir: str | Path, *, shot_id: str,
                               contract_version: int,
                               aspect_ratio: str = "16:9") -> str:
    """Compose clean model outputs into a labelled contact sheet locally."""
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    paths = [str(value) for value in panel_paths]
    if not 3 <= len(paths) <= 6 or len(paths) != len(frames):
        raise ValueError("运动画格数量与关键帧合同不一致")
    if not all(os.path.exists(path) for path in paths):
        raise OSError("运动画格文件不完整")
    columns, rows = (2, 2) if len(paths) <= 4 else (3, 2)
    aspect = normalize_aspect_ratio(aspect_ratio)
    ratio = aspect_ratio_value(aspect)
    long_edge = 800 if columns == 2 else 640
    if ratio >= 1:
        panel_w, panel_h = long_edge, round(long_edge / ratio)
    else:
        panel_w, panel_h = round(long_edge * ratio), long_edge
    header_h, gap, margin = 44, 18, 24
    width = margin * 2 + columns * panel_w + (columns - 1) * gap
    height = margin * 2 + rows * (header_h + panel_h) + (rows - 1) * gap
    board = Image.new("RGB", (width, height), (15, 16, 20))
    draw = ImageDraw.Draw(board)
    font = None
    for candidate in (
            "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(candidate):
            try:
                font = ImageFont.truetype(candidate, 22)
                break
            except OSError:
                pass
    font = font or ImageFont.load_default()
    for index, (path, frame) in enumerate(zip(paths, frames)):
        column, row = index % columns, index // columns
        left = margin + column * (panel_w + gap)
        top = margin + row * (header_h + panel_h + gap)
        label = (f"K{int(_number(frame.get('index'), index + 1))}  "
                 f"{_number(frame.get('time_seconds')):g}s  "
                 f"{_compact(frame.get('label') or frame.get('action'), 24)}")
        draw.rounded_rectangle(
            (left, top, left + panel_w, top + header_h + panel_h), radius=8,
            fill=(29, 31, 38), outline=(93, 104, 128), width=2)
        draw.text((left + 14, top + 10), label, fill=(230, 233, 240), font=font)
        with Image.open(path) as source:
            panel = ImageOps.fit(
                source.convert("RGB"), (panel_w, panel_h),
                method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        board.paste(panel, (left, top + header_h))
    signature = []
    for path in paths:
        stat = os.stat(path)
        signature.append(f"{os.path.abspath(path)}:{stat.st_mtime_ns}:{stat.st_size}")
    digest = hashlib.sha1(
        ("|".join(signature) + f"|v{contract_version}|{aspect}").encode("utf-8")
    ).hexdigest()[:14]
    folder = Path(output_dir); folder.mkdir(parents=True, exist_ok=True)
    output = folder / f"motion_board_{shot_id or 'shot'}_{digest}.png"
    board.save(output, "PNG")
    return str(output)


def inspect_motion_panels(panel_paths: list[str], shot: dict,
                          aspect_ratio: str = "16:9") -> dict:
    """Check native ratio, visible progression and fixed-scene geometry."""
    try:
        from PIL import Image, ImageChops, ImageStat
    except ImportError:
        return {"status":"unavailable", "issues":["MOTION_PANEL_QC_UNAVAILABLE"]}
    frames = _dict_rows(shot.get("motion_keyframes"))
    paths = [str(value) for value in panel_paths]
    if len(paths) != len(frames) or not 3 <= len(paths) <= 6:
        return {"status":"fail", "issues":["MOTION_PANEL_COUNT_MISMATCH"]}
    aspect = normalize_aspect_ratio(aspect_ratio)
    expected_ratio = aspect_ratio_value(aspect)
    panels, ratios, issues = [], [], []
    try:
        for path in paths:
            with Image.open(path) as image:
                width, height = image.size
                if width < 900 or height < 500:
                    issues.append("MOTION_PANEL_RESOLUTION_LOW")
                ratio = width / max(1, height)
                ratios.append(round(ratio, 4))
                if abs(ratio - expected_ratio) > max(.045, expected_ratio * .055):
                    issues.append("MOTION_PANEL_ASPECT_MISMATCH")
                sample_h = max(96, round(240 / expected_ratio))
                panels.append(image.convert("RGB").resize((240, sample_h)).convert("L"))
    except (OSError, ValueError):
        return {"status":"fail", "issues":["MOTION_PANEL_READ_FAILED"]}
    deltas = [
        round(ImageStat.Stat(ImageChops.difference(left, right)).mean[0] / 255.0, 5)
        for left, right in zip(panels, panels[1:])
    ]
    if deltas and max(deltas) < .025:
        issues.append("MOTION_PANELS_NEAR_DUPLICATE")
    if deltas and sum(delta < .018 for delta in deltas) >= max(2, len(deltas) - 1):
        issues.append("MOTION_PROGRESS_INVISIBLE")
    spatial_checks = []
    try:
        from ai.deterministic_qc import compare_fixed_regions
        from ai.scene_geometry import fixture_view_bboxes
        protected = fixture_view_bboxes(
            shot.get("scene_proxy") or {}, str(shot.get("scene_view_id") or "master"))
        for left, right in zip(paths, paths[1:]):
            check = compare_fixed_regions(
                left, right, editable_bbox=shot.get("editable_bbox_xy"),
                protected_bboxes=protected, threshold=.48)
            spatial_checks.append(check)
            if check.get("status") == "fail":
                issues.append("MOTION_FIXED_SCENE_DRIFT")
    except (ImportError, OSError, TypeError, ValueError):
        spatial_checks.append({"status":"unavailable"})
    issues = list(dict.fromkeys(issues))
    return {
        "status":"fail" if issues else "pass", "issues":issues,
        "expected_aspect_ratio":aspect, "panel_aspect_ratios":ratios,
        "adjacent_visual_deltas":deltas,
        "spatial_checks":spatial_checks,
    }
