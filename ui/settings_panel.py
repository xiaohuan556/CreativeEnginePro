"""CreativeEnginePro 全局设置中心。

全局设置只管理跨工作台共享的配置：AI 凭据、下载环境、性能和程序路径。
尾页、轮播、配音等业务预设继续在对应工作台维护，避免重复配置源。
"""
from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal, QSettings
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QFrame, QScrollArea, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox,
    QStackedWidget, QFileDialog,
)


_DEFAULT_BASE = {
    "openai": "https://modelhub.ailemac.com/api/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "custom_llm": "",
}


class SettingsPanel(QWidget):
    """按工具工作流组织的全局设置面板。"""

    settings_saved = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._nav_buttons = []
        self._build()
        self._load_prefs()

    # ── 布局 ──────────────────────────────────────────────
    def _build(self):
        self.setStyleSheet("background:#202124;color:#d8dbe2;")
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QFrame()
        sidebar.setFixedWidth(176)
        sidebar.setStyleSheet("QFrame{background:#17181b;border-right:1px solid #303137;}")
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 18, 12, 14)
        side.setSpacing(6)
        title = _label("设置", "#f2f3f5", 18, True)
        side.addWidget(title)
        subtitle = _label("CreativeEnginePro", "#737782", 9)
        side.addWidget(subtitle)
        side.addSpacing(14)

        self.stack = QStackedWidget()
        pages = [
            ("🤖  AI 引擎", self._build_ai_page()),
            ("⬇  下载与素材", self._build_download_page()),
            ("⚡  性能与缓存", self._build_performance_page()),
            ("ℹ  关于与路径", self._build_about_page()),
        ]
        for index, (text, page) in enumerate(pages):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(_NAV_BUTTON)
            button.clicked.connect(lambda checked=False, i=index: self._switch_page(i))
            self._nav_buttons.append(button)
            side.addWidget(button)
            self.stack.addWidget(page)
        side.addStretch()
        side.addWidget(_label("业务预设请在对应工作台设置", "#676b74", 9))
        root.addWidget(sidebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self.stack, 1)

        actions = QFrame()
        actions.setStyleSheet("QFrame{background:#191a1d;border-top:1px solid #303137;}")
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(18, 10, 18, 10)
        self.status_label = _label("", "#78c58b", 10)
        action_layout.addWidget(self.status_label)
        action_layout.addStretch()
        reload_btn = QPushButton("重新加载")
        reload_btn.setStyleSheet(_SECONDARY_BUTTON)
        reload_btn.clicked.connect(self._load_prefs)
        action_layout.addWidget(reload_btn)
        save_btn = QPushButton("保存设置")
        save_btn.setObjectName("Primary")
        save_btn.setStyleSheet(_PRIMARY_BUTTON)
        save_btn.clicked.connect(self._save_prefs)
        action_layout.addWidget(save_btn)
        content_layout.addWidget(actions)
        root.addWidget(content, 1)
        self._switch_page(0)

    def _switch_page(self, index: int):
        self.stack.setCurrentIndex(index)
        for i, button in enumerate(self._nav_buttons):
            button.setChecked(i == index)

    def _scroll_page(self, title: str, subtitle: str):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(_SCROLL)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 20, 22, 24)
        layout.setSpacing(14)
        layout.addWidget(_label(title, "#f0f1f4", 18, True))
        hint = _label(subtitle, "#858a94", 10)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        scroll.setWidget(page)
        return scroll, layout

    # ── AI ────────────────────────────────────────────────
    def _build_ai_page(self):
        scroll, layout = self._scroll_page(
            "AI 引擎", "统一管理脚本、翻译、AI 图片和 AI 视频使用的服务凭据。")

        llm = _card("语言模型", "AI 脚本、翻译和润色")
        form = llm.layout()
        self.llm_combo = QComboBox()
        self.llm_combo.addItem("ModelHub / OpenAI 兼容", "openai")
        self.llm_combo.addItem("DeepSeek 官方", "deepseek")
        self.llm_combo.addItem("自定义兼容接口", "custom_llm")
        self.llm_combo.setStyleSheet(_COMBO)
        self.llm_combo.currentIndexChanged.connect(self._on_llm_mode_change)
        _form_row(form, "服务商", self.llm_combo)
        self.llm_model = _line("模型名，如 gpt-5.5 / deepseek-chat")
        _form_row(form, "模型", self.llm_model)
        self.llm_key = _line("当前语言模型 API Key", password=True)
        _form_row(form, "LLM Key", self.llm_key)
        self.llm_url = _line("留空使用默认地址")
        _form_row(form, "Base URL", self.llm_url)
        self.llm_hint = _label("", "#737782", 9)
        self.llm_hint.setWordWrap(True)
        form.addWidget(self.llm_hint)
        layout.addWidget(llm)

        generation = _card("图片与视频生成", "按实际共用关系配置，避免为同一服务重复填写")
        form = generation.layout()
        self.modelhub_key = _line("ModelHub sk-…", password=True)
        _form_row(form, "ModelHub / OpenAI Key", self.modelhub_key)
        form.addWidget(_note("供 GPT-Image、Veo 使用；也可作为 ModelHub 语言模型 Key。"))
        self.ark_key = _line("火山方舟 ark-…", password=True)
        _form_row(form, "豆包 Ark Key", self.ark_key)
        form.addWidget(_note("Seedream 与 Seedance 共用这一枚 Key。"))
        self.show_keys = QCheckBox("显示密钥")
        self.show_keys.setStyleSheet(_CHECK)
        self.show_keys.toggled.connect(self._toggle_keys)
        form.addWidget(self.show_keys)
        layout.addWidget(generation)

        voice_note = _card("配音服务", "音色、TTS 引擎和配音 Key 在剪辑工作台的「配音」面板统一管理。")
        voice_note.layout().addWidget(_note("这里不重复放置配音参数，避免两边配置不一致。"))
        layout.addWidget(voice_note)
        layout.addStretch()
        return scroll

    # ── 下载 ──────────────────────────────────────────────
    def _build_download_page(self):
        scroll, layout = self._scroll_page(
            "下载与素材", "管理下载、扒取和开放许可音频使用的共享路径。")
        paths = _card("默认下载位置", "下载页与扒取页共用")
        form = paths.layout()
        self.download_dir = _line("选择视频和音频的保存目录")
        browse = QPushButton("选择目录")
        browse.setStyleSheet(_SECONDARY_BUTTON)
        browse.clicked.connect(self._pick_download_dir)
        _form_row_with_button(form, "下载目录", self.download_dir, browse)
        layout.addWidget(paths)

        login = _card("登录态与站点兼容", "YouTube、TikTok、抖音等站点可能需要登录 Cookie")
        form = login.layout()
        self.cookies_file = _line("Netscape cookies.txt（可选）")
        cookie_btn = QPushButton("选择文件")
        cookie_btn.setStyleSheet(_SECONDARY_BUTTON)
        cookie_btn.clicked.connect(self._pick_cookie_file)
        _form_row_with_button(form, "Cookie 文件", self.cookies_file, cookie_btn)
        self.browser_status = _note("浏览器检测：尚未检测")
        form.addWidget(self.browser_status)
        detect_btn = QPushButton("重新检测浏览器")
        detect_btn.setStyleSheet(_SECONDARY_BUTTON)
        detect_btn.clicked.connect(lambda: self._refresh_browser_status(True))
        form.addWidget(detect_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(login)

        music = _card("Openverse 音频素材", "无需 API Key，在剪辑工作台的「音乐音效」页搜索和下载")
        music.layout().addWidget(_note("下载时会自动保存作者、来源与许可证记录。"))
        layout.addWidget(music)
        layout.addStretch()
        return scroll

    # ── 性能 ──────────────────────────────────────────────
    def _build_performance_page(self):
        scroll, layout = self._scroll_page(
            "性能与缓存", "调整剪辑预览、素材缩略图和磁盘缓存策略。")
        performance = _card("预览性能", "改动会影响后续新建的解码器和缩略图")
        form = performance.layout()
        self.spin_buffer = QSpinBox()
        self.spin_buffer.setRange(4, 120)
        self.spin_buffer.setSingleStep(2)
        self.spin_buffer.setStyleSheet(_SPIN)
        _form_row(form, "解码缓冲帧", self.spin_buffer)
        form.addWidget(_note("数值越大播放越稳，但会占用更多内存。推荐 24。"))
        self.spin_thumb = QSpinBox()
        self.spin_thumb.setRange(80, 640)
        self.spin_thumb.setSingleStep(20)
        self.spin_thumb.setSuffix(" px")
        self.spin_thumb.setStyleSheet(_SPIN)
        _form_row(form, "缩略图宽度", self.spin_thumb)
        layout.addWidget(performance)

        cache = _card("缓存管理", "Cache 与 work_temp 的自动清理策略")
        form = cache.layout()
        self.cache_enable = QCheckBox("启用启动时自动清理")
        self.cache_enable.setStyleSheet(_CHECK)
        self.cache_enable.toggled.connect(self._on_cache_toggle)
        form.addWidget(self.cache_enable)
        self.spin_cache = QDoubleSpinBox()
        self.spin_cache.setRange(0, 100)
        self.spin_cache.setSingleStep(0.5)
        self.spin_cache.setDecimals(1)
        self.spin_cache.setSuffix(" GB")
        self.spin_cache.setStyleSheet(_SPIN)
        _form_row(form, "占用阈值", self.spin_cache)
        self.cache_readout = _note("当前占用：计算中…")
        form.addWidget(self.cache_readout)
        layout.addWidget(cache)
        layout.addStretch()
        return scroll

    # ── 关于 ──────────────────────────────────────────────
    def _build_about_page(self):
        scroll, layout = self._scroll_page(
            "关于与路径", "查看配置位置，明确哪些设置应在哪个工作台完成。")
        about = _card("CreativeEnginePro", "面向批量内容生产的剪辑、AI 生成和自动化工作台")
        form = about.layout()
        form.addWidget(_path_line("项目目录", Path.cwd()))
        try:
            from api_config import ENV_PATH
            env_path = ENV_PATH
        except Exception:
            env_path = Path.cwd() / ".env"
        form.addWidget(_path_line("环境配置", env_path))
        form.addWidget(_path_line("缓存目录", Path.cwd() / "work_temp"))
        layout.addWidget(about)

        scope = _card("设置归属", "功能参数就近管理，减少重复和冲突")
        form = scope.layout()
        for text in (
            "尾页模式、尾页文件、自动输出 → 尾页处理页",
            "轮播数量、转场、BGM、AI 风格 → 图片轮播页",
            "配音引擎、音色、TTS Key → 剪辑工作台 / 配音",
            "下载后的自动去向 → 扒取页",
        ):
            form.addWidget(_note("• " + text))
        layout.addWidget(scope)
        layout.addStretch()
        return scroll

    # ── 交互与持久化 ──────────────────────────────────────
    def _on_llm_mode_change(self, *_):
        mode = self.llm_combo.currentData()
        default_url = _DEFAULT_BASE.get(mode, "")
        self.llm_url.setPlaceholderText(
            "必填，如 https://your-server/v1" if mode == "custom_llm"
            else f"可选，默认 {default_url}")
        examples = {
            "openai": "如 gpt-5.5 / gpt-4o-mini",
            "deepseek": "如 deepseek-chat",
            "custom_llm": "自定义服务的模型名",
        }
        self.llm_model.setPlaceholderText(examples.get(mode, "模型名"))
        self.llm_hint.setText("保存后新任务使用新配置；已初始化的 AI 任务建议重启程序。")

    def _toggle_keys(self, visible: bool):
        mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        for widget in (self.llm_key, self.modelhub_key, self.ark_key):
            widget.setEchoMode(mode)

    def _pick_download_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择默认下载目录", self.download_dir.text())
        if path:
            self.download_dir.setText(path)

    def _pick_cookie_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Netscape Cookie 文件", self.cookies_file.text(),
            "Cookie 文件 (*.txt);;所有文件 (*.*)")
        if path:
            self.cookies_file.setText(path)

    def _refresh_browser_status(self, force=False):
        try:
            from core.downloader import get_available_browsers, BROWSER_LABELS
            browsers = get_available_browsers(force_refresh=force)
            names = "、".join(BROWSER_LABELS.get(item, item) for item in browsers)
            self.browser_status.setText(f"检测到：{names}" if names else "未检测到可用浏览器")
        except Exception as exc:
            self.browser_status.setText(f"浏览器检测失败：{str(exc)[:80]}")

    def _on_cache_toggle(self, checked):
        self.spin_cache.setEnabled(checked)
        if not checked:
            self.spin_cache.setValue(0.0)

    def _load_prefs(self):
        from api_config import read_env
        import config

        env = read_env()
        mode = env.get("LLM_MODE", getattr(config, "LLM_MODE", "openai"))
        idx = self.llm_combo.findData(mode)
        self.llm_combo.setCurrentIndex(max(0, idx))
        if mode == "custom_llm":
            self.llm_key.setText(env.get("CUSTOM_LLM_KEY", ""))
            self.llm_model.setText(env.get("CUSTOM_LLM_MODEL", ""))
            self.llm_url.setText(env.get("CUSTOM_LLM_URL", ""))
        else:
            self.llm_key.setText(env.get("LLM_API_KEY", ""))
            self.llm_model.setText(env.get("LLM_MODEL", getattr(config, "LLM_MODEL", "")))
            self.llm_url.setText(env.get("LLM_BASE_URL", ""))
        self.modelhub_key.setText(env.get("OPENAI_API_KEY", ""))
        self.ark_key.setText(env.get("SEEDREAM_API_KEY", ""))

        settings = QSettings("CreativeEnginePro", "DownloadPanel")
        try:
            from core import downloader
            default_download_dir = downloader.DOWNLOAD_DIR
        except Exception:
            default_download_dir = ""
        self.download_dir.setText(str(settings.value("download_dir", default_download_dir)))
        self.cookies_file.setText(str(settings.value("cookies_file", "")))
        self.spin_buffer.setValue(int(getattr(config, "DECODER_BUFFER", 24)))
        self.spin_thumb.setValue(int(getattr(config, "THUMB_SIZE", 320)))
        cache = float(getattr(config, "CACHE_MAX_GB", 0.0) or 0.0)
        self.cache_enable.setChecked(cache > 0)
        self.spin_cache.setValue(cache)
        self.spin_cache.setEnabled(cache > 0)
        self._on_llm_mode_change()
        self._update_cache_readout()
        self._refresh_browser_status()
        self.status_label.setStyleSheet("color:#78c58b;font-size:10px;")
        self.status_label.setText("已重新加载当前配置")

    def _save_prefs(self):
        from api_config import write_env
        import config

        mode = self.llm_combo.currentData()
        key = self.llm_key.text().strip()
        model = self.llm_model.text().strip()
        url = self.llm_url.text().strip()
        download_dir = self.download_dir.text().strip()
        cookie_file = self.cookies_file.text().strip()
        if mode == "custom_llm" and not url:
            self.status_label.setStyleSheet("color:#e6a15c;font-size:10px;")
            self.status_label.setText("自定义 LLM 必须填写 Base URL")
            return
        if download_dir and not os.path.isdir(download_dir):
            self.status_label.setStyleSheet("color:#e6a15c;font-size:10px;")
            self.status_label.setText("下载目录不存在，请重新选择")
            self._switch_page(1)
            return
        if cookie_file and not os.path.isfile(cookie_file):
            self.status_label.setStyleSheet("color:#e6a15c;font-size:10px;")
            self.status_label.setText("Cookie 文件不存在，请重新选择或清空")
            self._switch_page(1)
            return
        if mode == "custom_llm":
            updates = {
                "LLM_MODE": mode, "CUSTOM_LLM_KEY": key,
                "CUSTOM_LLM_MODEL": model, "CUSTOM_LLM_URL": url,
                "LLM_BASE_URL": "",
            }
        else:
            updates = {
                "LLM_MODE": mode, "LLM_API_KEY": key,
                "LLM_MODEL": model, "LLM_BASE_URL": url,
            }
        updates.update({
            "OPENAI_API_KEY": self.modelhub_key.text().strip(),
            "SEEDREAM_API_KEY": self.ark_key.text().strip(),
            "DECODER_BUFFER": str(self.spin_buffer.value()),
            "THUMB_SIZE": str(self.spin_thumb.value()),
            "CACHE_MAX_GB": str(self.spin_cache.value() if self.cache_enable.isChecked() else 0.0),
        })
        write_env(updates)

        for key_name, caster in (
            ("DECODER_BUFFER", int), ("THUMB_SIZE", int), ("CACHE_MAX_GB", float),
            ("OPENAI_API_KEY", str), ("SEEDREAM_API_KEY", str),
            ("LLM_MODE", str),
            ("LLM_MODEL", str), ("LLM_API_KEY", str), ("LLM_BASE_URL", str),
        ):
            if key_name in updates:
                try:
                    setattr(config, key_name, caster(updates[key_name]))
                except Exception:
                    pass

        # config.py 中有一组根据 LLM_MODE 派生的运行时变量；保存后同步重算，
        # 让新建的脚本/翻译任务立即使用刚刚选择的服务。
        if mode == "custom_llm":
            config.CUSTOM_LLM_KEY = key
            config.CUSTOM_LLM_MODEL = model
            config.CUSTOM_LLM_URL = url
            config.LLM_API_KEY = key
            config.LLM_BASE_URL = url
            config.LLM_MODEL_NAME = model or "gpt-3.5-turbo"
        else:
            config.LLM_API_KEY = key
            config.LLM_BASE_URL = url or _DEFAULT_BASE[mode]
            config.LLM_MODEL_NAME = model or (
                "deepseek-chat" if mode == "deepseek" else "gpt-5.5")
        config.LLM_MODEL = model or config.LLM_MODEL_NAME

        settings = QSettings("CreativeEnginePro", "DownloadPanel")
        if download_dir:
            settings.setValue("download_dir", download_dir)
        else:
            settings.remove("download_dir")
        if cookie_file:
            settings.setValue("cookies_file", cookie_file)
        else:
            settings.remove("cookies_file")
        try:
            import core.downloader as downloader
            if download_dir and os.path.isdir(download_dir):
                downloader.DOWNLOAD_DIR = download_dir
            downloader.set_cookies_file(cookie_file if os.path.isfile(cookie_file) else "")
        except Exception:
            pass

        self._update_cache_readout()
        self.status_label.setStyleSheet("color:#78c58b;font-size:10px;")
        self.status_label.setText("设置已保存；AI Key 变更后建议重启程序")
        self.settings_saved.emit({"download_dir": download_dir, "cookies_file": cookie_file})

    def _current_cache_bytes(self):
        total = 0
        for directory in (Path("Cache"), Path("work_temp")):
            if not directory.exists():
                continue
            for item in directory.rglob("*"):
                if item.is_file():
                    try:
                        total += item.stat().st_size
                    except OSError:
                        pass
        return total

    def _update_cache_readout(self):
        size = self._current_cache_bytes() / (1024 ** 3)
        self.cache_readout.setText(f"当前占用：{size:.2f} GB（Cache + work_temp）")


# ── 组件辅助 ──────────────────────────────────────────────
def _label(text, color="#d8dbe2", size=11, bold=False):
    widget = QLabel(text)
    widget.setStyleSheet(
        f"color:{color};font-size:{size}px;" + ("font-weight:600;" if bold else ""))
    return widget


def _note(text):
    widget = _label(text, "#7f8490", 9)
    widget.setWordWrap(True)
    return widget


def _line(placeholder="", password=False):
    widget = QLineEdit()
    widget.setPlaceholderText(placeholder)
    widget.setStyleSheet(_INPUT)
    if password:
        widget.setEchoMode(QLineEdit.EchoMode.Password)
    return widget


def _card(title, subtitle=""):
    card = QFrame()
    card.setStyleSheet(_CARD)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(14, 12, 14, 14)
    layout.setSpacing(9)
    layout.addWidget(_label(title, "#e9eaed", 12, True))
    if subtitle:
        layout.addWidget(_note(subtitle))
    return card


def _form_row(layout, label, widget):
    row = QHBoxLayout()
    row.setSpacing(12)
    text = _label(label, "#abb0ba", 10)
    text.setFixedWidth(138)
    row.addWidget(text)
    row.addWidget(widget, 1)
    layout.addLayout(row)


def _form_row_with_button(layout, label, widget, button):
    row = QHBoxLayout()
    row.setSpacing(8)
    text = _label(label, "#abb0ba", 10)
    text.setFixedWidth(138)
    row.addWidget(text)
    row.addWidget(widget, 1)
    row.addWidget(button)
    layout.addLayout(row)


def _path_line(label, path):
    widget = _label(f"{label}：{Path(path)}", "#9aa0aa", 10)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    widget.setWordWrap(True)
    return widget


_NAV_BUTTON = (
    "QPushButton{text-align:left;padding:9px 10px;border:none;border-radius:6px;"
    "background:transparent;color:#9297a1;font-size:11px;}"
    "QPushButton:hover{background:#25272c;color:#d8dbe2;}"
    "QPushButton:checked{background:#27364d;color:#78aef8;font-weight:600;}"
)
_CARD = "QFrame{background:#292b30;border:1px solid #383b42;border-radius:9px;}"
_INPUT = (
    "QLineEdit{background:#18191c;border:1px solid #3d4048;border-radius:6px;"
    "color:#e1e3e7;padding:7px 9px;font-size:11px;}"
    "QLineEdit:focus{border-color:#4d91f7;}"
)
_COMBO = (
    "QComboBox{background:#18191c;border:1px solid #3d4048;border-radius:6px;"
    "color:#e1e3e7;padding:6px 8px;font-size:11px;}"
    "QComboBox QAbstractItemView{background:#202124;color:#ddd;selection-background-color:#315d9b;}"
)
_SPIN = (
    "QSpinBox,QDoubleSpinBox{background:#18191c;border:1px solid #3d4048;border-radius:6px;"
    "color:#e1e3e7;padding:6px 8px;min-width:90px;}"
)
_CHECK = (
    "QCheckBox{color:#c6c9d0;font-size:10px;spacing:7px;}"
    "QCheckBox::indicator{width:15px;height:15px;background:#18191c;border:1px solid #454952;border-radius:3px;}"
    "QCheckBox::indicator:checked{background:#3d8ef8;border-color:#3d8ef8;}"
)
_PRIMARY_BUTTON = (
    "QPushButton{background:#3d8ef8;color:white;border:none;border-radius:6px;"
    "padding:8px 18px;font-weight:600;}QPushButton:hover{background:#559df7;}"
)
_SECONDARY_BUTTON = (
    "QPushButton{background:#2b2d32;color:#c5c8cf;border:1px solid #41444c;"
    "border-radius:6px;padding:7px 12px;}QPushButton:hover{border-color:#5a94e8;color:white;}"
)
_SCROLL = (
    "QScrollArea{background:#202124;border:none;}"
    "QScrollBar:vertical{background:#202124;width:7px;}"
    "QScrollBar::handle:vertical{background:#454850;border-radius:3px;min-height:30px;}"
)
