# Default Prompt

When a repository is not explicitly mentioned, use the repository provided in the run metadata or dashboard settings. Do not assume a hardcoded repository name.

## GitHub App permission scopes

The open-swe GitHub App installation token does **not** hold the `workflows` write scope. Any task that requires committing changes under `.github/workflows/` cannot be pushed by the agent — `git push` is rejected by GitHub with `refusing to allow a GitHub App to create or update workflow ... without workflows permission`. When the user's request implies edits under `.github/workflows/`, declare this constraint up front in your first Slack/Linear reply *before* spending investigation or edit budget, and offer to either (a) paste the proposed diff inline for a human to apply, or (b) wait while the user grants the App `workflows: write` and re-runs. Only continue with the deeper investigation + edits after the user picks a path. If `git push` ever surfaces that exact `refusing to allow a GitHub App to create or update workflow` string, treat it as the same constraint and stop pushing instead of retrying.
