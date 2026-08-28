---
type: quickstart-hub
title: Open SWE Quickstart & Wiki Map
description: Entry point that orients a coding agent to Open SWE — a LangGraph + Deep Agents framework for internal coding agents — with the dev loop and a task-routing map to every wiki section.
tags: [open-swe, langgraph, deepagents, coding-agent, quickstart, wiki-map, architecture-overview]
verified:
  - by: openwiki/0.4.2
    at: 2026-08-27T06:27:22.313Z
sources:
  - id: openwiki-source-328bde9e94017848bb09ba23
    resource: repo://agent/api/app.py
  - id: openwiki-source-f8665996049065d2172f68e2
    resource: repo://agent/graphs/agent.py
  - id: openwiki-source-368e3a3da2c40119aead4316
    resource: repo://agent/graphs/chat.py
  - id: openwiki-source-1116ea2d477f08cf0f5b2ef0
    resource: repo://agent/graphs/scheduler.py
  - id: openwiki-source-3e15117ace082a39e1f130d8
    resource: repo://agent/scheduler.py
  - id: openwiki-source-3096620cfd0eb1bae6d9e78c
    resource: repo://agent/webapp.py
  - id: openwiki-source-8037e2358a2c4f9b2c722a11
    resource: repo://AGENTS.md
  - id: openwiki-source-5bbba7b2a8ea8360ff233d63
    resource: repo://langgraph.json
  - id: openwiki-source-012f2c78e3b1446dfc35803f
    resource: repo://Makefile
  - id: openwiki-source-5b54a58d1b51cd490b0e7162
    resource: repo://package.json
  - id: openwiki-source-05ccef8d4cf1698187f20464
    resource: repo://pyproject.toml
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
generated: { by: "openwiki/0.4.2", at: "2026-08-27T06:27:22.313Z" }
---

# Open SWE Quickstart & Wiki Map

This is the navigational hub for the Open SWE wiki. It orients you to what the
system is, how to run it locally, and which section to open for a given task.
Deep content lives in the linked pages; treat source code and tests as
authoritative over any prose here.

## What Open SWE is

Open SWE is an open-source framework for building an org's internal coding
agent. It is **composed on** the Deep Agents framework
(`deepagents.create_deep_agent`) and runs as a **LangGraph app**: each thread
spawns its own isolated cloud sandbox, and the agent is invoked from Slack,
Linear, or GitHub (PR comments, plus auto-review on `opened` /
`ready_for_review`).

The runtime is served by `langgraph dev` from `langgraph.json`, which declares
**five graph entrypoints plus a FastAPI app**, all served together:

| Graph | `langgraph.json` entrypoint | Underlying factory | Purpose |
|---|---|---|---|
| `agent` | `agent.graphs.agent:traced_agent` | `agent.server:get_agent` | Main coding agent (Slack/Linear/GitHub-triggered). |
| `reviewer` | `agent.graphs.reviewer:traced_reviewer_agent` | `agent.reviewer:get_reviewer_agent` | Read-only PR reviewer; findings model + `publish_review`. |
| `analyzer` | `agent.graphs.analyzer:traced_analyzer` | `agent.analyzer:get_analyzer` | Learns per-repo review style from historical PRs. |
| `chat` | `agent.graphs.chat:traced_chat_agent` | `agent.chat:get_chat_agent` | Dashboard Agents chat graph. |
| `scheduler` | `agent.graphs.scheduler:get_scheduler` | `agent.scheduler:get_scheduler` | Deterministic cron fan-out, reconciliation, `/baby-sit` CI checks. |

The FastAPI app is `agent.webapp:app` (a compatibility shim re-exporting
`agent.api.app:app`). At startup it mounts the dashboard router and the plan,
workflow-approval, Linear, Slack, health, and GitHub webhook routers.

The agent itself is **stateless** — all per-thread state lives in the sandbox
plus thread metadata — and is rebuilt per thread by the graph factory.

```mermaid
flowchart TD
  subgraph Triggers
    SL["Slack mention"]
    LI["Linear comment"]
    GH["GitHub PR comment / PR event"]
  end
  SL --> WA["FastAPI webapp (agent.webapp:app)"]
  LI --> WA
  GH --> WA
  WA --> AG["agent graph (get_agent)"]
  WA --> RV["reviewer graph"]
  WA --> DASH["dashboard router + ui/ dashboard"]
  SCH["scheduler graph"] --> AG
  AN["analyzer graph"] --> RV
  AG --> SB["per-thread cloud sandbox"]
  RV --> SB
```

Inbound triggers land on the FastAPI webapp, which dispatches LangGraph runs; every agent/reviewer thread owns an isolated sandbox.

## Developer loop

Python dependencies are managed with **uv**; the JS dashboard/desktop use
**pnpm**. `langgraph dev` serves all graphs and the FastAPI app together.

```bash
make install            # uv sync --extra dev (pytest, ruff, basedpyright, …)
make dev                # uv run langgraph dev — serves all graphs + FastAPI app
make run                # uvicorn agent.webapp:app --reload --port 8000 (FastAPI only)
make test               # uv run pytest -vvv tests/
make lint               # ruff check + ruff format --diff
make format             # ruff format + ruff check --fix
make typecheck          # basedpyright agent tests
make web                # pnpm run dev — the ui/ dashboard
```

Conventions that matter: `requires-python = ">=3.11"` while `langgraph.json`
pins the runtime to 3.12; ruff uses line-length 100; tests default to unit-only
under `tests/` with `asyncio_mode = "auto"`; the app is **async-only** (do not
add sync/async dual implementations).

## Task-routing map

Pick the section that matches what you are trying to do.

### Architecture
- [System Architecture Overview](architecture/overview.md) — the five graphs, the FastAPI webapp, dashboard router, sandbox layer, and web/desktop UI, and how they connect.
- [Agent Graph & get_agent Factory](architecture/agent-graph.md) — how a deep agent is assembled per-thread with resolved model, curated tools, backend, subagents, and middleware.
- [Middleware Stack](architecture/middleware-stack.md) — the ordered middleware chain around every model call for the agent and reviewer.
- [Sandbox Lifecycle & Providers](architecture/sandbox-lifecycle.md) — per-thread get-or-create-then-reconnect lifecycle, provider selection, the GitHub proxy, and failure handling.
- [Reviewer & Review-Style Analyzer Graphs](architecture/reviewer-and-analyzer.md) — read-only reviewer graph and the analyzer that learns per-repo review style.

### Concepts
- [Agent Tools (Curated Toolset)](concepts/tools.md) — what tools the agent/reviewer/analyzer expose and the agent/UI parity principle.
- [Models, Profiles, Team Defaults & Instructions](concepts/models-profiles-instructions.md) — model + reasoning-effort resolution precedence and layered instructions.
- [Threads, Thread IDs & Persistence](concepts/threads-and-state.md) — deterministic thread-id derivation, thread metadata/state, and Slack code-channel keying.
- [Authentication, Authorization & Security Boundaries](concepts/auth-and-security.md) — GitHub dual-mode auth, webhook signature verification, dashboard OAuth, and encryption at rest.

### Workflows
- [Invocation: Slack, Linear & GitHub Webhooks](workflows/invocation.md) — how an inbound mention/comment/event becomes a dispatched run.
- [PR Creation & GitHub Delivery](workflows/pr-creation.md) — commit, push, and draft-PR flow plus guards and CI feedback.
- [PR Review Workflow](workflows/pr-review.md) — auto-review and comment-triggered reviews from webhook to published findings.
- [Scheduling, Cron & Baby-Sit CI Monitoring](workflows/scheduling-and-baby-sit.md) — scheduler graph and the opt-in `/baby-sit` CI monitoring flow.
- [Context Engineering: AGENTS.md, Source Context & Skills](workflows/context-engineering.md) — how the agent gathers context and uses skills.
- [Mid-Run Follow-Up Messages](workflows/follow-up-messages.md) — how messages sent while the agent is working are queued and injected.

### Integrations
- [Dashboard API & Web/Desktop UI](integrations/dashboard-ui.md) — the dashboard router and the `ui/` React dashboard + `desktop/` Electron wrapper.
- [Observability & MCP Integrations](integrations/observability-and-mcp.md) — Datadog/LangSmith tools, Corridor/Notion MCP, Currents, and Stagehand browser.
- [Sandbox Provider Integrations](integrations/sandbox-providers.md) — pluggable sandbox providers and how to add a new one.

### Operations & Testing
- [Configuration & Environment Variables](operations/configuration.md) — environment variables across sandbox, models, auth, webhooks, and integrations.
- [Local Dev, Build & Deployment](operations/deployment.md) — running locally, building, and deploying the backend and dashboard.
- [Testing Guide](testing/overview.md) — test layout, conventions, and how to run unit and e2e tests.
