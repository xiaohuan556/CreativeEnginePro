import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
from PyQt6.QtCore import QPointF
from PyQt6.QtWidgets import QApplication
from ui.image_editor import ImageEditorWidget, ImageLayer, Artboard, numpy_from_qimage
from ui.image_folders import walk_layers


class FolderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = ImageEditorWidget()
        self.editor.new_project(24, 16)
        self.layers = []
        for name, color in [('红', [255, 0, 0, 255]), ('蓝', [0, 0, 255, 255])]:
            pixels = np.zeros((8, 8, 4), np.uint8)
            pixels[:] = color
            layer = ImageLayer(name, pixels, 8, 8)
            layer.x, layer.y = 12, 8
            self.editor.project.add_layer(layer)
            self.layers.append(layer)
        self.editor.active = self.layers[-1]
        self.editor.selected = self.layers[:]

    def tearDown(self):
        self.editor.close()
        self.editor.deleteLater()
        self.app.processEvents()

    def group(self):
        self.editor._group_selected()
        return self.editor.active

    def test_folder_is_real_container_and_roundtrips(self):
        before = numpy_from_qimage(self.editor._render_composite(True))
        folder = self.group()
        self.assertEqual(self.editor.project.layers, [folder])
        self.assertEqual(folder.children, self.layers)
        np.testing.assert_array_equal(before, numpy_from_qimage(self.editor._render_composite(True)))
        snap = self.editor.unpack_project(self.editor.pack_project(self.editor._snapshot()))
        self.editor._restore(snap)
        self.assertEqual(len(self.editor.active.children), 2)
        self.assertEqual(len(self.editor._all_layers()), 3)

    def test_folder_opacity_applies_after_children_composite(self):
        folder = self.group()
        folder.opacity = .5
        image = numpy_from_qimage(self.editor._render_composite(True))
        self.assertEqual(image[8, 12, 3], 128)
        np.testing.assert_array_equal(image[8, 12, :3], [0, 0, 255])

    def test_nested_folder_mask_and_visibility(self):
        outer = self.group()
        self.editor.active = outer.children[0]
        self.editor.selected = outer.children[:]
        inner = self.group()
        self.assertIs(outer.children[0], inner)
        inner.mask = np.zeros((16, 24), np.uint8)
        inner.mask[:, :12] = 255
        image = numpy_from_qimage(self.editor._render_composite(True))
        self.assertEqual(image[8, 10, 3], 255)
        self.assertEqual(image[8, 14, 3], 0)
        outer.visible = False
        self.assertFalse(numpy_from_qimage(self.editor._render_composite(True))[:, :, 3].any())

    def test_group_ungroup_undo_redo(self):
        self.group()
        self.editor._ungroup_selected()
        self.assertEqual(self.editor.project.layers, self.layers)
        self.editor.undo()
        self.assertEqual(self.editor.project.layers[0].kind, 'group')
        self.editor.redo()
        self.assertEqual(len(self.editor.project.layers), 2)

    def test_folder_drag_moves_descendants_once(self):
        folder = self.group()
        self.editor.smart_guides_on = False
        self.editor.view._start = QPointF(0, 0)
        self.editor._begin_move(QPointF(0, 0))
        self.editor._move_to(QPointF(4, 3))
        self.editor._end_move()
        self.assertEqual((folder.children[0].x, folder.children[0].y), (16, 11))
        self.editor.undo()
        self.assertEqual((self.editor.active.children[0].x, self.editor.active.children[0].y), (12, 8))

    def test_folder_transform_cancel_restores_children(self):
        folder = self.group()
        self.editor._start_free_transform()
        states = [(node, node.x, node.y, node.scale, node.rotation, None) for node in walk_layers([folder])]
        self.editor.transform_nodes(states, QPointF(12, 8), QPointF(18, 10), 2, 90)
        self.editor._ft_cancel()
        child = self.editor.active.children[0]
        self.assertEqual((child.x, child.y, child.scale, child.rotation), (12, 8, 1, 0))

    def test_folder_clipboard_ids_are_unique_and_data_is_independent(self):
        folder = self.group()
        self.editor.copy_layers()
        self.assertTrue(self.editor.paste_layers())
        ids = [node.id for node in self.editor._all_layers()]
        self.assertEqual(len(ids), len(set(ids)))
        self.editor.active.children[0].pixels.fill(0)
        self.assertTrue(folder.children[0].pixels.any())

    def test_cannot_create_folder_cycle(self):
        outer = self.group()
        self.editor.active = outer.children[0]
        self.editor.selected = outer.children[:]
        inner = self.group()
        self.assertFalse(self.editor.move_layer_into(outer, inner))

    def test_nested_parent_and_artboard_resolution(self):
        folder = self.group()
        board = Artboard('画板', 0, 0, 24, 16)
        board.layers = [folder]
        self.editor.project.artboards = [board]
        self.editor.project.layers = []
        self.editor.active_artboard = board
        self.assertIs(self.editor._parent_list_of(folder.children[0]), folder.children)
        self.assertIs(self.editor._active_artboard_of(folder.children[0]), board)

    def test_duplicate_nested_folder_preserves_masks_and_renews_ids(self):
        folder = self.group()
        folder.children[0].mask = np.full((16, 24), 128, np.uint8)
        self.editor._duplicate_layer(folder)
        ids = [layer.id for layer in self.editor._all_layers()]
        self.assertEqual(len(set(ids)), len(ids))
        np.testing.assert_array_equal(self.editor.active.children[0].mask, folder.children[0].mask)


if __name__ == '__main__':
    unittest.main()
