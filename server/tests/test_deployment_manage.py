from __future__ import annotations

import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[2] / "deploy" / "manage.py"
SPEC = importlib.util.spec_from_file_location("creative_engine_deploy_manage", MODULE_PATH)
assert SPEC and SPEC.loader
manage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manage)


def write_media_archive(path: Path, name: str = "project/asset.png") -> None:
    payload = b"safe-media"
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo(name); info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))


def test_production_env_rejects_examples_and_short_password(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.production"
    env_file.write_text("STUDIO_DOMAIN=studio.example.com\nPOSTGRES_PASSWORD=short\n", encoding="utf-8")
    with pytest.raises(manage.DeploymentError):
        manage.validate_env(manage.read_env(env_file))
    env_file.write_text("STUDIO_DOMAIN=studio.company.cn\nPOSTGRES_PASSWORD=0123456789abcdef0123456789abcdef\n", encoding="utf-8")
    manage.validate_env(manage.read_env(env_file))


def test_backup_verification_detects_tampering_and_archive_escape(tmp_path: Path) -> None:
    backup = tmp_path / "creative-engine-test"; backup.mkdir()
    database = backup / "database.dump"; database.write_bytes(b"database")
    media = backup / "media.tar.gz"; write_media_archive(media)
    manifest = {
        "format": manage.BACKUP_FORMAT,
        "created_at": "2026-08-20T00:00:00+00:00",
        "files": {
            "database.dump": {"sha256": manage.sha256(database), "size": database.stat().st_size},
            "media.tar.gz": {"sha256": manage.sha256(media), "size": media.stat().st_size},
        },
    }
    (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert manage.verify_backup(backup)["format"] == manage.BACKUP_FORMAT
    database.write_bytes(b"tampered")
    with pytest.raises(manage.DeploymentError, match="校验失败"):
        manage.verify_backup(backup)

    unsafe = tmp_path / "unsafe.tar.gz"; write_media_archive(unsafe, "../../escape")
    with pytest.raises(manage.DeploymentError, match="越界路径"):
        manage.validate_media_archive(unsafe)


def test_restore_requires_exact_backup_name_before_running_docker(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env.production"
    env_file.write_text("STUDIO_DOMAIN=studio.company.cn\nPOSTGRES_PASSWORD=0123456789abcdef0123456789abcdef\n", encoding="utf-8")
    backup = tmp_path / "creative-engine-confirm-me"; backup.mkdir()
    database = backup / "database.dump"; database.write_bytes(b"database")
    media = backup / "media.tar.gz"; write_media_archive(media)
    manifest = {
        "format": manage.BACKUP_FORMAT,
        "created_at": "2026-08-20T00:00:00+00:00",
        "files": {
            "database.dump": {"sha256": manage.sha256(database), "size": database.stat().st_size},
            "media.tar.gz": {"sha256": manage.sha256(media), "size": media.stat().st_size},
        },
    }
    (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(manage, "run", lambda *args, **kwargs: pytest.fail("确认前不应执行 Docker"))
    with pytest.raises(manage.DeploymentError, match="--confirm creative-engine-confirm-me"):
        manage.restore(env_file, backup, tmp_path / "backups", "wrong-name")
