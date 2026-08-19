from types import SimpleNamespace
from unittest.mock import patch

from creative_server.provider_catalog import available_providers


def _api_entry(name: str) -> SimpleNamespace:
    return SimpleNamespace(value=lambda: "fish-test-key" if name == "fish_audio" else "")


def test_fish_audio_catalog_and_runtime_use_the_same_key_source() -> None:
    with patch("api_config.get", side_effect=_api_entry):
        available_providers.cache_clear()
        catalog = available_providers()
        from ai.service import _build_registry
        registry = _build_registry()
    available_providers.cache_clear()

    fish = next(item for item in catalog if item["name"] == "fish_audio")
    assert fish["capabilities"] == ["text_to_speech", "clone_voice"]
    assert registry.get("fish_audio") is not None
