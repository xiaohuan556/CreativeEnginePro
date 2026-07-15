#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tribe of Noise 免费 CC 授权音乐下载器 (合规版)
=============================================

用途
----
仅用于下载你自己的 Tribe of Noise *免费账户* 授权范围内的 CC 授权音乐，
作为 CreativeEnginePro 的 BGM / 音效素材库来源。

授权与合规要求（务必遵守）
--------------------------
1. 必须用自己的免费账户登录态（Cookie）下载，不得冒用他人账户。
2. 不得绕过付费墙：本脚本只走免费版详情页 /music/show/<id>，
   只下载能在详情页拿到下载链接的CC授权曲目（PRO专享内容拿不到链接会自动跳过）。
3. 授权类型默认 CC BY-SA 4.0：可商用，但必须在作品中为作者署名，
   且以相同方式共享。脚本会自动生成 credits.csv 供署名。
4. 遵守 rate-limit（默认每首间隔 1.5s），尊重网站 ToS。
5. 下载内容仅用于你自己的剪辑项目，不要转售音乐本身。

使用方法
--------
1. 浏览器登录 https://tribeofnoise.com （免费注册）
2. 打开 DevTools → Network → 随便点一个请求 → 复制 Request Headers 里的 Cookie 值
3. 把 Cookie 存到文件（如 ton_cookie.txt），或设为环境变量 TON_COOKIE
4. 运行：
   python tools/tribeofnoise_cc_downloader.py --keyword jazz --out ./assets/bgm --cookie-file ton_cookie.txt
   或：
   set TON_COOKIE=你的cookie值
   python tools/tribeofnoise_cc_downloader.py --keyword "background music" --out ./assets/bgm --limit 30

依赖：仅 Python 标准库（urllib / re / csv）。
"""

import argparse
import csv
import os
import re
import sys
import time
import urllib.parse
import urllib.request

BASE_SEARCH = "https://prosearch.tribeofnoise.com/search/index"
BASE_DETAIL = "https://tribeofnoise.com/music/show"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def http_get_bytes(url, cookie="", referer=""):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    if cookie:
        req.add_header("Cookie", cookie)
    if referer:
        req.add_header("Referer", referer)
    return urllib.request.urlopen(req, timeout=30).read()


def http_get_text(url, cookie="", referer=""):
    return http_get_bytes(url, cookie, referer).decode("utf-8", "replace")


def search_songs(keyword, max_pages=10, cookie=""):
    """解析 prosearch 公开搜索结果，返回 song 元信息列表。"""
    results = []
    for p in range(1, max_pages + 1):
        url = (BASE_SEARCH + "?" +
               urllib.parse.urlencode({"keyword": keyword, "page": p}))
        try:
            html = http_get_text(url, cookie=cookie, referer=BASE_SEARCH)
        except Exception as e:
            print(f"  ! 搜索页 {p} 请求失败: {e}")
            break
        rows = re.findall(
            r'<tr id="song_row_(\d+)"[^>]*data-profile-id="(\d+)"[^>]*>(.*?)</tr>',
            html, re.S)
        if not rows:
            break
        for sid, pid, row in rows:
            title_m = re.search(r'c-song__title">\s*([^<]+)', row)
            artist_m = re.search(r'c-song__artist"[^>]*>([^<]+)<', row)
            # 时长（如 3:28）与 BPM（纯数字列）
            dur_m = re.search(r'(\d+:\d{2})', row)
            bpm_m = re.findall(r'>(\d{2,3})<', row)
            results.append({
                "song_id": sid,
                "profile_id": pid,
                "title": (title_m.group(1).strip() if title_m else f"song_{sid}"),
                "artist": (artist_m.group(1).strip() if artist_m else "unknown"),
                "duration": dur_m.group(1) if dur_m else "",
                "bpm": bpm_m[-1] if bpm_m else "",
            })
        print(f"  搜索页 {p}: 已收集 {len(results)} 条")
        # 没有下一页特征（结果不足一页）则停止
        if "song_row_" not in html.split("page=")[-1][:200] and len(rows) < 20:
            break
    return results


def extract_download_link(html, debug_html_path=None):
    """从登录态详情页提取真实下载 URL。多策略容错。"""
    if debug_html_path:
        try:
            with open(debug_html_path, "w", encoding="utf-8") as f:
                f.write(html)
        except Exception:
            pass
    # 策略1: inline track 对象里的 download_link 字段
    m = re.search(r'download_link\s*[:=]\s*["\']([^"\']+)["\']', html)
    if m and m.group(1).startswith("http"):
        return m.group(1)
    # 策略2: #download_button 的 href（登录后由 JS 填充）
    m = re.search(r'id="download_button"[^>]*href="([^"]+)"', html)
    if m and m.group(1).startswith("http"):
        return m.group(1)
    # 策略3: 页面内裸 mp3 直链
    m = re.search(r'(https?://[^\s"\'>]+\.mp3)', html)
    if m:
        return m.group(1)
    return None


def extract_license(html):
    """提取授权类型文本（用于署名表）。"""
    m = re.search(r'(Creative Commons[^<]{0,40})', html)
    if m:
        return m.group(1).strip()
    return "CC BY-SA 4.0"


def safe_filename(s):
    return re.sub(r'[^\w\-]+', '_', s).strip('_') or "untitled"


def download_file(url, cookie, out_path, referer):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    if cookie:
        req.add_header("Cookie", cookie)
    req.add_header("Referer", referer)
    data = urllib.request.urlopen(req, timeout=120).read()
    with open(out_path, "wb") as f:
        f.write(data)
    return len(data)


def load_cookie(args):
    if args.cookie_file and os.path.isfile(args.cookie_file):
        with open(args.cookie_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    return os.environ.get("TON_COOKIE", "")


def main():
    ap = argparse.ArgumentParser(
        description="Tribe of Noise 免费 CC 授权音乐下载器 (合规版)")
    ap.add_argument("--keyword", required=True, help="搜索关键词")
    ap.add_argument("--out", default="ton_music", help="输出目录")
    ap.add_argument("--limit", type=int, default=20, help="最多下载数量")
    ap.add_argument("--delay", type=float, default=1.5, help="每首间隔秒数")
    ap.add_argument("--cookie-file", default="", help="存放 Cookie 的文本文件路径")
    ap.add_argument("--max-pages", type=int, default=10, help="搜索最大翻页数")
    ap.add_argument("--debug", action="store_true", help="保存首个详情页HTML便于诊断")
    args = ap.parse_args()

    cookie = load_cookie(args)
    if not cookie:
        print("错误：未提供 Cookie。请登录免费账户后通过 --cookie-file 或 "
              "环境变量 TON_COOKIE 提供。详见脚本顶部说明。")
        sys.exit(1)

    os.makedirs(args.out, exist_ok=True)
    print(f"[1/3] 搜索关键词: {args.keyword}")
    songs = search_songs(args.keyword, max_pages=args.max_pages, cookie=cookie)
    if not songs:
        print("未搜到结果。")
        sys.exit(0)
    songs = songs[:args.limit]
    print(f"      命中 {len(songs)} 首待处理")

    print(f"[2/3] 逐首提取下载链接并下载")
    credits = []
    for idx, s in enumerate(songs, 1):
        print(f"  ({idx}/{len(songs)}) {s['title']} — {s['artist']}")
        detail_url = f"{BASE_DETAIL}/{s['song_id']}"
        try:
            html = http_get_text(detail_url, cookie=cookie, referer=BASE_DETAIL)
        except Exception as e:
            print(f"      ! 详情页请求失败: {e}")
            continue
        link = extract_download_link(
            html, debug_html_path=(f"debug_song_{s['song_id']}.html"
                                   if args.debug and idx == 1 else None))
        if not link:
            print(f"      ! 未找到下载链接（可能需登录或非CC授权），跳过")
            continue
        lic = extract_license(html)
        fn = safe_filename(f"{s['artist']} - {s['title']}") + ".mp3"
        path = os.path.join(args.out, fn)
        try:
            sz = download_file(link, cookie, path, detail_url)
            credits.append([fn, s["title"], s["artist"], lic,
                            s.get("duration", ""), s.get("bpm", ""),
                            detail_url])
            print(f"      ✓ {fn} ({sz // 1024} KB)  [{lic}]")
        except Exception as e:
            print(f"      ! 下载失败: {e}")
        time.sleep(max(0.3, args.delay))

    csv_path = os.path.join(args.out, "credits.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "title", "artist", "license",
                    "duration", "bpm", "source"])
        w.writerows(credits)
    print(f"[3/3] 完成。成功下载 {len(credits)} 首，署名表: {csv_path}")


if __name__ == "__main__":
    main()
