# Files

- [Coding Agent Assembly](agent-graph.md) - How the primary Deep Agent graph is assembled for each executable thread run, from tolerant run configuration and model selection to sandbox-backed workspace context, tools, subagents, and middleware.
- [Agent Middleware and Failure Boundaries](middleware-stack.md) - The ordered middleware paths around coding-agent and reviewer model and tool loops. Covers run preparation, model routing and fallback, dynamic tools, follow-up delivery, transcript observability, policy guards, and terminal failure behavior.
- [Runtime and Product Architecture](overview.md) - How Open SWE deploys LangGraph graphs, composes its FastAPI ingress, dispatches durable work, and separates cloud dashboard, desktop, and external integration surfaces.
- [Review, Scout, and Style-Analysis Graphs](reviewer-and-analyzer.md)
- [Per-Thread Sandbox Lifecycle](sandbox-lifecycle.md) - How a thread binds to, reconnects to, initializes, refreshes, and replaces its sandbox, including hosted providers and local-machine bridges. Explains the durable metadata contract and failure rules that protect a thread's working tree.
