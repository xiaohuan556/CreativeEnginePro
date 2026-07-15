"""
小欢语音 - 语音工作室 (Tab 1)
左:文案输入  右:AI润色结果  下:朗读控制 + 历史
"""
import os
import time
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QListWidget, QListWidgetItem, QProgressBar,
    QSplitter, QMessageBox, QInputDialog, QMenu, QFileDialog, QSlider,
)
from PyQt6.QtCore import Qt, pyqtSignal, QUrl
from PyQt6.QtGui import QShortcut, QKeySequence
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput


class VoiceWorkbench(QWidget):
    """语音工作室 - 文字转语音"""

    voice_ready = pyqtSignal(str)          # TTS 完成 → 文件路径
    voice_pushed = pyqtSignal(str)         # 推送语音到视频区
    script_to_polish = pyqtSignal(str)     # 发送文字到 AI 脚本润色 Tab
    status_msg = pyqtSignal(str, str)      # (消息, level) → 主窗口状态栏

    def __init__(self, parent=None):
        super().__init__(parent)
        from config import TTS_ENGINE
        self._tts_engine = TTS_ENGINE
        self._voice = "zh-CN-XiaoxiaoNeural" if self._tts_engine not in ("elevenlabs", "fish_audio", "auto_lang", "siliconflow", "deepgram") else ""

        # 千语种引擎：不再 import 时阻塞预热，改为延迟到首次使用
        self._rate = "+0%"
        self._history = []
        self._worker = None
        self._polish_worker = None
        self._trans_worker = None
        self._tts_source = "right"  # 'left' | 'right'，默认用润色结果
        self._left_original = ""   # 左栏翻译前原文
        self._right_before_trans = ""  # 右栏翻译前内容（可能是润色后的）
        self._left_translated = ""  # 左栏译文
        self._right_translated = "" # 右栏译文
        self._build()
        self._setup_player()

    def _setup_player(self):
        """内置音频播放器"""
        self._player = QMediaPlayer()
        self._audio_out = QAudioOutput()
        self._player.setAudioOutput(self._audio_out)
        self._audio_out.setVolume(0.8)
        self._player.playbackStateChanged.connect(self._on_playback_state)
        self._player.errorOccurred.connect(self._on_player_error)

    def _build(self):
        self.setStyleSheet("background:#1a1a1a;")
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        # ═══════════════════════════════════════
        # 上部：文案输入 ↔ AI润色结果（左右分栏）
        # ═══════════════════════════════════════
        top_split = QSplitter(Qt.Orientation.Horizontal)
        top_split.setHandleWidth(2)
        top_split.setStyleSheet("QSplitter::handle{background:#2a2a2a;}")

        # ── 左侧：原始文案 ──
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(6)

        hdr_l = QHBoxLayout()
        hdr_l.addWidget(QLabel("📝 输入文案"))
        hdr_l.addStretch()
        btn_paste = QPushButton("📋 粘贴")
        btn_paste.setStyleSheet(_GHOST)
        btn_paste.clicked.connect(self._paste)
        hdr_l.addWidget(btn_paste)
        btn_clear = QPushButton("清空")
        btn_clear.setStyleSheet(_GHOST)
        btn_clear.clicked.connect(self._clear_input)
        hdr_l.addWidget(btn_clear)
        self.btn_show_orig_left = QPushButton("显示原文")
        self.btn_show_orig_left.setStyleSheet(_GHOST)
        self.btn_show_orig_left.setFixedWidth(70)
        self.btn_show_orig_left.clicked.connect(lambda: self._toggle_original("left"))
        self.btn_show_orig_left.hide()
        hdr_l.addWidget(self.btn_show_orig_left)
        left_lay.addLayout(hdr_l)

        self.editor = QTextEdit()
        self.editor.setAcceptRichText(False)
        self.editor.setPlaceholderText("在此输入或粘贴要朗读的文字…")
        self.editor.setStyleSheet(_TEXT_EDIT)
        left_lay.addWidget(self.editor, 1)

        top_split.addWidget(left)

        # ── 右侧：AI润色结果 ──
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(6)

        hdr_r = QHBoxLayout()
        hdr_r.addWidget(QLabel("✨ AI润色结果（可编辑）"))
        hdr_r.addStretch()
        # 润色模式选择
        from PyQt6.QtWidgets import QComboBox
        self.combo_polish_mode = QComboBox()
        self.combo_polish_mode.addItems(["精简", "同量", "丰富"])
        self.combo_polish_mode.setCurrentIndex(2)  # 默认丰富
        self.combo_polish_mode.setStyleSheet(
            "QComboBox{background:#1e1e1e;border:1px solid #2a2a2a;border-radius:4px;"
            "color:#999;font-size:11px;padding:2px 6px;}"
            "QComboBox::drop-down{border:none;}"
            "QComboBox QAbstractItemView{background:#1e1e1e;color:#ccc;}")
        self.combo_polish_mode.setFixedWidth(56)
        hdr_r.addWidget(self.combo_polish_mode)
        self.btn_polish = QPushButton("✨ AI润色")
        self.btn_polish.setStyleSheet(_ACCENT)
        self.btn_polish.clicked.connect(self._ai_polish)
        hdr_r.addWidget(self.btn_polish)
        self.btn_show_orig_right = QPushButton("显示原文")
        self.btn_show_orig_right.setStyleSheet(_GHOST)
        self.btn_show_orig_right.setFixedWidth(70)
        self.btn_show_orig_right.clicked.connect(lambda: self._toggle_original("right"))
        self.btn_show_orig_right.hide()
        hdr_r.addWidget(self.btn_show_orig_right)
        right_lay.addLayout(hdr_r)

        self.editor_polished = QTextEdit()
        self.editor_polished.setAcceptRichText(False)
        self.editor_polished.setPlaceholderText("点击「AI润色」生成优化后的朗读文案…")
        self.editor_polished.setStyleSheet(_TEXT_EDIT)
        right_lay.addWidget(self.editor_polished, 1)

        top_split.addWidget(right)
        top_split.setSizes([500, 500])
        root.addWidget(top_split, 1)

        # ═══════════════════════════════════════
        # 中部：朗读控制（声音 + 语速 + 生成）
        # ═══════════════════════════════════════
        ctrl = QHBoxLayout()
        ctrl.setSpacing(12)

        # 声音选择
        from ui.voice_picker import VoiceSelectButton
        self.btn_voice = VoiceSelectButton()
        self.btn_voice.set_engine(self._tts_engine)
        if self._tts_engine in ("elevenlabs", "fish_audio", "siliconflow", "deepgram"):
            self.btn_voice.setText("🎵  点击选择声音")
        elif self._tts_engine == "auto_lang":
            self.btn_voice.setText("🌐  自动识别")
            self.btn_voice.setEnabled(False)
        else:
            self.btn_voice.setEnabled(True)
        self.btn_voice.setFixedWidth(150)
        self.btn_voice.voice_changed.connect(lambda s, z: setattr(self, '_voice', s))
        ctrl.addWidget(self.btn_voice)

        # 生成来源切换
        ctrl.addWidget(QLabel("来源"))
        self._src_btns = []
        for side, label in [("left", "左栏"), ("right", "右栏")]:
            btn = QPushButton(label)
            btn.setFixedSize(42, 28)
            btn.setCheckable(True)
            btn.setStyleSheet(_SPEED_BTN)
            btn.clicked.connect(lambda _, s=side: self._set_tts_source(s))
            self._src_btns.append(btn)
            ctrl.addWidget(btn)
        self._set_tts_source("right")  # 默认选中右栏

        # 语速按钮组
        ctrl.addWidget(QLabel("语速"))
        self._speed_btns = []
        for v, t in [("0.8x", "-20%"), ("1.0x", "+0%"), ("1.2x", "+20%"), ("1.5x", "+50%")]:
            btn = QPushButton(v)
            btn.setFixedSize(42, 28)
            btn.setCheckable(True)
            btn.setStyleSheet(_SPEED_BTN)
            btn.clicked.connect(lambda _, r=t: self._set_rate(r))
            self._speed_btns.append(btn)
            ctrl.addWidget(btn)
            if t == "+0%":
                btn.setChecked(True)

        # 全文译中（同时翻译左右两栏）
        self.btn_trans_all = QPushButton("🌐 译中")
        self.btn_trans_all.setStyleSheet(_GHOST)
        self.btn_trans_all.setToolTip("将左侧输入和右侧润色结果一并翻译为中文")
        self.btn_trans_all.clicked.connect(self._translate_all_to_chinese)
        ctrl.addWidget(self.btn_trans_all)

        ctrl.addStretch()

        # ⚙ 设置
        btn_settings = QPushButton("⚙ 设置")
        btn_settings.setStyleSheet(
            "QPushButton{background:#2a2a3a;color:#aaa;border:1px solid #3a3a4a;"
            "border-radius:6px;font-size:12px;padding:5px 14px;}"
            "QPushButton:hover{color:#fff;border-color:#3d8ef8;background:#2d2d3d;}"
        )
        btn_settings.clicked.connect(self._open_settings)
        ctrl.addWidget(btn_settings)

        # TTS 音量
        ctrl.addWidget(QLabel("音量"))
        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 200)
        self.vol_slider.setValue(75)
        self.vol_slider.setFixedWidth(60)
        ctrl.addWidget(self.vol_slider)

        # 试听按钮（播放当前选中的历史或最新生成的）
        self.btn_preview = QPushButton("▶ 试听")
        self.btn_preview.setStyleSheet(_SECONDARY)
        self.btn_preview.clicked.connect(self._preview_selected)
        ctrl.addWidget(self.btn_preview)

        self.btn_generate = QPushButton("🔊 生成语音")
        self.btn_generate.setStyleSheet(_PRIMARY)
        self.btn_generate.clicked.connect(self._generate)
        ctrl.addWidget(self.btn_generate)

        root.addLayout(ctrl)

        # 进度条
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setFixedHeight(2)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet(_PROGRESS)
        root.addWidget(self.progress)

        # ═══════════════════════════════════════
        # 下部：生成历史（紧凑）
        # ═══════════════════════════════════════
        hist_hdr = QHBoxLayout()
        lbl_hist = QLabel("生成历史")
        lbl_hist.setStyleSheet("color:#666;font-size:11px;font-weight:bold;")
        hist_hdr.addWidget(lbl_hist)
        hist_hdr.addStretch()

        btn_push = QPushButton("→ 推送选中到视频区")
        btn_push.setFixedHeight(22)
        btn_push.setStyleSheet(_TINY_BTN)
        btn_push.clicked.connect(self._push)
        hist_hdr.addWidget(btn_push)
        root.addLayout(hist_hdr)

        self.history_list = QListWidget()
        self.history_list.setMaximumHeight(160)
        self.history_list.setStyleSheet(_LIST)
        self.history_list.itemClicked.connect(self._on_history_click)
        self.history_list.itemDoubleClicked.connect(self._on_history_dbl_click)
        self.history_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history_list.customContextMenuRequested.connect(self._history_menu)
        root.addWidget(self.history_list)

        # Ctrl+Enter 快捷生成
        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(self._generate)

    # ── 核心方法 ──

    def get_voice_path(self) -> str:
        return self._history[-1]["path"] if self._history else ""

    def set_voice(self, short_name: str):
        self._voice = short_name

    def update_engine(self, engine: str):
        """设置面板切换引擎后调用"""
        self._tts_engine = engine
        self.btn_voice.set_engine(engine)
        self.btn_voice.setEnabled(True)  # 先恢复，千语种再禁用
        # 不支持语速调节的引擎禁用语速按钮
        speed_supported = engine not in ("elevenlabs", "fish_audio", "siliconflow", "deepgram")
        for btn in self._speed_btns:
            btn.setEnabled(speed_supported)
        if engine in ("elevenlabs", "fish_audio", "siliconflow", "deepgram"):
            self._voice = ""
            self.btn_voice.setText("🎵  点击选择声音")
        elif engine == "auto_lang":
            self._voice = "female"
            self.btn_voice.setText("🌐  自动识别")
            self.btn_voice.setEnabled(False)
        else:
            self._voice = "zh-CN-XiaoxiaoNeural"
            self.btn_voice.setText("🎵  晓晓")

    def _open_settings(self):
        """打开设置面板（弹窗）"""
        from ui.settings_panel import SettingsPanel
        if not hasattr(self, '_settings_panel'):
            self._settings_panel = SettingsPanel()
            self._settings_panel.setWindowFlags(
                self._settings_panel.windowFlags() | Qt.WindowType.Dialog
            )
            self._settings_panel.setWindowTitle("语音设置")
            self._settings_panel.settings_changed.connect(self._on_settings_changed)
            # 修复：初始化时不应该 collapse，弹窗必须可见
            self._settings_panel._expand()
        if self._settings_panel.isVisible():
            self._settings_panel.hide()
        else:
            self._settings_panel.show()

    def _on_settings_changed(self):
        """设置变更后刷新语音引擎"""
        import os
        engine = os.getenv("TTS_ENGINE", "edge")
        self.update_engine(engine)
        self.status_msg.emit(f"TTS引擎已切换为: {engine}", "info")

    def load_text(self, text: str):
        """外部载入文字（来自 AI 脚本 Tab 的推送）"""
        self.editor.setPlainText(text)
        self.status_msg.emit("已接收润色文案", "info")

    def _clear_input(self):
        self.editor.clear()
        self.editor_polished.clear()

    def _generate(self):
        """生成 → 取消 切换"""
        w = getattr(self, '_worker', None)
        if w is not None and w.isRunning():
            self._stop_generation()
            return
        self._start_generation()

    def _stop_generation(self):
        """取消当前生成"""
        w = getattr(self, '_worker', None)
        if w is not None and w.isRunning():
            w.requestInterruption()
            w.wait(2000)  # 给 2 秒时间消化中断
            if w.isRunning():
                w.terminate()  # 强制终止
            self.status_msg.emit("生成已取消", "info")
        self._reset_generate_ui()

    def _reset_generate_ui(self):
        self.btn_generate.setEnabled(True)
        self.btn_generate.setText("🔊 生成语音")
        self.btn_generate.setStyleSheet(_PRIMARY)
        self.progress.setValue(0)

    def _start_generation(self):
        # 根据用户选择的来源取文本
        if self._tts_source == "left":
            text = self.editor.toPlainText().strip()
            fallback = self.editor_polished.toPlainText().strip()
            if not text and fallback:
                r = QMessageBox.question(self, "确认", "左栏为空，是否用右栏内容生成？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if r == QMessageBox.StandardButton.Yes:
                    text = fallback
                    self._set_tts_source("right")
                else:
                    return
        else:
            text = self.editor_polished.toPlainText().strip()
            fallback = self.editor.toPlainText().strip()
            if not text and fallback:
                r = QMessageBox.question(self, "确认", "右栏为空，是否用左栏内容生成？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if r == QMessageBox.StandardButton.Yes:
                    text = fallback
                    self._set_tts_source("left")
                else:
                    return
        if not text:
            self.status_msg.emit("请先输入文案", "warn")
            return

        from ui.workers.tts_worker import TTSGenerationWorker

        # 清理旧 worker
        if self._worker is not None:
            old = self._worker
            try: old.finished.disconnect(); old.error.disconnect(); old.progress.disconnect()
            except Exception: pass
            if old.isRunning():
                old.finished.connect(old.deleteLater)
            else:
                old.deleteLater()

        self.btn_generate.setEnabled(False)
        self.btn_generate.setText("生成中...")
        self.progress.setValue(0)

        self._worker = TTSGenerationWorker(text=text, voice=self._voice, rate=self._rate, engine_type=self._tts_engine,
            volume=self.vol_slider.value() / 100.0)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _cleanup_worker(self, w):
        """安全清理已完成的 worker（非阻塞）"""
        if w:
            w.deleteLater()
            if self._worker is w:
                self._worker = None

    def _on_done(self, path: str):
        w = self._worker  # 先保存引用
        if w and w.isInterruptionRequested():
            self._cleanup_worker(w)
            return  # 取消完成，跳过命名
        import shutil
        self._reset_generate_ui()
        self.progress.setValue(100)

        # 命名
        p = Path(path)
        name, ok = QInputDialog.getText(self, "命名语音", "给这条语音起个名字：", text=p.stem)
        if ok and name.strip():
            new_path = p.with_stem(name.strip())
            shutil.move(str(p), str(new_path))
            path = str(new_path)
            p = Path(path)
            pname = p.name
        else:
            pname = p.name

        text_preview = self.editor.toPlainText().strip()[:20]
        self._history.append({"path": path, "name": pname, "text": text_preview, "time": time.strftime("%H:%M")})
        # 限制历史记录数量
        if len(self._history) > 100:
            self._history = self._history[-100:]

        item = QListWidgetItem(f"▶ {pname}  |  {self._history[-1]['time']}")
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setToolTip(path)
        self.history_list.insertItem(0, item)
        self.history_list.setCurrentRow(0)

        self.voice_ready.emit(path)
        self.status_msg.emit(f"语音完成: {pname}", "success")
        self._cleanup_worker(w)

    def _on_error(self, err: str):
        w = self._worker
        self._reset_generate_ui()
        self.status_msg.emit(f"生成失败: {err}", "error")
        self._cleanup_worker(w)

    def _ai_polish(self):
        text = self.editor.toPlainText().strip()
        if not text:
            self.status_msg.emit("请先输入文案", "warn")
            return

        # 清理旧 polish worker
        if self._polish_worker is not None:
            old = self._polish_worker
            try: old.finished.disconnect(); old.error.disconnect()
            except Exception: pass
            if old.isRunning():
                old.finished.connect(old.deleteLater)
            else:
                old.deleteLater()

        self.btn_polish.setEnabled(False)
        self.btn_polish.setText("润色中...")
        self.status_msg.emit("AI润色中...", "info")

        # 后台线程执行润色
        from PyQt6.QtCore import QThread

        class _PolishWorker(QThread):
            finished = pyqtSignal(str)
            error = pyqtSignal(str)

            def __init__(self, text, mode):
                super().__init__()
                self._text = text
                self._mode = mode

            def run(self):
                try:
                    result = _polish_text(self._text, self._mode)
                    self.finished.emit(result)
                except Exception as e:
                    self.error.emit(str(e))

        mode = self.combo_polish_mode.currentText()
        self._polish_worker = _PolishWorker(text, mode)
        self._polish_worker.finished.connect(self._on_polish_done)
        self._polish_worker.error.connect(self._on_polish_error)
        self._polish_worker.start()

    def _on_polish_done(self, text: str):
        w = self._polish_worker
        self.editor_polished.setPlainText(text)
        # 润色后重置右栏翻译状态，确保「显示原文」对应最新内容
        self._right_before_trans = text
        self._right_translated = ""
        self.btn_show_orig_right.setText("显示原文")
        self.btn_show_orig_right.hide()
        self.btn_polish.setEnabled(True)
        self.btn_polish.setText("✨ AI润色")
        self.status_msg.emit("AI润色完成", "success")
        if w:
            w.deleteLater()
            if self._polish_worker is w: self._polish_worker = None

    def _on_polish_error(self, err: str):
        w = self._polish_worker
        self.btn_polish.setEnabled(True)
        self.btn_polish.setText("✨ AI润色")
        self.status_msg.emit(f"润色失败: {err}", "error")
        if w:
            w.deleteLater()
            if self._polish_worker is w: self._polish_worker = None

    def _push(self):
        item = self.history_list.currentItem()
        if not item:
            self.status_msg.emit("请先选中一条语音", "warn")
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if path and Path(path).exists():
            self.voice_pushed.emit(path)
            self.status_msg.emit(f"已推送: {Path(path).name}", "info")

    def _history_menu(self, pos):
        item = self.history_list.itemAt(pos)
        if not item: return
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path or not Path(path).exists(): return
        menu = QMenu(self)
        act_rename = menu.addAction("✏ 重命名")
        menu.addSeparator()
        act_export = menu.addAction("📥 导出为 MP3")
        act_open = menu.addAction("📂 打开文件夹")
        action = menu.exec(self.history_list.mapToGlobal(pos))
        if action == act_rename:
            name, ok = QInputDialog.getText(self, "重命名", "新名称：", text=Path(path).stem)
            if ok and name.strip():
                new = Path(path).with_stem(name.strip())
                import shutil; shutil.move(path, str(new))
                item.setData(Qt.ItemDataRole.UserRole, str(new))
                item.setText(f"▶ {name.strip()}  |  {time.strftime('%H:%M')}")
                item.setToolTip(str(new))
                # 同步更新 _history 中的路径，避免变速等操作找不到文件
                for h in self._history:
                    if h.get("path") == path:
                        h["path"] = str(new)
                        h["orig_path"] = str(new)
                        break
                self.status_msg.emit(f"已重命名: {new.name}", "success")
        elif action == act_export:
            try:
                dest, _ = QFileDialog.getSaveFileName(self, "导出 MP3", Path(path).stem + ".mp3", "MP3 (*.mp3)")
                if dest:
                    import shutil; shutil.copy(path, dest)
                    self.status_msg.emit(f"已导出: {dest}", "success")
            except Exception as e:
                self.status_msg.emit(f"导出失败: {e}", "error")
        elif action == act_open:
            os.startfile(str(Path(path).parent))

    def _on_history_dbl_click(self, item: QListWidgetItem):
        """双击历史项 → 试听该语音"""
        path = item.data(Qt.ItemDataRole.UserRole)
        if path and Path(path).exists():
            self._play_file(path)

    def _on_history_click(self, item: QListWidgetItem):
        """点击历史项时自动播放"""
        path = item.data(Qt.ItemDataRole.UserRole)
        if path and Path(path).exists():
            self._play_file(path)

    def _preview_selected(self):
        """试听：播放中则暂停，否则播放"""
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            self.btn_preview.setText("▶ 试听")
            return
        item = self.history_list.currentItem()
        if item:
            path = item.data(Qt.ItemDataRole.UserRole)
            if path and Path(path).exists():
                self._play_file(path)
                return
        self.status_msg.emit("没有可试听的语音", "warn")

    def _play_file(self, path: str):
        """用内置播放器播放，失败则用系统播放器"""
        if not Path(path).exists():
            self.status_msg.emit("文件不存在", "warn")
            return
        try:
            self._player.setSource(QUrl.fromLocalFile(path))
            self._audio_out.setVolume(min(self.vol_slider.value(), 100) / 100.0)
            self._player.play()
            self.btn_preview.setText("⏸ 试听")
            self.status_msg.emit(f"正在试听: {Path(path).name}", "info")
        except Exception:
            self._fallback_play(path)

    def _fallback_play(self, path: str):
        """系统默认播放器兜底"""
        try:
            os.startfile(path)
            self.status_msg.emit(f"正在试听: {Path(path).name}", "info")
        except OSError:
            # Windows 没有关联应用，用 PowerShell 兜底
            import subprocess
            try:
                subprocess.Popen(["powershell", "-Command",
                    f"Add-Type -AssemblyName PresentationCore; "
                    f"$player = New-Object System.Media.MediaPlayer; "
                    f"$player.Open('{path}'); $player.Play(); Start-Sleep -Seconds 5"],
                    shell=True)
            except Exception:
                self.status_msg.emit(f"播放失败，请手动打开: {path}", "warn")

    def _on_player_error(self, error, error_string):
        """QMediaPlayer 播放失败 → 试试系统播放器"""
        # 如果 source 有值但播放失败，用 fallback
        src = self._player.source()
        if src and src.isLocalFile():
            self._fallback_play(src.toLocalFile())

    def _on_playback_state(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.btn_preview.setText("⏸ 试听")
        else:
            self.btn_preview.setText("▶ 试听")

    def _paste(self):
        from PyQt6.QtWidgets import QApplication
        text = QApplication.clipboard().text()
        if text:
            self.editor.setPlainText(text)

    def _toggle_original(self, side: str):
        """切换显示原文/译文"""
        editor = self.editor if side == "left" else self.editor_polished
        btn_orig = self.btn_show_orig_left if side == "left" else self.btn_show_orig_right
        original = self._left_original if side == "left" else self._right_before_trans
        translated = self._left_translated if side == "left" else self._right_translated

        if btn_orig.text() == "显示原文":
            if original:
                editor.setPlainText(original)
                btn_orig.setText("显示译文")
                self.status_msg.emit("已切换到原文", "info")
        else:
            if translated:
                editor.setPlainText(translated)
                btn_orig.setText("显示原文")
                self.status_msg.emit("已切换到译文", "info")

    def _translate_all_to_chinese(self):
        """一键翻译左右两栏为中文"""
        left_text = self.editor.toPlainText().strip()
        right_text = self.editor_polished.toPlainText().strip()
        if not left_text and not right_text:
            self.status_msg.emit("没有可翻译的内容", "warn")
            return

        # 检查是否有非中文内容
        has_non_zh = False
        for t in (left_text, right_text):
            if not t: continue
            ch = sum(1 for c in t if '\u4e00' <= c <= '\u9fff')
            if ch < len(t) * 0.5:
                has_non_zh = True
                break
        if not has_non_zh:
            self.status_msg.emit("当前内容已经是中文了", "info")
            return

        # 保存原文
        if left_text:
            self._left_original = left_text
        if right_text:
            self._right_before_trans = right_text

        # 清理旧翻译 worker
        if self._trans_worker is not None:
            old = self._trans_worker
            try: old.finished.disconnect(); old.error.disconnect()
            except Exception: pass
            if old.isRunning():
                old.finished.connect(old.deleteLater)
            else:
                old.deleteLater()

        self.btn_trans_all.setEnabled(False)
        self.btn_trans_all.setText("翻译中...")
        self.status_msg.emit("全文翻译中...", "info")

        from PyQt6.QtCore import QThread
        class _TransAllWorker(QThread):
            finished = pyqtSignal(str, str)  # (left_result, right_result)
            error = pyqtSignal(str)
            def __init__(self, lt, rt):
                super().__init__(); self._lt = lt; self._rt = rt
            def run(self):
                try:
                    from core.builtin_translator import translate_text
                    left_r = right_r = ""
                    if self._lt:
                        left_r = translate_text(self._lt, "zh")
                    if self._rt:
                        right_r = translate_text(self._rt, "zh")
                    self.finished.emit(left_r, right_r)
                except Exception as e:
                    self.error.emit(str(e))

        self._trans_worker = _TransAllWorker(left_text, right_text)
        self._trans_worker.finished.connect(lambda l, r: self._on_trans_all_done(l, r))
        self._trans_worker.error.connect(lambda e: self._on_trans_all_err(e))
        self._trans_worker.start()

    def _on_trans_all_done(self, left_r, right_r):
        if left_r:
            self._left_translated = left_r
            self.editor.setPlainText(left_r)
            self.btn_show_orig_left.show()
        if right_r:
            self._right_translated = right_r
            self.editor_polished.setPlainText(right_r)
            self.btn_show_orig_right.show()
        self.btn_trans_all.setEnabled(True)
        self.btn_trans_all.setText("🌐 译中")
        self.status_msg.emit("全文翻译完成", "success")
        if self._trans_worker: self._trans_worker.deleteLater()
        self._trans_worker = None

    def _on_trans_all_err(self, err):
        self.btn_trans_all.setEnabled(True)
        self.btn_trans_all.setText("🌐 译中")
        self.status_msg.emit(f"翻译失败: {err}", "error")
        if self._trans_worker: self._trans_worker.deleteLater()
        self._trans_worker = None

    def _set_rate(self, rate: str):
        self._rate = rate
        # 更新语速按钮状态
        rate_labels = {"-20%": "0.8x", "+0%": "1.0x", "+20%": "1.2x", "+50%": "1.5x"}
        for btn in self._speed_btns:
            btn.setChecked(btn.text() == rate_labels.get(rate, ""))
        # 已生成的语音直接变速，不等下次生成
        if self._history:
            last = self._history[-1]["path"]
            if Path(last).exists():
                self._speed_audio(last, rate)
                return
        self.status_msg.emit(f"语速: {rate}", "info")

    def _speed_audio(self, path: str, rate: str):
        """FFmpeg atempo 变速已有音频，始终基于原始文件"""
        import subprocess, shutil
        p = Path(path)
        atempo = {"-20%": "0.8", "+0%": "1.0", "+20%": "1.2", "+50%": "1.5"}.get(rate, "1.0")
        orig = path  # 始终基于当前文件变速
        if atempo == "1.0":
            # 原速：直接用原始文件
            self._play_file(orig)
            return
        tmp = Path(orig).with_stem(f"{Path(orig).stem}_{rate.replace('%','').replace('+','p')}")
        from config import FFMPEG_BIN
        r = subprocess.run([FFMPEG_BIN, "-y", "-i", str(orig), "-filter:a", f"atempo={atempo}", str(tmp)],
            capture_output=True)
        if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            self.status_msg.emit(f"已变速: {rate}", "success")
            self._play_file(str(tmp))
        else:
            self.status_msg.emit(f"变速失败: {r.stderr.decode('utf-8', errors='replace')[-200:]}", "error")

    def _set_tts_source(self, side: str):
        """切换 TTS 生成来源：左栏（输入）或 右栏（润色结果）"""
        self._tts_source = side
        # 更新切换按钮状态
        for btn in self._src_btns:
            btn.setChecked((btn.text() == "左栏" and side == "left") or
                           (btn.text() == "右栏" and side == "right"))
        self._highlight_source()

    def _highlight_source(self):
        """高亮当前选中来源的编辑器边框"""
        active_border = "border:2px solid #3d8ef8;"
        inactive_border = "border:1px solid #2a2a2a;"
        if self._tts_source == "left":
            self.editor.setStyleSheet(_TEXT_EDIT.replace("border:1px solid #2a2a2a;", active_border))
            self.editor_polished.setStyleSheet(_TEXT_EDIT)
        else:
            self.editor.setStyleSheet(_TEXT_EDIT)
            self.editor_polished.setStyleSheet(_TEXT_EDIT.replace("border:1px solid #2a2a2a;", active_border))


def _polish_text(text: str, mode: str = "丰富") -> str:
    """AI润色：自动检测语言 + 三种润色模式（精简/同量/丰富）"""
    from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME

    if not LLM_API_KEY:
        t = text.strip()
        t = t.replace("，", ",").replace("。", ". ")
        lines = [l.strip() for l in t.split("\n") if l.strip()]
        return "\n".join(lines)

    # 检测语言
    cleaned = text.strip()
    if not cleaned:
        return cleaned
    chinese_chars = sum(1 for c in cleaned if '\u4e00' <= c <= '\u9fff')
    english_chars = sum(1 for c in cleaned if c.isascii() and c.isalpha())
    total_alpha = chinese_chars + english_chars
    if total_alpha == 0:
        lang = "other"
    elif chinese_chars / max(total_alpha, 1) > 0.3:
        lang = "zh"
    elif english_chars / max(total_alpha, 1) > 0.5:
        lang = "en"
    else:
        lang = "other"

    # 精简模式
    concise_zh = (
        "你是短视频文案编辑。请将以下文案做**精简润色**：\n"
        "— 删掉啰嗦、重复、无信息量的词句\n"
        "— 保留核心卖点和情绪，用更少的字表达同样的意思\n"
        "— 口语化，适合配音朗读\n"
        "— 输出纯精简后的文案，不要解释"
    )
    concise_en = (
        "You are a short-video script editor. **Condense** the following script:\n"
        "— Cut redundant, wordy, or filler phrases\n"
        "— Keep the core message and emotional impact, say the same thing in fewer words\n"
        "— Conversational tone, suitable for voice-over\n"
        "— Output ONLY the condensed script, no explanation"
    )

    # 同量模式
    same_zh = (
        "你是短视频配音文案专家。请润色以下文案，**保持字数大致不变**：\n"
        "— 替换平淡的词汇为更有画面感、更抓耳的表达\n"
        "— 调整句式让节奏更适合配音朗读\n"
        "— 可以换词、换句式，但不要明显扩写或缩写\n"
        "— 输出纯润色后的文案，不要解释"
    )
    same_en = (
        "You are a voice-over script editor. Polish the following script, **keeping roughly the same word count**:\n"
        "— Replace flat words with punchier, more visual alternatives\n"
        "— Adjust sentence rhythm for better voice-over flow\n"
        "— Swap words and rephrase, but don't significantly expand or shorten\n"
        "— Output ONLY the polished script, no explanation"
    )

    # 丰富模式
    rich_zh = (
        "你是短视频配音文案专家。请将以下文案做**丰富润色**，让表达更加饱满、生动、有感染力：\n"
        "— 口语化，节奏感强，适合配音朗读\n"
        "— 用更有画面感的词汇，适当加入情绪词、转折词增加张力\n"
        "— 可以适当扩写，让内容更饱满，但不能偏离原意\n"
        "— 输出纯润色后的文案，不要解释"
    )
    rich_en = (
        "You are a short-video copywriter. **Enrich** the following script — make it fuller, more vivid, more engaging:\n"
        "— Conversational tone, strong rhythm, suitable for voice-over\n"
        "— Use visual, punchy language; add emotional hooks and transitions for impact\n"
        "— Feel free to expand where natural, but stay true to the original meaning\n"
        "— Output ONLY the enriched script, no explanation"
    )

    mode_prompts = {
        "精简": {"zh": concise_zh, "en": concise_en, "other": concise_en},
        "同量": {"zh": same_zh, "en": same_en, "other": same_en},
        "丰富": {"zh": rich_zh, "en": rich_en, "other": rich_en},
    }
    system_prompt = mode_prompts.get(mode, mode_prompts["丰富"]).get(lang, rich_en)

    try:
        from openai import OpenAI
        client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=45.0)
        resp = client.chat.completions.create(
            model=LLM_MODEL_NAME,
            messages=[{
                "role": "system",
                "content": system_prompt,
            }, {
                "role": "user",
                "content": cleaned,
            }],
            temperature=0.75,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        t = text.strip()
        t = t.replace("，", ",").replace("。", ". ")
        lines = [l.strip() for l in t.split("\n") if l.strip()]
        return "\n".join(lines)


# ── 共享样式 ──

_TEXT_EDIT = (
    "QTextEdit{background:#0e0e0e;border:1px solid #2a2a2a;border-radius:6px;"
    "color:#ccc;font-size:13px;padding:8px;}"
    "QTextEdit:focus{border-color:#3d8ef8;}"
)

_PRIMARY = (
    "QPushButton{background:#3d8ef8;color:#fff;border:none;border-radius:6px;"
    "font-size:13px;font-weight:bold;padding:8px 18px;}"
    "QPushButton:hover{background:#5a9ff9;}"
    "QPushButton:disabled{background:#333;color:#666;}"
)

_SECONDARY = (
    "QPushButton{background:#1a3050;color:#3d8ef8;border:1px solid #3d8ef8;"
    "border-radius:6px;font-size:12px;font-weight:bold;padding:6px 14px;}"
    "QPushButton:hover{background:#2a4a70;}"
)

_GHOST = (
    "QPushButton{background:transparent;color:#888;border:1px solid #333;"
    "border-radius:5px;font-size:11px;padding:4px 10px;}"
    "QPushButton:hover{color:#ccc;border-color:#555;}"
)

_ACCENT = (
    "QPushButton{background:#3d8ef8;color:#fff;border:none;border-radius:6px;"
    "font-size:13px;font-weight:bold;padding:8px 20px;}"
    "QPushButton:hover{background:#5a9ff9;}"
    "QPushButton:disabled{background:#333;color:#666;}")

_SPEED_BTN = (
    "QPushButton{background:#1e1e1e;color:#888;border:1px solid #2a2a2a;"
    "border-radius:5px;font-size:12px;padding:2px;}"
    "QPushButton:hover{color:#ccc;border-color:#3d8ef8;}"
    "QPushButton:checked{background:#1a3050;color:#3d8ef8;border-color:#3d8ef8;}"
)

_TINY_BTN = (
    "QPushButton{background:#1e1e1e;color:#888;border:1px solid #2a2a2a;"
    "border-radius:4px;font-size:11px;padding:2px 10px;}"
    "QPushButton:hover{color:#ccc;border-color:#555;}"
)

_PROGRESS = (
    "QProgressBar{background:#222;border:none;border-radius:2px;}"
    "QProgressBar::chunk{background:#3d8ef8;border-radius:2px;}"
)

_LIST = (
    "QListWidget{background:#0e0e0e;border:1px solid #222;border-radius:6px;"
    "color:#aaa;font-size:12px;padding:4px;}"
    "QListWidget::item{padding:4px 8px;border-bottom:1px solid #1a1a1a;}"
    "QListWidget::item:hover{background:#1a2a3a;}"
    "QListWidget::item:selected{background:#1a3050;color:#3d8ef8;}"
)
