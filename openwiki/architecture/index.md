# Files

- [Coding agent assembly and execution](agent-graph.md) - How Open SWE builds the executable Deep Agents coding graph from run configuration, thread and workspace policy, a sandbox or bridge backend, tools, prompt context, and middleware.
- [Agent and reviewer middleware stack](middleware-stack.md) - Ordering-sensitive middleware around Open SWE's coding-agent and reviewer loops. Covers run preparation, context and tool handling, model routing and recovery, policy guards, observability, and completion guarantees.
- [System architecture and runtime boundaries](overview.md) - How Open SWE composes LangGraph graphs, FastAPI service surfaces, durable runs, sandboxes, scheduling, and cloud or desktop user interfaces.
- [Persistence, workspaces, threads, and tasks](persistence-workspaces-and-tasks.md) - Durable-state ownership across LangGraph and PostgreSQL, including workspace routing and snapshots, people and credentials, thread artifacts, task delegation, notifications, and database evolution.
- [Review, scout, and findings architecture](reviewer-and-analyzer.md) - How Open SWE prepares and executes isolated pull-request reviews, builds a durable walkthrough with the review scout, persists and reconciles findings, publishes GitHub reviews, and optionally delegates the model loop to Managed Deep Agents.
- [Sandbox and backend lifecycle](sandbox-lifecycle.md) - How Open SWE acquires, reconnects, publishes, recovers, and replaces a thread's sandbox or local bridge. Covers workspace snapshots, credential proxying, checkout handoff, task sharing, and local alternatives.
