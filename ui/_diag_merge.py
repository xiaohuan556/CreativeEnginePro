import os, sys, faulthandler, traceback
faulthandler.enable()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import Qt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import importlib.util
spec = importlib.util.spec_from_file_location("image_editor", os.path.join(HERE, "image_editor.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

app = QApplication.instance() or QApplication(sys.argv)
app.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)

# 捕获 QMessageBox 在 headless 下会阻塞/崩溃的情况，改为记录返回
_msg_log = []
_orig_info = QMessageBox.information
def _fake_info(*a, **k):
    _msg_log.append(a[2] if len(a) > 2 else "")
    return QMessageBox.StandardButton.Ok
QMessageBox.information = staticmethod(_fake_info)

TMP = os.path.join(HERE, "_diag")
os.makedirs(TMP, exist_ok=True)

def mk(name, color, size=200):
    arr = np.zeros((size, size, 4), dtype=np.uint8)
    arr[:, :, 0] = color[0]; arr[:, :, 1] = color[1]; arr[:, :, 2] = color[2]; arr[:, :, 3] = 255
    p = os.path.join(TMP, name)
    mod.qimage_from_numpy(arr).save(p)
    return p

p1 = mk("a.png", (255, 0, 0))
p2 = mk("b.png", (0, 255, 0))
p3 = mk("c.png", (0, 0, 255))

ed = mod.ImageEditorWidget()

def run(label, fn):
    try:
        fn()
        print("PASS:", label)
    except Exception:
        print("FAIL:", label)
        traceback.print_exc()

# A) merge_down 真正执行渲染路径（active 在顶层 index 1）
def testA():
    ed.new_project(400, 400)
    ed.add_image_from_path(p1)
    ed.add_image_from_path(p2)
    layers = ed.project.layers
    assert len(layers) == 2, len(layers)
    top = layers[-1]
    ed.set_active(top)
    assert layers.index(top) == 1
    ed._merge_down()
    assert len(ed.project.layers) == 1, len(ed.project.layers)
    L = ed.project.layers[0]
    assert L.kind == "image"
    assert L.pixels.shape[2] == 4 and L.pixels.shape[0] == 200
testA and run("A merge_down execute render path", testA)

# B) 复现原始崩溃序列（top 先上移失败再下移到底，触发 i==0 守卫）
def testB():
    _msg_log.clear()
    ed.new_project(400, 400)
    ed.add_image_from_path(p1)
    ed.add_image_from_path(p2)
    top = ed.project.layers[-1]
    ed.set_active(top)
    ed._reorder(top, +1)
    ed._reorder(top, -1)
    print("  reorder ok; top idx =", ed.project.layers.index(top))
    ed._merge_down()
    print("  merge returned; guard msg shown:", _msg_log)
testB and run("B reorder->merge hits guard", testB)

# C) 三层 + 正确 net 位移后合并（active 不在底）
def testC():
    ed.new_project(400, 400)
    ed.add_image_from_path(p1)
    ed.add_image_from_path(p2)
    ed.add_image_from_path(p3)
    layers = ed.project.layers  # [a,b,c]
    c = layers[-1]
    ed.set_active(c)
    ed._reorder(c, -1)  # c: 2->1
    ed._reorder(c, +1)  # c: 1->2 (回到顶层)
    assert layers.index(c) == 2
    ed._merge_down()
    # c 与 b 合并，剩余 [a, merged]
    assert len(ed.project.layers) == 2, len(ed.project.layers)
    assert ed.project.layers[-1].kind == "image"
testC and run("C reorder-net-zero then merge", testC)

# D) undo 恢复合并
def testD():
    ed.new_project(400, 400)
    ed.add_image_from_path(p1)
    ed.add_image_from_path(p2)
    top = ed.project.layers[-1]
    ed.set_active(top)
    ed._merge_down()
    assert len(ed.project.layers) == 1
    ed.undo()
    assert len(ed.project.layers) == 2, len(ed.project.layers)
testD and run("D undo of merge", testD)

# E) 多选 / 全选 / 反选 不回归
def testE():
    ed.new_project(400, 400)
    ed.add_image_from_path(p1)
    ed.add_image_from_path(p2)
    ed.add_image_from_path(p3)
    ed._select_all_layers()
    assert len(ed.selected) == 3, len(ed.selected)
    ed._invert_layer_selection()
    assert len(ed.selected) == 0, len(ed.selected)
    ed._select_all_layers()
    ed._delete_selected()
    assert len(ed.project.layers) == 0
testE and run("E select-all/invert/delete regression", testE)

print("ALL DONE")
