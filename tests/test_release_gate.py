from datetime import datetime, timezone
import json

import pytest

from core.release_gate import ReleaseGateError, ReleasePolicy


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


def test_login_release_requires_a_server(tmp_path):
    with pytest.raises(ReleaseGateError, match="登录服务器"):
        ReleasePolicy.load(write_policy(tmp_path, auth_base_url=""))


def test_release_manifest_accepts_windows_powershell_utf8_bom(tmp_path):
    path = write_policy(tmp_path)
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    assert ReleasePolicy.load(path).version == "1.0.0"
