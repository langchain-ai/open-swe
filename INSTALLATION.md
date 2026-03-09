# Installation Guide

This guide walks you through setting up Open SWE end-to-end: local development, GitHub App creation, Linear and Slack webhooks, and production deployment.

## Prerequisites

- Python 3.11+
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

## 2. Create a GitHub App

Open SWE authenticates as a [GitHub App](https://docs.github.com/en/apps/creating-github-apps) to clone repos, push branches, and open PRs.

1. Go to **GitHub Settings** → **Developer settings** → **GitHub Apps** → **New GitHub App**
2. Fill in:
   - **App name**: `open-swe` (or your preferred name)
   - **Homepage URL**: any valid URL
   - **Callback URL**: Set this to `https://smith.langchain.com/host-oauth-callback/<your-provider-id>` (replace `<your-provider-id>` with the actual provider ID you configure in LangSmith during step #3. e.g. `github-oauth-provider`)
   - **Request user authorization (OAuth) during installation**: Enable this
   - **Webhook URL**: `https://<your-ngrok-url>/webhooks/github` (you'll set this up in step 4)
   - **Webhook secret**: generate with `openssl rand -hex 32` — save this for `GITHUB_WEBHOOK_SECRET`
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
6. Note the **App ID** from the app settings page - you'll need this for the `GITHUB_APP_ID` environment variable.
7. Generate a **private key** (scroll down on the app page → **Generate a private key**). Save the `.pem` file contents. You'll need to set this under `GITHUB_APP_PRIVATE_KEY`.
8. **Install the app** on the repositories you want Open SWE to access:
   - Go to your app's page → **Install App** → select your org/account → choose repositories
   - Note the **Installation ID** from the URL after installation (e.g. `https://github.com/settings/installations/12345678` → `12345678`) - you'll need to set this under `GITHUB_APP_INSTALLATION_ID`

## 3. Set up LangSmith

Open SWE uses [LangSmith](https://smith.langchain.com/) for two things:
- **Tracing**: all agent runs are logged for debugging and observability
- **Sandboxes**: each task runs in an isolated LangSmith cloud sandbox

1. Create a [LangSmith account](https://smith.langchain.com/) if you don't have one
2. Go to **Settings** → **API Keys** → create a new API key
3. Save it as `LANGSMITH_API_KEY_PROD`

### GitHub OAuth (for user authentication)

Open SWE resolves GitHub tokens per-user via LangSmith's OAuth integration. This lets each user authenticate with their own GitHub account rather than sharing a single bot token.

You'll need these from your LangSmith workspace settings:
- `GITHUB_OAUTH_PROVIDER_ID` — the OAuth provider ID configured in LangSmith (e.g. `github-oauth-provider`)

> **Note**: If these aren't configured, the agent will fall back to the GitHub App's installation token for all operations.

### Sandbox templates (optional)

You can configure a custom sandbox template for the agent's execution environment:

- `DEFAULT_SANDBOX_TEMPLATE_NAME` — name of a LangSmith sandbox template
- `DEFAULT_SANDBOX_TEMPLATE_IMAGE` — Docker image for the sandbox

If not set, the default LangSmith sandbox image is used.

## 4. Set up triggers

Open SWE can be triggered from Linear, Slack, or GitHub. Configure whichever invocation surfaces your team uses — you don't need all of them.

### Linear

Open SWE listens for Linear comments that mention `@openswe`.

**Create a webhook:**

1. In Linear, go to **Settings** → **API** → **Webhooks** → **New webhook**
2. Fill in:
   - **Label**: `open-swe`
   - **URL**: `https://<your-ngrok-url>/webhooks/linear`
   - **Secret**: generate with `openssl rand -hex 32` — save this for `LINEAR_WEBHOOK_SECRET`
3. Under **Data change events**, enable **Comments** → `Create` only
4. Click **Create webhook**

**Get your API key:**

1. Go to **Settings** → **API** → **Personal API keys** → **New API key**
2. Name it `open-swe`, select **All access**, and copy the key
3. Save it as `LINEAR_API_KEY`

**Configure team-to-repo mapping:**

Open SWE routes Linear issues to GitHub repos based on the Linear team and project. The mapping is defined in `agent/webapp.py` in the `LINEAR_TEAM_TO_REPO` dict:

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

- **Flat mapping**: team name → single repo
- **Nested mapping**: team name → project name → repo, with an optional `default` fallback

Update this to match your Linear workspace structure.

### Slack

**Create a Slack App:**

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From a manifest**
2. Copy the following Slack App Manifest, and paste it in
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
            "https://smith.langchain.com/host-oauth-callback/<replace-with-your-langsmith-oauth-provider-id>"
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
            "request_url": "<replace-with-your-langsmith-deployment-url-or-ngrok-url>/webhooks/slack",
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

Place the two URLs with their proper values:
1. **redirect_urls**: `"https://smith.langchain.com/host-oauth-callback/<replace-with-your-langsmith-oauth-provider-id>"` add your LangSmith OAuth provider ID you set when creating the OAuth provider in LangSmith.
2. **request_url**: `"https://<your-ngrok-url>/webhooks/slack"` add your ngrok URL pointing to `http://localhost:2024` for local development, or your LangSmith deployment URL if deployed on LangSmith Deployments.

</details>
3. Copy the following App Manifest and paste it in the Slack App configuration page.

Install the app to your workspace and copy the **Bot User OAuth Token** (`xoxb-...`).

**Credentials you'll need:**

- `SLACK_BOT_TOKEN`: the Bot User OAuth Token (`xoxb-...`)
- `SLACK_SIGNING_SECRET`: found under **Basic Information** → **App Credentials**
- `SLACK_BOT_USER_ID`: the bot's user ID (find it in Slack by clicking the bot's profile)
- `SLACK_BOT_USERNAME`: the bot's display name (e.g. `open-swe`)

**Configure default repo:**

Slack messages are routed to a default repo unless the user specifies one with `repo:owner/name`:

```bash
SLACK_REPO_OWNER="my-org"      # Default GitHub org
SLACK_REPO_NAME="my-repo"      # Default GitHub repo
```

### GitHub

GitHub triggering works automatically once your GitHub App is set up (step 2). Tag `@openswe` in PR comments on agent-created PRs to have it address review feedback and push fixes to the same branch.

## 5. Environment variables

Create a `.env` file in the project root:

```bash
# === LangSmith ===
LANGSMITH_API_KEY_PROD=""              # LangSmith API key
LANGCHAIN_TRACING_V2="true"
LANGCHAIN_PROJECT=""                   # LangSmith project name for traces

# === LLM ===
ANTHROPIC_API_KEY=""                   # Anthropic API key (default provider)

# === GitHub App ===
GITHUB_APP_ID=""                       # From step 2
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
...
-----END RSA PRIVATE KEY-----
"
GITHUB_APP_INSTALLATION_ID=""          # From step 2

# === GitHub Webhook ===
GITHUB_WEBHOOK_SECRET=""               # openssl rand -hex 32

# === GitHub OAuth (via LangSmith) ===
GITHUB_OAUTH_PROVIDER_ID=""            # Optional — LangSmith OAuth provider

# === Linear ===
LINEAR_API_KEY=""                      # From step 4
LINEAR_WEBHOOK_SECRET=""               # From step 4

# === Slack (optional) ===
SLACK_BOT_TOKEN=""                     # From step 4
SLACK_BOT_USER_ID=""
SLACK_BOT_USERNAME=""
SLACK_SIGNING_SECRET=""
SLACK_REPO_OWNER=""                    # Default org for Slack-triggered tasks
SLACK_REPO_NAME=""                     # Default repo for Slack-triggered tasks

# === Sandbox ===
DEFAULT_SANDBOX_TEMPLATE_NAME=""       # Optional — custom sandbox template
DEFAULT_SANDBOX_TEMPLATE_IMAGE=""      # Optional — custom Docker image

# === Token Encryption ===
TOKEN_ENCRYPTION_KEY=""                # openssl rand -base64 32
```

## 6. Start the server (local development)

Start ngrok in one terminal to expose your local server:

In one terminal, expose your local server:

```bash
ngrok http 2024
```

Copy the HTTPS URL (e.g. `https://xxxx.ngrok.io`) and update your webhook URLs from step 4.

Then start the LangGraph server in another terminal:

```bash
uv run langgraph dev --no-browser
```

The server runs on `http://localhost:2024` with these endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /webhooks/linear` | Linear comment webhooks |
| `GET /webhooks/linear` | Linear webhook verification |
| `POST /webhooks/slack` | Slack event webhooks |
| `GET /webhooks/slack` | Slack webhook verification |
| `GET /health` | Health check |

## 7. Verify it works

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

## 8. Production deployment

For production, deploy the agent on [LangGraph Cloud](https://langchain-ai.github.io/langgraph/cloud/) instead of running locally:

1. Push your code to a GitHub repository
2. Connect the repo to LangGraph Cloud
3. Set all environment variables from step 5 in the deployment config
4. Update your Linear and Slack webhook URLs to point to your production URL (replace the ngrok URL)

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

- Verify ngrok is running and the URL matches what's configured in Linear/Slack
- Check the ngrok web inspector at `http://localhost:4040` for incoming requests
- Ensure you enabled the correct event types (Comments → Create for Linear, `app_mention` for Slack)

### GitHub authentication errors

- Verify `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, and `GITHUB_APP_INSTALLATION_ID` are set correctly
- Ensure the GitHub App is installed on the target repositories
- Check that the private key includes the full `-----BEGIN RSA PRIVATE KEY-----` and `-----END RSA PRIVATE KEY-----` lines

### Sandbox creation failures

- Verify `LANGSMITH_API_KEY_PROD` is set and valid
- Check LangSmith sandbox quotas in your workspace settings
- If using a custom template, verify `DEFAULT_SANDBOX_TEMPLATE_NAME` matches an existing template

### Agent not responding to comments

- For Linear: ensure the comment contains `@openswe` (case-insensitive)
- For Slack: ensure the bot is invited to the channel and the message is an `@mention`
- Check server logs for webhook processing errors

### Token encryption errors

- Ensure `TOKEN_ENCRYPTION_KEY` is set (generate with `openssl rand -base64 32`)
- The key must be a valid 32-byte Fernet-compatible base64 string
