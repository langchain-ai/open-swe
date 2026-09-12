# AGENTS.md

## Project

Open SWE is an asynchronous coding agent and software factory.

Each thread uses an isolated sandbox. A separate read-only reviewer graph reviews pull requests, and a review-style analyzer learns repository-specific review preferences.

`ui`, `desktop`, and `tests/e2e` form a pnpm/turbo workspace (`pnpm-workspace.yaml`). Use pnpm for them.

## Local Development

Follow [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for local startup, tunnel configuration, and preserving LangGraph state across worktrees.

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
