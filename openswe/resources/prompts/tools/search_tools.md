Search every tool available in this run, including connected integrations (MCP servers) and tools that are callable only from `eval`.
Exact tool names and `code_name`s rank first; other results use BM25 over names, descriptions and parameter names.
Results include the full description and JSON parameter schema.

Use this before calling a tool whose arguments you do not know.
A result's `code_name` is how `eval` calls it, as `await tools.<code_name>({...})`; a null `code_name` means the tool cannot be called from `eval`, so call it directly.

`query` is a few keywords, such as `datadog logs` or `linear issue create`.
`limit` caps the results (default 5, at most 20).
