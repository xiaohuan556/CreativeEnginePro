import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import importlib.util
src_path = r"C:\Users\A\Desktop\CreativeEnginePro\ui\image_editor.py"
src = open(src_path, encoding="utf-8").read()
mod = type(sys)("ie"); mod.__file__ = src_path
exec(compile(src, src_path, "exec"), mod.__dict__)
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage, QColor
app = QApplication([])
ed = mod.ImageEditorWidget()

def ck(c, m):
    assert c, "FAIL: " + m
    print("OK:", m)

# 造两张图
def mk(w, h, color):
    p = tempfile.mktemp(suffix=".png")
    qi = QImage(w, h, QImage.Format.Format_RGBA8888); qi.fill(QColor(*color))
    qi.save(p); return p

p1 = mk(100, 100, (255, 0, 0, 255))
p2 = mk(100, 100, (0, 255, 0, 255))
ed.add_image_from_path(p1)   # 底
ed.add_image_from_path(p2)   # 顶
ck(len(ed.project.layers) == 2, "legacy: 2 layers")
bottom, top = ed.project.layers[0], ed.project.layers[1]

# reorder: 顶上移应到更顶（idx 增大）
ed.set_active(top)
ed._reorder(top, +1)
ck(ed.project.layers[-1] is top, "legacy: reorder moves layer up")
ed._reorder(top, -1)
ck(ed.project.layers[0] is top, "legacy: reorder moves layer down")

# duplicate
ed.set_active(bottom)
n0 = len(ed.project.layers)
ed._duplicate_layer(bottom)
ck(len(ed.project.layers) == n0 + 1, "legacy: duplicate adds layer")

# merge down: 把 top 合并进它下方
ed.set_active(top)
ed._merge_down()
ck(len(ed.project.layers) == n0, "legacy: merge_down reduces count by 1")
ck(top not in ed.project.layers, "legacy: merged-away layer removed")

# delete layer
ed.set_active(bottom)
n1 = len(ed.project.layers)
ed._delete_layer(bottom)
ck(len(ed.project.layers) == n1 - 1, "legacy: delete removes layer")

# undo/redo round trip
ed.undo()
ck(len(ed.project.layers) == n1, "legacy: undo restores layer")
ed.redo()
ck(len(ed.project.layers) == n1 - 1, "legacy: redo removes again")

# selection PS-style
ed._select_all_layers()
ck(len(ed.selected) == len(ed.project.layers), "legacy: select all")
ed._invert_layer_selection()
ck(len(ed.selected) == 0, "legacy: invert -> empty")

print("\nALL LEGACY REGRESSION TESTS PASSED")
for p in (p1, p2):
    os.remove(p)
