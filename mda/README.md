# Open SWE on Managed Deep Agents

Open SWE's coding agent, pull request reviewer and review scout, packaged as one [Managed Deep Agents](https://docs.langchain.com/langsmith/python/managed-deep-agents-overview) project with a single graph, `open-swe`. Each run's context names the Open SWE graph it plays (`agent`, `reviewer` or `review-scout`); the factory in `agent.py` builds that graph's deep agent from it.

The model loop and its checkpoints run here. Everything else is the in-process Open SWE graph, which the backend builds for each run (`openswe/remote_runtime/graph.py`), except the thread's sandbox, which MDA owns, and the file and shell tools MDA runs in it (below). All calls go to `POST /remote-runtime/hooks/{hook}`, authenticated with the run token.

- **Sandbox:** MDA names, creates, adopts, restarts and replaces each thread's LangSmith box, one per thread. The catalog gives the options it is created with (the workspace snapshot, VM size, TTLs, `preserve_memory_on_stop`), which apply only when MDA creates it, so a refreshed snapshot never strands a thread's work. Before `prepare`, the run opens the box (`open_sandbox`) and reports its name; the backend binds the thread to it (`sandbox_owner: mda`) and connects as to any reused box: proxy credentials, git identity, tool channel. MDA leaves the proxy config to the backend (`_openswe_no_manage_proxy=True`). `recreate_sandbox` on such a box comes back from the backend as what to replace it with; MDA replaces it under the same name (`replace_sandbox`, the old box stopped and kept renamed), and the backend reconnects through the `sandbox` hook. The backend never creates or replaces an MDA-owned box. A tool MDA runs that finds the box unreachable ends the run, and the backend tells the person through the `sandbox_unreachable` hook. Threads the backend gave a box keep it; bridged threads run on the user's machine; a task worker works in its coordinator's MDA box once the backend has attached it there.
- **Browser:** the coding agent (`agent`) also gets Stagehand's `run`, `snapshot` and `screenshot` tools on one LangSmith browser per thread (`managed_deepagents.browser`, carried in the vendored SDK as managed-deepagents-sdk#897). The deployment's workspace needs the LangSmith browser beta.
- **`catalog`:** the graph's tools, subagents and models. The factory declares each tool under its in-process name, as a call to the backend's `call` hook. The prompts, the transcript and the dashboard therefore see exactly what an in-process run shows. Every call runs through the graph's tools node, with its tool-call middleware (transcript, PR and workflow push guards, `AGENTS.md` reads, tool errors), against the thread's sandbox. Tools the graph hides from the model are dropped from each request.
- **File and shell tools:** where MDA owns the box, the catalog names the file and shell tools MDA runs in it itself (`ls`, `read_file`, `write_file`, `edit_file`, `delete`, `glob`, `grep`, `execute`), with no call to the backend in between. `SandboxToolsMiddleware` turns their tool errors into results where the catalog lists that check, and for the coding agent keeps a file posing as an image from being attached and loads the `AGENTS.md` files above a file it reads (`rt/validate_image_reads.py`, `rt/subdir_agents.py`). Those read checks also cover the box tool calls MDA sends to the backend; the backend keeps its own copies for the file calls it runs itself, such as a desktop thread's. Commands get the backend's five-minute default timeout and a client-side deadline past it. The transcript hears of each call alongside it (`tool_started`, `tool_result`); each phase and reply waits for the reports made before it. Calls only the backend can settle still go to its `call` hook: calls on the paths it serves (skills, offloaded images), and, where the graph guards pushes and pull requests, every `execute` whose command names `push`, `gh` or `pulls` or nests three shells, a match wider than what the guards block. Code mode keeps calling the backend's tools: its calls skip tool-call middleware. A graph with tool-call middleware the backend does not map to one of these checks gets no tools in the box, so every call still reaches that middleware. Elsewhere, the backend's tools of the same name replace MDA's own.
- **Phases:** `BackendRunMiddleware` hands each phase of the loop to the graph's own middleware on the backend:
  - `prepare`: before the run (preparation, transcript turn, skills, reply bookkeeping);
  - `before_model`: follow-up queue, event matches, proxy refresh, task coordination, model routing, a requested conversation offload, plus the system prompt and model for the call;
  - `model_result` (`ReportRepliesMiddleware`): the transcript and usage see each response;
  - `after_model`: reply nudges, which can send the loop back to the model;
  - `after_agent`: usage, transcript, step-limit notice, review-check settle.
- **Subagents:** MDA runs `task` and its subagents. `RuntimeToolsMiddleware` reports each call to a tool MDA runs itself through `tool_started` and `tool_result`, so the transcript records the `task` call. Every call made from inside a subagent names the `task` call it runs in (`namespace`), and subagents hand their replies to `model_result` too. Their tool calls and replies therefore land under the `task` call, as in-process. Subagents start from the task alone: deepagents' `fork` mode would hand them this agent's middleware, `BackendRunMiddleware` included.
- **State:** graph state those hooks write that MDA does not know rides in `open_swe_state` and goes back with every call. Custom stream events they emit are re-emitted here, so the UI gets them too.
- **MDA-native behaviour:** model-call shaping stays MDA's own: retries, the fallback model the backend names, call limits, caching, and automatic summarization. Innermost around every model call, subagents' included, `rt/model_calls.py` sends a history the provider accepts on replay (parallel tool results in call order, no empty Anthropic thinking blocks, no unresolvable OpenAI reasoning ids, no legacy Fireworks `function_call`) and fails a call that outlives 15 minutes, its retries included, with `ModelCallTimeoutError`. An agent's call then goes to the fallback model and, failing that, ends the run with that error. A subagent has no fallback: its timed-out call becomes its answer, which its `task` call hands back to the agent that delegated it. A call to a text-only model goes to the vision model the backend names (`vision` in the `before_model` reply) once the conversation holds an image.

The run's context carries the signed run token and the models dispatch picked. The backend's thread router also sets the run's LangSmith tracing project to the backend's, so every graph's traces stay in one project whichever runtime runs them. The backend reads the thread and run configuration from the token, never from the request body. This deployment holds no GitHub, Slack or database credentials.

This directory is its own project and is never imported by the backend. Its tests live in `tests/` here and run against this project's locked environment: `make mda-test` and `make mda-typecheck` from the repository root, which CI runs alongside the backend's.

## Routing

Every agent, reviewer and review scout thread runs here. The backend refuses to start without `MDA_RUNTIME_URL`, and its thread router (`openswe.threads.router`), which serves threads from its PostgreSQL thread index, routes these threads' runs to this deployment:

- **Creating.** Every new thread is created here (`runtime: mda` in its metadata). Its runs are created on `open-swe`, with their configurable moved into the signed context. A protocol v2 `run.start` becomes such a run too. Threads created on the backend before it required MDA keep running there, and the router logs a warning each time one is used.
- **Forwarding.** Every run, state, history, stream and cancel request for an MDA thread is forwarded here. This covers the dashboard, the CLI and every backend client.
- **Indexing.** Thread reads and searches answer from the backend's thread index. A run started here marks the thread busy there, and the run-complete webhook settles it.

## Configure

The deployment's `.env`, forwarded as deployment secrets by `mda deploy`:

| Variable | Purpose |
|---|---|
| `OPEN_SWE_BACKEND_URL` | Public HTTPS origin of the Open SWE backend |
| `LANGSMITH_API_KEY` | Authenticates the LLM Gateway and tracing |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, … | Model keys for direct (non-gateway) model calls and deepagents' summarization |
| `LANGSMITH_GATEWAY_API_KEY` | Gateway key, the backend's; falls back to `LANGSMITH_API_KEY` |

The backend:

| Variable | Purpose |
|---|---|
| `MDA_RUNTIME_URL` | This deployment's URL; every new agent, reviewer and review scout thread runs here |
| `MDA_RUNTIME_API_KEY` | LangSmith service key the backend calls this deployment with |
| `REMOTE_RUNTIME_TOKEN_SECRET` | Signs run tokens; comma-separated for rotation |

## Run locally

1. In the repository root `.env`, add `REMOTE_RUNTIME_TOKEN_SECRET` (any random string) and `MDA_RUNTIME_URL=http://localhost:2025`, then start the backend with `make dev`.
2. In `mda/.env`, set `OPEN_SWE_BACKEND_URL=http://localhost:2024`, `LANGSMITH_API_KEY`, and the model keys from the table above. Then run `make dev-mda` from the repository root. Unset `ANTHROPIC_BASE_URL` in that shell if a tool such as Claude Code set it.
3. Use Open SWE as usual: your dashboard, Slack and GitHub threads run here. `uv run python -m evals.reviewer.run_eval --limit 3` runs reviews here without publishing to GitHub.

The browser E2E suite runs this project the same way, with a scripted model: `tests/e2e/mda-dev.sh`.

## Build and deploy

Each Open SWE backend calls its own MDA deployment, created with `mda deploy`:

| Deployment | Backend it calls |
|---|---|
| `open-swe-mda-staging` | `open-swe-staging-v2` |
| `open-swe-mda-preview` | `open-swe-preview` |
| `open-swe-mda-prod` | `open-swe-v3` |

Each deployment's configuration is `OPEN_SWE_BACKEND_URL` and `LANGSMITH_GATEWAY_API_KEY`, in the project `.env` that `mda deploy` forwards as secrets, replacing any set before. The gateway key is its backend's, so model calls count against that key's gateway spend cap; without it they fall back to the deployment's own LangSmith key and the workspace's cap. `deploy_mda.yml` reads it from the `LANGSMITH_GATEWAY_API_KEY_PROD` or `LANGSMITH_GATEWAY_API_KEY_STAGING` repository secret; `build_preview_branch.yml` reads `LANGSMITH_GATEWAY_API_KEY_PREVIEW`. Deploys fail if their gateway key is missing. The backends' `MDA_RUNTIME_URL` and `REMOTE_RUNTIME_TOKEN_SECRET` live in their Terraform. To deploy one by hand from the repository root, with `mda/.env` holding that deployment's backend URL and gateway key:

```bash
make mda-cli
cd mda
uv run --locked --python 3.13 mda deploy --name open-swe-mda-staging --deployment-type prod
```

CI and manual deploys run the CLI from this project's locked environment. `managed-deepagents` is the SDK vendored in [`vendor/managed-deepagents-sdk`](../vendor/README.md): `mda/` installs its runtime as an editable path source, and `make mda-cli` builds its CLI with cargo into that runtime, where the `mda` command runs it. Run it before `mda build`, `mda dev` or `mda deploy`: until it has, the runtime has no CLI, and it rebuilds whenever the vendored CLI changed; `mda build` and `mda deploy` bundle that runtime, so what deploys is exactly the vendored tree. Only the SDK's `main` is vendored: `uv run scripts/vendor.py [sha]` vendors main's head or a commit on it, records the exact commit in `UPSTREAM`, three-way merges upstream's changes into the files we edited there, and relocks `mda/`; `--pr <n>` carries an upstream pull request not yet on main, recorded in `vendor/mda-upstream-prs`; `--diff` prints our changes ([`vendor/README.md`](../vendor/README.md)). Keep `deepagents` exactly pinned and refresh `mda/uv.lock` when bumping it; `--locked` rejects an out-of-date lockfile.

Nothing here is generated from the backend: tools, subagents and models come from each run's `catalog`.

Locally, each fresh `mda dev` start rebuilds `.mda/build` and starts with empty checkpoints. MDA's dev server also refuses loopback webhooks, so the completion webhook (`COMPLETION_WEBHOOK_URL`) needs the backend at a non-loopback URL.
