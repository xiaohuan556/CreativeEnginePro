import sys
import os
from functools import lru_cache
from pathlib import Path


def _configured_model(name: str, env_key: str = "", fallback: str = "") -> str:
    if env_key and os.environ.get(env_key, "").strip():
        return os.environ[env_key].strip()
    try:
        from api_config import get as api_get
        return str(api_get(name).default_model or fallback).strip()
    except Exception:
        return fallback


@lru_cache
def available_providers() -> list[dict]:
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import config
    result = [{"name": "edge_tts", "capabilities": ["text_to_speech"], "profile": {"cost": "free"}}]
    if getattr(config, "LLM_API_KEY", ""):
        llm_name = "deepseek" if str(getattr(config, "LLM_MODE", "")).lower() == "deepseek" else "openai"
        result.append({"name": llm_name, "capabilities": ["chat", "json"], "profile": {"model": getattr(config, "LLM_MODEL_NAME", "")}})
    if getattr(config, "OPENAI_API_KEY", ""):
        result.extend([
            {"name": "gptimage", "capabilities": ["text_to_image", "image_edit", "inpaint"], "profile": {"reference_assets": 10, "model": _configured_model("openai_image", "GPTIMAGE_MODEL", "gpt-image-2")}},
            {"name": "veo", "capabilities": ["text_to_video", "image_to_video"], "profile": {"reference_assets": 3, "native_audio": True, "model": _configured_model("veo", "VEO_MODEL", "veo-3.1-generate-preview")}},
        ])
    if getattr(config, "SEEDREAM_API_KEY", ""):
        seedance_default = "doubao-seedance-2-0-260128" if str(getattr(config, "SEEDREAM_API_KEY", "")).startswith("ark-") else "doubao-seedance-2.0"
        result.extend([
            {"name": "seedream", "capabilities": ["text_to_image", "image_edit"], "profile": {"reference_assets": 10, "model": _configured_model("seedream", "SEEDREAM_MODEL", "doubao-seedream-5-0-pro-260628")}},
            {"name": "seedance", "capabilities": ["text_to_video", "image_to_video"], "profile": {"reference_assets": 9, "native_audio": True, "model": str(os.environ.get("SEEDANCE_MODEL") or seedance_default)}},
        ])
    try:
        from api_config import get as api_get
        for name in ("fish_audio", "elevenlabs", "siliconflow", "deepgram"):
            if api_get(name).value():
                capabilities = ["text_to_speech", "clone_voice"] if name == "fish_audio" else ["text_to_speech"]
                result.append({"name": name, "capabilities": capabilities, "profile": {"cost": "paid", "model": str(api_get(name).default_model or "")}})
    except Exception:
        # The free Edge provider remains usable even when optional desktop TTS
        # configuration is unavailable in a minimal server image.
        pass
    return result


def resolve_provider_model(provider: str, requested: str = "") -> str:
    """Resolve an explicit persisted model without letting workers reroute it."""
    if requested.strip():
        return requested.strip()
    profile = next((item for item in available_providers() if item["name"] == provider), None)
    return str((profile or {}).get("profile", {}).get("model") or "").strip()
