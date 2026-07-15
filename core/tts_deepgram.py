"""
小欢语音 - Deepgram Aura TTS 引擎
顶级英文音质，12种声音
"""
from pathlib import Path


class DeepgramTTSEngine:
    """Deepgram TTS — Aura 系列"""

    VOICES = {
        "aura-asteria-en": "Asteria（知性女声）",
        "aura-luna-en": "Luna（温柔女声）",
        "aura-stella-en": "Stella（明亮女声）",
        "aura-athena-en": "Athena（权威女声）",
        "aura-hera-en": "Hera（成熟女声）",
        "aura-orion-en": "Orion（沉稳男声）",
        "aura-arcas-en": "Arcas（温暖男声）",
        "aura-perseus-en": "Perseus（清晰男声）",
        "aura-angus-en": "Angus（磁性男声）",
        "aura-orpheus-en": "Orpheus（文艺男声）",
        "aura-helios-en": "Helios（明亮男声）",
        "aura-zeus-en": "Zeus（权威男声）",
    }

    def __init__(self, api_key: str = "", voice_id: str = "aura-asteria-en"):
        import os
        self.api_key = api_key or os.getenv("DEEPGRAM_KEY", "")
        self.voice_id = voice_id

    def synthesize_segment(self, text: str, output_path, **kwargs) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        vid = kwargs.get("voice_id") or self.voice_id

        import urllib.request, json
        data = json.dumps({"text": text}).encode()
        req = urllib.request.Request(
            f"https://api.deepgram.com/v1/speak?model={vid}",
            data=data,
            headers={
                "Authorization": f"Token {self.api_key}",
                "Content-Type": "application/json",
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                with open(output_path, "wb") as f:
                    f.write(resp.read())
            return output_path
        except Exception as e:
            err = str(e)
            if "401" in err:
                raise RuntimeError("Deepgram Key 无效")
            elif "402" in err:
                raise RuntimeError("Deepgram 余额不足")
            else:
                raise RuntimeError(f"Deepgram TTS 失败: {e}")

    @staticmethod
    def list_voices():
        return []
