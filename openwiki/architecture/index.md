# Files

- [Coding Agent Assembly](agent-graph.md) - How the primary Deep Agents coding graph is assembled for an executable LangGraph thread, including configuration, sandbox and skill backends, model policy, tools, middleware, and per-run preparation.
- [Middleware and Run Guardrails](middleware-stack.md) - Ordering-sensitive middleware that prepares coding and review runs, governs model and tool calls, and turns delivery, timeout, queue, and failure edge cases into controlled outcomes.
- [System Architecture and Runtime Surfaces](overview.md) - System-level map of Open SWE's LangGraph graph suite, FastAPI ingress, durable-run boundary, persistence, and cloud and desktop product surfaces.
- [Review, Style Analysis, and Review Scout Graphs](reviewer-and-analyzer.md) - The reviewer, analyzer, and review-scout graphs prepare pull-request context, produce durable findings or walkthroughs, and publish review guidance under separate authority and sandbox boundaries.
- [Thread Sandbox Lifecycle](sandbox-lifecycle.md) - How an agent thread binds to a hosted sandbox or a local CLI bridge, provisions workspace snapshots, refreshes credentials, and handles reconnection and replacement safely.
