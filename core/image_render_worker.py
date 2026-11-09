"""Isolated full-resolution QImage rendering without touching live widgets."""
import inspect
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor
from core.image_project_io import ProjectSaveWorker


class ImageRenderContext:
    def __getattr__(self, name):
        from ui.image_editor import ImageEditorWidget
        value = inspect.getattr_static(ImageEditorWidget, name)
        return value.__get__(self, ImageEditorWidget) if hasattr(value, '__get__') else value

    def load(self, snapshot):
        from ui.image_editor import ImageProject, Artboard
        self.project = ImageProject(snapshot['w'], snapshot['h'])
        self.project.transparent = snapshot['transparent']
        self.project.bg_color = QColor(*snapshot['bg'])
        self.project.layers = [self._deser_layer(item) for item in snapshot['layers']]
        self.project.artboards = []
        for data in snapshot.get('artboards', []):
            board = Artboard(data['name'], data['x'], data['y'], data['w'], data['h'])
            board.id = data['id']
            board.transparent = data.get('transparent', False)
            board.bg_color = QColor(*data.get('bg', (255, 255, 255)))
            board.layers = [self._deser_layer(item) for item in data['layers']]
            self.project.artboards.append(board)
        self.active_artboard = next((board for board in self.project.artboards
                                     if board.id == snapshot.get('active_artboard_id')), None)
        self.active = None
        self._text_editing = False
        self._cached_doc_bounds = None
        return self


class ImageCompositeWorker(ProjectSaveWorker):
    rendered = pyqtSignal(object, object)

    def __init__(self, snapshot, ticket):
        super().__init__('', snapshot)
        self.ticket = ticket

    def run(self):
        try:
            image = ImageRenderContext().load(self.payload)._render_composite(True)
            self.rendered.emit(image, self.ticket)
        except Exception as exc:
            self.failed.emit('', str(exc))
