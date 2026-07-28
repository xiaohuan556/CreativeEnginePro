#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
热点雷达 Web 版 — Flask 后端 (v2)
────────────────────────────────────────────────────────────────
特性（对齐桌面版 CreativeEnginePro 的热点雷达 Tab5）：
  · 8 大数据源，绝大多数「无需任何 API Key」即可运行：
      🔥 热门Meme   → Know Your Meme / Google News（兜底）
      🌟 娱乐新闻   → NewsAPI(可选) + RSS(Variety/ESPN/BBC…)
      🔥 热映资讯   → 豆瓣实时榜单 / TMDB(可选)
      🏈 体育热点   → RSS(ESPN/BBC Sport/Yahoo)
      📹 视频热点   → YouTube(可选) + B站热门 + TikTok
      🔍 搜索趋势   → Google Trends RSS
      𝕏 X热搜      → GetDayTrends 真实趋势（美区+全球，免 Key）
      🎵 TikTok    → tikwm 跨区聚合 hashtag（免 Key）
  · 并发抓取（ThreadPoolExecutor, max_workers=8），最坏耗时≈单源最慢
  · 复合热度评分 + Top3 金银铜 + 高热标记
  · 缓存优先秒开 + 后台静默刷新 + 定时自动刷新
  · 数据源配置（开关 / 条数 / 排除关键词 / 刷新间隔 / 最低分）
  · 免费谷歌翻译（单条 / 批量），无需 API Key

运行：pip install flask && python app.py  →  http://localhost:5000
"""
import os
import json
import math
import time
import ssl
import re
import html
import hashlib
import threading
from pathlib import Path
from datetime import date
from urllib.request import urlopen, Request
from urllib.parse import urlencode, quote
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# ── 跨域（允许 GitHub Pages / 任意前端调用）──
@app.after_request
def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp

# ── 加载 .env（可选，仅用于 YouTube / TMDB / NewsAPI 等增值源）──
PROJECT = Path(__file__).parent
ENV_FILE = PROJECT / ".env"
if ENV_FILE.exists():
    with open(ENV_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip("\"'")
            if k and k not in os.environ:
                os.environ[k] = v

YOUTUBE_KEY = os.getenv("YOUTUBE_API_KEY", "")
TMDB_KEY = os.getenv("TMDB_API_KEY", "")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")


def _hs_key_valid(v):
    """判断 API Key 是否可用 —— 过滤空值与占位符(your-xxx-key)"""
    if not v or not v.strip():
        return False
    v = v.strip().strip("\"'")
    if v.lower().startswith("your-") or "your-key" in v.lower():
        return False
    return True


# ═══════════════ 分类 & 颜色 ═══════════════
CAT_COLORS = {
    "🔥 热门Meme": "#E0646E",
    "🎵 TikTok": "#25F4EE",
    "🌟 娱乐新闻": "#CB9842",
    "🔥 热映资讯": "#FF6B8A",
    "🏈 体育热点": "#44A87A",
    "📹 视频热点": "#D0659E",
    "🔍 搜索趋势": "#8B7CF6",
    "𝕏 X热搜": "#1DA1F2",
}
CAT_KEYS = list(CAT_COLORS.keys())

SOURCE_LABELS = {
    "meme": "🔥 热门Meme",
    "entertainment": "🌟 娱乐新闻",
    "movie": "🔥 热映资讯",
    "sports": "🏈 体育热点",
    "video": "📹 视频热点",
    "search": "🔍 搜索趋势",
    "x": "𝕏 X热搜",
    "tiktok": "🎵 TikTok",
}

DEFAULT_CONFIG = {
    "sources": {
        "meme":          {"enabled": True, "limit": 20},
        "entertainment": {"enabled": True, "limit": 20},
        "movie":         {"enabled": True, "limit": 80},
        "sports":        {"enabled": True, "limit": 20},
        "video":         {"enabled": True, "limit": 20},
        "search":        {"enabled": True, "limit": 20},
        "x":             {"enabled": True, "limit": 20},
        "tiktok":        {"enabled": True, "limit": 20},
    },
    "exclude_keywords": [],
    "refresh_interval_min": 15,
    "min_score": 0,
}


# ═══════════════ 配置 / 缓存 ═══════════════
CONFIG_FILE = PROJECT / "hotspot_config.json"
CACHE_FILE = PROJECT / "hotspot_cache.json"

_cfg_lock = threading.Lock()
_cache_lock = threading.Lock()
_cache = {"updated_at": 0, "data": {}, "summaries": {}}   # 内存缓存，供 /api/data 秒回
_refreshing = False


def _hs_load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # 深拷贝默认
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                user = json.load(f)
            if isinstance(user.get("sources"), dict):
                for k, v in user["sources"].items():
                    if k in cfg["sources"]:
                        cfg["sources"][k].update(v)
                    else:
                        cfg["sources"][k] = v
            for key in ("exclude_keywords", "refresh_interval_min", "min_score"):
                if key in user:
                    cfg[key] = user[key]
        except Exception as e:
            print(f"[HotspotConfig] load error: {e}")
    return cfg


def _hs_save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[HotspotConfig] save error: {e}")


def _hs_load_cache():
    global _cache
    with _cache_lock:
        if CACHE_FILE.exists():
            try:
                with open(CACHE_FILE, encoding="utf-8") as f:
                    blob = json.load(f)
                _cache = {"updated_at": blob.get("updated_at", 0),
                          "data": blob.get("data", {}),
                          "summaries": blob.get("summaries", {})}
            except Exception as e:
                print(f"[HotspotCache] load error: {e}")


def _hs_save_cache(data, ts, summaries=None):
    global _cache
    with _cache_lock:
        _cache = {"updated_at": ts, "data": data, "summaries": summaries or {}}
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"updated_at": ts, "data": data,
                           "summaries": summaries or {}}, f, ensure_ascii=False)
        except Exception as e:
            print(f"[HotspotCache] save error: {e}")


# ═══════════════ 评分模型 ═══════════════
def _hs_enrich_scores(items):
    """复合热度评分（对齐桌面版）。

    绝大多数源不返回原始数值 → 采用『排名感知』：
      - 有真实 score（Google Trends 流量 / 播放量 / 评论数）→ log10 热度
      - 无真实 score → 用列表内排名倒推热度（抓回来的就是已排序的热榜）
      - 互动率：评论数 log10 归一
      - 综合分 = 0.7×热度(归一) + 0.3×互动(归一)，0–100
    按分降序，前 ~20%（至少 3 条）标记 hot。
    """
    if not items:
        return items
    n = len(items)
    heats, engs = [], []
    for i, it in enumerate(items):
        s = it.get("score", 0) or 0
        c = it.get("comments", 0) or 0
        h = math.log10(s + 1) if s > 0 else math.log10(n - i + 1)
        it["_heat"] = h
        it["_eng"] = math.log10(c + 1)
        heats.append(h)
        engs.append(it["_eng"])
    hmin, hmax = min(heats), max(heats)
    emin, emax = min(engs), max(engs)
    for it in items:
        hn = (it["_heat"] - hmin) / (hmax - hmin) if hmax > hmin else 1.0
        en = (it["_eng"] - emin) / (emax - emin) if emax > emin else 0.0
        it["composite"] = round(0.7 * hn * 100 + 0.3 * en * 100, 1)
    items.sort(key=lambda x: x.get("composite", 0), reverse=True)
    hot_n = max(3, n // 5)
    for i, it in enumerate(items):
        it["hot"] = i < hot_n
    return items


# ═══════════════ 数据获取（从桌面版迁移，免 Key 优先） ═══════════════
def _hs_fetch_reddit(subreddits=None, limit=15):
    if subreddits is None:
        subreddits = {
            "memes": "memes+dankmemes+me_irl+funny",
            "entertainment": "entertainment+television+movies",
            "sports": "sports+nba+soccer+nfl",
            "worldnews": "worldnews+news",
        }
    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    }
    for category, subs in subreddits.items():
        try:
            url = f"https://www.reddit.com/r/{subs}/hot.json?limit={limit}"
            req = Request(url, headers=headers)
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            for post in data.get("data", {}).get("children", []):
                p = post["data"]
                if p.get("stickied"):
                    continue
                selftext = p.get("selftext", "")[:200]
                results.append(dict(
                    id=f"reddit_{p['id']}", title=p["title"],
                    source=f"r/{p['subreddit']}", category=category,
                    url=f"https://reddit.com{p['permalink']}",
                    score=p.get("score", 0), comments=p.get("num_comments", 0),
                    desc=(selftext or p["title"])[:200],
                ))
        except Exception as e:
            print(f"[Reddit] {category}: {e}")
    return results


def _hs_fetch_meme_fallback(limit=20):
    """Know Your Meme 主源 → Google News RSS 补量（确保凑够 limit 条）"""
    results = []

    def _clean_meme_title(raw):
        t = raw.replace("★", " ").replace("  ", " ")
        t = re.split(r"\s[•·]\s", t)[0]
        t = re.sub(r"^Meme\s+", "", t, flags=re.I).strip()
        return t.strip()

    try:
        req = Request("https://knowyourmeme.com/memes", headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urlopen(req, timeout=10) as resp:
            htmltext = resp.read().decode("utf-8", errors="replace")
        pattern = re.compile(r'<a[^>]+href="(/memes/[^"]+)"[^>]*>(.*?)</a>', re.S)
        seen = set()
        for match in pattern.finditer(htmltext):
            href, block = match.group(1), match.group(2)
            if "page/" in href:
                continue
            raw = re.sub(r"<[^>]+>", " ", block)
            raw = re.sub(r"\s+", " ", raw).strip()
            title = _clean_meme_title(raw)
            if not title or title in seen or len(title) < 3:
                continue
            if title.lower() in ("meme", "subculture", "event", "entry", "photo", "video", "image"):
                continue
            seen.add(title)
            img_match = re.search(r'<img[^>]+src="([^"]+)"', block)
            img = img_match.group(1) if img_match else ""
            img_url = img if img.startswith("http") else f"https:{img}" if img.startswith("//") else img
            results.append(dict(
                id=f"kym_{hashlib.md5(title.encode()).hexdigest()[:12]}",
                title=title, source="Know Your Meme", category="memes",
                url=f"https://knowyourmeme.com{href}", score=0, comments=0,
                desc=title[:200], image=img_url,
            ))
            if len(results) >= limit:
                break
    except Exception as e:
        print(f"[MemeFallback KYM]: {e}")

    if len(results) < limit:
        try:
            rss_url = "https://news.google.com/rss/search?q=viral+meme+funny+trending&hl=en-US&gl=US&ceid=US:en"
            req = Request(rss_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            with urlopen(req, timeout=10) as resp:
                content = resp.read().decode("utf-8", errors="replace")
            root = ET.fromstring(content)
            have = {r["title"] for r in results}
            for item in root.iter("item"):
                if len(results) >= limit:
                    break
                title = item.find("title")
                link = item.find("link")
                if title is None or not title.text:
                    continue
                title_text = title.text.strip()
                if title_text in have:
                    continue
                if not any(k in title_text.lower() for k in ["meme", "viral", "tiktok", "funny", "trend", "internet"]):
                    continue
                have.add(title_text)
                gimg = ""
                d_el = item.find("description")
                if d_el is not None and d_el.text:
                    gm = re.search(r'<img[^>]+src="([^"]+)"', d_el.text)
                    if gm:
                        gimg = gm.group(1)
                if gimg and not gimg.startswith("http"):
                    gimg = "https:" + gimg if gimg.startswith("//") else ""
                results.append(dict(
                    id=f"meme_{hashlib.md5(title_text.encode()).hexdigest()[:12]}",
                    title=title_text, source="Google News", category="memes",
                    url=link.text.strip() if link is not None else "",
                    score=0, comments=0, desc=title_text[:200], image=gimg,
                ))
        except Exception as e:
            print(f"[MemeFallback GoogleNews]: {e}")

    if results:
        return results
    return [dict(
        id="meme_notice", title="⚠️ Meme 数据源暂时不可用",
        source="系统提示", category="memes",
        url="", score=0, comments=0,
        desc="Know Your Meme 与 Google News 均无法访问，请稍后再试。",
    )]


def _hs_fetch_google_trends(limit=20):
    results = []
    ns = {"ht": "https://trends.google.com/trending/rss"}
    try:
        req = Request("https://trends.google.com/trending/rss?geo=US", headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8", errors="replace")
        root = ET.fromstring(content)
        for i, item in enumerate(root.findall(".//item")):
            if i >= limit:
                break
            title_el = item.find("title")
            traffic_el = item.find("ht:approx_traffic", ns)
            title = title_el.text.strip() if title_el is not None and title_el.text else ""
            traffic = traffic_el.text.strip() if traffic_el is not None and traffic_el.text else ""
            news_el = item.find("ht:news_item", ns)
            news_title = ""
            news_url = ""
            if news_el is not None:
                nt = news_el.find("ht:news_item_title", ns)
                nu = news_el.find("ht:news_item_url", ns)
                news_title = nt.text.strip() if nt is not None and nt.text else ""
                news_url = nu.text.strip() if nu is not None and nu.text else ""
            traffic_val = 0
            m = re.search(r"([\d.]+)\s*([KMB]?)\+?", traffic)
            if m:
                num = float(m.group(1))
                unit = m.group(2)
                mul = {"K": 1e3, "M": 1e6, "B": 1e9}.get(unit, 1)
                traffic_val = int(num * mul)
            search_url = f"https://trends.google.com/trends/explore?q={quote(title)}"
            results.append(dict(
                id=f"trend_{hashlib.md5(title.encode()).hexdigest()[:12]}",
                title=title, source="Google Trends", category="trend",
                url=news_url or search_url, score=traffic_val, comments=0,
                desc=news_title or f"搜索量: {traffic}",
            ))
        return results
    except Exception as e:
        print(f"[GoogleTrends]: {e}")
        return []


def _hs_fetch_youtube_official(max_results=20):
    """YouTube 官方 mostPopular（需 YOUTUBE_API_KEY，按真实 views 排序）。"""
    try:
        params = dict(part="snippet,statistics", chart="mostPopular", regionCode="US",
                      maxResults=max_results, key=YOUTUBE_KEY)
        url = f"https://www.googleapis.com/youtube/v3/videos?{urlencode(params)}"
        with urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        results = []
        for item in data.get("items", []):
            s = item.get("snippet", {})
            st = item.get("statistics", {})
            results.append(dict(
                id=f"yt_{item['id']}", title=s.get("title", ""),
                source=f"{s.get('channelTitle', 'YouTube')} · YouTube", category="video",
                url=f"https://youtube.com/watch?v={item['id']}",
                score=int(st.get("viewCount", 0) or 0),
                comments=int(st.get("commentCount", 0) or 0),
                desc=s.get("description", "")[:200],
                image=s.get("thumbnails", {}).get("medium", {}).get("url", ""),
            ))
        return results
    except Exception as e:
        print(f"[YouTube API]: {e}")
        return []


def _hs_fetch_bilibili(limit=20):
    """B站热门榜（真实当下、按播放量，无需 Key）。"""
    try:
        url = f"https://api.bilibili.com/x/web-interface/popular?ps={limit}&pn=1"
        req = Request(url, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/120 Safari/537.36"),
            "Referer": "https://www.bilibili.com/",
            "Accept": "application/json",
        })
        with urlopen(req, timeout=12) as resp:
            j = json.loads(resp.read())
        if j.get("code") != 0:
            return []
        out = []
        for it in (j.get("data", {}) or {}).get("list", [])[:limit]:
            stat = it.get("stat", {}) or {}
            bvid = it.get("bvid", "")
            out.append(dict(
                id=f"bili_{bvid}",
                title=it.get("title", ""),
                source=f"{it.get('owner', {}).get('name', '')} · B站",
                category="video",
                url=f"https://www.bilibili.com/video/{bvid}",
                score=int(stat.get("view", 0) or 0),
                comments=int(stat.get("reply", 0) or 0),
                desc=(it.get("desc", "") or "")[:200],
                image=(it.get("pic", "") or "").replace("http://", "https://"),
            ))
        return out
    except Exception as e:
        print(f"[Bilibili]: {e}")
        return []


def _hs_fetch_tiktok(limit=20):
    """TikTok 热搜：主路径用 tikwm 真实热门视频流，跨区聚合 hashtag 按播放量加权排名（免 Key）。"""
    GENERIC = {"fyp", "foryou", "foryoupage", "viral", "trending", "fypシ",
               "fypage", "trend", "viralvideo", "foryoupageシ", "シ"}
    regions = ["us", "gb", "id", "ph", "br", "de", "kr", "jp"]

    def _fetch_region(region):
        local = {}
        try:
            url = f"https://tikwm.com/api/feed/list?region={region}&count=30"
            req = Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            with urlopen(req, timeout=9) as resp:
                j = json.loads(resp.read())
            for v in j.get("data", []):
                pc = int(v.get("play_count") or 0)
                cover = v.get("cover") or v.get("origin_cover") or v.get("dynamic_cover") or ""
                avatar = (v.get("author") or {}).get("avatar") or ""
                for tag in re.findall(r"#([a-zA-Z0-9_]+)", (v.get("title") or "")):
                    tag = tag.lower()
                    if tag in GENERIC:
                        continue
                    a = local.setdefault(tag, {"play": 0, "n": 0, "covers": []})
                    a["play"] += pc
                    a["n"] += 1
                    # 收集候选封面（去重，视频封面降权高于作者头像）
                    has = [c[1] for c in a["covers"]]
                    if cover and cover not in has:
                        a["covers"].append((pc * 100, cover))
                    if avatar and avatar not in has and avatar != cover:
                        a["covers"].append((pc, avatar))
        except Exception as e:
            print(f"[TikTok {region}]: {e}")
        return local

    agg = {}
    with ThreadPoolExecutor(max_workers=len(regions)) as ex:
        for local in ex.map(_fetch_region, regions):
            for tag, a in local.items():
                g = agg.setdefault(tag, {"play": 0, "n": 0, "covers": []})
                g["play"] += a["play"]
                g["n"] += a["n"]
                # 合并候选池（跨 region 去重）
                existing = {c[1] for c in g["covers"]}
                for w, c in a["covers"]:
                    if c not in existing:
                        g["covers"].append((w, c))
                        existing.add(c)
    # 主排序：独立视频数 n，避免同视频多 hashtag 数值并列；次：总播放量
    ranked = sorted(agg.items(), key=lambda kv: (kv[1]["n"], kv[1]["play"]), reverse=True)[:limit]
    results = []
    seen = set()
    for tag, a in ranked:
        # 按权重(视频封面 100x > 作者头像 1x)取第一个未被占用的
        cover = ""
        sorted_cands = sorted(a["covers"], key=lambda x: -x[0])
        for _, cv in sorted_cands:
            if cv and cv not in seen:
                cover = cv
                break
        if cover:
            seen.add(cover)
        results.append(dict(
            id=f"tk_{hashlib.md5(tag.encode()).hexdigest()[:12]}",
            title=f"#{tag}", source="TikTok", category="tiktok",
            url=f"https://www.tiktok.com/tag/{quote(tag)}",
            score=a["play"], comments=a["n"],
            desc=f"{a['play'] // 1000:,} 播放 · {a['n']} 条热门视频",
            image=cover,
        ))
    return results


def _hs_fetch_video_hotspots(max_results=20):
    """视频热点（多源融合）：YouTube(可选) + B站(免Key) + TikTok(免Key)"""
    chunks = []
    if _hs_key_valid(YOUTUBE_KEY):
        yt = _hs_fetch_youtube_official(max_results)
        if yt:
            chunks.append(yt)
    bili = _hs_fetch_bilibili(limit=max_results)
    if bili:
        chunks.append(bili)
    tk = _hs_fetch_tiktok(limit=max_results)
    if tk:
        chunks.append(tk)

    if not chunks:
        return []
    quota = max(4, max_results // len(chunks))
    merged, seen, leftovers = [], set(), []
    per_used = [0] * len(chunks)
    for idx, items in enumerate(chunks):
        for it in items:
            iid = it.get("id")
            if iid in seen:
                continue
            seen.add(iid)
            if per_used[idx] < quota:
                merged.append(it)
                per_used[idx] += 1
            else:
                leftovers.append(it)
    if len(merged) < max_results and leftovers:
        leftovers.sort(key=lambda x: x.get("score", 0) or 0, reverse=True)
        merged.extend(leftovers[:max_results - len(merged)])
    merged.sort(key=lambda x: x.get("score", 0) or 0, reverse=True)
    return merged[:max_results]


def _hs_fetch_douban_subjects(tag, stype="movie", limit=10, sort="recommend"):
    """豆瓣实时榜单（无需 Key）"""
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120 Safari/537.36")
    url = (f"https://movie.douban.com/j/search_subjects?type={stype}"
           f"&tag={quote(tag)}&sort={sort}&page_limit={limit}&page_start=0")
    req = Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://movie.douban.com/",
        "Accept": "application/json, text/plain, */*",
    })
    with urlopen(req, timeout=12, context=ssl.create_default_context()) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))
    return j.get("subjects", [])


def _tmdb_fetch(path, params=None):
    """通用 TMDB 调用，params 是 dict。失败返回 None。"""
    qs = "&".join(f"{k}={v}" for k, v in (params or {}).items())
    url = f"https://api.themoviedb.org/3{path}?api_key={TMDB_KEY}&language=zh-CN"
    if qs:
        url += "&" + qs
    try:
        with urlopen(url, timeout=10, context=ssl.create_default_context()) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[TMDB {path}]: {e}")
        return None


def _tmdb_to_item(it, source_label, sub_group, rank=None):
    rid = it.get("id")
    title = it.get("title") or it.get("name") or it.get("original_title") or it.get("original_name") or ""
    rel = (it.get("release_date") or it.get("first_air_date") or "")[:10]
    desc_parts = []
    if rank:
        desc_parts.append(f"#{rank}")
    desc_parts.append(source_label)
    if rel:
        desc_parts.append(rel)
    rating = it.get("vote_average")
    if rating:
        desc_parts.append(f"⭐{rating:.1f}")
    media_type = it.get("media_type") or ("tv" if it.get("first_air_date") else "movie")
    url = f"https://www.themoviedb.org/{media_type}/{rid}"
    popularity = float(it.get("popularity") or 0)
    img_path = it.get("poster_path") or it.get("profile_path") or ""
    return dict(
        id=f"tm_{rid}", title=title, source=source_label, category="movie",
        sub_group=sub_group,
        url=url, desc=" · ".join(desc_parts), score=popularity,
        image=f"https://image.tmdb.org/t/p/w200{img_path}" if img_path else "",
    )


def _hs_fetch_now_showing(limit=80):
    """热映资讯 4 子版块：正在热映电影 / 今日电视剧 / 即将上映 / 豆瓣动漫。"""
    results = []
    seen = set()
    limit_per_group = max(limit // 4, 12)

    def add(item):
        if not item.get("title"):
            return
        rid = item["id"]
        if rid in seen:
            return
        seen.add(rid)
        results.append(item)

    # ── 1) + 3) 电影：统一拉取后按 release_date 精确拆分，US 日期优先 ──
    if _hs_key_valid(TMDB_KEY):
        today_str = date.today().isoformat()
        movie_map = {}

        for path, pages in [("/movie/now_playing", (1, 2)), ("/movie/upcoming", (1, 2, 3))]:
            for region in ("US", "CN"):
                for page in range(pages[0], pages[-1] + 1):
                    data = _tmdb_fetch(path, {"region": region, "page": page})
                    if not data:
                        continue
                    for it in data.get("results", []):
                        rid = it.get("id")
                        rel = (it.get("release_date") or "").strip()
                        if not rel:
                            continue
                        entry = movie_map.get(rid)
                        if entry is None:
                            movie_map[rid] = {"item": it, "release_date": rel,
                                               "cn_date": rel if region == "CN" else "",
                                               "us_date": rel if region == "US" else ""}
                        else:
                            if region == "CN" and not entry["cn_date"]:
                                entry["cn_date"] = rel
                            if region == "US" and not entry["us_date"]:
                                entry["us_date"] = rel
                            if region == "US" and rel:
                                entry["release_date"] = rel

        now_playing, upcoming = [], []
        for rid, entry in movie_map.items():
            display_date = entry["us_date"] or entry["release_date"]
            m = dict(entry["item"])
            m["release_date"] = display_date
            if display_date <= today_str:
                now_playing.append(m)
            else:
                upcoming.append(m)

        now_playing.sort(key=lambda x: float(x.get("popularity") or 0), reverse=True)
        for rank, it in enumerate(now_playing, 1):
            if rank > limit_per_group:
                break
            item = _tmdb_to_item(it, "🎬 正在热映", "正在热映的电影", rank=rank)
            if item.get("image"):
                add(item)

        upcoming.sort(key=lambda x: float(x.get("popularity") or 0), reverse=True)
        for rank, it in enumerate(upcoming, 1):
            if rank > limit_per_group:
                break
            item = _tmdb_to_item(it, "🎬 即将上映", "即将上映的热门电影", rank=rank)
            if item.get("image"):
                add(item)

    # ── 2) 今日播放的电视剧 (TMDB tv/airing_today, popularity desc) ──
    if _hs_key_valid(TMDB_KEY):
        data = _tmdb_fetch("/tv/airing_today")
        if data:
            at_items = sorted(
                data.get("results", []),
                key=lambda x: float(x.get("popularity") or 0), reverse=True
            )
            for rank, it in enumerate(at_items, 1):
                if rank > limit_per_group:
                    break
                item = _tmdb_to_item(it, "📺 今日播出", "今日播放的电视剧", rank=rank)
                if item.get("image"):
                    add(item)

    # ── 4) 正在热映的动漫 (豆瓣 日本动画，实时) ──
            if item.get("image"):
                add(item)

    # ── 4) 正在热映的动漫 (豆瓣 日本动画，实时) ──
    try:
        added_anime = 0
        for it in _hs_fetch_douban_subjects("日本动画", "tv", limit_per_group, "time"):
            if added_anime >= limit_per_group:
                break
            sid = f"db_{it.get('id') or it.get('url')}"
            if sid in seen:
                continue
            seen.add(sid)
            rate = it.get("rate") or ""
            try:
                score = int(float(rate) * 100)
            except Exception:
                score = 0
            results.append(dict(
                id=sid, title=it.get("title", ""),
                source="📺 连载日番", category="movie",
                sub_group="正在热映的动漫",
                url=it.get("url", "https://movie.douban.com/"),
                desc=f"豆瓣评分 {rate}" if rate else "正在连载",
                score=score, image=it.get("cover", ""),
            ))
            added_anime += 1
    except Exception as e:
        print(f"[Douban anime]: {e}")

    return results[:limit] if len(results) > limit else results
def _hs_fetch_newsapi(max_results=10):
    if not NEWSAPI_KEY:
        return []
    results = []
    queries = {
        "entertainment": "entertainment OR celebrity OR Hollywood",
        "sports": "sports OR NFL OR NBA OR soccer",
    }
    for cat, q in queries.items():
        try:
            url = (f"https://newsapi.org/v2/everything?"
                   f"q={quote(q)}&language=en&sortBy=popularity&pageSize={max_results}&apiKey={NEWSAPI_KEY}")
            with urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
            for a in data.get("articles", []):
                results.append(dict(
                    id=f"news_{hashlib.md5(a['url'].encode()).hexdigest()[:12]}",
                    title=a.get("title", ""),
                    source=a.get("source", {}).get("name", "News"), category=cat,
                    url=a.get("url", ""), desc=(a.get("description") or "")[:200],
                ))
        except Exception as e:
            print(f"[NewsAPI] {cat}: {e}")
    return results


RSS_FEEDS = {
    "entertainment": [
        ("Variety", "https://variety.com/feed/"),
        ("Deadline", "https://deadline.com/feed/"),
        ("Hollywood Reporter", "https://feeds.feedburner.com/thr/news"),
        ("Billboard", "https://www.billboard.com/feed/"),
    ],
    "sports": [
        ("ESPN", "https://www.espn.com/espn/rss/news"),
        ("BBC Sport", "https://feeds.bbci.co.uk/sport/rss.xml"),
        ("Yahoo Sports", "https://sports.yahoo.com/rss/"),
    ],
    "world": [("BBC News", "https://feeds.bbci.co.uk/news/world/rss.xml")],
}


def _hs_fetch_rss(categories=None):
    results = []
    for category, feeds in RSS_FEEDS.items():
        if categories and category not in categories:
            continue
        for name, url in feeds:
            try:
                req = Request(url, headers={"User-Agent": "HotRadar/5.0"})
                with urlopen(req, timeout=10) as resp:
                    content = resp.read().decode("utf-8", errors="replace")
                root = ET.fromstring(content)
                for item in root.iter("item"):
                    title = item.find("title")
                    link = item.find("link")
                    desc = item.find("description")
                    if title is None or not title.text:
                        continue
                    desc_text = ""
                    img_url = ""
                    if desc is not None and desc.text:
                        dt = desc.text
                        desc_text = re.sub(r'<[^>]+>', '', dt)[:200]
                        m = re.search(r'<img[^>]+src="([^"]+)"', dt)
                        if m:
                            img_url = m.group(1)
                    if not img_url:
                        mc = item.find("{http://search.yahoo.com/mrss/}content")
                        if mc is not None:
                            img_url = mc.get("url", "")
                    if not img_url:
                        enc = item.find("enclosure")
                        if enc is not None and (enc.get("type") or "").startswith("image"):
                            img_url = enc.get("url", "")
                    if img_url and not img_url.startswith("http"):
                        img_url = "https:" + img_url if img_url.startswith("//") else ""
                    results.append(dict(
                        id=f"rss_{hashlib.md5((title.text or '').encode()).hexdigest()[:12]}",
                        title=title.text.strip(), source=name, category=category,
                        url=link.text.strip() if link is not None else "",
                        desc=desc_text, image=img_url,
                    ))
            except Exception as e:
                print(f"[RSS] {name}: {e}")
    return results


def _hs_fetch_getdaytrends(slug="united-states", limit=50):
    """GetDayTrends —— 真实 X/Twitter 热搜（美区/全球），无需任何 Key。"""
    url = f"https://getdaytrends.com/{slug + '/' if slug else ''}"
    try:
        req = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urlopen(req, timeout=12) as resp:
            h = resp.read().decode("utf-8", errors="replace")
        m = re.search(r'<table[^>]*ranking trends[^>]*>(.*?)</table>', h, re.S)
        if not m:
            return []
        out = []
        for r in re.findall(r'<tr[^>]*>(.*?)</tr>', m.group(1), re.S):
            a = re.search(r'<a[^>]*>([^<]+)</a>', r)
            if not a:
                continue
            name = html.unescape(a.group(1)).strip()
            if not name:
                continue
            pos = re.search(r'class="pos">(\d+)<', r)
            rank = int(pos.group(1)) if pos else len(out) + 1
            out.append(dict(
                id=f"xt_{hashlib.md5(name.encode()).hexdigest()[:12]}",
                title=name, source="X (Twitter)", category="x_trend",
                url=f"https://x.com/search?q={quote(name)}",
                score=0, comments=0,
                desc=f"X 实时热搜第 {rank} 位",
                _rank=rank,
            ))
        return out[:limit]
    except Exception as e:
        print(f"[GetDayTrends {slug}]: {e}")
        return []


def _hs_fetch_x_trends(limit=20):
    """X 热搜：GetDayTrends 真实趋势（美区 + 全球去重合并，免 Key）。"""
    merged = {}
    for slug in ("united-states", ""):
        for it in _hs_fetch_getdaytrends(slug, 50):
            merged.setdefault(it["title"], it)
    items = list(merged.values())
    items.sort(key=lambda x: x.get("_rank", 999))
    return items[:limit]


def _hs_load_meme_cache():
    cache_file = PROJECT / "meme_cache.json"
    if cache_file.exists():
        try:
            with open(cache_file, encoding="utf-8") as f:
                cache = json.load(f)
            return cache.get("memes", [])
        except Exception as e:
            print(f"[MemeCache] load error: {e}")
    return []


def _hs_fetch_memes(limit=15):
    items = _hs_fetch_meme_fallback(limit=limit)
    if items:
        cache_file = PROJECT / "meme_cache.json"
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump({"memes": items, "source": "Know Your Meme"}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return items
    reddit = _hs_fetch_reddit({"memes": "memes+dankmemes+funny"}, limit=limit)
    if reddit:
        return reddit
    return _hs_load_meme_cache()


# ═══════════════ 免费谷歌翻译 ═══════════════
def _hs_google_translate(text, src="en", dst="zh-CN"):
    if not text or not text.strip():
        return text
    try:
        url = ("https://translate.googleapis.com/translate_a/single?"
               f"client=gtx&sl={src}&tl={dst}&dt=t&q={quote(text)}")
        req = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        result = ""
        for part in data[0]:
            if part and isinstance(part, list) and len(part) > 0:
                result += part[0]
        return result.strip() if result else text
    except Exception as e:
        print(f"[Google翻译失败] {e}")
        return f"⚠️ 翻译失败"


def _hs_google_translate_batch(titles, src="en", dst="zh-CN"):
    if not titles:
        return {}
    BATCH = 40
    result_map = {}
    for start in range(0, len(titles), BATCH):
        batch = titles[start:start + BATCH]
        combined = "\n".join(batch)
        translated = _hs_google_translate(combined, src, dst)
        lines = translated.split("\n")
        for i, title in enumerate(batch):
            if i < len(lines) and lines[i].strip():
                result_map[title] = lines[i].strip()
            else:
                result_map[title] = title
    return result_map


def _hs_llm_summary(items, category_name):
    """生成分类摘要 —— 批量翻译前几条标题后本地拼接（单次请求）。"""
    if not items:
        return ""
    top = [it.get("title", "") for it in items[:3] if it.get("title")]
    if not top:
        return ""
    try:
        zh_map = _hs_google_translate_batch(top)
        parts = [zh_map.get(t, t) for t in top]
        parts = [p for p in parts if p and not p.startswith("⚠️")]
        if parts:
            return f"📌 {category_name}：{'；'.join(parts[:3])}"
    except Exception:
        pass
    return ""


# ═══════════════ 抓取编排 ═══════════════
def _build_sections(config):
    src = config.get("sources", {})
    sections = []

    def on(k):
        return src.get(k, {}).get("enabled", True)

    if on("meme"):
        lim = src.get("meme", {}).get("limit", 15)
        sections.append(("🔥 热门Meme", lambda: _hs_fetch_memes(limit=lim)))
    if on("entertainment"):
        lim = src.get("entertainment", {}).get("limit", 10)
        sections.append(("🌟 娱乐新闻",
                         lambda: _hs_fetch_newsapi(max_results=lim) + _hs_fetch_rss(categories=["entertainment"])))
    if on("movie"):
        lim = src.get("movie", {}).get("limit", 30)
        sections.append(("🔥 热映资讯", lambda lim=lim: _hs_fetch_now_showing(limit=lim)))
    if on("sports"):
        sections.append(("🏈 体育热点", lambda: _hs_fetch_rss(categories=["sports"])))
    if on("video"):
        lim = src.get("video", {}).get("limit", 20)
        sections.append(("📹 视频热点", lambda: _hs_fetch_video_hotspots(max_results=lim)))
    if on("search"):
        lim = src.get("search", {}).get("limit", 20)
        sections.append(("🔍 搜索趋势", lambda: _hs_fetch_google_trends(lim)))
    if on("x"):
        lim = src.get("x", {}).get("limit", 20)
        sections.append(("𝕏 X热搜", lambda: _hs_fetch_x_trends(limit=lim)))
    if on("tiktok"):
        lim = src.get("tiktok", {}).get("limit", 20)
        sections.append(("🎵 TikTok", lambda: _hs_fetch_tiktok(limit=lim)))
    return sections


def _postprocess(items, config):
    excl = [k.strip().lower() for k in config.get("exclude_keywords", []) if k.strip()]
    if excl:
        items = [it for it in items
                 if not any(k in (it.get("title", "") + " " + it.get("desc", "")).lower() for k in excl)]
    _hs_enrich_scores(items)
    min_score = config.get("min_score", 0)
    if min_score and min_score > 0:
        items = [it for it in items if it.get("composite", 0) >= min_score]
    return items


def _public_item(it):
    return {k: v for k, v in it.items() if not k.startswith("_")}


def refresh_all(config):
    """并发抓取所有启用源 → 评分 → 全局缓存。返回 (data, summaries)。"""
    global _refreshing
    sections = _build_sections(config)
    total = max(len(sections), 1)
    results, summaries = {}, {}
    with ThreadPoolExecutor(max_workers=min(8, total)) as ex:
        futures = {}
        for name, func in sections:
            futures[ex.submit(_safe_fetch, name, func, config)] = name
        for fut in as_completed(futures):
            name, items, summary = fut.result()
            results[name] = items
            summaries[name] = summary
    # 确保未启用/失败的分类也有键（空列表），前端 Tab 不丢
    for cat in CAT_KEYS:
        results.setdefault(cat, [])
    _hs_save_cache(results, time.time(), summaries)
    return results, summaries


def _safe_fetch(name, func, config):
    try:
        items = func()
    except Exception as e:
        items = []
        print(f"[Fetch] {name}: {e}")
    items = _postprocess(items, config)
    summary = _hs_llm_summary(items, name)
    return name, [_public_item(it) for it in items], summary


# ═══════════════ Flask 路由 ═══════════════
@app.route("/")
def index():
    cfg = _hs_load_config()
    return render_template("index.html",
        cat_keys_json=json.dumps(CAT_KEYS, ensure_ascii=False),
        cat_colors_json=json.dumps(CAT_COLORS, ensure_ascii=False),
        source_labels_json=json.dumps(SOURCE_LABELS, ensure_ascii=False),
        config_json=json.dumps(cfg, ensure_ascii=False))


@app.route("/api/data")
def api_data():
    """缓存优先秒回：{updated_at, data, summaries}。前端打开即渲染，再后台刷新。"""
    with _cache_lock:
        return jsonify({"updated_at": _cache["updated_at"],
                        "data": _cache["data"],
                        "summaries": _cache["summaries"]})


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """触发一次（带防重入）刷新，返回最新数据。"""
    global _refreshing
    if _refreshing:
        with _cache_lock:
            return jsonify({"updated_at": _cache["updated_at"], "data": _cache["data"],
                            "summaries": _cache["summaries"], "note": "already_refreshing"})
    _refreshing = True
    try:
        cfg = _hs_load_config()
        data, summaries = refresh_all(cfg)
        return jsonify({"updated_at": int(time.time()), "data": data, "summaries": summaries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        _refreshing = False


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        return jsonify(_hs_load_config())
    # POST：保存配置
    body = request.get_json(force=True, silent=True) or {}
    cfg = _hs_load_config()
    if isinstance(body.get("sources"), dict):
        for k, v in body["sources"].items():
            if k in cfg["sources"]:
                cfg["sources"][k].update(v)
    for key in ("exclude_keywords", "refresh_interval_min", "min_score"):
        if key in body:
            cfg[key] = body[key]
    _hs_save_config(cfg)
    return jsonify({"ok": True, "config": cfg})


@app.route("/api/translate", methods=["POST"])
def api_translate():
    body = request.get_json(force=True, silent=True) or {}
    src = body.get("src", "en")
    dst = body.get("dst", "zh-CN")
    # 批量
    if "texts" in body and isinstance(body["texts"], list):
        result = _hs_google_translate_batch(body["texts"], src, dst)
        return jsonify({"translated": result})
    text = body.get("text", "")
    if not text:
        return jsonify({"error": "empty text"}), 400
    return jsonify({"translated": _hs_google_translate(text, src, dst)})


# ═══════════════ 后台自动刷新 ═══════════════
def _scheduler_loop():
    """定时（按配置间隔）在后台静默刷新缓存。"""
    while True:
        try:
            cfg = _hs_load_config()
            interval = max(1, cfg.get("refresh_interval_min", 15)) * 60
            time.sleep(interval)
            if not _refreshing:
                try:
                    refresh_all(cfg)
                    print("[Scheduler] 后台刷新完成")
                except Exception as e:
                    print(f"[Scheduler] 刷新失败: {e}")
        except Exception as e:
            print(f"[Scheduler] 异常: {e}")
            time.sleep(60)


def _startup_refresh():
    """启动即拉一次（不阻塞主线程），让缓存尽快变热。"""
    def _run():
        try:
            refresh_all(_hs_load_config())
            print("[Startup] 初次抓取完成")
        except Exception as e:
            print(f"[Startup] 初次抓取失败: {e}")
    t = threading.Thread(target=_run, daemon=True)
    t.start()


# ── 初始化 ──
_hs_load_cache()
_scheduler = threading.Thread(target=_scheduler_loop, daemon=True)
_scheduler.start()
_startup_refresh()


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
