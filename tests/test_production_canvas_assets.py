import tempfile
import unittest
from pathlib import Path

from ai.assets import Character, approve_asset_version, assign_asset_view
from ai.ui.production_canvas import _asset_visual_entries


class ProductionCanvasAssetNodeTests(unittest.TestCase):
    def test_master_is_not_repeated_as_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            master = Path(temp) / "master.png"
            side = Path(temp) / "side.png"
            candidate = Path(temp) / "candidate.png"
            for path in (master, side, candidate):
                path.touch()

            item = Character(id="robot", name="机器人", reference_images=[
                str(master), str(side), str(candidate)])
            approve_asset_version(item, str(master), "test")
            assign_asset_view(item, "front", str(master))
            assign_asset_view(item, "side", str(side))

            thumbnail, entries = _asset_visual_entries(item)

            self.assertEqual(str(master.resolve()), thumbnail)
            self.assertNotIn(str(master.resolve()), [entry["path"] for entry in entries])
            self.assertEqual(
                [(entry["type"], entry["roles"]) for entry in entries],
                [("fixed_view", ["side"]), ("candidate", [])],
            )


if __name__ == "__main__":
    unittest.main()
