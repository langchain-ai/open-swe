# AGENTS.md

Open SWE is an asynchronous coding agent and software factory. Graphs and the FastAPI app are registered in `langgraph.json`. JS packages form a pnpm/turbo workspace (`pnpm-workspace.yaml`); use pnpm for them.

Local setup: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Constant gardening

Before making any change, first till the soil: refactor the surrounding code so the change slots in naturally and the result reads as if it had always been designed that way. Spend real effort here rather than bolting features onto whatever shape the code happens to be in.

With `MDA_RUNTIME_URL` set, threads of people who turn on **Run on Managed Deep Agents** (the `run_on_mda` user preference; threads no person starts follow the workspace setting of that name) run the `agent` and `reviewer` graphs on Managed Deep Agents as one graph, `open-swe`, from the project in `mda/`; every other thread runs in-process as before. `openswe/remote_runtime/proxy.py` routes the Agent Server API of those threads there. MDA owns the model loop and checkpoints; the backend keeps the thread's sandbox. `openswe/remote_runtime/graph.py` builds the in-process graph for each MDA run and serves it at `/remote-runtime/hooks/{hook}`, behind a run token the proxy signs: its tools under their own names, and its middleware phase by phase. Behavior added as graph middleware or tools therefore reaches MDA runs without changes there.

## Conventions

- Async-only. Add a sync method only when an interface requires it, and make it raise `NotImplementedError`.
- Strong types in Python and TypeScript. Never use `Any`/`any`, including to silence a type error; use a union, generic, protocol, or `object`/`unknown` with narrowing.
- Put behavior on the object it acts on. If functions keep passing the same value around (a request, a client, a `(backend, repo_dir)` pair), that value is a missing class. Free functions are for framework entrypoints (routes, graph nodes, tools) and helpers spanning unrelated types. A new class must own real state, never be a bag of arguments.
- Call GitHub through named `RepoClient`/`PullRequestClient` methods (e.g. `pull.comment(body)`); outside `openswe/github/http.py` and `openswe/github/pull_request_status.py`, never pass a REST path or GraphQL query to the transport (`get`, `post`, `patch`, `delete`, `pages`, `graphql`, `request`). Open the client as who it acts for, `GitHubClient.as_user(login)` or `GitHubClient.as_app(owner, repo)`; `GitHubClient.connect(token=...)` is only for a token a run was handed.
- Absolute imports across packages; same-package imports may start with one dot. Never use parent-relative imports.
- New dashboard endpoints go in the `router` of the package that owns the feature, never in `openswe/dashboard/routes.py`.
- Model-facing prompts live in `openswe/resources/prompts/` as `<name>.md` or `<name>.md.jinja`, rendered with `prompt("<dir>/<name>")`. Never inline prompt text in Python. User-facing copy (UI labels, Slack/GitHub notifications) may stay inline.
- Keep comments minimal and only explain non-obvious reasons.
- Slack: prefer @mentions with plain-language requests and buttons for explicit actions. Typed and slash commands are optional shortcuts, never the only way.
- User-initiated UI mutations are optimistic: update immediately, roll back on failure, show an error toast. Skip this only when an immediate update would be unsafe or misleading. Slack Block Kit counts: update the clicked message before slow GitHub or Slack calls.
- Create database migrations with `make migration m="Short description"`.
- Don't use the LangGraph Store (`client.store`) for state; keep it in PostgreSQL tables.
- New tables share one shape: an `id uuid` primary key (`uuid7()`, set in Python); `created_at` and `updated_at` (`timestamptz NOT NULL DEFAULT clock_timestamp()`, `updated_at` set again on every write); and `created_by` and `updated_by` (`uuid REFERENCES users (id) ON DELETE SET NULL`: the person whose action caused the write, NULL for system, API-key and workflow writes). Natural keys are unique indexes, not primary keys. Name a JSON column after what it holds, never `value`, and add no column another column already implies. Reference other rows by foreign key whenever a table holds them (our own `id`, or the key of an existing table such as `slack_channel`), creating the referenced row first if it is filled lazily; store a bare id only for things kept outside our database, like Agent Server threads or Slack teams.
- Structured logging: static message, values in `extra`. Avoid standard `LogRecord` field names in `extra`.
- Prefer exposing UI write operations as authorized agent tools. Destructive or sensitive actions may stay human-only. Prefer existing sandbox CLIs, such as the authenticated `gh`, over new tools.
- A person's concierge DM thread must see everything in their DM with the bot: every post from outside its own run and every button click there must reach its context.
- Only the agent that owns a Slack channel thread posts in it. Background work hands it events instead: `wake_thread_owner` when it must act or report, `note_for_thread_owner` to queue informational ones for its next run. A DM sent for a thread passes `origin` to `send_dm`.
- Use exceptions for Python failures, not `(value, error)` returns. Convert to user-facing errors only at API/tool boundaries.
- Never discard an error: every `except` re-raises or logs what it swallowed.

## Testing

Never run the full test suite locally; run only tests related to the change.

Tests are maintenance cost. Add one only when you can name a concrete regression existing coverage would miss.

- Prefer extending an existing behavioral test over new files, fixtures, or mock-heavy harnesses.
- No tests for docs, prompt wording, constants, mappings, source structure, trivial accessors, or behavior guaranteed by types or a library.
- No change-detector tests, incidental snapshots, call-order assertions, or mocks that only prove the mock was called.
- Behavior-preserving refactors should not need mechanical test updates; rewrite or remove tests that do.
- No speculative edge cases or the same behavior tested across layers. Security, authorization, data integrity, and tricky state transitions do deserve targeted coverage.
- For bug fixes, prefer a focused regression test that fails before the fix.
- Before submitting, prune redundant or low-signal tests the change added. Shipping no new tests is often correct.

## Pull Requests

Titles must be Conventional Commits (`<type>(<scope>): <description>`, scope optional), linted by `.github/workflows/pr_lint.yml`. Do not include test-running or validation sections in descriptions.

<!-- OPENWIKI:START -->

## OpenWiki

This repository has a generated `openwiki/` evidence index. It is optional just-in-time context, not required startup reading.

- Treat source code and tests as authoritative. A brief's unknowns and review items are verification gaps, not automatic requirements.
- Prefer the narrowest quiet validation that proves the changed behavior. Preserve complete failure output.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->
