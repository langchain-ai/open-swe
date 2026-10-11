---
type: architecture overview
title: Runtime and Product Architecture
description: LangGraph deployment, graph entrypoints, FastAPI ingress, durable dispatch, sandbox ownership, and the dashboard and desktop product surfaces.
tags: [architecture, langgraph, fastapi, dashboard, runtime]
sources:
  - id: openwiki-source-b76f79b6cfae139d1784a43a
    resource: repo://langgraph.desktop.json
  - id: openwiki-source-5bbba7b2a8ea8360ff233d63
    resource: repo://langgraph.json
  - id: openwiki-source-4b1279a0a1e5ec2d55a4558a
    resource: repo://openswe/api/app.py
  - id: openwiki-source-70b814b26d317c2b15c4a4fb
    resource: repo://openswe/chat.py
  - id: openwiki-source-e4bce0ee35cec33ca72293f7
    resource: repo://openswe/dashboard/__init__.py
  - id: openwiki-source-7fc33e4789861923a6f12e78
    resource: repo://openswe/dashboard/routes.py
  - id: openwiki-source-3e4d955c2e907c017e3302d0
    resource: repo://openswe/desktop.py
  - id: openwiki-source-1685d34aae8025be9332f45a
    resource: repo://openswe/dispatch.py
  - id: openwiki-source-d0edf7555209b3e6418b5c5f
    resource: repo://openswe/github/routes.py
  - id: openwiki-source-813c25f6bac2408de322a1f5
    resource: repo://openswe/graphs/agent.py
  - id: openwiki-source-c9678be3f577e55e574a349f
    resource: repo://openswe/graphs/analyzer.py
  - id: openwiki-source-a9562865a4bff791686b49dd
    resource: repo://openswe/graphs/review_scout.py
  - id: openwiki-source-90b15fd6117126ebfe5b6b22
    resource: repo://openswe/graphs/reviewer.py
  - id: openwiki-source-6b99105488d7c23beda1e5ad
    resource: repo://openswe/graphs/scheduler.py
  - id: openwiki-source-ff94e6d6f8e823f174c61b08
    resource: repo://openswe/linear/routes.py
  - id: openwiki-source-96bcad07b4fe7078402bc2b8
    resource: repo://openswe/reviewer.py
  - id: openwiki-source-685dc33e7199aa1f6e402f7a
    resource: repo://openswe/scheduler.py
  - id: openwiki-source-919e16feae379651f2cbc1c9
    resource: repo://openswe/server.py
  - id: openwiki-source-c1d629bf5196269b73880148
    resource: repo://openswe/slack/routes.py
  - id: openwiki-source-3bd49e1c2bb74350a7519268
    resource: repo://openswe/webapp.py
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
  - id: openwiki-source-4eb06f8c7641cb7107e39ca8
    resource: repo://ui/src/router.tsx
  - id: openwiki-source-c7a3ad58e4b4017484c1e326
    resource: repo://ui/src/routes/agents.tsx
generated: { by: "openwiki/0.4.2", at: "2026-10-08T15:19:10.971Z" }
verified:
  - by: openwiki/0.4.2
    at: 2026-10-08T15:19:10.971Z
---

# Runtime and Product Architecture

Open SWE is a LangGraph deployment with a custom FastAPI application. The HTTP layer accepts browser and integration traffic, while durable LangGraph runs execute coding or review work against thread-scoped state and—except for PR chat—a backend. Six registered graph entrypoints separate the primary agent, reviewer, review-style analyzer, review-scout tool inspector, read-only PR chat, and time-triggered work.

## Mission and workflow

Open SWE turns engineering work into a repeatable system:

1. **Investigate** — Understand the codebase and requirements
2. **Implement in a sandbox** — Make changes in an isolated, persistent backend
3. **Validate** — Run focused tests and checks
4. **Deliver a PR** — Open or update a pull request on GitHub
5. **Review and iterate** — Respond to CI feedback, code review comments, and repository-specific review preferences

It also operates as a reviewer (runs on-demand or automatic reviews, publishes findings, learns repo-specific review styles), as a PR chat agent (read-only discussion of changes), and as a scheduler (recurring tasks, CI monitoring, deadline management).

## Runtime map

`langgraph.json` is the cloud deployment manifest. It registers thin `openswe/graphs/` re-export modules as stable dotted entrypoints, mounts `openswe.webapp:app`, configures checkpointer retention, loads `.env`, and builds the dashboard into the deployment image.

| Graph | Registered entrypoint | Responsibility |
|---|---|---|
| `agent` | `openswe.graphs.agent:traced_agent` | Per-run coding-agent factory: backend, models, tools, skills, and middleware. |
| `reviewer` | `openswe.graphs.reviewer:traced_reviewer_agent` | PR review and findings publication workflow. |
| `analyzer` | `openswe.graphs.analyzer:traced_analyzer` | Repository-specific review-style learning. |
| `review-scout` | `openswe.graphs.review_scout:traced_review_scout` | Review-tool inspection and capability discovery. |
| `chat` | `openswe.graphs.chat:traced_chat_agent` | Read-only discussion of one PR, without a sandbox. |
| `scheduler` | `openswe.graphs.scheduler:get_scheduler` | A cron-tick dispatcher for maintenance and scheduled work. |

<!-- openwiki: mermaid parse failed and this diagram was converted to a text fence so it does not break rendering. Fix the diagram source and restore the mermaid fence. Parser error: Heuristic: an unescaped angle bracket inside a label breaks rendering; rephrase the label. -->
```text
flowchart TD
  Slack["Slack"] --> Webhooks["Webhook routers<br/>(GitHub, Linear, Slack)"]
  Linear["Linear"] --> Webhooks
  GitHub["GitHub"] --> Webhooks
  Browser["Web dashboard"] --> Dashboard["Dashboard router"]
  Cron["Cron tick"] --> Scheduler["scheduler graph"]

  subgraph App["FastAPI app"]
    Webhooks
    Dashboard
  end

  Webhooks --> Dispatch["dispatch_agent_run"]
  Dashboard --> Dispatch
  Scheduler --> Dispatch
  Dispatch --> Agent["agent graph"]
  Dispatch --> Reviewer["reviewer graph"]
  Agent --> Backend["Thread sandbox or desktop backend"]
  Reviewer --> Backend
  Analyzer["analyzer graph"] --> Backend
  Scout["review-scout graph"] --> Backend
  Browser --> Chat["chat graph"]
```

This shows the principal paths. `dispatch_agent_run` is the shared creation boundary for `agent` and `reviewer` runs; chat, analysis, review-scout, and scheduler invocations use their own graph entrypoints.

## Graph and execution boundaries

`openswe.server:get_agent` (wrapped as `_get_agent`) produces a fresh deep-agent graph for an executable thread. It uses the thread ID to acquire a cached or reconnected backend (or a desktop `LocalShellBackend`), starts it, resolves thread/team/profile model settings, persists normalized thread settings when needed, and assembles a composite backend, tools, skills, subagents, and middleware. If no thread ID is supplied or the graph is being loaded rather than executed, it returns a deliberately empty deep agent without provisioning a backend. This is important for graph discovery and other non-execution loads. See [Agent Graph & get_agent Factory](./agent-graph.md) and [Middleware Stack](./middleware-stack.md) for its detailed composition.

The reviewer follows the sandbox lifecycle but deliberately has a review-only toolset: `add_finding`, `update_finding`, `list_findings`, and `publish_review`; it does not receive commit, push, or PR-opening tools. Its preparation computes an in-diff line set so finding validation occurs when a finding is created, rather than failing only during GitHub publication. The analyzer uses the reviewer-style sandbox and authenticated `gh` access to mine historical human review feedback and finding outcomes, then saves a per-repository prompt through `save_review_style_prompt`. The review-scout graph uses the same sandbox and tool environment but is designed for capability inspection and review-tool discovery. See [Reviewer & Review-Style Analyzer Graphs](./reviewer-and-analyzer.md).

The chat graph is intentionally sandbox-less and read-only. The dashboard review-chat proxy seeds the PR diff, findings, and overview as virtual `/pr/` files in the graph's `files` state channel. Filesystem tools can read that context, while `execute`, writes, edits, and deletion are excluded; GitHub-backed tools use a repository-scoped GitHub App token rather than a user credential.

The scheduler is a compiled, single-node `StateGraph`. Its `_launch` node dispatches a cron tick to reconciliation, watch evaluation, background-task monitoring, session-cost or agent-cost refresh, thread-feedback evaluation, human-review deadline management, or `launch_scheduled_agent_run`. Missing a watch key, thread ID, or schedule ID yields a structured status instead of launching ambiguous work.

## HTTP composition and ingress

`openswe/webapp.py` is a compatibility re-export of the application assembled by `openswe/api/app.py:create_app`. At import time the API pins one event loop before queue workers are built. The app factory configures credentialed CORS from `DASHBOARD_ALLOWED_ORIGINS` and rejects `*`, then mounts dashboard, plan, workflow-approval, Linear, Notion, Slack, GitHub, health, and sandbox-tool routers plus the bundled dashboard UI. Its lifespan hook repeats event-loop pinning, validates sandbox and local-development LLM configuration at startup, runs database migrations and user imports, activates analytics, starts listeners for transcripts and sandbox bridges, closes cached models on shutdown, and handles imports of legacy Store records (user mappings, concierge mode, automations).

The dashboard router is rooted at `/dashboard/api` and applies a same-origin dependency to mutations. It is the browser-facing boundary for OAuth, profiles, team defaults, administration, repository and review-style configuration, and thread APIs. Importing `openswe.dashboard` does not eagerly load this large surface: a PEP 562 `__getattr__` imports and caches `routes.router` only when the web application mounts it. This prevents middleware and non-webapp modules from pulling in FastAPI and all downstream API/job dependencies.

Slack, Linear, and GitHub webhook routes validate and normalize external events before scheduling their service work. They derive or resolve stable thread identities—for example, Linear uses the issue ID and reviewer runs use repository and PR coordinates—so later activity can recover the corresponding thread state rather than starting an unrelated session. GitHub rejects invalid webhook signatures; Slack rejects conflicting mapping rather than guessing an agent thread.

## Durable dispatch and state ownership

`dispatch_agent_run` is the common run-creation contract used by Slack, Linear, GitHub, dashboard, and scheduled agent/reviewer triggers. `assistant_id` selects `agent` or `reviewer`; `source` determines input identity and is retained for metadata/logging, not graph selection. The dispatcher rejects ambiguous combinations of a prebuilt input with content or identity arguments, ensuring callers choose either a fully constructed `RunInput` or build one from content, context, and identities.

The durable defaults are intentional: `multitask_strategy="interrupt"` interrupts an active run so the follow-up resumes with prior history; `durability="sync"` checkpoints before steps; streams are resumable and include subgraphs; and a private event-streaming v3 configurable marker (`__event_streaming_v2`) and compatible stream modes (`values`, `updates`, `messages`, `custom`, `tasks`, `checkpoints`) make externally initiated runs observable in the dashboard. Background follow-ups may explicitly choose another multitask strategy such as `enqueue`.

Completion notification is best effort. The dispatcher attaches a webhook only when `RUN_COMPLETE_WEBHOOK_SECRET` is configured and `COMPLETION_WEBHOOK_URL` is absolute and non-loopback. Otherwise it logs the condition and creates the run without a webhook, avoiding a configuration error that would poison all run creation.

A graph factory is ephemeral, but thread execution context is durable: LangGraph checkpointing retains graph state, while LangGraph thread metadata holds the sandbox ID and related thread settings. The sandbox cache is in process and keyed by thread ID; another worker reconnects using the persisted ID. An existing unreachable sandbox raises rather than being automatically replaced, because silent replacement can discard uncommitted work. Reviewer callers can allow replacement because their checkout is re-derived. The sandbox is published to the cache only after initialization and metadata binding succeed. See [Sandbox Lifecycle](./sandbox-lifecycle.md) and [Invocation](../workflows/invocation.md).

## Cloud and desktop product surfaces

The cloud manifest currently pins Python 3.14 and LangGraph API version 0.15.0rc1. Its checkpointer TTL uses `delete`, sweeps every 60 minutes, and defaults to 43,200 minutes. The Dockerfile instructions attempt to build and install the dashboard static assets but allow a backend-only deployment if that build fails.

`langgraph.desktop.json` intentionally registers only the main agent graph, uses `openswe.local_auth:auth` with Studio authentication disabled, and disables the bundled UI. A desktop run is identified by `configurable.source == "desktop"`. Its requested `local_project_path` must resolve to an existing directory that is either in `OPEN_SWE_LOCAL_PROJECTS_FILE` or beneath `OPEN_SWE_LOCAL_WORKTREES_DIR`; otherwise it is rejected. The local backend inherits only a small shell environment allowlist (`HOME`, `LANG`, `LC_ALL`, `PATH`, `SHELL`, `TMPDIR`). Desktop scratch routes put large tool results and conversation history outside the project so they are not swept into `git add -A`.

The `ui/` application is a TanStack Router React client with routes for agent sessions and threads, local sessions, plans, automations, skills, reviews and styles, administration, integrations, usage, settings, environments, instructions, and sandbox views. The `/agents` layout requires a session except for enabled desktop-local routes and selects `cloud` or `local` streaming transport from the active route. Router `basepath` follows Vite's build base, allowing the bundle to run below a configured mount prefix.

## Operations and safe changes

- Add a deployable graph by exporting a stable factory through `openswe/graphs/` and registering it in the appropriate manifest. Do not assume it becomes eligible for `dispatch_agent_run`; that contract selects only `agent` and `reviewer`.
- Add browser APIs through `create_app`, preserving session and mutation-origin protections rather than bypassing the dashboard boundary.
- Treat a coding sandbox that is unreachable as a recovery decision, not a cache miss. Replacing it changes the thread working tree.
- When changing dispatch defaults or stream fields, test durable run creation and cross-surface event attachment: dashboard observability relies on replayable v3-compatible streams for runs created outside the browser.
- When changing desktop path handling, retain real-path validation, the allowlist/worktree boundary, and artifact routing; each prevents a distinct local safety or repository-hygiene failure.

Related pages: [Agent Graph & get_agent Factory](./agent-graph.md), [Reviewer & Review-Style Analyzer Graphs](./reviewer-and-analyzer.md), [Sandbox Lifecycle](./sandbox-lifecycle.md), [Dashboard UI](../integrations/dashboard-ui.md), and [Invocation](../workflows/invocation.md).
