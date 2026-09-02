import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ui.editor_tab import EditorTab, SENSITIVE_SCENE_THRESHOLD


class SceneDetectQuickStartTests(unittest.TestCase):
    def test_click_starts_sensitive_mode_without_dialog(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as handle:
            video_path = handle.name
        try:
            owner = SimpleNamespace(
                _ai_busy=lambda: False,
                _stop_ai_worker=MagicMock(),
                _on_ai_progress=MagicMock(),
                _on_scene_detect_error=MagicMock(),
                _on_scene_detect_done=MagicMock(),
                status_msg=SimpleNamespace(emit=MagicMock()),
            )
            clip = SimpleNamespace(source_path=video_path, duration=12.0)
            worker = MagicMock()
            with patch("ui.editor_tab._SceneDetectWorker", return_value=worker) as factory:
                EditorTab._start_scene_detection(owner, clip)

            factory.assert_called_once_with(
                clip,
                threshold=SENSITIVE_SCENE_THRESHOLD,
                min_length=0.8,
                filter_flashes=True,
            )
            worker.start.assert_called_once_with()
            self.assertIs(owner._scene_detect_clip, clip)
        finally:
            os.unlink(video_path)


if __name__ == "__main__":
    unittest.main()
