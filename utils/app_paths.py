"""Application paths shared by source runs and frozen Windows releases.

PyInstaller one-file applications are unpacked into ``sys._MEIPASS``.  That
directory is for bundled read-only resources only: it is deleted when the
process exits.  User configuration, canvas state and generated intermediates
therefore live under LocalAppData in a frozen build.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


APP_DIR_NAME = "CreativeEnginePro"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def resource_root() -> Path:
    """Root containing files bundled with the application."""
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[1]


def install_root() -> Path:
    """Folder containing the executable (project root during development)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return resource_root()


def user_data_root() -> Path:
    override = os.environ.get("CEP_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if not is_frozen():
        # Preserve the repository's existing development behaviour.
        return resource_root()
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / APP_DIR_NAME


def work_root() -> Path:
    override = os.environ.get("CEP_WORK_DIR", "").strip()
    return Path(override).expanduser().resolve() if override else user_data_root() / "work_temp"


def output_root() -> Path:
    override = os.environ.get("CEP_OUTPUT_DIR", "").strip()
    return Path(override).expanduser().resolve() if override else user_data_root() / "work_output"


def ensure_user_directories() -> None:
    for folder in (user_data_root(), work_root(), output_root()):
        folder.mkdir(parents=True, exist_ok=True)


def user_file(*parts: str) -> Path:
    return user_data_root().joinpath(*parts)
