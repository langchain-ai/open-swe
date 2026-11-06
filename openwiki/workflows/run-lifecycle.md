---
type: workflow
title: Invocation, dispatch, and continuation
description: How dashboard, API, GitHub, Slack, and Linear work become durable agent runs, and how threads, inboxes, runtime reports, completion, and follow-up pickup preserve the conversation.
tags: [run-lifecycle, dispatch, threads, managed-runtime, integrations]
verified:
  - by: openwiki/0.4.2
    at: 2026-11-04T08:09:12.042Z
sources:
  - id: openwiki-source-66d57d4fcd8fa04d35848667
    resource: repo://mda/rt/loop.py
  - id: openwiki-source-46c7d96fd7d65774ed102441
    resource: repo://mda/rt/outbox.py
  - id: openwiki-source-913527bc7b548b4bf81f6a35
    resource: repo://openswe/completion.py
  - id: openwiki-source-1685d34aae8025be9332f45a
    resource: repo://openswe/dispatch.py
  - id: openwiki-source-836966ba5e0c4d710801c9a9
    resource: repo://openswe/input_messages.py
  - id: openwiki-source-41b3f3ac3c45b0e2ff376c3d
    resource: repo://openswe/invocation.py
  - id: openwiki-source-ab57de9770aafc1c1c0da3e6
    resource: repo://openswe/linear/channel.py
  - id: openwiki-source-8150cb2d41bed45b1f3b10a4
    resource: repo://openswe/linear/session.py
  - id: openwiki-source-a4ee5d7441799febd770ba39
    resource: repo://openswe/slack/mda_forwarding.py
  - id: openwiki-source-72370931d61f0a7232adcf12
    resource: repo://openswe/slack/webhook.py
  - id: openwiki-source-5e89303bbff1b32492ae2b1c
    resource: repo://openswe/threads/creation.py
  - id: openwiki-source-022ee08422abaad398dba679
    resource: repo://openswe/threads/inbox.py
  - id: openwiki-source-932730a18e85495ceba5df6d
    resource: repo://openswe/threads/router.py
  - id: openwiki-source-5c84530a3d0edb1fb15187f1
    resource: repo://openswe/threads/runs.py
  - id: openwiki-source-8ad5df7d840644498e09c75f
    resource: repo://tests/agent/test_dispatch.py
  - id: openwiki-source-89749f6e9338414ea6744190
    resource: repo://tests/test_task_events.py
  - id: openwiki-source-3de2b7b708190a05e806a12d
    resource: repo://tests/threads/test_stalls.py
  - id: openwiki-source-a9842c19fa28878dfa7fcd61
    resource: repo://tests/webhooks/test_completion_webhook.py
generated: { by: "openwiki/0.4.2", at: "2026-11-04T08:09:12.042Z" }
---

# Invocation, dispatch, and continuation

Open SWE treats a request as a **turn on a durable thread**, not as an isolated model call. The backend owns request policy, identity, thread indexing, transcript and status settlement; the Managed Deep Agents (MDA) runtime executes the graph and calls the backend for catalogued services. This separation is described further in [backend control plane](../architecture/backend-control-plane.md), [managed runtime](../architecture/managed-runtime.md), and [dashboard and channels](../integrations/dashboard-and-channels.md).

## Lifecycle at a glance

```mermaid
sequenceDiagram
    participant Caller as Dashboard API GitHub Slack Linear
    participant Backend as Open SWE backend
    participant Index as Thread index
    participant MDA as MDA runtime
    participant Loop as Runtime loop
    participant Inbox as Thread inbox
    participant Reports as Durable outbox
    participant Complete as Completion webhook

    Caller->>Backend: validated request or channel event
    Backend->>Backend: build input envelope and run config
    alt backend-owned thread
        Backend->>Index: create or route titled thread
        Backend->>MDA: create durable run
    else MDA channel-owned thread
        Backend->>Index: adopt channel thread and mark busy
        Backend-->>MDA: prepared input context metadata webhook
        MDA->>MDA: channel starts its own run
    end
    alt ordinary follow-up
        Backend->>MDA: interrupt active run and start replacement
    else background or ordered work
        Backend->>MDA: enqueue run behind active work
    end
    MDA->>Loop: execute graph from checkpoints
    Loop->>Inbox: read owed follow-ups events tasks
    Inbox-->>Loop: messages and receipt candidates
    Loop->>Reports: reply tools receipts notices
    Reports->>Backend: reports batch with stable item keys
    Backend-->>Reports: acknowledge taken items
    Reports-->>MDA: retain and retry unacknowledged items
    MDA->>Complete: terminal status payload
    Complete->>Index: refresh thread status and settle turn
    alt success with leftover follow-ups
        Complete->>Backend: start reject-mode pickup run
    else error or timeout
        Complete->>Caller: one deduplicated channel failure reply
    end
```

This sequence shows the cross-process dispatch path, including interrupt versus enqueue and the acknowledgement-based reporting path.

## Entrypoints and ownership

### Request sources

- **Dashboard and API.** Dashboard startup creates a titled, indexed thread with ownership, visibility, repository, workspace and chosen-model metadata. When PostgreSQL is configured it additionally appends a `ThreadCreated` transcript event. It then dispatches the first agent run; later dashboard actions use the same thread and the thread router routes the call to its runtime. The dashboard accepts image input only for supported MIME types, limits it to five images of at most 10 MiB each, and rejects image use with a non-vision model.
- **GitHub.** GitHub webhook handlers build structured human or system `RunInput` messages. Issue context is represented as a system webhook message plus the issue/comment history, with per-person introductions for replayed authors that are not the triggering author. This keeps actor identity and untrusted source content explicit rather than embedding a bare prompt.
- **Slack.** A normal Slack turn selects `interrupt` for an explicit request and `enqueue` otherwise. Some Slack conversations are handed to MDA: the backend deterministically maps the Slack root to a thread ID, indexes it as `channel: slack`, remembers the gated request, and forwards the signed Slack request to Trigger Server. Atomic recall and fallback handling ensure a forwarded message is either taken by the channel or reported by the backend, rather than producing duplicate work or silence.
- **Linear.** MDA validates Linear deliveries and calls a backend channel endpoint protected by the MDA shared-secret dependency. Only agent-session events prepare runs; issue updates may launch automations but do not themselves start agent runs. The backend verifies that the author is a linked Open SWE user, resolves repository/workspace and returns prepared input, context, safe metadata, and the completion webhook for MDA to start on the session thread.

A thread with `channel` metadata is a conversation owned by that MDA channel. Backend thread routing permits trusted channel ingress for its run, interruption, and cancellation, but refuses ordinary copy/cron writes to prevent the dashboard or backend from taking over a Slack, Linear, or GitHub conversation.

## From input to an executable run

### Structured identity and invocation identity

`dispatch_agent_run` is the common dispatch contract. It either accepts an already-built `RunInput` or constructs one from content plus a typed message context. The envelope has a namespaced sender, surface, message kind, optional channel, and structured data; channel and system introductions are emitted as deduplicable `dynamic-context` messages. Sender selection is surface-aware: Slack uses the triggering Slack user and channel/thread context, dashboard uses an authenticated user where available, GitHub uses the login, Linear uses email, and automation gets a `system:` identity. Canonical-person lookup normalizes equivalent identities.

Each dispatch has an application-owned UUID `invocation_id`. Configuration and metadata may still carry the legacy `prepare_run_id`, but conflicting or malformed values are rejected; writing normalizes to `invocation_id`. Dispatch stamps a start time and streaming compatibility configuration, removes caller-supplied attribution fields, resolves trace attribution server-side, and only supplies the acting-user capability when that person actually started the run or an MDA channel starts it as its verified sender. This prevents a wakeup, event, or task from impersonating the user it is merely attributed to.

### Thread creation and routing

`create_durable_run` creates a titled thread only when given `thread_title`; a pre-created dashboard or channel thread passes `None`. The thread router is the API boundary in front of LangGraph `/threads` and `/runs`: PostgreSQL is the thread index, new backend-owned threads are created on MDA, and their metadata is asynchronously copied to the runtime. It routes state, history, commands, streams and runs to the indexed runtime. A run successfully accepted by MDA marks the index `busy`; completion and reconciliation later settle it.

Thread titles are mandatory. `ensure_titled_thread` creates missing system-owned threads idempotently and refreshes generated titles, but does not overwrite a title after a person sets `title_locked`.

### Durable dispatch defaults

The shared dispatch function creates LangGraph runs with `multitask_strategy="interrupt"`, async durability, v3 stream modes and subgraph streaming, and non-resumable event streams. `interrupt` preserves checkpointed progress and lets a new user turn continue with history; callers that need ordering, such as sandbox tool results and background work, choose `enqueue`. The run is created with a completion webhook only when `RUN_COMPLETE_WEBHOOK_SECRET` is configured and `COMPLETION_WEBHOOK_URL` is an absolute non-loopback HTTP(S) URL. Otherwise dispatch deliberately omits the webhook so an invalid endpoint cannot reject every run.

## Execution, inbox, and reports

MDA runs graph steps from durable checkpoints. Before model calls its loop applies catalog `LoopRules`: it can stop, incorporate task context, refresh the sandbox GitHub proxy, choose route/model, and assemble prompts and tools. The loop also restores run guidance or task context that summarization has made invisible. The runtime, not the backend, owns the active graph execution; the backend answers named runtime API calls and holds application state.

The inbox is a **read-then-receipt** mechanism, not destructive dequeue. On each model call, `ThreadInbox` concurrently reads queued follow-ups, task messages, and event matches according to the run's `InboxSpec`. It preserves oldest-first follow-up ordering and turns queued material into structured messages. Items remain owed until the runtime reports that the model call received them; a run that stops first leaves them for a later run. A dashboard follow-up can move the reply surface from Slack to web, and the resulting state update is checkpointed with the reading run.

The runtime outbox makes reporting effectively at-least-once while making backend effects idempotent. Replies, tool starts/results, inbox receipts, and notifications are added in order to `open_swe_outbox`, checkpointed with the producing graph step, and batched to the backend. An item is removed only when the backend acknowledges it by key. Failed delivery retries with backoff; after a prolonged outage the checkpoint retains pending items for a restarted or later run. Stable per-run/per-tool keys let the backend record a retried report once. An unrecoverably refused item is eventually dropped after three refusals so it cannot block later reports.

## Completion and continuation

The platform posts terminal run payloads to `/webhooks/run-complete`. The endpoint is fail-closed: without `RUN_COMPLETE_WEBHOOK_SECRET`, no token is accepted. Completion refreshes indexed status (with a reconciler as fallback), finalizes invocation usage telemetry when identity is present, settles the transcript turn as completed, failed, or interrupted, and notifies review/task machinery. The same turn settlement may have been emitted by middleware; its command identity makes the later duplicate harmless.

An `interrupted` run is terminal for status and transcript purposes but is **not** a user-visible failure: it is the expected result of a replacement `interrupt` turn. `error` and `timeout` can produce a short Slack, Linear, or GitHub failure reply. Replies are deduplicated per run using both persisted recent run IDs and an event claim with a two-day TTL; if a post fails, the claim is released for a retry. Linear channel-owned sessions report their failures themselves, so the backend suppresses its duplicate reply. Event-woken failures are additionally capped after three consecutive replies to avoid a noisy failure loop.

After a successful non-pickup run, completion checks for queued follow-ups that arrived after its last model-call inbox read. If no pending run already exists, it starts an empty-input `follow_up_pickup` run with `reject` strategy; its first inbox read takes the leftovers. The pickup marker prevents recursively starting another pickup for the same unchanged queue. Task messages and event subscriptions are delivered after success, error, or timeout with `enqueue` semantics, ready for a later run.

## Operational invariants and change guidance

- Keep all new triggers on `dispatch_agent_run` or `create_durable_run`; bypassing them loses canonical invocation metadata, durable defaults, stream compatibility, and completion hookup.
- Use `interrupt` only when the new request should supersede active interactive work. Use `enqueue` for work that must not preempt it. Do not treat an interrupted completion as an error.
- Preserve input envelopes and introductions. They carry source, sender, channel and structured event data that model policy and transcripts rely on.
- Channel-owned threads require MDA trusted ingress; do not create them as ordinary backend-owned MDA threads or allow generic writes.
- Treat reports and completion as retryable and potentially duplicated. New externally visible completion effects need a stable run/item idempotency key or an equivalent claim.
- Configure both `RUN_COMPLETE_WEBHOOK_SECRET` and an externally reachable HTTPS `COMPLETION_WEBHOOK_URL` to enable completion callbacks. Monitor completion failures and thread-status reconciliation; without a callback, agent output may still exist in the transcript, but failure notices and prompt status settlement are degraded.

## Focused verification

The most relevant tests cover dispatch defaults and invocation-ID compatibility in `tests/agent/test_dispatch.py`; completion status, failure reply and idempotence paths in `tests/webhooks/test_completion_webhook.py`; event/task completion behavior in `tests/test_task_events.py`; and stalled-run completion handling in `tests/threads/test_stalls.py`. When modifying a channel boundary, also test replay/deduplication and fallback ownership, not only the happy-path run creation.

See [background automation](background-automation.md) for event- and schedule-initiated work, and [review and delivery](review-and-delivery.md) for reviewer-specific completion and delivery behavior.
