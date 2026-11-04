Reorder the requester's chats within one sidebar folder, persisting their personal order across devices without changing anybody else's sidebar or any thread metadata.

Pass `thread_ids` in the desired order and choose exactly one folder: `project` (project ID), `repo` (owner/repository), or `ownerless=true` (no repository/project).
`project="none"` is equivalent to `ownerless=true`.
Global recents have no custom order.
Only readable threads belonging to that folder, including readable task coordinators shown through matching workers, are accepted; duplicate IDs and inaccessible projects are rejected.
Omitted previously ordered IDs keep their relative order after the supplied IDs.
New chats created after the last arrangement appear first; older threads without a saved position follow the saved arrangement in activity order.
Deleted, moved, or inaccessible threads are filtered out when listing the folder.

Only a verified triggering person can reorder their own sidebar; automation runs cannot do so.
This does not move, pin, resolve, share, or change access to any thread.
