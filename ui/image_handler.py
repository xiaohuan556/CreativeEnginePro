import os
import sys
import subprocess
import cv2
import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QGroupBox, QLineEdit, QComboBox, QFileDialog, QSlider,
    QSpinBox, QMessageBox, QProgressBar, QSizePolicy
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QUrl   # ← 这里加了 QUrl
from PyQt6.QtGui import QPixmap, QImage, QColor, QDragEnterEvent, QDropEvent
from .widgets import CheckMarkBox


# ==================== 独立工具函数（不挂在线程上）====================

def fit_image(img, tw, th, mode):
    """将图片适配到目标尺寸，独立函数供预览和导出共用"""
    h, w = img.shape[:2]
    if tw <= 0 or th <= 0:
        return img

    if mode == "等比缩放居中 (补模糊背景)":
        scale = min(tw / w, th / h)
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LANCZOS4)
        bg = cv2.resize(img, (tw, th), interpolation=cv2.INTER_LINEAR)
        bg = cv2.GaussianBlur(bg, (99, 99), 30)
        bg = (bg * 0.5).astype(np.uint8)
        x = max(0, (tw - nw) // 2)
        y = max(0, (th - nh) // 2)
        nh2 = min(nh, th - y)
        nw2 = min(nw, tw - x)
        bg[y:y+nh2, x:x+nw2] = resized[:nh2, :nw2]
        return bg

    elif mode == "强制拉伸":
        return cv2.resize(img, (tw, th), interpolation=cv2.INTER_LANCZOS4)

    elif mode == "等比缩放裁切 (充满)":
        scale = max(tw / w, th / h)
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LANCZOS4)
        x = max(0, (nw - tw) // 2)
        y = max(0, (nh - th) // 2)
        return resized[y:y+th, x:x+tw]

    return img


def format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f}MB"


# ==================== 图片导入线程 ====================

class ImageImportThread(QThread):
    item_ready = pyqtSignal(str, int, int, int)  # path, w, h, filesize
    finished = pyqtSignal()
    log_signal = pyqtSignal(str)

    def __init__(self, file_paths):
        super().__init__()
        self.file_paths = file_paths

    def run(self):
        for f in self.file_paths:
            try:
                size_bytes = os.path.getsize(f)
                img = cv2.imdecode(np.fromfile(f, dtype=np.uint8), cv2.IMREAD_COLOR)
                if img is not None:
                    h, w = img.shape[:2]
                    self.item_ready.emit(f, w, h, size_bytes)
                else:
                    self.log_signal.emit(f"无法读取图片: {os.path.basename(f)}")
            except Exception as e:
                self.log_signal.emit(f"读取出错: {os.path.basename(f)} — {e}")
        self.finished.emit()


# ==================== 图片导出线程 ====================

class ImageExportThread(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal()

    def __init__(self, tasks, config):
        super().__init__()
        self.tasks = tasks
        self.config = config
        self._is_running = True

    def stop(self):
        self._is_running = False

    def run(self):
        total = len(self.tasks)
        if total == 0:
            self.finished_signal.emit()
            return

        out_dir    = self.config['out_dir']
        target_w   = self.config['target_w']
        target_h   = self.config['target_h']
        mode       = self.config['mode']
        rename_tpl = self.config['rename'].strip()
        fmt        = self.config['format']
        quality    = self.config['quality']

        for i, task in enumerate(self.tasks):
            if not self._is_running:
                break

            row_idx = task['row_index']
            self.log_signal.emit(f"STATUS_UPDATE_IMG:{row_idx}:处理中...")

            try:
                src_path = task['path']
                img = cv2.imdecode(np.fromfile(src_path, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
                if img is None:
                    self.log_signal.emit(f"STATUS_UPDATE_IMG:{row_idx}:读取失败")
                    continue

                if len(img.shape) == 3 and img.shape[2] == 4:
                    img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

                # AI 去水印
                wm = self.config.get('watermark')
                if wm:
                    ih, iw = img.shape[:2]
                    x0 = max(0, iw - wm['margin_right'] - wm['width'])
                    y0 = max(0, ih - wm['margin_bottom'] - wm['height'])
                    x1 = min(iw, iw - wm['margin_right'])
                    y1 = min(ih, ih - wm['margin_bottom'])
                    if x0 < x1 and y0 < y1:
                        mask = np.zeros((ih, iw), dtype=np.uint8)
                        mask[y0:y1, x0:x1] = 255
                        img = cv2.inpaint(img, mask, 3, cv2.INPAINT_TELEA)

                result = fit_image(img, target_w, target_h, mode)

                base = os.path.splitext(os.path.basename(src_path))[0]
                if rename_tpl:
                    final_name = (rename_tpl
                                  .replace('{index}', str(i + 1).zfill(2))
                                  .replace('{name}', base))
                else:
                    final_name = base

                ext_map = {'JPG': '.jpg', 'PNG': '.png',
                           '原格式': os.path.splitext(src_path)[1]}
                ext = ext_map.get(fmt, os.path.splitext(src_path)[1])
                out_path = os.path.join(out_dir, final_name + ext)

                if ext.lower() in ['.jpg', '.jpeg']:
                    params = [cv2.IMWRITE_JPEG_QUALITY, quality]
                elif ext.lower() == '.png':
                    png_compress = max(0, min(9, int((100 - quality) / 11)))
                    params = [cv2.IMWRITE_PNG_COMPRESSION, png_compress]
                else:
                    params = []

                cv2.imencode(ext, result, params)[1].tofile(out_path)

                self.log_signal.emit(f"STATUS_UPDATE_IMG:{row_idx}:已完成")
                self.progress_signal.emit(int((i + 1) / total * 100))

            except Exception as e:
                self.log_signal.emit(f"STATUS_UPDATE_IMG:{row_idx}:出错")
                self.log_signal.emit(f"第{row_idx+1}张出错: {e}")

        self.finished_signal.emit()


# ==================== 可直接输入的宽高框 ====================

class SizeLineEdit(QLineEdit):
    """支持直接键盘输入数字的尺寸框，回车或失焦时校验范围"""
    value_changed = pyqtSignal(int)

    def __init__(self, default=1080, min_val=1, max_val=9999):
        super().__init__()
        self._min = min_val
        self._max = max_val
        self.setText(str(default))
        self.setFixedWidth(72)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.returnPressed.connect(self._commit)
        self.editingFinished.connect(self._commit)

    def _commit(self):
        try:
            v = max(self._min, min(self._max, int(self.text())))
        except ValueError:
            v = self._min
        self.setText(str(v))
        self.value_changed.emit(v)

    def value(self):
        try:
            return max(self._min, min(self._max, int(self.text())))
        except ValueError:
            return self._min

    def setValue(self, v):
        self.setText(str(v))


# ==================== 预览标签 ====================

class PreviewLabel(QLabel):
    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(160, 160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("""
            QLabel {
                background-color: #0d0d0d;
                border: 1px solid #2a2a2a;
                border-radius: 4px;
                color: #444;
                font-size: 12px;
            }
        """)
        self._cv_img = None
        self._mask_w = 300
        self._mask_h = 80
        self._mask_mr = 10
        self._mask_mb = 10
        self._mask_enabled = False
        self.setText("未选择")

    def set_image_from_cv(self, cv_img):
        self._cv_img = cv_img
        self._repaint()

    def set_mask(self, w, h, mr, mb, enabled=True):
        self._mask_w = w
        self._mask_h = h
        self._mask_mr = mr
        self._mask_mb = mb
        self._mask_enabled = enabled
        self._repaint()

    def _repaint(self):
        if self._cv_img is None:
            return
        img = self._cv_img.copy()
        # 蒙版叠加：在 BGR 图上画红色虚线框
        if self._mask_enabled:
            H, W = img.shape[:2]
            x0 = max(0, W - self._mask_mr - self._mask_w)
            y0 = max(0, H - self._mask_mb - self._mask_h)
            x1 = min(W, W - self._mask_mr)
            y1 = min(H, H - self._mask_mb)
            if x0 < x1 and y0 < y1:
                cv2.rectangle(img, (x0, y0), (x1, y1), (0, 0, 255), 3)
                # 四角手柄小方块
                hs = 6
                for cx, cy in [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]:
                    cv2.rectangle(img,
                                  (cx - hs, cy - hs), (cx + hs, cy + hs),
                                  (0, 0, 255), -1)
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data.tobytes(), w, h, ch * w, QImage.Format.Format_RGB888)
        pix = QPixmap.fromImage(qimg)
        self.setPixmap(pix.scaled(
            max(1, self.width() - 4), max(1, self.height() - 4),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        ))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._cv_img is not None:
            self._repaint()

    def clear_image(self):
        self._cv_img = None
        self.clear()
        self.setText("未选择")


# ==================== 图片处理 Mixin ====================

class ImageHandler:
    """图片处理模块，混入 UltimateEngine 使用"""

    # ------------------------------------------------------------------ #
    #  UI 构建
    # ------------------------------------------------------------------ #
    def build_image_module(self):
        # 初始化状态
        self.img_tasks = []
        self._current_preview_img = None
        self._preview_timer = QTimer()
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._do_refresh_preview_after)

        w = QWidget()
        w.setAcceptDrops(True)
        w.dragEnterEvent = self._img_drag_enter
        w.dropEvent      = self._img_drop

        root = QHBoxLayout(w)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # ============================================================
        # 左侧：队列
        # ============================================================
        left_p = QWidget()
        left_p.setAcceptDrops(True)
        left_p.dragEnterEvent = self._img_drag_enter
        left_p.dropEvent = self._img_drop
        ll = QVBoxLayout(left_p)
        ll.setSpacing(6)

        title = QLabel("图片队列 (Image Queue)")
        title.setStyleSheet("font-weight:bold; font-size:16px; color:#e0e0e0;")
        ll.addWidget(title)

        hint = QLabel("支持直接拖拽图片到此区域导入")
        hint.setStyleSheet("color:#555; font-size:11px;")
        ll.addWidget(hint)

        # -- 筛选栏 --
        filter_lay = QHBoxLayout()
        filter_lay.addWidget(QLabel("筛选尺寸:"))
        self.img_filter_combo = QComboBox()
        self.img_filter_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.img_filter_combo.addItem("全部")
        self.img_filter_combo.currentTextChanged.connect(self._apply_img_filter)
        filter_lay.addWidget(self.img_filter_combo, 1)
        btn_clear_filter = QPushButton("✖ 清除筛选")
        btn_clear_filter.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_clear_filter.setFixedWidth(90)
        btn_clear_filter.clicked.connect(
            lambda: self.img_filter_combo.setCurrentIndex(0))
        filter_lay.addWidget(btn_clear_filter)
        ll.addLayout(filter_lay)

        # -- 操作按钮行 --
        ctrl_lay = QHBoxLayout()
        self.btn_add_img = QPushButton("➕ 导入图片")
        self.btn_add_img.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_add_img.clicked.connect(self.mock_import_image)

        self.btn_select_all_img = QPushButton("全选/反选")
        self.btn_select_all_img.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_select_all_img.clicked.connect(self._img_toggle_select_all)

        btn_del_i = QPushButton("删除选中")
        btn_del_i.setObjectName("DangerBtn")
        btn_del_i.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_del_i.clicked.connect(self.delete_selected_image)

        btn_clear_i = QPushButton("清空列表")
        btn_clear_i.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_clear_i.clicked.connect(self._img_clear_list)

        ctrl_lay.addWidget(self.btn_add_img)
        ctrl_lay.addWidget(self.btn_select_all_img)
        ctrl_lay.addWidget(btn_del_i)
        ctrl_lay.addWidget(btn_clear_i)
        ctrl_lay.addStretch()
        ll.addLayout(ctrl_lay)

        # -- 表格 --
        self.i_table = QTableWidget(0, 6)
        self.i_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.i_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.i_table.setHorizontalHeaderLabels(
            ["选择", "文件名", "原尺寸", "修改后尺寸", "文件大小", "状态"])
        self.i_table.setColumnWidth(0, 40)
        self.i_table.setColumnWidth(4, 75)
        self.i_table.setColumnWidth(5, 70)
        hh = self.i_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.i_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # 表格本身也接受拖拽
        self.i_table.viewport().setAcceptDrops(True)
        self.i_table.viewport().dragEnterEvent = self._img_drag_enter
        self.i_table.viewport().dropEvent = self._img_drop
        self.i_table.itemSelectionChanged.connect(self._on_image_selected)
        ll.addWidget(self.i_table)

        # 进度条
        self.img_progress = QProgressBar()
        self.img_progress.setValue(0)
        self.img_progress.setVisible(False)
        ll.addWidget(self.img_progress)

        root.addWidget(left_p, 5)

        # ============================================================
        # 右侧：预览 + 参数 + 导出按钮
        # ============================================================
        right_p = QWidget()
        rl = QVBoxLayout(right_p)
        rl.setSpacing(8)

        # -- 预览对比 --
        preview_grp = QGroupBox("预览对比")
        preview_grp.setStyleSheet("QGroupBox { color: #00eaff; font-weight: bold; }")
        pg_lay = QHBoxLayout(preview_grp)
        pg_lay.setSpacing(8)
        self.preview_before = PreviewLabel()
        self.preview_after  = PreviewLabel()
        pg_lay.addWidget(self._wrap_preview(self.preview_before, "修改前"))
        pg_lay.addWidget(self._wrap_preview(self.preview_after,  "修改后"))
        rl.addWidget(preview_grp, stretch=3)

        # -- 参数区 --
        param_grp = QGroupBox("图片批量重构参数")
        pgl = QVBoxLayout(param_grp)
        pgl.setSpacing(7)

        # 目标尺寸（可直接输入）
        size_lay = QHBoxLayout()
        size_lay.addWidget(QLabel("目标 W:"))
        self.img_w = SizeLineEdit(1080)
        self.img_w.value_changed.connect(self._on_size_changed)
        size_lay.addWidget(self.img_w)
        size_lay.addWidget(QLabel("H:"))
        self.img_h = SizeLineEdit(1920)
        self.img_h.value_changed.connect(self._on_size_changed)
        size_lay.addWidget(self.img_h)
        # 快捷预设
        for label, ww, hh in [("9:16",1080,1920),("4:5",1080,1350),
                               ("1:1",1080,1080),("16:9",1920,1080)]:
            btn = QPushButton(label)
            btn.setFixedWidth(46)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setStyleSheet("padding:3px; font-size:11px;")
            btn.clicked.connect(lambda _, a=ww, b=hh: self._set_preset_size(a, b))
            size_lay.addWidget(btn)
        pgl.addLayout(size_lay)

        # 变形策略
        mode_lay = QHBoxLayout()
        mode_lay.addWidget(QLabel("变形策略:"))
        self.img_mode = QComboBox()
        self.img_mode.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.img_mode.addItems([
            "等比缩放居中 (补模糊背景)",
            "强制拉伸",
            "等比缩放裁切 (充满)"
        ])
        self.img_mode.currentIndexChanged.connect(self._schedule_preview_refresh)
        mode_lay.addWidget(self.img_mode, 1)
        pgl.addLayout(mode_lay)

        # 重命名
        rename_lay = QHBoxLayout()
        rename_lay.addWidget(QLabel("批量命名:"))
        self.i_rename = QLineEdit()
        self.i_rename.setPlaceholderText("留空保持原名，支持 {index} {name}")
        rename_lay.addWidget(self.i_rename, 1)
        pgl.addLayout(rename_lay)

        # 导出格式
        fmt_lay = QHBoxLayout()
        fmt_lay.addWidget(QLabel("导出格式:"))
        self.i_format = QComboBox()
        self.i_format.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.i_format.addItems(["原格式", "JPG", "PNG"])
        fmt_lay.addWidget(self.i_format)
        pgl.addLayout(fmt_lay)

        # 压缩质量
        quality_lay = QHBoxLayout()
        quality_lay.addWidget(QLabel("压缩质量:"))
        self.img_quality = QSlider(Qt.Orientation.Horizontal)
        self.img_quality.setRange(1, 100)
        self.img_quality.setValue(92)
        self.img_quality.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.img_quality_label = QLabel("92%")
        self.img_quality_label.setFixedWidth(36)
        self.img_quality.valueChanged.connect(
            lambda v: self.img_quality_label.setText(f"{v}%"))
        quality_lay.addWidget(self.img_quality, 1)
        quality_lay.addWidget(self.img_quality_label)
        pgl.addLayout(quality_lay)

        # 导出后自动打开文件夹
        self.cb_img_open_dir = CheckMarkBox("导出完成后自动打开文件夹")
        self.cb_img_open_dir.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cb_img_open_dir.setChecked(True)
        pgl.addWidget(self.cb_img_open_dir)

        # ── AI 去水印 ──
        self.cb_wm = CheckMarkBox("AI 去水印（固定蒙版）")
        self.cb_wm.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cb_wm.toggled.connect(self._on_wm_toggle)
        pgl.addWidget(self.cb_wm)

        self.wm_box = QWidget()
        wm_lay = QHBoxLayout(self.wm_box)
        wm_lay.setContentsMargins(0, 0, 0, 0); wm_lay.setSpacing(4)
        self.wm_w = QSpinBox(); self.wm_w.setRange(1, 4000); self.wm_w.setValue(300)
        self.wm_h = QSpinBox(); self.wm_h.setRange(1, 4000); self.wm_h.setValue(80)
        self.wm_mr = QSpinBox(); self.wm_mr.setRange(0, 2000); self.wm_mr.setValue(10)
        self.wm_mb = QSpinBox(); self.wm_mb.setRange(0, 2000); self.wm_mb.setValue(10)
        for s, tip in [(self.wm_w, "宽"), (self.wm_h, "高"),
                       (self.wm_mr, "距右"), (self.wm_mb, "距底")]:
            s.setToolTip(tip); s.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            s.valueChanged.connect(self._on_wm_param_changed)
            wm_lay.addWidget(QLabel(tip)); wm_lay.addWidget(s)
        self.wm_box.setVisible(False)
        pgl.addWidget(self.wm_box)

        param_grp.setLayout(pgl)
        rl.addWidget(param_grp)

        # 刷新预览
        self.btn_preview_refresh = QPushButton("刷新预览")
        self.btn_preview_refresh.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_preview_refresh.clicked.connect(self._do_refresh_preview_after)
        rl.addWidget(self.btn_preview_refresh)

        # 单条导出（高亮选中）
        self.btn_export_img_single = QPushButton("仅导出当前选中图片")
        self.btn_export_img_single.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_export_img_single.clicked.connect(self._start_img_export_single)
        rl.addWidget(self.btn_export_img_single)

        # 批量导出
        self.btn_export_img = QPushButton("一键批量处理所有勾选图片")
        self.btn_export_img.setObjectName("PrimaryBtn")
        self.btn_export_img.setFixedHeight(50)
        self.btn_export_img.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_export_img.clicked.connect(self._start_img_export)
        rl.addWidget(self.btn_export_img)

        root.addWidget(right_p, 4)
        return w

    # ------------------------------------------------------------------ #
    #  辅助
    # ------------------------------------------------------------------ #
    def _wrap_preview(self, label, title):
        wrap = QWidget()
        vl = QVBoxLayout(wrap)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(2)
        t = QLabel(title)
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t.setStyleSheet("color:#888; font-size:11px;")
        vl.addWidget(t)
        vl.addWidget(label, 1)
        return wrap

    # ------------------------------------------------------------------ #
    #  拖拽（widget + 表格都绑定）
    # ------------------------------------------------------------------ #
    def _img_drag_enter(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def _img_drop(self, event: QDropEvent):
        files = [u.toLocalFile() for u in event.mimeData().urls()]
        img_files = [f for f in files
                     if f.lower().endswith(
                         ('.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tiff'))]
        if img_files:
            self._execute_img_import(img_files)

    # ------------------------------------------------------------------ #
    #  导入
    # ------------------------------------------------------------------ #
    def mock_import_image(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "导入图片", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp *.tiff)"
        )
        if files:
            self._execute_img_import(files)

    def _execute_img_import(self, files):
        if not hasattr(self, 'img_tasks'):
            self.img_tasks = []
        existing = {t['path'] for t in self.img_tasks}
        new_files = [f for f in files if f not in existing]
        if not new_files:
            self.update_log("所选图片已全部存在于队列中。")
            return
        if len(new_files) < len(files):
            self.update_log(f"已过滤 {len(files)-len(new_files)} 张重复图片。")

        self.btn_add_img.setEnabled(False)
        self.img_import_thread = ImageImportThread(new_files)
        self.img_import_thread.item_ready.connect(self._add_image_item_to_table)
        self.img_import_thread.log_signal.connect(self.update_log)
        self.img_import_thread.finished.connect(self._on_img_import_finished)
        self.img_import_thread.start()

    def _on_img_import_finished(self):
        self.btn_add_img.setEnabled(True)
        self._rebuild_filter_combo()

    def _add_image_item_to_table(self, path, w, h, size_bytes):
        r = self.i_table.rowCount()
        self.i_table.insertRow(r)

        chk = QTableWidgetItem()
        chk.setCheckState(Qt.CheckState.Checked)
        self.i_table.setItem(r, 0, chk)

        name_item = QTableWidgetItem(os.path.basename(path))
        name_item.setData(Qt.ItemDataRole.UserRole, path)
        self.i_table.setItem(r, 1, name_item)

        self.i_table.setItem(r, 2, QTableWidgetItem(f"{w}×{h}"))
        self.i_table.setItem(r, 3, QTableWidgetItem(
            f"{self.img_w.value()}×{self.img_h.value()}"))
        self.i_table.setItem(r, 4, QTableWidgetItem(format_size(size_bytes)))

        status = QTableWidgetItem("就绪")
        status.setForeground(QColor("#888888"))
        self.i_table.setItem(r, 5, status)

        self.img_tasks.append({
            'path': path,
            'name': os.path.basename(path),
            'orig_w': w,
            'orig_h': h,
            'size_bytes': size_bytes,
        })

    # ------------------------------------------------------------------ #
    #  筛选
    # ------------------------------------------------------------------ #
    def _rebuild_filter_combo(self):
        """收集所有不重复的原始尺寸，重建筛选下拉"""
        current = self.img_filter_combo.currentText()
        sizes = sorted({f"{t['orig_w']}×{t['orig_h']}" for t in self.img_tasks})
        self.img_filter_combo.blockSignals(True)
        self.img_filter_combo.clear()
        self.img_filter_combo.addItem("全部")
        for s in sizes:
            self.img_filter_combo.addItem(s)
        idx = self.img_filter_combo.findText(current)
        self.img_filter_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.img_filter_combo.blockSignals(False)
        self._apply_img_filter(self.img_filter_combo.currentText())

    def _apply_img_filter(self, filter_text):
        """隐藏/显示行实现筛选，不破坏 img_tasks 顺序"""
        for r in range(self.i_table.rowCount()):
            if filter_text == "全部":
                self.i_table.setRowHidden(r, False)
            else:
                orig = (self.i_table.item(r, 2).text()
                        if self.i_table.item(r, 2) else "")
                self.i_table.setRowHidden(r, orig != filter_text)

    # ------------------------------------------------------------------ #
    #  删除 / 清空 / 全选
    # ------------------------------------------------------------------ #
    def delete_selected_image(self):
        rows = [r for r in range(self.i_table.rowCount())
                if self.i_table.item(r, 0) and
                   self.i_table.item(r, 0).checkState() == Qt.CheckState.Checked]
        if not rows:
            QMessageBox.information(self, "提示", "请先勾选左侧复选框再执行删除")
            return
        for r in reversed(rows):
            self.i_table.removeRow(r)
            if r < len(self.img_tasks):
                self.img_tasks.pop(r)
        self._rebuild_filter_combo()

    def _img_clear_list(self):
        if self.i_table.rowCount() == 0:
            return
        res = QMessageBox.question(self, "确认", "确定要清空所有图片吗？")
        if res == QMessageBox.StandardButton.Yes:
            self.i_table.setRowCount(0)
            self.img_tasks = []
            self._current_preview_img = None
            self.preview_before.clear_image()
            self.preview_after.clear_image()
            self.img_filter_combo.blockSignals(True)
            self.img_filter_combo.clear()
            self.img_filter_combo.addItem("全部")
            self.img_filter_combo.blockSignals(False)

    def _img_toggle_select_all(self):
        if self.i_table.rowCount() == 0:
            return
        visible = [r for r in range(self.i_table.rowCount())
                   if not self.i_table.isRowHidden(r)]
        if not visible:
            return
        first_state = self.i_table.item(visible[0], 0).checkState()
        new_state = (Qt.CheckState.Unchecked
                     if first_state == Qt.CheckState.Checked
                     else Qt.CheckState.Checked)
        for r in visible:
            if self.i_table.item(r, 0):
                self.i_table.item(r, 0).setCheckState(new_state)

    # ------------------------------------------------------------------ #
    #  预览（防抖 300ms）
    # ------------------------------------------------------------------ #
    def _on_wm_toggle(self, checked):
        self.wm_box.setVisible(checked)
        if self._current_preview_img is not None:
            self.preview_before.set_mask(
                self.wm_w.value(), self.wm_h.value(),
                self.wm_mr.value(), self.wm_mb.value(), enabled=checked)
        self._schedule_preview_refresh()

    def _on_wm_param_changed(self, _):
        if self.cb_wm.isChecked() and self._current_preview_img is not None:
            self.preview_before.set_mask(
                self.wm_w.value(), self.wm_h.value(),
                self.wm_mr.value(), self.wm_mb.value(), enabled=True)
        self._schedule_preview_refresh()

    def _apply_watermark(self, img):
        """对 BGR 图像应用去水印，返回处理后的图像。"""
        h, w = img.shape[:2]
        x0 = max(0, w - self.wm_mr.value() - self.wm_w.value())
        y0 = max(0, h - self.wm_mb.value() - self.wm_h.value())
        x1 = min(w, w - self.wm_mr.value())
        y1 = min(h, h - self.wm_mb.value())
        if x0 < x1 and y0 < y1:
            mask = np.zeros((h, w), dtype=np.uint8)
            mask[y0:y1, x0:x1] = 255
            return cv2.inpaint(img, mask, 3, cv2.INPAINT_TELEA)
        return img
    def _on_image_selected(self):
        selected = self.i_table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        if row >= len(self.img_tasks):
            return
        task = self.img_tasks[row]
        try:
            img = cv2.imdecode(
                np.fromfile(task['path'], dtype=np.uint8), cv2.IMREAD_COLOR)
            self._current_preview_img = img
            self.preview_before.set_image_from_cv(img)
            self._schedule_preview_refresh()
        except Exception as e:
            self.update_log(f"预览加载失败: {e}")

    def _on_size_changed(self, _):
        """宽高变化时，批量更新所有行的"修改后尺寸"列，并防抖刷新预览"""
        tw = self.img_w.value()
        th = self.img_h.value()
        for r in range(self.i_table.rowCount()):
            self.i_table.setItem(r, 3, QTableWidgetItem(f"{tw}×{th}"))
        self._schedule_preview_refresh()

    def _schedule_preview_refresh(self):
        self._preview_timer.start(300)

    def _do_refresh_preview_after(self):
        if self._current_preview_img is None:
            return
        tw   = self.img_w.value()
        th   = self.img_h.value()
        mode = self.img_mode.currentText()
        try:
            img = self._current_preview_img.copy()
            if self.cb_wm.isChecked():
                img = self._apply_watermark(img)
            result = fit_image(img, tw, th, mode)
            self.preview_after.set_image_from_cv(result)
        except Exception as e:
            self.update_log(f"预览渲染失败: {e}")

    def _set_preset_size(self, w, h):
        self.img_w.setValue(w)
        self.img_h.setValue(h)
        self._on_size_changed(None)

    # ------------------------------------------------------------------ #
    #  导出（公共）
    # ------------------------------------------------------------------ #
    def _build_export_config(self, out_dir):
        cfg = {
            'out_dir':  out_dir,
            'target_w': self.img_w.value(),
            'target_h': self.img_h.value(),
            'mode':     self.img_mode.currentText(),
            'rename':   self.i_rename.text().strip(),
            'format':   self.i_format.currentText(),
            'quality':  self.img_quality.value(),
        }
        if self.cb_wm.isChecked():
            cfg['watermark'] = {
                'width':        self.wm_w.value(),
                'height':       self.wm_h.value(),
                'margin_right': self.wm_mr.value(),
                'margin_bottom':self.wm_mb.value(),
            }
        return cfg

    def _lock_export_ui(self):
        self.btn_export_img.setEnabled(False)
        self.btn_export_img_single.setEnabled(False)
        self.btn_add_img.setEnabled(False)
        self.img_progress.setVisible(True)
        self.img_progress.setValue(0)

    def _unlock_export_ui(self):
        self.btn_export_img.setEnabled(True)
        self.btn_export_img_single.setEnabled(True)
        self.btn_add_img.setEnabled(True)
        self.img_progress.setVisible(False)
        self.img_progress.setValue(0)

    def _run_export(self, tasks_to_export):
        out_dir = QFileDialog.getExistingDirectory(self, "选择保存文件夹")
        if not out_dir:
            return
        self._img_export_out_dir = out_dir
        self._lock_export_ui()
        config = self._build_export_config(out_dir)
        self.img_export_thread = ImageExportThread(tasks_to_export, config)
        self.img_export_thread.log_signal.connect(self._on_img_log)
        self.img_export_thread.progress_signal.connect(self.img_progress.setValue)
        self.img_export_thread.finished_signal.connect(self._on_img_export_finished)
        self.img_export_thread.start()

    # -- 批量导出 --
    def _start_img_export(self):
        tasks_to_export = []
        for r in range(self.i_table.rowCount()):
            if (self.i_table.item(r, 0) and
                    self.i_table.item(r, 0).checkState() == Qt.CheckState.Checked
                    and r < len(self.img_tasks)):
                t = self.img_tasks[r].copy()
                t['row_index'] = r   # 始终用当前真实行号
                tasks_to_export.append(t)
        if not tasks_to_export:
            QMessageBox.warning(self, "提醒", "没有勾选任何图片！")
            return
        self._run_export(tasks_to_export)

    # -- 单条导出（高亮选中行）--
    def _start_img_export_single(self):
        selected = self.i_table.selectedItems()
        if not selected:
            QMessageBox.warning(self, "提醒", "请先在列表中点击选中（高亮）一张图片！")
            return
        row = selected[0].row()
        if row >= len(self.img_tasks):
            return
        t = self.img_tasks[row].copy()
        t['row_index'] = row
        self._run_export([t])

    # ------------------------------------------------------------------ #
    #  日志回调
    # ------------------------------------------------------------------ #
    def _on_img_log(self, text):
        if text.startswith("STATUS_UPDATE_IMG:"):
            try:
                _, row_idx, status_text = text.split(":", 2)
                item = QTableWidgetItem(status_text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if "完成" in status_text:
                    color = "#00ff88"
                elif "处理" in status_text:
                    color = "#ffcc00"
                else:
                    color = "#ff4444"
                item.setForeground(QColor(color))
                self.i_table.setItem(int(row_idx), 5, item)
            except Exception:
                pass
        else:
            self.update_log(text)

    def _on_img_export_finished(self):
        self._unlock_export_ui()
        QMessageBox.information(self, "完成", "图片处理完毕！")
        if (hasattr(self, 'cb_img_open_dir') and self.cb_img_open_dir.isChecked()):
            out_dir = getattr(self, '_img_export_out_dir', '')
            if out_dir and os.path.exists(out_dir):
                if sys.platform == 'win32':
                    os.startfile(out_dir)
                elif sys.platform == 'darwin':
                    subprocess.Popen(['open', out_dir])
                else:
                    subprocess.Popen(['xdg-open', out_dir])
