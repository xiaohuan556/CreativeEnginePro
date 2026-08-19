from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from .config import get_settings


ALLOWED_PREFIXES = ("image/", "video/", "audio/")
EXTENSIONS = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
    "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm",
    "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav",
    "audio/mp4": ".m4a", "audio/flac": ".flac", "audio/ogg": ".ogg",
}


def media_kind(content_type: str) -> str:
    return content_type.split("/", 1)[0] if "/" in content_type else "file"


def storage_root() -> Path:
    root = Path(get_settings().storage_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_object(key: str) -> Path:
    root = storage_root()
    target = (root / key).resolve()
    if root not in target.parents:
        raise ValueError("invalid object key")
    return target


def _valid_signature(path: Path, content_type: str) -> bool:
    with path.open("rb") as source:
        head = source.read(32)
    checks = {
        "image/png": head.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": head.startswith(b"\xff\xd8\xff"),
        "image/webp": head.startswith(b"RIFF") and head[8:12] == b"WEBP",
        "video/mp4": head[4:8] == b"ftyp",
        "video/quicktime": head[4:8] == b"ftyp",
        "video/webm": head.startswith(b"\x1aE\xdf\xa3"),
        "audio/mpeg": head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0),
        "audio/wav": head.startswith(b"RIFF") and head[8:12] == b"WAVE",
        "audio/x-wav": head.startswith(b"RIFF") and head[8:12] == b"WAVE",
        "audio/mp4": head[4:8] == b"ftyp",
        "audio/flac": head.startswith(b"fLaC"),
        "audio/ogg": head.startswith(b"OggS"),
    }
    return checks.get(content_type, False)


async def save_upload(upload: UploadFile, project_id: str, asset_id: str) -> tuple[str, int, str, str]:
    content_type = (upload.content_type or mimetypes.guess_type(upload.filename or "")[0] or "application/octet-stream").lower()
    if not content_type.startswith(ALLOWED_PREFIXES):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "只允许图片、视频和音频文件")
    extension = EXTENSIONS.get(content_type) or Path(upload.filename or "").suffix.lower()
    if len(extension) > 8 or not extension.startswith("."):
        extension = ".bin"
    object_key = f"{project_id[:12]}/{asset_id}{extension}"
    path = resolve_object(object_key); path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(); size = 0; maximum = get_settings().max_upload_mb * 1024 * 1024
    try:
        with path.open("wb") as output:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > maximum:
                    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "文件超过管理员设置的上传上限")
                digest.update(chunk); output.write(chunk)
        if not _valid_signature(path, content_type):
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "文件内容与声明的图片、视频或音频格式不一致")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return object_key, size, digest.hexdigest(), content_type


def import_generated_file(source: str | Path, project_id: str, asset_id: str) -> tuple[str, int, str, str]:
    source_path = Path(source).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(str(source_path))
    content_type = mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
    extension = source_path.suffix.lower()[:8]
    object_key = f"{project_id[:12]}/{asset_id}{extension}"
    target = resolve_object(object_key); target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target)
    digest_builder = hashlib.sha256()
    with target.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest_builder.update(chunk)
    digest = digest_builder.hexdigest()
    return object_key, target.stat().st_size, digest, content_type
