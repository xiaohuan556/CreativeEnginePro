---
name: vision-qc-repair
description: Audit generated storyboard images and videos with multimodal evidence, classify defects, and create the smallest shot-scoped repair plan. Use for identity drift, spatial or axis errors, anatomy artifacts, prompt contamination, temporal flicker, dialogue/lip-sync issues, or selective regeneration.
---

# Vision QC Repair

Inspect actual media, not prompts alone. Run automatically after rendering, produce a non-destructive repair plan, and wait for confirmation before regeneration.

## Automatic gates

1. Run **POST-QC** as soon as each video segment has a real local result. Compare its first/middle/last frames with the approved scene master and K1/Klast anchors.
2. Score G1 identity/assets (25), G2 space/axis/eyeline (20), G3 action/time (20), G4 composition/camera (10), G5 render defects/contamination (15), and G6 story/dialogue/sync (10). Require 80/100 and no F1-F6 blocker.
3. After every segment reaches a terminal POST-QC state, run **SEQUENCE-QC** on each adjacent segment's actual tail/head frame pair. Require 85/100 and no blocker.
4. If visual review is unavailable, preserve the generated media, record the missing evidence explicitly, and continue with a warning. Never fabricate a visual pass.
5. A failed gate pauses before audio, selects only the affected shots, and creates or updates one visible `自动审片 · POST + SEQUENCE` canvas node.

## Audit

1. Collect each shot contract, approved assets, clean K1/Klast anchors, selected image, and video first/middle/last frames.
2. Ask the multimodal reviewer for strict JSON containing `id`, `score`, `passed`, `issues`, `issue_codes`, `repair_target`, and `revision`.
3. Require visual evidence for visual pass/fail claims. Mark design-only shots as unverified.
4. Normalize the result with `ai.production_skills.build_repair_plan`.
5. Normalize automatic clip and sequence results with `normalize_clip_qc` and `normalize_sequence_qc`; numeric scores never override blocker codes.

## Route repairs

- `asset` → step 2 for identity, outfit, or source-asset drift.
- `blocking` → step 3 for axis, eyeline, position, geometry, or movement-direction errors.
- `prompt` → step 4 for an incorrect or contaminated instruction.
- `image` → step 5 for anatomy, composition, text, watermark, or still-frame defects.
- `video` → step 6 for motion, flicker, temporal identity, endpoint, or camera errors.
- `audio` → step 7 for voice, dialogue, timing, or lip-sync defects.

Preserve all unrelated shots. For a video-only repair, keep approved assets, blocking, prompts, and K1/Klast. For one bad candidate, detach only that generator's outputs.

Read [references/repair-routing.md](references/repair-routing.md) before adding issue codes or changing branch cleanup behavior.

## Output rules

- Create one visible repair-plan node/group on the canvas.
- Mark only failed shots as selected for repair.
- Never append video-only revisions to the image prompt.
- Never regenerate automatically after review unless the producer explicitly enabled it.
- Let the producer explicitly accept the recorded risk and continue; keep the repair plan and failure marks when they do.
- Offer a canvas review movie that concatenates approved video segments and places shot TTS at timeline offsets. The review mix replaces model speech and never modifies source media.
