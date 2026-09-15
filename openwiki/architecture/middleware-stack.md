---
type: architecture-component
title: Agent Middleware Stack
description: Ordering-sensitive middleware around coding-agent and reviewer model and tool loops. Covers preparation, context compaction, policy guards, queue delivery, retries, deadlines, usage, and provider-message normalization.
tags: [middleware, agent, reviewer, model-call, tool-call, fallback, guardrails]
sources:
  - id: openwiki-source-828b741451bbda4468382d9b
    resource: repo://agent/middleware/check_message_queue.py
  - id: openwiki-source-a7ebc203098eabde91c26f60
    resource: repo://agent/middleware/conversation_offloading.py
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
  - id: openwiki-source-f26d060fb4408e89b50964a5
    resource: repo://agent/middleware/plan_mode.py
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
  - id: openwiki-source-92111c4334ccba0303d5acde
    resource: repo://agent/middleware/validate_image_reads.py
  - id: openwiki-source-c53f5f816c45a89d9453ccd6
    resource: repo://agent/middleware/workflow_push_guard.py
  - id: openwiki-source-9e521d5bdc790cdf222fc698
    resource: repo://agent/middleware/workspace_skills.py
  - id: openwiki-source-276ab38291eb5741b4c2141c
    resource: repo://agent/reviewer.py
  - id: openwiki-source-267a662990890ab782a8bf32
    resource: repo://agent/sandboxes/retry.py
  - id: openwiki-source-856ade03ef31ac38e1347f7c
    resource: repo://agent/server.py
  - id: openwiki-source-10026b2dd7b7368bb04e27f0
    resource: repo://tests/sandbox/test_reviewer_sandbox_recovery.py
  - id: openwiki-source-b074bf11145a0ff6206cec7b
    resource: repo://tests/sandbox/test_sandbox_retry.py
verified:
  - by: openwiki/0.4.2
    at: 2026-09-15T08:15:12.744Z
generated: { by: "openwiki/0.4.2", at: "2026-09-15T08:15:12.744Z" }
---

# Agent Middleware Stack

`get_agent` and `get_reviewer_agent` give ordered middleware lists to `create_deep_agent`. The list is an onion: earlier entries are outer wrappers around later entries. Consequently, outer middleware can prepare a request, intercept a tool call, or handle exceptions from every inner layer; ordering is a runtime contract. See [Agent Graph](agent-graph.md), [Tools](../concepts/tools.md), [Follow-up Messages](../workflows/follow-up-messages.md), and [PR Creation](../workflows/pr-creation.md) for adjacent concerns.

## Coding-agent assembly and scope

The coding-agent list is outer to inner:

1. `ConversationOffloadingMiddleware`
2. `PrepareAgentRunMiddleware`
3. `IncidentMiddleware`, when an incident session exists
4. `WorkspaceSkillsMiddleware`, for non-local anonymous runs
5. `DynamicToolMiddleware`, when configured integration groups are nonempty
6. `SanitizeToolInputsMiddleware`
7. `ValidateImageReadsMiddleware`
8. `ModelCallLimitMiddleware`
9. `ToolErrorMiddleware`
10. `ExcludeToolsMiddleware`
11. `SubdirAgentsReadMiddleware`
12. `ToolRetryMiddleware` for `task`
13. `PullRequestCreationGuardMiddleware`, except in local runs
14. `WorkflowPushGuardMiddleware`
15. `refresh_github_proxy_before_model`
16. `check_message_queue_before_model`, except in stop-summary mode
17. `TimeoutWrapupMiddleware`
18. `notify_step_limit_reached`
19. `record_run_usage`
20. `ModelSelectionMiddleware`, when adaptive routing is enabled
21. `ModelFallbackMiddleware`, when a distinct fallback resolves
22. `PlanModeMiddleware`
23. `SanitizeFireworksMessagesMiddleware`
24. `SanitizeOpenAIResponsesMiddleware`
25. `SanitizeThinkingBlocksMiddleware`
26. `StableToolResultOrderMiddleware`
27. `ModelErrorMiddleware`
28. `ModelCallTimeoutMiddleware`

```mermaid
flowchart TD
  Offload["Conversation offloading"] --> Prepare["Run preparation"]
  Prepare --> Scope["Conditional incident skills and dynamic tools"]
  Scope --> ToolPath["Tool repair validation policy and retry"]
  ToolPath --> Before["Proxy refresh and queue hooks"]
  Before --> Lifecycle["Wrapup limit notice and usage"]
  Lifecycle --> Routing["Optional routing and fallback"]
  Routing --> ProviderPrep["Plan filter and message normalization"]
  ProviderPrep --> Failure["Error recorder and deadline"]
  Failure --> Provider["Provider model call"]
```
This shows the coding-agent middleware nesting, with earlier boxes wrapping later boxes.

The parent stack is intentionally broader than subagents. The parent installs preparation, policy, queue, usage, selection, and fallback responsibilities. Its general-purpose subagent receives its own conversation offloader and may receive incident, workspace-skill, and dynamic-tool middleware, but does not inherit the complete parent list; `ToolRetryMiddleware` is therefore the parent-side escalation boundary around the delegated `task` tool.

`ConversationOffloadingMiddleware` extends Deep Agents summarization. It can compact context automatically or, when configured manually, ends the graph after producing the summary; it emits start, completion, failure, or skipped status events while hiding summarizer output from the user stream. `WorkspaceSkillsMiddleware` replaces the built-in skills middleware at its normal stack position and scopes checkpointed skill metadata to its configured source routes, preventing unrelated personal-skill context from being retained in public thread state.

## Preparation, context, and tools

`BasePrepareRunMiddleware` supplies checkpointed `before_agent` setup for agent and reviewer specializations. Its fingerprint combines the middleware class, latest message, and preparation configuration. A matching `run_prepared_for` latch skips completed setup on a resumed invocation; a later invocation on the same thread gets fresh tokens, prompts, and review/diff context. Subclass preparation must be idempotent because a failure before the checkpoint may execute it again. Its model wrapper inserts the rendered system prompt.

`DynamicToolMiddleware` exposes integration groups lazily, while `ExcludeToolsMiddleware` removes configured names from model requests. `SanitizeToolInputsMiddleware` repairs malformed `read_file` `offset` and `limit` strings before validation. `ValidateImageReadsMiddleware` checks image blocks returned by `read_file` against known image signatures and replaces an extension/content mismatch with a text error, avoiding a checkpointed invalid image that would cause later provider requests to fail.

`PlanModeMiddleware` is always installed. Its `before_agent` hook resets `plan_mode` to the value resolved for this invocation, and it recomputes offered tools on every model call. While active it removes external-mutation tools (and MCP tools in the coding-agent configuration), so an `enter_plan_mode` action restricts the next turn rather than only a run that started in plan mode. When adaptive routing is enabled, `ModelSelectionMiddleware` persists a selected route per run and swaps the request model; plan mode uses the performance route, and the optional fast-versus-fast-alt experiment is deterministic per thread.

Before every model call, the proxy-refresh hook best-effort renews a near-expiry sandbox GitHub installation token. The queue hook then reads `("queue", thread_id)` from the LangGraph store, deletes `pending_messages` before building the update to avoid duplicate delivery, and injects queued human input FIFO. It also consumes a pending autofix event. Follow-up images are omitted when the resolved model lacks vision support.

## Policy, limits, and completion hooks

`ModelCallLimitMiddleware` terminates the agent at its configured run limit; in the main agent an incident policy may override the normal recursion limit. `TimeoutWrapupMiddleware` starts a monotonic clock lazily per middleware instance and, after `OPEN_SWE_WRAPUP_TIMEOUT_SECONDS` (45 minutes by default), appends a one-time wrap-up instruction to each subsequent model request. `notify_step_limit_reached` is an after-agent hook that recognizes the limit marker and best-effort posts a Slack-thread explanation.

The PR guard blocks `execute` and `background_execute` fallbacks that create pull requests outside `open_pull_request`, including `gh pr create`, GitHub API, `curl`, and bounded nested-shell forms. It returns a non-recoverable error `ToolMessage` instead of running the command and is omitted only for local runs. The workflow-push guard intercepts pushes which change `.github/workflows`: it records the exact change fingerprint, requests human approval, and only then substitutes a safe rewritten push command.

`RecordRunUsageMiddleware` tags model responses with invocation and selected-route metadata. On an exception it finalizes an invocation as an error (including authentication rejection where identifiable), and its after-agent hook finalizes usage for runs with both invocation and thread IDs. This is a parent-only accounting boundary.

## Model failure and recovery

Provider-specific message sanitizers and stable tool-result ordering form the request-normalization boundary immediately inside plan-mode filtering. `ModelCallTimeoutMiddleware` is innermost, so its wall-clock deadline covers the provider operation itself. A stalled call becomes `ModelCallTimeoutError`, a `TimeoutError`; `ModelErrorMiddleware` logs, classifies, stores type and classification code in thread metadata when context is available, and re-raises it. The optional fallback wrapper is outside both layers and can then retry the classified timeout.

```mermaid
flowchart TD
  Request["Normalized model request"] --> Fallback["Fallback attempt"]
  Fallback --> Errors["Model error recorder"]
  Errors --> Deadline["Model call deadline"]
  Deadline --> Provider["Provider call"]
  Provider --> Result["Model response"]
  Provider -. "timeout or provider error" .-> Deadline
  Deadline -. "typed timeout" .-> Errors
  Errors -. "record and re-raise" .-> Fallback
  Fallback -. "retryable failure" .-> Alternate["Alternate primary or fallback"]
  Alternate --> Errors
  Fallback -. "budget exhausted" .-> Outage["Visible outage message"]
```
This is the inner model failure flow; recording occurs before fallback decides whether to retry.

`ModelCallTimeoutMiddleware` reads `OPEN_SWE_MODEL_CALL_TIMEOUT_SECONDS`, accepts only a positive numeric value, and otherwise uses 900 seconds. This sits above provider client request timeouts, allowing provider-level retry first while making websocket stalls observable.

`ModelFallbackMiddleware` is installed only when configured or default fallback resolution produces a different model. It makes six attempts by default—one initial attempt plus backoff entries `0, 5, 15, 30, 45` seconds, with positive-delay jitter—and alternates primary and fallback models. It retries connection and timeout failures, retryable `ModelError`, and selected 408/409/425/429/5xx/529 provider statuses. Model-not-available access errors immediately become a user-facing `AIMessage`; exhausted transient failures normally return an outage `AIMessage`, though `surface_outage_message=False` re-raises the last error.

## Tool and sandbox failure boundary

`ToolErrorMiddleware` turns ordinary unhandled tool exceptions into `ToolMessage(status="error")` JSON, allowing the model to self-correct. A `SandboxRetryableConnectionError` becomes a `sandbox_transient` error because the SDK guarantees that the command never started. In contrast, `SandboxConnectionError` other than `SandboxServerReloadError`, and a `ResourceNotFoundError` for the sandbox resource, are unreachable-sandbox failures: the middleware notifies the user and re-raises to end the run rather than repeatedly fail and notify.

`ToolRetryMiddleware` is narrower than model fallback: it wraps parent `task` delegation with at most two retries, a one-second initial delay, and ten-second maximum delay. Its predicate accepts retryable statuses and transient transport names, including a subagent `ModelCallTimeoutError`, because subagents lack parent fallback. On exhaustion, prompt/context errors return structured failed data to the model; other errors are re-raised.

`retry_transient_sandbox_errors`, used for direct operations, retries only SDK-marked pre-start connection rejections, at most four times with bounded exponential backoff and jitter. Sandbox-unreachable notification prefers the active Slack thread, then Linear, then a configured GitHub issue or PR when a token is available. Coding-agent recovery deliberately does not automatically replace a sandbox: replacement could conceal loss of uncommitted work.

## Reviewer differences and exit guarantee

The reviewer has a smaller outer-to-inner stack: `PrepareReviewerRunMiddleware`, `SanitizeToolInputsMiddleware`, `ModelCallLimitMiddleware`, `ToolErrorMiddleware`, `refresh_github_proxy_before_model`, `check_message_queue_before_model`, `TimeoutWrapupMiddleware`, the three message sanitizers, `RepairOrphanedToolCallsMiddleware`, `StableToolResultOrderMiddleware`, `ModelErrorMiddleware`, `ModelCallTimeoutMiddleware`, and `settle_review_check_on_exit`.

It omits conversation offloading, incident and workspace-skill layers, dynamic/excluded tools, image-read validation, subdirectory instructions, task retry, PR/workflow guards, run usage, model selection, plan mode, and fallback. `RepairOrphanedToolCallsMiddleware` inserts synthetic error results directly after persisted AI tool calls whose IDs have no `ToolMessage`, so an interrupted review can resume without provider tool-result validation failure.

Reviewer sandbox setup may replace a dead sandbox because its checkout is re-derived. A failed replacement remains a typed `SandboxUnreachableError`. On reviewer exit, `settle_review_check_on_exit` closes an unpublished tracked check as neutral so an infrastructure interruption is not presented as a PR code failure. If `publish_review` finished but its completion PATCH failed, it instead retries the stored real conclusion.

## Safe changes and focused tests

When adding a layer, decide whether it applies to the parent, subagent, reviewer, or only a model/tool phase, then preserve its nesting relationship. In particular, moving the deadline outside fallback prevents timeout recovery, moving error recording outside fallback misses failures fallback consumes, and moving queue deletion after injection risks duplicate follow-up delivery. Treat sandbox failures as retryable only where the SDK guarantees the command did not start.

Focused tests cover queue injection, fallback eligibility and alternation, preparation latching, timeout cancellation, step-limit notification, subdirectory instructions, and sandbox retry/recovery. Extend the closest test when changing an ordering edge, a classification boundary, or a completion short-circuit.
