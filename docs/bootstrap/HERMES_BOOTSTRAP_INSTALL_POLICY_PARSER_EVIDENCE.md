# Hermes Bootstrap Install Policy Parser Evidence

Verification evidence:
- Harness root: `/home/olle/northstar-agent-harness/open-swe-hermes`.
- Branch: `northstar/harness-audit`.
- HEAD: `faa27479c3b67ce65ba772cc6912a6b923540bfc`.
- Scope safety: harness root resolves outside `/erp`; `/erp/kanban/agent-control.mjs status` returned active=0/edit=0/readonly=0/stale=0 before edits.
- RED command: `uv run pytest -q tests/test_hermes_bootstrap_install_policy_parser.py`.
- RED result before implementation: `ModuleNotFoundError: No module named 'docs.hermes.pseudo_router.bootstrap_install_policy_parser'`.
- GREEN command: `uv run pytest -q tests/test_hermes_bootstrap_install_policy_parser.py`.
- GREEN result: `6 passed, 1 warning in 0.06s`.
- Targeted regression command: `uv run pytest -q tests/test_hermes_bootstrap_install_policy_parser.py tests/test_hermes_bootstrap_execution_manifest.py tests/test_hermes_bootstrap_packet_writer.py tests/test_hermes_bootstrap_packet_renderer.py tests/test_hermes_testrepo_bootstrap_approval_gate.py`.
- Targeted regression result: `27 passed, 1 warning in 0.22s`.
- Full suite command: `uv run pytest -q tests/`.
- Full suite result: `392 passed, 8 warnings in 137.07s`.
- Formatter/linter commands: `uv run ruff format --check docs/hermes/pseudo_router/bootstrap_install_policy_parser.py tests/test_hermes_bootstrap_install_policy_parser.py`; `uv run ruff check docs/hermes/pseudo_router/bootstrap_install_policy_parser.py tests/test_hermes_bootstrap_install_policy_parser.py`.
- Formatter/linter results: `2 files already formatted`; `All checks passed!`.
- Audit/scanner commands: `uv audit --preview-features audit`; `gitleaks detect --no-git --redact --exit-code 1`; `trufflehog filesystem . --no-update --only-verified --fail`; `osv-scanner scan source -r .`; `(cd ui && yarn audit --level moderate)`.
- Audit/scanner results: uv audit found no known vulnerabilities; gitleaks no leaks; trufflehog verified_secrets=0 and unverified_secrets=0; OSV no issues; yarn audit 0 vulnerabilities.
- Semgrep: MISSING (`command -v semgrep` produced no path).
- CLI dry-run command: `uv run python docs/hermes/pseudo_router/bootstrap_install_policy_parser.py docs/hermes/examples/testrepo_bootstrap_install_policy_input.example.json > docs/hermes/examples/testrepo_bootstrap_install_policy_output.example.json`.
- CLI dry-run result: status=`POLICY_EVALUATED`, gate=`PASS`, `INSTALL_ALLOWED=true`, side_effects=`[]`, allowed_actions=`['dry_run_testrepo_bootstrap_install']`.

Files created/changed in this slice:
- `docs/hermes/pseudo_router/bootstrap_install_policy_parser.py`
- `tests/test_hermes_bootstrap_install_policy_parser.py`
- `docs/hermes/examples/testrepo_bootstrap_install_policy_input.example.json`
- `docs/hermes/examples/testrepo_bootstrap_install_policy_output.example.json`
- `docs/bootstrap/HERMES_BOOTSTRAP_INSTALL_POLICY_PARSER_EVIDENCE.md`

SHA256:
- `docs/hermes/pseudo_router/bootstrap_install_policy_parser.py`: `996a87d0ef5e96d11c351800c09297a0198bd088abf358f5f97aac8e5a923f52`
- `tests/test_hermes_bootstrap_install_policy_parser.py`: `dcfd47a486aa9b814020aea9f956296bbefa0754a28655ce39ad540d671065f9`
- `docs/hermes/examples/testrepo_bootstrap_install_policy_input.example.json`: `074ca8a5fe0e35ad1639e11e66f52f35c353648bac4d884fcd9d0fd3c76df1ee`
- `docs/hermes/examples/testrepo_bootstrap_install_policy_output.example.json`: `87f37fbd39675bc290e0fe2744dfcba549a7677a77637c44c3907d45f0f68ce8`

Policy contract:
- Schema: `hermes.bootstrap-install-policy.v1`.
- Default result is always `INSTALL_ALLOWED=false` unless all allow conditions pass.
- Required exact approval phrase: `ALLOW_BOOTSTRAP_INSTALL=YES`.
- Exact means the phrase must appear as a standalone line; casing changes, inserted spaces, suffixes and semicolon-tailed instructions remain blocked.
- Target repo must be in the explicit testrepo allowlist.
- Northstar production repo is blocked unless explicitly present in the supplied testrepo allowlist; the example allowlist contains only `ollehillbom1/hermes-open-swe-testrepo`.
- Secret-like approval text blocks the decision and is not echoed in output.
- Output never includes raw human approval text.
- Allowed action, when granted, is only `dry_run_testrepo_bootstrap_install`.
- Parser side effects are always `[]`.

Explicitly not performed:
- No server or worker started.
- No GitHub App created.
- No webhook configured.
- No ngrok/public tunnel opened.
- No branch pushed and no PR opened.
- No production deploy performed.
- No `/erp` product code edited.
- No secrets, `.env` contents, private keys, tokens or credentials read/logged.

GATE=PASS
GATE_REASON=Dry-run ALLOW_BOOTSTRAP_INSTALL policy parser is implemented with default-deny semantics, exact approval phrase matching, explicit testrepo allowlist gating, no raw approval-text echo, no side effects, and green RED/GREEN, targeted/full tests, ruff, audits and scanners.
