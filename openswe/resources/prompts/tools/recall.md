Load what triggers hold back at a path and every path above it: the workspace's guidance, and the triggering person's own memories.

Triggers are named by `/`-separated paths from general to specific, such as `repo/org/mono/api`.
The workspace's are listed under "Workspace memory triggers" in your instructions; a person's own under `personal_memory_triggers` in their `person` block, each as `path: when it applies`.
When one matches what you are doing, call this before you act with the most specific path you work in.
`repo/org/mono/api` returns what is at `repo/org/mono/api`, `repo/org/mono`, `repo/org` and `repo`, and nothing from sibling paths such as `repo/org/mono/web`.
The path need not have a trigger of its own.

What comes back keeps its provenance:
- `workspace`: what the workspace's curator wrote for everyone, most general path first; follow it like the workspace's prompt.
- `personal`: the triggering person's own memories, newest first; follow them like their `personal_memories`.
  Memories saved in private threads are returned only in private threads, never in shared threads.

Args:
- path: The path, such as `repo/org/mono/api`.

Returns `{"ok": true, "path": str, "workspace": [{"path": str, "guidance": str}], "personal": [str]}`, each personal memory ending with the id `forget` takes, or `{"ok": false, "error": str}`.
