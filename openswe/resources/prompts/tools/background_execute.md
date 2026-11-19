Start a long-running, non-interactive sandbox command and return immediately.

Use this for tests, builds, and waits.
It shares the worktree: don't start edits, installs, commits, or pushes with it, and don't make them yourself while it runs.
Completion wakes this thread with the result: keep doing other useful work, or end your turn if none remains.
Never `sleep`, poll, or tail its output to wait for it.
Output is capped and saved in the sandbox.
