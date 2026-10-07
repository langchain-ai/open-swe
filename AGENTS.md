# AGENTS.md

Open SWE is an asynchronous coding agent and software factory. Graphs and the FastAPI app are registered in `langgraph.json`. JS packages form a pnpm/turbo workspace (`pnpm-workspace.yaml`); use pnpm for them.

Local setup: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Constant gardening

Before making any change, first till the soil: refactor the surrounding code so the change slots in naturally and the result reads as if it had always been designed that way. Spend real effort here rather than bolting features onto whatever shape the code happens to be in.

## Conventions

- Async-only. Add a sync method only when an interface requires it, and make it raise `NotImplementedError`.
- Strong types in Python and TypeScript. Never use `Any`/`any`, including to silence a type error; use a union, generic, protocol, or `object`/`unknown` with narrowing.
- Absolute imports across packages; same-package imports may start with one dot. Never use parent-relative imports.
- New dashboard endpoints go in the `router` of the package that owns the feature, never in `agent/dashboard/routes.py`.
- Model-facing prompts live in `agent/resources/prompts/` as `<name>.md` or `<name>.md.jinja`, rendered with `prompt("<dir>/<name>")`. Never inline prompt text in Python. User-facing copy (UI labels, Slack/GitHub notifications) may stay inline.
- Keep comments minimal and only explain non-obvious reasons.
- Slack: prefer @mentions with plain-language requests and buttons for explicit actions. Typed and slash commands are optional shortcuts, never the only way.
- User-initiated UI mutations are optimistic: update immediately, roll back on failure, show an error toast. Skip this only when an immediate update would be unsafe or misleading.
- Create database migrations with `make migration m="Short description"`.
- Structured logging: static message, values in `extra`. Avoid standard `LogRecord` field names in `extra`.
- Prefer exposing UI write operations as authorized agent tools. Destructive or sensitive actions may stay human-only. Prefer existing sandbox CLIs, such as the authenticated `gh`, over new tools.
- A person's concierge DM thread must see everything in their DM with the bot: every post from outside its own run and every button click there must reach its context.
- Use exceptions for Python failures, not `(value, error)` returns. Convert to user-facing errors only at API/tool boundaries.
- Never discard an error: every `except` re-raises or logs what it swallowed.

## Testing

Never run the full test suite locally; run only tests related to the change.

Tests are maintenance cost. Add one only when you can name a concrete regression existing coverage would miss.

- Prefer extending an existing behavioral test over new files, fixtures, or mock-heavy harnesses.
- No tests for docs, prompt wording, constants, trivial accessors, or behavior guaranteed by types or a library.
- No change-detector tests, incidental snapshots, call-order assertions, or mocks that only prove the mock was called.
- For bug fixes, prefer a focused regression test that fails before the fix.

## Pull Requests

Titles must be Conventional Commits (`<type>(<scope>): <description>`, scope optional), linted by `.github/workflows/pr_lint.yml`. Do not include test-running or validation sections in descriptions.

<!-- OPENWIKI:START -->

## OpenWiki

This repository has a generated `openwiki/` evidence index. It is optional just-in-time context, not required startup reading.

- Treat source code and tests as authoritative. A brief's unknowns and review items are verification gaps, not automatic requirements.
- Prefer the narrowest quiet validation that proves the changed behavior. Preserve complete failure output.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->
