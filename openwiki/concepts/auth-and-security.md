---
type: security architecture concept
title: Authentication, Authorization, and Secret Boundaries
description: How Open SWE authenticates dashboard and automation users, resolves GitHub authority, verifies inbound requests, encrypts stored credentials, and keeps secrets out of sandboxes.
tags: [authentication, authorization, github-oauth, github-app, webhooks, encryption, csrf, sandbox-security]
sources:
  - id: openwiki-source-4b1279a0a1e5ec2d55a4558a
    resource: repo://openswe/api/app.py
  - id: openwiki-source-035276d8c595782faca6e595
    resource: repo://openswe/api/health.py
  - id: openwiki-source-913527bc7b548b4bf81f6a35
    resource: repo://openswe/completion.py
  - id: openwiki-source-fe0fc757d24cd7cfa5264c72
    resource: repo://openswe/credential_scope.py
  - id: openwiki-source-31bc4bba3dc364743082e410
    resource: repo://openswe/dashboard/admin.py
  - id: openwiki-source-50d64b46ab06b6436266b4d0
    resource: repo://openswe/dashboard/oauth.py
  - id: openwiki-source-4cb48d234248941982c6537f
    resource: repo://openswe/dashboard/profiles.py
  - id: openwiki-source-b11ec0af4e40439361058935
    resource: repo://openswe/encryption.py
  - id: openwiki-source-660db75c29aed6870aab6c3d
    resource: repo://openswe/github/app.py
  - id: openwiki-source-783616155a6663ff5d3b0dfa
    resource: repo://openswe/github/comments.py
  - id: openwiki-source-3a7e1a8d071789849d64c6c9
    resource: repo://openswe/github/org_membership.py
  - id: openwiki-source-d0edf7555209b3e6418b5c5f
    resource: repo://openswe/github/routes.py
  - id: openwiki-source-0c4b1aac46b8420871177918
    resource: repo://openswe/github/thread_token.py
  - id: openwiki-source-4194ce777e6975dfc2a2c3d1
    resource: repo://openswe/github/token_auth.py
  - id: openwiki-source-8d544a43b3113d48789eff4f
    resource: repo://openswe/github/token.py
  - id: openwiki-source-1087d65aaa83434d4f7c209b
    resource: repo://openswe/middleware/refresh_github_proxy.py
  - id: openwiki-source-d16a45e9fc6aa80a3708c88c
    resource: repo://openswe/sandboxes/providers/langsmith.py
  - id: openwiki-source-49cd80b1b712410f02d313d6
    resource: repo://openswe/slack/client.py
  - id: openwiki-source-d683445251a7ec19a5def965
    resource: repo://openswe/slack/oauth.py
  - id: openwiki-source-b5fe0e0520028e80429116d3
    resource: repo://openswe/tools/admin_gate.py
  - id: openwiki-source-3087256f0cd599176fba3c38
    resource: repo://openswe/webhooks/common.py
  - id: openwiki-source-3a1539e01daa921ba15e9617
    resource: repo://tests/dashboard/test_dashboard_oauth_redirect.py
  - id: openwiki-source-d8c75a797d0ce06ee3b8d9fb
    resource: repo://tests/dashboard/test_github_token_auth.py
generated: { by: "openwiki/0.4.2", at: "2026-10-08T15:19:10.971Z" }
verified:
  - by: openwiki/0.4.2
    at: 2026-10-08T15:19:10.971Z
---

# Authentication, Authorization, and Secret Boundaries

Open SWE crosses distinct trust boundaries: dashboard users authenticate with GitHub, external systems deliver webhooks, agent runs need GitHub authority, and sandboxed code must not receive long-lived secrets. This page describes the enforcement points and their failure modes. See also [sandbox lifecycle](../architecture/sandbox-lifecycle.md), [tools](./tools.md), [dashboard UI](../integrations/dashboard-ui.md), [configuration](../operations/configuration.md), and [invocation](../workflows/invocation.md).

## GitHub authority for runs

`agent.github.token.resolve_github_token` routes GitHub token resolution by run source. For sources carrying a mapped GitHub login (Slack, Linear, dashboard, schedule), it prefers the triggering user's per-user OAuth token from the dashboard store — even in bot-token-only mode — so PRs and comments are attributed to that user. When no valid per-user token is available, resolution falls back to a GitHub App installation token only in bot-token-only mode (LANGSMITH_API_KEY set but neither X_SERVICE_AUTH_JWT_SECRET nor USER_ID_API_KEY_MAP configured); in interactive mode it instead raises `GitHubUserAuthRequired` to force (re-)authentication rather than silently acting as the bot. For public threads it requests a GitHub App installation token; for private threads it reads the owner's valid dashboard OAuth credential if available. This design preserves user attribution even when running unattended.

For LangSmith per-user-auth flows on GitHub-originated runs, the system maps login to email and uses the standard GitHub OAuth resolution. Other sources use a configured user email. A missing source is an error because the system cannot route an auth failure response.

```mermaid
sequenceDiagram
    participant Run as Agent run
    participant Scope as Credential scope
    participant Resolver as Token resolver
    participant Store as Dashboard OAuth store
    participant App as GitHub App
    participant Cache as Process cache

    Run->>Scope: Check thread visibility
    Scope-->>Run: visibility, owner
    alt private thread with owner login
        Run->>Resolver: Get owner's token
        Resolver->>Store: Retrieve valid OAuth
        Store-->>Resolver: User token (or None)
        alt Token found
            Resolver->>Cache: Cache by thread + principal
        else No token (interactive)
            Resolver-->>Run: GitHubUserAuthRequired
        end
    else public thread
        Run->>Resolver: Request app token
        Resolver->>App: Mint installation token
        App-->>Resolver: Bot token
        Resolver->>Cache: Cache as bot principal
    end
```

Token resolution for public and private thread runs.

### Cache and GitHub App lifetimes

Resolved GitHub tokens are cached only in process memory and are keyed by `(thread_id, principal)`. Normalized `login:` or `email:` principals isolate users; `bot` is a separate principal. The cache refuses an unbound user token, and caches an entry at token expiry with a 60-second skew or after 24 hours, whichever comes first. `invalidate_cached_github_token` clears a thread's entries when stale or revoked credentials are detected.

GitHub App installation tokens are minted by signing a short-lived RS256 JWT with the App private key and exchanging it for an installation access token. The JWT is issued 60 seconds in the past (for clock skew) and valid for nine minutes. Its in-process cache is segregated by installation ID, repository IDs/names, and requested permissions. Cached installation tokens are no longer reused within ten minutes of expiry, a margin that exceeds the proxy refresh window so a near-expiry proxy refresh still mints a genuinely fresh token. Missing App configuration or an invalid installation yields no token rather than an unauthenticated request.

### Sandbox proxy boundary

A LangSmith sandbox is configured with a GitHub **App installation** token through opaque proxy headers. The sandbox environment receives `GH_TOKEN=proxy-injected`, not the real token; API traffic to `api.github.com` receives Bearer auth and traffic to `github.com` receives Basic `x-access-token` auth. The proxy token expiry record retains repository and permission scope so a refresh cannot broaden authority. Before each model call, middleware refreshes a near-expiry proxy token; a reused sandbox that cannot be reconfigured is treated as unreachable rather than silently continuing with stale access.

## Token types and scope narrowing

```mermaid
flowchart TD
    A["GitHub Token Types"] --> B["Installation Token"]
    A --> C["User OAuth Token"]
    A --> D["Bearer Token API Auth"]
    
    B --> B1["Minted by GitHub App"]
    B --> B2["RS256 JWT exchange"]
    B --> B3["In-process cached"]
    B --> B4["Scope: repos, perms"]
    
    C --> C1["Dashboard OAuth store"]
    C --> C2["Encrypted at rest"]
    C --> C3["Per-user principal"]
    C --> C4["Access + refresh token"]
    
    D --> D1["GitHub token header"]
    D --> D2["CSRF exempt"]
    D --> D3["Requires admin match"]
    
    B4 --> Narrow["Scope Narrowing"]
    C3 --> Narrow
    
    Narrow --> N1["App-wide repos"]
    Narrow --> N2["Thread subset repos"]
    Narrow --> N3["Per-proxy refresh"]
    
    style A fill:#e1f5ff
    style Narrow fill:#f3e5f5
```

Token types, caching, and scope narrowing for GitHub authorization.

## Dashboard authentication

The dashboard uses the GitHub App OAuth code flow and an HS256 session JWT signed with `DASHBOARD_JWT_SECRET`. `osw_session` is valid for seven days; `require_session` rejects absent or invalid sessions, and `/me` returns the session identity and a freshly evaluated `is_admin` flag.

`GET /dashboard/api/auth/login` generates a random nonce, places its HMAC in the signed state JWT, and stores the raw nonce in `osw_oauth_state`. The callback constant-time compares the recomputed HMAC before exchanging the OAuth code and identifying the GitHub user. It applies the organization gate before persisting the OAuth result or issuing a session. `sanitize_redirect_to` admits only a non-protocol-relative relative path or an absolute origin in `DASHBOARD_BASE_URL` plus `DASHBOARD_ALLOWED_ORIGINS`; it rejects login and API callback paths to prevent open-redirect loops and attacker-controlled destinations.

Session cookies are `HttpOnly`. The API uses `Secure; SameSite=None` only for HTTPS split-origin deployments; same-origin or HTTP deployments use `SameSite=Lax`. The state cookie is also `HttpOnly`, `SameSite=Lax`, scoped to `/dashboard/api/auth`, and has the 10-minute state lifetime.

### Membership, desktop, and automation entrypoints

`ALLOWED_GITHUB_ORGS` is a shared comma-separated allowlist. With entries, a login must be an active member of at least one organization; membership is checked through that organization's App installation with `members: read`, and missing installation/token, HTTP/parsing error, or inactive/non-member result fails closed with 403. With no entries, login intentionally fails open for compatibility and logs once per process that all GitHub accounts may log in and read surfaced threads.

Desktop login avoids placing a browser session on the loopback redirect. The callback instead sends a 120-second signed handoff code containing inert identity claims and the app's S256 PKCE challenge to a fixed `127.0.0.1` callback. The desktop exchange mints a session only after a constant-time verifier check. Cloud terminal tickets are separate 60-second JWTs, validated for fixed audience and the requested `thread_id`.

Cookie-authenticated mutations have an origin check: safe methods are exempt, and unsafe requests must have an allowed `Origin` or `Referer` when dashboard origins are configured. Bearer-token CSRF exemption applies only when no session cookie is present: requests that carry both a bearer token and a session cookie are still subject to the origin check, protecting against simultaneous session hijacking. No configured dashboard origins makes this check a local-development fail-open default. CORS is added only for configured origins and refuses `*` with credentials.

Certain admin endpoints additionally accept an explicit GitHub bearer token or Actions OIDC token. For a GitHub token, the service resolves `/user` and, if needed, the primary address from `/user/emails`, then requires that login or email to match `CONFIGURED_ADMINS`; an installation token that cannot identify a user is rejected.

Slack account linking uses Slack OIDC claims rather than user-supplied identity. If `SLACK_TEAM_ID` is configured, a different workspace is rejected, including Slack Connect identities. Authentication failure notices in shared Slack threads link only to the token-free dashboard settings URL, never to a user-specific authorization URL.

## Authenticating inbound calls

Webhook verifiers operate on raw request bodies and fail closed when their secret is missing:

- GitHub computes `sha256=HMAC(GITHUB_WEBHOOK_SECRET, body)`, constant-time compares `X-Hub-Signature-256`, and the GitHub route rejects failures before parsing the payload.
- Slack constant-time compares the HMAC of `v0:timestamp:body` and rejects timestamps more than 300 seconds from now, limiting replay.
- Linear constant-time compares its raw-body HMAC-SHA256 against `Linear-Signature`.
- `/webhooks/run-complete` compares its query token with `RUN_COMPLETE_WEBHOOK_SECRET` in constant time. Without that secret every call is rejected and run-failure replies remain disabled.

## Credential storage and authorization gates

`TOKEN_ENCRYPTION_KEY` may contain one key or a newest-first comma/newline-separated Fernet key list. `MultiFernet` encrypts with the first key and attempts all keys for decryption, supporting rotation. Invalid ciphertext or an unavailable key yields an empty decrypted value rather than raising. Dashboard profile OAuth records encrypt both GitHub access and refresh tokens before storing them. A near-expiry GitHub credential is refreshed under a per-login lock; GitHub's permanent `bad_refresh_token` and `unauthorized_client` errors cause the old authorization to be deleted unless a concurrent OAuth callback has already replaced it.

Authentication is not authorization. `CONFIGURED_ADMINS` matches emails or logins case-insensitively, and `require_admin` checks the triggering run identity at tool-call time instead of trusting thread metadata. Team observability tools are exposed only when the current run's identity is an admin or its email is in `OBSERVABILITY_AUTHORIZED_EMAILS`; the decision is intentionally evaluated per run to prevent attacker-influenced thread state from granting access.

## GitHub comment trust model

External GitHub comment content must be wrapped in reserved trust tags so the agent can identify untrusted input. `sanitize_github_comment_body` strips the reserved `<dangerous-external-untrusted-users-comment>` open and close tags from raw comment bodies before they are processed, preventing external authors from spoofing the trust wrapper. `format_github_comment_body_for_prompt` classifies comments by author membership in a known Open SWE users set (lowercased GitHub login): registered authors bypass the wrapper, while external authors are fenced with the reserved tags. This design prevents prompt injection by making untrusted boundaries explicit within the prompt.

## Focused verification

`tests/dashboard/test_dashboard_oauth_redirect.py` covers redirect allowlisting, state-cookie binding, and PKCE desktop exchange. `tests/dashboard/test_github_token_auth.py` covers GitHub bearer identity resolution and the admin gate; focused tests verify opaque proxy injection and that real keys do not enter sandbox environment variables.
