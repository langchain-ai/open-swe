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

Copy the `.env.example` file to `.env` and fill in the values:

```bash
cp ./apps/open-swe/.env.example ./apps/open-swe/.env
```

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

# E2B API key for accessing and modifying the code in the cloud sandbox.
E2B_API_KEY=""

# Your GitHub PAT with access to the repositories you want to modify.
GITHUB_PAT=""

```

Your GitHub personal access token should have access to only the repositories you want to modify. It must have the following `Repository` permissions:

- `Read access to metadata`
- `Read and Write access to code and pull requests`

You can generate a personal access token in [GitHub settings](https://github.com/settings/personal-access-tokens).

## Running the graph

To run the graph, first modify the `e2e.ts` file to set the `userRequest` along with the target repository information.

The `userRequest` should contain the task description for Open SWE to execute.

The `target_repository` object should contain the information for the repository Open SWE should clone and make changes to. You're required to set the `owner` and `repo` properties. The `branch` property is optional, and defaults to whatever the base branch of the repository is.

After setting the `userRequest` and `target_repository`, run the following command on a terminal session:

```bash
yarn dev
```

This will start the LangGraph server running locally. The server will be available at `http://localhost:2024` by default.

Then, run the following command on a second terminal session:

```bash
yarn run:e2e
```

This will start the graph with the inputs you specified in the file. The graph always runs a planning sub-graph first, and once it's finished planning it will interrupt with the plan. To interact with the interrupt, you should add Open SWE to [Agent Inbox](https://dev.agentinbox.ai).

Once you've added the graph to the inbox, you can inspect the interrupt with the generated plan. It will allow you to:

1. Accept the plan as is.
2. Edit, and submit the plan.
3. Respond with natural language to have the plan modified. This step will _not_ rerun the planning subgraph, it will only modify the plan.
4. Reject the plan by clicking the `Ignore` button.

If you want to accept the plan as is, you should re-invoke the graph from the terminal with the `yarn run:e2e --threadId <threadId>` command. Replace `<threadId>` with the thread ID of the interrupt you want to accept. This will resume the graph, and log the updates to the terminal. You may also resume from Agent Inbox, however this will not show you the outputs of the graph.

## Accessing Changes

Open SWE will automatically create a branch whenever you create a new thread with a naming format of `open-swe/<threadId>`. Every time a file is created, modified, or deleted, the changes will be committed to this branch. You can access the changes in the repository by checking out this branch.
