# -*- coding: utf-8 -*-
"""超分辨率（像素插件，可选）。

复用工具现有 Real-ESRGAN x4 ONNX 子进程方案（与 ui/image_editor.py 的
_run_realesr_subproc 同机制）：干净子进程跑 onnxruntime，避开 PyQt6/cv2 加载后的
DLL 冲突；模型按需下载到 ~/.cep_models/。模型缺失时跳过（原样返回 + 警告），
保证批处理流水线在无模型环境下也能跑通。

仅处理 RGB；RGBA 输入会分离 alpha 并随比例放大后重新附着。
"""
import os
import sys
import numpy as np

from core.plugins import register

_REALES_R_FILE = "realesr-general-x4v3.onnx"


def _model_path():
    d = os.path.join(os.path.expanduser("~"), ".cep_models")
    return os.path.join(d, _REALES_R_FILE)


def _run_realesr_subproc(rgb, model_path):
    """自包含 ONNX 超分子进程（镜像 image_editor._run_realesr_subproc）。"""
    import subprocess
    import tempfile

    scale = 4
    script = (
        "import sys, numpy as np\n"
        "import onnxruntime as ort\n"
        "model, fin, fout = sys.argv[1], sys.argv[2], sys.argv[3]\n"
        "rgb = np.load(fin)\n"
        "sess = ort.InferenceSession(model, providers=['CPUExecutionProvider'])\n"
        "inp = sess.get_inputs()[0]\n"
        "shp = inp.shape\n"
        "fixed = isinstance(shp[-1], int) and isinstance(shp[-2], int) and shp[-1] > 0 and shp[-2] > 0\n"
        "tile = int(shp[-1]) if fixed else 128\n"
        "tile = max(64, min(tile, 256))\n"
        "pad = 8 if not fixed else 0\n"
        "h, w = rgb.shape[:2]\n"
        "x = rgb.astype(np.float32) / 255.0\n"
        "out = np.zeros((h * scale, w * scale, 3), np.float32)\n"
        "for y0 in range(0, h, tile):\n"
        "    for x0 in range(0, w, tile):\n"
        "        y1 = min(y0 + tile, h); x1 = min(x0 + tile, w)\n"
        "        yy0, xx0 = max(0, y0 - pad), max(0, x0 - pad)\n"
        "        yy1, xx1 = min(h, y1 + pad), min(w, x1 + pad)\n"
        "        patch = x[yy0:yy1, xx0:xx1, :]\n"
        "        ph, pw = patch.shape[:2]\n"
        "        if fixed:\n"
        "            buf = np.zeros((tile, tile, 3), np.float32)\n"
        "            buf[:ph, :pw, :] = patch\n"
        "            feed = buf\n"
        "        else:\n"
        "            feed = patch\n"
        "        t = np.transpose(feed, (2, 0, 1))[None, ...]\n"
        "        res = sess.run(None, {inp.name: t})[0][0]\n"
        "        res = np.clip(res, 0, 1)\n"
        "        res = np.transpose(res, (1, 2, 0))\n"
        "        if fixed:\n"
        "            res = res[:ph * scale, :pw * scale, :]\n"
        "        oy0 = (y0 - yy0) * scale; ox0 = (x0 - xx0) * scale\n"
        "        out[y0 * scale:y1 * scale, x0 * scale:x1 * scale, :] = \\\n"
        "            res[oy0:oy0 + (y1 - y0) * scale, ox0:ox0 + (x1 - x0) * scale, :]\n"
        "np.save(fout, (out * 255).clip(0, 255).astype(np.uint8))\n"
        "print('ESR_OK')\n"
    )
    tmpdir = tempfile.mkdtemp(prefix="cep_esr_")
    fin = os.path.join(tmpdir, "in.npy")
    fout = os.path.join(tmpdir, "out.npy")
    fscript = os.path.join(tmpdir, "run_esr.py")
    try:
        np.save(fin, rgb)
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--realesr-worker", model_path, fin, fout]
        else:
            with open(fscript, "w", encoding="ascii") as f:
                f.write(script)
            cmd = [sys.executable, fscript, model_path, fin, fout]
        p = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600,
            creationflags=(subprocess.CREATE_NO_WINDOW
                           if hasattr(subprocess, "CREATE_NO_WINDOW") else 0))
        if not (p.returncode == 0 and b"ESR_OK" in (p.stdout or b"")):
            raise RuntimeError(
                (p.stderr or b"").decode("utf-8", "replace")[:400] or "subprocess failed")
        return np.load(fout)
    finally:
        for fp in (fin, fout, fscript):
            try:
                if os.path.exists(fp):
                    os.remove(fp)
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass


@register
class Superres:
    NAME = "superres"
    LABEL = "AI 超分辨率 x4"
    CATEGORY = "pixel"

    def run(self, image, ctx):
        model = _model_path()
        if not os.path.exists(model):
            # 模型未下载：跳过超分，保持原图（批处理不中断）
            return image
        has_alpha = image.ndim == 3 and image.shape[2] == 4
        rgb = image[..., :3] if has_alpha else image
        out = _run_realesr_subproc(rgb, model)
        if has_alpha:
            import cv2
            ah, aw = image.shape[:2]
            alpha = image[..., 3:]
            alpha_up = cv2.resize(alpha, (out.shape[1], out.shape[0]),
                                  interpolation=cv2.INTER_NEAREST)
            return np.dstack([out, alpha_up]).copy()
        return out
