# Files

- [Dashboard, Web UI, and Desktop Clients](dashboard-ui.md) - The dashboard API contract, same-origin web proxy, and Electron local-agent boundary. Covers authentication, thread access, settings domains, and supervised local execution.
- [Observability, MCP, and Connected Tools](observability-and-mcp.md) - How the agent traces graphs and optionally routes models through LangSmith, and how it loads, authorizes, and safely executes Datadog, LangSmith, Corridor, Notion, Currents, and browser tools.
- [Sandbox Provider Integrations](sandbox-providers.md) - Compare Open SWE's registered sandbox backends, their creation and reconnection behavior, and provider-specific operational constraints. Covers the thread-bound lifecycle, LangSmith provisioning and proxy behavior, and the extension contract.
