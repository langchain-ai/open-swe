---
type: architecture overview
title: Runtime and Product Architecture
description: LangGraph deployment, graph entrypoints, FastAPI ingress, durable dispatch, sandbox ownership, and the dashboard and desktop product surfaces.
tags: [architecture, langgraph, fastapi, dashboard, runtime]
verified:
  - by: openwiki/0.4.2
    at: 2026-09-08T08:15:30.533Z
sources:
  - id: openwiki-source-63ebc853556c1b852ed80aff
    resource: repo://agent/analyzer.py
  - id: openwiki-source-328bde9e94017848bb09ba23
    resource: repo://agent/api/app.py
  - id: openwiki-source-921ec88ab63280d28b3dddb5
    resource: repo://agent/chat.py
  - id: openwiki-source-412c2c84023da365b8201b9f
    resource: repo://agent/dashboard/__init__.py
  - id: openwiki-source-61ace7d4952db9ddb8316aeb
    resource: repo://agent/dashboard/routes.py
  - id: openwiki-source-8c60a9544ea26006748dd7a3
    resource: repo://agent/desktop.py
  - id: openwiki-source-c48b309c5ca416cf623f0866
    resource: repo://agent/dispatch.py
  - id: openwiki-source-ba064e884edcde6097165df2
    resource: repo://agent/github/webhook.py
  - id: openwiki-source-f8665996049065d2172f68e2
    resource: repo://agent/graphs/agent.py
  - id: openwiki-source-73db7609f2a24f4a0ff5c32c
    resource: repo://agent/graphs/reviewer.py
  - id: openwiki-source-1116ea2d477f08cf0f5b2ef0
    resource: repo://agent/graphs/scheduler.py
  - id: openwiki-source-2d78b3dc0a340eaacb9e53e2
    resource: repo://agent/linear/webhook.py
  - id: openwiki-source-276ab38291eb5741b4c2141c
    resource: repo://agent/reviewer.py
  - id: openwiki-source-6fd11c8bb15f5eb94b765440
    resource: repo://agent/sandboxes/lifecycle.py
  - id: openwiki-source-3e15117ace082a39e1f130d8
    resource: repo://agent/scheduler.py
  - id: openwiki-source-856ade03ef31ac38e1347f7c
    resource: repo://agent/server.py
  - id: openwiki-source-e0785b4f2497c26e024d92fc
    resource: repo://agent/slack/routes.py
  - id: openwiki-source-3096620cfd0eb1bae6d9e78c
    resource: repo://agent/webapp.py
  - id: openwiki-source-b76f79b6cfae139d1784a43a
    resource: repo://langgraph.desktop.json
  - id: openwiki-source-5bbba7b2a8ea8360ff233d63
    resource: repo://langgraph.json
  - id: openwiki-source-4eb06f8c7641cb7107e39ca8
    resource: repo://ui/src/router.tsx
  - id: openwiki-source-c7a3ad58e4b4017484c1e326
    resource: repo://ui/src/routes/agents.tsx
  - id: openwiki-source-767ef8a0f66938a5c0710041
    resource: repo://ui/src/routeTree.gen.ts
generated: { by: "openwiki/0.4.2", at: "2026-09-08T08:15:30.533Z" }
---

# Runtime and Product Architecture

Open SWE is a LangGraph deployment with a custom FastAPI application. The HTTP layer accepts browser and integration traffic, while durable LangGraph runs execute coding or review work against thread-scoped state and—except for PR chat—a backend. Five registered graph entrypoints separate the primary agent, reviewer, review-style analyzer, read-only PR chat, and time-triggered work.

## Runtime map

`langgraph.json` is the cloud deployment manifest. It registers thin `agent/graphs/` re-export modules as stable dotted entrypoints, mounts `agent.webapp:app`, configures checkpointer retention, loads `.env`, and builds the dashboard into the deployment image.

| Graph | Registered entrypoint | Responsibility |
|---|---|---|
| `agent` | `agent.graphs.agent:traced_agent` | Per-run coding-agent factory: backend, models, tools, skills, and middleware. |
| `reviewer` | `agent.graphs.reviewer:traced_reviewer_agent` | PR review and findings publication workflow. |
| `analyzer` | `agent.graphs.analyzer:traced_analyzer` | Repository-specific review-style learning. |
| `chat` | `agent.graphs.chat:traced_chat_agent` | Read-only discussion of one PR, without a sandbox. |
| `scheduler` | `agent.graphs.scheduler:get_scheduler` | A cron-tick dispatcher for maintenance and scheduled work. |

```mermaid
flowchart TD
  Slack["Slack"] --> Webhooks["Webhook routers"]
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
  Browser --> Chat["chat graph"]
```

This shows the principal paths. `dispatch_agent_run` is the shared creation boundary for `agent` and `reviewer` runs; chat, analysis, and scheduler invocations use their own graph entrypoints.

## Graph and execution boundaries

`agent.server:get_agent` produces a fresh deep-agent graph for an executable thread. It uses the thread ID to acquire a cached or reconnected backend (or a desktop `LocalShellBackend`), starts it, resolves thread/team/profile model settings, persists normalized thread settings when needed, and assembles a composite backend, tools, skills, subagents, and middleware. If no thread ID is supplied or the graph is being loaded rather than executed, it returns a deliberately empty deep agent without provisioning a backend. This is important for graph discovery and other non-execution loads. See [Agent Graph & get_agent Factory](./agent-graph.md) and [Middleware Stack](./middleware-stack.md) for its detailed composition.

The reviewer follows the sandbox lifecycle but deliberately has a review-only toolset: `add_finding`, `update_finding`, `list_findings`, and `publish_review`; it does not receive commit, push, or PR-opening tools. Its preparation computes an in-diff line set so finding validation occurs when a finding is created, rather than failing only during GitHub publication. The analyzer uses the reviewer-style sandbox and authenticated `gh` access to mine historical human review feedback and finding outcomes, then saves a per-repository prompt through `save_review_style_prompt`. See [Reviewer & Review-Style Analyzer Graphs](./reviewer-and-analyzer.md).

The chat graph is intentionally sandbox-less and read-only. The dashboard review-chat proxy seeds the PR diff, findings, and overview as virtual `/pr/` files in the graph's `files` state channel. Filesystem tools can read that context, while `execute`, writes, edits, and deletion are excluded; GitHub-backed tools use a repository-scoped GitHub App token rather than a user credential.

The scheduler is a compiled, single-node `StateGraph`. Its `task` selects stale-run reconciliation, watch evaluation, background-task monitoring, session-cost or agent-cost refresh, or `launch_scheduled_agent_run`. Missing a watch key, thread ID, or schedule ID yields a structured status instead of launching ambiguous work. Scheduled agent runs ultimately use durable dispatch.

## HTTP composition and ingress

`agent/webapp.py` is a compatibility re-export of the application assembled by `agent/api/app.py:create_app`. At import time the API pins one event loop before queue workers are built. The app factory configures credentialed CORS from `DASHBOARD_ALLOWED_ORIGINS` and rejects `*`, then mounts dashboard, plan, workflow-approval, Linear, Slack, GitHub, and health routers plus the bundled dashboard UI. Its lifespan repeats event-loop pinning, validates sandbox and local-development LLM configuration at startup, and closes cached models on shutdown.

The dashboard router is rooted at `/dashboard/api` and applies a same-origin dependency to mutations. It is the browser-facing boundary for OAuth, profiles, team defaults, administration, repository and review-style configuration, and thread APIs. Importing `agent.dashboard` does not eagerly load this large surface: a PEP 562 `__getattr__` imports and caches `routes.router` only when the web application mounts it.

Slack, Linear, and GitHub webhook routes validate and normalize external events before scheduling their service work. They derive or resolve stable thread identities—for example, Linear uses the issue ID and reviewer runs use repository and PR coordinates—so later activity can recover the corresponding thread state rather than starting an unrelated session. GitHub rejects invalid webhook signatures; Slack rejects conflicting mapping rather than guessing an agent thread.

## Durable dispatch and state ownership

`dispatch_agent_run` is the common run-creation contract used by Slack, Linear, GitHub, dashboard, and scheduled agent/reviewer triggers. `assistant_id` selects `agent` or `reviewer`; `source` determines input identity and is retained for metadata/logging, not graph selection. The dispatcher also rejects ambiguous combinations of a prebuilt input with content or identity arguments.

The durable defaults are intentional: `multitask_strategy="interrupt"` interrupts an active run so the follow-up resumes with prior history; `durability="sync"` checkpoints before steps; streams are resumable and include subgraphs; and a private event-streaming v2 configurable marker and compatible stream modes make externally initiated runs observable in the dashboard. Background follow-ups may explicitly choose another multitask strategy such as `enqueue`.

Completion notification is best effort. The dispatcher attaches a webhook only when `RUN_COMPLETE_WEBHOOK_SECRET` is configured and `COMPLETION_WEBHOOK_URL` is absolute and non-loopback. Otherwise it logs the condition and creates the run without a webhook, avoiding a configuration error that would poison all run creation.

A graph factory is ephemeral, but thread execution context is durable: LangGraph checkpointing retains graph state, while LangGraph thread metadata holds the sandbox ID and related thread settings. The sandbox cache is in process and keyed by thread ID; another worker reconnects using the persisted ID. A deleted sandbox is replaced, but an existing unreachable coding sandbox raises by default because silent replacement can discard uncommitted work. Reviewer callers can allow replacement because their checkout is re-derived. The sandbox is published to the cache only after initialization and metadata binding succeed. See [Sandbox Lifecycle](./sandbox-lifecycle.md) and [Invocation](../workflows/invocation.md).

## Cloud and desktop product surfaces

The cloud manifest currently pins Python 3.14 and LangGraph API version 0.13.3. Its checkpointer TTL uses `delete`, sweeps every 60 minutes, and defaults to 43,200 minutes. The Dockerfile instructions attempt to build and install the dashboard static assets but allow a backend-only deployment if that build fails.

`langgraph.desktop.json` intentionally registers only the main agent graph, uses `agent.local_auth:auth` with Studio authentication disabled, and disables the bundled UI. A desktop run is identified by `configurable.source == "desktop"`. Its requested `local_project_path` must resolve to an existing directory that is either in `OPEN_SWE_LOCAL_PROJECTS_FILE` or beneath `OPEN_SWE_LOCAL_WORKTREES_DIR`; otherwise it is rejected. The local backend inherits only a small shell environment allowlist. Desktop scratch routes put large tool results and conversation history outside the project so they are not swept into `git add -A`.

The `ui/` application is a TanStack Router React client with routes for agent sessions and threads, local sessions, plans, automations, skills, reviews and styles, administration, integrations, usage, settings, environments, instructions, and sandbox views. The `/agents` layout requires a session except for enabled desktop-local routes and selects `cloud` or `local` streaming transport from the active route. Router `basepath` follows Vite's build base, allowing the bundle to run below a configured mount prefix.

## Operations and safe changes

- Add a deployable graph by exporting a stable factory through `agent/graphs/` and registering it in the appropriate manifest. Do not assume it becomes eligible for `dispatch_agent_run`; that contract selects only `agent` and `reviewer`.
- Add browser APIs through `create_app`, preserving session and mutation-origin protections rather than bypassing the dashboard boundary.
- Treat a coding sandbox that is unreachable as a recovery decision, not a cache miss. Replacing it changes the thread working tree.
- When changing dispatch defaults or stream fields, test durable run creation and cross-surface event attachment: dashboard observability relies on replayable v2-compatible streams for runs created outside the browser.
- When changing desktop path handling, retain real-path validation, the allowlist/worktree boundary, and artifact routing; each prevents a distinct local safety or repository-hygiene failure.

Related pages: [Agent Graph & get_agent Factory](./agent-graph.md), [Reviewer & Review-Style Analyzer Graphs](./reviewer-and-analyzer.md), [Sandbox Lifecycle](./sandbox-lifecycle.md), [Dashboard UI](../integrations/dashboard-ui.md), and [Invocation](../workflows/invocation.md).
