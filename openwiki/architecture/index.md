# Files

- [Coding Agent Assembly and Execution](agent-graph.md) - How an executable Open SWE thread is assembled into a configured Deep Agent, including configuration, thread and workspace policy, sandbox or desktop backends, prompt preparation, tool surfaces, model routing, and tracing.
- [Agent Middleware, Guards, and Recovery](middleware-stack.md) - Ordered middleware boundaries around the coding agent and PR reviewer. Covers preparation, transcript and message repair, dynamic tools, model routing and recovery, tool and sandbox failures, approval gates, and completion handling.
- [Runtime Architecture and Service Composition](overview.md) - How Open SWE composes registered LangGraph graphs, a lifecycle-managed FastAPI application, PostgreSQL-backed services, and web, desktop, and CLI clients into one durable execution system.
- [Review, Analyzer, and Scout Graphs](reviewer-and-analyzer.md) - Specialized LangGraph deep-agent factories that prepare pull-request reviews, produce a review walkthrough, persist and publish findings, and learn repository-specific review guidance.
- [Sandbox and Local Execution Lifecycle](sandbox-lifecycle.md) - How Open SWE binds a thread to a managed sandbox, a shared task sandbox, or a user-owned bridge, and how it safely reconnects, moves, and replaces execution environments.
