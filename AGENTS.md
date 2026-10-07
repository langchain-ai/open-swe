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
| `agent` | `openswe.graphs.agent:traced_agent` | `openswe/server.py` |
| `reviewer` | `openswe.graphs.reviewer:traced_reviewer_agent` | `openswe/reviewer.py` |
| `analyzer` | `openswe.graphs.analyzer:traced_analyzer` | `openswe/analyzer.py` |
| `review-scout` | `openswe.graphs.review_scout:traced_review_scout` | `openswe/review_scout/graph.py` |
| `chat` | `openswe.graphs.chat:traced_chat_agent` | `openswe/chat.py` |
| `scheduler` | `openswe.graphs.scheduler:get_scheduler` | `openswe/scheduler.py` |

The FastAPI app is `openswe.webapp:app`. `openswe/dashboard/routes.py` only aggregates routers under `/dashboard/api`: each feature package (`openswe/threads/`, `openswe/review/`, `openswe/workspaces/`, `openswe/schedules/`, `openswe/skill_store/`, `openswe/mcp/`, `openswe/slack/`, `openswe/analytics/`, `openswe/incidents/`, `openswe/github/`, `openswe/bridge/`) exposes its own `router`, and `openswe/dashboard/` keeps auth, session, and per-user/team settings. New endpoints go in the package that owns the feature, never in `routes.py`.

The main agent is assembled in `openswe/server.py` from the middleware in `openswe/middleware/`, with tools from `openswe/tools/` and sandboxes from `openswe/sandboxes/`.

## Conventions

- Use async-only implementations. Add a sync method only when an interface requires it, and then raise `NotImplementedError`.
- Use strong types everywhere, in both Python and TypeScript. Prefer precise types, type aliases, TypedDicts/dataclasses/Pydantic models (Python) or interfaces/`satisfies` (TypeScript), and Literal/enum types over loose ones. Never use `Any` (Python) or `any` (TypeScript) — strongly discouraged even when it would be convenient; if a value's shape is dynamic, type it with a union, a generic, a protocol, or `object`/`unknown` plus narrowing instead. Widening a parameter or return type to `Any`/`any` is not acceptable to silence a type error. Expanding the scope of a PR to add or fix types is worth it.
- Put behavior on the object it acts on. Before adding a module-level function, name the object it works on: if that class exists, add a method or classmethod; if several functions pass the same value around (a request, a client, a `(backend, repo_dir)` pair), that value is a missing class. Keep free functions for framework entrypoints (FastAPI routes, LangGraph nodes, tool implementations) and helpers spanning unrelated types. A new class must own real state, never be a bag of arguments.
- Use absolute imports across packages; same-package imports may start with one dot. Never use parent-relative imports.
- Keep model-facing prompts (system prompts, tool descriptions, agent wake-up prompts) in Markdown files under `openswe/resources/prompts/` and render them with `prompt("<dir>/<name>")`, which uses `<name>.md.jinja` (Jinja for variables and conditional sections) when it exists and otherwise loads static `<name>.md` without substitutions; never inline prompt text in Python. This applies to instructions sent to the model, not ordinary user-facing copy: UI labels, Slack button/modal text, and Slack or GitHub notifications may remain inline.
- Keep comments minimal and only explain non-obvious reasons.
- For Slack interactions, prefer @mentions with plain-language requests and buttons for explicit actions. Keep typed commands, including slash commands, as optional shortcuts; never make them the only way to perform an action.
- Make user-initiated UI mutations optimistic by default: update the visible state immediately, roll it back on failure, and show an error toast. Use a non-optimistic flow when an immediate update would be unsafe or misleading. Slack Block Kit counts: update the clicked message before slow GitHub or Slack calls, then show the outcome, or restore its buttons if the click failed.
- Create database migrations with `make migration m="Short description"`.
- Use structured logging with a static message and values in `extra`; never interpolate values into log messages. Avoid standard `LogRecord` field names in `extra`.
- Prefer making API write operations exposed through UI controls available as appropriately authorized agent tools, but treat this as a guideline, not a requirement. Destructive or sensitive UI actions may remain human-only. Prefer reversible operations and existing sandbox CLIs, such as the authenticated `gh`, over adding tools.
- A person's concierge DM thread must know everything that happens in their DM with the bot. Anything Open SWE posts into that DM outside the concierge thread's own run (approval cards, notifications, messages from other threads or schedules), and every button the person clicks there, must reach the concierge thread's context. Never add a DM post or DM button without that.
- Only the agent that owns a Slack channel thread posts in it. Background work (deadlines, watchers, webhooks) hands its events to that agent instead of posting: `wake_thread_owner` starts a run when the agent must act or report, so it reads the thread first and decides what to say; `note_for_thread_owner` queues informational events (button clicks, DMs sent on the thread's behalf) for its next run without starting one. A DM sent on a thread's behalf passes `origin` to `send_dm`, which links back to that thread, tells its agent, and lets a reply to the DM know where it came from.
- Use exceptions for Python failures, not `(value, error)` or `(success, error)` returns. Convert exceptions to user-facing error results only at API/tool boundaries.
- Never discard an error. Every `except` either propagates (re-raise, or raise a more useful error) or logs what it swallowed — a bare `except ...: return None` / `pass` hides the failure from everyone debugging it later.

## Testing

Never run the full test suite locally; run only tests related to the change.

Tests are maintenance cost, not a deliverable quota. Default to no new tests unless you can name a concrete, plausible behavioral regression that existing coverage would miss. A code change alone is not justification for a test.

- Prefer extending one existing behavioral test over adding a new test file, fixture framework, or mock-heavy harness. Add the smallest deterministic test that catches the identified failure.
- Do not add tests for documentation, prompt wording, constants, mappings, source structure, trivial getters/setters, or behavior already guaranteed by types or a library. Test a meaningful observable outcome, not that the implementation matches itself.
- Reject change-detector tests, snapshots of incidental details, internal call-order assertions, and mocks that merely prove the mocked calls happened. Refactors that preserve behavior should not require mechanical test updates; rewrite or remove tests that do.
- Do not enumerate speculative edge cases or duplicate the same behavior across layers. Each case must protect a distinct, credible failure with real user impact; security, authorization, data integrity, and tricky state transitions are worth targeted coverage.
- For bug fixes, prefer a focused regression test that fails before the fix and passes after it when it adds missing behavioral coverage. Do not build elaborate scaffolding solely to test a tiny change.
- Before submitting, prune redundant or low-signal tests introduced by the change. It is correct to ship no new tests when existing coverage or a focused manual check is sufficient.

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
