
### Code tool discovery

All tools in this run's catalog remain callable through `await tools.<code_name>({ ... })` in `eval`. Use `search_tools` to discover tools and retrieve their full descriptions, argument schemas and exact `code_name`. Search before calling a tool whose arguments are not already known. Results are native JavaScript values; use `Promise.all` for independent calls and chain dependent calls within one program.

Argument JSON Schemas for common tools, keyed by `code_name`:
