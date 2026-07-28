"""
小欢语音 - 内嵌设置面板（侧边栏）
精美分类 · 付费/免费标识 · 引擎 + 语言模型配置
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QFrame, QMessageBox, QScrollArea,
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QPen, QColor
from pathlib import Path
import os, sys
import config


class SettingsPanel(QWidget):
    settings_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # 关键：子 QWidget 带样式表背景时，必须开启 WA_StyledBackground 才能可靠绘制
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # 不再初始 collapse 到 0 宽：QScrollArea(widgetResizable) 在 0 宽下缓存内容尺寸，
        # 之后展开到 300 时常常无法重新布局，导致只剩暗色背景、文字图标全无。
        # 不在此处 setFixedWidth：面板宽度由宿主（voice_workbench._position_settings）控制，
        # 否则固定宽度会覆盖 setGeometry 的宽度设置。
        self._collapsed = False
        self._build()
        self._content.show()

    def _build(self):
        self.setStyleSheet("background:#232323;border:1px solid #3a3a3a;border-radius:10px;")
        L = QVBoxLayout(self); L.setContentsMargins(0,0,0,0); L.setSpacing(0)

        # ═══ 顶部栏 ═══
        top = QWidget(); top.setFixedHeight(40)
        top.setStyleSheet("background:#1a1a1a;border-bottom:1px solid #333333;border-top-left-radius:10px;border-top-right-radius:10px;")
        th = QHBoxLayout(top); th.setContentsMargins(14,0,10,0)
        th.addWidget(_lbl("设置", "#eeeeee", 13, True))
        th.addStretch()
        self.btn_toggle = QPushButton()
        self.btn_toggle.setFixedSize(26, 26)
        self.btn_toggle.setIcon(self._x_icon())
        self.btn_toggle.setIconSize(QSize(14, 14))
        self.btn_toggle.setToolTip("关闭设置")
        self.btn_toggle.setStyleSheet("QPushButton{background:#2a2a2a;border:1px solid #444;border-radius:5px;}QPushButton:hover{background:#3a3a3a;border-color:#ff6b6b;}")
        self.btn_toggle.clicked.connect(self.toggle); th.addWidget(self.btn_toggle)
        L.addWidget(top)

        # ═══ 滚动内容 ═══
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}QScrollBar:vertical{background:#141414;width:4px;border-radius:2px;}QScrollBar::handle:vertical{background:#333;border-radius:2px;}")
        self._content = QWidget(); self._content.setStyleSheet("background:transparent;")
        C = QVBoxLayout(self._content); C.setContentsMargins(16,14,16,18); C.setSpacing(12)

        # ═══ TTS 语音引擎 ═══
        C.addWidget(_section_header("🎙  TTS 语音引擎", "选择配音服务商"))

        # 引擎卡片行
        engines_card = QFrame()
        engines_card.setStyleSheet(_CARD)
        ec = QVBoxLayout(engines_card); ec.setContentsMargins(14,10,14,12); ec.setSpacing(10)

        self._current_engine = "edge"
        self._engine_btns = []
        engs = [
            ("edge", "Edge-TTS", "微软免费语音，支持50+语言和方言", "🆓 免费"),
            ("auto_lang", "🌐 千语种", "自动识别语言·edge/gTTS兜底·80+语种", "🆓 免费"),
            ("siliconflow", "硅基流动", "CosyVoice2 · 8种定制音色 · 2000万token", "🆓 免费"),
            ("deepgram", "Deepgram", "顶级英文音质 · 12种Aura声音", "🆓 免费"),
            ("elevenlabs", "ElevenLabs", "高品质AI配音，21个英语声音", "💎 付费"),
            ("fish_audio", "Fish Audio", "192万社区声音模型，多语言", "💎 付费"),
        ]
        for i, (key, name, desc, badge) in enumerate(engs):
            row = QFrame(); row.setCursor(Qt.CursorShape.PointingHandCursor)
            row.setStyleSheet(_ENG_OFF)
            rl = QHBoxLayout(row); rl.setContentsMargins(10,8,10,8); rl.setSpacing(8)

            left = QVBoxLayout(); left.setSpacing(1)
            top_row = QHBoxLayout(); top_row.setSpacing(6)
            top_row.addWidget(_lbl(name, "#ddd", 12, True))
            b = _lbl(badge, "#4caf50" if "免费" in badge else "#f0ad4e", 9, True)
            b.setStyleSheet(f"QLabel{{background:{'#1a3a1a' if '免费' in badge else '#3a2a1a'};color:{'#4caf50' if '免费' in badge else '#f0ad4e'};border-radius:3px;padding:1px 6px;font-size:9px;}}")
            top_row.addWidget(b); top_row.addStretch()
            left.addLayout(top_row)
            left.addWidget(_lbl(desc, "#9a9a9a", 10))
            rl.addLayout(left, 1)

            self._engine_btns.append((key, row))
            row.mousePressEvent = lambda ev, k=key: self._select_engine(k)
            ec.addWidget(row)

        C.addWidget(engines_card)

        # 当前引擎提示
        self.hint_engine = _lbl("", "#555", 10); self.hint_engine.setWordWrap(True)
        C.addWidget(self.hint_engine)

        # ═══ API 密钥配置 ═══
        C.addWidget(_section_header("🔑  API 密钥", "配置当前引擎的访问密钥"))

        keys_card = QFrame(); keys_card.setStyleSheet(_CARD)
        kc = QVBoxLayout(keys_card); kc.setContentsMargins(14,10,14,14); kc.setSpacing(10)

        self.eleven_frame = self._key_row("ElevenLabs Key", "sk_...")
        kc.addWidget(self.eleven_frame)
        self.edit_eleven_key = self._edt_from(self.eleven_frame)

        self.fish_frame = self._key_row("Fish Audio Key", "fish API key")
        kc.addWidget(self.fish_frame)
        self.edit_fish_key = self._edt_from(self.fish_frame)

        self.sf_frame = self._key_row("硅基流动 Key", "sk-...")
        kc.addWidget(self.sf_frame)
        self.edit_sf_key = self._edt_from(self.sf_frame)

        self.dg_frame = self._key_row("Deepgram Key", "Deepgram API key")
        kc.addWidget(self.dg_frame)
        self.edit_dg_key = self._edt_from(self.dg_frame)

        kc.addStretch()
        C.addWidget(keys_card)

        # ═══ 语言模型 ═══
        C.addWidget(_section_header("🧠  语言模型", "翻译·润色·脚本生成"))
        llm_card = QFrame(); llm_card.setStyleSheet(_CARD)
        lc = QVBoxLayout(llm_card); lc.setContentsMargins(14,10,14,14); lc.setSpacing(8)
        lc.addWidget(_lbl("DeepSeek-V3", "#eeeeee", 12, True))
        lc.addWidget(_lbl("高性能中文大模型，润色/翻译/脚本均用此模型", "#9a9a9a", 10))
        self.llm_key_frame = self._key_row("API Key", "sk-...")
        lc.addWidget(self.llm_key_frame)
        self.edit_openai_key = self._edt_from(self.llm_key_frame)
        C.addWidget(llm_card)

        # ═══ 保存 ═══
        btn = QPushButton("💾  保存设置")
        btn.setFixedHeight(38); btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet("QPushButton{background:#3d8ef8;color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:bold;}QPushButton:hover{background:#4a9df9;}QPushButton:pressed{background:#2d7ee8;}")
        btn.clicked.connect(self._save); C.addWidget(btn)
        C.addStretch()
        scroll.setWidget(self._content)
        L.addWidget(scroll, 1)
        self._load()

    def _key_row(self, label, placeholder):
        f = QFrame()
        fl = QVBoxLayout(f); fl.setContentsMargins(0,0,0,0); fl.setSpacing(4)
        fl.addWidget(_lbl(label, "#999", 10))
        e = QLineEdit(); e.setPlaceholderText(placeholder)
        e.setEchoMode(QLineEdit.EchoMode.Password); e.setStyleSheet(_INPUT)
        fl.addWidget(e)
        f.setVisible(False)
        return f

    def _edt_from(self, frm):
        for c in frm.findChildren(QLineEdit): return c
        return None

    def _select_engine(self, key: str):
        """点击引擎卡片切换（自动保存）"""
        self._current_engine = key
        for k, row in self._engine_btns:
            row.setStyleSheet(_ENG_ON if k == key else _ENG_OFF)
        self._on_engine_change(key)
        self._save(silent=True)  # 切换即保存

    def _on_engine_change(self, key: str = ""):
        hints = {
            "edge": "Microsoft Edge-TTS · 完全免费 · 50+语言 · 中文方言支持",
            "auto_lang": "千语种模式 · 自动检测语言 · edge-tts优先 + gTTS兜底",
            "siliconflow": "硅基流动 CosyVoice2 · 8种音色 · 新用户2000万token",
            "deepgram": "Deepgram Aura · 12种声音 · 顶级英文音质",
            "elevenlabs": "ElevenLabs · 付费按量 · 21个高质量英语声音",
            "fish_audio": "Fish Audio · 付费按量 · 192万社区声音模型",
        }
        self.eleven_frame.setVisible(key in ("elevenlabs",))
        self.fish_frame.setVisible(key == "fish_audio")
        self.sf_frame.setVisible(key == "siliconflow")
        self.dg_frame.setVisible(key == "deepgram")
        self.hint_engine.setText(hints.get(key, ""))

    def toggle(self):
        self._collapsed = not self._collapsed
        if self._collapsed: self._collapse()
        else: self._expand()

    def _x_icon(self):
        """用 QPainter 画一个关闭「×」图标，完全不依赖字体字形（避免豆腐块）。"""
        size = 26
        pm = QPixmap(size, size)
        pm.fill(QColor(0, 0, 0, 0))  # 透明底
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#dddddd"))
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        m = 8  # 边距
        p.drawLine(m, m, size - m, size - m)
        p.drawLine(size - m, m, m, size - m)
        p.end()
        return QIcon(pm)

    def _collapse(self): self._content.hide()
    def _expand(self): self._content.show()

    def _env_path(self):
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            return Path(sys.executable).parent / ".env"
        return Path(__file__).parent.parent / ".env"

    def _save(self, silent=False):
        ep = self._env_path()
        # 收集要写入的键值
        updates = {"TTS_ENGINE": self._current_engine}
        if v := self.edit_eleven_key.text().strip(): updates["ELEVENLABS_API_KEY"] = v
        if v := self.edit_fish_key.text().strip(): updates["FISH_AUDIO_KEY"] = v
        if v := self.edit_sf_key.text().strip(): updates["SILICONFLOW_KEY"] = v
        if v := self.edit_dg_key.text().strip(): updates["DEEPGRAM_KEY"] = v
        if v := self.edit_openai_key.text().strip(): updates["OPENAI_API_KEY"] = v

        # 增量更新：保留注释、空白行、不相关的配置项，只更新/追加我们管理的 key
        managed = set(updates.keys())
        lines_out = []
        seen = set()
        if ep.exists():
            for ln in ep.read_text(encoding="utf-8").splitlines():
                stripped = ln.strip()
                if not stripped or stripped.startswith("#"):
                    lines_out.append(ln)
                    continue
                if "=" in stripped:
                    k = stripped.split("=", 1)[0].strip()
                    seen.add(k)
                    if k in managed:
                        lines_out.append(f'{k}="{updates[k]}"')
                    else:
                        lines_out.append(ln)
                else:
                    lines_out.append(ln)
        # 追加未出现过的管理键
        for k in managed - seen:
            lines_out.append(f'{k}="{updates[k]}"')
        ep.write_text("\n".join(lines_out) + "\n", encoding="utf-8")

        # 更新运行时环境变量，并同步已加载的 config 模块，避免重启才生效
        for k, v in updates.items():
            os.environ[k] = v
            if hasattr(config, k):
                setattr(config, k, v)
        # LLM 派生配置同步
        if "OPENAI_API_KEY" in updates:
            if config.LLM_MODE == "custom_llm" and getattr(config, "CUSTOM_LLM_KEY", ""):
                config.LLM_API_KEY = config.CUSTOM_LLM_KEY
                config.LLM_BASE_URL = getattr(config, "CUSTOM_LLM_URL", "") or "https://api.openai.com/v1"
                config.LLM_MODEL_NAME = getattr(config, "CUSTOM_LLM_MODEL", "") or "gpt-3.5-turbo"
            else:
                config.LLM_API_KEY = updates["OPENAI_API_KEY"]
                config.LLM_BASE_URL = config.OPENAI_BASE_URL
                config.LLM_MODEL_NAME = getattr(config, "LLM_MODEL", os.getenv("LLM_MODEL", "deepseek-chat"))
        self.settings_changed.emit()
        if not silent:
            QMessageBox.information(self, "已保存", "设置已保存，引擎已切换")
            self.hide()   # 点击“保存设置”后自动关闭面板

    def _load(self):
        ep = self._env_path()
        if not ep.exists(): return
        cfg = {}
        for ln in ep.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if "=" in ln and not ln.startswith("#"):
                k, _, v = ln.partition("="); cfg[k.strip()] = v.strip().strip('"').strip("'")
        eng = cfg.get("TTS_ENGINE", "edge")
        self._select_engine(eng)
        if v := cfg.get("ELEVENLABS_API_KEY"): self.edit_eleven_key.setText(v)
        if v := cfg.get("FISH_AUDIO_KEY"): self.edit_fish_key.setText(v)
        if v := cfg.get("SILICONFLOW_KEY"): self.edit_sf_key.setText(v)
        if v := cfg.get("DEEPGRAM_KEY"): self.edit_dg_key.setText(v)
        if v := cfg.get("OPENAI_API_KEY"): self.edit_openai_key.setText(v)


# ═══ 样式 ═══
def _lbl(text, color="#ccc", size=11, bold=False):
    l = QLabel(text)
    l.setStyleSheet(f"color:{color};font-size:{size}px;{'font-weight:bold;' if bold else ''}")
    return l

def _section_header(icon_text, desc):
    f = QFrame()
    fl = QVBoxLayout(f); fl.setContentsMargins(0,0,0,0); fl.setSpacing(2)
    fl.addWidget(_lbl(icon_text, "#5aa6ff", 12, True))
    fl.addWidget(_lbl(desc, "#8a8a8a", 10))
    return f

_CARD = "QFrame{background:#2a2a2a;border:1px solid #3a3a3a;border-radius:8px;}"
_INPUT = (
    "QLineEdit{background:#141414;border:1px solid #3a3a3a;border-radius:6px;"
    "color:#dddddd;font-size:12px;padding:7px 10px;}"
    "QLineEdit:focus{border-color:#3d8ef8;}"
)
_ENG_OFF = "QFrame{background:#262626;border:1px solid #333;border-radius:6px;}QFrame:hover{background:#303030;}"
_ENG_ON  = "QFrame{background:#1a2a4a;border:1px solid #3d8ef8;border-radius:6px;}"
