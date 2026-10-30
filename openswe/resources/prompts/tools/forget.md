Delete one of the triggering person's personal memories.

Call this when they ask you to forget or stop following something they asked you to remember.
Their personal memories are listed in their `person` block, each ending with its memory id.
To change one, forget it and `remember` the new wording.

Args:
- memory_id: The id shown after the memory.
- scope: `"personal"`, the only scope you can forget: a workspace memory is the curator's to decide.

Returns `{"ok": true, "memory_id": str}`, or `{"ok": false, "error": str}` when they have no personal memory with that id.
