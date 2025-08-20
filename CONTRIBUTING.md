# Contributing to Open SWE

Thank you for your interest in contributing to Open SWE! This guide will help you get started.

## Development Setup

### Requirements

- **Node.js** (version 18 or higher)
- **Yarn** (version 3.5.1 or higher)
- **Git**

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

4. **Create GitHub App** (required):
   
   > **Note:** You need a **GitHub App** (not OAuth App). Consider separate apps for development and production.
   
   **Create the App:**
   1. Go to [GitHub App creation page](https://github.com/settings/apps/new)
   2. Fill in basic information:
      - **GitHub App name:** Your preferred name (e.g., "open-swe-dev")
      - **Description:** Development instance of Open SWE coding agent
      - **Homepage URL:** Your repository URL
      - **Callback URL:** `http://localhost:3000/api/auth/github/callback`
   
   **Configure OAuth Settings:**
   - ✅ Request user authorization (OAuth) during installation
   - ✅ Redirect on update
   - ❌ Expire user authorization tokens
   
   **Set Up Webhook:**
   - ✅ Enable webhook
   - **Webhook URL:** Use ngrok to expose your local server:
     ```bash
     ngrok http 2024
     ```
     Use the ngrok URL + `/webhook/github` (e.g., `https://abc123.ngrok.io/webhook/github`)
   - **Webhook secret:** Generate and save:
     ```bash
     openssl rand -hex 32
     ```
     Add this value to GITHUB_WEBHOOK_SECRET in apps/open-swe/.env 

   **Configure Permissions (Repository):**
   - **Contents:** Read & Write
   - **Issues:** Read & Write  
   - **Pull requests:** Read & Write
   - **Metadata:** Read only (auto-enabled)
   
   **Organization permissions**: None
   **Account permissions**: None
   
   **Subscribe to Events:**
   - ✅ Issues
   - ✅ Pull request review
   - ✅ Pull request review comment
   - ✅ Issue comment
   
   **Installation Settings:**
   - Choose "Any account" for broader testing or "Only on this account" to limit scope

   **Complete App Creation**
   - Click Create GitHub App to finish the setup
   
   **Collect Credentials:**
   After creating the app, collect these values and add them to both environment files:
   
   - **`GITHUB_APP_NAME`** - The name you chose
   - **`GITHUB_APP_ID`** - Found in the "About" section (e.g., `12345678`)
   - **`NEXT_PUBLIC_GITHUB_APP_CLIENT_ID`** - Found in the "About" section
   - **`GITHUB_APP_CLIENT_SECRET`**:
     1. Scroll to "Client secrets" section
     2. Click "Generate new client secret"
     3. Copy the generated value
   - **`GITHUB_APP_PRIVATE_KEY`**:
     1. Scroll to "Private keys" section
     2. Click "Generate a private key"
     3. Download the `.pem` file and copy its contents
     4. Format as a single line with `\\n` for line breaks, or use the multiline format shown in the example
   - **`GITHUB_APP_REDIRECT_URI`** - Should be `http://localhost:3000/api/auth/github/callback` for local development, or `https://your-production-url.com/api/auth/github/callback` for production
   - **`GITHUB_WEBHOOK_SECRET`** - Generate and save this value:
     ```bash
     openssl rand -hex 32
     ```
     Add this value to `GITHUB_WEBHOOK_SECRET` in `apps/open-swe/.env` and `apps/web/.env`   

5. **Start development servers**:
   ```bash
   yarn dev  # Starts both web app (port 3000) and agent (port 2024)
   ```

## Development Cycle

While working on Open SWE code, you can run `yarn dev`. It will automatically build your code, restart the backend services, and refresh the frontend (web UI) on every change you make.

### Basic Development Workflow

1. **Start development mode**:
   ```bash
   yarn dev
   ```
   This starts all services with hot reload enabled.

2. **Make your changes** - Code gets automatically rebuilt and reloaded

3. **Check if everything still works in production mode**:
   ```bash
   yarn build
   yarn start  # Test production builds (individual apps only)
   ```

4. **Create tests** for new functionality

5. **Run all tests**:
   ```bash
   yarn test
   ```

6. **Run quality checks** and **commit your changes**

## Before Submitting PRs

Run these commands to ensure your changes are ready:

```bash
yarn lint          # Check linting
yarn build         # Ensure TypeScript compilation
yarn test          # Run tests
```

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