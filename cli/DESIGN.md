# Open SWE CLI — Final Design

A terminal client for Open SWE. Drives **local** agent runs (the existing coda flow, unchanged in spirit), creates and **attaches** to **cloud** agent runs hosted by an Open SWE LangGraph deployment, and supports **handoff** of an in-flight run between local and cloud sandboxes. Distributed as a standalone bun-compiled binary, gated behind GitHub-App-backed user OAuth + GitHub org membership.

The CLI lives in this `cli/` directory inside the open-swe monorepo. It has its own `package.json`, its own build, and its own CI job; the Python LangGraph server in `agent/` remains the source of truth for everything cloud-side.

---

## Identity and trust model

Open SWE is open-source but operated as a private deployment per org. The CLI must guarantee that **only members of the deployment's configured GitHub org** can list, create, attach to, or hand off runs on a given deployment.

The three existing entry surfaces (Slack, Linear, GitHub webhooks) are gated by the surface itself — to send a Slack mention you must be in the workspace; to comment on a Linear issue you must be in the org; to comment on a private repo PR you must have repo access. The CLI has no such ambient gate, so it adds an explicit one:

1. The CLI authenticates the user via **GitHub App user-to-server OAuth** against the *same* GitHub App that already runs the deployment's webhook + sandbox flows. No second App is required; the App simply has a CLI callback URL added to its "User authorization callback URL" list.
2. After the OAuth code exchange, the backend verifies the authenticated user is a member of the deployment's configured GitHub org (`ALLOWED_GITHUB_ORG`) using the App's installation token, via `GET /orgs/{org}/members/{username}`.
3. If the membership check passes, the backend issues a CLI session token bound to that GitHub user. If it fails, no token is issued and the OAuth response is discarded.
4. Org membership is **re-checked on every CLI request** (cached in-process for 60s) so a user who leaves the org loses CLI access on the next request after the cache expires, not whenever the cached session token would have expired.

The user-to-server token from GitHub is *only* used to identify the user during login; it is not stored long-term and is not used as the CLI's session token. The session token is a server-issued opaque bearer (HMAC-signed JWT, 30-day rotation) so the deployment can revoke it independently of GitHub.

### Why reuse the existing GitHub App

A GitHub App supports both *installation tokens* (server-to-server, scoped to repos the App is installed on — what `utils/github_app.py` already produces for sandbox git operations) and *user-to-server tokens* (identifies a specific user who authorized the App). These are distinct artifacts with distinct scopes. The CLI auth flow uses only the user-to-server side; sandbox creation continues to use the installation token side exactly as today. Sharing the App means forks set up one App, not two.

---

## Backend additions

All new routes live in `agent/webapp.py` under a `/cli/*` prefix. They are mounted on the same FastAPI app that serves webhooks today.

### Public (unauthenticated) routes

- `GET /cli/config` — returns the public config a CLI needs before login: `{github_app_client_id, allowed_org, server_version, supports_handoff}`. This is the only call the CLI makes before it has a session token. A fork's onboarding doc tells users to run `openswe login https://<their-deployment>`; the CLI hits `/cli/config` on that URL to learn the rest.
- `GET /cli/auth/start` — initiates GitHub OAuth. Returns the URL to redirect/open in the browser. CLI generates a PKCE verifier and a one-shot local listener; query string carries `code_challenge` and a CLI-side nonce.
- `GET /cli/auth/callback` — GitHub redirects here with `?code=...&state=...`. Backend exchanges code for a user-to-server access token, fetches `/user` to get the github login, verifies org membership, and issues a CLI session token. Renders a "you can close this tab" page. The session token is delivered to the CLI by the callback page POSTing back to the CLI's local listener with the PKCE proof.

### Authenticated routes (`Authorization: Bearer <session_token>`)

- `GET /cli/me` — returns `{github_login, email, org_membership_verified_at}`. Used by `openswe whoami` and by the CLI's auth-validity check on startup.
- `GET /cli/runs` — lists active runs for the authenticated user. Joins LangGraph threads to identity metadata stamped on each thread by the webhooks. A run is considered "the user's" if any of these match:
  - GitHub: thread metadata `github_login == user.github_login`.
  - Slack: thread metadata `slack_user_id` is in the user's mapped slack identities (resolved via `utils/github_user_email_map.py` extended with a slack-side mapping; see *Identity correlation* below).
  - Linear: thread metadata `linear_user_id` resolved similarly.
  - CLI: thread metadata `cli_owner_login == user.github_login` for runs the user created via `openswe new --cloud`.
  Response is `[{thread_id, source: 'github'|'slack'|'linear'|'cli', title, status: 'running'|'idle'|'completed'|'error', last_event_at, repo, branch, source_url}]`. Status is derived from LangGraph SDK's thread state plus a server-side liveness check (whether the message queue has unprocessed items or a run is currently executing).
- `POST /cli/runs` — creates a new cloud run. Body: `{repo, branch, prompt, model?, agent?}`. Backend creates a thread with `cli_owner_login` metadata set, stamps the same identity fields the webhooks stamp, and triggers a run via the LangGraph SDK exactly the way `_trigger_or_queue_run` does today. Returns `{thread_id}`. The CLI then attaches.
- `POST /cli/runs/{thread_id}/messages` — sends a chat message to a running thread. Internally calls `queue_message_for_thread(thread_id, message)`, which is the same primitive the Slack/Linear surfaces use to deliver mid-run user messages. The existing `check_message_queue_before_model` middleware will pick the message up on the next LLM iteration. Authorization: the requester's `github_login` must match `cli_owner_login`, or one of the source-side identity fields, on the thread's metadata.
- `GET /cli/runs/{thread_id}/stream` — server-sent events (SSE) proxy onto the LangGraph SDK's thread stream. Yields the same event shape the local `stream-processor.ts` already consumes. Implemented as a thin pass-through so the CLI's render layer is identical between local and cloud.
- `POST /cli/runs/{thread_id}/interrupt` — interrupts a running thread. Delegates to LangGraph SDK's interrupt.
- `POST /cli/runs/{thread_id}/handoff` — exports sandbox state for handoff. See *Handoff* below.
- `POST /cli/runs/{thread_id}/adopt` — imports a previously-exported local state into a new cloud thread. See *Handoff* below.

### Backend implementation notes

- Session tokens are HMAC-signed JWTs with `{sub: github_login, iat, exp}`, signed with a deployment-side `CLI_SESSION_SECRET`. 30-day expiry, rotated on every successful authenticated request whose token is older than 24h (sliding window). No refresh-token dance; the CLI just renews opportunistically.
- The CLI auth middleware lives at `agent/middleware/cli_auth.py` (FastAPI dependency, not LangGraph middleware) and is applied to every `/cli/*` route except the three public ones. It (a) verifies the JWT signature, (b) re-checks org membership with a 60s in-process cache, (c) attaches `request.state.cli_user` for handler use.
- Org-membership cache is keyed by `github_login` and stores `{verified_at, is_member}`. On miss it calls the GitHub API using the App installation token. On API failure the cached value (even if past TTL) is used to avoid lockout during GitHub outages — except if the cache says `is_member=false`, which fails closed.

---

## Identity correlation

`utils/github_user_email_map.py` already maintains a github-login → email mapping populated when the webhooks see verified emails. The CLI needs to surface "runs *you* started" across all four surfaces, which means correlating identities.

The final design adds a single `agent/utils/user_identity_map.py` keyed by canonical email (lower-cased, dot-stripped where the provider does), holding:

```
{
  email: str,
  github_logins: list[str],
  slack_user_ids: list[str],
  linear_user_ids: list[str],
  last_seen: dict[surface, datetime],
}
```

The map is populated incrementally:
- GitHub: existing `github_user_email_map.py` writes here as well.
- Slack: when a Slack event arrives, the existing Slack utils already fetch the user's email via `users.info`. Extend that call site to upsert the slack_user_id under the email's row.
- Linear: when a Linear webhook fires, fetch the actor's email via the Linear API and upsert similarly.

`GET /cli/runs` resolves the requesting user's row by their GitHub-verified email, then matches threads whose metadata includes any of the user's mapped IDs. If a user's email is missing from a provider (e.g., they've never been seen on Slack), threads from that provider simply won't appear; this is correct behavior and not an error.

Each surface's webhook handler is updated to stamp identity fields on thread metadata at creation time:
- `process_linear_issue` → `linear_user_id`, `linear_actor_email` if available.
- `process_slack_mention` → `slack_user_id`, `slack_team_id`.
- GitHub webhook handlers → `github_login`, `github_sender_id`.
- CLI run creation → `cli_owner_login` (always equal to the session user's github_login).

These fields are additive to the existing per-thread metadata schema; nothing currently set is renamed.

---

## CLI structure

Inside `cli/`:

```
cli/
  index.ts                          # bun entrypoint (compiled binary main)
  src/
    coda.tsx                        # Ink renderer setup (renamed: openswe.tsx)
    types/                          # shared TS types
    agent/
      graph.ts                      # local agent factory (deepagents + LocalShellBackend)
      model-factory.ts
      prompts.ts
    app/
      store.ts                      # zustand state store
      commands.ts                   # slash commands registry
      command-executor.ts
      slash-command.ts
      agent-runner.ts               # drives local agent run + stream
      stream-processor.ts           # consumes deepagents stream → store updates
      cloud-runner.ts               # NEW: drives cloud run via /cli/runs + /cli/runs/{id}/stream
      cloud-stream-processor.ts     # NEW: consumes SSE → same store updates as local
    tui/
      App.tsx                       # top-level Ink component
      Login.tsx                     # NEW: login flow screen
      RunsList.tsx                  # NEW: runs picker (cloud + local sessions)
      Attach.tsx                    # NEW: attach view (transcript + input)
      theme.ts
      figures.ts
      tools/                        # per-tool render components (unchanged)
    lib/
      config.ts                     # NEW: ~/.openswe/config.json read/write
      api-client.ts                 # NEW: typed fetch client for /cli/*
      sse.ts                        # NEW: SSE consumer
      handoff.ts                    # NEW: local↔cloud state pack/unpack
      storage.ts                    # session storage (existing, retargeted to ~/.openswe/)
      logger.ts
      diff.ts
      structured-diff.ts
      file-search.ts
      models.ts
      constants.ts
      prompt-augmentation.ts
      time.ts
      api-key-format.ts
      image-paste.ts
      text-input/
  examples/                         # example prompts (existing)
  scripts/
    install.sh                      # NEW: curl-able installer
  .github/workflows/
    cli-release.yml                 # NEW: builds compiled binaries on tag
  package.json
  tsconfig.json
  bun.lock
  eslint.config.js
  vitest.config.ts
```

`~/.openswe/` on the user's machine:

```
~/.openswe/
  config.json                       # { backend_url, session_token, github_login }
  bin/openswe                       # the installed binary
  logs/openswe.log
  sessions/                         # local-session transcripts (existing coda behavior)
  handoffs/                         # staged handoff bundles
```

---

## Local mode

`openswe` with no args (or `openswe new --local`) launches the existing coda flow: `createAgent` builds a deepagents JS agent against `LocalShellBackend`, `agent-runner.ts` streams it, `stream-processor.ts` translates events into store updates, the Ink TUI renders. No API keys live on disk; the user supplies `ANTHROPIC_API_KEY` (or equivalent) via env. This path has no dependency on a deployment — useful for offline work, demos, and anyone who has the binary but no org membership.

Local mode does not call any `/cli/*` endpoint and does not require login.

---

## Cloud mode

`openswe new --cloud --repo owner/repo --branch foo "fix the flaky test"` is the primary cloud-creation command. The CLI:

1. Reads `~/.openswe/config.json`; if no session token or it's expired, runs login first.
2. `POST /cli/runs` with the body above. Backend returns `{thread_id}`.
3. CLI immediately transitions to the attach screen for that thread.

`openswe runs` opens a picker (`RunsList.tsx`) that calls `GET /cli/runs` and lists everything the user owns across all four surfaces. Selecting one transitions to attach.

`openswe attach <thread_id>` jumps directly to a thread (useful for following a Slack-triggered run from the terminal).

### The attach view

`Attach.tsx` opens an SSE connection to `GET /cli/runs/{thread_id}/stream` and feeds events into `cloud-stream-processor.ts`, which calls the same store actions (`addMessage`, `updateToolExecution`, etc.) that the local stream processor calls. Result: the per-tool render components in `src/tui/tools/` are reused unchanged; the user cannot tell from the transcript whether a run is local or remote.

User input in the attach view is sent via `POST /cli/runs/{thread_id}/messages`. The existing `check_message_queue_before_model` middleware will deliver it to the next LLM iteration. The CLI shows a "queued — will be delivered before next step" indicator until the SSE stream emits an event whose timestamp is after the message was posted.

Detaching (Esc or `Ctrl+D`) closes the SSE stream but does not stop the run. Reattaching re-opens the stream; the LangGraph SDK supports replaying from a cursor, so the CLI passes a `?since=<event_id>` query param to backfill missed events.

---

## Handoff

Handoff moves a run between a local sandbox (`LocalShellBackend` on the user's machine) and a cloud sandbox (whatever `SANDBOX_TYPE` the deployment is configured with, typically `langsmith`). It is symmetric in design but uses different paths in each direction because cloud sandboxes own the working tree while local mode is the working tree.

### State envelope

A handoff bundle is a JSON file (`~/.openswe/handoffs/<thread_id>.json`) containing:

```
{
  thread_id,
  source: 'local' | 'cloud',
  taken_at: ISO8601,
  conversation: BaseMessage[],            # full message history
  pending_queue: QueuedMessage[],         # mid-run messages not yet consumed
  git: {
    remote_url, branch, head_sha,         # what the sandbox/local repo is on
    uncommitted_diff: string,             # `git diff HEAD` output (text)
    untracked_files: { path, content }[]  # base64 if binary
  },
  agent: {
    model, system_prompt, tools_enabled
  }
}
```

`uncommitted_diff` and `untracked_files` make handoff lossless without requiring a commit. A handoff that exceeds 5 MB is rejected with guidance to commit and retry — large binary state is out of scope.

### Local → cloud

1. User runs `openswe handoff --to cloud` from within an attached local session.
2. CLI builds the bundle locally from its in-memory conversation, the local working tree, and the local pending queue.
3. CLI `POST /cli/runs/{none}/adopt` with the bundle. Backend creates a fresh thread, creates a sandbox, materializes the git state (clones repo, checks out `head_sha`, applies the diff, writes untracked files), seeds the message queue, and starts a run that picks up at the next LLM call.
4. CLI transitions to attach mode on the new `thread_id`. The local agent is terminated.

### Cloud → local

1. User runs `openswe handoff --to local` from within an attached cloud session.
2. CLI `POST /cli/runs/{thread_id}/handoff`. Backend pauses the run (sends a cooperative interrupt and waits for the current tool call to finish, with a hard 30s timeout), runs `git diff HEAD` and an untracked-file listing inside the sandbox, packages the bundle, returns it. Sandbox is *not* destroyed — it's left idle, marked `handed_off_to_local_at=<ts>` on thread metadata, and will be reaped by the existing sandbox-GC if not reattached within 24h.
3. CLI applies the bundle to the local working directory: validates it's the right repo (compares `remote_url`), checks out `head_sha`, applies the diff, writes untracked files. If the local working tree is dirty or on a different repo, the operation aborts and tells the user to stash/clean.
4. CLI starts a local agent seeded with the conversation history and pending queue.
5. If the user later runs `openswe handoff --to cloud` on the same `thread_id`, the cloud side reuses the idle sandbox if still alive; otherwise creates a fresh one.

### Handoff authorization

Both `handoff` and `adopt` require the requester's github_login to match `cli_owner_login` or another stamped identity field on the thread. Adopting an arbitrary bundle into a new cloud thread is allowed for any authenticated user (the new thread is theirs); the constraint is that you cannot adopt *over* an existing thread.

---

## Slash commands

Inside an attached or local session, the existing coda slash commands work, plus cloud-specific additions:

- `/help`, `/status`, `/model`, `/review`, `/reset`, `/clear`, `/quit` — existing.
- `/handoff local` and `/handoff cloud` — equivalent to the top-level handoff commands.
- `/detach` — closes the cloud stream but leaves the run running.
- `/interrupt` — sends interrupt to the active run (works in both local and cloud).
- `/pr` — in cloud mode, asks the agent to push its branch and open/update its draft PR (the agent already does this on its own; this is a manual nudge).
- `/whoami` — prints the logged-in github_login + backend URL.

---

## Configuration

### Deployment-side env vars (added to the LangGraph deploy)

- `GITHUB_APP_CLIENT_ID` — the App's OAuth client ID (already required for installation flows in some setups).
- `GITHUB_APP_CLIENT_SECRET` — required to exchange the OAuth code.
- `ALLOWED_GITHUB_ORG` — the GitHub org whose members may use the CLI for this deployment.
- `CLI_SESSION_SECRET` — HMAC key for signing CLI session JWTs. 32+ random bytes.
- `CLI_PUBLIC_BASE_URL` — the externally-reachable URL of the deployment, used to build the OAuth redirect URI.

### GitHub App configuration (one-time, per fork)

- Add `https://<deployment>/cli/auth/callback` to the App's "User authorization callback URLs" list.
- Ensure "Request user authorization (OAuth) during installation" is enabled if you want first-time users to authorize as part of installing the App on their account; not required.

### CLI-side config (`~/.openswe/config.json`)

```
{
  "backend_url": "https://open-swe.langchain.dev",
  "session_token": "<jwt>",
  "github_login": "jduplessis-lc"
}
```

Multiple deployments are supported by storing an array under `deployments` and tracking a `default` key; the CLI picks the default unless overridden by `--backend` on the command line or an `OPENSWE_BACKEND` env var.

---

## Distribution

### Build

The CLI is compiled with `bun build --compile --target=bun-<os>-<arch> ./index.ts --outfile openswe-<os>-<arch>` for four targets: `darwin-arm64`, `darwin-x64`, `linux-x64`, `linux-arm64`. Each binary is ~60–90 MB and embeds the bun runtime; no runtime dependency on bun, node, or npm.

### Release

`.github/workflows/cli-release.yml` triggers on tag push matching `cli-v*`. Matrix-builds the four targets on `ubuntu-latest` and `macos-latest` runners, uploads each as a GitHub Release asset alongside a SHA-256 sums file.

### Install

`cli/scripts/install.sh` is the curl-able installer:

```
curl -fsSL https://raw.githubusercontent.com/langchain-ai/open-swe/main/cli/scripts/install.sh | sh
```

It detects OS/arch via `uname`, downloads the matching asset from the latest release (or a pinned version via `OPENSWE_VERSION=...`), verifies the SHA against the sums file, drops the binary in `~/.openswe/bin/openswe`, makes it executable, and prints the line to add to the user's shell rc. The script honors `OPENSWE_REPO=<owner>/<name>` so forks can reuse the upstream installer verbatim against their own release assets.

Gatekeeper is not an issue for `curl | sh` installs (the quarantine xattr is set by LaunchServices-registered downloaders only). No codesigning required.

### Upgrade

`openswe upgrade` re-runs the install script with the current binary's repo + version as defaults. `openswe upgrade --version cli-v0.4.0` pins a specific version.

---

## Compatibility and versioning

`GET /cli/config` returns `server_version` and a `supports_handoff` capability flag. The CLI's API client compares its own client-version against `server_version` and prints a warning if the server is older than the client's minimum-supported-server (encoded at build time). The capability flag exists so a deployment can disable handoff entirely (e.g., if its sandbox provider doesn't support state export) without breaking the rest of the CLI.

Breaking changes to the `/cli/*` API increment a `cli_api_version` field on `/cli/config`; the CLI refuses to operate against a backend whose `cli_api_version` is higher than it understands and prompts for upgrade.

---

## Testing

- Local agent and TUI: existing vitest suite from coda, retained as-is.
- New cloud paths: vitest unit tests for `api-client.ts`, `cloud-stream-processor.ts`, `handoff.ts` (the last two against fixture event streams and fixture bundles).
- Backend `/cli/*` routes: pytest under `tests/test_cli_routes.py`, using the same FastAPI `TestClient` pattern as existing webapp tests. Org-membership check is mocked at the GitHub-API layer.
- End-to-end: a single integration test under `tests/integration_tests/test_cli_e2e.py` that runs the local agent inside a `LocalShellBackend`, packages a handoff bundle, and adopts it into a mocked cloud thread to verify the round-trip.

---

## What this design intentionally does not include

- **Generic third-party access**: there is no API key, no service account, no machine-to-machine flow. CLI access is always tied to a human GitHub user who is a current member of the configured org.
- **A separate web UI**: the CLI is the only first-party client of `/cli/*`. The web surfaces remain Slack/Linear/GitHub.
- **Multi-user attach to the same thread**: only one CLI may be attached to a thread at a time. Concurrent attach is rejected with the github_login of the current holder, so a user can ask their coworker to detach. (The webhooks-driven sources can still deliver messages to the thread regardless of attach state.)
- **Local sandbox parity with cloud sandbox providers**: local mode uses `LocalShellBackend` and runs in the user's working directory. It does not emulate the LangSmith sandbox's GitHub proxy or the per-thread isolation. This is by design — local mode is for interactive iteration; cloud mode is for the production agent loop.
