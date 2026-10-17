---
type: contributor guide
title: Open SWE Engineering Guide
description: Set up Open SWE, select the runtime and change owner, and validate only the boundary you changed. This guide routes contributors to the architecture, domain, integration, workflow, operations, and testing pages for deeper context.
tags: [open-swe, contributor-guide, development, runtime, testing]
verified:
  - by: openwiki/0.4.2
    at: 2026-10-17T08:12:29.923Z
sources:
  - id: openwiki-source-8037e2358a2c4f9b2c722a11
    resource: repo://AGENTS.md
  - id: openwiki-source-5bbba7b2a8ea8360ff233d63
    resource: repo://langgraph.json
  - id: openwiki-source-012f2c78e3b1446dfc35803f
    resource: repo://Makefile
  - id: openwiki-source-e5b7abf11bb2daa504eef827
    resource: repo://mda/README.md
  - id: openwiki-source-4b1279a0a1e5ec2d55a4558a
    resource: repo://openswe/api/app.py
  - id: openwiki-source-813c25f6bac2408de322a1f5
    resource: repo://openswe/graphs/agent.py
  - id: openwiki-source-a9562865a4bff791686b49dd
    resource: repo://openswe/graphs/review_scout.py
  - id: openwiki-source-90b15fd6117126ebfe5b6b22
    resource: repo://openswe/graphs/reviewer.py
  - id: openwiki-source-2ccb7a88c95f9b2d94544b63
    resource: repo://openswe/remote_runtime/graph.py
  - id: openwiki-source-932730a18e85495ceba5df6d
    resource: repo://openswe/threads/router.py
  - id: openwiki-source-3bd49e1c2bb74350a7519268
    resource: repo://openswe/webapp.py
  - id: openwiki-source-5b54a58d1b51cd490b0e7162
    resource: repo://package.json
  - id: openwiki-source-859f98720585f4648f0f7b2e
    resource: repo://tests/e2e/playwright.config.ts
  - id: openwiki-source-4b944ec14a3d793a6f771403
    resource: repo://tests/e2e/playwright.desktop.config.ts
  - id: openwiki-source-7ef60dc4372e1a33c7728fe6
    resource: repo://tests/e2e/README.md
generated: { by: "openwiki/0.4.2", at: "2026-10-17T08:12:29.923Z" }
---

# Open SWE Engineering Guide

Open SWE is an asynchronous software factory built with Deep Agents and LangGraph. It receives work from the dashboard, GitHub, Slack, Linear, and schedules; coding work uses a thread-scoped sandbox and may deliver a pull request, while review and review-scout work have their own graph entrypoints.

This is a change-routing hub, not a replacement for code review: **read the owning source and its focused tests first.** OpenWiki is optional just-in-time context; source code and tests are authoritative.

## Before changing code

1. **Till the soil first.** Refactor the immediately surrounding code so the change fits the existing design rather than bolting on a special case.
2. Find the owner using the routing map below, then trace callers, state owners, and the nearest behavioral test.
3. Preserve repository conventions that affect design: async-only implementations, strong Python/TypeScript types without `Any`/`any`, PostgreSQL rather than the LangGraph Store for application state, and feature-owned API routers rather than additions to a central dashboard route file.
4. Add or extend a test only for a concrete behavioral regression that existing coverage would miss. Authorization, data integrity, and difficult state transitions deserve focused coverage; mechanical tests of structure, constants, prompts, or mocks do not.

## Local development

The backend requires Python 3.14+ and `uv`; the dashboard and desktop workspace require Node 22.22.2+ and `pnpm`. Local agent runs also require a PostgreSQL database and a local Managed Deep Agents (MDA) runtime. Follow [`docs/DEVELOPMENT.md`](../docs/DEVELOPMENT.md) for GitHub/Slack app setup, the required `.env` values, tunnel safety, worktree state, and full local verification.

```bash
uv venv
source .venv/bin/activate
uv sync --all-extras
pnpm install --frozen-lockfile

make build-dashboard  # builds the bundled dashboard
make dev-mda          # terminal 1: Managed Deep Agents on :2025
make dev              # terminal 2: LangGraph, backend, and bundled dashboard on :2024
```

`make dev` starts the local PostgreSQL container when `POSTGRES_URI` is not configured, refuses to share an occupied port 2024, and serves the LangGraph runtime, FastAPI application, and built dashboard together. Use `make dev-ui` instead when changing the dashboard: it runs Vite on `:3000` while the backend at `:2024` fronts it, so the browser stays on the backend origin. `make desktop` starts Electron, but its backend must already be running.

`make run` is deliberately different: it runs `openswe.webapp:app` under Uvicorn on `:8000`, without the LangGraph runtime. It is appropriate for API documentation or route work, not for creating runs.

For local GitHub and Slack webhooks, use the fixed-domain tunnel command below. The supplied ngrok policy exposes only `/webhooks/*`; do not expose the complete `langgraph dev` port because its Agent Server endpoints do not have authentication.

```bash
make tunnel NGROK_DOMAIN=<name>.ngrok-free.dev
```

## Runtime orientation

`langgraph.json` declares the Python 3.14 deployment runtime, three graph entrypoints, the FastAPI app, Agent Server authentication, and delete-based checkpoint cleanup (a 60-minute sweep and 43,200-minute default TTL). The graph modules are intentionally thin exports: make behavior changes in their owning implementation, not in the registration shim.

| Entrypoint | Responsibility | Start here |
| --- | --- | --- |
| `openswe.graphs.agent:traced_agent` | Coding-agent graph | [Coding Agent Assembly](architecture/agent-graph.md), [Middleware and Failure Semantics](architecture/middleware-stack.md), and [Tool Surfaces](concepts/tools.md) |
| `openswe.graphs.reviewer:traced_reviewer_agent` | Read-only pull-request reviewer | [Review and Review-Scout Graphs](architecture/reviewer-and-review-scout.md) and [Pull Request Review Lifecycle](workflows/pr-review.md) |
| `openswe.graphs.review_scout:traced_review_scout` | Review-scout graph | [Review and Review-Scout Graphs](architecture/reviewer-and-review-scout.md) |
| `openswe.webapp:app` | FastAPI composition and custom HTTP surfaces | [System Architecture and Runtime Boundaries](architecture/overview.md), [Managed Runtime and Thread Routing](architecture/remote-runtime-and-threads.md), and [Configuration and Startup Validation](operations/configuration.md) |

Every `agent`, `reviewer`, and `review-scout` thread sends its model loop and checkpoints to Managed Deep Agents; the backend retains the thread sandbox. The backend requires `MDA_RUNTIME_URL` and `REMOTE_RUNTIME_TOKEN_SECRET`, and its thread router uses the PostgreSQL thread index to direct those runs to MDA. Changes to graph middleware or tools reach MDA execution through the remote-runtime graph and hooks, so identify whether a change belongs to backend routing, the remote graph, or the MDA project before editing.

The FastAPI lifespan validates login, sandbox, local-development LLM, remote-runtime, and database configuration; runs migrations; starts reporting, event/listener, invalidation, and job infrastructure; and tears those resources down on shutdown. The app places `ThreadRouter` ahead of core Agent Server `/threads` and `/runs` routes, then mounts dashboard, webhook, sandbox, remote-runtime hook, optional MCP, and dashboard UI surfaces. See [System Architecture and Runtime Boundaries](architecture/overview.md) before modifying app composition or startup behavior.

## Route the change

### Agent, runtime, and state

- [Coding Agent Assembly](architecture/agent-graph.md) — graph construction, prompts, models, tools, skills, subagents, and coding preparation.
- [Middleware and Failure Semantics](architecture/middleware-stack.md) — retries, model/tool failures, queues, policy guards, and user-visible failures.
- [Managed Runtime and Thread Routing](architecture/remote-runtime-and-threads.md) — MDA routing, signed hooks, local recovery boundaries, and thread APIs.
- [Thread Sandbox Lifecycle](architecture/sandbox-lifecycle.md) — sandbox ownership, snapshots, reconnection, and the rule against silently replacing an unreachable coding sandbox that may contain uncommitted work.
- [Threads, Persistence, and Run State](concepts/threads-and-state.md) — canonical identities, metadata, transcripts, checkpoints, and completion state.
- [Models, Profiles, and Instructions](concepts/models-profiles-instructions.md) — configuration precedence, model selection, and assembled instructions.
- [Tool Surfaces and Capability Gating](concepts/tools.md) — tool authorization, modes, and safe extension points.

### Inbound work and product behavior

- [Inbound Invocation and Durable Dispatch](workflows/invocation.md) — GitHub, Slack, Linear, dashboard, desktop, and scheduled requests through normalization, threads, dispatch, and completion.
- [Follow-Ups, Interrupts, and Completion Replies](workflows/follow-up-messages.md) — active-thread delivery, interruption, queueing, stopping, and owner-thread replies.
- [Implementation and Pull Request Creation](workflows/pr-creation.md) — sandbox edits, push/PR guards, approvals, and CI handoff.
- [Pull Request Review Lifecycle](workflows/pr-review.md) — review triggers, findings, publication, webhook reconciliation, and settlement.
- [Scheduling, Monitoring, and Baby-Sit](workflows/scheduling-and-baby-sit.md) — wakes, background tasks, CI monitoring, retries, and costs.
- [Task and Worker Coordination](workflows/task-coordination.md) — durable multi-thread work, ownership, worker lifecycle, and inheritance.
- [Context Assembly and Prompt Engineering](workflows/context-engineering.md) — external events, repository guidance, skills, source context, and workspace context.
- [Human and Expedited Review](workflows/human-review.md) and [Incident Response Workflow](workflows/incidents.md) — Slack-centered approval and incident-specific behavior.

### Integration, access, and operations

- [Dashboard, Web UI, and Desktop](integrations/dashboard-ui.md) — dashboard APIs, server/UI proxying, invalidation, and Electron.
- [Authentication, Credentials, and Safety Boundaries](concepts/auth-and-security.md) — login, webhook verification, tokens, encryption, and authorization.
- [Workspaces, Repository Routing, and Access](concepts/workspaces-and-access.md) — workspace resolution, repository/channel bindings, settings, and credentials.
- [Sandbox Providers and External Execution](integrations/sandbox-providers.md) — provider selection and provider-specific behavior.
- [MCP, Observability, and External Tool Loaders](integrations/observability-and-mcp.md) — MCP, tracing, and optional external integrations.
- [Configuration and Startup Validation](operations/configuration.md) — environment and persisted settings, secrets, database/runtime/sandbox requirements, and safe customization.
- [Development, Packaging, and Deployment](operations/deployment.md) — development topology, packaging, dashboard builds, migrations, and lifecycle prerequisites.

## Focused validation only

**Never run the full test suite locally.** Choose the smallest behavioral test that owns the changed boundary, preserve its failure output, then run only relevant quality checks. Prefer extending a nearby behavioral test over creating fixtures or broad mock harnesses.

```bash
make test TEST_FILE=tests/path/to_test.py
uv run pytest -vvv tests/path/to_test.py::test_name
make lint
make format-check
make typecheck
```

`make test` runs the path passed as `TEST_FILE`; direct `pytest` is appropriate for a single node id. `make lint` runs Ruff checking and format diff; `make format-check` checks formatting only; `make typecheck` checks `openswe` and `tests` with `ty`. Use `make mda-boundary` when a change affects MDA imports or dependency separation.

For dashboard or desktop changes, run the narrow package/workspace check rather than an unrelated backend suite. For an end-to-end contract, run one relevant Playwright spec—not the whole E2E suite:

```bash
pnpm install --frozen-lockfile
pnpm run test:e2e:install
pnpm exec playwright test tests/full_flow.spec.ts
```

The Playwright harness runs real backend code, MDA runtime, agent tools and middleware, a temporary local sandbox, local git, the actual dashboard, and Electron paths. It fakes only the LLM and external SaaS HTTP boundaries. Browser tests are serial with one worker; the desktop configuration selects only `desktop.spec.ts`. See [Testing Strategy and Focused Validation](testing/overview.md) for fixtures, targeted test families, fakes, artifacts, and frontend-specific commands.
