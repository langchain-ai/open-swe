---
type: contributor guide
title: Open SWE codebase guide
description: Start here to select the Open SWE owner, entrypoint, local runtime, and focused validation for a safe change. This guide routes coding agents to the detailed architecture, workflow, integration, operations, and testing pages.
tags: [open-swe, contributor-guide, development, langgraph, testing]
verified:
  - by: openwiki/0.4.2
    at: 2026-10-10T08:14:14.686Z
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
  - id: openwiki-source-70b814b26d317c2b15c4a4fb
    resource: repo://openswe/chat.py
  - id: openwiki-source-1685d34aae8025be9332f45a
    resource: repo://openswe/dispatch.py
  - id: openwiki-source-813c25f6bac2408de322a1f5
    resource: repo://openswe/graphs/agent.py
  - id: openwiki-source-3bfcac4339fc43029fdaee09
    resource: repo://openswe/graphs/chat.py
  - id: openwiki-source-a9562865a4bff791686b49dd
    resource: repo://openswe/graphs/review_scout.py
  - id: openwiki-source-90b15fd6117126ebfe5b6b22
    resource: repo://openswe/graphs/reviewer.py
  - id: openwiki-source-6b99105488d7c23beda1e5ad
    resource: repo://openswe/graphs/scheduler.py
  - id: openwiki-source-96bcad07b4fe7078402bc2b8
    resource: repo://openswe/reviewer.py
  - id: openwiki-source-1b32e9f41fa7e64702b380f6
    resource: repo://openswe/sandboxes/lifecycle.py
  - id: openwiki-source-685dc33e7199aa1f6e402f7a
    resource: repo://openswe/scheduler.py
  - id: openwiki-source-919e16feae379651f2cbc1c9
    resource: repo://openswe/server.py
  - id: openwiki-source-3bd49e1c2bb74350a7519268
    resource: repo://openswe/webapp.py
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
generated: { by: "openwiki/0.4.2", at: "2026-10-10T08:14:14.686Z" }
---

# Open SWE codebase guide

Open SWE is an asynchronous software factory built on Deep Agents and LangGraph. The custom FastAPI app is the product and integration boundary; durable graph runs do coding and review work, usually in per-thread sandboxes. Treat repository source and tests as authoritative. Use this page to identify the owning boundary, then read the linked detailed page and its local callers, callees, and behavioral tests before changing code.

## Start the local runtime

Install Python 3.14+ with `uv`; dashboard development also needs Node 22.22.2+ and `pnpm`. For the normal backend loop, configure `.env` with the required credentials and run:

```bash
make install
make build-dashboard
make dev
```

`make dev` starts a local PostgreSQL container when `POSTGRES_URI` is unset, then runs `langgraph dev` on port 2024. It serves the graphs, FastAPI API, and a built dashboard together. Use `make dev-ui` instead while editing the dashboard: it runs Vite on port 3000 and has the backend front it at port 2024. `make run` is a FastAPI-only Uvicorn process on port 8000; it does not include the LangGraph runtime, so it cannot create graph runs.

Use `make tunnel NGROK_DOMAIN=<name>.ngrok-free.dev` only for local webhook work. Its supplied policy exposes `/webhooks/*` rather than the unauthenticated LangGraph development API. For a full credential, database, OAuth, tunnel, and worktree-state setup, follow [Development, deployment, and maintenance](operations/deployment.md) and [Configuration and customization](operations/configuration.md).

Python code is async-only. If an interface requires a synchronous method, make that method raise `NotImplementedError` rather than adding a second implementation. Preserve strong types; do not use `Any`/`any` to silence a type error.

## Locate the runtime owner

`langgraph.json` is the deployable registration boundary. It selects Python 3.14, loads `.env`, mounts `openswe.webapp:app`, and exposes five graph keys through thin `openswe/graphs/` re-export modules. Change the owning module below rather than a shim unless moving the public entrypoint. The deployed checkpointer deletes expired data, sweeping every 60 minutes with a default TTL of 43,200 minutes.

| Graph or surface | Owning code | Start here when changing… |
| --- | --- | --- |
| `agent` — `openswe.graphs.agent:traced_agent` | `openswe/server.py` | coding-agent assembly, models, tools, prompts, middleware, skills, subagents, or sandbox-backed execution |
| `reviewer` — `openswe.graphs.reviewer:traced_reviewer_agent` | `openswe/reviewer.py` | pull-request findings, diff validation, publication, or review checkout behavior |
| `review-scout` — `openswe.graphs.review_scout:traced_review_scout` | `openswe/review_scout/` | PR walkthrough planning and review-scout lifecycle |
| `chat` — `openswe.graphs.chat:traced_chat_agent` | `openswe/chat.py` | dashboard PR chat, seeded PR virtual files, and repository reads |
| `scheduler` — `openswe.graphs.scheduler:get_scheduler` | `openswe/scheduler.py` | cron tasks, watches, stale-run reconciliation, background work, refreshes, or scheduled agent launches |
| `openswe.webapp:app` | `openswe/api/app.py` | service composition, dashboard/API routes, health, CORS, webhook ingress, mounted MCP/remote-runtime surfaces, or dashboard assets |

The coding-agent factory is stateless: per-thread continuity belongs to LangGraph checkpoints and thread metadata plus the sandbox. Do not use a process-local graph instance as persistent state. An existing unreachable coding sandbox must not be silently replaced because it may contain uncommitted work; reviewer and scout checkouts are re-derived, so their recovery rules differ. Read [System architecture and runtime boundaries](architecture/overview.md) before changing a cross-boundary path.

## Route the change

### Agent, state, sandbox, and configuration

- [Coding agent assembly and execution](architecture/agent-graph.md) — resolve model, backend, context, tool and middleware composition, and Deep Agents execution.
- [Agent and reviewer middleware stack](architecture/middleware-stack.md) — ordering-sensitive guards, retries, model fallback, approvals, queues, and tool failure handling.
- [Sandbox and backend lifecycle](architecture/sandbox-lifecycle.md) — acquisition, reconnection, snapshots, safe replacement, proxies, bridge mode, and recovery.
- [Persistence, workspaces, threads, and tasks](architecture/persistence-workspaces-and-tasks.md) and [Threads, runs, messages, and artifacts](concepts/threads-and-state.md) — decide whether state belongs in checkpoints/store, PostgreSQL, thread metadata, workspace records, or task state.
- [Models, profiles, instructions, and prompts](concepts/models-profiles-instructions.md), [Tool surfaces and dynamic capability policy](concepts/tools.md), and [Configuration and customization](operations/configuration.md) — preserve settings precedence, model/provider selection, prompt ownership, credential scope, and dynamic tool authorization.
- [Sandbox provider implementations](integrations/sandbox-providers.md) and [CLI bridge and remote reviewer runtime](integrations/local-bridge-and-remote-runtime.md) — provider registry changes, local checkout execution, and remote reviewer boundaries.

### Invocation, integrations, and user-visible workflows

- [Invocations from UI, webhooks, and schedules](workflows/invocation.md) — use this first for a new or changed dashboard, GitHub, Slack, Linear, desktop, API, or automation trigger. It covers admission, identity, workspace/thread routing, input construction, and durable dispatch.
- [Dashboard, web UI, and desktop surfaces](integrations/dashboard-ui.md) — FastAPI dashboard APIs, React/Nitro behavior, sessions, UI state, and Electron.
- [GitHub, Slack, and Linear integration boundaries](integrations/github-slack-and-linear.md) — signed webhooks, event claiming, outbound clients, and provider-specific replies.
- [Identity, authorization, and credential scope](concepts/auth-and-security.md) — session, OAuth/App tokens, webhook verification, encrypted credentials, principals, and sandbox proxy boundaries.
- [Context construction and repository guidance](workflows/context-engineering.md) — source context, scoped `AGENTS.md`, instructions, skills, attachments, and integration context.
- [Follow-ups, interruption, queues, and cancellation](workflows/follow-up-messages.md) — do not change active-thread behavior without preserving interruption and queued-work semantics.
- [Implementation, push approval, and pull-request delivery](workflows/pr-creation.md), [Pull-request review workflow](workflows/pr-review.md), and [Human and expedited Slack review](workflows/human-review-and-merge.md) — PR delivery, review findings, approval/merge gates, replies, and human workflow.
- [Schedules, automations, background tasks, and baby-sit](workflows/scheduling-and-baby-sit.md) — cron records, automation triggers, watch evaluation, locks, retries, and maintenance jobs.
- [MCP, model gateway, and observability integrations](integrations/observability-and-mcp.md) — configured MCPs, provider/gateway behavior, tracing, analytics, and audit boundaries.

## Preserve high-risk boundaries

- Route an agent or reviewer trigger through `dispatch_agent_run`/durable run creation rather than constructing a one-off LangGraph run. The shared path normalizes input, records invocation context, defaults to interrupt multitasking with synchronous checkpoints and resumable streaming, and makes externally created runs observable in the dashboard. A caller must provide either a prebuilt run input or raw content/context/identity inputs, never both.
- Preserve raw-body signature verification, event claims, workspace routing, and authorization before webhook side effects. A successfully acknowledged provider delivery does not necessarily mean a run was created.
- The reviewer prepares a checkout and restricts itself to finding-oriented tools; it has no commit, push, or PR-opening tools. PR chat has no sandbox: the dashboard seeds `/pr/` virtual files and it uses a repository-scoped GitHub App token for read-oriented access, not a user credential.
- Treat settings and secrets as operational interfaces. Environment defaults, instance settings, workspace overrides, and supported caller-specific profile/thread/run values have defined precedence; do not move secrets into workspace scripts or sandbox create parameters.
- Add browser endpoints to the feature-owning router, not a catch-all dashboard route, so the standard request-ID, audit/tracing, same-origin mutation, and authorization layers continue to apply.

## Validate the changed behavior

Never run the entire suite merely as a default. Start with the narrowest existing behavioral test that owns the changed boundary, then add a focused regression only when it catches a concrete behavior existing coverage misses. Preserve complete failure output.

```bash
make test TEST_FILE=tests/sandbox/test_sandbox_state.py
uv run pytest -vvv tests/sandbox/test_sandbox_state.py::test_sandbox_proxy_retries_failed_startup
make lint
make typecheck
```

`make test` accepts an existing file or directory; use direct `uv run pytest` for a node ID. Pytest uses asyncio auto mode. Shared fixtures route Store operations through an in-memory implementation while retaining the production serialization path, so prefer the existing fixture rather than a mock-only test.

Select a suite by behavioral owner: `tests/agent/` for assembly and middleware, `tests/sandbox/` for connection/recovery, `tests/reviewer/` for findings and review outcomes, `tests/webhooks/` for terminal/reply behavior, and the matching dashboard, integration, GitHub, Slack, Linear, tool, or thread suite for its surface. Run `make lint` only when Python lint/format coverage is relevant, and `make typecheck` when type coverage is relevant. For a UI or Electron change, start with the owning package:

```bash
pnpm --filter open-swe-dashboard run test
pnpm --dir desktop run test
```

Escalate to one Playwright spec only for a real cross-boundary contract. The E2E harness runs `langgraph dev`, real agent/tool/middleware paths, a local sandbox, local git, the actual dashboard, and Electron; it fakes the LLM and external SaaS/credential HTTP seams. Install Chromium once, then target one existing spec. Browser tests run serially with one worker; desktop runs use the separate Electron configuration.

```bash
pnpm install --frozen-lockfile
pnpm run test:e2e:install
pnpm exec playwright test tests/full_flow.spec.ts
pnpm run test:e2e:desktop
```

See [Testing strategy and focused validation](testing/overview.md) for fixture ownership, E2E fakes, test artifacts, and focused commands.
