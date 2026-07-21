---
type: Operations and Quality Guide
title: Development, deployment, tests, and reviewer evaluation
description: Operational guide to Open SWE local runtime commands, CI, end-to-end verification, reviewer benchmarks, sandbox image maintenance, and scheduled automation.
resource: /Makefile
tags: [open-swe, operations, testing, ci, evaluation, deployment]
---
# Development, deployment, tests, and reviewer evaluation

## Local runtime

The repository uses Python 3.11+ with `uv`, pytest with async mode, and Ruff. The root `Makefile` provides the baseline:

```bash
make install              # uv sync
make dev                  # uv run langgraph dev
make run                  # FastAPI only, port 8000
make lint                 # Ruff check + formatter diff
make format-check         # formatter check
make test                 # pytest -vvv tests/
```

Use `make dev` for normal agent/dashboard development: it serves the graphs and FastAPI application declared in `langgraph.json`. `make run` is HTTP-only and does not provide the LangGraph runtime required by normal agent execution. The [runtime architecture](runtime-architecture.md) page explains the units that `make dev` exposes.

The UI has independent `pnpm` scripts in `ui/package.json`: `dev`, `build`, `test` (Vitest), `lint`, and `typecheck`. The existing installation guide documents local UI/API base URL and CORS setup; do not place live provider values into docs or tests.

## Test layers

| Layer | Evidence and purpose | Typical command |
|---|---|---|
| Python tests | `tests/` is organized by agent, dashboard, auth, GitHub, Slack, reviewer, sandbox, tools, middleware, and webhooks. | `make test` or `uv run pytest -vvv tests/reviewer/...` |
| UI tests | Vitest tests sit near UI utilities/components. | `cd ui && pnpm run test` |
| UI static checks | TypeScript, ESLint, and Vite build validate client integration. | `cd ui && pnpm run typecheck && pnpm run lint && pnpm run build` |
| E2E | Playwright drives a real runtime and built dashboard around fake external SaaS/LLM boundaries. | `cd tests/e2e && npm install && npx playwright install chromium && npx playwright test` |
| Reviewer quality | LangSmith benchmark compares published review findings to a frozen reference dataset. | `uv run python -m evals.reviewer.run_eval --limit 3` |

The E2E harness is deliberately high signal: it runs actual webhooks, agent graph, tools, middleware, a local temporary sandbox, and local git. Only the LLM and external GitHub/Slack HTTP boundaries are faked. It also builds the real dashboard and uses a real signed session cookie. This verifies the cross-domain behavior described in [workflows](workflows.md) and [dashboard](dashboard.md).

## Continuous integration and releases

`.github/workflows/ci.yml` runs on pull requests, pushes to `main`, and manual dispatch. It installs locked dependencies, runs lint, format check, unit tests, then Playwright E2E; failure reports/traces are uploaded. Run the closest equivalent checks before changing a shared flow.

Other repository automation includes semantic PR-title linting, manual reviewer evaluation against the deployment, scheduled/manual promotion from `main` to `prod`, and the scheduled OpenWiki update workflow. The OpenWiki workflow opens a documentation PR and includes `openwiki`, `AGENTS.md`, `CLAUDE.md`, and its workflow file; generated pages should remain under `openwiki/`.

## Reviewer evaluation

`evals/reviewer/` implements an offline LangSmith evaluation over 50 PRs and 136 reference findings from `withmartian/code-review-benchmark`. `run_eval.py` drives the reviewer graph, while the judge scores the final surfaced/published findings. A smoke run is:

```bash
uv run python -m evals.reviewer.run_eval --limit 3
```

The full run is normally dispatched with `.github/workflows/reviewer_eval.yml` against the deployed reviewer. It reports live state to the dashboard’s Admin → Reviewer eval screen. Evaluation runs mark themselves so review publication does not post to GitHub. Because review style prompts affect evaluation too, validate style-analysis changes with [workflows](workflows.md) in mind.

## Sandbox and operational assets

`Dockerfile` defines the base sandbox image: Python, Git/GitHub CLI, Docker CLI, `uv`, Node/Yarn, Socket `sfw`, Chromium for local Stagehand, Go, Rust, and build utilities. The image is designed for task execution, not as the application server’s deployment definition.

Scripts include sandbox snapshot creation/listing, PR merge-status inspection, and cleanup of expired one-shot wakeup cron records (`scripts/purge_wakeup_crons.py`). Provider and credential setup is intentionally left to `docs/INSTALLATION.md` and [integrations and security](integrations-security.md), where security boundaries are described without exposing values.

## Change checklist

- **Backend behavior:** run focused pytest modules plus `make lint`, `make format-check`, and the relevant end-to-end or webhook path.
- **UI/API work:** run UI type/lint/build/test plus focused dashboard tests; consider Playwright when streaming/session/thread behavior changes.
- **Review changes:** run reviewer tests; use the benchmark smoke run for changes that affect finding quality, publication, or style context.
- **Sandbox/integration changes:** test startup/config validation and failure/recovery cases under `tests/sandbox/` or `tests/tools/`; review the boundary requirements in [integrations and security](integrations-security.md).
