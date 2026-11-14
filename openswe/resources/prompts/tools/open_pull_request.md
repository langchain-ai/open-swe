Open a draft GitHub pull request using the thread’s credential scope.

Use this to OPEN a NEW pull request instead of `gh pr create` so authorship and repository-access checks are enforced.
The PR opens as the thread’s owner; system-owned threads outside Ask channels use the GitHub App.
In Ask channels, use this only after a human explicitly requests implementation or a PR; a bug report alone does not authorize PR creation.
Public Ask-channel PRs open as the authenticated requester using their own linked GitHub authorization, never the App or another participant.
A background completion may finish an existing explicit PR request using that requester’s authenticated run identity; never infer authorization from the thread owner.
Missing authorization fails without falling back to another person or the bot.

Push your branch with `git push origin <branch>` BEFORE calling this.
The `Made by [Robin]` footer is appended to the body here, naming the thread and the model that opened the PR; do not write one yourself.

For everything else — updating an existing PR, marking it ready for review, commenting, reading status — keep using `gh`.
If a PR already exists for the branch, this returns that PR's URL without creating a duplicate; switch to `gh pr edit` for updates.

Args:
- owner: Repository owner/org (e.g. "langchain-ai").
- repo: Repository name (e.g. "open-swe").
- head: The branch with your changes (already pushed to origin).
- base: The branch you want to merge into (e.g. "main").
- title: PR title.
- body: PR description (Markdown).
- draft: Requested draft status.
  The authenticated user's dashboard preference overrides this value for newly created PRs; existing PRs are returned unchanged.
- retitle_thread: Retitle this thread to the new PR's title by default, unless a person has renamed it.
  Set False to preserve its title; returning an existing PR does not retitle the thread.
- resolves_thread: Set True when merging or closing this PR finishes the thread's work, so the thread auto-resolves once every PR it opened is merged or closed.
  Prefer True.
  Use False only when you know more PRs are coming for this thread (a stacked PR, a follow-up you still plan to open) and set True on the last one instead.
  Threads whose PRs never set this stay open until someone resolves them by hand.

Returns: On success: {"success": True, "created": bool, "url": str, "number": int, "author": str}.
``created`` is False when an open PR already existed.
On failure: {"success": False, "error": str}, where ``error`` states what failed and quotes the request, status, headers, and body GitHub actually returned — read it and decide what to do next.

Public Ask-channel PRs use the requester’s linked GitHub authorization and repository access, without falling back to the App, and remain drafts.
A human other than the requester must validate and approve; preserve the repository’s review policy.
