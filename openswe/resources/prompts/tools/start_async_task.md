Launch an Open SWE worker in the background for an independent assignment using `subagent_type="general-purpose"`. The worker shares this thread's sandbox and reports back automatically through task messages.

After successful launches, briefly describe the delegated work and whether you are continuing other work or waiting for results. In Slack replies, omit internal task and worker/subagent UUIDs unless explicitly requested or necessary for actionable troubleshooting; do not report an ID merely because this tool returned it.

Use `task_status`, `message_task_thread`, and `control_worker` to coordinate workers. Do not use `check_async_task`, `update_async_task`, `cancel_async_task`, or `list_async_tasks`. Do not immediately poll for completion; results arrive automatically. Continue useful work when available, or end your turn while waiting.
