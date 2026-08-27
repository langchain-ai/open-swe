# Files

- [Agent Graph & get_agent Factory](agent-graph.md) - How the main Open SWE coding-agent graph is assembled per-thread by get_agent, resolving GitHub identity, sandbox, model and effort, curated tools, a composite backend, subagents, and the middleware stack around create_deep_agent.
- [Middleware Stack](middleware-stack.md) - The ordered LangChain/Deep Agents middleware chain that wraps every model and tool call for the Open SWE agent and reviewer, and what each hook does.
- [System Architecture Overview](overview.md) - High-level map of the Open SWE runtime - the five LangGraph graphs, the FastAPI webapp and dashboard router, the per-thread sandbox layer, and the web/desktop UI, and how invocation surfaces flow through them.
- [Reviewer & Review-Style Analyzer Graphs](reviewer-and-analyzer.md) - How the read-only reviewer graph reviews one PR through a durable findings model and how the analyzer graph learns a per-repo review style in bootstrap and nightly continual modes.
- [Sandbox Lifecycle & Providers](sandbox-lifecycle.md) - How each thread is bound to a per-thread sandbox through a get-or-create-then-reconnect lifecycle, how the SANDBOX_TYPE provider is selected, how the LangSmith GitHub proxy is configured, and how unreachable versus deleted sandboxes are handled.
