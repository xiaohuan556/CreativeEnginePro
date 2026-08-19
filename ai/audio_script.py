"""画布配音脚本协议：停顿、自然语气词与跨 TTS Provider 参数。"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

_INTERJECTIONS = {
    "叹气": "唉……", "轻笑": "呵呵……", "大笑": "哈哈哈！",
    "吸气": "嘶……", "犹豫": "嗯……", "惊讶": "啊？", "清嗓": "咳、咳。",
}


def parse_audio_script(text: str):
    """返回 phrase/pause 片段；[语气:x] 是元数据，不会被朗读。"""
    emotion = ""
    parts = []
    pattern = re.compile(r"\[(停顿|pause)\s*[:：]?\s*([0-9.]+)\s*(?:s|秒)?\]|"
                         r"\[(语气|emotion)\s*[:：]\s*([^\]]+)\]|"
                         r"\[([^\]]+)\]", re.I)
    cursor = 0
    for match in pattern.finditer(text or ""):
        if match.start() > cursor:
            value = text[cursor:match.start()].strip()
            if value:
                parts.append({"type":"phrase", "text":value})
        if match.group(1):
            parts.append({"type":"pause", "seconds":max(0.05, min(10.0, float(match.group(2))))})
        elif match.group(3):
            emotion = str(match.group(4) or "").strip()
        else:
            token = str(match.group(5) or "").strip()
            value = _INTERJECTIONS.get(token, "")
            if value:
                parts.append({"type":"phrase", "text":value, "interjection":token})
        cursor = match.end()
    tail = (text or "")[cursor:].strip()
    if tail:
        parts.append({"type":"phrase", "text":tail})
    return parts or [{"type":"phrase", "text":str(text or "").strip()}], emotion


def spoken_text(text: str):
    """不支持精确停顿的 Provider 使用；保留自然语气词，停顿转为标点。"""
    parts, emotion = parse_audio_script(text)
    value = "".join(item.get("text", "") if item["type"] == "phrase" else
                    ("……" if item.get("seconds", 0) < 1 else "…………") for item in parts)
    return value, emotion


def synthesize_with_real_pauses(text: str, output_path: Path, synth_phrase):
    """逐段 TTS 并用 FFmpeg 插入真实静音；synth_phrase(text, path) 负责音色 API。"""
    parts, emotion = parse_audio_script(text)
    if not any(item["type"] == "pause" for item in parts):
        phrase = "".join(item.get("text", "") for item in parts if item["type"] == "phrase")
        return synth_phrase(phrase, output_path), emotion
    from utils.ffmpeg_utils import get_ffmpeg_path
    ffmpeg = get_ffmpeg_path()
    folder = Path(tempfile.mkdtemp(prefix="cep_tts_script_"))
    segments = []
    for index, item in enumerate(parts):
        target = folder / f"segment_{index:03d}.mp3"
        if item["type"] == "pause":
            subprocess.run([ffmpeg, "-y", "-f", "lavfi", "-i",
                            "anullsrc=r=44100:cl=stereo", "-t",
                            f"{float(item['seconds']):.3f}", "-q:a", "4", str(target)],
                           capture_output=True, timeout=30, check=True)
        else:
            synth_phrase(str(item.get("text") or ""), target)
        if target.exists():
            segments.append(target)
    list_file = folder / "concat.txt"
    list_file.write_text("\n".join(
        "file '" + str(path).replace("'", "'\\''") + "'" for path in segments), encoding="utf-8")
    subprocess.run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
                    "-c:a", "libmp3lame", "-q:a", "2", str(output_path)],
                   capture_output=True, timeout=120, check=True)
    return output_path, emotion
