# core/slideshow_engine.py
"""
图片轮播视频生成器 — 渲染引擎
移植自「小欢图片轮播生成器」renderer.py，适配 CreativeEnginePro 的 FFmpeg 工具链
"""

import os
import sys
import re
import subprocess
import numpy as np
import cv2
from pathlib import Path

from utils.ffmpeg_utils import get_ffmpeg_path

# ─── 常量 ──────────────────────────────────────────────────
ZOOM_PUSH = 1.28

TRANSITIONS = {
    "推进放大": "zoom_push", "淡入淡出": "fade", "向左擦除": "wipe_left",
    "向右擦除": "wipe_right", "向上擦除": "wipe_up", "向下擦除": "wipe_down",
    "旋转推进": "spin_push", "缩放溶解": "zoom_dissolve",
    "闪白过渡": "flash_white", "径向扩散": "radial",
    "滑动推移": "slide_push", "像素溶解": "pixelate", "RGB故障": "glitch",
    "圆形划入": "circle_open", "窗帘拉开": "curtain",
}

TRANS_DESCS = {
    "推进放大": "当前图放大推出，下一张缩小进入（经典）",
    "淡入淡出": "两张图柔和叠加切换",
    "向左擦除": "新图从右往左划入", "向右擦除": "新图从左往右划入",
    "向上擦除": "新图从底部向上推入", "向下擦除": "新图从顶部向下落入",
    "旋转推进": "旧图旋转缩小退出，新图旋转放大进入",
    "缩放溶解": "旧图放大淡出，新图叠显",
    "闪白过渡": "先闪一帧白光再切入，节奏感强",
    "径向扩散": "从中心向四周圆形扩散揭开",
    "滑动推移": "旧图滑出同时新图滑入，双图联动",
    "像素溶解": "马赛克块从大到小，新图从模糊变清晰",
    "RGB故障": "红蓝通道错位+闪白，赛博故障风",
    "圆形划入": "锐利圆形从中心扩散，光圈感",
    "窗帘拉开": "从中间向左右打开，舞台开幕感",
}

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".gif"}


# ─── 工具函数 ──────────────────────────────────────────────

def cv_imread(path):
    """cv2.imread 不支持中文路径，用 np.fromfile 替代"""
    return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)


def resize_fill(img, target_w, target_h):
    """等比缩放并居中裁剪，不拉伸变形"""
    h, w = img.shape[:2]
    if w == target_w and h == target_h:
        return img
    scale = max(target_w / w, target_h / h)
    new_w = int(w * scale + 0.5)
    new_h = int(h * scale + 0.5)
    interp = getattr(cv2, "INTER_LANCZOS4", cv2.INTER_CUBIC)
    scaled = cv2.resize(img, (new_w, new_h), interpolation=interp)
    cx = (new_w - target_w) // 2
    cy = (new_h - target_h) // 2
    return scaled[cy:cy + target_h, cx:cx + target_w]


def is_image(p):
    return Path(p).suffix.lower() in IMG_EXTS


# ─── 转场效果 ──────────────────────────────────────────────

def apply_transition(a, b, alpha, tfn, w, h):
    """a, b: BGR numpy arrays, alpha: 0→1"""
    if tfn == "fade":
        return cv2.addWeighted(a, 1 - alpha, b, alpha, 0)

    elif tfn == "wipe_left":
        split = int(w * (1 - alpha))
        result = a.copy()
        result[:, :w - split] = b[:, :w - split]
        return result

    elif tfn == "wipe_right":
        split = int(w * (1 - alpha))
        result = a.copy()
        result[:, split:] = b[:, split:]
        return result

    elif tfn == "wipe_up":
        split = int(h * (1 - alpha))
        result = a.copy()
        result[:h - split, :] = b[:h - split, :]
        return result

    elif tfn == "wipe_down":
        split = int(h * (1 - alpha))
        result = a.copy()
        result[split:, :] = b[split:, :]
        return result

    elif tfn == "spin_push":
        scale_a = 1.0 - 0.3 * alpha
        scale_b = 0.7 + 0.3 * alpha
        angle_a = int(alpha * 45)
        angle_b = int((1 - alpha) * 45)
        ma = cv2.getRotationMatrix2D((w / 2, h / 2), angle_a, scale_a)
        mb = cv2.getRotationMatrix2D((w / 2, h / 2), -angle_b, scale_b)
        wa = cv2.warpAffine(a, ma, (w, h), borderMode=cv2.BORDER_REFLECT)
        wb = cv2.warpAffine(b, mb, (w, h), borderMode=cv2.BORDER_REFLECT)
        return cv2.addWeighted(wa, 1 - alpha, wb, alpha, 0)

    elif tfn == "zoom_dissolve":
        scale_a = 1.0 + 0.2 * alpha
        scale_b = 1.2 - 0.2 * alpha
        ma = cv2.getRotationMatrix2D((w / 2, h / 2), 0, scale_a)
        mb = cv2.getRotationMatrix2D((w / 2, h / 2), 0, scale_b)
        za = cv2.warpAffine(a, ma, (w, h))
        zb = cv2.warpAffine(b, mb, (w, h))
        return cv2.addWeighted(za, 1 - alpha, zb, alpha, 0)

    elif tfn == "flash_white":
        if alpha < 0.15:
            return np.ones_like(a) * 240
        elif alpha < 0.3:
            fade = (alpha - 0.15) / 0.15
            return cv2.addWeighted(np.ones_like(a) * 240, 1 - fade, b, fade, 0)
        else:
            return b

    elif tfn == "radial":
        result = a.copy()
        cx, cy = w // 2, h // 2
        max_r = np.sqrt(cx ** 2 + cy ** 2)
        current_r = int(max_r * alpha)
        y_arr, x_arr = np.ogrid[:h, :w]
        dist = np.sqrt((x_arr - cx) ** 2 + (y_arr - cy) ** 2)
        mask = dist <= current_r
        result[mask] = b[mask]
        return result

    elif tfn == "slide_push":
        offset = int(w * alpha)
        result = np.zeros_like(a)
        result[:, :w - offset] = a[:, offset:]
        result[:, w - offset:] = b[:, w - offset:]
        return result

    elif tfn == "pixelate":
        block = max(1, int(40 * (1 - alpha) + 1))
        small_h, small_w = max(1, h // block), max(1, w // block)
        small = cv2.resize(b, (small_w, small_h))
        large = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
        return cv2.addWeighted(a, 1 - alpha, large, alpha, 0)

    elif tfn == "glitch":
        fade = 1 - alpha
        if alpha < 0.3:
            shift_r = int(20 * fade * np.sin(alpha * 20))
            shift_b = int(-15 * fade * np.sin(alpha * 23))
            br = np.roll(b, shift_r, axis=1)
            bb = np.roll(b, shift_b, axis=1)
            result = b.copy()
            result[:, :, 2] = br[:, :, 2]
            result[:, :, 0] = bb[:, :, 0]
            if 0.15 < alpha < 0.25:
                result = cv2.addWeighted(result, 0.5, np.ones_like(b) * 255, 0.5, 0)
        else:
            local_fade = (alpha - 0.3) / 0.7
            shift_r = int(3 * (1 - local_fade) * np.sin(alpha * 10))
            shift_b = int(-3 * (1 - local_fade) * np.cos(alpha * 12))
            result = b.copy()
            result[:, :, 2] = np.roll(b[:, :, 2], shift_r, axis=1)
            result[:, :, 0] = np.roll(b[:, :, 0], shift_b, axis=1)
        return result

    elif tfn == "circle_open":
        r = int(max(w, h) * alpha * 0.75)
        mask = np.zeros((h, w), dtype=np.uint8)
        cv2.circle(mask, (w // 2, h // 2), r, 255, -1)
        mask3 = np.repeat(mask[:, :, np.newaxis], 3, axis=2)
        result = a.copy()
        result[mask3[:, :, 0] == 255] = b[mask3[:, :, 0] == 255]
        return result

    elif tfn == "curtain":
        gap = int(w * alpha)
        mid = w // 2
        left = max(0, mid - gap // 2)
        right = min(w, mid + gap // 2)
        result = a.copy()
        if right > left:
            result[:, left:right] = b[:, left:right]
        if left > 1:
            result[:, left - 1:left] = result[:, left - 1:left] // 2
        if right < w - 1:
            result[:, right:right + 1] = result[:, right:right + 1] // 2
        return result

    else:  # zoom_push default
        scale_a = 1.0 + (ZOOM_PUSH - 1.0) * alpha
        scale_b = ZOOM_PUSH - (ZOOM_PUSH - 1.0) * alpha
        ma = cv2.getRotationMatrix2D((w / 2, h / 2), 0, scale_a)
        mb = cv2.getRotationMatrix2D((w / 2, h / 2), 0, scale_b)
        za = cv2.warpAffine(a, ma, (w, h))
        zb = cv2.warpAffine(b, mb, (w, h))
        return cv2.addWeighted(za, 1 - alpha, zb, alpha, 0)


# ─── FFmpeg 工具 ───────────────────────────────────────────

def _run_ffmpeg(cmd, stop_event=None):
    """执行 FFmpeg 命令"""
    if sys.platform == "win32":
        cmd = [c.replace("\\", "/") if not c.startswith("-") else c for c in cmd]
    r = subprocess.run(cmd, capture_output=True,
                       encoding="utf-8", errors="replace",
                       creationflags=0x08000000 if sys.platform == "win32" else 0)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()[-300:]
        raise RuntimeError(f"FFmpeg 失败: {err}")


def _has_audio_stream(video_path, ffmpeg_path=None):
    """探测视频是否包含音频流"""
    if ffmpeg_path is None:
        ffmpeg_path = get_ffmpeg_path()
    path = str(video_path).replace("\\", "/") if sys.platform == "win32" else str(video_path)
    cmd = [ffmpeg_path, "-i", path]
    r = subprocess.run(cmd, capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       creationflags=0x08000000 if sys.platform == "win32" else 0)
    return "Audio:" in r.stderr


def _get_video_resolution(video_path, ffmpeg_path=None):
    """探测视频分辨率，返回 (width, height)"""
    if ffmpeg_path is None:
        ffmpeg_path = get_ffmpeg_path()
    try:
        path = str(video_path).replace("\\", "/") if sys.platform == "win32" else str(video_path)
        cmd = [ffmpeg_path, "-i", path]
        r = subprocess.run(cmd, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           creationflags=0x08000000 if sys.platform == "win32" else 0)
        for line in r.stderr.split("\n"):
            if "Stream #" in line and "Video:" in line:
                matches = re.findall(r'(\d+)x(\d+)', line)
                if matches:
                    w, h = matches[-1]
                    return int(w), int(h)
    except Exception:
        import logging; logging.getLogger("CreativeEnginePro").debug("_get_video_resolution probe failed", exc_info=True)
    return 1080, 1920


def get_video_duration(video_path, ffmpeg_path=None):
    """获取视频时长（秒）"""
    if ffmpeg_path is None:
        ffmpeg_path = get_ffmpeg_path()
    try:
        path = str(video_path).replace("\\", "/") if sys.platform == "win32" else str(video_path)
        cmd = [ffmpeg_path, "-i", path]
        r = subprocess.run(cmd, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           creationflags=0x08000000 if sys.platform == "win32" else 0)
        for line in r.stderr.split("\n"):
            if "Duration" in line:
                t = line.split("Duration:")[1].split(",")[0].strip()
                parts = t.split(":")
                return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    except Exception:
        import logging; logging.getLogger("CreativeEnginePro").debug("get_video_duration failed", exc_info=True)
    return 10.0


# ─── 核心渲染 ──────────────────────────────────────────────

def render_video(imgs, out, cfg, progress_callback=None, stop_event=None):
    """
    逐帧渲染图片轮播视频
    :param imgs: 图片路径列表
    :param out: 输出视频路径 (Path)
    :param cfg: 配置字典
    :param progress_callback: 进度回调 (current_frame, total_frames)
    :param stop_event: 停止事件 (threading.Event)
    :return: 输出路径
    """
    ffmpeg_path = get_ffmpeg_path()
    fps = cfg.get("fps", 30)
    duration = cfg.get("video_duration", 10.0)
    res_str = cfg.get("resolution", "1080x1920")
    try:
        w, h = map(int, res_str.split("x"))
    except Exception:
        import logging; logging.getLogger("CreativeEnginePro").debug("resolution parse failed", exc_info=True)
        w, h = 1080, 1920
    frames_per_img = int(duration * fps / max(len(imgs), 1))
    if frames_per_img < 1:
        frames_per_img = 1

    tf = cfg.get("transition_frames", 15)
    tt = cfg.get("transition_type", "推进放大")
    tfn = TRANSITIONS.get(tt, "zoom_push")

    tmpdir = Path(os.path.dirname(out)) / ".tmpframes"
    tmpdir.mkdir(parents=True, exist_ok=True)
    # 清空临时目录
    for old_file in tmpdir.iterdir():
        try:
            old_file.unlink()
        except Exception:
            import logging; logging.getLogger("CreativeEnginePro").debug("temp frame cleanup failed", exc_info=True)

    fidx = 0
    loaded = 0
    total_frames = len(imgs) * frames_per_img

    for i in range(len(imgs)):
        if stop_event and stop_event.is_set():
            break
        img = cv_imread(imgs[i])
        if img is None:
            continue
        loaded += 1
        img_bgr = resize_fill(img, w, h)

        nxt = None
        if i < len(imgs) - 1:
            nxt = cv_imread(imgs[i + 1])
            if nxt is not None:
                nxt = resize_fill(nxt, w, h)

        t = tf
        for j in range(frames_per_img):
            if stop_event and stop_event.is_set():
                break
            # 转场放在每张图末尾
            if j >= frames_per_img - t and nxt is not None:
                alpha = (j - (frames_per_img - t)) / t
                frame = apply_transition(img_bgr, nxt, alpha, tfn, w, h)
            else:
                frame = img_bgr.copy()
            # 写帧（中文路径兼容）
            result, encoded = cv2.imencode('.png', frame)
            if result:
                with open(tmpdir / f"{fidx:06d}.png", 'wb') as f:
                    f.write(encoded.tobytes())
            fidx += 1
            if progress_callback:
                progress_callback(fidx, total_frames)

    if fidx == 0:
        raise RuntimeError(
            f"未能读取任何图片（{len(imgs)} 张中有 {len(imgs) - loaded} 张读取失败）。"
            f"请检查图片路径是否存在、格式是否支持。")

    out.parent.mkdir(parents=True, exist_ok=True)

    quality_map = {
        "best":    ("libx264", "-crf 12 -preset slower"),
        "high":    ("libx264", "-crf 18 -preset medium"),
        "normal":  ("libx264", "-crf 23 -preset medium"),
        "low":     ("libx264", "-crf 28 -preset fast"),
        "minimal": ("libx264", "-crf 32 -preset ultrafast"),
    }
    codec, qp = quality_map.get(cfg.get("video_quality", "minimal"),
                                 ("libx264", "-crf 32 -preset ultrafast"))

    cmd = [
        ffmpeg_path, "-y", "-framerate", str(fps),
        "-i", str(tmpdir / "%06d.png"),
        "-c:v", codec, *qp.split(),
        "-pix_fmt", "yuv420p",
        str(out)
    ]
    _run_ffmpeg(cmd, stop_event)

    # 清理临时帧
    for f in tmpdir.iterdir():
        try:
            f.unlink()
        except Exception:
            import logging; logging.getLogger("CreativeEnginePro").debug("frame unlink failed", exc_info=True)
    try:
        tmpdir.rmdir()
    except Exception:
        import logging; logging.getLogger("CreativeEnginePro").debug("tmpdir rmdir failed", exc_info=True)

    return out


def mix_audio(video_path, bgm_path, out_path, volume_pct=80, ffmpeg_path=None, stop_event=None):
    """混音：将 BGM 与视频音频混合"""
    if ffmpeg_path is None:
        ffmpeg_path = get_ffmpeg_path()
    vol = volume_pct / 100.0
    dur = get_video_duration(video_path, ffmpeg_path)
    has_audio = _has_audio_stream(video_path, ffmpeg_path)

    if has_audio:
        cmd = [
            ffmpeg_path, "-y",
            "-i", str(video_path),
            "-i", str(bgm_path),
            "-filter_complex",
            f"[1:a]volume={vol},atrim=0:{dur},asetpts=PTS-STARTPTS[a1];"
            f"[0:a][a1]amix=inputs=2:duration=first:dropout_transition=2",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(out_path)
        ]
    else:
        cmd = [
            ffmpeg_path, "-y",
            "-i", str(video_path),
            "-i", str(bgm_path),
            "-filter_complex",
            f"[1:a]volume={vol},atrim=0:{dur},asetpts=PTS-STARTPTS[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(out_path)
        ]
    _run_ffmpeg(cmd, stop_event)
    return out_path


def concat_videos(video_path, endpage_path, out_path, ffmpeg_path=None, stop_event=None):
    """拼接主视频和尾页"""
    if ffmpeg_path is None:
        ffmpeg_path = get_ffmpeg_path()
    has_audio_v = _has_audio_stream(video_path, ffmpeg_path)
    has_audio_e = _has_audio_stream(endpage_path, ffmpeg_path)
    w, h = _get_video_resolution(video_path, ffmpeg_path)

    vf = (f"[1:v]scale={w}:{h}:force_original_aspect_ratio=1,format=yuv420p,setsar=1[ep];"
          f"[0:v]format=yuv420p,setsar=1[ref];"
          f"[ref][ep]concat=n=2:v=1:a=0[outv]")

    if has_audio_v and has_audio_e:
        cmd = [
            ffmpeg_path, "-y",
            "-i", str(video_path), "-i", str(endpage_path),
            "-filter_complex",
            vf + ";"
            "[0:a]aresample=44100[a0];[1:a]aresample=44100[a1];"
            "[a0][a1]concat=n=2:v=0:a=1[outa]",
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-crf", "23", "-preset", "medium",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            str(out_path)
        ]
    elif has_audio_v:
        cmd = [
            ffmpeg_path, "-y",
            "-i", str(video_path), "-i", str(endpage_path),
            "-filter_complex", vf,
            "-map", "[outv]", "-map", "0:a",
            "-c:v", "libx264", "-crf", "23", "-preset", "medium",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            str(out_path)
        ]
    elif has_audio_e:
        cmd = [
            ffmpeg_path, "-y",
            "-i", str(video_path), "-i", str(endpage_path),
            "-filter_complex", vf,
            "-map", "[outv]", "-map", "1:a",
            "-c:v", "libx264", "-crf", "23", "-preset", "medium",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            str(out_path)
        ]
    else:
        cmd = [
            ffmpeg_path, "-y",
            "-i", str(video_path), "-i", str(endpage_path),
            "-filter_complex", vf,
            "-map", "[outv]",
            "-c:v", "libx264", "-crf", "23", "-preset", "medium",
            "-pix_fmt", "yuv420p",
            "-an",
            str(out_path)
        ]

    _run_ffmpeg(cmd, stop_event)
    return out_path
