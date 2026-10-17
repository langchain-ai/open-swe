# Files

- [Coding Agent Assembly](agent-graph.md) - Construction and execution preparation of the thread-bound Open SWE coding graph, including model policy, sandbox-backed workspace, tool surfaces, skills, subagents, and ordered middleware.
- [Middleware and Failure Semantics](middleware-stack.md) - Ordered middleware defines how Open SWE's coding agent, reviewer, and Managed Deep Agents remote loop prepare runs, shape calls, enforce tool policy, retry failures, and surface terminal outcomes.
- [System Architecture and Runtime Boundaries](overview.md) - How Open SWE composes its FastAPI service, LangGraph graph entrypoints, PostgreSQL-backed control plane, Managed Deep Agents execution, and durable background infrastructure.
- [Managed Runtime and Thread Routing](remote-runtime-and-threads.md) - How Open SWE presents one Agent Server thread API while routing fresh hosted graph runs to Managed Deep Agents and retaining legacy local executions. Covers signed remote hooks, state exchange, status settlement, and recovery boundaries.
- [Review and Review-Scout Graphs](reviewer-and-review-scout.md) - The isolated LangGraph reviewer and review-scout roles that prepare a pull request deterministically, turn evidence into durable findings, and safely reconcile and publish review feedback.
- [Thread Sandbox Lifecycle](sandbox-lifecycle.md) - Per-thread sandbox ownership, provisioning, reconnection, credential proxying, handoff, and safe recovery. Explains hosted sandboxes, shared task sandboxes, and user-machine bridge backends.
