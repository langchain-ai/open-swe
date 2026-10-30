Take back a memory the triggering person saved.

Call this when they ask you to forget, stop following, or take back something they asked you to remember.

- `scope: "personal"`: one of their personal memories stops reaching their runs at once.
  Their personal memories are listed in their `person` block, each ending with its memory id.
  To change one, forget it and `remember` the new wording.
- `scope: "workspace"`: suggests withdrawing a workspace memory they saved, by the `memory_id` `remember` returned, or the id their `person` block shows for one awaiting the curator.

A memory the curator never saw, held from a private thread, is deleted at once.
Any other goes back to the workspace's curator as a withdrawal, even if it already adopted it: only the curator changes the workspace's prompt, and other people's memories may still keep what it said there.

Args:
- memory_id: The memory's id.
- scope: `"personal"` or `"workspace"`.

Returns `{"ok": true, "memory_id": str, "status": "deleted" | "requested"}`, or `{"ok": false, "error": str}` when they saved no such memory.
