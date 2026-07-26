---
type: Project Guide
title: Open SWE quickstart
description: Entry point to Open SWE, an internal coding-agent framework built on LangGraph and Deep Agents with Slack, Linear, GitHub, and dashboard workflows.
resource: /README.md
tags: [open-swe, architecture, agent-platform, quickstart]
---
# Open SWE quickstart

Open SWE is a framework for an organization-specific coding agent. It combines a LangGraph runtime, Deep Agents-based coding and review graphs, isolated sandboxes, and collaboration surfaces in Slack, Linear, GitHub, and a web dashboard. The intended outcome is an agent that can receive engineering context, work in a repository, and report or publish its result without giving its credentials directly to the execution environment.

Start here when orienting to the repository:

- [Runtime architecture](runtime-architecture.md) explains the graphs, FastAPI composition, durable dispatch contract, and per-thread execution state.
- [Workflows](workflows.md) explains how coding, review, planning, approvals, and review-style learning fit together.
- [Integrations and security](integrations-security.md) explains sandbox isolation, identities, webhook gates, and optional server-side tools.
- [Dashboard](dashboard.md) explains the authenticated UI, its FastAPI API boundary, and admin/user management surfaces.
- [Operations and quality](operations-quality.md) explains development commands, CI, E2E verification, reviewer evaluation, and deployment assets.

## Product model

The primary coding graph is assembled with `deepagents.create_deep_agent` in `agent/server.py`. It receives source context from a Slack thread, Linear issue, GitHub interaction, or dashboard chat; works against a sandbox associated with the thread; and uses a deliberately curated set of collaboration and research tools. The [runtime architecture](runtime-architecture.md) describes the graph factory and durable thread behavior.

Open SWE also separates code review from code modification. The read-only reviewer graph prepares a PR-specific checkout and changed-line context before publishing findings, while the analyzer graph learns a repository-specific review style. These user-facing flows are documented in [workflows](workflows.md), and their evaluator is documented in [operations and quality](operations-quality.md).

The web dashboard is not a separate backend: it is a Vite/TanStack client over FastAPI endpoints mounted in the same application as the webhooks. It surfaces agent threads, plans, schedules, review administration, profiles, and workspace settings; see [dashboard](dashboard.md).

## Repository map

| Area | Source anchors | Why it matters |
|---|---|---|
| Agent runtime | `agent/server.py`, `agent/graphs/`, `langgraph.json` | Assembles the coding graph and registers deployed assistants. |
| Ingress and dispatch | `agent/api/app.py`, `agent/webhooks/`, `agent/dispatch.py` | Verifies incoming events and creates durable LangGraph runs. |
| Review system | `agent/reviewer.py`, `agent/review/`, `agent/analyzer.py` | Implements diff-scoped review and per-repository review style. |
| Dashboard | `agent/dashboard/`, `ui/src/` | Owns OAuth/session-backed management APIs and their client UI. |
| Execution boundary | `agent/utils/sandbox.py`, `agent/integrations/`, `Dockerfile` | Selects isolated execution providers and configures the sandbox image. |
| Verification | `tests/`, `tests/e2e/`, `evals/reviewer/`, `.github/workflows/ci.yml` | Covers Python behavior, end-to-end flow, and reviewer quality. |

## First changes: where to start

- **Change core agent behavior:** begin in `agent/server.py`, then read [runtime architecture](runtime-architecture.md) and [integrations and security](integrations-security.md). Middleware order and the tool list are operational policy, not incidental wiring.
- **Add or alter a source workflow:** begin in `agent/webhooks/` and `agent/dispatch.py`, then follow [workflows](workflows.md). Preserve signature validation, source/context construction, deterministic thread identity, and durable dispatch.
- **Change review behavior:** begin in `agent/reviewer.py` and `agent/review/`; follow [workflows](workflows.md) and run the targeted checks in [operations and quality](operations-quality.md).
- **Change UI or dashboard APIs:** use [dashboard](dashboard.md) to locate the route, typed UI client, and secured FastAPI endpoint as a unit.

## Local developer baseline

Python dependencies use `uv`; the Python test suite uses pytest; linting/formatting uses Ruff. The central commands are:

```bash
make install
make dev                 # LangGraph development server: graphs + FastAPI app
make lint
make format-check
make test
```

`make run` starts only `uvicorn agent.webapp:app` and is useful for HTTP-only work, but the agent/dashboard runtime normally needs `make dev`. The UI has its own `pnpm` scripts in `ui/package.json`. See [operations and quality](operations-quality.md) for practical test selection and E2E setup.

## Documentation notes

The source of truth is current code plus the maintained installation/customization guides. `AGENTS.md`, `CLAUDE.md`, and parts of `docs/CUSTOMIZATION.md` contain useful orientation but currently disagree with source on some graph registration, middleware, and sandbox details; this wiki follows `langgraph.json` and current implementation paths when they differ.

## Backlog

- **Full configuration reference** — `docs/INSTALLATION.md`; deferred because it is a large provider-specific setup guide with credential-sensitive material, better maintained in the existing installation documentation.
- **Per-tool API reference** — `agent/tools/`; deferred because tool behavior is intentionally curated and changes frequently. Start from the registered tool lists in `agent/server.py` or `agent/reviewer.py` for a particular change.
