# Open SWE on Managed Deep Agents

Open SWE's coding agent and pull request reviewer, packaged as one [Managed Deep Agents](https://docs.langchain.com/langsmith/python/managed-deep-agents-overview) project with a single graph, `open-swe`. Each run's context names the Open SWE graph it plays (`agent` or `reviewer`); the factory in `agent.py` builds that graph's deep agent from it.

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

The run's context carries the signed run token and the models dispatch picked. The proxy also sets the run's LangSmith tracing project to the backend's, so agent and reviewer traces stay in one project whichever runtime runs them. The backend reads the thread and run configuration from the token, never from the request body. This deployment holds no GitHub, Slack or database credentials.

This directory is its own project and is never imported by the backend.

## Routing

With `MDA_RUNTIME_URL` set, the backend's `openswe.remote_runtime.proxy` middleware routes these threads to this deployment:

- **Choosing.** Each person picks the runtime with **Run on Managed Deep Agents** under *My settings → Experiments* (the `run_on_mda` preference; the switch shows only when `MDA_RUNTIME_URL` is set). It decides for threads they own, or for threads with no owner whose run they start. Threads no person starts (API keys, GitHub Actions workflows, system reviews) follow the workspace's **Managed Deep Agents** setting, which admins switch in workspace settings. Everything else keeps the in-process graphs on this backend.
- **Claiming.** Starting an `agent` or `reviewer` run on a thread with no local history, for a person or workspace that chose MDA, claims the thread for MDA (`runtime: mda` in its metadata). The run is created on `open-swe`, with its configurable moved into the signed context. A protocol v2 `run.start` becomes such a run too. A thread stays on the runtime it started on, so turning the switch off later leaves existing MDA threads on MDA.
- **Forwarding.** Every run, state, history, stream and cancel request for a claimed thread is then forwarded here. This covers the dashboard, the CLI and every backend client.
- **Merging.** `GET /threads/{id}` and `POST /threads/search` still answer from the backend, with run status taken from here.

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
| `MDA_RUNTIME_URL` | This deployment's URL; new agent and reviewer threads of people (or, for threads no person starts, workspaces) that turned on `run_on_mda` run here |
| `MDA_RUNTIME_API_KEY` | LangSmith service key the backend calls this deployment with |
| `REMOTE_RUNTIME_TOKEN_SECRET` | Signs run tokens; comma-separated for rotation |

## Run locally

1. In the repository root `.env`, add `REMOTE_RUNTIME_TOKEN_SECRET` (any random string) and `MDA_RUNTIME_URL=http://localhost:2025`, then start the backend with `make dev`.
2. In `mda/.env`, set `OPEN_SWE_BACKEND_URL=http://localhost:2024`, `LANGSMITH_API_KEY`, and the model keys from the table above. Then run `mda dev --port 2025 --no-browser` from `mda`. Unset `ANTHROPIC_BASE_URL` in that shell if a tool such as Claude Code set it.
3. Turn on **Run on Managed Deep Agents** under *My settings → Experiments*, then use Open SWE as usual: your dashboard, Slack and GitHub threads start here. `uv run python -m evals.reviewer.run_eval --limit 3` runs reviews here without publishing to GitHub.

## Build and deploy

Each Open SWE backend calls its own MDA deployment, created with `mda deploy`:

| Deployment | Backend it calls |
|---|---|
| `open-swe-mda-staging` | `open-swe-staging-v2` |
| `open-swe-mda-preview` | `open-swe-preview` |
| `open-swe-mda-prod` | `open-swe-v3` |

Each deployment's only configuration is `OPEN_SWE_BACKEND_URL`, in the project `.env` that `mda deploy` forwards as secrets; model calls go through the LLM Gateway with the deployment's own LangSmith key. The backends' `MDA_RUNTIME_URL` and `REMOTE_RUNTIME_TOKEN_SECRET` live in their Terraform. To deploy one by hand, with `.env` holding only that deployment's backend URL:

```bash
uv tool install managed-deepagents==0.9.1
cd mda
mda deploy --name open-swe-mda-staging --deployment-type prod
```

Nothing here is generated from the backend: tools, subagents and models come from each run's `catalog`.

Locally, each fresh `mda dev` start rebuilds `.mda/build` and starts with empty checkpoints. MDA's dev server also refuses loopback webhooks, so the completion webhook (`COMPLETION_WEBHOOK_URL`) needs the backend at a non-loopback URL.
