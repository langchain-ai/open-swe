Start a separate Open SWE thread that works on a task in the background, owned by the person you are talking to.

- `title`: a short name for the thread.
- `instructions`: everything the new thread needs, self-contained; it cannot see this conversation.
- `repos`: `owner/name` repositories the task touches. The first is the one its sandbox opens in; the others are listed for it to clone.
- `visibility`: `workspace` (default) is visible to the workspace; `private` only to the person.

Returns `thread_id` and `dashboard_url`. Share the link. Check progress later with `get_thread`, and send follow-ups with `manage_thread`.
