"""
AI Provider 基类 — 所有 AI 能力的统一接口。

设计原则：
1. 每个 Provider 只负责一种能力域（图片 / 视频 / 语音 / LLM）。
2. UI 不直接调用 Provider。所有请求通过 TaskManager 调度。
3. 新增 Provider 只需继承 AIProvider + 实现 execute()，UI 零改动。

用法：
    provider = SeedreamProvider(api_key="xxx")
    request = TaskRequest(operation="text_to_image", inputs={"prompt": "..."})
    handle = provider.execute(request)   # 返回 TaskHandle，包含 id / status / progress
"""

from __future__ import annotations

import uuid
import time
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional
from pathlib import Path


# ──────────────────────────────────────────────
# 枚举
# ──────────────────────────────────────────────

class TaskStatus(Enum):
    QUEUED = auto()        # 已入队，等待执行
    RUNNING = auto()       # 执行中
    DOWNLOADING = auto()   # 下载结果中（云端 API）
    DONE = auto()          # 完成
    FAILED = auto()        # 失败
    CANCELLED = auto()     # 已取消


class ProviderDomain(Enum):
    """Provider 所属能力域"""
    IMAGE = "image"
    VIDEO = "video"
    VOICE = "voice"
    LLM = "llm"


# ──────────────────────────────────────────────
# 核心数据结构
# ──────────────────────────────────────────────

@dataclass
class TaskRequest:
    """一次 AI 任务的请求体。

    UI 层构造此对象，交给 TaskManager.submit()。
    """
    operation: str                              # "text_to_image" / "image_to_video" / "text_to_speech" / ...
    inputs: dict[str, Any] = field(default_factory=dict)   # {"prompt": "...", "image": bytes, ...}
    params: dict[str, Any] = field(default_factory=dict)   # {"width": 1024, "steps": 20, ...}
    priority: int = 0                           # 数字越大优先级越高
    metadata: dict[str, Any] = field(default_factory=dict) # 任意透传数据（回调用）
    use_cache: bool = True                      # 生成新候选时设为 False，避免复用旧结果

    def to_cache_key(self) -> str:
        """生成幂等缓存键（相同请求不重复调用 API）。"""
        raw = json.dumps({
            "op": self.operation,
            "inputs": {k: str(v)[:200] for k, v in self.inputs.items()},
            "params": self.params,
        }, sort_keys=True, ensure_ascii=False)
        import hashlib
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class TaskResult:
    """Provider.execute() 的返回值。"""
    success: bool
    data: Any = None                    # 生成结果（bytes / Path / str / dict ...）
    error: str = ""
    cache_hit: bool = False
    cost_credits: float = 0.0           # API 调用消耗点数
    provider_raw: dict[str, Any] = field(default_factory=dict)  # 原始返回（调试用）


@dataclass
class TaskHandle:
    """返回给调用方的任务句柄。

    调用方通过此对象轮询进度 / 获取结果 / 取消任务。
    """
    id: str
    provider_name: str
    operation: str
    status: TaskStatus = TaskStatus.QUEUED
    progress: float = 0.0               # 0.0 ~ 1.0
    result: Optional[TaskResult] = None
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    # 内部回调链（TaskManager 管理，调用方不直接操作）
    _on_done: list[Callable[[TaskHandle], None]] = field(default_factory=list, repr=False)
    _cancel_token: bool = field(default=False, repr=False)

    @property
    def is_finished(self) -> bool:
        return self.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)

    @property
    def is_success(self) -> bool:
        return self.status == TaskStatus.DONE and self.result is not None and self.result.success

    def cancel(self):
        """取消任务。Provider 应在 execute() 中周期性检查 _cancel_token。"""
        self._cancel_token = True
        self.status = TaskStatus.CANCELLED
        self.finished_at = time.time()

    def notify_done(self):
        """触发所有完成回调（TaskManager 在任务结束时调用）。"""
        for cb in self._on_done:
            try:
                cb(self)
            except Exception:
                pass

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "provider": self.provider_name,
            "operation": self.operation,
            "status": self.status.name,
            "progress": round(self.progress, 3),
            "success": self.is_success,
            "error": self.result.error if self.result else "",
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


# ──────────────────────────────────────────────
# Provider 基类
# ──────────────────────────────────────────────

class AIProvider(ABC):
    """所有 AI Provider 的抽象基类。

    子类只需实现：
    - name: str                  Provider 唯一名称
    - domain: ProviderDomain     所属能力域
    - capabilities: list[str]    支持的操作列表
    - execute(request) -> TaskHandle    核心执行逻辑
    """

    name: str
    domain: ProviderDomain
    capabilities: list[str]              # 如 ["text_to_image", "image_edit"]
    requires_auth: bool = True

    def __init__(self, api_key: str = "", **config):
        self.api_key = api_key
        self.config = config
        self._validate()

    def _validate(self):
        """子类可重写，在构造时校验 API Key / 配置有效性。"""
        if self.requires_auth and not self.api_key:
            raise ValueError(f"{self.name}: api_key 不能为空")

    def supports(self, operation: str) -> bool:
        """检查是否支持指定操作。"""
        return operation in self.capabilities

    def capability_profile(self) -> dict:
        """Expose the real generation control surface to planners and gates."""
        try:
            from ai.production_contracts import model_profile
            return model_profile(self.name, {"operations":list(self.capabilities)})
        except Exception:
            return {"name":self.name, "operations":list(self.capabilities)}

    @abstractmethod
    def execute(self, request: TaskRequest) -> TaskHandle:
        """执行任务。

        Provider 负责：
        1. 创建 TaskHandle（id 用 uuid）
        2. 设置 status 为 RUNNING
        3. 周期性更新 progress + 检查 handle._cancel_token
        4. 完成后设置 result + status 为 DONE / FAILED
        5. 返回 handle（不调用 notify_done — 由 TaskManager 统一调）

        注意：此方法应在**后台线程**中调用，不应阻塞 UI。
        """
        ...

    def __repr__(self):
        return f"<{self.name} domain={self.domain.value} caps={self.capabilities}>"


# ──────────────────────────────────────────────
# Provider 注册表
# ──────────────────────────────────────────────

class ProviderRegistry:
    """全局 Provider 注册中心。

    用法：
        registry = ProviderRegistry()
        registry.register(SeedreamProvider(api_key="xxx"))
        provider = registry.get("seedream")
        image_providers = registry.by_domain(ProviderDomain.IMAGE)
    """

    def __init__(self):
        self._providers: dict[str, AIProvider] = {}

    def register(self, provider: AIProvider):
        if provider.name in self._providers:
            raise ValueError(f"Provider '{provider.name}' 已注册")
        self._providers[provider.name] = provider

    def unregister(self, name: str):
        self._providers.pop(name, None)

    def get(self, name: str) -> Optional[AIProvider]:
        return self._providers.get(name)

    def by_domain(self, domain: ProviderDomain) -> list[AIProvider]:
        return [p for p in self._providers.values() if p.domain == domain]

    def by_capability(self, operation: str) -> list[AIProvider]:
        return [p for p in self._providers.values() if p.supports(operation)]

    def list_all(self) -> list[AIProvider]:
        return list(self._providers.values())

    @property
    def count(self) -> int:
        return len(self._providers)
