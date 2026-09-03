import tempfile
import socket
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ai.providers.video.provider_media_relay import (
    ProviderMediaRelay, _provider_ready_video,
)


def test_relay_serves_only_registered_token_and_supports_ranges():
    root = Path(tempfile.mkdtemp(prefix="cep_media_relay_"))
    source = root / "sample.mp4"
    source.write_bytes(b"0123456789")
    relay = ProviderMediaRelay()
    try:
        relay._ensure_http_server()
        relay._ensure_public_origin = lambda: relay.local_origin
        url = relay.publish(source)
        with urllib.request.urlopen(url, timeout=3) as response:
            assert response.status == 200
            assert response.read() == b"0123456789"
            assert response.headers["Content-Type"] == "video/mp4"

        request = urllib.request.Request(url, headers={"Range":"bytes=2-5"})
        with urllib.request.urlopen(request, timeout=3) as response:
            assert response.status == 206
            assert response.headers["Content-Range"] == "bytes 2-5/10"
            assert response.read() == b"2345"

        bad_url = url.replace("/media/", "/media/not-a-real-token-")
        try:
            urllib.request.urlopen(bad_url, timeout=3)
            assert False, "unknown relay token must not be readable"
        except urllib.error.HTTPError as error:
            assert error.code == 404
    finally:
        relay.close()


def test_relay_rejects_missing_local_file():
    relay = ProviderMediaRelay()
    try:
        missing = Path(tempfile.gettempdir()) / "cep-relay-missing-video.mp4"
        if missing.exists():
            missing.unlink()
        try:
            relay.publish(missing)
            assert False, "missing input must fail before tunnel startup"
        except Exception as error:
            assert "不存在" in str(error)
    finally:
        relay.close()


def test_mov_is_normalized_to_faststart_mp4_without_crop_or_stretch():
    root = Path(tempfile.mkdtemp(prefix="cep_media_normalize_"))
    source = root / "portrait.mov"
    source.write_bytes(b"source-mov")
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"m" * 2048)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch(
            "ai.providers.video.provider_media_relay.subprocess.run",
            side_effect=fake_run):
        target = _provider_ready_video(source)

    assert target.suffix == ".mp4"
    assert target.stat().st_size == 2048
    command = commands[0]
    assert "+faststart" in command
    assert "0:a:0?" in command
    assert "-an" not in command
    assert "libx264" in command
    video_filter = command[command.index("-vf") + 1]
    assert "setsar=1" in video_filter
    assert "crop=" not in video_filter
    assert "pad=" not in video_filter


def test_mov_with_undecodable_aux_audio_retries_video_only():
    root = Path(tempfile.mkdtemp(prefix="cep_media_no_audio_"))
    source = root / "phone.mov"
    source.write_bytes(b"source-mov-with-aux-track")
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        if len(commands) == 1:
            return SimpleNamespace(
                returncode=1, stdout="",
                stderr="Decoding requested, but no decoder found for: none")
        Path(command[-1]).write_bytes(b"v" * 2048)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch(
            "ai.providers.video.provider_media_relay.subprocess.run",
            side_effect=fake_run):
        target = _provider_ready_video(source)

    assert target.is_file()
    assert len(commands) == 2
    assert "0:a:0?" in commands[0]
    assert "-an" in commands[1]


def test_registered_tunnel_is_not_blocked_by_windows_dns_lag():
    dns_error = urllib.error.URLError(
        socket.gaierror(11001, "getaddrinfo failed"))
    with (
        patch(
            "ai.providers.video.provider_media_relay.urllib.request.urlopen",
            side_effect=dns_error),
        patch("ai.providers.video.provider_media_relay.time.sleep"),
    ):
        # The cloudflared registration event is authoritative; Ark performs
        # its own DNS lookup from the provider network.
        ProviderMediaRelay._wait_until_reachable(
            "https://new-tunnel.trycloudflare.com/media/token/video.mp4")


def test_registered_tunnel_still_rejects_real_http_download_failures():
    http_error = urllib.error.HTTPError(
        "https://relay.invalid/video.mp4", 502, "Bad Gateway", {}, None)
    with (
        patch(
            "ai.providers.video.provider_media_relay.urllib.request.urlopen",
            side_effect=http_error),
        patch("ai.providers.video.provider_media_relay.time.sleep"),
    ):
        try:
            ProviderMediaRelay._wait_until_reachable(
                "https://relay.invalid/video.mp4")
            assert False, "real HTTP failures must still stop submission"
        except Exception as error:
            assert "尚不能从公网下载" in str(error)
