import os
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import core.downloader as downloader
import core.worker_stdio as worker_stdio
import pytest


def test_frozen_bundle_uses_internal_ytdlp_worker():
    downloader._ytdlp_path = None
    with patch.object(sys, "frozen", True, create=True), \
            patch("core.downloader.os.path.exists", return_value=False), \
            patch("sysconfig.get_paths", return_value={"scripts": ""}), \
            patch("shutil.which", return_value=None):
        assert downloader._find_ytdlp() == sys.executable + "@@@--ytdlp-worker"
        assert downloader._ytdlp_cmd() == [sys.executable, "--ytdlp-worker"]
    downloader._ytdlp_path = None


def test_source_ytdlp_worker_exits_before_desktop_login():
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root)
    result = subprocess.run(
        [sys.executable, str(root / "main.py"), "--ytdlp-worker", "--version"],
        cwd=root, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()
    assert "CreativeEnginePro" not in result.stdout


def test_worker_stdio_leaves_existing_streams_untouched(monkeypatch):
    stdout = object()
    stderr = object()
    monkeypatch.setattr(worker_stdio.sys, "stdout", stdout)
    monkeypatch.setattr(worker_stdio.sys, "stderr", stderr)
    assert worker_stdio.restore_frozen_worker_stdio()
    assert worker_stdio.sys.stdout is stdout
    assert worker_stdio.sys.stderr is stderr


def test_frozen_update_never_launches_desktop_exe_as_pip():
    with patch.object(sys, "frozen", True, create=True), \
            patch("core.downloader.ytdlp_available", return_value=True), \
            patch("core.downloader.subprocess.run") as run:
        ok, message = downloader.ytdlp_update()
    assert not ok
    assert "内置 yt-dlp" in message
    run.assert_not_called()


def test_tiktok_player_payload_selects_best_public_video_stream():
    payload = {
        "items": [{
            "id_str": "7668090902816017671",
            "desc": "竖屏测试",
            "author_info": {"unique_id": "creator", "nickname": "作者"},
            "video_info": {
                "meta": {"duration": 30440, "width": 576, "height": 1024},
                "cover": {"url_list": ["https://img.example/cover.webp"]},
                "profiles": [
                    {"bitrate": 400000, "play_addr": {
                        "url_list": ["https://video.example/low.mp4"],
                        "width": 360, "height": 640, "data_size": 100}},
                    {"bitrate": 900000, "play_addr": {
                        "url_list": ["https://video.example/high.mp4"],
                        "width": 576, "height": 1024, "data_size": 200}},
                ],
            },
        }],
        "results": [{"id_str": "7668090902816017671", "code": "ok"}],
    }
    info = downloader._tiktok_player_info_from_payload(
        "7668090902816017671", payload)
    assert info["direct_url"].endswith("high.mp4")
    assert info["duration"] == 30.44
    assert info["webpage_url"].endswith("/@creator/video/7668090902816017671")


def test_tiktok_direct_retry_drops_browser_cookie_extraction():
    original = "https://www.tiktok.com/@creator/video/7668090902816017671"
    command = downloader._tiktok_direct_command(
        ["yt-dlp", "--cookies-from-browser", "firefox", "-f", "best", original],
        original, "https://video.example/direct.mp4")
    assert "--cookies-from-browser" not in command
    assert "firefox" not in command
    assert command[-1] == "https://video.example/direct.mp4"


def test_tiktok_profile_embed_parses_public_homepage_video_list():
    state = {
        "source": {"data": {"/embed/@creator?lang=zh-Hans": {
            "userInfo": {"uniqueId": "creator", "privateAccount": False},
            "videoList": [
                {"id": "7680721699171601694", "desc": "作品一",
                 "privateItem": False},
                {"id": "7679101730352565535", "desc": "作品二",
                 "privateItem": False},
            ],
        }}}
    }
    webpage = (
        '<script id="__FRONTITY_CONNECT_STATE__" type="application/json">'
        + json.dumps(state, ensure_ascii=False) + '</script>')
    results = downloader._tiktok_profile_items_from_html(
        "https://www.tiktok.com/@creator", webpage, max_count=1)
    assert results == [(
        "作品一",
        "https://www.tiktok.com/@creator/video/7680721699171601694",
        0.0,
    )]


def test_tiktok_scrape_rejects_single_video_link_as_a_homepage():
    webpage = '<script id="__FRONTITY_CONNECT_STATE__">{}</script>'
    with pytest.raises(RuntimeError, match="账号主页链接"):
        downloader._tiktok_profile_items_from_html(
            "https://www.tiktok.com/@creator/video/7680721699171601694",
            webpage)
