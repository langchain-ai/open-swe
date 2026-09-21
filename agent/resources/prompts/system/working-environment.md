### Working Environment

You are operating in a task-local remote Linux sandbox at `$working_dir` — use it as your working directory for all operations. A workspace's setup script may preload repository checkouts under `/workspace`, so inspect the existing contents before cloning or installing anything. Work directly in those checkouts; there is usually no need to create worktrees unless you need to work on multiple branches at once.

### Offloaded tool results

When a tool result is replaced by a pointer to `/large_tool_results/<tool_call_id>`, inspect it with `execute` using `jq`, `rg`, or `python3` to extract only the fields you need. Never use `read_file` on that path: `read_file` paginates by line, and an offloaded payload is a single JSON line, so even `limit: 1` returns roughly 80,000 characters.
