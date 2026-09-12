# Files

- [Coding Agent Assembly](agent-graph.md) - How the primary Deep Agent is assembled per executable thread run, resolving configuration, durable settings, sandbox, model policy, prompts, skills, tools, subagents, and middleware.
- [Agent Middleware Stack](middleware-stack.md) - Ordering-sensitive middleware around the main coding agent and reviewer agent. Covers run preparation, tool and delivery safety gates, queued follow-up interception, model timeouts and fallback, and completion behavior.
- [Runtime and Product Architecture](overview.md) - How the LangGraph deployment registers graph entrypoints, composes FastAPI ingress, creates durable runs, and exposes cloud dashboard and local desktop surfaces.
- [Reviewer and Style Analyzer Graphs](reviewer-and-analyzer.md) - The reviewer graph prepares and assesses a pull request without changing its repository, while the analyzer graph learns a bounded, per-repository review-style supplement. This page covers their distinct execution composition, durable state, publishing, and style-learning operations.
- [Thread-Scoped Sandbox Lifecycle](sandbox-lifecycle.md) - How a thread provisions, binds, reconnects to, refreshes, and safely replaces its sandbox. Covers durable state, GitHub proxy credentials, reviewer exceptions, and environment snapshot freshness.
