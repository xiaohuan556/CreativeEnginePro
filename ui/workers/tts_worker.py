"""
小欢语音 - TTS 生成工作线程
将 core/tts_factory 的合成操作封装到 QThread 中，避免阻塞 UI
"""
from PyQt6.QtCore import QThread, pyqtSignal
from pathlib import Path
import time
import os
import uuid


class TTSGenerationWorker(QThread):
    """
    后台 TTS 语音生成线程

    用法：
        worker = TTSGenerationWorker(text="你好世界", voice="zh-CN-XiaoxiaoNeural")
        worker.finished.connect(on_done)
        worker.error.connect(on_error)
        worker.start()
    """

    progress = pyqtSignal(int)        # 0-100
    finished = pyqtSignal(str)        # 输出文件路径
    error = pyqtSignal(str)           # 错误消息

    def __init__(self, text: str, voice: str = "zh-CN-XiaoxiaoNeural",
                 rate: str = "+0%", engine_type: str = "auto", volume: float = 1.0):
        super().__init__()
        self._text = text
        self._voice = voice
        self._rate = rate
        self._engine_type = engine_type
        self._volume = volume  # 0.0~1.0, 默认 1.0 原声

    def run(self):
        """在线程中执行 TTS 合成"""
        try:
            from core.tts_factory import create_engine, TTSEngineType

            self.progress.emit(5)
            if self.isInterruptionRequested(): return

            # 确定引擎类型
            if self._engine_type == "edge":
                etype = TTSEngineType.EDGE_TTS
            elif self._engine_type == "elevenlabs":
                etype = TTSEngineType.ELEVENLABS
            elif self._engine_type == "fish_audio":
                etype = TTSEngineType.FISH_AUDIO
            elif self._engine_type == "auto_lang":
                etype = TTSEngineType.AUTO_LANG
            elif self._engine_type == "siliconflow":
                etype = TTSEngineType.SILICONFLOW
            elif self._engine_type == "deepgram":
                etype = TTSEngineType.DEEPGRAM
            else:
                etype = None  # 让工厂自动选择

            engine = create_engine(
                etype,
                voice=self._voice,
                rate=self._rate,
            )

            self.progress.emit(20)
            if self.isInterruptionRequested(): return

            # 输出目录
            project_root = os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))))
            out_dir = Path(project_root) / "work_output" / "tts"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"tts_{int(time.time())}_{uuid.uuid4().hex[:8]}.mp3"

            self.progress.emit(40)
            if self.isInterruptionRequested(): return

            # 执行合成
            actual = engine.synthesize_segment(self._text, out_path)
            if actual is not None:
                out_path = actual    # 引擎可能改了文件名（如千语种追加前缀）

            self.progress.emit(90)
            if self.isInterruptionRequested(): return

            if out_path.exists() and out_path.stat().st_size > 0:
                # 音量调整
                if self._volume != 1.0:
                    import subprocess
                    from config import FFMPEG_BIN
                    tmp = out_path.with_stem(f"{out_path.stem}_vol")
                    r = subprocess.run([FFMPEG_BIN, "-y", "-i", str(out_path),
                        "-filter:a", f"volume={self._volume:.2f}",
                        str(tmp)], capture_output=True)
                    if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
                        out_path.unlink()
                        tmp.rename(out_path)
                self.progress.emit(100)
                self.finished.emit(str(out_path))
            else:
                self.error.emit("生成的语音文件为空")

        except Exception as e:
            self.error.emit(str(e))
