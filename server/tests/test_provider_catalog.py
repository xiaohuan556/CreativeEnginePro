from types import SimpleNamespace
from unittest.mock import patch

from creative_server.provider_catalog import available_providers, resolve_provider_model


def _api_entry(name: str) -> SimpleNamespace:
    return SimpleNamespace(value=lambda: "fish-test-key" if name == "fish_audio" else "", default_model="")


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


def test_provider_model_is_resolved_once_and_explicit_requests_win() -> None:
    rows = [{"name": "seedance", "capabilities": ["image_to_video"], "profile": {"model": "configured-seedance"}}]
    with patch("creative_server.provider_catalog.available_providers", return_value=rows):
        assert resolve_provider_model("seedance") == "configured-seedance"
        assert resolve_provider_model("seedance", "locked-by-task") == "locked-by-task"
