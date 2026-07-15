"""
downloader.py — 内置下载引擎

- yt-dlp 子进程封装（视频/音频下载）
- 格式探测与选择
- 免版权音乐搜索（Pixabay / Mixkit / Freesound）
"""
from __future__ import annotations

import os, re, json, sys, time, threading, subprocess, logging
from dataclasses import dataclass, field
from typing import Optional, Callable

from PyQt6.QtCore import QThread, pyqtSignal


# ─── 常量 ───
DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "CreativeEnginePro", "downloads")

# Netscape 格式 cookies 文件路径（由 UI 的「导入 Cookie」按钮设置）。
# 用于 YouTube / 抖音 等需要登录态的站点。
# 从浏览器扩展「Get cookies.txt LOCALLY」导出。
YOUTUBE_COOKIES_FILE = ""


def set_cookies_file(path: str):
    """设置全局 cookies 文件路径（Netscape 格式）"""
    global YOUTUBE_COOKIES_FILE
    YOUTUBE_COOKIES_FILE = path or ""

# 支持的站点（国内优先 + 国际需代理）
SITES = {
    "bilibili.com":    "B站",
    "douyin.com":      "抖音",
    "kuaishou.com":    "快手",
    "xiaohongshu.com": "小红书",
    "xhslink.com":     "小红书",
    "pinterest.com":   "Pinterest",
    "pin.it":          "Pinterest",
    "youtube.com":     "YouTube",
    "youtu.be":        "YouTube",
    "tiktok.com":      "TikTok",
    "vimeo.com":       "Vimeo",
    "twitter.com":     "Twitter/X",
    "x.com":           "Twitter/X",
    "instagram.com":   "Instagram",
    "facebook.com":    "Facebook",
}
NEEDS_PROXY_SITES = {"youtube.com", "youtu.be", "tiktok.com", "twitter.com", "x.com",
                      "instagram.com", "facebook.com", "vimeo.com", "pinterest.com", "pin.it"}


# ─── 数据模型 ───
@dataclass
class FormatInfo:
    """yt-dlp 解析出的可用格式"""
    format_id: str
    ext: str
    resolution: str = ""       # 如 "1920x1080"
    note: str = ""             # 如 "1080p"
    filesize: int = 0
    vcodec: str = ""
    acodec: str = ""
    is_video_only: bool = False
    is_audio_only: bool = False

    @property
    def label(self) -> str:
        parts = []
        if self.resolution:
            h = self.resolution.split("x")[-1] if "x" in self.resolution else self.resolution
            parts.append(f"{h}p")
        elif self.note:
            parts.append(self.note)
        else:
            parts.append(self.format_id)
        if self.filesize > 0:
            parts.append(f"({self._fmt_size()})")
        if self.is_audio_only:
            parts.append("[仅音频]")
        elif self.is_video_only:
            parts.append("[仅视频]")
        return " ".join(parts)

    def _fmt_size(self):
        s = self.filesize
        for u in ("B", "KB", "MB", "GB"):
            if s < 1024:
                return f"{s:.0f}{u}"
            s /= 1024
        return f"{s:.1f}TB"


@dataclass
class DownloadTask:
    """单个下载任务"""
    url: str
    output_dir: Optional[str] = None   # None → 运行时取最新 DOWNLOAD_DIR（支持「更改目录」生效）
    format_id: str = ""        # 空=默认最佳
    title: str = ""
    status: str = "pending"    # pending / parsing / downloading / done / failed
    progress: float = 0.0      # 0~100
    speed: str = ""
    eta: str = ""
    downloaded: str = ""
    total_size: str = ""
    output_path: str = ""
    error: str = ""
    thumbnail: str = ""
    formats: list[FormatInfo] = field(default_factory=list)
    media_type: str = "video"  # video / audio
    video_only: bool = False   # 仅下载视频流（不含音频）
    cookies_browser: str = ""  # 指定用于 cookies 的浏览器（""=自动）


# ─── yt-dlp 可用性检测 ───
def _find_ytdlp() -> Optional[str]:
    """查找 yt-dlp 可执行文件路径"""
    # 1. 优先用项目内的 yt-dlp.exe
    local = os.path.join(os.path.dirname(__file__), "..", "yt-dlp.exe")
    if os.path.exists(local):
        return os.path.abspath(local)
    # 2. Python 模块可导入时，定位到对应 Scripts 目录的 yt-dlp.exe
    try:
        import sysconfig
        scripts_dir = sysconfig.get_paths().get("scripts", "")
        if scripts_dir:
            for name in ("yt-dlp.exe", "yt-dlp"):
                p = os.path.join(scripts_dir, name)
                if os.path.exists(p):
                    return p
    except Exception:
        pass
    # 3. Python module 自身能跑
    try:
        from yt_dlp import YoutubeDL  # noqa
        # 用 python -m yt_dlp 也能跑
        return sys.executable + "@@@-m@@@yt_dlp"
    except ImportError:
        pass
    # 4. 系统 PATH 中查找
    import shutil
    found = shutil.which("yt-dlp")
    if found:
        return found
    found = shutil.which("yt-dlp.exe")
    if found:
        return found
    return None


_ytdlp_path: Optional[str] = None


def ytdlp_available() -> bool:
    global _ytdlp_path
    if _ytdlp_path is None:
        _ytdlp_path = _find_ytdlp()
    return _ytdlp_path is not None


def ytdlp_update() -> tuple[bool, str]:
    """更新 yt-dlp 到最新版本。返回 (成功, 消息)

    注意：不能用 `yt-dlp -U`（它内部会调起 pip 孙进程并继承 stdout 管道，
    超时杀掉 yt-dlp 后 pip 仍持有管道 → communicate() 永远等不到 EOF → 线程挂死）。
    改为直接 `python -m pip install -U`，且 stdout/stderr 重定向到 DEVNULL，
    彻底避免管道被孙进程占用导致的卡死。
    """
    if not ytdlp_available():
        return False, "yt-dlp 未安装"
    try:
        python = sys.executable
        cmd = [python, "-m", "pip", "install", "--upgrade",
               "--no-input", "--no-warn-script-location", "yt-dlp"]
        # DEVNULL：不捕获输出，规避孙进程管道占用造成的永久挂死
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=120)
        if r.returncode == 0:
            return True, "已更新到最新版本"
        return False, f"更新失败（退出码 {r.returncode}）"
    except subprocess.TimeoutExpired:
        return False, "更新超时（网络较慢或被拦截），可稍后重试"
    except Exception as e:
        return False, str(e)


def _ytdlp_cmd() -> list[str]:
    global _ytdlp_path
    if _ytdlp_path is None:
        _ytdlp_path = _find_ytdlp()
    if _ytdlp_path:
        # 自定义占位符：exe@@@-m@@@yt_dlp
        if "@@@" in _ytdlp_path:
            parts = _ytdlp_path.split("@@@")
            return [parts[0], parts[1], parts[2]]
        return [_ytdlp_path]
    return ["yt-dlp"]


def _is_youtube(url: str) -> bool:
    return "youtube.com" in url or "youtu.be" in url


def _clean_youtube_url(url: str) -> str:
    """清理 YouTube URL，去掉播放列表/电台参数，只下单个视频"""
    import urllib.parse as _up
    if "youtu.be/" in url:
        # 短链接直接取视频 ID
        return url
    parsed = list(_up.urlparse(url))
    if "youtube.com" not in (parsed[1] or ""):
        return url
    qs = dict(_up.parse_qsl(parsed[4]))
    # 去掉播放列表/radio/index 参数
    for k in list(qs.keys()):
        if k in ("list", "start_radio", "index", "si"):
            del qs[k]
    parsed[4] = _up.urlencode(qs)
    return _up.urlunparse(parsed)


# ─── 浏览器检测 ───
# 优先级：Firefox 排最前 —— Firefox 不使用 Windows DPAPI 加密，
# 在 DPAPI 损坏的机器上仍能正常读出 cookies，作为「自动」首选最稳。
# key 为 yt-dlp 接受的浏览器名；每项为多候选路径，存在其一即视为已安装。
_BROWSER_PATHS = {
    "firefox": [
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
    ],
    "edge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ],
    "chromium": [
        r"C:\Program Files\Chromium\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Chromium\Application\chrome.exe"),
    ],
    "brave": [
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
    ],
    "opera": [
        os.path.join(os.path.expanduser("~"), "AppData", "Local", "Programs", "Opera", "opera.exe"),
        r"C:\Program Files\Opera\opera.exe",
        r"C:\Program Files (x86)\Opera\opera.exe",
    ],
    "vivaldi": [
        r"C:\Program Files\Vivaldi\Application\vivaldi.exe",
        r"C:\Program Files (x86)\Vivaldi\Application\vivaldi.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Vivaldi\Application\vivaldi.exe"),
    ],
    "whale": [
        r"C:\Program Files\Naver\Naver Whale\Application\whale.exe",
    ],
    "ghostery": [
        r"C:\Program Files\GhosteryBrowser\Application\ghostery.exe",
    ],
}
_BROWSER_PATHS_MAC = {
    "firefox": "/Applications/Firefox.app",
    "chrome": "/Applications/Google Chrome.app",
    "chromium": "/Applications/Chromium.app",
    "brave": "/Applications/Brave Browser.app",
    "opera": "/Applications/Opera.app",
    "vivaldi": "/Applications/Vivaldi.app",
    "edge": "/Applications/Microsoft Edge.app",
    "safari": "/Applications/Safari.app",
}
_BROWSER_PATHS_LINUX = {
    "firefox": ["/usr/bin/firefox", "/usr/bin/firefox-esr", "/snap/bin/firefox"],
    "chrome": ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/opt/google/chrome/chrome"],
    "chromium": ["/usr/bin/chromium", "/usr/bin/chromium-browser", "/snap/bin/chromium"],
    "brave": ["/usr/bin/brave-browser", "/opt/brave.com/brave/brave"],
    "opera": ["/usr/bin/opera", "/usr/bin/opera-stable"],
    "vivaldi": ["/usr/bin/vivaldi", "/usr/bin/vivaldi-stable"],
}
# 供 UI 显示的中文名（覆盖 yt-dlp 支持的全部常见浏览器）
BROWSER_LABELS = {
    "firefox": "Firefox", "edge": "Edge", "chrome": "Chrome", "chromium": "Chromium",
    "brave": "Brave", "opera": "Opera", "vivaldi": "Vivaldi", "whale": "Whale",
    "ghostery": "Ghostery", "safari": "Safari",
}


def get_available_browsers() -> list[str]:
    """返回本机已安装、可被 yt-dlp 用作 cookies 来源的浏览器名列表（按优先级）。

    跨平台扫描常见安装位置（含用户态目录 %LOCALAPPDATA%），
    并补充 PATH 兜底，确保任意品牌 / 任意系统的浏览器都能被识别。
    """
    found: list[str] = []
    if os.name == "nt":
        for name, paths in _BROWSER_PATHS.items():
            for p in paths:
                if p and os.path.exists(p):
                    found.append(name)
                    break
    elif sys.platform == "darwin":
        for name, p in _BROWSER_PATHS_MAC.items():
            if os.path.exists(p):
                found.append(name)
    else:  # Linux / 其他类 Unix
        for name, paths in _BROWSER_PATHS_LINUX.items():
            for p in paths:
                if p and os.path.exists(p):
                    found.append(name)
                    break
    # PATH 兜底（跨平台）：覆盖 yt-dlp 支持的全部浏览器可执行名
    import shutil as _sh
    for name in ("firefox", "edge", "msedge", "chrome", "chromium",
                 "brave", "opera", "vivaldi", "whale", "ghostery", "safari"):
        if name in found:
            continue
        found_path = _sh.which(name) or _sh.which(name + ".exe")
        if found_path:
            found.append("edge" if name == "msedge" else name)
    # 去重保序
    seen = set()
    return [b for b in found if not (b in seen or seen.add(b))]


def _find_browser_for_cookies() -> Optional[str]:
    """自动查找已安装的浏览器用于 --cookies-from-browser（返回优先级最高的那个）"""
    browsers = get_available_browsers()
    return browsers[0] if browsers else None


def auto_detect_browser() -> str:
    """返回自动检测到的浏览器名（无则空字符串），供 UI 显示"""
    return _find_browser_for_cookies() or ""


def _has_ejs_package() -> bool:
    """检测 yt-dlp-ejs 解密组件包是否已安装。

    该 PyPI 包（yt_dlp_ejs）内置 YouTube 的 n-challenge/n-sig JS 解密脚本，
    随本软件一起打包分发。安装后 yt-dlp 直接走本地 PYPACKAGE 源，
    完全不需要从 GitHub 运行时拉取（避免国内网络失败 + 首次「点两次」）。
    """
    try:
        import yt_dlp_ejs  # noqa: F401
        return True
    except Exception:
        return False


def _yt_youtube_args(browser: str = "", cookies_file: str = "") -> list[str]:
    """返回 YouTube 专用参数。

    优先级：
    1. cookies 文件（Netscape 格式，绕过 DPAPI + Chrome App-Bound Encryption）
    2. 浏览器 cookies（--cookies-from-browser，DPAPI 可能失败）

    同时加入 Node.js 运行时解决 2026 n-challenge/n-sig JS 解密；
    解密脚本优先用本地内置的 yt-dlp-ejs 组件包，仅当该包缺失时
    才回退到运行时从 GitHub 拉取（兜底，不应在正常分发中出现）。
    """
    args = []
    # ── cookies：浏览器优先（永不过期），文件兜底 ──
    if browser:
        args += ["--cookies-from-browser", browser]
    elif cookies_file and os.path.exists(cookies_file):
        args += ["--cookies", cookies_file]

    # ── n-challenge / n-sig JS 解密 ──
    # YouTube 2026 强制要求客户端执行 JavaScript 挑战。
    # 优先用本地 yt-dlp-ejs 包（已随 Python 环境安装，零网络依赖），
    # 仅在本地包缺失时回退 GitHub 远程拉取（兜底，正常不会走到）。
    args += ["--js-runtimes", "node"]
    if not _has_ejs_package():
        args += ["--remote-components", "ejs:github"]

    # ── 伪装客户端 + 跳过 SSL 证书验证（代理环境常见）──
    args += ["--extractor-args", "youtube:player_client=web,tv"]
    args += ["--no-check-certificates"]

    return args


def _is_tiktok(url: str) -> bool:
    return "tiktok.com" in url


def _yt_tiktok_args(browser: str = "", cookies_file: str = "") -> list[str]:
    """返回 TikTok 专用参数。

    优先级：
    1. 浏览器 cookies（--cookies-from-browser，自动提取当前登录态，不过期）
    2. cookies 文件（Netscape 格式，兜底）
    """
    args = []
    # ── cookies：浏览器优先（不过期）──
    browser = browser or _find_browser_for_cookies()
    if browser:
        args += ["--cookies-from-browser", browser]
    elif cookies_file and os.path.exists(cookies_file):
        args += ["--cookies", cookies_file]
    # ── TikTok 反爬 ──
    args += ["--no-check-certificates"]
    return args


def _is_douyin(url: str) -> bool:
    return "douyin.com" in url or "iesdouyin.com" in url or "v.douyin.com" in url


def _yt_douyin_args(browser: str = "", cookies_file: str = "") -> list[str]:
    """返回 抖音 专用参数：cookies 文件优先 → Firefox cookies（免 DPAPI）→ 其他浏览器"""
    if cookies_file and os.path.exists(cookies_file):
        # 检查 cookie 文件是否包含抖音域名
        # （用户可能导入的只是 YouTube 的 cookie，抖音需要单独导出 douyin.com 的 cookie）
        try:
            with open(cookies_file, "r", encoding="utf-8", errors="replace") as f:
                head = f.read(4096)
            if "douyin.com" in head:
                return ["--cookies", cookies_file, "--no-check-certificates"]
            # 不带抖音 cookie → 跳过，尝试浏览器 cookies
        except Exception:
            pass

    # Firefox 不用 Windows DPAPI 加密，本机 DPAPI 坏时也能正常读取
    browsers = get_available_browsers()
    firefox_ok = "firefox" in browsers
    if firefox_ok:
        return ["--cookies-from-browser", "firefox", "--no-check-certificates"]
    # 用户选择的浏览器（DPAPI 可能失败，但值得一试）
    if browser:
        return ["--cookies-from-browser", browser, "--no-check-certificates"]
    # 最后手段：任意已安装浏览器
    if browsers:
        return ["--cookies-from-browser", browsers[0], "--no-check-certificates"]
    return ["--no-check-certificates"]


def _is_chinese_url(url: str) -> bool:
    """国内站点直连可达"""
    chinese = {"bilibili.com", "douyin.com", "kuaishou.com", "ixigua.com"}
    for k in chinese:
        if k in url:
            return True
    return False


# ─── 视频信息探测 ───
def probe_url(url: str, timeout: int = 30) -> dict:
    """探测 URL 的可用格式和标题，返回 {"title": str, "formats": [FormatInfo], "thumbnail": str} 或 {}"""
    try:
        import subprocess as sp
        cmd = _ytdlp_cmd() + ["--dump-json", "--no-playlist",
                               "--socket-timeout", str(max(min(timeout, 20), 15))]
        # YouTube / TikTok 需要专用参数（cookies / extractor-args）
        if _is_youtube(url):
            cmd += _yt_youtube_args()
        if _is_tiktok(url):
            cmd += _yt_tiktok_args()
        cmd.append(url)
        r = sp.run(cmd, capture_output=True, timeout=timeout)
        # 检查 yt-dlp 是否失败
        if r.returncode != 0:
            err_text = r.stderr.decode("utf-8", errors="replace").strip()
            if not err_text:
                err_text = r.stdout.decode("utf-8", errors="replace").strip()
            # 提取最后一行 ERROR 作为消息
            for line in err_text.splitlines():
                if "ERROR:" in line:
                    err_text = line
                    break
            return {"_error": err_text[:300] if err_text else "yt-dlp 退出码 %d" % r.returncode}
        data = json.loads(r.stdout.decode("utf-8", errors="replace"))
        title = data.get("title", "")
        thumbnail = data.get("thumbnail", "") or ""
        raw_formats = data.get("formats", [])
        formats: list[FormatInfo] = []
        seen = set()
        for f in raw_formats:
            fid = f.get("format_id", "")
            if fid in seen:
                continue
            seen.add(fid)
            vcodec = f.get("vcodec", "") or ""
            acodec = f.get("acodec", "") or ""
            has_video = vcodec not in ("none", "")
            has_audio = acodec not in ("none", "")
            is_audio_only = not has_video and has_audio
            is_video_only = has_video and not has_audio
            fmt = FormatInfo(
                format_id=fid,
                ext=f.get("ext", "mp4"),
                resolution=f.get("resolution", "") or "",
                note=f.get("format_note", "") or "",
                filesize=f.get("filesize", 0) or 0,
                vcodec=vcodec,
                acodec=acodec,
                is_video_only=is_video_only,
                is_audio_only=is_audio_only,
            )
            formats.append(fmt)
        # 去重 + 按质量排序
        formats.sort(key=lambda x: (
            not (x.is_audio_only or x.is_video_only),  # 合并流优先
            -x.filesize,
            -(int(x.resolution.split("x")[-1]) if x.resolution and "x" in x.resolution else 0)
        ))
        return {"title": title, "formats": formats, "thumbnail": thumbnail}
    except Exception as e:
        # 尝试提取 yt-dlp 的具体错误信息
        err_msg = ""
        try:
            if isinstance(e, sp.CalledProcessError):
                err_msg = (e.stderr.decode("utf-8", errors="replace") if e.stderr else str(e))
            elif hasattr(e, 'stderr') and e.stderr:
                err_msg = e.stderr.decode("utf-8", errors="replace") if isinstance(e.stderr, bytes) else str(e.stderr)
        except Exception:
            pass
        if not err_msg:
            err_msg = str(e)[:200]
        logging.debug("probe_url failed: %s", err_msg[:100])
        return {"_error": err_msg}



# ─── 下载工作线程 ───
class DownloadWorker(QThread):
    """后台下载线程：运行 yt-dlp 子进程，通过信号报告进度"""

    progress_signal = pyqtSignal(str, float, str, str, str, str)  # task_id, pct(0-100), speed, eta, downloaded, total
    finished_signal = pyqtSignal(str, bool, str, str)  # task_id, success, output_path, error

    def __init__(self, task: DownloadTask):
        super().__init__()
        self.task = task
        self._stopped = False
        self._last_active = time.time()   # 最后一次收到子进程输出（看门狗用）
        self._timed_out = False           # 被看门狗强杀标记

    def stop(self):
        self._stopped = True

    def _build_env(self):
        """构建包含 Node.js PATH 的子进程环境"""
        env = os.environ.copy()
        self._ensure_node_in_path(env)
        # 强制 yt-dlp 以 UTF-8 输出（避免 Windows 上中文路径被 GBK 编码后解析失败）
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        return env

    def run(self):
        task = self.task
        tid = task.url  # 用 url 做临时 id
        # 清理 YouTube URL 播放列表参数（&list= / &start_radio= / &index=），
        # 确保只下单个视频，不受播放列表/电台影响
        url = _clean_youtube_url(task.url) if _is_youtube(task.url) else task.url
        out_dir = task.output_dir or DOWNLOAD_DIR   # None → 用当前最新目录（「更改目录」后立即生效）
        try:
            os.makedirs(out_dir, exist_ok=True)
            # 立即通知 UI 已进入下载流程（避免一直停在「排队中」看不出状态）
            self.progress_signal.emit(tid, 0.0, "", "解析中…", "", "")

            tmpl = os.path.join(out_dir, "%(title).100s.%(ext)s")
            cmd = _ytdlp_cmd() + [
                "--no-playlist",
                "--newline",
                "--socket-timeout", "30",
                "-o", tmpl,
            ]
            # YouTube / TikTok / 抖音 需要专用参数（cookies / extractor-args）
            browser = task.cookies_browser or _find_browser_for_cookies() or ""
            if _is_youtube(url):
                cmd += _yt_youtube_args(browser, YOUTUBE_COOKIES_FILE)
            if _is_tiktok(url):
                cmd += _yt_tiktok_args(browser, YOUTUBE_COOKIES_FILE)
            if _is_douyin(url):
                cmd += _yt_douyin_args(browser, YOUTUBE_COOKIES_FILE)
            # 按类型选择正确的格式（无需探测）
            if task.media_type == "audio":
                cmd += ["-x", "--audio-format", "mp3"]
                if task.format_id:
                    cmd += ["-f", task.format_id]
            elif task.video_only:
                cmd += ["-f", task.format_id or "bestvideo"]
            elif task.format_id:
                cmd += ["-f", task.format_id]
            cmd.append(url)

            # ── 执行下载（支持 DPAPI 失败自动降级重试）──
            output_path, error_lines, proc = self._run_with_retry(
                cmd, task, tid)

            if proc.returncode == 0:
                # Destination 行解析的路径可能因编码问题不准确，先验证存在性
                if not output_path or not os.path.exists(output_path):
                    print(f"[downloader] output_path 不可用: {output_path!r}，回退 _find_output")
                    output_path = _find_output(out_dir, task.title)
                if output_path and os.path.exists(output_path):
                    print(f"[downloader] 下载成功: {output_path}")
                    self.progress_signal.emit(tid, 100.0, "", "完成", "", "")
                    self.finished_signal.emit(tid, True, output_path, "")
                    return

            # ── 以下均为失败分支 ──
            if self._timed_out:
                self.finished_signal.emit(
                    tid, False, "",
                    "下载超时：长时间无响应（国内站点可能需要代理，或链接已失效）")
                return
            err = self._build_error_message(error_lines, proc.returncode, task)
            self.finished_signal.emit(tid, False, "", err)

        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
            self.finished_signal.emit(tid, False, "", "下载超时（网络不可达或需代理）")
        except FileNotFoundError:
            self.finished_signal.emit(tid, False, "", "yt-dlp 未安装，请运行: pip install yt-dlp")
        except Exception as e:
            self.finished_signal.emit(tid, False, "", str(e)[:120])

    def _run_with_retry(self, cmd: list[str], task, tid: str):
        """执行 yt-dlp 命令，按错误类型自动重试。

        返回 (output_path, error_lines, proc) 三元组。
        """
        output_path, error_lines, proc = self._execute_ytdlp(cmd, tid)
        if proc.returncode != 0 and not self._stopped and not self._timed_out:
            low_err = " ".join(error_lines).lower()

            # ── 1) DPAPI 解密失败：去掉 --cookies-from-browser 重试一次 ──
            if "--cookies-from-browser" in cmd and \
                    ("dpapi" in low_err or "decrypt" in low_err):
                retry_cmd = []
                skip_next = False
                for c in cmd:
                    if skip_next:
                        skip_next = False
                        continue
                    if c == "--cookies-from-browser":
                        skip_next = True  # 跳过后面的浏览器名
                        continue
                    retry_cmd.append(c)
                self.progress_signal.emit(tid, 0.0, "", "重试(无cookies)", "", "")
                return self._execute_ytdlp(retry_cmd, tid)

            # ── 2) YouTube bot 检测 → cookies 可能过期，降级无 cookies 重试一次 ──
            if (_is_youtube(task.url)
                    and "sign in" in low_err
                    and any(c == "--cookies" for c in cmd)):
                retry_cmd = []
                skip_next = False
                for c in cmd:
                    if skip_next:
                        skip_next = False
                        continue
                    if c in ("--cookies",):
                        skip_next = True
                        continue
                    retry_cmd.append(c)
                # 替换 player_client 为 tv_embedded（最宽松）
                for i, c in enumerate(retry_cmd):
                    if c.startswith("youtube:player_client="):
                        retry_cmd[i] = "youtube:player_client=tv_embedded"
                        break
                self.progress_signal.emit(
                    tid, 0.0, "", "Cookie过期,降级重试", "", "")
                return self._execute_ytdlp(retry_cmd, tid)

        return output_path, error_lines, proc

    @staticmethod
    def _ensure_node_in_path(env: dict):
        """确保 Node.js 在 PATH 中（yt-dlp 需要用于 JS 解密）"""
        import shutil
        # 系统已有 node 则直接返回
        if shutil.which("node", path=env.get("PATH", "")):
            return
        # 查找 managed Node.js
        candidates = [
            os.path.join(os.path.expanduser("~"),
                         ".workbuddy", "binaries", "node", "versions"),
        ]
        for base in candidates:
            if not os.path.isdir(base):
                continue
            for ver in sorted(os.listdir(base), reverse=True):
                d = os.path.join(base, ver)
                if os.path.isfile(os.path.join(d, "node.exe")):
                    env["PATH"] = d + os.pathsep + env.get("PATH", "")
                    return

    @staticmethod
    def _is_benign_warning(line: str) -> bool:
        """过滤不影响下载结果的无害 WARNING 行，避免弹窗吓到用户"""
        low = line.lower()
        benign_patterns = (
            "extractor failed to obtain",      # 小红书/部分站点常见 transient 警告
            "some formats may be missing",     # YouTube player_client 降级时的格式缺失
            "sabr-only streaming",             # YouTube 实验性流
            "gvs po token",                     # YouTube iOS token 缺失
            "signature solving failed",         # Firefox cookies 无 Node.js 时
            "n challenge solving",              # 同上
            "unable to find",                   # 可选组件缺失
            "javascript runtime",               # Node.js 未安装提示
        )
        return any(p in low for p in benign_patterns)

    def _execute_ytdlp(self, cmd: list[str], tid: str):
        """执行单次 yt-dlp 子进程，实时解析进度和错误。

        使用「读取线程 + 队列」架构：读取线程负责从管道读行→放入队列，
        主线程从队列取行→解析→发信号。彻底避免 Windows 管道死锁——
        即使 yt-dlp 孙进程不关 stdout 句柄导致管道永不 EOF，
        主线程也能通过看门狗超时 / 停止标志安全退出。


        返回 (output_path: str, error_lines: list[str], proc)。
        """
        import queue as _queue

        env = self._build_env()
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=env,
        )

        output_path = ""
        error_lines: list[str] = []
        last_pct = 0
        self._last_active = time.time()

        # ── 队列 + 读取线程 ──
        line_queue: _queue.Queue = _queue.Queue()

        def _reader():
            """独立线程：从管道逐行读取，放入队列。
            管道 EOF 或任何异常时退出，放入 sentinel。"""
            try:
                for raw in iter(proc.stdout.readline, b""):
                    if self._stopped:
                        break
                    line_queue.put(raw)
            except Exception:
                pass
            finally:
                line_queue.put(None)  # sentinel

        threading.Thread(target=_reader, daemon=True).start()

        # ── 看门狗：90s 无输出 → 杀进程 ──
        def _watchdog():
            while proc.poll() is None:
                if self._stopped:
                    return
                if time.time() - self._last_active > 90:
                    self._timed_out = True
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    return
                time.sleep(2)
        threading.Thread(target=_watchdog, daemon=True).start()

        # ── 主循环：从队列取行，解析进度 / 状态 / 错误 ──
        while True:
            try:
                raw = line_queue.get(timeout=2)
            except _queue.Empty:
                # 2s 无新行：检查是否该退出
                if self._stopped or self._timed_out:
                    break
                continue

            if raw is None:
                # 读取线程已退出（管道 EOF 或异常）
                break

            self._last_active = time.time()
            line = raw.decode("utf-8", errors="replace").strip()

            # ── 解析 yt-dlp 标准进度输出：[download] XX.X% … ──
            mm = re.search(r'\[download\]\s+([\d.]+)%', line)
            if mm:
                try:
                    pct = float(mm.group(1))
                    speed = eta = total = ""
                    sm = re.search(r'of\s+~?([\d.]+[KMGT]?i?B)', line)
                    if sm:
                        total = sm.group(1)
                    vm = re.search(r'at\s+([\d.]+\s*[KMGT]?i?B/s)', line)
                    if vm:
                        speed = vm.group(1).strip()
                    em = re.search(r'ETA\s+(\S+)', line)
                    if em:
                        eta = em.group(1)
                    self.progress_signal.emit(tid, pct, speed, eta, total, total)
                    last_pct = pct
                except Exception:
                    pass
                continue

            # ── 捕获目标文件名 ──
            if "[download] Destination:" in line:
                output_path = line.split("Destination:")[-1].strip()
            elif "[ExtractAudio] Destination:" in line:
                output_path = line.split("Destination:")[-1].strip()
            elif "[Merger]" in line and "Merging formats into" in line:
                output_path = line.split('"')[1] if '"' in line else ""

            # ── 提取阶段状态反馈 ──
            elif line.startswith("[youtube]") or line.startswith("[info]"):
                short = line
                if ": Downloading " in short:
                    short = short.split(": ", 1)[-1]
                self.progress_signal.emit(tid, 0.0, "", short[:80], "", "")

            # ── 收集错误/警告行 ──
            elif any(kw in line for kw in ("ERROR:", "WARNING:", "Unable to",
                                           "HTTP Error", "Connection",
                                           "timed out", "Forbidden",
                                           "blocked", "Sign in",
                                           "not a bot", "cookies")):
                if not ("WARNING:" in line and self._is_benign_warning(line)):
                    error_lines.append(line)

        # ── 清理：等待进程退出 ──
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                pass

        if self._stopped:
            return output_path, error_lines, proc

        # ── 收集进程退出后管道中残留的输出 ──
        try:
            remaining = proc.stdout.read()
            if remaining:
                for rl in remaining.decode("utf-8", errors="replace").splitlines():
                    rl = rl.strip()
                    if rl and any(kw in rl for kw in ("ERROR:", "WARNING:", "Unable")):
                        if not ("WARNING:" in rl and self._is_benign_warning(rl)):
                            error_lines.append(rl)
        except Exception:
            pass

        return output_path, error_lines, proc

    @staticmethod
    def _build_error_message(error_lines: list[str], returncode: int, task) -> str:
        """从原始错误行构建用户友好的错误信息"""
        if error_lines:
            err = error_lines[0][:200]
            low = err.lower()
            if "not a bot" in low or "sign in" in low:
                if _is_youtube(task.url):
                    using_file = bool(YOUTUBE_COOKIES_FILE and os.path.exists(YOUTUBE_COOKIES_FILE))
                    using_browser = bool(not using_file and task.cookies_browser)
                    bname = BROWSER_LABELS.get(task.cookies_browser, task.cookies_browser) or "所选浏览器"
                    if using_file:
                        return ("YouTube 反爬拦截：Cookie 文件可能已过期。"
                                "请用浏览器扩展「Get cookies.txt LOCALLY」重新导出 "
                                "YouTube 的 Cookie 文件后在面板「🍪 导入Cookie」重新导入")
                    if using_browser:
                        return (f"YouTube 反爬拦截：{bname} 的 cookie 已过期。"
                                f"请先打开 {bname} → 访问 YouTube → 刷新页面（确保已登录Google），"
                                f"再回 app 重新下载。（也可「🍪 导入Cookie」用扩展导出，cookie 文件比浏览器更稳定）")
                    return ("YouTube 反爬拦截：需在面板「🍪 导入Cookie」导入 "
                            "YouTube 的 Netscape 格式 Cookie 文件"
                            "（浏览器扩展「Get cookies.txt LOCALLY」导出）")
                if _is_douyin(task.url):
                    if YOUTUBE_COOKIES_FILE:
                        return ("抖音反爬拦截：Cookie 文件可能过期或缺少抖音域名。"
                                "请在浏览器扩展中导出抖音（douyin.com）的 Cookie，"
                                "或在 Firefox 浏览器登录抖音后重试（Firefox 无需 DPAPI）")
                    return ("抖音反爬拦截：请在 Firefox 浏览器登录抖音网页版后重试"
                            "（Firefox 无需 Windows DPAPI 加密，可直接读取）")
                return ("被反爬拦截（需已登录对应平台的 Cookie 文件，"
                        "或确保所选浏览器已登录该平台）")
            elif "dpapi" in low or "decrypt" in low:
                return ("浏览器 cookies 读取失败（Windows DPAPI 加密限制）。"
                        "已自动尝试无 cookies 下载 —— 如仍失败，请在面板"
                        "「🍪 导入Cookie」用浏览器扩展导出 Cookie 文件后重试")
            elif "cookies" in low or "login" in low or "fresh cookies" in low:
                bname = BROWSER_LABELS.get(task.cookies_browser, task.cookies_browser) or "自动"
                site_hint = "（可在面板「🍪 导入Cookie」用浏览器扩展导出 Cookie 文件后重试）"
                return (f"需要登录态：{site_hint} — 当前浏览器：{bname}")
            elif "forbidden" in low or "403" in low:
                return "访问被拒绝（403，可能需要登录或代理）"
            elif "timed out" in low or "connection" in low:
                return "网络超时/不可达（国内国际站点可能需要代理，或确认链接有效）"
            elif "unable to extract" in low or "unsupported" in low:
                return "无法解析该链接（可能已失效、链接格式不对、或 yt-dlp 需要更新）"
        if returncode != 0:
            return f"下载失败（yt-dlp 退出码 {returncode}），可能原因：链接无效 / 需要登录 / 被屏蔽 / 需代理"
        return "下载完成但未生成文件"


def _find_output(directory: str, title_hint: str) -> str:
    """在目录下找最近创建的文件（下载完成后可能不知道确切文件名）"""
    try:
        files = [os.path.join(directory, f) for f in os.listdir(directory)]
        files = [f for f in files if os.path.isfile(f)]
        if not files:
            return ""
        return max(files, key=os.path.getmtime)
    except Exception:
        return ""


# ─── 免版权音乐搜索 ───
FREESOUND_CLIENT_ID = "freesound_client_id"      # 用户替换为注册的 client_id
FREESOUND_CLIENT_SECRET = "freesound_client_secret"  # 用户替换为注册的 client_secret
FREESOUND_TOKEN_URL = "https://freesound.org/apiv2/oauth2/access_token/"
FREESOUND_SEARCH_URL = "https://freesound.org/apiv2/search/text/"
FREESOUND_DOWNLOAD_URL = "https://freesound.org/apiv2/sounds/{id}/download/"

# Freesound token 缓存
_freesound_token: Optional[str] = None
_freesound_token_expiry: float = 0


def _freesound_auth(client_id: str = "", client_secret: str = "") -> Optional[str]:
    """获取 Freesound OAuth2 access token（client credentials grant）"""
    global _freesound_token, _freesound_token_expiry
    cid = client_id or FREESOUND_CLIENT_ID
    csec = client_secret or FREESOUND_CLIENT_SECRET
    if not cid or cid == "freesound_client_id":
        return None
    # 缓存复用（1小时内有效）
    if _freesound_token and time.time() < _freesound_token_expiry:
        return _freesound_token
    try:
        import urllib.request, urllib.parse
        data = urllib.parse.urlencode({
            "client_id": cid, "client_secret": csec,
            "grant_type": "client_credentials",
        }).encode()
        req = urllib.request.Request(FREESOUND_TOKEN_URL, data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            token_data = json.loads(resp.read().decode())
        _freesound_token = token_data.get("access_token", "")
        expires_in = token_data.get("expires_in", 3600)
        _freesound_token_expiry = time.time() + expires_in - 60
        return _freesound_token
    except Exception:
        logging.debug("Freesound auth failed", exc_info=True)
        return None


def freesound_configured() -> bool:
    """检查 Freesound 密钥是否已配置"""
    return bool(_freesound_auth())


class MusicSearcher:
    """免版权音乐搜索

    🥇 Freesound API：直接搜索可下载的 mp3/wav 音频（需注册免费密钥）
    🥈 YouTube 搜索：通过 yt-dlp 搜索 royalty free music（需代理）
    🥉 精选网站链接：无需代理，直接浏览器打开
    """

    _instance: Optional[MusicSearcher] = None

    # 精选免版权音乐网站
    CURATED_SITES = [
        ("Pixabay Music", "https://pixabay.com/music/", "免费商用音乐"),
        ("Mixkit Music", "https://mixkit.co/free-stock-music/", "高质量免版权音乐"),
        ("Uppbeat", "https://uppbeat.io/browse/music", "免费音乐（需署名）"),
        ("Freesound", "https://freesound.org/search/?q=music", "社区音效与音乐"),
        ("YouTube 音频库", "https://www.youtube.com/audiolibrary/music", "YouTube 官方免费音乐"),
        ("Bensound", "https://www.bensound.com/royalty-free-music", "免版权背景音乐"),
    ]

    def __init__(self):
        self._cache: dict[str, list] = {}

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def search(self, query: str, page: int = 1, per_page: int = 20) -> list[dict]:
        """搜索音乐，返回 [{title, url, duration, source, ...}]"""
        cache_key = f"{query}:{page}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        results: list[dict] = []

        # 1. Freesound 直接音乐搜索（无需代理，真实 mp3/wav 下载）
        fs_results = self.search_freesound(query, max_results=min(per_page, 15))
        results.extend(fs_results)

        # 2. yt-dlp YouTube 搜索（需代理）
        yt_results = self.search_youtube(query, max_results=min(per_page - len(results), 10))
        results.extend(yt_results)

        # 3. 精选网站链接（始终显示在最后）
        curated = self._curated_links(query)
        results.extend(curated)

        self._cache[cache_key] = results
        return results

    def _curated_links(self, query: str) -> list[dict]:
        """精选免版权音乐网站"""
        results = []
        q_lower = query.lower().strip()
        for name, url, desc in self.CURATED_SITES:
            if (not q_lower
                    or q_lower in name.lower()
                    or q_lower in desc.lower()
                    or q_lower in url.lower()):
                results.append({
                    "title": f"🔗 {name}",
                    "url": url,
                    "duration": 0,
                    "preview_url": "",
                    "source": desc,
                    "is_curated_link": True,
                })
        return results

    def search_youtube(self, query: str, max_results: int = 15) -> list[dict]:
        """通过 yt-dlp 搜索 YouTube 'royalty free music'（需代理）。
        返回 [{title, url, duration, source: 'YouTube'}, ...]
        """
        if not ytdlp_available():
            return []
        try:
            import subprocess as sp
            search_term = f"ytsearch{max_results}:royalty free {query} music no copyright"
            cmd = _ytdlp_cmd() + [
                "--dump-json", "--flat-playlist",
                "--no-playlist", "--socket-timeout", "15",
                search_term,
            ]
            r = sp.run(cmd, capture_output=True, timeout=25)
            results = []
            for line in r.stdout.decode("utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    dur = data.get("duration", 0) or 0
                    dur_str = ""
                    if dur > 0:
                        m, s = divmod(int(dur), 60)
                        dur_str = f"{m}:{s:02d}"
                    results.append({
                        "title": data.get("title", ""),
                        "url": data.get("webpage_url", ""),
                        "duration": dur,
                        "duration_str": dur_str,
                        "preview_url": data.get("url", "") or data.get("webpage_url", ""),
                        "source": f"YouTube · {dur_str}" if dur_str else "YouTube",
                    })
                except json.JSONDecodeError:
                    continue
            return results
        except Exception:
            logging.debug("yt-dlp YouTube music search failed", exc_info=True)
            return []

    def search_freesound(self, query: str, max_results: int = 15) -> list[dict]:
        """通过 Freesound API 搜索真实可下载音频（需注册免费密钥）。
        返回 [{title, url, preview_url, download_url, duration, source: 'Freesound'}, ...]
        未配置密钥时返回空列表。
        """
        token = _freesound_auth()
        if not token:
            return []
        try:
            import urllib.request, urllib.parse
            params = urllib.parse.urlencode({
                "query": query, "page_size": max_results,
                "fields": "id,name,duration,previews,license,username",
                "token": token,
            })
            url = f"{FREESOUND_SEARCH_URL}?{params}"
            req = urllib.request.Request(url, headers={"User-Agent": "CreativeEnginePro/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            results = []
            for sound in data.get("results", []):
                sid = sound.get("id")
                dur = float(sound.get("duration", 0) or 0)
                dur_str = ""
                if dur > 0:
                    m, s = divmod(int(dur), 60)
                    dur_str = f"{m}:{s:02d}"
                preview = ""
                previews = sound.get("previews", {})
                if isinstance(previews, dict):
                    preview = previews.get("preview-lq-mp3", "")
                results.append({
                    "title": sound.get("name", ""),
                    "url": f"https://freesound.org/s/{sid}/",
                    "download_url": FREESOUND_DOWNLOAD_URL.format(id=sid),
                    "preview_url": preview,
                    "duration": dur,
                    "duration_str": dur_str,
                    "source": f"Freesound · {dur_str}" if dur_str else "Freesound",
                    "freesound_id": sid,
                })
            return results
        except Exception:
            logging.debug("Freesound search failed", exc_info=True)
            return []
