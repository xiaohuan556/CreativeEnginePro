from pathlib import Path
from uuid import uuid4

import pytest

from core.duo_delivery import DeliveryJournal, DuoError


class TestDuo:
    __test__ = False
    address = "http://10.13.13.216:3080"
    user = {"id": "designer"}

    def __init__(self):
        self.published = [7]
        self.uploaded = []
        self.receipts = {}
        self.lose_response = True

    def requests(self, request_id=None):
        return {"user": self.user, "requests": [{"id": number} for number in self.published]}

    def upload(self, path, name, _mime):
        self.uploaded.append((name, Path(path).read_bytes()))
        return str(uuid4())

    def commit(self, batch):
        if batch["id"] not in self.receipts:
            self.receipts[batch["id"]] = {
                "batchId": batch["id"], "requestId": batch["request_id"], "files": [
                    {"name": f"official_{index}.png", "url": "/api/files/" + item["upload_id"],
                     "size": item["size"]} for index, item in enumerate(batch["items"], 1)
                ],
            }
        if self.lose_response:
            self.lose_response = False
            raise DuoError("连接中断")
        return self.receipts[batch["id"]]


def test_no_matching_published_request_leaves_original_and_does_not_upload(tmp_path):
    media = tmp_path / "cover.png"
    media.write_bytes(b"image contents")
    client = TestDuo()
    journal = DeliveryJournal(tmp_path / "pending.json")
    with pytest.raises(DuoError, match="已发布需求"):
        journal.start(client, 8, [media], tmp_path / "archive")
    assert journal.read() is None
    assert media.read_bytes() == b"image contents"
    assert client.uploaded == []


def test_retry_after_lost_commit_response_reuses_archived_bytes_and_attachments(tmp_path):
    media = tmp_path / "cover.png"
    media.write_bytes(b"original image")
    client = TestDuo()
    journal = DeliveryJournal(tmp_path / "pending.json")
    batch = journal.start(client, 7, [media], tmp_path / "archive")
    with pytest.raises(DuoError, match="连接中断"):
        journal.resume(batch, client)
    media.write_bytes(b"new export")
    receipt, folder = DeliveryJournal(journal.path).resume(journal.read(), client)
    assert receipt["requestId"] == 7
    assert client.uploaded == [("cover.png", b"original image")]
    assert (folder / "official_1.png").read_bytes() == b"original image"
    assert journal.read() is None


def test_concurrent_delivery_conflict_releases_journal_and_keeps_local_copy(tmp_path, monkeypatch):
    media = tmp_path / "cover.png"
    media.write_bytes(b"original image")
    client = TestDuo()
    journal = DeliveryJournal(tmp_path / "pending.json")
    batch = journal.start(client, 7, [media], tmp_path / "archive")

    def reject(_):
        raise DuoError("这条需求已交付", 409, "already_delivered")

    monkeypatch.setattr(client, "commit", reject)
    with pytest.raises(DuoError, match="已交付"):
        journal.resume(batch, client)
    assert journal.read() is None
    assert (Path(batch["folder"]) / batch["items"][0]["staged"]).read_bytes() == media.read_bytes()


def test_direct_delivery_replaces_previous_batch_and_requires_a_selected_request(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("CEP_DATA_DIR", str(tmp_path))
    qt = pytest.importorskip("PyQt6.QtWidgets")
    from ui import duo_delivery

    app = qt.QApplication.instance() or qt.QApplication([])
    parent = qt.QWidget()
    dialog = duo_delivery.DuoDeliveryDialog(parent)
    first, second = tmp_path / "first.png", tmp_path / "second.png"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    try:
        dialog.open_submission([first], "图片工作台")
        assert dialog.login_form.isVisible() and not dialog.advanced.isVisible()
        dialog.client = SimpleNamespace(address=TestDuo.address)
        monkeypatch.setattr(dialog, "_refresh", lambda: None)
        dialog._set_listing({"user": {"name": "设计师"}, "requests": [
            {"id": 101, "description": "手机主题"}, {"id": 102, "description": "已完成", "fileCount": 2},
        ]})
        app.processEvents()
        assert not dialog.login_form.isVisible() and dialog.request_form.isVisible()
        assert dialog.publish_button.isVisible() and dialog.request.count() == 1
        assert not dialog.submit_button.isEnabled()
        disabled_width = dialog.submit_button.width()
        dialog._choose_request_match(dialog.request.itemText(0))
        assert dialog._request_id() == 101 and dialog.submit_button.isEnabled()
        app.processEvents()
        button = dialog.submit_button
        option = qt.QStyleOptionButton()
        button.initStyleOption(option)
        content = button.style().subElementRect(qt.QStyle.SubElement.SE_PushButtonContents, option, button)
        assert button.width() == disabled_width
        assert content.width() >= option.fontMetrics.horizontalAdvance(button.text())
        dialog.request.setEditText("REQ-404")
        assert not dialog.submit_button.isEnabled()

        dialog.open_submission([second], "图片工作台")
        assert dialog._paths == (str(second.resolve()),)
        assert dialog._request_id() is None and not dialog.submit_button.isEnabled()
    finally:
        dialog.close()
        parent.close()
