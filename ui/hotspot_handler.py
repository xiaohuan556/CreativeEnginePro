# ui/hotspot_handler.py
"""
热点雷达 Tab5 混入模块 — 海外热点聚合器
数据源: KYM / Reddit / NewsAPI / TMDB / YouTube / Google Trends / RSS
布局: 左（分类导航 + AI 摘要）| 右（热点列表 + 翻译 + 跳转）
"""

import os
import sys
import json
import hashlib
import re
import threading
import webbrowser
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import urlencode, quote
import xml.etree.ElementTree as ET

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QProgressBar, QSizePolicy,
    QAbstractItemView, QMessageBox, QStackedWidget,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QUrl
from PyQt6.QtGui import QFont, QColor, QMouseEvent

# ── 加载 .env ──
PROJECT = Path(__file__).parent.parent
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
TRENDMCP_KEY = os.getenv("TRENDMCP_KEY", "")

# ═══════════════ 分类 & 颜色 ═══════════════
CAT_COLORS = {
    "🔥 热门Meme":    "#E0646E",
    "🌟 娱乐新闻":    "#CB9842",
    "🎬 电影资讯":    "#787CE6",
    "🏈 体育热点":    "#44A87A",
    "📹 视频热点":    "#D0659E",
    "🔍 搜索趋势":    "#8B7CF6",
}
CAT_KEYS = list(CAT_COLORS.keys())

# ═══════════════ 数据获取（从热点雷达 main.py 迁移） ═══════════════

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


def _hs_fetch_meme_fallback(limit=15):
    """Know Your Meme → Google News 多层降级"""
    results = []
    try:
        req = Request("https://knowyourmeme.com/memes", headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        pattern = re.compile(
            r'<a[^>]+href="(/memes/[^"]+)"[^>]*>(.*?)</a>', re.S)
        seen = set()
        for match in pattern.finditer(html):
            href, block = match.group(1), match.group(2)
            if "page/" in href:
                continue
            img_match = re.search(r'<img[^>]+src="([^"]+)"', block)
            img = img_match.group(1) if img_match else ""
            h3_match = re.search(r'<h3[^>]+class="title"[^>]*>([^<]+)</h3>', block)
            if h3_match:
                title = h3_match.group(1).strip()
            else:
                span_match = re.search(r'<span[^>]*>([^<]+)</span>', block)
                title = span_match.group(1).strip() if span_match else ""
            if not title or title in seen or len(title) < 3:
                continue
            if title.lower() in ("meme", "subculture", "event", "entry", "photo", "video", "image"):
                continue
            seen.add(title)
            url = f"https://knowyourmeme.com{href}"
            img_url = img if img.startswith("http") else f"https:{img}" if img.startswith("//") else img
            results.append(dict(
                id=f"kym_{hashlib.md5(title.encode()).hexdigest()[:12]}",
                title=title, source="Know Your Meme", category="memes",
                url=url, score=0, comments=0, desc=title[:200], image=img_url,
            ))
            if len(results) >= limit:
                break
        if results:
            return results
    except Exception as e:
        print(f"[MemeFallback KYM]: {e}")

    try:
        rss_url = "https://news.google.com/rss/search?q=viral+meme+funny+trending&hl=en-US&gl=US&ceid=US:en"
        req = Request(rss_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8", errors="replace")
        root = ET.fromstring(content)
        for i, item in enumerate(root.iter("item")):
            if i >= limit:
                break
            title = item.find("title")
            link = item.find("link")
            if title is None or not title.text:
                continue
            title_text = title.text.strip()
            if not any(k in title_text.lower() for k in ["meme", "viral", "tiktok", "funny", "trend", "internet"]):
                continue
            results.append(dict(
                id=f"meme_{hashlib.md5(title_text.encode()).hexdigest()[:12]}",
                title=title_text, source="Google News", category="memes",
                url=link.text.strip() if link is not None else "",
                score=0, comments=0, desc=title_text[:200],
            ))
        if results:
            return results
    except Exception as e:
        print(f"[MemeFallback GoogleNews]: {e}")

    return [dict(
        id="meme_notice", title="⚠️ Meme 数据源暂时不可用",
        source="系统提示", category="memes",
        url="", score=0, comments=0,
        desc="Reddit 和 Know Your Meme 均无法访问，请稍后再试。",
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
            search_url = f"https://trends.google.com/trends/explore?q={quote(title)}"
            results.append(dict(
                id=f"trend_{hashlib.md5(title.encode()).hexdigest()[:12]}",
                title=title, source="Google Trends", category="trend",
                url=news_url or search_url, score=0, comments=0,
                desc=news_title or f"搜索量: {traffic}",
            ))
        return results
    except Exception as e:
        print(f"[GoogleTrends]: {e}")
        return []


def _hs_fetch_youtube(max_results=12):
    if not YOUTUBE_KEY:
        return []
    try:
        params = dict(part="snippet", chart="mostPopular", regionCode="US",
                      maxResults=max_results, key=YOUTUBE_KEY)
        url = f"https://www.googleapis.com/youtube/v3/videos?{urlencode(params)}"
        with urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        results = []
        for item in data.get("items", []):
            s = item.get("snippet", {})
            results.append(dict(
                id=f"yt_{item['id']}", title=s.get("title", ""),
                source=s.get("channelTitle", "YouTube"), category="video",
                url=f"https://youtube.com/watch?v={item['id']}",
                desc=s.get("description", "")[:200],
            ))
        return results
    except Exception as e:
        print(f"[YouTube]: {e}")
        return []


def _hs_fetch_tmdb():
    if not TMDB_KEY:
        return []
    results = []
    try:
        for ep in ["/movie/upcoming", "/trending/movie/week"]:
            url = f"https://api.themoviedb.org/3{ep}?api_key={TMDB_KEY}&language=zh-CN&region=US"
            with urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
            for item in data.get("results", [])[:10]:
                results.append(dict(
                    id=f"tmdb_{item['id']}", title=item.get("title", ""),
                    source="TMDB", category="movie",
                    url=f"https://www.themoviedb.org/movie/{item['id']}",
                    desc=(item.get("overview") or "")[:200],
                    score=int(item.get("vote_average", 0) * 100),
                ))
        return results
    except Exception as e:
        print(f"[TMDB]: {e}")
        return []


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
                    if desc is not None and desc.text:
                        desc_text = re.sub(r'<[^>]+>', '', desc.text)[:200]
                    results.append(dict(
                        id=f"rss_{hashlib.md5((title.text or '').encode()).hexdigest()[:12]}",
                        title=title.text.strip(), source=name, category=category,
                        url=link.text.strip() if link is not None else "",
                        desc=desc_text,
                    ))
            except Exception as e:
                print(f"[RSS] {name}: {e}")
    return results


def _hs_fetch_trendmcp(source, limit=20):
    if not TRENDMCP_KEY:
        return []
    try:
        body = json.dumps({"mode": "top_trends", "type": source, "limit": limit}).encode()
        req = Request("https://api.trendsmcp.ai/api", data=body, headers={
            "Authorization": f"Bearer {TRENDMCP_KEY}",
            "Content-Type": "application/json"
        })
        with urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read())
        if isinstance(raw, dict) and "body" in raw:
            data = json.loads(raw["body"])
        else:
            data = raw
        results = []
        for rk, name in data.get("data", []):
            results.append(dict(
                id=f"trend_{hashlib.md5(name.encode()).hexdigest()[:12]}",
                title=name, source=source, category="trend",
                url=f"https://trends.google.com/trends/explore?q={quote(name)}",
                score=rk, desc="",
            ))
        return results
    except Exception as e:
        print(f"[TrendMCP] {source}: {e}")
        return []


def _hs_fetch_x_trends(limit=25):
    if not TRENDMCP_KEY:
        return []
    try:
        body = json.dumps({"mode": "top_trends", "type": "X (Twitter)", "limit": limit}).encode()
        req = Request("https://api.trendsmcp.ai/api", data=body, headers={
            "Authorization": f"Bearer {TRENDMCP_KEY}",
            "Content-Type": "application/json"
        })
        with urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read())
        if isinstance(raw, dict) and "body" in raw:
            data = json.loads(raw["body"])
        else:
            data = raw
        results = []
        for rk, name in data.get("data", []):
            results.append(dict(
                id=f"xt_{hashlib.md5(name.encode()).hexdigest()[:12]}",
                title=name, source="X (Twitter)", category="x_trend",
                url=f"https://x.com/search?q={quote(name)}",
                score=rk, desc="",
            ))
        if results:
            return results
    except Exception as e:
        print(f"[X Trends]: {e}")

    try:
        body = json.dumps({"mode": "top_trends", "source": "google news", "limit": limit}).encode()
        req = Request("https://api.trendsmcp.ai/api", data=body, headers={
            "Authorization": f"Bearer {TRENDMCP_KEY}",
            "Content-Type": "application/json"
        })
        with urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read())
        if isinstance(raw, dict) and "body" in raw:
            data = json.loads(raw["body"])
        else:
            data = raw
        results = []
        for rk, name in data.get("data", []):
            results.append(dict(
                id=f"soc_{hashlib.md5(name.encode()).hexdigest()[:12]}",
                title=name, source="Google News", category="social",
                url=f"https://news.google.com/search?q={quote(name)}",
                score=rk, desc="",
            ))
        return results
    except Exception as e:
        print(f"[SocialTrends]: {e}")
        return []


# ═══════════════ LLM 翻译 & 摘要 ═══════════════

def _hs_google_translate(text, src="en", dst="zh-CN"):
    """免费谷歌翻译 — 直接调网页接口，无需 API Key"""
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
        # 返回结构: [["译文","原文",...], ...]
        result = ""
        for part in data[0]:
            if part and isinstance(part, list) and len(part) > 0:
                result += part[0]
        return result.strip() if result else text
    except Exception as e:
        print(f"[Google翻译失败] {e}")
        return f"⚠️ 翻译失败"


def _hs_google_translate_batch(titles, src="en", dst="zh-CN"):
    """批量谷歌翻译 — 多条拼成一次请求，返回 {原文: 译文} 字典"""
    if not titles:
        return {}
    # 谷歌单次请求有长度限制，每批最多 ~50 条短标题
    BATCH = 40
    result_map = {}
    for start in range(0, len(titles), BATCH):
        batch = titles[start:start + BATCH]
        # 用换行拼接多条，一次翻译
        combined = "\n".join(batch)
        translated = _hs_google_translate(combined, src, dst)
        lines = translated.split("\n")
        for i, title in enumerate(batch):
            if i < len(lines) and lines[i].strip():
                result_map[title] = lines[i].strip()
            else:
                result_map[title] = title  # 翻译缺失时保留原文
    return result_map


def _hs_translate_text(text):
    """单条翻译 — 调用免费谷歌翻译"""
    return _hs_google_translate(text)


def _hs_translate_batch(titles):
    """批量翻译 — 调用免费谷歌翻译，多条拼一次请求"""
    return _hs_google_translate_batch(titles)


def _hs_llm_summary(items, category_name):
    """生成分类摘要 — 翻译前几条标题后本地拼接，无需 LLM / API Key"""
    if not items:
        return ""
    top = items[:5]
    titles_zh = []
    for it in top:
        t = it.get("title", "")
        if t:
            zh = _hs_google_translate(t)
            if not zh.startswith("⚠️"):
                titles_zh.append(zh)
    if not titles_zh:
        return ""
    return f"📌 {category_name}：{'；'.join(titles_zh[:3])}"





# ═══════════════ Meme 缓存 ═══════════════

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


def _hs_fetch_memes():
    items = _hs_fetch_meme_fallback()
    if items:
        cache_file = PROJECT / "meme_cache.json"
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump({"memes": items, "source": "Know Your Meme"}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return items
    reddit = _hs_fetch_reddit({"memes": "memes+dankmemes+funny"})
    if reddit:
        return reddit
    return _hs_load_meme_cache()


# ═══════════════ 热点条目卡（暗色主题适配） ═══════════════

class HotItemCard(QFrame):
    """单条热点：序号 + 翻译按钮 + 色标 + 标题 + 来源 + 热度"""
    _trans_done_signal = pyqtSignal(str)

    def __init__(self, item, color, rank, parent=None):
        super().__init__(parent)
        self._item = item
        self._color = color
        self._rank = rank
        self._translated = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(52)
        self.setStyleSheet(f"""
            HotItemCard{{background:#252525;border:1px solid #383838;border-radius:8px;}}
            HotItemCard:hover{{background:#303030;border-color:#505050;}}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)

        # 序号
        lbl_rank = QLabel(f"{rank:02d}")
        lbl_rank.setFixedWidth(24)
        lbl_rank.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_rank.setStyleSheet(f"color:{color};font-size:11px;font-weight:700;background:transparent;")
        layout.addWidget(lbl_rank)

        # 翻译按钮
        self.btn_trans = QPushButton("译")
        self.btn_trans.setFixedSize(26, 26)
        self.btn_trans.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_trans.setStyleSheet(
            f"QPushButton{{background:transparent;color:{color};border:1px solid {color}30;"
            f"border-radius:5px;font-size:10px;font-weight:600;}}"
            f"QPushButton:hover{{background:{color}15;border-color:{color}50;}}")
        self.btn_trans.clicked.connect(self._do_translate)
        layout.addWidget(self.btn_trans)

        # 色标竖线
        bar = QFrame()
        bar.setFixedWidth(3)
        bar.setFixedHeight(24)
        bar.setStyleSheet(f"background:{color};border-radius:1px;")
        layout.addWidget(bar)

        # 标题
        self.lbl_title = QLabel(item["title"])
        self.lbl_title.setWordWrap(False)
        self.lbl_title.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_title.setStyleSheet("color:#cccccc;font-size:13px;background:transparent;")
        layout.addWidget(self.lbl_title, 1)

        # 来源标签
        src = item.get("source", "")
        if src:
            lbl_src = QLabel(src)
            lbl_src.setStyleSheet(
                "color:#999999;font-size:10px;background:#2a2a2a;"
                "border:1px solid #383838;border-radius:3px;padding:1px 6px;")
            lbl_src.setMaximumWidth(120)
            layout.addWidget(lbl_src)

        # 热度
        if item.get("score"):
            heat = item["score"]
            heat_str = f"{heat/1000:.1f}k" if heat >= 1000 else str(heat)
            lbl_heat = QLabel(f"⬆ {heat_str}")
            lbl_heat.setStyleSheet("color:#777777;font-size:10px;background:transparent;")
            lbl_heat.setFixedWidth(46)
            lbl_heat.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(lbl_heat)

        self._trans_done_signal.connect(self._on_trans_done)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        url = self._item.get("url", "")
        if url:
            webbrowser.open(url)
        super().mouseDoubleClickEvent(event)

    def _do_translate(self):
        if self._translated:
            self.lbl_title.setText(self._item["title"])
            self.lbl_title.setStyleSheet("color:#cccccc;font-size:13px;background:transparent;")
            self.btn_trans.setText("译")
            self.btn_trans.setStyleSheet(
                f"QPushButton{{background:transparent;color:{self._color};"
                f"border:1px solid {self._color}30;border-radius:5px;font-size:10px;font-weight:600;}}"
                f"QPushButton:hover{{background:{self._color}15;border-color:{self._color}50;}}")
            self._translated = False
            return

        self.lbl_title.setText("⏳ 翻译中…")
        self.lbl_title.setStyleSheet("color:#777777;font-size:13px;font-style:italic;background:transparent;")
        self.btn_trans.setEnabled(False)

        def _run():
            result = _hs_translate_text(self._item["title"])
            try:
                self._trans_done_signal.emit(result)
            except RuntimeError:
                pass  # 控件已被刷新销毁，忽略
        threading.Thread(target=_run, daemon=True).start()

    def _on_trans_done(self, result):
        try:
            if result.startswith("⚠️"):
                self.lbl_title.setText(result)
                self.lbl_title.setStyleSheet("color:#e06060;font-size:12px;background:transparent;")
                self.btn_trans.setEnabled(True)
                self.btn_trans.setText("译")
            else:
                self.lbl_title.setText(result)
                self.lbl_title.setStyleSheet(f"color:{self._color};font-size:13px;font-weight:500;background:transparent;")
                self.btn_trans.setEnabled(True)
                self.btn_trans.setText("↩")
                self.btn_trans.setStyleSheet(
                    f"QPushButton{{background:{self._color}20;color:{self._color};"
                    f"border:1px solid {self._color}40;border-radius:5px;font-size:10px;font-weight:600;}}"
                    f"QPushButton:hover{{background:{self._color}30;}}")
                self._translated = True
        except RuntimeError:
            pass  # 控件已被刷新销毁


# ═══════════════ 分类面板 ═══════════════

class HotCategoryPanel(QWidget):
    trans_progress = pyqtSignal(int, int)

    def __init__(self, category, color, parent=None):
        super().__init__(parent)
        self._category = category
        self._color = color
        self._items = []
        self.setStyleSheet("background:transparent;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(10)

        # AI 摘要卡片
        self.summary_card = QFrame()
        self.summary_card.setStyleSheet(
            f"QFrame{{background:#1e2828;border:1px solid #2a3838;"
            f"border-radius:8px;border-left:4px solid {color};}}")
        self.summary_card.setVisible(False)
        sum_layout = QHBoxLayout(self.summary_card)
        sum_layout.setContentsMargins(14, 10, 14, 10)
        icon = QLabel("📊")
        icon.setStyleSheet("font-size:16px;background:transparent;")
        sum_layout.addWidget(icon)
        self.lbl_summary = QLabel("")
        self.lbl_summary.setWordWrap(True)
        self.lbl_summary.setStyleSheet("color:#aaaaaa;font-size:12px;line-height:1.5;background:transparent;")
        sum_layout.addWidget(self.lbl_summary, 1)
        layout.addWidget(self.summary_card)

        # 列表头
        header = QHBoxLayout()
        header.setSpacing(8)
        lbl_rank_h = QLabel("#")
        lbl_rank_h.setFixedWidth(24)
        lbl_rank_h.setStyleSheet("color:#777777;font-size:10px;font-weight:600;")
        lbl_rank_h.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(lbl_rank_h)
        header.addSpacing(26)
        header.addSpacing(3)
        lbl_title_h = QLabel("标题 / 来源")
        lbl_title_h.setStyleSheet("color:#777777;font-size:10px;font-weight:600;")
        header.addWidget(lbl_title_h, 1)

        # 全部翻译
        self.btn_trans_all = QPushButton("全部翻译")
        self.btn_trans_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_trans_all.setFixedSize(72, 24)
        self.btn_trans_all.setStyleSheet(
            f"QPushButton{{background:transparent;color:{color};"
            f"border:1px solid {color}40;border-radius:4px;font-size:10px;}}"
            f"QPushButton:hover{{background:{color}15;border-color:{color};}}"
            f"QPushButton:disabled{{color:#555555;border-color:#383838;}}")
        self.btn_trans_all.clicked.connect(self._translate_all)
        header.addWidget(self.btn_trans_all)
        header.addSpacing(8)

        self.lbl_count = QLabel("加载中…")
        self.lbl_count.setStyleSheet("color:#777777;font-size:10px;")
        header.addWidget(self.lbl_count)
        layout.addLayout(header)

        # 分隔线
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background:#383838;")
        layout.addWidget(sep)

        # 列表区
        self._list_widget = QWidget()
        self._list_widget.setStyleSheet("background:transparent;")
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 4, 0, 0)
        self._list_layout.setSpacing(4)
        layout.addWidget(self._list_widget)
        layout.addStretch()

        self.trans_progress.connect(self._on_trans_progress)

    def set_items(self, items, summary=""):
        while self._list_layout.count():
            child = self._list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._items = []
        self.btn_trans_all.setEnabled(True)
        self.btn_trans_all.setText("全部翻译")

        if summary:
            self.lbl_summary.setText(summary)
            self.summary_card.setVisible(True)
        else:
            self.summary_card.setVisible(False)

        shown = min(len(items), 20)
        for i, item in enumerate(items[:20], 1):
            w = HotItemCard(item, self._color, i)
            self._list_layout.addWidget(w)
            self._items.append(w)

        if shown == 0:
            self.lbl_count.setText("暂无数据")
            self.btn_trans_all.setEnabled(False)
            hint_map = {
                "🎬 电影资讯": "⚠️ 未配置 TMDB_API_KEY → https://www.themoviedb.org/settings/api",
                "📹 视频热点": "⚠️ 未配置 YOUTUBE_API_KEY → https://console.cloud.google.com/apis",
            }
            if self._category in hint_map:
                hint = QLabel(hint_map[self._category])
                hint.setWordWrap(True)
                hint.setStyleSheet(
                    "color:#999999;font-size:11px;background:#2a2218;"
                    "border:1px solid #554422;border-radius:6px;padding:8px 12px;")
                self._list_layout.addWidget(hint)
        elif len(items) > 20:
            self.lbl_count.setText(f"20/{len(items)}")
        else:
            self.lbl_count.setText(f"{shown}条")

    def _translate_all(self):
        self.btn_trans_all.setEnabled(False)
        self.btn_trans_all.setText("翻译中…")
        untranslated = [w for w in self._items if not w._translated]
        if not untranslated:
            self.btn_trans_all.setEnabled(True)
            self.btn_trans_all.setText("全部翻译")
            return

        titles = [w._item["title"] for w in untranslated]

        def _run():
            result_map = _hs_translate_batch(titles)
            for idx, w in enumerate(untranslated):
                title = w._item["title"]
                translation = result_map.get(title, title)
                try:
                    w._trans_done_signal.emit(translation)
                    self.trans_progress.emit(idx + 1, len(untranslated))
                except RuntimeError:
                    pass  # 控件已被刷新销毁

        threading.Thread(target=_run, daemon=True).start()

    def _on_trans_progress(self, done, total):
        self.btn_trans_all.setText(f"{done}/{total}")
        if done >= total:
            self.btn_trans_all.setEnabled(True)
            self.btn_trans_all.setText("✓ 已翻译")


# ═══════════════ Tab 导航按钮 ═══════════════

class HotTabButton(QPushButton):
    """分类 Tab 按钮 - 暗色主题"""
    ACTIVE_STYLE = (
        "QPushButton{{background:transparent;color:{color};border:none;"
        "border-bottom:2px solid {color};border-radius:0px;"
        "font-size:12px;font-weight:600;padding:6px 16px 3px 16px;}}"
    )
    INACTIVE_STYLE = (
        "QPushButton{{background:transparent;color:#777777;border:none;"
        "border-bottom:2px solid transparent;border-radius:0px;"
        "font-size:12px;padding:6px 16px 3px 16px;}}"
        "QPushButton:hover{{color:{color};}}"
    )

    def __init__(self, text, color, parent=None):
        super().__init__(text, parent)
        self._color = color
        self._active = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_style()

    def set_active(self, active):
        self._active = active
        self._update_style()

    def _update_style(self):
        if self._active:
            self.setStyleSheet(self.ACTIVE_STYLE.format(color=self._color))
        else:
            self.setStyleSheet(self.INACTIVE_STYLE.format(color=self._color))


# ═══════════════ 数据获取线程 ═══════════════

class HotFetchWorker(QThread):
    progress = pyqtSignal(int, str)
    done_section = pyqtSignal(str, list, str)
    all_done = pyqtSignal()

    def run(self):
        sections = [
            ("🔥 热门Meme", _hs_fetch_memes),
            ("🌟 娱乐新闻", lambda: _hs_fetch_newsapi() + _hs_fetch_rss(categories=["entertainment"])),
            ("🎬 电影资讯", _hs_fetch_tmdb),
            ("🏈 体育热点", lambda: _hs_fetch_rss(categories=["sports"])),
            ("📹 视频热点", _hs_fetch_youtube),
            ("🔍 搜索趋势", lambda: _hs_fetch_google_trends(20)),
        ]
        total = len(sections)
        for i, (name, func) in enumerate(sections):
            self.progress.emit(int(i / total * 100), name)
            try:
                items = func()
            except Exception as e:
                items = []
                print(f"[Worker] {name}: {e}")
            summary = _hs_llm_summary(items, name)
            self.done_section.emit(name, items, summary)
        self.all_done.emit()


# ═══════════════ HotspotHandler 混入 ═══════════════

class HotspotHandler:
    """热点雷达 Tab5 混入"""

    def _build_hs_nav(self):
        """构建分类 Tab 导航栏"""
        tab_bar = QHBoxLayout()
        tab_bar.setContentsMargins(0, 0, 0, 0)
        tab_bar.setSpacing(6)

        self._hs_tabs = {}
        self._hs_tab_order = []
        for cat in CAT_KEYS:
            color = CAT_COLORS[cat]
            btn = HotTabButton(cat, color)
            btn.clicked.connect(lambda checked, c=cat: self._hs_switch_tab(c))
            tab_bar.addWidget(btn)
            self._hs_tabs[cat] = btn
            self._hs_tab_order.append(cat)

        tab_bar.addStretch()
        return tab_bar

    def _hs_switch_tab(self, cat):
        for c, btn in self._hs_tabs.items():
            btn.set_active(c == cat)
        idx = self._hs_tab_order.index(cat)
        self._hs_stack.setCurrentIndex(idx)

    def build_hotspot_module(self):
        """构建完整的 Tab5 热点雷达面板"""
        wrapper = QWidget()
        wrapper.setStyleSheet("background:transparent;")
        root = QVBoxLayout(wrapper)
        root.setContentsMargins(24, 16, 24, 16)
        root.setSpacing(12)

        # ═══ 顶栏 ═══
        top = QHBoxLayout()
        top.setSpacing(10)

        logo_icon = QLabel("📡")
        logo_icon.setStyleSheet("font-size:24px;background:transparent;")
        top.addWidget(logo_icon)

        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        lbl_title = QLabel("热点雷达")
        lbl_title.setStyleSheet("font-size:18px;font-weight:700;color:#cccccc;background:transparent;")
        title_col.addWidget(lbl_title)
        lbl_sub = QLabel("海外热点聚合 · 双击标题即可跳转原文")
        lbl_sub.setStyleSheet("color:#777777;font-size:11px;background:transparent;")
        title_col.addWidget(lbl_sub)
        top.addLayout(title_col)
        top.addStretch()

        self.btn_hs_refresh = QPushButton("🔄  刷新热点")
        self.btn_hs_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_hs_refresh.setObjectName("PrimaryBtn")
        self.btn_hs_refresh.setMinimumWidth(120)
        self.btn_hs_refresh.clicked.connect(self._hs_refresh)
        top.addWidget(self.btn_hs_refresh)
        root.addLayout(top)

        # ═══ 进度条 ═══
        self.hs_progress = QProgressBar()
        self.hs_progress.setFixedHeight(3)
        self.hs_progress.setStyleSheet(
            "QProgressBar{background:#2a2a2a;border:none;border-radius:2px;}"
            "QProgressBar::chunk{background:#3d8ef8;border-radius:2px;}")
        self.hs_progress.setRange(0, 100)
        self.hs_progress.setTextVisible(False)
        root.addWidget(self.hs_progress)

        # ═══ 状态行 ═══
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 2, 0, 0)
        self.hs_status = QLabel("就绪")
        self.hs_status.setStyleSheet("color:#777777;font-size:11px;")
        status_row.addWidget(self.hs_status)
        status_row.addStretch()
        tip_label = QLabel("💡 双击任意热点即可打开原文链接")
        tip_label.setStyleSheet("color:#555555;font-size:11px;")
        status_row.addWidget(tip_label)
        root.addLayout(status_row)

        # ═══ Tab 导航 ═══
        root.addLayout(self._build_hs_nav())

        # 分隔线
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background:#383838;")
        root.addWidget(sep)

        # ═══ 内容区 ═══
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollBar:vertical{background:transparent;width:6px;margin:0;}"
            "QScrollBar::handle:vertical{background:#555555;border-radius:3px;min-height:40px;}"
            "QScrollBar::handle:vertical:hover{background:#777777;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0px;}"
            "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:none;}"
        )

        self._hs_stack = QStackedWidget()
        self._hs_stack.setStyleSheet("background:transparent;")
        self._hs_panels = {}

        for cat in CAT_KEYS:
            color = CAT_COLORS[cat]
            panel = HotCategoryPanel(cat, color)
            self._hs_panels[cat] = panel
            self._hs_stack.addWidget(panel)

        scroll.setWidget(self._hs_stack)
        root.addWidget(scroll, 1)

        # 默认选中第一个分类
        if self._hs_tab_order:
            self._hs_switch_tab(self._hs_tab_order[0])

        # 启动后自动拉取一次
        QTimer.singleShot(300, self._hs_refresh)

        return wrapper

    def _hs_refresh(self):
        # 防止重复点击
        if hasattr(self, '_hs_worker') and self._hs_worker and self._hs_worker.isRunning():
            return
        self.btn_hs_refresh.setEnabled(False)
        self.btn_hs_refresh.setText("⏳ 获取中…")
        self.hs_progress.setValue(0)
        self.hs_status.setText("正在拉取海外热点数据…")

        # 清空现有面板
        for panel in self._hs_panels.values():
            panel.set_items([], "")

        self._hs_worker = HotFetchWorker()
        self._hs_worker.progress.connect(self._hs_on_progress)
        self._hs_worker.done_section.connect(self._hs_on_section)
        self._hs_worker.all_done.connect(self._hs_on_all_done)
        self._hs_worker.start()

    def _hs_on_progress(self, pct, name):
        self.hs_progress.setValue(pct)
        self.hs_status.setText(f"⏳ 正在获取「{name}」…")

    def _hs_on_section(self, category, items, summary):
        panel = self._hs_panels.get(category)
        if panel:
            panel.set_items(items, summary)

    def _hs_on_all_done(self):
        self.btn_hs_refresh.setEnabled(True)
        self.btn_hs_refresh.setText("🔄  刷新热点")
        self.hs_progress.setValue(100)
        self.hs_status.setText("✅ 热点更新完成")
        QTimer.singleShot(3000, lambda: self.hs_progress.setValue(0))
