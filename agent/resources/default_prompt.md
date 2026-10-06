# Default Prompt

When a repository is not explicitly mentioned, use the repository provided in the run metadata or dashboard settings. Do not assume a hardcoded repository name.

## Dashboard UI Map

Dashboard paths are relative to the active deployment's base URL shown in **Dashboard Context**; use that value rather than assuming a hosted domain.

- **Agents** (`/agents`): start or continue agent conversations and inspect their work.
- **Settings → General** (`/my-settings`): theme, thread defaults (visibility, workspace, follow-up behavior), and notifications.
- **Settings → Agent** (`/my-settings/agent`): personal model, reasoning effort, adaptive routing, and sandbox defaults.
- **Settings → Git** (`/my-settings/git`): default repository, base branch, branch prefix, and pull request draft/review preferences.
- **Settings → Instructions** (`/my-settings/instructions`): personal standing instructions.
- **Settings → Connections** (`/my-settings/connections`): Slack, Notion, and LangSmith accounts, Slack behavior such as concierge mode, and personal MCP servers. **Connect Notion** starts the Notion OAuth flow.
- **Code review** (`/review`): configure auto-review repositories. **Review styles** (`/review/styles`) and **Repository instructions** (`/agents/instructions`) manage per-repository review and agent guidance.
- **Usage** (`/usage`): view agent usage and reviewer statistics.
