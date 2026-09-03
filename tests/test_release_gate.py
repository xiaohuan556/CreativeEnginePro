from datetime import datetime, timezone
import json
import time
from unittest.mock import patch

import pytest

from core.release_gate import DesktopAuthClient, ReleaseGateError, ReleasePolicy


def write_policy(tmp_path, **updates):
    payload = {
        "channel": "release",
        "version": "1.0.0",
        "expires_at": "2026-12-02T00:00:00+08:00",
        "require_login": True,
        "auth_base_url": "https://studio.example.com",
        "session_check_minutes": 10,
    }
    payload.update(updates)
    path = tmp_path / "release_manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_release_is_valid_through_december_first_in_china(tmp_path):
    policy = ReleasePolicy.load(write_policy(tmp_path))
    assert not policy.is_expired(datetime.fromisoformat("2026-12-01T23:59:59+08:00"))
    assert policy.is_expired(datetime.fromisoformat("2026-12-02T00:00:00+08:00"))


def test_server_utc_time_enforces_same_deadline(tmp_path):
    policy = ReleasePolicy.load(write_policy(tmp_path))
    assert not policy.is_expired(datetime(2026, 12, 1, 15, 59, 59, tzinfo=timezone.utc))
    assert policy.is_expired(datetime(2026, 12, 1, 16, 0, 0, tzinfo=timezone.utc))


def test_release_requires_https_except_loopback(tmp_path):
    with pytest.raises(ReleaseGateError, match="HTTPS"):
        ReleasePolicy.load(write_policy(tmp_path, auth_base_url="http://example.com"))
    assert ReleasePolicy.load(
        write_policy(tmp_path, auth_base_url="http://127.0.0.1:8000")
    ).require_login


def test_explicit_temporary_lan_mode_accepts_only_private_ipv4_http(tmp_path):
    policy = ReleasePolicy.load(write_policy(
        tmp_path, auth_base_url="http://10.13.12.67:8000",
        allow_insecure_lan=True))
    assert policy.allow_insecure_lan
    with pytest.raises(ReleaseGateError, match="HTTPS"):
        ReleasePolicy.load(write_policy(
            tmp_path, auth_base_url="http://8.8.8.8:8000",
            allow_insecure_lan=True))
    with pytest.raises(ReleaseGateError, match="HTTPS"):
        ReleasePolicy.load(write_policy(
            tmp_path, auth_base_url="http://example.com:8000",
            allow_insecure_lan=True))


def test_login_release_requires_a_server(tmp_path):
    with pytest.raises(ReleaseGateError, match="登录服务器"):
        ReleasePolicy.load(write_policy(tmp_path, auth_base_url=""))


def test_release_manifest_accepts_windows_powershell_utf8_bom(tmp_path):
    path = write_policy(tmp_path)
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    assert ReleasePolicy.load(path).version == "1.0.0"


def test_desktop_session_is_remembered_without_storing_password(tmp_path):
    session_path = tmp_path / "auth" / "session.bin"
    first = DesktopAuthClient("http://10.13.12.67:8000")
    first.session.cookies.set(
        "cep_session", "server-issued-token", domain="10.13.12.67",
        path="/", expires=int(time.time()) + 3600)
    with patch("core.release_gate.user_file", return_value=session_path), \
            patch("core.release_gate._protect_user_data", side_effect=lambda value: b"dpapi:" + value), \
            patch("core.release_gate._unprotect_user_data", side_effect=lambda value: value[6:]):
        assert first.save_session()
        encrypted = session_path.read_bytes()
        assert b"password" not in encrypted

        restored = DesktopAuthClient("http://10.13.12.67:8000")
        assert restored.restore_saved_session()
        assert restored.session.cookies.get("cep_session") == "server-issued-token"


def test_expired_remembered_session_is_deleted(tmp_path):
    session_path = tmp_path / "auth" / "session.bin"
    client = DesktopAuthClient("http://10.13.12.67:8000")
    client.session.cookies.set(
        "cep_session", "expired-token", domain="10.13.12.67",
        path="/", expires=int(time.time()) - 1)
    with patch("core.release_gate.user_file", return_value=session_path), \
            patch("core.release_gate._protect_user_data", side_effect=lambda value: value), \
            patch("core.release_gate._unprotect_user_data", side_effect=lambda value: value):
        assert client.save_session()
        restored = DesktopAuthClient("http://10.13.12.67:8000")
        assert not restored.restore_saved_session()
        assert not session_path.exists()
