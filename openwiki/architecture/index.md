# Files

- [Managed Agent Runtime and Typed Backend Contract](managed-runtime.md) - How Open SWE plans agent, reviewer, and review-scout runs for Managed Deep Agents, and how the separate runtime calls the backend through a signed, typed API.
- [System Architecture and Deployment Boundaries](overview.md) - Open SWE separates its FastAPI and PostgreSQL control plane from Managed Deep Agents execution. This page describes public ingress, Agent Server-compatible thread routing, typed runtime callbacks, and dashboard, desktop, and CLI clients.
- [Persistent State, Transcripts, and Background Processing](state-and-async-services.md) - PostgreSQL-backed state, durable thread transcripts, cross-replica notifications, jobs, and analytics delivery in Open SWE. Covers lifecycle ordering, recovery behavior, and the constraints that make persistence changes safe.
