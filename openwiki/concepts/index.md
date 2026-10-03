# Files

- [Identity, Credentials, and Safety Boundaries](auth-and-security.md) - Authentication and authorization boundaries for dashboard users, inbound integrations, GitHub authority, private credentials, and sandboxed repository work.
- [Models, Profiles, and Instruction Resolution](models-profiles-instructions.md) - Explains how workspace, profile, thread, and request settings select models and reasoning effort, including routing, fallbacks, and provider construction. Describes the persistence and authority boundaries for repository and user instructions.
- [Threads, Durable Runs, and State](threads-and-state.md) - How Open SWE derives durable conversation identities, turns activity into attributed LangGraph runs, separates checkpoints from metadata and Store data, and preserves sandbox continuity.
- [Tool Surfaces and Dynamic Availability](tools.md) - How agent graphs compose curated, built-in, MCP, and client-provided tools, then restrict them by trusted run context and per-call authorization. Covers deferred integration schemas, read-only boundaries, and recoverable tool failures.
