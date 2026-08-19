# -*- coding: utf-8 -*-
"""Openverse 音频搜索与下载客户端。

Openverse 无需 API Key 即可匿名搜索。下载时在音频旁写入 ``.license.json``，
把作者、来源页和许可证一起保存，便于后续商业项目追溯。
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable


API_URL = "https://api.openverse.org/v1/audio/"
USER_AGENT = "CreativeEnginePro/1.0 (Openverse audio integration)"
MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024

LICENSE_FILTERS = {
    # 排除 NC（禁止商用）与 ND（禁止改编），适合视频剪辑默认使用。
    "commercial": "cc0,pdm,by,by-sa",
    "no_attribution": "cc0,pdm",
    "attribution": "by",
}

LICENSE_LABELS = {
    "cc0": "CC0 · 免署名",
    "pdm": "公共领域",
    "by": "CC BY · 需署名",
    "by-sa": "CC BY-SA · 需署名/相同方式共享",
    "by-nd": "CC BY-ND · 禁止改编",
    "by-nc": "CC BY-NC · 禁止商用",
    "by-nc-sa": "CC BY-NC-SA · 禁止商用",
    "by-nc-nd": "CC BY-NC-ND · 禁止商用/改编",
    "sampling+": "Sampling+",
}

# Openverse 的标题和标签以英文为主。常用剪辑词优先本地转换，避免每次搜索都
# 依赖外部翻译服务；不在词表中的中文再尝试项目已有的免费翻译器。
_CN_QUERY_EXACT = {
    "雨声": "rain sound",
    "风声": "wind sound",
    "雷声": "thunder sound",
    "海浪": "ocean waves",
    "鸟叫": "bird song nature",
    "脚步声": "footsteps sound",
    "点击音效": "button click sound effect",
    "按钮音效": "button click sound effect",
    "转场音效": "transition whoosh sound effect",
    "爆炸音效": "explosion sound effect",
    "键盘声": "keyboard typing sound",
    "欢快音乐": "cheerful upbeat music",
    "轻快音乐": "light upbeat music",
    "科技音乐": "futuristic technology music",
    "史诗音乐": "epic cinematic music",
    "紧张音乐": "tense suspense music",
    "浪漫音乐": "romantic music",
    "悲伤音乐": "sad emotional music",
    "治愈音乐": "calm relaxing music",
    "背景音乐": "background music",
    "白噪音": "white noise ambience",
}

_CN_QUERY_PARTS = {
    "中国风": "traditional chinese",
    "古风": "traditional asian",
    "欢快": "cheerful upbeat",
    "轻快": "light upbeat",
    "活力": "energetic",
    "科技": "futuristic technology",
    "史诗": "epic cinematic",
    "大气": "epic atmospheric",
    "紧张": "tense suspense",
    "悬疑": "mystery suspense",
    "浪漫": "romantic",
    "悲伤": "sad emotional",
    "治愈": "calm relaxing",
    "安静": "quiet calm",
    "搞笑": "funny comedy",
    "广告": "advertising commercial",
    "背景音乐": "background music",
    "音乐": "music",
    "转场": "transition whoosh",
    "点击": "button click",
    "按钮": "button click",
    "爆炸": "explosion",
    "脚步": "footsteps",
    "键盘": "keyboard typing",
    "雨声": "rain sound",
    "风声": "wind sound",
    "雷声": "thunder sound",
    "海浪": "ocean waves",
    "鸟叫": "bird song",
    "汽车": "car sound",
    "城市": "urban ambience",
    "自然": "nature ambience",
    "办公室": "office ambience",
    "白噪音": "white noise",
    "音效": "sound effect",
}


class OpenverseError(RuntimeError):
    """Openverse 网络、响应或下载错误。"""


def prepare_search_query(query: str) -> tuple[str, bool]:
    """把中文剪辑关键词转换为更适合 Openverse 索引的英文查询。"""
    query = query.strip()
    if not re.search(r"[\u3400-\u9fff]", query):
        return query, False

    exact = _CN_QUERY_EXACT.get(query)
    if exact:
        return exact, True

    # 先用本地片段词表；按长度匹配，避免“背景音乐”又重复命中“音乐”。
    matched_ranges: list[tuple[int, int]] = []
    translated_parts: list[str] = []
    for chinese, english in sorted(_CN_QUERY_PARTS.items(), key=lambda pair: -len(pair[0])):
        for match in re.finditer(re.escape(chinese), query):
            span = match.span()
            if any(not (span[1] <= old[0] or span[0] >= old[1]) for old in matched_ranges):
                continue
            matched_ranges.append(span)
            if english not in translated_parts:
                translated_parts.append(english)
    if translated_parts:
        return " ".join(translated_parts), True

    # 生僻描述走项目已有的免费翻译器；失败时保留原词，不让搜索线程报错。
    try:
        from core.builtin_translator import translate_text
        translated = str(translate_text(query, "en") or "").strip()
        if translated and not re.search(r"[\u3400-\u9fff]", translated):
            return translated, True
    except Exception:
        pass
    return query, False


def _request(url: str, timeout: int = 25):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            detail = ""
        if exc.code == 429:
            raise OpenverseError("Openverse 请求过于频繁，请稍后再试") from exc
        raise OpenverseError(f"Openverse 返回 HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise OpenverseError(f"无法连接 Openverse：{exc.reason}") from exc
    except Exception as exc:
        raise OpenverseError(f"Openverse 请求失败：{exc}") from exc


def _duration_seconds(value) -> float:
    """Openverse 音频时长字段单位为毫秒。"""
    try:
        return max(0.0, float(value or 0) / 1000.0)
    except (TypeError, ValueError):
        return 0.0


def _normalize_result(raw: dict) -> dict:
    license_code = str(raw.get("license") or "").lower()
    audio_url = str(raw.get("url") or "")
    return {
        "id": str(raw.get("id") or ""),
        "title": str(raw.get("title") or "未命名音频"),
        "creator": str(raw.get("creator") or "未知作者"),
        "creator_url": str(raw.get("creator_url") or ""),
        "audio_url": audio_url,
        "landing_url": str(raw.get("foreign_landing_url") or raw.get("detail_url") or ""),
        "license": license_code,
        "license_label": LICENSE_LABELS.get(license_code, license_code.upper() or "许可证未知"),
        "license_url": str(raw.get("license_url") or ""),
        "duration": _duration_seconds(raw.get("duration")),
        "filetype": str(raw.get("filetype") or ""),
        "filesize": int(raw.get("filesize") or 0),
        "category": str(raw.get("category") or ""),
        "source": str(raw.get("source") or raw.get("provider") or "Openverse"),
        "thumbnail": str(raw.get("thumbnail") or ""),
        "attribution": str(raw.get("attribution") or ""),
    }


def search_audio(
    query: str,
    *,
    category: str = "",
    license_filter: str = "commercial",
    page: int = 1,
    page_size: int = 20,
    timeout: int = 25,
) -> tuple[list[dict], int]:
    """搜索 Openverse 音频，返回 ``(结果列表, 总数)``。"""
    query = query.strip()
    if not query:
        raise OpenverseError("请输入音乐或音效关键词")

    params = {
        "q": query,
        "page": max(1, int(page)),
        "page_size": min(50, max(1, int(page_size))),
        "mature": "false",
    }
    if category in {"music", "sound_effect"}:
        params["category"] = category
    licenses = LICENSE_FILTERS.get(license_filter, LICENSE_FILTERS["commercial"])
    if licenses:
        params["license"] = licenses

    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    with _request(url, timeout=timeout) as response:
        try:
            payload = json.loads(response.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OpenverseError("Openverse 返回了无法解析的数据") from exc

    results = []
    for raw in payload.get("results", []):
        item = _normalize_result(raw)
        if item["audio_url"]:
            results.append(item)
    return results, int(payload.get("result_count") or len(results))


def format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds or 0)))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _safe_filename(text: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .")
    text = re.sub(r"\s+", " ", text)
    if not text:
        text = "Openverse 音频"
    return text[:120]


def _guess_extension(item: dict, content_type: str = "") -> str:
    filetype = (item.get("filetype") or content_type or "").split(";", 1)[0].strip().lower()
    mime_map = {
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/ogg": ".ogg",
        "application/ogg": ".ogg",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/flac": ".flac",
        "audio/mp4": ".m4a",
        "audio/aac": ".aac",
        "audio/webm": ".webm",
    }
    if filetype in mime_map:
        return mime_map[filetype]
    guessed = mimetypes.guess_extension(filetype) if "/" in filetype else ""
    if guessed:
        return ".mp3" if guessed == ".mpga" else guessed
    path_ext = Path(urllib.parse.urlparse(item.get("audio_url", "")).path).suffix.lower()
    if path_ext in {".mp3", ".ogg", ".wav", ".flac", ".m4a", ".aac", ".webm"}:
        return path_ext
    return ".mp3"


def _available_path(directory: Path, stem: str, extension: str) -> Path:
    candidate = directory / f"{stem}{extension}"
    index = 2
    while candidate.exists() or Path(str(candidate) + ".license.json").exists():
        candidate = directory / f"{stem} ({index}){extension}"
        index += 1
    return candidate


def download_audio(
    item: dict,
    directory: str,
    progress: Callable[[int, int], None] | None = None,
    timeout: int = 60,
) -> str:
    """下载一个结果，并在旁边写入可追溯的许可证 JSON。"""
    audio_url = str(item.get("audio_url") or "")
    if not audio_url.startswith(("http://", "https://")):
        raise OpenverseError("该素材没有可下载的音频地址")

    target_dir = Path(directory).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(audio_url, headers={"User-Agent": USER_AGENT})
    try:
        response = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise OpenverseError(f"音频源站拒绝下载（HTTP {exc.code}）") from exc
    except urllib.error.URLError as exc:
        raise OpenverseError(f"无法连接音频源站：{exc.reason}") from exc

    with response:
        total = int(response.headers.get("Content-Length") or item.get("filesize") or 0)
        if total > MAX_DOWNLOAD_BYTES:
            raise OpenverseError("该音频超过 500 MB，已停止下载")
        extension = _guess_extension(item, response.headers.get("Content-Type", ""))
        path = _available_path(target_dir, _safe_filename(item.get("title", "")), extension)
        partial = Path(str(path) + ".part")
        downloaded = 0
        try:
            with partial.open("wb") as handle:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > MAX_DOWNLOAD_BYTES:
                        raise OpenverseError("该音频超过 500 MB，已停止下载")
                    handle.write(chunk)
                    if progress:
                        progress(downloaded, total)
            if downloaded == 0:
                raise OpenverseError("源站返回了空文件")
            os.replace(partial, path)
        except Exception:
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    attribution = item.get("attribution") or (
        f'“{item.get("title", "未命名音频")}” — {item.get("creator", "未知作者")}，'
        f'{item.get("license_label", item.get("license", ""))}'
    )
    metadata = {
        "provider": "Openverse",
        "openverse_id": item.get("id", ""),
        "title": item.get("title", ""),
        "creator": item.get("creator", ""),
        "creator_url": item.get("creator_url", ""),
        "source": item.get("source", ""),
        "source_url": item.get("landing_url", ""),
        "media_url": audio_url,
        "license": item.get("license", ""),
        "license_url": item.get("license_url", ""),
        "attribution": attribution,
    }
    sidecar = Path(str(path) + ".license.json")
    sidecar.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)
