import base64
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    # The lightweight control-plane test venv omits worker-only HTTP packages.
    # These tests patch every network call and only need imports to succeed.
    sys.modules["requests"] = SimpleNamespace(post=None, get=None)

from ai.providers.base import TaskRequest
from ai.providers.image.seedream import GPTImageProvider, SeedreamProvider
from ai.providers.video.veo import SeedanceProvider


class MediaProviderModelLockTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _download(_url, target, timeout=0):
        Path(target).write_bytes(b"generated-media")

    def test_seedream_uses_task_model_in_paid_payload(self):
        provider = SeedreamProvider(api_key="ark-test", model="configured-seedream", base_url="https://ark.example")
        request = TaskRequest(operation="text_to_image", inputs={"prompt": "anime robot"}, params={"model": "locked-seedream", "n": 1})
        with patch("ai.providers.image.seedream.ark_post", return_value={"data": [{"url": "https://example.test/image.png"}]}) as post, patch("ai.providers.image.seedream.download", side_effect=self._download), patch.object(provider, "_out_dir", return_value=self.output_dir):
            result = provider.execute(request)
        self.assertTrue(result.is_success, result.result.error if result.result else "")
        self.assertEqual("locked-seedream", post.call_args.args[2]["model"])

    def test_gpt_image_uses_task_model_in_paid_payload(self):
        provider = GPTImageProvider(api_key="sk-test", model="configured-image", base_url="https://image.example/v1")
        encoded = base64.b64encode(b"fake-png").decode("ascii")
        request = TaskRequest(operation="text_to_image", inputs={"prompt": "anime mailbox"}, params={"model": "locked-gpt-image", "n": 1})
        with patch("ai.providers.image.seedream.ark_post", return_value={"data": [{"b64_json": encoded}]}) as post, patch.object(provider, "_out_dir", return_value=self.output_dir):
            result = provider.execute(request)
        self.assertTrue(result.is_success, result.result.error if result.result else "")
        self.assertEqual("locked-gpt-image", post.call_args.args[2]["model"])

    def test_seedance_uses_task_model_in_paid_payload(self):
        provider = SeedanceProvider(api_key="ark-test", model="configured-seedance", base_url="https://video.example/v3")
        request = TaskRequest(operation="text_to_video", inputs={"prompt": "anime robot walks"}, params={"model": "locked-seedance", "duration": 5, "ratio": "16:9"})
        with patch("ai.providers.video.veo.ark_post", return_value={"id": "task-1"}) as post, patch("ai.providers.video.veo.ark_get", return_value={"status": "succeeded", "content": {"video_url": "https://example.test/video.mp4"}}), patch("ai.providers.video.veo.download", side_effect=self._download), patch.object(provider, "_out_dir", return_value=self.output_dir):
            result = provider.execute(request)
        self.assertTrue(result.is_success, result.result.error if result.result else "")
        self.assertEqual("locked-seedance", post.call_args.args[2]["model"])


if __name__ == "__main__":
    unittest.main()
