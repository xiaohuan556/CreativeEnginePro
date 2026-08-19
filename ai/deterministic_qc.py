"""Deterministic media QC signals that complement multimodal review."""
from __future__ import annotations

import math
import os
from pathlib import Path
import subprocess
import tempfile
import wave


def cosine_similarity(left, right) -> float:
    """Compare face/identity embeddings supplied by any local vision backend."""
    try:
        import numpy as np
        a, b = np.asarray(left, dtype=np.float64).reshape(-1), np.asarray(right, dtype=np.float64).reshape(-1)
        if a.size == 0 or a.shape != b.shape:
            return 0.0
        denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
        return round(float(np.dot(a, b) / denominator), 6) if denominator else 0.0
    except (ImportError, TypeError, ValueError):
        return 0.0


def histogram_similarity(left, right, bins: int = 16) -> float:
    """Compare clothing/color crops without binding QC to one face model."""
    try:
        import numpy as np
        a, b = np.asarray(left), np.asarray(right)
        if a.size == 0 or b.size == 0:
            return 0.0
        channels = 1 if a.ndim < 3 else min(3, a.shape[-1], b.shape[-1])
        scores = []
        for channel in range(channels):
            av = a if channels == 1 else a[..., channel]
            bv = b if channels == 1 else b[..., channel]
            ah, _ = np.histogram(av, bins=bins, range=(0, 256), density=True)
            bh, _ = np.histogram(bv, bins=bins, range=(0, 256), density=True)
            denominator = float(np.linalg.norm(ah) * np.linalg.norm(bh))
            scores.append(float(np.dot(ah, bh) / denominator) if denominator else 0.0)
        return round(sum(scores) / len(scores), 6)
    except (ImportError, TypeError, ValueError):
        return 0.0


def screen_motion_direction(centers: list[tuple[float, float]], dead_zone: float = 0.02) -> str:
    """Classify tracked subject centers into continuity-friendly screen direction."""
    if len(centers or []) < 2:
        return "unknown"
    dx = float(centers[-1][0]) - float(centers[0][0])
    dy = float(centers[-1][1]) - float(centers[0][1])
    if abs(dx) < dead_zone and abs(dy) < dead_zone:
        return "static"
    if abs(dx) >= abs(dy):
        return "right" if dx > 0 else "left"
    return "down" if dy > 0 else "up"


def av_sync_offset(audio_envelope, visual_mouth_motion, sample_hz: float) -> dict:
    """Estimate audio/visual lead-lag from equally sampled activity envelopes."""
    try:
        import numpy as np
        audio = np.asarray(audio_envelope, dtype=np.float64).reshape(-1)
        visual = np.asarray(visual_mouth_motion, dtype=np.float64).reshape(-1)
        size = min(audio.size, visual.size)
        if size < 3 or sample_hz <= 0:
            return {"status":"insufficient", "offset_ms":0}
        audio, visual = audio[:size] - audio[:size].mean(), visual[:size] - visual[:size].mean()
        correlation = np.correlate(audio, visual, mode="full")
        lag = int(np.argmax(correlation) - (size - 1))
        offset = lag / sample_hz * 1000.0
        return {"status":"fail" if abs(offset) > 120 else "pass",
                "offset_ms":round(offset, 2),
                "issue":"AV_SYNC_OFFSET" if abs(offset) > 120 else ""}
    except (ImportError, TypeError, ValueError):
        return {"status":"unavailable", "offset_ms":0}


def extract_audio_envelope(video_path: str, sample_hz: float = 12.5,
                           ffmpeg_path: str = "") -> dict:
    """Decode the real video soundtrack and return an RMS speech envelope."""
    try:
        import numpy as np
        if not ffmpeg_path:
            from utils.ffmpeg_utils import get_ffmpeg_path
            ffmpeg_path = get_ffmpeg_path()
        with tempfile.TemporaryDirectory(prefix="cep_avsync_") as folder:
            wav_path = str(Path(folder) / "audio.wav")
            process = subprocess.run(
                [ffmpeg_path, "-y", "-i", str(video_path), "-vn", "-ac", "1",
                 "-ar", "16000", "-acodec", "pcm_s16le", wav_path],
                capture_output=True, timeout=60, check=False)
            if process.returncode != 0 or not os.path.exists(wav_path):
                return {"status":"no_audio", "envelope":[], "sample_hz":sample_hz}
            with wave.open(wav_path, "rb") as stream:
                rate = stream.getframerate()
                samples = np.frombuffer(stream.readframes(stream.getnframes()), dtype=np.int16)
        if samples.size == 0:
            return {"status":"no_audio", "envelope":[], "sample_hz":sample_hz}
        samples = samples.astype(np.float64) / 32768.0
        window = max(1, int(rate / sample_hz))
        envelope = [float(np.sqrt(np.mean(samples[index:index + window] ** 2)))
                    for index in range(0, samples.size, window)
                    if samples[index:index + window].size]
        return {"status":"pass", "envelope":envelope, "sample_hz":sample_hz,
                "sample_rate":rate, "duration":round(samples.size / rate, 3)}
    except (ImportError, OSError, subprocess.SubprocessError, wave.Error, ValueError) as error:
        return {"status":"unavailable", "envelope":[], "sample_hz":sample_hz,
                "reason":str(error)[:160]}


def extract_mouth_motion(video_path: str, sample_hz: float = 12.5,
                         max_seconds: float = 30.0) -> dict:
    """Track mouth ROI activity from continuous frames using MediaPipe or Haar fallback."""
    try:
        import cv2
        import numpy as np
        capture = cv2.VideoCapture(str(video_path))
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 25.0)
        stride = max(1, int(round(fps / sample_hz)))
        limit = int(max_seconds * fps)
        cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        values, centers = [], []
        previous_mouth = None
        frame_index = 0
        while frame_index < limit:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % stride:
                frame_index += 1
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            detected = cascade.detectMultiScale(gray, 1.12, 4, minSize=(32, 32))
            motion = 0.0
            if len(detected):
                x, y, w, h = max(detected, key=lambda box:box[2] * box[3])
                mouth = gray[y + h // 2:y + h, x:x + w]
                mouth = cv2.resize(mouth, (64, 32))
                centers.append(((x + w / 2) / frame.shape[1],
                                (y + h / 2) / frame.shape[0]))
                if previous_mouth is not None:
                    motion = float(np.mean(cv2.absdiff(previous_mouth, mouth))) / 255.0
                previous_mouth = mouth
            values.append(motion)
            frame_index += 1
        capture.release()
        if len(values) < 3:
            return {"status":"insufficient", "motion":values, "sample_hz":sample_hz}
        return {"status":"pass", "motion":values, "sample_hz":sample_hz,
                "screen_motion":screen_motion_direction(centers),
                "tracked_frames":len(centers), "sampled_frames":len(values)}
    except Exception as error:
        return {"status":"unavailable", "motion":[], "sample_hz":sample_hz,
                "reason":str(error)[:160]}


def _resample(values, size: int):
    import numpy as np
    data = np.asarray(values, dtype=np.float64)
    if data.size == size:
        return data
    if data.size < 2 or size < 2:
        return np.zeros(max(0, size), dtype=np.float64)
    return np.interp(np.linspace(0, data.size - 1, size),
                     np.arange(data.size), data)


def run_syncnet_onnx(audio_envelope, mouth_motion, model_path: str = "") -> dict:
    """Optional learned sync discriminator. Requires a compatible two-input ONNX model."""
    default_model = Path(__file__).parents[1] / "models" / "syncnet.onnx"
    path = str(model_path or os.environ.get("CEP_SYNCNET_ONNX", "") or
               (default_model if default_model.exists() else "")).strip()
    if not path or not os.path.exists(path):
        return {"status":"unavailable", "reason":"SYNCNET_MODEL_NOT_CONFIGURED"}
    try:
        import numpy as np
        import onnxruntime as ort
        size = min(len(audio_envelope), len(mouth_motion))
        if size < 5:
            return {"status":"insufficient", "reason":"SYNCNET_SIGNAL_TOO_SHORT"}
        audio = _resample(audio_envelope, size).astype(np.float32)[None, None, :]
        visual = _resample(mouth_motion, size).astype(np.float32)[None, None, :]
        session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        inputs = session.get_inputs()
        if len(inputs) != 2:
            return {"status":"unavailable", "reason":"SYNCNET_ONNX_REQUIRES_TWO_INPUTS"}
        output = session.run(None, {inputs[0].name:audio, inputs[1].name:visual})[0]
        confidence = float(np.asarray(output).reshape(-1)[0])
        if confidence < 0 or confidence > 1:
            confidence = 1.0 / (1.0 + math.exp(-confidence))
        return {"status":"pass" if confidence >= 0.5 else "fail",
                "confidence":round(confidence, 5),
                "issue":"SYNCNET_MISMATCH" if confidence < 0.5 else ""}
    except Exception as error:
        return {"status":"unavailable", "reason":str(error)[:200]}


def inspect_av_sync(video_path: str, *, syncnet_model: str = "",
                    sample_hz: float = 12.5) -> dict:
    audio = extract_audio_envelope(video_path, sample_hz)
    mouth = extract_mouth_motion(video_path, sample_hz)
    if audio.get("status") != "pass":
        return {"status":audio.get("status"), "audio":audio, "mouth":mouth,
                "syncnet":{"status":"unavailable", "reason":"NO_AUDIO_SIGNAL"},
                "issues":[]}
    if mouth.get("status") != "pass":
        return {"status":mouth.get("status"), "audio":audio, "mouth":mouth,
                "syncnet":{"status":"unavailable", "reason":"NO_MOUTH_TRACK"},
                "issues":[]}
    size = min(len(audio["envelope"]), len(mouth["motion"]))
    audio_signal = _resample(audio["envelope"], size)
    mouth_signal = _resample(mouth["motion"], size)
    offset = av_sync_offset(audio_signal, mouth_signal, sample_hz)
    syncnet = run_syncnet_onnx(audio_signal, mouth_signal, syncnet_model)
    issues = []
    if offset.get("status") == "fail":
        issues.append("AV_SYNC_OFFSET")
    if syncnet.get("status") == "fail":
        issues.append("SYNCNET_MISMATCH")
    return {"status":"fail" if issues else "pass", "issues":issues,
            "offset":offset, "syncnet":syncnet,
            "audio":{"status":audio.get("status"), "duration":audio.get("duration")},
            "mouth":{"status":mouth.get("status"),
                     "tracked_frames":mouth.get("tracked_frames"),
                     "sampled_frames":mouth.get("sampled_frames")}}


def frame_metrics(frames) -> dict:
    """Measure freezes, cuts/flicker and luminance drift from RGB/gray arrays."""
    try:
        import numpy as np
    except ImportError:
        return {"status":"unavailable", "issues":["NUMPY_UNAVAILABLE"]}
    values = [np.asarray(frame, dtype=np.float32) for frame in frames or []]
    if len(values) < 2:
        return {"status":"insufficient", "issues":["FRAMES_INSUFFICIENT"]}
    means = [float(value.mean()) for value in values]
    diffs = [float(np.mean(np.abs(right - left)))
             for left, right in zip(values, values[1:]) if left.shape == right.shape]
    if not diffs:
        return {"status":"invalid", "issues":["FRAME_SHAPE_MISMATCH"]}
    freeze_ratio = sum(delta < 0.35 for delta in diffs) / len(diffs)
    spikes = [index + 1 for index, delta in enumerate(diffs)
              if delta > max(28.0, sum(diffs) / len(diffs) * 3.0)]
    luminance_drift = max(means) - min(means)
    issues = []
    if freeze_ratio > 0.65:
        issues.append("FREEZE_FRAME")
    if len(spikes) > max(1, len(diffs) // 8):
        issues.append("TEMPORAL_FLICKER")
    if luminance_drift > 55:
        issues.append("LIGHTING_DRIFT")
    return {"status":"fail" if issues else "pass", "issues":issues,
            "freeze_ratio":round(freeze_ratio, 4), "flicker_spikes":spikes,
            "luminance_drift":round(luminance_drift, 3)}


def inspect_frame_paths(paths: list[str]) -> dict:
    frames = []
    try:
        from PIL import Image
        for path in paths or []:
            with Image.open(path) as image:
                frames.append(image.convert("RGB").resize((320, 180)))
    except (ImportError, OSError):
        return {"status":"unavailable", "issues":["FRAME_READ_FAILED"]}
    result = frame_metrics(frames)
    try:
        import cv2
        import numpy as np
        arrays = [np.asarray(frame, dtype=np.uint8) for frame in frames]
        cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces, clothes, centers, mouths = [], [], [], []
        previous = None
        for image in arrays:
            gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
            detected = cascade.detectMultiScale(gray, 1.12, 4, minSize=(24, 24))
            if len(detected):
                x, y, w, h = max(detected, key=lambda box:box[2] * box[3])
                face = gray[y:y + h, x:x + w]
                faces.append(cv2.resize(face, (16, 16)).reshape(-1).astype(float).tolist())
                clothing = image[min(image.shape[0], y + h):min(image.shape[0], y + 3 * h),
                                 max(0, x - w // 2):min(image.shape[1], x + 3 * w // 2)]
                if clothing.size:
                    clothes.append(clothing)
                mouth = face[h // 2:, :]
                mouths.append(float(np.std(mouth)))
                centers.append(((x + w / 2) / image.shape[1],
                                (y + h / 2) / image.shape[0]))
            elif previous is not None:
                delta = cv2.absdiff(previous, gray)
                points = np.argwhere(delta > 20)
                if points.size:
                    centers.append((float(points[:, 1].mean() / gray.shape[1]),
                                    float(points[:, 0].mean() / gray.shape[0])))
            previous = gray
        identity_scores = [cosine_similarity(faces[0], value) for value in faces[1:]]
        clothing_scores = [histogram_similarity(clothes[0], value) for value in clothes[1:]]
        identity = min(identity_scores) if identity_scores else None
        clothing = min(clothing_scores) if clothing_scores else None
        warnings = []
        if identity is not None and identity < 0.72:
            warnings.append("FACE_IDENTITY_WEAK_EVIDENCE")
        if clothing is not None and clothing < 0.62:
            warnings.append("CLOTHING_COLOR_WEAK_EVIDENCE")
        # Haar crops are not identity embeddings. Only a strong, repeated,
        # cross-signal disagreement is safe enough to become a blocker.
        if (len(faces) >= 3 and identity is not None and clothing is not None and
                identity < 0.55 and clothing < 0.45):
            result.setdefault("issues", []).append("SUBJECT_APPEARANCE_DRIFT")
        result.update({
            "face_frames":len(faces), "identity_similarity":identity,
            "clothing_similarity":clothing,
            "screen_motion":screen_motion_direction(centers),
            "mouth_motion":mouths,
            "warnings":warnings,
        })
        if result.get("issues"):
            result["status"] = "fail"
    except Exception:
        result["feature_extractor"] = "unavailable"
    return result


def compare_endpoint_paths(left_path: str, right_path: str) -> dict:
    """Compare adjacent real tail/head frames for identity, clothing and light."""
    try:
        from PIL import Image
        import numpy as np
        left = np.asarray(Image.open(left_path).convert("RGB").resize((320, 180)))
        right = np.asarray(Image.open(right_path).convert("RGB").resize((320, 180)))
    except (ImportError, OSError, ValueError):
        return {"status":"unavailable", "issues":["ENDPOINT_READ_FAILED"]}
    color = histogram_similarity(left, right)
    light_delta = abs(float(left.mean()) - float(right.mean()))
    issues = []
    if color < 0.45:
        issues.append("ENDPOINT_APPEARANCE_DRIFT")
    if light_delta > 48:
        issues.append("ENDPOINT_LIGHTING_DRIFT")
    return {"status":"fail" if issues else "pass", "issues":issues,
            "appearance_similarity":color, "luminance_delta":round(light_delta, 3)}


def compare_fixed_regions(reference_path: str, candidate_path: str,
                          editable_bbox=None, protected_bboxes=None,
                          threshold: float = 0.62) -> dict:
    """Compare static scene edges while ignoring the permitted action region.

    Edge geometry is intentionally used instead of RGB difference so a valid
    day/night or practical-light change does not look like moved furniture.
    """
    try:
        import cv2
        import numpy as np
        from PIL import Image
        left = np.asarray(Image.open(reference_path).convert("RGB").resize((512, 288)))
        right = np.asarray(Image.open(candidate_path).convert("RGB").resize((512, 288)))
        lg = cv2.cvtColor(left, cv2.COLOR_RGB2GRAY)
        rg = cv2.cvtColor(right, cv2.COLOR_RGB2GRAY)
        le = cv2.Canny(lg, 55, 150) > 0
        re = cv2.Canny(rg, 55, 150) > 0
        static = np.ones(le.shape, dtype=bool)
        values = list(editable_bbox or []) if isinstance(editable_bbox, (list, tuple)) else []
        if len(values) == 4:
            x, y, w, h = [max(0.0, min(1.0, float(value))) for value in values]
            x1, y1 = int(x * 512), int(y * 288)
            x2, y2 = int(min(1.0, x + w) * 512), int(min(1.0, y + h) * 288)
            static[max(0, y1):max(0, y2), max(0, x1):max(0, x2)] = False
        for protected in protected_bboxes or []:
            values = list(protected or []) if isinstance(protected, (list, tuple)) else []
            if len(values) != 4:
                continue
            x, y, w, h = [max(0.0, min(1.0, float(value))) for value in values]
            x1, y1 = int(x * 512), int(y * 288)
            x2, y2 = int(min(1.0, x + w) * 512), int(min(1.0, y + h) * 288)
            static[max(0, y1):max(0, y2), max(0, x1):max(0, x2)] = True
        le &= static; re &= static
        if protected_bboxes and (int(le.sum()) < 80 or int(re.sum()) < 80):
            return {
                "status":"fail", "issues":["FIXED_SCENE_GEOMETRY_DRIFT"],
                "reason":"DECLARED_FIXTURE_EDGES_MISSING",
                "reference_edge_count":int(le.sum()),
                "candidate_edge_count":int(re.sum()),
                "threshold":threshold,
            }
        if int(le.sum()) < 80 or int(re.sum()) < 80:
            return {"status":"insufficient", "issues":["FIXED_EDGES_INSUFFICIENT"]}
        ld = cv2.distanceTransform((~le).astype(np.uint8), cv2.DIST_L2, 3)
        rd = cv2.distanceTransform((~re).astype(np.uint8), cv2.DIST_L2, 3)
        distance = (float(rd[le].mean()) + float(ld[re].mean())) / 2.0
        distance_score = max(0.0, 1.0 - distance / 10.0)
        density_ratio = min(float(le.sum()), float(re.sum())) / max(float(le.sum()), float(re.sum()))
        score = round(distance_score * 0.78 + density_ratio * 0.22, 4)
        issues = ["FIXED_SCENE_GEOMETRY_DRIFT"] if score < threshold else []
        return {"status":"fail" if issues else "pass", "issues":issues,
                "fixed_structure_similarity":score,
                "mean_edge_distance":round(distance, 3),
                "edge_density_ratio":round(density_ratio, 4),
                "threshold":threshold}
    except Exception as error:
        return {"status":"unavailable", "issues":["FIXED_REGION_QC_UNAVAILABLE"],
                "reason":str(error)[:160]}


def subtitle_safe_area(boxes: list[dict], width: int, height: int,
                       margin: float = 0.08) -> dict:
    problems = []
    left, top = width * margin, height * margin
    right, bottom = width * (1 - margin), height * (1 - margin)
    for index, box in enumerate(boxes or []):
        x, y = float(box.get("x") or 0), float(box.get("y") or 0)
        w, h = float(box.get("width") or 0), float(box.get("height") or 0)
        if x < left or y < top or x + w > right or y + h > bottom:
            problems.append(index)
    return {"status":"fail" if problems else "pass", "unsafe_boxes":problems,
            "safe_margin":margin}


def audio_metrics(samples, sample_rate: int, *, silence_db: float = -50.0) -> dict:
    try:
        import numpy as np
    except ImportError:
        return {"status":"unavailable", "issues":["NUMPY_UNAVAILABLE"]}
    signal = np.asarray(samples, dtype=np.float64).reshape(-1)
    if signal.size == 0 or sample_rate <= 0:
        return {"status":"invalid", "issues":["AUDIO_EMPTY"]}
    peak = float(np.max(np.abs(signal)))
    rms = float(np.sqrt(np.mean(signal ** 2)))
    rms_db = 20 * math.log10(max(rms, 1e-12))
    issues = []
    if peak >= 0.999:
        issues.append("AUDIO_CLIPPING")
    if rms_db < silence_db:
        issues.append("AUDIO_SILENT")
    return {"status":"fail" if issues else "pass", "issues":issues,
            "peak":round(peak, 6), "rms_db":round(rms_db, 3),
            "duration":round(signal.size / sample_rate, 3)}
