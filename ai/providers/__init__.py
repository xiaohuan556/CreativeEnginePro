"""
Provider 桩文件。

每个文件对应一个 AI 服务提供商。初期仅定义类骨架 + execute() 占位，
后续逐一接入真实 API。

目录约定：
    ai/providers/image/   → 图片生成 / 编辑
    ai/providers/video/   → 视频生成 / 编辑
    ai/providers/voice/   → TTS / 语音克隆 / ASR
    ai/providers/llm/     → 大语言模型
"""

# ── 图片 ──
# from .image.seedream import SeedreamProvider
# from .image.flux import FluxProvider
# from .image.gptimage import GPTImageProvider

# ── 视频 ──
# from .video.seedance import SeedanceProvider
# from .video.veo import VeoProvider
# from .video.kling import KlingProvider

# ── 语音 ──
# from .voice.edge import EdgeTTSProvider
# from .voice.fish import FishAudioProvider
# from .voice.voxcpm import VoxCPMProvider

# ── LLM ──
# from .llm.openai import OpenAIProvider
# from .llm.deepseek import DeepSeekProvider
