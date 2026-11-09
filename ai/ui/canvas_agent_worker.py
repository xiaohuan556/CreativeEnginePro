"""Qt boundary for the locally orchestrated canvas Agent."""
from copy import deepcopy
import json
from pathlib import Path
import threading

from PyQt6.QtCore import QThread, pyqtSignal

from ai.canvas_agent_runtime import run_agent_plan, choose_provider, wait_task, AgentCancelled
from ai.providers.base import TaskRequest


class CanvasAgentWorker(QThread):
    stateChanged = pyqtSignal(object)
    completed = pyqtSignal(object)

    def __init__(self, instruction, references, settings, output_dir, manager,
                 *, previous=None, plan_only=False, parent=None):
        super().__init__(parent)
        self.instruction = instruction
        self.references = deepcopy(references)
        self.settings = deepcopy(settings)
        self.output_dir = Path(output_dir)
        self.manager = manager
        self.previous = deepcopy(previous or {})
        self.plan_only = plan_only
        self._cancelled = threading.Event()

    def cancel(self):
        self._cancelled.set()

    def run(self):
        from ai.canvas_agent_planner import plan_agent, validate_plan
        try:
            self.stateChanged.emit({**deepcopy(self.previous), "status": "planning",
                                    "steps": deepcopy(self.previous.get("steps") or {})})
            if self._cancelled.is_set():
                raise AgentCancelled("任务已停止")
            plan = self.previous.get("plan") or plan_agent(self.instruction, self.references, self.settings)
            if plan.get("status") == "unknown":
                from ai.canvas_agent_planner import build_planning_messages, parse_plan_json
                provider = choose_provider(self.manager, "chat", str(self.settings.get("planner_provider") or ""))
                model = self.settings.get("planner_model")
                if not model:
                    from api_config import get
                    model = get("llm").default_model or "gpt-5.5"
                raw = wait_task(self.manager, provider.name, TaskRequest("chat",
                    {"messages": build_planning_messages(self.instruction, self.references, self.settings)},
                    {"model": model, "response_format": {"type": "json_object"}}, use_cache=False),
                    self._cancelled.is_set, lambda _value: None)
                plan = parse_plan_json(raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False),
                                       self.references, self.instruction, self.settings)
            validate_plan(plan, self.references, self.instruction, self.settings)
            needs_input = plan.get("needs_input") or []
            if plan.get("status") == "unknown":
                raise ValueError("还无法规划该任务，请补充目标、素材用途和期望结果。")
            if needs_input:
                state = {"plan": plan, "status": "needs_input", "steps": {},
                         "error": "\n".join(str(value) for value in needs_input)}
            elif self._cancelled.is_set():
                raise AgentCancelled("任务已停止")
            elif self.plan_only:
                state = {"plan": plan, "status": "ready", "steps": {}}
            else:
                state = run_agent_plan(plan, self.references, self.settings, self.output_dir,
                    self.manager, previous=self.previous, cancelled=self._cancelled.is_set,
                    on_state=self.stateChanged.emit)
        except Exception as error:
            state = deepcopy(self.previous)
            if "plan" in locals():
                state["plan"] = deepcopy(plan)
            state.update(status="cancelled" if self._cancelled.is_set() else "failed", error=str(error))
            state.setdefault("steps", {})
        self.completed.emit(deepcopy(state))
