"""
AI 视频生成对话框 —— 从时间线视频片段右键菜单打开。

支持：
- Seedance 2.0 / Veo 3.1（ModelHub）
- 文生视频 / 图生视频（参考图来自本地文件）
- 时长 / 比例 / 是否生成音频
- 后台生成 + 进度轮询 + 结果预览
- 「添加到时间线」→ 调用 add_to_timeline_cb(path) 把生成视频作为新片段

线程安全：TaskManager 在后台线程执行 Provider，进度通过 QTimer 轮询 handle，
完成通过 handle._on_done 回调回到 GUI 线程。
"""
from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QUrl
from PyQt6.QtGui import QPixmap, QDesktopServices
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QTextEdit, QLineEdit,
    QComboBox, QPushButton, QLabel, QRadioButton, QButtonGroup,
    QFrame, QFileDialog, QDialog, QProgressBar, QCheckBox,
)

from ai import TaskRequest, ProviderDomain
from ai.service import get_ai_manager


PROVIDER_LABELS_VIDEO = {
    "seedance": "Seedance 2.0（豆包）",
    "veo": "Veo 3.1（ModelHub / OpenAI）",
}

RATIO_OPTIONS = ["adaptive", "16:9", "9:16", "1:1"]
DURATION_OPTIONS = [3, 4, 5, 6, 8, 10]
VEO_RATIO_OPTIONS = ["16:9", "9:16"]
VEO_DURATION_OPTIONS = [4, 6, 8]


class VideoGenDialog(QDialog):
    """AI 视频生成对话框。"""

    video_ready = pyqtSignal(str)

    def __init__(self, parent=None, reference_image: str | None = None,
                 add_to_timeline_cb=None, on_status=None):
        super().__init__(parent)
        self.setWindowTitle("🎬 AI 视频生成")
        self.setMinimumWidth(420)
        self.resize(460, 560)
        self._add_to_timeline_cb = add_to_timeline_cb
        self._on_status = on_status
        self._mgr = None
        self._handle = None
        self._result_path: str | None = None
        self._ref_path: str | None = reference_image
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(500)
        self._poll_timer.timeout.connect(self._poll_progress)

        self._init_manager()
        self._build_ui()
        if reference_image and os.path.exists(reference_image):
            self._rb_image.setChecked(True)
            self._set_ref(reference_image)

    # ── UI ──
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        title = QLabel("🎬 AI 视频生成")
        title.setStyleSheet("font-weight:bold; font-size:15px; color:#00eaff;")
        root.addWidget(title)

        # Provider（复用 _init_manager 已创建的 combo，仅挂上信号与布局）
        hprov = QHBoxLayout()
        hprov.addWidget(QLabel("引擎"))
        self._provider.currentTextChanged.connect(self._on_provider_changed)
        hprov.addWidget(self._provider, 1)
        root.addLayout(hprov)

        if not self._provider.count():
            self._notice = QLabel(getattr(self, "_init_error",
                "未检测到可用的视频生成引擎。\n请配置 SEEDREAM_API_KEY 或 OPENAI_API_KEY 后重启。"))
            self._notice.setStyleSheet("color:#e08; font-size:11px;")
            self._notice.setWordWrap(True)
            root.addWidget(self._notice)
            root.addStretch(1)
            return

        # 模式
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("模式"))
        self._mode_group = QButtonGroup(self)
        self._rb_text = QRadioButton("文生视频")
        self._rb_image = QRadioButton("图生视频")
        self._rb_text.setChecked(True)
        self._mode_group.addButton(self._rb_text, 0)
        self._mode_group.addButton(self._rb_image, 1)
        self._rb_text.toggled.connect(self._on_mode_changed)
        mode_row.addWidget(self._rb_text)
        mode_row.addWidget(self._rb_image)
        mode_row.addStretch(1)
        root.addLayout(mode_row)

        # 参考图
        self._ref_box = QGroupBox("参考图 / 首帧（图生视频）")
        rv = QVBoxLayout(self._ref_box)
        rv.setSpacing(6)
        self._ref_preview = QLabel("未选择")
        self._ref_preview.setFixedHeight(100)
        self._ref_preview.setStyleSheet(
            "background:#161618; border:1px solid #2c2c2c; color:#666; qproperty-alignment:AlignCenter;")
        self._ref_preview.setScaledContents(True)
        rv.addWidget(self._ref_preview)
        href = QHBoxLayout()
        self._ref_file_btn = QPushButton("选择文件")
        self._ref_file_btn.clicked.connect(self._pick_ref_file)
        self._ref_clear_btn = QPushButton("清除")
        self._ref_clear_btn.clicked.connect(self._clear_ref)
        href.addWidget(self._ref_file_btn)
        href.addWidget(self._ref_clear_btn)
        rv.addLayout(href)
        self._ref_box.setVisible(False)
        root.addWidget(self._ref_box)


        # Prompt
        self._prompt = QTextEdit()
        self._prompt.setPlaceholderText("描述你想生成的视频，例如：镜头缓慢推进，一只猫在霓虹都市的屋顶上行走，电影质感")
        self._prompt.setMaximumHeight(80)
        self._prompt.setAcceptRichText(False)
        root.addWidget(self._prompt)

        # 时长 + 比例
        h1 = QHBoxLayout()
        h1.addWidget(QLabel("时长"))
        self._duration = QComboBox()
        self._duration.addItems([str(d) for d in DURATION_OPTIONS])
        self._duration.setCurrentText("5")
        h1.addWidget(self._duration, 1)
        h1.addWidget(QLabel("比例"))
        self._ratio = QComboBox()
        self._ratio.addItems(RATIO_OPTIONS)
        self._ratio.setCurrentText("16:9")
        h1.addWidget(self._ratio, 1)
        root.addLayout(h1)

        # 音频
        self._audio = QCheckBox("生成配音 / 音效（原生音画）")
        self._audio.setChecked(True)
        root.addWidget(self._audio)

        # 生成
        self._gen_btn = QPushButton("✨ 生成视频")
        self._gen_btn.setStyleSheet(
            "QPushButton{background:#3d8ef8;color:#fff;font-weight:bold;border-radius:4px;padding:7px;}"
            "QPushButton:hover{background:#5aa0ff;}")
        self._gen_btn.clicked.connect(self._on_generate)
        root.addWidget(self._gen_btn)

        self._prog = QProgressBar()
        self._prog.setRange(0, 100)
        self._prog.setValue(0)
        root.addWidget(self._prog)

        self._status = QLabel("")
        self._status.setStyleSheet("color:#999; font-size:11px;")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        # 结果
        self._result = QLabel("生成结果将显示在这里")
        self._result.setMinimumHeight(60)
        self._result.setStyleSheet(
            "background:#161618; border:1px solid #2c2c2c; color:#666; qproperty-alignment:AlignCenter;")
        root.addWidget(self._result)

        hres = QHBoxLayout()
        self._add_btn = QPushButton("⬇ 添加到时间线")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._on_add_to_timeline)
        self._play_btn = QPushButton("▶ 播放")
        self._play_btn.setEnabled(False)
        self._play_btn.clicked.connect(self._on_play)
        self._open_btn = QPushButton("📁 打开文件夹")
        self._open_btn.setEnabled(False)
        self._open_btn.clicked.connect(self._on_open_folder)
        hres.addWidget(self._add_btn)
        hres.addWidget(self._play_btn)
        hres.addWidget(self._open_btn)
        root.addLayout(hres)

        root.addStretch(1)
        self._on_provider_changed(self._provider.currentText())

    # ── manager ──
    def _init_manager(self):
        # 先创建并填充 provider；_build_ui 会复用本对象。
        # 用 blockSignals 避免 addItem 期间触发信号。
        try:
            self._mgr = get_ai_manager()
            vids = self._mgr.registry.by_domain(ProviderDomain.VIDEO)
        except Exception as e:  # noqa: BLE001
            self._mgr = None
            vids = []
        self._provider = QComboBox()
        self._provider.setMinimumWidth(180)
        self._provider.blockSignals(True)
        for p in vids:
            self._provider.addItem(PROVIDER_LABELS_VIDEO.get(p.name, p.name), p.name)
        self._provider.blockSignals(False)
        if not vids:
            self._init_error = "未检测到可用的视频生成引擎。\n请配置 SEEDREAM_API_KEY 或 OPENAI_API_KEY 后重启。"

    # ── 交互 ──
    def _on_provider_changed(self, _):
        provider = self._provider.currentData() or ""
        ratios = VEO_RATIO_OPTIONS if provider == "veo" else RATIO_OPTIONS
        durations = VEO_DURATION_OPTIONS if provider == "veo" else DURATION_OPTIONS
        old_ratio = self._ratio.currentText()
        old_duration = int(self._duration.currentText() or 8)

        self._ratio.blockSignals(True)
        self._ratio.clear()
        self._ratio.addItems(ratios)
        self._ratio.setCurrentText(
            old_ratio if old_ratio in ratios else "16:9")
        self._ratio.blockSignals(False)

        self._duration.blockSignals(True)
        self._duration.clear()
        self._duration.addItems([str(value) for value in durations])
        nearest = min(durations, key=lambda value: abs(value - old_duration))
        self._duration.setCurrentText(str(nearest))
        self._duration.blockSignals(False)
        self._ratio.setToolTip(
            "Veo 3.1 仅支持 16:9 和 9:16" if provider == "veo" else "")

    def _on_mode_changed(self, _):
        self._ref_box.setVisible(self._rb_image.isChecked())

    def _pick_ref_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择参考图 / 首帧", "", "图片 (*.png *.jpg *.jpeg *.webp)")
        if path:
            self._set_ref(path)

    def _set_ref(self, path: str):
        self._ref_path = path
        pm = QPixmap(path)
        if not pm.isNull():
            self._ref_preview.setPixmap(
                pm.scaled(180, 100, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation))
        self._ref_preview.setText("")

    def _clear_ref(self):
        self._ref_path = None
        self._ref_preview.clear()
        self._ref_preview.setText("未选择")

    # ── 生成 ──
    def _on_generate(self):
        if self._mgr is None or not self._provider.count():
            self._set_status("未配置视频生成引擎")
            return
        prompt = self._prompt.toPlainText().strip()
        if not prompt:
            self._set_status("请输入描述（prompt）")
            return
        prov = self._provider.currentData() or self._provider.currentText()
        is_img = self._rb_image.isChecked()
        operation = "image_to_video" if is_img else "text_to_video"

        if is_img and not self._ref_path:
            self._set_status("图生视频需要选择参考图 / 首帧")
            return
        duration = int(self._duration.currentText())
        ratio = self._ratio.currentText()
        gen_audio = self._audio.isChecked()

        params: dict = {"duration": duration, "generate_audio": gen_audio}
        if prov == "seedance":
            params["ratio"] = ratio
            params["watermark"] = False
        else:  # veo
            params["aspect_ratio"] = "16:9" if ratio == "adaptive" else ratio
            params["resolution"] = "720p"

        inputs: dict = {"prompt": prompt}
        if is_img and self._ref_path:
            inputs["image"] = self._ref_path

        req = TaskRequest(operation=operation, inputs=inputs, params=params)
        self._set_status("提交生成任务…（视频生成可能需数十秒至数分钟）")
        self._gen_btn.setEnabled(False)
        self._prog.setValue(0)
        self._result_path = None
        self._add_btn.setEnabled(False)
        self._play_btn.setEnabled(False)
        self._open_btn.setEnabled(False)
        try:
            h = self._mgr.submit(prov, req)
            self._handle = h
            h._on_done.append(lambda hh: self._on_done(hh))
            self._poll_timer.start()
        except Exception as e:  # noqa: BLE001
            self._set_status(f"提交失败：{e}")
            self._gen_btn.setEnabled(True)

    def _poll_progress(self):
        if self._handle is None:
            self._poll_timer.stop()
            return
        prog = int(self._handle.progress * 100)
        self._prog.setValue(max(self._prog.value(), prog))
        if self._handle.is_finished:
            self._poll_timer.stop()

    def _on_done(self, h):
        self._gen_btn.setEnabled(True)
        self._poll_timer.stop()
        self._prog.setValue(100 if h.is_success else self._prog.value())
        if h.is_success and h.result:
            self._result_path = str(h.result.data)
            self._result.setText(f"✅ {os.path.basename(self._result_path)}")
            self._result.setStyleSheet(
                "background:#16241a; border:1px solid #2c2c2c; color:#7fe; qproperty-alignment:AlignCenter;")
            self._add_btn.setEnabled(True)
            self._play_btn.setEnabled(True)
            self._open_btn.setEnabled(True)
            self._set_status("生成完成 ✓")
            self.video_ready.emit(self._result_path)
        else:
            err = h.result.error if h.result else "未知错误"
            if "not authorized for this api key" in err.lower():
                err = ("当前 ModelHub API Key 没有 Seedance 2.0 权限；请在 API Key "
                       "管理中把 doubao-seedance-2.0 加入模型范围")
            self._result.setText("❌ 生成失败")
            self._set_status(f"失败：{err}")

    # ── 结果操作 ──
    def _on_add_to_timeline(self):
        if not self._result_path:
            return
        if self._add_to_timeline_cb is not None:
            try:
                self._add_to_timeline_cb(self._result_path)
                self._set_status("已添加到时间线 ✓")
            except Exception as e:  # noqa: BLE001
                self._set_status(f"添加失败：{e}")
        else:
            self._set_status("未提供添加到时间线的回调")

    def _on_play(self):
        if self._result_path and os.path.exists(self._result_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._result_path))

    def _on_open_folder(self):
        if not self._result_path:
            return
        folder = str(Path(self._result_path).parent)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    # ── 工具 ──
    def _set_status(self, msg: str):
        self._status.setText(msg)
        if self._on_status:
            try:
                self._on_status(msg)
            except Exception:  # noqa: BLE001
                pass
