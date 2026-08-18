
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
   - **App name**: `Open SWE` (or your preferred name)
   - **Homepage URL**: This can be any valid URL — it's only shown on the GitHub Marketplace page (which you won't be using). Use something like `https://github.com/langchain-ai/open-swe`
   - **Callback URL**: GitHub Apps allow multiple callback URLs (one per line). Add **both**:
     1. `https://smith.langchain.com/host-oauth-callback/<your-provider-id>` — replace `<your-provider-id>` with the ID you chose in step 3a (e.g. `https://smith.langchain.com/host-oauth-callback/your-org-github-oauth`). This is the **agent-runtime** OAuth callback, brokered by LangSmith (step 4b).
     2. `http://localhost:2024/dashboard/api/auth/callback` — the **dashboard-login** OAuth callback (step 8). For production, also add `https://<your-dashboard-api-url>/dashboard/api/auth/callback`. If you distribute the desktop app, add `https://<your-backend-url>/dashboard/api/auth/callback` as well. This is a separate, direct GitHub OAuth flow (not via LangSmith), so it needs its own callback URL.
   - **Request user authorization (OAuth) during installation**: ✅ Enable this
   - **Webhook URL**: `https://<your-ngrok-url>/webhooks/github` — use the ngrok URL from step 2
   - **Webhook secret**: generate one and save it — you'll need it later as `GITHUB_WEBHOOK_SECRET`:
     ```bash
     openssl rand -hex 32
     ```
3. Set permissions:
   - **Repository permissions**:
     - Contents: Read & write
     - Pull requests: Read & write — required to publish review comments and, when an admin explicitly enables the disabled-by-default policy, submit approvals. Bot approvals may not satisfy CODEOWNERS or repository rules that exclude the App identity.
     - Issues: Read & write
     - Checks: Read & write — reports an "Open SWE Review" check run on PRs while an auto-review runs and lets `/baby-sit` read third-party CI conclusions. Without it, check-run creation fails (logged, best-effort), reviews still work, and `/baby-sit` fails closed when it cannot read the complete check set.
     - Commit statuses: Read-only — required for `/baby-sit` to evaluate the complete PR status set, including integrations that report via legacy commit statuses instead of check runs.
     - Actions: Read-only — optional for CI diagnostics and log access. Grant **Read & write** only to enable `/baby-sit` to rerun evidence-backed flaky GitHub Actions jobs. Existing installations must approve this permission elevation. Actions write also permits rerunning, canceling, and deleting workflow runs at the token level; `/baby-sit` is instructed to use only failed-job reruns.
     - Workflows: Read & write — required to let Open SWE directly push branches containing explicitly requested GitHub Actions workflow changes.
     - Metadata: Read-only
   - **Organization permissions** (required only if you plan to set `ALLOWED_GITHUB_ORGS` — see step 5 / Security):
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
First build and push the sandbox Docker image to a registry LangSmith can pull from. The sandbox image is `Dockerfile.sandbox` — pass `-f Dockerfile.sandbox`, because the root `Dockerfile` builds the API server image instead and produces snapshots without `git`, `gh`, `sfw`, the Docker CLI, or the language runtimes agent runs need. On Apple Silicon, force `linux/amd64`

```bash
docker buildx build \
  -f Dockerfile.sandbox \
  --platform linux/amd64 \
  -t <your-docker-hub>/<name-of-your-image> \
  --push .
```

For a multi-arch tag that also runs locally on Apple Silicon:

```bash
docker buildx build \
  -f Dockerfile.sandbox \
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
    docker_image="johanneslangchain/open-swe-sandbox:gh-cli-amd64",  # built from ./Dockerfile.sandbox
    fs_capacity_bytes=128 * 1024**3,
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
# Optional; overrides the snapshot's root FS size at sandbox boot. Default is 128 GiB.
DEFAULT_SANDBOX_SNAPSHOT_FS_CAPACITY_BYTES="137438953472"
# Optional; number of vCPUs per sandbox. Default is 4.
DEFAULT_SANDBOX_VCPUS="4"
# Optional; memory in bytes per sandbox. Default is 16 GiB.
DEFAULT_SANDBOX_MEM_BYTES="17179869184"
# Optional; auto-stop a sandbox after this many seconds of inactivity. Default is 7200 (2 hours). 0 disables.
DEFAULT_SANDBOX_IDLE_TTL_SECONDS="7200"
# Optional; delete a stopped sandbox after this many seconds. Default is 2592000 (30 days). 0 disables.
DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS="2592000"
# Optional; required only for the admin Repository Snapshots page/template generator.
REPO_SNAPSHOT_BASE_IMAGE="<your-docker-hub>/<name-of-your-image>"
```

A base snapshot is required when `SANDBOX_TYPE=langsmith` — either `DEFAULT_SANDBOX_SNAPSHOT_ID` or the runtime setting described below. The server logs a warning at startup when neither the env var nor a stored setting is present, and sandbox creation fails until one is. The snapshot should include the GitHub CLI from `Dockerfile.sandbox`; Open SWE authenticates `git` and `gh` through the LangSmith sandbox proxy using runtime-minted GitHub App installation tokens, not deployment-stored GitHub access tokens.

### Environments

An **environment** pairs a prompt with a snapshot every run boots from, and can span several repos. Admins build one from an **admin thread** (the **Admin** toggle in the composer, available when their login or email is in `CONFIGURED_ADMINS`): the agent provisions its own sandbox — cloning repos, installing toolchains, warming caches — and then captures it. The environment named `default` is the one runs use; any other name is a draft. Records are managed on the admin **Environments** page.

With more than one environment configured, a picker appears in the dashboard composer (any signed-in user, names only), and a Slack thread can pick one with an `env:<name>` tag on the message that opens it — `@Open SWE env:staging fix the flaky test`. Only the opening message can: the sandbox is created once, so a later tag would change the prompt but not the image. A run with no selection uses `default`.

Captures are named `openswe-environment-<name>` (the platform appends its own `:latest` tag, and rejects a name that carries one); set `ENVIRONMENT_SNAPSHOT_PREFIX` to replace the `openswe` prefix when several deployments share one LangSmith workspace. Snapshot resolution for a new sandbox is: the run's environment, then the repo's snapshot, then the base snapshot below.

### Changing the base snapshot without a redeploy

Admins can override `DEFAULT_SANDBOX_SNAPSHOT_ID` at runtime from the **Repository Snapshots** page (**Base snapshot** field). The stored value wins; clearing it falls back to the env var. Per-repo snapshots still take precedence for runs targeting a repo with a ready snapshot.

The same setting is available over the API, which is how the repo that builds your sandbox image can roll a new snapshot out on its own:

```bash
curl -X PUT "$OPEN_SWE_BASE_URL/dashboard/api/sandbox-settings" \
  -H "Authorization: Bearer $ADMIN_GITHUB_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"base_snapshot_id": "<snapshot-uuid>"}'
```

Admin-gated sandbox-settings requests accept two CI credentials in place of the browser session cookie, both as `Authorization: Bearer`:

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

`REPO_SNAPSHOT_BASE_IMAGE` should point at the same published Open SWE sandbox image you used to create the default snapshot (for example, the image built from `./Dockerfile.sandbox`). The admin **Repository Snapshots** page uses it as the `FROM` line when generating per-repo Dockerfile templates. If it is not set, template generation is intentionally disabled so admins do not accidentally build repo-scoped snapshots from a bare image that lacks Open SWE's required tools (`git`, `gh`, `sfw`, language runtimes, and proxy assumptions).

## 5. Set up triggers

Open SWE can be triggered from GitHub, Linear, and/or Slack. **Configure whichever surfaces your team uses — you don't need all of them.**

### GitHub

GitHub triggering works automatically once your GitHub App is set up (step 3). Users can:
- Tag `@openswe` in issue titles or bodies to start a task
- Tag `@openswe` in issue comments for follow-up instructions
- Tag `@openswe` in PR review comments to have it address review feedback

The handles this deployment answers to default to `@openswe,@open-swe,@openswe-dev` and are configurable — set `OPEN_SWE_MENTION_TAGS` to a comma-separated list. Handles are matched on a word boundary, so `@openswe` does not fire on `@openswe-preview`. Give each deployment a distinct handle when more than one shares a GitHub org, Slack workspace, or Linear workspace.

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

A GitHub or Linear webhook is accepted if the resolved repo's org is in `ALLOWED_GITHUB_ORGS` **or** the `owner/repo` is in `ALLOWED_GITHUB_REPOS`. If both are empty, all repos are allowed.

For Slack and dashboard requests, `ALLOWED_GITHUB_ORGS` also adds a prompt-level edit guard. To modify a repository outside those organizations, the user must explicitly request that exact repository with its full `https://github.com/<owner>/<repo>` URL. Repository hints, defaults, shorthand, and contextual links do not qualify. This does not bypass the server-side GitHub/Linear webhook filter above or GitHub credential and App installation permissions.

`ALLOWED_GITHUB_ORGS` also gates **dashboard login**: when set, only GitHub accounts that are active members of one of the listed organizations can complete the OAuth login and receive a session. Membership is verified server-side with the GitHub App installation token (so private memberships are visible and no extra OAuth scope is required), and the check fails closed on any API error. When `ALLOWED_GITHUB_ORGS` is empty, dashboard login is open to any GitHub account (the prior behavior).

> **Observability access**: when team LangSmith credentials are connected, every active member of an organization in `ALLOWED_GITHUB_ORGS` can use the read-only LangSmith trace tools. Only list organizations whose full active membership may access team-level trace data. This does not grant Datadog access.

> **Required GitHub App installation and permission**: install the App in every organization listed in `ALLOWED_GITHUB_ORGS` and grant **Organization → Members: Read-only** (see step 3b). Membership checks resolve each organization's installation and call `GET /orgs/{org}/memberships/{username}`. Missing installations, unapproved permissions, and API errors fail closed. `GITHUB_APP_INSTALLATION_ID` remains the default installation for ordinary GitHub operations.

### Linear (optional)

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
OPENAI_API_KEY=""                      # OpenAI models and dashboard voice dictation
# OPENAI_BASE_URL="https://api.openai.com/v1"  # Optional OpenAI-compatible API base URL
GOOGLE_API_KEY=""                      # Google AI API key (when using google_genai: models)
FIREWORKS_API_KEY=""                   # Fireworks API key (when using fireworks: models)
# Voice dictation uses this OpenAI configuration.
# Admins choose its transcription model in the dashboard Admin page.

# === GitHub App (required) ===
GITHUB_APP_ID=""                       # From step 3c
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
...
-----END RSA PRIVATE KEY-----
"
GITHUB_APP_INSTALLATION_ID=""          # From step 3d

# === GitHub Webhook (required) ===
GITHUB_WEBHOOK_SECRET=""               # The secret you generated in step 3b

# === Mention handles (optional) ===
# Comma-separated handles this deployment answers to, across GitHub, Linear and Slack.
# Defaults to "@openswe,@open-swe,@openswe-dev".
OPEN_SWE_MENTION_TAGS=""               # e.g. "@openswe-preview"
# Comma-separated bot logins to treat as internal rather than untrusted external
# commenters. Set this to the bot logins of any other Open SWE deployments sharing
# these repos.
EXTRA_INTERNAL_BOT_LOGINS=""           # e.g. "openswe-preview[bot]"

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
# Comma-separated list of GitHub orgs allowed by the GitHub/Linear webhook filter.
# Also gates dashboard login and prompts the agent to require an explicit full repository
# URL before editing outside these orgs (requires Organization -> Members: Read-only).
# Leave empty to allow all orgs and disable the prompt-level edit guard.
ALLOWED_GITHUB_ORGS=""                 # e.g. "my-org,my-other-org"
# Comma-separated list of specific owner/repo pairs allowed by the GitHub/Linear webhook filter.
# A repo is accepted if its org is in ALLOWED_GITHUB_ORGS OR its owner/repo is in ALLOWED_GITHUB_REPOS.
# Slack/dashboard access remains bounded by GitHub credentials and App installation permissions.
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
# Optional; lets a GitHub Actions workflow act as an admin over the API via OIDC
# (see step 4c). Empty => off.
ADMIN_OIDC_SUBJECTS=""                 # e.g. "acme/sandbox-images" or "repo:acme/sandbox-images:ref:refs/heads/main"
ADMIN_OIDC_AUDIENCE=""                 # audience the workflow must request; defaults to "open-swe"
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
# Provider: langsmith (default), modal, daytona, runloop, e2b, or local. See CUSTOMIZATION.md.
SANDBOX_TYPE="langsmith"
DEFAULT_SANDBOX_SNAPSHOT_ID=""         # Required when SANDBOX_TYPE=langsmith unless set at runtime by an admin (see step 4c)
DEFAULT_SANDBOX_SNAPSHOT_FS_CAPACITY_BYTES=""  # Root FS size in bytes (default: 128 GiB)
DEFAULT_SANDBOX_VCPUS=""               # vCPUs per sandbox (default: 4)
DEFAULT_SANDBOX_MEM_BYTES=""           # Memory in bytes per sandbox (default: 16 GiB)
DEFAULT_SANDBOX_IDLE_TTL_SECONDS=""    # Auto-stop after N seconds idle (default: 7200; 0 disables)
DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS=""  # Delete N seconds after stop (default: 2592000; 0 disables)
ENVIRONMENT_SNAPSHOT_PREFIX=""         # Prefix for environment snapshot names (default: openswe)

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

The dashboard is the web app in `ui/`. It's a server-rendered TanStack Start app that calls the FastAPI dashboard API from step 7. Run it in a third terminal:

```bash
pnpm install          # from the repo root: ui/ and desktop/ are one pnpm workspace
pnpm run dev          # turbo -> vite dev --port 3000 -> http://localhost:3000
```

No `ui/.env` is needed: the dev server proxies `/dashboard/api/*` to `DASHBOARD_API_URL`, which defaults to `http://localhost:2024`. Point it elsewhere by exporting that variable before `pnpm run dev`. It is read at build time, so a production build bakes in the backend it proxies to.

Because the browser only ever talks to `http://localhost:3000`, no **CORS** preflight is involved. `DASHBOARD_ALLOWED_ORIGINS="http://localhost:3000"` is still required, though: the same allowlist is the backend's CSRF gate for every non-GET request, and it compares the browser's `Origin` — the dashboard's — against the origins it knows. Without it, the dashboard reads fine and every save returns `403 CSRF check failed`.

The `osw_session` cookie has to be set on the dashboard origin too: set `DASHBOARD_API_BASE_URL="http://localhost:3000"` and register `http://localhost:3000/dashboard/api/auth/callback` as a GitHub App callback URL. Keep it on an `http://` URL locally so the cookie uses `SameSite=Lax` rather than `Secure`.

For the dashboard login to succeed, you need (from steps 3c / 6): `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `DASHBOARD_JWT_SECRET`, `DASHBOARD_API_BASE_URL`, `DASHBOARD_BASE_URL`, and `DASHBOARD_ALLOWED_ORIGINS`. To reach the admin pages (user mappings, etc.), add your GitHub login or email to `CONFIGURED_ADMINS`.

Other root scripts run the same task across the workspace through Turborepo: `pnpm run build`, `pnpm run typecheck`, `pnpm run lint`, `pnpm run test`. Scope one to a package with `pnpm --filter open-swe-dashboard run <script>`.

### Run the desktop app (optional)

> **Experimental:** The desktop wrapper is an early-access convenience surface. The web UI is
> the recommended way to use Open SWE.

The Electron app in `desktop/` includes the compiled dashboard UI. It only needs the Open SWE
backend to be running:

```bash
pnpm install                  # from the repo root
pnpm run dev:desktop
```

Development connects to `http://localhost:2024`. To use a hosted backend instead, run
`pnpm --dir desktop run start -- --backend-url=https://your-backend.example.com` or set
`OPEN_SWE_BACKEND_URL`. Create an unpacked application with `pnpm --dir desktop run pack`, or an
installer with `pnpm --dir desktop run dist`. Packaged builds ask for the organization's backend
URL on first launch and store it locally; they never default to the maintainers' deployment. The
GitHub App must allow `<backend-url>/dashboard/api/auth/callback` for desktop login.

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

**Backend — standalone Docker:** the root `Dockerfile` builds a production LangGraph API server image for Open SWE. It is not the sandbox image; build sandbox snapshots from `Dockerfile.sandbox`.

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
  -e DASHBOARD_API_BASE_URL="https://<your-dashboard-or-backend-url>" \
  open-swe
```

The example above assumes Postgres and Redis run on the Docker host. `host.docker.internal` only resolves automatically on Docker Desktop, so `--add-host=host.docker.internal:host-gateway` is what makes it work on a plain Linux Docker Engine. If Postgres and Redis run as their own containers, drop the flag and point `DATABASE_URI` / `REDIS_URI` at their service names on a shared Docker network instead.

Set all environment variables from step 6, plus the standalone Agent Server requirements: `DATABASE_URI`, `REDIS_URI`, `LANGSMITH_API_KEY` (unless tracing is disabled for your deployment), and `LANGGRAPH_CLOUD_LICENSE_KEY` for the production LangGraph server. Expose the container's port `8000` through your ingress. Do not use scale-to-zero hosting; background runs rely on Redis/Postgres-backed workers staying available. If the built-in LangGraph API routes are reachable from the public internet, put the service behind a private network, API gateway, or custom LangGraph auth before using `LANGGRAPH_AUTH_TYPE=noop`.

Set `LANGGRAPH_URL` to the public backend URL so webhooks and the dashboard can create runs against this same server. Set `DASHBOARD_API_BASE_URL` to the URL browsers use for dashboard API requests and OAuth callbacks: either the backend URL for direct cross-origin calls, or the dashboard/Vercel URL when a same-origin rewrite proxies `/dashboard/api/*`. Update your webhook URLs (Linear, Slack, GitHub App) and the GitHub App / Slack OAuth callback URLs to your production URLs. The dashboard GitHub App callback must be `<DASHBOARD_API_BASE_URL>/dashboard/api/auth/callback`.

The `langgraph.json` at the project root defines the graphs and HTTP app baked into the image:

```json
{
  "graphs": {
    "agent": "agent.graphs.agent:traced_agent",
    "reviewer": "agent.graphs.reviewer:traced_reviewer_agent",
    "analyzer": "agent.graphs.analyzer:traced_analyzer",
    "chat": "agent.graphs.chat:traced_chat_agent",
    "scheduler": "agent.graphs.scheduler:get_scheduler"
  },
  "http": {
    "app": "agent.webapp:app"
  }
}
```

**Backend — LangGraph Cloud / Platform:** alternatively, push your code to a GitHub repository, connect the repo to LangGraph Cloud, set the same environment variables in the deployment config, and use the hosted deployment URL for `LANGGRAPH_URL` and webhook callbacks.

**Dashboard** — the `ui/` app deploys to [Vercel](https://vercel.com/). The build detects Vercel and emits a Nitro server that renders routes on request, so set `DASHBOARD_API_URL` in the Vercel project to your hosted backend URL. Browser requests to `/dashboard/api/*` are rewritten to it at the platform edge (no function in the path, so streaming responses are unaffected), and server renders call it directly with the request's `osw_session` cookie forwarded.

Requests are therefore **same-origin**: set both `DASHBOARD_API_BASE_URL` and the GitHub App dashboard callback URL to the Vercel/dashboard origin (for example, `https://your-dashboard.vercel.app/dashboard/api/auth/callback`). The OAuth callback response then sets the `osw_session` cookie on the dashboard host, and later `/dashboard/api/*` requests include it.

Alternatively, you can have the browser call the backend cross-origin: set `VITE_DASHBOARD_API_BASE_URL` to the hosted backend origin, set `DASHBOARD_API_BASE_URL` to that same backend origin, and include the dashboard origin in `DASHBOARD_ALLOWED_ORIGINS`. Keep `DASHBOARD_API_URL` pointed at the same backend so server renders reach it too. In this mode `osw_session` belongs to the backend's origin, so the dashboard's own requests never carry it and the session is resolved on the client instead — pages render unauthenticated and fill in after hydration.

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
- Confirm the dev server is proxying: `curl -i http://localhost:3000/dashboard/api/me` should return the backend's `401`, not an HTML page. If the backend is on another port, export `DASHBOARD_API_URL` before `pnpm run dev`.

### Sandbox creation failures

- Verify `LANGSMITH_API_KEY_PROD` is set and valid
- Check LangSmith sandbox quotas in your workspace settings
- If sandbox creation fails with `No base snapshot configured`, build a snapshot (see step 4c) and either export its UUID as `DEFAULT_SANDBOX_SNAPSHOT_ID` or set it as the base snapshot on the admin **Repository Snapshots** page
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
