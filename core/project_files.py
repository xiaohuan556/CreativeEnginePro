"""Persistence for XiaoHuan desktop projects and their legacy JSON files."""

import io
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path


PIC_SUFFIX = ".xiaohuanpic"
VID_SUFFIX = ".xiaohuanvid"
AI_SUFFIX = ".xiaohuanai"
PROJECT_SUFFIXES = {PIC_SUFFIX, VID_SUFFIX, AI_SUFFIX}
LEGACY_SUFFIXES = {".json", ".cep", ".cepstudio"}

PIC_FILTER = f"小欢图片工程 (*{PIC_SUFFIX})"
VID_FILTER = f"小欢剪辑工程 (*{VID_SUFFIX})"
AI_FILTER = f"小欢 AI 画布工程 (*{AI_SUFFIX})"


def project_path(path, suffix):
    target = Path(path)
    current = target.suffix.lower()
    if current == suffix:
        return target
    if current in LEGACY_SUFFIXES | PROJECT_SUFFIXES:
        return target.with_suffix(suffix)
    return target.with_name(target.name + suffix)


def _project_member(suffix):
    return f"{suffix[1:]}/project-v1.json"


def write_project(stream, data, suffix):
    """Write a compressed, typed project to an open binary stream."""
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as package:
        with package.open(_project_member(suffix), "w", force_zip64=True) as payload:
            with io.TextIOWrapper(payload, encoding="utf-8") as document:
                json.dump(data, document, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def read_project(source, suffix):
    """Read a project path or binary stream, including previous JSON projects."""
    if isinstance(source, (str, os.PathLike)):
        with open(source, "rb") as stream:
            return read_project(stream, suffix)
    signature = source.read(4)
    source.seek(0)
    if signature == b"PK\x03\x04":
        with zipfile.ZipFile(source) as package:
            member = _project_member(suffix)
            if member not in package.namelist():
                raise ValueError(f"工程类型或版本不兼容，需要 {suffix} 工程。")
            with package.open(member) as payload:
                data = json.load(payload)
    else:
        data = json.load(source)
    if not isinstance(data, dict):
        raise ValueError("工程内容无效。")
    return data


def save_project_file(path, data, suffix=None, *, backup=False):
    """Replace a complete project atomically. A missing suffix writes legacy JSON."""
    target = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb" if suffix else "w", encoding=None if suffix else "utf-8",
            dir=target.parent, prefix="." + target.name + ".",
            suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            if suffix:
                write_project(stream, data, suffix)
            else:
                json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        if backup and target.is_file():
            shutil.copy2(target, target.with_suffix(target.suffix + ".bak"))
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
