# Hermes Installation Runbook for Open SWE Harness (No Prod Install)

Generated: 2026-05-21T21:10:02+02:00
Commit: faa27479c3b67ce65ba772cc6912a6b923540bfc
This is a runbook only. No production install was performed. Do not run bootstrap/install steps until human sets `ALLOW_BOOTSTRAP_INSTALL=YES`.

## Local dev mode without webhooks

1. Work only inside `/home/olle/northstar-agent-harness/open-swe-hermes`.
2. Run `scripts/northstar_local_readiness.sh` before dependency install, Docker image work, webhook setup, or GitHub App setup.
3. Run `scripts/northstar_testrepo_bootstrap_gate.sh` before any testrepo bootstrap step; it is a local-only dry-run gate and must report `NORTHSTAR_TESTREPO_BOOTSTRAP_GATE=PASS`.
4. Use uv-managed Python 3.13.13, not default system Python 3.14.
5. Do not create `.env` from real secrets until bootstrap is approved. Use `.env.example` with placeholders only.
6. Do not start ngrok/public tunnel.
7. Do not create GitHub App yet.
8. If bootstrap is approved, install in a disposable venv first and run tests before any webhook/server exposure.

Local machine note: the `olle` account is in the `docker` group and Hermes has a `GITHUB_TOKEN` env entry. `/home/olle/.local/bin/docker` bridges old long-running sessions until supplementary groups refresh; fresh sessions should use the real Docker group path directly.

Suggested later commands after explicit approval only:

```bash
cd /home/olle/northstar-agent-harness/open-swe-hermes
scripts/northstar_local_readiness.sh
uv python install 3.13.13
uv venv --python 3.13.13
source .venv/bin/activate
python --version
uv sync --all-extras
uv run pytest -vvv tests/
```

## GitHub App minimum permissions for later setup

Initial test repo phase only, not Northstar production:

- Repository access: selected repositories only.
- Contents: read/write only if branch/PR automation is enabled.
- Pull requests: read/write.
- Issues: read/write only for issue-comment trigger.
- Metadata: read-only.
- Subscribe only to issue_comment and PR comment/review events required for initial flow.

Do not grant initially:

- org-wide repository access
- Actions write
- Secrets/variables access
- Administration
- Deployments/environments
- Slack or Linear scopes

## Required/optional env vars

Required for later GitHub-only sandboxed test:

- `LANGSMITH_API_KEY_PROD` or selected sandbox provider credentials
- `LANGSMITH_TENANT_ID_PROD` and tracing project config if using LangSmith
- `GITHUB_APP_ID`
- `GITHUB_APP_PRIVATE_KEY`
- `GITHUB_APP_INSTALLATION_ID`
- `GITHUB_WEBHOOK_SECRET` when webhook is enabled
- `TOKEN_ENCRYPTION_KEY` for OAuth token storage
- `ALLOWED_GITHUB_REPOS=ollehillbom1/north-star-erp` or test repo first
- `DEFAULT_REPO_OWNER=ollehillbom1`
- `DEFAULT_REPO_NAME=north-star-erp`
- `SANDBOX_TYPE=langsmith` or other isolated provider
- `DEFAULT_SANDBOX_SNAPSHOT_ID` if `SANDBOX_TYPE=langsmith`

Optional/later:

- `GITHUB_OAUTH_PROVIDER_ID` for per-user OAuth
- `DASHBOARD_ALLOWED_ORIGINS` for dashboard CORS
- LLM provider keys depending on selected model if not using hosted profile
- Slack/Linear env vars only after separate decision
- `EXA_API_KEY` only if web_search is deliberately enabled

## Testrepo-first rollout

1. Create/use a disposable private test repo.
2. Install GitHub App only on test repo.
3. Enable GitHub issue/PR comments only.
4. Verify allowlist rejects all other repos/users.
5. Run a read-only issue-comment task.
6. Run a branch-only draft PR task.
7. Verify deterministic gates and independent reviewer.
8. Only then propose Northstar repo installation.

## Webhook/LangSmith/token encryption later setup

- Webhook: configure only after local tests and with HTTPS endpoint controlled by approved deployment, not ad-hoc public tunnel for production.
- LangSmith sandbox: build reviewed Docker image/snapshot, checksum external downloads, avoid Python 3.14 dependency conflict.
- Token encryption: generate Fernet key with `openssl rand -base64 32`; support rotation by keeping most-recent key first. Never log key material.

## Rollback/disable

- Disable GitHub App webhook delivery.
- Suspend or uninstall GitHub App from selected repos.
- Revoke App private key and regenerate if exposed.
- Remove/rotate `TOKEN_ENCRYPTION_KEY` and force reauth if token metadata is suspected compromised.
- Stop LangGraph service and sandbox provider credentials.
- Disable dashboard/admin endpoints or restrict to localhost.
- Delete sandbox snapshots/images if compromised.

## Explicitly not run in this phase

- `uv sync --all-extras`
- `uv run langgraph dev`
- `make run` / server start
- Docker build or snapshot creation
- ngrok/public tunnel
- GitHub App creation
- webhook configuration
- GitHub push/PR
