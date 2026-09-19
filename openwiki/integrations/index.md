# Files

- [Dashboard, Web UI, and Desktop Clients](dashboard-ui.md) - Dashboard API ownership, session and thread boundaries, web serving and proxying, and the Electron-supervised local-agent execution model.
- [MCP, Connected Tools, and Observability Integrations](observability-and-mcp.md) - How OpenSWE loads credential-scoped MCP and Notion tools, stores and protects connection credentials, and optionally routes model calls through the LangSmith LLM Gateway.
- [Sandbox Provider Integration Contract](sandbox-providers.md) - Contract for selecting, creating, reconnecting, and operating isolated execution backends. Covers the registry, LangSmith-specific workspace snapshots and proxy authentication, provider capabilities, lifecycle safety, and extension requirements.
