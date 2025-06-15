# Open SWE

> [!WARNING]
> Open SWE is under active development and is not yet ready for production use.

Open SWE is an open-source cloud based coding agent.

## Usage

First, clone the repository:

```bash
git clone https://github.com/langchain-ai/open-swe.git
cd open-swe
```

Next, install dependencies:

```bash
yarn install
```

Copy the `.env.example` files to `.env` in their respective packages, and fill in the values:

```bash
# Agent .env file
cp ./apps/open-swe/.env.example ./apps/open-swe/.env
# Web .env file
cp ./apps/web/.env.example ./apps/web/.env
```

The open-swe `.env` file should contain the following variables:

```bash
# ------------------LangSmith tracing------------------
LANGCHAIN_PROJECT="default"
LANGCHAIN_API_KEY=""
LANGCHAIN_TRACING_V2=true
# -----------------------------------------------------

# Defaults to Anthropic models, OpenAI & Google keys are optional, unless using those models
ANTHROPIC_API_KEY=""
OPENAI_API_KEY=""
GOOGLE_API_KEY=""

# Daytona API key for accessing and modifying the code in the cloud sandbox.
DAYTONA_API_KEY=""

# Encryption key for GitHub tokens (32-byte hex string for AES-256)
# Should be the same value as the one used in the web app.
# Can be generated via: `openssl rand -hex 32`
GITHUB_TOKEN_ENCRYPTION_KEY=""
# Used for setting the git user name & email for commits.
GITHUB_APP_NAME="open-swe-dev"
```

And the web `.env` file should contain the following variables:

```bash
# Change both to production URLs when deployed
NEXT_PUBLIC_API_URL="http://localhost:3000/api"
LANGGRAPH_API_URL="http://localhost:2024"
NEXT_PUBLIC_ASSISTANT_ID="open-swe"

# For the GitHub OAuth flow
GITHUB_APP_CLIENT_ID=""
GITHUB_APP_CLIENT_SECRET=""
GITHUB_APP_REDIRECT_URI="http://localhost:3000/api/auth/github/callback"

GITHUB_APP_NAME="open-swe-dev"
GITHUB_APP_ID=""
GITHUB_APP_PRIVATE_KEY=""

# Encryption key for GitHub tokens (32-byte hex string for AES-256)
# Should be the same value as the one used in the open-swe app.
# Can be generated via: `openssl rand -hex 32`
GITHUB_TOKEN_ENCRYPTION_KEY=""
```

**REMINDER**: The `GITHUB_TOKEN_ENCRYPTION_KEY` environment variable must be the same in both the web and open-swe apps.

To get the GitHub App secrets, first create a new GitHub app (note: this is not the same as the OAuth app) in [the developer settings](https://github.com/settings/apps/new).

Give the app a name and description.

Under `Callback URL`, set it to: `http://localhost:3000/api/auth/github/callback` for local development. Then, uncheck `Expire user authorization tokens`, and check `Request user authorization (OAuth) during installation`.

Under `Post installation`, check `Redirect on update`.

Under `Webhook`, uncheck `Active`.

Under `Repository permissions`, give the app the following permissions:

- `Contents` - `Read & Write`
- `Metadata` - `Read & Write`
- `Pull requests` - `Read & Write`
- `Issues` - `Read & Write`

Finally, under `Where can this GitHub App be installed?` ensure `Any account` is selected.

After creating the app, you will be taken to the app's settings page. Copy/generate the following fields for your environment variables:

`App ID` - `GITHUB_APP_ID`
`Client ID` - `GITHUB_APP_CLIENT_ID`
`Client secrets` - Generate a new secret key, and set it under `GITHUB_APP_CLIENT_SECRET`
Scroll down to `Private keys`, and generate a new private key. This will download a file. Set the contents of this file under `GITHUB_APP_PRIVATE_KEY`.
Set `GITHUB_APP_REDIRECT_URI` to `http://localhost:3000/api/auth/github/callback` for local development.
Set `GITHUB_APP_NAME` to the name of your app.

That's it! You can now authenticate users with GitHub, and generate tokens for them.

## Running the graph

> [!INFO]
> Since Open SWE is still under development, the following requires hard coding the repository information. This will be changed before the release.

To run the graph, first you must set which repository you want Open SWE to make changes to inside the `web` package. To do this, search for instances of `repo: "open-swe",` inside the `apps/web` directory. This should yield _7_ results in _5_ files. Go through each of these, and modify the `owner` and `repo` properties to match the repository you want Open SWE to make changes to. (ensure your GitHub PAT has access to this repository).

After updating these values, you should start the web server:

```bash
# Inside `apps/web`
yarn dev
```

And the agent server:

```bash
# Inside `apps/open-swe`
yarn dev
```

You can now open the web app at `http://localhost:3000` and send a request.

Sending a request will trigger the agent, which first enters the planning subgraph. This will run for just under a minute, and once it's finished, it'll interrupt the graph with the proposed plan.

To start the agent execution flow, you can:
- accept the plan as is
- edit & submit the plan

Both of these actions will trigger the agent to start the execution flow with the plan.

If you are not happy with the plan, you can also send a response. This will cause the agent to rewrite the plan according to the instructions you provided.

> [!TIP]
> Responding to the plan interrupt will not trigger the agent to re-enter the planning subgraph, so it will not be able to gather more information than it already has. If you want the agent to start over and gather new context, you must create a new chat and send an updated prompt.

Once you've accepted the plan, it will begin the execution flow. When the agent finishes, a pull request will automatically be opened in the repository.

> [!NOTE]
> The chat UI is very buggy at the moment, so I recommend also having the agent server terminal window open so you can inspect the logs as the agent runs.

## Accessing Changes

Open SWE will automatically create a branch whenever you create a new thread with a naming format of `open-swe/<threadId>`. Every time a file is created, modified, or deleted, the changes will be committed to this branch. You can access the changes in the repository by checking out this branch.
