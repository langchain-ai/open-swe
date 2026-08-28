# Files

- [Dashboard API & Web/Desktop UI](dashboard-ui.md) - How the FastAPI dashboard router exposes GitHub OAuth, profiles, admin, review, usage, schedules, and the Agents thread API to the ui/ TanStack-router web app and the experimental Electron desktop wrapper, and how the UI reaches the backend same-origin.
- [Observability & MCP Integrations](observability-and-mcp.md) - Optional server-side integrations — Datadog and LangSmith observability tools, Corridor and Notion MCP, Currents, and the Stagehand browser — and the security model that gates them and keeps credentials out of the sandbox.
- [Sandbox Provider Integrations](sandbox-providers.md) - Reference for the pluggable sandbox providers Open SWE can run agents in, how the SANDBOX_TYPE selector and SANDBOX_FACTORIES registry choose a provider, the env vars each provider needs, and how to add a new provider.
