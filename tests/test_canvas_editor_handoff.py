import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace

try:
    from PyQt6.QtWidgets import QApplication
    from core.edit_engine import EditTimeline
    from ui.editor_tab import EditorTab
except ImportError:
    EditorTab = None


@unittest.skipIf(EditorTab is None, "editor dependencies unavailable")
class CanvasEditorHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _fake_editor():
        return SimpleNamespace(
            timeline=EditTimeline(),
            media_lib=SimpleNamespace(add_file=lambda _path: None),
            _detect_video_alpha=lambda _path: False,
            _thumb_params=lambda _duration: (3, 36),
            _start_thumbnail_worker=lambda *_args: None,
            _mark_dirty=lambda: None,
            status_msg=SimpleNamespace(emit=lambda *_args: None),
        )

    def test_tts_uses_dedicated_track_and_replaces_or_ducks_video_audio(self):
        folder = Path(tempfile.mkdtemp())
        video = folder / "veo.mp4"
        audio = folder / "tts.wav"
        video.write_bytes(b"placeholder")
        with wave.open(str(audio), "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(16000)
            target.writeframes(b"\0\0" * 16000)
        board = {"shots":[{
            "id":"s1", "number":1, "start":0, "duration":1,
            "selected_video_asset":str(video), "dialogue_audio":str(audio),
            "video_segment_offset":0.25,
            "assets":[{"path":str(video), "kind":"video", "actual_duration":1.25}],
        }]}

        replace = self._fake_editor()
        imported = EditorTab.import_storyboard(replace, board, "replace")
        self.assertEqual(1, imported)
        self.assertTrue(replace.timeline.video_tracks[0][0].mute)
        self.assertAlmostEqual(0.25, replace.timeline.video_tracks[0][0].trim_start)
        self.assertEqual("AI 对白", replace.timeline.audio_track_info[0].name)
        self.assertEqual(str(audio), replace.timeline.audio_tracks[0][0].source_path)

        duck = self._fake_editor()
        imported = EditorTab.import_storyboard(duck, board, "duck")
        self.assertEqual(1, imported)
        self.assertFalse(duck.timeline.video_tracks[0][0].mute)
        self.assertAlmostEqual(0.12, duck.timeline.video_tracks[0][0].volume)

    def test_string_transitions_are_normalized_during_canvas_handoff(self):
        folder = Path(tempfile.mkdtemp())
        videos = []
        for index in range(2):
            path = folder / f"shot_{index}.mp4"
            path.write_bytes(b"placeholder")
            videos.append(path)
        board = {"shots": [
            {"id": "s1", "start": 0, "duration": 1,
             "selected_video_asset": str(videos[0]), "transition": "fade",
             "assets": [{"path": str(videos[0]), "kind": "video", "actual_duration": 1}]},
            {"id": "s2", "start": 1, "duration": 1,
             "selected_video_asset": str(videos[1]), "transition": "cut",
             "assets": [{"path": str(videos[1]), "kind": "video", "actual_duration": 1}]},
        ]}

        editor = self._fake_editor()
        self.assertEqual(2, EditorTab.import_storyboard(editor, board))
        clips = editor.timeline.video_tracks[0]
        self.assertEqual({"type": "fade", "duration": 0.5}, clips[0].out_transition)
        self.assertIsNone(clips[1].out_transition)


if __name__ == "__main__":
    unittest.main()
