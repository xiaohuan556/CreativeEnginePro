import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QApplication

from ui.replace_video_dialog import _fit_thumbnail


class ReplaceThumbnailAspectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_portrait_thumbnail_is_not_stretched_to_wide_cell(self):
        source = QPixmap.fromImage(QImage(90, 160, QImage.Format.Format_RGB32))

        fitted = _fit_thumbnail(source, 140, 54)

        self.assertEqual(54, fitted.height())
        self.assertLess(fitted.width(), fitted.height())
        self.assertAlmostEqual(90 / 160, fitted.width() / fitted.height(), delta=0.02)

    def test_landscape_thumbnail_also_keeps_its_ratio(self):
        source = QPixmap.fromImage(QImage(160, 90, QImage.Format.Format_RGB32))

        fitted = _fit_thumbnail(source, 140, 54)

        self.assertEqual(54, fitted.height())
        self.assertAlmostEqual(160 / 90, fitted.width() / fitted.height(), delta=0.03)


if __name__ == "__main__":
    unittest.main()
