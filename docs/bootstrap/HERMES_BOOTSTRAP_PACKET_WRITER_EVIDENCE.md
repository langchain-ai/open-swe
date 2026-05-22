# Hermes Bootstrap Packet Writer Evidence

Verification evidence:
- `pwd && git status --short --branch && git rev-parse HEAD && git branch --show-current && node /erp/kanban/agent-control.mjs status` from `/home/olle/northstar-agent-harness/open-swe-hermes` -> cwd correct, branch `northstar/harness-audit`, HEAD `faa27479c3b67ce65ba772cc6912a6b923540bfc`, `/erp` agent-control active=0/stale=0.
- `uv run pytest -q tests/test_hermes_bootstrap_packet_writer.py` -> RED first: failed with `ModuleNotFoundError: No module named 'docs.hermes.pseudo_router.bootstrap_packet_writer'` before implementation.
- `uv run pytest -q tests/test_hermes_bootstrap_packet_writer.py` after implementation -> `6 passed, 1 warning`.
- CLI dry-run writer chain:
  - `uv run python docs/hermes/pseudo_router/bootstrap_packet_renderer.py docs/hermes/examples/testrepo_bootstrap_approval_packet.example.json > /tmp/hermes_rendered_bootstrap_packet.md`
  - `python3 - <<'PY' ... render_bootstrap_packet_markdown(...) -> docs/hermes/examples/testrepo_bootstrap_rendered_packet.example.json`
  - `uv run python docs/hermes/pseudo_router/bootstrap_packet_writer.py docs/hermes/examples/testrepo_bootstrap_rendered_packet.example.json` -> `status=WROTE_DRY_RUN_PACKET`, `relative_path=docs/bootstrap/BOOTSTRAP_PACKET_GH-REVIEW-42-9001.md`, `side_effects=["wrote_markdown_file"]`.
- `uv run pytest -q tests/test_hermes_bootstrap_packet_writer.py tests/test_hermes_bootstrap_packet_renderer.py tests/test_hermes_testrepo_bootstrap_approval_gate.py tests/test_hermes_dry_run_pipeline.py && uv run ruff format docs/hermes/pseudo_router/bootstrap_packet_writer.py tests/test_hermes_bootstrap_packet_writer.py && uv run ruff check docs/hermes/pseudo_router/bootstrap_packet_writer.py tests/test_hermes_bootstrap_packet_writer.py` -> `21 passed, 1 warning`; `1 file reformatted, 1 file left unchanged`; `All checks passed!`.
- `uv run pytest -q tests/` -> `381 passed, 8 warnings in 134.64s`.
- `uv audit` -> no known vulnerabilities in 147 packages.
- `gitleaks detect --no-git --redact --exit-code 1` -> no leaks found.
- `trufflehog filesystem . --no-update --only-verified --fail` -> `verified_secrets: 0`, `unverified_secrets: 0`.
- `osv-scanner scan source -r .` -> `No issues found`.
- `yarn audit --level moderate` in `ui/` -> `0 vulnerabilities found - Packages audited: 756`.
- `sha256sum docs/hermes/pseudo_router/bootstrap_packet_writer.py tests/test_hermes_bootstrap_packet_writer.py docs/hermes/examples/testrepo_bootstrap_rendered_packet.example.json docs/bootstrap/BOOTSTRAP_PACKET_GH-REVIEW-42-9001.md` ->
  - `2ca3bafcc489234b415d7e6a84b1b4d7ab57a00e1179e6ca1bdc494f281358bb  docs/hermes/pseudo_router/bootstrap_packet_writer.py`
  - `a7364dc6fc39131e209cb6ec3ac3f860a3036934e2253015d83bf2a4fd781eb7  tests/test_hermes_bootstrap_packet_writer.py`
  - `760d5bc5be3ca51359365e4d5143b155f79d2b372edde051bdd36bc4a37f91c6  docs/hermes/examples/testrepo_bootstrap_rendered_packet.example.json`
  - `7ad7ea70570acc167f889abb84a0121a7d34132214f85488bb2803bc3b7106e4  docs/bootstrap/BOOTSTRAP_PACKET_GH-REVIEW-42-9001.md`

## What changed

Created a dry-run writer for already-rendered bootstrap packet JSON:

- `docs/hermes/pseudo_router/bootstrap_packet_writer.py`
  - Requires `schema_version=hermes.bootstrap-packet-renderer.v1` and non-empty `markdown`.
  - Writes only to a relative `.md` path under `docs/bootstrap/` below the current harness root.
  - Blocks absolute paths.
  - Blocks parent escapes such as `../erp/...` and `docs/../...`.
  - Has explicit `/erp` guard even after path resolution.
  - CLI prints JSON result only.
- `tests/test_hermes_bootstrap_packet_writer.py`
  - Verifies successful write under `docs/bootstrap/`.
  - Verifies approval-gate JSON is rejected because it is not rendered packet JSON.
  - Verifies absolute path and `/erp`/parent escape blocking.
  - Verifies non-Markdown and missing output filename blocking.
  - Verifies CLI writes inside current harness root.
- `docs/hermes/examples/testrepo_bootstrap_rendered_packet.example.json`
  - Rendered packet fixture consumed by the writer.
- `docs/bootstrap/BOOTSTRAP_PACKET_GH-REVIEW-42-9001.md`
  - Actual dry-run Markdown packet written by the new writer under harness docs/bootstrap.

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

`Kör nästa harness-slice: skapa en dry-run bootstrap execution manifest som läser packet-writer-resultatet och producerar en human approval checklist utan att skapa GitHub App/webhook/server/push. Den ska kräva writer-resultat status=WROTE_DRY_RUN_PACKET, verifiera att path ligger under docs/bootstrap, och lista exakta manuella steg som fortfarande kräver ALLOW_BOOTSTRAP_INSTALL=YES. RED-GREEN tests + full scanners.`

GATE=PASS
GATE_REASON=Dry-run bootstrap packet writer is implemented and verified with RED-first tests, targeted/full pytest, ruff, uv/yarn audits, gitleaks, trufflehog, and osv-scanner; only the rendered Markdown file was written under harness docs/bootstrap, with no external side effects or /erp product edits.
