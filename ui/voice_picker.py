"""
ui/voice_picker.py
声音选择器 — 支持 edge-tts 和 ElevenLabs 两种引擎
根据当前引擎自动切换声音来源
"""
import threading
import tempfile
import time
import os
from pathlib import Path

from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QGridLayout, QWidget, QSizePolicy, QProgressBar,
)
from PyQt6.QtCore import Qt, pyqtSignal, QUrl, QThread

# ── ElevenLabs voice fetch ──
def _fetch_eleven_voices(api_key: str) -> list:
    import urllib.request, json
    req = urllib.request.Request(
        "https://api.elevenlabs.io/v1/voices",
        headers={"xi-api-key": api_key, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    return [
        {
            "voice_id": v.get("voice_id", ""),
            "name": v.get("name", ""),
            "labels": v.get("labels", {}),
            "preview_url": v.get("preview_url", ""),
            "category": v.get("category", ""),
        }
        for v in data.get("voices", [])
    ]


def _fetch_custom_voices() -> list:
    """从自定义 TTS API 拉取声音列表，兼容常见返回格式"""
    import urllib.request, json, os
    key = os.getenv("CUSTOM_TTS_KEY", "")
    url = os.getenv("CUSTOM_TTS_VOICES_URL", "")
    if not url:
        raise ValueError("未设置自定义 API 地址")
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
        headers["xi-api-key"] = key
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    raw = data if isinstance(data, list) else data.get("voices") or data.get("data") or data.get("results") or []
    return [
        {
            "voice_id": v.get("voice_id") or v.get("id") or v.get("name", str(i)),
            "name": v.get("name") or v.get("voice_id") or v.get("id", str(i)),
            "labels": v.get("labels") or v.get("language") or {},
            "preview_url": v.get("preview_url") or v.get("preview") or "",
            "category": v.get("category", ""),
        }
        for i, v in enumerate(raw)
    ]

# ── 百度语音 ──
BAIDU_VOICES = [
    {"voice_id":"0","name":"度小美（标准女声）","labels":{"language":"zh"}},
    {"voice_id":"1","name":"度小宇（标准男声）","labels":{"language":"zh"}},
    {"voice_id":"3","name":"度逍遥（情感男声）","labels":{"language":"zh"}},
    {"voice_id":"4","name":"度丫丫（童声）","labels":{"language":"zh"}},
    {"voice_id":"5","name":"度小娇（粤语女声）","labels":{"language":"zh"}},
    {"voice_id":"106","name":"度博文（新闻男声）","labels":{"language":"zh"}},
    {"voice_id":"110","name":"度小童（可爱童声）","labels":{"language":"zh"}},
    {"voice_id":"111","name":"度小萌（软萌女声）","labels":{"language":"zh"}},
    {"voice_id":"103","name":"度米朵（甜美女声）","labels":{"language":"zh"}},
    {"voice_id":"5003","name":"度逍遥2.0（精品男声）","labels":{"language":"zh"}},
    {"voice_id":"5118","name":"度小鹿（精品女声）","labels":{"language":"zh"}},
]

# ── 硅基流动声音 ──
SF_VOICES = [
    {"voice_id":"alex","name":"沉稳男声","labels":{"language":"zh"}},
    {"voice_id":"benjamin","name":"低沉男声","labels":{"language":"zh"}},
    {"voice_id":"charles","name":"磁性男声","labels":{"language":"zh"}},
    {"voice_id":"david","name":"欢快男声","labels":{"language":"zh"}},
    {"voice_id":"anna","name":"沉稳女声","labels":{"language":"zh"}},
    {"voice_id":"bella","name":"激情女声","labels":{"language":"zh"}},
    {"voice_id":"claire","name":"温柔女声","labels":{"language":"zh"}},
    {"voice_id":"diana","name":"欢快女声","labels":{"language":"zh"}},
]

# ── Deepgram 声音 ──
DG_VOICES = [
    {"voice_id":"aura-asteria-en","name":"Asteria（知性女声）","labels":{"language":"en"}},
    {"voice_id":"aura-luna-en","name":"Luna（温柔女声）","labels":{"language":"en"}},
    {"voice_id":"aura-stella-en","name":"Stella（明亮女声）","labels":{"language":"en"}},
    {"voice_id":"aura-athena-en","name":"Athena（权威女声）","labels":{"language":"en"}},
    {"voice_id":"aura-hera-en","name":"Hera（成熟女声）","labels":{"language":"en"}},
    {"voice_id":"aura-orion-en","name":"Orion（沉稳男声）","labels":{"language":"en"}},
    {"voice_id":"aura-arcas-en","name":"Arcas（温暖男声）","labels":{"language":"en"}},
    {"voice_id":"aura-perseus-en","name":"Perseus（清晰男声）","labels":{"language":"en"}},
    {"voice_id":"aura-angus-en","name":"Angus（磁性男声）","labels":{"language":"en"}},
    {"voice_id":"aura-orpheus-en","name":"Orpheus（文艺男声）","labels":{"language":"en"}},
    {"voice_id":"aura-helios-en","name":"Helios（明亮男声）","labels":{"language":"en"}},
    {"voice_id":"aura-zeus-en","name":"Zeus（权威男声）","labels":{"language":"en"}},
]

# ── Fish Audio ──
_FISH_LANG_ZH = {"zh":"中文","en":"英语","ja":"日语","ko":"韩语","es":"西语","ar":"阿语","fr":"法语","de":"德语","pt":"葡语","vi":"越南语","th":"泰语","id":"印尼语","ms":"马来语","ru":"俄语","it":"意大利语","tr":"土耳其语","nl":"荷兰语","pl":"波兰语","hi":"印地语","fil":"菲律宾语"}

def _fish_name_zh(title: str, languages: list = None) -> str:
    """翻译英文标题为中文"""
    if any('\u4e00' <= c <= '\u9fff' for c in title):
        return title
    if len(title) < 3:
        return title
    try:
        from deep_translator import GoogleTranslator
        result = GoogleTranslator(source="auto", target="zh-CN").translate(title)
        return result if result else title
    except Exception:
        return title[:20] if len(title) > 20 else title

def _batch_translate_titles(titles: list) -> dict:
    """批量翻译标题，复用 translator 实例"""
    result = {}
    try:
        from deep_translator import GoogleTranslator
        t = GoogleTranslator(source="auto", target="zh-CN")
        for title in titles:
            if any('\u4e00' <= c <= '\u9fff' for c in title):
                result[title] = title
            elif len(title) < 3:
                result[title] = title
            else:
                try:
                    result[title] = t.translate(title)
                except Exception:
                    result[title] = title[:20] if len(title) > 20 else title
    except Exception:
        for title in titles:
            result[title] = title[:20] if len(title) > 20 else title
    return result

def _fetch_fish_voices() -> list:
    import urllib.request, json, os
    key = os.getenv("FISH_AUDIO_KEY","") or os.getenv("BAIDU_TTS_KEY","")
    if not key: raise ValueError("未设置 Fish Audio Key")
    all_v = []; seen = set(); page = 0
    while len(all_v) < 30:
        url = f"https://api.fish.audio/model?page={page}&page_size=30"
        req = urllib.request.Request(url, headers={"Authorization":f"Bearer {key}","Accept":"application/json"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        items = data.get("items",[])
        if not items: break
        for v in items:
            if v.get("type")!="tts": continue
            vid = v.get("_id","")
            title = v.get("title","")
            key_dedup = title.lower().strip()
            if key_dedup in seen: continue
            seen.add(key_dedup)
            ls = v.get("languages",[])
            if isinstance(ls,str): ls=[ls]
            desc = v.get("description","")
            all_v.append({"voice_id":vid,"name":title,"name_orig":title,"labels":{"language":ls,"description":desc},"preview_url":"","category":v.get("type","")})
        page += 1
        if len(items)<30: break
    return all_v


VOICE_ZH_MAP = {
    "zh-CN-XiaoxiaoNeural": "晓晓", "zh-CN-XiaoyiNeural": "晓依",
    "zh-CN-XiaohanNeural": "晓涵", "zh-CN-XiaomengNeural": "晓梦",
    "zh-CN-XiaomoNeural": "晓墨", "zh-CN-XiaoruiNeural": "晓睿",
    "zh-CN-XiaoshuangNeural": "晓双", "zh-CN-XiaoxuanNeural": "晓萱",
    "zh-CN-XiaozhenNeural": "晓甄", "zh-CN-XiaochenNeural": "晓辰",
    "zh-CN-XiaoqiuNeural": "晓秋", "zh-CN-XiaoyanNeural": "晓妍",
    "zh-CN-YunjianNeural": "云健", "zh-CN-YunxiNeural": "云希",
    "zh-CN-YunxiaNeural": "云夏", "zh-CN-YunyangNeural": "云扬",
    "zh-CN-YunzeNeural": "云泽", "zh-CN-YunhaoNeural": "云皓",
    "zh-CN-YunjieNeural": "云杰",
    "zh-CN-liaoning-XiaobeiNeural": "小北（东北话）",
    "zh-CN-shaanxi-XiaoniNeural": "小妮（陕西话）",
    "zh-TW-HsiaoChenNeural": "曉晨（台湾）",
    "zh-TW-YunJheNeural": "雲哲（台湾）",
    "zh-HK-HiuGaaiNeural": "曉佳（粤语）",
    "zh-HK-HiuMaanNeural": "曉曼（粤语）",
    "zh-HK-WanLungNeural": "雲龍（粤语）",
    "en-US-JennyNeural": "Jenny（美式女声）", "en-US-AriaNeural": "Aria（美式女声）",
    "en-US-AnaNeural": "Ana（美式童声）", "en-US-EmmaNeural": "Emma（美式女声）",
    "en-US-AvaNeural": "Ava（美式女声）", "en-US-MichelleNeural": "Michelle（美式女声）",
    "en-US-ElizabethNeural": "Elizabeth（美式女声）",
    "en-GB-SoniaNeural": "Sonia（英式女声）", "en-GB-LibbyNeural": "Libby（英式女声）",
    "en-GB-MiaNeural": "Mia（英式女声）",
    "en-AU-NatashaNeural": "Natasha（澳式女声）",
    "en-US-GuyNeural": "Guy（美式男声）", "en-US-ChristopherNeural": "Chris（美式男声）",
    "en-US-EricNeural": "Eric（美式男声）", "en-US-BrianNeural": "Brian（美式男声）",
    "en-GB-RyanNeural": "Ryan（英式男声）", "en-GB-ThomasNeural": "Thomas（英式男声）",
    "en-AU-WilliamNeural": "William（澳式男声）",
    "ja-JP-NanamiNeural": "七海（日语女声）", "ja-JP-KeitaNeural": "圭太（日语男声）",
    "ko-KR-SunHiNeural": "善熙（韩语女声）", "ko-KR-InJoonNeural": "仁俊（韩语男声）",
}

EDGE_CATS = [
    ("中文女声", lambda v: v.get("Locale","").startswith("zh-CN") and v.get("Gender")=="Female"),
    ("中文男声", lambda v: v.get("Locale","").startswith("zh-CN") and v.get("Gender")=="Male"),
    ("方言港台", lambda v: v.get("Locale","").startswith(("zh-TW","zh-HK","zh-CN-liaoning","zh-CN-shaanxi"))),
    ("英语女声", lambda v: v.get("Locale","").startswith("en") and v.get("Gender")=="Female"),
    ("英语男声", lambda v: v.get("Locale","").startswith("en") and v.get("Gender")=="Male"),
    ("日韩", lambda v: v.get("Locale","").startswith(("ja","ko"))),
]

_PREVIEW_TEXT = {
    "zh": "你好，这是声音试听。", "en": "Hello, this is a voice preview.",
    "ja": "こんにちは、音声プレビューです。", "ko": "안녕하세요, 음성 미리듣기입니다.",
}


class VoiceCard(QFrame):
    clicked = pyqtSignal(str)
    fav_toggled = pyqtSignal(str, bool)

    def __init__(self, voice_id: str, name: str, tag: str = "", favorited: bool = False):
        super().__init__()
        self._voice_id = voice_id
        self._favorited = favorited
        self.setFixedHeight(54)
        self.setMinimumWidth(240)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        h = QHBoxLayout(self); h.setContentsMargins(4,3,8,3); h.setSpacing(5)
        # 收藏星标（用 QLabel 避免按钮字体问题）
        self.lbl_fav = QLabel("☆")
        self.lbl_fav.setFixedSize(24, 24)
        self.lbl_fav.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_fav.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_fav.setToolTip("收藏" if not favorited else "取消收藏")
        fav_color = "#f0ad4e" if favorited else "#555"
        self.lbl_fav.setStyleSheet(f"QLabel{{color:{fav_color};font-size:18px;font-weight:bold;}}QLabel:hover{{color:#f0ad4e;}}")
        self.lbl_fav.mousePressEvent = self._toggle_fav_label
        h.addWidget(self.lbl_fav)
        n = QLabel(name[:18] + ("…" if len(name)>18 else ""))
        n.setStyleSheet("color:#eee;font-size:13px;font-weight:bold;")
        n.setWordWrap(False)
        h.addWidget(n, 1)
        if tag:
            t = QLabel(tag[:25])
            t.setStyleSheet("color:#666;font-size:10px;")
            t.setWordWrap(False)
            h.addWidget(t)
        self._apply_style(False)

    def _toggle_fav_label(self, ev):
        self._favorited = not self._favorited
        self.lbl_fav.setText("★" if self._favorited else "☆")
        self.lbl_fav.setToolTip("取消收藏" if self._favorited else "收藏")
        fav_color = "#f0ad4e" if self._favorited else "#555"
        self.lbl_fav.setStyleSheet(f"QLabel{{color:{fav_color};font-size:18px;font-weight:bold;}}QLabel:hover{{color:#f0ad4e;}}")
        self.fav_toggled.emit(self._voice_id, self._favorited)

    def _apply_style(self, sel: bool):
        s = "QFrame{background:#1a2a4a;border:2px solid #3d8ef8;border-radius:8px;}" if sel else \
            "QFrame{background:#141414;border:1px solid #2a2a2a;border-radius:8px;}QFrame:hover{border-color:#3d8ef8;background:#1a1e2a;}"
        self.setStyleSheet(s)

    def set_selected(self, sel: bool):
        self._apply_style(sel)

    def mousePressEvent(self, ev):
        # 只在直接点击卡片时触发，不拦截星标按钮
        if ev.position().toPoint().x() > 30:
            self.clicked.emit(self._voice_id)


class VoicePickerPopup(QFrame):
    voice_selected = pyqtSignal(str, str)
    _preview_done = pyqtSignal(str)
    _refresh_cards = pyqtSignal()

    def __init__(self, parent=None, engine: str = "edge"):
        super().__init__(parent)
        self._engine = engine
        self._voices = []
        self._selected_id = ""
        self._selected_name = ""
        self._categories = EDGE_CATS
        self._loader = None
        self._preview_lock = threading.Lock()
        self._preview_cancel = threading.Event()
        self._favorites = self._load_favorites()  # set of voice_id
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(580, 460)
        self.setStyleSheet("QFrame{background:#1a1a1a;border:1px solid #333;border-radius:12px;}")
        self._build()
        self._preview_done.connect(self._on_preview_done)
        self._refresh_cards.connect(self._on_translate_done)
        self._start_load()

    def closeEvent(self, ev):
        """关闭弹窗时取消试听线程，防止信号发送到已销毁的 widget"""
        self._preview_cancel.set()
        # 清理试听临时文件
        import glob
        for f in glob.glob(os.path.join(tempfile.gettempdir(), "_vp_*.mp3")):
            try: os.remove(f)
            except Exception: pass
        super().closeEvent(ev)

    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(12,12,12,12); root.setSpacing(6)
        hdr = QHBoxLayout()
        hdr.addWidget(self._lbl("🎵 选择声音", "#3d8ef8"))
        self.lbl_status = self._lbl("加载中…", "#f0ad4e"); hdr.addWidget(self.lbl_status)
        hdr.addStretch()
        self.btn_apply = QPushButton("✓ 应用")
        self.btn_apply.setFixedHeight(30)
        self.btn_apply.setStyleSheet("QPushButton{background:#3d8ef8;color:#fff;border:none;border-radius:5px;font-size:12px;font-weight:bold;padding:4px 14px;}QPushButton:hover{background:#5a9ff9;}QPushButton:disabled{background:#333;color:#666;}")
        self.btn_apply.clicked.connect(self._apply_selection); self.btn_apply.setEnabled(False)
        hdr.addWidget(self.btn_apply)
        btn_close = QPushButton("✕ 关闭")
        btn_close.setFixedSize(52,26)
        btn_close.setStyleSheet("QPushButton{background:transparent;color:#888;border:1px solid #333;border-radius:4px;font-size:11px;}QPushButton:hover{color:#e74c3c;border-color:#e74c3c;}")
        btn_close.clicked.connect(self.close)
        hdr.addWidget(btn_close)
        root.addLayout(hdr)

        self.progress = QProgressBar()
        self.progress.setRange(0,0); self.progress.setFixedHeight(2); self.progress.setTextVisible(False)
        self.progress.setStyleSheet("QProgressBar{background:#222;border:1px solid #333;border-radius:2px;}QProgressBar::chunk{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #3d8ef8,stop:1 #5a9ff9);border-radius:2px;}")
        root.addWidget(self.progress)

        body = QHBoxLayout(); body.setSpacing(0)
        self._left_layout = QVBoxLayout(); self._left_layout.setContentsMargins(0,0,4,0); self._left_layout.setSpacing(2)
        self._left_layout.addStretch()
        self.cat_btns = []
        body.addLayout(self._left_layout)
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.VLine); sep.setStyleSheet("color:#2a2a2a;")
        body.addWidget(sep)

        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}QScrollBar:vertical{background:#141414;width:4px;border-radius:2px;}QScrollBar::handle:vertical{background:#333;border-radius:2px;}")
        self.cards_widget = QWidget()
        self.cards_layout = QGridLayout(self.cards_widget)
        self.cards_layout.setContentsMargins(4,4,4,4); self.cards_layout.setSpacing(6)
        self.cards_layout.setAlignment(Qt.AlignmentFlag.AlignTop|Qt.AlignmentFlag.AlignLeft)
        self.scroll.setWidget(self.cards_widget)
        body.addWidget(self.scroll, 1)
        root.addLayout(body, 1)
        self._current_cat = 0

    def _lbl(self, text, color):
        l = QLabel(text); l.setStyleSheet(f"color:{color};font-size:12px;"); return l

    def _start_load(self):
        # 清理旧 loader
        if self._loader is not None:
            old = self._loader
            try: old.finished_data.disconnect(); old.error_msg.disconnect()
            except Exception: pass
            if old.isRunning():
                old.finished.connect(old.deleteLater)
            else:
                old.deleteLater()
        self._loader = _VoiceLoaderWorker(self._engine)
        self._loader.finished_data.connect(self._on_loaded)
        self._loader.error_msg.connect(lambda e: (self.lbl_status.setText(f"加载失败: {e[:40]}"), self.progress.setRange(0,100), self.progress.setValue(100)))
        self._loader.start()

    def _on_loaded(self, voices, categories, engine):
        # 清理 loader
        if self._loader:
            self._loader.deleteLater()
            self._loader = None
        self._voices = voices; self._categories = categories; self._engine = engine
        self.progress.setRange(0,100); self.progress.setValue(100)
        eng_name = {"elevenlabs":"ElevenLabs","edge":"Edge-TTS"}.get(engine, engine)
        self.lbl_status.setText(f"✓ {eng_name} · {len(voices)}个声音")

        # 重建分类按钮
        for b in self.cat_btns:
            self._left_layout.removeWidget(b); b.deleteLater()
        self.cat_btns.clear()
        stretch = self._left_layout.takeAt(self._left_layout.count()-1)
        for idx, (name, _) in enumerate(categories):
            btn = QPushButton(name)
            btn.setFixedHeight(28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setStyleSheet(_CAT_NORMAL)
            btn.clicked.connect(lambda _,i=idx: self._on_cat_click(i))
            self.cat_btns.append(btn)
            self._left_layout.addWidget(btn)
        self._left_layout.addStretch()

        self._current_cat = 0
        if self.cat_btns:
            self._highlight_cat(0)
        self._show_category(0)
        # 收藏夹分类
        self._add_fav_category()
        # Fish Audio 后台异步翻译标题
        if self._engine == "fish_audio" and voices:
            threading.Thread(target=self._translate_titles_bg, daemon=True).start()

    def _translate_titles_bg(self):
        """后台线程：批量翻译 Fish 模型标题，完成后通过信号刷新"""
        titles = [v.get("name",v.get("voice_id","")) for v in self._voices if isinstance(v, dict)]
        translated = _batch_translate_titles(titles)
        for v in self._voices:
            if isinstance(v, dict) and v.get("name") in translated:
                v["name_orig"] = v.get("name", "")
                v["name"] = translated[v["name_orig"]]
        self._refresh_cards.emit()

    def _on_translate_done(self):
        self.lbl_status.setText("✓ Fish Audio · 已翻译")
        self._add_fav_category()
        self._show_category(self._current_cat)

    def _load_favorites(self) -> set:
        """加载当前引擎的收藏列表"""
        import json
        fav_file = Path(__file__).parent.parent / "hooks" / f"fav_{self._engine}.json"
        try:
            if fav_file.exists():
                return set(json.loads(fav_file.read_text(encoding="utf-8")))
        except Exception:
            pass
        return set()

    def _save_favorites(self):
        """保存当前引擎的收藏列表"""
        import json
        fav_file = Path(__file__).parent.parent / "hooks" / f"fav_{self._engine}.json"
        fav_file.parent.mkdir(parents=True, exist_ok=True)
        fav_file.write_text(json.dumps(list(self._favorites), ensure_ascii=False), encoding="utf-8")

    def _toggle_favorite(self, voice_id: str, fav: bool):
        """切换收藏状态"""
        if fav:
            self._favorites.add(voice_id)
        else:
            self._favorites.discard(voice_id)
        self._save_favorites()
        # 刷新收藏夹分类
        self._add_fav_category()

    def _add_fav_category(self):
        """在分类列表首部插入/更新收藏夹"""
        if not self._voices:
            return
        # 统一用 voice_id 或 ShortName 查找
        fav_voices = []
        for v in self._voices:
            vid = ""
            if isinstance(v, dict):
                vid = v.get("voice_id") or v.get("ShortName", "")
            if vid and vid in self._favorites:
                fav_voices.append(v)
        # 移除旧的收藏分类按钮
        for b in self.cat_btns:
            if b.text().startswith("⭐"):
                self._left_layout.removeWidget(b)
                b.deleteLater()
                self.cat_btns.remove(b)
                break
        if fav_voices:
            btn = QPushButton(f"⭐ 收藏夹({len(fav_voices)})")
            btn.setFixedHeight(28); btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(_CAT_NORMAL)
            btn.clicked.connect(lambda: self._show_favorites(fav_voices))
            self.cat_btns.insert(0, btn)
            self._left_layout.insertWidget(0, btn)

    def _show_favorites(self, fav_voices: list):
        """显示收藏夹"""
        while self.cards_layout.count():
            it = self.cards_layout.takeAt(0)
            if it and it.widget(): it.widget().deleteLater()
        cols = 2
        for i, v in enumerate(fav_voices):
            self._add_card(i, cols, v)
        for b in self.cat_btns:
            b.setStyleSheet(_CAT_NORMAL)

    def _add_card(self, i: int, cols: int, v):
        """创建一张语音卡片"""
        if isinstance(v, dict) and "voice_id" in v:
            vid = v["voice_id"]; name = v.get("name", vid)
            labels = v.get("labels", {})
            if isinstance(labels, dict):
                tag = labels.get("description","")[:30] if labels.get("description") else ""
            elif isinstance(labels, str):
                tag = labels
            else:
                tag = ""
        else:
            vid = v["ShortName"]
            name = VOICE_ZH_MAP.get(vid, vid.split("-")[-1].replace("Neural",""))
            tag = ""
        card = VoiceCard(vid, name, tag, favorited=(vid in self._favorites))
        if isinstance(v, dict) and v.get("name_orig"):
            card.setToolTip(f"原名: {v['name_orig']}")
        if vid == self._selected_id: card.set_selected(True)
        card.clicked.connect(self._on_card_click)
        card.fav_toggled.connect(self._toggle_favorite)
        self.cards_layout.addWidget(card, i//cols, i%cols)

    def _on_cat_click(self, idx):
        self._current_cat = idx; self._highlight_cat(idx); self._show_category(idx)

    def _highlight_cat(self, idx):
        for i, b in enumerate(self.cat_btns):
            b.setStyleSheet(_CAT_ACTIVE if i==idx else _CAT_NORMAL)

    def _show_category(self, idx):
        while self.cards_layout.count():
            it = self.cards_layout.takeAt(0)
            if it and it.widget(): it.widget().deleteLater()
        if idx >= len(self._categories): return
        _, fn = self._categories[idx]
        filtered = [v for v in self._voices if fn(v)]
        cols = 2
        for i, v in enumerate(filtered):
            self._add_card(i, cols, v)

    def _on_card_click(self, voice_id):
        self._selected_id = voice_id; self._selected_name = voice_id
        self.btn_apply.setEnabled(True)
        for v in self._voices:
            if isinstance(v, dict) and v.get("voice_id")==voice_id:
                self._selected_name = v.get("name", voice_id)
            elif not isinstance(v, dict) and v.get("ShortName")==voice_id:
                self._selected_name = VOICE_ZH_MAP.get(voice_id, voice_id)
        for i in range(self.cards_layout.count()):
            it = self.cards_layout.itemAt(i)
            if it and it.widget() and isinstance(it.widget(), VoiceCard):
                it.widget().set_selected(it.widget()._voice_id == voice_id)
        self._preview(voice_id)

    def _preview(self, voice_id):
        self.lbl_status.setText("试听中…")
        # 取消上一次试听
        self._preview_cancel.set()
        # 短暂等待上一次线程退出
        time.sleep(0.05)
        self._preview_cancel.clear()
        threading.Thread(target=self._do_preview, args=(voice_id,), daemon=True).start()

    def _do_preview(self, voice_id):
        """试听线程：如果被取消则不播放"""
        if not self._preview_lock.acquire(blocking=False):
            # 有正在进行的试听，跳过
            return
        try:
            if self._preview_cancel.is_set():
                return
            if self._engine == "elevenlabs":
                self._preview_eleven(voice_id)
            elif self._engine == "fish_audio":
                self._preview_fish(voice_id)
            elif self._engine == "siliconflow":
                self._preview_siliconflow(voice_id)
            elif self._engine == "deepgram":
                self._preview_deepgram(voice_id)
            elif self._engine == "baidu":
                self._preview_done.emit("百度暂不支持试听")
            else:
                self._preview_edge(voice_id)
        except Exception as e:
            if not self._preview_cancel.is_set():
                self._preview_done.emit(f"试听失败: {e}")
        finally:
            self._preview_lock.release()

    def _preview_edge(self, sn):
        try:
            import asyncio, edge_tts
            tmp = os.path.join(tempfile.gettempdir(), f"_vp_{sn}.mp3")
            text = _PREVIEW_TEXT.get("zh" if sn.startswith("zh") else "ja" if sn.startswith("ja") else "ko" if sn.startswith("ko") else "en", _PREVIEW_TEXT["en"])
            async def g(): await edge_tts.Communicate(text, sn).save(tmp)
            asyncio.run(g())
            self._preview_done.emit(f"PLAY:{tmp}")
        except Exception as e:
            self._preview_done.emit(f"生成失败: {e}")

    def _preview_eleven(self, vid):
        import urllib.request, json
        from config import ELEVENLABS_API_KEY
        tmp = os.path.join(tempfile.gettempdir(), f"_vp_el_{vid}.mp3")
        try:
            preview_url = ""
            for v in self._voices:
                if isinstance(v, dict) and v.get("voice_id")==vid:
                    preview_url = v.get("preview_url",""); break
            if preview_url:
                urllib.request.urlretrieve(preview_url, tmp)
            else:
                data = json.dumps({"text":"Hello, this is a voice preview.","model_id":"eleven_multilingual_v2","voice_settings":{"stability":0.5,"similarity_boost":0.75}}).encode()
                req = urllib.request.Request(f"https://api.elevenlabs.io/v1/text-to-speech/{vid}", data=data,
                    headers={"xi-api-key":ELEVENLABS_API_KEY,"Content-Type":"application/json","Accept":"audio/mpeg"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    with open(tmp,"wb") as f: f.write(resp.read())
            self._preview_done.emit(f"PLAY:{tmp}")
        except Exception as e:
            err = str(e)
            if "401" in err: self._preview_done.emit("Key无效")
            elif "402" in err or "quota" in err.lower(): self._preview_done.emit("余额不足/配额已用完")
            elif "429" in err: self._preview_done.emit("请求太频繁")
            else: self._preview_done.emit(f"试听失败: {e}")

    def _preview_fish(self, vid):
        """Fish Audio TTS 试听"""
        import urllib.request, json
        tmp = os.path.join(tempfile.gettempdir(), f"_vp_fish_{vid[:8]}.mp3")
        key = os.getenv("FISH_AUDIO_KEY", "")
        if not key:
            self._preview_done.emit("未设置 Key"); return
        data = json.dumps({
            "text": "Hello, this is a voice preview.",
            "reference_id": vid,
            "format": "mp3",
            "normalize": True,
            "latency": "normal",
        }).encode()
        req = urllib.request.Request("https://api.fish.audio/v1/tts", data=data,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "model": "s2-pro",
                "Accept": "audio/mpeg",
            })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                with open(tmp, "wb") as f: f.write(resp.read())
            self._preview_done.emit(f"PLAY:{tmp}")
        except Exception as e:
            err = str(e)
            if hasattr(e, 'read'):
                try:
                    body = json.loads(e.read().decode())
                    msg = body.get("message", "")
                except: msg = ""
            else: msg = ""
            if "401" in err: self._preview_done.emit("Key 无效")
            elif "402" in err: self._preview_done.emit(f"余额不足: {msg}" if msg else "余额不足，请充值")
            elif "422" in err: self._preview_done.emit("请求格式错误")
            elif "429" in err: self._preview_done.emit("请求太频繁")
            else: self._preview_done.emit(f"试听失败: {msg or e}")

    def _preview_siliconflow(self, vid):
        """硅基流动 TTS 试听"""
        import urllib.request, json
        key = os.getenv("SILICONFLOW_KEY", "")
        if not key: self._preview_done.emit("未设置 Key"); return
        tmp = os.path.join(tempfile.gettempdir(), f"_vp_sf_{vid}.mp3")
        data = json.dumps({
            "model": "FunAudioLLM/CosyVoice2-0.5B",
            "voice": f"FunAudioLLM/CosyVoice2-0.5B:{vid}",
            "input": "你好，这是语音试听。",
            "response_format": "mp3",
        }).encode()
        req = urllib.request.Request("https://api.siliconflow.cn/v1/audio/speech",
            data=data,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                with open(tmp, "wb") as f: f.write(resp.read())
            self._preview_done.emit(f"PLAY:{tmp}")
        except Exception as e:
            self._preview_done.emit(f"硅基试听失败: {e}")

    def _preview_deepgram(self, vid):
        """Deepgram TTS 试听"""
        import urllib.request, json
        key = os.getenv("DEEPGRAM_KEY", "")
        if not key: self._preview_done.emit("未设置 Key"); return
        tmp = os.path.join(tempfile.gettempdir(), f"_vp_dg_{vid}.mp3")
        data = json.dumps({"text": "Hello, this is a voice preview."}).encode()
        req = urllib.request.Request(f"https://api.deepgram.com/v1/speak?model={vid}",
            data=data,
            headers={"Authorization": f"Token {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                with open(tmp, "wb") as f: f.write(resp.read())
            self._preview_done.emit(f"PLAY:{tmp}")
        except Exception as e:
            self._preview_done.emit(f"Deepgram试听失败: {e}")

    def _on_preview_done(self, msg):
        # 如果已被取消（用户点了新的试听），不播放旧结果
        if self._preview_cancel.is_set() and msg.startswith("PLAY:"):
            return
        if msg.startswith("PLAY:"):
            tmp_path = msg[5:]
            if not hasattr(self, '_pp'):
                from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
                self._pp = QMediaPlayer(); self._pa = QAudioOutput()
                self._pp.setAudioOutput(self._pa); self._pa.setVolume(0.8)
            # 清理上一次试听的临时文件
            if hasattr(self, '_last_preview_tmp') and self._last_preview_tmp:
                try: os.remove(self._last_preview_tmp)
                except Exception: pass
            self._last_preview_tmp = tmp_path
            self._pp.setSource(QUrl.fromLocalFile(tmp_path)); self._pp.play()
            self.lbl_status.setText("▶ 正在试听…")
        else:
            self.lbl_status.setText(msg)

    def _apply_selection(self):
        if self._selected_id:
            self.voice_selected.emit(self._selected_id, self._selected_name)
            self.close()

    def popup_at(self, x: int, y: int):
        self.move(x, y); self.show()


class _VoiceLoaderWorker(QThread):
    finished_data = pyqtSignal(list, list, str)
    error_msg = pyqtSignal(str)

    def __init__(self, engine):
        super().__init__(); self._engine = engine

    def run(self):
        try:
            if self._engine == "elevenlabs":
                from config import ELEVENLABS_API_KEY
                if not ELEVENLABS_API_KEY:
                    self.error_msg.emit("未设置 ElevenLabs Key"); return
                voices = _fetch_eleven_voices(ELEVENLABS_API_KEY)
                langs = {}
                for v in voices:
                    l = v.get("labels",{}).get("language","") or v.get("labels",{}).get("locale","") or "other"
                    langs.setdefault(l, []).append(v)
                cats = []
                el_zh = {"en":"英语","zh":"中文","ja":"日语","ko":"韩语"}
                for code, zh in el_zh.items():
                    if code in langs:
                        cats.append((f"{zh}({len(langs[code])})", lambda v,c=code: v.get("labels",{}).get("language","")==c))
                cats.append((f"全部({len(voices)})", lambda v: True))
                self.finished_data.emit(voices, cats, "elevenlabs")
            elif self._engine == "fish_audio":
                voices = _fetch_fish_voices()
                langs = {}
                for v in voices:
                    ls = v.get("labels", {}).get("language", "other")
                    for l in (ls if isinstance(ls, list) else [ls]):
                        langs.setdefault(l, []).append(v)
                cats = []
                for l, zh_name in [("zh","中文"),("en","英语"),("ja","日语"),("ko","韩语"),("es","西语"),("ar","阿语")]:
                    if l in langs:
                        def _mkfn(ll):
                            return lambda v: ll in (v.get("labels",{}).get("language",[]) if isinstance(v.get("labels",{}).get("language",""), list) else [v.get("labels",{}).get("language","other")])
                        cats.append((f"{zh_name}({len(langs[l])})", _mkfn(l)))
                # 其他语言统一放"其他"
                others = []
                for l in langs:
                    if l not in ("zh","en","ja","ko","es","ar"):
                        others.extend(langs[l])
                if others:
                    cats.append((f"其他({len(others)})", lambda v: v in others))
                cats.append((f"全部({len(voices)})", lambda v: True))
                self.finished_data.emit(voices, cats, "fish_audio")
            elif self._engine == "siliconflow":
                cats = [(f"全部({len(SF_VOICES)})", lambda v: True)]
                self.finished_data.emit(SF_VOICES, cats, "siliconflow")
            elif self._engine == "deepgram":
                cats = [(f"全部({len(DG_VOICES)})", lambda v: True)]
                self.finished_data.emit(DG_VOICES, cats, "deepgram")
            else:
                import asyncio, edge_tts
                voices = asyncio.run(edge_tts.list_voices())
                self.finished_data.emit(voices, EDGE_CATS, "edge")
        except Exception as e:
            import traceback
            self.error_msg.emit(f"{e}\n{traceback.format_exc()}")


class VoiceSelectButton(QPushButton):
    voice_changed = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__()
        self._voice_id = "zh-CN-XiaoxiaoNeural"
        self._voice_name = "晓晓"
        self._engine = "edge"
        self.setText("🎵  "+self._voice_name)
        self.setFixedHeight(36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QPushButton{background:#141414;border:1px solid #2a2a2a;border-radius:6px;color:#ccc;font-size:13px;padding:6px 12px;text-align:left;}QPushButton:hover{border-color:#3d8ef8;}")
        self.clicked.connect(self._show)

    def set_engine(self, e):
        self._engine = e

    def _show(self):
        # 千语种模式：无需选声音，自动检测
        if self._engine == "auto_lang":
            return
        p = VoicePickerPopup(self, engine=self._engine)
        p.voice_selected.connect(self._on_sel)
        p._selected_id = self._voice_id
        # 智能定位：按钮上方优先，不够则屏幕居中
        btn_rect = self.mapToGlobal(self.rect().bottomLeft())
        pw, ph = 560, 440
        screen = self.screen().availableGeometry()
        x = max(0, min(btn_rect.x() - (pw - self.width())//2, screen.right() - pw))
        y = btn_rect.y() - ph - 4  # 按钮上方4px
        if y < 0:
            y = min(btn_rect.y() + self.height() + 4, screen.bottom() - ph)
        p.popup_at(x, y)

    def _on_sel(self, vid, name):
        self._voice_id = vid; self._voice_name = name
        self.setText(f"🎵  {name}")
        self.voice_changed.emit(vid, name)


_CAT_NORMAL = "QPushButton{background:transparent;color:#888;border:none;border-radius:5px;font-size:12px;text-align:left;padding:4px 10px;}QPushButton:hover{background:#1e1e1e;color:#ccc;}"
_CAT_ACTIVE = "QPushButton{background:#1a2a4a;color:#3d8ef8;border:none;border-radius:5px;font-size:12px;text-align:left;padding:4px 10px;}"
