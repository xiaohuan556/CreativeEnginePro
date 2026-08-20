#!/usr/bin/env python3
"""Production lifecycle helpers for the self-hosted Creative Engine stack.

This host-side tool deliberately uses Docker Compose instead of opening the
PostgreSQL or media volumes to the public network.  Backups are taken while
the API and workers are quiesced so database rows and media files describe
the same point in time.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


DEPLOY_DIR = Path(__file__).resolve().parent
COMPOSE_FILE = DEPLOY_DIR / "compose.yml"
DEFAULT_ENV_FILE = DEPLOY_DIR / ".env.production"
BACKUP_FORMAT = "creative-engine-backup-v1"
REQUIRED_ENV = ("STUDIO_DOMAIN", "POSTGRES_PASSWORD")
PLACEHOLDERS = ("example.com", "replace-with", "change-me", "changeme")


class DeploymentError(RuntimeError):
    pass


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        raise DeploymentError(f"环境文件不存在：{path}")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def validate_env(values: dict[str, str]) -> None:
    missing = [key for key in REQUIRED_ENV if not values.get(key)]
    if missing:
        raise DeploymentError("生产环境变量缺失：" + ", ".join(missing))
    unsafe = [key for key in REQUIRED_ENV if any(token in values[key].lower() for token in PLACEHOLDERS)]
    if unsafe:
        raise DeploymentError("仍在使用示例值：" + ", ".join(unsafe))
    if len(values["POSTGRES_PASSWORD"]) < 24:
        raise DeploymentError("POSTGRES_PASSWORD 至少需要 24 个字符")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def compose_prefix(env_file: Path) -> list[str]:
    return ["docker", "compose", "--env-file", str(env_file), "-f", str(COMPOSE_FILE)]


def run(command: list[str], *, stdin=None, stdout=None, check: bool = True) -> subprocess.CompletedProcess:
    printable = " ".join(command[:2] + (["…"] if len(command) > 2 else []))
    print(f"→ {printable}")
    return subprocess.run(command, stdin=stdin, stdout=stdout, check=check)


def wait_ready(compose: list[str], attempts: int = 30) -> None:
    probe = [
        *compose, "exec", "-T", "api", "python", "-c",
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3).read()",
    ]
    for _ in range(attempts):
        if run(probe, check=False).returncode == 0:
            return
        time.sleep(2)
    raise DeploymentError("API 在恢复服务后仍未通过 /ready 检查")


def validate_media_archive(path: Path) -> None:
    try:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive.getmembers():
                name = PurePosixPath(member.name)
                if name.is_absolute() or ".." in name.parts:
                    raise DeploymentError(f"媒体备份包含越界路径：{member.name}")
                if member.issym() or member.islnk() or member.isdev():
                    raise DeploymentError(f"媒体备份包含不允许的链接或设备：{member.name}")
    except (tarfile.TarError, OSError) as error:
        raise DeploymentError(f"媒体备份无法读取：{error}") from error


def preflight(env_file: Path) -> None:
    validate_env(read_env(env_file))
    if not shutil.which("docker"):
        raise DeploymentError("没有找到 Docker；请先安装 Docker Engine 与 Compose 插件")
    compose = compose_prefix(env_file)
    run(["docker", "version"])
    run(["docker", "compose", "version"])
    run([*compose, "config", "--quiet"])
    print("生产部署预检通过：环境变量、Docker 与 Compose 配置均有效。")


def backup(env_file: Path, backup_root: Path, *, resume_services: bool = True) -> Path:
    validate_env(read_env(env_file))
    compose = compose_prefix(env_file)
    backup_root = backup_root.expanduser().resolve()
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_dir = backup_root / f"creative-engine-{stamp}"
    if final_dir.exists():
        raise DeploymentError(f"备份目录已存在：{final_dir}")

    run([*compose, "up", "-d", "postgres"])
    stopped = False
    try:
        run([*compose, "stop", "api", "worker"])
        stopped = True
        with tempfile.TemporaryDirectory(prefix=".cep-backup-", dir=backup_root) as temp_name:
            temp_dir = Path(temp_name)
            database = temp_dir / "database.dump"
            media = temp_dir / "media.tar.gz"
            with database.open("wb") as output:
                run([*compose, "exec", "-T", "postgres", "pg_dump", "-U", "creative_engine", "-d", "creative_engine", "-Fc"], stdout=output)
            with media.open("wb") as output:
                run([*compose, "run", "--rm", "--no-deps", "-T", "api", "tar", "-C", "/var/lib/creative-engine/media", "-czf", "-", "."], stdout=output)
            validate_media_archive(media)
            manifest = {
                "format": BACKUP_FORMAT,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "files": {
                    "database.dump": {"sha256": sha256(database), "size": database.stat().st_size},
                    "media.tar.gz": {"sha256": sha256(media), "size": media.stat().st_size},
                },
            }
            (temp_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            Path(temp_name).replace(final_dir)
    finally:
        if stopped and resume_services:
            run([*compose, "up", "-d", "api", "worker"])
            wait_ready(compose)
    print(f"一致性备份完成：{final_dir}")
    return final_dir


def verify_backup(path: Path) -> dict:
    path = path.expanduser().resolve()
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        raise DeploymentError("备份缺少 manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DeploymentError(f"备份清单无效：{error}") from error
    if manifest.get("format") != BACKUP_FORMAT:
        raise DeploymentError("不支持的备份格式")
    for name in ("database.dump", "media.tar.gz"):
        target = path / name
        expected = str(manifest.get("files", {}).get(name, {}).get("sha256", ""))
        if not target.is_file() or not expected or sha256(target) != expected:
            raise DeploymentError(f"备份文件缺失或校验失败：{name}")
    validate_media_archive(path / "media.tar.gz")
    return manifest


def restore(env_file: Path, backup_dir: Path, backup_root: Path, confirmation: str) -> None:
    validate_env(read_env(env_file))
    backup_dir = backup_dir.expanduser().resolve()
    verify_backup(backup_dir)
    if confirmation != backup_dir.name:
        raise DeploymentError(f"恢复会覆盖当前数据库和媒体；请传入 --confirm {backup_dir.name}")

    print("恢复前先为当前线上数据创建安全备份。")
    backup(env_file, backup_root)
    compose = compose_prefix(env_file)
    run([*compose, "stop", "api", "worker"])
    restored = False
    try:
        with (backup_dir / "database.dump").open("rb") as source:
            run([*compose, "exec", "-T", "postgres", "pg_restore", "-U", "creative_engine", "-d", "creative_engine", "--clean", "--if-exists", "--no-owner", "--no-privileges"], stdin=source)
        with (backup_dir / "media.tar.gz").open("rb") as source:
            run([
                *compose, "run", "--rm", "--no-deps", "-T", "api", "sh", "-ceu",
                "find /var/lib/creative-engine/media -mindepth 1 -depth -delete; tar -xzf - -C /var/lib/creative-engine/media",
            ], stdin=source)
        restored = True
    finally:
        if restored:
            run([*compose, "up", "-d", "api", "worker"])
            wait_ready(compose)
        else:
            print("恢复没有完整结束；为防止暴露半恢复数据，API 和 Worker 保持停止。请使用刚创建的恢复前备份排查或回滚。", file=sys.stderr)
    print(f"恢复完成并通过就绪检查：{backup_dir}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Creative Engine 公司服务器部署管理")
    result.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    result.add_argument("--backup-root", type=Path, default=DEPLOY_DIR / "backups")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("preflight", help="检查生产环境变量、Docker 和 Compose")
    commands.add_parser("backup", help="创建数据库与媒体一致性备份")
    verify = commands.add_parser("verify", help="离线校验一个备份")
    verify.add_argument("backup_dir", type=Path)
    restore_command = commands.add_parser("restore", help="校验、预备份并恢复数据库和媒体")
    restore_command.add_argument("backup_dir", type=Path)
    restore_command.add_argument("--confirm", required=True, help="必须精确填写备份目录名")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "preflight":
            preflight(args.env_file)
        elif args.command == "backup":
            backup(args.env_file, args.backup_root)
        elif args.command == "verify":
            manifest = verify_backup(args.backup_dir)
            print(f"备份校验通过：{manifest['created_at']}")
        elif args.command == "restore":
            restore(args.env_file, args.backup_dir, args.backup_root, args.confirm)
    except (DeploymentError, subprocess.CalledProcessError) as error:
        print(f"操作失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
