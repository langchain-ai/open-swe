Link a personal memory to a trigger, or unlink it.

A linked memory leaves its person's context, which shows the trigger's summary instead, until no trigger links it.
A memory may have several triggers, and a trigger several memories.
Link a memory once, at the most specific path that holds all of it: runs working at that path or below it recall it with the paths above theirs.
Held memories and workspace-scoped ones cannot be linked: write what a workspace-scoped one teaches as the trigger's guidance instead.

Args:
- path: The trigger's path, named first with `set_memory_trigger`.
- memory_id: The memory's id.
- linked: `true` (the default) to link it, `false` to unlink it.
