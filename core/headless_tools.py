"""Local desktop tools, adapted from the Web migration's headless adapters.

No Qt application, Web server or model service is needed. The public Agent
entry point is ``execute_desktop_tool``; ``execute`` retains the source adapter's
``(paths, metadata)`` contract. Progress uses integer percentages, 0..100.
"""
from __future__ import annotations

import math
import random
import re
import shutil
from pathlib import Path
from typing import Callable

from core.headless_support import DesktopToolCancelled, check_cancel


Progress = Callable[[int], None]
Cancelled = Callable[[], bool]


CAPABILITIES = {
    "image_batch": {
        "label": "图片批处理",
        "operations": ["watermark_fixed", "superres", "denoise", "resize", "rename", "convert", "compress"],
    },
    "audio_separate": {"label": "人声/背景声分离", "outputs": ["vocals", "accompaniment"]},
    "slideshow": {
        "label": "图片轮播",
        "features": [
            "transition", "random_transition", "bgm", "random_bgm",
            "shuffle_cycle", "pure_random", "outro", "batch",
        ],
    },
    "video_mix": {"label": "批量混剪", "modes": ["A", "B", "C", "D", "E", "F"]},
}


def _check_cancel(cancelled: Cancelled) -> None:
    check_cancel(cancelled)


def _output_path(folder: Path, stem: str, suffix: str) -> Path:
    """Never overwrite a source, earlier batch result or previous task output."""
    if not stem or stem in {".", ".."} or re.search(r'[<>:"/\\|?*\x00-\x1f]', stem):
        raise ValueError("输出名称不能包含路径或特殊字符")
    target = folder / f"{stem}{suffix}"
    index = 2
    while target.exists():
        target = folder / f"{stem}-{index}{suffix}"
        index += 1
    return target


def _number(text: str, pattern: str, default: float) -> float:
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return default
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return default


def _ratio_resolution(text: str, default: str = "1080x1920") -> str:
    compact = text.replace(" ", "")
    explicit = re.search(r"(\d{2,4})\s*[x×*]\s*(\d{2,4})", compact, re.IGNORECASE)
    if explicit:
        return f"{int(explicit.group(1))}x{int(explicit.group(2))}"
    for ratio, resolution in {
        "9:16": "1080x1920", "16:9": "1920x1080", "1:1": "1080x1080", "4:5": "1080x1350",
    }.items():
        if ratio in compact:
            return resolution
    return default


def classify_instruction(instruction: str, references: list[dict]) -> str:
    """Return a specialized desktop action, or an empty string for timeline edit."""
    text = str(instruction or "").strip()
    compact = re.sub(r"\s+", "", text)
    kinds = {str(item.get("kind") or "") for item in references}
    image_count = sum(1 for item in references if str(item.get("kind") or "") == "image")
    image_batch_request = re.search(
            r"批量.{0,8}(?:处理|重命名|改名|压缩|转换|修改|调整|缩放|裁剪|改)(?:这些|所有|全部|多张)?"
            r"(?:图片|图像|照片)?(?:的)?(?:大小|尺寸|分辨率|比例)?|"
            r"(?:图片|图像|照片).{0,12}(?:批量重命名|批量修改|批量调整|批量缩放|批量裁剪|格式转换|压缩|去噪|"
            r"改尺寸|调整尺寸|修改尺寸|改大小|调整大小|修改大小|改比例)|"
            r"(?:多张|所有|全部)(?:图片|图像|照片).{0,10}(?:改|修改|调整|缩放|裁剪).{0,6}"
            r"(?:大小|尺寸|分辨率|比例)|"
            r"(?:多张|所有|全部)?(?:图片|图像|照片).{0,8}(?:大小|尺寸|分辨率|比例).{0,8}"
            r"(?:统一|批量|修改|调整|改成|变为)", compact)
    explicit_multi_resize = image_count >= 2 and re.search(
        r"(?:改成|修改为|调整为|统一为|缩放到|裁剪为).{0,8}\d{2,5}[x×*]\d{2,5}", compact, re.IGNORECASE,
    )
    if "image" in kinds and (image_batch_request or explicit_multi_resize):
        return "image_batch"
    timed_images = re.findall(
        r"@?(?:图片|图像|照片|图)[0-9一二两三四五六七八九十]+(?:出现|显示|展示|停留|播放)"
        r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百点半]+)秒",
        compact,
    )
    if "image" in kinds and (
            re.search(r"图片轮播|照片轮播|相册视频|图片(?:做成|生成)视频|轮播视频|按顺序轮播|依次轮播", compact)
            or len(timed_images) >= 2):
        return "slideshow"
    if "video" in kinds and re.search(r"批量混剪|随机混剪|视频混剪|随机拼接|顺序片头模式|模式[ABCDEFＡＢＣＤＥＦ]", compact, re.IGNORECASE):
        return "video_mix"
    if kinds.intersection({"video", "audio"}) and re.search(
            r"分离(?:人声|背景声|背景音乐|伴奏)|提取(?:人声|伴奏)|人声(?:和|与|、)背景(?:声|音乐)分开", compact):
        return "audio_separate"
    return ""


_CHINESE_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
                   "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _spoken_number(value: str) -> float | None:
    """Parse the small Arabic/Chinese numbers used in slideshow durations."""
    raw = str(value or "").strip()
    try:
        return float(raw)
    except ValueError:
        pass
    if raw == "半":
        return 0.5
    if "点" in raw:
        whole, fraction = raw.split("点", 1)
        whole_value = _spoken_number(whole) if whole else 0.0
        if whole_value is None or not fraction or any(char not in _CHINESE_DIGITS for char in fraction):
            return None
        return whole_value + float("0." + "".join(str(_CHINESE_DIGITS[char]) for char in fraction))
    if not raw or any(char not in _CHINESE_DIGITS and char not in {"十", "百"} for char in raw):
        return None
    total = 0
    current = 0
    for char in raw:
        if char in _CHINESE_DIGITS:
            current = _CHINESE_DIGITS[char]
        elif char == "十":
            total += (current or 1) * 10
            current = 0
        elif char == "百":
            total += (current or 1) * 100
            current = 0
    return float(total + current)


def slideshow_schedule(instruction: str, image_sources: list[dict]) -> list[tuple[Path, float]]:
    """Resolve explicit ``@图片N 出现 X 秒`` entries in the written order."""
    pattern = re.compile(
        r"@?\s*(?:图片|图像|照片|图)\s*([0-9一二两三四五六七八九十]+)\s*"
        r"(?:出现|显示|展示|停留|播放)\s*"
        r"([0-9]+(?:\.[0-9]+)?|[零〇一二两三四五六七八九十百点半]+)\s*秒",
        re.IGNORECASE,
    )
    records: list[tuple[Path, str]] = []
    for item in image_sources:
        path = Path(str(item.get("path") or item.get("local_path") or ""))
        if path.is_file():
            records.append((path, re.sub(r"\s+", "", str(item.get("alias") or "")).lstrip("@")))
    schedule: list[tuple[Path, float]] = []
    for match in pattern.finditer(str(instruction or "")):
        ordinal_value = _spoken_number(match.group(1))
        duration = _spoken_number(match.group(2))
        if ordinal_value is None or duration is None or duration <= 0:
            continue
        ordinal = int(ordinal_value)
        requested_aliases = {f"图片{match.group(1)}", f"图像{match.group(1)}", f"照片{match.group(1)}", f"图{match.group(1)}",
                             f"图片{ordinal}", f"图像{ordinal}", f"照片{ordinal}", f"图{ordinal}"}
        source = next((path for path, alias in records if alias in requested_aliases), None)
        if source is None and 1 <= ordinal <= len(records):
            source = records[ordinal - 1][0]
        if source is not None:
            schedule.append((source, max(0.1, min(600.0, duration))))
    return schedule


def slideshow_mentioned_image_order(instruction: str, image_sources: list[dict]) -> list[Path]:
    """Resolve explicitly mentioned images in written order, without repeats."""
    records: list[tuple[Path, str]] = []
    for item in image_sources:
        path = Path(str(item.get("path") or item.get("local_path") or ""))
        if path.is_file():
            records.append((path, re.sub(r"\s+", "", str(item.get("alias") or "")).lstrip("@")))
    pattern = re.compile(
        r"@?\s*(?:图片|图像|照片|图)\s*([0-9一二两三四五六七八九十]+)",
        re.IGNORECASE,
    )
    ordered: list[Path] = []
    for match in pattern.finditer(str(instruction or "")):
        ordinal_value = _spoken_number(match.group(1))
        if ordinal_value is None:
            continue
        ordinal = int(ordinal_value)
        requested_aliases = {
            f"图片{match.group(1)}", f"图像{match.group(1)}", f"照片{match.group(1)}", f"图{match.group(1)}",
            f"图片{ordinal}", f"图像{ordinal}", f"照片{ordinal}", f"图{ordinal}",
        }
        source = next((path for path, alias in records if alias in requested_aliases), None)
        if source is None and 1 <= ordinal <= len(records):
            source = records[ordinal - 1][0]
        if source is not None and source not in ordered:
            ordered.append(source)
    return ordered


def has_explicit_slideshow_schedule(instruction: str) -> bool:
    """Whether the command requires the v3 per-image-duration behavior."""
    return len(re.findall(
        r"@?\s*(?:图片|图像|照片|图)\s*[0-9一二两三四五六七八九十]+\s*"
        r"(?:出现|显示|展示|停留|播放)\s*"
        r"(?:[0-9]+(?:\.[0-9]+)?|[零〇一二两三四五六七八九十百点半]+)\s*秒",
        str(instruction or ""), re.IGNORECASE,
    )) >= 1


def has_explicit_slideshow_packaging(instruction: str) -> bool:
    """Whether slideshow videos have explicitly assigned watermark/outro roles."""
    return bool(re.search(r"(?:透明)?水印|尾页|片尾", str(instruction or ""), re.IGNORECASE))


def resolve_slideshow_video_roles(instruction: str, video_sources: list[dict]) -> dict[str, dict | None]:
    """Resolve connected videos as overlays or outros from roles and @mentions.

    A watermark is composited over the slideshow; an outro is appended after it.
    This must not depend on connection order because both inputs are videos.
    """
    compact = re.sub(r"\s+", "", str(instruction or "")).lower()
    records = []
    for item in video_sources:
        path = Path(str(item.get("path") or item.get("local_path") or ""))
        if not path.is_file():
            continue
        aliases = {
            re.sub(r"[\s@]", "", str(value or "")).lower()
            for value in (item.get("alias"), item.get("label"))
            if str(value or "").strip()
        }
        records.append((item, aliases, str(item.get("role") or "").strip().lower()))

    def explicit(role: str, labels: tuple[str, ...]) -> dict | None:
        role_matches = [item for item, _aliases, item_role in records if item_role == role]
        if len(role_matches) == 1:
            return role_matches[0]
        label_group = "|".join(re.escape(label) for label in labels)
        matches: list[tuple[int, dict]] = []
        for item, aliases, _item_role in records:
            for alias in aliases:
                escaped = re.escape(alias)
                patterns = (
                    rf"@?{escaped}(?:是|就是|作为|当作|用作)?(?:成品|透明)?(?:{label_group})(?:视频|素材|动画)?",
                    rf"(?:{label_group})(?:视频|素材|动画)?(?:是|用|使用|采用|选择|指定为|[:：])?@?{escaped}",
                )
                positions = [match.start() for pattern in patterns for match in re.finditer(pattern, compact)]
                if positions:
                    matches.append((min(positions), item))
        return sorted(matches, key=lambda row: row[0])[0][1] if matches else None

    watermark = explicit("watermark_overlay", ("水印", "透明水印"))
    outro = explicit("outro", ("尾页", "片尾", "成品尾页"))
    # Preserve the former one-video convenience only when no explicit role was
    # written.  Never silently turn the remaining video into an outro once the
    # user has assigned either packaging role.
    if watermark is None and outro is None and records:
        outro = records[-1][0]
    return {"watermark": watermark, "outro": outro}


def resolve_slideshow_audio(instruction: str, audio_sources: list[dict]) -> Path | None:
    """Select a connected audio source from an explicit @音频N mention."""
    compact = re.sub(r"\s+", "", str(instruction or "")).lower()
    matches: list[tuple[int, Path]] = []
    for item in audio_sources:
        path = Path(str(item.get("path") or item.get("local_path") or ""))
        if not path.is_file():
            continue
        for value in (item.get("alias"), item.get("label")):
            alias = re.sub(r"[\s@]", "", str(value or "")).lower()
            if not alias:
                continue
            for match in re.finditer(r"@" + re.escape(alias), compact):
                matches.append((match.start(), path))
    return sorted(matches, key=lambda row: row[0])[0][1] if matches else None


def _image_pipeline_context(instruction: str, params: dict) -> tuple[list[str], dict]:
    text = str(instruction or "")
    compact = re.sub(r"\s+", "", text)
    operations: list[str] = []
    if re.search(r"去水印|消除水印|固定蒙版", compact):
        operations.append("watermark_fixed")
    if re.search(r"高清化|超分|放大.*清晰|清晰度", compact):
        operations.append("superres")
    if re.search(r"去噪|降噪", compact):
        operations.append("denoise")
    if re.search(
            r"改(?:图片|图像|照片)?(?:的)?(?:大小|尺寸|分辨率|比例)|"
            r"(?:修改|调整)(?:图片|图像|照片)?(?:的)?(?:大小|尺寸|分辨率|比例)|"
            r"缩放|裁剪|\d{2,5}[x×*]\d{2,5}|(?:9:16|16:9|1:1|4:5)|横屏|竖屏|方形|正方形",
            compact,
    ):
        operations.append("resize")
    if re.search(r"重命名|改名|命名", compact):
        operations.append("rename")
    if re.search(r"格式转换|转成|导出为|输出为|\b(?:jpg|jpeg|png|webp)\b", text, re.IGNORECASE):
        operations.append("convert")
    if re.search(r"压缩|画质|质量", compact):
        operations.append("compress")
    if not operations:
        raise ValueError("没有识别到图片批处理步骤；可用：去水印、高清化、去噪、改尺寸、重命名、格式转换、压缩")

    ctx: dict = {}
    if "watermark_fixed" in operations:
        ctx["watermark"] = {
            "width": float(params.get("erase_width") or 0.25),
            "height": float(params.get("erase_height") or 0.15),
            "margin_right": max(0.0, 1.0 - float(params.get("erase_x") or 0.72) - float(params.get("erase_width") or 0.25)),
            "margin_bottom": max(0.0, 1.0 - float(params.get("erase_y") or 0.82) - float(params.get("erase_height") or 0.15)),
        }
    if "denoise" in operations:
        ctx["denoise"] = {"enabled": True, "strength": int(_number(text, r"去噪(?:强度)?[^\d]{0,6}(\d+)", 5))}
    if "resize" in operations:
        explicit = re.search(r"(\d{2,5})\s*[x×*]\s*(\d{2,5})", text, re.IGNORECASE)
        if not explicit:
            explicit = re.search(
                r"(?:宽|宽度)\s*(\d{2,5})\s*(?:像素|px)?[^\d]{0,10}(?:高|高度)\s*(\d{2,5})",
                text, re.IGNORECASE,
            )
        ratio = next((value for value in ("9:16", "16:9", "1:1", "4:5") if value in compact), "")
        if not ratio:
            ratio = "16:9" if re.search(r"横屏|横版|宽屏", compact) else ""
        if not ratio:
            ratio = "9:16" if re.search(r"竖屏|竖版|手机屏", compact) else ""
        if not ratio:
            ratio = "1:1" if re.search(r"方形|正方形", compact) else ""
        scale = _number(text, r"(?:放大|缩小|缩放)[^\d]{0,5}(\d+(?:\.\d+)?)\s*倍", 0)
        configured_width = int(params.get("image_width") or params.get("width") or 0)
        configured_height = int(params.get("image_height") or params.get("height") or 0)
        configured_scale = float(params.get("image_scale") or 0)
        if explicit:
            ctx["resize"] = {"enabled": True, "mode": "size", "width": int(explicit.group(1)), "height": int(explicit.group(2))}
        elif configured_width > 0 and configured_height > 0:
            ctx["resize"] = {"enabled": True, "mode": "size", "width": configured_width, "height": configured_height}
        elif ratio:
            ctx["resize"] = {"enabled": True, "mode": "preset", "preset": ratio}
        elif scale > 0 or configured_scale > 0:
            ctx["resize"] = {"enabled": True, "mode": "scale", "scale": scale or configured_scale}
        else:
            raise ValueError(
                "已识别为桌面端批量修改图片尺寸，但没有找到目标尺寸。"
                "请写成 1080×1920、宽1080高1920、9:16/16:9/1:1/4:5，或“缩放2倍”"
            )
    if "rename" in operations:
        rename = re.search(r"(?:重命名|改名|命名)(?:为|成|：|:)?\s*([^，。；;\n]{1,50})", text)
        pattern = str(params.get("rename_pattern") or (rename.group(1).strip() if rename else "{name}_{num}"))
        if "{" not in pattern:
            pattern += "_{num}"
        ctx["rename"] = {"pattern": pattern}
    if "convert" in operations:
        fmt = re.search(r"\b(jpe?g|png|webp)\b", text, re.IGNORECASE)
        ctx["convert"] = {"format": (fmt.group(1).lower().replace("jpeg", "jpg") if fmt else str(params.get("image_format") or "png"))}
    if "compress" in operations:
        ctx["compress"] = {"quality": max(1, min(100, int(_number(text, r"(?:质量|画质|压缩)[^\d]{0,8}(\d+)\s*%?", float(params.get("image_quality") or 85)))))}
    return operations, ctx


def run_image_batch(sources: list[Path], output_dir: Path, instruction: str, params: dict,
                    cancelled: Cancelled, progress: Progress) -> tuple[list[Path], dict]:
    from PIL import Image
    import numpy as np
    from core.plugins import get_plugin

    operations, ctx = _image_pipeline_context(instruction, params)
    plugins = []
    for name in operations:
        plugin = get_plugin(name)
        if plugin is None:
            raise ValueError(f"本地图片插件不可用：{name}")
        if name == "superres":
            from core.headless_support import local_superres
            plugins.append(local_superres(cancelled))
        else:
            plugins.append(plugin())
    outputs: list[Path] = []
    failures: list[dict] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    for index, source in enumerate(sources):
        _check_cancel(cancelled)
        try:
            with Image.open(source) as image:
                array = np.array(image.convert("RGBA"))
            for plugin in plugins:
                _check_cancel(cancelled)
                before_shape = array.shape[:2]
                plugin_ctx = ctx
                if plugin.NAME == "resize" and ctx["resize"].get("preset") == "4:5":
                    # This desktop plugin predates the Web adapter's 4:5 preset.
                    height, width = array.shape[:2]
                    crop_width, crop_height = min(width, int(height * 4 / 5)), min(height, int(width * 5 / 4))
                    x, y = (width - crop_width) // 2, (height - crop_height) // 2
                    array = array[y:y + max(1, crop_height), x:x + max(1, crop_width)]
                    plugin_ctx = {**ctx, "resize": {"enabled": True, "mode": "size", "width": 1080, "height": 1350}}
                array = plugin.run(array, plugin_ctx)
                _check_cancel(cancelled)
                if getattr(plugin, "NAME", "") == "superres" and array.shape[:2] == before_shape:
                    raise ValueError("桌面超分模型未安装或未产生超分结果；需预置 ~/.cep_models/realesr-general-x4v3.onnx（不自动下载）")
            stem = source.stem
            rename = ctx.get("rename") or {}
            pattern = str(rename.get("pattern") or "")
            if pattern:
                stem = pattern.replace("{name}", source.stem).replace("{num}", f"{index + 1:04d}")
            fmt = str((ctx.get("convert") or {}).get("format") or source.suffix.lstrip(".") or "png").lower()
            fmt = "jpg" if fmt in {"jpg", "jpeg"} else fmt if fmt in {"png", "webp"} else "png"
            target = _output_path(output_dir, stem, f".{fmt}")
            image = Image.fromarray(array)
            if fmt == "jpg":
                image = image.convert("RGB")
            kwargs = {"quality": int((ctx.get("compress") or {}).get("quality", 95))} if fmt in {"jpg", "webp"} else {"optimize": True}
            _check_cancel(cancelled)
            image.save(target, format={"jpg": "JPEG", "png": "PNG", "webp": "WEBP"}[fmt], **kwargs)
            outputs.append(target)
        except Exception as error:  # one broken file must not stop the batch
            _check_cancel(cancelled)
            if isinstance(error, DesktopToolCancelled):
                raise
            failures.append({"source": source.name, "error": str(error)[:400]})
        progress(10 + round((index + 1) / max(1, len(sources)) * 85))
    if not outputs:
        raise ValueError("图片批处理全部失败：" + (failures[0]["error"] if failures else "没有可用图片"))
    return outputs, {"operation": "image_batch", "steps": operations, "failures": failures}


def run_audio_separate(sources: list[Path], output_dir: Path, cancelled: Cancelled,
                       progress: Progress) -> tuple[list[Path], dict]:
    from core.headless_support import local_separator
    import tempfile

    outputs: list[Path] = []
    failures: list[dict] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    for index, source in enumerate(sources):
        _check_cancel(cancelled)
        try:
            with tempfile.TemporaryDirectory(prefix=".separation-", dir=output_dir) as temporary:
                work = Path(temporary)
                separator = local_separator(cancelled, work)
                # Normalize to stereo first: the existing center-channel filter
                # addresses c1 and therefore cannot accept mono inputs directly.
                audio = separator.extract_audio(source, work / "full_audio.wav")
                vocals, accompaniment = separator.separate_vocals(audio, work)
                for path, suffix in ((Path(vocals), "_人声"), (Path(accompaniment), "_背景声")):
                    _check_cancel(cancelled)
                    target = _output_path(output_dir, source.stem + suffix, ".wav")
                    shutil.copy2(path, target)
                    outputs.append(target)
        except Exception as error:
            _check_cancel(cancelled)
            if isinstance(error, DesktopToolCancelled):
                raise
            failures.append({"source": source.name, "error": str(error)[:400]})
        progress(10 + round((index + 1) / max(1, len(sources)) * 85))
    if not outputs:
        raise ValueError("音频分离全部失败：" + (failures[0]["error"] if failures else "没有可用音频"))
    return outputs, {"operation": "audio_separate", "engine": "desktop-ffmpeg-separator",
                     "method": "center-channel-filter", "neural_separation": False,
                     "failures": failures}


def run_slideshow(images: list[Path], audios: list[Path], videos: list[Path], output_dir: Path,
                  instruction: str, params: dict, cancelled: Cancelled,
                  progress: Progress, image_sources: list[dict] | None = None,
                  video_sources: list[dict] | None = None,
                  audio_sources: list[dict] | None = None) -> tuple[list[Path], dict]:
    from core.headless_support import slideshow_engine

    engine = slideshow_engine(cancelled)
    TRANSITIONS = engine.TRANSITIONS
    mix_audio, package_slideshow_video, render_video = engine.mix_audio, engine.package_slideshow_video, engine.render_video

    if not images:
        raise ValueError("图片轮播至少需要连接 1 张图片")
    count = max(1, min(100, int(_number(instruction, r"(?:生成|输出|做)[^\d]{0,8}(\d+)\s*(?:条|个|支|段)", float(params.get("batch_count") or 1)))))
    per_video = max(1, min(100, int(_number(
        instruction, r"每(?:条|个|支)(?:视频)?[^\d]{0,8}(\d+)\s*张",
        float(params.get("images_per_video") or len(images)),
    ))))
    explicit_schedule = slideshow_schedule(instruction, image_sources or [
        {"path": str(path), "alias": f"图片{index + 1}"} for index, path in enumerate(images)
    ])
    mentioned_order = slideshow_mentioned_image_order(instruction, image_sources or [
        {"path": str(path), "alias": f"图片{index + 1}"} for index, path in enumerate(images)
    ])
    duration = (sum(item[1] for item in explicit_schedule) if explicit_schedule else
                max(1.0, min(600.0, _number(instruction, r"(?:视频时长|每条|成片|总时长)[^\d]{0,8}(\d+(?:\.\d+)?)\s*秒", float(params.get("target_duration") or 10)))))
    transition = next((name for name in TRANSITIONS if name in instruction), str(params.get("transition_type") or "推进放大"))
    random_transition = bool(
        params.get("random_transition")
        or re.search(r"随机(?:转场|过渡)|转场(?:随机|打乱)", instruction, re.IGNORECASE)
    )
    random_bgm = bool(
        params.get("random_bgm")
        or re.search(r"随机(?:BGM|bgm|音乐|配乐|背景音乐)|(?:BGM|bgm|音乐|配乐|背景音乐)(?:随机|打乱)", instruction, re.IGNORECASE)
    )
    ordered_requested = bool(params.get("sequential_mode") or re.search(r"按(?:照)?顺序|依次|逐张", instruction))
    sequential_mode = bool(explicit_schedule or ordered_requested)
    shuffle_mode = bool(params.get("shuffle_mode", True))
    if re.search(r"纯随机|独立随机|允许重复", instruction):
        shuffle_mode = False
    elif re.search(r"洗牌轮巡|轮巡|尽量不重复|不重复", instruction):
        shuffle_mode = True
    cfg = {
        "fps": max(12, min(60, int(_number(instruction, r"(\d+)\s*(?:fps|帧)", float(params.get("fps") or 30))))),
        "video_duration": duration,
        "resolution": _ratio_resolution(instruction, str(params.get("slideshow_resolution") or params.get("resolution") or "1080x1920")),
        "transition_frames": max(0, min(90, int(params.get("transition_frames", 15)))),
        "transition_type": transition,
        "random_transition": random_transition,
        "random_bgm": random_bgm,
        "shuffle_mode": shuffle_mode,
        "sequential_mode": sequential_mode,
        "video_quality": str(params.get("video_quality") or "minimal"),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    failures: list[dict] = []
    rng = random.Random(str(params.get("batch_seed") or instruction))
    shuffled_pool = list(images)
    rng.shuffle(shuffled_pool)
    shuffle_cursor = 0
    video_roles = resolve_slideshow_video_roles(
        instruction,
        video_sources or [
            {"path": str(path), "alias": f"视频{index + 1}", "kind": "video"}
            for index, path in enumerate(videos)
        ],
    )
    watermark_item = video_roles.get("watermark")
    outro_item = video_roles.get("outro")
    watermark = Path(str((watermark_item or {}).get("path") or (watermark_item or {}).get("local_path") or "")) if watermark_item else None
    outro = Path(str((outro_item or {}).get("path") or (outro_item or {}).get("local_path") or "")) if outro_item else None
    mentioned_audio = resolve_slideshow_audio(instruction, audio_sources or [
        {"path": str(path), "alias": f"音频{index + 1}", "kind": "audio"}
        for index, path in enumerate(audios)
    ])
    item_details: list[dict] = []

    class _CancelEvent:
        def is_set(self) -> bool:
            return bool(cancelled())

    stop_event = _CancelEvent()

    def select_images() -> list[Path]:
        nonlocal shuffled_pool, shuffle_cursor
        if explicit_schedule:
            return [path for path, _duration in explicit_schedule]
        if ordered_requested and mentioned_order:
            return mentioned_order[:per_video]
        if sequential_mode:
            return images[:per_video]
        if not shuffle_mode:
            return (rng.sample(images, per_video) if per_video <= len(images)
                    else rng.choices(images, k=per_video))
        chosen: list[Path] = []
        while len(chosen) < per_video:
            if shuffle_cursor >= len(shuffled_pool):
                shuffled_pool = list(images)
                rng.shuffle(shuffled_pool)
                shuffle_cursor = 0
            take = min(per_video - len(chosen), len(shuffled_pool) - shuffle_cursor)
            chosen.extend(shuffled_pool[shuffle_cursor:shuffle_cursor + take])
            shuffle_cursor += take
        return chosen

    for index in range(count):
        _check_cancel(cancelled)
        selected = select_images()
        item_transition = rng.choice(list(TRANSITIONS)) if random_transition else transition
        item_bgm = (rng.choice(audios) if random_bgm and len(audios) > 1
                    else mentioned_audio if mentioned_audio
                    else audios[min(max(0, int(params.get("bgm_selected") or 0)), len(audios) - 1)]
                    if audios else None)
        item_cfg = {
            **cfg,
            "transition_type": item_transition,
            **({"image_durations": [item[1] for item in explicit_schedule]} if explicit_schedule else {}),
        }
        final = _output_path(output_dir, f"slideshow-{index + 1:03d}", ".mp4")
        base = _output_path(output_dir, final.stem + "-base", ".mp4")
        with_audio = _output_path(output_dir, final.stem + "-audio", ".mp4")
        try:
            render_video([str(path) for path in selected], base, item_cfg, stop_event=stop_event,
                         progress_callback=lambda done, total: progress(10 + round((index + 0.7 * done / max(1, total)) / count * 85)))
            _check_cancel(cancelled)
            current = base
            if item_bgm:
                mix_audio(base, item_bgm, with_audio, int(params.get("bgm_volume", 80)),
                          stop_event=stop_event)
                _check_cancel(cancelled)
                current = with_audio
            packaging = []
            if watermark or outro:
                packaging = package_slideshow_video(
                    current, final, watermark_path=watermark, outro_path=outro,
                    opacity=float(params.get("watermark_opacity", 1)), stop_event=stop_event,
                )
                _check_cancel(cancelled)
            else:
                shutil.move(str(current), final)
            outputs.append(final)
            item_details.append({
                "index": index + 1,
                "images": [path.name for path in selected],
                "image_durations": list(item_cfg.get("image_durations") or []),
                "transition": item_transition,
                "bgm": item_bgm.name if item_bgm else "",
                "packaging": packaging,
                "watermark": watermark.name if watermark else "",
                "outro": outro.name if outro else "",
            })
        except Exception as error:
            final.unlink(missing_ok=True)
            _check_cancel(cancelled)
            if isinstance(error, DesktopToolCancelled):
                raise
            failures.append({"index": index + 1, "error": str(error)[:400]})
        finally:
            for temp in (base, with_audio):
                if temp != final:
                    temp.unlink(missing_ok=True)
        progress(10 + round((index + 1) / count * 85))
    if not outputs:
        raise ValueError("图片轮播全部失败：" + (failures[0]["error"] if failures else "未知错误"))
    return outputs, {"operation": "slideshow", "config": cfg,
                     "items": item_details, "failures": failures}


def run_video_mix(videos: list[Path], audios: list[Path], output_dir: Path,
                  instruction: str, params: dict, cancelled: Cancelled,
                  progress: Progress) -> tuple[list[Path], dict]:
    from core.mix_engine import ClipMaterial, MixMode, TaskGenerator
    from core.headless_support import local_mixer, slideshow_engine
    from utils.ffmpeg_utils import get_ffmpeg_path

    if not videos:
        raise ValueError("批量混剪至少需要连接 1 条视频")
    mode_letter = (re.search(r"模式\s*([A-FＡ-Ｆ])", instruction, re.IGNORECASE).group(1).upper()
                   if re.search(r"模式\s*([A-FＡ-Ｆ])", instruction, re.IGNORECASE)
                   else "F" if "顺序片头模式" in instruction else str(params.get("mix_mode") or "C").upper())
    fullwidth = {"Ａ": "A", "Ｂ": "B", "Ｃ": "C", "Ｄ": "D", "Ｅ": "E", "Ｆ": "F"}
    mode_letter = fullwidth.get(mode_letter, mode_letter)
    modes = {"A": MixMode.MODE_A, "B": MixMode.MODE_B, "C": MixMode.MODE_C,
             "D": MixMode.MODE_D, "E": MixMode.MODE_E, "F": MixMode.MODE_F}
    if mode_letter not in modes:
        raise ValueError("混剪模式必须是 A、B、C、D、E、F")
    mode = modes[mode_letter]
    count = max(1, min(100, int(_number(instruction, r"(?:生成|输出|混剪)[^\d]{0,8}(\d+)\s*(?:条|个|支|段)", float(params.get("batch_count") or 1)))))
    total = max(1.0, min(600.0, _number(instruction, r"(?:总时长|成片|每条)[^\d]{0,8}(\d+(?:\.\d+)?)\s*秒", float(params.get("target_duration") or 15))))
    get_video_duration = slideshow_engine(cancelled).get_video_duration
    materials = [ClipMaterial(str(path)) for path in videos]
    for material in materials:
        _check_cancel(cancelled)
        material._duration = get_video_duration(material.path)
        _check_cancel(cancelled)
    fixed = {"head": materials[0], "mid": materials[len(materials) // 2], "tail": materials[-1]}
    randoms = materials if mode == MixMode.MODE_C else materials[1:-1] or materials
    suffix = materials[1:] if mode == MixMode.MODE_F else []
    if mode == MixMode.MODE_F:
        randoms = materials[:1]
        total = min(4.0, materials[0].usable_duration) + sum(item.usable_duration for item in suffix)
    generator = TaskGenerator(mode, fixed, randoms, total=total, suffix_materials=suffix)
    tasks = generator.generate(count)
    width, height = map(int, _ratio_resolution(instruction, str(params.get("resolution") or "1080x1920")).split("x"))
    if width <= 0 or height <= 0 or width % 2 or height % 2:
        raise ValueError("混剪输出宽高必须为正偶数")
    mixer = local_mixer(cancelled, get_ffmpeg_path())
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    failures: list[dict] = []
    for index, task_slots in enumerate(tasks):
        _check_cancel(cancelled)
        target = _output_path(output_dir, f"mix-{index + 1:03d}", ".mp4")
        try:
            ok = mixer.render_task(task_slots, str(audios[index % len(audios)]) if audios else "",
                                   str(target), total_duration=total, target_w=width, target_h=height)
        except DesktopToolCancelled:
            target.unlink(missing_ok=True)
            raise
        if cancelled():
            target.unlink(missing_ok=True)
            _check_cancel(cancelled)
        if ok and target.is_file() and target.stat().st_size:
            outputs.append(target)
        else:
            target.unlink(missing_ok=True)
            failures.append({"index": index + 1, "error": "混剪渲染失败"})
        progress(10 + round((index + 1) / max(1, len(tasks)) * 85))
    if not outputs:
        raise ValueError("批量混剪全部失败")
    return outputs, {"operation": "video_mix", "mode": mode_letter, "failures": failures}


def execute(operation: str, sources: list[dict], output_dir: str | Path, instruction: str,
            params: dict | None = None, cancelled: Cancelled | None = None,
            progress: Progress | None = None) -> tuple[list[Path], dict]:
    params = dict(params or {})
    cancelled = cancelled or (lambda: False)
    progress = progress or (lambda _value: None)
    _check_cancel(cancelled)
    if operation not in CAPABILITIES:
        raise ValueError(f"不支持的桌面工具操作：{operation}")
    output_dir = Path(output_dir)
    normalized = []
    for item in sources:
        raw = str(item.get("path") or item.get("local_path") or "")
        if re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.IGNORECASE):
            raise ValueError("桌面工具只接受本地文件路径，不下载远程素材")
        path = Path(raw)
        if not raw or not path.is_file():
            raise ValueError(f"本地素材文件不存在：{raw or '未提供路径'}")
        kind = str(item.get("kind") or item.get("media_type") or "")
        normalized.append({**item, "path": str(path.resolve()), "kind": kind})
    sources = normalized
    paths = [(Path(item["path"]), item["kind"]) for item in sources]
    images = [path for path, kind in paths if kind == "image"]
    videos = [path for path, kind in paths if kind == "video"]
    audios = [path for path, kind in paths if kind == "audio"]
    progress(5)
    if operation == "image_batch":
        return run_image_batch(images, output_dir, instruction, params, cancelled, progress)
    if operation == "audio_separate":
        return run_audio_separate(videos + audios, output_dir, cancelled, progress)
    if operation == "slideshow":
        return run_slideshow(images, audios, videos, output_dir, instruction, params, cancelled, progress,
                             [item for item in sources if str(item.get("kind") or "") == "image"],
                             [item for item in sources if str(item.get("kind") or "") == "video"],
                             [item for item in sources if str(item.get("kind") or "") == "audio"])
    if operation == "video_mix":
        return run_video_mix(videos, audios, output_dir, instruction, params, cancelled, progress)
    raise ValueError(f"不支持的桌面工具操作：{operation}")


def execute_desktop_tool(operation, instruction, references, output_dir, params=None,
                         progress=None, cancelled=None) -> list[dict]:
    """Agent-compatible entry point; returns local path/kind or error records.

    Errors affecting every input and cancellation raise exceptions. Partial
    failures are explicit ``{"error": ...}`` records alongside valid outputs.
    Optional super-resolution needs preinstalled ONNX weights; nothing downloads.
    """
    cancelled = cancelled or (lambda: False)
    progress = progress or (lambda _value: None)
    outputs, metadata = execute(operation, references, output_dir, instruction,
                                params=params, progress=progress, cancelled=cancelled)
    _check_cancel(cancelled)
    kind = {"image_batch": "image", "audio_separate": "audio",
            "slideshow": "video", "video_mix": "video"}[operation]
    records = []
    for path in outputs:
        if not path.is_file() or not path.stat().st_size:
            raise RuntimeError(f"工具结果文件不存在或为空：{path}")
        records.append({"path": str(path.resolve()), "kind": kind, "metadata": metadata})
    for failure in metadata.get("failures", []):
        records.append({"error": str(failure.get("error") or "素材处理失败"), **failure})
    progress(100)
    return records
