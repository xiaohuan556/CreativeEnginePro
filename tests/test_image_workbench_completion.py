import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
from PyQt6.QtCore import QPointF
from PyQt6.QtTest import QTest, QSignalSpy
from PyQt6.QtWidgets import QApplication
from ui.image_editor import ImageEditorWidget, ImageLayer, Artboard, Tool, numpy_from_qimage
from ui.image_recovery import list_recovery_files
from core.image_history import ImageHistoryStore, FrozenArray, retained_bytes


class WorkbenchCompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_removing_last_artboard_clears_its_border_and_title(self):
        self.editor._add_artboard('最后一个画板', 160, 120)
        self.assertFalse(self.editor.view.artboard_item.pixmap().isNull())
        self.editor._delete_artboard(self.editor.active_artboard)
        self.assertTrue(self.editor.view.artboard_item.pixmap().isNull())

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.environment = patch.dict(os.environ, {'CEP_IMAGE_RECOVERY_DIR': str(self.root/'recovery')})
        self.environment.start()
        self.editor = ImageEditorWidget()
        self.editor.new_project(160, 120)
        pixels = np.empty((120, 160, 4), np.uint8)
        pixels[:] = [200, 70, 30, 255]
        pixels[:, 60:100] = [20, 70, 230, 255]
        self.layer = ImageLayer('photo', pixels, 160, 120)
        self.layer.x, self.layer.y = 80, 60
        self.editor.project.add_layer(self.layer)
        self.editor.set_active(self.layer)
        self.editor._redraw()

    def tearDown(self):
        worker = self.editor._recovery_worker
        if worker is not None:
            worker.wait(5000)
            self.app.processEvents()
        self.editor._recovery_timer.stop()
        self.editor.close()
        self.editor.deleteLater()
        self.app.processEvents()
        self.environment.stop()
        self.folder.cleanup()

    def backup(self):
        worker = self.editor.autosave_recovery()
        self.assertIsNotNone(worker)
        self.assertTrue(worker.wait(5000))
        self.app.processEvents()
        records = list_recovery_files(self.root/'recovery')
        self.assertTrue(records)
        return records[0]

    def test_compressed_tiles_share_unchanged_pixels_and_only_replace_changed_tile(self):
        store = ImageHistoryStore()
        pixels = np.random.default_rng(3).integers(0, 256, (512, 512, 4), np.uint8)
        first = store.capture(pixels)
        second = store.capture(pixels)
        self.assertEqual(retained_bytes([first, second]), retained_bytes(first))
        pixels[10, 10] = 0
        third = store.capture(pixels)
        shared = sum(a[-1] is b[-1] for a, b in zip(first.tiles, third.tiles))
        self.assertEqual(shared, len(first.tiles)-1)
        self.assertLess(retained_bytes([first, third]), pixels.nbytes*1.1)
        self.assertFalse(np.array_equal(first.copy()[10, 10], pixels[10, 10]))
        np.testing.assert_array_equal(third.copy(), pixels)

    def test_metadata_snapshots_reuse_pixels_and_destructive_edits_invalidate_cache(self):
        before = self.editor._snapshot(metadata_only=True)
        original = before['layers'][0]['pixels']
        self.assertIsInstance(original, FrozenArray)
        self.layer.x += 5
        with patch.object(self.editor._history_store, 'capture', wraps=self.editor._history_store.capture) as capture:
            moved = self.editor._snapshot(metadata_only=True)
            self.assertIs(moved['layers'][0]['pixels'], original)
            self.assertEqual(capture.call_count, 0)
        self.editor._push_undo('paint')
        self.layer.pixels[10, 10] = [0, 0, 0, 0]
        after = self.editor._snapshot(metadata_only=True)
        self.assertEqual(after['layers'][0]['pixels'][10, 10, 3], 0)
        self.assertEqual(original[10, 10, 3], 255)

    def test_history_budget_excludes_shared_live_pixels_and_prunes_unique_states(self):
        self.editor.max_undo = 100
        self.editor._history_byte_limit = 1000
        for _ in range(8):
            before = self.editor._snapshot(metadata_only=True)
            self.layer.x += 1
            self.editor._push_undo_snapshot('move', before)
        self.assertGreater(len(self.editor._history), 5)
        rng = np.random.default_rng(8)
        for _ in range(5):
            self.editor._push_undo('paint')
            self.layer.pixels[:] = rng.integers(0, 256, self.layer.pixels.shape, np.uint8)
        self.assertEqual(len(self.editor._history), 2)

    def test_soft_selection_and_animation_dtypes_survive_project_roundtrip(self):
        self.editor.selection = np.zeros((120, 160), bool)
        self.editor.selection[20:60, 30:80] = True
        self.editor.sel_alpha = self.editor.selection.astype(np.float32)*.375
        self.layer.frames = [self.layer.pixels.copy(), np.zeros_like(self.layer.pixels)]
        self.layer.frame_durations = [80, 120]
        payload = self.editor.pack_project(self.editor._snapshot())
        self.editor._restore(self.editor.unpack_project(payload))
        self.assertEqual(self.editor.selection.dtype, np.bool_)
        self.assertEqual(self.editor.sel_alpha.dtype, np.float32)
        self.assertAlmostEqual(self.editor.sel_alpha[30, 40], .375)
        self.assertEqual(len(self.editor.active.frames), 2)

    def test_selection_changes_clear_undo_redo_and_preserve_zoom(self):
        self.editor.view.resetTransform()
        self.editor.view.zoom_by(2)
        self.editor._commit_rect_sel(QPointF(10, 10), QPointF(80, 80), Tool.SELECT_RECT, 'new')
        first = self.editor.selection.copy()
        self.editor._commit_rect_sel(QPointF(50, 40), QPointF(100, 100), Tool.SELECT_RECT, 'intersect')
        second = self.editor.selection.copy()
        self.editor._clear_selection()
        self.assertIsNone(self.editor.selection)
        self.editor.undo()
        np.testing.assert_array_equal(self.editor.selection, second)
        self.editor.undo()
        np.testing.assert_array_equal(self.editor.selection, first)
        self.editor.redo()
        np.testing.assert_array_equal(self.editor.selection, second)
        self.assertAlmostEqual(self.editor.view.transform().m11(), 2)

    def test_feather_slider_records_one_step_and_restores_soft_edges(self):
        self.editor._commit_rect_sel(QPointF(30, 30), QPointF(100, 90), Tool.SELECT_RECT, 'new')
        initial_count = len(self.editor._history)
        self.editor._on_feather_slider(3)
        self.editor._on_feather_slider(7)
        self.editor._commit_property_history()
        after = self.editor.sel_alpha.copy()
        self.assertEqual(len(self.editor._history), initial_count+1)
        self.editor.undo()
        self.assertIsNone(self.editor.sel_alpha)
        self.editor.redo()
        np.testing.assert_array_equal(self.editor.sel_alpha, after)
        self.assertEqual(self.editor.feather, 7)

    def test_quick_select_uses_brush_size_and_records_one_step_for_whole_stroke(self):
        self.editor.brush_size = 8
        self.editor._quick_select_begin(mode='new')
        self.editor._quick_select_point(QPointF(48, 60))
        self.editor._quick_select_end()
        small = self.editor.selection.copy()
        count = len(self.editor._history)
        self.editor.brush_size = 40
        self.editor._quick_select_begin(mode='new')
        self.editor._quick_select_point(QPointF(48, 60))
        self.editor._quick_select_point(QPointF(50, 60))
        self.editor._quick_select_end()
        large = self.editor.selection.copy()
        self.assertGreater(large.sum(), small.sum())
        self.assertTrue(large[60, 65])
        self.assertEqual(len(self.editor._history), count+1)
        self.editor.undo()
        np.testing.assert_array_equal(self.editor.selection, small)
        self.editor.redo()
        np.testing.assert_array_equal(self.editor.selection, large)

    def test_quick_select_brush_respects_transformed_layer_and_transparency(self):
        self.layer.scale = 2
        self.layer.pixels[:, :40, 3] = 0
        self.editor.brush_size = 20
        self.editor._quick_select_begin(mode='new')
        self.editor._quick_select_point(QPointF(80, 60))
        self.editor._quick_select_end()
        self.assertTrue(self.editor.selection[60, 80])
        self.assertFalse(self.editor.selection[:, :1].any())

    def test_complex_group_mask_blend_uses_draft_and_finishes_full_resolution(self):
        group = ImageLayer('folder', kind='group')
        group.children = [self.layer]
        group.mask = np.full((120, 160), 220, np.uint8)
        group.effects = {'effect_stroke': True, 'effect_stroke_width': 3}
        top = ImageLayer('adjust', kind='adjust')
        top.adjust = {'type': 'invert'}
        self.editor.project.layers = [group, top]
        self.editor.set_active(group)
        original_project = self.editor.project
        original_pixels = self.layer.pixels.copy()
        self.editor.view._start = QPointF(10, 10)
        self.editor._begin_move(self.editor.view._start)
        self.editor._move_to(QPointF(25, 20))
        self.assertTrue(self.editor._draft_active)
        self.assertIs(self.editor.project, original_project)
        self.assertEqual(group.mask.shape, (120, 160))
        exported = numpy_from_qimage(self.editor._render_composite(True))
        self.assertEqual(exported.shape, (120, 160, 4))
        self.editor._end_move()
        self.assertFalse(self.editor._draft_active)
        self.assertAlmostEqual(self.editor.view.composite_item.transform().m11(), 1)
        np.testing.assert_array_equal(self.layer.pixels, original_pixels)
        np.testing.assert_array_equal(numpy_from_qimage(self.editor.view.composite_item.pixmap().toImage()),
                                      numpy_from_qimage(self.editor._render_composite(False)))

    def test_multiselect_draft_undo_keeps_both_layers_selected(self):
        other = ImageLayer('other', self.layer.pixels.copy(), 160, 120)
        other.x, other.y = 90, 70
        self.editor.project.add_layer(other)
        self.editor.selected = [self.layer, other]
        ids = [layer.id for layer in self.editor.selected]
        self.editor.view._start = QPointF(10, 10)
        self.editor._begin_move(self.editor.view._start)
        self.editor._move_to(QPointF(30, 30))
        self.assertTrue(self.editor._draft_active)
        self.editor._end_move()
        self.editor.undo()
        self.assertEqual([layer.id for layer in self.editor.selected], ids)

    def test_artboard_draft_preserves_board_coordinates_and_export_size(self):
        board = Artboard('board', 10, 20, 160, 120)
        board.layers = [self.layer]
        self.editor.project.layers = []
        self.editor.project.artboards = [board]
        self.editor.active_artboard = board
        self.editor._invalidate_doc_bounds()
        size = self.editor._doc_bounds()
        self.editor.view._start = QPointF(250, 250)
        self.editor._begin_move(self.editor.view._start)
        self.editor._move_to(QPointF(260, 260))
        self.assertTrue(self.editor._draft_active)
        self.assertEqual((board.x, board.y), (10, 20))
        self.assertEqual(self.editor._doc_bounds(), size)
        self.editor._end_move()
        self.assertEqual((self.editor._render_composite(True).width(), self.editor._render_composite(True).height()), size)

    def test_large_effect_preview_is_reduced_and_latest_background_result_wins(self):
        self.editor.project.w, self.editor.project.h = 1200, 900
        self.layer.x, self.layer.y = 600, 450
        self.layer.shadow = True
        self.editor.smart_guides_on = False
        self.editor.view._start = QPointF(50, 50)
        self.editor._begin_move(self.editor.view._start)
        self.editor._move_to(QPointF(60, 60))
        self.assertLessEqual(self.editor.view.composite_item.pixmap().width(), 512)
        self.assertGreater(self.editor.view.composite_item.transform().m11(), 1)
        self.editor._end_move()
        first = self.editor._full_render_worker
        self.assertIsNotNone(first)
        # Start another edit before the first job's result is delivered.
        self.editor._begin_move(self.editor.view._start)
        self.editor._move_to(QPointF(80, 70))
        self.editor._end_move()
        self.assertIsNotNone(self.editor._full_render_pending)
        ticks = []
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, lambda: ticks.append(True))
        self.app.processEvents()
        self.assertTrue(ticks)
        for _ in range(200):
            if self.editor._full_render_worker is None:
                break
            QTest.qWait(10)
        self.assertIsNone(self.editor._full_render_worker)
        self.assertIsNone(getattr(self.editor, '_last_full_render_error', None))
        self.assertEqual(self.editor.view.composite_item.pixmap().size().width(), 1200)
        np.testing.assert_array_equal(numpy_from_qimage(self.editor.view.composite_item.pixmap().toImage()),
                                      numpy_from_qimage(self.editor._render_composite(False)))

    def test_stale_render_does_not_overwrite_later_edit(self):
        from PyQt6.QtGui import QImage, QColor
        ticket = (self.editor.project, self.editor._display_generation)
        before = numpy_from_qimage(self.editor.view.composite_item.pixmap().toImage())
        self.editor._display_generation += 1
        outdated = QImage(160, 120, QImage.Format.Format_ARGB32)
        outdated.fill(QColor('magenta'))
        self.editor._full_render_ready(outdated, ticket)
        np.testing.assert_array_equal(numpy_from_qimage(self.editor.view.composite_item.pixmap().toImage()), before)

    def test_draft_failure_restores_live_project_and_uses_exact_fallback(self):
        self.layer.shadow = True
        original = self.editor.project
        self.editor.view._start = QPointF(20, 20)
        self.editor._begin_move(self.editor.view._start)
        with patch.object(self.editor, '_render_draft', side_effect=ValueError('preview unavailable')):
            self.editor._move_to(QPointF(30, 30))
        self.assertIs(self.editor.project, original)
        self.assertFalse(self.editor._draft_active)
        self.editor._end_move()

    def test_rotating_backups_preserve_original_project_and_recover_latest(self):
        source = self.root/'original.json'
        source.write_text('original data', encoding='utf-8')
        self.editor._source_project_path = str(source)
        self.editor._push_undo('first edit')
        first = self.backup()
        self.editor._push_undo('paint')
        self.layer.pixels[0, 0] = [0, 0, 0, 0]
        second = self.backup()
        self.assertNotEqual(first['path'], second['path'])
        self.assertEqual(len(list((self.root/'recovery').glob('*.json'))), 2)
        self.assertEqual(source.read_text(encoding='utf-8'), 'original data')
        self.editor.restore_recovery(second)
        self.assertEqual(self.editor.active.pixels[0, 0, 3], 0)

    def test_new_changes_during_backup_remain_dirty_and_are_saved_next(self):
        self.editor._push_undo('edit')
        worker = self.editor.autosave_recovery()
        self.assertTrue(worker.wait(5000))
        self.editor._push_undo('next edit')
        self.layer.x += 20
        self.app.processEvents()
        self.assertNotEqual(self.editor._recovery_written_token, self.editor._recovery_token())
        latest = self.backup()
        self.assertEqual(latest['revision'], self.editor._recovery_revision)
        self.assertEqual(latest['snapshot']['layers'][0]['x'], 100)

    def test_forced_close_backup_keeps_latest_changes_while_a_save_is_active(self):
        self.editor._push_undo('first')
        worker = self.editor.autosave_recovery()
        self.editor._push_undo('next')
        self.layer.x = 125
        latest = self.editor.autosave_recovery(force=True)
        self.assertIsNotNone(latest)
        self.assertTrue(worker.wait(5000))
        self.assertTrue(latest.wait(5000))
        self.app.processEvents()
        records = list_recovery_files(self.root/'recovery')
        self.assertEqual(records[0]['snapshot']['layers'][0]['x'], 125)

    def test_backup_failure_keeps_previous_generation_and_retries(self):
        self.editor._push_undo('first')
        first = self.backup()
        self.editor._push_undo('second')
        with patch('core.project_files.os.replace', side_effect=OSError('disk unavailable')):
            worker = self.editor.autosave_recovery()
            self.assertTrue(worker.wait(5000))
            self.app.processEvents()
        self.assertTrue(Path(first['path']).exists())
        self.assertNotEqual(self.editor._recovery_written_token, self.editor._recovery_token())
        self.backup()
        self.assertEqual(self.editor._recovery_written_token, self.editor._recovery_token())

    def test_corrupted_newest_backup_falls_back_to_previous_generation(self):
        self.editor._push_undo('first')
        first = self.backup()
        self.editor._push_undo('second')
        newest = self.backup()
        newest['snapshot']['layers'][0]['pixels']['data'] = 'bad compressed pixels'
        Path(newest['path']).write_text(json.dumps({k:v for k,v in newest.items() if k != 'path'}), encoding='utf-8')
        self.editor.restore_recovery(newest)
        self.assertEqual(self.editor.active.pixels[0, 0, 3], 255)
        Path(newest['path']).write_text('{broken json', encoding='utf-8')
        self.assertEqual(list_recovery_files(self.root/'recovery')[0]['path'], first['path'])

    def test_backup_preserves_uncommitted_text_without_stopping_edit_session(self):
        text = ImageLayer('text', kind='text', w=100, h=60)
        text.x, text.y = 80, 60
        self.editor.project.add_layer(text)
        self.editor.set_active(text)
        self.editor._start_text_edit(text, False)
        self.editor._edit_flat = 'Still editing'
        self.editor._edit_cursor = len(self.editor._edit_flat)
        record = self.backup()
        self.assertTrue(self.editor._text_editing)
        self.editor.restore_recovery(record)
        self.assertEqual(self.editor.active.text, 'Still editing')

    def test_recovery_notice_and_manual_save_cleanup(self):
        self.editor._push_undo('edit')
        record = self.backup()
        self.editor._start_recovery_session()
        self.editor.refresh_recovery_notice()
        self.assertFalse(self.editor._recovery_notice.isHidden())
        other = ImageEditorWidget()
        try:
            other.refresh_recovery_notice()
            self.assertFalse(other._recovery_notice.isHidden())
            self.editor.restore_recovery(record)
            self.assertTrue(other._recovery_notice.isHidden())
        finally:
            other.close()
            other.deleteLater()
        self.editor._push_undo('edit restored')
        self.backup()
        worker = self.editor.save_project(self.root/'saved.json')
        self.assertTrue(worker.wait(5000))
        self.app.processEvents()
        self.assertFalse(list((self.root/'recovery').glob('*.recovery.json')))
        self.assertTrue(self.editor._recovery_notice.isHidden())

    def test_expired_recovery_notice_disappears_without_empty_popup(self):
        self.editor._set_recovery_notice(1)
        with patch('ui.image_recovery.QMessageBox.information') as popup:
            self.editor.show_recovery_dialog()
            popup.assert_not_called()
        self.assertTrue(self.editor._recovery_notice.isHidden())

    def test_close_cancel_keeps_backup_and_discard_removes_it(self):
        from PyQt6.QtCore import QTimer
        self.editor._push_undo('edit')
        record = self.backup()
        for label, expected in [('取消', None), ('不保存', True)]:
            def click(label=label):
                dialog = self.app.activeModalWidget()
                next(button for button in dialog.buttons() if button.text() == label).click()
            QTimer.singleShot(0, click)
            self.assertIs(self.editor.close_project_decision(), expected)
            self.assertTrue(Path(record['path']).is_file())
        self.assertTrue(self.editor.discard_recovery())
        self.assertFalse(Path(record['path']).exists())
        self.assertIsNone(self.editor.autosave_recovery(force=True))

    def test_close_save_waits_for_project_and_clears_recovery(self):
        from PyQt6.QtCore import QTimer
        from core.project_files import PIC_SUFFIX
        self.editor._push_undo('edit')
        record = self.backup()
        path = str(self.root / ('confirmed' + PIC_SUFFIX))

        def click_save():
            dialog = self.app.activeModalWidget()
            next(button for button in dialog.buttons() if button.text() == '保存').click()

        with patch('ui.image_file_ops.choose_project_save_path', return_value=path), patch('ui.image_recovery.QMessageBox.warning'):
            QTimer.singleShot(0, click_save)
            self.assertIs(self.editor.close_project_decision(), False, self.editor.status_tool.text())
        self.assertTrue(Path(path).is_file())
        self.assertFalse(Path(record['path']).exists())

    def test_manual_save_of_recent_property_edit_closes_without_prompt(self):
        from core.project_files import PIC_SUFFIX, read_project

        path = self.root / ('property-edit' + PIC_SUFFIX)
        self.editor._set_shadow_blur(23)
        self.assertIsNotNone(self.editor._pending_property_history)
        self.assertIsNotNone(self.editor.save_project(path))
        # Reproduce the deferred history update landing between Save and Close.
        self.editor._commit_property_history()
        with patch('ui.image_recovery.QMessageBox') as prompt:
            self.assertIs(self.editor.close_project_decision(), False)
            prompt.assert_not_called()
        self.assertEqual(read_project(path, PIC_SUFFIX)['layers'][0]['shadow_blur'], 23)

        self.editor._set_shadow_blur(31)
        with patch('ui.image_recovery.QMessageBox') as prompt:
            self.assertIsNone(self.editor.close_project_decision())
            prompt.assert_called_once()

    def test_invalid_backup_does_not_replace_existing_document(self):
        before = self.layer.pixels.copy()
        bad = dict(document_id='a'*32, path=str(self.root/'invalid'), snapshot={
            'w': 40, 'h': 50, 'transparent': True, 'bg': [0, 0, 0], 'layers': [{}]})
        with self.assertRaises(ValueError):
            self.editor.restore_recovery(bad)
        self.assertEqual((self.editor.project.w, self.editor.project.h), (160, 120))
        np.testing.assert_array_equal(self.editor.active.pixels, before)

    def test_damaged_metadata_is_ignored_without_hiding_valid_recovery(self):
        self.editor._push_undo('edit')
        record = self.backup()
        damaged = dict(record)
        damaged.pop('saved_at')
        (self.root/'recovery'/'damaged.recovery.json').write_text(json.dumps(damaged), encoding='utf-8')
        self.assertEqual(len(list_recovery_files(self.root/'recovery')), 1)
        damaged['document_id'] = '../outside'
        with self.assertRaises(ValueError):
            self.editor.restore_recovery(damaged)


if __name__ == '__main__':
    unittest.main()
