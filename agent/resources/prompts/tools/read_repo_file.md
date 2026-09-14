Read a file (or list a directory) from the PR's repository at a git ref.

Use this to inspect code beyond the diff — callers, definitions, neighboring
modules, config — at the exact commit under review. The diff itself is
already available as the virtual file ``/pr/diff.patch``.

Args:
    path: Repo-relative path, e.g. ``src/app/main.py`` or ``src/app`` for a
        directory listing. Leading slashes are ignored.
    ref: Git ref (branch, tag, or SHA). Defaults to the PR head commit.

Returns:
    For a file: ``{success, path, ref, content, truncated}``.
    For a directory: ``{success, path, ref, entries}`` where each entry is
    ``{name, type, path}``.
    On failure: ``{success: False, error}``.
