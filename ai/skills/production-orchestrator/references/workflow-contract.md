# Workflow contract

This project adapts workflow patterns rather than copying repository code.

- [HKUDS/ViMax](https://github.com/HKUDS/ViMax) inspired resumable orchestration, multi-stage state, and checkpoint recovery.
- [Jellyfish](https://github.com/Forget-C/Jellyfish) inspired explicit production entities and asynchronous task visibility.
- [Wind Comic](https://github.com/ChrisChen667788/wind-comic) inspired stage persistence and local rerun instead of restarting the entire film.

The CreativeEngine implementation remains the authority. Keep the seven public stages stable unless saved-project migration is included. Treat `workflow_trace` as an audit log, not as executable truth; derive the current action from `pipeline_stage` and actual generator state.

Checkpoint mode pauses only for meaningful creative approval: authoritative assets, K1, and Klast. Auto mode may adopt current candidates, but must still stop on missing files, missing contracts, provider failures, or incomplete character authority sets.
