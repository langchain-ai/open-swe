# Hermes Open SWE GitHub Review Adapter Evidence

Date: 2026-05-21T23:43:00+02:00
Workspace: /home/olle/northstar-agent-harness/open-swe-hermes
Branch: northstar/harness-audit
Pinned upstream commit: faa27479c3b67ce65ba772cc6912a6b923540bfc
Scope: harness-only pure adapter from GitHub review comments to the dry-run review-loop contract

## Safety scope

- No /erp product code was edited.
- No server was started.
- No webhook was created or connected.
- No GitHub App was created.
- No ngrok/public tunnel was started.
- No push or PR was made.
- No GitHub API call was made by the adapter.
- No live agent was spawned.
- No credentials, tokens, private keys, or .env contents were read or logged.

## Implemented slice

Implemented a no-network adapter in:

- docs/hermes/pseudo_router/github_review_adapter.py

Added tests in:

- tests/test_hermes_github_review_adapter.py

The adapter maps an already-received GitHub comment-like dictionary into the existing dry-run router contract exposed by:

- docs/hermes/pseudo_router/review_trigger_router.py

It uses the upstream Open SWE parser:

- agent/utils/github_comments.py::parse_github_review_command

## Behavior

Supported trigger comments:

- `@openswe review`
- `@open-swe review`
- optional PR URL, preserved for later validation but not fetched

Default allowlist:

- repo: ollehillbom1/north-star-erp
- trigger user: ollehillbom1

Blocked before contract routing:

- missing required event fields
- unapproved trigger user

Routed into contract and blocked by contract:

- unknown repo
- forbidden path overlap
- disallowed tool request
- missing evidence

Side-effect model:

- side_effects=[]
- no webhook/server/GitHub API/runtime start fields emitted
- git diff summary explicitly says `not_fetched_in_dry_run`

## RED evidence

Command:

```bash
uv run pytest -q tests/test_hermes_github_review_adapter.py
```

Expected RED result before implementation:

- exit=2
- collection error
- ModuleNotFoundError: No module named docs.hermes.pseudo_router.github_review_adapter

This proved the adapter did not exist before implementation.

## GREEN evidence

Command:

```bash
uv run pytest -q tests/test_hermes_github_review_adapter.py
```

Result:

- exit=0
- 6 passed, 1 warning in 0.01s

## Targeted integration evidence

Command:

```bash
uv run pytest -q tests/test_hermes_github_review_adapter.py tests/test_hermes_review_trigger_router_contract.py tests/test_github_comment_prompts.py
```

Result before formatting:

- exit=0
- 18 passed, 1 warning in 0.01s

Result after formatting:

- exit=0
- 18 passed, 1 warning in 0.02s

## Ruff evidence

Command:

```bash
uv run ruff check docs/hermes/pseudo_router/github_review_adapter.py docs/hermes/pseudo_router/review_trigger_router.py tests/test_hermes_github_review_adapter.py tests/test_hermes_review_trigger_router_contract.py
uv run ruff format --check docs/hermes/pseudo_router/github_review_adapter.py docs/hermes/pseudo_router/review_trigger_router.py tests/test_hermes_github_review_adapter.py tests/test_hermes_review_trigger_router_contract.py
```

First result:

- ruff check: All checks passed
- ruff format --check: one file would be reformatted: tests/test_hermes_github_review_adapter.py

Fix command:

```bash
uv run ruff format tests/test_hermes_github_review_adapter.py
```

Final result:

- ruff check: All checks passed
- ruff format --check: 4 files already formatted

## Full test evidence

Command:

```bash
uv run pytest -q tests/
```

Result:

- exit=0
- 352 passed, 8 warnings in 137.14s

Warnings were pre-existing/dependency/runtime warnings:

- langsmith.sandbox alpha FutureWarning
- ast.Str deprecation warning in langsmith evaluation runner
- LangChainDeprecationWarning for message.text() in existing tests
- RuntimeWarning about AsyncMock not awaited in existing Linear webhook tests

## Audit and scanner evidence

Command:

```bash
uv audit
```

Result:

- exit=0
- Found no known vulnerabilities and no adverse project statuses in 147 packages

Command:

```bash
gitleaks detect --no-git --redact --exit-code 1
```

Result:

- exit=0
- no leaks found

Command:

```bash
trufflehog filesystem . --no-update --only-verified --fail
```

Result:

- exit=0
- verified_secrets=0
- unverified_secrets=0
- scanned 143359996 bytes

Command:

```bash
osv-scanner scan source -r .
```

Result:

- exit=0
- scanned uv.lock, ui/yarn.lock, ui/bun.lock
- No issues found

## File fingerprints

- docs/hermes/pseudo_router/github_review_adapter.py
  - lines=120
  - bytes=4054
  - sha256=94cab9e20c74616b99ab3c88c7c549614bf69d2e2009351af8d03dc10059e2be

- docs/hermes/pseudo_router/review_trigger_router.py
  - lines=236
  - bytes=8323
  - sha256=80b2a03f0fbaf6ec11a998123b791dcce82c3b13ed313ba6c03dc853e79910ce

- tests/test_hermes_github_review_adapter.py
  - lines=89
  - bytes=3133
  - sha256=c46cce9bb0d70f35e03100c9302cec6d7c10de46017836891b7440010415c0d1

## Current git status note

Observed git status includes pre-existing/unrelated harness changes from earlier phases:

```text
## northstar/harness-audit
 M .gitignore
 M agent/reviewer.py
 M agent/server.py
 M agent/webapp.py
?? .env.example
?? .gitleaks.toml
?? .python-version
?? agent/utils/tool_policy.py
?? docs/
?? scripts/northstar_local_readiness.sh
?? scripts/northstar_testrepo_bootstrap_gate.sh
?? tests/test_hermes_github_review_adapter.py
?? tests/test_hermes_review_trigger_router_contract.py
?? tests/test_northstar_tool_policy.py
```

This slice intentionally edited/created only:

- docs/hermes/pseudo_router/github_review_adapter.py
- tests/test_hermes_github_review_adapter.py
- docs/bootstrap/HERMES_GITHUB_REVIEW_ADAPTER_EVIDENCE.md

## Explicitly not done

- No ERP feature work.
- No /erp source edit.
- No Open SWE server start.
- No GitHub App creation.
- No webhook registration.
- No public tunnel.
- No GitHub push or PR.
- No Slack/Linear activation.
- No live agent spawning.
- No production deployment.

## Recommended next slice

Next recommended slice is still harness-only:

1. Add a pure policy adapter for reviewer result -> next-action decision.
2. Require reviewer verdict, reviewer identity, evidence log, allowed action enum, and stop-condition scan.
3. Block final PASS unless reviewer decision is PASS or WARN_NO_BLOCKERS.
4. Keep output as a deterministic JSON packet only; no Kanban writes, no GitHub comments, no agent spawn.
5. Re-run RED/GREEN, targeted tests, full tests, ruff, uv audit, gitleaks, trufflehog, and osv-scanner.

GATE=PASS
GATE_REASON=Harness-only GitHub comment adapter implemented and verified. RED failed as expected, GREEN passed, parser/contract integration tests passed, full tests passed (352 passed), ruff passed after formatting, uv audit passed, gitleaks/trufflehog/osv-scanner found no issues. No /erp edits, no webhook/GitHub App/server/GitHub API/push/PR/deploy performed.
