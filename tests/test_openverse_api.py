import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.openverse_api import download_audio, prepare_search_query, search_audio


class _Response:
    def __init__(self, body: bytes, content_type="application/json"):
        self._body = body
        self._position = 0
        self.headers = {
            "Content-Length": str(len(body)),
            "Content-Type": content_type,
        }

    def read(self, size=-1):
        if size is None or size < 0:
            size = len(self._body) - self._position
        part = self._body[self._position:self._position + size]
        self._position += len(part)
        return part

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class OpenverseApiTests(unittest.TestCase):
    def test_chinese_music_and_sound_queries_are_converted_locally(self):
        self.assertEqual(("rain sound", True), prepare_search_query("雨声"))
        converted, changed = prepare_search_query("轻快科技背景音乐")
        self.assertTrue(changed)
        self.assertIn("upbeat", converted)
        self.assertIn("technology", converted)
        self.assertIn("background music", converted)

    def test_search_normalizes_audio_and_uses_safe_license_filter(self):
        payload = {
            "result_count": 1,
            "results": [{
                "id": "abc",
                "title": "Rain",
                "creator": "Alice",
                "url": "https://media.example/rain.mp3",
                "foreign_landing_url": "https://source.example/rain",
                "license": "by",
                "license_url": "https://creativecommons.org/licenses/by/4.0/",
                "duration": 65_000,
                "filetype": "audio/mpeg",
            }],
        }
        response = _Response(json.dumps(payload).encode())
        with patch("core.openverse_api._request", return_value=response) as request:
            results, total = search_audio("rain", category="sound_effect")
        self.assertEqual(1, total)
        self.assertEqual(65.0, results[0]["duration"])
        self.assertEqual("CC BY · 需署名", results[0]["license_label"])
        url = request.call_args.args[0]
        self.assertIn("category=sound_effect", url)
        self.assertIn("license=cc0%2Cpdm%2Cby%2Cby-sa", url)

    def test_download_writes_audio_and_license_sidecar(self):
        item = {
            "id": "abc",
            "title": "A/B: Rain",
            "creator": "Alice",
            "audio_url": "https://media.example/rain",
            "landing_url": "https://source.example/rain",
            "license": "by",
            "license_label": "CC BY · 需署名",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "filetype": "audio/mpeg",
            "source": "example",
        }
        with tempfile.TemporaryDirectory() as directory:
            response = _Response(b"fake mp3 data", "audio/mpeg")
            with patch("urllib.request.urlopen", return_value=response):
                output = Path(download_audio(item, directory))
            self.assertEqual(".mp3", output.suffix)
            self.assertTrue(output.exists())
            sidecar = Path(str(output) + ".license.json")
            self.assertTrue(sidecar.exists())
            metadata = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertEqual("Openverse", metadata["provider"])
            self.assertEqual("Alice", metadata["creator"])
            self.assertEqual("by", metadata["license"])


if __name__ == "__main__":
    unittest.main()
