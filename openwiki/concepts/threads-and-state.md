---
type: state-management concept
title: Threads, Invocations, and Durable State
description: How Open SWE identifies conversations across product surfaces, separates checkpointed LangGraph state from metadata, Store records, and PostgreSQL rows, and creates resumable runs without losing sandbox continuity.
tags: [threads, state, langgraph, durability, checkpoints, sandbox, slack, integrations]
sources:
  - id: openwiki-source-068d65a84c760eb8d555055e
    resource: repo://agent/completion.py
  - id: openwiki-source-dbb5064052b2047b2c3d504a
    resource: repo://agent/database/migrations/versions/0011_pull_requests.py
  - id: openwiki-source-0dc2eaa9f468f4d742bc32b4
    resource: repo://agent/database/postgres.py
  - id: openwiki-source-c48b309c5ca416cf623f0866
    resource: repo://agent/dispatch.py
  - id: openwiki-source-ba064e884edcde6097165df2
    resource: repo://agent/github/webhook.py
  - id: openwiki-source-cb4e403499865fd6b797127c
    resource: repo://agent/input_messages.py
  - id: openwiki-source-2d78b3dc0a340eaacb9e53e2
    resource: repo://agent/linear/webhook.py
  - id: openwiki-source-b6e514b5a92c6b11a90aac55
    resource: repo://agent/local_checkpointer.py
  - id: openwiki-source-f2ef7b73c8002cd7b756ad30
    resource: repo://agent/review/findings.py
  - id: openwiki-source-24b1722c4aacbce0b06350ae
    resource: repo://agent/run_config.py
  - id: openwiki-source-6fd11c8bb15f5eb94b765440
    resource: repo://agent/sandboxes/lifecycle.py
  - id: openwiki-source-856ade03ef31ac38e1347f7c
    resource: repo://agent/server.py
  - id: openwiki-source-41a696e92db10ba3dc9c66b0
    resource: repo://agent/slack/client.py
  - id: openwiki-source-92871ba83020d97558f679b2
    resource: repo://agent/slack/code_channels.py
  - id: openwiki-source-e747dfa76de43823582b8bab
    resource: repo://agent/slack/tools/manage_code_channel.py
  - id: openwiki-source-4ffd3d31ffb2d798faaaad59
    resource: repo://agent/slack/webhook.py
  - id: openwiki-source-db8a5812295508f44c54b439
    resource: repo://agent/source_context.py
  - id: openwiki-source-e7e51eafe569197d9f0f4de2
    resource: repo://agent/store.py
  - id: openwiki-source-2df3763659a7f9d1944f28e7
    resource: repo://agent/thread_ids.py
  - id: openwiki-source-5a076918234c4d1ae2ea9cc3
    resource: repo://agent/threads/access.py
  - id: openwiki-source-7e34667f01351599d23e4443
    resource: repo://agent/threads/summary.py
  - id: openwiki-source-79be4c606a697afbf6efb749
    resource: repo://agent/utils/thread_ops.py
  - id: openwiki-source-7c60191e42b8e30b62935af1
    resource: repo://agent/utils/thread_participants.py
  - id: openwiki-source-bd05fb2fcc2066f4d449df18
    resource: repo://agent/utils/thread_settings.py
  - id: openwiki-source-25a50e8385de61204afe1bcf
    resource: repo://agent/webhooks/common.py
  - id: openwiki-source-5bbba7b2a8ea8360ff233d63
    resource: repo://langgraph.json
verified:
  - by: openwiki/0.4.2
    at: 2026-09-19T08:13:05.087Z
generated: { by: "openwiki/0.4.2", at: "2026-09-19T08:13:05.087Z" }
---

# Threads, Invocations, and Durable State

A LangGraph thread is Open SWE's unit of conversational continuity. Its stable `thread_id` selects checkpointed graph state and history; a run is one execution against that thread. Thread metadata is a small, queryable cross-surface index; the LangGraph Store is separately namespaced application storage; PostgreSQL holds relational product records. These owners are complementary, not interchangeable.

## Identity is a persistence contract

`agent/thread_ids.py` is the single home for deterministic thread-id derivation. The exact namespace and input string are durable routing contracts: webhooks, the dashboard, and reviewers independently derive an ID from external identifiers. Changing a formula makes live state unreachable through normal entrypoints.

| Conversation or purpose | Derivation key |
| --- | --- |
| Slack location | `slack:{channel}:{timestamp}:{nonce}` |
| PR comment on a non-Open-SWE branch | `{owner}/{repo}/pr/{pr_number}` |
| Reviewer for a PR | `{owner}/{repo}/pr/{pr_number}/reviewer` |
| Per-user review chat | `{owner}/{repo}/pr/{pr_number}/chat/{login.lower()}` |
| Repository review style | `{owner}/{repo}/review-style` |
| Linear issue | `linear-issue:{issue_id}` |
| GitHub issue | `github-issue:{issue_id}` |
| Baby-sit lock | `open-swe:baby-sit-lock:{key}` |

Slack, PR, reviewer, review-chat, review-style, and baby-sit IDs are URL-namespace UUIDv5 values. Linear and GitHub issue IDs use a SHA-256-derived UUID. Reviewer and PR-agent keys intentionally differ, preventing those conversations from colliding. For an Open SWE branch, the GitHub PR-comment webhook first recovers the UUID embedded in its branch name, falling back to the PR key only when no UUID is present. Linear delivery uses the issue ID, so redeliveries converge on one thread.

```mermaid
flowchart TD
  Slack["Slack location"] --> SlackID["Resolve Slack thread id"]
  Linear["Linear issue id"] --> LinearID["Derive issue thread id"]
  PR["GitHub PR comment"] --> Branch["Extract branch UUID when present"]
  Branch --> AgentThread["Agent thread"]
  SlackID --> AgentThread
  LinearID --> AgentThread
  PR --> Reviewer["Derive reviewer thread id"]
  AgentThread --> Run["Create durable run"]
  Reviewer --> Run
```
Thread identity lets separately triggered work converge on a durable conversation.

### Slack mappings support moves and retirement

A Slack location has a Store-backed explicit mapping in namespace `("slack_thread_map", channel)`, keyed by its timestamp. `resolve_slack_thread_id` checks it first; otherwise it searches thread metadata for an exact `source_context` location. Multiple matches are an error. One matching thread, or the deterministic Slack fallback, is then bound and read back. Binding validates location syntax and refuses to overwrite a different thread, enforcing one active Open SWE thread per Slack location.

Detaching a location writes a fresh nonce rather than merely erasing the map. A later deterministic fallback therefore differs from the retired thread. This supports moves such as code-channel creation without accidental reuse.

A Slack code channel is one channel-wide agent session, identified by `CODE_CHANNEL_SESSION_TS = "0"`, not a reply thread. `manage_code_channel` binds the agent thread to `(channel_id, "0")`, updates `source_context`, and detaches the previous location. The sentinel switches context retrieval from `conversations.replies` for an ordinary thread to channel-wide `conversations.history`. Its Slack session statuses—`processing`, `active`, `suspended`, and `closed`—are Slack UI lifecycle state, not LangGraph thread status.

## State owners and durable metadata

Use each persistence surface for what it owns:

| Owner | Purpose and examples | Do not use it as |
| --- | --- | --- |
| LangGraph thread and checkpointer | Conversation state, message history, thread status, and checkpoint lineage | A relational product database |
| Thread metadata | Small durable index: source, title, participants, visibility, repository/workspace hints, `sandbox_id`, and settings snapshot | An arbitrary large-record store |
| LangGraph Store | Namespaced application records and short coordination data, including Slack mappings and dashboard queues | Thread checkpoint state or metadata |
| PostgreSQL | Transactional relational records: users and provider identities, repositories, pull requests, PR-to-thread links, reviews, and workspace bindings | A replacement for LangGraph conversation history |

`source_context` in thread metadata describes the original Slack, Linear, GitHub, or PR source. Its model preserves unknown supplied fields, returns an empty context for malformed historical data, and dumps only supplied fields. Metadata upsert logic preserves an existing opening context and title rather than allowing later activity to repoint a conversation. Participant logins and emails are key-per-person objects such as `{"octocat": true}` rather than lists, because metadata JSONB containment can match an object entry.

Reviewer threads carry `kind = "reviewer"`; metadata searches and completion handling use it to distinguish reviewer state from normal agent Slack work. Dashboard visibility and ownership are also metadata-enforced: private threads are readable by their immutable owner or an administrator, while posting to private threads remains owner-only.

The Store wrapper is the sanctioned access path. Missing items read as `None`; any other Store failure raises, so an outage cannot masquerade as absent data. `TypedStore` validates records on read: a direct unreadable `get` raises, while list operations log and skip malformed entries so one old record does not take down a listing. The Store's namespace search is prefix-based; callers that must distinguish direct entries from descendants use `search_all_entries` and its reported actual namespace.

PostgreSQL is configured by `POSTGRES_URI`, uses the `open_swe` schema, and applies migrations under a PostgreSQL advisory transaction lock. It contains durable relations that need database constraints: notably a PR has at most one `primary` thread link, while it may have any number of secondary links. This relation complements—rather than derives from—thread metadata and avoids ambiguous metadata searches when navigating from a PR to its authoritative thread.

## Settings, input, and invocation metadata

Thread-level model and repository settings are resolved once and snapshotted under metadata key `agent_settings`. Model, effort, subagent settings, model-routing flag, and repository instructions belong there; sender identity, personal instructions, and PR preferences remain per-message. A later profile edit does not affect an established thread unless an explicit per-run model override rewrites the snapshot. Settings are strictly normalized, cached for five minutes, and read/write failures fail soft so metadata trouble does not stop a run.

Each run contributes a new input; the graph retains thread state. `build_run_input` serializes the authored request in an escaped `<input-message>` envelope with validated sender, surface, kind, optional channel, and structured data. It can prepend hashed person, channel, and system `<dynamic-context>` introductions. Existing hashes suppress duplicate introductions; contexts hidden behind the summarization cutoff are eligible for reintroduction because the model no longer sees them.

`configurable` is per-run transport metadata, not durable thread state. `RunConfig` tolerates unknown keys, emits only supplied values, and drops only invalid fields during parsing. This lets independent webhook, dashboard, and graph hops add data without erasing fields they do not own. Dispatch also assigns one invocation ID and start time to both `configurable` and run metadata, providing a cross-hop correlation identity distinct from the durable thread and platform run IDs.

## Durable dispatch and checkpoints

`dispatch_agent_run` is the common trigger boundary for agent and reviewer runs. It either accepts a prebuilt input or builds one from content and source identities—never both—and selects the graph with `assistant_id`. It delegates to `create_durable_run`, which merges metadata, adds the invocation correlation values, and creates the LangGraph run.

```mermaid
sequenceDiagram
  participant Trigger as Product trigger
  participant Dispatch as dispatch_agent_run
  participant Input as Input builder
  participant LG as LangGraph
  participant Thread as Durable thread

  Trigger->>Dispatch: thread id and request
  Dispatch->>Input: build input when needed
  Dispatch->>Dispatch: prepare config and metadata
  Dispatch->>LG: create durable run
  LG->>Thread: append input and checkpoint execution
  LG-->>Trigger: run identity
```
A trigger creates a run on the selected durable thread rather than creating a replacement conversation.

The default `multitask_strategy="interrupt"` stops an active run with its synchronous checkpoint preserved, then starts the follow-up against history plus the new input. Background work can request `enqueue`. `durability="sync"` checkpoints before each step, enabling recovery from the latest checkpoint after a crash or recycle. Webhook triggers consequently need no in-process busy lock.

Runs are configured for resumable v3 event streaming: `stream_resumable=True`, the `__event_streaming_v2` compatibility marker, v3 stream modes (`values`, `updates`, `messages`, `custom`, `tasks`, and `checkpoints`), and subgraph streaming. This lets a dashboard attach after a Slack, Linear, or GitHub launch and replay events instead of appearing idle until the next event.

A deliberate Store FIFO remains for dashboard injection into a busy run and Slack message edits. It deduplicates a supplied `queue_id`, caps `pending_messages` at 100, and drops oldest messages beyond that cap. Completion callbacks are optional: dispatch adds one only when `RUN_COMPLETE_WEBHOOK_SECRET` is set and `COMPLETION_WEBHOOK_URL` is absolute, HTTP(S), and non-loopback; invalid configuration warns and omits the webhook instead of making every `runs.create` fail. Platform checkpointer TTL is deletion-based: dormant checkpoints expire after 43,200 minutes and are swept hourly.

For the desktop app, `agent/local_checkpointer.py` supplies an SQLite `AsyncSqliteSaver` so every checkpoint is committed rather than relying on the development server's periodic in-memory pickle. On first use it safely imports legacy pickle checkpoints once: the completion marker is written only after successful upsert-based copying, so an interrupted import retries on the next startup.

## Sandbox association and recovery boundary

A thread's metadata `sandbox_id` links durable conversation state to its working tree. `ensure_sandbox_for_thread` reuses a cached backend or reconnects to that ID, creating only if no sandbox exists. A deleted sandbox is replaced, because its stale ID would otherwise brick future runs. An existing but unreachable agent sandbox raises `SandboxUnreachableError` rather than being silently replaced: replacement could discard uncommitted work. `allow_replacement` permits that risk only for re-derivable read-only reviewer checkouts.

Creation ordering is intentional: Open SWE creates and initializes the sandbox, then writes `sandbox_id` to thread metadata, and finally publishes the backend to the thread-keyed proxy cache. A failure before binding leaves no half-built ID for a future run to adopt; a failure before publication cannot expose a partially initialized backend. Explicit recreation likewise prepares a distinct replacement before rebinding metadata.

## Change and test checklist

- Treat new ID derivations and Slack mapping changes as migrations of a persisted routing contract; test mapping conflicts, metadata fallback, duplicate matches, and retirement nonce behavior.
- Test `create_durable_run` defaults, interruption versus enqueue, v3 resumable stream settings, and invalid completion-webhook degradation.
- Test Store failure semantics separately from missing records, and verify relational PostgreSQL constraints rather than recreating PR-to-thread authority in metadata.
- Test input escaping, identity validation, dynamic-context de-duplication, and reintroduction after summarization.
- Test sandbox create/bind/publish ordering and the distinct unreachable-versus-gone paths. See [Sandbox Lifecycle](../architecture/sandbox-lifecycle.md).

For surrounding flows, see [Invocation](../workflows/invocation.md), [Follow-up Messages](../workflows/follow-up-messages.md), and [Models, Profiles, and Instructions](./models-profiles-instructions.md).
