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

The agent `.env` file should contain the following variables:

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

# Your GitHub PAT with access to the repositories you want to modify.
GITHUB_PAT=""
```

And the web `.env` file should contain the following variables:

```bash
NEXT_PUBLIC_API_URL=http://localhost:2024 # Change to production URL when deployed
NEXT_PUBLIC_ASSISTANT_ID=open-swe
```

To generate the GitHub personal access token, you should:

1. Go to [GitHub settings](https://github.com/settings/personal-access-tokens)
2. Click on `Generate new token` to generate a new fine grained token.
3. Give the token a name & description.
4. Choose `Only select repositories`, and select the repositories you want to give Open SWE access to.
5. Under `Permissions`, give it `Repository permission`:
  - `Contents` - `Read and write`
  - `Metadata` - `Read-only` (should be auto enabled after selecting `Contents`)
  - `Pull requests` - `Read and write`
6. Click `Generate token` & copy the token.
7. Paste the token into the `GITHUB_PAT` variable in the agent `.env` file.

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
