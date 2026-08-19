"""Pinterest 公共页面图片导入器。

只接收 Pinterest / pin.it 页面地址，只下载页面已经公开返回的 i.pinimg.com
图片。模块本身不依赖 Qt，便于轮播工作台在线程中调用和独立测试。

v2 — 支持搜索/集合页 hydration JSON 提取 + 竖屏比例过滤。
"""
from __future__ import annotations

import hashlib
import html as html_lib
import io
import json
import os
import re
import time
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from threading import Event
from typing import Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# 匹配 http/https 以及协议相对 `//` 开头的 i.pinimg.com 图片链接
_PINIMG_URL_RE = re.compile(
    r"(?:https?:)?//i\.pinimg\.com/[^\s\"'<>]+",
    re.IGNORECASE,
)

_PIN_LINK_RE = re.compile(
    r"(?:https://(?:[a-z0-9-]+\.)*pinterest\.[a-z.]+)?"
    r"(/pin/(?:[^/?#\"'<>\s]+--)?[0-9]+/?(?:\?[^\"'<>\s]*)?)",
    re.IGNORECASE,
)

_SIZE_TOKEN_RE = re.compile(r"^(?:originals|\d+x(?:\d+)?(?:_RS)?)$", re.IGNORECASE)

# 用于提取 <script> 标签内容中的图片 URL（搜索页 hydration JSON 常在此处）
_SCRIPT_TAG_RE = re.compile(
    r"<script\b[^>]*?>\s*(.*?)\s*</script>",
    re.DOTALL | re.IGNORECASE,
)

# Pinterest 搜索页 JSON 中常见的图片对象模式：
#   "images":{"orig":{"url":"https://i.pinimg.com/originals/..."}}
_JSON_IMAGE_OBJ_RE = re.compile(
    r'"images"\s*:\s*\{[^}]*?"orig"\s*:\s*\{[^}]*?"url"\s*:\s*"(https?://i\.pinimg\.com/[^"]+)"',
    re.IGNORECASE | re.DOTALL,
)

# ── 比例过滤默认值 ──
# 竖屏 = 高/宽 >= 16/9 ≈ 1.778  (即 9:16 或更窄)
DEFAULT_MIN_ASPECT = 16.0 / 9.0   # height/width
DEFAULT_MIN_SIDE = 240             # 任一边的最小像素


def is_pinterest_page_url(url: str) -> bool:
    """仅允许 Pinterest 页面，避免把通用下载器变成任意 URL 请求器。"""
    try:
        parsed = urlsplit((url or "").strip())
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if host in {"pin.it", "www.pin.it"}:
        return True
    return bool(re.fullmatch(r"(?:[a-z0-9-]+\.)*pinterest\.[a-z.]+", host))


def _decode_page_text(text: str) -> str:
    decoded = html_lib.unescape(text or "")
    # Pinterest 的 hydration JSON 同时出现 JSON 与 unicode 两种斜杠转义。
    for old, new in (("\\u002F", "/"), ("\\u002f", "/"),
                     ("\\/", "/"), ("\\u0026", "&")):
        decoded = decoded.replace(old, new)
    return decoded


def _image_signature(url: str) -> tuple[str, str, int]:
    """返回 (去尺寸后的唯一签名, 原图候选 URL, 清晰度分值)。"""
    parsed = urlsplit(url)
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return parsed.path, url, 0
    token = parts[0]
    if _SIZE_TOKEN_RE.fullmatch(token) and len(parts) > 1:
        signature = "/".join(parts[1:]).lower()
        original_path = "/originals/" + "/".join(parts[1:])
        preferred = urlunsplit(("https", "i.pinimg.com", original_path, "", ""))
        if token.lower() == "originals":
            score = 10000
        else:
            match = re.match(r"(\d+)", token)
            score = int(match.group(1)) if match else 0
    else:
        signature = "/".join(parts).lower()
        preferred = urlunsplit(("https", "i.pinimg.com", parsed.path, "", ""))
        score = 5000
    return signature, preferred, score


def _normalize_pinimg_url(raw_url: str) -> str | None:
    """把各种形式的 i.pinimg.com URL 统一成 https://i.pinimg.com/path。"""
    raw_url = raw_url.rstrip(".,;:)]}\"'")
    parsed = urlsplit(raw_url)
    host = (parsed.hostname or "").lower()
    if host != "i.pinimg.com":
        return None
    if not parsed.path:
        return None
    if Path(parsed.path).suffix.lower() not in _IMAGE_EXTS:
        return None
    return urlunsplit(("https", "i.pinimg.com", parsed.path, "", ""))


def extract_pinimg_candidates(page_text: str) -> list[dict]:
    """从公开 HTML / hydration JSON 提取并合并同一图片的不同尺寸。"""
    decoded = _decode_page_text(page_text)
    grouped: dict[str, dict] = {}
    for match in _PINIMG_URL_RE.finditer(decoded):
        clean = _normalize_pinimg_url(match.group(0))
        if not clean:
            continue
        parsed = urlsplit(clean)
        parts = [part for part in parsed.path.split("/") if part]
        size_token = parts[0] if parts else ""
        # *_RS 主要是头像；小于 200px 的资源通常是站点图标或占位图。
        if size_token.upper().endswith("_RS"):
            continue
        size_match = re.fullmatch(r"(\d+)x(?:\d+)?", size_token, re.IGNORECASE)
        if size_match and int(size_match.group(1)) < 200:
            continue
        signature, preferred, score = _image_signature(clean)
        current = grouped.get(signature)
        if current is None:
            grouped[signature] = {
                "signature": signature,
                "preferred_url": preferred,
                "fallback_url": clean,
                "score": score,
            }
        elif score > current["score"]:
            current["fallback_url"] = clean
            current["score"] = score
    return list(grouped.values())


def extract_pin_links(page_url: str, page_text: str) -> list[str]:
    """提取看板/搜索页中的 Pin 详情链接，供首屏图片不足时补抓。"""
    decoded = _decode_page_text(page_text)
    links = []
    seen = set()
    for match in _PIN_LINK_RE.finditer(decoded):
        link = urljoin(page_url, match.group(1).split("?", 1)[0])
        if link not in seen:
            seen.add(link)
            links.append(link)
    return links


def extract_hydration_images(page_text: str) -> list[dict]:
    """
    从 Pinterest 搜索/浏览页面的 <script> JSON hydration 数据中提取图片。

    Pinterest 搜索页的初始 HTML 常把图片数据藏在 <script id="__PWS_DATA__">
    或类似标签中。标准正则可能遗漏 JSON 键深层嵌套的 URL，
    这里在 script 内容中做额外扫描。
    """
    decoded = _decode_page_text(page_text)
    grouped: dict[str, dict] = {}

    # 策略1：在所有 <script> 标签内容中单独扫描 i.pinimg.com URL
    for script_match in _SCRIPT_TAG_RE.finditer(decoded):
        script_text = script_match.group(1)
        if "pinimg.com" not in script_text.lower():
            continue
        for img_match in _PINIMG_URL_RE.finditer(script_text):
            clean = _normalize_pinimg_url(img_match.group(0))
            if not clean:
                continue
            parsed = urlsplit(clean)
            parts = [part for part in parsed.path.split("/") if part]
            size_token = parts[0] if parts else ""
            if size_token.upper().endswith("_RS"):
                continue
            signature, preferred, score = _image_signature(clean)
            current = grouped.get(signature)
            if current is None:
                grouped[signature] = {
                    "signature": signature,
                    "preferred_url": preferred,
                    "fallback_url": clean,
                    "score": score,
                }
            elif score > current["score"]:
                current["fallback_url"] = clean
                current["score"] = score

    # 策略2：匹配 Pinterest JSON 中 "images" → "orig" → "url" 的深层对象
    for match in _JSON_IMAGE_OBJ_RE.finditer(decoded):
        url = match.group(1)
        clean = _normalize_pinimg_url(url)
        if not clean:
            continue
        signature, preferred, score = _image_signature(clean)
        current = grouped.get(signature)
        if current is None:
            grouped[signature] = {
                "signature": signature,
                "preferred_url": preferred,
                "fallback_url": clean,
                "score": max(score, 8000),
            }
        elif score > current["score"]:
            current["fallback_url"] = clean
            current["score"] = score

    return list(grouped.values())


def _safe_parse_json(text: str):
    """尽量解析 JSON，失败返回 None（用于 <script> hydration 内容）。"""
    import json as json_lib
    if not text:
        return None
    # Pinterest 偶尔在 JSON 前加 )]}' 防 XSSI，需要剥掉
    stripped = text.lstrip()
    for prefix in (")]}'\n", ")]}'", "/*--*/", "<!--"):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):].lstrip()
            break
    if not stripped:
        return None
    try:
        return json_lib.loads(stripped)
    except Exception:
        return None


def _walk_json_tree(obj, found: list, depth: int = 0) -> None:
    """递归遍历 JSON 树，收集所有 i.pinimg.com 图片 URL。"""
    if depth > 25:
        return
    if isinstance(obj, dict):
        images = obj.get("images")
        if isinstance(images, dict):
            for size_data in images.values():
                if isinstance(size_data, dict):
                    url = size_data.get("url")
                    if isinstance(url, str) and "pinimg.com" in url:
                        found.append(url)
                    # Pinterest 也把 url 放在 "url" 键下嵌套对象
                    if isinstance(url, dict):
                        nested = url.get("url")
                        if isinstance(nested, str) and "pinimg.com" in nested:
                            found.append(nested)
        for v in obj.values():
            _walk_json_tree(v, found, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _walk_json_tree(item, found, depth + 1)


def extract_from_json_payload(payload_text: str) -> list[dict]:
    """
    解析一段 JSON（来自 <script> 或 API 响应），递归提取所有图片 URL。
    """
    found: list[str] = []
    obj = _safe_parse_json(payload_text)
    if obj is not None:
        _walk_json_tree(obj, found)
    # JSON 解析失败时退回到正则扫描
    if not found:
        return extract_hydration_images(payload_text)
    grouped: dict[str, dict] = {}
    for url in found:
        clean = _normalize_pinimg_url(url)
        if not clean:
            continue
        signature, preferred, score = _image_signature(clean)
        current = grouped.get(signature)
        if current is None:
            grouped[signature] = {
                "signature": signature,
                "preferred_url": preferred,
                "fallback_url": clean,
                "score": score,
            }
        elif score > current["score"]:
            current["fallback_url"] = clean
            current["score"] = score
    return list(grouped.values())


def extract_hydration_script(page_text: str) -> str | None:
    """
    从页面 HTML 抠出 __PWS_DATA__ / __INITIAL_STATE__ / __NEXT_DATA__ 的 JSON 串。
    """
    if not page_text:
        return None
    for script_match in _SCRIPT_TAG_RE.finditer(page_text):
        attrs = page_text[script_match.start():script_match.start() + 600]
        if any(token in attrs for token in (
                "__PWS_DATA__", "__INITIAL_STATE__", "__NEXT_DATA__",
                "application/json", "application/ld+json")):
            body = script_match.group(1).strip()
            if body.startswith("{") or body.startswith("["):
                return body
    return None


def parse_search_query(url: str) -> str | None:
    """从 Pinterest 搜索 URL 提取查询关键词。"""
    from urllib.parse import parse_qs
    parsed = urlsplit(url)
    qs = parse_qs(parsed.query)
    values = qs.get("q", [])
    return values[0].strip() if values and values[0].strip() else None


class PinterestImporter:
    """解析 Pinterest 页面并把指定数量图片标准化保存到本地。"""

    def __init__(self, cookie_file: str = ""):
        import requests

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/127.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Sec-Ch-Ua": '"Chromium";v="127", "Not)A;Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
        })
        self._load_cookies(cookie_file)

    def _load_cookies(self, cookie_file: str):
        if not cookie_file or not os.path.isfile(cookie_file):
            return
        try:
            jar = MozillaCookieJar(cookie_file)
            jar.load(ignore_discard=True, ignore_expires=True)
            self.session.cookies.update(jar)
        except Exception:
            pass

    def _get_csrf_token(self) -> str:
        """安全取 pinterest.com 域的 csrftoken（其他站点同名 cookie 不会撞车）。"""
        try:
            token = self.session.cookies.get(
                "csrftoken", default="", domain=".pinterest.com")
            if token:
                return token
        except Exception:
            pass
        # 退而求其次：遍历 session cookies 找
        for cookie in self.session.cookies:
            if cookie.name == "csrftoken" and "pinterest.com" in (
                    cookie.domain or "").lstrip("."):
                return cookie.value or ""
        return ""

    def _get_page(self, url: str):
        response = self.session.get(url, timeout=(10, 30), allow_redirects=True)
        response.raise_for_status()
        if not is_pinterest_page_url(response.url):
            raise RuntimeError("Pinterest 短链跳转到了非 Pinterest 页面，已停止")
        return response

    def _is_search_or_browse_url(self, url: str) -> bool:
        """判断是否为搜索 / 浏览 / 看板类集合页面。"""
        lowered = urlsplit(url).path.lower()
        return any(seg in lowered for seg in ("/search/", "/board/", "/boards/"))

    def _fetch_via_resource_api(
        self,
        source_url: str,
        query: str | None,
        limit: int,
        stop_event: Event,
        progress: Callable[[int, int, str], None],
    ) -> list[dict]:
        """
        Pinterest 前端用的资源 API 后端直调，绕开 JS 渲染。

        适用：搜索页 (`/search/pins/`) 没有登录 cookie 也能拉回部分公开数据。
        """
        import json as json_lib

        candidates: list[dict] = []
        seen: set[str] = set()

        # 已知常用的 Pinterest 资源端点
        endpoints = [
            ("https://www.pinterest.com/resource/BaseSearchResource/get/",
             {"query": query or "", "page_size": min(limit, 50),
              "scope": "pins", "rs": "ac"}),
        ]

        for endpoint, options in endpoints:
            if stop_event.is_set() or len(candidates) >= limit:
                break
            progress(len(candidates), limit, f"正在直连 Pinterest 资源接口…")
            payload = {
                "source_url": source_url,
                "data": json_lib.dumps(
                    {"options": options, "context": {}}),
            }
            csrf = self._get_csrf_token()
            try:
                response = self.session.post(
                    endpoint,
                    data=payload,
                    headers={
                        "Accept": "application/json, text/javascript, */*; q=0.01",
                        "X-Requested-With": "XMLHttpRequest",
                        "Content-Type": "application/x-www-form-urlencoded",
                        "X-CSRFToken": csrf,
                        "Referer": source_url,
                    },
                    timeout=(10, 30),
                )
                response.raise_for_status()
                items = extract_from_json_payload(response.text)
                for item in items:
                    if item["signature"] in seen:
                        continue
                    seen.add(item["signature"])
                    candidates.append(item)
                if items:
                    break  # 成功就直接返回
            except Exception:
                continue

        return candidates

    def collect_candidates(
        self,
        page_url: str,
        fetch_target: int,
        display_total: int,
        stop_event: Event,
        progress: Callable[[int, int, str], None],
    ) -> tuple[str, list[dict]]:
        """
        收集候选图片 URL。

        fetch_target: 实际要尝试获取的候选数量（含冗余以应对后续过滤）。
        display_total: 进度条显示的总数（用户期望的目标数）。
        """
        progress(0, display_total, "正在解析 Pinterest 页面…")
        response = self._get_page(page_url)
        final_url = response.url
        page_text = response.text

        # 1) 标准扫描（整页 HTML）
        candidates = extract_pinimg_candidates(page_text)
        known = {item["signature"] for item in candidates}

        # 2) 搜索/集合页额外从 hydration JSON 中提取
        if (self._is_search_or_browse_url(final_url)
                and len(candidates) < fetch_target
                and not stop_event.is_set()):
            progress(
                min(len(candidates), display_total), display_total,
                "正在搜索页面嵌入数据…",
            )
            for item in extract_hydration_images(page_text):
                if item["signature"] not in known:
                    known.add(item["signature"])
                    candidates.append(item)
            # 2b) 尝试解析整段 JSON 树
            script_json = extract_hydration_script(page_text)
            if script_json and not stop_event.is_set():
                for item in extract_from_json_payload(script_json):
                    if item["signature"] not in known:
                        known.add(item["signature"])
                        candidates.append(item)

        # 3) 前两步没数据且是搜索页 → 直接调 Pinterest 资源 API
        if (len(candidates) < fetch_target
                and self._is_search_or_browse_url(final_url)
                and not stop_event.is_set()):
            query = parse_search_query(final_url) or parse_search_query(page_url)
            if query:
                api_items = self._fetch_via_resource_api(
                    final_url, query, fetch_target, stop_event, progress)
                for item in api_items:
                    if item["signature"] not in known:
                        known.add(item["signature"])
                        candidates.append(item)

        # 4) 仍然不足 → 逐个访问页面中列出的 Pin 详情链接
        if len(candidates) < fetch_target and not stop_event.is_set():
            links = extract_pin_links(final_url, page_text)
            max_detail_pages = min(len(links), max(fetch_target * 2, 20), 300)
            for index, link in enumerate(links[:max_detail_pages], start=1):
                if stop_event.is_set() or len(candidates) >= fetch_target:
                    break
                progress(
                    min(len(candidates), display_total), display_total,
                    f"正在展开 Pin {index}/{max_detail_pages}…",
                )
                try:
                    detail = self._get_page(link)
                    for item in extract_pinimg_candidates(detail.text):
                        if item["signature"] not in known:
                            known.add(item["signature"])
                            candidates.append(item)
                except Exception:
                    continue

        return final_url, candidates

    def _download_bytes(self, url: str, referer: str) -> tuple[bytes, str]:
        response = self.session.get(
            url,
            headers={"Referer": referer, "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"},
            timeout=(10, 45),
            stream=True,
        )
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if content_type and not content_type.startswith("image/"):
            raise RuntimeError(f"返回内容不是图片：{content_type}")
        limit = 30 * 1024 * 1024
        length = int(response.headers.get("Content-Length", "0") or 0)
        if length > limit:
            raise RuntimeError("图片超过 30 MB")
        chunks = []
        total = 0
        for chunk in response.iter_content(128 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > limit:
                raise RuntimeError("图片超过 30 MB")
            chunks.append(chunk)
        return b"".join(chunks), response.url

    @staticmethod
    def _save_standard_image(
        data: bytes,
        output_dir: Path,
        index: int,
        min_aspect: float = 0.0,
        min_side: int = 200,
    ) -> tuple[Path, str]:
        """
        标准化保存图片。

        min_aspect: 最低 高/宽 比例。例如 16/9≈1.778 表示只接受 9:16 或更窄的竖屏图。
                     0 表示不做比例过滤。
        min_side:   图片任一边的最低像素数。

        Raises RuntimeError 如果图片不满足过滤条件。
        """
        from PIL import Image, ImageOps

        digest = hashlib.sha256(data).hexdigest()
        with Image.open(io.BytesIO(data)) as source:
            try:
                source.seek(0)
            except EOFError:
                pass
            image = ImageOps.exif_transpose(source).copy()

        w, h = image.width, image.height

        # 尺寸下限
        if w < min_side or h < min_side:
            raise RuntimeError(
                f"图片尺寸过小 ({w}x{h}，最低要求每边 ≥{min_side}px)")

        # 竖屏比例过滤
        if min_aspect > 0 and w > 0:
            ratio = h / w
            if ratio < min_aspect:
                raise RuntimeError(
                    f"图片非竖屏 ({w}x{h}，比例 {ratio:.2f} < {min_aspect:.2f})")

        has_alpha = image.mode in ("RGBA", "LA") or (
            image.mode == "P" and "transparency" in image.info)
        if has_alpha:
            image = image.convert("RGBA")
            suffix = ".png"
        else:
            image = image.convert("RGB")
            suffix = ".jpg"
        path = output_dir / f"Pinterest_{index:04d}_{digest[:10]}{suffix}"
        if suffix == ".png":
            image.save(path, format="PNG", optimize=True)
        else:
            image.save(path, format="JPEG", quality=95, optimize=True)
        return path, digest

    def import_page(
        self,
        page_url: str,
        desired: int,
        output_dir: str | Path,
        stop_event: Event | None = None,
        progress: Callable[[int, int, str], None] | None = None,
        item_ready: Callable[[str, str], None] | None = None,
        min_aspect: float = 0.0,
        min_side: int = 200,
    ) -> dict:
        """
        导入 Pinterest 页面图片。

        desired:    期望下载的图片数量（过滤后）。
        min_aspect: 最低 高/宽 比例。0=不过滤。竖屏图建议 16/9≈1.778。
        min_side:   图片任一边最低像素。
        """
        if not is_pinterest_page_url(page_url):
            raise ValueError("请输入 pinterest.com 或 pin.it 页面链接")
        desired = max(1, min(int(desired), 500))
        stop_event = stop_event or Event()
        progress = progress or (lambda _done, _total, _text: None)
        item_ready = item_ready or (lambda _path, _url: None)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # 如果有比例过滤，多拉一些候选以弥补过滤淘汰
        fetch_factor = max(2, int(desired * 1.5)) if min_aspect > 0 else 1
        fetch_target = min(desired * fetch_factor, 500)

        final_url, candidates = self.collect_candidates(
            page_url, fetch_target, desired, stop_event, progress)
        if not candidates:
            is_search = self._is_search_or_browse_url(final_url)
            hint = (
                "搜索页需要登录才能拿到完整结果；请在设置里导入 Pinterest Cookie 后重试。"
                if is_search else
                "页面没有返回可下载的 Pinterest 图片；若页面需要登录，请在设置中导入 Cookie。"
            )
            raise RuntimeError(hint)

        downloaded = []
        failures = []
        content_hashes = set()
        candidate_idx = 0
        saved_index = 0

        for candidate in candidates:
            if stop_event.is_set() or len(downloaded) >= desired:
                break
            candidate_idx += 1
            progress(
                min(len(downloaded), desired), desired,
                f"正在下载 {candidate_idx}/{len(candidates)}…",
            )
            urls = [candidate["preferred_url"]]
            if candidate["fallback_url"] not in urls:
                urls.append(candidate["fallback_url"])
            last_error = "下载失败"
            for image_url in urls:
                if stop_event.is_set():
                    break
                try:
                    data, actual_url = self._download_bytes(image_url, final_url)
                    saved_index += 1
                    path, digest = self._save_standard_image(
                        data, output_dir, saved_index,
                        min_aspect=min_aspect, min_side=min_side,
                    )
                    if digest in content_hashes:
                        path.unlink(missing_ok=True)
                        last_error = "重复图片"
                        break
                    content_hashes.add(digest)
                    record = {
                        "path": str(path),
                        "image_url": actual_url,
                        "page_url": final_url,
                        "source": "Pinterest",
                    }
                    downloaded.append(record)
                    item_ready(str(path), actual_url)
                    break
                except Exception as exc:
                    last_error = str(exc)[:100]
            else:
                failures.append(last_error)

        # 进度条收尾
        progress(len(downloaded), desired, f"已筛选 {len(downloaded)}/{desired} 张")

        manifest = {
            "source": "Pinterest",
            "page_url": final_url,
            "requested": desired,
            "downloaded": len(downloaded),
            "stopped": stop_event.is_set(),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "items": downloaded,
            "filter_min_aspect": min_aspect,
            "filter_min_side": min_side,
        }
        (output_dir / "pinterest_source.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest["failures"] = failures[:20]
        manifest["output_dir"] = str(output_dir)
        return manifest
