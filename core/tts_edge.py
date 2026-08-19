"""
小欢语音 - Edge-TTS 语音合成引擎
使用 edge-tts Python SDK（微软免费神经网络语音）
接口与 ElevenLabs TTSEngine 对齐，方便 TTSFactory 统一调用
"""
import asyncio
import subprocess
from pathlib import Path
from typing import Optional, List, Tuple

from config import FFMPEG_BIN, FFPROBE_BIN
from core.transcriber import SRTEntry


class EdgeTTSEngine:
    """
    Edge-TTS 引擎（微软免费语音合成）

    特点：
    - 无需 API Key，免费使用
    - 支持 100+ 种神经网络声音
    - 支持语速调节（-50% ~ +100%）
    - 接口与 ElevenLabs TTSEngine 对齐
    """

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural", rate: str = "+0%"):
        self._voice = voice
        self._rate = rate

    @property
    def voice(self) -> str:
        return self._voice

    @voice.setter
    def voice(self, v: str):
        self._voice = v

    @property
    def rate(self) -> str:
        return self._rate

    @rate.setter
    def rate(self, v: str):
        self._rate = v

    # ------------------------------------------------------------
    # 核心方法：单条合成
    # ------------------------------------------------------------
    def synthesize_segment(
        self,
        text: str,
        output_path: Path,
        voice: Optional[str] = None,
        rate: Optional[str] = None,
    ) -> Path:
        """
        合成单条文本为音频文件

        Args:
            text: 要合成的文本
            output_path: 输出音频路径 (.mp3)
            voice: 声音 ShortName，如 "zh-CN-XiaoxiaoNeural"
            rate: 语速，如 "+0%"、"-20%"

        Returns:
            输出文件路径
        """
        import edge_tts

        v = voice or self._voice
        r = rate or self._rate

        async def _gen():
            comm = edge_tts.Communicate(text, v, rate=r)
            await comm.save(str(output_path))

        asyncio.run(_gen())
        return output_path

    # ------------------------------------------------------------
    # 批量合成 SRT 条目
    # ------------------------------------------------------------
    def synthesize_srt(
        self,
        entries: List[SRTEntry],
        output_dir: Optional[Path] = None,
        voice_preset: Optional[str] = None,
    ) -> List[Tuple[SRTEntry, Path]]:
        """
        按 SRT 条目批量合成音频切片

        Args:
            entries: SRT 条目列表
            output_dir: 输出目录
            voice_preset: 兼容参数（Edge引擎不使用此参数，用 voice 属性控制）

        Returns:
            [(SRTEntry, audio_path)] 列表
        """
        from config import WORK_DIR, ensure_work_dir
        ensure_work_dir()

        if output_dir is None:
            output_dir = WORK_DIR / "tts_segments"
        output_dir.mkdir(parents=True, exist_ok=True)

        results = []
        for entry in entries:
            seg_path = output_dir / f"segment_{entry.index:04d}.mp3"
            try:
                self.synthesize_segment(entry.text, seg_path)
                results.append((entry, seg_path))
            except Exception:
                # 合成失败时生成静音占位
                self._generate_silence(
                    seg_path, max(entry.duration, 0.5)
                )
                results.append((entry, seg_path))

        return results

    # ------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------
    @staticmethod
    def get_audio_duration(audio_path: Path) -> float:
        """获取音频文件时长（秒）。优先 ffprobe；若项目未附带 ffprobe.exe 则回退 ffmpeg 解析。"""
        import subprocess, json, re
        # 1) ffprobe（部分分发里没有 ffprobe.exe，会直接抛 FileNotFoundError）
        try:
            result = subprocess.run(
                [FFPROBE_BIN, "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "json", str(audio_path)],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                try:
                    d = float(json.loads(result.stdout)["format"]["duration"])
                    if d > 0:
                        return d
                except Exception:
                    pass
        except Exception:
            pass
        # 2) 回退：ffmpeg -i 解析 stderr 中的 Duration 行
        try:
            r = subprocess.run([FFMPEG_BIN, "-i", str(audio_path)],
                               capture_output=True, text=True)
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
            if m:
                h, mi, s = m.groups()
                d = int(h) * 3600 + int(mi) * 60 + float(s)
                if d > 0:
                    return d
        except Exception:
            pass
        return 0.0

    @staticmethod
    def _generate_silence(output_path: Path, duration: float):
        """生成静音 MP3 占位文件"""
        subprocess.run(
            [FFMPEG_BIN, "-y", "-f", "lavfi", "-i",
             f"anullsrc=r=44100:cl=stereo", "-t", str(duration),
             "-q:a", "9", "-acodec", "libmp3lame", str(output_path)],
            capture_output=True
        )

    @staticmethod
    def list_voices():
        """
        获取所有可用 edge-tts 声音列表

        Returns:
            voice 对象列表，每个对象有 ShortName, Locale, Gender, FriendlyName 属性
        """
        import edge_tts
        return asyncio.run(edge_tts.list_voices())
