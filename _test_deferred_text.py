import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import importlib.util
src_path = r"C:\Users\A\Desktop\CreativeEnginePro\ui\image_editor.py"
src = open(src_path, encoding="utf-8").read()
mod = type(sys)("ie")
mod.__file__ = src_path
exec(compile(src, src_path, "exec"), mod.__dict__)

from PyQt6.QtWidgets import QApplication, QSpinBox
from PyQt6.QtGui import QKeyEvent, QInputMethodEvent, QMouseEvent
from PyQt6.QtCore import QPointF
from PyQt6.QtCore import QEvent, Qt

app = QApplication([])
ed = mod.ImageEditorWidget()

def ck(cond, msg):
    assert cond, "FAIL: " + msg
    print("OK:", msg)

def key(k, text=""):
    e = QKeyEvent(QEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier, text)
    ed._text_edit_key(e)

def ime_commit(s):
    e = QInputMethodEvent("", [])
    e.setCommitString(s)
    ed.inputMethodEvent(e)

# ── 干净工程（传统单画布）──
ed.new_project()
ed.active_artboard = None
ck(len(ed.project.layers) == 0, "start: 0 layers")

# 场景 1：文字工具点击空白 → 不应立刻建层（PS 式延迟建层）
ed._start_text_edit(None, is_new=True, pt=None)
ck(len(ed.project.layers) == 0, "click-empty: layer NOT added immediately")
ck(ed._text_edit_added is False, "click-empty: _text_edit_added False")
ck(ed._text_editing is True, "click-empty: in editing state")

# 场景 2：首次键入字符 → 真正加入工程
key(Qt.Key.Key_H, "H")
ck(len(ed.project.layers) == 1, "type-H: layer committed to project")
ck(ed._text_edit_added is True, "type-H: _text_edit_added True")
ck(ed._edit_flat == "H", "type-H: buffer == 'H'")
ck(ed.project.layers[-1].text == "", "type-H: layer.text still empty (not yet end)")
ed._end_text_edit(save=True)
ck(ed.project.layers[-1].text == "H", "end-save: layer.text == 'H'")
ck(len(ed.project.layers) == 1, "end-save: exactly 1 layer")

# 场景 3：点击空白但不输入 → 退出时丢弃，不留空图层
before = len(ed.project.layers)
ed._start_text_edit(None, is_new=True, pt=None)
ck(len(ed.project.layers) == before, "click-no-type: still not added")
ed._end_text_edit(save=True)
ck(len(ed.project.layers) == before, "click-no-type: empty new layer discarded")
ck(all(l.text.strip() != "" or l.kind != "text" for l in ed.project.layers),
    "click-no-type: no empty text layer remains")

# 场景 4：输入后按 Escape 取消 → 新层不残留
ed._start_text_edit(None, is_new=True, pt=None)
key(Qt.Key.Key_W, "W")
ck(len(ed.project.layers) == before + 1, "esc-setup: layer committed")
ed._text_edit_key(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                             Qt.KeyboardModifier.NoModifier, ""))
ck(len(ed.project.layers) == before, "escape: new layer discarded")

# 场景 5：IME 提交中文 → 建层
ed._start_text_edit(None, is_new=True, pt=None)
ime_commit("你好")
ck(len(ed.project.layers) == before + 1, "ime: commit creates layer")
ck(ed._text_edit_added is True, "ime: _text_edit_added True")
ed._end_text_edit(save=True)
ck(ed.project.layers[-1].text == "你好", "ime: text committed")

# 场景 6：编辑已有文字层（is_new=False）→ 立即计入（已在工程里）
existing = ed.project.layers[-1]
ed._start_text_edit(existing, is_new=False)
ck(ed._text_edit_added is True, "edit-existing: already added")
ck(len(ed.project.layers) == before + 1, "edit-existing: count unchanged")
key(Qt.Key.Key_X, "X")
ed._end_text_edit(save=True)
ck(existing.text == "你好X", "edit-existing: appended X")

# 场景 7：多画板模式 → 新建文字进入激活画板
ed.new_project()
ed._add_artboard("AB", 400, 300)
ab = ed.project.artboards[-1]
ed.active_artboard = ab
ck(len(ab.layers) == 0, "artboard: starts empty")
ed._start_text_edit(None, is_new=True, pt=None)
key(Qt.Key.Key_A, "A")
ck(len(ab.layers) == 1, "artboard: new text into artboard")
ck(len(ed.project.layers) == 0, "artboard: not in root project")
ed._end_text_edit(save=True)
ck(ab.layers[-1].text == "A", "artboard: text set")

# 场景 8：多画板点击空白不输入 → 不残留于画板
abn = len(ab.layers)
ed._start_text_edit(None, is_new=True, pt=None)
ed._end_text_edit(save=True)
ck(len(ab.layers) == abn, "artboard: empty new discarded from artboard")

# 场景 9（关键回归）：输入时画布必须实时显示（叠加层须烤进显示的 pixmap，
# 而不是画在 setPixmap 之后已分离的副本上）
ed.new_project()
ed.active_artboard = None
ed._start_text_edit(None, is_new=True, pt=None)
ed._redraw()
px_empty = ed.view.composite_item.pixmap()
key(Qt.Key.Key_H, "H")   # 内部会 _redraw()
px_typed = ed.view.composite_item.pixmap()
def _region_sum(px):
    im = px.toImage()
    # 编辑器框在 pt=None 时居中（默认画布 1080，框 200x80）
    s = 0
    for yy in range(500, 581, 4):
        for xx in range(440, 641, 4):
            c = im.pixelColor(xx, yy)
            s += c.red() + c.green() * 2 + c.blue() * 3 + c.alpha() * 4
    return s
ck(_region_sum(px_empty) != _region_sum(px_typed),
   "visibility: typed 'H' is baked into displayed pixmap (not a detached copy)")
ed._end_text_edit(save=True)

# 场景 10（关键回归）：焦点在属性面板 QSpinBox 时，Delete 仍应删除选中文字层
# （旧守卫把 QSpinBox/QComboBox 也排除，导致选文字层后焦点落在属性输入框、
#  按 Delete 永远删不掉图层）。仅真正文本输入框(QLineEdit/QPlainTextEdit)才让行。
ed.new_project(); ed.active_artboard=None
ed._start_text_edit(None, is_new=True, pt=None)
key(Qt.Key.Key_Z, "Z")
ed._end_text_edit(save=True)
n_del = len(ed.project.layers)
assert n_del == 1, "scenario10: one text layer present"
spin = QSpinBox()
orig_focus = QApplication.focusWidget
QApplication.focusWidget = staticmethod(lambda: spin)  # 强制焦点在一个属性 QSpinBox 上
try:
    del_ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete,
                       Qt.KeyboardModifier.NoModifier, "")
    res = ed.eventFilter(ed, del_ev)   # 走真实 eventFilter 路径
    ck(res is True, "scenario10: Delete on spinbox focus is consumed (layer deleted)")
    ck(len(ed.project.layers) == n_del - 1,
       "scenario10: text layer deleted even when focus on QSpinBox")
finally:
    QApplication.focusWidget = orig_focus

# 场景 11（关键回归）：文字工具「单击」不应弹出/新建文字框，「双击」才弹。
# 避免用户反馈的「一点画布就一堆空文字框」。
ed.new_project(); ed.active_artboard=None
ed.view.setFixedSize(900, 600)   # 给视口一个尺寸，保证 mapToScene 可用
def _mouse_click(kind):
    # kind: 'press' -> 单击；'dbl' -> 双击
    t = (QEvent.Type.MouseButtonPress if kind == "press"
         else QEvent.Type.MouseButtonDblClick)
    QApplication.setActiveWindow(ed)
    ev = QMouseEvent(t, QPointF(30, 30), Qt.MouseButton.LeftButton,
                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    if kind == "press":
        ed.view.mousePressEvent(ev)
    else:
        ed.view.mouseDoubleClickEvent(ev)

ed.set_tool(mod.Tool.TEXT)
# 单击空白 → 不应进入编辑态，也不应新增图层
_mouse_click("press")
ck(not ed._text_editing, "scenario11: single click on blank does NOT enter text edit")
ck(len(ed.project.layers) == 0, "scenario11: single click on blank adds NO layer")
# 再双击同一处 → 应弹出文字框（进入编辑态，pending 层待输入）
_mouse_click("dbl")
ck(ed._text_editing, "scenario11: double click on blank enters text edit")
ck(len(ed.project.layers) == 0, "scenario11: double click makes pending layer (not yet added)")
# 输入文字后落定，图层才真正进入工程
key(Qt.Key.Key_H, "H"); key(Qt.Key.Key_I, "I")
ed._end_text_edit(save=True)
ck(len(ed.project.layers) == 1, "scenario11: after typing, layer committed to project")
# 再次单击已存在的文字层（仍用单击）→ 只选中，不进入编辑（双击才编辑）
ed.set_tool(mod.Tool.TEXT)
_mouse_click("press")
ck(not ed._text_editing, "scenario11: single click on existing text only selects (no edit)")
ck(len(ed.project.layers) == 1, "scenario11: single click on existing text keeps layer count")

print("\nALL DEFERRED-TEXT-LAYER TESTS PASSED")
