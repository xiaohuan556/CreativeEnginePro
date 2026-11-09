"""Single registry for workbench actions, shortcuts and user remapping."""
import json
from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QTableWidget, QTableWidgetItem,
                            QKeySequenceEdit, QDialogButtonBox, QPushButton, QMessageBox, QHeaderView)


def normalized_shortcut(text):
    text = text.replace('⇧', 'Shift').replace('⌘', 'Ctrl')
    return QKeySequence(text).toString(QKeySequence.SequenceFormat.PortableText)


class ImageCommandsOperations:
    def _ensure_commands(self):
        if hasattr(self, '_commands'):
            return
        self._commands = {}
        self._remapped_defaults = set()
        try:
            self._shortcut_overrides = json.loads(QSettings('CreativeEnginePro', 'ImageWorkbench').value('shortcuts', '{}'))
        except (ValueError, TypeError):
            self._shortcut_overrides = {}

    def _command_path(self, menu, text):
        parts = [text]
        current = menu
        while current:
            title = current.title() if hasattr(current, 'title') else ''
            if not title and hasattr(current, 'text'):
                title = current.text()
            if title:
                parts.insert(0, title)
            current = current.parent()
            if current is self:
                break
        return ' › '.join(parts)

    def register_command(self, menu, text, callback, shortcut=''):
        self._ensure_commands()
        action = menu.addAction(text)
        action.triggered.connect(callback)
        key = self._command_path(menu, text)
        self._commands[key] = {'action':action, 'default':normalized_shortcut(shortcut)}
        self._configure_shortcut(key)
        return action

    def _configure_shortcut(self, key):
        record = self._commands[key]
        value = normalized_shortcut(self._shortcut_overrides.get(key, record['default']))
        record['action'].setShortcut(QKeySequence(value))
        record['action'].setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.addAction(record['action'])
        self._remapped_defaults = {entry['default'] for identity, entry in self._commands.items()
                                   if entry['default'] and normalized_shortcut(self._shortcut_overrides.get(identity, entry['default'])) != entry['default']}

    def install_tool_shortcuts(self):
        from ui.image_editor import Tool
        self._ensure_commands()
        keys = {Tool.MOVE:'V', Tool.SELECT_RECT:'M', Tool.SELECT_ELLIPSE:'Shift+M', Tool.SELECT_LASSO:'L',
                Tool.POLY_LASSO:'P', Tool.WAND:'W', Tool.QUICK_SELECT:'A', Tool.BRUSH:'B', Tool.ERASER:'E',
                Tool.TEXT:'T', Tool.SHAPE_RECT:'R', Tool.SHAPE_ELLIPSE:'O', Tool.EYEDROPPER:'I', Tool.CROP:'C',
                Tool.GRADIENT:'G', Tool.CLONE:'S', Tool.HEAL:'J'}
        keys[Tool.OBJECT_SELECT] = 'Q'
        keys[Tool.SHAPE_LINE] = 'U'
        self._tool_actions = {}
        for tool, button in self._tool_buttons.items():
            action = QAction(button.text(), self)
            action.triggered.connect(lambda _=False, value=tool:self.set_tool(value))
            identity = '工具 › ' + tool.name
            self._commands[identity] = {'action':action, 'default':keys[tool]}
            self._tool_actions[tool] = action
            def sync_badge(b=button, a=action):
                if hasattr(b, 'set_shortcut'):
                    b.set_shortcut(a.shortcut().toString(QKeySequence.SequenceFormat.PortableText))
            action.changed.connect(sync_badge)
            self._configure_shortcut(identity)
            sync_badge()

    def suppress_remapped_key(self, event):
        sequence = QKeySequence(event.keyCombination()).toString(QKeySequence.SequenceFormat.PortableText)
        return sequence in getattr(self, '_remapped_defaults', set())

    def apply_shortcuts(self, mapping, persist=True):
        self._ensure_commands()
        combined = {key:normalized_shortcut(mapping.get(key, self._shortcut_overrides.get(key, record['default'])))
                    for key, record in self._commands.items()}
        assigned = {}
        for key, sequence in combined.items():
            if sequence and sequence in assigned:
                raise ValueError(f'{sequence} 已用于“{assigned[sequence]}”，不能同时分配给“{key}”')
            if sequence:
                assigned[sequence] = key
        self._shortcut_overrides = combined
        for key in self._commands:
            self._configure_shortcut(key)
        if persist:
            QSettings('CreativeEnginePro', 'ImageWorkbench').setValue('shortcuts', json.dumps(combined, ensure_ascii=False))

    def edit_shortcuts(self):
        self._ensure_commands()
        dialog = QDialog(self)
        dialog.setWindowTitle('键盘快捷键')
        dialog.resize(700, 600)
        layout = QVBoxLayout(dialog)
        table = QTableWidget(len(self._commands), 2)
        table.setHorizontalHeaderLabels(['操作', '快捷键（留空可禁用）'])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.setColumnWidth(1, 200)
        controls = {}
        for row, (key, record) in enumerate(self._commands.items()):
            item = QTableWidgetItem(key)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, 0, item)
            control = QKeySequenceEdit(record['action'].shortcut())
            controls[key] = control
            table.setCellWidget(row, 1, control)
        layout.addWidget(table)
        reset = QPushButton('恢复默认快捷键')
        reset.clicked.connect(lambda: [control.setKeySequence(QKeySequence(self._commands[key]['default'])) for key,control in controls.items()])
        layout.addWidget(reset)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        def accept():
            try:
                self.apply_shortcuts({key:control.keySequence().toString(QKeySequence.SequenceFormat.PortableText) for key,control in controls.items()})
            except ValueError as exc:
                QMessageBox.information(dialog, '快捷键冲突', str(exc))
                return
            dialog.accept()
        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()
