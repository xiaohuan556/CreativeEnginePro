# Runtime hook for onnxruntime under PyInstaller (single-file).
# onnxruntime loads its native DLL via a path that PyInstaller's frozen
# layout doesn't satisfy by default. We locate onnxruntime.dll inside the
# extracted _MEIPASS tree and add its directory to the DLL/search path
# BEFORE onnxruntime is imported.
import os
import sys


def _fix_onnxruntime_path():
    if not getattr(sys, "frozen", False):
        return
    base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    if not os.path.isdir(base):
        return
    found = []
    for root, _dirs, files in os.walk(base):
        if "onnxruntime.dll" in files:
            found.append(root)
            break
    for d in found:
        try:
            os.add_dll_directory(d)
        except Exception:
            pass
        p = os.environ.get("PATH", "")
        if d not in p.split(";"):
            os.environ["PATH"] = d + ";" + p


_fix_onnxruntime_path()


def _patch_importlib_metadata():
    """PyInstaller 单文件包里没有第三方包的 .dist-info 元数据，
    部分包（如 pymatting）在 __init__ 里调用 importlib.metadata.version()
    会抛 PackageNotFoundError。这里兜底返回占位版本，避免导入即崩。"""
    if not getattr(sys, "frozen", False):
        return
    import importlib.metadata as _imd

    _orig_version = _imd.version
    _orig_distribution = _imd.distribution

    def _version(name, *a, **k):
        try:
            return _orig_version(name, *a, **k)
        except _imd.PackageNotFoundError:
            return "0.0.0"

    def _distribution(name, *a, **k):
        try:
            return _orig_distribution(name, *a, **k)
        except _imd.PackageNotFoundError:
            class _Stub:
                version = "0.0.0"

                def read_text(self, *a, **k):
                    return ""
            return _Stub()

    _imd.version = _version
    _imd.distribution = _distribution


_patch_importlib_metadata()
