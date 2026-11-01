---
type: operations architecture
title: PostgreSQL, Workers, Reconciliation, and Observability
description: How Open SWE persists operational data, runs durable asynchronous work, recovers from missed or stalled execution, and exposes analytics, audit, live-update, and request-correlation signals.
tags: [postgresql, migrations, background-jobs, observability, reconciliation]
verified:
  - by: openwiki/0.4.2
    at: 2026-11-01T08:08:25.741Z
sources:
  - id: openwiki-source-a4aaa0f63a38b5655ce97972
    resource: repo://openswe/analytics/capture.py
  - id: openwiki-source-9fcb687a67ed857e2da77896
    resource: repo://openswe/analytics/outbox.py
  - id: openwiki-source-f66a8da4ee15886c6d545ddd
    resource: repo://openswe/analytics/worker.py
  - id: openwiki-source-4b1279a0a1e5ec2d55a4558a
    resource: repo://openswe/api/app.py
  - id: openwiki-source-a1ce8d749c508a0238207a1c
    resource: repo://openswe/api/request_ids.py
  - id: openwiki-source-1c10d1b4bdf51f06452acf76
    resource: repo://openswe/api/tracing.py
  - id: openwiki-source-207aefcaab1a73f59734eea0
    resource: repo://openswe/audit_logs/middleware.py
  - id: openwiki-source-c6b451aaf94a41c9ff957ad6
    resource: repo://openswe/audit_logs/store.py
  - id: openwiki-source-2d29cb50e2b917c0c150594d
    resource: repo://openswe/database/migrations/versions/0066_5b2d2caf7c5a_job_queue.py
  - id: openwiki-source-f175238f542eb112a8427909
    resource: repo://openswe/database/migrations/versions/0069_c8d8a5c65df3_job_and_thread_index_table_shape.py
  - id: openwiki-source-5ed6a670f969678cfd4ddca6
    resource: repo://openswe/database/notifications.py
  - id: openwiki-source-a13697e04823548408653de5
    resource: repo://openswe/database/postgres.py
  - id: openwiki-source-1501ebbb20af772307a3e518
    resource: repo://openswe/jobs/kinds.py
  - id: openwiki-source-cd47fe08eddb4619c6ba9c8b
    resource: repo://openswe/jobs/queue.py
  - id: openwiki-source-dcbefd98f1da3b6fb7dd8e68
    resource: repo://openswe/jobs/worker.py
  - id: openwiki-source-34d496899c8a38f20f349e4f
    resource: repo://openswe/reconcile.py
  - id: openwiki-source-a9948eeff19407dbd37e95f5
    resource: repo://openswe/threads/stalls.py
  - id: openwiki-source-67b94d49c28542b9f2313bb4
    resource: repo://openswe/transcript/engine.py
  - id: openwiki-source-e1895c186d2c595977e01875
    resource: repo://openswe/transcript/listener.py
  - id: openwiki-source-6922535f6d64f951fd826089
    resource: repo://openswe/ui_invalidations/hub.py
  - id: openwiki-source-2512319e9271dcf5a394f96f
    resource: repo://openswe/ui_invalidations/outbox.py
  - id: openwiki-source-207041ea6aef8242981dc8d7
    resource: repo://openswe/ui_invalidations/routes.py
  - id: openwiki-source-ecb57aa0f8ed4cfa0c7e39b9
    resource: repo://tests/analytics/test_delivery.py
  - id: openwiki-source-33e354dda942ff54749cbec6
    resource: repo://tests/database/test_postgres_read_only.py
  - id: openwiki-source-3ef8041305afeb35047b3f9a
    resource: repo://tests/jobs/test_jobs.py
generated: { by: "openwiki/0.4.2", at: "2026-11-01T08:08:25.741Z" }
---

Open SWE uses PostgreSQL as the durable coordination and read-model store alongside external runtime services. The FastAPI lifespan validates required configuration, migrates the database before serving, then starts listeners and workers; shutdown stops them before disposing database and HTTP resources. See [Thread Runtime](/openwiki/architecture/thread-runtime.md) for the execution model, [Configuration and Deployment](/openwiki/operations/configuration-and-deployment.md) for environment settings, and [Testing Overview](/openwiki/testing/overview.md) for the broader test strategy.

## PostgreSQL lifecycle, schema, and transactions

`POSTGRES_URI` is mandatory for normal operation. It is normalized to the `postgresql+asyncpg` driver form (with `sslmode` converted to `ssl`), and non-PostgreSQL schemes are rejected. In local development, the `local_dev` API variant supplies a default local URI. The shared async engine uses pre-ping and configurable pool size, overflow, and timeout; it tags connections with application name `open-swe` and logs queries that cross `POSTGRES_SLOW_QUERY_MS` with duration, SQL statement, and failure status.

All application tables live in the `open_swe` schema. `connection()` sets its search path, while `transaction()` sets it locally and commits/rolls back as the context exits. Use `read_only_transaction()` for trustworthy read paths; it rolls back settings before returning the connection. For arbitrary SQL, use `untrusted_read_only_transaction()`: it invalidates the connection so session state such as advisory locks cannot leak into the pool. A multi-statement snapshot plus live-event reduction should use `snapshot_transaction()`, which is repeatable-read and read-only rather than per-statement read-committed.

Migrations are Alembic revisions under `openswe/database/migrations/versions`. Startup serializes migration across replicas with a PostgreSQL transaction advisory lock, creates the schema if needed, and upgrades to all heads. Preview deployments additionally drop stamped revisions that no longer chain to a current head, but abort rather than replaying every migration when *all* stamped revisions are unknown. Migration failures are startup failures, not a background best effort.

When adding persistence, add an additive Alembic migration rather than creating tables at runtime. Follow the repository’s shared-record conventions where applicable: UUID primary keys, database-clock timestamps, `created_by`/`updated_by` attribution (with `NULL` for system writes), and PostgreSQL foreign keys with deliberate delete semantics. Preserve external natural identifiers as unique keys where needed rather than substituting them for record identity. For example, jobs have UUID identity, timestamps, and actor columns, while `thread_index` keeps a unique external `thread_id` beside its UUID primary key.

## Application startup and cross-replica notifications

```mermaid
sequenceDiagram
    participant App as FastAPI lifespan
    participant DB as PostgreSQL
    participant Listen as shared listener
    participant Jobs as job worker
    participant Analytics as analytics worker
    App->>DB: validate and migrate schema
    App->>Analytics: load reporting and start
    App->>Listen: register transcript bridge and UI channels
    App->>Jobs: start standing jobs and polling
    DB-->>Listen: committed notifications
    Listen-->>App: route payload to local subscribers
    App->>Jobs: stop on shutdown
    App->>Listen: stop listeners
    App->>Analytics: stop worker
    App->>DB: dispose engine
```

This shows the ordering of persistence, local services, and shutdown in the application lifespan.

Several noncritical starters are deliberately isolated: analytics, transcript listening, sandbox-bridge listening, UI invalidations, stale-cron cleanup, and agent-build warming log exceptions and allow the application to serve with reduced freshness or slower first use. Job polling and the shared notification listener are started after their channels have been registered. A failed listener does not make local writers fail: it primarily prevents this replica from immediately hearing writes performed by another replica.

There is one shared `asyncpg` LISTEN connection per process. Channels register a synchronous payload handler and optional asynchronous reconnect handler. The listener reconnects with exponential delay from 0.5 to 30 seconds, catches up each channel after reconnect, and isolates a bad payload handler or catch-up routine so it cannot tear down other channels. Notifications sent while disconnected are inherently lost, so durable channel-specific replay is the correctness mechanism.

## Durable jobs and scheduled work

The database-backed `job` queue carries a handler `kind`, JSON payload and metadata, due time, optional cron expression/time zone, optional unique key, attempts, lease, and claim token. Calling `schedule()` with a key replaces the prior queued job by default; `replace=False` preserves the existing schedule and claim. One-shot jobs are removed on successful completion, while cron jobs are moved to the next fire time in their configured time zone.

```mermaid
stateDiagram-v2
    [*] --> Queued
    Queued --> Claimed: due and lease free
    Claimed --> Queued: retry or cron next fire
    Claimed --> Done: one shot succeeds
    Claimed --> Removed: cancellation
    Claimed --> Claimed: heartbeat renews lease
    Claimed --> Claimed: another worker takes expired lease
```

This is the durable job lifecycle; an expired lease permits recovery by another replica.

Every replica may run the worker, but `claim_due()` uses `FOR UPDATE SKIP LOCKED` and a 30-minute lease to assign a due row to just one claimant. A per-run claim token prevents an older worker from deleting or rescheduling a row that was replaced and reclaimed. Up to 20 handlers run concurrently; the worker polls every 250 ms while it starts work and every 5 seconds while idle, renewing claims every 10 seconds. Cancellation is observed at a heartbeat and cancels the handler. On shutdown, active handlers are cancelled; their rows remain claimable after lease expiry.

Handler exceptions are logged and a one-shot job retries with exponential backoff starting at 30 seconds and capped at one hour. It gives up after five attempts and is deleted; a failing cron job advances to its next fire rather than retrying the same fire. Missing handler kinds take the same retry path. Lease-renewal errors are logged and retain the existing lease until it expires, preferring recovery over silently losing work.

`openswe.jobs.kinds` is the extension boundary: importing it registers handlers before `JOB_WORKER` starts. It registers domain work such as schedules, workspace refresh, monitoring, delivery, cost refreshes, cleanup, thread wakeups, and reconciliation. Startup imports legacy server crons and thread indexes and establishes standing keyed jobs, including thread-status reconciliation and retention/cleanup jobs. Automation fires take a durable event claim keyed by job ID and scheduled due time, making a re-claimed fire idempotent. Conversely, a reclaimed thread wakeup is deliberately skipped because the prior worker may have already launched the external run; it is safer not to duplicate it.

## Reconciliation and stalled-run recovery

The job queue is not the only recovery layer. The `reconcile` handler scans paginated busy threads in the external runtime, lists each thread’s pending runs, and interrupts pending runs older than its 30-minute default threshold. A failure for one thread is logged and does not abort the remainder; a failed thread search ends the sweep and returns the counts accumulated so far.

Thread-status reconciliation also detects a different failure: an active MDA run that has produced no transcript activity for 90 minutes. It claims the `(thread_id, run_id)` recovery action for two days so replicas act once, reports an error through the normal completion path before cancelling the runtime run, and marks the indexed thread `error` if all remaining active runs have already been claimed as stalled. It does not restart frozen runs because re-sent frozen runs were observed to freeze again. If completion reporting fails, it releases the claim so a later sweep can retry.

Operate these mechanisms as safety nets, not substitutes for normal webhook delivery: investigate recurring lease takeovers, jobs that exhaust retries, stale pending runs, and stalled-run failures. Preserve idempotency at external side effects—either an external delivery key, as with GitHub forwarding, or a durable event claim—because a worker can crash after the side effect and before it records job completion.

## Analytics, audit, transcript, and UI freshness

### Analytics delivery

Product transitions emit strict analytics envelopes through a fail-soft capture wrapper: if PostgreSQL is unavailable or capture raises, the product operation continues and a structured warning includes the operation name. Enqueue deduplicates by event ID into an `outbox` row and records capture progress in the same transaction. The analytics worker repeatedly claims pending records (or deliveries whose five-minute lock expired) with `SKIP LOCKED`, ingests them, acknowledges successes, recomputes dirty summary partitions, and enforces retention hourly.

Failed delivery records the exception class, clears the lock, and retries with jittered exponential delay. After `ANALYTICS_OUTBOX_MAX_ATTEMPTS` (default 10), it becomes `dead_letter`; `outbox_status()` reports pending, dead-letter, and stale work for operational checks. Ingestion itself is deduplicated and transactional: a projection failure does not advance processing progress. Treat analytics as at-least-once at the delivery boundary and rely on event/receipt deduplication rather than assuming exactly-once transport.

### Audit records

Audit middleware considers only dashboard `POST`, `PUT`, `PATCH`, and `DELETE` requests. It writes an audit row only when the matched route explicitly opts in with `@audit_endpoint` and authentication has bound an actor. It records operation, success derived from response status, actor and workspace attribution, route/method/status, UUID-shaped path resource IDs, and typed enrichments—not request payloads. Append is idempotent by audit ID; the fail-safe path has a three-second shielded deadline and logs persistence failure without changing the already-completed request. Queries are descending, keyset-paginated by `(request_time, id)` and filterable by operation, actor, key, or workspace.

### Transcript delivery

The transcript is an append-only per-thread log whose event table is the source of truth and whose other tables are projections. `append()` takes a transaction advisory lock on the thread, assigns gapless versions, writes events, projections, attachments/tool output, command receipts, head version, and `pg_notify` in one transaction. Command receipts make replay idempotent: an accepted command returns its original version instead of appending again. The transactional notification means a subscriber cannot observe a version before its rows commit.

SSE transcript subscribers register before replaying events, preventing an initial race. Local publishing is a latency optimization; PostgreSQL notification is the cross-replica path and carries only thread ID plus version. On notification-listener reconnection, the transcript channel queries each subscribed thread’s durable head and publishes it (or a deletion sentinel), allowing readers that fetch by version to catch up.

### Dashboard invalidation

Committed writes invalidate named UI topics by inserting one `ui_invalidation` row per topic and issuing `pg_notify` in that same transaction; topics contain no business data, so browsers refetch their own queries. The invalidation hub fans a notification to local SSE streams subscribed to that topic. Stream registration precedes replay, and the SSE route authorizes requested topics, sends an initial `hello` containing missed and denied topics, then sends coalesced `invalidated` frames or heartbeat frames.

The outbox retains invalidations for one day and is pruned hourly. On reconnect, the hub asks which subscribed topics changed during the listener outage plus a margin. If a requested replay reaches beyond retention—or replay fails—the safe result is to invalidate rather than claim freshness, producing an extra refetch rather than a stale page. Call `invalidate()` last in the transaction so the replay timestamp reflects the commit closely; use `invalidate_standalone()` only after an external write, accepting that its logged failure may delay refresh.

## Request correlation and tracing

The application assigns or accepts only request IDs matching `req_<uuid>` and returns the value in `X-Request-ID`. It stores the ID in request state and tags the current Datadog root span. For dashboard APIs, every 5xx and failed write is logged under `error_id`; if the endpoint raises, the exception log has the same ID. This makes the browser’s error toast searchable in server logs and APM.

Datadog environment is derived from `OPENSWE_ENV` when configured. A second middleware renames the root APM resource after routing to `METHOD route-template`, including when the endpoint raises, so route-level dashboards and alerts are meaningful even when the outer platform tracer sees only a generic mount route. Both tracing integrations degrade to debug logging when Datadog is unavailable or tagging/naming fails.

## Focused verification

The focused tests demonstrate the operational contracts worth preserving:

- `tests/database/test_postgres_read_only.py` proves that read-only transactions reject writes and that the untrusted variant disposes session state such as advisory locks.
- `tests/jobs/test_jobs.py` covers cross-worker single claiming, key replacement, replacement while running, retry exhaustion, cancellation of a running handler, cron time zones, and idempotent recovery behavior for automation and wakeups.
- `tests/analytics/test_delivery.py` covers capture/delivery deduplication, transactional processing progress, retention, and restart persistence.

When changing these systems, retain structured static log messages with diagnostic values in `extra`, log exceptions rather than discarding them, and add a focused failure-path test for changed lease, transaction, replay, or idempotency behavior.
