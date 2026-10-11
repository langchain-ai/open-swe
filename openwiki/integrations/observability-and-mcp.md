---
type: integration and observability architecture
title: Observability, Tracing, and MCP
description: LangSmith tracing and cost collection, analytics event capture, invocation and session cost tracking, MCP connections, and full-transcript recording for debugging and replay.
tags: [observability, tracing, mcp, analytics, langsmith, credentials, costs, transcript]
sources:
  - id: openwiki-source-dcac5237c97d18021dd8e1b7
    resource: repo://openswe/agent_cost.py
  - id: openwiki-source-b23248ea29f610c9e611ae8c
    resource: repo://openswe/analytics/usage.py
  - id: openwiki-source-1c10d1b4bdf51f06452acf76
    resource: repo://openswe/api/tracing.py
  - id: openwiki-source-8e86bc5c07d70c0696441776
    resource: repo://openswe/mcp/instance.py
  - id: openwiki-source-573a0cf072c076b2dd72dbbd
    resource: repo://openswe/mcp/runtime.py
  - id: openwiki-source-75b8c8788feef409fe98f64c
    resource: repo://openswe/mcp/user.py
  - id: openwiki-source-1175dd137a24de0ce785a4c1
    resource: repo://openswe/mcp/workspace.py
  - id: openwiki-source-75a672d9a8b6d6c500b1cf8d
    resource: repo://openswe/middleware/dynamic_tools.py
  - id: openwiki-source-7f36425bcc9aa67f9d9ae31a
    resource: repo://openswe/middleware/trace.py
  - id: openwiki-source-39f8a68480d768367a7dd112
    resource: repo://openswe/middleware/transcript.py
  - id: openwiki-source-919e16feae379651f2cbc1c9
    resource: repo://openswe/server.py
  - id: openwiki-source-84f99988450cd60ecc31ec89
    resource: repo://openswe/session_cost.py
  - id: openwiki-source-78aaabb479325754c1118320
    resource: repo://openswe/utils/langsmith.py
generated: { by: "openwiki/0.4.2", at: "2026-10-08T15:19:10.971Z" }
verified:
  - by: openwiki/0.4.2
    at: 2026-10-08T15:19:10.971Z
---

# Observability, Tracing, and MCP

The agent combines three observability and integration systems:

1. **LangSmith tracing and cost collection**: Every run is traced to LangSmith for debugging, inspection, and model-call cost attribution. Trace resource names are set by APM middleware, and trace URLs are generated for dashboard embedding and user review context.

2. **Analytics event capture**: Invocation starts, completions, failures, and deferred cost updates flow through a fail-soft event system backed by PostgreSQL. Session costs track per-workspace usage for attribution.

3. **Extensible integrations**: MCP (Model Context Protocol) connections are scoped by instance, workspace, and user. Missing credentials or connection failures remove optional tools rather than blocking a run.

Additionally, the system maintains a **full append-only transcript** for debugging, replay, and audit purposes, and wraps middleware with tracing policies that omit sensitive input payloads.

See [Authentication and security](../concepts/auth-and-security.md) for the broader trust model, [Tools](../concepts/tools.md) for dynamic tool availability, [Models, profiles, and instructions](../concepts/models-profiles-instructions.md) for model selection, and [Configuration](../operations/configuration.md) for environment settings.

## LangSmith tracing and APM instrumentation

Every agent run is traced to [LangSmith](https://www.langsmith.com) for inspection, debugging, and model-call cost attribution. The tracing system provides:

- **Trace resource naming**: `openswe/api/tracing.py` defines `TraceResourceNameMiddleware`, which names APM spans after the route that handled each request. Since the platform instruments the server at a higher level, requests would otherwise land on a single generic resource. The middleware captures the route path and method (e.g., `GET /api/mcp/instance`) so traces can be searched, compared, and alerted on by endpoint.

- **Middleware trace policies**: `openswe/middleware/trace.py` provides `OpenSWEMiddleware`, a base class for all agent middleware that enforces `SCRUBBED_TRACE_POLICY`, which omits input payloads from traces to protect sensitive data. The `scrub_middleware_inputs` helper applies this policy to any traceable middleware before registration.

- **Trace URL generation**: `openswe/utils/langsmith.py:get_langsmith_trace_url` builds LangSmith dashboard URLs for embedding in dashboard context blocks and for user review. It resolves the tenant ID and project ID once per workspace and caches them; it returns `None` if LangSmith is not configured. This provides a best-effort link that works even if tracing is misconfigured; it does not fail the run.

LangSmith integration requires `LANGSMITH_API_KEY` and `LANGSMITH_ENDPOINT` environment variables, with optional `LANGSMITH_TENANT_ID` to bypass discovery.

## Cost collection and deferred enrichment

After a run completes, cost is enriched and tracked by two independent systems:

### Invocation cost tracking

`openswe/agent_cost.py:finalize_agent_invocation_usage` records terminal invocation usage and schedules deferred cost collection:

1. **Immediate recording** of completion status and model-call token counts (input, output, total) via `record_agent_invocation_completion`.
2. **Cost refresh scheduling** with bounded retries at delays (15, 30, 60, 120, 240 seconds) if cost is not immediately available from LangSmith.

The refresh function `run_agent_cost_refresh` queries LangSmith thread stats to retrieve total invocation cost, correlating by `invocation_id` or `prepare_run_id` metadata, then stores the cost via `record_agent_invocation_cost`. If cost retrieval fails with a 4xx error (except 408/429), the error is treated as terminal and no further retries are scheduled; otherwise, timeouts and 5xx errors schedule the next attempt.

### Session cost tracking

`openswe/session_cost.py` tracks cumulative per-workspace usage and posts deferred cost updates to Slack replies. Each Slack run has a mapped final reply message; when session cost becomes available, the bot updates the message to display total cost and token usage. Like invocation costs, session cost refresh is scheduled with bounded retries, but it also marks the Slack message with a pending-cost indicator before the cost is known.

### Analytics event capture

`openswe/analytics/usage.py` captures product lifecycle events into PostgreSQL:

- **`record_agent_invocation_usage`**: Logs when a run starts, with model ID, effort level, source entry point, and user identity.
- **`record_agent_invocation_completion`**: Logs terminal status (success, failure, timeout, or cancellation), failure code, and token counts.
- **`record_agent_invocation_cost`**: Emits the `run.cost_recorded` event when cost becomes available.

Events are queued via `openswe/analytics/emitter.py` with fail-soft semantics: failures to persist analytics never block the run. Events carry an opaque `workspace_id` for multi-tenant attribution and are stored in an outbox table for asynchronous delivery.

## Transcript recording

`openswe/middleware/transcript.py` captures a complete append-only transcript of every run for debugging, replay, and audit purposes. One middleware instance serves all runs; per-run state lives in a module-level registry keyed by `thread_id:run_id`. Transcripts are written by a background task so the model stream never waits on database I/O.

The transcript captures:

- **Turn lifecycle events**: request, start, completion, interruption, failure.
- **Message events**: each agent message, human message, and tool message with sender identity, attachments, and usage (tokens).
- **Tool lifecycle**: tool calls with invocation details and results (truncated to 256 KiB and stripped of sensitive data).
- **Reasoning blocks**: expanded reasoning and internal thinking from advanced models like Claude Sonnet 4.

Events are queued and flushed asynchronously by a writer per run, with soft-buffer boundaries at paragraph breaks and hard boundaries at 24 KB. Failures to write transcripts are logged but never fail the run, so transcript problems are observability-only.

## MCP connection scoping and discovery

MCP connections are organized in a three-tier hierarchy: **instance** (globally managed by deployment admins), **workspace** (managed by workspace admins per slug), and **user** (personal connections owned by an individual GitHub login). When loading tools for a run, all three tiers are consulted in order; a later tier's connection with the same name replaces the earlier one.

- **Instance tier** (`openswe/mcp/instance.py`): Connections stored in `["instance_mcps"]` that every run can access. The tier adopts and migrates pre-workspace records from the flat `["workspace_mcps"]` namespace on first read, ensuring backward compatibility.
- **Workspace tier** (`openswe/mcp/workspace.py`): Connections stored under `["workspace_mcps", <slug>]`, where `<slug>` is the lowercase workspace identifier. Each workspace has its own set of connections.
- **User tier** (`openswe/mcp/user.py`): Personal connections stored under `["user_mcps", <login>]`, scoped to a GitHub login and visible only in runs where that login is the triggering user.

Each tier exposes its own admin UI route for creation, validation, token refresh, and discovery. A connection is a reusable MCP server reference with a name, URL, transport (streamable_http or SSE), optional headers (including OAuth token URLs and client secrets), and an optional allowlist of tool names to expose. Stored tokens are encrypted at rest with `openswe.encryption`.

## Tool loading and availability

The server loads MCP tools during agent assembly for non-summary, non-local runs with a known credential scope. It loads the three tiers via `load_mcp_tools(*sources)` in `openswe/mcp/runtime.py`, which walks each source's connection list, discovers tool definitions, and builds runnable tools. The `DynamicToolMiddleware` exposes only tool *names* initially and defers the MCP handshake (which may fail, timeout, or be expensive) until the agent explicitly calls `load_integration_tools` and requests to use them. This lazy strategy avoids blocking the first model call when MCP availability is uncertain.

Tool loaders use a stale-while-revalidate TTL cache (300 seconds for most tiers, 600 for expensive operations) and enforce a per-load timeout. An exception, timeout, or missing credentials returns an empty tool list rather than failing the run. Reads on the tool-loading path are fail-soft: if the Store backend is unreachable, the run continues without those tools, whereas dashboard status reads surface Store failures.

```mermaid
graph TD
  Trigger["Triggering user"] --> Scope["Credential scope known?"]
  Scope -->|yes, not local/summary| Load["Load MCPs"]
  Scope -->|no or local/summary| Skip["Skip optional tools"]
  Load --> Instance["Instance tier"]
  Load --> Workspace["Workspace tier"]
  Load --> User["User tier if known login"]
  Instance --> Discover["MCP discovery + tool cache"]
  Workspace --> Discover
  User --> Discover
  Discover --> Dynamic["DynamicToolMiddleware exposes names only"]
  Skip --> Server["LangGraph server"]
  Dynamic --> Server
  Discover --> MCP["MCP servers"]
  Server --> MCP
```

MCP tool loading flow: credential scope gates tool loading, then instance, workspace, and user tiers are consulted in order and combined before exposure to the agent via DynamicToolMiddleware.

## Tool loading lifecycle and failures

While assembling a non-local, non-summary agent for a run with known credential scope, the server loads:

- **MCP tools** from instance, workspace, and user tiers using `_mcp_tools_for`.

Summary-stop and local/desktop runs skip optional tool loading. The tool loader is wrapped in a TTL cache and timeout, returning an empty list on failure. The server then registers the tool group with `DynamicToolMiddleware`, which defers the MCP handshake. The agent can call `load_integration_tools` to load the group and begin using its tools.

The operational invariant is that optional tool loss reduces available tools, not the ability to start or complete a run. If all MCPs are unavailable, the agent continues without them.

## Operational safeguards and observability invariants

The combination of these systems enforces key invariants:

- **No observability blocking**: LangSmith cost collection, analytics capture, and transcript writing use fail-soft patterns so failures are logged but never block a run or cause a visible error to the user.
- **Lazy tool availability**: MCP handshakes are deferred until explicitly requested, so unavailable or slow integrations do not block the first model call.
- **Trace isolation**: Middleware trace policies omit sensitive input payloads from traces, and trace resource naming happens at the middleware boundary so platform-level APM can properly instrument routes.
- **Cost attribution**: Session costs and invocation costs are tracked independently with bounded-retry scheduling, ensuring cost data eventually reaches Slack replies and analytics even if LangSmith is temporarily unavailable.

## Focused verification

Relevant tests cover:

- **Analytics capture**: Event queueing and invocation lifecycle in `tests/analytics/`.
- **MCP loading**: Tool loading and caching in `tests/tools/test_mcp_sources.py` and `tests/tools/test_workspace_mcp_tools.py`; tier consultation and connection discovery in `tests/tools/test_mcp_oauth.py`.
- **Concurrent assembly**: Factory-level tool loading in `tests/agent/test_factory_tool_loading.py`.
- **Transcript recording**: Transcript lifecycle and event flushing in tests under `tests/middleware/transcript/` and `tests/transcript/`.
- **Cost tracking**: Invocation and session cost refresh in `tests/agent/test_agent_cost.py` and `tests/agent/test_session_cost.py`.
