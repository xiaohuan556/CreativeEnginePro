import os
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QDialog

from core.release_gate import LoginResult, ReleasePolicy
from ui.release_login import ReleaseLoginDialog


class _RememberedClient:
    def __init__(self):
        self.validations = 0
        self.forgot = False

    def restore_saved_session(self):
        return True

    def validate_session(self):
        self.validations += 1
        return LoginResult(
            {"username": "remembered-user", "role": "producer"},
            datetime(2026, 9, 2, tzinfo=timezone.utc))

    def forget_saved_session(self):
        self.forgot = True


def test_remembered_session_enters_without_password_input():
    app = QApplication.instance() or QApplication([])
    policy = ReleasePolicy(
        channel="release", version="test",
        expires_at=datetime(2026, 12, 2, tzinfo=timezone.utc),
        require_login=True, auth_base_url="http://127.0.0.1:8000")
    client = _RememberedClient()
    dialog = ReleaseLoginDialog(policy, client)

    code = dialog.exec()

    assert code == QDialog.DialogCode.Accepted
    assert client.validations == 1
    assert dialog.result is not None
    assert dialog.result.user["username"] == "remembered-user"
    assert not client.forgot
    dialog.deleteLater()
    app.processEvents()
