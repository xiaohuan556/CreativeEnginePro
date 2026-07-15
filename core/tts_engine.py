"""
GlobalFlux AI - AI 拟真强情绪配音模块
负责：将翻译后的 SRT 文本渲染为带有呼吸感、强情绪波动的真人嗓音
技术：ElevenLabs API（主打强情绪、短剧、带货声线）
"""
import time
import json
import subprocess
import shutil
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any

import requests

from config import (
    ELEVENLABS_API_KEY,
    TTS_VOICE_ID,
    TTS_MODEL_ID,
    TTS_STABILITY,
    TTS_SIMILARITY_BOOST,
    VOICE_PRESETS,
    FFMPEG_BIN, FFPROBE_BIN,
    WORK_DIR, ensure_work_dir
)
from core.transcriber import SRTEntry


class TTSEngine:
    """ElevenLabs TTS 配音引擎"""
    
    ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None,
        stability: Optional[float] = None,
        similarity_boost: Optional[float] = None
    ):
        self.api_key = api_key or ELEVENLABS_API_KEY
        self.voice_id = voice_id or TTS_VOICE_ID
        self.model_id = model_id or TTS_MODEL_ID
        self.stability = stability if stability is not None else TTS_STABILITY
        self.similarity_boost = similarity_boost if similarity_boost is not None else TTS_SIMILARITY_BOOST
        
        if not self.api_key:
            raise ValueError(
                "未设置 ELEVENLABS_API_KEY。请通过以下方式提供:\n"
                "  1. 设置环境变量: export ELEVENLABS_API_KEY=xxx\n"
                "  2. 在 .env 文件中添加: ELEVENLABS_API_KEY=xxx"
            )
        
        if not self.voice_id:
            # 自动选择一个默认可用声音
            self.voice_id = self._get_default_voice()
    
    def _get_default_voice(self) -> str:
        """获取 ElevenLabs 默认声音 ID"""
        try:
            resp = requests.get(
                f"{self.ELEVENLABS_API_BASE}/voices",
                headers={"xi-api-key": self.api_key}
            )
            if resp.status_code == 200:
                voices = resp.json().get("voices", [])
                if voices:
                    # 优先选 "Adam" 声音（通用男声，适合解说）
                    for v in voices:
                        if v.get("name") == "Adam":
                            return v["voice_id"]
                    return voices[0]["voice_id"]
        except Exception:
            pass
        
        raise ValueError(
            "无法获取 ElevenLabs 默认声音，请手动设置 TTS_VOICE_ID\n"
            "  获取声音列表: https://api.elevenlabs.io/v1/voices"
        )
    
    def _check_ffmpeg(self):
        """检查 FFmpeg"""
        if shutil.which(FFMPEG_BIN):
            return
        try:
            subprocess.run([FFMPEG_BIN, "-version"], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise RuntimeError("FFmpeg 未安装")
    
    def synthesize_segment(
        self,
        text: str,
        output_path: Path,
        voice_id: Optional[str] = None,
        stability: Optional[float] = None,
        similarity_boost: Optional[float] = None
    ) -> Path:
        """
        合成单条台词为音频切片
        
        Args:
            text: 台词文本
            output_path: 输出音频路径（mp3 格式）
            voice_id: 可选覆盖声音 ID
            stability: 可选覆盖稳定性参数（0-1，越低情绪越强烈）
            similarity_boost: 可选覆盖相似度参数（0-1）
        
        Returns:
            输出文件路径
        """
        voice = voice_id or self.voice_id
        stab = stability if stability is not None else self.stability
        sim = similarity_boost if similarity_boost is not None else self.similarity_boost
        
        url = f"{self.ELEVENLABS_API_BASE}/text-to-speech/{voice}"
        
        headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg"
        }
        
        payload = {
            "text": text,
            "model_id": self.model_id,
            "voice_settings": {
                "stability": stab,
                "similarity_boost": sim,
                "style": 0.3,        # 风格强度
                "use_speaker_boost": True
            }
        }
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=60)
                
                if resp.status_code == 200:
                    output_path.write_bytes(resp.content)
                    return output_path
                
                if resp.status_code == 429:
                    wait = 2 ** attempt + 1
                    print(f"    ⚠ 限速，等待 {wait}s 后重试...")
                    time.sleep(wait)
                    continue
                
                raise RuntimeError(f"ElevenLabs API 错误 [{resp.status_code}]: {resp.text[:200]}")
                
            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    raise RuntimeError(f"ElevenLabs 请求失败: {e}")
                time.sleep(2)
        
        raise RuntimeError("ElevenLabs TTS 合成失败")
    
    def synthesize_srt(
        self,
        entries: List[SRTEntry],
        output_dir: Optional[Path] = None,
        voice_preset: Optional[str] = None
    ) -> List[Tuple[SRTEntry, Path]]:
        """
        按 SRT 条目批量合成音频切片
        
        Args:
            entries: SRT 条目列表
            output_dir: 输出目录
            voice_preset: 预设声线名称（如 "passionate_female"）
        
        Returns:
            [(SRTEntry, audio_path)] 列表，按时间顺序排列
        """
        if output_dir is None:
            output_dir = WORK_DIR / "tts_segments"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # 加载预设
        voice_id = self.voice_id
        stability = self.stability
        similarity_boost = self.similarity_boost
        
        if voice_preset and voice_preset in VOICE_PRESETS:
            preset = VOICE_PRESETS[voice_preset]
            if preset["voice_id"]:
                voice_id = preset["voice_id"]
            stability = preset["stability"]
            similarity_boost = preset["similarity_boost"]
            print(f"  使用预设声线: {preset['name']}")
        
        results = []
        total = len(entries)
        
        print(f"  开始合成 {total} 条台词...")
        
        for i, entry in enumerate(entries):
            segment_path = output_dir / f"segment_{entry.index:04d}.mp3"
            
            try:
                self.synthesize_segment(
                    text=entry.text,
                    output_path=segment_path,
                    voice_id=voice_id,
                    stability=stability,
                    similarity_boost=similarity_boost
                )
                results.append((entry, segment_path))
                
                # 进度显示
                if (i + 1) % 5 == 0 or (i + 1) == total:
                    print(f"    [{i + 1}/{total}] {entry.text[:30]}...")
                    
            except Exception as e:
                print(f"    ✗ 第 {entry.index} 条合成失败: {e}")
                # 生成静音占位
                silence_path = output_dir / f"segment_{entry.index:04d}.mp3"
                self._generate_silence(silence_path, entry.duration)
                results.append((entry, silence_path))
        
        print(f"  ✓ 合成完成: {len(results)}/{total} 条成功")
        return results
    
    def get_audio_duration(self, audio_path: Path) -> float:
        """获取音频文件时长（秒）"""
        try:
            import soundfile as sf
            info = sf.info(str(audio_path))
            return info.duration
        except ImportError:
            # 回退到 ffprobe
            try:
                result = subprocess.run(
                    [
                        FFPROBE_BIN, "-v", "quiet",
                        "-show_entries", "format=duration",
                        "-of", "json",
                        str(audio_path)
                    ],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    return float(data["format"]["duration"])
            except Exception:
                pass
        return 0.0
    
    @staticmethod
    def _generate_silence(output_path: Path, duration: float):
        """生成静音 MP3 文件"""
        subprocess.run(
            [
                FFMPEG_BIN, "-y",
                "-f", "lavfi",
                "-i", f"anullsrc=r=44100:cl=stereo",
                "-t", str(max(duration, 0.5)),
                "-q:a", "9",
                "-acodec", "libmp3lame",
                str(output_path)
            ],
            capture_output=True
        )
    
    def list_available_voices(self) -> List[Dict[str, Any]]:
        """列出所有可用声音"""
        resp = requests.get(
            f"{self.ELEVENLABS_API_BASE}/voices",
            headers={"xi-api-key": self.api_key}
        )
        if resp.status_code != 200:
            raise RuntimeError(f"获取声音列表失败: {resp.text}")
        
        voices = resp.json().get("voices", [])
        return [
            {
                "id": v["voice_id"],
                "name": v["name"],
                "labels": v.get("labels", {}),
                "preview": v.get("preview_url", "")
            }
            for v in voices
        ]


# ── 便捷函数 ──
def synthesize_translated_srt(
    translated_entries: List[SRTEntry],
    voice_preset: Optional[str] = None
) -> List[Tuple[SRTEntry, Path]]:
    """
    便捷函数：合成翻译后的 SRT 条目为音频切片
    
    Args:
        translated_entries: 翻译后的 SRT 条目列表
        voice_preset: 预设声线名称
    
    Returns:
        [(SRTEntry, audio_path)] 列表
    """
    ensure_work_dir()
    engine = TTSEngine()
    return engine.synthesize_srt(translated_entries, voice_preset=voice_preset)


if __name__ == "__main__":
    import sys
    from core.transcriber import parse_srt
    
    if len(sys.argv) < 2:
        print("用法: python tts_engine.py <translated.srt> [voice_preset]")
        print("  预设声线: passionate_female, mature_male, villain, narrator_young")
        sys.exit(1)
    
    srt_path = Path(sys.argv[1])
    preset = sys.argv[2] if len(sys.argv) > 2 else None
    
    entries = parse_srt(srt_path.read_text(encoding="utf-8"))
    results = synthesize_translated_srt(entries, preset)
    print(f"\n合成完成: {len(results)} 条音频切片")
    for entry, path in results:
        print(f"  #{entry.index} {path.name}")
