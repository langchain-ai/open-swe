---
type: quickstart-hub
title: Open SWE Quickstart & Wiki Map
description: Start here to orient yourself in the Open SWE LangGraph application, select the appropriate local developer command, and route a change to its owning agent, dashboard, tools, sandbox, or testing guide.
tags: [open-swe, quickstart, langgraph, deepagents, development, wiki-map]
verified:
  - by: openwiki/0.4.2
    at: 2026-08-31T08:17:06.525Z
sources:
  - id: openwiki-source-328bde9e94017848bb09ba23
    resource: repo://agent/api/app.py
  - id: openwiki-source-921ec88ab63280d28b3dddb5
    resource: repo://agent/chat.py
  - id: openwiki-source-61ace7d4952db9ddb8316aeb
    resource: repo://agent/dashboard/routes.py
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
  - id: openwiki-source-7ef60dc4372e1a33c7728fe6
    resource: repo://tests/e2e/README.md
generated: { by: "openwiki/0.4.2", at: "2026-08-31T08:17:06.525Z" }
---

# Open SWE Quickstart & Wiki Map

Open SWE is an open-source framework for building an organization's internal coding agent. It composes Deep Agents (`deepagents.create_deep_agent`) into a LangGraph application. This is a routing hub, not a subsystem specification: follow the linked guide that owns a behavior before changing it, and use source and focused tests as the authority.

## Start with the runtime boundary

`langgraph.json` is the deployment registration point. It serves five named graphs and the FastAPI HTTP app together under `langgraph dev`; the graph modules in `agent/graphs/` are intentionally thin re-exports of their owning implementation modules.

| Entrypoint | Registered target | Ownership boundary |
|---|---|---|
| `agent` | `agent.graphs.agent:traced_agent` | Main coding work. `get_agent` builds a fresh, stateless graph per thread; sandbox and thread metadata carry continuity. |
| `reviewer` | `agent.graphs.reviewer:traced_reviewer_agent` | Non-mutating PR review and findings. |
| `analyzer` | `agent.graphs.analyzer:traced_analyzer` | Per-repository review-style analysis. |
| `chat` | `agent.graphs.chat:traced_chat_agent` | Read-only dashboard chat about one PR; it has no sandbox and uses seeded PR virtual files plus GitHub-backed repository reads. |
| `scheduler` | `agent.graphs.scheduler:get_scheduler` | A one-node cron router for reconciliation, watches, background tasks, session costs, or scheduled agent work. |
| HTTP app | `agent.webapp:app` | API composition, dashboard, health, and platform webhook routes. |

Slack, Linear, GitHub, and dashboard work enter the durable-run dispatch contract; its graph selection is normally `agent` or `reviewer`, and overlapping work defaults to the interrupt multitask strategy. GitHub can also start automatic PR review for opted-in repositories and authors on supported PR actions. For invocation-specific validation, thread identity, and follow-up behavior, use [Invocation: Slack, Linear & GitHub Webhooks](workflows/invocation.md).

### Boundaries that prevent unsafe changes

- **Agent assembly, tools, and middleware:** Start at [Agent Graph & get_agent Factory](architecture/agent-graph.md) for model/backend resolution, curated tools, subagent limits, plan mode, and middleware order. For the tool catalog and graph-specific availability, use [Agent Tools (Curated Toolset)](concepts/tools.md); do not duplicate Deep Agents built-ins in the application tool list.
- **Review is separate from coding:** The reviewer is deliberately non-mutating, while PR chat is sandbox-less and read-only. Route reviewer or analyzer work to [Reviewer & Review-Style Analyzer Graphs](architecture/reviewer-and-analyzer.md) and PR workflow changes to [PR Review Workflow](workflows/pr-review.md).
- **Sandbox state is work state:** The main agent does not silently replace an unreachable existing sandbox, because a replacement could hide the loss of uncommitted work. The reviewer can opt into replacement because it reconstructs its checkout for each review. Provider selection, startup configuration, timeouts, and recovery belong in [Sandbox Provider Integrations](integrations/sandbox-providers.md) and [Sandbox Lifecycle & Providers](architecture/sandbox-lifecycle.md).
- **Models and instructions are run configuration:** Route model/profile/default resolution and instruction precedence to [Models, Profiles, Team Defaults & Instructions](concepts/models-profiles-instructions.md), rather than embedding preference logic in a tool or webhook.
- **HTTP composition is centralized:** `agent.webapp:app` re-exports the app constructed in `agent/api/app.py`. The lifespan pins an event loop, validates sandbox and local-development LLM configuration before serving, and closes cached models at shutdown. It mounts the dashboard, plan, workflow-approval, platform webhook, and health routers. Credentialed CORS is enabled only for configured dashboard origins; `*` is rejected.

## Local developer loop

Python dependencies are managed with **uv**; the JavaScript workspace uses **pnpm**. Use `make dev` when a change needs a graph runtime. `make run` starts only the FastAPI app, which is useful for route work but cannot execute LangGraph graphs.

```bash
make install            # uv sync --extra dev
make dev                # uv run langgraph dev --no-browser --port 2024
make run                # uv run uvicorn agent.webapp:app --reload --port 8000
make web                # pnpm run dev
make desktop            # pnpm run dev:desktop
```

The project requires Python `>=3.11`; the served LangGraph runtime is Python 3.12. Ruff uses a 100-character line length, pytest uses `asyncio_mode = "auto"`, and the repository is async-only by convention—implement the async path rather than parallel sync and async implementations.

### Validate at the owner boundary

```bash
make test
make test TEST_FILE=tests/dashboard/test_dashboard_thread_api.py
uv run pytest -vvv tests/path/to_test.py::test_name
make lint
make format
make typecheck
```

`make test` runs the supplied `TEST_FILE` when it exists and prints a skip message otherwise; `make integration_tests` does the same for `tests/integration_tests/`. The shared pytest fixtures provide an in-memory store through the real `agent.store` serialization path and clear the process-wide TTL cache around every test. They also enable auto-review by default in the no-live-store test environment, so an auto-review gate test must override that stub.

For dashboard, desktop, or cross-boundary work, do not stop at pytest by default. [Testing Guide](testing/overview.md) maps Python subsystem tests, dashboard Vitest, desktop Node tests, and the Playwright harness. The e2e harness runs real application code with a local sandbox and local git remote while faking the LLM and external SaaS boundaries; use `pnpm run test:e2e` or `pnpm run test:e2e:desktop` when the changed contract crosses those boundaries.

## Task-routing map

### Core agent and state

- [System Architecture Overview](architecture/overview.md) — top-level components and request paths.
- [Agent Graph & get_agent Factory](architecture/agent-graph.md) — factory assembly, main-agent capability boundaries, tools, subagents, and plan mode.
- [Middleware Stack](architecture/middleware-stack.md) — ordering, retries, model timeouts, queued follow-ups, tool errors, and sandbox failures.
- [Threads, Thread IDs & Persistence](concepts/threads-and-state.md) — continuity, metadata, durable runs, and access semantics.
- [Sandbox Lifecycle & Providers](architecture/sandbox-lifecycle.md) — reuse/reconnect/create behavior and preservation of working state.
- [Sandbox Provider Integrations](integrations/sandbox-providers.md) — provider selector, LangSmith execution behavior, and adding a provider.
- [Agent Tools (Curated Toolset)](concepts/tools.md) — curated and optional tools, authorization, and extension work.
- [Models, Profiles, Team Defaults & Instructions](concepts/models-profiles-instructions.md) — model choice, profiles, defaults, and prompt inputs.

### User-facing surfaces and automation

- [Dashboard API & Web/Desktop UI](integrations/dashboard-ui.md) — GitHub login, profiles, repository settings, reviews, skills, schedules, thread access, UI proxy, and Electron boundary. Add an HTTP endpoint in `agent/dashboard/routes.py`; keep its policy and storage behavior in the focused module that the route delegates to.
- [Invocation: Slack, Linear & GitHub Webhooks](workflows/invocation.md) — platform gates, input construction, dispatch, and replies.
- [PR Creation & GitHub Delivery](workflows/pr-creation.md) — commits, pushes, PR creation, and delivery guards.
- [PR Review Workflow](workflows/pr-review.md) — findings, publication, auto-review, and reconciliation.
- [Scheduling, Cron & Baby-Sit CI Monitoring](workflows/scheduling-and-baby-sit.md) — schedule lifecycle and scheduler tasks.
- [Context Engineering: AGENTS.md, Source Context & Skills](workflows/context-engineering.md) — repository instructions, source context, and skills.
- [Authentication, Authorization & Security Boundaries](concepts/auth-and-security.md) — credentials, webhook verification, and dashboard authorization.
- [Observability & MCP Integrations](integrations/observability-and-mcp.md) — optional server-side integrations and their security boundary.

### Configuration, operations, and tests

- [Configuration & Environment Variables](operations/configuration.md) — service, sandbox, model, auth, webhook, and integration settings.
- [Local Dev, Build & Deployment](operations/deployment.md) — setup, build, and deployment operations.
- [Testing Guide](testing/overview.md) — focused test ownership, fixture isolation, quality gates, and browser/Electron e2e.
