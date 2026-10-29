Start a separate, top-level Open SWE thread that works on a task in the background, owned by the person you are talking to.
It is its own session in their thread list, not a worker under this conversation.

When the person asks for a "new session", "new thread", "new chat" or "separate task", this is the tool, not `start_async_task`: a worker is part of this conversation's task and reports back here.
If you cannot tell whether they want a separate top-level thread or a worker that reports back here, ask them before starting either.

For PR implementation or fixes, first follow the Task Execution existing-thread lookup and reuse rule.
Start a new task only when no suitable accessible thread is found or the user explicitly requests a separate task; a busy owner is not a reason to create a competing worker.
Dedicated review requests use the review tools instead.

- `title`: a short name for the thread.
- `instructions`: everything the new thread needs, self-contained; it cannot see this conversation.
- `repos`: `owner/name` repositories the task touches.
  The first is the one its sandbox opens in; the others are listed for it to clone.
- `visibility`: `workspace` (default) is visible to the workspace; `private` only to the person.

Returns `thread_id` and `dashboard_url`.
Share the link.
Check progress later with `get_thread`, and send follow-ups with `manage_thread`.
