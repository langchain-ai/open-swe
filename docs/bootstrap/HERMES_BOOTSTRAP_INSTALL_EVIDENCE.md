# HERMES_BOOTSTRAP_INSTALL_EVIDENCE

Generated: 2026-05-21T21:45+02:00
Scope: local dev install for testrepo only
Workspace: /home/olle/northstar-agent-harness/open-swe-hermes
Branch: northstar/harness-audit
Pinned upstream HEAD: faa27479c3b67ce65ba772cc6912a6b923540bfc

Hard exclusions honored:
- No /erp writes from this bootstrap slice.
- No GitHub App created.
- No webhook started.
- No ngrok/public tunnel started.
- No push/PR.
- No production deploy/artifacts.
- No global tool installs.
- No curl|bash installation.
- No new npm/pnpm/yarn lockfile created for audit.

Special workspace risk:
- /erp is dirty and was treated as external read-only context.
- External untracked file observed and not touched: /erp/kanban/prompts/HERMES_OPEN_SWE_HARNESS_20260521.md
- /erp status remained dirty with unrelated modified/untracked files; it was not mixed into harness work.

## 1. commands_run

1. git status --short
2. git branch --show-current
3. git rev-parse HEAD
4. uv --version
5. uv python list
6. uv run python --version
7. search/read local Open SWE Python requirement evidence in pyproject.toml, uv.lock, INSTALLATION.md
8. write .env.example with placeholders only
9. uv sync --all-extras
10. uv run python --version && uv run pytest -vvv tests/
11. uv audit
12. uv run ruff check .
13. uv run ruff format --check .
14. uv run ruff format docs/hermes/pseudo_router/review_trigger_router.py
15. uv run ruff check . && uv run ruff format --check .
16. inspect ui/package.json
17. check ui lockfiles: package-lock.json, pnpm-lock.yaml, yarn.lock, bun.lock
18. yarn --version
19. yarn npm audit --all
20. yarn audit --level moderate
21. command -v gitleaks/trufflehog/osv-scanner/semgrep
22. gitleaks detect --source . --no-git --redact --exit-code 1
23. trufflehog filesystem . --no-update --only-verified --fail
24. osv-scanner scan source -r .
25. git status --short && git diff --name-only && git -C /erp status --short --branch
26. SHA256/line/byte verification for .env.example and formatted pseudo_router

## 2. command_exit_codes

- git status/branch/HEAD/uv python evidence: exit=0
- uv sync --all-extras: exit=0
- uv run python --version: exit=0
- uv run pytest -vvv tests/: exit=0
- uv audit: exit=0
- uv run ruff check .: exit=0
- initial uv run ruff format --check .: exit=1, one docs/hermes pseudo-router file needed formatting
- uv run ruff format docs/hermes/pseudo_router/review_trigger_router.py: exit=0
- final uv run ruff check . && uv run ruff format --check .: exit=0
- yarn npm audit --all: exit=1, Yarn v1 treats `npm` as a missing script/command
- yarn audit --level moderate: exit=0
- gitleaks detect: exit=0
- trufflehog filesystem: exit=0
- osv-scanner scan source -r .: exit=0
- semgrep: MISSING, not run

## 3. files_created_or_changed

Created:
- .env.example
  - lines=67
  - bytes=2724
  - sha256=75d0f35af6b4b1015439c0b2da61a3899ccd9c05713eefb901f81189c79c970f
- docs/bootstrap/HERMES_BOOTSTRAP_INSTALL_EVIDENCE.md
  - this evidence report

Changed:
- docs/hermes/pseudo_router/review_trigger_router.py
  - reason: ruff format correction after `uv run ruff format --check .` reported it would reformat
  - lines=117
  - bytes=4350
  - sha256=c5beaca9552cfac09af6f98771c8142274f3d4438c6dcee778d2975f6c16bb71

Pre-existing untracked harness files from prior harness/audit phase remain untracked:
- .gitleaks.toml
- .python-version
- docs/
- scripts/northstar_local_readiness.sh

No files outside /home/olle/northstar-agent-harness/open-swe-hermes were changed by this bootstrap slice.

## 4. python_version_evidence

- uv --version: uv 0.11.11 (x86_64-unknown-linux-gnu)
- uv python list includes:
  - cpython-3.13.13-linux-x86_64-gnu at /home/olle/.local/bin/python3.13 -> /home/olle/.local/share/uv/python/cpython-3.13.13-linux-x86_64-gnu/bin/python3.13
  - cpython-3.11.15-linux-x86_64-gnu also available
  - system python3 is 3.14.4, but not used for Open SWE venv execution
- uv run python --version: Python 3.13.13
- local .python-version: 3.13.13
- pyproject.toml line 6: requires-python = ">=3.11"
- uv.lock lines 2-6:
  - revision = 3
  - requires-python = ">=3.11"
  - resolution-markers include python_full_version >= '3.14' and < '3.14'
- INSTALLATION.md line 10 states: Python 3.11 – 3.13; 3.14 is not yet supported due to dependency constraints.

Conclusion: uv-managed Python 3.13.13 is used and satisfies the documented Open SWE 3.11-3.13 installation constraint.

## 5. uv_sync_result

Command: uv sync --all-extras
Exit: 0
Summary:
- Resolved 148 packages
- Installed dev extras including pytest, pytest-asyncio, ruff
- No global install performed
- Local .venv was used under harness working copy

## 6. pytest_result

Command: uv run python --version && uv run pytest -vvv tests/
Exit: 0
Summary:
- Python 3.13.13
- 336 tests collected
- 336 passed
- 8 warnings
- Runtime: 135.90s

Warnings were non-blocking:
- langsmith.sandbox alpha warning
- ast.Str deprecation from langsmith evaluation runner
- LangChain .text() deprecation warning
- RuntimeWarning for AsyncMock not awaited in two Linear webhook tests

## 7. uv_audit_result

Command: uv audit
Exit: 0
Summary:
- Resolved 148 packages
- Found no known vulnerabilities and no adverse project statuses in 147 packages
- uv audit itself warned that the command is experimental

## 8. ruff_result

Commands:
- uv run ruff check .
- uv run ruff format --check .
- uv run ruff format docs/hermes/pseudo_router/review_trigger_router.py
- uv run ruff check . && uv run ruff format --check .

Result:
- Initial check: PASS
- Initial format check: WARN, one docs/hermes pseudo-router file would be reformatted
- Applied formatting only inside harness docs/hermes pseudo-router
- Final check: PASS, `All checks passed!`
- Final format check: PASS, `125 files already formatted`

## 9. ui_audit_result

UI directory: /home/olle/northstar-agent-harness/open-swe-hermes/ui

Observed:
- package.json: exists
- package-lock.json: missing
- pnpm-lock.yaml: missing
- yarn.lock: exists
- bun.lock: exists
- packageManager field in package.json: not present
- yarn available: /home/olle/.local/bin/yarn
- yarn --version: 1.22.22
- bun command: missing
- pnpm available, but no pnpm-lock.yaml
- npm available, but no package-lock.json and npm audit would require/generate npm lock state, so it was not used

Attempted safe audit:
- `yarn npm audit --all`: exit=1 because Yarn v1 has no `npm` subcommand
- `yarn audit --level moderate`: exit=0, `0 vulnerabilities found - Packages audited: 756`

UI_AUDIT=PASS_YARN_AUDIT

## 10. scanner_result

Installed/MISSING:
- gitleaks: present at /usr/bin/gitleaks
- trufflehog: present at /home/olle/.local/bin/trufflehog
- osv-scanner: present at /home/olle/.local/bin/osv-scanner
- semgrep: MISSING, not installed and not installed globally

Runs:
- gitleaks detect --source . --no-git --redact --exit-code 1
  - exit=0
  - no leaks found
- trufflehog filesystem . --no-update --only-verified --fail
  - exit=0
  - verified_secrets=0
  - unverified_secrets=0
- osv-scanner scan source -r .
  - exit=0
  - scanned uv.lock, ui/yarn.lock and ui/bun.lock
  - no issues found
- semgrep
  - SCANNER=SKIPPED_MISSING

## 11. env_example_status

Created: .env.example
Status: PLACEHOLDERS_ONLY

Evidence:
- gitleaks: no leaks found
- trufflehog: verified_secrets=0, unverified_secrets=0
- file uses REPLACE_WITH_TEST_* placeholders and example-owner/example-testrepo only
- no real tokens, private keys, credentials, personal values, or /erp production values were written

## 12. remaining_risks

- /erp remains dirty outside this harness task, including untracked /erp/kanban/prompts/HERMES_OPEN_SWE_HARNESS_20260521.md; not touched here.
- semgrep is missing locally and was not installed, per instruction.
- Open SWE local install is dependency-synced and test-passing, but no runtime server has been started.
- No GitHub App, webhook, ngrok tunnel, or testrepo wiring exists yet.
- .env.example includes placeholder names for sensitive settings; real .env creation must happen only in an isolated testrepo/dev context with human approval.
- UI has both yarn.lock and bun.lock, but no packageManager field. Yarn v1 audit passed; npm audit was intentionally not run because there is no package-lock.json.

## 13. exact_next_step

Recommended next prompt:

"Fortsätt i /home/olle/northstar-agent-harness/open-swe-hermes på branch northstar/harness-audit. Skapa en rent lokal testrepo-only runtime readiness slice utan webhooks: lägg till hermes_review_contract.py och hermes_review_router.py som pure functions, plus pytest för dummy VAT/review-next scenario. Kör uv run pytest -vvv tests/test_hermes_review_router.py, uv audit, ruff check/format --check. Starta inte server, ingen GitHub App, ingen webhook, ingen ngrok, ingen push/PR. Rapportera evidence först och sluta med GATE."

## 14. GATE

GATE=WARN
GATE_REASON=Local dev bootstrap succeeded: uv sync passed, Python 3.13.13 is used, pytest passed 336/336, uv audit passed, ruff passed after formatting one harness-only pseudo-router file, yarn audit passed, gitleaks/trufflehog/osv-scanner found no issues, and .env.example contains placeholders only. WARN remains because semgrep is missing and /erp is still dirty with unrelated external untracked files including /erp/kanban/prompts/HERMES_OPEN_SWE_HARNESS_20260521.md, which was not touched.
