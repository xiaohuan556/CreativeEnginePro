import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtGui import QColor, QImage
    from PyQt6.QtWidgets import QApplication, QPushButton
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from ai.storyboard import normalize_storyboard
    from ui.script_workbench import (
        StoryboardProductionPanel, StoryboardShotCard, ScriptWorkbench,
    )
    from ui.media_preview import open_single_media_preview
    from ai.ui.production_canvas import CanvasContextInspector, _InspectorPreviewLabel
    QT_AVAILABLE = True
except ModuleNotFoundError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 runtime is not available")
class StoryboardResultPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _image(path: Path, color: str):
        image = QImage(64, 64, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        assert image.save(str(path))

    def test_storyboard_model_selection_is_saved_as_production_contract(self):
        workbench = ScriptWorkbench()
        workbench._storyboard = {"id":"provider-lock", "shots":[]}
        workbench.storyboard_image_provider.blockSignals(True)
        workbench.storyboard_image_provider.clear()
        workbench.storyboard_image_provider.addItem("GPT-Image-2", "gptimage")
        workbench.storyboard_image_provider.addItem("Seedream 5.0 Pro", "seedream")
        workbench.storyboard_image_provider.setCurrentIndex(1)
        workbench.storyboard_image_provider.blockSignals(False)
        workbench.storyboard_video_provider.blockSignals(True)
        workbench.storyboard_video_provider.clear()
        workbench.storyboard_video_provider.addItem("Seedance 2.0", "seedance")
        workbench.storyboard_video_provider.blockSignals(False)

        workbench._save_visual_lock()

        self.assertEqual(
            "seedream",
            workbench._storyboard["production_models"]["image_provider"])
        self.assertEqual(
            "seedream",
            workbench._storyboard["visual_bible"]["image_provider"])
        self.assertEqual(
            "seedance",
            workbench._storyboard["production_models"]["video_provider"])
        workbench.deleteLater()
        self.app.processEvents()

    def test_each_candidate_can_be_refined_or_removed_by_exact_path(self):
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "first.png"
            second = Path(temp) / "second.png"
            self._image(first, "red")
            self._image(second, "blue")
            shot = {
                "id": "shot_1", "number": 1, "start": 0, "duration": 4,
                "assets": [
                    {"path": str(first), "kind": "image"},
                    {"path": str(second), "kind": "image"},
                ],
                "selected_asset": str(first),
                "preview_asset": str(first),
                "selected_image_asset": str(first),
                "anchor_frame_id": str(first),
                "selected_video_asset": "",
            }
            panel = StoryboardProductionPanel()
            panel.set_shot(shot)
            refined = []
            removed = []
            approved = []
            panel.refine_requested.connect(
                lambda shot_id, path: refined.append((shot_id, path)))
            panel.remove_result_requested.connect(
                lambda shot_id, path: removed.append((shot_id, path)))
            panel.asset_approved.connect(
                lambda shot_id, path: approved.append((shot_id, path)))

            panel._request_refine(str(second))
            panel._request_remove_result(str(first))
            panel._request_approve_asset(str(second))
            self.app.processEvents()

            self.assertEqual(str(second), shot["selected_asset"])
            self.assertEqual(str(second), shot["preview_asset"])
            self.assertEqual(str(first), shot["selected_image_asset"])
            self.assertEqual([("shot_1", str(second))], refined)
            self.assertEqual([("shot_1", str(first))], removed)
            self.assertEqual([("shot_1", str(second))], approved)
            self.assertEqual("送图片 2 到 PS 精修", panel.refine_btn.text())
            button_texts = [button.text() for button in
                            panel.findChildren(QPushButton)]
            self.assertIn("PS 精修", button_texts)
            self.assertIn("删除", button_texts)
            self.assertIn("设为定稿图片", button_texts)
            panel.deleteLater()
            self.app.processEvents()

    def test_missing_prerequisites_show_explicit_messages(self):
        panel = StoryboardProductionPanel()
        panel.set_shot({
            "id": "shot_empty", "number": 1, "start": 0, "duration": 4,
            "assets": [], "selected_image_asset": "", "selected_video_asset": "",
        })
        messages = []
        with patch.object(
                __import__("ui.script_workbench", fromlist=["QMessageBox"]).QMessageBox,
                "information",
                side_effect=lambda _parent, title, text: messages.append((title, text))):
            panel._request_preview()
            panel._request_image_to_video()
            panel._request_exact_element()
        self.assertEqual(3, len(messages))
        self.assertIn("还没有可以预览", messages[0][1])
        self.assertIn("设为定稿图片", messages[1][1])
        self.assertIn("没有绑定", messages[2][1])
        panel.deleteLater()
        self.app.processEvents()

    def test_last_shot_primary_button_has_bottom_scroll_safety_area(self):
        workbench = ScriptWorkbench()
        workbench.resize(1280, 760)
        workbench._storyboard = normalize_storyboard({
            "title": "滚动测试",
            "shots": [{
                "scene": f"镜头 {index}", "duration": 2,
                "image_prompt": "frame", "video_prompt": "motion",
            } for index in range(10)],
        }, 20)
        workbench._render_storyboard()
        # The legacy storyboard widget is detached from the public AI Script
        # tabs, but remains renderable while canvas generation handlers are
        # being migrated.
        self.assertEqual(-1, workbench.mode_tabs.indexOf(workbench.storyboard_page))
        workbench.storyboard_page.setParent(None)
        workbench.storyboard_page.resize(1280, 760)
        workbench.storyboard_page.show()
        self.app.processEvents()
        scroll = workbench.storyboard_scroll
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        self.app.processEvents()
        last_card = list(workbench._shot_cards.values())[-1]
        button_bottom = last_card._primary_btn.mapTo(
            scroll.viewport(), last_card._primary_btn.rect().bottomLeft()).y()
        safety_space = scroll.viewport().height() - button_bottom
        self.assertGreaterEqual(safety_space, 80)
        workbench.storyboard_page.close()
        workbench.deleteLater()
        self.app.processEvents()

    def test_ai_section_exposes_only_ai_script(self):
        workbench = ScriptWorkbench()
        self.assertEqual(
            ["AI 脚本"],
            [workbench.mode_tabs.tabText(index)
             for index in range(workbench.mode_tabs.count())])
        self.assertIs(workbench.director_page, workbench.idea_page)
        self.assertEqual(-1, workbench.mode_tabs.indexOf(workbench.idea_page))
        self.assertEqual(-1, workbench.mode_tabs.indexOf(workbench.storyboard_page))
        self.assertIs(workbench.mode_tabs.currentWidget(), workbench.script_page)
        self.assertTrue(workbench.idea_assistant_box.isHidden())
        self.assertTrue(workbench.director_advanced_box.isHidden())

        workbench._idea_messages = [{
            "role": "assistant", "content": "一版完整故事",
        }]
        workbench._adopt_idea()
        self.assertEqual("一版完整故事", workbench.director_idea.toPlainText())
        self.assertIsNone(workbench._asset_extract_worker)
        self.assertEqual("生成剧本和分镜  →", workbench.btn_director.text())
        workbench.deleteLater()
        self.app.processEvents()

    def test_shot_card_uses_one_primary_button_before_advanced_controls(self):
        shot = {
            "id": "shot_simple", "number": 1, "start": 0, "duration": 4,
            "scene": "机器人走进客厅", "assets": [],
        }
        card = StoryboardShotCard(shot)
        prepared = []
        generated = []
        card.prepare_assets.connect(prepared.append)
        card.generate_image.connect(generated.append)

        self.assertTrue(card._advanced_box.isHidden())
        self.assertEqual("准备本镜头素材", card._primary_btn.text())
        card._run_primary_action()
        self.assertEqual(["shot_simple"], prepared)
        self.assertEqual([], generated)

        card.set_asset_preparation_status("素材已就绪 ✓", True)
        self.assertEqual("生成这张画面", card._primary_btn.text())
        card._run_primary_action()
        self.assertEqual(["shot_simple"], generated)
        card.deleteLater()
        self.app.processEvents()

    def test_storyboard_page_exposes_gpt_screenplay_before_paid_generation(self):
        workbench = ScriptWorkbench()
        workbench._storyboard = normalize_storyboard({
            "title": "剧本草稿测试",
            "production_bible": {
                "logline": "机器人发现客厅每天都会忘记自己。",
                "visual_style": "克制的霓虹动漫",
                "continuity_rules": ["机器人外壳始终保持白色"],
            },
            "screenplay": {
                "hook": "墙上的全家福少了一个人。",
                "conflict": "机器人发现消失的是未来的自己。",
                "ending": "它主动走进空白相框。",
                "beats": [{
                    "id": "beat_01", "start": 0, "end": 4,
                    "purpose": "钩子", "summary": "发现照片异常",
                }],
            },
            "shots": [{
                "duration": 4, "beat_id": "beat_01",
                "dramatic_purpose": "建立悬念",
                "entry_state": "机器人平静", "exit_state": "机器人警觉",
                "scene": "机器人看向墙上的照片",
            }],
        }, 4)
        workbench._render_storyboard()
        self.assertIn("1 个剧情段", workbench.storyboard_plan_status.text())
        self.assertIn("1/1 个镜头拍摄合同", workbench.storyboard_plan_status.text())
        self.assertIn("墙上的全家福少了一个人", workbench.storyboard_plan_view.toPlainText())
        card = next(iter(workbench._shot_cards.values()))
        self.assertIn("建立悬念", card._contract_summary.text())
        workbench.deleteLater()
        self.app.processEvents()

    def test_hidden_performance_controls_update_structured_shot_data(self):
        shot = {
            "id": "shot_performance", "number": 1, "start": 0, "duration": 4,
            "scene": "女孩看向朋友", "assets": [],
            "performance": {
                "line_type": "none", "dialogue": "", "emotion": "自然",
            },
        }
        card = StoryboardShotCard(shot)
        card._performance_type.setCurrentIndex(
            card._performance_type.findData("dialogue"))
        card._performance_speaker.setText("女孩")
        card._performance_emotion.setCurrentText("紧张")
        card._dialogue_edit.setPlainText("你也听见了吗？")
        card._performance_gaze.setText("朋友")
        card._performance_gesture.setText("抓紧杯子")
        card._sync_performance()

        self.assertEqual("dialogue", shot["performance"]["line_type"])
        self.assertEqual("女孩", shot["performance"]["speaker"])
        self.assertEqual("你也听见了吗？", shot["performance"]["dialogue"])
        self.assertEqual("紧张", shot["performance"]["emotion"])
        self.assertEqual("dialogue_performance", shot["generation_route"])
        self.assertIn("女孩", card._performance_summary.text())
        card.deleteLater()
        self.app.processEvents()

    def test_prepare_all_storyboard_assets_deduplicates_shared_assets(self):
        workbench = ScriptWorkbench()
        workbench._storyboard = normalize_storyboard({
            "title": "全片准备测试",
            "shots": [
                {"scene": "同一客厅", "scene_name": "共享客厅", "duration": 3},
                {"scene": "仍在客厅", "scene_name": "共享客厅", "duration": 3},
            ],
        }, 6)
        workbench._resource_db = object()
        shared = SimpleNamespace(
            id="scene_shared", name="共享客厅", reference_images=[])
        requirement = {
            "kind": "scene", "asset_id": "scene_shared", "name": "共享客厅",
        }
        opened = []
        with (
                patch.object(workbench, "_shot_asset_requirements",
                             side_effect=lambda _shot: [dict(requirement)]),
                patch.object(workbench, "_find_requirement_asset",
                             return_value=shared),
                patch.object(workbench, "_bind_requirement_to_shot") as bind,
                patch.object(workbench, "refresh_resource_links"),
                patch.object(workbench, "_open_asset_preparation_dialog",
                             side_effect=lambda **kwargs: opened.append(kwargs)),
                patch("ui.script_workbench.asset_is_approved", return_value=False)):
            workbench._prepare_all_storyboard_assets()

        self.assertEqual(2, bind.call_count)
        self.assertEqual(1, len(opened))
        self.assertEqual("全部镜头", opened[0]["context_title"])
        self.assertEqual(1, len(opened[0]["entries"]))
        workbench.deleteLater()
        self.app.processEvents()

    def test_candidate_double_click_opens_preview_signal(self):
        with tempfile.TemporaryDirectory() as temp:
            image_path = Path(temp) / "preview.png"
            self._image(image_path, "green")
            shot = {
                "id": "shot_preview", "number": 1, "start": 0, "duration": 4,
                "assets": [{"path": str(image_path), "kind": "image"}],
                "preview_asset": str(image_path),
                "selected_asset": str(image_path),
                "selected_image_asset": str(image_path),
                "selected_video_asset": "",
            }
            panel = StoryboardProductionPanel()
            panel.set_shot(shot)
            panel.show()
            self.app.processEvents()
            opened = []
            panel.preview_requested.connect(
                lambda path, kind: opened.append((path, kind)))
            candidate = next(
                button for button in panel.findChildren(__import__(
                    "ui.script_workbench", fromlist=["_DoubleClickToolButton"]
                )._DoubleClickToolButton)
                if button.toolTip() == str(image_path))
            QTest.mouseDClick(candidate, Qt.MouseButton.LeftButton)
            self.app.processEvents()
            self.assertEqual([(str(image_path), "image")], opened)
            button_texts = [button.text() for button in
                            panel.findChildren(QPushButton)]
            self.assertNotIn("查看大图 / 播放视频", button_texts)
            panel.close()
            panel.deleteLater()
            self.app.processEvents()

    def test_new_media_preview_closes_previous_dialog(self):
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "first.png"
            second = Path(temp) / "second.png"
            self._image(first, "red")
            self._image(second, "blue")
            first_dialog = open_single_media_preview(str(first), "image")
            self.app.processEvents()
            self.assertTrue(first_dialog.isVisible())
            second_dialog = open_single_media_preview(str(second), "image")
            self.assertFalse(first_dialog.isVisible())
            self.assertTrue(second_dialog.isVisible())
            second_dialog.close()
            self.app.processEvents()

    def test_canvas_inspector_preview_uses_double_click_without_view_button(self):
        with tempfile.TemporaryDirectory() as temp:
            image_path = Path(temp) / "canvas.png"
            self._image(image_path, "yellow")

            class Owner:
                def __init__(self):
                    self.opened = []

                def open_media_preview(self, path, kind):
                    self.opened.append((path, kind))

                def adopt_shot_take(self, _node):
                    pass

                def request_result_video(self, _node):
                    pass

                def request_result_refine(self, _node):
                    pass

                def save_result_to_library(self, _node):
                    pass

                def remove_shot_result(self, _node):
                    pass

                def keep_only_shot_result(self, _node):
                    pass

            owner = Owner()
            inspector = CanvasContextInspector(owner)
            node = type("Node", (), {
                "title": "图片版本 1", "subtitle": "canvas.png",
                "payload": {"path": str(image_path), "kind": "image"},
            })()
            inspector.show_shot_take(node)
            inspector.show()
            self.app.processEvents()
            preview = inspector.findChild(_InspectorPreviewLabel)
            self.assertIsNotNone(preview)
            QTest.mouseDClick(preview, Qt.MouseButton.LeftButton)
            self.app.processEvents()
            self.assertEqual([(str(image_path), "image")], owner.opened)
            button_texts = [button.text() for button in
                            inspector.findChildren(QPushButton)]
            self.assertNotIn("查看大图", button_texts)
            inspector.close()
            inspector.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
