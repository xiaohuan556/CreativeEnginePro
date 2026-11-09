"""Agent-only interface for batch work on the current image template."""
from dataclasses import replace
from pathlib import Path
from secrets import randbits

from PyQt6.QtCore import QThread, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QInputDialog,
                             QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QProgressBar,
                             QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from core.image_template_batch import BatchOptions, filename_prefix, image_slots, run_template_batch


_BATCH_STYLE = """
QDialog#templateBatch, #templateBatch QDialog, QDialog#batchSamplePreview { background: #171a21; }
#templateBatch QLabel, #batchSamplePreview QLabel { color: #e5eaf4; background: transparent; }
#templateBatch QLabel[batchRole="title"] { font-size: 19px; font-weight: 600; }
#templateBatch QLabel[batchRole="muted"], #batchSamplePreview QLabel[batchRole="muted"] {
    color: #a4aec0;
}
#templateBatch QLabel[batchRole="status"] {
    background: #202735; border: 1px solid #303a4b; border-radius: 7px; padding: 8px 10px;
}
#templateBatch QWidget[batchRole="card"] {
    background: #20242e; border: 1px solid #343b49; border-radius: 9px;
}
#templateBatch QLabel[batchRole="image"], #batchSamplePreview QLabel[batchRole="image"] {
    background: #101219; border-radius: 5px;
}
#templateBatch QPlainTextEdit, #templateBatch QLineEdit, #templateBatch QListWidget,
#templateBatch QSpinBox {
    background: #12151c; color: #e5eaf4; border: 1px solid #3a4354;
    border-radius: 7px; padding: 7px 9px; selection-background-color: #3e65ab;
}
#templateBatch QPlainTextEdit:focus, #templateBatch QLineEdit:focus { border-color: #6e9cf4; }
#templateBatch QPushButton, #batchSamplePreview QPushButton {
    color: #e6ebf4; background: #293241; border: 1px solid #3a4658;
    border-radius: 6px; padding: 6px 11px; min-height: 17px;
}
#templateBatch QPushButton:hover, #batchSamplePreview QPushButton:hover { background: #364359; }
#templateBatch QPushButton[batchRole="agent"] {
    color: #c8dcff; background: #253750; border-color: #405c83;
}
#templateBatch QPushButton[batchRole="agent"]:hover { background: #304969; }
#templateBatch QPushButton[batchRole="primary"], #batchSamplePreview QPushButton[batchRole="primary"] {
    background: #4e7bd6; border-color: #6996f1; color: #fff; font-weight: 600;
}
#templateBatch QPushButton[batchRole="primary"]:hover, #batchSamplePreview QPushButton[batchRole="primary"]:hover {
    background: #5d89e4;
}
#templateBatch QPushButton:disabled {
    background: #242a34; border-color: #323946; color: #788496;
}
#templateBatch QProgressBar {
    background: #202735; border: none; border-radius: 3px; max-height: 6px;
}
#templateBatch QProgressBar::chunk { background: #6e9cf4; border-radius: 3px; }
"""


class BatchWorker(QThread):
    outcome = pyqtSignal(object, str)
    progress = pyqtSignal(int, int, str)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            result = self.operation(self.isInterruptionRequested, self.progress.emit)
        except Exception as error:
            self.outcome.emit(None, str(error))
        else:
            self.outcome.emit(result, '')


class SampleImage(QLabel):
    opened = pyqtSignal()

    def __init__(self, image, parent=None):
        super().__init__(parent)
        self.original = QPixmap.fromImage(image)
        self.setProperty('batchRole', 'image')
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setPixmap(self.original.scaled(event.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.opened.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class TemplateBatchDialog(QDialog):
    def __init__(self, editor, snapshot):
        super().__init__(editor)
        self.editor, self.snapshot = editor, snapshot
        self.worker = None
        self.instructions = []
        self.awaiting_reply = False
        self.source_folders = []
        self.extra_instruction = ''
        self.prefix_override = None
        self.max_kb_override = None
        saved = snapshot.get('batch_template')
        self.saved = saved if isinstance(saved, dict) else {}
        self.default_name = self.saved.get('name') or '素材_{date}_'
        self.default_max_kb = self.saved.get('max_kb', 500)
        self.delivery_paths = ()
        self.request_hint = None
        self.prepared = None
        self.setWindowTitle('图片批处理 Agent')
        self.setObjectName('templateBatch')
        self.setStyleSheet(_BATCH_STYLE)
        self.setMinimumWidth(560)
        self.resize(690, 245)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 17, 20, 17)
        root.setSpacing(11)
        header = QHBoxLayout()
        title = QLabel('图片批处理 Agent')
        title.setProperty('batchRole', 'title')
        header.addWidget(title)
        header.addStretch()
        canvas = QLabel(f'当前模板 · {snapshot["w"]} × {snapshot["h"]}')
        canvas.setProperty('batchRole', 'muted')
        header.addWidget(canvas)
        root.addLayout(header)
        source_row = QHBoxLayout()
        source_row.addWidget(QLabel('素材目录'))
        self.source_display = QLineEdit()
        self.source_display.setAccessibleName('素材目录')
        self.source_display.setPlaceholderText('选择或粘贴目录，多个目录用 | 分隔')
        self.source_display.textEdited.connect(self._invalidate_prepared)
        source_row.addWidget(self.source_display, 1)
        self.source_button = QPushButton('选择素材…')
        self.source_button.clicked.connect(self._choose_sources)
        source_row.addWidget(self.source_button)
        root.addLayout(source_row)

        composer_actions = QHBoxLayout()
        self.rename_button = QPushButton('命名')
        self.rename_button.setToolTip(self.default_name)
        self.rename_button.clicked.connect(self._choose_prefix)
        composer_actions.addWidget(self.rename_button)
        self.limit_button = QPushButton(f'单张 ≤ {self.default_max_kb} KB')
        self.limit_button.clicked.connect(self._choose_limit)
        composer_actions.addWidget(self.limit_button)
        self.notes_button = QPushButton('补充要求')
        self.notes_button.clicked.connect(self._edit_notes)
        composer_actions.addWidget(self.notes_button)
        composer_actions.addStretch()
        self.action = QPushButton('交给 Agent')
        self.action.setProperty('batchRole', 'agent')
        self.action.clicked.connect(self._submit)
        composer_actions.addWidget(self.action)
        root.addLayout(composer_actions)
        template_hint = QLabel('排版与其余样式沿用模板')
        template_hint.setProperty('batchRole', 'muted')
        template_hint.setToolTip('换图保留图层位置、裁切和样式。识别到的时钟图层会按背景自动选色。')
        root.addWidget(template_hint)
        self.status = QLabel('选择素材目录后，Agent 会按当前模板生成样图。')
        self.status.setProperty('batchRole', 'status')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        root.addWidget(self.progress_bar)
        self.preview_area = QWidget()
        self.previews = QHBoxLayout(self.preview_area)
        self.previews.setContentsMargins(0, 0, 0, 0)
        self.previews.setSpacing(12)
        self.preview_area.hide()
        root.addWidget(self.preview_area, 1)

        output = QHBoxLayout()
        root.addLayout(output)
        output.addWidget(QLabel('输出目录'))
        self.output_dir = QLineEdit()
        self.output_dir.setPlaceholderText('输出目录 · Agent 配好后可修改')
        self.output_dir.setAccessibleName('输出目录')
        output.addWidget(self.output_dir, 1)
        self.browse = QPushButton('选择目录…')
        self.browse.clicked.connect(self._choose_output)
        output.addWidget(self.browse)
        self.export_button = QPushButton('开始导出')
        self.export_button.setProperty('batchRole', 'primary')
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self._start_export)
        output.addWidget(self.export_button)

        self.result_actions = QWidget()
        row = QHBoxLayout(self.result_actions)
        row.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.result_actions)
        self.result_actions.hide()
        self.folder_button = QPushButton('打开成品')
        self.folder_button.clicked.connect(self._open_folder)
        self.folder_button.hide()
        row.addWidget(self.folder_button)
        self.duo_button = QPushButton('交付到 Duo')
        self.duo_button.clicked.connect(self._deliver)
        self.duo_button.hide()
        row.addWidget(self.duo_button)
        row.addStretch()
        self.source_button.setFocus()

    def _submit(self):
        if self.worker is not None:
            self._stop()
            return
        folders = self._typed_sources()
        if any(not Path(folder).is_absolute() for folder in folders):
            self.status.setText('请粘贴完整的素材目录路径。')
            return
        self._set_source_folders(folders)
        if not self.source_folders and not self.extra_instruction:
            self._choose_sources()
        if not self.source_folders and not self.extra_instruction:
            return
        locations = '、'.join(f'"{path}"' for path in self.source_folders)
        self.instructions = [f'用当前模板处理这些素材目录 {locations}。'] if locations else []
        if self.extra_instruction:
            self.instructions.append(self.extra_instruction)
        self.awaiting_reply = False
        self.notes_button.setText('补充要求 · 已填写' if self.extra_instruction else '补充要求')
        self.prepared = None
        self.folder_button.hide()
        self.duo_button.hide()
        self.result_actions.hide()
        while self.previews.count():
            self.previews.takeAt(0).widget().deleteLater()
        self.preview_area.hide()
        self.status.setText('Agent 正在查看模板和素材目录…')
        self._launch(self._run_instruction)

    def _launch(self, operation):
        self.action.setText('停止')
        self._set_inputs_enabled(False)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()
        worker = BatchWorker(operation, self)
        self.worker = worker
        worker.progress.connect(self._progress)
        worker.outcome.connect(self._finished)
        worker.start()

    def _set_inputs_enabled(self, enabled):
        for widget in (self.source_display, self.source_button, self.rename_button, self.limit_button, self.notes_button,
                       self.output_dir, self.browse):
            widget.setEnabled(enabled)
        self.export_button.setEnabled(enabled and self.prepared is not None)

    def _choose_sources(self):
        self._set_source_folders(self._typed_sources())
        if not self.source_folders:
            folder = QFileDialog.getExistingDirectory(self, '选择素材目录')
            if folder:
                self._set_source_folders([folder])
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('素材目录')
        dialog.resize(510, 240)
        layout = QVBoxLayout(dialog)
        folders = QListWidget(dialog)
        folders.addItems(self.source_folders)
        layout.addWidget(folders)
        actions = QHBoxLayout()
        add = QPushButton('添加目录…', dialog)
        remove = QPushButton('移除选中', dialog)
        actions.addWidget(add)
        actions.addWidget(remove)
        actions.addStretch()
        layout.addLayout(actions)

        def add_folder():
            folder = QFileDialog.getExistingDirectory(dialog, '添加素材目录')
            if folder and all(folders.item(i).text() != folder for i in range(folders.count())):
                folders.addItem(folder)

        def remove_folder():
            index = folders.currentRow()
            if index >= 0:
                folders.takeItem(index)

        add.clicked.connect(add_folder)
        remove.clicked.connect(remove_folder)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._set_source_folders([folders.item(i).text() for i in range(folders.count())])

    def _set_source_folders(self, folders):
        if folders == self.source_folders:
            return
        self.source_folders = folders
        self.source_display.setText(' | '.join(folders))
        self.source_display.setCursorPosition(0)
        self.source_display.setToolTip('\n'.join(folders))
        self.source_button.setText('管理目录…' if folders else '选择素材…')
        self._invalidate_prepared()

    def _typed_sources(self):
        values = (value.strip().strip('"“”') for value in self.source_display.text().split('|'))
        return list(dict.fromkeys(value for value in values if value))

    def _choose_prefix(self):
        current = self.prefix_override or (self.prepared[1].name if self.prepared else self.default_name)
        value, accepted = QInputDialog.getText(self, '成品命名', '文件名前缀，自动追加序号。日期可用 {date}。',
                                               text=current)
        if accepted:
            try:
                filename_prefix(value.strip())
            except ValueError as error:
                self.status.setText(str(error))
                return
            self.prefix_override = value.strip()
            self.rename_button.setToolTip(self.prefix_override)
            self.status.setText(f'成品命名将使用「{self.prefix_override}」加序号。')

    def _choose_limit(self):
        current = self.max_kb_override or (self.prepared[1].max_kb if self.prepared else self.default_max_kb)
        limit, accepted = QInputDialog.getInt(self, 'JPG 大小上限', '单张文件大小（KB）', current, 1, 100000)
        if accepted:
            self.max_kb_override = limit
            self.limit_button.setText(f'单张 ≤ {limit} KB')
            if limit != current:
                self._invalidate_prepared()

    def _edit_notes(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('补充要求')
        dialog.resize(530, 220)
        layout = QVBoxLayout(dialog)
        if self.awaiting_reply:
            question = QLabel(self.status.text())
            question.setTextFormat(Qt.TextFormat.PlainText)
            question.setWordWrap(True)
            layout.addWidget(question)
        notes = QPlainTextEdit(self.extra_instruction, dialog)
        notes.setPlaceholderText('可填写要去掉的字、配图方式或对 Agent 的补充说明。')
        layout.addWidget(notes, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            value = notes.toPlainText().strip()
            if value != self.extra_instruction:
                self.extra_instruction = value
                self.notes_button.setText('补充要求 · 已填写' if value else '补充要求')
                self.notes_button.setToolTip(value)
                self._invalidate_prepared()

    def _invalidate_prepared(self):
        if self.prepared is not None:
            self.prepared = None
            self.export_button.setEnabled(False)
            self.status.setText('设置已更新，再交给 Agent 生成一版样图。')

    def _run_instruction(self, cancelled, progress):
        from ai.image_batch_agent import plan_batch_instruction

        layers = [{'id': layer['id'], 'name': layer['name']} for layer in image_slots(self.snapshot)]
        plan = plan_batch_instruction('\n'.join(self.instructions), cancelled, layers=layers)
        if cancelled() or 'question' in plan:
            return plan, None, None, None
        options = BatchOptions(plan['bindings'], plan['mode'],
                               tuple(plan.get('targets', self.saved.get('targets') or ())),
                               self.prefix_override or plan.get('prefix') or self.default_name,
                               plan.get('first_index', self.saved.get('first_index', 1)),
                               self.max_kb_override or plan.get('max_kb', self.default_max_kb),
                               pairing=plan.get('pairing', 'same'),
                               cross_layers=plan.get('cross_layers', ()), seed=randbits(64),
                               selected_jobs=plan.get('selected_jobs', ()),
                               input_roots=plan.get('input_roots', ()))
        source = Path(plan['source']).resolve() if 'source' in plan else None
        anchor = source or options.bindings[0].folder
        destination = (Path(plan['destination']).resolve() if 'destination' in plan
                       else anchor.parent / f'{anchor.name}_导出')
        result = run_template_batch(self.snapshot, options, source, destination, preview=True,
                                    cancelled=cancelled, progress=progress)
        return plan, result, destination, (options, source, result.jobs, result.source_errors)

    def _choose_output(self):
        folder = QFileDialog.getExistingDirectory(self, '选择输出目录', self.output_dir.text().strip())
        if folder:
            self.output_dir.setText(folder)

    def _start_export(self):
        if self.worker is not None or self.prepared is None:
            return
        value = self.output_dir.text().strip().strip('"')
        if not value:
            self.status.setText('请选择或输入输出目录。')
            return
        destination = Path(value).expanduser()
        if not destination.is_absolute():
            self.status.setText('请填写完整的输出目录。')
            return
        plan, options, source, jobs, source_errors = self.prepared
        if self.prefix_override:
            options = replace(options, name=self.prefix_override)
        self.folder_button.hide()
        self.duo_button.hide()
        self.result_actions.hide()
        self.status.setText('正在按样图方案导出…')

        def export(cancelled, progress):
            result = run_template_batch(self.snapshot, options, source, destination,
                                        cancelled=cancelled, progress=progress, planned_jobs=jobs,
                                        source_errors=source_errors)
            return plan, result, destination, None

        self._launch(export)

    def _progress(self, position, total, theme):
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(position - 1)
        self.status.setText(f'处理中 {position}/{total} · {theme}')

    def _stop(self):
        self.worker.requestInterruption()
        self.action.setEnabled(False)
        self.status.setText('正在停止…')

    def reject(self):
        if self.worker:
            self._stop()
            return
        super().reject()

    def _finished(self, outcome, error):
        worker = self.worker
        worker.wait()
        stopped = worker.isInterruptionRequested()
        self.worker = None
        worker.deleteLater()
        self.progress_bar.hide()
        self.action.setText('交给 Agent')
        self.action.setEnabled(True)
        self._set_inputs_enabled(True)
        if error:
            self.status.setText('已停止。' if stopped else error)
            return
        plan, result, self.destination, prepared = outcome
        if result is None:
            self.awaiting_reply = not stopped and 'question' in plan
            if self.awaiting_reply:
                self.notes_button.setText('回答 Agent…')
            self.status.setText('已停止。' if stopped else plan['question'])
            return
        self.result = result
        self.request_hint = plan.get('request_id')
        if prepared is not None:
            self.rename_button.setToolTip(prepared[0].name)
            self.limit_button.setText(f'单张 ≤ {prepared[0].max_kb} KB')
            if plan.get('destination') or not self.output_dir.text().strip():
                self.output_dir.setText(str(self.destination))
            for name, image, size, reason in result.previews:
                self._show_sample(name, image, size, reason)
            self.preview_area.setVisible(bool(result.previews))
            if result.previews and not result.cancelled:
                self.prepared = (plan, *prepared)
                self.export_button.setEnabled(True)
            summary = f'已生成 {len(result.previews)} 张样图。双击可放大，确认输出目录后可开始导出。'
            if result.previews:
                available = self.screen().availableGeometry().height() - 55
                self.resize(self.width(), min(available, max(555, self.height())))
        else:
            summary = f'已导出 {len(result.ready)} 张，待复查 {len(result.review)} 张。'
            self.folder_button.setVisible(self.destination.is_dir())
            self.duo_button.setVisible(bool(result.ready or result.review)
                                       and hasattr(self.editor.window(), 'duo_delivery'))
            self.result_actions.setVisible(not self.folder_button.isHidden() or not self.duo_button.isHidden())
            if not result.cancelled and not result.errors:
                self.instructions.clear()
                if plan.get('delivery') and result.ready and not result.review and self.duo_button.isVisible():
                    self._deliver()
        if result.cancelled:
            summary = '已停止。' + summary
        if result.errors:
            summary += f'\n{len(result.errors)} 组未完成。{result.errors[0]}'
        self.status.setText(summary)

    def _show_sample(self, name, image, size, reason):
        card = QWidget()
        card.setProperty('batchRole', 'card')
        layout = QVBoxLayout(card)
        layout.setContentsMargins(9, 9, 9, 9)
        layout.setSpacing(7)
        sample = SampleImage(image, card)
        sample.setMinimumHeight(155)
        sample.setCursor(Qt.CursorShape.PointingHandCursor)
        sample.setToolTip(f'{name} · {size / 1000:.1f} KB' + (f' · {reason}' if reason else ''))
        sample.opened.connect(lambda: self._open_preview(name, image))
        layout.addWidget(sample, 1)
        label = QLabel(name)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(label)
        detail = QLabel(f'{size / 1000:.1f} KB' + (f' · {reason}' if reason else ''))
        detail.setProperty('batchRole', 'muted')
        detail.setTextFormat(Qt.TextFormat.PlainText)
        detail.setWordWrap(True)
        layout.addWidget(detail)
        enlarge = QPushButton('放大预览')
        enlarge.clicked.connect(lambda: self._open_preview(name, image))
        layout.addWidget(enlarge, alignment=Qt.AlignmentFlag.AlignLeft)
        self.previews.addWidget(card, 1)

    def _open_preview(self, name, image):
        dialog = QDialog(self)
        dialog.setObjectName('batchSamplePreview')
        dialog.setWindowTitle(name)
        area = self.screen().availableGeometry()
        dialog.resize(min(850, area.width()), min(750, area.height() - 50))
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.addWidget(SampleImage(image, dialog), 1)
        dialog.exec()

    def _open_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.destination)))

    def _deliver(self):
        paths = self.result.ready
        if self.result.review:
            choice = QMessageBox.question(self, '确认待复查图片',
                                          '已检查「待复查」图片，并将它们一并交付？',
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
                                          QMessageBox.StandardButton.Cancel,
                                          QMessageBox.StandardButton.Cancel)
            if choice == QMessageBox.StandardButton.Cancel:
                return
            if choice == QMessageBox.StandardButton.Yes:
                paths = sorted(paths + self.result.review, key=lambda path: (len(Path(path).name), Path(path).name))
        if paths:
            self.delivery_paths = tuple(paths)
            self.accept()


def open_template_batch(editor):
    if editor._text_editing:
        editor._end_text_edit(save=True)
    snapshot = editor._snapshot()
    if snapshot.get('artboards') or not image_slots(snapshot):
        QMessageBox.information(editor, '图片批处理 Agent', '请打开含可见静态图片图层的普通画布。')
        return
    dialog = TemplateBatchDialog(editor, snapshot)
    dialog.exec()
    if dialog.delivery_paths:
        delivery = getattr(editor.window(), 'duo_delivery', None)
        if delivery is not None:
            delivery.open_submission(dialog.delivery_paths, '图片工作台 · 模板批处理',
                                     request_id=dialog.request_hint)
