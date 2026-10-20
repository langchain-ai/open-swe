# Agent evaluation report

Experiment completed: $completed. Evaluated instances: $evaluated_instances.

```json
$configuration
```

| Metric | Value |
|---|---:|
$metric_rows

| Instance | Resolved | Failure stage | Model turns | Agent seconds |
|---|---|---|---:|---:|
$instance_rows

All evaluated instances, including execution failures, contribute to the
resolution-rate denominator. An incomplete experiment is not a benchmark result.
See `results.jsonl` for full command output, failed and missing test IDs,
exit codes, phase durations, and the originating thread ID.
Model-turn counts are unavailable when execution fails before final state is
returned; those rows are excluded from the median of observed turns.

Token counts cover main-agent messages only. Missing usage is not zero usage;
`usage_complete_rate` reports coverage within that scope. Subagent, compaction,
retry, and monetary costs require tracing data and are not estimated here.
