"""手绘分镜稿：把草图直接写回 storyboard shot.draft_panel。"""
from __future__ import annotations

import json
import math
import uuid
from pathlib import Path

from PyQt6.QtCore import Qt, QPointF, QRectF, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton,
    QSpinBox, QStackedWidget, QTextEdit, QVBoxLayout, QWidget,
)

from ai.providers.base import TaskRequest
from ai.service import get_ai_manager
from ai.storyboard import extract_json


ANNOTATION_TYPES = {
    "action": ("人物运动 / 构图", "#ef5350"),
    "camera": ("镜头运动 / 机位", "#3488e8"),
    "gaze": ("视线 / 互动关系", "#35ad66"),
    "effect": ("光影 / 冲击 / 重点", "#f39a28"),
    "sound": ("声音 / 情绪提示", "#9c62d6"),
}


class StoryboardSheet(QWidget):
    """可导出的整页导演分镜；图片、元数据与标注分层绘制。"""

    shot_selected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.shots = []
        self.selected = -1
        self.columns = 3
        self.setMinimumSize(760, 500)

    def set_shots(self, shots, selected=-1, columns=3):
        self.shots = shots or []; self.selected = selected; self.columns = columns
        self.update()

    def _cells(self, rect):
        margin, gap = 18, 12
        cols = max(1, self.columns)
        rows = max(1, math.ceil(len(self.shots) / cols))
        width = (rect.width() - margin * 2 - gap * (cols - 1)) / cols
        height = (rect.height() - margin * 2 - gap * (rows - 1)) / rows
        return [QRectF(rect.left() + margin + (i % cols) * (width + gap),
                       rect.top() + margin + (i // cols) * (height + gap), width, height)
                for i in range(len(self.shots))]

    @staticmethod
    def _annotation_points(value):
        points = value.get("points") or []
        if len(points) != 2: return None
        try: return tuple(float(v) for pair in points for v in pair)
        except (TypeError, ValueError): return None

    def _draw_annotations(self, painter, shot, image_rect):
        for value in shot.get("annotations", []) or []:
            if not isinstance(value, dict): continue
            points = self._annotation_points(value)
            if not points: continue
            x1, y1, x2, y2 = points
            color = QColor(ANNOTATION_TYPES.get(value.get("type"), ("", "#ef5350"))[1])
            pen = QPen(color, 3, Qt.PenStyle.DashLine if value.get("type") == "gaze" else Qt.PenStyle.SolidLine)
            a = QPointF(image_rect.left() + x1 * image_rect.width(), image_rect.top() + y1 * image_rect.height())
            b = QPointF(image_rect.left() + x2 * image_rect.width(), image_rect.top() + y2 * image_rect.height())
            SketchCanvas._arrow(painter, a, b, pen)

    def _paint(self, painter, rect):
        painter.fillRect(rect, QColor("#17171b")); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for index, cell in enumerate(self._cells(rect)):
            shot = self.shots[index]
            painter.fillRect(cell, QColor("#f4f0e8"))
            painter.setPen(QPen(QColor("#3488e8" if index == self.selected else "#59595f"),
                                3 if index == self.selected else 1)); painter.drawRect(cell)
            meta_h = min(34, cell.height() * .18)
            image_rect = QRectF(cell.left() + 1, cell.top() + 1, cell.width() - 2, cell.height() - meta_h - 2)
            path = str(shot.get("draft_panel") or "")
            image = QImage(path) if path and Path(path).exists() else QImage()
            if image.isNull():
                painter.fillRect(image_rect, QColor("#fffdf8")); painter.setPen(QColor("#8b8983"))
                painter.drawText(image_rect, Qt.AlignmentFlag.AlignCenter, "等待分镜画面")
            else:
                painter.drawImage(image_rect, image)
            self._draw_annotations(painter, shot, image_rect)
            painter.fillRect(QRectF(cell.left(), cell.bottom() - meta_h, cell.width(), meta_h), QColor("#eee9df"))
            painter.setPen(QColor("#242429")); painter.setFont(QFont("Microsoft YaHei UI", 8, 600))
            move = shot.get("camera") or shot.get("camera_slot") or "固定"
            meta = f"{index + 1:02d}   {float(shot.get('duration') or 5):g}s   {shot.get('shot_size') or '中景'}   {move}"
            painter.drawText(QRectF(cell.left() + 8, cell.bottom() - meta_h, cell.width() - 16, meta_h),
                             Qt.AlignmentFlag.AlignVCenter, meta)

    def paintEvent(self, _event):
        painter = QPainter(self); self._paint(painter, QRectF(self.rect())); painter.end()

    def mousePressEvent(self, event):
        for index, rect in enumerate(self._cells(QRectF(self.rect()))):
            if rect.contains(event.position()): self.shot_selected.emit(index); return

    def render_image(self, width=1800, height=1200):
        image = QImage(width, height, QImage.Format.Format_ARGB32); image.fill(QColor("#17171b"))
        painter = QPainter(image); self._paint(painter, QRectF(0, 0, width, height)); painter.end()
        return image


class SketchCanvas(QWidget):
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(720, 405)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.tool = "pen"
        self.color = QColor("#202026")
        self.width = 4
        self.background = QImage()
        self.strokes = []
        self.redo_strokes = []
        self._current = None

    def set_background(self, path: str):
        self.background = QImage(path) if path and Path(path).exists() else QImage()
        self.strokes = []
        self.redo_strokes = []
        self.update()

    def set_tool(self, tool: str):
        self.tool = tool
        self.setCursor(Qt.CursorShape.CrossCursor)

    def _canvas_rect(self):
        margin = 18
        available = QRectF(margin, margin, self.width() - margin * 2,
                           self.height() - margin * 2)
        ratio = 16 / 9
        width = available.width()
        height = width / ratio
        if height > available.height():
            height = available.height(); width = height * ratio
        return QRectF((self.width() - width) / 2, (self.height() - height) / 2,
                      width, height)

    def _normalized(self, point):
        rect = self._canvas_rect()
        return QPointF((point.x() - rect.left()) / max(1, rect.width()),
                       (point.y() - rect.top()) / max(1, rect.height()))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self._canvas_rect().contains(event.position()):
            return
        self._current = {"tool": self.tool, "points": [self._normalized(event.position())],
                         "width": self.width, "color": self.color.name()}
        self.redo_strokes.clear()

    def mouseMoveEvent(self, event):
        if self._current is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        self._current["points"].append(self._normalized(event.position()))
        self.update()

    def mouseReleaseEvent(self, event):
        if self._current is None:
            return
        self._current["points"].append(self._normalized(event.position()))
        self.strokes.append(self._current)
        self._current = None
        self.changed.emit(); self.update()

    def undo(self):
        if self.strokes:
            self.redo_strokes.append(self.strokes.pop()); self.changed.emit(); self.update()

    def redo(self):
        if self.redo_strokes:
            self.strokes.append(self.redo_strokes.pop()); self.changed.emit(); self.update()

    def clear(self):
        if self.strokes:
            self.redo_strokes.extend(reversed(self.strokes)); self.strokes = []
            self.changed.emit(); self.update()

    @staticmethod
    def _arrow(painter, start, end, pen):
        painter.setPen(pen); painter.drawLine(start, end)
        angle = math.atan2(end.y() - start.y(), end.x() - start.x())
        length = 16
        painter.drawLine(end, QPointF(end.x() - length * math.cos(angle - .55),
                                      end.y() - length * math.sin(angle - .55)))
        painter.drawLine(end, QPointF(end.x() - length * math.cos(angle + .55),
                                      end.y() - length * math.sin(angle + .55)))

    def _draw_stroke(self, painter, stroke, rect):
        points = stroke.get("points") or []
        if len(points) < 2:
            return
        mapped = [QPointF(rect.left() + p.x() * rect.width(),
                          rect.top() + p.y() * rect.height()) for p in points]
        tool = stroke.get("tool")
        color = QColor("#ffffff") if tool == "eraser" else QColor(stroke.get("color", "#202026"))
        width = 24 if tool == "eraser" else int(stroke.get("width", 4))
        pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                   Qt.PenJoinStyle.RoundJoin)
        if tool == "arrow":
            self._arrow(painter, mapped[0], mapped[-1], pen); return
        path = QPainterPath(mapped[0])
        for point in mapped[1:]: path.lineTo(point)
        painter.setPen(pen); painter.drawPath(path)

    def paintEvent(self, _event):
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#151519"))
        rect = self._canvas_rect(); painter.fillRect(rect, QColor("#ffffff"))
        if not self.background.isNull():
            painter.drawImage(rect, self.background)
        for stroke in self.strokes: self._draw_stroke(painter, stroke, rect)
        if self._current: self._draw_stroke(painter, self._current, rect)
        painter.setPen(QPen(QColor("#55555f"), 1)); painter.drawRect(rect)

    def render_image(self, width=1280, height=720):
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(QColor("#ffffff")); painter = QPainter(image)
        rect = QRectF(0, 0, width, height)
        if not self.background.isNull(): painter.drawImage(rect, self.background)
        for stroke in self.strokes: self._draw_stroke(painter, stroke, rect)
        painter.end(); return image


class HanddrawStoryboardDialog(QDialog):
    saved = pyqtSignal()

    def __init__(self, board: dict, parent=None):
        super().__init__(parent)
        self.board = board
        locks = board.get("production_models") if isinstance(board, dict) else {}
        self.image_provider_name = str(
            (locks or {}).get("image_provider") or
            (board.get("image_provider") if isinstance(board, dict) else "") or "")
        self.current_index = -1
        self.states = {}
        self._ai_plan_task = None
        self._ai_image_tasks = {}
        self._ai_image_queue = []
        self._ai_previous_image = ""
        self.setWindowTitle("AI 分镜稿")
        self.resize(1540, 900)
        self.setStyleSheet(
            "QDialog{background:#111115;color:#e8e8ed;font-family:'Microsoft YaHei UI';}"
            "QFrame{background:#1b1b20;border:1px solid #303038;border-radius:12px;}"
            "QPushButton,QComboBox,QSpinBox{background:#292930;color:#eee;border:1px solid #41414a;"
            "border-radius:7px;padding:7px 11px;}QPushButton:checked{background:#e8e8ed;color:#17171b;}"
            "QTextEdit,QListWidget{background:#17171b;color:#ddd;border:1px solid #33333b;"
            "border-radius:8px;padding:7px;}QListWidget::item{padding:10px;border-radius:7px;}"
            "QListWidget::item:selected{background:#343442;}"
        )
        root = QHBoxLayout(self); root.setContentsMargins(14, 14, 14, 14)
        left = QFrame(); left.setFixedWidth(225); ll = QVBoxLayout(left)
        title = QLabel("AI 分镜稿"); title.setStyleSheet("font-size:18px;font-weight:700;")
        ll.addWidget(title); self.list = QListWidget(); ll.addWidget(self.list, 1)
        add = QPushButton("＋ 新建镜头格"); add.clicked.connect(self.add_shot); ll.addWidget(add)
        root.addWidget(left)
        center = QVBoxLayout()
        mode_bar = QHBoxLayout(); self.mode_buttons = {}
        for text, mode in (("叙事板", "narrative"), ("动作板", "action"), ("空间板", "spatial")):
            button = QPushButton(text); button.setCheckable(True)
            button.clicked.connect(lambda _=False, value=mode: self.set_board_mode(value))
            mode_bar.addWidget(button); self.mode_buttons[mode] = button
        mode_bar.addStretch(); self.annotation_toggle = QPushButton("导演标注")
        self.annotation_toggle.setCheckable(True); self.annotation_toggle.setChecked(True)
        self.annotation_toggle.clicked.connect(self.refresh_sheet); mode_bar.addWidget(self.annotation_toggle)
        export_sheet = QPushButton("导出整页"); export_sheet.clicked.connect(self.export_sheet)
        mode_bar.addWidget(export_sheet); center.addLayout(mode_bar)
        ai_box = QFrame(); ai_layout = QVBoxLayout(ai_box)
        ai_title = QLabel("把想法交给 AI，自动生成整套分镜")
        ai_title.setStyleSheet("font-size:15px;font-weight:700;"); ai_layout.addWidget(ai_title)
        self.idea = QTextEdit(); self.idea.setFixedHeight(72)
        self.idea.setPlaceholderText("只写你的想法。例如：一个失忆的宇航员回到废弃地球，在旧电影院发现自己的童年录像……")
        ai_layout.addWidget(self.idea)
        ai_actions = QHBoxLayout(); self.shot_count = QSpinBox(); self.shot_count.setRange(1, 24)
        self.shot_count.setValue(8); self.shot_count.setPrefix("镜头数 "); ai_actions.addWidget(self.shot_count)
        self.ai_style = QComboBox(); self.ai_style.addItems(
            ["电影写实", "黑白手绘分镜", "动画电影", "商业广告", "纪录片", "复古胶片"])
        ai_actions.addWidget(self.ai_style); self.ai_generate = QPushButton("✦ AI 生成整套分镜")
        self.ai_generate.clicked.connect(self.generate_with_ai); ai_actions.addWidget(self.ai_generate)
        self.ai_status = QLabel("等待创意"); ai_actions.addWidget(self.ai_status, 1)
        ai_layout.addLayout(ai_actions); self.ai_progress = QProgressBar(); self.ai_progress.setRange(0, 100)
        self.ai_progress.setValue(0); self.ai_progress.setTextVisible(False); ai_layout.addWidget(self.ai_progress)
        center.addWidget(ai_box)
        toolbar = QHBoxLayout()
        self.tool_buttons = {}
        for text, tool in (("✎ 画笔", "pen"), ("➜ 箭头", "arrow"), ("▱ 橡皮", "eraser")):
            button = QPushButton(text); button.setCheckable(True)
            button.clicked.connect(lambda _=False, value=tool: self.select_tool(value))
            toolbar.addWidget(button); self.tool_buttons[tool] = button
        toolbar.addWidget(QLabel("粗细")); self.pen_width = QSpinBox(); self.pen_width.setRange(1, 16)
        self.pen_width.setValue(4); self.pen_width.valueChanged.connect(self.change_width)
        toolbar.addWidget(self.pen_width); toolbar.addStretch()
        undo = QPushButton("↶ 撤销"); undo.clicked.connect(self.canvas_undo); toolbar.addWidget(undo)
        redo = QPushButton("↷ 重做"); redo.clicked.connect(self.canvas_redo); toolbar.addWidget(redo)
        clear = QPushButton("清空"); clear.clicked.connect(self.clear_canvas); toolbar.addWidget(clear)
        center.addLayout(toolbar); self.canvas = SketchCanvas(); self.sheet = StoryboardSheet()
        self.sheet.shot_selected.connect(self.list.setCurrentRow)
        self.work_stack = QStackedWidget(); self.work_stack.addWidget(self.sheet); self.work_stack.addWidget(self.canvas)
        center.addWidget(self.work_stack, 1)
        view_bar = QHBoxLayout(); self.sheet_view = QPushButton("▦ 整页故事板"); self.sheet_view.setCheckable(True)
        self.sheet_view.setChecked(True); self.sheet_view.clicked.connect(lambda: self.set_view(0))
        self.draw_view = QPushButton("✎ 逐格绘制"); self.draw_view.setCheckable(True)
        self.draw_view.clicked.connect(lambda: self.set_view(1)); view_bar.addWidget(self.sheet_view)
        view_bar.addWidget(self.draw_view); view_bar.addStretch(); center.addLayout(view_bar)
        info = QHBoxLayout(); self.shot_size = QComboBox(); self.shot_size.addItems(
            ["远景", "全景", "中景", "中近景", "近景", "特写"])
        self.duration = QSpinBox(); self.duration.setRange(1, 60); self.duration.setSuffix(" 秒")
        self.notes = QTextEdit(); self.notes.setPlaceholderText("镜头内容、人物动作、台词、运镜说明…")
        self.notes.setFixedHeight(82); info.addWidget(self.shot_size); info.addWidget(self.duration)
        info.addWidget(self.notes, 1); center.addLayout(info)
        foot = QHBoxLayout(); export = QPushButton("导出当前 PNG"); export.clicked.connect(self.export_current)
        foot.addWidget(export); foot.addStretch(); save = QPushButton("保存到分镜"); save.clicked.connect(self.save_all)
        save.setStyleSheet("background:#eeeeef;color:#151519;font-weight:700;"); foot.addWidget(save)
        center.addLayout(foot); root.addLayout(center, 1)
        inspector = QFrame(); inspector.setFixedWidth(300); ir = QVBoxLayout(inspector)
        self.inspector_title = QLabel("镜头 --"); self.inspector_title.setStyleSheet("font-size:17px;font-weight:700;")
        ir.addWidget(self.inspector_title); form = QFormLayout()
        self.camera_angle = QLineEdit(); self.camera_angle.setPlaceholderText("如：低机位肩后")
        self.lens = QLineEdit(); self.lens.setPlaceholderText("如：35mm")
        self.camera_move = QLineEdit(); self.camera_move.setPlaceholderText("一种主导运镜或固定")
        self.dramatic_purpose = QLineEdit(); self.entry_state = QLineEdit(); self.exit_state = QLineEdit()
        self.screen_direction = QLineEdit(); self.transition = QLineEdit(); self.sound = QLineEdit()
        for label, widget in (("机位角度", self.camera_angle), ("镜头焦段", self.lens),
                              ("镜头运动", self.camera_move), ("剧情功能", self.dramatic_purpose),
                              ("开始状态", self.entry_state), ("结束状态", self.exit_state),
                              ("屏幕方向", self.screen_direction), ("转场", self.transition), ("声音", self.sound)):
            form.addRow(label, widget)
        ir.addLayout(form); ir.addWidget(QLabel("动作节点 / 场面调度"))
        self.blocking = QTextEdit(); self.blocking.setFixedHeight(82); ir.addWidget(self.blocking)
        ir.addWidget(QLabel("连续性锚点")); self.continuity = QTextEdit(); self.continuity.setFixedHeight(82)
        ir.addWidget(self.continuity); ir.addWidget(QLabel("标注图例"))
        for _key, (label, color) in ANNOTATION_TYPES.items():
            legend = QLabel(f"●  {label}"); legend.setStyleSheet(f"color:{color};padding:2px;"); ir.addWidget(legend)
        ir.addStretch(); root.addWidget(inspector)
        self.list.currentRowChanged.connect(self.switch_shot)
        self._ai_timer = QTimer(self); self._ai_timer.setInterval(500)
        self._ai_timer.timeout.connect(self._poll_ai)
        self._inspector_fields = (self.camera_angle, self.lens, self.camera_move, self.dramatic_purpose,
                                  self.entry_state, self.exit_state, self.screen_direction, self.transition,
                                  self.sound, self.blocking, self.continuity)
        self.select_tool("pen"); self.set_board_mode("narrative"); self.reload_list()

    def shots(self): return self.board.setdefault("shots", [])

    def reload_list(self):
        self.list.clear()
        for index, shot in enumerate(self.shots()):
            item = QListWidgetItem(f"镜头 {index + 1:02d}  ·  {shot.get('shot_size') or '中景'}")
            item.setToolTip(str(shot.get("visual") or shot.get("description") or "")); self.list.addItem(item)
        if self.list.count(): self.list.setCurrentRow(0)
        self.refresh_sheet()

    def set_view(self, index):
        self.work_stack.setCurrentIndex(index); self.sheet_view.setChecked(index == 0); self.draw_view.setChecked(index == 1)
        if index == 0: self.refresh_sheet()

    def set_board_mode(self, mode):
        self.board["storyboard_mode"] = mode
        for key, button in self.mode_buttons.items(): button.setChecked(key == mode)
        self.refresh_sheet()

    def refresh_sheet(self):
        if not hasattr(self, "sheet"): return
        shots = self.shots()
        if not self.annotation_toggle.isChecked():
            shots = [{**shot, "annotations": []} for shot in shots]
        columns = 4 if self.board.get("storyboard_mode") == "action" else 3
        self.sheet.set_shots(shots, self.current_index, columns)

    def _capture_state(self):
        if self.current_index < 0: return
        self.states[self.current_index] = {
            "strokes": json.loads(json.dumps(self.canvas.strokes, default=lambda p: [p.x(), p.y()])),
            "shot_size": self.shot_size.currentText(), "duration": self.duration.value(),
            "notes": self.notes.toPlainText(),
            "camera_angle": self.camera_angle.text(), "lens": self.lens.text(),
            "camera": self.camera_move.text(), "dramatic_purpose": self.dramatic_purpose.text(),
            "entry_state": self.entry_state.text(), "exit_state": self.exit_state.text(),
            "screen_direction": self.screen_direction.text(), "transition": self.transition.text(),
            "sound": self.sound.text(), "blocking": self.blocking.toPlainText(),
            "continuity_notes": self.continuity.toPlainText(),
        }

    def switch_shot(self, index):
        self._capture_state(); self.current_index = index
        if index < 0 or index >= len(self.shots()): return
        shot = self.shots()[index]; state = self.states.get(index, {})
        self.canvas.set_background(str(shot.get("draft_panel") or ""))
        raw_strokes = state.get("strokes", [])
        self.canvas.strokes = [{**s, "points": [QPointF(*p) for p in s.get("points", [])]}
                               for s in raw_strokes]
        self.shot_size.setCurrentText(str(state.get("shot_size") or shot.get("shot_size") or "中景"))
        self.duration.setValue(int(float(state.get("duration") or shot.get("duration") or 5)))
        self.notes.setPlainText(str(state.get("notes") or shot.get("visual") or shot.get("description") or ""))
        values = {
            self.camera_angle: state.get("camera_angle") or shot.get("camera_angle") or "",
            self.lens: state.get("lens") or shot.get("lens") or "",
            self.camera_move: state.get("camera") or shot.get("camera") or shot.get("camera_slot") or "固定镜头",
            self.dramatic_purpose: state.get("dramatic_purpose") or shot.get("dramatic_purpose") or "",
            self.entry_state: state.get("entry_state") or shot.get("entry_state") or "",
            self.exit_state: state.get("exit_state") or shot.get("exit_state") or "",
            self.screen_direction: state.get("screen_direction") or shot.get("screen_direction") or "",
            self.transition: state.get("transition") or ((shot.get("transition") or {}).get("type") if isinstance(shot.get("transition"), dict) else shot.get("transition")) or "cut",
            self.sound: state.get("sound") or shot.get("sound") or "",
            self.blocking: state.get("blocking") or shot.get("blocking") or "",
            self.continuity: state.get("continuity_notes") or shot.get("continuity_notes") or "",
        }
        for widget, value in values.items():
            (widget.setPlainText if isinstance(widget, QTextEdit) else widget.setText)(str(value))
        self.inspector_title.setText(f"镜头 {index + 1:02d}")
        self.refresh_sheet()
        self.canvas.update()

    def select_tool(self, tool):
        self.canvas.set_tool(tool)
        for key, button in self.tool_buttons.items(): button.setChecked(key == tool)

    def change_width(self, value): self.canvas.width = value
    def canvas_undo(self): self.canvas.undo()
    def canvas_redo(self): self.canvas.redo()
    def clear_canvas(self): self.canvas.clear()

    def generate_with_ai(self):
        idea = self.idea.toPlainText().strip()
        if not idea:
            QMessageBox.information(self, "AI 分镜稿", "先写一句你的故事想法。")
            return
        manager = get_ai_manager()
        providers = manager.registry.by_capability("chat")
        provider = next((item for item in providers if item.name == "openai"),
                        providers[0] if providers else None)
        if provider is None:
            QMessageBox.warning(self, "缺少 GPT", "请先在设置中配置 OpenAI 文本模型。")
            return
        try:
            from api_config import get as api_get
            model = api_get("llm").default_model or "gpt-5.5"
        except Exception:
            model = "gpt-5.5"
        count = self.shot_count.value(); style = self.ai_style.currentText()
        system = (
            "你是电影导演和分镜师。先按叙事节拍和场面调度设计镜头，再输出可执行的手绘分镜计划。"
            "每镜只承担一个明确剧情功能，只允许一种主导运镜；动作必须写清开始状态、过程和结束状态。"
            "相邻镜头保持人物身份、服装、道具持有状态、光线方向、空间轴线与屏幕方向连续。"
            "只输出一个 JSON 对象，不要 Markdown。结构必须为："
            '{"title":"片名","summary":"故事梗概","visual_bible":"统一人物、服装、场景、色彩和画风设定",'
            '"shots":[{"dramatic_purpose":"本镜剧情功能","shot_size":"景别","camera_angle":"机位角度",'
            '"lens":"焦段","duration":5,"visual":"主体位置、前中后景、关键道具与表情",'
            '"entry_state":"开始姿态与道具状态","action":"一个可见动作过程","exit_state":"明确结束姿态",'
            '"camera":"一种运镜或固定镜头","screen_direction":"人物朝向和运动方向",'
            '"blocking":"人物与道具空间位置","continuity_notes":"前后镜必须匹配的具体锚点",'
            '"transition":"切出方式","sound":"环境音、拟音或情绪声音","dialogue":"台词或空字符串",'
            '"annotations":[{"type":"action/camera/gaze/effect/sound","points":[[0.1,0.5],[0.7,0.5]],"label":"短标签"}],'
            '"image_prompt":"可直接交给图像模型的中文提示词"}]}。'
            f"必须正好 {count} 个镜头，视觉风格为{style}。镜头覆盖应包含建立、关系、细节、反应、转折或余韵，避免连续重复构图。"
        )
        self.ai_generate.setEnabled(False); self.ai_progress.setValue(3)
        self.ai_status.setText("GPT 正在创作故事与镜头…")
        try:
            self._ai_plan_task = manager.submit(provider.name, TaskRequest(
                operation="chat", inputs={"messages":[
                    {"role":"system", "content":system},
                    {"role":"user", "content":idea},
                ]}, params={"model":model}, metadata={"purpose":"ai_storyboard_plan"},
                use_cache=False))
            self._ai_timer.start()
        except Exception as error:
            self.ai_generate.setEnabled(True)
            QMessageBox.warning(self, "AI 分镜提交失败", str(error))

    def _poll_ai(self):
        if self._ai_plan_task is not None:
            handle = self._ai_plan_task
            self.ai_progress.setValue(max(3, min(28, int(handle.progress * 28))))
            if not handle.is_finished:
                return
            self._ai_plan_task = None
            if not handle.is_success or not handle.result:
                self._ai_failed(handle.result.error if handle.result else "GPT 生成失败")
                return
            try:
                plan = extract_json(str(handle.result.data or ""))
                raw_shots = plan.get("shots") or []
                if not raw_shots:
                    raise ValueError("GPT 没有返回镜头")
                self.board["title"] = str(plan.get("title") or self.board.get("title") or "AI 分镜稿")
                self.board["summary"] = str(plan.get("summary") or "")
                bible_data = self.board.setdefault("visual_bible", {})
                if not isinstance(bible_data, dict):
                    bible_data = {}; self.board["visual_bible"] = bible_data
                bible_data["ai_storyboard"] = str(plan.get("visual_bible") or "")
                self.board["shots"] = []
                for index, value in enumerate(raw_shots):
                    self.board["shots"].append({
                        "id": f"shot-{uuid.uuid4().hex[:10]}", "number": index + 1,
                        "duration": float(value.get("duration") or 5),
                        "shot_size": str(value.get("shot_size") or "中景"),
                        "visual": str(value.get("visual") or ""),
                        "dramatic_purpose": str(value.get("dramatic_purpose") or ""),
                        "camera_angle": str(value.get("camera_angle") or "平视"),
                        "lens": str(value.get("lens") or "35mm"),
                        "camera": str(value.get("camera") or "固定镜头"),
                        "camera_slot": str(value.get("camera_slot") or "自由机位"),
                        "entry_state": str(value.get("entry_state") or ""),
                        "action": str(value.get("action") or ""),
                        "exit_state": str(value.get("exit_state") or ""),
                        "screen_direction": str(value.get("screen_direction") or ""),
                        "blocking": str(value.get("blocking") or ""),
                        "continuity_notes": str(value.get("continuity_notes") or ""),
                        "transition": {"type": str(value.get("transition") or "cut"), "duration": 0.0},
                        "sound": str(value.get("sound") or ""),
                        "annotations": value.get("annotations") if isinstance(value.get("annotations"), list) else [],
                        "dialogue": str(value.get("dialogue") or ""),
                        "image_prompt": str(value.get("image_prompt") or value.get("visual") or ""),
                        "draft_panel": "", "draft_source": "ai", "assets": [],
                    })
                self.states.clear(); self.current_index = -1; self.reload_list()
                self._ai_image_queue = list(range(len(self.shots())))
                self._ai_previous_image = ""; self.ai_progress.setValue(30)
                self._submit_next_ai_image()
            except Exception as error:
                self._ai_failed(f"分镜结构解析失败：{error}")
            return
        finished = []
        for task_id, task in list(self._ai_image_tasks.items()):
            handle = task["handle"]
            if not handle.is_finished:
                continue
            finished.append(task_id); index = task["index"]
            if not handle.is_success or not handle.result:
                self._ai_failed(handle.result.error if handle.result else f"镜头 {index + 1} 生成失败")
                return
            value = handle.result.data
            if isinstance(value, (list, tuple)): value = value[0] if value else ""
            path = str(value or "")
            self.shots()[index]["draft_panel"] = path
            self.shots()[index]["draft_source"] = "ai"
            self._ai_previous_image = path
            done = len(self.shots()) - len(self._ai_image_queue)
            self.ai_progress.setValue(30 + int(done / max(1, len(self.shots())) * 68))
        for task_id in finished: self._ai_image_tasks.pop(task_id, None)
        if finished:
            self.reload_list(); self._submit_next_ai_image()

    def _submit_next_ai_image(self):
        if self._ai_image_tasks:
            return
        if not self._ai_image_queue:
            self._ai_timer.stop(); self.ai_progress.setValue(100)
            self.ai_status.setText("整套 AI 分镜已生成，可逐格修改或直接保存")
            self.ai_generate.setEnabled(True); self.saved.emit(); return
        manager = get_ai_manager(); providers = manager.registry.by_capability(
            "image_edit" if self._ai_previous_image else "text_to_image")
        provider = next((item for item in providers
                         if item.name == self.image_provider_name), None)
        if not self.image_provider_name and providers:
            provider = providers[0]
            self.image_provider_name = provider.name
            self.board.setdefault("production_models", {})["image_provider"] = provider.name
        if provider is None:
            self._ai_failed(
                f"故事板锁定的图片模型“{self.image_provider_name or '未选择'}”"
                "当前不可用或不支持参考图编辑；系统不会切换到其他模型。")
            return
        index = self._ai_image_queue.pop(0); shot = self.shots()[index]
        bible = str((self.board.get("visual_bible") or {}).get("ai_storyboard") or "")
        prompt = (
            f"生成电影分镜画面，第 {index + 1}/{len(self.shots())} 镜。\n"
            f"统一视觉圣经：{bible}\n景别：{shot.get('shot_size')}\n"
            f"机位与焦段：{shot.get('camera_angle') or '平视'}，{shot.get('lens') or '35mm'}。\n"
            f"构图：{shot.get('image_prompt') or shot.get('visual')}\n场面调度：{shot.get('blocking')}。\n"
            f"动作节点：{shot.get('entry_state')} → {shot.get('action')} → {shot.get('exit_state')}。\n"
            f"屏幕方向：{shot.get('screen_direction')}。连续性锚点：{shot.get('continuity_notes')}。\n"
            f"运镜意图：{shot.get('camera') or shot.get('camera_slot')}。风格：{self.ai_style.currentText()}。"
            "黑白铅笔制作分镜，轮廓明确，适量结构线和灰阶，只精画叙事关键区域，前中后景清楚，动作姿态可读。"
            "不要生成彩色导演箭头；箭头由软件独立叠加。画面中不要出现编号、字幕、水印和说明文字。"
            "严格保持人物身份、服装、道具、场景结构、光线方向和轴线连续。"
        )
        operation = "image_edit" if self._ai_previous_image else "text_to_image"
        inputs = {"prompt": prompt}
        if self._ai_previous_image:
            inputs.update({"image": self._ai_previous_image,
                           "images": [self._ai_previous_image]})
        self.ai_status.setText(
            f"{provider.name} 正在生成镜头 {index + 1}/{len(self.shots())}…")
        try:
            handle = manager.submit(provider.name, TaskRequest(
                operation=operation, inputs=inputs,
                params={"size":"1536x1024", "quality":"medium", "n":1},
                metadata={"purpose":"ai_storyboard_image", "shot_id":shot["id"]},
                use_cache=False))
            self._ai_image_tasks[handle.id] = {"handle":handle, "index":index}
        except Exception as error:
            self._ai_failed(str(error))

    def _ai_failed(self, message):
        self._ai_timer.stop(); self._ai_plan_task = None; self._ai_image_tasks.clear()
        self.ai_generate.setEnabled(True); self.ai_status.setText("生成中断")
        QMessageBox.warning(self, "AI 分镜生成失败", str(message))

    def add_shot(self):
        self._capture_state(); number = len(self.shots()) + 1
        self.shots().append({"id": f"shot-{uuid.uuid4().hex[:10]}", "number": number,
                             "duration": 5.0, "shot_size": "中景", "visual": "",
                             "draft_panel": "", "assets": []})
        self.reload_list(); self.list.setCurrentRow(number - 1)

    def _folder(self):
        board_id = str(self.board.get("id") or "untitled")
        folder = Path.cwd() / "data" / "storyboard_sketches" / board_id
        folder.mkdir(parents=True, exist_ok=True); return folder

    def export_current(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出分镜格", "storyboard-panel.png", "PNG (*.png)")
        if path: self.canvas.render_image().save(path, "PNG")

    def export_sheet(self):
        self._capture_state(); self._apply_states()
        path, _ = QFileDialog.getSaveFileName(self, "导出整页故事板", "storyboard-sheet.png", "PNG (*.png)")
        if path: self.sheet.render_image().save(path, "PNG")

    def _apply_states(self):
        for index, state in self.states.items():
            if index >= len(self.shots()): continue
            shot = self.shots()[index]
            for key, value in state.items():
                if key != "strokes": shot[key] = value
            shot["visual"] = state.get("notes", shot.get("visual", ""))
            shot["duration"] = float(state.get("duration", shot.get("duration", 5)))
            transition = state.get("transition")
            if transition: shot["transition"] = {"type": transition, "duration": 0.0}
        self.refresh_sheet()

    def save_all(self):
        self._capture_state(); self._apply_states(); folder = self._folder()
        for index, shot in enumerate(self.shots()):
            state = self.states.get(index)
            if not state: continue
            current = self.current_index
            if index != current: self.switch_shot(index)
            path = folder / f"shot_{index + 1:03d}.png"
            self.canvas.render_image().save(str(path), "PNG")
            shot["draft_panel"] = str(path); shot["shot_size"] = state["shot_size"]
        self.saved.emit(); self.accept()
