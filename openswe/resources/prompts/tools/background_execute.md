Start a long-running, non-interactive sandbox command and return immediately.

Prefer this over `execute` for tests, builds, training runs, and commands expected to take longer than ~1–2 minutes.
It shares the worktree: don't start edits, installs, commits, or pushes with it, and don't make them yourself while it runs.
Completion wakes this thread with the result: keep doing other useful work, or end your turn if none remains.
`timeout` kills the command when it ends, even one still making progress, so set it to the longest the command can safely run, with headroom for a slow run.
Before ending your turn on a command that may run longer than 10 minutes, call `schedule_thread_wakeup` for about when it should finish; on that wakeup, if it is still running, check its output with `background_task` and stop it if it is stuck.
Never `sleep`, poll its status files, or tail its output in `execute` to wait for it.
Output is capped and saved in the sandbox.
