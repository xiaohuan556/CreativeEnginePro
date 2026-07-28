import os, sys, faulthandler, traceback
faulthandler.enable()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
from PyQt6.QtCore import Qt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import importlib.util
spec = importlib.util.spec_from_file_location("image_editor", os.path.join(HERE, "image_editor.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

app = QApplication.instance() or QApplication(sys.argv)

TMP = os.path.join(HERE, "_diag")
os.makedirs(TMP, exist_ok=True)
OUT = os.path.join(TMP, "out")
os.makedirs(OUT, exist_ok=True)

QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: OUT)
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)

def mk(name, color, size=200):
    arr = np.zeros((size, size, 4), dtype=np.uint8)
    arr[:, :, 0] = color[0]; arr[:, :, 1] = color[1]; arr[:, :, 2] = color[2]; arr[:, :, 3] = 255
    p = os.path.join(TMP, name)
    mod.qimage_from_numpy(arr).save(p)
    return p

p1 = mk("a.png", (255, 0, 0), 200)
p2 = mk("b.png", (0, 0, 255), 300)
ed = mod.ImageEditorWidget()

def run(label, fn):
    try:
        fn(); print("PASS:", label)
    except Exception:
        print("FAIL:", label); traceback.print_exc()

def test():
    ed.new_project(200, 200)
    ed.add_image_from_path(p1)
    ed._add_artboard("画板 1", 200, 200)
    ed._add_artboard("Board2", 300, 300)
    ed.add_image_from_path(p2)   # 进入激活的 Board2
    ed.export_all_artboards()
    f1 = os.path.join(OUT, "画板 1.png")
    f2 = os.path.join(OUT, "Board2.png")
    assert os.path.exists(f1), os.listdir(OUT)
    assert os.path.exists(f2), os.listdir(OUT)
    from PyQt6.QtGui import QImage
    im1 = QImage(f1); im2 = QImage(f2)
    assert (im1.width(), im1.height()) == (200, 200), (im1.width(), im1.height())
    assert (im2.width(), im2.height()) == (300, 300), (im2.width(), im2.height())
    print("  exported:", f1, f2)
run("batch export artboards -> per-board PNG", test)

print("ALL DONE")
