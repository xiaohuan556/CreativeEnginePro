"""Formal-release policy and remote desktop authentication client."""
from __future__ import annotations

import json
import mimetypes
import threading
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from utils.app_paths import is_frozen, resource_root


class ReleaseGateError(RuntimeError):
    pass


class AuthenticationError(ReleaseGateError):
    pass


class AuthenticationUnavailable(ReleaseGateError):
    pass


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("release time must include a timezone")
    return parsed


@dataclass(frozen=True)
class ReleasePolicy:
    channel: str
    version: str
    expires_at: datetime
    require_login: bool
    auth_base_url: str
    session_check_minutes: int = 10

    @classmethod
    def load(cls, path: str | Path | None = None) -> "ReleasePolicy":
        manifest = Path(path) if path else resource_root() / "release_manifest.json"
        try:
            # Windows PowerShell 5's ``-Encoding UTF8`` writes a BOM.  Accept
            # both BOM and BOM-less manifests so an otherwise valid formal
            # build never blocks behind an authorization error dialog.
            payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
            policy = cls(
                channel=str(payload.get("channel") or "release"),
                version=str(payload.get("version") or "0.0.0"),
                expires_at=_parse_datetime(str(payload["expires_at"])),
                require_login=bool(payload.get("require_login", True)),
                auth_base_url=str(payload.get("auth_base_url") or "").strip().rstrip("/"),
                session_check_minutes=max(1, int(payload.get("session_check_minutes", 10))),
            )
        except Exception as error:
            if is_frozen():
                raise ReleaseGateError(f"正式版授权清单缺失或损坏：{error}") from error
            # Source runs remain usable for development and automated tests.
            return cls("development", "dev", datetime(2099, 1, 1, tzinfo=timezone.utc), False, "")
        policy.validate()
        return policy

    def validate(self) -> None:
        if self.require_login and not self.auth_base_url:
            raise ReleaseGateError("正式版尚未配置登录服务器地址")
        if self.auth_base_url:
            parsed = urllib.parse.urlparse(self.auth_base_url)
            localhost = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            if parsed.scheme != "https" and not localhost:
                raise ReleaseGateError("正式版登录服务器必须使用 HTTPS")

    def is_expired(self, now: datetime | None = None) -> bool:
        current = now or datetime.now().astimezone()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc) >= self.expires_at.astimezone(timezone.utc)

    @property
    def expiry_label(self) -> str:
        return self.expires_at.astimezone().strftime("%Y年%m月%d日 %H:%M")


@dataclass(frozen=True)
class LoginResult:
    user: dict[str, Any]
    server_time: datetime


class DesktopAuthClient:
    """Cookie-backed client for the existing Creative Engine control plane."""

    def __init__(self, base_url: str, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "CreativeEnginePro-Desktop/1.0",
        })
        self._request_lock = threading.RLock()
        self._project_lock = threading.Lock()
        self.csrf_token = ""
        self.user: dict[str, Any] = {}
        self._desktop_project_id = ""

    @staticmethod
    def _response_error(response: requests.Response) -> str:
        try:
            return str(response.json().get("detail") or response.json().get("error") or "")
        except Exception:
            return ""

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            with self._request_lock:
                response = self.session.request(
                    method, f"{self.base_url}{path}", json=payload,
                    timeout=self.timeout,
                )
            if response.status_code >= 400:
                detail = self._response_error(response)
                if response.status_code in {401, 403, 429}:
                    raise AuthenticationError(detail or "账号验证失败")
                raise AuthenticationUnavailable(
                    detail or f"登录服务器返回 {response.status_code}")
            return response.json()
        except (AuthenticationError, AuthenticationUnavailable):
            raise
        except requests.RequestException as error:
            raise AuthenticationUnavailable(f"无法连接登录服务器：{error}") from error
        except ValueError as error:
            raise AuthenticationUnavailable("登录服务器返回了无效数据") from error

    def request_json(
            self, method: str, path: str,
            payload: dict[str, Any] | None = None, *, write: bool = False,
            extra_headers: dict[str, str] | None = None,
            timeout: int | None = None) -> dict[str, Any]:
        headers = dict(extra_headers or {})
        if write:
            if not self.csrf_token:
                token = self._request("GET", "/api/auth/csrf")
                self.csrf_token = str(token.get("csrf_token") or "")
            headers["x-csrf-token"] = self.csrf_token
        try:
            with self._request_lock:
                response = self.session.request(
                    method, f"{self.base_url}{path}", json=payload,
                    headers=headers, timeout=timeout or self.timeout,
                )
            if response.status_code >= 400:
                detail = self._response_error(response)
                if response.status_code in {401, 403, 429}:
                    raise AuthenticationError(detail or "账号权限不足或登录已失效")
                raise AuthenticationUnavailable(
                    detail or f"中心服务返回 {response.status_code}")
            return response.json()
        except (AuthenticationError, AuthenticationUnavailable):
            raise
        except requests.RequestException as error:
            raise AuthenticationUnavailable(f"无法连接中心服务：{error}") from error
        except ValueError as error:
            raise AuthenticationUnavailable("中心服务返回了无效数据") from error

    def ensure_desktop_project(self) -> str:
        if self._desktop_project_id:
            return self._desktop_project_id
        with self._project_lock:
            if self._desktop_project_id:
                return self._desktop_project_id
            payload = self.request_json("GET", "/api/projects")
            user_id = str(self.user.get("id") or "")
            title = "CreativeEnginePro 桌面任务"
            project = next((item for item in payload.get("projects", [])
                            if str(item.get("title") or "") == title and
                            str(item.get("owner_id") or "") == user_id), None)
            if project is None:
                created = self.request_json("POST", "/api/projects", {
                    "title": title,
                    "canvas": {
                        "protocol": "creative-engine-canvas",
                        "version": 1, "nodes": [], "edges": [],
                        "desktopSource": {"client": "CreativeEnginePro"},
                    },
                }, write=True)
                project = created.get("project") or {}
            self._desktop_project_id = str(project.get("id") or "")
            if not self._desktop_project_id:
                raise AuthenticationUnavailable("中心服务未能创建桌面任务项目")
            return self._desktop_project_id

    def upload_asset(self, path: str | Path, node_id: str = "") -> dict[str, Any]:
        source = Path(path)
        if not source.is_file():
            raise FileNotFoundError(f"待上传参考素材不存在：{source}")
        project_id = self.ensure_desktop_project()
        if not self.csrf_token:
            token = self._request("GET", "/api/auth/csrf")
            self.csrf_token = str(token.get("csrf_token") or "")
        content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        try:
            with source.open("rb") as stream, self._request_lock:
                response = self.session.post(
                    f"{self.base_url}/api/assets",
                    data={"project_id": project_id, "node_id": node_id,
                          "metadata_json": json.dumps({"source": "desktop"})},
                    files={"file": (source.name, stream, content_type)},
                    headers={"x-csrf-token": self.csrf_token}, timeout=600,
                )
            if response.status_code >= 400:
                detail = self._response_error(response)
                if response.status_code in {401, 403, 429}:
                    raise AuthenticationError(detail or "参考素材上传被拒绝")
                raise AuthenticationUnavailable(
                    detail or f"参考素材上传失败（{response.status_code}）")
            return dict(response.json().get("asset") or {})
        except (AuthenticationError, AuthenticationUnavailable):
            raise
        except requests.RequestException as error:
            raise AuthenticationUnavailable(f"参考素材上传失败：{error}") from error

    def download_asset(self, asset_id: str) -> Path:
        from utils.app_paths import output_root

        target_dir = output_root() / "remote_results"
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            with self._request_lock:
                response = self.session.get(
                    f"{self.base_url}/api/assets/{asset_id}",
                    timeout=600, stream=True,
                )
                if response.status_code >= 400:
                    detail = self._response_error(response)
                    if response.status_code in {401, 403}:
                        raise AuthenticationError(detail or "生成结果下载权限已失效")
                    raise AuthenticationUnavailable(
                        detail or f"生成结果下载失败（{response.status_code}）")
                content_type = response.headers.get("content-type", "").split(";", 1)[0]
                extension = mimetypes.guess_extension(content_type) or ".bin"
                extension = {".jpe": ".jpg", ".mpeg": ".mp3"}.get(extension, extension)
                target = target_dir / f"{asset_id}{extension}"
                with target.open("wb") as stream:
                    for chunk in response.iter_content(1024 * 1024):
                        if chunk:
                            stream.write(chunk)
            return target
        except (AuthenticationError, AuthenticationUnavailable):
            raise
        except requests.RequestException as error:
            raise AuthenticationUnavailable(f"生成结果下载失败：{error}") from error

    @staticmethod
    def _server_time(payload: dict[str, Any]) -> datetime:
        raw = str(payload.get("server_time") or "")
        if not raw:
            raise AuthenticationUnavailable("登录服务器未返回可信时间")
        try:
            return _parse_datetime(raw)
        except ValueError as error:
            raise AuthenticationUnavailable("登录服务器时间格式无效") from error

    def login(self, username: str, password: str) -> LoginResult:
        payload = self._request("POST", "/api/auth/login", {
            "username": username.strip(), "password": password,
        })
        self.user = dict(payload.get("user") or {})
        self.csrf_token = str(payload.get("csrf_token") or "")
        return LoginResult(self.user, self._server_time(payload))

    def validate_session(self) -> LoginResult:
        payload = self._request("GET", "/api/auth/me")
        self.user = dict(payload.get("user") or {})
        return LoginResult(self.user, self._server_time(payload))


_desktop_control_client: DesktopAuthClient | None = None


def configure_desktop_control_client(client: DesktopAuthClient | None) -> None:
    global _desktop_control_client
    _desktop_control_client = client


def desktop_control_client() -> DesktopAuthClient | None:
    return _desktop_control_client
