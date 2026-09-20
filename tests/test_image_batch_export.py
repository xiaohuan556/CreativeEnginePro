import os
import tempfile
import unittest

import cv2
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from ui.image_handler import ImageExportThread, build_image_export_path
    QT_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 runtime is not available")
class ImageBatchExportTests(unittest.TestCase):
    def test_fixed_batch_name_gets_an_index_instead_of_overwriting(self):
        with tempfile.TemporaryDirectory() as folder:
            source_paths = []
            for index in range(3):
                path = os.path.join(folder, f"source_{index}.png")
                image = np.full((8, 8, 3), 30 + index, dtype=np.uint8)
                cv2.imencode('.png', image)[1].tofile(path)
                source_paths.append(path)

            output_dir = os.path.join(folder, "output")
            os.makedirs(output_dir)
            tasks = [
                {'path': path, 'row_index': index}
                for index, path in enumerate(source_paths)
            ]
            config = {
                'out_dir': output_dir,
                'target_w': 8,
                'target_h': 8,
                'mode': '强制拉伸',
                'rename': '处理结果',
                'format': 'PNG',
                'quality': 92,
            }

            ImageExportThread(tasks, config).run()

            self.assertEqual(
                ['处理结果_01.png', '处理结果_02.png', '处理结果_03.png'],
                sorted(os.listdir(output_dir)),
            )

    def test_same_original_names_from_different_folders_stay_unique(self):
        with tempfile.TemporaryDirectory() as folder:
            used_paths = set()
            first = build_image_export_path(
                folder, os.path.join('a', 'photo.png'), '', 0, 2, used_paths)
            second = build_image_export_path(
                folder, os.path.join('b', 'photo.png'), '', 1, 2, used_paths)

            self.assertEqual('photo.png', os.path.basename(first))
            self.assertEqual('photo_02.png', os.path.basename(second))


if __name__ == "__main__":
    unittest.main()
