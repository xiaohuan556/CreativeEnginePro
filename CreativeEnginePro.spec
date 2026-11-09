# -*- mode: python ; coding: utf-8 -*-


import importlib.util as _iu
import os as _os
from pathlib import Path as _Path
from PyInstaller.building import splash_templates as _splash_templates
from PyInstaller.utils.hooks import collect_all as _collect_all


def _create_startup_splash():
    """Create an opaque branded splash used while the one-file EXE extracts."""
    import io as _io
    from PIL import Image as _Image, ImageDraw as _ImageDraw, ImageFont as _ImageFont

    width, height = 680, 360
    canvas = _Image.new("RGB", (width, height), "#121318")
    draw = _ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (1, 1, width - 2, height - 2), radius=18,
        fill="#181a21", outline="#343946", width=2)

    icon = _Image.open("assets/icon.png").convert("RGBA")
    icon.thumbnail((88, 88), _Image.Resampling.LANCZOS)
    plate = _Image.new("RGBA", (112, 112), (0, 0, 0, 0))
    plate_draw = _ImageDraw.Draw(plate)
    plate_draw.rounded_rectangle(
        (0, 0, 111, 111), radius=22,
        fill="#f3f4f7", outline="#d9dce3", width=2)
    plate.alpha_composite(icon, ((112 - icon.width) // 2, (112 - icon.height) // 2))
    canvas.paste(plate, (48, 60), plate)

    fonts = _Path(_os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    try:
        title_font = _ImageFont.truetype(str(fonts / "msyhbd.ttc"), 30)
        caption_font = _ImageFont.truetype(str(fonts / "segoeui.ttf"), 15)
    except OSError:
        title_font = _ImageFont.load_default()
        caption_font = _ImageFont.load_default()
    draw.text((188, 76), "小欢ovo", font=title_font, fill="#f4f6fb")
    draw.text((190, 124), "AI Filmmaking Desktop", font=caption_font, fill="#9199aa")

    output = _Path("build") / "startup_splash.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = _io.BytesIO()
    canvas.save(encoded, format="PNG", optimize=True)
    payload = encoded.getvalue()
    # Keep the mtime stable when the artwork is unchanged so an interrupted
    # final EXE write can reuse the expensive Analysis/PKG build safely.
    if not output.exists() or output.read_bytes() != payload:
        output.write_bytes(payload)
    return str(output.resolve())


def _install_realtime_splash_progress(total_items):
    """Turn PyInstaller filename updates into a live 0-100% progress bar."""
    old_update = r"""
proc canvas_text_update {canvas tag _var - -}  {
    # This function is rigged to be called if the a variable
    # status_text gets changed. This updates the text on
    # the canvas
    upvar $_var var
    $canvas itemconfigure $tag -text $var
}
"""
    progress_update = r"""
proc canvas_text_update {canvas tag _var - -}  {
    global progress_count progress_total progress_value
    upvar $_var var

    if {$var eq "__CEP_BOOT__"} {
        set progress_value 0
    } elseif {$var eq "__CEP_AUTH__"} {
        set progress_value 98
    } elseif {$var eq "__CEP_READY__"} {
        set progress_value 100
    } else {
        incr progress_count
        set progress_value [expr {int(96.0 * $progress_count / $progress_total)}]
        if {$progress_value > 96} { set progress_value 96 }
        if {$progress_value < 1} { set progress_value 1 }
    }

    set progress_right [expr {48 + int(584.0 * $progress_value / 100.0)}]
    $canvas coords progress_fill 48 276 $progress_right 282
    $canvas itemconfigure $tag -text "$progress_value%"
    update idletasks
}
"""
    if old_update not in _splash_templates.image_script:
        raise RuntimeError("Unsupported PyInstaller splash template: progress hook missing")
    _splash_templates.image_script = _splash_templates.image_script.replace(
        old_update, progress_update)

    # Re-center only after Tk has measured the real borderless window.  Always
    # use the primary screen's Tk coordinate space: pointer-based monitor
    # arithmetic mixes virtual-desktop and DPI-scaled coordinates on Windows,
    # which can move the splash away from the visible screen centre.
    geometry_marker = "wm geometry         . +${x_position}+${y_position}"
    centered_geometry = r"""update idletasks
set splash_width [winfo reqwidth .]
set splash_height [winfo reqheight .]
set splash_primary_center 1
set x_position [expr {max(0, int(($display_width - $splash_width) / 2))}]
set y_position [expr {max(0, int(($display_height - $splash_height) / 2))}]
set splash_geometry [format "%dx%d%+d%+d" $splash_width $splash_height $x_position $y_position]
wm geometry         . $splash_geometry
update idletasks"""
    for template_name in ("position_window", "position_window_on_top"):
        template = getattr(_splash_templates, template_name)
        if geometry_marker not in template:
            raise RuntimeError(
                "Unsupported PyInstaller splash template: geometry hook missing")
        setattr(_splash_templates, template_name,
                template.replace(geometry_marker, centered_geometry))

    progress_widgets = f"""
.root.canvas create rectangle 48 276 632 282 \\
        -fill #2a2e39 -outline "" -tag progress_track
.root.canvas create rectangle 48 276 48 282 \\
        -fill #3d8ef8 -outline "" -tag progress_fill
set progress_count 0
set progress_total {max(1, int(total_items))}
set progress_value 0
"""
    marker = "trace add variable status_text write \\\n"
    text_template = _splash_templates.splash_canvas_text
    if marker not in text_template:
        raise RuntimeError("Unsupported PyInstaller splash template: trace hook missing")
    text_template = text_template.replace(marker, progress_widgets + marker)
    text_template = text_template.replace(
        'set status_text "%(default_text)s"', 'set status_text "__CEP_BOOT__"')
    text_template = text_template.replace("-anchor sw", "-anchor se")
    _splash_templates.splash_canvas_text = text_template


def _pkgloc(name):
    """返回包目录（不含 __init__.py）；找不到返回 None；不会真正导入该包。"""
    try:
        s = _iu.find_spec(name)
        if s is None or not s.origin:
            return None
        return _os.path.dirname(s.origin)
    except Exception:
        return None


_release_manifest = _Path(_os.environ.get(
    "CEP_RELEASE_MANIFEST", "release_manifest.json")).resolve()
if not _release_manifest.is_file():
    raise FileNotFoundError(f"release manifest not found: {_release_manifest}")

_datas = [
    ('assets', 'assets'),
    ('hooks', 'hooks'),
    ('ai/skills', 'ai/skills'),
    ('tools/read_image_ocr.ps1', 'tools'),
    (str(_release_manifest), '.'),
]
_rembg_dir = _pkgloc("rembg")
_ort_dir = _pkgloc("onnxruntime")
_curl_datas, _curl_binaries, _curl_hiddenimports = _collect_all("curl_cffi")
if _rembg_dir:
    _datas.append((_rembg_dir, "rembg"))
if _ort_dir:
    _datas.append((_ort_dir, "onnxruntime"))
_datas.extend(_curl_datas)
_image_binaries, _image_hiddenimports = [], []
for _image_package in ('psd_tools', 'rawpy', 'pillow_heif'):
    _image_datas, _image_native, _image_imports = _collect_all(_image_package)
    _datas.extend(_image_datas)
    _image_binaries.extend(_image_native)
    _image_hiddenimports.extend(_image_imports)


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[('ffmpeg.exe', '.'), *_curl_binaries, *_image_binaries],
    datas=_datas,
    # yt_dlp: 下载引擎（随包内置，避免依赖系统 PATH 上的 yt-dlp）
    # yt_dlp_ejs: YouTube n-challenge/n-sig JS 解密组件包（内置后无需运行时从 GitHub 拉取）
    # rembg / onnxruntime 不放在 hiddenimports：其原生 .pyd 在 PyInstaller 的导入分析子进程里
    # 会 access violation（与 cv2/PyQt6 等大量原生库同处一个子进程加载器时）。改为在代码里用
    # importlib 动态导入，并把整个包作为 datas 拷贝进 bundle，运行时由
    # hooks/onnxruntime_runtime.py 设置 DLL 搜索路径。
    hiddenimports=['cv2', 'numpy', 'PyQt6', 'moviepy', 'requests', 'yt_dlp', 'yt_dlp_ejs',
                   'curl_cffi', *_curl_hiddenimports,
                   'PyQt6.QtSvg', 'PyQt6.QtPdf', *_image_hiddenimports,
                   'pymatting', 'skimage', 'skimage.morphology', 'scipy',
                   'pooch', 'jsonschema', 'tqdm', 'PIL',
                   'core.release_gate', 'ui.release_login', 'ui.personal_center',
                   'ui.desktop_account_admin', 'utils.app_paths'],
    hookspath=['hooks'],  # 含自定义 hook-onnxruntime.py（空实现，覆盖 std hook 以避免分析期导入原生 .pyd 崩溃）
    hooksconfig={},
    runtime_hooks=['hooks/qt_runtime.py', 'hooks/onnxruntime_runtime.py'],
    excludes=['onnx', 'torch', 'torchvision', 'torchaudio', 'whisper',
               'cupy', 'jax',
               # Server, test and notebook stacks are never part of desktop.
               'fastapi', 'uvicorn', 'sqlalchemy', 'psycopg', 'pytest',
               'IPython', 'ipykernel', 'jupyter',
               # Optional analytical/office stacks pulled by broad hooks.
               'pandas', 'openpyxl', 'matplotlib', 'tkinter', '_tkinter',
               'scipy._lib.array_api_compat.torch',
               'scipy._lib.array_api_compat.cupy',
               'scipy._lib.array_api_compat.jax',
               'sklearn.externals.array_api_compat.torch',
               'sklearn.externals.array_api_compat.cupy',
               'sklearn.externals.array_api_compat.jax',
               'sklearn.externals.array_api_compat.dask.array'],  # onnx/torch 等重型包在
                         # PyInstaller 依赖扫描子进程里会崩溃或 access violation，且会污染
                         # 加载器状态连带拖垮 onnxruntime；运行时均不需要
                         # （onnxruntime 自带 C++ ONNX 解析器；scipy/sklearn 的
                         # array_api_compat 不应触发 torch/cupy/jax 后端）
    noarchive=False,
    optimize=0,
)

# Qt6Core on current Windows imports the operating-system ICU shim named
# ``icuuc.dll``.  A developer PATH can contain an unrelated full ICU runtime
# with the same file name (Codex's bundled Poppler is one example); PyInstaller
# may then copy that DLL to the bundle root, where it shadows System32 and makes
# every PyQt6 import fail.  Never ship a non-system unversioned ICU beside the
# EXE.  Versioned ICU data files pulled in solely with that conflicting runtime
# are removed as well.
def _is_foreign_windows_icu(entry):
    target = _Path(str(entry[0])).name.casefold()
    source = str(entry[1]).replace('/', '\\').casefold()
    is_icu = target in {"icuuc.dll", "icuin.dll"} or target.startswith("icudt")
    return is_icu and "\\windows\\system32\\" not in source


a.binaries = [entry for entry in a.binaries if not _is_foreign_windows_icu(entry)]
pyz = PYZ(a.pure)

# The bootloader reports every extracted binary/data entry.  Convert those
# otherwise-visible filenames into a real extraction percentage instead.
_install_realtime_splash_progress(len(a.binaries) + len(a.datas) + 1)

splash = Splash(
    _create_startup_splash(),
    binaries=a.binaries,
    datas=a.datas,
    text_pos=(632, 323),
    text_size=11,
    # PyInstaller writes this value into Tcl without quoting it. Font names
    # containing spaces abort the splash script and leave a blank 200x200
    # window titled "tk", so use Tk's portable single-token font alias.
    text_font="TkDefaultFont",
    text_color="#aeb7c8",
    text_default="0%",
    always_on_top=True,
)

exe = EXE(
    pyz,
    a.scripts,
    splash,
    splash.binaries,
    a.binaries,
    a.datas,
    [],
    name='CreativeEnginePro',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=_os.environ.get("CEP_BUILD_CONSOLE", "").strip() == "1",
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
    version='version_info.txt',
)
