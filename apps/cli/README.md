# Open SWE CLI

> **⚠️ Under Development**  
> This CLI is currently under active development and may contain bugs or incomplete features.

A command-line interface for Open SWE that provides a terminal-based chat experience to interact with the autonomous coding agent. Built with React and Ink, it offers real-time streaming of agent logs, repository selection, and GitHub integration directly from your terminal.

## Documentation

## Development

1. Copy the environment file: `cp .env.example .env` and fill in the required values
2. Install dependencies: `yarn install`
3. Build the CLI: `yarn build`
4. Run the CLI: `yarn cli`

## Usage

Run the CLI and follow the interactive prompts:

```bash
yarn cli
```

The CLI will guide you through:

1. GitHub authentication (if not already logged in)
2. Repository selection
3. GitHub App installation
4. Interactive chat with the Open SWE agent
