import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication
    from ui.editor_tab import SubtitleManagerDialog
    QT_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    QT_AVAILABLE = False


class _FakeSignal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def disconnect(self, slot=None):
        if slot is None:
            self.slots.clear()
        elif slot in self.slots:
            self.slots.remove(slot)


class _FakeTranslationWorker:
    instances = []

    def __init__(self, entries, language):
        self.entries = entries
        self.language = language
        self.finished = _FakeSignal()
        self.error = _FakeSignal()
        self.started = False
        self.deleted = False
        self.running = False
        self.interrupted = False
        self.terminated = False
        self.__class__.instances.append(self)

    def start(self):
        self.started = True
        self.running = True

    def isRunning(self):
        return self.running

    def requestInterruption(self):
        self.interrupted = True

    def quit(self):
        self.running = False

    def wait(self, _timeout):
        return not self.running

    def terminate(self):
        self.terminated = True
        self.running = False

    def deleteLater(self):
        self.deleted = True


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 runtime is not available")
class SubtitleTranslationDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        _FakeTranslationWorker.instances.clear()
        self.dialog = SubtitleManagerDialog([
            {"start": 0.0, "end": 1.5, "text": "测试字幕"},
        ], [])

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        self.app.processEvents()

    def test_first_translation_starts_without_missing_cleanup_method(self):
        with patch("ui.editor_tab._SubTransWorker", _FakeTranslationWorker):
            self.dialog._request_translate("en")

        worker = _FakeTranslationWorker.instances[-1]
        self.assertTrue(worker.started)
        self.assertEqual("en", worker.language)
        self.assertIs(self.dialog._ai_worker, worker)

    def test_finished_worker_is_released_before_next_translation(self):
        old_worker = _FakeTranslationWorker([], "en")
        self.dialog._ai_worker = old_worker

        with patch("ui.editor_tab._SubTransWorker", _FakeTranslationWorker):
            self.dialog._request_translate("ja")

        self.assertTrue(old_worker.deleted)
        self.assertEqual("ja", self.dialog._ai_worker.language)
        self.assertTrue(self.dialog._ai_worker.started)

    def test_accepting_dialog_stops_running_translation(self):
        worker = _FakeTranslationWorker([], "en")
        worker.running = True
        self.dialog._ai_worker = worker

        self.dialog.accept()

        self.assertTrue(worker.interrupted)
        self.assertTrue(worker.deleted)
        self.assertIsNone(self.dialog._ai_worker)


if __name__ == "__main__":
    unittest.main()
