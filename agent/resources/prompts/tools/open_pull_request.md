Open a draft GitHub pull request attributed to the triggering user.

Use this to OPEN a NEW pull request (instead of `gh pr create`) so the PR is
created as the person who triggered the run rather than open-swe[bot]. Push
your branch with `git push origin <branch>` BEFORE calling this.

For everything else — updating an existing PR, marking it ready for review,
commenting, reading status — keep using `gh`. If a PR already
exists for the branch, this returns that PR's URL without creating a
duplicate; switch to `gh pr edit` for updates.

Args:
    owner: Repository owner/org (e.g. "langchain-ai").
    repo: Repository name (e.g. "open-swe").
    head: The branch with your changes (already pushed to origin).
    base: The branch you want to merge into (e.g. "main").
    title: PR title.
    body: PR description (Markdown).
    draft: Requested draft status. The authenticated user's dashboard preference
      overrides this value for newly created PRs; existing PRs are returned unchanged.
    resolves_thread: Set True when merging or closing this PR finishes the
      thread's work, so the thread auto-resolves once every PR it opened is
      merged or closed. Prefer True. Use False only when you know more PRs
      are coming for this thread (a stacked PR, a follow-up you still plan
      to open) and set True on the last one instead. Threads whose PRs never
      set this stay open until someone resolves them by hand.

Returns:
    On success: {"success": True, "created": bool, "url": str, "number": int,
    "author": str}. ``created`` is False when an open PR already existed.
    On failure: {"success": False, "error": str}, where ``error`` states what
    failed and quotes the request, status, headers, and body GitHub actually
    returned — read it and decide what to do next.
