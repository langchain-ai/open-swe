# Files

- [Dashboard, Web UI, and Desktop Integration](dashboard-ui.md) - How the dashboard composes its API, static or proxied web UI, authorization boundary, thread and automation interactions, and Electron-supervised local graph backend.
- [MCP, Connected Services, and Observability](observability-and-mcp.md) - How configured MCP connections and the Notion connected service store credentials, are selected for a run, and safely become dynamic agent tools. Covers scope precedence, OAuth, transport protections, and run eligibility boundaries.
- [Sandbox Provider Integration](sandbox-providers.md) - How Open SWE selects sandbox backends, provisions and reconnects them for threads, and applies LangSmith-specific resource, execution, and proxy behavior. Covers optional provider packages, local and desktop variants, and the boundary for adding a provider.
