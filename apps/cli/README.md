# Open SWE CLI

A command-line interface for Open SWE that provides a terminal-based chat experience to interact with the autonomous coding agent. Built with React and Ink, it offers real-time streaming of agent logs, repository selection, and GitHub integration directly from your terminal.

## Documentation

## Development

1. Copy the environment file: `cp .env.example .env` and fill in the required values
2. Install dependencies: `yarn install`
3. Build the CLI: `yarn build`
4. Run the CLI: `yarn start` or `node dist/index.js`

## Usage

Run the CLI and follow the interactive prompts:

```bash
npx tsc && node dist/index.js
```

The CLI will guide you through:
1. GitHub authentication (if not already logged in)
2. Repository selection with fuzzy search
3. GitHub App installation (if required)
4. Interactive chat with the Open SWE agent

Use `Ctrl+C` during chat to switch repositories, and simply type messages to interact with or interrupt the running agent. 