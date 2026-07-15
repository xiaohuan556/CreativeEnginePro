"""
GlobalFlux AI - 独家音画自动对齐算法 (Time-Sync Algorithm) 🌟
核心关键技术：解决中外语言长短不一导致的"音画不同步"痛点

算法逻辑：
  情况 A（外语长于中文 T2 > T1）：
    R = T2 / T1
    若 R <= 1.25 → FFmpeg atempo 无损变速不变调轻微加速
    若 R > 1.25 → 警告 + 自动截断多余静音
  情况 B（外语短于中文 T2 < T1）：
    保持原速，填充 T1-T2 时长的静音轨
"""
import json
import subprocess
import shutil
from pathlib import Path
from typing import Optional, List, Tuple

from config import (
    FFMPEG_BIN, FFPROBE_BIN,
    MAX_TEMPO_RATIO, WORK_DIR, ensure_work_dir
)
from core.transcriber import SRTEntry, seconds_to_srt_time


class TimeSyncEngine:
    """音画自动对齐引擎"""
    
    def __init__(self, work_dir: Optional[Path] = None):
        self.work_dir = work_dir or WORK_DIR / "synced_segments"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._check_ffmpeg()
    
    def _check_ffmpeg(self):
        """检查 FFmpeg"""
        if not shutil.which(FFMPEG_BIN):
            try:
                subprocess.run([FFMPEG_BIN, "-version"], capture_output=True, check=True)
            except (subprocess.CalledProcessError, FileNotFoundError):
                raise RuntimeError("FFmpeg 未安装")
    
    @staticmethod
    def get_audio_duration(audio_path: Path) -> float:
        """使用 ffprobe 获取音频精确时长"""
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
        
        # 回退：使用 soundfile
        try:
            import soundfile as sf
            info = sf.info(str(audio_path))
            return info.duration
        except ImportError:
            pass
        
        return 0.0
    
    @staticmethod
    def atempo_filter(ratio: float) -> str:
        """
        生成 FFmpeg atempo 滤镜链
        atempo 支持范围 [0.5, 100.0]，超范围需链式拼接
        
        Args:
            ratio: 变速比（>1 加速，<1 减速）
        
        Returns:
            FFmpeg atempo 滤镜字符串
        """
        if 0.5 <= ratio <= 100.0:
            return f"atempo={ratio:.4f}"
        
        # 超出单次范围，链式拆分
        filters = []
        remaining = ratio
        while remaining > 100.0:
            filters.append("atempo=100.0")
            remaining /= 100.0
        while remaining < 0.5:
            filters.append("atempo=0.5")
            remaining /= 0.5
        filters.append(f"atempo={remaining:.4f}")
        return ",".join(filters)
    
    def sync_segment(
        self,
        audio_path: Path,
        target_duration: float,
        output_path: Optional[Path] = None,
        segment_index: int = 0
    ) -> Tuple[Path, dict]:
        """
        对齐单条音频切片到目标时长
        
        Args:
            audio_path: TTS 生成的音频切片路径
            target_duration: 原中文台词的目标时长（秒）
            output_path: 对齐后的音频输出路径
            segment_index: 条目序号（用于命名）
        
        Returns:
            (aligned_path, sync_info) 元组
            sync_info 包含: original_duration, target_duration, ratio, action
        """
        if output_path is None:
            output_path = self.work_dir / f"synced_{segment_index:04d}.mp3"
        
        actual_duration = self.get_audio_duration(audio_path)
        
        if actual_duration <= 0:
            # 无法获取时长，生成静音
            self._generate_silence(output_path, target_duration)
            return output_path, {
                "original_duration": 0,
                "target_duration": target_duration,
                "ratio": 0,
                "action": "silence_fallback"
            }
        
        ratio = actual_duration / target_duration
        sync_info = {
            "original_duration": round(actual_duration, 3),
            "target_duration": round(target_duration, 3),
            "ratio": round(ratio, 4),
            "action": ""
        }
        
        if abs(ratio - 1.0) < 0.03:
            # 差异 < 3%，无需处理
            shutil.copy2(str(audio_path), str(output_path))
            sync_info["action"] = "no_change"
            
        elif ratio > 1.0:
            # 情况 A：外语长于中文，需要加速或截断
            if ratio <= MAX_TEMPO_RATIO:
                # 轻微加速，变速不变调
                filter_str = self.atempo_filter(ratio)
                self._apply_atempo(audio_path, output_path, filter_str)
                sync_info["action"] = f"speed_up_{ratio:.2f}x"
            else:
                # 超出安全加速比，先加速到上限，再截断尾部
                filter_str = self.atempo_filter(MAX_TEMPO_RATIO)
                temp_path = self.work_dir / f"_temp_speed_{segment_index:04d}.mp3"
                self._apply_atempo(audio_path, temp_path, filter_str)
                # 截断到目标时长
                self._trim_audio(temp_path, output_path, target_duration)
                temp_path.unlink(missing_ok=True)
                sync_info["action"] = f"speed_up_1.25x+trim"
                
        else:
            # 情况 B：外语短于中文，填充静音
            silence_duration = target_duration - actual_duration
            self._pad_with_silence(audio_path, output_path, silence_duration)
            sync_info["action"] = f"pad_silence_{silence_duration:.2f}s"
        
        return output_path, sync_info
    
    def sync_all_segments(
        self,
        segments: List[Tuple[SRTEntry, Path]]
    ) -> Tuple[List[Tuple[SRTEntry, Path]], List[dict]]:
        """
        批量对齐所有音频切片
        
        Args:
            segments: [(SRTEntry, audio_path)] 列表
        
        Returns:
            (aligned_segments, sync_reports) 元组
        """
        aligned = []
        reports = []
        warnings = []
        
        total = len(segments)
        print(f"  开始对齐 {total} 条音频切片...")
        
        for i, (entry, audio_path) in enumerate(segments):
            aligned_path, info = self.sync_segment(
                audio_path=audio_path,
                target_duration=entry.duration,
                segment_index=entry.index
            )
            aligned.append((entry, aligned_path))
            reports.append(info)
            
            if "trim" in info["action"]:
                warnings.append(
                    f"    ⚠ #{entry.index} 加速比 {info['ratio']:.2f} > {MAX_TEMPO_RATIO}，"
                    f"已截断尾部"
                )
            
            if (i + 1) % 5 == 0 or (i + 1) == total:
                print(f"    [{i + 1}/{total}] 对齐中...")
        
        if warnings:
            print(f"\n  对齐警告 ({len(warnings)} 条):")
            for w in warnings[:5]:  # 最多显示 5 条
                print(w)
            if len(warnings) > 5:
                print(f"    ... 及其他 {len(warnings) - 5} 条")
        
        print(f"  ✓ 对齐完成: {len(aligned)} 条")
        return aligned, reports
    
    def concatenate_aligned(
        self,
        aligned_segments: List[Tuple[SRTEntry, Path]],
        output_path: Optional[Path] = None,
        total_duration: Optional[float] = None
    ) -> Path:
        """
        将所有对齐后的音频切片拼接为完整的配音总轨
        严格按原始时间戳定位，确保音画同步
        
        Args:
            aligned_segments: [(SRTEntry, aligned_path)] 列表
            output_path: 输出路径
            total_duration: 总时长（秒），用于确定最终音频长度
        
        Returns:
            拼接后的完整配音轨路径
        """
        if output_path is None:
            output_path = WORK_DIR / "dubbing_track.wav"
        
        if not aligned_segments:
            return output_path
        
        # 生成 silence 基底 + 每段配音精准 overlay
        # 策略：先生成指定时长的静音基底，再逐段叠加
        if total_duration is None:
            total_duration = aligned_segments[-1][0].end + 1.0
        
        # 创建静音基底
        silence_base = self.work_dir / "_silence_base.wav"
        self._generate_silence_wav(silence_base, total_duration)
        
        # 使用 FFmpeg overlay 方式精确叠加每段配音
        # 构建 FFmpeg 复杂滤镜
        inputs = ["-i", str(silence_base)]
        filter_parts = []
        input_idx = 1  # 0 是静音基底
        
        for entry, seg_path in aligned_segments:
            inputs.extend(["-i", str(seg_path)])
            # 将每个片段延迟到正确的时间位置
            delay_ms = int(entry.start * 1000)
            filter_parts.append(
                f"[{input_idx}]adelay={delay_ms}|{delay_ms},apad=whole_dur={entry.duration}[d{input_idx}]"
            )
            input_idx += 1
        
        # 混合所有延迟后的音轨
        mix_inputs = "".join(f"[d{i}]" for i in range(1, input_idx))
        filter_parts.append(
            f"{mix_inputs}amix=inputs={input_idx - 1}:duration=longest:dropout_transition=0[aout]"
        )
        
        filter_complex = ";".join(filter_parts)
        
        cmd = [
            FFMPEG_BIN, "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[aout]",
            "-acodec", "pcm_s16le",
            "-ar", "44100",
            "-ac", "2",
            str(output_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            # 回退方案：简单拼接
            print(f"    ⚠ 精确 overlay 失败，使用回退拼接方案")
            output_path = self._simple_concatenate(aligned_segments, total_duration, output_path)
        
        # 清理
        silence_base.unlink(missing_ok=True)
        
        return output_path
    
    def _simple_concatenate(
        self,
        aligned_segments: List[Tuple[SRTEntry, Path]],
        total_duration: float,
        output_path: Path
    ) -> Path:
        """
        回退方案：简单拼接（不保证精确时间戳，但保证不报错）
        """
        # 生成 concat 文件列表
        concat_file = self.work_dir / "_concat_list.txt"
        
        # 先填充开头静音（到第一条字幕）
        if aligned_segments and aligned_segments[0][0].start > 0.1:
            start_silence = self.work_dir / "_start_silence.wav"
            self._generate_silence_wav(start_silence, aligned_segments[0][0].start)
        
        lines = []
        if aligned_segments and aligned_segments[0][0].start > 0.1:
            lines.append(f"file '{start_silence.resolve()}'")
        
        prev_end = aligned_segments[0][0].start if aligned_segments else 0
        
        for entry, seg_path in aligned_segments:
            # 填充字幕之间的间隔静音
            gap = entry.start - prev_end
            if gap > 0.05:
                gap_silence = self.work_dir / f"_gap_{entry.index:04d}.wav"
                self._generate_silence_wav(gap_silence, gap)
                lines.append(f"file '{gap_silence.resolve()}'")
            
            lines.append(f"file '{seg_path.resolve()}'")
            prev_end = entry.end
        
        # 尾部静音
        if prev_end < total_duration:
            tail_silence = self.work_dir / "_tail_silence.wav"
            self._generate_silence_wav(tail_silence, total_duration - prev_end)
            lines.append(f"file '{tail_silence.resolve()}'")
        
        concat_file.write_text("\n".join(lines), encoding="utf-8")
        
        cmd = [
            FFMPEG_BIN, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-acodec", "pcm_s16le",
            "-ar", "44100",
            "-ac", "2",
            str(output_path)
        ]
        
        subprocess.run(cmd, capture_output=True, check=True)
        
        # 清理临时文件
        for line in lines:
            path_str = line.split("'", 1)[1].rsplit("'", 1)[0]
            Path(path_str).unlink(missing_ok=True)
        concat_file.unlink(missing_ok=True)
        
        return output_path
    
    # ── 底层 FFmpeg 操作 ──
    
    def _apply_atempo(self, input_path: Path, output_path: Path, filter_str: str):
        """应用 atempo 滤镜（变速不变调）"""
        cmd = [
            FFMPEG_BIN, "-y",
            "-i", str(input_path),
            "-af", filter_str,
            "-acodec", "libmp3lame",
            "-q:a", "4",
            str(output_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"atempo 处理失败: {result.stderr[:200]}")
    
    def _trim_audio(self, input_path: Path, output_path: Path, duration: float):
        """截断音频到指定时长"""
        cmd = [
            FFMPEG_BIN, "-y",
            "-i", str(input_path),
            "-t", str(duration),
            "-acodec", "libmp3lame",
            "-q:a", "4",
            str(output_path)
        ]
        subprocess.run(cmd, capture_output=True, check=True)
    
    def _pad_with_silence(self, input_path: Path, output_path: Path, silence_duration: float):
        """在音频尾部填充静音"""
        cmd = [
            FFMPEG_BIN, "-y",
            "-i", str(input_path),
            "-af", f"apad=whole_dur={silence_duration + self.get_audio_duration(input_path):.3f}",
            "-acodec", "libmp3lame",
            "-q:a", "4",
            str(output_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            # 回退：使用 concat 方式
            silence_path = self.work_dir / f"_pad_silence_{output_path.stem}.mp3"
            self._generate_silence_mp3(silence_path, silence_duration)
            concat_file = self.work_dir / f"_pad_concat_{output_path.stem}.txt"
            concat_file.write_text(
                f"file '{input_path.resolve()}'\nfile '{silence_path.resolve()}'\n",
                encoding="utf-8"
            )
            cmd = [
                FFMPEG_BIN, "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_file),
                "-acodec", "libmp3lame", "-q:a", "4",
                str(output_path)
            ]
            subprocess.run(cmd, capture_output=True, check=True)
            silence_path.unlink(missing_ok=True)
            concat_file.unlink(missing_ok=True)
    
    @staticmethod
    def _generate_silence_wav(output_path: Path, duration: float):
        """生成静音 WAV 文件"""
        subprocess.run(
            [
                FFMPEG_BIN, "-y",
                "-f", "lavfi",
                "-i", "anullsrc=r=44100:cl=stereo",
                "-t", str(max(duration, 0.01)),
                "-acodec", "pcm_s16le",
                "-ar", "44100",
                "-ac", "2",
                str(output_path)
            ],
            capture_output=True,
            check=True
        )
    
    @staticmethod
    def _generate_silence_mp3(output_path: Path, duration: float):
        """生成静音 MP3 文件"""
        subprocess.run(
            [
                FFMPEG_BIN, "-y",
                "-f", "lavfi",
                "-i", "anullsrc=r=44100:cl=stereo",
                "-t", str(max(duration, 0.01)),
                "-acodec", "libmp3lame",
                "-q:a", "9",
                str(output_path)
            ],
            capture_output=True,
            check=True
        )


# ── 便捷函数 ──
def sync_and_concatenate(
    segments: List[Tuple[SRTEntry, Path]],
    total_duration: Optional[float] = None
) -> Path:
    """
    完整流程：对齐所有音频切片 -> 拼接为完整配音总轨
    
    Args:
        segments: [(SRTEntry, tts_audio_path)] 列表
        total_duration: 原视频总时长
    
    Returns:
        完整配音轨路径
    """
    ensure_work_dir()
    engine = TimeSyncEngine()
    
    print("[1/2] 音画自动对齐...")
    aligned, reports = engine.sync_all_segments(segments)
    
    print("[2/2] 拼接配音总轨...")
    dubbing_track = engine.concatenate_aligned(aligned, total_duration=total_duration)
    
    print(f"  ✓ 配音总轨: {dubbing_track.name}")
    return dubbing_track


if __name__ == "__main__":
    import sys
    print("Time-Sync 对齐模块（作为模块导入使用）")
