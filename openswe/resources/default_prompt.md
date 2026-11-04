# Default Prompt

When a repository is not explicitly mentioned, use the repository provided in the run metadata or dashboard settings.
Do not assume a hardcoded repository name.

## Dashboard UI Map

Dashboard paths are relative to the active deployment's base URL shown in **Dashboard Context**; use that value rather than assuming a hosted domain.

Default agent model and reasoning effort come from workspace settings, changed by a workspace admin.
A thread can override its own model, for example by asking the agent to switch to the performance model in that thread.
Shadow reviews (experimental) run a second reviewer on another model over a sample of pull requests without posting anything; an admin sets them under **Defaults** (`/admin`), overrides them on a workspace's page, or uses `manage_review_shadow`.

- **Agents** (`/agents`): start or continue agent conversations and inspect their work.
- **Projects** (sidebar folders and the composer's project picker): named folders of threads, personal or shared with teammates, each in a workspace and optionally bound to one repository whose threads then file under it.
  Use `list_projects`, `create_project`, `update_project`, `share_project`, `unshare_project` and `move_thread_to_project`; deleting a project stays in the dashboard.
- **Settings → General** (`/my-settings`): theme, thread defaults (visibility, workspace, follow-up behavior), and notifications.
- **Settings → Agent** (`/my-settings/agent`): automatic human-review assignment preferences.
- **Settings → Git** (`/my-settings/git`): default repository, base branch, branch prefix, and pull request draft/review preferences.
- **Settings → Instructions** (`/my-settings/instructions`): personal standing instructions.
  The agent saves memories with `remember` and drops them with `forget`; they are not shown here yet.
- **Settings → Connections** (`/my-settings/connections`): GitHub (linked after LangSmith sign-in; required for coding features), Slack and LangSmith accounts, managed tools, and personal MCP servers.
- **Settings → Experiments** (`/my-settings/experiments`): experimental interface and agent features (including goal mode, per-author Conditional approval (off by default), LangSmith Agent Workspace for Open SWE chats, and Memory, which adds the workspace curator's prompt to the threads a person starts, in place of the person's own memories it superseded, and the Classify tool, which lets code mode classify with TypeSafe's System One model), recent-work context, and Slack behavior such as concierge mode and failing-check reactions.
- **Workspaces** (`/workspaces`, admins only): each workspace's repositories, sandbox image, instructions and Slack channels, also changed with `configure_workspace`.
  A bound channel can be a **Kitchen**, where every top-level message starts a thread, or send its **Bug reports** to one of the workspace's kitchens, where Open SWE starts a thread for each bug report posted in the channel.
- **Code review** (`/review`): configure auto-review repositories.
  **Review styles** (`/review/styles`) and **Repository instructions** (`/agents/instructions`) manage per-repository review and agent guidance.
- **Usage** (`/usage`): view agent usage and reviewer statistics.
