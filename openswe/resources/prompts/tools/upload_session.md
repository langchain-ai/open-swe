Move this local coding session to Open SWE: its transcript becomes a new Open SWE thread where a cloud agent picks up the work. No agent run starts until someone sends a message in that thread.

Uploading takes two steps, because the transcript is a file on this machine:

1. Commit every change in the working directory, including untracked files, and push it to `branch` on `repo`, or to the head branch of the pull request at `pr_url`. The cloud agent sees only what was pushed. Then call this tool with that `repo` and `branch`, or with `pr_url` alone. It creates the thread and returns its `url` and a one-time `upload_url` valid for `expires_in_seconds`.
2. POST the session's JSONL transcript, verbatim and gzipped, to `upload_url` from this machine:

   ```sh
   gzip -c <transcript_path> | curl -fsS -X POST -H 'Content-Type: application/x-ndjson' -H 'Content-Encoding: gzip' --data-binary @- '<upload_url>'
   ```

   For Claude Code, `<transcript_path>` is `~/.claude/projects/<the session's working directory with every character other than a letter or digit replaced by '-'>/<session id>.jsonl`, where the session id is `$CLAUDE_CODE_SESSION_ID`. The response is the thread once the transcript is stored; until then the thread is empty. `upload_url` is its own credential, so keep it out of anything you post.

`visibility` is `workspace` (default) or `private`, as for any thread.
