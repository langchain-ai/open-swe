# AGENTS.md

## Project

Open SWE is an asynchronous coding agent and software factory.

Each thread uses an isolated sandbox. A separate read-only reviewer graph reviews pull requests, and a review-style analyzer learns repository-specific review preferences.

`ui`, `desktop`, and `tests/e2e` form a pnpm/turbo workspace (`pnpm-workspace.yaml`). Use pnpm for them.

## Local Development

- Use `make dev-ui` for the backend plus Vite, and open the dashboard at `http://localhost:2024`. `make dev` starts only the backend; without a dashboard build or Vite, a healthy `/ok` does not mean the UI is ready.
- Check existing processes and ports before starting. When switching worktrees, stop the previous backend gracefully and wait for it to release port 2024 before starting the replacement.
- Always run an ngrok tunnel when starting Open SWE locally. Reuse the existing tunnel and its exact configured domain, forwarding to the active backend (normally localhost:2024). If no tunnel is running, recover the domain from configuration or prior local runtime notes before starting one; an automatically assigned hostname will not match existing webhook settings.
- Set `SLACK_PUBLIC_BASE_URL` to the active ngrok HTTPS URL. Keep dashboard/API URLs on localhost. Preserve the webhook traffic policy and the Slack OAuth callback redirect from `/dashboard/api/slack/callback` to localhost, including the complete query string.
- Before reporting readiness, verify backend health, the dashboard in a browser, ngrok forwarding, and the OAuth callback redirect. Record the worktree, process IDs, fixed tunnel domain, and state location in ignored `logs/local-dev/` notes in the primary checkout so the next session can reuse them.

### LangGraph State Across Worktrees

`langgraph dev` persists local threads, checkpoints, and Store data under `.langgraph_api` in its working directory. A fresh worktree otherwise starts with separate, empty state.

- Preserve existing local data when moving development to a new worktree unless the user requests a clean start. Stop the source backend gracefully so it flushes persistence, back up its state, and copy the entire `.langgraph_api` directory, including hidden files, into the new worktree before startup. Preserve any existing destination state rather than overwriting it automatically.
- A copy is a snapshot: subsequent changes in the two worktrees diverge. For one continuous local instance across worktrees, link `.langgraph_api` to the primary checkout's state directory instead. Only one backend may use that shared directory at a time; restart from the desired worktree to switch code.
- Copied state also retains schedules and integration settings. Avoid running multiple copies against the same live integrations, which can duplicate background work. Keep state, backups, and environment files ignored by Git; reference the existing local environment without printing credentials.

## Architecture

`langgraph.json`:

| Graph | Entrypoint | Implementation |
|---|---|---|
| `agent` | `agent.graphs.agent:traced_agent` | `agent/server.py` |
| `reviewer` | `agent.graphs.reviewer:traced_reviewer_agent` | `agent/reviewer.py` |
| `analyzer` | `agent.graphs.analyzer:traced_analyzer` | `agent/analyzer.py` |
| `chat` | `agent.graphs.chat:traced_chat_agent` | `agent/chat.py` |
| `scheduler` | `agent.graphs.scheduler:get_scheduler` | `agent/scheduler.py` |

The FastAPI app is `agent.webapp:app`; dashboard routes live in `agent/dashboard/`.

The main agent is assembled in `agent/server.py` from the middleware in `agent/middleware/`, with tools from `agent/tools/` and sandboxes from `agent/sandboxes/`.

## Conventions

- Use async-only implementations. Add a sync method only when an interface requires it, and then raise `NotImplementedError`.
- Use absolute imports across packages; same-package imports may start with one dot. Never use parent-relative imports.
- Keep comments minimal and only explain non-obvious reasons.
- Use structured logging with a static message and values in `extra`; never interpolate values into log messages. Avoid standard `LogRecord` field names in `extra`.

## Testing

Never run the full test suite locally; run only tests related to the change.

Add tests only when they meaningfully protect observable behavior. Do not add change-detector tests that merely restate constants, mappings, prompt text, source structure, or incidental interactions such as internal call order. Refactors that preserve behavior should not require mechanical test updates; rewrite or remove tests that do. Cover meaningful edge cases and keep tests deterministic.

## Pull Requests

Titles are linted as Conventional Commits by `.github/workflows/pr_lint.yml`: `<type>: <description>`, or `<type>(<scope>): <description>` since the scope is optional. The type must be one of `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`, `release`. The `ignore-lint-pr-title` label bypasses the check.

Do not include test-running or validation sections in descriptions.

<!-- OPENWIKI:START -->

## OpenWiki

This repository has a generated `openwiki/` evidence index. It is optional just-in-time context, not required startup reading.

- Treat source code and tests as authoritative. A brief's unknowns and review items are verification gaps, not automatic requirements.
- Prefer the narrowest quiet validation that proves the changed behavior. Preserve complete failure output.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->
