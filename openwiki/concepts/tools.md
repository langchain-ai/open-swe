---
type: tool catalog and authorization model
title: Tools and Tool Access
description: How tools are defined, registered, dynamically loaded, and exposed to agents; tool categories (sandbox, integration, review, etc.), authorization policies, and mode-specific controls.
tags: [tools, agent, authorization, integrations, dynamic-tools, automation, reviewer, access-control, mcp]
sources:
  - id: openwiki-source-75a672d9a8b6d6c500b1cf8d
    resource: repo://openswe/middleware/dynamic_tools.py
  - id: openwiki-source-1c036e99d40740776a65df4f
    resource: repo://openswe/middleware/exclude_tools.py
  - id: openwiki-source-7a02c62499495310b04f21ef
    resource: repo://openswe/review/chat.py
  - id: openwiki-source-96bcad07b4fe7078402bc2b8
    resource: repo://openswe/reviewer.py
  - id: openwiki-source-cc9c77c1e30f40e9c07d39bc
    resource: repo://openswe/sandboxes/tool_access.py
  - id: openwiki-source-919e16feae379651f2cbc1c9
    resource: repo://openswe/server.py
  - id: openwiki-source-17ae624050e95666294107c5
    resource: repo://openswe/tools/__init__.py
  - id: openwiki-source-d23ea4120596965e811d7103
    resource: repo://openswe/tools/access.py
  - id: openwiki-source-b5fe0e0520028e80429116d3
    resource: repo://openswe/tools/admin_gate.py
  - id: openwiki-source-fd7a021556d7302cd1037647
    resource: repo://openswe/tools/automations.py
  - id: openwiki-source-1a477779016142ec6f3dc601
    resource: repo://openswe/tools/read_user_settings.py
generated: { by: "openwiki/0.4.2", at: "2026-10-21T13:36:13.083Z" }
verified:
  - by: openwiki/0.4.2
    at: 2026-10-21T13:36:13.083Z
---

# Tools and Tool Access

Open SWE does not treat the tools package as a universal capability grant. A tool must be exported from the curated catalog, deliberately wired into a particular graph, and—where appropriate—protected at its own boundary with access policies. This separation keeps credentials, administrative actions, and graph-specific operations out of tool surfaces that do not need them.

## Tool catalog structure

`openswe.tools` is the curated export facade. The module exports `_TOOL_MODULES`, a flat map of public tool names to their implementation modules. Lazy imports are cached so each access loads once; several public names can share a single implementation, such as all automation operations drawing from `.automations`.

Exporting a name from the catalog only makes it importable; each graph factory supplies its own list to `create_deep_agent`. This decouples availability from registration.

Deep Agents separately supplies filesystem and delegation tools: `read_file`, `write_file`, `edit_file`, `delete`, `ls`, `glob`, `grep`, `execute`, and `task`. `DEEP_AGENT_TOOL_NAMES` reserves these names, preventing static or dynamic integrations from colliding with them. The main graph hides `grep` by default; stop-summary mode additionally hides the mutating filesystem and shell tools.

## Tool visibility by graph

| Graph | Curated tools and intent |
| --- | --- |
| **Main agent** | Context-dependent static tools, eligible dynamic groups (MCPs, workspace skills), and Deep Agents built-ins. Resolves access policies and excludes tools based on run mode. |
| **Reviewer** | Finding operations (`add_finding`, `update_finding`, `list_findings`, `publish_review`, `resolve_finding_thread`, `reply_to_finding_thread`), `fetch_review_diff`, plus `web_search`, `fetch_url`, and `http_request`. Does not include PR creation or modification. |
| **PR chat** | `read_repo_file`, `search_repo_code`, `list_review_findings`, `web_search`, `fetch_url`, `propose_review_comment`, and `propose_pr_review`, with read-only GitHub-backed tools and virtual-file `/pr/` context seeded by the dashboard. Has **no sandbox** and excludes shell and write built-ins. |

## Main coding agent assembly

`openswe.server:get_agent` constructs the main agent's `static_tools` list with **required tools** including:

- **Web access** (`http_request`, `fetch_url`, `web_search`)
- **Planning** (`save_plan`)
- **Background execution** (`background_execute`, `background_task`)
- **User instructions and skills** (`save_user_instructions`, `save_user_skill`, `delete_user_skill`)
- **Thread operations** (`list_threads`, `get_thread`, `manage_thread`, optionally `start_thread`)
- **PR creation and operations** (`open_pull_request`, `link_pull_request`, `search_pull_requests`)
- **PR review workflow** (`expedite_pr_approval`, `merge_expedited_pr`, `request_pr_review`)
- **Human reviewer management** (`request_human_review`, `assign_human_reviewer`, `auto_assign_human_reviewer`, `dismiss_human_review_request`, `get_human_review_status`)
- **Notifications and feedback** (`submit_thread_feedback`, `submit_review_assessment_feedback`)
- **Sandbox recovery** (`recreate_sandbox`)
- **Scheduling** (`schedule_thread_wakeup`, `request_rollout_check`)
- **User-settings lookup** (`read_user_settings`) and optional mutation (`save_user_settings`)
- **Platform support** (`report_platform_issue`)
- **Slack tools** (reactions, thread moves, channel read/write, etc.)
- **Incident management** (`manage_incident`)
- **Code-channel management** (`manage_code_channel`, `code_channel_set_view`)
- **Event operations** (`listen_events`, `list_event_types`)
- **Optional Notion integrations** (`comment_on_notion_task`, design tools)
- **SQL query** (`read_only_sql`)
- **Sandbox downloads** (`output_iframe`, `create_sandbox_file_download_url`, `expose_port`, when enabled)
- **Design and review tools** (`propose_review_comment`, `propose_pr_review`)
- **Feature flags and review policy** (`manage_feature_flags`, `manage_review_approval_mode`, `manage_review_repos`)
- **Miscellaneous** (`manage_baby_sit`, `switch_to_performance_model`, optional `connect_managed_tools`)

Tool inclusion depends on **runtime context**:

- **Admin threads** receive `ADMIN_TOOLS`: automation management (`create_automation`, `update_automation`, `delete_automation`, `trigger_automation`, `list_automations`), workspace management (`list_workspaces`, `publish_workspace`, `refresh_workspace_start`, `configure_repository`, `delete_workspace`), and organization-skill operations (`save_organization_skill`, `delete_organization_skill`). The factory verifies the triggering identity is an admin before adding these.
- **Desktop `local_run`** collapses to only `http_request`, `fetch_url`, and `web_search`. Integration groups are not collected.
- **Stop-summary mode** initially gets only `slack_read_thread_messages` and `slack_reply`. Integration groups are not collected.
- **Slack operations** are conditionally removed unless trusted Slack context enables them. Channel-ask mode removes thread-bound operations while retaining write capabilities. Slack DM mode additionally excludes `slack_add_reaction`.
- **Personal user-settings tools** are removed when no credential login is verified.
- **Human review tools** are removed when Slack bot context is unavailable. Expedited review tools are removed when disabled or when Slack bot context is unavailable.
- **Incident-session tools** are added if an incident is in scope; automatic incident sweeps additionally exclude mutations.
- **Subagents** receive the applicable static list except `save_user_settings` and tools that require the parent agent context (Slack thread-bound operations, background operations, notion tasks, thread/event listeners, etc.). Separately compiled subagent graphs receive the parent's dynamic integration middleware but do not inherit parent middleware exclusions.

## Deferred integration tool loading

Eligible normal runs construct candidate groups for MCPs (workspace, instance, and personal-tier scoped) and workspace skills. `DynamicToolMiddleware` presents a single loader, `load_integration_tools`, with a catalog of group-qualified tool names rather than placing all schemas on the first model call.

This deferred loading works as follows:

1. The model calls `load_integration_tools` with a list of names from the catalog.
2. Middleware serializes loading per group (one lock per group) and caches both successful and failed resolutions.
3. A successful loader call updates run state so the requested schema becomes available on the next model turn.
4. Names must be unique across groups and must not collide with the loader, built-ins, or static tools.
5. Middleware returns tool errors rather than failing the run when loading fails.

Integration loading is a credential boundary: workspace-scoped MCP tools load from workspace, instance, and (if a user is logged in) personal tiers, with later tiers' connections replacing earlier ones. All tool invocations receive a fresh credential check at runtime.

### Tool addition protocol

Models that support in-conversation tool addition (Anthropic's `tool_addition` and OpenAI's `additional_tools`) receive loaded integration tools via those fields to preserve prompt cache. Models without this support receive tools in the standard tools list, which invalidates cache. Recognized models:

- **Anthropic** (`tool_addition`): `claude-opus-5`, `claude-fable-5`, `claude-opus-4-8`, `claude-mythos-5`, `claude-sonnet-5-5`
- **OpenAI Responses API** (`additional_tools`): `gpt-6-astra`, `gpt-6.1-sol`, `gpt-6-luna`

## Sandbox tool access

Sandboxed shell and file operations access tools through a capability-based bridge. When a sandbox is created, the system issues a JWT capability (`ToolAccess`) bearing the thread id and sandbox id, signed with `DASHBOARD_JWT_SECRET`. The sandbox receives a `$TOOLS_PORT` bridge URL and environment variables to call back into the dashboard's tool endpoints. Tool calls from the sandbox are authenticated by verifying the JWT, checking thread visibility, and confirming the sandbox is bound to that thread. This keeps secrets (credentials, long-lived tokens) out of the sandbox; only short-lived, bearer-token capabilities cross the boundary.

For desktop runs, tool URLs are written to `/tmp/open-swe-tools-url` by the provisioning step. For hosted LangSmith sandboxes, proxy rules directly wire the capability into the runtime environment.

## Tool-side authorization and safe responses

Graph wiring is a convenience and least-privilege measure, not the sole authorization control. The `@access` decorator implements independent tool-side authorization as a check against a `Policy` object specifying:

- **Where the tool may run**: `anywhere`, `private` (private thread only), `admin_thread`, or `admin_surface`
- **Who can use it**: `anyone`, `owner` (the sole writer in a private thread), or `admin`
- **Result projection for sole writers**: an optional `Projection` callable that redacts full results when the thread is shared

The decorator rechecks authorization at runtime, applies projection when appropriate, and returns a refused error if the current run's `Access` mode does not permit the tool.

`read_user_settings` derives verified participants from thread configuration and returns mapped profile settings, instructions, and redacted connection status metadata without tokens or credentials. It takes no caller-provided user, thread, or source identifier.

Automation operations repeat their authorization with `require_admin`, which checks the runtime identity or (for scheduled runs) whether a saved admin authorized the schedule. They wrap the dashboard schedule service and return structured `{ok: false, error: ...}` responses for authorization and service failures rather than propagating exceptions. Creation records the verified admin identity; update preserves omitted fields while rejecting simultaneous clear/set values for repository or Slack destination; triggering is allowed for paused automations.

## Mode-specific tool gating

Tool exclusion is applied per-mode using `ExcludeToolsMiddleware` to remove context-inappropriate tools after Deep Agents injects built-ins:

- **Stop-summary mode** removes filesystem mutation (`write_file`, `edit_file`, `delete`), shell execution (`execute`), delegation (`task`), and `grep`, leaving only Slack read/reply tools.
- **Slack channel-ask mode** removes thread-bound Slack operations (`slack_add_reaction`, `slack_attach_html`, `slack_move_thread`, `slack_start_review_channel`), incident management (`manage_code_channel`, `manage_incident`), and `grep`, but retains write capabilities. By-the-way mode additionally excludes `slack_breakout_thread`.
- **Slack DM mode** removes `slack_add_reaction` to avoid clutter on user messages.
- **Incident automatic mode** removes mutable operations (including `code_channel_set_view`, `manage_code_channel`, `manage_incident`, `slack_reply`, and many others) when an incident sweep runs without explicit user request, preventing unintended side effects.
- **PR chat** removes `execute`, `write_file`, `edit_file`, and `delete` to ensure read-only operation over GitHub-backed read tools.

## Safely extending a tool

1. Implement an async tool in `openswe/tools/`, map it in `_TOOL_MODULES`, and add its type-checking export in `__all__`.
2. Wire it only into the graph(s) that need it. Decide whether desktop, stop-summary, Slack, admin-thread, or subagent filtering applies.
3. Reserve the name against Deep Agents built-ins and static/dynamic integration names. For expensive or credentialed integrations, use an `IntegrationGroup` and make loading failure recoverable.
4. Declare access policies with `@access(Policy(...))` if the tool has sensitive side effects or must run in specific thread contexts. Derive actor and resource identity from trusted runtime context where possible. Keep secrets server-side and return redacted status/error data.
5. Add focused behavior tests: catalog/graph composition and mode filtering, authorization denial, credential/scope handling, success and failure results, and mode-specific exclusion for each new mutation.

## Related pages

- [Agent graph](../architecture/agent-graph.md) — graph factories and runtime assembly.
- [Reviewer and analyzer](../architecture/reviewer-and-analyzer.md) — reviewer graph and code review.
- [Authorization and security](auth-and-security.md) — trust boundaries and credentials.
- [MCP integration](../integrations/observability-and-mcp.md) — integration configuration.
