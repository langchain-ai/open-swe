Leave a note on diff lines of the pull request the user is looking at, for their eyes only.

The note appears on its lines in the user's review page and in the chat, signed as Open SWE; it never goes to GitHub and nobody else sees it.
Use it to point the user at code while explaining it, or when they ask you to mark, flag or note something in the diff.
For a comment the user wants on GitHub, use `add_review_comment` instead.

Args:
- file: Path of a file in the diff, as it appears in `/pr/diff.patch`.
- line: The line the note attaches to; for a range, its last line.
- body: Note text in GitHub Markdown, written as you, to the user.
- start_line: First line of a multi-line range; omit for a single line.
- side: `RIGHT` for new-file line numbers (added or unchanged lines), `LEFT` for old-file line numbers (removed lines).

Returns: `{noted: true, range, body}`, or `{noted: false, error}` when the input is invalid.
A note on lines outside the diff is not shown.
