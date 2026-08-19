import unittest
from unittest.mock import patch

try:
    from ai.assets import Character, Element, Scene
    from ui.script_workbench import ScriptWorkbench
    UI_AVAILABLE = True
except ModuleNotFoundError:
    UI_AVAILABLE = False


class _AssetDB:
    def __init__(self):
        self.character = Character(id="real_character", name="真实主体")
        self.scene = Scene(id="real_scene", name="真实场景")
        self.element = Element(id="real_element", name="真实元素")

    def list_characters(self, limit=5000):
        return [self.character]

    def list_scenes(self, limit=5000):
        return [self.scene]

    def list_elements(self, limit=5000):
        return [self.element]

    def get_character(self, item_id):
        return self.character if item_id == self.character.id else None

    def get_scene(self, item_id):
        return self.scene if item_id == self.scene.id else None

    def get_element(self, item_id):
        return self.element if item_id == self.element.id else None


@unittest.skipUnless(UI_AVAILABLE, "script workbench dependencies are unavailable")
class StoryboardBindingRepairTests(unittest.TestCase):
    def test_shot_requirements_recognize_and_deduplicate_assets(self):
        db = _AssetDB()
        board = {"characters": [{
            "asset_id": db.character.id,
            "name": db.character.name,
            "description": "固定白色机器人外形",
        }], "asset_inventory": {"missing": [{
            "name": "旧录音机", "kind": "element", "description": "银色机身",
        }]}}
        owner = type("Owner", (), {
            "_storyboard": board,
            "_resource_db": db,
            "_split_asset_names": staticmethod(ScriptWorkbench._split_asset_names),
            "_find_requirement_asset": ScriptWorkbench._find_requirement_asset,
        })()
        shot = {
            "scene": "真实场景中，真实主体拿起旧录音机",
            "scene_name": db.scene.name,
            "scene_asset_id": db.scene.id,
            "character": db.character.name,
            "character_names": [db.character.name],
            "character_bindings": [{
                "asset_id": db.character.id, "name": db.character.name,
            }],
            "element_names": ["旧录音机"],
            "element_bindings": [],
        }
        requirements = ScriptWorkbench._shot_asset_requirements(owner, shot)
        self.assertEqual(3, len(requirements))
        self.assertEqual(
            {"scene", "character", "element"},
            {item["kind"] for item in requirements})
        element = next(item for item in requirements if item["kind"] == "element")
        self.assertEqual("银色机身", element["description"])

    def test_approved_asset_version_is_synced_to_connected_shots(self):
        db = _AssetDB()
        db.character.version = 4
        board = {"shots": [{
            "id": "shot_1",
            "character_id": db.character.id,
            "character_bindings": [{
                "asset_id": db.character.id, "name": "旧名称", "version": 0,
            }],
        }]}
        owner = type("Owner", (), {
            "_resource_db": db,
            "_storyboard": board,
            "_split_asset_names": staticmethod(ScriptWorkbench._split_asset_names),
        })()
        changed = ScriptWorkbench._sync_prepared_asset_bindings(
            owner, "character", db.character.id)
        binding = board["shots"][0]["character_bindings"][0]
        self.assertEqual(["shot_1"], changed)
        self.assertEqual(db.character.name, binding["name"])
        self.assertEqual(4, binding["version"])
        self.assertEqual([db.character.name], board["shots"][0]["character_names"])

    def test_director_unknown_ids_are_not_restored_by_legacy_sync(self):
        board = {"shots": [{
            "id": "shot_1", "scene": "测试画面", "character": "不存在的主体",
            "scene_asset_id": "fake_scene", "scene_id": "fake_scene",
            "character_id": "fake_character", "character_ids": [],
            "character_bindings": [{"asset_id": "fake_character"}],
            "element_id": "fake_element", "element_ids": [],
            "element_bindings": [{"asset_id": "fake_element"}],
        }]}
        db = _AssetDB()
        owner = object()
        with patch("ai.service.get_asset_db", return_value=db):
            ScriptWorkbench._auto_bind_assets_to_storyboard(owner, board)
        shot = board["shots"][0]
        self.assertEqual("", shot["scene_asset_id"])
        self.assertEqual([], shot["character_bindings"])
        self.assertEqual("", shot["character_id"])
        self.assertEqual([], shot["element_bindings"])
        self.assertEqual("", shot["element_id"])

    def test_real_canvas_links_remove_stale_placeholder_ids(self):
        db = _AssetDB()
        owner = type("Owner", (), {"_resource_db": db})()
        shot = {
            "character_id": "fake_character",
            "character_ids": ["real_character"],
            "character_bindings": [
                {"asset_id": "fake_character"},
                {"asset_id": "real_character"},
            ],
            "element_id": "fake_element",
            "element_ids": ["real_element"],
            "element_bindings": [
                {"asset_id": "fake_element"},
                {"asset_id": "real_element"},
            ],
        }
        repaired = ScriptWorkbench._repair_stale_shot_bindings(owner, shot)
        self.assertTrue(repaired)
        self.assertEqual("real_character", shot["character_id"])
        self.assertEqual(
            ["real_character"],
            [item["asset_id"] for item in shot["character_bindings"]])
        self.assertEqual("real_element", shot["element_id"])
        self.assertEqual(
            ["real_element"],
            [item["asset_id"] for item in shot["element_bindings"]])

    def test_paid_generation_is_blocked_when_named_contract_asset_is_unbound(self):
        db = _AssetDB()
        owner = type("Owner", (), {
            "_resource_db": db,
            "_storyboard": {"visual_bible": {}},
            "_split_asset_names": staticmethod(ScriptWorkbench._split_asset_names),
            "_asset_reference_paths": staticmethod(lambda _item: ["approved.png"]),
        })()
        shot = {
            "scene_asset_id": db.scene.id,
            "scene_name": db.scene.name,
            "character_names": [db.character.name],
            "character_bindings": [{"asset_id": db.character.id}],
            "element_names": ["必须出现的红色手机"],
            "element_bindings": [],
        }
        problems = ScriptWorkbench._shot_readiness_problems(owner, shot)
        self.assertIn("未绑定指定元素：必须出现的红色手机", problems)


if __name__ == "__main__":
    unittest.main()
