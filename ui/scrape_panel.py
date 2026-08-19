"""
scrape_panel.py — 扒取面板

输入账号/频道/合辑链接 → 扒取视频列表 → 选择数量 → 批量下载
"""
from __future__ import annotations
import os, json, re, logging, random, uuid
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QSpinBox, QScrollArea, QFrame,
    QSizePolicy, QApplication, QMessageBox, QComboBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QSettings

# 项目内白色 ✓ 复选框
from ui.widgets import CheckMarkBox

from core.downloader import (
    DownloadTask, ytdlp_available, DOWNLOAD_DIR, _ytdlp_cmd, _yt_youtube_args,
    _yt_tiktok_args, _yt_douyin_args, _is_youtube, _is_tiktok,
    _is_douyin, YOUTUBE_COOKIES_FILE, _find_browser_for_cookies,
    auto_detect_browser, get_available_browsers, BROWSER_LABELS,
)

INPUT_STYLE = (
    "QLineEdit,QTextEdit,QSpinBox { background:#1a1a1a; color:#ccc;"
    "border:1px solid #333; border-radius:3px; padding:4px 6px;"
    "font-size:11px; }"
)
BTN_STYLE = (
    "QPushButton { background:#2a2a2a; color:#aaa; border:1px solid #444;"
    "border-radius:3px; padding:4px 10px; font-size:11px; }"
    "QPushButton:hover { color:#fff; border-color:#3d8ef8; }"
)
BTN_PRIMARY = (
    "QPushButton { background:#3d8ef8; color:#fff; border:none;"
    "border-radius:3px; padding:5px 14px; font-size:12px;"
    "font-weight:500; }"
    "QPushButton:hover { background:#5a9ff8; }"
)


class _ScrapeWorker(QThread):
    """后台线程：用 yt-dlp --flat-playlist --dump-json 获取视频列表"""
    progress = pyqtSignal(str)
    finished = pyqtSignal(list)  # [(title, url, duration), ...]
    error = pyqtSignal(str)

    def __init__(self, url: str, max_count: int = 50, random_pick: bool = False):
        super().__init__()
        self._url = url
        self._max = max_count
        self._random_pick = random_pick

    def run(self):
        import subprocess
        try:
            # 随机模式：扒取更大的池子再从中随机抽取
            fetch_count = self._max * 5 if self._random_pick else self._max
            fetch_count = min(fetch_count, 200)  # 上限 200 防止太慢

            cmd = _ytdlp_cmd() + [
                "--flat-playlist",
                "--dump-json",
                "--no-playlist",
                "--playlist-end", str(fetch_count),
                "--socket-timeout", "30",
            ]
            # 平台专用参数
            if _is_youtube(self._url):
                browser = _find_browser_for_cookies() or ""
                cmd += _yt_youtube_args(browser, YOUTUBE_COOKIES_FILE)
            elif _is_tiktok(self._url):
                browser = _find_browser_for_cookies() or ""
                cmd += _yt_tiktok_args(browser, YOUTUBE_COOKIES_FILE)
            elif _is_douyin(self._url):
                browser = _find_browser_for_cookies() or ""
                cmd += _yt_douyin_args(browser, YOUTUBE_COOKIES_FILE)

            cmd.append(self._url)
            self.progress.emit("正在扒取列表…")

            r = subprocess.run(
                cmd, capture_output=True, timeout=120 if self._random_pick else 60,
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
            )
            if r.returncode != 0:
                err = r.stderr.decode("utf-8", errors="replace").strip()
                if not err:
                    err = r.stdout.decode("utf-8", errors="replace").strip()
                self.error.emit(err[:200] or "yt-dlp 退出码 %d" % r.returncode)
                return

            results = []
            raw = r.stdout.decode("utf-8", errors="replace")
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                title = d.get("title", "") or d.get("fulltitle", "") or "(无标题)"
                url = d.get("url", "") or d.get("webpage_url", "") or d.get("original_url", "")
                dur = d.get("duration", 0) or 0
                if url:
                    results.append((title, url, dur))

            if not results:
                self.error.emit("未找到任何视频（链接可能不是频道/账号/合辑页，或不支持此站点）")
            else:
                # 随机模式：从更大的池子中随机抽取用户指定数量
                if self._random_pick and len(results) > self._max:
                    results = random.sample(results, self._max)
                self.finished.emit(results)

        except subprocess.TimeoutExpired:
            self.error.emit("扒取超时（网络不可达或需代理）")
        except Exception as e:
            self.error.emit(str(e)[:200])


class _VideoCheckItem(QFrame):
    """扒取列表中单个视频条目（含复选框）"""
    toggled = pyqtSignal()
    remove_clicked = pyqtSignal()  # 点击删除

    def __init__(self, title: str, url: str, duration: float, parent=None):
        super().__init__(parent)
        self.url = url
        self._title = title
        self.setFixedHeight(32)
        self.setStyleSheet(
            "background:#1e1e1e; border-bottom:1px solid #2a2a2a;"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 2, 4, 2)
        lay.setSpacing(6)

        self._cb = CheckMarkBox()
        self._cb.setChecked(True)
        self._cb.toggled.connect(self.toggled.emit)
        lay.addWidget(self._cb)

        # 标题（省略太长的）
        display = title if len(title) <= 40 else title[:38] + "…"
        lbl = QLabel(display)
        lbl.setStyleSheet("color:#ccc; font-size:11px; border:none;")
        lbl.setToolTip(f"{title}\n双击在浏览器预览")
        lay.addWidget(lbl, 1)

        # 时长
        if duration > 0:
            m, s = divmod(int(duration), 60)
            dur_str = f"{m}:{s:02d}" if m > 0 else f"{s}s"
        else:
            dur_str = ""
        dur_lbl = QLabel(dur_str)
        dur_lbl.setStyleSheet("color:#666; font-size:10px; border:none;")
        dur_lbl.setFixedWidth(40)
        dur_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        lay.addWidget(dur_lbl)

        # 删除按钮
        del_btn = QPushButton("✕")
        del_btn.setFixedSize(18, 18)
        del_btn.setStyleSheet(
            "QPushButton { background:transparent; color:#666; border:none; font-size:10px; }"
            "QPushButton:hover { color:#e05555; }"
        )
        del_btn.clicked.connect(self.remove_clicked.emit)
        lay.addWidget(del_btn)

    def mouseDoubleClickEvent(self, event):
        """双击 → 在浏览器打开预览"""
        import webbrowser
        webbrowser.open(self.url)

    @property
    def checked(self) -> bool:
        return self._cb.isChecked()

    @checked.setter
    def checked(self, val: bool):
        self._cb.setChecked(val)


class ScrapePanel(QWidget):
    """
    扒取面板
    信号：
    - download_requested(list[DownloadTask]) — 用户确认下载一批视频
    """
    download_requested = pyqtSignal(list)
    tail_settings_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[_VideoCheckItem] = []
        self._downloaded_urls: set = set()  # 已下载过的 URL
        self._scrape_dir = DOWNLOAD_DIR
        self._load_download_dir()
        self._build_ui()
        self._load_downloaded_cache()

    def _load_download_dir(self):
        """从 QSettings 恢复上次保存的下载目录"""
        from PyQt6.QtCore import QSettings
        import core.downloader as dl
        s = QSettings("CreativeEnginePro", "DownloadPanel")
        saved = s.value("download_dir", "")
        if saved and os.path.isdir(str(saved)):
            dl.DOWNLOAD_DIR = str(saved)
            self._scrape_dir = str(saved)

    # ─── 已下载缓存 ───
    def _get_cache_path(self):
        import core.downloader as dl
        return os.path.join(dl.DOWNLOAD_DIR, ".cep_scraped.json")

    def _load_downloaded_cache(self):
        """加载已下载 URL 列表，用于自动取消勾选"""
        try:
            p = self._get_cache_path()
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._downloaded_urls = set(data.get("urls", []))
        except Exception:
            self._downloaded_urls = set()

    def _save_downloaded_cache(self):
        try:
            p = self._get_cache_path()
            data = {"urls": list(self._downloaded_urls)}
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass

    def mark_downloaded(self, url: str):
        """标记某个 URL 已下载（由外部下载完成后调用）"""
        self._downloaded_urls.add(url)
        self._save_downloaded_cache()

    # ─── UI ───
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 标题栏 ──
        title = QLabel("📡 扒取视频")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFixedHeight(28)
        title.setStyleSheet(
            "background:#1a1a1a; color:#aaa; font-size:12px; font-weight:500;"
            "border-bottom:1px solid #333;"
        )
        root.addWidget(title)

        # ── 输入区域 ──
        input_section = QWidget()
        inp = QVBoxLayout(input_section)
        inp.setContentsMargins(8, 8, 8, 6)
        inp.setSpacing(6)

        # 链接输入
        url_label = QLabel("链接")
        url_label.setStyleSheet("color:#aaa; font-size:11px; font-weight:500; border:none;")
        inp.addWidget(url_label)
        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("粘贴账号主页 / 频道 / 合辑链接…")
        self._url_input.setMinimumHeight(32)
        self._url_input.setStyleSheet(
            "QLineEdit { background:#111; color:#fff; border:2px solid #3d8ef8;"
            "border-radius:4px; padding:6px 10px; font-size:12px; }"
            "QLineEdit:focus { border-color:#5a9ff8; }")
        self._url_input.returnPressed.connect(self._scrape)
        inp.addWidget(self._url_input)

        # 存储目录
        dir_row = QHBoxLayout()
        dir_row.setSpacing(6)
        dir_label = QLabel("存储")
        dir_label.setStyleSheet("color:#aaa; font-size:11px; font-weight:500; border:none;")
        dir_row.addWidget(dir_label)
        self._dir_lbl = QLabel(self._scrape_dir)
        self._dir_lbl.setStyleSheet("color:#888; font-size:10px; background:#1a1a1a;"
                                     "border-radius:3px; padding:3px 6px; border:none;")
        self._dir_lbl.setToolTip("扒取的视频将下载到此目录")
        dir_row.addWidget(self._dir_lbl, 1)
        dir_btn = QPushButton("更改")
        dir_btn.setFixedWidth(44)
        dir_btn.setStyleSheet(
            "QPushButton { background:#2a2a2a; color:#aaa; border:1px solid #444;"
            "border-radius:3px; font-size:10px; padding:3px; }"
            "QPushButton:hover { color:#fff; border-color:#3d8ef8; }")
        dir_btn.clicked.connect(self._change_dir)
        dir_row.addWidget(dir_btn)
        inp.addLayout(dir_row)

        # 数量 + 按钮
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(8)
        ctrl_row.addWidget(QLabel("扒取"))
        self._count_spin = QSpinBox()
        self._count_spin.setRange(1, 200)
        self._count_spin.setValue(20)
        self._count_spin.setFixedWidth(56)
        self._count_spin.setStyleSheet(
            "QSpinBox { background:#111; color:#fff; border:1px solid #444;"
            "border-radius:3px; padding:3px 6px; font-size:11px; }")
        ctrl_row.addWidget(self._count_spin)
        ctrl_row.addWidget(QLabel("个"))

        # 随机选取复选框
        self._random_cb = CheckMarkBox()
        self._random_cb.setChecked(False)
        self._random_cb.setToolTip("开启后从更大范围内随机抽取，每次结果不同")
        ctrl_row.addWidget(self._random_cb)
        random_lbl = QLabel("🔀随机")
        random_lbl.setStyleSheet("color:#aaa; font-size:11px; border:none;")
        random_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        random_lbl.mousePressEvent = lambda e: self._random_cb.setChecked(not self._random_cb.isChecked())
        ctrl_row.addWidget(random_lbl)

        self._scrape_btn = QPushButton("🔍 开始扒取")
        self._scrape_btn.setStyleSheet(BTN_PRIMARY)
        self._scrape_btn.clicked.connect(self._scrape)
        ctrl_row.addWidget(self._scrape_btn, 1)
        inp.addLayout(ctrl_row)

        # 提示
        tip = QLabel("💡 勾选「🔀随机」每次扒取结果不同；同一账号建议先切换存储目录避免重复下载")
        tip.setStyleSheet("color:#e0a040; font-size:10px; padding:2px 0; border:none;")
        tip.setWordWrap(True)
        inp.addWidget(tip)

        root.addWidget(input_section)

        # ── 下载后动作：这里只选择去向，具体尾页预设统一在尾页处理页维护 ──
        auto_box = QFrame()
        auto_box.setStyleSheet(
            "QFrame{background:#171a20;border:1px solid #303641;border-radius:5px;}"
            "QLabel{border:none;background:transparent;}"
            "QComboBox{background:#111;color:#ddd;border:1px solid #3b414d;"
            "border-radius:3px;padding:3px 5px;font-size:10px;}"
        )
        auto_lay = QVBoxLayout(auto_box)
        auto_lay.setContentsMargins(7, 6, 7, 7)
        auto_lay.setSpacing(5)
        title_row = QHBoxLayout()
        auto_title = QLabel("⚡ 下载后处理")
        auto_title.setStyleSheet("color:#7db3ff;font-size:11px;font-weight:600;")
        title_row.addWidget(auto_title)
        title_row.addStretch()
        auto_lay.addLayout(title_row)

        action_row = QHBoxLayout()
        self._post_dest = QComboBox()
        self._post_dest.addItem("只进入素材库", "library")
        self._post_dest.addItem("使用尾页处理页预设", "tail")
        action_row.addWidget(self._post_dest, 1)
        self._tail_settings_btn = QPushButton("去尾页设置")
        self._tail_settings_btn.setStyleSheet(BTN_STYLE)
        self._tail_settings_btn.clicked.connect(self.tail_settings_requested.emit)
        action_row.addWidget(self._tail_settings_btn)
        auto_lay.addLayout(action_row)
        self._auto_summary = QLabel("")
        self._auto_summary.setWordWrap(True)
        self._auto_summary.setStyleSheet("color:#777;font-size:9px;")
        auto_lay.addWidget(self._auto_summary)
        root.addWidget(auto_box)

        self._load_postprocess_preset()
        self._post_dest.currentIndexChanged.connect(self._on_postprocess_changed)
        self._on_postprocess_changed()

        # ── 分隔线 ──
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#333;")
        root.addWidget(sep)

        # ── 结果区域 ──
        result_section = QWidget()
        res = QVBoxLayout(result_section)
        res.setContentsMargins(8, 6, 8, 8)
        res.setSpacing(4)

        # 工具栏
        bar = QHBoxLayout()
        bar.setSpacing(4)
        self._check_all = QPushButton("全选")
        self._check_all.setStyleSheet(BTN_STYLE)
        self._check_all.clicked.connect(lambda: self._set_all_checked(True))
        bar.addWidget(self._check_all)
        self._uncheck_all = QPushButton("取消全选")
        self._uncheck_all.setStyleSheet(BTN_STYLE)
        self._uncheck_all.clicked.connect(lambda: self._set_all_checked(False))
        bar.addWidget(self._uncheck_all)
        self._clear_btn = QPushButton("清空")
        self._clear_btn.setStyleSheet(BTN_STYLE)
        self._clear_btn.clicked.connect(self._clear_list)
        bar.addWidget(self._clear_btn)
        bar.addStretch()
        self._sel_lbl = QLabel("")
        self._sel_lbl.setStyleSheet("color:#888; font-size:10px; border:none;")
        bar.addWidget(self._sel_lbl)
        res.addLayout(bar)

        # 列表
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(
            "QScrollArea { background:#1a1a1a; border:1px solid #2a2a2a; border-radius:4px; }"
            "QScrollBar:vertical { background:#1a1a1a; width:6px; }"
            "QScrollBar::handle:vertical { background:#444; border-radius:3px; }")
        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(0)
        self._list_layout.addStretch()
        self._scroll.setWidget(self._list_widget)
        res.addWidget(self._scroll, 1)

        # 状态 + 下载按钮
        self._status = QLabel("")
        self._status.setStyleSheet("color:#888; font-size:10px; padding:2px 0; border:none;")
        res.addWidget(self._status)

        self._dl_btn = QPushButton("⬇ 下载勾选的视频")
        self._dl_btn.setStyleSheet(BTN_PRIMARY + "padding:7px; font-size:13px;")
        self._dl_btn.clicked.connect(self._download_selected)
        self._dl_btn.setEnabled(False)
        res.addWidget(self._dl_btn)

        root.addWidget(result_section, 1)

        # ── 更新按钮文字 ──
        self._update_dl_btn_text()

    # ─── 逻辑 ───
    def _load_postprocess_preset(self):
        s = QSettings("CreativeEnginePro", "ScrapeAutomation")
        dest = str(s.value("destination", "library"))
        idx = self._post_dest.findData(dest)
        self._post_dest.setCurrentIndex(max(0, idx))

    def _save_postprocess_preset(self, *_args):
        if not hasattr(self, "_post_dest"):
            return
        s = QSettings("CreativeEnginePro", "ScrapeAutomation")
        s.setValue("destination", self._post_dest.currentData())
        self._update_dl_btn_text()

    def _on_postprocess_changed(self, *_args):
        is_tail = self._post_dest.currentData() == "tail"
        self._tail_settings_btn.setEnabled(is_tail)
        self._refresh_tail_preset_summary()
        self._save_postprocess_preset()

    @staticmethod
    def _settings_bool(value, default=False):
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def _tail_preset_from_settings(self):
        s = QSettings("CreativeEnginePro", "TailAutomation")
        return {
            "destination": "tail",
            "tail_mode": str(s.value("tail_mode", "append")),
            "tail_path": str(s.value("tail_path", "")),
            "ratio": str(s.value("ratio", "保持原样 (极速模式 - 不重编码)")),
            "rename": str(s.value("rename", "")),
            "output_dir": str(s.value("output_dir", "")),
            "auto_export": self._settings_bool(s.value("auto_export", True), True),
        }

    def _refresh_tail_preset_summary(self):
        if self._post_dest.currentData() != "tail":
            self._auto_summary.setText("下载完成后自动进入剪辑素材库")
            return
        preset = self._tail_preset_from_settings()
        mode = "智能替换旧尾页" if preset["tail_mode"] == "smart_replace" else "直接追加"
        tail = os.path.basename(preset["tail_path"]) if preset["tail_path"] else "未设置尾页"
        export = "自动导出" if preset["auto_export"] else "仅进入尾页队列"
        self._auto_summary.setText(f"当前尾页预设：{mode} · {tail} · {export}")

    def _postprocess_preset(self):
        if self._post_dest.currentData() == "tail":
            return self._tail_preset_from_settings()
        return {"destination": "library"}

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_tail_preset_summary()

    def _scrape(self):
        url = self._url_input.text().strip()
        if not url or not url.startswith("http"):
            self._status.setText("请输入以 http(s):// 开头的完整链接")
            self._status.setStyleSheet("color:#e0a040; font-size:11px;")
            return

        if not ytdlp_available():
            self._status.setText("yt-dlp 未安装")
            self._status.setStyleSheet("color:#e05555; font-size:11px;")
            return

        self._clear_list()
        self._status.setText("⏳ 正在扒取…")
        self._status.setStyleSheet("color:#3d8ef8; font-size:11px;")
        self._scrape_btn.setEnabled(False)
        self._dl_btn.setEnabled(False)

        self._worker = _ScrapeWorker(url, self._count_spin.value(), self._random_cb.isChecked())
        self._worker.progress.connect(lambda m: self._on_scrape_progress(m))
        self._worker.finished.connect(self._on_scrape_done)
        self._worker.error.connect(self._on_scrape_error)
        self._worker.start()

    def _on_scrape_progress(self, msg: str):
        self._status.setText(msg)

    def _on_scrape_done(self, results: list):
        self._scrape_btn.setEnabled(True)

        if not results:
            self._status.setText("未找到视频（链接可能不是频道/账号/合辑页）")
            self._status.setStyleSheet("color:#e0a040; font-size:11px;")
            return

        # 刷新已下载缓存
        self._load_downloaded_cache()

        self._items.clear()
        count = 0
        for title, url, dur in results:
            item = _VideoCheckItem(title, url, dur)
            # 已下载过的自动取消勾选
            if url in self._downloaded_urls:
                item.checked = False
            item.toggled.connect(self._update_selection_label)
            item.remove_clicked.connect(lambda u=url: self._remove_one_item(u))
            # 插入到 stretch 之前
            self._list_layout.insertWidget(self._list_layout.count() - 1, item)
            self._items.append(item)
            count += 1

        self._update_selection_label()
        self._dl_btn.setEnabled(True)
        self._status.setText(
            f"✅ 共扒取 {count} 个视频，已下载 {count - sum(1 for i in self._items if i.checked)} 个已取消勾选"
        )
        self._status.setStyleSheet("color:#4caf50; font-size:11px;")

    def _on_scrape_error(self, err: str):
        self._scrape_btn.setEnabled(True)
        self._status.setText(f"❌ {err}")
        self._status.setStyleSheet("color:#e05555; font-size:11px;")

    def _clear_list(self):
        for item in self._items:
            self._list_layout.removeWidget(item)
            item.setParent(None)
            item.deleteLater()
        self._items.clear()
        self._update_selection_label()
        self._dl_btn.setEnabled(False)
        self._status.setText("")

    def _remove_one_item(self, url: str):
        """删除单个扒取条目"""
        for item in self._items:
            if item.url == url:
                self._items.remove(item)
                self._list_layout.removeWidget(item)
                item.setParent(None)
                item.deleteLater()
                self._update_selection_label()
                if not self._items:
                    self._dl_btn.setEnabled(False)
                    self._status.setText("")
                return

    def _update_selection_label(self):
        total = len(self._items)
        sel = sum(1 for i in self._items if i.checked)
        self._sel_lbl.setText(f"已选 {sel}/{total}")
        self._update_dl_btn_text()

    def _update_dl_btn_text(self):
        if not hasattr(self, "_dl_btn"):
            return
        sel = sum(1 for i in self._items if i.checked)
        if sel > 0:
            if hasattr(self, "_post_dest") and self._post_dest.currentData() == "tail":
                self._dl_btn.setText(f"⚡ 下载并自动加尾页（{sel} 个）")
            else:
                self._dl_btn.setText(f"⬇ 下载勾选的 {sel} 个视频")
        else:
            self._dl_btn.setText("⬇ 下载勾选的视频")

    def set_postprocess_status(self, text: str, error: bool = False):
        """供主窗口的自动化编排器回写批次状态。"""
        self._status.setText(text)
        color = "#e05555" if error else "#4caf50"
        self._status.setStyleSheet(f"color:{color};font-size:10px;border:none;")
        if text.startswith(("✅", "❌")):
            self._dl_btn.setEnabled(True)

    def _set_all_checked(self, val: bool):
        for item in self._items:
            item.checked = val
        self._update_selection_label()

    def _download_selected(self):
        preset = self._postprocess_preset()
        if preset["destination"] == "tail":
            if not preset["tail_path"] or not os.path.isfile(preset["tail_path"]):
                self._status.setText("请先选择有效的预设尾页视频")
                self._status.setStyleSheet("color:#e0a040;font-size:10px;border:none;")
                return
            if preset["auto_export"] and not os.path.isdir(preset["output_dir"]):
                self._status.setText("请先选择有效的自动导出目录")
                self._status.setStyleSheet("color:#e0a040;font-size:10px;border:none;")
                return
        self._save_postprocess_preset()
        batch_id = uuid.uuid4().hex
        tasks = []
        for item in self._items:
            if item.checked and item.url:
                t = DownloadTask(url=item.url)
                t.title = ""
                t.media_type = "video"
                t.output_dir = self._scrape_dir
                t.batch_id = batch_id
                t.postprocess = dict(preset)
                tasks.append(t)

        if not tasks:
            self._status.setText("请至少勾选一个视频")
            self._status.setStyleSheet("color:#e0a040; font-size:10px; border:none;")
            return

        self.download_requested.emit(tasks)
        self._dl_btn.setEnabled(False)
        action = "下载并自动加尾页" if preset["destination"] == "tail" else "下载"
        self._status.setText(f"✅ 已提交 {len(tasks)} 个视频：{action}")
        self._status.setStyleSheet("color:#4caf50; font-size:10px; border:none;")

    def _change_dir(self):
        """更改扒取视频的存储目录"""
        from PyQt6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self, "选择存储目录", self._scrape_dir)
        if d:
            self._scrape_dir = d
            self._dir_lbl.setText(d)
            # 同步到全局下载目录（避免下载页显示不同）
            import core.downloader as dl
            dl.DOWNLOAD_DIR = d
            from PyQt6.QtCore import QSettings
            QSettings("CreativeEnginePro", "DownloadPanel").setValue("download_dir", d)
            self._status.setText(f"📁 存储目录已更改为：{d}")
            self._status.setStyleSheet("color:#4caf50; font-size:11px; border:none;")
