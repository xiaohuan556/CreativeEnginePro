import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    from ai.assets import Scene
    from ai.ui.resource_center import AssetStudioDialog
    QT_AVAILABLE = True
except ModuleNotFoundError:
    QT_AVAILABLE = False


class _Registry:
    def by_capability(self, _operation):
        return []

    def by_domain(self, _domain):
        return []


class _Manager:
    registry = _Registry()


class _DB:
    def __init__(self):
        self.saved = []

    def save_scene(self, item):
        self.saved.append(item)


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 runtime is not available")
class AssetStudioNonModalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_asset_studio_does_not_block_main_workbench(self):
        item = Scene(id="scene_test", name="测试场景")
        with patch("ai.ui.resource_center.get_ai_manager", return_value=_Manager()):
            dialog = AssetStudioDialog(item, "scene", object())
        self.assertFalse(dialog.isModal())
        self.assertEqual(dialog.windowModality().name, "NonModal")
        self.assertTrue(dialog.windowFlags() & Qt.WindowType.WindowMinimizeButtonHint)
        dialog.deleteLater()
        self.app.processEvents()

    def test_upload_reference_switches_to_image_edit(self):
        item = Scene(id="scene_reference", name="参考场景")
        db = _DB()
        with TemporaryDirectory() as temp_dir:
            reference = Path(temp_dir) / "reference.png"
            reference.write_bytes(b"not-a-real-png")
            with patch("ai.ui.resource_center.get_ai_manager", return_value=_Manager()):
                dialog = AssetStudioDialog(item, "scene", db)
            with patch(
                    "ai.ui.resource_center.QFileDialog.getOpenFileNames",
                    return_value=([str(reference)], "")):
                dialog._upload_references()

            self.assertEqual(dialog.mode_combo.currentData(), "image_edit")
            self.assertEqual(dialog._working_reference, str(reference))
            self.assertIn("参考生图", dialog.generate_btn.text())
            self.assertEqual(db.saved, [item])
            dialog.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
