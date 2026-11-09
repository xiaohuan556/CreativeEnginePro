"""Duo delivery protocol and durable local copies for an in-flight batch."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import uuid4

import requests


DEFAULT_DUO_URL = "http://10.13.13.216:3080"
DELIVERY_API = "/api/desktop/deliveries"
REQUEST_API = "/api/desktop/requests"
MISSING_REQUEST = "未找到对应的已发布需求。请先在需求对接平台发布需求，或确认当前账号有提交权限。"
MEDIA_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".mp4": "video/mp4", ".webm": "video/webm",
}
FILE_URL = re.compile(r"/api/files/([a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})\Z")
LAN_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "::1/128",
))


class DuoError(RuntimeError):
    def __init__(self, message, status=0, code=""):
        super().__init__(message)
        self.status = status
        self.code = code


def duo_url(value):
    try:
        parsed = urlsplit(value.strip())
        parsed.port  # Validate the port even if it is supplied by the user.
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError
        if parsed.scheme == "http":
            address = ipaddress.ip_address(parsed.hostname)
            if not any(address in network for network in LAN_NETWORKS):
                raise ValueError
    except ValueError as error:
        raise DuoError("请输入有效的平台地址。局域网 IP 可用 HTTP，域名请使用 HTTPS。") from error
    return f"{parsed.scheme}://{parsed.netloc}"


class DuoClient:
    def __init__(self, address):
        self.address = duo_url(address)
        self.session = requests.Session()
        self.session.trust_env = False
        self.user = None

    def close(self):
        self.session.close()

    def _call(self, method, path, **kwargs):
        headers = {"Origin": self.address, **kwargs.pop("headers", {})}
        try:
            with self.session.request(method, self.address + path, headers=headers,
                                      timeout=(8, 130), allow_redirects=False, **kwargs) as response:
                status = response.status_code
                try:
                    value = response.json()
                except ValueError:
                    value = {}
        except requests.RequestException as error:
            raise DuoError("平台连接中断或超时，请检查内网连接后重试。") from error
        if not isinstance(value, dict):
            value = {}
        if status == 404 and path == DELIVERY_API and method == "GET":
            raise DuoError("需求对接平台尚未启用桌面交付，请更新服务器端。", status)
        if not 200 <= status < 300:
            detail = value.get("error")
            raise DuoError(detail.replace("Duo", "需求对接平台") if isinstance(detail, str) and len(detail) <= 240
                           else f"平台请求失败（{status}）", status, value.get("code", ""))
        return value

    def login(self, account, password):
        # Confirm the delivery endpoint exists before sending login information.
        try:
            self._call("GET", DELIVERY_API)
        except DuoError as error:
            if error.status != 401:
                raise
        self._call("POST", "/api/auth/login", json={"account": account, "password": password, "remember": False})
        return self.requests()

    def requests(self, request_id=None):
        kwargs = {"params": {"requestId": request_id}} if request_id is not None else {}
        try:
            result = self._call("GET", DELIVERY_API, **kwargs)
        except DuoError as error:
            if error.status == 404 and request_id is not None:
                raise DuoError(MISSING_REQUEST, 404) from error
            raise
        user = result.get("user")
        if (result.get("version") != 1 or result.get("singleDelivery") is not True
                or not isinstance(user, dict) or not user.get("id")
                or not isinstance(result.get("requests"), list)):
            raise DuoError("请更新需求对接平台服务端，以启用防重复交付和发布需求。")
        self.user = user
        return result

    def publication_form(self):
        result = self._call("GET", REQUEST_API)
        if not isinstance(result.get("today"), str) or any(not isinstance(result.get(field), list) for field in (
            "products", "channels", "requesters", "designers", "priorities", "tags",
        )):
            raise DuoError("平台返回的发布选项无效，请刷新或在网页发布。")
        return result

    def publish_request(self, publication_id, fields, attachments, progress=lambda _: None):
        for index, item in enumerate(attachments, 1):
            if not item["upload_id"]:
                path = Path(item["source"])
                stat = path.stat()
                if (stat.st_size, stat.st_mtime_ns) != (item["size"], item["mtime_ns"]):
                    raise DuoError(f"{path.name} 已发生变化，请重新选择参考附件。", 400)
                progress(f"正在上传参考附件 {index}/{len(attachments)}…")
                item["upload_id"] = self.upload(path, item["original_name"], item["mime"])
        progress("正在发布需求…")
        try:
            result = self._call("POST", REQUEST_API, json={
                **fields, "publicationId": publication_id, "uploadIds": [item["upload_id"] for item in attachments],
            })
        except DuoError as error:
            if not error.status or error.status >= 500:
                raise DuoError("发布结果未确认。可重试以确认同一条需求。", code="publication_unconfirmed") from error
            raise
        if type(result.get("requestId")) is not int or result["requestId"] < 1:
            raise DuoError("发布结果未确认，请重试确认。", code="publication_unconfirmed")
        return result["requestId"]

    def upload(self, path, original_name, mime):
        with Path(path).open("rb") as stream:
            value = self._call("POST", "/api/files", data=stream,
                               headers={"Content-Type": mime, "X-File-Name": quote(original_name, safe="")})
        match = FILE_URL.fullmatch(str(value.get("url") or ""))
        if match is None:
            raise DuoError("平台没有返回有效的附件编号。")
        return match[1]

    def commit(self, batch):
        return self._call("POST", DELIVERY_API, json={
            "batchId": batch["id"], "requestId": batch["request_id"],
            "uploadIds": [item["upload_id"] for item in batch["items"]],
        })


def inspect_media(path):
    path = Path(path).resolve()
    mime = MEDIA_TYPES.get(path.suffix.lower())
    if not mime:
        raise DuoError(f"{path.name}：平台支持 JPG、PNG、WebP、GIF、MP4 和 WebM。")
    try:
        stats = path.stat()
        if not path.is_file() or not stats.st_size:
            raise OSError
    except OSError as error:
        raise DuoError(f"{path.name}：文件不存在或为空。") from error
    limit = 10 if mime.startswith("image/") else 50
    if stats.st_size > limit * 1024 * 1024:
        raise DuoError(f"{path.name}：超过 {limit} MB 的单文件限制。")
    return {"source": str(path), "original_name": path.name, "mime": mime,
            "size": stats.st_size, "mtime_ns": stats.st_mtime_ns, "upload_id": ""}


class DeliveryJournal:
    def __init__(self, path):
        self.path = Path(path)

    def read(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (ValueError, OSError) as error:
            raise DuoError("无法读取上次的交付记录，请检查本地文件后重试。") from error

    def write(self, batch):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.path)

    def start(self, client, request_id, paths, archive):
        if self.read() is not None:
            raise DuoError("请先处理上次的交付。")
        if type(request_id) is not int or request_id < 1:
            raise DuoError(MISSING_REQUEST)
        if not 1 <= len(paths) <= 200:
            raise DuoError("请选择 1–200 个成品。")
        if not str(archive).strip():
            raise DuoError("请选择本地归档目录。")
        items = [inspect_media(path) for path in paths]
        if len({os.path.normcase(item["source"]) for item in items}) != len(items):
            raise DuoError("同一个文件只能加入一次。")
        account = self._target(client, request_id)
        batch_id = str(uuid4())
        folder = Path(archive).expanduser().resolve() / f"REQ-{request_id}" / batch_id[:12]
        for index, item in enumerate(items, 1):
            item["staged"] = f"待提交_{index:03d}_{item['original_name'][-160:]}"
        batch = {"id": batch_id, "request_id": request_id, "address": client.address, "actor_id": account,
                 "folder": str(folder), "items": items}
        self.write(batch)
        return batch

    @staticmethod
    def _target(client, request_id, actor_id=None):
        if actor_id and (not client.user or client.user["id"] != actor_id):
            raise DuoError("请使用发起这批交付的平台账号继续。")
        listing = client.requests(request_id)
        account = listing["user"]["id"]
        if not any(item.get("id") == request_id for item in listing["requests"]):
            raise DuoError(MISSING_REQUEST, 404)
        return account

    def _local_copies(self, batch, progress):
        folder = Path(batch["folder"])
        folder.mkdir(parents=True, exist_ok=True)
        for index, item in enumerate(batch["items"], 1):
            progress(f"正在保存到本地 {index}/{len(batch['items'])}")
            staged = folder / item["staged"]
            if staged.is_file() and staged.stat().st_size == item["size"]:
                continue
            source = Path(item["source"])
            temporary = staged.with_suffix(staged.suffix + ".part")
            try:
                stats = source.stat()
                if (stats.st_size, stats.st_mtime_ns) != (item["size"], item["mtime_ns"]):
                    raise DuoError(f"{source.name} 已被重新导出，请保留原文件后重试。")
                shutil.copyfile(source, temporary)
                after = source.stat()
                if (after.st_size, after.st_mtime_ns) != (stats.st_size, stats.st_mtime_ns):
                    raise DuoError(f"{source.name} 在复制期间发生变化，请重试。")
                os.replace(temporary, staged)
            finally:
                temporary.unlink(missing_ok=True)
        return folder

    def resume(self, batch, client, progress=lambda _: None):
        folder = Path(batch["folder"])
        if "receipt" not in batch:
            if client is None or client.address != batch["address"]:
                raise DuoError("请登录发起这批交付的平台后重试。")
            if client.user is None or client.user["id"] != batch["actor_id"]:
                raise DuoError("请使用发起这批交付的平台账号继续。")
            try:
                if not all(item["upload_id"] for item in batch["items"]):
                    self._target(client, batch["request_id"], batch["actor_id"])
                    self._local_copies(batch, progress)
                    for index, item in enumerate(batch["items"], 1):
                        if not item["upload_id"]:
                            progress(f"正在上传成品 {index}/{len(batch['items'])}")
                            item["upload_id"] = client.upload(folder / item["staged"], item["original_name"], item["mime"])
                            self.write(batch)
                progress(f"正在提交到已发布需求 REQ-{batch['request_id']}")
                receipt = client.commit(batch)
            except DuoError as error:
                if error.code == "already_delivered":
                    self.path.unlink(missing_ok=True)
                if error.status == 404:
                    raise DuoError(MISSING_REQUEST, 404) from error
                raise
            self._check_receipt(batch, receipt)
            batch["receipt"] = receipt
            self.write(batch)

        progress("正在按平台确认的文件名归档")
        receipt = batch["receipt"]
        self._check_receipt(batch, receipt)
        for item, file in zip(batch["items"], receipt["files"]):
            staged, final = folder / item["staged"], folder / file["name"]
            if staged.is_file():
                if final.exists():
                    raise DuoError(f"本地归档已存在同名文件：{final.name}")
                staged.rename(final)
            elif not final.is_file() or final.stat().st_size != item["size"]:
                raise DuoError(f"无法归档 {file['name']}：本地文件不完整。")
        self.path.unlink()
        return receipt, folder

    @staticmethod
    def _check_receipt(batch, receipt):
        files = receipt.get("files")
        if (receipt.get("batchId") != batch["id"] or receipt.get("requestId") != batch["request_id"]
                or not isinstance(files, list) or len(files) != len(batch["items"])):
            raise DuoError("平台提交回执无效，请重试当前批次。")
        names = set()
        for item, file in zip(batch["items"], files):
            name = file.get("name", "") if isinstance(file, dict) else ""
            if (not isinstance(name, str) or not name or name in {".", ".."} or name.casefold() in names
                    or re.search(r'[\\/:*?"<>|\x00-\x1f]', name) or name.endswith((" ", "."))
                    or file.get("url") != "/api/files/" + item["upload_id"] or file.get("size") != item["size"]):
                raise DuoError("平台返回的文件名或附件记录无效。")
            names.add(name.casefold())
