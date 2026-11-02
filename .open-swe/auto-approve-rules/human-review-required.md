Whatever the other rules allow, always require human review when a pull request touches:

- removal or restriction of existing user-facing functionality, including hiding support or diagnostic tools, gating an existing feature to admins, or making a supported user flow less accessible.
  Small diffs, passing tests, and no findings do not make these changes low-risk.
  The PR description must explain why the functionality is being removed or restricted; a rationale does not waive human review;
- authentication, sessions, permissions, credentials, or tokens (for example `openswe/dashboard/oauth.py`, `openswe/dashboard/repo_access.py`, `openswe/api_keys/`, `openswe/github/`);
- sandboxes, webhooks and their signature checks, or database migrations (`openswe/sandboxes/`, `openswe/webhooks/`, `openswe/database/migrations/`);
- the reviewer or approval logic itself (`openswe/review/`, `openswe/tools/publish_review.py`);
- instructions agents follow: anything under `.open-swe/` (including these rules, unless the stricter-approval-policy rule applies), `AGENTS.md`, `CLAUDE.md`, `.agents/`, `.claude/`, or `openswe/resources/prompts/`;
- CI, build, or deployment files (`.github/`, `Dockerfile`, `compose.yaml`, `langgraph*.json`), or dependency manifests and lockfiles;
- generated pages under `openwiki/`.
