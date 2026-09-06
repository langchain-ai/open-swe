# Files

- [Dashboard, web UI, and desktop clients](dashboard-ui.md) - The dashboard FastAPI API, same-origin Vite and Nitro proxy boundary, review and thread product APIs, and Electron's supervised local-agent boundary. It explains the authorization and filesystem invariants needed to safely change these clients.
- [Observability, MCP, browser, and connected tools](observability-and-mcp.md) - Optional connected-tool architecture for observability, hosted MCP services, Currents, Notion OAuth, LangSmith gateway-backed models, and sandbox-local Stagehand browser automation. Explains credential boundaries, authorization, lazy loading, and fail-soft behavior.
- [Sandbox provider abstraction](sandbox-providers.md) - How Open SWE selects, provisions, reconnects to, and safely recovers sandbox backends. Covers the provider extension contract, LangSmith-specific snapshots and proxy authentication, and operating limits for every built-in provider.
