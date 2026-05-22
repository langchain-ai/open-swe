# Hermes Open SWE Next Action Policy Evidence

Date: 2026-05-22T11:36:00+02:00
Workspace: /home/olle/northstar-agent-harness/open-swe-hermes
Branch: northstar/harness-audit
Pinned upstream commit: faa27479c3b67ce65ba772cc6912a6b923540bfc
Scope: harness-only pure reviewer-result to next-action policy adapter

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

Implemented deterministic, side-effect-free policy adapter in:

- docs/hermes/pseudo_router/next_action_policy.py

Added tests in:

- tests/test_hermes_next_action_policy.py

The policy maps an independent reviewer packet plus an explicit action context into a JSON-only next-action decision.

## Policy behavior

Required review fields:

- schema_version
- owner_agent_role
- reviewer_identity
- verdict
- evidence_checked

Required context:

- TASK_ID
- allowed_next_actions

Final PASS rule:

- requested_final_gate=PASS is allowed only for reviewer verdict PASS or WARN_NO_BLOCKERS.
- NEEDS_FOLLOWUP cannot become final PASS.

Supported deterministic action mapping:

- PASS -> MARK_TASK_DONE
- WARN_NO_BLOCKERS -> CREATE_FOLLOWUP_TASK
- qa_agent -> START_QA_AGENT
- builder_fix_agent -> START_BUILDER_FIX_AGENT
- security_agent -> START_SECURITY_AGENT
- accounting_domain_agent -> START_ACCOUNTING_DOMAIN_AGENT

Default-deny behavior:

- missing reviewer_identity blocks
- missing evidence_checked blocks
- missing TASK_ID blocks
- missing allowed_next_actions blocks
- unknown next_agent_role_recommendation blocks
- next action not in allowed_next_actions blocks
- hard stop markers block and escalate

Hard stop marker scan includes:

- secret
- credential
- token
- private key
- unsafe github auth
- dirty scope
- missing reviewer

Side-effect model:

- side_effects=[]
- no GitHub comment
- no Kanban write
- no agent spawn
- no server/webhook start

## RED evidence

Command:

```bash
uv run pytest -q tests/test_hermes_next_action_policy.py
```

Expected RED result before implementation:

- exit=2
- collection error
- ModuleNotFoundError: No module named docs.hermes.pseudo_router.next_action_policy

This proved the policy module did not exist before implementation.

## GREEN evidence

Command:

```bash
uv run pytest -q tests/test_hermes_next_action_policy.py
```

Result:

- exit=0
- 8 passed, 1 warning in 0.01s

## Targeted integration evidence

Command:

```bash
uv run pytest -q tests/test_hermes_next_action_policy.py tests/test_hermes_github_review_adapter.py tests/test_hermes_review_trigger_router_contract.py
```

Result before formatting:

- exit=0
- 19 passed, 1 warning in 0.02s

Result after formatting:

- exit=0
- 19 passed, 1 warning in 0.02s

## Ruff evidence

Command:

```bash
uv run ruff check docs/hermes/pseudo_router/next_action_policy.py docs/hermes/pseudo_router/github_review_adapter.py docs/hermes/pseudo_router/review_trigger_router.py tests/test_hermes_next_action_policy.py tests/test_hermes_github_review_adapter.py tests/test_hermes_review_trigger_router_contract.py
uv run ruff format --check docs/hermes/pseudo_router/next_action_policy.py docs/hermes/pseudo_router/github_review_adapter.py docs/hermes/pseudo_router/review_trigger_router.py tests/test_hermes_next_action_policy.py tests/test_hermes_github_review_adapter.py tests/test_hermes_review_trigger_router_contract.py
```

Initial result:

- ruff reported one import-order issue in tests/test_hermes_next_action_policy.py

Fix command:

```bash
uv run ruff check --fix tests/test_hermes_next_action_policy.py
uv run ruff format docs/hermes/pseudo_router/next_action_policy.py tests/test_hermes_next_action_policy.py
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
- 360 passed, 8 warnings in 136.33s

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
- scanned 143404271 bytes

Command:

```bash
osv-scanner scan source -r .
```

Result:

- exit=0
- scanned uv.lock, ui/yarn.lock, ui/bun.lock
- No issues found

## File fingerprints

- docs/hermes/pseudo_router/next_action_policy.py
  - lines=142
  - bytes=4768
  - sha256=1eb1aee3b09fe74481fd538bc81f7b7bbdbfa7171ea11f1c147ae2bcb10aa6aa

- tests/test_hermes_next_action_policy.py
  - lines=140
  - bytes=4870
  - sha256=c5660635f2e5415877ea21368f24e2c439a539250f72cfe2cb8d7bfa8c083785

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
?? tests/test_hermes_next_action_policy.py
?? tests/test_hermes_review_trigger_router_contract.py
?? tests/test_northstar_tool_policy.py
```

This slice intentionally edited/created only:

- docs/hermes/pseudo_router/next_action_policy.py
- tests/test_hermes_next_action_policy.py
- docs/bootstrap/HERMES_NEXT_ACTION_POLICY_EVIDENCE.md

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

1. Compose the current three pure pieces into one dry-run pipeline:
   - GitHub comment adapter
   - review trigger router
   - next-action policy
2. Add a JSON fixture that simulates `@openswe review` -> reviewer report -> next-action decision.
3. Keep the pipeline file-only and side-effect-free.
4. Add CLI dry-run output with stable schema and no secret-bearing fields.
5. Re-run RED/GREEN, targeted tests, full tests, ruff, uv audit, gitleaks, trufflehog, and osv-scanner.

GATE=PASS
GATE_REASON=Harness-only next-action policy adapter implemented and verified. RED failed as expected, GREEN passed, targeted integration tests passed, full tests passed (360 passed), ruff passed after formatting/import fix, uv audit passed, gitleaks/trufflehog/osv-scanner found no issues. No /erp edits, no webhook/GitHub App/server/GitHub API/Kanban write/GitHub comment/push/PR/deploy performed.
