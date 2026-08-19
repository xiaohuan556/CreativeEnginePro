import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai.providers.base import (
    AIProvider, ProviderDomain, ProviderRegistry, TaskHandle, TaskRequest,
    TaskResult, TaskStatus,
)
from ai.task_manager import TaskManager


class _CountingImageProvider(AIProvider):
    name = "counting_image"
    domain = ProviderDomain.IMAGE
    capabilities = ["text_to_image"]
    requires_auth = False

    def __init__(self):
        self.calls = 0
        super().__init__()

    def execute(self, request: TaskRequest) -> TaskHandle:
        self.calls += 1
        return TaskHandle(
            id=f"provider_{self.calls}",
            provider_name=self.name,
            operation=request.operation,
            status=TaskStatus.DONE,
            progress=1.0,
            result=TaskResult(success=True, data=f"result-{self.calls}".encode()),
        )


class _FlakyChatProvider(AIProvider):
    name = "flaky_chat"
    domain = ProviderDomain.LLM
    capabilities = ["chat"]
    requires_auth = False

    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0
        super().__init__()

    def execute(self, request: TaskRequest) -> TaskHandle:
        self.calls += 1
        error = self.errors.pop(0) if self.errors else ""
        return TaskHandle(
            id=f"flaky_{self.calls}", provider_name=self.name,
            operation=request.operation,
            status=TaskStatus.FAILED if error else TaskStatus.DONE,
            progress=1.0,
            result=TaskResult(success=not error, data="ok" if not error else None,
                              error=error),
        )


class TaskCachePolicyTests(unittest.TestCase):
    def _manager(self, root: Path):
        provider = _CountingImageProvider()
        registry = ProviderRegistry()
        registry.register(provider)
        manager = TaskManager(
            registry, retry_count=0,
            db_path=str(root / "tasks.db"),
            cache_dir=str(root / "cache"),
        )
        return manager, provider

    @staticmethod
    def _run(manager, provider, request, suffix):
        handle = TaskHandle(
            id=f"task_{suffix}", provider_name=provider.name,
            operation=request.operation,
        )
        manager._execute_with_retry(provider, request, handle)
        return handle

    def test_identical_generation_can_explicitly_bypass_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            manager, provider = self._manager(Path(temp))
            request = TaskRequest(
                operation="text_to_image",
                inputs={"prompt": "same prompt"},
                use_cache=False,
            )
            first = self._run(manager, provider, request, "first")
            second = self._run(manager, provider, request, "second")

            self.assertEqual(2, provider.calls)
            self.assertEqual(b"result-1", first.result.data)
            self.assertEqual(b"result-2", second.result.data)
            self.assertFalse(first.result.cache_hit)
            self.assertFalse(second.result.cache_hit)

    def test_cache_remains_available_for_idempotent_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            manager, provider = self._manager(Path(temp))
            request = TaskRequest(
                operation="text_to_image",
                inputs={"prompt": "cache this"},
            )
            first = self._run(manager, provider, request, "first")
            second = self._run(manager, provider, request, "second")

            self.assertEqual(1, provider.calls)
            self.assertFalse(first.result.cache_hit)
            self.assertTrue(second.result.cache_hit)
            self.assertEqual(first.result.data, second.result.data)

    def test_storyboard_retries_one_transient_gateway_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = _FlakyChatProvider(["504 Gateway Timeout", ""])
            registry = ProviderRegistry(); registry.register(provider)
            manager = TaskManager(
                registry, retry_count=5,
                db_path=str(Path(temp) / "tasks.db"),
                cache_dir=str(Path(temp) / "cache"))
            request = TaskRequest(
                operation="chat", use_cache=False,
                metadata={"retry_count": 1, "retry_transient_only": True})
            with patch("ai.task_manager.time.sleep") as sleep:
                handle = self._run(manager, provider, request, "gateway")
            self.assertTrue(handle.is_success)
            self.assertEqual(2, provider.calls)
            sleep.assert_called_once_with(2)

    def test_storyboard_does_not_retry_permanent_schema_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = _FlakyChatProvider(["HTTP 400 invalid parameter"])
            registry = ProviderRegistry(); registry.register(provider)
            manager = TaskManager(
                registry, retry_count=5,
                db_path=str(Path(temp) / "tasks.db"),
                cache_dir=str(Path(temp) / "cache"))
            request = TaskRequest(
                operation="chat", use_cache=False,
                metadata={"retry_count": 1, "retry_transient_only": True})
            with patch("ai.task_manager.time.sleep") as sleep:
                handle = self._run(manager, provider, request, "schema")
            self.assertFalse(handle.is_success)
            self.assertEqual(1, provider.calls)
            sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
