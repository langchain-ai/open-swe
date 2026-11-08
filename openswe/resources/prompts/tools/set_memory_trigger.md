Name a memory trigger, or change an existing one's summary.

A trigger holds what matters only in one situation: guidance for every thread of the workspace (`set_memory_trigger_guidance`), and personal memories linked to it (`link_memory_trigger`), which leave their person's context.
Runs read only the trigger's summary, and load what it holds with `recall(path)` when it applies.
A run's `recall` of a path returns what the triggers at that path and every path above it hold, so name the situation as a path from general to specific.

Args:
- path: 1-100 lowercase letters, digits and `/._-`, a `/`-separated path naming the situation, such as `repo/org/name`, `repo/org/mono/api` or `task/release`.
- summary: One short sentence saying only when its memories apply, such as "Working in `org/name`."
  Never restate the memories themselves.
