---
type: "Reference"
title: "Quick Start: Navigate the Open SWE Codebase"
openwiki_generated: true
verified:
  - by: openwiki/0.4.2
    at: 2026-10-08T15:19:10.971Z
sources:
  - id: openwiki-source-8037e2358a2c4f9b2c722a11
    resource: repo://AGENTS.md
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
  - id: openwiki-source-6b99105488d7c23beda1e5ad
    resource: repo://openswe/graphs/scheduler.py
  - id: openwiki-source-169564263f818f7bae30cd90
    resource: repo://openswe/review_scout/graph.py
  - id: openwiki-source-96bcad07b4fe7078402bc2b8
    resource: repo://openswe/reviewer.py
  - id: openwiki-source-685dc33e7199aa1f6e402f7a
    resource: repo://openswe/scheduler.py
  - id: openwiki-source-919e16feae379651f2cbc1c9
    resource: repo://openswe/server.py
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
generated: { by: "openwiki/0.4.2", at: "2026-10-08T15:19:10.971Z" }
---


# Quick Start: Navigate the Open SWE Codebase

Open SWE is a LangGraph and Deep Agents software-engineering framework: work can arrive from the dashboard, GitHub, Slack, Linear, or a schedule; coding work runs in a thread-scoped isolated sandbox and can produce a pull request. This page routes you through the wiki hierarchy based on your task. Read the relevant source and tests first; the linked OpenWiki pages are optional just-in-time context, not an authority over the repository.

## Start a local developer loop

Use Python 3.14 and `uv` for the backend. The dashboard and desktop workspace use `pnpm`.

```bash
make install            # uv sync --extra dev
make dev                # uv run langgraph dev --no-browser --port 2024
make run                # uv run uvicorn openswe.webapp:app --reload --port 8000
make dev-ui             # Vite plus LangGraph development server
make web                # pnpm run dev
make desktop            # pnpm run dev:desktop
```

Use `make dev` when a change needs LangGraph graph execution; it serves all six registered graphs and the HTTP app. `make run` is FastAPI-only for dashboard and webhook testing without graph changes. `make dev-ui` fronts Vite through the backend at `:2024`; `make desktop` starts Electron and requires the backend separately. For local webhook exposure, use `make tunnel NGROK_DOMAIN=<name>.ngrok-free.dev` to restrict the ngrok policy to `/webhooks/*` only, since the development server has no authentication.

Python is async-first: implement the async path. Add a synchronous method only when an interface requires it, and make that method raise `NotImplementedError`; do not maintain parallel implementations.

## Entrypoints and execution boundaries

`langgraph.json` is the deployment registration point. Its graph targets are thin `openswe/graphs/` re-export shims; change the owning module, not the shim, unless the public entrypoint itself must move. It also mounts `openswe.webapp:app` and configures the deployed checkpointer with delete-based TTL cleanup (60-minute sweep and 43,200-minute default retention).

| Entrypoint | Owning concern | Start here for changes to… |
| --- | --- | --- |
| `openswe.graphs.agent:traced_agent` | Main coding graph (`openswe/server.py`) | Agent assembly, tools, skills, models, prompts, middleware, and coding sandbox preparation. |
| `openswe.graphs.reviewer:traced_reviewer_agent` | Reviewer graph (`openswe/reviewer.py`) | Diff-grounded findings, review publication, reviewer sandbox behavior, and reviewer middleware. |
| `openswe.graphs.analyzer:traced_analyzer` | Style analyzer (`openswe/analyzer.py`) | Repository review-style analysis and learned guidance. |
| `openswe.graphs.review_scout:traced_review_scout` | Review scout (`openswe/review_scout/graph.py`) | Diff walkthrough generation, commit ordering, and scout-specific sandbox behavior. |
| `openswe.graphs.chat:traced_chat_agent` | PR chat (`openswe/chat.py`) | Dashboard "chat with this PR," virtual PR files, and read-only repository access. |
| `openswe.graphs.scheduler:get_scheduler` | Scheduler (`openswe/scheduler.py`) | Cron routing, scheduled work, stale-run repair, CI watches, background tasks, and cost refreshes. |
| `openswe.webapp:app` | FastAPI composition (`openswe/api/app.py`) | Dashboard APIs/UI mount, health, plan/approval APIs, CORS, and webhook ingress. |

### Request flow: from trigger to durable run

All interactive triggers (dashboard, GitHub, Slack, Linear webhooks) and scheduled work converge through a single dispatch contract. The key entrypoint is `openswe.dispatch:dispatch_agent_run`, which creates a durable LangGraph run with the appropriate graph (agent or reviewer) and configurable multitask strategy (default: `interrupt`).

```mermaid
flowchart TD
    Dashboard["Dashboard"] --> Api["FastAPI routes"]
    GitHub["GitHub webhook"] --> Api
    Slack["Slack webhook"] --> Api
    Linear["Linear webhook"] --> Api
    Cron["Cron tick"] --> Scheduler["Scheduler dispatch"]
    
    Api --> Dispatch["dispatch_agent_run"]
    Dispatch --> RunInput["Create run input with identity and context"]
    RunInput --> DurableRun["Create durable LangGraph run"]
    DurableRun --> AgentGraph["Agent or Reviewer graph"]
    Scheduler --> RunInput
    
    AgentGraph --> Sandbox["Thread-scoped sandbox"]
    Sandbox --> Result["Result: work or findings"]
    Result --> Webhook["Completion webhook"]
```

The main agent graph is stateless and rebuilt per thread; all per-thread state lives in the sandbox plus LangGraph thread metadata. The reviewer is a non-mutating PR-analysis graph with finding tools. The scheduler routes cron ticks to maintenance work (reconciliation, watch evaluation, cost refresh) or scheduled agent runs.

### Key invariants to preserve

- **Stateless graphs and thread binding:** The main agent factory is stateless and rebuilt per thread. Thread continuity belongs to LangGraph state/metadata and the sandbox, not to a long-lived graph object.
- **Sandbox preservation:** A missing sandbox may be recreated, but do **not** silently replace an unreachable main-agent sandbox—it could contain uncommitted work. Only the reviewer and scout opt into replacement because their checkouts are recreated each run.
- **Reviewer constraints:** The reviewer has no commit, push, or PR-opening tools. It only adds findings. The chat graph is also sandbox-less and excludes shell and file mutation; it answers from `/pr/` virtual files and read-only GitHub API access.
- **Dispatch contract:** `dispatch_agent_run` is the shared creation contract for all Slack, Linear, GitHub, and dashboard triggers of `agent` or `reviewer` graphs. Its default multitask strategy is `interrupt`, and callers must choose exactly one input-construction path: either a prebuilt run input or separate content/context/identity parameters—not both.
- **FastAPI startup:** The lifespan pins a single event loop before queue construction, validates sandbox and local-development LLM configuration at startup, and configures credentialed CORS for configured dashboard origins only—rejecting a wildcard origin.

## Choose the detailed guide

### Architecture and extensibility

- [Runtime and Product Architecture](architecture/overview.md) — deployment topology, FastAPI composition, durable dispatch, and surface boundaries.
- [Coding Agent Assembly](architecture/agent-graph.md) — `get_agent`, backend/model/profile resolution, curated tools, skills, subagents, and preparation.
- [Middleware and Failure Boundaries](architecture/middleware-stack.md) — ordering-sensitive retries, timeouts, guards, queues, fallbacks, and error reporting.
- [Thread Sandbox Lifecycle](architecture/sandbox-lifecycle.md) and [Sandbox Provider Integration](integrations/sandbox-providers.md) — thread binding, safe recovery, proxy state, provider selection, and adding a provider.
- [Review and Style Analysis Graphs](architecture/reviewer-and-analyzer.md) — the non-mutating reviewer, finding lifecycle, and style analysis.
- [Threads, Durable Runs, and State](concepts/threads-and-state.md) — checkpoints, metadata, thread identity, and ownership boundaries.
- [Tool Catalog and Authorization](concepts/tools.md) — exporting, wiring, authorizing, and safely changing tools.
- [Models, Profiles, and Instructions](concepts/models-profiles-instructions.md) — configuration precedence and prompt inputs.

### Ingress, product, and delivery workflows

- [Inbound Invocation to Durable Run](workflows/invocation.md) — validation, identity/context construction, threads, dispatch, and completion across dashboard, desktop, Slack, Linear, GitHub, and automation.
- [Pull Request Review Workflow](workflows/pr-review.md) — manual/automatic reviews, findings, publishing, replies, and settlement.
- [Scheduling, Background Work, and CI Monitoring](workflows/scheduling-and-baby-sit.md) — schedule lifecycle, reconciliation, watches, and background tasks.
- [Dashboard and Desktop Clients](integrations/dashboard-ui.md) — authenticated browser APIs, UI proxy/mount behavior, Electron supervision, and local projects.
- [Authentication, Authorization, and Secret Boundaries](concepts/auth-and-security.md) — webhook verification, membership gates, OAuth/App tokens, encryption, and credential proxies.
- [Observability, Browser, and MCP Integrations](integrations/observability-and-mcp.md) — optional integrations and their configuration/authorization gates.

### Operations

- [Configuration and Startup Validation](operations/configuration.md) — lazy environment settings, persisted administrator settings, credentials, aliases, and failure behavior.
- [Development, Deployment, and Serving](operations/deployment.md) — local versus deployed serving, dashboard builds/mount prefixes, webhook exposure, and desktop distribution.

### Testing

- [Testing Strategy and Patterns](testing/overview.md) — test ownership, shared fixtures, deterministic patterns, and focused E2E validation.

## Validate only the changed boundary

**Never run the full suite locally.** Select the narrowest test that owns the behavior, then run the relevant quality check.

```bash
make test TEST_FILE=tests/github/test_open_pull_request.py
uv run pytest -vvv tests/path/to_test.py::test_name
make lint
make format-check
make typecheck
```

`make test` accepts an existing path; use direct `pytest` for a node id. Pytest uses asyncio auto mode; shared fixtures substitute an in-memory store through the production serialization route, clear the global TTL cache before and after each case, hide any locally bundled dashboard, and enable auto-review by default. Override those defaults explicitly when testing their gates.

Use focused pytest families such as `tests/agent/`, `tests/reviewer/`, `tests/sandbox/`, `tests/webhooks/`, `tests/dashboard/`, `tests/github/`, `tests/slack/`, `tests/middleware/`, or `tests/tools/` according to the changed owner. Run a focused dashboard or desktop workspace check through its package when changing frontend code.

### End-to-end validation

Escalate to a Playwright spec only for a genuine cross-boundary contract:

```bash
pnpm install --frozen-lockfile
pnpm run test:e2e:install
pnpm exec playwright test tests/full_flow.spec.ts
```

The E2E harness exercises real agent code, a temporary local sandbox, local git, the real dashboard, and Electron paths while faking the model and external SaaS HTTP boundaries. Browser runs use one worker; the separate desktop configuration selects `desktop.spec.ts`. See [Testing Strategy and Patterns](testing/overview.md) for test ownership, fakes, artifacts, and narrow frontend/desktop commands.
