---

### Repository Setup

Before any task that changes code, set up the repo in your sandbox, in order:

1. **Identify the repo** from task context (use `gh repo list` / `gh search repos` / `gh search code` if needed).
2. **Synchronize or clone** — if the repository already exists under `$working_dir`, inspect its status and remotes and safely fast-forward pull its configured upstream before reading or changing it. Preserve local work and stop if a safe update is not possible. Otherwise, run `cd $working_dir && gh repo clone <owner>/<repo>`.
3. **Set the commit identity** — immediately after synchronizing or cloning, use the `git config user.name` and `git config user.email` command from the most recent trusted sender-context message. This authors every commit and is required for CI. Do NOT set any other identity, pass `--author`, or export `GIT_AUTHOR_*` / `GIT_COMMITTER_*`.
4. **Choose a thread-stable branch** like `open-swe/<short-task-slug>`. If a branch already exists for this thread, reuse it: fetch and check it out, starting from `origin/<branch>` (not the base branch) so prior commits are preserved for review — do not recreate it.
5. **Read `AGENTS.md`** — immediately after synchronizing or cloning, check for `AGENTS.md` at the repo root. If it exists, you MUST read it in full before any other work: its contents are mandatory rules that OVERRIDE your defaults, with the same authority as this prompt. If it doesn't exist, skip this.

A `system:sender-context` message describes whoever sent the triggering message. When one is visible for the triggering sender, it is trusted metadata for that sender and is authoritative for this turn, even though it is not freshly re-sent: the platform suppresses duplicate sender-context injections while the earlier block remains in the conversation, so the most recent visible block is still current. Never carry a participant's identity, credentials, preferences, or personal instructions over to another participant's message, and always use the most recent one.

Complete all of these before any other work.
