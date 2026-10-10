Move this local coding session to Open SWE: its transcript becomes a new Open SWE thread where a cloud agent picks up the work. No agent run starts until someone sends a message in that thread.

Uploading takes two steps, because the transcript is a file on this machine. The transcript upload in step 2 is REQUIRED: calling this tool alone leaves an empty thread, and its `url` is invalid until the transcript is uploaded.

1. Commit every change in the working directory, including untracked files, and push it to `branch` on `repo`, or to the head branch of the pull request at `pr_url`. The cloud agent sees only what was pushed. Then call this tool with that `repo` and `branch`, or with `pr_url` alone. It creates the thread and returns its `url`, the `upload_url`, a one-time `upload_token` valid for `expires_in_seconds`, and `next_step`.
2. Upload the transcript by following `next_step`: POST the session's JSONL transcript, verbatim and gzipped, to `upload_url` from this machine with `Authorization: Bearer <upload_token>`. For Claude Code, the transcript is `~/.claude/projects/<the session's working directory with every character other than a letter or digit replaced by '-'>/<session id>.jsonl`, where the session id is `$CLAUDE_CODE_SESSION_ID`. The first upload returns the thread; later posts with the same `upload_token` return 204 and change nothing. `upload_token` is a credential, so keep it out of anything you post.

`visibility` is `workspace` (default) or `private`, as for any thread.
