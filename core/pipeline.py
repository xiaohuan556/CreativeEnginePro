"""
GlobalFlux AI - 全链路流水线编排器
CLI 和 GUI 共用此模块，无 GUI 依赖。
负责：5 步流水线串联 + 进度回调 + 批量处理
"""
import time
import shutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable, Optional, List

from config import WORK_DIR, ensure_work_dir


class CancelledError(Exception):
    """流水线被用户取消"""
    pass


# ── 5 步定义 ──
STEPS = [
    "音视频分离",
    "AI 听写翻译",
    "TTS 配音",
    "音画对齐",
    "混剪渲染",
]


@dataclass
class PipelineConfig:
    """流水线配置"""
    video_path: Path
    target_lang: str = "en"
    whisper_model: str = "base"
    voice_preset: str = "default"
    target_aspect: str = "original"
    bgm_volume: float = 0.3
    voice_volume: float = 0.7
    apply_dedup: bool = True
    output_dir: Optional[Path] = None


@dataclass
class StepResult:
    """单步执行结果"""
    step: int
    name: str
    success: bool
    output_path: Optional[Path] = None
    duration_sec: float = 0.0
    message: str = ""
    detail: dict = field(default_factory=dict)


class Pipeline:
    """
    全链路流水线编排器

    使用方式：
        config = PipelineConfig(video_path=Path("input.mp4"))
        pipe = Pipeline(config, on_step=..., on_progress=..., on_log=...)
        results = pipe.run()
    """

    def __init__(
        self,
        config: PipelineConfig,
        on_step: Optional[Callable[[int, str], None]] = None,
        on_progress: Optional[Callable[[int, str], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
    ):
        self.config = config
        self._on_step = on_step or (lambda i, s: None)
        self._on_progress = on_progress or (lambda p, s: None)
        self._on_log = on_log or (lambda s: None)
        self._cancelled = False

    def cancel(self):
        """取消流水线"""
        self._cancelled = True

    def _check_cancel(self):
        """检查取消标志，如已取消则抛出异常"""
        if self._cancelled:
            raise CancelledError()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def run(self) -> List[StepResult]:
        """
        执行全链路，返回每步结果。
        每一步失败后即停止，不会继续后续步骤。
        """
        results = []
        c = self.config

        # 确保输出目录
        if c.output_dir is None:
            c.output_dir = WORK_DIR / "output"
        c.output_dir.mkdir(parents=True, exist_ok=True)
        ensure_work_dir()

        self._log(f"🚀 开始处理: {c.video_path.name}")
        self._log(f"   目标语言: {c.target_lang} | 声线: {c.voice_preset} | 比例: {c.target_aspect}")

        try:
            # ── Step 1: 音视频分离 ─────────────────────────────────────────
            self._step(0, "active")
            t0 = time.time()
            from core.separator import AudioSeparator
            sep = AudioSeparator()
            vocals, bgm = sep.process_video(c.video_path)
            results.append(StepResult(
                step=0, name=STEPS[0], success=True,
                output_path=vocals,
                duration_sec=round(time.time() - t0, 1),
                message=f"人声+BG分离完成 → {vocals.name}, {bgm.name}",
                detail={"vocals": str(vocals), "bgm": str(bgm)},
            ))
            self._step(0, "done")
            self._progress(20)
            self._check_cancel()

            # ── Step 2: ASR + 翻译 ────────────────────────────────────────
            self._step(1, "active")
            t0 = time.time()
            from core.transcriber import Transcriber, Translator
            trans = Transcriber(model_size=c.whisper_model)
            entries = trans.transcribe(vocals)
            translator = Translator(target_lang=c.target_lang)
            translated = translator.translate(entries)
            results.append(StepResult(
                step=1, name=STEPS[1], success=True,
                duration_sec=round(time.time() - t0, 1),
                message=f"ASR {len(entries)} 条 → 翻译 {len(translated)} 条",
                detail={"source_count": len(entries), "translated_count": len(translated)},
            ))
            self._step(1, "done")
            self._progress(40)
            self._check_cancel()

            # ── Step 3: TTS ───────────────────────────────────────────────
            self._step(2, "active")
            t0 = time.time()
            from core.tts_factory import create_engine, TTSEngineType
            from config import TTS_ENGINE, ELEVENLABS_API_KEY
            # 根据配置选择 TTS 引擎
            if TTS_ENGINE == "elevenlabs" and ELEVENLABS_API_KEY:
                etype = TTSEngineType.ELEVENLABS
            elif TTS_ENGINE == "edge":
                etype = TTSEngineType.EDGE_TTS
            else:
                etype = None  # 让工厂自动选择
            tts = create_engine(etype, voice=c.voice_preset)
            tts_segments = tts.synthesize_srt(translated, voice_preset=c.voice_preset)
            results.append(StepResult(
                step=2, name=STEPS[2], success=True,
                duration_sec=round(time.time() - t0, 1),
                message=f"生成 {len(tts_segments)} 条音频",
                detail={"segment_count": len(tts_segments)},
            ))
            self._step(2, "done")
            self._progress(60)
            self._check_cancel()

            # ── Step 4: 音画对齐 ──────────────────────────────────────────
            self._step(3, "active")
            t0 = time.time()
            from core.time_sync import TimeSyncEngine
            sync = TimeSyncEngine()
            aligned, reports = sync.sync_all_segments(tts_segments)

            # 获取原视频时长
            from core.mixer import VideoMixer
            dur = VideoMixer().get_video_duration(c.video_path)
            dub_track = sync.concatenate_aligned(aligned, total_duration=dur)

            speed_c = sum(1 for r in reports if "speed_up" in r["action"])
            pad_c = sum(1 for r in reports if "pad_silence" in r["action"])
            trim_c = sum(1 for r in reports if "trim" in r["action"])
            results.append(StepResult(
                step=3, name=STEPS[3], success=True,
                output_path=dub_track,
                duration_sec=round(time.time() - t0, 1),
                message=f"加速 {speed_c} | 填充 {pad_c} | 截断 {trim_c}",
                detail={
                    "speed_up": speed_c,
                    "pad_silence": pad_c,
                    "trim": trim_c,
                    "total": len(reports),
                },
            ))
            self._step(3, "done")
            self._progress(80)
            self._check_cancel()

            # ── Step 5: 混剪渲染 ──────────────────────────────────────────
            self._step(4, "active")
            t0 = time.time()
            mixer = VideoMixer()
            mixed = mixer.mix_audio_tracks(
                voice_track=dub_track,
                bgm_track=bgm,
                target_duration=dur,
                voice_ratio=c.voice_volume,
                bgm_ratio=c.bgm_volume,
            )
            out_path = c.output_dir / f"{c.video_path.stem}_{c.target_lang}.mp4"
            final = mixer.render_final_video(
                source_video=c.video_path,
                mixed_audio=mixed,
                output_path=out_path,
                target_aspect=c.target_aspect,
                apply_dedup=c.apply_dedup,
            )
            size_mb = final.stat().st_size / (1024 * 1024)
            results.append(StepResult(
                step=4, name=STEPS[4], success=True,
                output_path=final,
                duration_sec=round(time.time() - t0, 1),
                message=f"输出 {final.name} ({size_mb:.1f}MB)",
                detail={"output_path": str(final), "size_mb": round(size_mb, 1)},
            ))
            self._step(4, "done")
            self._progress(100)

            total_sec = sum(r.duration_sec for r in results)
            self._log(f"✅ 全链路完成，总耗时 {total_sec:.1f}s → {final.name}")

        except CancelledError:
            self._log("⏹ 已取消")
        except Exception as e:
            step_idx = len(results)
            step_name = STEPS[step_idx] if step_idx < len(STEPS) else "未知"
            results.append(StepResult(
                step=step_idx, name=step_name, success=False,
                duration_sec=0, message=f"❌ {e}",
            ))
            self._step(step_idx, "error")
            self._log(f"❌ 第 {step_idx + 1} 步 [{step_name}] 失败: {e}")

        return results


class BatchPipeline:
    """
    批量流水线处理器

    对一个目录下的所有视频依次执行流水线。
    """

    def __init__(
        self,
        config_template: PipelineConfig,
        on_video_start: Optional[Callable[[int, int, Path], None]] = None,
        on_video_done: Optional[Callable[[int, int, Path, List[StepResult]], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
    ):
        self.config_template = config_template
        self._on_video_start = on_video_start or (lambda i, t, p: None)
        self._on_video_done = on_video_done or (lambda i, t, p, r: None)
        self._on_log = on_log or (lambda s: None)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self, video_dir: Path) -> dict:
        """
        批量处理目录下所有视频。

        Returns:
            {"total": N, "success": M, "failed": K, "results": [...]}
        """
        extensions = (".mp4", ".mov", ".avi", ".mkv")
        videos = []
        for ext in extensions:
            videos.extend(video_dir.glob(f"*{ext}"))
            videos.extend(video_dir.glob(f"*{ext.upper()}"))
        videos = sorted(set(videos))

        total = len(videos)
        self._on_log(f"📂 找到 {total} 个视频")

        all_results = []
        success = 0
        failed = 0

        for i, video in enumerate(videos):
            if self._cancelled:
                self._on_log("⏹ 批量处理已取消")
                break

            self._on_video_start(i + 1, total, video)
            self._on_log(f"\n[{i + 1}/{total}] {video.name}")

            cfg = PipelineConfig(
                video_path=video,
                target_lang=self.config_template.target_lang,
                whisper_model=self.config_template.whisper_model,
                voice_preset=self.config_template.voice_preset,
                target_aspect=self.config_template.target_aspect,
                bgm_volume=self.config_template.bgm_volume,
                voice_volume=self.config_template.voice_volume,
                apply_dedup=self.config_template.apply_dedup,
                output_dir=self.config_template.output_dir,
            )

            pipe = Pipeline(
                config=cfg,
                on_log=lambda msg: self._on_log(f"  {msg}"),
            )

            results = pipe.run()
            all_results.append({"video": str(video), "results": results})

            if all(r.success for r in results):
                success += 1
            else:
                failed += 1

            self._on_video_done(i + 1, total, video, results)

        summary = {
            "total": total,
            "success": success,
            "failed": failed,
            "results": all_results,
        }
        self._on_log(f"\n📊 批量完成: 成功 {success}/{total}, 失败 {failed}")
        return summary
