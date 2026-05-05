# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Open SWE is an open-source coding-agent framework built on **LangGraph** + **Deep Agents** (`deepagents.create_deep_agent`). It runs as a LangGraph app: each thread spawns its own isolated cloud sandbox, and the agent is invoked from Slack, Linear, or GitHub PR comments.

## Commands

Dependencies are managed with **uv**. Tests use pytest (`asyncio_mode = "auto"`). Lint/format is **ruff** (line-length 100, target py311).

```bash
make install            # uv pip install -e .
make dev                # langgraph dev — run the LangGraph dev server (graph defined in langgraph.json)
make run                # uvicorn agent.webapp:app --reload --port 8000 (webhook server only)
make test               # uv run pytest -vvv tests/
make test TEST_FILE=tests/test_open_pr_middleware.py    # single test file
uv run pytest -vvv tests/test_open_pr_middleware.py::test_name  # single test
make lint               # ruff check + ruff format --diff
make format             # ruff format + ruff check --fix
```

`langgraph.json` declares the graph entrypoint as `agent.server:get_agent` and the FastAPI app as `agent.webapp:app`. Both are served together by `langgraph dev`.

## Architecture

### Two entrypoints, one process

- **`agent/server.py` → `get_agent(config)`** — the LangGraph graph factory. Called per-thread. Resolves the GitHub token, gets-or-creates the sandbox for the thread, then constructs a fresh `create_deep_agent(...)` with the full tool list and middleware stack. The agent itself is stateless — all per-thread state lives in the sandbox + thread metadata.
- **`agent/webapp.py`** — custom FastAPI routes mounted alongside the LangGraph server. This is where webhooks land (GitHub, Linear, Slack). Each webhook resolves a deterministic `thread_id` (so follow-up messages route to the same agent run), then triggers/streams a run via the `langgraph_sdk` client.

### Sandbox lifecycle (the tricky part)

`SANDBOX_BACKENDS` is an in-process dict keyed by `thread_id`. Thread metadata persists `sandbox_id` across processes. `get_agent` handles four cases:

1. Sandbox cached in memory → ping it (`echo ok`); recreate on `SandboxClientError`.
2. Metadata says `__creating__` and no cache → poll until ready (`_wait_for_sandbox_id`).
3. No sandbox at all → create one, set `__creating__` sentinel, then real id.
4. Metadata has an id but no cache → reconnect; fall back to recreate on failure.

For `SANDBOX_TYPE=langsmith` (default), every sandbox creation/refresh also calls `_configure_github_proxy` with a fresh GitHub App installation token (`get_github_app_installation_token`). The proxy injects Basic auth for `github.com` git traffic and Bearer auth for `api.github.com` so sandbox commands can use `GH_TOKEN=dummy gh ...` without storing real tokens in the sandbox. Other providers (modal, daytona, runloop, local) skip the proxy step. Provider is selected via `SANDBOX_TYPE` env var; factory is `agent/utils/sandbox.py:create_sandbox`.

### Middleware stack (order matters)

Configured in `get_agent`, runs around every model call:

1. `ToolErrorMiddleware` — catches tool exceptions.
2. `check_message_queue_before_model` — pulls Linear comments / Slack messages that arrived mid-run from the thread queue and injects them as user messages before the next LLM call. This is what makes "message the agent while it's working" work.
3. `ensure_no_empty_msg` — guards against empty assistant messages that some providers reject.
4. `notify_step_limit_reached` — after-agent hook that posts a Slack reply when the agent hits the step limit, so the user gets a clear signal instead of silence.

There is intentionally no after-agent safety net that opens a PR for the agent. The agent itself is responsible for committing, pushing, opening/updating the draft PR, and replying in the source channel — all via `GH_TOKEN=dummy gh` and `slack_thread_reply` / `linear_comment`.

### Tools

All tools live in `agent/tools/` and are flat-imported via `agent/tools/__init__.py`. The set is intentionally small and curated — see README "Tools — Curated, Not Accumulated". Built-in deepagents tools (`read_file`, `execute`, `glob`, `grep`, `task` for subagent spawning, …) are added by `create_deep_agent` itself; don't duplicate them.

### Auth

- **GitHub**: dual-mode. User OAuth tokens are encrypted-at-rest in thread metadata (`agent/encryption.py`, `utils/auth.py:resolve_github_token`). When no user token is available, falls back to a GitHub App installation token (`utils/github_app.py`). The installation token is also what configures the LangSmith sandbox's GitHub proxy.
- **Webhooks**: GitHub signatures verified in `utils/github_comments.py:verify_github_signature`; Slack/Linear handled in their respective utils.

### Thread-id derivation

Webhooks compute deterministic thread ids so the same Linear issue / Slack thread / PR routes back to the same running agent. See `utils/github_comments.py:get_thread_id_from_branch` and the equivalents in `utils/linear.py` / `utils/slack.py`.

## Conventions

- Tests are unit-only by default (`tests/`). Integration tests would go under `tests/integration_tests/` (currently empty — `make integration_tests` no-ops if missing).
- New sandbox providers: add a module under `agent/integrations/` and wire it into `utils/sandbox.py:create_sandbox`. See `CUSTOMIZATION.md`.
- New tools: add to `agent/tools/`, export from `agent/tools/__init__.py`, add to the `tools=[...]` list in `server.py:get_agent`.
- New middleware: add to `agent/middleware/`, export from `agent/middleware/__init__.py`, add to the `middleware=[...]` list in `server.py:get_agent` — order is significant.
