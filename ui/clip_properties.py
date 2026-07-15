"""
clip_properties.py — 片段属性面板
根据选中的片段类型显示对应的可编辑属性
支持：VideoClip / AudioClip / SubtitleBlock 的样式完整编辑
"""
from __future__ import annotations
import os
import logging
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QSlider,
    QPushButton, QComboBox, QColorDialog, QSpinBox,
    QDoubleSpinBox, QTextEdit, QGroupBox, QFormLayout, QScrollArea,
    QSizePolicy, QFrame, QDialog
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QFont, QWheelEvent, QPainter, QPen, QBrush, QPainterPath
from .widgets import CheckMarkBox


# ── 关键帧菱形按钮 ──
class DiamondButton(QPushButton):
    """自定义菱形关键帧按钮：空心◇或实心◆"""
    def __init__(self, prop_name: str, parent=None):
        super().__init__(parent)
        self._prop = prop_name
        self._active = False  # 空心 / 实心
        self._hover = False
        self.setFixedSize(20, 20)
        self.setToolTip(f"切换 {prop_name} 关键帧")
        self.setStyleSheet(
            "QPushButton { background:transparent; border:none; }"
            "QToolTip { background:#2a2a2a; color:#fff; border:1px solid #555; "
            "padding:2px 6px; font-size:11px; }"
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    @property
    def prop_name(self): return self._prop

    def set_active(self, active: bool):
        self._active = active
        self.update()

    def enterEvent(self, e):
        self._hover = True; self.update()
    def leaveEvent(self, e):
        self._hover = False; self.update()

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainterPath
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        r = min(w, h) / 2 - 2  # 菱形半径

        path = QPainterPath()
        path.moveTo(cx, cy - r)
        path.lineTo(cx + r, cy)
        path.lineTo(cx, cy + r)
        path.lineTo(cx - r, cy)
        path.closeSubpath()

        if self._active:
            p.setBrush(QColor("#00eaff"))
            p.setPen(Qt.PenStyle.NoPen)
        elif self._hover:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor("#8ab4f8"), 1.5))
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor("#555"), 1))

        p.drawPath(path)
        p.end()


# ── 禁用滚轮的子类 ──
class NoWheelSpinBox(QSpinBox):
    """禁止鼠标滚轮改变数值，避免误操作"""
    def wheelEvent(self, e: QWheelEvent):
        e.ignore()

class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """禁止鼠标滚轮改变数值"""
    def wheelEvent(self, e: QWheelEvent):
        e.ignore()

class NoWheelSlider(QSlider):
    """禁止鼠标滚轮改变数值"""
    def wheelEvent(self, e: QWheelEvent):
        e.ignore()

from core.edit_engine import VideoClip, AudioClip, SubtitleBlock, EditTimeline, interpolate_keyframes
from core.slideshow_engine import TRANSITIONS, TRANS_DESCS
from ui.replace_video_dialog import ReplaceVideoDialog


def _color_btn(color_hex: str) -> QPushButton:
    btn = QPushButton()
    btn.setFixedSize(40, 22)
    btn.setStyleSheet(
        f"background:{color_hex}; border:1px solid #555; border-radius:3px;"
    )
    return btn


def _pick_color(parent, current: str) -> str:
    """弹出颜色选择器，返回 HEX 字符串（带#），取消则返回原色"""
    dlg = QColorDialog(QColor(current), parent)
    if dlg.exec():
        return dlg.selectedColor().name()
    return current


FORM_LABEL_STYLE = "color:#888; font-size:12px;"
GROUP_STYLE = """
    QGroupBox {
        color:#888; font-size:11px; border:1px solid #333;
        border-radius:4px; margin-top:8px; padding-top:4px;
    }
    QGroupBox::title { subcontrol-origin:margin; left:8px; }
"""
SPINBOX_STYLE = """
    QSpinBox, QDoubleSpinBox {
        background:#2a2a2a; color:#ccc; border:1px solid #444;
        border-radius:3px; padding:2px 4px;
    }
"""
COMBO_STYLE = """
    QComboBox {
        background:#2a2a2a; color:#ccc; border:1px solid #444;
        border-radius:3px; padding:2px 6px;
    }
    QComboBox QAbstractItemView { background:#2a2a2a; color:#ccc; selection-background-color:#3d8ef8; }
"""
LINE_STYLE = """
    QLineEdit { background:#2a2a2a; color:#ccc; border:1px solid #444; border-radius:3px; padding:2px 4px; }
"""
CHECK_STYLE = "QCheckBox { color:#ccc; font-size:12px; }"


SLIDER_STYLE = """
    QSlider::groove:horizontal {
        height:4px; background:#333; border-radius:2px;
    }
    QSlider::handle:horizontal {
        width:12px; height:12px; margin:-4px 0;
        background:#3d8ef8; border-radius:6px;
    }
    QSlider::handle:horizontal:hover { background:#5aa0ff; }
    QSlider::sub-page:horizontal { background:#3d8ef8; border-radius:2px; }
"""


class ClipPropertiesPanel(QWidget):
    """属性面板主体，根据选中类型切换显示内容"""
    property_changed = pyqtSignal()  # 任何属性变更后发出
    seek_requested = pyqtSignal(float)  # 请求跳转到指定时间（秒）

    def __init__(self, timeline: EditTimeline, parent=None):
        super().__init__(parent)
        self.tl = timeline
        self._clip = None
        self._track = ""
        self._blocking = False
        self._sync_subs = True  # 字幕同步开关（默认开启，同步所有字幕）
        self._current_sec = None  # 当前播放头时间
        self._tx_sliders: dict = {}  # 视频变换滑块引用（pos_x/pos_y/scale/rotation）
        self._kf_buttons: list = []  # 预收集的 DiamondButton，避免每次播放头移动时递归遍历 widget 树
        # 字幕同步 debounce：快速拖拽滑块时避免逐帧全量同步卡顿
        self._sync_debounce = QTimer(self)
        self._sync_debounce.setSingleShot(True)
        self._sync_debounce.setInterval(80)
        self._sync_debounce.timeout.connect(self._do_sync_to_all_subs)
        self._pending_sync_attrs: dict = {}  # {attr: value} 待同步的属性
        # 滑块拖拽撤回（press 捕获快照，release 推入栈，避免每个 tick 都存）
        self._slider_drag_snapshot = None

        self.setStyleSheet("background:#1e1e1e;")
        self.installEventFilter(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 标题行
        title_bar = QWidget()
        title_bar.setStyleSheet("background:#1a1a1a; border-bottom:1px solid #333;")
        title_bar.setFixedHeight(28)
        tl = QHBoxLayout(title_bar)
        tl.setContentsMargins(8, 0, 4, 0)
        tl.setSpacing(4)

        self._title = QLabel("属性")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setStyleSheet("color:#aaa; font-size:12px; font-weight:500; background:transparent; border:none;")
        tl.addWidget(self._title, 1)
        root.addWidget(title_bar)

        # 字幕同步对号框（选择字幕时显示，其他隐藏）
        self._sync_bar = QWidget()
        self._sync_bar.setStyleSheet("background:#252525; border-bottom:1px solid #333;")
        self._sync_bar.setFixedHeight(26)
        sl = QHBoxLayout(self._sync_bar)
        sl.setContentsMargins(10, 0, 10, 0)
        sl.setSpacing(4)

        self._sync_cb = CheckMarkBox("字幕同步")
        self._sync_cb.setChecked(True)
        self._sync_cb.setToolTip("修改任意字幕属性自动同步到所有字幕\n开启时立刻对所有字幕执行一次全量同步")
        self._sync_cb.setStyleSheet(CHECK_STYLE)
        self._sync_cb.toggled.connect(self._on_sync_cb_toggled)
        sl.addWidget(self._sync_cb)
        sl.addStretch()
        self._sync_bar.setVisible(False)  # 默认隐藏，选中字幕时显示
        root.addWidget(self._sync_bar)

        # 滚动内容区
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("QScrollArea { border:none; }")
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 禁止焦点自动滚动（避免点击滑块/按钮时乱跳）
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        scroll = self._scroll

        self._content = QWidget()
        self._content.setStyleSheet("background:#1e1e1e;")
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(8, 8, 8, 8)
        self._content_layout.setSpacing(8)

        self._placeholder = QLabel("选中时间线上的片段\n查看和编辑属性")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color:#555; font-size:12px;")
        self._content_layout.addWidget(self._placeholder)
        self._content_layout.addStretch()

        scroll.setWidget(self._content)
        root.addWidget(scroll, 1)

    def eventFilter(self, obj, event):
        """拦截 Delete/Backspace 键防止误删轨道片段"""
        from PyQt6.QtCore import QEvent
        if event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                # 如果有 QLineEdit / QTextEdit 聚焦，放行
                fw = self.window().focusWidget()
                if isinstance(fw, (QLineEdit, QTextEdit)):
                    return False
                # 否则吞掉 Delete 事件（防止误删轨道文件）
                return True
        return super().eventFilter(obj, event)

    # ─── 外部调用：获取/设置当前选中 ───
    def current_clip(self):
        """返回当前选中的片段对象，未选中时返回 None"""
        return self._clip

    def set_selection(self, clip, track: str):
        self._clip = clip
        self._track = track
        # 切换片段时取消待处理的字幕同步（避免泄漏到新片段）
        self._pending_sync_attrs.clear()
        self._sync_debounce.stop()
        # 仅在从未设置过播放头时间时，默认使用片段起始时间
        # 否则保持当前播放头位置（由 set_current_time 更新），避免关键帧误落第一帧
        if self._current_sec is None:
            self._current_sec = clip.timeline_start if clip else None
        self._rebuild_ui()

    def set_current_time(self, sec: float | None):
        """更新当前播放头时间（用于关键帧按钮高亮）"""
        self._current_sec = sec
        # 只更新按钮高亮，不重建整个 UI
        self._highlight_kf_buttons()

    def _highlight_kf_buttons(self):
        """遍历预收集的关键帧按钮列表，高亮当前时间点已设置关键帧的按钮"""
        sec = self._current_sec
        clip = self._clip
        if sec is None or clip is None:
            return
        rel_t = sec - clip.timeline_start
        kfs = getattr(clip, "keyframes", None) or {}
        for btn in self._kf_buttons:
            has_kf = any(abs(t - rel_t) < 0.05 for t, _ in kfs.get(btn.prop_name, []))
            btn.set_active(has_kf)

    def clear_selection(self):
        self._clip = None
        self._track = ""
        self._sync_bar.setVisible(False)
        self._rebuild_ui()

    @staticmethod
    def _get_video_duration(path: str) -> float:
        """快速获取视频时长（秒），不成功返回 0"""
        try:
            import cv2
            cap = cv2.VideoCapture(path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            dur = frames / fps if fps > 0 else 0.0
            cap.release()
            return dur
        except Exception:
            logging.debug("_get_video_duration error", exc_info=True)
            return 0.0

    # ─── 重建 UI ───
    def _rebuild_ui(self):
        # 字幕同步条：仅选中字幕时显示
        self._sync_bar.setVisible(self._track == "subtitle")

        # 清空关键帧按钮收集（_make_kf_btn 在下方的构建中重新填充）
        self._kf_buttons.clear()

        # 保存滚动位置（重建后恢复，避免"吸附顶部"）
        vsb = self._scroll.verticalScrollBar()
        saved_pos = vsb.value()

        # 清空内容区（先断开所有信号避免 deleteLater 异步销毁时意外触发）
        from PyQt6.QtCore import QObject
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                try:
                    QObject.blockSignals(w, True)
                except Exception:
                    pass
                # 无需手动 w.disconnect()：deleteLater() 销毁时 Qt 会自动清理全部连接，
                # 且上面已 blockSignals(w, True) 阻断销毁窗口内的信号发射；
                # 对即将销毁的控件做通配 disconnect 反而会触发
                # "QObject::disconnect: wildcard call disconnects from destroyed signal" 告警。
                w.deleteLater()

        clip = self._clip
        if clip is None:
            lbl = QLabel("选中时间线上的片段\n查看和编辑属性")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color:#555; font-size:12px;")
            self._content_layout.addWidget(lbl)
            self._content_layout.addStretch()
            self._title.setText("属性")
            vsb.setValue(0)
            return

        if self._track == "video":
            self._title.setText("视频片段属性")
            self._build_video_props(clip)
        elif self._track == "audio":
            self._title.setText("音频片段属性")
            self._build_audio_props(clip)
        elif self._track == "subtitle":
            self._title.setText("字幕属性")
            self._build_subtitle_props(clip)

        self._content_layout.addStretch()

        # 恢复滚动位置（用 singleShot 等布局计算完成后再设，避免设了又被 Qt 归零）
        if saved_pos > 0:
            from PyQt6.QtCore import QTimer as _Qt
            _Qt.singleShot(0, lambda: vsb.setValue(min(saved_pos, vsb.maximum())))

    # ─── 视频属性 ───
    def _build_video_props(self, clip: VideoClip):
        # 文件名
        fn_lbl = QLabel(f"文件：{clip.source_path.split('/')[-1].split(chr(92))[-1]}")
        fn_lbl.setStyleSheet("color:#666; font-size:11px;")
        fn_lbl.setWordWrap(True)
        self._content_layout.addWidget(fn_lbl)

        dur_lbl = QLabel(f"时长：{clip.duration:.2f}s  |  帧段：{clip.trim_start:.2f}s ~ {clip.trim_end:.2f}s")
        dur_lbl.setStyleSheet("color:#666; font-size:11px;")
        self._content_layout.addWidget(dur_lbl)

        # 位置 & 变换
        grp_xform = self._make_group("位置 & 变换")
        form_x = QFormLayout(grp_xform)
        form_x.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        px = int(getattr(clip, "pos_x", 0.0))
        px_slider, px_lbl, px_row = self._slider_row(-1500, 1500, px, " px", "pos_x")
        px_slider.valueChanged.connect(lambda v: self._set(clip, "pos_x", float(v)))

        py = int(getattr(clip, "pos_y", 0.0))
        py_slider, py_lbl, py_row = self._slider_row(-1500, 1500, py, " px", "pos_y")
        py_slider.valueChanged.connect(lambda v: self._set(clip, "pos_y", float(v)))

        sc = int((getattr(clip, "scale", 1.0) or 1.0) * 100)
        sc_slider, sc_val, sc_row = self._slider_row(10, 300, sc, " %", "scale")
        sc_slider.valueChanged.connect(lambda v: self._set(clip, "scale", v / 100))

        rt = int(getattr(clip, "rotation", 0.0) or 0.0)
        rt_slider, rt_val, rt_row = self._slider_row(-360, 360, rt, " °", "rotation")
        rt_slider.valueChanged.connect(lambda v: self._set(clip, "rotation", float(v)))

        bl = int(getattr(clip, "blur_radius", 0.0) or 0.0)
        bl_slider, bl_val, bl_row = self._slider_row(0, 50, bl, " px", "blur_radius")
        bl_slider.valueChanged.connect(lambda v: self._set(clip, "blur_radius", float(v)))

        form_x.addRow(self._lbl("位置 X:"), px_row)
        form_x.addRow(self._lbl("位置 Y:"), py_row)
        form_x.addRow(self._lbl("缩放:"), sc_row)
        form_x.addRow(self._lbl("旋转:"), rt_row)
        form_x.addRow(self._lbl("模糊:"), bl_row)
        self._content_layout.addWidget(grp_xform)

        # 记录变换滑块引用（供重置使用）
        self._tx_sliders: dict = {
            "pos_x": px_slider, "pos_y": py_slider,
            "scale": sc_slider, "rotation": rt_slider,
            "blur_radius": bl_slider,
        }

        # ── 重置变换按钮 ──
        reset_btn = QPushButton("⟳  重置变换")
        reset_btn.setFixedSize(120, 24)
        reset_btn.setStyleSheet(
            "QPushButton { background:transparent; color:#888; border:1px solid #333;"
            "border-radius:4px; padding:1px 6px; font-size:11px; }"
            "QPushButton:hover { color:#fff; border-color:#ff6b6b; }"
            "QToolTip { background:#333; color:#fff; border:1px solid #555; }"
        )
        reset_btn.clicked.connect(lambda: self._reset_video_transform(clip))
        self._content_layout.addWidget(reset_btn)

        # 不透明度
        grp_op = self._make_group("不透明度")
        form_op = QFormLayout(grp_op)
        form_op.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        op = int((getattr(clip, "opacity", 1.0) or 1.0) * 100)
        op_slider, op_lbl, op_row = self._slider_row(0, 100, op, " %", "opacity")
        op_slider.valueChanged.connect(lambda v: self._set(clip, "opacity", v / 100))
        form_op.addRow(self._lbl("不透明度:"), op_row)
        self._content_layout.addWidget(grp_op)

        # 绿幕抠像（Chroma Key）
        grp_ck = self._make_group("绿幕抠像")
        form_ck = QFormLayout(grp_ck)
        form_ck.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        ck_enable = CheckMarkBox("启用绿幕抠像")
        ck_enable.blockSignals(True)
        ck_enable.setChecked(getattr(clip, "chroma_key_enabled", False))
        ck_enable.blockSignals(False)
        ck_enable.setStyleSheet(CHECK_STYLE)
        ck_enable.toggled.connect(
            lambda v: self._undo_set(clip, "chroma_key_enabled", v))
        form_ck.addRow(ck_enable)

        # 键色取色器
        ck_color = getattr(clip, "chroma_key_color", (0, 255, 0)) or (0, 255, 0)
        ck_r = int(ck_color[0]) if len(ck_color) > 0 else 0
        ck_g = int(ck_color[1]) if len(ck_color) > 1 else 255
        ck_b = int(ck_color[2]) if len(ck_color) > 2 else 0
        ck_hex = "#%02X%02X%02X" % (ck_r, ck_g, ck_b)
        ck_color_btn = _color_btn(ck_hex)

        def _pick_ck_color():
            c_hex = _pick_color(self, ck_hex)
            try:
                r = int(c_hex[1:3], 16)
                g = int(c_hex[3:5], 16)
                b = int(c_hex[5:7], 16)
            except Exception:
                return
            self._undo_set(clip, "chroma_key_color", (r, g, b))
            ck_color_btn.setStyleSheet(
                f"background:{c_hex}; border:1px solid #555; border-radius:3px;")
        ck_color_btn.clicked.connect(_pick_ck_color)
        form_ck.addRow(self._lbl("键色:"), ck_color_btn)

        # 相似度
        ck_sim = int(getattr(clip, "chroma_key_similarity", 0.40) * 100)
        ck_sim_slider, ck_sim_lbl, ck_sim_row = self._slider_row(0, 100, ck_sim, " %")
        ck_sim_slider.valueChanged.connect(
            lambda v: self._set(clip, "chroma_key_similarity", v / 100))
        form_ck.addRow(self._lbl("相似度:"), ck_sim_row)

        # 边缘羽化
        ck_sm = int(getattr(clip, "chroma_key_smoothness", 0.10) * 100)
        ck_sm_slider, ck_sm_lbl, ck_sm_row = self._slider_row(0, 100, ck_sm, " %")
        ck_sm_slider.valueChanged.connect(
            lambda v: self._set(clip, "chroma_key_smoothness", v / 100))
        form_ck.addRow(self._lbl("边缘:"), ck_sm_row)

        # 溢色抑制
        ck_sp = int(getattr(clip, "chroma_key_spill", 0.10) * 100)
        ck_sp_slider, ck_sp_lbl, ck_sp_row = self._slider_row(0, 100, ck_sp, " %")
        ck_sp_slider.valueChanged.connect(
            lambda v: self._set(clip, "chroma_key_spill", v / 100))
        form_ck.addRow(self._lbl("溢色:"), ck_sp_row)

        self._content_layout.addWidget(grp_ck)

        # 速度 & 音量
        grp = self._make_group("速度 & 音量")
        form = QFormLayout(grp)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # 速度（0.1x ~ 20.0x，默认 1.0x）
        sp = int(clip.speed * 100)
        speed_slider, sp_val, sp_row = self._slider_row(10, 2000, sp, "")
        speed_slider.valueChanged.connect(lambda v: self._set(clip, "speed", v / 100))
        # 覆盖编辑框显示为倍速格式
        speed_slider.valueChanged.connect(lambda v: (
            sp_val.blockSignals(True),
            sp_val.setText(f"{v/100:.1f}x"),
            sp_val.blockSignals(False)
        ))
        sp_val.setText(f"{sp/100:.1f}x")
        # 覆盖速度编辑框的输入解析：支持 "1" / "1.5" / "2.0x" 等格式
        sp_val.editingFinished.disconnect()  # 断开通用的 _edit_to_slider
        sp_val.editingFinished.connect(
            lambda sld=speed_slider, ed=sp_val: self._speed_edit_to_slider(sld, ed))

        vol_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        vol_slider.setRange(0, 200)
        vol_slider.setValue(int(clip.volume * 100))
        vol_slider.valueChanged.connect(lambda v: self._set(clip, "volume", v / 100))
        self._vol_label = QLabel(f"{int(clip.volume*100)}%")
        self._vol_label.setStyleSheet(FORM_LABEL_STYLE)
        vol_slider.valueChanged.connect(lambda v: self._vol_label.setText(f"{v}%"))

        form.addRow(self._lbl("速度:"), sp_row)
        form.addRow(self._lbl("音量:"), self._row(vol_slider, self._vol_label))
        self._content_layout.addWidget(grp)

        # 转场（淡出到下一段，仅背景轨生效）
        grp_tr = self._make_group("转场（到下一段）")
        form_tr = QFormLayout(grp_tr)
        form_tr.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        ot = getattr(clip, "out_transition", None) or {}
        tr_enabled = bool(ot and ot.get("type"))
        tr_type = ot.get("type", "fade")
        tr_dur = float(ot.get("duration", 0.5))

        # 启用开关
        tr_enable = CheckMarkBox("启用转场")
        tr_enable.blockSignals(True)
        tr_enable.setChecked(tr_enabled)
        tr_enable.blockSignals(False)
        tr_enable.setStyleSheet(CHECK_STYLE)
        form_tr.addRow(tr_enable)

        # 类型下拉
        tr_combo = QComboBox()
        tr_combo.setStyleSheet(COMBO_STYLE)
        tr_combo.setFixedHeight(24)
        for disp, eng in TRANSITIONS.items():
            tr_combo.addItem(disp, eng)
        idx = tr_combo.findData(tr_type)
        tr_combo.setCurrentIndex(idx if idx >= 0 else 0)
        desc = TRANS_DESCS.get(tr_type, "")
        tr_combo.setToolTip(desc)
        form_tr.addRow(self._lbl("类型:"), tr_combo)

        # 时长滑块（0.10s ~ 2.00s，0.01s 步进）
        tr_dur_slider, tr_dur_lbl, tr_dur_row = self._slider_row(
            10, 200, int(round(tr_dur * 100)), " s")
        # 时长编辑框支持小数：断开整数解析，改用浮点解析
        try:
            tr_dur_lbl.editingFinished.disconnect()
        except Exception:
            pass
        tr_dur_lbl.editingFinished.connect(
            lambda ed=tr_dur_lbl, sld=tr_dur_slider:
                self._edit_transition_duration(clip, ed, sld))
        # 滑块 → 显示秒 + 写入 out_transition
        def _on_dur_slide(v, _e=tr_dur_lbl):
            _e.blockSignals(True)
            _e.setText(f"{v / 100:.2f}s")
            _e.blockSignals(False)
            cur = getattr(clip, "out_transition", None)
            if not cur:
                return
            self._set(clip, "out_transition", {**cur, "duration": v / 100})
        tr_dur_slider.valueChanged.connect(_on_dur_slide)
        tr_dur_lbl.setText(f"{tr_dur:.2f}s")
        form_tr.addRow(self._lbl("时长:"), tr_dur_row)

        # 启用状态联动：禁用时灰掉类型/时长
        def _on_tr_enable(checked, _c=tr_combo, _s=tr_dur_slider, _e=tr_dur_lbl):
            if checked:
                d = getattr(clip, "out_transition", None) or {"type": "fade", "duration": 0.5}
                self._undo_set(clip, "out_transition", d)
            else:
                self._undo_set(clip, "out_transition", None)
            _c.setEnabled(checked)
            _s.setEnabled(checked)
            _e.setEnabled(checked)
        tr_enable.toggled.connect(_on_tr_enable)
        tr_combo.setEnabled(tr_enabled)
        tr_dur_slider.setEnabled(tr_enabled)
        tr_dur_lbl.setEnabled(tr_enabled)

        # 类型切换
        def _on_tr_type(_i, _c=tr_combo):
            cur = getattr(clip, "out_transition", None)
            base = cur or {"type": "fade", "duration": 0.5}
            self._undo_set(clip, "out_transition", {**base, "type": _c.currentData()})
        tr_combo.currentIndexChanged.connect(_on_tr_type)

        self._content_layout.addWidget(grp_tr)

    def _edit_transition_duration(self, clip, edit, slider):
        """转场时长编辑框（支持小数秒，范围 0.10s ~ 2.00s）"""
        try:
            v = float(edit.text())
        except ValueError:
            edit.blockSignals(True)
            edit.setText(f"{slider.value() / 100:.2f}s")
            edit.blockSignals(False)
            return
        v = max(0.1, min(2.0, v))
        iv = int(round(v * 100))
        slider.blockSignals(True)
        slider.setValue(iv)
        slider.blockSignals(False)
        edit.blockSignals(True)
        edit.setText(f"{iv / 100:.2f}s")
        edit.blockSignals(False)
        cur = getattr(clip, "out_transition", None)
        if cur:
            self._set(clip, "out_transition", {**cur, "duration": iv / 100})

    # ─── 音频属性 ───
    def _build_audio_props(self, clip: AudioClip):
        fn_lbl = QLabel(f"文件：{clip.source_path.split('/')[-1].split(chr(92))[-1]}")
        fn_lbl.setStyleSheet("color:#666; font-size:11px;")
        fn_lbl.setWordWrap(True)
        self._content_layout.addWidget(fn_lbl)

        grp = self._make_group("音频控制")
        form = QFormLayout(grp)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # 音量滑轨（使用 _slider_row 获得完整的 < ◆ > 导航按钮）
        vu = int(clip.volume * 100)
        vol_slider, vol_val, vol_row = self._slider_row(0, 200, vu, "", "volume")
        vol_slider.valueChanged.connect(lambda v: self._set(clip, "volume", v / 100))
        vol_val.setText(f"{vu}%")
        vol_slider.valueChanged.connect(lambda v: (
            vol_val.blockSignals(True),
            vol_val.setText(f"{v}%"),
            vol_val.blockSignals(False)
        ))

        fi_spin = NoWheelDoubleSpinBox()
        fi_spin.setRange(0, 10)
        fi_spin.setSingleStep(0.1)
        fi_spin.setSuffix(" s")
        fi_spin.setValue(clip.fade_in)
        fi_spin.setStyleSheet(SPINBOX_STYLE)
        fi_spin.valueChanged.connect(lambda v: self._set(clip, "fade_in", v))

        fo_spin = NoWheelDoubleSpinBox()
        fo_spin.setRange(0, 10)
        fo_spin.setSingleStep(0.1)
        fo_spin.setSuffix(" s")
        fo_spin.setValue(clip.fade_out)
        fo_spin.setStyleSheet(SPINBOX_STYLE)
        fo_spin.valueChanged.connect(lambda v: self._set(clip, "fade_out", v))

        form.addRow(self._lbl("音量:"), vol_row)
        form.addRow(self._lbl("淡入:"), fi_spin)
        form.addRow(self._lbl("淡出:"), fo_spin)
        self._content_layout.addWidget(grp)

    # ─── 字幕属性 ───
    def _build_subtitle_props(self, clip: SubtitleBlock):
        # 文本编辑区
        grp_text = self._make_group("字幕文本")
        vl = QVBoxLayout(grp_text)
        self._sub_edit = QTextEdit()
        self._sub_edit.setPlainText(clip.text)
        self._sub_edit.setFixedHeight(80)
        self._sub_edit.setStyleSheet(
            "QTextEdit { background:#2a2a2a; color:#fff; border:1px solid #444;"
            "border-radius:3px; font-size:13px; padding:4px; }"
        )
        self._sub_edit.textChanged.connect(
            lambda: self._set(clip, "text", self._sub_edit.toPlainText())
        )
        vl.addWidget(self._sub_edit)
        self._content_layout.addWidget(grp_text)

        # ── 字体样式（无时间范围）──
        grp_font = self._make_group("字体样式")
        form_f = QFormLayout(grp_font)
        form_f.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # 字体名（下拉列表）
        font_cb = QComboBox()
        common_fonts = [
            "Microsoft YaHei", "微软雅黑", "SimHei", "黑体",
            "SimSun", "宋体", "KaiTi", "楷体", "FangSong", "仿宋",
            "Arial", "Helvetica", "Times New Roman", "Courier New",
            "Verdana", "Tahoma", "Georgia", "Impact", "Comic Sans MS",
            "Trebuchet MS", "Segoe UI", "Calibri", "Cambria",
            "Noto Sans SC", "Noto Serif SC", "Source Han Sans SC",
            "DengXian", "等线", "YouYuan", "幼圆",
        ]
        font_cb.addItems(common_fonts)
        # 如果当前字体不在列表中，添加到第一项
        if clip.font_family and font_cb.findText(clip.font_family) < 0:
            font_cb.insertItem(0, clip.font_family)
        font_cb.setCurrentText(clip.font_family)
        font_cb.setStyleSheet(
            "QComboBox{background:#2a2a2a;color:#ccc;border:1px solid #444;"
            "border-radius:3px;padding:4px 8px;font-size:12px;}"
            "QComboBox:hover{border:1px solid #666;}"
            "QComboBox::drop-down{border:none;width:20px;}"
            "QComboBox::down-arrow{image:none;border-left:4px solid transparent;"
            "border-right:4px solid transparent;border-top:6px solid #888;margin-right:4px;}"
            "QComboBox QAbstractItemView{background:#2a2a2a;color:#ccc;"
            "selection-background-color:#3d8ef8;font-size:12px;"
            "outline:none;border:1px solid #555;}"
            "QComboBox QAbstractItemView::item{min-height:24px;padding:4px 10px;}"
        )
        font_cb.currentTextChanged.connect(
            lambda t: self._undo_set(clip, "font_family", t) if t else None
        )

        # 字号
        sz_slider, sz_lbl, sz_row = self._slider_row(10, 200, clip.font_size)
        sz_slider.valueChanged.connect(lambda v: self._set(clip, "font_size", v))

        # 粗体/斜体/下划线
        bold_cb = CheckMarkBox("B")
        bold_cb.setStyleSheet(CHECK_STYLE + " QCheckBox { font-weight:bold; }")
        bold_cb.blockSignals(True)
        bold_cb.setChecked(clip.font_bold)
        bold_cb.blockSignals(False)
        bold_cb.toggled.connect(lambda v: self._undo_set(self._clip, "font_bold", v) if self._clip else None)

        italic_cb = CheckMarkBox("I")
        italic_cb.setStyleSheet(CHECK_STYLE + " QCheckBox { font-style:italic; }")
        italic_cb.blockSignals(True)
        italic_cb.setChecked(clip.font_italic)
        italic_cb.blockSignals(False)
        italic_cb.toggled.connect(lambda v: self._undo_set(self._clip, "font_italic", v) if self._clip else None)

        underline_cb = CheckMarkBox("U")
        underline_cb.setStyleSheet(CHECK_STYLE + " QCheckBox { text-decoration:underline; }")
        underline_cb.blockSignals(True)
        underline_cb.setChecked(getattr(clip, "font_underline", False))
        underline_cb.blockSignals(False)
        underline_cb.toggled.connect(lambda v: self._undo_set(self._clip, "font_underline", v) if self._clip else None)

        form_f.addRow(self._lbl("字体:"), font_cb)
        form_f.addRow(self._lbl("字号:"), sz_row)
        form_f.addRow(self._lbl("样式:"), self._row(bold_cb, italic_cb, underline_cb))
        self._content_layout.addWidget(grp_font)

        # ── 颜色 & 描边 ──
        grp_color = self._make_group("颜色 & 描边")
        form_c = QFormLayout(grp_color)
        form_c.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        txt_color_btn = _color_btn(clip.color)
        txt_color_btn.setToolTip(clip.color)
        def pick_text_color():
            c = _pick_color(self, clip.color)
            self._undo_set(clip, "color", c)
            txt_color_btn.setStyleSheet(
                f"background:{c}; border:1px solid #555; border-radius:3px;"
            )
        txt_color_btn.clicked.connect(pick_text_color)

        out_color_btn = _color_btn(clip.outline_color)
        def pick_outline_color():
            c = _pick_color(self, clip.outline_color)
            self._undo_set(clip, "outline_color", c)
            out_color_btn.setStyleSheet(
                f"background:{c}; border:1px solid #555; border-radius:3px;"
            )
        out_color_btn.clicked.connect(pick_outline_color)

        outline_slider, ol_lbl, ol_row = self._slider_row(0, 10, clip.outline_width, " px")
        outline_slider.valueChanged.connect(lambda v: self._set(clip, "outline_width", v))

        form_c.addRow(self._lbl("文字色:"), txt_color_btn)
        form_c.addRow(self._lbl("描边色:"), out_color_btn)
        form_c.addRow(self._lbl("描边宽:"), ol_row)
        self._content_layout.addWidget(grp_color)

        # ── 背景填充 & 圆角 ──
        grp_fill = self._make_group("背景填充")
        form_fi = QFormLayout(grp_fill)
        form_fi.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        fill_cb = CheckMarkBox("启用填充")
        fill_cb.blockSignals(True)
        fill_cb.setChecked(getattr(clip, "fill_enabled", False))
        fill_cb.blockSignals(False)
        fill_cb.setStyleSheet(CHECK_STYLE)
        self._fill_cb = fill_cb  # 存储引用，供互斥逻辑使用
        def _on_fill_toggled(v):
            c = self._clip
            if c:
                self.tl._save_history()
                self._set(c, "fill_enabled", v)
            if v and hasattr(self, "_word_anim_cb") and self._word_anim_cb.isChecked():
                # 打开背景填充 → 自动关闭逐词动画
                self._word_anim_cb.blockSignals(True)
                self._word_anim_cb.setChecked(False)
                self._word_anim_cb.blockSignals(False)
        fill_cb.toggled.connect(_on_fill_toggled)

        bg_color_btn = _color_btn(getattr(clip, "background_color", "#000000") or "#000000")
        def pick_bg_color():
            c = _pick_color(self, getattr(clip, "background_color", "#000000") or "#000000")
            self._undo_set(clip, "background_color", c)
            bg_color_btn.setStyleSheet(
                f"background:{c}; border:1px solid #555; border-radius:3px;"
            )
        bg_color_btn.clicked.connect(pick_bg_color)

        radius_slider, rd_lbl, rd_row = self._slider_row(0, 100, getattr(clip, "border_radius", 0), " px")
        radius_slider.valueChanged.connect(lambda v: self._set(clip, "border_radius", v))

        form_fi.addRow(fill_cb)
        form_fi.addRow(self._lbl("背景色:"), bg_color_btn)
        form_fi.addRow(self._lbl("圆角:"), rd_row)
        self._content_layout.addWidget(grp_fill)

        # ── 间距 ──
        grp_spacing = self._make_group("间距")
        form_sp = QFormLayout(grp_spacing)
        form_sp.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        ls_slider, ls_lbl, ls_row = self._slider_row(-20, 100, getattr(clip, "letter_spacing", 0), " px")
        ls_slider.valueChanged.connect(lambda v: self._set(clip, "letter_spacing", v))

        lh_slider, lh_lbl, lh_row = self._slider_row(-20, 100, getattr(clip, "line_spacing", 0), " px")
        lh_slider.valueChanged.connect(lambda v: self._set(clip, "line_spacing", v))

        form_sp.addRow(self._lbl("字间距:"), ls_row)
        form_sp.addRow(self._lbl("行间距:"), lh_row)
        self._content_layout.addWidget(grp_spacing)

        # ── 逐词动画 ──
        grp_anim = self._make_group("逐词动画")
        form_a = QFormLayout(grp_anim)
        form_a.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        word_anim_cb = CheckMarkBox("启用逐词淡入")
        word_anim_cb.setStyleSheet(CHECK_STYLE)
        word_anim_cb.blockSignals(True)
        word_anim_cb.setChecked(getattr(clip, "word_animation", False))
        word_anim_cb.blockSignals(False)
        self._word_anim_cb = word_anim_cb  # 存储引用，供互斥逻辑使用
        def _on_word_anim_toggled(v):
            c = self._clip  # 用当前选中片段而非闭包捕获，避免引用过期
            if c:
                self.tl._save_history()
                self._set(c, "word_animation", v)
            if v and hasattr(self, "_fill_cb") and self._fill_cb.isChecked():
                # 打开逐词动画 → 自动关闭背景填充
                self._fill_cb.blockSignals(True)
                self._fill_cb.setChecked(False)
                self._fill_cb.blockSignals(False)
        word_anim_cb.toggled.connect(_on_word_anim_toggled)
        form_a.addRow(word_anim_cb)

        ad_ms = int((getattr(clip, "word_anim_duration", 0.15) or 0.15) * 1000)
        ad_slider, ad_lbl, ad_row = self._slider_row(30, 500, ad_ms, " ms")
        ad_slider.valueChanged.connect(
            lambda v: self._set(clip, "word_anim_duration", v / 1000)
        )
        form_a.addRow(self._lbl("弹出时长:"), ad_row)

        self._content_layout.addWidget(grp_anim)

        # ── 变换（位置 & 缩放 & 旋转）──
        grp_xform = self._make_group("变换")
        form_x = QFormLayout(grp_xform)
        form_x.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # 位置（归一化 -100~100 → -1.0~1.0），考虑 position 回退
        eff_px = getattr(clip, "pos_x", None)
        eff_py = getattr(clip, "pos_y", None)
        if eff_px is None or eff_py is None:
            position = getattr(clip, 'position', 'bottom') or 'bottom'
            pos_map = {'top': -0.85, 'center': 0.0, 'bottom': 0.85}
            if eff_px is None:
                eff_px = 0.0
            if eff_py is None:
                eff_py = pos_map.get(position, 0.85)
        px = int(float(eff_px) * 100)
        px_slider, px_lbl, px_row = self._slider_row(-100, 100, px, " %", "pos_x")
        px_slider.valueChanged.connect(lambda v: self._set(clip, "pos_x", v / 100))

        py = int(float(eff_py) * 100)
        py_slider, py_lbl, py_row = self._slider_row(-100, 100, py, " %", "pos_y")
        py_slider.valueChanged.connect(lambda v: self._set(clip, "pos_y", v / 100))

        sc = int((getattr(clip, "scale", 1.0) or 1.0) * 100)
        sub_scale_slider, sc_val, sc_row = self._slider_row(10, 300, sc, " %", "scale")
        sub_scale_slider.valueChanged.connect(lambda v: self._set(clip, "scale", v / 100))

        rt = int(getattr(clip, "rotation", 0.0) or 0.0)
        sub_rot_slider, rt_val, rt_row = self._slider_row(-360, 360, rt, " °", "rotation")
        sub_rot_slider.valueChanged.connect(lambda v: self._set(clip, "rotation", float(v)))

        form_x.addRow(self._lbl("水平位置:"), px_row)
        form_x.addRow(self._lbl("垂直位置:"), py_row)
        form_x.addRow(self._lbl("缩放:"), sc_row)
        form_x.addRow(self._lbl("旋转:"), rt_row)
        self._content_layout.addWidget(grp_xform)

        # ── 不透明度 ──
        grp_op = self._make_group("不透明度")
        form_op = QFormLayout(grp_op)
        form_op.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        op = int((getattr(clip, "opacity", 1.0) or 1.0) * 100)
        op_slider, op_lbl, op_row = self._slider_row(0, 100, op, " %", "opacity")
        op_slider.valueChanged.connect(lambda v: self._set(clip, "opacity", v / 100))
        form_op.addRow(self._lbl("不透明度:"), op_row)
        self._content_layout.addWidget(grp_op)

    # ─── 工具方法 ───
    def _set(self, clip, attr: str, value):
        """设置属性并触发预览刷新。
        自动关键帧：若该属性已有至少一个关键帧，则自动在当前时间点插入关键帧。
        速度变更时同步缩放所有关键帧时间。
        字幕同步：若开启了字幕同步开关，自动同步样式到所有其他字幕块。
        """
        old_val = getattr(clip, attr, None)
        setattr(clip, attr, value)

        # 字幕统一样式：debounce 同步到所有其他字幕块（避免拖拽滑块时全量遍历卡顿）
        if (self._sync_subs and self._track == "subtitle"
                and attr not in ("text", "timeline_start", "timeline_end", "from_asr", "keyframes")):
            self._pending_sync_attrs[attr] = value
            self._sync_debounce.start()

        # 速度变更：同步缩放所有关键帧时间
        if attr == "speed" and old_val is not None and old_val != value:
            ratio = value / old_val
            kfs = getattr(clip, "keyframes", None) or {}
            for prop, kf_list in kfs.items():
                kfs[prop] = [(t * ratio, v) for t, v in kf_list]

        # 自动关键帧逻辑
        kfs = getattr(clip, "keyframes", None) or {}
        if attr in kfs and kfs[attr] and self._current_sec is not None:
            rel_t = self._current_sec - clip.timeline_start
            if 0 <= rel_t <= getattr(clip, 'duration', 9999):
                kfs[attr] = [(t, v) for t, v in kfs[attr] if abs(t - rel_t) > 0.05]
                kfs[attr].append((rel_t, value))
        self.property_changed.emit()

    def _undo_set(self, clip, attr: str, value):
        """带撤回的 _set（用于复选框/颜色/下拉等即时变更控件）"""
        self.tl._save_history()
        self._set(clip, attr, value)

    def _begin_slider_interaction(self):
        """滑块开始拖拽时：如果还未捕获快照则捕获"""
        if self._slider_drag_snapshot is None:
            self._slider_drag_snapshot = self.tl._snapshot()

    def _end_slider_interaction(self):
        """滑块释放时：将拖拽前快照推入撤回栈"""
        if self._slider_drag_snapshot is not None:
            self._slider_drag_snapshot = None
            # 推入 undo 栈（截断 redo）—— 使用 _save_history 而非手动操作栈
            self.tl._save_history()

    def _do_sync_to_all_subs(self):
        """debounce 后批量同步字幕样式（避免拖拽滑块时逐帧全量遍历）"""
        if not self._pending_sync_attrs or not self._clip:
            return
        clip = self._clip
        pending = self._pending_sync_attrs.copy()
        self._pending_sync_attrs.clear()
        self.tl._save_history()  # 撤回：捕获同步前的所有字幕状态
        for track in self.tl.subtitle_tracks:
            for b in track:
                if b is not clip:
                    for attr, value in pending.items():
                        setattr(b, attr, value)
                        # fill_enabled/word_animation 互斥处理
                        if attr == "fill_enabled" and value:
                            setattr(b, "word_animation", False)
                        elif attr == "word_animation" and value:
                            setattr(b, "fill_enabled", False)
        # 通知预览刷新叠加层（不走 tl.changed 避免清帧缓存导致闪黑）
        self.tl.overlays_changed.emit()

    def _on_sync_cb_toggled(self, checked: bool):
        """字幕同步开关切换：更新标志，开启时立刻全量同步当前字幕属性到所有字幕"""
        self._sync_subs = checked
        if not checked or not self._clip or self._track != "subtitle":
            return
        # 开启时立刻收集所有可同步属性并触发一次全量同步
        clip = self._clip
        SYNC_ATTRS = (
            "font_family", "font_size", "font_bold", "font_italic", "font_underline",
            "color", "outline_color", "outline_width",
            "fill_enabled", "background_color", "border_radius",
            "letter_spacing", "line_spacing",
            "word_animation", "word_anim_duration",
            "align", "valign",
        )
        for attr in SYNC_ATTRS:
            val = getattr(clip, attr, None)
            if val is not None:
                self._pending_sync_attrs[attr] = val
        if self._pending_sync_attrs:
            self._sync_debounce.start()

    def _reset_video_transform(self, clip):
        """重置视频的位置/缩放/旋转到默认值，同时清除对应关键帧"""
        self.tl._save_history()  # 撤回
        defaults = {"pos_x": 0, "pos_y": 0, "scale": 100, "rotation": 0, "blur_radius": 0}
        # 清除变换属性的关键帧
        kfs = getattr(clip, "keyframes", None) or {}
        for prop in ("pos_x", "pos_y", "scale", "rotation", "blur_radius"):
            if prop in kfs:
                kfs.pop(prop)
        # 直接写入默认值（跳过 _set 的自动关键帧逻辑）
        clip.pos_x = 0.0
        clip.pos_y = 0.0
        clip.scale = 1.0
        clip.rotation = 0.0
        clip.blur_radius = 0.0
        # 批量更新滑块（block signals 防止触发多次 valueChanged）
        for prop, slider in self._tx_sliders.items():
            slider.blockSignals(True)
            slider.setValue(defaults[prop])
            slider.blockSignals(False)
        self.property_changed.emit()

    def _replace_video(self, clip: VideoClip, preset_path: str = ""):
        """打开替换视频对话框并执行替换"""
        from PyQt6.QtWidgets import QMessageBox
        
        # 拖入替换时，先检查新视频时长是否足够
        if preset_path and os.path.exists(preset_path):
            new_dur = self._get_video_duration(preset_path)
            if 0 < new_dur < clip.duration:
                QMessageBox.warning(
                    self, "无法替换",
                    f"新视频时长（{new_dur:.1f}s）短于原片段（{clip.duration:.1f}s），无法替换"
                )
                return
        dlg = ReplaceVideoDialog(clip, self)
        if preset_path and os.path.exists(preset_path):
            dlg._load_video(preset_path)
        else:
            dlg._load_video(clip.source_path)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        new_path = dlg.new_path
        if not new_path or not os.path.exists(new_path):
            return

        # 用户确认替换后才保存历史（对话框取消不产生撤消记录）
        self.tl._save_history()

        try:
            import cv2
            cap = cv2.VideoCapture(new_path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            dur = frames / fps if fps > 0 else 0.0
            cap.release()
        except Exception:
            QMessageBox.warning(self, "提示", "无法读取新视频时长")
            return

        if dur <= 0:
            QMessageBox.warning(self, "提示", "新视频时长无效")
            return

        # 记录原效果
        effects = {
            "pos_x": getattr(clip, "pos_x", 0.0),
            "pos_y": getattr(clip, "pos_y", 0.0),
            "scale": getattr(clip, "scale", 1.0),
            "rotation": getattr(clip, "rotation", 0.0),
            "speed": getattr(clip, "speed", 1.0),
            "volume": getattr(clip, "volume", 1.0),
            "mute": getattr(clip, "mute", False),
        }

        old_duration = clip.duration
        old_timeline_start = clip.timeline_start
        trim_start = getattr(dlg, "_trim_start", 0.0)

        # 替换源文件信息
        clip.source_path = new_path
        clip.source_duration = dur
        clip.trim_start = trim_start
        # 使用用户选取的片段长度必须 >= 原片段时长（对话框已校验）
        clip.trim_end = trim_start + old_duration
        clip.thumbnail = None
        clip.thumbnails = None  # 清除旧缩略图条
        if hasattr(clip, "_scaled_thumbs_cache"):
            clip._scaled_thumbs_cache = None

        if dlg.keep_effects:
            for k, v in effects.items():
                setattr(clip, k, v)
        else:
            clip.pos_x = 0.0
            clip.pos_y = 0.0
            clip.scale = 1.0
            clip.rotation = 0.0
            clip.speed = 1.0
            clip.volume = 1.0
            clip.mute = False

        # 保持时间线起始位置不变（总时长可能变化）
        clip.timeline_start = old_timeline_start

        self.tl.changed.emit()
        self.property_changed.emit()
        self._rebuild_ui()

    def _lbl(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(FORM_LABEL_STYLE)
        return lbl

    def _row(self, *widgets) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        for widget in widgets:
            lay.addWidget(widget)
        lay.addStretch()
        return w

    def _make_group(self, title: str) -> QGroupBox:
        grp = QGroupBox(title)
        grp.setStyleSheet(GROUP_STYLE)
        return grp

    def _make_kf_btn(self, prop_name: str, clip=None):
        """创建菱形关键帧按钮（自定义绘制）"""
        btn = DiamondButton(prop_name)
        btn.clicked.connect(lambda: self._toggle_kf(prop_name))
        self._kf_buttons.append(btn)
        return btn

    def _slider_row(self, range_min: int, range_max: int, value: int,
                    suffix: str = "", prop_name: str = "", **kwargs):
        """滑轨 + 可编辑数值框 + 菱形关键帧按钮"""
        s = NoWheelSlider(Qt.Orientation.Horizontal)
        s.setRange(range_min, range_max)
        s.setValue(value)
        s.setStyleSheet(SLIDER_STYLE)
        # 滑块撤回：press 捕获快照 / release 推入 undo
        s.sliderPressed.connect(self._begin_slider_interaction)
        s.sliderReleased.connect(self._end_slider_interaction)

        # 可编辑数值框
        val_edit = QLineEdit()
        val_edit.setFixedWidth(42)
        val_edit.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        val_edit.setStyleSheet(
            "QLineEdit { background:#1e1e1e; color:#ccc; border:1px solid #444;"
            "border-radius:2px; padding:1px 3px; font-size:11px; }"
            "QLineEdit:focus { border-color:#3d8ef8; }"
        )
        val_edit.setText(f"{value}")
        # slider → edit
        s.valueChanged.connect(lambda v, e=val_edit: (
            e.blockSignals(True), e.setText(str(v)), e.blockSignals(False)))
        # edit → slider（回车或失去焦点）
        val_edit.editingFinished.connect(
            lambda sld=s, ed=val_edit, lo=range_min, hi=range_max:
                self._edit_to_slider(sld, ed, lo, hi))
        val_edit.returnPressed.connect(val_edit.editingFinished.emit)

        widgets = [s, val_edit]
        if prop_name:
            # < 上一个关键帧
            prev_btn = QPushButton("<")
            prev_btn.setFixedSize(20, 20)
            prev_btn.setToolTip(f"上一个 {prop_name} 关键帧")
            prev_btn.setStyleSheet(
                "QPushButton { background:transparent; color:#888; border:1px solid #333;"
                "border-radius:3px; font-size:12px; font-weight:bold; }"
                "QPushButton:hover { color:#fff; border-color:#00eaff; }"
                "QToolTip { background:#333; color:#fff; border:1px solid #555; }"
            )
            prev_btn.clicked.connect(lambda _checked, pn=prop_name, d=-1: self._jump_kf(pn, d))
            # ◆ 菱形切换
            kf_btn = self._make_kf_btn(prop_name)
            # > 下一个关键帧
            next_btn = QPushButton(">")
            next_btn.setFixedSize(20, 20)
            next_btn.setToolTip(f"下一个 {prop_name} 关键帧")
            next_btn.setStyleSheet(
                "QPushButton { background:transparent; color:#888; border:1px solid #333;"
                "border-radius:3px; font-size:12px; font-weight:bold; }"
                "QPushButton:hover { color:#fff; border-color:#00eaff; }"
                "QToolTip { background:#333; color:#fff; border:1px solid #555; }"
            )
            next_btn.clicked.connect(lambda _checked, pn=prop_name, d=1: self._jump_kf(pn, d))
            widgets.extend([prev_btn, kf_btn, next_btn])
        row_w = self._row(*widgets)
        return s, val_edit, row_w

    def _edit_to_slider(self, slider, edit, lo, hi):
        """编辑框输入 → 滑轨更新"""
        try:
            v = int(float(edit.text()))
            v = max(lo, min(hi, v))
            slider.blockSignals(True)
            slider.setValue(v)
            slider.blockSignals(False)
            slider.valueChanged.emit(v)
        except ValueError:
            edit.setText(str(slider.value()))

    def _speed_edit_to_slider(self, slider, edit):
        """速度编辑框输入 → 滑轨（支持倍速格式：1 / 1.5 / 2.0x）"""
        try:
            raw = edit.text().strip().replace("x", "").replace("X", "")
            mult = float(raw)  # 用户输入的是倍速值，如 1.0 / 2.0
            v = int(round(mult * 100))  # 转换为滑块内部值 (10~2000)
            v = max(10, min(2000, v))
            slider.blockSignals(True)
            slider.setValue(v)
            slider.blockSignals(False)
            slider.valueChanged.emit(v)
        except ValueError:
            edit.setText(f"{slider.value()/100:.1f}x")

    def _toggle_kf(self, prop: str):
        """点击菱形：切换关键帧（无→添加，有→删除）"""
        clip = self._clip
        if clip is None:
            return
        self.tl._save_history()  # 撤回：捕获关键帧变更前状态
        # 用当前播放头位置，未设置则使用片段起始位置
        t = self._current_sec if self._current_sec is not None else clip.timeline_start
        rel_t = t - clip.timeline_start
        dur = getattr(clip, 'duration', None) or (clip.timeline_end - clip.timeline_start)
        if rel_t < 0:
            rel_t = 0.0
        if rel_t > max(dur, 0.01):
            rel_t = max(dur, 0.01)

        kfs = getattr(clip, 'keyframes', None)
        if kfs is None:
            clip.keyframes = {}
            kfs = clip.keyframes
        if prop not in kfs:
            kfs[prop] = []

        # 检查是否已存在
        existing = [i for i, (t0, _) in enumerate(kfs[prop]) if abs(t0 - rel_t) < 0.05]
        if existing:
            # 删除
            del kfs[prop][existing[0]]
            if not kfs[prop]:
                del kfs[prop]
        else:
            # 使用插值后的当前值作为关键帧值，避免跳变
            base_val = {prop: getattr(clip, prop, 0)}
            vals = interpolate_keyframes(clip, kfs, rel_t, base_val)
            val = vals.get(prop)
            if val is None:
                val = getattr(clip, prop, 0)
            kfs[prop] = [(t0, v) for t0, v in kfs[prop] if abs(t0 - rel_t) > 0.05]
            kfs[prop].append((rel_t, val))
        self._highlight_kf_buttons()
        self.tl.changed.emit()
        self.property_changed.emit()

    def _jump_kf(self, prop: str, direction: int):
        """跳转到上一个(-1)或下一个(+1)关键帧"""
        clip = self._clip
        if clip is None:
            return
        kfs = getattr(clip, "keyframes", None) or {}
        kf_list = sorted(kfs.get(prop, []), key=lambda x: x[0])
        if not kf_list:
            return
        t = self._current_sec if self._current_sec is not None else 0.0
        rel_t = t - clip.timeline_start
        if direction == 1:
            for kt, _ in kf_list:
                if kt > rel_t + 0.01:
                    self.seek_requested.emit(clip.timeline_start + kt)
                    return
        else:
            for kt, _ in reversed(kf_list):
                if kt < rel_t - 0.01:
                    self.seek_requested.emit(clip.timeline_start + kt)
                    return


# ── 字幕批量编辑对话框已移除（与字幕同步功能重复） ──

