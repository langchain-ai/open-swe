# Files

- [Coding Agent Assembly](agent-graph.md) - How an executable coding-agent run is parsed, resolved into a sandbox, durable settings, prompt context, tools, and middleware, then compiled as a Deep Agent graph.
- [Agent Middleware Stack](middleware-stack.md) - Ordering-sensitive middleware around coding-agent and reviewer model and tool loops. Covers preparation, transcript capture, policy enforcement, queue delivery, fallback, completion, and sandbox failure boundaries.
- [Runtime Architecture and Public Surfaces](overview.md) - How the LangGraph deployment combines registered agent graphs, a FastAPI application, PostgreSQL, scheduler work, dashboard UI, and authenticated external triggers into one runtime.
- [Review, Style Analysis, and Review Scout Graphs](reviewer-and-analyzer.md) - How the read-only PR reviewer, repository-style analyzer, and review scout coordinate sandboxes, PostgreSQL records, LangGraph thread metadata, GitHub publication, and re-reviewing.
- [Thread Sandbox Lifecycle](sandbox-lifecycle.md) - How an agent thread binds to a sandbox, reconnects safely, provisions from workspace snapshots, and refreshes sandbox-scoped GitHub access. Explains replacement rules that protect uncommitted work.
