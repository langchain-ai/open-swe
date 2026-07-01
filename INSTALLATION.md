
# Installation Guide

This guide walks you through setting up Open SWE end-to-end: local development, GitHub App creation, LangSmith configuration, webhooks, the web dashboard, and production deployment.

Open SWE has two runnable pieces:

- **The backend** — a LangGraph app (three graphs: `agent`, `reviewer`, `analyzer`) plus a FastAPI app (`agent.webapp:app`) that owns the webhooks and the dashboard API. Both are served together by `langgraph dev`.
- **The dashboard** — a TanStack Start + Vite web app in `ui/` (package name `open-swe-dashboard`). It's a thin client over the FastAPI dashboard API (`/dashboard/api/*`): GitHub-login, per-user model/profile settings, team defaults, enabled-repo and review-style management, user mappings, and the Agents chat UI. It's optional for pure webhook-driven use, but recommended.

> **The steps are ordered to avoid forward references.** Each step only depends on things you've already completed.

## Prerequisites

- **Python 3.11 – 3.13** (3.14 is not yet supported due to dependency constraints)
- [uv](https://docs.astral.sh/uv/) package manager
- [LangGraph CLI](https://docs.langchain.com/langsmith/cli)
- [ngrok](https://ngrok.com/) (for local development — exposes webhook endpoints to the internet)
- [pnpm](https://pnpm.io/) (only if you want to run the dashboard UI locally — see step 8). Node 20+ also works, but `ui/pnpm-lock.yaml` is the canonical lockfile.

## 1. Clone and install

```bash
git clone https://github.com/langchain-ai/open-swe.git
cd open-swe
uv venv
source .venv/bin/activate
uv sync --all-extras
```

## 2. Start ngrok

You'll need the ngrok URL in subsequent steps when configuring webhooks, so start it first.

```bash
ngrok http 2024 --url https://some-url-you-configure.ngrok.dev
```

You don't need to pass the `--url` flag, however doing so will use the same subdomain each time you startup the server. Without this, you'll need to update the webhook URL in GitHub, Slack and Linear every time you restart your server for local development.

Copy the HTTPS URL you set, or if you didn't pass `--url`, the one ngrok gives you. You'll paste this into the webhook settings in steps 3 and 5.

> Keep this terminal open — ngrok needs to stay running during local development. Use a second terminal for the rest of the steps.

## 3. Create a GitHub App

Open SWE authenticates as a [GitHub App](https://docs.github.com/en/apps/creating-github-apps) to clone repos, push branches, and open PRs.

### 3a. Choose your OAuth provider ID

Before creating the app you need to decide on an **OAuth provider ID** — this is a short string you'll use in both GitHub and LangSmith to link the two. Pick something memorable, for example:

```
your-org-github-oauth
```

Write this down. You'll use it in the callback URL below and again in step 4 when configuring LangSmith.

### 3b. Create the app

1. Go to **GitHub Settings → Developer settings → [GitHub Apps](https://github.com/settings/apps) → [New GitHub App](https://github.com/settings/apps/new)**
2. Fill in:
   - **App name**: `open-swe` (or your preferred name)
   - **Homepage URL**: This can be any valid URL — it's only shown on the GitHub Marketplace page (which you won't be using). Use something like `https://github.com/langchain-ai/open-swe`
   - **Callback URL**: GitHub Apps allow multiple callback URLs (one per line). Add **both**:
     1. `https://smith.langchain.com/host-oauth-callback/<your-provider-id>` — replace `<your-provider-id>` with the ID you chose in step 3a (e.g. `https://smith.langchain.com/host-oauth-callback/your-org-github-oauth`). This is the **agent-runtime** OAuth callback, brokered by LangSmith (step 4b).
     2. `http://localhost:2024/dashboard/api/auth/callback` — the **dashboard-login** OAuth callback (step 8). For production, also add `https://<your-dashboard-api-url>/dashboard/api/auth/callback`. This is a separate, direct GitHub OAuth flow (not via LangSmith), so it needs its own callback URL.
   - **Request user authorization (OAuth) during installation**: ✅ Enable this
   - **Webhook URL**: `https://<your-ngrok-url>/webhooks/github` — use the ngrok URL from step 2
   - **Webhook secret**: generate one and save it — you'll need it later as `GITHUB_WEBHOOK_SECRET`:
     ```bash
     openssl rand -hex 32
     ```
3. Set permissions:
   - **Repository permissions**:
     - Contents: Read & write
     - Pull requests: Read & write
     - Issues: Read & write
     - Checks: Read & write — reports an "Open SWE Review" check run on PRs while an auto-review runs, and reads third-party CI conclusions for the auto-fix flow (it watches failing checks on agent-authored PRs and pushes fixes). Without it, check-run creation fails (logged, best-effort) but reviews still work, and CI auto-fix is disabled.
     - Commit statuses: Read-only — only needed if you enable the `Status` event below; the CI auto-fix flow reads the legacy combined commit-status API for integrations that report via statuses instead of check runs. Without it, status-based CI is silently ignored (logged as "Failed to read combined status").
     - Actions: Read-only — optional; lets Open SWE's sandbox proxy tokens download GitHub Actions workflow/job logs when troubleshooting CI failures. Do **not** grant Actions write for log access: write permission also allows rerunning, canceling, and deleting workflow runs, which is unnecessary for diagnostics.
     - Workflows: Read & write — required to let Open SWE push branches containing GitHub Actions workflow changes after explicit human approval. Runtime sandbox tokens are still minted without this permission by default and are elevated only around an approved workflow push.
     - Metadata: Read-only
   - **Organization permissions** (required only if you plan to set `ALLOWED_GITHUB_ORGS` — see step 5 / Security):
     - Members: Read-only — used to verify org membership for the dashboard-login gate via `GET /orgs/{org}/memberships/{username}`. Without this permission that call returns 403, the check fails closed, and **every** dashboard login is rejected.
4. Under **Subscribe to events**, enable:
   - `Issue comment`
   - `Pull request review`
   - `Pull request review comment`
   - `Check run` — required for CI auto-fix (watching failing GitHub Actions checks on agent PRs)
   - `Check suite` — required for CI auto-fix
   - `Workflow run` — required for CI auto-fix
   - `Status` — optional; covers integrations that report via the legacy commit-status API
5. Click **Create GitHub App**

### 3c. Collect credentials

After creating the app:

1. **App ID** — shown at the top of the app's settings page. Save this as `GITHUB_APP_ID`.
2. **Private key** — scroll down to **Private keys** → click **Generate a private key**. A `.pem` file will download. Save its contents as `GITHUB_APP_PRIVATE_KEY`.
3. **Client ID** — shown near the top of the app's settings page (starts with `Iv...`). Save this as `GITHUB_APP_CLIENT_ID`.
4. **Client secret** — under **Client secrets** → **Generate a new client secret**. Save it as `GITHUB_APP_CLIENT_SECRET`.

> `GITHUB_APP_CLIENT_ID` / `GITHUB_APP_CLIENT_SECRET` power the **dashboard login** flow (the direct GitHub OAuth in 3b's second callback URL). They are independent of the LangSmith OAuth provider in step 4b — the dashboard talks to GitHub directly, while the agent runtime resolves per-user tokens through LangSmith.

### 3d. Install the app on your repositories

1. From your app's settings page, click **Install App** in the sidebar
2. Select your org or personal account
3. Choose which repositories Open SWE should have access to
4. Click **Install**
5. After installation, look at the URL in your browser — it will look like:
   ```
   https://github.com/settings/installations/12345678
   ```
   or for an org:
   ```
   https://github.com/organizations/YOUR-ORG/settings/installations/12345678
   ```
   The number at the end (`12345678`) is your **Installation ID**. Save this as `GITHUB_APP_INSTALLATION_ID`.

> **Note**: The installation page may prompt you to authenticate with LangSmith. If you haven't set up LangSmith yet (step 4), that's fine — you can still grab the Installation ID from the URL and complete the OAuth setup later.

## 4. Set up LangSmith

Open SWE uses [LangSmith](https://smith.langchain.com/) for:
- **Tracing**: all agent runs are logged for debugging and observability
- **Sandboxes**: each task runs in an isolated LangSmith cloud sandbox

### 4a. Get your API key, project and tenant IDs

1. Create a [LangSmith account](https://smith.langchain.com/) if you don't have one
2. Go to **Settings → API Keys → Create API Key**
3. Save it as `LANGSMITH_API_KEY_PROD`
4. Get your **Tenant ID**: Visit LangSmith, login, then copy the UUID in the URL. Example: if your URL is `https://smith.langchain.com/o/72184268-01ea-4d29-98cc-6cfcf0f2abb0/agents/chat` -> the tenant ID would be `72184268-01ea-4d29-98cc-6cfcf0f2abb0`. Save it as `LANGSMITH_TENANT_ID_PROD`.
5. Get your **Project ID**: open your tracing project in LangSmith, then click on the **ID** button in the top left, directly next to the project name. Save it as `LANGSMITH_TRACING_PROJECT_ID_PROD`

> **Note on per-graph tracing projects.** The graphs trace into separate projects by name — `open-swe-agent` (main agent) and `open-swe-review` (reviewer/analyzer). "View trace" links resolve the correct project ID from these names automatically (via the `LANGSMITH_API_KEY_PROD` client), so make sure projects with these names exist in your tenant. If a name can't be resolved, links fall back to `LANGSMITH_TRACING_PROJECT_ID_PROD`, so set it to whichever project you want links to point at by default.

### 4b. Configure GitHub OAuth (optional but recommended)

This is the **agent-runtime** OAuth provider: it lets each agent run authenticate with the triggering user's own GitHub account, brokered by LangSmith. (It is separate from the dashboard-login OAuth, which uses `GITHUB_APP_CLIENT_ID`/`GITHUB_APP_CLIENT_SECRET` directly — see step 3c.) Without it, all agent operations use the GitHub App's installation token (a shared bot identity).

**What this affects:**
- **With per-user OAuth**: PRs and commits show the triggering user's identity; each user's GitHub permissions are respected
- **Without it (bot-token-only mode)**: all PRs and commits appear as the GitHub App bot; the app's installation-level permissions are used for everything

To set up per-user OAuth:

1. In LangSmith, go to **Settings → OAuth Providers → Add Provider**
2. Set the **Provider ID** to the same string you chose in step 3a (e.g. `your-org-github-oauth`)
3. Enter the **Client ID** and **Client Secret** from your GitHub App (found on the GitHub App settings page under **OAuth credentials**)
4. Enter the **Authorization URL** as `https://github.com/login/oauth/authorize` and the **Token URL** as `https://github.com/login/oauth/access_token`.
5. Leave "Enable PKCE" unchecked.
6. Save. You'll reference this Provider ID as `GITHUB_OAUTH_PROVIDER_ID` in your environment variables.

### 4c. Sandbox snapshots

LangSmith sandboxes provide the isolated execution environment for each agent run. Open SWE boots each sandbox from a pre-built **snapshot** — you build the snapshot once (from a Docker image) and then reference it by UUID.

(Optional) Build and Push a custom Docker Image to Docker hub
First build and push the sandbox Docker image to a registry LangSmith can pull from. On Apple Silicon, force `linux/amd64`

```bash
docker buildx build \
  --platform linux/amd64 \
  -t <your-docker-hub>/<name-of-your-image> \
  --push .
```

For a multi-arch tag that also runs locally on Apple Silicon:

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t <your-docker-hub>/<name-of-your-image> \
  --push .
```

Then build a snapshot in the LangSmith UI (Sandboxes → Snapshots → New), or via the SDK:

```python
from langsmith.sandbox import SandboxClient

client = SandboxClient(api_key="<your key>")
snapshot = client.create_snapshot(
    name="open-swe",
    docker_image="johanneslangchain/open-swe-sandbox:gh-cli-amd64",  # built from ./Dockerfile
    fs_capacity_bytes=32 * 1024**3,
)
print(snapshot.id)
```

You can also use the helper script:

```bash
uv run python scripts/create_sandbox_snapshot.py \
  --name open-swe-gh-cli-amd64 \
  --image johanneslangchain/open-swe-sandbox:gh-cli-amd64
```

Then set the resulting UUID in your environment:

```bash
DEFAULT_SANDBOX_SNAPSHOT_ID="<snapshot-uuid>"
# Optional; overrides the snapshot's root FS size at sandbox boot. Default is 32 GiB.
DEFAULT_SANDBOX_SNAPSHOT_FS_CAPACITY_BYTES="34359738368"
# Optional; number of vCPUs per sandbox. Default is 4.
DEFAULT_SANDBOX_VCPUS="4"
# Optional; memory in bytes per sandbox. Default is 15 GiB.
DEFAULT_SANDBOX_MEM_BYTES="16106127360"
# Optional; auto-stop a sandbox after this many seconds of inactivity. Default is 7200 (2 hours). 0 disables.
DEFAULT_SANDBOX_IDLE_TTL_SECONDS="7200"
# Optional; delete a stopped sandbox after this many seconds. Default is 86400 (24 hours). 0 disables.
DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS="86400"
# Optional; required only for the admin Repository Snapshots page/template generator.
REPO_SNAPSHOT_BASE_IMAGE="<your-docker-hub>/<name-of-your-image>"
```

`DEFAULT_SANDBOX_SNAPSHOT_ID` is required when `SANDBOX_TYPE=langsmith`. The server validates this at startup and refuses to boot if it's missing. The snapshot should include the GitHub CLI from the project Dockerfile; Open SWE authenticates `git` and `gh` through the LangSmith sandbox proxy using runtime-minted GitHub App installation tokens, not deployment-stored GitHub access tokens.

`REPO_SNAPSHOT_BASE_IMAGE` should point at the same published Open SWE sandbox image you used to create the default snapshot (for example, the image built from `./Dockerfile`). The admin **Repository Snapshots** page uses it as the `FROM` line when generating per-repo Dockerfile templates. If it is not set, template generation is intentionally disabled so admins do not accidentally build repo-scoped snapshots from a bare image that lacks Open SWE's required tools (`git`, `gh`, `sfw`, language runtimes, and proxy assumptions).

## 5. Set up triggers

Open SWE can be triggered from GitHub, Linear, and/or Slack. **Configure whichever surfaces your team uses — you don't need all of them.**

### GitHub

GitHub triggering works automatically once your GitHub App is set up (step 3). Users can:
- Tag `@openswe` in issue titles or bodies to start a task
- Tag `@openswe` in issue comments for follow-up instructions
- Tag `@openswe` in PR review comments to have it address review feedback

Which GitHub users can trigger the agent is controlled by the **user mapping** (GitHub login ⇄ work email ⇄ optional Slack ID), stored in the LangGraph Store rather than in code. Manage it in the dashboard under **Admin → User mappings**:

- **Add / update** a single mapping (GitHub login + work email, plus an optional Slack user ID). The list is paged (20 per page).
- Users can also **self-onboard**: when an unmapped person tags Open SWE in Slack, the agent runs with limited (GitHub App installation) permissions and posts a "link your GitHub account" prompt. Completing the org-gated GitHub OAuth login records a `self` mapping (carrying the originating Slack ID and work email). Self-signup is therefore bounded by the same `ALLOWED_GITHUB_ORGS` gate as dashboard login.

You should also configure which GitHub organizations and/or repositories the agent is allowed to operate on. You can specify allowed orgs, specific `owner/repo` pairs, or both:

```bash
# Allow all repos in these orgs
ALLOWED_GITHUB_ORGS="langchain-ai,anthropics"

# Allow specific repos (owner/repo format)
ALLOWED_GITHUB_REPOS="some-user/their-repo,another-org/specific-repo"
```

A GitHub or Linear webhook is accepted if the resolved repo's org is in `ALLOWED_GITHUB_ORGS` **or** the `owner/repo` is in `ALLOWED_GITHUB_REPOS`. If both are empty, all repos are allowed. Slack mentions are not rejected from regex-inferred repository text; repository access is bounded by the GitHub App installation permissions.

`ALLOWED_GITHUB_ORGS` also gates **dashboard login**: when set, only GitHub accounts that are active members of one of the listed organizations can complete the OAuth login and receive a session. Membership is verified server-side with the GitHub App installation token (so private memberships are visible and no extra OAuth scope is required), and the check fails closed on any API error. When `ALLOWED_GITHUB_ORGS` is empty, dashboard login is open to any GitHub account (the prior behavior).

> **Required GitHub App permission**: the membership check calls `GET /orgs/{org}/memberships/{username}`, which requires the GitHub App's **Organization → Members: Read-only** permission (see step 3b). If you set `ALLOWED_GITHUB_ORGS` without granting that permission, the call returns 403, the check fails closed, and **every** dashboard login is rejected. After changing an installed app's permissions, GitHub requires you to **approve the new permission** on each installation before it takes effect.

### Linear (optional)

Open SWE listens for Linear comments that mention `@openswe`.

**Create a webhook:**

1. In Linear, go to **Settings → API → Webhooks → New webhook**
2. Fill in:
   - **Label**: `open-swe`
   - **URL**: `https://<your-ngrok-url>/webhooks/linear` — use the ngrok URL from step 2
   - **Secret**: generate with `openssl rand -hex 32` — save this as `LINEAR_WEBHOOK_SECRET`
3. Under **Data change events**, enable **Comments → Create** only
4. Click **Create webhook**

**Get your API key:**

1. Go to **Settings → API → Personal API keys → New API key**
2. Name it `open-swe`, select **All access**, and copy the key
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

Users can also override the team/project mapping per-comment by including `repo:owner/name` (or a GitHub URL) in their `@openswe` comment. The mapping is used as a fallback when no repo is specified in the comment text.

### Slack (optional)

**Create a Slack App:**

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From a manifest**
2. Copy the manifest below, replacing the two placeholder URLs:
   - Replace `<your-provider-id>` with the OAuth provider ID from step 3a
   - Replace `<your-ngrok-url>` with the backend URL from step 2 (or your deployed LangGraph/FastAPI URL in production)

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

3. Install the app to your workspace and copy the **Bot User OAuth Token** (`xoxb-...`)

**Slack URL checklist:**

Both Slack URLs must point at the Open SWE backend that serves `agent.webapp:app` (locally, your ngrok URL forwarding to `langgraph dev`; in production, your LangGraph/FastAPI deployment URL), not the dashboard frontend URL.

- **Event Subscriptions → Request URL:** `https://<your-backend-url>/webhooks/slack`
- **Interactivity & Shortcuts → Interactivity Request URL:** `https://<your-backend-url>/webhooks/slack/interactivity`

Slack Block Kit option buttons only work when Interactivity is enabled and pointed at `/webhooks/slack/interactivity`.

**Credentials you'll need:**

- `SLACK_BOT_TOKEN`: the Bot User OAuth Token (`xoxb-...`)
- `SLACK_SIGNING_SECRET`: found under **Basic Information → App Credentials**
- `SLACK_BOT_USER_ID`: the bot's user ID (find it in Slack by clicking the bot's profile)
- `SLACK_BOT_USERNAME`: the bot's display name (e.g. `open-swe`)

**Default repo:**

Slack messages are routed to the Slack default repo (`SLACK_REPO_OWNER`/`SLACK_REPO_NAME`, falling back to `DEFAULT_REPO_OWNER`/`DEFAULT_REPO_NAME` — see step 6) unless the user specifies one with `repo:owner/name` in their message.

**"Sign in with Slack" account linking (optional):**

The dashboard can let a user link their Slack identity to their GitHub login via Slack OIDC ("Sign in with Slack"). This is what lets a Slack-triggered run resolve to the right GitHub user. To enable it:

1. The manifest above already registers the OIDC redirect (`.../dashboard/api/slack/callback`). Under **OpenID Connect** (or **Sign in with Slack**) make sure the `openid`, `email`, and `profile` user scopes are available.
2. From **Basic Information → App Credentials**, save the app's **Client ID** as `SLACK_CLIENT_ID` and **Client Secret** as `SLACK_CLIENT_SECRET`.
3. (Optional) Set `SLACK_TEAM_ID` (your workspace ID, `T...`) to restrict linking to a single workspace.

If `SLACK_CLIENT_ID`/`SLACK_CLIENT_SECRET` are unset, the "Sign in with Slack" link is simply disabled; the rest of Slack triggering still works.

## 6. Environment variables

Create a `.env` file in the project root. Below is the full list — only fill in the sections relevant to the triggers you configured.

```bash
# === LangSmith ===
LANGSMITH_API_KEY_PROD=""              # From step 4a
LANGCHAIN_TRACING_V2="true"
LANGCHAIN_PROJECT=""                   # LangSmith project name for traces
LANGSMITH_TENANT_ID_PROD=""           
LANGSMITH_TRACING_PROJECT_ID_PROD=""   # Fallback project ID for "View trace" links; graphs trace into the open-swe-agent / open-swe-review projects by name
LANGSMITH_URL_PROD="https://smith.langchain.com"                 

# === LLM ===
ANTHROPIC_API_KEY=""                   # Anthropic API key
OPENAI_API_KEY=""                      # OpenAI API key (when using openai: models)
GOOGLE_API_KEY=""                      # Google AI API key (when using google_genai: models)
FIREWORKS_API_KEY=""                   # Fireworks API key (when using fireworks: models)

# === GitHub App (required) ===
GITHUB_APP_ID=""                       # From step 3c
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
...
-----END RSA PRIVATE KEY-----
"
GITHUB_APP_INSTALLATION_ID=""          # From step 3d

# === GitHub Webhook (required) ===
GITHUB_WEBHOOK_SECRET=""               # The secret you generated in step 3b

# === Dashboard GitHub OAuth (required for the dashboard) ===
# Direct GitHub OAuth used by the dashboard login flow (not via LangSmith).
GITHUB_APP_CLIENT_ID=""                # From step 3c
GITHUB_APP_CLIENT_SECRET=""            # From step 3c

# === Agent-runtime GitHub OAuth via LangSmith (optional) ===
# Without these, all agent operations use the GitHub App's bot token.
# With these, each agent run authenticates as the triggering user.
GITHUB_OAUTH_PROVIDER_ID=""            # The provider ID from steps 3a / 4b
# Secret used to mint short-lived service JWTs that ask LangSmith to resolve a
# specific user's GitHub token. Needed for per-user token resolution in deployed mode.
X_SERVICE_AUTH_JWT_SECRET=""

# === Repo Allowlist (optional) ===
# Comma-separated list of GitHub orgs the agent is allowed to operate on.
# Also gates dashboard login to members of these orgs (requires the GitHub App's
# Organization -> Members: Read-only permission; without it, all dashboard logins are rejected).
# Leave empty to allow all orgs.
ALLOWED_GITHUB_ORGS=""                 # e.g. "my-org,my-other-org"
# Comma-separated list of specific owner/repo pairs the agent is allowed to operate on.
# For GitHub/Linear webhooks, a repo is allowed if its org is in ALLOWED_GITHUB_ORGS OR its owner/repo is in ALLOWED_GITHUB_REPOS.
# Slack mentions are not rejected from regex-inferred repository text; repository access is bounded by GitHub App installation permissions.
# Leave both empty to allow all repos.
ALLOWED_GITHUB_REPOS=""                # e.g. "some-user/their-repo,another-org/specific-repo"

# === Default Repository ===
# Used across all triggers when no repo is specified.
DEFAULT_REPO_OWNER=""                  # Default GitHub org (e.g. "my-org")
DEFAULT_REPO_NAME=""                   # Default GitHub repo (e.g. "my-repo")

# === Dashboard (required to run the web dashboard) ===
# Public URL that browsers use for /dashboard/api/* and OAuth callbacks.
# Use the FastAPI backend URL for local/cross-origin direct API calls.
# Use the dashboard frontend URL when a same-origin frontend rewrite proxies /dashboard/api/*.
# Its scheme drives cookie security: http:// => SameSite=Lax (local);
# https:// => Secure + SameSite=None (production).
DASHBOARD_API_BASE_URL="http://localhost:2024"
# Public base URL of the dashboard frontend (the ui/ app). Default post-login redirect.
DASHBOARD_BASE_URL="http://localhost:3000"
# HMAC secret for dashboard JWTs (session cookie and OAuth state).
DASHBOARD_JWT_SECRET=""                # Generate with: openssl rand -hex 32
# Comma-separated origins allowed for credentialed CORS and post-login redirects.
# Required whenever the frontend and API are on different origins — including local
# dev (UI :3000 -> API :2024 is cross-origin). CORS is only enabled when this is set.
DASHBOARD_ALLOWED_ORIGINS="http://localhost:3000"  # prod: your frontend origin(s)
# Comma-separated GitHub login or email allowlist for admin dashboard endpoints.
# Empty => nobody is an admin.
CONFIGURED_ADMINS=""                   # e.g. "alice,bob@my-org.com"
# URL of the LangGraph server the FastAPI side calls to trigger/stream runs.
# Defaults to http://localhost:2024 locally; set to your deployment URL in prod.
LANGGRAPH_URL="http://localhost:2024"

# === Linear (if using Linear trigger) ===
LINEAR_API_KEY=""                      # From step 5
LINEAR_WEBHOOK_SECRET=""               # From step 5

# === Slack (if using Slack trigger) ===
SLACK_BOT_TOKEN=""                     # From step 5
SLACK_BOT_USER_ID=""
SLACK_BOT_USERNAME=""
SLACK_SIGNING_SECRET=""
# Optional: Slack-specific default repo (falls back to DEFAULT_REPO_OWNER/NAME).
SLACK_REPO_OWNER=""
SLACK_REPO_NAME=""
# Optional: "Sign in with Slack" account linking (GitHub <-> Slack). See step 5.
SLACK_CLIENT_ID=""
SLACK_CLIENT_SECRET=""
SLACK_TEAM_ID=""                       # Optional; restrict linking to one workspace (T...)

# === Exa (optional — enables web search tool) ===
EXA_API_KEY=""                         # From https://dashboard.exa.ai

# === Reviewer / Analyzer (optional) ===
# LangSmith dataset where reviewer finding outcomes are recorded and read back by
# the analyzer. Defaults to "openswe-reviewer-outcomes" if unset.
REVIEWER_OUTCOMES_DATASET=""
# Single GitHub org whose members may trigger the agent on *public* repos.
# Empty => no public-repo gate (back-compat). Distinct from ALLOWED_GITHUB_ORGS.
PUBLIC_REPO_ORG_GATE=""

# === Sandbox (optional) ===
# Provider: langsmith (default), modal, daytona, runloop, or local. See CUSTOMIZATION.md.
SANDBOX_TYPE="langsmith"
DEFAULT_SANDBOX_SNAPSHOT_ID=""         # Required when SANDBOX_TYPE=langsmith (see step 4c)
DEFAULT_SANDBOX_SNAPSHOT_FS_CAPACITY_BYTES=""  # Root FS size in bytes (default: 32 GiB)
DEFAULT_SANDBOX_VCPUS=""               # vCPUs per sandbox (default: 4)
DEFAULT_SANDBOX_MEM_BYTES=""           # Memory in bytes per sandbox (default: 15 GiB)
DEFAULT_SANDBOX_IDLE_TTL_SECONDS=""    # Auto-stop after N seconds idle (default: 7200; 0 disables)
DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS=""  # Delete N seconds after stop (default: 86400; 0 disables)

# === Token Encryption ===
TOKEN_ENCRYPTION_KEY=""                # Generate with: openssl rand -base64 32
                                       # Supports key rotation: see "Rotating TOKEN_ENCRYPTION_KEY" below
```

### Rotating TOKEN_ENCRYPTION_KEY

`TOKEN_ENCRYPTION_KEY` accepts either a single Fernet key or a comma- or
newline-separated **ordered list of keys, most-recent-first**. New writes always
encrypt under the first key; reads try every key in order. To rotate without
invalidating already-stored GitHub tokens:

1. Generate a new key: `openssl rand -base64 32`.
2. Prepend it to `TOKEN_ENCRYPTION_KEY`, keeping the old key second:
   ```
   TOKEN_ENCRYPTION_KEY="<new_key>,<old_key>"
   ```
   Restart the server. New encryptions use `<new_key>`; existing ciphertexts
   still decrypt against `<old_key>`.
3. Let active threads cycle (each fresh OAuth flow re-encrypts under the new
   key). After every active thread has re-authed, drop the old key:
   ```
   TOKEN_ENCRYPTION_KEY="<new_key>"
   ```
   Any thread still holding ciphertext under `<old_key>` will fail to decrypt
   and the user will be re-prompted to authenticate — same UX as if the thread
   had never authed.

## 7. Start the backend

Make sure ngrok is still running from step 2, then start the backend in a second terminal:

```bash
make dev          # uv run langgraph dev
# or: uv run langgraph dev --no-browser
```

`langgraph dev` serves **all three graphs** (`agent`, `reviewer`, `analyzer`) *and* the FastAPI app (`agent.webapp:app`) together on `http://localhost:2024`. The FastAPI app owns both the webhooks and the dashboard API:

| Endpoint | Purpose |
|---|---|
| `POST /webhooks/github` | GitHub issue/PR/comment webhooks |
| `POST /webhooks/linear` | Linear comment webhooks |
| `GET /webhooks/linear` | Linear webhook verification |
| `POST /webhooks/slack` | Slack event webhooks |
| `POST /webhooks/slack/interactivity` | Slack Block Kit button interactions |
| `GET /webhooks/slack` | Slack webhook verification |
| `GET /dashboard/api/auth/login` | Dashboard GitHub OAuth login |
| `GET /dashboard/api/auth/callback` | Dashboard GitHub OAuth callback (registered on the App in step 3b) |
| `GET /dashboard/api/*` | Dashboard API (profiles, team settings, repos, review styles, threads, …) |
| `GET /health` | Health check |

> `make run` (`uvicorn agent.webapp:app --port 8000`) serves the FastAPI app **without** the LangGraph runtime, on port 8000. The dashboard's Agents chat features call LangGraph, so for full local dev use `make dev` on `:2024`, not `make run`.

## 8. Run the dashboard (optional)

The dashboard is the web app in `ui/`. It's a static TanStack Start client that calls the FastAPI dashboard API from step 7. Run it in a third terminal:

```bash
cd ui
pnpm install
cat > .env <<'EOF'
VITE_DASHBOARD_API_BASE_URL="http://localhost:2024"
EOF
pnpm run dev          # vite dev --port 3000 -> http://localhost:3000
```

The dashboard needs `VITE_DASHBOARD_API_BASE_URL` in `ui/.env` pointing at the backend for local dev. The file is intentionally untracked because `.env*` files are gitignored.

The client calls `${VITE_DASHBOARD_API_BASE_URL}/dashboard/api/*` with `credentials: "include"`, so the backend's `osw_session` cookie rides along. Because the UI (`:3000`) and API (`:2024`) are different origins, the backend needs **CORS** enabled for the UI origin — set `DASHBOARD_ALLOWED_ORIGINS="http://localhost:3000"` (CORS is off unless this is set). Keep `DASHBOARD_API_BASE_URL` on an `http://` URL locally so the cookie uses `SameSite=Lax` rather than `Secure`.

For the dashboard login to succeed, you need (from steps 3c / 6): `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `DASHBOARD_JWT_SECRET`, `DASHBOARD_API_BASE_URL`, `DASHBOARD_BASE_URL`, and `DASHBOARD_ALLOWED_ORIGINS`. To reach the admin pages (user mappings, etc.), add your GitHub login or email to `CONFIGURED_ADMINS`.

Other UI scripts: `pnpm run build`, `pnpm run typecheck`, `pnpm run lint`, `pnpm run test`.

## 9. Verify it works

### GitHub

1. Go to any issue in a repository where the app is installed
2. Create or comment on an issue with: `@openswe what files are in this repo?`
3. You should see:
   - A 👀 reaction on your comment within a few seconds
   - A new run in your LangSmith project
   - The agent replies with a comment on the issue

### Linear

1. Go to any Linear issue in a team you configured in `LINEAR_TEAM_TO_REPO`
2. Add a comment: `@openswe what files are in this repo?`
3. You should see:
   - A 👀 reaction on your comment within a few seconds
   - A new run in your LangSmith project
   - The agent replies with a comment on the issue

### Slack

1. In any channel where the bot is invited, start a thread
2. Mention the bot: `@open-swe what's in the repo?`
3. You should see a reply in the thread with the agent's response.

### Dashboard

1. With the backend (step 7) and UI (step 8) both running, open `http://localhost:3000`
2. Click **Sign in with GitHub** — you'll be sent through the GitHub OAuth flow and back to the dashboard
3. You should land logged-in and be able to see your profile/settings. If your GitHub login or email is in `CONFIGURED_ADMINS`, the **Admin** pages (e.g. User mappings) are available.

## 10. Production deployment

Production runs the backend and dashboard separately.

**Backend** — deploy on [LangGraph Cloud / Platform](https://langchain-ai.github.io/langgraph/cloud/):

1. Push your code to a GitHub repository
2. Connect the repo to LangGraph Cloud
3. Set all environment variables from step 6 in the deployment config. Set `DASHBOARD_BASE_URL` and `LANGGRAPH_URL` to your production URLs (all `https://`). Set `DASHBOARD_API_BASE_URL` to the URL browsers use for dashboard API requests and OAuth callbacks: either the backend URL for direct cross-origin calls, or the dashboard/Vercel URL when a same-origin rewrite proxies `/dashboard/api/*`.
4. Update your webhook URLs (Linear, Slack, GitHub App) and the GitHub App / Slack OAuth callback URLs to your production URLs (replace the ngrok / localhost values). The dashboard GitHub App callback must be `<DASHBOARD_API_BASE_URL>/dashboard/api/auth/callback`.

The `langgraph.json` at the project root defines the three graphs and the HTTP app:

```json
{
  "graphs": {
    "agent": "agent.server:traced_agent",
    "reviewer": "agent.reviewer:traced_reviewer_agent",
    "analyzer": "agent.analyzer:traced_analyzer"
  },
  "http": {
    "app": "agent.webapp:app"
  }
}
```

**Dashboard** — the `ui/` app deploys to [Vercel](https://vercel.com/). The recommended production setup uses **same-origin** requests to `/dashboard/api/*` (leave `VITE_DASHBOARD_API_BASE_URL` empty), and `ui/vercel.json` rewrites those to the hosted LangGraph deployment. In this mode, set both `DASHBOARD_API_BASE_URL` and the GitHub App dashboard callback URL to the Vercel/dashboard origin (for example, `https://your-dashboard.vercel.app/dashboard/api/auth/callback`). The OAuth callback response then sets the `osw_session` cookie on the dashboard host, and later same-origin `/dashboard/api/*` requests include it. Update the rewrite `destination` in `ui/vercel.json` to your own LangGraph deployment URL.

Alternatively, you can run the dashboard as a direct cross-origin client: set `VITE_DASHBOARD_API_BASE_URL` to the hosted backend origin, set `DASHBOARD_API_BASE_URL` to that same backend origin, and include the dashboard origin in `DASHBOARD_ALLOWED_ORIGINS`.

## Troubleshooting

### Webhook not receiving events

- Verify ngrok is running and the URL matches what's configured in GitHub/Linear/Slack
- Check the ngrok web inspector at `http://localhost:4040` for incoming requests
- Ensure you enabled the correct event types (Comments → Create for Linear, `app_mention` for Slack, Issues + Issue comment for GitHub)
- **Webhook secrets are required** — if `GITHUB_WEBHOOK_SECRET`, `LINEAR_WEBHOOK_SECRET`, or `SLACK_SIGNING_SECRET` is not set, all requests to that endpoint will be rejected with 401

### GitHub authentication errors

- Verify `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, and `GITHUB_APP_INSTALLATION_ID` are set correctly
- Ensure the GitHub App is installed on the target repositories
- Check that the private key includes the full `-----BEGIN RSA PRIVATE KEY-----` and `-----END RSA PRIVATE KEY-----` lines

### Dashboard login fails or won't stay logged in

- `500 GITHUB_APP_CLIENT_ID not configured` (or client secret): set `GITHUB_APP_CLIENT_ID` / `GITHUB_APP_CLIENT_SECRET` (step 3c) and `DASHBOARD_JWT_SECRET`.
- OAuth `redirect_uri` mismatch: the GitHub App must list `<DASHBOARD_API_BASE_URL>/dashboard/api/auth/callback` as a callback URL (step 3b). Locally that's `http://localhost:2024/dashboard/api/auth/callback`.
- Login redirects but the session doesn't stick: this is almost always a cookie problem. Locally, keep `DASHBOARD_API_BASE_URL` on `http://` (so cookies are `SameSite=Lax`); in prod use `https://` for both API and frontend and add the frontend origin to `DASHBOARD_ALLOWED_ORIGINS`.
- Login rejected with an org error: `ALLOWED_GITHUB_ORGS` gates dashboard login (and requires the App's Organization → Members: Read-only permission). See step 5.
- Admin pages 403: add your GitHub login or email to `CONFIGURED_ADMINS`.

### Dashboard UI can't reach the backend

- Confirm the backend is running via `make dev` on `:2024` (not `make run` on `:8000`).
- Confirm `ui/.env` has `VITE_DASHBOARD_API_BASE_URL=http://localhost:2024`. If it's empty, the UI falls back to relative `/dashboard/api/*`, which only works behind the Vercel rewrite, not in local dev.

### Sandbox creation failures

- Verify `LANGSMITH_API_KEY_PROD` is set and valid
- Check LangSmith sandbox quotas in your workspace settings
- If the server refuses to start with `DEFAULT_SANDBOX_SNAPSHOT_ID must be set`, build a snapshot (see step 4c) and export its UUID
- If you see `Failed to create sandbox from snapshot '<id>'`, confirm the snapshot exists in your workspace and has status `ready`
- If you get a 403 Forbidden error on the sandbox endpoints, your LangSmith workspace may not have sandbox access enabled — contact LangSmith support

### Agent not responding to comments

- For GitHub: ensure the comment or issue contains `@openswe` (case-insensitive), and the commenter has a user mapping (Admin → User mappings; see "Configure triggering surfaces"). Add any missing user with **Add / update** in that section.
- For Linear: ensure the comment contains `@openswe` (case-insensitive)
- For Slack: ensure the bot is invited to the channel and the message is an `@mention`
- Check server logs for webhook processing errors

### Token encryption errors

- Ensure `TOKEN_ENCRYPTION_KEY` is set (generate with `openssl rand -base64 32`)
- The key must be a valid 32-byte Fernet-compatible base64 string
- For key rotation, `TOKEN_ENCRYPTION_KEY` may be a comma- or newline-separated
  list of keys (most-recent-first). See "Rotating TOKEN_ENCRYPTION_KEY" above.
