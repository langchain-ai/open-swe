Rebind this thread to a fresh sandbox.

``source`` selects what the new sandbox boots from:

- ``workspace`` (default): the thread's workspace snapshot, with its repositories
  already checked out. On a private admin surface, pass ``workspace=<slug>`` (a
  slug from ``list_workspaces``) to boot a different workspace's snapshot
  instead; other threads are refused.
- ``base``: the deployment's base snapshot with no workspace content — the same
  sandbox a thread without a workspace would get. Use this when the user asks
  for an empty or clean sandbox.

Either way the new sandbox has none of the thread's current files or worktree
state. First it attempts to stop the old sandbox, waiting at most 10 seconds;
stop failure does not prevent creating the new sandbox. The old sandbox is not
deleted, but it becomes inaccessible from this thread after the handoff.

Returns ``success``, ``old_sandbox_id``, ``new_sandbox_id``,
``old_sandbox_stopped``, and ``old_sandbox_stop_error`` on success. Report the
stop outcome to the user, including any failure, alongside the recreation result.
