---
type: contributor guide
title: Open SWE change guide
description: Route an Open SWE change to its owning backend, Managed Deep Agents runtime, integration, workflow, operations, and test boundary. Use this page to establish a safe local loop and repository-wide constraints before making a focused change.
tags: [open-swe, contributor-guide, development, managed-deep-agents, testing]
verified:
  - by: openwiki/0.4.2
    at: 2026-10-23T08:13:55.386Z
sources:
  - id: openwiki-source-8037e2358a2c4f9b2c722a11
    resource: repo://AGENTS.md
  - id: openwiki-source-19973c87ca458faa5d03fecc
    resource: repo://docs/DEVELOPMENT.md
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
  - id: openwiki-source-3bd49e1c2bb74350a7519268
    resource: repo://openswe/webapp.py
  - id: openwiki-source-5b54a58d1b51cd490b0e7162
    resource: repo://package.json
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
generated: { by: "openwiki/0.4.2", at: "2026-10-23T08:13:55.386Z" }
---

# Open SWE change guide

Open SWE is an asynchronous software factory built on Deep Agents and LangGraph. It accepts work from the dashboard, GitHub, Slack, Linear, schedules, and local clients; it can implement and deliver pull requests, review them, and monitor follow-up work. This is a routing page, not a substitute for source and tests: inspect the owning implementation and its focused behavioral tests before changing it.

## Start with the right local loop

The backend requires Python 3.14+ and `uv`; the dashboard, Electron app, and CLI live in a pnpm/Turborepo workspace. A normal local instance also needs PostgreSQL plus two cooperating services: the LangGraph backend on `:2024` and Managed Deep Agents (MDA) on `:2025`.

```bash
make install
make build-dashboard
make dev                 # backend, LangGraph graphs, API, and built dashboard on :2024
make dev-mda             # MDA runtime on :2025, in a second terminal
```

`make dev` starts the loopback PostgreSQL container when `POSTGRES_URI` is absent. Configure the backend root `.env` with `MDA_RUNTIME_URL=http://localhost:2025` and `REMOTE_RUNTIME_TOKEN_SECRET`; configure `mda/.env` with `OPEN_SWE_BACKEND_URL=http://localhost:2024`, `LANGSMITH_API_KEY`, and model credentials. The backend validates its database, sandbox, local model, and MDA settings on startup, so missing required runtime configuration prevents a working service rather than silently falling back.

For dashboard work, prefer `make dev-ui`: it runs Vite on `:3000` and the backend on `:2024`, which fronts UI traffic so the browser stays on the backend origin. `make run` starts FastAPI alone on `:8000`; it does **not** have the LangGraph runtime and cannot create runs. `make desktop` starts Electron and expects a backend already running.

For GitHub or Slack development, use the static-domain webhook tunnel rather than exposing the backend directly:

```bash
make tunnel NGROK_DOMAIN=<name>.ngrok-free.dev
```

The supplied policy exposes only `/webhooks/*`. This matters because the local LangGraph server has unauthenticated runtime endpoints; do not replace the restricted tunnel with one that forwards all of port `2024`. See [local development and deployment](operations/local-development-and-deployment.md) for setup, dashboard builds, worktree state, deployment, and tunnel details.

## Runtime boundary: backend versus MDA

New `agent`, `reviewer`, and `review-scout` threads are MDA threads. The backend owns product-facing HTTP/API composition, PostgreSQL records, routing, authorization, integrations, and the in-process graph behavior it exposes through remote hooks. MDA owns the model loop, checkpoints, and the persistent per-thread sandbox; its single `open-swe` graph selects the role from run context. The backend signs the run token and reads run/thread configuration from that token rather than trusting hook request bodies. Older threads created before the MDA requirement remain in-process for compatibility.

```mermaid
sequenceDiagram
    participant Client as Dashboard or integration
    participant Backend as Open SWE backend
    participant MDA as Managed Deep Agents
    participant Box as Per-thread sandbox
    Client->>Backend: create or continue thread
    Backend->>MDA: create or forward run with signed context
    MDA->>Backend: invoke remote runtime hooks
    Backend-->>MDA: graph phases and backend tools
    MDA->>Box: model loop file and shell operations
    MDA-->>Backend: completion webhook
    Backend-->>Client: indexed state transcript and notifications
```

This shows the normal MDA-thread path; legacy local threads do not take the MDA hop.

**Safe-change implication:** MDA and the backend are separate projects. Do not import `openswe` from `mda/`. Treat hook payloads as a versioned contract and test each side against exchanged JSON. A graph middleware or ordinary graph tool added in the backend is available to MDA runs through the hook protocol; MDA-native file/shell handling and tool-call middleware require corresponding MDA support. Start with [managed runtime](architecture/managed-runtime.md) for the protocol, [sandbox lifecycle](architecture/sandbox-lifecycle.md) for box ownership/recovery, and [thread identities and state](concepts/threads-and-state.md) for the PostgreSQL index/transcript versus runtime-owned state.

## Entrypoints and composition

`langgraph.json` is the deployment registration point. It declares three graph entrypoints—`agent`, `reviewer`, and `review-scout`—as thin `openswe/graphs/` exports, plus the FastAPI compatibility entrypoint `openswe.webapp:app`. Change the owning module behind an export unless the public registration itself is changing. The deployment checkpointer uses delete-based cleanup: it sweeps every 60 minutes and defaults to 43,200 minutes of retention.

| Entry point or boundary | Change owner | Route to |
| --- | --- | --- |
| `openswe.graphs.agent:traced_agent` | Coding-agent construction, tools, skills, prompts, models, middleware | [agent assembly and middleware](architecture/agent-assembly-and-middleware.md) |
| `openswe.graphs.reviewer:traced_reviewer_agent` | Non-mutating review execution and findings | [reviewer and review-scout](architecture/reviewer-and-review-scout.md), [pull-request review](workflows/pull-request-review.md) |
| `openswe.graphs.review_scout:traced_review_scout` | Review-scout delegation and review discovery | [reviewer and review-scout](architecture/reviewer-and-review-scout.md) |
| `openswe.webapp:app` / `openswe.api.app` | API composition, middleware, startup/shutdown, routes | [system architecture](architecture/overview.md), [configuration](operations/configuration.md) |
| `ThreadRouter` and remote runtime hooks | MDA forwarding, run/state APIs, busy settlement | [managed runtime](architecture/managed-runtime.md), [thread runtime and transcript](architecture/thread-runtime-and-transcript.md) |

The FastAPI lifespan pins one event loop, migrates the configured database, validates required configuration, starts job/listener/UI-invalidation services, and stops them with HTTP/database cleanup. Several auxiliary listeners may degrade with warnings rather than block startup; their loss affects cross-process updates, while pages can still refetch. The application also installs credentialed CORS only for explicit dashboard origins (and `open-swe://app`), rejecting `*`.

## Find the owner before editing

Use this task-routing map to avoid putting behavior in a convenient but wrong layer.

### Product state and execution

- **Threads, durable views, visibility, queues, and continuation:** [threads and state](concepts/threads-and-state.md) and [thread runtime and transcript](architecture/thread-runtime-and-transcript.md).
- **Workspace selection, repositories, snapshots, and routing precedence:** [workspaces and routing](concepts/workspaces-and-routing.md).
- **Normalized inbound content, dispatch settings, tool policy, skills, and agent-visible errors:** [run inputs and tools](concepts/runs-inputs-and-tools.md).
- **Model/profile selection, prompts, and configuration precedence:** [configuration and model resolution](concepts/configuration-and-model-resolution.md).
- **Sandboxes, bridge clients, reconnect/recreate semantics, and providers:** [sandbox lifecycle](architecture/sandbox-lifecycle.md) and [sandbox providers](integrations/sandbox-providers.md).

### Ingress and delivery

- **Dashboard, desktop, CLI, browser streams, or local-project behavior:** [dashboard, desktop, and CLI](integrations/dashboard-ui.md).
- **Webhook verification, GitHub/Slack/Linear identities, comments, and actions:** [GitHub, Slack, and Linear](integrations/github-slack-and-linear.md).
- **Start/interrupt/enqueue semantics and completion replies:** [invocation and follow-ups](workflows/invocation-and-follow-ups.md).
- **Coding edits, approval gates, pushes, PR opening, and CI follow-up:** [implementation and PR delivery](workflows/implementation-and-pr-delivery.md).
- **Scheduled work, watches, reconciliation, and recovery:** [scheduled and background automation](workflows/scheduled-and-background-automation.md) and [background jobs and recovery](operations/background-jobs-and-recovery.md).

### Security, integrations, and operations

- **Principals, OAuth/API keys, encrypted credentials, App/proxy permissions, or webhook checks:** [authentication and security](concepts/auth-and-security.md).
- **MCP connections, gateways, tracing, analytics, or external observability:** [observability and MCP](integrations/observability-and-mcp.md).
- **Environment settings, secrets, startup failures, or service configuration:** [configuration and secrets](operations/configuration.md).
- **Builds, local PostgreSQL, MDA deployment, dashboard proxy/mount behavior, or releases:** [local development and deployment](operations/local-development-and-deployment.md).

## Repository-wide constraints

These rules deliberately shape where and how a change is implemented:

- **Async and types:** implement asynchronous paths only. If an interface forces a synchronous method, it must raise `NotImplementedError`; do not maintain sync and async implementations. Use strong Python and TypeScript types rather than `Any`/`any` to silence errors.
- **State and schema:** keep durable application state in PostgreSQL, not the LangGraph Store. Use migrations (`make migration m="Short description"`) and follow the required common table shape, audit columns, foreign keys, and natural-key indexes.
- **Boundaries:** place dashboard endpoints in the feature package's `router`, not the central dashboard routes. Keep model-facing prompts in `openswe/resources/prompts/` and render them through `prompt(...)`. Use named GitHub clients rather than raw REST/GraphQL transport outside the explicitly designated transport modules.
- **Failure behavior:** use exceptions inside Python; turn them into user-facing results only at API/tool edges. Never silently swallow an exception—re-raise or log it with structured logging.
- **Sandbox safety:** an unreachable coding sandbox is not silently replaced because it can hold uncommitted work. Review and chat have narrower mutation constraints, but do not infer that a sandbox reset is safe without following the owning lifecycle policy.
- **UI and messaging:** user-initiated UI mutations are optimistic unless that would be unsafe or misleading. Only a Slack thread's owning agent posts in that channel; background work hands it events instead.
- **Change quality:** first refactor the immediate surrounding code enough for the change to fit naturally. Favor behavior on the object that owns its state rather than argument-bag helpers.

## Validate the changed boundary

Never run the whole suite locally. Select the narrowest behavioral test that can demonstrate the changed contract, then run the quality command relevant to the edited language.

```bash
make test TEST_FILE=tests/threads/
uv run pytest -vvv tests/threads/test_example.py::test_behavior
make lint
make format-check
make typecheck

make mda-test PYTEST_ARGS="tests/test_backend_calls.py"
make mda-typecheck
```

Backend tests live under `tests/` and are organized by owner (for example `threads/`, `runtime/`, `transcript/`, `sandbox/`, `reviewer/`, `dashboard/`, `github/`, `slack/`, `webhooks/`, `workspaces/`, and `middleware/`). MDA tests are isolated in `mda/tests/` and run in MDA's locked environment. Do not add mechanical, mock-only, snapshot, or duplicate-layer tests: extend an existing behavioral test when it covers a concrete regression, with targeted coverage especially for authorization, integrity, and difficult state transitions.

For UI/desktop changes, use the pnpm workspace scripts (`pnpm run typecheck`, `pnpm run test`, or a filtered package command). Escalate to Playwright only when the change crosses the real client/runtime boundary:

```bash
pnpm install --frozen-lockfile
pnpm run test:e2e:install
pnpm run test:e2e
# or
pnpm run test:e2e:desktop
```

The browser E2E environment starts MDA in the same arrangement with a scripted model. See [testing strategy and focused validation](testing/overview.md) for fixture/database requirements, contract-test guidance, fake boundaries, and focused frontend and desktop commands.
