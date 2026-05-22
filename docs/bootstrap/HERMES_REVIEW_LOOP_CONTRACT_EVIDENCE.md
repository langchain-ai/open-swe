# Hermes Open SWE Review Loop Contract Evidence

Date: 2026-05-21T23:28:00+02:00
Workspace: /home/olle/northstar-agent-harness/open-swe-hermes
Branch: northstar/harness-audit
Pinned upstream commit: faa27479c3b67ce65ba772cc6912a6b923540bfc
Scope: harness-only pure Python review-loop contract and tests

## Safety scope

- No /erp product code was edited.
- No server was started.
- No webhook was created or connected.
- No GitHub App was created.
- No ngrok/public tunnel was started.
- No push or PR was made.
- No credentials, tokens, private keys, or .env contents were read or logged.

## Implemented slice

Implemented a pure dry-run review-loop contract in:

- docs/hermes/pseudo_router/review_trigger_router.py

Added tests in:

- tests/test_hermes_review_trigger_router_contract.py

The contract exposes `route_review_trigger(payload)` and returns structured output for a future Open SWE/Hermes integration without runtime side effects.

Default policy in this slice:

- schema_version: hermes.review-loop.v1
- allowed repo: ollehillbom1/north-star-erp only
- blocked by default: unknown repos
- blocked by default: forbidden path included in allowed paths
- blocked by default: external tools not in the initial allowed set
- blocked by default: missing evidence log
- no agent spawn, runtime start, GitHub write, webhook handling, or network operation

Allowed next actions encoded:

- MARK_TASK_DONE
- CREATE_FOLLOWUP_TASK
- START_BUILDER_FIX_AGENT
- START_QA_AGENT
- START_SECURITY_AGENT
- START_ACCOUNTING_DOMAIN_AGENT
- BLOCK_AND_ESCALATE

## RED evidence

Command:

```bash
uv run pytest -q tests/test_hermes_review_trigger_router_contract.py
```

Expected RED result before implementation:

- exit=1
- 5 failed
- all failures were AttributeError: module docs.hermes.pseudo_router.review_trigger_router has no attribute route_review_trigger

This proved the new contract did not exist before implementation.

## GREEN evidence

Command:

```bash
uv run pytest -q tests/test_hermes_review_trigger_router_contract.py
```

Result:

- exit=0
- 5 passed, 1 warning in 0.01s

## Targeted regression evidence

Command:

```bash
uv run pytest -q tests/test_hermes_review_trigger_router_contract.py tests/test_northstar_tool_policy.py
```

Result:

- exit=0
- 10 passed, 1 warning in 0.02s

## Dry-run router evidence

Command:

```bash
uv run python docs/hermes/pseudo_router/review_trigger_router.py docs/hermes/examples/review_trigger_input.example.json
```

Summarized result:

- status=OK
- event_type=independent review recommended
- review_verdict=NEEDS_FOLLOWUP
- next_decision=START_QA_AGENT
- has_review_prompt=True
- has_next_prompt=True

## Ruff evidence

Command:

```bash
uv run ruff check --fix docs/hermes/pseudo_router/review_trigger_router.py tests/test_hermes_review_trigger_router_contract.py
uv run ruff format docs/hermes/pseudo_router/review_trigger_router.py tests/test_hermes_review_trigger_router_contract.py
uv run ruff check docs/hermes/pseudo_router/review_trigger_router.py tests/test_hermes_review_trigger_router_contract.py
uv run ruff format --check docs/hermes/pseudo_router/review_trigger_router.py tests/test_hermes_review_trigger_router_contract.py
```

Result:

- first ruff check found one import-order issue in the new test file
- ruff --fix fixed it
- final ruff check: All checks passed
- final ruff format --check: 2 files already formatted

## Full test evidence

Command:

```bash
uv run pytest -q tests/
```

Result:

- exit=0
- 346 passed, 8 warnings in 137.34s

Warnings were from existing dependency/runtime test behavior:

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
- scanned 143351294 bytes

Command:

```bash
osv-scanner scan source -r .
```

Result:

- exit=0
- scanned uv.lock, ui/yarn.lock, ui/bun.lock
- No issues found

## File fingerprints

- docs/hermes/pseudo_router/review_trigger_router.py
  - lines=236
  - bytes=8323
  - sha256=80b2a03f0fbaf6ec11a998123b791dcce82c3b13ed313ba6c03dc853e79910ce

- tests/test_hermes_review_trigger_router_contract.py
  - lines=92
  - bytes=3414
  - sha256=8ae5eb47fff99ca334a4a808664b55cfc80b0a9c9dc8e8b1d91e5ae1347d9ed5

## Current git status note

Observed git status includes pre-existing/unrelated harness changes from earlier phases:

```text
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
?? tests/test_hermes_review_trigger_router_contract.py
?? tests/test_northstar_tool_policy.py
```

This slice intentionally edited only:

- docs/hermes/pseudo_router/review_trigger_router.py
- tests/test_hermes_review_trigger_router_contract.py
- docs/bootstrap/HERMES_REVIEW_LOOP_CONTRACT_EVIDENCE.md

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

Next recommended slice is a no-network integration adapter test that maps Open SWE GitHub comment parsing output to this pure contract:

1. Add tests for `@openswe review` and `@open-swe review` commands using existing `agent/utils/github_comments.py` parser.
2. Map parsed repo/user/comment metadata into `route_review_trigger(payload)`.
3. Keep the adapter pure: no FastAPI route changes, no GitHub API calls, no webhook signature path changes.
4. Re-run targeted tests, full tests, ruff, uv audit, and secret scanners.

GATE=PASS
GATE_REASON=Harness-only pure Python review-loop contract implemented and verified. RED failed as expected, GREEN passed, targeted tests passed, full tests passed (346 passed), ruff passed after import-order fix, uv audit passed, gitleaks/trufflehog/osv-scanner found no issues. No /erp edits, no webhook/GitHub App/server/push/PR/deploy performed.
