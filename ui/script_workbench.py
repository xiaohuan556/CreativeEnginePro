"""
小欢语音 - AI 脚本生成器 (Tab 3)
产品描述 → AI 生成广告脚本 + 翻译功能
支持产品名标签记忆，情感风格词
"""
import json
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QComboBox, QProgressBar, QLineEdit,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread

# 标签存储
_TAGS_FILE = Path(__file__).parent.parent / "work_temp" / "_script_tags.json"

def _load_tags() -> list:
    if _TAGS_FILE.exists():
        try:
            return json.loads(_TAGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []

def _save_tags(tags: list):
    _TAGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TAGS_FILE.write_text(json.dumps(tags, ensure_ascii=False), encoding="utf-8")

# 翻译语种
LANGUAGES = [
    ("中文","zh"), ("英语","en"),("日语","ja"),("韩语","ko"),("泰语","th"),
    ("越南语","vi"),("西语","es"),("葡语","pt"),("阿语","ar"),("印尼语","id"),
]


class ScriptWorkbench(QWidget):
    polished_text = pyqtSignal(str)
    status_msg = pyqtSignal(str, str)

    STYLES = [
        "激情抓眼球", "沉稳放松", "幽默有趣", "紧迫急迫",
        "高端大气", "网感爆棚", "情感共鸣", "专业权威",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._tags = _load_tags()
        self._last_original = ""  # 翻译前的原文，用于恢复
        self._build()

    def _build(self):
        self.setStyleSheet("background:#1a1a1a;")
        root = QVBoxLayout(self); root.setContentsMargins(24,16,24,16); root.setSpacing(10)

        root.addWidget(self._lbl("✨ AI 广告脚本生成器", "#ccc"))

        # 产品名 + 标签记忆
        nr = QHBoxLayout(); nr.addWidget(QLabel("产品名称")); nr.addStretch()
        root.addLayout(nr)
        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText("例：AI配音助手、智能扫地机器人…"); self.edit_name.setFixedHeight(32)
        self.edit_name.setStyleSheet(_INPUT); self.edit_name.returnPressed.connect(self._add_tag)
        root.addWidget(self.edit_name)

        # 标签行
        self.tags_layout = QHBoxLayout()
        self.tags_layout.setSpacing(4)
        self.tags_layout.addStretch()
        root.addLayout(self.tags_layout)
        self._refresh_tags()
        btn_add_tag = QPushButton("+ 保存标签")
        btn_add_tag.setFixedHeight(24); btn_add_tag.setStyleSheet(_TAG_BTN)
        btn_add_tag.clicked.connect(self._add_tag)
        self.tags_layout.insertWidget(self.tags_layout.count()-1, btn_add_tag)

        root.addWidget(QLabel("产品描述"))
        self.editor_desc = QTextEdit()
        self.editor_desc.setPlaceholderText("描述产品功能、卖点、目标用户…\n面向短视频创作者的一键AI配音工具，支持50+语言…")
        self.editor_desc.setStyleSheet(_EDITOR); self.editor_desc.setMaximumHeight(120)
        root.addWidget(self.editor_desc)

        # 参数行
        pr = QHBoxLayout(); pr.setSpacing(12)
        pr.addWidget(QLabel("风格"))
        self.combo_style = QComboBox(); self.combo_style.addItems(self.STYLES)
        self.combo_style.setStyleSheet(_COMBO); self.combo_style.setFixedWidth(120)
        pr.addWidget(self.combo_style)
        pr.addWidget(QLabel("时长"))
        self.combo_dur = QComboBox(); self.combo_dur.addItems(["15s","20s","30s","45s","60s","自定义"])
        self.combo_dur.setStyleSheet(_COMBO); self.combo_dur.setFixedWidth(80)
        self.combo_dur.currentTextChanged.connect(lambda t: self.edit_dur.setVisible(t=="自定义"))
        pr.addWidget(self.combo_dur)
        self.edit_dur = QLineEdit(); self.edit_dur.setFixedWidth(50); self.edit_dur.setFixedHeight(28)
        self.edit_dur.setPlaceholderText("秒"); self.edit_dur.setStyleSheet(_INPUT); self.edit_dur.hide()
        pr.addWidget(self.edit_dur)
        pr.addStretch()
        self.btn_gen = QPushButton("✨ 生成脚本"); self.btn_gen.setStyleSheet(_PRIMARY); self.btn_gen.setFixedHeight(34)
        self.btn_gen.clicked.connect(self._generate); pr.addWidget(self.btn_gen)
        root.addLayout(pr)

        self.progress = QProgressBar(); self.progress.setRange(0,100); self.progress.setFixedHeight(2)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet("QProgressBar{background:#1a1a1a;border:none;border-radius:1px;}QProgressBar::chunk{background:#2a4a70;border-radius:1px;}")
        root.addWidget(self.progress)

        # 结果
        rh = QHBoxLayout()
        rh.addWidget(QLabel("📝 生成结果"))
        # 翻译
        rh.addWidget(QLabel("  翻译:"))
        self._lang_btns = {}
        for label, code in LANGUAGES:
            b = QPushButton(label)
            b.setFixedHeight(22)
            b.setStyleSheet(_LANG_OFF)
            b.clicked.connect(lambda _,c=code: self._translate_result(c))
            self._lang_btns[code] = b
            rh.addWidget(b)
        # 自定义语种
        self.btn_custom_lang = QPushButton("自定义")
        self.btn_custom_lang.setFixedHeight(22)
        self.btn_custom_lang.setStyleSheet(_LANG_OFF)
        self.btn_custom_lang.clicked.connect(self._pick_custom_trans)
        rh.addWidget(self.btn_custom_lang)
        rh.addStretch()
        btn_send = QPushButton("→ 转到语音台朗读"); btn_send.setStyleSheet(_ACCENT)
        btn_send.clicked.connect(self._send_to_voice); rh.addWidget(btn_send)
        btn_copy = QPushButton("📋 复制"); btn_copy.setStyleSheet(_GHOST)
        btn_copy.clicked.connect(self._copy); rh.addWidget(btn_copy)
        self.btn_restore = QPushButton("↩ 恢复原文")
        self.btn_restore.setStyleSheet(_GHOST)
        self.btn_restore.setFixedHeight(22)
        self.btn_restore.setFixedWidth(90)
        self.btn_restore.clicked.connect(self._restore_original)
        self.btn_restore.hide()
        rh.addWidget(self.btn_restore)
        root.addLayout(rh)

        self.editor_result = QTextEdit()
        self.editor_result.setPlaceholderText("AI 生成的广告脚本…"); self.editor_result.setStyleSheet(_EDITOR)
        root.addWidget(self.editor_result, 1)

    def _lbl(self, t, c):
        l = QLabel(t); l.setStyleSheet(f"color:{c};font-size:14px;font-weight:bold;"); return l

    def _refresh_tags(self):
        # 清除旧标签
        for i in reversed(range(self.tags_layout.count())):
            w = self.tags_layout.itemAt(i).widget()
            if w and hasattr(w, 'property') and w.property('_tag_widget'):
                self.tags_layout.removeWidget(w); w.deleteLater()
        # 重建
        for tag in self._tags:
            from PyQt6.QtWidgets import QWidget
            tw = QWidget()
            tw.setProperty('_tag_widget', True)
            tl = QHBoxLayout(tw); tl.setContentsMargins(0,0,0,0); tl.setSpacing(1)
            b = QPushButton(tag)
            b.setFixedHeight(22); b.setStyleSheet(_TAG_STYLE)
            b.clicked.connect(lambda _,t=tag: (self.edit_name.setText(t), self._load_tag_desc(t)))
            tl.addWidget(b)
            # 删除按钮
            db = QPushButton("×")
            db.setFixedSize(18, 18); db.setToolTip(f"删除标签「{tag}」")
            db.setStyleSheet("QPushButton{background:transparent;color:#e74c3c;border:none;font-size:12px;font-weight:bold;border-radius:9px;}QPushButton:hover{background:#3a1a1a;color:#ff4444;}")
            db.clicked.connect(lambda _,t=tag: self._remove_tag(t))
            tl.addWidget(db)
            self.tags_layout.insertWidget(self.tags_layout.count()-1, tw)

    def _remove_tag(self, tag: str):
        if tag in self._tags:
            self._tags.remove(tag)
            _save_tags(self._tags)
            self._refresh_tags()
            self.status_msg.emit(f"已删除标签: {tag}", "info")

    def _add_tag(self):
        name = self.edit_name.text().strip()
        desc = self.editor_desc.toPlainText().strip()
        if name and name not in self._tags:
            self._tags.append(name)
            _save_tags(self._tags)
            self._refresh_tags()
            self.status_msg.emit(f"已保存标签: {name}", "success")

    def _load_tag_desc(self, name: str):
        self.edit_name.setText(name)
        # 尝试加载标签描述
        tf = _TAGS_FILE.parent / f"_tag_{name}.txt"
        if tf.exists():
            self.editor_desc.setPlainText(tf.read_text(encoding="utf-8"))
        self.status_msg.emit(f"已加载: {name}", "info")

    def _save_tag_desc(self, name: str, desc: str):
        try:
            (_TAGS_FILE.parent / f"_tag_{name}.txt").write_text(desc, encoding="utf-8")
        except Exception:
            pass  # 标签保存失败不阻塞主流程

    def load_raw_text(self, text: str):
        self.editor_desc.setPlainText(text)

    def _generate(self):
        name = self.edit_name.text().strip()
        desc = self.editor_desc.toPlainText().strip()
        if not desc:
            self.status_msg.emit("请先描述产品", "warn"); return
        # 自动保存描述
        if name:
            self._save_tag_desc(name, desc)
        style = self.combo_style.currentText()
        dur = self.combo_dur.currentText()
        if dur == "自定义":
            raw = self.edit_dur.text().strip() or "30"
            dur = f"{raw}秒" if raw.isdigit() else raw

        # 清理旧 worker
        if self._worker is not None:
            old = self._worker
            try: old.finished.disconnect(); old.error.disconnect(); old.progress.disconnect()
            except Exception: pass
            if old.isRunning():
                old.finished.connect(old.deleteLater)
            else:
                old.deleteLater()

        self.btn_gen.setEnabled(False); self.btn_gen.setText("生成中..."); self.progress.setValue(10)
        self._worker = _ScriptWorker(name, desc, style, dur)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.finished.connect(self._on_done); self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, r):
        self.btn_gen.setEnabled(True); self.btn_gen.setText("✨ 生成脚本"); self.progress.setValue(100)
        self.editor_result.setPlainText(r); self._last_original = ""; self.btn_restore.hide()
        self.status_msg.emit("生成完成", "success")
        if self._worker: self._worker.deleteLater()
        self._worker = None

    def _on_error(self, e):
        self.btn_gen.setEnabled(True); self.btn_gen.setText("✨ 生成脚本")
        self.status_msg.emit(f"生成失败: {e}", "error")
        if self._worker: self._worker.deleteLater()
        self._worker = None

    def _translate_result(self, code: str):
        text = self.editor_result.toPlainText().strip()
        if not text:
            self.status_msg.emit("请先生成脚本", "warn"); return
        if self._worker is not None and self._worker.isRunning():
            self.status_msg.emit("正在生成中，请稍候", "warn"); return
        # 首次翻译时保存原文
        if not self._last_original:
            self._last_original = text
        # 清理旧 worker
        if self._worker is not None:
            old = self._worker
            try: old.finished.disconnect(); old.error.disconnect()
            except Exception: pass
            if old.isRunning():
                old.finished.connect(old.deleteLater)
            else:
                old.deleteLater()
        self._worker = _TransWorker(text, code)
        self._worker.finished.connect(self._on_trans_done); self._worker.error.connect(self._on_trans_error)
        self.status_msg.emit("翻译中...", "info"); self._worker.start()

    def _on_trans_done(self, r):
        self.editor_result.setPlainText(r); self.btn_restore.show()
        self.status_msg.emit("翻译完成", "success")
        if self._worker: self._worker.deleteLater()
        self._worker = None

    def _restore_original(self):
        """恢复翻译前的原文"""
        if self._last_original:
            self.editor_result.setPlainText(self._last_original)
            self._last_original = ""
            self.btn_restore.hide()
            self.status_msg.emit("已恢复原文", "info")

    def _on_trans_error(self, e):
        self.status_msg.emit(f"翻译失败: {e}", "error")
        if self._worker: self._worker.deleteLater()
        self._worker = None

    def _pick_custom_trans(self):
        from PyQt6.QtWidgets import QInputDialog
        lang, ok = QInputDialog.getText(self, "自定义翻译语种",
            "请输入目标语言（如：法语、德语、意大利语…）")
        if ok and lang.strip():
            self._translate_result(lang.strip())

    def _copy(self):
        t = self.editor_result.toPlainText()
        if t:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(t); self.status_msg.emit("已复制", "info")

    def _send_to_voice(self):
        t = self.editor_result.toPlainText().strip()
        if t: self.polished_text.emit(t); self.status_msg.emit("已发送到语音台", "success")
        else: self.status_msg.emit("没有可发送的脚本", "warn")


class _ScriptWorker(QThread):
    finished=pyqtSignal(str); error=pyqtSignal(str); progress=pyqtSignal(int)
    def __init__(self, n,d,s,r):
        super().__init__(); self._n=n; self._d=d; self._s=s; self._r=r
    def run(self):
        try:
            from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME
            if not LLM_API_KEY: self.error.emit("未配置 API Key，请在设置中填写"); return
            self.progress.emit(30)
            from openai import OpenAI
            c=OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=45.0)
            p=self._n or "产品"
            sys=f"你是顶级短视频广告文案专家。为「{p}」写{self._s}风格口播脚本。\n严格约束：脚本朗读时长必须控制在{self._r}左右，每句8-12字为宜，按语速每秒4字计算总字数。\n要求：前3秒强钩子、卖点突出、CTA结尾、口语化配音感、纯文案无标注、中文输出每句一行。"
            self.progress.emit(60)
            r=c.chat.completions.create(model=LLM_MODEL_NAME,messages=[{"role":"system","content":sys},{"role":"user","content":self._d}],temperature=0.85)
            self.progress.emit(90)
            self.finished.emit(r.choices[0].message.content.strip())
        except Exception as e:
            import traceback; self.error.emit(f"{e}\n{traceback.format_exc()}")

class _TransWorker(QThread):
    finished=pyqtSignal(str); error=pyqtSignal(str)
    def __init__(self, t,l): super().__init__(); self._t=t; self._l=l
    def run(self):
        try:
            from core.builtin_translator import translate_text
            result = translate_text(self._t, self._l)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))
        # 不打印 traceback，避免控制台噪音


_EDITOR="QTextEdit{background:#0e0e0e;border:1px solid #222;border-radius:6px;color:#ccc;font-size:13px;padding:10px;}QTextEdit:focus{border-color:#3d8ef8;}"
_INPUT="QLineEdit{background:#0e0e0e;border:1px solid #222;border-radius:6px;color:#ccc;font-size:13px;padding:6px 10px;}QLineEdit:focus{border-color:#3d8ef8;}"
_COMBO="QComboBox{background:#0e0e0e;border:1px solid #2a2a2a;border-radius:5px;color:#ccc;font-size:12px;padding:4px 8px;}QComboBox QAbstractItemView{background:#1a1a1a;color:#ccc;}QComboBox::drop-down{border:none;}"
_PRIMARY="QPushButton{background:#3d8ef8;color:#fff;border:none;border-radius:6px;font-size:13px;font-weight:bold;padding:8px 20px;}QPushButton:hover{background:#5a9ff9;}QPushButton:disabled{background:#333;color:#666;}"
_ACCENT="QPushButton{background:#3d8ef8;color:#fff;border:none;border-radius:6px;font-size:13px;font-weight:bold;padding:8px 20px;}QPushButton:hover{background:#5a9ff9;}"
_GHOST="QPushButton{background:transparent;color:#888;border:1px solid #333;border-radius:6px;font-size:12px;padding:6px 14px;}QPushButton:hover{color:#ccc;border-color:#555;}"
_TAG_BTN="QPushButton{background:transparent;color:#3d8ef8;border:1px dashed #3d8ef8;border-radius:4px;font-size:11px;padding:2px 8px;}QPushButton:hover{background:#1a3050;}"
_TAG_STYLE="QPushButton{background:#1a2a3a;color:#3d8ef8;border:1px solid #2a4a5a;border-radius:4px;font-size:11px;padding:2px 8px;}QPushButton:hover{background:#1a3050;color:#5a9ff9;}"
_LANG_ON="QPushButton{background:#1a3050;color:#3d8ef8;border:1px solid #3d8ef8;border-radius:3px;font-size:10px;font-weight:bold;padding:2px 6px;}"
_LANG_OFF="QPushButton{background:#1e1e1e;color:#666;border:1px solid #2a2a2a;border-radius:3px;font-size:10px;padding:2px 6px;}QPushButton:hover{color:#ccc;border-color:#555;}"
