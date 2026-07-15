"""
preview_player.py — OpenCV 帧预览播放器
跟随时间线播放头联动，实时显示对应帧
"""
from __future__ import annotations
from core.clip_decoder import DecoderManager
from PyQt6.QtGui import (QPixmap, QImage, QColor, QPainter, QPen, QFont,
                         QFontMetrics, QBrush, QPainterPath, QTextOption)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QSize, QUrl, QRect, QRectF, QPoint
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QApplication,
                             QPushButton, QSizePolicy, QFrame)
from typing import Optional, Tuple
from collections import OrderedDict
import numpy as np
import time
import threading
import logging
import sys
import os
# 强制 ffmpeg 单线程解码：多个 cv2.VideoCapture 共享 ffmpeg 时
# "Assertion fctx->async_lock failed" → 崩溃。必须在 cv2 import 之前设置。
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;1")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}

# Alpha 视频支持（MOV 透明背景等，OpenCV 会丢弃 alpha，需走 FFmpeg）
try:
    from utils.alpha_video import (
        probe_has_alpha, read_frame_with_alpha, close_all_pipe_readers,
    )
    _HAS_ALPHA = True
except Exception:
    _HAS_ALPHA = False
# 只有这些扩展名才值得检测 alpha（MP4 几乎不会有，跳过子进程避免阻塞）
_ALPHA_EXTS = {".mov", ".webm"}
try:
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    _HAS_MEDIA = True
except ImportError:
    _HAS_MEDIA = False

# ─── Windows Qt6 QMediaPlayer 回退：用 soundfile（主进程读元数据）+ subprocess（独立进程播放）───
try:
    import soundfile as _sf_mod
    _HAS_SF = True
except ImportError:
    _HAS_SF = False
try:
    import sounddevice as _sd_mod  # 仅子进程使用（子进程通过 sys.executable 导入）
    _HAS_SD = True
except ImportError:
    _HAS_SD = False


class _AudioOutputSD:
    """QAudioOutput API 兼容层（仅音量控制）"""

    def __init__(self):
        self._volume = 1.0

    def setVolume(self, vol: float):
        self._volume = max(0.0, min(2.0, vol))

    def volume(self) -> float:
        return self._volume


class _AudioPlayerSD:
    """QMediaPlayer API 兼容层，通过 subprocess 在独立进程中播放音频。
    sounddevice 与 PyQt6 在 Windows 上存在 PortAudio 冲突 → 子进程隔离。
    线程安全：play/stop/seek 可从任意线程调用。"""

    class PlaybackState:
        StoppedState = 0
        PlayingState = 1
        PausedState = 2

    def __init__(self):
        self._sf = None            # soundfile.SoundFile（主进程，仅读元数据）
        self._source = None        # 当前文件路径
        self._position_ms = 0      # seek 位置
        self._rate = 1.0           # 播放速率
        self._volume = 1.0
        self._duration_ms = 0      # 限播时长（0=播到文件末尾）
        self._playing = False
        self._proc = None          # subprocess.Popen 句柄
        self._lock = threading.Lock()
        self._audio_output = _AudioOutputSD()
        self._start_monotonic = 0.0  # 音频启动时刻(perf_counter)，用于反推音频主时钟

    def setAudioOutput(self, output):
        self._audio_output = output

    def setSource(self, url):
        path = url.toLocalFile() if hasattr(url, 'toLocalFile') else str(url)
        with self._lock:
            self.stop()
            if self._sf:
                try:
                    self._sf.close()
                except Exception:
                    pass
        self._sf = None
        self._source = None
        try:
            self._sf = _sf_mod.SoundFile(path)
            self._source = path
            self._position_ms = 0
        except Exception:
            logging.debug("AudioPlayerSD: cannot open %s",
                          path, exc_info=True)
            self._sf = None

    def setDuration(self, ms: int):
        """限播时长（毫秒），0=不限，播到文件末尾"""
        self._duration_ms = max(0, ms)

    def setPosition(self, ms: int):
        self._position_ms = max(0, ms)

    def position(self) -> int:
        return self._position_ms

    def setPlaybackRate(self, rate: float):
        self._rate = max(0.25, min(4.0, float(rate)))

    def setVolume(self, vol: float):
        vol = max(0.0, min(2.0, float(vol)))
        self._volume = vol
        if hasattr(self, '_audio_output'):
            self._audio_output.setVolume(vol)

    def _find_ffplay(self) -> str:
        """查找 ffplay.exe（优先项目目录，其次 PATH，最后 _MEIPASS）"""
        import shutil
        candidates = []
        try:
            base = os.path.dirname(os.path.abspath(__file__))
            candidates.append(os.path.join(base, '..', 'ffplay.exe'))
        except Exception:
            pass
        if hasattr(sys, '_MEIPASS'):
            candidates.append(os.path.join(sys._MEIPASS, 'ffplay.exe'))
        try:
            from utils.ffmpeg_utils import get_ffmpeg_path
            candidates.append(os.path.join(
                os.path.dirname(get_ffmpeg_path()), 'ffplay.exe'))
        except Exception:
            pass
        for p in candidates:
            p = os.path.normpath(p)
            if os.path.isfile(p):
                return p
        found = shutil.which('ffplay')
        return found or ''

    def _find_ffmpeg(self) -> str:
        """查找 ffmpeg.exe"""
        try:
            from utils.ffmpeg_utils import get_ffmpeg_path
            p = get_ffmpeg_path()
            if os.path.isfile(p):
                return p
        except Exception:
            pass
        import shutil
        return shutil.which('ffmpeg') or 'ffmpeg.exe'

    def _play_via_ffplay(self, source: str, offset_sec: float, rate: float,
                         vol: float, limit_sec: float,
                         fade_in: float = 0.0, fade_out: float = 0.0,
                         duration_sec: float = 0.0):
        """用 ffplay 播放音频"""
        import subprocess
        ffplay = self._find_ffplay()
        cmd = [ffplay, '-nodisp', '-autoexit', '-loglevel', 'quiet']
        if offset_sec > 0.001:
            cmd.extend(['-ss', str(offset_sec)])
        cmd.extend(['-i', source])
        filters = self._build_audio_filters(rate, vol, fade_in, fade_out, duration_sec)
        if filters:
            cmd.extend(['-af', filters])
        if limit_sec > 0.001:
            cmd.extend(['-t', str(limit_sec)])
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)

    def _play_via_sdl(self, source: str, offset_sec: float, rate: float,
                      vol: float, limit_sec: float,
                      fade_in: float = 0.0, fade_out: float = 0.0,
                      duration_sec: float = 0.0):
        """用 ffmpeg SDL2 输出播放音频（ffplay 不可用时的回退方案）"""
        import subprocess
        ffmpeg = self._find_ffmpeg()
        cmd = [ffmpeg, '-loglevel', 'quiet']
        if offset_sec > 0.001:
            cmd.extend(['-ss', str(offset_sec)])
        cmd.extend(['-i', source])
        filters = self._build_audio_filters(rate, vol, fade_in, fade_out, duration_sec)
        if filters:
            cmd.extend(['-af', filters])
        if limit_sec > 0.001:
            cmd.extend(['-t', str(limit_sec)])
        # SDL2 音频输出（本 ffmpeg 编译时 --enable-sdl2）
        cmd.extend(['-f', 'sdl2', '-'])
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)

    @staticmethod
    def _build_audio_filters(rate: float, vol: float,
                              fade_in: float = 0.0, fade_out: float = 0.0,
                              duration_sec: float = 0.0) -> str:
        """构建 ffmpeg 音频滤镜链"""
        filters = []
        if rate != 1.0:
            if 0.5 <= rate <= 2.0:
                filters.append(f'atempo={rate:.4f}')
            elif rate < 0.5:
                filters.append(f'atempo={max(0.5, rate * 2):.4f}')
                filters.append('atempo=0.5')
            else:
                filters.append('atempo=2.0')
                step = rate / 2.0
                if step != 1.0:
                    filters.append(f'atempo={step:.4f}')
        if vol != 1.0:
            filters.append(f'volume={vol:.4f}')
        if fade_in > 0.001:
            filters.append(f'afade=t=in:st=0:d={fade_in:.4f}')
        if fade_out > 0.001 and duration_sec > 0.001:
            fo_st = max(0.0, duration_sec - fade_out)
            filters.append(f'afade=t=out:st={fo_st:.4f}:d={fade_out:.4f}')
        return ','.join(filters) if filters else ''

    def play(self, fade_in: float = 0.0, fade_out: float = 0.0,
             duration_sec: float = 0.0):
        """启动子进程播放音频（ffplay/ffmpeg 子进程，与 Qt 主进程隔离）"""
        with self._lock:
            if not self._source:
                return
            self.stop()
            self._playing = True
            offset_sec = self._position_ms / 1000.0
            rate = self._rate
            vol = self._audio_output.volume()
            source = self._source
            fi = fade_in
            fo = fade_out
            d = duration_sec if duration_sec > 0.001 else 0.0
            # 不设 -t 限制：由 _check_audio_boundary / play_all_audio → stop_audio 主动终止。
            # 设 -t 会导致同一视频截断后跨边界时 ffplay 提前自毁 → 可听静音间隙。

        try:
            # 优先 ffplay（更轻量）
            if self._find_ffplay():
                self._play_via_ffplay(source, offset_sec, rate, vol, 0.0, fi, fo, d)
            else:
                # 回退：ffmpeg SDL2 输出
                self._play_via_sdl(source, offset_sec, rate, vol, 0.0, fi, fo, d)
            self._start_monotonic = time.perf_counter()  # 记录音频启动时刻（主时钟基准）
            threading.Thread(target=self._monitor_proc, daemon=True).start()
        except Exception:
            self._playing = False
            logging.debug("AudioPlayerSD: spawn failed", exc_info=True)

    def _monitor_proc(self):
        """等待子进程退出，更新播放状态"""
        if self._proc:
            try:
                self._proc.wait()
            except Exception:
                pass
        self._playing = False

    def stop(self):
        self._playing = False
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=1)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    def pause(self):
        self.stop()

    def seamless_position(self, offset_ms: int, dur_ms: int, rate: float = 1.0):
        """无缝切换位置/时长：先启动新 ffplay 进程，再杀旧进程，消除跨越剪辑边界的静音间隙。
        play() 的 stop()-then-start 会产生 20–50ms 可听中断，此方法用 first-start-then-kill 消除。"""
        import time as _time
        old_proc = None
        with self._lock:
            if not self._source:
                return
            self._position_ms = max(0, offset_ms)
            self._duration_ms = max(0, dur_ms)
            self._rate = max(0.25, min(4.0, float(rate)))
            self._playing = True
            old_proc = self._proc
            self._proc = None  # 阻止 stop() 误杀旧进程（马上会手动杀）

        offset_sec = self._position_ms / 1000.0
        source = self._source
        r = self._rate
        vol = self._audio_output.volume()
        # 不设 -t：原因同上（play() 注释）

        try:
            if self._find_ffplay():
                self._play_via_ffplay(source, offset_sec, r, vol, 0.0)
            else:
                self._play_via_sdl(source, offset_sec, r, vol, 0.0)
            self._start_monotonic = time.perf_counter()  # 新进程启动 → 重置主时钟基准
            threading.Thread(target=self._monitor_proc, daemon=True).start()
        except Exception:
            self._playing = False
            self._proc = old_proc  # 回退
            return

        # 等新进程启动（极小延迟，约等于一次音频缓冲区填充）
        _time.sleep(0.015)
        # 杀旧进程
        if old_proc:
            try:
                old_proc.terminate()
                old_proc.wait(timeout=0.5)
            except Exception:
                try:
                    old_proc.kill()
                except Exception:
                    pass

    def playbackState(self):
        if self._playing and self._proc and self._proc.poll() is None:
            return self.PlaybackState.PlayingState
        return self.PlaybackState.StoppedState

    def is_playing(self) -> bool:
        return self.playbackState() == self.PlaybackState.PlayingState

    def audio_clock_sec(self) -> float:
        """反推音频时钟：启动时刻 + (-ss 偏移) + 经过时间 × 速率。
        供视频主时钟对齐，消除 wall-clock 漂移导致的卡顿。
        注意：ffplay 自身有 ~10–50ms 启动延迟，故视频会略微领先音频同一量级，
        对预览编辑器可忽略；后续如需可加校准偏移。"""
        with self._lock:
            if not self._playing or self._proc is None:
                return self._position_ms / 1000.0
            try:
                elapsed = time.perf_counter() - self._start_monotonic
            except Exception:
                return self._position_ms / 1000.0
            return self._position_ms / 1000.0 + elapsed * self._rate

    def isAvailable(self) -> bool:
        return self._sf is not None and self._source is not None

    def deleteLater(self):
        self.stop()
        with self._lock:
            if self._sf:
                try:
                    self._sf.close()
                except Exception:
                    pass
        self._sf = None
        self._source = None

    def mediaStatus(self):
        if self.isAvailable():
            return type('Status', (), {'value': 3})()  # LoadedMedia=3
        return type('Status', (), {'value': 0})()      # NoMedia=0

    def duration(self) -> int:
        """返回音频时长（毫秒）"""
        with self._lock:
            if self._sf is not None:
                try:
                    return int(self._sf.frames / self._sf.samplerate * 1000)
                except Exception:
                    pass
        return 0


# 决定使用哪个播放器类（需要 soundfile + sounddevice 都可用）
if _HAS_SF and _HAS_SD:
    AudioPlayerCls = _AudioPlayerSD
    AudioOutputCls = _AudioOutputSD
    _USING_SD_FALLBACK = True
elif _HAS_MEDIA:
    AudioPlayerCls = QMediaPlayer
    AudioOutputCls = QAudioOutput
    _USING_SD_FALLBACK = False
else:
    AudioPlayerCls = None
    AudioOutputCls = None
    _USING_SD_FALLBACK = False

# 调试开关：设置 True 输出异常详情到 stderr，False 静默
_DEBUG_EXC = False


def _log_exc(msg: str = ""):
    """调试日志：仅在 _DEBUG_EXC=True 时输出异常"""
    if _DEBUG_EXC:
        import traceback
        import sys
        if msg:
            print(f"[PreviewPlayer] {msg}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)


from core.edit_engine import EditTimeline, VideoClip

# 预览转场仅对接「导出也走 xfade」的 11 种常见型，保证「导出=预览」一致；
# 预览支持的转场型集合（与 slideshow_engine.TRANSITIONS 的英文型名一致，共 15 种）。
# 全部在预览中实时渲染；导出路径在 edit_engine 中按型分流（xfade / compositor / 硬切）。
_ALL_TRANSITION_TYPES = {
    "fade", "wipe_left", "wipe_right", "wipe_up", "wipe_down",
    "zoom_push", "spin_push", "zoom_dissolve", "flash_white", "radial",
    "slide_push", "pixelate", "glitch", "circle_open", "curtain",
}


class _PerfProbe:
    """性能探针（已停用）。

    Ctrl+Shift+P 性能浮层与热键已移除。保留此对象仅为兼容历史埋点分支：
    所有埋点均以 `if self._perf.enabled:` 守卫，本对象 enabled 恒为 False，
    故这些分支永不执行、零运行时开销。如需彻底删除埋点代码，全局搜索
    `self._perf` 并清理即可。"""
    enabled = False


class PreviewPlayer(QWidget):
    """
    OpenCV 帧提取预览器
    - 接收时间线 playhead 位置 → 找对应视频片段 → 用 cv2 提取帧 → 显示
    - 线程安全：帧提取在后台线程，UI 更新在主线程
    - 支持在画布上拖拽字幕调整位置
    """
    seek_requested = pyqtSignal(float)  # 用户在此控件拖进度条时发出
    subtitle_pos_changed = pyqtSignal(
        object, float, float)  # (block, pos_x, pos_y)
    # (VideoClip, kind:"video"/"subtitle")
    video_selected = pyqtSignal(object, str)
    pause_requested = pyqtSignal()  # 右键画布时请求暂停播放

    def __init__(self, timeline: EditTimeline, parent=None):
        super().__init__(parent)
        self.tl = timeline
        self._comp = None                      # 惰性创建的 VideoCompositor（预览转场复用）
        self._pending_transition = None        # 背景轨转场状态 (A,B,alpha,tfn,A_end)
        self._trans_cache = None               # A 冻结帧缓存 (cache_key, a_bgr)
        self._transition_fullframe = False     # 当前帧是否为转场合成全画布帧
        self._current_sec: float = 0.0
        self._playing = False                    # 播放状态标志
        self._cap_cache: dict = {}   # {path: cv2.VideoCapture}
        self._cap_lock = threading.Lock()    # 保护 _cap_cache 的锁
        self._pending_frame: Optional[QImage] = None
        # 后台线程存放 RGB 数据，主线程转 QImage
        self._pending_raw: Optional[np.ndarray] = None
        self._pending_raw_w: int = 0  # 与 _pending_raw 配套的宽高
        self._pending_raw_h: int = 0
        self._pending_raw_is_image: bool = False  # True → _pending_raw 实际是图片路径字符串
        # 后台线程存放 overlay raw 数据 [(clip, ("image"|"video", data), w, h)]
        self._pending_raw_overlays: list = []
        self._frame_lock = threading.Lock()
        # ── Phase 1：帧缓存环（解码预读 + 回拖命中，避免重复 decode）──
        # key = round(sec, 3)；value = 完整帧载荷 dict（主帧+叠加+字幕+转场）
        self._payload_ring: "OrderedDict[float, dict]" = OrderedDict()
        self._payload_ring_max = 8            # 环容量（约 0.27s@30fps 的回放窗口）
        # 领先解码帧数；须 >= 解码器 _FILL_AHEAD(12)。
        self._decode_ahead_frames = 12
                                             # 太小(原=1)→切片段时新 clip 窗口未预取→整 tick 走 seek+预填(93ms)
                                             # → 播放头前跳。与解码器预填窗口对齐后 bench 实测切段 tick 93ms→9ms。
        # 帧缓冲对象池：按 (w,h,ch) 复用 numpy 缓冲 + QImage，避免每帧 malloc/.copy()
        self._frame_buf_pool: "OrderedDict[tuple, tuple]" = OrderedDict()
        self._frame_buf_pool_max = 8
        # 持久帧提取线程（daemon），仅 cv2 解码，不创建 QImage/QPixmap
        import queue as _queue_mod
        self._fetch_queue: _queue_mod.Queue = _queue_mod.Queue(maxsize=2)
        self._fetch_thread = threading.Thread(
            target=self._fetch_loop, daemon=True)
        self._fetch_thread.start()

        self._pending_clip = None             # 待渲染的主视频clip
        self._pending_overlays: list = []     # 待渲染的覆盖视频clip列表
        self._pending_subs: list = []         # 待渲染的活跃字幕块列表
        self._pending_cleared: bool = False   # 后台跑完但无片段，需清空画布
        self._clip_src_w = 0
        self._clip_src_h = 0       # 当前视频帧的原始尺寸
        self._canvas_w = 640
        self._canvas_h = 360       # 画布像素尺寸
        self._canvas_cache = None                        # 画布 QImage 复用缓存
        self._canvas_cache_size = None                   # 缓存尺寸 (w, h)
        self._has_alpha = False                         # 当前主轨片段是否含 alpha 通道
        self._alpha_cache: dict = {}                    # probe_has_alpha 结果缓存
        # alpha 视频整段预解码缓存：key=source_path → dict(state,info,thread)
        # 后台线程解码一次到 .rgba 临时文件，预览按帧索引读取，绝不阻塞 FFmpeg
        # 用 source_path 而非 id(clip)，因为 clip 对象可能被重建（id 变化），
        # 同一文件的多个 clip 也可共享解码结果
        self._alpha_clip_cache: dict = {}
        # RLock：_get_alpha_frame 在锁内调用 _ensure_alpha_decoded
        self._alpha_clip_lock = threading.RLock()
        self._alpha_cache_tl_id = -1                     # 时间线变化检测，用于清理缓存
        # 上一帧成功解码的画面缓存（播放时某帧解码失败兜底，避免整画面闪空 / 叠加轨闪黑）
        self._last_main_raw = None          # 主轨成功解码的帧 (np.ndarray)
        self._last_main_clip = None         # 对应的主轨 clip 对象（用于判断是否同一片段）
        self._last_alpha_overlay: dict = {}  # 叠加轨 alpha 视频成功解码帧 {path: (np.ndarray RGBA, w, h)}
        self._ratio_bg_config: dict = {}                 # 按比例存储的背景色
        self._canvas_bg_color = '#000000'                # 背景色
        self._aspect_ratio = None                        # 当前画布比例 ("默认","默认") 等
        self._snap_threshold = 3                         # 视频位置吸附阈值（像素）
        # 按片段缓存的源尺寸（source_path -> (w, h)），用于多轨道命中检测/缩放
        self._clip_src_cache: dict = {}
        self._cache_lock = threading.Lock()  # 保护 _clip_src_cache 的线程安全
        self._seq_state = None               # 顺序读取优化缓存 {(path, track, clip)}
        self._stall_count = 0               # 帧诊断：连续无帧 tick 计数
        self._seq_lock = threading.Lock()    # 保护 _seq_state 的线程安全
        self._stale_caps: set = set()        # cv2 读取超时后标记需重建的 cap
        self._set_seq_state(None)            # 初始化 seq 状态
        # ── 状态机解码器（替代内联 cv2 seek+read）──
        self._decoders = DecoderManager()    # clip.path -> ClipDecoder
        self._decode_state = "playing"       # playing/paused/scrubbing/seek

        # ── 第二段初始化：UI / 定时器 / 音频（原在 _set_seq_state 体内，现移至此）──
        # 素材库画布内预览模式
        self._preview_active = False
        self._preview_path = ""
        self._preview_type = ""
        self._preview_cap = None
        self._preview_fps = 30.0
        self._preview_duration = 0.0
        self._preview_current = 0.0
        self._preview_audio_proc = None
        self._preview_img_pix = None
        self._preview_elapsed_acc = 0.0   # 帧率控制：累积未消费的时间（秒）
        self._preview_last_tick_time = 0.0  # 上次 tick 的时间戳

        self._build_ui()
        # 启用 IME 输入法支持（中文/日文/韩文等组合字符）
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)

        # 定时刷新（主线程渲染）。暂停时自动拉长间隔降低CPU占用
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(8)  # ~120fps 播放/交互时
        self._refresh_timeout_idle = 200    # 空闲时 200ms 低功耗轮询
        self._refresh_timer.timeout.connect(self._safe_flush)
        self._refresh_timer.start()

        # 多路音频播放器：每个 slot 对应一条音源（视频轨/音频轨各独立）
        self._audio_players: list = []
        self._audio_sources: dict = {}   # slot → 当前 setSource 的路径
        self._audio_pending: dict = {}   # slot → (ms, rate) 待 seek 参数
        # 向后兼容：保留 _audio_player 指向第0个 player
        self._audio_player = None
        self._audio_output = None
        # 音频提取缓存：视频文件 → 提取后的 WAV 路径
        self._audio_extract_cache: dict = {}
        self._audio_extract_lock = threading.Lock()
        self._audio_extracting: set = set()  # 正在提取音频的源文件路径（防重复启动）
        import atexit
        atexit.register(self._cleanup_extracted_audio)
        if AudioPlayerCls is not None:
            self._ensure_audio_player(0)  # 至少创建一个

        # ── 交互 / 编辑状态初始化 ──
        self._editing_sub = None
        self._selected_sub = None
        self._selected_video_clip = None
        self._dragging_video = None
        self._resize_handle = None
        self._rotation_active = False
        self._drag_snap_saved = False  # 本次手势是否已保存撤回快照
        self._resize_center_xy = (0, 0)
        self._rotation_start_rot = 0.0
        self._rotation_start_angle = 0.0
        self._rotation_center_xy = (0, 0)
        self._sub_interaction = None
        self._last_frame_image = None
        self._last_raw_img = None
        self._last_raw_overlays = []
        self._profile = False  # 调试开关（已关闭）
        self._current_sec = 0.0
        self._last_good_sec = 0.0
        self._last_decoded_sec = 0.0
        self._payload_diag = None
        self._ring_diag = None
        # 变换缓存：原始帧ID + 变换参数相同则直接复用 QPixmap，避免每帧重算
        self._transform_cache: dict = {}
        self._transform_cache_max = 8
        self._raw_frame_id = 0

        # 内联编辑光标闪烁定时器
        self._edit_blink_timer = QTimer(self)
        self._edit_blink_timer.setInterval(500)
        self._edit_blink_timer.timeout.connect(self._toggle_edit_blink)
        self._edit_flat = ""
        self._edit_cursor = 0
        self._edit_blink = True
        self._edit_cursor_rect = QRect()          # 光标闪烁矩形（画布坐标）
        self._ime_active = False                  # IME 输入法组合中（中文/日文等）
        self._ime_preedit = ""                   # 当前 IME 预编辑文本（拼音，仅显示，不入 _edit_flat）
        self._ime_compose_start = 0              # IME 组合在 _edit_flat 中的起始位置

    _MAX_SRC_CACHE = 200  # _clip_src_cache 最大条目数，超出时淘汰最旧

    def _cache_src_size(self, path: str, size: tuple = None) -> Optional[tuple]:
        """线程安全的 _clip_src_cache 访问。size 传 None 为读取，传 (w,h) 为写入。
        写入时超过 _MAX_SRC_CACHE 条目会淘汰最早的条目。"""
        with self._cache_lock:
            if size is not None:
                self._clip_src_cache[path] = size
                # 容量限制：超出时淘汰最早的条目
                if len(self._clip_src_cache) > self._MAX_SRC_CACHE:
                    oldest_key = next(iter(self._clip_src_cache))
                    self._clip_src_cache.pop(oldest_key, None)
                return size
            return self._clip_src_cache.get(path)

    def _get_seq_state(self):
        """线程安全读取 _seq_state"""
        with self._seq_lock:
            return self._seq_state

    def _set_seq_state(self, val):
        """线程安全写入 _seq_state（仅状态写入，无其他副作用）"""
        with self._seq_lock:
            self._seq_state = val

    def _cv2_seek_read(self, cap, frame_idx: int, source_path: str,
                       timeout: float = 2.0):
        """cv2 cap.set + cap.read，带超时保护。

        Windows 上某些 OpenCV/FFmpeg 组合下 cap.set(POS_FRAMES) 或 cap.read()
        可能无限 hang。用独立线程执行，超时则标记 cap 为 stale 并返回失败。
        """
        import threading as _th
        import cv2 as _cv2
        result = {'ret': False, 'frame': None, 'done': False}

        def _do():
            try:
                _ts = time.perf_counter()
                cap.set(_cv2.CAP_PROP_POS_FRAMES, frame_idx)
                self._perf_seek_acc += time.perf_counter() - _ts
                _tr = time.perf_counter()
                result['ret'], result['frame'] = cap.read()
                self._perf_read_acc += time.perf_counter() - _tr
                self._perf_cv2_acc += (time.perf_counter() - _ts)
            except Exception:
                pass
            result['done'] = True

        t = _th.Thread(target=_do, daemon=True)
        t.start()
        t.join(timeout)
        if result['done']:
            return result['ret'], result['frame']
        # 超时：cap 卡死了，标记需要重建
        self._stale_caps.add(source_path)
        import sys as _sys
        print(f"[PREVIEW] cv2 hung on {os.path.basename(source_path)} "
              f"frame={frame_idx}, marking cap stale",
              file=_sys.stderr, flush=True)
        return False, None

    def _build_ui(self):
        # 防护：避免重复设置 layout（QWidget 只能有一个 layout）
        if self.layout() is not None:
            return
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 标题
        title = QLabel("预览")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFixedHeight(24)
        title.setStyleSheet(
            "background:#1a1a1a; color:#888; font-size:11px; border-bottom:1px solid #333;"
        )

        # 画面容器（手动定位 screen 和 inline editor，不使用布局）
        screen_container = QWidget()
        screen_container.setStyleSheet("background:#1e1e1e;")  # 窗口底色（与画布区分）
        screen_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        screen_container.installEventFilter(self)  # 监听 resize 以重定位 _screen

        # child of container, positioned manually
        self._screen = QLabel(screen_container)
        self._screen.installEventFilter(self)
        self._screen.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._screen.setStyleSheet("background:transparent;")
        self._screen.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._screen.setMouseTracking(True)
        self._screen.setAttribute(Qt.WidgetAttribute.WA_Hover)  # 确保接收 hover 事件
        self._show_placeholder()

        self._screen_container = screen_container

        # 性能探针（已停用：Ctrl+Shift+P 浮层已移除，enabled 恒 False，零开销）
        self._perf = _PerfProbe()

        # 时间码
        self._timecode = QLabel("00:00.00")
        self._timecode.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._timecode.setFixedHeight(22)
        self._timecode.setStyleSheet(
            "background:#1a1a1a; color:#00eaff; font-family:'Courier New'; "
            "font-size:13px; border-top:1px solid #333;"
        )

        root.addWidget(title)
        root.addWidget(screen_container, 1)
        root.addWidget(self._timecode)

    def _show_placeholder(self):
        """无视频时显示占位画布（用配置的背景色/虚化）"""
        cw = max(getattr(self, '_canvas_w', 0)
                 or self._screen.width() or 320, 320)
        ch = max(getattr(self, '_canvas_h', 0)
                 or self._screen.height() or 180, 180)
        img = self._alloc_canvas(cw, ch)
        painter = QPainter(img)
        # 文字颜色按背景亮度自适应
        bg = getattr(self, '_canvas_bg_color', '#000000') or '#000000'
        bg_c = QColor(bg)
        text_lum = bg_c.red() * 0.299 + bg_c.green() * 0.587 + bg_c.blue() * 0.114
        text_col = QColor(
            "#cccccc") if text_lum < 128 else QColor("#333333")
        painter.setPen(QPen(text_col))
        painter.setFont(QFont("Microsoft YaHei", 12))
        painter.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, "拖入视频预览")
        painter.end()
        self._screen.setPixmap(QPixmap.fromImage(img))

    # ─── 多路音频播放器管理 ───
    def _ensure_audio_player(self, slot: int):
        """确保 slot 号播放器存在，返回 (player, output) 或 (None, None)。
        最大保留 16 个播放器，超出时复用最早的（避免内存泄漏）。
        安全回收：先停止→卸载源→延迟删除，防止 QMediaPlayer 内部线程未退出时报 Destroyed 错误。"""
        if AudioPlayerCls is None:
            return None, None
        MAX_PLAYERS = 16
        while len(self._audio_players) <= slot:
            if len(self._audio_players) >= MAX_PLAYERS:
                # 回收最早的播放器：停止→卸载→延迟删除
                old = self._audio_players.pop(0)
                if old[0] is not None:
                    try:
                        old[0].stop()
                        # QMediaPlayer 需要先卸载源来触发内部线程关闭
                        if hasattr(old[0], 'setSource'):
                            old[0].setSource(QUrl())
                        old[0].deleteLater()
                    except Exception:
                        pass
            try:
                out = AudioOutputCls()
                out.setVolume(1.0)
                player = AudioPlayerCls()
                player.setAudioOutput(out)
                self._audio_players.append((player, out))
            except Exception:
                import traceback
                traceback.print_exc()
                self._audio_players.append((None, None))
                break
        pair = self._audio_players[slot] if slot < len(
            self._audio_players) else (None, None)
        # 更新向后兼容指针
        if slot == 0:
            self._audio_player = pair[0]
            self._audio_output = pair[1]
        return pair

    # ─── 视频文件音频预提取 ───
    VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi",
        ".mkv", ".wmv", ".flv", ".webm", ".m4v", ".ts"}
    # libsndfile 不支持的音频格式，需 FFmpeg 转码为 WAV
    _NEED_TRANSCODE_AUDIO = {".mp3", ".m4a", ".aac", ".wma", ".ac3"}

    def _ensure_audio_for_video(self, source_path: str) -> str:
        """对于视频文件或不支持的音频格式，用 FFmpeg 提取/转码为 WAV，缓存并返回提取后的路径。
        对于 soundfile 原生支持的音频格式（WAV/FLAC/OGG等），直接返回原路径。

        首次提取在后台线程进行（不阻塞主线程），返回 "" 表示音频尚未就绪；
        下次 play_all_audio（每 ~200ms 调用一次）会自动命中缓存。
        """
        ext = os.path.splitext(source_path)[1].lower()
        is_video = ext in self.VIDEO_EXTENSIONS
        is_audio_need_transcode = ext in self._NEED_TRANSCODE_AUDIO

        if not is_video and not is_audio_need_transcode:
            return source_path  # soundfile 原生支持，无需转码

        # 快速路径：已有缓存（成功或失败都直接返回，不重试以避免死循环）
        with self._audio_extract_lock:
            cached = self._audio_extract_cache.get(source_path)
            if cached is not None:
                return cached  # 可能为 ""（之前失败），返回空让调用方跳过音频
            # 正在提取中 → 不重复启动
            if source_path in self._audio_extracting:
                return ""

        # 慢路径：启动后台线程提取音频，本次返回空（稍后命中缓存）
        self._audio_extracting.add(source_path)
        t = threading.Thread(target=self._extract_audio_worker,
                             args=(source_path, is_video),
                             daemon=True)
        t.start()
        return ""

    def _get_ffmpeg(self) -> str:
        try:
            from utils.ffmpeg_utils import get_ffmpeg_path
            return get_ffmpeg_path()
        except Exception:
            return "ffmpeg"

    def _extract_audio_worker(self, source_path: str, is_video: bool):
        """后台线程：ffmpeg 提取音频为 WAV（不阻塞主线程）。

        超时根据文件大小动态计算：~100MB/min 的提取速度，
        最小 30s，最大 600s。避免短文件等太久、长文件超时截断。
        失败后不重试（_audio_extract_cache 永久记录失败）。
        """
        import subprocess
        import tempfile
        wav_path = ""
        try:
            # 动态超时：按文件大小估算（保守估计 60MB/min 提取速度）
            try:
                file_mb = os.path.getsize(source_path) / (1024 * 1024)
                timeout = max(30, min(600, int(file_mb / 60 * 60)))
            except Exception:
                timeout = 120
            tmp = tempfile.NamedTemporaryFile(
                suffix=".wav", prefix="va_", delete=False)
            wav_path = tmp.name
            tmp.close()
            ffmpeg = self._get_ffmpeg()
            cmd = [
                ffmpeg,
                "-i", source_path,
                "-acodec", "pcm_s16le",
                "-ar", "44100",
                "-ac", "2",
                "-y", wav_path,
            ]
            if is_video:
                cmd.insert(3, "-vn")
            result = subprocess.run(cmd, capture_output=True, timeout=timeout)
            if result.returncode != 0:
                stderr = result.stderr.decode(errors="replace")[:200]
                logging.debug("FFmpeg audio extract failed rc=%d: %s",
                              result.returncode, stderr)
            if os.path.exists(wav_path) and os.path.getsize(wav_path) > 44:
                with self._audio_extract_lock:
                    self._audio_extract_cache[source_path] = wav_path
                    self._audio_extracting.discard(source_path)
                return  # 成功，保留 wav_path
            else:
                # 无音频流或提取失败 → 清理
                try:
                    os.remove(wav_path)
                except Exception:
                    pass
                wav_path = ""
        except Exception:
            logging.debug("_extract_audio_worker failed for %s",
                          source_path, exc_info=True)
            if wav_path and os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except Exception:
                    pass
            # 记录失败
        with self._audio_extract_lock:
            self._audio_extract_cache[source_path] = ""
            self._audio_extracting.discard(source_path)

    # ─── 音频播放控制 ───
    def play_audio(self, source_path: str, offset_sec: float, slot: int = 0, rate: float = 1.0, volume: float = 1.0, duration_sec: float = 0.0,
                   fade_in: float = 0.0, fade_out: float = 0.0):
        """在指定 slot（音轨编号）播放音频，从 offset_sec 处开始。
        支持 sounddevice（Windows）和 QMediaPlayer（其他平台）双后端。
        duration_sec=0 表示播放到文件末尾；>0 则限播指定秒数的源音频。"""
        if not os.path.exists(source_path):
            logging.debug("play_audio: source not found %s", source_path)
            return
        player, _ = self._ensure_audio_player(slot)
        if player is None:
            logging.debug("play_audio: no player available (slot=%d)", slot)
            return

        # 视频文件 → 提取音频为 WAV
        actual_source = self._ensure_audio_for_video(source_path)
        if not actual_source:
            logging.debug("play_audio: no audio stream in %s",
                          os.path.basename(source_path))
            return  # 视频无音频流或提取失败，跳过

        # 应用音量（clip.volume 范围 0~2，限制到播放器 0~1）
        vol = max(0.0, min(2.0, float(volume)))
        try:
            player.setVolume(vol)
        except Exception:
            pass

        try:
            ms = int(offset_sec * 1000)
            dur_ms = int(duration_sec * 1000) if duration_sec > 0 else 0
            last_src = self._audio_sources.get(slot)

            if last_src == actual_source:
                # 同文件（如视频截断后的相邻片段）—
                # ffplay 子进程正在播放同一 WAV，只需更新时长即可连续播放。
                # 不重启进程：单进程连续输出 PCM 流天然无间隙。
                # 兜底：若 ffplay 已意外退出则重启。
                if player.playbackState() != player.__class__.PlaybackState.PlayingState:
                    player.setDuration(dur_ms)
                    player.setPosition(ms)
                    if rate != 1.0:
                        player.setPlaybackRate(rate)
                    player.play(fade_in, fade_out, duration_sec)
                else:
                    player.setDuration(dur_ms + 500)
                    player.setPosition(ms)
                    if rate != 1.0:
                        player.setPlaybackRate(rate)
                # 已在播放 → 不调 play/seamless_position，避免重启引入间隙
            else:
                # 不同文件 — 也尝试无缝切换：先设好新源参数，启动新进程，
                # 再杀旧进程。只有在旧进程正在播放时才走无缝路径。
                if player.playbackState() == player.__class__.PlaybackState.PlayingState:
                    player.stop()  # 先停旧源（此处仍有微小间隙，但跨文件不可避）
                player.setDuration(dur_ms)
                player.setSource(QUrl.fromLocalFile(actual_source))
                self._audio_sources[slot] = actual_source
                if rate != 1.0:
                    player.setPlaybackRate(rate)
                player.setPosition(ms)
                player.play(fade_in, fade_out, duration_sec)
                # 异步加载后延迟再设一次位置

                def _retry_seek(p=player, m=ms):
                    try:
                        if p.isAvailable():
                            p.setPosition(m)
                    except Exception:
                        pass
                QTimer.singleShot(100, _retry_seek)
        except Exception as e:
            logging.debug("play_audio failed slot=%d path=%s: %s",
                          slot, source_path, e, exc_info=True)

    def _cleanup_extracted_audio(self):
        """进程退出时清理所有提取的临时音频文件"""
        with self._audio_extract_lock:
            paths = list(self._audio_extract_cache.values())
            self._audio_extract_cache.clear()
        for p in paths:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

    def stop_audio(self, slot: int = -1):
        """停止音频。slot=-1 停止所有，否则停止指定 slot"""
        if slot == -1:
            for player, _ in self._audio_players:
                if player:
                    try:
                        player.stop()
                    except Exception: import traceback; traceback.print_exc()
        else:
            if slot < len(self._audio_players):
                player, _ = self._audio_players[slot]
                if player:
                    try:
                        player.stop()
                    except Exception: import traceback; traceback.print_exc()

    def clear_file_cache(self, path: str):
        """清除指定文件的缓存，当素材库删除文件时调用"""
        with self._cap_lock:
            if path in self._cap_cache:
                try:
                    cap = self._cap_cache.pop(path)
                    if cap:
                        cap.release()
                except Exception:
                    import traceback
                    traceback.print_exc()
        # 同时清除 clip_src_cache 中该文件的尺寸缓存
        with self._cache_lock:
            self._clip_src_cache.pop(path, None)
        # 清除音频提取缓存并删除临时文件
        with self._audio_extract_lock:
            wav = self._audio_extract_cache.pop(path, None)
        if wav and os.path.exists(wav):
            try:
                os.remove(wav)
            except Exception:
                pass
            # 停止播放
        self.stop_audio()
        # 如果当前正在显示该文件，清空显示
        if hasattr(self, '_current_sec'):
            try:
                self._async_fetch(self._current_sec)
            except Exception:
                import traceback
                traceback.print_exc()

    def play_all_audio(self, sec: float):
        """
        在当前播放头位置，同时播放所有非静音音频轨道。
        视频轨的原声通过 slot 0..N-1，音频轨通过 slot N..N+M-1。
        每个轨道的 slot 由 track index 固定，静音不改序号避免串扰。
        """
        # ── 第一阶段：收集需要播放的 slot → (source, src_sec, rate, volume, duration, fade_in, fade_out) ──
        # active_slots: {slot: (source_path, src_sec, rate, volume, duration_sec, fade_in, fade_out)}
        # duration_sec = 源音频剩余秒数，0=不限（播到文件末尾）
        active_slots: dict = {}
        n_video = len(self.tl.video_tracks)
        n_audio = len(self.tl.audio_tracks)

        # 视频轨音频（slot = track_index）
        for i in range(n_video):
            track = self.tl.video_tracks[i]
            info = (self.tl.video_track_info[i]
                    if hasattr(self.tl, "video_track_info") and i < len(self.tl.video_track_info)
                    else None)
            track_muted = info.muted if info else False
            if track_muted:
                continue
            for c in track:
                if not getattr(c, "visible", True):
                    continue
                if c.timeline_start <= sec < c.timeline_end and not c.mute:
                    src_sec = c.trim_start + (sec - c.timeline_start) * c.speed
                    if os.path.exists(c.source_path):
                        dur = max(0.0, c.trim_end - src_sec)
                        active_slots[i] = (
                            c.source_path, src_sec, c.speed, c.volume, dur, 0.0, 0.0)
                    break  # 每条轨道最多一个片段同时发声

        # 音频轨（slot = n_video + track_index）
        for i in range(n_audio):
            track = self.tl.audio_tracks[i]
            info = (self.tl.audio_track_info[i]
                    if hasattr(self.tl, "audio_track_info") and i < len(self.tl.audio_track_info)
                    else None)
            track_muted = info.muted if info else False
            slot = n_video + i
            if track_muted:
                continue
            for c in track:
                if not getattr(c, "visible", True):
                    continue
                if c.timeline_start <= sec < c.timeline_end and not c.mute:
                    src_sec = c.trim_start + (sec - c.timeline_start)
                    if os.path.exists(c.source_path):
                        dur = max(0.0, c.trim_end - src_sec)
                        # 淡入仅在片段起始处生效
                        fi = getattr(c, 'fade_in', 0) or 0
                        if sec - c.timeline_start > 0.05:
                            fi = 0.0
                        # 淡出仅在剩余时长接近片段末尾时生效
                        fo = getattr(c, 'fade_out', 0) or 0
                        remaining = c.timeline_end - sec
                        if remaining > fo + 0.05:
                            fo = 0.0
                        active_slots[slot] = (
                            c.source_path, src_sec, 1.0, c.volume, dur, fi, fo)
                    break

        # ── 第二阶段：播放活跃 slot，停止非活跃 slot ──
        total_slots = n_video + n_audio
        max_slot = max(total_slots, max(active_slots.keys()) + \
                       1) if active_slots else total_slots
        for s in range(max_slot):
            if s in active_slots:
                src, offset, rate, vol, dur, fi, fo = active_slots[s]
                self.play_audio(src, offset, s, rate=rate,
                                volume=vol, duration_sec=dur,
                                fade_in=fi, fade_out=fo)
            else:
                self.stop_audio(s)

        # 停止超出最大范围的 slot（防止旧的播放器泄漏）
        for s in range(max_slot, len(self._audio_players)):
            self.stop_audio(s)

    def seek_audio(self, sec: float):
        """播放头跳转时同步所有音频（重新调用 play_all_audio）"""
        self.play_all_audio(sec)

    def set_playing(self, on: bool):
        """由 TimelineWidget 设置播放状态，播放中 seek() 跳过音频同步"""
        if on:
            self._hide_sub_editor(save=True)
        self._playing = on

    def set_decode_state(self, state: str):
        """驱动解码器状态机：playing / paused / scrubbing / seek。
        播放时连续 read，跳转/拖拽时单次 seek，避免后台无效解码与每帧随机 seek。"""
        if state not in ("playing", "paused", "scrubbing", "seek"):
            return
        self._decode_state = state
        if getattr(self, '_decoders', None) is not None:
            self._decoders.set_state(state)

    def master_clock_sec(self):
        """返回主时钟（音频时钟）。
        只用 slot 0（主视频轨原声）——若它不播放（如 V 键隐藏、静音），
        返回 None 由 _tick_play 回退 wall-clock 从 canvas.playhead 连续推进。
        绝不遍历其他 slot：每个 slot 有独立 _position_ms（该轨起点偏移），
        切换会导致时钟跳到不同轨的时间基 → 播放头跳几秒（V 键跳、第二轨开始跳）。"""
        players = getattr(self, '_audio_players', [])
        if len(players) > 0:
            p0 = players[0][0]
            if p0 is not None and p0.is_playing():
                return p0.audio_clock_sec()
        return None

    # ─── 主接口：接收播放头位置 ───
    def seek(self, sec: float, force: bool = False):
        # 同一位置 seek（如字幕属性 slider 变更）：保留帧缓存，仅重绘叠加层，避免闪黑
        same_pos = (abs(sec - self._current_sec) < 0.001
                    and self._last_frame_image is not None)
        self._current_sec = sec
        if same_pos and not force:
            self._recompose_overlays()
            return
        self._last_frame_image = None  # 清除帧缓存，确保 seek 后显示正确画面
        self._last_raw_img = None       # 同步清除原始帧缓存
        self._last_raw_overlays = []
        if self._preview_active:
            self.stop_preview()
        m = int(sec // 60)
        s = sec % 60
        self._timecode.setText(f"{m:02d}:{s:05.2f}")
        # 异步提取帧（播放中领先解码 ahead 帧，消除卡顿）
        ahead = self._decode_ahead_frames if (
            self._playing and not force) else 0
        self._async_fetch(sec, ahead=ahead)
        # 如果选中的字幕已不在当前时间范围，取消选中
        if self._selected_sub is not None:
            b = self._selected_sub
            if not (b.timeline_start <= sec < b.timeline_end):
                self._selected_sub = None
                self._sub_interaction = None
                self._hide_sub_editor(save=True)

    def _async_fetch(self, sec: float, ahead: int = 0):
        """投递帧提取请求到后台线程，不阻塞主线程。
        ahead>0 时携带领先解码帧数（decode-ahead）。"""
        import queue as _queue_mod
        item = (sec, ahead) if ahead > 0 else sec
        try:
            self._fetch_queue.put_nowait(item)
        except _queue_mod.Full:
            try:
                self._fetch_queue.get_nowait()
                self._fetch_queue.put_nowait(item)
            except _queue_mod.Empty:
                pass

    # ─────────────────────────────────────────────────────────────────────
    # Alpha 视频整段预解码缓存（解决多轨道叠加时主视频卡死）
    # ─────────────────────────────────────────────────────────────────────
    def _ensure_alpha_decoded(self, clip, retry_count: int = 0):
        """（线程安全）若 clip 尚未解码，启动后台线程整段解码。retry_count 超过 3 不再重试"""
        cid = clip.source_path
        with self._alpha_clip_lock:
            if cid in self._alpha_clip_cache:
                return
            # 缓存上限 6 个，超出则淘汰最旧（释放临时文件）
            if len(self._alpha_clip_cache) >= 6:
                self._evict_oldest_alpha()
            entry = {'state': 'pending', 'info': None,
                'thread': None, 'retry_count': retry_count}
            self._alpha_clip_cache[cid] = entry
        path = clip.source_path

        def _worker():
            try:
                from utils.alpha_video import decode_alpha_clip_to_file
                import sys as _sys
                # ── 诊断：打印文件路径和是否存在 ──
                _abspath = os.path.abspath(path)
                _exists = os.path.exists(path)
                print(f"[ALPHA] start decode {os.path.basename(path)} | "
                      f"path={_abspath} | exists={_exists}",
                      file=_sys.stderr, flush=True)
                res = decode_alpha_clip_to_file(path, timeout=300)
                with self._alpha_clip_lock:
                    e = self._alpha_clip_cache.get(cid)
                    if e is not None:
                        if res is not None:
                            e['state'] = 'ready'
                            e['info'] = res
                            print(f"[ALPHA] decode OK {os.path.basename(path)}: "
                                  f"{res['w']}x{res['h']} {res['nframes']} frames",
                                  file=_sys.stderr, flush=True)
                            # 解码完成 → 回到主线程刷新当前帧，使透明通道立即生效，
                            # 无需用户手动拖动播放头（暂停时 _refresh_timer 不会
                            # 自动重取 MOV 帧，画面会停在解码前的旧状态）。
                            _sec = self._current_sec
                            from PyQt6.QtCore import QTimer
                            QTimer.singleShot(
                                0, lambda sec=_sec: self._async_fetch(sec))
                        else:
                            e['state'] = 'failed'
                            print(f"[ALPHA] decode FAILED {os.path.basename(path)}",
                                  file=_sys.stderr, flush=True)
            except Exception:
                with self._alpha_clip_lock:
                    e = self._alpha_clip_cache.get(cid)
                    if e is not None:
                        e['state'] = 'failed'

        t = threading.Thread(target=_worker, daemon=True)
        entry['thread'] = t
        t.start()

    def _evict_oldest_alpha(self):
        """淘汰最旧解码缓存并删除其临时文件。"""
        if not self._alpha_clip_cache:
            return
        oldest = next(iter(self._alpha_clip_cache))
        e = self._alpha_clip_cache.pop(oldest)
        info = e.get('info')
        if info and info.get('file') and os.path.exists(info['file']):
            try:
                os.remove(info['file'])
            except Exception:
                pass

    def _clear_alpha_cache(self):
        """清空所有 alpha 解码缓存并删除临时文件（切时间线/关闭时调用）。"""
        with self._alpha_clip_lock:
            items = list(self._alpha_clip_cache.items())
            self._alpha_clip_cache.clear()
        # 切换时间线：清空上一帧兜底缓存，避免复用已失效片段的画面
        self._last_main_raw = None
        self._last_main_clip = None
        self._last_alpha_overlay = {}
        # 关闭叠加轨 alpha 视频的持久管道读取器（避免 ffmpeg 进程泄漏）
        if _HAS_ALPHA:
            try:
                close_all_pipe_readers()
            except Exception:
                pass
        for _cid, e in items:
            info = e.get('info')
            if info and info.get('file') and os.path.exists(info['file']):
                try:
                    os.remove(info['file'])
                except Exception:
                    pass

    def _get_alpha_frame(self, clip, src_sec):
        """返回 clip 在 src_sec 处的 BGRA 帧（来自整段预解码缓存）。

        缓存未就绪返回 None —— 调用方据此跳过/回退，**绝不阻塞**。
        失败后自动重试（清除缓存条目，重新触发解码）。
        """
        cid = clip.source_path
        with self._alpha_clip_lock:
            entry = self._alpha_clip_cache.get(cid)
            if entry is None:
                self._ensure_alpha_decoded(clip)
                return None
            state = entry.get('state')
            if state == 'failed':
                # 解码失败 → 检查重试次数，超过上限则永久放弃（避免无限重试）
                _retry_count = entry.get('retry_count', 0) + 1
                if _retry_count > 3:
                    import sys as _sys
                    print(f"[ALPHA] decode permanently failed for {os.path.basename(clip.source_path)}, "
                          f"stopped retrying after {_retry_count} attempts",
                          file=_sys.stderr, flush=True)
                    entry['state'] = 'permanent_fail'
                    return None
                # 重新触发解码
                del self._alpha_clip_cache[cid]
                self._ensure_alpha_decoded(clip, retry_count=_retry_count)
                return None
            if state != 'ready' or entry.get('info') is None:
                return None
            info = entry['info']
        try:
            fps = info['fps'] or 30.0
            nframes = info['nframes']
            # 用 int()（向下取整）而非 round()：与 MP4 主轨取帧一致。
            # round() 会在半帧边界（如 100.5）随播放头时钟抖动来回跳变，
            # 导致 MOV 在第 N / N+1 帧间快速闪烁（MP4 用 int() 故不闪）。
            fi = int(src_sec * fps)
            if fi < 0:
                fi = 0
            if fi >= nframes:
                fi = nframes - 1
            off = fi * info['fb']
            # 用 open+seek+read 代替 np.memmap。
            # memmap 每帧创建新文件映射，Windows 上不主动释放句柄，
            # 30fps 几十秒即耗尽系统句柄 → cv2 也无法打开主轨 MP4
            # → 表现为"叠加 MOV 后两个视频都卡死，删掉 MOV 后 MP4 也不动"。
            with open(info['file'], 'rb') as f:
                f.seek(off)
                data = f.read(info['fb'])
            if len(data) < info['fb']:
                return None
            return np.frombuffer(data, dtype=np.uint8).reshape(
                (info['h'], info['w'], 4)).copy()
        except Exception:
            logging.debug("alpha frame read failed", exc_info=True)
            return None

    def _fetch_alpha_overlay_frame(self, oc, src_o):
        """取叠加轨 alpha 视频在 src_o 处的 RGBA 帧（含 alpha，绝不失透）。

        仅从整段预解码缓存读取；解码未完成或瞬时读取失败时复用上一帧
        成功的 alpha 帧（不闪）。无任何可用帧时返回 None（调用方跳过），
        绝不回退 cv2（OpenCV 丢 alpha → 透明区变黑框，与透明帧交替=闪烁），
        也绝不使用持久管道读取器（其帧跳过/seek 逻辑在高频调用下会返回
        错乱时间点帧，与正常帧交替=疯狂闪烁）。

        解码等待期（数秒）内叠加轨暂不显示；解码完成后由
        _ensure_alpha_decoded 的 QTimer.singleShot 自动刷新当前帧，
        透明通道立即生效，无需手动拖动播放头。
        """
        if not (_HAS_ALPHA and os.path.splitext(oc.source_path)[1].lower() in _ALPHA_EXTS):
            return "USE_CV2"  # 非 alpha 视频，调用方走原 cv2 路径
        import cv2
        # 1) 整段预解码文件缓存（解码完成后最快、零阻塞、绝对稳定）
        bgra_o = self._get_alpha_frame(oc, src_o)
        if bgra_o is not None:
            o_rgb = cv2.cvtColor(bgra_o, cv2.COLOR_BGRA2RGBA)
            h, w = o_rgb.shape[:2]
            self._cache_src_size(oc.source_path, (w, h))
            self._last_alpha_overlay[oc.source_path] = (o_rgb, w, h)
            return o_rgb, w, h
        # 2) 解码未完成 / 瞬时读取失败 → 复用上一帧成功的 alpha 帧（不闪）
        _last_ov = self._last_alpha_overlay.get(oc.source_path)
        if _last_ov is not None:
            return _last_ov
        # 3) 真的无可用帧 → 返回 None（调用方跳过，绝不画不透明黑框）
        return None

    def _get_compositor(self):
        """惰性创建 / 复用 VideoCompositor（预览转场复用其 _render_clip_offscreen + apply_transition）。
        时间线切换或画布尺寸变化时自动重建。
        分辨率取预览画布尺寸（_canvas_w/_canvas_h，源自项目 cfg["resolution"]），
        因 EditTimeline 本身不带 W/H/fps。"""
        comp = getattr(self, '_comp', None)
        tl = getattr(self, 'tl', None)
        w = getattr(self, '_canvas_w', 0) or 1920
        h = getattr(self, '_canvas_h', 0) or 1080
        if comp is not None and (getattr(comp, 'tl', None) is not tl
                                 or comp.W != w or comp.H != h):
            comp = None  # 时间线或画布尺寸变化 → 重建
        if comp is None and tl is not None:
            try:
                from core.compositor import VideoCompositor
                comp = VideoCompositor(tl, (w, h), 30.0)
                self._comp = comp
            except Exception:
                logging.debug("compositor 创建失败", exc_info=True)
                self._comp = None
        return getattr(self, '_comp', None)

    def _bg_transition_state(self, main_clip, sec):
        """背景轨（track 0）转场检测：若当前帧在 main_clip 的 incoming 转场窗口内，
        返回纯元数据 (A_clip, B_clip, alpha, tfn, A_end)，帧提取由主线程 _flush_frame 完成。
        不在此调用 _extract_frame（避免后台线程与主线程争用 compositor 的 _cap_cache）。"""
        if main_clip is None:
            return None
        tracks = getattr(self.tl, 'video_tracks', []) or []
        if not tracks:
            return None
        track0 = tracks[0]
        # 找 main_clip 在 track0 中的前驱 A
        A_prev = None
        for c in track0:
            if c is main_clip:
                break
            A_prev = c
        if A_prev is None:
            return None
        ot = getattr(A_prev, 'out_transition', None)
        if not (ot and ot.get('type')):
            return None
        if ot['type'] not in _ALL_TRANSITION_TYPES:
            return None
        d = max(0.0, float(ot.get('duration', 0.5)))
        if d <= 0:
            return None
        A_end = A_prev.timeline_start + \
            (A_prev.trim_end - A_prev.trim_start) / max(A_prev.speed, 0.01)
        if main_clip.timeline_start > A_end + d + 1e-3:
            return None
        if not (A_end - 1e-6 <= sec <= A_end + d + 1e-6):
            return None
        alpha = (sec - A_end) / d
        alpha = max(0.0, min(1.0, alpha))
        return (A_prev, main_clip, alpha, ot['type'], A_end)

    def _fetch_frame(self, sec: float, ahead: int = 0):
        """后台线程入口（由 _fetch_loop 调用）：
        1. 计算 sec 处完整载荷并写入 _pending_raw*（write_pending=True）；
        2. 把该载荷按 round(sec,3) 存入帧缓存环；
        3. 若 ahead>0 且正在播放，领先解码 [sec+1/fps .. sec+ahead/fps] 窗口帧，
           仅入环（write_pending=False），实现 decode-ahead，消除播放卡顿。"""
        payload = self._compute_payload(sec, write_pending=True)
        if payload is not None and payload.get('raw') is not None:
            with self._frame_lock:
                self._payload_ring[self._ring_key(sec)] = payload
                while len(self._payload_ring) > self._payload_ring_max:
                    self._payload_ring.popitem(last=False)
        # decode-ahead：仅入环，不写 _pending_raw，避免污染主消费路径
        if ahead and self._playing and payload is not None and payload.get('raw') is not None:
            fps = getattr(self.tl, 'fps', 30) or 30
            for i in range(1, int(ahead) + 1):
                s = sec + i / fps
                p2 = self._compute_payload(s, write_pending=False)
                if p2 is not None and p2.get('raw') is not None:
                    with self._frame_lock:
                        self._payload_ring[self._ring_key(s)] = p2
                        while len(self._payload_ring) > self._payload_ring_max:
                            self._payload_ring.popitem(last=False)

    def _ring_key(self, sec: float) -> float:
        """帧缓存环的 key：毫秒精度，与 _flush_frame 用 _current_sec 查环对齐。"""
        return round(sec, 3)

    def _fetch_loop(self):
        """后台线程：从队列取请求，cv2 解码存入 _pending_raw。
        主线程 _refresh_timer → _flush_frame 拾取并转换为 QImage 渲染。"""
        import queue as _queue_mod
        import traceback
        while True:
            try:
                item = self._fetch_queue.get()
                if item is None:
                    break
                if isinstance(item, tuple):
                    sec, ahead = item
                else:
                    sec, ahead = item, 0
                _dt0 = time.perf_counter() if self._perf.enabled else 0
                self._fetch_frame(sec, ahead)
                if self._perf.enabled:
                    _dt = time.perf_counter() - _dt0
                    _a = 0.2
                    self._perf.ema_decode = self._perf.ema_decode * \
                        (1 - _a) + _dt * _a
                    # payload = 后台帧生产总耗时 - 实际 cv2 解码耗时 = 纯 Prepare 成本
                    # （时间线查询 + 字幕/叠加轨收集 + 转场状态检测，不含像素解码）
                    _cv2 = getattr(self, '_perf_cv2_acc', 0.0)
                    if _cv2 <= 0:
                        _cv2 = (getattr(self, '_perf_seek_acc', 0.0)
                                + getattr(self, '_perf_read_acc', 0.0)
                                + getattr(self, '_perf_cvt_acc', 0.0))
                    _prepare = _dt - _cv2
                    if _prepare < 0:
                        _prepare = 0.0
                    self._perf.ema_payload = self._perf.ema_payload * \
                        (1 - _a) + _prepare * _a
            except Exception:
                traceback.print_exc()

    def _compute_payload(self, sec: float, write_pending: bool = True):
        """后台线程中运行：仅 cv2 解码 + 收集字幕，不创建任何 QImage/QPixmap。
        返回完整帧载荷 dict（raw/clip/subs/ovs/...）；write_pending=True 时同时写入
        _pending_raw*（供现有消费路径兼容）。decode-ahead 窗口帧用 write_pending=False
        仅入帧缓存环。"""
        try:
            import cv2
            self._perf_cv2_acc = 0.0  # 仅计 cv2 解码耗时，供 payload = 总耗时 - cv2
            self._perf_seek_acc = 0.0
            self._perf_read_acc = 0.0
            self._perf_cvt_acc = 0.0
            self._pending_transition_val = None  # 本帧背景轨转场状态（原子写入 pending）
            # 时间线切换 → 清空 alpha 解码缓存（避免读取到已失效 clip 的帧）
            _tid = id(self.tl)
            if self._alpha_cache_tl_id != _tid:
                self._clear_alpha_cache()
                self._alpha_cache_tl_id = _tid
            active_clips: list = []
            for ti, track in enumerate(self.tl.video_tracks):
                for c in track:
                    if not getattr(c, "visible", True):
                        continue
                    if c.timeline_start <= sec < c.timeline_start + c.duration:
                        active_clips.append((ti, c))
                        break

            main_clip = None
            for ti, c in active_clips:
                if ti == 0:
                    main_clip = c
                    break

            # ── 性能探针：主轨片段切换检测（仅主消费路径 write_pending=True）──
            if self._perf.enabled and write_pending:
                _mp = main_clip.source_path if main_clip is not None else None
                if _mp != self._perf._perf_cur_main_path:
                    if self._perf._perf_cur_main_path is not None and _mp is not None:
                        self._perf.clip_switch_count += 1
                        logging.info("[PERF] Clip Changed old=%s new=%s sec=%.3f",
                                     os.path.basename(
                                         self._perf._perf_cur_main_path),
                                     os.path.basename(_mp), sec)
                    self._perf._perf_cur_main_path = _mp
                    self._perf.current_clip = os.path.basename(
                        _mp) if _mp else "-"

            # 背景轨转场检测：若 main_clip 是带 outgoing 转场的 B，取 A 末帧备用
            self._pending_transition_val = self._bg_transition_state(
                main_clip, sec)

            if main_clip is None or not os.path.exists(main_clip.source_path):
                _skip_sub2 = self._editing_sub or self._selected_sub
                active_subs = []
                for ti, track in enumerate(self.tl.subtitle_tracks):
                    for b in track:
                        if b is _skip_sub2:
                            continue
                        if not getattr(b, "visible", True):
                            continue
                        if b.timeline_start <= sec < b.timeline_end:
                            active_subs.append(b)
                # 无主视频帧：收集 overlay raw 数据
                raw_ovs = []
                for ti, oc in active_clips:
                    if ti == 0 or not write_pending or not os.path.exists(oc.source_path):
                        continue
                    ext_o = os.path.splitext(oc.source_path)[1].lower()
                    try:
                        if ext_o in IMAGE_EXTS:
                            raw_ovs.append(
                                (oc, ("image", oc.source_path), 0, 0))
                        else:
                            src_o = oc.trim_start + \
                                (sec - oc.timeline_start) * oc.speed
                            # alpha 视频：走稳健取帧（预解码缓存→管道即时取帧→复用上一帧→跳过），
                            # 绝不回退 cv2 丢 alpha（否则透明区闪黑框）
                            _alpha_res = self._fetch_alpha_overlay_frame(
                                oc, src_o)
                            if _alpha_res == "USE_CV2":
                                pass  # 非 alpha 视频，走下方 ClipDecoder 路径
                            elif _alpha_res is None:
                                continue  # 无任何可用 alpha 帧：跳过，不画不透明黑框
                            else:
                                o_rgb, w, h = _alpha_res
                                raw_ovs.append((oc, ("video", o_rgb), w, h))
                                continue
                            # 状态机解码器路径（连续 read + 窗口缓存，根除每帧 seek）
                            dec_o = self._decoders.get(oc)
                            if dec_o is not None:
                                _ov_ahead = 5 if (self._playing and write_pending) else 1
                                res_o = dec_o.request(src_o, self._decode_state, ahead_frames=_ov_ahead)
                                if res_o is not None:
                                    o_rgb, w, h = res_o
                                    self._cache_src_size(oc.source_path, (w, h))
                                    raw_ovs.append((oc, ("video", o_rgb), w, h))
                    except Exception:
                        logging.debug("overlay fetch error", exc_info=True)
                _payload = {
                    'raw': ("solid_bg", None) if (active_subs or raw_ovs) else None,
                    'clip': None, 'subs': active_subs, 'ovs': raw_ovs,
                    'cleared': (not active_clips),
                    'is_image': False, 'w': 0, 'h': 0,
                    'trans': self._pending_transition_val,
                }
                if write_pending:
                    with self._frame_lock:
                        # 有叠加轨或字幕时，用 solid_bg 创建画布，确保 _flush_frame
                        # 进入 has_video_frame=True 分支 → 叠加轨才会被渲染
                        self._pending_raw = _payload['raw']
                        self._pending_raw_w = 0
                        self._pending_raw_h = 0
                        self._pending_raw_is_image = False
                        self._pending_clip = None
                        self._pending_subs = active_subs
                        self._pending_raw_overlays = raw_ovs
                        self._pending_overlays = []
                        self._pending_transition = self._pending_transition_val
                        self._pending_cleared = (not active_clips)
                return _payload

            # ── 提取主帧 ──
            clip = main_clip
            ext = os.path.splitext(clip.source_path)[1].lower()
            is_image = ext in IMAGE_EXTS

            if is_image:
                # 图片：存路径，_flush_frame 负责加载 QImage
                # 不在此处加锁写入 _pending_raw —— 统一在末尾与叠加轨原子写入
                _pending_raw_val = ("image", clip.source_path)
                _pending_raw_w_val = 0
                _pending_raw_h_val = 0
                _pending_raw_is_image_val = True
                # 提前获取尺寸（cv2 加载在 _flush_frame 完成）
            else:
                src_sec = clip.trim_start + \
                    (sec - clip.timeline_start) * clip.speed
                frame = None
                frame_rgb = None
                _pending_raw_w_val = 0
                _pending_raw_h_val = 0
                _pending_raw_is_image_val = False

                # ── MOV/WebM：从整段预解码缓存读取（后台线程已解码，绝不阻塞）──
                if _HAS_ALPHA and ext in _ALPHA_EXTS:
                    bgra = self._get_alpha_frame(clip, src_sec)
                    if bgra is not None:
                        frame_rgb = cv2.cvtColor(bgra, cv2.COLOR_BGRA2RGBA)
                        h, w = frame_rgb.shape[:2]
                        self._clip_src_w = w
                        self._clip_src_h = h
                        self._cache_src_size(clip.source_path, (w, h))
                        self._last_main_raw = frame_rgb
                        self._last_main_clip = clip
                        # 不再提前 return：统一在末尾设置 _pending_raw，并继续收集叠加轨

                if frame_rgb is None:
                    # ── 非 alpha 视频：走状态机解码器（连续 read + 窗口缓存，根除每帧 seek）──
                    dec = self._decoders.get(clip)
                    if dec is not None:
                        _ahead = 5 if (self._playing and write_pending) else 1
                        res = dec.request(
                            src_sec, self._decode_state, ahead_frames=_ahead)
                        if res is not None:
                            frame_rgb, w, h = res
                            self._clip_src_w = w
                            self._clip_src_h = h
                            self._cache_src_size(clip.source_path, (w, h))
                            self._last_main_raw = frame_rgb
                            self._last_main_clip = clip
                    if frame_rgb is None:
                        # 解码失败（cap 卡死 / 文件问题）：复用上一帧成功画面，避免闪空/闪黑
                        if self._last_main_raw is not None and self._last_main_clip is clip:
                            frame_rgb = self._last_main_raw
                        else:
                            _payload = {
                                'raw': None, 'clip': clip, 'subs': [],
                                'ovs': [], 'cleared': False, 'is_image': False,
                                'w': 0, 'h': 0, 'trans': self._pending_transition_val,
                            }
                            if write_pending:
                                with self._frame_lock:
                                    self._pending_raw = None
                                    self._pending_raw_is_image = False
                                    self._pending_clip = clip
                                    self._pending_raw_overlays = []
                                    self._pending_overlays = []
                                    self._pending_transition = self._pending_transition_val
                            return _payload

                h, w = frame_rgb.shape[:2]
                self._clip_src_w = w
                self._clip_src_h = h
                self._cache_src_size(clip.source_path, (w, h))
                _pending_raw_val = frame_rgb
                _pending_raw_w_val = w
                _pending_raw_h_val = h
                # 注意：不在此处设置 _pending_raw —— 必须等叠加轨也收集完毕后
                # 与 _pending_raw_overlays 在同一个锁块内原子写入，否则主线程
                # _flush_frame 可能在两段写入之间取到「新主帧 + 旧/空叠加」，
                # 导致 MOV 叠加层每隔一帧消失 = 闪烁。
                # 注意：此处不再 return，继续向下收集字幕与叠加轨视频帧

            # ── 收集字幕 ──
            _skip_sub = self._editing_sub or self._selected_sub
            active_subs = []
            for ti, track in enumerate(self.tl.subtitle_tracks):
                for b in track:
                    if b is _skip_sub:
                        continue
                    if not getattr(b, "visible", True):
                        continue
                    if b.timeline_start <= sec < b.timeline_end:
                        active_subs.append(b)

            # ── 收集叠加轨视频帧（raw numpy）──
            raw_ovs = []
            for ti, oc in active_clips:
                if ti == 0 or not os.path.exists(oc.source_path):
                    continue
                ext_o = os.path.splitext(oc.source_path)[1].lower()
                _ov_short = os.path.basename(oc.source_path)[:8]
                try:
                    if ext_o in IMAGE_EXTS:
                        raw_ovs.append((oc, ("image", oc.source_path), 0, 0))
                    else:
                        src_o = oc.trim_start + \
                            (sec - oc.timeline_start) * oc.speed
                        # ── alpha 视频：走稳健取帧（预解码缓存→管道即时取帧→复用→跳过）──
                        #    绝不回退 cv2 丢 alpha（否则透明区闪黑框）
                        _alpha_res = self._fetch_alpha_overlay_frame(oc, src_o)
                        if _alpha_res == "USE_CV2":
                            pass  # 非 alpha 视频，走下方 cv2 路径
                        elif _alpha_res is None:
                            continue  # 无任何可用 alpha 帧：跳过，不画不透明黑框
                        else:
                            o_rgb, w, h = _alpha_res
                            raw_ovs.append((oc, ("video", o_rgb), w, h))
                            continue
                        # cv2 路径（非 alpha 视频）：走状态机解码器，与主轨共用零 seek 策略
                        dec_o = self._decoders.get(oc)
                        if dec_o is not None:
                            _ov_ahead = 5 if (self._playing and write_pending) else 1
                            res_o = dec_o.request(src_o, self._decode_state, ahead_frames=_ov_ahead)
                            if res_o is not None:
                                o_rgb, w, h = res_o
                                self._cache_src_size(oc.source_path, (w, h))
                                raw_ovs.append((oc, ("video", o_rgb), w, h))
                except Exception:
                    logging.debug("bg frame fetch error", exc_info=True)

            _payload = {
                'raw': _pending_raw_val, 'clip': clip, 'subs': active_subs,
                'ovs': raw_ovs, 'cleared': False,
                'is_image': _pending_raw_is_image_val,
                'w': _pending_raw_w_val, 'h': _pending_raw_h_val,
                'trans': self._pending_transition_val,
            }
            if write_pending:
                with self._frame_lock:
                    # 原子写入：主帧 + 字幕 + 叠加轨同时设置，杜绝主线程读到
                    # 「新主帧 + 空/旧叠加」导致的叠加层闪烁
                    self._pending_raw = _pending_raw_val
                    self._pending_raw_w = _pending_raw_w_val
                    self._pending_raw_h = _pending_raw_h_val
                    self._pending_raw_is_image = _pending_raw_is_image_val
                    self._pending_clip = clip
                    self._pending_subs = active_subs
                    self._pending_raw_overlays = raw_ovs
                    self._pending_overlays = []  # 标记需要 _flush_frame 转换
                    self._pending_transition = self._pending_transition_val
                    self._pending_cleared = False
            return _payload

        except Exception:
            import traceback
            traceback.print_exc()
            if write_pending:
                with self._frame_lock:
                    self._pending_raw = None
                    self._pending_raw_is_image = False
                    self._pending_clip = None
                    self._pending_subs = []
                    self._pending_raw_overlays = []
                    self._pending_overlays = []
                    self._pending_transition = None
            return None

    def _safe_flush(self):
        """安全包装 _flush_frame，防止异常导致定时器停止。
        并根据播放/交互状态自适应调整刷新间隔，暂停时降低 CPU 占用。"""
        try:
            # ── 帧诊断：播放中连续无新帧超过 1 秒 → 输出状态 ──
            if self._playing and not self._preview_active:
                with self._frame_lock:
                    has_raw = self._pending_raw is not None
                if has_raw:
                    self._stall_count = 0
                else:
                    self._stall_count += 1
                    if self._stall_count == 30 or self._stall_count % 90 == 0:
                        import sys as _sys
                        print(
                            f"[PREVIEW STALL] {self._stall_count} ticks no frame | "
                            f"fetch_thread_alive={self._fetch_thread.is_alive()} "
                            f"queue_size={self._fetch_queue.qsize()} "
                            f"pending_cleared={self._pending_cleared} "
                            f"pending_raw_is_image={self._pending_raw_is_image}",
                            file=_sys.stderr, flush=True)
            else:
                self._stall_count = 0

            self._flush_frame()
            # 自适应刷新率：播放/拖拽/缩放/内联编辑 → 8ms；空闲 → 200ms
            is_busy = (getattr(self, '_playing', False)
                       or getattr(self, '_preview_active', False)
                       or self._sub_interaction is not None
                       or self._resize_handle is not None
                       or self._rotation_active
                       or self._dragging_video is not None
                       or getattr(self, '_editing_sub', None) is not None)
            target = 8 if is_busy else self._refresh_timeout_idle
            if self._refresh_timer.interval() != target:
                self._refresh_timer.setInterval(target)
        except Exception:
            import traceback
            import sys
            traceback.print_exc(file=sys.stderr)

    def _alloc_canvas(self, cw: int, ch: int) -> QImage:
        """分配/复用画布 QImage，尺寸不变时复用避免内存分配

        预览画布使用不透明格式（Format_RGB32）。
        背景始终尊重用户设定的纯色背景（透明叠加轨的透明区域会透出此色）。
        导出画布由 compositor 管理（使用 Format_ARGB32 保留 alpha）。
        """
        need_prepare = False
        _cached_bg = getattr(self, '_canvas_cache_bg_color', None)
        if (self._canvas_cache is not None and not self._canvas_cache.isNull()
                and self._canvas_cache_size == (cw, ch)
                and getattr(self, '_canvas_cache_bg_type', 'solid') == 'solid'
                and _cached_bg == self._canvas_bg_color):
            # 尺寸、背景类型、背景色都不变，复用缓存
            pass
        else:
            # 尺寸或背景色变化，重建画布
            self._canvas_cache_size = (cw, ch)
            self._canvas_cache_bg_type = 'solid'
            self._canvas_cache_bg_color = self._canvas_bg_color
            need_prepare = True  # 新建：需要准备画布

        if need_prepare:
            # 纯色背景：填用户设定的背景色（透明叠加轨透明区透出此色）
            self._canvas_cache = QImage(cw, ch, QImage.Format.Format_RGB32)
            bg = self._canvas_bg_color or '#000000'
            if isinstance(bg, str):
                from PyQt6.QtGui import QColor as _QColor
                bg = _QColor(bg)
            self._canvas_cache.fill(bg)

        return self._canvas_cache

    def _numpy_to_qimage(self, arr: "np.ndarray") -> QImage:
        """numpy 帧转 QImage，自动检测并保留 alpha 通道。
        复用持久 numpy 缓冲 + QImage（对象池，按 (w,h,ch) 索引），消除每帧
        .copy()/QImage 构造的 malloc 与内存碎片（#2/#14）。"""
        h, w = arr.shape[:2]
        ch = arr.shape[2] if len(arr.shape) == 3 else 1
        bytes_per_line = ch * w
        key = (w, h, ch)
        pool = self._frame_buf_pool
        cached = pool.get(key)
        if cached is not None:
            buf, qimg = cached
            pool.move_to_end(key)          # LRU 更新
        else:
            fmt = (QImage.Format.Format_RGBA8888 if ch == 4
                   else QImage.Format.Format_RGB888)
            buf = np.empty((h, w, ch), dtype=np.uint8)   # 持久缓冲，跨帧复用
            qimg = QImage(buf.data, w, h, bytes_per_line, fmt)
            pool[key] = (buf, qimg)
            while len(pool) > self._frame_buf_pool_max:
                pool.popitem(last=False)
        # 原地填充持久缓冲（QPixmap.fromImage 会复制，故可安全复用）
        cont = arr if arr.flags['C_CONTIGUOUS'] else np.ascontiguousarray(arr)
        np.copyto(buf, cont)
        return qimg

    def _blur_qpixmap(self, pix: "QPixmap", radius: float) -> "QPixmap":
        """用 OpenCV GaussianBlur 代替 QGraphicsBlurEffect，性能更好。
        对 QPixmap 做高斯模糊，返回模糊后的 QPixmap。
        """
        try:
            import cv2
            import numpy as np
            img = pix.toImage()
            w, h = img.width(), img.height()
            if w <= 0 or h <= 0:
                return pix
            # QImage → numpy（保留 alpha）
            ptr = img.bits()
            ptr.setsize(h * img.bytesPerLine())
            arr = np.frombuffer(ptr, np.uint8).reshape(
                (h, w, 4)).copy()  # copy 防止悬挂指针
            # 只对 RGB 通道模糊，保留 A
            bgr = cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2BGR)
            blur_cv = cv2.GaussianBlur(bgr, (0, 0), radius)
            blur_rgb = cv2.cvtColor(blur_cv, cv2.COLOR_BGR2RGB)
            arr[:, :, :3] = blur_rgb
            # numpy → QImage → QPixmap
            blur_img = QImage(arr.data, w, h, img.bytesPerLine(),
                              QImage.Format.Format_RGBA8888).copy()
            return QPixmap.fromImage(blur_img)
        except Exception:
            logging.debug(
                "OpenCV blur failed, fallback to original", exc_info=True)
            return pix

    @staticmethod
    def _blur_qimage(img: "QImage", radius: float) -> "QImage":
        """对 QImage 做高斯模糊，保留 alpha 通道。
        兼容 PyQt5/6 的 bits 返回类型（voidptr / memoryview），并消除行对齐 padding
        导致 reshape 失败的隐患：先转成 RGBA8888（stride 恒为 w*4），再复制出独立内存。
        """
        try:
            import cv2
            import numpy as np
            w, h = img.width(), img.height()
            if w <= 0 or h <= 0:
                return img
            # 统一转 RGBA8888：保证 4 通道且 stride == w*4（无行对齐 padding），
            # 避免原图 RGB32 / 带 padding 时 reshape 维度不匹配。
            src = img.convertToFormat(QImage.Format.Format_RGBA8888)
            nbytes = h * w * 4
            bits = src.constBits()
            # PyQt5/6 的 voidptr 支持 buffer protocol；用 asarray 取得可复制内存
            try:
                buf = bytes(bits.asarray(nbytes))
            except AttributeError:
                buf = bytes(bits)
            arr = np.frombuffer(buf, np.uint8).reshape((h, w, 4)).copy()
            # 仅模糊 RGB 通道，alpha 保持不变
            bgr = cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2BGR)
            ksize = max(3, int(round(radius * 2)) | 1)
            blr = cv2.GaussianBlur(bgr, (ksize, ksize), radius)
            arr[:, :, :3] = cv2.cvtColor(blr, cv2.COLOR_BGR2RGB)
            # 写回独立 QImage（.copy() 保证内存生命周期安全）
            return QImage(arr.data, w, h, w * 4,
                          QImage.Format.Format_RGBA8888).copy()
        except Exception:
            logging.debug("OpenCV blur (QImage) failed", exc_info=True)
            return img

    def _clip_opacity(self, clip, default: float = 1.0) -> float:
        """返回片段不透明度（含关键帧插值）。

        注意：opacity=0 是合法值（完全透明），绝不能用 `or 1.0` 写法——
        `0 or 1.0` 在 Python 中恒为 1.0，会导致「不透明度拉到 0 反而恢复原样」。
        """
        op = getattr(clip, 'opacity', default)
        if not isinstance(op, (int, float)):
            op = default
        kf = getattr(clip, 'keyframes', None) or {}
        op_kf = kf.get('opacity')
        if op_kf:
            try:
                from core.edit_engine import interpolate_keyframes
                rel_t = self._current_sec - clip.timeline_start
                vals = interpolate_keyframes(
                    clip, {'opacity': op_kf}, rel_t, {'opacity': op})
                v = vals.get('opacity', op)
                if isinstance(v, (int, float)):
                    op = v
            except Exception:
                logging.debug("opacity 关键帧插值失败", exc_info=True)
        return op

    @staticmethod
    def _qimage_from_path(path: str) -> Optional[QImage]:
        """加载图片 QImage，保留 alpha 通道"""
        img = QImage(path)
        if img.isNull():
            return None
        if img.hasAlphaChannel():
            return img.convertToFormat(QImage.Format.Format_ARGB32)
        return img.convertToFormat(QImage.Format.Format_RGB32)

    def _apply_chroma_key(self, clip, img):
        """若片段启用绿幕抠像，对 QImage 做色度键处理（预览路径）。"""
        if not getattr(clip, 'chroma_key_enabled', False):
            return img
        try:
            from utils.chroma_key import apply_chroma_key
            return apply_chroma_key(
                img,
                getattr(clip, 'chroma_key_color', (0, 255, 0)),
                getattr(clip, 'chroma_key_similarity', 0.40),
                getattr(clip, 'chroma_key_smoothness', 0.10),
                getattr(clip, 'chroma_key_spill', 0.10),
            )
        except Exception:
            logging.debug("chroma key preview failed", exc_info=True)
            return img

    def _draw_video_layer(self, painter, clip, scaled_img, ox, oy):
        """绘制单个视频片段到 canvas（含整体不透明度），并绘制选中框/手柄。
        绿幕（chroma_key）片段延迟到最后调用本方法，使其透明区露出下层所有轨道。"""
        _op = self._clip_opacity(clip)
        if _op < 1.0:
            painter.save()
            painter.setOpacity(_op)
            painter.drawImage(ox, oy, scaled_img)
            painter.restore()
        else:
            painter.drawImage(ox, oy, scaled_img)
        sel = getattr(self, '_selected_video_clip', None)
        if sel is not None and sel is clip:
            solid_pen = QPen(QColor("#ffffff"), 2, Qt.PenStyle.SolidLine)
            painter.setPen(solid_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(ox, oy, scaled_img.width(), scaled_img.height())
            HS = 8
            hHS = HS // 2
            handle_pen = QPen(QColor("#00eaff"), 1.5, Qt.PenStyle.SolidLine)
            handle_brush = QBrush(QColor("#ffffff"))
            corners = [
                (ox - hHS, oy - hHS),
                (ox + scaled_img.width() - hHS, oy - hHS),
                (ox - hHS, oy + scaled_img.height() - hHS),
                (ox + scaled_img.width() - hHS,
                 oy + scaled_img.height() - hHS),
            ]
            for (sx, sy) in corners:
                painter.drawRect(sx, sy, HS, HS)
            rcx = ox + scaled_img.width() // 2
            rcy = oy - 20
            painter.setPen(
                QPen(QColor("#00eaff"), 1.5, Qt.PenStyle.SolidLine))
            painter.drawLine(rcx, oy, rcx, rcy + 8)
            painter.setBrush(QBrush(QColor("#1a1a2e")))
            painter.drawEllipse(QPoint(rcx, rcy), 6, 6)

    def _recompose_overlays(self):
        """同一位置轻量刷新：复用原始帧，仅重新渲染叠加层（字幕/变换），不异步解码、不闪黑。
        用于属性面板 slider 拖拽等场景。"""
        cw = getattr(self, '_canvas_w', 0) or (
            self._screen.width() if self._screen else 640)
        ch = getattr(self, '_canvas_h', 0) or (
            self._screen.height() if self._screen else 360)
        if cw <= 0 or ch <= 0:
            return

        # 优先从原始帧重新合成（零残留）
        canvas = self._compose_from_raw(cw, ch)
        if canvas is None or canvas.isNull():
            # 回退：复用缓存帧
            last_img = getattr(self, '_last_frame_image', None)
            if last_img is not None and not last_img.isNull() and last_img.width() == cw and last_img.height() == ch:
                canvas = last_img.copy()
            else:
                return

        # ── 收集当前活跃字幕（主线程，使用已更新的数据模型）──
        skip = self._editing_sub or self._selected_sub
        active_subs = []
        for track in self.tl.subtitle_tracks:
            for b in track:
                if b is skip:
                    continue
                if not getattr(b, "visible", True):
                    continue
                if b.timeline_start <= self._current_sec < b.timeline_end:
                    active_subs.append(b)

        # ── 渲染活跃字幕 ──
        if active_subs:
            self._overlay_subtitles(canvas, active_subs, cw, ch)

        # ── 缓存帧（干净缓存：视频+背景+非选中字幕，不含选中/编辑字幕层）──
        clean_cache = canvas.copy()
        self._last_frame_image = clean_cache
        self._last_active_subs = list(active_subs) if active_subs else []

        # ── 选中字幕边框 ──
        if self._selected_sub is not None and self._editing_sub is None:
            sel_block = self._selected_sub
            sel_text = getattr(sel_block, 'text', '') or ''
            if sel_text.strip():
                self._overlay_subtitles(canvas, [sel_block], cw, ch)
            sel_painter = QPainter(canvas)
            sel_painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._draw_sub_selection(sel_painter, sel_block, cw, ch)
            sel_painter.end()

        # ── 内联编辑文字 ──
        if self._editing_sub is not None:
            edit_painter = QPainter(canvas)
            edit_painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._draw_edit_subtitle(edit_painter, cw, ch)
            edit_painter.end()

        self._screen.setPixmap(QPixmap.fromImage(canvas))

    def _flush_frame(self, force: bool = False):
        """主线程：把后台线程准备好的帧合成到画布上再渲染"""
        if self._perf.enabled:
            self._perf_t = time.perf_counter()
            self._perf_scale_acc = 0.0
            self._perf_draw_acc = 0.0
            self._perf_sub_acc = 0.0
            self._perf_copy_acc = 0.0
            self._perf_pix_acc = 0.0
            self._perf_present = 0.0
            self._perf_source = "-"
        # ── 预览模式：跳过时间线渲染，由 _tick_preview 处理 ──
        if self._preview_active:
            self._tick_preview()
            return

        # 帧缓存环命中（当前 sec 已有解码完成的帧）→ 也算需要刷新
        _rk_cur = self._ring_key(self._current_sec)
        with self._frame_lock:
            _ring_entry = self._payload_ring.get(_rk_cur)
            _has_ring = (
                _ring_entry is not None and _ring_entry.get('raw') is not None)
        need_frame = (force or self._pending_raw is not None
                      or self._pending_cleared or _has_ring)

        # ── 快速路径：交互期间复用缓存帧，不消耗异步帧池，零闪烁 ──
        if force and (self._sub_interaction is not None or self._selected_video_clip is not None):
            try:
                cw = getattr(self, '_canvas_w', 0) or (
                    self._screen.width() if self._screen else 640)
                ch = getattr(self, '_canvas_h', 0) or (
                    self._screen.height() if self._screen else 360)
                if cw <= 0 or ch <= 0:
                    return
                last_img = getattr(self, '_last_frame_image', None)
                if last_img is not None and not last_img.isNull() and last_img.width() == cw and last_img.height() == ch:
                    canvas = last_img.copy()
                else:
                    canvas = self._alloc_canvas(cw, ch)

                if self._sub_interaction is not None and self._selected_sub is not None:
                    # 字幕拖拽/缩放：复用缓存帧 + 绘制新位置文字 + 选中框
                    sel_block = self._selected_sub
                    sel_text = getattr(sel_block, 'text', '') or ''
                    if sel_text.strip():
                        self._overlay_subtitles(canvas, [sel_block], cw, ch)
                    sp = QPainter(canvas)
                    sp.setRenderHint(QPainter.RenderHint.Antialiasing)
                    self._draw_sub_selection(sp, sel_block, cw, ch)
                    sp.end()
                else:
                    # 视频：拖拽/缩放/旋转中需实时渲染；仅单击选中则复用完整合成帧
                    # （含主视频+背景+叠加轨），只在上面画选中框，不重新取帧 → 不闪、不丢叠加
                    if self._dragging_video is not None or self._resize_handle is not None or self._rotation_active:
                        canvas = self._compose_from_raw(cw, ch)
                        if canvas is None:
                            # 无原始帧缓存 → 用 _alloc_canvas 准备的画布（纯色/虚化背景）
                            canvas = self._alloc_canvas(cw, ch)
                    else:
                        if last_img is not None and not last_img.isNull() and last_img.width() == cw and last_img.height() == ch:
                            canvas = last_img.copy()
                        else:
                            canvas = self._alloc_canvas(cw, ch)
                    sel = self._selected_video_clip
                    if sel is not None:
                        src_w, src_h = self._get_clip_src_size(sel)
                        rect = self._video_screen_rect(sel, src_w, src_h)
                        if rect:
                            ox, oy, rw, rh = rect
                            vp = QPainter(canvas)
                            vp.setRenderHint(QPainter.RenderHint.Antialiasing)
                            solid_pen = QPen(
                                QColor("#ffffff"), 2, Qt.PenStyle.SolidLine)
                            vp.setPen(solid_pen)
                            vp.setBrush(Qt.BrushStyle.NoBrush)
                            vp.drawRect(ox, oy, rw, rh)
                            # 四角缩放把手：8×8 白色方块（标准双向箭头样式）
                            HS = 8
                            hHS = HS // 2
                            handle_pen = QPen(
                                QColor("#00eaff"), 1.5, Qt.PenStyle.SolidLine)
                            handle_brush = QBrush(QColor("#ffffff"))
                            vp.setPen(handle_pen)
                            vp.setBrush(handle_brush)
                            corners = [
                                (ox - hHS, oy - hHS),             # NW
                                (ox + rw - hHS, oy - hHS),        # NE
                                (ox - hHS, oy + rh - hHS),        # SW
                                (ox + rw - hHS, oy + rh - hHS),   # SE
                            ]
                            for (sx, sy) in corners:
                                vp.drawRect(sx, sy, HS, HS)
                            # ── 旋转把手：顶部中央圆圈 + 连接线 ──
                            rcx = ox + rw // 2
                            rcy = oy - 20
                            vp.setPen(QPen(QColor("#00eaff"),
                                      1.5, Qt.PenStyle.SolidLine))
                            vp.drawLine(rcx, oy, rcx, rcy + 8)
                            vp.setBrush(QBrush(QColor("#1a1a2e")))
                            vp.drawEllipse(QPoint(rcx, rcy), 6, 6)
                            # 圆圈内画旋转箭头标记
                            vp.setPen(QPen(QColor("#00eaff"),
                                      1, Qt.PenStyle.SolidLine))
                            vp.drawText(QRectF(rcx - 5, rcy - 6, 10, 12),
                                        Qt.AlignmentFlag.AlignCenter, "↻")
                            # ── 缩放比例显示 ──
                            scale_pct = int(
                                round((getattr(sel, 'scale', 1.0) or 1.0) * 100))
                            label = f"{scale_pct}%"
                            vp.setPen(Qt.PenStyle.NoPen)
                            vp.setBrush(QColor(0, 0, 0, 160))
                            f = QFont("Microsoft YaHei", 9, QFont.Weight.Bold)
                            vp.setFont(f)
                            fm = QFontMetrics(f)
                            tw = fm.horizontalAdvance(label) + 10
                            th = fm.height() + 4
                            lx = ox + (rw - tw) // 2
                            ly = oy - th - 4
                            if ly < 0:
                                ly = oy + rh + 4
                            vp.drawRoundedRect(lx, ly, tw, th, 4, 4)
                            vp.setPen(QColor("#ffffff"))
                            vp.drawText(QRectF(lx, ly, tw, th),
                                        Qt.AlignmentFlag.AlignCenter, label)
                            vp.end()

                self._screen.setPixmap(QPixmap.fromImage(canvas))
            except Exception:
                import traceback
                import sys
                traceback.print_exc(file=sys.stderr)
                self._resize_handle = None
                self._dragging_video = None
                self._sub_interaction = None
            return

        if not need_frame:
            # 没有新帧到达
            if self._playing:
                if self._editing_sub is None:
                    return
            else:
                if self._editing_sub is None and self._selected_sub is None:
                    return
            # 编辑/选中但无新帧 → 复用上一帧底图重新叠编辑层
            cw = getattr(self, '_canvas_w', 0) or (
                self._screen.width() if self._screen else 640)
            ch = getattr(self, '_canvas_h', 0) or (
                self._screen.height() if self._screen else 360)
            if cw <= 0 or ch <= 0:
                return
            last_img = getattr(self, '_last_frame_image', None)
            if last_img is not None and not last_img.isNull() and last_img.width() == cw and last_img.height() == ch:
                canvas = last_img.copy()
                # 重新叠编辑层/选中框
                if self._selected_sub is not None and self._editing_sub is None:
                    sel_block = self._selected_sub
                    sel_text = getattr(sel_block, 'text', '') or ''
                    if sel_text.strip():
                        self._overlay_subtitles(canvas, [sel_block], cw, ch)
                    sel_painter = QPainter(canvas)
                    sel_painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                    self._draw_sub_selection(sel_painter, sel_block, cw, ch)
                    sel_painter.end()
                if self._editing_sub is not None:
                    edit_painter = QPainter(canvas)
                    edit_painter.setRenderHint(
                        QPainter.RenderHint.Antialiasing)
                    self._draw_edit_subtitle(edit_painter, cw, ch)
                    edit_painter.end()
                self._screen.setPixmap(QPixmap.fromImage(canvas))
                return
            # 无缓存帧（首次编辑 / 尺寸变化）→ 画布已含背景色/虚化
            if self._editing_sub is not None:
                sel_canvas = self._alloc_canvas(cw, ch)
                sp = QPainter(sel_canvas)
                sp.setRenderHint(QPainter.RenderHint.Antialiasing)
                self._draw_edit_subtitle(sp, cw, ch)
                sp.end()
                self._screen.setPixmap(QPixmap.fromImage(sel_canvas))
            elif self._selected_sub is not None:
                sel_canvas = self._alloc_canvas(cw, ch)
                sel_block = self._selected_sub
                sel_text = getattr(sel_block, 'text', '') or ''
                if sel_text.strip():
                    self._overlay_subtitles(sel_canvas, [sel_block], cw, ch)
                sp = QPainter(sel_canvas)
                sp.setRenderHint(QPainter.RenderHint.Antialiasing)
                self._draw_sub_selection(sp, sel_block, cw, ch)
                sp.end()
                self._screen.setPixmap(QPixmap.fromImage(sel_canvas))
            return

        # 取帧（优先帧缓存环命中当前 sec；否则回退 _pending_raw）
        _rk = self._ring_key(self._current_sec)
        with self._frame_lock:
            payload = self._payload_ring.get(_rk)
            if payload is not None and payload.get('raw') is not None:
                self._perf_source = 'ring'
                raw = payload['raw']
                clip = payload['clip']
                active_subs = payload['subs']
                raw_ovs = payload['ovs']
                cleared = payload['cleared']
                raw_is_image = payload['is_image']
                raw_w = payload['w']
                raw_h = payload['h']
                trans = payload['trans']
            else:
                self._perf_source = 'pending'
                raw = self._pending_raw
                clip = self._pending_clip
                active_subs = self._pending_subs
                raw_ovs = self._pending_raw_overlays
                cleared = self._pending_cleared
                raw_is_image = self._pending_raw_is_image
                raw_w = self._pending_raw_w
                raw_h = self._pending_raw_h
                trans = self._pending_transition
            # 无论来源，消费后清掉 _pending_raw（环帧或 pending），避免回退显示过期帧
            self._pending_raw = None
            self._pending_clip = None
            self._pending_subs = []
            self._pending_raw_overlays = []
            self._pending_cleared = False
            self._pending_raw_is_image = False
            self._pending_transition = None

        # 主线程转换 raw → QImage
        img: Optional[QImage] = None
        overlays: list = []
        if isinstance(raw, np.ndarray):
            # 视频帧：numpy RGB/RGBA → QImage（自动检测 alpha）
            img = self._numpy_to_qimage(raw)
        elif isinstance(raw, tuple):
            kind, payload = raw
            if kind == "image" and isinstance(payload, str) and os.path.exists(payload):
                img = self._qimage_from_path(payload)
                if not img.isNull() and clip:
                    self._clip_src_w = img.width()
                    self._clip_src_h = img.height()
                    self._cache_src_size(
                        clip.source_path, (img.width(), img.height()))
            elif kind == "solid_bg":
                cw = getattr(self, '_canvas_w', 0) or (
                    self._screen.width() if self._screen else 640)
                ch = getattr(self, '_canvas_h', 0) or (
                    self._screen.height() if self._screen else 360)
                if cw > 0 and ch > 0:
                    img = self._alloc_canvas(cw, ch)
                    # 不再 fill：_alloc_canvas 已准备画布（纯色填背景色，虚化填黑底）
                    self._clip_src_w = cw
                    self._clip_src_h = ch
        elif raw is None:
            # 无新帧到达（如单击空白画布只重绘画布、移除选中框，未触发重新取帧）。
            # 复用完整合成缓存帧（含主视频+背景+叠加轨），避免画面"消失"成纯背景。
            last_img = getattr(self, '_last_frame_image', None)
            if last_img is not None and not last_img.isNull():
                cw = getattr(self, '_canvas_w', 0) or (
                    self._screen.width() if self._screen else 640)
                ch = getattr(self, '_canvas_h', 0) or (
                    self._screen.height() if self._screen else 360)
                if cw > 0 and ch > 0 and last_img.width() == cw and last_img.height() == ch:
                    canvas = last_img.copy()
                    self._screen.setPixmap(QPixmap.fromImage(canvas))
                    return
            # 无缓存帧可用 → 后续按纯背景/占位处理

        # 更新 alpha 状态（用于合成时的 SourceOver 透明判断）
        # 只检测实际 QImage 的 alpha 通道，不用 probe_has_alpha（误判率高）
        self._has_alpha = False
        if img is not None and not img.isNull() and img.hasAlphaChannel():
            self._has_alpha = True

        # 转换 overlay raw → QImage
        for ov_entry in raw_ovs:
            ov_clip, ov_raw, ov_w, ov_h = ov_entry
            if isinstance(ov_raw, tuple) and ov_raw[0] == "image":
                ov_path = ov_raw[1]
                if os.path.exists(ov_path):
                    oq = self._qimage_from_path(ov_path)
                    if oq is not None and not oq.isNull():
                        self._cache_src_size(
                            ov_clip.source_path, (oq.width(), oq.height()))
                        # 第三元素：源 numpy 数组（None=图片，视频为 RGB/RGBA ndarray）
                        overlays.append((ov_clip, oq, None))
            elif isinstance(ov_raw, tuple) and ov_raw[0] == "video":
                ov_arr = ov_raw[1]
                if isinstance(ov_arr, np.ndarray):
                    oq = self._numpy_to_qimage(ov_arr)
                    overlays.append((ov_clip, oq, ov_arr))

        # 更新叠加轨 alpha 状态（检查实际 QImage 是否含 alpha 通道）
        if not self._has_alpha:
            for _ov_clip, _ov_img, _ in overlays:
                if _ov_img is not None and not _ov_img.isNull() and _ov_img.hasAlphaChannel():
                    self._has_alpha = True
                    break

        # 保存转换后的 QImage 供其他方法读取（如 _get_clip_src_size 的 fallback）
        self._pending_frame = img
        self._pending_overlays = overlays
        # 更新原始帧缓存（供 _compose_from_raw / 交互拖拽时使用）
        if img is not None and not img.isNull():
            self._last_raw_img = img.copy()
            self._raw_frame_id += 1

        cw = getattr(self, '_canvas_w', 0) or (
            self._screen.width() if self._screen else 640)
        ch = getattr(self, '_canvas_h', 0) or (
            self._screen.height() if self._screen else 360)
        if cw <= 0 or ch <= 0:
            return

        # ── 背景轨转场混合（实时预览，复用 compositor + apply_transition）
        # 帧提取全在主线程；A 冻结帧提取+渲染一次后缓存，B 复用到预览管线已取帧避免 cv2 ──
        self._transition_fullframe = False
        if trans is not None and img is not None and not img.isNull():
            try:
                import cv2
                from core.slideshow_engine import apply_transition
                _A, _B, _alpha, _tfn, _A_end = trans
                _comp = self._get_compositor()
                if _comp is not None:
                    _cache_key = (id(_A), round(_A_end, 4), _comp.W, _comp.H)
                    _tc = getattr(self, '_trans_cache', None)
                    if _tc is not None and _tc[0] == _cache_key:
                        _a_bgr = _tc[1]  # 命中：A 冻结帧已渲染
                    else:
                        _a_qimg = _comp._extract_frame(_A, _A_end - 0.001)
                        if _a_qimg is None or _a_qimg.isNull():
                            raise RuntimeError("A freeze frame failed")
                        _a_bgr = _comp._render_clip_offscreen(
                            _A, _A_end - 0.001, _a_qimg)
                        if _a_bgr is None:
                            raise RuntimeError("A render failed")
                        self._trans_cache = (_cache_key, _a_bgr)
                    # B 帧：直接复用管线已提取的 img，仅做 full-canvas 渲染
                    _b_bgr = _comp._render_clip_offscreen(
                        _B, self._current_sec, img)
                    if _b_bgr is not None:
                        _blended = apply_transition(
                            _a_bgr, _b_bgr, _alpha, _tfn, _comp.W, _comp.H)
                        _blended_rgba = cv2.cvtColor(
                            _blended, cv2.COLOR_BGR2RGBA)
                        _bh, _bw = _blended_rgba.shape[:2]
                        _bq = QImage(_blended_rgba.data, _bw, _bh, _bw * 4,
                                     QImage.Format.Format_RGBA8888).copy()
                        if _bq.width() != cw or _bq.height() != ch:
                            _bq = _bq.scaled(cw, ch, Qt.AspectRatioMode.IgnoreAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation)
                        img = _bq
                        self._transition_fullframe = True
            except Exception:
                logging.debug("预览转场混合失败，回退硬切", exc_info=True)
                self._transition_fullframe = False
                self._trans_cache = None  # 失效缓存
        else:
            self._trans_cache = None  # 非转场帧 → 清缓存

        # 决定渲染模式
        has_video_frame = (img is not None and not img.isNull())

        if has_video_frame:
            # ── 有视频帧：在帧上合成 ──
            # "默认"模式：跟随视频原始尺寸，切换不同视频时自动调整画布
            if self._aspect_ratio is None:
                iw_new = getattr(self, '_clip_src_w', 0) or img.width()
                ih_new = getattr(self, '_clip_src_h', 0) or img.height()
                if iw_new > 0 and ih_new > 0:
                    last_iw = getattr(self, '_last_src_w', 0)
                    last_ih = getattr(self, '_last_src_h', 0)
                    if iw_new != last_iw or ih_new != last_ih:
                        self._last_src_w = iw_new
                        self._last_src_h = ih_new
                        # 同步更新 _clip_src_w/_clip_src_h，防止 stop_preview 后残留旧值
                        self._clip_src_w = iw_new
                        self._clip_src_h = ih_new
                        self._position_screen()
                        cw = getattr(self, '_canvas_w',
                                     0) or self._screen.width()
                        ch = getattr(self, '_canvas_h',
                                     0) or self._screen.height()
            canvas = self._alloc_canvas(cw, ch)
            bg_color = QColor(
                getattr(self, '_canvas_bg_color', '#000000') or '#000000')

            # ── 纯色背景 ──
            canvas.fill(bg_color)

            painter = QPainter(canvas)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            # 默认 SourceOver：主轨视频含 alpha 时自动透出下层背景，不含时完全不透明

            if getattr(self, '_transition_fullframe', False):
                # 转场已合成全画布帧，直接铺满（不重施 transform / 选中框）
                _td = time.perf_counter() if self._perf.enabled else 0
                painter.drawImage(0, 0, img)
                if self._perf.enabled:
                    self._perf_draw_acc += time.perf_counter() - _td
            iw, ih = getattr(self, '_clip_src_w', 0) or img.width(), getattr(
                self, '_clip_src_h', 0) or img.height()
            if (not getattr(self, '_transition_fullframe', False)) and iw > 0 and ih > 0:
                s = 1.0
                px = 0.0; py = 0.0; rot = 0.0; blur = 0.0
                if clip:
                    s = getattr(clip, 'scale', 1.0) or 1.0
                    px = getattr(clip, 'pos_x', 0.0) or 0.0
                    py = getattr(clip, 'pos_y', 0.0) or 0.0
                    rot = getattr(clip, 'rotation', 0.0) or 0.0
                    blur = getattr(clip, 'blur_radius', 0.0) or 0.0
                    kf = getattr(clip, 'keyframes', None) or {}
                    if kf:
                        rel_t = self._current_sec - clip.timeline_start
                        base = {"scale": s, "pos_x": px, "pos_y": py,
                            "rotation": rot, "blur_radius": blur}
                        from core.edit_engine import interpolate_keyframes
                        vals = interpolate_keyframes(clip, kf, rel_t, base)
                        s = vals["scale"]
                        px = vals["pos_x"]
                        py = vals["pos_y"]
                        rot = vals["rotation"]
                        blur = vals["blur_radius"]

                fit_w = cw / iw
                fit_h = ch / ih
                base_scale = min(fit_w, fit_h)
                total_scale = base_scale * s

                new_w = max(1, int(iw * total_scale))
                new_h = max(1, int(ih * total_scale))

                # 缩放（QImage.scaled() 保留 alpha 通道，不转 QPixmap）
                _ts = time.perf_counter() if self._perf.enabled else 0
                scaled_img = img.scaled(
                    new_w, new_h,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                if self._perf.enabled:
                    self._perf_scale_acc += time.perf_counter() - _ts
                # 旋转（QImage）
                if rot != 0.0:
                    t = QTransform().translate(scaled_img.width() / 2, scaled_img.height() / 2)
                    t.rotate(rot)
                    t.translate(-scaled_img.width() / 2, - \
                                scaled_img.height() / 2)
                    scaled_img = scaled_img.transformed(
                        t, Qt.TransformationMode.SmoothTransformation)
                # 模糊（QImage 路径，不转 QPixmap）
                if blur > 0.5:
                    scaled_img = self._blur_qimage(scaled_img, blur)
                # 绿幕抠像（主视频轨同样支持，保证导出=预览）
                scaled_img = self._apply_chroma_key(clip, scaled_img)
                ox = (cw - scaled_img.width()) // 2 + int(px)
                oy = (ch - scaled_img.height()) // 2 + int(py)
                self._draw_video_layer(painter, clip, scaled_img, ox, oy)

            painter.end()

            # 叠视频帧（PiP）
            if overlays:
                ov_painter = QPainter(canvas)
                ov_painter.setRenderHint(
                    QPainter.RenderHint.SmoothPixmapTransform)
                # 强制 SourceOver 合成模式，确保叠加轨 alpha 视频透明区域显示下层内容
                ov_painter.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_SourceOver)
                for ov_clip, ov_img, ov_src in overlays:
                    try:
                        ov_iw, ov_ih = self._cache_src_size(
                            ov_clip.source_path) or (ov_img.width(), ov_img.height())
                        if ov_iw <= 0 or ov_ih <= 0:
                            continue
                        ov_s = getattr(ov_clip, 'scale', 1.0) or 1.0
                        ov_px = getattr(ov_clip, 'pos_x', 0.0) or 0.0
                        ov_py = getattr(ov_clip, 'pos_y', 0.0) or 0.0
                        ov_rot = getattr(ov_clip, 'rotation', 0.0) or 0.0
                        ov_blur = getattr(ov_clip, 'blur_radius', 0.0) or 0.0
                        ov_base = min(cw / ov_iw, ch / ov_ih)
                        ov_ts = ov_base * ov_s
                        ov_nw = max(1, int(ov_iw * ov_ts))
                        ov_nh = max(1, int(ov_ih * ov_ts))
                        # 缩放：alpha 视频用 cv2.resize（可靠保留 4 通道），
                        # 杜绝 QImage.scaled() 偶发丢 alpha 导致透明区变黑闪烁
                        _ts = time.perf_counter() if self._perf.enabled else 0
                        if isinstance(ov_src, np.ndarray) and ov_src.shape[2] == 4:
                            import cv2 as _cv2
                            _interp = _cv2.INTER_AREA if ov_ts < 1 else _cv2.INTER_LINEAR
                            _resized = _cv2.resize(
                                ov_src, (ov_nw, ov_nh), interpolation=_interp)
                            ov_img = self._numpy_to_qimage(_resized)
                        else:
                            ov_img = ov_img.scaled(
                                ov_nw, ov_nh,
                                Qt.AspectRatioMode.IgnoreAspectRatio,
                                Qt.TransformationMode.SmoothTransformation
                            )
                        if self._perf.enabled:
                            self._perf_scale_acc += time.perf_counter() - _ts
                        # 兜底：确保带 alpha 的叠加帧始终为 RGBA8888 格式
                        if ov_img.format() != QImage.Format.Format_RGBA8888 and ov_img.hasAlphaChannel():
                            ov_img = ov_img.convertToFormat(
                                QImage.Format.Format_RGBA8888)
                        # 旋转（QImage.transformed() 保留格式）
                        if ov_rot != 0.0:
                            from PyQt6.QtGui import QTransform
                            t = QTransform().translate(ov_img.width() / 2, ov_img.height() / 2)
                            t.rotate(ov_rot)
                            t.translate(-ov_img.width() / 2, - \
                                        ov_img.height() / 2)
                            ov_img = ov_img.transformed(
                                t, Qt.TransformationMode.SmoothTransformation)
                        # 模糊（QImage 路径，不转 QPixmap，保留 alpha）
                        if ov_blur > 0.5:
                            ov_img = self._blur_qimage(ov_img, ov_blur)
                        # 绿幕抠像
                        ov_img = self._apply_chroma_key(ov_clip, ov_img)
                        ov_ox = (cw - ov_img.width()) // 2 + int(ov_px)
                        ov_oy = (ch - ov_img.height()) // 2 + int(ov_py)
                        self._draw_video_layer(ov_painter, ov_clip, ov_img, ov_ox, ov_oy)
                    except Exception:
                        logging.debug(
                            "overlay gizmo paint error", exc_info=True)
                ov_painter.end()

            # ── 字幕渲染（画布尺寸下，避免视频缩放导致字号不一致）──
            # active_subs 已由后台线程收集（排除了 editing/selected 字幕）
            _ts = time.perf_counter() if self._perf.enabled else 0
            if active_subs:
                self._overlay_subtitles(canvas, active_subs, cw, ch)
            if self._perf.enabled:
                self._perf_sub_acc += time.perf_counter() - _ts

            # ── 缓存帧（视频+背景+非选中字幕，供拖拽/编辑时复用）──
            _tc = time.perf_counter() if self._perf.enabled else 0
            clean_cache = canvas.copy()
            if self._perf.enabled:
                self._perf_copy_acc += time.perf_counter() - _tc
            self._last_frame_image = clean_cache
            self._last_active_subs = list(active_subs) if active_subs else []
            # 同时缓存原始帧像素（缩放/拖拽时从原始帧重新渲染，避免残影）
            self._last_raw_img = img.copy() if img is not None and not img.isNull() else None
            self._last_raw_overlays = [(c, ov.copy())
                                        for c, ov, _ in overlays] if overlays else []

            # ── 字幕选中边框 + 手柄 ──
            if self._selected_sub is not None and self._editing_sub is None:
                sel_block = self._selected_sub
                # 渲染该字幕的文字
                sel_text = getattr(sel_block, 'text', '') or ''
                if sel_text.strip():
                    self._overlay_subtitles(canvas, [sel_block], cw, ch)
                # 选中边框+手柄
                sel_painter = QPainter(canvas)
                sel_painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                self._draw_sub_selection(sel_painter, sel_block, cw, ch)
                sel_painter.end()

            # ── 内联编辑文字 + 光标 ──
            if self._editing_sub is not None:
                edit_painter = QPainter(canvas)
                edit_painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                self._draw_edit_subtitle(edit_painter, cw, ch)
                edit_painter.end()

            self._screen.setPixmap(QPixmap.fromImage(canvas))

        else:
            # ── 无视频帧：黑底渲染编辑框或选中框 ──
            if self._editing_sub is not None:
                sel_canvas = self._alloc_canvas(cw, ch)
                sp = QPainter(sel_canvas)
                sp.setRenderHint(QPainter.RenderHint.Antialiasing)
                self._draw_edit_subtitle(sp, cw, ch)
                sp.end()
                self._screen.setPixmap(QPixmap.fromImage(sel_canvas))
            elif self._selected_sub is not None:
                # 选中但不编辑：黑底 + 字幕文字 + 选中框
                sel_canvas = self._alloc_canvas(cw, ch)
                sel_block = self._selected_sub
                sel_text = getattr(sel_block, 'text', '') or ''
                if sel_text.strip():
                    self._overlay_subtitles(sel_canvas, [sel_block], cw, ch)
                sp = QPainter(sel_canvas)
                sp.setRenderHint(QPainter.RenderHint.Antialiasing)
                self._draw_sub_selection(sp, sel_block, cw, ch)
                sp.end()
                self._screen.setPixmap(QPixmap.fromImage(sel_canvas))
            else:
                self._show_placeholder()

    # ─── 画布比例约束 ───
    def set_aspect_ratio(self, ratio):
        """ratio: (w, h) tuple 或 None(默认-跟随视频尺寸)"""
        self._aspect_ratio = ratio
        if ratio is not None:
            self._auto_ratio_applied = True
        else:
            # "默认"模式：重置自动检测标志，下次渲染自动跟随视频尺寸
            self._auto_ratio_applied = False
        self._apply_ratio_bg_config()
        self._position_screen()

    def _position_screen(self):
        """根据 _aspect_ratio 在 screen_container 内定位 _screen。"""
        container = getattr(self, '_screen_container', None)
        if container is None or self._screen is None:
            return
        cw = container.width()
        ch = container.height()
        if cw <= 0 or ch <= 0:
            return
        ar = self._aspect_ratio
        if ar is None:
            # "默认"模式：跟随视频原始尺寸比例
            iw = getattr(self, '_clip_src_w', 0)
            ih = getattr(self, '_clip_src_h', 0)
            if iw > 0 and ih > 0:
                # 用视频原始比例在容器内做最佳适配
                scale = min(cw / iw, ch / ih)
                sw = int(iw * scale)
                sh = int(ih * scale)
                sx = (cw - sw) // 2
                sy = (ch - sh) // 2
                self._screen.setGeometry(sx, sy, sw, sh)
                self._canvas_w = sw
                self._canvas_h = sh
            else:
                # 尚无视频尺寸信息，填满容器
                self._screen.setGeometry(0, 0, cw, ch)
                self._canvas_w = cw
                self._canvas_h = ch
        else:
            ratio_w, ratio_h = ar
            if ratio_w <= 0 or ratio_h <= 0:
                self._screen.setGeometry(0, 0, cw, ch)
                self._canvas_w = cw
                self._canvas_h = ch
            else:
                # 计算最佳适配矩形（居中）
                scale = min(cw / ratio_w, ch / ratio_h)
                sw = int(ratio_w * scale)
                sh = int(ratio_h * scale)
                sx = (cw - sw) // 2
                sy = (ch - sh) // 2
                self._screen.setGeometry(sx, sy, sw, sh)
                self._canvas_w = sw
                self._canvas_h = sh
        # 画布尺寸变化后刷新显示
        self._async_fetch(self._current_sec)

    def _get_clip_src_size(self, clip):
        """返回片段的源文件尺寸 (w, h)。用 _cache_src_size 线程安全缓存。"""
        if clip is None:
            return 0, 0
        path = getattr(clip, 'source_path', '')
        cached = self._cache_src_size(path)
        if cached:
            return cached
        # 直接读取
        try:
            import cv2
            cap = cv2.VideoCapture(path)
            if cap.isOpened():
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                cap.release()
                if w > 0 and h > 0:
                    self._cache_src_size(path, (w, h))
                    return w, h
        except Exception:
            _log_exc()
        return 0, 0

    def _overlay_subtitles(self, qimg: QImage, active_subs: list, img_w: int, img_h: int) -> QImage:
        """在帧图像上叠加字幕（后台线程调用，仅 QPainter on QImage，不涉及 GUI widget）
        支持：归一化位置、字间距、行间距、描边、下划线、背景填充、逐词动画"""
        if not active_subs or img_w <= 0 or img_h <= 0:
            return qimg
        painter = QPainter(qimg)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        sec = self._current_sec
        try:
            from core.edit_engine import interpolate_keyframes
            _HAS_KF = True
        except ImportError:
            _HAS_KF = False
        for b in active_subs:
            text = getattr(b, 'text', '') or ''
            if not text.strip():
                continue
            # ── 读取字幕属性（字段名对齐 SubtitleBlock） ──
            # 填充字段 (注意：SubtitleBlock 用 fill_enabled，不用 bg_fill)
            has_fill = getattr(b, 'fill_enabled', False)
            bg_color = getattr(b, 'background_color', '') or '#000000'
            border_radius = getattr(b, 'border_radius', 0) or 0

            # 位置
            px = getattr(b, 'pos_x', None)
            py = getattr(b, 'pos_y', None)
            position = getattr(b, 'position', 'bottom') or 'bottom'
            margin_v = getattr(b, 'margin_v', 60) or 60
            # 归一化坐标优先；否则用 position + margin_v
            if px is None or py is None:
                # 默认位置映射
                pos_map = {'top': -0.85, 'center': 0.0, 'bottom': 0.85}
                px = 0.0
                py = pos_map.get(position, 0.85)
            px = float(px)
            py = float(py)

            # 字体 (注意：SubtitleBlock 用 color，不用 font_color)
            fs = getattr(b, 'font_size', 15) or 15
            fc = getattr(b, 'color', '#ffffff') or '#ffffff'
            family = getattr(b, 'font_family',
                             'Microsoft YaHei') or 'Microsoft YaHei'
            bold = getattr(b, 'font_bold', False)
            italic = getattr(b, 'font_italic', False)
            underline = getattr(b, 'font_underline', False)
            letter_sp = getattr(b, 'letter_spacing', 0) or 0
            line_sp = getattr(b, 'line_spacing', 0) or 0

            # 描边
            ow = getattr(b, 'outline_width', 0) or 0
            oc = getattr(b, 'outline_color', '#000000') or '#000000'

            # 逐词动画
            word_anim = getattr(b, 'word_animation', False)
            word_anim_dur = getattr(b, 'word_anim_duration', 0.15) or 0.15
            from_asr = getattr(b, 'from_asr', False)
            word_timings = getattr(b, 'word_timings', []) or []

            # 关键帧插值
            kf = getattr(b, 'keyframes', None) or {}
            rot = getattr(b, 'rotation', 0.0) or 0.0
            sc = getattr(b, 'scale', 1.0) or 1.0
            kf_applied = False
            if _HAS_KF and kf:
                try:
                    rel_t = sec - b.timeline_start
                    base = {"pos_x": px, "pos_y": py,
                        "font_size": fs, "rotation": rot, "scale": sc}
                    vals = interpolate_keyframes(b, kf, rel_t, base)
                    px = float(vals.get("pos_x", px))
                    py = float(vals.get("pos_y", py))
                    fs = int(vals.get("font_size", fs) * vals.get("scale", sc))
                    rot = float(vals.get("rotation", rot))
                    kf_applied = True
                except Exception:
                    logging.debug(
                        "subtitle keyframe interpolation error", exc_info=True)
            if not kf_applied:
                fs = max(6, int(fs * sc))  # 无关键帧也应用 scale

            # ── custom_width 实时换行：按像素宽度重排文字 ──
            cw_custom = getattr(b, 'custom_width', 0) or 0
            if cw_custom > 0:
                flat = text.replace('\n', '')
                wrap_font = QFont(family, fs)
                wrap_font.setBold(bold)
                wrap_font.setItalic(italic)
                wrap_fm = QFontMetrics(wrap_font)
                wrap_w = max(1, cw_custom)
                text = '\n'.join(self._wrap_text_pixel(flat, wrap_fm, wrap_w))

            # 归一化坐标 → 像素坐标
            cx = int((px + 1.0) / 2.0 * img_w)
            cy = int((py + 1.0) / 2.0 * img_h)

            # ── 逐词动画 vs 正常渲染 ──
            # 背景填充和逐词动画互斥
            # 字幕整体不透明度（0~1）；逐词动画自带淡入 alpha，二者相乘
            _op = self._clip_opacity(b)
            painter.save()
            if _op < 1.0:
                painter.setOpacity(_op)
            if word_anim and from_asr and not has_fill and text.strip():
                self._draw_subtitle_word_anim(painter, b, text, sec, cx, cy, img_w, img_h,
                                              fs, fc, family, bold, italic, underline,
                                              ow, oc, word_anim_dur, word_timings, letter_sp, line_sp)
            else:
                self._draw_subtitle_normal(painter, text, cx, cy, img_w, img_h,
                                           fs, fc, family, bold, italic, underline,
                                           ow, oc, letter_sp, line_sp,
                                           has_fill, bg_color, border_radius, rot)
            painter.restore()
        painter.end()
        return qimg

    def _draw_subtitle_normal(self, painter: QPainter, text: str,
                              cx: int, cy: int, img_w: int, img_h: int,
                              fs: int, fc: str, family: str,
                              bold: bool, italic: bool, underline: bool,
                              ow: int, oc: str, letter_sp: int, line_sp: int,
                              has_fill: bool, bg_color: str, border_radius: int,
                              rot: float = 0.0):
        """正常字幕渲染（支持字间距、行间距、下划线、背景填充、旋转）"""
        font = QFont(family, fs)
        font.setBold(bold)
        font.setItalic(italic)
        font.setUnderline(underline)
        if letter_sp > 0:
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letter_sp)
        painter.setFont(font)
        fm = QFontMetrics(font)

        # 多行支持
        lines = text.split('\n')
        line_h = fm.height() + line_sp
        total_h = line_h * len(lines) - \
                               line_sp if line_sp else fm.height() * len(lines)

        # 计算每行宽度（含字间距）
        line_widths = []
        for line in lines:
            if letter_sp > 0:
                w = fm.horizontalAdvance(
                    line) + letter_sp * (len(line) - 1) if len(line) > 1 else fm.horizontalAdvance(line)
            else:
                w = fm.horizontalAdvance(line)
            line_widths.append(w)
        max_w = max(line_widths) if line_widths else 0

        # 旋转：绕字幕中心点 (cx, cy) 旋转
        if rot != 0.0:
            painter.save()
            painter.translate(cx, cy)
            painter.rotate(rot)
            painter.translate(-cx, -cy)

        # 背景填充
        if has_fill:
            pad_h = 8
            pad_v = 6
            fill_x = cx - max_w // 2 - pad_h
            fill_y = cy - total_h // 2 - pad_v
            fill_w = max_w + pad_h * 2
            fill_h = total_h + pad_v * 2
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(bg_color))
            if border_radius > 0:
                painter.drawRoundedRect(
                    fill_x, fill_y, fill_w, fill_h, border_radius, border_radius)
            else:
                painter.drawRect(fill_x, fill_y, fill_w, fill_h)

        # 逐行绘制
        start_y = cy - total_h // 2
        for i, line in enumerate(lines):
            lw = line_widths[i]
            lx = cx - lw // 2
            ly = start_y + i * line_h
            if ow > 0:
                path = QPainterPath()
                path.addText(lx, ly + fm.ascent(), font, line)
                # 两遍绘制避免描边侵入文字内部使字色变暗
                # 第一遍：仅描边（drawPath 的 pen 半幅在内、半幅在外，覆盖掉再画填充）
                pen = QPen(QColor(oc), ow * 2)  # 双倍宽补偿：一半会被后续填充覆盖
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path)
                # 第二遍：填充文字颜色（覆盖掉侵入文字内部的描边部分）
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(fc))
                painter.drawPath(path)
            else:
                painter.setPen(QColor(fc))
                painter.drawText(lx, ly + fm.ascent(), line)

        if rot != 0.0:
            painter.restore()

    def _draw_subtitle_word_anim(self, painter: QPainter, b, text: str, sec: float,
                                 cx: int, cy: int, img_w: int, img_h: int,
                                 fs: int, fc: str, family: str,
                                 bold: bool, italic: bool, underline: bool,
                                 ow: int, oc: str, word_dur: float, word_timings: list,
                                 letter_sp: int, line_sp: int):
        """逐词动画字幕渲染：单屏只显示一个词，居中淡入切换。
        中文/日文/韩文文本按字切分（无空格分隔时），英文按空格分词。"""
        # CJK 兼容：无空格文本按字切分（如"你好世界"→["你","好","世","界"]）
        words = text.split()
        if len(words) <= 1:
            # 尝试按字切分（对 CJK 文本有效）
            import re as _re
            # 匹配 CJK 字符、英文单词、标点等
            char_words = _re.findall(
                r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]|[^\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af\s]+|\S', text)
            if char_words and len(char_words) > 1:
                words = char_words
        if not words:
            return

        rel_t = sec - b.timeline_start
        total_dur = b.timeline_end - b.timeline_start

        # 计算每个词的显示时段
        if word_timings and len(word_timings) == len(words):
            # 有精确时间戳 → 每个词在 [word_timings[i], next_word_start] 显示
            word_intervals = []
            for i, start_t in enumerate(word_timings):
                end_t = word_timings[i+1] if i+ \
                    1 < len(word_timings) else total_dur
                word_intervals.append((start_t, end_t, words[i]))
        else:
            # 等分时间
            per_word = total_dur / len(words)
            word_intervals = [(i * per_word, (i+1) * per_word, words[i])
                               for i in range(len(words))]

        # 找到当前显示的词
        current_word = ""
        anim_t = 0.0  # 相对当前词的动画时间
        for start_t, end_t, word in word_intervals:
            if start_t <= rel_t < end_t:
                current_word = word
                anim_t = rel_t - start_t
                break

        if not current_word:
            return

        # 淡入动画：前 word_dur 秒从透明到不透明
        alpha = min(1.0, anim_t / max(word_dur, 0.01))

        font = QFont(family, fs)
        font.setBold(bold)
        font.setItalic(italic)
        font.setUnderline(underline)
        if letter_sp > 0:
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letter_sp)
        painter.setFont(font)
        fm = QFontMetrics(font)
        word_w = fm.horizontalAdvance(current_word)
        word_h = fm.height()

        # 应用透明度
        fc_q = QColor(fc)
        fc_q.setAlpha(int(255 * alpha))
        oc_q = QColor(oc)
        oc_q.setAlpha(int(255 * alpha))

        tx = cx - word_w // 2
        ty = cy - word_h // 2
        if ow > 0:
            path = QPainterPath()
            path.addText(tx, ty + fm.ascent(), font, current_word)
            # 两遍绘制避免描边侵入字色
            pen = QPen(oc_q, ow * 2)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fc_q)
            painter.drawPath(path)
        else:
            painter.setPen(fc_q)
            painter.drawText(tx, ty + fm.ascent(), current_word)

    def _compose_from_raw(self, cw: int, ch: int):
        """从缓存的原始帧重新渲染画布（缩放/拖拽时调用，避免 _last_frame_image 旧比例残影）。
        返回 QImage 或 None（无可用的原始帧时）。
        """
        try:
            raw = getattr(self, '_last_raw_img', None)
            if raw is None:
                return None
            # 双重检查：isNull + 尺寸有效性
            try:
                if raw.isNull() or raw.width() <= 0 or raw.height() <= 0:
                    return None
            except Exception:
                # C++ 对象可能已被销毁
                self._last_raw_img = None
                return None

            if cw <= 0 or ch <= 0:
                return None

            canvas = self._alloc_canvas(cw, ch)
            if canvas.isNull():
                return None
            # 每帧强制重填背景色，防止拖拽视频时旧帧像素残留 → 残影
            _bg = self._canvas_bg_color or '#000000'
            if isinstance(_bg, str):
                from PyQt6.QtGui import QColor as _QColor
                _bg = _QColor(_bg)
            canvas.fill(_bg)

            iw = getattr(self, '_clip_src_w', 0) or raw.width()
            ih = getattr(self, '_clip_src_h', 0) or raw.height()
            if iw <= 0 or ih <= 0:
                return canvas

            # 渲染所有可见视频轨（包含选中片段）
            painter = QPainter(canvas)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

            # ── 1. 主视频轨（track 0，画布背景层）──
            base_s = min(cw / iw, ch / ih)
            clip = self._get_active_clip()
            if clip:
                s = getattr(clip, 'scale', 1.0) or 1.0
                px = getattr(clip, 'pos_x', 0.0) or 0.0
                py = getattr(clip, 'pos_y', 0.0) or 0.0
                rot = getattr(clip, 'rotation', 0.0) or 0.0
                blur = getattr(clip, 'blur_radius', 0.0) or 0.0
                kf = getattr(clip, 'keyframes', None) or {}
                if kf and clip.timeline_start is not None:
                    rel_t = (self._current_sec or 0.0) - clip.timeline_start
                    base_vals = {"scale": s, "pos_x": px, "pos_y": py,
                        "rotation": rot, "blur_radius": blur}
                    from core.edit_engine import interpolate_keyframes
                    vals = interpolate_keyframes(clip, kf, rel_t, base_vals)
                    s = vals["scale"]
                    px = vals["pos_x"]; py = vals["pos_y"]; rot = vals["rotation"]; blur = vals["blur_radius"]
                total_s = base_s * s
                new_w = max(1, int(iw * total_s))
                new_h = max(1, int(ih * total_s))
                # 直接用 QImage，不转 QPixmap（保留 alpha 通道）
                scaled_img = raw.scaled(
                    new_w, new_h, Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                if rot != 0.0:
                    from PyQt6.QtGui import QTransform
                    t = QTransform().translate(scaled_img.width() / 2, scaled_img.height() / 2)
                    t.rotate(rot)
                    t.translate(-scaled_img.width() / 2, - \
                                scaled_img.height() / 2)
                    scaled_img = scaled_img.transformed(
                        t, Qt.TransformationMode.SmoothTransformation)
                if blur > 0.5:
                    scaled_img = self._blur_qimage(scaled_img, blur)
                # 绿幕抠像（主视频轨）
                scaled_img = self._apply_chroma_key(clip, scaled_img)
                ox = (cw - scaled_img.width()) // 2 + int(px)
                oy = (ch - scaled_img.height()) // 2 + int(py)
                self._draw_video_layer(painter, clip, scaled_img, ox, oy)
            else:
                # 无主 clip 时仍显示原始帧（全画布适配）
                scaled_img = raw.scaled(
                    cw, ch, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                ox = (cw - scaled_img.width()) // 2
                oy = (ch - scaled_img.height()) // 2
                painter.drawImage(ox, oy, scaled_img)
            painter.end()

            # ── 2. PiP 叠加层 ──
            raw_overlays = getattr(self, '_last_raw_overlays', [])
            if raw_overlays:
                ov_painter = QPainter(canvas)
                ov_painter.setRenderHint(
                    QPainter.RenderHint.SmoothPixmapTransform)
                for ov_clip, ov_img in raw_overlays:
                    try:
                        if ov_img is None:
                            continue
                        try:
                            if ov_img.isNull() or ov_img.width() <= 0:
                                continue
                        except Exception:
                            continue
                        ov_iw, ov_ih = self._cache_src_size(
                            ov_clip.source_path) or (ov_img.width(), ov_img.height())
                        if ov_iw <= 0 or ov_ih <= 0:
                            continue
                        ov_s = getattr(ov_clip, 'scale', 1.0) or 1.0
                        ov_px = getattr(ov_clip, 'pos_x', 0.0) or 0.0
                        ov_py = getattr(ov_clip, 'pos_y', 0.0) or 0.0
                        ov_rot = getattr(ov_clip, 'rotation', 0.0) or 0.0
                        ov_blur = getattr(ov_clip, 'blur_radius', 0.0) or 0.0
                        ov_base = min(cw / ov_iw, ch / ov_ih)
                        ov_ts = ov_base * ov_s
                        ov_nw = max(1, int(ov_iw * ov_ts))
                        ov_nh = max(1, int(ov_ih * ov_ts))
                        # 缩放（QImage.scaled() 保留 alpha 通道，不转 QPixmap）
                        ov_img = ov_img.scaled(
                            ov_nw, ov_nh, Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation
                        )
                        # 兜底：确保带 alpha 的叠加帧始终为 RGBA8888 格式
                        if ov_img.format() != QImage.Format.Format_RGBA8888 and ov_img.hasAlphaChannel():
                            ov_img = ov_img.convertToFormat(
                                QImage.Format.Format_RGBA8888)
                        # 旋转（QImage.transformed() 保留格式）
                        if ov_rot != 0.0:
                            from PyQt6.QtGui import QTransform
                            t = QTransform().translate(ov_img.width() / 2, ov_img.height() / 2)
                            t.rotate(ov_rot)
                            t.translate(-ov_img.width() / 2, - \
                                        ov_img.height() / 2)
                            ov_img = ov_img.transformed(
                                t, Qt.TransformationMode.SmoothTransformation)
                        # 模糊（QImage 路径，不转 QPixmap，保留 alpha）
                        if ov_blur > 0.5:
                            ov_img = self._blur_qimage(ov_img, ov_blur)
                        # 绿幕抠像
                        ov_img = self._apply_chroma_key(ov_clip, ov_img)
                        ov_ox = (cw - ov_img.width()) // 2 + int(ov_px)
                        ov_oy = (ch - ov_img.height()) // 2 + int(ov_py)
                        self._draw_video_layer(ov_painter, ov_clip, ov_img, ov_ox, ov_oy)
                    except Exception:
                        logging.debug(
                            "overlay pixmap paint error", exc_info=True)
                ov_painter.end()

            return canvas
        except Exception:
            import traceback
            import sys
            traceback.print_exc(file=sys.stderr)
            return None

    def _video_screen_rect(self, clip=None, src_w=0, src_h=0):
        """返回视频在画布上的渲染区域 (ox, oy, w, h)，用于命中检测。None=不可用"""
        if clip is None:
            clip = self._get_active_clip()
        if clip is None:
            return None
        # 优先用每片段缓存尺寸（修复多轨道四角缩放）
        if not src_w or not src_h:
            cw_s, ch_s = self._get_clip_src_size(clip)
            if cw_s > 0 and ch_s > 0:
                src_w, src_h = cw_s, ch_s
        iw = src_w or getattr(self, '_clip_src_w', 0)
        ih = src_h or getattr(self, '_clip_src_h', 0)
        if iw <= 0 or ih <= 0:
            # 尝试从 pending_frame 或 clip 自身获取尺寸
            img = self._pending_frame
            if img is None or img.isNull():
                # 最后尝试：从 clip 的源文件推断（尝试一次 OpenCV）
                try:
                    import cv2
                    cap = cv2.VideoCapture(clip.source_path)
                    iw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    ih = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    cap.release()
                except Exception:
                    logging.debug(
                        "_cache_src_size cv2 fallback error", exc_info=True)
                    return None
                if iw <= 0 or ih <= 0:
                    return None
            else:
                iw, ih = img.width(), img.height()
        cw = getattr(self, '_canvas_w', 0) or self._screen.width()
        ch = getattr(self, '_canvas_h', 0) or self._screen.height()
        if cw <= 0 or ch <= 0:
            return None
        s = getattr(clip, 'scale', 1.0) or 1.0
        px = getattr(clip, 'pos_x', 0.0) or 0.0
        py = getattr(clip, 'pos_y', 0.0) or 0.0
        fit_w = cw / iw
        fit_h = ch / ih
        base_scale = min(fit_w, fit_h)
        total_scale = base_scale * s
        new_w = max(1, int(iw * total_scale))
        new_h = max(1, int(ih * total_scale))
        rot = getattr(clip, 'rotation', 0.0) or 0.0
        if rot != 0.0:
            import math
            rad = math.radians(rot)
            cos_a = abs(math.cos(rad))
            sin_a = abs(math.sin(rad))
            rw = max(1, int(new_w * cos_a + new_h * sin_a))
            rh = max(1, int(new_w * sin_a + new_h * cos_a))
        else:
            rw, rh = new_w, new_h
        ox = (cw - rw) // 2 + int(px)
        oy = (ch - rh) // 2 + int(py)
        return (ox, oy, rw, rh)

    def _hit_handle(self, x, y, rect):
        """检测 (x,y) 是否落在视频矩形某个把手上。
        返回 ('rotate',) | ('resize', 方向) | ('move',) | None"""
        ox, oy, w, h = rect
        # 动态句柄大小：小视频自动缩句柄，上限 40% 维度以确保始终有移动区域
        HANDLE = max(7, min(14, int(min(w, h) * 0.4)))

        # 0. 旋转把手（顶部中央圆圈），距视频上边 20px
        rot_cx = ox + w // 2
        rot_cy = oy - 20
        if abs(x - rot_cx) <= 14 and abs(y - rot_cy) <= 14:
            return ("rotate",)

        # 1. 判断是否在矩形内部
        inside = ox <= x <= ox + w and oy <= y <= oy + h

        # 2. 四角缩放把手
        corners = {
            "NW": (ox, oy),
            "NE": (ox + w, oy),
            "SW": (ox, oy + h),
            "SE": (ox + w, oy + h),
        }
        for name, (cx, cy) in corners.items():
            if abs(x - cx) <= HANDLE and abs(y - cy) <= HANDLE:
                return ("resize", name)

        # 3. 上下边（非角区）→ 移动
        near_corner_l = x <= ox + HANDLE
        near_corner_r = x >= ox + w - HANDLE
        near_corner_t = y <= oy + HANDLE
        near_corner_b = y >= oy + h - HANDLE
        on_top = y <= oy + HANDLE
        on_bottom = y >= oy + h - HANDLE
        if (on_top and not near_corner_l and not near_corner_r) or \
           (on_bottom and not near_corner_l and not near_corner_r):
               return ("move",)
        # 垂直边也当移动（方便拖动到画布中央）
        on_left = x <= ox + HANDLE
        on_right = x >= ox + w - HANDLE
        if (on_left or on_right) and not (near_corner_t or near_corner_b):
            return ("move",)

        # 4. 矩形内部（非边缘/非角落）→ 移动
        #    对于极小视频这至关重要：角落句柄可能覆盖整个矩形，
        #    但角落检查在前，此处作为兜底确保中心区域可移动
        if inside:
            return ("move",)

        return None

    # ─── 画布背景配置 ───
    def _show_canvas_context_menu(self, event):
        """右键画布 → 背景色/虚化菜单"""
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self._screen)
        menu.setStyleSheet(
            "QMenu{background:#2a2a2a;color:#ccc;border:1px solid #444;}"
            "QMenu::item:selected{background:#3d8ef8;}"
            "QMenu::separator{background:#444;margin:4px 8px;height:1px;}"
        )

        solid_act = menu.addAction("🎨 纯色背景…")
        solid_act.triggered.connect(self._pick_solid_bg)

        black_act = menu.addAction("⬛ 黑色背景（默认）")
        black_act.triggered.connect(
            lambda: self._set_canvas_bg("#000000"))

        menu.addSeparator()

        # 视频变换
        center_act = menu.addAction("📌 居中")
        center_act.triggered.connect(self._center_video)
        reset_act = menu.addAction("🔄 重置缩放/旋转")
        reset_act.triggered.connect(self._reset_video_transform)

        menu.addSeparator()

        pos = event.globalPosition().toPoint() if hasattr(
            event, 'globalPosition') else event.globalPos()
        menu.exec(pos)

    def _pick_solid_bg(self):
        """弹出颜色选择器选纯色"""
        from PyQt6.QtWidgets import QColorDialog
        cur = QColor(getattr(self, '_canvas_bg_color', '#000000') or '#000000')
        col = QColorDialog.getColor(cur, self, "选择画布背景色")
        if col.isValid():
            self._set_canvas_bg(col.name())

    def _center_video(self):
        """视频归中"""
        clip = self._get_active_clip()
        if clip:
            self.tl._save_history()
            clip.pos_x = 0.0
            clip.pos_y = 0.0
            self.tl.changed.emit()
            self._async_fetch(self._current_sec)

    def _reset_video_transform(self):
        """重置缩放/旋转"""
        clip = self._get_active_clip()
        if clip:
            self.tl._save_history()
            clip.scale = 1.0
            clip.rotation = 0.0
            clip.pos_x = 0.0
            clip.pos_y = 0.0
            self.tl.changed.emit()
            self._async_fetch(self._current_sec)

    def _set_canvas_bg(self, color: str):
        """设置画布背景色并保存到比例配置"""
        self._canvas_bg_color = color
        cur_ratio = self._aspect_ratio or ("默认", "默认")
        self._ratio_bg_config[cur_ratio] = {"color": color}
        # 清掉帧缓存，确保换色后重新合成/重绘画布：
        # 空时间线也会按新背景色刷新，不会因复用 _last_frame_image 而背景色不变
        self._last_frame_image = None
        self._last_raw_img = None
        self._last_raw_overlays = []
        self._async_fetch(self._current_sec)

    def _apply_ratio_bg_config(self):
        """切换画布比例时恢复保存的背景色"""
        cur_ratio = self._aspect_ratio or ("默认", "默认")
        if cur_ratio in self._ratio_bg_config:
            cfg = self._ratio_bg_config[cur_ratio]
            self._canvas_bg_color = cfg.get("color", "#000000")

    # ─── 左键点击空白画布弹出颜色选择器 ───
    _BG_PRESETS = [
        ("#000000", "纯黑"), ("#1a1a2e", "深蓝黑"), ("#0d1117", "GitHub黑"),
        ("#222222", "深灰"), ("#333333", "中灰"), ("#555555", "灰"),
        ("#888888", "浅灰"), ("#cccccc", "白灰"), ("#ffffff", "纯白"),
        ("#0a1628", "深海蓝"), ("#1b2838", "深蓝"), ("#1a3a2a", "深绿"),
        ("#2a1a3a", "深紫"), ("#3a2a1a", "深棕"), ("#4a0a0a", "深红"),
        ("#ffeb3b", "亮黄"), ("#4caf50", "绿"), ("#2196f3", "蓝"),
        ("#e91e63", "玫红"), ("#00bcd4", "青"), ("#8bc34a", "黄绿"),
    ]

    def _show_bg_color_popup(self, event):
        """在点击位置弹出紧凑型画布背景色选择面板"""
        from PyQt6.QtWidgets import QFrame, QGridLayout, QLabel, QPushButton, QVBoxLayout, QHBoxLayout
        popup = QFrame(self._screen, Qt.WindowType.Popup |
                       Qt.WindowType.FramelessWindowHint)
        popup.setStyleSheet(
            "QFrame#bg_popup { background:#2a2a2a; border:2px solid #3d8ef8; border-radius:8px; }"
        )
        popup.setObjectName("bg_popup")

        outer = QVBoxLayout(popup)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)

        title = QLabel("🎨 画布背景色")
        title.setStyleSheet(
            "color:#3d8ef8; font-size:12px; font-weight:bold; border:none;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(title)

        # 预设色块网格
        grid = QGridLayout()
        grid.setSpacing(3)
        cols = 7
        cur_color = getattr(self, '_canvas_bg_color', '#000000') or '#000000'

        def make_swatch(hex_color: str, tooltip: str):
            btn = QPushButton()
            btn.setFixedSize(26, 26)
            btn.setToolTip(tooltip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            border = "2px solid #00eaff" if hex_color == cur_color else "1px solid #555"
            btn.setStyleSheet(
                f"QPushButton {{ background:{hex_color}; border:{border}; border-radius:3px; }}"
                f"QPushButton:hover {{ border:2px solid #fff; }}"
            )
            btn.clicked.connect(lambda checked, c=hex_color: (
                self._set_canvas_bg(c), popup.close()))
            return btn

        for i, (hex_c, name) in enumerate(self._BG_PRESETS):
            btn = make_swatch(hex_c, name)
            grid.addWidget(btn, i // cols, i % cols)

        outer.addLayout(grid)

        # 自定义颜色 + 关闭按钮
        btn_row = QHBoxLayout()
        custom_btn = QPushButton("🎨 自定义…")
        custom_btn.setFixedHeight(28)
        custom_btn.setStyleSheet(
            "QPushButton { background:#333; color:#ccc; border:1px solid #555; border-radius:4px; font-size:11px; }"
            "QPushButton:hover { background:#444; color:#fff; border-color:#3d8ef8; }"
        )
        custom_btn.clicked.connect(
            lambda: (self._pick_solid_bg(), popup.close()))
        btn_row.addWidget(custom_btn)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet(
            "QPushButton { background:transparent; color:#888; border:1px solid #444; border-radius:4px; font-size:14px; }"
            "QPushButton:hover { background:#444; color:#ff6b6b; border-color:#ff6b6b; }"
        )
        close_btn.clicked.connect(popup.close)
        btn_row.addWidget(close_btn)
        outer.addLayout(btn_row)

        popup.adjustSize()
        # 定位在点击位置附近
        gp = event.globalPosition().toPoint() if hasattr(
            event, 'globalPosition') else event.globalPos()
        popup.move(gp.x() - popup.width() // 2, gp.y() - \
                   popup.height() - 10 if gp.y() > 200 else gp.y() + 20)
        popup.show()

    # ─── 画布字幕交互（直接画布模式：不依赖 QFrame overlay） ───
    _SUB_HANDLE = 12  # 手柄命中区半边长（像素），视觉手柄比这小
    _SUB_PAD = 12     # 选择框边距

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if obj is getattr(self, '_screen_container', None):
            if event.type() == QEvent.Type.Resize:
                self._position_screen()
            return False
        if hasattr(self, '_screen') and obj is self._screen:
            if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
                pass  # 静默处理
            if event.type() == QEvent.Type.MouseButtonPress:
                self._on_screen_press(event)
            elif event.type() == QEvent.Type.MouseMove:
                self._on_screen_move(event)
            elif event.type() == QEvent.Type.MouseButtonRelease:
                if self._sub_interaction is not None:
                    self.tl.overlays_changed.emit()
                    self._sub_interaction = None
                if self._resize_handle is not None or self._dragging_video is not None or self._rotation_active:
                    # 拖拽/缩放/旋转释放：snap 到边界 + 标记工程脏
                    if self._dragging_video is not None and self._selected_video_clip is not None:
                        clip = self._selected_video_clip
                        snap_x, snap_y = self._snap_video_position(
                            clip.pos_x, clip.pos_y, getattr(clip, 'scale', 1.0), clip)
                        clip.pos_x, clip.pos_y = snap_x, snap_y
                    self.tl.changed.emit()  # 标记工程脏，触发属性面板刷新
                    self._resize_handle = None
                    self._dragging_video = None
                    self._rotation_active = False
                    self._drag_snap_saved = False
                    self._async_fetch(self._current_sec)  # 刷新帧去除拖拽手柄
            elif event.type() == QEvent.Type.MouseButtonDblClick:
                self._on_screen_double_click(event)
            return False
        return super().eventFilter(obj, event)

    def _screen_to_norm(self, x: int, y: int):
        sw, sh = self._screen.width(), self._screen.height()
        if sw <= 0 or sh <= 0:
            return 0.0, 0.0
        nx = x / sw * 2.0 - 1.0
        ny = y / sh * 2.0 - 1.0
        return max(-1.0, min(1.0, nx)), max(-1.0, min(1.0, ny))

    def _get_active_clip(self) -> Optional[VideoClip]:
        tl = getattr(self, 'tl', None)
        if tl is None or not hasattr(tl, 'video_tracks'):
            return None
        if tl.video_tracks:
            for c in tl.video_tracks[0]:
                if not getattr(c, "visible", True):
                    continue
                if c.timeline_start <= (self._current_sec or 0.0) < c.timeline_start + c.duration:
                    return c
        return None

    def _get_all_active_clips(self) -> list:
        result = []
        tl = getattr(self, 'tl', None)
        if tl is None or not hasattr(tl, 'video_tracks'):
            return result
        for ti, track in enumerate(tl.video_tracks):
            for c in track:
                if not getattr(c, "visible", True):
                    continue
                if c.timeline_start <= (self._current_sec or 0.0) < c.timeline_start + c.duration:
                    result.append((ti, c))
                    break
        return result

    def _snap_video_position(self, pos_x: float, pos_y: float, scale: float = 1.0, clip=None):
        cw = getattr(self, '_canvas_w', 0) or self._screen.width() or 640
        ch = getattr(self, '_canvas_h', 0) or self._screen.height() or 360
        # 优先使用传入 clip 的源尺寸（多轨道兼容）
        if clip is not None:
            csw, csh = self._get_clip_src_size(clip)
            iw = csw or getattr(self, '_clip_src_w', 0)
            ih = csh or getattr(self, '_clip_src_h', 0)
        else:
            iw = getattr(self, '_clip_src_w', 0)
            ih = getattr(self, '_clip_src_h', 0)
        if iw <= 0 or ih <= 0:
            img = self._pending_frame
            if img is not None and not img.isNull():
                iw, ih = img.width(), img.height()
        if iw <= 0 or ih <= 0:
            return (pos_x, pos_y)
        base_scale = min(cw / max(iw, 1), ch / max(ih, 1))
        total_scale = base_scale * max(scale, 0.1)
        nw = max(1, int(iw * total_scale))
        nh = max(1, int(ih * total_scale))
        # 旋转后的外接矩形可能更大
        rot = getattr(clip, 'rotation', 0.0) if clip else 0.0
        if rot:
            import math
            rad = math.radians(rot)
            cos_a = abs(math.cos(rad))
            sin_a = abs(math.sin(rad))
            nw_orig, nh_orig = nw, nh
            nw = max(1, int(nw_orig * cos_a + nh_orig * sin_a))
            nh = max(1, int(nw_orig * sin_a + nh_orig * cos_a))
        # 仅当视频放大超出画布时才启用吸附
        # 视频在画布内时不做任何吸附，让用户自由放置
        if nw <= cw and nh <= ch:
            return (pos_x, pos_y)

        ox = (cw - nw) // 2 + pos_x
        oy = (ch - nh) // 2 + pos_y
        Left, Right = ox, ox + nw
        Top, Bottom = oy, oy + nh
        thr = self._snap_threshold
        snap_px, snap_py = pos_x, pos_y
        # 水平：左右边缘贴到画布边界时吸附
        if nw >= cw - thr:
            if -thr < Left < thr:                       # 左边缘靠近画布左边
                snap_px = -(cw - nw) / 2
            elif -thr < cw - Right < thr:               # 右边缘靠近画布右边
                snap_px = (cw - nw) / 2
        # 垂直：上下边缘贴到画布边界时吸附
        if nh >= ch - thr:
            if -thr < Top < thr:                        # 上边缘靠近画布上边
                snap_py = -(ch - nh) / 2
            elif -thr < ch - Bottom < thr:              # 下边缘靠近画布下边
                snap_py = (ch - nh) / 2
        return (snap_px, snap_py)

    # ─── 字幕包围盒计算 ───
    def _compute_sub_bbox(self, block) -> Tuple[float, float, float, float] | None:
        """计算字幕块在画布上的包围盒 (x, y, w, h)，含选择边距。返回 None 表示不可用。"""
        sw = self._screen.width()
        sh = self._screen.height()
        # fallback: 用 canvas_w/canvas_h 或容器尺寸
        if sw <= 0 or sh <= 0:
            sw = getattr(self, '_canvas_w', 0) or 0
            sh = getattr(self, '_canvas_h', 0) or 0
        if sw <= 0 or sh <= 0:
            container = getattr(self, '_screen_container', None)
            if container:
                sw = container.width()
                sh = container.height()
        if sw <= 0 or sh <= 0:
            return None
        scale = getattr(block, 'scale', 1.0) or 1.0
        fs = max(6, int((getattr(block, 'font_size', 15) or 15) * scale))
        family = getattr(block, 'font_family',
                         'Microsoft YaHei') or 'Microsoft YaHei'
        text = getattr(block, 'text', '字幕文本') or '字幕文本'
        font = QFont(family, fs)
        font.setBold(getattr(block, 'font_bold', False))
        font.setItalic(getattr(block, 'font_italic', False))
        fm = QFontMetrics(font)
        lines = text.split('\n')
        # ── custom_width 实时换行：重算 lines + 框宽以匹配渲染 ──
        cw_custom = getattr(block, 'custom_width', 0) or 0
        if cw_custom > 0:
            flat = text.replace('\n', '')
            lines = self._wrap_text_pixel(flat, fm, max(1, cw_custom))
            max_w = cw_custom  # 框宽 = 自定义宽度
        else:
            widths = [fm.horizontalAdvance(ln) for ln in lines]
            max_w = max(widths) if widths else 0
        px = getattr(block, 'pos_x', None)
        py = getattr(block, 'pos_y', None)
        # 归一化坐标优先；否则用 position + margin_v（与 _overlay_subtitles 保持一致）
        if px is None or py is None:
            position = getattr(block, 'position', 'bottom') or 'bottom'
            pos_map = {'top': -0.85, 'center': 0.0, 'bottom': 0.85}
            px = 0.0
            py = pos_map.get(position, 0.85)
        px = float(px)
        py = float(py)
        cx = int((px + 1.0) / 2.0 * sw)
        cy = int((py + 1.0) / 2.0 * sh)
        if hasattr(self, '_selected_sub') and self._selected_sub is not None:
            if id(block) == id(self._selected_sub):
                pass  # bbox debug removed
        # total_h = 所有行的总高度（含行间距），与 _draw_subtitle_normal 一致
        line_sp = getattr(block, 'line_spacing', 0) or 0
        line_h = fm.height() + line_sp
        total_h = line_h * len(lines) - \
                               line_sp if line_sp else fm.height() * len(lines)
        pad = self._SUB_PAD
        x = cx - max_w // 2 - pad
        y = cy - total_h // 2 - pad
        w = max_w + pad * 2
        h = total_h + pad * 2
        return (float(x), float(y), float(w), float(h))

    def _hit_test_subtitle(self, x: int, y: int):
        """检测 (x,y) 是否命中当前活跃字幕，返回 (SubtitleBlock, interaction_kind | None)。
        interaction_kind: "resize_nw/ne/sw/se" | "width_left/right" | "move" | None（命中文字区=选中）"""
        sec = self._current_sec
        hs = self._SUB_HANDLE
        total_checked = 0
        for track in reversed(self.tl.subtitle_tracks):
            for b in track:
                total_checked += 1
                vis = getattr(b, "visible", True)
                in_range = b.timeline_start <= sec < b.timeline_end
                if not vis:
                    continue
                if not in_range:
                    continue
                bbox = self._compute_sub_bbox(b)
                if bbox is None:
                    continue
                bx, by, bw, bh = bbox
                in_bbox = bx <= x <= bx + bw and by <= y <= by + bh
                if not in_bbox:
                    continue
                # 命中 → 判断手柄区域
                on_left = x <= bx + hs
                on_right = x >= bx + bw - hs
                on_top = y <= by + hs
                on_bottom = y >= by + bh - hs
                # 四角 → 缩放（优先，用完整 hs 范围）
                if on_left and on_top:
                    return (b, "resize_nw")
                if on_right and on_top:
                    return (b, "resize_ne")
                if on_left and on_bottom:
                    return (b, "resize_sw")
                if on_right and on_bottom:
                    return (b, "resize_se")
                # 左右边（缩小角排除区，让侧边更容易抓到）
                corner_margin = hs // 2  # 角只用一半高度，侧边范围更大
                near_corner_top = y <= by + corner_margin
                near_corner_bottom = y >= by + bh - corner_margin
                if on_left and not near_corner_top and not near_corner_bottom:
                    return (b, "width_left")
                if on_right and not near_corner_top and not near_corner_bottom:
                    return (b, "width_right")
                # 上下边（非角区，两侧角只用 half hs）→ 移动
                near_corner_left = x <= bx + corner_margin
                near_corner_right = x >= bx + bw - corner_margin
                if (on_top and not near_corner_left and not near_corner_right) or \
                   (on_bottom and not near_corner_left and not near_corner_right):
                       return (b, "move")
                # 上下边角区也当作移动（已不是四角缩放）
                if on_top or on_bottom:
                    return (b, "move")
                # 内部文字区 → 可拖拽移动
                return (b, "move")
        return None

    def _on_screen_press(self, event):
        x, y = event.pos().x(), event.pos().y()

        # ── 预览模式下点击画布 → 退出预览 ──
        if self._preview_active:
            self.stop_preview()
            return

        # ── 字幕编辑模式下点击画布 → 退出编辑（保存），不论点击什么位置 ──
        if self._editing_sub is not None:
            self._hide_sub_editor(save=True)
            # 不 return，让后续逻辑正常处理（可点选新字幕/视频/空白）

        # ── 右键 → 暂停播放 + 弹出画布菜单 ──
        if event.button() == Qt.MouseButton.RightButton:
            self.pause_requested.emit()
            self._show_canvas_context_menu(event)
            return

        # ── 字幕命中检测 ──
        hit_result = self._hit_test_subtitle(x, y)
        if hit_result is not None:
            hit_sub, interaction = hit_result
            # 先隐藏编辑器
            self._hide_sub_editor(save=True)
            self._selected_sub = hit_sub
            self._selected_video_clip = None
            self._dragging_video = None
            self._resize_handle = None
            self._drag_snap_saved = False
            if interaction is not None:
                # 拖拽/缩放模式：捕获拖拽前快照用于撤回
                self.tl._save_history()
                # 拖拽/缩放模式
                self._sub_interaction = interaction
                self._sub_drag_start_xy = (x, y)
                # 获取有效位置（考虑 position 回退，与 _overlay_subtitles 一致）
                spx = getattr(hit_sub, 'pos_x', None)
                spy = getattr(hit_sub, 'pos_y', None)
                if spx is None or spy is None:
                    pos = getattr(hit_sub, 'position', 'bottom') or 'bottom'
                    pos_map = {'top': -0.85, 'center': 0.0, 'bottom': 0.85}
                    if spx is None:
                        spx = 0.0
                    if spy is None: spy = pos_map.get(pos, 0.85)
                self._sub_drag_start_pos = (float(spx), float(spy))
                self._sub_drag_start_width = getattr(
                    hit_sub, 'custom_width', 0) or 0
                self._sub_drag_start_scale = getattr(
                    hit_sub, 'scale', 1.0) or 1.0
            else:
                # 单击文字区 → 选中（显示边框）
                self._sub_interaction = None
            self.video_selected.emit(hit_sub, "subtitle")
            self._set_seq_state(None)
            # 立即刷新显示选中框（不依赖异步帧）
            self._flush_frame(force=True)
            return

        # ── 视频命中检测（从顶层往底层检测，确保上层视频优先） ──
        all_clips = self._get_all_active_clips()
        for ti, c in reversed(all_clips):
            src_size = self._cache_src_size(c.source_path) or (0, 0)
            csw, csh = src_size[0], src_size[1]
            iw = csw or getattr(self, '_clip_src_w', 0)
            ih = csh or getattr(self, '_clip_src_h', 0)
            if iw <= 0 or ih <= 0:
                continue
            rect = self._video_screen_rect(c, iw, ih)
            if rect:
                ox, oy, rw, rh = rect
                # 扩展命中框：包含把手/旋转把手区域（上下左右各扩展 HANDLE 像素）
                HANDLE_MARGIN = max(7, min(14, int(min(rw, rh) * 0.4)))
                if (ox - HANDLE_MARGIN <= x <= ox + rw + HANDLE_MARGIN and
                        oy - HANDLE_MARGIN <= y <= oy + rh + HANDLE_MARGIN) or \
                   (abs(x - (ox + rw // 2)) <= 14 and oy - 30 <= y <= oy + HANDLE_MARGIN):
                    self._selected_sub = None
                    handle = self._hit_handle(x, y, rect)
                    if handle:
                        h_type = handle[0]
                        self._selected_video_clip = c
                        self._dragging_video = c
                        self._drag_snap_saved = False
                        self._resize_handle = None
                        self._rotation_active = False
                        if h_type == "rotate":
                            self._rotation_active = True
                            self._rotation_start_rot = getattr(
                                c, 'rotation', 0.0) or 0.0
                            cx = ox + rw // 2
                            cy = oy + rh // 2
                            import math
                            self._rotation_start_angle = math.atan2(
                                y - cy, x - cx)
                            self._rotation_center_xy = (cx, cy)
                        elif h_type == "resize":
                            # "NW"/"NE"/"SW"/"SE"
                            self._resize_handle = handle[1]
                            self._resize_start_xy = (x, y)
                            self._resize_start_scale = getattr(
                                c, 'scale', 1.0) or 1.0
                            self._resize_start_pos_x = getattr(
                                c, 'pos_x', 0.0) or 0.0
                            self._resize_start_pos_y = getattr(
                                c, 'pos_y', 0.0) or 0.0
                            ox2, oy2, rw2, rh2 = rect
                            self._resize_center_xy = (
                                ox2 + rw2 // 2, oy2 + rh2 // 2)
                        else:
                            # "move" — 拖拽移动（边缘/四边，非角落/旋转）
                            self._drag_start_xy = (x, y)
                            self._drag_start_pos_x = getattr(
                                c, 'pos_x', 0.0) or 0.0
                            self._drag_start_pos_y = getattr(
                                c, 'pos_y', 0.0) or 0.0
                        self.video_selected.emit(c, "video")
                        self._set_seq_state(None)
                        self._flush_frame(force=True)  # 仅重绘选中框，不重新取帧（避免单击闪烁）
                        return
                    else:
                        self._selected_video_clip = c
                        self._dragging_video = c
                        self._drag_snap_saved = False
                        self._drag_start_xy = (x, y)
                        self._drag_start_pos_x = getattr(
                            c, 'pos_x', 0.0) or 0.0
                        self._drag_start_pos_y = getattr(
                            c, 'pos_y', 0.0) or 0.0
                        self.video_selected.emit(c, "video")
                        self._set_seq_state(None)
                        self._flush_frame(force=True)  # 仅重绘选中框，不重新取帧（避免单击闪烁）
                        return
                    break

        # 点空白 → 清空选中（画布颜色选择器已移至双击）
        self._selected_video_clip = None
        self._dragging_video = None
        self._resize_handle = None
        self._rotation_active = False
        self._drag_snap_saved = False
        if self._selected_sub is not None:
            self._selected_sub = None
            self._sub_interaction = None
        self._hide_sub_editor(save=True)
        self.video_selected.emit(None, "")
        self._set_seq_state(None)  # 清缓存强制重取帧
        self._flush_frame(force=True)  # 立即刷新画布去除选中框（无需重新取帧）

    def _on_screen_double_click(self, event):
        """双击字幕 → 进入行内编辑 / 双击空白 → 画布颜色选择器"""
        x, y = event.pos().x(), event.pos().y()
        hit_result = self._hit_test_subtitle(x, y)
        if hit_result is not None:
            hit_sub, _ = hit_result
            self._selected_sub = hit_sub
            self._sub_interaction = None
            self._selected_video_clip = None
            self._dragging_video = None
            self._resize_handle = None
            self._show_sub_editor(hit_sub)
            return

        # 双击空白处 → 弹出画布颜色选择器
        self._show_bg_color_popup(event)

    def _on_screen_move(self, event):
        from PyQt6.QtCore import Qt as _Qt
        x = event.pos().x()
        y = event.pos().y()

        # ── 视频旋转 ──
        if self._rotation_active and self._selected_video_clip is not None:
            if not self._drag_snap_saved:
                self.tl._save_history()
                self._drag_snap_saved = True
            try:
                clip = self._selected_video_clip
                if not (event.buttons() & _Qt.MouseButton.LeftButton):
                    self._rotation_active = False
                    return
                cx, cy = self._rotation_center_xy
                import math
                cur_angle = math.atan2(y - cy, x - cx)
                delta_deg = math.degrees(
                    cur_angle - self._rotation_start_angle)
                if abs(delta_deg) > 180:
                    delta_deg = delta_deg - 360 if delta_deg > 0 else delta_deg + 360
                # 吸附：接近 0°/±90°/±180° 时 snap
                new_rot = self._rotation_start_rot + delta_deg
                for snap in (0, 90, -90, 180, -180, 270, -270):
                    if abs(new_rot - snap) < 3:
                        new_rot = snap
                        break
                clip.rotation = round(new_rot, 1)
                self._set_seq_state(None)
                self._flush_frame(force=True)
            except Exception:
                self._rotation_active = False
            return

        # ── 视频缩放 ──
        if self._resize_handle is not None:
            if not self._drag_snap_saved:
                self.tl._save_history()
                self._drag_snap_saved = True
            try:
                clip = self._selected_video_clip
                if clip is None or not (event.buttons() & _Qt.MouseButton.LeftButton):
                    self._resize_handle = None
                    return
                src_w, src_h = self._get_clip_src_size(clip)
                if src_w <= 0 or src_h <= 0:
                    return
                cw = getattr(self, '_canvas_w', self._screen.width()) or 640
                ch = getattr(self, '_canvas_h', self._screen.height()) or 360
                if cw <= 0 or ch <= 0:
                    return
                handle = self._resize_handle
                cx, cy = self._resize_center_xy

                # 从中心到鼠标的距离比 → 缩放（中心锚点，四边对称缩放）
                start_dx = self._resize_start_xy[0] - cx
                start_dy = self._resize_start_xy[1] - cy
                cur_dx = x - cx
                cur_dy = y - cy

                start_dist = max(abs(start_dx) + abs(start_dy), 1)
                cur_dist = max(abs(cur_dx) + abs(cur_dy), 1)
                raw_ratio = max(
                    0.15, min(5.0, (cur_dist / start_dist) * self._resize_start_scale))

                # ── 强吸附：视频边缘接近画布边界时 snap ──
                base_scale = min(cw / max(src_w, 1), ch / max(src_h, 1))
                thr = self._snap_threshold * 2
                if abs(raw_ratio - 1.0) * base_scale * max(src_w, src_h) < thr:
                    raw_ratio = 1.0
                fill_w_ratio = (cw / max(src_w, 1)) / base_scale
                if abs(raw_ratio - fill_w_ratio) * base_scale * src_w < thr:
                    raw_ratio = fill_w_ratio
                fill_h_ratio = (ch / max(src_h, 1)) / base_scale
                if abs(raw_ratio - fill_h_ratio) * base_scale * src_h < thr:
                    raw_ratio = fill_h_ratio

                clip.scale = raw_ratio

                # 从缩放比例反推新尺寸，居中于保存的中心点
                new_total_scale = base_scale * raw_ratio
                new_w = max(1, int(src_w * new_total_scale))
                new_h = max(1, int(src_h * new_total_scale))
                new_ox = cx - new_w // 2
                new_oy = cy - new_h // 2

                clip.pos_x = new_ox - (cw - new_w) // 2
                clip.pos_y = new_oy - (ch - new_h) // 2

                self._set_seq_state(None)
                self._flush_frame(force=True)
            except Exception:
                import traceback
                import sys
                traceback.print_exc(file=sys.stderr)
                self._resize_handle = None
            return

        # ── 视频拖拽 ──
        if self._dragging_video is not None:
            if not self._drag_snap_saved:
                self.tl._save_history()
                self._drag_snap_saved = True
            dx = x - self._drag_start_xy[0]
            dy = y - self._drag_start_xy[1]
            cw = getattr(self, '_canvas_w', self._screen.width())
            ch = getattr(self, '_canvas_h', self._screen.height())
            if cw <= 0 or ch <= 0:
                return
            # 拖拽范围：允许视频中心偏移高达画布宽/高的 2 倍
            # 保证缩小后的视频可以推到画布最边缘甚至大部分移出画布
            max_x = cw * 2.0
            max_y = ch * 2.0
            new_x = max(-max_x, min(max_x, self._drag_start_pos_x + dx))
            new_y = max(-max_y, min(max_y, self._drag_start_pos_y + dy))
            # 拖拽中不 snap（只在 release 时做），避免接近边界/中心时振荡跳动
            self._dragging_video.pos_x = new_x
            self._dragging_video.pos_y = new_y
            self._set_seq_state(None)
            self._flush_frame(force=True)
            return

        # ── 字幕交互（缩放/宽度/移动） ──
        if self._sub_interaction is not None and self._selected_sub is not None:
            if not (event.buttons() & _Qt.MouseButton.LeftButton):
                self._sub_interaction = None
                return
            self._do_sub_interact(self._selected_sub, x, y)

        # ── 鼠标悬浮：更新光标 ──
        self._update_hover_cursor(x, y)

    def _do_sub_interact(self, block, x: int, y: int):
        """处理字幕拖拽/缩放/宽度调整"""
        dx = x - self._sub_drag_start_xy[0]
        dy = y - self._sub_drag_start_xy[1]
        sw = self._screen.width()
        sh = self._screen.height()
        if sw <= 0 or sh <= 0:
            return
        mode = self._sub_interaction

        if mode == "move":
            new_px = max(-1.0,
                         min(1.0, self._sub_drag_start_pos[0] + dx / sw * 2.0))
            new_py = max(-1.0,
                         min(1.0, self._sub_drag_start_pos[1] + dy / sh * 2.0))
            old_px, old_py = getattr(
                block, 'pos_x', 99), getattr(block, 'pos_y', 99)
            block.pos_x = new_px
            block.pos_y = new_py
            # 触发字幕同步（画布拖拽绕过属性面板 _set，需手动广播）
            self.subtitle_pos_changed.emit(block, new_px, new_py)
            # 字幕移动只需重绘选中框，不需要新视频帧，直接 force flush
            self._flush_frame(force=True)

        elif mode in ("resize_nw", "resize_ne", "resize_sw", "resize_se"):
            # 四角缩放：等比改变 scale，用像素位移比例计算新 scale
            start_w = getattr(block, 'custom_width', 0) or 0
            if start_w <= 0:
                # 无自定义宽度：从文本宽度估算原始 box 宽度
                fs0 = max(6, int((getattr(block, 'font_size', 15) or 15)
                          * (self._sub_drag_start_scale or 1.0)))
                fm0 = QFontMetrics(QFont(
                    getattr(block, 'font_family', 'Microsoft YaHei') or 'Microsoft YaHei', fs0))
                raw = getattr(block, 'text', '') or ''
                start_w = max(1, fm0.horizontalAdvance(raw.replace('\n', '')))
            # 根据拖拽方向：右下/右上 向右拖=放大，左上/左下 向左拖=放大
            if mode in ("resize_se", "resize_ne"):
                new_w = max(10, start_w + dx)
            else:
                new_w = max(10, start_w - dx)
            ratio = new_w / max(start_w, 1)
            block.scale = max(
                0.2, min(5.0, self._sub_drag_start_scale * ratio))
            # 字体缩放不需要新视频帧，直接 force flush
            self._flush_frame(force=True)

        elif mode in ("width_left", "width_right"):
            # 宽度约束：最短 2 字宽 → 再缩换行 → 再缩整体变小(scale)
            fs = max(6, int((getattr(block, 'font_size', 15) or 15)
                     * (getattr(block, 'scale', 1.0) or 1.0)))
            fm = QFontMetrics(
                QFont(getattr(block, 'font_family', 'Microsoft YaHei') or 'Microsoft YaHei', fs))
            char_w = max(1, fm.horizontalAdvance("中"))
            min2_w = char_w * 2 + self._SUB_PAD * 2  # 两字宽+边距
            if mode == "width_left":
                new_w = int(self._sub_drag_start_width - dx)
            else:
                new_w = int(self._sub_drag_start_width + dx)
            if new_w < min2_w:
                # 已达两字极限 → 缩小 scale
                block.custom_width = min2_w
                ratio = new_w / max(min2_w, 1)
                block.scale = max(
                    0.15, min(5.0, self._sub_drag_start_scale * ratio))
            else:
                block.custom_width = new_w
                block.scale = self._sub_drag_start_scale  # 恢复原始 scale（如果之前缩小过）
            # 宽度调整不需要新视频帧，直接 force flush 立即显示换行效果
            self._flush_frame(force=True)

    def _update_hover_cursor(self, x: int, y: int):
        """鼠标悬浮时根据手柄区域更新光标样式"""
        if self._sub_interaction is not None:
            # 拖拽/缩放中，光标由交互模式决定，不重新 hit test
            cursor_map = {
                "move": Qt.CursorShape.ClosedHandCursor,
                "resize_nw": Qt.CursorShape.SizeFDiagCursor,
                "resize_se": Qt.CursorShape.SizeFDiagCursor,
                "resize_ne": Qt.CursorShape.SizeBDiagCursor,
                "resize_sw": Qt.CursorShape.SizeBDiagCursor,
                "width_left": Qt.CursorShape.SizeHorCursor,
                "width_right": Qt.CursorShape.SizeHorCursor,
            }
            self._screen.setCursor(cursor_map.get(
                self._sub_interaction, Qt.CursorShape.ArrowCursor))
            return

        # ── 1. 视频把手（优先于字幕，因为把手更小更精确）──
        if self._selected_video_clip is not None and \
           self._resize_handle is None and self._dragging_video is None and not self._rotation_active:
            rect = self._video_screen_rect(self._selected_video_clip)
            if rect is not None:
                handle = self._hit_handle(x, y, rect)
                if handle is not None:
                    h_type = handle[0]
                    if h_type == "rotate":
                        self._screen.setCursor(Qt.CursorShape.CrossCursor)
                    elif h_type == "resize":
                        direction = handle[1]
                        if direction in ("NW", "SE"):
                            self._screen.setCursor(
                                Qt.CursorShape.SizeFDiagCursor)
                        else:
                            self._screen.setCursor(
                                Qt.CursorShape.SizeBDiagCursor)
                    else:  # move
                        self._screen.setCursor(Qt.CursorShape.OpenHandCursor)
                    return

        # ── 2. 字幕把手 ──
        hit_result = self._hit_test_subtitle(x, y)
        if hit_result is None:
            self._screen.setCursor(Qt.CursorShape.ArrowCursor)
            return
        _, interaction = hit_result
        cursors = {
            "resize_nw": Qt.CursorShape.SizeFDiagCursor,
            "resize_se": Qt.CursorShape.SizeFDiagCursor,
            "resize_ne": Qt.CursorShape.SizeBDiagCursor,
            "resize_sw": Qt.CursorShape.SizeBDiagCursor,
            "width_left": Qt.CursorShape.SizeHorCursor,
            "width_right": Qt.CursorShape.SizeHorCursor,
            "move": Qt.CursorShape.OpenHandCursor,
        }
        cur = cursors.get(interaction, Qt.CursorShape.PointingHandCursor)
        self._screen.setCursor(cur)

    def leaveEvent(self, event):
        """鼠标离开预览区时恢复默认箭头光标"""
        if hasattr(self, '_screen') and self._screen:
            self._screen.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    # ─── 像素级文字换行（支持 CJK/拉丁/阿拉伯等混合语言） ───
    @staticmethod
    def _wrap_text_pixel(text: str, fm, max_px: float) -> list:
        """按像素宽度换行：先按 \\n 分段，每段再逐字符 pixel-wrap。
        CJK 字符通常 ~1.2×font_size，拉丁字符 ~0.55×font_size，此函数自动处理差异。"""
        lines = []
        for paragraph in text.split("\n"):
            cur = ""
            cur_w = 0.0
            for ch in paragraph:
                ch_w = fm.horizontalAdvance(ch)
                if cur and cur_w + ch_w > max_px:
                    lines.append(cur)
                    cur = ch
                    cur_w = ch_w
                else:
                    cur += ch
                    cur_w += ch_w
            if cur or not paragraph:
                lines.append(cur)
        return lines or [""]

    # ─── 字幕画布内联编辑（无 QTextEdit — 直接在虚线框内输入）───
    def _show_sub_editor(self, block):
        """双击字幕 → 进入内联编辑模式（画布直接输入，像素级换行）。
        自动暂停播放防止编辑框闪烁。"""
        if getattr(self, '_playing', False):
            self.pause_requested.emit()
        self._hide_sub_editor(save=True)
        self._selected_sub = None
        self._sub_interaction = None
        self._selected_video_clip = None
        self._dragging_video = None
        self._resize_handle = None
        raw = getattr(block, 'text', '') or ''
        self._edit_flat = raw
        self._edit_cursor = len(self._edit_flat)
        self._edit_blink = True
        self._ime_active = False                 # 每次进入编辑重置 IME 状态
        self._ime_preedit = ""                   # 清除旧预编辑文本
        self._ime_compose_start = 0
        self._editing_sub = block
        self._edit_blink_timer.start()
        self.setFocus()
        self._set_seq_state(None)
        self._flush_frame(force=True)
        from PyQt6.QtCore import QTimer as _Qt2
        _Qt2.singleShot(
            80, lambda: self._editing_sub is block and self._flush_frame(force=True))

    def _hide_sub_editor(self, save=True):
        """退出内联编辑模式，可选保存（像素级换行）"""
        block = self._editing_sub
        if block is None:
            return
        self._edit_blink_timer.stop()
        if save:
            flat = self._edit_flat
            if flat:
                # 用 _edit_flat 直接测宽换行（对齐 _draw_edit_subtitle）
                fs = getattr(block, 'font_size', 15) or 15
                sc = getattr(block, 'scale', 1.0) or 1.0
                fs = max(8, int(fs * sc))
                font = QFont(getattr(block, 'font_family',
                             'Microsoft YaHei') or 'Microsoft YaHei', fs)
                fm = QFontMetrics(font)
                one_cjk = fm.horizontalAdvance("测")
                w4 = int(one_cjk * 4)
                w9 = int(one_cjk * 9)
                cw_block = getattr(block, 'custom_width', 0) or 0
                text_px = max((fm.horizontalAdvance(ln)
                              for ln in flat.split('\n')), default=0)
                if cw_block > 0:
                    wrap_w = max(int(cw_block), w4)
                else:
                    wrap_w = max(w4, min(text_px, w9))
                lines = self._wrap_text_pixel(flat, fm, wrap_w)
                new_text = '\n'.join(lines)
            else:
                new_text = ''
            if new_text != (getattr(block, 'text', '') or ''):
                self.tl._save_history()  # 撤回：捕获文本变更前状态
                block.text = new_text
                self.tl.changed.emit()
        # 编辑结束后恢复选中状态（显示虚线框+手柄），方便继续拖拽/调整
        self._selected_sub = block
        self._editing_sub = None
        self._edit_flat = ""
        self._edit_cursor = 0
        self._ime_active = False                # 退出编辑时清除 IME 状态
        self._ime_preedit = ""
        self._ime_compose_start = 0
        self._async_fetch(self._current_sec)

    def _toggle_edit_blink(self):
        """光标闪烁切换 — 轻量重绘，不触发视频帧重新取"""
        if self._editing_sub is None:
            self._edit_blink_timer.stop()
            return
        self._edit_blink = not self._edit_blink
        # 直接触发 _flush_frame 走编辑渲染分支，不 invalidate 帧缓存
        self._flush_frame()

    def inputMethodEvent(self, event):
        """IME 输入法事件：处理中文/日文/韩文等组合字符输入。

        核心设计：
        - 预编辑文本（拼音等）= _ime_preedit，仅屏幕显示，不写入 _edit_flat
        - 提交文本（汉字等）= commit，插入 _edit_flat 的 _ime_compose_start 位置
        - 不依赖 replacementStart/replacementLength（各平台/IME 语义不一致）
        """
        if self._editing_sub is None:
            event.ignore()
            return

        commit = event.commitString()
        preedit = event.preeditString()

        flat = self._edit_flat

        # ── IME 开始组合（之前没有预编辑，现在有了）──
        if preedit and not self._ime_preedit:
            self._ime_compose_start = self._edit_cursor

        # ── 有提交文本：直接写入 _edit_flat（拼音从未进入过 flat，无需清除）──
        if commit:
            pos = self._ime_compose_start
            if len(flat) + len(commit) > 99:
                event.ignore()
                return
            flat = flat[:pos] + commit + flat[pos:]
            self._edit_flat = flat
            self._edit_cursor = pos + len(commit)
            self._ime_preedit = ""
            self._ime_active = False

        # ── 只有预编辑（拼音）：更新 _ime_preedit，不动 _edit_flat ──
        elif preedit:
            self._ime_preedit = preedit
            self._ime_active = True

        # ── 既无预编辑也无提交：IME 取消 ──
        else:
            self._ime_preedit = ""
            self._ime_active = False

        self._edit_blink = True
        event.accept()
        self._flush_frame(force=True)

    def inputMethodQuery(self, query: Qt.InputMethodQuery):
        """IME 查询：告知输入法光标位置、周围文字等信息"""
        if self._editing_sub is None:
            return super().inputMethodQuery(query)

        if query == Qt.InputMethodQuery.ImEnabled:
            return True
        if query == Qt.InputMethodQuery.ImCursorRectangle:
            r = self._edit_cursor_rect
            return QRectF(r.x(), r.y(), max(r.width(), 2), max(r.height(), 10))
        if query == Qt.InputMethodQuery.ImCursorPosition:
            # IME 组合中：返回 _edit_flat 内组合起始位置（不含预编辑）
            return self._ime_compose_start if self._ime_preedit else self._edit_cursor
        if query == Qt.InputMethodQuery.ImSurroundingText:
            return self._edit_flat
        if query == Qt.InputMethodQuery.ImCurrentSelection:
            return ""

        return super().inputMethodQuery(query)

    def keyPressEvent(self, event):
        """画布内联编辑：直接在虚线框内键入文字"""
        if self._editing_sub is None:
            super().keyPressEvent(event)
            return

        key = event.key()
        text = event.text()
        flat = self._edit_flat
        cur = self._edit_cursor

        def _redraw():
            """编辑文本变更后同步刷新画布（force=True 强制渲染编辑框，无视帧缓冲）"""
            self._flush_frame(force=True)

        if key == Qt.Key.Key_Return or key == Qt.Key.Key_Enter:
            # Shift+Enter = 确认保存，Enter = 插入换行
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._hide_sub_editor(save=True)
            else:
                if len(flat) >= 99:
                    return
                flat = flat[:cur] + "\n" + flat[cur:]
                cur += 1
                self._edit_flat = flat
                self._edit_cursor = cur
                self._edit_blink = True
                _redraw()
            return

        if key == Qt.Key.Key_Escape:
            self._edit_blink_timer.stop()
            block = self._editing_sub  # 先保存引用再清除
            self._editing_sub = None
            self._edit_flat = ""
            self._edit_cursor = 0
            if block is not None:
                self._selected_sub = block  # 取消编辑后也恢复选中状态
            _redraw()
            return

        if key == Qt.Key.Key_Backspace:
            if self._ime_active:
                # IME 正在组合拼音，Backspace 应由 IME 处理
                event.ignore()
                super().keyPressEvent(event)
                return
            event.accept()
            if cur > 0:
                # 删除光标前一个字符（若是 \\n 则自然合并到上一行）
                flat = flat[:cur - 1] + flat[cur:]
                cur -= 1
            self._edit_flat = flat
            self._edit_cursor = cur
            self._edit_blink = True
            _redraw()
            return

        if key == Qt.Key.Key_Delete:
            if self._ime_active:
                # IME 正在组合拼音，Delete 应由 IME 处理
                event.ignore()
                super().keyPressEvent(event)
                return
            event.accept()
            if cur < len(flat):
                # 如果光标正好在 \\n 上：删除 \\n 合并到下一行
                flat = flat[:cur] + flat[cur + 1:]
                # cur 不变，自然落在合并后的位置
            self._edit_flat = flat
            self._edit_cursor = cur
            self._edit_blink = True
            _redraw()
            return

        if key == Qt.Key.Key_Left:
            if cur > 0:
                self._edit_cursor = cur - 1
                self._edit_blink = True
                _redraw()
            return

        if key == Qt.Key.Key_Right:
            if cur < len(flat):
                self._edit_cursor = cur + 1
                self._edit_blink = True
                _redraw()
            return

        if key == Qt.Key.Key_Home:
            self._edit_cursor = 0
            self._edit_blink = True
            _redraw()
            return

        if key == Qt.Key.Key_End:
            self._edit_cursor = len(flat)
            self._edit_blink = True
            _redraw()
            return

        # 可打印字符 → 仅非 IME 输入（英文直接键入）时走这里
        # IME 组合中（中文/日文等）由 inputMethodEvent 处理，keyPressEvent 不干预
        if text and len(text) == 1 and text.isprintable() and not self._ime_active:
            if len(flat) >= 99:
                return
            flat = flat[:cur] + text + flat[cur:]
            cur += 1
            self._edit_flat = flat
            self._edit_cursor = cur
            self._edit_blink = True
            _redraw()
            return

        super().keyPressEvent(event)

    def _draw_edit_subtitle(self, painter: QPainter, cw: int, ch: int):
        """编辑字幕渲染：基于 _edit_flat 动态计算 bbox
        规则：
        - 最小宽 = 4个中文字
        - 无 custom_width 时：打字自动扩宽，上限9字；超9字换行
        - 有 custom_width 时：按拖拽宽度换行，无上限
        - 始终显示虚线编辑框（空文本时也可见）
        """
        block = self._editing_sub
        if block is None:
            return
        flat = self._edit_flat  # 已确定的文字

        # ── IME 预编辑文本拼入显示（拼音仅屏幕显示，不入 _edit_flat）──
        if self._ime_preedit:
            pos = self._ime_compose_start
            # 确保 pos 在合法范围
            pos = max(0, min(pos, len(flat)))
            display_flat = flat[:pos] + self._ime_preedit + flat[pos:]
            # 显示光标放在预编辑文本末尾
            display_cursor = pos + len(self._ime_preedit)
        else:
            display_flat = flat
            display_cursor = self._edit_cursor

        # ── 字体 ──
        fs = getattr(block, 'font_size', 15) or 15
        sc = getattr(block, 'scale', 1.0) or 1.0
        fs = max(8, int(fs * sc))
        fc = getattr(block, 'color', '#ffffff') or '#ffffff'
        family = getattr(block, 'font_family',
                         'Microsoft YaHei') or 'Microsoft YaHei'
        bold = getattr(block, 'font_bold',   False)
        italic = getattr(block, 'font_italic',  False)
        ow = getattr(block, 'outline_width', 0) or 0
        oc = getattr(block, 'outline_color', '#000000') or '#000000'

        font = QFont(family, fs)
        font.setBold(bold)
        font.setItalic(italic)
        fm = QFontMetrics(font)
        line_h = fm.height()
        pad = self._SUB_PAD

        # ── 中心点（归一化 pos_x/pos_y → 画布像素）──
        npx = getattr(block, 'pos_x', None)
        npy = getattr(block, 'pos_y', None)
        if npx is None or npy is None:
            pos_map = {'top': -0.85, 'center': 0.0, 'bottom': 0.85}
            position = getattr(block, 'position', 'bottom') or 'bottom'
            npx = 0.0
            npy = pos_map.get(position, 0.85)
        cx = int((float(npx) + 1.0) / 2.0 * cw)
        cy = int((float(npy) + 1.0) / 2.0 * ch)

        # ── 框宽：4字起，打字自动扩到9字后换行；手动拖拽宽度优先 ──
        one_cjk = fm.horizontalAdvance("测")
        w4 = int(one_cjk * 4)
        w9 = int(one_cjk * 9)
        cw_block = getattr(block, 'custom_width', 0) or 0
        text_px = max((fm.horizontalAdvance(ln) for ln in display_flat.split('\n')), default=0) if display_flat else 0

        if cw_block > 0:
            box_w = max(int(cw_block), w4)      # 手动拖拽：无上限
        else:
            box_w = max(w4, min(text_px, w9))   # 自动：最大9字宽

        # ── 换行：按 box_w 换行 ──
        lines = self._wrap_text_pixel(
            display_flat, fm, box_w) if display_flat else []
        actual_rows = max(1, len(lines))         # 至少1行
        box_h = actual_rows * line_h

        # ── 框坐标（box_w × box_h 内容区，pad 留边）──
        bx = cx - box_w // 2 - pad
        by = cy - box_h // 2 - pad
        bw = box_w + pad * 2
        bh = box_h + pad * 2

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # ── 半透明黑色背景 ──
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 160))
        painter.drawRoundedRect(int(bx), int(by), int(bw), int(bh), 4, 4)

        # ── 虚线白色边框（编辑框标志，始终可见）──
        dash_pen = QPen(QColor(255, 255, 255, 220), 1.5, Qt.PenStyle.DashLine)
        painter.setPen(dash_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(int(bx), int(by), int(bw), int(bh), 4, 4)

        # ── 绘制文字 ──
        painter.setFont(font)
        for li, line in enumerate(lines):
            ly = int(by) + pad + li * line_h
            lw_line = fm.horizontalAdvance(line)
            lx = int(bx + pad + (box_w - lw_line) / 2)  # 居中

            if ow > 0:
                path = QPainterPath()
                path.addText(lx, ly + fm.ascent(), font, line)
                # 两遍绘制：先描边再填充，保证字色纯净不被描边侵入
                spen = QPen(QColor(oc), ow * 2)
                spen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                spen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(spen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(fc))
                painter.drawPath(path)
            else:
                painter.setPen(QColor(fc))
                painter.drawText(lx, ly + fm.ascent(), line)

        # ── 闪烁光标 ──
        # 先计算光标像素位置（用于 IME 输入法定位 + 闪烁渲染）
        cur = display_cursor
        cli = 0
        ccol = 0
        # 同步重建 lines→flat 位置映射（与上面 _wrap_text_pixel 一致）
        mapped = []  # [(line_text, flat_start, flat_end)]
        fp = 0
        for para in (display_flat.split("\n") if display_flat else []):
            plines = self._wrap_text_pixel(para, fm, box_w) if para else [""]
            for pline in plines:
                mapped.append((pline, fp, fp + len(pline)))
                fp += len(pline)
            fp += 1  # 跳过 \n
        for i, (ln, s, e) in enumerate(mapped):
            if s <= cur <= e:
                cli = i
                ccol = cur - s; break
        else:
            if mapped:
                cli = len(mapped) - 1
                ccol = len(mapped[-1][0])
        if 0 <= cli < len(lines):
            prefix_w = fm.horizontalAdvance(
                lines[cli][:ccol]) if lines[cli] else 0
            lw_line = fm.horizontalAdvance(lines[cli])
            lx_base = int(bx + pad + (box_w - lw_line) / 2)
            cur_x = lx_base + prefix_w
            cur_y = int(by) + pad + cli * line_h
        elif not lines:
            # 无文字时光标在框中间
            cur_x = int(bx + bw / 2)
            cur_y = int(by) + pad
        else:
            # fallback：框左上角
            cur_x = int(bx + pad)
            cur_y = int(by) + pad
        self._edit_cursor_rect = QRect(int(cur_x), int(cur_y), 2, line_h)
        # 转为 PreviewPlayer 控件坐标（IME 输入法需要控件本地坐标）
        if self._screen is not None:
            wp = self._screen.mapTo(self, QPoint(int(cur_x), int(cur_y)))
            self._edit_cursor_rect = QRect(wp.x(), wp.y(), 2, line_h)

        if self._edit_blink:
            painter.setPen(QPen(QColor("#00eaff"), 2))
            painter.drawLine(cur_x, cur_y, cur_x, cur_y + line_h)

        painter.restore()

    def _draw_sub_selection(self, painter: QPainter, block, cw: int, ch: int):
        """在画布上绘制字幕选中边框 + 手柄（字幕文字由调用方在外部渲染）"""
        bbox = self._compute_sub_bbox(block)
        if bbox is None:
            return
        bx, by, bw, bh = bbox
        hs = self._SUB_HANDLE

        # ── 青色虚线边框 ──
        dash_pen = QPen(QColor("#00eaff"), 1.5, Qt.PenStyle.DashLine)
        dash_pen.setDashPattern([4, 3])
        painter.setPen(dash_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(int(bx), int(by), int(bw), int(bh))

        # ── 四角圆形手柄（小圆点，美观）──
        handle_r = 4  # 视觉半径
        corners = [
            (int(bx), int(by)),                    # NW
            (int(bx + bw), int(by)),               # NE
            (int(bx), int(by + bh)),               # SW
            (int(bx + bw), int(by + bh)),          # SE
        ]
        corner_order = ["NW", "NE", "SW", "SE"]
        painter.setPen(QPen(QColor("#00eaff"), 1.5))
        painter.setBrush(QColor("#1a1a2e"))
        for (cx, cy), cname in zip(corners, corner_order):
            painter.drawEllipse(QPoint(cx, cy), handle_r, handle_r)

        # ── 左右边宽度把手（竖线）──
        mid_y = int(by + bh / 2)
        bar_h = 10
        bar_w = 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 180))
        painter.drawRoundedRect(int(bx) - 1, mid_y - \
                                bar_h // 2, bar_w, bar_h, 1, 1)
        painter.drawRoundedRect(
            int(bx + bw) - 1, mid_y - bar_h // 2, bar_w, bar_h, 1, 1)

        # ── 上下边移动把手（横线）──
        mid_x = int(bx + bw / 2)
        bar_w2 = 10
        bar_h2 = 2
        painter.setBrush(QColor(255, 255, 255, 180))
        painter.drawRoundedRect(mid_x - bar_w2 // 2,
                                int(by) - 1, bar_w2, bar_h2, 1, 1)
        painter.drawRoundedRect(mid_x - bar_w2 // 2,
                                int(by + bh) - 1, bar_w2, bar_h2, 1, 1)

    def wheelEvent(self, event):
        """鼠标滚轮：逐帧步进播放头。向上=前进1帧，向下=后退1帧。"""
        if hasattr(self, 'tl') and self.tl:
            fps = 30.0
            try:
                for track in self.tl.video_tracks:
                    for clip in track:
                        if hasattr(clip, 'source_path') and clip.source_path:
                            import cv2
                            cap = cv2.VideoCapture(clip.source_path)
                            fps = cap.get(cv2.CAP_PROP_FPS) or 30
                            cap.release()
                            break
                    if fps != 30.0:
                        break
            except Exception:
                _log_exc()
            delta_sec = 1.0 / max(fps, 1.0)
            direction = -1 if event.angleDelta().y() > 0 else 1
            new_sec = getattr(self, '_current_sec', 0) + direction * delta_sec
            new_sec = max(0, new_sec)
            self.seek(new_sec)
            # 通知时间线移动播放头
            if hasattr(self, '_on_playhead_update'):
                self._on_playhead_update(new_sec)
        event.accept()

    # ════════════════════════════════════════════════════════════
    # 素材库画布内预览（双击素材 → 直接在画布区按原尺寸播放）
    # ════════════════════════════════════════════════════════════

    def start_preview(self, path: str, media_type: str):
        """开始画布内预览：不加入时间线，直接在预览区按原始尺寸播放"""
        self.stop_preview()
        self._preview_active = True
        self._preview_path = path
        self._preview_type = media_type
        self._preview_current = 0.0
        self._preview_img_pix = None
        import time
        self._preview_last_tick_time = time.perf_counter()  # 用于帧率控制

        if media_type == "video":
            import cv2
            self._preview_cap = cv2.VideoCapture(path)
            if not self._preview_cap.isOpened():
                self.stop_preview()
                return
            self._preview_fps = self._preview_cap.get(cv2.CAP_PROP_FPS) or 30.0
            total_frames = int(self._preview_cap.get(
                cv2.CAP_PROP_FRAME_COUNT) or 0)
            self._preview_duration = total_frames / \
                max(self._preview_fps, 0.01)
            # 用视频原始尺寸更新 _screen 布局，使画布按视频比例显示
            src_w = int(self._preview_cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            src_h = int(self._preview_cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            if src_w > 0 and src_h > 0:
                self._clip_src_w = src_w
                self._clip_src_h = src_h
                self._position_screen()
            self._show_preview_first_frame()
        elif media_type == "image":
            self._preview_img_pix = QPixmap(path)
            self._preview_duration = 0.0
            self._preview_current = 0.0
            # 图片也按原始比例调整画布
            src_w = self._preview_img_pix.width()
            src_h = self._preview_img_pix.height()
            if src_w > 0 and src_h > 0:
                self._clip_src_w = src_w
                self._clip_src_h = src_h
                self._position_screen()
            self._render_preview_image()
        elif media_type == "audio":
            from ui.media_library import _get_duration
            self._preview_duration = _get_duration(path, "audio")
            self._preview_current = 0.0
            # 清空画布显示音频信息
            cw = getattr(self, '_canvas_w', 0) or 640
            ch = getattr(self, '_canvas_h', 0) or 360
            canvas = self._alloc_canvas(cw, ch)
            # 不再 fill：_alloc_canvas 已准备画布
            p = QPainter(canvas)
            p.setPen(QColor("#888"))
            p.setFont(QFont("Microsoft YaHei", 11))
            fname = os.path.basename(path)
            dur_str = f"{self._preview_duration:.1f}s"
            p.drawText(canvas.rect(), Qt.AlignmentFlag.AlignCenter,
                       f"🎵 {fname}\n时长：{dur_str}\n双击画布退出预览")
            p.end()
            self._screen.setPixmap(QPixmap.fromImage(canvas))

        # 暂停时间线播放，启动预览音频
        if self._playing:
            self.stop_audio()
            self.set_playing(False)
        self._start_preview_audio()

    def stop_preview(self):
        """停止画布内预览，回到时间线模式"""
        if not self._preview_active:
            return
        self._preview_active = False
        self._preview_path = ""
        self._preview_type = ""
        self._stop_preview_audio()
        if self._preview_cap:
            try:
                self._preview_cap.release()
            except Exception:
                pass
            self._preview_cap = None
        self._preview_fps = 30.0
        self._preview_duration = 0.0
        self._preview_current = 0.0
        self._preview_img_pix = None
        self._preview_elapsed_acc = 0.0
        self._preview_last_tick_time = 0.0
        # 恢复画布：清缓存，让 _flush_frame 根据时间线帧重建正确的画布尺寸
        self._last_frame_image = None
        self._last_raw_img = None
        self._last_raw_overlays = []
        self._seq_state = None
        # 清除尺寸追踪，确保 _flush_frame 根据新帧重新计算画布比例
        self._last_src_w = 0
        self._last_src_h = 0
        # 清除预览视频尺寸，防止残留影响画布比例
        self._clip_src_w = 0
        self._clip_src_h = 0
        # 调用 _position_screen 根据当前 _aspect_ratio 重新布局画布
        self._position_screen()
        self._async_fetch(self._current_sec)

    def _show_preview_first_frame(self):
        """显示预览视频第一帧"""
        import cv2
        if not self._preview_cap:
            return
        self._preview_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ret, frame = self._preview_cap.read()
        if ret:
            self._render_preview_frame(frame)

    def _render_preview_frame(self, frame):
        """将预览视频帧渲染到画布（按原始尺寸，居中裁剪）"""
        import cv2
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = frame_rgb.shape
        qimg = QImage(frame_rgb.data, w, h, ch * w,
                      QImage.Format.Format_RGB888).copy()
        pix = QPixmap.fromImage(qimg)
        self._preview_img_pix = pix
        self._render_preview_image()

    def _render_preview_image(self):
        """将当前预览图像按原始尺寸渲染到画布"""
        if self._preview_img_pix is None or self._preview_img_pix.isNull():
            return
        cw = getattr(self, '_canvas_w', 0) or (
            self._screen.width() if self._screen else 640)
        ch = getattr(self, '_canvas_h', 0) or (
            self._screen.height() if self._screen else 360)
        if cw <= 0 or ch <= 0:
            return
        canvas = self._alloc_canvas(cw, ch)
        canvas.fill(
            QColor(getattr(self, '_canvas_bg_color', '#000000') or '#000000'))

        src_w = self._preview_img_pix.width()
        src_h = self._preview_img_pix.height()
        if src_w <= 0 or src_h <= 0:
            return

        # 按原始比例缩放到画布内（不拉伸变形）
        scale = min(cw / src_w, ch / src_h)
        draw_w = int(src_w * scale)
        draw_h = int(src_h * scale)
        draw_x = (cw - draw_w) // 2
        draw_y = (ch - draw_h) // 2

        scaled = self._preview_img_pix.scaled(
            draw_w, draw_h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        p = QPainter(canvas)
        p.drawPixmap(draw_x, draw_y, scaled)
        p.end()
        self._screen.setPixmap(QPixmap.fromImage(canvas))

    def _start_preview_audio(self):
        """启动预览音频播放"""
        import subprocess
        if self._preview_type not in ("video", "audio"):
            return
        try:
            from utils.ffmpeg_utils import get_ffmpeg_path
            ffmpeg = get_ffmpeg_path()
        except Exception:
            ffmpeg = "ffmpeg"
        if not os.path.exists(ffmpeg):
            import shutil
            ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        ffplay = os.path.join(os.path.dirname(ffmpeg), "ffplay.exe")
        if not os.path.exists(ffplay):
            import shutil
            ffplay = shutil.which("ffplay")
        if not ffplay:
            return
        try:
            cmd = [
                ffplay, "-nodisp", "-autoexit",
                "-ss", str(self._preview_current),
                "-i", self._preview_path,
                "-loglevel", "quiet"
            ]
            self._preview_audio_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL
            )
        except Exception:
            self._preview_audio_proc = None

    def _stop_preview_audio(self):
        """停止预览音频"""
        import subprocess
        if self._preview_audio_proc:
            try:
                self._preview_audio_proc.terminate()
                try:
                    self._preview_audio_proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._preview_audio_proc.kill()
            except Exception:
                pass
            self._preview_audio_proc = None

    def _tick_preview(self):
        """预览模式刷新：按真实时间推进视频帧，保证播放速度正常"""
        if not self._preview_active:
            return

        import time
        now = time.perf_counter()
        elapsed = now - getattr(self, '_preview_last_tick_time', now)
        self._preview_last_tick_time = now

        if self._preview_type == "video":
            if not self._preview_cap:
                return
            fps = max(self._preview_fps, 1.0)
            frame_duration = 1.0 / fps  # 每帧对应的时长（秒）

            # 累积时间，决定需要跳过多少帧
            self._preview_elapsed_acc = getattr(
                self, '_preview_elapsed_acc', 0.0) + elapsed

            # 每积累满一帧时长才读取一帧（保证不快进）
            frames_to_advance = int(self._preview_elapsed_acc / frame_duration)
            if frames_to_advance <= 0:
                return  # 时间还没到下一帧，跳过本次
            self._preview_elapsed_acc -= frames_to_advance * frame_duration

            # 若需要跳多帧（如定时器精度问题），跳到目标帧
            if frames_to_advance > 1:
                import cv2
                target_frame = int(self._preview_current * \
                                   fps) + frames_to_advance
                self._preview_cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)

            self._preview_current += frames_to_advance * frame_duration
            if self._preview_current >= self._preview_duration - 0.01:
                self.stop_preview()
                return
            ret, frame = self._preview_cap.read()
            if not ret:
                self.stop_preview()
                return
            self._render_preview_frame(frame)

        elif self._preview_type == "audio":
            self._preview_current += elapsed
            if self._preview_current >= self._preview_duration - 0.01:
                self.stop_preview()
                return
            if (self._preview_audio_proc and
                    self._preview_audio_proc.poll() is not None):
                self.stop_preview()
                return
        # 更新时间码
        m = int(self._preview_current) // 60
        s = self._preview_current % 60
        self._timecode.setText(f"[预览] {m:02d}:{s:05.2f}")

    def closeEvent(self, event):
        """关闭时释放资源"""
        self.stop_preview()
        # 停止所有音频子进程（ffplay/ffmpeg），防止关闭后残留播放
        try:
            self.stop_audio()
        except Exception:
            _log_exc()
        # 停止后台取帧线程
        try:
            self._fetch_queue.put_nowait(None)
        except Exception:
            _log_exc()
        if hasattr(self, '_fetch_thread') and self._fetch_thread.is_alive():
            self._fetch_thread.join(timeout=2.0)
        # 停止刷新定时器
        self._refresh_timer.stop()
        # 释放 OpenCV 资源
        try:
            import cv2
            for cap in list(self._cap_cache.values()):
                cap.release()
            self._cap_cache.clear()
        except Exception:
            _log_exc()
        # 释放状态机解码器（含其内部 VideoCapture / RingBuffer）
        try:
            if getattr(self, '_decoders', None) is not None:
                self._decoders.release()
        except Exception:
            _log_exc()
        # 清理 alpha 视频整段解码缓存（删除临时 .rgba 文件）
        try:
            self._clear_alpha_cache()
        except Exception:
            pass
        if event is not None:
            super().closeEvent(event)

    def generate_thumbnail(self, source_path: str, at_sec: float = 1.0) -> QPixmap:
        """生成视频缩略图（用于素材库显示）"""
        try:
            import cv2
            cap = cv2.VideoCapture(source_path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(at_sec * fps))
            ret, frame = cap.read()
            cap.release()
            if ret:
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = frame_rgb.shape
                qimg = QImage(frame_rgb.data, w, h, ch * w,
                              QImage.Format.Format_RGB888).copy()
                return QPixmap.fromImage(qimg).scaled(
                    120, 68, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
        except Exception:
            _log_exc()
        pix = QPixmap(120, 68)
        pix.fill(QColor("#222"))
        return pix
