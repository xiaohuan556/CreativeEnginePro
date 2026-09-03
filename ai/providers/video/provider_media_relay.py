"""Ephemeral HTTPS relay for local media consumed by cloud video models.

Cloud video APIs cannot read a Windows path.  This module exposes only files
explicitly registered for a task through an unguessable, expiring URL and uses
Cloudflare Quick Tunnel to provide the required public HTTPS origin.
"""

from __future__ import annotations

import atexit
import hashlib
import mimetypes
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..ark_http import ArkHTTPError


_TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.I)
_DEFAULT_TTL_SECONDS = 2 * 60 * 60
_VIDEO_SUFFIXES = {
    ".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".mpeg", ".mpg",
}


@dataclass(frozen=True)
class _PublishedFile:
    path: Path
    expires_at: float


class _RelayHandler(BaseHTTPRequestHandler):
    server_version = "CreativeEngineMediaRelay/1.0"

    def log_message(self, _format, *_args):
        return

    def do_HEAD(self):
        self._serve(send_body=False)

    def do_GET(self):
        self._serve(send_body=True)

    def _serve(self, send_body: bool):
        relay = getattr(self.server, "relay", None)
        parsed = urllib.parse.urlsplit(self.path)
        parts = parsed.path.strip("/").split("/", 2)
        if relay is None or len(parts) < 2 or parts[0] != "media":
            self.send_error(404)
            return
        published = relay.resolve(parts[1])
        if published is None:
            self.send_error(404)
            return
        try:
            size = published.path.stat().st_size
            start, end, partial = self._range(size)
            content_type = (
                mimetypes.guess_type(published.path.name)[0]
                or "application/octet-stream")
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Cache-Control", "private, max-age=60")
            self.end_headers()
            if not send_body:
                return
            with published.path.open("rb") as source:
                source.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (OSError, ConnectionError, BrokenPipeError):
            return

    def _range(self, size: int) -> tuple[int, int, bool]:
        value = str(self.headers.get("Range") or "").strip()
        if not value.startswith("bytes=") or size <= 0:
            return 0, max(0, size - 1), False
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
        if not match:
            return 0, size - 1, False
        left, right = match.groups()
        if not left:
            length = max(1, int(right or 1))
            return max(0, size - length), size - 1, True
        start = min(int(left), size - 1)
        end = min(int(right), size - 1) if right else size - 1
        if end < start:
            end = start
        return start, end, True


class ProviderMediaRelay:
    def __init__(self):
        self._lock = threading.RLock()
        self._files: dict[str, _PublishedFile] = {}
        self._server: ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._tunnel: subprocess.Popen | None = None
        self._public_origin = ""

    def publish(self, value: str | Path,
                ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> str:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise ArkHTTPError(f"Seedance 参考媒体文件不存在或无法读取：{path}")
        with self._lock:
            self._ensure_http_server()
            origin = self._ensure_public_origin()
            self._purge_expired()
            token = uuid.uuid4().hex + uuid.uuid4().hex
            self._files[token] = _PublishedFile(
                path=path,
                expires_at=time.time() + max(300, int(ttl_seconds)),
            )
            filename = urllib.parse.quote(path.name, safe="")
            url = f"{origin}/media/{token}/{filename}"
        # Do not hold the registry lock while probing: the HTTP handler needs
        # that same lock to resolve this token on its own server thread.
        try:
            self._wait_until_reachable(url)
        except Exception:
            with self._lock:
                self._files.pop(token, None)
            raise
        return url

    def resolve(self, token: str) -> _PublishedFile | None:
        with self._lock:
            item = self._files.get(str(token or ""))
            if item is None:
                return None
            if item.expires_at <= time.time() or not item.path.is_file():
                self._files.pop(str(token or ""), None)
                return None
            return item

    def _purge_expired(self):
        now = time.time()
        self._files = {
            key: value for key, value in self._files.items()
            if value.expires_at > now and value.path.is_file()
        }

    def _ensure_http_server(self):
        if self._server is not None:
            return
        server = ThreadingHTTPServer(("127.0.0.1", 0), _RelayHandler)
        server.daemon_threads = True
        server.relay = self
        thread = threading.Thread(
            target=server.serve_forever,
            name="provider-media-relay", daemon=True)
        thread.start()
        self._server = server
        self._server_thread = thread

    @property
    def local_origin(self) -> str:
        if self._server is None:
            return ""
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    @staticmethod
    def _cloudflared_path() -> str:
        configured = str(os.environ.get("CEP_CLOUDFLARED_PATH") or "").strip()
        candidates = [
            configured,
            shutil.which("cloudflared") or "",
            str(Path(sys.executable).resolve().parent / "cloudflared.exe"),
            str(Path(__file__).resolve().parents[3] / "cloudflared.exe"),
            r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
            r"C:\Program Files\cloudflared\cloudflared.exe",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return str(Path(candidate))
        raise ArkHTTPError(
            "Seedance 编辑本地视频需要 HTTPS 素材中转，但未找到 cloudflared。"
            "请安装 Cloudflare Tunnel，或配置 CEP_CLOUDFLARED_PATH。")

    def _ensure_public_origin(self) -> str:
        if (self._public_origin and self._tunnel is not None and
                self._tunnel.poll() is None):
            return self._public_origin
        self._stop_tunnel()
        executable = self._cloudflared_path()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen(
                [executable, "tunnel", "--no-autoupdate", "--url",
                 self.local_origin],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
            )
        except OSError as exc:
            raise ArkHTTPError(f"无法启动 Seedance 素材中转：{exc}") from exc
        self._tunnel = process
        messages: queue.Queue[str] = queue.Queue()
        connection_ready = threading.Event()

        def read_output():
            if process.stdout is None:
                return
            for line in process.stdout:
                match = _TUNNEL_URL_RE.search(line)
                if match:
                    messages.put(match.group(0).rstrip("/"))
                lowered = line.lower()
                if ("registered tunnel connection" in lowered or
                        "connection registered" in lowered):
                    connection_ready.set()

        threading.Thread(
            target=read_output, name="cloudflared-output", daemon=True).start()
        try:
            origin = messages.get(timeout=25)
        except queue.Empty as exc:
            exit_code = process.poll()
            self._stop_tunnel()
            detail = (f"（进程退出码 {exit_code}）" if exit_code is not None else "")
            raise ArkHTTPError(
                "Seedance 本地视频 HTTPS 中转启动超时"
                f"{detail}，请检查网络或 Cloudflare Tunnel。") from exc
        if not connection_ready.wait(timeout=20):
            exit_code = process.poll()
            self._stop_tunnel()
            detail = (f"（进程退出码 {exit_code}）" if exit_code is not None else "")
            raise ArkHTTPError(
                "Seedance 本地视频 HTTPS 中转尚未连接成功"
                f"{detail}，请检查网络后重试。")
        self._public_origin = origin
        return origin

    @staticmethod
    def _wait_until_reachable(url: str):
        last_error = None
        only_local_dns_failures = True
        for attempt in range(8):
            try:
                request = urllib.request.Request(
                    url, method="HEAD",
                    headers={"User-Agent":"CreativeEnginePro-MediaProbe/1.0"})
                with urllib.request.urlopen(request, timeout=12) as response:
                    if int(getattr(response, "status", 0) or 0) == 200:
                        return
            except urllib.error.URLError as error:
                last_error = error
                # Quick Tunnel publishes a brand-new hostname.  Windows DNS
                # can lag behind the already registered Cloudflare tunnel;
                # Ark resolves the URL independently, so a local-only DNS
                # miss must not prevent the paid request from being submitted.
                only_local_dns_failures = (
                    only_local_dns_failures
                    and isinstance(error.reason, socket.gaierror)
                )
            except Exception as error:
                last_error = error
                only_local_dns_failures = False
            time.sleep(min(0.75 + attempt * 0.5, 3.0))
        if last_error is not None and only_local_dns_failures:
            return
        raise ArkHTTPError(
            "Seedance 临时视频地址尚不能从公网下载，请检查网络后重试："
            f"{last_error or 'HTTPS 地址自检失败'}")

    def _stop_tunnel(self):
        process = self._tunnel
        self._tunnel = None
        self._public_origin = ""
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=3)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    def close(self):
        with self._lock:
            self._stop_tunnel()
            server = self._server
            self._server = None
            if server is not None:
                server.shutdown()
                server.server_close()
            self._files.clear()


_RELAY = ProviderMediaRelay()
atexit.register(_RELAY.close)


def _ffmpeg_path() -> str:
    configured = str(os.environ.get("CEP_FFMPEG_PATH") or "").strip()
    candidates = [
        configured,
        shutil.which("ffmpeg") or "",
        str(Path(sys.executable).resolve().parent / "ffmpeg.exe"),
        str(Path(__file__).resolve().parents[3] / "ffmpeg.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate))
    raise ArkHTTPError("找不到 ffmpeg，无法把本地视频转换为 Seedance 标准 MP4。")


def _provider_ready_video(source: Path) -> Path:
    """Create a non-distorted H.264/AAC faststart MP4 for provider fetching."""
    stat = source.stat()
    signature = hashlib.sha256(
        f"{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")
    ).hexdigest()[:20]
    cache_dir = Path(tempfile.gettempdir()) / "creative_engine_provider_media"
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"reference-{signature}.mp4"
    if target.is_file() and target.stat().st_size > 1024:
        return target
    temporary = target.with_suffix(".building.mp4")
    temporary.unlink(missing_ok=True)
    common = [
        _ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source), "-map", "0:v:0",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
    ]
    tail = [
        "-movflags", "+faststart", "-metadata:s:v:0", "rotate=0",
        str(temporary),
    ]
    # Some phone MOV files contain an auxiliary stream advertised as audio
    # with codec ``none``.  Mapping every audio stream (0:a?) asks FFmpeg to
    # decode that non-media track and aborts the whole conversion.  Preserve
    # only the first real audio candidate, then retry video-only when even that
    # candidate has no decoder.
    audio_command = [
        *common, "-map", "0:a:0?", "-c:a", "aac", "-b:a", "128k", *tail,
    ]

    def run(command):
        return subprocess.run(
            command, capture_output=True, text=True, timeout=1800,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    completed = run(audio_command)
    if (completed.returncode != 0 and
            "no decoder found" in str(completed.stderr or "").lower()):
        temporary.unlink(missing_ok=True)
        completed = run([*common, "-an", *tail])
    if (completed.returncode != 0 or not temporary.is_file() or
            temporary.stat().st_size <= 1024):
        temporary.unlink(missing_ok=True)
        detail = (completed.stderr or completed.stdout or "视频转换失败").strip()
        raise ArkHTTPError(f"Seedance 本地视频标准化失败：{detail[-1000:]}")
    temporary.replace(target)
    return target


def publish_local_media(value: str | Path,
                        ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> str:
    source = Path(value).expanduser().resolve()
    if not source.is_file():
        raise ArkHTTPError(f"Seedance 参考媒体文件不存在或无法读取：{source}")
    prepared = (
        _provider_ready_video(source)
        if source.suffix.lower() in _VIDEO_SUFFIXES else source)
    return _RELAY.publish(prepared, ttl_seconds=ttl_seconds)
