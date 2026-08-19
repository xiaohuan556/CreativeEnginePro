"""
CreativeEnginePro AI 层 — 统一 AI 能力入口。

分层架构：
    ai/providers/   → AI 能力插件（图片 / 视频 / 语音 / LLM）
    ai/             → TaskManager 统一任务调度
    ai/assets/      → AI 资源中心（人物 / 场景 / Prompt / 声音）
    ai/workflows/   → AI 工作流（短剧 / 带货 / 图文）
    ai/prompts/     → Prompt 模板库
    ai/models/      → 本地模型管理
    ai/cache/       → 生成结果缓存

快速开始：
    from ai import TaskManager, ProviderRegistry

    registry = ProviderRegistry()
    from ai.providers.voice import EdgeTTSProvider
    registry.register(EdgeTTSProvider())

    mgr = TaskManager(registry)
    mgr.start()

    request = TaskRequest(operation="text_to_speech", inputs={"text": "你好"})
    handle = mgr.submit("edge_tts", request)
"""

# ── 核心接口 ──
from .providers.base import (
    AIProvider, ProviderRegistry, ProviderDomain,
    TaskRequest, TaskResult, TaskHandle, TaskStatus,
)
from .task_manager import TaskManager, AITaskSignals, TaskHistoryDB

# ── 资源中心 ──
from .assets import AssetDB, Character, Scene, PromptTemplate, VoicePreset

# ── 工作流 ──
from .workflows import BaseWorkflow, WorkflowStep, StepStatus

__all__ = [
    # Core
    "AIProvider", "ProviderRegistry", "ProviderDomain",
    "TaskRequest", "TaskResult", "TaskHandle", "TaskStatus",
    "TaskManager", "AITaskSignals", "TaskHistoryDB",
    # Assets
    "AssetDB", "Character", "Scene", "PromptTemplate", "VoicePreset",
    # Workflow
    "BaseWorkflow", "WorkflowStep", "StepStatus",
]
