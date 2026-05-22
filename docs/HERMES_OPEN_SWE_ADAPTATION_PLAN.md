# Hermes Open SWE Adaptation Plan

Generated: 2026-05-21T21:10:02+02:00
Commit: faa27479c3b67ce65ba772cc6912a6b923540bfc

## Target identity

Rename operationally from Open SWE to Hermes Northstar Agent Harness. Upstream attribution remains in source/docs. User-facing trigger tag can later become `@hermes-northstar` or similar, but no production trigger is enabled in this phase.

## Repository policy

- Default repo: `ollehillbom1/north-star-erp`.
- Initial repo allowlist: only `ollehillbom1/north-star-erp`.
- Do not use org-wide allowlist initially.
- Trigger users: explicit GitHub user allowlist; start with `ollehillbom1` only.
- Initial triggers: GitHub issue and PR comments only.
- Slack and Linear: disabled until separate decision.

## Sandbox policy

- Autonomous runs require isolated cloud/container sandbox. Acceptable candidates: LangSmith sandbox with reviewed snapshot, Daytona, Runloop, Modal, or a future Hermes-managed container sandbox.
- `SANDBOX_TYPE=local` is RED for autonomy and allowed only for manual dev with human watching.
- Sandbox snapshot must include controlled versions of git, gh, node, npm, Python 3.12/3.13, uv, ripgrep, and any Northstar verification wrappers.

## Northstar agent discipline integration

Every run prompt/config must include:

- `TASK_ID`
- `OWNER_SESSION`
- `AGENT_SCOPE`
- `HERMES_TASK_ID`
- active lease path/id
- target repo and branch
- allowed paths
- verification level
- reviewer requirement

Run lifecycle:

1. Create isolated sandbox/worktree from `github/main`, never dirty `/erp`.
2. Enforce repo and trigger-user allowlist before run creation.
3. Require branch naming under `task/<TASK_ID>` or `harness/<TASK_ID>`.
4. Coder agent may implement in sandbox only.
5. Deterministic middleware runs gates.
6. Independent reviewer agent reviews evidence and diff.
7. Human approval required before merge/release.

## Stop conditions

- Secrets, `.env`, private keys, tokens, credentials, or suspected credential material in input/output/logs.
- Migration without tenant provisioning path, `tenant-connection.service.ts`, module wiring, tenant migration, and rollback evidence.
- Dirty or unrelated scope.
- Missing active lease or owner session.
- Missing independent reviewer.
- Failed `npm run -s pr:doctor`.
- Unclear GitHub auth identity or scope.
- Runtime/live claim without exact command evidence and raw response sample.
- Any attempt to edit `/erp` directly from raw Hermes/non-sandbox run.

## Minimum fork changes before testrepo use

- Run and archive `scripts/northstar_local_readiness.sh` output; block unattended bootstrap on FAIL, and require human decision for Docker/GitHub-token WARNs.
- Replace default prompt repo from `langchain-ai/langchainplus` to `ollehillbom1/north-star-erp` for Northstar profile only.
- Add trigger-user allowlist.
- Make empty repo allowlist fail closed.
- Remove/disable Slack and Linear routes/tools from initial deployment profile.
- Disable public http/fetch/search tools for coding-runs by default.
- Add deterministic after-agent middleware for secret scan, dependency audit, pr:doctor, Northstar gate selector, PR evidence log, reviewer-required final gate.
- Add docs/templates/NORTHSTAR_AGENTS.md to target repo later only by explicit approval.
