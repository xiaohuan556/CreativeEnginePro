import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt6.QtCore import Qt,QPoint,QPointF
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
from ui.image_editor import ImageEditorWidget,ImageLayer,Artboard,Tool


class TextFrameInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = ImageEditorWidget()
        self.editor.new_project(800,500)
        self.layer = ImageLayer('文字',kind='text',w=180,h=100)
        self.layer.text = 'Resize the paragraph frame and keep the font size.'
        self.layer.font_size = 18
        self.layer.x,self.layer.y = 400,250
        self.editor.project.add_layer(self.layer)
        self.editor.set_active(self.layer)
        self.editor.set_tool(Tool.TEXT)
        self.editor.smart_guides_on = False
        self.editor.snap_on = False
        self.editor.resize(1400,900)
        self.editor.show()
        self.app.processEvents()
        self.editor.view.resetTransform()
        self.editor.view.centerOn(400,250)
        self.app.processEvents()

    def tearDown(self):
        self.editor.stop_text_session()
        self.editor.close()
        self.editor.deleteLater()
        self.app.processEvents()

    def drag(self,start,end):
        surface = self.editor.view.viewport()
        QTest.mousePress(surface,Qt.MouseButton.LeftButton,pos=start)
        QTest.mouseMove(surface,end,delay=5)
        QTest.mouseRelease(surface,Qt.MouseButton.LeftButton,pos=end)
        self.app.processEvents()

    def center(self):
        return self.editor.view.mapFromScene(self.editor._text_scene_point(self.layer,QPointF(self.layer.w/2,self.layer.h/2)))

    def test_text_tool_drags_whole_frame(self):
        start = self.center()
        self.drag(start,start+QPoint(45,25))
        self.assertAlmostEqual(self.layer.x,445,delta=1)
        self.assertAlmostEqual(self.layer.y,275,delta=1)
        self.assertEqual((self.layer.font_size,self.layer.scale),(18,1.))
        self.editor.undo()
        self.assertEqual((self.editor.active.x,self.editor.active.y),(400,250))

    def test_text_tool_stretches_width_without_scaling_font(self):
        left = self.editor._layer_bbox(self.layer).left()
        handle = self.editor.text_frame_handles(self.layer)['e']
        self.drag(handle,handle+QPoint(120,0))
        self.assertAlmostEqual(self.layer.w,300,delta=1)
        self.assertEqual(self.layer.h,100)
        self.assertAlmostEqual(self.editor._layer_bbox(self.layer).left(),left,delta=1)
        self.assertEqual((self.layer.font_size,self.layer.scale),(18,1.))

    def test_editing_frame_can_stretch_and_move_without_ending_typing(self):
        self.editor._start_text_edit(self.layer,False)
        handle = self.editor.text_frame_handles(self.layer)['se']
        self.drag(handle,handle+QPoint(80,40))
        self.assertTrue(self.editor._text_editing)
        self.assertEqual((self.layer.w,self.layer.h),(260,140))
        start = self.center()
        before = self.layer.x,self.layer.y
        self.drag(start,start+QPoint(30,-20))
        self.assertTrue(self.editor._text_editing)
        self.assertAlmostEqual(self.layer.x,before[0]+30,delta=1)
        self.assertAlmostEqual(self.layer.y,before[1]-20,delta=1)
        QTest.keyClicks(self.editor.view,'!')
        self.assertIn('!',self.editor._edit_flat)

    def test_accepting_text_does_not_shrink_resized_frame(self):
        self.editor._start_text_edit(self.layer,False)
        handle = self.editor.text_frame_handles(self.layer)['e']
        self.drag(handle,handle+QPoint(150,0))
        width,height = self.layer.w,self.layer.h
        self.editor._edit_flat = 'Short'
        self.editor._end_text_edit(True)
        self.assertEqual((self.layer.w,self.layer.h),(width,height))
        self.assertEqual(self.layer.text,'Short')
        self.editor._start_text_edit(self.layer,False)
        self.editor._end_text_edit(True)
        self.assertEqual((self.layer.w,self.layer.h),(width,height))

    def test_move_tool_resizes_text_box_before_free_transform(self):
        self.editor.set_tool(Tool.MOVE)
        handle = self.editor.text_frame_handles(self.layer)['s']
        self.drag(handle,handle+QPoint(0,70))
        self.assertEqual(self.layer.h,170)
        self.assertEqual((self.layer.font_size,self.layer.scale),(18,1.))

    def test_resize_undo_redo_and_project_roundtrip(self):
        handle = self.editor.text_frame_handles(self.layer)['e']
        self.drag(handle,handle+QPoint(100,0))
        self.editor.undo()
        self.assertEqual(self.editor.active.w,180)
        self.editor.redo()
        self.assertEqual(self.editor.active.w,280)
        snapshot = self.editor.unpack_project(self.editor.pack_project(self.editor._snapshot()))
        self.editor._restore(snapshot)
        self.assertEqual(self.editor.active.w,280)
        self.assertTrue(self.editor.active.text_box_explicit)

    def test_rotated_frame_resize_uses_local_geometry(self):
        self.layer.rotation = 30
        self.editor._redraw()
        start_world = self.editor._text_scene_point(self.layer,QPointF(self.layer.w,self.layer.h/2))
        target_world = self.editor._text_scene_point(self.layer,QPointF(self.layer.w+80,self.layer.h/2))
        self.drag(self.editor.view.mapFromScene(start_world),self.editor.view.mapFromScene(target_world))
        self.assertAlmostEqual(self.layer.w,260,delta=2)
        self.assertEqual((self.layer.font_size,self.layer.rotation),(18,30))

    def test_artboard_text_drag_and_resize_preserve_artboard_context(self):
        board = Artboard('画板',100,50,800,500)
        board.layers = [self.layer]
        self.editor.project.layers = []
        self.editor.project.artboards = [board]
        self.editor.active_artboard = board
        self.editor._invalidate_doc_bounds()
        self.editor._redraw()
        self.editor.view.centerOn(self.editor._text_scene_point(self.layer,QPointF(90,50)))
        self.app.processEvents()
        self.editor._start_text_edit(self.layer,False)
        handle = self.editor.text_frame_handles(self.layer)['e']
        self.drag(handle,handle+QPoint(90,0))
        self.assertAlmostEqual(self.layer.w,270,delta=1)
        self.assertTrue(self.editor._text_editing)
        self.assertIs(self.editor.active_artboard,board)

    def test_click_without_drag_does_not_move_or_add_history(self):
        count = len(self.editor._history)
        QTest.mouseClick(self.editor.view.viewport(),Qt.MouseButton.LeftButton,pos=self.center())
        self.assertEqual((self.layer.x,self.layer.y),(400,250))
        self.assertEqual(len(self.editor._history),count)

    def test_numeric_box_width_reflows_without_font_scale(self):
        self.editor._text_property_panel.controls['w'].setValue(320)
        self.assertEqual(self.layer.w,320)
        self.assertEqual((self.layer.font_size,self.layer.scale),(18,1.))
        self.assertEqual(self.editor._layer_bbox(self.layer).left(),310)


if __name__=='__main__':
    unittest.main()
