---
type: developer guide
title: Testing Guide
description: Test-layer routing and focused test inventory for Open SWE, including dashboard thread contracts, shared pytest isolation, workspace suites, and the Playwright cross-boundary harness.
tags: [testing, pytest, vitest, playwright, dashboard, desktop, e2e]
verified:
  - by: openwiki/0.4.2
    at: 2026-09-02T08:15:43.727Z
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
  - id: openwiki-source-a7a923eb42c2ccc6f4c875de
    resource: repo://tests/agent/test_agent_assembly_context.py
  - id: openwiki-source-f0a6e7dc03522b2682f88655
    resource: repo://tests/conftest.py
  - id: openwiki-source-ec095d27060c9e7bc2c62460
    resource: repo://tests/dashboard/test_dashboard_csrf.py
  - id: openwiki-source-62d0819e47a738ba26f898fd
    resource: repo://tests/dashboard/test_dashboard_thread_api_activity.py
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
generated: { by: "openwiki/0.4.2", at: "2026-09-02T08:15:43.727Z" }
---

# Testing Guide

Choose the lowest-cost layer that owns the changed contract. Put agent assembly, API, webhook, middleware, and sandbox behavior in focused Python tests; dashboard component or client state in Vitest; and Electron main-process behavior in desktop Node tests. Use Playwright when the contract crosses real webhook, authenticated dashboard, local git/sandbox, or Electron boundaries—not as a substitute for focused coverage.

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

This routes a change to the layer that directly owns its observable behavior; one change can need a focused contract test and an e2e proof when it crosses a boundary.

## Python contracts and isolation

Pytest collects from `tests/` and uses asyncio auto mode, so async tests and fixtures need no per-test asyncio marker. The suite is organized by production owner, with agent, dashboard, sandbox, Slack, webhook, middleware, tool, reviewer, and GitHub areas under `tests/`. `tests/e2e/` is Playwright infrastructure even though it is below that directory.

Keep common isolation fixtures unless the test deliberately replaces their boundary:

- `fake_store` routes `agent.store` through an in-memory `FakeStore`, preserving the production `model_dump`/`model_validate` round trip. Seed it when stored state matters.
- The autouse TTL-cache reset prevents cached team settings from leaking between cases.
- The autouse auto-review stub treats every repository as enabled because the no-live-store environment otherwise has an empty opt-in list. Override it when testing the opt-in gate.

### Focused contracts worth preserving

**Agent assembly.** `tests/agent/test_agent_assembly_context.py` guards graph construction rather than an agent run: the main agent must receive an initialized sandbox-backed composite backend, which enables deepagents' context eviction and summarization behavior. It also covers concurrent sandbox startup while settings load, source-sensitive skills and Slack tools, parent-only thread/settings tools, middleware ordering, and the read-only stop-summary mode. Change these wiring decisions with their assembly tests, not a broad e2e test.

**Dashboard thread API.** Treat `tests/dashboard/test_dashboard_thread_api.py` and `test_dashboard_thread_api_activity.py` as the server-side contract inventory:

| Contract | What focused tests protect |
| --- | --- |
| Input and run preparation | Image capability validation; request, profile, and team model precedence; trusted configurable values; dashboard metadata; sender attribution; and Slack-to-web handoff/trace updates. |
| List and read visibility | Only surfaced sources are discoverable/readable; authenticated teammates can read surfaced threads, and finished runs refresh status and are marked viewed while running threads are not. |
| Posting and cancellation authority | Non-admins cannot write admin or automation threads; admins retain those writes; non-owner messages retain their sender identity; activity that cannot be determined fails rather than sending. |
| Pins and project discovery | A readable thread can be pinned by a non-owner, unreadable or missing threads cannot, and pinned listings retain only readable summaries. |
| Terminal, diff, and recovery access | Terminal access requires an existing sandbox; the creating sentinel is hidden/unusable. Recovery patches require a sandbox and reject empty or over-limit output; working/branch diffs retain repository and safe-branch constraints. |
| Thread lifecycle | Only `run.start` can lazily create a missing thread; resolve/unresolve is persisted, and a new run clears resolution. |

Keep route-validation tests separate from authentication defenses. `tests/dashboard/test_dashboard_csrf.py` verifies that configured cookie-authenticated mutations require an allowed `Origin` or `Referer` (including the desktop origin), reject malformed, null, missing, and prefix-bypass origins, and reject non-JSON command bodies. Reads are exempt from the mutation origin check. This is a security boundary, not UI validation.

**Sandbox state.** `tests/sandbox/test_sandbox_state.py` protects the lazy sandbox proxy. It must remain `BaseSandbox`-compatible for capture offload, delegate to an offload-capable backend or safely execute normally, coalesce concurrent reconnects, survive cancellation of a waiter, retry failed startup, and recover a sandbox ID from live thread metadata.

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

## Playwright cross-boundary harness

```bash
pnpm install --frozen-lockfile
pnpm run test:e2e:install
pnpm run test:e2e
pnpm run test:e2e:desktop
```

Install Chromium before the first run. Browser Playwright uses one worker, excludes `desktop.spec.ts`, has a 90-second test timeout, and starts real `langgraph dev` with `tests/e2e/langgraph.e2e.json`. The desktop configuration selects only `desktop.spec.ts`, raises the test timeout to 180 seconds and the expectation timeout to 120 seconds, and writes separate results and reports.

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
    Agent->>GitHub: PR tool calls fake REST API
    Agent->>Slack: Reply tool calls fake API
    PW->>Dash: Follow dashboard link with session
    Dash->>API: Dashboard API requests
```

The harness keeps the production paths real while controlling its external seams.

### Real paths, controlled seams

The harness overlays the real agent API with fake GitHub and Slack HTTP endpoints, mock UIs, and control endpoints, and signs simulated Slack Events API deliveries before posting them to the real webhook route. The fake stores are the shared source of truth rendered by the mock UIs. Git is real: runs use seeded local bare remotes, and PR file lists are calculated from pushed branches.

Only the LLM and external SaaS or credential boundaries are replaced. `fake_llm.py` is a scripted `BaseChatModel`; real agent graph, prompts, middleware, tools, local sandbox, webhook, and git paths run unchanged. The core scenario therefore proves that a Slack mention creates an implementation in a temporary local sandbox, pushes a branch, opens a PR, and replies with its link in the same Slack thread. Browser coverage additionally exercises Slack-to-web continuation, plan approval, environment authorization and propagation, Slack redelivery and busy-thread queueing, raw SSR authentication, PR health presentation, and thread-tool changes reflected in the dashboard.

### Real dashboard and Electron flows

The dashboard is not mocked. Global setup builds `ui/` with `VITE_DASHBOARD_API_BASE_URL` and `DASHBOARD_API_URL` directed to the harness, starts its Nitro server on `E2E_UI_PORT` (default `3100`), and waits for `/login`. The harness proxies page requests to that server. A signed `osw_session` issued by `/control/login` therefore exercises server rendering, session gating and redirects, hydration, and same-origin `/dashboard/api/*` calls. Set `E2E_FORCE_UI_BUILD=1` after UI or port changes; otherwise setup reuses the existing build.

The Electron scenario resets shared harness state, clones the seeded local bare remote into an isolated temporary project, injects a harness-issued `osw_session` cookie, and runs a local-agent request. It verifies both the project edit and the fake-GitHub PR title, branches, draft flag, and changed file. It also records its own Electron trace and screenshots, then removes temporary desktop state unless `E2E_KEEP_TMP` is set.

## Diagnostics and operations

Browser traces and videos are retained on failure locally and on the first retry in CI; screenshots are failure-only. Set `E2E_ARTIFACTS=1` to capture trace and video for every attempt. Artifacts are under `test-results/<test>/` and `playwright-report/`. The desktop configuration disables automatic Playwright artifacts because its spec explicitly records an Electron trace and attaches screenshots of the unified and completed local-agent views.

```bash
pnpm exec playwright show-report
pnpm exec playwright show-trace test-results/<test>/trace.zip
SLOW_MO=700 pnpm exec playwright test --headed
pnpm exec playwright test tests/full_flow.spec.ts
```

Prefer a warm server and a single relevant spec while iterating. Inspect the trace, screenshot, and fake-boundary state before raising timeouts or weakening an assertion.

## Related pages

- [Agent graph](/openwiki/architecture/agent-graph.md)
- [Authentication and security](/openwiki/concepts/auth-and-security.md)
- [Dashboard UI](/openwiki/integrations/dashboard-ui.md)
- [Deployment](/openwiki/operations/deployment.md)
- [Quickstart](/openwiki/quickstart.md)
- [PR creation](/openwiki/workflows/pr-creation.md)
