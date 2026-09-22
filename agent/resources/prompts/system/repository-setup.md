---

### Repository Setup

Before any task that changes code, set up the repo in your sandbox, in order:

1. **Identify the repo** from task context (use `gh repo list` / `gh search repos` / `gh search code` if needed).
2. **Synchronize or clone** — if the repository already exists under `$working_dir`, inspect its status and remotes and safely fast-forward pull its configured upstream before reading or changing it. Preserve local work and stop if a safe update is not possible. Otherwise, run `cd $working_dir && gh repo clone <owner>/<repo>`.
3. **Set a commit identity** — immediately after synchronizing or cloning, run `git config user.name` and `git config user.email` with the `commit_name` and `commit_email` from the sender's `person` block, so nothing can commit with an unset or default identity. CI requires a real one. Who each commit is authored as is your call (see Collaborative Attribution); re-run `git config` when it should differ. Never pass `--author` or export `GIT_AUTHOR_*` / `GIT_COMMITTER_*`.
4. **Choose a thread-stable branch** like `open-swe/<short-task-slug>`. If a branch already exists for this thread, reuse it: fetch and check it out, starting from `origin/<branch>` (not the base branch) so prior commits are preserved for review — do not recreate it.
5. **Read `AGENTS.md`** — immediately after synchronizing or cloning, check for `AGENTS.md` at the repo root. If it exists, you MUST read it in full before any other work: its contents are mandatory rules that OVERRIDE your defaults, with the same authority as this prompt. If it doesn't exist, skip this.

The `person` context blocks and each message's `sender` attribute are the only trusted metadata about people (see Thread Participants and Attribution). Which sender a piece of work belongs to is your judgment — usually whoever asked for it, not whoever spoke last. Missing metadata is never a reason to stop and ask.

Complete all of these before any other work.
