# Installation Guide

This guide gets Open SWE running with the **minimum supported installation: GitHub + Slack**. Everything else — the web dashboard, Linear, per-user GitHub identity, "Sign in with Slack", custom sandbox images, alternate sandbox providers, web search — is an optional add-on you enable later.

Open SWE has two runnable pieces:

- **The backend** — a LangGraph app (the `agent`, `reviewer`, `analyzer`, `chat`, and `scheduler` graphs) plus a FastAPI app (`agent.webapp:app`) that owns the webhooks and the dashboard API. Both are served together by `langgraph dev`.
- **The dashboard** — an optional TanStack Start + Vite web app in `ui/` (package name `open-swe-dashboard`). It is a thin client over the FastAPI dashboard API. Webhook-driven use does not need it.

The minimum install needs these values, and nothing else:

| Variable | Where it comes from |
|---|---|
| `LANGSMITH_API_KEY_PROD` | LangSmith → Settings → API Keys |
| One model provider key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GOOGLE_API_KEY`), **or** `LANGSMITH_GATEWAY_ENABLED=true` | Your provider, or the LangSmith LLM Gateway |
| `GITHUB_APP_CLIENT_ID` | GitHub App settings page |
| `GITHUB_APP_PRIVATE_KEY` | GitHub App settings page → Private keys |
| `GITHUB_WEBHOOK_SECRET` | You generate it; paste the same value into the GitHub App |
| `SLACK_BOT_TOKEN` | Slack app → OAuth & Permissions |
| `SLACK_SIGNING_SECRET` | Slack app → Basic Information |

Everything Open SWE used to ask for beyond that is now discovered at runtime: the GitHub App installation, the Slack bot's user id and handle, the LangSmith workspace (tenant) and tracing-project ids, and the sandbox base image. The [Advanced overrides](#advanced-overrides-and-deprecated-variables) table lists the explicit overrides if you ever need them.

> **The steps are ordered to avoid forward references.** Each step only depends on things you've already completed.

## Prerequisites

- **Python 3.11 – 3.13** (3.14 is not yet supported due to dependency constraints)
- [uv](https://docs.astral.sh/uv/) package manager
- [ngrok](https://ngrok.com/) (for local development — exposes the webhook endpoints to the internet)
- [pnpm](https://pnpm.io/) — only for the optional dashboard (see [Dashboard](#dashboard-web-ui))

## 1. Clone and install

```bash
git clone https://github.com/langchain-ai/open-swe.git
cd open-swe
uv venv
source .venv/bin/activate
uv sync --all-extras
```

## 2. Start ngrok

You'll need the ngrok URL in the GitHub and Slack steps, so start it first.

```bash
ngrok http 2024 --url https://some-url-you-configure.ngrok.dev
```

You don't need to pass the `--url` flag, however doing so will use the same subdomain each time you start the server. Without it, you'll need to update the webhook URL in GitHub and Slack every time you restart ngrok.

Copy the HTTPS URL. You'll paste it into the webhook settings in steps 3 and 5.

> Keep this terminal open — ngrok needs to stay running during local development. Use a second terminal for the rest of the steps.

## 3. Create a GitHub App

Open SWE authenticates as a [GitHub App](https://docs.github.com/en/apps/creating-github-apps) to clone repos, push branches, and open PRs.

### 3a. Create the app

1. Go to **GitHub Settings → Developer settings → [GitHub Apps](https://github.com/settings/apps) → [New GitHub App](https://github.com/settings/apps/new)**
2. Fill in:
   - **App name**: `Open SWE` (or your preferred name)
   - **Homepage URL**: any valid URL — it is only shown on the GitHub Marketplace page. Use something like `https://github.com/langchain-ai/open-swe`
   - **Callback URL**: leave empty. Callback URLs are only needed for the [dashboard](#dashboard-web-ui) and [per-user GitHub identity](#per-user-github-identity-langsmith-oauth-provider) add-ons.
   - **Request user authorization (OAuth) during installation**: leave unchecked (same add-ons)
   - **Webhook URL**: `https://<your-ngrok-url>/webhooks/github`
   - **Webhook secret**: generate one and paste it here (`openssl rand -hex 32`), then enter the same value as `GITHUB_WEBHOOK_SECRET` in step 6. Or leave it empty for now, let `make setup` generate one in step 6, and come back to paste it from `.env`.
3. Set permissions:
   - **Repository permissions**:
     - Contents: Read & write
     - Pull requests: Read & write
     - Issues: Read & write
     - Checks: Read & write — reports an "Open SWE Review" check run on PRs while an auto-review runs and lets `/baby-sit` read third-party CI conclusions. Without it, check-run creation fails (logged, best-effort), reviews still work, and `/baby-sit` fails closed when it cannot read the complete check set.
     - Commit statuses: Read-only — required for `/baby-sit` to evaluate the complete PR status set, including integrations that report via legacy commit statuses instead of check runs.
     - Actions: Read-only — optional for CI diagnostics and log access. Grant **Read & write** only to enable `/baby-sit` to rerun evidence-backed flaky GitHub Actions jobs. Existing installations must approve this permission elevation. Actions write also permits rerunning, canceling, and deleting workflow runs at the token level; `/baby-sit` is instructed to use only failed-job reruns.
     - Workflows: Read & write — required to let Open SWE directly push branches containing explicitly requested GitHub Actions workflow changes.
     - Metadata: Read-only
   - **Organization permissions** (only if you plan to set `ALLOWED_GITHUB_ORGS` — see [Allowlists](#repository-allowlists-mention-handles-and-user-mapping)):
     - Members: Read-only — used to verify org membership for dashboard login and LangSmith trace-tool access via `GET /orgs/{org}/memberships/{username}`. Without this permission that call returns 403 and the check fails closed.
4. Under **Subscribe to events**, enable:
   - `Issue comment`
   - `Pull request review`
   - `Pull request review comment`
   - `Check run` — required for immediate `/baby-sit` failure detection
   - `Check suite` — required for immediate `/baby-sit` failure detection
   - `Workflow run` — required for immediate `/baby-sit` failure detection
   - `Status` — optional; covers integrations that report via the legacy commit-status API
5. Click **Create GitHub App**

### 3b. Collect credentials

After creating the app:

1. **Client ID** — shown near the top of the app's settings page (starts with `Iv...`). Save it as `GITHUB_APP_CLIENT_ID`. It is both the dashboard OAuth client id and the issuer Open SWE signs its GitHub App JWTs with, so the numeric **App ID** is not needed.
2. **Private key** — scroll down to **Private keys** → **Generate a private key**. A `.pem` file downloads; keep it, `make setup` will read it (or paste its contents as `GITHUB_APP_PRIVATE_KEY`).

### 3c. Install the app on your repositories

1. From your app's settings page, click **Install App** in the sidebar
2. Select your org or personal account
3. Choose which repositories Open SWE should have access to
4. Click **Install**

You do not need to copy the installation id. Open SWE resolves the installation from the repository a webhook or run refers to, and when the app has exactly one installation it uses that for everything else. If you install the app on several accounts and also trigger runs without repository context (for example a Slack message with no default repository), set `GITHUB_APP_INSTALLATION_ID` to pick the default.

## 4. Get a LangSmith API key

Open SWE uses [LangSmith](https://smith.langchain.com/) for tracing and for the isolated cloud sandbox each task runs in.

1. Create a [LangSmith account](https://smith.langchain.com/) if you don't have one
2. Go to **Settings → API Keys → Create API Key**
3. Save it as `LANGSMITH_API_KEY_PROD`

That is the whole LangSmith setup:

- **Sandboxes** boot from the LangSmith default snapshot, which ships `git`, `gh`, Python, `uv`, and Node. Build your own image only when your repos need more — see [Custom sandbox snapshot and environments](#custom-sandbox-snapshot-and-environments).
- **Trace links** ("View trace" in Slack and GitHub) find your workspace and the `open-swe-agent` / `open-swe-review` projects by name, so no tenant or project ids are required.
- **Tracing itself** is on automatically on LangGraph Platform. For local `make dev`, also export `LANGSMITH_API_KEY` (the same key) if you want local runs traced.

## 5. Create the Slack app

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From a manifest**
2. Copy the manifest below, replacing `<your-ngrok-url>` with the backend URL from step 2 (or your deployed LangGraph/FastAPI URL in production). The `redirect_urls` are only used by the optional add-ons; leaving them in is harmless.

<details>
<summary>Slack App Manifest</summary>

```json
{
    "display_information": {
        "name": "Open SWE",
        "description": "Enables Open SWE to interact with your workspace",
        "background_color": "#000000"
    },
    "features": {
        "app_home": {
            "home_tab_enabled": false,
            "messages_tab_enabled": true,
            "messages_tab_read_only_enabled": false
        },
        "bot_user": {
            "display_name": "Open SWE",
            "always_online": true
        }
    },
    "oauth_config": {
        "redirect_urls": [
            "https://smith.langchain.com/host-oauth-callback/<your-provider-id>",
            "http://localhost:2024/dashboard/api/slack/callback"
        ],
        "scopes": {
            "bot": [
                "reactions:write",
                "app_mentions:read",
                "channels:history",
                "channels:read",
                "chat:write",
                "files:write",
                "groups:history",
                "groups:read",
                "im:history",
                "im:read",
                "im:write",
                "mpim:history",
                "mpim:read",
                "team:read",
                "users:read",
                "users:read.email"
            ]
        }
    },
    "settings": {
        "event_subscriptions": {
            "request_url": "https://<your-ngrok-url>/webhooks/slack",
            "bot_events": [
                "app_mention",
                "message.im",
                "message.mpim"
            ]
        },
        "interactivity": {
            "is_enabled": true,
            "request_url": "https://<your-ngrok-url>/webhooks/slack/interactivity"
        },
        "org_deploy_enabled": false,
        "socket_mode_enabled": false,
        "token_rotation_enabled": false
    }
}
```

</details>

3. Install the app to your workspace and copy the **Bot User OAuth Token** (`xoxb-...`) as `SLACK_BOT_TOKEN`
4. From **Basic Information → App Credentials**, copy the **Signing Secret** as `SLACK_SIGNING_SECRET`

Open SWE learns the bot's user id and handle from the token (`auth.test` plus `users.info`, which is why the manifest includes `users:read`), so there is nothing else to copy.

**Slack URL checklist.** Both Slack URLs must point at the Open SWE backend that serves `agent.webapp:app` (locally, your ngrok URL forwarding to `langgraph dev`; in production, your LangGraph/FastAPI deployment URL), not the dashboard frontend URL.

- **Event Subscriptions → Request URL:** `https://<your-backend-url>/webhooks/slack`
- **Interactivity & Shortcuts → Interactivity Request URL:** `https://<your-backend-url>/webhooks/slack/interactivity`

Slack Block Kit option buttons only work when Interactivity is enabled and pointed at `/webhooks/slack/interactivity`.

Open SWE refuses Slack Connect channels when `conversations.info` reports `is_ext_shared`, before starting an agent run. If Slack cannot verify a channel, it fails closed and does not operate there.

## 6. Create `.env` with `make setup`

```bash
make setup
```

The guided setup asks for the values from steps 3–5 (secrets are read without echo), lets you point it at the downloaded `.pem`, and writes `.env` with owner-only permissions. It also:

- generates `DASHBOARD_JWT_SECRET` and `TOKEN_ENCRYPTION_KEY` — two independent random keys, so the dashboard add-on works later without another step;
- generates `GITHUB_WEBHOOK_SECRET` if you leave that prompt empty, and tells you how to copy it from `.env` into the GitHub App (the value itself is never printed).

Re-running `make setup` keeps values that are already set; add `--force` to rotate the generated secrets, or `--dashboard` to collect the dashboard settings too.

Your `.env` now looks like this:

```bash
LANGSMITH_API_KEY_PROD="lsv2_..."
ANTHROPIC_API_KEY="sk-ant-..."          # or OPENAI_API_KEY / GOOGLE_API_KEY, or LANGSMITH_GATEWAY_ENABLED="true"
GITHUB_APP_CLIENT_ID="Iv1...."
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
GITHUB_WEBHOOK_SECRET="..."
SLACK_BOT_TOKEN="xoxb-..."
SLACK_SIGNING_SECRET="..."

# Added by scripts/setup_env.py
DASHBOARD_JWT_SECRET="..."
TOKEN_ENCRYPTION_KEY="..."
```

Prefer to write it by hand? The same seven variables are all that is required; generate the two secrets with `openssl rand -hex 32` (JWT secret) and `openssl rand -base64 32` (Fernet key).

## 7. Start the backend

Make sure ngrok is still running from step 2, then start the backend in a second terminal:

```bash
make dev          # uv run langgraph dev --no-browser --port 2024
```

`langgraph dev` serves all the graphs *and* the FastAPI app together on `http://localhost:2024`. On startup it logs which surfaces are enabled (`config: GitHub: enabled`, `config: Slack: enabled`, …), any variable a surface is still missing, and any deprecated variable it found.

| Endpoint | Purpose |
|---|---|
| `POST /webhooks/github` | GitHub issue/PR/comment webhooks |
| `POST /webhooks/slack` | Slack event webhooks |
| `POST /webhooks/slack/interactivity` | Slack Block Kit button interactions |
| `GET /webhooks/slack` | Slack webhook verification |
| `POST /webhooks/linear` / `GET /webhooks/linear` | Linear (add-on) |
| `GET /dashboard/api/*` | Dashboard API (add-on) |
| `GET /health` | Health check |

> `make run` (`uvicorn agent.webapp:app --port 8000`) serves the FastAPI app **without** the LangGraph runtime, on port 8000. Use `make dev` on `:2024` for anything that creates runs.

## 8. Tell Open SWE which repository to work in

- **GitHub** triggers always know the repository: the issue or pull request the mention came from.
- **Slack** messages name a repository with `repo:owner/name` (or a GitHub URL) in the message, or once per channel in the channel topic or purpose. A run that cannot resolve a repository is rejected with `no default repository configured`.
- A **team-wide default** lives in **Admin → Team settings → Default Repository** in the [dashboard](#dashboard-web-ui), or over the API: `PUT /dashboard/api/team-settings` with `{"default_repo": "owner/name"}` using an [admin credential](#admin-api-credentials). With a default set, `repo:name` shorthand (no owner) also resolves against that owner.

## 9. Verify it works

### GitHub

1. Go to any issue in a repository where the app is installed
2. Create or comment on an issue with: `@openswe what files are in this repo?`
3. You should see a 👀 reaction within a few seconds, a new run in the `open-swe-agent` LangSmith project, and a reply comment on the issue

### Slack

1. In any channel where the bot is invited, start a thread (or set the channel topic to `repo:owner/name` first)
2. Mention the bot: `@Open SWE repo:owner/name what's in the repo?`
3. You should see a reply in the thread with the agent's response

---

## Optional add-ons

Open each section only when you want that feature. Everything in the quick start keeps working without them.

<details id="dashboard-web-ui">
<summary><strong>Dashboard (web UI)</strong></summary>

The dashboard in `ui/` adds GitHub login, per-user model/profile settings, team defaults (including the default repository), enabled-repo and review-style management, user mappings, and the Agents chat UI. Requires [pnpm](https://pnpm.io/) (Node 20+ also works, but `ui/pnpm-lock.yaml` is the canonical lockfile).

**GitHub App changes.** On the app's settings page add the callback URL `http://localhost:2024/dashboard/api/auth/callback` (for production also `https://<your-dashboard-api-url>/dashboard/api/auth/callback`; for the desktop app also `https://<your-backend-url>/dashboard/api/auth/callback`), then under **Client secrets** click **Generate a new client secret**. This is a direct GitHub OAuth flow between the browser, your backend, and GitHub; it does not go through LangSmith.

**Environment.** Run `make setup --dashboard`, or add by hand:

```bash
GITHUB_APP_CLIENT_SECRET=""            # from the GitHub App settings page
# Public URL that browsers use for /dashboard/api/* and OAuth callbacks.
# Use the FastAPI backend URL for local/cross-origin direct API calls.
# Use the dashboard frontend URL when a same-origin frontend rewrite proxies /dashboard/api/*.
# Its scheme drives cookie security: http:// => SameSite=Lax (local);
# https:// => Secure + SameSite=None (production).
DASHBOARD_API_BASE_URL="http://localhost:2024"
# Public base URL of the dashboard frontend (the ui/ app). Default post-login redirect,
# and the origin allowed for CORS and the CSRF check.
DASHBOARD_BASE_URL="http://localhost:3000"
# Comma-separated GitHub login or email allowlist for admin dashboard endpoints.
# Empty => nobody is an admin.
CONFIGURED_ADMINS=""                   # e.g. "alice,bob@my-org.com"
# URL of the LangGraph server the FastAPI side calls to trigger/stream runs.
# Defaults to http://localhost:2024 locally; set to your deployment URL in prod.
LANGGRAPH_URL="http://localhost:2024"
```

`DASHBOARD_JWT_SECRET` (session cookie and OAuth state HMAC) and `TOKEN_ENCRYPTION_KEY` (encryption of stored OAuth tokens) were already generated by `make setup`. The dashboard's own origin — `DASHBOARD_BASE_URL` — is always allowed for credentialed CORS and for the CSRF check on non-GET requests. Set `DASHBOARD_ALLOWED_ORIGINS` only when **additional** origins must call the API with credentials, such as preview deploys or a frontend served from a different host: `DASHBOARD_ALLOWED_ORIGINS="https://preview.example"`. `*` is rejected.

**Run it.**

```bash
pnpm install          # from the repo root: ui/ and desktop/ are one pnpm workspace
make web              # pnpm run dev -> Vite on http://localhost:3000
```

No `ui/.env` is needed: the dev server proxies `/dashboard/api/*` to `DASHBOARD_API_URL`, which defaults to `http://localhost:2024`. Point it elsewhere by exporting that variable before `make web` or `pnpm run dev`. It is read at request time, so the same build can front any backend.

Because the browser only ever talks to `http://localhost:3000`, no CORS preflight is involved locally. The `osw_session` cookie has to be set on the dashboard origin: set `DASHBOARD_API_BASE_URL="http://localhost:3000"` and register `http://localhost:3000/dashboard/api/auth/callback` as a GitHub App callback URL. Keep it on an `http://` URL locally so the cookie uses `SameSite=Lax` rather than `Secure`.

**Verify.** With the backend and UI running, open `http://localhost:3000`, click **Sign in with GitHub**, and you should land logged in. If your GitHub login or email is in `CONFIGURED_ADMINS`, the **Admin** pages (Team settings, User mappings, Sandbox, Environments, …) are available.

To enable Datadog browser RUM, set `VITE_DATADOG_APPLICATION_ID` and `VITE_DATADOG_CLIENT_TOKEN` when building the dashboard. Optional build-time settings are `VITE_DATADOG_SITE` (default `datadoghq.com`), `VITE_DATADOG_SERVICE` (default `open-swe-dashboard`), `VITE_DATADOG_ENV`, `VITE_DATADOG_VERSION`, `VITE_DATADOG_SESSION_SAMPLE_RATE` (default `100`), and `VITE_DATADOG_SESSION_REPLAY_SAMPLE_RATE` (default `100`). Session Replay is enabled by default for sampled RUM sessions with all content masked; telemetry also strips URL query strings and fragments. Values prefixed with `VITE_` are public in the browser bundle; use a Datadog client token, never an API or application key.

`pnpm run build`, `pnpm run typecheck` and `pnpm run test` run the same task across the workspace through Turborepo; scope one to a package with `pnpm --filter open-swe-dashboard run <script>`. `pnpm run lint` (oxlint) and `pnpm run format` / `pnpm run format:check` (oxfmt) run once from the root over every JS and TS file in the repo.

**Voice dictation** in the composer uses your OpenAI configuration (`OPENAI_API_KEY`, optional `OPENAI_BASE_URL`); admins choose the transcription model on the Admin page.

**Desktop app (experimental).** The Electron app in `desktop/` includes the compiled dashboard UI. Run it alongside the backend (and, optionally, the web UI) in separate terminals:

```bash
pnpm install                  # from the repo root
make dev                      # terminal 1
pnpm run dev:desktop          # terminal 2
make web                      # terminal 3, optional web UI
```

Development connects to `http://localhost:2024`. To use a hosted backend instead, run `pnpm --dir desktop run start -- --backend-url=https://your-backend.example.com` or set `OPEN_SWE_BACKEND_URL`. Create an unpacked application with `pnpm --dir desktop run pack`, or an installer with `pnpm --dir desktop run dist`. Packaged builds ask for the organization's backend URL on first launch and store it locally; they never default to the maintainers' deployment. The GitHub App must allow `<backend-url>/dashboard/api/auth/callback` for desktop login.

</details>

<details id="admin-api-credentials">
<summary><strong>Admin API credentials (CI and scripts)</strong></summary>

Admin-gated endpoints such as `PUT /dashboard/api/team-settings` and `PUT /dashboard/api/sandbox-settings` accept two credentials in place of the browser session cookie, both as `Authorization: Bearer`:

**GitHub Actions OIDC (preferred — no stored secret).** A workflow with `permissions: id-token: write` mints a short-lived token that GitHub signs and scopes to the repo, ref, and audience it requested. Allowlist it on the deployment:

```bash
ADMIN_OIDC_SUBJECTS="acme/sandbox-images"                       # any workflow/ref in this repo
# or pin the ref with a full subject:
# ADMIN_OIDC_SUBJECTS="repo:acme/sandbox-images:ref:refs/heads/main"
ADMIN_OIDC_AUDIENCE="open-swe"                                  # optional; this is the default
```

`ADMIN_OIDC_SUBJECTS` is the on/off switch — while it is empty, OIDC auth is unavailable. Entries containing `:` are matched against the token's `sub` claim, and `owner/repo` entries against its `repository` claim. The audience is verified either way, defaulting to `open-swe`; override it only if you set the workflow's requested audience to match. Anyone who can run a workflow on an allowlisted repo/ref gets admin on these endpoints, so keep the list to internal repos.

**Admin personal access token.** The token only needs to identify its owner (`GET /user`), and that login (or email) must appear in `CONFIGURED_ADMINS`. Matching by login needs no token permissions; matching by email needs a token that can read email addresses (classic `user:email`, or the fine-grained "Email addresses" read permission) when the account's email isn't public. Prefer a machine user over a human's token.

`secrets.GITHUB_TOKEN` works for neither: installation tokens have no user identity, and they are not OIDC tokens. `examples/github-actions/set-base-snapshot.yml` is a copy-ready workflow using the OIDC path.

</details>

<details id="per-user-github-identity-langsmith-oauth-provider">
<summary><strong>Per-user GitHub identity (LangSmith OAuth provider)</strong></summary>

By default every agent operation uses the GitHub App's installation token: PRs and commits appear as the app's bot identity, and the app's installation-level permissions apply. This add-on lets each run authenticate as the triggering user instead, brokered by LangSmith, so PRs show the user's identity and their own GitHub permissions are respected.

1. Pick an **OAuth provider ID** — a short string used in both GitHub and LangSmith, e.g. `your-org-github-oauth`.
2. On the GitHub App, add the callback URL `https://smith.langchain.com/host-oauth-callback/<your-provider-id>` and enable **Request user authorization (OAuth) during installation**. Generate a client secret if you have not already.
3. In LangSmith, go to **Settings → OAuth Providers → Add Provider**, set the **Provider ID** to the string from step 1, enter the GitHub App's **Client ID** and **Client Secret**, set the **Authorization URL** to `https://github.com/login/oauth/authorize` and the **Token URL** to `https://github.com/login/oauth/access_token`, leave "Enable PKCE" unchecked, and save.
4. Add to `.env`:

```bash
GITHUB_OAUTH_PROVIDER_ID=""            # the provider ID from step 1
# Secret used to mint short-lived service JWTs that ask LangSmith to resolve a
# specific user's GitHub token. Needed for per-user token resolution in deployed mode.
X_SERVICE_AUTH_JWT_SECRET=""
```

The Slack manifest's `https://smith.langchain.com/host-oauth-callback/<your-provider-id>` redirect URL belongs to this flow; replace the placeholder with your provider ID.

</details>

<details id="linear">
<summary><strong>Linear</strong></summary>

Open SWE listens for Linear comments that mention `@openswe`.

**Create a webhook:**

1. In Linear, go to **Settings → API → Webhooks → New webhook**
2. Fill in:
   - **Label**: `Open SWE`
   - **URL**: `https://<your-ngrok-url>/webhooks/linear` — use the ngrok URL from step 2
   - **Secret**: generate with `openssl rand -hex 32` — save this as `LINEAR_WEBHOOK_SECRET`
3. Under **Data change events**, enable **Comments → Create** only
4. Click **Create webhook**

**Get your API key:**

1. Go to **Settings → API → Personal API keys → New API key**
2. Name it `Open SWE`, select **All access**, and copy the key
3. Save it as `LINEAR_API_KEY`

**Configure team-to-repo mapping:**

Open SWE routes Linear issues to GitHub repos based on the Linear team and project. Edit the mapping in `agent/utils/linear_team_repo_map.py`:

```python
LINEAR_TEAM_TO_REPO = {
    "My Team": {"owner": "my-org", "name": "my-repo"},
    "Engineering": {
        "projects": {
            "backend": {"owner": "my-org", "name": "backend"},
            "frontend": {"owner": "my-org", "name": "frontend"},
        },
        "default": {"owner": "my-org", "name": "monorepo"},
    },
}
```

Users can also override the team/project mapping per-comment by including `repo:owner/name` (or a GitHub URL) in their `@openswe` comment. The mapping is used as a fallback when no repo is specified in the comment text, and the team default repository is the fallback after that.

**Verify:** comment `@openswe what files are in this repo?` on an issue in a mapped team. You should see a 👀 reaction, a new run in LangSmith, and a reply comment.

</details>

<details id="sign-in-with-slack">
<summary><strong>"Sign in with Slack" account linking</strong></summary>

The dashboard can let a user link their Slack identity to their GitHub login via Slack OIDC ("Sign in with Slack"). This is what lets a Slack-triggered run resolve to the right GitHub user without an admin mapping. Requires the [dashboard](#dashboard-web-ui).

1. The manifest already registers the OIDC redirect (`.../dashboard/api/slack/callback`). Under **OpenID Connect** (or **Sign in with Slack**) make sure the `openid`, `email`, and `profile` user scopes are available.
2. From **Basic Information → App Credentials**, save the app's **Client ID** as `SLACK_CLIENT_ID` and **Client Secret** as `SLACK_CLIENT_SECRET`.
3. (Optional) Set `SLACK_TEAM_ID` (your workspace ID, `T...`) to restrict linking to a single workspace.

If `SLACK_CLIENT_ID`/`SLACK_CLIENT_SECRET` are unset, the "Sign in with Slack" link is simply disabled; the rest of Slack triggering still works.

Without this, an unmapped person who tags Open SWE in Slack still gets a run with the GitHub App's installation permissions, plus a "link your GitHub account" prompt.

</details>

<details id="slack-code-channels">
<summary><strong>Slack code channels (early access)</strong></summary>

The default manifest uses the legacy Slack integration. To enable Slack [code channels](https://api.slack.com/partners/code-channels), open **Admin → Slack integration** in the dashboard, turn on **Slack Code Channels**, copy the generated manifest, update the existing Slack app, and reinstall or re-authorize it. The admin selection is browser-local and only controls which manifest is copied.

In a code channel the whole channel is one Open SWE session, so Open SWE answers messages without requiring an `@`-mention, replies at the channel level by default (or in a thread the user started), reports its session status, and keeps the context bar current. The `manage_code_channel` tool covers channel creation and archival, status and title, context actions and external resources, runtime slash commands, HTML/diff/Block Kit/canvas views, view reconciliation, and canvas content/comments.

This requires the `code_channels:manage` bot scope, the `agent_session_stopped` and `code_channel_action` bot events, and `features.code_channels.enabled`. The feature's `slash_command_url` delivers runtime-registered commands to the signed Open SWE endpoint, while Block Kit view actions use the normal interactivity endpoint. Code channel messages arrive over the `message.channels` / `message.groups` subscriptions an app already uses; `message.session` is an alternative for apps that want *only* session messages, and subscribing to both does not duplicate deliveries. Code channels are in early access: if your workspace is not enrolled, leave the Admin toggle off — everything else keeps working unchanged.

</details>

<details id="custom-sandbox-snapshot-and-environments">
<summary><strong>Custom sandbox snapshot and environments</strong></summary>

LangSmith sandboxes provide the isolated execution environment for each agent run. Without any configuration they boot from the LangSmith default snapshot. Build your own **snapshot** (from a Docker image) when your repos need extra toolchains or internal tools pre-installed.

The image must carry the toolchain agent runs expect — `git`, `gh`, `sfw`, the Docker CLI, and the language runtimes — and must be in a registry LangSmith can pull from. Run `sfw --version` while building the image to populate its binary cache, and set `SFW_SKIP_UPDATE_CHECK=1` at runtime so the sandbox proxy does not block its update request. Open SWE authenticates `git` and `gh` through the LangSmith sandbox proxy using runtime-minted GitHub App installation tokens, so no GitHub token belongs in the image.

Build a snapshot in the LangSmith UI (Sandboxes → Snapshots → New), via the SDK, or with the helper script:

```bash
uv run python scripts/create_sandbox_snapshot.py \
  --name open-swe-gh-cli-amd64 \
  --image johanneslangchain/open-swe-sandbox:gh-cli-amd64
```

Then either set the resulting UUID in your environment or, in the dashboard, on **Admin → Sandbox → Base snapshot** (the stored value wins; clearing it falls back to the env var):

```bash
DEFAULT_SANDBOX_SNAPSHOT_ID="<snapshot-uuid>"   # Optional; omit to use the LangSmith default snapshot
# Optional sizing/TTL overrides. Defaults: 128 GiB root FS, 4 vCPUs, 16 GiB RAM,
# 7200 s idle stop (0 disables), 2592000 s (30 d) delete-after-stop (0 disables).
DEFAULT_SANDBOX_SNAPSHOT_FS_CAPACITY_BYTES="137438953472"
DEFAULT_SANDBOX_VCPUS="4"
DEFAULT_SANDBOX_MEM_BYTES="17179869184"
DEFAULT_SANDBOX_IDLE_TTL_SECONDS="7200"
DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS="2592000"
```

The same setting is available over the API, which is how the repo that builds your sandbox image can roll a new snapshot out on its own (see [Admin API credentials](#admin-api-credentials)):

```bash
curl -X PUT "$OPEN_SWE_BASE_URL/dashboard/api/sandbox-settings" \
  -H "Authorization: Bearer $ADMIN_GITHUB_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"base_snapshot_id": "<snapshot-uuid>"}'
```

**Environments.** An environment pairs a prompt with a snapshot every run boots from, and can span several repos. Admins build one from an **admin thread** (the **Admin** toggle in the composer, available when their login or email is in `CONFIGURED_ADMINS`): the agent provisions its own sandbox — cloning repos, installing toolchains, warming caches — and then captures it. The environment named `default` is the one runs use; any other name is a draft. Records are managed on the admin **Environments** page.

With more than one environment configured, a picker appears in the dashboard composer (any signed-in user, names only), and a Slack thread can pick one with an `env:<name>` tag on the message that opens it — `@Open SWE env:staging fix the flaky test`. Only the opening message can: the sandbox is created once, so a later tag would change the prompt but not the image. A run with no selection uses `default`.

Captures are named `openswe-environment-<name>` (the platform appends its own `:latest` tag, and rejects a name that carries one); set `ENVIRONMENT_SNAPSHOT_PREFIX` to replace the `openswe` prefix when several deployments share one LangSmith workspace. Snapshot resolution for a new sandbox is: the run's environment, then the admin base snapshot, then `DEFAULT_SANDBOX_SNAPSHOT_ID`, then the LangSmith default snapshot.

</details>

<details id="other-sandbox-providers">
<summary><strong>Other sandbox providers</strong></summary>

`SANDBOX_TYPE` defaults to `langsmith`. Set it to `modal`, `daytona`, `runloop`, `e2b`, or `local` to use another provider; each needs its own credentials, listed in [CUSTOMIZATION.md](CUSTOMIZATION.md#using-a-different-sandbox-provider). Only the LangSmith provider gets the GitHub proxy, so other providers see GitHub tokens inside the sandbox.

</details>

<details id="repository-allowlists-mention-handles-and-user-mapping">
<summary><strong>Repository allowlists, mention handles, and user mapping</strong></summary>

**Mention handles.** The handles this deployment answers to default to `@openswe,@open-swe,@openswe-dev` and are configurable — set `OPEN_SWE_MENTION_TAGS` to a comma-separated list. Handles are matched on a word boundary, so `@openswe` does not fire on `@openswe-staging`. Give each deployment a distinct handle when more than one shares a GitHub org, Slack workspace, or Linear workspace. Set `EXTRA_INTERNAL_BOT_LOGINS` (e.g. `openswe-staging[bot]`) to treat other Open SWE deployments' comments as internal rather than untrusted.

**Allowlists.**

```bash
# Allow all repos in these orgs
ALLOWED_GITHUB_ORGS="langchain-ai,anthropics"

# Allow specific repos (owner/repo format)
ALLOWED_GITHUB_REPOS="some-user/their-repo,another-org/specific-repo"

# Single GitHub org whose members may trigger the agent on *public* repos.
# Empty => no public-repo gate. Distinct from ALLOWED_GITHUB_ORGS.
PUBLIC_REPO_ORG_GATE=""
```

A GitHub or Linear webhook is accepted if the resolved repo's org is in `ALLOWED_GITHUB_ORGS` **or** the `owner/repo` is in `ALLOWED_GITHUB_REPOS`. If both are empty, all repos are allowed.

For Slack and dashboard requests, `ALLOWED_GITHUB_ORGS` also adds a prompt-level edit guard. To modify a repository outside those organizations, the user must explicitly request that exact repository with its full `https://github.com/<owner>/<repo>` URL. Repository hints, defaults, shorthand, and contextual links do not qualify. This does not bypass the server-side GitHub/Linear webhook filter above or GitHub credential and App installation permissions.

`ALLOWED_GITHUB_ORGS` also gates **dashboard login**: when set, only GitHub accounts that are active members of one of the listed organizations can complete the OAuth login and receive a session. Membership is verified server-side with the GitHub App installation token (so private memberships are visible and no extra OAuth scope is required), and the check fails closed on any API error. When `ALLOWED_GITHUB_ORGS` is empty, dashboard login is open to any GitHub account.

> **Observability access**: when team LangSmith credentials are connected, every active member of an organization in `ALLOWED_GITHUB_ORGS` can use the read-only LangSmith trace tools. Only list organizations whose full active membership may access team-level trace data. This does not grant Datadog access.

> **Required GitHub App installation and permission**: install the App in every organization listed in `ALLOWED_GITHUB_ORGS` and grant **Organization → Members: Read-only** (see step 3a). Membership checks resolve each organization's installation and call `GET /orgs/{org}/memberships/{username}`. Missing installations, unapproved permissions, and API errors fail closed.

**User mapping.** Which GitHub users can trigger the agent is controlled by the **user mapping** (GitHub login ⇄ work email ⇄ optional Slack ID), stored in the LangGraph Store rather than in code. Manage it in the dashboard under **Admin → User mappings**: add or update a single mapping (GitHub login + work email, plus an optional Slack user ID); the list is paged (20 per page). Users can also **self-onboard**: when an unmapped person tags Open SWE in Slack, the agent runs with limited (GitHub App installation) permissions and posts a "link your GitHub account" prompt. Completing the org-gated GitHub OAuth login records a `self` mapping (carrying the originating Slack ID and work email). Self-signup is therefore bounded by the same `ALLOWED_GITHUB_ORGS` gate as dashboard login.

</details>

<details id="models-gateway-search-and-reviewer">
<summary><strong>Models, LLM Gateway, web search, and reviewer settings</strong></summary>

```bash
# Additional model providers (the quick start needs only one key)
ANTHROPIC_API_KEY=""
OPENAI_API_KEY=""                      # OpenAI models and dashboard voice dictation
# OPENAI_BASE_URL="https://api.openai.com/v1"  # Optional OpenAI-compatible API base URL
GOOGLE_API_KEY=""                      # google_genai: models
FIREWORKS_API_KEY=""                   # fireworks: models
BASETEN_API_KEY=""                     # Baseten models when not using the LangSmith Gateway
LLM_MODEL_ID=""                        # Default model, provider:model format (see CUSTOMIZATION.md)

# Route provider calls through the LangSmith LLM Gateway using LANGSMITH_API_KEY_PROD
# instead of per-provider keys. Admins can also toggle this per team in the dashboard.
LANGSMITH_GATEWAY_ENABLED="false"

# Web search tool
EXA_API_KEY=""                         # From https://dashboard.exa.ai

# Reviewer / analyzer: LangSmith dataset for finding outcomes. Default: openswe-reviewer-outcomes
REVIEWER_OUTCOMES_DATASET=""
```

</details>

<details id="rotating-token_encryption_key">
<summary><strong>Rotating <code>TOKEN_ENCRYPTION_KEY</code></strong></summary>

`TOKEN_ENCRYPTION_KEY` accepts either a single Fernet key or a comma- or newline-separated **ordered list of keys, most-recent-first**. New writes always encrypt under the first key; reads try every key in order. To rotate without invalidating already-stored GitHub tokens:

1. Generate a new key: `openssl rand -base64 32`.
2. Prepend it to `TOKEN_ENCRYPTION_KEY`, keeping the old key second:
   ```
   TOKEN_ENCRYPTION_KEY="<new_key>,<old_key>"
   ```
   Restart the server. New encryptions use `<new_key>`; existing ciphertexts still decrypt against `<old_key>`.
3. Let active threads cycle (each fresh OAuth flow re-encrypts under the new key). After every active thread has re-authed, drop the old key:
   ```
   TOKEN_ENCRYPTION_KEY="<new_key>"
   ```
   Any thread still holding ciphertext under `<old_key>` will fail to decrypt and the user will be re-prompted to authenticate — same UX as if the thread had never authed.

</details>

<details id="production-deployment">
<summary><strong>Production deployment</strong></summary>

Production runs the backend and, optionally, the dashboard separately.

**Backend — standalone Docker:** the root `Dockerfile` builds a production LangGraph API server image for Open SWE. It is not the sandbox image.

```bash
docker build -t open-swe .

docker run \
  --env-file .env \
  -p 8123:8000 \
  --add-host=host.docker.internal:host-gateway \
  -e DATABASE_URI="postgres://postgres:postgres@host.docker.internal:5432/postgres?sslmode=disable" \
  -e REDIS_URI="redis://host.docker.internal:6379" \
  -e LANGGRAPH_AUTH_TYPE="noop" \
  -e LANGGRAPH_URL="https://<your-backend-url>" \
  open-swe
```

The example above assumes Postgres and Redis run on the Docker host. `host.docker.internal` only resolves automatically on Docker Desktop, so `--add-host=host.docker.internal:host-gateway` is what makes it work on a plain Linux Docker Engine. If Postgres and Redis run as their own containers, drop the flag and point `DATABASE_URI` / `REDIS_URI` at their service names on a shared Docker network instead.

Set the variables from your `.env`, plus the standalone Agent Server requirements: `DATABASE_URI`, `REDIS_URI`, `LANGSMITH_API_KEY` (unless tracing is disabled for your deployment), and `LANGGRAPH_CLOUD_LICENSE_KEY` for the production LangGraph server. Expose the container's port `8000` through your ingress. Do not use scale-to-zero hosting; background runs rely on Redis/Postgres-backed workers staying available. If the built-in LangGraph API routes are reachable from the public internet, put the service behind a private network, API gateway, or custom LangGraph auth before using `LANGGRAPH_AUTH_TYPE=noop`.

Set `LANGGRAPH_URL` to the public backend URL so webhooks (and the dashboard) can create runs against this same server. Update your webhook URLs (GitHub App, Slack, Linear) to the production URL.

**Backend — LangGraph Platform:** alternatively, push your code to a GitHub repository, connect the repo to LangGraph Platform, set the same environment variables in the deployment config, and use the hosted deployment URL for `LANGGRAPH_URL` and webhook callbacks. The platform injects `LANGSMITH_API_KEY` for tracing; Open SWE's own LangSmith calls keep using `LANGSMITH_API_KEY_PROD`.

**Dashboard** — the `ui/` app builds to a Nitro server that renders routes on request. Set `DASHBOARD_API_URL` in its environment to your hosted backend URL; it is read per request, so one image serves any backend. Browser requests to `/dashboard/api/*` and webhook deliveries to `/webhooks/*` are proxied to it, and server renders call it directly with the request's `osw_session` cookie forwarded.

Requests are therefore **same-origin**: set both `DASHBOARD_API_BASE_URL` and the GitHub App dashboard callback URL to the dashboard origin (for example, `https://your-dashboard.vercel.app/dashboard/api/auth/callback`). The OAuth callback response then sets the `osw_session` cookie on the dashboard host, and later `/dashboard/api/*` requests include it. The dashboard GitHub App callback must be `<DASHBOARD_API_BASE_URL>/dashboard/api/auth/callback`.

Alternatively, you can have the browser call the backend cross-origin: set `VITE_DASHBOARD_API_BASE_URL` to the hosted backend origin, set `DASHBOARD_API_BASE_URL` to that same backend origin, and — because the frontend now lives on an origin other than `DASHBOARD_BASE_URL`'s if they differ — include it in `DASHBOARD_ALLOWED_ORIGINS`. Keep `DASHBOARD_API_URL` pointed at the same backend so server renders and the webhook proxy reach it too. In this mode `osw_session` belongs to the backend's origin, so the dashboard's own requests never carry it and the session is resolved on the client instead — pages render unauthenticated and fill in after hydration.

The `langgraph.json` at the project root defines the graphs and HTTP app baked into the image.

</details>

## Advanced overrides and deprecated variables

None of these are needed for a normal installation. Overrides pin a value Open SWE would otherwise discover; deprecated variables keep working during a transition period and log a warning at startup.

| Variable | Status | Behavior without it |
|---|---|---|
| `GITHUB_APP_INSTALLATION_ID` | Override | Installation resolved from the repository/organization of the run; if none, the app's only installation is used. With several installations and no context, token minting is skipped with a warning naming the accounts. |
| `SLACK_BOT_USER_ID`, `SLACK_BOT_USERNAME` | Override | Discovered from `SLACK_BOT_TOKEN` via `auth.test` and `users.info` at startup (retried on the first webhook if Slack was unreachable). Setting the user id disables discovery. |
| `LANGSMITH_TENANT_ID_PROD` | Override | Read from the tracing project's metadata (or the first project in the workspace). |
| `LANGSMITH_URL_PROD` | Override | Derived from `LANGSMITH_ENDPOINT` (`https://smith.langchain.com` for the default endpoint; the API host minus `/api` for self-hosted LangSmith). |
| `LANGSMITH_ENDPOINT`, `LANGSMITH_ENDPOINT_PROD` | Override | `https://api.smith.langchain.com`. Set for self-hosted or regional LangSmith. |
| `DEFAULT_SANDBOX_SNAPSHOT_ID` | Override | LangSmith default snapshot, unless an admin set a base snapshot. |
| `SANDBOX_TYPE` | Override | `langsmith`. |
| `LANGGRAPH_URL` | Override | `http://localhost:2024`. Set to the public backend URL in production. |
| `GITHUB_APP_ID` | Deprecated | The JWT issuer is `GITHUB_APP_CLIENT_ID`; the app id is only read when the client id is unset. |
| `DEFAULT_REPO_OWNER`, `DEFAULT_REPO_NAME` | Deprecated | Seed the team default repository when no team setting has been saved. Use **Admin → Team settings → Default Repository**. |
| `SLACK_REPO_OWNER`, `SLACK_REPO_NAME` | Deprecated | Slack-only fallback repository; the team default repository replaces it. |
| `LANGSMITH_TRACING_PROJECT_ID_PROD` | Deprecated | Trace links resolve the `open-swe-agent` / `open-swe-review` projects by name. |
| `LANGCHAIN_PROJECT` | Deprecated | No effect: each graph pins its own tracing project. |

## Troubleshooting

### Webhook not receiving events

- Verify ngrok is running and the URL matches what's configured in GitHub/Slack/Linear
- Check the ngrok web inspector at `http://localhost:4040` for incoming requests
- Ensure you enabled the correct event types (Issue comment and the PR review events for GitHub, `app_mention` for Slack, Comments → Create for Linear)
- **Webhook secrets are required** — if `GITHUB_WEBHOOK_SECRET`, `SLACK_SIGNING_SECRET`, or `LINEAR_WEBHOOK_SECRET` is not set, all requests to that endpoint are rejected with 401

### GitHub authentication errors

- Verify `GITHUB_APP_CLIENT_ID` and `GITHUB_APP_PRIVATE_KEY` are set correctly; the private key must include the full `-----BEGIN RSA PRIVATE KEY-----` and `-----END RSA PRIVATE KEY-----` lines
- Ensure the GitHub App is installed on the target repositories
- `The GitHub App has N installations` in the logs: runs without repository context cannot pick an installation. Set `GITHUB_APP_INSTALLATION_ID`, or make sure Slack messages name a repository (or a team default repository is set)
- `The GitHub App is not installed on any account yet`: complete step 3c

### Slack mentions are ignored

- Ensure the bot is invited to the channel and the message `@`-mentions it
- `Slack auth.test failed: invalid_auth` in the logs means `SLACK_BOT_TOKEN` is wrong or the app was not installed to the workspace; reinstall and copy the new `xoxb-` token
- Check the startup log line `config: Slack: …` for a missing variable

### Dashboard login fails or won't stay logged in

- `500 GITHUB_APP_CLIENT_ID not configured` (or client secret): set `GITHUB_APP_CLIENT_ID` / `GITHUB_APP_CLIENT_SECRET` and `DASHBOARD_JWT_SECRET` (the [Dashboard](#dashboard-web-ui) add-on).
- OAuth `redirect_uri` mismatch: the GitHub App must list `<DASHBOARD_API_BASE_URL>/dashboard/api/auth/callback` as a callback URL. Locally that's `http://localhost:2024/dashboard/api/auth/callback`.
- Login redirects but the session doesn't stick: this is almost always a cookie problem. Locally, keep `DASHBOARD_API_BASE_URL` on `http://` (so cookies are `SameSite=Lax`); in prod use `https://` for both API and frontend.
- `403 CSRF check failed` on saves: the request's `Origin` is not `DASHBOARD_BASE_URL` or one of `DASHBOARD_ALLOWED_ORIGINS`. Fix `DASHBOARD_BASE_URL`, or add the extra frontend origin to `DASHBOARD_ALLOWED_ORIGINS`.
- Login rejected with an org error: `ALLOWED_GITHUB_ORGS` gates dashboard login (and requires the App's Organization → Members: Read-only permission).
- Admin pages 403: add your GitHub login or email to `CONFIGURED_ADMINS`.

### Dashboard UI can't reach the backend

- Confirm the backend is running via `make dev` on `:2024` (not `make run` on `:8000`).
- Confirm the dev server is proxying: `curl -i http://localhost:3000/dashboard/api/me` should return the backend's `401`, not an HTML page. If the backend is on another port, export `DASHBOARD_API_URL` before `make web` or `pnpm run dev`.

### Sandbox creation failures

- Verify `LANGSMITH_API_KEY_PROD` is set and valid
- Check LangSmith sandbox quotas in your workspace settings
- If you see `Failed to create sandbox from snapshot '<id>'`, confirm the snapshot exists in your workspace and has status `ready`; clear the admin **Base snapshot** or `DEFAULT_SANDBOX_SNAPSHOT_ID` to fall back to the LangSmith default snapshot
- If you get a 403 Forbidden error on the sandbox endpoints, your LangSmith workspace may not have sandbox access enabled — contact LangSmith support

### Agent not responding to comments

- For GitHub: ensure the comment or issue contains `@openswe` (case-insensitive), and the commenter has a user mapping (Admin → User mappings) or self-onboarded
- For Linear: ensure the comment contains `@openswe` (case-insensitive)
- For Slack: ensure the bot is invited to the channel and the message is an `@mention`
- Check server logs for webhook processing errors

### Token encryption errors

- Ensure `TOKEN_ENCRYPTION_KEY` is set (`make setup` generates it; by hand: `openssl rand -base64 32`)
- The key must be a valid 32-byte Fernet-compatible base64 string
- For key rotation, `TOKEN_ENCRYPTION_KEY` may be a comma- or newline-separated list of keys (most-recent-first). See [Rotating `TOKEN_ENCRYPTION_KEY`](#rotating-token_encryption_key).
