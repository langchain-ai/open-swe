<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="apps/docs/logo/dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="apps/docs/logo/light.svg">
    <img src="apps/docs/logo/dark.svg" alt="Open SWE Logo" width="35%">
  </picture>
</div>

<div align="center">
  <h1>Open SWE - An Open-Source Asynchronous Coding Agent</h1>
</div>

Open SWE is an open-source cloud-based asynchronous coding agent built with [LangGraph](https://docs.langchain.com/oss/javascript/langgraph/overview). It autonomously understands codebases, plans solutions, and executes code changes across entire repositories—from initial planning to opening pull requests.

>
> **Note: you're required to set your own LLM API keys to use the demo.**

> [!NOTE]
> 💬 Read the **announcement blog post [here](https://blog.langchain.com/introducing-open-swe-an-open-source-asynchronous-coding-agent/)**

# Features

![UI Screenshot](./static/ui-screenshot.png)

- 🔗 **Trigger from Linear, Slack, or GitHub** — mention `@openswe` in a Linear comment, Slack thread, or GitHub PR comment to kick off a task
- 👀 **Instant acknowledgement** — reacts with 👀 the moment it picks up your message so you know it's on it
- 💬 **Message it while it's running** — send follow-up messages mid-task and it'll pick them up before its next step
- 🔀 **Run multiple tasks in parallel** — each task runs in its own isolated cloud sandbox, no queuing
- 🔐 **GitHub OAuth built-in** — authenticates with your GitHub account automatically, no token setup needed
- 🚀 **Opens PRs automatically** — commits changes and opens a draft PR when done, linked back to your Linear ticket


## Installation

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- [LangGraph CLI](https://langchain-ai.github.io/langgraph/cloud/reference/cli/)
- [ngrok](https://ngrok.com/) (for exposing local webhooks)

### 1. Clone the repo

```bash
git clone https://github.com/langchain-ai/open-swe.git
cd open-swe/apps/agent
```

### 2. Install dependencies

```bash
uv sync
```

### 3. Set up the Linear webhook

In a terminal, start ngrok to get your public URL:

```bash
ngrok http 2024
# e.g. https://xxxx.ngrok.io

```

Then in Linear:

1. Go to **Settings** → **API** → **Webhooks** → **New webhook**
2. Fill in:
   - **Label**: `open-swe`
   - **URL**: `https://xxxx.ngrok.io/webhooks/linear`
   - **Secret**: generate a random string — copy it, you'll need it for `LINEAR_WEBHOOK_SECRET`
3. Under **Data change events**, enable **Comments** → `Create` only
4. Click **Create webhook**

To get your `LINEAR_API_KEY`:

1. Go to **Settings** → **API** → **Personal API keys** → **New API key**
2. Name it `open-swe` and copy the key

### 4. Set environment variables

Create a `.env` file in `apps/agent/` with the following:

```bash
# LangSmith
LANGSMITH_API_KEY_PROD=""           # Your LangSmith API key
LANGCHAIN_TRACING_V2="true"
LANGCHAIN_PROJECT=""

# LLM
ANTHROPIC_API_KEY=""                # Anthropic API key (recommended default provider)

# GitHub App (Bot)
GITHUB_APP_ID=""                    # GitHub App ID
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
...
-----END RSA PRIVATE KEY-----
"
GITHUB_APP_INSTALLATION_ID=""       # GitHub App installation ID

# GitHub Webhook
GITHUB_WEBHOOK_SECRET=""            # Secret for verifying GitHub webhooks

# Linear
LINEAR_API_KEY=""                   # Linear API key (from step 3)
LINEAR_WEBHOOK_SECRET=""            # Secret you set when creating the webhook (from step 3)

# Slack (optional)
SLACK_BOT_TOKEN=""
SLACK_BOT_USER_ID=""
SLACK_BOT_USERNAME=""
SLACK_SIGNING_SECRET=""

# Sandbox
DEFAULT_SANDBOX_TEMPLATE_NAME=""    # LangSmith sandbox template name (uses default if not set)

# Token encryption
# Generate with: openssl rand -base64 32
TOKEN_ENCRYPTION_KEY=""
```

### 5. Run the agent

```bash
uv run langgraph dev --no-browser
```

The LangGraph server runs on `http://localhost:2024` and serves the webhook endpoints automatically.

### 6. Verify it works

Comment `@openswe` on any Linear issue. You should see:
- A 👀 reaction on your comment within a few seconds
- A new run appear in your LangSmith project

---

## Usage

Open SWE can be used in multiple ways:

- 📋 **From Linear**. Mention `@openswe` in a comment on any Linear issue to trigger the agent. It will automatically read the issue description and full context, then autonomously start working on it. You can also include additional instructions in the comment if needed (e.g. `@openswe focus on the auth module`).
- 🐙 **GitHub (for Open SWE-generated PRs)**. In PRs which Open SWE has created, you can tag it in comments or reviews via `@openswe` to have it resolve reviews automatically for you. Tagging `@openswe` on an Open SWE generated PR will create a new run passing all of the comments from the PR as the prompt. Any changes will be directly committed back to the same branch. 