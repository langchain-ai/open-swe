# Files

- [Authentication, Credentials, and Safety Boundaries](auth-and-security.md) - How Open SWE authenticates dashboard and Agent Server callers, links identities, selects and protects GitHub credentials, verifies webhooks, and enforces trust boundaries.
- [Models, Profiles, and Instructions](models-profiles-instructions.md) - How Open SWE resolves models across deployment, instance, workspace, thread, and run layers, and how it freezes thread choices. Explains profile and instruction persistence and the prompt context assembled for each agent turn.
- [Threads, Persistence, and Run State](threads-and-state.md) - Defines durable thread identity, the PostgreSQL thread index and transcript, runtime-owned checkpoints, and the lifecycle that keeps dashboard-visible state coherent.
- [Tool Surfaces and Capability Gating](tools.md) - How Open SWE assembles graph-specific tool surfaces, loads MCP integrations on demand, and applies thread, sandbox, and caller authorization. Use this page to safely add, expose, or restrict an agent capability.
- [Workspaces, Repository Routing, and Access](workspaces-and-access.md) - Workspaces are the persistent scope for inbound work routing, sandbox definitions, repository and Slack bindings, and workspace settings. This page explains precedence, failure policy, snapshot refresh, and the GitHub credential boundary.
