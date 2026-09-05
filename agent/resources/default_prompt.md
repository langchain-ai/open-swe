# Default Prompt

When a repository is not explicitly mentioned, use the repository provided in the run metadata or dashboard settings. Do not assume a hardcoded repository name.

## Dashboard UI Map

Dashboard paths are relative to the active deployment's base URL shown in **Dashboard Context**; use that value rather than assuming a hosted domain.

- **Agents** (`/agents`): start or continue agent conversations and inspect their work.
- **Profile Settings** (`/my-settings`): manage Slack identity mapping, pull request and review preferences, personal instructions, notifications, and user-scoped Currents.dev and LangSmith connections.
- **Plugins** (`/plugins`): manage user-owned MCP connections, including Notion and Datadog, and skills.
- **Open SWE Agent** (`/cloud-agents`): configure model, reasoning, repository, branch, and pull request defaults. **Repository Instructions** (`/agents/instructions`) manages per-repository agent guidance.
- **Open SWE Review** (`/review`): configure auto-review repositories, review styles, organization guidelines, and review behavior.
- **Usage** (`/usage`): view agent usage and reviewer statistics.
