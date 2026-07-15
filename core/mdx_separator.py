"""
小欢语音 - MDX-Net ONNX 纯推理模块
基于 UVR MDX-Net 模型，ONNX Runtime + librosa，零 PyTorch 依赖
"""
import numpy as np
import onnxruntime as ort
import librosa
import soundfile as sf
from pathlib import Path
from typing import Tuple
import tempfile
import os


class MDXNetSeparator:
    """纯 ONNX 人声分离器"""

    MODEL_URLS = [
        "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/UVR-MDX-NET-Inst_HQ_3.onnx",
        "https://raw.githubusercontent.com/TRvlvr/model_repo/main/UVR-MDX-NET-Inst_HQ_3.onnx",
    ]

    def __init__(self, model_path: str = None):
        self._model_path = model_path or self._ensure_model()
        self._session = None
        # 模型参数（从 ONNX 输入 shape 推导）
        self._n_fft = 6144
        self._hop = 1024
        self._dim_f = 3072   # 模型频率维度（n_fft//2近似）
        self._dim_t = 256    # 模型时间维度（chunk size）

    def _ensure_model(self) -> str:
        model_dir = Path(tempfile.gettempdir()) / "audio-sep-models"
        model_dir.mkdir(parents=True, exist_ok=True)
        model_file = model_dir / "UVR-MDX-NET-Inst_HQ_3.onnx"

        if not model_file.exists() or model_file.stat().st_size < 10 * 1024 * 1024:
            print(f"[MDX-Net] 下载模型 {model_file.name} (约63MB)...")
            self._download_model(str(model_file))

        return str(model_file)

    def _download_model(self, dest: str):
        import urllib.request

        for url in self.MODEL_URLS:
            try:
                print(f"[MDX-Net] 下载 {url[:70]}...")
                self._try_download(url, dest)
                if Path(dest).stat().st_size > 10 * 1024 * 1024:
                    return
            except Exception as e:
                print(f"[MDX-Net] 失败: {e}")
        raise RuntimeError("模型下载失败，请手动下载")

    def _try_download(self, url: str, dest: str):
        import urllib.request

        last = [0]

        def report(block_num, block_size, total_size):
            pct = int(block_num * block_size * 100 / total_size) if total_size > 0 else 0
            if pct - last[0] >= 15:
                print(f"  {pct}%", end="", flush=True)
                last[0] = pct

        urllib.request.urlretrieve(url, dest, reporthook=report)
        if last[0] > 0:
            print("  100%")

    def _load_session(self):
        if self._session is None:
            opts = ort.SessionOptions()
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self._session = ort.InferenceSession(
                self._model_path, opts,
                providers=["CPUExecutionProvider"],
            )

    def separate(self, audio_path: str, output_dir: str = None) -> Tuple[str, str]:
        """分离人声和背景音，返回 (vocals_path, bgm_path)"""
        output_dir = output_dir or str(Path(audio_path).parent)
        self._load_session()
        print(f"[MDX-Net] 加载: {Path(audio_path).name}")

        # 加载立体声音频
        mixture, sr = librosa.load(audio_path, sr=44100, mono=False)
        if mixture.ndim == 1:
            mixture = np.stack([mixture, mixture], axis=0)
        print(f"[MDX-Net] {mixture.shape[1]/sr:.1f}s")

        # STFT
        spec_L = librosa.stft(mixture[0], n_fft=self._n_fft, hop_length=self._hop)
        spec_R = librosa.stft(mixture[1], n_fft=self._n_fft, hop_length=self._hop)

        # 截断到模型频率维度
        if spec_L.shape[0] > self._dim_f:
            spec_L = spec_L[:self._dim_f, :]
            spec_R = spec_R[:self._dim_f, :]
        elif spec_L.shape[0] < self._dim_f:
            pad_h = self._dim_f - spec_L.shape[0]
            spec_L = np.pad(spec_L, ((0, pad_h), (0, 0)))
            spec_R = np.pad(spec_R, ((0, pad_h), (0, 0)))

        # 构建 4 通道输入: [batch, 4, freq, time]
        # 通道: real_L, imag_L, real_R, imag_R
        frames = spec_L.shape[1]
        chunks = []
        chunk_size = self._dim_t
        overlap = 128
        step = chunk_size - overlap

        if frames < chunk_size:
            pad = chunk_size - frames
            spec_L = np.pad(spec_L, ((0, 0), (0, pad)))
            spec_R = np.pad(spec_R, ((0, 0), (0, pad)))
            frames = chunk_size

        n_chunks = max(1, (frames - overlap) // step + 1)

        print(f"[MDX-Net] 推理 {n_chunks} 切片...")
        output_chunks = []

        for idx in range(n_chunks):
            start = idx * step
            end = min(start + chunk_size, frames)
            if end - start < chunk_size:
                start = frames - chunk_size
                end = frames

            # 提取切片
            chunk_L = spec_L[:, start:end]
            chunk_R = spec_R[:, start:end]

            # [batch=1, 4, freq, time]
            inp = np.stack([
                chunk_L.real, chunk_L.imag,
                chunk_R.real, chunk_R.imag,
            ], axis=0)  # [4, freq, time]
            inp = inp[np.newaxis, ...]  # [1, 4, freq, time]

            outputs = self._session.run(None, {"input": inp.astype(np.float32)})
            out = outputs[0][0]  # [4, freq, time]

            output_chunks.append((start, end, out))

            if (idx + 1) % max(1, n_chunks // 4) == 0:
                print(f"  {idx+1}/{n_chunks}")

        # 重建：加权平均重叠区域
        out_shape = (4, self._dim_f, frames)
        out_accum = np.zeros(out_shape, dtype=np.float32)
        weight = np.zeros((1, self._dim_f, frames), dtype=np.float32)

        # 线性权重窗口
        win = np.hanning(chunk_size * 2)[chunk_size:]
        win = np.concatenate([win[:overlap], np.ones(chunk_size - 2*overlap), win[-overlap:]])

        for start, end, out_chunk in output_chunks:
            w = win[:end-start]
            out_accum[:, :, start:end] += out_chunk * w[np.newaxis, np.newaxis, :]
            weight[:, :, start:end] += w[np.newaxis, np.newaxis, :]

        weight = np.maximum(weight, 1e-8)
        out_accum /= weight

        # 分离 4 通道: real_L, imag_L, real_R, imag_R
        v_real = (out_accum[0] + out_accum[2]) * 0.5  # 人声
        v_imag = (out_accum[1] + out_accum[3]) * 0.5
        i_real = out_accum[0] - v_real + out_accum[2] - v_real  # 背景 = 原 - 人声
        i_imag = out_accum[1] - v_imag + out_accum[3] - v_imag

        # 重建音频
        orig_len = len(mixture[0])
        print(f"[MDX-Net] 重建音频...")
        vocals = librosa.istft(
            (v_real + 1j * v_imag)[:self._n_fft//2+1],
            hop_length=self._hop, length=orig_len,
        )
        instrumental = librosa.istft(
            (i_real + 1j * i_imag)[:self._n_fft//2+1],
            hop_length=self._hop, length=orig_len,
        )

        v_path = os.path.join(output_dir, "vocals.wav")
        i_path = os.path.join(output_dir, "accompaniment.wav")
        sf.write(v_path, vocals, sr)
        sf.write(i_path, instrumental, sr)

        print(f"[MDX-Net] ✓ 人声: {Path(v_path).name}")
        print(f"[MDX-Net] ✓ 背景: {Path(i_path).name}")
        return v_path, i_path


def separate_vocals_mdx(audio_path: str, output_dir: str = None) -> Tuple[str, str]:
    sep = MDXNetSeparator()
    return sep.separate(audio_path, output_dir)
