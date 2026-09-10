---
type: testing strategy
title: Focused Validation Strategy
description: Select the narrowest Python, frontend, or Playwright validation that owns an Open SWE change. This guide explains shared fakes, production-boundary coverage, and focused commands.
tags: [testing, pytest, vitest, playwright, sandbox, webhooks, reviewer]
verified:
  - by: openwiki/0.4.2
    at: 2026-09-08T08:15:30.533Z
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
generated: { by: "openwiki/0.4.2", at: "2026-09-08T08:15:30.533Z" }
---

# Focused Validation Strategy

Validate at the lowest layer that owns the changed observable contract. Use focused pytest tests for agent assembly, reviewer, sandbox, webhook, API, and tool behavior; use dashboard Vitest tests for React rendering and client state; use desktop Node tests for Electron main-process code. Escalate to Playwright only when the contract crosses the real webhook, authenticated dashboard, local git/sandbox, or Electron boundary. Never run the full local suite: target the owning file or one test first.

```mermaid
flowchart TD
    Change["Changed behavior"] --> Owner{"Boundary that owns it"}
    Owner -->|"Agent reviewer sandbox webhook"| Pytest["Focused pytest"]
    Owner -->|"Dashboard rendering or client state"| Vitest["Dashboard Vitest"]
    Owner -->|"Electron main process"| Node["Desktop Node test"]
    Owner -->|"Real service crossing"| Playwright["Focused Playwright spec"]
    Pytest --> Gates["Relevant quality gate"]
    Vitest --> Gates
    Node --> Gates
    Playwright --> Gates
```

This routing keeps feedback narrow while still requiring an end-to-end proof where independently deployed or stateful pieces meet.

## Python tests: contracts, not prompt snapshots

Pytest collects `tests/` and uses asyncio auto mode, so asynchronous tests and fixtures need no per-test asyncio marker. Tests are grouped by system owner, including `tests/agent/`, `tests/reviewer/`, `tests/sandbox/`, `tests/webhooks/`, `tests/dashboard/`, `tests/github/`, `tests/slack/`, `tests/middleware/`, and `tests/tools/`.

Do not add tests that merely restate static prompt text. For agent instructions, test rendered output, configuration precedence, tool composition, or a behavioral result instead. For example, the assembly tests capture the `create_deep_agent` arguments: they protect an initialized sandbox-backed composite backend required for deepagents context eviction and summarization, source-dependent skill routing, and the separation between parent and subagent tools. This is the appropriate focused location for server graph wiring, middleware, skill, backend, or authorization changes.

### Shared isolation and fakes

`tests/conftest.py` intentionally makes ordinary unit tests independent of a running LangGraph Store and of a locally built dashboard:

- `fake_store` redirects `agent.store` to an in-memory store, while retaining the production serialization round trip. Seed it only when persisted state is part of the contract.
- An autouse dashboard fixture points `DASHBOARD_STATIC_DIR` at a missing temporary directory, so a local `ui/.output` cannot change a Python test's behavior.
- The autouse TTL-cache reset runs before and after every case, preventing cached team settings from leaking.
- The autouse auto-review stub enables every repository because no live Store means the dashboard opt-in list is empty. A test of the opt-in gate must replace this stub with its intended policy.

### System-boundary test locations

Use the narrow test family that carries the failure semantics being changed:

| Change | Focused location and protected behavior |
| --- | --- |
| Main agent construction, source-specific tools, backend, skills, or middleware | `tests/agent/test_agent_assembly_context.py`; it verifies the initialized `CompositeBackend`/`SandboxBackendProxy` arrangement, read-only skills, dynamic browser-tool exposure, and parent-only tool boundaries. |
| Reviewer findings, published reviews, reconciliation, check runs, or learning outcomes | `tests/reviewer/`; for example `test_reviewer_outcomes.py` maps resolution and feedback into true/false-positive outcomes and treats absent credentials or repository data as a no-op. |
| Lazy sandbox reconnection, capture offload, or sandbox identity recovery | `tests/sandbox/test_sandbox_state.py`; it requires `BaseSandbox` compatibility, safe offload fallback, one shared reconnect, cancellation-safe waiting, retry after failed startup, and live-thread metadata fallback. Provider-specific behavior has companion tests in the same directory. |
| Completion notification and reviewer error cleanup | `tests/webhooks/test_completion_webhook.py`; errors on Slack-originated work send a thread reply and record the run, while reviewer failures settle a tracked check when its metadata and token exist. |

## Focused commands and independent gates

Install Python development dependencies with `make install`, which runs `uv sync --extra dev`. The dev group contains pytest, pytest-asyncio, Ruff, ty, and Pygments. Target a path with `TEST_FILE`; use direct pytest for a single node id because the Makefile's existence guard accepts paths, not a `file.py::test_name` node id.

```bash
make install
make test TEST_FILE=tests/sandbox/test_sandbox_state.py
uv run pytest -vvv tests/sandbox/test_sandbox_state.py::test_sandbox_proxy_retries_failed_startup
make lint
make typecheck
```

`make test` and `make tests` execute `uv run pytest -vvv $(TEST_FILE)` when the target path exists, otherwise they print a skip message. Quality gates are separate: `make lint` runs Ruff checking and a format diff, `make format` applies Ruff formatting and fixes, and `make typecheck` runs `ty check agent tests`.

For frontend changes, target the relevant workspace rather than root `pnpm test`, which delegates all workspace test tasks to Turbo:

```bash
pnpm --filter open-swe-dashboard run test
pnpm --dir desktop run test
```

The dashboard command is `vitest run`; its tests cover components and client-side utilities under `ui/src/`. The desktop command first builds the main bundle and then runs `node --test test/*.test.cjs`. Use a component/client test for rendering, optimistic state, stream transformation, terminal state, or API-client behavior before considering browser e2e.

## Playwright: real paths with controlled external seams

The E2E harness proves production integration without relying on live SaaS. It runs the real agent through `langgraph dev`, real webhook routes, tools, middleware, local sandbox provider, and real git against a seeded local bare remote. It substitutes a scripted `BaseChatModel`, external GitHub and Slack HTTP endpoints, credential/token paths, and the snapshot service. Fake Slack and GitHub stores are the source rendered by their mock UIs, so browser assertions observe what the real agent wrote.

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
    Harness->>API: POST Slack webhook
    API->>Agent: Dispatch run
    Agent->>Git: Edit commit and push branch
    Agent->>Hub: Create pull request
    Agent->>Slack: Post thread reply
    PW->>Slack: Assert reply and pull request link
```

The diagram shows the browser happy path while preserving the real webhook, graph, sandbox, and git execution paths.

The browser suite drives the real built `ui/` application, not a mock. Global setup builds it with the harness as the server-side API and E2E proxy target, starts its Nitro server, and the browser drives that UI-server origin. This exercises SSR, session gate and redirects, hydration, and the same-origin dashboard API proxy. `E2E_FORCE_UI_BUILD=1` rebuilds after UI or port changes; otherwise the built server is reused.

Use Playwright for the flow most directly related to the change. `full_flow.spec.ts` proves the Slack request to implementation, PR, and same-thread reply path. The browser directory also separates dashboard/thread behavior, environment and plan approval, Slack redelivery/debouncing, SSR, output iframe, sandbox identity, and workspace scenarios. The desktop spec separately resets harness state, clones the seeded remote into an isolated temporary project, installs a harness-issued `osw_session` cookie, and verifies both the local edit and fake-GitHub PR result.

```bash
pnpm install --frozen-lockfile
pnpm run test:e2e:install
pnpm exec playwright test tests/full_flow.spec.ts
pnpm run test:e2e:desktop
```

Install Chromium before the first browser run. The browser configuration is serial with one worker, ignores `desktop.spec.ts`, uses a 90-second test timeout, and reuses a warm server outside CI. Desktop selects only `desktop.spec.ts` with longer test and expectation timeouts and separate output directories.

## Failures and diagnostics

Browser runs retain screenshots on failure and retain trace/video on failure locally or on the first retry in CI. Set `E2E_ARTIFACTS=1` when a passing scenario needs inspection; artifacts are written below `test-results/` and `playwright-report/`. The desktop configuration turns off automatic Playwright media because its spec explicitly records an Electron trace and attaches screenshots; temporary desktop state is removed unless `E2E_KEEP_TMP` is set.

```bash
pnpm exec playwright show-report
pnpm exec playwright show-trace test-results/<test>/trace.zip
SLOW_MO=700 pnpm exec playwright test --headed
```

Inspect the trace, screenshot, and fake-boundary state before raising a timeout or weakening an assertion.

## Related pages

- [Agent graph](/openwiki/architecture/agent-graph.md)
- [Sandbox lifecycle](/openwiki/architecture/sandbox-lifecycle.md)
- [Dashboard UI](/openwiki/integrations/dashboard-ui.md)
- [Quickstart](/openwiki/quickstart.md)
- [PR review workflow](/openwiki/workflows/pr-review.md)
