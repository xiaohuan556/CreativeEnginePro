"""
小欢语音 - Demucs 高质量分离器
通过独立进程运行，monkeypatch torchaudio.save→soundfile 绕过 torchcodec DLL
"""
import subprocess
import sys
import shutil
from pathlib import Path
from typing import Tuple
import os


def separate_demucs(audio_path: str, output_dir: str, model: str = "htdemucs") -> Tuple[str, str]:
    audio = Path(audio_path)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    script = _build_script(audio, out, model)

    print(f"[Demucs] {model} | {audio.name}")

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, timeout=600,
        cwd=str(audio.parent),
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    # decode with utf-8 + replace to handle special chars
    output = result.stdout.decode("utf-8", errors="replace") + "\n" + result.stderr.decode("utf-8", errors="replace")
    print(output.strip()[-500:])

    if result.returncode != 0:
        raise RuntimeError(f"Demucs 失败: {output[-800:]}")

    # 找到输出文件
    v_path = out / "vocals.wav"
    b_path = out / "no_vocals.wav"

    if not v_path.exists():
        raise RuntimeError(f"找不到 vocals.wav")

    return str(v_path), str(b_path)


def _build_script(audio: Path, output_dir: Path, model: str) -> str:
    return f'''
import sys, os
import numpy as np
import soundfile as sf
import torch

sr = 44100
data, _sr = sf.read(r"{str(audio)}", always_2d=True)
if data.ndim > 1:
    data = data.T
if data.shape[0] == 1:
    data = np.concatenate([data, data], axis=0)
elif data.shape[0] > 2:
    data = data[:2]
wav = torch.from_numpy(data.astype(np.float32))

# 加载 demucs 模型
from demucs.pretrained import get_model
from demucs.apply import apply_model

model_name = "{model}"
print(f"加载 {{model_name}}...", flush=True)
model_obj = get_model(name=model_name)
model_obj.cpu()
model_obj.eval()

# 标准化 + 推理
ref = wav.mean(0, keepdim=True)
wav_norm = (wav - ref.mean()) / ref.std()

print("推理中...", flush=True)
sources = apply_model(model_obj, wav_norm[None], device="cpu", shifts=1, split=True, overlap=0.25)
sources = sources * ref.std() + ref.mean()

# sources: [1, stems, channels, samples]
stem_names = model_obj.sources
print(f"音轨: {{stem_names}}", flush=True)

out_dir = r"{str(output_dir)}"
os.makedirs(out_dir, exist_ok=True)

# htdemucs 默认4轨: ['drums','bass','other','vocals']
# htdemucs_ft/6s 可能6轨，但都有 vocals
vocals_idx = None
no_vocals_idxs = []
for idx, stem in enumerate(stem_names):
    if stem == 'vocals':
        vocals_idx = idx
    else:
        no_vocals_idxs.append(idx)

if vocals_idx is None:
    # fallback: 最后一个轨作为人声
    vocals_idx = len(stem_names) - 1
    no_vocals_idxs = list(range(vocals_idx))

# 保存人声
vocals_wav = sources[0, vocals_idx].numpy()
if vocals_wav.ndim > 1 and vocals_wav.shape[0] < vocals_wav.shape[1]:
    vocals_wav = vocals_wav.T
if vocals_wav.ndim == 1:
    vocals_wav = np.column_stack([vocals_wav, vocals_wav])
sf.write(os.path.join(out_dir, "vocals.wav"), vocals_wav, 44100)

# 合并非人声轨为背景音
bgm_wav = None
for idx in no_vocals_idxs:
    part = sources[0, idx].numpy()
    if part.ndim > 1 and part.shape[0] < part.shape[1]:
        part = part.T
    if bgm_wav is None:
        bgm_wav = part
    else:
        # 对齐长度
        n = min(bgm_wav.shape[0], part.shape[0])
        if bgm_wav.ndim == 1 and part.ndim > 1:
            bgm_wav = np.column_stack([bgm_wav[:n], bgm_wav[:n]])
        if part.ndim == 1 and bgm_wav.ndim > 1:
            part = np.column_stack([part[:n], part[:n]])
        bgm_wav = bgm_wav[:n] + part[:n]
if bgm_wav.ndim == 1:
    bgm_wav = np.column_stack([bgm_wav, bgm_wav])
sf.write(os.path.join(out_dir, "no_vocals.wav"), bgm_wav, 44100)

print("DONE", flush=True)
'''
