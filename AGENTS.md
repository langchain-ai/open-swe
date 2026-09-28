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
| `review-scout` | `agent.graphs.review_scout:traced_review_scout` | `agent/review_scout/graph.py` |
| `chat` | `agent.graphs.chat:traced_chat_agent` | `agent/chat.py` |
| `scheduler` | `agent.graphs.scheduler:get_scheduler` | `agent/scheduler.py` |

The FastAPI app is `agent.webapp:app`. `agent/dashboard/routes.py` only aggregates routers under `/dashboard/api`: each feature package (`agent/threads/`, `agent/review/`, `agent/workspaces/`, `agent/schedules/`, `agent/skill_store/`, `agent/mcp/`, `agent/slack/`, `agent/analytics/`, `agent/incidents/`, `agent/github/`, `agent/bridge/`) exposes its own `router`, and `agent/dashboard/` keeps auth, session, and per-user/team settings. New endpoints go in the package that owns the feature, never in `routes.py`.

The main agent is assembled in `agent/server.py` from the middleware in `agent/middleware/`, with tools from `agent/tools/` and sandboxes from `agent/sandboxes/`.

## Conventions

- Use async-only implementations. Add a sync method only when an interface requires it, and then raise `NotImplementedError`.
- Use strong types everywhere, in both Python and TypeScript. Prefer precise types, type aliases, TypedDicts/dataclasses/Pydantic models (Python) or interfaces/`satisfies` (TypeScript), and Literal/enum types over loose ones. Never use `Any` (Python) or `any` (TypeScript) — strongly discouraged even when it would be convenient; if a value's shape is dynamic, type it with a union, a generic, a protocol, or `object`/`unknown` plus narrowing instead. Widening a parameter or return type to `Any`/`any` is not acceptable to silence a type error. Expanding the scope of a PR to add or fix types is worth it.
- Use absolute imports across packages; same-package imports may start with one dot. Never use parent-relative imports.
- Keep model-facing prompts (system prompts, tool descriptions, agent wake-up prompts) in Markdown files under `agent/resources/prompts/` and render them with `prompt("<dir>/<name>")`, which uses `<name>.md.jinja` (Jinja for variables and conditional sections) when it exists and otherwise loads static `<name>.md` without substitutions; never inline prompt text in Python. This applies to instructions sent to the model, not ordinary user-facing copy: UI labels, Slack button/modal text, and Slack or GitHub notifications may remain inline.
- Keep comments minimal and only explain non-obvious reasons.
- Create database migrations with `make migration m="Short description"`.
- Use structured logging with a static message and values in `extra`; never interpolate values into log messages. Avoid standard `LogRecord` field names in `extra`.
- Prefer making API write operations exposed through UI controls available as appropriately authorized agent tools, but treat this as a guideline, not a requirement. Direct UI controls may ship without a corresponding tool, especially for secret input until a secure tool-driven input flow exists. Do not add a tool when the agent can already do the same thing through a CLI in its sandbox, such as the authenticated `gh`.
- Never discard an error. Every `except` either propagates (re-raise, or raise a more useful error) or logs what it swallowed — a bare `except ...: return None` / `pass` hides the failure from everyone debugging it later.

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
