# Local Development

Run Open SWE on your machine: the backend and the dashboard on `http://localhost:2024`, with GitHub and Slack webhooks arriving through a tunnel. Deploying it for a team is the [installation guide](INSTALLATION.md).

## Prerequisites

- **Python 3.14+** and [uv](https://docs.astral.sh/uv/)
- [LangGraph CLI](https://docs.langchain.com/langsmith/cli) (installed by `uv sync`)
- Node 22.22.2+ and [pnpm](https://pnpm.io/) for the dashboard
- A free [ngrok](https://ngrok.com/) account, so GitHub and Slack can reach your local backend (step 3)
- A Slack workspace where you may create apps, and a GitHub account or organization where you may create a GitHub App

## 1. Clone and install

```bash
git clone https://github.com/langchain-ai/open-swe.git
cd open-swe
uv venv
source .venv/bin/activate
uv sync --all-extras
```

## 2. Create a GitHub App for your machine

Follow [Create a GitHub App](INSTALLATION.md#3-create-a-github-app) in the installation guide with these values, and install it on the repositories you want to test against:

- **Callback URL**: `http://localhost:2024/dashboard/api/auth/callback`. This is where GitHub sends the browser after "Sign in with GitHub" on your local dashboard.
- **Webhook URL**: `https://<name>.ngrok-free.dev/webhooks/github` with your ngrok domain from step 3, or leave the webhook off (untick **Active**) if you only start runs from the dashboard. GitHub cannot deliver to `localhost`.

Use a name of your own (GitHub App names are unique), and give it a distinct mention handle (`OPEN_SWE_MENTION_TAGS`) if a shared deployment already answers to `@openswe` in the same repositories.

## 3. Tunnel for webhooks

GitHub and Slack need a public HTTPS hostname that stays the same across restarts (skip this only if you will start runs from the dashboard alone). The free ngrok plan gives you one:

1. Sign up at [dashboard.ngrok.com](https://dashboard.ngrok.com/signup) and install the agent (`brew install ngrok`, or the download the dashboard offers).
2. Connect the agent to your account with the `ngrok config add-authtoken …` command shown under **Getting Started → Your Authtoken**.
3. Under **Domains**, claim the free static domain. It looks like `<name>.ngrok-free.dev`.
4. Start the tunnel and leave it running while you develop:

   ```bash
   make tunnel NGROK_DOMAIN=<name>.ngrok-free.dev   # or export NGROK_DOMAIN once in your shell
   ```

`make tunnel` runs `ngrok http 2024` on that domain with [`examples/ngrok/webhooks-only.yml`](../examples/ngrok/webhooks-only.yml) as its traffic policy, so only `/webhooks/*` is reachable from the internet. That restriction is not optional. Under `langgraph dev` the LangGraph API itself (`/threads`, `/runs`, `/assistants`, `/store`, …) has no authentication at all: the dashboard API checks its session cookie and the webhook endpoints check their signatures, but anyone who can reach port 2024 can read and create threads and runs. A tunnel that forwards the whole port publishes exactly that. Everything except the webhooks stays on `http://localhost:2024`, where you keep opening the dashboard. Check the policy once the backend is up (step 6): `curl https://<name>.ngrok-free.dev/webhooks/slack` answers `{"status":"ok", …}` from the backend, while `/ok` gets ngrok's own 404.

Use ngrok with this policy. A different tunnel is only an option if it can restrict the public paths to `/webhooks/*` the same way; one that forwards the whole port is not.

## 4. Create a Slack app for your machine

Slack delivers events to one URL per app, so a local backend needs its own Slack app rather than the one a shared deployment uses. Follow [Create the Slack app](INSTALLATION.md#5-create-the-slack-app) in the installation guide with your ngrok domain from step 3, `<name>.ngrok-free.dev`, as `<your-url>` (the manifest supplies the `https://`), and give it a name that says it is yours, for example `open-swe-<you>`; the bot's handle follows from it. Copy the four values it lists into `.env` in the next step.

Slack checks the events Request URL against a running backend. If you create the app before `make dev` is up, open **Event Subscriptions** afterwards and press **Retry**. The same applies whenever you change `SLACK_SIGNING_SECRET`: restart the backend, then Retry.

## 5. Write `.env`

Create `.env` in the repository root; `langgraph dev` loads it.

```bash
LANGSMITH_API_KEY=""            # LangSmith → Settings → API Keys; also used for sandboxes and trace links
LANGSMITH_TRACING="true"        # trace runs to LangSmith
LANGSMITH_PROJECT=""            # optional project for traces and "View trace" links; default "default"

ANTHROPIC_API_KEY=""            # any provider key, or LANGSMITH_GATEWAY_API_KEY for the LLM Gateway; see the installation guide

GITHUB_APP_ID=""                # step 2
GITHUB_APP_CLIENT_ID=""
GITHUB_APP_CLIENT_SECRET=""
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"   # one double-quoted line, \n between the PEM lines
GITHUB_WEBHOOK_SECRET=""
GITHUB_APP_INSTALLATION_ID=""

SLACK_BOT_TOKEN=""              # step 4: OAuth & Permissions → Bot User OAuth Token (xoxb-...)
SLACK_SIGNING_SECRET=""         # Basic Information → App Credentials
SLACK_BOT_USER_ID=""            # the bot's member id (bot profile → ⋮ → Copy member ID)
SLACK_BOT_USERNAME=""           # the bot's handle, e.g. open_swe_you

TOKEN_ENCRYPTION_KEY=""         # openssl rand -base64 32  (encrypts stored GitHub and Slack tokens)
DASHBOARD_JWT_SECRET=""         # openssl rand -hex 32     (signs the session cookie and OAuth state)
CONFIGURED_ADMINS=""            # your GitHub login or email; admins see the Admin pages
```

`LANGGRAPH_URL` defaults to `http://localhost:2024`, and `DASHBOARD_BASE_URL` / `DASHBOARD_API_BASE_URL` default to it, so none of the three is needed locally. Provider keys, the LLM Gateway, and how the running model is chosen are in [Model providers and API keys](INSTALLATION.md#4-model-providers-and-api-keys). Linear, if you use it, comes from the [Linear](INSTALLATION.md#linear) section of the installation guide, with your ngrok domain as the URL.

## 6. Run

```bash
make build-dashboard   # pnpm install + Vite build into ui/.output/public
make dev               # langgraph dev on http://localhost:2024, serving the API and the dashboard
```

`langgraph dev` serves the graphs, the FastAPI app, and the dashboard build together on port 2024. The bundled UI is a static build, so rebuild it when you pull UI changes, or skip `make build-dashboard` if you only need webhooks and the API. It reloads on code changes only: after editing `.env`, restart it.

**Working on the UI?** Have the backend front the Vite dev server instead of serving a build:

```bash
make dev-ui   # Vite on :3000 and the backend on :2024 forwarding UI requests to it, in one terminal
```

`make dev-ui` runs `make web` and `make dev` side by side, the backend with `DASHBOARD_DEV_SERVER_URL=http://localhost:3000`; Ctrl-C stops both. Open `http://localhost:2024` as usual: the page, its modules, and hot module replacement come from Vite, while `/dashboard/api/*` and the LangGraph routes stay with the backend. Nothing else changes, because the browser never leaves port 2024. The HMR WebSocket connects straight to Vite's port; the UI's Vite config points the client there.

| Endpoint | Purpose |
|---|---|
| `/` | Dashboard |
| `POST /webhooks/github` | GitHub issue, PR, and comment webhooks |
| `POST /webhooks/slack`, `POST /webhooks/slack/interactivity` | Slack events and Block Kit interactions |
| `POST /webhooks/linear` | Linear comment webhooks |
| `GET /dashboard/api/auth/login`, `GET /dashboard/api/auth/callback` | GitHub login |
| `/dashboard/api/*` | Dashboard API |
| `GET /ok`, `GET /health` | Health checks |

> `make run` serves the FastAPI app alone with uvicorn on port 8000, without the LangGraph runtime. Nothing that creates runs works there; use `make dev`.

## 7. Verify it works

**Dashboard.** Open `http://localhost:2024`, click **Sign in with GitHub**, and you should land logged in. With your login in `CONFIGURED_ADMINS`, the **Admin** pages appear. Set **Admin → Team settings → Default repository**, then start a task from the composer.

**Slack.** With the tunnel running and the Request URL verified, invite your bot to a channel and mention it: `@open_swe_you what's in the repo?`. It replies in a thread; ngrok's inspector at `http://localhost:4040` shows the event arriving.

**GitHub.** With the tunnel running and the App's webhook pointed at it, comment `@openswe what files are in this repo?` on an issue in a repository where the App is installed. Within a few seconds you should see a 👀 reaction, a run in your LangSmith project, and a reply comment. GitHub-triggered runs act as the commenting user, so that account has to have signed in to your local dashboard once. The App's **Advanced** tab lists every delivery and its response, and ngrok's inspector at `http://localhost:4040` shows what arrived.

## Dashboard on the Vite dev server directly

`make dev-ui` is the simple way to develop the UI. Opening Vite on `http://localhost:3000` directly also works:

```bash
pnpm install      # from the repo root: ui/ and desktop/ are one pnpm workspace
make web          # Vite on http://localhost:3000, proxying /dashboard/api/* to DASHBOARD_API_URL (default http://localhost:2024)
```

The browser now talks to `http://localhost:3000`, so the session cookie has to be set on that origin and the login callback has to return there:

```bash
DASHBOARD_BASE_URL="http://localhost:3000"       # the frontend origin; allowed for the CSRF check and post-login redirects
DASHBOARD_API_BASE_URL="http://localhost:3000"   # what browsers use for /dashboard/api/* and the OAuth callback
```

and the GitHub App needs `http://localhost:3000/dashboard/api/auth/callback` as an additional callback URL. Keep both URLs on `http://` locally so the cookie is `SameSite=Lax`. `DASHBOARD_ALLOWED_ORIGINS` lists **additional** origins that may call the API with credentials; credentialed CORS is only enabled when it is set, and `*` is rejected.

## Dashboard against a deployed backend

`pnpm run dev:prod` runs the Vite dev server, hot reload and all, against a deployment instead of a local backend — UI work against real threads, repositories, and settings without running the agent locally. Put the deployment in `.env` (step 5's file, gitignored):

```bash
DASHBOARD_API_URL=https://your-deployment.example.com   # a preview deploy, not production
```

then `pnpm run dev:prod`. There is no default, because a fallback would be someone else's production. `DASHBOARD_API_URL` in the environment overrides the file, and it is the only key read out of `.env`: the rest of it is the local backend's secrets, which have no business in the environment of a dev server the browser talks to.

The first run opens your browser to sign in. That leg still runs against the deployment's own registered GitHub callback — nothing changes on the GitHub App, and nothing changes on the deployment. It is the PKCE loopback handoff the desktop app uses (`?desktop_handoff=…&desktop_port=…`): the deployment redirects your browser to `http://127.0.0.1:<port>/callback` with a code that lives 120 seconds, and the script exchanges it at `/dashboard/api/auth/desktop/exchange` for a session. That tab then waits for Vite and sends you on to the dev server, so signing in ends where you want to be. The session lasts a week and is cached in `~/.cache/open-swe/dev-session.json`; after that you sign in again.

The dev server then attaches that session to everything it proxies, and presents the deployment's own origin so its CSRF check accepts mutations. **The session is real.** A task started from `http://localhost:3000` is a real run on that deployment, and the Admin pages edit its real settings.

`pnpm run build`, `pnpm run typecheck`, and `pnpm run test` run across the workspace through Turborepo (`pnpm --filter open-swe-dashboard run <script>` scopes one); `pnpm run lint` (oxlint) and `pnpm run format` / `pnpm run format:check` (oxfmt) run once from the root over every JS and TS file.

**Voice dictation** in the composer uses your OpenAI configuration (`OPENAI_API_KEY`, optional `OPENAI_BASE_URL`); admins choose the transcription model on the Admin page.

## Desktop app (experimental)

The Electron app in `desktop/` includes the compiled dashboard UI. Run it next to the backend:

```bash
pnpm install                  # from the repo root
make dev                      # terminal 1
pnpm run dev:desktop          # terminal 2
```

Development connects to `http://localhost:2024`. For a hosted backend run `pnpm --dir desktop run start -- --backend-url=https://your-backend.example.com` or set `OPEN_SWE_BACKEND_URL`. `pnpm --dir desktop run pack` creates an unpacked application and `pnpm --dir desktop run dist` an installer. Packaged builds ask for the organization's backend URL on first launch and never default to the maintainers' deployment. The GitHub App must allow `<backend-url>/dashboard/api/auth/callback` for desktop login.

## Make targets

| Target | What it does |
|---|---|
| `make dev` | `langgraph dev` on port 2024: graphs, webhooks, dashboard API, and the bundled dashboard when a build exists |
| `make dev-ui` | `make web` and `make dev` together, the backend fronting Vite so the UI hot-reloads on port 2024 |
| `make web` | The Vite dev server alone on port 3000 |
| `make build-dashboard` | Installs the dashboard's dependencies and builds it into `ui/.output/public` |
| `make tunnel NGROK_DOMAIN=…` | `ngrok http 2024` on your static domain, exposing only `/webhooks/*` |
| `make run` | The FastAPI app alone on port 8000, no LangGraph runtime |
| `make desktop` | The Electron app in development, against a backend on port 2024 |
| `make install` | `uv sync --extra dev` |
| `make test [TEST_FILE=tests/…]` | `pytest -vvv` on `tests/` or the given path |
| `make lint`, `make format`, `make format-check` | ruff check and format (`format` rewrites files) |
| `make typecheck` | `ty check agent tests` |

## Troubleshooting

### Webhook not receiving events

- The tunnel must be running (`make tunnel`) against port 2024, and the URL in GitHub or Slack must be your ngrok domain. Do not swap in a tunnel that forwards the whole port; see step 3. GitHub shows each delivery under the App's **Advanced** tab; ngrok's inspector at `http://localhost:4040` shows what arrived. With the webhooks-only policy, ngrok itself answers 404 for anything outside `/webhooks/*`, so test with `/webhooks/slack`, not `/ok`.
- Restart the backend after changing `.env`: `langgraph dev` reloads on code changes only, so a new `GITHUB_WEBHOOK_SECRET` or `SLACK_SIGNING_SECRET` is not picked up until then, and every delivery is rejected as `Invalid signature` in the meantime. Slack then needs **Retry** on its Request URL under **Event Subscriptions**.
- Webhook secrets are required: without `GITHUB_WEBHOOK_SECRET`, `SLACK_SIGNING_SECRET`, or `LINEAR_WEBHOOK_SECRET`, every request to that endpoint is rejected with 401.

### Dashboard login fails or won't stay logged in

- `redirect_uri is not associated with this application`: the App must list `http://localhost:2024/dashboard/api/auth/callback` (or the `:3000` one when you open Vite directly). Add it in the App's settings.
- Login redirects but the session does not stick: keep local URLs on `http://` so the cookie is `SameSite=Lax`.
- `DASHBOARD_BASE_URL not configured` on Sign in with Slack or Notion: the backend has neither a dashboard build nor `DASHBOARD_DEV_SERVER_URL`, so it does not know where the dashboard is. Run `make build-dashboard` or use `make dev-ui`.
- Admin pages 403: add your GitHub login or email to `CONFIGURED_ADMINS`.

### Dashboard shows the LangGraph JSON instead of the UI, or 404s at `/`

- There is no dashboard build: run `make build-dashboard`, or use `make dev-ui`.
- With Vite on port 3000, `curl -i http://localhost:3000/dashboard/api/me` should return the backend's `401`, not HTML; otherwise export `DASHBOARD_API_URL` before `make web`.

### `Port 3000 is already in use`

`make dev-ui` and `make web` refuse to start when another Vite is still running (Vite is configured with `strictPort`). Stop the old one or check `lsof -iTCP:3000 -sTCP:LISTEN`.

For sandbox, token-encryption, and "agent not responding" problems, see the installation guide's [Troubleshooting](INSTALLATION.md#troubleshooting).
