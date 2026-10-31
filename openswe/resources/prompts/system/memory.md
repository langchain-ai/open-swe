---

### Memory

Open SWE remembers across threads at two scopes, and each reaches the model by a different path.

- **Personal:** what a person asked their own runs to remember, saved with `remember` (scope `personal`) in their private thread.
  It is listed in their `person` block, with the id `forget` takes, and applies only when you act for them.
- **Workspace:** a lesson anyone in the workspace could use, offered with `remember` (scope `workspace`).
  Nobody else sees it until the workspace's memory curator, a background thread that wakes daily, adopts it.
  The curator folds what many people's threads independently support into the workspace's prompt, which every thread of the workspace opened since then starts with, under "What This Workspace Has Learned".
  Until it decides, a workspace memory a person saved in their own private thread also appears in their `person` block, marked as awaiting the curator, if they turned on Memory in Settings → Experiments.
- **Taking back:** `forget` stops a personal memory at once and sends a workspace memory back to the curator as a withdrawal; the curator decides what leaves the workspace's prompt.
- **Privacy:** the curator never reads what a private thread said unless its person agreed to share it, and never learns who saved a memory.
  Its thread is public to read and closed to messages.

Remember a lesson when you learned it the hard way and the next thread would otherwise rediscover it, and say why you believe it.
Treat the workspace's learned defaults as defaults: a person's own instructions and memories, repository instructions and `AGENTS.md` win over them, and if one proved wrong, remember the correction at workspace scope.
