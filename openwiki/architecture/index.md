# Files

- [Agent Graph & get_agent Factory](agent-graph.md) - Per-run assembly of the Open SWE main-agent graph, including execution gating, thread-scoped model and backend selection, tools, subagents, prompt preparation, and ordered middleware.
- [Middleware Stack](middleware-stack.md) - The ordered LangChain and Deep Agents middleware chain around Open SWE agent and reviewer model and tool calls, including failure boundaries, retries, and guardrails.
- [System Architecture Overview](overview.md) - High-level map of the Open SWE runtime - the five LangGraph graphs, the FastAPI webapp and dashboard router, the per-thread sandbox layer, and the web/desktop UI, and how invocation surfaces flow through them.
- [Reviewer & Review-Style Analyzer Graphs](reviewer-and-analyzer.md) - How the read-only reviewer graph reviews one PR through a durable findings model and how the analyzer graph learns a per-repo review style in bootstrap and nightly continual modes.
- [Sandbox Lifecycle & Providers](sandbox-lifecycle.md) - How each thread is bound to a per-thread sandbox through a get-or-create-then-reconnect lifecycle, how the SANDBOX_TYPE provider is selected, how the LangSmith GitHub proxy is configured, and how unreachable versus deleted sandboxes are handled.
