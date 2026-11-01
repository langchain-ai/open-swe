# Files

- [Backend–Managed Deep Agents Runtime Contract](backend-mda-runtime.md) - How Open SWE plans and authenticates a Managed Deep Agents run, and how the MDA-owned agent loop invokes the backend through typed, stateless endpoints for data, credentials, tools, reporting, and completion.
- [System Topology and Entrypoints](overview.md) - Open SWE combines a FastAPI and PostgreSQL control plane with a Managed Deep Agents runtime that executes all current agent, reviewer, and review-scout threads. This page maps public ingress, graph registrations, runtime boundaries, and durable thread ownership.
- [Thread Indexing, Routing, and Runtime Ownership](thread-runtime.md) - PostgreSQL-backed thread metadata and status, the Agent Server-compatible ThreadRouter, and the rules that route Open SWE conversations and runs to Managed Deep Agents or legacy local compatibility threads.
