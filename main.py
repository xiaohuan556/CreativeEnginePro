import os
import sys
import traceback
import json
from datetime import datetime

# 强制 ffmpeg 单线程解码（OpenCV 后端）：
# 同一进程内多个 cv2.VideoCapture 争夺 async_lock → crash。
# 必须在任何 cv2 import 之前设置。
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;1")

# ─────────────────────────────────────────────────────────────────────────────
# EXE 内 yt-dlp 命令行 worker 模式
# ─────────────────────────────────────────────────────────────────────────────
# 冻结后的 sys.executable 指向 CreativeEnginePro.exe，而不是 python.exe。
# 下载器若继续执行 ``sys.executable -m yt_dlp``，就会再次启动完整桌面应用并
# 弹出登录框。这个入口必须位于任何 PyQt / 登录逻辑之前，让同一个 EXE 只运行
# 内置 yt-dlp 的 CLI，然后把 stdout / 退出码原样交还下载线程。
if "--ytdlp-worker" in sys.argv:
    try:
        # ``console=False`` 的冻结程序没有 Python CRT stdout/stderr，但下载器
        # 通过 PIPE 启动时仍会传入有效 Win32 标准句柄。先恢复文本流，否则
        # yt-dlp 的版本、进度和错误会全部丢失，桌面下载线程也无法判断状态。
        from core.worker_stdio import restore_frozen_worker_stdio
        restore_frozen_worker_stdio()
        from yt_dlp import main as _ytdlp_main
        _i = sys.argv.index("--ytdlp-worker")
        _worker_args = sys.argv[_i + 1:]
        _result = _ytdlp_main(_worker_args)
        sys.exit(int(_result or 0))
    except SystemExit:
        raise
    except Exception as _e:
        if sys.stderr is not None:
            print("YTDLP_ERR: " + str(_e), file=sys.stderr, flush=True)
        sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# EXE 内 Real-ESRGAN 子进程 worker 模式
# ─────────────────────────────────────────────────────────────────────────────
# 必须在 import PyQt6 / cv2 之前执行！本进程已加载 PyQt6/cv2 后，onnxruntime 的
# 原生 DLL 会与已加载的运行库冲突而初始化失败。因此让 EXE 以 --realesr-worker
# 重新启动一个「干净子进程」，其中 onnxruntime 可正常加载，推理完把结果写回 .npy。
if "--realesr-worker" in sys.argv:
    try:
        import numpy as np
        import onnxruntime as ort
        _i = sys.argv.index("--realesr-worker")
        _model, _fin, _fout = sys.argv[_i + 1], sys.argv[_i + 2], sys.argv[_i + 3]
        _rgb = np.load(_fin)
        _sess = ort.InferenceSession(_model, providers=["CPUExecutionProvider"])
        _inp = _sess.get_inputs()[0]
        _shp = _inp.shape
        _fixed = (isinstance(_shp[-1], int) and isinstance(_shp[-2], int)
                  and _shp[-1] > 0 and _shp[-2] > 0)
        _tile = int(_shp[-1]) if _fixed else 128
        _tile = max(64, min(_tile, 256))
        _pad = 8 if not _fixed else 0
        _h, _w = _rgb.shape[:2]
        _scale = 4
        _x = _rgb.astype(np.float32) / 255.0
        _out = np.zeros((_h * _scale, _w * _scale, 3), np.float32)
        for _y0 in range(0, _h, _tile):
            for _x0 in range(0, _w, _tile):
                _y1 = min(_y0 + _tile, _h)
                _x1 = min(_x0 + _tile, _w)
                _yy0, _xx0 = max(0, _y0 - _pad), max(0, _x0 - _pad)
                _yy1, _xx1 = min(_h, _y1 + _pad), min(_w, _x1 + _pad)
                _patch = _x[_yy0:_yy1, _xx0:_xx1, :]
                _ph, _pw = _patch.shape[:2]
                if _fixed:
                    _buf = np.zeros((_tile, _tile, 3), np.float32)
                    _buf[:_ph, :_pw, :] = _patch
                    _feed = _buf
                else:
                    _feed = _patch
                _t = np.transpose(_feed, (2, 0, 1))[None, ...]
                _res = _sess.run(None, {_inp.name: _t})[0][0]
                _res = np.clip(_res, 0, 1)
                _res = np.transpose(_res, (1, 2, 0))
                if _fixed:
                    _res = _res[:_ph * _scale, :_pw * _scale, :]
                _oy0 = (_y0 - _yy0) * _scale
                _ox0 = (_x0 - _xx0) * _scale
                _out[_y0 * _scale:_y1 * _scale, _x0 * _scale:_x1 * _scale, :] = \
                    _res[_oy0:_oy0 + (_y1 - _y0) * _scale, _ox0:_ox0 + (_x1 - _x0) * _scale, :]
        np.save(_fout, (_out * 255).clip(0, 255).astype(np.uint8))
        print("ESR_OK")
    except Exception as _e:
        print("ESR_ERR: " + str(_e), file=sys.stderr)
    sys.exit(0)

# ─────────────────────────────────────────────────────────────────────────────
# EXE 内 BiRefNet / CodeFormer 通用 onnxruntime 子进程 worker 模式
# ─────────────────────────────────────────────────────────────────────────────
# 与 --realesr-worker 同原理：在干净子进程里加载 onnxruntime，避免主进程已加载
# PyQt6/cv2 后 DLL 初始化失败。主进程只负责 cv2 预处理，worker 只跑推理。
if "--ai-worker" in sys.argv:
    try:
        import numpy as np
        import onnxruntime as ort
        _i = sys.argv.index("--ai-worker")
        _model = sys.argv[_i + 1]
        _fin = sys.argv[_i + 2]
        _fout = sys.argv[_i + 3]
        _data = np.load(_fin)
        _sess = ort.InferenceSession(_model, providers=["CPUExecutionProvider"])
        _metas = _sess.get_inputs()
        # 按形状把传入数组匹配到模型输入
        _feed = {}
        _used = set()
        for _k in _data.files:
            _arr = _data[_k]
            for _meta in _metas:
                if _meta.name in _used:
                    continue
                _sh = _meta.shape
                if len(_sh) != len(_arr.shape):
                    continue
                _match = True
                for _a, _b in zip(_arr.shape, _sh):
                    if isinstance(_b, int) and _b > 0 and _a != _b:
                        _match = False
                        break
                if _match:
                    _feed[_meta.name] = _arr
                    _used.add(_meta.name)
                    break
        # 兜底：按顺序分配剩余未匹配的数组
        if len(_feed) < len(_metas):
            for _k, _meta in zip(_data.files, _metas):
                if _meta.name not in _feed:
                    _feed[_meta.name] = _data[_k]
        _out = _sess.run(None, _feed)[0]
        np.save(_fout, _out)
        print("AI_OK")
    except Exception as _e:
        print("AI_ERR: " + str(_e), file=sys.stderr)
    sys.exit(0)

# --- 1. 全局未捕获异常处理器（诊断用）---
# 捕获 PyQt 槽函数 / 渲染中的未处理异常，写日志并弹窗，便于定位诡异崩溃
# （例如 "wrapped C/C++ object of type QImage has been deleted"）。
# 注意：--ai-worker / --realesr-worker 子进程已在上方 sys.exit(0)，不会到达此处。
import traceback as _traceback

def _cep_global_excepthook(etype, value, tb):
    msg = "".join(_traceback.format_exception(etype, value, tb))
    try:
        _log_dir = os.path.join(os.path.expanduser("~"), ".cep_models")
        os.makedirs(_log_dir, exist_ok=True)
        with open(os.path.join(_log_dir, "cep_crash.log"), "a", encoding="utf-8") as _f:
            _f.write("\n=== {} ===\n".format(datetime.now().isoformat()) + msg + "\n")
    except Exception:
        pass
    try:
        from PyQt6.QtWidgets import QApplication, QMessageBox
        _app = QApplication.instance()
        if _app is not None and not getattr(_cep_global_excepthook, "_busy", False):
            _cep_global_excepthook._busy = True
            try:
                QMessageBox.critical(
                    None, "程序异常",
                    "捕获到未处理异常：\n\n" + msg[-3000:] +
                    "\n\n（完整错误已写入 ~/.cep_models/cep_crash.log，可发给我定位问题）")
            finally:
                _cep_global_excepthook._busy = False
    except Exception:
        pass
    sys.__excepthook__(etype, value, tb)

sys.excepthook = _cep_global_excepthook

# --- 2. 路径兼容处理 (必须最先运行) ---
if hasattr(sys, '_MEIPASS'):
    base_path = sys._MEIPASS
else:
    base_path = os.path.abspath(".")

sys.path.insert(0, base_path)
sys.path.insert(0, os.path.join(base_path, "core"))
sys.path.insert(0, os.path.join(base_path, "ui"))

# --- 3. 先创建 QApplication（必须在任何 Qt 对象创建之前）---
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox
from PyQt6.QtGui import QFont, QIcon

if __name__ == '__main__':
    app = QApplication(sys.argv)
    # 应用级图标会自动传递给主窗口、弹窗和 Windows 任务栏。
    app.setWindowIcon(QIcon(os.path.join(base_path, "assets", "icon.ico")))

    # 正式版日期锁与公司账号门禁。开发清单默认不要求登录；正式构建脚本会
    # 生成 require_login=true 且固化 HTTPS 服务地址的授权清单。
    try:
        from core.release_gate import DesktopAuthClient, ReleaseGateError, ReleasePolicy
        from ui.release_login import ReleaseLoginDialog
        release_policy = ReleasePolicy.load()
    except Exception as error:
        QMessageBox.critical(None, "版本授权异常", str(error))
        sys.exit(1)

    if release_policy.is_expired():
        QMessageBox.critical(
            None, "版本已到期",
            f"当前版本已于 {release_policy.expiry_label} 到期，请联系管理员获取新版本。")
        sys.exit(1)

    bundle_smoke = "--bundle-smoke" in sys.argv
    auth_client = None
    authenticated_user = None
    if release_policy.require_login and not bundle_smoke:
        auth_client = DesktopAuthClient(release_policy.auth_base_url)
        login_dialog = ReleaseLoginDialog(release_policy, auth_client)
        if login_dialog.exec() != QDialog.DialogCode.Accepted or login_dialog.result is None:
            sys.exit(0)
        authenticated_user = login_dialog.result.user

    app.setFont(QFont("Segoe UI", 9))
    app.setStyleSheet("QToolTip { color: #ffffff; background-color: #2a2a2a; border: 1px solid #555555; padding: 2px 6px; }")

    # ==========================================
    # 全局异常捕获逻辑（防闪退神器）
    # ==========================================
    def handle_exception(exc_type, exc_value, exc_traceback):
        """拦截所有未捕获的异常并弹窗显示"""
        error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Critical)
        msg.setWindowTitle("功能运行出错")
        msg.setText("程序在执行操作时发生崩溃，详情如下：")
        msg.setInformativeText(error_msg)
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.exec()

    sys.excepthook = handle_exception

    # --- 3. QApplication 就绪后再导入业务逻辑 ---
    if auth_client is not None:
        from core.release_gate import configure_desktop_control_client
        configure_desktop_control_client(auth_client)
    from ui.main_window import UltimateEngine

    window = UltimateEngine()
    if authenticated_user:
        display_name = str(authenticated_user.get("display_name") or authenticated_user.get("username") or "")
        if display_name:
            window.setWindowTitle(f"{window.windowTitle()} · {display_name}")
    if auth_client is not None:
        from ui.release_login import DesktopSessionGuard, close_for_invalid_session
        # Retain the guard on the window; otherwise Qt/Python may collect it.
        window._release_session_guard = DesktopSessionGuard(
            release_policy, auth_client, window)
        window._release_session_guard.invalidated.connect(close_for_invalid_session)
    if bundle_smoke:
        # Build-time probe: prove that Qt and the complete main window can be
        # constructed from the frozen bundle.  It never exposes a usable app
        # or bypasses the login gate for an interactive session.
        from PyQt6.QtCore import QTimer

        marker = os.environ.get("CEP_BUNDLE_SMOKE_MARKER", "").strip()

        def _complete_bundle_smoke():
            if marker:
                with open(marker, "w", encoding="utf-8") as stream:
                    json.dump({
                        "ok": True,
                        "version": release_policy.version,
                        "login_required": release_policy.require_login,
                    }, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
            # Some multimedia backends crash during offscreen Qt teardown.
            # The build probe has already proven full construction at this
            # point, so terminate without exposing an interactive session.
            os._exit(0)

        window.show()
        QTimer.singleShot(750, _complete_bundle_smoke)
    else:
        window.show()
    sys.exit(app.exec())
