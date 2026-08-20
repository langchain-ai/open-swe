---
type: User Interface Architecture
title: Dashboard and workspace management
description: The Open SWE Vite/TanStack dashboard, its FastAPI API boundary, authenticated agent workflows, and workspace administration features.
resource: /ui/src
tags: [open-swe, dashboard, ui, fastapi, administration]
---
# Dashboard and workspace management

## Client and API boundary

The dashboard is a Vite/TanStack Start application in `ui/`, with file-based routes under `ui/src/routes/`. It is an authenticated client, not an independent application backend: `ui/src/lib/api.ts` and `ui/src/features/agents/lib/api.ts` call FastAPI’s `/dashboard/api/*` endpoints with credentials included.

The corresponding backend is `agent/dashboard/routes.py`, mounted by `agent/api/app.py` alongside webhook and plan/approval routes. Session, admin, and mutation-origin rules therefore remain server-enforced. This interface **depends on the FastAPI composition in [runtime architecture](runtime-architecture.md)** and should be changed as a client/server pair.

Vercel’s `ui/vercel.json` rewrites `/dashboard/api/*` to the hosted LangGraph application for same-origin production operation. Local development can point the client at a separate API base URL, with explicit CORS configuration. Installation details remain in `docs/INSTALLATION.md`.

## User-facing workflows

`/` redirects to `/agents`, the principal authenticated workspace. The agents area includes:

- a landing/new-work surface (`ui/src/routes/agents/index.tsx`),
- streamed thread/chat and plan views (`agents/$threadId.tsx`, `agents/$threadId_.plan.tsx`),
- searchable thread history (`agents/threads.tsx`),
- automation/schedule views (`agents/automations/`), and
- PR-review history/detail (`agents/reviews/`).

The agent client streams and manages threads through dashboard endpoints, thereby **surfacing the durable coding workflow in [workflows](workflows.md)**. User settings cover GitHub-linked preferences, Slack mapping, notifications, and integrations. Repository instructions and snapshot views live in the agents area and connect workspace configuration to the prompts/sandbox runtime.

## Review and admin management

The review workspace configures enabled repositories, organization guidance, draft-review policy, summaries, model defaults, and repository-specific review styles. The backend protects mutations with administrator authorization even when read-oriented screens are generally visible.

`/admin` is explicitly admin-gated. It includes workspace model/gateway configuration, review triggering, running-agent interruption, observability credentials, PR trace handling, and user mappings. `/admin/evals` renders the latest reviewer-evaluation state/logs exposed by the evaluation store. This makes the UI an operational control plane for the review-style and evaluation lifecycle described in [workflows](workflows.md) and [operations and quality](operations-quality.md).

## Change guidance

For a feature that spans dashboard and backend:

1. Find the relevant file route in `ui/src/routes/` and feature components under `ui/src/features/` or `ui/src/components/`.
2. Add or adjust a typed client method in `ui/src/lib/api.ts` or `ui/src/features/agents/lib/api.ts`.
3. Add the secured endpoint and domain logic in `agent/dashboard/routes.py` and its focused module under `agent/dashboard/`.
4. Keep authorization/same-origin checks on the backend; don’t rely on a hidden UI control.
5. Do not hand-edit `ui/src/routeTree.gen.ts`; it is generated.

Run UI unit/type/lint/build checks for UI work, plus focused Python dashboard tests. [Operations and quality](operations-quality.md) lists those commands and the real-dashboard E2E harness.
