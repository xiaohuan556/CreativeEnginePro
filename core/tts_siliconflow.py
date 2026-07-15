"""
小欢语音 - 硅基流动 CosyVoice2 TTS 引擎
OpenAI 兼容接口，8种定制音色
"""
import os
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from config import WORK_DIR, ensure_work_dir, FFMPEG_BIN


class SiliconFlowTTSEngine:
    """硅基流动 TTS — CosyVoice2-0.5B"""

    VOICES = {
        "alex": "沉稳男声", "benjamin": "低沉男声",
        "charles": "磁性男声", "david": "欢快男声",
        "anna": "沉稳女声", "bella": "激情女声",
        "claire": "温柔女声", "diana": "欢快女声",
    }

    def __init__(self, api_key: str = "", voice_id: str = "alex"):
        self.api_key = api_key or os.getenv("SILICONFLOW_KEY", "")
        self.voice_id = voice_id

    def synthesize_segment(self, text: str, output_path, **kwargs) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        vid = kwargs.get("voice_id") or self.voice_id
        model = "FunAudioLLM/CosyVoice2-0.5B"

        try:
            from openai import OpenAI
            client = OpenAI(api_key=self.api_key, base_url="https://api.siliconflow.cn/v1", timeout=30.0)
            resp = client.audio.speech.create(
                model=model,
                voice=f"{model}:{vid}",
                input=text,
                response_format="mp3",
            )
            resp.stream_to_file(str(output_path))
            return output_path
        except Exception as e:
            raise RuntimeError(f"硅基流动 TTS 失败: {e}")

    def synthesize_srt(
        self,
        entries: List,
        output_dir: Optional[Path] = None,
        voice_preset: Optional[str] = None,
    ) -> List[Tuple]:
        """按 SRT 条目批量合成音频切片"""
        ensure_work_dir()
        if output_dir is None:
            output_dir = WORK_DIR / "tts_segments"
        output_dir.mkdir(parents=True, exist_ok=True)

        results = []
        for entry in entries:
            seg_path = output_dir / f"segment_{entry.index:04d}.mp3"
            try:
                self.synthesize_segment(entry.text, seg_path)
                results.append((entry, seg_path))
            except Exception:
                self._generate_silence(seg_path, max(getattr(entry, 'duration', 0.5), 0.5))
                results.append((entry, seg_path))

        return results

    def _generate_silence(self, output_path: Path, duration: float):
        """生成静音 MP3 占位文件"""
        try:
            subprocess.run(
                [FFMPEG_BIN, "-y", "-f", "lavfi", "-i",
                 f"anullsrc=r=44100:cl=stereo", "-t", str(duration),
                 "-q:a", "9", "-acodec", "libmp3lame", str(output_path)],
                capture_output=True,
            )
        except Exception:
            pass

    @staticmethod
    def list_voices():
        return []
