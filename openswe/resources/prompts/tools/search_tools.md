Search every tool available in this run, including connected integrations (MCP servers) and tools that are callable only from `eval`.
It returns up to 10 matches: the best 3 with their full description and JSON parameter schema, the rest with a one-line description; search a tool's exact name for its schema.
A tool whose exact name or `code_name` is the query comes first; after that, matches on a tool's name rank above matches in its description or parameter names.

Use this before calling a tool whose arguments you do not know.
A result's `code_name` is how `eval` calls it, as `await tools.<code_name>({...})`; a null `code_name` means the tool cannot be called from `eval`, so call it directly.

`query` is a few keywords, such as `datadog logs` or `linear issue create`, or a tool's exact name.
Prefix a word with `+` to keep only tools whose name contains it, such as `+linear issue create`; when nothing matches, the result says so.
