import os, sys, faulthandler, traceback
faulthandler.enable()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication
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

def mk(name, color, size=200):
    arr = np.zeros((size, size, 4), dtype=np.uint8)
    arr[:, :, 0] = color[0]; arr[:, :, 1] = color[1]; arr[:, :, 2] = color[2]; arr[:, :, 3] = 255
    p = os.path.join(TMP, name)
    mod.qimage_from_numpy(arr).save(p)
    return p

p1 = mk("a.png", (255, 0, 0))
p2 = mk("b.png", (0, 255, 0))
ed = mod.ImageEditorWidget()

def run(label, fn):
    try:
        fn()
        print("PASS:", label)
    except Exception:
        print("FAIL:", label)
        traceback.print_exc()

# 1) 传统模式 opt-in：无画板时 _ctx_* 走 project
def test1():
    ed.new_project(200, 200)
    assert ed.project.artboards == []
    assert ed._ctx_layers() is ed.project.layers
    assert ed._ctx_size() == (200, 200)
test1 and run("1 legacy opt-in ctx", test1)

# 2) 首次加画板：把当前单画布内容转成画板[0]
def test2():
    ed.new_project(200, 200)
    ed.add_image_from_path(p1)            # 传统模式导入（画布匹配 200）
    assert len(ed.project.layers) == 1
    ed._add_artboard("A", 200, 200)
    assert len(ed.project.artboards) == 1
    assert ed.project.layers == []        # 内容已迁入画板
    ab0 = ed.project.artboards[0]
    assert len(ab0.layers) == 1
    assert ed.active_artboard is ab0
    assert ed._ctx_layers() is ab0.layers
    assert ed._ctx_size() == (200, 200)
test2 and run("2 first artboard converts legacy", test2)

# 3) 第二个画板右侧排布（间距 80）
def test3():
    ed._add_artboard("B", 300, 300)
    ab1 = ed.project.artboards[1]
    assert ab1.x == 280, ab1.x        # 200 + 80
    assert ab1.w == 300 and ab1.h == 300
    assert ed.active_artboard is ab1
    # 文档边界 = 画板包围盒 + 200 边距
    dw, dh = ed._doc_bounds()
    assert (dw, dh) == (280 + 300 + 200, 300 + 200), (dw, dh)
test3 and run("3 second artboard layout + doc bounds", test3)

# 4) 新图进入激活画板（本地坐标居中）
def test4():
    before = len(ed.active_artboard.layers)
    ed.add_image_from_path(p2)
    assert len(ed.active_artboard.layers) == before + 1
    nl = ed.active_artboard.layers[-1]
    # 本地居中：x 约为画板宽/2
    assert abs(nl.x - 150) < 1, (nl.x, nl.y)
test4 and run("4 image enters active artboard (local)", test4)

# 5) 撤销/重做 画板增删 往返
def test5():
    n0 = len(ed.project.artboards)
    ed._add_artboard("C", 150, 150)
    assert len(ed.project.artboards) == n0 + 1
    ed.undo()
    assert len(ed.project.artboards) == n0
    ed.redo()
    assert len(ed.project.artboards) == n0 + 1
test5 and run("5 artboard undo/redo", test5)

# 6) 删除到最后一个画板 -> 退回传统单画布
def test6():
    # 当前有 3 个画板；逐个删除直到剩 1，再删最后一个
    while len(ed.project.artboards) > 1:
        ed._delete_artboard(ed.project.artboards[-1])
    assert len(ed.project.artboards) == 1
    last = ed.project.artboards[0]
    layers_inside = list(last.layers)
    ed._delete_artboard(last)
    assert ed.project.artboards == []
    assert ed.active_artboard is None
    assert ed.project.layers == layers_inside
    assert ed._ctx_layers() is ed.project.layers
test6 and run("6 delete-to-last reverts legacy", test6)

# 7) 快照/重建 画板往返（序列化）
def test7():
    ed.new_project(200, 200)
    ed.add_image_from_path(p1)
    ed._add_artboard("A", 200, 200)
    ed._add_artboard("B", 300, 300)
    snap = ed._snapshot()
    # 清掉再重建
    ed.project = mod.ImageProject(10, 10)
    ed._rebuild_from_snap(snap)
    assert len(ed.project.artboards) == 2
    assert ed.project.artboards[0].name == "A"
    assert ed.project.artboards[1].x == 280
    assert len(ed.project.artboards[0].layers) == 1
    assert ed.project.artboards[0].layers[0].pixels.shape == (200, 200, 4)
test7 and run("7 snapshot/rebuild artboards", test7)

print("ALL DONE")
