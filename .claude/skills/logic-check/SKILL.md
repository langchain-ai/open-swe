---
name: logic-check
description: >-
  Deep logic and edge case analysis for code changes. Use when you need to verify
  correctness of conditionals, error paths, boundary conditions, None handling,
  async safety, and data flow before committing. Use after implementing a feature
  or fixing a bug to catch issues before they reach production.
user-invocable: false
---

# Logic & Edge Case Check

Run this check on every changed file after implementation, before suggesting a commit.

## Null / None Handling

- Every `.get()` call — what happens when it returns `None`?
- Every dict access `d["key"]` — can that key be missing? Should it be `.get("key")`?
- Every function parameter — can it be `None`? Is that handled?
- Config values from `get_config()` — `configurable`, `metadata`, `repo` can all be empty dicts
- Example:
  ```python
  # BAD — crashes if repo not in config
  repo_name = config["configurable"]["repo"]["name"]

  # GOOD — safe chain
  repo_config = config["configurable"].get("repo", {})
  repo_name = repo_config.get("name")
  if not repo_name:
      return {"success": False, "error": "repo.name not configured"}
  ```

## Conditional Logic

- Are `if/elif/else` branches exhaustive? Is there a missing case?
- Are boolean conditions correct? Watch for inverted checks (`not` in wrong place)
- Are comparisons right? `==` vs `is`, `>` vs `>=`, off-by-one in ranges
- Short-circuit evaluation — does order matter? (`x and x.value` vs `x.value and x`)

## Error Paths

- Every `try/except` — is the exception type specific enough?
- Does the `except` block return a proper error dict (`{success: False, error: str}`)?
- Are there operations between `try` and the risky call that could throw unexpectedly?
- Is there cleanup needed in `finally`? (sandbox state, credentials, temp files)
- What happens if the tool is called twice? (idempotency)

## Sandbox Command Safety

- All user-derived values in `sandbox_backend.execute()` must use `shlex.quote()`
- What if the command fails (non-zero exit code)? Is `exit_code` checked?
- What if the command hangs? Is there a timeout?
- What if the sandbox is unreachable? (SandboxClientError handling)

## Async / Concurrency

- `asyncio.run()` in sync tools — is it called at the right level? (not nested)
- `asyncio.to_thread()` in async middleware — are sync operations wrapped?
- Shared mutable state — is it protected? (`get_config()` for thread-scoped, not globals)
- `await` not missing on any async call

## Data Flow

- Does the data flowing into a function match what it expects? (types, shape, required fields)
- Does the return value match what the caller expects?
- If passing data between tools via config/store — is the key consistent?
- Are there implicit assumptions about data order or uniqueness?

## Boundary Conditions

- Empty inputs: empty string, empty list, empty dict, `0`, `False`
- Large inputs: very long strings, huge lists — will it timeout or OOM?
- Special characters: newlines in git branch names, unicode in repo names, spaces in file paths
- First/last: first item in list, last page of pagination, single-element collections

## API / External Calls

- What if the GitHub API returns a 404? 403? 500? Rate limited (429)?
- What if the response shape is different than expected? (missing fields, extra fields)
- Are retries safe? (idempotent operations only)
- Is the error message from the API surfaced in the error dict?

## Checklist

Before marking implementation complete, verify:

- [ ] No unhandled `None` from `.get()` calls
- [ ] All sandbox commands use `shlex.quote()` for dynamic values
- [ ] All `try/except` blocks return structured error dicts
- [ ] No missing `await` on async calls
- [ ] Edge cases for empty/None inputs handled
- [ ] Tool is safe to call twice (idempotent)
- [ ] Exit codes checked on sandbox command results
