---
type: architecture-component
title: Agent Middleware Stack
description: Ordering-sensitive middleware around coding-agent and reviewer model and tool loops. Covers preparation, transcript capture, policy enforcement, queue delivery, fallback, completion, and sandbox failure boundaries.
tags: [middleware, agent, reviewer, model-call, tool-call, transcript, guardrails]
verified:
  - by: openwiki/0.4.2
    at: 2026-09-23T08:15:27.313Z
sources:
  - id: openwiki-source-828b741451bbda4468382d9b
    resource: repo://agent/middleware/check_message_queue.py
  - id: openwiki-source-0b53777f0ea426a90cf976b4
    resource: repo://agent/middleware/model_call_timeout.py
  - id: openwiki-source-92dfac98dd4efa19a44e0c4e
    resource: repo://agent/middleware/model_errors.py
  - id: openwiki-source-5bbb58a2bed24dc7e0fea26d
    resource: repo://agent/middleware/model_fallback.py
  - id: openwiki-source-35d4ee0245b72a6fbd3e7345
    resource: repo://agent/middleware/model_selection.py
  - id: openwiki-source-f996b5011c02e2c53895ada1
    resource: repo://agent/middleware/notify_step_limit.py
  - id: openwiki-source-3d6d2704e3f7fa58a6207393
    resource: repo://agent/middleware/pr_creation_guard.py
  - id: openwiki-source-de97adb0acb9dec0664a44b6
    resource: repo://agent/middleware/prepare_run.py
  - id: openwiki-source-739850fbbfceb2f1f047ce4e
    resource: repo://agent/middleware/record_run_usage.py
  - id: openwiki-source-9d5775155057d8f8c3a08e3e
    resource: repo://agent/middleware/refresh_github_proxy.py
  - id: openwiki-source-68ed7096f2c698e329abb45c
    resource: repo://agent/middleware/repair_orphaned_tool_calls.py
  - id: openwiki-source-5c8eb2cbacd6371e399d4b52
    resource: repo://agent/middleware/require_user_reply.py
  - id: openwiki-source-69db7ced9516fc1b66a19d47
    resource: repo://agent/middleware/sandbox_circuit_breaker.py
  - id: openwiki-source-3de68f2dbfda5bbd7f86131c
    resource: repo://agent/middleware/sanitize_tool_inputs.py
  - id: openwiki-source-626b1e5ad4f4c7d45dbc8f12
    resource: repo://agent/middleware/settle_review_check.py
  - id: openwiki-source-bcc3375e7c46eaf87e2b2f28
    resource: repo://agent/middleware/task_retry.py
  - id: openwiki-source-f1fe8d3c50a37935c727ca87
    resource: repo://agent/middleware/timeout_wrapup.py
  - id: openwiki-source-a3215ee5f347eab65c5c27a3
    resource: repo://agent/middleware/tool_error_handler.py
  - id: openwiki-source-17ea8e97cc9e7a3b7987fc9f
    resource: repo://agent/middleware/transcript.py
  - id: openwiki-source-92111c4334ccba0303d5acde
    resource: repo://agent/middleware/validate_image_reads.py
  - id: openwiki-source-c53f5f816c45a89d9453ccd6
    resource: repo://agent/middleware/workflow_push_guard.py
  - id: openwiki-source-276ab38291eb5741b4c2141c
    resource: repo://agent/reviewer.py
  - id: openwiki-source-267a662990890ab782a8bf32
    resource: repo://agent/sandboxes/retry.py
  - id: openwiki-source-856ade03ef31ac38e1347f7c
    resource: repo://agent/server.py
  - id: openwiki-source-21b76dac7c922f46808bae74
    resource: repo://tests/middleware/test_check_message_queue.py
  - id: openwiki-source-10026b2dd7b7368bb04e27f0
    resource: repo://tests/sandbox/test_reviewer_sandbox_recovery.py
  - id: openwiki-source-b074bf11145a0ff6206cec7b
    resource: repo://tests/sandbox/test_sandbox_retry.py
generated: { by: "openwiki/0.4.2", at: "2026-09-23T08:15:27.313Z" }
---

# Agent Middleware Stack

`build_agent` and the reviewer factory give ordered middleware lists to `create_deep_agent`. The list is an **onion**: entries nearer the beginning wrap entries after them for the hooks they share. A wrapper can therefore modify a request before an inner layer sees it, observe an inner exception, or alter completion behavior. Middleware order is a change-safety contract, not a cosmetic list: moving a deadline, fallback, policy guard, or transcript layer can change recovery, security, or observability semantics. See [Agent Graph](agent-graph.md), [Tools](../concepts/tools.md), [Follow-up Messages](../workflows/follow-up-messages.md), and [PR Creation](../workflows/pr-creation.md).

## Coding-agent assembly

The production coding-agent list below is outer to inner. Items marked conditional are omitted when their factory condition is not met.

1. `ConversationOffloadingMiddleware`
2. `PrepareAgentRunMiddleware`
3. `TranscriptMiddleware`
4. `IncidentMiddleware` — conditional on an incident session
5. `WorkspaceSkillsMiddleware` — conditional for eligible non-local credential-less runs
6. `DynamicToolMiddleware` — conditional when integration groups exist
7. `SanitizeToolInputsMiddleware`
8. `ValidateImageReadsMiddleware`
9. `ModelCallLimitMiddleware`
10. `ToolErrorMiddleware`
11. `ExcludeToolsMiddleware`
12. `SubdirAgentsReadMiddleware`
13. `ToolRetryMiddleware` for `task`
14. `PullRequestCreationGuardMiddleware` — omitted for local/desktop runs
15. `WorkflowPushGuardMiddleware`
16. `refresh_github_proxy_before_model`
17. `check_message_queue_before_model` — omitted in stop-summary mode
18. `TimeoutWrapupMiddleware`
19. `RequireUserReplyMiddleware`
20. `notify_step_limit_reached`
21. `record_run_usage`
22. `ModelSelectionMiddleware` — conditional when adaptive routing is enabled
23. `ModelFallbackMiddleware` — conditional when a distinct fallback model resolves
24. `SanitizeFireworksMessagesMiddleware`
25. `SanitizeOpenAIResponsesMiddleware`
26. `SanitizeThinkingBlocksMiddleware`
27. `StableToolResultOrderMiddleware`
28. `ModelErrorMiddleware`
29. `ModelCallTimeoutMiddleware`

The first layers establish the run and its durable observation boundary. Preparation populates state and the system prompt; transcript writes are best-effort observability, deliberately decoupled through a per-run background writer so a transcript failure cannot fail the run. Conversation offloading derives from the deep-agents summarization middleware, records its outcome in state and emits status events; its summary model is hidden from the user-facing stream.

The middle layers govern the tool surface. Sanitization repairs known malformed numeric tool arguments, while image-read validation prevents extension/content mismatches from leaving an invalid image block checkpointed in conversation history. Exclusion and subdirectory instructions shape the model-visible surface. The two command guards run outside the actual tool execution: the PR guard returns an error result for shell/API fallbacks that create a PR rather than `open_pull_request`, and the workflow guard holds pushes that change `.github/workflows` until recorded human approval.

### Model-call onion

```mermaid
flowchart TD
  Outer["Preparation, transcript, policy, queue, and completion wrappers"] --> Route["Optional model selection"]
  Route --> Fallback["Optional fallback retry"]
  Fallback --> Clean["Provider sanitizers and stable tool result order"]
  Clean --> Errors["Model error recorder"]
  Errors --> Deadline["Model call deadline"]
  Deadline --> Provider["Provider model call"]
  Provider -. "deadline error" .-> Errors
  Errors -. "record then re-raise" .-> Fallback
  Fallback -. "transient attempts exhausted" .-> Visible["Terminal outage AI message"]
```
*Caption: The ordered inner coding-agent model-call path; exceptions move outward, so the deadline must remain inside error recording and fallback.*

`ModelCallTimeoutMiddleware` is innermost, so `asyncio.wait_for` covers the provider operation rather than merely outer bookkeeping. It converts a wall-clock expiration into `ModelCallTimeoutError`, a `TimeoutError`. `ModelErrorMiddleware` logs and classifies that exception, persists its type and classification to thread metadata when context exists, then re-raises it. The optional fallback wrapper can consequently treat a timeout as transient. Moving the deadline outside fallback would disable this recovery; moving error recording outside fallback would omit errors the fallback absorbs.

When adaptive routing is enabled, `ModelSelectionMiddleware` stores the selected `fast`, `balanced`, or `performance` route in state before a model turn and overrides the request model. In automatic mode it uses a hidden structured-output classifier on the latest human task; a classifier failure falls back to `balanced`. `RecordRunUsageMiddleware` tags model responses with the selected route and invocation ID, and finalizes invocation usage both on normal agent completion and on model-call failure.

`ModelFallbackMiddleware` is attached only when configuration or the primary model's default resolves to a different fallback model. It alternates primary and fallback across six default attempts, using a jittered `0, 5, 15, 30, 45` second backoff schedule. Connection, timeout, retryable LangChain model errors, and selected overload/status failures are eligible; invalid requests are not. A provider model-access error is surfaced immediately as an `AIMessage`; exhausted eligible attempts ordinarily produce an outage `AIMessage`, while `surface_outage_message=False` re-raises the last error.

## Run lifecycle, follow-ups, and termination

`BasePrepareRunMiddleware` is the base for agent and reviewer setup. Its `before_agent` latch fingerprints the latest message, middleware class, and subclass preparation configuration. A matching checkpointed `run_prepared_for` skips setup on a resumed invocation; a changed invocation prepares fresh credentials, prompt material, and context. Subclass preparation must be idempotent because a failure before checkpointing can run it again. The wrapper then prepends `rendered_system_prompt` to the request system message.

Before each eligible coding-agent model call, `refresh_github_proxy_before_model` refreshes a near-expiry GitHub-proxy token. The queue hook also consumes a pending autofix event and reads `("queue", thread_id) / "pending_messages"`. It snapshots queued follow-ups in FIFO order, builds human input messages, and then removes only consumed entries. That last step is intentionally after construction: if a follow-up arrives during image fetching or model lookup, it remains queued instead of being deleted with the snapshot. A dashboard follow-up can move the owed reply surface from Slack to web; images are omitted with a warning for a text-only resolved model.

`TimeoutWrapupMiddleware` starts its per-instance monotonic clock lazily and, after `OPEN_SWE_WRAPUP_TIMEOUT_SECONDS` (45 minutes by default), appends a system instruction to finish the current step and report useful state rather than start new investigation. `ModelCallLimitMiddleware` ends at its configured limit; the after-agent step-limit hook recognizes its marker and notifies Slack. `RequireUserReplyMiddleware` initializes the reply surface for each run. If a turn that owes a reply ends without a successful final reply-tool result (or `slack_no_reply_needed`), it re-invokes the model with at most two nudges; after that budget it posts the last assistant text on the model's behalf rather than leave the requester silent.

## Tool and sandbox failure boundaries

`ToolErrorMiddleware` converts ordinary unhandled tool exceptions into `ToolMessage(status="error")` JSON with the error type, message, and tool name where available, allowing a subsequent model turn to correct course. It deliberately re-raises cancellation.

Sandbox failures are classified more narrowly:

- `SandboxRetryableConnectionError` means the SDK rejected the WebSocket upgrade before the execute frame was sent. It becomes a `sandbox_transient` error ToolMessage stating that nothing ran or changed; retrying it cannot double-run a command.
- `SandboxConnectionError`, except `SandboxServerReloadError`, means the sandbox is unreachable. `ResourceNotFoundError` is terminal only when its resource type is `sandbox`; a missing file remains an ordinary tool-local error.
- A terminal sandbox failure triggers a best-effort user notification and is re-raised, ending the run so later sandbox calls do not repeatedly fail and notify.

`retry_transient_sandbox_errors` is the direct-operation counterpart. It retries only the SDK-guaranteed pre-start error, at most four times with bounded exponential backoff and jitter. Terminal failures are never retried. Notification selects an active Slack thread first, then Linear, then a configured GitHub issue or PR when a token is available. The coding agent does not automatically replace an unreachable sandbox: replacement could hide loss of uncommitted work. Reviewer setup can replace its sandbox because its checkout is re-derived; a failed replacement remains a typed `SandboxUnreachableError`.

The delegated `task` tool has a separate retry envelope. `ToolRetryMiddleware` retries it twice for retryable HTTP/provider statuses and transport exception types, including a subagent `ModelCallTimeoutError` because subagents lack this parent fallback wrapper. On exhaustion, `task_on_failure` returns structured failed data only for invalid-prompt or context-length errors; other failures propagate.

## Reviewer stack and completion guarantee

The reviewer has a smaller, separately assembled list: `PrepareReviewerRunMiddleware`, `SanitizeToolInputsMiddleware`, `ModelCallLimitMiddleware`, `ToolErrorMiddleware`, `refresh_github_proxy_before_model`, `check_message_queue_before_model`, `TimeoutWrapupMiddleware`, the three provider message sanitizers, `RepairOrphanedToolCallsMiddleware`, `StableToolResultOrderMiddleware`, `ModelErrorMiddleware`, `ModelCallTimeoutMiddleware`, and `settle_review_check_on_exit`.

It does not install conversation offloading, transcript capture, dynamic/excluded tools, image validation, subdirectory instructions, task retry, PR/workflow guards, reply enforcement, usage recording, adaptive selection, or fallback. `RepairOrphanedToolCallsMiddleware` protects a resumed review whose persisted `AIMessage` contains a tool call but no corresponding result: before the next model request it inserts a synthetic error `ToolMessage` immediately after each orphaned call, allowing the model to recover rather than sending a provider-invalid transcript.

`settle_review_check_on_exit` is an after-agent completion guarantee. If a tracked review check remains unpublished, it closes it as **neutral**, since an incomplete reviewer run is infrastructure failure rather than a code failure. If `publish_review` completed but only the check-completion PATCH failed, stored pending conclusion data is retried instead of replacing the real conclusion with neutral.

## Safe extension and focused tests

When adding or moving middleware, first decide which hooks it needs (`before_agent`, `before_model`, model wrapper, tool wrapper, or `after_agent`) and test the resulting nesting. Preserve the outer-to-inner order above, especially the timeout → error recording → fallback relationship, guard placement ahead of execution, and best-effort transcript boundary. Preserve queue snapshot/consume behavior and only retry a sandbox operation when the SDK proves it never started.

Focused tests cover preparation latching and prompt injection, queue FIFO delivery and concurrent-arrival preservation, fallback eligibility/alternation, deadline cancellation, error-tool conversion and cancellation propagation, message sanitizers, image validation, orphaned-call repair, stable tool ordering, reply enforcement, offloading, model selection, usage recording, and transcript buffering. Sandbox retry/recovery tests additionally verify the pre-start retry invariant, no automatic coding-agent replacement, reviewer replacement eligibility, and typed failure when replacement fails. Extend the nearest of these tests whenever changing ordering, state ownership, an error classification, or a completion short circuit.
