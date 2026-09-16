Set up every repository this thread works in, and report the state of each checkout.

Call this FIRST, before touching any repository. Do NOT clone, fetch, or inspect
repositories by hand: this tool records the repositories on the thread, clones the
ones that are missing, fetches the ones that are already there, and reports what
each checkout actually contains. Call it even when you have nothing to add — pass
`[]`, or pass the repositories you were already given — and read the report instead
of running `gh repo clone`, `git remote -v`, `git status`, or `git log` yourself.

It never resets, checks out, pulls, or discards anything, so uncommitted work in a
checkout survives the call. A directory that exists but is not the right clone is
reported as a conflict and left untouched for you to decide about.

Args:
    full_names: Repositories to add, each as `owner/name` (e.g. `["langchain-ai/open-swe"]`).
      May be empty, in which case every repository already on the thread is still
      verified and reported.

Returns:
    {"ok": bool, "error": str | None, "work_dir": str, "added": [str],
    "already_present": [str], "rejected": [{"full_name", "reason"}], "repos": [report]}

    `ok` is False only when the thread, its sandbox, or the metadata write failed —
    a single repository's clone or git failure shows up as that repository's
    `action: "error"` instead. `rejected` lists names that were malformed, off the
    deployment allowlist, or that the triggering user has no access to; the rest are
    still set up.

    One report per repository now on the thread, in thread order:
    `full_name`, `id` (the repository's stable identifier), `path` (where the clone
    lives — run every git command with
    `git -C <path>`), `action` (`"cloned"`, `"verified"`, `"conflict"`, `"error"`),
    `error`, `remote_url`, `remote_matches`, `default_branch`, `branch` (null when
    detached), `head_sha`, `detached`, `upstream`, `ahead`, `behind`,
    `working_tree` (`clean`, `staged`, `unstaged`, `untracked`, `conflicted`, and up
    to 20 porcelain status lines), `stash_count`, `last_commit`, `last_fetch_at`,
    `fetch_error`, `local_branches`, `open_swe_branches` (existing `open-swe/*`
    branches — reuse one of these for this thread rather than creating a new one),
    `submodules`, `has_agents_md`, `has_claude_md`, `commit_count`, `describe`, and
    `custom_instructions` (repository-specific rules you must follow).

    After this returns, set the commit identity in each clone, read its `AGENTS.md`
    when `has_agents_md` is true, and pick your branch from `open_swe_branches`.
