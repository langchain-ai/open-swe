---
type: runtime architecture
title: Runtime Architecture and Public Surfaces
description: How the LangGraph service composes graph entrypoints, FastAPI ingress, dashboard delivery, durable execution state, analytics lifecycle, and the desktop-local backend.
tags: [architecture, langgraph, fastapi, dashboard, desktop, persistence]
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
  - id: openwiki-source-498da25d3e3138b4b82e11fe
    resource: repo://agent/database/analytics.py
  - id: openwiki-source-8c60a9544ea26006748dd7a3
    resource: repo://agent/desktop.py
  - id: openwiki-source-c48b309c5ca416cf623f0866
    resource: repo://agent/dispatch.py
  - id: openwiki-source-f8665996049065d2172f68e2
    resource: repo://agent/graphs/agent.py
  - id: openwiki-source-73db7609f2a24f4a0ff5c32c
    resource: repo://agent/graphs/reviewer.py
  - id: openwiki-source-1116ea2d477f08cf0f5b2ef0
    resource: repo://agent/graphs/scheduler.py
  - id: openwiki-source-276ab38291eb5741b4c2141c
    resource: repo://agent/reviewer.py
  - id: openwiki-source-6fd11c8bb15f5eb94b765440
    resource: repo://agent/sandboxes/lifecycle.py
  - id: openwiki-source-3e15117ace082a39e1f130d8
    resource: repo://agent/scheduler.py
  - id: openwiki-source-19dd52d603eb15a9bf38885d
    resource: repo://agent/schedules/store.py
  - id: openwiki-source-856ade03ef31ac38e1347f7c
    resource: repo://agent/server.py
  - id: openwiki-source-6e64b1ccdb133daeb8f4d1d4
    resource: repo://agent/utils/dashboard_ui.py
  - id: openwiki-source-3096620cfd0eb1bae6d9e78c
    resource: repo://agent/webapp.py
  - id: openwiki-source-f94f5d5d16b6aac2f4bc309c
    resource: repo://desktop/src/backend-supervisor.cjs
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
verified:
  - by: openwiki/0.4.2
    at: 2026-09-15T08:15:12.744Z
generated: { by: "openwiki/0.4.2", at: "2026-09-15T08:15:12.744Z" }
---

# Runtime Architecture and Public Surfaces

Open SWE is a LangGraph deployment whose custom FastAPI app is the public HTTP composition point. Browser, webhook, and scheduled work ultimately create durable LangGraph runs; the graph factory creates the thread-specific execution environment when a run actually executes. The registered graphs deliberately separate code changes, PR review, review-style analysis, PR discussion, and cron work.

## Hosted runtime and graph entrypoints

`langgraph.json` is the cloud manifest. It uses Python 3.14 and API version 0.13.3, loads `.env`, mounts `agent.webapp:app`, and exposes five stable entries via the thin `agent/graphs/` re-export modules:

| Graph ID | Entry point | Runtime role |
|---|---|---|
| `agent` | `agent.graphs.agent:traced_agent` | Coding-agent factory for an executable thread. |
| `reviewer` | `agent.graphs.reviewer:traced_reviewer_agent` | Review and finding-publication agent. |
| `analyzer` | `agent.graphs.analyzer:traced_analyzer` | Learns repository-specific review style. |
| `chat` | `agent.graphs.chat:traced_chat_agent` | Read-only, PR-context chat agent. |
| `scheduler` | `agent.graphs.scheduler:get_scheduler` | Cron-tick dispatcher. |

```mermaid
flowchart TD
  Browser["Browser dashboard"] --> Api["FastAPI application"]
  Slack["Slack"] --> Api
  Linear["Linear"] --> Api
  GitHub["GitHub"] --> Api
  Cron["Cron tick"] --> Scheduler["scheduler graph"]
  Api --> Dispatch["durable dispatch"]
  Scheduler --> Dispatch
  Dispatch --> Agent["agent graph"]
  Dispatch --> Reviewer["reviewer graph"]
  Agent --> Backend["thread backend"]
  Reviewer --> Backend
  Browser --> Chat["chat graph"]
  Analyzer["analyzer graph"] --> Backend
```

This is the principal hosted request topology. Durable dispatch is the shared creation boundary for agent and reviewer work; chat, analysis, and scheduler calls use their own registered graphs.

`get_agent` returns a fresh deep-agent graph for each executable run. It reads the thread configuration, resolves the sender's GitHub identity and thread settings, gets or reconnects the backend, starts it, then assembles models, tools, skills, subagents, and middleware. Discovery or non-execution loads—and calls without a thread ID—return an empty, no-sandbox agent instead, so merely loading a graph cannot provision infrastructure. See [Agent Graph](./agent-graph.md) for its detailed composition.

The reviewer shares the sandbox lifecycle but gets only finding tools (`add_finding`, `update_finding`, `list_findings`, and `publish_review`), not commit, push, or PR-opening tools. The analyzer uses the corresponding authenticated sandbox pattern to inspect historical PR reviews and finding outcomes, then persists a repository review-style prompt. In contrast, the `chat` graph has no sandbox: its dashboard proxy supplies PR information as virtual `/pr/` files in the graph state, retaining read access while excluding execution and file mutation. See [Reviewer and Analyzer](./reviewer-and-analyzer.md).

The scheduler is a one-node `StateGraph`. A tick selects reconciliation, watch evaluation, background-task monitoring, environment refresh, session or agent cost refresh, thread-feedback prompting, or a scheduled agent launch. Where a task requires an identifier, such as a watch key, thread ID, or schedule ID, missing input returns a status rather than launching uncertain work.

## HTTP application and dashboard boundary

`agent.webapp` is intentionally only a compatibility export of the app built in `agent.api.app`. `create_app()` installs tracing, dashboard, plan, workflow-approval, Linear, Slack, health, and GitHub routers, then mounts the dashboard last. Credentialed CORS is enabled only for configured `DASHBOARD_ALLOWED_ORIGINS`; `*` is rejected because credentials are allowed.

```mermaid
sequenceDiagram
  participant Client
  participant Api as FastAPI app
  participant Dash as Dashboard API
  participant Dispatch as Durable dispatch
  participant Graph as LangGraph run
  Client->>Api: dashboard request or webhook
  Api->>Dash: browser API when applicable
  Dash->>Dispatch: agent or reviewer trigger
  Api->>Dispatch: integration trigger
  Dispatch->>Graph: create durable run
  Graph-->>Client: resumable event stream through client transport
```

This shows the ingress-to-run path; webhook routers use the same dispatch contract after validating and normalizing their external input.

The dashboard aggregate router is prefixed `/dashboard/api` and applies the same-origin mutation dependency. It owns browser-facing authentication, preferences and profiles, team settings, repositories, reviews and review styles, schedules, environments, skills, integrations, analytics, incidents, and thread APIs. Its package lazily imports the aggregate router through `__getattr__`, avoiding a transitive import of every API and job module for code that only needs another dashboard submodule.

The dashboard is normally served from the backend origin. `mount_dashboard_ui` either serves a static build or reverse-proxies a Vite development server selected by `DASHBOARD_DEV_SERVER_URL`. Its catch-all explicitly declines LangGraph and API prefixes, serves immutable hashed assets, and returns the shell only for HTML navigation requests. The route must remain last: otherwise it can shadow routes that follow it. A deployment with no build and no dev server still serves the backend.

## Lifecycle, persistence, and failure behavior

At module import, and again during lifespan startup, the API pins one event loop before queue workers are created. Startup validates the GitHub login allowlist, sandbox provider configuration, and local-development LLM settings. It then attempts analytics migration, reporting activation, and worker startup; analytics failures are logged rather than preventing the service from starting. Shutdown stops that worker, closes analytics connections, and closes cached models.

Analytics is an optional PostgreSQL-backed reporting boundary. When `POSTGRES_URI` is unset it is disabled. When configured, migration loads the persisted deployment workspace ID and reporting activation records a one-time cutover timestamp. Readiness reports configuration, outbox pending/dead-letter state, and whether event projections are only complete from the capture start; it returns a non-ready result rather than propagating readiness-query failures.

`dispatch_agent_run` is the common agent/reviewer trigger contract used by the dashboard, integrations, and scheduled launches. `assistant_id` selects the graph while `source` is used to construct identity context and for metadata/logging. It rejects a prebuilt input combined with independent content or identity fields. The created run defaults to synchronous durability, interruption of an active run, subgraph streaming, resumable streams, and the event-stream compatibility marker; this lets the dashboard attach to runs initiated outside the browser.

Completion callbacks are optional. A callback is attached only when `RUN_COMPLETE_WEBHOOK_SECRET` is present and `COMPLETION_WEBHOOK_URL` is an absolute non-loopback HTTP(S) URL; otherwise dispatch creates the run without a callback rather than allowing bad callback configuration to fail every run.

LangGraph checkpoints persist graph state. Separately, thread metadata persists a sandbox ID and settings, while the process-local backend cache is keyed by thread ID. A later worker reconnects from the persisted ID. A missing/deleted sandbox may be recreated, but a reachable-recorded sandbox that cannot be connected raises by default: replacing it silently would discard uncommitted coding work. Review callers may opt into replacement because their checkout is re-derived. See [Invocation](../workflows/invocation.md) and [Deployment](../operations/deployment.md).

## Dashboard and desktop surfaces

The React `ui/` client uses TanStack Router. Its agent layout requires a normal session except for enabled desktop-local routes, tracks the active cloud thread or local session, and selects the `cloud` or `local` streaming transport accordingly. The wider application exposes agent sessions, plans, automations, skills, reviews, administration, integrations, usage, settings, environments, instructions, and sandbox-related views. See [Dashboard UI](../integrations/dashboard-ui.md).

Desktop intentionally starts a smaller local LangGraph service. `BackendSupervisor` reserves a loopback port, generates a per-start bearer token, requires a project allowlist and worktree directory, launches `langgraph dev` with ten jobs per worker, and polls its authenticated root endpoint for up to 60 seconds. Requests at `/local-graph` are proxied to that process with cookies removed and the bearer token injected. Startup failure includes captured backend logs; shutdown sends `SIGTERM` and escalates to `SIGKILL` after the stop timeout.

The desktop manifest exposes only `agent`, uses `agent.local_auth:auth`, disables Studio auth and the bundled UI. Desktop graph runs use a `LocalShellBackend`, but only after `local_project_path` resolves to an existing allowlisted project or a directory under the desktop worktree root. Its shell receives only a limited environment. Agent scratch routes for large results and conversation history point outside the project, preventing those artifacts from becoming accidental `git add -A` input.

## Safe extension and focused verification

- Register a new deployable graph through `agent/graphs/` and the relevant manifest; registration alone does not make it selectable by `dispatch_agent_run`, which is an agent/reviewer contract.
- Add HTTP routes before the dashboard catch-all and preserve the dashboard router's same-origin mutation protection.
- Treat analytics startup as optional but test configured migrations and readiness separately from a backend-only start.
- Preserve durable dispatch defaults and test both run creation and late dashboard stream attachment when changing event-stream fields.
- Exercise the desktop supervisor's readiness, failure, proxy, and termination paths in `desktop/test/backend-supervisor.test.cjs`; preserve real-path project authorization and out-of-project artifact routing when changing local execution.

Related: [Agent Graph](./agent-graph.md), [Reviewer and Analyzer](./reviewer-and-analyzer.md), [Dashboard UI](../integrations/dashboard-ui.md), [Deployment](../operations/deployment.md), and [Invocation](../workflows/invocation.md).
