import unittest
from unittest.mock import MagicMock, patch

from core.video_engine import VideoProcessor, build_alpha_watermark_command


class TailAlphaWatermarkTests(unittest.TestCase):
    def test_command_loops_alpha_mov_and_keeps_only_base_audio(self):
        command = build_alpha_watermark_command(
            "ffmpeg", "base.mp4", "watermark.mov", "output.mp4", 1080, 1920)

        self.assertEqual(
            ["-i", "base.mp4", "-stream_loop", "-1", "-i", "watermark.mov"],
            command[2:8],
        )
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("scale=1080:1920", graph)
        self.assertIn("format=rgba", graph)
        self.assertIn("overlay=0:0:shortest=1", graph)
        map_values = [command[index + 1] for index, value in enumerate(command[:-1])
                      if value == "-map"]
        self.assertEqual(["[v]", "0:a?"], map_values)
        self.assertNotIn("1:a", command)

    def test_non_alpha_mov_is_rejected_before_export(self):
        signal = MagicMock()
        processor = VideoProcessor(signal)
        config = {"watermark_path": "opaque.mov", "ratio_mode": "保持原样"}
        task = {"path": "input.mp4", "name": "input.mp4", "duration": 1.0}

        with patch("core.video_engine.os.path.isfile", return_value=True), \
                patch("utils.alpha_video.probe_has_alpha", return_value=False):
            result = processor.process_task(task, config)

        self.assertFalse(result)
        self.assertTrue(any("没有 Alpha" in call.args[0]
                            for call in signal.emit.call_args_list))


if __name__ == "__main__":
    unittest.main()
