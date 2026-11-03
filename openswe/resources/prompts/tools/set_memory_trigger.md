Name a memory trigger, or change an existing one's summary.

A trigger moves the personal memories linked to it (`link_memory_trigger`) out of their person's context: their runs read only the trigger's summary, and load the memories with `recall(slug)` when it applies.
Use one for memories that matter only in a narrow situation, so they stop costing every run.

Args:
- slug: 1-100 lowercase letters, digits and `:/._-`, naming the situation, such as `repo:org/name` or `task:release`.
- summary: One short sentence saying only when its memories apply, such as "Working in `org/name`." Never restate the memories themselves.
