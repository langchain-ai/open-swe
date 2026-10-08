---
type: engineering map
title: Open SWE Engineering Map
description: A task-routing guide for contributors that connects local setup and graph or API entrypoints to the architecture, workflow, integration, operations, and testing documentation that owns a change.
tags: [open-swe, contributor-guide, development, langgraph, testing]
verified:
  - by: openwiki/0.4.2
    at: 2026-10-08T08:17:40.044Z
sources:
  - id: openwiki-source-8037e2358a2c4f9b2c722a11
    resource: repo://AGENTS.md
  - id: openwiki-source-19973c87ca458faa5d03fecc
    resource: repo://docs/DEVELOPMENT.md
  - id: openwiki-source-5bbba7b2a8ea8360ff233d63
    resource: repo://langgraph.json
  - id: openwiki-source-012f2c78e3b1446dfc35803f
    resource: repo://Makefile
  - id: openwiki-source-4b1279a0a1e5ec2d55a4558a
    resource: repo://openswe/api/app.py
  - id: openwiki-source-813c25f6bac2408de322a1f5
    resource: repo://openswe/graphs/agent.py
  - id: openwiki-source-c9678be3f577e55e574a349f
    resource: repo://openswe/graphs/analyzer.py
  - id: openwiki-source-3bfcac4339fc43029fdaee09
    resource: repo://openswe/graphs/chat.py
  - id: openwiki-source-a9562865a4bff791686b49dd
    resource: repo://openswe/graphs/review_scout.py
  - id: openwiki-source-90b15fd6117126ebfe5b6b22
    resource: repo://openswe/graphs/reviewer.py
  - id: openwiki-source-6b99105488d7c23beda1e5ad
    resource: repo://openswe/graphs/scheduler.py
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
generated: { by: "openwiki/0.4.2", at: "2026-10-08T08:17:40.044Z" }
---

# Open SWE Engineering Map

Open SWE is an asynchronous software factory built on Deep Agents and LangGraph. It accepts engineering work from the dashboard, GitHub, Slack, Linear, and schedules; coding work normally runs in a persistent isolated sandbox and can deliver a pull request. This is a routing map, not required startup reading: inspect the owning code and focused behavioral tests first, then use the linked pages for cross-cutting context.

## Set up the smallest useful local loop

Python requires 3.14 or later and uses `uv`; the dashboard and desktop packages use the root `pnpm` workspace. Follow [Local Development](../docs/DEVELOPMENT.md) for credentials, PostgreSQL, webhook setup, and worktree state.

```bash
make install            # uv sync --extra dev
make dev                # LangGraph dev, FastAPI, and registered graphs on :2024
make dev-ui             # Vite on :3000 behind the :2024 backend
make run                # FastAPI-only uvicorn on :8000
make web                # dashboard dev server
make desktop            # Electron wrapper
```

Use `make dev` for any change that creates or executes a LangGraph run. It starts a local PostgreSQL container when `POSTGRES_URI` is not configured and refuses to share port 2024 with another process. `make run` is useful for FastAPI route work and live OpenAPI inspection, but has no LangGraph runtime, so it cannot create runs. For UI work, `make dev-ui` keeps the browser on `:2024` while the backend fronts Vite and its hot reload server.

Local webhook exposure is a security boundary: `make tunnel NGROK_DOMAIN=<name>.ngrok-free.dev` uses a policy that exposes only `/webhooks/*`, because `langgraph dev` runtime endpoints are unauthenticated. Do not replace it with a whole-port public tunnel.

### Contribution constraints

- Keep Python async-only. If an interface demands a synchronous method, make it raise `NotImplementedError` rather than maintaining parallel sync and async behavior.
- Put a dashboard endpoint in the owning feature package's `router`, not in a central dashboard route file. Keep model-facing prompt text in `openswe/resources/prompts/`.
- Before changing behavior, refactor the nearby design enough that the change belongs naturally. Preserve errors: Python failures use exceptions, and caught exceptions must be re-raised or logged.

## Runtime entrypoints: identify the owner before editing

`langgraph.json` is the deployment registry. It declares six graph targets and the `openswe.webapp:app` HTTP application. The modules in `openswe/graphs/` are intentionally thin re-exports; change the factory's owning module unless the public deployment target itself is changing. The registry also applies delete-based checkpointer expiration: a 60-minute sweep and a 43,200-minute default TTL.

| Registered target | Owns changes to | Route to |
| --- | --- | --- |
| `openswe.graphs.agent:traced_agent` | Coding-agent assembly, tools, models, prompts, middleware, and coding sandbox preparation | [Coding Agent Assembly and Execution](architecture/agent-graph.md), [Agent Middleware, Guards, and Recovery](architecture/middleware-stack.md), [Sandbox and Local Execution Lifecycle](architecture/sandbox-lifecycle.md) |
| `openswe.graphs.reviewer:traced_reviewer_agent` | PR review context, findings, publication, and reviewer-specific sandbox behavior | [Review, Analyzer, and Scout Graphs](architecture/reviewer-and-analyzer.md), [Pull Request Review and Finding Publication](workflows/pr-review.md) |
| `openswe.graphs.analyzer:traced_analyzer` | Repository review-style analysis and learned guidance | [Review, Analyzer, and Scout Graphs](architecture/reviewer-and-analyzer.md) |
| `openswe.graphs.review_scout:traced_review_scout` | Review walkthrough preparation and its dependencies | [Review, Analyzer, and Scout Graphs](architecture/reviewer-and-analyzer.md), [Pull Request Review and Finding Publication](workflows/pr-review.md) |
| `openswe.graphs.chat:traced_chat_agent` | Read-only PR chat, virtual PR files, and repository reads | [Review, Analyzer, and Scout Graphs](architecture/reviewer-and-analyzer.md), [Dashboard, Web UI, Desktop, and CLI Clients](integrations/dashboard-ui.md) |
| `openswe.graphs.scheduler:get_scheduler` | Cron dispatch, reconciliation, watches, background work, and refreshes | [Scheduling, Monitoring, and Background Work](workflows/scheduling-and-baby-sit.md) |
| `openswe.webapp:app` | FastAPI composition, dashboard APIs, health, CORS, and webhook ingress | [Runtime Architecture and Service Composition](architecture/overview.md), [Inbound Invocation and Durable Dispatch](workflows/invocation.md) |

The FastAPI app pins one event loop before queue-related initialization. During its lifespan it validates GitHub login and sandbox/model configuration, requires and migrates the database, then starts optional analytics, transcript, bridge, and UI-invalidation listeners. Failures in the optional services are logged and degrade cross-process behavior rather than blocking startup; database closure and listener shutdown occur on exit. Credentialed CORS accepts configured origins plus `open-swe://app` and rejects `*`.

## Route a change by responsibility

### Core architecture and runtime domains

- [Runtime Architecture and Service Composition](architecture/overview.md) — service composition, persistence, durable execution, and product surfaces.
- [Threads, Durable Runs, Workspaces, and Task State](concepts/threads-and-state.md) — canonical identity, state ownership, workspace selection, and durable-run metadata.
- [Coding Agent Assembly and Execution](architecture/agent-graph.md) — agent factory configuration, tool surfaces, model routing, and tracing.
- [Agent Middleware, Guards, and Recovery](architecture/middleware-stack.md) — ordering-sensitive preparation, fallback, retry, guard, and recovery behavior.
- [Sandbox and Local Execution Lifecycle](architecture/sandbox-lifecycle.md) and [Sandbox Providers, Snapshots, and GitHub Access](integrations/sandbox-providers.md) — per-thread acquisition, safe recovery, local execution, provider dependencies, and repository credentials.
- [Models, Profiles, Instructions, and Prompts](concepts/models-profiles-instructions.md), [Tool Surfaces, Dynamic Loading, and Authorization](concepts/tools.md), and [Context and Prompt Assembly](workflows/context-engineering.md) — model choice, prompt inputs, dynamic tools, restrictions, and authorization.
- [Coordinator and Worker Task Collaboration](workflows/collaborative-tasks.md) — delegation, shared-sandbox ownership, worker control, and task results.

### Ingress, delivery, and user-facing integrations

- [Inbound Invocation and Durable Dispatch](workflows/invocation.md) — dashboard, Slack, GitHub, Linear, desktop, and schedule triggers through normalization and run creation.
- [Follow-ups, Interrupts, and Completion Delivery](workflows/follow-up-messages.md) — concurrent messages, stop/cancel semantics, and owner notifications.
- [Implementation, Push, and Pull Request Creation](workflows/pr-creation.md) — edits through push detection, approvals, PR publication, and CI monitoring.
- [Pull Request Review and Finding Publication](workflows/pr-review.md) — on-demand or automatic review, findings, checks, and feedback loops.
- [Scheduling, Monitoring, and Background Work](workflows/scheduling-and-baby-sit.md) — scheduler routing, stale-run repair, CI watches, and cost or workspace refreshes.
- [Dashboard, Web UI, Desktop, and CLI Clients](integrations/dashboard-ui.md) — React/API boundaries, streaming interactions, Electron, local worktrees, and CLI behavior.
- [Identity, Credentials, and Security Boundaries](concepts/auth-and-security.md) — authentication, webhook verification, encrypted credentials, authorization, CORS, and audit logs.
- [MCP, External Tools, and Observability Integrations](integrations/observability-and-mcp.md) and [Incident Response and Human Review Integrations](integrations/incident-and-human-review.md) — scoped external tools, OAuth delivery, traces, incident work, and human review.

### Operations and configuration

- [Configuration, Workspaces, and Persistence](operations/configuration.md) — environment registry, migrations, workspace settings, secrets, and compatibility behavior.
- [Development, Build, Deployment, and Runtime Operations](operations/deployment.md) — dashboard build and mount behavior, deployment, tunnel constraints, CLI/desktop builds, and startup checks.

## Validate the changed boundary

Never run the full suite locally. Prefer an existing focused behavioral test; add a test only for a concrete missed regression, especially for authorization, integrity, or complicated state transitions.

```bash
make test TEST_FILE=tests/github/test_open_pull_request.py
uv run pytest -vvv tests/path/to_test.py::test_name
make lint
make format-check
make typecheck
```

`make test` runs an existing path with pytest. Python quality checks use Ruff (100-character line length) and `ty`; pytest is configured for automatic asyncio handling. Start in the focused family that owns the behavior—such as agent, reviewer, sandbox, webhook, dashboard, GitHub, Slack, middleware, or tools—and read its fixtures before introducing a mock. Shared fixtures provide an in-memory LangGraph Store through the production serialization path and helper patches for cross-module thread dependencies and tool access.

For a browser or desktop contract, use the Playwright harness only when the boundary is genuinely end to end:

```bash
pnpm install --frozen-lockfile
pnpm run test:e2e:install
pnpm exec playwright test tests/full_flow.spec.ts
```

The harness runs the real agent code, webhook routes, local temporary sandbox, local git remote, dashboard, and Electron flow. It fakes the model and external GitHub/Slack HTTP boundaries. Browser tests use one worker; the separate desktop configuration selects `desktop.spec.ts`. See [Testing Strategy and Focused Validation](testing/overview.md) for the focused suites, database requirements, harness boundaries, and artifact workflow.
