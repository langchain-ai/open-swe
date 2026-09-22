Start a long-running, non-interactive sandbox command and return immediately.

Use this for tests, builds, and waits while useful foreground work remains. Do not use it
for commands that edit files concurrently with the agent, installs, commits, or pushes.
Completion is delivered automatically; do not poll with repeated
`background_task(action="status", task_id=...)` calls. If the result is needed
in the current turn, use `background_task(action="wait", task_id=..., timeout=...)`;
otherwise end the turn and rely on the completion notification. Output is capped
and saved in the sandbox.
