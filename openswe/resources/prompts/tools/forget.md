Take back an engram the triggering person saved.

Call this when they ask you to forget, stop following, or take back something they asked you to remember.
Their engrams are listed in their `person` block, each ending with its engram id.
To change one, forget it and `remember` the new wording.

It stops reaching their runs at once.
One the curator never saw, held from a private thread, is deleted.
Any other goes back to the workspace's curator as a withdrawal, even if it already adopted it: only the curator changes the workspace's prompt, and other people's engrams may still keep what it said there.

Args:
- engram_id: The engram's id.

Returns `{"ok": true, "engram_id": str, "status": "deleted" | "requested"}`, or `{"ok": false, "error": str}` when they saved no such engram.
