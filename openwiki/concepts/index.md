# Files

- [Authentication, Authorization, and Secret Boundaries](auth-and-security.md) - How Open SWE authenticates dashboard and automation users, resolves GitHub authority, verifies inbound requests, encrypts stored credentials, and keeps secrets out of sandboxes.
- [Models, Profiles, and Instructions](models-profiles-instructions.md) - Model and reasoning selection, fallback, gateway construction, and the team, profile, and thread layers that govern agent runs. Explains how repository, environment, and sender instructions are persisted and placed into prompts.
- [Threads, Durable Runs, and State](threads-and-state.md) - How Open SWE identifies durable LangGraph conversations, constructs follow-up inputs, owns thread metadata and Store records, and preserves sandbox continuity across product surfaces.
- [Tool Ecosystem and Dynamic Loading](tools.md) - How tools are defined, registered, dynamically loaded, and exposed to agents; tool categories (sandbox, integration, review, etc), authorization models, and mode-specific controls.
