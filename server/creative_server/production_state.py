from dataclasses import dataclass


STAGES = {
    1: "拆解镜头",
    2: "生成资产候选",
    3: "生成调度与多帧运动分镜",
    4: "确认调度并合成定稿提示词",
    5: "创建定稿图片生成器组",
    6: "确认定稿图片并生成视频",
    7: "创建对白音频组",
}
CHECKPOINT_STAGES = {2, 5}


@dataclass(frozen=True)
class State:
    stage: int = 1
    completed_stage: int = 0
    status: str = "ready"
    active_task_id: str | None = None


def should_wait(mode: str, completed_stage: int) -> bool:
    return mode == "manual" or (mode == "checkpoints" and completed_stage in CHECKPOINT_STAGES)


def start(state: State, task_id: str) -> State:
    if state.status == "complete": return state
    if state.active_task_id: return State(state.stage, state.completed_stage, "running", state.active_task_id)
    if state.status not in {"ready", "paused", "failed"}: return state
    return State(state.stage, state.completed_stage, "running", task_id)


def task_finished(state: State, mode: str, success: bool) -> State:
    if not success:
        return State(state.stage, state.completed_stage, "failed", None)
    completed = state.stage
    if completed >= max(STAGES):
        return State(completed, completed, "complete", None)
    if should_wait(mode, completed):
        return State(completed, completed, "waiting_review", None)
    return State(completed + 1, completed, "ready", None)


def approve(state: State) -> State:
    if state.status != "waiting_review": return state
    if state.stage >= max(STAGES): return State(state.stage, state.stage, "complete", None)
    return State(state.stage + 1, state.completed_stage, "ready", None)


def pause(state: State) -> State:
    if state.status in {"running", "ready"}: return State(state.stage, state.completed_stage, "paused", state.active_task_id)
    return state


def resume(state: State) -> State:
    if state.status != "paused": return state
    return State(state.stage, state.completed_stage, "running" if state.active_task_id else "ready", state.active_task_id)


def rewind(state: State, target_stage: int) -> State:
    if target_stage not in STAGES: raise ValueError("无效的重做阶段")
    return State(target_stage, target_stage - 1, "ready", None)
