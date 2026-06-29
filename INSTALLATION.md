  3. Set permissions:
   - **Repository permissions**:
     - Contents: Read & write
     - Pull requests: Read & write
     - Issues: Read & write
     - Checks: Read & write — reports an "Open SWE Review" check run on PRs while an auto-review runs, and reads third-party CI conclusions for the auto-fix flow (it watches failing checks on agent-authored PRs and pushes fixes). Without it, check-run creation fails (logged, best-effort) but reviews still work, and CI auto-fix is disabled.
     - Actions: Read-only — required to download CI logs (e.g. `GET /repos/{owner}/{repo}/actions/runs/{run_id}/logs`). Without it, the agent cannot retrieve build or test logs when debugging CI failures.
     - Commit statuses: Read-only — only needed if you enable the `Status` event below; the CI auto-fix flow reads the legacy combined commit-status API for integrations that report via statuses instead of check runs. Without it, status-based CI is silently ignored (logged as "Failed to read combined status").
     - Workflows: Read & write — required to let Open SWE push branches containing GitHub Actions workflow changes after explicit human approval. Runtime sandbox tokens are still minted without this permission by default and are elevated only around an approved workflow push.
     - Metadata: Read-only
   - **Organization permissions** (required only if you plan to set `ALLOWED_GITHUB_ORGS` — see step 5 / Security):
     - Members: Read-only — used to verify org membership for the dashboard-login gate via `GET /orgs/{org}/memberships/{username}`. Without this permission that call returns 403, the check fails closed, and **every** dashboard login is rejected.
4. Under **Subscribe to events**, enable:
   - `Issue comment`
   - `Pull request review`
   - `Pull request review comment`
   - `Check run` — required for CI auto-fix (watching failing GitHub Actions checks on agent PRs)
   - `Check suite` — required for CI auto-fix
   - `Workflow run` — required for CI auto-fix
   - `Status` — optional; covers integrations that report via the legacy commit-status API
5. Click **Create GitHub App**