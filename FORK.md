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
| 1 | `SANDBOX_FACTORIES` entry for the Docker sandbox backend | the `SANDBOX_FACTORIES` dict in `agent/utils/sandbox.py` |
| 2 | `SpeedbayConventionsMiddleware` (and future gate middleware) in the `middleware=[...]` list | the list inside `get_agent()` in `agent/server.py`, plus its direct import above |

Identified by symbol, not `file:line` — the middleware list moved from :946 to :953 on the very first upstream merge.

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

## Speed Bay org layer

Everything we add lives in files upstream does not own:

| Path | Purpose |
|---|---|
| `speedbay/mint_token.py` | Mints a GitHub App installation token on demand |
| `speedbay/bin/gh` | `gh` shim; replaces the hardcoded `GH_TOKEN=dummy` with a real token |
| `speedbay/bin/git-credential-openswe` | Git credential helper for `github.com` |
| `speedbay/gitconfig` | Registers the credential helper; sets the bot git identity |
| `speedbay/githooks/commit-msg` | Strips AI-attribution trailers from every commit |
| `speedbay/run-dev.sh` | **Start the backend with this**, not bare `langgraph dev` |
| `speedbay/set_model.py` | Reads/sets the agent's default model (no dashboard needed) |
| `speedbay/create_linear_webhook.py` | Creates/lists the Linear trigger webhooks (needs a temp admin key) |
| `agent/middleware/speedbay_conventions.py` | Appends warehouse's commit/PR contract to the system prompt |

### Why the local sandbox needs all this

The agent never holds a GitHub token: upstream's prompts hardcode
`GH_TOKEN=dummy` and expect a **sandbox proxy** to swap in a real one. That proxy
is LangSmith-only — `refresh_proxy_token` in `agent/utils/github_proxy.py`
returns early unless `SANDBOX_TYPE=langsmith`, and imports
`_configure_github_proxy` from `agent/integrations/langsmith.py`. So with **any**
other backend (local, docker, e2b, daytona, modal) every `git`/`gh` call fails
with 401 out of the box.

`speedbay/run-dev.sh` replaces the proxy with host-side credentials. The `local`
backend runs commands with the parent process's environment
(`LocalShellBackend(..., inherit_env=True)`), so exporting `PATH`,
`GIT_CONFIG_GLOBAL` and `core.hooksPath` is enough — no upstream code changes.

**When the Docker backend lands, it must solve this again**: containers do not
inherit the host environment, so the token has to be injected into the container
(proxy, credential helper, or mounted helper script).

### Bot-token-only mode requires a LangSmith placeholder

Counter-intuitively, running **without** LangSmith requires setting
`LANGSMITH_API_KEY_PROD`. `is_bot_token_only_mode()` in `agent/utils/auth.py` is
`LANGSMITH_API_KEY and not X_SERVICE_AUTH_JWT_SECRET and not USER_ID_API_KEY_MAP`;
with the key empty the code takes the per-user OAuth path instead and dies with
`No ls_user_id found from email ...`. The variable is fork-local (no installed
package reads it) and on the bot path is used only as a flag, so a placeholder
value is enough. `LANGSMITH_ENDPOINT_PROD` / `LANGSMITH_URL_PROD` point at
`http://127.0.0.1:9` so the other five call sites that would build a real
LangSmith client fail locally instead of reaching the network.

### Choosing the model

`LLM_MODEL_ID` in `.env` does **not** select the runtime model — it is read only
by `validate_local_dev_llm_config` as a boot-time credential check. The real
precedence is per-thread override -> user profile -> **team default**, stored in
the LangGraph Store (`team_settings` / `default`). Use:

```bash
speedbay/set_model.py                                            # show current
speedbay/set_model.py --list                                     # options
speedbay/set_model.py fireworks:accounts/fireworks/models/kimi-k3
```

Settings live in the Store, so they survive restarts but not a Store wipe.

## Upstream deviations (re-check after every merge)

Two upstream-owned files carry edits. Both are marked in-code with
`SPEEDBAY DEVIATION` / `SPEEDBAY REGISTRATION` comments.

| File | Edit | Why not elsewhere |
|---|---|---|
| `agent/server.py` | Import + one entry in the `get_agent()` middleware list | Sanctioned registration point; no alternative seam |
| `agent/dashboard/options.py` | `kimi-k3-code` -> `kimi-k3` in `SUPPORTED_MODELS` and `DEPRECATED_MODEL_REPLACEMENTS` | Upstream ships a model id that does not exist on Fireworks (404 from the platform API). `SUPPORTED_MODEL_IDS` gates model selection, so it cannot be fixed from config. **File upstream so this deviation disappears.** |
| `agent/utils/linear_team_repo_map.py` | Upstream's own workspace mapping replaced with an empty dict | Docs designate this file as deployer config. Our Linear team "Open SWE" collided with upstream's entry of the same name and routed to `langchain-ai/open-swe`, which the allowlist rejected. Empty mapping falls back to `DEFAULT_REPO_OWNER`/`DEFAULT_REPO_NAME` (`speedbay/warehouse`); per-comment `repo:owner/name` still overrides. |

Deliberately **not** patched, to keep the merge surface small:

- `agent/prompt.py` — upstream's PR/commit format instructions conflict with
  warehouse's contract, but that file takes ~70 commits per 90 days. Overriding
  it via `SpeedbayConventionsMiddleware` costs nothing at merge time.
- `agent/utils/authorship.py` — attribution is stripped by the `commit-msg` hook
  rather than by editing the footer/trailer helpers.

## Linear trigger

A `@openswe` comment on a Linear ticket triggers a run. Setup facts that cost a
night to learn:

- **Linear's webhook UI no longer accepts a user-supplied secret** — it
  generates a `lin_wh_...` value. Upstream's docs (and this fork's `.env`
  layout) assume we choose the secret, so webhooks are created **via the API**
  with `speedbay/create_linear_webhook.py`, passing the `LINEAR_WEBHOOK_SECRET`
  from `.env`. A UI-created webhook's generated secret never verified against
  our HMAC check; the API-created webhook with our own secret verified
  immediately.
- **Only workspace admins can manage webhooks.** The forge-bot runtime key gets
  `Invalid role: admin required`. Use a temporary admin key
  (`LINEAR_ADMIN_KEY` env var), then revoke it. Never store it.
- **`allPublicTeams: true` does not cover private teams.** Each private Linear
  team needs its own webhook (`--team KEY`). Two webhooks currently exist: one
  for all public teams, one for the private team OPE. Both share the same
  secret and URL.
- **API-authored comments do fire the webhook**, and arrive with
  `botActor: null` — the route's bot filter does not catch comments posted with
  a plain API key (e.g. forge-bot). Loop protection rests on the `@openswe`
  mention requirement and the agent's known reply prefixes, so agent replies
  must never contain `@openswe`.
- The runtime `LINEAR_API_KEY` is a forge-bot service-account key: agent
  comments on tickets are attributed to `forge-bot@speedbay.com`, and the key
  is revocable without touching anyone's personal access.
- **Sandbox runs can mutate `speedbay/gitconfig`**: an agent once ran
  `gh auth setup-git`, which rewrote the credential helper in the file
  `GIT_CONFIG_GLOBAL` points at (routing pushes through the host's gh auth).
  The file is kept read-only (`chmod 444`) to fail such attempts loudly; if it
  shows up modified, restore it from git.

## Known issues

- **Studio graph preview 500s** — `langgraph-api` 0.10.3 substitutes
  `langgraph_sdk.runtime._ReadRuntime`, which has no `override()`; `langgraph`
  1.2.8 calls it (`langgraph/pregel/_algo.py:691`). Upgrade path:
  `langgraph-api>=0.11.1`. `_ExecutionRuntime` lacks `override()` too, so real
  agent runs may hit this as well — unverified.
