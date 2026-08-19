import tempfile
import unittest
from pathlib import Path
import sqlite3

from ai.assets.db import (
    AssetDB, Character, Scene, approve_asset_version, approved_asset_path,
    asset_is_approved, assign_asset_view,
)


class AssetApprovalTests(unittest.TestCase):
    def test_legacy_reference_is_migrated_to_approved_v1(self):
        item = Character.from_dict({
            "id": "legacy",
            "name": "旧角色",
            "reference_images": ["C:/legacy/front.png"],
        })
        self.assertEqual(item.approval_status, "approved")
        self.assertEqual(item.version, 1)
        self.assertEqual(approved_asset_path(item), "C:/legacy/front.png")

    def test_new_reference_stays_draft_until_explicit_approval(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "candidate.png"
            image.write_bytes(b"image")
            db = AssetDB(Path(temp_dir) / "assets.db")
            item = Character(id="new", name="新角色",
                             reference_images=[str(image)])
            db.save_character(item)
            loaded = db.get_character("new")
            self.assertEqual(loaded.approval_status, "draft")
            self.assertFalse(asset_is_approved(loaded))
            db.close()

    def test_approval_creates_versions_only_when_master_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "v1.png"
            second = Path(temp_dir) / "v2.png"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            item = Character(id="character", name="角色")
            self.assertTrue(approve_asset_version(item, str(first), "test"))
            self.assertEqual(item.version, 1)
            self.assertFalse(approve_asset_version(item, str(first), "test"))
            self.assertEqual(item.version, 1)
            self.assertTrue(approve_asset_version(item, str(second), "test"))
            self.assertEqual(item.version, 2)
            self.assertEqual(len(item.version_history), 2)
            self.assertEqual(approved_asset_path(item), str(second.resolve()))

    def test_scene_view_does_not_replace_approved_master(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            master = Path(temp_dir) / "master.png"
            camera_a = Path(temp_dir) / "camera_a.png"
            master.write_bytes(b"master")
            camera_a.write_bytes(b"camera")
            scene = Scene(id="scene", name="客厅")
            approve_asset_version(scene, str(master), "test")
            assign_asset_view(scene, "camera_a", str(camera_a))
            self.assertEqual(scene.reference_views["camera_a"], str(camera_a.resolve()))
            self.assertEqual(approved_asset_path(scene), str(master.resolve()))
            self.assertEqual(scene.version, 1)

    def test_transient_disk_io_read_reconnects_without_losing_assets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = AssetDB(Path(temp_dir) / "assets.db")
            scene = Scene(name="可恢复场景")
            db.save_scene(scene)
            original = db.conn

            class FailOneRead:
                def __init__(self, connection):
                    self.connection = connection
                    self.failed = False

                def execute(self, statement, params=()):
                    if not self.failed and statement.lstrip().upper().startswith("SELECT"):
                        self.failed = True
                        raise sqlite3.OperationalError("disk I/O error")
                    return self.connection.execute(statement, params)

                def close(self):
                    self.connection.close()

            db._conn = FailOneRead(original)
            rows = db.list_scenes()
            self.assertEqual(["可恢复场景"], [item.name for item in rows])
            self.assertIsNot(db.conn, original)
            db.close()


if __name__ == "__main__":
    unittest.main()
