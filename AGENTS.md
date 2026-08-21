# AGENTS.md

This file provides guidance to Coding Agents when working with code in this repository.

## Project

Open SWE is an open-source coding-agent framework built on **LangGraph** + **Deep Agents** (`deepagents.create_deep_agent`). It runs as a LangGraph app: each thread spawns its own isolated cloud sandbox, and the agent is invoked from Slack, Linear, or GitHub (PR comments, plus auto-review on opened / ready-for-review).

Four smaller graphs sit alongside it: a read-only PR **reviewer**, a review-style **analyzer** that learns per-repo review style, a sandbox-less **chat** agent for the review UI, and a **scheduler** that fans cron ticks into runs.

## Commands

Dependencies are managed with **uv**. Tests use pytest (`asyncio_mode = "auto"`). Lint/format is **ruff** (line-length 100, target py311). Type checking is **basedpyright** (`typeCheckingMode = "standard"`). `requires-python = ">=3.11"`; `langgraph.json` pins the runtime to 3.12.

```bash
make install            # uv sync --extra dev (pytest, ruff, …)
make dev                # uv run langgraph dev — serves every graph + the FastAPI app from langgraph.json
make run                # uvicorn agent.webapp:app --reload --port 8000 (FastAPI only, no LangGraph runtime)
make test               # uv run pytest -vvv tests/
make test TEST_FILE=tests/github/test_open_pull_request.py    # single test file
uv run pytest -vvv tests/github/test_open_pull_request.py::test_name  # single test
make lint               # ruff check + ruff format --diff
make format             # ruff format + ruff check --fix
make typecheck          # basedpyright agent tests
```

`langgraph.json` declares five graph entrypoints and the FastAPI app, all served together by `langgraph dev`. Every factory lives in the module its entrypoint names, under `agent/graphs/`.

| Graph | Entrypoint | Purpose |
|---|---|---|
| `agent` | `agent.graphs.agent:traced_agent` | Main coding agent (Slack/Linear/GitHub/dashboard-triggered). |
| `reviewer` | `agent.graphs.reviewer:traced_reviewer_agent` | Read-only PR reviewer. Findings model + `publish_review`. |
| `analyzer` | `agent.graphs.analyzer:traced_analyzer` | Learns per-repo reviewer style from historical PRs and this reviewer's own finding outcomes. |
| `chat` | `agent.graphs.chat:traced_chat_agent` | Sandbox-less "chat with this PR" agent for the review UI. |
| `scheduler` | `agent.graphs.scheduler:get_scheduler` | Fans deterministic cron tasks into scheduled agent runs, `/baby-sit` PR checks, stale-run reconciliation, and cost refreshes. |

The FastAPI app is `agent.webapp:app`, a re-export of `agent/api/app.py:app`.

Opt-in PR CI monitoring lives in `agent/baby_sit.py` and the bundled `/baby-sit` skill. Signed GitHub CI webhooks trigger immediate evaluation of active watches, while per-watch scheduler crons provide a deterministic 10-minute fallback without invoking a model for unchanged state. New failures resume the originating agent thread for confidence-gated diagnosis; flake reruns are capped and deduplicated, and terminal outcomes are posted directly to the originating Slack thread.

## Architecture

### Entrypoints

- **`agent/graphs/agent.py` → `get_agent(config)`** — main graph factory. Called per-thread. Starts the thread's sandbox in the background, resolves the team/profile/per-thread model + effort, loads the optional integration tools, then constructs a fresh `create_deep_agent(...)` with the tool list and middleware stack. It passes `system_prompt=""`: the prompt is rendered per run by `PrepareAgentRunMiddleware` (same file), which is also where the sandbox is awaited, the GitHub token resolved, and the turn checkpoint recorded. The agent itself is stateless — all per-thread state lives in the sandbox + thread metadata.
- **`agent/graphs/reviewer.py` → `get_reviewer_agent(config)`** — reviewer graph factory. Shares `ensure_sandbox_for_thread` with the main agent but wires a reviewer-only toolset (`fetch_review_diff`, `add_finding`, `update_finding`, `list_findings`, `publish_review`, `resolve_finding_thread`, `reply_to_finding_thread`, `web_search`, `fetch_url`, `http_request`) and a different system prompt that pins the single-evolving-findings model and the diff-anchored bar for filing a finding. Read-only: no commit/push/PR-opening tools.
- **`agent/graphs/analyzer.py` → `get_analyzer(config)`** — small graph that emits a per-repo style prompt via the `save_review_style_prompt` tool, consumed by the reviewer as a "repository-specific review style" appendix. It runs in one of two modes (`analyzer_mode` in `configurable`): **bootstrap** (cold-start: crawl historical PR reviews) and **continual** (nightly: refine using this reviewer's own finding outcomes via `read_finding_outcomes`). Each mode's procedure lives in a deepagents **skill** (`agent/skills/bootstrap-repo-analysis/`, `agent/skills/continual-learning/`) served as virtual files via a `CompositeBackend` `/skills/` route + `StateBackend` (seeded into the run's `files` channel by the launcher — never written to the sandbox). Launchers and the per-repo nightly cron live in `agent/dashboard/review_style_jobs.py` and `agent/dashboard/analyzer_cron.py`; the cron is registered when bootstrap completes.
- **`agent/api/app.py` → `create_app()`** — the FastAPI app served alongside the LangGraph runtime. It only composes routers: dashboard, plan, workflow-approval, health, and the three webhook routers. `agent/webapp.py` re-exports `app` for `langgraph.json` and `make run`.
- **`agent/webhooks/`** — one route module per surface (`github_routes.py`, `slack_routes.py`, `linear_routes.py`) holding just signature verification and request plumbing; the event handling lives beside it in `github.py` / `slack.py` / `linear.py`, and everything shared (repo resolution, allow-list gates, thread metadata) in `common.py`; thread-id derivation is in `agent/thread_ids.py`. GitHub `opened` / `ready_for_review` events also start a reviewer run when the repo+author opt in.
- **`agent/dispatch.py` → `dispatch_agent_run()`** — the single way a run is started, from webhooks and the dashboard alike. Runs are durable: `multitask_strategy="interrupt"` so a follow-up preempts the active run and resumes with full history, `durability="sync"` so a crash resumes from the last checkpoint, `stream_resumable=True` so a client that attaches later can replay, and a completion webhook so a run that dies still ends with a signal. That webhook lands on `POST /webhooks/run-complete` (`agent/api/health.py`) and is handled by `agent/completion.py`; `agent/reconcile.py` sweeps runs that ended without any signal at all.
- **`agent/dashboard/`** — `router` mounted under the FastAPI app at startup (`app.include_router(dashboard_router)`). Owns GitHub OAuth, per-user profiles, admin endpoints, team defaults, enabled-repo lists, review-style management, and the Agents chat thread API used by the UI in `ui/`.

### Sandbox lifecycle (the tricky part)

`SANDBOX_BACKENDS` (in `agent/utils/sandbox_state.py`) is an in-process dict keyed by `thread_id`. Thread metadata persists `sandbox_id` across processes. `ensure_sandbox_for_thread` handles three cases:

1. Sandbox cached in memory → refresh the GitHub proxy.
2. Metadata has an id but no cache → reconnect, then refresh the GitHub proxy.
3. No sandbox at all → create one and persist the id.

Nothing pings the box first: the proxy refresh has to reach it anyway, and raises the same unreachable error when it cannot.

Only case 3 creates. An existing sandbox that can't be reached raises `SandboxUnreachableError` (`agent/utils/sandbox_state.py`) rather than being replaced: a replacement is empty, so swapping one in would destroy uncommitted work while looking like a recovery. The main agent catches that in `PrepareAgentRunMiddleware` and notifies the user via `post_sandbox_unreachable_notification`. A *deleted* sandbox (`SandboxGoneError`) is always replaced — it holds nothing, and the stale id in thread metadata is what every later run reconnects to, so refusing would brick the thread.

`allow_replacement=True` opts out of that protection and is passed **only** by the reviewer (`agent/graphs/reviewer.py:_ensure_reviewer_sandbox_for_thread`), whose sandbox holds nothing but a checkout `prepare_review_repo` re-derives every run. Reviewer threads are one-per-PR and outlive their sandbox, so without this a deleted sandbox bricks reviews on that PR permanently.

For `SANDBOX_TYPE=langsmith` (default), every sandbox creation/refresh also calls `_configure_github_proxy` with a fresh GitHub App installation token (`get_github_app_installation_token`). The proxy injects Basic auth for `github.com` git traffic and Bearer auth for `api.github.com`, so sandbox commands run plain `gh ...` with no real token in the sandbox. Other providers (modal, daytona, runloop, e2b, local) skip the proxy step. Provider is selected via `SANDBOX_TYPE`; factory is `agent/utils/sandbox.py:create_sandbox` (`SANDBOX_FACTORIES` maps each provider name to a creator in `agent/integrations/`).

Every run re-applies `git config --global user.name/email` for the bot identity, because reused/reconnected sandboxes can lose `--global` config and Vercel preview deploys reject commits whose author email doesn't resolve to a GitHub account.

`PrepareAgentRunMiddleware` also snapshots the worktree into `refs/open-swe/turns/<user-message-id>` at run start (`utils/turn_checkpoint.py`), recording the refs in thread metadata. `GET /threads/{id}/turn-diff` reads them back so the dashboard's changed-files views come from git rather than from replaying edit tool calls — which is the only way to catch edits made through `execute` and to drop files that were later reverted.

### Middleware stack (order matters)

The stack is the `middleware=[...]` list in `agent/graphs/agent.py:get_agent` — read it there; it is ~20 entries long and several are conditional. Order is outermost-first, so the last entry sits closest to the provider call. The load-bearing ones:

- `PrepareAgentRunMiddleware` — first, so everything else runs against a prepared run: it awaits the sandbox, renders the system prompt, and records the turn checkpoint.
- `ModelCallLimitMiddleware` (from `langchain.agents.middleware`) — caps model calls at `MODEL_CALL_RECURSION_LIMIT` (~half of `DEFAULT_RECURSION_LIMIT`); `exit_behavior="end"`.
- `ToolErrorMiddleware` — catches tool exceptions and surfaces them as tool messages; it is also what notices an unreachable sandbox and notifies the user.
- `SubdirAgentsReadMiddleware` — appends applicable ancestor `AGENTS.md` instructions to `read_file` results once per run, so scoped rules are visible before edits.
- `PullRequestCreationGuardMiddleware` / `WorkflowPushGuardMiddleware` — wrap tool calls to block `gh pr create`-style fallbacks and unapproved `.github/workflows` pushes.
- `check_message_queue_before_model` — pulls Linear comments / Slack messages that arrived mid-run from the thread queue and injects them as user messages before the next LLM call. This is what makes "message the agent while it's working" work.
- `notify_step_limit_reached` — after-agent hook that posts a Slack reply when the agent hits the step limit, so the user gets a clear signal instead of silence.
- `ModelFallbackMiddleware` (conditional) — added only when `LLM_FALLBACK_MODEL_ID` or the per-model default fallback differs from the primary model.
- `SanitizeThinkingBlocksMiddleware` — strips malformed empty Anthropic thinking blocks immediately before provider calls.
- `ModelCallTimeoutMiddleware` — innermost. Caps a single model call at `OPEN_SWE_MODEL_CALL_TIMEOUT_SECONDS` (default 15 min) so a stalled provider connection raises instead of parking the run; the timeout escalates outward to `ModelFallbackMiddleware`. Complements the per-request `timeout` `agent/utils/model.py` sets on every provider. Subagents compile into their own graphs, so each `SubAgent` spec carries its own instance (`_subagent_model_middleware`) — parent middleware never wraps a delegated `task`'s model calls, and a wedged one escalates via `ToolRetryMiddleware`'s `task` retry.

The system prompt instructs the agent to call a tool every turn; nothing re-injects one. A model turn with no tool call ends the run, which is also how a run that should stay silent (an untagged Slack message not addressed to the agent) is meant to end.

The other graph factories build their own shorter lists from the same modules — see `agent/graphs/reviewer.py`, `agent/graphs/chat.py`, and `agent/graphs/analyzer.py`.

There is intentionally no after-agent safety net that opens a PR for the agent. The agent itself is responsible for committing, pushing, opening/updating the draft PR, and replying in the source channel — via `gh` and `git` in the sandbox, the `open_pull_request` tool for creating the PR (`PullRequestCreationGuardMiddleware` blocks `gh pr create` so the PR is attributed to the triggering user), and `slack_thread_reply` / `linear_comment`.

### Tools

All tools live in `agent/tools/` and are flat-imported via `agent/tools/__init__.py`. The set is curated, not accumulated — see README "Tools — Curated, Not Accumulated".

Agent/UI parity is a product principle: anything users can do in the dashboard UI should generally also be possible through an agent tool, subject to the same authorization and safety boundaries. When adding a UI capability, add or extend the corresponding curated tool unless there is a documented reason not to.

The authoritative list is `static_tools` in `agent/graphs/agent.py:get_agent`; read it there rather than trusting a copy. It is roughly three dozen tools in these categories: web/HTTP research, Slack, Linear, GitHub/PR (`open_pull_request`, `request_pr_review`, `manage_baby_sit`), planning (`enter_plan_mode`, `save_plan`, `approve_plan`), thread discovery and scheduling, sandbox/environment (background execution, sandbox recreation, file downloads), and user settings/skills. Parts of it are conditional: Slack tools are dropped without trusted Slack context, environment tools are added only for admin threads, and stop-summary and desktop runs get a cut-down list. Integration tools (Corridor, observability, Currents, Notion) are loaded per run and exposed through `DynamicToolMiddleware`; browser automation is a subagent, not a tool.

`list_threads`, `get_thread`, and `manage_thread` are parent-agent-only; `manage_thread` is unavailable during plan mode while the two read-only tools remain available.

The reviewer, analyzer, and chat graphs each wire their own short list in their own factory; the reviewer's is findings-centric (`add_finding`, `update_finding`, `publish_review`, …) and the analyzer's is `save_review_style_prompt` plus `read_finding_outcomes`.

Built-in deepagents tools (`read_file`, `write_file`, `edit_file`, `delete`, `ls`, `glob`, `grep`, `execute`, `task` for subagent spawning, …) are added by `create_deep_agent` itself; don't duplicate them.

### Models, profiles, and team defaults

Model + reasoning effort are resolved per run in this precedence (highest wins):

1. Per-run config (`agent_model_id` + `agent_effort` in `configurable`) — set by webhooks/UI. This is the only thing allowed to move a thread off its stored settings, and the new choice is stored in turn.
2. Settings already stored on the thread (`agent/utils/thread_settings.py`), frozen on the thread's first run.
3. Per-user dashboard profile override (`agent/dashboard/agent_overrides.py:load_profile`), keyed by resolved GitHub login.
4. Team default (`agent/dashboard/team_settings.py:get_team_default_model_pair("agent")`, which resolves the main and subagent models together).

Custom instructions come from two stores: per-repo instructions (`agent/dashboard/agent_instructions.py`) are layered into the system prompt, while per-user instructions (`agent/dashboard/user_instructions.py`) are attached to each triggering user's message so multi-party threads do not inherit another participant's preferences. Repo instructions and `AGENTS.md` win over user-level ones on conflict.

Supported model IDs and per-model effort/reasoning rules live in `agent/dashboard/options.py`. Profile preferences also control draft PRs and CI automation. Model construction goes through `agent/utils/model.py` (`make_model`, `provider_model_kwargs`, `fallback_model_id_for`).

### Auth

- **GitHub**: dual-mode. User OAuth tokens are encrypted at rest in the dashboard OAuth store and cached only in process during a run (`utils/auth.py:resolve_github_token`, `utils/github_token.py`). When no user token is available, falls back to a GitHub App installation token (`utils/github_app.py`). The installation token is also what configures the LangSmith sandbox's GitHub proxy.
- **Webhooks**: GitHub signatures verified in `utils/github_comments.py:verify_github_signature`; Slack/Linear handled in their respective utils.
- **Dashboard / UI**: GitHub OAuth login lives in `agent/dashboard/oauth.py` and `routes.py` (`/auth/login`, `/auth/callback`, `/auth/logout`, `/me`).

### Thread-id derivation

Webhooks compute deterministic thread ids so the same Linear issue / Slack thread / PR routes back to the same running agent. Every derivation lives in `agent/thread_ids.py` (`reviewer_thread_id`, `pr_comment_thread_id`, `linear_issue_thread_id`, `github_issue_thread_id`, `slack_thread_id`, `review_style_thread_id`, `thread_id_from_branch`, …), with `tests/agent/test_thread_ids.py` pinning each formula — these ids are persisted, so changing one orphans live threads. `resolve_slack_thread_id` (`utils/slack.py`) resolves through a stored mapping rather than hashing. Reviewer threads are tagged with `REVIEWER_THREAD_KIND` metadata so the FastAPI side can find them.

## Conventions

- Tests are unit-only by default (`tests/`). Integration tests would go under `tests/integration_tests/` (currently empty — `make integration_tests` no-ops if missing).
- New sandbox providers: add a module under `agent/integrations/` and wire it into `SANDBOX_FACTORIES` in `agent/utils/sandbox.py`. See `docs/CUSTOMIZATION.md`.
- New tools: add to `agent/tools/`, export from `agent/tools/__init__.py`, add to `static_tools` in `server.py:get_agent` (or the graph's own `tools=[...]` list for reviewer/analyzer/chat-only tools).
- New middleware: add to `agent/middleware/`, register it in `_MIDDLEWARE_MODULES`/`__all__`/the `TYPE_CHECKING` block of `agent/middleware/__init__.py`, then add it to the `middleware=[...]` list in `server.py:get_agent` — order is significant (see the stack above).
- Async-only: this app runs exclusively async, so do not add sync/async dual implementations. Implement only the async variant (`awrap_*`, `_arun`, etc.); the sync counterpart is never invoked. Omit the sync method entirely when the interface allows it (e.g. `AgentMiddleware` already raises `NotImplementedError` on the sync path). Only when a type/ABC requires the sync method to exist (e.g. `BaseTool._run` is abstract), define it with a bare `raise NotImplementedError` rather than a real sync implementation.
- New dashboard endpoints: add to `agent/dashboard/routes.py`. The router is auto-mounted on the FastAPI app.
- New graphs: add the factory module, re-export it from a module under `agent/graphs/`, and register that entrypoint in `langgraph.json` under `graphs`.
- Minimal-to-no code comments — only when the *why* isn't obvious from the code.

<!-- OPENWIKI:START -->

## OpenWiki

This repository uses OpenWiki for recurring code documentation. Start with `openwiki/quickstart.md`, then follow its links to architecture, workflows, domain concepts, operations, integrations, testing guidance, and source maps.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->
