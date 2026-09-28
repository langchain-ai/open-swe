# Approval policy

Open SWE may approve a pull request when it fits one of these and has no unresolved findings:

1. **Documentation.** Markdown under `docs/`, `README.md`, or `SECURITY.md`, including
   updating documented commands or settings to match the current code.
2. **Tests only.** Adds or fixes tests without deleting assertions, skipping tests,
   or loosening what a test checks.
3. **Small, low-risk code changes.** Roughly 50 changed lines or fewer, in one feature
   area, with tests for any behavior change. For example: UI copy or styling fixes,
   log or error message fixes, type-only changes, lint fixes, or removing dead code.

Always require human review when a pull request touches:

- authentication, sessions, permissions, credentials, or tokens (for example
  `agent/dashboard/oauth.py`, `agent/dashboard/repo_access.py`, `agent/api_keys/`,
  `agent/github/`);
- sandboxes, webhooks and their signature checks, or database migrations
  (`agent/sandboxes/`, `agent/webhooks/`, `agent/database/migrations/`);
- the reviewer or approval logic itself (`agent/review/`, `agent/tools/publish_review.py`);
- instructions agents follow: `APPROVALS.md`, `AGENTS.md`, `CLAUDE.md`, `.agents/`,
  `.claude/`, or `agent/resources/prompts/`;
- CI, build, or deployment files (`.github/`, `Dockerfile`, `compose.yaml`,
  `langgraph*.json`), or dependency manifests and lockfiles;
- generated pages under `openwiki/`.
