"""
download_panel.py — 下载面板

- 粘贴链接 → 选择音视频类型 → 下载到素材库
"""
from __future__ import annotations

import os, logging, re, threading, time, tempfile
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QTextEdit, QComboBox, QScrollArea, QProgressBar,
    QFrame, QSizePolicy, QApplication,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QUrl

from core.downloader import (
    DownloadTask, DownloadWorker,
    ytdlp_available, DOWNLOAD_DIR,
    get_available_browsers, auto_detect_browser, BROWSER_LABELS,
)
# ─── 样式常量 ───
PANEL_BG = "#1e1e1e"
INPUT_STYLE = """
    QLineEdit {
        background:#232a3a; color:#ffffff; border:2px solid #3d8ef8;
        border-radius:6px; padding:9px 12px; font-size:13px;
    }
    QLineEdit:focus { border-color:#5aa6ff; background:#2a3346; }
    QLineEdit::placeholder { color:#8b97ad; }
"""
COMBO_STYLE = """
    QComboBox {
        background:#2a2a2a; color:#ccc; border:1px solid #444;
        border-radius:3px; padding:3px 6px; font-size:11px;
        min-width:80px;
    }
    QComboBox:hover { border-color:#3d8ef8; }
    QComboBox QAbstractItemView {
        background:#2a2a2a; color:#ccc;
        selection-background-color:#3d8ef8;
    }
"""
BTN_STYLE = """
    QPushButton {
        background:#2a2a2a; color:#bbb; border:1px solid #444;
        border-radius:4px; padding:4px 12px; font-size:11px;
    }
    QPushButton:hover { background:#3a3a3a; color:#fff; border-color:#3d8ef8; }
"""
BTN_PRIMARY = """
    QPushButton {
        background:#3d8ef8; color:#fff; border:1px solid #3d8ef8;
        border-radius:4px; padding:4px 12px; font-size:11px; font-weight:500;
    }
    QPushButton:hover { background:#5599ff; }
"""
BTN_X = """
    QPushButton {
        background:transparent; color:#888; border:none;
        border-radius:4px; padding:0; font-size:14px; font-weight:bold;
    }
    QPushButton:hover { background:rgba(224,85,85,0.20); color:#e05555; }
"""
PROGRESS_STYLE = """
    QProgressBar {
        background:#2a2a2a; border:1px solid #333; border-radius:3px;
        height:8px; text-align:center;
    }
    QProgressBar::chunk {
        background:#3d8ef8; border-radius:3px;
    }
"""


class _DownloadItem(QFrame):
    """下载队列中单个项目的进度卡片"""
    cancel_requested = pyqtSignal(object)

    def __init__(self, task: DownloadTask, parent=None):
        super().__init__(parent)
        self.task = task
        self._final_path = ""
        self._state = "active"      # active / progress / completed / failed / cancelled
        self._got_real_progress = False  # 是否已收到真实下载进度（非阶段提示）
        self._start_time = time.time()
        self.setFixedHeight(54)
        self.setStyleSheet(
            "background:#222; border:1px solid #333; border-radius:4px;"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(3)

        # 第一行：标题 + 状态 + 操作按钮
        top = QHBoxLayout()
        top.setSpacing(6)
        self._title_lbl = QLabel(task.title or _extract_domain(task.url))
        self._title_lbl.setStyleSheet("color:#ddd; font-size:12px; border:none;")
        self._title_lbl.setToolTip(task.url)
        self._title_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top.addWidget(self._title_lbl, 1)

        self._status_lbl = QLabel("连接中...")
        self._status_lbl.setStyleSheet("color:#aaa; font-size:10px; border:none;")
        self._status_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top.addWidget(self._status_lbl)

        # 操作按钮容器
        self._action_btn = QPushButton("✕")
        self._action_btn.setFixedSize(20, 20)
        self._action_btn.setStyleSheet(BTN_X)
        self._action_btn.clicked.connect(self._on_action_click)
        self._action_btn.setToolTip("取消下载")
        top.addWidget(self._action_btn)
        lay.addLayout(top)

        # 进度条（初始为 busy/不确定模式，表示「正在处理，进度未知」；
        # 一旦收到真实下载进度会自动切回 0-100 模式）
        self._bar = QProgressBar()
        self._bar.setRange(0, 0)   # min==max → 不确定忙碌条
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        self._bar.setStyleSheet(PROGRESS_STYLE)
        lay.addWidget(self._bar)

        # 卡顿检测：若长时间拿不到真实进度，提示网络慢/需代理
        self._stuck_timer = QTimer(self)
        self._stuck_timer.setInterval(6000)
        self._stuck_timer.timeout.connect(self._check_stuck)
        self._stuck_timer.start()

    def _on_action_click(self):
        if self._final_path:
            # 已完成 → 在文件管理器中打开
            import subprocess
            subprocess.Popen(f'explorer /select,"{self._final_path}"')
        else:
            # 进行中 → 取消下载；失败/已结束时 → 删除队列条目
            self.cancel_requested.emit(self.task)

    def set_progress(self, pct: float, speed: str, eta: str, downloaded: str, total: str):
        # 阶段提示（无真实下载量，例如「解析链接中…」「换客户端重试」）：
        # 保持 busy 进度条，仅更新状态文字，不让用户误以为卡死
        if not downloaded and not total:
            text = (eta or "处理中...").strip()
            # 初始化阶段用亮蓝提示，避免用户以为卡死
            color = "#3d8ef8" if "初始化" in text else "#aaa"
            self._status_lbl.setText(text)
            self._status_lbl.setStyleSheet(f"color:{color}; font-size:10px; border:none;")
            return

        # 真实下载进度：切回 0-100 模式并显示百分比/速度/ETA
        self._state = "progress"
        self._got_real_progress = True
        self._stuck_timer.stop()
        self._bar.setRange(0, 100)
        self._bar.setValue(int(pct))
        self._bar.setStyleSheet(PROGRESS_STYLE)
        self._action_btn.setText("✕")
        self._action_btn.setFixedSize(20, 20)
        self._action_btn.setStyleSheet(BTN_X)
        self._action_btn.setToolTip("取消下载")
        self._action_btn.setVisible(True)
        parts = []
        if speed:
            parts.append(f"⬇ {speed}")
        if eta and eta != "Unknown":
            parts.append(f"⏱ {eta}")
        if downloaded and total:
            parts.append(f"{downloaded}/{total}")
        elif downloaded:
            parts.append(downloaded)
        self._status_lbl.setText("  ".join(parts))
        self._status_lbl.setStyleSheet("color:#aaa; font-size:10px; border:none;")

    def _check_stuck(self):
        """长时间无真实进度时提示用户当前正在等待（区分卡死与下载中）"""
        if self._state != "active" or self._got_real_progress:
            self._stuck_timer.stop()
            return
        elapsed = time.time() - self._start_time
        if elapsed > 12:
            self._status_lbl.setText("网络较慢，正在等待响应…（国内站点可能需代理）")
            self._status_lbl.setStyleSheet("color:#e0a040; font-size:10px; border:none;")

    def set_completed(self, path: str):
        self._state = "completed"
        self._final_path = path
        self._stuck_timer.stop()
        self._bar.setRange(0, 100)
        self._bar.setValue(100)
        self._bar.setStyleSheet(PROGRESS_STYLE.replace("#3d8ef8", "#4caf50"))
        name = os.path.basename(path)
        size = _fmt_file_size(path)
        self._status_lbl.setText(f"✅ {name}  ({size})")
        self._status_lbl.setStyleSheet("color:#4caf50; font-size:10px; border:none;")
        self._status_lbl.setToolTip(path)
        # 换成 📁 打开文件夹按钮
        self._action_btn.setText("📁")
        self._action_btn.setFixedSize(24, 24)
        self._action_btn.setStyleSheet("""
            QPushButton { background:#1a3a1a; color:#4caf50; border:1px solid #2a5a2a;
                border-radius:3px; font-size:12px; }
            QPushButton:hover { background:#2a5a2a; }
        """)
        self._action_btn.setToolTip(f"打开文件夹\n{path}")

    def set_failed(self, err: str):
        self._state = "failed"
        self._stuck_timer.stop()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setStyleSheet(PROGRESS_STYLE.replace("#3d8ef8", "#e05555"))
        self._status_lbl.setText(f"❌ {err[:50]}")
        self._status_lbl.setStyleSheet("color:#e05555; font-size:10px; border:none;")
        self._status_lbl.setToolTip(err)
        # 失败 → 换成 ✕ 删除按钮（点击直接删除该队列条目，不再重试）
        self._action_btn.setText("✕")
        self._action_btn.setFixedSize(20, 20)
        self._action_btn.setStyleSheet(BTN_X)
        self._action_btn.setToolTip("删除队列条目")
        self._action_btn.setVisible(True)

    def set_cancelled(self):
        self._state = "cancelled"
        self._stuck_timer.stop()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setStyleSheet(PROGRESS_STYLE.replace("#3d8ef8", "#555"))
        self._status_lbl.setText("已取消")
        self._status_lbl.setStyleSheet("color:#666; font-size:10px; border:none;")
        self._action_btn.setVisible(False)


def _fmt_file_size(path: str) -> str:
    try:
        s = os.path.getsize(path)
        for u in ("B", "KB", "MB", "GB"):
            if s < 1024:
                return f"{s:.0f}{u}"
            s /= 1024
        return f"{s:.1f}TB"
    except Exception:
        return ""


class DownloadPanel(QWidget):
    """
    下载面板
    信号：
    - download_finished(str) — 下载完成时发出文件路径
    """

    download_finished = pyqtSignal(str)
    url_downloaded = pyqtSignal(str)   # 下载完成时发出原始 URL（供扒取面板去重）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tasks: dict[str, DownloadTask] = {}      # task_id → task
        self._workers: dict[str, DownloadWorker] = {}  # task_id → worker
        self._items: dict[str, _DownloadItem] = {}     # task_id → ui
        self._build_ui()
        # 加载已保存的下载目录
        self._load_download_dir()
        # 恢复已保存的 Cookie 文件路径
        self._load_cookies_file()

    def _load_download_dir(self):
        """从 QSettings 恢复下载目录"""
        from PyQt6.QtCore import QSettings
        import core.downloader as dl
        s = QSettings("CreativeEnginePro", "DownloadPanel")
        saved = s.value("download_dir", "")
        if saved and os.path.isdir(str(saved)):
            dl.DOWNLOAD_DIR = str(saved)
            if hasattr(self, '_path_lbl'):
                self._path_lbl.setText(f"📁 {dl.DOWNLOAD_DIR}")

    # ─── Cookie 文件管理 ───
    def _load_cookies_file(self):
        """从 QSettings 恢复已保存的 Cookie 文件路径"""
        from PyQt6.QtCore import QSettings
        import core.downloader as dl
        s = QSettings("CreativeEnginePro", "DownloadPanel")
        saved = s.value("cookies_file", "")
        if saved and os.path.isfile(str(saved)):
            dl.set_cookies_file(str(saved))
            self._cookie_lbl.setText(f"🍪 {os.path.basename(str(saved))}")
            self._cookie_lbl.setStyleSheet("color:#4caf50; font-size:9px; border:none;")

    def _import_cookies(self):
        """导入 Netscape 格式 Cookie 文件"""
        from PyQt6.QtWidgets import QFileDialog
        import core.downloader as dl
        path, _ = QFileDialog.getOpenFileName(
            self, "导入 Cookie 文件（用 Get cookies.txt LOCALLY 扩展导出）",
            "", "Cookie 文件 (*.txt);;所有文件 (*.*)")
        if not path:
            return
        dl.set_cookies_file(path)
        self._cookie_lbl.setText(f"🍪 {os.path.basename(path)}")
        self._cookie_lbl.setStyleSheet("color:#4caf50; font-size:9px; border:none;")
        from PyQt6.QtCore import QSettings
        QSettings("CreativeEnginePro", "DownloadPanel").setValue("cookies_file", path)
        self._set_status(
            f"已导入 Cookie：{os.path.basename(path)}（YouTube/抖音 下载将使用此登录态）", "#4caf50")

    def _clear_cookies(self):
        """清除已导入的 Cookie 文件"""
        import core.downloader as dl
        dl.set_cookies_file("")
        self._cookie_lbl.setText("未导入")
        self._cookie_lbl.setStyleSheet("color:#666; font-size:9px; border:none;")
        from PyQt6.QtCore import QSettings
        QSettings("CreativeEnginePro", "DownloadPanel").remove("cookies_file")
        self._set_status("已清除 Cookie 文件", "#888")

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 标题 ──
        title = QLabel("下载")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFixedHeight(28)
        title.setStyleSheet(
            "background:#1a1a1a; color:#aaa; font-size:12px; font-weight:500;"
            "border-bottom:1px solid #333;"
        )
        root.addWidget(title)

        # ── 视频下载页（内含扒取板块，包滚动区避免内容溢出）──
        self._video_page = QWidget()
        self._build_video_page()
        self._video_scroll = QScrollArea()
        self._video_scroll.setWidgetResizable(True)
        self._video_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._video_scroll.setStyleSheet(
            "QScrollArea { background:transparent; border:none; }"
            "QScrollBar:vertical { background:#1a1a1a; width:6px; }"
            "QScrollBar::handle:vertical { background:#444; border-radius:3px; }"
        )
        self._video_scroll.setWidget(self._video_page)
        root.addWidget(self._video_scroll, 1)

    # ─── 视频下载页 ───
    def _build_video_page(self):
        from core.downloader import DOWNLOAD_DIR

        vp = QVBoxLayout(self._video_page)
        vp.setContentsMargins(6, 6, 6, 6)
        vp.setSpacing(4)

        # 下载目录
        path_row = QHBoxLayout()
        path_row.setSpacing(4)
        self._path_lbl = QLabel(f"📁 {DOWNLOAD_DIR}")
        self._path_lbl.setStyleSheet("color:#888; font-size:10px; background:#1a1a1a; border-radius:3px; padding:2px 6px;")
        self._path_lbl.setToolTip("下载文件保存目录")
        self._path_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        path_row.addWidget(self._path_lbl, 1)
        path_chg_btn = QPushButton("更改")
        path_chg_btn.setFixedWidth(40)
        path_chg_btn.setStyleSheet("""
            QPushButton { background:#2a2a2a; color:#aaa; border:1px solid #444; border-radius:3px; font-size:10px; padding:2px; }
            QPushButton:hover { color:#fff; border-color:#3d8ef8; }
        """)
        path_chg_btn.clicked.connect(self._change_download_dir)
        path_row.addWidget(path_chg_btn)
        vp.addLayout(path_row)

        # 链接输入（多行，每行一个链接，单链接也是一行）
        self._url_input = QTextEdit()
        self._url_input.setPlaceholderText("粘贴链接，每行一个：\nhttps://www.youtube.com/watch?v=xxx\nhttps://www.bilibili.com/video/xxx\nhttps://www.douyin.com/video/xxx")
        self._url_input.setMaximumHeight(100)
        self._url_input.setStyleSheet(INPUT_STYLE)
        self._url_input.setAcceptRichText(False)
        vp.addWidget(self._url_input)

        # 类型行
        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(6)
        fmt_row.addWidget(QLabel("类型:"))

        self._type_combo = QComboBox()
        self._type_combo.addItems(["视频+音频", "仅视频", "仅音频(MP3)"])
        self._type_combo.setStyleSheet(COMBO_STYLE)
        fmt_row.addWidget(self._type_combo)

        # 浏览器选择（决定用哪个浏览器的登录态 cookies）
        fmt_row.addWidget(QLabel("浏览器:"))
        self._browser_combo = QComboBox()
        self._browser_combo.setStyleSheet(COMBO_STYLE)
        self._browser_combo.setMinimumWidth(96)
        fmt_row.addWidget(self._browser_combo)

        # 唯一下载入口
        self._dl_btn = QPushButton("⬇ 下载")
        self._dl_btn.setStyleSheet(BTN_PRIMARY)
        self._dl_btn.clicked.connect(self._on_download)
        self._dl_btn.setToolTip("下载所有链接（每行一个，空行自动跳过）")
        fmt_row.addWidget(self._dl_btn)
        fmt_row.addStretch()
        vp.addLayout(fmt_row)

        # yt-dlp 更新按钮（独立一行，避免窄宽度时与类型/浏览器/下载挤成一团）
        update_row = QHBoxLayout()
        update_row.setSpacing(6)
        self._update_btn = QPushButton("🔄 更新yt-dlp")
        self._update_btn.setStyleSheet(BTN_STYLE)
        self._update_btn.clicked.connect(self._update_ytdlp)
        self._update_btn.setToolTip("更新 yt-dlp 到最新版本，修复某些站点失效问题")
        update_row.addWidget(self._update_btn)
        update_row.addStretch()
        vp.addLayout(update_row)

        # Cookie 文件导入（YouTube/抖音需要登录态，绕过 DPAPI 限制）
        cookie_row = QHBoxLayout()
        cookie_row.setSpacing(4)
        self._cookie_btn = QPushButton("🍪 导入Cookie")
        self._cookie_btn.setStyleSheet(BTN_STYLE)
        self._cookie_btn.clicked.connect(self._import_cookies)
        self._cookie_btn.setToolTip(
            "用浏览器扩展「Get cookies.txt LOCALLY」导出 YouTube/抖音 的\n"
            "Netscape 格式 Cookie 文件后导入，配合 Node.js 解决 n-challenge 反爬")
        cookie_row.addWidget(self._cookie_btn)

        self._cookie_lbl = QLabel("未导入")
        self._cookie_lbl.setStyleSheet("color:#666; font-size:9px; border:none;")
        cookie_row.addWidget(self._cookie_lbl, 1)

        self._cookie_clear = QPushButton("✕")
        self._cookie_clear.setFixedSize(20, 20)
        self._cookie_clear.setStyleSheet(BTN_X)
        self._cookie_clear.clicked.connect(self._clear_cookies)
        self._cookie_clear.setToolTip("清除已导入的 Cookie 文件")
        cookie_row.addWidget(self._cookie_clear)
        vp.addLayout(cookie_row)

        # 轻量状态提示（不阻塞 UI）
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color:#888; font-size:10px; padding:1px 0;")
        self._status_lbl.setWordWrap(True)
        vp.addWidget(self._status_lbl)

        # 延迟填充浏览器下拉（必须在 _status_lbl 创建之后，因为 _refresh_browser_combo 会调 _set_status）
        self._refresh_browser_combo()

        # 下载队列标题
        self._queue_title = QLabel("下载中:")
        self._queue_title.setVisible(False)
        vp.addWidget(self._queue_title)

        # 队列用滚动区包裹：列表变长时内部滚动，不会把面板/剪辑轨道挤变形
        self._queue_scroll = QScrollArea()
        self._queue_scroll.setWidgetResizable(True)
        self._queue_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._queue_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._queue_scroll.setStyleSheet(
            "QScrollArea { background:#151515; border:none; }"
            "QScrollArea QWidget#queueContent { background:#151515; }"
            "QScrollBar:vertical { background:#222; width:8px; }"
            "QScrollBar::handle:vertical { background:#444; border-radius:4px; }"
        )
        self._queue_content = QWidget()
        self._queue_content.setObjectName("queueContent")
        self._queue_container = QVBoxLayout(self._queue_content)
        self._queue_container.setContentsMargins(0, 0, 0, 0)
        self._queue_container.setSpacing(3)
        self._queue_container.addStretch()
        self._queue_scroll.setWidget(self._queue_content)
        vp.addWidget(self._queue_scroll, 1)

        # 最小宽度兜底：窄于此宽度布局会崩，限制后用户拉到这尺寸就拉不动，
        # UI 始终保持整齐（配合上面各 QLabel 的 elide，进一步变窄也不会重叠）
        self._video_page.setMinimumWidth(280)

    # ─── 下载 ───
    def _set_status(self, msg: str, color: str = "#888"):
        """轻量状态提示（约 2.5 秒后自动清除）"""
        self._status_lbl.setText(msg)
        self._status_lbl.setStyleSheet(f"color:{color}; font-size:10px; padding:1px 0;")
        if getattr(self, "_status_timer", None):
            self._status_timer.stop()
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(lambda: self._status_lbl.setText(""))
        self._status_timer.start(2500)

    def _refresh_browser_combo(self):
        """填充浏览器下拉：自动 + 本机已检测到的浏览器"""
        from core.downloader import get_available_browsers, auto_detect_browser
        self._browser_combo.clear()
        self._browser_combo.addItem("自动(推荐)")
        for b in get_available_browsers():
            self._browser_combo.addItem(f"{BROWSER_LABELS.get(b, b)}")
        # 显示当前将使用的浏览器（中性说明，不是报错）
        auto = auto_detect_browser()
        if auto:
            self._set_status(
                f"将使用「{BROWSER_LABELS.get(auto, auto)}」的登录态下载"
                f"（仅 YouTube / TikTok / 抖音 等需登录的站点会用到）", "#666")
        else:
            self._set_status(
                "未检测到可用浏览器，将尝试无登录下载（部分平台会失败）", "#666")


    def _on_download(self):
        """下载入口：解析多行链接，每行一个，加入队列"""
        raw = self._url_input.toPlainText().strip()
        if not raw:
            self._set_status("请粘贴链接（每行一个）", "#e0a040")
            return

        urls = []
        for line in raw.split("\n"):
            u = line.strip()
            if u and u.startswith("http"):
                urls.append(u)

        if not urls:
            self._set_status("未检测到有效链接（需以 http(s):// 开头）", "#e0a040")
            return
        if not ytdlp_available():
            self._set_status("yt-dlp 未安装，请运行: pip install yt-dlp", "#e05555")
            return

        type_idx = self._type_combo.currentIndex()
        sel = self._browser_combo.currentText()
        browser_key = self._resolve_browser_key(sel)

        for url in urls:
            task = DownloadTask(url=url)
            task.media_type = "audio" if type_idx == 2 else "video"
            task.video_only = (type_idx == 1)
            task.cookies_browser = browser_key
            task.title = ""
            self._add_task(task)

        self._queue_title.setVisible(True)
        cnt = len(urls)
        self._set_status(
            f"已添加 {cnt} 个链接到下载队列" if cnt > 1
            else f"已开始下载：{_extract_domain(urls[0])}",
            "#3d8ef8")

    @staticmethod
    def _resolve_browser_key(sel_text: str) -> str:
        """把下拉中文名映射回 yt-dlp 浏览器标识"""
        for k, v in BROWSER_LABELS.items():
            if v == sel_text:
                return k
        return ""

    # ─── 下载目录 ───
    def _change_download_dir(self):
        """更改下载保存目录"""
        import core.downloader as dl
        from PyQt6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self, "选择下载保存目录", dl.DOWNLOAD_DIR)
        if d:
            dl.DOWNLOAD_DIR = d
            self._path_lbl.setText(f"📁 {d}")
            from PyQt6.QtCore import QSettings
            QSettings("CreativeEnginePro", "DownloadPanel").setValue("download_dir", d)

    def _update_ytdlp(self):
        """更新 yt-dlp 到最新版本（带看门狗，避免后台线程挂死导致按钮永久不可用）"""
        if getattr(self, "_updating", False):
            return  # 防重复点击
        self._updating = True
        self._update_btn.setEnabled(False)
        self._update_btn.setText("更新中...")
        # 看门狗：即使后台线程异常未回调，90s 后强制恢复按钮
        if getattr(self, "_update_watchdog", None):
            self._update_watchdog.stop()
        self._update_watchdog = QTimer(self)
        self._update_watchdog.setSingleShot(True)
        self._update_watchdog.timeout.connect(self._force_update_btn_reset)
        self._update_watchdog.start(90000)
        def _bg():
            from core.downloader import ytdlp_update
            ok, msg = ytdlp_update()
            QTimer.singleShot(0, lambda: self._on_update_done(ok, msg))
        threading.Thread(target=_bg, daemon=True).start()

    def _force_update_btn_reset(self):
        """看门狗兜底：后台更新线程超时未返回时，强制恢复按钮"""
        self._updating = False
        self._update_btn.setEnabled(True)
        self._update_btn.setText("🔄 更新yt-dlp")
        self._update_btn.setStyleSheet(BTN_STYLE)
        self._set_status("更新超时，按钮已恢复 — 可稍后重试", "#e0a040")

    def _on_update_done(self, ok: bool, msg: str):
        self._updating = False
        if getattr(self, "_update_watchdog", None):
            self._update_watchdog.stop()
        self._update_btn.setEnabled(True)
        if ok:
            self._update_btn.setText("✅ 已更新")
            self._update_btn.setStyleSheet(BTN_STYLE.replace("#bbb", "#4caf50"))
        else:
            self._update_btn.setText("🔄 更新yt-dlp")
            self._update_btn.setStyleSheet(BTN_STYLE)
            # 静默，不弹窗

    def _add_task(self, task: DownloadTask):
        # 新下载前，先清理已完成/失败/已取消的旧条目，避免列表无限增长
        self._clear_finished_items()

        tid = f"{task.url}#{id(task)}"
        self._tasks[tid] = task

        # UI
        item = _DownloadItem(task)
        item.cancel_requested.connect(self._cancel_task)
        # 插入 stretch 之前
        self._queue_container.insertWidget(
            self._queue_container.count() - 1, item
        )
        self._items[tid] = item

        # Worker
        worker = DownloadWorker(task)
        worker.progress_signal.connect(
            lambda t, p, s, e, d, tot, tid=tid: self._on_progress(tid, p, s, e, d, tot)
        )
        worker.finished_signal.connect(
            lambda t, ok, path, err, tid=tid: self._on_finished(tid, ok, path, err)
        )
        self._workers[tid] = worker

        task.status = "downloading"
        worker.start()

    def add_tasks(self, tasks: list):
        """公开接口：批量添加任务（供扒取面板等外部调用）"""
        for t in tasks:
            self._add_task(t)
        self._queue_title.setVisible(True)
        self._set_status(f"已添加 {len(tasks)} 个链接到下载队列", "#3d8ef8")

    def _clear_finished_items(self):
        """移除已完成/失败/已取消的旧条目，保持队列只显示当前下载"""
        for tid in list(self._tasks.keys()):
            t = self._tasks[tid]
            if t.status in ("completed", "failed", "cancelled"):
                self._remove_item(tid)

    def _remove_item(self, tid: str):
        """从队列 UI 与数据结构中彻底移除某个条目"""
        w = self._items.pop(tid, None)
        if w:
            w.setParent(None)
            w.deleteLater()
        self._tasks.pop(tid, None)
        self._workers.pop(tid, None)
        if not self._items:
            self._queue_title.setVisible(False)

    def _cancel_task(self, task: DownloadTask):
        for tid, t in list(self._tasks.items()):
            if t is task:
                w = self._workers.get(tid)
                if w:
                    # 进行中 → 停止 worker 并标记取消
                    w.stop()
                    w.wait(3000)
                    self._workers.pop(tid, None)
                    if tid in self._items:
                        self._items[tid].set_cancelled()
                    t.status = "cancelled"
                    # 短暂显示「已取消」后从队列移除
                    QTimer.singleShot(1500, lambda t=tid: self._remove_item(t))
                else:
                    # 失败 / 已结束但无运行中的 worker → 直接删除队列条目
                    self._remove_item(tid)
                break

    def _on_progress(self, tid: str, pct: float, speed: str, eta: str,
                     downloaded: str, total: str):
        if tid in self._items:
            self._items[tid].set_progress(pct, speed, eta, downloaded, total)

    def _on_finished(self, tid: str, success: bool, path: str, error: str):
        print(f"[download_panel] _on_finished tid={tid!r} success={success} path={path!r} error={error!r}")
        worker = self._workers.pop(tid, None)
        if worker:
            worker.deleteLater()

        task = self._tasks.get(tid)

        if tid in self._items:
            if success:
                self._items[tid].set_completed(path)
                print(f"[download_panel] 发射 download_finished 信号: {path!r}")
                self.download_finished.emit(path)
                if task:
                    task.status = "completed"
                    self.url_downloaded.emit(task.url)
                QTimer.singleShot(3000, lambda t=tid: self._remove_item(t))
            elif error == "已取消":
                self._items[tid].set_cancelled()
                if task:
                    task.status = "cancelled"
                QTimer.singleShot(1500, lambda t=tid: self._remove_item(t))
            else:
                self._items[tid].set_failed(error)
                if task:
                    task.status = "failed"

# ─── 工具函数 ───
def _extract_domain(url: str) -> str:
    m = re.match(r'https?://([^/]+)', url)
    return m.group(1) if m else url[:30]

