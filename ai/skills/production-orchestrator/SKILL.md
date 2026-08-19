---
name: production-orchestrator
description: Plan, advance, pause, resume, or rewind an AI film-production canvas while preserving approved upstream work. Use for one-idea-to-video production, automatic or checkpointed stage progression, interrupted task recovery, and deciding the smallest safe next action.
---

# Production Orchestrator

Treat the canvas project as the source of truth. Do not create an off-canvas parallel workflow.

## Run the workflow

1. Read the source node's `pipeline_stage`, `automation_mode`, selected shot scope, and latest unfinished generator group.
2. Call the readiness contract for the intended next action before submitting providers.
3. Advance only when every blocking issue passes. Preserve warnings in the source status.
4. Stop at asset, K1, and Klast checkpoints in checkpoint mode. Continue without aesthetic pauses in auto mode, but never bypass a technical blocker.
5. Append a compact workflow event containing stage, intended action, gate, outcome, and reason.
6. On interruption, resume pending nodes in the existing group. Do not recreate completed nodes.
7. On rewind, clear only the chosen stage and its descendants. Keep approved upstream media and keep physical files recoverable.

## Choose the next action

Use `ai.production_skills.plan_next_action`. Use its `allowed` field as the execution boundary and surface its `reason` on the canvas.

- `shots_ready` → generate assets after `shot_plan` passes.
- `assets_ready` → generate blocking after `locked_assets` passes.
- `storyboard_panels_ready` → compile clean prompts after `blocking` passes.
- `prompts_ready` → create image generators after `prompts` passes.
- `start_image_candidates_ready` → create Klast after `start_frames` passes.
- `image_candidates_ready` → create videos after `video_anchors` passes.
- `video_ready` → create external dialogue audio after `videos` passes.

Read [references/workflow-contract.md](references/workflow-contract.md) when changing stages, checkpoints, recovery, or project persistence.

## Safety rules

- Never silently switch an explicitly selected model or provider.
- Never use motion-storyboard pixels as final image/video references.
- Never auto-adopt aesthetic candidates in checkpoint mode.
- Never delete upstream work merely because a downstream take failed.
- Keep all decisions inspectable and removable as canvas data.
