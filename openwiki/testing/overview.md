---
type: testing strategy
title: Testing Strategy and Focused Validation
description: Choose the narrowest Python, frontend, or Playwright suite that owns an Open SWE behavior. This guide maps shared fakes and real integration seams to the contracts they protect.
tags: [testing, pytest, vitest, playwright, sandbox, webhooks, reviewer]
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
verified:
  - by: openwiki/0.4.2
    at: 2026-09-26T08:14:17.321Z
generated: { by: "openwiki/0.4.2", at: "2026-09-26T08:14:17.321Z" }
---

# Testing Strategy and Focused Validation

Validate at the lowest layer that owns the changed observable contract. Use focused pytest for Python graph, reviewer, sandbox, webhook, API, and tool behavior; use dashboard Vitest for React rendering and client state; use desktop Node tests for Electron main-process code. Escalate to Playwright when the contract crosses the real webhook, authenticated dashboard, local git/sandbox, or Electron boundary. Start with the owning file or node, then broaden only when the change crosses another seam.

```mermaid
flowchart TD
    Change["Changed behavior"] --> Owner{"Owning boundary"}
    Owner -->|"Agent reviewer sandbox webhook"| Pytest["Focused pytest"]
    Owner -->|"Dashboard rendering or client state"| Vitest["Dashboard Vitest"]
    Owner -->|"Electron main process"| NodeTest["Desktop Node test"]
    Owner -->|"Real service crossing"| Browser["Focused Playwright"]
    Pytest --> Gate["Relevant quality gate"]
    Vitest --> Gate
    NodeTest --> Gate
    Browser --> Gate
```

This routing keeps feedback narrow while reserving end-to-end execution for independently stateful or deployed boundaries.

## Python: behavior and assembly contracts

Pytest collects `tests/` and runs in asyncio auto mode, so asynchronous test functions and fixtures need no per-test asyncio marker. The suite is organized by owner—among others `agent`, `reviewer`, `sandbox`, `webhooks`, `dashboard`, `github`, `slack`, `middleware`, and `tools`—rather than by a single generic integration tier.

Prefer a test of rendered configuration, composition, state transition, or externally visible result over a snapshot of static prompt text. `tests/agent/test_agent_assembly_context.py` captures the arguments passed to `create_deep_agent`; it protects the initialized sandbox-backed composite backend that enables deepagents context eviction and summarization, as well as source- and visibility-sensitive skills and tools, middleware placement, and parent-versus-subagent tool boundaries. It is the focused seam for graph wiring, backend, skill, middleware, or authorization changes.

### Shared test isolation

`tests/conftest.py` makes ordinary tests independent of a running Store, local dashboard build, and process-global state:

- `fake_store` routes `agent.store` through an in-memory `FakeStore`, but retains the production `model_dump`/`model_validate` round trip. Seed it only when persisted state is part of the behavior under test.
- The autouse dashboard fixture sets `DASHBOARD_STATIC_DIR` to a missing temporary path, so a developer's `ui/.output` cannot change Python behavior.
- TTL cache and both sandbox registries are cleared before and after each case; cached workspace settings or a leaked sandbox handle therefore cannot answer for a later test.
- Workspace-store import is treated as completed and automatic review is enabled by default. Tests of fail-closed workspace routing or the review opt-in gate must deliberately replace those defaults.

### Which Python family protects which failure

| Change | Focused location and protected behavior |
| --- | --- |
| Main-agent construction, source-specific tools, skills, middleware, or sandbox backend | `tests/agent/test_agent_assembly_context.py`; assembly and context-management seams. |
| Reviewer findings, publish/reconcile behavior, check runs, approval settings, or learning outcomes | `tests/reviewer/`; `test_reviewer_outcomes.py` maps resolved/dismissed status and GitHub/Slack feedback to true/false-positive outcomes, while missing credentials or repo configuration is a no-op. |
| Sandbox proxy reconnect, capture offload, or identity recovery | `tests/sandbox/test_sandbox_state.py`; the proxy remains `BaseSandbox` compatible, delegates or safely falls back from offload, shares one reconnect, survives waiter cancellation, retries failed startup, and can recover an ID from thread metadata. |
| Completion notifications and reviewer cleanup after terminal failures | `tests/webhooks/test_completion_webhook.py`; Slack-context failures post and record a failure reply, reviewer failures settle a tracked GitHub check only when metadata and token exist, and failed cleanup cannot suppress the Slack reply. |

## Commands and independent gates

Install Python development tooling with `make install`, which runs `uv sync --extra dev`. The `dev` extra supplies `pytest`, `pytest-asyncio`, `Ruff`, and `ty`; Pygments is a normal runtime dependency. `make test` (also `make tests`) runs `uv run pytest -vvv $(TEST_FILE)` only when `TEST_FILE` is an existing file or directory; otherwise it prints a skip message. Use direct pytest for a node ID, because `file.py::test_name` is not a filesystem path.

```bash
make install
make test TEST_FILE=tests/sandbox/test_sandbox_state.py
uv run pytest -vvv tests/sandbox/test_sandbox_state.py::test_sandbox_proxy_retries_failed_startup
make lint
make typecheck
```

Tests are not a substitute for quality gates: `make lint` runs Ruff checking plus a format diff, `make format` fixes formatting and lint issues in place, and `make typecheck` runs `ty check agent tests`.

For frontend changes, target the owning workspace before root `pnpm test`, which delegates workspace test tasks to Turbo:

```bash
pnpm --filter open-swe-dashboard run test
pnpm --dir desktop run test
```

The dashboard command is `vitest run`, for components and client utilities. The desktop command builds its main bundle, then runs `node --test test/*.test.cjs`; use it for main-process behavior before invoking Electron E2E.

## Playwright: real paths with controlled SaaS seams

The E2E harness proves the production integration without live SaaS. It runs the real graph through `langgraph dev`, webhook routes, tools, middleware, workspace/store behavior, a local temp-directory sandbox, and git against a seeded local bare remote. It replaces the LLM with a scripted `BaseChatModel` and supplies fake Slack/GitHub HTTP services, credential paths, and snapshot service. The fake Slack and GitHub stores are the single source of truth rendered by their mock UIs, so assertions see the result the real agent wrote.

```mermaid
sequenceDiagram
    participant PW as Playwright
    participant Slack as Fake Slack UI
    participant Harness as E2E harness
    participant API as Real webhook API
    participant Agent as Real agent graph
    participant Git as Local sandbox and git
    participant Hub as Fake GitHub API
    PW->>Slack: Submit request
    Slack->>Harness: Simulate signed event
    Harness->>API: Post Slack webhook
    API->>Agent: Dispatch run
    Agent->>Git: Edit commit and push branch
    Agent->>Hub: Create pull request
    Agent->>Slack: Post thread reply
    PW->>Slack: Assert reply and pull request link
```

The browser happy path uses the real signed webhook, graph, sandbox, and git paths while controlling model and SaaS responses.

`full_flow.spec.ts` protects the user-visible Slack request → implementation → PR → same-thread reply path. The harness overlays the real `agent.webapp` with fake APIs, mock UIs, and control endpoints, including signed simulated Slack Events API delivery to the real webhook route.

The dashboard is also real, not a mock. Global setup builds the `ui/` app with the harness as its server-side API target, then runs the app's Nitro server. Browser tests drive that server origin, exercising SSR, session gating/redirects, hydration, same-origin `/dashboard/api/*` proxying, and a genuine signed session cookie. Set `E2E_FORCE_UI_BUILD=1` after UI or port changes to force a rebuild.

```bash
pnpm install --frozen-lockfile
pnpm run test:e2e:install
pnpm exec playwright test tests/full_flow.spec.ts
pnpm run test:e2e:desktop
```

Install Chromium before the first browser run. The browser configuration is serial with one worker, excludes `desktop.spec.ts`, uses a 90-second test timeout, and reuses an existing server outside CI. The desktop configuration selects only `desktop.spec.ts`, raises test and expectation timeouts, and uses separate outputs.

### Desktop E2E

The Electron spec resets harness state, clones the seeded bare remote into an isolated temporary project, obtains and injects a harness-issued `osw_session` cookie, and runs a local-agent request. It verifies both the actual project edit and the fake-GitHub PR title, branch, draft state, and changed file. It explicitly records the Electron context trace, attaches screenshots for unified and completed local-agent views, and removes temporary desktop state unless `E2E_KEEP_TMP` is set.

## Diagnostics and operating requirements

The browser suite retains screenshots on failure and keeps trace/video on failed local attempts or the first CI retry. `E2E_ARTIFACTS=1` records trace and video for every attempt under `test-results/` and `playwright-report/`. The desktop configuration disables Playwright's automatic media because the spec records and attaches its own Electron trace and screenshots.

The E2E backend requires PostgreSQL: set `POSTGRES_URI` before running the suite (CI provides a service). For a failure, inspect the trace, screenshot, and fake-boundary state before increasing a timeout or weakening an assertion.

```bash
pnpm exec playwright show-report
pnpm exec playwright show-trace test-results/<test>/trace.zip
SLOW_MO=700 pnpm exec playwright test --headed
```

## Related pages

- [Agent graph](/openwiki/architecture/agent-graph.md)
- [Sandbox lifecycle](/openwiki/architecture/sandbox-lifecycle.md)
- [Dashboard UI](/openwiki/integrations/dashboard-ui.md)
- [Quickstart](/openwiki/quickstart.md)
- [PR review workflow](/openwiki/workflows/pr-review.md)
