"""Temporary frozen-runtime diagnostic for the Qt dependency chain."""
import ctypes
import os
import sys
from pathlib import Path


root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
qt_bin = root / "PyQt6" / "Qt6" / "bin"
print("ROOT", root, flush=True)
print("QTBIN", qt_bin, qt_bin.is_dir(), flush=True)
print("HANDLES", len(getattr(sys, "_cep_dll_directory_handles", [])), flush=True)
for name in ("Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll"):
    try:
        ctypes.WinDLL(str(qt_bin / name))
        print("LOAD", name, "OK", flush=True)
    except OSError as error:
        print("LOAD", name, "ERROR", repr(error), getattr(error, "winerror", None), flush=True)

from PyQt6.QtWidgets import QApplication

print("IMPORT QtWidgets OK", flush=True)
app = QApplication([])
print("QApplication OK", flush=True)
