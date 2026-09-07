# Installation Guide

Open SWE is one deployment: a LangGraph server that runs the graphs (`agent`, `reviewer`, `analyzer`, `chat`, `scheduler`), the FastAPI app (`agent.webapp:app`) that owns the webhooks and the dashboard API, and the web dashboard, served from the same origin at `/`. Locally that is `make dev` on `http://localhost:2024`; on LangGraph Platform it is the deployment URL. Webhooks, the dashboard, GitHub login, and the API all share that one URL, so there is no second frontend deploy and no cross-origin cookie or CORS setup.

What a deployment needs:

| Value | How you get it |
|---|---|
| `LANGSMITH_API_KEY` | LangSmith → Settings → API Keys. LangGraph Platform injects it. |
| A model key: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GOOGLE_API_KEY`; or `LANGSMITH_GATEWAY_API_KEY` to route through the LangSmith LLM Gateway | Your provider, or a LangSmith key with `gateway:invoke` |
| `GITHUB_APP_ID`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_APP_INSTALLATION_ID` | `scripts/create_apps.py` creates the App and writes all six (step 3) |
| Slack, optional: `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`, `SLACK_APP_ID`, `SLACK_BOT_USER_ID`, `SLACK_BOT_USERNAME`, `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET` | the same script with `--slack` ([Slack](#slack)) |
| `TOKEN_ENCRYPTION_KEY`, `DASHBOARD_JWT_SECRET` | Two random secrets you generate (step 4) |
| `CONFIGURED_ADMINS` | Your GitHub login (step 4) |
| `LANGGRAPH_URL` | The deployment's public URL; defaults to `http://localhost:2024` |

Slack and Linear are optional triggers; see [Optional add-ons](#optional-add-ons). Every variable Open SWE reads is declared in `agent/config.py` with its description and default; that file is the complete reference.

## Prerequisites

- **Python 3.14+** and [uv](https://docs.astral.sh/uv/)
- [LangGraph CLI](https://docs.langchain.com/langsmith/cli) (installed by `uv sync`)
- Node 22.22.2+ and [pnpm](https://pnpm.io/) to build the dashboard locally (LangGraph Platform builds it for you)
- For local development with GitHub or Slack triggers: a free [ngrok](https://ngrok.com/) account. It comes with one static domain, so the URL you register with GitHub and Slack survives restarts (step 2).

## 1. Clone and install

```bash
git clone https://github.com/langchain-ai/open-swe.git
cd open-swe
uv venv
source .venv/bin/activate
uv sync --all-extras
```

## 2. Pick the public URL, and set up ngrok for local development

The GitHub App from step 3 delivers webhooks to `<URL>/webhooks/github` and lists `<URL>/dashboard/api/auth/callback` as a login callback; a Slack app posts events to `<URL>/webhooks/slack`. Both need a public HTTPS URL that does not change, so pick it first.

- **LangGraph Platform:** the deployment URL, `https://<name>-<hash>.<region>.langgraph.app`. Create the deployment first (see [Deploy](#7-deploy)) so you have it, and skip the rest of this step.
- **Local development without GitHub or Slack triggers:** `http://localhost:2024`. Runs started from the dashboard work; GitHub cannot deliver webhooks to localhost, so the App is created without one and comment triggers wait until you have a public URL.
- **Local development with GitHub or Slack triggers:** a tunnel to port 2024 on a hostname that stays the same across restarts. The free ngrok plan gives you one:

  1. Sign up at [dashboard.ngrok.com](https://dashboard.ngrok.com/signup) and install the agent (`brew install ngrok`, or the download the dashboard offers).
  2. Connect the agent to your account with the `ngrok config add-authtoken …` command shown under **Getting Started → Your Authtoken**.
  3. Under **Domains**, claim the free static domain. It looks like `<name>.ngrok-free.dev`.
  4. Start the tunnel and leave it running while you develop:

     ```bash
     make tunnel NGROK_DOMAIN=<name>.ngrok-free.dev   # or export NGROK_DOMAIN once in your shell
     ```

  `make tunnel` runs `ngrok http 2024` on that domain with [`examples/ngrok/webhooks-only.yml`](../examples/ngrok/webhooks-only.yml) as its traffic policy, so only `/webhooks/*` is reachable from the internet. That matters: `langgraph dev` has no authentication, so a bare tunnel would expose your threads, runs, and dashboard API to anyone who finds the hostname. Everything else stays on `http://localhost:2024`, where you keep opening the dashboard; the tunnel is only for GitHub and Slack. Check it with `curl https://<name>.ngrok-free.dev/webhooks/slack` once the backend is up (step 5): the backend answers `{"status":"ok", …}`, while `/ok` gets ngrok's own 404.

  Any other tunnel works the same way as long as it forwards to port 2024 on a fixed hostname; restrict it to `/webhooks/*` if it can. A throwaway `cloudflared tunnel --url http://localhost:2024` is fine for a quick test, but its hostname changes every run and you would edit the App's webhook and Slack's URLs each time.

  Use `https://<name>.ngrok-free.dev` as `--url` in the steps below.

## 3. Create a GitHub App

Open SWE authenticates as a [GitHub App](https://docs.github.com/en/apps/creating-github-apps) to clone repositories, push branches, open pull requests, and sign users in to the dashboard. The script creates the App through GitHub's manifest flow with the right permissions, events, webhook, and callback, then writes the credentials where the deployment reads them. It never prints them. Add `--slack` to create the Slack app in the same run (public URL only; see [Slack](#slack)).

```bash
# local: writes the credentials into .env
uv run python scripts/create_apps.py --url https://<your-domain>.ngrok-free.dev --env-file .env --callback-url http://localhost:2024

# LangGraph Platform: writes them into the deployment's environment (a new revision rolls out)
LANGSMITH_API_KEY=<key for the workspace that owns the deployment> \
uv run python scripts/create_apps.py --url https://<your-deployment>.langgraph.app --deployment <deployment id>
```

What happens:

1. Your browser opens on GitHub with the App preconfigured. Add `--org <name>` to create it under an organization instead of your account, and `--name` to change the App name, which has to be unique across GitHub. Click **Create GitHub App**.
2. GitHub sends the new App's credentials to the script, which writes `GITHUB_APP_ID`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `GITHUB_APP_PRIVATE_KEY`, and `GITHUB_WEBHOOK_SECRET`.
3. The browser opens the App's install page. Install it on the account and repositories Open SWE may work in. The script records the installation as `GITHUB_APP_INSTALLATION_ID` and exits. Pass `--skip-install` to do this later by hand: the installation id is the number at the end of `https://github.com/settings/installations/<id>`.

`--callback-url http://localhost:2024` registers a second login callback, so the dashboard you open at `http://localhost:2024` can sign in while webhooks arrive through the tunnel; the tunnel URL's own callback is what a deployment would use. When `--url` is `http://localhost:2024` itself, the App is created without a webhook; add one under the App's settings later (URL `<public URL>/webhooks/github`, the `GITHUB_WEBHOOK_SECRET` from your `.env`, and the events below).

<details>
<summary>Creating the App by hand instead</summary>

Go to **GitHub Settings → Developer settings → [GitHub Apps](https://github.com/settings/apps) → New GitHub App** and fill in:

- **Callback URL**: `<URL>/dashboard/api/auth/callback` (one per line; add `http://localhost:2024/dashboard/api/auth/callback` for local logins)
- **Request user authorization (OAuth) during installation**: off
- **Webhook URL**: `<URL>/webhooks/github`, with a secret from `openssl rand -hex 32` saved as `GITHUB_WEBHOOK_SECRET`
- **Repository permissions**:
  - Contents: Read & write
  - Pull requests: Read & write
  - Issues: Read & write
  - Checks: Read & write — reports an "Open SWE Review" check run on PRs while an auto-review runs and lets `/baby-sit` read third-party CI conclusions. Without it, check-run creation fails (logged, best-effort), reviews still work, and `/baby-sit` fails closed when it cannot read the complete check set.
  - Commit statuses: Read-only — required for `/baby-sit` to evaluate the complete PR status set, including integrations that report via legacy commit statuses.
  - Actions: Read-only — optional for CI diagnostics and log access. Grant **Read & write** only to enable `/baby-sit` to rerun evidence-backed flaky GitHub Actions jobs; existing installations must approve the elevation, and the token could then also cancel or delete runs.
  - Workflows: Read & write — lets Open SWE push branches containing explicitly requested GitHub Actions workflow changes.
  - Metadata: Read-only
- **Organization permissions**: Members: Read-only — verifies org membership for dashboard login and LangSmith trace-tool access when `ALLOWED_GITHUB_ORGS` is set. Without it that check fails closed.
- **Subscribe to events**: Issue comment, Pull request review, Pull request review comment, Check run, Check suite, Workflow run (the last three give `/baby-sit` immediate failure detection), and Status (optional; legacy commit-status integrations).

After creating it, collect the **App ID** (`GITHUB_APP_ID`), **Client ID** (`GITHUB_APP_CLIENT_ID`), a generated **client secret** (`GITHUB_APP_CLIENT_SECRET`), and a generated **private key** (`GITHUB_APP_PRIVATE_KEY`, the whole `.pem` including the BEGIN and END lines). Install the App and take the installation id from the URL.

</details>

## 4. Write the rest of `.env`

```bash
LANGSMITH_API_KEY=""            # LangSmith → Settings → API Keys
LANGSMITH_TRACING="true"        # trace runs to LangSmith; LangGraph Platform sets this for you
LANGSMITH_PROJECT=""            # optional project for traces and "View trace" links; default "default", Platform sets the deployment name

ANTHROPIC_API_KEY=""            # or OPENAI_API_KEY / GOOGLE_API_KEY, or LANGSMITH_GATEWAY_API_KEY for the LLM Gateway

TOKEN_ENCRYPTION_KEY=""         # openssl rand -base64 32  (encrypts stored GitHub and Slack tokens)
DASHBOARD_JWT_SECRET=""         # openssl rand -hex 32     (signs the session cookie and OAuth state)
CONFIGURED_ADMINS=""            # your GitHub login or email; admins see the Admin pages
```

`LANGSMITH_API_KEY` is also what sandboxes and trace links use. Trace links find your workspace through the key and the project by name, so no tenant or project ids are needed; `LANGSMITH_TENANT_ID` remains an override. The LLM Gateway is on whenever `LANGSMITH_GATEWAY_API_KEY` is set; `LANGSMITH_GATEWAY_ENABLED` overrides that either way, and admins can toggle it per team in the dashboard.

## 5. Build the dashboard and start

```bash
make build-dashboard   # pnpm install + Vite build into ui/.output/public
make dev               # langgraph dev on http://localhost:2024, serving the API and the dashboard
```

`langgraph dev` serves the graphs, the FastAPI app, and the dashboard build together on port 2024. The bundled UI is a static build, so rebuild it when you pull UI changes, or skip `make build-dashboard` if you only need webhooks and the API. It reloads on code changes only: after editing `.env`, restart it.

**Working on the UI?** Have the backend front the Vite dev server instead of serving a build:

```bash
make dev-ui   # Vite on :3000 and the backend on :2024 forwarding UI requests to it, in one terminal
```

`make dev-ui` runs `make web` and `make dev` side by side, the backend with `DASHBOARD_DEV_SERVER_URL=http://localhost:3000`; Ctrl-C stops both. Open `http://localhost:2024` as usual: the page, its modules, and hot module replacement come from Vite, while `/dashboard/api/*` and the LangGraph routes stay with the backend. Nothing else changes, because the browser never leaves port 2024. The HMR WebSocket connects straight to Vite's port; the UI's Vite config points the client there. Opening Vite on port 3000 directly also works but needs the extra settings in [Dashboard on its own origin](#dashboard-on-its-own-origin).

| Endpoint | Purpose |
|---|---|
| `/` | Dashboard |
| `POST /webhooks/github` | GitHub issue, PR, and comment webhooks |
| `POST /webhooks/slack`, `POST /webhooks/slack/interactivity` | Slack events and Block Kit interactions (add-on) |
| `POST /webhooks/linear` | Linear comment webhooks (add-on) |
| `GET /dashboard/api/auth/login`, `GET /dashboard/api/auth/callback` | GitHub login |
| `/dashboard/api/*` | Dashboard API |
| `GET /ok`, `GET /health` | Health checks |

> `make run` serves the FastAPI app alone with uvicorn on port 8000, without the LangGraph runtime. Nothing that creates runs works there; use `make dev`.

## 6. Verify it works

**Dashboard.** Open `http://localhost:2024`, click **Sign in with GitHub**, and you should land logged in. With your login in `CONFIGURED_ADMINS`, the **Admin** pages (Team settings, User mappings, Sandbox, Environments, …) appear. Set **Admin → Team settings → Default repository** so runs that name no repository have somewhere to go. Start a task from the composer.

**GitHub.** Signing in once is also what lets GitHub-triggered runs act as you: they run as the commenting user and need the token the sign-in stored; an unmapped commenter is skipped with a warning in the server log. Comment `@openswe what files are in this repo?` on an issue in a repository where the App is installed. Within a few seconds you should see a 👀 reaction, a run in your LangSmith project, and a reply comment.

## 7. Deploy

**LangGraph Platform.** Connect the repository to a new deployment in LangSmith → Deployments. The image build bundles the dashboard (the `dockerfile_lines` in `langgraph.json`), so the deployment URL serves the UI at `/` and the API beneath it; a failed UI build is logged and the backend still deploys. The platform injects `LANGSMITH_API_KEY`, `LANGSMITH_TRACING`, and `LANGSMITH_PROJECT`. Set as environment variables:

```bash
LANGGRAPH_URL="https://<your-deployment>.langgraph.app"   # the deployment's own URL
ANTHROPIC_API_KEY=""                                       # or another model key / LANGSMITH_GATEWAY_API_KEY
TOKEN_ENCRYPTION_KEY=""
DASHBOARD_JWT_SECRET=""
CONFIGURED_ADMINS=""
```

Then run `scripts/create_apps.py --url <deployment URL> --deployment <id>` as in step 3 (the id is in the deployment page's URL); it writes the GitHub App credentials into the deployment's environment and the platform rolls out a new revision. Reusing an App you created for local development works too: add the deployment URL to its webhook URL and callback URLs and copy the same six variables into the deployment.

Give each deployment its own GitHub App, or at least a distinct mention handle (`OPEN_SWE_MENTION_TAGS`) when several share a GitHub organization.

**Standalone Docker.** The root `Dockerfile` builds a production LangGraph API server image (not the sandbox image):

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

The example assumes Postgres and Redis run on the Docker host; `--add-host` is what makes `host.docker.internal` resolve on a plain Linux Docker Engine. If they run as containers, drop the flag and point `DATABASE_URI` / `REDIS_URI` at their service names on a shared network. Add the standalone Agent Server requirements: `DATABASE_URI`, `REDIS_URI`, `LANGSMITH_API_KEY`, and `LANGGRAPH_CLOUD_LICENSE_KEY`. Expose port `8000` through your ingress, and do not use scale-to-zero hosting: background runs rely on the Redis- and Postgres-backed workers staying up. If the built-in LangGraph API routes are reachable from the public internet, put the service behind a private network, API gateway, or custom LangGraph auth before using `LANGGRAPH_AUTH_TYPE=noop`. Bundle the dashboard by building it (`make build-dashboard`) before `docker build`, or set `DASHBOARD_STATIC_DIR` to a directory holding the build.

---

## Optional add-ons

Open a section when you want that feature; everything above keeps working without it.

<details id="slack">
<summary><strong>Slack</strong></summary>

Slack only delivers events to a public HTTPS URL, so this needs the ngrok domain or deployment URL from step 2.

**With the script.** Generate an app configuration token under **Your App Configuration Tokens** on [api.slack.com/apps](https://api.slack.com/apps) (valid twelve hours), then:

```bash
uv run python scripts/create_apps.py --url https://<your-url> --env-file .env --no-github --slack
```

It pastes the manifest below into Slack's `apps.manifest.create` for you, opens the app's install page, and, because Slack has no way to hand the bot token back to a script, asks you to paste the **Bot User OAuth Token** shown after installing. From that it discovers the bot's user id and handle and writes all seven Slack variables (the app id binds Investigate to the app; the client id and secret enable "Sign in with Slack" below). Drop `--no-github` to create both apps in one run; add `--slack-code-channels` for the code-channels manifest.

**By hand.**

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From a manifest**, and paste the manifest below with `<your-url>` replaced by the URL from step 2.

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
            "https://<your-url>/dashboard/api/slack/callback"
        ],
        "scopes": {
            "bot": [
                "reactions:write",
                "app_mentions:read",
                "channels:history",
                "channels:read",
                "channels:join",
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
            "request_url": "https://<your-url>/webhooks/slack",
            "bot_events": [
                "app_mention",
                "channel_created",
                "channel_rename",
                "channel_archive",
                "message.channels",
                "message.im",
                "message.mpim"
            ]
        },
        "interactivity": {
            "is_enabled": true,
            "request_url": "https://<your-url>/webhooks/slack/interactivity"
        },
        "org_deploy_enabled": false,
        "socket_mode_enabled": false,
        "token_rotation_enabled": false
    }
}
```

</details>

2. Install the app to your workspace.
3. Add to the environment:

```bash
SLACK_BOT_TOKEN=""        # OAuth & Permissions → Bot User OAuth Token (xoxb-...)
SLACK_SIGNING_SECRET=""   # Basic Information → App Credentials → Signing Secret
SLACK_APP_ID=""           # Basic Information → App ID (A...); Investigate accepts events only from this app
SLACK_BOT_USER_ID=""      # the bot's member id (open the bot's profile in Slack → ⋮ → Copy member ID)
SLACK_BOT_USERNAME=""     # the bot's handle, e.g. open-swe
```

Both Slack URLs must point at the Open SWE deployment, and Block Kit buttons only work with Interactivity enabled and pointed at `/webhooks/slack/interactivity`. Slack messages are routed to the thread's repository, a `repo:owner/name` token in the message, or the team default repository. Open SWE refuses Slack Connect channels (`is_ext_shared`) and fails closed when it cannot verify a channel.

**Verify:** invite the bot to a channel and mention it: `@Open SWE what's in the repo?`. The agent replies in a thread.

**"Sign in with Slack" account linking.** Lets a user link their Slack identity to their GitHub login from **My settings**, so Slack-triggered runs resolve to the right GitHub user through Slack's verified claims. Without it, an admin links people under **Admin → User mappings**. The manifest already registers the OIDC redirect; make sure the `openid`, `email`, and `profile` user scopes are available, then set `SLACK_CLIENT_ID` and `SLACK_CLIENT_SECRET` from **Basic Information → App Credentials**, and optionally `SLACK_TEAM_ID` (`T...`) to restrict linking to one workspace. When they are unset the link is simply hidden.

**Code channels (early access).** To enable Slack [code channels](https://api.slack.com/partners/code-channels), open **Admin → Slack integration**, turn on **Slack Code Channels**, copy the generated manifest, update the Slack app, and reinstall it. In a code channel the whole channel is one Open SWE session: it answers without an `@`-mention, replies at the channel level by default, reports session status, and keeps the context bar current; the `manage_code_channel` tool covers channel lifecycle, status, views, and canvases. This requires the `code_channels:manage` bot scope, the `agent_session_stopped` and `code_channel_action` bot events, and `features.code_channels.enabled`; `slash_command_url` delivers runtime-registered commands to the signed Open SWE endpoint. If your workspace is not enrolled, leave the toggle off.

</details>

<details id="linear">
<summary><strong>Linear</strong></summary>

Open SWE listens for Linear comments that mention `@openswe`.

1. **Settings → API → Webhooks → New webhook**: label `Open SWE`, URL `https://<your-url>/webhooks/linear`, a secret from `openssl rand -hex 32` saved as `LINEAR_WEBHOOK_SECRET`, and under **Data change events** only **Comments → Create**.
2. **Settings → API → Personal API keys → New API key** with **All access**, saved as `LINEAR_API_KEY`.
3. Map Linear teams and projects to repositories in `agent/linear/team_repo_map.py`:

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

A `repo:owner/name` token or GitHub URL in the comment overrides the mapping. **Verify:** comment `@openswe what files are in this repo?` on an issue in a mapped team.

</details>

<details id="dashboard-on-its-own-origin">
<summary><strong>Dashboard on its own origin (Vite dev server or separate frontend)</strong></summary>

`make dev-ui` (step 5) is the simple way to develop the UI. This section is for opening the Vite dev server on its own port, or deploying the dashboard separately from the backend.

```bash
pnpm install      # from the repo root: ui/ and desktop/ are one pnpm workspace
make web          # Vite on http://localhost:3000, proxying /dashboard/api/* to DASHBOARD_API_URL (default http://localhost:2024)
```

The browser now talks to `http://localhost:3000`, so the session cookie has to be set on that origin and the login callback has to return there:

```bash
DASHBOARD_BASE_URL="http://localhost:3000"       # the frontend origin; allowed for the CSRF check and post-login redirects
DASHBOARD_API_BASE_URL="http://localhost:3000"   # what browsers use for /dashboard/api/* and the OAuth callback
```

and the GitHub App needs `http://localhost:3000/dashboard/api/auth/callback` as a callback URL (`scripts/create_apps.py --callback-url http://localhost:3000`, or add it in the App's settings). Keep both URLs on `http://` locally so the cookie is `SameSite=Lax`.

Both variables default to `LANGGRAPH_URL` when the dashboard is bundled, which is why the single-origin setup needs neither. `DASHBOARD_ALLOWED_ORIGINS` lists **additional** origins that may call the API with credentials (preview deploys, a frontend on another host); credentialed CORS is only enabled when it is set, and `*` is rejected.

**A separate frontend deployment.** The `ui/` app also builds to a Nitro server (`ui/Dockerfile`) that renders on request. Set its `DASHBOARD_API_URL` to the backend URL; browser requests to `/dashboard/api/*` and webhook deliveries to `/webhooks/*` are proxied there, and server renders forward the `osw_session` cookie. Set `DASHBOARD_BASE_URL` and `DASHBOARD_API_BASE_URL` on the backend to the frontend origin and register `<frontend origin>/dashboard/api/auth/callback` on the GitHub App. To have the browser call the backend cross-origin instead, build the UI with `VITE_DASHBOARD_API_BASE_URL` set to the backend origin, keep `DASHBOARD_API_BASE_URL` on the backend origin, and add the frontend origin to `DASHBOARD_ALLOWED_ORIGINS`; the session is then resolved on the client after hydration.

**Mount prefix.** If the server runs under a LangGraph `http.mount_prefix`, the Platform image builds the UI for that prefix automatically; locally pass it to the build (`DASHBOARD_BASE_PATH=/<prefix>/ make build-dashboard`) and keep `LANGGRAPH_URL` on the mounted URL.

**Datadog RUM.** Set `VITE_DATADOG_APPLICATION_ID` and `VITE_DATADOG_CLIENT_TOKEN` when building. Optional: `VITE_DATADOG_SITE` (default `datadoghq.com`), `VITE_DATADOG_SERVICE` (default `open-swe-dashboard`), `VITE_DATADOG_ENV`, `VITE_DATADOG_VERSION`, `VITE_DATADOG_SESSION_SAMPLE_RATE` and `VITE_DATADOG_SESSION_REPLAY_SAMPLE_RATE` (default `100`). Session Replay masks all content and telemetry strips query strings and fragments. `VITE_` values are public in the bundle; use a client token, never an API or application key.

`pnpm run build`, `pnpm run typecheck`, and `pnpm run test` run across the workspace through Turborepo (`pnpm --filter open-swe-dashboard run <script>` scopes one); `pnpm run lint` (oxlint) and `pnpm run format` / `pnpm run format:check` (oxfmt) run once from the root over every JS and TS file.

**Voice dictation** in the composer uses your OpenAI configuration (`OPENAI_API_KEY`, optional `OPENAI_BASE_URL`); admins choose the transcription model on the Admin page.

</details>

<details id="desktop-app">
<summary><strong>Desktop app (experimental)</strong></summary>

The Electron app in `desktop/` includes the compiled dashboard UI. Run it next to the backend:

```bash
pnpm install                  # from the repo root
make dev                      # terminal 1
pnpm run dev:desktop          # terminal 2
```

Development connects to `http://localhost:2024`. For a hosted backend run `pnpm --dir desktop run start -- --backend-url=https://your-backend.example.com` or set `OPEN_SWE_BACKEND_URL`. `pnpm --dir desktop run pack` creates an unpacked application and `pnpm --dir desktop run dist` an installer. Packaged builds ask for the organization's backend URL on first launch and never default to the maintainers' deployment. The GitHub App must allow `<backend-url>/dashboard/api/auth/callback` for desktop login.

</details>

<details id="admin-api-credentials">
<summary><strong>Admin API credentials (CI and scripts)</strong></summary>

Admin-gated endpoints such as `PUT /dashboard/api/team-settings` and `PUT /dashboard/api/sandbox-settings` accept two credentials in place of the browser session cookie, both as `Authorization: Bearer`:

**GitHub Actions OIDC (preferred, no stored secret).** A workflow with `permissions: id-token: write` mints a short-lived token that GitHub signs and scopes to the repo, ref, and audience. Allowlist it on the deployment:

```bash
ADMIN_OIDC_SUBJECTS="acme/sandbox-images"                       # any workflow/ref in this repo
# or pin the ref with a full subject:
# ADMIN_OIDC_SUBJECTS="repo:acme/sandbox-images:ref:refs/heads/main"
ADMIN_OIDC_AUDIENCE="open-swe"                                  # optional; this is the default
```

`ADMIN_OIDC_SUBJECTS` is the on/off switch. Entries containing `:` match the token's `sub` claim, `owner/repo` entries match its `repository` claim, and the audience is verified either way. Anyone who can run a workflow on an allowlisted repo/ref gets admin on these endpoints, so keep the list to internal repos.

**Admin personal access token.** The token only needs to identify its owner (`GET /user`), whose login or email must be in `CONFIGURED_ADMINS`. Matching by email needs a token that can read email addresses when the account's email is not public. Prefer a machine user.

`secrets.GITHUB_TOKEN` works for neither. `examples/github-actions/set-base-snapshot.yml` is a copy-ready workflow using the OIDC path.

</details>

<details id="custom-sandbox-snapshot-and-environments">
<summary><strong>Custom sandbox snapshot and environments</strong></summary>

Each run executes in an isolated LangSmith sandbox booted from a **snapshot**. Without configuration that is LangSmith's root snapshot. Build your own (from a Docker image) when your repos need extra toolchains.

The image must carry `git`, `gh`, `sfw`, the Docker CLI, and the language runtimes, and live in a registry LangSmith can pull from. Run `sfw --version` while building to populate its binary cache, and set `SFW_SKIP_UPDATE_CHECK=1` at runtime. Open SWE authenticates `git` and `gh` through the LangSmith sandbox proxy with runtime-minted installation tokens, so no GitHub token belongs in the image.

Build a snapshot in the LangSmith UI (Sandboxes → Snapshots → New), via the SDK, or with the helper script:

```bash
uv run python scripts/create_sandbox_snapshot.py \
  --name open-swe-gh-cli-amd64 \
  --image johanneslangchain/open-swe-sandbox:gh-cli-amd64
```

Then set the UUID in the environment, or on **Admin → Sandbox → Base snapshot** (the stored value wins; clearing it falls back to the env var):

```bash
DEFAULT_SANDBOX_SNAPSHOT_ID="<snapshot-uuid>"
# Optional sizing and TTL overrides. Defaults: 128 GiB root FS, 4 vCPUs, 16 GiB RAM,
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

**Environments.** An environment pairs a prompt with a snapshot every run boots from, and can span several repos. Admins build one from an **admin thread** (the **Admin** toggle in the composer): the agent provisions its own sandbox, cloning repos, installing toolchains, and warming caches, and then captures it. The environment named `default` is the one runs use; any other name is a draft. Records live on the admin **Environments** page. With more than one environment, a picker appears in the composer, and a Slack thread can pick one with an `env:<name>` tag on its opening message (`@Open SWE env:staging fix the flaky test`); only the opening message can, because the sandbox is created once. Captures are named `openswe-environment-<name>`; set `ENVIRONMENT_SNAPSHOT_PREFIX` to replace the prefix when several deployments share a workspace. Resolution for a new sandbox: the run's environment, then the admin base snapshot, then `DEFAULT_SANDBOX_SNAPSHOT_ID`, then LangSmith's root snapshot.

**Other providers.** `SANDBOX_TYPE` defaults to `langsmith`; `modal`, `daytona`, `runloop`, `e2b`, and `local` are available with their own credentials, listed in [CUSTOMIZATION.md](CUSTOMIZATION.md). Only the LangSmith provider gets the GitHub proxy, so other providers see GitHub tokens inside the sandbox.

</details>

<details id="repository-allowlists-mention-handles-and-user-mapping">
<summary><strong>Repository allowlists, mention handles, and user mapping</strong></summary>

**Mention handles.** The handles this deployment answers to default to `@openswe,@open-swe,@openswe-dev`; set `OPEN_SWE_MENTION_TAGS` to change them. Handles match on a word boundary, so `@openswe` does not fire on `@openswe-staging`. Set `EXTRA_INTERNAL_BOT_LOGINS` (e.g. `openswe-staging[bot]`) to treat other Open SWE deployments' comments as internal rather than untrusted.

**Allowlists.**

```bash
ALLOWED_GITHUB_ORGS="langchain-ai,anthropics"                        # all repos in these orgs
ALLOWED_GITHUB_REPOS="some-user/their-repo,another-org/specific-repo"  # specific owner/repo pairs
PUBLIC_REPO_ORG_GATE=""   # single org whose members may trigger runs on *public* repos; empty = no gate
```

A GitHub or Linear webhook is accepted if the repo's org is in `ALLOWED_GITHUB_ORGS` **or** the `owner/repo` is in `ALLOWED_GITHUB_REPOS`; both empty allows everything. For Slack and dashboard requests, `ALLOWED_GITHUB_ORGS` also adds a prompt-level guard: editing a repository outside those orgs requires the user to name it with its full `https://github.com/<owner>/<repo>` URL. It also gates **dashboard login** to active members of the listed organizations, verified server-side with the installation token and failing closed on any API error; install the App in every listed organization and grant **Organization → Members: Read-only**. When team LangSmith credentials are connected, every active member of a listed organization can use the read-only LangSmith trace tools, so only list organizations whose full membership may see team-level trace data.

**User mapping.** Which GitHub users can trigger the agent is controlled by the user mapping (GitHub login ⇄ work email ⇄ optional Slack ID) in the LangGraph Store, managed under **Admin → User mappings**. Signing in to the dashboard records a mapping for that user. An unmapped person who tags Open SWE in Slack gets a run with the GitHub App's installation permissions and a "link your GitHub account" prompt; completing the org-gated login records a `self` mapping.

**Default repository.** Runs that name no repository use **Admin → Team settings → Default repository**, seeded from `DEFAULT_REPO_OWNER` / `DEFAULT_REPO_NAME` when set; `SLACK_REPO_OWNER` / `SLACK_REPO_NAME` are a Slack-only fallback.

</details>

<details id="models-gateway-search-and-reviewer">
<summary><strong>Models, LLM Gateway, web search, and reviewer settings</strong></summary>

```bash
ANTHROPIC_API_KEY=""
OPENAI_API_KEY=""                      # OpenAI models and dashboard voice dictation
# OPENAI_BASE_URL="https://api.openai.com/v1"  # optional OpenAI-compatible base URL
GOOGLE_API_KEY=""                      # google_genai: models
FIREWORKS_API_KEY=""                   # fireworks: models
BASETEN_API_KEY=""                     # Baseten models when not using the gateway
LLM_MODEL_ID=""                        # default model in provider:model form (see CUSTOMIZATION.md)

LANGSMITH_GATEWAY_API_KEY=""           # LangSmith key with gateway:invoke; enables the LLM Gateway
LANGSMITH_GATEWAY_ENABLED=""           # force the gateway on or off regardless of the key

EXA_API_KEY=""                         # enables the web search tool (https://dashboard.exa.ai)
REVIEWER_OUTCOMES_DATASET=""           # LangSmith dataset for reviewer outcomes; default openswe-reviewer-outcomes
```

</details>

<details id="rotating-token_encryption_key">
<summary><strong>Rotating <code>TOKEN_ENCRYPTION_KEY</code></strong></summary>

`TOKEN_ENCRYPTION_KEY` accepts a single Fernet key or a comma- or newline-separated **ordered list, most recent first**. Writes use the first key; reads try every key in order.

1. Generate a new key: `openssl rand -base64 32`.
2. Prepend it, keeping the old key second: `TOKEN_ENCRYPTION_KEY="<new_key>,<old_key>"`, and restart.
3. Once every active user has signed in again (each fresh OAuth flow re-encrypts under the new key), drop the old key. Anything still encrypted under it fails to decrypt and that user is asked to sign in again.

</details>

## Troubleshooting

### Webhook not receiving events

- The URL configured in GitHub, Slack, or Linear must be the deployment's URL (locally, your ngrok domain), and the tunnel (`make tunnel`) must be running against the port the backend listens on; GitHub shows each delivery and its response under the App's **Advanced** tab, and ngrok's inspector at `http://localhost:4040` shows what arrived. With the webhooks-only policy, ngrok itself answers 404 for anything outside `/webhooks/*`, so test with `/webhooks/slack`, not `/ok`.
- Restart the backend after changing `.env`: `langgraph dev` reloads on code changes only, so a new `GITHUB_WEBHOOK_SECRET` or `SLACK_SIGNING_SECRET` is not picked up until then, and every delivery is rejected as `Invalid signature` in the meantime. Slack then needs **Retry** on its Request URL under **Event Subscriptions**.
- Enable the right events: Issue comment and the pull request review events for GitHub, `app_mention` for Slack, Comments → Create for Linear.
- Webhook secrets are required: without `GITHUB_WEBHOOK_SECRET`, `SLACK_SIGNING_SECRET`, or `LINEAR_WEBHOOK_SECRET`, every request to that endpoint is rejected with 401.

### GitHub authentication errors

- Check `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, and `GITHUB_APP_INSTALLATION_ID`. The private key must include the full `-----BEGIN RSA PRIVATE KEY-----` and `-----END RSA PRIVATE KEY-----` lines; in `.env` write it as one double-quoted line with `\n` escapes, which is what the script does.
- Make sure the App is installed on the target repositories.

### Dashboard login fails or won't stay logged in

- `500 GITHUB_APP_CLIENT_ID not configured` (or client secret): set `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, and `DASHBOARD_JWT_SECRET`.
- `redirect_uri is not associated with this application`: the App must list `<URL you opened the dashboard on>/dashboard/api/auth/callback`; locally `http://localhost:2024/dashboard/api/auth/callback`. Add it in the App's settings.
- Login redirects but the session does not stick: keep local URLs on `http://` (cookie `SameSite=Lax`); in production use `https://` and open the dashboard on `LANGGRAPH_URL` itself.
- `403 CSRF check failed` on saves: the request's `Origin` is neither `DASHBOARD_BASE_URL` (defaults to `LANGGRAPH_URL`) nor in `DASHBOARD_ALLOWED_ORIGINS`.
- Login rejected with an org error: `ALLOWED_GITHUB_ORGS` gates login and needs the App's Organization → Members permission.
- Admin pages 403: add your GitHub login or email to `CONFIGURED_ADMINS`.

### Dashboard shows the LangGraph JSON instead of the UI, or 404s at `/`

- There is no dashboard build: run `make build-dashboard`, or set `DASHBOARD_STATIC_DIR` to a directory holding one. On LangGraph Platform, check the build log for `dashboard build failed`.
- With the Vite dev server, `curl -i http://localhost:3000/dashboard/api/me` should return the backend's `401`, not HTML; otherwise export `DASHBOARD_API_URL` before `make web`.

### Sandbox creation failures

- `LANGSMITH_API_KEY` must be set and valid, and the workspace must have sandbox access (403 on the sandbox endpoints means it does not; contact LangSmith support).
- `Failed to create sandbox from snapshot '<id>'`: the snapshot must exist in that workspace with status `ready`; clear the admin **Base snapshot** or `DEFAULT_SANDBOX_SNAPSHOT_ID` to fall back to LangSmith's root snapshot.

### Agent not responding to comments

- GitHub: the comment must contain a configured handle (`@openswe` by default, case-insensitive), and the commenter must have signed in to the dashboard once; otherwise the log says `No email mapping for GitHub user`.
- Linear: the comment must contain the handle; Slack: the bot must be in the channel and `@`-mentioned.
- Check the server log for webhook processing errors.

### Token encryption errors

- `TOKEN_ENCRYPTION_KEY` must be set to a valid Fernet key (`openssl rand -base64 32`), or an ordered list of them; see [Rotating `TOKEN_ENCRYPTION_KEY`](#rotating-token_encryption_key).
