# AGENTS.md

Open SWE is an asynchronous coding agent and software factory. Graphs and the FastAPI app are registered in `langgraph.json`. JS packages form a pnpm/turbo workspace (`pnpm-workspace.yaml`); use pnpm for them.

Local setup: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Constant gardening

Before making any change, first till the soil: refactor the surrounding code so the change slots in naturally and the result reads as if it had always been designed that way. Spend real effort here rather than bolting features onto whatever shape the code happens to be in.

Every `agent`, `reviewer` and `review-scout` thread runs on Managed Deep Agents, as one graph, `open-swe`, from the project in `mda/`; the backend refuses to start without `MDA_RUNTIME_URL` and `REMOTE_RUNTIME_TOKEN_SECRET`. `openswe/threads/router.py` serves the Agent Server's thread API from the PostgreSQL thread index and sends those threads' runs there; every new thread is created there, and threads created before that are read-only (`openswe/graphs/read_only.py`). MDA owns the model loop, checkpoints, the thread's sandbox and every tool call; the backend never runs a tool call. `openswe/agent_api/graph.py` takes each MDA run's plan from its graph's planner (`plan_agent`, `plan_reviewer`, `plan_review_scout` fill a `ToolSurface`; no graph is compiled) and serves it behind a run token the router signs, as one typed API at `/agent-api/{endpoint}` (`packages/runtime-api`, imported by both sides): the catalog, each phase of the loop, and the work of MDA's tools (an operation per planned tool, the thread's files, the shell-command guards and incident evidence). A new call is a new `Endpoint` there, never an untyped body. Behavior added as phase middleware or tools reaches MDA runs without changes there; middleware adds to each model call only through `ashape_model_call` (`openswe/middleware/model_call_shape.py`: a system prompt section, or tools kept from the call), since MDA makes the call (the backend runs `awrap_model_call` only for the transcript and usage to observe each response); a check around tool calls belongs in `mda/rt` (`rt/file_tools.py` for the file and shell tools), or behind an API endpoint where it needs the backend.

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
- Don't add new uses of `langgraph_client` in the backend. Everything must be on MDA now; use MDA for runs and PostgreSQL for thread state. Existing uses are legacy, not a pattern to copy.
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

`mda/` is its own project: its tests live in `mda/tests/` and run in its own environment (`make mda-test`, `make mda-typecheck`). Never import `openswe` there; test each side of an endpoint against the models it exchanges.

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
