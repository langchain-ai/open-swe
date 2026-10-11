---
type: testing strategy
title: Testing Infrastructure and Validation Patterns
description: Comprehensive test infrastructure, focused validation strategies, and end-to-end flows for agent behavior, middleware, integrations, and production boundaries.
tags: [testing, pytest, vitest, playwright, e2e, fixtures, isolation, fakes]
sources:
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
  - id: openwiki-source-c484c171a84d342028bf0794
    resource: repo://tests/e2e/global-setup.ts
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
  - id: openwiki-source-ec3fbe14e1e05123704c4f28
    resource: repo://tests/reviewer/test_reviewer_outcomes.py
  - id: openwiki-source-f05d7497d4c60c3b322628eb
    resource: repo://tests/sandbox/test_sandbox_state.py
  - id: openwiki-source-a9842c19fa28878dfa7fcd61
    resource: repo://tests/webhooks/test_completion_webhook.py
  - id: openwiki-source-440ae1e215cb02721dda855c
    resource: repo://turbo.json
  - id: openwiki-source-436f4179fe22abf615d2f7d0
    resource: repo://ui/package.json
generated: { by: "openwiki/0.4.2", at: "2026-10-08T15:19:10.971Z" }
verified:
  - by: openwiki/0.4.2
    at: 2026-10-08T15:19:10.971Z
---

# Testing Infrastructure and Validation Patterns

The testing strategy separates concerns by layer: focused pytest tests for agent, middleware, reviewer, sandbox, and webhook behavior; dashboard Vitest for React rendering and client state; desktop Node tests for Electron main-process code; and Playwright for real webhook, authenticated dashboard, git/sandbox, and Electron integration. Choose the test layer that owns the contract being changed, running it in isolation with fakes and mocking only external boundaries.

<!-- openwiki: mermaid parse failed and this diagram was converted to a text fence so it does not break rendering. Fix the diagram source and restore the mermaid fence. Parser error: Heuristic: an unescaped angle bracket inside a label breaks rendering; rephrase the label. -->
```text
flowchart TD
    Change["Changed behavior"] --> Owner{"Which system<br/>boundary owns it?"}
    Owner -->|"Agent, middleware,<br/>reviewer, sandbox,<br/>webhook, tools"| Pytest["Focused pytest<br/>in asyncio auto mode"]
    Owner -->|"Dashboard React<br/>component or client"| Vitest["Dashboard Vitest"]
    Owner -->|"Electron main<br/>process"| Node["Desktop Node test"]
    Owner -->|"Real webhook, UI auth,<br/>git, Electron"| Playwright["Focused Playwright<br/>E2E"]
    Pytest --> Gate["Relevant quality gate"]
    Vitest --> Gate
    Node --> Gate
    Playwright --> Gate
```

Test layer responsibilities: pytest owns Python backend contracts via unit tests with fakes; Vitest owns frontend React components; Node tests own Electron main process; Playwright owns real webhook routes, UI integration, and end-to-end flows through the agent.

## Python test organization and fixtures

### Pytest configuration and execution

Pytest collects tests from `tests/` (per `testpaths` in `pyproject.toml`). Tests run in asyncio auto mode (`asyncio_mode = "auto"`), so async test functions and fixtures are awaited automatically without per-test markers. Install development dependencies with `make install`, which runs `uv sync --extra dev`; the dev group includes pytest, pytest-asyncio, Ruff, ty, and Pygments.

```bash
make install
make test TEST_FILE=tests/sandbox/test_sandbox_state.py
uv run pytest -vvv tests/sandbox/test_sandbox_state.py::test_sandbox_proxy_retries_failed_startup
make lint
make typecheck
```

`make test` (alias `make tests`) executes `uv run pytest -vvv $(TEST_FILE)` when the path exists; otherwise it prints a skip message. Use `TEST_FILE` for a file or directory path; for a single node id like `file.py::test_name`, invoke pytest directly. Quality gates are independent: `make lint` runs Ruff checking and a format diff, `make format` fixes code in place, and `make typecheck` runs `ty check openswe tests`.

### Shared fixtures and isolation

`tests/conftest.py` provides autouse fixtures that make unit tests independent of a live Store and built dashboard:

- **`fake_store`**: Routes `agent.store` access to an in-memory `FakeStore` that round-trips values through `model_dump`/`model_validate` the same way production does. Seed it only when persisted state is part of the contract.
- **`_no_bundled_dashboard`** (autouse): Points `DASHBOARD_STATIC_DIR` to a missing temporary directory so a locally built `ui/.output` cannot influence Python tests.
- **`_reset_ttl_cache`** (autouse): Clears the process-global TTL cache before and after each test, preventing cached team settings from leaking between tests. Also clears the langgraph_api in-process cache when not running against a PostgreSQL backend.
- **`_reset_sandbox_registries`** (autouse): Clears `SANDBOX_BACKENDS` and `SANDBOX_CONNECTIONS` (process globals) to prevent one test's sandbox from answering for another's thread.
- **`_default_enable_auto_review`** (autouse): Stubs `is_review_repo_enabled` to return `True` for every repository because the dashboard opt-in list is empty without a live Store. Tests targeting the auto-review gate must override it with a stricter policy.
- **`registry_db`** and **`registry_db_if_available`**: Isolated PostgreSQL schemas for pull-request and repository rows; required for tests using the real database code path. Set `TEST_ANALYTICS_POSTGRES_URI` to enable.
- **`slack_api`**: A mock Slack API server for tests driving real Slack endpoints without credentials.
- **`allowed_bot`**: Seeds a test Slack bot configuration into the fake store.

### System-boundary test locations and patterns

Use the narrow test family that carries the failure semantics being changed:

| Change | Focused location and protected behavior |
| --- | --- |
| Main agent construction, source-specific tools, backend, skills, or middleware | `tests/agent/test_agent_assembly_context.py`; it verifies the initialized `CompositeBackend`/`SandboxBackendProxy` arrangement, read-only skills, dynamic browser-tool exposure, and parent-only tool boundaries. |
| Reviewer findings, published reviews, reconciliation, check runs, or learning outcomes | `tests/reviewer/test_reviewer_outcomes.py`; maps resolution and feedback into true/false-positive outcomes and treats absent credentials or repository data as a no-op. |
| Lazy sandbox reconnection, capture offload, or sandbox identity recovery | `tests/sandbox/test_sandbox_state.py`; requires `BaseSandbox` compatibility, safe offload fallback, one shared reconnect, cancellation-safe waiting, retry after failed startup, and live-thread metadata fallback. |
| Completion notification and reviewer error cleanup | `tests/webhooks/test_completion_webhook.py`; errors on Slack-originated work send a thread reply and record the run, while reviewer failures settle a tracked check when its metadata and token exist. |
| Dashboard routes, thread API, session gate, or server rendering | `tests/dashboard/`; for example `test_dashboard_thread_api.py` verifies `/dashboard/api/*` behavior, and `test_dashboard_ui.py` asserts SSR and session requirements. |
| Middleware ordering, tool composition, error recovery, or state transitions | `tests/middleware/`; each middleware has focused contract tests (e.g., `test_model_fallback_middleware.py` for retry logic, `test_stable_tool_order.py` for ordering). |

Tests are organized by system owner. Use prompt snapshots only to protect rendered output, configuration precedence, tool composition, or behavioral results—not to restate static prompt text.

## Frontend tests: Vitest and Node

### Dashboard unit tests

For dashboard component rendering, client-side utilities, and state management, use Vitest:

```bash
pnpm --filter open-swe-dashboard run test
```

Vitest runs via `vitest run` from the dashboard workspace. Write tests for React rendering, optimistic state, stream transformation, terminal state transitions, and API-client behavior before escalating to browser E2E.

### Desktop unit tests

For Electron main-process code, use Node's built-in test harness:

```bash
pnpm --dir desktop run test
```

The desktop package builds its main bundle first, then runs `node --test test/*.test.cjs`. Tests verify IPC, window lifecycle, git operations, and local-agent invocation without launching a full browser.

### Root test delegation

`pnpm test` at the repository root delegates all workspace test tasks to Turbo, running dashboard Vitest and desktop Node tests in parallel. Use it after broad changes; otherwise target the workspace:

```bash
pnpm --filter open-swe-dashboard run test
pnpm --dir desktop run test
```

## End-to-end: Playwright with controlled boundaries

The E2E suite proves production integration without relying on live SaaS. It runs the real agent through `langgraph dev`, real webhook routes, tools, middleware, local sandbox provider, and real git against a seeded local bare remote. Only the LLM and external SaaS HTTP endpoints are faked; everything else is production code.

```mermaid
sequenceDiagram
    participant PW as Playwright
    participant Slack as Mock Slack UI
    participant Harness as E2E harness
    participant API as Real webhook API
    participant Agent as Real agent graph
    participant Git as Local sandbox and git
    participant Hub as Mock GitHub API
    PW->>Slack: Submit request
    Slack->>Harness: Simulate signed event
    Harness->>API: POST Slack webhook
    API->>Agent: Dispatch run
    Agent->>Git: Edit, commit, push
    Agent->>Hub: Create pull request
    Agent->>Slack: Post thread reply
    PW->>Slack: Assert PR link in thread
```

The E2E flow drives the entire happy path: user mention in Slack → real agent implementation → PR opened → PR link posted back to the same thread.

### E2E architecture and fakes

- **Real pieces**: Webhook routes, agent graph, deepagents loop, tools, middleware, real local sandbox provider, real git, real browser UI, real dashboard server rendering.
- **Faked boundaries**: LLM (scripted `BaseChatModel` in `fake_llm.py`), GitHub and Slack HTTP endpoints, token mint and installation lookups, LangSmith snapshot service.
- **Controlled state**: In-memory PR/Slack stores (`fakes.py`) that serve the mock UIs and enforce eligibility rules; seeded local bare remote that the agent clones and pushes.

The harness overlays the real `agent.webapp` with fake endpoints, mock UIs at `/mock/{slack,github}`, and control routes (`/control/reset`, `/control/login`, `/control/github-event`) that let the test driver compose requests and inspect state. It signs simulated Slack Events API deliveries before posting them to the real webhook route.

### Browser E2E: real dashboard and full flow

The browser suite drives the **actual built `ui/` React application**—not a mock—served same-origin from the harness so the session cookie and `/dashboard/api/*` calls work without CORS. Global setup (`global-setup.ts`) builds the real dashboard once (cached unless `E2E_FORCE_UI_BUILD=1`), starts its Nitro server on `E2E_UI_PORT` (default 3100), and the harness proxies page requests to it. This exercises real server rendering—the root session gate, the redirect, hydration—instead of a static shell.

`full_flow.spec.ts` proves the Slack request → implementation → PR → same-thread reply path. The browser directory also contains specs for dashboard/thread behavior, environment and plan approval, Slack redelivery/debouncing, SSR, output iframe, sandbox identity, and workspace scenarios.

```bash
pnpm install --frozen-lockfile
pnpm run test:e2e:install          # install Chromium dependencies
pnpm exec playwright test tests/full_flow.spec.ts  # single spec
pnpm run test:e2e                  # full browser suite
```

The browser configuration runs serially with one worker, ignores `desktop.spec.ts`, uses a 90-second test timeout, and reuses a warm `langgraph dev` server outside CI.

### Desktop E2E: Electron local-agent flow

The desktop spec (`tests/desktop.spec.ts`) separately resets harness state, clones the seeded local bare remote into an isolated temporary project, installs a harness-issued `osw_session` cookie, invokes the local-agent request, and verifies both the local project edit and fake-GitHub PR fields. It explicitly traces the Electron context, attaches screenshots for unified and completed views, and removes temporary state unless `E2E_KEEP_TMP` is set.

```bash
pnpm run test:e2e:desktop
```

The desktop configuration selects only `desktop.spec.ts`, raises timeouts to 180 seconds, uses a separate output directory, and disables automatic Playwright media recording because the spec explicitly records an Electron trace.

Recording costs real time on every spec, so browser tests keep a **trace** (DOM-snapshot timeline + network + console + source) and a **video** only for a failed attempt; failures also get a screenshot. Set `E2E_ARTIFACTS=1` to capture both unconditionally, which is what you want when a spec passes but does the wrong thing. The Desktop test records an Electron trace and a success screenshot. Artifacts land in `test-results/<test>/` and `playwright-report/`:

```bash
pnpm exec playwright show-report                       # browse runs; each has a Trace tab
pnpm exec playwright show-trace test-results/<test>/trace.zip   # open one trace directly
```

In CI the browser shards upload **playwright-report-1**, **playwright-report-2**, and **playwright-report-3**; Desktop uploads **playwright-report-desktop**. Each contains `playwright-report/` and `test-results/`. Download the relevant artifact, then `pnpm exec playwright show-report <unzipped-dir>` (or drag a `trace.zip` onto <https://trace.playwright.dev>) to replay.

The backend requires PostgreSQL: export `POSTGRES_URI` before running the suite or `langgraph dev` (a throwaway `docker run -d -p 5433:5432 -e POSTGRES_PASSWORD=postgres postgres:16` with `POSTGRES_URI=postgresql://postgres:postgres@localhost:5433/postgres` is enough). CI provides one as a job service.

Poke at it by hand (from the repo root):

```bash
uv run langgraph dev --config tests/e2e/langgraph.e2e.json --port 2024 \
  --no-browser --allow-blocking --no-reload
# open http://127.0.0.1:2024/mock/slack  and  /mock/github
```
