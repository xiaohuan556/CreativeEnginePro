---
name: shot-readiness
description: Validate whether shots can enter asset, blocking, prompt, image, video, audio, or delivery stages. Use before expensive generation, when a step is stuck, when continuity inputs may be incomplete, or when explaining exactly what the producer must confirm next.
---

# Shot Readiness

Run deterministic checks before generation. Separate blockers from warnings and never use a subjective quality score as a technical gate.

## Evaluate a gate

1. Select the exact shot scope used by the next generator group.
2. Call `ai.production_skills.evaluate_readiness` with the intended gate, board, linked asset records, and file-existence function.
3. Block only on missing executable inputs. Surface optional spatial details as warnings when the prompt compiler can repair them.
4. Store the structured report on the source node as `readiness_report`.
5. Route each blocker to its `repair_step` and focus its `shot_id` or `node_id` on the canvas.

## Gate meanings

- `shot_plan`: stable ID, positive duration, and executable visual/action.
- `locked_assets`: approved files, locked versions, and authoritative four-part character references.
- `blocking`: confirmed staging, 3+ motion keyframes, motion board, and entry/exit states.
- `prompts`: clean final image prompt and an accepted shot contract.
- `start_frames`: adopted K1 exists on disk.
- `video_anchors`: K1 exists; require Klast for strict dual-anchor production.
- `videos`: every in-scope shot has a selected video.
- `delivery`: videos plus independent audio for dialogue shots.

Read [references/readiness-contract.md](references/readiness-contract.md) before changing severity, field requirements, or backward-compatibility policy.

## Output rules

- Return issue codes stable enough for tests and UI routing.
- Include human-readable Chinese messages.
- Do not mutate the project while evaluating.
- Do not claim a file is ready unless it exists.
