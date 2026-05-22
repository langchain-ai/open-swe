# Hermes Tools and Gates for Northstar Agent Harness

Generated: 2026-05-21T21:10:02+02:00
Commit: faa27479c3b67ce65ba772cc6912a6b923540bfc

## Initial tool policy

### Keep initially

- Sandbox `execute` only inside isolated container/cloud sandbox.
- Sandbox file read/write/edit.
- Sandbox `ls`/glob/grep.
- Todos.
- Task/subagent only when bounded to same repo/scope and evidence requirements.
- GitHub branch/PR operations only through controlled `gh` proxy/wrapper.
- Reviewer tools limited to PR diff/findings/review publishing.

### Disable initially

- Slack tools and Slack trigger.
- Linear tools and Linear trigger.
- General `http_request`, `fetch_url`, and `web_search` for coding-runs.
- External integrations not required for GitHub issue/PR comment flow.
- Local sandbox for autonomous runs.

## Controlled gh proxy policy

The harness should wrap `GH_TOKEN=dummy gh` so it can enforce:

- repo exactly `ollehillbom1/north-star-erp` unless human-approved test repo;
- no direct push to `main`/`master`;
- branch created from `github/main`;
- draft PR by default;
- no force push unless separate explicit approval;
- no secret-bearing output;
- evidence log of commands and sanitized outputs.

## Deterministic after-agent middleware

Add these after-agent gates before any PASS:

0. Local readiness preflight: `scripts/northstar_local_readiness.sh`. PASS is required for unattended bootstrap; WARN requires explicit human decision before affected steps.
0.1. Testrepo bootstrap profile gate: `scripts/northstar_testrepo_bootstrap_gate.sh`. PASS is required before any testrepo GitHub App/webhook/bootstrap approval request.
1. Secret scan: `gitleaks detect --redact --source . --no-git --verbose` and `trufflehog filesystem --no-update --only-verified --fail .`, redacted output only.
2. Dependency audit: `uv audit` for harness Python, and for Northstar use npm-compatible audit where lockfiles support it.
3. Northstar `npm run -s pr:doctor`.
4. Northstar gate selector: choose targeted tests based on diff paths.
5. PR evidence log: root cause, diff scope, verification, data impact, commit message, raw samples for runtime claims.
6. Reviewer-required final gate: independent reviewer must emit PASS or WARN_NO_BLOCKERS; coder/orchestrator cannot self-approve.

## Northstar gate commands

Use npm, not pnpm, for `/erp` because packageManager is npm.

Harness/local candidates:

- `scripts/northstar_local_readiness.sh`
- `scripts/northstar_testrepo_bootstrap_gate.sh`
- `gitleaks detect --redact --source . --no-git --verbose`
- `trufflehog filesystem --no-update --only-verified --fail .`
- `osv-scanner scan source .`
- `uv audit --preview-features audit`
- `grype dir:. -q`
- `syft dir:. -q -o table`

Baseline candidates:

- `./b.sh --quick`
- `npm run typecheck` when API/types/hooks are touched
- `npm run -s pr:doctor` before push/PR
- targeted Jest/Playwright/API smoke command based on changed path
- runtime verification commands for live claims

## Stop-to-human conditions

- any scanner finding that may contain secret material; redact and stop;
- failed pr:doctor;
- test failure not clearly unrelated;
- target branch/repo mismatch;
- reviewer missing or negative;
- unapproved migration;
- dirty scope/unrelated files;
- production deploy requested implicitly rather than explicitly.
