---
type: developer guide
title: Testing Guide
description: Owner-oriented test strategy for Open SWE, covering focused Python and workspace tests plus the serial Playwright harness that exercises real agent, dashboard, git, and desktop flows behind controlled external boundaries.
tags: [testing, pytest, vitest, playwright, dashboard, desktop, e2e]
verified:
  - by: openwiki/0.4.2
    at: 2026-09-01T08:16:00.848Z
sources:
  - id: openwiki-source-8037e2358a2c4f9b2c722a11
    resource: repo://AGENTS.md
  - id: openwiki-source-24f77a48f966a05631988d08
    resource: repo://desktop/package.json
  - id: openwiki-source-012f2c78e3b1446dfc35803f
    resource: repo://Makefile
  - id: openwiki-source-5b54a58d1b51cd490b0e7162
    resource: repo://package.json
  - id: openwiki-source-05ccef8d4cf1698187f20464
    resource: repo://pyproject.toml
  - id: openwiki-source-f0a6e7dc03522b2682f88655
    resource: repo://tests/conftest.py
  - id: openwiki-source-654bec991273a9eb3ccdf2c1
    resource: repo://tests/dashboard/test_dashboard_thread_api.py
  - id: openwiki-source-069ae2b497200c26ef2dc134
    resource: repo://tests/e2e/fake_llm.py
  - id: openwiki-source-8317f526f4e30c2659c8614e
    resource: repo://tests/e2e/fakes.py
  - id: openwiki-source-c484c171a84d342028bf0794
    resource: repo://tests/e2e/global-setup.ts
  - id: openwiki-source-aefe409f90608437573cbad3
    resource: repo://tests/e2e/harness.py
  - id: openwiki-source-16e94b1dfd40df68fa54c87f
    resource: repo://tests/e2e/package.json
  - id: openwiki-source-28a3fe2bdb4cd54e328962f0
    resource: repo://tests/e2e/patches.py
  - id: openwiki-source-859f98720585f4648f0f7b2e
    resource: repo://tests/e2e/playwright.config.ts
  - id: openwiki-source-4b944ec14a3d793a6f771403
    resource: repo://tests/e2e/playwright.desktop.config.ts
  - id: openwiki-source-7ef60dc4372e1a33c7728fe6
    resource: repo://tests/e2e/README.md
  - id: openwiki-source-fbac30b19a864a52310a1665
    resource: repo://tests/e2e/tests/dashboard_pull_requests.spec.ts
  - id: openwiki-source-84b0f9cd64db5f62b58c0ae3
    resource: repo://tests/e2e/tests/dashboard.spec.ts
  - id: openwiki-source-86954185ec7b6e72d7a5a7a7
    resource: repo://tests/e2e/tests/desktop.spec.ts
  - id: openwiki-source-5a5c5a848de71e99970fff9a
    resource: repo://tests/e2e/tests/environments.spec.ts
  - id: openwiki-source-4cedab06aadc98083b348ddb
    resource: repo://tests/e2e/tests/full_flow.spec.ts
  - id: openwiki-source-0c18114176408608cd798894
    resource: repo://tests/e2e/tests/plan_review.spec.ts
  - id: openwiki-source-85717af8eec9e8415783b73b
    resource: repo://tests/e2e/tests/slack_debounce.spec.ts
  - id: openwiki-source-9b825352235c3d4892a6951c
    resource: repo://tests/e2e/tests/slack_event_dedupe.spec.ts
  - id: openwiki-source-50d5bb6d0d448392edc9d1ea
    resource: repo://tests/e2e/tests/ssr.spec.ts
  - id: openwiki-source-3fc3591ebb7e0b354b4c3ae0
    resource: repo://tests/e2e/tests/thread_tools.spec.ts
  - id: openwiki-source-f05d7497d4c60c3b322628eb
    resource: repo://tests/sandbox/test_sandbox_state.py
  - id: openwiki-source-440ae1e215cb02721dda855c
    resource: repo://turbo.json
  - id: openwiki-source-436f4179fe22abf615d2f7d0
    resource: repo://ui/package.json
generated: { by: "openwiki/0.4.2", at: "2026-09-01T08:16:00.848Z" }
---

# Testing Guide

Choose the lowest-cost test layer that owns the changed contract. Put agent, API, webhook, middleware, and sandbox behavior in focused Python tests; dashboard client behavior in Vitest; and Electron main-process behavior in desktop Node tests. Use Playwright when the contract crosses a real webhook, authenticated dashboard, local git/sandbox, or Electron boundary—not as a substitute for the focused test.

## Test routing

```mermaid
flowchart TD
    Change["Code change"] --> Boundary{"Owning boundary"}
    Boundary -->|"Agent API webhook middleware sandbox"| Python["Focused pytest test"]
    Boundary -->|"Dashboard component or client state"| Dashboard["Dashboard Vitest"]
    Boundary -->|"Electron main process"| Desktop["Desktop Node test"]
    Boundary -->|"Cross-boundary integration"| E2E["Playwright harness"]
    Python --> Gates["Run relevant quality gates"]
    Dashboard --> Gates
    Desktop --> Gates
    E2E --> Gates
```

This routes a change to the test layer that directly owns its observable behavior.

### Python contracts and isolation

Pytest collects from `tests/` and uses asyncio auto mode, so async tests and fixtures need no per-test asyncio marker. The suite is organized by production owner: agent behavior in `tests/agent/`, dashboard/auth in `tests/dashboard/` and `tests/auth/`, GitHub in `tests/github/`, middleware in `tests/middleware/`, reviewer behavior in `tests/reviewer/`, sandboxes in `tests/sandbox/`, Slack in `tests/slack/`, tools in `tests/tools/`, and webhooks in `tests/webhooks/`. `tests/e2e/` is Playwright infrastructure, despite sitting below that directory.

Keep the common isolation fixtures unless a test is explicitly about a boundary they replace:

- `fake_store` routes `agent.store` through an in-memory `FakeStore`, while preserving the production serialization/deserialization round trip. Seed it when stored state is relevant.
- The autouse TTL-cache reset prevents cached team settings from leaking between test cases.
- The autouse auto-review stub treats every repository as enabled because the no-live-store test environment otherwise has an empty opt-in list. Override it for a test of the opt-in gate.

#### Focused examples worth preserving

`tests/dashboard/test_dashboard_thread_api.py` owns server-side dashboard-thread rules: model and image validation, request/profile/team model precedence, run metadata enrichment, summary visibility, Slack-to-web handoff, unavailable-sandbox and recovery-patch failures, and command validation. Pair a change there with Vitest only for client presentation or state behavior; add browser coverage when auth, SSR, or the dashboard/server wire is itself part of the contract.

`tests/sandbox/test_sandbox_state.py` protects the lazy sandbox proxy. It must remain `BaseSandbox`-compatible for capture offload, delegate to an offload-capable backend or safely execute normally, coalesce concurrent reconnects, survive cancellation of a waiter, retry failed startup, and recover a sandbox ID from live thread metadata.

## Commands and quality gates

```bash
make install
make test
make test TEST_FILE=tests/dashboard/test_dashboard_thread_api.py
uv run pytest -vvv tests/dashboard/test_dashboard_thread_api.py::test_name
make lint
make typecheck
```

`make install` runs `uv sync --extra dev`. `make test` (also `make tests`) runs `uv run pytest -vvv $(TEST_FILE)` with `TEST_FILE` defaulting to `tests/`; a missing target prints a skip message rather than failing. Linting, formatting, and typing are separate checks: `make lint` runs Ruff checking plus a format diff, `make format` fixes both, and `make typecheck` runs basedpyright over `agent` and `tests`.

For workspace packages:

```bash
pnpm install --frozen-lockfile
pnpm --filter open-swe-dashboard run test
pnpm --dir desktop run test
pnpm test
```

The dashboard command is `vitest run`. Desktop builds its main bundle and then runs `node --test test/*.test.cjs`; root `pnpm test` delegates workspace test tasks to Turbo.

## Playwright harness

```bash
pnpm install --frozen-lockfile
pnpm run test:e2e:install
pnpm run test:e2e
pnpm run test:e2e:desktop
```

Install Chromium before the first run. The browser configuration runs one worker, excludes `desktop.spec.ts`, uses a 90-second test timeout, and starts `langgraph dev` with `tests/e2e/langgraph.e2e.json`. The desktop configuration selects only `desktop.spec.ts`, uses a 180-second test timeout and 120-second expectation timeout, and has a separate output/report directory.

```mermaid
sequenceDiagram
    participant PW as Playwright
    participant Harness as E2E harness
    participant API as Real agent API
    participant Agent as Real agent graph
    participant Local as Local sandbox and git
    participant GitHub as Fake GitHub boundary
    participant Slack as Fake Slack boundary
    participant Dash as Real dashboard UI
    PW->>Harness: Send mock Slack request
    Harness->>API: Signed Slack webhook delivery
    API->>Agent: Dispatch run through langgraph dev
    Agent->>Local: Edit commit and push branch
    Agent->>GitHub: Real PR tool calls fake REST API
    Agent->>Slack: Real reply tool calls fake API
    PW->>Dash: Follow dashboard link with session
    Dash->>API: Dashboard API requests
```

The diagram shows the browser harness's production paths and its controlled external seams.

### What is real and what is faked

The harness mounts fake GitHub and Slack HTTP endpoints, mock UIs, and test control endpoints over the real agent API; it signs Slack event deliveries before posting them to the real webhook route. The fake stores are the shared source of truth for both the real tools and mock UI assertions. Git is real: each run operates against seeded local bare remotes, and PR file lists are calculated from the pushed branches.

Only the model and external SaaS/credential boundaries are replaced. `fake_llm.py` is a scripted `BaseChatModel` that drives the real deepagents loop and reads the generated PR URL before issuing the reply. The real agent graph, prompts, middleware, tools, local sandbox, and webhook code run unchanged. This makes the basic Slack scenario prove: a mention triggers an implementation in a temporary local sandbox, a branch is pushed, a PR is created, and its link returns to the same Slack thread. The same spec also covers an unmentioned DM request and breakout creation of a separate top-level Open SWE thread.

Browser coverage extends well beyond that happy path. It exercises Slack-to-web continuation and queued-message handoff, plan review and approval through to a PR, authorization and selection of environments, Slack redelivery deduplication and busy-thread follow-up queueing, authenticated and unauthenticated SSR, PR presentation and health actions, and agent thread tools reflected in the real threads UI. Keep an e2e scenario focused on an integration invariant and leave detailed policy/validation coverage to the subsystem tests.

### Real dashboard server and sessions

The dashboard is not a mock. Global setup builds `ui/` with `VITE_DASHBOARD_API_BASE_URL` and `DASHBOARD_API_URL` aimed at the harness, starts its Nitro server on `E2E_UI_PORT` (default `3100`), and waits for `/login`; the harness proxies page requests to that server. A signed `osw_session` minted at `/control/login` therefore exercises real server rendering, the session gate and redirect, hydration, and same-origin `/dashboard/api/*` calls. Set `E2E_FORCE_UI_BUILD=1` after a dashboard or port change; the normal setup reuses an existing build.

### Desktop scenario

The Electron spec resets the shared harness, clones the seeded bare remote into an isolated temporary project, and starts the real Electron executable in development mode. It isolates HOME, platform app-data/config paths, git configuration, Python path, and uv cache; it obtains a harness session and injects an HTTP-only `osw_session` cookie. The test then selects the local project, sends a local-agent request, verifies local navigation and completion, confirms the edited `greet.py`, and checks the fake-GitHub PR title, head/base branches, draft flag, and changed file. Temporary state is removed unless `E2E_KEEP_TMP` is set.

## Diagnostics and operations

Browser traces and videos are retained on failure locally and on the first retry in CI; screenshots are failure-only. Set `E2E_ARTIFACTS=1` to capture trace and video for every attempt. Artifacts are under `test-results/<test>/` and `playwright-report/`. The desktop config disables Playwright's automatic artifacts, but its spec explicitly records an Electron trace and attaches screenshots of the unified and completed local-agent views.

```bash
pnpm exec playwright show-report
pnpm exec playwright show-trace test-results/<test>/trace.zip
SLOW_MO=700 pnpm exec playwright test --headed
pnpm exec playwright test tests/full_flow.spec.ts
```

Prefer a warm server and a single spec while iterating. Inspect the trace, screenshot, and fake-boundary state before raising timeouts or weakening an assertion.

## Related pages

- [Agent graph](/openwiki/architecture/agent-graph.md)
- [Middleware stack](/openwiki/architecture/middleware-stack.md)
- [Sandbox lifecycle](/openwiki/architecture/sandbox-lifecycle.md)
- [Dashboard UI](/openwiki/integrations/dashboard-ui.md)
- [Invocation workflow](/openwiki/workflows/invocation.md)
- [PR creation](/openwiki/workflows/pr-creation.md)
