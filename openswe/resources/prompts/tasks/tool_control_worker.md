Inspect or cancel one explicitly selected worker in this task.
Only its permanent coordinator may control it.
Use message_task_thread for a new assignment to an existing worker. cancel requests interruption of this worker's pending/running invocations and discards its undelivered events, event subscriptions, and scheduled wakeups.
Cancellation is asynchronous; it does not promise that detached sandbox processes have stopped.
Cancelled workers cannot be relaunched; create a new worker if needed.
