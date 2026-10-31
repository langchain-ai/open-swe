# Files

- [Backend–Managed Deep Agents boundary](backend-runtime-boundary.md) - How Open SWE plans and signs a Managed Deep Agents run, supplies a names-only catalog, and handles authenticated typed callbacks, worker delegation, and durable report delivery.
- [System architecture](overview.md) - Open SWE is a stateless FastAPI control plane backed by PostgreSQL and a separate Managed Deep Agents runtime that executes durable agent work. This page distinguishes the product and intake surfaces from legacy Agent Server compatibility entrypoints and active remote execution.
- [Threads, runs, and durable state](threads-runs-and-persistence.md) - How Open SWE indexes thread identity and metadata in PostgreSQL, routes stateful execution to Managed Deep Agents, and reconciles distributed thread and run status.
- [Run catalog and operation boundary](tool-catalog-and-operations.md) - How Open SWE derives a per-run catalog that configures MDA models, tools, sandboxes, skills, integrations, and loop behavior, while preserving the boundary between MDA-native tooling and backend operations.
