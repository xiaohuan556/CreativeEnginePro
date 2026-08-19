"""
AI 助手面板 —— 注入到剪辑工作台右侧第三栏（与属性面板共用一个 QStackedWidget）。

提供 2 个 Tab：
- 🎬 视频生成（自实现 VideoGenPanel，支持截取时间线帧作参考图、首尾帧过渡）
- 📜 历史记录（共用 _HistoryView，记录所有生成项，可一键插入素材库 / 时间线）

公共 API：
- open_video_gen(reference_image=None) / open_history()
- add_history(kind, path, prompt)
"""
from __future__ import annotations

import os
import json
from pathlib import Path
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QLineEdit,
    QComboBox, QPushButton, QLabel, QRadioButton, QButtonGroup,
    QFrame, QProgressBar, QSlider, QScrollArea,
    QToolButton, QCheckBox, QListWidget, QListWidgetItem, QTabWidget,
)

from ai import TaskRequest, ProviderDomain
from ai.service import get_ai_manager
from ai.ui.image_ai_panel import _ChipGroup, _ChipButton, _Ref1Slot


# ═══════ 视频生成面板 ═══════

PROVIDER_LABELS_VIDEO = {
    "seedance": "Seedance 2.0（豆包）",
    "veo": "Veo 3.1（ModelHub / OpenAI）",
}

VIDEO_DURATION_OPTIONS = [4, 6, 8]
VIDEO_RATIO_OPTIONS = ["adaptive", "16:9", "9:16", "1:1", "4:5", "3:4"]


class VideoGenPanel(QWidget):
    """视频生成子面板：嵌入 AIAssistantPanel 的视频 Tab。

    设计：
    - 引擎下拉 + prompt + 负向词 + 时长/比例
    - 参考图 1（首帧）+ 参考图 2（尾帧）并排，各带 📷 截帧按钮
    - 🌓 首尾帧模式开关（ref1=首帧, ref2=尾帧 → veo lastFrame）
    - ＋ 按钮可无限添加参考图 3/4/5...（风格参考，竖排堆叠）
    - 参考强度（滚轮禁用，仅拖拽）
    - 生成按钮 + 进度 + 状态
    """

    video_ready = pyqtSignal(str)
    add_to_timeline_requested = pyqtSignal(str)
    add_to_library_requested = pyqtSignal(str)
    capture_frame_requested = pyqtSignal(int)  # slot index: 1=ref1, 2=ref2, ...

    REF_SLOT_STYLE = (
        "QFrame{background:#161618;border:1px dashed #3a3a3a;border-radius:6px;}"
    )

    def __init__(self, parent=None, on_status=None):
        super().__init__(parent)
        self._on_status = on_status
        self._mgr = None
        self._handle = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(500)
        self._poll_timer.timeout.connect(self._poll_progress)

        self._result_paths: list[str] = []

        # 动态参考图槽位：[(idx, slot_widget, header_label), ...]
        # idx=1 → ref1, idx=2 → ref2, idx>=3 → 动态添加
        self._ref_slots: list[dict] = []       # [{idx, slot, header, cap_btn, del_btn}]

        self._init_manager()
        self._build_ui()

    # ── manager ──
    def _init_manager(self):
        try:
            self._mgr = get_ai_manager()
            vids = self._mgr.registry.by_domain(ProviderDomain.VIDEO)
        except Exception:
            self._mgr = None
            vids = []
        self._provider = QComboBox()
        self._provider.setMinimumWidth(140)
        self._provider.setStyleSheet(
            "QComboBox{color:#eee;background:#1e1e22;border:1px solid #3a3a3a;"
            "border-radius:4px;padding:2px 6px;}"
            "QComboBox::drop-down{border:0;width:18px;}"
            "QComboBox QAbstractItemView{background:#1e1e22;color:#eee;"
            "selection-background-color:#3d8ef8;selection-color:#fff;outline:0;}"
            "QComboBox QAbstractItemView::item:hover{background:#3d8ef8;color:#fff;}"
            "QComboBox QAbstractItemView::item:selected{background:#3d8ef8;color:#fff;}")
        self._provider.blockSignals(True)
        for p in vids:
            self._provider.addItem(PROVIDER_LABELS_VIDEO.get(p.name, p.name), p.name)
        self._provider.blockSignals(False)
        if not vids:
            self._init_error = "未检测到可用的视频生成引擎。\n请配置 SEEDREAM_API_KEY 或 OPENAI_API_KEY 后重启。"

    # ── 错误友好化 ──
    @staticmethod
    def _friendly_error(err: str) -> str:
        """把冗长英文错误转为简短中文提示。"""
        if not err:
            return "未知错误"
        err_lower = err.lower()
        if "not authorized for this api key" in err_lower or (
                "403" in err and "model" in err_lower):
            return ("当前 ModelHub API Key 没有 Seedance 2.0 权限。请在 ModelHub 的 "
                    "API Key 管理中把 doubao-seedance-2.0 加入模型范围")
        # Seedance 2.0 真人隐私保护
        if "privacyinformation" in err_lower or "real person" in err_lower or "真人隐私" in err:
            return "参考帧包含可识别真人，Seedance 已按隐私策略拒绝。请改用非真人/已授权素材，或切换 Veo 引擎"
        # 版权 / 审核（优先级最高）
        if any(kw in err_lower for kw in ("copyright", "版权", "审核", "safety", "content polic")):
            return "参考图可能包含版权或敏感内容，被安全策略拦截。请换用原创图片或切换 Veo 引擎"
        # base64 / 图片格式错误
        if "invalid base64" in err_lower or "invalid image" in err_lower:
            return ("参考图数据在提交时未被 Seedance 正确识别（Invalid base64）。"
                    "这属于图片传输/编码错误，不代表内容审核拒绝；请重新截帧后重试")
        if "image too" in err_lower or "size" in err_lower and "image" in err_lower:
            return "参考图过大或尺寸超限，请压缩到 4096×4096 以内"
        if "缺少 prompt" in err or "prompt" in err_lower:
            return "请输入视频描述"
        if "ark" in err_lower or "seedance" in err_lower:
            if "key" in err_lower or "auth" in err_lower or "401" in err:
                return "API 密钥无效，请检查配置"
            if "35561375" in err:
                return "内容未通过安全审核，请修改描述或参考图后重试"
            if "timeout" in err_lower or "超时" in err or "轮询" in err:
                return "生成超时，请重试或缩短视频时长"
            return err[:80] + ("…" if len(err) > 80 else "")
        if "未注册" in err:
            return "引擎未就绪，请检查 API 配置"
        if "网络" in err or "network" in err_lower or "connect" in err_lower:
            return "网络连接失败，请检查网络"
        if "取消" in err or "cancel" in err_lower:
            return "已取消"
        return err[:60] + ("…" if len(err) > 60 else "")

    # ── UI ──
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # 引擎
        prov_row = QHBoxLayout()
        prov_row.addWidget(QLabel("引擎"))
        self._provider.currentTextChanged.connect(lambda _: self._refresh_provider_params())
        prov_row.addWidget(self._provider, 1)
        root.addLayout(prov_row)

        if not self._provider.count():
            self._notice = QLabel(getattr(self, "_init_error", ""))
            self._notice.setStyleSheet("color:#e08; font-size:11px;")
            self._notice.setWordWrap(True)
            root.addWidget(self._notice)
            root.addStretch(1)
            return

        # 描述
        self._prompt = QTextEdit()
        self._prompt.setPlaceholderText("描述你想生成的视频，例如：一只橘猫在城市天台上奔跑，霓虹灯光，慢动作")
        self._prompt.setMaximumHeight(72)
        self._prompt.setAcceptRichText(False)
        self._prompt.setStyleSheet(
            "QTextEdit{background:#1a1a1c;color:#ddd;border:1px solid #2c2c2c;"
            "border-radius:4px;padding:6px;}")
        root.addWidget(self._prompt)

        root.addWidget(self._section_label("创意提示词（图生视频）"))
        self._creative_prompt = QTextEdit()
        self._creative_prompt.setPlaceholderText(
            "加入你的想法：人物怎样动、镜头怎样走、节奏、氛围和光影变化……")
        self._creative_prompt.setMaximumHeight(58)
        self._creative_prompt.setAcceptRichText(False)
        self._creative_prompt.setStyleSheet(self._prompt.styleSheet())
        root.addWidget(self._creative_prompt)

        # 负向词（折叠）
        self._neg_toggle = QToolButton()
        self._neg_toggle.setText("∨ 负向词（排除内容）")
        self._neg_toggle.setCheckable(True)
        self._neg_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._neg_toggle.setStyleSheet(
            "QToolButton{background:transparent;color:#888;border:0;text-align:left;padding:4px 0;}")
        self._neg_toggle.toggled.connect(self._on_neg_toggle)
        root.addWidget(self._neg_toggle)
        self._neg_edit = QTextEdit()
        self._neg_edit.setPlaceholderText("不希望出现的元素：抖动、畸形、低质量、字幕水印")
        self._neg_edit.setMaximumHeight(50)
        self._neg_edit.setVisible(False)
        self._neg_edit.setStyleSheet(self._prompt.styleSheet())
        root.addWidget(self._neg_edit)

        # 时长 + 比例 chip
        self._duration_group = _ChipGroup(
            [{"key": str(d), "label": f"{d} 秒"} for d in VIDEO_DURATION_OPTIONS],
            default_key="8",
        )
        self._ratio_group = _ChipGroup(
            [{"key": r, "label": r} for r in VIDEO_RATIO_OPTIONS],
            default_key="16:9",
        )
        d_row = QVBoxLayout()
        d_row.setSpacing(2)
        d_row.addWidget(self._section_label("时长"))
        d_row.addWidget(self._duration_group)
        root.addLayout(d_row)

        r_row = QVBoxLayout()
        r_row.setSpacing(2)
        r_row.addWidget(self._section_label("比例"))
        r_row.addWidget(self._ratio_group)
        root.addLayout(r_row)

        # ── 参考图区域 ──
        root.addWidget(self._section_label("参考图"))
        self._refs_container = QVBoxLayout()
        self._refs_container.setSpacing(4)
        root.addLayout(self._refs_container)

        # 首尾帧模式开关（放在参考图区域顶部）
        fl_row = QHBoxLayout()
        fl_row.setSpacing(4)
        self._first_last_toggle = QCheckBox("🌓 首尾帧模式（参考图1=首帧，参考图2=尾帧）")
        self._first_last_toggle.setChecked(False)
        self._first_last_toggle.setToolTip("勾选后，参考图1作为视频起始帧、参考图2作为结束帧，生成平滑过渡视频")
        self._first_last_toggle.setStyleSheet(
            "QCheckBox{color:#bbb;font-size:10px;padding:2px 4px;}"
            "QCheckBox::indicator{width:12px;height:12px;}"
            "QToolTip{color:#fff;background:#2a2a2a;border:1px solid #3a3a3a;padding:2px 6px;}")
        fl_row.addWidget(self._first_last_toggle)
        fl_row.addStretch(1)
        self._refs_container.addLayout(fl_row)

        # 参考图 1+2 并排
        ref12_row = QHBoxLayout()
        ref12_row.setSpacing(8)
        self._ref1 = self._build_ref_slot(1, "参考图 1（首帧）", ref12_row, show_capture=True)
        self._ref2 = self._build_ref_slot(2, "参考图 2（尾帧）", ref12_row, show_capture=True)
        self._refs_container.addLayout(ref12_row)

        # ＋ 按钮 —— 添加参考图 3/4/5...
        add_row = QHBoxLayout()
        add_row.addStretch(1)
        self._add_ref_btn = QPushButton("＋ 添加参考图")
        self._add_ref_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_ref_btn.setStyleSheet(
            "QPushButton{background:transparent;color:#5aa0ff;border:1px dashed #3a3a3a;"
            "border-radius:4px;padding:4px 12px;font-size:10px;}"
            "QPushButton:hover{background:#1a2a3a;color:#8ac8ff;}")
        self._add_ref_btn.clicked.connect(self._on_add_ref)
        add_row.addWidget(self._add_ref_btn)
        add_row.addStretch(1)
        self._refs_container.addLayout(add_row)

        # 参考强度（滚轮禁用）
        sr = QVBoxLayout()
        sr.setSpacing(2)
        sr.addWidget(self._section_label("参考强度"))
        self._strength = _NoWheelSlider(Qt.Orientation.Horizontal)
        self._strength.setRange(0, 100)
        self._strength.setValue(70)
        self._strength.setStyleSheet(
            "QSlider::groove:horizontal{height:4px;background:#2a2a2a;border-radius:2px;}"
            "QSlider::sub-page:horizontal{background:#3d8ef8;border-radius:2px;}"
            "QSlider::handle:horizontal{background:#fff;width:14px;height:14px;"
            "margin:-5px 0;border-radius:7px;border:1px solid #3d8ef8;}")
        sr.addWidget(self._strength)
        self._strength_value = QLabel("70%")
        self._strength_value.setStyleSheet("color:#888; font-size:10px;")
        self._strength_value.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._strength.valueChanged.connect(lambda v: self._strength_value.setText(f"{v}%"))
        sr.addWidget(self._strength_value)
        root.addLayout(sr)

        self._refresh_provider_params()

        # 立即生成
        self._gen_btn = QPushButton("✨ 立即生成")
        self._gen_btn.setMinimumHeight(38)
        self._gen_btn.setStyleSheet(
            "QPushButton{background:#3d8ef8;color:#fff;font-weight:bold;"
            "border-radius:6px;font-size:13px;}"
            "QPushButton:hover{background:#5aa0ff;}"
            "QPushButton:disabled{background:#2a3a55;color:#888;}")
        self._gen_btn.clicked.connect(self._on_generate)
        root.addWidget(self._gen_btn)

        # 进度 + 状态
        self._prog = QProgressBar()
        self._prog.setRange(0, 100)
        self._prog.setStyleSheet(
            "QProgressBar{background:#1a1a1c;border:1px solid #2c2c2c;border-radius:3px;"
            "text-align:center;color:#ddd;height:16px;}"
            "QProgressBar::chunk{background:#3d8ef8;border-radius:3px;}")
        root.addWidget(self._prog)
        self._status = QLabel("")
        self._status.setStyleSheet("color:#999; font-size:11px;")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        root.addStretch(1)

    # ── 参考图槽位构建 ──
    def _build_ref_slot(self, idx: int, title: str, parent_layout: QHBoxLayout,
                        show_capture: bool = True, show_delete: bool = False) -> _Ref1Slot:
        """创建单个参考图槽位（竖排：标题栏 + Ref1Slot），返回 _Ref1Slot 实例。"""
        box = QVBoxLayout()
        box.setSpacing(2)

        # 标题栏
        header = QHBoxLayout()
        header.setSpacing(4)
        title_lab = QLabel(title)
        title_lab.setStyleSheet("color:#bbb; font-size:11px;")
        header.addWidget(title_lab)
        header.addStretch(1)

        if show_capture:
            cap_btn = QToolButton()
            cap_btn.setText("📷")
            cap_btn.setToolTip("截取时间线当前帧")
            cap_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            cap_btn.setStyleSheet(
                "QToolButton{background:transparent;color:#5aa0ff;border:0;"
                "font-size:11px;padding:0 2px;}"
                "QToolButton:hover{color:#8ac8ff;}"
                "QToolTip{color:#fff;background:#2a2a2a;border:1px solid #3a3a3a;padding:2px 6px;}")
            cap_btn.clicked.connect(lambda: self.capture_frame_requested.emit(idx))
            header.addWidget(cap_btn)

        if show_delete:
            del_btn = QToolButton()
            del_btn.setText("×")
            del_btn.setToolTip("移除此参考图")
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.setStyleSheet(
                "QToolButton{background:transparent;color:#e44;border:0;"
                "font-size:14px;font-weight:bold;padding:0 2px;}"
                "QToolButton:hover{color:#f66;}"
                "QToolTip{color:#fff;background:#2a2a2a;border:1px solid #3a3a3a;padding:2px 6px;}")
            del_btn.clicked.connect(lambda: self._remove_ref(idx))
            header.addWidget(del_btn)

        box.addLayout(header)

        slot = _Ref1Slot(f"参考图 {idx}")
        box.addWidget(slot, 1)

        parent_layout.addLayout(box, 1)

        entry = {
            "idx": idx, "slot": slot, "header": title_lab,
            "layout_box": box, "parent_layout": parent_layout,
        }
        self._ref_slots.append(entry)
        return slot

    def _on_add_ref(self):
        """添加新的参考图槽位（3, 4, 5...）。"""
        next_idx = max((r["idx"] for r in self._ref_slots), default=2) + 1

        row_layout = QHBoxLayout()
        row_layout.setSpacing(0)
        self._build_ref_slot(next_idx, f"参考图 {next_idx}", row_layout,
                             show_capture=True, show_delete=True)
        # 把 row_layout 写入 entry（供 _remove_ref 完整清理）
        self._ref_slots[-1]["row_layout"] = row_layout
        # 插入在 + 按钮之前
        self._refs_container.insertLayout(
            self._refs_container.count() - 1, row_layout)

    def _remove_ref(self, idx: int):
        """移除 idx>=3 的动态参考图槽位（连同行布局完整清理，不留空行）。"""
        entry = next((r for r in self._ref_slots if r["idx"] == idx), None)
        if entry is None:
            return
        # 递归删除 layout_box 中的所有 widget
        box = entry["layout_box"]
        self._delete_layout_widgets(box)
        # 从父布局移除 box
        parent = entry["parent_layout"]
        parent.removeItem(box)
        # 如果是独占行（ref3+），把整行从容器中移除
        row = entry.get("row_layout")
        if row is not None:
            self._delete_layout_widgets(row)
            self._refs_container.removeItem(row)
        self._ref_slots.remove(entry)

    @staticmethod
    def _delete_layout_widgets(layout):
        """递归删除 layout 中所有 widget 和子 layout。"""
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
            child = item.layout()
            if child is not None:
                VideoGenPanel._delete_layout_widgets(child)

    # ── 小工具 ──
    def _section_label(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setStyleSheet("color:#aaa; font-size:11px; padding-top:4px;")
        return lab

    def _on_neg_toggle(self, checked: bool):
        self._neg_edit.setVisible(checked)
        self._neg_toggle.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)

    # ── 外部入口 ──
    def set_ref(self, slot_idx: int, path: str):
        """设置指定槽位的参考图。"""
        entry = next((r for r in self._ref_slots if r["idx"] == slot_idx), None)
        if entry and path and os.path.exists(path):
            entry["slot"].set_path(path)
            label = {1: "参考图 1（首帧）", 2: "参考图 2（尾帧）"}.get(slot_idx, f"参考图 {slot_idx}")
            self._set_status(f"已截取当前帧作为{label}")

    def prefill_reference_image(self, path: str):
        """外部入口：从时间线唤起时填入参考图 1。"""
        self.set_ref(1, path)

    def prefill_storyboard(self, prompt: str, ratio: str = "16:9", duration: int = 8):
        """外部分镜入口：填入镜头描述、比例和最接近的可用时长。"""
        self._prompt.setPlainText((prompt or "").strip())
        if (self._provider.currentData() == "veo"
                and ratio not in {"16:9", "9:16"}):
            ratio = "9:16" if ratio in {"3:4", "4:5"} else "16:9"
        if ratio in VIDEO_RATIO_OPTIONS:
            self._ratio_group.set_selected_key(ratio)
        nearest = min(VIDEO_DURATION_OPTIONS, key=lambda value: abs(value - int(duration or 8)))
        self._duration_group.set_selected_key(str(nearest))
        self._prompt.setFocus()

    def _all_ref_paths(self) -> list[tuple[int, str | None]]:
        """返回 [(idx, path), ...] 所有已填参考图。"""
        return [(r["idx"], r["slot"].path) for r in self._ref_slots if r["slot"].path]

    def _ref_path(self, idx: int) -> str | None:
        entry = next((r for r in self._ref_slots if r["idx"] == idx), None)
        return entry["slot"].path if entry else None

    # ── provider ──
    def _refresh_provider_params(self):
        provider = self._provider.currentData() or ""
        if provider == "veo":
            self._ratio_group.set_enabled_keys(
                {"16:9", "9:16"}, fallback_key="16:9")
            self._ratio_group.setToolTip(
                "Veo 3.1 仅支持 16:9 和 9:16")
        else:
            self._ratio_group.set_enabled_keys(None)
            self._ratio_group.setToolTip("")

    # ── 生成 ──
    @staticmethod
    def _image_video_prompt(base_prompt: str, creative_prompt: str) -> str:
        base = str(base_prompt or "").strip()
        creative = str(creative_prompt or "").strip()
        if not creative:
            return base
        return (
            f"{base}\n\n【用户创意与动态意图】\n{creative}\n"
            "保持输入首帧及尾帧中的主体、构图和画面设定，只实现上述动作、"
            "运镜、节奏与氛围变化。"
        ).strip()

    def _on_generate(self):
        if self._mgr is None or not self._provider.count():
            self._set_status("未配置视频生成引擎")
            return
        prompt = self._prompt.toPlainText().strip()
        all_refs = self._all_ref_paths()
        has_ref = any(p for _, p in all_refs)
        if not prompt and not has_ref:
            self._set_status("请输入描述或上传参考图")
            return

        prov = self._provider.currentData()
        operation = "image_to_video" if has_ref else "text_to_video"

        if operation == "image_to_video" and not prompt:
            prompt = "让图片中的主体自然动起来，保持原有画风，电影质感"
            self._prompt.setPlainText(prompt)
        if operation == "image_to_video":
            prompt = self._image_video_prompt(
                prompt, self._creative_prompt.toPlainText())

        params = {
            "duration": int(self._duration_group.selected_key or "8"),
            "ratio": self._ratio_group.selected_key or "16:9",
            "strength": self._strength.value() / 100.0,
        }
        if prov == "veo":
            params["resolution"] = "720p"
        inputs = {"prompt": prompt}
        neg = self._neg_edit.toPlainText().strip()
        if neg:
            inputs["negative_prompt"] = neg

        # 参考图逻辑
        ref1_path = self._ref_path(1)
        ref2_path = self._ref_path(2)
        is_first_last = self._first_last_toggle.isChecked()

        if is_first_last and ref1_path and ref2_path:
            # 首尾帧模式：ref1=首帧, ref2=尾帧
            inputs["image"] = ref1_path
            inputs["last_frame"] = ref2_path
            self._set_status("首尾帧模式已启用：参考图 1 = 首帧，参考图 2 = 尾帧")
        elif ref1_path:
            inputs["image"] = ref1_path
            # ref2+ 作为风格参考
            style_refs = []
            if ref2_path:
                style_refs.append(ref2_path)
            for r in self._ref_slots:
                if r["idx"] >= 3 and r["slot"].path:
                    style_refs.append(r["slot"].path)
            if style_refs:
                inputs["style_images"] = style_refs
        elif ref2_path:
            inputs["image"] = ref2_path

        req = TaskRequest(operation=operation, inputs=inputs, params=params)
        self._set_status("提交视频生成…")
        self._gen_btn.setEnabled(False)
        self._prog.setValue(0)
        self._result_paths = []
        self._done_called = False
        try:
            h = self._mgr.submit(prov, req)
            self._handle = h
            h._on_done.append(lambda hh: QTimer.singleShot(0, lambda: self._on_done(hh)))
            self._poll_timer.start()
        except Exception as e:
            self._set_status(f"提交失败：{self._friendly_error(str(e))}")
            self._gen_btn.setEnabled(True)

    def _poll_progress(self):
        if self._handle is None:
            self._poll_timer.stop()
            return
        prog = int(self._handle.progress * 100)
        self._prog.setValue(max(self._prog.value(), prog))
        if self._handle.is_finished:
            self._poll_timer.stop()
            if not self._done_called:
                self._on_done(self._handle)

    def _on_done(self, h):
        if self._done_called:
            return
        self._done_called = True
        self._gen_btn.setEnabled(True)
        self._poll_timer.stop()
        self._prog.setValue(100 if h.is_success else self._prog.value())
        if h.is_success and h.result:
            data = h.result.data
            if isinstance(data, str):
                self._result_paths = [data]
            elif isinstance(data, (list, tuple)):
                self._result_paths = [str(x) for x in data]
            else:
                self._result_paths = [str(data)]
            self._set_status(f"生成完成 ✓ 已加入素材库")
            for p in self._result_paths:
                self.video_ready.emit(p)
                self.add_to_library_requested.emit(p)
        else:
            err = h.result.error if h.result else "未知错误"
            self._set_status(f"生成失败：{self._friendly_error(err)}")

    # ── 状态 ──
    def _set_status(self, msg: str):
        self._status.setText(msg)
        if self._on_status:
            try:
                self._on_status(msg)
            except Exception:
                pass


# ═══════ 无滚轮滑杆 ═══════

class _NoWheelSlider(QSlider):
    """禁止滚轮事件的 QSlider，避免与 ScrollArea 滚动冲突。"""
    def wheelEvent(self, e):
        e.ignore()


# ═══════ 历史记录视图 ═══════

class _HistoryView(QWidget):
    """AI 历史记录视图。记录所有图片/视频生成项，支持点击插入或打开文件夹。"""

    insert_to_library = pyqtSignal(str)       # 图片 → 素材库
    add_to_timeline = pyqtSignal(str)          # 视频 → 时间线
    open_folder = pyqtSignal(str)              # 打开所在文件夹

    HISTORY_FILE = "ai_history.json"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._history_path = Path.home() / ".cep_models" / self.HISTORY_FILE
        self._items: list[dict] = self._load()

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        # 顶部工具
        top = QHBoxLayout()
        title = QLabel("📜 AI 历史记录")
        title.setStyleSheet("font-weight:bold; font-size:13px; color:#00eaff;")
        top.addWidget(title)
        top.addStretch(1)
        clear_btn = QPushButton("清空")
        clear_btn.setStyleSheet(
            "QPushButton{background:#2a2a2a;color:#ddd;border:1px solid #3a3a3a;"
            "border-radius:3px;padding:2px 10px;font-size:11px;}"
            "QPushButton:hover{background:#a33;color:#fff;}")
        clear_btn.clicked.connect(self._clear)
        top.addWidget(clear_btn)
        root.addLayout(top)

        # 过滤
        flt = QHBoxLayout()
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("🔍 过滤（prompt / 引擎 / 路径）")
        self._filter.setStyleSheet(
            "QLineEdit{background:#1a1a1c;color:#ddd;border:1px solid #2c2c2c;"
            "border-radius:3px;padding:4px;}")
        self._filter.textChanged.connect(self._refresh)
        flt.addWidget(self._filter)
        root.addLayout(flt)

        # 列表
        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget{background:#141416;color:#ddd;border:1px solid #2c2c2c;"
            "border-radius:4px;}"
            "QListWidget::item{padding:4px;border-bottom:1px solid #2c2c2c;}"
            "QListWidget::item:hover{background:#3d8ef8;color:#fff;}"
            "QListWidget::item:selected{background:#3d8ef8;color:#fff;}")
        self._list.itemDoubleClicked.connect(self._on_double_click)
        root.addWidget(self._list, 1)
        self._refresh()

    def add_entry(self, kind: str, path: str, prompt: str = "", provider: str = ""):
        if not path or not os.path.exists(path):
            return
        entry = {
            "kind": kind,                # "image" / "video"
            "path": str(path),
            "prompt": (prompt or "")[:200],
            "provider": provider,
            "ts": datetime.now().isoformat(timespec="seconds"),
        }
        self._items.insert(0, entry)
        # 上限 200
        self._items = self._items[:200]
        self._save()
        self._refresh()

    def _refresh(self):
        flt = self._filter.text().strip().lower()
        self._list.clear()
        for it in self._items:
            if flt and flt not in (it["prompt"] + it["provider"] + it["path"]).lower():
                continue
            row = self._format_row(it)
            qi = QListWidgetItem(row)
            qi.setData(Qt.ItemDataRole.UserRole, it)
            self._list.addItem(qi)

    def _format_row(self, it: dict) -> str:
        icon = "🖼" if it["kind"] == "image" else "🎬"
        ts = it["ts"].replace("T", " ")
        prompt = it["prompt"][:60] + ("…" if len(it["prompt"]) > 60 else "")
        return f"{icon}  {ts}\n    {prompt}\n    {it['provider']}  →  {os.path.basename(it['path'])}"

    def _on_double_click(self, item: QListWidgetItem):
        it = item.data(Qt.ItemDataRole.UserRole)
        if it["kind"] == "image":
            self.insert_to_library.emit(it["path"])
        else:
            self.add_to_timeline.emit(it["path"])

    def _clear(self):
        self._items = []
        self._save()
        self._refresh()

    def _load(self) -> list[dict]:
        try:
            if self._history_path.exists():
                with open(self._history_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return data
        except Exception:  # noqa: BLE001
            pass
        return []

    def _save(self):
        try:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._history_path, "w", encoding="utf-8") as f:
                json.dump(self._items, f, ensure_ascii=False, indent=2)
        except Exception:  # noqa: BLE001
            pass


# ═══════ 主面板：AIAssistantPanel ═══════

class AIAssistantPanel(QWidget):
    """AI 助手面板（剪辑工作台右侧第三栏，与属性面板共用 QStackedWidget）。

    包含两个 Tab：
    - 🎬 视频生成
    - 📜 历史记录
    """

    # 历史记录联动
    add_video_to_timeline = pyqtSignal(str)
    add_to_library = pyqtSignal(str)          # 视频生成完成 → 加入素材库
    video_generated = pyqtSignal(str)         # 分镜等外部调用方接收生成结果

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.setStyleSheet("""
            QTabWidget::pane { border:none; background:#1a1a1a; }
            QTabBar::tab {
                background:#1e1e1e; color:#888; border:none;
                padding:6px 10px; font-size:12px; min-width:60px;
            }
            QTabBar::tab:selected { color:#fff; border-bottom:2px solid #3d8ef8; }
            QTabBar::tab:hover { color:#ccc; }
        """)

        # Tab 1: 视频生成
        self._video_panel = VideoGenPanel(on_status=lambda m: self._echo_status(m))
        self._video_panel.video_ready.connect(self._on_video_ready)
        self._video_panel.add_to_library_requested.connect(self.add_to_library)
        self._video_panel.capture_frame_requested.connect(self._on_capture_frame_requested)
        vid_scroll = QScrollArea()
        vid_scroll.setWidgetResizable(True)
        vid_scroll.setFrameShape(QFrame.Shape.NoFrame)
        vid_scroll.setStyleSheet("QScrollArea{background:#1a1a1a;border:none;}")
        vid_wrap = QWidget()
        vid_wrap.setStyleSheet("background:#1a1a1a;")
        vid_lay = QVBoxLayout(vid_wrap)
        vid_lay.setContentsMargins(0, 0, 0, 0)
        vid_lay.addWidget(self._video_panel)
        vid_scroll.setWidget(vid_wrap)
        self._tabs.addTab(vid_scroll, "🎬 视频生成")

        # Tab 2: 历史记录
        self._history = _HistoryView()
        self._history.add_to_timeline.connect(self.add_video_to_timeline)
        self._tabs.addTab(self._history, "📜 历史记录")

        root.addWidget(self._tabs)

    # 信号中转：截取帧请求 → 外层 EditorTab 处理
    capture_frame_requested = pyqtSignal(int)

    def _on_capture_frame_requested(self, slot: int):
        """VideoGenPanel 的截图请求 → 透传给外层的 EditorTab。"""
        self.capture_frame_requested.emit(slot)

    # ── 公共入口 ──
    def open_video_gen(self, reference_image: Optional[str] = None):
        if reference_image:
            self._video_panel.prefill_reference_image(reference_image)
        self._tabs.setCurrentIndex(0)

    def open_storyboard_video(self, prompt: str, ratio: str, duration: int):
        self._video_panel.prefill_storyboard(prompt, ratio, duration)
        self._tabs.setCurrentIndex(0)

    def open_history(self):
        self._tabs.setCurrentIndex(1)

    def record_video(self, path: str, prompt: str = "", provider: str = ""):
        self._history.add_entry("video", path, prompt, provider)

    # ── 内部 ──
    def _on_video_ready(self, path: str):
        prompt = self._video_panel._prompt.toPlainText().strip()
        prov = self._video_panel._provider.currentData() or ""
        self._history.add_entry("video", path, prompt, prov)
        self.video_generated.emit(path)

    def _echo_status(self, msg: str):
        pass
