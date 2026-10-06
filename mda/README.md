# Open SWE on Managed Deep Agents

Open SWE's core agent as a [Managed Deep Agent](https://github.com/langchain-ai/managed-deepagents-sdk), deployed separately from the main LangGraph deployment. MDA owns the sandbox, store, checkpointer, and system prompt (`instructions.md`).

## Deploy

```bash
cd mda
mda deploy
mda connections create github --secret-from-env GITHUB_TOKEN
```

`.env` needs `LANGSMITH_API_KEY` and `OPENAI_API_KEY`. The `github` connection authenticates `gh` and `git` in the sandbox through its proxy.

## Route Open SWE runs here

Set on the main deployment:

- `MDA_AGENT_URL`: this deployment's Agent Server URL.
- `MDA_AGENT_API_KEY`: a LangSmith API key for this deployment's workspace (defaults to `LANGSMITH_API_KEY`).

Then enable the `mda_agent_enabled` feature flag on a workspace. That workspace's agent runs are forwarded to this deployment on the same thread ID, and their messages stream back into the Open SWE thread.
