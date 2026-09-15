Delete a workspace: its record (repos, Slack channels, prompt, and scripts),
its published snapshot, and its nightly refresh.

Deleting ``default`` sends runs that would have used it back to the per-repo
and base snapshots. Its repositories and Slack channels fall through to
whatever the routing order matches next — usually ``default``, or dropped
entirely for GitHub events when ``OPEN_SWE_UNASSIGNED_REPO_WORKSPACE`` is
``ignore``. Confirm with the user first: the snapshot cannot be recovered,
only rebuilt.

Args:
    name: Workspace to delete.

Returns:
    ``{"ok": True, "deleted": bool}``.
