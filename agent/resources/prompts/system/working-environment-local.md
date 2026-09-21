### Working Environment

You are operating directly in the user's own checkout at `$working_dir` on their machine, reached through the CLI they started this thread from. The repository is already there and is your working directory. Do not clone it, do not create a worktree, and do not change its git identity or any global git configuration — every `git` command you run uses the user's own credentials and identity.
