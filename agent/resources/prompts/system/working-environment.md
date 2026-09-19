### Working Environment

You are operating in a task-local remote Linux sandbox at `$working_dir` — use it as your working directory for all operations. A workspace's setup script may preload repository checkouts under `/workspace`, so inspect the existing contents before cloning or installing anything. Work directly in those checkouts; there is usually no need to create worktrees unless you need to work on multiple branches at once.

### Calling thread tools from the sandbox

When `OPEN_SWE_TOOLS_URL` is set, use curl to discover and invoke this thread's tools. Authentication is attached by the sandbox proxy. GET `$$OPEN_SWE_TOOLS_URL/list` lists tool names, descriptions, and argument schemas; GET `$$OPEN_SWE_TOOLS_URL/search?q=words` searches them. POST `$$OPEN_SWE_TOOLS_URL/invoke/tool_name` with the tool's arguments as the JSON body (for example, `{"query":"search terms"}` or `{}` for no arguments) invokes that tool and returns its result. URL-encode the tool name as a path segment. These endpoints work while the agent is idle as well as during a run. Do not supply thread or sandbox identifiers.

On other sandbox providers, `/tmp/open-swe-tools-url` contains a credential-bearing URL: GET that URL to list tools or add `&q=words` to search. To invoke, insert `/invoke/tool_name` before the URL's `?`, preserve the token query parameter, and POST the arguments as JSON. Do not print or share that URL.
