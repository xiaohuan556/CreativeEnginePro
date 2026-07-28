# -*- coding: utf-8 -*-
"""批量 AI 素材处理工作台（独立左侧 Tab，「图片部分」分组下）。

三栏布局：
  ┌─ 素材库（左侧）────────┬─ 预览窗口（中间）───────────┬─ 处理设置（右侧）───────┐
  │ 拖入/双击选取单张或多图   │ 原图 / 效果  +  自动预览      │ 步骤勾选 + 排序          │
  │ 每张图可打勾/取消打勾     │ 画布上直接显示水印红框叠加    │ 选中步骤参数             │
  │ 全选/取消全选/删除未勾    │ 拖动/拉角调整水印区域（实时） │ ▶ 开始 / 暂停 / 停止     │
  │ 选中即预览               │ 拖动中左栏参数同步更新        │ 打开输出                 │
  │ 处理完后标绿              │ 仅处理勾选的图片              │                          │
  └─────────────────────────┴─────────────────────────────┴─────────────────────────┘

只处理素材库中**打勾**的图片。拖入文件/文件夹均可，双击选取多张图片。
"""
import os
import io
import numpy as np
from PIL import Image
from PIL.ImageQt import ImageQt

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QGroupBox, QComboBox, QSpinBox, QCheckBox,
    QTextEdit, QProgressBar, QFileDialog, QScrollArea, QFrame, QStackedWidget,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QSize, QRectF
from PyQt6.QtGui import (
    QDragEnterEvent, QDropEvent, QImage, QPixmap, QIcon, QColor,
    QPainter, QPen,
)

from core.asset_pipeline import BatchProcessor, ALLOWED_EXT
from core.plugins import DISPLAY_ORDER, get_plugin

_GROUP_STYLE = (
    "QGroupBox{border:1px solid #33333a;border-radius:8px;margin-top:10px;"
    "padding:10px 12px 12px;background:#202024;}"
    "QGroupBox::title{subcontrol-origin:margin;left:12px;padding:0 6px;"
    "color:#9aa0aa;font-size:11px;}"
)


# ═══════════════════════ _LoadFilesThread ═══════════════════════
class _LoadFilesThread(QThread):
    """后台为文件列表逐张生成缩略图（PNG bytes），主线程再解码。"""
    item_ready = pyqtSignal(str, str, bytes)   # abs_path, name, png_thumb
    scan_done   = pyqtSignal(int)

    def __init__(self, file_list):
        super().__init__()
        self.file_list = file_list

    def run(self):
        count = 0
        for ap in self.file_list:
            if count >= 1500:
                self.scan_done.emit(count)
                return
            try:
                im = Image.open(ap)
                im.load()
                im.thumbnail((120, 120), Image.LANCZOS)
                if im.mode != "RGBA":
                    im = im.convert("RGBA")
                buf = io.BytesIO()
                im.save(buf, "PNG")
                self.item_ready.emit(ap, os.path.basename(ap), buf.getvalue())
                count += 1
            except Exception:
                continue
        self.scan_done.emit(count)


# ═══════════════════════ _PreviewWorker ═══════════════════════
class _PreviewWorker(QThread):
    done = pyqtSignal(bytes)

    def __init__(self, arr, active_names, ctx):
        super().__init__()
        self.arr = arr
        self.active_names = active_names
        self.ctx = ctx

    def run(self):
        try:
            a = np.ascontiguousarray(self.arr).copy()
            for n in self.active_names:
                p = get_plugin(n)
                if not p: continue
                try:
                    a = p().run(a, self.ctx)
                except Exception:
                    continue
            im = Image.fromarray(a)
            if im.mode != "RGBA":
                im = im.convert("RGBA")
            buf = io.BytesIO()
            im.save(buf, "PNG")
            self.done.emit(buf.getvalue())
        except Exception:
            self.done.emit(b"")


# ═══════════════════════ _PreviewCanvas ═══════════════════════
class _PreviewCanvas(QWidget):
    mask_changed = pyqtSignal(int, int, int, int)
    mask_settled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._qimg = None
        self._img_offset = (0, 0)
        self._img_scale = 1.0
        self._mask_w = 300; self._mask_h = 80
        self._mask_mr = 10; self._mask_mb = 10
        self._mask_visible = True
        self._handle = None
        self._drag_start = (0, 0)
        self._drag_orig = (300, 80, 10, 10)
        self.setMouseTracking(True)
        self.setMinimumHeight(240)

    def set_image(self, qimg):
        self._qimg = qimg
        self._recalc(); self.update()

    def set_mask(self, w, h, mr, mb, visible=True):
        self._mask_w = w; self._mask_h = h
        self._mask_mr = mr; self._mask_mb = mb
        self._mask_visible = visible
        self.update()

    def _recalc(self):
        if self._qimg is None or self._qimg.isNull(): return
        iw, ih = self._qimg.width(), self._qimg.height()
        ww, wh = self.width(), self.height()
        if iw <= 0 or ih <= 0: return
        s = min(ww / iw, wh / ih)
        self._img_scale = s
        self._img_offset = ((ww - iw * s) / 2, (wh - ih * s) / 2)

    def _img_to_widget(self, ix, iy):
        ox, oy = self._img_offset
        return (ox + ix * self._img_scale, oy + iy * self._img_scale)

    def _widget_to_img(self, wx, wy):
        ox, oy = self._img_offset
        return ((wx - ox) / self._img_scale, (wy - oy) / self._img_scale)

    def _mask_img_rect(self):
        if self._qimg is None: return None
        iw, ih = self._qimg.width(), self._qimg.height()
        return (max(0, iw - self._mask_mr - self._mask_w),
                max(0, ih - self._mask_mb - self._mask_h),
                self._mask_w, self._mask_h)

    def _mask_widget_rect(self):
        mr = self._mask_img_rect()
        if mr is None: return None
        x, y, w, h = mr
        wx, wy = self._img_to_widget(x, y)
        return (wx, wy, w * self._img_scale, h * self._img_scale)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#0e0e10"))
        if self._qimg is None or self._qimg.isNull():
            p.setPen(QColor("#666"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "（从左侧素材库选择一张图片以预览）")
            p.end(); return
        ox, oy = self._img_offset
        s = self._img_scale
        p.drawImage(QRectF(ox, oy, self._qimg.width() * s, self._qimg.height() * s), self._qimg)
        if not self._mask_visible: p.end(); return
        mr = self._mask_widget_rect()
        if mr is None: p.end(); return
        mx, my, mw, mh = mr
        pen = QPen(QColor("#ff4444"), 2, Qt.PenStyle.DashLine)
        p.setPen(pen); p.setBrush(QColor(255, 68, 68, 35))
        p.drawRect(QRectF(mx, my, mw, mh))
        hs = 8
        for cx, cy in [(mx, my), (mx + mw, my), (mx, my + mh), (mx + mw, my + mh)]:
            p.fillRect(QRectF(cx - hs / 2, cy - hs / 2, hs, hs), QColor("#ff4444"))
        p.end()

    def _handle_at(self, wpx, wpy):
        mr = self._mask_widget_rect()
        if mr is None: return None
        mx, my, mw, mh = mr
        d = 10
        n = lambda a, b: abs(a - b) < d
        if n(wpy, my) and n(wpx, mx):      return "tl"
        if n(wpy, my) and n(wpx, mx + mw):  return "tr"
        if n(wpy, my + mh) and n(wpx, mx):  return "bl"
        if n(wpy, my + mh) and n(wpx, mx + mw): return "br"
        if n(wpy, my):         return "top"
        if n(wpy, my + mh):    return "bottom"
        if n(wpx, mx):         return "left"
        if n(wpx, mx + mw):    return "right"
        if mx <= wpx <= mx + mw and my <= wpy <= my + mh: return "move"
        return None

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or not self._mask_visible:
            super().mousePressEvent(e); return
        h = self._handle_at(e.position().x(), e.position().y())
        if h is None: super().mousePressEvent(e); return
        self._handle = h
        self._drag_start = self._widget_to_img(e.position().x(), e.position().y())
        self._drag_orig = (self._mask_w, self._mask_h, self._mask_mr, self._mask_mb)
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._handle is None:
            if self._mask_visible:
                h = self._handle_at(e.position().x(), e.position().y())
                cur = {"tl": Qt.CursorShape.SizeFDiagCursor,
                       "br": Qt.CursorShape.SizeFDiagCursor,
                       "tr": Qt.CursorShape.SizeBDiagCursor,
                       "bl": Qt.CursorShape.SizeBDiagCursor,
                       "top": Qt.CursorShape.SizeVerCursor,
                       "bottom": Qt.CursorShape.SizeVerCursor,
                       "left": Qt.CursorShape.SizeHorCursor,
                       "right": Qt.CursorShape.SizeHorCursor,
                       "move": Qt.CursorShape.OpenHandCursor}.get(h)
                self.setCursor(cur if cur else Qt.CursorShape.ArrowCursor)
            return
        ix, iy = self._widget_to_img(e.position().x(), e.position().y())
        ox, oy = self._drag_start
        ow, oh, omr, omb = self._drag_orig
        dx, dy = ix - ox, iy - oy
        w, h, mr, mb = ow, oh, omr, omb
        hdl = self._handle
        if hdl == "move":   mr = max(0, int(omr - dx)); mb = max(0, int(omb - dy))
        elif hdl == "tl":   w = max(1, int(ow - dx));  h = max(1, int(oh - dy))
        elif hdl == "br":   mr = max(0, int(omr + dx)); mb = max(0, int(omb + dy))
        elif hdl == "tr":   h = max(1, int(oh - dy)); mr = max(0, int(omr + dx))
        elif hdl == "bl":   w = max(1, int(ow - dx)); mb = max(0, int(omb + dy))
        elif hdl == "left": w = max(1, int(ow - dx))
        elif hdl == "right": mr = max(0, int(omr + dx))
        elif hdl == "top":  h = max(1, int(oh - dy))
        elif hdl == "bottom": mb = max(0, int(omb + dy))
        self._mask_w = min(w, 4000);  self._mask_h = min(h, 4000)
        self._mask_mr = min(mr, 2000); self._mask_mb = min(mb, 2000)
        self.mask_changed.emit(self._mask_w, self._mask_h, self._mask_mr, self._mask_mb)
        self.update()

    def mouseReleaseEvent(self, e):
        if self._handle is not None:
            self._handle = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.mask_settled.emit()
        super().mouseReleaseEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._recalc(); self.update()


# ═══════════════════════ BatchWorkspace ═══════════════════════
class BatchWorkspace(QWidget):
    sig_start    = pyqtSignal(int)
    sig_progress = pyqtSignal(int, int, float, float)
    sig_item     = pyqtSignal(str, str, str)
    sig_finish   = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._param_groups = {}
        self.processor = BatchProcessor(callbacks={
            "on_start":    lambda t: self.sig_start.emit(t),
            "on_progress": lambda d, t, s, e: self.sig_progress.emit(d, t, s, e),
            "on_item":     lambda r, st, m: self.sig_item.emit(r, st, m),
            "on_finish":   lambda s: self.sig_finish.emit(s),
        })
        self._running       = False
        self._file_list     = []      # 所有已添加文件的绝对路径
        self._lib_items     = {}      # abs_path → QListWidgetItem
        self._load_thread   = None
        self._preview_worker = None
        self._current_arr   = None
        self._orig_qimg     = None
        self._effect_qimg   = None
        self._current_qimg  = None
        self._showing_effect = False
        self._init_ui()
        self.sig_start.connect(self._on_start)
        self.sig_progress.connect(self._on_progress)
        self.sig_item.connect(self._on_item)
        self.sig_finish.connect(self._on_finish)

    # ═══════════════════ UI ═══════════════════════
    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)
        title = QLabel("批量 AI 素材处理")
        title.setStyleSheet("font-size:15px;font-weight:700;color:#e8e8ea;")
        root.addWidget(title)

        main = QHBoxLayout(); main.setSpacing(12)
        main.addLayout(self._make_library_col(), 3)
        main.addLayout(self._make_preview_col(), 6)
        main.addLayout(self._make_settings_col(), 4)
        root.addLayout(main, 1)

        self.progress = QProgressBar(); self.progress.setValue(0)
        root.addWidget(self.progress)
        stat = QHBoxLayout()
        self.lb_count  = QLabel("0 / 0")
        self.lb_speed  = QLabel("速度 —")
        self.lb_eta    = QLabel("ETA —")
        self.lb_gpu    = QLabel("引擎: CPU · 大模型未启用")
        self.lb_fail   = QLabel("失败 0")
        for w in (self.lb_count, self.lb_speed, self.lb_eta, self.lb_gpu):
            w.setStyleSheet("font-size:11px;color:#9aa0aa;")
        self.lb_fail.setStyleSheet("font-size:11px;color:#ff6b6b;")
        stat.addWidget(self.lb_count); stat.addWidget(self.lb_speed)
        stat.addWidget(self.lb_eta); stat.addWidget(self.lb_gpu)
        stat.addStretch(1); stat.addWidget(self.lb_fail)
        root.addLayout(stat)

    # ── 左栏：素材库 ──
    def _make_library_col(self):
        col = QVBoxLayout(); col.setSpacing(6)
        self.lib_stack = QStackedWidget()

        # 页 0 — 引导提示
        welcome = QFrame()
        welcome.setStyleSheet("QFrame{background:#161618;border:1.5px dashed #3a3a3e;border-radius:10px;}")
        wl = QVBoxLayout(welcome); wl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wlab = QLabel("📂 拖入图片 / 双击选取\n支持多张图片或文件夹")
        wlab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wlab.setStyleSheet("font-size:13px;color:#9aa;border:none;background:transparent;")
        wl.addWidget(wlab)
        self.lib_stack.addWidget(welcome)

        # 页 1 — 素材库
        lib_page = QWidget()
        lpl = QVBoxLayout(lib_page); lpl.setContentsMargins(0, 0, 0, 0); lpl.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(QLabel("素材库"))
        head.addStretch(1)
        b_sel = QPushButton("全选"); b_sel.clicked.connect(self._on_lib_sel_all)
        b_none = QPushButton("取消"); b_none.clicked.connect(self._on_lib_sel_none)
        b_del = QPushButton("删除未勾"); b_del.clicked.connect(self._on_lib_del_unchecked)
        for b in (b_sel, b_none, b_del):
            b.setStyleSheet("QPushButton{padding:3px 8px;font-size:10px;}")
            head.addWidget(b)
        lpl.addLayout(head)

        self.lib_list = QListWidget()
        self.lib_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.lib_list.setIconSize(QSize(96, 96))
        self.lib_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.lib_list.setMovement(QListWidget.Movement.Static)
        self.lib_list.setSpacing(6)
        self.lib_list.setAcceptDrops(False)
        self.lib_list.setStyleSheet(
            "QListWidget{background:#161618;border:1px solid #2a2a30;"
            "border-radius:8px;padding:8px;}")
        self.lib_list.setMinimumHeight(220)
        self.lib_list.currentItemChanged.connect(self._on_lib_sel)
        lpl.addWidget(self.lib_list, 1)

        self.lb_lib_count = QLabel("")
        self.lb_lib_count.setStyleSheet("font-size:11px;color:#888;")
        lpl.addWidget(self.lb_lib_count)

        self.lib_stack.addWidget(lib_page)
        self.lib_stack.setCurrentIndex(0)
        col.addWidget(self.lib_stack, 1)
        return col

    # ── 中栏：预览 ──
    def _make_preview_col(self):
        col = QVBoxLayout(); col.setSpacing(6)
        tool = QHBoxLayout()
        self.b_orig = QPushButton("原图"); self.b_effect = QPushButton("效果")
        self.b_orig.setCheckable(True); self.b_effect.setCheckable(True)
        self.b_orig.setChecked(True)
        self.b_orig.clicked.connect(self._on_orig)
        self.b_effect.clicked.connect(self._on_effect)
        self.b_preview = QPushButton("生成预览")
        self.b_preview.clicked.connect(self._gen_preview)
        self.b_auto = QCheckBox("自动预览"); self.b_auto.setChecked(True)
        self.b_auto.toggled.connect(
            lambda v: (self._gen_preview() if (v and self._current_arr is not None) else None))
        for b in (self.b_orig, self.b_effect, self.b_preview):
            b.setStyleSheet(
                "QPushButton{padding:5px 12px;border:1px solid #33333a;"
                "border-radius:6px;background:#232327;color:#cfcfd4;}"
                "QPushButton:checked{background:#3d8ef8;border-color:#3d8ef8;color:#fff;}")
        tool.addWidget(self.b_orig); tool.addWidget(self.b_effect)
        tool.addWidget(self.b_preview); tool.addWidget(self.b_auto)
        tool.addStretch(1); col.addLayout(tool)

        self.canvas = _PreviewCanvas()
        self.canvas.mask_changed.connect(self._on_mask_changed)
        self.canvas.mask_settled.connect(self._on_mask_settled)
        col.addWidget(self.canvas, 1)

        self.lb_dim = QLabel("")
        self.lb_dim.setStyleSheet("font-size:11px;color:#888;")
        col.addWidget(self.lb_dim)
        return col

    # ── 右栏：处理设置 ──
    def _make_settings_col(self):
        col = QVBoxLayout(); col.setSpacing(8)
        col.addWidget(QLabel("处理步骤（勾选启用）"))
        self.plist = QListWidget(); self.plist.setSpacing(2)
        for name in DISPLAY_ORDER:
            cls = get_plugin(name)
            if not cls: continue
            it = QListWidgetItem(); it.setText(cls.LABEL)
            it.setData(Qt.ItemDataRole.UserRole, name)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if name in ("watermark_fixed",)
                             else Qt.CheckState.Unchecked)
            self.plist.addItem(it)
        col.addWidget(self.plist, 1)
        mv = QHBoxLayout()
        b_up = QPushButton("↑ 上移"); b_down = QPushButton("↓ 下移")
        b_up.clicked.connect(lambda: self._move(-1))
        b_down.clicked.connect(lambda: self._move(1))
        mv.addWidget(b_up); mv.addWidget(b_down); col.addLayout(mv)

        col.addWidget(QLabel("参数（选中上方某一步骤时显示）"))
        sa = QScrollArea(); sa.setWidgetResizable(True)
        self.param_box = QWidget(); self._build_params(self.param_box)
        sa.setWidget(self.param_box); sa.setMaximumHeight(220); col.addWidget(sa)

        btn = QHBoxLayout()
        self.b_start  = QPushButton("▶ 开始"); self.b_pause = QPushButton("⏸ 暂停")
        self.b_stop   = QPushButton("⏹ 停止"); self.b_retry = QPushButton("↻ 重试")
        self.b_open   = QPushButton("打开输出")
        self.b_pause.setEnabled(False); self.b_stop.setEnabled(False)
        self.b_retry.setEnabled(False); self.b_open.setEnabled(False)
        self.b_start.clicked.connect(self._on_start_click)
        self.b_pause.clicked.connect(self._on_pause_click)
        self.b_stop.clicked.connect(self._on_stop_click)
        self.b_retry.clicked.connect(self._on_retry_click)
        self.b_open.clicked.connect(self._on_open_click)
        for b in (self.b_start, self.b_pause, self.b_stop, self.b_retry, self.b_open):
            btn.addWidget(b)
        col.addLayout(btn)
        self.log = QTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(90)
        self.log.setStyleSheet("background:#161618;border:1px solid #2a2a30;border-radius:6px;")
        col.addWidget(self.log)
        self.plist.setCurrentRow(0); self._show_params_for(0)
        return col

    def _build_params(self, box):
        v = QVBoxLayout(box); v.setAlignment(Qt.AlignmentFlag.AlignTop); v.setSpacing(0)
        g = QGroupBox("AI 去水印（固定蒙版）"); g.setStyleSheet(_GROUP_STYLE)
        gl = QFormLayout(g)
        self.wm_w  = QSpinBox(); self.wm_w.setRange(1, 4000); self.wm_w.setValue(300)
        self.wm_h  = QSpinBox(); self.wm_h.setRange(1, 4000); self.wm_h.setValue(80)
        self.wm_mr = QSpinBox(); self.wm_mr.setRange(0, 2000); self.wm_mr.setValue(10)
        self.wm_mb = QSpinBox(); self.wm_mb.setRange(0, 2000); self.wm_mb.setValue(10)
        gl.addRow("区域宽(px)", self.wm_w); gl.addRow("区域高(px)", self.wm_h)
        gl.addRow("距右边(px)", self.wm_mr); gl.addRow("距底边(px)", self.wm_mb)
        v.addWidget(g)
        v.addWidget(self._note("拖动预览红框实时调整，右侧数值自动同步"))
        self._param_groups["watermark_fixed"] = g
        g = QGroupBox("AI 超分辨率 x4（可选）"); g.setStyleSheet(_GROUP_STYLE)
        glv = QVBoxLayout(g)
        glv.addWidget(QLabel("需 Real-ESRGAN 模型；缺失时自动跳过。"))
        v.addWidget(g); self._param_groups["superres"] = g
        g = QGroupBox("AI 去噪（可选）"); g.setStyleSheet(_GROUP_STYLE)
        glh = QHBoxLayout(g)
        self.dn_strength = QSpinBox(); self.dn_strength.setRange(1, 15); self.dn_strength.setValue(3)
        glh.addWidget(QLabel("强度")); glh.addWidget(self.dn_strength)
        v.addWidget(g); self._param_groups["denoise"] = g
        g = QGroupBox("改尺寸 / 改比例"); g.setStyleSheet(_GROUP_STYLE); gl = QFormLayout(g)
        self.rs_mode = QComboBox(); self.rs_mode.addItems(["scale", "size", "preset"])
        self.rs_scale = QSpinBox(); self.rs_scale.setRange(1, 20); self.rs_scale.setValue(2)
        self.rs_w = QSpinBox(); self.rs_w.setRange(1, 8000); self.rs_w.setValue(1080)
        self.rs_h = QSpinBox(); self.rs_h.setRange(1, 8000); self.rs_h.setValue(1080)
        self.rs_preset = QComboBox(); self.rs_preset.addItems(["1:1", "9:16", "16:9"])
        gl.addRow("模式", self.rs_mode); gl.addRow("比例(scale)", self.rs_scale)
        gl.addRow("宽(size)", self.rs_w); gl.addRow("高(size)", self.rs_h); gl.addRow("预设(preset)", self.rs_preset)
        v.addWidget(g); self._param_groups["resize"] = g
        g = QGroupBox("批量重命名"); g.setStyleSheet(_GROUP_STYLE); gl = QFormLayout(g)
        self.rn_pattern = QLineEdit("{name}"); gl.addRow("模板", self.rn_pattern)
        gl.addRow(QLabel("支持 {name} 原名 / {num} 4位序号")); v.addWidget(g); self._param_groups["rename"] = g
        g = QGroupBox("格式转换"); g.setStyleSheet(_GROUP_STYLE); glh = QHBoxLayout(g)
        self.cv_format = QComboBox(); self.cv_format.addItems(["png", "jpg", "webp"])
        glh.addWidget(QLabel("输出格式")); glh.addWidget(self.cv_format); v.addWidget(g); self._param_groups["convert"] = g
        g = QGroupBox("压缩 / 画质"); g.setStyleSheet(_GROUP_STYLE); glh = QHBoxLayout(g)
        self.cp_quality = QSpinBox(); self.cp_quality.setRange(1, 100); self.cp_quality.setValue(95)
        glh.addWidget(QLabel("质量(1-100)")); glh.addWidget(self.cp_quality); v.addWidget(g); self._param_groups["compress"] = g

    @staticmethod
    def _note(text):
        lab = QLabel(text); lab.setStyleSheet("font-size:10px;color:#666;padding:4px 2px 8px;"); return lab

    def _show_params_for(self, row):
        if row < 0: return
        it = self.plist.item(row)
        if not it: return
        name = it.data(Qt.ItemDataRole.UserRole)
        for key, g in self._param_groups.items():
            g.setVisible(key == name)

    # ═══════════════════ 输入（文件/文件夹 → _file_list）═══════════════════
    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent):
        paths = []
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if os.path.isdir(p):
                for root, _d, fns in os.walk(p):
                    for fn in fns:
                        if os.path.splitext(fn)[1].lower() in ALLOWED_EXT:
                            paths.append(os.path.join(root, fn))
            elif os.path.isfile(p) and os.path.splitext(p)[1].lower() in ALLOWED_EXT:
                paths.append(p)
        if paths:
            self._add_sources(paths)
        e.acceptProposedAction()

    def mouseDoubleClickEvent(self, e):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择图片", "",
            "图片 (*.png *.jpg *.jpeg *.webp)")
        if files:
            self._add_sources(files)
        super().mouseDoubleClickEvent(e)

    def _add_sources(self, paths):
        new = []
        seen = set(self._file_list)
        for p in paths:
            ap = os.path.abspath(p)
            if ap not in seen:
                seen.add(ap)
                new.append(ap)
        if not new:
            return
        self._file_list.extend(new)
        self.lib_stack.setCurrentIndex(1)
        self._rebuild_library()

    def _rebuild_library(self):
        """用 _file_list 重建缩略图网格。"""
        self.lib_list.clear()
        self._lib_items.clear()
        self.lb_lib_count.setText("加载中…")
        self._load_thread = _LoadFilesThread(self._file_list)
        self._load_thread.item_ready.connect(self._on_lib_item)
        self._load_thread.scan_done.connect(self._on_lib_done)
        self._load_thread.finished.connect(self._on_load_finished)
        self._load_thread.start()

    def _on_load_finished(self):
        w = self.sender()
        if w is self._load_thread:
            self._load_thread = None
        if w is not None:
            w.deleteLater()

    def _on_lib_item(self, abs_path, name, png_bytes):
        im = Image.open(io.BytesIO(png_bytes))
        qimg = ImageQt(im)
        item = QListWidgetItem(QIcon(QPixmap.fromImage(qimg)), name)
        item.setData(Qt.ItemDataRole.UserRole, abs_path)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)   # 默认勾选
        self.lib_list.addItem(item)
        self._lib_items[abs_path] = item

    def _on_lib_done(self, total):
        self.lb_lib_count.setText(f"共 {total} 张")

    def _on_lib_sel(self, cur, _prev):
        if cur is None: return
        ap = cur.data(Qt.ItemDataRole.UserRole)
        if not ap: return
        try:
            arr = np.array(Image.open(ap).convert("RGBA"))
        except Exception as e:
            self._log(f"预览加载失败 {os.path.basename(ap)}: {e}")
            return
        self._current_arr = arr
        self._effect_qimg = None; self._showing_effect = False
        self.b_orig.setChecked(True); self.b_effect.setChecked(False)
        self._orig_qimg = self._arr_to_qimage(arr)
        self._current_qimg = self._orig_qimg
        self.canvas.set_image(self._orig_qimg)
        self._sync_canvas_mask()
        self.lb_dim.setText(f"原图 {arr.shape[1]}×{arr.shape[0]}")
        if self.b_auto.isChecked():
            self._gen_preview()

    # ── 素材库工具栏 ──
    def _on_lib_sel_all(self):
        for i in range(self.lib_list.count()):
            it = self.lib_list.item(i)
            if it: it.setCheckState(Qt.CheckState.Checked)

    def _on_lib_sel_none(self):
        for i in range(self.lib_list.count()):
            it = self.lib_list.item(i)
            if it: it.setCheckState(Qt.CheckState.Unchecked)

    def _on_lib_del_unchecked(self):
        """删除所有未勾选的素材（缩小列表）。"""
        to_remove = []
        for i in range(self.lib_list.count() - 1, -1, -1):
            it = self.lib_list.item(i)
            if it and it.checkState() != Qt.CheckState.Checked:
                ap = it.data(Qt.ItemDataRole.UserRole)
                to_remove.append((i, ap))
        for i, ap in to_remove:
            if ap in self._file_list:
                self._file_list.remove(ap)
            self._lib_items.pop(ap, None)
            self.lib_list.takeItem(i)
        self.lb_lib_count.setText(f"共 {self.lib_list.count()} 张")

    # ═══════════════════ 水印蒙版 ═══════════════════════
    def _sync_canvas_mask(self):
        wm_active = "watermark_fixed" in self._active_plugins()
        visible = wm_active and not self._showing_effect
        self.canvas.set_mask(self.wm_w.value(), self.wm_h.value(),
                             self.wm_mr.value(), self.wm_mb.value(), visible=visible)

    def _on_mask_changed(self, w, h, mr, mb):
        self.wm_w.blockSignals(True); self.wm_w.setValue(w); self.wm_w.blockSignals(False)
        self.wm_h.blockSignals(True); self.wm_h.setValue(h); self.wm_h.blockSignals(False)
        self.wm_mr.blockSignals(True); self.wm_mr.setValue(mr); self.wm_mr.blockSignals(False)
        self.wm_mb.blockSignals(True); self.wm_mb.setValue(mb); self.wm_mb.blockSignals(False)
        self._effect_qimg = None
        self.lb_dim.setText(f"水印区域 {w}×{h}  距右 {mr}  距底 {mb}")

    def _on_mask_settled(self):
        if self.b_auto.isChecked() and self._effect_qimg is None:
            self._gen_preview()

    # ═══════════════════ 预览 ═══════════════════════
    @staticmethod
    def _arr_to_qimage(arr):
        arr = np.ascontiguousarray(arr)
        h, w = arr.shape[:2]
        c = arr.shape[2] if arr.ndim == 3 else 1
        if c == 4:       fmt = QImage.Format.Format_RGBA8888
        elif c == 3:     fmt = QImage.Format.Format_RGB888
        else:            fmt = QImage.Format.Format_Grayscale8
        return QImage(arr.tobytes(), w, h, arr.strides[0], fmt).copy()

    def _on_orig(self):
        self._showing_effect = False
        self.b_orig.setChecked(True); self.b_effect.setChecked(False)
        self.canvas.set_image(self._orig_qimg)
        self._sync_canvas_mask()
        if self._current_arr is not None:
            self.lb_dim.setText(f"原图 {self._current_arr.shape[1]}×{self._current_arr.shape[0]}")

    def _on_effect(self):
        self.b_effect.setChecked(True); self.b_orig.setChecked(False)
        if self._effect_qimg is None:
            self._gen_preview()
        else:
            self._showing_effect = True
            self.canvas.set_image(self._effect_qimg)
            self.canvas.set_mask(self.wm_w.value(), self.wm_h.value(),
                                 self.wm_mr.value(), self.wm_mb.value(), visible=False)

    def _gen_preview(self):
        if self._current_arr is None: return
        active = self._active_plugins()
        if not active:
            self.lb_dim.setText("未勾选处理步骤"); return
        if self._preview_worker is not None and self._preview_worker.isRunning():
            return
        self.lb_dim.setText("生成预览中…")
        ctx = self._build_ctx(active)
        self._preview_worker = _PreviewWorker(self._current_arr, active, ctx)
        self._preview_worker.done.connect(self._on_preview)
        self._preview_worker.finished.connect(self._on_preview_finished)
        self._preview_worker.start()

    def _on_preview_finished(self):
        w = self.sender()
        if w is self._preview_worker:
            self._preview_worker = None
        if w is not None:
            w.deleteLater()

    def _on_preview(self, png_bytes):
        if not png_bytes:
            self.lb_dim.setText("效果生成失败（检查步骤参数）"); return
        try:
            im = Image.open(io.BytesIO(png_bytes)); qimg = ImageQt(im)
        except Exception:
            self.lb_dim.setText("效果生成失败"); return
        self._effect_qimg = qimg
        self.lb_dim.setText(f"���果 {qimg.width()}×{qimg.height()}")
        if self.b_effect.isChecked():
            self._showing_effect = True
            self.canvas.set_image(qimg)
            self.canvas.set_mask(self.wm_w.value(), self.wm_h.value(),
                                 self.wm_mr.value(), self.wm_mb.value(), visible=False)

    # ═══════════════════ 管线 ═══════════════════════
    def _move(self, delta):
        row = self.plist.currentRow()
        if row < 0: return
        new = row + delta
        if 0 <= new < self.plist.count():
            it = self.plist.takeItem(row)
            self.plist.insertItem(new, it)
            self.plist.setCurrentRow(new)

    def _active_plugins(self):
        order = []
        for i in range(self.plist.count()):
            it = self.plist.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                order.append(it.data(Qt.ItemDataRole.UserRole))
        return order

    def _build_ctx(self, active):
        ctx = {}
        for name in active:
            if name == "watermark_fixed":
                ctx["watermark"] = {"width": self.wm_w.value(), "height": self.wm_h.value(),
                                    "margin_right": self.wm_mr.value(),
                                    "margin_bottom": self.wm_mb.value()}
            elif name == "denoise":
                ctx["denoise"] = {"enabled": True, "strength": self.dn_strength.value()}
            elif name == "resize":
                mode = self.rs_mode.currentText(); d = {"enabled": True, "mode": mode}
                if mode == "scale": d["scale"] = self.rs_scale.value()
                elif mode == "size": d["width"] = self.rs_w.value(); d["height"] = self.rs_h.value()
                else: d["preset"] = self.rs_preset.currentText()
                ctx["resize"] = d
            elif name == "rename":
                ctx["rename"] = {"pattern": self.rn_pattern.text().strip()}
            elif name == "convert":
                ctx["convert"] = {"format": self.cv_format.currentText()}
            elif name == "compress":
                ctx["compress"] = {"quality": self.cp_quality.value()}
        return ctx

    # ═══════════════════ 控制 ═══════════════════════
    def _checked_paths(self):
        """返回素材库中所有勾选项的绝对路径列表。"""
        out = []
        for i in range(self.lib_list.count()):
            it = self.lib_list.item(i)
            if it and it.checkState() == Qt.CheckState.Checked:
                out.append(it.data(Qt.ItemDataRole.UserRole))
        return out

    def _on_start_click(self):
        checked = self._checked_paths()
        if not checked:
            self._log("请至少勾选一张要处理的图片")
            return
        active = self._active_plugins()
        if not active:
            self._log("请至少勾选一个处理步骤")
            return
        ctx = self._build_ctx(active)
        plugins = [get_plugin(n)() for n in active]

        # 取第一个文件的目录作为"输入目录"，输出 = 该目录 + _output
        inp = os.path.dirname(checked[0])
        out = inp + "_output"

        # monkey‑patch _scan：只处理勾选文件，rel=basename 适合存盘子目录
        self.processor._scan = lambda: [(i, ap, os.path.basename(ap))
                                        for i, ap in enumerate(checked)]
        self.processor.configure(inp, out, plugins, ctx)
        self.processor.start()

    def _on_pause_click(self):
        if self._running:
            self.processor.pause()
            self.b_pause.setText("▶ 继续")
            self.b_pause.disconnect()
            self.b_pause.clicked.connect(self._on_resume_click)
            self._log("已暂停")

    def _on_resume_click(self):
        self.processor.resume()
        self.b_pause.setText("⏸ 暂停")
        self.b_pause.disconnect()
        self.b_pause.clicked.connect(self._on_pause_click)
        self._log("已继续")

    def _on_stop_click(self):
        self.processor.stop()
        self._log("已发送停止信号")

    def _on_retry_click(self):
        # 文件列表模式重试：手动补 jobs，避免 processor._scan 用旧目录
        checked = self._checked_paths()
        if not checked:
            self._log("无勾选项，无法重试")
            return
        self._log("重试失败项…")
        failed_map = {rel: err for rel, err in self.processor.failed}
        jobs = []
        for i, ap in enumerate(checked):
            rel = os.path.basename(ap)
            if rel in failed_map:
                jobs.append((i, ap, rel))
        if not jobs:
            self._log("没有可重试的失败项"); return
        self.processor.failed = []
        # 用 _run(jobs) 直接跑
        self.processor.total = len(jobs)
        self.processor.done = 0; self.processor.ok = 0; self.processor.skipped = 0
        self.processor.start_time = __import__("time").time()
        self.processor._running = True
        self.processor._stop.clear(); self.processor._pause.clear()
        self.processor._emit("on_start", len(jobs))
        import threading
        t_prod = threading.Thread(target=lambda: (self.processor._producer(jobs)), daemon=True)
        t_cons = threading.Thread(target=self.processor._consumer, daemon=True)
        t_sav  = threading.Thread(target=self.processor._saver, daemon=True)
        self.processor._threads = [t_prod, t_cons, t_sav]
        for t in self.processor._threads:
            t.start()

    def _on_open_click(self):
        checked = self._checked_paths()
        if not checked:
            return
        out = os.path.dirname(checked[0]) + "_output"
        if os.path.isdir(out) and hasattr(os, "startfile"):
            os.startfile(out)

    # ═══════════════════ 信号 → UI ═══════════════════════
    def _on_start(self, total):
        self._running = True
        self.progress.setMaximum(max(1, total)); self.progress.setValue(0)
        self.lb_count.setText(f"0 / {total}")
        self.b_start.setEnabled(False); self.b_pause.setEnabled(True)
        self.b_stop.setEnabled(True); self.b_retry.setEnabled(False)
        self.b_open.setEnabled(False); self.lb_fail.setText("失败 0")
        checked = self._checked_paths()
        self._log(f"开始处理：共 {total} 张（从 {len(self._file_list)} 张素材中勾选的 {len(checked)} 张）")

    def _on_progress(self, done, total, speed, eta):
        self.progress.setValue(done); self.lb_count.setText(f"{done} / {total}")
        self.lb_speed.setText(f"速度 {speed:.1f} 张/s")
        self.lb_eta.setText(f"ETA {int(eta)}s" if eta > 0 else "ETA —")

    def _on_item(self, rel, status, msg):
        if status == "done":
            it = self._lib_items.get(rel)
            if it:
                it.setForeground(QColor("#3ddc84"))
            return
        if status == "failed":
            self._log(f"✗ 失败 {rel}：{msg}")
            self.lb_fail.setText(f"失败 {len(self.processor.failed) if self.processor.failed else 0}")
        elif status == "skipped":
            self._log(f"– 跳过 {rel}（已取消）")

    def _on_finish(self, s):
        self._running = False
        self.b_start.setEnabled(True); self.b_pause.setEnabled(False)
        self.b_stop.setEnabled(False); self.b_retry.setEnabled(s["failed"] > 0)
        self.b_open.setEnabled(True); self.lb_fail.setText(f"失败 {s['failed']}")
        self._log(f"完成：成功 {s['ok']} / 失败 {s['failed']} / 跳过 {s['skipped']} / 共 {s['total']}")

    def _log(self, msg):
        self.log.append(msg)
