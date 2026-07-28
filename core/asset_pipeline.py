# -*- coding: utf-8 -*-
"""批处理引擎：扫描 → 推理 → 保存 三线程队列。

设计（对齐产品规格 Producer / Consumer / IO 三线程）：
  Producer  递归扫描输入目录，把 (idx, 绝对路径, 相对路径) 推入 work_q
  Consumer  取一张 → 加载 → 跑 Pipeline（插件列表）→ 结果推入 save_q
  Saver    取结果 → 保持子目录结构写盘 → 更新进度/状态

特点：
  - 内存友好：始终"读一张→推理→存→释放"，不整批加载。
  - 有界队列（maxsize）做背压，GPU/CPU 永不空转也不爆内存。
  - 进度 / 速度(张/s) / ETA / 每项状态（done/failed/skipped）。
  - 暂停(resume) / 停止(abort) / 失败重试。
  - 失败项记录 (相对路径, 错误)，retry_failed() 仅重跑失败项。

回调（在 worker 线程调用，UI 用 pyqtSignal 转发到主线程）：
  on_start(total) / on_progress(done, total, speed, eta) /
  on_item(relpath, status, msg) / on_finish(summary)
"""
import os
import time
import queue
import threading
from PIL import Image
import numpy as np

ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp"}
_FMT_MAP = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}


class BatchProcessor:
    def __init__(self, callbacks=None):
        self.cb = callbacks or {}
        self.input_dir = ""
        self.output_dir = ""
        self.plugins = []
        self.ctx = {}
        self._reset()

    def _reset(self):
        self._stop = threading.Event()
        self._pause = threading.Event()
        self.work_q = queue.Queue(16)
        self.save_q = queue.Queue(16)
        self.total = 0
        self.done = 0
        self.ok = 0
        self.skipped = 0
        self.failed = []
        self.start_time = None
        self._threads = []
        self._running = False
        self._counter = 1

    # ───────────────────────── 配置 / 启动 ─────────────────────────
    def configure(self, input_dir, output_dir, plugins, ctx):
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.plugins = list(plugins)
        self.ctx = ctx or {}

    def start(self):
        if self._running:
            return
        jobs = self._scan()
        self._run(jobs)

    def retry_failed(self):
        if not self.failed or self._running:
            return
        jobs = []
        for i, (rel, _err) in enumerate(self.failed):
            ap = os.path.join(self.input_dir, rel)
            jobs.append((i, ap, rel))
        self.failed = []
        self._run(jobs)

    def _run(self, jobs):
        self.total = len(jobs)
        self.done = 0
        self.ok = 0
        self.skipped = 0
        self.start_time = time.time()
        self._counter = 1
        self._running = True
        self._stop.clear()
        self._pause.clear()
        self._emit("on_start", self.total)
        if self.total == 0:
            self._running = False
            self._emit("on_finish", {"total": 0, "ok": 0, "failed": 0, "skipped": 0})
            return
        t_prod = threading.Thread(target=self._producer, args=(jobs,), daemon=True)
        t_cons = threading.Thread(target=self._consumer, daemon=True)
        t_sav = threading.Thread(target=self._saver, daemon=True)
        self._threads = [t_prod, t_cons, t_sav]
        for t in self._threads:
            t.start()

    # ───────────────────────── 扫描 ─────────────────────────
    def _scan(self):
        jobs = []
        for root, _dirs, files in os.walk(self.input_dir):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in ALLOWED_EXT:
                    ap = os.path.join(root, f)
                    rel = os.path.relpath(ap, self.input_dir)
                    jobs.append((len(jobs), ap, rel))
        return jobs

    # ───────────────────────── 线程：生产者 ─────────────────────────
    def _producer(self, jobs):
        for item in jobs:
            if self._stop.is_set():
                break
            self.work_q.put(item)
        self.work_q.put(None)  # 哨兵

    # ───────────────────────── 线程：消费者 ─────────────────────────
    def _consumer(self):
        while True:
            item = self.work_q.get()
            if item is None:
                self.save_q.put(None)
                break
            idx, ap, rel = item
            if self._stop.is_set():
                self.save_q.put((idx, ap, rel, None, "已取消"))
                continue
            while self._pause.is_set() and not self._stop.is_set():
                time.sleep(0.1)
            if self._stop.is_set():
                self.save_q.put((idx, ap, rel, None, "已取消"))
                continue
            try:
                arr = self._load(ap)
                for p in self.plugins:
                    arr = p.run(arr, self.ctx)
                self.save_q.put((idx, ap, rel, arr, None))
            except Exception as e:  # noqa: BLE001
                self.save_q.put((idx, ap, rel, None, str(e)[:300]))

    # ───────────────────────── 线程：保存 ─────────────────────────
    def _saver(self):
        while True:
            item = self.save_q.get()
            if item is None:
                break
            self._after_item(item)

    def _after_item(self, item):
        _idx, _ap, rel, arr, err = item
        if err is not None:
            if err == "已取消":
                self.skipped += 1
                self._emit("on_item", rel, "skipped", err)
            else:
                self.failed.append((rel, err))
                self._emit("on_item", rel, "failed", err)
        else:
            try:
                self._save(arr, rel)
                self.ok += 1
                self._emit("on_item", rel, "done", "")
            except Exception as e:  # noqa: BLE001
                self.failed.append((rel, str(e)[:300]))
                self._emit("on_item", rel, "failed", str(e)[:300])
        self.done += 1
        self._emit_progress()
        if self.done >= self.total:
            self._running = False
            self._emit("on_finish", {
                "total": self.total, "ok": self.ok,
                "failed": len(self.failed), "skipped": self.skipped,
            })

    # ───────────────────────── 加载 / 保存 ─────────────────────────
    @staticmethod
    def _load(ap):
        im = Image.open(ap)
        return np.array(im.convert("RGBA"))

    def _save(self, arr, rel):
        base = os.path.dirname(rel)
        stem, ext0 = os.path.splitext(os.path.basename(rel))
        ext0 = ext0.lower().lstrip(".")

        rn = self.ctx.get("rename") or {}
        new_stem = self._apply_rename(stem, rn)

        cv = self.ctx.get("convert") or {}
        fmt = (cv.get("format") or "").lower().lstrip(".")
        if fmt == "jpeg":
            fmt = "jpg"
        out_ext = fmt if fmt else ext0

        out_dir = os.path.join(self.output_dir, base)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, new_stem + "." + out_ext)

        im = Image.fromarray(arr)
        if out_ext == "jpg":
            im = im.convert("RGB")
        quality = int((self.ctx.get("compress") or {}).get("quality", 95))
        kwargs = {}
        if out_ext in ("jpg", "webp"):
            kwargs["quality"] = quality
        elif out_ext == "png":
            kwargs["optimize"] = True
        im.save(out_path, format=_FMT_MAP.get(out_ext, "PNG"), **kwargs)

    def _apply_rename(self, stem, rn):
        pattern = (rn.get("pattern") or "").strip()
        if not pattern:
            return stem
        if "{" not in pattern:
            return pattern
        num = self._counter
        self._counter += 1
        return (pattern.replace("{name}", stem)
                       .replace("{num}", f"{num:04d}"))

    # ───────────────────────── 控制 ─────────────────────────
    def pause(self):
        self._pause.set()

    def resume(self):
        self._pause.clear()

    def stop(self):
        self._stop.set()
        self._pause.clear()

    @property
    def is_running(self):
        return self._running

    # ───────────────────────── 回调 ─────────────────────────
    def _emit_progress(self):
        elapsed = time.time() - self.start_time if self.start_time else 0
        speed = self.done / elapsed if elapsed > 0.01 else 0.0
        eta = (self.total - self.done) / speed if speed > 0 else 0.0
        self._emit("on_progress", self.done, self.total, speed, eta)

    def _emit(self, name, *args):
        cb = self.cb.get(name)
        if cb:
            try:
                cb(*args)
            except Exception:  # noqa: BLE001
                pass
