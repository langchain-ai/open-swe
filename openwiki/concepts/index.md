# Files

- [Authentication, Authorization, and Security Boundaries](auth-and-security.md) - Explains dashboard GitHub OAuth sessions, credential selection and storage, repository and actor authorization, inbound webhook verification, and the boundary that keeps GitHub credentials out of sandboxes.
- [Models, Profiles, and Instructions](models-profiles-instructions.md) - Explains how agent model and reasoning choices are validated, resolved, persisted per thread, routed adaptively, and constructed. Covers team defaults, user profiles, runtime fallbacks, and repository, environment, and sender instruction context.
- [Threads, Invocations, and Durable State](threads-and-state.md) - Defines how Open SWE identifies durable LangGraph conversations and executions, separates checkpoints, metadata, Store records, and analytics, and enforces ownership and visibility boundaries.
- [Tool and Skill Capability Model](tools.md) - How the Open SWE Deep Agent acquires curated tools, deferred MCP integrations, skills, and storage backends, and how source, credential, administrative, incident, and plan-mode gates limit those capabilities.
