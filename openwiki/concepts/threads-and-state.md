---
type: state-management concept
title: Threads, Durable Runs, and State
description: How Open SWE identifies durable LangGraph conversations, constructs follow-up inputs, owns thread metadata and Store records, and preserves sandbox continuity across product surfaces.
tags: [threads, state, langgraph, durability, checkpoints, sandbox, slack, integrations]
sources:
  - id: openwiki-source-5bbba7b2a8ea8360ff233d63
    resource: repo://langgraph.json
  - id: openwiki-source-70b814b26d317c2b15c4a4fb
    resource: repo://openswe/chat.py
  - id: openwiki-source-913527bc7b548b4bf81f6a35
    resource: repo://openswe/completion.py
  - id: openwiki-source-1685d34aae8025be9332f45a
    resource: repo://openswe/dispatch.py
  - id: openwiki-source-836966ba5e0c4d710801c9a9
    resource: repo://openswe/input_messages.py
  - id: openwiki-source-41b3f3ac3c45b0e2ff376c3d
    resource: repo://openswe/invocation.py
  - id: openwiki-source-85f325a37c97d6000b6e6a23
    resource: repo://openswe/review/findings.py
  - id: openwiki-source-e9b2ac0cf383e184a317d349
    resource: repo://openswe/run_config.py
  - id: openwiki-source-1b32e9f41fa7e64702b380f6
    resource: repo://openswe/sandboxes/lifecycle.py
  - id: openwiki-source-49cd80b1b712410f02d313d6
    resource: repo://openswe/slack/client.py
  - id: openwiki-source-7f665eda03d736dd3db8c67c
    resource: repo://openswe/slack/code_channels.py
  - id: openwiki-source-5cbe93451d362668e53e314b
    resource: repo://openswe/slack/tools/manage_code_channel.py
  - id: openwiki-source-72370931d61f0a7232adcf12
    resource: repo://openswe/slack/webhook.py
  - id: openwiki-source-76820c5856f1479d850c1ab9
    resource: repo://openswe/source_context.py
  - id: openwiki-source-c5e061972b62c56ca429a531
    resource: repo://openswe/store.py
  - id: openwiki-source-53ea9aa9c1bc2a186e16ba04
    resource: repo://openswe/thread_ids.py
  - id: openwiki-source-5e89303bbff1b32492ae2b1c
    resource: repo://openswe/threads/creation.py
  - id: openwiki-source-996097a4d0a674613168d766
    resource: repo://openswe/utils/thread_ops.py
  - id: openwiki-source-183593e64b0a8affdce9abac
    resource: repo://openswe/utils/thread_participants.py
  - id: openwiki-source-1962e84a7cbf37fcca83381c
    resource: repo://openswe/utils/thread_settings.py
generated: { by: "openwiki/0.4.2", at: "2026-10-08T15:19:10.971Z" }
verified:
  - by: openwiki/0.4.2
    at: 2026-10-08T15:19:10.971Z
---

# Threads, Durable Runs, and State

A LangGraph thread is Open SWE's unit of continuity. A stable `thread_id` selects a conversation's checkpointed graph state and message history; thread metadata holds durable, queryable facts about that conversation; and the LangGraph Store holds separately namespaced application records. A run is an execution on that thread, not a replacement for it.

The boundary matters when changing an integration: a webhook, dashboard action, reviewer, or background task must route a follow-up to the right existing thread and supply a new input. It must not manufacture a random identity, overwrite another surface's source context, or replace a sandbox merely because a reconnect failed.

## Thread creation enforces a title

Threads are created through `openswe.threads.creation.create_thread`, which is the only place LangGraph threads are created. Every thread people can open must have a title; the module enforces this invariant by requiring a non-empty, stripped title string. Titles are stored in thread metadata and remain unchanging from the opening activity unless explicitly updated. System-named threads use `ensure_titled_thread` to create or keep titles current.

## Identity is a persistence contract

`openswe/thread_ids.py` is the single home for deterministic thread-id derivation. Its exact keys and namespaces are persisted routing contracts: separate processes re-derive IDs from external identifiers, so changing a formula makes existing threads unreachable through their normal entrypoints.

| Conversation or purpose | Derivation | Stable key |
| --- | --- | --- |
| Slack location | `slack_thread_id` | `slack:{channel}:{timestamp}:{nonce}` |
| PR comment on a non-Open-SWE branch | `pr_comment_thread_id` | `{owner}/{repo}/pr/{pr_number}` |
| Reviewer for a PR | `reviewer_thread_id` | `{owner}/{repo}/pr/{pr_number}/reviewer` |
| Repository review style | `review_style_thread_id` | `{owner}/{repo}/review-style` |
| Linear issue | `linear_issue_thread_id` | `linear-issue:{issue_id}` |
| GitHub issue | `github_issue_thread_id` | `github-issue:{issue_id}` |
| Baby-sit lock | `baby_sit_lock_thread_id` | `open-swe:baby-sit-lock:{key}` |

IDs are derived either as URL-namespace UUIDv5 values (Slack, PR-comment, reviewer, review-style, baby-sit lock) via `uuid.uuid5(NAMESPACE_URL, key)`, or as a SHA-256-derived UUID (Linear and GitHub issues) via `_sha256_uuid`. The reviewer key deliberately differs from the agent PR-comment key, so a PR's autonomous reviewer and its agent conversation cannot collide.

For a PR that Open SWE created, the GitHub PR-comment handler first extracts a UUID embedded in the branch with `thread_id_from_branch`; only a branch without one uses `pr_comment_thread_id`. Linear delivery uses `linear_issue_thread_id(issue_id)`, so redelivery stays on the issue's thread.

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
Thread identity lets independently triggered work converge on the correct durable conversation.

### Slack mappings support moves and retirement

Slack has a Store-backed mapping in a per-channel namespace, keyed by the Slack thread timestamp. `resolve_slack_thread_id` first checks an explicit stored mapping, then searches thread metadata for matching `source_context`, rejects multiple matches, and falls back to deriving a deterministic fallback ID or using an existing nonce. Binding validates the Slack location, refuses a conflicting existing mapping by raising, then verifies the write persisted; thus one Slack location cannot be assigned to two Open SWE threads.

Deleting associations does not simply erase the map: `delete_slack_thread_associations` writes a fresh nonce at that location. With no mapped ID, a future resolution derives a different fallback ID, avoiding collision with the retired conversation. This is used by moves such as creating a code channel.

A code channel is one session for the whole channel, keyed by `CODE_CHANNEL_SESSION_TS = "0"`, not a Slack reply thread. `manage_code_channel` binds the existing agent thread to `(channel_id, "0")` and updates its `source_context`. The sentinel selects `conversations.history` (fetching the whole channel with no thread-timestamp parameter); ordinary Slack conversations use `conversations.replies` keyed by thread timestamp. Slack's `processing`, `active`, `suspended`, and `closed` session statuses are Slack UI lifecycle state, distinct from LangGraph thread status.

## State ownership and metadata

Thread metadata is the durable cross-surface index and small per-thread state. `source_context` identifies the originating Slack location, Linear issue, GitHub issue, or PR; its Pydantic model permits unknown fields and preserves only supplied fields on output. It parses malformed historical metadata as an empty context rather than breaking a run. Upsert logic keeps the opening context rather than letting later activity repoint a thread, preserves the first title, and stores participant identities as metadata.

Participant logins and emails use key-per-person maps such as `{"octocat": true}`, rather than lists. That shape permits a JSONB containment query for one participant. Reviewer threads additionally carry `kind = "reviewer"`, plus reviewer-specific metadata such as PR state, head SHA, watch flag, and findings. The kind is both a UI/query discriminator and a completion-handling boundary: normal agent Slack completion work is skipped for reviewer threads.

Thread-level settings are a separate metadata snapshot under `agent_settings`. On the first run, model, effort, subagent model/effort, and repository instructions are chosen for the thread; sender identity, personal instructions, and PR preferences remain per-message. Later profile edits do not change the snapshot unless a caller explicitly stores a replacement, such as a model override. Reads cache for five minutes and reads/writes fail soft, so settings storage cannot prevent a run. Strict normalization drops invalid or obsolete settings rather than retaining arbitrary profile data.

The LangGraph Store is not thread metadata. `openswe/store.py` is the sanctioned wrapper for namespaced key/value access: a missing item returns `None`, whereas other HTTP failures propagate. This makes an outage observably different from an empty record. `TypedStore` validates records through a Pydantic model; `get` fails for an unreadable requested record, while listings log and skip malformed records so one old record does not take down a listing.

## Follow-up input and run configuration

Each run supplies new messages; the graph retains its thread state. `build_run_input` serializes the authored request into an `<input-message>` envelope with a namespaced sender, surface, kind, optional channel, and structured data. It may precede that request with person, channel, and system `<dynamic-context>` introductions. Entity IDs are validated and text is escaped; Slack channel topic and purpose are marked untrusted.

Dynamic context blocks are content-hashed. `build_input_messages` excludes introductions already recorded in the injected-hash set. When summarization has moved early messages behind its cutoff, `visible_dynamic_context_hashes` treats those hidden blocks as no longer visible, allowing necessary identity context to be introduced again. This prevents deduplication state from making a summarized conversation lose information the model can no longer see.

`configurable` is the per-run transport contract, not durable thread state. `RunConfig` accepts unknown keys and dumps only fields that were supplied, so independent writers can enrich it without erasing one another's keys. Parsing is deliberately tolerant: invalid fields are dropped iteratively while valid fields, including a thread ID, survive. Its values are optional because each graph and trigger needs a different subset. The dictionary typically carries `thread_id`, `repo` (owner and name), `workspace`, `source` (webhook trigger name), `github_login`, `slack_thread` (for Slack-specific context), and per-run model/effort overrides that shadow thread-level settings.

## Durable dispatch and checkpoints

All product triggers use `dispatch_agent_run`, which delegates to `create_durable_run`; it can select the `agent` or `reviewer` graph and accepts either a prebuilt input or source identities, never both. The dispatch helper adds a unique `invocation_id` into both `configurable` and run metadata, merges supplied metadata, and enables the event-streaming marker.

```mermaid
sequenceDiagram
  participant Trigger as Product trigger
  participant Dispatch as dispatch_agent_run
  participant Input as Input builder
  participant LG as LangGraph
  participant Thread as Existing thread

  Trigger->>Dispatch: thread id and request
  Dispatch->>Input: build input when not prebuilt
  Dispatch->>Dispatch: prepare config and metadata
  Dispatch->>LG: runs.create with durable defaults
  LG->>Thread: append input and checkpoint execution
  LG-->>Trigger: run identity
```
A trigger creates a run on the selected durable thread using a normalized input and configuration.

The standard defaults are `multitask_strategy="interrupt"`, `durability="sync"`, `if_not_exists="create"`, resumable streaming, the Protocol v3 stream modes, and subgraph streaming. Interrupt stops an active run while preserving its sync checkpoint, then runs with history plus the follow-up; background work such as baby-sit can choose `enqueue`. Webhook triggers therefore do not need an in-process busy lock. A Store FIFO remains for deliberate dashboard injection and Slack message edits; it caps `pending_messages` at `MAX_QUEUED_MESSAGES` (100), dropping oldest entries.

A completion webhook is attached only if `RUN_COMPLETE_WEBHOOK_SECRET` is set and `COMPLETION_WEBHOOK_URL` is an absolute non-loopback HTTP(S) URL. Otherwise dispatch logs a warning and creates the run without the webhook, rather than allowing a platform-rejected URL to fail every run. The checkpointer has deletion TTL configured as 43,200 minutes with a 60-minute sweep interval: checkpointed state of inactive threads eventually expires.

### Invocation identity for deduplication and retry safety

Each run carries an `invocation_id` (aliased as `prepare_run_id` for backwards compatibility) in both metadata and configurable. This UUID is a deduplication key for follow-up messages and user retries: the same invocation_id on a follow-up to an existing thread means "reuse the same run result," while a new invocation_id always creates a fresh run. The ID is assigned once during dispatch and checked across metadata, configurable, and historical values to reject conflicts. A trigger should generate one invocation_id per user action, then reuse it on retries; this prevents duplicate work and user-facing duplicates when a webhook redelivers or a retry button is clicked.

## Sandbox association and recovery boundary

A thread's `sandbox_id` in metadata connects durable conversation state to its working tree. `ensure_sandbox_for_thread` first uses a cached backend, otherwise reconnects using that ID, and creates a sandbox only when neither exists. It writes the ID only after creation and initialization have succeeded, then publishes the backend to the per-thread proxy cache. This ordering prevents the next run from adopting a half-built sandbox.

Do not interpret reconnect failure as permission to replace an agent sandbox. An unreachable existing sandbox raises `SandboxUnreachableError`: replacement could discard uncommitted work. A deleted sandbox (`SandboxGoneError`) is replaced because its stored ID otherwise bricks future runs. `allow_replacement` additionally permits replacement for an unreachable read-only reviewer sandbox whose checkout can be rebuilt. Explicit reset and recreate operations also bind a new ID only after the replacement has been prepared.

The proxy is a stable, thread-keyed backend handle. It serializes lazy reconnect startup and lets a middleware-held reference observe a newly connected backend, rather than handing each layer a stale backend object.

## Operational checks

When changing these mechanisms, test the invariants rather than only a caller:

- Verify every new entrypoint chooses an existing deterministic ID or explicitly creates a new identity boundary.
- Exercise Slack mapping conflict, metadata fallback, duplicate metadata match, retirement nonce, and code-channel sentinel paths.
- Verify dispatch arguments, invalid completion-webhook degradation, interrupt versus enqueue behavior, and resumable Protocol v3 configuration.
- Verify input envelope escaping, identity validation, dynamic-context de-duplication, and reintroduction after summarization.
- Verify sandbox create/publish ordering and the distinction between unreachable and gone sandboxes. See [Sandbox Lifecycle](../architecture/sandbox-lifecycle.md).

For surrounding flows, see [Invocation](../workflows/invocation.md), [Follow-up Messages](../workflows/follow-up-messages.md), and [Models, Profiles, and Instructions](./models-profiles-instructions.md).
