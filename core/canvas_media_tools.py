"""Offline image upscaling with AI and explicit traditional modes.

run_media_tool('image_upscale', references, params, output_dir,
               cancel_check=None, progress=None) returns one row per reference.
Rows have path, kind, metadata and status; failed/cancelled rows have path=''
and error. Successful rows can be chained. Inputs are never mutated.

mode='ai' (default) or 'traditional', scale=2|4 (upscale_scale alias),
engine='auto'|'ncnn'|'onnx', model_path (local x4 RGB/NCHW ONNX),
realesrgan_dir (NCNN installation). No downloads or implicit interpolation.

progress receives (percent: int, message: str). Cancel callbacks return truthy;
subprocess trees, including isolated ONNX inference, are stopped. Pillow calls
check cancellation before/after processing. Only validated files are published
by atomic rename, with unique output names.
"""
from __future__ import annotations

import logging
import math
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path


class MediaToolCancelled(RuntimeError):
    """Internal cancellation signal; exposed as cancelled result rows."""


def _check(cancel_check):
    if cancel_check and cancel_check():
        raise MediaToolCancelled("媒体处理已取消")


def _notify(progress, percent, message):
    if progress:
        try:
            progress(int(percent), message)
        except Exception:
            logging.getLogger(__name__).warning("媒体进度回调失败", exc_info=True)


def _number(value, minimum, maximum, name):
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
    return number


def _local_path(value):
    if value is None or not str(value).strip():
        raise ValueError("缺少本地文件路径")
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"本地文件不存在：{path}")
    return path


def _stop(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        finally:
            if process.poll() is None:
                process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=10)


def _run(command, cancel_check=None, timeout=1800):
    """Disk-backed output avoids full pipes and blocked readline cancellation."""
    _check(cancel_check)
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen([str(arg) for arg in command], stdin=subprocess.DEVNULL,
                                   stdout=stdout, stderr=stderr, **flags)
        started = time.monotonic()
        try:
            while process.poll() is None:
                _check(cancel_check)
                if time.monotonic() - started > timeout:
                    raise TimeoutError(f"{Path(command[0]).name} 执行超时")
                time.sleep(0.05)
            _check(cancel_check)
            if process.returncode:
                stderr.seek(max(0, stderr.tell() - 4000))
                detail = stderr.read().decode("utf-8", errors="replace").strip()
                if not detail:
                    stdout.seek(max(0, stdout.tell() - 4000))
                    detail = stdout.read().decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"{Path(command[0]).name} 失败（{process.returncode}）：{detail}")
            stdout.seek(0)
            return stdout.read().decode("utf-8", errors="replace")
        finally:
            _stop(process)


def _image(path):
    from PIL import Image, ImageOps
    with Image.open(path) as source:
        source.load()
        # Normalize EXIF once, preserving the alpha plane for all image tools.
        return ImageOps.exif_transpose(source).convert("RGBA" if "A" in source.getbands() or "transparency" in source.info else "RGB")


def _ncnn_runtime(params):
    configured = params.get("realesrgan_dir") or os.environ.get("CEP_REALESRGAN_DIR")
    roots = [Path(configured)] if configured else [
        Path.home() / ".cep_models" / "realesrgan-ncnn-vulkan",
        Path("G:/CreativeEngineData/models/realesrgan-ncnn-vulkan"),
    ]
    for root in roots:
        exe = root / ("realesrgan-ncnn-vulkan.exe" if os.name == "nt" else "realesrgan-ncnn-vulkan")
        models = root / "models"
        if exe.is_file() and all((models / f"realesrgan-x4plus.{ext}").is_file() for ext in ("param", "bin")):
            return exe, models
    if configured or params.get("engine") == "ncnn":
        raise RuntimeError("未找到本地 Real-ESRGAN NCNN 程序及 x4plus 模型；未下载、未降级")
    return None


def _onnx_model(params):
    if params.get("model_path"):
        return _local_path(params["model_path"])
    for name in ("realesr_general_x4v3.onnx", "realesr-general-x4v3.onnx"):
        path = Path.home() / ".cep_models" / name
        if path.is_file():
            return path
    raise RuntimeError("未找到本地 Real-ESRGAN 模型；请设置 model_path 或 CEP_REALESRGAN_DIR。未下载、未降级；传统放大需显式 mode='traditional'")


def _onnx_worker_infer(image, model, cancel_check, tick):
    """CHILD PROCESS ONLY. Never call from the Qt/OpenCV host process.

    Kept in this module so source installations can execute this file directly,
    without importing UI or loading their native DLLs in the inference worker.
    """
    import numpy as np
    import onnxruntime as ort
    from PIL import Image

    _check(cancel_check)
    session = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
    inputs = session.get_inputs()
    if len(inputs) != 1 or len(inputs[0].shape) != 4:
        raise ValueError("仅支持单输入 RGB NCHW x4 Real-ESRGAN ONNX 模型")
    inp = inputs[0]
    if inp.type != "tensor(float)" or inp.shape[1] != 3:
        raise ValueError("ONNX 模型必须接受 float32 RGB NCHW 输入")
    fixed_h, fixed_w = inp.shape[-2:]
    fixed = all(isinstance(v, int) and v > 0 for v in (fixed_h, fixed_w))
    tile_h, tile_w = (fixed_h, fixed_w) if fixed else (128, 128)
    if tile_h > 1024 or tile_w > 1024:
        raise ValueError("ONNX 固定输入过大（超过 1024），无法安全分块")
    pad = 0 if fixed else 8
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255
    h, w = rgb.shape[:2]
    result = np.empty((h * 4, w * 4, 3), dtype=np.uint8)
    total = math.ceil(h / tile_h) * math.ceil(w / tile_w)
    done = 0
    for y in range(0, h, tile_h):
        for x in range(0, w, tile_w):
            _check(cancel_check)
            y1, x1 = min(h, y + tile_h), min(w, x + tile_w)
            yy, xx = max(0, y - pad), max(0, x - pad)
            patch = rgb[yy:min(h, y1 + pad), xx:min(w, x1 + pad)]
            ph, pw = patch.shape[:2]
            if fixed:
                patch = np.pad(patch, ((0, tile_h - ph), (0, tile_w - pw), (0, 0)), mode="edge")
            tensor = np.ascontiguousarray(patch.transpose(2, 0, 1)[None])
            prediction = session.run(None, {inp.name: tensor})[0]
            _check(cancel_check)
            expected = (1, 3, patch.shape[0] * 4, patch.shape[1] * 4)
            if prediction.shape != expected or not np.isfinite(prediction).all():
                raise RuntimeError("Real-ESRGAN 未返回有效的 x4 图像，任务停止，未降级")
            pixels = (prediction[0].transpose(1, 2, 0).clip(0, 1) * 255).round().astype(np.uint8)
            oy, ox = (y - yy) * 4, (x - xx) * 4
            result[y*4:y1*4, x*4:x1*4] = pixels[oy:oy+(y1-y)*4, ox:ox+(x1-x)*4]
            done += 1
            tick(done / total * 0.85, "Real-ESRGAN 离线分块推理")
    return Image.fromarray(result)


def _onnx_command(model, input_path, output_path):
    if getattr(sys, "frozen", False):
        # Existing main.py dispatches this flag before all Qt/OpenCV imports.
        return [sys.executable, "--realesr-worker", str(model), str(input_path), str(output_path)]
    return [sys.executable, str(Path(__file__).resolve()), "--onnx-worker",
            str(model), str(input_path), str(output_path)]


def _onnx_upscale(image, model, cancel_check, tick):
    """Crash isolation is mandatory, including ONNX DLL import/session setup."""
    import numpy as np
    from PIL import Image

    _check(cancel_check)
    with tempfile.TemporaryDirectory(prefix="cep_canvas_onnx_") as folder:
        input_path, output_path = Path(folder) / "input.npy", Path(folder) / "output.npy"
        np.save(input_path, np.asarray(image.convert("RGB")), allow_pickle=False)
        tick(0.05, "Real-ESRGAN 离线子进程推理")
        _run(_onnx_command(model, input_path, output_path), cancel_check)
        _check(cancel_check)
        if not output_path.is_file():
            # The existing frozen worker can return zero after catching an
            # inference exception. Exit code alone must never imply success.
            raise RuntimeError("Real-ESRGAN 子进程未生成图像；模型/运行库异常，未降级")
        pixels = np.load(output_path, allow_pickle=False)
        expected = (image.height * 4, image.width * 4, 3)
        if pixels.dtype != np.uint8 or pixels.shape != expected:
            raise RuntimeError("Real-ESRGAN 子进程输出无效，任务停止，未降级")
        output = Image.fromarray(pixels)
        output.load()
        tick(0.85, "Real-ESRGAN 子进程推理完成")
        return output


def _upscale(source, target, params, cancel_check, tick):
    from PIL import Image
    image = _image(source)
    scale = _number(params.get("scale", params.get("upscale_scale", 4)), 2, 4, "scale")
    if scale not in (2, 4):
        raise ValueError("scale 只支持 2 或 4")
    scale = int(scale)
    size = (image.width * scale, image.height * scale)
    mode = str(params.get("mode", "ai")).lower()
    metadata = {"scale": scale, "source_width": image.width, "source_height": image.height,
                "width": size[0], "height": size[1], "mode": mode}
    if mode in {"traditional", "lanczos"}:
        output = image.resize(size, Image.Resampling.LANCZOS)
        metadata.update(engine="pillow-lanczos", ai=False, model=None)
    elif mode == "ai":
        engine = params.get("engine", "auto")
        if engine not in {"auto", "ncnn", "onnx"}:
            raise ValueError("engine 只支持 auto、ncnn、onnx")
        runtime = _ncnn_runtime(params) if engine != "onnx" and not params.get("model_path") else None
        if engine == "ncnn" and params.get("model_path"):
            raise ValueError("NCNN 请使用 realesrgan_dir，不接受 ONNX model_path")
        if runtime:
            exe, models = runtime
            # Keep source/output paths out of Chinese output folders for older
            # NCNN builds. Pillow handles the final Unicode path itself.
            with tempfile.TemporaryDirectory(prefix="cep_ncnn_") as folder:
                staged, rendered = Path(folder) / "input.png", Path(folder) / "output.png"
                image.convert("RGB").save(staged)
                _run([str(exe), "-i", str(staged), "-o", str(rendered), "-n", "realesrgan-x4plus",
                      "-s", str(scale), "-m", str(models), "-f", "png"], cancel_check)
                output = _image(rendered).convert("RGB")
            metadata.update(engine="Real-ESRGAN NCNN Vulkan", model="realesrgan-x4plus", ai=True)
        else:
            model = _onnx_model(params)
            output = _onnx_upscale(image, model, cancel_check, tick)
            if scale == 2:
                output = output.resize(size, Image.Resampling.BOX)
            metadata.update(engine="Real-ESRGAN ONNX", model=str(model), ai=True,
                            model_scale=4, isolated_process=True, alpha_engine="pillow-lanczos")
        if output.size != size:
            raise RuntimeError("Real-ESRGAN 输出尺寸与请求不符，未发布结果")
        if image.mode == "RGBA":
            output.putalpha(image.getchannel("A").resize(size, Image.Resampling.LANCZOS))
    else:
        raise ValueError("mode 只支持 ai 或 traditional（传统 Lanczos）")
    _check(cancel_check)
    output.save(target, "PNG")
    return metadata


def _kind(ref):
    kind = str(ref.get("kind") or "").lower()
    return kind if kind in {"image", "video", "audio"} else str(ref.get("media_type") or kind).lower()


def run_media_tool(operation, references: list[dict], params: dict, output_dir: Path,
                   cancel_check=None, progress=None) -> list[dict]:
    """Run image upscaling synchronously on the caller's worker."""
    if operation != "image_upscale":
        raise ValueError(f"不支持的本地媒体工具：{operation}；仅支持 image_upscale")
    if not isinstance(references, list) or not all(isinstance(ref, dict) for ref in references):
        raise TypeError("references 必须是 list[dict]")
    if not isinstance(params, dict):
        raise TypeError("params 必须是 dict")
    if not references:
        raise ValueError("没有可处理的源素材")
    output_dir = Path(output_dir).expanduser().resolve()
    rows, cancelled = [], False
    total = len(references)
    for index, ref in enumerate(references):
        metadata = {"operation": operation, "source_path": str(ref.get("path") or ""),
                    "source_id": ref.get("id"), "source_index": index}
        tick = lambda fraction, message: _notify(progress, (index + fraction) / total * 100, message)
        published = None
        try:
            if cancelled:
                raise MediaToolCancelled("批次已取消，未开始处理")
            _check(cancel_check)
            tick(0, f"{operation}：{index+1}/{total}")
            _check(cancel_check)
            if ref.get("error") or ref.get("status") in {"error", "cancelled"}:
                raise ValueError("输入是上一步失败/取消项，不能作为媒体")
            declared = _kind(ref)
            if declared and declared != "image":
                raise ValueError(f"素材类型不符：需要 image，得到 {declared}")
            output_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".canvas_media_", dir=output_dir) as folder:
                target = Path(folder) / "result.png"
                source = _local_path(ref.get("path"))
                details = _upscale(source, target, params, cancel_check, tick)
                _check(cancel_check)
                if not target.is_file() or not target.stat().st_size:
                    raise RuntimeError("工具未生成非空结果文件")
                verified = _image(target)
                if verified.size != (details["width"], details["height"]):
                    raise RuntimeError("结果图片校验失败")
                _check(cancel_check)
                destination = output_dir / f"{operation}_{uuid.uuid4().hex}.png"
                target.rename(destination)
                published = destination
                _check(cancel_check)
                metadata.update(details, status="success")
                row = {"path": str(destination), "kind": "image", "metadata": metadata, "status": "success"}
        except Exception as error:
            if published is not None:
                published.unlink(missing_ok=True)
            cancelled = cancelled or isinstance(error, MediaToolCancelled)
            status = "cancelled" if cancelled else "error"
            metadata.update(status=status, error=str(error), error_type=type(error).__name__)
            row = {"path": "", "kind": "image", "metadata": metadata, "status": status, "error": str(error)}
        rows.append(row)
        # Completion of an item is distinct from successful media production.
        tick(1, f"{index+1}/{total}：{row['status']}")
    return rows


if __name__ == "__main__":
    # Source-mode isolated worker. This branch imports neither Qt nor OpenCV.
    if len(sys.argv) != 5 or sys.argv[1] != "--onnx-worker":
        raise SystemExit("Use --onnx-worker MODEL INPUT.npy OUTPUT.npy")
    try:
        import numpy as np
        from PIL import Image

        worker_input = np.load(sys.argv[3], allow_pickle=False)
        worker_result = _onnx_worker_infer(Image.fromarray(worker_input), Path(sys.argv[2]),
                                           None, lambda *args: None)
        np.save(sys.argv[4], np.asarray(worker_result), allow_pickle=False)
        print("ESR_OK", flush=True)
    except Exception as worker_error:
        print(f"Real-ESRGAN worker failed: {worker_error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
