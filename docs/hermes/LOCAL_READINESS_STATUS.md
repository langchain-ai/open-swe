# Local Readiness Status

Generated: 2026-05-21 21:45 Europe/Stockholm

## Current good state

- CPython 3.14.5 is installed side-by-side at `/home/olle/.local/bin/python3.14.5`; `/usr/bin/python3` remains Ubuntu's Python 3.14.4.
- The 3.14.5 full module smoke passes for `ssl`, `zlib`, `bz2`, `lzma`, `readline`, `curses`, `tkinter`, `sqlite3`, `ctypes`, and `hashlib`.
- Open SWE runtime venv is separate and uses uv-managed Python 3.13.13 at `/home/olle/northstar-agent-harness/open-swe-python-runtime-check/.venv`.
- Host capacity is currently healthy: disk has large headroom, swap is 99 GiB with 0 used, and inotify limits match the Northstar large-workspace baseline.
- Installed audit tools now include `gitleaks`, `trufflehog`, `osv-scanner`, `pip-audit`, `syft`, and `grype`.
- `scripts/northstar_local_readiness.sh` is executable and should run before harness bootstrap, Docker image work, webhook setup, or GitHub App setup.
- Latest readiness run: `NORTHSTAR_HARNESS_READINESS_GATE=PASS`.
- Security smoke on the Open SWE clone:
  - `gitleaks detect --redact --source . --no-git --verbose` -> PASS, no leaks found, using `.gitleaks.toml` with source-focused excludes for local dependency/build folders.
  - `trufflehog filesystem --no-update --only-verified --fail .` -> PASS, 0 verified secrets.
  - `osv-scanner scan source .` -> PASS, no issues.
  - `uv audit --preview-features audit` -> PASS, no known vulnerabilities.
  - `grype dir:. -q` -> PASS, no vulnerabilities found.
  - `syft dir:. -q -o table` -> PASS, SBOM output works.
- `gh auth status` succeeds locally.
- Hermes env now has `GITHUB_TOKEN` present in `~/.hermes/.env`; file mode is `0600`. Token value was not printed.
- The `olle` account is now in the `docker` group. A fresh `sudo -u olle` login-context check shows `docker info` returns 0.
- `/home/olle/.local/bin/docker` is a local wrapper that keeps current long-running sessions usable before supplementary groups refresh. In refreshed sessions it delegates directly to `/usr/bin/docker`; in old sessions it uses a same-user fresh-login bridge.
- Current session verification: `command -v docker` -> `/home/olle/.local/bin/docker`; `docker ps` returns live containers.
- `/erp` agent-control latest readiness run reports `active=0`, `edit=0`, `stale=0`.

## Known remaining friction

- npm audit cannot run usefully until an npm lockfile exists for the scanned UI/package scope.
- `/erp` may have active observe/edit agents at any given readiness run; treat `agent-control active>0` as a scope-collision warning, not a harness blocker unless scope overlaps.

## Recommended next local adaptations

1. Wire `scripts/northstar_local_readiness.sh` into the Hermes bootstrap prompt/runbook as the first local preflight.
2. Add an npm-lock-aware audit path only if the Open SWE UI/package scope gets a lockfile.
3. Keep Open SWE installs pinned to Python 3.13.13 until upstream explicitly supports Python 3.14.
