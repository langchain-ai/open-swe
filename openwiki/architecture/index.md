# Files

- [Coding Agent Assembly](agent-graph.md) - How each executable Open SWE thread run assembles its Deep Agent graph, including configuration and model resolution, sandbox-backed filesystem, skills, tools, subagents, and middleware.
- [Middleware Stack and Failure Boundaries](middleware-stack.md) - Ordered middleware around Open SWE agent and reviewer model and tool calls. Covers lifecycle hooks, safety policy, queue interruption, deadlines, retries, and terminal failure behavior.
- [Runtime Architecture and Entrypoints](overview.md) - How the LangGraph deployment, FastAPI composition, dashboard, webhooks, scheduler, and desktop client reach Open SWE's specialized runtime graphs.
- [Review and Review-Style Graphs](reviewer-and-analyzer.md) - The read-only reviewer prepares and reviews GitHub pull requests through durable findings, while the analyzer learns and maintains repository-specific review guidance.
- [Thread Sandbox Lifecycle](sandbox-lifecycle.md) - How a thread acquires, persists, reconnects to, replaces, and explicitly rebinds its sandbox, including provider dispatch, initialization, credentials, and failure safety.
