import sys
from functools import lru_cache
from pathlib import Path


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
            {"name": "gptimage", "capabilities": ["text_to_image", "image_edit", "inpaint"], "profile": {}},
            {"name": "veo", "capabilities": ["text_to_video", "image_to_video"], "profile": {}},
        ])
    if getattr(config, "SEEDREAM_API_KEY", ""):
        result.extend([
            {"name": "seedream", "capabilities": ["text_to_image", "image_edit"], "profile": {}},
            {"name": "seedance", "capabilities": ["text_to_video", "image_to_video"], "profile": {}},
        ])
    return result
