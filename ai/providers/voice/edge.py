"""
语音 Provider — Edge TTS / Fish Audio / VoxCPM 桩。

EdgeTTSProvider 已对接现有 core/tts_edge.py，其余为桩。
"""

from pathlib import Path
from ..base import AIProvider, ProviderDomain, TaskRequest, TaskResult, TaskHandle, TaskStatus
from ai.audio_script import spoken_text, synthesize_with_real_pauses


class VoiceProvider(AIProvider):
    """TTS / 语音克隆 / ASR Provider 基类。"""
    domain = ProviderDomain.VOICE


class EdgeTTSProvider(VoiceProvider):
    """Microsoft Edge TTS（免费，已可用）。

    直接复用现有 core/tts_edge.py。
    """
    name = "edge_tts"
    capabilities = ["text_to_speech"]
    requires_auth = False  # Edge TTS 免费，无需 API Key

    def execute(self, request: TaskRequest) -> TaskHandle:
        handle = TaskHandle(
            id=f"edge_{request.to_cache_key()[:8]}",
            provider_name=self.name, operation=request.operation,
            status=TaskStatus.RUNNING,
        )

        try:
            source_text = request.inputs.get("text", "")
            text, detected_emotion = spoken_text(source_text)
            voice = request.params.get("voice", "zh-CN-XiaoxiaoNeural")
            emotion = request.params.get("emotion") or detected_emotion

            # 语速：优先使用调用方显式传入的字符串 rate（如 "+10%"），
            # 否则将数值 speed（1.0=原速）转换为 edge_tts 要求的百分比格式。
            rate = request.params.get("rate")
            if rate is None:
                speed = request.params.get("speed", 1.0)
                if isinstance(speed, (int, float)):
                    pct = int(round((float(speed) - 1.0) * 100))
                    rate = f"{pct:+d}%"          # 1.0→"+0%"，1.2→"+20%"，0.8→"-20%"
                else:
                    rate = str(speed)
            else:
                rate = str(rate)

            output_path = request.params.get("output_path", "")
            if not output_path:
                output_path = str(Path(__import__("tempfile").gettempdir()) / f"tts_{handle.id}.mp3")

            # 接入现有 Edge TTS 引擎
            from core.tts_edge import EdgeTTSEngine
            engine = EdgeTTSEngine()
            result_path, _ = synthesize_with_real_pauses(
                source_text, Path(output_path),
                lambda phrase, target: engine.synthesize_segment(
                    phrase, target, voice=voice, rate=rate))

            handle.status = TaskStatus.DONE
            handle.progress = 1.0
            handle.result = TaskResult(success=True, data=str(result_path))
            handle.finished_at = __import__("time").time()
        except Exception as e:
            handle.status = TaskStatus.FAILED
            handle.result = TaskResult(success=False, error=str(e))
            handle.finished_at = __import__("time").time()

        return handle


class FishAudioProvider(VoiceProvider):
    """Fish Audio TTS（已可用，需 API Key）。

    直接复用现有 core/tts_fish.py。
    """
    name = "fish_audio"
    capabilities = ["text_to_speech", "clone_voice"]

    def execute(self, request: TaskRequest) -> TaskHandle:
        handle = TaskHandle(
            id=f"fish_{request.to_cache_key()[:8]}",
            provider_name=self.name, operation=request.operation,
            status=TaskStatus.RUNNING,
        )

        try:
            source_text = request.inputs.get("text", "")
            text, detected_emotion = spoken_text(source_text)
            voice = request.params.get("voice", "")
            emotion = request.params.get("emotion") or detected_emotion
            output_path = request.params.get("output_path",
                str(Path(__import__("tempfile").gettempdir()) / f"tts_{handle.id}.mp3"))

            from core.tts_fish import FishAudioEngine
            engine = FishAudioEngine(api_key=self.api_key)
            result_path, _ = synthesize_with_real_pauses(
                source_text, Path(output_path),
                lambda phrase, target: engine.synthesize_segment(
                    phrase, target, voice_id=voice))

            handle.status = TaskStatus.DONE
            handle.progress = 1.0
            handle.result = TaskResult(success=True, data=str(result_path))
            handle.finished_at = __import__("time").time()
        except Exception as e:
            handle.status = TaskStatus.FAILED
            handle.result = TaskResult(success=False, error=str(e))
            handle.finished_at = __import__("time").time()

        return handle


class FactoryTTSProvider(VoiceProvider):
    """把剪辑工作台已有的 TTS 引擎暴露给画布 TaskManager。"""
    capabilities = ["text_to_speech"]

    def __init__(self, engine_name: str):
        super().__init__()
        self.name = engine_name

    def execute(self, request: TaskRequest) -> TaskHandle:
        handle = TaskHandle(id=f"{self.name}_{request.to_cache_key()[:8]}",
                            provider_name=self.name, operation=request.operation,
                            status=TaskStatus.RUNNING)
        try:
            from core.tts_factory import create_engine, TTSEngineType
            mapping = {"elevenlabs":TTSEngineType.ELEVENLABS,
                       "siliconflow":TTSEngineType.SILICONFLOW,
                       "deepgram":TTSEngineType.DEEPGRAM,
                       "auto_lang":TTSEngineType.AUTO_LANG}
            source_text = str(request.inputs.get("text") or "")
            voice = str(request.params.get("voice") or "")
            speed = float(request.params.get("speed") or 1)
            rate = f"{int(round((speed - 1) * 100)):+d}%"
            engine = create_engine(mapping[self.name], voice=voice, rate=rate)
            output_path = Path(request.params.get("output_path") or
                (Path(__import__("tempfile").gettempdir()) / f"tts_{handle.id}.mp3"))
            result_path, _ = synthesize_with_real_pauses(
                source_text, output_path,
                lambda phrase, target: engine.synthesize_segment(phrase, target))
            handle.status = TaskStatus.DONE; handle.progress = 1.0
            handle.result = TaskResult(success=True, data=str(result_path))
        except Exception as error:
            handle.status = TaskStatus.FAILED
            handle.result = TaskResult(success=False, error=str(error))
        handle.finished_at = __import__("time").time()
        return handle


class VoxCPMProvider(VoiceProvider):
    """VoxCPM 语音生成 / 克隆（占位，待接入）。"""
    name = "voxcpm"
    capabilities = ["text_to_speech", "clone_voice"]

    def execute(self, request: TaskRequest) -> TaskHandle:
        h = TaskHandle(id=f"voxcpm_{request.to_cache_key()[:8]}",
                       provider_name=self.name, operation=request.operation)
        h.status = TaskStatus.FAILED
        h.result = TaskResult(success=False, error="VoxCPMProvider 尚未实现")
        h.finished_at = __import__("time").time()
        return h


class WhisperProvider(VoiceProvider):
    """OpenAI Whisper ASR（已可用，本地 / API 双模式）。

    复用现有 core/whisper_runner.py / core/transcriber.py。
    """
    name = "whisper"
    capabilities = ["speech_to_text"]

    def execute(self, request: TaskRequest) -> TaskHandle:
        h = TaskHandle(id=f"whisper_{request.to_cache_key()[:8]}",
                       provider_name=self.name, operation=request.operation,
                       status=TaskStatus.RUNNING)
        try:
            audio_path = request.inputs.get("audio", "")
            language = request.params.get("language", "zh")
            from core.transcriber import Transcriber
            t = Transcriber()
            text = t.transcribe(audio_path, language=language)
            h.status = TaskStatus.DONE
            h.progress = 1.0
            h.result = TaskResult(success=True, data=text)
            h.finished_at = __import__("time").time()
        except Exception as e:
            h.status = TaskStatus.FAILED
            h.result = TaskResult(success=False, error=str(e))
            h.finished_at = __import__("time").time()
        return h
