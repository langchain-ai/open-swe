# Contributing to Open SWE

Thank you for your interest in contributing to Open SWE! This guide will help you get started.

## Development Setup

### Requirements

- **Node.js** 18.x or 20.x  
- **Yarn** (managed via package.json)

### Setup Steps

1. **Fork and clone** the repository:
   ```bash
   git clone https://github.com/<your_username>/open-swe.git
   cd open-swe
   git remote add upstream https://github.com/langchain-ai/open-swe.git
   ```

2. **Install dependencies**:
   ```bash
   yarn install
   ```

3. **Copy environment files**:
   ```bash
   cp apps/web/.env.example apps/web/.env
   cp apps/open-swe/.env.example apps/open-swe/.env
   ```

4. **Configure required API keys** in your `.env` files:
   - `ANTHROPIC_API_KEY` - LLM provider (required)
   - `DAYTONA_API_KEY` - Cloud sandboxes (required)
   - `SECRETS_ENCRYPTION_KEY` - Generate with `openssl rand -hex 32` (required)
   - GitHub App credentials (required for GitHub integration)

   > See the [development setup docs](https://docs.langchain.com/labs/swe/setup/development) for complete configuration details.

5. **Start development servers**:
   ```bash
   yarn dev  # Starts both web app (port 3000) and agent (port 2024)
   ```

## Before Submitting PRs

Run these commands to ensure your changes are ready:

```bash
yarn lint          # Check linting (may show warnings)
yarn build         # Ensure TypeScript compilation
yarn test          # Run tests
```

> **Note:** `yarn format:check` may fail on generated files in `/dist` directories. This is expected and won't block PRs.

All CI checks must pass before your PR can be merged.

## Project Structure

- `apps/open-swe/` - Core LangGraph agent
- `apps/web/` - Next.js web interface
- `apps/cli/` - Terminal interface  
- `apps/docs/` - Documentation

## Guidelines

- **Small, focused PRs** - One feature/fix per PR
- **Follow existing patterns** - Study similar code before implementing
- **Include tests** for new functionality
- **No `@ts-ignore`** - Use proper TypeScript types
- **Clear PR descriptions** with context and motivation

## Security

Report security issues to security@langchain.dev - do not open public issues.

Never commit API keys or secrets to the repository.

---

For detailed setup instructions, see the [development documentation](https://docs.langchain.com/labs/swe/setup/development).