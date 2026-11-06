Link a memory to a trigger, or unlink it.

A linked memory leaves its person's context, which shows the trigger's summary instead, until no trigger links it.
A memory may have several triggers, and a trigger several memories.
Held memories cannot be linked.

Args:
- slug: The trigger's slug, named first with `set_memory_trigger`.
- memory_id: The memory's id.
- linked: `true` (the default) to link it, `false` to unlink it.
