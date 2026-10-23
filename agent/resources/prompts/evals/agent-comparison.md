# Paired agent evaluation comparison

```json
$comparison
```

Every task, including execution failures, contributes to the resolution-rate
denominator. Both runs have the same content-addressed task set, scoring code,
recorded host policy, and concurrency. External dependencies and sandbox/model
versions must also be held fixed by the experiment operator; `baseline_condition`
and `candidate_condition` record the intended variable (model, effort, and the
compaction arm).

Time, turn, token, and compaction changes are medians of per-task differences
(candidate minus baseline). Turn, token, and compaction deltas use only pairs
with observations in both runs; missing observations are not zero. Tokens cover
main-agent messages only. Compaction token counts are middleware estimates, not
billed usage.

`failure_stage_transitions` is the baseline-to-candidate stage matrix, including
`none` (resolved) rows. Read it together with `improved_instance_ids` and
`regressed_instance_ids`; a stage move that does not change resolution still
indicates where the experiment changed behavior. `baseline_overflow_compactions`
and `candidate_overflow_compactions` separate compactions forced by provider
context-overflow rejections from threshold-triggered ones.

These descriptive results do not establish statistical significance or benchmark
comparability. Repeat model experiments and inspect individual regressions before
claiming a performance improvement. See each run's raw predictions and test output.
