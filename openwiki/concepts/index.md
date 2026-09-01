# Files

- [Authentication, Authorization & Security Boundaries](auth-and-security.md) - How Open SWE authenticates GitHub in dual-mode (per-user OAuth vs. GitHub App installation token), verifies inbound webhooks, protects dashboard sessions, encrypts credentials at rest, and gates admin/observability/MCP tools against attacker-influenced content.
- [Models, Profiles, Team Defaults & Instructions](models-profiles-instructions.md) - How the agent validates and resolves model and reasoning-effort choices across team settings, profiles, and thread snapshots, then constructs provider-specific chat models. It also describes dashboard option enrichment and the scope and precedence of repository and sender instructions.
- [Threads, Thread IDs & Persistence](threads-and-state.md) - How Open SWE derives deterministic LangGraph thread ids per surface, persists per-thread state and settings, checkpoints runs durably, and keys Slack code-channel sessions so follow-ups route back to the same run.
- [Agent Tools (Curated Toolset)](tools.md) - Map of Open SWE's curated tool exports, graph-specific and runtime-conditional tool surfaces, deferred integrations, and plan-mode safety controls.
