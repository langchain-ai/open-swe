# AGENTS.md

This file provides guidance to Coding Agents when working with code in this repository.

## Project

Open SWE is an open-source coding-agent framework built on **LangGraph** + **Deep Agents** (`deepagents.create_deep_agent`). It runs as a LangGraph app: each thread spawns its own isolated cloud sandbox, and the agent is invoked from Slack, Linear, or GitHub (PR comments, plus auto-review on opened / ready-for-review).

A separate **reviewer** graph runs read-only code reviews on PRs, and a **review-style analyzer** graph learns per-repo review style from historical PRs.

## Commands

Dependencies are managed with **uv**. Tests use pytest (`asyncio_mode = "auto"`). Lint/format is **ruff** (line-length 100, target py311). Type checking is **basedpyright** (`typeCheckingMode = "standard"`). `requires-python = ">=3.11"`; `langgraph.json` pins the runtime to 3.12.

```bash
make install            # uv sync --extra dev (pytest, ruff, …)
make dev                # uv run langgraph dev — serves all three graphs + the FastAPI app from langgraph.json
make run                # uvicorn agent.webapp:app --reload --port 8000 (FastAPI only, no LangGraph runtime)
make test               # uv run pytest -vvv tests/
make test TEST_FILE=tests/github/test_open_pull_request.py    # single test file
uv run pytest -vvv tests/github/test_open_pull_request.py::test_name  # single test
make lint               # ruff check + ruff format --diff
make format             # ruff format + ruff check --fix
make typecheck          # basedpyright agent tests
```

`langgraph.json` declares three graph entrypoints and the FastAPI app, all served together by `langgraph dev`:

| Graph | Entrypoint | Purpose |
|---|---|---|
| `agent` | `agent.server:get_agent` | Main coding agent (Slack/Linear/GitHub-triggered). |
| `reviewer` | `agent.reviewer:get_reviewer_agent` | Read-only PR reviewer. Findings model + `publish_review`. |
| `analyzer` | `agent.analyzer:get_analyzer` | Learns per-repo reviewer style from historical PRs and this reviewer's own finding outcomes. |
| `ci_monitor` | `agent.ci_monitor:get_ci_monitor` | Polling fallback for CI auto-fix: each tick sweeps open agent-authored PRs for failing checks / merge conflicts via `agent.ci_autofix.sweep_open_prs`. |

The FastAPI app is `agent.webapp:app`.

CI auto-fix ("PR babysitting") lives in `agent/ci_autofix.py`: when a CI check fails (webhook `check_run` / `check_suite` / `workflow_run` / `status`) or a reviewer leaves actionable feedback on a PR Open SWE opened, it locates the originating agent thread (by `pr_url` metadata) and dispatches a confidence-gated fix run on the `agent` graph. Gated by the per-user `auto_fix_ci` profile flag, the enabled-repos opt-in, and a per-PR `@open-swe autofix on|off` toggle (`agent/dashboard/autofix_state.py`). Skip-rules (base-branch failures, human commits, same-head dedupe, batching while runs are active, loop cap) all live in `ci_autofix.py`.

## Architecture

### Entrypoints

- **`agent/server.py` → `get_agent(config)`** — main graph factory. Called per-thread. Resolves the GitHub token, gets-or-creates the sandbox for the thread, resolves the team/profile/per-thread model + effort, then constructs a fresh `create_deep_agent(...)` with the curated tool list and middleware stack. The agent itself is stateless — all per-thread state lives in the sandbox + thread metadata.
- **`agent/reviewer.py` → `get_reviewer_agent(config)`** — reviewer graph factory. Shares `ensure_sandbox_for_thread` with the main agent but wires a reviewer-only toolset (`add_finding`, `update_finding`, `list_findings`, `publish_review`, `web_search`, `fetch_url`, `http_request`) and a different system prompt that pins the single-evolving-findings model and the diff-anchored bar for filing a finding. Read-only: no commit/push/PR-opening tools.
- **`agent/analyzer.py` → `get_analyzer(config)`** — small graph that emits a per-repo style prompt via the `save_review_style_prompt` tool, consumed by the reviewer as a "repository-specific review style" appendix. It runs in one of two modes (`analyzer_mode` in `configurable`): **bootstrap** (cold-start: crawl historical PR reviews) and **continual** (nightly: refine using this reviewer's own finding outcomes via `read_finding_outcomes`). Each mode's procedure lives in a deepagents **skill** (`agent/skills/bootstrap-repo-analysis/`, `agent/skills/continual-learning/`) served as virtual files via a `CompositeBackend` `/skills/` route + `StateBackend` (seeded into the run's `files` channel by the launcher — never written to the sandbox). Launchers and the per-repo nightly cron live in `agent/dashboard/review_style_jobs.py` and `agent/dashboard/analyzer_cron.py`; the cron is registered when bootstrap completes.
- **`agent/webapp.py`** — custom FastAPI routes mounted alongside the LangGraph server. Webhooks land here (GitHub, Linear, Slack). Each webhook resolves a deterministic `thread_id` (so follow-up messages route to the same agent run) and triggers/streams a run via the `langgraph_sdk` client. Also auto-reviews PRs on `opened` / `ready_for_review` events when the repo+author opt in.
- **`agent/dashboard/`** — `router` mounted under the FastAPI app at startup (`app.include_router(dashboard_router)`). Owns GitHub OAuth, per-user profiles, admin endpoints, team defaults, enabled-repo lists, review-style management, and the Agents chat thread API used by the UI in `ui/`.

### Sandbox lifecycle (the tricky part)

`SANDBOX_BACKENDS` (in `agent/utils/sandbox_state.py`) is an in-process dict keyed by `thread_id`. Thread metadata persists `sandbox_id` across processes. `ensure_sandbox_for_thread` handles four cases:

1. Sandbox cached in memory → ping it (`echo ok`); recreate on `SandboxClientError`. Healthy reused sandboxes also get a GitHub-proxy refresh (recreate on failure).
2. Metadata says `__creating__` and no cache → reset stale metadata so a fresh sandbox can be created.
3. No sandbox at all → set `__creating__` sentinel, create one, persist the real id.
4. Metadata has an id but no cache → reconnect; fall back to recreate on failure.

For `SANDBOX_TYPE=langsmith` (default), every sandbox creation/refresh also calls `_configure_github_proxy` with a fresh GitHub App installation token (`get_github_app_installation_token`). The proxy injects Basic auth for `github.com` git traffic and Bearer auth for `api.github.com` so sandbox commands can use `GH_TOKEN=dummy gh ...` without storing real tokens in the sandbox. Other providers (modal, daytona, runloop, e2b, local) skip the proxy step. Provider is selected via `SANDBOX_TYPE`; factory is `agent/utils/sandbox.py:create_sandbox` (`SANDBOX_FACTORIES` maps each provider name to a creator in `agent/integrations/`).

Every run re-applies `git config --global user.name/email` for the bot identity, because reused/reconnected sandboxes can lose `--global` config and Vercel preview deploys reject commits whose author email doesn't resolve to a GitHub account.

### Middleware stack (order matters)

Configured in `agent/server.py:get_agent`, runs around every model call (in this order):

1. `SanitizeToolInputsMiddleware` — strips/normalizes tool inputs before they reach tools.
2. `ModelCallLimitMiddleware` (from `langchain.agents.middleware`) — caps model calls at `MODEL_CALL_RECURSION_LIMIT` (~half of `DEFAULT_RECURSION_LIMIT`); `exit_behavior="end"`.
3. `ToolErrorMiddleware` — catches tool exceptions and surfaces them as tool messages.
4. `SubdirAgentsReadMiddleware` — appends applicable ancestor `AGENTS.md` instructions to `read_file` results once per run, so scoped rules are visible before edits.
5. `check_message_queue_before_model` — pulls Linear comments / Slack messages that arrived mid-run from the thread queue and injects them as user messages before the next LLM call. This is what makes "message the agent while it's working" work.
6. `SlackAssistantStatusMiddleware` — keeps the Slack "assistant is typing"-style status up to date around model calls.
7. `ensure_no_empty_msg` — after-model hook; when the model emits a message with no tool call (and hasn't already messaged the user or confirmed completion) it re-injects a synthetic `no_op` / `confirming_completion` tool call so the run continues instead of ending prematurely.
8. `notify_step_limit_reached` — after-agent hook that posts a Slack reply when the agent hits the step limit, so the user gets a clear signal instead of silence.
9. `SandboxCircuitBreakerMiddleware` — trips the agent out of repeated sandbox failures instead of looping.
10. `ModelFallbackMiddleware` (optional) — added only when `LLM_FALLBACK_MODEL_ID` or the per-model default fallback differs from the primary model.
11. `SanitizeThinkingBlocksMiddleware` — strips malformed empty Anthropic thinking blocks immediately before provider calls.

The system prompt instructs the agent to call a tool every turn, and `ensure_no_empty_msg` re-injects a tool call when it doesn't — together these keep runs from stopping partway through a task.

Other middleware exists in `agent/middleware/` (`ExcludeToolsMiddleware`) but isn't wired into the default agent. The reviewer uses a leaner stack: `SanitizeToolInputsMiddleware`, `ModelCallLimitMiddleware`, `ToolErrorMiddleware`, `SlackAssistantStatusMiddleware`, `SanitizeThinkingBlocksMiddleware`.

There is intentionally no after-agent safety net that opens a PR for the agent. The agent itself is responsible for committing, pushing, opening/updating the draft PR, and replying in the source channel — all via `GH_TOKEN=dummy gh` and `slack_thread_reply` / `linear_comment`.

### Tools

All tools live in `agent/tools/` and are flat-imported via `agent/tools/__init__.py`. The set is intentionally small and curated — see README "Tools — Curated, Not Accumulated".

Wired into `get_agent`:
`http_request`, `fetch_url`, `web_search`, `linear_comment`, `linear_create_issue`, `linear_delete_issue`, `linear_get_issue`, `linear_get_issue_comments`, `linear_list_teams`, `linear_search_issues`, `linear_update_issue`, `request_pr_review`, `schedule_thread_wakeup`, `slack_add_reaction`, `slack_read_thread_messages`, `slack_thread_reply`.

Reviewer-only tools (in `agent/reviewer.py`): `add_finding`, `update_finding`, `list_findings`, `publish_review`. The review-style analyzer uses `save_review_style` (exported as `save_review_style_prompt`).

Built-in deepagents tools (`read_file`, `write_file`, `edit_file`, `ls`, `glob`, `grep`, `execute`, `write_todos`, `task` for subagent spawning, …) are added by `create_deep_agent` itself; don't duplicate them.

### Models, profiles, and team defaults

Model + reasoning effort are resolved per run in this precedence (highest wins):

1. Per-thread config (`agent_model_id` + `agent_effort` in `configurable`) — set by webhooks/UI.
2. Per-user dashboard profile override (`agent/dashboard/agent_overrides.py:load_profile`), keyed by resolved GitHub login.
3. Team default model (`agent/dashboard/team_settings.py:get_team_default_model("agent")`).

Supported model IDs and per-model effort/reasoning rules live in `agent/dashboard/options.py`. Profile flags also drive run behavior — e.g. `profile_create_prs` enables the opt-in Always Create PRs policy. Model construction goes through `agent/utils/model.py` (`make_model`, `provider_model_kwargs`, `fallback_model_id_for`).

### Auth

- **GitHub**: dual-mode. User OAuth tokens are encrypted at rest in the dashboard OAuth store and cached only in process during a run (`utils/auth.py:resolve_github_token`, `utils/github_token.py`). When no user token is available, falls back to a GitHub App installation token (`utils/github_app.py`). The installation token is also what configures the LangSmith sandbox's GitHub proxy.
- **Webhooks**: GitHub signatures verified in `utils/github_comments.py:verify_github_signature`; Slack/Linear handled in their respective utils.
- **Dashboard / UI**: GitHub OAuth login lives in `agent/dashboard/oauth.py` and `routes.py` (`/auth/login`, `/auth/callback`, `/auth/logout`, `/me`).

### Thread-id derivation

Webhooks compute deterministic thread ids so the same Linear issue / Slack thread / PR routes back to the same running agent. See `utils/github_comments.py:get_thread_id_from_branch` and the equivalents in `utils/linear.py` / `utils/slack.py`. Reviewer threads have their own deterministic ids and are tagged with `REVIEWER_THREAD_KIND` metadata so the FastAPI side can find them.

## Conventions

- Tests are unit-only by default (`tests/`). Integration tests would go under `tests/integration_tests/` (currently empty — `make integration_tests` no-ops if missing).
- New sandbox providers: add a module under `agent/integrations/` and wire it into `SANDBOX_FACTORIES` in `agent/utils/sandbox.py`. See `docs/CUSTOMIZATION.md`.
- New tools: add to `agent/tools/`, export from `agent/tools/__init__.py`, add to the `tools=[...]` list in `server.py:get_agent` (or `reviewer.py` for reviewer-only tools).
- New middleware: add to `agent/middleware/`, export from `agent/middleware/__init__.py`, add to the `middleware=[...]` list in `server.py:get_agent` — order is significant (see the stack above).
- Async-only: this app runs exclusively async, so do not add sync/async dual implementations. Implement only the async variant (`awrap_*`, `_arun`, etc.); the sync counterpart is never invoked. Omit the sync method entirely when the interface allows it (e.g. `AgentMiddleware` already raises `NotImplementedError` on the sync path). Only when a type/ABC requires the sync method to exist (e.g. `BaseTool._run` is abstract), define it with a bare `raise NotImplementedError` rather than a real sync implementation.
- New dashboard endpoints: add to `agent/dashboard/routes.py`. The router is auto-mounted on the FastAPI app.
- New graphs: register the entrypoint in `langgraph.json` under `graphs`.
- Minimal-to-no code comments — only when the *why* isn't obvious from the code.
