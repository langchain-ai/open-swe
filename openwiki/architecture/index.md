# Files

- [Coding agent assembly](agent-graph.md) - How the main Deep Agents coding graph is constructed for an executable thread and how each run prepares sandbox, context, tools, skills, and model behavior.
- [Agent middleware stack](middleware-stack.md) - Intentional middleware ordering for the coding, reviewer, analyzer, and PR chat agent graphs, including preparation, policy, model and tool failure boundaries, timeouts, fallback, and completion hooks.
- [Runtime architecture and service composition](overview.md) - LangGraph deployment architecture, FastAPI ingress, graph entrypoints, durable execution, and dashboard and desktop boundaries.
- [Review and review-style graphs](reviewer-and-analyzer.md) - The read-only reviewer prepares a regenerated PR checkout, manages durable findings, and publishes GitHub review comments. A separate analyzer learns repository-specific review guidance, while PR chat answers questions without a sandbox or code-mutation capability.
- [Sandbox binding and recovery lifecycle](sandbox-lifecycle.md) - How Open SWE binds one durable sandbox identity to a thread, lazily reconnects async backends, selects creation configuration, and recovers safely. It also covers proxy credential refresh, explicit rebinding, and the crucial distinction between deleted and unreachable sandboxes.
