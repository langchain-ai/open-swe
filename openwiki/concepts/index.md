# Files

- [Identity, Credentials, and Security Boundaries](auth-and-security.md) - Dashboard and integration authentication, credential scope, webhook verification, audit records, and desktop trust boundaries in Open SWE.
- [Models, Profiles, Instructions, and Prompts](models-profiles-instructions.md) - How Open SWE resolves workspace, profile, thread, and per-run model choices; constructs provider clients; and layers persisted instructions, prompt resources, and skills into agent behavior.
- [Threads, Durable Runs, Workspaces, and Task State](threads-and-state.md) - Defines durable thread and run identity, the boundary between LangGraph and PostgreSQL state, workspace routing, and coordinator-worker task ownership. Explains the defaults and failure semantics that preserve continuity across Open SWE entrypoints.
- [Tool Surfaces, Dynamic Loading, and Authorization](tools.md) - How Open SWE assembles graph-specific tool surfaces, defers MCP schemas, routes sandbox-originated calls, and rechecks authorization at execution time. Use this page when adding, exposing, or restricting a model capability.
