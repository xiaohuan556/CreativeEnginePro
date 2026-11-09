import os
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

try:
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtTest import QSignalSpy
    from ui.media_library import MediaItem, MediaLibrary, _MediaCard
    QT_AVAILABLE = True
except ModuleNotFoundError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 runtime is not available")
class MediaLibraryRevealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_reveal_selects_the_media_file_on_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            media_path = os.path.join(directory, "素材 文件.mp4")
            with open(media_path, "wb") as handle:
                handle.write(b"placeholder")

            library = MediaLibrary()
            with patch("ui.media_library.sys.platform", "win32"), \
                    patch("ui.media_library.subprocess.Popen") as popen:
                self.assertTrue(library._reveal_media(media_path))
            popen.assert_called_once_with([
                "explorer.exe", "/select,", os.path.normpath(os.path.abspath(media_path))])
            library.close()

    def test_copy_directory_uses_absolute_folder_path(self):
        library = MediaLibrary()
        media_path = os.path.join("relative", "clip.mp4")
        expected = os.path.dirname(os.path.abspath(media_path))
        self.assertEqual(expected, library._copy_media_directory(media_path))
        self.assertEqual(expected, QApplication.clipboard().text())
        library.close()

    def test_track_badge_only_appears_when_media_is_used(self):
        with tempfile.TemporaryDirectory() as directory:
            media_path = os.path.join(directory, "clip.mp4")
            with open(media_path, "wb") as handle:
                handle.write(b"placeholder")
            with patch("ui.media_library._get_duration", return_value=0.0):
                card = _MediaCard(MediaItem(media_path), {})

            self.assertTrue(card._status_badge.isHidden())
            card.show()
            card.set_on_track(True)
            self.assertFalse(card._status_badge.isHidden())
            self.assertEqual("✓ 时间线中", card._status_badge.text())
            card.set_on_track(False)
            self.assertTrue(card._status_badge.isHidden())
            card.close()

    def test_library_rejects_non_media_files_from_programmatic_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            library = MediaLibrary()
            rejected = []
            for filename in ("metadata.json", "notes.txt", "project.cep"):
                path = os.path.join(directory, filename)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("placeholder")
                rejected.append(path)
                self.assertFalse(library.add_file(path))

            image_path = os.path.join(directory, "cover.png")
            with open(image_path, "wb") as handle:
                handle.write(b"placeholder")
            with patch("ui.media_library._get_duration", return_value=0.0), \
                    patch.object(library, "_start_thumb_worker"):
                self.assertTrue(library.add_file(image_path))

            self.assertEqual([image_path], library.get_paths())
            self.assertTrue(all(path not in library.get_paths() for path in rejected))
            library.close()

    def test_video_duration_probe_does_not_block_import(self):
        with tempfile.TemporaryDirectory() as directory:
            media_path = os.path.join(directory, "clip.mp4")
            with open(media_path, "wb"):
                pass
            library = MediaLibrary()
            allow_probe_to_finish = Event()

            def probe(_path, _media_type):
                self.assertTrue(allow_probe_to_finish.wait(2))
                return 4.0

            with patch("ui.media_library._get_duration", side_effect=probe), \
                    patch("ui.media_library._make_thumbnail"):
                try:
                    self.assertTrue(library.add_file(media_path))
                    self.assertEqual("", library._cards[0]._dur_badge.text())
                    worker = library._workers[0]
                    finished = QSignalSpy(worker.finished)
                    allow_probe_to_finish.set()
                    self.assertTrue(finished.wait(2000) or len(finished) == 1)
                    self.assertEqual("4.0s", library._cards[0]._dur_badge.text())
                finally:
                    allow_probe_to_finish.set()
                    for worker in list(library._workers):
                        worker.wait(2000)
                    library.close()


if __name__ == "__main__":
    unittest.main()
