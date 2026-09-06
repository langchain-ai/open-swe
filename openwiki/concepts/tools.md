---
type: capability model
title: Tool capability model
description: How Open SWE composes Deep Agents primitives, curated tools, and integrations into graph-specific capability surfaces, and where sandbox, authorization, and plan-mode boundaries are enforced.
tags: [tools, agent, deepagents, integrations, sandbox, authorization, plan-mode, extension]
sources:
  - id: openwiki-source-63ebc853556c1b852ed80aff
    resource: repo://agent/analyzer.py
  - id: openwiki-source-921ec88ab63280d28b3dddb5
    resource: repo://agent/chat.py
  - id: openwiki-source-9103280889fa6c4d9c5bb0df
    resource: repo://agent/middleware/dynamic_tools.py
  - id: openwiki-source-f26d060fb4408e89b50964a5
    resource: repo://agent/middleware/plan_mode.py
  - id: openwiki-source-276ab38291eb5741b4c2141c
    resource: repo://agent/reviewer.py
  - id: openwiki-source-856ade03ef31ac38e1347f7c
    resource: repo://agent/server.py
  - id: openwiki-source-e4901f6a09c372487ff11987
    resource: repo://agent/tool_loaders/corridor_mcp.py
  - id: openwiki-source-252c217caee95d761fdf9d4b
    resource: repo://agent/tool_loaders/currents.py
  - id: openwiki-source-6de9e7b7779ea6aada343f2a
    resource: repo://agent/tool_loaders/langsmith.py
  - id: openwiki-source-2cd7e2018ae35c5972204803
    resource: repo://agent/tool_loaders/notion_mcp.py
  - id: openwiki-source-a46a7cd7d143369055b05580
    resource: repo://agent/tools/__init__.py
  - id: openwiki-source-9bef6ead94fcf55bf6db8787
    resource: repo://agent/tools/admin_gate.py
  - id: openwiki-source-74fafd9666607114e1ad0431
    resource: repo://agent/tools/automations.py
  - id: openwiki-source-d9bf67d6a09bd54eb3e306cf
    resource: repo://agent/tools/background_execute.py
  - id: openwiki-source-400c7123b7a35e5547f18d86
    resource: repo://agent/tools/http_request.py
  - id: openwiki-source-dcf576fc340e5f1a2bc3f5f4
    resource: repo://agent/tools/read_user_settings.py
  - id: openwiki-source-8037e2358a2c4f9b2c722a11
    resource: repo://AGENTS.md
  - id: openwiki-source-fef236c0a2029fbda76955d6
    resource: repo://tests/agent/test_plan_mode.py
generated: { by: "openwiki/0.4.2", at: "2026-09-06T08:13:04.096Z" }
verified:
  - by: openwiki/0.4.2
    at: 2026-09-06T08:13:04.096Z
---

# Tool capability model

A tool export is not a grant of authority. Open SWE uses `deepagents.create_deep_agent` to compile several graphs, each with an intentionally selected tool surface and backend. Curated tools exist where repository shell work is insufficient—for example, dashboard state, a credentialed service, a user-visible workflow, or a server-enforced authorization check. Routine repository and GitHub work instead uses the sandbox filesystem, shell, and `gh` proxy.

## Capability sources and graph wiring

`agent.tools` is the curated catalog, not a universal capability set. Modules are flat below `agent/tools/`; the package maps public names to modules, imports an export on first access, caches it, and ensures an imported submodule cannot shadow an export of the same name. A module can provide multiple exported operations, such as both background-task aliases or the automation operations.

Every Deep Agents graph also receives its own built-ins: `read_file`, `write_file`, `edit_file`, `delete`, `ls`, `glob`, `grep`, `execute`, and `task`. `DEEP_AGENT_TOOL_NAMES` reserves those names against curated and integration collisions. The main graph normally removes `grep`; stop-summary mode additionally removes mutating filesystem/shell/delegation built-ins.

```mermaid
flowchart TD
    Catalog["Curated export catalog"]
    Builtins["Deep Agents built-ins"]
    Main["Main coding graph"]
    Reviewer["PR reviewer graph"]
    Analyzer["Review style analyzer"]
    Chat["Read only PR chat"]
    Dynamic["Integration middleware"]

    Catalog --> Main
    Catalog --> Reviewer
    Catalog --> Analyzer
    Catalog --> Chat
    Builtins --> Main
    Builtins --> Reviewer
    Builtins --> Analyzer
    Builtins --> Chat
    Dynamic --> Main
```

This shows that the catalog and built-ins are inputs to graph-specific capability surfaces; integration tools are only attached to the applicable main-agent turns.

### Main coding graph

`get_agent` starts or reconnects the thread sandbox, builds the selected tool lists, and compiles the main graph. Its normal `static_tools` cover web access; plan lifecycle; foreground/background execution; user instructions and skills; Linear; thread and notification workflows; PR creation/review; sandbox recovery; scheduling; and Slack. `read_user_settings` is a normal static tool. The graph backend is a composite: the thread sandbox is the default, while bundled, organization, and (when applicable) user skills are mounted read-only.

The factory changes that surface before graph construction:

- An `admin_thread` adds `ADMIN_TOOLS`: sandbox reset, automation administration, environment administration, and organization-skill administration. The factory checks both the flag and the currently triggering user's configured-admin identity; a thread marked admin cannot transfer that capability to a later non-admin speaker.
- Signed sandbox-download helpers (`output_iframe`, `create_sandbox_file_download_url`, and `create_sandbox_service_url`) exist only for non-desktop, non-stop-summary LangSmith sandbox runs.
- Desktop `local_run` is reduced to `http_request`, `fetch_url`, and `web_search`. Stop-summary starts with Slack read/reply only. In all modes, Slack tools are removed unless the source is `slack` or `schedule` and trusted channel and thread identifiers are present.
- The general-purpose subagent is a separately compiled graph. It receives the applicable static tools except `background_execute` and `background_task`, and explicitly removes Slack and other parent-context-dependent tools (`list_threads`, `get_thread`, `manage_thread`, notification, code-channel, and user-settings operations). It gets the same dynamic-tool middleware when present, because parent middleware does not automatically wrap it.

### Specialist graph surfaces

| Graph | Curated capability surface | Sandbox boundary |
| --- | --- | --- |
| Main (`get_agent`) | Context-conditioned static tools, optional integration groups, and Deep Agents built-ins. | Thread sandbox, with read-only skill routes. |
| Reviewer (`get_reviewer_agent`) | `fetch_review_diff`, finding lifecycle operations, `web_search`, `fetch_url`, and `http_request`. `open_pull_request` is deliberately absent. | Reviewer sandbox. |
| Analyzer (`get_analyzer`) | `save_review_style_prompt` and `read_finding_outcomes` only. | Sandbox plus a state-backed skills route. |
| PR chat (`get_chat_agent`) | `read_repo_file`, `search_repo_code`, `list_review_findings`, `web_search`, and `fetch_url`. | No sandbox; read-only PR virtual files and GitHub API access. |

PR chat is the strongest isolation example. The dashboard supplies PR overview, diff, and findings as `/pr/` files. The parent hides `execute`, `write_file`, `edit_file`, and `delete`, while its replacement subagent allowlists only `read_file`, `ls`, `glob`, and `grep`. `PrepareChatRunMiddleware` obtains a repository-scoped GitHub App token, rather than passing a user credential to the GitHub-backed read tools.

## Dynamic integration tools

`DynamicToolMiddleware` separates discovery from schema exposure. It presents a single `load_integration_tools` tool whose description lists names and integration groups, but not each integration schema. The model must load exact names (or `Group:name` / `Group: name` aliases), then call the newly exposed tool on the following model turn. Loaded names are held in `loaded_integration_tools` state, reset at the start of each agent run; the middleware adds the resolved schemas only to subsequent model requests and routes calls to the resolved tool.

```mermaid
sequenceDiagram
    participant Model
    participant Dynamic as Dynamic middleware
    participant Loader as Group loader
    Model->>Dynamic: load_integration_tools named tools
    Dynamic->>Loader: build requested groups
    Loader-->>Dynamic: resolved tools or failure
    Dynamic-->>Model: state update and next turn instruction
    Model->>Dynamic: call loaded integration tool
    Dynamic->>Dynamic: route to resolved tool
```

This shows the required two-turn loading protocol for an integration capability.

The middleware validates its catalog at construction: names cannot duplicate each other, `load_integration_tools`, static names, or Deep Agents names. Per-group locks and a resolved cache prevent concurrent or repeated construction. A direct call before loading, an unknown catalog name, or an unavailable tool produces an error `ToolMessage`; load failures are logged and become “continue without it” errors rather than terminating the agent run.

The main graph offers Observability, Currents, Notion, and—when configured—Browser groups. Tool definitions for the first three are discovered by bounded, cached loaders during eligible main-graph construction, then their schemas remain deferred by the middleware. Corridor differs: it advertises a static allowlist (`analyzePlan`) and defers the MCP handshake itself until requested. Local and stop-summary runs skip these groups.

Integration credentials remain server-side. Currents tools resolve an API key for a named, verified thread participant. LangSmith tools similarly resolve a participant's credentials per call and are read-only; only an observability-authorized triggering user gets team Datadog/LangSmith tools, while an allowed organization member may get the appropriate LangSmith surface. Notion's wrapper requires `on_behalf_of`, resolves that participant, and refreshes the participant's MCP tool/token at invocation. Corridor reads its deployment token from environment configuration and validates its hosted MCP URL. These mechanisms prevent the sandbox from receiving the corresponding service secrets.

## Tool safety and authorization boundaries

Tool selection reduces accidental reachability, but sensitive actions must also validate at invocation time. For example, `read_user_settings` accepts no caller-supplied user, thread, or source identity: it resolves verified thread participants from runtime configuration and returns a limited profile subset, instructions, connection status, and an unresolved-participant count—not tokens or credentials.

Automation operations illustrate defense in depth. They are attached only through `ADMIN_TOOLS`, re-check admin status with the runtime login/email, and return structured errors instead of propagating dashboard-service exceptions. Creation derives the identity from trusted runtime configuration; updates preserve omitted values and reject conflicting clear/set arguments; triggers can start a paused automation. The server also excludes their mutating operations in plan mode.

`http_request` is intentionally powerful enough to be a plan-mode-excluded external-write boundary: callers can select an HTTP method and body. It follows the safe-redirect helper, reports HTTP failures as structured results, and offloads serialized responses above 100,000 characters to sandbox JSONL. Its contract directs GitHub API work to sandbox `gh`, where GitHub authentication is handled by the sandbox proxy. Background commands also belong to the sandbox boundary; their runner limits active tasks to four, caps captured output at 1 MiB, and stores task state under `TASK_ROOT`.

## Plan mode is a stateful capability filter

Plan mode is not just prompt guidance. `PlanModeMiddleware` is installed unconditionally on the main graph and filters every model request according to the run state's `plan_mode`. Before the agent runs it resets that state to the initial value resolved for this run, avoiding stale persisted state; a successful mid-run `enter_plan_mode` command changes the next model turn.

`PLAN_MODE_EXCLUDED_TOOLS` removes delegation and background work, browser actions, external HTTP, service URL creation, PR/review and thread mutation, baby-sit, sandbox reset/recreation, user-skill, Slack, mutating Linear, environment, and automation operations. `task` is excluded specifically because its subagent has an independently compiled surface. `approve_plan`, read-only thread lookup, and file/shell built-ins remain available so the agent can create a plan artifact; the no-repository-mutation restriction on `write_file`, `edit_file`, and `execute` is prompt-enforced discipline, not a full technical sandbox policy.

## Safe extension contract

To introduce a capability without accidentally granting it everywhere:

1. Add an async implementation under `agent/tools/`.
2. Export it explicitly from `agent/tools/__init__.py`; the lazy export makes it importable but does **not** expose it to a graph.
3. Wire it explicitly into the applicable factory—normally `agent/server.py`, or `agent/reviewer.py` for review-only behavior—and decide whether a subagent should receive it.
4. Review the authorization and execution boundary: trusted runtime identity, participant checks, admin recheck, credentials staying server-side, sandbox requirements, source context, plan-mode exclusion, and collisions with built-in or dynamic names.
5. For a credentialed/large schema integration, use an `IntegrationGroup` and test load-before-call, collisions, unavailable loading, and repeated/concurrent resolution. Otherwise add focused behavior tests at the graph and tool boundary.

The repository's guidance is explicit: tools must be added under `agent/tools`, exported, wired into the relevant graph, and reviewed for authorization rather than accumulated as unused catalog entries.

## Related pages

- [Agent graph](../architecture/agent-graph.md) — factories, graph lifecycle, and backend composition.
- [Middleware stack](../architecture/middleware-stack.md) — ordering and cross-cutting enforcement.
- [Authorization and security](auth-and-security.md) — identity and secret-handling boundaries.
- [Observability and MCP](../integrations/observability-and-mcp.md) — integration configuration and operations.
- [PR creation](../workflows/pr-creation.md) — PR workflow behavior.
