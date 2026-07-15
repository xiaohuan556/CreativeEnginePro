"""
GlobalFlux AI - 海量矩阵去重混剪与最终渲染合成模块
负责：
  1. 配音总轨与原厂 BGM 混合（6:4 / 7:3 比例）
  2. 画面尺寸适配（9:16 / 16:9 / 1:1）
  3. 防重复算法（像素偏移、色彩抖动、噪点）
  4. 批量命名一键导出
"""
import json
import subprocess
import shutil
import random
from pathlib import Path
from typing import Optional, Tuple

from config import (
    FFMPEG_BIN, FFPROBE_BIN,
    BGM_VOICE_RATIO,
    OUTPUT_ASPECT,
    WORK_DIR, ensure_work_dir
)


class VideoMixer:
    """视频混剪合成器"""
    
    def __init__(self, work_dir: Optional[Path] = None):
        self.work_dir = work_dir or WORK_DIR / "mix_output"
        self.work_dir.mkdir(parents=True, exist_ok=True)
    
    def get_video_duration(self, video_path: Path) -> float:
        """获取视频时长"""
        result = subprocess.run(
            [
                FFPROBE_BIN, "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "json",
                str(video_path)
            ],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return float(data["format"]["duration"])
        return 0.0
    
    def get_video_info(self, video_path: Path) -> dict:
        """获取视频详细信息（宽、高、时长、帧率）"""
        result = subprocess.run(
            [
                FFPROBE_BIN, "-v", "quiet",
                "-show_entries", "stream=width,height,r_frame_rate,duration",
                "-show_entries", "format=duration",
                "-of", "json",
                str(video_path)
            ],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            return {}
        
        data = json.loads(result.stdout)
        video_stream = None
        for stream in data.get("streams", []):
            if stream.get("width"):
                video_stream = stream
                break
        
        info = {
            "duration": float(data.get("format", {}).get("duration", 0)),
        }
        if video_stream:
            info["width"] = video_stream.get("width", 0)
            info["height"] = video_stream.get("height", 0)
            # 解析帧率
            fps_str = video_stream.get("r_frame_rate", "30/1")
            if "/" in fps_str:
                num, den = fps_str.split("/")
                info["fps"] = float(num) / float(den) if float(den) > 0 else 30
            else:
                info["fps"] = float(fps_str)
        
        return info
    
    def mix_audio_tracks(
        self,
        voice_track: Path,
        bgm_track: Path,
        output_path: Optional[Path] = None,
        voice_ratio: Optional[float] = None,
        bgm_ratio: Optional[float] = None,
        target_duration: Optional[float] = None
    ) -> Path:
        """
        混合配音总轨与 BGM 伴奏轨
        
        Args:
            voice_track: 配音总轨路径
            bgm_track: BGM 伴奏轨路径
            output_path: 混合后音频输出路径
            voice_ratio: 人声音量比例（默认 0.7）
            bgm_ratio: BGM 音量比例（默认 0.3）
            target_duration: 目标时长（秒），用于截断
        
        Returns:
            混合音频路径
        """
        if output_path is None:
            output_path = WORK_DIR / "mixed_audio.wav"
        
        v_ratio = voice_ratio if voice_ratio is not None else BGM_VOICE_RATIO[0]
        b_ratio = bgm_ratio if bgm_ratio is not None else BGM_VOICE_RATIO[1]
        
        # 使用 FFmpeg amix 混合
        # amix 的 weights 参数直接控制音轨音量权重
        cmd = [
            FFMPEG_BIN, "-y",
            "-i", str(voice_track),
            "-i", str(bgm_track),
            "-filter_complex",
            f"[0:a]volume={v_ratio}[voice];"
            f"[1:a]volume={b_ratio}[bgm];"
            f"[voice][bgm]amix=inputs=2:duration=longest:dropout_transition=2[aout]",
            "-map", "[aout]",
            "-acodec", "pcm_s16le",
            "-ar", "44100",
            "-ac", "2",
        ]
        
        if target_duration:
            cmd.extend(["-t", str(target_duration)])
        
        cmd.append(str(output_path))
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"音频混合失败: {result.stderr[:300]}")
        
        return output_path
    
    def adapt_aspect_ratio(
        self,
        width: int,
        height: int,
        target_aspect: str
    ) -> Tuple[int, int, str]:
        """
        计算画面适配参数
        
        Args:
            width: 原始宽度
            height: 原始高度
            target_aspect: 目标比例 "9:16"|"16:9"|"1:1"|"original"
        
        Returns:
            (target_width, target_height, filter_str) 元组
        """
        if target_aspect == "original":
            return width, height, ""
        
        # 解析目标比例
        parts = target_aspect.split(":")
        aspect_w = int(parts[0])
        aspect_h = int(parts[1])
        target_ratio = aspect_w / aspect_h
        source_ratio = width / height
        
        if target_aspect == "9:16":
            # 竖屏：窄变宽 -> 高斯模糊背景填充
            target_width = int(height * target_ratio)
            target_height = height
            if target_width > width:
                # 源比目标窄，需要填充
                # 策略：中心裁剪 + 双层高斯模糊背景
                scale_w = target_width
                scale_h = height
                filter_str = (
                    f"[0:v]split[original][blurred];"
                    f"[blurred]scale={scale_w}:{scale_h}:force_original_aspect_ratio=increase,"
                    f"gblur=sigma=20[bg];"
                    f"[original]scale={scale_w}:{scale_h}:force_original_aspect_ratio=decrease[fg];"
                    f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
                )
                return target_width, target_height, filter_str
            else:
                # 源比目标宽，直接中心裁剪
                crop_w = int(height * target_ratio)
                x_offset = (width - crop_w) // 2
                filter_str = f"crop={crop_w}:{height}:{x_offset}:0"
                return crop_w, height, filter_str
        
        elif target_aspect == "16:9":
            # 横屏：宽变窄 -> 高斯模糊背景填充
            target_width = width
            target_height = int(width / target_ratio)
            if target_height > height:
                # 源比目标矮，需要填充
                scale_w = width
                scale_h = target_height
                filter_str = (
                    f"[0:v]split[original][blurred];"
                    f"[blurred]scale={scale_w}:{scale_h}:force_original_aspect_ratio=increase,"
                    f"gblur=sigma=20[bg];"
                    f"[original]scale={scale_w}:{scale_h}:force_original_aspect_ratio=decrease[fg];"
                    f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
                )
                return width, target_height, filter_str
            else:
                crop_h = int(width / target_ratio)
                y_offset = (height - crop_h) // 2
                filter_str = f"crop={width}:{crop_h}:0:{y_offset}"
                return width, crop_h, filter_str
        
        elif target_aspect == "1:1":
            # 正方形
            side = min(width, height)
            x_offset = (width - side) // 2
            y_offset = (height - side) // 2
            filter_str = f"crop={side}:{side}:{x_offset}:{y_offset}"
            return side, side, filter_str
        
        return width, height, ""
    
    def apply_anti_duplicate(
        self,
        seed: Optional[int] = None
    ) -> str:
        """
        生成防重复滤镜链
        包括：微小像素偏移、轻微色彩/对比度抖动、细微噪点
        
        Args:
            seed: 随机种子
        
        Returns:
            FFmpeg 滤镜字符串
        """
        rng = random.Random(seed)
        
        # 随机参数（范围很小，肉眼不可见但能过机审）
        dx = rng.randint(-3, 3)       # 水平偏移 -3~3 像素
        dy = rng.randint(-3, 3)       # 垂直偏移 -3~3 像素
        brightness = rng.uniform(-0.01, 0.01)  # 亮度微调
        contrast = rng.uniform(0.98, 1.02)     # 对比度微调
        saturation = rng.uniform(0.98, 1.02)   # 饱和度微调
        noise_strength = rng.randint(1, 3)     # 噪点强度
        
        filters = []
        
        # 像素偏移
        if dx != 0 or dy != 0:
            # pad + overlay 实现偏移
            filters.append(f"pad=iw+6:ih+6:3:3:color=black,overlay={dx}:{dy}")
        
        # 色彩抖动
        filters.append(
            f"eq=brightness={brightness:.3f}:contrast={contrast:.3f}:saturation={saturation:.3f}"
        )
        
        # 细微噪点
        filters.append(f"noise=alls={noise_strength}:allf=t")
        
        return ",".join(filters)
    
    def render_final_video(
        self,
        source_video: Path,
        mixed_audio: Path,
        output_path: Optional[Path] = None,
        target_aspect: Optional[str] = None,
        apply_dedup: bool = True,
        dedup_seed: Optional[int] = None
    ) -> Path:
        """
        最终渲染合成：替换原视频音轨为混合音轨，应用画面适配和防重复
        
        Args:
            source_video: 原始视频路径
            mixed_audio: 混合音频路径
            output_path: 输出视频路径
            target_aspect: 目标画面比例
            apply_dedup: 是否应用防重复滤镜
            dedup_seed: 防重复随机种子
        
        Returns:
            输出视频路径
        """
        if output_path is None:
            output_path = self.work_dir / "output_final.mp4"
        
        target_aspect = target_aspect or OUTPUT_ASPECT
        
        # 获取视频信息
        info = self.get_video_info(source_video)
        width = info.get("width", 1920)
        height = info.get("height", 1080)
        duration = info.get("duration", 0)
        
        # 构建视频滤镜链
        vfilters = []
        
        # 画面适配
        _, _, aspect_filter = self.adapt_aspect_ratio(width, height, target_aspect)
        if aspect_filter:
            vfilters.append(aspect_filter)
        
        # 防重复
        if apply_dedup:
            dedup_filter = self.apply_anti_duplicate(seed=dedup_seed)
            vfilters.append(dedup_filter)
        
        # 构建 FFmpeg 命令
        cmd = [
            FFMPEG_BIN, "-y",
            "-i", str(source_video),
            "-i", str(mixed_audio),
            "-map", "0:v",
            "-map", "1:a",
        ]
        
        if vfilters:
            cmd.extend(["-vf", ",".join(vfilters)])
        
        cmd.extend([
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            str(output_path)
        ])
        
        import logging; logging.getLogger("CreativeEnginePro").info(f"  渲染最终视频: {output_path.name}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            raise RuntimeError(f"视频渲染失败: {result.stderr[:300]}")
        
        return output_path
    
    def batch_render(
        self,
        source_video: Path,
        mixed_audio: Path,
        output_dir: Optional[Path] = None,
        count: int = 5,
        target_aspect: Optional[str] = None
    ) -> list:
        """
        批量渲染去重视频
        
        Args:
            source_video: 原始视频
            mixed_audio: 混合音频
            output_dir: 输出目录
            count: 生成数量
            target_aspect: 画面比例
        
        Returns:
            输出文件路径列表
        """
        if output_dir is None:
            output_dir = self.work_dir / "batch_output"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        results = []
        for i in range(count):
            output_path = output_dir / f"output_{i + 1:03d}.mp4"
            seed = random.randint(1, 999999)
            
            try:
                path = self.render_final_video(
                    source_video=source_video,
                    mixed_audio=mixed_audio,
                    output_path=output_path,
                    target_aspect=target_aspect,
                    apply_dedup=True,
                    dedup_seed=seed
                )
                results.append(path)
                print(f"  ✓ [{i + 1}/{count}] {path.name}")
            except Exception as e:
                print(f"  ✗ [{i + 1}/{count}] 渲染失败: {e}")
        
        return results


# ── 便捷函数 ──
def render_output_video(
    source_video: Path,
    dubbing_track: Path,
    accompaniment: Path,
    output_path: Optional[Path] = None,
    target_aspect: Optional[str] = None
) -> Path:
    """
    完整流程：混合配音+BGM -> 渲染最终视频
    
    Args:
        source_video: 原始视频路径
        dubbing_track: 配音总轨路径
        accompaniment: BGM 伴奏轨路径
        output_path: 输出视频路径
        target_aspect: 画面比例
    
    Returns:
        输出视频路径
    """
    ensure_work_dir()
    mixer = VideoMixer()
    
    # 获取视频时长
    video_duration = mixer.get_video_duration(source_video)
    
    print("[1/2] 混合配音与 BGM...")
    mixed_audio = mixer.mix_audio_tracks(
        voice_track=dubbing_track,
        bgm_track=accompaniment,
        target_duration=video_duration
    )
    
    print("[2/2] 渲染最终视频...")
    output = mixer.render_final_video(
        source_video=source_video,
        mixed_audio=mixed_audio,
        output_path=output_path,
        target_aspect=target_aspect
    )
    
    return output


if __name__ == "__main__":
    print("VideoMixer 模块（作为模块导入使用）")
