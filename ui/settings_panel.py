"""CreativeEnginePro 全局设置中心。

全局设置只管理跨工作台共享的配置：AI 凭据、下载环境、性能和程序路径。
尾页、轮播、配音等业务预设继续在对应工作台维护，避免重复配置源。
"""
from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, pyqtSignal, QSettings
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QFrame, QScrollArea, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox,
    QStackedWidget, QFileDialog,
)

from ui.widgets.workbench_controls import workbench_icon
from utils.app_paths import resource_root


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
        self.setObjectName("settingsPanel")
        self.setStyleSheet(_STYLE)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("settingsSidebar")
        sidebar.setFixedWidth(196)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 21, 14, 16)
        side.setSpacing(7)
        brand = QHBoxLayout()
        brand.setContentsMargins(3, 0, 0, 0)
        brand.setSpacing(9)
        mark = _label("", "settingsBrandIcon")
        mark.setFixedSize(34, 34)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo = QPixmap(str(resource_root() / "assets" / "icon.png"))
        mark.setPixmap(logo.scaled(
            24, 24, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        brand.addWidget(mark)
        brand.addWidget(_label("设置中心", "settingsBrand"), 1)
        side.addLayout(brand)
        side.addSpacing(15)
        side.addWidget(_label("全局设置", "settingsNavCaption"))

        self.stack = QStackedWidget()
        pages = [
            ("AI 引擎", "sparkles", self._build_ai_page()),
            ("下载与素材", "download", self._build_download_page()),
            ("性能与缓存", "sliders-horizontal", self._build_performance_page()),
            ("关于与路径", "folder-open", self._build_about_page()),
        ]
        for index, (text, icon, page) in enumerate(pages):
            button = QPushButton(text)
            button.setObjectName("settingsNav")
            button.setIcon(workbench_icon(icon, "#bfb2fa"))
            button.setIconSize(QSize(17, 17))
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, i=index: self._switch_page(i))
            self._nav_buttons.append(button)
            side.addWidget(button)
            self.stack.addWidget(page)
        side.addStretch()
        side.addWidget(_label("业务预设请在对应工作台设置", "settingsSidebarHint", wrap=True))
        root.addWidget(sidebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self.stack, 1)

        actions = QFrame()
        actions.setObjectName("settingsActions")
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(23, 12, 23, 12)
        action_layout.setSpacing(8)
        self.status_label = _label("", "settingsStatus", wrap=True)
        action_layout.addWidget(self.status_label, 1)
        reload_btn = QPushButton("重新加载")
        reload_btn.clicked.connect(self._load_prefs)
        action_layout.addWidget(reload_btn)
        save_btn = QPushButton("保存设置")
        save_btn.setObjectName("settingsSave")
        save_btn.clicked.connect(self._save_prefs)
        action_layout.addWidget(save_btn)
        for button in (reload_btn, save_btn):
            button.setAutoDefault(False)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        content_layout.addWidget(actions)
        root.addWidget(content, 1)
        self._switch_page(0)

    def _switch_page(self, index: int):
        self.stack.setCurrentIndex(index)
        for i, button in enumerate(self._nav_buttons):
            button.setChecked(i == index)

    def _scroll_page(self, title: str, subtitle: str):
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)
        header = QFrame()
        header.setObjectName("settingsHeader")
        heading = QVBoxLayout(header)
        heading.setContentsMargins(24, 21, 24, 15)
        heading.setSpacing(5)
        heading.addWidget(_label(title, "settingsPageTitle"))
        heading.addWidget(_note(subtitle))
        panel_layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 12, 24, 20)
        layout.setSpacing(12)
        scroll.setWidget(page)
        panel_layout.addWidget(scroll, 1)
        return panel, layout

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
        self.llm_combo.currentIndexChanged.connect(self._on_llm_mode_change)
        _form_row(form, "服务商", self.llm_combo)
        self.llm_model = _line("模型名，如 gpt-5.5 / deepseek-chat")
        _form_row(form, "模型", self.llm_model)
        self.llm_key = _line("当前语言模型 API Key", password=True)
        _form_row(form, "LLM Key", self.llm_key)
        self.llm_url = _line("留空使用默认地址")
        _form_row(form, "Base URL", self.llm_url)
        self.llm_hint = _note("")
        form.addWidget(self.llm_hint)
        layout.addWidget(llm)

        generation = _card("图片与视频生成")
        form = generation.layout()
        self.modelhub_key = _line("ModelHub sk-…", password=True)
        _form_row(form, "ModelHub / OpenAI Key", self.modelhub_key)
        form.addWidget(_note("供 GPT-Image、Veo 使用；也可作为 ModelHub 语言模型 Key。"))
        self.ark_key = _line("火山方舟 ark-…", password=True)
        _form_row(form, "豆包 Ark Key", self.ark_key)
        form.addWidget(_note("Seedream 与 Seedance 共用这一枚 Key。"))
        self.show_keys = QCheckBox("显示密钥")
        self.show_keys.toggled.connect(self._toggle_keys)
        form.addWidget(self.show_keys)
        layout.addWidget(generation)

        layout.addWidget(_label(
            "配音服务 · 在剪辑工作台的「配音」面板管理音色、引擎和密钥。",
            "settingsCallout", wrap=True))
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
        browse.clicked.connect(self._pick_download_dir)
        _form_row(form, "下载目录", self.download_dir, browse)
        layout.addWidget(paths)

        login = _card("登录态与站点兼容", "YouTube、TikTok、抖音等站点可能需要登录 Cookie")
        form = login.layout()
        self.cookies_file = _line("Netscape cookies.txt（可选）")
        cookie_btn = QPushButton("选择文件")
        cookie_btn.clicked.connect(self._pick_cookie_file)
        _form_row(form, "Cookie 文件", self.cookies_file, cookie_btn)
        self.browser_status = _note("浏览器检测：尚未检测")
        form.addWidget(self.browser_status)
        detect_btn = QPushButton("重新检测浏览器")
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
        _form_row(form, "解码缓冲帧", self.spin_buffer)
        form.addWidget(_note("数值越大播放越稳，但会占用更多内存。推荐 24。"))
        self.spin_thumb = QSpinBox()
        self.spin_thumb.setRange(80, 640)
        self.spin_thumb.setSingleStep(20)
        self.spin_thumb.setSuffix(" px")
        _form_row(form, "缩略图宽度", self.spin_thumb)
        layout.addWidget(performance)

        cache = _card("缓存管理", "Cache 与 work_temp 的自动清理策略")
        form = cache.layout()
        self.cache_enable = QCheckBox("启用启动时自动清理")
        self.cache_enable.toggled.connect(self._on_cache_toggle)
        form.addWidget(self.cache_enable)
        self.spin_cache = QDoubleSpinBox()
        self.spin_cache.setRange(0, 100)
        self.spin_cache.setSingleStep(0.5)
        self.spin_cache.setDecimals(1)
        self.spin_cache.setSuffix(" GB")
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
        about = _card("小欢ovo", "面向批量内容创作的剪辑、AI 生成和自动化工作台")
        form = about.layout()
        from api_config import ENV_PATH
        from utils.app_paths import install_root, work_root
        form.addWidget(_path_line("安装目录", install_root()))
        env_path = ENV_PATH
        form.addWidget(_path_line("环境配置", env_path))
        form.addWidget(_path_line("缓存目录", work_root()))
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

    def _set_status(self, message: str, *, warning: bool = False):
        self.status_label.setProperty("warning", warning)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.status_label.setText(message)

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
        self._set_status("已加载当前设置")

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
            self._set_status("自定义 LLM 必须填写 Base URL", warning=True)
            return
        if download_dir and not os.path.isdir(download_dir):
            self._set_status("下载目录不存在，请重新选择", warning=True)
            self._switch_page(1)
            return
        if cookie_file and not os.path.isfile(cookie_file):
            self._set_status("Cookie 文件不存在，请重新选择或清空", warning=True)
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
        self._set_status("设置已保存。密钥变更后建议重启程序。")
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
def _label(text, name, *, wrap=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def _note(text):
    return _label(text, "settingsNote", wrap=True)


def _line(placeholder="", password=False):
    widget = QLineEdit()
    widget.setPlaceholderText(placeholder)
    if password:
        widget.setEchoMode(QLineEdit.EchoMode.Password)
    return widget


def _card(title, subtitle=""):
    card = QFrame()
    card.setObjectName("settingsCard")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(15, 13, 15, 14)
    layout.setSpacing(8)
    layout.addWidget(_label(title, "settingsCardTitle"))
    if subtitle:
        layout.addWidget(_note(subtitle))
    return card


def _form_row(layout, label, widget, button=None):
    row = QHBoxLayout()
    row.setSpacing(10)
    text = _label(label, "settingsField", wrap=True)
    text.setFixedWidth(138)
    text.setBuddy(widget)
    row.addWidget(text)
    row.addWidget(widget, 1)
    if button is not None:
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        row.addWidget(button)
    layout.addLayout(row)


def _path_line(label, path):
    widget = _label(f"{label}：{Path(path)}", "settingsField", wrap=True)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


_STYLE = """
    QWidget#settingsPanel { background: #0d111c; }
    #settingsPanel QWidget { background: transparent; color: #e9edf8; }
    #settingsPanel QFrame#settingsSidebar { background: #111625; border-right: 1px solid #283149; }
    #settingsPanel QLabel#settingsBrandIcon { background: #eae9ff; border-radius: 9px; }
    #settingsPanel QLabel#settingsBrand { color: #f5f5ff; font-size: 17px; font-weight: 700; }
    #settingsPanel QLabel#settingsNavCaption { color: #a1adc5; padding: 0 8px; font-size: 11px; }
    #settingsPanel QLabel#settingsSidebarHint {
        color: #aab5ce; background: #192033; border: 1px solid #2b354d;
        border-radius: 9px; padding: 9px; font-size: 11px;
    }
    #settingsPanel QFrame#settingsHeader { border-bottom: 1px solid #232c40; }
    #settingsPanel QLabel#settingsPageTitle { color: #f6f7ff; font-size: 26px; font-weight: 700; }
    #settingsPanel QFrame#settingsCard { background: #151b2a; border: 1px solid #2b354c; border-radius: 12px; }
    #settingsPanel QLabel#settingsCardTitle { color: #f0efff; font-size: 15px; font-weight: 700; }
    #settingsPanel QLabel#settingsNote { color: #a0adc5; font-size: 12px; }
    #settingsPanel QLabel#settingsCallout {
        background: #191d30; color: #bbc4dc; border: 1px solid #323b55;
        border-radius: 9px; padding: 10px 13px; font-size: 12px;
    }
    #settingsPanel QLabel#settingsField { color: #d1d9ec; font-size: 12px; }
    #settingsPanel QLineEdit, #settingsPanel QComboBox,
    #settingsPanel QSpinBox, #settingsPanel QDoubleSpinBox {
        background: #101625; color: #f0f2ff; border: 1px solid #3b4560;
        border-radius: 8px; padding: 0 10px; min-height: 32px; font-size: 13px;
        selection-background-color: #6759a7; selection-color: #fff;
    }
    #settingsPanel QLineEdit:focus, #settingsPanel QComboBox:focus,
    #settingsPanel QSpinBox:focus, #settingsPanel QDoubleSpinBox:focus { border-color: #ab98f8; }
    #settingsPanel QLineEdit:disabled, #settingsPanel QSpinBox:disabled,
    #settingsPanel QDoubleSpinBox:disabled { color: #8590a9; border-color: #30384c; }
    #settingsPanel QSpinBox QLineEdit, #settingsPanel QDoubleSpinBox QLineEdit {
        background: transparent; border: none; padding: 0; min-height: 0;
    }
    #settingsPanel QComboBox { padding-right: 34px; }
    #settingsPanel QComboBox::drop-down { width: 30px; border: none; }
    #settingsPanel QComboBox::down-arrow { image: url("__CHEVRON__"); width: 12px; height: 12px; }
    #settingsPanel QComboBox QAbstractItemView {
        background: #1a2235; color: #e9edf8; border: 1px solid #495573;
        selection-background-color: #393057; selection-color: #fff; outline: 0;
    }
    #settingsPanel QCheckBox { color: #dce3f6; spacing: 7px; font-size: 12px; }
    #settingsPanel QScrollArea { border: none; padding: 0; }
    #settingsPanel QScrollBar:vertical { width: 8px; background: #111625; margin: 0; }
    #settingsPanel QScrollBar::handle:vertical {
        background: #414c69; border-radius: 4px; min-height: 28px;
    }
    #settingsPanel QScrollBar::add-line:vertical, #settingsPanel QScrollBar::sub-line:vertical { height: 0; }
    #settingsPanel QScrollBar::add-page:vertical, #settingsPanel QScrollBar::sub-page:vertical {
        background: transparent;
    }
    #settingsPanel QPushButton {
        background: #20283b; color: #dce3f6; border: 1px solid #414d69;
        border-radius: 8px; min-height: 34px; padding: 0 12px; font-size: 12px; font-weight: 600;
    }
    #settingsPanel QPushButton:hover { background: #2b3551; border-color: #6c7ba3; }
    #settingsPanel QPushButton:pressed { background: #161e31; }
    #settingsPanel QPushButton:focus { border-color: #bcadff; }
    #settingsPanel QPushButton:disabled { color: #8590a9; border-color: #30384c; }
    #settingsPanel QPushButton#settingsNav {
        text-align: left; background: transparent; border: 1px solid transparent;
        padding: 0 10px; min-height: 39px; font-size: 13px;
    }
    #settingsPanel QPushButton#settingsNav:hover { background: #20283d; }
    #settingsPanel QPushButton#settingsNav:checked {
        color: #f2ebff; background: #2c2547; border-color: #5c4c83;
    }
    #settingsPanel QPushButton#settingsNav:focus { border-color: #bcadff; }
    #settingsPanel QFrame#settingsActions { background: #111625; border-top: 1px solid #293249; }
    #settingsPanel QLabel#settingsStatus { color: #a3dbbf; font-size: 12px; }
    #settingsPanel QLabel#settingsStatus[warning="true"] { color: #f3c792; }
    #settingsPanel QPushButton#settingsSave {
        background: #b6a6ff; color: #211938; border-color: #c5b8ff; padding: 0 17px; font-weight: 700;
    }
    #settingsPanel QPushButton#settingsSave:hover { background: #c8bcff; }
    #settingsPanel QPushButton#settingsSave:pressed { background: #a294e8; }
    #settingsPanel QPushButton#settingsSave:focus { border-color: #f2eaff; }
""".replace("__CHEVRON__", (resource_root() / "assets" / "batch-combo-chevron.svg").as_posix())
