Rebind this thread to a fresh sandbox.

``source`` selects what the new sandbox boots from:

- ``workspace`` (default): the trusted execution profile selected for this replacement.
  With **Use Robin-wide Defaults** enabled, it uses the same Robin-wide defaults for every environment; passing a legacy workspace does not select different execution configuration.
  With the toggle off, it uses the thread's legacy workspace snapshot.
  On a private admin surface, pass ``workspace=<slug>`` (from ``list_workspaces``) to request a different legacy image; other threads are refused.
  Cloud environment selectors are removed.
  The replacement's configuration is pinned when requested, not reselected from mutable thread metadata.
- ``base``: the deployment's base snapshot with no workspace content — the same sandbox a thread without a workspace would get.
  Use this when the user asks for an empty or clean sandbox.

Either way the new sandbox has none of the thread's current files or worktree state.
First it attempts to stop the old sandbox, waiting at most 10 seconds; stop failure does not prevent creating the new sandbox.
The old sandbox is not deleted, but it becomes inaccessible from this thread after the handoff.

Returns ``success``, ``old_sandbox_id``, ``new_sandbox_id``, ``old_sandbox_stopped``, and ``old_sandbox_stop_error`` on success.
Report the stop outcome to the user, including any failure, alongside the recreation result.

A thread whose sandbox predates Managed Deep Agents gets no ``new_sandbox_id`` but ``handed_to_mda: true``: this run has no sandbox anymore, and the thread's next run starts in the fresh one.
Tell the user, then end your turn.
