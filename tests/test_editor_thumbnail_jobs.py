import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtGui import QColor, QImage, QPixmap
    from PyQt6.QtWidgets import QApplication
    from core.edit_engine import VideoClip
    from ui.editor_tab import EditorTab, ThumbnailWorker
    QT_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 runtime is not available")
class EditorThumbnailJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_worker_reads_image_without_creating_gui_pixmap(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "frame.png")
            image = QImage(8, 8, QImage.Format.Format_RGB32)
            image.fill(QColor("red"))
            self.assertTrue(image.save(path))
            clip = VideoClip(source_path=path, source_duration=2.0,
                             trim_start=0.0, trim_end=2.0)
            worker = ThumbnailWorker(clip, 3)
            emitted = []
            worker.finished.connect(lambda _clip, images: emitted.extend(images))
            worker.run()
            self.assertEqual(1, len(emitted))
            self.assertIsInstance(emitted[0], QImage)

    def test_old_trim_result_cannot_replace_current_thumbnail(self):
        clip = VideoClip(source_path="source.mp4", source_duration=4.0,
                         trim_start=0.0, trim_end=4.0)
        clip.trim_start = 1.0
        clip.thumbnail = QPixmap(2, 2)
        original = clip.thumbnail
        job = SimpleNamespace(_source_path="source.mp4",
                              _trim_start=0.0, _trim_end=4.0)
        refresh = Mock()
        fake_editor = SimpleNamespace(
            sender=lambda: job,
            _find_current_clip_by_id=lambda _id: clip,
            _thumb_workers=[],
            _process_thumb_pending=Mock(),
            timeline_widget=SimpleNamespace(refresh_canvas=refresh),
        )
        stale = QImage(4, 4, QImage.Format.Format_RGB32)
        EditorTab._on_thumbnails_ready(fake_editor, clip, [stale])
        self.assertIs(clip.thumbnail, original)
        refresh.assert_not_called()

        job._trim_start = 1.0
        EditorTab._on_thumbnails_ready(fake_editor, clip, [stale])
        self.assertIsInstance(clip.thumbnail, QPixmap)
        refresh.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
