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

    def test_translation_result_is_visible_and_translation_tab_is_selected(self):
        from core.transcriber import SRTEntry

        self.dialog._on_trans_done([
            SRTEntry(1, 0.0, 1.5, "Translated line")])

        self.assertFalse(self.dialog._btn_orig.isChecked())
        self.assertTrue(self.dialog._btn_trans.isChecked())
        self.assertEqual("Translated line", self.dialog._table.item(0, 3).text())
        self.assertEqual("Translated line", self.dialog._selected_subs()[0]["text"])

    def test_blank_translation_is_reported_instead_of_shown_as_success(self):
        from core.builtin_translator import translate_srt_entries
        from core.transcriber import SRTEntry

        entry = SRTEntry(1, 0.0, 1.5, "测试字幕")
        with (patch("core.builtin_translator.GoogleTranslator") as translator,
              patch("core.builtin_translator.MyMemoryTranslator") as fallback):
            translator.return_value.translate.return_value = None
            fallback.return_value.translate.return_value = None
            with self.assertRaisesRegex(RuntimeError, "空文本"):
                translate_srt_entries([entry], "en", strict=True)
            self.assertEqual("测试字幕",
                             translate_srt_entries([entry], "en")[0].text)

    def test_sentence_batch_preserves_timing_and_uses_one_request(self):
        from core.builtin_translator import translate_srt_entries
        from core.transcriber import SRTEntry

        entries = [SRTEntry(1, 0.0, 1.5, "你好"),
                   SRTEntry(2, 2.0, 3.0, "谢谢")]
        with patch("core.builtin_translator.GoogleTranslator") as translator:
            translator.return_value.translate.return_value = (
                "[[SRT000000]] Hello\n[[SRT000001]] Thank you")
            result = translate_srt_entries(entries, "en", strict=True)

        self.assertEqual(["Hello", "Thank you"], [item.text for item in result])
        self.assertEqual([(0.0, 1.5), (2.0, 3.0)],
                         [(item.start, item.end) for item in result])
        translator.return_value.translate.assert_called_once()

    def test_rate_limited_google_uses_batched_backup(self):
        from deep_translator.exceptions import TooManyRequests
        from core.builtin_translator import translate_srt_entries
        from core.transcriber import SRTEntry

        entries = [SRTEntry(1, 0.0, 1.5, "你好"),
                   SRTEntry(2, 2.0, 3.0, "谢谢")]
        with (patch("core.builtin_translator.GoogleTranslator") as translator,
              patch("core.builtin_translator.MyMemoryTranslator") as fallback):
            translator.return_value.translate.side_effect = TooManyRequests()
            fallback.return_value.translate.return_value = (
                "[[SRT000000]] Hello\n[[SRT000001]] Thank you")
            result = translate_srt_entries(entries, "en", strict=True)

        self.assertEqual(["Hello", "Thank you"], [item.text for item in result])
        fallback.return_value.translate.assert_called_once()

    def test_long_subtitle_splits_backup_requests(self):
        from deep_translator.exceptions import TooManyRequests
        from core.builtin_translator import translate_srt_entries
        from core.transcriber import SRTEntry

        entry = SRTEntry(1, 0.0, 4.0, "长" * 850)
        with (patch("core.builtin_translator.GoogleTranslator") as translator,
              patch("core.builtin_translator.MyMemoryTranslator") as fallback):
            translator.return_value.translate.side_effect = TooManyRequests()
            fallback.return_value.translate.side_effect = lambda text: "part"
            result = translate_srt_entries([entry], "en", strict=True)

        assert result[0].text == "part part part"
        assert fallback.return_value.translate.call_count == 3
        assert all(len(call.args[0]) <= 400
                   for call in fallback.return_value.translate.call_args_list)


if __name__ == "__main__":
    unittest.main()
