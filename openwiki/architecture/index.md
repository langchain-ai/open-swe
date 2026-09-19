# Files

- [Coding Agent Assembly](agent-graph.md) - How an executable coding-thread run resolves durable configuration, backend, models, prompts, skills, tools, subagents, and middleware into a Deep Agents graph.
- [Agent Middleware, Limits, and Failure Semantics](middleware-stack.md) - Ordering-sensitive middleware surrounding the coding-agent and reviewer tool and model loops. Covers per-run preparation, routing, queues, safety policy, retries, timeouts, completion, and observability.
- [Runtime Architecture and Service Composition](overview.md) - How Open SWE composes LangGraph graph entrypoints, FastAPI ingress, durable run dispatch, PostgreSQL-backed services, and cloud and desktop user interfaces.
- [Pull Request Reviewer and Style Analyzer](reviewer-and-analyzer.md) - Architecture of the read-only pull-request reviewer and the repository-specific style analyzer. Covers durable findings, re-review and publication behavior, and bootstrap and continual style-learning operations.
- [Thread Sandbox Lifecycle](sandbox-lifecycle.md) - How a normal agent thread acquires, persists, reconnects to, and deliberately replaces its sandbox. Covers workspace-backed provisioning, stable proxy handles, credential injection, recovery safety, and reviewer checkout preparation.
