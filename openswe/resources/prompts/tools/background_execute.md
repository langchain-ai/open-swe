Start a long-running, non-interactive sandbox command and return immediately.

Use this for tests, builds, and waits while useful foreground work remains. Do not use it
for commands that edit files concurrently with the agent, installs, commits, or pushes.
Completion is delivered automatically; do not poll. Output is capped and saved in the sandbox.
