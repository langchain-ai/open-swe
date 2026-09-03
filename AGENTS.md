# AGENTS.md

This file provides guidance to Coding Agents when working with code in this repository.

## Project

Open SWE is an open-source coding-agent framework built on **LangGraph** + **Deep Agents** (`deepagents.create_deep_agent`). It runs as a LangGraph app: each thread spawns its own isolated cloud sandbox, and the agent is invoked from Slack, Linear, or GitHub (PR comments, plus auto-review on opened / ready-for-review).

A separate **reviewer** graph runs read-only code reviews on PRs, and a **review-style analyzer** graph learns per-repo review style from historical PRs.

## Commands

Dependencies are managed with **uv**. Tests use pytest (`asyncio_mode = "auto"`). Lint/format is **ruff** (line-length 100, target py314). Type checking is **basedpyright** (`typeCheckingMode = "standard"`). `requires-python = ">=3.14"`; `langgraph.json` pins the runtime to 3.14.

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
| `scheduler` | `agent.scheduler:get_scheduler` | Fans deterministic cron tasks into scheduled agent runs, reconciliation, and `/baby-sit` PR checks. |

The FastAPI app is `agent.webapp:app`.

Opt-in PR CI monitoring lives in `agent/baby_sit.py` and the bundled `/baby-sit` skill. Signed GitHub CI webhooks trigger immediate evaluation of active watches, while per-watch scheduler crons provide a deterministic 10-minute fallback without invoking a model for unchanged state. New failures resume the originating agent thread for confidence-gated diagnosis; flake reruns are capped and deduplicated, and terminal outcomes are posted directly to the originating Slack thread.

## Architecture

### Entrypoints

- **`agent/server.py` → `get_agent(config)`** — main graph factory. Called per-thread. Resolves the GitHub token, gets-or-creates the sandbox for the thread, resolves the team/profile/per-thread model + effort, then constructs a fresh `create_deep_agent(...)` with the curated tool list and middleware stack. The agent itself is stateless — all per-thread state lives in the sandbox + thread metadata.
- **`agent/reviewer.py` → `get_reviewer_agent(config)`** — reviewer graph factory. Shares `ensure_sandbox_for_thread` with the main agent but wires a reviewer-only toolset (`add_finding`, `update_finding`, `list_findings`, `publish_review`, `web_search`, `fetch_url`, `http_request`) and a different system prompt that pins the single-evolving-findings model and the diff-anchored bar for filing a finding. Read-only: no commit/push/PR-opening tools.
- **`agent/analyzer.py` → `get_analyzer(config)`** — small graph that emits a per-repo style prompt via the `save_review_style_prompt` tool, consumed by the reviewer as a "repository-specific review style" appendix. It runs in one of two modes (`analyzer_mode` in `configurable`): **bootstrap** (cold-start: crawl historical PR reviews) and **continual** (nightly: refine using this reviewer's own finding outcomes via `read_finding_outcomes`). Each mode's procedure lives in a deepagents **skill** (`agent/skills/bootstrap-repo-analysis/`, `agent/skills/continual-learning/`) served as virtual files via a `CompositeBackend` `/skills/` route + `StateBackend` (seeded into the run's `files` channel by the launcher — never written to the sandbox). Launchers and the per-repo nightly cron live in `agent/review/style_jobs.py` and `agent/review/analyzer_cron.py`; the cron is registered when bootstrap completes.
- **`agent/webapp.py`** — custom FastAPI routes mounted alongside the LangGraph server. Webhooks land here (GitHub, Linear, Slack). Each webhook resolves a deterministic `thread_id` (so follow-up messages route to the same agent run) and triggers/streams a run via the `langgraph_sdk` client. Also auto-reviews PRs on `opened` / `ready_for_review` events when the repo+author opt in.
- **`agent/dashboard/`** — `router` mounted under the FastAPI app at startup (`app.include_router(dashboard_router)`). Owns GitHub OAuth, per-user profiles, admin endpoints, team defaults, enabled-repo lists, review-style management, and the Agents chat thread API used by the UI in `ui/`.

### Sandbox lifecycle (the tricky part)

`SANDBOX_BACKENDS` (in `agent/sandboxes/state.py`) is an in-process dict keyed by `thread_id`. Thread metadata persists `sandbox_id` across processes. `ensure_sandbox_for_thread` handles three cases:

1. Sandbox cached in memory → ping it (`echo ok`), then refresh the GitHub proxy.
2. Metadata has an id but no cache → reconnect, then refresh the GitHub proxy.
3. No sandbox at all → create one and persist the id.

Only case 3 creates. An existing sandbox that can't be reached raises `SandboxUnreachableError` (`agent/sandboxes/state.py`) rather than being replaced: a replacement is empty, so swapping one in would destroy uncommitted work while looking like a recovery. The main agent catches that in `PrepareAgentRunMiddleware` and notifies the user via `post_sandbox_unreachable_notification`.

`allow_replacement=True` opts out of that protection and is passed **only** by the reviewer (`agent/reviewer.py:_ensure_reviewer_sandbox_for_thread`), whose sandbox holds nothing but a checkout `prepare_review_repo` re-derives every run. Reviewer threads are one-per-PR and outlive their sandbox, so without this a deleted sandbox bricks reviews on that PR permanently.

For `SANDBOX_TYPE=langsmith` (default), every sandbox creation/refresh also calls `_configure_github_proxy` with a fresh GitHub App installation token (`get_github_app_installation_token`). The proxy injects Basic auth for `github.com` git traffic and Bearer auth for `api.github.com`, so sandbox commands run plain `gh ...` with no real token in the sandbox. Other providers (modal, daytona, runloop, e2b, local) skip the proxy step. Provider is selected via `SANDBOX_TYPE`; factory is `agent/sandboxes/providers/registry.py:create_sandbox` (`SANDBOX_FACTORIES` maps each provider name to a creator in `agent/sandboxes/providers/`).

Every run re-applies `git config --global user.name/email` for the bot identity, because reused/reconnected sandboxes can lose `--global` config and Vercel preview deploys reject commits whose author email doesn't resolve to a GitHub account.

### Middleware stack (order matters)

Configured in `agent/server.py:get_agent`, runs around every model call (in this order):

1. `SanitizeToolInputsMiddleware` — strips/normalizes tool inputs before they reach tools.
2. `ModelCallLimitMiddleware` (from `langchain.agents.middleware`) — caps model calls at `MODEL_CALL_RECURSION_LIMIT` (~half of `DEFAULT_RECURSION_LIMIT`); `exit_behavior="end"`.
3. `ToolErrorMiddleware` — catches tool exceptions and surfaces them as tool messages.
4. `SubdirAgentsReadMiddleware` — appends applicable ancestor `AGENTS.md` instructions to `read_file` results once per run, so scoped rules are visible before edits.
5. `check_message_queue_before_model` — pulls Linear comments / Slack messages that arrived mid-run from the thread queue and injects them as user messages before the next LLM call. This is what makes "message the agent while it's working" work.
6. `notify_step_limit_reached` — after-agent hook that posts a Slack reply when the agent hits the step limit, so the user gets a clear signal instead of silence.
7. `ModelFallbackMiddleware` (optional) — added only when `LLM_FALLBACK_MODEL_ID` or the per-model default fallback differs from the primary model.
8. `SanitizeThinkingBlocksMiddleware` — strips malformed empty Anthropic thinking blocks immediately before provider calls.
9. `ModelCallTimeoutMiddleware` — innermost. Caps a single model call at `OPEN_SWE_MODEL_CALL_TIMEOUT_SECONDS` (default 15 min) so a stalled provider connection raises instead of parking the run; the timeout escalates outward to `ModelFallbackMiddleware`. Complements the per-request `timeout` `agent/utils/model.py` sets on every provider. Subagents compile into their own graphs, so each `SubAgent` spec carries its own instance (`_subagent_model_timeout_middleware`) — parent middleware never wraps a delegated `task`'s model calls, and a wedged one escalates via `ToolRetryMiddleware`'s `task` retry.

The system prompt instructs the agent to call a tool every turn; nothing re-injects one, so a model turn with no tool call ends the run.

Other middleware exists in `agent/middleware/` (`ExcludeToolsMiddleware`) but isn't wired into the default agent. The reviewer uses a leaner stack: `SanitizeToolInputsMiddleware`, `ModelCallLimitMiddleware`, `ToolErrorMiddleware`, `SanitizeThinkingBlocksMiddleware`.

### Tools

All tools live in `agent/tools/` and are flat-imported via `agent/tools/__init__.py`. The set is intentionally small and curated — see README "Tools — Curated, Not Accumulated".

Agent/UI parity is a product principle: anything users can do in the dashboard UI should generally also be possible through an agent tool, subject to the same authorization and safety boundaries. When adding a UI capability, add or extend the corresponding curated tool unless there is a documented reason not to.

Wired into `get_agent`:
`http_request`, `fetch_url`, `web_search`, `approve_plan`, `enter_plan_mode`, `save_plan`, `save_user_instructions`, `list_threads`, `get_thread`, `manage_thread`, `manage_baby_sit`, `linear_comment`, `linear_create_issue`, `linear_delete_issue`, `linear_get_issue`, `linear_get_issue_comments`, `linear_list_teams`, `linear_search_issues`, `linear_update_issue`, `open_pull_request`, `request_pr_review`, `report_platform_issue`, `schedule_thread_wakeup`, `manage_code_channel`, `slack_add_reaction`, `slack_read_thread_messages`, `slack_start_new_thread`, `slack_thread_reply`.

`list_threads`, `get_thread`, and `manage_thread` are parent-agent-only; `manage_thread` is unavailable during plan mode while the two read-only tools remain available.

Reviewer-only tools (in `agent/reviewer.py`): `add_finding`, `update_finding`, `list_findings`, `publish_review`. The review-style analyzer uses `save_review_style` (exported as `save_review_style_prompt`).

Built-in deepagents tools (`read_file`, `write_file`, `edit_file`, `delete`, `ls`, `glob`, `grep`, `execute`, `task` for subagent spawning, …) are added by `create_deep_agent` itself; don't duplicate them.

### Models, profiles, and team defaults

Model + reasoning effort are resolved per run in this precedence (highest wins):

1. Per-thread config (`agent_model_id` + `agent_effort` in `configurable`) — set by webhooks/UI.
2. Per-user dashboard profile override (`agent/dashboard/agent_overrides.py:load_profile`), keyed by resolved GitHub login.
3. Team default model (`agent/dashboard/team_settings.py:get_team_default_model("agent")`).

Custom instructions come from two stores: per-repo instructions (`agent/dashboard/agent_instructions.py`) are layered into the system prompt, while per-user instructions (`agent/dashboard/user_instructions.py`) are attached to each triggering user's message so multi-party threads do not inherit another participant's preferences. Repo instructions and `AGENTS.md` win over user-level ones on conflict.

Supported model IDs and per-model effort/reasoning rules live in `agent/dashboard/options.py`. Profile preferences also control draft PRs and CI automation. Model construction goes through `agent/utils/model.py` (`make_model`, `provider_model_kwargs`, `fallback_model_id_for`).

### Auth

- **GitHub**: dual-mode. User OAuth tokens are encrypted at rest in the dashboard OAuth store and cached only in process during a run (`utils/auth.py:resolve_github_token`, `utils/github_token.py`). When no user token is available, falls back to a GitHub App installation token (`utils/github_app.py`). The installation token is also what configures the LangSmith sandbox's GitHub proxy.
- **Webhooks**: GitHub signatures verified in `utils/github_comments.py:verify_github_signature`; Slack/Linear handled in their respective utils.
- **Dashboard / UI**: GitHub OAuth login lives in `agent/dashboard/oauth.py` and `routes.py` (`/auth/login`, `/auth/callback`, `/auth/logout`, `/me`).

### Thread-id derivation

Webhooks compute deterministic thread ids so the same Linear issue / Slack thread / PR routes back to the same running agent. See `utils/github_comments.py:get_thread_id_from_branch` and the equivalents in `utils/linear.py` / `utils/slack.py`. Reviewer threads have their own deterministic ids and are tagged with `REVIEWER_THREAD_KIND` metadata so the FastAPI side can find them.

Slack **code channels** (`utils/slack_code_channels.py`) are a channel-per-task session. Open SWE keys Slack locations by `(channel_id, thread_ts)`, so a code channel uses the `CODE_CHANNEL_SESSION_TS` (`"0"`) sentinel as its timestamp: it satisfies the existing timestamp validation, can never collide with a real message ts, and makes default replies post top-level while preserving a real `reply_thread_ts` when the user starts a Slack thread. Channel context reads from `conversations.history`; user-started thread context reads from `conversations.replies`. Session status, runtime commands, properties, views, canvases, and archival go through `agents.sessions.*` / `agents.conversations.*`.

## Conventions

- Tests are unit-only by default (`tests/`). Integration tests would go under `tests/integration_tests/` (currently empty — `make integration_tests` no-ops if missing).
- Do not add tests that only assert or restate static prompt text. Prompt tests must verify rendering, composition, precedence, or another meaningful behavioral contract.
- New sandbox providers: add a module under `agent/sandboxes/providers/` and wire it into `SANDBOX_FACTORIES` in `agent/sandboxes/providers/registry.py`. See `docs/CUSTOMIZATION.md`.
- New tools: add to `agent/tools/`, export from `agent/tools/__init__.py`, add to the `tools=[...]` list in `server.py:get_agent` (or `reviewer.py` for reviewer-only tools).
- New middleware: add to `agent/middleware/`, export from `agent/middleware/__init__.py`, add to the `middleware=[...]` list in `server.py:get_agent` — order is significant (see the stack above).
- Async-only: this app runs exclusively async, so do not add sync/async dual implementations. Implement only the async variant (`awrap_*`, `_arun`, etc.); the sync counterpart is never invoked. Omit the sync method entirely when the interface allows it (e.g. `AgentMiddleware` already raises `NotImplementedError` on the sync path). Only when a type/ABC requires the sync method to exist (e.g. `BaseTool._run` is abstract), define it with a bare `raise NotImplementedError` rather than a real sync implementation.
- New dashboard endpoints: add to `agent/dashboard/routes.py`. The router is auto-mounted on the FastAPI app.
- New graphs: register the entrypoint in `langgraph.json` under `graphs`.
- Minimal-to-no code comments — only when the *why* isn't obvious from the code.
- Do not use parent-relative Python imports that start with `..`; use absolute imports instead. Same-package imports with a single dot, such as `.foo`, are allowed.

<!-- OPENWIKI:START -->

## OpenWiki

This repository has a generated `openwiki/` evidence index. It is optional just-in-time context, not required startup reading.

- Treat source code and tests as authoritative. A brief's unknowns and review items are verification gaps, not automatic requirements.
- Prefer the narrowest quiet validation that proves the changed behavior. Preserve complete failure output.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->
