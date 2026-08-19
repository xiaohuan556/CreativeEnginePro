"""Fast local quality checks for generated storyboard candidates.

These checks deliberately reject only objective technical failures.  Identity
and scene matching still require a vision model or the user's final approval;
the report makes that distinction explicit instead of claiming a false pass.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any


def _ratio_value(value: str) -> float:
    try:
        left, right = str(value).split(":", 1)
        return float(left) / float(right)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _average_hash(image, size: int = 8) -> int:
    from PIL import Image
    sample = image.convert("L").resize((size, size), Image.Resampling.LANCZOS)
    getter = getattr(sample, "get_flattened_data", sample.getdata)
    values = list(getter())
    average = sum(values) / max(1, len(values))
    result = 0
    for value in values:
        result = (result << 1) | int(value >= average)
    return result


def inspect_image(path: str, expected_ratio: str = "",
                  reference_assets: list[dict[str, Any]] | None = None) -> dict:
    checks: list[dict[str, Any]] = []
    problems: list[str] = []
    warnings: list[str] = []
    width = height = 0
    image_hash = 0
    try:
        from PIL import Image, ImageStat
        with Image.open(path) as image:
            image.load()
            width, height = image.size
            if width < 512 or height < 512:
                problems.append(f"分辨率过低：{width}×{height}")
            expected = _ratio_value(expected_ratio)
            actual = width / max(1, height)
            if expected and abs(actual - expected) / expected > 0.13:
                problems.append(
                    f"画幅不符：期望 {expected_ratio}，实际约 {width}:{height}")
            gray = image.convert("L")
            deviation = float(ImageStat.Stat(gray).stddev[0])
            if not math.isfinite(deviation) or deviation < 3.0:
                problems.append("画面接近纯色或内容为空")
            elif deviation < 10.0:
                warnings.append("画面对比度很低，请确认是否符合镜头要求")
            image_hash = _average_hash(image)
            checks.extend([
                {"name": "文件可读", "passed": True},
                {"name": "分辨率", "passed": width >= 512 and height >= 512},
                {"name": "画面非空", "passed": deviation >= 3.0},
            ])
    except Exception as error:
        problems.append(f"图片文件损坏或无法读取：{str(error)[:80]}")
        checks.append({"name": "文件可读", "passed": False})

    roles = []
    for item in reference_assets or []:
        role = str(item.get("role") or "reference")
        if role not in roles and role != "composition":
            roles.append(role)
    manual_labels = {
        "scene": "场景结构与灯光",
        "character": "主体身份、服装和比例",
        "element": "指定元素与位置",
        "style": "画面风格",
        "reference": "参考内容",
    }
    manual_checks = [manual_labels.get(role, role) for role in roles]
    status = (
        "reject" if problems else
        "warn" if warnings else
        "pending" if manual_checks else
        "pass"
    )
    summary = (
        "技术检查失败" if status == "reject" else
        "技术检查通过 · 待确认绑定内容" if manual_checks else
        "技术检查通过"
    )
    return {
        "status": status,
        "summary": summary,
        "problems": problems,
        "warnings": warnings,
        "manual_checks": manual_checks,
        "checks": checks,
        "width": width,
        "height": height,
        "average_hash": image_hash,
        "path": str(Path(path)),
    }


def inspect_candidate_group(paths: list[str], expected_ratio: str = "",
                            reference_assets: list[dict[str, Any]] | None = None,
                            ) -> dict[str, dict]:
    reports = {
        path: inspect_image(path, expected_ratio, reference_assets)
        for path in paths if path and os.path.exists(path)
    }
    valid = [(path, report.get("average_hash", 0))
             for path, report in reports.items() if report.get("average_hash")]
    for index, (path, image_hash) in enumerate(valid):
        for other_path, other_hash in valid[:index]:
            if (image_hash ^ other_hash).bit_count() <= 2:
                warning = f"与候选 {Path(other_path).name} 几乎重复"
                reports[path].setdefault("warnings", []).append(warning)
                if reports[path].get("status") == "pass":
                    reports[path]["status"] = "warn"
                    reports[path]["summary"] = "技术检查通过 · 候选近似重复"
                break
    return reports
