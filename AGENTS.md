# AGENTS.md

Robin is an asynchronous coding agent and software factory.
Graphs and the FastAPI app are registered in `langgraph.json`.
JS packages form a pnpm/turbo workspace (`pnpm-workspace.yaml`); use pnpm for them.

Local setup: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Constant gardening

Before making any change, first till the soil: refactor the surrounding code so the change slots in naturally and the result reads as if it had always been designed that way.
Spend real effort here rather than bolting features onto whatever shape the code happens to be in.

Every `agent`, `reviewer` and `review-scout` thread runs on Managed Deep Agents, as one graph, `open-swe`, from the project in `mda/`; the backend refuses to start without `MDA_RUNTIME_URL` and `REMOTE_RUNTIME_TOKEN_SECRET`.
`openswe/threads/router.py` serves the Agent Server's thread API from the PostgreSQL thread index and sends those threads' runs there; every new thread is created there, and threads created before that are read-only (`openswe/graphs/read_only.py`).

MDA owns the run: the model loop, checkpoints, the thread's sandbox, every tool call, and every hook and middleware around them.
The backend is a set of stateless APIs MDA calls: typed endpoints at `/agent-api/{endpoint}` (`packages/runtime-api`, imported by both sides), each answered from the run token the router signs and the request alone, on whichever replica it lands.
Shape new behavior to fit:

- Control flow (when to nudge, route, stop, refresh) is middleware in `mda/rt`; checks around the file and shell tools go in `rt/file_tools.py`.
- Work that needs the backend's credentials or data is a named `Endpoint` for that work (`model_choice`, `refresh_github_proxy`), never a generic phase call or an untyped body.
- Data fixed for a run is worked out once when the backend starts it and sent in the run context (`Catalog`, `openswe/agent_api/client.py`); a call that needs a fact derives it from the token.
- State kept between calls lives in MDA's run state or PostgreSQL, never in backend memory.

Robin starts three kinds of run (`agent`, `reviewer`, `review-scout`), each a `RunKind` (`openswe/agent_api/run_kinds.py`); MDA plays all of them with its one graph.
As a run starts, its kind resolves the run's catalog from what only the backend holds, such as thread settings, workspace defaults, the sender's access and credentialed integrations (`agent_catalog`, `reviewer_catalog`, `review_scout_catalog`, `openswe/agent_api/run_catalog.py`); after that, every call is answered from the token (`openswe/agent_api/run_calls.py`).
The backend never runs middleware around a model call; the only middleware objects it keeps are the preparation and the reporters a named endpoint calls.

## Conventions

- The product is Robin: name it that in new prose, comments, prompts, user- and agent-facing text, and test data.
  "Open SWE" survives only in existing identifiers (`open-swe`, `openswe`, `open_swe_notice.`); don't rename those in passing.

- Do not build special-purpose features when the need can be met by composing generic capabilities such as plugins, automations, skills, or external MCP servers.
  First check whether existing capabilities, with small generic additions if needed, can deliver the feature.

- Before changing the dashboard (`ui/`), read `ui/AGENTS.md` and `ui/DESIGN.md`.
- Flag likely desktop-release requirements to the developer for native shell/preload, bundled bridge/CLI, packaging, or incompatible IPC changes; downloaded UI and backend-only changes need no desktop release.

- Async-only.
  Add a sync method only when an interface requires it, and make it raise `NotImplementedError`.
- Strong types in Python and TypeScript.
  Never use `Any`/`any`, including to silence a type error; use a union, generic, protocol, or `object`/`unknown` with narrowing.
- **Every new function is a method.**
  Put behavior on the object it acts on: a store, a model, a client, or a class that owns the state.
  The only new module-level functions are framework entrypoints: routes, graph nodes, tools and job handlers.
  A private helper (`_person()`, `_workspace_id(cfg)`) is a missing method on the class whose data it reads, and a value functions keep passing around (a request, a client, a `(backend, repo_dir)` pair) is a missing class.
  A new class must own real state, never be a bag of arguments.
  Do not construct an object just to immediately call a method, such as `Reporter(args).report()`.
  Use a classmethod or staticmethod instead when no instance lifecycle or reusable state is needed.
- Identify people by `users.id` everywhere in our code: comparisons, caches, stored state and the values passed between functions.
  Resolve a provider handle (GitHub login, Slack user id, email) to a `User` where it enters from that provider, and turn it back into a handle only to call that provider or to match its own data (a CODEOWNERS entry, a review's author).
- Call GitHub through named `RepoClient`/`PullRequestClient` methods (e.g. `pull.comment(body)`); outside `openswe/github/http.py` and `openswe/github/pull_request_status.py`, never pass a REST path or GraphQL query to the transport (`get`, `post`, `patch`, `delete`, `pages`, `graphql`, `request`).
  Open the client as who it acts for, `GitHubClient.as_user(login)` or `GitHubClient.as_app(owner, repo)`; `GitHubClient.connect(token=...)` is only for a token a run was handed.
- Use `httpx2` for HTTP clients and transports we own; retain `httpx` where an upstream SDK's types or exceptions require it, and keep both exception families in mixed SDK/direct-client paths.
  CI checks added client and transport constructor calls in `openswe/`, `mda/rt/`, and `scripts/` with `scripts/check_httpx.py`; tests and vendored SDKs are outside its scope.
  SDK-required constructors may use a trailing `# httpx-sdk: <SDK and required type>` on the call's opening line; an explanation is required and the exception applies only to calls starting on that line.
- Build a per-call `httpx2.AsyncClient` with `transport=pooled_transport()` (an `aiohttp` session with `connector=pooled_connector(), connector_owner=False`) from `openswe/utils/http_pool.py`, so it reuses the event loop's warm connections instead of opening new ones.
  Preserve dedicated read/write/stream pools and their limits where isolation is intentional, as in `MdaRuntime`; standalone scripts cannot import the backend pool.
- Absolute imports across packages; same-package imports may start with one dot.
  Never use parent-relative imports.
- New dashboard endpoints go in the `router` of the package that owns the feature, never in `openswe/dashboard/routes.py`.
- Model-facing prompts live in `openswe/resources/prompts/` as `<name>.md` or `<name>.md.jinja`, rendered with `prompt("<dir>/<name>")`.
  Never inline prompt text in Python.
  User-facing copy (UI labels, Slack/GitHub notifications) may stay inline.
  Exception: a prompt only an MDA channel handler renders lives in `mda/prompts/`, rendered with `rt.prompts.prompt`; so do the route classifier's prompts, copies of the backend's that `tests/agent/test_mda_routing_prompts.py` keeps in step, and the prompts the tools MDA runs itself answer with, which `tests/agent/test_mda_local_results.py` keeps in step.
- Internal agent notifications use `RunMessage` and the builders in `openswe/input_messages.py` (`system_input` or `build_input_messages`), not bare prose or feature-specific queue payloads.
  Preserve structured messages through delivery instead of wrapping their envelopes again.
  For model-only context, use the structured sender to exclude it from pending previews and the transcript; do not suppress ordinary human follow-ups or user-facing events.
  Every input message records what it is (`human`, `system` or `context`) and whom it is from in its provenance (`runtime_api.provenance`); ask `Provenance.of(message)`, never the envelope text, whether a person wrote a message.
- Keep comments minimal and only explain non-obvious reasons.
- Before asking a clarifying question about Robin's behavior or guidance, read where it lives (prompts in `openswe/resources/prompts/`, tools in `openswe/tools/`, graph wiring) to check the question makes sense.
  Before asking whether a change should be personal or shared, confirm from that code which scopes the behavior actually supports; never offer a scope it cannot have.
- Slack: prefer @mentions with plain-language requests and buttons for explicit actions.
  Typed and slash commands are optional shortcuts, never the only way.
- User-initiated UI mutations are optimistic: update immediately, roll back on failure, show an error toast.
  Skip this only when an immediate update would be unsafe or misleading.
  Slack Block Kit counts: update the clicked message before slow GitHub or Slack calls.
- The dashboard (`ui/`) builds with the React Compiler, which memoizes components, values and callbacks itself.
  Write derived values and handlers as plain expressions; keep `useMemo`/`useCallback` only where a hook's dependency list needs a stable identity.
- Create database migrations with `make migration m="Short description"`.
- Delayed, retried or periodic background work goes through the job queue: `queue.schedule(kind, payload, due_at=..., key=...)` from `openswe/jobs/queue.py`, with its handler registered in `openswe/jobs/kinds.py`.
  A handler raises to retry with backoff.
  Never hand-roll due-time columns, claim tokens, attempt counters or polling sweeps on a feature's own table.
- Don't add new uses of the LangGraph SDK client (`langgraph_client`, `get_client`, `LangGraphClient`) in the backend.
  Everything must be on MDA now; use MDA for runs and PostgreSQL for thread state.
  Read and change threads, their metadata included, through `THREADS` (`openswe/threads/store.py`).
  Existing uses are legacy, not a pattern to copy.
- Don't use the LangGraph Store (`client.store`) for state; keep it in PostgreSQL tables.
- New tables share one shape: an `id uuid` primary key (`uuid7()`, set in Python); `created_at` and `updated_at` (`timestamptz NOT NULL DEFAULT clock_timestamp()`, `updated_at` set again on every write); and `created_by` and `updated_by` (`uuid REFERENCES users (id) ON DELETE SET NULL`: the person whose action caused the write, NULL for system, API-key and workflow writes).
  Natural keys are unique indexes, not primary keys.
  Name a JSON column after what it holds, never `value`, and add no column another column already implies.
  Reference other rows by foreign key whenever a table holds them (our own `id`, or the key of an existing table such as `slack_channel`), creating the referenced row first if it is filled lazily; store a bare id only for things kept outside our database, like Agent Server threads or Slack teams.
- Structured logging: static message, values in `extra`.
  Avoid standard `LogRecord` field names in `extra`.
- Web plugins (`plugins/`, served by `openswe/plugins/`) run in a sandboxed frame whose policy blocks their requests and reach the backend only through the generic `/plugins/{id}/...` API.
  Never add backend code for one plugin; grow that API (storage, GitHub, rooms, people, threads), checking both the installation's grant and the person's own access.
- Prefer exposing UI write operations as authorized agent tools.
  Whenever adding or changing a setting, add or update its authorized agent tool alongside the UI, reusing the same validation and permission checks.
  Keep the tool guidance and `openswe/resources/default_prompt.md` dashboard settings map current with those changes.
  Destructive or sensitive actions may stay human-only.
  Prefer existing sandbox CLIs, such as the authenticated `gh`, over new tools.
- Design new agent tools as fine-grained, Unix-style primitives: each does one thing and returns structured data that code mode can compose.
  Don't add batch tools or combined orchestration tools; use code mode to loop, branch, and run independent calls in parallel instead.
- A person's concierge DM thread must see everything in their DM with the bot: every post from outside its own run and every button click there must reach its context.
- An agent thread's view of its Slack thread never diverges from the Slack thread: its next turn reads every message there it doesn't hold yet.
  That includes commands another thread took (`/btw`, `/breakout`) and their answers, and anything Robin posts that the thread's own agent didn't write; post those as notices (`SlackNotice`; replay keeps what `is_slack_notice` matches).
  Never start a run or create a thread just to deliver them: a thread created later reads the Slack thread from its start.
- Only the agent that owns a Slack channel thread posts in it.
  Background work hands it events instead: `wake_thread_owner` when it must act or report, `note_for_thread_owner` to queue informational ones for its next run.
  A DM sent for a thread passes `origin` to `send_dm`.
- Use exceptions for Python failures, not `(value, error)` returns.
  Convert to user-facing errors only at API/tool boundaries.
- Never discard an error: every `except` re-raises or logs what it swallowed.

## Testing

Never run the full test suite locally; run only tests related to the change.

`mda/` is its own project: its tests live in `mda/tests/` and run in its own environment (`make mda-test`, `make mda-typecheck`).
Never import `openswe` there; test each side of an endpoint against the models it exchanges.
Exception for channel input: a channel handler in `mda/` that writes a run's input keeps its own copy of what it needs from the backend, and only that: the `<input-message>` envelope (`mda/rt/input_messages.py`, standard library only, held byte-identical to `openswe.input_messages` by `tests/agent/test_mda_input_messages.py`) fetching the images a message links to (`mda/rt/images.py`, over `rt.public_http`), reading the repository a message names (`mda/rt/repositories.py`, standard library only, held to `openswe.utils.repo` by `tests/agent/test_mda_repositories.py`), and fencing someone else's words as untrusted (`mda/rt/untrusted.py`, standard library only, held byte-identical to `openswe.github.comments` by `tests/agent/test_mda_untrusted.py`).
The GitHub channel writes an issue mention's run and an automation's prompt (`mda/rt/github_issues.py`, `mda/rt/github_automations.py`) byte for byte as the backend did; `mda/tests/github_prompt_backend.json` holds what the backend wrote.
Exception for the tools MDA runs itself (`ToolSpec.runtime_runs`): what those that read only their arguments answer (`mda/rt/local_results.py`, standard library only) is held byte-identical to the backend's tools by `tests/agent/test_mda_local_results.py`; change both together.
The GitHub channel's switchboard (`mda/rt/github_routes.py`, standard library and `runtime_api` only) keeps the backend's GitHub action sets and its mention pattern, held to `openswe.webhooks.common` by `tests/agent/test_mda_github_routes.py`; the handles themselves come from the backend with each delivery.
The backend still checks anything that grants access, such as a run's repository, before it signs the run.
Change the backend's envelope and the copy together.

Tests are maintenance cost.
Add one only when you can name a concrete regression existing coverage would miss.

- Prefer extending an existing behavioral test over new files, fixtures, or mock-heavy harnesses.
- No tests for docs, prompt wording, constants, mappings, source structure, trivial accessors, or behavior guaranteed by types or a library.
- No change-detector tests, incidental snapshots, call-order assertions, or mocks that only prove the mock was called.
- Behavior-preserving refactors should not need mechanical test updates; rewrite or remove tests that do.
- No speculative edge cases or the same behavior tested across layers.
  Security, authorization, data integrity, and tricky state transitions do deserve targeted coverage.
- For bug fixes, prefer a focused regression test that fails before the fix.
- Before submitting, prune redundant or low-signal tests the change added.
  Shipping no new tests is often correct.

## Pull Requests

Titles must be Conventional Commits (`<type>(<scope>): <description>`, scope optional), linted by `.github/workflows/pr_lint.yml`.
Do not include test-running or validation sections in descriptions.

<!-- OPENWIKI:START -->

## OpenWiki

This repository has a generated `openwiki/` evidence index.
It is optional just-in-time context, not required startup reading.

- Treat source code and tests as authoritative.
  A brief's unknowns and review items are verification gaps, not automatic requirements.
- Prefer the narrowest quiet validation that proves the changed behavior.
  Preserve complete failure output.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki.
Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->
