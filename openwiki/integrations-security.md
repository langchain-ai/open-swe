---
type: Security Architecture
title: Integrations, execution boundaries, and security controls
description: Security and integration model for Open SWE sandboxes, GitHub identity, webhook verification, dashboard sessions, and optional server-side MCP or observability tools.
resource: /agent/integrations
tags: [open-swe, security, integrations, sandbox, authentication]
---
# Integrations, execution boundaries, and security controls

## Isolation boundary: per-thread sandboxes

Open SWE runs repository work in a sandbox backend selected by `SANDBOX_TYPE`. `agent/utils/sandbox.py` registers factories for LangSmith (the default), Daytona, Modal, Runloop, E2B, and local execution. The local provider deliberately has no isolation and is development-only; it should not be treated as a production-equivalent configuration.

The coding graph keeps a sandbox stable for a thread across follow-ups. Process memory caches backend objects, while LangGraph thread metadata retains the durable sandbox ID. Reconnection logic pings/reuses/reconnects/recreates a backend as needed. This execution lifecycle **underpins the coding and review paths in [runtime architecture](runtime-architecture.md)** and makes a conversation-like task able to preserve its working environment.

The default LangSmith integration configures a GitHub proxy rather than placing a real GitHub token inside the sandbox. Git operations for `github.com` and API operations for `api.github.com` receive the appropriate proxy authentication using a fresh GitHub App installation token. Other sandbox providers do not inherit that exact proxy path automatically, so provider additions must document their credential boundary explicitly.

## GitHub identity and repository access

Open SWE resolves GitHub identity in dual mode: it prefers a user token made available through the configured OAuth/session path and falls back to a GitHub App installation token when necessary. User/credential handling is server-side; the dashboard provides GitHub OAuth endpoints while agent token resolution is implemented in `agent/utils/auth.py` and related utilities.

GitHub event routes verify signatures before dispatch. Slack and Linear routes likewise verify their provider requests, and webhook routes enforce repository/source gates before they create work. Deterministic thread IDs then connect later messages to the right run. These checks **protect the external-entry workflows in [workflows](workflows.md)**; new triggers must preserve them rather than calling a graph directly.

## Dashboard and API safeguards

The FastAPI application enables CORS only for configured origins and rejects `*` when credentials are enabled. Dashboard APIs use session and admin dependencies, while mutation paths additionally perform same-origin protections. The [dashboard](dashboard.md) UI is a credentialed client of `/dashboard/api/*`; it is not the authority for authorization decisions.

For production, the Vercel configuration rewrites `/dashboard/api/*` to the hosted backend, allowing dashboard cookies and API calls to remain same-origin. A direct cross-origin deployment is supported only with coordinated explicit frontend/backend URL and origin configuration; see `docs/INSTALLATION.md` for the setup sequence.

## Optional server-side tools

`agent/server.py` can load integrations such as Datadog, LangSmith, Corridor, Notion, Currents, and browser tooling. They are server-side capabilities, not generic sandbox credentials:

- Observability tools are limited to authorized users because logs/traces can be attacker-influenced and include sensitive organizational context.
- Datadog and LangSmith credentials are retained server-side rather than copied into task sandboxes.
- Corridor validates the allowed HTTPS endpoint and exposes only its plan-analysis tool.
- Notion access is per user and refreshes its OAuth token at invocation time.

These integrations expand the coding graph described in [runtime architecture](runtime-architecture.md), so adding one requires a threat-model review: least-privileged scopes, explicit user eligibility, untrusted-tool-output handling, and tests for denied/invalid paths.

## Safe extension checklist

1. **Sandbox provider:** implement a `SandboxBackendProtocol`-compatible factory under `agent/integrations/`; register it in `agent/utils/sandbox.py`; test creation/reconnection and explain how Git credentials are isolated.
2. **Webhook source:** verify signatures at the route boundary; validate allowed repositories/users; treat remote text as untrusted; construct deterministic thread IDs; use `agent/dispatch.py`.
3. **MCP/external capability:** keep credentials server-side, expose only narrowly required tools, restrict who can load them, and sanitize/review external content as untrusted.
4. **Dashboard mutation:** implement backend authorization and same-origin checks first, then add the typed UI client and route described in [dashboard](dashboard.md).

## Verification focus

Security-sensitive changes should be paired with targeted tests under `tests/auth/`, `tests/github/`, `tests/slack/`, `tests/webhooks/`, `tests/sandbox/`, and `tests/tools/`. The broader command matrix, real-flow E2E test, and CI checks are in [operations and quality](operations-quality.md).
