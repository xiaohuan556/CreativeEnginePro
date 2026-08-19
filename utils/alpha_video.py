# utils/alpha_video.py
"""Alpha 视频支持 — 检测 alpha 通道 + FFmpeg 帧提取（保留透明度）

OpenCV 的 VideoCapture 默认把所有帧强制转为 BGR 3通道，丢弃 alpha 通道，
导致 MOV（ProRes 4444 / yuva420p 等）透明背景视频在预览/导出时显示为黑底。
本模块用 FFmpeg 直接解码为 BGRA，保留 alpha 通道。

性能优化：AlphaVideoPipeReader 维护一个持久 FFmpeg 进程，顺序播放时
直接从管道读取 raw BGRA 字节（~5-15ms/帧），避免每帧启动新进程（~100-300ms/帧）。
"""
from __future__ import annotations
import os
import re
import subprocess
import logging
import threading
import numpy as np

# ── 缓存：path → has_alpha（避免重复 probe）──
_alpha_cache: dict = {}

# ── 视频信息缓存：path → (w, h, fps) ──
_info_cache: dict = {}

# 含 alpha 通道的 pix_fmt 集合
_ALPHA_PIXFMTS = {
    # yuva 系列（完整覆盖 9/10/12/16 bit LE/BE）
    'yuva420p', 'yuva422p', 'yuva444p',
    'yuva420p9le', 'yuva420p9be',
    'yuva420p10le', 'yuva420p10be',
    'yuva420p12le', 'yuva420p12be',
    'yuva420p16le', 'yuva420p16be',
    'yuva422p10le', 'yuva422p10be',
    'yuva422p12le', 'yuva422p12be',
    'yuva422p16le', 'yuva422p16be',
    'yuva444p10le', 'yuva444p10be',
    'yuva444p12le', 'yuva444p12be',
    'yuva444p16le', 'yuva444p16be',
    # rgba 系列
    'rgba', 'rgba64le', 'rgba64be',
    'bgra', 'bgra64le', 'bgra64be',
    'argb', 'abgr',
    # gbrap 系列
    'gbrap', 'gbrap10le', 'gbrap10be',
    'gbrap12le', 'gbrap12be',
    'gbrap16le', 'gbrap16be',
    # 调色板（可能含 alpha）
    'pal8',
}

# ffmpeg -i stderr 中搜索的 alpha 关键词（用于 ffprobe 不可用时的回退检测）
_ALPHA_KEYWORDS = ['yuva', 'rgba', 'bgra', 'argb', 'abgr', 'gbrap', 'alpha']

# Windows: 隐藏子进程窗口的 flag
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def _ffprobe_bin() -> str:
    try:
        from config import FFPROBE_BIN
        return FFPROBE_BIN
    except Exception:
        return "ffprobe"


def _ffmpeg_bin() -> str:
    try:
        from config import FFMPEG_BIN
        return FFMPEG_BIN
    except Exception:
        return "ffmpeg"


def _probe_video_info(path: str, input_path_override: str | None = None) -> tuple:
    """获取视频的 width, height, fps（缓存）

    用 ffmpeg -i 解析 stderr 中的 Stream 信息。
    input_path_override: 若提供，ffmpeg -i 用此路径（如英文临时副本），
                         但缓存 key 仍用原始 path。
    """
    if path in _info_cache and input_path_override is None:
        return _info_cache[path]
    w, h, fps = 0, 0, 30.0
    # ffmpeg 实际打开的文件路径（中文路径时用英文临时副本）
    ff_input = input_path_override or path
    try:
        result = subprocess.run(
            [_ffmpeg_bin(), '-i', ff_input],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,    # 二进制模式读取，避免 GBK 解码崩溃
            timeout=10,
            creationflags=_NO_WINDOW,
        )
        # 手动解码 stderr（utf-8 + errors='replace' 避免 GBK 问题）
        info = result.stderr.decode('utf-8', errors='replace') if result.stderr else ''
        # 解析分辨率：1920x1080 或 1280x720
        m = re.search(r'(\d{2,5})x(\d{2,5})', info)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
        # 解析帧率：30 fps 或 29.97 fps
        m2 = re.search(r'(\d+(?:\.\d+)?)\s*fps', info)
        if m2:
            fps = float(m2.group(1))
    except Exception:
        logging.debug("probe_video_info failed: %s", path, exc_info=True)
    if fps <= 0:
        fps = 30.0
    # 只在成功获取尺寸时缓存（避免缓存错误的 0 值导致后续调用永远失败）
    if w > 0 and h > 0:
        _info_cache[path] = (w, h, fps)
    return w, h, fps


def probe_has_alpha(path: str, input_path_override: str | None = None) -> bool:
    """检测视频文件是否包含 alpha 通道（结果缓存）

    优先用 ffprobe 读取 pix_fmt；ffprobe 不存在时回退到 ffmpeg -i 解析 stderr。
    input_path_override: 若提供，ffmpeg/ffprobe 用此路径（如英文临时副本），
                         但缓存 key 仍用原始 path。
    """
    if path in _alpha_cache and input_path_override is None:
        return _alpha_cache[path]
    has = False
    ff = _ffmpeg_bin()
    ff_input = input_path_override or path

    # ── 方案1：ffprobe 精确检测（如果存在）──
    fp = _ffprobe_bin()
    if os.path.exists(fp):
        try:
            result = subprocess.run(
                [fp, '-v', 'quiet',
                 '-select_streams', 'v:0',
                 '-show_entries', 'stream=pix_fmt',
                 '-of', 'csv=p=0', ff_input],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
                creationflags=_NO_WINDOW,
            )
            pix = result.stdout.decode('utf-8', errors='replace').strip().splitlines()[0].strip() if result.stdout else ''
            has = pix in _ALPHA_PIXFMTS or pix.startswith('yuva')
        except Exception:
            logging.debug("probe_has_alpha(ffprobe) failed: %s", path, exc_info=True)

    # ── 方案2：ffmpeg -i 解析 stderr（ffprobe 不存在时）──
    if not has:
        try:
            result = subprocess.run(
                [ff, '-i', ff_input],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,   # 二进制模式，避免 GBK 解码崩溃
                timeout=10,
                creationflags=_NO_WINDOW,
            )
            info = result.stderr.decode('utf-8', errors='replace').lower() if result.stderr else ''
            # 搜索 alpha pix_fmt 关键词
            has = any(kw in info for kw in _ALPHA_KEYWORDS)
        except Exception:
            logging.debug("probe_has_alpha(ffmpeg) failed: %s", path, exc_info=True)

    _alpha_cache[path] = has
    return has


_audio_cache: dict[str, bool] = {}


def probe_has_audio(path: str) -> bool:
    """快速检测文件是否包含音频流（结果缓存）。

    项目发布包通常只内置 ffmpeg.exe，不一定带 ffprobe.exe，因此 ffprobe
    不可用或执行失败时必须回退到 ``ffmpeg -i``。否则会把探测失败误判成
    “无音轨”，导出器继而过滤掉全部音频并输出 ``-an`` 静音视频。
    """
    if path in _audio_cache:
        return _audio_cache[path]
    has = False
    probed = False
    try:
        fp = _ffprobe_bin()
        result = subprocess.run(
            [fp, '-v', 'quiet', '-select_streams', 'a', '-show_entries',
             'stream=codec_type', '-of', 'csv=p=0', path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=5, creationflags=_NO_WINDOW,
        )
        if result.returncode == 0:
            has = len(result.stdout.strip()) > 0
            probed = True
    except Exception:
        pass

    if not probed:
        try:
            result = subprocess.run(
                [_ffmpeg_bin(), '-i', path],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                timeout=10, creationflags=_NO_WINDOW,
            )
            info = result.stderr.decode('utf-8', errors='replace') if result.stderr else ''
            # 仅匹配 FFmpeg 的音频 Stream 行，避免文件名/元数据中的 "Audio" 误判。
            has = re.search(r'Stream\s+#\S+.*:\s*Audio\s*:', info, re.IGNORECASE) is not None
            probed = bool(info)
        except Exception:
            logging.debug("probe_has_audio(ffmpeg) failed: %s", path, exc_info=True)

    # 探测工具完全不可用时不缓存失败结果，避免一次环境抖动让本次会话永久静音。
    if probed:
        _audio_cache[path] = has
    return has


# ═══════════════════════════════════════════════════════════════════════════
# AlphaVideoPipeReader — 持久 FFmpeg 管道读取器
# ═══════════════════════════════════════════════════════════════════════════

class AlphaVideoPipeReader:
    """持久 FFmpeg 管道读取器，顺序播放时复用同一进程。

    传统方式每帧启动一个新 FFmpeg 进程（~100-300ms/帧），30fps 播放时
    后台线程完全跟不上。本类维护一个持续输出 raw BGRA 帧的 FFmpeg 进程，
    顺序播放时直接从 stdout 管道读取字节（~5-15ms/帧）。

    - 顺序播放（帧间 < _SEEK_THRESHOLD）：从管道读取，跳过中间帧
    - 远跳（帧间 >= _SEEK_THRESHOLD）：kill 旧进程，用 -ss 重新启动
    - 线程安全：内部有 lock
    """

    _SEEK_THRESHOLD = 1.0  # 秒；超过此距离的跳转触发进程重启

    def __init__(self, path: str):
        self.path = path
        # ── 中文路径处理 ──
        self._src_tmp = None
        self._ff_input = path
        try:
            path.encode('ascii')
        except UnicodeEncodeError:
            import tempfile, shutil
            _fd, self._src_tmp = tempfile.mkstemp(suffix='.mov', prefix='cep_pipe_')
            os.close(_fd)
            shutil.copy2(path, self._src_tmp)
            self._ff_input = self._src_tmp
        w, h, fps = _probe_video_info(path, input_path_override=self._ff_input)
        self._w = w
        self._h = h
        self._fps = fps
        self._frame_dt = 1.0 / fps if fps > 0 else 1.0 / 30.0
        self._frame_bytes = w * h * 4  # BGRA = 4 bytes/pixel
        self._proc = None
        self._cur_sec = -1.0
        self._lock = threading.Lock()

    def _start(self, start_sec: float):
        """在 start_sec 处启动 FFmpeg 进程"""
        self._stop()

        # 如果尺寸未知，重新 probe
        if self._w <= 0 or self._h <= 0:
            w, h, fps = _probe_video_info(self.path, input_path_override=self._ff_input)
            self._w, self._h, self._fps = w, h, fps
            self._frame_dt = 1.0 / fps if fps > 0 else 1.0 / 30.0
            self._frame_bytes = w * h * 4

        if self._frame_bytes <= 0:
            logging.debug("AlphaVideoPipeReader: cannot determine frame size for %s", self.path)
            return

        ff = _ffmpeg_bin()
        cmd = [
            ff,
            '-ss', f'{max(0.0, start_sec):.4f}',
            '-i', self._ff_input,   # 用英文临时副本路径
            '-f', 'rawvideo',
            '-pix_fmt', 'bgra',
            '-an',            # 不处理音频
            '-loglevel', 'error',
            'pipe:1',
        ]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=_NO_WINDOW,
            )
            self._cur_sec = max(0.0, start_sec)
        except Exception:
            logging.debug("AlphaVideoPipeReader._start failed: %s", self.path, exc_info=True)
            self._proc = None

    def _stop(self):
        """终止 FFmpeg 进程"""
        if self._proc:
            try:
                self._proc.kill()
                self._proc.wait(timeout=1)
            except Exception:
                pass
            self._proc = None

    def _read_raw(self) -> bytes | None:
        """从管道读取一帧 raw BGRA 数据"""
        if not self._proc:
            return None
        try:
            data = self._proc.stdout.read(self._frame_bytes)
        except Exception:
            return None
        if not data or len(data) < self._frame_bytes:
            return None
        return data

    def read_frame(self, src_sec: float) -> np.ndarray | None:
        """读取 src_sec 处的帧（BGRA numpy 数组）。

        - 顺序播放时复用管道，仅跳过中间帧
        - 远跳超过 _SEEK_THRESHOLD 时重启进程
        """
        with self._lock:
            src_sec = max(0.0, src_sec)

            # ── 判断是否需要重启 ──
            need_restart = (
                self._proc is None
                or self._cur_sec < 0
                or abs(src_sec - self._cur_sec) > self._SEEK_THRESHOLD
            )

            if need_restart:
                self._start(src_sec)
                if not self._proc:
                    return None
                data = self._read_raw()
                if data is None:
                    return None
                self._cur_sec = src_sec
                return np.frombuffer(data, dtype=np.uint8).reshape(
                    (self._h, self._w, 4))

            # ── 顺序读取：跳过中间帧 ──
            frames_to_skip = max(0, int(
                (src_sec - self._cur_sec) / self._frame_dt + 0.5))
            for _ in range(frames_to_skip):
                if self._read_raw() is None:
                    # 管道断了（可能到达文件尾），重启
                    self._start(src_sec)
                    if not self._proc:
                        return None
                    break
                self._cur_sec += self._frame_dt

            # 读取目标帧
            data = self._read_raw()
            if data is None:
                # 管道异常，尝试重启一次
                self._start(src_sec)
                if not self._proc:
                    return None
                data = self._read_raw()
                if data is None:
                    return None

            self._cur_sec = src_sec
            return np.frombuffer(data, dtype=np.uint8).reshape(
                (self._h, self._w, 4))

    def close(self):
        """终止进程，释放资源"""
        with self._lock:
            self._stop()
            self._cur_sec = -1.0
        # 清理中文路径的临时副本
        if self._src_tmp and os.path.exists(self._src_tmp):
            try:
                os.remove(self._src_tmp)
            except Exception:
                pass


# ── 管道读取器全局缓存 ──
_pipe_readers: dict = {}  # path → AlphaVideoPipeReader
_pipe_lock = threading.Lock()


def _get_pipe_reader(path: str) -> AlphaVideoPipeReader | None:
    """获取或创建管道读取器（缓存）"""
    with _pipe_lock:
        reader = _pipe_readers.get(path)
        if reader is None:
            try:
                reader = AlphaVideoPipeReader(path)
                if reader._w > 0 and reader._h > 0:
                    _pipe_readers[path] = reader
                else:
                    return None
            except Exception:
                logging.debug("create pipe reader failed: %s", path, exc_info=True)
                return None
        return reader


def close_pipe_reader(path: str):
    """关闭指定路径的管道读取器"""
    with _pipe_lock:
        reader = _pipe_readers.pop(path, None)
    if reader:
        reader.close()


def close_all_pipe_readers():
    """关闭所有管道读取器（切换时间线/关闭窗口时调用）"""
    with _pipe_lock:
        readers = list(_pipe_readers.values())
        _pipe_readers.clear()
    for r in readers:
        r.close()


def read_frame_with_alpha(path: str, src_sec: float,
                           timeout: float = 3.0) -> np.ndarray | None:
    """用 FFmpeg 提取单帧为 BGRA numpy 数组（保留 alpha 通道）

    每次调用启动一个 FFmpeg 子进程，用 -frames:v 1 限制只输出一帧，
    写入临时文件（非 stdout pipe，避免 Windows 上大帧数据填满 pipe 缓冲区的
    死锁问题）。带 timeout 保护，避免子进程异常时永久阻塞。

    Args:
        path: 视频文件路径
        src_sec: 源视频中的时间点（秒）
        timeout: 子进程超时时间（秒），默认 3 秒

    Returns:
        BGRA numpy 数组 (h, w, 4)，或 None（失败时）
    """
    import tempfile, shutil
    tmp = None
    _src_tmp = None
    try:
        import numpy as np
        ff = _ffmpeg_bin()
        # ── 中文路径处理 ──
        _ff_input = path
        try:
            path.encode('ascii')
        except UnicodeEncodeError:
            _src_fd, _src_tmp = tempfile.mkstemp(suffix='.mov', prefix='cep_src_')
            os.close(_src_fd)
            shutil.copy2(path, _src_tmp)
            _ff_input = _src_tmp
        # 获取视频尺寸（从缓存或 probe）
        if path in _info_cache:
            w, h, fps = _info_cache[path]
        else:
            w, h, fps = _probe_video_info(path, input_path_override=_ff_input)
            if w > 0 and h > 0:
                _info_cache[path] = (w, h, fps)
        if w <= 0 or h <= 0:
            return None
        frame_bytes = w * h * 4
        # 输出到临时文件，避免 pipe 缓冲区死锁（一帧 BGRA 可能达数 MB）
        fd, tmp = tempfile.mkstemp(suffix=".rgba", prefix="cep_alpha_")
        os.close(fd)
        cmd = [
            ff,
            '-y',
            '-ss', f'{max(0.0, src_sec):.4f}',
            '-i', _ff_input,   # 用英文临时副本路径
            '-frames:v', '1',
            '-pix_fmt', 'bgra',
            '-f', 'rawvideo',
            '-an',
            '-loglevel', 'error',
            tmp,
        ]
        # 无 stdout=PIPE → 无管道死锁风险；timeout 后 kill 子进程即可正常返回
        subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
        if not os.path.exists(tmp) or os.path.getsize(tmp) < frame_bytes:
            return None
        with open(tmp, 'rb') as f:
            data = f.read(frame_bytes)
        if len(data) < frame_bytes:
            return None
        frame = np.frombuffer(data, dtype=np.uint8).reshape((h, w, 4))
        return frame
    except subprocess.TimeoutExpired:
        logging.debug("read_frame_with_alpha timeout: %s @ %.3f", path, src_sec)
        return None
    except Exception:
        logging.debug("read_frame_with_alpha failed: %s @ %.3f", path, src_sec,
                      exc_info=True)
        return None
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
        # 清理中文路径的临时副本
        if _src_tmp and os.path.exists(_src_tmp):
            try:
                os.remove(_src_tmp)
            except Exception:
                pass


def decode_alpha_clip_to_file(path: str, timeout: float = 300.0) -> dict | None:
    """整段解码 alpha 视频为 raw BGRA 临时文件，返回帧索引信息。

    与 read_frame_with_alpha（每帧启动子进程）不同，本函数一次性把整个
    视频解码成 .rgba 原始文件。预览线程通过 np.memmap 按帧索引读取，
    完全不阻塞 FFmpeg —— 这是解决「多轨道叠加 alpha 视频时主视频卡死」
    的根本手段（每帧同步启动子进程会拖死单后台取帧线程）。

    Returns:
        dict: {'file': tmp_path, 'w', 'h', 'fps', 'nframes', 'fb'}
        或 None（失败）。
    """
    import tempfile, shutil
    tmp = None
    _src_tmp = None
    try:
        ff = _ffmpeg_bin()
        # ── 中文路径处理：ffmpeg 可能无法直接打开含非ASCII字符的路径
        #    方案：先复制到一个英文临时文件，让 ffmpeg 解码这个副本 ──
        _src_path_for_ffmpeg = path
        try:
            path.encode('ascii')
        except UnicodeEncodeError:
            _src_fd, _src_tmp = tempfile.mkstemp(suffix='.mov', prefix='cep_src_')
            os.close(_src_fd)
            shutil.copy2(path, _src_tmp)
            _src_path_for_ffmpeg = _src_tmp

        w, h, fps = _probe_video_info(path, input_path_override=_src_path_for_ffmpeg)
        if w <= 0 or h <= 0:
            return None
        fb = w * h * 4
        fd, tmp = tempfile.mkstemp(suffix=".rgba", prefix="cep_a_")
        os.close(fd)
        # 整段解码到文件（非 stdout pipe，避免大帧填满管道缓冲区死锁）
        # stderr 用二进制文件捕获（Windows 上 subprocess.PIPE 有 GBK 编码问题）
        _err_fd, _err_path = tempfile.mkstemp(suffix=".txt", prefix="cep_fferr_")
        os.close(_err_fd)
        cmd = [ff, '-y', '-i', _src_path_for_ffmpeg,
               '-f', 'rawvideo', '-pix_fmt', 'bgra',
               '-an', tmp]
        with open(_err_path, 'wb') as _ef:
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=_ef,
                           timeout=timeout, creationflags=_NO_WINDOW)
        if not os.path.exists(tmp) or os.path.getsize(tmp) < fb:
            # 解码失败：打印 ffmpeg 的 stderr 帮助诊断
            import sys as _sys
            _stderr_text = ''
            try:
                with open(_err_path, 'r', encoding='utf-8', errors='replace') as _ef2:
                    _stderr_text = _ef2.read()[:3000]
            except Exception:
                _stderr_text = '(cannot read stderr file)'
            print(f"[ALPHA DECODE FAIL] {os.path.basename(path)}\n"
                  f"  tmp_size={os.path.getsize(tmp) if os.path.exists(tmp) else 'MISSING'}\n"
                  f"  expect={fb} bytes\n"
                  f"  ffmpeg stderr: {_stderr_text}",
                  file=_sys.stderr, flush=True)
            try:
                os.remove(tmp)
            except Exception:
                pass
            try:
                os.remove(_err_path)
            except Exception:
                pass
            return None
        else:
            # 成功：删除临时 stderr 文件
            try:
                os.remove(_err_path)
            except Exception:
                pass
        nframes = os.path.getsize(tmp) // fb
        return {'file': tmp, 'w': w, 'h': h, 'fps': fps,
                'nframes': nframes, 'fb': fb}
    except subprocess.TimeoutExpired:
        logging.debug("decode_alpha_clip_to_file timeout: %s", path)
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
        return None
    except Exception:
        logging.debug("decode_alpha_clip_to_file failed: %s", path, exc_info=True)
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
        return None
    finally:
        # 清理中文路径的临时副本
        if _src_tmp and os.path.exists(_src_tmp):
            try:
                os.remove(_src_tmp)
            except Exception:
                pass


def clear_cache():
    """清除所有缓存（alpha 检测 + 视频信息 + 管道读取器）

    文件被替换或工程切换时调用。
    """
    _alpha_cache.clear()
    _info_cache.clear()
    close_all_pipe_readers()
