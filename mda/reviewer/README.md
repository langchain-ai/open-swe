# Reviewer on Managed Deep Agents

Open SWE's pull request reviewer packaged as a [Managed Deep Agents](https://docs.langchain.com/langsmith/python/managed-deep-agents-overview) project. The model loop, its checkpoints and its sandbox run here. The Open SWE backend keeps everything else:

- the reviewer tools, an MCP server at `/remote-runtime/mcp` that the factory attaches with MDA's MCP support; MDA names them `openswe_{tool}`, and the prepared prompt says so
- run hooks at `POST /remote-runtime/hooks/{prepare,drain,settle}`: run preparation (diff, rendered review prompt), the follow-up queue and the check-run settle

`fetch_review_diff` runs here because it writes into this deployment's sandbox.

Dispatch signs a run token into the context of each reviewer run it starts here, and every call back presents it. The context also carries the models dispatch picked, so the main loop, the subagent and summarization all run on them. The backend reads the thread, repository and pull request from the token, never from tool arguments. This deployment holds no GitHub, Slack or database credentials.

MDA creates the sandbox. The factory in `agent.py` boots it from the workspace snapshot that dispatch passes as the run's `snapshot_id` context, and the run's first step clones the pull request's repository into `/workspace` and writes the review diff there. Only public repositories are supported: the clone is unauthenticated.

This directory is its own project and is never imported by the backend.

## Configure

The deployment's `.env`, forwarded as deployment secrets by `mda deploy`:

| Variable | Purpose |
|---|---|
| `OPEN_SWE_BACKEND_URL` | Public HTTPS origin of the Open SWE backend |
| `LANGSMITH_API_KEY` | Creates sandboxes in the workspace that holds the snapshots; also authenticates the LLM Gateway |
| `ANTHROPIC_API_KEY` | Backs deepagents' summarization model, and model calls when the gateway is off |
| `LANGSMITH_GATEWAY_API_KEY` | Optional gateway key, as on the backend |

The backend:

| Variable | Purpose |
|---|---|
| `REVIEWER_RUNTIME_URL` | This deployment's URL; reviews started with `request_pr_review(use_mda=True)` run here |
| `REVIEWER_RUNTIME_API_KEY` | LangSmith service key the backend calls this deployment with |
| `REMOTE_RUNTIME_TOKEN_SECRET` | Signs run tokens; comma-separated for rotation |

## Run locally

Run the backend and this project side by side, then point the reviewer eval at them. The eval follows the same routing as dispatch: with `REVIEWER_RUNTIME_URL` set, it creates the thread on the backend and runs the review on this deployment.

1. In the repository root `.env`, add `REMOTE_RUNTIME_TOKEN_SECRET` (any random string), `REVIEWER_RUNTIME_URL=http://localhost:2025` and `REVIEWER_RUNTIME_API_KEY` (your LangSmith API key), then start the backend with `make dev`.
2. In `mda/reviewer/.env`, set `OPEN_SWE_BACKEND_URL=http://localhost:2024`, `LANGSMITH_API_KEY`, and the model keys from the table above, then run `mda dev --port 2025 --no-browser` from `mda/reviewer`.
3. From the repository root, run `uv run python -m evals.reviewer.run_eval --limit 3`.

The eval scores the findings `publish_review` surfaces (`score_mode = "surfaced_findings"`, the default). Findings added from code mode are not tool-call messages, so the `all_findings` mode undercounts them.

## Build and deploy

```bash
uv tool install --prerelease allow managed-deepagents
cd mda/reviewer
mda build
mda deploy --name open-swe-reviewer
```

`open_swe_reviewer/spec.json` holds the subagent prompts and the description of `fetch_review_diff`, which this project binds at build time. The backend exports it; after changing `fetch_review_diff` or the subagent prompts, run `uv run python -m scripts.export_remote_reviewer_spec` from the repository root. A test fails while it is stale.
