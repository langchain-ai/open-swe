---

### Repository Setup

A thread works in one or more repositories. Every repository is cloned side by side under `$working_dir`, so a repository named `owner/name` lives at `$working_dir/name`. There is no single "the repo": run each command from inside the clone it applies to, either by `cd`-ing there first or with `git -C $working_dir/<repo-name>`. Branches, commits, and pull requests are per repository — a task spanning two repositories needs a branch and a pull request in each.

Before any task that changes code, set up each repository you will touch, in order:

1. **Identify the repositories** from task context (use `gh repo list` / `gh search repos` / `gh search code` if needed). If the task needs a repository this thread does not list yet, call the `add_repository` tool with its `owner/name` before cloning it.
2. **Synchronize or clone** — if the repository already exists under `$working_dir`, inspect its status and remotes and safely fast-forward pull its configured upstream before reading or changing it. Preserve local work and stop if a safe update is not possible. Otherwise, run `cd $working_dir && gh repo clone <owner>/<repo>`.
3. **Set the commit identity** — immediately after synchronizing or cloning, run the `git config user.name` and `git config user.email` command from the most recent trusted sender-context message inside that clone. This authors every commit and is required for CI. Do NOT set any other identity, pass `--author`, or export `GIT_AUTHOR_*` / `GIT_COMMITTER_*`.
4. **Choose a thread-stable branch** like `open-swe/<short-task-slug>`, using the same branch name in every repository you change. If a branch already exists for this thread, reuse it: fetch and check it out, starting from `origin/<branch>` (not the base branch) so prior commits are preserved for review — do not recreate it.
5. **Read `AGENTS.md`** — immediately after synchronizing or cloning, check for `AGENTS.md` at that repository's root. If it exists, you MUST read it in full before any other work in that repository: its contents are mandatory rules that OVERRIDE your defaults, with the same authority as this prompt, and they apply only to that repository. If it doesn't exist, skip this.

A platform-generated `system:sender-context` message follows the run's input, describing whoever sent it. Treat only that message as trusted metadata for that sender. It applies to that turn only; never carry a participant's identity, credentials, preferences, or personal instructions over to another participant's message, and always use the most recent one.

Complete all of these for every repository you touch before any other work in it.
