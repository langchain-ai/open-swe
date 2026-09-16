# Opening provenance correction

Opening attribution is rejected only when the attributed invocation's `run_projection.started_at`
(from `run.started`, not thread creation, event recording, completion, or observation time) is
**more than 24 hours after** `pr_projection.opened_at` (the opening event's GitHub `created_at`).
This deliberately conservative tolerance rejects clear multi-day contradictions without treating
five minutes of clock skew as proof. At or below 24 hours, or without either timestamp, leave
provenance alone. This is a rejection policy, not proof that every remaining opener is correct.
Capture's existing-PR suppression and configured-model fallback remain unchanged.

The shared projection rule runs on opening, invocation-start, and PR/run-link events, and before
configured-model repair. It clears the contradicted `opening_run_id` and `originating_model_id`,
sets quality to `unavailable`, and deletes only contradicted `opening` links. Outcomes, observations,
follow-up/review links, invocation/thread identity, and raw events are not rewritten. Replaying
retained events uses the same rule, including links arriving after correction. No tombstone is
needed while the durable PR and invocation timestamps remain available. If timestamps were never
captured or were lost, this rule cannot establish a contradiction; it does not invent evidence.
Existing summaries do not group by opening run or model and need no recomputation.

### Earlier-start corrections and scope

The projector accepts distinct `run.started` identities for the same run and takes their earliest
`occurred_at`. If a later start first causes rejection, a subsequently ingested earlier start does
not restore the cleared claim. This is an actual projector-level order dependence, not a guarantee
of convergence for arbitrary corrected evidence. The summary regression
`tests/analytics/test_summaries.py::test_earlier_run_start_moves_cost_and_membership_out_of_previous_day`
exercises distinct synthetic event identities; it does not demonstrate a capture path.

The current invocation producer emits `run:{invocation_id}:started` with producer `open-swe` and
the current schema version (`agent/analytics/emitter.py::run_started`). Timestamp and source version
are not part of that event UUID (`agent/analytics/events.py::event_uuid`). Outbox insertion and
`ingest` deduplicate this identity before projection. Thus retrying that producer with an earlier
timestamp cannot exercise the `LEAST` update while its deduplication identity is retained. No current
second producer or versioned start-correction path was found. A changed natural key, producer, or
schema version could make this reachable, as could manual projection replay of conflicting bodies;
those are not supported start corrections for this narrowly scoped rejection policy.

Do not extend this correction into automatic restoration or infer a replacement opener. If a future
producer supports start corrections, it must explicitly address revalidation using only retained
original opening/link evidence. Missing or expired raw evidence cannot justify restoring a claim.
This limitation also means the replay statement above applies to the current producer's retained
events, not arbitrary conflicting start events.

Timestamp evidence: `agent/analytics/usage.py::record_agent_invocation_usage` calls `run_started`
without an explicit timestamp, so `agent/analytics/emitter.py::enqueue_event` uses emission-time UTC;
this is invocation-start telemetry, not an independently measured execution-start timestamp.
`agent/analytics/ingestion.py::_project` stores its `occurred_at` as `started_at`.
`agent/tools/open_pull_request.py` supplies GitHub `created_at` to
`agent/analytics/usage.py::record_agent_pr_usage`, which uses that timestamp when parseable and falls back
to current UTC otherwise. The PR projection stores the opening event's `occurred_at` as `opened_at`.
`agent/analytics/events.py::make_event` assigns `recorded_at` separately; recording time is not used
by the contradiction predicate. GitHub creation-time provenance therefore requires confirming the
capture supplied a parseable `created_at`, rather than assuming every opening event has it.

## Operator procedure

Deploy the new ingestion code before correcting history. Use an approved database connection via
`POSTGRES_URI`; do not put credentials in command arguments or logs. No migration or production
correction runs automatically. From the repository root, the following is a **read-only dry run**:

```sh
uv run python -m agent.analytics.attribution \
  --workspace-id 7849c27e-81ef-4651-8982-719c84d95e7d \
  --pr-id aba01c5a-66a4-5b57-97f2-1db4d93de929
```

The confirmed incident is [langchain-google PR #1993](https://github.com/langchain-ai/langchain-google/pull/1993),
natural key `pr:langchain-ai/langchain-google#1993:opened`. GitHub creation was
`2026-09-03T01:55:59Z`; analytics run `455db9e6-ff8d-5aea-baed-72b11b609348`
(original invocation `95562c2a-c095-4f15-8d1b-35cab031c991`) started
`2026-09-15T18:30:18.812349Z`. The event was recorded `2026-09-15T18:45:05.478535Z`;
that recording time is not used as opening evidence.

Verify the dry run lists exactly the expected PR and independently verify these timestamps in the
selected database. Append `--apply` to the same command to commit. Apply takes short-lived locks
on the three affected projection tables to serialize against ingestion (including other workspaces);
a five-second lock timeout aborts safely if busy. Schedule for a quiet period. All writes are scoped
to the explicit workspace and PR. Repeat the dry run: it should report an empty `pr_ids` list.
Repeated apply is a no-op. An empty dry run can also mean missing evidence or the wrong workspace/PR,
so do not interpret it alone as proof of correction. Raw-event audits will still show the original
claim by design; opening analytics must use the corrected projections.
