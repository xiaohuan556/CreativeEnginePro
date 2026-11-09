"""Per-document rotating recovery snapshots, written atomically off the UI thread."""
import json
import math
import os
import re
import time
import uuid
import weakref
from pathlib import Path
from datetime import datetime
from PyQt6.QtCore import QTimer, Qt, QCoreApplication
from PyQt6.QtWidgets import QApplication, QDialog, QVBoxLayout, QListWidget, QListWidgetItem, QDialogButtonBox, QLabel, QMessageBox
from core.image_project_io import ProjectSaveWorker

_EDITORS = weakref.WeakSet()


def recovery_folder():
    from utils.app_paths import work_root
    override = os.environ.get('CEP_IMAGE_RECOVERY_DIR')
    return Path(override).resolve() if override else work_root() / 'image_recovery'


def list_recovery_files(folder=None):
    records = []
    folder = Path(folder) if folder is not None else recovery_folder()
    if not folder.is_dir():
        return records
    for path in folder.glob('*.recovery.json'):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get('recovery_version') != 1 or not isinstance(data.get('snapshot'), dict):
                continue
            if not isinstance(data.get('document_id'), str) or not re.fullmatch(r'[0-9a-f]{32}', data['document_id']):
                continue
            if not isinstance(data.get('saved_at'), (int, float)) or not math.isfinite(data['saved_at']):
                continue
            datetime.fromtimestamp(data['saved_at'])
            if not isinstance(data.get('name', ''), str) or not isinstance(data.get('revision'), int):
                continue
            if not {'w', 'h', 'layers', 'transparent', 'bg'} <= data['snapshot'].keys():
                continue
            source = Path(data['source_path']) if data.get('source_path') else None
            if source and source.is_file() and source.stat().st_mtime >= data['saved_at']:
                continue
            data['path'] = str(path)
            records.append(data)
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            continue
    return sorted(records, key=lambda item: item['saved_at'], reverse=True)


def _backup_on_exit():
    from PyQt6 import sip
    workers = []
    for editor in tuple(_EDITORS):
        if not sip.isdeleted(editor):
            worker = editor.autosave_recovery(force=True)
            if worker is not None:
                workers.append(worker)
    for worker in workers:
        worker.wait()
    QCoreApplication.processEvents()


class ImageRecoveryOperations:
    def init_recovery(self):
        self._recovery_timer = QTimer(self)
        self._recovery_timer.setInterval(30000)
        self._recovery_timer.timeout.connect(self.autosave_recovery)
        self._recovery_timer.start()
        self._start_recovery_session()
        _EDITORS.add(self)
        app = QApplication.instance()
        if app is not None and not getattr(app, '_image_recovery_exit_hook', False):
            app.aboutToQuit.connect(_backup_on_exit)
            app._image_recovery_exit_hook = True
        QTimer.singleShot(0, self.refresh_recovery_notice)

    def _start_recovery_session(self, document_id=None):
        self._recovery_id = document_id or uuid.uuid4().hex
        self._recovery_revision = 0
        self._recovery_written_token = (0, '')
        self._recovery_saved_token = (0, '')
        self._recovery_worker = None
        self._recovery_slot = 0
        self._source_project_path = None

    def _mark_recovery_dirty(self):
        if hasattr(self, '_recovery_revision'):
            self._recovery_revision += 1

    def _recovery_token(self):
        text = self._edit_flat if self._text_editing else ''
        return self._recovery_revision, text

    def autosave_recovery(self, force=False):
        if not hasattr(self, '_recovery_id'):
            return None
        token = self._recovery_token()
        if token == self._recovery_written_token or token == self._recovery_saved_token:
            return None
        if self._recovery_worker is not None and not force:
            return self._recovery_worker
        if not force and (self._gesture_in_progress() or self.view._dragging):
            return None
        try:
            folder = recovery_folder()
            folder.mkdir(parents=True, exist_ok=True)
            snapshot = self._snapshot(metadata_only=True)
            if self._text_editing and self._text_edit_layer is not None:
                layer = self._text_edit_layer
                def find(items):
                    for item in items:
                        if item['id'] == layer.id:
                            return item
                        found = find(item.get('children', []))
                        if found:
                            return found
                roots = snapshot['layers']
                for board in snapshot['artboards']:
                    if self.active_artboard and board['id'] == self.active_artboard.id:
                        roots = board['layers']
                saved_text = find(roots)
                if saved_text is None:
                    saved_text = self._ser_layer(layer, with_pixels=True)
                    roots.append(saved_text)
                saved_text['text'] = self._edit_flat
                snapshot['active_id'] = layer.id
                snapshot['selected_ids'] = [layer.id]
            payload = dict(recovery_version=1, document_id=self._recovery_id,
                           revision=self._recovery_revision, saved_at=time.time(),
                           name=self.doc_name, source_path=self._source_project_path, snapshot=snapshot)
            self._history_store.freeze_pending()
            if self._recovery_worker is not None and self._recovery_worker.queue_snapshot(payload):
                self._recovery_worker._recovery_latest_token = token
                return self._recovery_worker
            path = folder / f'{self._recovery_id}.{self._recovery_slot}.recovery.json'
            document_id = self._recovery_id
            worker = ProjectSaveWorker(path, payload)
            self._recovery_worker = worker
            worker._recovery_latest_token = token
            worker.saved.connect(lambda result, w=worker, identity=document_id, version=token:
                                 self._recovery_complete(w, identity, version))
            worker.failed.connect(lambda result, message, w=worker, identity=document_id:
                                  self._recovery_failed(w, identity, message))
            worker.start()
            return worker
        except Exception as exc:
            self.status_tool.setText('自动备份暂未成功：' + str(exc)[:100])
            return None

    def _recovery_complete(self, worker, document_id, token):
        from PyQt6 import sip
        if sip.isdeleted(self) or document_id != self._recovery_id or self._recovery_worker is not worker:
            return
        self._recovery_worker = None
        token = worker._recovery_latest_token
        self._recovery_written_token = token
        self._recovery_slot = 1-self._recovery_slot
        if token == self._recovery_saved_token:
            self._remove_current_backups()
            self.refresh_recovery_notices()

    def _recovery_failed(self, worker, document_id, message):
        from PyQt6 import sip
        if sip.isdeleted(self) or document_id != self._recovery_id or self._recovery_worker is not worker:
            return
        self._recovery_worker = None
        self.status_tool.setText('自动备份未成功，将重试：' + message[:100])

    def _remove_current_backups(self):
        folder = recovery_folder()
        removed = True
        for slot in (0, 1):
            path = folder / f'{self._recovery_id}.{slot}.recovery.json'
            try:
                path.unlink(missing_ok=True)
            except OSError:
                removed = False
        return removed

    def _wait_project_saves(self):
        while getattr(self, '_save_jobs', None):
            path, worker = next(iter(self._save_jobs.items()))
            worker.wait()
            self._finish_project_save(path, worker, worker.save_error)

    def close_project_decision(self):
        """Return True for discard, False for saved/clean, and None for cancel."""
        if self._text_editing:
            self._end_text_edit(save=True)
        self._wait_project_saves()
        if self._recovery_token() == self._recovery_saved_token:
            return False
        box = QMessageBox(QMessageBox.Icon.Question, '保存图片工程',
                          f'关闭「{self.doc_name}」前要保存更改吗？', parent=self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        save = box.addButton('保存', QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton('不保存', QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton('取消', QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save)
        box.setEscapeButton(cancel)
        box.exec()
        choice = box.clickedButton()
        if choice is discard:
            return True
        if choice is not save:
            return None
        from core.project_files import PIC_SUFFIX
        source = self._source_project_path
        path = source if source and Path(source).suffix.lower() == PIC_SUFFIX else None
        if self.save_project(path) is None:
            return None
        self._wait_project_saves()
        if self._recovery_token() == self._recovery_saved_token:
            return False
        QMessageBox.warning(self, '保存图片工程', self.status_tool.text())
        return None

    def discard_recovery(self):
        if self._recovery_worker is not None:
            self._recovery_worker.wait()
        if not self._remove_current_backups():
            QMessageBox.warning(self, '关闭图片工程', '无法清理这份画布的恢复副本，请稍后重试。')
            return False
        self._recovery_saved_token = self._recovery_token()
        self.refresh_recovery_notices()
        return True

    def manual_save_completed(self, path, token):
        self._source_project_path = str(path)
        if token == self._recovery_token():
            self._recovery_saved_token = token
            if self._recovery_worker is None:
                self._remove_current_backups()
        self.refresh_recovery_notices()

    def available_recoveries(self):
        from PyQt6 import sip
        live_ids = {editor._recovery_id for editor in tuple(_EDITORS)
                    if not sip.isdeleted(editor) and hasattr(editor, '_recovery_id')}
        records, seen = [], set()
        for record in list_recovery_files():
            identity = record['document_id']
            if identity not in seen and identity not in live_ids:
                records.append(record)
                seen.add(identity)
        return records

    def refresh_recovery_notice(self):
        from PyQt6 import sip
        if not sip.isdeleted(self):
            self._set_recovery_notice(len(self.available_recoveries()))

    def _set_recovery_notice(self, count):
        self._recovery_notice.setText(f'发现 {count} 份可恢复的未保存编辑，点击恢复' if count else '')
        self._recovery_notice.setVisible(count > 0)

    def refresh_recovery_notices(self):
        from PyQt6 import sip
        count = len(self.available_recoveries())
        for editor in tuple(_EDITORS):
            if not sip.isdeleted(editor):
                editor._set_recovery_notice(count)

    def restore_recovery(self, record):
        if not isinstance(record.get('document_id'), str) or not re.fullmatch(r'[0-9a-f]{32}', record['document_id']):
            raise ValueError('备份标识无效')
        # Try the other generation if the newest file's compressed data is damaged.
        candidates = [record] + [item for item in list_recovery_files()
                                 if item['document_id'] == record['document_id'] and item['path'] != record['path']]
        last_error = None
        before = self._snapshot()
        for candidate in candidates:
            try:
                snapshot = self.unpack_project(candidate['snapshot'])
                if not {'w', 'h', 'layers', 'transparent', 'bg'} <= snapshot.keys():
                    raise ValueError('备份缺少画布数据')
                self._rebuild_from_snap(snapshot)
                self._start_recovery_session(record['document_id'])
                self._source_project_path = candidate.get('source_path')
                self.set_doc_name(candidate.get('name', '未命名') + '（恢复）')
                self._push_undo_snapshot('恢复自动备份', before)
                self.refresh_recovery_notices()
                return True
            except Exception as exc:
                last_error = exc
                self._rebuild_from_snap(before)
        raise ValueError('备份无法恢复：' + str(last_error))

    def show_recovery_dialog(self):
        records = self.available_recoveries()
        if not records:
            self.refresh_recovery_notices()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('恢复未保存编辑')
        dialog.resize(520, 320)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('选择一份编辑备份；恢复后请保存工程。'))
        listing = QListWidget()
        for record in records:
            moment = datetime.fromtimestamp(record['saved_at']).strftime('%m-%d %H:%M:%S')
            item = QListWidgetItem(f"{record.get('name', '未命名')}  ·  {moment}")
            item.setData(Qt.ItemDataRole.UserRole, record)
            listing.addItem(item)
        listing.setCurrentRow(0)
        layout.addWidget(listing)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Open).setText('恢复')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        listing.itemDoubleClicked.connect(lambda *_: dialog.accept())
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted and listing.currentItem():
            record = listing.currentItem().data(Qt.ItemDataRole.UserRole)
            target = self.host.new_document(name='恢复的编辑') if getattr(self, 'host', None) else self
            try:
                target.restore_recovery(record)
            except Exception as exc:
                QMessageBox.warning(self, '恢复失败', str(exc))
