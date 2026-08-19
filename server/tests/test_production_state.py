from creative_server.production_state import State, approve, pause, resume, rewind, start, task_finished


def test_checkpoint_mode_waits_only_at_assets_and_final_images() -> None:
    state = start(State(), "t1")
    state = task_finished(state, "checkpoints", True)
    assert (state.stage, state.status) == (2, "ready")
    state = start(state, "t2")
    state = task_finished(state, "checkpoints", True)
    assert (state.stage, state.status) == (2, "waiting_review")
    state = approve(state)
    assert (state.stage, state.status) == (3, "ready")


def test_resume_keeps_existing_task_and_never_duplicates_a_node() -> None:
    running = start(State(stage=4, completed_stage=3), "same-task")
    paused = pause(running)
    resumed = resume(paused)
    assert resumed.active_task_id == "same-task"
    assert start(resumed, "duplicate-task").active_task_id == "same-task"


def test_rewind_clears_active_task_and_downstream_state() -> None:
    state = rewind(State(stage=6, completed_stage=5, status="running", active_task_id="old"), 4)
    assert state == State(stage=4, completed_stage=3, status="ready", active_task_id=None)
