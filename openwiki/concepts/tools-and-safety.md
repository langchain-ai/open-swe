---
type: runtime tool-surface and safeguards
title: Tool Surfaces, Sandbox Access, and Safety Guards
description: How Open SWE plans the tools available to an individual run, transports them to Managed Deep Agents, and applies scoped access, sandbox routing, and targeted delivery guards.
tags: [tools, managed-runtime, sandbox, mcp, safety, authorization]
verified:
  - by: openwiki/0.4.2
    at: 2026-10-24T08:12:50.219Z
sources:
  - id: openwiki-source-345a31e6397cebf3f169e57e
    resource: repo://mda/rt/file_tools.py
  - id: openwiki-source-fb24ffc1656ce0258fa2ac33
    resource: repo://openswe/agent_api/graph.py
  - id: openwiki-source-ce4c4e555faede99a99ef43a
    resource: repo://openswe/agent_api/shell_guards.py
  - id: openwiki-source-0519ca95c6cd2fcdcab520d6
    resource: repo://openswe/middleware/client_tools.py
  - id: openwiki-source-75a672d9a8b6d6c500b1cf8d
    resource: repo://openswe/middleware/dynamic_tools.py
  - id: openwiki-source-1c036e99d40740776a65df4f
    resource: repo://openswe/middleware/exclude_tools.py
  - id: openwiki-source-8028ebab3ac3beac1691bf84
    resource: repo://openswe/middleware/pr_creation_guard.py
  - id: openwiki-source-d618115330c9c5a6ad6a6eec
    resource: repo://openswe/middleware/workflow_push_guard.py
  - id: openwiki-source-cc9c77c1e30f40e9c07d39bc
    resource: repo://openswe/sandboxes/tool_access.py
  - id: openwiki-source-c369ed8dca93cdbaf02cdbb8
    resource: repo://openswe/sandboxes/tool_runtime.py
  - id: openwiki-source-919e16feae379651f2cbc1c9
    resource: repo://openswe/server.py
  - id: openwiki-source-84653ff47cf43d028d6e5454
    resource: repo://openswe/tools/search_tools.py
  - id: openwiki-source-a7a923eb42c2ccc6f4c875de
    resource: repo://tests/agent/test_agent_assembly_context.py
  - id: openwiki-source-e183a9ef71582b9a5e0d7634
    resource: repo://tests/agent/test_workflow_push_guard.py
  - id: openwiki-source-9bdb0b2eb3f539b9ff213426
    resource: repo://tests/github/test_pr_creation_guard.py
generated: { by: "openwiki/0.4.2", at: "2026-10-24T08:12:50.219Z" }
---

An Open SWE run does not receive a global, static tool list. Its graph factory constructs a **ToolSurface** for the thread: server operations, middleware-provided tools, optional integrations, models, subagents, file routes, skills, checks, and shell guards. Managed Deep Agents (MDA) owns the model loop and invokes the catalog it receives; the Open SWE backend retains the authority to plan the surface and execute its operations. This is an important boundary for changing tools: add a capability to the planner and runtime surface, not merely to a prompt or an export list.

```mermaid
sequenceDiagram
    participant Planner as Open SWE planner
    participant Surface as ToolSurface
    participant Runtime as Agent API runtime
    participant MDA as Managed Deep Agents
    participant Box as Thread sandbox
    Planner->>Surface: scope tools files skills guards
    Surface->>Runtime: prepare catalog and tool registry
    Runtime->>MDA: catalog models sandbox and checks
    MDA->>Runtime: operation or shell check
    Runtime->>Box: invoke allowed operation
    Box-->>MDA: tool result
```

This shows the run-specific planning and execution path. MDA runs the loop, while the backend serves operations and policy decisions.

## What the surface contains

`ToolSurface.prepare()` normalizes the factory's base tools and middleware tools into named `BaseTool` instances, optionally resolves integration tools, and removes excluded names. It can describe or BM25-search the resulting registry, which is how `search_tools` discovers only tools available in the current run. MCP provenance is included in a described tool when the tool supplies it.

The primary planner starts from a broad set of built-in operations—such as web, repository, thread, Slack, review, task, and sandbox-related operations—but filters them through `resolve_access`, source and Slack context, feature/configuration gates, incident and stop-summary modes, and administrator status. This means `openswe.tools` is a lazy public-export registry, **not** a statement that every exported tool is available to every run.

The catalog sent to MDA separates several forms of availability:

- **Operations** are backend tools with schemas and declared injected reads. MDA invokes them through the typed runtime API; `RunSession.operation()` rejects names outside the prepared surface and unexpected arguments before invoking the tool in run context.
- **MDA-native file and shell tools** (`ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, and `execute`, with runtime-specific additions such as `delete`) are supplied by MDA's filesystem facility. The catalog carries file routes, artifact root, read checks, and whether shell checks are active.
- **Code-mode tools** are separately classified. Some operations are schema-visible and callable from code, while others are code-only so their schemas stay off the model's ordinary tool list. File/shell calls used from code are wrapped through the same file-tool middleware, rather than bypassing its checks.
- **Subagents** receive either the parent set or an explicitly listed subset. The general-purpose subagent's planned list omits parent-only capabilities such as Slack, thread, background, and personal-settings tools.
- **Client tools** are schemas supplied by the Responses client. The server replaces a same-named server tool with the client-owned one. Calling such a tool yields a placeholder result and ends the run before another model call; the client later supplies the real result under that placeholder message identifier.

## Visibility is both a catalog decision and a model-call decision

A tool can be registered yet hidden from the model. `ExcludeToolsMiddleware` contributes hidden names to every model call, while the catalog records exclusions so MDA does not offer them. The runtime also subtracts excluded tools from code mode. This preserves in-process behavior for a direct invocation of a registered hidden tool, but it is not an authorization mechanism by itself: the surface and operation endpoint remain the authoritative membership checks.

Run planning makes visibility sensitive to thread and caller context. For example, a public thread omits personal skill routes and personal settings/skill tools; unknown credential scope omits MCP loading; private-thread visibility affects channel-reading availability; and administrative operations require an eligible private admin surface. The focused assembly tests exercise these differences, including the absence of personal tools on public or unauthorized runs and the parent/subagent distinction.

## MCP integrations: discover first, load deliberately

Connected MCP tools are grouped behind `DynamicToolMiddleware`. Up front, the model receives `load_integration_tools` and a name catalog, rather than every integration schema. A successful request validates names, lazily builds the requested group (which may entail an MCP handshake or credential round trip), records `loaded_integration_tools` in agent state, and makes the schemas available on a subsequent call. An attempted call before loading receives an error; a load/build failure reports the missing tools and tells the agent to continue without them. Resolution is cached per group and protected by an async lock.

The surface nevertheless resolves integration tools when constructing the MDA operation catalog, so MDA can route operation calls, while `load_integration_tools` itself is excluded from that catalog. On providers that support mid-conversation tool additions, additions are anchored after the corresponding load result to retain prompt-cache position; other providers receive the loaded schemas in the normal tools list.

MCP access is deliberately fail-closed at planning time: the primary planner resolves the private credential identity first, and if that resolution fails it does not start MCP loading. Personal integrations are therefore not selected from unverified thread-provided identity.

## Files, skills, and sandbox ownership

A surface names a composite file backend for the thread. Sandbox files are the default; backend-served routes cover items such as offloaded blobs. Skill routes are distinct, read-only sources: bundled and organization skills are always planned, and personal skills are added only when a credential identity exists. The agent API lists each source's skill directories and `SKILL.md` files for MDA, then the MDA runtime serves reads under skill paths from its retained skill mapping. Writes to those paths return `Skills are read-only here`; skill sources are intentionally not backend paths.

`FileToolsMiddleware` selects the execution location per call: a skill-path call is served from that retained mapping; a backend-path call in an MDA-owned sandbox uses the backend route; otherwise the native file/shell tool runs in the thread's sandbox or remote-files backend. Every file/shell call is reported to the transcript. Configured checks reject a file masquerading as an image and load applicable ancestor `AGENTS.md` instructions when a file is read. For sandbox calls, retryable connection failures are retried with bounded backoff; an `execute` call that exceeds its requested/default timeout plus 30 seconds returns an error rather than waiting indefinitely.

MDA creates the managed sandbox before preparation, and the run binds that sandbox ID to the thread. If MDA replaces it, the backend reconnects the thread; reviewer sandboxes reconnect with a read-only GitHub proxy permission set. Bridged desktop/CLI threads are different: the backend routes file operations to the bridged thread files where appropriate, and some MDA-owned-sandbox features (for example signed sandbox downloads) are unavailable.

### Sandbox callback capability and credentials

A sandbox may call a narrow dashboard tools endpoint using a signed, sandbox-specific capability rather than receiving a reusable backend credential. The capability contains the host thread ID and sandbox ID, is audience-bound, and is accepted only when its JWT verifies and the thread still names that sandbox ID. For proxy-based sandboxes, the token becomes an opaque injected header and is accompanied by an allowlisted host rule; an optional OpenAI-compatible proxy uses a placeholder API key. For non-LangSmith sandboxes, provisioning writes the tokenized URL to `/tmp/open-swe-tools-url` under `umask 077` via an atomic rename. These mechanics contain the callback credential to the associated sandbox/thread, but do not turn arbitrary code executing in that sandbox into a generally trusted backend principal.

## Delivery guards: targeted controls, not a general write boundary

The shell-guard chain is planned for `execute`, `background_execute`, and `monitor` command/filter arguments. Backend operations run the chain before invocation. For MDA-native `execute`, the file middleware only sends commands that look relevant—containing `push`, `gh`, or `pulls`, or deeply nested shells—to the backend `check_shell` endpoint; the backend can refuse or replace the command, and the refusal is returned and transcribed as a tool error.

Two guards are installed on the main agent surface:

1. **Pull-request creation guard.** Shell fallback creation is blocked so that PRs must use `open_pull_request`, preserving attribution to the triggering user. It tokenizes shell input, recursively examines nested `bash`/`sh`/`zsh`/`dash` commands to a bounded depth, and rejects `gh pr create`, POST/body writes to GitHub's `/pulls` API, and comparable `curl` requests. This covers `execute`, `background_execute`, and monitor filters.
2. **Workflow-push guard.** A standalone inspectable `git push` is examined against the thread sandbox for changed `.github/workflows/` files. If workflow changes are present, it fingerprints the exact inspected diff, stores a pending approval, optionally notifies the Slack thread, and refuses execution until the thread owner approves that fingerprint. After approval it replaces the command with a fixed SHA/refspec command. If a lease-protected push cannot be safely inspected it is refused; chained pushes that might carry workflow changes are also refused because the guard cannot present an exact standalone push for review.

Guard coverage must not be mistaken for a general security boundary. In particular, workflow approval detects and gates guarded, inspectable pushes that carry workflow changes; it does **not** approve or mediate every shell command, every API write, every Git push form, or arbitrary code already executing in a sandbox. The MDA-side relevance heuristic is intentionally broader than what a guard ultimately blocks, and nonmatching native shell calls do not reach `check_shell`. Maintain access control at tool planning and operation implementation layers; use these guards as focused delivery protections.

## Operational change guide

When introducing a tool, decide first which graph factory owns it and which contexts may receive it. Add it to the planned base/middleware surface, reserve its name against integrations, set subagent and code-mode policy, and ensure its operation works through `RunSession.operation()` with state/config injection. If it executes shell, add its shell-bearing arguments to `SHELL_TOOL_ARGS` so configured shell guards see it. If it writes a delivery-sensitive resource, do not assume the workflow guard applies—implement a specific authorization or approval control.

Relevant regression points include `tests/agent/test_agent_assembly_context.py` for scope-sensitive assembly, `tests/middleware/test_dynamic_tools.py` for staged integration loading, `tests/github/test_pr_creation_guard.py` for nested and alternate PR fallbacks, `tests/agent/test_workflow_push_guard.py` for approval, rewriting, and uninspectable/chained-push failures, and `tests/sandbox/test_thread_tools.py` for sandbox capability authentication.

## Related pages

- [Managed runtime](/openwiki/architecture/managed-runtime.md)
- [Models and context](/openwiki/concepts/models-and-context.md)
- [Security and tenancy](/openwiki/concepts/security-and-tenancy.md)
- [GitHub and MCP integrations](/openwiki/integrations/github-and-mcp.md)
- [Coding and delivery workflow](/openwiki/workflows/coding-and-delivery.md)
