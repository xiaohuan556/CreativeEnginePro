"""
AI Task Manager — 统一任务调度中心。

职责：
1. 接收 TaskRequest → 匹配 Provider → 入队 → 调度执行
2. 并发控制（CPU 任务 / GPU 任务 / API 请求分池）
3. 失败重试（指数退避）
4. 结果缓存（相同参数不重复调 API）
5. 持久化历史（SQLite，可查询）
6. 进度信号（Qt signals，UI 可绑定）

用法：
    manager = TaskManager(registry)
    manager.start()

    request = TaskRequest(operation="text_to_image", inputs={"prompt": "..."})
    handle = manager.submit("seedream", request)

    # 方式 1：轮询
    while not handle.is_finished:
        time.sleep(0.5)

    # 方式 2：回调
    handle._on_done.append(lambda h: print("完成!", h.result.data))

    # 方式 3：Qt 信号（见 AITaskSignals）
"""

from __future__ import annotations

import os
import time
import json
import sqlite3
import hashlib
import threading
import traceback
from queue import PriorityQueue, Queue
from pathlib import Path
from typing import Any, Callable, Optional
from dataclasses import dataclass, field

from .providers.base import (
    AIProvider, TaskRequest, TaskResult, TaskHandle, TaskStatus,
    ProviderDomain, ProviderRegistry,
)


def _transient_failure(error: str) -> bool:
    """Return True only for failures that can plausibly succeed on retry."""
    text = str(error or "").lower()
    return any(token in text for token in (
        " 429", "status code: 429", "rate limit", "too many requests",
        " 500", " 502", " 503", " 504", "gateway timeout", "cloudfront",
        "service unavailable", "upstream timed out", "timed out", "timeout",
        "connection reset", "connection aborted", "connection refused",
        "temporarily unavailable", "remote end closed", "network error",
        "网络错误", "连接超时", "服务暂时不可用",
    ))


# ──────────────────────────────────────────────
# Qt 信号桥（可选，UI 侧通过此信号接收进度/完成通知）
# ──────────────────────────────────────────────

class AITaskSignals:
    """轻量信号总线（不依赖 PyQt6 import，允许纯逻辑层测试）。

    实际使用中由 UI 层注入 QObject 子类替换此占位实现。
    """
    def __init__(self):
        self._listeners: dict[str, list[Callable]] = {}

    def connect(self, event: str, callback: Callable):
        self._listeners.setdefault(event, []).append(callback)

    def emit(self, event: str, *args):
        for cb in self._listeners.get(event, []):
            try:
                cb(*args)
            except Exception:
                pass


# ──────────────────────────────────────────────
# 任务条目（内部队列数据结构）
# ──────────────────────────────────────────────

@dataclass(order=True)
class _TaskEntry:
    priority: int
    enqueued_at: float = field(compare=False)
    provider_name: str = field(compare=False)
    request: TaskRequest = field(compare=False)
    handle: TaskHandle = field(compare=False)


# ──────────────────────────────────────────────
# SQLite 历史持久化
# ──────────────────────────────────────────────

class TaskHistoryDB:
    """所有已完成 / 失败 / 取消的任务入库，可查询/统计。"""

    def __init__(self, db_path: str | Path = ""):
        if not db_path:
            db_path = Path(os.environ.get("CEP_DATA_DIR", Path.home() / ".cep_data")) / "ai_tasks.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._migrate()
        return self._conn

    def _migrate(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                operation TEXT NOT NULL,
                status TEXT NOT NULL,
                success INTEGER DEFAULT 0,
                error TEXT DEFAULT '',
                cache_hit INTEGER DEFAULT 0,
                cost_credits REAL DEFAULT 0.0,
                duration_ms INTEGER DEFAULT 0,
                cache_key TEXT DEFAULT '',
                request_json TEXT DEFAULT '{}',
                created_at REAL NOT NULL,
                finished_at REAL
            )
        """)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        self.conn.commit()

    def save(self, handle: TaskHandle, request: TaskRequest, cache_key: str = ""):
        with self._lock:
            duration = 0
            if handle.finished_at:
                duration = int((handle.finished_at - handle.created_at) * 1000)
            self.conn.execute("""
                INSERT OR REPLACE INTO tasks
                (id, provider, operation, status, success, error, cache_hit,
                 cost_credits, duration_ms, cache_key, request_json, created_at, finished_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                handle.id, handle.provider_name, handle.operation,
                handle.status.name,
                1 if handle.is_success else 0,
                handle.result.error if handle.result else "",
                1 if (handle.result and handle.result.cache_hit) else 0,
                handle.result.cost_credits if handle.result else 0.0,
                duration,
                cache_key,
                json.dumps({"operation": request.operation, "params": request.params},
                           ensure_ascii=False),
                handle.created_at,
                handle.finished_at,
            ))
            self.conn.commit()

    def query(self, limit: int = 50, offset: int = 0,
              status: Optional[str] = None, provider: Optional[str] = None) -> list[dict]:
        sql = "SELECT * FROM tasks WHERE 1=1"
        params: list = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if provider:
            sql += " AND provider = ?"
            params.append(provider)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(zip([d[0] for d in rows[0].__class__.__dict__], row)) if False else
                { "id": row[0], "provider": row[1], "operation": row[2],
                  "status": row[3], "success": bool(row[4]), "error": row[5],
                  "cache_hit": bool(row[6]), "cost_credits": row[7],
                  "duration_ms": row[8], "cache_key": row[9],
                  "request_json": row[10], "created_at": row[11],
                  "finished_at": row[12] }
                for row in rows]

    def stats(self) -> dict:
        row = self.conn.execute("""
            SELECT COUNT(*), SUM(CASE WHEN success THEN 1 ELSE 0 END),
                   SUM(cost_credits), SUM(duration_ms)
            FROM tasks
        """).fetchone()
        return {"total": row[0], "success": row[1] or 0,
                "credits": row[2] or 0, "total_ms": row[3] or 0}

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


# ──────────────────────────────────────────────
# 结果缓存
# ──────────────────────────────────────────────

class TaskCache:
    """基于文件的简单缓存。

    cache_key 来自 TaskRequest.to_cache_key()。
    """

    def __init__(self, cache_dir: str | Path = ""):
        if not cache_dir:
            cache_dir = Path(os.environ.get("CEP_CACHE_DIR", Path.home() / ".cep_cache")) / "ai"
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def get(self, cache_key: str) -> Optional[bytes]:
        path = self.cache_dir / f"{cache_key}.bin"
        if path.exists():
            try:
                return path.read_bytes()
            except Exception:
                return None
        return None

    def put(self, cache_key: str, data: bytes):
        path = self.cache_dir / f"{cache_key}.bin"
        with self._lock:
            try:
                path.write_bytes(data)
            except Exception:
                pass

    def has(self, cache_key: str) -> bool:
        return (self.cache_dir / f"{cache_key}.bin").exists()

    def clear(self):
        for f in self.cache_dir.glob("*.bin"):
            try:
                f.unlink()
            except Exception:
                pass


# ──────────────────────────────────────────────
# TaskManager 核心
# ──────────────────────────────────────────────

class TaskManager:
    """AI 任务调度中心。

    线程模型：
    - _submit_thread: 从 registry 匹配 Provider，构造 handle，入队。
    - _workers (N 个): 从队列取任务 → Provider.execute() → 回调 / 持久化。

    并发控制：
    - max_api_workers:  云端 API 并发数（默认 3）
    - max_local_workers: 本地推理并发数（默认 1，避免 CPU/GPU 争抢）

    用法：
        mgr = TaskManager(registry)
        mgr.start()
        handle = mgr.submit("seedream", TaskRequest(...))
        handle._on_done.append(lambda h: print("done"))
    """

    def __init__(self, registry: ProviderRegistry,
                 max_api_workers: int = 3,
                 max_local_workers: int = 1,
                 retry_count: int = 2,
                 enable_cache: bool = True,
                 db_path: str = "",
                 cache_dir: str = ""):
        self.registry = registry
        self.max_api_workers = max_api_workers
        self.max_local_workers = max_local_workers
        self.retry_count = retry_count
        self.enable_cache = enable_cache

        self.history = TaskHistoryDB(db_path)
        self.cache = TaskCache(cache_dir) if enable_cache else None
        self.signals = AITaskSignals()

        # 队列
        self._queue: PriorityQueue[_TaskEntry] = PriorityQueue()

        # 活跃任务
        self._active: dict[str, TaskHandle] = {}
        self._active_lock = threading.Lock()

        # 线程控制
        self._running = False
        self._workers: list[threading.Thread] = []
        self._shutdown_event = threading.Event()

    # ── 生命周期 ──

    def start(self):
        """启动工作线程池。"""
        if self._running:
            return
        self._running = True
        self._shutdown_event.clear()

        # API workers
        for _ in range(self.max_api_workers):
            t = threading.Thread(target=self._worker_loop, args=("api",), daemon=True)
            t.start()
            self._workers.append(t)

        # Local workers
        for _ in range(self.max_local_workers):
            t = threading.Thread(target=self._worker_loop, args=("local",), daemon=True)
            t.start()
            self._workers.append(t)

    def stop(self, wait: bool = True):
        """停止所有工作线程。"""
        self._running = False
        self._shutdown_event.set()
        # 放入哨兵值唤醒所有阻塞的 worker
        for _ in self._workers:
            self._queue.put(_TaskEntry(priority=-999, enqueued_at=0,
                                        provider_name="", request=TaskRequest(""),
                                        handle=TaskHandle(id="__sentinel__", provider_name="",
                                                          operation="")))
        if wait:
            for t in self._workers:
                t.join(timeout=3)
        self._workers.clear()
        self.history.close()

    # ── 提交任务 ──

    def submit(self, provider_name: str, request: TaskRequest) -> TaskHandle:
        """提交一个 AI 任务。

        Args:
            provider_name: 已注册的 Provider 名称（如 "seedream"）
            request: 任务请求

        Returns:
            TaskHandle: 任务句柄，可通过它轮询进度/取消/设置回调

        Raises:
            ValueError: Provider 未注册或不支持该操作
        """
        provider = self.registry.get(provider_name)
        if provider is None:
            raise ValueError(f"Provider '{provider_name}' 未注册")

        if not provider.supports(request.operation):
            raise ValueError(
                f"Provider '{provider_name}' 不支持操作 '{request.operation}'。"
                f"支持：{provider.capabilities}"
            )

        handle = TaskHandle(
            id=f"task_{uuid_lite()}",
            provider_name=provider_name,
            operation=request.operation,
        )

        with self._active_lock:
            self._active[handle.id] = handle

        entry = _TaskEntry(
            priority=-request.priority,   # PriorityQueue 越小越优先
            enqueued_at=time.time(),
            provider_name=provider_name,
            request=request,
            handle=handle,
        )

        self._queue.put(entry)
        self.signals.emit("task_queued", handle.to_dict())

        return handle

    # ── Worker 循环 ──

    def _worker_loop(self, worker_type: str):
        """工作线程主循环。"""
        while self._running:
            try:
                entry = self._queue.get(timeout=1)
            except Exception:
                continue

            # 哨兵值 — 退出
            if entry.handle.id == "__sentinel__":
                continue

            handle = entry.handle
            request = entry.request
            provider = self.registry.get(entry.provider_name)

            if provider is None:
                handle.status = TaskStatus.FAILED
                handle.result = TaskResult(success=False, error="Provider 已注销")
                self._finish(handle, request)
                continue

            # 执行任务（含重试）
            self._execute_with_retry(provider, request, handle)
            self._finish(handle, request)

    def _execute_with_retry(self, provider: AIProvider, request: TaskRequest, handle: TaskHandle):
        """执行任务，失败时按指数退避重试。"""
        last_error = ""
        cache_enabled = self.enable_cache and self.cache is not None and request.use_cache
        try:
            retry_count = max(0, min(
                5, int(request.metadata.get("retry_count", self.retry_count))))
        except (TypeError, ValueError):
            retry_count = self.retry_count
        transient_only = bool(request.metadata.get("retry_transient_only", False))

        for attempt in range(retry_count + 1):
            if handle._cancel_token:
                return

            try:
                # 尝试缓存命中
                if cache_enabled:
                    ck = request.to_cache_key()
                    cached = self.cache.get(ck)
                    if cached is not None:
                        handle.status = TaskStatus.DONE
                        handle.progress = 1.0
                        handle.result = TaskResult(success=True, data=cached, cache_hit=True)
                        handle.finished_at = time.time()
                        return

                # 调用 Provider
                handle.status = TaskStatus.RUNNING
                result_handle = provider.execute(request)

                # 合并结果
                handle.status = result_handle.status
                handle.progress = result_handle.progress
                handle.result = result_handle.result
                handle.finished_at = result_handle.finished_at or time.time()

                if handle.is_success:
                    # 写入缓存
                    if cache_enabled and handle.result and handle.result.data:
                        ck = request.to_cache_key()
                        if isinstance(handle.result.data, bytes):
                            self.cache.put(ck, handle.result.data)
                        elif isinstance(handle.result.data, (str, Path)):
                            p = Path(handle.result.data)
                            if p.exists():
                                self.cache.put(ck, p.read_bytes())
                    return
                else:
                    last_error = handle.result.error if handle.result else "未知错误"
                    # 参数/内容策略类错误不会因重试而改变。尤其 Seedance 常把
                    # 品牌/IP/素材策略拒绝笼统返回为 Invalid base64 image_url。
                    # 立即返回，避免同一请求被重复提交多次。
                    permanent_markers = (
                        "invalid base64", "invalidparameter", "badrequest",
                        "privacyinformation", "real person", "copyright",
                        "sensitive", "审核", "版权", "不支持",
                    )
                    if any(marker in last_error.lower() for marker in permanent_markers):
                        return
                    if transient_only and not _transient_failure(last_error):
                        return

            except Exception as e:
                last_error = str(e)
                traceback.print_exc()
                if transient_only and not _transient_failure(last_error):
                    break

            # 重试前等待
            if attempt < retry_count:
                wait = min(8, 2 ** (attempt + 1))  # 2s, 4s, 8s ...
                time.sleep(wait)

        # 全部重试失败
        handle.status = TaskStatus.FAILED
        handle.result = TaskResult(success=False, error=last_error)
        handle.finished_at = time.time()

    def _finish(self, handle: TaskHandle, request: TaskRequest):
        """任务结束后的统一收尾：持久化 + 回调 + 信号。"""
        ck = request.to_cache_key() if self.enable_cache and request.use_cache else ""
        self.history.save(handle, request, ck)
        with self._active_lock:
            self._active.pop(handle.id, None)
        handle.notify_done()
        self.signals.emit("task_finished", handle.to_dict())

    # ── 查询 ──

    def active_count(self) -> int:
        with self._active_lock:
            return len(self._active)

    def pending_count(self) -> int:
        return self._queue.qsize()

    def find_handle(self, task_id: str) -> Optional[TaskHandle]:
        with self._active_lock:
            return self._active.get(task_id)


# ──────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────

def uuid_lite() -> str:
    """轻量唯一 ID（12 字符），避免完整 uuid4 的臃肿。"""
    import random
    import string
    ts = int(time.time() * 1000) % (36 ** 6)
    rnd = random.randint(0, 36 ** 6 - 1)
    alphabet = string.digits + string.ascii_lowercase
    def _b36(n):
        s = ""
        for _ in range(6):
            s = alphabet[n % 36] + s
            n //= 36
        return s
    return _b36(ts) + _b36(rnd)
