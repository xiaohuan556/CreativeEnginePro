"""
小欢语音 - Whisper ASR 语音识别器
通过独立子进程运行，绕过 PyTorch c10.dll 在 Microsoft Store Python 3.13 下的加载失败
和 demucs_runner.py 同理：主进程不导入 torch，子进程中加载
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List


def run_whisper_asr(
    audio_path: str,
    output_srt: str = "",
    model_size: str = "small",
    language: str = "",
) -> List[dict]:
    """
    在子进程中运行 Whisper ASR，返回字幕条目列表

    Args:
        audio_path: 输入音频/视频文件路径
        output_srt: SRT 输出路径（空则不保存文件）
        model_size: Whisper 模型大小 (tiny/base/small/medium/large)
                    small 及以上支持中英文精确识别
        language: 源语言代码（空则自动检测）
                  常用: zh=中文, en=英语, ja=日语, ko=韩语, th=泰语,
                        vi=越南语, es=西语, pt=葡语, ar=阿语, id=印尼语

    Returns:
        [{"start": float, "end": float, "text": str}, ...]
    """
    audio = Path(audio_path)
    if not audio.exists():
        raise FileNotFoundError(f"音频文件不存在: {audio_path}")

    # 输出 JSON 临时文件
    out_json = Path(output_srt).with_suffix(".json") if output_srt else Path(
        audio.parent
    ) / f"_{audio.stem}_whisper_result.json"

    script = _build_script(str(audio), str(out_json), model_size, language)

    print(f"[Whisper] {model_size} | {audio.name}")

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        timeout=600,
        cwd=str(audio.parent),
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )

    # 处理输出（中文 Windows GBK 兼容）
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    output = stdout + "\n" + stderr
    # 打印子进程输出（调试用）
    if output.strip():
        print(output.strip()[-500:])

    if result.returncode != 0:
        raise RuntimeError(f"Whisper ASR 失败: {output[-800:]}")

    # 读取 JSON 结果
    if not out_json.exists():
        raise RuntimeError("Whisper 未生成结果文件")

    with open(out_json, encoding="utf-8") as f:
        entries = json.load(f)

    # 保存 SRT 文件
    if output_srt:
        _save_srt(entries, output_srt)

    return entries


def _build_script(audio_path: str, out_json: str, model_size: str, language: str) -> str:
    # 将语言参数写入子进程脚本：空字符串 → None（触发自动检测），否则传代码
    lang_val = f'"{language}"' if language else "None"
    return f'''
import json
import sys

# 加载 Whisper
try:
    import whisper
except ImportError:
    print("ERROR: whisper not installed. pip install openai-whisper", file=sys.stderr)
    sys.exit(1)

model_size = "{model_size}"
language = {lang_val}
audio_path = r"{audio_path}"
out_json = r"{out_json}"

print(f"加载 Whisper 模型: {{model_size}}...", flush=True)
model = whisper.load_model(model_size)

# 先检测语言（未指定时）
if language is None:
    try:
        mel = whisper.log_mel_spectrogram(whisper.load_audio(audio_path)).to(model.device)[:,:3000]
        _, probs = model.detect_language(mel)
        detected = max(probs, key=probs.get)
        print(f"自动检测语言: {{detected}} (置信度: {{probs[detected]:.2f}})", flush=True)
        language = detected
    except Exception as e:
        print(f"语言检测失败: {{e}}，使用默认英语", flush=True)
        language = "en"

print(f"开始转写（语言: {{language}}）...", flush=True)
result = model.transcribe(
    audio_path,
    language=language,
    verbose=False,
    word_timestamps=True
)

# 提取字幕条目（含词级时间戳）
entries = []
for i, seg in enumerate(result.get("segments", []), 1):
    text = seg["text"].strip()
    start = seg["start"]
    end = seg["end"]
    # 过滤空白条目（不过滤时长，短句也保留）
    if text:
        words_data = []
        for w in seg.get("words", []):
            w_text = w.get("word", "").strip()
            if w_text:
                words_data.append({{
                    "word": w_text,
                    "start": w.get("start", 0),
                    "end": w.get("end", 0)
                }})
        entries.append({{
            "start": start, "end": end, "text": text,
            "words": words_data
        }})

# 保存 JSON
with open(out_json, "w", encoding="utf-8") as f:
    json.dump(entries, f, ensure_ascii=False, indent=2)

print(f"转写完成: {{len(entries)}} 条字幕", flush=True)
print("DONE", flush=True)
'''


def _save_srt(entries: List[dict], srt_path: str):
    """将条目保存为 SRT 文件"""
    lines = []
    for i, e in enumerate(entries, 1):
        lines.append(str(i))
        lines.append(f"{_srt_time(e['start'])} --> {_srt_time(e['end'])}")
        lines.append(e["text"])
        lines.append("")
    Path(srt_path).write_text("\n".join(lines), encoding="utf-8")


def _srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
