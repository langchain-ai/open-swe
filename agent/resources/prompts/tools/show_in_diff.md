Scroll the user's review page to a range of diff lines and briefly pulse them.

Use this whenever you point at specific code, so the user sees it instead of
hunting for it. To show a whole hunk, pass the hunk's first and last line. Call
it once per location, in the order you discuss them.

Args:
    file: Path of a file in the diff, as it appears in `/pr/diff.patch`.
    start_line: First line of the range.
    end_line: Last line of the range; defaults to `start_line`.
    side: `RIGHT` for new-file line numbers (added or unchanged lines), `LEFT`
        for old-file line numbers (removed lines).

Returns:
    `{shown: true, range}`, or `{shown: false, error}` when the range is invalid.
