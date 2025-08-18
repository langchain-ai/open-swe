# Contributing to Open SWE

Thank you for your interest in contributing to Open SWE! This guide will help you get started.

## Contents

- [Code of conduct](#code-of-conduct)
- [Directory structure](#directory-structure)
- [Development setup](#development-setup)
- [Development cycle](#development-cycle)
- [Community PR Guidelines](#community-pr-guidelines)
- [Test suite](#test-suite)
- [Code quality tools](#code-quality-tools)
- [Security](#security)

## Code of conduct

By participating in this project, you agree to uphold our community standards. Please report unacceptable behavior to the maintainers.

## Directory structure

Open SWE is organized as a Turborepo monorepo:

- [`/apps/open-swe/`](/apps/open-swe/) - Core LangGraph agent application
- [`/apps/web/`](/apps/web/) - Next.js web interface  
- [`/apps/cli/`](/apps/cli/) - Terminal interface
- [`/apps/docs/`](/apps/docs/) - Documentation website
- [`/packages/shared/`](/packages/shared/) - Shared utilities and types

## Development setup

### Requirements

- **Node.js** 18.x or 20.x
- **Yarn** 3.5.1+ (managed via package.json)

### Setup steps

1. **Fork and clone** the repository:
   ```bash
   git clone https://github.com/<your_username>/open-swe.git
   cd open-swe
   git remote add upstream https://github.com/langchain-ai/open-swe.git
   ```

2. **Install dependencies**:
   ```bash
   yarn install --immutable
   ```

3. **Configure environment** (copy example files):
   ```bash
   cp apps/web/.env.example apps/web/.env
   cp apps/open-swe/.env.example apps/open-swe/.env
   ```

4. **Set up required API keys** in your `.env` files:
   - `ANTHROPIC_API_KEY` - Primary LLM provider
   - `LANGCHAIN_API_KEY` - For tracing (from LangSmith)
   - `GITHUB_APP_*` - For GitHub integration
   - `DAYTONA_API_KEY` - For cloud sandbox

   > See the [development setup docs](https://docs.langchain.com/labs/swe/setup/development) for detailed configuration.

5. **Build all packages**:
   ```bash
   yarn build
   ```

6. **Start development servers**:
   ```bash
   yarn dev
   ```

## Development cycle

### Basic workflow

1. **Start development**:
   ```bash
   yarn dev  # Starts all apps in development mode
   ```

2. **Make changes** following existing code patterns

3. **Check code quality**:
   ```bash
   yarn lint          # Check linting
   yarn format:check  # Check formatting
   yarn test          # Run tests
   ```

4. **Build and verify**:
   ```bash
   yarn build
   ```

5. **Commit and open PR**

## Community PR Guidelines

### General Requirements

- **Small, focused PRs** - One feature/fix per PR
- **Follow TypeScript standards** - No `@ts-ignore`, proper typing
- **Include tests** - Unit tests for core changes, integration tests for workflows
- **No console logging** - Use logger utilities (enforced by ESLint)
- **Follow existing patterns** - Study similar code before implementing

### PR Process

1. **Address feedback within 14 days** or PR will be auto-closed
2. **Pass all CI checks**:
   - Linting and formatting
   - TypeScript compilation  
   - Unit tests (Node 18.x/20.x)
   - Spell checking
3. **Clear PR description** with context and motivation

### Automatic rejection criteria

- Large, unfocused PRs
- Missing tests
- CI failures
- Typo-only changes

## Test suite

### Unit tests
```bash
yarn test              # All packages
cd apps/open-swe && yarn test  # Core agent only
```

### Integration tests
```bash
cd apps/open-swe && yarn test:int
```

### Evaluation tests
```bash
cd apps/open-swe && yarn eval:single
```

## Code quality tools

### Linting
```bash
yarn lint      # Check issues
yarn lint:fix  # Auto-fix issues
```

### Formatting
```bash
yarn format        # Format code
yarn format:check  # Check formatting
```

### Build validation
```bash
yarn build  # Ensure TypeScript compilation
```

## Security

**Report security issues** to security@langchain.dev - do not open public issues.

**Development security**:
- Never commit API keys or secrets
- Follow secure coding practices in tool implementations
- Review security implications of agent capabilities

---

For detailed setup instructions, see the [official development documentation](https://docs.langchain.com/labs/swe/setup/development).