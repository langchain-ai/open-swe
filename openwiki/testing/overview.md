---
type: developer-guide
title: Testing Guide
description: A production-owner test map for Open SWE's Python, dashboard, desktop, and Playwright layers. It highlights dashboard-thread and sandbox contracts plus the isolated Electron end-to-end scenario.
tags: [testing, pytest, vitest, playwright, dashboard, desktop, e2e]
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
  - id: openwiki-source-86954185ec7b6e72d7a5a7a7
    resource: repo://tests/e2e/tests/desktop.spec.ts
  - id: openwiki-source-4cedab06aadc98083b348ddb
    resource: repo://tests/e2e/tests/full_flow.spec.ts
  - id: openwiki-source-f05d7497d4c60c3b322628eb
    resource: repo://tests/sandbox/test_sandbox_state.py
  - id: openwiki-source-440ae1e215cb02721dda855c
    resource: repo://turbo.json
  - id: openwiki-source-436f4179fe22abf615d2f7d0
    resource: repo://ui/package.json
verified:
  - by: openwiki/0.4.2
    at: 2026-08-31T08:17:06.525Z
generated: { by: "openwiki/0.4.2", at: "2026-08-31T08:17:06.525Z" }
---

# Testing Guide

Open SWE uses complementary layers, selected by the **production owner** of the changed contract:

- Python behavior in the agent, dashboard API, webhook, middleware, and sandbox belongs in `tests/` under its owning subsystem.
- React client behavior belongs in the dashboard's Vitest suite; Electron main-process behavior belongs in the desktop Node test suite.
- Playwright proves an integration that genuinely crosses the webhook/server, signed-session dashboard, local git/sandbox, or Electron boundary. It is intentionally not a replacement for the focused contract test.

## Test routing

```mermaid
flowchart TD
    Change["Code change"] --> Boundary{"Production boundary"}
    Boundary -->|"Agent, API, webhook, middleware, sandbox"| Python["Focused pytest subsystem test"]
    Boundary -->|"Dashboard component or client state"| Dashboard["Dashboard Vitest"]
    Boundary -->|"Electron main process"| Desktop["Desktop Node test"]
    Boundary -->|"Webhook, session, local git, or Electron integration"| E2E["Playwright end-to-end"]
    Python --> Gates["Run applicable quality gates"]
    Dashboard --> Gates
    Desktop --> Gates
    E2E --> Gates
```

This routes changes to the cheapest layer that owns their behavior, reserving e2e for real cross-boundary wiring.

### Python ownership map

Pytest collects `tests/`. Route by the code that owns the contract, not the UI symptom:

| Production area | Focused location |
|---|---|
| Agent graph, dispatch, planning, reviews, schedules, skills, usage | `tests/agent/` |
| Dashboard authentication, settings, thread proxy, terminal, environments | `tests/dashboard/` and `tests/auth/` |
| GitHub PRs, comments, webhook handling, repository normalization, prompts | `tests/github/` |
| Provider/model behavior | `tests/models/` |
| Queueing, dynamic tools, timeout/fallback, sanitization, ordering | `tests/middleware/` |
| Reviewer graph, findings, publishing, watches, auto-review | `tests/reviewer/` |
| Sandbox state, recovery, provider behavior, proxy and git identity | `tests/sandbox/` |
| Slack events, context, interactivity, code channels, replies | `tests/slack/` |
| Curated tools, MCP, HTTP safety, observability | `tests/tools/` |
| Completion and Linear webhooks | `tests/webhooks/` |

`tests/e2e/` is a separate Playwright harness, even though it is below `tests/`.

## Python environment and isolation

Pytest discovery is rooted at `tests/` and asyncio auto mode awaits async tests and fixtures without per-test markers. Install its development dependencies with `make install`.

`tests/conftest.py` supplies isolation that focused tests should keep using:

- `fake_store` replaces the store client with an in-memory store while retaining the real `agent.store` serialization round trip. Seed it rather than bypassing storage when persisted state matters.
- An autouse fixture clears the process-global TTL cache before and after each test, so cached team settings cannot cross test cases.
- Another autouse fixture makes auto-review enabled in the no-live-store test environment. A test of the opt-in gate must override that stub.

### Dashboard thread proxy contracts

`tests/dashboard/test_dashboard_thread_api.py` protects server-side behavior that browser tests should not be the sole owners of:

- model resolution gives explicit request choices precedence over profile and team defaults; image input is rejected for text-only models and can select the vision fallback;
- `run.start` creation stamps dashboard origin, participant, title, repository, resolved model, and preparation metadata, converts repository shape, attributes the human message, and removes dashboard-only hints before dispatch;
- summaries map PR and diff metadata, hide the `__creating__` sandbox sentinel, and expose Slack source links only for private repositories;
- a continuation from a Slack thread adds a dashboard-handoff system message before the web message while preserving sender attribution;
- terminal and recovery-patch paths reject unavailable sandboxes; recovery patches reject empty output and enforce the size limit.

Pair changes here with Vitest when client rendering/state changes. Add Playwright only where session/SSR or dashboard-to-server wiring is part of the contract.

### Sandbox proxy contracts

`tests/sandbox/test_sandbox_state.py` focuses on resilience at the backend boundary. The proxy remains a `BaseSandbox` so capture-at-source can use offload when supported, delegates offload to a live backend, and falls back to ordinary execution for protocol-only backends. Concurrent first operations share one metadata- or callback-based reconnect; a cancelled waiter does not cancel startup, while a failed startup can be retried. Sandbox-ID lookup falls back to live thread metadata when request configuration lacks it.

## Commands and quality gates

```bash
make install
make test
make test TEST_FILE=tests/dashboard/test_dashboard_thread_api.py
uv run pytest -vvv tests/dashboard/test_dashboard_thread_api.py::test_name
make lint
make typecheck
```

`make test` (also `make tests`) runs `uv run pytest -vvv $(TEST_FILE)` and defaults `TEST_FILE` to `tests/`; it prints a skip message rather than failing if the supplied path does not exist. `make lint` performs Ruff checking and a formatting diff, `make format` fixes both, and `make typecheck` runs basedpyright over `agent` and `tests`.

For workspace code:

```bash
pnpm install --frozen-lockfile
pnpm --filter open-swe-dashboard run test
pnpm --dir desktop run test
pnpm test
```

The dashboard `test` script is `vitest run`. Desktop builds its main bundle before `node --test test/*.test.cjs`; the root `pnpm test` delegates workspace test tasks to Turbo. Prefer the package command while iterating.

## Browser and Electron end-to-end harness

```bash
pnpm install --frozen-lockfile
pnpm run test:e2e:install
pnpm run test:e2e
pnpm run test:e2e:desktop
```

The install command downloads Chromium. The browser configuration runs serially (`workers: 1`) against real `langgraph dev`, ignores `desktop.spec.ts`, and uses a 90-second test timeout. The desktop configuration selects only `desktop.spec.ts` and raises test and expectation timeouts to 180 and 120 seconds.

```mermaid
sequenceDiagram
    participant PW as Playwright
    participant Slack as Mock Slack UI
    participant Server as Real webhook server
    participant Agent as Real agent graph
    participant Local as Local sandbox and git remote
    participant GitHub as Fake GitHub API and UI
    participant Dash as Real dashboard UI
    PW->>Slack: Send request
    Slack->>Server: Deliver webhook
    Server->>Agent: Dispatch run
    Agent->>Local: Edit, commit, and push branch
    Agent->>GitHub: Create pull request
    Agent->>Slack: Reply with pull request link
    PW->>Dash: Open dashboard thread
    Dash->>Server: Signed-session API calls
```

The browser flow and its observable integration boundaries: a Slack request reaches the real webhook and agent graph, the agent edits a local temporary sandbox and pushes to a local bare remote, the real PR and Slack tools target in-process fakes, and the mock UIs render the fake stores that those tools updated. The scripted model and external SaaS/token/snapshot boundaries are faked; agent code, git, and the local sandbox are not.

Global setup builds the actual dashboard with both API bases aimed at the harness, starts its server, and waits for `/login`. Consequently the suite covers server rendering, session gate/redirect/hydration, and same-origin `/dashboard/api/*` calls with a signed harness cookie. Set `E2E_FORCE_UI_BUILD=1` after dashboard or port changes.

### Current Electron scenario

The desktop spec proves the local-client path against the same harness fakes:

1. It resets harness state, clones the seeded local bare remote into a temporary project, and launches the real Electron executable in development mode.
2. Per-run `HOME`, XDG/AppData, Git configuration, Python path, uv cache, and desktop project registry isolate application and local-agent state. The temporary state is removed unless `E2E_KEEP_TMP` is set.
3. The test obtains a signed `osw_session` from `/control/login` and injects it as an HTTP-only Electron session cookie before navigating to `open-swe://app/agents`.
4. It verifies the local project controls, submits a local request, then asserts the local route and completion, `greet.py` content in the cloned project, and the created fake-GitHub PR's title, branch, base, draft state, and changed file.

This is a local execution and IPC/client integration test, not a mocked desktop UI test.

## Diagnostics

Browser runs record trace and video locally and take a screenshot on failure; CI records trace/video on first retry. Reports and artifacts are written beneath `test-results/` and `playwright-report/`.

```bash
pnpm exec playwright show-report
pnpm exec playwright show-trace test-results/<test>/trace.zip
```

The desktop config disables Playwright's automatic artifacts, but the spec explicitly creates an Electron trace and attaches screenshots for the unified and completed local-agent views. Inspect these artifacts before increasing timeouts or weakening assertions. For interactive browser debugging, use `SLOW_MO=700 pnpm exec playwright test --headed`.

## Related pages

- [Agent graph](/openwiki/architecture/agent-graph.md)
- [Middleware stack](/openwiki/architecture/middleware-stack.md)
- [Sandbox lifecycle](/openwiki/architecture/sandbox-lifecycle.md)
- [Dashboard UI](/openwiki/integrations/dashboard-ui.md)
- [Invocation workflow](/openwiki/workflows/invocation.md)
- [PR creation](/openwiki/workflows/pr-creation.md)
