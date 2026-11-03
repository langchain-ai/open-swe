Add a comment on diff lines to the user's pending GitHub review of the pull request this conversation is about, as the user.

The comment is on GitHub at once, but only the user sees it until they submit their review, which posts every pending comment together; they can edit or delete it before then.
Use it when the user asks you to add, leave or post a review comment.
It works only when the user wrote from the pull request's review page; anywhere else, draft the comment with `propose_review_comment`.
Write the body as the user would, in the first person, without mentioning that you wrote it.
To point something out to the user alone, use `note_on_diff`; to let them vet a comment before it reaches GitHub, use `propose_review_comment`.

Args:
- file: Path of a file in the diff, as it appears in `/pr/diff.patch`.
- line: The line the comment attaches to; for a range, its last line.
- body: Comment text in GitHub Markdown.
- start_line: First line of a multi-line range; omit for a single line.
- side: `RIGHT` for new-file line numbers (added or unchanged lines), `LEFT` for old-file line numbers (removed lines).

Returns: `{added: true, range, body, review_id}`, or `{added: false, error}` when the input is invalid or GitHub refuses it, for instance because the lines are not part of the diff.
Never say the comment was posted or published: it waits in the user's pending review.
