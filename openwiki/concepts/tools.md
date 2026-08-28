---
type: concept
title: Agent Tools (Curated Toolset)
description: Authoritative map of Open SWE's curated tools, Deep Agents built-ins, graph-specific tool surfaces, dynamic integrations, and tool-level safety boundaries.
tags: [tools, agent, reviewer, analyzer, deepagents, integrations, plan-mode, authorization]
verified:
  - by: openwiki/0.4.2
    at: 2026-08-28T11:53:01.759Z
sources:
  - id: openwiki-source-63ebc853556c1b852ed80aff
    resource: repo://agent/analyzer.py
  - id: openwiki-source-921ec88ab63280d28b3dddb5
    resource: repo://agent/chat.py
  - id: openwiki-source-049148e9c970ff263c957b04
    resource: repo://agent/dashboard/review_chat_api.py
  - id: openwiki-source-9103280889fa6c4d9c5bb0df
    resource: repo://agent/middleware/dynamic_tools.py
  - id: openwiki-source-276ab38291eb5741b4c2141c
    resource: repo://agent/reviewer.py
  - id: openwiki-source-856ade03ef31ac38e1347f7c
    resource: repo://agent/server.py
  - id: openwiki-source-a46a7cd7d143369055b05580
    resource: repo://agent/tools/__init__.py
  - id: openwiki-source-d8298c1a08304a86bd1da991
    resource: repo://agent/tools/approve_plan.py
  - id: openwiki-source-d9bf67d6a09bd54eb3e306cf
    resource: repo://agent/tools/background_execute.py
  - id: openwiki-source-e89cf8ceb9792c1cbeb7569e
    resource: repo://agent/tools/enter_plan_mode.py
  - id: openwiki-source-b5e769b43720e69b4e5eab75
    resource: repo://agent/tools/environments.py
  - id: openwiki-source-69330a855dafd7cace0820b0
    resource: repo://agent/tools/fetch_review_diff.py
  - id: openwiki-source-400c7123b7a35e5547f18d86
    resource: repo://agent/tools/http_request.py
  - id: openwiki-source-c3b12b5693b6aa5458b6b53a
    resource: repo://agent/tools/manage_baby_sit.py
  - id: openwiki-source-ba666a428b107356ed2aa395
    resource: repo://agent/tools/manage_code_channel.py
  - id: openwiki-source-d9f2a513cf28971a9676bf89
    resource: repo://agent/tools/open_pull_request.py
  - id: openwiki-source-2381c11d698eab667b973058
    resource: repo://agent/tools/read_repo_file.py
  - id: openwiki-source-9a9aaf4b265831fa9c7e3bd2
    resource: repo://agent/tools/schedule_thread_wakeup.py
  - id: openwiki-source-c631f720f8d212e6d3b82c53
    resource: repo://agent/tools/search_repo_code.py
  - id: openwiki-source-f04e398bd4e627fb9faff701
    resource: repo://agent/tools/threads.py
  - id: openwiki-source-8037e2358a2c4f9b2c722a11
    resource: repo://AGENTS.md
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
  - id: openwiki-source-fef236c0a2029fbda76955d6
    resource: repo://tests/agent/test_plan_mode.py
  - id: openwiki-source-3b6e8359d52d6e7ed5a50ef0
    resource: repo://tests/tools/test_background_execute.py
  - id: openwiki-source-66160b6a3ab0caa3aa64bf3e
    resource: repo://tests/tools/test_http_security.py
  - id: openwiki-source-749071bb736ba933e244501a
    resource: repo://tests/tools/test_manage_baby_sit.py
  - id: openwiki-source-7416596e0d9fc9b802355ff6
    resource: repo://tests/tools/test_schedule_thread_wakeup.py
  - id: openwiki-source-432efb2a605cb424bc404a25
    resource: repo://tests/tools/test_threads.py
generated: { by: "openwiki/0.4.2", at: "2026-08-28T11:53:01.759Z" }
---

# Agent Tools (Curated Toolset)

Open SWE deliberately keeps its first-class tool surface small. Repository work normally uses `gh` and shell commands in the sandbox, rather than adding a specialized tool for every GitHub action; similarly, code search normally uses shell tooling. Curated tools exist where a capability needs application state, dashboard parity, an integration boundary, or safety and authorization logic that shell access cannot provide.

## Tool package and composition model

Curated tools are flat modules in `agent/tools/`. `agent.tools` is a lazy export facade: `_TOOL_MODULES` maps public names to modules; `_load_export` imports and caches a tool only when it is accessed; and `_LazyToolsModule` ensures a same-named imported submodule does not shadow the public function. A module may therefore provide several exports, such as `background_execute` and `background_task`, or the four environment-management operations.

A graph factory passes curated tools to `deepagents.create_deep_agent`. Deep Agents separately supplies its filesystem, shell, and delegation capabilities: `read_file`, `write_file`, `edit_file`, `delete`, `ls`, `glob`, `grep`, `execute`, and `task`. `DEEP_AGENT_TOOL_NAMES` reserves these names, so a dynamic or curated tool must not collide with them. The main agent hides `grep`; stop-summary mode also hides the mutating built-ins `delete`, `edit_file`, `execute`, `task`, and `write_file`.

```mermaid
flowchart TD
    Package["agent/tools lazy curated exports"]
    Builtins["Deep Agents built-ins"]
    Main["Main coding graph"]
    Reviewer["PR reviewer graph"]
    Analyzer["Review-style analyzer graph"]
    Chat["PR chat graph"]
    Static["Static curated tools"]
    Dynamic["On-demand integration tools"]

    Package --> Static
    Static --> Main
    Dynamic --> Main
    Package --> Reviewer
    Package --> Analyzer
    Package --> Chat
    Builtins --> Main
    Builtins --> Reviewer
    Builtins --> Analyzer
    Builtins --> Chat
```

Curated exports are selected by each graph factory, while `create_deep_agent` adds its own built-in capabilities to every executing graph.

## Main coding agent: static and conditional tools

`agent.server:get_agent` builds the main graph's `static_tools` list. It includes:

- **Web and research:** `http_request`, `fetch_url`, and `web_search`.
- **Planning and personal configuration:** `enter_plan_mode`, `save_plan`, `approve_plan`, `save_user_instructions`, `save_user_skill`, and `delete_user_skill`.
- **Sandbox and work tracking:** `background_execute`, `background_task`, `recreate_sandbox`, `schedule_thread_wakeup`, and `report_platform_issue`.
- **Project systems and threads:** the Linear tools; `list_threads`, `get_thread`, `manage_thread`; `manage_baby_sit`; `notify_automation_channel`; `open_pull_request`; and `request_pr_review`.
- **Slack:** `manage_code_channel` and the Slack message, reaction, attachment, and move tools.

The factory then changes this surface for the run context. An admin thread adds `sandbox_reset`, environment administration (`list_environments`, `save_environment`, `capture_environment_snapshot`, and `delete_environment`), and organization-skill administration. Sandbox download support conditionally adds `output_iframe`, `create_sandbox_file_download_url`, and `create_sandbox_service_url`. Slack tools are removed when Slack is disabled. `local_run` is intentionally only the three web tools; `stop_summary` is only Slack thread read/reply before Slack disabling is applied.

The main graph also creates a general-purpose subagent. It receives the static tools except the two background-execution aliases. Parent-context-dependent tools are excluded from subagents, including Slack, thread management, `manage_code_channel`, `notify_automation_channel`, and user-settings access; the parent agent must relay those interactions.

### On-demand integration groups

When the run is neither local nor stop-summary, the server gathers browser tools plus Observability, Currents, and Notion integration tools. If configured, Corridor is represented by a static name catalog with a deferred MCP loader. These integration groups are mediated by `DynamicToolMiddleware`, not appended directly to the initial static list:

1. The model initially sees `load_integration_tools` and a catalog of group-qualified names.
2. It must request exact names with that loader.
3. The middleware builds the required group once, under a per-group lock, stores the successfully resolved tools, and records loaded names in graph state.
4. Later model calls receive only the requested resolved tools. Calling an integration tool before loading it, or requesting one unavailable after loading, returns an error tool message and tells the agent to continue without it.

The middleware rejects duplicate names across groups and collisions with the built-in/static reserved names. This defers expensive MCP handshakes and credential reads until a tool is actually wanted, rather than putting them on the first model-call critical path.

## Specialist graphs

The factories deliberately give specialist graphs different curated surfaces; their Deep Agents built-ins still come from `create_deep_agent` unless middleware removes them.

| Graph | Curated tools and operational boundary |
| --- | --- |
| Main (`get_agent`) | The configurable surface described above, plus Deep Agents built-ins and optional integrations. |
| Reviewer (`get_reviewer_agent`) | `fetch_review_diff`, finding lifecycle tools (`add_finding`, `update_finding`, `list_findings`, `publish_review`, `resolve_finding_thread`, `reply_to_finding_thread`), and `web_search`, `fetch_url`, `http_request`. It does not wire `open_pull_request` as a curated tool, but it is still a Deep Agents graph with a reviewer sandbox; do not equate the curated list alone with a complete capability/security boundary. |
| Analyzer (`get_analyzer`) | `save_review_style_prompt` and `read_finding_outcomes`, used to produce/refine a per-repository review-style prompt. |
| PR chat (`get_chat_agent`) | `read_repo_file`, `search_repo_code`, `list_review_findings`, `web_search`, and `fetch_url`. It has no sandbox backend; middleware excludes `execute`, `write_file`, `edit_file`, and `delete`, and its delegated subagent allowlists only `read_file`, `ls`, `glob`, and `grep`. |

The dashboard review-chat backend mints private chat threads scoped to a viewer, repository, and PR. On the first run it seeds virtual `/pr/overview.md`, `/pr/diff.patch`, and `/pr/findings.md` files (truncating a diff at 400,000 characters). It validates this ownership/scope on every proxied thread access. `PrepareChatRunMiddleware` acquires a GitHub App installation token and supplies it as `chat_github_token`; the chat read tools use that token with the Contents and code-search APIs. `read_repo_file` defaults to the PR head and caps returned file bytes; `search_repo_code` is explicitly default-branch indexed, so it locates symbols but cannot search an arbitrary PR ref.

## High-value tool behaviors

- **Outbound HTTP:** `http_request` allows arbitrary methods but uses safe redirect handling. It treats returned web data as untrusted and offloads a serialized response larger than 100,000 characters to sandbox JSONL, returning a path and size instead of inlining it. Its contract tells agents to use sandbox `gh`, not this tool, for GitHub API calls.
- **Background commands:** `background_execute`/`background_task` launch non-blocking shell work tracked below `/tmp/open-swe-background-tasks`. The runner bounds active tasks to four, output to 1 MiB, and timeout to at most 24 hours; task state and output are durable sandbox files with a one-week TTL.
- **PR opening:** `open_pull_request` is for a new already-pushed branch. For dashboard, Slack, and Linear triggers it prefers the triggering user's OAuth token so the PR is attributed to that user, falling back to the GitHub App token. It returns an existing branch PR rather than duplicating it; ordinary PR updates remain `gh` work.
- **Review diff:** `fetch_review_diff` materializes the selected review range in the reviewer sandbox and returns a path plus bounded changed-file metadata. The reviewer can inspect the local artifact using built-ins rather than placing the full diff in a tool result.
- **Wakeups and CI watching:** `schedule_thread_wakeup` creates a one-shot LangGraph cron after 1–1,440 minutes, persists a generation/count budget, and refuses more than ten system wakeups between human messages. `manage_baby_sit` starts/stops a durable PR watch or records a flaky retry; it verifies a canonical PR URL, executable thread, repository match, open PR/head information, and GitHub App installation before starting a watch.

## Parent-only authorization and dashboard parity

`list_threads`, `get_thread`, and `manage_thread` implement dashboard-equivalent operations, not trust in caller-provided thread metadata. They derive an actor from trusted run configuration (with the current state identity able to supersede it), enforce allowed-organization membership, then call dashboard operations that apply owner/participant/admin rules. Cross-user listing and `admin_cancel` require an admin; destructive deletion requires `confirm=true`. These tools are also excluded from the general-purpose subagent because they depend on parent source context.

This is an example of the agent/UI parity principle: a dashboard feature should normally have a curated agent interface, but it must preserve the same authorization and confirmation boundaries rather than exposing a raw backend primitive.

## Plan-mode safety and lifecycle

Plan mode is stateful. `enter_plan_mode` persists `planning` status when it has a thread ID and returns a LangGraph `Command` setting `plan_mode=True`; `approve_plan` requires active mode and a non-shared plan, persists approval with approver identity, and returns a command setting it false. `PlanModeMiddleware` is always installed and reads state, so it applies when the tool turns plan mode on mid-run as well as when a later run starts with configuration carrying the flag.

`PLAN_MODE_EXCLUDED_TOOLS` removes delegation and explicit side effects: `task`, background execution, `create_sandbox_service_url`, `http_request`, baby-sit, thread mutation, PR opening/review request, sandbox reset/recreation, user-skill mutation, Slack moves/new threads, mutating Linear actions, and environment mutations. Read-only thread lookup remains available, as do `approve_plan`, `read_file`, `write_file`, `edit_file`, and `execute`.

That final distinction is important: plan mode's no-repository-mutation rule for shell and file built-ins is **prompt discipline**, not a complete technical prohibition. `task` is removed because its separately compiled subagent would otherwise bypass the parent exclusion. Safety-sensitive changes must preserve both the middleware exclusion list and the plan-mode prompt guidance.

## Adding or changing a tool

1. Add an async implementation under `agent/tools/`.
2. Add its module mapping, public export, and type-checking import in `agent/tools/__init__.py`.
3. Wire it into the appropriate graph factory—usually `get_agent`, but reviewer, analyzer, and chat have intentionally separate surfaces.
4. Decide whether it is static, conditionally exposed, parent-only, plan-mode-excluded, or an on-demand integration. Reserve names to avoid collisions with Deep Agents and existing tools.
5. Add focused tests. The existing suite exercises HTTP redirect safety, background task limits/lifecycle, wakeup scheduling/budgets, thread authorization/actions, baby-sit validation, reviewer findings/diff behavior, PR chat ownership and read access, plan-mode transitions/exclusions, and factory tool-loading concurrency.

## Related pages

- [Agent graph](../architecture/agent-graph.md) — graph factories and runtime assembly.
- [Middleware stack](../architecture/middleware-stack.md) — enforcement order and graph middleware.
- [Authorization and security](auth-and-security.md) — user and organization authorization boundaries.
- [Observability and MCP](../integrations/observability-and-mcp.md) — integration configuration.
- [PR creation](../workflows/pr-creation.md), [PR review](../workflows/pr-review.md), and [scheduling and baby-sit](../workflows/scheduling-and-baby-sit.md) — user-facing workflows.
- [Testing overview](../testing/overview.md) — test-suite organization.
