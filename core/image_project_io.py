"""Background, atomic project persistence independent of editor lifetime."""
import threading
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal, QCoreApplication

from core.project_files import PIC_SUFFIX, save_project_file

_RUNNING = set()


def _finish_saves_on_exit():
    while _RUNNING:
        for worker in tuple(_RUNNING):
            worker.wait()
        QCoreApplication.processEvents()


class ProjectSaveWorker(QThread):
    saved = pyqtSignal(str)
    failed = pyqtSignal(str,str)

    def __init__(self, path, payload):
        super().__init__()
        self.path, self.payload = str(path), payload
        self.save_error = None
        self._queue_lock = threading.Lock()
        self._queued_payload = None
        self._accepting = True
        _RUNNING.add(self)
        app = QCoreApplication.instance()
        if app is not None and not getattr(app,'_image_save_exit_hook',False):
            app.aboutToQuit.connect(_finish_saves_on_exit)
            app._image_save_exit_hook = True
        self.finished.connect(self._release)

    def _release(self):
        _RUNNING.discard(self)
        self.deleteLater()

    def queue_snapshot(self,payload):
        with self._queue_lock:
            if not self._accepting:
                return False
            self._queued_payload = payload
            return True

    def run(self):
        try:
            from ui.image_workbench_ops import WorkbenchOperations
            suffix = PIC_SUFFIX if Path(self.path).suffix.lower() == PIC_SUFFIX else None
            while True:
                payload = WorkbenchOperations.pack_project(self.payload)
                save_project_file(self.path, payload, suffix)
                with self._queue_lock:
                    if self._queued_payload is None:
                        self._accepting = False
                        break
                    self.payload,self._queued_payload = self._queued_payload,None
            self.saved.emit(self.path)
        except Exception as exc:
            with self._queue_lock:
                self._accepting = False
            self.save_error = str(exc)
            self.failed.emit(self.path, self.save_error)
