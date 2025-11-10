# GitLab Integration Setup Guide

This guide explains how to set up and use Open SWE with GitLab as an alternative to GitHub.

## Overview

Open SWE now supports both GitHub and GitLab as git hosting providers. The GitLab integration includes:

- ✅ OAuth2 authentication
- ✅ Webhook support for issues and merge requests
- ✅ Automated merge request creation
- ✅ Issue and MR comment handling
- ✅ Self-hosted GitLab instance support

## Architecture

The GitLab integration uses a provider abstraction layer that allows Open SWE to work with multiple git hosting services through a unified interface.

### Key Components

1. **Git Provider Abstraction** (`packages/shared/src/git-provider/`)
   - Common interfaces for issues, merge requests, comments, etc.
   - Provider factory for instantiating GitHub or GitLab providers
   - Unified types across providers

2. **GitLab Provider** (`packages/shared/src/git-provider/gitlab/`)
   - Implementation using `@gitbeaker/rest` SDK
   - Maps GitLab concepts (Merge Requests, iid) to common interface
   - Supports self-hosted instances

3. **Webhook Handlers** (`apps/open-swe/src/routes/gitlab/`)
   - Issue label events
   - Merge request comments
   - Merge request reviews/approvals

4. **Authentication** (`packages/shared/src/gitlab/auth.ts`)
   - OAuth2 flow
   - Token management
   - User verification

## Setup Instructions

### Step 1: Create a GitLab OAuth Application

1. Navigate to your GitLab instance (e.g., https://gitlab.com)
2. Go to **User Settings** → **Applications**
3. Create a new application with:
   - **Name**: `open-swe` (or your preferred name)
   - **Redirect URI**: `http://localhost:3000/api/auth/gitlab/callback` (for development)
     - For production: `https://your-domain.com/api/auth/gitlab/callback`
   - **Scopes**: Select the following:
     - ✅ `api` - Full API access
     - ✅ `read_user` - Read user information
     - ✅ `read_repository` - Read repository content
     - ✅ `write_repository` - Write to repository
4. Save and note down:
   - **Application ID**
   - **Secret**

### Step 2: Configure Environment Variables

#### Agent Configuration (`apps/open-swe/.env`)

Add the following variables to your agent's `.env` file:

```bash
# GitLab OAuth Secrets
GITLAB_APPLICATION_ID="your-application-id"
GITLAB_APPLICATION_SECRET="your-application-secret"
GITLAB_BASE_URL="https://gitlab.com"  # or your self-hosted URL
GITLAB_WEBHOOK_TOKEN="generate-a-random-token"
GITLAB_TRIGGER_USERNAME="open-swe"  # username to tag for triggering
```

#### Web App Configuration (`apps/web/.env`)

Add the following variables to your web app's `.env` file:

```bash
# GitLab OAuth Secrets
NEXT_PUBLIC_GITLAB_APPLICATION_ID="your-application-id"
GITLAB_APPLICATION_SECRET="your-application-secret"
GITLAB_REDIRECT_URI="http://localhost:3000/api/auth/gitlab/callback"
NEXT_PUBLIC_GITLAB_BASE_URL="https://gitlab.com"  # or your self-hosted URL
```

### Step 3: Configure Webhooks

1. Go to your GitLab project
2. Navigate to **Settings** → **Webhooks**
3. Create a new webhook:
   - **URL**: `https://your-domain.com/webhooks/gitlab`
     - For local development with ngrok: `https://your-ngrok-url.ngrok.io/webhooks/gitlab`
   - **Secret token**: Use the same token from `GITLAB_WEBHOOK_TOKEN`
   - **Trigger events**: Select:
     - ✅ Issues events
     - ✅ Merge request events
     - ✅ Comments
4. Save the webhook

### Step 4: Configure Labels

Create the following labels in your GitLab project:

1. **open-swe** - Triggers Open SWE to start working on an issue
2. **open-swe-auto** - Triggers Open SWE with auto-approval
3. **open-swe-max** - Triggers Open SWE with extended context
4. **open-swe-max-auto** - Combines max and auto modes

To create labels:
1. Go to **Project** → **Labels**
2. Create new labels with the names above
3. Choose any color you prefer

## Usage

### Triggering Open SWE on Issues

1. Create an issue in your GitLab project
2. Add the `open-swe` label to the issue
3. Open SWE will automatically:
   - Clone the repository
   - Analyze the issue
   - Create a plan
   - Implement changes
   - Open a merge request

### Interacting via Comments

You can interact with Open SWE by mentioning it in comments:

```markdown
@open-swe please add unit tests for this function
```

### Merge Request Workflow

1. Open SWE creates a draft merge request
2. Review the changes in the MR
3. Provide feedback via:
   - Merge request comments
   - Inline code review comments
   - Approval/changes requested
4. Open SWE will iterate based on feedback
5. When ready, Open SWE marks the MR as ready for review

## Key Differences: GitLab vs GitHub

| Feature | GitHub | GitLab |
|---------|--------|--------|
| **PRs/MRs** | Pull Requests | Merge Requests |
| **Number** | `number` | `iid` (internal ID) |
| **Draft** | `draft: true` | `draft: true` or `WIP:` prefix |
| **Auth** | GitHub App + Installations | OAuth2 |
| **Webhook Verification** | HMAC signature | Token-based |
| **Self-hosted** | GitHub Enterprise | GitLab CE/EE |

## Provider Abstraction API

### Using the Git Provider in Your Code

```typescript
import { createGitProvider, ProviderType } from '@openswe/shared/git-provider';

// Create a provider instance
const provider = createGitProvider({
  type: 'gitlab', // or 'github'
  token: 'your-access-token',
  baseUrl: 'https://gitlab.com', // optional for GitLab
});

// Use unified interface
const issue = await provider.getIssue('owner', 'repo', 123);
const pr = await provider.createPullRequest({
  owner: 'owner',
  repo: 'repo',
  title: 'Add new feature',
  body: 'Description of changes',
  head: 'feature-branch',
  base: 'main',
  draft: true,
});
```

### Available Provider Methods

- **Repository**: `getRepository()`, `getBranch()`
- **Issues**: `getIssue()`, `createIssue()`, `updateIssue()`, `listIssueComments()`
- **Comments**: `createIssueComment()`, `updateIssueComment()`
- **Pull/Merge Requests**: `getPullRequest()`, `createPullRequest()`, `updatePullRequest()`, `markPullRequestReady()`
- **Labels**: `addLabels()`, `removeLabel()`, `createLabel()`
- **Auth**: `verifyToken()`
- **Webhooks**: `verifyWebhookSignature()`, `parseWebhookPayload()`

## Self-Hosted GitLab

To use Open SWE with a self-hosted GitLab instance:

1. Set `GITLAB_BASE_URL` to your instance URL:
   ```bash
   GITLAB_BASE_URL="https://gitlab.your-company.com"
   NEXT_PUBLIC_GITLAB_BASE_URL="https://gitlab.your-company.com"
   ```

2. Ensure your GitLab instance is accessible from your Open SWE deployment

3. Configure the OAuth application on your self-hosted instance

4. All other steps remain the same

## Troubleshooting

### Webhooks Not Working

1. Check webhook secret matches `GITLAB_WEBHOOK_TOKEN`
2. Verify webhook URL is correct and accessible
3. Check webhook delivery logs in GitLab:
   - Go to **Settings** → **Webhooks**
   - Click **Edit** on your webhook
   - Scroll to **Recent Deliveries**

### OAuth Authentication Fails

1. Verify `GITLAB_APPLICATION_ID` and `GITLAB_APPLICATION_SECRET` are correct
2. Check redirect URI matches exactly (including protocol and port)
3. Ensure required scopes are enabled in your OAuth application

### Cannot Access Self-Hosted GitLab

1. Verify `GITLAB_BASE_URL` is correct
2. Check network connectivity from your Open SWE deployment
3. Ensure SSL certificates are valid (if using HTTPS)

## Implementation Status

### ✅ Completed

- Git provider abstraction layer
- GitHub provider implementation
- GitLab provider implementation
- Provider factory
- Webhook routes for GitLab
- Authentication utilities
- Environment configuration
- LangGraph header configuration

### 🚧 In Progress / To Do

- Web app OAuth flow for GitLab
- UI components for provider selection
- Complete webhook handler implementations
- Refactor agent code to use provider abstraction
- E2E tests for GitLab integration
- Advanced GitLab features:
  - Discussions/threads
  - Approval rules
  - Merge trains
  - Protected branches handling

## Contributing

To add support for additional git providers (e.g., Bitbucket, Azure DevOps):

1. Create a new provider class in `packages/shared/src/git-provider/<provider>/`
2. Implement the `GitProvider` interface
3. Add provider type to `ProviderType` union
4. Update factory to handle new provider
5. Add authentication utilities
6. Create webhook handlers
7. Update documentation

## Resources

- [GitLab API Documentation](https://docs.gitlab.com/ee/api/)
- [GitLab Webhooks](https://docs.gitlab.com/ee/user/project/integrations/webhooks.html)
- [GitLab OAuth2](https://docs.gitlab.com/ee/api/oauth2.html)
- [@gitbeaker/rest Documentation](https://github.com/jdalrymple/gitbeaker)

## Support

For issues or questions:
- Create an issue on GitHub: https://github.com/langchain-ai/open-swe/issues
- Check existing documentation: https://docs.langchain.com/labs/swe
