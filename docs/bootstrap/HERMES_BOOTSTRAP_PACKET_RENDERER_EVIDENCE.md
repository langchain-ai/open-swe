# Hermes Bootstrap Packet Renderer Evidence

Verification evidence:
- `pwd && git status --short --branch && git rev-parse HEAD && git branch --show-current && node /erp/kanban/agent-control.mjs status` from `/home/olle/northstar-agent-harness/open-swe-hermes` -> cwd correct, branch `northstar/harness-audit`, HEAD `faa27479c3b67ce65ba772cc6912a6b923540bfc`, `/erp` agent-control active=0/stale=0.
- `uv run pytest -q tests/test_hermes_bootstrap_packet_renderer.py` -> RED first: failed with `ModuleNotFoundError: No module named 'docs.hermes.pseudo_router.bootstrap_packet_renderer'` before implementation.
- `uv run pytest -q tests/test_hermes_bootstrap_packet_renderer.py` after implementation -> `4 passed, 1 warning`.
- `uv run python docs/hermes/pseudo_router/dry_run_pipeline.py docs/hermes/examples/github_review_pipeline_input.example.json > /tmp/hermes_pipeline_result.json && uv run python docs/hermes/pseudo_router/testrepo_bootstrap_approval_gate.py /tmp/hermes_pipeline_result.json docs/hermes/examples/testrepo_bootstrap_profile.example.json > docs/hermes/examples/testrepo_bootstrap_approval_packet.example.json && uv run python docs/hermes/pseudo_router/bootstrap_packet_renderer.py docs/hermes/examples/testrepo_bootstrap_approval_packet.example.json > /tmp/hermes_bootstrap_packet.md` -> rendered file-only Markdown packet; no server/webhook/GitHub App/push/PR.
- `uv run pytest -q tests/test_hermes_bootstrap_packet_renderer.py tests/test_hermes_testrepo_bootstrap_approval_gate.py tests/test_hermes_dry_run_pipeline.py && uv run ruff format --check docs/hermes/pseudo_router/bootstrap_packet_renderer.py tests/test_hermes_bootstrap_packet_renderer.py && uv run ruff check docs/hermes/pseudo_router/bootstrap_packet_renderer.py tests/test_hermes_bootstrap_packet_renderer.py` -> `15 passed, 1 warning`; `2 files already formatted`; `All checks passed!`.
- `uv run pytest -q tests/` -> `375 passed, 8 warnings in 135.32s`.
- `uv audit` -> no known vulnerabilities in 147 packages.
- `gitleaks detect --no-git --redact --exit-code 1` -> no leaks found.
- `trufflehog filesystem . --no-update --only-verified --fail` -> `verified_secrets: 0`, `unverified_secrets: 0`.
- `osv-scanner scan source -r .` -> `No issues found`.
- `yarn audit --level moderate` in `ui/` -> `0 vulnerabilities found - Packages audited: 756`.
- `sha256sum docs/hermes/pseudo_router/bootstrap_packet_renderer.py tests/test_hermes_bootstrap_packet_renderer.py docs/hermes/examples/testrepo_bootstrap_approval_packet.example.json /tmp/hermes_bootstrap_packet.md` ->
  - `334df34d729fbb57e31e1d77e16e0ee645f9103c1d404ecfb5e2632679231391  docs/hermes/pseudo_router/bootstrap_packet_renderer.py`
  - `dd1119c642c78f2341ad0a8f62683a13f0232e41247dbcc343223ebccfbb8243  tests/test_hermes_bootstrap_packet_renderer.py`
  - `662d8872db103a9bdb01f7109b2b2bb7ba6fd91c8a22aeefdc44f104730f735e  docs/hermes/examples/testrepo_bootstrap_approval_packet.example.json`
  - `7ad7ea70570acc167f889abb84a0121a7d34132214f85488bb2803bc3b7106e4  /tmp/hermes_bootstrap_packet.md`

## What changed

Created a pure, side-effect-free renderer for the testrepo bootstrap approval gate:

- `docs/hermes/pseudo_router/bootstrap_packet_renderer.py`
  - Renders approval-gate JSON into Markdown.
  - Returns structured metadata: status, schema version, gate, recommended filename, Markdown, side_effects=[].
  - Blocks unsafe input packets that already claim side effects.
  - Blocks approval-phrase emission for blocked packets.
  - CLI prints Markdown to stdout only.
- `tests/test_hermes_bootstrap_packet_renderer.py`
  - Covers safe READY_FOR_HUMAN_APPROVAL rendering.
  - Covers blocked Northstar repo packet rendering.
  - Covers side-effect contamination rejection.
  - Covers CLI rendering from JSON.
- `docs/hermes/examples/testrepo_bootstrap_approval_packet.example.json`
  - Fixture generated from existing dry-run pipeline + approval gate.

## Explicitly not done

- Did not edit `/erp` product code.
- Did not create GitHub App.
- Did not configure webhook.
- Did not start Open SWE server.
- Did not build Docker snapshot.
- Did not push or open PR.
- Did not run production deploy.
- Did not read or print secrets/.env/private keys/tokens.

## Recommended next prompt

If you want the next safe slice, use:

`Kör nästa harness-slice: skapa en dry-run bootstrap packet writer som endast skriver den renderade Markdown-filen under docs/bootstrap/ i harness-kopian, kräver redan-renderad packet JSON, blockerar absoluta sökvägar och /erp, och verifiera med RED-GREEN tests + full scanners. Ingen GitHub App, webhook, serverstart, push eller prod-install.`

GATE=PASS
GATE_REASON=File-only bootstrap packet renderer is implemented and verified with RED-first tests, targeted/full pytest, ruff, uv/yarn audits, gitleaks, trufflehog, and osv-scanner; no external side effects or /erp product edits were performed.
