"""Local first-pass video breakdown for the production canvas."""
from __future__ import annotations

from pathlib import Path


def _direction(dx: float, dy: float, threshold: float = 0.025) -> str:
    horizontal = "向右" if dx > threshold else "向左" if dx < -threshold else ""
    vertical = "向下" if dy > threshold else "向上" if dy < -threshold else ""
    return horizontal + vertical if horizontal or vertical else "基本静止"


def _trajectory_analysis(samples) -> dict:
    """Estimate global camera transform and residual subject-motion trajectory."""
    import cv2
    import numpy as np
    if len(samples) < 2:
        return {"camera_motion":"无法判断", "subject_trajectory":"无法判断",
                "trajectory_confidence":0.0}
    grays = [cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (320, 180))
             for frame in samples]
    global_moves, scales, rotations, local_centres = [], [], [], []
    for previous, current in zip(grays, grays[1:]):
        points = cv2.goodFeaturesToTrack(
            previous, maxCorners=240, qualityLevel=0.015, minDistance=6)
        matrix = None
        if points is not None and len(points) >= 8:
            tracked, status, _error = cv2.calcOpticalFlowPyrLK(
                previous, current, points, None,
                winSize=(21, 21), maxLevel=3,
                criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))
            if tracked is not None and status is not None:
                good_old = points[status.ravel() == 1]
                good_new = tracked[status.ravel() == 1]
                if len(good_old) >= 8:
                    matrix, _inliers = cv2.estimateAffinePartial2D(
                        good_old, good_new, method=cv2.RANSAC,
                        ransacReprojThreshold=2.5)
        if matrix is None:
            matrix = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        dx, dy = float(matrix[0, 2]) / 320.0, float(matrix[1, 2]) / 180.0
        scale = float((matrix[0, 0] ** 2 + matrix[0, 1] ** 2) ** 0.5)
        rotation = float(np.degrees(np.arctan2(matrix[1, 0], matrix[0, 0])))
        global_moves.append((dx, dy)); scales.append(scale); rotations.append(rotation)

        stabilized = cv2.warpAffine(previous, matrix, (320, 180))
        residual = cv2.absdiff(stabilized, current)
        threshold = max(18.0, float(np.percentile(residual, 86)))
        mask = (residual >= threshold).astype(np.uint8) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        moments = cv2.moments(mask)
        if moments["m00"] > 2000:
            local_centres.append((moments["m10"] / moments["m00"] / 320.0,
                                  moments["m01"] / moments["m00"] / 180.0))
    mean_dx = float(np.median([value[0] for value in global_moves]))
    mean_dy = float(np.median([value[1] for value in global_moves]))
    mean_scale = float(np.median(scales))
    mean_rotation = float(np.median(rotations))
    if mean_scale > 1.012:
        camera = "推镜/画面整体放大"
    elif mean_scale < 0.988:
        camera = "拉镜/画面整体缩小"
    elif abs(mean_dx) > abs(mean_dy) and abs(mean_dx) > 0.012:
        camera = "向左摇或横移" if mean_dx > 0 else "向右摇或横移"
    elif abs(mean_dy) > 0.012:
        camera = "向上摇或升镜" if mean_dy > 0 else "向下摇或降镜"
    elif abs(mean_rotation) > 0.7:
        camera = f"旋转运镜约 {mean_rotation:+.1f}°/采样间隔"
    else:
        camera = "固定机位或极轻微漂移"
    if len(local_centres) >= 2:
        start, end = local_centres[0], local_centres[-1]
        subject = (_direction(end[0] - start[0], end[1] - start[1]) +
                   f"；运动中心由({start[0]:.2f},{start[1]:.2f})到"
                   f"({end[0]:.2f},{end[1]:.2f})")
        confidence = min(1.0, len(local_centres) / max(1, len(samples) - 1))
    else:
        subject, confidence = "没有分离出稳定的局部主体轨迹", 0.25
    return {"camera_motion":camera, "subject_trajectory":subject,
            "trajectory_confidence":confidence,
            "global_flow":{"dx":mean_dx, "dy":mean_dy,
                           "scale":mean_scale, "rotation_deg":mean_rotation}}


def analyze_video(path: str, output_dir: str) -> dict:
    import cv2
    import numpy as np
    from core.scene_detector import detect_scene_changes
    from utils.ffmpeg_utils import get_ffmpeg_path

    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(path)
    cap = cv2.VideoCapture(str(source))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = frames / fps if frames > 0 else 0.0
    cap.release()
    if duration <= 0:
        raise RuntimeError("无法读取视频时长")
    cuts = detect_scene_changes(
        str(source), ffmpeg_path=get_ffmpeg_path(), source_start=0,
        source_end=duration, threshold=0.30, min_length=0.65,
        filter_flashes=True)
    boundaries = [0.0] + [float(row["time"]) for row in cuts] + [duration]
    folder = Path(output_dir); folder.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(source))
    shots = []
    try:
        for index, (start, end) in enumerate(zip(boundaries, boundaries[1:]), 1):
            samples = []
            for ratio in (0.05, 0.18, 0.31, 0.44, 0.57, 0.70, 0.83, 0.95):
                timestamp = start + (end - start) * ratio
                cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000.0)
                ok, frame = cap.read()
                if ok and frame is not None:
                    samples.append(frame)
            keyframe = ""
            if samples:
                keyframe = str(folder / f"shot_{index:03d}.jpg")
                cv2.imencode(".jpg", samples[len(samples) // 2],
                             [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tofile(keyframe)
            motion = 0.0
            if len(samples) >= 2:
                left = cv2.resize(cv2.cvtColor(samples[0], cv2.COLOR_BGR2GRAY), (160, 90))
                right = cv2.resize(cv2.cvtColor(samples[-1], cv2.COLOR_BGR2GRAY), (160, 90))
                motion = float(np.mean(cv2.absdiff(left, right))) / 255.0
            motion_label = ("低运动/可能固定机位" if motion < 0.08 else
                            "中等运动/可能有主体动作或缓慢运镜" if motion < 0.18 else
                            "高运动/快速动作或明显运镜")
            trajectory = _trajectory_analysis(samples)
            shots.append({"number":index, "start":start, "end":end,
                          "duration":end - start, "keyframe":keyframe,
                          "motion_score":motion, "motion_label":motion_label,
                          **trajectory})
    finally:
        cap.release()
    average = duration / max(1, len(shots))
    rhythm = "快节奏" if average < 2.2 else "中等节奏" if average < 4.5 else "慢节奏"
    return {"source":str(source), "duration":duration, "shot_count":len(shots),
            "average_shot_length":average, "rhythm":rhythm, "shots":shots}
