# Files

- [Dashboard, Web UI, and Desktop Integration](dashboard-ui.md) - The authenticated dashboard API, its thread and review experiences, and the same-origin web and desktop boundaries that protect backend credentials while connecting users to LangGraph.
- [Observability and Connected Tool Integrations](observability-and-mcp.md) - How LangSmith Gateway model routing, generic workspace and personal MCP connections, Notion OAuth, and sandbox browser tools are loaded and scoped. Covers encrypted credential handling, network boundaries, lazy tool loading, and failure behavior.
- [Sandbox Provider Integration](sandbox-providers.md) - How Open SWE selects and operates sandbox providers, binds them safely to threads, and handles LangSmith-specific provisioning, credentials, and execution behavior. Covers provider capabilities, local and desktop exceptions, reviewer preparation, and the extension contract.
