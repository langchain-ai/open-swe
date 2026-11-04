# Files

- [Backend control plane, threads, and durable state](backend-control-plane.md) - How the FastAPI control plane starts, owns PostgreSQL state, routes thread and run requests to Managed Deep Agents, and serves durable transcript and inbox behavior.
- [Managed Deep Agents runtime](managed-runtime.md) - The Managed Deep Agents runtime is the shared Open SWE graph that executes agent, reviewer, and review-scout runs. Per-run catalogs and tokens select tools, models, loop rules, files, and sandbox behavior without making those run-specific concerns part of the compiled graph.
- [System architecture and ownership boundaries](overview.md) - Open SWE is a FastAPI and PostgreSQL control plane paired with a separate Managed Deep Agents execution deployment. This overview defines the ownership, typed contracts, and request paths that connect the dashboard and channels to durable agent runs.
- [Signed runtime API contract](runtime-api-contract.md) - How Open SWE resolves and signs a remote run, exposes its typed /agent-api callback surface, and preserves authorization and report-delivery invariants across backend replicas.
