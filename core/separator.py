"""
小欢语音 - 音视频分离模块
优先级：Spleeter > Demucs > FFmpeg 纯滤波（默认回退）
"""
import subprocess
import shutil
from pathlib import Path
from typing import Tuple, Optional

from config import FFMPEG_BIN, WORK_DIR, ensure_work_dir


class AudioSeparator:
    """音视频分离器"""

    def __init__(self, work_dir: Optional[Path] = None):
        self.work_dir = work_dir or WORK_DIR
        ensure_work_dir()
        self._check_ffmpeg()
        self._engine = self._detect_engine()

    def _check_ffmpeg(self):
        try:
            subprocess.run(
                [FFMPEG_BIN, "-version"],
                capture_output=True, check=True, timeout=5,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise RuntimeError("FFmpeg 未安装或不在 PATH 中")

    def _detect_engine(self) -> str:
        """检测可用的分离引擎: demucs_ht / mdx_onnx / ffmpeg(默认)"""
        # HTDemucs（Meta最新模型，最高质量）
        try:
            from core.demucs_runner import separate_demucs
            return "demucs_ht"
        except ImportError:
            pass
        try:
            import spleeter
            return "spleeter"
        except ImportError:
            pass
        try:
            import demucs
            # 验证 demucs 实际可用（torchcodec DLL 问题）
            try:
                from torchaudio import save as _ts
                # 实际调用测试（torchcodec 可能在调用时才加载 DLL）
                import torch, tempfile
                _t = torch.zeros(1, 8000)
                _tmp = tempfile.mktemp(suffix=".wav")
                _ts(_tmp, _t, 8000)
                return "demucs"
            except (ImportError, OSError, RuntimeError):
                import logging; logging.getLogger("CreativeEnginePro").info("[separator] demucs/torchcodec 不可用，回退到 FFmpeg 模式")
                pass
        except ImportError:
            pass
        return "ffmpeg"  # 默认：纯 FFmpeg 滤波，不依赖任何额外包

    @property
    def engine_name(self) -> str:
        return self._engine

    def extract_audio(
        self,
        video_path: Path,
        output_audio: Optional[Path] = None,
        sample_rate: int = 44100,
    ) -> Path:
        """从视频提取完整音频（wav 16bit 立体声）"""
        if output_audio is None:
            output_audio = self.work_dir / "full_audio.wav"

        cmd = [
            FFMPEG_BIN, "-y",
            "-i", str(video_path),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", str(sample_rate),
            "-ac", "2",
            str(output_audio),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            raise RuntimeError(f"音频提取失败: {result.stderr[-500:]}")
        return output_audio

    def separate_vocals(
        self,
        audio_path: Path,
        output_dir: Optional[Path] = None,
    ) -> Tuple[Path, Path]:
        """分离人声与伴奏，自动选择可用引擎"""
        if output_dir is None:
            output_dir = self.work_dir / "separated"
        output_dir.mkdir(parents=True, exist_ok=True)

        if self._engine == "demucs_ht":
            return self._separate_demucs_ht(audio_path)
        elif self._engine == "mdx_onnx":
            return self._separate_mdx_onnx(audio_path)
        elif self._engine == "spleeter":
            return self._separate_spleeter(audio_path, output_dir)
        elif self._engine == "demucs":
            return self._separate_demucs(audio_path, output_dir)
        else:
            return self._separate_ffmpeg(audio_path)

    # ── Demucs HT（Meta 最新 Hybrid Transformer，最高质量）──

    def _separate_demucs_ht(self, audio_path: Path) -> Tuple[Path, Path]:
        """使用 Meta HTDemucs 模型进行顶级人声分离"""
        from core.demucs_runner import separate_demucs

        out_dir = self.work_dir / "_demucs"
        vocals, bgm = separate_demucs(
            str(audio_path),
            str(out_dir),
            model="htdemucs",
        )
        return Path(vocals), Path(bgm)

    # ── MDX-Net ONNX（纯推理，无PyTorch依赖，接近剪映效果）──

    def _separate_mdx_onnx(self, audio_path: Path) -> Tuple[Path, Path]:
        """使用 MDX-Net ONNX 模型进行高质量人声分离"""
        from core.mdx_separator import MDXNetSeparator

        sep = MDXNetSeparator()
        vocals_path, bgm_path = sep.separate(
            str(audio_path),
            output_dir=str(self.work_dir),
        )
        return Path(vocals_path), Path(bgm_path)

    # ── Spleeter ──

    def _separate_spleeter(self, audio_path: Path, output_dir: Path) -> Tuple[Path, Path]:
        from spleeter.separator import Separator

        separator = Separator("spleeter:2stems")
        separator.separate_to_file(
            str(audio_path), str(output_dir),
            codec="wav", sync=True,
        )
        separated_dir = output_dir / audio_path.stem
        vocals = separated_dir / "vocals.wav"
        accompaniment = separated_dir / "accompaniment.wav"

        if not vocals.exists() or not accompaniment.exists():
            raise RuntimeError("Spleeter 分离失败")

        final_v = self.work_dir / "vocals.wav"
        final_a = self.work_dir / "accompaniment.wav"
        shutil.move(str(vocals), str(final_v))
        shutil.move(str(accompaniment), str(final_a))
        shutil.rmtree(separated_dir, ignore_errors=True)
        return final_v, final_a

    # ── Demucs ──

    def _separate_demucs(self, audio_path: Path, output_dir: Path) -> Tuple[Path, Path]:
        out_path = self.work_dir

        if shutil.which("demucs"):
            cmd = ["demucs", "--two-stems=vocals", "-o", str(out_path), str(audio_path)]
        else:
            cmd = [subprocess.sys.executable, "-m", "demucs", "--two-stems=vocals",
                   "-o", str(out_path), str(audio_path)]

        result = subprocess.run(cmd, capture_output=True, timeout=600)
        if result.returncode != 0:
            err = result.stderr.decode(errors='replace')[-800:] if result.stderr else "(无输出)"
            raise RuntimeError(f"Demucs 分离失败: {err}")

        audio_stem = audio_path.stem
        model_dirs = list(out_path.iterdir()) if out_path.exists() else []
        vocals_path = no_vocals_path = None

        for model_dir in model_dirs:
            if not model_dir.is_dir():
                continue
            candidate = model_dir / audio_stem
            if candidate.is_dir():
                v = candidate / "vocals.wav"
                nv = candidate / "no_vocals.wav"
                if v.exists(): vocals_path = v
                if nv.exists(): no_vocals_path = nv

        if not vocals_path or not no_vocals_path:
            raise RuntimeError(f"Demucs 输出未找到\nstderr: {result.stderr[-300:]}")

        final_v = self.work_dir / "vocals.wav"
        final_a = self.work_dir / "accompaniment.wav"
        shutil.move(str(vocals_path), str(final_v))
        shutil.move(str(no_vocals_path), str(final_a))
        for d in model_dirs:
            if d.is_dir() and d.name != "separated":
                shutil.rmtree(d, ignore_errors=True)
        return final_v, final_a

    # ── FFmpeg 纯滤波（默认回退）──

    def _separate_ffmpeg(self, audio_path: Path) -> Tuple[Path, Path]:
        """
        使用 FFmpeg 滤波器进行人声分离（中心声道提取法）。
        原理：立体声中人声通常居中（L=R），左右差值就是背景音乐。
        不依赖任何额外 Python 包，只需 ffmpeg 即可工作。
        """
        final_v = self.work_dir / "vocals.wav"
        final_a = self.work_dir / "accompaniment.wav"
        src = str(audio_path)

        # 提取人声：左右声道相加 → 中心声道（人声）
        cmd_v = [
            FFMPEG_BIN, "-y", "-i", src,
            "-af", "pan=mono|c0=c0+c1",
            "-acodec", "pcm_s16le", "-ar", "44100",
            str(final_v),
        ]
        r = subprocess.run(cmd_v, capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 0:
            raise RuntimeError(f"FFmpeg 人声提取失败: {r.stderr[-400:]}")

        # 提取背景音：原音频 - 人声（反相抵消中心声道）
        cmd_a = [
            FFMPEG_BIN, "-y", "-i", src, "-i", str(final_v),
            "-filter_complex", "[0:a][1:a]amix=inputs=2:weights=1 -1:duration=first[aout]",
            "-map", "[aout]", "-acodec", "pcm_s16le", "-ar", "44100",
            str(final_a),
        ]
        r = subprocess.run(cmd_a, capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 0:
            raise RuntimeError(f"FFmpeg 背景提取失败: {r.stderr[-400:]}")

        return final_v, final_a

    def process_video(self, video_path: Path) -> Tuple[Path, Path]:
        """完整流程：视频 → 提取音频 → 分离人声/伴奏"""
        import logging
        log = logging.getLogger("CreativeEnginePro")
        log.info(f"[1/2] 提取音轨: {video_path.name}")
        full_audio = self.extract_audio(video_path)
        log.info(f"[2/2] 分离人声/伴奏 ({self._engine})...")
        vocals, accomp = self.separate_vocals(full_audio)
        full_audio.unlink(missing_ok=True)
        log.info(f"  ✓ 人声: {vocals.name}")
        log.info(f"  ✓ 背景: {accomp.name}")
        return vocals, accomp


def separate_audio_from_video(video_path: Path) -> Tuple[Path, Path]:
    separator = AudioSeparator()
    return separator.process_video(video_path)
