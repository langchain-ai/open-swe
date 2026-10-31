# Approval policy

Open SWE may approve a pull request when it fits one of these and has no unresolved findings:

1. **Documentation.**
   Markdown under `docs/`, `README.md`, or `SECURITY.md`, including updating documented commands or settings to match the current code.
2. **Tests only.**
   Adds or fixes tests without deleting assertions, skipping tests, or loosening what a test checks.
3. **Small, low-risk code changes.**
   Roughly 50 changed lines or fewer, in one feature area, with tests for any behavior change.
   For example: UI copy or styling fixes, log or error message fixes, type-only changes, lint fixes, or removing dead code that no supported user flow can reach.

4. **Stricter approval policy only.**
   Changes confined to this file that solely narrow the set of PRs eligible for automatic approval (for example, adding an exclusion or tightening a limit).
   Any project member may propose restrictions on what they would approve.
   Compare the whole diff against the base policy: every PR eligible under the proposed policy must already be eligible under the base policy, and at least some previously eligible PRs must become ineligible.
   If that cannot be established, require human review.
   Mixed changes, relaxed rules, new exceptions, and changes to authorization or human approval requirements do not qualify.
   This exception waives only the instruction-file exclusion for this file; all other human-review requirements still apply.
   Evaluate this PR using the base policy, not the policy it proposes.

5. **Isolated, default-off experiments.**
   Changes behind a feature flag (experiment) that is off by default, with evidence that every changed behavior affects only users who explicitly enable it.
   Verify the flag's default and every affected execution path, including shared code, background jobs, and work performed before checking the flag; the flag's presence alone is not proof of isolation.
   The change must not enable the flag by default or alter behavior for users who have not opted in, and must not introduce undue production storage, compute, or memory risk, even when enabled.
   If isolation, the default, or production resource risk is ambiguous or cannot be established, require human review.
   This criterion does not waive any human-review requirement below.

6. **Automated LangChain/LangGraph dependency refreshes only.**
   PRs produced by the scheduled LangChain dependency-refresh automation, confined to `pyproject.toml`, `uv.lock`, `mda/pyproject.toml`, and `mda/uv.lock`.
   Manifest edits may only raise existing `langchain`, `langchain-*`, `langgraph`, or `langgraph-*` dependency versions within the existing major version; lockfile changes must follow solely from those targeted upgrades.
   Preserve extras, Python requirements, compatibility caps, dependency groups, and source overrides.
   Do not add or remove dependencies, change vendored code, or introduce a new prerelease track; updates within an already opted-in prerelease track are allowed.
   Require all required checks and test jobs to complete successfully on the current PR head and no unresolved review findings.
   Missing, pending, cancelled, or failed checks do not qualify.
   This exception waives only the dependency-manifest and lockfile exclusion below; all other human-review requirements still apply.

Always require human review when a pull request touches:

- removal or restriction of existing user-facing functionality, including hiding support or diagnostic tools, gating an existing feature to admins, or making a supported user flow less accessible.
  Small diffs, passing tests, and no findings do not make these changes low-risk.
  The PR description must explain why the functionality is being removed or restricted; a rationale does not waive human review;
- authentication, sessions, permissions, credentials, or tokens (for example `openswe/dashboard/oauth.py`, `openswe/dashboard/repo_access.py`, `openswe/api_keys/`, `openswe/github/`);
- sandboxes, webhooks and their signature checks, or database migrations (`openswe/sandboxes/`, `openswe/webhooks/`, `openswe/database/migrations/`);
- the reviewer or approval logic itself (`openswe/review/`, `openswe/tools/publish_review.py`);
- instructions agents follow: anything under `.open-swe/` (including this file, unless criterion 4 applies), `AGENTS.md`, `CLAUDE.md`, `.agents/`, `.claude/`, or `openswe/resources/prompts/`;
- CI, build, or deployment files (`.github/`, `Dockerfile`, `compose.yaml`, `langgraph*.json`), or dependency manifests and lockfiles (unless criterion 6 applies);
- generated pages under `openwiki/`.
