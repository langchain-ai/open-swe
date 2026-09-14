List the findings the reviewer published for this PR.

Use this to ground answers about the review — what was flagged, the
severity/confidence, and any resolution notes. Prefer quoting these over
re-deriving issues from the diff.

Args:
    status_filter: One of ``open``, ``resolved``, ``dismissed``. ``None``
        (default) returns findings of every status.

Returns:
    ``{findings, count}``; ``{findings: [], count: 0, error}`` on failure.
