List findings on the reviewer thread, optionally filtered by status.

Most useful on a re-review run to inspect what existed before deciding
which findings the new commits resolved.

Args:
    status_filter: One of ``open``, ``resolved``, ``dismissed``. ``None``
        (default) returns every finding regardless of status.

Returns:
    Dictionary with ``findings`` (list) and ``count`` (int).
