Delete an environment and its snapshot.

Deleting ``default`` sends runs back to the per-repo and base snapshots.
Confirm with the user first: the snapshot cannot be recovered, only rebuilt.

Args:
    name: Environment to delete.

Returns:
    ``{"ok": True, "deleted": bool}``.
