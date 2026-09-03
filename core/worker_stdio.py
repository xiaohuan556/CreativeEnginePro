"""Helpers for command-line workers hosted by the windowed frozen EXE."""

from __future__ import annotations

import io
import os
import sys
from typing import TextIO


def _windows_inherited_text_stream(std_handle_id: int) -> TextIO | None:
    """Wrap an inherited Windows standard handle without taking ownership of it.

    PyInstaller's windowed executable intentionally starts without CRT stdout and
    stderr objects.  A subprocess launched with PIPE still receives valid Win32
    handles, so duplicate and wrap those handles for command-line worker modes.
    """
    if os.name != "nt":
        return None

    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.DuplicateHandle.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.DuplicateHandle.restype = wintypes.BOOL

        # GetStdHandle receives the unsigned DWORD representation of -11/-12.
        inherited = kernel32.GetStdHandle(std_handle_id & 0xFFFFFFFF)
        invalid_handle = ctypes.c_void_p(-1).value
        inherited_value = ctypes.cast(inherited, ctypes.c_void_p).value
        if inherited_value in (None, 0, invalid_handle):
            return None

        process = kernel32.GetCurrentProcess()
        duplicate = wintypes.HANDLE()
        duplicate_same_access = 0x00000002
        if not kernel32.DuplicateHandle(
                process, inherited, process, ctypes.byref(duplicate), 0,
                False, duplicate_same_access):
            return None

        flags = os.O_WRONLY | getattr(os, "O_BINARY", 0)
        fd = msvcrt.open_osfhandle(int(duplicate.value), flags)
        binary = os.fdopen(fd, "wb", buffering=0, closefd=True)
        return io.TextIOWrapper(
            binary,
            encoding="utf-8",
            errors="replace",
            line_buffering=True,
            write_through=True,
        )
    except Exception:
        return None


def restore_frozen_worker_stdio() -> bool:
    """Restore stdout/stderr for a CLI worker inside a windowed Windows EXE.

    Returns ``True`` when both streams are available after the attempt.  Normal
    source/console launches are left untouched.
    """
    if os.name == "nt":
        if sys.stdout is None:
            stream = _windows_inherited_text_stream(-11)
            if stream is not None:
                sys.stdout = stream
        if sys.stderr is None:
            stream = _windows_inherited_text_stream(-12)
            if stream is not None:
                sys.stderr = stream
    return sys.stdout is not None and sys.stderr is not None
