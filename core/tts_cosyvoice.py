"""HTTP client for the official CosyVoice FastAPI zero-shot endpoint."""
from __future__ import annotations

import mimetypes
import uuid
import urllib.error
import urllib.request
import wave
from pathlib import Path


class CosyVoiceEngine:
    # The official FastAPI endpoint streams mono signed 16-bit PCM without a
    # container header. CosyVoice 2/3 use a 24 kHz output sample rate.
    OUTPUT_SAMPLE_RATE = 24_000

    def __init__(self, base_url: str, timeout: int = 300):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @staticmethod
    def _multipart(fields: dict[str, str], file_path: Path) -> tuple[bytes, str]:
        boundary = f"----CreativeEngine{uuid.uuid4().hex}"
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.extend([
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode("utf-8"), b"\r\n",
            ])
        content_type = mimetypes.guess_type(file_path.name)[0] or "audio/wav"
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="prompt_wav"; filename="{file_path.name}"\r\n'.encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            file_path.read_bytes(), b"\r\n", f"--{boundary}--\r\n".encode(),
        ])
        return b"".join(chunks), boundary

    def synthesize(self, text: str, reference_audio: str, reference_text: str,
                   output_path: str | Path, speed: float = 1.0) -> Path:
        reference = Path(reference_audio)
        if not reference.is_file():
            raise ValueError("CosyVoice 参考录音不存在")
        if not text.strip():
            raise ValueError("CosyVoice 目标文字不能为空")
        if not reference_text.strip():
            raise ValueError("CosyVoice 零样本克隆需要准确填写参考录音内容")
        body, boundary = self._multipart({
            "tts_text": text, "prompt_text": reference_text,
            "speed": str(max(0.5, min(2.0, float(speed)))),
        }, reference)
        request = urllib.request.Request(
            f"{self.base_url}/inference_zero_shot", data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                     "Accept": "application/octet-stream"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                audio = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"CosyVoice 服务返回 {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"无法连接 CosyVoice 服务 {self.base_url}: {error.reason}") from error
        if not audio:
            raise RuntimeError("CosyVoice 服务返回了空音频")
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Keep compatibility with gateways that already wrap the response as
        # WAV, while correctly packaging the raw PCM returned by the official
        # runtime/python/fastapi server.
        if audio.startswith(b"RIFF"):
            target.write_bytes(audio)
        else:
            with wave.open(str(target), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(self.OUTPUT_SAMPLE_RATE)
                wav_file.writeframes(audio)
        return target
