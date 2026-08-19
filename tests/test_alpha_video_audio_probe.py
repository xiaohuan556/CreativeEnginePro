import subprocess
import unittest
from unittest.mock import patch

from utils import alpha_video


class ProbeHasAudioTests(unittest.TestCase):
    def setUp(self):
        alpha_video._audio_cache.clear()

    @patch("utils.alpha_video.subprocess.run")
    def test_falls_back_to_ffmpeg_when_ffprobe_is_missing(self, run):
        run.side_effect = [
            FileNotFoundError("ffprobe missing"),
            subprocess.CompletedProcess(
                args=[], returncode=1, stdout=b"",
                stderr=b"Stream #0:1: Audio: aac, 44100 Hz, stereo",
            ),
        ]

        self.assertTrue(alpha_video.probe_has_audio("with-audio.mp4"))
        self.assertEqual(2, run.call_count)
        self.assertIn("-i", run.call_args_list[1].args[0])

    @patch("utils.alpha_video.subprocess.run")
    def test_ffmpeg_fallback_rejects_video_only_input(self, run):
        run.side_effect = [
            FileNotFoundError("ffprobe missing"),
            subprocess.CompletedProcess(
                args=[], returncode=1, stdout=b"",
                stderr=b"Stream #0:0: Video: h264, yuv420p, 1920x1080",
            ),
        ]

        self.assertFalse(alpha_video.probe_has_audio("video-only.mp4"))


if __name__ == "__main__":
    unittest.main()
