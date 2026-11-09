import os
import tempfile
import zipfile
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CEP_DATA_DIR", tempfile.mkdtemp(prefix="cep_result_export_"))
from PyQt6.QtCore import QEvent, QPointF
from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest
import ai.ui.production_canvas as canvas
from core.project_files import AI_SUFFIX, read_project


@pytest.fixture
def panel(tmp_path):
    app = QApplication.instance() or QApplication([])
    with patch.object(canvas, "LAYOUT_FILE", tmp_path / "layout.json"):
        tab = canvas.ProductionCanvasTab()
        tab._checkpoint_timer.stop()
        tab._task_timer.stop()
        tab._layout_timer.stop()
        tab._layout_timer.timeout.disconnect()
        tab._save_layout_now = lambda *a, **k: None
        yield tab
        worker = getattr(tab, "_selection_export_worker", None)
        if worker is not None:
            worker.wait(5000)
        tab.close()
        app.processEvents()
        tab.deleteLater()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def select(panel, ids):
    panel._defer_inline_editor_until_pointer_release = True
    panel.scene.clearSelection()
    for node_id in ids:
        panel._nodes[node_id].setSelected(True)
    panel._defer_inline_editor_until_pointer_release = False


def finish_export(panel):
    for _ in range(500):
        QApplication.processEvents()
        if panel._selection_export_worker is None:
            return
        QTest.qWait(10)
    pytest.fail("Export worker did not finish")


def test_default_export_five_videos_excludes_covers_references_history_and_other_nodes(panel, tmp_path):
    image = tmp_path / "封面和参考.png"
    image.write_bytes(b"reference-image")
    history = tmp_path / "旧版本.mp4"
    history.write_bytes(b"old-version")
    videos, ids = [], []
    for index in range(5):
        path = tmp_path / f"video{index}.mov"
        path.write_bytes(f"current-video-{index}".encode())
        videos.append(path)
        node_id = panel.create_custom_node("video_node", QPointF(index * 300, 0), {
            "path": str(path), "first_frame": str(image),
            "video_thumbnail": str(image), "video_tail_frame": str(image),
            "video_review_frames": [str(image)] * 6,
            "reference_assets": [{"path": str(image)}],
            "candidates": [{"path": str(history)}],
        })
        ids.append(node_id)
    panel.create_custom_node("image_node", QPointF(0, 500), {"path": str(image)})
    select(panel, ids)
    output = tmp_path / "five-videos.zip"

    def save_dialog(*args):
        assert args[4].startswith("所选素材文件")
        return str(output), args[4]

    with patch.object(canvas.QFileDialog, "getSaveFileName", side_effect=save_dialog), \
            patch.object(canvas.QMessageBox, "information") as success, \
            patch.object(canvas.QMessageBox, "warning") as warning:
        assert panel.export_canvas_selection()
        finish_export(panel)
        warning.assert_not_called()
        assert "5 个素材文件" in success.call_args.args[2]
        assert "5 个视频" in success.call_args.args[2]
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == [path.name for path in videos]
        assert [archive.read(path.name) for path in videos] == [path.read_bytes() for path in videos]


def test_explicit_project_filter_keeps_editable_project_and_dependencies(panel, tmp_path):
    image, video = tmp_path / "reference.png", tmp_path / "video.mov"
    image.write_bytes(b"reference")
    video.write_bytes(b"video")
    node_id = panel.create_custom_node("video_node", QPointF(0, 0), {
        "path": str(video), "first_frame": str(image)})
    select(panel, [node_id])
    output = tmp_path / "editable.zip"

    def choose_project(*args):
        return str(output), args[3].split(";;")[1]

    with patch.object(canvas.QFileDialog, "getSaveFileName", side_effect=choose_project), \
            patch.object(canvas.QMessageBox, "information"), \
            patch.object(canvas.QMessageBox, "warning") as warning:
        assert panel.export_canvas_selection()
        finish_export(panel)
        warning.assert_not_called()
    with zipfile.ZipFile(output) as archive:
        assert "selection.xiaohuanai" in archive.namelist()
        assert any(name.endswith("reference.png") for name in archive.namelist())
        with archive.open("selection.xiaohuanai") as stream:
            saved = read_project(stream, AI_SUFFIX)
        assert saved["canvas"]["__custom_nodes__"][0]["first_frame"].startswith("media/")


def test_missing_video_never_exports_its_thumbnail_as_a_result(panel, tmp_path):
    image = tmp_path / "thumbnail.png"
    image.write_bytes(b"thumbnail")
    node_id = panel.create_custom_node("video_node", QPointF(0, 0), {
        "path": "", "video_thumbnail": str(image)})
    select(panel, [node_id])
    output = tmp_path / "missing.zip"
    with patch.object(canvas.QMessageBox, "warning") as warning:
        assert not panel.export_canvas_selection(str(output))
        warning.assert_called_once()
    assert not output.exists()


def test_explicit_mixed_media_selection_keeps_selected_picture_and_audio(panel, tmp_path):
    files = [tmp_path / "video.mp4", tmp_path / "picture.png", tmp_path / "audio.wav"]
    ids = []
    for index, (path, kind) in enumerate(zip(files, ("video_node", "image_node", "audio_node"))):
        path.write_bytes(kind.encode())
        ids.append(panel.create_custom_node(kind, QPointF(index * 300, 0), {"path": str(path)}))
    select(panel, ids)
    output = tmp_path / "mixed.zip"
    with patch.object(canvas.QMessageBox, "information"), \
            patch.object(canvas.QMessageBox, "warning") as warning:
        assert panel.export_canvas_selection(str(output))
        finish_export(panel)
        warning.assert_not_called()
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {path.name for path in files}
