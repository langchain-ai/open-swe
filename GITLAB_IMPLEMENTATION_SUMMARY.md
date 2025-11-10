# GitLab Integration - Implementation Summary

## Overview

This document summarizes the GitLab integration implementation for Open SWE, including what has been completed and what remains to be done.

## ✅ Completed Work

### 1. Git Provider Abstraction Layer

**Location**: `packages/shared/src/git-provider/`

Created a comprehensive abstraction layer that allows Open SWE to work with multiple git hosting platforms through a unified interface.

**Files Created**:
- `types.ts` - Common interfaces and types for all providers
- `factory.ts` - Provider factory for instantiating providers
- `index.ts` - Module exports
- `github/github-provider.ts` - GitHub implementation
- `gitlab/gitlab-provider.ts` - GitLab implementation

**Key Features**:
- Unified interface for issues, pull/merge requests, comments, labels
- Provider-agnostic types that work across platforms
- Factory pattern for easy provider instantiation
- Support for both GitHub and GitLab

### 2. GitLab Provider Implementation

**Location**: `packages/shared/src/git-provider/gitlab/gitlab-provider.ts`

Implemented the `GitProvider` interface for GitLab using the `@gitbeaker/rest` SDK.

**Capabilities**:
- Repository and branch operations
- Issue management (CRUD operations)
- Merge request management
- Comment operations
- Label management
- OAuth2 authentication
- Self-hosted GitLab support
- Webhook payload parsing

**Key Differences Handled**:
- GitLab uses `iid` (internal ID) instead of global `id`
- Merge Requests vs Pull Requests
- Different draft MR marking (`draft: true` or `work_in_progress`)
- Token-based webhook verification instead of HMAC
- OAuth2 flow instead of GitHub App installations

### 3. GitHub Provider Wrapper

**Location**: `packages/shared/src/git-provider/github/github-provider.ts`

Wrapped existing GitHub functionality into the provider interface to maintain backward compatibility while enabling the abstraction layer.

### 4. GitLab Webhook Handlers

**Location**: `apps/open-swe/src/routes/gitlab/`

Created webhook infrastructure for GitLab events:

**Files Created**:
- `unified-webhook.ts` - Main webhook handler with token verification
- `issue-labeled.ts` - Handles issue label events (stub)
- `merge-request-comment.ts` - Handles MR comment events (stub)
- `merge-request-review.ts` - Handles MR approval events (stub)

**Features**:
- Token-based webhook authentication
- Event routing based on `object_kind`
- GitLab-specific event mapping

### 5. GitLab Authentication

**Location**: `packages/shared/src/gitlab/auth.ts`

Implemented OAuth2 authentication flow for GitLab:

**Functions**:
- `getGitLabAccessToken()` - Exchange auth code for access token
- `refreshGitLabAccessToken()` - Refresh expired tokens
- `getGitLabUser()` - Fetch user information
- `verifyGitLabToken()` - Validate access tokens

### 6. Configuration Updates

**Updated Files**:
- `packages/shared/src/constants.ts` - Added GitLab and provider-agnostic constants
- `apps/open-swe/.env.example` - Added GitLab environment variables
- `apps/web/.env.example` - Added GitLab OAuth configuration
- `langgraph.json` - Added GitLab headers to configurable headers
- `apps/open-swe/src/routes/app.ts` - Added `/webhooks/gitlab` route
- `packages/shared/package.json` - Added `@gitbeaker/rest` dependency

**New Constants**:
```typescript
// GitLab-specific
GITLAB_TOKEN_COOKIE
GITLAB_BASE_URL
GITLAB_USER_ID_HEADER
GITLAB_USER_LOGIN_HEADER
GITLAB_WEBHOOK_TOKEN

// Provider-agnostic
GIT_PROVIDER_TYPE
GIT_PROVIDER_TOKEN
GIT_PROVIDER_USER_ID
GIT_PROVIDER_USER_LOGIN
GIT_PROVIDER_INSTALLATION_ID
GIT_PROVIDER_INSTALLATION_NAME
```

### 7. Documentation

**Files Created**:
- `GITLAB_SETUP.md` - Comprehensive setup and usage guide
- `GITLAB_IMPLEMENTATION_SUMMARY.md` - This file

**Documentation Includes**:
- Step-by-step setup instructions
- OAuth application creation
- Webhook configuration
- Environment variable reference
- Usage examples
- Troubleshooting guide
- API reference
- Self-hosted GitLab support

## 🚧 Remaining Work

### High Priority

#### 1. Complete Webhook Handler Implementations

**Location**: `apps/open-swe/src/routes/gitlab/`

**What's Needed**:
- Implement full logic in `issue-labeled.ts`:
  - Check for open-swe labels
  - Create new agent runs
  - Fetch issue details
  - Initialize workspace

- Implement full logic in `merge-request-comment.ts`:
  - Parse @mentions
  - Handle user requests
  - Create responses
  - Trigger agent actions

- Implement full logic in `merge-request-review.ts`:
  - Handle approvals/rejections
  - Process reviewer feedback
  - Update MR status

**Approach**: Reference the existing GitHub handlers (`apps/open-swe/src/routes/github/`) and adapt the logic for GitLab using the provider abstraction.

#### 2. Web App OAuth Flow

**Location**: `apps/web/src/app/api/auth/gitlab/`

**What's Needed**:
- Create OAuth routes:
  - `login/route.ts` - Initiate GitLab OAuth flow
  - `callback/route.ts` - Handle OAuth callback

- Session management for GitLab tokens

**Reference**: Copy and adapt from `apps/web/src/app/api/auth/github/`

#### 3. Web UI Provider Selection

**Location**: `apps/web/src/components/`

**What's Needed**:
- Provider selection dropdown/toggle
- GitLab-specific components:
  - Project selector
  - Branch selector
  - Authentication status
  - Installation/project connection UI

**Approach**:
- Create `components/gitlab/` directory
- Mirror structure from `components/github/`
- Create provider-agnostic components where possible

#### 4. Refactor Agent Code

**Location**: `apps/open-swe/src/`

**What's Needed**:
- Update graph nodes to use provider abstraction
- Replace direct GitHub API calls with provider methods
- Make agent logic provider-agnostic
- Update context passing to include provider type

**Files to Update**:
- Graph implementations in `apps/open-swe/src/graphs/`
- Tool implementations that interact with git hosting
- State management for provider information

### Medium Priority

#### 5. Testing

**What's Needed**:
- Unit tests for GitLab provider
- Integration tests for webhook handlers
- E2E tests for GitLab workflow
- Provider factory tests
- Authentication tests

**Location**: Create test files alongside implementation

#### 6. Error Handling & Logging

**What's Needed**:
- Comprehensive error handling in GitLab provider
- Provider-specific error messages
- Logging for debugging GitLab-specific issues
- Rate limit handling for GitLab API

#### 7. Advanced GitLab Features

**What's Needed**:
- Discussion threads (GitLab's comment threading)
- Approval rules support
- Merge train handling
- Protected branch logic
- CI/CD integration (GitLab CI)
- Labels with scopes (e.g., `workflow::in-progress`)

### Low Priority

#### 8. Additional Providers

**What's Needed**:
- Bitbucket provider implementation
- Azure DevOps provider
- Gitea/Forgejo support

#### 9. Migration Tools

**What's Needed**:
- Tool to migrate from GitHub to GitLab
- Configuration migration scripts
- Label migration utilities

## Implementation Guide

### To Complete Webhook Handlers

1. **Read** the existing GitHub handlers:
   ```
   apps/open-swe/src/routes/github/issue-labeled.ts
   apps/open-swe/src/routes/github/pull-request-comment.ts
   apps/open-swe/src/routes/github/pull-request-review.ts
   ```

2. **Identify** GitHub-specific code:
   - Direct Octokit calls
   - GitHub data structures
   - GitHub-specific logic

3. **Refactor** using provider abstraction:
   ```typescript
   // Before (GitHub-specific)
   const issue = await octokit.issues.get({ owner, repo, issue_number });

   // After (provider-agnostic)
   const provider = createGitProvider(config);
   const issue = await provider.getIssue(owner, repo, issueNumber);
   ```

4. **Add** provider type detection:
   ```typescript
   const providerType = config.type; // 'github' or 'gitlab'
   const provider = createGitProvider(config);
   ```

5. **Handle** provider-specific edge cases where necessary

### To Add Web App OAuth

1. **Create** GitLab OAuth routes:
   ```typescript
   // apps/web/src/app/api/auth/gitlab/login/route.ts
   export async function GET(request: Request) {
     // Generate OAuth URL
     // Redirect to GitLab
   }

   // apps/web/src/app/api/auth/gitlab/callback/route.ts
   export async function GET(request: Request) {
     // Exchange code for token
     // Store in session
     // Redirect to app
   }
   ```

2. **Add** provider selection in UI:
   ```typescript
   // Provider toggle component
   <ProviderSelector
     value={provider}
     onChange={setProvider}
     options={['github', 'gitlab']}
   />
   ```

3. **Update** authentication hooks:
   ```typescript
   // Support both providers
   const { token, provider } = useAuthToken();
   ```

### To Refactor Agent Code

1. **Identify** all GitHub API calls in graphs:
   ```bash
   grep -r "octokit\." apps/open-swe/src/graphs/
   grep -r "github/api" apps/open-swe/src/graphs/
   ```

2. **Replace** with provider calls:
   ```typescript
   // Before
   import { createIssueComment } from '../../utils/github/api';

   // After
   import { createGitProvider } from '@openswe/shared/git-provider';
   const provider = createGitProvider(config);
   await provider.createIssueComment(params);
   ```

3. **Update** state to include provider type:
   ```typescript
   interface AgentState {
     providerType: 'github' | 'gitlab';
     providerConfig: ProviderConfig;
     // ... other fields
   }
   ```

## Testing Strategy

### Unit Tests

```typescript
// Test GitLab provider
describe('GitLabProvider', () => {
  it('should map GitLab issues to common format', () => {
    // Test iid to number mapping
    // Test state mapping
    // Test label handling
  });

  it('should handle self-hosted instances', () => {
    // Test custom base URL
  });
});
```

### Integration Tests

```typescript
// Test webhook handling
describe('GitLab Webhooks', () => {
  it('should handle issue label events', async () => {
    // Send mock webhook payload
    // Verify handler response
    // Check agent run created
  });
});
```

### E2E Tests

```typescript
// Test full workflow
describe('GitLab E2E', () => {
  it('should complete issue to MR workflow', async () => {
    // Create issue with label
    // Wait for agent run
    // Verify MR created
    // Check changes
  });
});
```

## Migration Path for Existing Users

### From GitHub-only to Multi-provider

1. **No breaking changes** - GitHub continues to work as before
2. **Gradual adoption** - Users can add GitLab alongside GitHub
3. **Configuration-based** - Provider type determined by config
4. **Backward compatible** - Existing GitHub code wrapped in provider interface

### For New Users

1. Choose provider during setup (GitHub or GitLab)
2. Follow provider-specific setup guide
3. Configure webhooks and OAuth
4. Start using Open SWE

## Performance Considerations

### Caching

- Cache provider instances per request
- Cache user info to reduce API calls
- Implement token refresh logic

### Rate Limiting

- GitLab has different rate limits than GitHub
- Implement exponential backoff
- Queue requests when near limits

### Webhooks

- Async processing for webhook events
- Queue system for high-volume repositories
- Deduplication of webhook deliveries

## Security Considerations

### Token Storage

- Encrypt tokens at rest
- Use secure session management
- Implement token rotation

### Webhook Security

- Verify all webhook signatures/tokens
- Validate payload structure
- Implement replay attack prevention

### OAuth

- Use PKCE for OAuth flow
- Validate redirect URIs
- Secure state parameter

## Resources

### Internal References

- Provider abstraction: `packages/shared/src/git-provider/`
- GitHub implementation: `apps/open-swe/src/routes/github/`
- Agent graphs: `apps/open-swe/src/graphs/`

### External Resources

- [GitLab API Docs](https://docs.gitlab.com/ee/api/)
- [@gitbeaker Documentation](https://github.com/jdalrymple/gitbeaker)
- [Open SWE Documentation](https://docs.langchain.com/labs/swe)

## Questions & Support

For questions about this implementation:
1. Review this document and `GITLAB_SETUP.md`
2. Check the provider abstraction code
3. Reference GitHub implementation as example
4. Create an issue on GitHub with questions

## Next Steps

**Immediate**:
1. Complete webhook handler implementations
2. Add web app OAuth flow
3. Create basic UI for provider selection

**Short-term**:
4. Refactor agent code to use providers
5. Add comprehensive tests
6. Improve error handling

**Long-term**:
7. Add advanced GitLab features
8. Support additional providers
9. Performance optimizations
