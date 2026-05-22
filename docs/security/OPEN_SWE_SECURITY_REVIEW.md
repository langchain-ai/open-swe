# Open SWE Initial Security Review

Generated: 2026-05-21T21:10:02+02:00
Commit: faa27479c3b67ce65ba772cc6912a6b923540bfc
Method: static source review plus available audit commands. No install, server start, webhook, GitHub App, Docker build, push, or production deploy was performed.

## Summary

Overall status for Northstar autonomous use: YELLOW/RED until a Northstar-specific safety fork adds strict allowlists, trigger-user allowlists, sandbox policy, deterministic post-run gates, secret scanning, and reviewer-required final gate.

## Area classification

| Area | Status | Findings | Required Northstar action |
|---|---|---|---|
| GitHub App/OAuth permissions and token flows | YELLOW | GitHub App model uses Contents/Pull requests/Issues write permissions; optional per-user OAuth tokens are encrypted in metadata with `TOKEN_ENCRYPTION_KEY`; installation token is used for sandbox proxy. Powerful but expected. | Least privilege repo-only install; explicit trigger-user allowlist; token encryption configured and rotated; no shared bot-only mode for high-risk writes unless approved. |
| Webhook signature checks | GREEN/YELLOW | GitHub, Slack, Linear signature verification exists and missing secret rejects requests. | Keep enabled; add replay/timestamp checks where upstream lacks them; never expose via ngrok/prod until reviewed. |
| Repo/user allowlists | YELLOW | Repo allowlist exists but empty means allow all. Public repo org gate exists. Trigger-user control currently maps GitHub username to email and dashboard profile logic. | Default deny: `ALLOWED_GITHUB_REPOS=ollehillbom1/north-star-erp`; explicit user allowlist starting `ollehillbom1`; no org-wide default. |
| Sandbox model: langsmith/daytona/runloop/modal/local | YELLOW/RED | Cloud/container providers exist; `local` executes on host without isolation. | Autonomous runs require cloud/container sandbox; `local` is RED except manual dev. |
| Tools: execute/file/deepagents built-ins | YELLOW | Deep Agents backend supplies shell and file operations in sandbox. | Keep only inside isolated sandbox with worktree/repo scope and deterministic post-run gates. |
| Tools: fetch_url/http_request/web_search | YELLOW/RED | Egress-capable. `http_request` and `fetch_url` validate public HTTP(S), block private/loopback/reserved IPs, pin DNS and validate redirects, but still enable data exfiltration to public internet. | Disable by default for coding-runs; enable per-task only with allowlisted destinations. |
| Tools: GitHub gh | YELLOW | Agent uses `GH_TOKEN=dummy gh` through sandbox proxy. Can push/PR if prompt permits. | Wrap with controlled gh-proxy; require branch naming, draft PR, pr:doctor, reviewer gate, no direct main. |
| Tools: Linear/Slack | RED initially | Can read/post/update external systems; Linear includes create/update/delete issue. | Disable until separate decision and scoped tokens. |
| Reviewer tools | YELLOW | Reviewer can fetch diffs and publish reviews/findings. | Good fit as mandatory independent reviewer, but must be bounded to PR and no self-approval. |
| Data egress | RED until gated | Slack/Linear/GitHub/http/fetch/search all can move data externally. | Egress allowlist + secret redaction + no raw logs of secrets. |
| Filesystem and credential risk | YELLOW/RED | Sandbox file ops are intended, but `local` provider risks host FS. `langgraph.json` loads `.env`. | No host local autonomy; block `.env`, private key, token, credential paths; post-run secret scan. |
| Dockerfile external sources | YELLOW/RED | apt, Docker repo, GitHub gh deb, uv tarball with checksum, NodeSource setup script piped to bash, Go tarball without checksum. | Do not run blindly; pin/checksum every remote artifact; replace curl|bash; use supported Python 3.12/3.13. |
| Dependency risk | YELLOW | Python `uv audit` clean in this environment; npm audit blocked by no npm lockfile. Large modern dependency set incl. LangGraph/DeepAgents/LangSmith, sandbox providers, Exa. | Lockfile review; OSV/audit in CI/local gate; no install until bootstrap approval. |

## Scanner/audit status

- gitleaks: PASS after adding project-local `.gitleaks.toml` that extends the default rules, excludes local dependency/build folders such as `.venv/`, and allowlists only known upstream documentation/test fixtures. Command: `gitleaks detect --redact --source . --no-git --verbose`; result: no leaks found.
- trufflehog: PASS. Command: `trufflehog filesystem --no-update --only-verified --fail .`; result: `verified_secrets=0`, `unverified_secrets=0`.
- osv-scanner: PASS. Command: `osv-scanner scan source .`; result: no issues found.
- uv audit: PASS. Command: `uv audit --preview-features audit`; result: no known vulnerabilities and no adverse project statuses in 147 packages.
- grype: PASS. Command: `grype dir:. -q`; result: no vulnerabilities found.
- syft: PASS in local smoke. Command: `syft dir:. -q -o table`; result: SBOM output works.
- npm audit (ui): exit=1; { |   "error": { |     "code": "ENOLOCK", |     "summary": "This command requires an existing lockfile.", |     "detail": "Try creating one first with: npm i --package-lock-only\nOriginal error: loadVirtual requires existing shrinkwrap file" |   } | } | npm warn Unknown builtin config "globalignorefile". This will stop working in the next major version of npm. See `npm help npmrc` for supported config options. | npm error code ENOLOCK | npm error audit This command requires an existing lockfile. | npm error audit Try creating one first with: npm i --package-lock-only | npm error audit Original error: loadVirtual requires existing shrinkwrap file | npm error A complete log of this run can be found in: /home/olle/.npm/_logs/2026-05-21T19_10_04_214Z-debug-0.log


Scanner caveat: npm audit still needs an npm lockfile before it can be useful. The Open SWE Python dependency audit should remain `uv audit` unless/until the upstream install path changes.

## Notable positives

- Webhook secrets are required; missing secret rejects.
- `http_request`/`fetch_url` include SSRF defenses: only http/https, DNS resolve/validate, private/loopback/link-local/reserved IP block, redirect validation, DNS pinning to reduce rebinding risk.
- Token encryption supports rotation.
- LangSmith sandbox proxy avoids storing real GitHub tokens inside sandbox command env.
- Existing tests include `test_http_security.py`, `test_sanitize_tool_inputs.py`, token TTL/OAuth tests, and sandbox config tests.

## Critical stop conditions for Northstar

- Any discovered secret, `.env`, private key, token, or credential in prompt/log/output.
- Target repo not exactly allowlisted.
- Trigger user not exactly allowlisted.
- `SANDBOX_TYPE=local` for autonomous run.
- Dirty scope or attempted edit outside sandbox/worktree.
- Migration without module wiring, tenant-connection, provisioning, and tenant migration evidence.
- Missing `npm run -s pr:doctor` for PR work.
- Missing independent reviewer evidence.
- Runtime/live claim without command evidence.
