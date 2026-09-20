import tempfile
import unittest
import wave
from pathlib import Path

from core.tts_edge import EdgeTTSEngine


class TTSUnicodeDurationTests(unittest.TestCase):
    def test_duration_probe_supports_chinese_auto_language_filename(self):
        with tempfile.TemporaryDirectory() as folder:
            audio_path = Path(folder) / "葡萄牙语_千语种配音.wav"
            with wave.open(str(audio_path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(b"\0\0" * 16000)

            duration = EdgeTTSEngine.get_audio_duration(audio_path)

            self.assertGreater(duration, 0.9)
            self.assertLess(duration, 1.1)


if __name__ == "__main__":
    unittest.main()
