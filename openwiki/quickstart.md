---
type: quickstart hub
title: Open SWE Quickstart & Wiki Map
description: Entry point for choosing the Open SWE runtime, UI, integration, workflow, operations, or testing guide. It lists the supported local commands and the browser and desktop end-to-end test entrypoints.
tags: [open-swe, quickstart, development, langgraph, testing, wiki-map]
verified:
  - by: openwiki/0.4.2
    at: 2026-09-01T08:16:00.848Z
sources:
  - id: openwiki-source-328bde9e94017848bb09ba23
    resource: repo://agent/api/app.py
  - id: openwiki-source-921ec88ab63280d28b3dddb5
    resource: repo://agent/chat.py
  - id: openwiki-source-c48b309c5ca416cf623f0866
    resource: repo://agent/dispatch.py
  - id: openwiki-source-f8665996049065d2172f68e2
    resource: repo://agent/graphs/agent.py
  - id: openwiki-source-368e3a3da2c40119aead4316
    resource: repo://agent/graphs/chat.py
  - id: openwiki-source-1116ea2d477f08cf0f5b2ef0
    resource: repo://agent/graphs/scheduler.py
  - id: openwiki-source-276ab38291eb5741b4c2141c
    resource: repo://agent/reviewer.py
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
  - id: openwiki-source-f0a6e7dc03522b2682f88655
    resource: repo://tests/conftest.py
  - id: openwiki-source-859f98720585f4648f0f7b2e
    resource: repo://tests/e2e/playwright.config.ts
  - id: openwiki-source-4b944ec14a3d793a6f771403
    resource: repo://tests/e2e/playwright.desktop.config.ts
  - id: openwiki-source-7ef60dc4372e1a33c7728fe6
    resource: repo://tests/e2e/README.md
generated: { by: "openwiki/0.4.2", at: "2026-09-01T08:16:00.848Z" }
---

# Open SWE Quickstart & Wiki Map

Open SWE is an open-source framework for an organization's internal coding agent. It composes Deep Agents (`deepagents.create_deep_agent`) as a LangGraph application. Use this page to select the owner for a change; the linked subsystem page, source, and focused tests define the behavior.

## Choose the runtime first

`langgraph.json` is the deployment registration point. `make dev` serves its five graph entrypoints and the FastAPI application together. The modules under `agent/graphs/` are thin, stable re-exports of factories owned by the implementation modules; make behavior changes in the owner, not in a registration shim.

| Entry point | Registered target | Use it for |
|---|---|---|
| `agent` | `agent.graphs.agent:traced_agent` | Main coding work. `get_agent` builds a fresh graph; thread metadata and the sandbox hold per-thread continuity. |
| `reviewer` | `agent.graphs.reviewer:traced_reviewer_agent` | Non-mutating pull-request review and findings. |
| `analyzer` | `agent.graphs.analyzer:traced_analyzer` | Repository review-style analysis. |
| `chat` | `agent.graphs.chat:traced_chat_agent` | Read-only dashboard discussion of a PR, using seeded PR virtual files and GitHub-backed reads without a sandbox. |
| `scheduler` | `agent.graphs.scheduler:get_scheduler` | Scheduled reconciliation, watch checks, background work, session-cost refresh, and scheduled agent runs. |
| HTTP app | `agent.webapp:app` | Dashboard API, health, plan and workflow-approval routes, and Slack, Linear, and GitHub webhook ingress. |

Slack, Linear, GitHub, and dashboard requests converge on `dispatch_agent_run` for durable `agent` or `reviewer` runs. The default multitask strategy interrupts overlapping work. GitHub can automatically review opted-in repositories and authors when a PR is opened or marked ready for review. For authentication, deterministic thread IDs, input construction, and delivery behavior, go to [Invocation: Slack, Linear & GitHub Webhooks](workflows/invocation.md).

### Safety boundaries worth retaining

- **Coding versus review:** Reviewer tools do not mutate a repository; PR chat is also sandbox-less and read-only. See [Reviewer & Review-Style Analyzer Graphs](architecture/reviewer-and-analyzer.md) and [PR Review Workflow](workflows/pr-review.md).
- **Sandbox work is state:** Do not silently replace an unreachable coding-agent sandbox: it may contain uncommitted work. Reviewer runs may replace theirs because their checkout is recreated for each review. See [Sandbox Lifecycle & Providers](architecture/sandbox-lifecycle.md).
- **HTTP composition:** `agent.webapp:app` re-exports the FastAPI app assembled in `agent/api/app.py`. Its lifespan validates sandbox and local-development LLM configuration before serving, pins one event loop, and closes cached models at shutdown. Credentialed CORS requires configured dashboard origins and rejects `*`. See [Dashboard API & Web/Desktop UI](integrations/dashboard-ui.md).

## Local developer loop

Python dependencies use **uv** and the dashboard/desktop workspace uses **pnpm**.

```bash
make install            # uv sync --extra dev
make dev                # uv run langgraph dev --no-browser --port 2024
make run                # uv run uvicorn agent.webapp:app --reload --port 8000
make web                # pnpm run dev
make desktop            # pnpm run dev:desktop
```

Use `make dev` for graph, webhook-to-run, or dashboard-agent work. `make run` serves only FastAPI, so it is useful for route work but cannot run LangGraph graphs. `make desktop` starts the Electron wrapper; its backend must already be available.

The package requires Python `>=3.11`; the served LangGraph runtime is Python 3.12. Ruff uses a 100-character line length, pytest uses `asyncio_mode = "auto"`, and the application is async-only by convention: implement the async path rather than parallel sync and async implementations.

### Validate the owning contract

```bash
make test
make test TEST_FILE=tests/dashboard/test_dashboard_thread_api.py
uv run pytest -vvv tests/path/to_test.py::test_name
make lint
make format
make typecheck
```

`make test` uses `TEST_FILE` when that path exists and otherwise prints a skip message; `make integration_tests` behaves likewise for `tests/integration_tests/`. Shared pytest fixtures use an in-memory store through the production serialization path, clear the process-global TTL cache for each test, and enable automatic review by default unless a test overrides the gate.

For a change crossing a browser, webhook, sandbox/git, or Electron boundary, use the focused test plus Playwright:

```bash
pnpm install --frozen-lockfile
pnpm run test:e2e:install
pnpm run test:e2e            # browser suite
pnpm run test:e2e:desktop    # Electron suite
```

The harness runs real agent code, middleware, tools, local sandbox, local git remote, dashboard, and Electron paths. It replaces the LLM and external SaaS/credential HTTP boundaries with controlled fakes. The browser happy path proves Slack request → implementation → branch/PR → reply in the same Slack thread; the desktop suite drives the pinned local-agent flow. For serial execution, mock UI/session details, artifacts, and single-spec iteration, see [Testing Guide](testing/overview.md) and `tests/e2e/README.md`.

## Task-routing map

### Core runtime and state

- [System Architecture Overview](architecture/overview.md) — runtime topology, FastAPI composition, durable dispatch, graph boundaries, and state ownership.
- [Agent Graph & get_agent Factory](architecture/agent-graph.md) — model/backend resolution, curated tools, subagents, plan mode, and middleware assembly.
- [Middleware Stack](architecture/middleware-stack.md) — ordering, retries, timeouts, queued follow-ups, tool errors, and failure behavior.
- [Threads, Thread IDs & Persistence](concepts/threads-and-state.md) — durable identity, metadata, checkpoints, and access semantics.
- [Sandbox Lifecycle & Providers](architecture/sandbox-lifecycle.md) — reuse, reconnect/create behavior, and protection of working state.
- [Sandbox Provider Integrations](integrations/sandbox-providers.md) — provider selection and adding a provider.
- [Agent Tools (Curated Toolset)](concepts/tools.md) — tool availability, authorization, and extension work.
- [Models, Profiles, Team Defaults & Instructions](concepts/models-profiles-instructions.md) — model/profile/default resolution and prompt inputs.

### Integrations and workflows

- [Dashboard API & Web/Desktop UI](integrations/dashboard-ui.md) — authenticated dashboard APIs, UI proxying, Electron supervision, and local-project controls.
- [Invocation: Slack, Linear & GitHub Webhooks](workflows/invocation.md) — verification, gates, dispatch, replies, and follow-ups.
- [PR Creation & GitHub Delivery](workflows/pr-creation.md) — commits, pushes, PR creation, and delivery guards.
- [PR Review Workflow](workflows/pr-review.md) — findings, publication, auto-review, and reconciliation.
- [Scheduling, Cron & Baby-Sit CI Monitoring](workflows/scheduling-and-baby-sit.md) — schedule lifecycle and scheduler tasks.
- [Context Engineering: AGENTS.md, Source Context & Skills](workflows/context-engineering.md) — repository instructions, source context, and skills.
- [Authentication, Authorization & Security Boundaries](concepts/auth-and-security.md) — credentials, webhook verification, and dashboard authorization.
- [Observability & MCP Integrations](integrations/observability-and-mcp.md) — optional server-side integrations and their security boundary.

### Operations and tests

- [Configuration & Environment Variables](operations/configuration.md) — service, sandbox, model, authentication, webhook, and integration settings.
- [Local Dev, Build & Deployment](operations/deployment.md) — setup, build, and deployment operations.
- [Testing Guide](testing/overview.md) — Python, dashboard, desktop, and Playwright test ownership and diagnostics.
