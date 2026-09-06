# Files

- [Authorization, credentials, and safety controls](auth-and-security.md) - Trust boundaries for inbound triggers, dashboard and CI authentication, GitHub credentials, local execution, repository access, and high-impact agent mutations. Explains fail-closed verification, least-privilege credential handling, and explicit approval controls.
- [Model, profile, and instruction resolution](models-profiles-instructions.md) - Explains how deployment, team, profile, explicit-run, and persisted-thread settings resolve into agent models, reasoning effort, gateway routing, fallbacks, and prompts. Covers instruction scopes, plan mode, storage behavior, and failure handling.
- [Threads, run state, and durable records](threads-and-state.md) - Defines how Open SWE identifies LangGraph conversations, separates checkpoints, thread metadata, and Store records, and preserves sandbox and run continuity across triggers.
- [Tool capability model](tools.md) - How Open SWE composes Deep Agents primitives, curated tools, and integrations into graph-specific capability surfaces, and where sandbox, authorization, and plan-mode boundaries are enforced.
