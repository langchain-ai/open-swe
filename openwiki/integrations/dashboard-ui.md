---
type: integration
title: Dashboard and UI Integration
description: React UI architecture, thread listing and chat interfaces, real-time updates, state invalidation, and OAuth integration.
tags: [dashboard, fastapi, oauth, threads, authorization, tanstack-start, langgraph, real-time]
sources:
  - id: openwiki-source-4b1279a0a1e5ec2d55a4558a
    resource: repo://openswe/api/app.py
  - id: openwiki-source-e4bce0ee35cec33ca72293f7
    resource: repo://openswe/dashboard/__init__.py
  - id: openwiki-source-6128627021aa8b6393710ab1
    resource: repo://openswe/dashboard/auth_routes.py
  - id: openwiki-source-c6809bf047de06f004194bd4
    resource: repo://openswe/dashboard/deps.py
  - id: openwiki-source-50d64b46ab06b6436266b4d0
    resource: repo://openswe/dashboard/oauth.py
  - id: openwiki-source-7fc33e4789861923a6f12e78
    resource: repo://openswe/dashboard/routes.py
  - id: openwiki-source-d4133df2d22c7f7c9b56d1fc
    resource: repo://openswe/review/routes.py
  - id: openwiki-source-7ee154e2a5d8a9f6403ac301
    resource: repo://openswe/schedules/routes.py
  - id: openwiki-source-919e16feae379651f2cbc1c9
    resource: repo://openswe/server.py
  - id: openwiki-source-1b56c0378ee7dbe5ac66ab32
    resource: repo://openswe/threads/handlers.py
  - id: openwiki-source-eedbca98a88c0fad1cac9934
    resource: repo://openswe/threads/listing.py
  - id: openwiki-source-a21a5bab5cd5ef26a5d5e360
    resource: repo://openswe/threads/summary.py
  - id: openwiki-source-59a48f28b14dd2fa02f2e8c3
    resource: repo://openswe/threads/terminal.py
  - id: openwiki-source-6922535f6d64f951fd826089
    resource: repo://openswe/ui_invalidations/hub.py
  - id: openwiki-source-207041ea6aef8242981dc8d7
    resource: repo://openswe/ui_invalidations/routes.py
  - id: openwiki-source-429c2b1da4ee5974be45c5c6
    resource: repo://openswe/ui_invalidations/topics.py
  - id: openwiki-source-33b1621aff91e24fa4b85e3f
    resource: repo://openswe/utils/dashboard_ui.py
  - id: openwiki-source-cee8c9d42a08db69733a075f
    resource: repo://ui/server/backend-proxy.ts
  - id: openwiki-source-3b0d59e2570cb537382d8c12
    resource: repo://ui/src/lib/dashboard-fetch.ts
  - id: openwiki-source-a741d432f952c0dbfb4fb35d
    resource: repo://ui/vite.config.ts
generated: { by: "openwiki/0.4.2", at: "2026-10-21T13:36:13.083Z" }
verified:
  - by: openwiki/0.4.2
    at: 2026-10-21T13:36:13.083Z
---

# Dashboard and UI Integration

The dashboard is the human-facing surface for agent operations: a FastAPI router exposing APIs, a React application built with TanStack Start serving the frontend, and TypeScript proxies that mediate between browsers/clients and the backend. The dashboard API is the policy boundary for sessions, GitHub-derived authority, dashboard records, and LangGraph operations; browser-facing clients use proxies rather than exposing server credentials or calling a raw LangGraph deployment directly.

## Composition and routing

`openswe.api.app.create_app()` includes the dashboard router, then calls `mount_dashboard_ui(app)`. The router has the `/dashboard/api` prefix and a router-wide mutation-origin dependency that guards all cookie-authenticated state changes. `openswe.dashboard` exports that router lazily with `__getattr__` so that importing a dashboard submodule (e.g., for middleware configuration) does not pull in FastAPI, routes, and every API/job module; only the application that mounts the router pays that cost.

The dashboard router aggregates over 30 feature routers including threads, reviews, schedules, skills, workspaces, profiles, and integrations (Slack, MCP). Each endpoint enforces session authentication, repository access control, and admin-only operations where required.

## Session and request security

GitHub login creates signed state containing a hash of a nonce placed in a short-lived HTTP-only state cookie, then redirects to GitHub. The callback verifies the state cookie for normal browser login, exchanges the authorization code, resolves the GitHub user, applies the organization login gate, persists the token response, and redirects with a signed session cookie. A desktop handoff is different: after the same identity checks it returns a PKCE-challenge-bound code to the desktop loopback listener without setting a browser session; `POST /auth/desktop/exchange` requires the matching verifier before minting the desktop session.

Cookie security is derived from the API URL and whether UI and API share an origin: HTTP is non-secure and `SameSite=Lax`; same-origin HTTPS is `Secure; SameSite=Lax`; split-origin HTTPS is `Secure; SameSite=None`. `require_session` turns a missing or invalid session cookie into `401`. Admin routes additionally enforce `is_admin`; no alternate authentication methods are currently supported for dashboard admin endpoints.

The router-wide CSRF guard permits safe methods and requests authenticated only with an explicit bearer token. Cookie-authenticated mutations require an allowed `Origin` or `Referer`; WebSockets always receive the origin check. This control protects the ambient cookie, not business authority: individual endpoints still enforce administrator status, repository access, or thread postability. At app construction, credentialed CORS may be configured from `DASHBOARD_ALLOWED_ORIGINS`, but `*` is rejected because it is unsafe with credentials.

## Thread discovery and access

Thread discovery and thread readability are intentionally different. Normal listings search participant login/email metadata, including legacy creator metadata. The `all=true` parameter is restricted to administrators. The paginated endpoint clamps `limit` to 1–100, normalizes negative offsets, validates `repo` as `owner/name`, and rejects `repo` together with `ownerless`. Metadata filtering occurs before summary construction; viewed/status filtering requires summaries. Potentially active latest runs are refreshed with a concurrency limit of eight.

Any authenticated organization member can read a thread whose source is surfaced; unsurfaced threads return `404`. Posting applies that readable check and additionally requires an administrator for automation and `admin_thread` threads. `/threads/{thread_id}` returns a metadata-derived summary, not converted messages: the client stream provider obtains the transcript from the LangGraph state endpoint. A finished detail read normally writes viewed metadata but supports `mark_viewed=false`, never marks a running thread viewed, and treats metadata-write failure as non-fatal. An interrupted latest run is reported as interrupted even while the thread itself briefly remains busy.

Projects are metadata-only groups of matching configured repositories, keyed case-insensitively and sorted by latest update; they omit ownerless work and default to excluding resolved and automation threads. Pins are stored per login, not in thread metadata. Pinning verifies readability; loading pins retrieves every saved ID independently and omits missing, failed, or no-longer-readable threads. Thus a pin never bypasses present access checks.

The cloud terminal uses a two-step contract. `POST /threads/{id}/terminal/connect` checks readable thread and ready sandbox, returns a no-store WebSocket URL, the `open-swe-terminal` protocol, and a signed ticket. The WebSocket expects protocol plus ticket, validates its thread-bound ticket and origin, then repeats readable/sandbox validation. It only operates for a LangSmith sandbox, permits 20 concurrent sessions, and closes with `1013` when full. It bridges bounded input and resize messages to a PTY shell and kills the handle when the connection ends.

## Dashboard-managed configuration and automation

Repository instruction records normalize `owner/repo`; route handlers filter lists and guard direct access through current repository authority. For a run's resolved repository, non-empty instructions are appended to the main prompt (and lookup failure is fail-soft). Review styles use similarly repository-guarded records with `idle`, `running`, `completed`, and `failed` states. A read reconciles an in-progress analyzer; a concurrent analysis returns `409`; a saved prompt allows a terminal or missing analyzer run to become completed.

Personal skills are virtual `SKILL.md` files isolated by GitHub login. Organization skills are shared, cursor-paginated, limited to 1,000 records, readable by every session, and writable by administrators only. Schedule listing requires a session, while create, update, trigger, and delete require administration. Workspace-scoped schedule records keep cron configuration separate from run state. Creation writes the record before creating a LangGraph cron and removes it with `502` if creation fails; enabled changes create the replacement cron before deleting the old one, while disabling deletes the cron. Before launch, repository access for the recorded owner is rechecked; failure records an unauthorized run state rather than launching. Successful launch creates an automation thread and resumable durable agent run.

## Real-time UI state invalidation

The browser opens a server-sent events stream at `GET /dashboard/api/ui-invalidations?t=...` with a list of topics and their staleness age (e.g., `t=42.workspaces` means "workspaces data is 42 seconds old"). The invalidation hub in `openswe.ui_invalidations.hub` manages a single shared PostgreSQL `LISTEN` connection that notifies all replicas when a topic changes; each replica's `Stream` registry tracks which topics connected clients need and immediately signals when an invalidation arrives.

The stream first reports which subscribed topics were invalidated within their staleness window (a replay window accounting for PostgreSQL lag and commit delays), then continues to emit every topic as it is invalidated, one per frame. The browser refetches only the invalidated queries; empty, out-of-window, or unauthorized topics are silently dropped. This pattern avoids polling while ensuring that queries stay synchronized with server state changes that happen during client inactivity. A `Topic` is a fully qualified name like `workspaces`, `threads/thread-id`, or `review-styles/owner/repo`, and authorization checks which keys a session may hear before allowing the subscription.

## Pull request reviews and findings display

The dashboard displays and manages PR reviews from `openswe.review.routes`, which serves both direct code review operations and a conversation UI for asynchronous discussion. Review styles hold an approval mode (when set, delegating approval authority to the analyzer), and a repository-held prompt that steers the analyzer behavior. Reading a review returns a summary containing the analyzer's findings; concurrent requests to start a new analysis return `409` to prevent redundant work.

The findings display retrieves review summaries in batches via `POST /dashboard/api/reviews/summaries`, passing a list of up to 100 PR references. Findings include tool-generated issues (code smells, security concerns, etc.) that the UI surfaces alongside the human review conversation. A `review_conversation` router manages the chat interface where reviewers propose comments, approve changes, and share insights asynchronously. The UI permits a terminal or missing analyzer run to be marked completed, allowing a saved prompt change to take effect without rerunning analysis.

## React UI and deployment proxy

`ui/` is a React/TanStack Start application with TanStack Router, React Query SSR integration, and a Vite/Nitro server build. The browser API layer forms relative `/dashboard/api/*` URLs and uses `credentials: "include"`. In development, Vite proxies backend prefixes (with a localhost default); deployed Nitro explicitly sends `/dashboard/api/**` and `/webhooks/**` to `ui/server/backend-proxy.ts` and requires `DASHBOARD_API_URL` at runtime.

The production proxy retains OAuth redirects with `redirect: "manual"`, forwards request bodies and headers, removes hop-by-hop and reframed response headers, and appends each `Set-Cookie` separately. Server rendering targets `DASHBOARD_API_URL` directly and explicitly forwards the incoming `cookie` header because server-side `credentials: "include"` cannot do so. The Agents layout permits an unauthenticated desktop-local-only mode only at `/agents` and `/agents/local/...`; its shared stream provider chooses local transport only for a local thread and cloud transport otherwise.

## Dashboard mounting and static serving

When a dashboard build is available, `openswe.utils.dashboard_ui` mounts immutable hashed assets at `/assets` and serves the shell (`_shell.html`) for HTML navigation requests. It deliberately declines API, webhook, health, LangGraph, docs, metrics, and asset prefixes; a non-HTML request for an unknown UI route is likewise left for the underlying server to return as a 404. The shell is `no-cache` so it can reference a new asset manifest, while hashed assets can be cached for a year. With `DASHBOARD_DEV_SERVER_URL`, the backend instead reverse-proxies non-reserved traffic to Vite, preserving the backend origin and redirect responses. The catch-all is registered last; code which subsequently adds a route must call `keep_dashboard_ui_last`.

`DASHBOARD_STATIC_DIR` selects an explicit build; otherwise the in-repository `ui/.output/public` build is used when present. A build served under a LangGraph mount prefix must be built with the matching `DASHBOARD_BASE_PATH`. The UI router uses Vite's `BASE_URL` as its `basepath`, so client navigation follows that mount.

## Run stream display and agent events

The dashboard displays agent runs using the LangGraph v3 protocol stream, which carries both state snapshots and intermediate event frames. The thread detail view embeds a stream provider that opens `/threads/{id}/stream` to receive events as the run progresses. Events include tool invocations, state updates, and checkpoint markers, allowing the UI to show real-time progress, tool calls with inputs and outputs, and retrieval windows.

Subagent visibility is determined by the run's manifest: tool events exposing subagent thread IDs are rendered as clickable references in the main thread UI, allowing users to navigate into nested invocations. Tool events carry the full tool schema, input parameters, and parsed output, enabling the UI to render domain-specific visualizations (e.g., file diffs for code review tools, artifact previews for generation tools). On desktop runs, the UI avoids exposing actual tool results containing credentials or artifacts; instead it renders locally-scoped results that come from the supervised backend and are safe to display in the browser.

## Related

- [Architecture overview](../architecture/overview.md)
- [Auth and security](../concepts/auth-and-security.md)
- [Deployment](../operations/deployment.md)
- [Invocation](../workflows/invocation.md)
