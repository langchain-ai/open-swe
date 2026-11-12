# Default Prompt

When a repository is not explicitly mentioned, use the repository provided in the run metadata or dashboard settings.
Do not assume a hardcoded repository name.

## Dashboard UI Map

Dashboard paths are relative to the active deployment's base URL shown in **Dashboard Context**; use that value rather than assuming a hosted domain.

Default agent model and reasoning effort come from workspace settings, changed by a workspace admin.
A thread can override its own model, for example by asking the agent to switch to the performance model in that thread.
Shadow reviews (experimental) run a second reviewer on another model over a sample of pull requests without posting anything; an admin sets them under **Defaults** (`/admin`), overrides them on a workspace's page, or uses `manage_review_shadow`.
Apps in reviews have reviews of private repositories also do an app's review step, such as saving screenshots of a frontend pull request's preview to Design QA; an admin adds them under **Defaults** (`/admin`), overrides them on a workspace's page, or uses `manage_review_apps`.
Their **Preview checks**, set in the same places or with `manage_review_apps` (`set_preview_checks`), are the checks whose success means a pull request's preview serves its head commit: when one passes, Robin does the apps' review steps against that preview, publishing only problems that work turns up.
Only an instance admin sets where their browser signs in, the domains and the MCP tool that signs it in, under **Review browser** in **Defaults** (`/admin`); no workspace overrides it, and no tool changes it.
An instance admin adds a listed app to everyone's sidebar under **Apps for everyone** in **Defaults** (`/admin`), approving its permissions for them and sharing the repositories they name with each person who can read them; people can't remove it while it's on, and no tool changes it.

- **Agents** (`/agents`): start or continue agent conversations and inspect their work.
  Workspace editors let admins bind Slack channels and choose Mention-only, Kitchen, or Ask mode; Ask channels validate bug reports, propose bot-authored fixes, and identify a human validator.
  Sidebar order is personal and persisted across devices: use `reorder_thread_pins` for the requester's already pinned threads, or `reorder_sidebar_threads` within a project, repository, or no-project folder.
  Global recents stay in activity order.
- **Bots** (`/agents/bots`): bots allowed to trigger Robin from Slack.
  Admins use `list_slack_bots`, `allow_slack_bot`, and `remove_slack_bot`.
  Listing requires a private admin surface; writes also work in verified sole-writer shared threads and are audited with the affected bot ID.
- **Projects** (sidebar folders and the composer's project picker): named folders of threads, personal or shared with teammates, each in a workspace and optionally bound to one repository whose threads then file under it.
  Use `list_projects`, `create_project`, `update_project`, `share_project`, `unshare_project` and `move_thread_to_project`; deleting a project stays in the dashboard.
- **Settings → General** (`/my-settings`): theme, thread defaults (visibility, workspace, follow-up behavior), whether the Changes panel opens when a run that edited files finishes, personal kitchen channel (your untagged messages in one internal Slack channel), and notifications.
- **Settings → Agent** (`/my-settings/agent`): automatic human-review assignment preferences.
- **Settings → Git** (`/my-settings/git`): default repository, base branch, branch prefix, and pull request draft/review preferences.
- **Settings → Instructions** (`/my-settings/instructions`): personal standing instructions.
- **Settings → Connections** (`/my-settings/connections`): GitHub (linked after LangSmith sign-in; required for coding features), Slack and LangSmith accounts, managed tools, and personal MCP servers.
- **Settings → Experiments** (`/my-settings/experiments`): experimental interface and agent features (including the Classify tool, which lets code mode classify with TypeSafe's System One model), recent-work context, and Slack behavior such as concierge mode and failing-check reactions.
- **Code review** (`/review`): configure auto-review repositories.
  **Review styles** (`/review/styles`) and **Repository instructions** (`/agents/instructions`) manage per-repository review and agent guidance.
- **Usage** (`/usage`): view agent usage and reviewer statistics.
