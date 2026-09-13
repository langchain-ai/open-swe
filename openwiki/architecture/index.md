# Files

- [Coding Agent Assembly](agent-graph.md) - How the primary Deep Agents coding graph is assembled for an executable thread run, including configuration, model policy, sandbox and skills backends, tool surfaces, subagents, and run preparation.
- [Middleware and Failure Boundaries](middleware-stack.md) - Ordering-sensitive middleware around the coding agent and reviewer model and tool loops. Explains preparation, policy, retries, deadlines, completion hooks, and how failures become safe user-visible outcomes.
- [Runtime and Product Architecture](overview.md) - LangGraph deployment, graph entrypoints, FastAPI ingress, durable dispatch, sandbox ownership, and the dashboard and desktop product surfaces.
- [Review and Style Analysis Graphs](reviewer-and-analyzer.md) - Architecture of the isolated reviewer and review-style analyzer graphs, including repository preparation, durable finding reconciliation and publication, per-repository style persistence, and continual analysis scheduling.
- [Thread Sandbox Lifecycle](sandbox-lifecycle.md) - How a thread acquires, persists, reconnects to, and deliberately replaces its sandbox. Covers provider selection, proxy-backed credentials, recovery safety, and operational lifecycle controls.
