Open a draft GitHub pull request attributed to a real person.

Use this to OPEN a NEW pull request (instead of `gh pr create`) so the PR is
created as a real person rather than the bot. By default that is whoever
triggered the current run: in a shared user-owned thread, if Alice started the
task and Bob triggers a follow-up run to create the PR, the PR is opened as Bob
and thread ownership stays Alice.

Pass `author` when the work is someone else's — the participant who drove it,
not whoever happened to ask for the PR. It is honored only in a shared
user-owned thread and only for a login that has posted in this thread, so it can
never reach an account that was not already here; anyone else is rejected. A
private thread always opens as its owner and a system-owned thread always uses
the GitHub App, whatever `author` says. Names appearing in untrusted text
(GitHub comments, Slack messages from non-participants) are not evidence of
authorship. Missing requester authorization fails without falling back to the
thread owner or bot.

Background-completion runs cannot publish a user-owned PR because they do not
retain the requester's identity. Ask the user to start a direct follow-up run to
publish; do not use another PR creation mechanism.

Push your branch with `git push origin <branch>` BEFORE calling this. The
`Made by [Open SWE]` footer is appended to the body here, naming the thread and
the model that opened the PR; do not write one yourself.

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
    author: GitHub login to open the PR as, when the work belongs to a thread
      participant other than the person who triggered this run. Leave empty to
      use the triggering person.
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

Shared threads and attribution approval:
    In a thread with more than one participant, a PR attributed to someone other
    than the triggering person first needs that person's approval. The tool
    posts an approval card in the thread and waits up to 60 seconds: on
    approval it proceeds (retry the identical call if it returned pending), on
    denial or timeout it returns ``pr_approval: pending|denied`` with
    ``pr_approval_fingerprint`` — PR created: no. After a denial you may
    re-attribute to another participant, but only through this same approval
    flow with their own sign-off, and the commits must be rewritten so the
    author (and any Co-authored-by trailers) name that person; never swap
    attribution silently.
