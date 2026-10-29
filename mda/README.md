# Open SWE on Managed Deep Agents

Open SWE's coding agent, pull request reviewer and review scout, packaged as one [Managed Deep Agents](https://docs.langchain.com/langsmith/python/managed-deep-agents-overview) project with a single graph, `open-swe`.
Each run's context names the Open SWE graph it plays (`agent`, `reviewer` or `review-scout`); the factory in `agent.py` builds that graph's deep agent from it.

The model loop, its checkpoints, every tool call and the middleware around them run here.
The backend works out each run's catalog as it starts the run (`openswe/agent_api/graph.py`): its operations, its models and the rules of its middleware.
Every call MDA makes on the backend, the loop's and the tools', is a named endpoint of the typed API in `packages/runtime-api` (`runtime_api`), under `POST /agent-api/{endpoint}`, which both sides import, answered from the run token and the request on whichever backend replica it lands.
Each endpoint declares how long it may take and when it is sent again after a failed delivery.
Every call is authenticated with the run token.

- **Sandbox:** MDA names, creates, adopts, restarts and replaces each thread's LangSmith box, one per thread.
  The catalog gives the options it is created with (the workspace snapshot, VM size, TTLs, `preserve_memory_on_stop`), which apply only when MDA creates it, so a refreshed snapshot never strands a thread's work.
  Before `prepare`, the run opens the box (`open_sandbox`), alongside reading its tool specs and sending what its outbox holds, and reports its name; the backend binds the thread to it (`sandbox_owner: mda`) and connects as to any reused box: proxy credentials, git identity, tool channel.
  MDA leaves the proxy config to the backend (`_openswe_no_manage_proxy=True`).
  `recreate_sandbox` on such a box comes back from the backend as what to replace it with; MDA replaces it under the same name (`replace_sandbox`, the old box stopped and kept renamed), and the backend reconnects through `sandbox_replaced`.
  The backend never creates or replaces an MDA-owned box.
  A tool MDA runs that finds the box unreachable ends the run, and the backend tells the person through `sandbox_unreachable`.
  Threads the backend gave a box keep it; bridged threads run on the user's machine; a task worker works in its coordinator's MDA box once the backend has attached it there.
- **Browser:** the coding agent (`agent`) also gets Stagehand's `browser_run`, `browser_snapshot` and `browser_screenshot` tools on one LangSmith browser per thread (`managed_deepagents.browser`), which shares the thread's sandbox's `localhost`.
  The deployment's workspace needs the LangSmith browser beta.
  It has no `browser_handoff`: MDA's watcher run would start the run that wakes the agent when the user hands the browser back, and Open SWE runs only what its backend signs (`rt/browser.py`).
- **`catalog`:** the run's operations, subagents, models, box and loop rules (`runtime_api.catalog.Catalog`), in every run's context: dispatch does not start a run it cannot plan.
  The context names the tools and carries none of their specs, since the Agent Server copies the context into every checkpoint; the run reads their specs and grants from the backend as it starts (`tool_specs`).
  The graph holds none of it: each model call is offered the run's own operations as tools under their in-process names, and LangGraph's tool node runs each call through the tool the run made for it (`rt/run_tools.py`, `rt/tools.py`), so the prompts, the transcript and the dashboard see what an in-process run shows.
  A tool call is this runtime's: it sends the operation its arguments and the run state its spec says it reads (`reads`, `reads_all`), and turns what it answers into the tool's result, the run state it changed, its stream events and git evidence.
  The backend runs no tools node and no tool-call middleware.
  An operation whose box stopped answering ends the run; the backend has told the person.
  Tools the graph hides from the model are dropped from each request.
- **`search_tools`, `resolve_tool`:** the catalog leaves the run's integrations (MCP tools) out: hundreds of them would put about a megabyte in each run's context, which the Agent Server copies into every checkpoint.
  The backend keeps every tool the run started with in PostgreSQL by digest (`run_tool_sets`), which the run token signs.
  `search_tools` searches them there, and the run calls one by the name a search gave it: `tools.<name>` in code mode for a tool it has none for, or a direct call by a name its tools don't have, asks `resolve_tool` which tool that is, with the run's grant of it (`BackendTools` in `rt/run_tools.py`, through the vendored code mode's `resolve_tool`).
  A name that is none throws `TypeError` in code mode.
- **File and shell tools:** this runtime's own (`ls`, `read_file`, `write_file`, `edit_file`, `delete`, `glob`, `grep`, `execute`), run by `FileToolsMiddleware` (`rt/file_tools.py`).
  Where MDA owns the box they run in it.
  On the paths the backend serves (`files.backend_paths`: offloaded images), and on a thread whose box is not MDA's, such as a desktop thread, deepagents' own tools run against the backend's files through the typed files API (`rt/remote_files.py`).
  Around every call: an `execute` whose command names `push`, `gh` or `pulls` or nests three shells is first put to the backend's push and pull request guards (`check_shell`), a match wider than what they block; they may refuse it, or let an approved push run as they fixed it.
  The backend's `background_execute` and `monitor` operations put their commands to the same guards.
  Tool errors become results.
  Where the catalog lists the checks, a file posing as an image is not attached and reading a file loads the `AGENTS.md` files above it (`rt/validate_image_reads.py`, `rt/subdir_agents.py`).
  Commands get the backend's five-minute default timeout and a client-side deadline past it.
  The transcript hears of every call alongside it (`tool_started`, `tool_result`); each phase and reply waits for the reports made before it.
  Code mode's calls skip the checks.
- **Skills:** the backend lists the skills a run may see (`runtime_api.skills`): a person's own only on a run that acts for them.
  `rt/skills.py` keeps them as read-only files, lists them in the system prompt of the agent's and its subagents' model calls with deepagents' skills middleware, listing them again every run, started before `prepare`, so a public thread never keeps a person's skills from a run that acted for them, and answers `read_file` and the other file tools on their paths.
- **System prompt:** each call's system prompt goes in the order of how many runs share each part, for the provider's prompt cache (`rt/run_prompt.py`): the graph's instructions with what code mode and the browser add, the run's prompt (`ModelCall.system_prompt`), the run's skills, then what only the thread's prompt says (`ModelCall.thread_prompt`).
  For Anthropic the first two each end in a cache breakpoint; with the system prompt's end and the conversation's they fill the four a request may have, and the tools, sent first, need none of their own.
- **Incident evidence:** in an incident run, each tool result is recorded as evidence the report can cite (`record_evidence`), and carries the id it was recorded under.
- **Loop:** `BackendRunMiddleware` (`rt/middleware.py`) prepares the run, reads its inbox and runs its loop:
  - `prepare`: before the run, on the backend (preparation, transcript turn, reply bookkeeping).
    It writes the graph state the loop reads;
  - `git_baseline`, started once `prepare` returns: the backend snapshots the thread's checkouts while the first model call runs, and the run waits for it once the model answers, before any tool runs (`rt/git_baseline.py`);
  - `inbox`, before each model call: only what was sent to the thread while the run worked (queued follow-ups and notes, event matches, task messages), read as the catalog's `InboxSpec` says.
    Once the model call goes ahead, the run hands the items back through `inbox_received`, alongside the call as the transcript's reports go, and the next read names what that receipt holds until the backend took it, so the backend receives it before reading; a run that ends first leaves them owed.
    A run started to carry events receives them at `prepare`;
  - `RunLoop` (`rt/loop.py`), around each model call, as the catalog's `LoopRules` say: it ends the run where a rule says to (a scout with nothing to plan, a client's call still pending), adds the task context (`task_context`) and keeps the GitHub proxy fresh (`refresh_github_proxy`), both asked while the inbox is read, picks the route (`route_model` classifies a request) and the model (`model_choice` for one a person asked for), and assembles the system prompt and tool list.
    After an answer that calls no tool it asks again for the CLI result and the Slack reply the run owes, then posts the answer itself (`reply_on_behalf`).
    At the end it reports the run ended (`run_ended`: transcript, usage, git work) and does the graph's own end-of-run work (`step_limit_reached`, `settle_review_check`, `finish_scout_plan`);
  - `model_result` (`ReportRepliesMiddleware`): the transcript and usage see each response;
  - **Reports:** the reports (`tool_started`, `tool_result`, `model_result`, `inbox_received`) and the notices whose answer the run never reads (`sandbox_unreachable` and the end-of-run work) go through the run's outbox (`rt/outbox.py`): in the order they were made, batched into one `reports` call (`runtime_api.reports`) with whatever else waits, and sent again until the backend says it took them.
    Each carries a key unique in the thread, and the backend records a key once (`agent_report_receipt`), so a resend after a lost answer records nothing twice.
    What the backend has not taken rides in the run's graph state (`open_swe_outbox`), written with the reply, tool result or hook that made it, so a restarted process, or the thread's next run, sends it first.
    A phase waits for the reports before it for up to five minutes; `run_ended` goes out after them;
  - a run whose catalog names no loop rules (a requested conversation offload, an incident run) hands `before_model`, `after_model` and `after_agent` to the graph's own middleware on the backend instead.
- **Subagents:** MDA runs `task` and its subagents.
  `RuntimeToolsMiddleware` reports each call to a tool MDA runs itself through `tool_started` and `tool_result`, so the transcript records the `task` call.
  Every call made from inside a subagent names the `task` call it runs in (`namespace`), and subagents hand their replies to `model_result` too.
  Their tool calls and replies therefore land under the `task` call, as in-process.
  Subagents start from the task alone: deepagents' `fork` mode would hand them this agent's middleware, `BackendRunMiddleware` included.
- **State:** graph state preparation and the loop write that MDA does not know rides in `open_swe_state` and goes back with every call.
  Custom stream events the backend emits are re-emitted here, so the UI gets them too.
- **MDA-native behaviour:** model-call shaping stays MDA's own: retries, the fallback model the backend names, call limits, caching, and automatic summarization.
  Innermost around every model call, subagents' included, `rt/model_calls.py` sends a history the provider accepts on replay (parallel tool results in call order, no empty Anthropic thinking blocks, no unresolvable OpenAI reasoning ids, no legacy Fireworks `function_call`) and fails a call that outlives 15 minutes, its retries included, with `ModelCallTimeoutError`.
  An agent's call then goes to the fallback model and, failing that, ends the run with that error.
  A subagent has no fallback: its timed-out call becomes its answer, which its `task` call hands back to the agent that delegated it.
  A call to a model that reads no images (`reads_images` in the catalog's `RunModels`) goes to the catalog's vision model once the conversation holds an image.

- **One graph per graph and models:** `agent` is a `shared_factory` (`vendor/mda-changes/2026-10-27-shared-factory.md`).
  MDA builds it only from what is the same for every run that plays the graph on the same models (`GraphInputs`: the graph, the run's models, and where its workers report), once per process for each distinct inputs, and shares its compiled graph between every such run, thread and person, and every read of a thread's state.
  Every graph has the same middleware, so a thread reads alike through any of them.
  Nothing built belongs to one run: each run reads its catalog as it starts, and its tools and middleware read the run in progress per call (`rt.backend.current_run`): the operations offered, the model a call is routed to, the skills, the loop rules, and the box, which MDA resolves per call from the run's catalog (`rt/managed_sandbox.py`).

The run's context carries the signed run token and the run's catalog; the token carries the settings the run started with (`RunSettings`: source, draft preference, model, and the digest of its tools).
The backend's thread router also sets the run's LangSmith tracing project to the backend's, so every graph's traces stay in one project whichever runtime runs them.
The backend reads the thread and run configuration from the token, never from the request body.
This deployment holds no GitHub, Slack or database credentials.

This directory is its own project and is never imported by the backend.
Its tests live in `tests/` here and run against this project's locked environment: `make mda-test` and `make mda-typecheck` from the repository root, which CI runs alongside the backend's.

## Routing

Every agent, reviewer and review scout thread runs here.
The backend refuses to start without `MDA_RUNTIME_URL`, and its thread router (`openswe.threads.router`), which serves threads from its PostgreSQL thread index, routes these threads' runs to this deployment:

- **Creating.**
  Every new thread is created here (`runtime: mda` in its metadata).
  Its runs are created on `open-swe`, with their configurable moved into the signed context.
  A protocol v2 `run.start` becomes such a run too.
  Threads created on the backend before it required MDA keep running there, and the router logs a warning each time one is used.
- **Forwarding.**
  Every run, state, history, stream and cancel request for an MDA thread is forwarded here.
  This covers the dashboard, the CLI and every backend client.
- **Indexing.**
  Thread reads and searches answer from the backend's thread index.
  A run started here marks the thread busy there, and the run-complete webhook settles it.

## Configure

The deployment's `.env`, forwarded as deployment secrets by `mda deploy`:

| Variable | Purpose |
|---|---|
| `OPEN_SWE_BACKEND_URL` | Public HTTPS origin of the Open SWE backend |
| `LANGSMITH_API_KEY` | Authenticates the LLM Gateway and tracing |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, … | Model keys for direct (non-gateway) model calls |
| `LANGSMITH_GATEWAY_API_KEY` | Gateway key, the backend's; falls back to `LANGSMITH_API_KEY` |

The backend:

| Variable | Purpose |
|---|---|
| `MDA_RUNTIME_URL` | This deployment's URL; every new agent, reviewer and review scout thread runs here |
| `MDA_RUNTIME_API_KEY` | LangSmith service key the backend calls this deployment with |
| `REMOTE_RUNTIME_TOKEN_SECRET` | Signs run tokens; comma-separated for rotation |

## Run locally

1. In the repository root `.env`, add `REMOTE_RUNTIME_TOKEN_SECRET` (any random string) and `MDA_RUNTIME_URL=http://localhost:2025`, then start the backend with `make dev`.
1. In `mda/.env`, set `OPEN_SWE_BACKEND_URL=http://localhost:2024`, `LANGSMITH_API_KEY`, and the model keys from the table above.
   Then run `make dev-mda` from the repository root.
   Unset `ANTHROPIC_BASE_URL` in that shell if a tool such as Claude Code set it.
1. Use Open SWE as usual: your dashboard, Slack and GitHub threads run here.
   `uv run python -m evals.reviewer.run_eval --limit 3` runs reviews here without publishing to GitHub.

The browser E2E suite runs this project the same way, with a scripted model: `tests/e2e/mda-dev.sh`.

## Build and deploy

Each Open SWE backend calls its own MDA deployment, created with `mda deploy`:

| Deployment | Backend it calls |
|---|---|
| `open-swe-mda-staging` | `open-swe-staging-v2` |
| `open-swe-mda-preview` | `open-swe-preview` |
| `open-swe-mda-prod` | `open-swe-v3` |

Each deployment's configuration is `OPEN_SWE_BACKEND_URL` and `LANGSMITH_GATEWAY_API_KEY`, in the project `.env` that `mda deploy` forwards as secrets, replacing any set before.
The gateway key is its backend's, so model calls count against that key's gateway spend cap; without it they fall back to the deployment's own LangSmith key and the workspace's cap.
`deploy_mda.yml` reads it from the `LANGSMITH_GATEWAY_API_KEY_PROD` or `LANGSMITH_GATEWAY_API_KEY_STAGING` repository secret; `build_preview_branch.yml` reads `LANGSMITH_GATEWAY_API_KEY_PREVIEW`.
Deploys fail if their gateway key is missing.
The backends' `MDA_RUNTIME_URL` and `REMOTE_RUNTIME_TOKEN_SECRET` live in their Terraform.
To deploy one by hand from the repository root, with `mda/.env` holding that deployment's backend URL and gateway key:

```bash
make mda-cli
cd mda
uv run --locked mda deploy --name open-swe-mda-staging --deployment-type prod
```

`deploy_mda.yml` and `build_preview_branch.yml` write the deployed commit (`git rev-parse HEAD`) into `mda/.env` as `LANGSMITH_REVISION_ID`; a manual deploy sets it there too.
Each turn's root trace carries that commit as `revision_id`, an `environment` derived from the CLI-injected `MDA_DEPLOYMENT_NAME` (`open-swe-mda-prod` reports `production`), and the backend's current `LANGCHAIN_REVISION_ID` as `LANGSMITH_AGENT_VERSION` from the run's catalog, overriding whatever metadata queued work saved.

CI and manual deploys run the CLI from this project's locked environment.
`managed-deepagents` is the SDK vendored in [`vendor/managed-deepagents-sdk`](../vendor/README.md): `mda/` installs its runtime as an editable path source, and `make mda-cli` builds its CLI with cargo into that runtime, where the `mda` command runs it.
Run it before `mda build`, `mda dev` or `mda deploy`: until it has, the runtime has no CLI, and it rebuilds whenever the vendored CLI changed; `mda build` and `mda deploy` bundle that runtime, so what deploys is exactly the vendored tree.
Only the SDK's `main` is vendored: `uv run scripts/vendor.py [sha]` vendors main's head or a commit on it, records the exact commit in `UPSTREAM`, three-way merges upstream's changes into the files we edited there, and relocks `mda/`; `--pr <n>` carries an upstream pull request not yet on main, recorded in `vendor/mda-upstream-prs`; `--diff` prints our changes ([`vendor/README.md`](../vendor/README.md)).
`langchain-quickjs`, the code mode, is vendored the same way in [`vendor/langchain-quickjs`](../vendor/README.md): `uv run scripts/vendor.py --project langchain-quickjs` takes the same arguments.
Keep `deepagents` exactly pinned and refresh `mda/uv.lock` when bumping it; `--locked` rejects an out-of-date lockfile.

Nothing here is generated from the backend: operations, subagents and models come from each run's `catalog`, typed by `runtime_api`.

Locally, each fresh `mda dev` start rebuilds `.mda/build` and starts with empty checkpoints.
MDA's dev server also refuses loopback webhooks, so the completion webhook (`COMPLETION_WEBHOOK_URL`) needs the backend at a non-loopback URL.

## Observability

Each deployment's runtime logs (its API and queue replicas) are in LangSmith: the deployment's **Logs** tab, or the LangSmith MCP tools `list_deployments` (find the deployment by name) and `get_deployment_logs`, which filter by `run_id`, a text `query` such as a thread id, time range and level (`sort_order: asc` reads a run in order).
The `langsmith` CLI has no command for them yet.
Filtering by `run_id` gives the run's own lines; a thread's `/state` and `/stream/events` requests carry no run id, so search for the thread id.

The lines worth knowing, each with its attributes (`run_id`, `thread_id`, `request_id`, `langgraph_node`, …):

- `Created run` (API replica): the backend's `POST /threads/{id}/runs` arrived.
- `Dequeued run for background worker`, then `Starting background run` with `run_queue_ms` (queue replica): a worker picked it up.
- `Defined the graph` (logger `rt.factory_timing`): the factory in `agent.py` built a graph's middleware, with its milliseconds (`middleware_ms`, `factory_ms`), `graph_name`, and the counts of `subagents` and `middleware`.
  Logged once per graph a process builds.
- `Slow graph load. Accessing graph 'open-swe' took …ms` (logger `langgraph_api.graph`, over 100ms): the whole graph access, the factory and MDA's compile of its definition.
  Only a process's first access of each graph compiles it.
- `HTTP Request: POST …/agent-api/<endpoint>` (logger `httpx2`): each call to the backend, with the middleware hook that made it in `langgraph_node`.

A prod agent run, before `Defined the graph` existed (times UTC):

```text
00:56:12.687  Created run
00:56:12.706  Dequeued run for background worker
00:56:12.709  Starting background run            run_queue_ms=62
00:56:13.092  POST /agent-api/catalog            factory: catalog fetched
00:56:16.421  Slow graph load … took 3710ms      graph access began at 00:56:12.711
00:56:26.130  POST /agent-api/prepare            BackendRunMiddleware.before_agent; nothing logged in between
00:56:27.114  POST /agent-api/skills
00:56:27.327  POST /agent-api/inbox
00:56:27.644  POST /agent-api/before_model
00:56:28.808  POST /agent-api/browser            mda-browser.before_agent
00:56:29.527  POST gateway …/responses           first model call
```
