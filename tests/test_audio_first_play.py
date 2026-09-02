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


class _FakeQtPlayer:
    """Qt6-shaped player: no setDuration/isAvailable and play() has no args."""

    class PlaybackState:
        PlayingState = 1
        StoppedState = 0

    def __init__(self):
        self.play_count = 0
        self._state = self.PlaybackState.StoppedState
        self._position = 0

    def playbackState(self):
        return self._state

    def setPosition(self, value):
        self._position = value

    def position(self):
        return self._position

    def setSource(self, url):
        self.source = url.toLocalFile()

    def setPlaybackRate(self, _value):
        pass

    def play(self):
        self.play_count += 1
        self._state = self.PlaybackState.PlayingState

    def stop(self):
        self._state = self.PlaybackState.StoppedState


class _FakeAudioOutput:
    def __init__(self):
        self.volume = None

    def setVolume(self, value):
        self.volume = value


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

    def test_qt6_backend_starts_without_subprocess_only_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "clip.wav")
            with open(source, "wb") as handle:
                handle.write(b"placeholder")

            timeline = EditTimeline()
            preview = PreviewPlayer(timeline)
            fake = _FakeQtPlayer()
            output = _FakeAudioOutput()
            preview._ensure_audio_player = lambda _slot: (fake, output)
            preview._ensure_audio_for_video = lambda path: path

            self.assertTrue(preview.play_audio(
                source, 1.25, volume=0.6, duration_sec=2.0))
            self.assertEqual(1, fake.play_count)
            self.assertEqual(1250, fake.position())
            self.assertEqual(0.6, output.volume)

            preview._audio_players = [(fake, output)]
            self.assertAlmostEqual(1.25, preview.master_clock_sec())
            preview.close()


if __name__ == "__main__":
    unittest.main()
