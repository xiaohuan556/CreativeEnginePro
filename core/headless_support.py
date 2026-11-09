"""Per-call cancellation adapters for the existing local media engines.

Engine functions get a private globals dictionary containing a subprocess facade.
The original modules (also used by the UI) are never patched. This also works in
a frozen build: no source files, Web worker, database or HTTP service are needed.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import time
from types import FunctionType, MethodType, SimpleNamespace


class DesktopToolCancelled(RuntimeError):
    """Cancellation must propagate instead of becoming a batch item failure."""


def check_cancel(cancelled):
    if cancelled():
        raise DesktopToolCancelled("用户已停止桌面工具任务")


class _Subprocess:
    """subprocess.run's subset used by the desktop engines, with polling."""

    def __init__(self, cancelled):
        self.cancelled = cancelled

    def __getattr__(self, name):
        return getattr(subprocess, name)

    def run(self, args, *, capture_output=False, check=False, timeout=None,
            input=None, **kwargs):
        check_cancel(self.cancelled)
        if capture_output:
            kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if input is not None:
            kwargs["stdin"] = subprocess.PIPE
        if os.name == "nt":
            kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
        started = time.monotonic()
        with subprocess.Popen(args, **kwargs) as process:
            try:
                while True:
                    check_cancel(self.cancelled)
                    remaining = None if timeout is None else timeout - (time.monotonic() - started)
                    if remaining is not None and remaining <= 0:
                        raise subprocess.TimeoutExpired(args, timeout)
                    try:
                        stdout, stderr = process.communicate(input=input, timeout=min(0.1, remaining) if remaining is not None else 0.1)
                        break
                    except subprocess.TimeoutExpired:
                        input = None
                check_cancel(self.cancelled)
            except BaseException:
                process.kill()
                process.communicate()
                raise
        result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result


class _SuperresSubprocess(_Subprocess):
    def run(self, args, **kwargs):
        if len(args) > 1 and Path(args[1]).name == "run_esr.py":
            # The desktop plugin's development script references `scale` without
            # defining it. Supply the same x4 constant as main.py's frozen worker
            # without editing either the plugin or its generated script.
            bootstrap = ("import runpy, sys; script=sys.argv[1]; sys.argv=sys.argv[1:]; "
                         "runpy.run_path(script, init_globals={'scale': 4}, run_name='__main__')")
            args = [args[0], "-c", bootstrap, *args[1:]]
        return super().run(args, **kwargs)


def _bind(function, namespace):
    bound = FunctionType(function.__code__, namespace, function.__name__,
                         function.__defaults__, function.__closure__)
    bound.__kwdefaults__ = function.__kwdefaults__
    return bound


def _engine_functions(module, cancelled):
    """Copy only this module's functions; preserve patched test functions."""
    namespace = dict(vars(module), subprocess=_Subprocess(cancelled))
    for name, value in list(namespace.items()):
        if isinstance(value, FunctionType) and value.__module__ == module.__name__:
            namespace[name] = _bind(value, namespace)
    return SimpleNamespace(**namespace)


def local_mixer(cancelled, ffmpeg_path):
    from core.mix_engine import FFmpegMixer

    mixer = FFmpegMixer(ffmpeg_path=ffmpeg_path)
    def render(task_slots, audio_path, out_path, total_duration=15.0, target_w=1080, target_h=1920):
        # The legacy mixer can leave an unfinished segment outside its cleanup
        # list when FFmpeg is stopped. Keep all its scratch files in this scope.
        with tempfile.TemporaryDirectory(prefix=".mix-", dir=Path(out_path).parent) as folder:
            namespace = dict(FFmpegMixer.render_task.__globals__,
                             subprocess=_Subprocess(cancelled),
                             tempfile=SimpleNamespace(mkdtemp=lambda: folder))
            result = _bind(FFmpegMixer.render_task, namespace)(
                mixer, task_slots, audio_path, out_path, total_duration, target_w, target_h)
            check_cancel(cancelled)
            return result

    mixer.render_task = render
    return mixer


def local_superres(cancelled):
    import builtins
    from core.plugins import superres

    # Existing installations use both spellings. Never download or rename a
    # model, and keep the desktop plugin's subprocess/DLL isolation intact.
    def model_path():
        configured = Path(superres._model_path())
        alternate = configured.with_name("realesr_general_x4v3.onnx")
        return str(configured if configured.is_file() or not alternate.is_file() else alternate)

    runner = _SuperresSubprocess(cancelled)

    def local_import(name, globals=None, locals=None, fromlist=(), level=0):
        return runner if name == "subprocess" and level == 0 else builtins.__import__(name, globals, locals, fromlist, level)

    namespace = dict(vars(superres), _model_path=model_path,
                     __builtins__={**vars(builtins), "__import__": local_import})
    namespace["_run_realesr_subproc"] = _bind(superres._run_realesr_subproc, namespace)
    plugin = superres.Superres()
    plugin.run = MethodType(_bind(superres.Superres.run, namespace), plugin)
    return plugin


def local_separator(cancelled, work_dir):
    from core.separator import AudioSeparator
    from utils.ffmpeg_utils import get_ffmpeg_path

    # Do not probe optional model engines: some download weights on first use.
    # Reuse the existing deterministic center-channel FFmpeg fallback explicitly.
    namespace = dict(AudioSeparator.extract_audio.__globals__,
                     subprocess=_Subprocess(cancelled), FFMPEG_BIN=get_ffmpeg_path(),
                     ensure_work_dir=lambda: work_dir.mkdir(parents=True, exist_ok=True))
    methods = {name: _bind(getattr(AudioSeparator, name), namespace)
               for name in ("__init__", "_check_ffmpeg", "extract_audio", "_separate_ffmpeg")}
    methods["_detect_engine"] = lambda self: "ffmpeg"
    separator_type = type("LocalFFmpegSeparator", (AudioSeparator,), methods)
    return separator_type(work_dir=work_dir)


def slideshow_engine(cancelled):
    from core import slideshow_engine as engine

    local = _engine_functions(engine, cancelled)
    # Newer Web schedules support unequal durations. Reuse desktop decoding,
    # resizing and transition functions while keeping its UI renderer unchanged.
    if getattr(engine.render_video, "__module__", "") == engine.__name__:
        local.render_video = lambda *args, **kwargs: _render_video(local, cancelled, *args, **kwargs)
    local.package_slideshow_video = lambda *args, **kwargs: _package_video(local, cancelled, *args, **kwargs)
    return local


def _render_video(engine, cancelled, imgs, out, cfg, progress_callback=None, stop_event=None):
    import cv2

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fps = int(cfg.get("fps", 30))
    width, height = map(int, cfg.get("resolution", "1080x1920").split("x"))
    if width <= 0 or height <= 0 or width % 2 or height % 2:
        raise ValueError("轮播输出宽高必须为正偶数")
    durations = cfg.get("image_durations")
    if durations is not None:
        if len(durations) != len(imgs):
            raise ValueError("逐图时长数量与图片数量不一致")
        counts = [max(1, round(float(value) * fps)) for value in durations]
    else:
        counts = [max(1, int(float(cfg.get("video_duration", 10)) * fps / len(imgs)))] * len(imgs)
    transition = engine.TRANSITIONS.get(cfg.get("transition_type", "推进放大"), "zoom_push")
    quality = {"best": ("12", "slower"), "high": ("18", "medium"),
               "normal": ("23", "medium"), "low": ("28", "fast"),
               "minimal": ("32", "ultrafast")}
    crf, preset = quality.get(cfg.get("video_quality", "minimal"), quality["minimal"])

    def check():
        check_cancel(cancelled)
        if stop_event and stop_event.is_set():
            raise DesktopToolCancelled("用户已停止图片轮播任务")

    with tempfile.TemporaryDirectory(prefix=".slideshow-", dir=out.parent) as folder:
        frame_index = 0
        image = None
        for index, source in enumerate(imgs):
            check()
            if image is None:
                image = engine.cv_imread(source)
            if image is None:
                raise ValueError(f"无法读取轮播图片：{source}")
            current = engine.resize_fill(image, width, height)
            image = engine.cv_imread(imgs[index + 1]) if index + 1 < len(imgs) else None
            next_image = engine.resize_fill(image, width, height) if image is not None else None
            count = counts[index]
            transition_frames = min(int(cfg.get("transition_frames", 15)), count)
            for frame_number in range(count):
                check()
                if transition_frames and next_image is not None and frame_number >= count - transition_frames:
                    alpha = (frame_number - (count - transition_frames)) / transition_frames
                    frame = engine.apply_transition(current, next_image, alpha, transition, width, height)
                else:
                    frame = current
                ok, encoded = cv2.imencode(".png", frame)
                if not ok:
                    raise RuntimeError("轮播帧编码失败")
                Path(folder, f"{frame_index:06d}.png").write_bytes(encoded.tobytes())
                frame_index += 1
                if progress_callback:
                    progress_callback(frame_index, sum(counts))
        check()
        engine._run_ffmpeg([
            engine.get_ffmpeg_path(), "-y", "-framerate", str(fps),
            "-i", str(Path(folder, "%06d.png")), "-c:v", "libx264",
            "-crf", crf, "-preset", preset, "-pix_fmt", "yuv420p", str(out),
        ], stop_event)
        check()
    return out


def _package_video(engine, cancelled, video_path, out_path, *, watermark_path=None,
                   outro_path=None, opacity=1.0, ffmpeg_path=None, stop_event=None):
    """Web's packaging order: alpha overlay first, append outro second."""
    if not watermark_path and not outro_path:
        raise ValueError("图片轮播包装至少需要水印或尾页中的一项")
    out_path = Path(out_path)
    ffmpeg_path = ffmpeg_path or engine.get_ffmpeg_path()
    with tempfile.TemporaryDirectory(prefix=".packaging-", dir=out_path.parent) as folder:
        current = Path(video_path)
        operations = []
        if watermark_path:
            from utils import alpha_video
            alpha = _engine_functions(alpha_video, cancelled)
            has_alpha = alpha.probe_has_alpha(str(watermark_path))
            check_cancel(cancelled)
            if not has_alpha:
                raise ValueError("水印视频没有透明通道；需要带 Alpha 的 MOV")
            duration = engine.get_video_duration(current, ffmpeg_path)
            width, height = engine._get_video_resolution(current, ffmpeg_path)
            opacity = max(0.0, min(1.0, float(opacity)))
            overlay = Path(folder, "watermarked.mp4")
            engine._run_ffmpeg([
                ffmpeg_path, "-y", "-i", str(current), "-stream_loop", "-1", "-i", str(watermark_path),
                "-filter_complex", "[0:v]setpts=PTS-STARTPTS[base];"
                f"[1:v]trim=duration={duration:.6f},setpts=PTS-STARTPTS,scale={width}:{height},"
                f"format=rgba,colorchannelmixer=aa={opacity:.4f}[wm];"
                "[base][wm]overlay=0:0:format=auto:shortest=1[outv]",
                "-map", "[outv]", "-map", "0:a?", "-t", str(duration),
                "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(overlay),
            ], stop_event)
            current = overlay
            operations.append("watermark")
        check_cancel(cancelled)
        if outro_path:
            engine.concat_videos(current, outro_path, out_path,
                                 ffmpeg_path=ffmpeg_path, stop_event=stop_event)
            operations.append("outro")
        else:
            os.replace(current, out_path)
        check_cancel(cancelled)
        return operations
