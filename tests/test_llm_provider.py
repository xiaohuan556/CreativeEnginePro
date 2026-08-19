import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ai.providers.base import TaskRequest
from ai.providers.llm.openai import OpenAIProvider


class OpenAIProviderTests(unittest.TestCase):
    def test_explicit_optional_limits_are_forwarded_without_overriding_sdk_retries(self):
        captured = {"client": None, "calls": []}

        class _Completions:
            def create(self, **kwargs):
                captured["calls"].append(kwargs)
                return SimpleNamespace(
                    id="response-1", model="gpt-5.5",
                    choices=[SimpleNamespace(
                        message=SimpleNamespace(content='{"ok":true}'))],
                    usage=SimpleNamespace(model_dump=lambda: {
                        "prompt_tokens": 20, "completion_tokens": 5}),
                )

        class _Client:
            def __init__(self, **kwargs):
                captured["client"] = kwargs
                self.chat = SimpleNamespace(completions=_Completions())

        fake_openai = SimpleNamespace(OpenAI=_Client)
        provider = OpenAIProvider(
            api_key="test-key", base_url="https://gateway.example/v1")
        request = TaskRequest(
            operation="chat",
            inputs={"messages": [{"role": "user", "content": "test"}]},
            params={
                "model": "gpt-5.5", "temperature": 0.5,
                "max_completion_tokens": 7000,
                "timeout_seconds": 300,
                "response_format": {"type": "json_object"},
            },
            use_cache=False,
        )

        with patch.dict(sys.modules, {"openai": fake_openai}):
            handle = provider.execute(request)

        self.assertTrue(handle.is_success)
        self.assertNotIn("max_retries", captured["client"])
        self.assertEqual(300.0, captured["client"]["timeout"])
        self.assertEqual("https://gateway.example/v1",
                         captured["client"]["base_url"])
        self.assertEqual(7000,
                         captured["calls"][0]["max_completion_tokens"])
        self.assertEqual({"type": "json_object"},
                         captured["calls"][0]["response_format"])

    def test_content_parts_are_joined_and_empty_reply_is_not_reported_as_success(self):
        replies = [
            SimpleNamespace(
                choices=[SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=[
                        {"type":"text", "text":"{\"ok\":"},
                        SimpleNamespace(text="true}"),
                    ], refusal=None))],
                id="parts", model="gpt-5.5", usage=None),
            SimpleNamespace(
                choices=[SimpleNamespace(
                    finish_reason="length",
                    message=SimpleNamespace(content=[], refusal=None))],
                id="empty", model="gpt-5.5", usage=None),
        ]

        class _Completions:
            def create(self, **_kwargs):
                return replies.pop(0)

        class _Client:
            def __init__(self, **_kwargs):
                self.chat = SimpleNamespace(completions=_Completions())

        provider = OpenAIProvider(api_key="test")
        request = TaskRequest(
            operation="chat", inputs={"messages":[]}, params={"model":"gpt-5.5"})
        with patch.dict(sys.modules, {"openai":SimpleNamespace(OpenAI=_Client)}):
            joined = provider.execute(request)
            empty = provider.execute(request)

        self.assertTrue(joined.is_success)
        self.assertEqual('{"ok":true}', joined.result.data)
        self.assertEqual(11, joined.result.provider_raw["content_chars"])
        self.assertFalse(empty.is_success)
        self.assertIn("finish_reason=length", empty.result.error)


if __name__ == "__main__":
    unittest.main()
