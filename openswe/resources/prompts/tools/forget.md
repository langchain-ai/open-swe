Take back a memory the triggering person saved.

Call this when they ask you to forget, stop following, or take back something they asked you to remember.
Their memories are listed in their `person` block, each ending with its memory id.
To change one, forget it and `remember` the new wording.

It stops reaching their runs at once.
One the curator never saw, held from it, is deleted.
Any other goes back to the workspace's curator as a withdrawal, even if it already decided it: only the curator changes the workspace's prompt, and other people's memories may still keep what it said there.

Args:
- memory_id: The memory's id.

Returns `{"ok": true, "memory_id": str, "status": "deleted" | "requested"}`, or `{"ok": false, "error": str}` when they saved no such memory.
