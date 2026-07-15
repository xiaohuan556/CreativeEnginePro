"""
CreativeEnginePro — 公共自定义控件
"""

from PyQt6.QtWidgets import QCheckBox
from PyQt6.QtCore import Qt, QSize, QRect
from PyQt6.QtGui import QColor, QPainter, QPen


# ─── 自定义打勾复选框 ──────────────────────────────────────

class CheckMarkBox(QCheckBox):
    """带白色 ✓ 打勾标记的复选框，替代默认蓝色方块"""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._hover = False

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        indicator_size = 16
        y_offset = (self.height() - indicator_size) // 2
        rect = QRect(0, y_offset, indicator_size, indicator_size)

        checked = self.isChecked()
        enabled = self.isEnabled()

        if checked:
            p.setPen(QPen(QColor("#64b5f6") if self._hover else QColor("#3d8ef8"), 2))
            p.setBrush(QColor("#3d8ef8") if enabled else QColor("#2a5a9a"))
        else:
            p.setPen(QPen(QColor("#777777") if self._hover else QColor("#555555"), 2))
            p.setBrush(QColor("#1a1a1a") if enabled else QColor("#111111"))

        p.drawRoundedRect(rect, 3, 3)

        if checked:
            pen = QPen(QColor("#ffffff"), 2.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)

            cx, cy = rect.x(), rect.y()
            p.drawLine(cx + 3, cy + 9, cx + 6, cy + 12)
            p.drawLine(cx + 6, cy + 12, cx + 13, cy + 4)

        p.end()

        text_x = indicator_size + 6
        text_rect = QRect(text_x, 0, self.width() - text_x, self.height())
        p2 = QPainter(self)
        p2.setPen(QColor("#cccccc") if enabled else QColor("#666666"))
        p2.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        p2.end()

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def sizeHint(self):
        fm = self.fontMetrics()
        text_w = fm.horizontalAdvance(self.text()) if self.text() else 0
        return QSize(22 + text_w, max(24, fm.height() + 6))
