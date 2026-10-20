# Open SWE on Managed Deep Agents

Open SWE's coding agent, pull request reviewer and review scout, packaged as one [Managed Deep Agents](https://docs.langchain.com/langsmith/python/managed-deep-agents-overview) project with a single graph, `open-swe`. Each run's context names the Open SWE graph it plays (`agent`, `reviewer` or `review-scout`); the factory in `agent.py` builds that graph's deep agent from it.

The model loop and its checkpoints run here. Everything else is the in-process Open SWE graph, which the backend builds for each run (`openswe/remote_runtime/graph.py`), including the thread's sandbox: the backend creates, reuses and credentials it exactly as for an in-process run, so local, bridged and task-worker sandboxes work too, and this deployment declares none. All calls go to `POST /remote-runtime/hooks/{hook}`, authenticated with the run token.

- **`catalog`:** the graph's tools, subagents and models. The factory declares each tool under its in-process name, as a call to the backend's `call` hook. The prompts, the transcript and the dashboard therefore see exactly what an in-process run shows. These tools replace MDA's own file and shell tools of the same name. Every call runs through the graph's tools node, with its tool-call middleware (transcript, PR and workflow push guards, `AGENTS.md` reads, tool errors), against the thread's sandbox. Tools the graph hides from the model are dropped from each request.
- **Phases:** `BackendRunMiddleware` hands each phase of the loop to the graph's own middleware on the backend:
  - `prepare`: before the run (preparation, transcript turn, skills, reply bookkeeping);
  - `before_model`: follow-up queue, event matches, proxy refresh, task coordination, model routing, a requested conversation offload, plus the system prompt and model for the call;
  - `model_result` (`ReportRepliesMiddleware`): the transcript and usage see each response;
  - `after_model`: reply nudges, which can send the loop back to the model;
  - `after_agent`: usage, transcript, step-limit notice, review-check settle.
- **Subagents:** MDA runs `task` and its subagents. `RuntimeToolsMiddleware` reports each call to a tool MDA runs itself through `tool_started` and `tool_result`, so the transcript records the `task` call. Every call made from inside a subagent names the `task` call it runs in (`namespace`), and subagents hand their replies to `model_result` too. Their tool calls and replies therefore land under the `task` call, as in-process. Subagents start from the task alone: deepagents' `fork` mode would hand them this agent's middleware, `BackendRunMiddleware` included.
- **State:** graph state those hooks write that MDA does not know rides in `open_swe_state` and goes back with every call. Custom stream events they emit are re-emitted here, so the UI gets them too.
- **MDA-native behaviour:** model-call shaping stays MDA's own: retries, the fallback model the backend names, call limits, caching, and automatic summarization.

The run's context carries the signed run token and the models dispatch picked. The backend's thread router also sets the run's LangSmith tracing project to the backend's, so every graph's traces stay in one project whichever runtime runs them. The backend reads the thread and run configuration from the token, never from the request body. This deployment holds no GitHub, Slack or database credentials.

This directory is its own project and is never imported by the backend.

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
| `LANGSMITH_GATEWAY_API_KEY` | Optional gateway key, as on the backend |

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

Each deployment's only configuration is `OPEN_SWE_BACKEND_URL`, in the project `.env` that `mda deploy` forwards as secrets; model calls go through the LLM Gateway with the deployment's own LangSmith key. The backends' `MDA_RUNTIME_URL` and `REMOTE_RUNTIME_TOKEN_SECRET` live in their Terraform. To deploy one by hand, with `.env` holding only that deployment's backend URL:

```bash
cd mda
uv run --locked --python 3.13 mda deploy --name open-swe-mda-staging --deployment-type prod
```

CI and manual deploys run the CLI from this project's locked environment, so its version follows the runtime dependency without a separate install pin. Keep `managed-deepagents` and `deepagents` exactly pinned and refresh `mda/uv.lock` when bumping them; `--locked` rejects an out-of-date lockfile.

The preview can also deploy an unreleased SDK commit, for a preview-labelled PR only. The PR takes `managed-deepagents` from that commit in `[tool.uv.sources]` (`git` on `langchain-ai/managed-deepagents-sdk`, a full `rev`, `subdirectory = "packages/pypi"`), pinned to that commit's package version, and relocks. A repository administrator approves the commit by setting the `PREVIEW_MDA_SOURCE_SHA` variable to it. The preview workflow then builds that commit's compiler and deploys with it and its runtime (`scripts/preview_mda_source.py`). A tree naming any other source fails its MDA deploy. Approving after a refused deploy takes effect on the next preview build that changes the tree, or a `workflow_dispatch` with `force`. Staging and production never build from source.

Nothing here is generated from the backend: tools, subagents and models come from each run's `catalog`.

Locally, each fresh `mda dev` start rebuilds `.mda/build` and starts with empty checkpoints. MDA's dev server also refuses loopback webhooks, so the completion webhook (`COMPLETION_WEBHOOK_URL`) needs the backend at a non-loopback URL.
