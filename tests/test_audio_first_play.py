import os
import tempfile
import unittest

try:
    from PyQt6.QtCore import QUrl
    from PyQt6.QtWidgets import QApplication
    from core.edit_engine import AudioClip, EditTimeline
    from ui.preview_player import PreviewPlayer
    QT_AVAILABLE = True
except ModuleNotFoundError:
    QT_AVAILABLE = False


class _FakePlayer:
    class PlaybackState:
        PlayingState = 1
        StoppedState = 0

    def __init__(self):
        self.play_count = 0
        self._state = self.PlaybackState.StoppedState

    def setVolume(self, _value):
        pass

    def playbackState(self):
        return self._state

    def setDuration(self, _value):
        pass

    def setPosition(self, _value):
        pass

    def setSource(self, url):
        self.source = url.toLocalFile()

    def setPlaybackRate(self, _value):
        pass

    def play(self, *_args):
        self.play_count += 1
        self._state = self.PlaybackState.PlayingState

    def isAvailable(self):
        return True

    def stop(self):
        self._state = self.PlaybackState.StoppedState


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 runtime is not available")
class AudioFirstPlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_pending_transcode_starts_without_second_user_play(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "newly-dropped.mp3")
            with open(source, "wb") as handle:
                handle.write(b"placeholder")

            timeline = EditTimeline()
            timeline.add_audio_clip(AudioClip(
                source_path=source,
                source_duration=5.0,
                trim_start=0.0,
                trim_end=5.0,
                timeline_start=0.0,
            ))
            preview = PreviewPlayer(timeline)
            preview._playing = True
            preview._current_sec = 0.0

            fake = _FakePlayer()
            preview._ensure_audio_player = lambda _slot: (fake, None)
            ready = {"value": False}
            preview._ensure_audio_for_video = (
                lambda path: path if ready["value"] else "")

            preview.play_all_audio(0.0)
            self.assertTrue(preview._audio_pending)
            self.assertEqual(0, fake.play_count)

            ready["value"] = True
            preview.audio_extract_ready.emit(source)

            self.assertFalse(preview._audio_pending)
            self.assertEqual(1, fake.play_count)
            preview._playing = False
            preview.close()


if __name__ == "__main__":
    unittest.main()
