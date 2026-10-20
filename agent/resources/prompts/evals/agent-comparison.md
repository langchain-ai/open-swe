# Paired agent evaluation comparison

```json
$comparison
```

Every task, including execution failures, contributes to the resolution-rate
denominator. Both runs have the same content-addressed task set, scoring code,
recorded host policy, and concurrency. External dependencies and sandbox/model
versions must also be held fixed by the experiment operator.

Time and turn changes are medians of per-task differences (candidate minus
baseline). Turn and token deltas use only pairs with observations in both runs;
missing observations are not zero. Tokens cover main-agent messages only.

These descriptive results do not establish statistical significance or benchmark
comparability. Repeat model experiments and inspect individual regressions before
claiming a performance improvement. See each run's raw predictions and test output.
