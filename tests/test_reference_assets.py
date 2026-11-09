import tempfile
import unittest
from pathlib import Path

from ai.reference_assets import (
    append_manifest, normalize_reference_assets, reference_paths,
)


class ReferenceAssetTests(unittest.TestCase):
    def test_roles_survive_ordering_and_duplicate_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            scene = Path(temp) / "scene.png"
            actor = Path(temp) / "actor.png"
            scene.write_bytes(b"scene")
            actor.write_bytes(b"actor")
            values = [
                {"path": str(actor), "role": "character", "asset_id": "c1",
                 "priority": 20, "label": "actor", "version": 2},
                {"path": str(scene), "role": "scene", "asset_id": "s1",
                 "priority": 10, "label": "room", "version": 3},
                {"path": str(actor), "role": "element", "asset_id": "wrong"},
            ]
            normalized = normalize_reference_assets(values)

            self.assertEqual([str(scene), str(actor)], reference_paths(normalized))
            self.assertEqual("scene", normalized[0]["role"])
            self.assertEqual("character", normalized[1]["role"])
            prompt = append_manifest("Make a shot", normalized)
            self.assertIn("资产ID=s1", prompt)
            self.assertIn("资产ID=c1", prompt)

    def test_trusted_asset_uri_is_preserved(self):
        values = normalize_reference_assets([{
            "path": "asset://trusted-character", "role": "character"}])
        self.assertEqual("asset://trusted-character", values[0]["path"])

    def test_explicit_zero_weight_priority_and_order_are_preserved(self):
        values = normalize_reference_assets([
            {"path": "asset://a", "role": "reference", "priority": 10, "order": 1},
            {"path": "asset://b", "role": "reference", "priority": 0, "order": 0, "weight": 0},
        ])
        self.assertEqual("asset://b", values[0]["path"])
        self.assertEqual(0, values[0]["weight"])
        self.assertEqual(0, values[0]["order"])
        self.assertEqual(values, normalize_reference_assets(values))


if __name__ == "__main__":
    unittest.main()
