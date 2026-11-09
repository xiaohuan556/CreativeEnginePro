import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QPointF, Qt, QMimeData, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QImage
from PyQt6.QtWidgets import QApplication, QPushButton

from ui.image_editor import ImageEditorWidget
from ui.main_window import UltimateEngine


def _mime(*paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    return mime


def test_navigation_accepts_videos_and_images_only_for_matching_workbenches(tmp_path):
    video = tmp_path / "clip.mp4"
    image = tmp_path / "layer.png"
    video.write_bytes(b"video")
    image.write_bytes(b"image")
    canvas = SimpleNamespace(media_node_type=lambda path: (
        "video_node" if path.endswith(".mp4") else
        "image_node" if path.endswith(".png") else ""))
    host = SimpleNamespace(production_canvas=canvas)
    mime = _mime(video, image)

    assert [Path(p) for p in UltimateEngine._nav_drop_paths(host, 0, mime)] == [video]
    assert [Path(p) for p in UltimateEngine._nav_drop_paths(host, 2, mime)] == [video]
    assert [Path(p) for p in UltimateEngine._nav_drop_paths(host, 1, mime)] == [image]
    assert [Path(p) for p in UltimateEngine._nav_drop_paths(host, 3, mime)] == [image]
    assert [Path(p) for p in UltimateEngine._nav_drop_paths(host, 7, mime)] == [
        video, image]


def test_navigation_routes_to_each_workbench_import(tmp_path):
    canvas = SimpleNamespace(
        _viewport_center=lambda: "center",
        import_media_paths=Mock(return_value=["node"]),
    )
    host = SimpleNamespace(
        execute_import=Mock(), _execute_img_import=Mock(),
        _mix_add_files=Mock(), _ss_add_images=Mock(),
        production_canvas=canvas,
    )
    files = [str(tmp_path / "asset.png")]

    for tab in (0, 1, 2, 3, 7):
        assert UltimateEngine._import_drop_paths(host, tab, files)
    host.execute_import.assert_called_once_with(files)
    host._execute_img_import.assert_called_once_with(files)
    host._mix_add_files.assert_called_once_with('random', files)
    host._ss_add_images.assert_called_once_with(files)
    canvas.import_media_paths.assert_called_once_with(files, "center")


def test_navigation_button_accepts_a_drop_and_switches_workbench(tmp_path):
    app = QApplication.instance() or QApplication([])
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    mime = _mime(video)
    button = QPushButton("AI 制片画布")
    button.setProperty("tab_index", 7)
    button.setAcceptDrops(True)
    host = SimpleNamespace(
        nav_btns=[button],
        _nav_drop_paths=lambda index, data: [str(video)],
        _import_drop_paths=Mock(return_value=True),
        _show_workspace_tab=Mock(),
        _hover_drop_tab=Mock(),
    )
    enter = QDragEnterEvent(QPoint(3, 3), Qt.DropAction.CopyAction,
                           mime, Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier)
    assert UltimateEngine.eventFilter(host, button, enter)
    assert enter.isAccepted()

    drop = QDropEvent(QPointF(3, 3), Qt.DropAction.CopyAction,
                      mime, Qt.MouseButton.LeftButton,
                      Qt.KeyboardModifier.NoModifier)
    assert UltimateEngine.eventFilter(host, button, drop)
    assert drop.isAccepted()
    host._import_drop_paths.assert_called_once_with(7, [str(video)])
    host._show_workspace_tab.assert_called_once_with(7)


def test_multiselected_layers_export_as_separate_durable_images(tmp_path):
    layers = [SimpleNamespace(kind="image", name="first"),
              SimpleNamespace(kind="text", name="second"),
              SimpleNamespace(kind="adjust", name="adjust")]

    def export(_layer, path):
        image = QImage(2, 2, QImage.Format.Format_ARGB32)
        image.fill(0xff336699)
        assert image.save(path)

    editor = SimpleNamespace(
        selected=layers, _ctx_layers=lambda: layers,
        _export_layer_to_png=export,
    )
    with patch("utils.app_paths.user_data_root", return_value=tmp_path):
        paths = ImageEditorWidget._export_layers_for_drag(editor, layers[0])

    assert len(paths) == 2
    assert all(Path(path).is_file() for path in paths)
    assert all(Path(path).parent == tmp_path / "dragged_layers" for path in paths)
