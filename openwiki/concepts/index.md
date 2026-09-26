# Files

- [Authorization, Credentials, and Security Boundaries](auth-and-security.md) - How Open SWE gates dashboard identities, scopes GitHub App and personal credentials, authorizes repositories and mutations, and verifies inbound requests without exposing sandbox secrets.
- [Models, Profiles, and Instruction Resolution](models-profiles-instructions.md) - Explains how workspace, profile, thread, and per-run choices resolve into provider models, including adaptive routing and recovery from stale settings. Covers durable instruction sources and how repository, workspace, and personal guidance enters an agent prompt.
- [Threads, Runs, and Durable State](threads-and-state.md) - How Open SWE identifies conversations, creates checkpointed LangGraph runs, separates metadata and Store records, and maintains a durable dashboard transcript across surfaces.
- [Tool Surfaces and Dynamic Loading](tools.md) - How Open SWE constructs least-privilege tool surfaces for its Deep Agents, exposes optional MCP and Notion schemas on demand, and rechecks sensitive authorization at invocation time.
