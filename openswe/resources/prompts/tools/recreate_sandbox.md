Replace this thread's sandbox only when the user explicitly asks to recreate or reset it.
Never use recreation proactively or as automatic recovery.

`source` chooses the replacement:
- `defaults` (default): the global execution image and current shared creation settings
- `base`: the empty base snapshot, for an explicitly requested empty or clean sandbox

The new sandbox has none of this thread's current files, worktree state, or running processes.
The old sandbox is preserved but becomes inaccessible from the thread after the handoff.
Do not claim recreation preserves unpushed work in the replacement.
