"""
小欢语音 - Fish Audio TTS 引擎
支持 reference_id 模式（使用已克隆的声音模型）
"""
import urllib.request
import json
from pathlib import Path


class FishTTSEngine:
    """Fish Audio TTS 引擎"""

    def __init__(self, api_key: str = "", voice_id: str = ""):
        from config import ELEVENLABS_API_KEY  # 用 FISH_AUDIO_KEY
        import os
        self.api_key = api_key or os.getenv("FISH_AUDIO_KEY", "")
        self.voice_id = voice_id

    def synthesize_segment(self, text: str, output_path, **kwargs) -> Path:
        """合成语音片段"""
        output_path = Path(output_path)
        vid = kwargs.get("voice_id") or self.voice_id
        if not vid:
            raise ValueError("未指定 Fish Audio 声音模型 (voice_id)")

        data = json.dumps({
            "text": text,
            "reference_id": vid,
            "format": "mp3",
            "normalize": True,
            "latency": "normal",
        }).encode()

        req = urllib.request.Request(
            "https://api.fish.audio/v1/tts",
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "model": "s2-pro",
                "Accept": "audio/mpeg",
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(resp.read())
            return output_path
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = json.loads(e.read().decode()).get("message", "")
            except Exception:
                pass
            if e.code == 402:
                raise RuntimeError(f"Fish Audio 余额不足: {body}")
            elif e.code == 401:
                raise RuntimeError(f"Fish Audio Key 无效: {body}")
            else:
                raise RuntimeError(f"Fish Audio {e.code}: {body or e}")

    @staticmethod
    def list_voices():
        return []
