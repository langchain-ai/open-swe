Drop a memory the triggering person saved.

Call this when they ask you to forget, stop following, or take back something they asked you to remember.

- `scope: "personal"` deletes one of their personal memories at once.
  Their personal memories are listed in their `person` block, each ending with its memory id.
  To change one, forget it and `remember` the new wording.
- `scope: "workspace"` suggests withdrawing a workspace memory they saved, by the `memory_id` `remember` returned.
  The workspace's curator decides at its next wake, and may remove what it said from the workspace's prompt even after adopting it; other people's memories may still keep it there.
  One held from a private thread, which the curator never saw, is deleted at once.

Args:
- memory_id: The memory's id.
- scope: `"personal"` or `"workspace"`.

Returns `{"ok": true, "memory_id": str, "status": str}`, or `{"ok": false, "error": str}` when they saved no such memory.
