import os
import tempfile
import unittest
from unittest.mock import patch

try:
    from PyQt6.QtCore import QSettings, Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication
    from ui.export_dialogs import AudioExportDialog, ExportDialog
    QT_AVAILABLE = True
except ModuleNotFoundError:
    QT_AVAILABLE = False


class _TestVideoExportDialog(ExportDialog):
    settings_path = ""

    def _settings(self):
        return QSettings(self.settings_path, QSettings.Format.IniFormat)


class _TestAudioExportDialog(AudioExportDialog):
    settings_path = ""

    def _settings(self):
        return QSettings(self.settings_path, QSettings.Format.IniFormat)


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 runtime is not available")
class ExportDialogMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_video_export_remembers_directory_and_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            _TestVideoExportDialog.settings_path = os.path.join(directory, "video.ini")
            output = os.path.join(directory, "我的成片.mp4")

            first = _TestVideoExportDialog(default_name="工程名称")
            first.name_edit.setText("我的成片.mp4")
            first.directory_edit.setText(directory)
            with patch("ui.export_dialogs.QFileDialog.getExistingDirectory") as chooser:
                self.assertEqual(output, first.get_settings()["path"])
                chooser.assert_not_called()
            first.close()

            reopened = _TestVideoExportDialog(default_name="另一个工程")
            self.assertEqual("我的成片.mp4", reopened.name_edit.text())
            self.assertEqual(directory, reopened.directory_edit.text())
            reopened.close()

    def test_audio_export_is_editable_and_remembers_name(self):
        with tempfile.TemporaryDirectory() as directory:
            _TestAudioExportDialog.settings_path = os.path.join(directory, "audio.ini")
            output = os.path.join(directory, "最终混音.mp3")

            first = _TestAudioExportDialog(default_name="工程名称")
            first.name_edit.setText("最终混音.mp3")
            first.directory_edit.setText(directory)
            with patch("ui.export_dialogs.QFileDialog.getExistingDirectory") as chooser:
                self.assertEqual(output, first.get_settings()["path"])
                chooser.assert_not_called()
            first.close()

            reopened = _TestAudioExportDialog(default_name="另一个工程")
            self.assertEqual("最终混音.mp3", reopened.name_edit.text())
            self.assertEqual(directory, reopened.directory_edit.text())
            reopened._fmt_combo.setCurrentText("WAV")
            self.assertEqual("最终混音.wav", reopened.name_edit.text())
            reopened.close()

    def test_directory_picker_changes_only_the_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            _TestVideoExportDialog.settings_path = os.path.join(directory, "picker.ini")
            chosen = os.path.join(directory, "chosen")
            os.mkdir(chosen)
            dialog = _TestVideoExportDialog(default_name="保留名称")
            original_name = dialog.name_edit.text()
            with patch("ui.export_dialogs.QFileDialog.getExistingDirectory",
                       return_value=chosen) as chooser:
                dialog._browse()
            chooser.assert_called_once()
            self.assertEqual(original_name, dialog.name_edit.text())
            self.assertEqual(chosen, dialog.directory_edit.text())
            dialog.close()

    def test_enter_exports_instead_of_opening_directory_picker(self):
        with tempfile.TemporaryDirectory() as directory:
            _TestVideoExportDialog.settings_path = os.path.join(directory, "enter.ini")
            dialog = _TestVideoExportDialog(default_name="直接导出")
            dialog.show()
            dialog._btn_browse.setFocus()
            with patch("ui.export_dialogs.QFileDialog.getExistingDirectory") as chooser:
                QTest.keyClick(dialog._btn_browse, Qt.Key.Key_Return)
            self.assertEqual(dialog.DialogCode.Accepted, dialog.result())
            chooser.assert_not_called()
            dialog.close()


if __name__ == "__main__":
    unittest.main()
