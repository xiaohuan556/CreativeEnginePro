# Repair routing

Order issues by the earliest source of truth:

1. Asset identity/outfit/source error.
2. Blocking, geometry, axis, eyeline, or screen-direction error.
3. Prompt compilation or annotation-contamination error.
4. Still-image anatomy, composition, text, or watermark error.
5. Video motion, flicker, temporal identity, endpoint, or camera error.
6. Audio, dialogue, timing, voice, or lip-sync error.

If one finding could match several targets, choose the earliest stage whose correction prevents the defect from recurring. Preserve unrelated branches and approved upstream artifacts.

Review evidence should include selected stills and video first/middle/last frames. A prompt-only review can diagnose design risk but cannot claim a rendered shot visually passed.

## Automatic review contract

- POST-QC runs per rendered segment and writes its result back to that video generator node.
- SEQUENCE-QC runs after all POST-QC jobs finish and compares actual outgoing tail frames with actual incoming first frames. Do not compare hand-drawn boards as rendered evidence.
- Hard cuts may change composition, but identity, wardrobe, prop state, time/light, geography, action phase, screen direction, eyeline, and axis must remain explainable.
- F1 identity, F2 space/axis, F3 severe anatomy or contamination, F4 broken action, F5 missing story information, and F6 unusable speech/lip-sync are blockers regardless of score.
- A failed transition marks the incoming and outgoing shots for inspection, but routes regeneration to the earliest responsible target and does not delete either branch.
- The combined canvas preview uses approved video order plus each shot's `video_segment_offset` and `dialogue_audio`; it is review media, not a destructive edit or final export.

The workflow borrows the “vision audit → retry only the failed stage” pattern from [Wind Comic](https://github.com/ChrisChen667788/wind-comic) and the shot/task entity separation from [Jellyfish](https://github.com/Forget-C/Jellyfish); this implementation uses original project data structures.
