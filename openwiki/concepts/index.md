# Files

- [Authentication, Authorization, and Credential Scope](auth-and-security.md) - How dashboard identity, GitHub credentials, inbound webhook verification, and sandbox proxy credentials are isolated and authorized. It distinguishes browser sessions, user and App GitHub authority, and workspace-scoped sandbox access.
- [Model, Profile, and Instruction Resolution](models-profiles-instructions.md) - Explains how workspace defaults, profile preferences, durable thread snapshots, and per-run choices resolve to usable models. Covers provider routing and fallback plus the instruction layers assembled for an agent run.
- [Threads, Invocations, and Durable State](threads-and-state.md) - Defines durable conversation identity, per-run invocation identity, LangGraph checkpoints and metadata, and the ownership of state shared across Open SWE surfaces.
- [Tool Surfaces and Capability Gating](tools.md) - How Open SWE selects static, connected-service, sandbox, Slack, administrative, workspace, and specialist-agent tools from trusted run context. Covers the independent authorization and invocation gates that bound each capability surface.
