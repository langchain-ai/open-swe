# Reviewer on Managed Deep Agents

Open SWE's pull request reviewer packaged as a [Managed Deep Agents](https://docs.langchain.com/langsmith/python/managed-deep-agents-overview) project. Only the model loop and its checkpoints run here. The Open SWE backend keeps everything else and serves it over MCP at `/remote-runtime/mcp`:

- run preparation (sandbox, checkout, diff, rendered review prompt, model choice)
- every reviewer tool
- the GitHub proxy refresh, the follow-up queue and the check-run settle

Dispatch signs a run token into each reviewer run it starts here, and every call back presents it. The backend reads the thread, repository and pull request from the token, never from tool arguments. This deployment holds no GitHub, Slack or database credentials.

The sandbox is the backend's. This project declares no `sandbox/`; its `FilesystemMiddleware` replaces deepagents' default by name and attaches to the sandbox the backend prepared, so file and shell calls go straight to it.

This directory is its own project and is never imported by the backend. Don't add it to the backend's `sys.path`: its `agent.py` shadows the backend's `agent` namespace package.

## Configure

The deployment's `.env`, forwarded as deployment secrets by `mda deploy`:

| Variable | Purpose |
|---|---|
| `OPEN_SWE_BACKEND_URL` | Public HTTPS origin of the Open SWE backend |
| `LANGSMITH_API_KEY` | Attaches to sandboxes in the backend's LangSmith workspace; also authenticates the LLM Gateway |
| `OPEN_SWE_SANDBOX_API_KEY` | Optional sandbox key when `LANGSMITH_API_KEY` is for another workspace |
| `ANTHROPIC_API_KEY` | Backs deepagents' summarization model, and model calls when the gateway is off |
| `LANGSMITH_GATEWAY_API_KEY` | Optional gateway key, as on the backend |

The backend:

| Variable | Purpose |
|---|---|
| `REVIEWER_RUNTIME_URL` | This deployment's URL; set it to route reviewer runs here |
| `REVIEWER_RUNTIME_API_KEY` | LangSmith service key the backend calls this deployment with |
| `REMOTE_RUNTIME_TOKEN_SECRET` | Signs run tokens; comma-separated for rotation |

## Build and deploy

```bash
uv tool install --prerelease allow managed-deepagents
cd mda/reviewer
mda build
mda deploy --name open-swe-reviewer
```

`open_swe_reviewer/spec.json` holds the tool schemas and subagent prompts this project binds at build time. The backend exports it from the tools it serves; after changing a reviewer tool or the subagent prompts, run `uv run python -m scripts.export_remote_reviewer_spec` from the repository root. A test fails while it is stale.
