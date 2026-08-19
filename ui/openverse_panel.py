# -*- coding: utf-8 -*-
"""剪辑工作台中的 Openverse 音乐/音效搜索板块。"""
from __future__ import annotations

import os
import re
from pathlib import Path

from PyQt6.QtCore import Qt, QSettings, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.downloader import DOWNLOAD_DIR
from core.openverse_api import (
    download_audio,
    format_duration,
    prepare_search_query,
    search_audio,
)


PANEL_BG = "#1e1e1e"
INPUT_STYLE = (
    "QLineEdit{background:#242a36;color:#fff;border:1px solid #3d8ef8;"
    "border-radius:5px;padding:7px 9px;}QLineEdit:focus{border-color:#65a6ff;}"
)
COMBO_STYLE = (
    "QComboBox,QSpinBox{background:#292929;color:#ccc;border:1px solid #444;"
    "border-radius:4px;padding:4px 6px;}"
    "QComboBox QAbstractItemView{background:#292929;color:#ddd;selection-background-color:#3d8ef8;}"
)
PRIMARY_STYLE = (
    "QPushButton{background:#3d8ef8;color:white;border:none;border-radius:4px;"
    "padding:6px 10px;font-weight:600;}QPushButton:hover{background:#5599ff;}"
    "QPushButton:disabled{background:#354052;color:#8792a5;}"
)
SECONDARY_STYLE = (
    "QPushButton{background:#2a2a2a;color:#bbb;border:1px solid #444;border-radius:4px;"
    "padding:5px 8px;}QPushButton:hover{color:white;border-color:#3d8ef8;}"
    "QPushButton:disabled{color:#666;border-color:#333;}"
)


class _SearchWorker(QThread):
    succeeded = pyqtSignal(object, int, object)
    failed = pyqtSignal(str)

    def __init__(self, query: str, category: str, license_filter: str,
                 page: int, page_size: int, parent=None):
        super().__init__(parent)
        self.args = (query, category, license_filter, page, page_size)

    def run(self):
        try:
            original_query, category, license_filter, page, page_size = self.args
            query, translated = prepare_search_query(original_query)
            results, total = search_audio(
                query,
                category=category,
                license_filter=license_filter,
                page=page,
                page_size=page_size,
            )
            category_relaxed = False
            # Openverse 很多音频有内容却缺少 category 元数据。分类筛选为 0 时
            # 自动扩大到全部音频，否则“音效”会经常看起来像完全搜不到。
            if category and not results:
                results, total = search_audio(
                    query,
                    category="",
                    license_filter=license_filter,
                    page=page,
                    page_size=page_size,
                )
                category_relaxed = bool(results)
            self.succeeded.emit(results, total, {
                "original_query": original_query,
                "search_query": query,
                "translated": translated,
                "category_relaxed": category_relaxed,
            })
        except Exception as exc:
            self.failed.emit(str(exc))


class _EmojiCheckButton(QPushButton):
    """用字符状态代替系统原生方块的轻量复选按钮。"""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._label = text
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            "QPushButton{background:transparent;border:none;color:#aaa;text-align:left;"
            "padding:2px 0;font-size:10px;}"
            "QPushButton:hover{color:#ddd;}"
        )
        self.toggled.connect(self._update_text)
        self._update_text(False)

    def _update_text(self, checked: bool):
        self.setText(f"{'☑️' if checked else '○'}  {self._label}")


class _DownloadWorker(QThread):
    progress = pyqtSignal(int)
    succeeded = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, item: dict, directory: str, parent=None):
        super().__init__(parent)
        self.item = dict(item)
        self.directory = directory

    def run(self):
        try:
            def report(done: int, total: int):
                self.progress.emit(int(done * 100 / total) if total > 0 else 0)

            path = download_audio(self.item, self.directory, report)
            self.succeeded.emit(path)
        except Exception as exc:
            self.failed.emit(str(exc))


class _ResultCard(QFrame):
    preview_requested = pyqtSignal(object, object)
    download_requested = pyqtSignal(object, object)
    source_requested = pyqtSignal(str)

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.item = item
        self.setStyleSheet(
            "QFrame{background:#242424;border:1px solid #353535;border-radius:6px;}"
            "QLabel{border:none;background:transparent;}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 8, 9, 8)
        layout.setSpacing(5)

        title = QLabel(item.get("title") or "未命名音频")
        title.setWordWrap(True)
        title.setToolTip(item.get("title") or "")
        title.setStyleSheet("color:#f0f0f0;font-size:12px;font-weight:600;")
        layout.addWidget(title)

        creator = QLabel(f"{item.get('creator') or '未知作者'} · {format_duration(item.get('duration', 0))}")
        creator.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        creator.setStyleSheet("color:#969696;font-size:10px;")
        layout.addWidget(creator)

        license_label = QLabel(item.get("license_label") or "许可证未知")
        license_label.setWordWrap(True)
        license_label.setStyleSheet(
            "color:#82c995;background:#203126;border-radius:3px;padding:2px 5px;font-size:9px;"
        )
        layout.addWidget(license_label)

        actions = QHBoxLayout()
        actions.setSpacing(5)
        self.preview_btn = QPushButton("▶ 试听")
        self.preview_btn.setStyleSheet(SECONDARY_STYLE)
        self.preview_btn.clicked.connect(lambda: self.preview_requested.emit(self.item, self))
        actions.addWidget(self.preview_btn)
        source_btn = QPushButton("来源")
        source_btn.setStyleSheet(SECONDARY_STYLE)
        source_btn.setEnabled(bool(item.get("landing_url")))
        source_btn.clicked.connect(lambda: self.source_requested.emit(item.get("landing_url", "")))
        actions.addWidget(source_btn)
        self.download_btn = QPushButton("下载")
        self.download_btn.setStyleSheet(PRIMARY_STYLE)
        self.download_btn.clicked.connect(lambda: self.download_requested.emit(self.item, self))
        actions.addWidget(self.download_btn, 1)
        layout.addLayout(actions)

    def set_previewing(self, active: bool):
        self.preview_btn.setText("■ 停止" if active else "▶ 试听")

    def set_downloading(self, active: bool):
        self.download_btn.setEnabled(not active)
        self.download_btn.setText("下载中…" if active else "下载")


class OpenversePanel(QWidget):
    """Openverse 搜索、试听、下载和素材库联动。"""

    download_finished = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page = 1
        self._total = 0
        self._shown = 0
        self._search_worker: _SearchWorker | None = None
        self._download_workers: set[_DownloadWorker] = set()
        self._active_preview_card: _ResultCard | None = None
        self._cards: list[_ResultCard] = []
        self._build_ui()
        self._build_player()

    def _build_ui(self):
        self.setStyleSheet(f"background:{PANEL_BG};color:#ddd;")
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 7)
        root.setSpacing(7)

        heading = QHBoxLayout()
        title = QLabel("🎵 Openverse")
        title.setStyleSheet("color:#f2f2f2;font-size:14px;font-weight:700;")
        heading.addWidget(title)
        heading.addStretch()
        free = QLabel("无需 Key")
        free.setStyleSheet("color:#82c995;background:#203126;border-radius:4px;padding:2px 6px;font-size:9px;")
        heading.addWidget(free)
        root.addLayout(heading)

        tip = QLabel("搜索开放许可音乐和音效；下载时自动保存作者与许可证。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#7f8792;font-size:10px;")
        root.addWidget(tip)

        search_row = QHBoxLayout()
        search_row.setSpacing(5)
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("例如：轻快、科技、转场、雨声…")
        self.query_edit.setStyleSheet(INPUT_STYLE)
        self.query_edit.returnPressed.connect(self.start_search)
        search_row.addWidget(self.query_edit, 1)
        self.search_btn = QPushButton("搜索")
        self.search_btn.setStyleSheet(PRIMARY_STYLE)
        self.search_btn.clicked.connect(self.start_search)
        search_row.addWidget(self.search_btn)
        root.addLayout(search_row)

        filters = QHBoxLayout()
        filters.setSpacing(5)
        self.category_combo = QComboBox()
        self.category_combo.addItem("全部", "")
        self.category_combo.addItem("音乐", "music")
        self.category_combo.addItem("音效", "sound_effect")
        self.category_combo.setStyleSheet(COMBO_STYLE)
        filters.addWidget(self.category_combo, 1)
        self.license_combo = QComboBox()
        self.license_combo.addItem("可商用/可改编", "commercial")
        self.license_combo.addItem("CC0/公共领域", "no_attribution")
        self.license_combo.addItem("CC BY", "attribution")
        self.license_combo.setStyleSheet(COMBO_STYLE)
        self.license_combo.setToolTip("默认排除禁止商用（NC）和禁止改编（ND）的素材")
        filters.addWidget(self.license_combo, 2)
        self.count_spin = QSpinBox()
        self.count_spin.setRange(10, 50)
        self.count_spin.setSingleStep(10)
        self.count_spin.setValue(20)
        self.count_spin.setSuffix(" 条")
        self.count_spin.setStyleSheet(COMBO_STYLE)
        filters.addWidget(self.count_spin)
        root.addLayout(filters)

        path_row = QHBoxLayout()
        path_row.setSpacing(5)
        self.path_label = QLabel()
        self.path_label.setStyleSheet("color:#777;font-size:9px;")
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_row.addWidget(self.path_label, 1)
        path_btn = QPushButton("保存位置")
        path_btn.setStyleSheet(SECONDARY_STYLE)
        path_btn.clicked.connect(self._choose_directory)
        path_row.addWidget(path_btn)
        root.addLayout(path_row)

        settings = QSettings("CreativeEnginePro", "OpenversePanel")
        shared = QSettings("CreativeEnginePro", "DownloadPanel")
        default_base = str(shared.value("download_dir", DOWNLOAD_DIR))
        self._directory = str(settings.value("download_dir", os.path.join(default_base, "Openverse")))
        self._update_path_label()

        self.add_to_library = _EmojiCheckButton("下载完成后自动加入剪辑素材库")
        self.add_to_library.setChecked(True)
        root.addWidget(self.add_to_library)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet(
            "QScrollArea{border:none;background:#1e1e1e;}"
            "QScrollBar:vertical{background:#1e1e1e;width:7px;}"
            "QScrollBar::handle:vertical{background:#454545;border-radius:3px;min-height:28px;}"
        )
        self.results_widget = QWidget()
        self.results_widget.setStyleSheet("background:#1e1e1e;")
        self.results_layout = QVBoxLayout(self.results_widget)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.setSpacing(7)
        self.empty_label = QLabel("输入关键词开始搜索\n支持音乐与音效直接下载")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet("color:#62666d;padding:28px 6px;font-size:11px;")
        self.results_layout.addWidget(self.empty_label)
        self.results_layout.addStretch()
        self.scroll.setWidget(self.results_widget)
        root.addWidget(self.scroll, 1)

        self.more_btn = QPushButton("加载更多")
        self.more_btn.setStyleSheet(SECONDARY_STYLE)
        self.more_btn.clicked.connect(self.load_more)
        self.more_btn.hide()
        root.addWidget(self.more_btn)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.setStyleSheet(
            "QProgressBar{background:#292929;border:none;border-radius:2px;}"
            "QProgressBar::chunk{background:#3d8ef8;border-radius:2px;}"
        )
        self.progress.hide()
        root.addWidget(self.progress)

        self.status_label = QLabel("Openverse 结果来自多个开放内容网站，请保留授权文件。")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color:#737983;font-size:9px;")
        root.addWidget(self.status_label)

    def _build_player(self):
        self._audio_output = QAudioOutput(self)
        self._audio_output.setVolume(0.8)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio_output)
        self._player.playbackStateChanged.connect(self._on_playback_state)
        self._player.errorOccurred.connect(self._on_player_error)

    def _update_path_label(self):
        compact = self._directory
        if len(compact) > 38:
            compact = "…" + compact[-37:]
        self.path_label.setText(f"保存到：{compact}")
        self.path_label.setToolTip(self._directory)

    def _choose_directory(self):
        path = QFileDialog.getExistingDirectory(self, "选择 Openverse 下载位置", self._directory)
        if path:
            self._directory = path
            QSettings("CreativeEnginePro", "OpenversePanel").setValue("download_dir", path)
            self._update_path_label()

    def start_search(self):
        self._page = 1
        self._shown = 0
        self._total = 0
        self._clear_results()
        self._run_search()

    def load_more(self):
        self._page += 1
        self._run_search()

    def _run_search(self):
        query = self.query_edit.text().strip()
        if not query:
            self.status_label.setText("请输入音乐或音效关键词")
            self.query_edit.setFocus()
            return
        if self._search_worker and self._search_worker.isRunning():
            return
        self.search_btn.setEnabled(False)
        self.more_btn.setEnabled(False)
        if re.search(r"[\u3400-\u9fff]", query):
            self.status_label.setText("正在转换中文关键词并搜索 Openverse…")
        else:
            self.status_label.setText("正在搜索 Openverse…")
        self.progress.setRange(0, 0)
        self.progress.show()
        worker = _SearchWorker(
            query,
            self.category_combo.currentData(),
            self.license_combo.currentData(),
            self._page,
            self.count_spin.value(),
            self,
        )
        self._search_worker = worker
        worker.succeeded.connect(self._on_search_success)
        worker.failed.connect(self._on_search_failed)
        worker.finished.connect(self._on_search_finished)
        worker.start()

    def _clear_results(self):
        self._stop_preview()
        for card in self._cards:
            self.results_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()
        self.empty_label.hide()
        self.more_btn.hide()

    def _on_search_success(self, results: list, total: int, info: dict):
        self._total = total
        for item in results:
            card = _ResultCard(item)
            card.preview_requested.connect(self._toggle_preview)
            card.download_requested.connect(self._start_download)
            card.source_requested.connect(self._open_source)
            self.results_layout.insertWidget(self.results_layout.count() - 1, card)
            self._cards.append(card)
        self._shown += len(results)
        if not self._cards:
            self.empty_label.setText("没有找到符合当前许可证条件的音频\n请更换关键词或筛选条件")
            self.empty_label.show()
        self.more_btn.setVisible(bool(results) and self._shown < self._total)
        notes = []
        if info.get("translated"):
            notes.append(f"中文已转换为：{info.get('search_query', '')}")
        if info.get("category_relaxed"):
            notes.append("所选分类无标签结果，已自动扩大搜索")
        prefix = " · ".join(notes)
        summary = f"已显示 {self._shown} 条 · 共约 {self._total} 条开放许可音频"
        self.status_label.setText(f"{prefix}\n{summary}" if prefix else summary)

    def _on_search_failed(self, message: str):
        if not self._cards:
            self.empty_label.setText("搜索失败\n请检查网络后重试")
            self.empty_label.show()
        self.status_label.setText(f"搜索失败：{message}")

    def _on_search_finished(self):
        self.search_btn.setEnabled(True)
        self.more_btn.setEnabled(True)
        self.progress.hide()
        worker = self._search_worker
        self._search_worker = None
        if worker:
            worker.deleteLater()

    def _toggle_preview(self, item: dict, card: _ResultCard):
        if self._active_preview_card is card and (
            self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        ):
            self._stop_preview()
            return
        self._stop_preview()
        url = item.get("audio_url", "")
        if not url:
            self.status_label.setText("该素材没有可用的试听地址")
            return
        self._active_preview_card = card
        card.set_previewing(True)
        self._player.setSource(QUrl(url))
        self._player.play()
        self.status_label.setText(f"正在试听：{item.get('title', '')}")

    def _stop_preview(self):
        self._player.stop() if hasattr(self, "_player") else None
        if self._active_preview_card:
            self._active_preview_card.set_previewing(False)
        self._active_preview_card = None

    def _on_playback_state(self, state):
        if state == QMediaPlayer.PlaybackState.StoppedState and self._active_preview_card:
            self._active_preview_card.set_previewing(False)
            self._active_preview_card = None

    def _on_player_error(self, _error, message: str):
        if message:
            self.status_label.setText(f"试听失败：{message}；仍可尝试直接下载")
        self._stop_preview()

    def _start_download(self, item: dict, card: _ResultCard):
        if not self._directory:
            self.status_label.setText("请先选择保存位置")
            return
        card.set_downloading(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.show()
        self.status_label.setText(f"正在下载：{item.get('title', '')}")
        worker = _DownloadWorker(item, self._directory, self)
        self._download_workers.add(worker)
        worker.progress.connect(self.progress.setValue)
        worker.succeeded.connect(lambda path, c=card: self._on_download_success(path, c))
        worker.failed.connect(lambda message, c=card: self._on_download_failed(message, c))
        worker.finished.connect(lambda w=worker: self._cleanup_download_worker(w))
        worker.start()

    def _on_download_success(self, path: str, card: _ResultCard):
        card.set_downloading(False)
        self.progress.setValue(100)
        self.status_label.setText(f"下载完成：{Path(path).name} · 授权信息已保存")
        if self.add_to_library.isChecked():
            self.download_finished.emit(path)

    def _on_download_failed(self, message: str, card: _ResultCard):
        card.set_downloading(False)
        self.status_label.setText(f"下载失败：{message}")

    def _cleanup_download_worker(self, worker: _DownloadWorker):
        self._download_workers.discard(worker)
        worker.deleteLater()
        if not any(w.isRunning() for w in self._download_workers):
            self.progress.hide()

    @staticmethod
    def _open_source(url: str):
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def closeEvent(self, event):
        self._stop_preview()
        super().closeEvent(event)
