# Speed Bay fork of Open SWE

Fork of [langchain-ai/open-swe](https://github.com/langchain-ai/open-swe) (MIT).
This repo is our **deployment repo**: Speed Bay customizations live in files
upstream does not touch, so pulling upstream improvements stays a clean merge.

Upstream `docs/INSTALLATION.md` remains authoritative for install steps. This
file is authoritative for **fork conventions only**.

## Upstream sync

```bash
git fetch upstream
git merge upstream/main
```

Remotes: `origin` → `speedbay/open-swe`, `upstream` → `langchain-ai/open-swe`.

### Re-check after every merge

Our customizations are new files, but each must be **registered** in an
upstream-owned file. These two lines are the entire merge surface — verify both
survived, and re-add them if a merge dropped them:

| # | Registration | Location |
|---|---|---|
| 1 | `SANDBOX_FACTORIES` entry for the Docker sandbox backend | `agent/utils/sandbox.py:12` |
| 2 | Gate middleware entry in the `middleware=[...]` list in `get_agent()` | `agent/server.py:946` (imports at `agent/server.py:69`) |

Both are upstream's documented extension points (`docs/CUSTOMIZATION.md` §6),
which is why they merge cleanly.

Note: `validate_sandbox_startup_config()` (`agent/utils/sandbox.py:65`) validates
**only** `SANDBOX_TYPE=langsmith`. A Docker backend gets no startup validation
unless we add it there.

## File placement rule

Speed Bay code goes in **new files** under paths upstream doesn't own
(e.g. `agent/integrations/docker.py`, `agent/middleware/<our_gate>.py`).
Never edit upstream logic in place — register, don't modify.

**Documented exception:** dependency pins require editing upstream
`pyproject.toml`. Keep such edits to a single minimal line and expect to
re-apply them on merge.

## Local boot (credential-free)

There is no `.env.example` upstream. Regenerate `.env` from
`docs/INSTALLATION.md` §6. Three values must deviate from that block for a
credential-free local boot:

| Var | Value | Why |
|---|---|---|
| `LANGCHAIN_TRACING_V2` | `"false"` or absent — **never `""`** | starlette casts it to bool; `""` raises `ValueError` before any app code runs |
| `SANDBOX_TYPE` | `"local"` | default is `langsmith`, which is fail-closed without `DEFAULT_SANDBOX_SNAPSHOT_ID`. `local` has no isolation — don't leave it set once real credentials exist |
| `DASHBOARD_BASE_URL` | `""` | any `http://localhost*` value turns on the local-dev LLM key check (`agent/utils/model.py:295`), which requires a key for the default model |

Both boot validators run in the FastAPI lifespan at `agent/api/app.py:24-25`.

Verify a boot:

```bash
langgraph dev
curl -s http://localhost:2024/health    # {"status":"healthy"}
```

`DASHBOARD_ALLOWED_ORIGINS` must also be empty (or include
`https://smith.langchain.com`) for LangGraph Studio to connect — a non-matching
value makes `agent/api/app.py:44` reject Studio's CORS preflight, which the
browser reports only as "Failed to fetch". Both dashboard vars come back
together when the dashboard is configured.

## Webhook tunnel

The backend runs on a laptop, so inbound webhooks arrive through a **named
Cloudflare Tunnel** on the `speedbay.com` zone. This is the canonical URL for
the GitHub App (OPE-3) and the Linear trigger (OPE-5):

| | |
|---|---|
| Public base URL | `https://openswe.speedbay.com` |
| GitHub webhook path | `https://openswe.speedbay.com/webhooks/github` |
| Tunnel name / id | `openswe` / `66d09a43-7dac-4001-9adb-b6df1806796d` |
| Local target | `http://localhost:2024` |

Start it alongside `langgraph dev` (separate terminal):

```bash
cloudflared tunnel run --url http://localhost:2024 openswe
```

Verify the public path end to end — `cf-ray` proves the request traversed
Cloudflare rather than looping back through localhost:

```bash
curl -s -D- https://openswe.speedbay.com/health | grep -i 'HTTP/\|cf-ray'
```

A stopped tunnel returns Cloudflare `530`. Restarting reuses the same hostname
and tunnel id with no reconfiguration, so the GitHub/Linear webhook settings
never need editing.

### Per-machine setup

`cloudflared tunnel login` writes `~/.cloudflared/cert.pem`, and
`tunnel create` writes `~/.cloudflared/<UUID>.json`. **Both are secrets and
neither is committed.** A second dev runs `cloudflared tunnel login` against the
`speedbay.com` zone and either reuses this tunnel (copy its credentials file via
a password manager) or creates their own with a distinct hostname.

If a freshly created hostname fails to resolve locally while working fine from
`dig @1.1.1.1`, the local resolver cached an NXDOMAIN from a pre-creation
lookup: `sudo dscacheutil -flushcache && sudo killall -HUP mDNSResponder`.

### Do not install as a service

Do **not** run `cloudflared service install` until the Docker sandbox backend
lands (OPE-7). A tunnel started at boot means a permanently public path into a
host running `SANDBOX_TYPE=local`, which executes agent shell commands directly
on the machine with the operator's environment inherited
(`agent/integrations/local.py:7`). Start the tunnel when working, stop it after.

### Why Cloudflare rather than ngrok

Measured `warehouse` activity is ~547 PRs/month at ~18 check runs per head SHA
and 1.7 commits per PR, i.e. **~35k webhook events/month** once the GitHub App
subscribes to `check_run`. ngrok's free tier caps at 20k requests/month and
cannot reserve a chosen subdomain, while a Cloudflare named tunnel on a zone we
already own has no request cap, a hostname we pick, and no interstitial page.

## Known issues

- **Studio graph preview 500s** — `langgraph-api` 0.10.3 substitutes
  `langgraph_sdk.runtime._ReadRuntime`, which has no `override()`; `langgraph`
  1.2.8 calls it (`langgraph/pregel/_algo.py:691`). Upgrade path:
  `langgraph-api>=0.11.1`. `_ExecutionRuntime` lacks `override()` too, so real
  agent runs may hit this as well — unverified.
