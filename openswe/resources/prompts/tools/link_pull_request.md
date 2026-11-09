Bind an existing GitHub pull request to this thread.

Call this once when the user gives you an existing PR to work on (push commits to, fix CI, address review comments), so the thread tracks it like a PR opened with `open_pull_request`.
Do not call it for PRs you are only asked to review, explain, or inspect.
PRs opened with `open_pull_request` are already bound.

Args:
- pr_url: The PR's GitHub URL (e.g. "https://github.com/langchain-ai/open-swe/pull/123").
- resolves_thread: Set True when merging or closing this PR finishes the thread's work.
- author: For a background completion in an Ask channel, name a thread participant whose GitHub authorization may read this PR.
  Human-triggered Ask runs always use the current requester.

Returns: On success: {"success": True, "url": str, "number": int}.
On failure: {"success": False, "error": str}.
