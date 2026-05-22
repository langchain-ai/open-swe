# Hermes Testrepo Bootstrap Approval Gate Evidence

Generated: 2026-05-22

Scope: harness-only pure Python approval gate for a safe testrepo bootstrap packet.
No server start, no webhook, no GitHub App, no ngrok/public tunnel, no push/PR, no /erp edit, and no production install.

## Preflight evidence

Command:

```bash
pwd && git status --short --branch && git rev-parse HEAD && git branch --show-current && python3 --version && uv --version
```

Result:

```text
/home/olle/northstar-agent-harness/open-swe-hermes
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
faa27479c3b67ce65ba772cc6912a6b923540bfc
northstar/harness-audit
Python 3.14.4
uv 0.11.11 (x86_64-unknown-linux-gnu)
```

Command:

```bash
node /erp/kanban/agent-control.mjs status
```

Result summary:

```text
status=ok
active=0
edit=0
readonly=0
stale=[]
```

## RED evidence

Command:

```bash
uv run pytest -q tests/test_hermes_testrepo_bootstrap_approval_gate.py
```

Result before implementation:

```text
ERROR tests/test_hermes_testrepo_bootstrap_approval_gate.py
ModuleNotFoundError: No module named 'docs.hermes.pseudo_router.testrepo_bootstrap_approval_gate'
exit=2
```

Expected failure: missing module. This confirmed the test was exercising new behavior before implementation.

## Implementation summary

Added pure module:

- `docs/hermes/pseudo_router/testrepo_bootstrap_approval_gate.py`

Added testrepo bootstrap profile fixture:

- `docs/hermes/examples/testrepo_bootstrap_profile.example.json`

Added tests:

- `tests/test_hermes_testrepo_bootstrap_approval_gate.py`

Behavior:

- consumes side-effect-free review pipeline output;
- validates target repo is a non-Northstar allowlisted test repo;
- blocks `ollehillbom1/north-star-erp` during testrepo bootstrap phase;
- blocks local sandbox for autonomy;
- requires Slack/Linear and generic egress tools to remain disabled;
- emits `READY_FOR_HUMAN_APPROVAL` with exact required approval phrase;
- exact approval only produces `APPROVED_DRY_RUN_PLAN` and still does not authorize GitHub App/webhook/push/server actions;
- always returns `side_effects=[]`.

## GREEN / targeted test evidence

Command:

```bash
uv run pytest -q tests/test_hermes_testrepo_bootstrap_approval_gate.py
```

Result:

```text
6 passed, 1 warning in 0.01s
```

Command:

```bash
uv run ruff format docs/hermes/pseudo_router/testrepo_bootstrap_approval_gate.py && uv run ruff check docs/hermes/pseudo_router/testrepo_bootstrap_approval_gate.py tests/test_hermes_testrepo_bootstrap_approval_gate.py && uv run ruff format --check docs/hermes/pseudo_router/testrepo_bootstrap_approval_gate.py tests/test_hermes_testrepo_bootstrap_approval_gate.py
```

Result:

```text
1 file reformatted
All checks passed!
2 files already formatted
```

Command:

```bash
uv run pytest -q tests/test_hermes_testrepo_bootstrap_approval_gate.py tests/test_hermes_dry_run_pipeline.py tests/test_hermes_next_action_policy.py tests/test_hermes_github_review_adapter.py tests/test_hermes_review_trigger_router_contract.py
```

Result:

```text
30 passed, 1 warning in 0.42s
```

## CLI dry-run evidence

Command:

```bash
uv run python -c 'import json; from pathlib import Path; from docs.hermes.pseudo_router.dry_run_pipeline import run_pipeline; payload=json.loads(Path("docs/hermes/examples/github_review_pipeline_input.example.json").read_text()); Path("/tmp/hermes_pipeline_result.json").write_text(json.dumps(run_pipeline(payload), indent=2, ensure_ascii=False), encoding="utf-8")' && uv run python docs/hermes/pseudo_router/testrepo_bootstrap_approval_gate.py /tmp/hermes_pipeline_result.json docs/hermes/examples/testrepo_bootstrap_profile.example.json
```

Result summary:

```text
status=READY_FOR_HUMAN_APPROVAL
gate=WARN
TASK_ID=GH-REVIEW-42-9001
target_repo=ollehillbom1/northstar-agent-harness-testrepo
required_exact_approval=ALLOW_TESTREPO_BOOTSTRAP=YES repo=ollehillbom1/northstar-agent-harness-testrepo
dry_run_only=true
may_create_github_app=false
may_configure_webhook=false
may_push_or_pr=false
may_start_server=false
may_edit_erp=false
side_effects=[]
```

## Full suite evidence

Command:

```bash
uv run pytest -q tests/
```

Result:

```text
371 passed, 8 warnings in 135.03s (0:02:15)
```

Warnings are pre-existing upstream/dependency warnings from LangSmith/LangChain and existing Linear webhook tests.

## Audit/scanner evidence

Command:

```bash
uv audit --preview-features audit
```

Result:

```text
Resolved 148 packages
Found no known vulnerabilities and no adverse project statuses in 147 packages
```

Command:

```bash
gitleaks detect --no-git --redact --exit-code 1 --source .
```

Result:

```text
no leaks found
```

Command:

```bash
trufflehog filesystem . --no-update --only-verified --fail
```

Result:

```text
verified_secrets=0
unverified_secrets=0
```

Command:

```bash
osv-scanner scan source -r .
```

Result:

```text
No issues found
```

## File hashes

Command:

```bash
sha256sum docs/hermes/pseudo_router/testrepo_bootstrap_approval_gate.py docs/hermes/examples/testrepo_bootstrap_profile.example.json tests/test_hermes_testrepo_bootstrap_approval_gate.py
wc -l docs/hermes/pseudo_router/testrepo_bootstrap_approval_gate.py docs/hermes/examples/testrepo_bootstrap_profile.example.json tests/test_hermes_testrepo_bootstrap_approval_gate.py
```

Result:

```text
825dc3c2a56a7c9e869d34e7c13ac6865cf9b5110cec14d01b7e2cf53713cd8f  docs/hermes/pseudo_router/testrepo_bootstrap_approval_gate.py
d532c43f5b4a0ffa91f7758f5a9f36b21319b3b99a54047a9de3fadef6707ea1  docs/hermes/examples/testrepo_bootstrap_profile.example.json
072b38834e7e1677e72e808d161c4b8ea230f86916fb12d57b35ee3f468d6dc0  tests/test_hermes_testrepo_bootstrap_approval_gate.py
178 docs/hermes/pseudo_router/testrepo_bootstrap_approval_gate.py
25 docs/hermes/examples/testrepo_bootstrap_profile.example.json
129 tests/test_hermes_testrepo_bootstrap_approval_gate.py
```

## Explicit non-actions

Not done:

- no ERP feature work;
- no `/erp` product edit;
- no GitHub App creation;
- no webhook setup;
- no ngrok/public tunnel;
- no GitHub push or PR;
- no production install/deploy;
- no server start;
- no Docker build;
- no real agent spawn;
- no external API call from the new module;
- no secrets or credentials read/logged.

GATE=PASS
GATE_REASON=The safe testrepo bootstrap approval gate is implemented as a deterministic side-effect-free dry-run component, verified by RED/GREEN tests, targeted integration tests, CLI fixture run, full test suite, ruff, uv audit, and secret/dependency scanners. The output requires explicit human approval and still authorizes only an approved dry-run plan, not live GitHub App/webhook/push/server actions.
