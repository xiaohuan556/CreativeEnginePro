"""
AI 工作流基类。

工作流 = 多个 AI 任务的串联编排。
每个 Workflow 定义步骤列表 + 步骤间的数据流。

用法：
    class ShortDramaWorkflow(BaseWorkflow):
        name = "short_drama"
        steps = [
            ("gen_script", "deepseek", "chat"),
            ("gen_images", "seedream", "text_to_image"),
            ("gen_videos", "seedance", "image_to_video"),
            ("gen_voice", "fish_audio", "text_to_speech"),
            ("assemble", "builtin", "composite"),
        ]

        async def run(self, input_data: dict) -> dict:
            ...
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Any


class StepStatus(Enum):
    PENDING = auto()
    RUNNING = auto()
    DONE = auto()
    FAILED = auto()
    SKIPPED = auto()


@dataclass
class WorkflowStep:
    """工作流中的一步。"""
    name: str
    provider: str           # "seedream" / "deepseek" / "builtin" / ...
    operation: str          # "text_to_image" / "chat" / ...
    status: StepStatus = StepStatus.PENDING
    result: Any = None
    error: str = ""


class BaseWorkflow(ABC):
    """工作流基类。"""

    name: str = "base"
    description: str = ""
    steps: list[tuple[str, str, str]] = []   # [(step_name, provider, operation), ...]

    def __init__(self, task_manager=None):
        self.task_manager = task_manager      # TaskManager 实例（由框架注入）

    @abstractmethod
    def run(self, input_data: dict) -> dict:
        """执行工作流。返回 {step_name: result}。"""
        ...

    def step_list(self) -> list[WorkflowStep]:
        return [WorkflowStep(name=n, provider=p, operation=o) for n, p, o in self.steps]

    def __repr__(self):
        return f"<Workflow {self.name} steps={len(self.steps)}>"
