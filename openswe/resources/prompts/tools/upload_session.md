Move this local coding session to Open SWE: its transcript becomes a new Open SWE thread where a cloud agent picks up the work. No agent run starts until someone sends a message in that thread.

Uploading takes two steps, because the transcript is a file on this machine:

1. Commit every change in the working directory, including untracked files, and push it to `branch` on `repo`, or to the head branch of the pull request at `pr_url`. The cloud agent sees only what was pushed. Then call this tool with that `repo` and `branch`, or with `pr_url` alone. It creates the thread and returns its `url`, a one-time `upload_code` valid for `expires_in_seconds`, and the `command` to run.
2. Run `command` on this machine, replacing `<transcript_path>` with the absolute path of the session's JSONL transcript. For Claude Code that is `~/.claude/projects/<the session's working directory with every character other than a letter or digit replaced by '-'>/<session id>.jsonl`, where the session id is `$CLAUDE_CODE_SESSION_ID`. The command prints the thread once the transcript is stored; until then the thread is empty.

`command` runs the `oswe` CLI. Use `oswe` on the `PATH` if it is there. Otherwise the Open SWE desktop app ships it at `/Applications/Open SWE.app/Contents/Resources/bin/oswe` on macOS. Without the app, download the standalone binary for this machine from `https://github.com/langchain-ai/open-swe/releases/latest/download/oswe-<os>-<arch>.tar.gz`, where `<os>` is `darwin` or `linux` and `<arch>` is `arm64` or `x64`, extract it with `tar -xz`, and run the extracted `oswe`. The upload code is its only credential, so no `oswe login` is needed.

`visibility` is `workspace` (default) or `private`, as for any thread.
