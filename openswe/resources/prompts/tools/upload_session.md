Move a local coding session to Robin: its transcript becomes a new Robin thread.
`type` is the agent that wrote it: `claude` (default) or `codex`.
No agent run starts until someone sends a message in that thread.

There are two uses:

- **Continue** a session: a cloud agent picks up the work.
  First commit every change in the working directory, including untracked files, and push it to `branch` on `repo`, or to the head branch of the pull request at `pr_url`.
  The cloud agent sees only what was pushed.
  Then call this tool with that `repo` and `branch`, or with `pr_url` alone.
- **Import** past history in bulk, such as when a person migrates to Robin: pass none of `repo`, `branch` or `pr_url`.
  One call returns one `upload_token` for every session of that `type`: each transcript posted with it becomes its own thread with no repository, and nothing needs pushing.
  A session already imported is skipped, so rerunning an import is safe.
  Imports default to `private` visibility.

Uploading takes two steps, because the transcript is a file on the machine that wrote it.
The transcript upload in step 2 is REQUIRED: calling this tool alone leaves an empty thread, and its `url` is invalid until the transcript is uploaded.

1. Call this tool.
   It returns the `upload_url`, an `upload_token` valid for `expires_in_seconds`, and `next_step`; when continuing, it also creates the thread and returns its `url`.
2. Upload by following `next_step`: POST each session's JSONL transcript, verbatim and gzipped, to `upload_url` from that machine with `Authorization: Bearer <upload_token>`.
   - Claude Code: `~/.claude/projects/<the session's working directory with every character other than a letter or digit replaced by '-'>/<session id>.jsonl`; the current session's id is `$CLAUDE_CODE_SESSION_ID`.
   - Codex: `~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<session id>.jsonl`.
     A resumed session writes a new rollout that replays the earlier one, so upload only the newest rollout of each session id.

   A post returns the thread it filled; posting a session that is already uploaded returns 204 and changes nothing.
   `upload_token` is a credential, so keep it out of anything you post.

`visibility` is `workspace` or `private`, as for any thread; it defaults to `workspace` when continuing a session.
