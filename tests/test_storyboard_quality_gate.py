import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtGui import QColor, QImage
    from PyQt6.QtWidgets import QApplication
    from ai.assets import Character, Scene
    from ai.storyboard import normalize_storyboard
    from ui.script_workbench import ScriptWorkbench
    QT_AVAILABLE = True
except ModuleNotFoundError:
    QT_AVAILABLE = False


class _AssetDB:
    def __init__(self, scene=None, character=None):
        self.scene = scene
        self.character = character

    def get_scene(self, item_id):
        return self.scene if self.scene and self.scene.id == item_id else None

    def get_character(self, item_id):
        return (self.character
                if self.character and self.character.id == item_id else None)

    def get_element(self, _item_id):
        return None


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 runtime is not available")
class StoryboardQualityGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _image(path: Path, color="green"):
        image = QImage(64, 64, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        assert image.save(str(path))

    def test_generated_candidate_does_not_become_final_implicitly(self):
        with tempfile.TemporaryDirectory() as temp:
            candidate = Path(temp) / "candidate.png"
            self._image(candidate)
            shot = {
                "id": "shot_candidate", "number": 1, "assets": [],
                "selected_image_asset": "", "anchor_frame_id": "",
            }
            workbench = ScriptWorkbench()
            workbench._storyboard = {"shots": [shot], "visual_bible": {}}
            workbench.attach_generated_asset(
                shot["id"], str(candidate), "image",
                metadata={"candidate_only": True})

            self.assertEqual(str(candidate), shot["preview_asset"])
            self.assertEqual("", shot["selected_image_asset"])
            self.assertEqual("", shot["anchor_frame_id"])
            self.assertEqual(
                "", ScriptWorkbench._selected_image_path(shot))
            workbench.close()
            workbench.deleteLater()
            self.app.processEvents()

    def test_manual_finalization_releases_waiting_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            candidate = Path(temp) / "candidate.png"
            self._image(candidate)
            shot = {
                "id": "shot_waiting", "number": 1,
                "assets": [{"path": str(candidate), "kind": "image",
                            "candidate_only": True}],
                "selected_image_asset": "", "anchor_frame_id": "",
            }
            workbench = ScriptWorkbench()
            workbench._storyboard = {"shots": [shot], "visual_bible": {}}
            workbench._storyboard_batch_active = True
            workbench._storyboard_batch_waiting_shot_id = shot["id"]
            workbench._storyboard_batch_queue = []
            callbacks = []
            with patch(
                    "ui.script_workbench.QTimer.singleShot",
                    side_effect=lambda _delay, callback: callbacks.append(callback)):
                workbench._approve_storyboard_asset(shot["id"], str(candidate))

            self.assertEqual(str(candidate), shot["selected_image_asset"])
            self.assertEqual("", workbench._storyboard_batch_waiting_shot_id)
            self.assertEqual(1, len(callbacks))
            workbench._storyboard_batch_active = False
            workbench.close()
            workbench.deleteLater()
            self.app.processEvents()

    def test_exact_element_must_be_applied_before_video(self):
        workbench = ScriptWorkbench()
        workbench._resource_db = None
        shot = {
            "element_id": "phone_ui", "element_ids": [],
            "element_mode": "exact",
            "element_bindings": [{"asset_id": "phone_ui", "mode": "exact"}],
        }
        self.assertEqual(
            ["phone_ui"], workbench._missing_exact_element_ids(shot, {}))
        self.assertEqual([], workbench._missing_exact_element_ids(
            shot, {"exact_elements_applied": ["phone_ui"]}))
        workbench.close()
        workbench.deleteLater()
        self.app.processEvents()

    def test_shot_generation_uses_one_scene_and_one_character_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            scene_master = folder / "scene_master.png"
            scene_camera = folder / "scene_camera.png"
            character_master = folder / "character_master.png"
            character_side = folder / "character_side.png"
            for index, path in enumerate((scene_master, scene_camera,
                                          character_master, character_side)):
                self._image(path, ("red", "blue", "green", "yellow")[index])
            scene = Scene(
                id="scene_1", name="客厅", description="固定客厅",
                approved_reference=str(scene_master), approval_status="approved",
                version=1, reference_views={"camera_a": str(scene_camera)})
            character = Character(
                id="character_1", name="女孩", description="固定女孩",
                approved_reference=str(character_master), approval_status="approved",
                version=1, reference_views={"side": str(character_side)})
            workbench = ScriptWorkbench()
            workbench._resource_db = _AssetDB(scene, character)
            workbench._storyboard = {"visual_bible": {}, "shots": []}
            shot = {
                "id": "shot_refs", "scene_asset_id": scene.id,
                "character_id": character.id, "character_ids": [],
                "camera_slot": "A", "shot_size": "side view medium shot",
                "element_bindings": [],
            }
            _prompt, refs, report = workbench._apply_visual_lock(
                shot, "cinematic shot")

            self.assertEqual([str(scene_camera), str(character_side)], refs)
            self.assertEqual(2, len(report["entries"]))
            self.assertEqual(
                ["scene", "character"],
                [item["role"] for item in report["entries"]])
            workbench.close()
            workbench.deleteLater()
            self.app.processEvents()

    def test_dialogue_is_added_to_video_prompt_and_tts_text(self):
        shot = {
            "voiceover": "Robot: Don't touch that button!",
            "sound": "quiet room tone",
        }
        prompt = ScriptWorkbench._video_prompt_with_dialogue(
            shot, "The camera slowly pushes in.")
        self.assertIn("Don't touch that button!", prompt)
        self.assertIn("visible lip synchronization", prompt)
        self.assertIn("quiet room tone", prompt)
        self.assertEqual(
            "Don't touch that button!",
            ScriptWorkbench._dialogue_text_for_tts(shot))

    def test_dialogue_video_waits_for_audio_before_submit(self):
        workbench = ScriptWorkbench()
        board = normalize_storyboard({
            "shots": [{
                "scene": "机器人说话", "duration": 4,
                "performance": {
                    "line_type": "dialogue", "speaker": "机器人",
                    "dialogue": "不要碰那个按钮。",
                },
            }],
        })
        workbench._storyboard = board
        shot = board["shots"][0]
        with (
                patch.object(workbench, "_submit_dialogue_audio_task",
                             return_value=True) as audio_submit,
                patch.object(workbench, "_submit_asset_task") as video_submit):
            workbench._submit_video_with_audio_first(
                shot, "robot speaks", reference_image="frame.png")
        audio_submit.assert_called_once()
        video_submit.assert_not_called()
        self.assertIn(shot["id"], workbench._pending_video_requests)
        workbench.close()
        workbench.deleteLater()
        self.app.processEvents()

    def test_finished_audio_reflows_timeline_before_video_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            audio_path = Path(temp) / "dialogue.mp3"
            audio_path.write_bytes(b"test")
            workbench = ScriptWorkbench()
            board = normalize_storyboard({
                "title": "对白先行",
                "shots": [
                    {
                        "scene": "角色说话", "duration": 4,
                        "performance": {
                            "line_type": "dialogue", "dialogue": "你好。",
                            "pause_before": 0.2, "pause_after": 0.3,
                        },
                    },
                    {"scene": "听者反应", "duration": 4},
                ],
            }, 8)
            workbench._storyboard = board
            shot = board["shots"][0]
            workbench._pending_video_requests[shot["id"]] = {
                "prompt": "speak", "reference_image": "frame.png",
            }
            handle = SimpleNamespace(
                is_success=True,
                result=SimpleNamespace(data=str(audio_path), error=""),
            )
            callbacks = []
            with (
                    patch("ui.media_library._get_duration", return_value=2.5),
                    patch("ui.script_workbench.QTimer.singleShot",
                          side_effect=lambda _delay, callback: callbacks.append(callback))):
                workbench._finish_asset_task({
                    "handle": handle, "shot_id": shot["id"],
                    "kind": "dialogue_audio", "provider": "edge_tts",
                    "reason": "audio first",
                })

            self.assertEqual("ready", shot["dialogue_audio_status"])
            self.assertEqual(3.0, shot["duration"])
            self.assertEqual(3.0, board["shots"][1]["start"])
            self.assertEqual(7.0, board["duration"])
            self.assertEqual(1, len(callbacks))
            workbench.close()
            workbench.deleteLater()
            self.app.processEvents()

    def test_stale_dialogue_audio_is_not_attached_after_text_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            audio_path = Path(temp) / "stale.mp3"
            audio_path.write_bytes(b"test")
            workbench = ScriptWorkbench()
            board = normalize_storyboard({
                "shots": [{
                    "scene": "角色说话", "duration": 4,
                    "performance": {
                        "line_type": "dialogue", "dialogue": "新的对白",
                    },
                }],
            })
            workbench._storyboard = board
            shot = board["shots"][0]
            workbench._pending_video_requests[shot["id"]] = {
                "prompt": "speak", "reference_image": "frame.png",
            }
            callbacks = []
            with patch(
                    "ui.script_workbench.QTimer.singleShot",
                    side_effect=lambda _delay, callback: callbacks.append(callback)):
                workbench._finish_asset_task({
                    "handle": SimpleNamespace(
                        is_success=True,
                        result=SimpleNamespace(data=str(audio_path), error="")),
                    "shot_id": shot["id"], "kind": "dialogue_audio",
                    "provider": "edge_tts", "source_text": "旧的对白",
                })
            self.assertEqual("", shot.get("dialogue_audio", ""))
            self.assertEqual("", shot.get("dialogue_audio_status", ""))
            self.assertEqual(1, len(callbacks))
            workbench.close()
            workbench.deleteLater()
            self.app.processEvents()

    def test_voiceover_does_not_request_visible_lip_sync(self):
        shot = {
            "performance": {
                "line_type": "voiceover", "dialogue": "雨一直没有停。",
            },
        }
        prompt = ScriptWorkbench._video_prompt_with_dialogue(
            shot, "Rain falls outside the window.")
        self.assertIn("Voice-over narration", prompt)
        self.assertNotIn("visible lip synchronization", prompt)


if __name__ == "__main__":
    unittest.main()
