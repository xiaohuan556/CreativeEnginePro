import unittest

from ai.scene_contracts import consolidate_scene_specs, scene_location_key


class SceneContractTest(unittest.TestCase):
    def test_same_interior_states_share_one_master(self):
        scenes = [
            {"name":"冷白灯洗衣店内", "description":"内景，正常冷白顶灯"},
            {"name":"橙色应急灯洗衣店内", "description":"内景，停电后应急灯"},
            {"name":"雨停前的洗衣店门边", "description":"内景，门边，雨势减弱"},
            {"name":"雨夜街角洗衣店外", "description":"外景，大雨，店内冷白灯透过玻璃"},
        ]
        masters, aliases = consolidate_scene_specs(scenes)
        self.assertEqual(2, len(masters))
        interior = next(value for value in masters
                        if value["location_id"].endswith(":interior"))
        self.assertEqual("冷白灯洗衣店内", interior["name"])
        self.assertEqual(3, len(interior["scene_states"]))
        self.assertEqual("冷白灯洗衣店内",
                         aliases["橙色应急灯洗衣店内"]["master_name"])

    def test_explicit_location_id_wins(self):
        self.assertEqual("laundry_main", scene_location_key({
            "name":"任意名称", "location_id":"LAUNDRY_MAIN"}))

    def test_new_contract_preserves_declared_states(self):
        masters, _ = consolidate_scene_specs([{
            "name":"洗衣店室内", "location_id":"laundry_interior",
            "states":[{"name":"正常灯光"}, {"name":"停电应急灯"}],
        }])
        self.assertEqual(["正常灯光", "停电应急灯"],
                         [value["name"] for value in masters[0]["scene_states"]])


if __name__ == "__main__":
    unittest.main()
