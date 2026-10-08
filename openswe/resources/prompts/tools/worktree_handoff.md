Move this thread out of the user's own checkout into a new git worktree on a new branch, so net-new work never lands on whatever branch the user has checked out. The desktop app creates the worktree and branch, and every later command, file read, and file write in this thread runs there. The user's checkout is left exactly as it was; uncommitted changes in it are not carried over, so call this before editing any file.

- `branch`: the new branch, such as `open-swe/<short-task-slug>`. If it is already checked out in the user's own checkout or in a worktree Open SWE manages, the thread moves there instead and nothing is created; use this to bring the thread back to the user's checkout. Never move the thread by running `git worktree` or `cd` yourself: the app would not follow.
- `base_ref`: the branch the new one starts from. Defaults to the repository's default branch.
- `start_from_origin`: fetch `origin` and start from its copy of `base_ref` rather than the local one. Defaults to true.

On success it returns the worktree's path: use it for absolute paths from now on, in place of the working directory named earlier. Creating a new worktree fails when the thread already runs in a worktree of its own.
