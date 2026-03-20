
# Installation Guide

This guide walks you through setting up Open SWE end-to-end: local development, GitHub App creation, LangSmith configuration, webhooks, and production deployment.

> **The steps are ordered to avoid forward references.** Each step only depends on things you've already completed.

## Prerequisites

- **Python 3.11 – 3.13** (3.14 is not yet supported due to dependency constraints)
- [uv](https://docs.astral.sh/uv/) package manager
- [LangGraph CLI](https://langchain-ai.github.io/langgraph/cloud/reference/cli/)
- [ngrok](https://ngrok.com/) (for local development — exposes webhook endpoints to the internet)

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
github-oauth-provider
```

Write this down. You'll use it in the callback URL below and again in step 4 when configuring LangSmith.

### 3b. Create the app

1. Go to **GitHub Settings → Developer settings → GitHub Apps → New GitHub App**
2. Fill in:
   - **App name**: `open-swe` (or your preferred name)
   - **Homepage URL**: This can be any valid URL — it's only shown on the GitHub Marketplace page (which you won't be using). Use something like `https://github.com/langchain-ai/open-swe`
   - **Callback URL**: `https://smith.langchain.com/host-oauth-callback/<your-provider-id>` — replace `<your-provider-id>` with the ID you chose in step 3a (e.g. `https://smith.langchain.com/host-oauth-callback/github-oauth-provider`)
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
     - Metadata: Read-only
4. Under **Subscribe to events**, enable:
   - `Issue comment`
   - `Pull request review`
   - `Pull request review comment`
5. Click **Create GitHub App**

### 3c. Collect credentials

After creating the app:

1. **App ID** — shown at the top of the app's settings page. Save this as `GITHUB_APP_ID`.
2. **Private key** — scroll down to **Private keys** → click **Generate a private key**. A `.pem` file will download. Save its contents as `GITHUB_APP_PRIVATE_KEY`.

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

### 4b. Configure GitHub OAuth (optional but recommended)

This lets each user authenticate with their own GitHub account. Without it, all operations use the GitHub App's installation token (a shared bot identity).

**What this affects:**
- **With per-user OAuth**: PRs and commits show the triggering user's identity; each user's GitHub permissions are respected
- **Without it (bot-token-only mode)**: all PRs and commits appear as the GitHub App bot; the app's installation-level permissions are used for everything

To set up per-user OAuth:

1. In LangSmith, go to **Settings → OAuth Providers → Add Provider**
2. Set the **Provider ID** to the same string you chose in step 3a (e.g. `github-oauth-provider`)
3. Enter the **Client ID** and **Client Secret** from your GitHub App (found on the GitHub App settings page under **OAuth credentials**)
4. Save. You'll reference this Provider ID as `GITHUB_OAUTH_PROVIDER_ID` in your environment variables.

### 4c. Sandbox templates (optional)

LangSmith sandboxes provide the isolated execution environment for each agent run. You can create a template using the same Docker image we use internally by visiting the sandbox page in LangSmith, and setting the following fields:

- `Name`: you can set this to whatever name you'd like, e.g. `open-swe`
- `Container Image`: `bracelangchain/deepagents-sandbox:v1` this contains the [Docker file in this repo](./Dockerfile)
- `CPU`: `500m`
- `Memory`: `4096Mi`
- `Ephemeral Storage`: `15Gi`

> If you don't set these, you can use a Python based docker image in the template.

## 5. Set up triggers

Open SWE can be triggered from GitHub, Linear, Slack, and/or Webex. **Configure whichever surfaces your team uses — you don't need all of them.**

### GitHub

GitHub triggering works automatically once your GitHub App is set up (step 3). Users can:
- Tag `@openswe` in issue titles or bodies to start a task
- Tag `@openswe` in issue comments for follow-up instructions
- Tag `@openswe` in PR review comments to have it address review feedback

To control which GitHub users can trigger the agent, add them to the `GITHUB_USER_EMAIL_MAP` in `agent/utils/github_user_email_map.py`:

```python
GITHUB_USER_EMAIL_MAP = {
    "their-github-username": "their-email@example.com",
}
```

You should also add the GitHub organization which should be allowed to be triggered from in GitHub:

`agent/webapp.py`
```python
ALLOWED_GITHUB_ORGS = "langchain-ai,anthropics"
```

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

### Slack (optional)

**Create a Slack App:**

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From a manifest**
2. Copy the manifest below, replacing the two placeholder URLs:
   - Replace `<your-provider-id>` with the OAuth provider ID from step 3a
   - Replace `<your-ngrok-url>` with the ngrok URL from step 2

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
            "https://smith.langchain.com/host-oauth-callback/<your-provider-id>"
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
        "org_deploy_enabled": false,
        "socket_mode_enabled": false,
        "token_rotation_enabled": false
    }
}
```

</details>

3. Install the app to your workspace and copy the **Bot User OAuth Token** (`xoxb-...`)

**Credentials you'll need:**

- `SLACK_BOT_TOKEN`: the Bot User OAuth Token (`xoxb-...`)
- `SLACK_SIGNING_SECRET`: found under **Basic Information → App Credentials**
- `SLACK_BOT_USER_ID`: the bot's user ID (find it in Slack by clicking the bot's profile)
- `SLACK_BOT_USERNAME`: the bot's display name (e.g. `open-swe`)

**Configure default repo:**

Slack messages are routed to a default repo unless the user specifies one with `repo:owner/name`:

```bash
SLACK_REPO_OWNER="my-org"      # Default GitHub org
SLACK_REPO_NAME="my-repo"      # Default GitHub repo
```

### Webex (optional)

Open SWE can be triggered from Webex spaces when a user @mentions the bot.

**Create a Webex Bot:**

1. Go to [developer.webex.com/my-apps/new/bot](https://developer.webex.com/my-apps/new/bot)
2. Fill in the bot name, username, and icon
3. Click **Add Bot** and copy the **Bot Access Token** — save it as `WEBEX_BOT_TOKEN`
4. Note the bot's email address (e.g. `open-swe@webex.bot`) — save it as `WEBEX_BOT_EMAIL`

> **Important:** The bot access token is only shown once. If you lose it, you can regenerate it from the bot's settings page.

**Create a webhook:**

First, generate a webhook secret and save it as `WEBEX_WEBHOOK_SECRET`:

```bash
openssl rand -hex 32
```

Then register the webhook using the Webex API. Replace `<your-ngrok-url>` with the URL from step 2, and `<your-webhook-secret>` with the secret you just generated:

```bash
curl -X POST https://webexapis.com/v1/webhooks \
  -H "Authorization: Bearer YOUR_BOT_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "open-swe-mentions",
    "targetUrl": "https://<your-ngrok-url>/webhooks/webex",
    "resource": "messages",
    "event": "created",
    "filter": "mentionedPeople=me",
    "secret": "<your-webhook-secret>"
  }'
```

The `mentionedPeople=me` filter ensures the bot only receives messages where it is @mentioned.

**Add the bot to a Webex space:**

Before you can test, add the bot to a Webex space (room) where you want to use it. In the Webex app, open the space → click the People icon → Add People → search for the bot's email address.

**Credentials you'll need:**

- `WEBEX_BOT_TOKEN`: the Bot Access Token from bot creation
- `WEBEX_BOT_EMAIL`: the bot's email address (e.g. `open-swe@webex.bot`)
- `WEBEX_WEBHOOK_SECRET`: the secret you used when creating the webhook

**Configure default repo:**

Webex messages are routed to a default repo unless the user specifies one with `repo:owner/name`:

```bash
WEBEX_REPO_OWNER="my-org"      # Default GitHub org
WEBEX_REPO_NAME="my-repo"      # Default GitHub repo
```

## 6. Environment variables

Copy the provided `.env.example` to `.env` and fill in the values for the triggers you configured:

```bash
cp .env.example .env
```

The file is organized by section (LangSmith, LLM, GitHub App, Linear, Slack, Webex, Sandbox) with inline comments explaining where each value comes from. Only fill in the sections relevant to the triggers you set up — leave the rest empty.

## 7. Start the server

Make sure ngrok is still running from step 2, then start the LangGraph server in a second terminal:

```bash
uv run langgraph dev --no-browser
```

The server runs on `http://localhost:2024` with these endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /webhooks/github` | GitHub issue/PR/comment webhooks |
| `POST /webhooks/linear` | Linear comment webhooks |
| `GET /webhooks/linear` | Linear webhook verification |
| `POST /webhooks/slack` | Slack event webhooks |
| `GET /webhooks/slack` | Slack webhook verification |
| `POST /webhooks/webex` | Webex message webhooks |
| `GET /webhooks/webex` | Webex webhook verification |
| `GET /health` | Health check |

## 8. Verify it works

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
3. You should see:
   - An 👀 reaction on your message
   - A reply in the thread with the agent's response

### Webex

1. In any Webex space where the bot has been added, send a message mentioning the bot: `@open-swe what's in the repo?`
2. You should see:
   - A new run in your LangSmith project
   - The agent replies in the same Webex thread with its response

## 9. Production deployment

For production, deploy the agent on [LangGraph Cloud](https://langchain-ai.github.io/langgraph/cloud/) instead of running locally:

1. Push your code to a GitHub repository
2. Connect the repo to LangGraph Cloud
3. Set all environment variables from step 6 in the deployment config
4. Update your webhook URLs (Linear, Slack, GitHub App, Webex) to point to your production URL (replace the ngrok URL). For Webex, you'll need to delete the old webhook and create a new one with the production URL using the same `curl` command from step 5.

The `langgraph.json` at the project root already defines the graph entry point and HTTP app:

```json
{
  "graphs": {
    "agent": "agent.server:get_agent"
  },
  "http": {
    "app": "agent.webapp:app"
  }
}
```

## Troubleshooting

### Webhook not receiving events

- Verify ngrok is running and the URL matches what's configured in GitHub/Linear/Slack/Webex
- Check the ngrok web inspector at `http://localhost:4040` for incoming requests
- Ensure you enabled the correct event types (Comments → Create for Linear, `app_mention` for Slack, Issues + Issue comment for GitHub, `messages:created` with `mentionedPeople=me` for Webex)
- **Webhook secrets are required** — if `GITHUB_WEBHOOK_SECRET`, `LINEAR_WEBHOOK_SECRET`, `SLACK_SIGNING_SECRET`, or `WEBEX_WEBHOOK_SECRET` is not set, all requests to that endpoint will be rejected with 401
- **Webex webhook not firing?** — verify the webhook was created successfully by listing your webhooks: `curl -H "Authorization: Bearer $WEBEX_BOT_TOKEN" https://webexapis.com/v1/webhooks`. Confirm the `targetUrl` matches your ngrok URL and the `status` is `active`.

### GitHub authentication errors

- Verify `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, and `GITHUB_APP_INSTALLATION_ID` are set correctly
- Ensure the GitHub App is installed on the target repositories
- Check that the private key includes the full `-----BEGIN RSA PRIVATE KEY-----` and `-----END RSA PRIVATE KEY-----` lines

### Sandbox creation failures

- Verify `LANGSMITH_API_KEY_PROD` is set and valid
- Check LangSmith sandbox quotas in your workspace settings
- If you see `Failed to check template ''`, ensure either `DEFAULT_SANDBOX_TEMPLATE_NAME` is set or that your LangSmith API key has permissions to create sandbox templates
- If you get a 403 Forbidden error on the sandbox templates endpoint, your LangSmith workspace may not have sandbox access enabled — contact LangSmith support

### Agent not responding to comments

- For GitHub: ensure the comment or issue contains `@openswe` (case-insensitive), and the commenter's GitHub username is in `GITHUB_USER_EMAIL_MAP`
- For Linear: ensure the comment contains `@openswe` (case-insensitive)
- For Slack: ensure the bot is invited to the channel and the message is an `@mention`
- For Webex: ensure the bot has been added to the space and the message is an `@mention`. Verify `WEBEX_BOT_EMAIL` matches the bot's actual email address — a mismatch will cause the bot to process its own messages in a loop.
- Check server logs for webhook processing errors

### Token encryption errors

- Ensure `TOKEN_ENCRYPTION_KEY` is set (generate with `openssl rand -base64 32`)
- The key must be a valid 32-byte Fernet-compatible base64 string
