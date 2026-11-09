import os
import tempfile
import unittest
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
from PIL import Image
from PyQt6.QtWidgets import QApplication
from core.image_import import read_asset
from ui.image_editor import ImageEditorWidget, numpy_from_qimage


class ImportFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.editor = ImageEditorWidget()
        self.editor.new_project(24, 16)

    def tearDown(self):
        self.editor.close()
        self.editor.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_svg_keeps_vector_source_and_renders_after_save_restore(self):
        path = self.root / 'shape.svg'
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="16"><rect width="24" height="16" fill="red"/></svg>')
        self.editor.insert_imported_asset(read_asset(path), path)
        self.assertEqual(self.editor.active.kind, 'svg')
        self.assertTrue(self.editor.active.svg_source)
        image = numpy_from_qimage(self.editor._render_composite(True))
        np.testing.assert_array_equal(image[8, 12], [255, 0, 0, 255])
        self.editor._restore(self.editor.unpack_project(self.editor.pack_project(self.editor._snapshot())))
        np.testing.assert_array_equal(image, numpy_from_qimage(self.editor._render_composite(True)))

    def test_gif_frames_duplicate_delete_timing_save_and_export(self):
        path = self.root / 'test.gif'
        a, b = Image.new('RGBA', (24, 16), 'red'), Image.new('RGBA', (24, 16), 'blue')
        a.save(path, save_all=True, append_images=[b], duration=[80, 150], loop=0)
        self.editor.insert_imported_asset(read_asset(path), path)
        self.assertEqual(self.editor.active.frame_durations, [80, 150])
        self.editor.select_gif_frame(1)
        np.testing.assert_array_equal(self.editor.active.pixels[0, 0], [0, 0, 255, 255])
        self.editor.duplicate_gif_frame()
        self.assertEqual(len(self.editor.active.frames), 3)
        self.editor.delete_gif_frame()
        self.assertEqual(len(self.editor.active.frames), 2)
        self.editor._restore(self.editor.unpack_project(self.editor.pack_project(self.editor._snapshot())))
        self.assertEqual(len(self.editor.active.frames), 2)
        target = self.root / 'export.gif'
        self.editor.export_gif(target)
        with Image.open(target) as image:
            self.assertEqual(image.n_frames, 2)
            self.assertEqual(image.info['duration'], 80)

    def test_pdf_pages_import_as_separate_layers(self):
        from PyQt6.QtGui import QPdfWriter, QPainter, QColor
        path = self.root / 'pages.pdf'
        writer = QPdfWriter(str(path))
        painter = QPainter(writer)
        painter.fillRect(0, 0, 100, 100, QColor('red'))
        writer.newPage()
        painter.fillRect(0, 0, 100, 100, QColor('blue'))
        painter.end()
        del writer
        asset = read_asset(path, pdf_dpi=72)
        self.assertEqual(len(asset.layers), 2)
        self.assertIn('第 1 页', asset.layers[-1]['name'])

    def test_heic_decoder_roundtrip(self):
        from pillow_heif import register_heif_opener
        register_heif_opener()
        path = self.root / 'image.heic'
        Image.new('RGB', (24, 16), (100, 160, 200)).save(path, format='HEIF')
        asset = read_asset(path)
        self.assertEqual((asset.width, asset.height), (24, 16))

    def test_psd_nested_group_is_preserved(self):
        from psd_tools import PSDImage
        from psd_tools.api.layers import Group, PixelLayer
        document = PSDImage.new('RGB', (24, 16))
        group = Group.new(document, 'Outer')
        nested = Group.new(group, 'Inner')
        PixelLayer.frompil(Image.new('RGBA', (8, 6), 'red'), nested, 'Red', top=4, left=5)
        path = self.root / 'group.psd'
        document.save(path)
        asset = read_asset(path)
        self.assertEqual(asset.layers[0]['kind'], 'group')
        self.assertEqual(asset.layers[0]['children'][0]['kind'], 'group')
        self.editor.insert_imported_asset(asset, path)
        self.assertFalse(self.editor.project.layers[0].collapsed)
        self.assertFalse(self.editor.project.layers[0].children[0].collapsed)
        self.assertEqual(self.editor._all_layers()[-1].name, 'Red')
        image = numpy_from_qimage(self.editor._render_composite(True))
        np.testing.assert_array_equal(image[6, 7], [255, 0, 0, 255])

    def test_flattened_psd_imports_merged_image_as_one_layer(self):
        from psd_tools import PSDImage

        path = self.root / 'flattened.psd'
        merged = Image.new('RGB', (24, 16), (33, 82, 145))
        PSDImage.frompil(merged).save(path)
        self.assertEqual(len(PSDImage.open(path)), 0)

        self.assertTrue(self.editor.add_image_from_path(path))
        self.assertEqual(len(self.editor.project.layers), 1)
        self.assertEqual(self.editor.active.name, 'flattened')
        self.assertIn('已导入为一层', self.editor.status_tool.text())
        np.testing.assert_array_equal(
            self.editor.active.pixels, np.array(merged.convert('RGBA')))

    def test_psd_artboards_keep_size_relative_position_and_local_layers(self):
        from psd_tools import PSDImage
        from psd_tools.api.layers import Group, PixelLayer
        from psd_tools.constants import Tag
        from psd_tools.psd.descriptor import Descriptor, Integer

        document = PSDImage.new('RGB', (80, 60))
        for name, bounds, color in [
            ('Board A', (6, 4, 26, 14), 'red'),
            ('Board B', (40, 10, 53, 30), 'blue'),
        ]:
            group = Group.new(document, name)
            group.tagged_blocks.set_data(Tag.ARTBOARD_DATA1)
            metadata = group.tagged_blocks.get_data(Tag.ARTBOARD_DATA1)
            rect = Descriptor()
            for key, value in zip((b'Left', b'Top ', b'Rght', b'Btom'), bounds):
                rect[key] = Integer(value)
            metadata[b'artboardRect'] = rect
            metadata[b'artboardBackgroundType'] = Integer(2)
            PixelLayer.frompil(Image.new('RGBA', (3, 3), color), group, color,
                               top=bounds[1] + 2, left=bounds[0] + 1)
        path = self.root / 'artboards.psd'
        document.save(path)

        self.assertTrue(self.editor.add_image_from_path(path))
        first, second = self.editor.project.artboards
        self.assertEqual((first.name, first.x, first.y, first.w, first.h), ('Board A', 0, 0, 20, 10))
        self.assertEqual((second.name, second.x, second.y, second.w, second.h), ('Board B', 34, 6, 13, 20))
        self.assertIn('2 个画板', self.editor.status_tool.text())
        for board, rgba in [(first, [255, 0, 0, 255]), (second, [0, 0, 255, 255])]:
            self.assertEqual((board.layers[0].x, board.layers[0].y), (2.5, 3.5))
            rendered = numpy_from_qimage(self.editor._render_artboard(board))
            np.testing.assert_array_equal(rendered[3, 2], rgba)
        self.assertGreaterEqual(self.editor._doc_bounds()[0], 47)
        self.assertFalse(self.editor.view.artboard_item.pixmap().isNull())
        self.editor._recovery_saved_token = self.editor._recovery_token()
        self.editor.new_project(24, 16)  # The last tab closes by reusing its view.
        self.assertTrue(self.editor.view.artboard_item.pixmap().isNull())


if __name__ == '__main__':
    unittest.main()
