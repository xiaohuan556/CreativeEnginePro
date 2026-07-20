"""ClipDecoder —— 独立的状态机解码器（替代 PreviewPlayer 内联的 cv2 seek+read）。

设计目标（来自代码审查，用户确认）：
- PreviewPlayer 永不直接碰 OpenCV，只调 decoder.request(src_sec, state) 取帧。
- 播放时连续 read()，绝不每帧 seek；只有真正跳转/拖拽才 seek 一次。
- 滑动 RingBuffer 按 frame_idx 缓存窗口帧，消费端命中即取，不重新解码。
- 四态：playing / paused / scrubbing / seek，不同策略避免后台无效解码。
- 未来换 PyAV / FFmpeg 管道 / 硬件解码，只改本文件，PreviewPlayer 不动。

测量验证（用户实机 4 场景）：每帧 decode≈200ms 中 seek≈200ms、read≈3ms →
瓶颈是随机 seek，不是解码。本模块通过"连续 read + 窗口缓存"根除每帧 seek。
"""
import os
import threading
import logging
from collections import OrderedDict

# 必须在 import cv2 之前设置，避免多 VideoCapture 抢 FFmpeg async_lock 崩溃
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;1")

import cv2

# 读超时保护（秒）：单帧 read/seek 超过此值判定 cap 卡死，标记重建
_READ_TIMEOUT = 2.0
# 连续读与 seek 的阈值：gap <= 此值向前顺序读；gap 更大（如播放起点/大跳）则 seek 一次
_MAX_SEQ_GAP = 8
# 单解码器 RingBuffer 窗口大小（帧）。1080p RGB ≈ 6MB/帧，24 帧 ≈ 150MB 上限。
_RING_MAX = 24
# scrub / seek 后预填窗口（平滑拖拽/跳转）：对称 ±，仅 1 次 seek 覆盖
_FILL_AHEAD = 12
_FILL_BACK = 8


class RingBuffer:
    """按 frame_idx 有序滑动的帧窗口缓存。pop 旧帧而非 hash 查找。"""

    def __init__(self, maxlen=_RING_MAX):
        self._max = maxlen
        self._data = OrderedDict()  # frame_idx -> (rgb_frame, w, h)

    def has(self, idx):
        return idx in self._data

    def get(self, idx):
        return self._data.get(idx)

    def push(self, idx, val):
        self._data[idx] = val
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    def min_idx(self):
        return next(iter(self._data)) if self._data else None

    def max_idx(self):
        return next(reversed(self._data)) if self._data else None

    def clear(self):
        self._data.clear()


class ClipDecoder:
    """单 clip 的解码器：封装 VideoCapture + RingBuffer + 状态机。

    request(src_sec, state) -> (rgb_frame, w, h) | None
      - playing：向前顺序 read 填充窗口，命中 ring 即返回；gap 过大（播放起点/大跳）
        才 seek 一次，之后恢复连续 read。
      - paused：保持当前缓存，按需 seek 单帧（不连续解码）。
      - scrubbing / seek：单次 seek 到目标并预填前向窗口。
    """

    STATE_IDLE = "idle"
    STATE_PLAYING = "playing"
    STATE_PAUSED = "paused"
    STATE_SCRUBBING = "scrubbing"
    STATE_SEEK = "seek"

    def __init__(self, path):
        self.path = path
        self.cap = None
        self.fps = 30.0
        self.total_frames = 0
        self._head = -1          # 最后顺序读到的 frame_idx（cap 已指向 _head+1）
        self._ring = RingBuffer()
        self._state = self.STATE_IDLE
        self._stale = False

    # ── 生命周期 ──
    def open(self):
        if self.cap is not None and self.cap.isOpened():
            return True
        if self._stale:
            return False
        self.cap = cv2.VideoCapture(self.path)
        if not self.cap.isOpened():
            self._stale = True
            return False
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self._head = -1
        self._ring.clear()
        return True

    def is_open(self):
        return self.cap is not None and self.cap.isOpened()

    def set_state(self, state):
        self._state = state

    def release(self):
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
        self.cap = None
        self._ring.clear()
        self._head = -1

    # ── 取帧入口 ──
    def request(self, src_sec, state=None, ahead_frames=0):
        """返回 (rgb_frame, w, h) 或 None（解码失败）。rgb_frame 为 RGB 8bit。"""
        if state is None:
            state = self._state
        if not self.open():
            return None
        target = int(round(src_sec * self.fps))
        if target < 0:
            target = 0
        if self.total_frames and target >= self.total_frames:
            target = max(0, self.total_frames - 1)

        # 命中窗口 → 直接返回，不解码不 seek（拖拽回跳/小幅抖动零成本）
        if self._ring.has(target):
            if state == self.STATE_PLAYING and ahead_frames > 0:
                self._ensure_forward(target + ahead_frames)
            return self._ring.get(target)

        if state == self.STATE_PLAYING:
            self._ensure_forward(target)
            if ahead_frames > 0:
                self._ensure_forward(target + ahead_frames)
        else:
            # paused / scrubbing / seek：单次 seek + 预填对称窗口
            self._seek_to(target)
        return self._ring.get(target)

    # ── 独立取帧（叠加轨用）：不修改 _head / ring，不干扰主轨连续读状态 ──
    def read_frame_at(self, target):
        """直接 seek 到 target 帧并读取，不修改 _head 和 ring buffer。
        用于叠加轨取帧，避免同一个 source_path 的主轨/叠加轨共用 decoder
        时来回 seek 互相覆盖 _head，导致每帧都 seek（瓶颈 ~200ms）。
        返回 (rgb_frame, w, h) 或 None。"""
        if not self.open():
            return None
        if target < 0:
            target = 0
        if self.total_frames and target >= self.total_frames:
            target = max(0, self.total_frames - 1)
        # ring 可能命中（如果主轨刚好读过目标帧附近）→ 免 seek
        if self._ring.has(target):
            return self._ring.get(target)
        ok, frame = self._seek_read(target)
        if not ok or frame is None:
            return None
        # 仅转换不做 push，不干扰主轨状态机
        if frame.shape[2] == 4:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGBA)
        else:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        return (rgb, w, h)

    # ── 内部：连续向前读 ──
    def _ensure_forward(self, target):
        if self._ring.has(target):
            return
        if target <= self._head:
            # 回拖但在窗口内 → ring 命中；否则 seek 一次
            if self._ring.has(target):
                return
            self._seek_to(target)
            return
        gap = target - self._head
        if gap > _MAX_SEQ_GAP:
            # 播放起点 / 大跳：先 seek 一次，之后恢复连续 read
            self._seek_to(target)
            return
        # 安全网：验证 cap 位置未被外部代码（如 read_frame_at）移动
        # 若位置已被其他 seek 污染，先纠正再顺序读，避免读到错误帧
        _expected = self._head + 1
        try:
            _actual = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES))
            if _actual != _expected:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, _expected)
        except Exception:
            pass
        # 顺序向前 read（每帧 3~6ms，无 seek）
        while self._head < target:
            ok, frame = self._seq_read()
            if not ok:
                break
            self._head += 1
            self._push(self._head, frame)

    def _push(self, target, frame):
        if frame is None:
            return
        if frame.shape[2] == 4:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGBA)
        else:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        self._ring.push(target, (rgb, w, h))

    # ── 内部：单次 seek ──
    def _seek_to(self, target):
        # 若目标就在前方不远，直接顺序推进，避免 seek 回退
        if 0 <= target - self._head <= _MAX_SEQ_GAP:
            self._ensure_forward(target)
            return
        # 对称窗口：先跳到 target-FILL_BACK，再顺序读满整个窗口（仅 1 次 seek）
        start = max(0, target - _FILL_BACK)
        ok, frame = self._seek_read(start)
        if ok and frame is not None:
            self._head = start
            self._push(start, frame)
            end = target + _FILL_AHEAD
            if self.total_frames:
                end = min(end, self.total_frames - 1)
            while self._head < end:
                ok2, f2 = self._seq_read()
                if not ok2:
                    break
                self._head += 1
                self._push(self._head, f2)
        else:
            # seek 失败（cap 卡死）：标记 stale，下次 open 重建
            self._stale = True
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None

    # ── 底层带超时保护的 read / seek ──
    def _seq_read(self):
        """顺序 read（无 set）。超时保护防止 Windows 上 cap.read 永久 hang。"""
        if self.cap is None:
            return False, None
        result = {"ok": False, "frame": None}

        def _do():
            try:
                ret, fr = self.cap.read()
                result["ok"] = ret
                result["frame"] = fr
            except Exception:
                pass

        t = threading.Thread(target=_do, daemon=True)
        t.start()
        t.join(_READ_TIMEOUT)
        return result["ok"], result["frame"]

    def _seek_read(self, frame_idx):
        """cap.set + read，带超时保护。"""
        if self.cap is None:
            return False, None
        result = {"ok": False, "frame": None, "done": False}

        def _do():
            try:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ret, fr = self.cap.read()
                result["ok"] = ret
                result["frame"] = fr
            except Exception:
                pass
            result["done"] = True

        t = threading.Thread(target=_do, daemon=True)
        t.start()
        t.join(_READ_TIMEOUT)
        if result["done"]:
            return result["ok"], result["frame"]
        logging.warning("[DECODER] cv2 hung on %s frame=%d, marking stale",
                        os.path.basename(self.path), frame_idx)
        return False, None


class DecoderManager:
    """clip.source_path -> ClipDecoder 的映射，替代 PreviewPlayer._cap_cache。"""

    def __init__(self):
        self._decs = {}
        self._lock = threading.Lock()
        self.reset_count = 0   # 解码器(重建)次数：stale/closed 后重开 VideoCapture 计数

    def get(self, clip):
        path = clip.source_path
        with self._lock:
            d = self._decs.get(path)
            if d is None or not d.is_open():
                d = ClipDecoder(path)
                self.reset_count += 1   # 新建/重建解码器（首次打开或 stale 重开）
                if d.open():
                    self._decs[path] = d
                else:
                    return None
            return d

    def set_state(self, state):
        with self._lock:
            for d in self._decs.values():
                d.set_state(state)

    def release(self, path=None):
        with self._lock:
            if path is not None:
                d = self._decs.pop(path, None)
                if d is not None:
                    d.release()
            else:
                for d in self._decs.values():
                    d.release()
                self._decs.clear()

    def reset(self):
        self.release()
