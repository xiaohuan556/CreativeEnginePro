import os
from datetime import datetime, timezone
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMessageBox

from core.release_gate import (
    AuthenticationUnavailable, DesktopAuthClient, ReleasePolicy,
)
from ui.personal_center import PersonalCenterDialog


def _policy():
    return ReleasePolicy(
        channel="release", version="test",
        expires_at=datetime(2026, 12, 2, tzinfo=timezone.utc),
        require_login=True, auth_base_url="http://127.0.0.1:8000")


def _user(role="producer"):
    return {
        "username": "maker", "display_name": "制片小组", "role": role,
        "status": "active",
        "limits": {
            "daily_tasks": 20, "concurrent_tasks": 2,
            "daily_credits": 500, "allowed_models": ["seedance-2.5"],
        },
    }


def test_personal_center_keeps_only_the_explicit_logout_action():
    app = QApplication.instance() or QApplication([])
    client = DesktopAuthClient("http://127.0.0.1:8000")
    dialog = PersonalCenterDialog(_policy(), client, _user())

    assert not hasattr(dialog, "close_button")
    assert not hasattr(dialog, "close_application_button")
    assert not hasattr(dialog, "closeApplicationRequested")
    assert dialog.logout_button.text() == "退出当前账号"
    assert dialog.manage_accounts.isHidden()

    emitted = []
    dialog.logoutRequested.connect(lambda: emitted.append(True))
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        dialog._confirm_logout()
    assert emitted == [True]
    assert dialog._logout_pending
    assert dialog.logout_button.text() == "正在退出登录…"
    dialog.deleteLater()
    app.processEvents()


def test_admin_personal_center_contains_account_management():
    app = QApplication.instance() or QApplication([])
    dialog = PersonalCenterDialog(
        _policy(), DesktopAuthClient("http://127.0.0.1:8000"), _user("admin"))
    assert not dialog.manage_accounts.isHidden()
    dialog.deleteLater()
    app.processEvents()


def test_logout_clears_local_identity_even_when_server_is_unavailable():
    client = DesktopAuthClient("http://127.0.0.1:8000")
    client.user = {"username": "maker"}
    client.csrf_token = "csrf"
    client._desktop_project_id = "project"
    client.session.cookies.set("cep_session", "session")

    with patch.object(
            client, "request_json",
            side_effect=AuthenticationUnavailable("offline")), \
            patch.object(client, "forget_saved_session") as forget:
        warning = client.logout()

    assert "本机登录记录已经清除" in warning
    forget.assert_called_once_with()
    assert not client.session.cookies
    assert client.user == {}
    assert client.csrf_token == ""
    assert client._desktop_project_id == ""
