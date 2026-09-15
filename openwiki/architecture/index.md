# Files

- [Coding Agent Assembly](agent-graph.md) - How an executable thread run becomes the primary Deep Agents coding graph, including run configuration, sandbox and skill backends, model policy, prompt preparation, capability gates, and the delegated subagent.
- [Agent Middleware Stack](middleware-stack.md) - Ordering-sensitive middleware around coding-agent and reviewer model and tool loops. Covers preparation, context compaction, policy guards, queue delivery, retries, deadlines, usage, and provider-message normalization.
- [Runtime Architecture and Public Surfaces](overview.md) - How the LangGraph service composes graph entrypoints, FastAPI ingress, dashboard delivery, durable execution state, analytics lifecycle, and the desktop-local backend.
- [Review and Review-Style Graphs](reviewer-and-analyzer.md) - The read-only pull-request reviewer and the separate analyzer that learns repository-specific review guidance. Covers execution, durable findings and publication, style-analysis jobs, and shared sandbox and model limits.
- [Thread Sandbox Lifecycle](sandbox-lifecycle.md) - How a thread acquires, persists, reconnects to, and deliberately replaces its sandbox. Covers provider selection, environment snapshots, proxy-backed GitHub credentials, and data-preserving recovery.
