# Files

- [Identity, authorization, and credential scope](auth-and-security.md) - How Open SWE authenticates dashboard, API, workflow, and webhook callers; limits personal and GitHub App credentials; and keeps sandbox access scoped and opaque.
- [Models, profiles, instructions, and prompts](models-profiles-instructions.md) - How Open SWE resolves workspace and thread model choices, applies routing and provider construction, and composes workspace, repository, and personal instructions into an agent run.
- [Threads, runs, messages, and artifacts](threads-and-state.md) - How Open SWE gives conversations stable identities, dispatches durable LangGraph runs, presents thread state, and stores plans, workspace files, queued follow-ups, and transcripts.
- [Tool surfaces and dynamic capability policy](tools.md) - How Open SWE assembles graph-specific tool surfaces, hands selected calls to clients or sandboxes, and protects tools through access policy, approval guards, and normalized failures.
