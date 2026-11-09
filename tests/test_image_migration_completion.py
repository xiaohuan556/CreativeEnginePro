import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import numpy as np
from PIL import Image
from PyQt6.QtCore import Qt,QPointF,QRectF
from PyQt6.QtTest import QTest,QSignalSpy
from PyQt6.QtWidgets import QApplication
from ui.image_editor import ImageEditorWidget,ImageLayer,Tool,numpy_from_qimage
from core.image_adjustments import EXTRA_ADJUSTMENTS,apply_development
from core.compositor_io import export_comp,read_comp
from core.image_project_io import ProjectSaveWorker
from core.project_files import PIC_SUFFIX, read_project


class CompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.editor = ImageEditorWidget()
        self.editor.new_project(24,16)
        pixels = np.zeros((16,24,4),np.uint8)
        pixels[4:12,8:16] = [210,90,30,255]
        self.layer = ImageLayer('图层',pixels,24,16)
        self.layer.x,self.layer.y = 12,8
        self.editor.project.add_layer(self.layer)
        self.editor.set_active(self.layer)

    def tearDown(self):
        self.editor.detach_comp()
        self.editor.close()
        self.editor.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_all_extra_effects_render_finite_pixels_and_deterministic_grain(self):
        rgb = np.full((16,24,3),120,np.float32)
        for kind,(_,specs) in EXTRA_ADJUSTMENTS.items():
            with self.subTest(kind=kind):
                params = {key:default for key,_,_,_,default in specs}
                result = apply_development(rgb,kind,params)
                self.assertEqual(result.shape,rgb.shape)
                self.assertTrue(np.isfinite(result).all())
                self.assertTrue(((result>=0)&(result<=255)).all())
                if kind=='grain':
                    np.testing.assert_array_equal(result,apply_development(rgb,kind,params))

    def test_neutral_photo_development_preserves_input(self):
        rgb = np.random.default_rng(3).uniform(0,255,(16,24,3)).astype(np.float32)
        result = apply_development(rgb,'camera_raw',{})
        np.testing.assert_allclose(result,rgb,atol=.0001)

    def test_adjustment_side_panel_edits_and_undo(self):
        self.editor._add_adjust_layer('camera_raw')
        panel = self.editor._adjust_property_panel
        self.assertEqual(panel.current,self.editor.active)
        panel.controls['exposure'].setValue(100)
        self.editor._commit_property_history()
        self.assertEqual(self.editor.active.adjust['exposure'],100)
        self.editor.undo()
        self.assertEqual(self.editor.active.adjust['exposure'],0)

    def test_system_clipboard_payload_works_without_process_local_cache(self):
        from ui.image_workbench_ops import WorkbenchOperations
        self.editor.copy_layers()
        WorkbenchOperations._layer_clipboard = None
        self.assertTrue(self.editor.paste_layers())
        np.testing.assert_array_equal(self.editor.active.pixels,self.layer.pixels)

    def test_shortcut_remap_disables_old_tool_key_and_works_in_canvas(self):
        self.editor.resize(1000,700)
        self.editor.show()
        self.app.processEvents()
        self.editor.apply_shortcuts({'工具 › BRUSH':'F8'},persist=False)
        self.editor.set_tool(Tool.MOVE)
        self.editor.view.setFocus()
        QTest.keyClick(self.editor.view,Qt.Key.Key_B)
        self.assertEqual(self.editor.tool,Tool.MOVE)
        QTest.keyClick(self.editor.view,Qt.Key.Key_F8)
        self.assertEqual(self.editor.tool,Tool.BRUSH)

    def test_shortcuts_do_not_steal_text_and_reject_conflicts(self):
        self.editor.show()
        self.app.processEvents()
        self.editor._layer_search.setFocus()
        QTest.keyClicks(self.editor._layer_search,'btest')
        self.assertEqual(self.editor._layer_search.text(),'btest')
        self.assertEqual(self.editor.tool,Tool.MOVE)
        with self.assertRaises(ValueError):
            self.editor.apply_shortcuts({'工具 › BRUSH':'Ctrl+S'},persist=False)

    def test_continuous_brush_has_no_gaps(self):
        self.layer.pixels.fill(0)
        self.editor.brush_size = 4
        self.editor.set_tool(Tool.BRUSH)
        self.editor._brush_last_point = None
        self.editor._stroke_point(QPointF(2,8))
        self.editor._stroke_point(QPointF(20,8))
        self.assertTrue((self.layer.pixels[8,2:21,3]>0).all())

    def test_pressure_controls_opacity_independently(self):
        self.layer.pixels.fill(0)
        self.editor._tablet_pressure = .25
        self.editor.pressure_size = False
        self.editor.paint_stamp(QPointF(12,8))
        self.assertEqual(self.layer.pixels[8,12,3],64)

    def test_clone_source_is_the_clicked_center(self):
        self.layer.pixels.fill(0)
        self.layer.pixels[6:10,2:6] = [255,0,0,255]
        self.editor.set_tool(Tool.CLONE)
        self.editor.brush_size = 2
        self.editor._set_clone_source(QPointF(4,8))
        self.editor.begin_clone_stroke(QPointF(12,8))
        self.editor._stroke_clone(QPointF(12,8))
        np.testing.assert_array_equal(self.layer.pixels[8,12],[255,0,0,255])

    def test_grid_targets_and_subdivisions_roundtrip(self):
        self.editor.project.grid_size = 20
        self.editor.project.grid_subdivisions = 4
        self.editor.project.show_grid = True
        self.editor.view.resetTransform()
        point = self.editor._snap_pt(QPointF(9,6))
        self.assertEqual(point,QPointF(10,5))
        self.editor.project.snap_targets['grid'] = False
        self.assertEqual(self.editor._snap_pt(QPointF(9,6)),QPointF(9,6))
        snapshot = self.editor._snapshot()
        self.editor._restore(snapshot)
        self.assertEqual(self.editor.project.grid_subdivisions,4)
        self.assertFalse(self.editor.project.snap_targets['grid'])

    def test_trim_corner_color_keeps_source_editable(self):
        self.editor.project.transparent = False
        self.editor.trim_canvas(1)
        self.assertEqual((self.editor.project.w,self.editor.project.h),(8,8))
        self.assertEqual(self.layer.pixels.shape,(16,24,4))
        self.editor.undo()
        self.assertEqual(self.editor.project.w,24)

    def test_image_stroke_overlay_and_shadow_render(self):
        self.layer.effects = {'effect_stroke':True,'effect_stroke_width':3,'effect_stroke_color':'#ffffff',
                              'color_overlay':True,'color_overlay_color':'#00ff00','color_overlay_opacity':1.}
        self.layer.shadow = True
        self.layer.shadow_dx = 4
        self.layer.shadow_dy = 0
        self.layer.shadow_blur = 0
        result = numpy_from_qimage(self.editor._render_layer_canvas(self.layer))
        np.testing.assert_array_equal(result[8,12,:3],[0,255,0])
        self.assertGreater(result[8,6,3],0)
        self.assertGreater(result[8,18,3],0)

    def test_applying_mask_does_not_repeat_baked_effects(self):
        self.layer.mask = np.full((16,24),200,np.uint8)
        self.layer.effects = {'effect_stroke':True,'effect_stroke_width':2}
        before = numpy_from_qimage(self.editor._render_layer_canvas(self.layer))
        self.editor._apply_layer_mask(self.layer)
        after = numpy_from_qimage(self.editor._render_layer_canvas(self.layer))
        np.testing.assert_array_equal(before,after)

    def test_background_save_and_atomic_failure_preserve_file(self):
        path = self.root/('save' + PIC_SUFFIX)
        worker = self.editor.save_project(path)
        self.assertTrue(worker.wait(5000))
        self.app.processEvents()
        self.assertTrue(path.exists())
        previous = path.read_bytes()
        failed = ProjectSaveWorker(path,self.editor._snapshot())
        signals = QSignalSpy(failed.failed)
        with patch('core.project_files.os.replace',side_effect=OSError('disk unavailable')):
            failed.start()
            self.assertTrue(failed.wait(5000))
        self.app.processEvents()
        self.assertEqual(path.read_bytes(),previous)
        self.assertEqual(len(signals),1)

    def test_horizontal_and_vertical_flip_are_independent_with_linked_mask(self):
        self.layer.pixels[:,:,:] = [220,80,20,255]
        self.layer.mask = np.zeros((16,24),np.uint8)
        self.layer.mask[:,:12] = 255
        before = numpy_from_qimage(self.editor._render_layer_canvas(self.layer))
        self.editor._flip_horizontal(self.layer)
        after = numpy_from_qimage(self.editor._render_layer_canvas(self.layer))
        np.testing.assert_array_equal(after,np.fliplr(before))
        self.assertTrue(self.layer.flip_x)
        self.assertFalse(self.layer.flip_y)
        self.editor._flip_vertical(self.layer)
        np.testing.assert_array_equal(numpy_from_qimage(self.editor._render_layer_canvas(self.layer)),np.flipud(after))

    def test_canvas_rotation_preserves_source_pixels_and_visuals(self):
        before = numpy_from_qimage(self.editor._render_composite(True))
        source = self.layer.pixels.copy()
        self.editor._rotate_canvas(90,True)
        self.assertEqual((self.editor.project.w,self.editor.project.h),(16,24))
        np.testing.assert_array_equal(source,self.layer.pixels)
        np.testing.assert_array_equal(numpy_from_qimage(self.editor._render_composite(True)),np.rot90(before,-1))

    def test_free_distort_corner_roundtrip(self):
        self.editor.distort_corner('nw',QPointF(4,2),{'bbox':self.editor._layer_bbox(self.layer)})
        mapped = self.layer.canvas_transform().map(QPointF(0,0))
        self.assertAlmostEqual(mapped.x(),4,places=5)
        self.assertAlmostEqual(mapped.y(),2,places=5)
        before = numpy_from_qimage(self.editor._render_composite(True))
        self.editor._restore(self.editor.unpack_project(self.editor.pack_project(self.editor._snapshot())))
        np.testing.assert_array_equal(before,numpy_from_qimage(self.editor._render_composite(True)))

    def test_resize_rotated_layer_maps_world_corners_without_resampling(self):
        self.layer.rotation = 25
        points = [(0,0),(24,0),(24,16),(0,16)]
        transform = self.layer.canvas_transform()
        before = [transform.map(QPointF(x,y)) for x,y in points]
        source = self.layer.pixels.copy()
        self.editor.resize_image(48,16)
        after = self.layer.canvas_transform()
        for point,(x,y) in zip(before,points):
            mapped = after.map(QPointF(x,y))
            self.assertAlmostEqual(mapped.x(),point.x()*2,places=4)
            self.assertAlmostEqual(mapped.y(),point.y(),places=4)
        np.testing.assert_array_equal(source,self.layer.pixels)

    def test_folder_alignment_moves_its_children(self):
        self.editor._group_selected()
        folder = self.editor.active
        self.editor._align_reference.setCurrentIndex(self.editor._align_reference.findData('canvas'))
        self.editor._align_layers('left')
        self.assertAlmostEqual(self.editor._layer_bbox(folder).left(),0)
        self.assertEqual(folder.children[0].x,12)

    def test_object_selection_separates_clicked_colored_object(self):
        self.layer.pixels[:] = [255,255,255,255]
        self.layer.pixels[4:12,8:16] = [255,0,0,255]
        self.editor._sel_combine = 'new'
        self.editor.object_select(QPointF(12,8))
        self.assertTrue(self.editor.selection[8,12])
        self.assertFalse(self.editor.selection[0,0])

    def test_late_subject_result_does_not_overwrite_switched_document(self):
        self.editor._subject_project = self.editor.project
        self.editor._subject_board = None
        self.editor.new_project(30,20)
        self.editor._subject_finished(np.full((16,24,4),255,np.uint8))
        self.assertIsNone(self.editor.selection)

    def test_paragraph_typography_persists_and_renders(self):
        layer = ImageLayer('文本',kind='text',w=240,h=150)
        layer.text = 'Paragraph\nSecond line'
        layer.font_size,layer.letter_spacing,layer.line_spacing = 18,3,150
        self.editor.project.add_layer(layer)
        self.editor.set_active(layer)
        image = numpy_from_qimage(self.editor._render_text(layer))
        self.assertTrue(image[:,:,3].any())
        self.assertIs(self.editor._text_property_panel.current,layer)
        restored = self.editor._deser_layer(self.editor._ser_layer(layer,True))
        self.assertEqual((restored.letter_spacing,restored.line_spacing),(3,150))

    def test_line_is_editable_shape_with_style_persistence(self):
        self.editor._line_direction = (12,-6)
        self.editor._commit_shape(Tool.SHAPE_LINE,QRectF(3,3,12,6))
        line = self.editor.active
        self.assertEqual(line.kind,'shape')
        self.assertEqual(line.shape,'line')
        self.assertEqual(line.line_start,(0.,1.))
        self.assertTrue(numpy_from_qimage(self.editor._render_shape(line))[:,:,3].any())
        self.editor.set_line_option('line_style',2)
        self.assertEqual(self.editor._deser_layer(self.editor._ser_layer(line,True)).line_style,2)

    def test_pdf_export_and_channel_pack_split(self):
        path = self.root/'image.pdf'
        self.editor.export_pdf(path)
        self.assertTrue(path.read_bytes().startswith(b'%PDF'))
        packed = self.editor.channel_pack_pixels([self.layer,0,255,255])
        self.assertTrue((packed[:,:,1]==0).all())
        self.assertTrue((packed[:,:,2:]==255).all())
        self.editor.split_channels()
        self.assertEqual(len(self.editor.selected),4)
        self.assertTrue(self.editor.active.name.endswith('A'))

    def test_queued_save_writes_latest_snapshot(self):
        path = self.root/('queue' + PIC_SUFFIX)
        worker = self.editor.save_project(path)
        self.editor._push_undo('改名')
        self.layer.name = '最新名称'
        self.editor.save_project(path)
        self.assertTrue(worker.wait(5000))
        for _ in range(20):
            self.app.processEvents()
            current = self.editor._save_jobs.get(str(path.resolve()))
            if current is None:
                break
            current.wait(5000)
        data = read_project(path, PIC_SUFFIX)
        self.assertEqual(data['layers'][0]['name'],'最新名称')

    def test_deleting_last_layer_does_not_leave_detached_active_selection(self):
        self.editor._delete_selected()
        self.assertIsNone(self.editor.active)
        self.assertEqual(self.editor.selected,[])

    def native_package(self):
        folder = self.root/'native.comp'
        (folder/'images').mkdir(parents=True)
        identity = str(uuid.uuid4()).upper()
        Image.fromarray(self.layer.pixels).save(folder/'images'/f'{identity}.png')
        record = dict(id=identity,name='Native',imageFile=f'{identity}.png',isVisible=True,isGroup=False,
                      transform=dict(origin=[0,0],size=[24,16],rotation=0),opacity=1,blendMode='Normal')
        manifest = dict(format='com.compositor.project',version=20,width=24,height=16,layers=[record])
        (folder/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
        return folder,manifest

    def test_native_comp_import_and_undo(self):
        folder,_ = self.native_package()
        asset = read_comp(folder)
        self.assertEqual(asset.layers[0]['name'],'Native')
        self.editor._load_comp(folder)
        self.assertEqual(self.editor.active.name,'Native')
        self.editor.undo()
        self.assertEqual(self.editor.active.name,'图层')

    def test_comp_export_reopens_complete_editable_workbench_snapshot(self):
        self.editor._add_adjust_layer('camera_raw')
        self.editor.active.adjust['exposure'] = 100
        folder = self.root/'export.comp'
        export_comp(self.editor,folder)
        self.assertTrue((folder/'manifest.json').exists())
        self.editor.new_project(2,2)
        self.editor._load_comp(folder)
        self.assertEqual(self.editor.active.adjust['type'],'camera_raw')
        self.assertEqual(self.editor.project.w,24)

    def test_comp_external_conflict_keeps_unsaved_local_changes(self):
        folder,manifest = self.native_package()
        self.editor.open_comp(folder)
        self.editor._push_undo('本地改名')
        self.editor.active.name = '本地修改'
        manifest['layers'][0]['name'] = '外部修改'
        (folder/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
        self.editor._comp_changed()
        self.assertTrue(self.editor._comp_pending)
        self.assertEqual(self.editor.active.name,'本地修改')

    def test_comp_rejects_unsafe_asset_path(self):
        folder,manifest = self.native_package()
        manifest['layers'][0]['imageFile'] = '../outside.png'
        (folder/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
        with self.assertRaises(ValueError):
            read_comp(folder)


if __name__ == '__main__':
    unittest.main()
