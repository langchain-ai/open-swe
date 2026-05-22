# Open SWE Upstream Record

Generated: 2026-05-21T21:10:02+02:00

- Upstream repository: https://github.com/langchain-ai/open-swe
- Local clone: /home/olle/northstar-agent-harness/open-swe-hermes
- Local branch: northstar/harness-audit
- Pinned commit: faa27479c3b67ce65ba772cc6912a6b923540bfc
- Remote HEAD verified by `git ls-remote https://github.com/langchain-ai/open-swe HEAD`: faa27479c3b67ce65ba772cc6912a6b923540bfc
- License: MIT (`pyproject.toml` license text is MIT; `LICENSE` begins with: The MIT License / `)

## Local intent

This clone is a safety/integration audit workspace for Hermes Northstar Agent Harness. It must not edit `/erp` production code and must not enable production webhooks, GitHub Apps, public tunnels, Docker snapshot builds, or GitHub pushes without separate human approval.

## Planned local change areas

- Documentation under `docs/` only during this audit phase.
- Northstar AGENTS template under `docs/templates/`.
- Future code changes, if approved separately, should be limited to harness safety gates, allowlists, trigger filtering, sandbox adapters, deterministic middleware, and branding/configuration for Northstar.

## Python runtime note

Upstream `INSTALLATION.md` states Python 3.11-3.13 is required; Python 3.14 is explicitly not supported for dependency install. This host's default `python3` is 3.14.4, so any later install must use uv-managed CPython 3.12/3.13 or another explicit supported interpreter. No dependency installation was performed in this phase.
