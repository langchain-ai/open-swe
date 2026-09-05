---
type: workflow
title: Scheduling, Background Work, and CI Monitoring
description: How deterministic scheduler ticks launch recurring automations, one-shot wakeups, cost refreshes, reconciliation, and sandbox monitoring. It also explains durable /baby-sit PR watches, signed GitHub CI events, and bounded flaky-CI handling.
tags: [scheduler, cron, background-tasks, ci-monitoring, baby-sit, thread-wakeup, reconciliation, cost-accounting]
sources:
  - id: openwiki-source-d2bd9c9ce8ccfbe9c55e6d30
    resource: repo://agent/agent_cost.py
  - id: openwiki-source-d87936e6d54eab24f7479af1
    resource: repo://agent/baby_sit.py
  - id: openwiki-source-26c2c4725a171eaf524f2ad7
    resource: repo://agent/background_tasks.py
  - id: openwiki-source-838cdb388dc01d838e2807cc
    resource: repo://agent/bundled_skills/baby-sit/SKILL.md
  - id: openwiki-source-068d65a84c760eb8d555055e
    resource: repo://agent/completion.py
  - id: openwiki-source-202e70aa1fb446ab05cc6d99
    resource: repo://agent/dashboard/schedules.py
  - id: openwiki-source-6664f6fd05037c7c782f7b09
    resource: repo://agent/github/comments.py
  - id: openwiki-source-3d1c7beecd605173281a3bf6
    resource: repo://agent/github/routes.py
  - id: openwiki-source-ba064e884edcde6097165df2
    resource: repo://agent/github/webhook.py
  - id: openwiki-source-1116ea2d477f08cf0f5b2ef0
    resource: repo://agent/graphs/scheduler.py
  - id: openwiki-source-739850fbbfceb2f1f047ce4e
    resource: repo://agent/middleware/record_run_usage.py
  - id: openwiki-source-d2c2e4ba7449d086f84f8ccd
    resource: repo://agent/reconcile.py
  - id: openwiki-source-3e15117ace082a39e1f130d8
    resource: repo://agent/scheduler.py
  - id: openwiki-source-75a22f97d6fc2af5a1a279e7
    resource: repo://agent/session_cost.py
  - id: openwiki-source-c3b12b5693b6aa5458b6b53a
    resource: repo://agent/tools/manage_baby_sit.py
  - id: openwiki-source-9a9aaf4b265831fa9c7e3bd2
    resource: repo://agent/tools/schedule_thread_wakeup.py
  - id: openwiki-source-5bbba7b2a8ea8360ff233d63
    resource: repo://langgraph.json
  - id: openwiki-source-b11620c8b3f8d7354abe85a9
    resource: repo://tests/agent/test_baby_sit.py
  - id: openwiki-source-a8868f4abfd7eb37a9a9680e
    resource: repo://tests/github/test_baby_sit_webhook.py
  - id: openwiki-source-a565a4a1fb4d3fc05d998ca3
    resource: repo://tests/reviewer/test_reconcile_sweep.py
  - id: openwiki-source-749071bb736ba933e244501a
    resource: repo://tests/tools/test_manage_baby_sit.py
  - id: openwiki-source-7416596e0d9fc9b802355ff6
    resource: repo://tests/tools/test_schedule_thread_wakeup.py
verified:
  - by: openwiki/0.4.2
    at: 2026-09-05T08:12:56.060Z
generated: { by: "openwiki/0.4.2", at: "2026-09-05T08:12:56.060Z" }
---

# Scheduling, Background Work, and CI Monitoring

Scheduling is deliberately split between a small, model-free dispatch graph and the producers that own each job's state and lifecycle. The scheduler is not an agent: it receives a LangGraph cron or delayed-run tick and calls one handler. A handler only resumes an `agent` thread when new work merits a model call.

## Scheduler routing

`agent.scheduler` is registered as the `scheduler` assistant. Its graph is one node, `START → launch → END`; `_launch` gets `task` and its arguments from graph state or `config.configurable`. It routes reconciliation, baby-sit, background-task, Slack session-cost, and usage-cost jobs explicitly. An unrecognized or absent task is a dashboard schedule tick and launches the stored schedule. Missing required identifiers return a `missing_*` result rather than raising.

```mermaid
flowchart TD
  Tick["Cron or delayed run"] --> Launch["scheduler launch"]
  Launch -->|"reconcile"| Reconcile["reconcile stale runs"]
  Launch -->|"baby_sit"| Watch["evaluate PR watch"]
  Launch -->|"background_tasks"| Background["monitor sandbox tasks"]
  Launch -->|"session_cost"| SessionCost["refresh Slack footer"]
  Launch -->|"agent_cost"| AgentCost["persist usage cost"]
  Launch -->|"otherwise"| Schedule["launch stored agent schedule"]
```

Diagram: one deterministic scheduler tick selects exactly one maintenance or automation handler.

### Dashboard recurring runs

The dashboard owns workspace schedules in `agent_schedules`; their run history is kept separately in `agent_schedule_run_state`. Creation validates a normalized five-field cron expression, creates a cron targeted at `scheduler`, and rolls back the store record if cron creation fails. Schedule changes or re-enabling create a replacement cron before deleting the old one; disabling and deletion remove the cron.

When a schedule fires, `launch_scheduled_agent_run` loads the record. It declines disabled schedules and verifies that the schedule owner still has access to its configured repository. A successful launch creates a fresh automation thread and durable `agent` run, optionally establishes a Slack root thread, and records the latest thread, run, timestamp, or launch error in the separate run-state record. This isolation means user-editable schedule configuration is not overwritten by operational status.

### Stale-run reconciliation

Normal durable runs rely on completion handling to release a busy thread. `reconcile_stale_runs` is the recovery sweep for a lost completion: it pages through busy threads, lists pending runs, and interrupts those older than 1,800 seconds by default. It skips unparsable timestamps, isolates errors per thread, and returns thread, stale-run, and cancellation counts. Deploy a periodic `task=reconcile` tick to make this safety net effective; the router alone does not register one.

## Delayed and background work

### One-shot thread wakeups

`schedule_thread_wakeup` is the agent-facing fallback for a future poll when no webhook-driven workflow exists. It accepts an integer delay from one minute through 24 hours, rounds the target to a UTC minute, and creates a thread-bound cron for the `agent` assistant—not `scheduler`. The cron includes a system polling prompt (or the supplied prompt), selected source/repository/context configuration, normal run preparation, an optional completion webhook, and an `end_time` 90 seconds after firing. The end time makes the five-field cron single-fire in practice.

Wakeups are deliberately bounded: the tool records a count in thread metadata and permits at most ten between human input-message generations. A new human message changes the generation and resets the count; a system message does not. It records the budget before creating the cron, so a metadata failure prevents scheduling. Fired cron rows remain, therefore each scheduling request makes a best-effort, paginated purge of only `metadata.kind=thread_wakeup` rows whose `end_time` is past.

### Sandbox background-task monitoring

Starting sandbox background commands can arrange an every-minute, thread-specific `background_tasks` scheduler cron. Registration searches by kind and thread, reuses one cron, and deletes duplicates. On a tick, monitoring loads the thread's sandbox; a missing sandbox removes monitoring crons.

For terminal command states (`completed`, `failed`, `timed_out`, `stopped`, or `lost`), the monitor atomically claims a task notification using a sandbox directory, dispatches a system completion message to the originating agent thread with enqueue semantics, then persists delivery by renaming the claim. If dispatch fails, it releases the claim for a later tick. Once no command is running and no terminal notification remains, a second sandbox lock and fresh task listing guard deletion of the monitoring cron against races.

### Deferred cost enrichment

Cost availability can lag run completion, so both cost paths form bounded chains of stateless delayed runs targeting `scheduler`, each deleted on completion. For a successful Slack-connected agent run, completion schedules `session_cost`; it looks up the run-to-Slack-message mapping, obtains the LangSmith thread aggregate, and updates the Slack usage footer. Missing trace data or a failed Slack fetch/update is `pending` and schedules the next delay from `(15, 30, 60, 120, 240)` seconds; unavailable/invalid data and exhaustion stop the chain.

Separately, after-agent usage middleware records token telemetry and schedules `agent_cost` for a completed prepared run. The handler asks LangSmith for that run's cost (`run_only=True`) and persists it in dashboard usage data. Transient lookup or persistence failure consumes the same bounded retry sequence; an explicitly unavailable LangSmith integration stops without retrying.

## Durable `/baby-sit` CI watches

The `/baby-sit` skill uses durable watches in cloud runs. Local/desktop execution instead uses a bounded foreground `gh pr checks --watch` loop and must not call the durable watch or wakeup tools. The agent-facing `manage_baby_sit` tool validates a canonical GitHub PR URL, requires the current executable thread, and prevents a configured repository from watching a different repository. Starting also verifies an open PR, its head SHA/ref, GitHub authentication, and an installed GitHub App; stop and retry recording enforce watch ownership by the current thread.

A `BabySitWatch` is persisted in `baby_sit_watches`, keyed as lower-case `owner/repo#number`. It retains the originating thread, PR head identity, App installation, a safe subset of run configuration, source destination context, cron id, retry and settling state, bounded failure/webhook/alert dedupe lists, and consecutive evaluation errors. Only one active originating thread may watch a PR. Reusing the same head carries retry and dedupe state; a changed head resets it.

Starting a watch creates or reuses one UTC `*/10 * * * *` `baby_sit_watch` cron targeted at `scheduler`; duplicates are deleted. A new watch is rolled back if cron setup fails. `stop_watch` deletes the store row only when no `cron_id` is recorded; when a cron id is present it attempts deletion and returns, leaving the watch row active after a successful deletion. If that deletion fails, it marks the row inactive so subsequent evaluation cleans it up. This current asymmetry means operators should treat a successful cron deletion as insufficient evidence that the durable watch record is gone.

```mermaid
sequenceDiagram
  participant GitHub
  participant Route as GitHub route
  participant Scheduler
  participant Watch as Baby sit watch
  participant Agent as Origin thread
  participant Source as Source context

  GitHub->>Route: Signed failing CI event
  Route->>Watch: Queue CI evaluation
  Scheduler->>Watch: Ten minute fallback tick
  Watch->>Watch: Acquire watch lock and fetch PR plus checks
  Watch-->>Watch: Pending settling or duplicate has no agent run
  Watch->>Agent: New failure queues continue run
  Watch->>Source: Terminal outcome notification
```

Diagram: signed CI events provide prompt evaluation while the cron supplies recovery polling; both use the same locked watch evaluation.

### Event processing, evaluation, and completion

`POST /webhooks/github` checks the raw-body `X-Hub-Signature-256` HMAC before it accepts any event; an absent webhook secret is also rejected. For allowlisted repositories, `check_run`, `check_suite`, `workflow_run`, and `status` events are queued as FastAPI background work and forwarded to `handle_ci_webhook`. It ignores payloads that do not represent failing CI, finds active watches by repository plus head SHA or branch, updates an installation id when supplied, and remembers up to 50 delivery ids before evaluation.

The webhook path and the cron path both use a per-watch LangGraph thread lock with a five-minute TTL. A lock collision returns `busy`, preventing concurrent evaluation and duplicate failure dispatch. Evaluation fetches the PR and current check runs plus commit statuses, then aggregates them as `pending`, `failure`, `blocked`, or `success`. It resets retry/settling/failure and alert dedupe state when the fetched PR head changes. Three consecutive failures to obtain token, PR, head, or CI status finish the watch with a permissions/access warning; a successful evaluation resets this error counter.

A failure is dispatched only once for each head SHA and retry count. The resulting `/baby-sit --continue` run is enqueued on the originating thread, and its prompt labels names, URLs, and fetched logs as untrusted data, requiring verification and evidence before a rerun. Dispatch failure removes the fingerprint so a later evaluation can retry delivery.

Success is intentionally delayed: the exact check/status set must remain unchanged for ten minutes before completion is announced. A closed or merged PR, settled success, blocked terminal checks, retry-cap exhaustion, and repeated evaluation failure are terminal outcomes. The watch first posts to its `SourceContext`—Slack, then Linear, then a GitHub issue/comment target—and otherwise queues `/baby-sit --terminal` on the originating thread; it then stops the watch.

### Flaky-job policy

The monitoring service never classifies a failure as flaky itself. It supplies a confidence-gated prompt; after an evidence-backed GitHub Actions rerun, the agent calls `record_retry`. The operation is locked, requires the same owner thread and head SHA, and caps retries at three per head. It sanitizes the check/evidence and accepts a details link only from `https://github.com/`. The first distinct check-and-URL alert for that head is sent to the source; later identical alerts are suppressed. A later evaluation after the cap terminates the watch if CI remains failing.

## Focused tests

- `tests/agent/test_baby_sit.py` exercises cron lifecycle, lock contention, webhook-delivery dedupe, failure dispatch/retry behavior, settling, terminal fallback, and scheduler routing.
- `tests/github/test_baby_sit_webhook.py` verifies all supported CI event types reach the background handler without a mention but still require a valid signature.
- `tests/tools/test_manage_baby_sit.py` covers start context and configured-repository enforcement; `tests/tools/test_schedule_thread_wakeup.py` covers validation, cron construction, wakeup budgets, and conservative expiry cleanup.
- `tests/reviewer/test_reconcile_sweep.py` covers stale-run paging, age handling, cancellation, and failure isolation.
