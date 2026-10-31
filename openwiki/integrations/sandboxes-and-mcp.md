---
type: integration architecture
title: Sandboxes, remote files, and MCP
description: How Open SWE uses MDA-owned per-thread sandboxes, backend-held and bridged file backends, proxy credentials, and backend- or connection-backed MCP tools. Covers ownership boundaries, lifecycle failure behavior, and credential-safe discovery and execution.
tags: [sandboxes, mcp, mda, files, credentials, integrations]
verified:
  - by: openwiki/0.4.2
    at: 2026-10-31T08:08:21.705Z
sources:
  - id: openwiki-source-8037e2358a2c4f9b2c722a11
    resource: repo://AGENTS.md
  - id: openwiki-source-345a31e6397cebf3f169e57e
    resource: repo://mda/rt/file_tools.py
  - id: openwiki-source-07efe6e957b39a2f02e06f46
    resource: repo://mda/rt/managed_sandbox.py
  - id: openwiki-source-ed078077fc679821e071b802
    resource: repo://mda/rt/mcp.py
  - id: openwiki-source-dfa30b9eea2bf1ca86b1810e
    resource: repo://mda/rt/remote_files.py
  - id: openwiki-source-cb1bc0f466bd1e9ba4631d7e
    resource: repo://mda/rt/run_tools.py
  - id: openwiki-source-8cfc852eca618272050378e4
    resource: repo://mda/tests/test_mcp.py
  - id: openwiki-source-fb24ffc1656ce0258fa2ac33
    resource: repo://openswe/agent_api/graph.py
  - id: openwiki-source-3436762fd1bce7c774640399
    resource: repo://openswe/bridge/backend.py
  - id: openwiki-source-5491be991f9727afe1f3163d
    resource: repo://openswe/github/proxy.py
  - id: openwiki-source-6db7b33a8f25326a89817057
    resource: repo://openswe/mcp/models.py
  - id: openwiki-source-573a0cf072c076b2dd72dbbd
    resource: repo://openswe/mcp/runtime.py
  - id: openwiki-source-b879cf1922598f772bbd87e1
    resource: repo://openswe/mcp/transport.py
  - id: openwiki-source-1b32e9f41fa7e64702b380f6
    resource: repo://openswe/sandboxes/lifecycle.py
  - id: openwiki-source-b3b6cc9ac2ecd95444dcdfbe
    resource: repo://openswe/sandboxes/managed.py
  - id: openwiki-source-2dbb6fddd1531095bb57d08e
    resource: repo://openswe/sandboxes/state.py
  - id: openwiki-source-0ddf09f270a5d50c493da783
    resource: repo://openswe/sandboxes/tool_routes.py
  - id: openwiki-source-919e16feae379651f2cbc1c9
    resource: repo://openswe/server.py
  - id: openwiki-source-787fcf9357f1e9a001c78e0d
    resource: repo://tests/sandbox/test_proxy_auth.py
  - id: openwiki-source-71e56ad3da996973b32520ab
    resource: repo://tests/sandbox/test_sandbox_recreation.py
generated: { by: "openwiki/0.4.2", at: "2026-10-31T08:08:21.705Z" }
---

## Ownership and boundaries

Managed Deep Agents (MDA) owns the agent loop, checkpoints, tool calls, and the sandbox lifecycle for MDA-managed threads. The Open SWE backend is a stateless typed-API peer: it plans the run catalog, retains application data and credentials, and reconnects a thread to the sandbox MDA has opened. It does **not** create or replace an MDA-owned sandbox.

A catalog selects either a `SandboxSpec` or no MDA sandbox. MDA declares a single selectable sandbox definition for the shared graph, while the run catalog supplies create options, an optional existing sandbox ID, and the thread whose sandbox is used. MDA retains one sandbox per thread even if later catalog options change; those options affect creation only. Task workers may share their coordinator's MDA sandbox after the backend verifies that relationship. A bridged thread instead runs against its owner's machine, and an older backend-held sandbox may continue to be used until it is gone.

```mermaid
sequenceDiagram
    participant MDA as MDA run
    participant API as Open SWE backend
    participant Host as Sandbox service
    participant Files as File backend or bridge
    MDA->>Host: open thread sandbox without waiting
    MDA->>API: report sandbox identity during preparation
    API->>API: bind thread and reconnect services
    MDA->>Host: wait when first sandbox tool needs it
    MDA->>API: call typed operations and reports
    MDA->>Files: use remote paths or bridge when selected
```

This shows the run choosing its file execution location while the backend binds and services the sandbox MDA owns.

## MDA sandbox lifecycle

Before preparation, MDA opens or adopts the selected sandbox without waiting for it to boot, reports its ID to the backend, and starts booting in parallel with preparation and the first model call. The first sandbox-dependent file or shell tool waits for readiness. Opening retries transient service, network, timeout, and schedulability failures for up to 90 seconds; non-transient errors are reported with a secret-redacted cause. If a bound sandbox is missing, MDA replaces it and marks the workspace as lost. A sandbox that is merely unreachable is not silently replaced, because it can contain the only uncommitted working tree.

`recreate_sandbox` is handled by MDA middleware only for an MDA-managed sandbox. The tool result must carry valid replacement create options; MDA replaces the sandbox, notifies `SANDBOX_REPLACED` so the backend reconnects its thread services, restarts setup against the new handle, and returns the old/new IDs plus best-effort old-stop status. Backend lifecycle code can hand over an eligible *older* cloud sandbox by clearing the binding and recording the next-create request; the next MDA run then opens its own sandbox. It refuses this handover for MDA-owned, bridged, absent, or task-worker shared sandboxes.

The backend keeps stable per-thread backend proxies to reconnect an existing provider or bridge on any replica. The proxy can detach when metadata points at a different sandbox and serializes reconnect startup. Provider connection failures become `SandboxUnreachableError`; their semantics deliberately preserve the working tree rather than substituting a fresh environment.

### GitHub proxy configuration and refresh

Sandbox proxy configuration is split by ownership:

- With no `SandboxSpec.github_proxy`, the backend writes proxy configuration while connecting an MDA-owned sandbox, including a GitHub App token scoped to the thread or a narrower review scope. It records expiry, repository and permission scope so refresh cannot broaden access. A known expiry is refreshed within five minutes; an unknown expiry falls back to 50 minutes.
- With `github_proxy`, MDA obtains the backend's credential-free proxy portion through `sandbox_proxy`, reads the designated MDA connection immediately before writing, and combines its GitHub rules with backend rules. It writes before preparation, refreshes before model and tool calls when due (normally every five minutes), and requests fresh backend rules after a replacement. If the backend refuses that endpoint, it owns the write instead.

The initial MDA-owned write retries while an idle host may be suspending, for up to three minutes; permanent connection/not-found failures or exhausted retries end the run because neither side can safely assume the workspace's egress rules are installed. Routine later refresh failures are logged and retried, leaving the last configuration in place. Tokens and credentials are read at the point of use, never placed in general model-visible context.

## File and shell execution

`FileToolsMiddleware` exposes the normal deep-agent file surface (`ls`, `read_file`, `write_file`, `edit_file`, `delete`, `glob`, `grep`, and `execute`) but routes each request according to the catalog:

1. A path under a skill source is served by the runtime's read-only skill tools.
2. A path explicitly designated as a backend path uses `RemoteFiles` even when a sandbox exists.
3. A run without an MDA sandbox uses `RemoteFiles` for its thread files—for example, a bridged thread whose worktree is on a person's machine.
4. Other calls use the MDA sandbox.

`RemoteFiles` adapts typed backend file endpoints into the deepagents sandbox protocol, including file transfer, search, edits, and asynchronous command execution. It supplies the current run ID on every call. Large tool results live under the run's backend artifacts root; this keeps results out of the root of a bridged machine while retaining a file-tool view of them.

The same middleware applies its safety and observability behavior whichever route executes the call. It reports calls to the transcript, optionally reads applicable `AGENTS.md` instructions and validates image reads, converts tool errors into model-actionable results, and records potentially checkout-changing activity for git evidence. Commands that could push or manipulate pull requests are sent to the backend `check_shell` guards before execution. Sandboxes that reject a call before it starts are retried with short jittered backoff; an unanswered `execute` receives its configured timeout plus 30 seconds and becomes an error result.

### Local bridge behavior

`BridgeSandboxBackend` is the alternate sandbox protocol implementation for a user's machine. It authorizes every operation against the bridge owner, subscribes before queuing a request to avoid a notification race, and long-polls for `execute`, upload, or download results. Its liveness check re-reads state every 15 seconds, so a disconnected machine fails promptly rather than consuming the full command timeout. A bridge is never replaced by this connection path; a disconnected bridge yields an unreachable-sandbox error.

The backend's `/sandbox-tools` router no longer offers general list, search, or invocation endpoints: those routes return HTTP 410 and direct callers to direct tools or `search_tools` from `eval`. Its remaining callback routes are capability-authenticated, set `no-store` and `no-referrer`, and currently accept background-task completion/heartbeats and monitor sync notifications. Authorization and ownership checks distinguish rejected callbacks from unknown resources.

## MCP tool sources and execution

Open SWE supports two materially different MCP paths.

| Source | Discovery and invocation owner | Credential handling |
| --- | --- | --- |
| Backend-held MCP connection | Backend discovers, caches, wraps, and invokes the server tool | Encrypted headers or OAuth remain backend-side |
| MDA connection-backed MCP server | Backend supplies a catalog `McpServerSpec`; MDA discovers and invokes at run time | MDA reads a per-run connection directly and sends it only to that server |

The backend combines instance, workspace, user, and (when applicable) managed-gateway sources in precedence order; a later connection with the same name replaces an earlier one. It only includes enabled connections that offer tools. Backend-held discovery uses a 10-minute fresh cache and may serve cached data up to 24 hours while refreshing; managed 404/method-not-found failures are briefly backed off. Tool names are made deterministic and safe (`mcp_<connection>_<tool>`, with a hash when necessary), and invocation rechecks authorization, connection identity/revision-relevant fields, enabled status, and the allowed-tool list. A changed scope, URL, transport, or allowlist requires a new run rather than silently using changed authority.

A connection-backed server cannot be discovered by the backend because only MDA may read its connection. Instead, the backend validates its declared allowlist and emits `McpServerSpec`; MDA loads those tools once per run after ordinary tool specs are ready. It reads each connection individually, so an authorization-required connection can prompt for that connection without preventing other servers from being listed. It lists the remote tools concurrently, retains only catalog-allowed names, omits tools with a `runtime` argument, resolves output-name collisions by skipping later tools, and offers the resulting tools alongside operations. MCP tools may be direct model tools or code-only according to the catalog, but MDA-connected tools are not searched by the backend's `search_tools` facility.

For every connection-backed call, MDA reads the credential again, builds a fresh FastMCP client, and invokes the selected definition with the caller's arguments. Server-reported tool errors become tool error messages; connection, transport, or unexpected call failures are logged without request material and return a generic connection-and-credentials message. LangChain-reserved argument names are forwarded by calling the remote coroutine directly; `runtime` itself is rejected at discovery because it is reserved for the local tool runtime.

### MCP security invariants

MCP records accept HTTPS URLs only: no URL credentials, fragments, or secret-looking query parameters. Both backend and MDA MCP transports disable environment proxy trust and redirects, require every request to remain on the configured HTTPS origin, resolve the host to public addresses, and pin the connection to a checked address while preserving SNI and `Host`. This prevents redirects, private-address SSRF, and DNS rebinding from receiving credentials.

Connection-backed MCP configuration may name only an MDA connection made for MCP servers, and it cannot also retain backend headers, OAuth, or encrypted secret fields. The backend only emits a runtime server when the connection owner matches the source tier: deployment-owned for instance/workspace sources and person-owned for user sources. A personal server is excluded unless the run actually acts for and is attributed to that person; service-principal runs therefore cannot grant or consume a person's connection. These checks preserve the broader invariant that credentialed integrations are granted per run and credentials are not embedded in general model-visible context.

## Operations and focused tests

When changing this area, keep the MDA/backend ownership boundary explicit: lifecycle APIs reconnect, bind, guard, or report an MDA sandbox; they must not become a second sandbox creator. Preserve the proxy writer's replacement reset and scope narrowing, and treat an unreachable workspace differently from a confirmed missing MDA binding.

Focused coverage includes MDA MCP tests for public-origin enforcement, absence of a personal principal, forbidden agent credentials, and reserved LangChain argument forwarding. Sandbox tests cover handover eligibility and shared-worker refusal, proxy payload preservation/retry behavior, replacement/recovery, provider configuration, bridge behavior, and state reconnection. These tests are useful regression targets when modifying lifecycle, proxy, bridge, or tool-routing behavior.
