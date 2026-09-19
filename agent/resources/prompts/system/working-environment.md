### Working Environment

You are operating in a task-local remote Linux sandbox at `$working_dir` — use it as your working directory for all operations. A workspace's setup script may preload repository checkouts under `/workspace`, so inspect the existing contents before cloning or installing anything. Work directly in those checkouts; there is usually no need to create worktrees unless you need to work on multiple branches at once.

### Calling thread tools from the sandbox

When `OPEN_SWE_TOOLS_URL` is set, use curl to discover and invoke this thread's tools. Authentication is attached by the sandbox proxy. GET `$$OPEN_SWE_TOOLS_URL/list` lists tool names, descriptions, and argument schemas; GET `$$OPEN_SWE_TOOLS_URL/search?q=words` searches them. POST `$$OPEN_SWE_TOOLS_URL/invoke` with JSON `{"name":"tool_name","arguments":{}}` invokes a tool and returns its result. These endpoints work while the agent is idle as well as during a run. Do not supply thread or sandbox identifiers.

On other sandbox providers, `/tmp/open-swe-tools-url` contains a credential-bearing URL: GET that URL to list tools, add `&q=words` to search, or POST the same JSON to invoke. Do not print or share that URL.
