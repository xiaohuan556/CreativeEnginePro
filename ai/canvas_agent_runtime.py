"""Local, resumable execution of validated canvas Agent plans."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import time
from urllib.parse import urlencode

from ai.providers.base import TaskRequest

TOOL_LABELS = {
    "image_generate": "图片生成", "video_generate": "视频生成", "audio_generate": "配音生成",
    "script_generate": "脚本创作", "video_analysis": "视频拉片", "image_upscale": "图片高清",
    "slideshow": "图片轮播", "video_mix": "视频混剪", "pinterest_import": "Pinterest 取材",
    "image_batch": "图片批处理", "audio_separate": "声音分离", "timeline_edit": "剪辑成片",
}


class AgentCancelled(RuntimeError):
    pass


class AgentNeedsInput(ValueError):
    pass


def wait_task(manager, provider, request, cancelled, progress):
    handle = manager.submit(provider, request)
    while not handle.is_finished:
        if cancelled():
            handle.cancel()
            raise AgentCancelled("任务已停止")
        progress(float(handle.progress or 0))
        time.sleep(0.1)
    if cancelled():
        raise AgentCancelled("任务已停止")
    if not handle.is_success or handle.result is None:
        raise RuntimeError(str(getattr(handle.result, "error", "") or "模型任务失败"))
    return handle.result.data


def choose_provider(manager, operation, preferred=""):
    providers = manager.registry.by_capability(operation)
    # Agent execution is local; a Web control-plane provider would upload assets.
    providers = [p for p in providers if p.__class__.__module__ != "ai.providers.control_plane"]
    if preferred:
        providers = [p for p in providers if p.name == preferred]
    if not providers:
        raise RuntimeError(f"桌面端没有配置可执行 {operation} 的模型，请在模型设置中配置。")
    return providers[0]


def result_records(data, kind, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if kind == "text":
        text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2)
        path = folder / "result.txt"
        path.write_text(text, encoding="utf-8")
        return [{"path": str(path), "kind": "text", "content": text}]
    values = list(data) if isinstance(data, (list, tuple)) else [data]
    results = []
    for index, value in enumerate(values):
        if isinstance(value, dict):
            value = value.get("path") or value.get("local_path") or value.get("data")
        if isinstance(value, bytes):
            suffix = {"image": ".png", "audio": ".wav", "video": ".mp4"}[kind]
            path = folder / f"result_{index + 1}{suffix}"
            path.write_bytes(value)
        else:
            path = Path(str(value or ""))
        if not path.is_file() or not path.stat().st_size:
            raise RuntimeError("模型没有返回有效的本地结果文件")
        results.append({"path": str(path.resolve()), "kind": kind})
    if not results:
        raise RuntimeError("模型返回的结果为空")
    return results


def reference_size(references):
    visual = [row for row in references if row.get("kind") in {"image", "video"}
              and row.get("role") not in {"watermark_overlay", "watermark", "outro", "product_icon", "product_background", "mask"}]
    visual.sort(key=lambda row: row.get("kind") != "video")
    if not visual:
        return 1080, 1080
    row = visual[0]
    if row["kind"] == "image":
        from PIL import Image, ImageOps
        with Image.open(row["path"]) as image:
            return ImageOps.exif_transpose(image).size
    import cv2
    capture = cv2.VideoCapture(str(row["path"]))
    try:
        width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if width <= 0 or height <= 0:
            raise ValueError("无法读取主视频尺寸")
        return width, height
    finally:
        capture.release()


def preset_size(params, references):
    edge = int(str(params["resolution"])[:-1])
    ratio = str(params.get("ratio") or "adaptive")
    if ratio == "adaptive":
        width, height = reference_size(references)
    else:
        match = re.fullmatch(r"([1-9]\d*):([1-9]\d*)", ratio)
        if not match:
            raise ValueError("输出比例必须是宽:高或跟随素材")
        width, height = int(match[1]), int(match[2])
    factor = edge / min(width, height)
    return max(2, round(width * factor / 2) * 2), max(2, round(height * factor / 2) * 2)


def _pinterest_references(params, folder, cancelled, progress):
    from core.downloader import YOUTUBE_COOKIES_FILE
    from core.pinterest_importer import PinterestImporter

    class StopSignal:
        def is_set(self):
            return cancelled()

    page = params.get("page_url") or (
        "https://www.pinterest.com/search/pins/?" + urlencode({"q": params["query"]})
    )
    count = params.get("reference_count", 2)
    downloaded = 0

    def on_image(_path, _url):
        nonlocal downloaded
        downloaded += 1
        progress(downloaded / count, "已保存图片")

    report = PinterestImporter(YOUTUBE_COOKIES_FILE).import_page(
        page, count, folder, candidate_factor=3, stop_event=StopSignal(), min_side=240,
        progress=lambda _done, _total, message: progress(downloaded / count, message),
        item_ready=on_image,
    )
    if cancelled():
        raise AgentCancelled("任务已停止")
    if not report.get("items"):
        raise AgentNeedsInput("Pinterest 没找到可用图片。请换关键词或连接可访问的 Pinterest 页面。")
    results = [{"path": str(Path(item["path"]).resolve()), "kind": "image",
             "role": "element", "title": "Pinterest 元素参考",
             "metadata": {"source": "Pinterest", "page_url": item.get("page_url", page),
                          "image_url": item.get("image_url", "")}}
            for item in report["items"]]
    if len(results) < count:
        results.append({"error": f"Pinterest 只下载到 {len(results)} / {count} 张。"
                                 "已保留下载的图片，请换链接或减少目标张数后重试。"})
    return results


def _canvas_image_provider(manager, preferred=""):
    configured = manager.registry.by_capability("image_edit")
    provider = next((p for p in configured if p.name in {"gptimage", "seedream"} and (not preferred or p.name == preferred)
                     and p.__class__.__module__ != "ai.providers.control_plane"), None)
    if provider is None:
        raise AgentNeedsInput(f"图片模型 {preferred or 'GPT Image / Seedream'} 未配置或当前不可用，请检查模型设置。")
    return provider


def execute_tool(step, references, settings, folder, manager, cancelled, progress):
    tool = step["tool"]
    prompt = str(step.get("prompt") or "")
    params = {**{key: value for key, value in settings.items() if not key.startswith("_")},
              **dict(step.get("params") or {})}
    def local_progress(value, *_message):
        progress(max(0.0, min(1.0, float(value or 0) / 100.0)))
    if tool == "pinterest_import":
        return _pinterest_references(step["params"], folder, cancelled, progress)
    if tool == "image_upscale":
        from core.canvas_media_tools import run_media_tool
        return run_media_tool(tool, references, params, folder, cancel_check=cancelled, progress=local_progress)
    if tool in {"image_batch", "audio_separate", "slideshow", "video_mix"}:
        from core.headless_tools import execute_desktop_tool
        if tool in {"slideshow", "video_mix"} and str(params.get("resolution") or "") in {"480p", "720p", "1080p"}:
            width, height = preset_size(params, references)
            params["resolution"] = f"{width}x{height}"
        return execute_desktop_tool(tool, prompt, references, folder, params=params,
                                    progress=local_progress, cancelled=cancelled)
    if tool == "timeline_edit":
        from ai.canvas_agent_editing import run_timeline_edit
        return run_timeline_edit(prompt, references, params, folder,
                                 cancel_check=cancelled, progress=local_progress, manager=manager)
    if tool == "video_analysis":
        from ai.video_breakdown import analyze_video
        source = next((row for row in references if row.get("kind") == "video"), None)
        if source is None:
            raise ValueError("拉片需要连接视频素材")
        return result_records(analyze_video(source["path"], str(folder)), "text", folder)
    images = [row for row in references if row.get("kind") == "image"]
    text_context = "\n".join(str(row.get("content") or "") for row in references
                             if row.get("kind") == "text" and row.get("content"))
    if text_context and tool in {"image_generate", "video_generate"}:
        prompt = text_context + "\n" + prompt
    if tool == "image_generate":
        operation, kind = ("image_edit" if images else "text_to_image"), "image"
        inputs = {"prompt": prompt, "references": [row["path"] for row in images],
                  "reference_assets": images}
        if images:
            inputs["image"] = images[0]["path"]
    elif tool == "video_generate":
        # Multiple images all enter reference mode; none may be discarded.
        extras = [row for row in references if row.get("kind") in {"video", "audio"}]
        operation = "image_to_video" if len(images) == 1 and not extras else "text_to_video"
        typed = images + [{**row, "role": "reference_video" if row["kind"] == "video" else "reference_audio"} for row in extras]
        kind, inputs = "video", {"prompt": prompt, "reference_assets": typed}
        first = next((row for row in images if row.get("role") == "first_frame"), None)
        last = next((row for row in images if row.get("role") == "last_frame"), None)
        endpoint_request = bool(re.search(r"首尾帧", prompt) and not re.search(r"(?:不要|不用|不使用|无需)首尾帧", prompt))
        if endpoint_request and not first and not last and len(images) == 2:
            first, last = images
        if first or last or endpoint_request:
            if not first or (endpoint_request and not last):
                raise ValueError("首尾帧视频需要明确连接首帧和尾帧图片。")
            allowed = {str(first["path"])} | ({str(last["path"])} if last else set())
            if extras or any(str(row["path"]) not in allowed for row in images):
                raise ValueError("严格首尾帧模式无法同时使用额外参考素材；请移除额外素材，或改用普通多图参考。")
            operation = "image_to_video"
            inputs["image"] = first["path"]
            if last:
                inputs["last_frame"] = last["path"]
        if operation == "image_to_video":
            inputs.setdefault("image", images[0]["path"])
    elif tool == "audio_generate":
        operation, kind, inputs = "text_to_speech", "audio", {"text": text_context or str(params.get("text") or prompt)}
        if params.get("mode") == "voice_clone":
            audio = next((row for row in references if row.get("kind") == "audio"), None)
            if not audio or params.get("voice_consent") is not True or not params.get("reference_transcript"):
                raise ValueError("声音克隆需要参考录音、录音原文和声音授权确认")
            operation = "clone_voice"
            inputs["reference_audio"] = audio["path"]
    elif tool == "script_generate":
        operation, kind = "chat", "text"
        context = "\n".join(str(row.get("content") or "") for row in references)
        if any(row.get("kind") in {"video", "audio"} for row in references):
            raise ValueError("根据视频或音频写脚本前，请先安排拉片或转录步骤，将报告作为文本输入。")
        content = context + "\n" + prompt
        if images:
            from ai.providers.ark_http import to_image_data_url
            content = [{"type": "text", "text": content}] + [
                {"type": "image_url", "image_url": {"url": to_image_data_url(row["path"])}} for row in images]
        inputs = {"messages": [{"role": "user", "content": content}]}
    else:
        raise ValueError(f"尚未接入本地执行的工具：{tool}")
    pinterest_edit = tool == "image_generate" and params.pop("preserve_layout", False)
    style_edit = tool == "image_generate" and params.pop("style_transfer", False)
    if pinterest_edit or style_edit:
        provider = _canvas_image_provider(manager, str(params.get("provider_name") or ""))
        reference_limit = params.pop("reference_limit", 4) if pinterest_edit else 0
        if pinterest_edit and reference_limit and len(images) < 2:
            raise AgentNeedsInput("换图需要原图和至少一张 Pinterest 元素参考。")
        chosen = images[:1 + reference_limit] if pinterest_edit else images
        inputs["references"] = [row["path"] for row in chosen]
        inputs["reference_assets"] = [
            {**row, "role": "composition" if i == 0 else "element" if pinterest_edit else "style",
             "label": "原图 · 版式基准" if i == 0 else "Pinterest 元素参考" if pinterest_edit else "风格参考"}
            for i, row in enumerate(chosen)
        ]
        params["n"] = params.get("candidate_count", 1)
        width, height = reference_size(images[:1])
        params.setdefault("size", "2K" if provider.name == "seedream" else "auto")
        inputs["prompt"] += f"\n成图保留原图的宽高比 {width}:{height}。"
    else:
        provider = choose_provider(manager, operation, str(params.get("provider_name") or ""))
    if tool == "video_generate" and len(images) > 1:
        profile = provider.capability_profile()
        limit = int(profile.get("reference_images") or profile.get("reference_assets") or 3)
        if len(images) > limit:
            raise ValueError(f"{provider.name} 最多接收 {limit} 张参考图，当前 {len(images)} 张")
    if tool == "video_generate" and extras:
        profile = provider.capability_profile()
        if provider.name == "seedance":
            from ai.providers.video.seedance_models import seedance_model_profile
            profile = seedance_model_profile(str(params.get("model") or "doubao-seedance-2-5-260628"))
        for media_kind, key in (("video", "reference_videos"), ("audio", "reference_audios")):
            count = sum(row.get("kind") == media_kind for row in extras)
            limit = int(profile.get(key) or 0)
            if count > limit:
                raise ValueError(f"当前模型不支持本次 {count} 个{media_kind}参考，支持数量为 {limit}。")
    def submit(request_params, destination, notify):
        try:
            data = wait_task(manager, provider.name,
                             TaskRequest(operation, inputs, request_params, use_cache=False), cancelled, notify)
        except RuntimeError as error:
            if pinterest_edit or style_edit:
                from ai.generation_errors import moderation_failure
                failure = moderation_failure(error)
                if failure:
                    request_id = failure["request_id"]
                    suffix = f" 请求编号：{request_id}。" if request_id else ""
                    raise RuntimeError(
                        f"{provider.name} 拒绝了这张图的生成请求。请检查提示词和素材。"
                        + (" 可以在出图设置中将「生图参考」切到「仅原图，不取材」，"
                           "再用有权使用的原图和原创元素描述生成。"
                           if pinterest_edit and reference_limit else "") + suffix) from error
            raise
        return result_records(data, kind, destination)

    count = int(params.get("n") or params.get("candidate_count") or 1) if tool == "image_generate" else 1
    if count <= 1:
        return submit(params, folder, progress)

    results = []
    for index in range(count):
        if cancelled():
            raise AgentCancelled("任务已停止")

        def item_progress(value):
            fraction = max(0.0, min(1.0, float(value or 0) / 100.0))
            progress((index + fraction) / count)

        try:
            results.extend(submit({**params, "n": 1}, folder / f"image_{index + 1:02d}", item_progress))
        except AgentCancelled:
            raise
        except Exception as error:
            results.append({"error": f"第 {index + 1} 张：{str(error) or '生成失败'}"})
        progress((index + 1) / count)
    return results


def run_agent_plan(plan, references, settings, output_dir, manager, *,
                   previous=None, cancelled=lambda: False, on_state=lambda state: None,
                   executor=None):
    from ai.canvas_agent_planner import validate_plan
    validate_plan(plan, references, plan.get("instruction"), settings)
    if plan.get("status") != "ready":
        raise ValueError("只有已就绪的计划可以执行")
    old = (previous or {}).get("steps") or {}

    def reusable(saved):
        if saved.get("status") != "completed" or not saved.get("outputs"):
            return False
        try:
            return all(Path(str(row.get("path") or "")).is_file() and
                       Path(str(row["path"])).stat().st_size > 0 for row in saved["outputs"])
        except OSError:
            return False

    restored = {}
    step_ids = {step["id"] for step in plan["steps"]}
    for step in plan["steps"]:
        dependencies = set(step.get("dependencies") or []) | (set(step.get("inputs") or []) & step_ids)
        if reusable(old.get(step["id"]) or {}) and dependencies <= restored.keys():
            restored[step["id"]] = deepcopy(old[step["id"]])
    state = {"plan": deepcopy(plan), "status": "running", "steps": restored}
    sources = {str(row["id"]): {**row, "kind": row.get("kind") or row.get("media_type")}
               for row in references}
    executor = executor or execute_tool

    def publish():
        on_state(deepcopy(state))

    try:
        settings = dict(settings)
        for step in plan["steps"]:
            sid = step["id"]
            if cancelled():
                raise AgentCancelled("任务已停止")
            if sid in state["steps"]:
                publish()
                continue
            inputs = []
            for source_id in step.get("inputs", []):
                if source_id in sources:
                    inputs.append(dict(sources[source_id]))
                else:
                    inputs.extend(deepcopy([row for row in state["steps"][source_id]["outputs"]
                                           if row.get("kind") != "project"]))
            current = {"status": "running", "outputs": [], "progress": 0.0}
            state["steps"][sid] = current
            publish()
            last_report = [0.0, -1.0]
            def report(value, message=""):
                if message:
                    current["message"] = str(message)
                value = float(value or 0)
                current["progress"] = max(0.0, min(1.0, value / 100 if value > 1 else value))
                if time.monotonic() - last_report[0] < .5 and abs(current["progress"] - last_report[1]) < .05:
                    return
                last_report[:] = [time.monotonic(), current["progress"]]
                publish()
            results = executor(step, inputs, settings, Path(output_dir) / sid, manager, cancelled, report)
            if cancelled():
                raise AgentCancelled("任务已停止")
            errors = [str(row.get("error")) for row in results if row.get("error")]
            outputs = [dict(row) for row in results if row.get("path") and not row.get("error")]
            if not outputs:
                if any(row.get("metadata", {}).get("needs_input") for row in results):
                    raise AgentNeedsInput("；".join(errors) or "请补齐本地运行资源")
                raise RuntimeError("；".join(errors) or "工具没有生成结果")
            for row in outputs:
                if not Path(str(row["path"])).is_file() or not Path(str(row["path"])).stat().st_size:
                    raise RuntimeError("工具结果文件不存在或为空")
                if step.get("params", {}).get("output_role"):
                    row["role"] = str(step["params"]["output_role"])
            if errors:
                current.update(outputs=outputs, errors=errors)
                if step["tool"] not in {"image_generate", "pinterest_import"}:
                    raise RuntimeError("部分素材处理失败：" + "；".join(errors))
            current.update(status="completed", outputs=outputs, progress=1.0)
            publish()
        state["status"] = "completed"
    except Exception as error:
        state["status"] = ("cancelled" if isinstance(error, AgentCancelled) or cancelled() else
                           "needs_input" if isinstance(error, AgentNeedsInput) else "failed")
        state["error"] = str(error)
        if 'current' in locals() and current.get("status") == "running":
            current.update(status=state["status"], error=str(error))
    publish()
    return state
