import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import importlib.util
src_path = r"C:\Users\A\Desktop\CreativeEnginePro\ui\image_editor.py"
src = open(src_path, encoding="utf-8").read()
mod = type(sys)("ie")
mod.__file__ = src_path
exec(compile(src, src_path, "exec"), mod.__dict__)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage, QColor
import numpy as np

app = QApplication([])
ed = mod.ImageEditorWidget()

# 造一张临时图片用于导入
tmp = tempfile.mktemp(suffix=".png")
qi = QImage(200, 150, QImage.Format.Format_RGBA8888)
qi.fill(QColor(255, 0, 0, 255))
qi.save(tmp)

def ck(cond, msg):
    assert cond, "FAIL: " + msg
    print("OK:", msg)

# ── 1. 传统单画布：导入图片，画布匹配图片尺寸 ──
ed.add_image_from_path(tmp)
ck(len(ed.project.layers) == 1, "legacy: 1 layer in project")
ck(ed.project.w == 200 and ed.project.h == 150, "legacy: canvas matched image size")

# ── 2. 添加第一个画板：把当前画布整体转成画板，内容保留 ──
ed._add_artboard("画板1", 200, 150)
ck(len(ed.project.artboards) == 1, "artboard: 1 artboard created")
ck(len(ed.project.layers) == 0, "artboard: project.layers emptied")
ab1 = ed.project.artboards[0]
ck(len(ab1.layers) == 1, "artboard: previous layer preserved inside artboard")
ck(ed.active_artboard is ab1, "artboard: active set to first")
ck(ab1.w == 200 and ab1.h == 150, "artboard: size from canvas")

# ── 3. 画板模式下导入新图片 → 进入当前激活画板（本地坐标）──
tmp2 = tempfile.mktemp(suffix=".png")
qi2 = QImage(400, 300, QImage.Format.Format_RGBA8888)
qi2.fill(QColor(0, 255, 0, 255))
qi2.save(tmp2)
ed.add_image_from_path(tmp2)
ck(len(ab1.layers) == 2, "artboard: new image added into active artboard")
newly = ab1.layers[-1]
ck(abs(newly.x - ab1.w / 2.0) < 1e-6, "artboard: new layer centered in artboard-local coords")
ck(newly.x <= ab1.w and newly.y <= ab1.h, "artboard: local coords within artboard")

# ── 4. 文档边界（含边距 200）──
dw, dh = ed._doc_bounds()
ck(dw == 200 + 200 and dh == 150 + 200, f"doc bounds = artboard + margin (got {dw}x{dh})")

# ── 5. 渲染单个画板 ──
buf = ed._render_artboard(ab1)
ck(buf.width() == ab1.w and buf.height() == ab1.h, "render_artboard size matches artboard")

# ── 6. 添加第二个画板：排布在右侧（间距 80）──
ed._add_artboard("画板2", 500, 500)
ab2 = ed.project.artboards[1]
ck(ab2.x == 200 + 80, f"artboard2 placed to right (x={ab2.x})")
ck(len(ed.project.artboards) == 2, "two artboards")
dw2, dh2 = ed._doc_bounds()
ck(dw2 == 200 + 80 + 500 + 200, f"doc width after 2nd artboard = {dw2}")

# ── 7. 渲染文档合成（尺寸=文档边界）──
doc = ed._render_doc_composite()
ck(doc.width() == dw2 and doc.height() == dh2, "render_doc_composite doc-sized")

# ── 8. 图层面板：画板模式下生成分组头 ──
ed._refresh_layers()
hdr_count = 0
for i in range(ed.layer_list.count()):
    w = ed.layer_list.itemWidget(ed.layer_list.item(i))
    if isinstance(w, mod.ArtboardHeaderWidget):
        hdr_count += 1
ck(hdr_count == 2, "layer panel shows 2 artboard headers")

# ── 9. 点击画板头 → 切换激活画板 ──
ed._on_list_click()  # no item -> safe
ed._set_active_artboard(ab2)
ck(ed.active_artboard is ab2, "set_active_artboard switches")

# ── 10. 删除第二个画板（还剩一个）──
ed._delete_artboard(ab2)
ck(len(ed.project.artboards) == 1, "after delete: 1 artboard remains")
ck(ed.active_artboard is ab1, "after delete: active falls back to remaining")

# ── 11. 删除最后一个画板 → 退回传统单画布（内容交还）──
ed._delete_artboard(ab1)
ck(len(ed.project.artboards) == 0, "after delete last: no artboards")
ck(len(ed.project.layers) >= 1, "legacy restored: layers returned to project")
ck(ed.active_artboard is None, "legacy restored: active_artboard None")

# ── 12. 撤销快照包含画板信息（round-trip）──
ed._add_artboard("A", 300, 300)
snap = ed._snapshot()
ck("artboards" in snap and len(snap["artboards"]) == 1, "snapshot includes artboards")
ed._add_artboard("B", 100, 100)
ed._restore(snap)
ck(len(ed.project.artboards) == 1 and ed.project.artboards[0].name == "A",
    "restore rebuilds single artboard A")

# ── 13. 剪贴板粘贴进入激活画板 ──
clip = QImage(80, 60, QImage.Format.Format_RGBA8888); clip.fill(QColor(0, 0, 255, 255))
QApplication.clipboard().setImage(clip)
abA = ed.project.artboards[0]
before = len(abA.layers)
ed.paste_image()
ck(len(abA.layers) == before + 1, "paste_image adds layer into active artboard")

print("\nALL ARTBOARD TESTS PASSED")
os.remove(tmp); os.remove(tmp2)
