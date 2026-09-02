"""Keep bundled Qt DLL directories alive for the whole frozen process.

PyQt6's Windows bootstrap calls ``os.add_dll_directory`` without retaining the
returned handle.  With Python 3.13 that directory may be removed before
``QtWidgets.pyd`` is imported, even though all Qt DLLs are present in the
one-file bundle.  This runtime hook runs before PyInstaller's PyQt hook and
keeps the handles on ``sys`` for the process lifetime.
"""
from __future__ import annotations

import os
import sys


def _configure_qt_dll_search() -> None:
    if not getattr(sys, "frozen", False):
        return

    bundle_root = os.path.abspath(
        getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    )
    candidates = [
        os.path.join(bundle_root, "PyQt6", "Qt6", "bin"),
        bundle_root,
    ]
    handles = list(getattr(sys, "_cep_dll_directory_handles", []))
    existing = os.environ.get("PATH", "").split(os.pathsep)
    prepend: list[str] = []
    for directory in candidates:
        if not os.path.isdir(directory):
            continue
        try:
            handles.append(os.add_dll_directory(directory))
        except (AttributeError, FileNotFoundError, OSError):
            pass
        if directory.casefold() not in {item.casefold() for item in existing + prepend}:
            prepend.append(directory)

    # Keep both the Python DLL-directory cookies and the legacy PATH fallback.
    sys._cep_dll_directory_handles = handles
    if prepend:
        os.environ["PATH"] = os.pathsep.join(prepend + existing)


_configure_qt_dll_search()
