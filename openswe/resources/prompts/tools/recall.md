Load the triggering person's memories that a trigger holds back.

The curator may link memories to triggers.
A linked memory leaves the person's `personal_memories`; their `person` block lists only its trigger under `memory_triggers`, as `slug: when it applies`.
When a trigger's summary matches what you are doing, call this with its slug before you act, and follow what comes back like any other memory.
One slug may recall several memories, and a memory may sit behind several slugs.
Memories saved in private threads are returned only in private threads, never in shared threads.

Args:
- trigger_slug: The slug, such as `repo:org/name`.

Returns `{"ok": true, "trigger_slug": str, "memories": [str]}`, each memory ending with the id `forget` takes, or `{"ok": false, "error": str}`.
