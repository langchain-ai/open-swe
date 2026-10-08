Update fields on an existing finding.

Use this on a re-review run to mark an existing finding as resolved or
dismissed, or to revise its severity/description/suggestion if the new
commits changed the situation.

Args:
    finding_id: The id returned by ``add_finding`` (or shown in the
        ``Existing findings`` block of the re-review user message).
    status: New status (``open``, ``resolved``, ``dismissed``).
        Use ``resolved`` when the new commits address the issue. Resolving
        or dismissing requires a ``note`` with the full message to post.
    severity: New severity, if reassessing.
    confidence: New confidence rating (``low``, ``medium``, ``high``), if
        new commits change how sure you are the finding is a real issue.
    title: New concise generated headline, if revising.
    description: New description body, if revising. Do not repeat ``title``
        as the first line.
    suggestion: New replacement text. Pass an empty string to clear it.
        Capped at 4 lines — longer values are dropped (the finding keeps
        its description). Only set this for small, obvious fixes.
    note: Optional free-form note explaining the change. Required when
        resolving or dismissing because it is posted verbatim as the full
        GitHub reply body.

Returns:
    Dictionary with ``success`` and (on success) the updated ``finding``.
