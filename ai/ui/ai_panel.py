"""
可复用的 AI 助手面板 —— 注入到剪辑工作台 / 图片工作台右侧。

功能：
- 语音合成 (TTS)：基于 EdgeTTSProvider，生成 MP3。
- 翻译 / 改写 / 摘要：基于 LLM Provider（配置了 LLM_API_KEY 时可用）。
- 「添加到时间线」按钮：host 通过 push_callback 决定如何消费生成的音频
  （剪辑工作台 → 加入素材库 + 时间线；图片工作台不传 callback，按钮隐藏）。

线程安全：Provider 在后台线程执行，完成回调通过 pyqtSignal 回到 GUI 线程更新 UI。
"""
from __future__ import annotations

import os

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QTextEdit, QLineEdit,
    QComboBox, QDoubleSpinBox, QPushButton, QLabel,
)
from PyQt6.QtCore import pyqtSignal, QThread, Qt, QUrl
from PyQt6.QtGui import QDesktopServices

from ai import TaskRequest
from ai.service import get_ai_manager


DEFAULT_VOICES = [
    ("zh-CN-XiaoxiaoNeural", "晓晓 (女·中文)"),
    ("zh-CN-YunxiNeural", "云希 (男·中文)"),
    ("zh-CN-YunyangNeural", "云扬 (男·新闻)"),
    ("zh-CN-XiaoyiNeural", "小艺 (女·中文)"),
    ("en-US-AriaNeural", "Aria (女·英文)"),
    ("en-US-GuyNeural", "Guy (男·英文)"),
    ("ja-JP-NanamiNeural", "Nanami (女·日文)"),
    ("ko-KR-SunHiNeural", "SunHi (女·韩文)"),
]

SYSTEM_PROMPTS = {
    "翻译为英文": "You are a translator. Translate the user text into English. Output only the translation.",
    "翻译为中文": "你是一位翻译。将用户输入翻译为自然流畅的中文，只输出译文。",
    "改写润色": "你是一位中文文案润色师。在不改变原意的前提下，让文本更流畅、专业。只输出改写后的文本。",
    "生成摘要": "请对下面的文本生成一段简洁的中文摘要（3 句以内）。只输出摘要。",
}


class VoiceFetcher(QThread):
    """后台拉取可用语音列表（需联网到微软）。"""
    voices_ready = pyqtSignal(list)
    failed = pyqtSignal(str)

    def run(self):
        try:
            import asyncio
            import edge_tts
            voices = asyncio.run(edge_tts.list_voices())
            out = [(v["ShortName"],
                    v.get("FriendlyName", v["ShortName"]),
                    v.get("Locale", "")) for v in voices]
            self.voices_ready.emit(out)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class AIPanel(QWidget):
    """AI 助手面板。"""

    audio_generated = pyqtSignal(str)        # 生成成功 → 发出 mp3 路径
    _task_done = pyqtSignal(object)          # 跨线程安全回传 TaskHandle

    def __init__(self, parent=None, push_label: str = "", push_callback=None):
        super().__init__(parent)
        self._push_label = push_label
        self._push_callback = push_callback
        self._handle = None
        self._last_path: str | None = None
        self._mgr = None
        self._llm_name: str | None = None

        self._build_ui()
        self._task_done.connect(self._on_handle_done)

        # 连接共享 AI 服务（懒加载 TaskManager）
        try:
            self._mgr = get_ai_manager()
            chat = self._mgr.registry.by_capability("chat")
            self._llm_name = chat[0].name if chat else None
        except Exception:  # noqa: BLE001
            self._mgr = None
            self._llm_name = None
        self._refresh_state()

    # ── UI 构建 ──
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        title = QLabel("🤖 AI 助手")
        title.setStyleSheet("font-weight:bold; font-size:14px; color:#00eaff;")
        root.addWidget(title)

        # ── TTS ──
        tts = QGroupBox("语音合成 (TTS)")
        tv = QVBoxLayout(tts)
        tv.setSpacing(6)

        self._tts_text = QTextEdit()
        self._tts_text.setPlaceholderText("输入要转为语音的文本…")
        self._tts_text.setMaximumHeight(64)
        self._tts_text.setAcceptRichText(False)
        tv.addWidget(self._tts_text)

        hv = QHBoxLayout()
        self._voice_combo = QComboBox()
        self._voice_combo.setMinimumWidth(120)
        for vid, label in DEFAULT_VOICES:
            self._voice_combo.addItem(label, vid)
        hv.addWidget(self._voice_combo, 2)
        self._voice_refresh = QPushButton("↻")
        self._voice_refresh.setToolTip("刷新语音列表（需联网）")
        self._voice_refresh.setMaximumWidth(30)
        self._voice_refresh.clicked.connect(self._refresh_voices)
        hv.addWidget(self._voice_refresh)
        tv.addLayout(hv)

        hsp = QHBoxLayout()
        hsp.addWidget(QLabel("语速"))
        self._speed = QDoubleSpinBox()
        self._speed.setRange(0.5, 2.0)
        self._speed.setSingleStep(0.05)
        self._speed.setValue(1.0)
        self._speed.setSuffix("x")
        hsp.addWidget(self._speed)
        tv.addLayout(hsp)

        self._gen_btn = QPushButton("🎙 生成语音")
        self._gen_btn.clicked.connect(self._on_generate)
        tv.addWidget(self._gen_btn)

        self._tts_status = QLabel("")
        self._tts_status.setStyleSheet("color:#999; font-size:11px;")
        self._tts_status.setWordWrap(True)
        tv.addWidget(self._tts_status)

        hopen = QHBoxLayout()
        self._open_btn = QPushButton("▶ 播放 / 打开")
        self._open_btn.setEnabled(False)
        self._open_btn.clicked.connect(self._on_open)
        hopen.addWidget(self._open_btn)
        self._push_btn = QPushButton(self._push_label or "添加到时间线")
        if self._push_callback is None:
            self._push_btn.setVisible(False)
        else:
            self._push_btn.setEnabled(False)
            self._push_btn.clicked.connect(self._on_push)
        hopen.addWidget(self._push_btn)
        tv.addLayout(hopen)
        root.addWidget(tts)

        # ── 翻译 / 改写 ──
        tr = QGroupBox("翻译 / 改写 (LLM)")
        rv = QVBoxLayout(tr)
        rv.setSpacing(6)
        self._tr_mode = QComboBox()
        self._tr_mode.addItems(list(SYSTEM_PROMPTS.keys()))
        rv.addWidget(self._tr_mode)
        self._tr_in = QTextEdit()
        self._tr_in.setPlaceholderText("输入原文…")
        self._tr_in.setMaximumHeight(56)
        self._tr_in.setAcceptRichText(False)
        rv.addWidget(self._tr_in)
        self._tr_btn = QPushButton("✨ 开始")
        self._tr_btn.clicked.connect(self._on_translate)
        rv.addWidget(self._tr_btn)
        self._tr_out = QTextEdit()
        self._tr_out.setReadOnly(True)
        self._tr_out.setPlaceholderText("结果将显示在这里…")
        self._tr_out.setMaximumHeight(80)
        rv.addWidget(self._tr_out)
        root.addWidget(tr)

        # ── 资源中心入口 ──
        self._res_btn = QPushButton("🗂 打开 AI 制片画布")
        self._res_btn.clicked.connect(self._open_resource_center)
        root.addWidget(self._res_btn)

        root.addStretch(1)

    # ── 状态 ──
    def _refresh_state(self):
        if not self._llm_name:
            self._tr_btn.setEnabled(False)
            self._tr_btn.setToolTip(
                "未配置 LLM API Key。请在 .env 设置 LLM_API_KEY / OPENAI_API_KEY 后重启。"
            )

    # ── TTS ──
    def _on_generate(self):
        if not self._mgr:
            self._tts_status.setText("AI 服务不可用")
            return
        text = self._tts_text.toPlainText().strip()
        if not text:
            self._tts_status.setText("请输入要合成的文本")
            return
        voice = self._voice_combo.currentData() or self._voice_combo.currentText()
        speed = self._speed.value()
        req = TaskRequest(operation="text_to_speech",
                          inputs={"text": text},
                          params={"voice": voice, "speed": speed})
        self._tts_status.setText("生成中…")
        self._gen_btn.setEnabled(False)
        self._open_btn.setEnabled(False)
        self._push_btn.setEnabled(False)
        h = self._mgr.submit("edge_tts", req)
        self._handle = h
        h._on_done.append(lambda hh: self._task_done.emit(hh))

    def _refresh_voices(self):
        self._voice_refresh.setEnabled(False)
        self._tts_status.setText("刷新语音列表…")
        self._fetcher = VoiceFetcher()
        self._fetcher.voices_ready.connect(self._on_voices)
        self._fetcher.failed.connect(lambda e: self._tts_status.setText(f"刷新失败：{e}"))
        self._fetcher.finished.connect(lambda: self._voice_refresh.setEnabled(True))
        self._fetcher.start()

    def _on_voices(self, voices):
        self._voice_combo.clear()
        for vid, friendly, locale in voices:
            label = f"{friendly} ({locale})" if locale else friendly
            self._voice_combo.addItem(label, vid)
        self._tts_status.setText(f"已加载 {len(voices)} 个语音")

    # ── 翻译 / 改写 ──
    def _on_translate(self):
        if not self._mgr or not self._llm_name:
            self._tr_out.setPlainText(
                "未配置 LLM API Key。请在 .env 设置 LLM_API_KEY / OPENAI_API_KEY 后重启。"
            )
            return
        text = self._tr_in.toPlainText().strip()
        if not text:
            self._tr_out.setPlainText("请输入原文。")
            return
        mode = self._tr_mode.currentText()
        messages = [
            {"role": "system", "content": SYSTEM_PROMPTS.get(mode, "You are a helpful assistant.")},
            {"role": "user", "content": text},
        ]
        params: dict = {}
        try:
            from config import LLM_MODEL_NAME
            if LLM_MODEL_NAME:
                params["model"] = LLM_MODEL_NAME
        except Exception:  # noqa: BLE001
            pass
        req = TaskRequest(operation="chat", inputs={"messages": messages}, params=params)
        self._tr_btn.setEnabled(False)
        self._tr_out.setPlainText("处理中…")
        h = self._mgr.submit(self._llm_name, req)
        h._on_done.append(lambda hh: self._task_done.emit(hh))

    # ── 完成回调（GUI 线程）──
    def _on_handle_done(self, h):
        if h.operation == "text_to_speech":
            self._gen_btn.setEnabled(True)
            if h.is_success and h.result:
                self._last_path = str(h.result.data)
                self._tts_status.setText(f"完成：{os.path.basename(self._last_path)}")
                self._open_btn.setEnabled(True)
                if self._push_callback is not None:
                    self._push_btn.setEnabled(True)
                self.audio_generated.emit(self._last_path)
            else:
                err = h.result.error if h.result else "未知错误"
                self._tts_status.setText(f"失败：{err}")
        elif h.operation == "chat":
            self._tr_btn.setEnabled(True)
            if h.is_success and h.result:
                self._tr_out.setPlainText(str(h.result.data))
            else:
                err = h.result.error if h.result else "未知错误"
                self._tr_out.setPlainText(f"失败：{err}")
        self._handle = None

    # ── 结果操作 ──
    def _on_open(self):
        if self._last_path and os.path.exists(self._last_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._last_path))

    def _on_push(self):
        if self._push_callback and self._last_path:
            self._push_callback(self._last_path)

    def _open_resource_center(self):
        try:
            win = self.window()
            if hasattr(win, "_on_director_resource_requested"):
                win._on_director_resource_requested("")
            elif hasattr(win, "stacked"):
                win.stacked.setCurrentIndex(7)
        except Exception:  # noqa: BLE001
            pass
