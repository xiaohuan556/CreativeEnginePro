"""
AI 服务单例 — 全局共享的 TaskManager + ProviderRegistry + AssetDB。

设计：
- 整个应用只创建一个 TaskManager（后台线程池），所有工作台共用。
- Provider 注册策略：
    * EdgeTTSProvider  —— 免费，始终注册（TTS 立即可用）。
    * OpenAI / DeepSeek —— 仅在 .env 配置了 LLM_API_KEY / OPENAI_API_KEY 时注册。
    * FishAudio       —— 仅在配置了 FISH_AUDIO_KEY 时注册。
- 图片 / 视频 Provider：Seedream（火山方舟）、Seedance（按 Key 自动选择方舟/ModelHub）、GPT-Image/Veo 3.1（ModelHub）已接入；
  FLUX / Sora / Kling 仍为桩。
- ai/ 核心层依旧不含任何 PyQt 依赖；UI 相关代码在 ai/ui/。
"""
from __future__ import annotations

import threading

from .task_manager import TaskManager, ProviderRegistry
from .providers.voice import EdgeTTSProvider
from .assets import AssetDB


_lock = threading.Lock()
_manager: "TaskManager | None" = None
_assets: "AssetDB | None" = None


def _build_registry() -> ProviderRegistry:
    reg = ProviderRegistry()
    # 1) Edge TTS —— 免费，永远可用
    reg.register(EdgeTTSProvider())

    # 2) LLM（OpenAI / DeepSeek）
    try:
        from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODE
        from api_config import get as _api_get
        if LLM_API_KEY:
            from .providers.llm import OpenAIProvider, DeepSeekProvider
            if (LLM_MODE or "openai") == "deepseek":
                reg.register(DeepSeekProvider(
                    api_key=LLM_API_KEY,
                    base_url=LLM_BASE_URL or _api_get("llm").default_base_url,
                ))
            else:
                reg.register(OpenAIProvider(
                    api_key=LLM_API_KEY,
                    base_url=LLM_BASE_URL or _api_get("llm").default_base_url,
                ))
    except Exception:
        pass

    # 复用剪辑配音模块已配置的外接音色服务。
    try:
        from api_config import get as _api_get
        from .providers.voice import FactoryTTSProvider
        for service_name in ("elevenlabs", "siliconflow", "deepgram"):
            service_config = _api_get(service_name)
            if service_config.value():
                reg.register(FactoryTTSProvider(service_name))
        reg.register(FactoryTTSProvider("auto_lang"))
    except Exception:
        pass

    # 3) Fish Audio（声音克隆）
    try:
        from config import FISH_AUDIO_KEY
        if FISH_AUDIO_KEY:
            from .providers.voice import FishAudioProvider
            reg.register(FishAudioProvider(api_key=FISH_AUDIO_KEY))
    except Exception:
        pass

    # 4) OpenAI 图像 + 视频（GPT-Image / Veo）
    #    复用同一个 OPENAI_API_KEY（ModelHub 统一代理）。
    try:
        from config import OPENAI_API_KEY, OPENAI_BASE_URL
        if OPENAI_API_KEY:
            from .providers.image.seedream import GPTImageProvider
            from .providers.video.veo import VeoProvider
            reg.register(GPTImageProvider(
                api_key=OPENAI_API_KEY,
                base_url=OPENAI_BASE_URL or "https://modelhub.ailemac.com/api/v1",
            ))
            reg.register(VeoProvider(
                api_key=OPENAI_API_KEY,
                base_url=OPENAI_BASE_URL or "https://modelhub.ailemac.com/api/v1",
            ))
    except Exception:
        pass

    # 5) Seedream / Seedance 共用豆包 Key；Seedance 按 Key 类型自动选路。
    try:
        from config import SEEDREAM_API_KEY
        if SEEDREAM_API_KEY:
            from .providers.image.seedream import SeedreamProvider
            from .providers.video.veo import SeedanceProvider
            reg.register(SeedreamProvider(api_key=SEEDREAM_API_KEY))
            reg.register(SeedanceProvider(api_key=SEEDREAM_API_KEY))
    except Exception:
        pass

    return reg


def get_ai_manager() -> TaskManager:
    """返回全局唯一的 TaskManager（首次调用时创建并启动线程池）。"""
    global _manager
    if _manager is None:
        with _lock:
            if _manager is None:
                _manager = TaskManager(_build_registry())
                _manager.start()
    return _manager


def get_asset_db() -> AssetDB:
    """返回全局唯一的资源中心数据库。"""
    global _assets
    if _assets is None:
        with _lock:
            if _assets is None:
                _assets = AssetDB()
    return _assets


def shutdown_ai():
    """应用退出时调用（可选），释放线程与数据库连接。"""
    global _manager, _assets
    if _manager is not None:
        _manager.stop(wait=False)
        _manager = None
    if _assets is not None:
        _assets.close()
        _assets = None
