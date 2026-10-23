# Files

- [Dashboard, desktop, and CLI client surfaces](dashboard-ui.md) - How the browser dashboard, Electron desktop client, and oswe CLI authenticate to Open SWE, proxy and stream thread operations, and use sandbox bridges to execute a cloud agent against a local checkout.
- [GitHub, Slack, and Linear integrations](github-slack-and-linear.md) - How Open SWE authenticates and filters GitHub, Slack, and Linear webhooks, resolves identities and workspaces, creates agent runs, and returns notifications to their originating surfaces.
- [MCP, model gateways, and observability integrations](observability-and-mcp.md) - How Open SWE resolves scoped MCP connections, protects credentials, routes eligible model calls through the LangSmith LLM Gateway, and records tracing and product-usage signals without making optional integrations run-critical.
- [Sandbox providers and local bridge backends](sandbox-providers.md) - How Open SWE selects sandbox providers, provisions LangSmith workspace images, and distinguishes provider-managed, MDA-managed, and user-owned local bridge backends.
