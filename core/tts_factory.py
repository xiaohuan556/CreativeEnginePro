"""
小欢语音 - TTS 引擎工厂
根据配置自动选择 edge-tts / ElevenLabs，提供统一调用接口
"""
from typing import Protocol, runtime_checkable, Optional
from pathlib import Path
from enum import Enum, auto


class TTSEngineType(Enum):
    """TTS 引擎类型"""
    EDGE_TTS = auto()
    ELEVENLABS = auto()
    FISH_AUDIO = auto()
    AUTO_LANG = auto()
    SILICONFLOW = auto()
    DEEPGRAM = auto()


@runtime_checkable
class ITTSEngine(Protocol):
    """TTS 引擎统一协议 -- edge-tts 和 ElevenLabs 都实现此接口"""
    def synthesize_segment(
        self, text: str, output_path: Path,
        voice: Optional[str] = None, **kwargs
    ) -> Path: ...

    @staticmethod
    def list_voices(): ...


def create_engine(
    engine_type: Optional[TTSEngineType] = None,
    **kwargs
) -> ITTSEngine:
    """
    TTS 引擎工厂

    优先级：
    1. 显式指定 engine_type
    2. 检查 ELEVENLABS_API_KEY 环境变量，有则用 ElevenLabs
    3. 其他情况回退到 edge-tts

    Args:
        engine_type: 显式指定引擎类型
        **kwargs: 传递给引擎构造函数的参数
                  Edge: voice, rate
                  ElevenLabs: api_key, voice_id, stability, similarity_boost

    Returns:
        实现了 ITTSEngine 协议的引擎实例
    """
    from config import ELEVENLABS_API_KEY, EDGE_TTS_DEFAULT_VOICE, EDGE_TTS_DEFAULT_RATE

    if engine_type is None:
        engine_type = TTSEngineType.ELEVENLABS if ELEVENLABS_API_KEY else TTSEngineType.EDGE_TTS

    if engine_type == TTSEngineType.ELEVENLABS:
        from core.tts_engine import TTSEngine
        voice_id = kwargs.pop("voice_id", kwargs.pop("voice", ""))
        kwargs.pop("rate", None)  # ElevenLabs 不需要语速参数
        return TTSEngine(voice_id=voice_id, **kwargs)
    elif engine_type == TTSEngineType.FISH_AUDIO:
        from core.tts_fish import FishTTSEngine
        voice_id = kwargs.pop("voice_id", kwargs.pop("voice", ""))
        return FishTTSEngine(voice_id=voice_id)
    elif engine_type == TTSEngineType.AUTO_LANG:
        from core.tts_auto_lang import AutoLangTTSEngine
        return AutoLangTTSEngine()
    elif engine_type == TTSEngineType.SILICONFLOW:
        from core.tts_siliconflow import SiliconFlowTTSEngine
        voice_id = kwargs.pop("voice_id", kwargs.pop("voice", "alex"))
        return SiliconFlowTTSEngine(voice_id=voice_id)
    elif engine_type == TTSEngineType.DEEPGRAM:
        from core.tts_deepgram import DeepgramTTSEngine
        voice_id = kwargs.pop("voice_id", kwargs.pop("voice", "aura-asteria-en"))
        return DeepgramTTSEngine(voice_id=voice_id)
    else:
        from core.tts_edge import EdgeTTSEngine
        voice = kwargs.pop("voice", EDGE_TTS_DEFAULT_VOICE)
        rate = kwargs.pop("rate", EDGE_TTS_DEFAULT_RATE)
        return EdgeTTSEngine(voice=voice, rate=rate)


def get_tts_engine():
    """
    快速获取 TTS 引擎实例（自动选择）

    这是最常用的入口函数：
        engine = get_tts_engine()
        engine.synthesize_segment("你好世界", Path("output.mp3"))

    Returns:
        ITTSEngine 实例
    """
    return create_engine()
