"""
dubbing_panel.py — 剪辑工作台属性区的「配音」面板

定位：只做「语音配音」本身。
- 多引擎 TTS 配音：Edge-TTS / 千语种 / 硅基流动 / Deepgram / ElevenLabs / Fish Audio
- 配音引擎与 API Key 配置收进可折叠的小按钮（点开才显示）
- 语音选择（Edge 默认音色下拉 + 联网刷新；其它引擎用 Voice ID 输入）
- 语速 / 音量 调节
- 生成 / 停止 + 进度 + 状态 + 试听（生成完成后自动试听）

文本来自选中的字幕 clip（无需在面板里输入）；AI 润色已移至轨道上字幕 clip 的
右键菜单。生成完成后通过 add_audio_cb(path, duration, timeline_start) 直接落到音频轨
（对齐字幕起点）并在素材库显示（由宿主 editor_tab 的回调统一处理）。

仅在 ClipPropertiesPanel 选中字幕 clip 时显示此面板。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QDoubleSpinBox, QPushButton, QFrame,
    QScrollArea, QProgressBar,
)
from PyQt6.QtCore import pyqtSignal, Qt, QThread, QUrl
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtGui import QDesktopServices

from ui.voice_picker import VoiceSelectButton, preload_voices


# ── 引擎定义（与 tts_factory.TTSEngineType / SettingsPanel 一致）──
ENGINES = [
    ("edge", "Edge-TTS", "微软免费语音，支持 50+ 语言和方言", "免费"),
    ("auto_lang", "千语种", "自动识别语言 · edge/gTTS 兜底 · 80+ 语种", "免费"),
    ("siliconflow", "硅基流动", "CosyVoice2 · 8 种定制音色", "免费"),
    ("deepgram", "Deepgram", "顶级英文音质 · 12 种 Aura 声音", "付费"),
    ("elevenlabs", "ElevenLabs", "高品质 AI 配音，英语声音", "付费"),
    ("fish_audio", "Fish Audio", "192 万社区声音模型，多语言", "付费"),
]

# 各引擎所需的 API Key（统一从 api_config 的 tts 条目派生；None 表示无需 Key）
# 付费引擎在 api_config 里标记了 tts_engine；新增付费 TTS 只需在 api_config 加一条。
from api_config import by_category as _by_category
_ENGINE_KEY_MAP = {
    a.tts_engine: (a.env_key, a.label, a.placeholder or "sk-...")
    for a in _by_category("tts") if a.tts_engine
}
ENGINE_KEYS = {
    "edge": None,
    "auto_lang": None,
    **_ENGINE_KEY_MAP,
}

# Edge-TTS 常用音色
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

# 各引擎默认音色（首次切到该引擎时按钮显示）
ENGINE_DEFAULT_VOICE = {
    "edge": ("zh-CN-XiaoxiaoNeural", "晓晓"),
    "siliconflow": ("alex", "沉稳男声"),
    "deepgram": ("aura-asteria-en", "Asteria（知性女声）"),
}

ENGINE_NAME = {k: n for k, n, _, _ in ENGINES}
ENGINE_KEYS_SET = {k for k, *_ in ENGINES}

# ── 样式 ──
GROUP_STYLE = """
    QGroupBox { color:#888; font-size:11px; border:1px solid #333;
        border-radius:4px; margin-top:8px; padding-top:4px; }
    QGroupBox::title { subcontrol-origin:margin; left:8px; }
"""
INPUT_STYLE = ("QLineEdit { background:#2a2a2a; color:#ccc; border:1px solid #444;"
               "border-radius:3px; padding:3px 6px; font-size:12px; }"
               "QLineEdit:focus { border-color:#3d8ef8; }")
COMBO_STYLE = ("QComboBox { background:#2a2a2a; color:#ccc; border:1px solid #444;"
               "border-radius:3px; padding:3px 6px; font-size:12px; }"
               "QComboBox QAbstractItemView { background:#2a2a2a; color:#ccc;"
               "selection-background-color:#3d8ef8; }")
ENG_OFF = "QFrame{background:#262626;border:1px solid #333;border-radius:6px;}"
ENG_ON = "QFrame{background:#1a2a4a;border:1px solid #3d8ef8;border-radius:6px;}"
PRIMARY = ("QPushButton { background:#3d8ef8; color:#fff; border:none; border-radius:5px;"
           "font-size:12px; font-weight:bold; padding:6px 10px; }"
           "QPushButton:hover { background:#4a9df9; }"
           "QPushButton:disabled { background:#2a2a2a; color:#666; }")
SECONDARY = ("QPushButton { background:#2a2a2a; color:#ccc; border:1px solid #444;"
             "border-radius:5px; font-size:12px; padding:6px 8px; "
             "text-align:left; }"
             "QPushButton:hover { border-color:#3d8ef8; color:#fff; }"
             "QPushButton:disabled { color:#666; }")


def _audio_duration(path: str) -> float:
    """获取音频时长（秒），失败返回 0。"""
    try:
        from core.tts_edge import EdgeTTSEngine
        return EdgeTTSEngine.get_audio_duration(Path(path)) or 0.0
    except Exception:
        return 0.0


class _PolishThread(QThread):
    """后台调用 LLM 润色文本（避免阻塞 UI）。供字幕右键菜单复用。"""
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, text: str, mode: str = "改写润色"):
        super().__init__()
        self._text = text
        self._mode = mode

    def run(self):
        try:
            from ai.service import get_ai_manager
            from ai import TaskRequest
            mgr = get_ai_manager()
            chat = mgr.registry.by_capability("chat")
            if not chat:
                self.failed.emit("未配置 LLM API Key（请在设置中配置 OPENAI_API_KEY / LLM_API_KEY）")
                return
            prompts = {
                "改写润色": "你是一位中文文案润色师。在不改变原意的前提下，让文本更流畅、专业、口语化，适合配音朗读。只输出改写后的文本。",
                "口语化": "你是一位短视频配音文案专家。请将以下文案调整句式，让节奏更适合配音朗读，更口语化。只输出调整后的文本。",
            }
            system = prompts.get(self._mode, prompts["改写润色"])
            req = TaskRequest(operation="chat",
                              inputs={"messages": [
                                  {"role": "system", "content": system},
                                  {"role": "user", "content": self._text},
                              ]})
            h = mgr.submit(chat[0].name, req)
            timeout = 60
            waited = 0
            while not h.is_finished and waited < timeout:
                time.sleep(0.3)
                waited += 0.3
            if h.is_success and h.result:
                self.done.emit(str(h.result.data))
            else:
                err = h.result.error if h.result else "超时"
                self.failed.emit(str(err))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class DubbingPanel(QWidget):
    """剪辑工作台属性区的「配音」面板（仅语音配音）。"""

    def __init__(self, parent=None, add_audio_cb=None, get_subtitles_cb=None):
        super().__init__(parent)
        self._add_audio_cb = add_audio_cb      # callable(path, duration, timeline_start)
        self._get_subtitles_cb = get_subtitles_cb  # 返回当前选中的多条字幕 clip（批量配音）
        self._subtitle = None
        self._subtitle_start = 0.0
        self._subtitle_end = None
        self._current_engine = "edge"
        self._worker = None
        self._last_path = None
        self._voice_by_engine = {}   # {engine: (voice_id, name)} 记住各引擎选过的声音
        # 批量生成队列状态
        self._gen_queue = []         # [(clip, text, start, end), ...]
        self._gen_idx = 0
        self._gen_total = 0
        self._gen_cancelled = False
        self._gen_first_path = None

        self._player = QMediaPlayer()
        self._audio_out = QAudioOutput()
        self._player.setAudioOutput(self._audio_out)

        self.setStyleSheet("background:#1e1e1e;")
        self._build_ui()
        self._load_engine_settings()
        self._apply_engine(self._current_engine, silent=True)
        self._engine_box.setVisible(False)
        self._engine_toggle.setText("配音引擎设置 ▼")

    # ── UI 构建 ──
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 可滚动主区
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;}")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll = scroll
        content = QWidget()
        content.setStyleSheet("background:#1e1e1e;")
        cv = QVBoxLayout(content)
        cv.setContentsMargins(8, 8, 8, 8)
        cv.setSpacing(8)
        self._cv = cv

        # ── 引擎设置（折叠）──
        self._engine_toggle = QPushButton("配音引擎设置 ▼")
        self._engine_toggle.setStyleSheet(SECONDARY)
        self._engine_toggle.clicked.connect(self._toggle_engine_box)
        cv.addWidget(self._engine_toggle)

        self._engine_box = QWidget()
        self._engine_box.setStyleSheet("QWidget{background:#232323;border:1px solid #3a3a3a;border-radius:8px;}")
        self._build_engine_box(self._engine_box)
        cv.addWidget(self._engine_box)

        # ── 语音选择 ──
        grp_v = QFrame()
        grp_v.setStyleSheet(GROUP_STYLE)
        gv = QVBoxLayout(grp_v)
        gv.setContentsMargins(8, 14, 8, 8)
        gv.setSpacing(6)
        gv.addWidget(self._lbl("音色", "#888", 11))
        # 与语音配音一致的声音选择按钮：点开弹出卡片列表，可分类/收藏/试听
        self._voice_btn = VoiceSelectButton()
        self._voice_btn.voice_changed.connect(self._on_voice_changed)
        gv.addWidget(self._voice_btn)
        self._voice_hint = self._lbl("", "#777", 10)
        self._voice_hint.setWordWrap(True)
        gv.addWidget(self._voice_hint)
        cv.addWidget(grp_v)

        # ── 语速 / 音量 ──
        grp_r = QFrame()
        grp_r.setStyleSheet(GROUP_STYLE)
        gr = QVBoxLayout(grp_r)
        gr.setContentsMargins(8, 14, 8, 8)
        gr.setSpacing(8)
        hr = QHBoxLayout()
        hr.addWidget(self._lbl("语速", "#888", 11))
        self._speed = QDoubleSpinBox()
        self._speed.setRange(0.5, 2.0)
        self._speed.setSingleStep(0.05)
        self._speed.setValue(1.0)
        self._speed.setSuffix("x")
        self._speed.setStyleSheet(INPUT_STYLE)
        hr.addWidget(self._speed)
        gr.addLayout(hr)
        hvol = QHBoxLayout()
        hvol.addWidget(self._lbl("音量", "#888", 11))
        self._volume = QDoubleSpinBox()
        self._volume.setRange(0.1, 2.0)
        self._volume.setSingleStep(0.05)
        self._volume.setValue(1.0)
        self._volume.setSuffix("x")
        self._volume.setStyleSheet(INPUT_STYLE)
        hvol.addWidget(self._volume)
        gr.addLayout(hvol)
        cv.addWidget(grp_r)

        cv.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        # ── 底部固定区：生成/停止 + 试听 + 进度 + 状态 ──
        bottom = QFrame()
        bottom.setStyleSheet("background:#1a1a1a;border-top:1px solid #333;")
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(8, 8, 8, 8)
        bl.setSpacing(6)

        self._target_lbl = QLabel("")
        self._target_lbl.setStyleSheet("color:#7fb2ff;font-size:11px;")
        self._target_lbl.setWordWrap(True)
        bl.addWidget(self._target_lbl)

        hbtn = QHBoxLayout()
        self._gen_btn = QPushButton("生成配音")
        self._gen_btn.setStyleSheet(PRIMARY)
        self._gen_btn.clicked.connect(self._generate)
        hbtn.addWidget(self._gen_btn, 2)
        self._play_btn = QPushButton("▶ 试听")
        self._play_btn.setStyleSheet(SECONDARY)
        self._play_btn.setEnabled(False)
        self._play_btn.clicked.connect(self._on_play)
        hbtn.addWidget(self._play_btn, 1)
        bl.addLayout(hbtn)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet(
            "QProgressBar{background:#2a2a2a;border-radius:3px;height:6px;}"
            "QProgressBar::chunk{background:#3d8ef8;border-radius:3px;}")
        bl.addWidget(self._progress)

        self._status = QLabel("选中字幕后，生成配音将直接加入音频轨")
        self._status.setStyleSheet("color:#999;font-size:11px;")
        self._status.setWordWrap(True)
        bl.addWidget(self._status)
        root.addWidget(bottom)

    def _build_engine_box(self, box: QWidget):
        bl = QVBoxLayout(box)
        bl.setContentsMargins(10, 10, 10, 10)
        bl.setSpacing(8)
        bl.addWidget(self._section_header("配音引擎", "选择 TTS 服务商并配置密钥"))

        self._engine_card = QFrame()
        self._engine_card.setStyleSheet("QFrame{background:#2a2a2a;border:1px solid #3a3a3a;border-radius:8px;}")
        ec = QVBoxLayout(self._engine_card)
        ec.setContentsMargins(8, 8, 8, 8)
        ec.setSpacing(6)
        self._engine_btns = []
        for key, name, desc, badge in ENGINES:
            row = QFrame()
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            row.setStyleSheet(ENG_OFF)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(8, 6, 8, 6)
            rl.setSpacing(8)
            left = QVBoxLayout()
            left.setSpacing(1)
            top_row = QHBoxLayout()
            top_row.setSpacing(6)
            top_row.addWidget(self._lbl(name, "#ddd", 12, True))
            b = self._lbl(badge, "#4caf50" if "免费" in badge else "#f0ad4e", 9, True)
            b.setStyleSheet(f"QLabel{{background:{'#1a3a1a' if '免费' in badge else '#3a2a1a'};"
                               f"color:{'#4caf50' if '免费' in badge else '#f0ad4e'};"
                               f"border-radius:3px;padding:1px 6px;font-size:9px;}}")
            top_row.addWidget(b)
            top_row.addStretch()
            left.addLayout(top_row)
            left.addWidget(self._lbl(desc, "#9a9a9a", 10))
            rl.addLayout(left, 1)
            row.mousePressEvent = lambda ev, k=key: self._select_engine(k)
            ec.addWidget(row)
            self._engine_btns.append((key, row))
        bl.addWidget(self._engine_card)

        # API Key 配置
        self._key_frame = QFrame()
        self._key_frame.setStyleSheet("QFrame{background:#2a2a2a;border:1px solid #3a3a3a;border-radius:8px;}")
        kf = QVBoxLayout(self._key_frame)
        kf.setContentsMargins(10, 8, 10, 10)
        kf.setSpacing(6)
        self._key_edit = QLineEdit()
        self._key_edit.setPlaceholderText("API Key")
        self._key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_edit.setStyleSheet(INPUT_STYLE)
        self._key_edit.editingFinished.connect(self._on_key_edited)
        kf.addWidget(self._key_edit)
        bl.addWidget(self._key_frame)

        # 确认按钮：保存设置并收起面板
        self._confirm_btn = QPushButton("✓ 确认")
        self._confirm_btn.setStyleSheet(PRIMARY)
        self._confirm_btn.clicked.connect(self._confirm_engine_box)
        bl.addWidget(self._confirm_btn)

    # ── 工具 ──
    @staticmethod
    def _lbl(text, color="#ccc", size=11, bold=False):
        l = QLabel(text)
        l.setStyleSheet(f"color:{color};font-size:{size}px;{'font-weight:bold;' if bold else ''}")
        return l

    @staticmethod
    def _section_header(icon_text, desc):
        f = QFrame()
        fl = QVBoxLayout(f)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(2)
        fl.addWidget(DubbingPanel._lbl(icon_text, "#5aa6ff", 12, True))
        fl.addWidget(DubbingPanel._lbl(desc, "#8a8a8a", 10))
        return f

    def _toggle_engine_box(self):
        vis = not self._engine_box.isVisible()
        self._engine_box.setVisible(vis)
        self._engine_toggle.setText("配音引擎设置 " + ("▲" if vis else "▼"))

    def _confirm_engine_box(self):
        """确认：保存引擎设置并收起面板。"""
        self._save_engine_settings(silent=True)
        self._engine_box.setVisible(False)
        self._engine_toggle.setText("配音引擎设置 ▼")
        self._status.setText(f"引擎已设置：{ENGINE_NAME.get(self._current_engine, self._current_engine)}")

    # ── 引擎切换 ──
    def _select_engine(self, key: str):
        self._current_engine = key
        for k, row in self._engine_btns:
            row.setStyleSheet(ENG_ON if k == key else ENG_OFF)
        self._apply_engine(key)
        self._save_engine_settings(silent=True)

    def _apply_engine(self, key: str, silent: bool = False):
        """根据引擎更新 Key 框 / 语音控件可用性。"""
        key_cfg = ENGINE_KEYS.get(key)
        if key_cfg:
            env_key, label, placeholder = key_cfg
            self._key_frame.setVisible(True)
            self._key_edit.setPlaceholderText(placeholder)
            self._key_edit.setText(os.environ.get(env_key, ""))
        else:
            self._key_frame.setVisible(False)

        # 声音选择按钮：切换引擎数据源 + 恢复该引擎上次选的声音
        auto = (key == "auto_lang")
        self._voice_btn.set_engine(key)
        # 切到 API 引擎时后台预热声音列表缓存（下次点开秒显，不卡）
        if not auto:
            preload_voices(key)
        self._voice_btn.setEnabled(not auto)
        if auto:
            self._voice_btn.setText("🎵  自动匹配语言音色")
            self._voice_btn._voice_id = ""
        else:
            vid, name = self._voice_by_engine.get(
                key, ENGINE_DEFAULT_VOICE.get(key, ("", "点击选择声音")))
            self._voice_btn._voice_id = vid
            self._voice_btn._voice_name = name
            self._voice_btn.setText(f"🎵  {name}")
        hints = {
            "edge": "点开可浏览全部微软语音，点卡片即试听",
            "auto_lang": "根据字幕文本自动识别语言并匹配音色",
            "fish_audio": "点开加载 Fish Audio 社区声音，点卡片即试听",
            "elevenlabs": "填好 Key 后点开加载你账户的声音",
        }
        self._voice_hint.setText(hints.get(key, "点开选择声音，点卡片即试听"))
        self._voice_hint.setVisible(True)
        if not silent:
            self._status.setText(f"已选择引擎：{ENGINE_NAME.get(key, key)}")

    def _on_voice_changed(self, vid: str, name: str):
        """声音选择器确认后记住当前引擎的选择。"""
        self._voice_by_engine[self._current_engine] = (vid, name)
        self._status.setText(f"音色：{name}")

    def _on_key_edited(self):
        self._save_engine_settings(silent=True)

    def _current_voice(self) -> str:
        """取当前音色 ID（来自声音选择器）。auto_lang 恒为空（自动检测）。"""
        if self._current_engine == "auto_lang":
            return ""
        return getattr(self._voice_btn, "_voice_id", "") or ""

    def get_config(self) -> dict:
        """当前配音配置（供轨道多选字幕朗读复用）：引擎 / 音色 / 语速 / 音量。"""
        speed = self._speed.value()
        pct = int(round((speed - 1.0) * 100))
        return {
            "engine": self._current_engine,
            "voice": self._current_voice(),
            "rate": f"{pct:+d}%",
            "volume": self._volume.value(),
        }

    # ── 生成 ──
    def set_subtitle(self, clip):
        """记录当前字幕 clip 与起点（配音文本取自字幕本身）。"""
        self._subtitle = clip
        self._subtitle_start = getattr(clip, "timeline_start", 0.0) or 0.0
        self._subtitle_end = getattr(clip, "timeline_end", None)
        self._refresh_target_label()

    def _refresh_target_label(self):
        """统计当前选中（含框选多选）字幕条数，提示将生成几条配音。"""
        clips = self._collect_subtitles()
        if not clips:
            self._target_lbl.setText("")
            return
        n = len(clips)
        if n == 1:
            self._target_lbl.setText("将为当前字幕生成配音")
        else:
            self._target_lbl.setText(f"已选中 {n} 条字幕，将逐条生成配音并分别落到音频轨")

    def _collect_subtitles(self):
        """取待配音的字幕 clip 列表（批量：含框选多选；单条：仅当前字幕）。"""
        clips = []
        if callable(self._get_subtitles_cb):
            try:
                clips = self._get_subtitles_cb() or []
            except Exception:
                clips = []
        if not clips and self._subtitle is not None:
            clips = [self._subtitle]
        # 仅保留有文本的字幕，按时间排序
        clips = [c for c in clips if (getattr(c, "text", "") or "").strip()]
        clips.sort(key=lambda c: getattr(c, "timeline_start", 0.0))
        return clips

    def _generate(self):
        """生成 ↔ 停止 切换。"""
        if self._worker is not None and self._worker.isRunning():
            self._gen_cancelled = True
            self._worker.requestInterruption()
            self._status.setText("生成已取消")
            self._reset_gen_ui()
            self._gen_queue = []
            return
        self._start_generation()

    def _start_generation(self):
        clips = self._collect_subtitles()
        if not clips:
            self._status.setText("当前没有可配音的字幕（选中字幕需含文本）")
            return
        voice = self._current_voice()
        if self._current_engine == "fish_audio" and not voice:
            self._status.setText("请先点开音色按钮选择一个 Fish Audio 声音")
            return
        speed = self._speed.value()
        pct = int(round((speed - 1.0) * 100))
        rate = f"{pct:+d}%"
        volume = self._volume.value()

        # 构建生成队列（每条字幕一段音频，落轨对齐各自时间段）
        self._gen_queue = []
        for c in clips:
            self._gen_queue.append((
                c,
                (getattr(c, "text", "") or "").strip(),
                getattr(c, "timeline_start", 0.0) or 0.0,
                getattr(c, "timeline_end", None),
            ))
        self._gen_total = len(self._gen_queue)
        self._gen_idx = 0
        self._gen_cancelled = False
        self._gen_first_path = None

        self._gen_btn.setEnabled(False)
        self._gen_btn.setText("生成中…")
        self._progress.setValue(0)
        if self._gen_total > 1:
            self._status.setText(f"生成中 1/{self._gen_total} …")
        else:
            self._status.setText("生成中…")
        self._start_one(0, voice, rate, volume)

    def _safe_delete_worker(self):
        """安全地删除当前 worker（每个 worker 只删除一次，避免重复 deleteLater 崩溃）。"""
        w = self._worker
        self._worker = None
        if w is not None:
            try:
                w.deleteLater()
            except Exception:  # noqa: BLE001
                pass

    def _start_one(self, idx: int, voice: str, rate: str, volume: float):
        """生成队列中第 idx 条字幕的配音。"""
        if idx >= self._gen_total or self._gen_cancelled:
            return
        self._gen_idx = idx
        clip, text, start, end = self._gen_queue[idx]
        # 供 _on_done 落轨使用
        self._subtitle = clip
        self._subtitle_start = start
        self._subtitle_end = end

        from ui.workers.tts_worker import TTSGenerationWorker
        # 旧 worker（刚完成的那条）由本函数统一 deleteLater 一次。
        # 注意：_on_done 不再重复删除，否则同一对象收到两个 DeferredDelete 事件会崩溃。
        old = self._worker
        self._worker = TTSGenerationWorker(
            text=text, voice=voice, rate=rate,
            engine_type=self._current_engine, volume=volume,
        )
        self._worker.progress.connect(self._progress.setValue)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()
        if old is not None and old is not self._worker:
            old.deleteLater()

    def _reset_gen_ui(self):
        self._gen_btn.setEnabled(True)
        self._gen_btn.setText("生成配音")
        self._play_btn.setEnabled(self._last_path is not None)

    def _on_done(self, path: str):
        if self._gen_cancelled:
            self._reset_gen_ui()
            self._gen_queue = []
            self._safe_delete_worker()
            return
        try:
            self._progress.setValue(100)
            self._last_path = path
            self._play_btn.setEnabled(True)

            dur = _audio_duration(path)
            if self._add_audio_cb is not None:
                try:
                    self._add_audio_cb(path, dur, self._subtitle_start, self._subtitle_end)
                except Exception as e:  # noqa: BLE001
                    self._status.setText(f"落轨失败：{e}")
                    self._reset_gen_ui()
                    self._gen_queue = []
                    self._safe_delete_worker()
                    return

            # 记录首条路径用于批量完成后试听
            if self._gen_idx == 0:
                self._gen_first_path = path

            # 还有下一条 → 继续（_start_one 内部会 deleteLater 刚完成的旧 worker）
            if self._gen_idx + 1 < self._gen_total:
                self._reset_gen_ui()
                self._gen_btn.setEnabled(False)
                self._gen_btn.setText("生成中…")
                self._status.setText(f"生成中 {self._gen_idx + 2}/{self._gen_total} …")
                self._start_one(self._gen_idx + 1,
                                self._current_voice(),
                                f"{int(round((self._speed.value() - 1.0) * 100)):+d}%",
                                self._volume.value())
                return

            # 全部完成：删除最后一条 worker
            self._reset_gen_ui()
            if self._gen_total > 1:
                self._status.setText(
                    f"完成：已生成 {self._gen_total} 条配音并分别落到音频轨"
                    f"（首条 {Path(self._gen_first_path).name} 已自动试听）")
                # 批量：播放首条作为样音
                self._last_path = self._gen_first_path
                self._play_btn.setEnabled(True)
                self._on_play()
            else:
                self._status.setText(
                    f"完成：{Path(path).name}（{dur:.1f}s，已加入音频轨并自动试听）")
                self._on_play()  # 生成完成自动试听
            self._safe_delete_worker()
        except Exception as e:  # noqa: BLE001
            self._status.setText(f"生成异常：{e}")
            self._reset_gen_ui()
            self._gen_queue = []
            self._safe_delete_worker()

    def _on_error(self, err: str):
        self._gen_cancelled = True  # 任一条失败则中止整批
        self._reset_gen_ui()
        self._progress.setValue(0)
        self._status.setText(f"生成失败（已停止）：{err}")
        self._gen_queue = []
        self._safe_delete_worker()

    # ── 试听 ──
    def _on_play(self):
        if self._last_path and os.path.exists(self._last_path):
            self._play_file(self._last_path)

    def _play_file(self, path: str):
        try:
            self._player.setSource(QUrl.fromLocalFile(path))
            self._player.play()
        except Exception:  # noqa: BLE001
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ── 引擎配置读写（统一容器 api_config 负责 .env）──
    def _save_engine_settings(self, silent: bool = False):
        from api_config import write_env
        key_cfg = ENGINE_KEYS.get(self._current_engine)
        updates = {"TTS_ENGINE": self._current_engine}
        if key_cfg:
            env_key = key_cfg[0]
            val = self._key_edit.text().strip()
            if val:
                updates[env_key] = val
        write_env(updates)
        # 同步已加载的 config 模块属性，避免重启才生效
        try:
            import config
            for k, v in updates.items():
                if hasattr(config, k):
                    setattr(config, k, v)
        except Exception:
            pass
        if not silent:
            self._status.setText(f"已保存引擎设置：{self._current_engine}")

    def _load_engine_settings(self):
        from api_config import read_env
        cfg = read_env()
        eng = cfg.get("TTS_ENGINE", "edge")
        if eng not in ENGINE_KEYS_SET:
            eng = "edge"
        self._current_engine = eng
        for k, row in self._engine_btns:
            row.setStyleSheet(ENG_ON if k == eng else ENG_OFF)
