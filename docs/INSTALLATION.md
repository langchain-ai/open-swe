# Installation Guide

This guide deploys Open SWE for a team. To run it on your own machine while developing, use the [development guide](DEVELOPMENT.md) instead.

Open SWE is one deployment: a LangGraph server that runs the graphs (`agent`, `reviewer`, `analyzer`, `chat`, `scheduler`), the FastAPI app (`agent.webapp:app`) that owns the webhooks and the dashboard API, and the web dashboard, served from the same origin at `/`. Webhooks, the dashboard, GitHub login, and the API all share the deployment's URL, so there is no second frontend deploy and no cross-origin cookie or CORS setup.

What a deployment needs:

| Value | How you get it |
|---|---|
| `LANGSMITH_API_KEY` | LangSmith → Settings → API Keys. LangGraph Platform injects it. |
| A model provider key such as `ANTHROPIC_API_KEY`, or `LANGSMITH_GATEWAY_API_KEY` for the LangSmith LLM Gateway | Your provider, or a LangSmith key with `gateway:invoke` (step 4) |
| `GITHUB_APP_ID`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_APP_INSTALLATION_ID` | The GitHub App you create in step 3 |
| `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`, `SLACK_BOT_USER_ID`, `SLACK_BOT_USERNAME` | The Slack app you create in step 5 |
| `TOKEN_ENCRYPTION_KEY`, `DASHBOARD_JWT_SECRET` | Two random secrets you generate (step 6) |
| `CONFIGURED_ADMINS` | The GitHub logins or emails of your admins (step 6) |
| `LANGGRAPH_URL` | The deployment's own public URL |

GitHub and Slack are the two surfaces every deployment has; Linear is an optional add-on. Every variable Open SWE reads is declared in `agent/config.py` with its description and default; that file is the complete reference.

## 1. Create the deployment

You need the deployment's public URL before the GitHub App can be created, so create the deployment first; it starts without the GitHub and Slack variables and picks them up in step 6.

**LangGraph Platform.** Connect the repository to a new deployment in LangSmith → Deployments. The image build bundles the dashboard (the `dockerfile_lines` in `langgraph.json`), so the deployment URL serves the UI at `/` and the API beneath it; a failed UI build is logged and the backend still deploys. The platform injects `LANGSMITH_API_KEY`, `LANGSMITH_TRACING`, and `LANGSMITH_PROJECT`. You will set the environment variables in step 6.

**Standalone Docker.** The root `Dockerfile` builds a production LangGraph API server image (not the sandbox image):

```bash
docker build -t open-swe .

docker run \
  --env-file .env \
  -p 8123:8000 \
  --add-host=host.docker.internal:host-gateway \
  -e DATABASE_URI="postgres://postgres:postgres@host.docker.internal:5432/postgres?sslmode=disable" \
  -e REDIS_URI="redis://host.docker.internal:6379" \
  -e LANGGRAPH_AUTH_TYPE="langsmith" \
  -e LANGSMITH_AUTH_ENDPOINT="https://api.smith.langchain.com" \
  -e LANGSMITH_TENANT_ID="<your LangSmith workspace id>" \
  -e LANGGRAPH_URL="https://<your-backend-url>" \
  open-swe
```

The example assumes Postgres and Redis run on the Docker host; `--add-host` is what makes `host.docker.internal` resolve on a plain Linux Docker Engine. If they run as containers, drop the flag and point `DATABASE_URI` / `REDIS_URI` at their service names on a shared network. Add the standalone Agent Server requirements: `DATABASE_URI`, `REDIS_URI`, `LANGSMITH_API_KEY`, and `LANGGRAPH_CLOUD_LICENSE_KEY`. Expose port `8000` through your ingress, and do not use scale-to-zero hosting: background runs rely on the Redis- and Postgres-backed workers staying up. Bundle the dashboard by building it (`make build-dashboard`) before `docker build`, or set `DASHBOARD_STATIC_DIR` to a directory holding the build.

**Authentication.** The three `LANGGRAPH_AUTH_TYPE` lines matter. The standalone image defaults to `noop`, which leaves the LangGraph API (`/threads`, `/runs`, `/assistants`, `/store`) open to anyone who can reach the port; only the dashboard API (session cookie) and the webhooks (signatures) check anything themselves. `langsmith` makes the LangGraph API require a LangSmith API key from your workspace on every call, which is what LangGraph Platform does and what Open SWE's own calls already send (`LANGSMITH_API_KEY`); the webhooks and dashboard API are custom routes and keep working as before. It needs `LANGSMITH_AUTH_ENDPOINT` (your LangSmith API URL) and `LANGSMITH_TENANT_ID` (the workspace id, shown under **Settings → Workspaces** in LangSmith). Use `noop` only on a private network behind a gateway that does the authentication for you.

Either way, the URL browsers and webhooks use from here on is `<URL>`: `https://<name>-<hash>.<region>.langgraph.app` on the platform, or your ingress hostname in front of the container.

## 2. LangSmith API key

Create a [LangSmith](https://smith.langchain.com/) API key under **Settings → API Keys** and save it as `LANGSMITH_API_KEY`. LangGraph Platform injects it into the deployment for you, along with `LANGSMITH_TRACING` and `LANGSMITH_PROJECT`; standalone deployments set it themselves.

The same key is used for tracing, sandboxes, and trace links. Trace links find your workspace through the key and the project by name, so no tenant or project ids are needed (`LANGSMITH_TENANT_ID` remains an override). Sandboxes boot from LangSmith's root snapshot, which ships `git`, `gh`, Python, `uv`, and Node, so there is nothing to configure; when your repositories need more, admins capture an **Environment** from the dashboard later (see step 6). Other sandbox providers are covered in [CUSTOMIZATION.md](CUSTOMIZATION.md).

## 3. Create a GitHub App

Open SWE authenticates as a [GitHub App](https://docs.github.com/en/apps/creating-github-apps) to clone repositories, push branches, open pull requests, and sign users in to the dashboard.

Go to **GitHub Settings → Developer settings → [GitHub Apps](https://github.com/settings/apps) → New GitHub App** and fill in:

- **Callback URL**: `<URL>/dashboard/api/auth/callback`. GitHub Apps take several, one per line; for [local development](DEVELOPMENT.md) add `http://localhost:2024/dashboard/api/auth/callback`.
- **Request user authorization (OAuth) during installation**: off
- **Webhook URL**: `<URL>/webhooks/github`, **Webhook secret**: the output of `openssl rand -hex 32`, saved as `GITHUB_WEBHOOK_SECRET`
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

Click **Create GitHub App**, then collect from its settings page:

- **App ID** → `GITHUB_APP_ID`
- **Client ID** (starts with `Iv`) → `GITHUB_APP_CLIENT_ID`
- **Client secrets → Generate a new client secret** → `GITHUB_APP_CLIENT_SECRET`
- **Private keys → Generate a private key** downloads a `.pem`; its whole contents, BEGIN and END lines included → `GITHUB_APP_PRIVATE_KEY`

Finally **Install App** in the sidebar: pick the account and the repositories Open SWE may work in. The number at the end of the resulting URL, `https://github.com/settings/installations/<id>` (or `/organizations/<org>/settings/installations/<id>`), is `GITHUB_APP_INSTALLATION_ID`.

Give each deployment its own GitHub App, or at least a distinct mention handle (`OPEN_SWE_MENTION_TAGS`, see [Allowlists](#repository-allowlists-mention-handles-and-user-mapping)) when several share a GitHub organization.

## 4. Model providers and API keys

Open SWE calls models through [LangChain](https://python.langchain.com/) chat models named `provider:model`, so any provider you give a key for is available. Set at least one:

| Provider | Variable | Notes |
|---|---|---|
| Anthropic | `ANTHROPIC_API_KEY` | Default model when it is the only key set |
| OpenAI | `OPENAI_API_KEY` | Default model otherwise; also used for voice dictation in the dashboard. `OPENAI_BASE_URL` points at an OpenAI-compatible API |
| Google | `GOOGLE_API_KEY` | `google_genai:` models |
| Fireworks | `FIREWORKS_API_KEY` | `fireworks:` models |
| Groq | `GROQ_API_KEY` | `groq:` models |
| Baseten | `BASETEN_API_KEY` | `baseten:` models |

**LangSmith LLM Gateway.** Instead of per-provider keys, route every model call through the gateway with one LangSmith key that has the `gateway:invoke` permission, set as `LANGSMITH_GATEWAY_API_KEY`. Setting that key turns the gateway on; `LANGSMITH_GATEWAY_ENABLED=true|false` forces it either way (with `true` and no gateway key, `LANGSMITH_API_KEY` is used, which on LangGraph Platform may lack the permission). `LANGSMITH_GATEWAY_BASE_URL` points at a regional or self-hosted gateway. Admins can also toggle the gateway per team in the dashboard.

**Which model runs.** The deployment default is `anthropic:claude-opus-5` when only an Anthropic key is configured and `openai:gpt-5.6-sol` otherwise, at `medium` reasoning effort; override it with `LLM_MODEL_ID` (`provider:model`) and `LLM_REASONING_EFFORT` (`low`, `medium`, `high`, `max`), and name a `LLM_FALLBACK_MODEL_ID` for when the primary provider fails. Admins set a team default under **Admin → Team settings**, and each user can pick their own model and effort under **My settings**; the supported list lives in `agent/dashboard/options.py`. Model ids and their providers are described in [CUSTOMIZATION.md](CUSTOMIZATION.md).

**Other API keys.** `EXA_API_KEY` (from [dashboard.exa.ai](https://dashboard.exa.ai)) enables the web search tool. `REVIEWER_OUTCOMES_DATASET` names the LangSmith dataset the reviewer records finding outcomes in (default `openswe-reviewer-outcomes`).

## 5. Create the Slack app

Open SWE answers `@`-mentions in Slack and posts its progress there, and Slack is how most teams start runs. The app posts events to your deployment's URL, so it needs the same public URL as the GitHub App.

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From a manifest**, and paste the manifest below with `<your-url>` replaced by the hostname of `<URL>`, for example `my-open-swe-abc123.us.langgraph.app`; the manifest already supplies the `https://`.

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
3. Add to the environment (step 6):

```bash
SLACK_BOT_TOKEN=""        # OAuth & Permissions → Bot User OAuth Token (xoxb-...)
SLACK_SIGNING_SECRET=""   # Basic Information → App Credentials → Signing Secret
SLACK_BOT_USER_ID=""      # the bot's member id (open the bot's profile in Slack → ⋮ → Copy member ID)
SLACK_BOT_USERNAME=""     # the bot's handle, e.g. open-swe
```

Both Slack URLs must point at the Open SWE deployment, and Block Kit buttons only work with Interactivity enabled and pointed at `/webhooks/slack/interactivity`. Slack messages are routed to the thread's repository, a `repo:owner/name` token in the message, or the team default repository. Open SWE refuses Slack Connect channels (`is_ext_shared`) and fails closed when it cannot verify a channel.

Slack verifies the events Request URL the first time it can reach it; if the backend is not up yet when you create the app, use **Retry** under **Event Subscriptions** after step 7.

## 6. Set the environment variables

```bash
LANGGRAPH_URL="<URL>"                 # the deployment's own URL
LANGSMITH_API_KEY=""                  # step 2; injected by LangGraph Platform
LANGSMITH_TRACING="true"              # injected by LangGraph Platform
ANTHROPIC_API_KEY=""                  # step 4: any provider key, or LANGSMITH_GATEWAY_API_KEY

GITHUB_APP_ID=""                      # step 3
GITHUB_APP_CLIENT_ID=""
GITHUB_APP_CLIENT_SECRET=""
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"   # one line with \n between the PEM lines, or the multi-line value your platform accepts
GITHUB_WEBHOOK_SECRET=""
GITHUB_APP_INSTALLATION_ID=""

SLACK_BOT_TOKEN=""                    # step 5
SLACK_SIGNING_SECRET=""
SLACK_BOT_USER_ID=""
SLACK_BOT_USERNAME=""

TOKEN_ENCRYPTION_KEY=""               # openssl rand -base64 32  (encrypts stored GitHub and Slack tokens)
DASHBOARD_JWT_SECRET=""               # openssl rand -hex 32     (signs the session cookie and OAuth state)
CONFIGURED_ADMINS=""                  # GitHub logins or emails, comma-separated; admins see the Admin pages
```

On LangGraph Platform, set them under the deployment's environment variables; saving rolls out a new revision. With Docker, put them in the file you pass as `--env-file`. `DASHBOARD_BASE_URL` and `DASHBOARD_API_BASE_URL` are not needed: they default to `LANGGRAPH_URL` because the dashboard is served from the same origin.

## 7. Verify it works

**Dashboard.** Open `<URL>`, click **Sign in with GitHub**, and you should land logged in. With your login in `CONFIGURED_ADMINS`, the **Admin** pages (Team settings, User mappings, Sandbox, Environments, …) appear. Set **Admin → Team settings → Default repository** so runs that name no repository have somewhere to go. Start a task from the composer. Every run gets a sandbox booted from LangSmith's root snapshot; when your repositories need extra toolchains preinstalled, an admin can start an **admin thread** (the Admin toggle in the composer), have the agent set the sandbox up, and capture it under **Admin → Environments** as the environment named `default`, which later runs boot from.

**Slack.** Invite the bot to a channel and mention it: `@Open SWE what's in the repo?`. It replies in a thread. Runs it starts act as the GitHub App until the Slack user is linked to a GitHub login, either by signing in to the dashboard once or through [Sign in with Slack](#slack-sign-in-and-code-channels).

**GitHub.** Signing in once is also what lets GitHub-triggered runs act as you: they run as the commenting user and need the token the sign-in stored; an unmapped commenter is skipped with a warning in the server log. Comment `@openswe what files are in this repo?` on an issue in a repository where the App is installed. Within a few seconds you should see a 👀 reaction, a run in your LangSmith project, and a reply comment. GitHub lists every delivery and its response under the App's **Advanced** tab.

---

## Optional add-ons

Open a section when you want that feature; everything above keeps working without it.

<details id="slack-sign-in-and-code-channels">
<summary><strong>Slack: "Sign in with Slack" linking and code channels</strong></summary>

**"Sign in with Slack" account linking.** Lets a user link their Slack identity to their GitHub login from **My settings**, so Slack-triggered runs resolve to the right GitHub user through Slack's verified claims. Without it, an admin links people under **Admin → User mappings**. The manifest already registers the OIDC redirect; make sure the `openid`, `email`, and `profile` user scopes are available, then set `SLACK_CLIENT_ID` and `SLACK_CLIENT_SECRET` from **Basic Information → App Credentials**, and optionally `SLACK_TEAM_ID` (`T...`) to restrict linking to one workspace. When they are unset the link is simply hidden.

**Code channels (early access).** To enable Slack [code channels](https://api.slack.com/partners/code-channels), open **Admin → Slack integration**, turn on **Slack Code Channels**, copy the generated manifest, update the Slack app, and reinstall it. In a code channel the whole channel is one Open SWE session: it answers without an `@`-mention, replies at the channel level by default, reports session status, and keeps the context bar current; the `manage_code_channel` tool covers channel lifecycle, status, views, and canvases. This requires the `code_channels:manage` bot scope, the `agent_session_stopped` and `code_channel_action` bot events, and `features.code_channels.enabled`; `slash_command_url` delivers runtime-registered commands to the signed Open SWE endpoint. If your workspace is not enrolled, leave the toggle off.

</details>

<details id="linear">
<summary><strong>Linear</strong></summary>

Open SWE listens for Linear comments that mention `@openswe`.

1. **Settings → API → Webhooks → New webhook**: label `Open SWE`, URL `<URL>/webhooks/linear`, a secret from `openssl rand -hex 32` saved as `LINEAR_WEBHOOK_SECRET`, and under **Data change events** only **Comments → Create**.
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
<summary><strong>Dashboard on its own origin (separate frontend deployment)</strong></summary>

The bundled dashboard needs none of this. Read on only if the dashboard is deployed separately from the backend.

**A separate frontend deployment.** The `ui/` app also builds to a Nitro server (`ui/Dockerfile`) that renders on request. Set its `DASHBOARD_API_URL` to the backend URL; browser requests to `/dashboard/api/*` and webhook deliveries to `/webhooks/*` are proxied there, and server renders forward the `osw_session` cookie. Set `DASHBOARD_BASE_URL` and `DASHBOARD_API_BASE_URL` on the backend to the frontend origin and register `<frontend origin>/dashboard/api/auth/callback` on the GitHub App. To have the browser call the backend cross-origin instead, build the UI with `VITE_DASHBOARD_API_BASE_URL` set to the backend origin, keep `DASHBOARD_API_BASE_URL` on the backend origin, and add the frontend origin to `DASHBOARD_ALLOWED_ORIGINS`; the session is then resolved on the client after hydration.

**Mount prefix.** If the server runs under a LangGraph `http.mount_prefix`, the Platform image builds the UI for that prefix automatically; locally pass it to the build (`DASHBOARD_BASE_PATH=/<prefix>/ make build-dashboard`) and keep `LANGGRAPH_URL` on the mounted URL.

**Datadog RUM.** Set `VITE_DATADOG_APPLICATION_ID` and `VITE_DATADOG_CLIENT_TOKEN` when building. Optional: `VITE_DATADOG_SITE` (default `datadoghq.com`), `VITE_DATADOG_SERVICE` (default `open-swe-dashboard`), `VITE_DATADOG_ENV`, `VITE_DATADOG_VERSION`, `VITE_DATADOG_SESSION_SAMPLE_RATE` and `VITE_DATADOG_SESSION_REPLAY_SAMPLE_RATE` (default `100`). Session Replay masks all content and telemetry strips query strings and fragments. `VITE_` values are public in the bundle; use a client token, never an API or application key.

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

<details id="rotating-token_encryption_key">
<summary><strong>Rotating <code>TOKEN_ENCRYPTION_KEY</code></strong></summary>

`TOKEN_ENCRYPTION_KEY` accepts a single Fernet key or a comma- or newline-separated **ordered list, most recent first**. Writes use the first key; reads try every key in order.

1. Generate a new key: `openssl rand -base64 32`.
2. Prepend it, keeping the old key second: `TOKEN_ENCRYPTION_KEY="<new_key>,<old_key>"`, and restart.
3. Once every active user has signed in again (each fresh OAuth flow re-encrypts under the new key), drop the old key. Anything still encrypted under it fails to decrypt and that user is asked to sign in again.

</details>

## Troubleshooting

### Webhook not receiving events

- The URL configured in GitHub, Slack, or Linear must be the deployment's URL; GitHub shows each delivery and its response under the App's **Advanced** tab. A new webhook or signing secret takes effect only after the deployment restarts with it; deliveries in between are rejected as `Invalid signature`, and Slack then needs **Retry** on its Request URL under **Event Subscriptions**.
- Enable the right events: Issue comment and the pull request review events for GitHub, `app_mention` for Slack, Comments → Create for Linear.
- Webhook secrets are required: without `GITHUB_WEBHOOK_SECRET`, `SLACK_SIGNING_SECRET`, or `LINEAR_WEBHOOK_SECRET`, every request to that endpoint is rejected with 401.

### GitHub authentication errors

- Check `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, and `GITHUB_APP_INSTALLATION_ID`. The private key must include the full `-----BEGIN RSA PRIVATE KEY-----` and `-----END RSA PRIVATE KEY-----` lines; in a `.env` file write it as one double-quoted line with `\n` between the PEM lines.
- Make sure the App is installed on the target repositories.

### Dashboard login fails or won't stay logged in

- `500 GITHUB_APP_CLIENT_ID not configured` (or client secret): set `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, and `DASHBOARD_JWT_SECRET`.
- `redirect_uri is not associated with this application`: the App must list `<URL you opened the dashboard on>/dashboard/api/auth/callback`. Add it in the App's settings.
- Login redirects but the session does not stick: use `https://` and open the dashboard on `LANGGRAPH_URL` itself.
- `403 CSRF check failed` on saves: the request's `Origin` is neither `DASHBOARD_BASE_URL` (defaults to `LANGGRAPH_URL`) nor in `DASHBOARD_ALLOWED_ORIGINS`.
- Login rejected with an org error: `ALLOWED_GITHUB_ORGS` gates login and needs the App's Organization → Members permission.
- Admin pages 403: add your GitHub login or email to `CONFIGURED_ADMINS`.

### Dashboard shows the LangGraph JSON instead of the UI, or 404s at `/`

- The image has no dashboard build. On LangGraph Platform, check the build log for `dashboard build failed`; with Docker, run `make build-dashboard` before `docker build`, or set `DASHBOARD_STATIC_DIR` to a directory holding a build.

### Sandbox creation failures

- `LANGSMITH_API_KEY` must be set and valid, and the workspace must have sandbox access (403 on the sandbox endpoints means it does not; contact LangSmith support).
- Check LangSmith sandbox quotas in your workspace settings.
- `Failed to create sandbox from snapshot '<id>'` means an admin-captured environment or base snapshot no longer exists or is not `ready` in that workspace; delete or recapture it under **Admin → Environments** (or clear **Admin → Sandbox → Base snapshot**) to fall back to the root snapshot.

### Agent not responding to comments

- GitHub: the comment must contain a configured handle (`@openswe` by default, case-insensitive), and the commenter must have signed in to the dashboard once; otherwise the log says `No email mapping for GitHub user`.
- Linear: the comment must contain the handle; Slack: the bot must be in the channel and `@`-mentioned.
- Check the server log for webhook processing errors.

### Token encryption errors

- `TOKEN_ENCRYPTION_KEY` must be set to a valid Fernet key (`openssl rand -base64 32`), or an ordered list of them; see [Rotating `TOKEN_ENCRYPTION_KEY`](#rotating-token_encryption_key).
