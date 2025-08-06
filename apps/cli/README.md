# Open SWE CLI

> **⚠️ Under Development**  
> This CLI is currently under active development and may contain bugs or incomplete features.

A command-line interface for Open SWE that provides a terminal-based chat experience to interact with the autonomous coding agent. Built with React and Ink, it offers real-time streaming of agent logs and works directly on your local codebase without requiring GitHub authentication.

## Documentation

## Development

1. Install dependencies: `yarn install`
2. Create a `.env` file and set `OPEN_SWE_LOCAL_PROJECT_PATH` to point to an existing git repository:
   ```bash
   echo "OPEN_SWE_LOCAL_PROJECT_PATH=/path/to/your/git/repository" > .env
   ```
3. Build the CLI: `yarn build`
4. Run the CLI: `yarn cli`

## Usage

Run the CLI and start chatting with the agent about your local codebase:

```bash
yarn cli
```

The CLI will:

1. Start in local mode (no authentication required)
2. Work directly on files in your current directory
3. Provide interactive chat with the Open SWE agent
4. Stream real-time logs and responses

## Prerequisites

- An existing git repository that you want to work on
- The repository must be initialized with git and have at least one commit

## Features

- **Local Mode Only**: Works directly on your local codebase without GitHub integration
- **Real-time Streaming**: See agent logs and responses as they happen
- **Interactive Chat**: Type your requests and get immediate feedback
- **Plan Approval**: Review and approve/deny proposed plans before execution
