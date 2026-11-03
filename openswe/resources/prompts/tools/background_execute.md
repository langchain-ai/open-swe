Start a long-running, non-interactive sandbox command and return immediately.

Use this for tests, builds, and waits.
Do not use it for commands that edit files concurrently with the agent, installs, commits, or pushes.
Completion wakes this thread with the result: keep doing other useful work, or end your turn if none remains.
Never `sleep`, poll, or tail its output to wait for it.
Output is capped and saved in the sandbox.
