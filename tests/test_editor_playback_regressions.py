import os
import array
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QPointF, QEvent, Qt
    from PyQt6.QtGui import QColor, QImage, QMouseEvent, QPixmap
    from PyQt6.QtWidgets import QApplication

    from core.edit_engine import (
        EditTimeline,
        FFmpegDirectExportWorker,
        AudioClip,
        SubtitleBlock,
        VideoClip,
    )
    from ui.preview_player import PreviewPlayer
    from ui.timeline_widget import TimelineWidget
    from utils.alpha_video import probe_has_audio
    from utils.ffmpeg_utils import get_ffmpeg_path

    QT_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    QT_AVAILABLE = False


class _ClockPlayer:
    class PlaybackState:
        PlayingState = 1

    def __init__(self, source_clock):
        self.source_clock = source_clock

    def playbackState(self):
        return self.PlaybackState.PlayingState

    def audio_clock_sec(self):
        return self.source_clock

    def stop(self):
        pass


class _CoarseClockPreview:
    def __init__(self, value):
        self.value = value

    def master_clock_sec(self):
        return self.value

    def stop_audio(self):
        pass

    def set_playing(self, _value):
        pass

    def set_decode_state(self, _value):
        pass


class _RatePlayer:
    class PlaybackState:
        PlayingState = 1
        StoppedState = 0

    def __init__(self):
        self.state = self.PlaybackState.StoppedState
        self.rates = []
        self._position = 0

    def playbackState(self):
        return self.state

    def setSource(self, _value):
        pass

    def setPlaybackRate(self, value):
        self.rates.append(value)

    def setPosition(self, value):
        self._position = value

    def position(self):
        return self._position

    def play(self):
        self.state = self.PlaybackState.PlayingState

    def stop(self):
        self.state = self.PlaybackState.StoppedState


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 runtime is not available")
class EditorPlaybackRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_speed_audio_clock_is_mapped_back_to_timeline_time(self):
        timeline = EditTimeline()
        clip = VideoClip(
            source_path="speed-source.mp4",
            source_duration=20.0,
            trim_start=4.0,
            trim_end=12.0,
            timeline_start=10.0,
            speed=2.0,
        )
        timeline.add_video_clip(clip)
        preview = PreviewPlayer(timeline)
        preview._current_sec = 11.0
        preview._audio_players = [(_ClockPlayer(source_clock=6.0), None)]

        # 源时钟 6s 对应 (6-4)/2 + 10 = 时间线 11s，而不是源时间 6s。
        self.assertAlmostEqual(11.0, preview.master_clock_sec())
        preview.close()

    def test_decode_ahead_does_not_precompose_future_payloads(self):
        preview = PreviewPlayer(EditTimeline())
        calls = []

        def compute(sec, write_pending=True):
            calls.append((sec, write_pending))
            return {
                "raw": "frame", "clip": None, "subs": [], "ovs": [],
                "cleared": False, "is_image": False, "w": 1, "h": 1,
                "trans": None,
            }

        preview._compute_payload = compute
        preview._playing = True
        preview._fetch_frame(1.0, ahead=12)

        self.assertEqual([(1.0, True)], calls)
        self.assertEqual([1.0], list(preview._payload_ring.keys()))
        preview.close()

    def test_same_source_explicitly_resets_playback_rate_to_normal(self):
        with tempfile.TemporaryDirectory() as directory:
            source = str(Path(directory) / "audio.wav")
            Path(source).write_bytes(b"placeholder")
            preview = PreviewPlayer(EditTimeline())
            player = _RatePlayer()
            preview._ensure_audio_player = lambda _slot: (player, None)
            preview._ensure_audio_for_video = lambda _path: source

            self.assertTrue(preview.play_audio(source, 0.0, rate=2.0))
            self.assertTrue(preview.play_audio(source, 0.0, rate=1.0))

            self.assertEqual([2.0, 1.0], player.rates)
            preview.close()

    def test_coarse_audio_clock_does_not_freeze_playhead_between_updates(self):
        timeline = EditTimeline()
        timeline.add_video_clip(VideoClip(
            source_path="clock.mp4", source_duration=5.0,
            trim_start=0.0, trim_end=5.0, timeline_start=0.0,
        ))
        widget = TimelineWidget(timeline)
        widget._preview_player = _CoarseClockPreview(0.0)
        widget._playing = True
        widget._last_tick = time.perf_counter() - 0.033

        widget._tick_play()

        self.assertGreater(widget.get_playhead(), 0.015)
        self.assertLess(widget.get_playhead(), 0.05)
        widget.close()

    def test_discontinuous_audio_sample_reanchors_without_playhead_jump(self):
        timeline = EditTimeline()
        timeline.add_video_clip(VideoClip(
            source_path="clock-jump.mp4", source_duration=60.0,
            trim_start=0.0, trim_end=60.0, timeline_start=0.0,
        ))
        widget = TimelineWidget(timeline)
        widget._preview_player = _CoarseClockPreview(25.0)
        widget._playing = True
        widget._last_tick = time.perf_counter() - 0.033

        widget._tick_play()

        # 播放器瞬时跳到 25s 只更新 offset；播放头仍连续走约一帧。
        self.assertGreater(widget.get_playhead(), 0.015)
        self.assertLess(widget.get_playhead(), 0.05)
        self.assertAlmostEqual(
            widget.get_playhead() - 25.0,
            widget._audio_timeline_offset,
            places=3,
        )
        widget.close()

    def test_master_clock_prefers_started_clip_mapping_at_speed_boundary(self):
        timeline = EditTimeline()
        timeline.add_video_clip(VideoClip(
            source_path="speed-boundary.mp4", source_duration=30.0,
            trim_start=0.0, trim_end=8.0, timeline_start=0.0, speed=2.0,
        ))
        preview = PreviewPlayer(timeline)
        preview._current_sec = 4.0
        preview._audio_players = [(_ClockPlayer(source_clock=8.0), None)]
        preview._audio_clock_map = (0.0, 0.0, 2.0)

        self.assertAlmostEqual(4.0, preview.master_clock_sec())
        preview.close()

    def test_trim_handle_emits_live_preview_at_new_out_frame(self):
        timeline = EditTimeline()
        clip = VideoClip(
            source_path="trim.mp4", source_duration=5.0,
            trim_start=0.0, trim_end=5.0, timeline_start=0.0,
        )
        timeline.add_video_clip(clip)
        widget = TimelineWidget(timeline)
        canvas = widget._canvas
        track = next(td for td in canvas._tracks
                     if td.kind == "video" and td.idx == 0)
        rect = canvas._clip_rect(clip, track)
        y = rect.center().y()
        emitted = []
        canvas.playhead_moved.connect(emitted.append)

        press_pos = QPointF(rect.right() - 1, y)
        canvas.mousePressEvent(QMouseEvent(
            QEvent.Type.MouseButtonPress, press_pos, press_pos,
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        ))
        move_pos = QPointF(rect.right() - 100, y)
        canvas.mouseMoveEvent(QMouseEvent(
            QEvent.Type.MouseMove, move_pos, move_pos,
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        ))

        self.assertTrue(emitted)
        self.assertLess(clip.trim_end, 5.0)
        self.assertAlmostEqual(clip.timeline_end - 0.5 / widget.fps,
                               emitted[-1], places=3)
        widget.close()

    def test_scrubbing_holds_last_frame_when_stale_request_clears(self):
        timeline = EditTimeline()
        timeline.add_video_clip(VideoClip(
            source_path="trim-race.mp4", source_duration=5.0,
            trim_start=0.0, trim_end=3.0, timeline_start=0.0,
        ))
        preview = PreviewPlayer(timeline)
        preview._canvas_w = 320
        preview._canvas_h = 180
        last = QImage(320, 180, QImage.Format.Format_ARGB32)
        last.fill(QColor("#d02020"))
        preview._last_frame_image = last.copy()
        preview._last_frame_no_subs = last.copy()
        preview._last_raw_img = last.copy()
        preview._screen.setPixmap(QPixmap.fromImage(last))
        preview.set_decode_state("scrubbing")

        # 模拟拖短到 3s 后，旧的 4s 请求才返回“无片段”。
        preview.seek(4.0)
        self.assertIsNotNone(preview._last_frame_image)
        with preview._frame_lock:
            preview._payload_ring.clear()
            preview._pending_raw = None
            preview._pending_cleared = True
        preview._flush_frame()

        shown = preview._screen.pixmap().toImage()
        self.assertEqual(QColor("#d02020"), shown.pixelColor(10, 10))
        preview.close()


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 runtime is not available")
class ComplexSubtitleAudioExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _assert_export_has_visible_blue_frame(self, ffmpeg, output):
        decoded_video = subprocess.run([
            ffmpeg, "-v", "error", "-ss", "0.25", "-i", output,
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(0, decoded_video.returncode, decoded_video.stderr.decode("utf-8", "replace"))
        self.assertEqual(160 * 90 * 3, len(decoded_video.stdout))
        rgb = decoded_video.stdout
        red = sum(rgb[0::3]) / (160 * 90)
        green = sum(rgb[1::3]) / (160 * 90)
        blue = sum(rgb[2::3]) / (160 * 90)
        self.assertGreater(blue, 80, f"exported video is black: RGB=({red:.1f}, {green:.1f}, {blue:.1f})")
        self.assertGreater(blue, red * 2)

    def test_word_animated_subtitle_export_retains_audio_stream(self):
        ffmpeg = get_ffmpeg_path()
        if not ffmpeg or not os.path.exists(ffmpeg):
            self.skipTest("FFmpeg is not available")

        with tempfile.TemporaryDirectory() as directory:
            source = str(Path(directory) / "source.mp4")
            output = str(Path(directory) / "output.mp4")
            generated = subprocess.run([
                ffmpeg, "-y",
                "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=12:d=0.6",
                "-f", "lavfi", "-i", "sine=frequency=660:duration=0.6",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", source,
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            self.assertEqual(0, generated.returncode)

            timeline = EditTimeline()
            timeline.add_video_clip(VideoClip(
                source_path=source, source_duration=0.6,
                trim_start=0.0, trim_end=0.6, timeline_start=0.0,
            ))
            timeline.add_subtitle(SubtitleBlock(
                text="逐词字幕", timeline_start=0.0, timeline_end=0.6,
                from_asr=True, word_animation=True,
            ))
            worker = FFmpegDirectExportWorker(
                timeline, output, (160, 90), fps=12.0)
            results = []
            worker.finished.connect(lambda ok, value: results.append((ok, value)))

            worker._do_export()

            self.assertTrue(results, "export worker did not emit a result")
            self.assertTrue(results[-1][0], results[-1][1])
            self.assertTrue(os.path.exists(output))
            self.assertTrue(probe_has_audio(output))
            decoded_audio = subprocess.run([
                ffmpeg, "-v", "error", "-i", output,
                "-map", "0:a:0", "-ac", "1", "-f", "s16le", "-",
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            self.assertEqual(0, decoded_audio.returncode)
            self.assertTrue(decoded_audio.stdout)
            self.assertTrue(any(decoded_audio.stdout), "exported audio is digital silence")

            self._assert_export_has_visible_blue_frame(ffmpeg, output)

    def test_direct_export_has_visible_video_frame(self):
        ffmpeg = get_ffmpeg_path()
        if not ffmpeg or not os.path.exists(ffmpeg):
            self.skipTest("FFmpeg is not available")

        with tempfile.TemporaryDirectory() as directory:
            source = str(Path(directory) / "source.mp4")
            output = str(Path(directory) / "output.mp4")
            generated = subprocess.run([
                ffmpeg, "-y", "-f", "lavfi", "-i",
                "color=c=blue:s=160x90:r=12:d=0.6",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", source,
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            self.assertEqual(0, generated.returncode)

            timeline = EditTimeline()
            timeline.add_video_clip(VideoClip(
                source_path=source, source_duration=0.6,
                trim_start=0.0, trim_end=0.6, timeline_start=0.0,
            ))
            worker = FFmpegDirectExportWorker(
                timeline, output, (160, 90), fps=12.0)
            results = []
            worker.finished.connect(lambda ok, value: results.append((ok, value)))

            worker._do_export()

            self.assertTrue(results, "export worker did not emit a result")
            self.assertTrue(results[-1][0], results[-1][1])
            self._assert_export_has_visible_blue_frame(ffmpeg, output)

    def test_compositor_falls_back_to_ffmpeg_when_opencv_decode_fails(self):
        ffmpeg = get_ffmpeg_path()
        if not ffmpeg or not os.path.exists(ffmpeg):
            self.skipTest("FFmpeg is not available")

        with tempfile.TemporaryDirectory() as directory:
            source = str(Path(directory) / "source.mp4")
            output = str(Path(directory) / "output.mp4")
            generated = subprocess.run([
                ffmpeg, "-y", "-f", "lavfi", "-i",
                "color=c=blue:s=160x90:r=12:d=0.6",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", source,
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            self.assertEqual(0, generated.returncode)

            timeline = EditTimeline()
            timeline.add_video_clip(VideoClip(
                source_path=source, source_duration=0.6,
                trim_start=0.0, trim_end=0.6, timeline_start=0.0,
            ))
            # 复杂字幕强制进入 compositor；模拟连续导出多条 HEVC 后 OpenCV
            # 无法再分配解码器，画面应由 FFmpeg 管道兜底而不是写入黑帧。
            timeline.add_subtitle(SubtitleBlock(
                text="描边字幕", timeline_start=0.0, timeline_end=0.6,
                outline_width=2,
            ))
            worker = FFmpegDirectExportWorker(
                timeline, output, (160, 90), fps=12.0)
            results = []
            worker.finished.connect(lambda ok, value: results.append((ok, value)))

            with patch("core.clip_decoder.DecoderManager.get", return_value=None):
                worker._do_export()

            self.assertTrue(results, "export worker did not emit a result")
            self.assertTrue(results[-1][0], results[-1][1])
            self._assert_export_has_visible_blue_frame(ffmpeg, output)

    def test_video_export_rejects_timeline_without_visible_video(self):
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / "should_not_exist.mp4")
            timeline = EditTimeline()
            timeline.add_subtitle(SubtitleBlock(
                text="only subtitle", timeline_start=0.0, timeline_end=1.0,
            ))
            worker = FFmpegDirectExportWorker(
                timeline, output, (160, 90), fps=12.0)
            results = []
            worker.finished.connect(lambda ok, value: results.append((ok, value)))

            worker._do_export()

            self.assertTrue(results)
            self.assertFalse(results[-1][0])
            self.assertIn("没有可导出的视频画面", results[-1][1])
            self.assertFalse(os.path.exists(output))

    def test_audio_mix_keeps_requested_gain_and_repeated_source_clips(self):
        timeline = EditTimeline()
        first = AudioClip(
            source_path="same.wav", source_duration=1.0,
            trim_start=0.0, trim_end=1.0, timeline_start=0.0,
            volume=1.8,
        )
        second = AudioClip(
            source_path="same.wav", source_duration=1.0,
            trim_start=0.0, trim_end=1.0, timeline_start=1.0,
            volume=1.8,
        )
        worker = FFmpegDirectExportWorker(
            timeline, "unused.mp4", (160, 90), fps=12.0)
        parts, label = worker._build_audio_graph([
            {"input_idx": 0, "clip": first, "from_video": False,
             "playback_dur": 1.0},
            {"input_idx": 0, "clip": second, "from_video": False,
             "playback_dur": 1.0},
        ], 2.0)
        graph = ";".join(parts)

        self.assertEqual("aout", label)
        self.assertIn("[0:a]asplit=2[asrc0_0][asrc0_1]", graph)
        self.assertEqual(2, graph.count("volume=1.8000"))
        self.assertIn("normalize=0", graph)
        self.assertIn("dropout_transition=0", graph)

    def test_exported_sequential_audio_clips_keep_even_loudness(self):
        ffmpeg = get_ffmpeg_path()
        if not ffmpeg or not os.path.exists(ffmpeg):
            self.skipTest("FFmpeg is not available")

        with tempfile.TemporaryDirectory() as directory:
            source_video = str(Path(directory) / "video.mp4")
            source_audio = str(Path(directory) / "tone.wav")
            output = str(Path(directory) / "output.mp4")
            video_result = subprocess.run([
                ffmpeg, "-y", "-f", "lavfi", "-i",
                "color=c=blue:s=160x90:r=12:d=1.2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", source_video,
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            audio_result = subprocess.run([
                ffmpeg, "-y", "-f", "lavfi", "-i",
                "sine=frequency=660:sample_rate=44100:duration=0.6",
                "-c:a", "pcm_s16le", source_audio,
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            self.assertEqual(0, video_result.returncode)
            self.assertEqual(0, audio_result.returncode)

            timeline = EditTimeline()
            timeline.add_video_clip(VideoClip(
                source_path=source_video, source_duration=1.2,
                trim_start=0.0, trim_end=1.2, timeline_start=0.0,
            ))
            for start in (0.0, 0.6):
                timeline.add_audio_clip(AudioClip(
                    source_path=source_audio, source_duration=0.6,
                    trim_start=0.0, trim_end=0.6, timeline_start=start,
                    volume=1.5,
                ))
            worker = FFmpegDirectExportWorker(
                timeline, output, (160, 90), fps=12.0)
            results = []
            worker.finished.connect(lambda ok, value: results.append((ok, value)))

            worker._do_export()

            self.assertTrue(results)
            self.assertTrue(results[-1][0], results[-1][1])
            decoded = subprocess.run([
                ffmpeg, "-v", "error", "-i", output,
                "-map", "0:a:0", "-ac", "1", "-ar", "44100",
                "-f", "s16le", "-",
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            self.assertEqual(0, decoded.returncode)
            samples = array.array("h")
            samples.frombytes(decoded.stdout)

            def mean_abs(start_sec, end_sec):
                start = int(start_sec * 44100)
                end = min(len(samples), int(end_sec * 44100))
                return sum(abs(value) for value in samples[start:end]) / max(1, end - start)

            first_level = mean_abs(0.15, 0.45)
            second_level = mean_abs(0.75, 1.05)
            self.assertGreater(first_level, 500)
            self.assertGreater(second_level, 500)
            ratio = max(first_level, second_level) / min(first_level, second_level)
            self.assertLess(ratio, 1.15,
                            f"audio level changes near the end: {first_level:.1f} vs {second_level:.1f}")


if __name__ == "__main__":
    unittest.main()
