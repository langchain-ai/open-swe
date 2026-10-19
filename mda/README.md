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

The run's context carries the signed run token and the models dispatch picked. The backend's thread router also sets the run's LangSmith tracing project to the backend's, so agent and reviewer traces stay in one project whichever runtime runs them. The backend reads the thread and run configuration from the token, never from the request body. This deployment holds no GitHub, Slack or database credentials.

This directory is its own project and is never imported by the backend.

## Routing

Every agent and reviewer thread runs here. The backend refuses to start without `MDA_RUNTIME_URL`, and its thread router (`openswe.threads.router`), which serves threads from its PostgreSQL thread index, routes these threads' runs to this deployment:

- **Claiming.** Starting an `agent` or `reviewer` run on a thread with no local history claims the thread for MDA (`runtime: mda` in its metadata). The run is created on `open-swe`, with its configurable moved into the signed context. A protocol v2 `run.start` becomes such a run too. Threads whose runs left checkpoints on the backend before it required MDA keep running there.
- **Forwarding.** Every run, state, history, stream and cancel request for a claimed thread is then forwarded here. This covers the dashboard, the CLI and every backend client.
- **Indexing.** Thread reads and searches answer from the backend's thread index. A run started here marks the thread busy there, and the run-complete webhook settles it.

## Configure

The deployment's `.env`, forwarded as deployment secrets by `mda deploy`:

| Variable | Purpose |
|---|---|
| `OPEN_SWE_BACKEND_URL` | Public HTTPS origin of the Open SWE backend |
| `LANGSMITH_API_KEY` | Authenticates the LLM Gateway and tracing |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, … | Model keys for direct (non-gateway) model calls and deepagents' summarization |
| `LANGSMITH_GATEWAY_API_KEY` | Optional gateway key, as on the backend |
| `MDA_SERVICE_SECRET` | Shared with the backend: the Linear channel presents it when it hands events to the backend. Open SWE's own credential between the two, not an MDA setting |

The backend:

| Variable | Purpose |
|---|---|
| `MDA_RUNTIME_URL` | This deployment's URL; every new agent and reviewer thread runs here |
| `MDA_RUNTIME_API_KEY` | LangSmith service key the backend calls this deployment with |
| `REMOTE_RUNTIME_TOKEN_SECRET` | Signs run tokens; comma-separated for rotation |
| `MDA_SERVICE_SECRET` | The same value as this deployment's; `/linear/channel/*` answers only callers that present it |

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

Each deployment's configuration is `OPEN_SWE_BACKEND_URL` and `MDA_SERVICE_SECRET` (repository secret `MDA_SERVICE_SECRET_<ENVIRONMENT>`), in the project `.env` that `mda deploy` forwards as secrets; model calls go through the LLM Gateway with the deployment's own LangSmith key. The backends' `MDA_RUNTIME_URL` and `REMOTE_RUNTIME_TOKEN_SECRET` live in their Terraform. To deploy one by hand, with `.env` holding only that deployment's backend URL:

```bash
uv tool install managed-deepagents==0.10.0.dev9
cd mda
mda deploy --name open-swe-mda-staging --deployment-type prod
```

Nothing here is generated from the backend: tools, subagents and models come from each run's `catalog`.

Locally, each fresh `mda dev` start rebuilds `.mda/build` and starts with empty checkpoints. MDA's dev server also refuses loopback webhooks, so the completion webhook (`COMPLETION_WEBHOOK_URL`) needs the backend at a non-loopback URL.

## Linear

The deployment's Linear channel (`channels/linear.py`) is how Open SWE works in Linear. Mentioning the app on an issue, or delegating the issue to it, opens an agent session. Each session is its own conversation: the channel starts its runs on the session's own thread, so the answer, Stop, interrupt notices and errors reach the session. A reply in the session is the next run there; a new mention on the same issue opens a new session and thread.

The channel verifies each delivery (Linear's signature and timestamp). The backend decides everything else, as for any other run: the repository, the workspace, the input, and the signed run token. Every handled event goes to the backend's endpoint, `POST /linear/channel/events` (the channel's `on_event` names it): MDA posts `{event: {key, deliveryId, payload}, thread}`, with the payload it validated. For a session event the backend indexes the thread and answers with the run's input, context, metadata and webhook; issue and comment events start no conversation, so it answers `null`, after logging them for `listen_events` and running Linear automations. The endpoint answers only callers that present `MDA_SERVICE_SECRET`.

Threads of Linear sessions belong to the channel, and MDA scopes threads by owner. The backend reads them as LangSmith Studio does (its LangSmith key with `x-auth-scheme: langsmith`), which MDA allows for reads only, so the dashboard shows them read-only: the backend starts no runs on them and writes nothing there. Each run carries the backend's run-complete webhook, so `/webhooks/run-complete` completes it as it does the backend's own runs. The channel reports failures in the session itself.

`mda deploy` sets up the Linear app, which takes a Linear workspace admin; see [Linear](../docs/INSTALLATION.md#linear).
