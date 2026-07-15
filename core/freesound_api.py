"""
freesound_api.py — Freesound API v2 客户端

免费、无需版权风险的音效搜索/下载。需要用户自己的 API key
（在 https://freesound.org/apiv2/apply 申请，存于 QSettings，不写死在代码）。

国内访问 freesound.org 可能较慢/需代理：自动读取 HTTPS_PROXY / HTTP_PROXY
环境变量走代理。
"""
from __future__ import annotations

import os
import re
import json
import logging
import ssl
import urllib.request
import urllib.parse
import urllib.error
from typing import Optional

logger = logging.getLogger(__name__)

API_BASE = "https://freesound.org/apiv2"
SEARCH_URL = API_BASE + "/search/text/"

# QSettings 组/键（与 download_panel 一致）
_SETTINGS_ORG = "CreativeEnginePro"
_SETTINGS_APP = "DownloadPanel"
_KEY_SETTING = "freesound_api_key"


def get_api_key() -> str:
    """读取已保存的 Freesound API key（空字符串表示未配置）"""
    try:
        from PyQt6.QtCore import QSettings
        s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        return str(s.value(_KEY_SETTING, "") or "")
    except Exception:
        return ""


def set_api_key(key: str):
    """保存 Freesound API key 到 QSettings"""
    try:
        from PyQt6.QtCore import QSettings
        QSettings(_SETTINGS_ORG, _SETTINGS_APP).setValue(_KEY_SETTING, key.strip())
    except Exception:
        logger.warning("无法保存 Freesound API key 到 QSettings")


class FreesoundError(Exception):
    """Freesound 客户端错误（网络 / 鉴权 / 解析）"""


def _build_opener():
    """构造支持代理的 urllib opener（读 HTTPS_PROXY / HTTP_PROXY 环境变量）"""
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"https": proxy, "http": proxy}))
    return urllib.request.build_opener(*handlers)


def _http_get_json(url: str, params: dict, timeout: int = 20):
    """发起 GET 请求并返回 (status_code, dict)。"""
    qs = urllib.parse.urlencode(params)
    full = url + ("?" + qs if qs else "")
    req = urllib.request.Request(full, headers={"User-Agent": "CreativeEnginePro/1.0"})
    opener = _build_opener()
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.getcode(), json.loads(raw)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise FreesoundError(f"Freesound 返回 HTTP {e.code}：{body[:200]}")
    except urllib.error.URLError as e:
        raise FreesoundError(f"网络请求失败：{e.reason}")
    except Exception as e:
        raise FreesoundError(f"请求异常：{e}")


def search(query: str, page: int = 1, page_size: int = 20,
           timeout: int = 25, sort: str = "") -> dict:
    """搜索/浏览音效，返回 Freesound 原始响应：{count, next, previous, results:[...]}

    - query 为空时自动用 "sound" 兜底，保证能列出内容（浏览模式）。
    - sort 可选：downloads_desc(热门) / rating_desc / created_desc 等。
    """
    key = get_api_key()
    if not key:
        raise FreesoundError("未配置 Freesound API key（请在音效库页填写并保存）")
    params = {
        "query": query.strip() or "sound",
        "page": page,
        "page_size": page_size,
        "token": key,
        "fields": "id,name,tags,duration,license,username,previews,download,type,filesize",
    }
    if sort:
        params["sort"] = sort
    code, data = _http_get_json(SEARCH_URL, params, timeout=timeout)
    if code != 200:
        raise FreesoundError(f"搜索失败（HTTP {code}）")
    return data


def preview_url(sound: dict) -> Optional[str]:
    """取音效预览音频直链（hq 优先）"""
    previews = sound.get("previews") or {}
    return previews.get("preview-hq-mp3") or previews.get("preview-lq-mp3") or None


def download_audio(sound: dict, dest_dir: str, timeout: int = 90) -> str:
    """下载音效预览音频到 dest_dir，返回本地文件路径。

    说明：Freesound 完整文件下载需要 OAuth 流程，预览直链（mp3）无需鉴权
    即可直接下载，对剪辑音效足够用，且避免了复杂的 OAuth 接入。
    """
    url = preview_url(sound)
    if not url:
        raise FreesoundError("该音效没有可用的预览音频")
    os.makedirs(dest_dir, exist_ok=True)
    raw_name = sound.get("name") or f"sound_{sound.get('id')}"
    safe = re.sub(r'[\\/*?:"<>|]', "_", raw_name)
    sid = sound.get("id", "0")
    path = os.path.join(dest_dir, f"sfx_{sid}_{safe}.mp3")

    req = urllib.request.Request(url, headers={"User-Agent": "CreativeEnginePro/1.0"})
    opener = _build_opener()
    try:
        with opener.open(req, timeout=timeout) as resp:
            with open(path, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
    except urllib.error.URLError as e:
        raise FreesoundError(f"下载失败：{e.reason}")
    except Exception as e:
        raise FreesoundError(f"下载异常：{e}")
    return path


def license_type(license_url: str) -> str:
    """从 license URL 推断授权类型，返回 'cc0' / 'by' / 'nc' / 'other'"""
    u = (license_url or "").lower()
    if "zero/1.0" in u or "publicdomain" in u:
        return "cc0"
    if "nc" in u:
        return "nc"
    if "by" in u:
        return "by"
    return "other"
