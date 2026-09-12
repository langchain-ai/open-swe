---
type: contributor guide
title: Open SWE Codebase Guide
description: Route an Open SWE change from local setup and registered runtime entrypoints to the owning architecture, workflow, integration, operations, and focused tests. Source code and tests remain authoritative; this is optional just-in-time routing context.
tags: [open-swe, contributor-guide, development, langgraph, testing]
verified:
  - by: openwiki/0.4.2
    at: 2026-09-12T08:12:50.175Z
sources:
  - id: openwiki-source-328bde9e94017848bb09ba23
    resource: repo://agent/api/app.py
  - id: openwiki-source-921ec88ab63280d28b3dddb5
    resource: repo://agent/chat.py
  - id: openwiki-source-c48b309c5ca416cf623f0866
    resource: repo://agent/dispatch.py
  - id: openwiki-source-f8665996049065d2172f68e2
    resource: repo://agent/graphs/agent.py
  - id: openwiki-source-f2c7a9cbc0f7af0b4db77658
    resource: repo://agent/graphs/analyzer.py
  - id: openwiki-source-368e3a3da2c40119aead4316
    resource: repo://agent/graphs/chat.py
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
  - id: openwiki-source-856ade03ef31ac38e1347f7c
    resource: repo://agent/server.py
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
  - id: openwiki-source-f0a6e7dc03522b2682f88655
    resource: repo://tests/conftest.py
  - id: openwiki-source-859f98720585f4648f0f7b2e
    resource: repo://tests/e2e/playwright.config.ts
  - id: openwiki-source-4b944ec14a3d793a6f771403
    resource: repo://tests/e2e/playwright.desktop.config.ts
  - id: openwiki-source-7ef60dc4372e1a33c7728fe6
    resource: repo://tests/e2e/README.md
generated: { by: "openwiki/0.4.2", at: "2026-09-12T08:12:50.175Z" }
---

# Open SWE Codebase Guide

Open SWE is an asynchronous coding agent and software factory. Coding threads use isolated sandboxes; separate reviewer and analyzer graphs handle pull-request review and repository-specific review-style learning. Treat source code and tests as authoritative. This page and its links are optional just-in-time context for locating the right owner and validating a safe change.

## Start the smallest useful local loop

The Python project requires Python 3.14 or later and uses `uv`; `ui`, `desktop`, and `tests/e2e` are a pnpm/Turbo workspace.

```bash
make install            # uv sync --extra dev
make dev                # uv run langgraph dev --no-browser --port 2024
make run                # uv run uvicorn agent.webapp:app --reload --port 8000
make dev-ui             # Vite plus LangGraph development server
make web                # pnpm run dev
make desktop            # pnpm run dev:desktop
```

Use `make dev` when the change executes a registered graph: it serves every graph plus the HTTP app. `make run` serves the FastAPI compatibility entrypoint only. `make dev-ui` runs Vite and the backend together, with the backend fronting Vite at `:2024`; `make desktop` starts Electron and needs a backend separately. To accept local GitHub or Slack webhooks, `make tunnel NGROK_DOMAIN=<name>.ngrok-free.dev` exposes only `/webhooks/*`, because the development server does not authenticate its LangGraph API.

Python is async-only: implement the asynchronous path. If an interface requires a synchronous method, make it raise `NotImplementedError` rather than maintaining two implementations. Use absolute imports except that a same-package import may use one leading dot; do not use parent-relative imports.

## Registered entrypoints and ownership

`langgraph.json` is the deployment registration point. It declares five graph entrypoints—`agent`, `reviewer`, `analyzer`, `chat`, and `scheduler`—and the FastAPI HTTP application. Graph targets are deliberately thin `agent/graphs/` re-export modules, so normally change the owning implementation rather than a shim. The deployment checkpointer deletes expired state, sweeps every 60 minutes, and defaults to a 43,200-minute TTL.

| Entrypoint | Owning concern | Route a change here when it affects… |
| --- | --- | --- |
| `agent.graphs.agent:traced_agent` | Main coding graph in `agent/server.py` | agent assembly, models, prompts, skills, middleware, tools, and coding sandbox preparation |
| `agent.graphs.reviewer:traced_reviewer_agent` | Reviewer graph in `agent/reviewer.py` | diff-grounded findings, reviewer tools, review publication, and reviewer sandbox preparation |
| `agent.graphs.analyzer:traced_analyzer` | Analyzer graph in `agent/analyzer.py` | repository review-style analysis and learned guidance |
| `agent.graphs.chat:traced_chat_agent` | PR chat graph in `agent/chat.py` | dashboard PR chat, virtual PR files, and GitHub-backed read-only access |
| `agent.graphs.scheduler:get_scheduler` | Scheduler graph in `agent/scheduler.py` | cron routing, scheduled runs, stale-run repair, watch evaluation, background tasks, environment refresh, and cost or feedback work |
| `agent.webapp:app` | FastAPI composition in `agent/api/app.py` | dashboard/API surface, health, plan and workflow-approval APIs, CORS, or webhook ingress |

`agent.webapp:app` is a compatibility re-export of the application constructed in `agent/api/app.py`. At import time the app pins its event loop before queue workers can be built. Its lifespan validates the GitHub login allowlist, sandbox startup configuration, and local-development model configuration; shutdown closes cached models. Dashboard, plan, workflow-approval, Linear, Slack, health, and GitHub routers are included, then the dashboard UI is mounted. Credentialed CORS is installed only when configured origins are present, and configuration rejects `*`.

```mermaid
flowchart LR
    Trigger["Dashboard Slack Linear GitHub"] --> Api["FastAPI routes"]
    Api --> Dispatch["dispatch_agent_run"]
    Dispatch --> Run["Durable LangGraph run"]
    Run --> Graph["Agent or reviewer graph"]
    Cron["Cron tick"] --> Scheduler["Scheduler graph"]
    Scheduler --> Run
```

This diagram shows the shared work-routing boundary: interactive coding and review triggers create durable runs, while the scheduler either performs a maintenance task or launches scheduled agent work.

### Boundaries to preserve while changing an entrypoint

- The main coding-agent graph is stateless and is rebuilt by `get_agent`; thread continuity lives in sandbox state and thread metadata rather than in a long-lived graph instance. Read [Threads, Runs, and Durable State](concepts/threads-and-state.md) before changing identity, checkpoint, metadata, or concurrency behavior.
- Do not automatically replace an unreachable coding sandbox: it could discard uncommitted work. Reviewer preparation can opt into replacement because its checkout is recreated for each review. See [Thread-Scoped Sandbox Lifecycle](architecture/sandbox-lifecycle.md) and [Sandbox Provider Integration](integrations/sandbox-providers.md).
- The reviewer is non-mutating: its reviewer-specific tools manage findings and publication, not commits, pushes, or PR creation. PR chat is sandbox-less; the dashboard seeds `/pr/` virtual files, and it uses a repository-scoped GitHub App token for read-only repository access. See [Reviewer and Style Analyzer Graphs](architecture/reviewer-and-analyzer.md) and [Pull Request Review and Re-review](workflows/pr-review.md).
- Slack, Linear, GitHub, dashboard, and desktop callers share `dispatch_agent_run` for `agent` or `reviewer` durable-run creation. It assigns an invocation identity and streaming configuration, defaults to the `interrupt` multitask strategy, and rejects a prebuilt input combined with content, context, or source identities. See [Invoking Work Across Product Surfaces](workflows/invocation.md) and [Follow-Up, Interrupt, and Queue Handling](workflows/follow-up-messages.md).
- The scheduler is a single launch node. It routes a tick to reconciliation, baby-sit evaluation, background-task monitoring, environment refresh, session-cost, thread-feedback, or agent-cost work; otherwise it launches a scheduled agent run. Required missing identifiers return explicit result statuses rather than falling through to work.

## Route the change to its detailed guide

### Architecture, state, and extension seams

- [Runtime and Product Architecture](architecture/overview.md) — deployment registration, product surfaces, FastAPI composition, and durable dispatch.
- [Coding Agent Assembly](architecture/agent-graph.md) — `get_agent`, sandbox/backend resolution, model and profile choices, prompts, skills, tools, subagents, and middleware.
- [Agent Middleware Stack](architecture/middleware-stack.md) — ordering-sensitive guards, retries, timeouts, queues, fallbacks, and error handling.
- [Thread-Scoped Sandbox Lifecycle](architecture/sandbox-lifecycle.md) — sandbox binding, reconnection, reset and replacement rules, and environment freshness.
- [Threads, Runs, and Durable State](concepts/threads-and-state.md) — thread and invocation identity, checkpoints, metadata, stores, and durable-run semantics.
- [Models, Profiles, and Instructions](concepts/models-profiles-instructions.md) — model selection, profile snapshots, prompt composition, and skills.
- [Tooling and Capability Boundaries](concepts/tools.md) — curated, dynamic, integration, and administrative tools plus their policy constraints.

### Ingress, delivery, and product behavior

- [Invoking Work Across Product Surfaces](workflows/invocation.md) — authorization, source context, thread selection, durable dispatch, and completion across dashboard, desktop, GitHub, Slack, Linear, and schedules.
- [Follow-Up, Interrupt, and Queue Handling](workflows/follow-up-messages.md) — follow-up routing, interruption, pending work, and terminal handling.
- [Pull Request Creation and Delivery Controls](workflows/pr-creation.md) — GitHub access, branch and PR delivery, approvals, and CI linkage.
- [Pull Request Review and Re-review](workflows/pr-review.md) — automatic and manual review, canonical reviewer threads, findings, replies, and re-review gates.
- [Scheduled Work, Background Tasks, and CI Monitoring](workflows/scheduling-and-baby-sit.md) — schedules, reconciliation, background work, watches, costs, and feedback jobs.
- [Dashboard, Web UI, and Desktop Integration](integrations/dashboard-ui.md) — dashboard APIs, UI proxying and packaging, desktop supervision, and local projects.
- [Authentication, Authorization, and Credential Scope](concepts/auth-and-security.md) — sessions, OAuth, webhook verification, repository/actor gates, and credential exposure.
- [Observability and Connected Tool Integrations](integrations/observability-and-mcp.md) — tracing, gateways, MCP, and connection scopes.

### Configuration and serving

- [Configuration and Runtime Settings](operations/configuration.md) — environment registry, startup validation, models/gateway controls, team settings, and sandbox settings.
- [Build, Development, and Deployment Topology](operations/deployment.md) — local and deployed serving, dashboard builds, Docker/LangGraph wiring, webhook exposure, and desktop packaging.

## Validate the changed boundary, not the repository

**Focused validation is preferred; do not run the full local test suite.** Select the narrowest behavior-owning test and only the quality check relevant to the change. Tests should protect observable behavior and meaningful deterministic edge cases, not constants, prompts, internal call order, or other implementation trivia.

```bash
make test TEST_FILE=tests/github/test_open_pull_request.py
uv run pytest -vvv tests/path/to_test.py::test_name
make lint
make format-check
make typecheck
```

`make test` runs an existing file or directory, so do not invoke it with its default `tests/` value locally; use direct pytest for a node id. Pytest runs asynchronous tests in auto mode. The shared fixtures route `agent.store` through an in-memory store while retaining production serialization, clear the process-global TTL cache around each test, hide any locally built dashboard, supply a test GitHub login allowlist, and enable automatic review by default. Tests of those gates must override the corresponding fixture or stub explicitly.

Start with the owning family—such as `tests/agent/`, `tests/reviewer/`, `tests/sandbox/`, `tests/webhooks/`, `tests/dashboard/`, `tests/github/`, `tests/slack/`, `tests/middleware/`, or `tests/tools/`—then consult [Testing Strategy and Focused Validation](testing/overview.md) for fakes and package-specific commands. For a frontend-only change, use the narrow workspace check rather than a Python suite.

Use a single Playwright spec only when the change crosses real product boundaries:

```bash
pnpm install --frozen-lockfile
pnpm run test:e2e:install
pnpm exec playwright test tests/full_flow.spec.ts
```

The E2E harness runs real agent code, the local sandbox provider, local git, and the actual dashboard and Electron paths; it fakes the model and external SaaS HTTP boundaries. Browser tests run serially with one worker, and the separate desktop configuration selects `desktop.spec.ts`. The harness reuses its local web server, so a focused warm spec is the intended iteration loop rather than the browser suite. See [Testing Strategy and Focused Validation](testing/overview.md) for the test boundary and artifacts.
