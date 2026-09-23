Draft a GitHub review comment on diff lines for the user to post as themselves.

Nothing is posted by this call. The user sees the draft in the chat, can edit
it, and chooses whether to post it. Only draft a comment when the user asks you
to comment, or agrees to your offer to. Write the body as the user would, in the
first person, without mentioning that you drafted it.

Args:
    file: Path of a file in the diff, as it appears in `/pr/diff.patch`.
    line: The line the comment attaches to; for a range, its last line.
    body: Comment text in GitHub Markdown.
    start_line: First line of a multi-line range; omit for a single line.
    side: `RIGHT` for new-file line numbers (added or unchanged lines), `LEFT`
        for old-file line numbers (removed lines).

Returns:
    `{proposed: true, range, body}`, or `{proposed: false, error}` when the
    input is invalid. The lines must be part of the diff or GitHub rejects the
    comment when the user posts it.
