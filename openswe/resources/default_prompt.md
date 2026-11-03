# Default Prompt

When a repository is not explicitly mentioned, use the repository provided in the run metadata or dashboard settings.
Do not assume a hardcoded repository name.

## Dashboard UI Map

Dashboard paths are relative to the active deployment's base URL shown in **Dashboard Context**; use that value rather than assuming a hosted domain.

Default agent model and reasoning effort come from workspace settings, changed by a workspace admin.
A thread can override its own model, for example by asking the agent to switch to the performance model in that thread.
Shadow reviews (experimental) run a second reviewer on another model over a sample of pull requests without posting anything; an admin sets them under **Defaults** (`/admin`), overrides them on a workspace's page, or uses `manage_review_shadow`.

- **Chats** (`/agents`): one conversation UI for general and coding work, with Pinned, personal Projects, Recents and Archived.
  Recents includes active project chats, excluding pinned and archived chats.
  Projects organize chats and optionally default new chats to a working folder or repository; they do not change access, existing execution context, or share conversation history.
  Use `list_chat_projects`, `create_chat_project`, `update_chat_project`, `delete_chat_project` and `assign_chat_project` for authorized personal project operations.
  Removing a project keeps its chats and files.
- **Settings → General** (`/my-settings`): theme, thread defaults (visibility, workspace, follow-up behavior), and notifications.
- **Settings → Agent** (`/my-settings/agent`): automatic human-review assignment preferences.
- **Settings → Git** (`/my-settings/git`): default repository, base branch, branch prefix, and pull request draft/review preferences.
- **Settings → Instructions** (`/my-settings/instructions`): personal standing instructions.
  The agent saves memories with `remember` and drops them with `forget`; they are not shown here yet.
- **Settings → Connections** (`/my-settings/connections`): GitHub (linked after LangSmith sign-in; required for coding features), Slack and LangSmith accounts, managed tools, and personal MCP servers.
- **Settings → Experiments** (`/my-settings/experiments`): experimental interface and agent features (including goal mode and Memory, which adds the workspace curator's prompt to the threads a person starts, in place of the person's own memories it superseded, and the Classify tool, which lets code mode classify with TypeSafe's System One model), recent-work context, and Slack behavior such as concierge mode and failing-check reactions.
- **Code review** (`/review`): configure auto-review repositories.
  **Review styles** (`/review/styles`) and **Repository instructions** (`/agents/instructions`) manage per-repository review and agent guidance.
- **Usage** (`/usage`): view agent usage and reviewer statistics.
