# Hermes Open SWE Dry Run Pipeline Evidence

Date: 2026-05-22T11:46:00+02:00
Workspace: /home/olle/northstar-agent-harness/open-swe-hermes
Branch: northstar/harness-audit
Pinned upstream commit: faa27479c3b67ce65ba772cc6912a6b923540bfc
Scope: harness-only pure dry-run pipeline composing comment adapter, review trigger router, and next-action policy

## Safety scope

- No /erp product code was edited.
- No server was started.
- No webhook was created or connected.
- No GitHub App was created.
- No ngrok/public tunnel was started.
- No push or PR was made.
- No GitHub API call was made.
- No Kanban write was made.
- No live agent was spawned.
- No credentials, tokens, private keys, or .env contents were read or logged.

## Implemented slice

Implemented deterministic, side-effect-free dry-run pipeline in:

- docs/hermes/pseudo_router/dry_run_pipeline.py

Added stable fixture:

- docs/hermes/examples/github_review_pipeline_input.example.json

Added tests:

- tests/test_hermes_dry_run_pipeline.py

The pipeline composes:

1. docs/hermes/pseudo_router/github_review_adapter.py
2. docs/hermes/pseudo_router/review_trigger_router.py
3. docs/hermes/pseudo_router/next_action_policy.py

## Pipeline behavior

Input:

- comment_event
- review_report
- requested_final_gate

Output schema:

- schema_version=hermes.review-pipeline.v1
- status
- gate
- TASK_ID
- source_event
- stages
- final_decision
- side_effects=[]

Happy-path fixture result:

- status=OK
- gate=WARN
- TASK_ID=GH-REVIEW-42-9001
- final_decision.next_action=START_QA_AGENT
- side_effects=[]

Blocked behavior:

- comment-stage block stops before next-action policy
- policy-stage block returns block_stage=next_action_policy
- ignored comments return status=IGNORED and no next action

CLI command:

```bash
uv run python docs/hermes/pseudo_router/dry_run_pipeline.py docs/hermes/examples/github_review_pipeline_input.example.json
```

CLI dry-run summary:

```text
status=OK gate=WARN task=GH-REVIEW-42-9001 next=START_QA_AGENT side_effects=[]
```

## RED evidence

Command:

```bash
uv run pytest -q tests/test_hermes_dry_run_pipeline.py
```

Expected RED result before implementation:

- exit=2
- collection error
- ModuleNotFoundError: No module named docs.hermes.pseudo_router.dry_run_pipeline

This proved the pipeline module did not exist before implementation.

## GREEN evidence

Command:

```bash
uv run pytest -q tests/test_hermes_dry_run_pipeline.py
```

Result:

- exit=0
- 5 passed, 1 warning in 0.45s

## Targeted integration evidence

Command:

```bash
uv run pytest -q tests/test_hermes_dry_run_pipeline.py tests/test_hermes_next_action_policy.py tests/test_hermes_github_review_adapter.py tests/test_hermes_review_trigger_router_contract.py
```

Result before formatting:

- exit=0
- 24 passed, 1 warning in 3.47s

Result after formatting:

- exit=0
- 24 passed, 1 warning in 0.40s

## Ruff evidence

Command:

```bash
uv run ruff check docs/hermes/pseudo_router/dry_run_pipeline.py docs/hermes/pseudo_router/next_action_policy.py docs/hermes/pseudo_router/github_review_adapter.py tests/test_hermes_dry_run_pipeline.py tests/test_hermes_next_action_policy.py tests/test_hermes_github_review_adapter.py
uv run ruff format --check docs/hermes/pseudo_router/dry_run_pipeline.py docs/hermes/pseudo_router/next_action_policy.py docs/hermes/pseudo_router/github_review_adapter.py tests/test_hermes_dry_run_pipeline.py tests/test_hermes_next_action_policy.py tests/test_hermes_github_review_adapter.py
```

Initial result:

- ruff reported one import-order issue in tests/test_hermes_dry_run_pipeline.py

Fix command:

```bash
uv run ruff check --fix tests/test_hermes_dry_run_pipeline.py
uv run ruff format docs/hermes/pseudo_router/dry_run_pipeline.py tests/test_hermes_dry_run_pipeline.py
```

Final result:

- ruff check: All checks passed
- ruff format --check: 6 files already formatted

## Full test evidence

Command:

```bash
uv run pytest -q tests/
```

Result:

- exit=0
- 365 passed, 8 warnings in 137.23s

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
- scanned 144727554 bytes

Command:

```bash
osv-scanner scan source -r .
```

Result:

- exit=0
- scanned uv.lock, ui/yarn.lock, ui/bun.lock
- No issues found

## File fingerprints

- docs/hermes/pseudo_router/dry_run_pipeline.py
  - lines=106
  - bytes=3582
  - sha256=9cc7748581a85ce3df5d8e0c1ecde67f31c78f7b3821df36c529546a2941800c

- docs/hermes/examples/github_review_pipeline_input.example.json
  - lines=58
  - bytes=1526
  - sha256=4a6316ce3a5516923d2fe6e9e47fd99a1cdb44db065dacf175ef9e649607209d

- tests/test_hermes_dry_run_pipeline.py
  - lines=129
  - bytes=4673
  - sha256=c1fdc820893eab582f9ba8d394e869af18068a18e219b19ad38e7f81d7d7ff5a

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
?? tests/test_hermes_dry_run_pipeline.py
?? tests/test_hermes_github_review_adapter.py
?? tests/test_hermes_next_action_policy.py
?? tests/test_hermes_review_trigger_router_contract.py
?? tests/test_northstar_tool_policy.py
```

This slice intentionally edited/created only:

- docs/hermes/pseudo_router/dry_run_pipeline.py
- docs/hermes/examples/github_review_pipeline_input.example.json
- tests/test_hermes_dry_run_pipeline.py
- docs/bootstrap/HERMES_DRY_RUN_PIPELINE_EVIDENCE.md

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
- No Kanban write.
- No GitHub comment write.
- No production deployment.

## Recommended next slice

Next recommended harness-only slice:

1. Add a safe testrepo bootstrap gate that consumes this pipeline output and emits an approval packet.
2. Keep it file-only and dry-run by default.
3. Require explicit ALLOW_BOOTSTRAP_INSTALL=YES before any future live integration step.
4. Include no GitHub App creation, webhook registration, or push unless separately approved.
5. Re-run RED/GREEN, targeted tests, full tests, ruff, uv audit, gitleaks, trufflehog, and osv-scanner.

GATE=PASS
GATE_REASON=Harness-only dry-run pipeline implemented and verified. RED failed as expected, GREEN passed, CLI fixture run passed, targeted integration tests passed, full tests passed (365 passed), ruff passed after formatting/import fix, uv audit passed, gitleaks/trufflehog/osv-scanner found no issues. No /erp edits, no webhook/GitHub App/server/GitHub API/Kanban write/GitHub comment/push/PR/deploy performed.
