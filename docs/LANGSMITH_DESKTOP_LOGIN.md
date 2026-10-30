# LangSmith desktop login

Agent Workspace uses a LangSmith account independently of Open SWE's GitHub login. A LangSmith identity receives its own `users.id` and `user_identity(provider='langsmith')` row. Email addresses never join the two accounts, and provider identities cannot be linked to a LangSmith account. GitHub sign-in continues to use its existing allowlists and OAuth application.

## Deployment prerequisites

Register a **confidential OAuth client** in LangSmith organization settings. Dynamic client registration is insufficient: dynamically registered clients are limited to the MCP resource, while login authorization needs the LangSmith API resource.

Configure the backend with:

- `LANGSMITH_CONNECTION_URL`: the LangSmith API issuer, normally `https://api.smith.langchain.com`.
- `LANGSMITH_OAUTH_CLIENT_ID` and `LANGSMITH_OAUTH_CLIENT_SECRET`: the registered confidential client. Never ship the secret in the desktop application.
- `DASHBOARD_API_BASE_URL`: the publicly reachable Open SWE backend origin.
- `DASHBOARD_BASE_URL`: the existing dashboard origin.
- `DASHBOARD_JWT_SECRET`: the existing session/state signing secret.
- `TOKEN_ENCRYPTION_KEY`: the existing Fernet credential-encryption key.
- `POSTGRES_URI`: the backend PostgreSQL database, with migration `0088_af406b7c5892` applied.

Register this exact redirect URI on the LangSmith client:

```text
<DASHBOARD_API_BASE_URL>/dashboard/api/auth/langsmith/callback
```

The application discovers `/.well-known/openid-configuration`, requests `openid profile email offline_access`, uses OAuth PKCE S256, and explicitly requests the API resource equal to `LANGSMITH_CONNECTION_URL`. Discovery endpoints must remain on that issuer's origin. The user must select organization `f5c798a2-2155-4999-ad27-6d466bd26e1c` during consent. This organization restriction is a backend constant, not a request parameter or desktop preference.

Configuration and client registration are operator prerequisites; checking in the application does not provision the client or deploy its secrets.

## Desktop handoff

The desktop app opens `/dashboard/api/auth/langsmith/login` in the system browser with `desktop_handoff=<S256 challenge>` and `desktop_port=<loopback listener port>`. Both parameters are mandatory. LangSmith returns to the backend, which exchanges its authorization code, verifies identity and membership, and redirects to the fixed `http://127.0.0.1:<port>/callback` target with a short-lived, signed handoff code. The browser receives no LangSmith Open SWE session cookie.

The desktop app redeems that code at `POST /dashboard/api/auth/desktop/exchange` with its private verifier. Wrong verifiers and connection-flow codes are rejected. The handoff JWT cannot itself authenticate as a session cookie. The resulting app session names `user:<Open SWE UUID>`, includes `provider='langsmith'` and a separate immutable `langsmith_subject`, and contains no provider access or refresh token.

## Membership and renewal

At login and on each authenticated session request, the backend validates the bearer token with `/userinfo` and queries `/orgs?skip_create=true`. The required organization must match both the token's `ls_org_id` and a current organization membership. `/orgs` returns an unpaginated list; there is no pagination cursor to follow. The `/userinfo` organization claim alone is insufficient because LangSmith's claims lookup does not check current organization identities.

This contract was checked against the LangSmith implementation in `smith-go/oauth/oidc_handler.go`, `smith-go/oauth/crud.go`, `smith-backend/app/api/endpoints/orgs.py`, and `smith-backend/app/models/organizations/list.py`.

Login credentials are encrypted in `user_oauth_credential` under the separate `langsmith_login` provider. Access tokens nearing expiry are refreshed under the existing cross-replica refresh guard, preserving a refresh token when the provider does not rotate it. Membership loss, identity mismatch, and revoked grants fail closed and remove the local login credential. Provider/network failures deny the request temporarily without discarding the grant.

After successful live authorization, `/me` renews the desktop session cookie for the existing seven-day lifetime. The app calls `/me` when loading and revalidates its stale session when the window regains focus. An app inactive past that session lifetime must sign in again; a stored provider refresh grant does not revive an expired Open SWE session.

Managed tools reuse the same LangSmith login grant after authorization rather than requiring a second account connection. Generic disconnect is rejected for login-backed accounts; sign out instead. GitHub repository endpoints reject LangSmith sessions rather than falling back to an installation or bot credential.

## Execution scope

Agent Workspace tasks are private and run through the desktop's local bridge, including task workers. New chats create their own local task folder; selecting an existing folder is optional. Cloud sandbox moves and backend GitHub operations are unavailable. LangSmith accounts cannot borrow the deployment's GitHub App, bot, or user credentials. Organization membership is revalidated before dispatch and on runtime API calls, including worker grants, so a running task cannot keep using backend tools after access is revoked.

The browser and regular Open SWE desktop remain coding-only. The Fleet experiment is removed; installing a different distribution does not change account preferences or link identities. Operator setup and real macOS/login smoke testing are still required before company distribution.

## Verification

The focused login integration tests use a real migrated PostgreSQL database and a mock LangSmith HTTP transport. They cover separate same-email accounts, denied organizations and revoked memberships, refresh and revoked grants, changed subjects, unavailable membership service, desktop-only login, and PKCE/purpose boundaries. A production smoke test still requires the registered confidential client and a real member/nonmember account; mocked tests do not establish that deployment wiring is complete.
