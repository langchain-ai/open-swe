---
type: architecture-component
title: Middleware Stack and Interceptors
description: Ordering-sensitive middleware around the coding agent and reviewer model and tool loops. Catalogs all middleware, their ordering, responsibilities, and how they shape agent behavior across models, tools, and state.
tags: [middleware, agent, reviewer, model-call, tool-call, fallback, guardrails, PrepareRunState]
verified:
  - by: openwiki/0.4.2
    at: 2026-10-08T15:19:10.971Z
sources:
  - id: openwiki-source-41baa38c611462fcbcd00a78
    resource: repo://openswe/middleware/deliver_event_matches.py
  - id: openwiki-source-75a672d9a8b6d6c500b1cf8d
    resource: repo://openswe/middleware/dynamic_tools.py
  - id: openwiki-source-90777797f3501969c0c62e93
    resource: repo://openswe/middleware/image_model_fallback.py
  - id: openwiki-source-f14e3b9027356fb68a4a9984
    resource: repo://openswe/middleware/model_errors.py
  - id: openwiki-source-f381ed570a6ee0e4c116f90e
    resource: repo://openswe/middleware/model_fallback.py
  - id: openwiki-source-6f3c650ebb229f208fe2ebcb
    resource: repo://openswe/middleware/notify_step_limit.py
  - id: openwiki-source-052a9a68c52dca5bb8277219
    resource: repo://openswe/middleware/prepare_run.py
  - id: openwiki-source-d8c0cb930a442c145a7b2e2a
    resource: repo://openswe/middleware/record_run_usage.py
  - id: openwiki-source-1087d65aaa83434d4f7c209b
    resource: repo://openswe/middleware/refresh_github_proxy.py
  - id: openwiki-source-86fa20d37b342b80b5e56a97
    resource: repo://openswe/middleware/repair_orphaned_tool_calls.py
  - id: openwiki-source-7f78050909c084a5110d4c49
    resource: repo://openswe/middleware/settle_review_check.py
  - id: openwiki-source-bd1d4cc6fca6c87a8b1fe988
    resource: repo://openswe/middleware/task_retry.py
  - id: openwiki-source-54936c5fc8d4f07851a05349
    resource: repo://openswe/middleware/tool_error_handler.py
  - id: openwiki-source-d618115330c9c5a6ad6a6eec
    resource: repo://openswe/middleware/workflow_push_guard.py
  - id: openwiki-source-96bcad07b4fe7078402bc2b8
    resource: repo://openswe/reviewer.py
  - id: openwiki-source-919e16feae379651f2cbc1c9
    resource: repo://openswe/server.py
  - id: openwiki-source-10026b2dd7b7368bb04e27f0
    resource: repo://tests/sandbox/test_reviewer_sandbox_recovery.py
generated: { by: "openwiki/0.4.2", at: "2026-10-08T15:19:10.971Z" }
---

# Middleware Stack and Interceptors

`build_agent(config)` in `openswe/server.py` composes ordered middleware into `create_deep_agent` to form the Deep Agents graph. The middleware list is an onion: earlier entries wrap later entries, so an outer layer can intercept, modify, or handle exceptions from every inner layer. This makes order part of the runtime contract, not an implementation detail. Middleware is categorized by responsibility: model-selection, tool-related, run-setup, flow-control, and integration-specific. Each intercepts at distinct points in the model-tool-state loop and mutates the `PrepareRunState` or global state in a coordinated sequence.

See [Coding Agent Assembly](agent-graph.md), [Sandbox Lifecycle](sandbox-lifecycle.md), and [PR Creation](../workflows/pr-creation.md) for the graph, review, sandbox, and delivery contexts.

## Middleware categories and responsibilities

### Model-selection middleware

These middleware select, validate, or route model choices:

- **`ModelSelectionMiddleware`** (conditional, enabled if adaptive routing configured)  
  Allows the agent to route requests between a primary and alternate model based on configured routing rules and request characteristics. Only installed when `model_selection` is provided.

- **`ModelFallbackMiddleware`** (conditional, installed only when a different fallback model is available)  
  Wraps the model call in a retry loop that alternates between primary and fallback models on transient errors using exponential backoff with jitter. It retries on 5xx/429-class HTTP statuses, connection/timeout errors, and the `TimeoutError` emitted by `ModelCallTimeoutMiddleware`. An immediate `model-not-available` provider error triggers a user-facing `AIMessage`. When all attempts fail, it returns a terminal outage message by default; `surface_outage_message=False` re-raises the final error instead.

- **`ImageModelFallbackMiddleware`** (conditional, installed only when vision support mismatch detected)  
  Routes image-bearing requests to a vision-capable fallback model when the primary model does not support images. When no fallback is available, image content is omitted with a warning.

### Tool-related middleware

These middleware control, validate, or handle tool execution:

- **`DynamicToolMiddleware`** (conditional, installed if integration groups present)  
  Exposes configured integration groups (MCPs, Notion) lazily: tool names are visible to the model up front, but the actual tools are built only after the model requests them. Positioned after fallback to avoid loading integrations during retries.

- **`ExcludeToolsMiddleware`**  
  Filters disallowed tool names from model requests. Applied after dynamic tools are loaded.

- **`SubdirAgentsReadMiddleware`**  
  Contributes applicable ancestor `AGENTS.md` instructions once per thread, allowing configuration inheritance from parent directories.

- **`ToolErrorMiddleware`**  
  Converts unhandled tool exceptions into `ToolMessage(status="error")` JSON carrying error type, error text, and tool name, allowing the model to self-correct. Treats the sandbox as unreachable when `SandboxConnectionError` (except `SandboxServerReloadError`) or `ResourceNotFoundError` for the sandbox occurs: notifies the user once and re-raises to end the run rather than looping. `SandboxRetryableConnectionError` is converted to a `sandbox_transient` error message because the SDK guarantees the command never started.

- **`ToolRetryMiddleware`** (wraps only `task` tool)  
  Retries delegated task calls up to two times with initial delay 1 second, maximum delay 10 seconds. `task_retry_on` retries on 5xx/429 statuses, transient exception names, and notably `ModelCallTimeoutError` from subagents (which lack their own fallback middleware). On exhaustion, `task_on_failure` returns structured `failed` data for prompt/context errors and re-raises all others.

- **`ValidateImageReadsMiddleware`**  
  Validates image-read tool calls and is positioned after `WorkspaceSkillsMiddleware` to allow basic tool configuration before validation occurs.

### Run-setup middleware

These middleware prepare and inject contextual state:

- **`PrepareAgentRunMiddleware` (concrete: `PrepareReviewerRunMiddleware` for reviewer)**  
  Checkpointed per-run setup that fingerprints the latest message, middleware class, and preparation configuration. A matching `run_prepared_for` latch on `PrepareRunState` skips setup when resuming the same invocation; a new invocation on the same thread re-prepares. Preparation is idempotent because failures before the checkpoint can re-run it. Its model wrapper injects the rendered system prompt into every model call. Setup may refresh tokens, fetch new diffs, or resolve review context.

- **`ConversationOffloadingMiddleware`**  
  Outermost agent middleware after `FilesystemMiddleware`. Defers conversation context to external storage when configured by the user, permitting long runs without hitting provider context limits.

- **`TranscriptMiddleware`**  
  Processes conversation state and populates the transcript for debugging and auditing. Positioned immediately after `PrepareAgentRunMiddleware`.

- **`FilesystemMiddleware`**  
  Outermost middleware. Exposes file read/write/delete and directory navigation tools with configurable binary content offloading. Backend-specific for local vs. sandboxed runs.

### Flow-control and policy middleware

These middleware enforce constraints and drive completion:

- **`ModelCallLimitMiddleware`**  
  Enforces a per-run limit on model calls. When exhausted, it terminates the agent and injects a marker message. `notify_step_limit_reached` recognizes this marker and posts a Slack notification.

- **`PullRequestCreationGuardMiddleware`** (not installed for local runs)  
  Blocks `execute` and `background_execute` command forms that create pull requests outside `open_pull_request`, including GitHub CLI, API, curl, and nested bash constructs. Returns a tool error instead of executing.

- **`WorkflowPushGuardMiddleware`**  
  Blocks push changes to `.github/workflows` files without recorded human approval. Returns a blocked result with an approval URL instead of allowing the push. Prompts for approval only once per push path and accepts user consent via dashboard link.

- **`notify_step_limit_reached`** (after-agent hook)  
  Recognizes the `ModelCallLimitMiddleware` marker and posts an explanatory Slack message when the agent hits its step limit.

- **`record_run_usage`** (after-agent hook)  
  Persists summarized token usage when the run has a preparation-run ID and schedules deferred cost enrichment. Bookkeeping failures are caught and do not alter the agent result.

### Integration-specific middleware

- **`RequireUserReplyMiddleware`**  
  Ensures the user has provided explicit feedback via a configured reply tool before the agent continues, blocking premature self-iteration.

- **`RequireCliResultMiddleware`** (conditional, only if CLI thread)  
  Ensures CLI output is available before continuing, used for bridged CLI invocations.

- **`IncidentMiddleware`** (conditional, only if incident session active)  
  Adapts run policy (e.g., model call limits) when an incident is being actively tracked.

- **`WorkspaceSkillsMiddleware`** (conditional, non-local, org admins)  
  Exposes workspace-configured skills to the agent.

### Model-call boundary and error handling

- **`ModelErrorMiddleware`**  
  Logs and classifies model-call exceptions, recording the error type and classification code in thread metadata when context is available. Re-raises the original exception unchanged, preserving the error category for completion handling even where platform messages are scrubbed.

- **`ModelCallTimeoutMiddleware`**  
  Innermost middleware. Reads `OPEN_SWE_MODEL_CALL_TIMEOUT_SECONDS` (default 900 seconds) and uses `asyncio.wait_for` to convert a stalled provider call into `ModelCallTimeoutError` (a `TimeoutError`). Sits above provider-level timeouts, allowing the provider client to retry first. This deadline covers the provider operation itself, so a timeout exception escalates outward to the optional fallback wrapper.

- **`refresh_github_proxy_before_model`** (before-model hook)  
  Refreshes a near-expiry sandbox GitHub-proxy installation token that expires after one hour, preventing 401s on git/gh commands in long-running agents.

- **`check_message_queue_before_model`** (before-model hook)  
  Reads `("queue", thread_id)` from the LangGraph store, deletes `pending_messages` before constructing the message list to avoid duplicate delivery, and injects queued human input in FIFO order. Also consumes a pending autofix event.

- **`deliver_event_matches_before_model`** (before-model hook, except in stop-summary mode)  
  Appends webhook-driven events owed by the thread from the database in oldest-first order, preventing event delivery delays until run completion.

### Message preparation

- **`SanitizeFireworksMessagesMiddleware`, `SanitizeOpenAIResponsesMiddleware`, `SanitizeThinkingBlocksMiddleware`**  
  Prepare provider-specific message formats and clean unsupported content blocks before the provider call.

- **`StableToolResultOrderMiddleware`**  
  Ensures tool results are ordered stably and deterministically across message lists, allowing the model to rely on consistent result positions.

### Reviewer-specific middleware

- **`RepairOrphanedToolCallsMiddleware`**  
  Inserts synthetic error `ToolMessage` results for tool-call IDs that have no corresponding result before model calls. Prevents permanently wedged reviewer threads when runs are interrupted mid-tool-call.

- **`settle_review_check_on_exit`** (after-agent hook)  
  Closes an unpublished tracked reviewer check as **neutral** so an incomplete review is not presented as a PR code failure. If `publish_review` completed but its conclusion PATCH failed transiently, the hook retries the stored pending real conclusion.

## Coding-agent middleware stack

The coding-agent middleware chain is outer to inner. Each wraps the next, forming a defense-in-depth model for failure handling and request shaping:

| # | Middleware | Type | Condition |
|---|---|---|---|
| 1 | FilesystemMiddleware | tool-related | always |
| 2 | ConversationOffloadingMiddleware | run-setup | always |
| 3 | PrepareAgentRunMiddleware | run-setup | always |
| 4 | TranscriptMiddleware | run-setup | always |
| 5 | IncidentMiddleware | integration-specific | incident session present |
| 6 | WorkspaceSkillsMiddleware | tool-related | configured (non-local) |
| 7 | ValidateImageReadsMiddleware | tool-related | always |
| 8 | ModelCallLimitMiddleware | flow-control | always |
| 9 | ToolErrorMiddleware | tool-related | always |
| 10 | ExcludeToolsMiddleware | tool-related | always |
| 11 | SubdirAgentsReadMiddleware | tool-related | always |
| 12 | ToolRetryMiddleware (task) | tool-related | always |
| 13 | PullRequestCreationGuardMiddleware | flow-control | non-local runs |
| 14 | WorkflowPushGuardMiddleware | flow-control | always |
| 15 | refresh_github_proxy_before_model | model-call | always |
| 16 | check_message_queue_before_model | model-call | not stop-summary mode |
| 16b | deliver_event_matches_before_model | model-call | not stop-summary mode |
| 17 | RequireUserReplyMiddleware | integration-specific | always |
| 18 | RequireCliResultMiddleware | integration-specific | CLI thread |
| 19 | notify_step_limit_reached | flow-control | always (after-agent hook) |
| 20 | record_run_usage | flow-control | always (after-agent hook) |
| 21 | ModelSelectionMiddleware | model-selection | adaptive routing enabled |
| 22 | ModelFallbackMiddleware | model-selection | fallback model differs |
| 23 | ImageModelFallbackMiddleware | model-selection | vision support mismatch |
| 24 | DynamicToolMiddleware | tool-related | integration groups present |
| 25 | SanitizeFireworksMessagesMiddleware | message-prep | always |
| 26 | SanitizeOpenAIResponsesMiddleware | message-prep | always |
| 27 | SanitizeThinkingBlocksMiddleware | message-prep | always |
| 28 | StableToolResultOrderMiddleware | message-prep | always |
| 29 | ModelErrorMiddleware | error-handling | always |
| 30 | ModelCallTimeoutMiddleware | error-handling | always (innermost) |

## PrepareRunState and mutation flow

`BasePrepareRunMiddleware` defines `PrepareRunState`, which extends the agent's `AgentState` with checkpoint fields:

- **`run_prepared`**: Boolean latch set to `True` after successful preparation.
- **`run_prepared_for`**: SHA256 fingerprint of `{"middleware": class name, "message": latest message fingerprint, "config": subclass-provided config}`. When resuming the same invocation, a matching fingerprint skips preparation.

The preparation flow is:

1. Agent factory calls `_prepare(state, runtime)` in the before-agent hook.
2. `_prepare` fingerprints the latest message, class name, and configuration details.
3. If `run_prepared_for` matches the computed fingerprint, skip setup (resumed invocation).
4. Otherwise, execute setup, collect updates dict, and return `{"run_prepared": True, "run_prepared_for": fingerprint, **updates}`.
5. LangGraph checkpoints this state after the before-agent node.
6. `awrap_model_call` merges `rendered_system_prompt` into the system message before each model call.

Preparation must be idempotent: a failure before the checkpoint can cause re-execution. Setup typically refreshes authorization tokens, loads diffs for reviewers, or resolves model-specific parameters.

## Critical model-failure boundary

The innermost model layers form a defense-in-depth boundary:

```
┌─ Fallback retry wrapper (ModelFallbackMiddleware)
│  ├─ Message sanitizers & stable result order
│  ├─ Error classification (ModelErrorMiddleware)
│  └─ Model call deadline (ModelCallTimeoutMiddleware)
│     └─ Provider call
│        └─ Stalled call → TimeoutError → ModelErrorMiddleware
│           → records + re-raises → Fallback retry loop
└─ Exhausted retries → Terminal outage AIMessage
```

**Key ordering constraint**: `ModelCallTimeoutMiddleware` is innermost so its wall-clock deadline covers the provider operation itself. A timeout exception passes outward through `ModelErrorMiddleware` for classification and thread metadata recording, then reaches the optional `ModelFallbackMiddleware` retry wrapper. An immediate provider access error short-circuits to a user-facing message. This ensures a hang becomes either a retried request (with an alternate model) or a controlled, visible end to the run, never a silent parked invocation.

## Retry and failure boundaries

### Model-call retry flow

`ModelFallbackMiddleware` is installed only when `LLM_FALLBACK_MODEL_ID` or the primary model's default fallback differs from the primary. By default, it makes six attempts (schedule: `0, 5, 15, 30, 45` seconds backoff plus ±25% jitter), alternating primary and fallback models. Each attempt applies the backoff delay, then calls `handler(request)` with the active model.

It retries on:
- 5xx HTTP status codes
- 429 (rate limit), 408 (request timeout), 409 (conflict), 425 (too early), 529 (service unavailable)
- `anthropic.APIConnectionError`, `anthropic.RateLimitError`, `anthropic.InternalServerError`
- `openai.APIConnectionError`, `openai.RateLimitError`, `openai.InternalServerError`
- `httpx2.TransportError`
- `TimeoutError` (from `ModelCallTimeoutMiddleware`)

Special cases:
- **Provider access error** (`model-not-available`): immediately returns a user-facing `AIMessage` without retrying.
- **Exhausted budget**: returns an outage `AIMessage` by default; `surface_outage_message=False` re-raises the final error.

### Task-call retry flow

`ToolRetryMiddleware` wraps the delegated `task` tool with two retries, 1–10 second backoff. `task_retry_on` retries on 5xx/429 HTTP statuses and transient exception class names, including notably `ModelCallTimeoutError` emitted by subagents (which have no fallback middleware). On exhaustion, `task_on_failure` returns structured `failed` JSON only for `invalid_request_error` (prompt/context length) and re-raises all other exceptions, allowing the agent to see the error context.

### Sandbox-level retry flow

`retry_transient_sandbox_errors` is a direct-operation utility that retries only SDK-marked pre-start errors (`SandboxRetryableConnectionError`), at most four times, with bounded exponential backoff and jitter. Terminal sandbox errors are never retried. An unreachable sandbox is notified once (via Slack, Linear, or GitHub) and re-raised to end the run; continuing would repeatedly fail and spam the user.

**Sandbox recovery and determinism**:
- Reviewer sandbox setup **opts into** auto-replacement because its checkout is re-derived for each run and a persistent PR thread should not be bricked by a dead sandbox.
- Coding-agent sandbox setup **deliberately does not** auto-replace to avoid concealing loss of uncommitted work; users can retrigger the thread or start a new one manually.

## Reviewer middleware stack

The reviewer uses a deliberately leaner chain, omitting conversation offloading, transcripts, incident/workspace features, dynamic tools, tool exclusion, subdirectory instructions, task retry, PR/workflow guards, run-usage recording, model selection, and cross-provider fallback:

| # | Middleware |
|---|---|
| 1 | PrepareReviewerRunMiddleware |
| 2 | ModelCallLimitMiddleware |
| 3 | ToolErrorMiddleware |
| 4 | refresh_github_proxy_before_model |
| 5 | check_message_queue_before_model |
| 6 | SanitizeFireworksMessagesMiddleware |
| 7 | SanitizeOpenAIResponsesMiddleware |
| 8 | SanitizeThinkingBlocksMiddleware |
| 9 | RepairOrphanedToolCallsMiddleware |
| 10 | StableToolResultOrderMiddleware |
| 11 | ModelRetryMiddleware (langchain, retries on `TimeoutError`) |
| 12 | ModelErrorMiddleware |
| 13 | ModelCallTimeoutMiddleware |
| 14 | settle_review_check_on_exit (after-agent hook) |

The reviewer adds `RepairOrphanedToolCallsMiddleware` to prevent permanently wedged threads when a run is interrupted mid-tool-call. Before each model call, it inserts synthetic error `ToolMessage` results for tool-call IDs without matching results, allowing the model to continue instead of being rejected by the provider.

`settle_review_check_on_exit` is the completion guarantee: it closes a tracked but unpublished GitHub review check as **neutral** so an incomplete review is not falsely marked as a code failure. If `publish_review` completed but its PATCH failed transiently, the hook retries that stored real conclusion instead.

## Idempotence and edge cases

### Preparation idempotence

`BasePrepareRunMiddleware` must keep `_prepare` idempotent. The fingerprint check allows resumed invocations to skip setup, but the checkpoint is applied *after* the hook returns. A failure before the checkpoint can re-run `_prepare` on the next attempt. All setup operations must tolerate re-execution: token refreshes, database writes, and state mutations must be safe to repeat.

### Orphaned tool calls

When a run is interrupted after a tool call is submitted but before its result is returned, the `tool_calls` list and `tool_results` list become mismatched. Later model calls will fail with provider-specific errors. `RepairOrphanedToolCallsMiddleware` (reviewer only) and careful run-completion hooks detect and repair this by inserting synthetic error results for missing tool calls.

### Queue delivery guarantee

`check_message_queue_before_model` deletes `pending_messages` from the LangGraph store *before* injecting them into the message list. This delete-before-inject pattern ensures that resumed invocations do not duplicate messages, even if the hook runs again.

### Model not available

When a provider reports model unavailability (e.g., `anthropic.BadRequestError` with code 1010 or `openai.NotFoundError`), `ModelFallbackMiddleware` immediately converts this to a user-visible `AIMessage` describing the outage and model name. It does not retry, because the model is genuinely not available to the API key; retrying would not help.

## Safe changes and focused tests

**Ordering constraints** that must be preserved:

1. `ModelCallTimeoutMiddleware` must be innermost; moving it outside fallback prevents timeout recovery.
2. `ModelErrorMiddleware` must be outside the deadline but inside fallback; moving it outside fallback misses failures that fallback consumes.
3. `ExcludeToolsMiddleware` must follow `SubdirAgentsReadMiddleware` and tool configuration; tool availability depends on prior setup.
4. Preparation must checkpoint before the first model call; otherwise resumed invocations will re-prepare when they should not.
5. `refresh_github_proxy_before_model` must run before model calls to refresh the token before it expires.

**Idempotence contracts**:

- Keep `_prepare` idempotent in all `BasePrepareRunMiddleware` subclasses; resumption will re-run it.
- Retain delete-before-inject queue semantics; resumed invocations must not duplicate messages.
- Treat a sandbox error as retryable only when the SDK guarantees the command never started (`SandboxRetryableConnectionError`).

**Focused tests to extend**:

- Queue injection and deduplication (`check_message_queue_before_model`)
- Dynamic tool lazy loading and availability
- Preparation latching and fingerprinting
- Orphaned-call repair (`RepairOrphanedToolCallsMiddleware`)
- Stable tool result ordering
- Timeout cancellation and deadline enforcement
- Fallback alternation and eligibility checks
- Step-limit notification
- Subdirectory instruction inheritance
- Usage recording and cost bookkeeping
- Model selection routing
- Sandbox recovery policies (reviewer replacement permitted, coding-agent replacement not permitted)
- Tool error classification and recovery
