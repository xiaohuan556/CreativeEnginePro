"""
widgets/log_console.py
紧凑日志条，默认只占 2-3 行高度
"""
from datetime import datetime

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QPlainTextEdit, QLabel, QPushButton, QHBoxLayout
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextCursor


class LogConsole(QWidget):
    _LEVEL_COLOR = {
        "info": "#00eaff",
        "error": "#ff7675",
        "warn": "#fdcb6e",
        "success": "#00b894",
    }

    def __init__(self, parent=None, max_height: int = 100):
        super().__init__(parent)
        self._max_height = max_height
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        # 标题栏（极简）
        header = QHBoxLayout()
        header.setSpacing(6)
        title = QLabel("📋 日志")
        title.setStyleSheet("color:#666; font-size:11px;")
        header.addWidget(title)
        header.addStretch()

        btn_clear = QPushButton("✕")
        btn_clear.setFixedSize(18, 18)
        btn_clear.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_clear.setStyleSheet(
            "background:transparent; border:none; color:#555; font-size:10px;"
        )
        btn_clear.clicked.connect(self.clear)
        header.addWidget(btn_clear)

        lay.addLayout(header)

        # 文本区（紧凑）
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumHeight(self._max_height)
        self.text.setStyleSheet(
            "background:#0d0d0d; color:#00eaff; border:1px solid #1e1e1e; "
            "border-radius:2px; font-family:'Consolas','Courier New',monospace; "
            "font-size:10px; padding:2px 4px;"
        )
        lay.addWidget(self.text)

    def log(self, message: str, level: str = "info"):
        ts = datetime.now().strftime("%H:%M:%S")
        color = self._LEVEL_COLOR.get(level, "#00eaff")
        self.text.appendHtml(
            f'<span style="color:#444">[{ts}]</span> '
            f'<span style="color:{color}">{message}</span>'
        )
        self.text.moveCursor(QTextCursor.MoveOperation.End)
        # 只保留最近 200 条
        if self.text.document().blockCount() > 200:
            cursor = self.text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            cursor.movePosition(QTextCursor.MoveOperation.Down, QTextCursor.MoveMode.KeepAnchor, 1)
            cursor.removeSelectedText()

    def clear(self):
        self.text.clear()
